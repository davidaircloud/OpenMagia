import unittest
from unittest import mock

import nle


class NleProbeTests(unittest.TestCase):
    def test_probe_uses_ffmpeg_when_ffprobe_is_unavailable(self):
        stderr = ("Duration: 00:00:05.25, start: 0.000000, bitrate: 1200 kb/s\n"
                  "Stream #0:0: Video: h264, yuv420p, 896x512\n"
                  "Stream #0:1: Audio: aac, 48000 Hz, stereo\n")
        result = mock.Mock(returncode=0, stdout="", stderr=stderr)
        with mock.patch.object(nle.shutil, "which", return_value=None), \
             mock.patch.object(nle, "run", return_value=result) as run:
            info = nle.probe("clip.mp4")
        self.assertEqual(info, {"duration": 5.25, "w": 896, "h": 512, "hasAudio": True})
        self.assertEqual(run.call_args.args[0][0], "ffmpeg")


if __name__ == "__main__":
    unittest.main()
