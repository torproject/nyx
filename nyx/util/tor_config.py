"""
Helper functions for working with tor's configuration file.
"""

from nyx.util import tor_controller

from stem.util import conf, enum, str_tools

CONFIG = conf.config_dict('nyx', {
  'torrc.alias': {},
  'torrc.units.size.b': [],
  'torrc.units.size.kb': [],
  'torrc.units.size.mb': [],
  'torrc.units.size.gb': [],
  'torrc.units.size.tb': [],
  'torrc.units.time.sec': [],
  'torrc.units.time.min': [],
  'torrc.units.time.hour': [],
  'torrc.units.time.day': [],
  'torrc.units.time.week': [],
  'tor.chroot': '',
})


def general_conf_handler(config, key):
  value = config.get(key)

  if key.startswith('torrc.units.') and value:
    # all the torrc.units.* values are comma separated lists
    return [entry.strip() for entry in value[0].split(',')]


conf.get_config('nyx').add_listener(general_conf_handler, backfill = True)

# enums and values for numeric torrc entries

ValueType = enum.Enum('UNRECOGNIZED', 'SIZE', 'TIME')
SIZE_MULT = {'b': 1, 'kb': 1024, 'mb': 1048576, 'gb': 1073741824, 'tb': 1099511627776}
TIME_MULT = {'sec': 1, 'min': 60, 'hour': 3600, 'day': 86400, 'week': 604800}

# enums for issues found during torrc validation:
# DUPLICATE  - entry is ignored due to being a duplicate
# MISMATCH   - the value doesn't match tor's current state
# IS_DEFAULT - the configuration option matches tor's default

ValidationError = enum.Enum('DUPLICATE', 'MISMATCH', 'IS_DEFAULT')

MULTILINE_PARAM = None  # cached multiline parameters (lazily loaded)


def get_multiline_parameters():
  """
  Provides parameters that can be defined multiple times in the torrc without
  overwriting the value.
  """

  # fetches config options with the LINELIST (aka 'LineList'), LINELIST_S (aka
  # 'Dependent'), and LINELIST_V (aka 'Virtual') types

  global MULTILINE_PARAM

  if MULTILINE_PARAM is None:
    controller, multiline_entries = tor_controller(), []

    config_option_query = controller.get_info('config/names', None)

    if config_option_query:
      for line in config_option_query.strip().split('\n'):
        conf_option, conf_type = line.strip().split(' ', 1)

        if conf_type in ('LineList', 'Dependant', 'Virtual'):
          multiline_entries.append(conf_option)
    else:
      # unable to query tor connection, so not caching results
      return ()

    MULTILINE_PARAM = multiline_entries

  return tuple(MULTILINE_PARAM)


