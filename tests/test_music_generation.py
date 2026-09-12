"""Music generation: the YuE 2 request contract and how OpenMagia carries it.

These tests are the guard against the two ways this feature could lie: offering a
control YuE 2 ignores, or rewriting the artist's words on the way to the model.
"""
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import server
import yue_prompts as yue
import yue_worker

LYRICS = ("# Kettle Season\n"
          "BPM: 88\n"
          "Key: D minor\n"
          "[Verse]\n"
          "Kettle on the stove again\n"
          "Rain keeps time against the glazing\n\n"
          "[Chorus]\n"
          "Stay a while, the road can wait\n"
          "Morning can have half of me")


class RequestCompiler(unittest.TestCase):
    def test_section_tags_survive_and_words_are_verbatim(self):
        compiled = yue.format_music_request(idea="piano pop", lyrics=LYRICS, song_id="kettle")
        request = compiled["request"]
        self.assertIn("[Verse]", request["lyrics"])
        self.assertIn("[Chorus]", request["lyrics"])
        self.assertIn("Kettle on the stove again", request["lyrics"])
        # A markdown heading and production notes are not singable, so they leave -
        # and the audit says so instead of losing them silently.
        self.assertNotIn("# Kettle Season", request["lyrics"])
        removed = {item["line"]: item.get("moved_to") for item in compiled["audit"]["lyric_lines_removed"]}
        self.assertIsNone(removed["# Kettle Season"])   # dropped, not moved
        self.assertEqual(removed["BPM: 88"], "style tags")

    def test_tempo_and_key_become_style_words_because_there_is_no_field_for_them(self):
        compiled = yue.format_music_request(idea="piano pop", lyrics=LYRICS, song_id="kettle")
        for fragment in ("88 BPM", "D minor"):
            self.assertIn(fragment, compiled["request"]["style"])
        self.assertNotIn("BPM:", compiled["request"]["lyrics"])

    def test_same_input_compiles_to_the_same_request(self):
        first = yue.format_music_request(idea="folk", lyrics="[Verse]\nOne two three", seed=3)["request"]
        second = yue.format_music_request(idea="folk", lyrics="[Verse]\nOne two three", seed=3)["request"]
        self.assertEqual(first, second)

    def test_request_only_carries_fields_the_cli_accepts(self):
        compiled = yue.format_music_request(idea="folk", lyrics="[Verse]\nOne two three", guidance=1.2)
        self.assertEqual(set(compiled["request"]) - {"id", "style", "tags", "lyrics", "cot", "abc",
                                                    "seed", "cfg_scale"}, set())
        for forbidden in ("duration", "seconds", "bpm", "negative_prompt", "reference_audio", "steps"):
            self.assertNotIn(forbidden, compiled["request"])

    def test_instrumental_has_no_vocal_contradiction_and_warns_about_length(self):
        compiled = yue.format_music_request(idea="solo cello", instrumental=True, plan_mode="full")
        self.assertIn("[Instrumental]", compiled["request"]["lyrics"])
        self.assertTrue(compiled["audit"]["instrumental"])
        self.assertIn("instrumental arrangement", compiled["request"]["style"])
        kinds = {warning["kind"] for warning in compiled["audit"]["warnings"]}
        self.assertIn("duration", kinds)
        self.assertTrue(compiled["audit"]["score_generated"])
        self.assertIn("abc", compiled["request"])
        self.assertIn("Q:1/4=", compiled["request"]["abc"])

    def test_artist_score_is_preserved_instead_of_replaced(self):
        score = "X:1\nM:4/4\nK:Dm\nD2 F2 A4 |]"
        compiled = yue.format_music_request(idea="dark strings", instrumental=True,
                                            plan_mode="full", abc=score)
        self.assertEqual(compiled["request"]["abc"], score)
        self.assertFalse(compiled["audit"]["score_generated"])

    def test_instrumental_preserves_the_selected_symbolic_plan(self):
        compiled = yue.format_music_request(idea="solo cello", instrumental=True, plan_mode="melody")
        self.assertEqual(compiled["request"]["cot"], "melody")
        self.assertEqual(compiled["audit"]["plan_mode_requested"], "melody")

    def test_direct_instrumental_is_rejected_before_a_scene_is_created(self):
        with self.assertRaisesRegex(ValueError, "needs Full plan"):
            yue.format_music_request(idea="ambient synth", instrumental=True, plan_mode="off")

    def test_sung_words_beat_an_instrumental_switch(self):
        compiled = yue.format_music_request(idea="anthem", lyrics="[Verse]\nwords remain", instrumental=True)
        self.assertFalse(compiled["audit"]["instrumental"])
        self.assertIn("words remain", compiled["request"]["lyrics"])

    def test_melody_planning_with_words_is_kept_and_without_a_score_is_still_honest(self):
        # `melody` with no ABC is legitimate when words are sung: the model plans the
        # score itself. It is only the words-less case that cannot end (covered by
        # test_instrumental_without_a_score_never_plans_by_melody).
        compiled = yue.format_music_request(idea="cover", lyrics="[Verse]\nla la", plan_mode="melody")
        self.assertEqual(compiled["request"]["cot"], "melody")
        self.assertEqual(compiled["audit"]["plan_mode_requested"], "melody")
        # An unparseable score is refused rather than silently ignored by the model.
        with self.assertRaises(ValueError):
            yue.format_music_request(idea="cover", lyrics="[Verse]\nla la", plan_mode="melody", abc="not abc at all")
        with self.assertRaises(ValueError):
            yue.format_music_request(idea="", lyrics="", plan_mode="full")

    def test_skill_contract_reaches_the_style_field(self):
        direction = server.compiled_skill_direction("score-underscore")
        compiled = yue.format_music_request(idea="strings", instrumental=True, skill_direction=direction,
                                            skill_id="score-underscore")
        self.assertIn("instrumental", compiled["request"]["style"].lower())

    def test_progress_uses_the_runtime_vocabulary(self):
        step = yue.parse_yue_progress("[YuE2] Running Generating song: 512/4096 tokens (12%) | "
                                      "54.0 tokens/s | elapsed 61.0s")
        self.assertEqual(step["phase"], "Generating song")
        self.assertEqual(step["completed"], 512)
        self.assertEqual(step["total"], 4096)
        done = yue.parse_yue_progress("[YuE2] Completed: 36.0s audio in 49.7s")
        self.assertEqual(done["audio_seconds"], 36.0)
        self.assertIsNone(yue.parse_yue_progress("nothing to see here"))

    def test_audio_quality_rejects_silence_and_late_clipping_collapse(self):
        silent = yue_worker.classify_audio_quality([0.0005, 0.0004], 0.01, 0.00045, 0)
        self.assertFalse(silent["accepted"])
        collapsed = yue_worker.classify_audio_quality(
            [0.07, 0.08, 0.16, 0.18, 0.25, 0.61, 0.53], 1.0, 0.32, 0.003)
        self.assertFalse(collapsed["accepted"])
        healthy = yue_worker.classify_audio_quality(
            [0.08, 0.1, 0.16, 0.22, 0.3], 0.97, 0.18, 0.0001)
        self.assertTrue(healthy["accepted"])


