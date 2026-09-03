import json
import tempfile
import unittest
from pathlib import Path

from openmagia_plugins import PluginError, PluginRegistry, load_manifest, send_notification


def make_plugin(tmp_path: Path, **overrides):
    root = tmp_path / "plugin"
    root.mkdir()
    (root / "index.html").write_text("<h1>Plugin</h1>")
    (root / "cover.svg").write_text("<svg xmlns='http://www.w3.org/2000/svg'/>")
    manifest = {
        "manifestVersion": 1,
        "id": "com.example.test-plugin",
        "name": "Test Plugin",
        "version": "1.0.0",
        "description": "A test plugin",
        "ui": "index.html",
        "cover": "cover.svg",
        "permissions": ["project.read", "storage"],
    }
    manifest.update(overrides)
    (root / "openmagia-plugin.json").write_text(json.dumps(manifest))
    return root


class PluginTests(unittest.TestCase):
    def test_manifest_normalizes_and_validates_assets(self):
        with tempfile.TemporaryDirectory() as directory:
            root = make_plugin(Path(directory))
            manifest = load_manifest(root)
            self.assertEqual(manifest["id"], "com.example.test-plugin")
            self.assertEqual(manifest["root"], str(root.resolve()))
            self.assertEqual(manifest["permissions"], ["project.read", "storage"])

    def test_manifest_rejects_unknown_permission(self):
        with tempfile.TemporaryDirectory() as directory:
            root = make_plugin(Path(directory), permissions=["host.shell"])
            with self.assertRaisesRegex(PluginError, "Unknown permissions"):
                load_manifest(root)

    def test_manifest_cannot_escape_plugin_folder(self):
        with tempfile.TemporaryDirectory() as directory:
            tmp_path = Path(directory)
            (tmp_path / "outside.html").write_text("no")
            root = make_plugin(tmp_path, ui="../outside.html")
            with self.assertRaisesRegex(PluginError, "inside the plugin folder"):
                load_manifest(root)

    def test_registry_requires_all_permissions_before_enable(self):
        with tempfile.TemporaryDirectory() as directory:
            tmp_path = Path(directory)
            root = make_plugin(tmp_path)
            registry = PluginRegistry(tmp_path / "data/plugins.json", tmp_path / "data/plugin.log")
            loaded = registry.install(root)
            self.assertFalse(loaded["enabled"])
            with self.assertRaisesRegex(PluginError, "Approve every"):
                registry.update(loaded["id"], enabled=True, grants=["project.read"])
            enabled = registry.update(loaded["id"], enabled=True, grants=["project.read", "storage"])
            self.assertTrue(enabled["enabled"])
            self.assertEqual(registry.authorize(loaded["id"], "storage")["id"], loaded["id"])

    def test_remove_does_not_delete_source(self):
        with tempfile.TemporaryDirectory() as directory:
            tmp_path = Path(directory)
            root = make_plugin(tmp_path)
            registry = PluginRegistry(tmp_path / "data/plugins.json", tmp_path / "data/plugin.log")
            plugin = registry.install(root)
            registry.remove(plugin["id"])
            self.assertTrue(root.exists())
            self.assertEqual(registry.list(), [])

    def test_empty_notification_target_is_safe_dry_run(self):
        self.assertTrue(send_notification("email", {}, "Title", "Message")["dryRun"])
        self.assertTrue(send_notification("imessage", {}, "Title", "Message")["dryRun"])


if __name__ == "__main__":
    unittest.main()
