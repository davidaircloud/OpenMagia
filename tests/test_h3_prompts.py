import unittest

from h3_prompts import (MAX_FRAMES, PRESETS, SHEET_RECIPES, duration_for_frames,
                        analyze_cut_timeline, format_prompt, format_image_prompt, format_sheet_prompt, sheet_extract_times,
                        validate_references)


class H3PromptTests(unittest.TestCase):
    def test_image_prompt_has_still_grammar_without_audio_or_timing(self):
        prompt = format_image_prompt(idea="Maria on a rainy rooftop.", style="Dim streetlights.",
                                     answers={"camera": "50mm portrait", "text": "OPEN"})
        self.assertIn("Create one finished still image", prompt)
        self.assertIn("Hold the same composition across all five decoded frames", prompt)
        self.assertIn("OPEN", prompt)
        self.assertNotIn("overall_soundscape", prompt)
        self.assertNotIn("CUT 01", prompt)

    def test_fifteen_second_limit(self):
        self.assertEqual((360, 15.0), duration_for_frames(999))
        self.assertEqual(360, MAX_FRAMES)

    def test_base_field_order(self):
        prompt = format_prompt(idea="A runner stops.", style="Cinematic.", frames=360)
        fields = [prompt.index(name) for name in (
            "integrated_multimodal_description:", "overall_soundscape:", "non_diegetic_music:")]
        self.assertEqual(fields, sorted(fields))
        self.assertIn("by 15.00 seconds", prompt)
        self.assertNotIn("[Shot 1] At", prompt)
        self.assertTrue(prompt.startswith("integrated_multimodal_description: [Shot 1] Cinematic."))

    def test_ref_field_order_and_labels(self):
        chars = [{"name": "AO", "paths": ["a.png", "b.png"]}]
        prompt = format_prompt(idea="AO jumps.", style="Anime.", frames=312,
                               mode="ref2va", characters=chars)
        fields = [prompt.index(name) for name in (
            "subject_definitions:", "summary:", "retention_analysis:",
            "detailed_description:", "overall_soundscape:", "non_diegetic_music:")]
        self.assertEqual(fields, sorted(fields))
        self.assertIn("<Picture 1>", prompt)
        self.assertIn("<Picture 2>", prompt)
        self.assertIn("<Subject 1>", prompt)
        self.assertIn("[reference generation]", prompt)
        self.assertIn("fully_preserved", prompt)

    def test_fast_pacing_uses_strictly_increasing_h3_cut_times(self):
        prompt = format_prompt(idea="A product is revealed.", style="Premium studio film.",
                               frames=360, answers={"pacing": "fast beat-driven"})
        self.assertIn("[Shot 2] At 00:05.000", prompt)
        self.assertIn("[Shot 3] At 00:10.000", prompt)
        self.assertNotIn("[Shot 1] 0.00", prompt)
        self.assertNotIn("CUT 01", prompt)

    def test_ref2va_uses_only_official_subject_labels_and_retention_markers(self):
        refs = [{"name": "Park palette", "paths": ["park.png"], "kind": "visual_reference"}]
        prompt = format_prompt(idea="A child walks.", style="Anime.", frames=120,
                               mode="ref2va", characters=refs)
        self.assertIn("<Subject 1>", prompt)
        self.assertIn("partially_preserved", prompt)
        self.assertNotIn("<Reference 1>", prompt)
        self.assertNotIn("selectively_preserved", prompt)

    def test_i2va_uses_exact_opening_frame_anchor_before_shared_schema(self):
        prompt = format_prompt(idea="Continue the runner's next stride.", style="Cinematic.",
                               frames=120, mode="i2va",
                               answers={"continuity": "Preserve camera axis and motion direction."})
        self.assertTrue(prompt.startswith(
            "For the target video, at 0.00 seconds into the target video, <Picture 1> (from [Shot 1]) is fully referenced."))
        self.assertIn("integrated_multimodal_description:", prompt)
        self.assertIn("Preserve camera axis and motion direction.", prompt)
        self.assertNotIn("subject_definitions:", prompt)

    def test_prompt_skill_direction_is_applied(self):
        prompt = format_prompt(idea="A title appears.", style="Graphic.", frames=96,
                               answers={"skill_instruction": "Preserve visible copy verbatim."})
        self.assertIn("Production direction: Preserve visible copy verbatim.", prompt)

    def test_authored_cut_timeline_is_preserved_without_generic_nested_cuts(self):
        authored = ("CUT 01 | 0.00-2.00s - A performer enters.\n"
                    "CUT 02 | 2.00-4.00s - Exact title appears.")
        prompt = format_prompt(idea=authored, style="Graphic.", frames=96,
                               answers={"pacing": "fast", "cuts": 8})
        self.assertEqual(prompt.count("CUT 01"), 1)
        self.assertEqual(prompt.count("CUT 02"), 1)
        self.assertNotIn("Create exactly 8 distinct cuts", prompt)

    def test_authored_cut_timeline_rejects_gaps_and_wrong_duration(self):
        result = analyze_cut_timeline("CUT 01 | 0.00-1.00s - A.\nCUT 02 | 1.50-3.00s - B.", 4)
        self.assertTrue(result["errors"])
        with self.assertRaisesRegex(ValueError, "Invalid authored CUT timeline"):
            format_prompt(idea="CUT 01 | 0.00-1.00s - A.\nCUT 02 | 1.50-3.00s - B.",
                          style="Graphic.", frames=96)

    def test_nine_reference_limit(self):
        validate_references([{"paths": list(range(9))}])
        with self.assertRaisesRegex(ValueError, "at most 9"):
            validate_references([{"paths": list(range(10))}])

    def test_audio_reference_requires_visual_and_uses_official_labels(self):
        audio = {"name": "Narrator", "paths": ["voice.wav"], "kind": "audio_reference",
                 "duration": 5, "description": "use its voice timbre and dialogue timing"}
        with self.assertRaisesRegex(ValueError, "at least one image or video"):
            validate_references([audio])
        refs = [{"name": "Speaker", "paths": ["speaker.png"]}, audio]
        validate_references(refs)
        prompt = format_prompt(idea="The speaker delivers one line.", style="Cinematic.", frames=120,
                               mode="ref2va", characters=refs)
        self.assertIn("<Picture 1>", prompt)
        self.assertIn("<Audio 1> is Narrator", prompt)
        self.assertIn("voice timbre", prompt)

    def test_audio_reference_limits(self):
        visual = {"name": "Subject", "paths": ["subject.png"]}
        with self.assertRaisesRegex(ValueError, "between 2 and 15"):
            validate_references([visual, {"name":"Short", "paths":["a.wav"], "kind":"audio_reference", "duration":1}])
        with self.assertRaisesRegex(ValueError, "total at most 15"):
            validate_references([visual,
                {"name":"A", "paths":["a.wav"], "kind":"audio_reference", "duration":8},
                {"name":"B", "paths":["b.wav"], "kind":"audio_reference", "duration":8}])

    def test_preset_catalog(self):
        ids = {p["id"] for p in PRESETS}
        self.assertGreaterEqual(len(ids), 8)
        self.assertTrue({"motion-graphics", "fast-trailer", "social-reel", "motion-typography"} <= ids)