class ParamsAndScene(unittest.TestCase):
    def test_music_params_drop_every_video_knob(self):
        clamped = server.clamp_music_params({"frames": 56, "width": 768, "steps": 20, "seed": "7",
                                             "plan_mode": "melody", "lyrics": "x" * 9000,
                                             "guidance": 9})
        self.assertNotIn("frames", clamped)
        self.assertNotIn("width", clamped)
        self.assertNotIn("steps", clamped)
        self.assertEqual(clamped["seed"], 7)
        self.assertEqual(clamped["plan_mode"], "melody")
        self.assertEqual(len(clamped["lyrics"]), yue.MAX_LYRICS_CHARS)
        self.assertEqual(clamped["guidance"], 2.0)

    def test_generation_params_route_music_separately(self):
        clamped = server.clamp_generation_params({"plan_mode": "nonsense", "seed": 4}, "music")
        self.assertEqual(clamped["plan_mode"], "full")
        self.assertNotIn("layers", clamped)

    def test_a_shot_writing_skill_cannot_be_applied_to_a_song(self):
        scene = {"id": "s2", "name": "x", "prompt": "punk", "prompt_skill_id": "h3-prompt",
                 "params": server.clamp_music_params({"lyrics": "[Verse]\nla la"})}
        with self.assertRaises(ValueError) as caught:
            server.music_request_from_scene(scene)
        self.assertIn("music skill", str(caught.exception))

    def test_length_is_a_band_because_the_model_decides_it(self):
        scene = {"id": "s3", "name": "band", "prompt": "folk", "prompt_skill_id": "",
                 "params": server.clamp_music_params({"lyrics": "[Verse]\nline one two three\n[Chorus]\nfade"})}
        audit = server.music_request_from_scene(scene)["audit"]
        low, high = audit["estimated_band"]
        self.assertLessEqual(low, high)
        self.assertLess(low, high)   # never a single confident number

    def test_scene_request_compilation_is_deterministic(self):
        scene = {"id": "s1", "name": "Kettle", "prompt": "piano pop", "prompt_skill_id": "",
                 "params": server.clamp_music_params({"lyrics": LYRICS, "seed": 42, "plan_mode": "full"})}
        first = server.music_request_from_scene(scene)["request"]
        second = server.music_request_from_scene(scene)["request"]
        self.assertEqual(first, second)
        self.assertEqual(first["cot"], "full")
        self.assertEqual(first["id"], "kettle")


