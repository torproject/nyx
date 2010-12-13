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
  q. Quit

Selection: """

def printDivider():
  print("\n" + "-" * 40 + "\n")

conn = None
while True:
  userInput = raw_input(MENU)
  
  # initiate the TorCtl connection if the test needs it
  if userInput in ("1", "2") and not conn:
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
      
      resolverLabel = "%-10s" % connections.CMD_STR[resolver]
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
      for i in range(1, 7):
        print("  %i. %s" % (i, connections.CMD_STR[i]))
      print("  q. Go back to the main menu")
      
      userSelection = raw_input("\nSelection: ")
      if userSelection == "q":
        printDivider()
        break
      
      if userSelection.isdigit() and int(userSelection) in range(1, 7):
        try:
          resolver = int(userSelection)
          startTime = time.time()
          
          print(connections.getResolverCommand(resolver, "tor", conn.getMyPid()))
          connectionResults = connections.getConnections(resolver, "tor", conn.getMyPid())
          connectionResults.sort()
          
          # prints results
          printDivider()
          for lIp, lPort, fIp, fPort in connectionResults:
            print("  %s:%s -> %s:%s" % (lIp, lPort, fIp, fPort))
          
          print("\n  Runtime: %0.4f seconds" % (time.time() - startTime))
        except IOError, exc:
          print exc
      else:
        print("'%s' isn't a valid selection\n" % userSelection)
  elif userInput == "3":
    uiTools.demoGlyphs()
    
    # Switching to a curses context and back repetedy seems to screw up the
    # terminal. Just to be safe this ends the process after the demo.
    break
  else:
    print("'%s' isn't a valid selection\n" % userInput)

