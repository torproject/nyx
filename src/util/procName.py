"""
Module to allow for arbitrary renaming of our python process. This is mostly
based on:
http://www.rhinocerus.net/forum/lang-python/569677-setting-program-name-like-0-perl.html#post2272369
and an adaptation by Jake: https://github.com/ioerror/chameleon

A cleaner implementation is available at:
https://github.com/cream/libs/blob/b38970e2a6f6d2620724c828808235be0445b799/cream/util/procname.py
but I'm not quite clear on their implementation, and it only does targeted
argument replacement (ie, replace argv[0], argv[1], etc but with a string
the same size).
"""

import sys
import ctypes
import ctypes.util

# flag for setting the process name, found in '/usr/include/linux/prctl.h'
PR_SET_NAME = 15

argc_t = ctypes.POINTER(ctypes.c_char_p)

Py_GetArgcArgv = ctypes.pythonapi.Py_GetArgcArgv
Py_GetArgcArgv.restype = None
Py_GetArgcArgv.argtypes = [ctypes.POINTER(ctypes.c_int),
                           ctypes.POINTER(argc_t)]

# tracks the last name we've changed the process to
currentProcessName = None
maxNameLength = -1

def renameProcess(processName):
  """
  Renames our current process from "python <args>" to a custom name.
  
  Arguments:
    processName - new name for our process
  """
  
  _setArgv(processName)
  if sys.platform == "linux2":
    _setPrctlName(processName)
  elif sys.platform == "freebsd7":
    _setProcTitle(processName)

def _setArgv(processName):
  """
  Overwrites our argv in a similar fashion to how it's done in C with:
  strcpy(argv[0], "new_name");
  """
  
  global currentProcessName, maxNameLength
  
  argv = ctypes.c_int(0)
  argc = argc_t()
  Py_GetArgcArgv(argv, ctypes.pointer(argc))
  
  # The original author did the memset for 256, while Jake did it for the
  # processName length (capped at 1608). I'm not sure of the reasons for
  # either of these limits, but setting it to anything higher than than the
  # length of the null terminated process name should be pointless, so opting
  # for Jake's implementation on this.
  
  if currentProcessName == None:
    # Getting argv via...
    # currentProcessName = " ".join(["python"] + sys.argv)
    # 
    # doesn't do the trick since this will miss interpretor arguments like...
    # python -W ignore::DeprecationWarning myScript.py
    # 
    # Hence we're fetching this via our ctypes argv. Alternatively we could
    # use ps, though this is less desirable:
    # "ps -p %i -o args" % os.getpid()
    
    args = []
    for i in range(100):
      if argc[i] == None: break
      args.append(str(argc[i]))
    
    currentProcessName = " ".join(args)
    maxNameLength = len(currentProcessName)
  
  if len(processName) > maxNameLength:
    msg = "can't rename process to something longer than our initial name since this would overwrite memory used for the env"
    raise IOError(msg)
  
  # space we need to clear
  zeroSize = max(len(currentProcessName), len(processName))
  
  ctypes.memset(argc.contents, 0, zeroSize + 1) # null terminate the string's end
  ctypes.memmove(argc.contents, processName, len(processName))
  currentProcessName = processName

def _setPrctlName(processName):
  """
  Sets the prctl name, which is used by top and killall. This appears to be
  Linux specific and has the max of 15 characters. Source:
  http://stackoverflow.com/questions/564695/is-there-a-way-to-change-effective-process-name-in-python/923034#923034
  """
  
  libc = ctypes.CDLL(ctypes.util.find_library("c"))
  nameBuffer = ctypes.create_string_buffer(len(processName)+1)
  nameBuffer.value = processName
  libc.prctl(PR_SET_NAME, ctypes.byref(nameBuffer), 0, 0, 0)

def _setProcTitle(processName):
  """
  BSD specific calls (should be compataible with both FreeBSD and OpenBSD:
  http://fxr.watson.org/fxr/source/gen/setproctitle.c?v=FREEBSD-LIBC
  http://www.rootr.net/man/man/setproctitle/3
  """
  
  libc = ctypes.CDLL(ctypes.util.find_library("c"))
  nameBuffer = ctypes.create_string_buffer(len(processName)+1)
  nameBuffer.value = processName
  libc.setproctitle(ctypes.byref(nameBuffer))