class Registry(unittest.TestCase):
    def test_music_backend_declares_its_medium(self):
        backend = next(item for item in server.MODEL_BACKENDS if item["id"] == "yue2")
        self.assertEqual(backend["media"], "music")
        self.assertEqual(backend["role"], "music_generation")
        self.assertNotIn("vram_min", backend)   # MPS has no VRAM to require
        self.assertEqual("openmagia-yue-worker/" + server.YUE_WORKER_VERSION, yue_worker.VERSION)

    def test_management_state_reports_a_music_source(self):
        state = server.model_management_state()
        self.assertIn("music", state)
        self.assertIn(state["music"]["selection"]["mode"], ("local", "endpoint", "none"))
        self.assertTrue(any(item.get("media") == "music" for item in state["catalog"]))
        if server.yue_local_runtime()["installed"]:
            installation = next(item for item in state["installations"] if item.get("backend_id") == "yue2")
            self.assertTrue(installation["managed"])
            self.assertIn(str(server.YUE_LOCAL_DIR.resolve()), installation["receipt"])

    def test_unsupported_controls_are_listed_rather_than_built(self):
        listed = " ".join(server.yue_music_state()["unsupported"]).lower()
        for truth in ("duration", "reference audio", "stems"):
            self.assertIn(truth, listed)

    def test_music_is_installable_from_the_shared_models_endpoint(self):
        source = Path(server.__file__).read_text(encoding="utf-8")
        self.assertIn('(\"h3\", \"formatter\", \"runtime\", \"yue\")', source)

    def test_generate_and_cast_are_sibling_panels(self):
        html = (Path(server.__file__).parent / "index.html").read_text(encoding="utf-8")
        generate_end = html.index("<!-- cast -->")
        self.assertGreaterEqual(html[html.index('id="generate"'):generate_end].count("</div>"), 4)

    def test_music_reuses_model_and_skill_components(self):
        root = Path(server.__file__).parent
        html = (root / "index.html").read_text(encoding="utf-8")
        script = (root / "app.js").read_text(encoding="utf-8")
        self.assertIn('id="activeMusicSkill"', html)
        self.assertIn('id="musicSkillBtn"', html)
        self.assertNotIn('class="musicSkills"', html)
        self.assertNotIn('const musicCard=', script)
        self.assertIn("musicSkillMode", script)
        self.assertIn('class="musicAdvanced"', html)
        self.assertIn('class="sheetPanel modelUninstallPanel musicLyricsPanel"', html)
        self.assertNotIn('id="musicManageBtn"', html)
        self.assertIn("m.kind==='audio'", script)
        self.assertIn("musicCompileFingerprint", script)
        self.assertIn("generateScrollTop", script)
        self.assertNotIn('class="musicCompileHead"', script)
        self.assertIn('class="musicValidationError"', script)
        self.assertLess(script.index("'/api/music/preview'", script.index('async function generateMusic')),
                        script.index("'/api/scenes'", script.index('async function generateMusic')))
        self.assertNotIn("Add a video clip to the timeline first", script)
        css = (root / "style.css").read_text(encoding="utf-8")
        self.assertIn(".mtile.sel{border-color:#8b5cf6", css)


