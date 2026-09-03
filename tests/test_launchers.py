import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class LauncherTests(unittest.TestCase):
    def test_mac_launcher_forces_versioned_restart(self):
        text = (ROOT / "OpenMagia.command").read_text()
        self.assertIn("./start.sh --stop", text)
        self.assertIn("exec ./start.sh", text)
        self.assertIn("is already running. Opening it now", text)

    def test_background_launcher_checks_api_version(self):
        text = (ROOT / "start.sh").read_text()
        self.assertIn("server_is_current", text)
        self.assertIn("server_is_openmagia", text)
        self.assertIn('get("app_version")', text)
        self.assertIn("EXPECTED_VERSION", text)

    def test_windows_launcher_checks_version_and_managed_pid(self):
        text = (ROOT / "OpenMagia-Windows.ps1").read_text()
        self.assertIn("engine.app_version", text)
        self.assertIn("server.pid", text)
        self.assertIn("server.py", text)

    def test_linux_launcher_owns_server_lifecycle(self):
        text = (ROOT / "OpenMagia-Linux.sh").read_text()
        self.assertIn("./start.sh --stop", text)
        self.assertIn("exec ./start.sh", text)


if __name__ == "__main__":
    unittest.main()
