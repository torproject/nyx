"""
Panel presenting the configuration state for tor or nyx. Options can be edited
and the resulting configuration files saved.
"""

import curses
import os

import nyx.controller
import nyx.curses
import nyx.panel
import nyx.popups

import stem.control
import stem.manual

from nyx.curses import GREEN, CYAN, WHITE, NORMAL, BOLD, HIGHLIGHT
from nyx import DATA_DIR, tor_controller

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


class ConfigPanel(nyx.panel.Panel):
  """
  Editor for tor's configuration.
  """

  def __init__(self):
    nyx.panel.Panel.__init__(self, 'configuration')

    self._contents = []
    self._scroller = nyx.curses.CursorScroller()
    self._sort_order = CONFIG['features.config.order']
    self._show_all = False  # show all options, or just the important ones

    cached_manual_path = os.path.join(DATA_DIR, 'manual')

    if os.path.exists(cached_manual_path):
      manual = stem.manual.Manual.from_cache(cached_manual_path)
    else:
      try:
        manual = stem.manual.Manual.from_man()

        try:
          manual.save(cached_manual_path)
        except IOError as exc:
          log.debug("Unable to cache manual information to '%s'. This is fine, but means starting Nyx takes a little longer than usual: " % (cached_manual_path, exc))
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

    sort_colors = dict([(attr, CONFIG['attr.config.sort_color'].get(attr, WHITE)) for attr in SortAttr])
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

          popup.addstr(i + 1, 1, option, GREEN, BOLD)
          popup.addstr(i + 1, len(option) + 2, arg, CYAN, BOLD)

        x = width - 16

        for i, option in enumerate(['Save', 'Cancel']):
          x = popup.addstr(height - 2, x, '[')
          x = popup.addstr(height - 2, x, option, BOLD, HIGHLIGHT if i == selection else NORMAL)
          x = popup.addstr(height - 2, x, '] ')

        popup.win.box()
        popup.addstr(0, 0, 'Torrc to save:', HIGHLIGHT)
        popup.win.refresh()

        key = nyx.curses.key_input()

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
      selected = self._scroller.selection(self._get_config_options())
      initial_value = selected.value() if selected.is_set() else ''
      new_value = nyx.popups.input_prompt('%s Value (esc to cancel): ' % selected.name, initial_value)

      if new_value != initial_value:
        try:
          if selected.value_type == 'Boolean':
            # if the value's a boolean then allow for 'true' and 'false' inputs

            if new_value.lower() == 'true':
              new_value = '1'
            elif new_value.lower() == 'false':
              new_value = '0'
          elif selected.value_type == 'LineList':
            new_value = new_value.split(',')  # set_conf accepts list inputs

          tor_controller().set_conf(selected.name, new_value)
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
    selected, scroll = self._scroller.selection(contents, height - DETAILS_HEIGHT)
    is_scrollbar_visible = len(contents) > height - DETAILS_HEIGHT

    if selected is not None:
      self._draw_selection_details(selected, width)

    hidden_msg = "press 'a' to hide most options" if self._show_all else "press 'a' to show all options"
    self.addstr(0, 0, 'Tor Configuration (%s):' % hidden_msg, HIGHLIGHT)

    scroll_offset = 1

    if is_scrollbar_visible:
      scroll_offset = 3
      self.add_scroll_bar(scroll, scroll + height - DETAILS_HEIGHT, len(contents), DETAILS_HEIGHT)

      if selected is not None:
        self.addch(DETAILS_HEIGHT - 1, 1, curses.ACS_TTEE)

    # Description column can grow up to eighty characters. After that any extra
    # space goes to the value.

    description_width = max(0, width - scroll_offset - NAME_WIDTH - VALUE_WIDTH - 2)

    if description_width > 80:
      value_width = VALUE_WIDTH + (description_width - 80)
      description_width = 80
    else:
      value_width = VALUE_WIDTH

    for i, entry in enumerate(contents[scroll:]):
      attr = [CONFIG['attr.config.category_color'].get(entry.manual.category, WHITE)]
      attr.append(BOLD if entry.is_set() else NORMAL)
      attr.append(HIGHLIGHT if entry == selected else NORMAL)

      option_label = str_tools.crop(entry.name, NAME_WIDTH).ljust(NAME_WIDTH + 1)
      value_label = str_tools.crop(entry.value(), value_width).ljust(value_width + 1)
      summary_label = str_tools.crop(entry.manual.summary, description_width).ljust(description_width)

      self.addstr(DETAILS_HEIGHT + i, scroll_offset, option_label + value_label + summary_label, *attr)

      if DETAILS_HEIGHT + i >= height:
        break

  def _get_config_options(self):
    return self._contents if self._show_all else filter(lambda entry: stem.manual.is_important(entry.name) or entry.is_set(), self._contents)

  def _draw_selection_details(self, selected, width):
    """
    Shows details of the currently selected option.
    """

    description = 'Description: %s' % (selected.manual.description)
    attr = ', '.join(('custom' if selected.is_set() else 'default', selected.value_type, 'usage: %s' % selected.manual.usage))
    selected_color = CONFIG['attr.config.category_color'].get(selected.manual.category, WHITE)
    self.draw_box(0, 0, width, DETAILS_HEIGHT)

    self.addstr(1, 2, '%s (%s Option)' % (selected.name, selected.manual.category), selected_color, BOLD)
    self.addstr(2, 2, 'Value: %s (%s)' % (selected.value(), str_tools.crop(attr, width - len(selected.value()) - 13)), selected_color, BOLD)

    for i in range(DETAILS_HEIGHT - 4):
      if not description:
        break  # done writing description

      line, description = description.split('\n', 1) if '\n' in description else (description, '')

      if i < DETAILS_HEIGHT - 5:
        line, remainder = str_tools.crop(line, width - 3, 4, 4, str_tools.Ending.HYPHEN, True)
        description = '  ' + remainder.strip() + description
        self.addstr(3 + i, 2, line, selected_color, BOLD)
      else:
        self.addstr(3 + i, 2, str_tools.crop(line, width - 3, 4, 4), selected_color, BOLD)
