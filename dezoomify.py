#!/usr/bin/env python3
# coding=utf8

"""
TAKE A URL CONTAINING A PAGE CONTAINING A ZOOMIFY OBJECT, A ZOOMIFY BASE
DIRECTORY OR A LIST OF THESE, AND RECONSTRUCT THE FULL RESOLUTION IMAGE


====LICENSE=====================================================================

This software is licensed under the Expat License (also called the MIT license).
"""

import sys

if sys.version_info[0] < 3:
    sys.exit("ERR: This program requires Python 3 to run.")

import os
import re
import urllib.request, urllib.parse
import argparse
import tempfile, shutil
import subprocess
import queue
import threading
import logging

from math import ceil, floor


def main():

    parser = argparse.ArgumentParser() #usage='Usage: %(prog)s <source> <output file> [options]'
    parser.add_argument('url', action='store',
                             help='the URL of a page containing a Zoomify object (unless -b or -l flags are set)')
    parser.add_argument('out', action='store',
                             help='the output file for the image' )
    parser.add_argument('-b', dest='base', action='store_true', default=False,
                             help='the URL is the base directory for the Zoomify tile structure' )
    parser.add_argument('-l', dest='list', action='store_true', default=False,
                             help='the URL refers to a local file containing a list of URLs or base directories to dezoomify. The output directory and default filename are derived from the -o value. The list format is "<url> [filename]". Extensions are added automatically to the filename, if they are missing.' )
    parser.add_argument('-v', '--verbose', dest='verbose', action='count', default=0,
                             help="increase verbosity (specify multiple times for more)")
    parser.add_argument('-z', dest='zoomLevel', action='store', default=False,
                             help='zoomlevel to grab image at (can be useful if some of a higher zoomlevel is corrupted or missing)' )
    parser.add_argument('-s', dest='store', action='store_true', default=False,
                             help='save all tiles in the local folder instead of the system temporary directory' )
    parser.add_argument('-j', dest='jpegtran', action='store',
                             help='location of jpegtran executable (assumed to be in the same directory as this script by default)' )
    parser.add_argument('-x', dest='nodownload', action='store_true', default=False,
                             help='create the image from previously downloaded files stored with -s (can be useful when an error occurred during tile joining)' )
    parser.add_argument('-t', dest='nthreads', action='store', default=16,
                             help='how many downloads will be made in parallel (default: 16)' )
    parser.add_argument('-p', dest='protocol', action='store', default='zoomify',
                             help='which image tiler protocol to use (options: zoomify. Default: zoomify)' )

    args = parser.parse_args()
    UntilerDezoomify(args)

def urlConcat(url1, url2):
    """simple concatenation routine for parts of urls

    Keyword arguments:
    url1 -- the first part of the url to join
    url2 -- the second part
    """

    if url1[-1] == '/':
        url1 = url1[0:-1]

    if url2[0] == '/':
        url2 = url2[1:]

    return url1 + '/' + url2

def getUrl(url):
    """
    getUrl accepts a URL string and return the server response code,
    response headers, and contents of the file

    Keyword arguments:
    url -- the url to fetch
    """

    # Escape the path part of the URL so spaces in it would not confuse the server.
    scheme, netloc, path, qs, anchor = urllib.parse.urlsplit(url)
    path = urllib.parse.quote(path, '/%')
    qs = urllib.parse.quote_plus(qs, ':&=')
    url = urllib.parse.urlunsplit((scheme, netloc, path, qs, anchor))

    # spoof the user-agent and referrer, in case that matters.
    req_headers = {
        'User-Agent': 'Mozilla/5.0 (Windows; U; Windows NT 5.1; en-US) AppleWebKit/525.13 (KHTML, like Gecko) Chrome/0.A.B.C Safari/525.13',
        'Referer': 'http://google.com'
        }

    request = urllib.request.Request(url, headers=req_headers) # create a request object for the URL
    opener = urllib.request.build_opener() # create an opener object
    response = opener.open(request) # open a connection and receive the http response headers + contents

    code = response.code
    headers = response.headers # headers object
    contents = response.read() # contents of the URL (HTML, javascript, css, img, etc.)
    return code, headers, contents

def downloadUrl(url, destination):
    """
    Copy a network object denoted by a URL to a local file.
    """
    with open(destination, 'wb') as f:
        f.write(getUrl(url)[2])
    
