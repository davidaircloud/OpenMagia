import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import server


class SkillContractTests(unittest.TestCase):
    def test_catalog_is_filesystem_backed_and_complete(self):
        catalog = server.skill_catalog()
        self.assertEqual(17, len(catalog))
        self.assertEqual(len(catalog), len({item["id"] for item in catalog}))
        for item in catalog:
            self.assertTrue((server.SKILL_ROOT / item["id"] / "SKILL.md").is_file())
            self.assertTrue(item["instruction"].strip())
            self.assertTrue(item["contract"]["invariants"])
            self.assertTrue(item["contract"]["required"])
            self.assertTrue(item["contract"]["forbidden"])

    def test_compiler_exposes_machine_contract_not_markdown_manual(self):
        direction = server.compiled_skill_direction("kinetic-type-conductor")
        self.assertIn("exactly two approved phrases", direction)
        self.assertIn("Must include:", direction)
        self.assertIn("Must not:", direction)
        self.assertNotIn("# Kinetic", direction)

    def test_structured_prompt_cannot_bypass_selected_skill(self):
        prompt = (
            "integrated_multimodal_description: [Shot 1] At 00:00.000, a performer enters. "
            "overall_soundscape: Footsteps. non_diegetic_music: A restrained beat."
        )
        prepared = server.apply_skill_contract_to_structured(
            prompt, "kinetic-type-conductor", 5.0
        )
        self.assertIn("Skill direction (kinetic-type-conductor@", prepared)
        self.assertIn("exactly two approved phrases", prepared)

    def test_visual_compiler_does_not_send_process_rules_to_h3(self):
        compiled = server.compile_skill_contract("kinetic-type-conductor")
        self.assertIn("Must include:", compiled["refinement_direction"])
        self.assertNotIn("official H3 field", compiled["visual_direction"])
        self.assertTrue(compiled["version"])

    def test_missing_selected_skill_fails_clearly_but_empty_skill_is_safe(self):
        self.assertIsNone(server.compile_skill_contract(""))
        with self.assertRaisesRegex(ValueError, "Selected skill 'missing'"):
            server.compile_skill_contract("missing")

    def test_catalog_reports_bad_entry_without_hiding_valid_entries(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / "good").mkdir()
            (root / "good" / "SKILL.md").write_text(
                "---\nname: Authoritative name\ndescription: Authoritative description.\n---\nBody\n"
            )
            catalog = root / "catalog.json"
            catalog.write_text(json.dumps([
                {"id": "good", "name": "stale", "description": "stale", "instruction": "Direct it.",
                 "contract": {"invariants": ["Keep it."], "required": [], "forbidden": []}},
                {"id": "broken", "instruction": "", "contract": {}},
            ]))
            with mock.patch.object(server, "SKILL_ROOT", root), mock.patch.object(server, "SKILL_CATALOG_FILE", catalog):
                server._skill_catalog_cache.update(signature=None, report=None)
                report = server.skill_catalog_report()
            server._skill_catalog_cache.update(signature=None, report=None)
            self.assertEqual(["good"], [item["id"] for item in report["skills"]])
            self.assertEqual("Authoritative name", report["skills"][0]["name"])
            self.assertEqual(1, len(report["errors"]))

    def test_invalid_authored_timing_fails_before_skill_application(self):
        prompt = (
            "integrated_multimodal_description: CUT 01 | 0.00-1.00s - Enter. "
            "CUT 02 | 2.00-3.00s - Stop. overall_soundscape: Steps. "
            "non_diegetic_music: None."
        )
        with self.assertRaisesRegex(ValueError, "conflicts with its selected duration"):
            server.apply_skill_contract_to_structured(prompt, "h3-prompt", 4.0)

    def test_pov_contract_rejects_external_camera_language(self):
        with self.assertRaisesRegex(ValueError, "POV refinement attempted"):
            server.validate_skill_prompt_integrity(
                "The camera follows the courier and then shows her body.", "pov-film"
            )

    def test_pov_formatter_conflict_falls_back_to_literal_eye_view(self):
        repaired = server.enforce_skill_expansion(
            "Camera pulls back to frame the courier's body.",
            "Climb the fire escape while carrying the parcel.",
            "pov-film", 5.0,
        )
        self.assertIn("literal first-person", repaired)
        self.assertIn("never visible externally", repaired)
        self.assertIn("Climb the fire escape", repaired)

    @mock.patch.object(server, "formatter_available", return_value=False)
    def test_refinement_fallback_does_not_invent_demo_characters(self, _available):
        expanded, used_ai = server.improve_idea_locally(
            "A driver jumps over a puddle.",
            {"_duration_seconds": 5, "skill_instruction": "Preserve the named driver."},
        )
        self.assertFalse(used_ai)
        self.assertNotIn("Lokillo", expanded)
        self.assertNotIn("car_jump", expanded)


if __name__ == "__main__":
    unittest.main()
