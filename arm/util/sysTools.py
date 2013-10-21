"""
Helper functions for working with the underlying system.
"""

import time

# Runtimes for system calls, used to estimate cpu usage. Entries are tuples of
# the form:
# (time called, runtime)
RUNTIMES = []
SAMPLING_PERIOD = 5 # time of the sampling period

def getSysCpuUsage():
  """
  Provides an estimate of the cpu usage for system calls made through this
  module, based on a sampling period of five seconds. The os.times() function,
  unfortunately, doesn't seem to take popen calls into account. This returns a
  float representing the percentage used.
  """

  currentTime = time.time()

  # removes any runtimes outside of our sampling period
  while RUNTIMES and currentTime - RUNTIMES[0][0] > SAMPLING_PERIOD:
    RUNTIMES.pop(0)

  runtimeSum = sum([entry[1] for entry in RUNTIMES])
  return runtimeSum / SAMPLING_PERIOD

