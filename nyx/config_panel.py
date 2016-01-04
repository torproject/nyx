"""
Panel presenting the configuration state for tor or nyx. Options can be edited
and the resulting configuration files saved.
"""

import curses

import nyx.controller
import nyx.popups

import stem.control
import stem.manual

from nyx.util import panel, tor_controller, ui_tools

from stem.util import conf, enum, log, str_tools

SortAttr = enum.Enum('NAME', 'VALUE', 'VALUE_TYPE', 'CATEGORY', 'USAGE', 'SUMMARY', 'DESCRIPTION', 'MAN_PAGE_ENTRY', 'IS_SET')

DETAILS_HEIGHT = 8
NAME_WIDTH = 25
VALUE_WIDTH = 15


def conf_handler(key, value):
  if key == 'features.config.order':
    return conf.parse_enum_csv(key, value[0], SortAttr, 3)


CONFIG = conf.config_dict('nyx', {
  'attr.config.category_color': {},
  'attr.config.sort_color': {},
  'features.config.order': [SortAttr.MAN_PAGE_ENTRY, SortAttr.NAME, SortAttr.IS_SET],
  'features.config.state.showPrivateOptions': False,
  'features.config.state.showVirtualOptions': False,
}, conf_handler)


class ConfigEntry(object):
  """
  Configuration option presented in the panel.

  :var str name: name of the configuration option
  :var str value_type: type of value
  :var stem.manual.ConfigOption manual: manual information about the option
  """

  def __init__(self, name, value_type, manual):
    self.name = name
    self.value_type = value_type
    self.manual = manual.config_options.get(name, stem.manual.ConfigOption(name))
    self._index = manual.config_options.keys().index(name) if name in manual.config_options else 99999

  def value(self):
    """
    Provides the value of this configuration option.

    :returns: **str** representation of the current config value
    """

    values = tor_controller().get_conf(self.name, [], True)

    if not values:
      return '<none>'
    elif self.value_type == 'Boolean' and values[0] in ('0', '1'):
      return 'False' if values[0] == '0' else 'True'
    elif self.value_type == 'DataSize' and values[0].isdigit():
      return str_tools.size_label(int(values[0]))
    elif self.value_type == 'TimeInterval' and values[0].isdigit():
      return str_tools.time_label(int(values[0]), is_long = True)
    else:
      return ', '.join(values)

  def is_set(self):
    """
    Checks if the configuration option has a custom value.

    :returns: **True** if the option has a custom value, **False** otherwise
    """

    return tor_controller().is_set(self.name, False)

  def sort_value(self, attr):
    """
    Provides a heuristic for sorting by a given value.

    :param SortAttr attr: sort attribute to provide a heuristic for

    :returns: comparable value for sorting
    """

    if attr == SortAttr.CATEGORY:
      return self.manual.category
    elif attr == SortAttr.NAME:
      return self.name
    elif attr == SortAttr.VALUE:
      return self.value()
    elif attr == SortAttr.VALUE_TYPE:
      return self.value_type
    elif attr == SortAttr.USAGE:
      return self.manual.usage
    elif attr == SortAttr.SUMMARY:
      return self.manual.summary
    elif attr == SortAttr.DESCRIPTION:
      return self.manual.description
    elif attr == SortAttr.MAN_PAGE_ENTRY:
      return self._index
    elif attr == SortAttr.IS_SET:
      return not self.is_set()


