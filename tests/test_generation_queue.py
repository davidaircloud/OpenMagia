import unittest
import tempfile
from pathlib import Path
from unittest import mock

import server


class GenerationQueueTests(unittest.TestCase):
    def test_completed_character_sheet_is_saved_to_cast_automatically(self):
        sheet = {"id": "sheet-1", "name": "Serina", "identity": "short dark hair",
                 "recipe": "turn-6", "style": "match", "status": "ready",
                 "frames": [{"mediaId": "front"}, {"mediaId": "back"}]}
        project = {"sheets": [sheet], "characters": [], "media": [
            {"id": "front", "kind": "image"}, {"id": "back", "kind": "image"}]}

        self.assertTrue(server.promote_completed_sheets(project))
        self.assertEqual([], project["sheets"])
        self.assertEqual("Serina", project["characters"][0]["name"])
        self.assertEqual(["front", "back"], project["characters"][0]["images"])

    def test_unavailable_reference_is_rejected_before_queueing(self):
        with tempfile.TemporaryDirectory() as directory:
            present = Path(directory) / "present.png"
            present.touch()
            server.validate_reference_availability([{"name": "Present", "paths": [present]}])
            with self.assertRaisesRegex(ValueError, "Reference media is unavailable"):
                server.validate_reference_availability([
                    {"name": "Missing portrait", "paths": [Path(directory) / "missing.png"]}])

    def test_failed_scene_without_media_is_repaired(self):
        project = {"scenes": [{"id": "failed-1", "name": "Failed scene", "status": "error",
                                "error": "engine failed", "generation_type": "video", "params": {}}],
                   "media": []}
        self.assertTrue(server.repair_generation_placeholders(project))
        self.assertEqual("error", project["media"][0]["status"])
        self.assertEqual("engine failed", project["media"][0]["error"])
        self.assertFalse(server.repair_generation_placeholders(project))

    def test_already_queued_scene_still_gets_media_placeholder(self):
        scene = {
            "id": "scene-1", "name": "Scene 1", "status": "queued",
            "generation_type": "video", "params": {"width": 896, "height": 512},
            "style_profile": {}, "prompt": "A scene", "prompt_skill_id": None,
        }
        project = {"slug": "test", "scenes": [scene], "media": []}
        original_queue = list(server.queue)
        original_active = server.active_job
        try:
            server.queue.clear()
            server.active_job = "another-scene"
            with mock.patch.object(server, "save_project"):
                server.enqueue(scene["id"], project)
            self.assertEqual(1, len(project["media"]))
            self.assertEqual(scene["id"], project["media"][0]["scene_id"])
            self.assertEqual("queued", project["media"][0]["status"])
        finally:
            server.queue[:] = original_queue
            server.active_job = original_active


if __name__ == "__main__":
    unittest.main()
