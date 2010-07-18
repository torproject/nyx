"""
project113.py

Quick, little script for periodically checking the relay count in the
consensus. Queries are done every couple hours and this sends an email notice
if it changes dramatically throughout the week.
"""

# TODO: this whole script is experimental and should be rewritten once we
# figure out what works best...

# TODO: save fingerprints to file so it's preserved between runs... maybe logs too

import sys
import time
import getpass
import smtplib
from email.mime.text import MIMEText

sys.path[0] = sys.path[0][:-5]

from TorCtl import TorCtl
import util.torTools

SAMPLING_INTERVAL = 7200 # two hours

USERNAME = ""
PASSWORD = ""
RECEIVER = ""
ALERT_HOURLY_DROP = False # sends alert for hourly network shrinking if true

# size of change (+/-) at which an alert is sent
BIHOURLY_THRESHOLD = 30
DAILY_THRESHOLD = 75
WEEKLY_THRESHOLD = 200

# location from which to fetch newline separated listing of existing fingerprints
FINGERPRINTS_PREPOPULATE = "./fingerprints_out"

# location to which seen fingerprints are saved
FINGERPRINTS_STORE = "./fingerprints_out"

SEEN_FINGERPRINTS = set()

def sendAlert(msg):
  mimeMsg = MIMEText(msg)
  mimeMsg['Subject'] = "Tor Relay Threshold Alert"
  mimeMsg['From'] = USERNAME
  mimeMsg['To'] = RECEIVER
  
  # Send the message via our own SMTP server, but don't include the
  # envelope header.
  try:
    server = smtplib.SMTP('smtp.gmail.com:587')
    server.starttls()
    server.login(USERNAME, PASSWORD)
    
    server.sendmail(USERNAME, [RECEIVER], mimeMsg.as_string())
    server.quit()
  except smtplib.SMTPAuthenticationError:
    print "Failed to sent alert"

def getCount(conn):
  nsEntries = conn.get_network_status()
  return len(nsEntries)

def getExits(conn):
  # provides ns entries associated with exit relays
  exitEntries = []
  for nsEntry in conn.get_network_status():
    queryParam = "desc/id/%s" % nsEntry.idhex
    
    try:
      descEntry = conn.get_info(queryParam)[queryParam]
    except TorCtl.ErrorReply:
      descEntry = ""
    
    isExit = False
    for line in descEntry.split("\n"):
      if line == "reject *:*": break # reject all before any accept entries
      elif line.startswith("accept"):
        # Guess this to be an exit (erroring on the side of inclusiveness)
        isExit = True
        break
    
    if isExit: exitEntries.append(nsEntry)
  
  return exitEntries

def getNewExits(newEntries):
  # provides relays that have never been seen before
  diffMapping = dict([(entry.idhex, entry) for entry in newEntries])
  
  for fingerprint in SEEN_FINGERPRINTS:
    if fingerprint in diffMapping.keys(): del diffMapping[fingerprint]
  
  return diffMapping.values()

def getExitsDiff(newEntries, oldEntries):
  # provides relays in newEntries but not oldEntries
  diffMapping = dict([(entry.idhex, entry) for entry in newEntries])
  
  for entry in oldEntries:
    if entry.idhex in diffMapping.keys(): del diffMapping[entry.idhex]
  
  return diffMapping.values()

