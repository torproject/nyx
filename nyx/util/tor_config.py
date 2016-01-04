"""
Helper functions for working with tor's configuration file.
"""

import threading

import stem.version

from nyx.util import tor_controller, ui_tools

from stem.util import conf, enum, log, str_tools, system

CONFIG = conf.config_dict('nyx', {
  'features.torrc.validate': True,
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
  'startup.data_directory': '~/.nyx',
  'features.config.descriptions.enabled': True,
  'features.config.descriptions.persist': True,
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
# MISSING    - value differs from its default but is missing from the torrc
# IS_DEFAULT - the configuration option matches tor's default

ValidationError = enum.Enum('DUPLICATE', 'MISMATCH', 'MISSING', 'IS_DEFAULT')

TORRC = None  # singleton torrc instance
MULTILINE_PARAM = None  # cached multiline parameters (lazily loaded)


def get_torrc():
  """
  Singleton constructor for a Controller. Be aware that this starts as being
  unloaded, needing the torrc contents to be loaded before being functional.
  """

  global TORRC

  if TORRC is None:
    TORRC = Torrc()

  return TORRC


def get_config_location():
  """
  Provides the location of the torrc, raising an IOError with the reason if the
  path can't be determined.
  """

  controller = tor_controller()
  config_location = controller.get_info('config-file', None)
  tor_pid, tor_prefix = controller.controller.get_pid(None), CONFIG['tor.chroot']

  if not config_location:
    raise IOError('unable to query the torrc location')

  try:
    tor_cwd = system.cwd(tor_pid)
    return tor_prefix + system.expand_path(config_location, tor_cwd)
  except IOError as exc:
    raise IOError("querying tor's pwd failed because %s" % exc)


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

  # checks if any custom options are missing from the torrc

  for option in custom_options:
    # In new versions the 'DirReqStatistics' option is true by default and
    # disabled on startup if geoip lookups are unavailable. If this option is
    # missing then that's most likely the reason.
    #
    # https://trac.torproject.org/projects/tor/ticket/4237

    if option == 'DirReqStatistics':
      continue

    if option not in seen_options:
      issues_found.append((None, ValidationError.MISSING, option))

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


class Torrc():
  """
  Wrapper for the torrc. All getters provide None if the contents are unloaded.
  """

  def __init__(self):
    self.contents = None
    self.config_location = None
    self._vals_lock = threading.RLock()

    # cached results for the current contents
    self.displayable_contents = None
    self.stripped_contents = None
    self.corrections = None

    # flag to indicate if we've given a load failure warning before
    self.is_foad_fail_warned = False

  def load(self, log_failure = False):
    """
    Loads or reloads the torrc contents, raising an IOError if there's a
    problem.

    Arguments:
      log_failure - if the torrc fails to load and we've never provided a
                   warning for this before then logs a warning
    """

    with self._vals_lock:
      # clears contents and caches
      self.contents, self.config_location = None, None
      self.displayable_contents = None
      self.stripped_contents = None
      self.corrections = None

      try:
        self.config_location = get_config_location()
        config_file = open(self.config_location, 'r')
        self.contents = config_file.readlines()
        config_file.close()
      except IOError as exc:
        if log_failure and not self.is_foad_fail_warned:
          log.warn('Unable to load torrc (%s)' % exc.strerror)
          self.is_foad_fail_warned = True

        raise exc

  def is_loaded(self):
    """
    Provides true if there's loaded contents, false otherwise.
    """

    return self.contents is not None

  def get_config_location(self):
    """
    Provides the location of the loaded configuration contents. This may be
    available, even if the torrc failed to be loaded.
    """

    return self.config_location

  def get_contents(self):
    """
    Provides the contents of the configuration file.
    """

    with self._vals_lock:
      return list(self.contents) if self.contents else None

  def get_display_contents(self, strip = False):
    """
    Provides the contents of the configuration file, formatted in a rendering
    frindly fashion:
    - Tabs print as three spaces. Keeping them as tabs is problematic for
      layouts since it's counted as a single character, but occupies several
      cells.
    - Strips control and unprintable characters.

    Arguments:
      strip - removes comments and extra whitespace if true
    """

    with self._vals_lock:
      if not self.is_loaded():
        return None
      else:
        if self.displayable_contents is None:
          # restricts contents to displayable characters
          self.displayable_contents = []

          for line_number in range(len(self.contents)):
            line_text = self.contents[line_number]
            line_text = line_text.replace('\t', '   ')
            line_text = ui_tools.get_printable(line_text)
            self.displayable_contents.append(line_text)

        if strip:
          if self.stripped_contents is None:
            self.stripped_contents = _strip_comments(self.displayable_contents)

          return list(self.stripped_contents)
        else:
          return list(self.displayable_contents)

  def get_corrections(self):
    """
    Performs validation on the loaded contents and provides back the
    corrections. If validation is disabled then this won't provide any
    results.
    """

    with self._vals_lock:
      if not self.is_loaded():
        return None
      else:
        tor_version = tor_controller().get_version(None)
        skip_validation = not CONFIG['features.torrc.validate']
        skip_validation |= (tor_version is None or not tor_version >= stem.version.Requirement.GETINFO_CONFIG_TEXT)

        if skip_validation:
          log.info('Skipping torrc validation (requires tor 0.2.2.7-alpha)')
          return {}
        else:
          if self.corrections is None:
            self.corrections = validate(self.contents)

          return list(self.corrections)

  def get_lock(self):
    """
    Provides the lock governing concurrent access to the contents.
    """

    return self._vals_lock

  def log_validation_issues(self):
    """
    Performs validation on the loaded contents, and logs warnings for issues
    that are found.
    """

    corrections = self.get_corrections()

    if corrections:
      duplicate_options, default_options, mismatch_lines, missing_options = [], [], [], []

      for line_number, issue, msg in corrections:
        if issue == ValidationError.DUPLICATE:
          duplicate_options.append('%s (line %i)' % (msg, line_number + 1))
        elif issue == ValidationError.IS_DEFAULT:
          default_options.append('%s (line %i)' % (msg, line_number + 1))
        elif issue == ValidationError.MISMATCH:
          mismatch_lines.append(line_number + 1)
        elif issue == ValidationError.MISSING:
          missing_options.append(msg)

      if duplicate_options or default_options:
        msg = "Unneeded torrc entries found. They've been highlighted in blue on the torrc page."

        if duplicate_options:
          if len(duplicate_options) > 1:
            msg += '\n- entries ignored due to having duplicates: '
          else:
            msg += '\n- entry ignored due to having a duplicate: '

          duplicate_options.sort()
          msg += ', '.join(duplicate_options)

        if default_options:
          if len(default_options) > 1:
            msg += '\n- entries match their default values: '
          else:
            msg += '\n- entry matches its default value: '

          default_options.sort()
          msg += ', '.join(default_options)

        log.notice(msg)

      if mismatch_lines or missing_options:
        msg = "The torrc differs from what tor's using. You can issue a sighup to reload the torrc values by pressing x."

        if mismatch_lines:
          if len(mismatch_lines) > 1:
            msg += '\n- torrc values differ on lines: '
          else:
            msg += '\n- torrc value differs on line: '

          mismatch_lines.sort()
          msg += ', '.join([str(val + 1) for val in mismatch_lines])

        if missing_options:
          if len(missing_options) > 1:
            msg += '\n- configuration values are missing from the torrc: '
          else:
            msg += '\n- configuration value is missing from the torrc: '

          missing_options.sort()
          msg += ', '.join(missing_options)

        log.warn(msg)
