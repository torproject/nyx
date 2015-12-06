"""
Panel presenting the configuration state for tor or nyx. Options can be edited
and the resulting configuration files saved.
"""

import curses

import nyx.controller
import nyx.popups

import stem.control
import stem.manual

from nyx.util import panel, tor_config, tor_controller, ui_tools

from stem.util import conf, enum, log, str_tools

try:
  # added in python 3.2
  from functools import lru_cache
except ImportError:
  from stem.util.lru_cache import lru_cache

SortAttr = enum.Enum('OPTION', 'VALUE', 'VALUE_TYPE', 'CATEGORY', 'USAGE', 'SUMMARY', 'DESCRIPTION', 'MAN_PAGE_ENTRY', 'IS_SET')

DETAILS_HEIGHT = 6
OPTION_WIDTH = 25
VALUE_WIDTH = 15


def conf_handler(key, value):
  if key == 'features.config.order':
    return conf.parse_enum_csv(key, value[0], SortAttr, 3)


CONFIG = conf.config_dict('nyx', {
  'attr.config.category_color': {},
  'attr.config.sort_color': {},
  'features.config.order': [SortAttr.MAN_PAGE_ENTRY, SortAttr.OPTION, SortAttr.IS_SET],
  'features.config.state.showPrivateOptions': False,
  'features.config.state.showVirtualOptions': False,
}, conf_handler)


@lru_cache()
def tor_manual():
  try:
    return stem.manual.Manual.from_man()
  except IOError as exc:
    log.debug("Unable to use 'man tor' to get information about config options (%s), using bundled information instead" % exc)
    return stem.manual.Manual.from_cache()


class ConfigEntry():
  """
  Configuration option in the panel.
  """

  def __init__(self, option, entry_type):
    self._option = option
    self._value_type = entry_type
    self._man_entry = tor_manual().config_options.get(option)

  def category(self):
    """
    Provides the category of this configuration option.

    :returns: **Category** this option belongs to
    """

    return self._man_entry.category if self._man_entry else stem.manual.Category.UNKNOWN

  def option(self):
    """
    Provides the name of the configuration option.

    :returns: **str** of the configuration option
    """

    return self._option

  def value(self):
    """
    Provides the value of this configuration option.

    :returns: **str** representation of the current config value
    """

    conf_value = ', '.join(tor_controller().get_conf(self.option(), [], True))

    # provides nicer values for recognized types

    if not conf_value:
      conf_value = '<none>'
    elif self.value_type() == 'Boolean' and conf_value in ('0', '1'):
      conf_value = 'False' if conf_value == '0' else 'True'
    elif self.value_type() == 'DataSize' and conf_value.isdigit():
      conf_value = str_tools.size_label(int(conf_value))
    elif self.value_type() == 'TimeInterval' and conf_value.isdigit():
      conf_value = str_tools.time_label(int(conf_value), is_long = True)

    return conf_value

  def value_type(self):
    """
    Provides this configuration value's type.

    :returns: **str** representation of this configuration value's type
    """

    return self._value_type  # TODO: should this be an enum instead?

  def summary(self):
    """
    Provides a summery of this configuration option.

    :returns: short **str** description of the option
    """

    return self._man_entry.summary if self._man_entry else ''

  def manual_entry(self):
    """
    Provides the entry's man page entry.

    :returns: :class:`~stem.manual.ConfigOption` if it was loaded, and **None** otherwise
    """

    return self._man_entry

  def is_set(self):
    """
    Checks if the configuration option has a custom value.

    :returns: **True** if the option has a custom value, **False** otherwise
    """

    return bool(tor_controller().get_conf(self.option(), [], False))

  def sort_value(self, attr):
    """
    Provides a heuristic for sorting by a given value.

    :param SortAttr attr: sort attribute to provide a heuristic for

    :returns: comparable value for sorting
    """

    if attr == SortAttr.CATEGORY:
      return self.category()
    elif attr == SortAttr.OPTION:
      return self.option()
    elif attr == SortAttr.VALUE:
      return self.value()
    elif attr == SortAttr.VALUE_TYPE:
      return self.value_type()
    elif attr == SortAttr.USAGE:
      return self._man_entry.usage if self._man_entry else ''
    elif attr == SortAttr.SUMMARY:
      return self.summary()
    elif attr == SortAttr.DESCRIPTION:
      return self._man_entry.description if self._man_entry else ''
    elif attr == SortAttr.MAN_PAGE_ENTRY:
      return tor_manual().config_options.keys().index(self.option()) if self.option() in tor_manual().config_options else 99999  # sorts non-man entries last
    elif attr == SortAttr.IS_SET:
      return not self.is_set()


