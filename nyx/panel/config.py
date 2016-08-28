# Copyright 2010-2016, Damian Johnson and The Tor Project
# See LICENSE for licensing information

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
import stem.util.connection

from nyx.curses import WHITE, NORMAL, BOLD, HIGHLIGHT
from nyx.menu import MenuItem, Submenu
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
    nyx.panel.Panel.__init__(self)

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

  def _show_sort_dialog(self):
    """
    Provides the dialog for sorting our configuration options.
    """

    sort_colors = dict([(attr, CONFIG['attr.config.sort_color'].get(attr, WHITE)) for attr in SortAttr])
    results = nyx.popups.select_sort_order('Config Option Ordering:', SortAttr, self._sort_order, sort_colors)

    if results:
      self._sort_order = results
      self._contents = sorted(self._contents, key = lambda entry: [entry.sort_value(field) for field in self._sort_order])

  def _show_write_dialog(self):
    """
    Confirmation dialog for saving tor's configuration.
    """

    controller = tor_controller()
    torrc = controller.get_info('config-text', None)

    if nyx.popups.confirm_save_torrc(torrc):
      try:
        controller.save_conf()
        nyx.controller.show_message('Saved configuration to %s' % controller.get_info('config-file', '<unknown>'), HIGHLIGHT, max_wait = 2)
      except IOError as exc:
        nyx.controller.show_message('Unable to save configuration (%s)' % exc.strerror, HIGHLIGHT, max_wait = 2)

    self.redraw()

  def key_handlers(self):
    def _scroll(key):
      page_height = self.get_height() - DETAILS_HEIGHT
      is_changed = self._scroller.handle_key(key, self._get_config_options(), page_height)

      if is_changed:
        self.redraw()

    def _edit_selected_value():
      selected = self._scroller.selection(self._get_config_options())
      initial_value = selected.value() if selected.is_set() else ''
      new_value = nyx.controller.input_prompt('%s Value (esc to cancel): ' % selected.name, initial_value)

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
          self.redraw()
        except Exception as exc:
          nyx.controller.show_message('%s (press any key)' % exc, HIGHLIGHT, max_wait = 30)

    def _toggle_show_all():
      self._show_all = not self._show_all
      self.redraw()

    return (
      nyx.panel.KeyHandler('arrows', 'scroll up and down', _scroll, key_func = lambda key: key.is_scroll()),
      nyx.panel.KeyHandler('enter', 'edit configuration option', _edit_selected_value, key_func = lambda key: key.is_selection()),
      nyx.panel.KeyHandler('w', 'write torrc', self._show_write_dialog),
      nyx.panel.KeyHandler('a', 'toggle filtering', _toggle_show_all),
      nyx.panel.KeyHandler('s', 'sort ordering', self._show_sort_dialog),
    )

  def submenu(self):
    """
    Submenu consisting of...

      Save Config...
      Sorting...
      Filter / Unfilter Options
    """

    return Submenu('Configuration', [
      MenuItem('Save Config...', self._show_write_dialog),
      MenuItem('Sorting...', self._show_sort_dialog),
    ])

  def _draw(self, subwindow):
    contents = self._get_config_options()
    selected, scroll = self._scroller.selection(contents, subwindow.height - DETAILS_HEIGHT)
    is_scrollbar_visible = len(contents) > subwindow.height - DETAILS_HEIGHT

    if selected is not None:
      _draw_selection_details(subwindow, selected)

    hidden_msg = "press 'a' to hide most options" if self._show_all else "press 'a' to show all options"
    subwindow.addstr(0, 0, 'Tor Configuration (%s):' % hidden_msg, HIGHLIGHT)

    scroll_offset = 1

    if is_scrollbar_visible:
      scroll_offset = 3
      subwindow.scrollbar(DETAILS_HEIGHT, scroll, len(contents) - 1)

      if selected is not None:
        subwindow._addch(1, DETAILS_HEIGHT - 1, curses.ACS_TTEE)

    # Description column can grow up to eighty characters. After that any extra
    # space goes to the value.

    description_width = max(0, subwindow.width - scroll_offset - NAME_WIDTH - VALUE_WIDTH - 2)

    if description_width > 80:
      value_width = VALUE_WIDTH + (description_width - 80)
      description_width = 80
    else:
      value_width = VALUE_WIDTH

    for i, entry in enumerate(contents[scroll:]):
      _draw_line(subwindow, scroll_offset, DETAILS_HEIGHT + i, entry, entry == selected, value_width, description_width)

      if DETAILS_HEIGHT + i >= subwindow.height:
        break

  def _get_config_options(self):
    return self._contents if self._show_all else list(filter(lambda entry: stem.manual.is_important(entry.name) or entry.is_set(), self._contents))


def _draw_line(subwindow, x, y, entry, is_selected, value_width, description_width):
  """
  Show an individual configuration line.
  """

  attr = [CONFIG['attr.config.category_color'].get(entry.manual.category, WHITE)]
  attr.append(BOLD if entry.is_set() else NORMAL)
  attr.append(HIGHLIGHT if is_selected else NORMAL)

  option_label = str_tools.crop(entry.name, NAME_WIDTH).ljust(NAME_WIDTH + 1)
  value_label = str_tools.crop(entry.value(), value_width).ljust(value_width + 1)
  summary_label = str_tools.crop(entry.manual.summary, description_width).ljust(description_width)

  subwindow.addstr(x, y, option_label + value_label + summary_label, *attr)


def _draw_selection_details(subwindow, selected):
  """
  Shows details of the currently selected option.
  """

  attr = ', '.join(('custom' if selected.is_set() else 'default', selected.value_type, 'usage: %s' % selected.manual.usage))
  selected_color = CONFIG['attr.config.category_color'].get(selected.manual.category, WHITE)
  subwindow.box(0, 0, subwindow.width, DETAILS_HEIGHT)

  subwindow.addstr(2, 1, '%s (%s Option)' % (selected.name, selected.manual.category), selected_color, BOLD)
  subwindow.addstr(2, 2, 'Value: %s (%s)' % (selected.value(), str_tools.crop(attr, subwindow.width - len(selected.value()) - 13)), selected_color, BOLD)

  description = 'Description: %s' % selected.manual.description

  for i in range(DETAILS_HEIGHT - 4):
    if not description:
      break  # done writing description

    line, description = description.split('\n', 1) if '\n' in description else (description, '')

    if i < DETAILS_HEIGHT - 5:
      line, remainder = str_tools.crop(line, subwindow.width - 3, 4, 4, str_tools.Ending.HYPHEN, True)
      description = '  ' + remainder.strip() + description
      subwindow.addstr(2, 3 + i, line, selected_color, BOLD)
    else:
      subwindow.addstr(2, 3 + i, str_tools.crop(line, subwindow.width - 3, 4, 4), selected_color, BOLD)
