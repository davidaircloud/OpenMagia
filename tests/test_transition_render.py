"""Real FFmpeg regression tests; run with FFmpeg on PATH (no source media needed)."""
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

import nle


@unittest.skipUnless(shutil.which('ffmpeg'), 'FFmpeg is required for rendered-frame checks')
class TransitionRenderTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        for color in ('red', 'blue', 'green'):
            self.command(['ffmpeg', '-y', '-v', 'error', '-f', 'lavfi', '-i',
                          f'color={color}:s=160x96:r=24:d=2', '-vf',
                          'drawbox=x=32:y=0:w=8:h=96:color=white:t=fill',
                          '-c:v', 'libx264', str(self.root / f'{color}.mp4')])

    def command(self, args):
        result = subprocess.run(args, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr.decode(errors='replace'))
        return result.stdout

    def frame(self, path, seconds):
        data = self.command(['ffmpeg', '-v', 'error', '-i', str(path), '-vf',
                             f'select=eq(n\\,{round(seconds*24)})', '-frames:v', '1',
                             '-f', 'rawvideo', '-pix_fmt', 'rgb24', '-'])
        self.assertEqual(len(data), 160*96*3)
        return lambda x, y=48: tuple(data[(y*160+x)*3:(y*160+x)*3+3])

    def project(self, kind, overlay=False):
        def clip(color, start, transitions):
            return dict(id=color, mediaId=color, start=start, **{'in': 0, 'out': 2}, transition={'items': transitions})
        incoming = {'type': kind, 'dur': 1, 'edge': 'start'}
        if overlay:
            tracks = [{'id': 'V2', 'kind': 'video', 'clips': [clip('blue', 0, [incoming, {'type': kind, 'dur': 1, 'edge': 'end'}])]},
                      {'id': 'V1', 'kind': 'video', 'clips': [clip('red', 0, [])]}]
        else:
            tracks = [{'id': 'V1', 'kind': 'video', 'clips': [clip('red', 0, []), clip('blue', 2, [incoming]), clip('green', 4, [])]}]
        return {'name': kind, 'canvas': {'width': 160, 'height': 96}, 'tracks': tracks,
                'media': [{'id': color, 'src': str(self.root / f'{color}.mp4'), 'hasAudio': False} for color in ('red', 'blue', 'green')]}

    def render(self, project):
        url = nle.export_project(project, self.root)
        return self.root / url.lstrip('/')

    def test_distinct_join_pixels_and_no_timeline_compression(self):
        for kind in ('dissolve', 'fade', 'wipe', 'slide', 'circle'):
            with self.subTest(kind=kind):
                path = self.render(self.project(kind))
                self.assertAlmostEqual(nle.probe(path)['duration'], 6, delta=1/24)
                pixel = self.frame(path, 2.5)
                if kind == 'fade':
                    self.assertLess(max(pixel(80)), 8)
                elif kind == 'dissolve':
                    self.assertAlmostEqual(pixel(80)[0], 127, delta=8)
                    self.assertAlmostEqual(pixel(80)[2], 127, delta=8)
                elif kind in ('wipe', 'slide'):
                    self.assertGreater(pixel(10)[0], 240)
                    self.assertGreater(pixel(150)[2], 240)
                    if kind == 'slide':
                        self.assertGreater(min(pixel(115)), 235)  # shifted white stripe
                    else:
                        self.assertLess(pixel(115)[0], 10)
                elif kind == 'circle':
                    self.assertGreater(pixel(80)[2], 240)
                    self.assertGreater(pixel(0, 0)[0], 240)
                # The third scene still starts at four seconds, not earlier.
                self.assertGreater(self.frame(path, 3.75)(80)[2], 240)
                self.assertGreater(self.frame(path, 4.25)(80)[1], 110)

    def test_overlay_entry_and_exit_use_selected_geometry(self):
        for kind in ('fade', 'dissolve', 'wipe', 'slide', 'circle'):
            with self.subTest(kind=kind):
                path = self.render(self.project(kind, overlay=True))
                self.assertAlmostEqual(nle.probe(path)['duration'], 2, delta=1/24)
                for at in (.5, 1.5):
                    pixel = self.frame(path, at)
                    if kind in ('fade', 'dissolve'):
                        self.assertAlmostEqual(pixel(80)[0], 127, delta=10)
                        self.assertAlmostEqual(pixel(80)[2], 127, delta=10)
                    elif kind == 'wipe':
                        self.assertGreater(pixel(10)[0], 240)
                        self.assertGreater(pixel(150)[2], 240)
                    elif kind == 'circle':
                        self.assertGreater(pixel(80)[2], 240)
                        self.assertGreater(pixel(0, 0)[0], 240)
                    else:
                        self.assertGreater(pixel(10 if at == .5 else 150)[0], 240)
                        self.assertGreater(pixel(150 if at == .5 else 10)[2], 240)
