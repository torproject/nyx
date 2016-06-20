from nyx.curses import GREEN, CYAN, RED, MAGENTA, BLUE, BOLD, HIGHLIGHT
from nyx import tor_controller


# initial location /write will save to when no path is specified
DEFAULT_WRITE_PATH = "/tmp/torInterpretor_output"

MULTILINE_UNIMPLEMENTED_NOTICE = "Multi-line control options like this are not yet implemented."

GENERAL_HELP = """Interpretor commands include:
  /help   - provides information for interpretor and tor commands/config options
  /info   - general information for a relay
  /find   - searches backlog for lines with the given regex
  /events - prints events that we've received
  /write  - saves backlog to a given location
  /quit   - shuts down the interpretor

Tor commands include:
  GETINFO - queries information from tor
  GETCONF, SETCONF, RESETCONF - show or edit a configuration option
  SIGNAL - issues control signal to the process (for resetting, stopping, etc)
  SETEVENTS - configures the events tor will notify us of

  USEFEATURE - enables custom behavior for the controller
  SAVECONF - writes tor's current configuration to our torrc
  LOADCONF - loads the given input like it was part of our torrc
  MAPADDRESS - replaces requests for one address with another
  POSTDESCRIPTOR - adds a relay descriptor to our cache
  EXTENDCIRCUIT - create or extend a tor circuit
  SETCIRCUITPURPOSE - configures the purpose associated with a circuit
  CLOSECIRCUIT - closes the given circuit
  ATTACHSTREAM - associates an application's stream with a tor circuit
  REDIRECTSTREAM - sets a stream's destination
  CLOSESTREAM - closes the given stream
  RESOLVE - issues an asynchronous dns or rdns request over tor
  TAKEOWNERSHIP - instructs tor to quit when this control connection is closed
  PROTOCOLINFO - queries version and controller authentication information
  QUIT - disconnect the control connection

For more information use '/help [OPTION]'."""

HELP_HELP = """Provides usage information for the given interpretor, tor command, or tor
configuration option.

Example:
  /help info        # provides a description of the '/info' option
  /help GETINFO     # usage information for tor's GETINFO controller option
  /help ExitPolicy  # description of tor's ExitPolicy configuration option"""

HELP_WRITE = """Writes the interpretor's backlog to the given path. If no location is
specified then this saves to the last path specified (initially '%s').""" % DEFAULT_WRITE_PATH

HELP_EVENTS = """Provides events that we've received belonging to the given event types. If
no types are specified then this provides all the messages that we've
received."""

HELP_INFO = """Provides general information for a relay that's currently in the consensus.
If no relay is specified then this provides information on ourselves."""

HELP_FIND = """Searches the backlog for lines matching a given regular expression pattern.
Results are deduplicated and the matching portions bolded."""

HELP_QUIT = """Terminates the interpretor."""

HELP_GETINFO = """Queries the tor process for information. Options are...
"""

HELP_GETCONF = """Provides the current value for a given configuration value. Options include...
"""

HELP_SETCONF = """Sets the given configuration parameters. Values can be quoted or non-quoted
strings, and reverts the option to 0 or NULL if not provided.

Examples:
  * Sets a contact address and resets our family to NULL
    SETCONF MyFamily ContactInfo=foo@bar.com

  * Sets an exit policy that only includes port 80/443
    SETCONF ExitPolicy=\"accept *:80, accept *:443, reject *:*\""""

HELP_RESETCONF = """Reverts the given configuration options to their default values. If a value
is provided then this behaves in the same way as SETCONF.

Examples:
  * Returns both of our accounting parameters to their defaults
    RESETCONF AccountingMax AccountingStart

  * Uses the default exit policy and sets our nickname to be 'Goomba'
    RESETCONF ExitPolicy Nickname=Goomba"""

HELP_SIGNAL = """Issues a signal that tells the tor process to reload its torrc, dump its
stats, halt, etc.
"""

SIGNAL_DESCRIPTIONS = (
  ("RELOAD / HUP", "reload our torrc"),
  ("SHUTDOWN / INT", "gracefully shut down, waiting 30 seconds if we're a relay"),
  ("DUMP / USR1", "logs information about open connections and circuits"),
  ("DEBUG / USR2", "makes us log at the DEBUG runlevel"),
  ("HALT / TERM", "immediately shut down"),
  ("CLEARDNSCACHE", "clears any cached DNS results"),
  ("NEWNYM", "clears the DNS cache and uses new circuits for future connections")
)