class ConfigPanel(panel.Panel):
  """
  Editor for tor's configuration.
  """

  def __init__(self, stdscr):
    panel.Panel.__init__(self, stdscr, 'configuration', 0)

    self._contents = []
    self._scroller = ui_tools.Scroller(True)
    self._sort_order = CONFIG['features.config.order']
    self._show_all = False  # show all options, or just the important ones

    try:
      manual = stem.manual.Manual.from_man()
    except IOError as exc:
      log.debug("Unable to use 'man tor' to get information about config options (%s), using bundled information instead" % exc)
      manual = stem.manual.Manual.from_cache()

    try:
      for line in tor_controller().get_info('config/names').splitlines():
        # Lines of the form "<option> <type>[ <documentation>]". Documentation
        # was apparently only in old tor versions like 0.2.1.25.

        if ' ' not in line:
          continue

        line_comp = line.split()
        name, value_type = line_comp[0], line_comp[1]

        # skips private and virtual entries if not configured to show them

        if name.startswith('__') and not CONFIG['features.config.state.showPrivateOptions']:
          continue
        elif value_type == 'Virtual' and not CONFIG['features.config.state.showVirtualOptions']:
          continue

        self._contents.append(ConfigEntry(name, value_type, manual))

      self._contents = sorted(self._contents, key = lambda entry: [entry.sort_value(field) for field in self._sort_order])
    except stem.ControllerError as exc:
      log.warn('Unable to determine the configuration options tor supports: %s' % exc)

  def show_sort_dialog(self):
    """
    Provides the dialog for sorting our configuration options.
    """

    sort_colors = dict([(attr, CONFIG['attr.config.sort_color'].get(attr, 'white')) for attr in SortAttr])
    results = nyx.popups.show_sort_dialog('Config Option Ordering:', SortAttr, self._sort_order, sort_colors)

    if results:
      self._sort_order = results
      self._contents = sorted(self._contents, key = lambda entry: [entry.sort_value(field) for field in self._sort_order])

  def show_write_dialog(self):
    """
    Confirmation dialog for saving tor's configuration.
    """

    selection, controller = 1, tor_controller()
    config_text = controller.get_info('config-text', None)
    config_lines = config_text.splitlines() if config_text else []

    with nyx.popups.popup_window(len(config_lines) + 2) as (popup, width, height):
      if not popup or height <= 2:
        return

      while True:
        height, width = popup.get_preferred_size()  # allow us to be resized
        popup.win.erase()

        for i, full_line in enumerate(config_lines):
          line = str_tools.crop(full_line, width - 2)
          option, arg = line.split(' ', 1) if ' ' in line else (line, '')

          popup.addstr(i + 1, 1, option, curses.A_BOLD, 'green')
          popup.addstr(i + 1, len(option) + 2, arg, curses.A_BOLD, 'cyan')

        x = width - 16

        for i, option in enumerate(['Save', 'Cancel']):
          x = popup.addstr(height - 2, x, '[')
          x = popup.addstr(height - 2, x, option, curses.A_BOLD, curses.A_STANDOUT if i == selection else curses.A_NORMAL)
          x = popup.addstr(height - 2, x, '] ')

        popup.win.box()
        popup.addstr(0, 0, 'Torrc to save:', curses.A_STANDOUT)
        popup.win.refresh()

        key = nyx.controller.get_controller().key_input()

        if key.match('left'):
          selection = max(0, selection - 1)
        elif key.match('right'):
          selection = min(1, selection + 1)
        elif key.is_selection():
          if selection == 0:
            try:
              controller.save_conf()
              nyx.popups.show_msg('Saved configuration to %s' % controller.get_info('config-file', '<unknown>'), 2)
            except IOError as exc:
              nyx.popups.show_msg('Unable to save configuration (%s)' % exc.strerror, 2)

          break
        elif key.match('esc'):
          break  # esc - cancel

  def handle_key(self, key):
    if key.is_scroll():
      page_height = self.get_preferred_size()[0] - DETAILS_HEIGHT
      is_changed = self._scroller.handle_key(key, self._get_config_options(), page_height)

      if is_changed:
        self.redraw(True)
    elif key.is_selection():
      selection = self._scroller.get_cursor_selection(self._get_config_options())
      initial_value = selection.value() if selection.is_set() else ''
      new_value = nyx.popups.input_prompt('%s Value (esc to cancel): ' % selection.name, initial_value)

      if new_value != initial_value:
        try:
          if selection.value_type == 'Boolean':
            # if the value's a boolean then allow for 'true' and 'false' inputs

            if new_value.lower() == 'true':
              new_value = '1'
            elif new_value.lower() == 'false':
              new_value = '0'
          elif selection.value_type == 'LineList':
            new_value = new_value.split(',')  # set_conf accepts list inputs

          tor_controller().set_conf(selection.name, new_value)
          self.redraw(True)
        except Exception as exc:
          nyx.popups.show_msg('%s (press any key)' % exc)
    elif key.match('a'):
      self._show_all = not self._show_all
      self.redraw(True)
    elif key.match('s'):
      self.show_sort_dialog()
    elif key.match('w'):
      self.show_write_dialog()
    else:
      return False

    return True

  def get_help(self):
    return [
      ('up arrow', 'scroll up a line', None),
      ('down arrow', 'scroll down a line', None),
      ('page up', 'scroll up a page', None),
      ('page down', 'scroll down a page', None),
      ('enter', 'edit configuration option', None),
      ('w', 'write torrc', None),
      ('a', 'toggle filtering', None),
      ('s', 'sort ordering', None),
    ]

  def draw(self, width, height):
    contents = self._get_config_options()
    selection = self._scroller.get_cursor_selection(contents)
    scroll_location = self._scroller.get_scroll_location(contents, height - DETAILS_HEIGHT)
    is_scrollbar_visible = len(contents) > height - DETAILS_HEIGHT

    if selection is not None:
      self._draw_selection_panel(selection, width, is_scrollbar_visible)

    if self.is_title_visible():
      hidden_msg = "press 'a' to hide most options" if self._show_all else "press 'a' to show all options"
      title_label = 'Tor Configuration (%s):' % hidden_msg
      self.addstr(0, 0, title_label, curses.A_STANDOUT)

    # draws left-hand scroll bar if content's longer than the height

    scroll_offset = 1

    if is_scrollbar_visible:
      scroll_offset = 3
      self.add_scroll_bar(scroll_location, scroll_location + height - DETAILS_HEIGHT, len(contents), DETAILS_HEIGHT)

    value_width = VALUE_WIDTH
    description_width = max(0, width - scroll_offset - NAME_WIDTH - value_width - 2)

    # if the description column is overly long then use its space for the
    # value instead

    if description_width > 80:
      value_width += description_width - 80
      description_width = 80

    for line_number in range(scroll_location, len(contents)):
      entry = contents[line_number]
      draw_line = line_number + DETAILS_HEIGHT - scroll_location

      line_format = [curses.A_BOLD if entry.is_set() else curses.A_NORMAL]
      line_format += [CONFIG['attr.config.category_color'].get(entry.manual.category, 'white')]

      if entry == selection:
        line_format += [curses.A_STANDOUT]

      option_label = str_tools.crop(entry.name, NAME_WIDTH)
      value_label = str_tools.crop(entry.value(), value_width)
      summary_label = str_tools.crop(entry.manual.summary, description_width, None)
      line_text_layout = '%%-%is %%-%is %%-%is' % (NAME_WIDTH, value_width, description_width)
      line_text = line_text_layout % (option_label, value_label, summary_label)

      self.addstr(draw_line, scroll_offset, line_text, *line_format)

      if draw_line >= height:
        break

  def _get_config_options(self):
    return self._contents if self._show_all else filter(lambda entry: stem.manual.is_important(entry.name) or entry.is_set(), self._contents)

  def _draw_selection_panel(self, selection, width, is_scrollbar_visible):
    """
    Renders a panel for the selected configuration option.
    """

    # This is a solid border unless the scrollbar is visible, in which case a
    # 'T' pipe connects the border to the bar.

    ui_tools.draw_box(self, 0, 0, width, DETAILS_HEIGHT)

    if is_scrollbar_visible:
      self.addch(DETAILS_HEIGHT - 1, 1, curses.ACS_TTEE)

    selection_format = (curses.A_BOLD, CONFIG['attr.config.category_color'].get(selection.manual.category, 'white'))

    # first entry:
    # <option> (<category> Option)

    option_label = ' (%s Option)' % selection.manual.category
    self.addstr(1, 2, selection.name + option_label, *selection_format)

    # second entry:
    # Value: <value> ([default|custom], <type>, usage: <argument usage>)

    if DETAILS_HEIGHT >= 4:
      value_attr_label = ', '.join([
        'custom' if selection.is_set() else 'default',
        selection.value_type,
        'usage: %s' % (selection.manual.usage)
      ])

      value_label_width = max(0, width - 12 - len(value_attr_label))
      value_label = str_tools.crop(selection.value(), value_label_width)

      self.addstr(2, 2, 'Value: %s (%s)' % (value_label, value_attr_label), *selection_format)

    # remainder is filled with the man page description

    description_height = max(0, DETAILS_HEIGHT - 4)
    description_content = 'Description: %s' % (selection.manual.description)

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