class ConfigPanel(panel.Panel):
  """
  Renders a listing of the tor or nyx configuration state, allowing options to
  be selected and edited.
  """

  def __init__(self, stdscr):
    panel.Panel.__init__(self, stdscr, 'configuration', 0)

    self._conf_contents = []
    self._conf_important_contents = []
    self._scroller = ui_tools.Scroller(True)
    self._sort_order = CONFIG['features.config.order']
    self._show_all = False  # show all options, or just the 'important' ones

    tor_controller().add_status_listener(self.reset_listener)
    self._load_config_options()

  def reset_listener(self, controller, event_type, _):
    # fetches configuration options if a new instance, otherewise keeps our
    # current contents

    if event_type == stem.control.State.INIT:
      self._load_config_options()

  def _load_config_options(self):
    """
    Fetches the configuration options available from tor or nyx.
    """

    self._conf_contents = []
    self._conf_important_contents = []

    config_names = tor_controller().get_info('config/names', None)

    if config_names:
      for line in config_names.strip().split('\n'):
        # lines are of the form "<option> <type>[ <documentation>]", like:
        # UseEntryGuards Boolean
        # documentation is aparently only in older versions (for instance,
        # 0.2.1.25)

        line_comp = line.strip().split(' ')
        conf_option, conf_type = line_comp[0], line_comp[1]

        # skips private and virtual entries if not configured to show them

        if not CONFIG['features.config.state.showPrivateOptions'] and conf_option.startswith('__'):
          continue
        elif not CONFIG['features.config.state.showVirtualOptions'] and conf_type == 'Virtual':
          continue

        self._conf_contents.append(ConfigEntry(conf_option, conf_type))

    # mirror listing with only the important configuration options

    self._conf_important_contents = filter(lambda entry: stem.manual.is_important(entry.option()), self._conf_contents)

    # if there aren't any important options then show everything

    if not self._conf_important_contents:
      self._conf_important_contents = self._conf_contents

    self._conf_contents = sorted(self._conf_contents, key = lambda entry: [entry.sort_value(field) for field in self._sort_order])
    self._conf_important_contents = sorted(self._conf_important_contents, key = lambda entry: [entry.sort_value(field) for field in self._sort_order])

  def get_selection(self):
    """
    Provides the currently selected entry.
    """

    return self._scroller.get_cursor_selection(self._get_config_options())

  def show_sort_dialog(self):
    """
    Provides the dialog for sorting our configuration options.
    """

    sort_colors = dict([(attr, CONFIG['attr.config.sort_color'].get(attr, 'white')) for attr in SortAttr])
    results = nyx.popups.show_sort_dialog('Config Option Ordering:', SortAttr, self._sort_order, sort_colors)

    if results:
      self._sort_order = results
      self._conf_contents = sorted(self._conf_contents, key = lambda entry: [entry.sort_value(field) for field in self._sort_order])
      self._conf_important_contents = sorted(self._conf_important_contents, key = lambda entry: [entry.sort_value(field) for field in self._sort_order])

  def handle_key(self, key):
    if key.is_scroll():
      page_height = self.get_preferred_size()[0] - DETAILS_HEIGHT - 2
      is_changed = self._scroller.handle_key(key, self._get_config_options(), page_height)

      if is_changed:
        self.redraw(True)
    elif key.is_selection() and self._get_config_options():
      # Prompts the user to edit the selected configuration value. The
      # interface is locked to prevent updates between setting the value
      # and showing any errors.

      with panel.CURSES_LOCK:
        selection = self.get_selection()
        config_option = selection.option()

        initial_value = '' if not selection.is_set() else selection.value()
        prompt_msg = '%s Value (esc to cancel): ' % config_option
        new_value = nyx.popups.input_prompt(prompt_msg, initial_value)

        if new_value is not None and new_value != initial_value:
          try:
            if selection.value_type() == 'Boolean':
              # if the value's a boolean then allow for 'true' and 'false' inputs

              if new_value.lower() == 'true':
                new_value = '1'
              elif new_value.lower() == 'false':
                new_value = '0'
            elif selection.value_type() == 'LineList':
              # set_option accepts list inputs when there's multiple values
              new_value = new_value.split(',')

            tor_controller().set_conf(config_option, new_value)

            # forces the label to be remade with the new value

            selection.label_cache = None

            self.redraw(True)
          except Exception as exc:
            nyx.popups.show_msg('%s (press any key)' % exc)
    elif key.match('a'):
      self._show_all = not self._show_all
      self.redraw(True)
    elif key.match('s'):
      self.show_sort_dialog()
    elif key.match('v'):
      self.show_write_dialog()
    else:
      return False

    return True

  def show_write_dialog(self):
    """
    Provies an interface to confirm if the configuration is saved and, if so,
    where.
    """

    # display a popup for saving the current configuration

    config_lines = tor_config.get_custom_options(True)
    popup, width, height = nyx.popups.init(len(config_lines) + 2)

    if not popup:
      return

    try:
      # displayed options (truncating the labels if there's limited room)

      if width >= 30:
        selection_options = ('Save', 'Save As...', 'Cancel')
      else:
        selection_options = ('Save', 'Save As', 'X')

      # checks if we can show options beside the last line of visible content

      is_option_line_separate = False
      last_index = min(height - 2, len(config_lines) - 1)

      # if we don't have room to display the selection options and room to
      # grow then display the selection options on its own line

      if width < (30 + len(config_lines[last_index])):
        popup.set_height(height + 1)
        popup.redraw(True)  # recreates the window instance
        new_height, _ = popup.get_preferred_size()

        if new_height > height:
          height = new_height
          is_option_line_separate = True

      selection = 2

      while True:
        # if the popup has been resized then recreate it (needed for the
        # proper border height)

        new_height, new_width = popup.get_preferred_size()

        if (height, width) != (new_height, new_width):
          height, width = new_height, new_width
          popup.redraw(True)

        # if there isn't room to display the popup then cancel it

        if height <= 2:
          selection = 2
          break

        popup.win.erase()
        popup.win.box()
        popup.addstr(0, 0, 'Configuration being saved:', curses.A_STANDOUT)

        visible_config_lines = height - 3 if is_option_line_separate else height - 2

        for i in range(visible_config_lines):
          line = str_tools.crop(config_lines[i], width - 2)

          if ' ' in line:
            option, arg = line.split(' ', 1)
            popup.addstr(i + 1, 1, option, curses.A_BOLD, 'green')
            popup.addstr(i + 1, len(option) + 2, arg, curses.A_BOLD, 'cyan')
          else:
            popup.addstr(i + 1, 1, line, curses.A_BOLD, 'green')

        # draws selection options (drawn right to left)

        draw_x = width - 1

        for i in range(len(selection_options) - 1, -1, -1):
          option_label = selection_options[i]
          draw_x -= (len(option_label) + 2)

          # if we've run out of room then drop the option (this will only
          # occure on tiny displays)

          if draw_x < 1:
            break

          selection_format = curses.A_STANDOUT if i == selection else curses.A_NORMAL
          x = popup.addstr(height - 2, draw_x, '[')
          x = popup.addstr(height - 2, x, option_label, selection_format, curses.A_BOLD)
          popup.addstr(height - 2, x, ']')

          draw_x -= 1  # space gap between the options

        popup.win.refresh()

        key = nyx.controller.get_controller().key_input()

        if key.match('left'):
          selection = max(0, selection - 1)
        elif key.match('right'):
          selection = min(len(selection_options) - 1, selection + 1)
        elif key.is_selection():
          break

      if selection in (0, 1):
        loaded_torrc, prompt_canceled = tor_config.get_torrc(), False

        try:
          config_location = loaded_torrc.get_config_location()
        except IOError:
          config_location = ''

        if selection == 1:
          # prompts user for a configuration location
          config_location = nyx.popups.input_prompt('Save to (esc to cancel): ', config_location)

          if not config_location:
            prompt_canceled = True

        if not prompt_canceled:
          try:
            tor_config.save_conf(config_location, config_lines)
            msg = 'Saved configuration to %s' % config_location
          except IOError as exc:
            msg = 'Unable to save configuration (%s)' % exc.strerror

          nyx.popups.show_msg(msg, 2)
    finally:
      nyx.popups.finalize()

  def get_help(self):
    return [
      ('up arrow', 'scroll up a line', None),
      ('down arrow', 'scroll down a line', None),
      ('page up', 'scroll up a page', None),
      ('page down', 'scroll down a page', None),
      ('enter', 'edit configuration option', None),
      ('v', 'save configuration', None),
      ('a', 'toggle option filtering', None),
      ('s', 'sort ordering', None),
    ]

  def draw(self, width, height):
    # Shrink detail panel if there isn't sufficient room for the whole
    # thing. The extra line is for the bottom border.

    scroll_location = self._scroller.get_scroll_location(self._get_config_options(), height - DETAILS_HEIGHT - 2)
    cursor_selection = self.get_selection()
    is_scrollbar_visible = len(self._get_config_options()) > height - DETAILS_HEIGHT - 2

    if cursor_selection is not None:
      self._draw_selection_panel(cursor_selection, width, DETAILS_HEIGHT + 1, is_scrollbar_visible)

    # draws the top label

    if self.is_title_visible():
      hidden_msg = "press 'a' to hide most options" if self._show_all else "press 'a' to show all options"
      title_label = 'Tor Configuration (%s):' % hidden_msg
      self.addstr(0, 0, title_label, curses.A_STANDOUT)

    # draws left-hand scroll bar if content's longer than the height

    scroll_offset = 1

    if is_scrollbar_visible:
      scroll_offset = 3
      self.add_scroll_bar(scroll_location, scroll_location + height - DETAILS_HEIGHT - 2, len(self._get_config_options()), DETAILS_HEIGHT + 2)

    value_width = VALUE_WIDTH
    description_width = max(0, width - scroll_offset - OPTION_WIDTH - value_width - 2)

    # if the description column is overly long then use its space for the
    # value instead

    if description_width > 80:
      value_width += description_width - 80
      description_width = 80

    for line_number in range(scroll_location, len(self._get_config_options())):
      entry = self._get_config_options()[line_number]
      draw_line = line_number + DETAILS_HEIGHT + 2 - scroll_location

      line_format = [curses.A_BOLD if entry.is_set() else curses.A_NORMAL]
      line_format += [CONFIG['attr.config.category_color'].get(entry.category(), 'white')]

      if entry == cursor_selection:
        line_format += [curses.A_STANDOUT]

      option_label = str_tools.crop(entry.option(), OPTION_WIDTH)
      value_label = str_tools.crop(entry.value(), value_width)
      summary_label = str_tools.crop(entry.summary(), description_width, None)
      line_text_layout = '%%-%is %%-%is %%-%is' % (OPTION_WIDTH, value_width, description_width)
      line_text = line_text_layout % (option_label, value_label, summary_label)

      self.addstr(draw_line, scroll_offset, line_text, *line_format)

      if draw_line >= height:
        break

  def _get_config_options(self):
    return self._conf_contents if self._show_all else self._conf_important_contents

  def _draw_selection_panel(self, selection, width, detail_panel_height, is_scrollbar_visible):
    """
    Renders a panel for the selected configuration option.
    """

    # This is a solid border unless the scrollbar is visible, in which case a
    # 'T' pipe connects the border to the bar.

    ui_tools.draw_box(self, 0, 0, width, detail_panel_height + 1)

    if is_scrollbar_visible:
      self.addch(detail_panel_height, 1, curses.ACS_TTEE)

    selection_format = (curses.A_BOLD, CONFIG['attr.config.category_color'].get(selection.category(), 'white'))

    # first entry:
    # <option> (<category> Option)

    option_label = ' (%s Option)' % selection.category()
    self.addstr(1, 2, selection.option() + option_label, *selection_format)

    # second entry:
    # Value: <value> ([default|custom], <type>, usage: <argument usage>)

    if detail_panel_height >= 3:
      value_attr_label = ', '.join([
        'custom' if selection.is_set() else 'default',
        selection.value_type(),
        'usage: %s' % (selection.manual_entry().usage if selection.manual_entry() else '')
      ])

      value_label_width = width - 12 - len(value_attr_label)
      value_label = str_tools.crop(selection.value(), value_label_width)

      self.addstr(2, 2, 'Value: %s (%s)' % (value_label, value_attr_label), *selection_format)

    # remainder is filled with the man page description

    description_height = max(0, detail_panel_height - 3)
    description_content = 'Description: %s' % (selection.manual_entry().description if selection.manual_entry() else '')

    for i in range(description_height):
      if not description_content:
        break  # done writing the description

      # there's a leading indent after the first line

      if i > 0:
        description_content = '  ' + description_content

      # we only want to work with content up until the next newline

      if '\n' in description_content:
        line_content, description_content = description_content.split('\n', 1)
      else:
        line_content, description_content = description_content, ''

      if i != description_height - 1:
        # there's more lines to display

        msg, remainder = str_tools.crop(line_content, width - 3, 4, 4, str_tools.Ending.HYPHEN, True)
        description_content = remainder.strip() + description_content
      else:
        # this is the last line, end it with an ellipse

        msg = str_tools.crop(line_content, width - 3, 4, 4)

      self.addstr(3 + i, 2, msg, *selection_format)