HELP_SETEVENTS = """Sets the events that we will receive. This turns off any events that aren't
listed so sending 'SETEVENTS' without any values will turn off all event reporting.

For Tor versions between 0.1.1.9 and 0.2.2.1 adding 'EXTENDED' causes some
events to give us additional information. After version 0.2.2.1 this is
always on.

Events include...
"""

HELP_USEFEATURE = """Customizes the behavior of the control port. Options include...
"""

HELP_SAVECONF = """Writes Tor's current configuration to its torrc."""

HELP_LOADCONF = """Reads the given text like it belonged to our torrc.

Example:
  +LOADCONF
  # sets our exit policy to just accept ports 80 and 443
  ExitPolicy accept *:80
  ExitPolicy accept *:443
  ExitPolicy reject *:*
  ."""

HELP_MAPADDRESS = """Replaces future requests for one address with another.

Example:
  MAPADDRESS 0.0.0.0=torproject.org 1.2.3.4=tor.freehaven.net"""

HELP_POSTDESCRIPTOR = """Simulates getting a new relay descriptor."""

HELP_EXTENDCIRCUIT = """Extends the given circuit or create a new one if the CircuitID is zero. The
PATH is a comma separated list of fingerprints. If it isn't set then this
uses Tor's normal path selection."""

HELP_SETCIRCUITPURPOSE = """Sets the purpose attribute for a circuit."""

HELP_CLOSECIRCUIT = """Closes the given circuit. If "IfUnused" is included then this only closes
the circuit if it isn't currently being used."""

HELP_ATTACHSTREAM = """Attaches a stream with the given built circuit (tor picks one on its own if
CircuitID is zero). If HopNum is given then this hop is used to exit the
circuit, otherwise the last relay is used."""

HELP_REDIRECTSTREAM = """Sets the destination for a given stream. This can only be done after a
stream is created but before it's attached to a circuit."""

HELP_CLOSESTREAM = """Closes the given stream, the reason being an integer matching a reason as
per section 6.3 of the tor-spec."""

HELP_RESOLVE = """Performs IPv4 DNS resolution over tor, doing a reverse lookup instead if
"mode=reverse" is included. This request is processed in the background and
results in a ADDRMAP event with the response."""

HELP_TAKEOWNERSHIP = """Instructs Tor to gracefully shut down when this control connection is closed."""

HELP_PROTOCOLINFO = """Provides bootstrapping information that a controller might need when first
starting, like Tor's version and controller authentication. This can be done
before authenticating to the control port."""

HELP_OPTIONS = {
  "HELP": ("/help [OPTION]", HELP_HELP),
  "WRITE": ("/write [PATH]", HELP_WRITE),
  "EVENTS": ("/events [types]", HELP_EVENTS),
  "INFO": ("/info [relay fingerprint, nickname, or IP address]", HELP_INFO),
  "FIND": ("/find PATTERN", HELP_FIND),
  "QUIT": ("/quit", HELP_QUIT),
  "GETINFO": ("GETINFO OPTION", HELP_GETINFO),
  "GETCONF": ("GETCONF OPTION", HELP_GETCONF),
  "SETCONF": ("SETCONF PARAM[=VALUE]", HELP_SETCONF),
  "RESETCONF": ("RESETCONF PARAM[=VALUE]", HELP_RESETCONF),
  "SIGNAL": ("SIGNAL SIG", HELP_SIGNAL),
  "SETEVENTS": ("SETEVENTS [EXTENDED] [EVENTS]", HELP_SETEVENTS),
  "USEFEATURE": ("USEFEATURE OPTION", HELP_USEFEATURE),
  "SAVECONF": ("SAVECONF", HELP_SAVECONF),
  "LOADCONF": ("LOADCONF...", HELP_LOADCONF),
  "MAPADDRESS": ("MAPADDRESS SOURCE_ADDR=DESTINATION_ADDR", HELP_MAPADDRESS),
  "POSTDESCRIPTOR": ("POSTDESCRIPTOR [purpose=general/controller/bridge] [cache=yes/no]...", HELP_POSTDESCRIPTOR),
  "EXTENDCIRCUIT": ("EXTENDCIRCUIT CircuitID [PATH] [purpose=general/controller]", HELP_EXTENDCIRCUIT),
  "SETCIRCUITPURPOSE": ("SETCIRCUITPURPOSE CircuitID purpose=general/controller", HELP_SETCIRCUITPURPOSE),
  "CLOSECIRCUIT": ("CLOSECIRCUIT CircuitID [IfUnused]", HELP_CLOSECIRCUIT),
  "ATTACHSTREAM": ("ATTACHSTREAM StreamID CircuitID [HOP=HopNum]", HELP_ATTACHSTREAM),
  "REDIRECTSTREAM": ("REDIRECTSTREAM StreamID Address [Port]", HELP_REDIRECTSTREAM),
  "CLOSESTREAM": ("CLOSESTREAM StreamID Reason [Flag]", HELP_CLOSESTREAM),
  "RESOLVE": ("RESOLVE [mode=reverse] address", HELP_RESOLVE),
  "TAKEOWNERSHIP": ("TAKEOWNERSHIP", HELP_TAKEOWNERSHIP),
  "PROTOCOLINFO": ("PROTOCOLINFO [ProtocolVersion]", HELP_PROTOCOLINFO),
}

