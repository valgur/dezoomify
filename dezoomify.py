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

from math import ceil, floor
import argparse
import logging
import os
import re
import subprocess
import tempfile
import shutil
import urllib.error
import urllib.request
import urllib.parse
import platform
import itertools
from multiprocessing.pool import ThreadPool
from time import sleep

# Progressbar module is optional but recommended.
progressbar = None
try:
    import progressbar
except ImportError:
    pass

def main():
    parser = argparse.ArgumentParser()  # usage='Usage: %(prog)s <source> <output file> [options]'
    parser.add_argument('url', action='store',
                        help='the URL of a page containing a Zoomify object '
                        '(unless -b or -l flags are set)')
    parser.add_argument('out', action='store',
                        help='the output file for the image')
    parser.add_argument('-b', dest='base', action='store_true', default=False,
        help='the URL is the base directory for the Zoomify tile structure')
    parser.add_argument('-l', dest='list', action='store_true', default=False,
                        help='the URL refers to a local file containing a list of URLs '
                        'or base directories to dezoomify. The output directory and '
                        'default filenames are derived from the "out" parameter. The list format '
                        'is "<url> [filename]". Extensions are added automatically to the '
                        'filenames, if they are missing.')
    parser.add_argument('-v', dest='verbose', action='count', default=0,
                        help="increase verbosity (specify multiple times for more)")
    parser.add_argument('-z', dest='zoomLevel', action='store', default=False,
                        help='zoomlevel to grab image at (can be useful if some of a '
                        'higher zoomlevel is corrupted or missing)')
    parser.add_argument('-s', dest='store', action='store_true', default=False,
                        help='save all tiles in the local folder instead of the '
                        'system\'s temporary directory')
    parser.add_argument('-j', dest='jpegtran', action='store',
                        help='location of jpegtran executable (assumed to be in the '
                        'same directory as this script by default)')
    parser.add_argument('-x', dest='nodownload', action='store_true', default=False,
                        help='create the image from previously downloaded files stored '
                        'with -s (can be useful when an error occurred during tile joining)')
    parser.add_argument('-t', dest='nthreads', action='store', default=16,
                        help='how many downloads will be made in parallel (default: 16)')
    parser.add_argument('-p', dest='protocol', action='store', default='zoomify',
                        help='which image tiler protocol to use (options: zoomify. Default: zoomify)')
    args = parser.parse_args()
    UntilerDezoomify(args)


def openUrl(url):
    """
    Similar to urllib.request.urlopen,
    except some additional preparation is done on the URL and 
    the user-agent and referrer are spoofed.

    Keyword arguments:
    url -- the URL to open
    """

    # Escape the path part of the URL so spaces in it would not confuse the server.
    scheme, netloc, path, qs, anchor = urllib.parse.urlsplit(url)
    path = urllib.parse.quote(path, '/%')
    qs = urllib.parse.quote_plus(qs, ':&=')
    url = urllib.parse.urlunsplit((scheme, netloc, path, qs, anchor))

    # spoof the user-agent and referrer, in case that matters.
    req_headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 6.2; WOW64; rv:24.0) Gecko/20100101 Firefox/24.0',
        'Referer': 'http://google.com'
    }
    # create a request object for the URL
    request = urllib.request.Request(url, headers=req_headers)
    # create an opener object
    opener = urllib.request.build_opener()
    # open a connection and receive the http response headers + contents
    return opener.open(request)

def downloadUrl(url, destination):
    """
    Copy a network object denoted by a URL to a local file.
    """
    with openUrl(url) as response, open(destination, 'wb') as out_file:
        shutil.copyfileobj(response, out_file)