class SheetPromptTests(unittest.TestCase):
    SHEET_FIELDS = ("subject_definitions:", "summary:", "retention_analysis:",
                    "detailed_description:", "overall_soundscape:", "non_diegetic_music:")

    def test_field_order_matches_official_ref2va_structure(self):
        prompt = format_sheet_prompt(name="Knight", identity="A weathered knight.",
                                     references=["keep the helmet, ignore the background"])
        positions = [prompt.index(f) for f in self.SHEET_FIELDS]
        self.assertEqual(positions, sorted(positions))

    def test_reference_notes_are_preserved_verbatim(self):
        note = "keep the black coat with silver buckles, ignore the person wearing it"
        prompt = format_sheet_prompt(references=[{"keep": note}, ""])
        self.assertIn(note, prompt)
        self.assertIn("<Picture 1>", prompt)
        self.assertIn("<Picture 2>", prompt)
        self.assertIn("fully_preserved", prompt)

    def test_nine_reference_limit(self):
        format_sheet_prompt(references=[f"ref {i}" for i in range(9)])
        with self.assertRaisesRegex(ValueError, "at most 9"):
            format_sheet_prompt(references=[f"ref {i}" for i in range(10)])

    def test_recipes_carry_frames_scripts_and_extract_times(self):
        for recipe in SHEET_RECIPES:
            self.assertLessEqual(recipe["frames"], MAX_FRAMES)
            self.assertIn("[0.00-", recipe["script"])
            self.assertTrue(recipe["extract"])
            for t, label in sheet_extract_times(recipe["id"]):
                self.assertLess(t, recipe["frames"] / 24)
                self.assertTrue(label)

    def test_full_turn_takes_three_quarter_from_verified_orbit(self):
        views = dict((label, t) for t, label in sheet_extract_times("turn-6"))
        self.assertLess(views["three-quarter"], views["left side"])
        self.assertGreater(views["front face"], views["right side"])
        self.assertNotIn("three-quarter face", views)

    def test_silent_sheet_and_staging_contract(self):
        prompt = format_sheet_prompt(recipe="turn-4", style="live-action")
        self.assertIn("seamless backdrop", prompt)
        self.assertIn("Only the camera moves", prompt)
        self.assertIn("Silence", prompt)
        self.assertIn("None.", prompt)
        self.assertIn("3.00-second", prompt)

    def test_sheet_staging_is_species_neutral_and_temporally_stable(self):
        prompt = format_sheet_prompt(name="Kiko", identity="A golden dog.", references=["golden coat"])
        self.assertIn("A quadruped stands naturally", prompt)
        self.assertIn("no flicker", prompt)
        self.assertIn("no external subject references", format_sheet_prompt())
        self.assertNotIn("rough design input", prompt)


if __name__ == "__main__":
    unittest.main()
