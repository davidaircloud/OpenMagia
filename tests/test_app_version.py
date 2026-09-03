import tempfile
import unittest
from pathlib import Path
from unittest import mock

import server


class AppVersionTests(unittest.TestCase):
    def test_checked_in_version_is_semantic(self):
        self.assertRegex(server.VERSION_FILE.read_text().strip(), r"^\d+\.\d+\.\d+$")

    def test_identity_falls_back_for_source_archive(self):
        with tempfile.TemporaryDirectory() as td, \
             mock.patch.object(server, "VERSION_FILE", Path(td) / "missing"), \
             mock.patch.dict(server.os.environ, {"OPENMAGIA_BUILD":""}, clear=False), \
             mock.patch.object(server.subprocess, "run", side_effect=OSError("git unavailable")):
            identity = server.application_identity()
        self.assertEqual(identity, {"version":"0.0.0-dev", "build":"source"})


if __name__ == "__main__":
    unittest.main()