class Skills(unittest.TestCase):
    def test_music_skills_exist_with_contracts(self):
        music = [item for item in server.skill_catalog() if item.get("type") == "music"]
        self.assertEqual({item["id"] for item in music},
                         {"song-director", "score-underscore", "album-identity"})
        for item in music:
            contract = server.compile_skill_contract(item["id"])
            self.assertTrue(contract["refinement_direction"])
            self.assertTrue(contract["music_direction"])
            self.assertFalse(contract["visual_direction"])

    def test_music_refiner_fallback_is_media_specific(self):
        original = server.FORMATTER_MODEL
        try:
            server.FORMATTER_MODEL = "/missing/refiner.gguf"
            result = server.refine_music_brief("warm piano pop", "[Verse]\nKeep these words", "song-director")
        finally:
            server.FORMATTER_MODEL = original
        self.assertFalse(result["used_ai"])
        self.assertEqual(result["lyrics"], "[Verse]\nKeep these words")
        self.assertIn("warm piano pop", result["style"])

    def test_music_refiner_infers_instrumental_controls_from_plain_language(self):
        with mock.patch.object(server, "formatter_available", return_value=False):
            result = server.refine_music_brief("Lo-fi piano, instrumental, no lead vocal")
        self.assertTrue(result["instrumental"])
        self.assertEqual("full", result["plan_mode"])
        self.assertEqual("[Instrumental]", result["lyrics"])

    def test_lyric_editor_drafts_and_revises_for_review(self):
        drafted = SimpleNamespace(returncode=0, stderr="", stdout='{"lyrics":"[Verse]\\nRoad home\\n[Chorus]\\nCarry me"}')
        with mock.patch.object(server, "formatter_available", return_value=True), \
             mock.patch.object(server, "run_formatter_command", return_value=drafted) as formatter:
            result = server.refine_music_lyrics("warm folk journey")
        self.assertTrue(result["used_ai"])
        self.assertIn("[Chorus]", result["lyrics"])
        prompt = formatter.call_args.args[0][formatter.call_args.args[0].index("-p") + 1]
        self.assertIn("Draft concise", prompt)

        revised = SimpleNamespace(returncode=0, stderr="", stdout='{"lyrics":"[Verse]\\nA brighter line"}')
        with mock.patch.object(server, "formatter_available", return_value=True), \
             mock.patch.object(server, "run_formatter_command", return_value=revised) as formatter:
            result = server.refine_music_lyrics("bright pop", "[Verse]\nAn old line")
        self.assertIn("A brighter line", result["lyrics"])
        prompt = formatter.call_args.args[0][formatter.call_args.args[0].index("-p") + 1]
        self.assertIn("Rewrite and improve", prompt)

    def test_music_title_is_generated_by_the_refinement_model(self):
        titled = SimpleNamespace(returncode=0, stderr="", stdout='{"title":"Neon Afterglow"}')
        with mock.patch.object(server, "formatter_available", return_value=True), \
             mock.patch.object(server, "run_formatter_command", return_value=titled) as formatter:
            result = server.generate_music_title("cinematic electronic demo music", "")
        self.assertEqual(result, {"title": "Neon Afterglow", "used_ai": True})
        prompt = formatter.call_args.args[0][formatter.call_args.args[0].index("-p") + 1]
        self.assertIn("two to six words", prompt)

    def test_music_title_does_not_fall_back_to_the_prompt(self):
        with mock.patch.object(server, "formatter_available", return_value=False):
            result = server.generate_music_title("This entire prompt must not become the title")
        self.assertEqual(result, {"title": "", "used_ai": False})

    def test_installed_runtime_requires_both_checkpoint_caches(self):
        runtime = server.yue_local_runtime()
        self.assertIn("installed", runtime)
        self.assertIn("ready", runtime)
        self.assertEqual(runtime["ready"], runtime["installed"] and all(runtime["weights"].values()))


if __name__ == "__main__":
    unittest.main()