class ControlInterpreter:
  """
  Interpretor that handles queries to the control port, providing usability
  imporvements like irc style help optoins. This tracks input and responses.
  """

  def do_help(self, arg, output_entry):
    """
    Performs the '/help' operation, giving usage information for the given
    argument or a general summary if there wasn't one.
    """

    arg = arg.upper()

    # If there's multiple arguments then just take the first. This is
    # particularly likely if they're trying to query a full command (for
    # instance "/help GETINFO version")
    arg = arg.split(" ")[0]

    # strip slash if someone enters an interpretor command (ex. "/help /help")
    if arg.startswith("/"): arg = arg[1:]

    if arg:
      if arg in HELP_OPTIONS:
        # Provides information for the tor or interpretor argument. This bolds
        # the usage information and indents the description after it.
        usage, description = HELP_OPTIONS[arg]

        output_entry.append([(usage, BLUE, BOLD)])

        for line in description.split("\n"):
          output_entry.append([("  " + line, BLUE, )])

        if arg == "SIGNAL":
          # lists descriptions for all of the signals
          for signal, description in SIGNAL_DESCRIPTIONS:
            output_entry.append([("%-15s" % signal, BLUE, BOLD), (" - %s" % description, BLUE, )])
        elif arg == "SETEVENTS":
          # lists all of the event types
          event_options = tor_controller().get_info("events/names")
          if event_options:
            event_entries = event_options.split()

            # displays four columns of 20 characters
            for i in range(0, len(event_entries), 4):
              line_entries = event_entries[i : i+4]

              line_content = ""
              for entry in line_entries:
                line_content += "%-20s" % entry

              output_entry.append([(line_content, BLUE, )])
        elif arg == "USEFEATURE":
          # lists the feature options
          feature_options = tor_controller().get_info("features/names")
          if feature_options:
            output_entry.append([(feature_options, BLUE, )])
        elif arg in ("LOADCONF", "POSTDESCRIPTOR"):
          # gives a warning that this option isn't yet implemented
          output_entry.append([(MULTILINE_UNIMPLEMENTED_NOTICE, RED, BOLD)])
      else:
        output_entry.append([("No help information available for '%s'..." % arg, RED, BOLD)])
    else:
      # provides the GENERAL_HELP with everything bolded except descriptions
      for line in GENERAL_HELP.split("\n"):
        cmd_start = line.find(" - ")
        if cmd_start != -1:
          output_entry.append([(line[:cmd_start], BLUE, BOLD), (line[cmd_start:], BLUE, )])
        else:
          output_entry.append([(line, BLUE, BOLD)])

  def handle_query(self, user_input):
    """
    Processes the given input. Requests starting with a '/' are special
    commands to the interpretor, and anything else is sent to the control port.
    This returns an input/output tuple, each entry being a list of lines, each
    line having a list of (msg, format) tuples for the content to be displayed.
    This raises a InterpretorClosed if the interpretor should be shut down.

    Arguments:
      user_input - user input to be processed
    """

    user_input = user_input.strip()

    input_entry, output_entry = [], []

    if " " in user_input: cmd, arg = user_input.split(" ", 1)
    else: cmd, arg = user_input, ""

    if cmd.startswith("/"):
      input_entry.append((user_input, MAGENTA, BOLD))
      if cmd == "/quit": raise InterpreterClosed()
      elif cmd == "/help": self.do_help(arg, output_entry)
      else:
        output_entry.append([("Not yet implemented...", RED, BOLD)])
    else:
      cmd = cmd.upper()
      input_entry.append((cmd + " ", GREEN, BOLD))
      if arg:
        input_entry.append((arg, CYAN, BOLD))

      if cmd == "GETINFO":
        resp = tor_controller().get_info(arg)
        for line in resp.split('\n'):
          output_entry.append([(line, CYAN,)])
    
    return input_entry, output_entry


class InterpreterClosed(Exception):
  """
  Exception raised when the interpreter should be shut down.
  """

  pass