class ImageUntiler():

    def getImage(self, imageDir, outputDestination):

        def newDownloadThread():
            t = threading.Thread(target=downloader)
            t.daemon = True
            t.start()

        def downloader():
            if downloadQueue.empty():
                return
            url, col, row = downloadQueue.get()
            destination = localTileName(col, row)
            self.log.info("Downloading tile: " + destination)

            try:
                downloadUrl(url, destination)
                joinQueue.put((col, row))
            except ValueError:
                self.log.warning("Tile (row {}, col {}) does not exist on the server (URL: {})".format(row, col, url))

            if not downloadQueue.empty():
                newDownloadThread()

        def localTileName(col, row):
            return os.path.join(self.tileDir, "{}_{}.{}".format(col, row, self.ext))

        downloadQueue = queue.Queue()
        joinQueue = queue.Queue()
        for col in range(self.xTiles):
            for row in range(self.yTiles):
                if self.nodownload:
                    joinQueue.put((col, row))
                    continue
                url = self.getImageTileURL(col, row)
                downloadQueue.put((url, col, row))

        # start predetermined number of downloader threads
        for i in range(self.nthreads):
            newDownloadThread()

        # do tile joining in parallel with the downloading
        # use two temporary files for the joining process
        tmpimgs = [None, None]
        for i in range(2):
            fhandle = tempfile.NamedTemporaryFile(suffix='.jpg', delete=False)
            tmpimgs[i] = fhandle.name
            fhandle.close()
            self.log.info("Created temporary image file: " + tmpimgs[i])
        activeTmp = 0 # the index of current temp image to be used for input, toggles between 0 and 1

        numJoined = 0
        while threading.active_count() > 1 or not joinQueue.empty():
            col, row = joinQueue.get(block=True)

            # as the very first step create an (almost) empty image with the target dimensions using jpegtran
            if numJoined == 0:
                cmd = [self.jpegtran, '-copy', 'all', '-crop', '%dx%d+0+0' % (self.width, self.height), '-outfile', tmpimgs[activeTmp], localTileName(col, row)]
                subprocess.call(cmd)

            self.log.info("Adding tile (row {:3}, col {:3}) to the image".format(row, col))
            cmd = [self.jpegtran, '-copy', 'all', '-drop', '+%d+%d' % (col*self.tileSize, row*self.tileSize), localTileName(col, row), '-outfile', tmpimgs[(activeTmp+1)%2], tmpimgs[activeTmp]]
            subprocess.call(cmd)

            activeTmp = (activeTmp+1) % 2 # toggle between the two temp images
            numJoined += 1
        
        numMissing = self.xTiles * self.yTiles - numJoined
        if numMissing != 0:
            self.log.warning( "Image is missing {0} tile{1}. "
                   "You might want to download the image at a different zoom level (currently {2}) to get the missing part{1}."
                   .format(numMissing, '' if numMissing == 1 else 's', self.zoomLevel) )

        # make a final optimization pass and save the image to the output file
        cmd = [self.jpegtran, '-copy', 'all', '-optimize', '-outfile', outputDestination, tmpimgs[activeTmp]]
        subprocess.call(cmd)

        # delete the temporary images
        os.unlink(tmpimgs[0])
        os.unlink(tmpimgs[1])


    def getUrlList(self, args): #returns a list of base URLs for the given Dezoomify object(s)

        if not args.list: #if we are dealing with a single object
            self.imageDirs = [ args.url ]
            self.outNames = [ self.out ]

        else: #if we are dealing with a file with a list of objects
            listFile = open( args.url, 'r')
            self.imageDirs = [] #empty list of directories
            self.outNames = []

            i = 1
            for line in listFile:
                line = line.strip().split(' ', 1)

                if len(line) == 1:
                    root, ext = os.path.splitext(self.out)
                    self.outNames.append(root + '%03d' % i + ext)
                    i += 1
                elif len(line) == 2:
                    # allow filenames to lack extensions
                    m = re.search('\\.' + self.ext + '$', line[1])
                    if not m:
                        line[1] += '.' + self.ext
                    self.outNames.append(os.path.join(os.path.dirname(self.out), line[1]))
                else:
                    continue

                self.imageDirs.append( line[0] )


    def setupDirectory(self, destination):
        # if we will save the tiles, set up the directory to save in
        # create a temporary directory otherwise
        if self.store:
            root, ext = os.path.splitext(destination)

            if not os.path.exists(root):
                self.log.info("Creating image storage directory: %s" % root)
                os.makedirs(root)
            self.tileDir = root
        else:
            self.tileDir = tempfile.mkdtemp(prefix='dezoomify_')
            self.log.info("Created temporary image storage directory: %s" % self.tileDir)


    def __init__(self, args):
        self.verbose = int(args.verbose)
        self.ext = 'jpg'
        self.store = args.store
        self.out = args.out
        self.jpegtran = args.jpegtran
        self.nodownload = args.nodownload
        self.nthreads = int(args.nthreads)

        if self.nodownload:
            self.store = True
        
        # Set up logging.
        log_level = logging.WARNING # default
        if args.verbose == 1:
            log_level = logging.INFO
        elif args.verbose >= 2:
            log_level = logging.DEBUG
        
        logging.basicConfig(level=log_level, format='%(levelname)s: %(message)s')
        self.log = logging.getLogger(__name__)


        if (self.jpegtran is None): #we need to locate jpegtran
            mod_dir = os.path.dirname(__file__) #location of this script
            jpegtran = os.path.join( mod_dir, 'jpegtran' )

            if os.path.exists(jpegtran):
                self.jpegtran = jpegtran
            else:
                self.log.error("No jpegtran given, and no file found at %s" % jpegtran)


        self.getUrlList(args)

        for i, imageDir in enumerate(self.imageDirs):
            destination = self.outNames[i]

            if not args.base:
                self.imageDir = self.getImageDirectory(imageDir)  # locate the base directory of the zoomify tile images
            else:
                self.imageDir = imageDir

            self.getProperties(self.imageDir, args.zoomLevel)       # inspect the ImageProperties.xml file to get properties, and derive the rest

            self.setupDirectory(destination) # create the directory where the tiles are stored

            self.getImage(self.imageDir, destination) # download and join tiles to create the dezoomified file

            self.log.info("Dezoomifed image created and saved to " + destination)

            if not self.store:
                shutil.rmtree(self.tileDir)
                self.log.info("Erased the temporary directory and its contents")

