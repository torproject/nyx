"""
Helper functions for working with tor's configuration file.
"""

import codecs
import os
import time
import socket
import threading

import stem.version

from nyx.util import tor_controller, ui_tools

from stem.util import conf, enum, log, str_tools, system

# filename used for cached tor config descriptions

CONFIG_DESC_FILENAME = 'torConfigDesc.txt'

# messages related to loading the tor configuration descriptions

DESC_LOAD_SUCCESS_MSG = "Loaded configuration descriptions from '%s' (runtime: %0.3f)"
DESC_LOAD_FAILED_MSG = 'Unable to load configuration descriptions (%s)'
DESC_INTERNAL_LOAD_SUCCESS_MSG = 'Falling back to descriptions for Tor %s'
DESC_INTERNAL_LOAD_FAILED_MSG = "Unable to load fallback descriptions. Categories and help for Tor's configuration options won't be available. (%s)"
DESC_READ_MAN_SUCCESS_MSG = "Read descriptions for tor's configuration options from its man page (runtime %0.3f)"
DESC_READ_MAN_FAILED_MSG = "Unable to get the descriptions of Tor's configuration options from its man page (%s)"
DESC_SAVE_SUCCESS_MSG = "Saved configuration descriptions to '%s' (runtime: %0.3f)"
DESC_SAVE_FAILED_MSG = 'Unable to save configuration descriptions (%s)'


def conf_handler(key, value):
  if key == 'torrc.important':
    # stores lowercase entries to drop case sensitivity
    return [entry.lower() for entry in value]


CONFIG = conf.config_dict('nyx', {
  'features.torrc.validate': True,
  'torrc.important': [],
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
}, conf_handler)


def general_conf_handler(config, key):
  value = config.get(key)

  if key.startswith('torrc.summary.'):
    # we'll look for summary keys with a lowercase config name
    CONFIG[key.lower()] = value
  elif key.startswith('torrc.units.') and value:
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

# descriptions of tor's configuration options fetched from its man page

CONFIG_DESCRIPTIONS_LOCK = threading.RLock()
CONFIG_DESCRIPTIONS = {}

# categories for tor configuration options

Category = enum.Enum('GENERAL', 'CLIENT', 'RELAY', 'DIRECTORY', 'AUTHORITY', 'HIDDEN_SERVICE', 'TESTING', 'UNKNOWN')

TORRC = None  # singleton torrc instance
MAN_OPT_INDENT = 7  # indentation before options in the man page
MAN_EX_INDENT = 15  # indentation used for man page examples
PERSIST_ENTRY_DIVIDER = '-' * 80 + '\n'  # splits config entries when saving to a file
MULTILINE_PARAM = None  # cached multiline parameters (lazily loaded)

# torrc options that bind to ports

PORT_OPT = ('SocksPort', 'ORPort', 'DirPort', 'ControlPort', 'TransPort')


class ManPageEntry:
  """
  Information provided about a tor configuration option in its man page entry.
  """

  def __init__(self, option, index, category, arg_usage, description):
    self.option = option
    self.index = index
    self.category = category
    self.arg_usage = arg_usage
    self.description = description


def get_torrc():
  """
  Singleton constructor for a Controller. Be aware that this starts as being
  unloaded, needing the torrc contents to be loaded before being functional.
  """

  global TORRC

  if TORRC is None:
    TORRC = Torrc()

  return TORRC


