#!/usr/bin/env python
# controller.py -- arm interface (curses monitor for relay status)
# Released under the GPL v3 (http://www.gnu.org/licenses/gpl.html)

"""
Curses (terminal) interface for the arm relay status monitor.
"""

import time
import curses
from threading import RLock
from TorCtl import TorCtl

import util
import headerPanel
import bandwidthPanel
import logPanel
import connPanel
import confPanel

REFRESH_RATE = 5        # seconds between redrawing screen
cursesLock = RLock()    # global curses lock (curses isn't thread safe and
                        # concurrency bugs produce especially sinister glitches)

# enums for message in control label
CTL_HELP, CTL_PAUSED = range(2)

# panel order per page
PAGE_S = ["header", "control", "popup"]    # sticky (ie, always available) page
PAGES = [
  ["bandwidth", "log"],
  ["conn"],
  ["torrc"]]
PAUSEABLE = ["header", "bandwidth", "log", "conn"]
PAGE_COUNT = 3 # all page numbering is internally represented as 0-indexed
# TODO: page for configuration information

# events needed for panels other than the event log
REQ_EVENTS = ["BW", "NEWDESC", "NEWCONSENSUS"]

class ControlPanel(util.Panel):
  """ Draws single line label for interface controls. """
  
  def __init__(self, lock, resolver):
    util.Panel.__init__(self, lock, 1)
    self.msgText = CTL_HELP           # message text to be displyed
    self.msgAttr = curses.A_NORMAL    # formatting attributes
    self.page = 1                     # page number currently being displayed
    self.resolver = resolver          # dns resolution thread
    self.resolvingCounter = -1         # count of resolver when starting (-1 if we aren't working on a batch)
  
  def setMsg(self, msgText, msgAttr=curses.A_NORMAL):
    """
    Sets the message and display attributes. If msgType matches CTL_HELP or
    CTL_PAUSED then uses the default message for those statuses.
    """
    
    self.msgText = msgText
    self.msgAttr = msgAttr
  
  def redraw(self):
    if self.win:
      self.clear()
      
      msgText = self.msgText
      msgAttr = self.msgAttr
      
      if msgText == CTL_HELP:
        msgAttr = curses.A_NORMAL
        
        if self.resolvingCounter != -1:
          if self.resolver.unresolvedQueue.empty() or self.resolver.isPaused:
            # done resolving dns batch
            self.resolvingCounter = -1
            curses.halfdelay(REFRESH_RATE * 10) # revert to normal refresh rate
          else:
            batchSize = self.resolver.totalResolves - self.resolvingCounter
            entryCount = batchSize - self.resolver.unresolvedQueue.qsize()
            if batchSize > 0: progress = 100 * entryCount / batchSize
            else: progress = 0
            
            additive = "(or l) " if self.page == 2 else ""
            msgText = "Resolving hostnames (%i / %i, %i%%) - press esc %sto cancel" % (entryCount, batchSize, progress, additive)
        
        if self.resolvingCounter == -1:
          msgText = "page %i / %i - q: quit, p: pause, h: page help" % (self.page, PAGE_COUNT)
      elif msgText == CTL_PAUSED:
        msgText = "Paused"
        msgAttr = curses.A_STANDOUT
      
      self.addstr(0, 0, msgText, msgAttr)
      self.refresh()

def setEventListening(loggedEvents, conn, logListener):
  """
  Tries to set events being listened for, displaying error for any event
  types that aren't supported (possibly due to version issues). This returns 
  a list of event types that were successfully set.
  """
  eventsSet = False
  
  while not eventsSet:
    try:
      # adds BW events if not already included (so bandwidth monitor will work)
      # removes UNKNOWN since not an actual event type
      connEvents = loggedEvents.union(set(REQ_EVENTS))
      connEvents.discard("UNKNOWN")
      conn.set_events(connEvents)
      eventsSet = True
    except TorCtl.ErrorReply, exc:
      msg = str(exc)
      if "Unrecognized event" in msg:
        # figure out type of event we failed to listen for
        start = msg.find("event \"") + 7
        end = msg.rfind("\"")
        eventType = msg[start:end]
        if eventType == "BW": raise exc # bandwidth monitoring won't work - best to crash
        
        # removes and notes problem
        loggedEvents.remove(eventType)
        logListener.monitor_event("WARN", "Unsupported event type: %s" % eventType)
      else:
        raise exc
    except TorCtl.TorCtlClosed:
      return []
  
  loggedEvents = list(loggedEvents)
  loggedEvents.sort() # alphabetizes
  return loggedEvents

