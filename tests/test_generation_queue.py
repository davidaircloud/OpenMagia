import unittest
from unittest import mock

import server


class GenerationQueueTests(unittest.TestCase):
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