def load_option_descriptions(load_path = None, check_version = True):
  """
  Fetches and parses descriptions for tor's configuration options from its man
  page. This can be a somewhat lengthy call, and raises an IOError if issues
  occure. When successful loading from a file this returns the version for the
  contents loaded.

  If available, this can load the configuration descriptions from a file where
  they were previously persisted to cut down on the load time (latency for this
  is around 200ms).

  Arguments:
    load_path     - if set, this attempts to fetch the configuration
                   descriptions from the given path instead of the man page
    check_version - discards the results if true and tor's version doens't
                   match the cached descriptors, otherwise accepts anyway
  """

  with CONFIG_DESCRIPTIONS_LOCK:
    CONFIG_DESCRIPTIONS.clear()

    raised_exc = None
    loaded_version = ''

    try:
      if load_path:
        # Input file is expected to be of the form:
        # <option>
        # <arg description>
        # <description, possibly multiple lines>
        # <PERSIST_ENTRY_DIVIDER>
        input_file = open(load_path, 'r')
        input_file_contents = input_file.readlines()
        input_file.close()

        try:
          version_line = input_file_contents.pop(0).rstrip()

          if version_line.startswith('Tor Version '):
            file_version = version_line[12:]
            loaded_version = file_version
            tor_version = tor_controller().get_info('version', '')

            if check_version and file_version != tor_version:
              msg = "wrong version, tor is %s but the file's from %s" % (tor_version, file_version)
              raise IOError(msg)
          else:
            raise IOError('unable to parse version')

          while input_file_contents:
            # gets category enum, failing if it doesn't exist
            category = input_file_contents.pop(0).rstrip()

            if category not in Category:
              base_msg = "invalid category in input file: '%s'"
              raise IOError(base_msg % category)

            # gets the position in the man page
            index_arg, index_str = -1, input_file_contents.pop(0).rstrip()

            if index_str.startswith('index: '):
              index_str = index_str[7:]

              if index_str.isdigit():
                index_arg = int(index_str)
              else:
                raise IOError('non-numeric index value: %s' % index_str)
            else:
              raise IOError('malformed index argument: %s' % index_str)

            option = input_file_contents.pop(0).rstrip()
            argument = input_file_contents.pop(0).rstrip()

            description, loaded_line = '', input_file_contents.pop(0)

            while loaded_line != PERSIST_ENTRY_DIVIDER:
              description += loaded_line

              if input_file_contents:
                loaded_line = input_file_contents.pop(0)
              else:
                break

            CONFIG_DESCRIPTIONS[option.lower()] = ManPageEntry(option, index_arg, category, argument, description.rstrip())
        except IndexError:
          CONFIG_DESCRIPTIONS.clear()
          raise IOError('input file format is invalid')
      else:
        man_call_results = system.call('man tor', None)

        if not man_call_results:
          raise IOError('man page not found')

        # Fetches all options available with this tor instance. This isn't
        # vital, and the valid_options are left empty if the call fails.

        controller, valid_options = tor_controller(), []
        config_option_query = controller.get_info('config/names', None)

        if config_option_query:
          for line in config_option_query.strip().split('\n'):
            valid_options.append(line[:line.find(' ')].lower())

        option_count, last_option, last_arg = 0, None, None
        last_category, last_description = Category.GENERAL, ''

        for line in man_call_results:
          line = codecs.latin_1_encode(line, 'replace')[0]
          line = ui_tools.get_printable(line)
          stripped_line = line.strip()

          # we have content, but an indent less than an option (ignore line)
          # if stripped_line and not line.startswith(' ' * MAN_OPT_INDENT): continue

          # line starts with an indent equivilant to a new config option

          is_opt_indent = line.startswith(' ' * MAN_OPT_INDENT) and line[MAN_OPT_INDENT] != ' '

          is_category_line = not line.startswith(' ') and 'OPTIONS' in line

          # if this is a category header or a new option, add an entry using the
          # buffered results

          if is_opt_indent or is_category_line:
            # Filters the line based on if the option is recognized by tor or
            # not. This isn't necessary for nyx, so if unable to make the check
            # then we skip filtering (no loss, the map will just have some extra
            # noise).

            stripped_description = last_description.strip()

            if last_option and (not valid_options or last_option.lower() in valid_options):
              CONFIG_DESCRIPTIONS[last_option.lower()] = ManPageEntry(last_option, option_count, last_category, last_arg, stripped_description)
              option_count += 1

            last_description = ''

            # parses the option and argument

            line = line.strip()
            div_index = line.find(' ')

            if div_index != -1:
              last_option, last_arg = line[:div_index], line[div_index + 1:]

            # if this is a category header then switch it

            if is_category_line:
              if line.startswith('OPTIONS'):
                last_category = Category.GENERAL
              elif line.startswith('CLIENT'):
                last_category = Category.CLIENT
              elif line.startswith('SERVER'):
                last_category = Category.RELAY
              elif line.startswith('DIRECTORY SERVER'):
                last_category = Category.DIRECTORY
              elif line.startswith('DIRECTORY AUTHORITY SERVER'):
                last_category = Category.AUTHORITY
              elif line.startswith('HIDDEN SERVICE'):
                last_category = Category.HIDDEN_SERVICE
              elif line.startswith('TESTING NETWORK'):
                last_category = Category.TESTING
              else:
                log.notice('Unrecognized category in the man page: %s' % line.strip())
          else:
            # Appends the text to the running description. Empty lines and lines
            # starting with a specific indentation are used for formatting, for
            # instance the ExitPolicy and TestingTorNetwork entries.

            if last_description and last_description[-1] != '\n':
              last_description += ' '

            if not stripped_line:
              last_description += '\n\n'
            elif line.startswith(' ' * MAN_EX_INDENT):
              last_description += '    %s\n' % stripped_line
            else:
              last_description += stripped_line
    except IOError as exc:
      raised_exc = exc

  if raised_exc:
    raise raised_exc
  else:
    return loaded_version


