"""
project113.py

Quick, little script for periodically checking the relay count in the
consensus. Queries are done every couple hours and this sends an email notice
if it changes dramatically throughout the week.
"""

import sys
import time
import getpass
import smtplib
from email.mime.text import MIMEText

sys.path[0] = sys.path[0][:-5]

import util.torTools

SAMPLING_INTERVAL = 7200 # two hours

USERNAME = ""
PASSWORD = ""
RECEIVER = ""

# size of change (+/-) at which an alert is sent
BIHOURLY_THRESHOLD = 15
DAILY_THRESHOLD = 50
WEEKLY_THRESHOLD = 100

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
    descEntry = conn.get_info(queryParam)[queryParam]
    
    isExit = False
    for line in descEntry.split("\n"):
      if line == "reject *:*": break # reject all before any accept entries
      elif line.startswith("accept"):
        # Guess this to be an exit (erroring on the side of inclusiveness)
        isExit = True
        break
    
    if isExit: exitEntries.append(nsEntry)
  
  return exitEntries

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
  nsEntries = [] # parallel listing for exiting ns entries
  lastQuery = 0
  
  while True:
    # sleep for a couple hours
    while time.time() < (lastQuery + SAMPLING_INTERVAL):
      sleepTime = max(1, SAMPLING_INTERVAL - (time.time() - lastQuery))
      time.sleep(sleepTime)
    
    # adds new count to the beginning
    #newCount = getCount(conn)
    exitEntries = getExits(conn)
    newCount = len(exitEntries)
    
    counts.insert(0, newCount)
    nsEntries.insert(0, exitEntries)
    if len(counts) > 84:
      counts.pop()
      nsEntries.pop()
    
    # check if we broke any thresholds (alert at the lowest increment)
    alarmHourly, alarmDaily, alarmWeekly = False, False, False
    
    if len(counts) >= 2:
      alarmHourly = abs(newCount - counts[1]) >= BIHOURLY_THRESHOLD
    
    if len(counts) >= 3:
      dayMin, dayMax = min(counts[:12]), max(counts[:12])
      alarmDaily = (dayMax - dayMin) > DAILY_THRESHOLD
    
    if len(counts) >= 12:
      weekMin, weekMax = min(counts), max(counts)
      alarmWeekly = (weekMax - weekMin) > WEEKLY_THRESHOLD
    
    # notes entry on terminal
    lastQuery = time.time()
    timeLabel = time.strftime("%H:%M %m/%d/%Y", time.localtime(lastQuery))
    print "%s - %s exits" % (timeLabel, newCount)
    
    # sends a notice with counts for the last week
    if alarmHourly or alarmDaily or alarmWeekly:
      if alarmHourly: threshold = "hourly"
      elif alarmDaily: threshold = "daily"
      elif alarmWeekly: threshold = "weekly"
      
      msg = "%s threshold broken\n" % threshold
      
      msg += "\nexit counts:\n"
      entryTime = lastQuery
      for countEntry in counts:
        timeLabel = time.strftime("%H:%M %m/%d/%Y", time.localtime(entryTime))
        msg += "%s - %i\n" % (timeLabel, countEntry)
        entryTime -= SAMPLING_INTERVAL
      
      msg += "\nnew exits (hourly):\n"
      entriesDiff = getExitsDiff(nsEntries[0], nsEntries[1])
      for entry in entriesDiff:
        msg += "%s (%s:%s)\n" % (entry.idhex, entry.ip, entry.orport)
        msg += "    nickname: %s\n    flags: %s\n\n" % (entry.nickname, ", ".join(entry.flags))
      
      if len(counts) >= 12:
        msg += "\nnew exits (daily):\n"
        entriesDiff = getExitsDiff(nsEntries[0], nsEntries[12])
        for entry in entriesDiff:
          msg += "%s (%s:%s)\n" % (entry.idhex, entry.ip, entry.orport)
          msg += "    nickname: %s\n    flags: %s\n\n" % (entry.nickname, ", ".join(entry.flags))
      
      if len(counts) >= 48:
        # require at least four days of data
        msg += "\nnew exits (weekly):\n"
        entriesDiff = getExitsDiff(nsEntries[0], nsEntries[-1])
        for entry in entriesDiff:
          msg += "%s (%s:%s)\n" % (entry.idhex, entry.ip, entry.orport)
          msg += "    nickname: %s\n    flags: %s\n\n" % (entry.nickname, ", ".join(entry.flags))
      
      sendAlert(msg)
      
      # clears entries so we don't repeatidly send alarms for the same event
      if alarmDaily: del counts[2:]
      elif alarmWeekly: del counts[12:]