if __name__ == '__main__':
  if not PASSWORD: PASSWORD = getpass.getpass("GMail Password: ")
  conn = util.torTools.connect()
  counts = [] # has entries for up to the past week
  newCounts = [] # parallel listing for new entries added on each time period
  nsEntries = [] # parallel listing for exiting ns entries
  newExitListings = []
  lastQuery = 0
  tick = 0
  
  # prepopulates existing fingerprints
  if FINGERPRINTS_PREPOPULATE:
    prepopulateFile = open(FINGERPRINTS_PREPOPULATE, "r")
    
    for entry in prepopulateFile:
      SEEN_FINGERPRINTS.add(entry.upper().strip())
      #if fpFile and FINGERPRINTS_PREPOPULATE != FINGERPRINTS_STORE: fpFile.write(entry.upper())
    
    prepopulateFile.close()
  
  fpFile = None
  if FINGERPRINTS_STORE: fpFile = open(FINGERPRINTS_STORE, "a")
  
  while True:
    tick += 1
    
    # sleep for a couple hours
    while time.time() < (lastQuery + SAMPLING_INTERVAL):
      sleepTime = max(1, SAMPLING_INTERVAL - (time.time() - lastQuery))
      time.sleep(sleepTime)
    
    # adds new count to the beginning
    exitEntries = getExits(conn)
    newExitEntries = getNewExits(exitEntries)
    count = len(exitEntries)
    newCount = len(newExitEntries)
    
    counts.insert(0, count)
    newCounts.insert(0, newCount)
    nsEntries.insert(0, exitEntries)
    newExitListings.insert(0, newExitEntries)
    if len(counts) > 84:
      counts.pop()
      newCounts.pop()
      nsEntries.pop()
      newExitListings.pop()
    
    # check if we broke any thresholds (alert at the lowest increment)
    alarmHourly, alarmDaily, alarmWeekly = False, False, False
    
    if len(counts) >= 2:
      #if ALERT_HOURLY_DROP: alarmHourly = abs(count - counts[1]) >= BIHOURLY_THRESHOLD
      #else: alarmHourly = count - counts[1] >= BIHOURLY_THRESHOLD
      alarmHourly = newCount >= BIHOURLY_THRESHOLD
    
    #if len(counts) >= 3:
    #  dayMin, dayMax = min(counts[:12]), max(counts[:12])
    #  alarmDaily = (dayMax - dayMin) > DAILY_THRESHOLD
    
    #if len(counts) >= 12:
    #  weekMin, weekMax = min(counts), max(counts)
    #  alarmWeekly = (weekMax - weekMin) > WEEKLY_THRESHOLD
    
    # notes entry on terminal
    lastQuery = time.time()
    timeLabel = time.strftime("%H:%M %m/%d/%Y", time.localtime(lastQuery))
    print "%s - %s exits (%s new)" % (timeLabel, count, newCount)
    
    # add all new fingerprints to seen set
    for entry in nsEntries[0]:
      SEEN_FINGERPRINTS.add(entry.idhex)
      if fpFile: fpFile.write(entry.idhex + "\n")
    
    # sends a notice with counts for the last week
    if alarmHourly or alarmDaily or alarmWeekly or (tick % 12 == 0):
      if alarmHourly: threshold = "hourly"
      elif alarmDaily: threshold = "daily"
      elif alarmWeekly: threshold = "weekly"
      else: threshold = "no"
      
      msg = "%s threshold broken\n" % threshold
      
      msg += "\nexit counts:\n"
      entryTime = lastQuery
      for i in range(len(counts)):
        countEntry, newCountEntry = counts[i], newCounts[i]
        timeLabel = time.strftime("%H:%M %m/%d/%Y", time.localtime(entryTime))
        msg += "%s - %i (%i new)\n" % (timeLabel, countEntry, newCountEntry)
        entryTime -= SAMPLING_INTERVAL
      
      msg += "\nnew exits (hourly):\n"
      for entry in newExitEntries:
        msg += "%s (%s:%s)\n" % (entry.idhex, entry.ip, entry.orport)
        msg += "    nickname: %s\n    flags: %s\n\n" % (entry.nickname, ", ".join(entry.flags))
      
      if len(counts) >= 12:
        msg += "\nnew exits (daily):\n"
        
        entryTime = lastQuery
        for i in range(len(newExitListings)):
          exitListing = newExitListings[i]
          timeLabel = time.strftime("%H:%M %m/%d/%Y", time.localtime(entryTime))
          msg += "entries for %s\n" % timeLabel
          
          for entry in exitListing:
            msg += "%s (%s:%s)\n" % (entry.idhex, entry.ip, entry.orport)
            msg += "    nickname: %s\n    flags: %s\n\n" % (entry.nickname, ", ".join(entry.flags))
          
          entryTime -= SAMPLING_INTERVAL
        
        #entriesDiff = getExitsDiff(nsEntries[0], nsEntries[11])
        #for entry in entriesDiff:
        #  msg += "%s (%s:%s)\n" % (entry.idhex, entry.ip, entry.orport)
        #  msg += "    nickname: %s\n    flags: %s\n\n" % (entry.nickname, ", ".join(entry.flags))
      
      #if len(counts) >= 48:
      #  # require at least four days of data
      #  msg += "\nnew exits (weekly):\n"
      #  entriesDiff = getExitsDiff(nsEntries[0], nsEntries[-1])
      #  for entry in entriesDiff:
      #    msg += "%s (%s:%s)\n" % (entry.idhex, entry.ip, entry.orport)
      #    msg += "    nickname: %s\n    flags: %s\n\n" % (entry.nickname, ", ".join(entry.flags))
      
      sendAlert(msg)
      
      # clears entries so we don't repeatidly send alarms for the same event
      if alarmDaily: del counts[2:]
      elif alarmWeekly: del counts[12:]
      