class ImageUntiler():
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
        log_level = logging.WARNING  # default
        if args.verbose == 1:
            log_level = logging.INFO
        elif args.verbose >= 2:
            log_level = logging.DEBUG

        logging.basicConfig(level=log_level, format='%(levelname)s: %(message)s')
        self.log = logging.getLogger(__name__)
        

        if self.jpegtran is None:  # we need to locate jpegtran
            mod_dir = os.path.dirname(__file__)  # location of this script
            if platform.system() == 'Windows':
                jpegtran = os.path.join(mod_dir, 'jpegtran.exe')
            else:
                jpegtran = os.path.join(mod_dir, 'jpegtran')

            if os.path.exists(jpegtran):
                self.jpegtran = jpegtran
            else:
                self.log.error("No jpegtran excecutable found at the script's directory. "
                               "Use -j option to set its location.")
                exit()

        if not os.path.exists(self.jpegtran):
            self.log.error("jpegtran excecutable not found. "
                           "Use -j option to set its location.")
            exit()
        elif not os.access(self.jpegtran, os.X_OK):
            self.log.error("{} does not have execute permission."
                           .format(self.jpegtran))
            exit()

        self.tileDir = None
        self.getUrlList(args.url, args.list)
        for i, imageUrl in enumerate(self.imageUrls):
            destination = self.outNames[i]
            if len(self.imageUrls) > 1:
                print("Processing image {} ({}/{})...".format(destination, i+1, len(self.imageUrls)))

            if not args.base:
                # locate the base directory of the zoomify tile images
                self.baseDir = self.getBaseDirectory(imageUrl)
            else:
                self.baseDir = imageUrl
                if self.baseDir.endswith('/ImageProperties.xml'):
                    self.baseDir = urllib.parse.urljoin(self.baseDir, '.')
                self.baseDir = self.baseDir.rstrip('/') + '/'

            try:
                # inspect the ImageProperties.xml file to get properties, and derive the rest
                self.getProperties(self.baseDir, args.zoomLevel)
    
                # create the directory where the tiles are stored
                self.setupDirectory(destination)
    
                # download and join tiles to create the dezoomified file
                self.getImage(destination)
            finally: 
                if not self.store and self.tileDir:
                    shutil.rmtree(self.tileDir)
                    self.log.info("Erased the temporary directory and its contents")

            self.log.info("Dezoomifed image created and saved to " + destination)

    def getImage(self, outputDestination):
        """
        Downloads image tiles and joins them.
        These processes are done in parallel.
        """
        numTiles = self.xTiles * self.yTiles
        numDownloaded = 0
        numJoined = 0
        
        # Progressbars for downloading and joining.
        if progressbar:
            downloadProgressbar = progressbar.ProgressBar( 
                widgets = ['Downloading tiles: ',
                           progressbar.Counter(), '/', str(numTiles), ' ',
                           progressbar.Bar('>', left='[', right=']'), ' ',
                           progressbar.ETA()],
                maxval = numTiles
            )
            downloadProgressbar.start()
        
            joiningProgressbar = progressbar.ProgressBar( 
                widgets = ['Joining tiles: ',
                           progressbar.Counter(), '/', str(numTiles), ' ',
                           progressbar.Bar('>', left='[', right=']'), ' ',
                           progressbar.ETA()],
                maxval = numTiles
            )
        
        def localTileName(col, row):
            return os.path.join(self.tileDir, "{}_{}.{}".format(col, row, self.ext))
        
        def download(tilePosition):
            col, row = tilePosition
            url = self.getImageTileURL(col, row)
            destination = localTileName(col, row)
            if not progressbar:
                self.log.info("Downloading tile (row {:3}, col {:3})".format(row, col))
            try:
                downloadUrl(url, destination)
            except urllib.error.HTTPError as e:
                self.log.warning(
                    "{}. Tile {} (row {}, col {}) does not exist on the server."
                    .format(e, url, row, col)
                )
                return (None, None)
            return tilePosition

        tilePositions = itertools.product(range(self.xTiles), range(self.yTiles))
        if not self.nodownload:
            pool = ThreadPool(processes=self.nthreads)
            downloadedIterator = pool.imap_unordered(download, tilePositions)
        else:
            downloadedIterator = tilePositions
            numDownloaded = numTiles

        # Do tile joining in parallel with the downloading.
        # Use two temporary files for the joining process.
        tmpimgs = [None, None]
        for i in range(2):
            fhandle = tempfile.NamedTemporaryFile(suffix='.jpg', dir=self.tileDir, delete=False)
            tmpimgs[i] = fhandle.name
            fhandle.close()
            self.log.debug("Created temporary image file: " + tmpimgs[i])

        # The index of current temp image to be used for input, toggles between 0 and 1.
        activeTmp = 0

        # Join tiles into a single image in parallel to them being downloaded.
        try:
            subproc = None # Popen class of the most recently called subprocess.
            for i, (col, row) in enumerate(downloadedIterator):
                if col is None: continue # Tile failed to download.
                
                if not progressbar:
                    self.log.info("Adding tile (row {:3}, col {:3}) to the image".format(row, col))
                
                # As the very first step create an (almost) empty image with the target dimensions.
                if i == 0:
                    subproc = subprocess.Popen([self.jpegtran,
                        '-copy', 'all',
                        '-crop', '{:d}x{:d}+0+0'.format(self.width, self.height),
                        '-outfile', tmpimgs[activeTmp],
                        localTileName(col, row)
                    ])
                    subproc.wait()
                
                subproc = subprocess.Popen([self.jpegtran,
                    '-copy', 'all',
                    '-drop', '+{:d}+{:d}'.format(col * self.tileSize, row * self.tileSize), localTileName(col, row),
                    '-outfile', tmpimgs[(activeTmp + 1) % 2],
                    tmpimgs[activeTmp]
                ])
                subproc.wait()

                activeTmp = (activeTmp + 1) % 2  # toggle between the two temp images
                
                numJoined += 1
                if not self.nodownload:
                    numDownloaded = downloadedIterator._index
                if progressbar:
                    if numDownloaded < numTiles:
                        downloadProgressbar.update(numDownloaded)
                    elif not downloadProgressbar.finished:
                        downloadProgressbar.finish()
                        joiningProgressbar.start()
                    else:
                        joiningProgressbar.update(numJoined)

            # Make a final optimization pass and save the image to the output file.
            subproc = subprocess.Popen([self.jpegtran,
                '-copy', 'all',
                '-optimize',
                '-outfile', outputDestination,
                tmpimgs[activeTmp]
            ])
            subproc.wait()
            
            numMissing = numTiles - numJoined
            if numMissing > 0:
                self.log.warning(
                    "Image '{3}' is missing {0} tile{1}. "
                    "You might want to download the image at a different zoom level "
                    "(currently {2}) to get the missing part{1}."
                    .format(numMissing, '' if numMissing == 1 else 's', self.zoomLevel,
                            outputDestination)
                )
            if progressbar:
                joiningProgressbar.finish()

        except KeyboardInterrupt:
            # Kill the jpegtran subprocess.
            if subproc and subproc.poll() is None:
                subproc.kill()
            sleep(1) # Wait for the file handles to be released.
            raise
        finally:
            # Delete the temporary images.
            os.unlink(tmpimgs[0])
            os.unlink(tmpimgs[1])

    def getUrlList(self, url, use_list):
        """
        Return a list of URLs to process and their respective output file names.
        """
        if not use_list:  # if we are dealing with a single object
            self.imageUrls = [url]
            self.outNames = [self.out]

        else:  # if we are dealing with a file with a list of objects
            listFile = open(url, 'r')
            self.imageUrls = []  # empty list of directories
            self.outNames = []

            i = 1
            for line in listFile:
                line = line.strip().split('\t', 1)

                if len(line) == 1:
                    root, ext = os.path.splitext(self.out)
                    self.outNames.append("{}{:3d}{}".format(root, i, ext))
                    i += 1
                elif len(line) == 2:
                    # allow filenames to lack extensions
                    m = re.search('\\.' + self.ext + '$', line[1])
                    if not m:
                        line[1] += '.' + self.ext
                    self.outNames.append(os.path.join(os.path.dirname(self.out), line[1]))
                else:
                    continue

                self.imageUrls.append(line[0])

    def setupDirectory(self, destination):
        # if we will save the tiles, set up the directory to save in
        # create a temporary directory otherwise
        if self.store:
            root, ext = os.path.splitext(destination)

            if not os.path.exists(root):
                self.log.info("Creating image storage directory: {}".format(root))
                os.makedirs(root)
            self.tileDir = root
        else:
            self.tileDir = tempfile.mkdtemp(prefix='dezoomify_')
            self.log.info("Created temporary image storage directory: {}".format(self.tileDir))

            
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

        index = x + y * int(ceil(floor(self.width / pow(2, self.maxZoom - level - 1)) / self.tileSize))

        for i in range(1, level + 1):
            index += int(ceil(floor(self.width / pow(2, self.maxZoom - i)) / self.tileSize)) * \
                int(ceil(floor(self.height / pow(2, self.maxZoom - i)) / self.tileSize))

        return index

    def getZoomLevels(self):
        """Construct a list of all zoomlevels with sizes in tiles"""
        locWidth = self.maxWidth
        locHeight = self.maxHeight
        self.levels = []
        while True:
            widthInTiles = int(ceil(locWidth / float(self.tileSize)))
            heightInTiles = int(ceil(locHeight / float(self.tileSize)))
            self.levels.append((widthInTiles, heightInTiles))
            
            if widthInTiles == 1 and heightInTiles == 1:
                break
            
            locWidth = int(locWidth / 2.)
            locHeight = int(locHeight / 2.)

        # make the 0th level the smallest zoom, and higher levels, higher zoom
        self.levels.reverse()
        self.log.debug("self.levels = {}".format(self.levels))

    def getBaseDirectory(self, url):
        """
        Gets the Zoomify image base directory for the image tiles. This function
        is called if the user does NOT supply a base directory explicitly. It works
        by parsing the HTML code of the given page and looking for
        zoomifyImagePath=....

        Keyword arguments
        url -- The URL of the page to look for the base directory on
        """

        try:
            with openUrl(url) as handle:
                content = handle.read().decode(errors='ignore')
        except Exception:
            self.log.error(
                "Specified directory not found ({}).\n"
                "Check the URL: {}"
                .format(sys.exc_info()[1], url)
            )
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
            
        self.log.info("Found zoomifyImagePath: {}".format(imagePath))
        
        imagePath = urllib.parse.unquote(imagePath)
        baseDir = urllib.parse.urljoin(url, imagePath)
        baseDir = baseDir.rstrip('/') + '/'
        return baseDir

    def getProperties(self, baseDir, zoomLevel):
        """
        Retrieve the XML properties file and extract the needed information.

        Sets the relevant variables for the grabbing phase.

        Keyword arguments
        baseDir -- the Zoomify base directory
        zoomLevel -- the level which we want to get
        """

        # READ THE XML FILE AND RETRIEVE THE ZOOMIFY PROPERTIES
        # NEEDED TO RECONSTRUCT (WIDTH, HEIGHT AND TILESIZE)

        # this file contains information about the image tiles
        xmlUrl = urllib.parse.urljoin(baseDir, 'ImageProperties.xml')

        self.log.info("xmlUrl=" + xmlUrl)
        content = None
        try:
            with openUrl(xmlUrl) as handle:
                content = handle.read().decode(errors='ignore')
        except Exception:
            self.log.error(
                "Could not open ImageProperties.xml ({}).\n"
                "URL: {}"
                .format(sys.exc_info()[1], xmlUrl)
            )
            sys.exit()

        # example: <IMAGE_PROPERTIES WIDTH="2679" HEIGHT="4000" NUMTILES="241" NUMIMAGES="1" VERSION="1.8" TILESIZE="256"/>
        properties = dict(re.findall(r"\b(\w+)\s*=\s*[\"']([^\"']*)[\"']", content))
        self.maxWidth = int(properties["WIDTH"])
        self.maxHeight = int(properties["HEIGHT"])
        self.tileSize = int(properties["TILESIZE"])

        # PROCESS PROPERTIES TO GET ADDITIONAL DERIVABLE PROPERTIES

        self.getZoomLevels()  # get one-indexed maximum zoom level
        self.maxZoom = len(self.levels)

        # GET THE REQUESTED ZOOMLEVEL
        if not zoomLevel:  # none requested, using maximum
            self.zoomLevel = self.maxZoom - 1
        else:
            zoomLevel = int(zoomLevel)
            if zoomLevel < self.maxZoom and zoomLevel >= 0:
                self.zoomLevel = zoomLevel
            else:
                self.zoomLevel = self.maxZoom - 1
                self.log.warning(
                    "The requested zoom level is not available, "
                    "defaulting to maximum ({:d})".format(self.zoomLevel)
                )

        # GET THE SIZE AT THE RQUESTED ZOOM LEVEL
        self.width = int(self.maxWidth / 2 ** (self.maxZoom - self.zoomLevel - 1))
        self.height = int(self.maxHeight / 2 ** (self.maxZoom - self.zoomLevel - 1))

        # GET THE NUMBER OF TILES AT THE REQUESTED ZOOM LEVEL
        self.maxxTiles = self.levels[-1][0]
        self.maxyTiles = self.levels[-1][1]

        self.xTiles = self.levels[self.zoomLevel][0]
        self.yTiles = self.levels[self.zoomLevel][1]

        self.log.info('\tMax zoom level:    {:d} (working zoom level: {:d})'.format(self.maxZoom - 1, self.zoomLevel))
        self.log.info('\tWidth (overall):   {:d} (at given zoom level: {:d})'.format(self.maxWidth, self.width))
        self.log.info('\tHeight (overall):  {:d} (at given zoom level: {:d})'.format(self.maxHeight, self.height))
        self.log.info('\tTile size:         {:d}'.format(self.tileSize))
        self.log.info('\tWidth (in tiles):  {:d} (at given level: {:d})'.format(self.maxxTiles, self.xTiles))
        self.log.info('\tHeight (in tiles): {:d} (at given level: {:d})'.format(self.maxyTiles, self.yTiles))
        self.log.info('\tTotal tiles:       {:d} (to be retrieved: {:d})'.format(self.maxxTiles * self.maxyTiles,
                                                                         self.xTiles * self.yTiles))

    def getImageTileURL(self, col, row):
        """
        Return the full URL of an image at a given position in the Zoomify structure.
        """
        tileIndex = self.getTileIndex(self.zoomLevel, col, row)
        tileGroup = tileIndex // self.tileSize
        url = self.baseDir + 'TileGroup{}/{}-{}-{}.{}'.format(tileGroup, self.zoomLevel, col, row, self.ext)
        return url

if __name__ == "__main__":
    main()
