"""
Provides a dialog with client locale or exiting port counts.
"""

import curses
import operator

import cli.controller
import cli.popups

from util import connections, enum, log, uiTools

CountType = enum.Enum("CLIENT_LOCALE", "EXIT_PORT")
EXIT_USAGE_WIDTH = 15

def showCountDialog(countType, counts):
  """
  Provides a dialog with bar graphs and percentages for the given set of
  counts. Pressing any key closes the dialog.
  
  Arguments:
    countType - type of counts being presented
    counts    - mapping of labels to counts
  """
  
  isNoStats = not counts
  noStatsMsg = "Usage stats aren't available yet, press any key..."
  
  if isNoStats:
    popup, width, height = cli.popups.init(3, len(noStatsMsg) + 4)
  else:
    popup, width, height = cli.popups.init(4 + max(1, len(counts)), 80)
  if not popup: return
  
  try:
    control = cli.controller.getController()
    
    popup.win.box()
    
    # dialog title
    if countType == CountType.CLIENT_LOCALE:
      title = "Client Locales"
    elif countType == CountType.EXIT_PORT:
      title = "Exiting Port Usage"
    else:
      title = ""
      log.log(log.WARN, "Unrecognized count type: %s" % countType)
    
    popup.addstr(0, 0, title, curses.A_STANDOUT)
    
    if isNoStats:
      popup.addstr(1, 2, noStatsMsg, curses.A_BOLD | uiTools.getColor("cyan"))
    else:
      sortedCounts = sorted(counts.iteritems(), key=operator.itemgetter(1))
      sortedCounts.reverse()
      
      # constructs string formatting for the max key and value display width
      keyWidth, valWidth, valueTotal = 3, 1, 0
      for k, v in sortedCounts:
        keyWidth = max(keyWidth, len(k))
        valWidth = max(valWidth, len(str(v)))
        valueTotal += v
      
      # extra space since we're adding usage informaion
      if countType == CountType.EXIT_PORT:
        keyWidth += EXIT_USAGE_WIDTH
      
      labelFormat = "%%-%is %%%ii (%%%%%%-2i)" % (keyWidth, valWidth)
      
      for i in range(height - 4):
        k, v = sortedCounts[i]
        
        # includes a port usage column
        if countType == CountType.EXIT_PORT:
          usage = connections.getPortUsage(k)
          
          if usage:
            keyFormat = "%%-%is   %%s" % (keyWidth - EXIT_USAGE_WIDTH)
            k = keyFormat % (k, usage[:EXIT_USAGE_WIDTH - 3])
        
        label = labelFormat % (k, v, v * 100 / valueTotal)
        popup.addstr(i + 1, 2, label, curses.A_BOLD | uiTools.getColor("green"))
        
        # All labels have the same size since they're based on the max widths.
        # If this changes then this'll need to be the max label width.
        labelWidth = len(label)
        
        # draws simple bar graph for percentages
        fillWidth = v * (width - 4 - labelWidth) / valueTotal
        for j in range(fillWidth):
          popup.addstr(i + 1, 3 + labelWidth + j, " ", curses.A_STANDOUT | uiTools.getColor("red"))
      
      popup.addstr(height - 2, 2, "Press any key...")
    
    popup.win.refresh()
    
    curses.cbreak()
    control.getScreen().getch()
  finally: cli.popups.finalize()

