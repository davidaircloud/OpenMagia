import io
import unittest
from pathlib import Path
from unittest import mock

import server


class ModelInstallerTests(unittest.TestCase):
    @staticmethod
    def process(output="done\n", returncode=0):
        process = mock.Mock()
        process.stdout = io.StringIO(output)
        process.wait.return_value = returncode
        return process

    def test_h3_runs_immutable_bash_snapshot(self):
        original = (server.ROOT / "install.sh").read_bytes()

        def completed(args, **kwargs):
            self.assertEqual(args[0], "/bin/bash")
            self.assertNotEqual(Path(args[1]), server.ROOT / "install.sh")
            self.assertEqual(Path(args[1]).read_bytes(), original)
            self.assertEqual(args[2:], ["--no-formatter"])
            self.assertEqual(kwargs["env"]["OPENMAGIA_ROOT"], str(server.ROOT))
            self.assertEqual(kwargs["start_new_session"], True)
            return self.process("download 37%\rdownload 100%\n")

        with mock.patch.object(server.subprocess, "Popen", side_effect=completed):
            server.install_model_component("h3")
        self.assertEqual(server.model_installs["h3"]["status"], "ready")

    def test_stalled_install_becomes_retryable_error(self):
        process = self.process("")
        process.wait.side_effect = [server.subprocess.TimeoutExpired("install", 30), None]
        with mock.patch.object(server.subprocess, "Popen", return_value=process), \
             mock.patch.object(server, "MODEL_INSTALL_STALL_TIMEOUT", 60), \
             mock.patch.object(server.time, "monotonic", side_effect=[0, 61]), \
             mock.patch.object(server, "terminate_process_tree") as terminate:
            server.install_model_component("h3")
        terminate.assert_called_once_with(process)
        self.assertEqual(server.model_installs["h3"]["status"], "error")
        self.assertIn("Try again", server.model_installs["h3"]["message"])

    def test_formatter_snapshot_skips_h3_and_models(self):
        captured = {}

        def completed(args, **kwargs):
            captured["snapshot"] = args[1]
            self.assertEqual(args[2:], ["--no-models", "--no-h3"])
            return self.process()

        with mock.patch.object(server.subprocess, "Popen", side_effect=completed):
            server.install_model_component("formatter")
        self.assertFalse(Path(captured["snapshot"]).exists())
        self.assertEqual(server.model_installs["formatter"]["status"], "ready")

    def test_runtime_snapshot_skips_models_and_h3(self):
        def completed(args, **kwargs):
            self.assertEqual(args[2:], ["--no-models", "--no-h3"])
            return self.process("managed FFmpeg ready\n")

        with mock.patch.object(server.subprocess, "Popen", side_effect=completed):
            server.install_model_component("runtime")
        self.assertEqual(server.model_installs["runtime"]["status"], "ready")


if __name__ == "__main__":
    unittest.main()
