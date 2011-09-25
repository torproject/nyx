"""
Interface for entries in the connection panel. These consist of two parts: the
entry itself (ie, Tor connection, client circuit, etc) and the lines it
consists of in the listing.
"""

from util import enum

# attributes we can list entries by
ListingType = enum.Enum(("IP_ADDRESS", "IP Address"), "HOSTNAME", "FINGERPRINT", "NICKNAME")

SortAttr = enum.Enum("CATEGORY", "UPTIME", "LISTING", "IP_ADDRESS", "PORT",
                     "HOSTNAME", "FINGERPRINT", "NICKNAME", "COUNTRY")

SORT_COLORS = {SortAttr.CATEGORY: "red",      SortAttr.UPTIME: "yellow",
               SortAttr.LISTING: "green",     SortAttr.IP_ADDRESS: "blue",
               SortAttr.PORT: "blue",         SortAttr.HOSTNAME: "magenta",
               SortAttr.FINGERPRINT: "cyan",  SortAttr.NICKNAME: "cyan",
               SortAttr.COUNTRY: "blue"}

# maximum number of ports a system can have
PORT_COUNT = 65536

class ConnectionPanelEntry:
  """
  Common parent for connection panel entries. This consists of a list of lines
  in the panel listing. This caches results until the display indicates that
  they should be flushed.
  """
  
  def __init__(self):
    self.lines = []
    self.flushCache = True
  
  def getLines(self):
    """
    Provides the individual lines in the connection listing.
    """
    
    if self.flushCache:
      self.lines = self._getLines(self.lines)
      self.flushCache = False
    
    return self.lines
  
  def _getLines(self, oldResults):
    # implementation of getLines
    
    for line in oldResults:
      line.resetDisplay()
    
    return oldResults
  
  def getSortValues(self, sortAttrs, listingType):
    """
    Provides the value used in comparisons to sort based on the given
    attribute.
    
    Arguments:
      sortAttrs   - list of SortAttr values for the field being sorted on
      listingType - ListingType enumeration for the attribute we're listing
                    entries by
    """
    
    return [self.getSortValue(attr, listingType) for attr in sortAttrs]
  
  def getSortValue(self, attr, listingType):
    """
    Provides the value of a single attribute used for sorting purposes.
    
    Arguments:
      attr        - list of SortAttr values for the field being sorted on
      listingType - ListingType enumeration for the attribute we're listing
                    entries by
    """
    
    if attr == SortAttr.LISTING:
      if listingType == ListingType.IP_ADDRESS:
        # uses the IP address as the primary value, and port as secondary
        sortValue = self.getSortValue(SortAttr.IP_ADDRESS, listingType) * PORT_COUNT
        sortValue += self.getSortValue(SortAttr.PORT, listingType)
        return sortValue
      elif listingType == ListingType.HOSTNAME:
        return self.getSortValue(SortAttr.HOSTNAME, listingType)
      elif listingType == ListingType.FINGERPRINT:
        return self.getSortValue(SortAttr.FINGERPRINT, listingType)
      elif listingType == ListingType.NICKNAME:
        return self.getSortValue(SortAttr.NICKNAME, listingType)
    
    return ""
  
  def resetDisplay(self):
    """
    Flushes cached display results.
    """
    
    self.flushCache = True

class ConnectionPanelLine:
  """
  Individual line in the connection panel listing.
  """
  
  def __init__(self):
    # cache for displayed information
    self._listingCache = None
    self._listingCacheArgs = (None, None)
    
    self._detailsCache = None
    self._detailsCacheArgs = None
    
    self._descriptorCache = None
    self._descriptorCacheArgs = None
  
  def getListingPrefix(self):
    """
    Provides a list of characters to be appended before the listing entry.
    """
    
    return ()
  
  def getListingEntry(self, width, currentTime, listingType):
    """
    Provides a [(msg, attr)...] tuple list for contents to be displayed in the
    connection panel listing.
    
    Arguments:
      width       - available space to display in
      currentTime - unix timestamp for what the results should consider to be
                    the current time (this may be ignored due to caching)
      listingType - ListingType enumeration for the highest priority content
                    to be displayed
    """
    
    if self._listingCacheArgs != (width, listingType):
      self._listingCache = self._getListingEntry(width, currentTime, listingType)
      self._listingCacheArgs = (width, listingType)
    
    return self._listingCache
  
  def _getListingEntry(self, width, currentTime, listingType):
    # implementation of getListingEntry
    return None
  
  def getDetails(self, width):
    """
    Provides a list of [(msg, attr)...] tuple listings with detailed
    information for this connection.
    
    Arguments:
      width - available space to display in
    """
    
    if self._detailsCacheArgs != width:
      self._detailsCache = self._getDetails(width)
      self._detailsCacheArgs = width
    
    return self._detailsCache
  
  def _getDetails(self, width):
    # implementation of getDetails
    return []
  
  def resetDisplay(self):
    """
    Flushes cached display results.
    """
    
    self._listingCacheArgs = (None, None)
    self._detailsCacheArgs = None

