"""
Wrapper for safely working with curses subwindows.
"""

import sys
import traceback
import curses
from threading import RLock

from util import log, uiTools

# global ui lock governing all panel instances (curses isn't thread save and 
# concurrency bugs produce especially sinister glitches)
CURSES_LOCK = RLock()

# tags used by addfstr - this maps to functor/argument combinations since the
# actual values (in the case of color attributes) might not yet be initialized
def _noOp(arg): return arg
FORMAT_TAGS = {"<b>": (_noOp, curses.A_BOLD),
               "<u>": (_noOp, curses.A_UNDERLINE),
               "<h>": (_noOp, curses.A_STANDOUT)}
for colorLabel in uiTools.COLOR_LIST: FORMAT_TAGS["<%s>" % colorLabel] = (uiTools.getColor, colorLabel)

CONFIG = {"log.panelRecreated": log.DEBUG}

def loadConfig(config):
  config.update(CONFIG)

class Panel():
  """
  Wrapper for curses subwindows. This hides most of the ugliness in common
  curses operations including:
    - locking when concurrently drawing to multiple windows
    - gracefully handle terminal resizing
    - clip text that falls outside the panel
    - convenience methods for word wrap, in-line formatting, etc
  
  This uses a design akin to Swing where panel instances provide their display
  implementation by overwriting the draw() method, and are redrawn with
  redraw().
  """
  
  def __init__(self, parent, name, top, height=-1, width=-1):
    """
    Creates a durable wrapper for a curses subwindow in the given parent.
    
    Arguments:
      parent - parent curses window
      name   - identifier for the panel
      top    - positioning of top within parent
      height - maximum height of panel (uses all available space if -1)
      width  - maximum width of panel (uses all available space if -1)
    """
    
    # The not-so-pythonic getters for these parameters are because some
    # implementations aren't entirely deterministic (for instance panels
    # might chose their height based on its parent's current width).
    
    self.parent = parent
    self.panelName = name
    self.top = top
    self.height = height
    self.width = width
    
    # The panel's subwindow instance. This is made available to implementors
    # via their draw method and shouldn't be accessed directly.
    # 
    # This is None if either the subwindow failed to be created or needs to be
    # remade before it's used. The later could be for a couple reasons:
    # - The subwindow was never initialized.
    # - Any of the parameters used for subwindow initialization have changed.
    self.win = None
    
    self.maxY, self.maxX = -1, -1 # subwindow dimensions when last redrawn
  
  def getName(self):
    """
    Provides panel's identifier.
    """
    
    return self.panelName
  
  def getParent(self):
    """
    Provides the parent used to create subwindows.
    """
    
    return self.parent
  
  def setParent(self, parent):
    """
    Changes the parent used to create subwindows.
    
    Arguments:
      parent - parent curses window
    """
    
    if self.parent != parent:
      self.parent = parent
      self.win = None
  
  def getTop(self):
    """
    Provides the position subwindows are placed at within its parent.
    """
    
    return self.top
  
  def setTop(self, top):
    """
    Changes the position where subwindows are placed within its parent.
    
    Arguments:
      top - positioning of top within parent
    """
    
    if self.top != top:
      self.top = top
      self.win = None
  
  def getHeight(self):
    """
    Provides the height used for subwindows (-1 if it isn't limited).
    """
    
    return self.height
  
  def setHeight(self, height):
    """
    Changes the height used for subwindows. This uses all available space if -1.
    
    Arguments:
      height - maximum height of panel (uses all available space if -1)
    """
    
    if self.height != height:
      self.height = height
      self.win = None
  
  def getWidth(self):
    """
    Provides the width used for subwindows (-1 if it isn't limited).
    """
    
    return self.width
  
  def setWidth(self, width):
    """
    Changes the width used for subwindows. This uses all available space if -1.
    
    Arguments:
      width - maximum width of panel (uses all available space if -1)
    """
    
    if self.width != width:
      self.width = width
      self.win = None
  
  def getPreferredSize(self):
    """
    Provides the dimensions the subwindow would use when next redrawn, given
    that none of the properties of the panel or parent change before then. This
    returns a tuple of (height, width).
    """
    
    newHeight, newWidth = self.parent.getmaxyx()
    setHeight, setWidth = self.getHeight(), self.getWidth()
    newHeight = max(0, newHeight - self.top)
    if setHeight != -1: newHeight = min(newHeight, setHeight)
    if setWidth != -1: newWidth = min(newWidth, setWidth)
    return (newHeight, newWidth)
  
  def draw(self, subwindow, width, height):
    """
    Draws display's content. This is meant to be overwritten by 
    implementations and not called directly (use redraw() instead). The
    dimensions provided are the drawable dimensions, which in terms of width is
    a column less than the actual space.
    
    Arguments:
      sudwindow - panel's current subwindow instance, providing raw access to
                  its curses functions
      width     - horizontal space available for content
      height    - vertical space available for content
    """
    
    pass
  
  def redraw(self, forceRedraw=False, block=False):
    """
    Clears display and redraws its content. This can skip redrawing content if
    able (ie, the subwindow's unchanged), instead just refreshing the display.
    
    Arguments:
      forceRedraw - forces the content to be cleared and redrawn if true
      block       - if drawing concurrently with other panels this determines
                    if the request is willing to wait its turn or should be
                    abandoned
    """
    
    # if the panel's completely outside its parent then this is a no-op
    newHeight, newWidth = self.getPreferredSize()
    if newHeight == 0:
      self.win = None
      return
    
    # recreates the subwindow if necessary
    isNewWindow = self._resetSubwindow()
    
    # The reset argument is disregarded in a couple of situations:
    # - The subwindow's been recreated (obviously it then doesn't have the old
    #   content to refresh).
    # - The subwindow's dimensions have changed since last drawn (this will
    #   likely change the content's layout)
    
    subwinMaxY, subwinMaxX = self.win.getmaxyx()
    if isNewWindow or subwinMaxY != self.maxY or subwinMaxX != self.maxX:
      forceRedraw = True
    
    self.maxY, self.maxX = subwinMaxY, subwinMaxX
    if not CURSES_LOCK.acquire(block): return
    try:
      if forceRedraw:
        self.win.erase() # clears any old contents
        self.draw(self.win, self.maxX - 1, self.maxY)
      self.win.refresh()
    except:
      # without terminating curses continues in a zombie state (requiring a
      # kill signal to quit, and screwing up the terminal)
      # TODO: provide a nicer, general purpose handler for unexpected exceptions
      try:
        tracebackFile = open("/tmp/armTraceback", "w")
        traceback.print_exc(file=tracebackFile)
      finally:
        sys.exit(1)
    finally:
      CURSES_LOCK.release()
  
  def addstr(self, y, x, msg, attr=curses.A_NORMAL):
    """
    Writes string to subwindow if able. This takes into account screen bounds
    to avoid making curses upset. This should only be called from the context
    of a panel's draw method.
    
    Arguments:
      y    - vertical location
      x    - horizontal location
      msg  - text to be added
      attr - text attributes
    """
    
    # subwindows need a single character buffer (either in the x or y 
    # direction) from actual content to prevent crash when shrank
    if self.win and self.maxX > x and self.maxY > y:
      self.win.addstr(y, x, msg[:self.maxX - x - 1], attr)
  
  def addfstr(self, y, x, msg):
    """
    Writes string to subwindow. The message can contain xhtml-style tags for
    formatting, including:
    <b>text</b>               bold
    <u>text</u>               underline
    <h>text</h>               highlight
    <[color]>text</[color]>   use color (see uiTools.getColor() for constants)
    
    Tag nesting is supported and tag closing is strictly enforced (raising an
    exception for invalid formatting). Unrecognized tags are treated as normal
    text. This should only be called from the context of a panel's draw method.
    
    Text in multiple color tags (for instance "<blue><red>hello</red></blue>")
    uses the bitwise OR of those flags (hint: that's probably not what you
    want).
    
    Arguments:
      y    - vertical location
      x    - horizontal location
      msg  - formatted text to be added
    """
    
    if self.win and self.maxY > y:
      formatting = [curses.A_NORMAL]
      expectedCloseTags = []
      unusedMsg = msg
      
      while self.maxX > x and len(unusedMsg) > 0:
        # finds next consumeable tag (left as None if there aren't any left)
        nextTag, tagStart, tagEnd = None, -1, -1
        
        tmpChecked = 0 # portion of the message cleared for having any valid tags
        expectedTags = FORMAT_TAGS.keys() + expectedCloseTags
        while nextTag == None:
          tagStart = unusedMsg.find("<", tmpChecked)
          tagEnd = unusedMsg.find(">", tagStart) + 1 if tagStart != -1 else -1
          
          if tagStart == -1 or tagEnd == -1: break # no more tags to consume
          else:
            # check if the tag we've found matches anything being expected
            if unusedMsg[tagStart:tagEnd] in expectedTags:
              nextTag = unusedMsg[tagStart:tagEnd]
              break # found a tag to use
            else:
              # not a valid tag - narrow search to everything after it
              tmpChecked = tagEnd
        
        # splits into text before and after tag
        if nextTag:
          msgSegment = unusedMsg[:tagStart]
          unusedMsg = unusedMsg[tagEnd:]
        else:
          msgSegment = unusedMsg
          unusedMsg = ""
        
        # adds text before tag with current formatting
        attr = 0
        for format in formatting: attr |= format
        self.win.addstr(y, x, msgSegment[:self.maxX - x - 1], attr)
        x += len(msgSegment)
        
        # applies tag attributes for future text
        if nextTag:
          formatTag = "<" + nextTag[2:] if nextTag.startswith("</") else nextTag
          formatMatch = FORMAT_TAGS[formatTag][0](FORMAT_TAGS[formatTag][1])
          
          if not nextTag.startswith("</"):
            # open tag - add formatting
            expectedCloseTags.append("</" + nextTag[1:])
            formatting.append(formatMatch)
          else:
            # close tag - remove formatting
            expectedCloseTags.remove(nextTag)
            formatting.remove(formatMatch)
      
      # only check for unclosed tags if we processed the whole message (if we
      # stopped processing prematurely it might still be valid)
      if expectedCloseTags and not unusedMsg:
        # if we're done then raise an exception for any unclosed tags (tisk, tisk)
        baseMsg = "Unclosed formatting tag%s:" % ("s" if len(expectedCloseTags) > 1 else "")
        raise ValueError("%s: '%s'\n  \"%s\"" % (baseMsg, "', '".join(expectedCloseTags), msg))
  
  def addScrollBar(self, top, bottom, size, drawTop = 0, drawBottom = -1):
    """
    Draws a left justified scroll bar reflecting position within a vertical
    listing. This is shorted if necessary, and left undrawn if no space is
    available. The bottom is squared off, having a layout like:
     | 
    *|
    *|
    *|
     |
    -+
    
    This should only be called from the context of a panel's draw method.
    
    Arguments:
      top        - list index for the top-most visible element
      bottom     - list index for the bottom-most visible element
      size       - size of the list in which the listed elements are contained
      drawTop    - starting row where the scroll bar should be drawn
      drawBottom - ending row where the scroll bar should end, -1 if it should
                   span to the bottom of the panel
    """
    
    if (self.maxY - drawTop) < 2: return # not enough room
    
    # sets drawBottom to be the actual row on which the scrollbar should end
    if drawBottom == -1: drawBottom = self.maxY - 1
    else: drawBottom = min(drawBottom, self.maxY - 1)
    
    # determines scrollbar dimensions
    scrollbarHeight = drawBottom - drawTop
    sliderTop = scrollbarHeight * top / size
    sliderSize = scrollbarHeight * (bottom - top) / size
    
    # ensures slider isn't at top or bottom unless really at those extreme bounds
    if top > 0: sliderTop = max(sliderTop, 1)
    if bottom != size: sliderTop = min(sliderTop, scrollbarHeight - sliderSize - 2)
    
    # draws scrollbar slider
    for i in range(scrollbarHeight):
      if i >= sliderTop and i <= sliderTop + sliderSize:
        self.addstr(i + drawTop, 0, " ", curses.A_STANDOUT)
    
    # draws box around the scroll bar
    self.win.vline(drawTop, 1, curses.ACS_VLINE, self.maxY - 2)
    self.win.vline(drawBottom, 1, curses.ACS_LRCORNER, 1)
    self.win.hline(drawBottom, 0, curses.ACS_HLINE, 1)
  
  def _resetSubwindow(self):
    """
    Create a new subwindow instance for the panel if:
    - Panel currently doesn't have a subwindow (was uninitialized or
      invalidated).
    - There's room for the panel to grow vertically (curses automatically
      lets subwindows regrow horizontally, but not vertically).
    - The subwindow has been displaced. This is a curses display bug that
      manifests if the terminal's shrank then re-expanded. Displaced
      subwindows are never restored to their proper position, resulting in
      graphical glitches if we draw to them.
    - The preferred size is smaller than the actual size (should shrink).
    
    This returns True if a new subwindow instance was created, False otherwise.
    """
    
    newHeight, newWidth = self.getPreferredSize()
    if newHeight == 0: return False # subwindow would be outside its parent
    
    # determines if a new subwindow should be recreated
    recreate = self.win == None
    if self.win:
      subwinMaxY, subwinMaxX = self.win.getmaxyx()
      recreate |= subwinMaxY < newHeight              # check for vertical growth
      recreate |= self.top > self.win.getparyx()[0]   # check for displacement
      recreate |= subwinMaxX > newWidth or subwinMaxY > newHeight # shrinking
    
    # I'm not sure if recreating subwindows is some sort of memory leak but the
    # Python curses bindings seem to lack all of the following:
    # - subwindow deletion (to tell curses to free the memory)
    # - subwindow moving/resizing (to restore the displaced windows)
    # so this is the only option (besides removing subwindows entirely which 
    # would mean far more complicated code and no more selective refreshing)
    
    if recreate:
      self.win = self.parent.subwin(newHeight, newWidth, self.top, 0)
      
      # note: doing this log before setting win produces an infinite loop
      msg = "recreating panel '%s' with the dimensions of %i/%i" % (self.getName(), newHeight, newWidth)
      log.log(CONFIG["log.panelRecreated"], msg)
    return recreate
  
