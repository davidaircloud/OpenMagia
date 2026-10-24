import shutil
import tempfile
import unittest
from pathlib import Path
import nle

class AnchorGeometryTests(unittest.TestCase):
    @unittest.skipUnless(shutil.which('ffmpeg'), 'FFmpeg required')
    def test_landscape_anchor_crops_without_squashing(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td);source=root/'source.ppm';out=root/'square.png'
            # A centered square in a wide image must remain square after fitting.
            pixels=bytearray()
            for y in range(100):
                for x in range(200):pixels.extend((255,255,255) if 80<=x<120 and 30<=y<70 else (0,0,0))
            source.write_bytes(b'P6\n200 100\n255\n'+pixels)
            nle.fit_generation_anchor(source,out,100,100)
            result=nle.subprocess.run(['ffmpeg','-v','error','-i',str(out),'-f','rawvideo','-pix_fmt','rgb24','-'],capture_output=True,check=True)
            lit=[(i%100,i//100)for i in range(10000)if result.stdout[i*3]>128]
            width=max(x for x,y in lit)-min(x for x,y in lit)+1
            height=max(y for x,y in lit)-min(y for x,y in lit)+1
            self.assertEqual(width,height)
            self.assertEqual((width,height),(40,40))
