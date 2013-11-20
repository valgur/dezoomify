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
    parser.add_argument('-z', dest='zoom_level', action='store', default=False,
                        help='zoom level to grab image at (can be useful if some of a '
                        'higher zoom level is corrupted or missing)')
    parser.add_argument('-s', dest='store', action='store_true', default=False,
                        help='save all tiles in the local folder instead of the '
                        'system\'s temporary directory')
    parser.add_argument('-j', dest='jpegtran', action='store',
                        help='location of jpegtran executable (assumed to be in the '
                        'same directory as this script by default)')
    parser.add_argument('-x', dest='no_download', action='store_true', default=False,
                        help='create the image from previously downloaded files stored '
                        'with -s (can be useful when an error occurred during tile joining)')
    parser.add_argument('-t', dest='nthreads', action='store', default=16,
                        help='how many downloads will be made in parallel (default: 16)')
    parser.add_argument('-p', dest='protocol', action='store', default='zoomify',
                        help='which image tiler protocol to use (options: zoomify. Default: zoomify)')
    args = parser.parse_args()
    UntilerDezoomify(args)


def open_url(url):
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

def download_url(url, destination):
    """
    Copy a network object denoted by a URL to a local file.
    """
    with open_url(url) as response, open(destination, 'wb') as out_file:
        shutil.copyfileobj(response, out_file)


