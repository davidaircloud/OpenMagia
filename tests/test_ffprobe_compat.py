import contextlib
import io
import sys
import unittest
from unittest import mock

import ffprobe_compat


class FfprobeCompatTests(unittest.TestCase):
    def test_reports_first_video_stream_dimensions(self):
        result = mock.Mock(stderr=(
            "Stream #0:0: Audio: aac, 48000 Hz, stereo\n"
            "Stream #0:1: Video: png, rgba, 1536x1024, 24 fps\n"
        ))
        output = io.StringIO()
        with mock.patch.object(sys, "argv", ["ffprobe", "-of", "csv", "portrait.png"]), \
             mock.patch.object(ffprobe_compat.shutil, "which", return_value="/managed/ffmpeg"), \
             mock.patch.object(ffprobe_compat.subprocess, "run", return_value=result), \
             contextlib.redirect_stdout(output):
            self.assertEqual(0, ffprobe_compat.main())
        self.assertEqual("1536x1024", output.getvalue().strip())

    def test_fails_when_input_has_no_video_stream(self):
        result = mock.Mock(stderr="Stream #0:0: Audio: aac, 48000 Hz, stereo\n")
        with mock.patch.object(sys, "argv", ["ffprobe", "audio.wav"]), \
             mock.patch.object(ffprobe_compat.shutil, "which", return_value="/managed/ffmpeg"), \
             mock.patch.object(ffprobe_compat.subprocess, "run", return_value=result), \
             contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(1, ffprobe_compat.main())


if __name__ == "__main__":
    unittest.main()
