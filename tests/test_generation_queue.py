import unittest
import tempfile
from pathlib import Path
from unittest import mock

import server


class GenerationQueueTests(unittest.TestCase):
    def test_sheet_completion_during_recovery_cannot_be_resurrected(self):
        import copy
        persisted = {"slug": "test", "scenes": [], "media": [],
                     "sheets": [{"id": "sheet", "status": "running"}]}
        class CompletionLock:
            def __enter__(inner):
                persisted["sheets"] = []
                persisted["characters"] = [{"id": "saved-cast"}]
                server.sheet_active = None
            def __exit__(inner, *args):
                return False
        with mock.patch.object(server, "active_job", None), \
             mock.patch.object(server, "sheet_active", "sheet"), \
             mock.patch.object(server, "sheet_queue", []), \
             mock.patch.object(server, "queue", []), \
             mock.patch.object(server, "lock", CompletionLock()), \
             mock.patch.object(server, "load_project_slug", side_effect=lambda slug: copy.deepcopy(persisted)), \
             mock.patch.object(server, "save_project") as save, \
             mock.patch.object(server, "pump_sheet_queue") as pump:
            server.recover_queue({"slug": "test"})
            save.assert_not_called()
            pump.assert_not_called()
            self.assertEqual([], server.sheet_queue)

    def test_active_sheet_cannot_be_enqueued_twice(self):
        with mock.patch.object(server, "sheet_active", "sheet"), \
             mock.patch.object(server, "sheet_queue", []), \
             mock.patch.object(server, "save_project") as save:
            server.enqueue_sheet("sheet", {"sheets": [{"id": "sheet", "status": "running"}]})
            self.assertEqual([], server.sheet_queue)
            save.assert_not_called()

    def test_music_completion_releases_slot_before_pumping_next_song(self):
        project = {"slug": "test", "scenes": [{"id": "song", "status": "running"}], "media": []}
        previous = server.active_job
        try:
            server.active_job = "song"
            with mock.patch.object(server, "music_request_from_scene", side_effect=ValueError("bad request")), \
                 mock.patch.object(server, "load_project_slug", return_value=project), \
                 mock.patch.object(server, "save_project"), \
                 mock.patch.object(server, "pump_queue") as pump:
                pump.side_effect = lambda project: self.assertIsNone(server.active_job)
                server.run_music_job("song", project)
            pump.assert_called_once()
            self.assertEqual("error", project["scenes"][0]["status"])
        finally:
            server.active_job = previous

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

    def test_music_placeholder_is_audio(self):
        project = {"scenes": [{"id": "song-1", "name": "Song", "status": "error",
                                "generation_type": "music", "params": {}}], "media": []}
        self.assertTrue(server.repair_generation_placeholders(project))
        self.assertEqual("audio", project["media"][0]["kind"])

    def test_ready_generation_replaces_stale_duplicate_without_losing_clip(self):
        project = {"slug": "test", "scenes": [], "media": [
            {"id": "stale", "scene_id": "song-1", "status": "running", "src": ""},
            {"id": "ready", "scene_id": "song-1", "status": "ready", "src": "media/song.flac"},
        ], "tracks": [{"id": "a1", "kind": "audio", "clips": [
            {"id": "clip-1", "sceneId": "song-1", "mediaId": "stale"}]}]}
        self.assertTrue(server.repair_generation_media_integrity(project))
        self.assertEqual(["ready"], [item["id"] for item in project["media"]])
        self.assertEqual("ready", project["tracks"][0]["clips"][0]["mediaId"])

    def test_legacy_failed_music_card_is_typed_as_audio(self):
        project = {"slug": "test", "scenes": [{"id": "song-1", "generation_type": "music"}],
                   "media": [{"id": "failed", "scene_id": "song-1", "kind": "video",
                              "status": "error", "src": ""}], "tracks": []}
        self.assertTrue(server.repair_generation_media_integrity(project))
        self.assertEqual("audio", project["media"][0]["kind"])

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