class ImageUntiler():
    def __init__(self, args):
        self.verbose = int(args.verbose)
        self.ext = 'jpg'
        self.store = args.store
        self.out = args.out
        self.jpegtran = args.jpegtran
        self.no_download = args.no_download
        self.nthreads = int(args.nthreads)

        if self.no_download:
            self.store = True

        # Set up logging.
        log_level = logging.WARNING  # default
        if args.verbose == 1:
            log_level = logging.INFO
        elif args.verbose >= 2:
            log_level = logging.DEBUG
        logging.basicConfig(level=log_level, format='%(levelname)s: %(message)s')
        self.log = logging.getLogger(__name__)

        # Set up jpegtran.
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

        # Check that jpegtran exists and has the lossless drop feature.
        if not os.path.exists(self.jpegtran):
            self.log.error("jpegtran excecutable not found. "
                           "Use -j option to set its location.")
            exit()
        elif not os.access(self.jpegtran, os.X_OK):
            self.log.error("{} does not have execute permission."
                           .format(self.jpegtran))
            exit()
        
        #This might not work on Windows (different parameters?)
        subproc = subprocess.Popen([self.jpegtran, "--nonsense"], stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        
        try:
            jpegtran_help_info = str(subproc.communicate(timeout=5))
            if '-drop' not in jpegtran_help_info:
                self.log.error("{} does not have the '-drop' feature. "
                "Either use the jpegtran supplied with Dezoomify or get it from "
                "http://jpegclub.org/jpegtran/ section \"3. Lossless crop 'n' drop (cut & paste)\" to fix the problem."
                .format(self.jpegtran))
                subproc.kill()
                exit()
        except Exception:
            subproc.kill()
            print("Communication with JpegTran has failed and the process was killed.")
            exit()

        self.tile_dir = None
        self.get_url_list(args.url, args.list)
        for i, image_url in enumerate(self.image_urls):
            destination = self.out_names[i]
            if len(self.image_urls) > 1:
                print("Processing image {} ({}/{})...".format(destination, i+1, len(self.image_urls)))

            if not args.base:
                # locate the base directory of the zoomify tile images
                self.base_dir = self.get_base_directory(image_url)
            else:
                self.base_dir = image_url
                if self.base_dir.endswith('/ImageProperties.xml'):
                    self.base_dir = urllib.parse.urljoin(self.base_dir, '.')
                self.base_dir = self.base_dir.rstrip('/') + '/'

            try:
                # inspect the ImageProperties.xml file to get properties, and derive the rest
                self.get_properties(self.base_dir, args.zoom_level)

                # create the directory where the tiles are stored
                self.setup_tile_directory(self.store, destination)

                # download and join tiles to create the dezoomified file
                self.untile_image(destination)
            finally:
                if not self.store and self.tile_dir:
                    shutil.rmtree(self.tile_dir)
                    self.log.info("Erased the temporary directory and its contents")

            self.log.info("Dezoomifed image created and saved to " + destination)

    def untile_image(self, output_destination):
        """
        Downloads image tiles and joins them.
        These processes are done in parallel.
        """
        num_tiles = self.x_tiles * self.y_tiles
        num_downloaded = 0
        num_joined = 0

        # Progressbars for downloading and joining.
        if progressbar:
            download_progressbar = progressbar.ProgressBar(
                widgets = ['Downloading tiles: ',
                           progressbar.Counter(), '/', str(num_tiles), ' ',
                           progressbar.Bar('>', left='[', right=']'), ' ',
                           progressbar.ETA()],
                maxval = num_tiles
            )
            download_progressbar.start()

            joining_progressbar = progressbar.ProgressBar(
                widgets = ['Joining tiles: ',
                           progressbar.Counter(), '/', str(num_tiles), ' ',
                           progressbar.Bar('>', left='[', right=']'), ' ',
                           progressbar.ETA()],
                maxval = num_tiles
            )

        def local_tile_path(col, row):
            return os.path.join(self.tile_dir, "{}_{}.{}".format(col, row, self.ext))

        def download(tile_position):
            col, row = tile_position
            url = self.get_tile_url(col, row)
            destination = local_tile_path(col, row)
            if not progressbar:
                self.log.info("Downloading tile (row {:3}, col {:3})".format(row, col))
            try:
                download_url(url, destination)
            except urllib.error.HTTPError as e:
                self.log.warning(
                    "{}. Tile {} (row {}, col {}) does not exist on the server."
                    .format(e, url, row, col)
                )
                return (None, None)
            return tile_position

        # Download tiles in self.nthreads parallel threads.
        tile_positions = itertools.product(range(self.x_tiles), range(self.y_tiles))
        if not self.no_download:
            pool = ThreadPool(processes=self.nthreads)
            downloaded_iterator = pool.imap_unordered(download, tile_positions)
        else:
            downloaded_iterator = tile_positions
            num_downloaded = num_tiles

        # Do tile joining in parallel with the downloading.
        # Use two temporary files for the joining process.
        tmpimgs = [None, None]
        for i in range(2):
            fhandle = tempfile.NamedTemporaryFile(suffix='.jpg', dir=self.tile_dir, delete=False)
            tmpimgs[i] = fhandle.name
            fhandle.close()
            self.log.debug("Created temporary image file: " + tmpimgs[i])

        # The index of current temp image to be used for input, toggles between 0 and 1.
        active_tmp = 0

        # Join tiles into a single image in parallel to them being downloaded.
        try:
            subproc = None # Popen class of the most recently called subprocess.
            for i, (col, row) in enumerate(downloaded_iterator):
                if col is None: continue # Tile failed to download.

                if not progressbar:
                    self.log.info("Adding tile (row {:3}, col {:3}) to the image".format(row, col))

                # As the very first step create an (almost) empty image with the target dimensions.
                if i == 0:
                    subproc = subprocess.Popen([self.jpegtran,
                        '-copy', 'all',
                        '-crop', '{:d}x{:d}+0+0'.format(self.width, self.height),
                        '-outfile', tmpimgs[active_tmp],
                        local_tile_path(col, row)
                    ])
                    subproc.wait()

                subproc = subprocess.Popen([self.jpegtran,
                    '-copy', 'all',
                    '-drop', '+{:d}+{:d}'.format(col * self.tile_size, row * self.tile_size), local_tile_path(col, row),
                    '-outfile', tmpimgs[(active_tmp + 1) % 2],
                    tmpimgs[active_tmp]
                ])
                subproc.wait()

                active_tmp = (active_tmp + 1) % 2  # toggle between the two temp images

                num_joined += 1
                if not self.no_download:
                    num_downloaded = downloaded_iterator._index
                if progressbar:
                    if num_downloaded < num_tiles:
                        download_progressbar.update(num_downloaded)
                    elif not download_progressbar.finished:
                        download_progressbar.finish()
                        joining_progressbar.start()
                    else:
                        joining_progressbar.update(num_joined)

            # Make a final optimization pass and save the image to the output file.
            subproc = subprocess.Popen([self.jpegtran,
                '-copy', 'all',
                '-optimize',
                '-outfile', output_destination,
                tmpimgs[active_tmp]
            ])
            subproc.wait()

            num_missing = num_tiles - num_joined
            if num_missing > 0:
                self.log.warning(
                    "Image '{3}' is missing {0} tile{1}. "
                    "You might want to download the image at a different zoom level "
                    "(currently {2}) to get the missing part{1}."
                    .format(num_missing, '' if num_missing == 1 else 's', self.zoom_level,
                            output_destination)
                )
            if progressbar:
                joining_progressbar.finish()

        except KeyboardInterrupt:
            # Kill the jpegtran subprocess.
            if subproc and subproc.poll() is None:
                subproc.kill()
            raise
        finally:
            # Delete the temporary images.
            os.unlink(tmpimgs[0])
            os.unlink(tmpimgs[1])

    def get_url_list(self, url, use_list):
        """
        Return a list of URLs to process and their respective output file names.
        """
        if not use_list:  # if we are dealing with a single object
            self.image_urls = [url]
            self.out_names = [self.out]

        else:  # if we are dealing with a file with a list of objects
            list_file = open(url, 'r')
            self.image_urls = []  # empty list of directories
            self.out_names = []

            i = 1
            for line in list_file:
                line = line.strip().split('\t', 1)

                if len(line) == 1:
                    root, ext = os.path.splitext(self.out)
                    self.out_names.append("{}{:3d}{}".format(root, i, ext))
                    i += 1
                elif len(line) == 2:
                    # allow filenames to lack extensions
                    m = re.search('\\.' + self.ext + '$', line[1])
                    if not m:
                        line[1] += '.' + self.ext
                    self.out_names.append(os.path.join(os.path.dirname(self.out), line[1]))
                else:
                    continue

                self.image_urls.append(line[0])

    def setup_tile_directory(self, in_local_dir, output_file_name=None):
        """
        Create the directory in which tile downloading & joining takes place.

        in_local_dir -- whether the directory should be placed in the working directory,
            will be created in the system's temp directory otherwise
        output_file_name -- the path of the final dezoomified image,
            used to derive the local directory's location
        """
        if in_local_dir:
            root, ext = os.path.splitext(output_file_name)

            if not os.path.exists(root):
                self.log.info("Creating image storage directory: {}".format(root))
                os.makedirs(root)
            self.tile_dir = root
        else:
            self.tile_dir = tempfile.mkdtemp(prefix='dezoomify_')
            self.log.info("Created temporary image storage directory: {}".format(self.tile_dir))


class UntilerDezoomify(ImageUntiler):
    def get_base_directory(self, url):
        """
        Gets the Zoomify image base directory for the image tiles. This function
        is called if the user does NOT supply a base directory explicitly. It works
        by parsing the HTML code of the given page and looking for
        zoomifyImagePath=....

        Keyword arguments
        url -- The URL of the page to look for the base directory on
        """

        try:
            with open_url(url) as handle:
                content = handle.read().decode(errors='ignore')
        except Exception:
            self.log.error(
                "Specified directory not found ({}).\n"
                "Check the URL: {}"
                .format(sys.exc_info()[1], url)
            )
            sys.exit()

        image_path = None
        image_path_regexes = [
            ('zoomifyImagePath=([^\'"&]*)[\'"&]', 1),
            ('ZoomifyCache/[^\'"&.]+\\.\\d+x\\d+', 0),
            # For HTML5 Zoomify.
            ('(["\'])([^"\']+)/TileGroup0[^"\']*\\1', 2),
            # Another JavaScript/HTML5 Zoomify version (v1.8).
            ('showImage\\([^,]+, *(["\'])([^"\']+)\\1', 2)]
        for rx, group in image_path_regexes:
            m = re.search(rx, content)
            if m:
                image_path = m.group(group)
                break

        if not image_path:
            self.log.error("Zoomify base directory not found. "
            "Ensure the given URL contains a Zoomify object.\n"
            "If that does not work, see \"Troubleshooting\" (http://sourceforge.net/p/dezoomify/wiki/Troubleshooting/) for additional help.")
            sys.exit()

        self.log.info("Found ZoomifyImagePath: {}".format(image_path))

        image_path = urllib.parse.unquote(image_path)
        base_dir = urllib.parse.urljoin(url, image_path)
        base_dir = base_dir.rstrip('/') + '/'
        return base_dir

    def get_properties(self, base_dir, zoom_level):
        """
        Retrieve the XML properties file and extract the needed information.

        Sets the relevant variables for the grabbing phase.

        Keyword arguments
        base_dir -- the Zoomify base directory
        zoom_level -- the level which we want to get
        """

        # READ THE XML FILE AND RETRIEVE THE ZOOMIFY PROPERTIES
        # NEEDED TO RECONSTRUCT (WIDTH, HEIGHT AND TILESIZE)

        # this file contains information about the image tiles
        xml_url = urllib.parse.urljoin(base_dir, 'ImageProperties.xml')

        self.log.info("xml_url=" + xml_url)
        content = None
        try:
            with open_url(xml_url) as handle:
                content = handle.read().decode(errors='ignore')
        except Exception:
            self.log.error(
                "Could not open ImageProperties.xml ({}).\n"
                "URL: {}".format(sys.exc_info()[1], xml_url)
            )
            sys.exit()

        # example: <IMAGE_PROPERTIES WIDTH="2679" HEIGHT="4000" NUMTILES="241" NUMIMAGES="1" VERSION="1.8" TILESIZE="256"/>
        properties = dict(re.findall(r"\b(\w+)\s*=\s*[\"']([^\"']*)[\"']", content))
        self.max_width = int(properties["WIDTH"])
        self.max_height = int(properties["HEIGHT"])
        self.tile_size = int(properties["TILESIZE"])

        # PROCESS PROPERTIES TO GET ADDITIONAL DERIVABLE PROPERTIES

        self.get_zoom_levels()  # get one-indexed maximum zoom level
        self.max_zoom = len(self.levels)

        # GET THE REQUESTED ZOOMLEVEL
        if not zoom_level:  # none requested, using maximum
            self.zoom_level = self.max_zoom - 1
        else:
            zoom_level = int(zoom_level)
            if zoom_level < self.max_zoom and zoom_level >= 0:
                self.zoom_level = zoom_level
            else:
                self.zoom_level = self.max_zoom - 1
                self.log.warning(
                    "The requested zoom level is not available, "
                    "defaulting to maximum ({:d})".format(self.zoom_level)
                )

        # GET THE SIZE AT THE RQUESTED ZOOM LEVEL
        self.width  = int(self.max_width  / 2 ** (self.max_zoom - self.zoom_level - 1))
        self.height = int(self.max_height / 2 ** (self.max_zoom - self.zoom_level - 1))

        # GET THE NUMBER OF TILES AT THE REQUESTED ZOOM LEVEL
        self.maxx_tiles, self.maxy_tiles = self.levels[-1]
        self.x_tiles,    self.y_tiles    = self.levels[self.zoom_level]

        self.log.info('\tMax zoom level:    {:d} (working zoom level: {:d})'.format(self.max_zoom - 1, self.zoom_level))
        self.log.info('\tWidth (overall):   {:d} (at given zoom level: {:d})'.format(self.max_width, self.width))
        self.log.info('\tHeight (overall):  {:d} (at given zoom level: {:d})'.format(self.max_height, self.height))
        self.log.info('\tTile size:         {:d}'.format(self.tile_size))
        self.log.info('\tWidth (in tiles):  {:d} (at given level: {:d})'.format(self.maxx_tiles, self.x_tiles))
        self.log.info('\tHeight (in tiles): {:d} (at given level: {:d})'.format(self.maxy_tiles, self.y_tiles))
        self.log.info('\tTotal tiles:       {:d} (to be retrieved: {:d})'.format(self.maxx_tiles * self.maxy_tiles,
                                                                         self.x_tiles * self.y_tiles))

    def get_zoom_levels(self):
        """Construct a list of all zoomlevels with sizes in tiles"""
        loc_width = self.max_width
        loc_height = self.max_height
        self.levels = []
        while True:
            width_in_tiles = int(ceil(loc_width / float(self.tile_size)))
            height_in_tiles = int(ceil(loc_height / float(self.tile_size)))
            self.levels.append((width_in_tiles, height_in_tiles))

            if width_in_tiles == 1 and height_in_tiles == 1:
                break

            loc_width = int(loc_width / 2.)
            loc_height = int(loc_height / 2.)

        # make the 0th level the smallest zoom, and higher levels, higher zoom
        self.levels.reverse()
        self.log.debug("self.levels = {}".format(self.levels))
        
    def get_tile_index(self, level, x, y):
        """
        Get the zoomify index of a tile in a given level, at given co-ordinates
        This is needed to get the tilegroup.

        Keyword arguments:
        level -- the zoomlevel of the tile
        x,y -- the co-ordinates of the tile in that level

        Returns -- the zoomify index
        """

        index = x + y * int(ceil(floor(self.width / pow(2, self.max_zoom - level - 1)) / self.tile_size))

        for i in range(1, level + 1):
            index += int(ceil(floor(self.width / pow(2, self.max_zoom - i)) / self.tile_size)) * \
                int(ceil(floor(self.height / pow(2, self.max_zoom - i)) / self.tile_size))

        return index

    def get_tile_url(self, col, row):
        """
        Return the full URL of an image at a given position in the Zoomify structure.
        """
        tile_index = self.get_tile_index(self.zoom_level, col, row)
        tile_group = tile_index // self.tile_size
        url = self.base_dir + 'TileGroup{}/{}-{}-{}.{}'.format(tile_group, self.zoom_level, col, row, self.ext)
        return url

if __name__ == "__main__":
    main()