def drawTorMonitor(stdscr, conn, loggedEvents):
  """
  Starts arm interface reflecting information on provided control port.
  
  stdscr - curses window
  conn - active Tor control port connection
  loggedEvents - types of events to be logged (plus an optional "UNKNOWN" for
    otherwise unrecognized events)
  """
  
  curses.use_default_colors()           # allows things like semi-transparent backgrounds
  util.initColors()                     # initalizes color pairs for colored text
  curses.halfdelay(REFRESH_RATE * 10)   # uses getch call as timer for REFRESH_RATE seconds
  
  # attempts to make the cursor invisible (not supported in all terminals)
  try: curses.curs_set(0)
  except curses.error: pass
  
  panels = {
    "header": headerPanel.HeaderPanel(cursesLock, conn),
    "popup": util.Panel(cursesLock, 9),
    "bandwidth": bandwidthPanel.BandwidthMonitor(cursesLock, conn),
    "log": logPanel.LogMonitor(cursesLock, loggedEvents),
    "torrc": confPanel.ConfPanel(cursesLock, conn.get_info("config-file")["config-file"])}
  panels["conn"] = connPanel.ConnPanel(cursesLock, conn, panels["log"])
  panels["control"] = ControlPanel(cursesLock, panels["conn"].resolver)
  
  # listeners that update bandwidth and log panels with Tor status
  conn.add_event_listener(panels["log"])
  conn.add_event_listener(panels["bandwidth"])
  conn.add_event_listener(panels["conn"])
  
  # tells Tor to listen to the events we're interested
  loggedEvents = setEventListening(loggedEvents, conn, panels["log"])
  panels["log"].loggedEvents = loggedEvents # strips any that couldn't be set
  
  oldY, oldX = -1, -1
  isUnresponsive = False    # true if it's been over five seconds since the last BW event (probably due to Tor closing)
  isPaused = False          # if true updates are frozen
  page = 0
  netstatRefresh = time.time()  # time of last netstat refresh
  
  while True:
    # tried only refreshing when the screen was resized but it caused a
    # noticeable lag when resizing and didn't have an appreciable effect
    # on system usage
    
    cursesLock.acquire()
    try:
      y, x = stdscr.getmaxyx()
      if x > oldX or y > oldY:
        # gives panels a chance to take advantage of the maximum bounds
        startY = 0
        for panelKey in PAGE_S[:2]:
          panels[panelKey].recreate(stdscr, startY)
          startY += panels[panelKey].height
        
        panels["popup"].recreate(stdscr, startY, 80)
        
        for panelSet in PAGES:
          tmpStartY = startY
          
          for panelKey in panelSet:
            panels[panelKey].recreate(stdscr, tmpStartY)
            tmpStartY += panels[panelKey].height
      
      # if it's been at least five seconds since the last BW event Tor's probably done
      if not isUnresponsive and panels["log"].getHeartbeat() >= 5:
        isUnresponsive = True
        panels["log"].monitor_event("NOTICE", "Relay unresponsive (last heartbeat: %s)" % time.ctime(panels["log"].lastHeartbeat))
      elif isUnresponsive and panels["log"].getHeartbeat() < 5:
        # this really shouldn't happen - BW events happen every second...
        isUnresponsive = False
        panels["log"].monitor_event("WARN", "Relay resumed")
      
      # if it's been at least five seconds since the last refresh of connection listing, update
      currentTime = time.time()
      if not panels["conn"].isPaused and currentTime - netstatRefresh >= 5:
        panels["conn"].reset()
        netstatRefresh = currentTime
      
      # I haven't the foggiest why, but doesn't work if redrawn out of order...
      for panelKey in (PAGE_S + PAGES[page]): panels[panelKey].redraw()
      oldY, oldX = y, x
      stdscr.refresh()
    finally:
      cursesLock.release()
    
    key = stdscr.getch()
    if key == ord('q') or key == ord('Q'): break # quits
    elif key == curses.KEY_LEFT or key == curses.KEY_RIGHT:
      # switch page
      if key == curses.KEY_LEFT: page = (page - 1) % PAGE_COUNT
      else: page = (page + 1) % PAGE_COUNT
      
      # pauses panels that aren't visible to prevent events from accumilating
      # (otherwise they'll wait on the curses lock which might get demanding)
      for key in PAUSEABLE: panels[key].setPaused(isPaused or (key not in PAGES[page] and key not in PAGE_S))
      
      panels["control"].page = page + 1
      panels["control"].refresh()
    elif key == ord('p') or key == ord('P'):
      # toggles update freezing
      cursesLock.acquire()
      try:
        isPaused = not isPaused
        for key in PAUSEABLE: panels[key].setPaused(isPaused or key not in PAGES[page])
        panels["control"].setMsg(CTL_PAUSED if isPaused else CTL_HELP)
      finally:
        cursesLock.release()
    elif key == ord('h') or key == ord('H'):
      # displays popup for current page's controls
      cursesLock.acquire()
      try:
        for key in PAUSEABLE: panels[key].setPaused(True)
        
        # lists commands
        popup = panels["popup"]
        popup.clear()
        popup.win.box()
        popup.addstr(0, 0, "Page %i Commands:" % (page + 1), util.LABEL_ATTR)
        
        if page == 0:
          bwVisibleLabel = "visible" if panels["bandwidth"].isVisible else "hidden"
          popup.addfstr(1, 2, "b: toggle bandwidth panel (<b>%s</b>)" % bwVisibleLabel)
          popup.addstr(1, 41, "e: change logged events")
        if page == 1:
          popup.addstr(1, 2, "up arrow: scroll up a line")
          popup.addstr(1, 41, "down arrow: scroll down a line")
          popup.addstr(2, 2, "page up: scroll up a page")
          popup.addstr(2, 41, "page down: scroll down a page")
          popup.addstr(3, 2, "enter: connection details")
          popup.addfstr(3, 41, "c: toggle cursor (<b>%s</b>)" % ("on" if panels["conn"].isCursorEnabled else "off"))
          
          listingType = connPanel.LIST_LABEL[panels["conn"].listingType].lower()
          popup.addfstr(4, 2, "l: listed identity (<b>%s</b>)" % listingType)
          
          allowDnsLabel = "allow" if panels["conn"].allowDNS else "disallow"
          popup.addfstr(4, 41, "r: permit DNS resolution (<b>%s</b>)" % allowDnsLabel)
          
          popup.addstr(5, 2, "s: sort ordering")
        elif page == 2:
          popup.addstr(1, 2, "up arrow: scroll up a line")
          popup.addstr(1, 41, "down arrow: scroll down a line")
          popup.addstr(2, 2, "page up: scroll up a page")
          popup.addstr(2, 41, "page down: scroll down a page")
          
          strippingLabel = "on" if panels["torrc"].stripComments else "off"
          popup.addfstr(3, 2, "s: comment stripping (<b>%s</b>)" % strippingLabel)
          
          lineNumLabel = "on" if panels["torrc"].showLineNum else "off"
          popup.addfstr(3, 41, "n: line numbering (<b>%s</b>)" % lineNumLabel)
        
        popup.addstr(7, 2, "Press any key...")
        
        popup.refresh()
        
        curses.cbreak()
        stdscr.getch()
        curses.halfdelay(REFRESH_RATE * 10)
        
        for key in PAUSEABLE: panels[key].setPaused(isPaused or key not in PAGES[page])
      finally:
        cursesLock.release()
    elif page == 0 and (key == ord('b') or key == ord('B')):
      # toggles bandwidth panel visability
      panels["bandwidth"].setVisible(not panels["bandwidth"].isVisible)
      oldY = -1 # force resize event
    elif page == 0 and (key == ord('e') or key == ord('E')):
      # allow user to enter new types of events to log - unchanged if left blank
      cursesLock.acquire()
      try:
        for key in PAUSEABLE: panels[key].setPaused(True)
        
        # provides prompt
        panels["control"].setMsg("Events to log: ")
        panels["control"].redraw()
        
        # makes cursor and typing visible
        try: curses.curs_set(1)
        except curses.error: pass
        curses.echo()
        
        # lists event types
        popup = panels["popup"]
        popup.clear()
        popup.addstr(0, 0, "Event Types:", util.LABEL_ATTR)
        lineNum = 1
        for line in logPanel.EVENT_LISTING.split("\n"):
          line = line[6:]
          popup.addstr(lineNum, 0, line)
          lineNum += 1
        popup.refresh()
        
        # gets user input (this blocks monitor updates)
        eventsInput = panels["control"].win.getstr(0, 15)
        eventsInput = eventsInput.replace(' ', '') # strips spaces
        
        # reverts visability settings
        try: curses.curs_set(0)
        except curses.error: pass
        curses.noecho()
        curses.halfdelay(REFRESH_RATE * 10) # evidenlty previous tweaks reset this...
        
        # TODO: it would be nice to quit on esc, but looks like this might not be possible...
        if eventsInput != "":
          try:
            expandedEvents = logPanel.expandEvents(eventsInput)
            loggedEvents = setEventListening(expandedEvents, conn, panels["log"])
            panels["log"].loggedEvents = loggedEvents
          except ValueError, exc:
            panels["control"].setMsg("Invalid flags: %s" % str(exc), curses.A_STANDOUT)
            panels["control"].redraw()
            time.sleep(2)
        
        panels["control"].setMsg(CTL_PAUSED if isPaused else CTL_HELP)
        for key in PAUSEABLE: panels[key].setPaused(isPaused or key not in PAGES[page])
      finally:
        cursesLock.release()
    elif key == 27 and panels["conn"].listingType == connPanel.LIST_HOSTNAME and panels["control"].resolvingCounter != -1:
      # canceling hostname resolution (esc on any page)
      panels["conn"].listingType = connPanel.LIST_IP
      panels["control"].resolvingCounter = -1
      panels["conn"].resolver.setPaused(True)
      panels["conn"].sortConnections()
    elif page == 1 and (key == ord('l') or key == ord('L')):
      # provides menu to pick identification info listed for connections
      cursesLock.acquire()
      try:
        for key in PAUSEABLE: panels[key].setPaused(True)
        curses.cbreak() # wait indefinitely for key presses (no timeout)
        popup = panels["popup"]
        
        # uses smaller dimentions more fitting for small content
        panels["popup"].height = 6
        panels["popup"].recreate(stdscr, startY, 20)
        
        # hides top label of conn panel
        panels["conn"].showLabel = False
        panels["conn"].redraw()
        
        selection = panels["conn"].listingType    # starts with current option selected
        options = [connPanel.LIST_IP, connPanel.LIST_HOSTNAME, connPanel.LIST_FINGERPRINT, connPanel.LIST_NICKNAME]
        key = 0
        
        while key not in (curses.KEY_ENTER, 10, ord(' ')):
          popup.clear()
          popup.win.box()
          popup.addstr(0, 0, "List By:", util.LABEL_ATTR)
          
          for i in range(len(options)):
            sortType = options[i]
            format = curses.A_STANDOUT if i == selection else curses.A_NORMAL
            
            if panels["conn"].listingType == sortType: tab = "> "
            else: tab = "  "
            sortLabel = connPanel.LIST_LABEL[sortType]
            
            popup.addstr(i + 1, 2, tab)
            popup.addstr(i + 1, 4, sortLabel, format)
          
          popup.refresh()
          key = stdscr.getch()
          if key == curses.KEY_UP: selection = max(0, selection - 1)
          elif key == curses.KEY_DOWN: selection = min(len(options) - 1, selection + 1)
          elif key == 27:
            # esc - cancel
            selection = panels["conn"].listingType
            key = curses.KEY_ENTER
        
        # reverts popup dimensions and conn panel label
        panels["popup"].height = 9
        panels["popup"].recreate(stdscr, startY, 80)
        panels["conn"].showLabel = True
        
        # applies new setting
        pickedOption = options[selection]
        if pickedOption != panels["conn"].listingType:
          panels["conn"].listingType = pickedOption
          
          if panels["conn"].listingType == connPanel.LIST_HOSTNAME:
            curses.halfdelay(10) # refreshes display every second until done resolving
            panels["control"].resolvingCounter = panels["conn"].resolver.totalResolves - panels["conn"].resolver.unresolvedQueue.qsize()
            
            resolver = panels["conn"].resolver
            resolver.setPaused(not panels["conn"].allowDNS)
            for connEntry in panels["conn"].connections: resolver.resolve(connEntry[connPanel.CONN_F_IP])
          else:
            panels["control"].resolvingCounter = -1
            panels["conn"].resolver.setPaused(True)
          
          panels["conn"].sortConnections()
        
        curses.halfdelay(REFRESH_RATE * 10) # reset normal pausing behavior
      finally:
        cursesLock.release()
    elif page == 1 and panels["conn"].isCursorEnabled and key in (curses.KEY_ENTER, 10, ord(' ')):
      # provides details on selected connection
      cursesLock.acquire()
      try:
        for key in PAUSEABLE: panels[key].setPaused(True)
        popup = panels["popup"]
        
        # reconfigures connection panel to accomidate details dialog
        panels["conn"].showLabel = False
        panels["conn"].showingDetails = True
        panels["conn"].redraw()
        
        resolver = panels["conn"].resolver
        resolver.setPaused(not panels["conn"].allowDNS)
        relayLookupCache = {} # temporary cache of entry -> (ns data, desc data)
        
        while key not in (curses.KEY_ENTER, 10, ord(' ')):
          key = 0
          curses.cbreak() # wait indefinitely for key presses (no timeout)
          popup.clear()
          popup.win.box()
          popup.addstr(0, 0, "Connection Details:", util.LABEL_ATTR)
          
          selection = panels["conn"].cursorSelection
          if not selection: break
          selectionColor = connPanel.TYPE_COLORS[selection[connPanel.CONN_TYPE]]
          format = util.getColor(selectionColor) | curses.A_BOLD
          
          selectedIp = selection[connPanel.CONN_F_IP]
          selectedPort = selection[connPanel.CONN_F_PORT]
          addrLabel = "address: %s:%s" % (selectedIp, selectedPort)
          
          hostname = resolver.resolve(selectedIp)
          if hostname == None:
            if resolver.isPaused: hostname = "DNS resolution disallowed"
            elif selectedIp not in resolver.resolvedCache.keys():
              # if hostname is still being resolved refresh panel every half-second until it's completed
              curses.halfdelay(5)
              hostname = "resolving..."
            else:
              # hostname couldn't be resolved
              hostname = "unknown"
          elif len(hostname) > 73 - len(addrLabel):
            # hostname too long - truncate
            hostname = "%s..." % hostname[:70 - len(addrLabel)]
          
          popup.addstr(1, 2, "%s (%s)" % (addrLabel, hostname), format)
          
          locale = selection[connPanel.CONN_COUNTRY]
          popup.addstr(2, 2, "locale: %s" % locale, format)
          
          # provides consensus data for selection (needs fingerprint to get anywhere...)
          fingerprint = panels["conn"].getFingerprint(selectedIp, selectedPort)
          
          if fingerprint == "UNKNOWN":
            if selectedIp not in panels["conn"].fingerprintMappings.keys():
              # no consensus entry for this ip address
              popup.addstr(3, 2, "No consensus data found", format)
            else:
              # couldn't resolve due to multiple matches - list them all
              popup.addstr(3, 2, "Muliple matching IPs, possible fingerprints are:", format)
              matchings = panels["conn"].fingerprintMappings[selectedIp]
              
              line = 4
              for (matchPort, matchFingerprint) in matchings:
                popup.addstr(line, 2, "%i. or port: %-5s fingerprint: %s" % (line - 3, matchPort, matchFingerprint), format)
                line += 1
                
                if line == 7 and len(matchings) > 4:
                  popup.addstr(8, 2, "... %i more" % len(matchings) - 3, format)
                  break
          else:
            # fingerprint found - retrieve related data
            if selection in relayLookupCache.keys(): nsEntry, descEntry = relayLookupCache[selection]
            else:
              nsData = conn.get_network_status("id/%s" % fingerprint)
              
              if len(nsData) > 1:
                # multiple records for fingerprint (shouldn't happen)
                panels["log"].monitor_event("WARN", "Multiple consensus entries for fingerprint: %s" % fingerprint)
              
              nsEntry = nsData[0]
              descLookupCmd = "desc/id/%s" % fingerprint
              descEntry = TorCtl.Router.build_from_desc(conn.get_info(descLookupCmd)[descLookupCmd].split("\n"), nsEntry)
              relayLookupCache[selection] = (nsEntry, descEntry)
            
            popup.addstr(2, 15, "fingerprint: %s" % fingerprint, format)
            
            nickname = panels["conn"].getNickname(selectedIp, selectedPort)
            dirPortLabel = "dirport: %i" % nsEntry.dirport if nsEntry.dirport else ""
            popup.addstr(3, 2, "nickname: %-25s orport: %-10i %s" % (nickname, nsEntry.orport, dirPortLabel), format)
            
            popup.addstr(4, 2, "published: %-24s os: %-14s version: %s" % (descEntry.published, descEntry.os, descEntry.version), format)
            popup.addstr(5, 2, "flags: %s" % ", ".join(nsEntry.flags), format)
            
            exitLine = ", ".join([str(k) for k in descEntry.exitpolicy])
            if len(exitLine) > 63: exitLine = "%s..." % exitLine[:60]
            popup.addstr(6, 2, "exit policy: %s" % exitLine, format)
            
            if descEntry.contact:
              # clears up some common obscuring
              contactAddr = descEntry.contact
              obscuring = [(" at ", "@"), (" AT ", "@"), ("AT", "@"), (" dot ", "."), (" DOT ", ".")]
              for match, replace in obscuring: contactAddr = contactAddr.replace(match, replace)
              if len(contactAddr) > 67: contactAddr = "%s..." % contactAddr[:64]
              popup.addstr(7, 2, "contact: %s" % contactAddr, format)
          
          popup.refresh()
          key = stdscr.getch()
          
          if key == curses.KEY_RIGHT: key = curses.KEY_DOWN
          elif key == curses.KEY_LEFT: key = curses.KEY_UP
          
          if key in (curses.KEY_DOWN, curses.KEY_UP, curses.KEY_PPAGE, curses.KEY_NPAGE):
            panels["conn"].handleKey(key)
        
        panels["conn"].showLabel = True
        panels["conn"].showingDetails = False
        resolver.setPaused(not panels["conn"].allowDNS and panels["conn"].listingType == connPanel.LIST_HOSTNAME)
        curses.halfdelay(REFRESH_RATE * 10) # reset normal pausing behavior
      finally:
        cursesLock.release()
      
    elif page == 1 and (key == ord('s') or key == ord('S')):
      # set ordering for connection listing
      cursesLock.acquire()
      try:
        for key in PAUSEABLE: panels[key].setPaused(True)
        curses.cbreak() # wait indefinitely for key presses (no timeout)
        
        # lists event types
        popup = panels["popup"]
        selections = []     # new ordering
        cursorLoc = 0       # index of highlighted option
        
        # listing of inital ordering
        prevOrdering = "<b>Current Order: "
        for sort in panels["conn"].sortOrdering: prevOrdering += connPanel.getSortLabel(sort, True) + ", "
        prevOrdering = prevOrdering[:-2] + "</b>"
        
        # Makes listing of all options
        options = []
        for (type, label, func) in connPanel.SORT_TYPES: options.append(connPanel.getSortLabel(type))
        options.append("Cancel")
        
        while len(selections) < 3:
          popup.clear()
          popup.win.box()
          popup.addstr(0, 0, "Connection Ordering:", util.LABEL_ATTR)
          popup.addfstr(1, 2, prevOrdering)
          
          # provides new ordering
          newOrdering = "<b>New Order: "
          if selections:
            for sort in selections: newOrdering += connPanel.getSortLabel(sort, True) + ", "
            newOrdering = newOrdering[:-2] + "</b>"
          else: newOrdering += "</b>"
          popup.addfstr(2, 2, newOrdering)
          
          row, col, index = 4, 0, 0
          for option in options:
            popup.addstr(row, col * 19 + 2, option, curses.A_STANDOUT if cursorLoc == index else curses.A_NORMAL)
            col += 1
            index += 1
            if col == 4: row, col = row + 1, 0
          
          popup.refresh()
          
          key = stdscr.getch()
          if key == curses.KEY_LEFT: cursorLoc = max(0, cursorLoc - 1)
          elif key == curses.KEY_RIGHT: cursorLoc = min(len(options) - 1, cursorLoc + 1)
          elif key == curses.KEY_UP: cursorLoc = max(0, cursorLoc - 4)
          elif key == curses.KEY_DOWN: cursorLoc = min(len(options) - 1, cursorLoc + 4)
          elif key in (curses.KEY_ENTER, 10, ord(' ')):
            # selected entry (the ord of '10' seems needed to pick up enter)
            selection = options[cursorLoc]
            if selection == "Cancel": break
            else:
              selections.append(connPanel.getSortType(selection.replace("Tor ID", "Fingerprint")))
              options.remove(selection)
              cursorLoc = min(cursorLoc, len(options) - 1)
          elif key == 27: break # esc - cancel
          
        if len(selections) == 3:
          panels["conn"].sortOrdering = selections
          panels["conn"].sortConnections()
        curses.halfdelay(REFRESH_RATE * 10) # reset normal pausing behavior
      finally:
        cursesLock.release()
    elif page == 1:
      panels["conn"].handleKey(key)
    elif page == 2:
      panels["torrc"].handleKey(key)

def startTorMonitor(conn, loggedEvents):
  curses.wrapper(drawTorMonitor, conn, loggedEvents)

