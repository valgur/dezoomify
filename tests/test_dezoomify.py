import unittest
import sys
import os
import tempfile
import shutil
from hashlib import md5

PACKAGE_PARENT = '..'
SCRIPT_DIR = os.path.dirname(os.path.realpath(os.path.join(os.getcwd(), os.path.expanduser(__file__))))
sys.path.append(os.path.normpath(os.path.join(SCRIPT_DIR, PACKAGE_PARENT)))
import dezoomify

def run_dezoomify(command):
    args = dezoomify.parser.parse_args(command.split())
    dezoomify.UntilerDezoomify(args)

testimage_url = 'http://www.bl.uk/onlinegallery/onlineex/apac/photocoll/s/zoomify64430.html'
# Hash of image at testimage_url, zoom level 1.
# This hash might change depending on the jpegtran implementation, I guess.
correct_md5 = '9618e8a26fde2a394498b883ddfb55b8'

class TestBatchMode(unittest.TestCase):

    def assertImageIsCorrect(self, image_path):
        self.assertTrue(os.path.exists(image_path))
        with open(image_path, 'rb') as f:
            image_md5 = md5(f.read()).hexdigest()
        self.assertEqual(image_md5, correct_md5)

    def setUp(self):
        self.tempdir_path = tempfile.mkdtemp(dir = '', prefix='dezoomify_test_')

    def test_correct_url(self):
        command = "batch_mode_normal.list.txt " + os.path.join(self.tempdir_path, "img.jpg") + " -l -s -z 1 -v"
        # Expecting no exceptions to be raised...
        run_dezoomify(command)
        self.assertImageIsCorrect(os.path.join(self.tempdir_path, "img_001.jpg"))
        self.assertImageIsCorrect(os.path.join(self.tempdir_path, "img_002.jpg"))

    def test_incorrect_url(self):
        command = "batch_mode_url_error.list.txt " + os.path.join(self.tempdir_path, "img.jpg") + " -l -s -z 1"
        # Expecting no exceptions to be raised...
        run_dezoomify(command)
        self.assertFalse(os.path.exists(os.path.join(self.tempdir_path, "img_001.jpg")))
        self.assertImageIsCorrect(os.path.join(self.tempdir_path, "img_002.jpg"))

    def tearDown(self):
        shutil.rmtree(self.tempdir_path)

class TestZoomLevels(unittest.TestCase):

    def assertImageIsCorrect(self, image_path):
        self.assertTrue(os.path.exists(image_path))
        with open(image_path, 'rb') as f:
            image_md5 = md5(f.read()).hexdigest()
        self.assertEqual(image_md5, correct_md5)

    def setUp(self):
        self.tempdir_path = tempfile.mkdtemp(dir = '', prefix='dezoomify_test_')

    def test_positive_zoom_level(self):
        command = testimage_url + ' ' + os.path.join(self.tempdir_path, "img.jpg") + " -s -z 1 -v"
        run_dezoomify(command)
        self.assertImageIsCorrect(os.path.join(self.tempdir_path, "img.jpg"))

    def test_negative_zoom_level(self):
        command = testimage_url + ' ' + os.path.join(self.tempdir_path, "img.jpg") + " -s -z -4 -vvv"
        run_dezoomify(command)
        self.assertImageIsCorrect(os.path.join(self.tempdir_path, "img.jpg"))

    def test_incorrect_zoom_level(self):
        command = testimage_url + ' ' + os.path.join(self.tempdir_path, "img.jpg") + " -s -z -100"
        with self.assertRaises(dezoomify.ZoomLevelError):
            run_dezoomify(command)

    def tearDown(self):
        shutil.rmtree(self.tempdir_path)

if __name__ == '__main__':
    unittest.main()