def validate(contents = None):
  """
  Performs validation on the given torrc contents, providing back a listing of
  (line number, issue, msg) tuples for issues found. If the issue occures on a
  multiline torrc entry then the line number is for the last line of the entry.

  Arguments:
    contents - torrc contents
  """

  controller = tor_controller()

  config_text = tor_controller().get_info('config-text', None)
  config_lines = config_text.splitlines() if config_text else []
  custom_options = list(set([line.split(' ')[0] for line in config_lines]))

  issues_found, seen_options = [], []

  # Strips comments and collapses multiline multi-line entries, for more
  # information see:
  # https://trac.torproject.org/projects/tor/ticket/1929

  stripped_contents, multiline_buffer = [], ''

  for line in _strip_comments(contents):
    if not line:
      stripped_contents.append('')
    else:
      line = multiline_buffer + line
      multiline_buffer = ''

      if line.endswith('\\'):
        multiline_buffer = line[:-1]
        stripped_contents.append('')
      else:
        stripped_contents.append(line.strip())

  for line_number in range(len(stripped_contents) - 1, -1, -1):
    line_text = stripped_contents[line_number]

    if not line_text:
      continue

    line_comp = line_text.split(None, 1)

    if len(line_comp) == 2:
      option, value = line_comp
    else:
      option, value = line_text, ''

    # Tor is case insensetive when parsing its torrc. This poses a bit of an
    # issue for us because we want all of our checks to be case insensetive
    # too but also want messages to match the normal camel-case conventions.
    #
    # Using the custom_options to account for this. It contains the tor reported
    # options (camel case) and is either a matching set or the following defaut
    # value check will fail. Hence using that hash to correct the case.
    #
    # TODO: when refactoring for stem make this less confusing...

    for custom_opt in custom_options:
      if custom_opt.lower() == option.lower():
        option = custom_opt
        break

    # if an aliased option then use its real name

    if option in CONFIG['torrc.alias']:
      option = CONFIG['torrc.alias'][option]

    # most parameters are overwritten if defined multiple times

    if option in seen_options and option not in get_multiline_parameters():
      issues_found.append((line_number, ValidationError.DUPLICATE, option))
      continue
    else:
      seen_options.append(option)

    # checks if the value isn't necessary due to matching the defaults

    if option not in custom_options:
      issues_found.append((line_number, ValidationError.IS_DEFAULT, option))

    # replace aliases with their recognized representation

    if option in CONFIG['torrc.alias']:
      option = CONFIG['torrc.alias'][option]

    # tor appears to replace tabs with a space, for instance:
    # "accept\t*:563" is read back as "accept *:563"

    value = value.replace('\t', ' ')

    # parse value if it's a size or time, expanding the units

    value, value_type = _parse_conf_value(value)

    # issues GETCONF to get the values tor's currently configured to use

    tor_values = controller.get_conf(option, [], True)

    # multiline entries can be comma separated values (for both tor and conf)

    value_list = [value]

    if option in get_multiline_parameters():
      value_list = [val.strip() for val in value.split(',')]

      fetched_values, tor_values = tor_values, []
      for fetched_value in fetched_values:
        for fetched_entry in fetched_value.split(','):
          fetched_entry = fetched_entry.strip()

          if fetched_entry not in tor_values:
            tor_values.append(fetched_entry)

    for val in value_list:
      # checks if both the argument and tor's value are empty

      is_blank_match = not val and not tor_values

      if not is_blank_match and val not in tor_values:
        # converts corrections to reader friedly size values

        display_values = tor_values

        if value_type == ValueType.SIZE:
          display_values = [str_tools.size_label(int(val)) for val in tor_values]
        elif value_type == ValueType.TIME:
          display_values = [str_tools.time_label(int(val)) for val in tor_values]

        issues_found.append((line_number, ValidationError.MISMATCH, ', '.join(display_values)))

  return issues_found


def _parse_conf_value(conf_arg):
  """
  Converts size or time values to their lowest units (bytes or seconds) which
  is what GETCONF calls provide. The returned is a tuple of the value and unit
  type.

  Arguments:
    conf_arg - torrc argument
  """

  if conf_arg.count(' ') == 1:
    val, unit = conf_arg.lower().split(' ', 1)

    if not val.isdigit():
      return conf_arg, ValueType.UNRECOGNIZED

    mult, mult_type = _get_unit_type(unit)

    if mult is not None:
      return str(int(val) * mult), mult_type

  return conf_arg, ValueType.UNRECOGNIZED


def _get_unit_type(unit):
  """
  Provides the type and multiplier for an argument's unit. The multiplier is
  None if the unit isn't recognized.

  Arguments:
    unit - string representation of a unit
  """

  for label in SIZE_MULT:
    if unit in CONFIG['torrc.units.size.' + label]:
      return SIZE_MULT[label], ValueType.SIZE

  for label in TIME_MULT:
    if unit in CONFIG['torrc.units.time.' + label]:
      return TIME_MULT[label], ValueType.TIME

  return None, ValueType.UNRECOGNIZED


def _strip_comments(contents):
  """
  Removes comments and extra whitespace from the given torrc contents.

  Arguments:
    contents - torrc contents
  """

  stripped_contents = []

  for line in contents:
    if line and '#' in line:
      line = line[:line.find('#')]

    stripped_contents.append(line.strip())

  return stripped_contents