def save_option_descriptions(path):
  """
  Preserves the current configuration descriptors to the given path. This
  raises an IOError or OSError if unable to do so.

  Arguments:
    path - location to persist configuration descriptors
  """

  # make dir if the path doesn't already exist

  base_dir = os.path.dirname(path)

  if not os.path.exists(base_dir):
    os.makedirs(base_dir)

  output_file = open(path, 'w')

  with CONFIG_DESCRIPTIONS_LOCK:
    sorted_options = CONFIG_DESCRIPTIONS.keys()
    sorted_options.sort()

    tor_version = tor_controller().get_info('version', '')
    output_file.write('Tor Version %s\n' % tor_version)

    for i in range(len(sorted_options)):
      man_entry = get_config_description(sorted_options[i])
      output_file.write('%s\nindex: %i\n%s\n%s\n%s\n' % (man_entry.category, man_entry.index, man_entry.option, man_entry.arg_usage, man_entry.description))

      if i != len(sorted_options) - 1:
        output_file.write(PERSIST_ENTRY_DIVIDER)

    output_file.close()


def get_config_summary(option):
  """
  Provides a short summary description of the configuration option. If none is
  known then this proivdes None.

  Arguments:
    option - tor config option
  """

  return CONFIG.get('torrc.summary.%s' % option.lower())


def is_important(option):
  """
  Provides True if the option has the 'important' flag in the configuration,
  False otherwise.

  Arguments:
    option - tor config option
  """

  return option.lower() in CONFIG['torrc.important']


def get_config_description(option):
  """
  Provides ManPageEntry instances populated with information fetched from the
  tor man page. This provides None if no such option has been loaded. If the
  man page is in the process of being loaded then this call blocks until it
  finishes.

  Arguments:
    option - tor config option
  """

  with CONFIG_DESCRIPTIONS_LOCK:
    if option.lower() in CONFIG_DESCRIPTIONS:
      return CONFIG_DESCRIPTIONS[option.lower()]
    else:
      return None


def get_config_options():
  """
  Provides the configuration options from the loaded man page. This is an empty
  list if no man page has been loaded.
  """

  with CONFIG_DESCRIPTIONS_LOCK:
    return [CONFIG_DESCRIPTIONS[opt].option for opt in CONFIG_DESCRIPTIONS]


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


def get_custom_options(include_value = False):
  """
  Provides the torrc parameters that differ from their defaults.

  Arguments:
    include_value - provides the current value with results if true, otherwise
                   this just contains the options
  """

  config_text = tor_controller().get_info('config-text', '').strip()
  config_lines = config_text.split('\n')

  # removes any duplicates

  config_lines = list(set(config_lines))

  # The "GETINFO config-text" query only provides options that differ
  # from Tor's defaults with the exception of its Log and Nickname entries
  # which, even if undefined, returns "Log notice stdout" as per:
  # https://trac.torproject.org/projects/tor/ticket/2362
  #
  # If this is from the deb then it will be "Log notice file /var/log/tor/log"
  # due to special patching applied to it, as per:
  # https://trac.torproject.org/projects/tor/ticket/4602

  try:
    config_lines.remove('Log notice stdout')
  except ValueError:
    pass

  try:
    config_lines.remove('Log notice file /var/log/tor/log')
  except ValueError:
    pass

  try:
    config_lines.remove('Nickname %s' % socket.gethostname())
  except ValueError:
    pass

  if include_value:
    return config_lines
  else:
    return [line[:line.find(' ')] for line in config_lines]


