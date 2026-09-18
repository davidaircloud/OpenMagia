"""Behavioral regressions for additive Magia and the OBS plugin contract."""
import copy
import unittest
from pathlib import Path
from unittest import mock
import server
from openmagia_plugins import load_manifest, PluginRegistry, PluginError
import tempfile


class PerformanceTests(unittest.TestCase):
    def project(self):
        return {"slug": "performance-test", "media": [{"id": "m", "kind": "video", "hasAudio": True}],
                "tracks": [{"id": "V1", "kind": "video", "clips": [
                    {"id": "v", "mediaId": "m", "start": 4, "in": 0, "out": 5,
                     "color": {"exposure": .2, "contrast": 1.1, "saturation": .8},
                     "blur": {"enabled": True, "amount": 3}}]},
                    {"id": "A1", "kind": "audio", "clips": [
                    {"id": "a", "mediaId": "m", "start": 1, "in": 0, "out": 8}]}]}

    def options(self, **enabled):
        return {**dict.fromkeys(server.MAGIA_CATEGORY_KEYS, False), **enabled}

    def test_stack_compounds_color_preserves_other_families_and_timing(self):
        p = self.project(); original = copy.deepcopy(p)
        req = {"seed": 42, "recipe_id": "cinematic", "mode": "stack", "options": self.options(color=True)}
        first = server.timeline_magia_plan(p, req)
        self.assertEqual(p, original, "Planning must not mutate the project")
        server.apply_timeline_magia_plan(p, first)
        c = p["tracks"][0]["clips"][0]; once = copy.deepcopy(c["color"])
        second = server.timeline_magia_plan(p, req)
        server.apply_timeline_magia_plan(p, second)
        self.assertNotEqual(c["color"], once)
        self.assertEqual(c["blur"], original["tracks"][0]["clips"][0]["blur"])
        self.assertEqual(c["start"], 4)
        server.restore_magia_effects(c, ["color"])
        self.assertEqual(c["color"], original["tracks"][0]["clips"][0]["color"])

    def test_audio_selected_scope_leaves_picture_untouched(self):
        p = self.project(); picture = copy.deepcopy(p["tracks"][0])
        plan = server.timeline_magia_plan(p, {"seed": 8, "mode": "stack", "scope": "selected",
            "selected_clip_id": "a", "options": self.options(audio=True, loudness=True)})
        self.assertEqual([u["clip_id"] for u in plan["updates"]], ["a"])
        server.apply_timeline_magia_plan(p, plan)
        self.assertEqual(p["tracks"][0], picture)
        audio = p["tracks"][1]["clips"][0]
        self.assertGreater(audio["audioFade"]["in"], 0)
        self.assertTrue(audio["audioProcessing"]["loudness"])

    def test_stack_retains_audio_cleanup_when_adding_loudness(self):
        p = self.project(); c = p["tracks"][1]["clips"][0]
        c["audioProcessing"] = {"voice": True, "compress": True}
        plan = server.timeline_magia_plan(p, {"seed": 4, "mode": "stack", "options": self.options(loudness=True)})
        server.apply_timeline_magia_plan(p, plan)
        self.assertEqual(c["audioProcessing"]["voice"], True)
        self.assertEqual(c["audioProcessing"]["loudness"], True)

    def test_empty_stack_does_not_clear_or_retime(self):
        p = self.project(); before = copy.deepcopy(p["tracks"])
        plan = server.timeline_magia_plan(p, {"mode": "stack", "options": self.options()})
        self.assertEqual(server.apply_timeline_magia_plan(p, plan), 0)
        self.assertEqual(p["tracks"], before)

    def test_plugin_requires_explicit_output_grant(self):
        path = Path(__file__).resolve().parents[1] / "plugins" / "obs-output"
        manifest = load_manifest(path)
        self.assertEqual(manifest["permissions"], ["output.open"])
        with tempfile.TemporaryDirectory() as root:
            registry = PluginRegistry(Path(root)/"plugins.json", Path(root)/"log.jsonl")
            plugin = registry.install(str(path))
            with self.assertRaises(PluginError):
                registry.authorize(plugin["id"], "output.open")
            registry.update(plugin["id"], enabled=True, grants=["output.open"])
            registry.authorize(plugin["id"], "output.open")
            with self.assertRaises(PluginError):
                registry.authorize(plugin["id"], "timeline.write")

    def test_output_has_named_page_and_project_title(self):
        root = Path(__file__).resolve().parents[1]
        output = (root / "program-output.html").read_text()
        app = (root / "app.js").read_text()
        backend = (root / "server.py").read_text()
        self.assertIn("<title>Preview</title>", output)
        self.assertIn("window.open('/Preview'", app)
        self.assertIn("Preview · ", app)
        self.assertIn('p == "/Preview"', backend)

    def test_obs_instructions_separate_timeline_mix_and_microphone(self):
        root = Path(__file__).resolve().parents[1]
        instructions = (root / "plugins" / "obs-output" / "index.html").read_text()
        self.assertIn("complete timeline mix", instructions)
        self.assertIn("Audio Input Capture", instructions)
        self.assertIn("Insta360 Link 2 Pro", instructions)

    def test_undo_restores_magia_metadata_with_the_tracks(self):
        p = self.project()
        p["timelineMagia"] = {"recipe_id": "subtle"}
        before = copy.deepcopy(p)
        server.push_timeline_undo(p)
        p["timelineMagia"] = {"recipe_id": "cinematic"}
        p["tracks"][0]["clips"][0]["color"] = {"exposure": 2}
        with mock.patch.object(server, "save_project"):
            self.assertTrue(server.undo_timeline(p))
        self.assertEqual(p, before)

    def test_magia_adds_motion_lane_beside_manual_transform_and_fade(self):
        p = self.project(); c = p['tracks'][0]['clips'][0]
        c.update(zoom=1.2, position={'x': 8, 'y': -4}, keyframes={'points': [
            {'id': 'manual-a', 'at': 0, 'zoom': 1, 'x': .5, 'y': .5},
            {'id': 'manual-b', 'at': 1, 'zoom': 1.25, 'x': .5, 'y': .5}]},
            transition={'items': [{'id': 'manual-fade', 'edge': 'start', 'type': 'fade', 'dur': 1.09}]})
        before = copy.deepcopy(c)
        req = {'seed': 42, 'mode': 'stack', 'options': self.options(transforms=True, transitions=True, color=True)}
        for _ in range(2):
            server.apply_timeline_magia_plan(p, server.timeline_magia_plan(p, req))
        for key in ('zoom', 'position', 'keyframes', 'transition', 'blur'):
            self.assertEqual(c[key], before[key], key)
        self.assertEqual(len(c['transformLayers']), 2)
        self.assertNotEqual(c['transformLayers'][0]['id'], c['transformLayers'][1]['id'])
        self.assertTrue(c['transformLayers'][0]['keyframes']['points'])
        server.restore_magia_effects(c, ['transforms'])
        self.assertNotIn('transformLayers', c)
        self.assertEqual(c['keyframes'], before['keyframes'])
        self.assertEqual(c['transition'], before['transition'])

    def test_magia_single_clip_gets_fade_and_motion_together(self):
        p = self.project(); c = p['tracks'][0]['clips'][0]
        req = {'seed': 42, 'mode': 'stack', 'options': self.options(transforms=True, transitions=True)}
        server.apply_timeline_magia_plan(p, server.timeline_magia_plan(p, req))
        self.assertEqual(c['transition']['items'][0]['type'], 'fade')
        self.assertEqual(len(c['transformLayers']), 1)
