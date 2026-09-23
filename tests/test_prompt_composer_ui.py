from pathlib import Path
import unittest


ROOT = Path(__file__).parents[1]


class PromptComposerUiTests(unittest.TestCase):
    def test_shared_style_is_collapsed_and_distinct_from_scene_prompt(self):
        html = (ROOT / "index.html").read_text(encoding="utf-8")
        self.assertIn('<section class="projectStyleCard" id="sharedStyleDetails">', html)
        self.assertIn('SHARED STYLE', html)
        self.assertIn('<span>PROMPT</span>', html)
        self.assertNotIn('SCENE PROMPT', html)
        video_composer = html[html.index('id="videoGenerationControls"'):html.index('id="musicGenerationControls"')]
        self.assertNotIn('<span class="scopeLabel">DIRECTION</span>', video_composer)
        self.assertIn('Applied to every scene', html)
        self.assertNotIn('Refine shared style', html)
        self.assertNotIn('id="projectStyleToggle"', html)
        self.assertIn('id="sharedStyleBody" hidden', html)
        self.assertNotIn('saved but not applied</small>', html)

    def test_refine_saves_style_draft_before_opening_sheet(self):
        script = (ROOT / "app.js").read_text(encoding="utf-8")
        handler = script[script.index("$('#styleRefineBtn').addEventListener"):]
        save = handler.index("await api('/api/project'")
        open_sheet = handler.index("openPromptSheet('style')")
        self.assertLess(save, open_sheet)
        self.assertIn("state.style_profile=profile;state.base_prompt=prompt", handler[:open_sheet])

    def test_music_composer_uses_padded_direction_body_and_outlined_lyrics(self):
        html = (ROOT / "index.html").read_text(encoding="utf-8")
        css = (ROOT / "style.css").read_text(encoding="utf-8")
        self.assertIn('class="projectStyleCard musicDirectionCard"', html)
        self.assertIn('class="musicDirectionBody"', html)
        self.assertIn('#musicGenerationControls .musicDirectionBody{padding:0 11px 11px}', css)
        self.assertIn('#musicGenerationControls #musicLyrics{', css)
        self.assertIn('border:1px solid #34343a!important', css)

    def test_browser_rejects_shared_style_that_drops_authored_subject(self):
        script = (ROOT / "app.js").read_text(encoding="utf-8")
        self.assertIn("function sharedStylePreservesSource(source,candidate)", script)
        self.assertIn("if(!sharedStylePreservesSource(authored,prompt))", script)
        self.assertIn("OpenMagia kept your original shared style", script)


if __name__ == "__main__":
    unittest.main()