def save_conf(destination = None, contents = None):
  """
  Saves the configuration to the given path. If this is equivilant to
  issuing a SAVECONF (the contents and destination match what tor's using)
  then that's done. Otherwise, this writes the contents directly. This raises
  an IOError if unsuccessful.

  Arguments:
    destination - path to be saved to, the current config location if None
    contents    - configuration to be saved, the current config if None
  """

  if destination:
    destination = os.path.abspath(destination)

  # fills default config values, and sets is_saveconf to false if they differ
  # from the arguments

  is_saveconf, start_time = True, time.time()

  current_config = get_custom_options(True)

  if not contents:
    contents = current_config
  else:
    is_saveconf &= contents == current_config

  # The "GETINFO config-text" option was introduced in Tor version 0.2.2.7. If
  # we're writing custom contents then this is fine, but if we're trying to
  # save the current configuration then we need to fail if it's unavailable.
  # Otherwise we'd write a blank torrc as per...
  # https://trac.torproject.org/projects/tor/ticket/3614

  if contents == ['']:
    # double check that "GETINFO config-text" is unavailable rather than just
    # giving an empty result

    if tor_controller().get_info('config-text', None) is None:
      raise IOError('determining the torrc requires Tor version 0.2.2.7')

  current_location = None

  try:
    current_location = get_config_location()

    if not destination:
      destination = current_location
    else:
      is_saveconf &= destination == current_location
  except IOError:
    pass

  if not destination:
    raise IOError("unable to determine the torrc's path")

  log_msg = 'Saved config by %%s to %s (runtime: %%0.4f)' % destination

  # attempts SAVECONF if we're updating our torrc with the current state

  if is_saveconf:
    try:
      tor_controller().save_conf()

      try:
        get_torrc().load()
      except IOError:
        pass

      log.debug(log_msg % ('SAVECONF', time.time() - start_time))
      return  # if successful then we're done
    except:
      pass

  # if the SAVECONF fails or this is a custom save then write contents directly

  try:
    # make dir if the path doesn't already exist

    base_dir = os.path.dirname(destination)

    if not os.path.exists(base_dir):
      os.makedirs(base_dir)

    # saves the configuration to the file

    config_file = open(destination, 'w')
    config_file.write('\n'.join(contents))
    config_file.close()
  except (IOError, OSError) as exc:
    raise IOError(exc)

  # reloads the cached torrc if overwriting it

  if destination == current_location:
    try:
      get_torrc().load()
    except IOError:
      pass

  log.debug(log_msg % ('directly writing', time.time() - start_time))


def validate(contents = None):
  """
  Performs validation on the given torrc contents, providing back a listing of
  (line number, issue, msg) tuples for issues found. If the issue occures on a
  multiline torrc entry then the line number is for the last line of the entry.

  Arguments:
    contents - torrc contents
  """

  controller = tor_controller()
  custom_options = get_custom_options()
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


def load_configuration_descriptions(path_prefix):
  """
  Attempts to load descriptions for tor's configuration options, fetching them
  from the man page and persisting them to a file to speed future startups.
  """

  # It is important that this is loaded before entering the curses context,
  # otherwise the man call pegs the cpu for around a minute (I'm not sure
  # why... curses must mess the terminal in a way that's important to man).

  if CONFIG['features.config.descriptions.enabled']:
    is_config_descriptions_loaded = False

    # determines the path where cached descriptions should be persisted (left
    # undefined if caching is disabled)

    descriptor_path = None

    if CONFIG['features.config.descriptions.persist']:
      data_dir = CONFIG['startup.data_directory']

      if not data_dir.endswith('/'):
        data_dir += '/'

      descriptor_path = os.path.expanduser(data_dir + 'cache/') + CONFIG_DESC_FILENAME

    # attempts to load configuration descriptions cached in the data directory

    if descriptor_path:
      try:
        load_start_time = time.time()
        load_option_descriptions(descriptor_path)
        is_config_descriptions_loaded = True

        log.info(DESC_LOAD_SUCCESS_MSG % (descriptor_path, time.time() - load_start_time))
      except IOError as exc:
        log.info(DESC_LOAD_FAILED_MSG % exc.strerror)

    # fetches configuration options from the man page

    if not is_config_descriptions_loaded:
      try:
        load_start_time = time.time()
        load_option_descriptions()
        is_config_descriptions_loaded = True

        log.info(DESC_READ_MAN_SUCCESS_MSG % (time.time() - load_start_time))
      except IOError as exc:
        log.notice(DESC_READ_MAN_FAILED_MSG % exc.strerror)

      # persists configuration descriptions

      if is_config_descriptions_loaded and descriptor_path:
        try:
          load_start_time = time.time()
          save_option_descriptions(descriptor_path)
          log.info(DESC_SAVE_SUCCESS_MSG % (descriptor_path, time.time() - load_start_time))
        except IOError as exc:
          log.notice(DESC_SAVE_FAILED_MSG % exc.strerror)
        except OSError as exc:
          log.notice(DESC_SAVE_FAILED_MSG % exc)

    # finally fall back to the cached descriptors provided with nyx (this is
    # often the case for tbb and manual builds)

    if not is_config_descriptions_loaded:
      try:
        load_start_time = time.time()
        loaded_version = load_option_descriptions('%sresources/%s' % (path_prefix, CONFIG_DESC_FILENAME), False)
        is_config_descriptions_loaded = True
        log.notice(DESC_INTERNAL_LOAD_SUCCESS_MSG % loaded_version)
      except IOError as exc:
        log.error(DESC_INTERNAL_LOAD_FAILED_MSG % exc.strerror)
