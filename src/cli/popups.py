"""
Functions for displaying popups in the interface.
"""

import curses

import controller

from util import panel, uiTools

def init(height = -1, width = -1):
  """
  Preparation for displaying a popup. This creates a popup with a valid
  subwindow instance. If that's successful then the curses lock is acquired
  and this returns a tuple of the...
  (popup, draw width, draw height)
  Otherwise this leaves curses unlocked and returns None.
  
  Arguments:
    height - maximum height of the popup
    width  - maximum width of the popup
  """
  
  topSize = controller.getPanel("header").getHeight()
  topSize += controller.getPanel("control").getHeight()
  
  popup = panel.Panel(controller.getScreen(), "popup", topSize, height, width)
  popup.setVisible(True)
  
  # Redraws the popup to prepare a subwindow instance. If none is spawned then
  # the panel can't be drawn (for instance, due to not being visible).
  popup.redraw(True)
  if popup.win != None:
    panel.CURSES_LOCK.acquire()
    return (popup, popup.maxX - 1, popup.maxY)
  else: return None

def finalize():
  """
  Cleans up after displaying a popup, releasing the cureses lock and redrawing
  the rest of the display.
  """
  
  controller.refresh()
  panel.CURSES_LOCK.release()

def showHelpPopup():
  """
  Presents a popup with instructions for the current page's hotkeys. This
  returns the user input used to close the popup. If the popup didn't close
  properly, this is an arrow, enter, or scroll key then this returns None.
  """
  
  popup, width, height = init(9, 80)
  if not popup: return
  
  exitKey = None
  try:
    pageNum = controller.getPage()
    pagePanels = controller.getPanels(pageNum)
    
    # the first page is the only one with multiple panels, and it looks better
    # with the log entries first, so reversing the order
    pagePanels.reverse()
    
    helpOptions = []
    for entry in pagePanels:
      helpOptions += entry.getHelp()
    
    # test doing afterward in case of overwriting
    popup.win.box()
    popup.addstr(0, 0, "Page %i Commands:" % pageNum, curses.A_STANDOUT)
    
    for i in range(len(helpOptions)):
      if i / 2 >= height - 2: break
      
      # draws entries in the form '<key>: <description>[ (<selection>)]', for
      # instance...
      # u: duplicate log entries (hidden)
      key, description, selection = helpOptions[i]
      if key: description = ": " + description
      row = (i / 2) + 1
      col = 2 if i % 2 == 0 else 41
      
      popup.addstr(row, col, key, curses.A_BOLD)
      col += len(key)
      popup.addstr(row, col, description)
      col += len(description)
      
      if selection:
        popup.addstr(row, col, " (")
        popup.addstr(row, col + 2, selection, curses.A_BOLD)
        popup.addstr(row, col + 2 + len(selection), ")")
    
    # tells user to press a key if the lower left is unoccupied
    if len(helpOptions) < 13 and height == 9:
      popup.addstr(7, 2, "Press any key...")
    
    popup.win.refresh()
    curses.cbreak()
    exitKey = controller.getScreen().getch()
    curses.halfdelay(controller.REFRESH_RATE * 10)
  finally: finalize()
  
  if not uiTools.isSelectionKey(exitKey) and \
    not uiTools.isScrollKey(exitKey) and \
    not exitKey in (curses.KEY_LEFT, curses.KEY_RIGHT):
    return exitKey
  else: return None