class UntilerDezoomify(ImageUntiler):

    def getTileIndex(self, level, x, y):
        """
        Get the zoomify index of a tile in a given level, at given co-ordinates
        This is needed to get the tilegroup.

        Keyword arguments:
        level -- the zoomlevel of the tile
        x,y -- the co-ordinates of the tile in that level

        Returns -- the zoomify index
        """

        index = x + y * int(ceil( floor(self.width/pow(2, self.maxZoom - level - 1)) / self.tileSize ) )

        for i in range(1, level+1):
            index += int(ceil( floor(self.width /pow(2, self.maxZoom - i)) / self.tileSize ) ) * \
                     int(ceil( floor(self.height/pow(2, self.maxZoom - i)) / self.tileSize ) )

        return index

    def getMaxZoom(self):
        """Construct a list of all zoomlevels with sizes in tiles"""

        width = int(ceil(self.maxWidth/float(self.tileSize))) #width of full image in tiles
        height = int(ceil(self.maxHeight/float(self.tileSize))) #height

        self.levels = []

        while True:

            self.levels.append((width, height))

            if width == 1 and height == 1:
                break
            else:
                width = int(ceil(width/2.0)) #each zoom level is a factor of two smaller
                height = int(ceil(height/2.0))

        self.levels.reverse() # make the 0th level the smallestt zoom, and higher levels, higher zoom

    def getImageDirectory(self, url):
        """
        Gets the Zoomify image base directory for the image tiles. This function
        is called if the user does NOT supply a base directory explicitly. It works
        by parsing the HTML code of the given page and looking for
        zoomifyImagePath=....

        Keyword arguments
        url -- The URL of the page to look for the base directory on
        """

        try:
            content = getUrl(url)[2].decode(errors='ignore')
        except Exception:
            self.log.error("Specified directory not found. Check the URL.\nException: %s " % sys.exc_info()[1])
            sys.exit()

        imagePath = None
        m = re.search('zoomifyImagePath=([^\'"&]*)[\'"&]', content)
        if m:
            imagePath = m.group(1)

        if not imagePath:
            m = re.search('ZoomifyCache/[^\'"&.]+\\.\\d+x\\d+', content)
            if m:
                imagePath = m.group(0)

        # For HTML5 Zoomify.
        if not imagePath:
            m = re.search('(["\'])([^"]+)/TileGroup0[^"]*\\1', content)
            if m:
                imagePath = m.group(2)
        
        # Another JavaScript/HTML5 Zoomify version (v1.8).
        if not imagePath:
            m = re.search('showImage\([^,]+, (["\'])([^"\']+)\\1', content)
            if m:
                imagePath = m.group(2)

        if not imagePath:
            self.log.error("Source directory not found. Ensure the given URL contains a Zoomify object.")
            sys.exit()
        else:
            self.log.info("Found zoomifyImagePath: %s" % imagePath)

            netloc   = urllib.parse.urlparse(imagePath).netloc
            if not netloc: #the given zoomifyPath is relative from the base url

                #split the given url into parts
                parsedURL = urllib.parse.urlparse(url)

                # remove the last bit of path, if it has a "." (i.e. it is a file, not a directory)
                pathParts = parsedURL.path.split('/')
                m = re.search('\.', pathParts[-1])
                if m:
                    del(pathParts[-1])
                path = '/'.join(pathParts)

                # reconstruct the url with the new path, and without queries, params and fragments
                url = urllib.parse.urlunparse([ parsedURL.scheme, parsedURL.netloc, path, None, None, None])

                imageDir = urlConcat(url, imagePath) #add the relative url to the current url

            else: #the zoomify path is absolute
                imageDir = imagePath

            self.log.info("Found image directory: " + imageDir)
            return imageDir

    def getProperties(self, imageDir, zoomLevel):
        """
        Retreive the XML properties file and extract the needed information.

        Sets the relevant variables for the grabbing phase.

        Keyword arguments
        imageDir -- the Zoomify base directory
        zoomLevel -- the level which we want to get
        """

        #READ THE XML FILE AND RETRIEVE THE ZOOMIFY PROPERTIES NEEDED TO RECONSTRUCT (WIDTH, HEIGHT AND TILESIZE)
        xmlUrl = imageDir + '/ImageProperties.xml' #this file contains information about the image tiles

        self.log.info("xmlUrl=" + xmlUrl)
        code, headers, content = getUrl(xmlUrl) #get the file's contents
        content = content.decode(errors='ignore')
        #example: <IMAGE_PROPERTIES WIDTH="2679" HEIGHT="4000" NUMTILES="241" NUMIMAGES="1" VERSION="1.8" TILESIZE="256"/>

        m = re.search('WIDTH="(\d+)"', content)
        if m:
            self.maxWidth = int(m.group(1))
        else:
            self.log.error("Width not found in ImageProperties.xml")
            sys.exit()

        m = re.search('HEIGHT="(\d+)"', content)
        if m:
            self.maxHeight = int(m.group(1))
        else:
            self.log.error("Height not found in ImageProperties.xml")
            sys.exit()

        m = re.search('TILESIZE="(\d+)"', content)
        if m:
            self.tileSize = int(m.group(1))
        else:
            self.log.error("Tile size not found in ImageProperties.xml")
            sys.exit()

        #PROCESS PROPERTIES TO GET ADDITIONAL DERIVABLE PROPERTIES

        self.getMaxZoom() #get one-indexed maximum zoom level

        self.maxZoom = len(self.levels)

        #GET THE REQUESTED ZOOMLEVEL
        if not zoomLevel: # none requested, using maximum
            self.zoomLevel = self.maxZoom-1
        else:
            zoomLevel = int(zoomLevel)
            if zoomLevel < self.maxZoom and zoomLevel >= 0:
                self.zoomLevel = zoomLevel
            else:
                self.zoomLevel = self.maxZoom-1
                self.log.warning("The requested zoom level is not available, defaulting to maximum (%d)" % self.zoomLevel)

        #GET THE SIZE AT THE RQUESTED ZOOM LEVEL
        self.width = self.maxWidth / 2**(self.maxZoom - self.zoomLevel - 1)
        self.height = self.maxHeight / 2**(self.maxZoom - self.zoomLevel - 1)

        #GET THE NUMBER OF TILES AT THE REQUESTED ZOOM LEVEL
        self.maxxTiles = self.levels[-1][0]
        self.maxyTiles = self.levels[-1][1]

        self.xTiles = self.levels[self.zoomLevel][0]
        self.yTiles = self.levels[self.zoomLevel][1]

        self.log.info( '\tMax zoom level:    %d (working zoom level: %d)' % (self.maxZoom-1, self.zoomLevel) )
        self.log.info( '\tWidth (overall):   %d (at given zoom level: %d)' % (self.maxWidth, self.width) )
        self.log.info( '\tHeight (overall):  %d (at given zoom level: %d)' % (self.maxHeight, self.height) )
        self.log.info( '\tTile size:         %d' % self.tileSize )
        self.log.info( '\tWidth (in tiles):  %d (at given level: %d)' % (self.maxxTiles, self.xTiles) )
        self.log.info( '\tHeight (in tiles): %d (at given level: %d)' % (self.maxyTiles, self.yTiles) )
        self.log.info( '\tTotal tiles:       %d (to be retreived: %d)' % (self.maxxTiles * self.maxyTiles, self.xTiles * self.yTiles) )
    
    def getImageTileURL(self, col, row):
        """
        Return the full URL of an image at a given position in the Zoomify structure.
        """
        tileIndex = self.getTileIndex(self.zoomLevel, col, row)
        tileGroup = tileIndex // self.tileSize
        url = '{}/TileGroup{}/{}-{}-{}.{}'.format(self.imageDir, tileGroup, self.zoomLevel, col, row, self.ext)
        return url

if __name__ == "__main__":
    try:
        main()
    finally:
        None
