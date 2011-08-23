"""
Wrapper for safely working with curses subwindows.
"""

import copy
import time
import curses
import curses.ascii
import curses.textpad
from threading import RLock

from util import log, textInput, uiTools

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

# prevents curses redraws if set
HALT_ACTIVITY = False

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
  
  def __init__(self, parent, name, top, left=0, height=-1, width=-1):
    """
    Creates a durable wrapper for a curses subwindow in the given parent.
    
    Arguments:
      parent - parent curses window
      name   - identifier for the panel
      top    - positioning of top within parent
      left   - positioning of the left edge within the parent
      height - maximum height of panel (uses all available space if -1)
      width  - maximum width of panel (uses all available space if -1)
    """
    
    # The not-so-pythonic getters for these parameters are because some
    # implementations aren't entirely deterministic (for instance panels
    # might chose their height based on its parent's current width).
    
    self.panelName = name
    self.parent = parent
    self.visible = False
    self.titleVisible = True
    
    # Attributes for pausing. The pauseAttr contains variables our getAttr
    # method is tracking, and the pause buffer has copies of the values from
    # when we were last unpaused (unused unless we're paused).
    
    self.paused = False
    self.pauseAttr = []
    self.pauseBuffer = {}
    self.pauseTime = -1
    
    self.top = top
    self.left = left
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
  
  def isTitleVisible(self):
    """
    True if the title is configured to be visible, False otherwise.
    """
    
    return self.titleVisible
  
  def setTitleVisible(self, isVisible):
    """
    Configures the panel's title to be visible or not when it's next redrawn.
    This is not guarenteed to be respected (not all panels have a title).
    """
    
    self.titleVisible = isVisible
  
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
  
  def isVisible(self):
    """
    Provides if the panel's configured to be visible or not.
    """
    
    return self.visible
  
  def setVisible(self, isVisible):
    """
    Toggles if the panel is visible or not.
    
    Arguments:
      isVisible - panel is redrawn when requested if true, skipped otherwise
    """
    
    self.visible = isVisible
  
  def isPaused(self):
    """
    Provides if the panel's configured to be paused or not.
    """
    
    return self.paused
  
  def setPauseAttr(self, attr):
    """
    Configures the panel to track the given attribute so that getAttr provides
    the value when it was last unpaused (or its current value if we're
    currently unpaused). For instance...
    
    > self.setPauseAttr("myVar")
    > self.myVar = 5
    > self.myVar = 6 # self.getAttr("myVar") -> 6
    > self.setPaused(True)
    > self.myVar = 7 # self.getAttr("myVar") -> 6
    > self.setPaused(False)
    > self.myVar = 7 # self.getAttr("myVar") -> 7
    
    Arguments:
      attr - parameter to be tracked for getAttr
    """
    
    self.pauseAttr.append(attr)
    self.pauseBuffer[attr] = self.copyAttr(attr)
  
  def getAttr(self, attr):
    """
    Provides the value of the given attribute when we were last unpaused. If
    we're currently unpaused then this is the current value. If untracked this
    returns None.
    
    Arguments:
      attr - local variable to be returned
    """
    
    if not attr in self.pauseAttr: return None
    elif self.paused: return self.pauseBuffer[attr]
    else: return self.__dict__.get(attr)
  
  def copyAttr(self, attr):
    """
    Provides a duplicate of the given configuration value, suitable for the
    pause buffer.
    
    Arguments:
      attr - parameter to be provided back
    """
    
    currentValue = self.__dict__.get(attr)
    return copy.copy(currentValue)
  
  def setPaused(self, isPause, suppressRedraw = False):
    """
    Toggles if the panel is paused or not. This causes the panel to be redrawn
    when toggling is pause state unless told to do otherwise. This is
    important when pausing since otherwise the panel's display could change
    when redrawn for other reasons.
    
    This returns True if the panel's pause state was changed, False otherwise.
    
    Arguments:
      isPause        - freezes the state of the pause attributes if true, makes
                       them editable otherwise
      suppressRedraw - if true then this will never redraw the panel
    """
    
    if isPause != self.paused:
      if isPause: self.pauseTime = time.time()
      self.paused = isPause
      
      if isPause:
        # copies tracked attributes so we know what they were before pausing
        for attr in self.pauseAttr:
          self.pauseBuffer[attr] = self.copyAttr(attr)
      
      if not suppressRedraw: self.redraw(True)
      return True
    else: return False
  
  def getPauseTime(self):
    """
    Provides the time that we were last paused, returning -1 if we've never
    been paused.
    """
    
    return self.pauseTime
  
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
  
  def getLeft(self):
    """
    Provides the left position where this subwindow is placed within its
    parent.
    """
    
    return self.left
  
  def setLeft(self, left):
    """
    Changes the left position where this subwindow is placed within its parent.
    
    Arguments:
      left - positioning of top within parent
    """
    
    if self.left != left:
      self.left = left
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
    newWidth = max(0, newWidth - self.left)
    if setHeight != -1: newHeight = min(newHeight, setHeight)
    if setWidth != -1: newWidth = min(newWidth, setWidth)
    return (newHeight, newWidth)
  
  def handleKey(self, key):
    """
    Handler for user input. This returns true if the key press was consumed,
    false otherwise.
    
    Arguments:
      key - keycode for the key pressed
    """
    
    return False
  
  def getHelp(self):
    """
    Provides help information for the controls this page provides. This is a
    list of tuples of the form...
    (control, description, status)
    """
    
    return []
  
  def draw(self, width, height):
    """
    Draws display's content. This is meant to be overwritten by 
    implementations and not called directly (use redraw() instead). The
    dimensions provided are the drawable dimensions, which in terms of width is
    a column less than the actual space.
    
    Arguments:
      width  - horizontal space available for content
      height - vertical space available for content
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
    
    # skipped if not currently visible or activity has been halted
    if not self.isVisible() or HALT_ACTIVITY: return
    
    # if the panel's completely outside its parent then this is a no-op
    newHeight, newWidth = self.getPreferredSize()
    if newHeight == 0 or newWidth == 0:
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
        self.draw(self.maxX, self.maxY)
      self.win.refresh()
    finally:
      CURSES_LOCK.release()
  
  def hline(self, y, x, length, attr=curses.A_NORMAL):
    """
    Draws a horizontal line. This should only be called from the context of a
    panel's draw method.
    
    Arguments:
      y      - vertical location
      x      - horizontal location
      length - length the line spans
      attr   - text attributes
    """
    
    if self.win and self.maxX > x and self.maxY > y:
      try:
        drawLength = min(length, self.maxX - x)
        self.win.hline(y, x, curses.ACS_HLINE | attr, drawLength)
      except:
        # in edge cases drawing could cause a _curses.error
        pass
  
  def vline(self, y, x, length, attr=curses.A_NORMAL):
    """
    Draws a vertical line. This should only be called from the context of a
    panel's draw method.
    
    Arguments:
      y      - vertical location
      x      - horizontal location
      length - length the line spans
      attr   - text attributes
    """
    
    if self.win and self.maxX > x and self.maxY > y:
      try:
        drawLength = min(length, self.maxY - y)
        self.win.vline(y, x, curses.ACS_VLINE | attr, drawLength)
      except:
        # in edge cases drawing could cause a _curses.error
        pass
  
  def addch(self, y, x, char, attr=curses.A_NORMAL):
    """
    Draws a single character. This should only be called from the context of a
    panel's draw method.
    
    Arguments:
      y    - vertical location
      x    - horizontal location
      char - character to be drawn
      attr - text attributes
    """
    
    if self.win and self.maxX > x and self.maxY > y:
      try:
        self.win.addch(y, x, char, attr)
      except:
        # in edge cases drawing could cause a _curses.error
        pass
  
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
      try:
        self.win.addstr(y, x, msg[:self.maxX - x], attr)
      except:
        # this might produce a _curses.error during edge cases, for instance
        # when resizing with visible popups
        pass
  
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
  
  def getstr(self, y, x, initialText = "", format = None, maxWidth = None, validator = None):
    """
    Provides a text field where the user can input a string, blocking until
    they've done so and returning the result. If the user presses escape then
    this terminates and provides back None. This should only be called from
    the context of a panel's draw method.
    
    This blanks any content within the space that the input field is rendered
    (otherwise stray characters would be interpreted as part of the initial
    input).
    
    Arguments:
      y           - vertical location
      x           - horizontal location
      initialText - starting text in this field
      format      - format used for the text
      maxWidth    - maximum width for the text field
      validator   - custom TextInputValidator for handling keybindings
    """
    
    if not format: format = curses.A_NORMAL
    
    # makes cursor visible
    try: previousCursorState = curses.curs_set(1)
    except curses.error: previousCursorState = 0
    
    # temporary subwindow for user input
    displayWidth = self.getPreferredSize()[1]
    if maxWidth: displayWidth = min(displayWidth, maxWidth + x)
    inputSubwindow = self.parent.subwin(1, displayWidth - x, self.top + y, self.left + x)
    
    # blanks the field's area, filling it with the font in case it's hilighting
    inputSubwindow.clear()
    inputSubwindow.bkgd(' ', format)
    
    # prepopulates the initial text
    if initialText:
      inputSubwindow.addstr(0, 0, initialText[:displayWidth - x - 1], format)
    
    # Displays the text field, blocking until the user's done. This closes the
    # text panel and returns userInput to the initial text if the user presses
    # escape.
    
    textbox = curses.textpad.Textbox(inputSubwindow)
    
    if not validator:
      validator = textInput.BasicValidator()
    
    textbox.win.attron(format)
    userInput = textbox.edit(lambda key: validator.validate(key, textbox)).strip()
    textbox.win.attroff(format)
    if textbox.lastcmd == curses.ascii.BEL: userInput = None
    
    # reverts visability settings
    try: curses.curs_set(previousCursorState)
    except curses.error: pass
    
    return userInput
  
  def addScrollBar(self, top, bottom, size, drawTop = 0, drawBottom = -1, drawLeft = 0):
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
      drawLeft   - left offset at which to draw the scroll bar
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
    
    # avoids a rounding error that causes the scrollbar to be too low when at
    # the bottom
    if bottom == size: sliderTop = scrollbarHeight - sliderSize - 1
    
    # draws scrollbar slider
    for i in range(scrollbarHeight):
      if i >= sliderTop and i <= sliderTop + sliderSize:
        self.addstr(i + drawTop, drawLeft, " ", curses.A_STANDOUT)
      else:
        self.addstr(i + drawTop, drawLeft, " ")
    
    # draws box around the scroll bar
    self.vline(drawTop, drawLeft + 1, drawBottom - 1)
    self.addch(drawBottom, drawLeft + 1, curses.ACS_LRCORNER)
    self.addch(drawBottom, drawLeft, curses.ACS_HLINE)
  
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
      self.win = self.parent.subwin(newHeight, newWidth, self.top, self.left)
      
      # note: doing this log before setting win produces an infinite loop
      msg = "recreating panel '%s' with the dimensions of %i/%i" % (self.getName(), newHeight, newWidth)
      log.log(CONFIG["log.panelRecreated"], msg)
    return recreate

