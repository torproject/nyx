#!/usr/bin/env python

"""
Handler for arm tests and demos.
"""

import time
from util import connections, torTools, uiTools

MENU = """Arm Test Options:
  1. Resolver Performance Test
  2. Resolver Dump
  3. Glyph Demo
  4. Exit Policy Check
  q. Quit

Selection: """

def printDivider():
  print("\n" + "-" * 40 + "\n")

conn = None
while True:
  userInput = raw_input(MENU)
  
  # initiate the TorCtl connection if the test needs it
  if userInput in ("1", "2", "4") and not conn:
    conn = torTools.getConn()
    conn.init()
    
    # prefetch pid so extra system calls don't effect the timing of tests
    conn.getMyPid()
  
  if userInput == "q":
    break # quit test script
  elif userInput == "1":
    systemResolvers = connections.getSystemResolvers()
    printDivider()
    
    allConnectionResults = []
    for resolver in systemResolvers:
      startTime = time.time()
      connectionResults = connections.getConnections(resolver, "tor", conn.getMyPid())
      connectionResults.sort()
      allConnectionResults.append(connectionResults)
      
      resolverLabel = "%-10s" % resolver
      countLabel = "%4i results" % len(connectionResults)
      timeLabel = "%0.4f seconds" % (time.time() - startTime)
      print "%s %s     %s" % (resolverLabel, countLabel, timeLabel)
    
    allResolversMatch = True
    firstResult = allConnectionResults.pop()
    while allConnectionResults:
      if allConnectionResults.pop() != firstResult:
        allResolversMatch = False
        break
    
    if allResolversMatch:
      print("\nThe results of all the connection resolvers match")
    else:
      print("\nWarning: Connection resolver results differ")
    
    printDivider()
  elif userInput == "2":
    # use the given resolver to fetch tor's connections
    while True:
      # provide the selection options
      printDivider()
      print("Select a resolver:")
      availableResolvers = connections.Resolver.values()
      for i in range(len(availableResolvers)):
        print("  %i. %s" % (i, availableResolvers[i]))
      print("  q. Go back to the main menu")
      
      userSelection = raw_input("\nSelection: ")
      if userSelection == "q":
        printDivider()
        break
      
      if userSelection.isdigit() and int(userSelection) in range(0, 7):
        try:
          resolver = connections.Resolver.values()[int(userSelection)]
          startTime = time.time()
          
          print(connections.getResolverCommand(resolver, "tor", conn.getMyPid()))
          connectionResults = connections.getConnections(resolver, "tor", conn.getMyPid())
          connectionResults.sort()
          
          # prints results
          printDivider()
          for lIp, lPort, fIp, fPort in connectionResults:
            print("  %s:%s -> %s:%s" % (lIp, lPort, fIp, fPort))
          
          print("\n  Runtime: %0.4f seconds" % (time.time() - startTime))
        except (IOError, IndexError), exc:
          print exc
      else:
        print("'%s' isn't a valid selection\n" % userSelection)
  elif userInput == "3":
    uiTools.demoGlyphs()
    
    # Switching to a curses context and back repeatedly seems to screw up the
    # terminal. Just to be safe this ends the process after the demo.
    break
  elif userInput == "4":
    # display the current exit policy and query if destinations are allowed by it
    exitPolicy = conn.getExitPolicy()
    print("Exit Policy: %s" % exitPolicy)
    printDivider()
    
    while True:
      # provide the selection options
      userSelection = raw_input("\nCheck if destination is allowed (q to go back): ")
      userSelection = userSelection.replace(" ", "").strip() # removes all whitespace
      
      isValidQuery, isExitAllowed = True, False
      if userSelection == "q":
        printDivider()
        break
      elif connections.isValidIpAddress(userSelection):
        # just an ip address (use port 80)
        isExitAllowed = exitPolicy.check(userSelection, 80)
      elif userSelection.isdigit():
        # just a port (use a common ip like 4.2.2.2)
        isExitAllowed = exitPolicy.check("4.2.2.2", userSelection)
      elif ":" in userSelection:
        # ip/port combination
        ipAddr, port = userSelection.split(":", 1)
        
        if connections.isValidIpAddress(ipAddr) and port.isdigit():
          isExitAllowed = exitPolicy.check(ipAddr, port)
        else: isValidQuery = False
      else: isValidQuery = False # invalid input
      
      if isValidQuery:
        resultStr = "is" if isExitAllowed else "is *not*"
        print("Exiting %s allowed to that destination" % resultStr)
      else:
        print("'%s' isn't a valid destination (should be an ip, port, or ip:port)\n" % userSelection)
    
  else:
    print("'%s' isn't a valid selection\n" % userInput)

