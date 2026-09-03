import unittest
import tempfile
import json
import threading
from types import SimpleNamespace
from pathlib import Path
from unittest import mock

import nle
import server


class TimelineCompositionTests(unittest.TestCase):
    def test_h3_reference_args_keep_audio_and_images_distinct(self):
        refs = [
            {"kind": "visual_reference", "paths": [Path("look.png")]},
            {"kind": "audio_reference", "paths": [Path("rhythm.wav")]},
        ]
        self.assertEqual(server.h3_reference_args(refs), [
            "--ref-image", "look.png", "--ref-audio", "rhythm.wav",
        ])
        self.assertEqual(server.visual_reference_count(refs), 1)

    def test_default_tracks_place_overlay_above_video(self):
        self.assertEqual([track["id"] for track in server.default_tracks()], ["V2", "V1", "A1"])

    def test_export_cover_zoom_is_applied_after_plain_cover(self):
        filters = nle._cover_vf(2.073, {"width": 512, "height": 512})
        self.assertEqual(filters[:2], ["scale=512:512:force_original_aspect_ratio=increase", "crop=512:512"])
        self.assertIn("scale=1060:1060", filters)
        self.assertIn("crop=512:512:(iw-512)/2:(ih-512)/2", filters)
        self.assertIn("pad=512:512:(ow-iw)/2:(oh-ih)/2:black",
                      nle._cover_vf(.5, {"width": 512, "height": 512}))
        self.assertEqual(nle._position_vf({"position": {"x": -22, "y": 10}}, {"width": 512, "height": 512}),
                         ["pad=1536:1536:399.360000:563.200000:black", "crop=512:512:512:512"])

    def test_export_color_filter_covers_every_inspector_control(self):
        filters = ",".join(nle._color_vf({"color": {"enabled": True, "exposure": .5,
            "contrast": 1.2, "saturation": .8, "temperature": .3, "tint": -.2,
            "highlights": .4, "shadows": -.1}}))
        self.assertIn("eq=brightness=", filters)
        self.assertIn(":contrast=1.2000:saturation=0.8000:", filters)
        self.assertIn("colorbalance=", filters)

    def test_export_blur_and_masks_match_inspector_metadata(self):
        self.assertIn("boxblur=luma_radius=12", ",".join(nle._blur_vf(
            {"blur": {"enabled": True, "amount": 12}})))
        self.assertEqual([], nle._blur_vf({"blur": {"enabled": False, "amount": 12}}))
        rectangle = nle._mask_condition({"mask": {"type": "rectangle", "x": 50, "y": 50,
                                                     "width": 50, "height": 40}},
                                        {"width": 800, "height": 600})
        self.assertIn("gte(X,200.000000)", rectangle)
        self.assertIn("lte(Y,420.000000)", rectangle)
        ellipse = nle._mask_condition({"mask": {"type": "ellipse", "invert": True}},
                                      {"width": 800, "height": 600})
        self.assertTrue(ellipse.startswith("1-(lte("))
        self.assertIn("abs((X-", nle._mask_condition({"mask": {"type": "diamond"}},
                                                     {"width": 800, "height": 600}))
        self.assertIn("atan2", nle._mask_condition({"mask": {"type": "star"}},
                                                   {"width": 800, "height": 600}))
        self.assertIn("^3", nle._mask_condition({"mask": {"type": "heart"}},
                                                {"width": 800, "height": 600}))
        self.assertTrue(nle._mask_condition({"mask": {"type": "split", "x": 40}},
                                            {"width": 800, "height": 600}).startswith("lte(X,320"))

    def test_export_video_transform_keyframes_match_inspector_animation(self):
        clip = {"id": "animated", "in": 0, "out": 5, "zoom": 1,
                "keyframes": {"enabled": True, "points": [
                    {"at": 0, "zoom": 1.12, "x": .5, "y": .5},
                    {"at": .2, "zoom": 1, "x": .5, "y": .5},
                    {"at": 1, "zoom": .94, "x": .5, "y": .5},
                ]}}
        filters = nle._transform_keyframe_vf(clip, {"width": 512, "height": 512})
        self.assertEqual(filters[:2], ["scale=1024:1024:force_original_aspect_ratio=increase", "crop=1024:1024"])
        self.assertIn("zoompan=", filters[2])
        self.assertIn(":d=1:", filters[2])
        self.assertIn("1.120000", filters[2])
        self.assertIn("0.940000", filters[2])

        commands = []
        def fake_run(command):
            commands.append(command)
            Path(command[-1]).touch()
            return SimpleNamespace(returncode=0, stderr="", stdout="")
        with tempfile.TemporaryDirectory() as tmp, mock.patch.object(nle, "run", side_effect=fake_run):
            source = Path(tmp) / "source.mp4"; source.touch()
            nle.render_video_pre(clip, {"src": str(source), "kind": "video"},
                                 {"width": 512, "height": 512}, tmp)
        vf = commands[0][commands[0].index("-vf") + 1]
        self.assertIn("zoompan=", vf)

    def test_export_uses_last_active_lane_endpoint_and_overlay_zoom_mode(self):
        project = {
            "canvas": {"width": 512, "height": 512},
            "media": [
                {"id": "m1", "src": "media/one.mp4", "kind": "video", "hasAudio": False},
                {"id": "m2", "src": "media/two.mp4", "kind": "video", "hasAudio": False},
                {"id": "m3", "src": "media/three.mp4", "kind": "video", "hasAudio": False},
            ],
            "tracks": [
                {"id": "V2", "kind": "video", "name": "Overlay", "muted": False, "clips": [
                    {"id": "overlay", "mediaId": "m3", "start": 9.75, "in": 0.0, "out": 2.0, "zoom": 2.073,
                     "mask": {"type": "ellipse", "x": 50, "y": 50, "width": 70, "height": 70},
                     "transition": {"items": [{"id": "end", "type": "dissolve", "edge": "end",
                                                   "dur": 1.226, "enabled": True}]}},
                ]},
                {"id": "V1", "kind": "video", "name": "Video", "muted": False, "clips": [
                    {"id": "base1", "mediaId": "m1", "start": 0.0, "in": 0.0, "out": 5.0, "zoom": 1.0},
                    {"id": "base2", "mediaId": "m2", "start": 5.25, "in": 0.0, "out": 5.0, "zoom": 1.0},
                ]},
            ],
        }
        commands = []

        def fake_render(clip, media, canvas, outdir, mode="cover"):
            target = Path(outdir) / (clip["id"] + ".mp4")
            target.touch()
            return target

        def fake_run(command):
            commands.append(command)
            Path(command[-1]).touch()
            return SimpleNamespace(returncode=0, stderr="", stdout="")

        with tempfile.TemporaryDirectory() as tmp, \
                mock.patch.object(nle, "render_video_pre", side_effect=fake_render) as render, \
                mock.patch.object(nle, "probe", return_value={"duration": 0, "w": 0, "h": 0, "hasAudio": False}), \
                mock.patch.object(nle, "run", side_effect=fake_run):
            (Path(tmp) / "media").mkdir()
            with mock.patch.object(nle.time, "strftime", return_value="20260831-003000"):
                url = nle.export_project(project, tmp)

        modes = {call.args[0]["id"]: call.kwargs["mode"] for call in render.call_args_list}
        self.assertEqual(modes, {"overlay": "contain", "base1": "cover", "base2": "cover"})
        final = commands[-1]
        self.assertEqual(final[final.index("-t") + 1], "11.750")
        self.assertEqual(url, "/media/OpenMagia-project-20260831-003000.mp4")
        self.assertEqual(Path(final[-1]).name, "OpenMagia-project-20260831-003000.mp4")
        graph = final[final.index("-filter_complex") + 1]
        self.assertIn("d=0.250000", graph)
        self.assertIn("tpad=stop_mode=add:stop_duration=1.500000", graph)
        self.assertIn("fade=t=out:st=0.774000:d=1.226000:alpha=1", graph)
        self.assertIn("a='alpha(X,Y)*(lte(", graph)

    def test_project_library_exposes_creation_and_modified_dates(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            project_dir = root / "film"
            project_dir.mkdir()
            (project_dir / "project.json").write_text(json.dumps({"name": "Film", "created": 1234.5, "media": [], "scenes": []}))
            with mock.patch.object(server, "PROJECTS", root), mock.patch.object(server, "active_slug", return_value="film"):
                projects = server.list_projects()
            self.assertEqual(projects[0]["created"], 1234.5)
            self.assertGreater(projects[0]["updated"], 0)

    def test_storyboard_batch_links_each_continuation_to_predecessor_media(self):
        project = server.new_project()
        project["slug"] = "story"
        payload = {
            "id": "board-1",
            "style_profile": {"name": "World", "prompt": "stable salt world", "source": "custom"},
            "output": {"width": 896, "height": 512, "frames": 120, "steps": 12,
                       "quality": "balanced", "layers": 45, "reuse": 2, "seed": 24017},
            "scenes": [
                {"name": "Arrival", "prompt": "Scene one", "continue_previous": False},
                {"name": "Bridge", "prompt": "Scene two", "continue_previous": True},
                {"name": "Outpost", "prompt": "Scene three", "continue_previous": False},
            ],
        }
        scenes = server.create_storyboard_batch(project, payload)
        self.assertEqual(len(scenes), 3)
        self.assertEqual(len(project["media"]), 3)
        self.assertIsNone(scenes[0]["source_media_id"])
        self.assertEqual(scenes[1]["source_media_id"], project["media"][0]["id"])
        self.assertEqual(scenes[1]["depends_on_scene_id"], scenes[0]["id"])
        self.assertIsNone(scenes[2]["source_media_id"])
        self.assertTrue(all(scene["status"] == "queued" for scene in scenes))
        self.assertEqual(project["style_profile"]["prompt"], "stable salt world")
        self.assertEqual(project["canvas"], {"width": 896, "height": 512})
        self.assertIn("physical sound effects", scenes[0]["guide_answers"]["sound"])

    def test_storyboard_batch_requires_two_complete_scene_prompts(self):
        project = server.new_project()
        with self.assertRaisesRegex(ValueError, "at least two"):
            server.create_storyboard_batch(project, {"scenes": [{"prompt": "Only one"}]})
        with self.assertRaisesRegex(ValueError, "Scene 2"):
            server.create_storyboard_batch(project, {"scenes": [{"prompt": "Ready"}, {"prompt": ""}]})

    def test_storyboard_batch_advances_automatic_scene_names(self):
        project = server.new_project()
        project["scenes"] = [{"id": "old", "name": "Scene 6"}]
        project["media"] = [{"id": "older", "name": "Scene 8", "kind": "video"}]
        scenes = server.create_storyboard_batch(project, {"scenes": [
            {"name": "Scene 1", "prompt": "First new scene", "continue_previous": False},
            {"name": "Scene 2", "prompt": "Second new scene", "continue_previous": True},
        ]})
        self.assertEqual([scene["name"] for scene in scenes], ["Scene 9", "Scene 10"])

    def test_storyboard_batch_preserves_authored_scene_titles(self):
        project = server.new_project()
        project["scenes"] = [{"id": "old", "name": "Scene 1"}]
        scenes = server.create_storyboard_batch(project, {"scenes": [
            {"name": "Arrival", "prompt": "First new scene", "continue_previous": False},
            {"name": "Payoff", "prompt": "Second new scene", "continue_previous": True},
        ]})
        self.assertEqual([scene["name"] for scene in scenes], ["Arrival", "Payoff"])

    def test_storyboard_optimizer_splits_long_scenes_and_preserves_references(self):
        card = {"id": "one", "name": "Ride", "prompt": "The family crosses town.",
                "character_ids": ["family"], "character_reference_ids": {"family": ["front"]},
                "reference_media_ids": ["taxi"], "continue_previous": False}
        result = server.optimize_storyboard_scenes({"frames": 360, "scenes": [card]}, use_model=False)
        self.assertEqual(len(result["scenes"]), 3)
        self.assertEqual([scene["params"]["frames"] for scene in result["scenes"]], [120, 120, 120])
        self.assertFalse(result["scenes"][0]["continue_previous"])
        self.assertTrue(result["scenes"][1]["continue_previous"])
        self.assertEqual(result["scenes"][2]["character_reference_ids"], {"family": ["front"]})
        self.assertIn("exact final frame", result["scenes"][1]["prompt"])

    def test_storyboard_optimizer_keeps_short_scene_duration(self):
        result = server.optimize_storyboard_scenes({"frames": 96, "scenes": [
            {"id": "one", "name": "Short", "prompt": "A short action."},
        ]}, use_model=False)
        self.assertEqual(len(result["scenes"]), 1)
        self.assertEqual(result["scenes"][0]["params"]["frames"], 96)

    def test_storyboard_optimizer_fallback_removes_original_long_timestamps(self):
        prompt = ("subject_definitions: no references.\n"
                  "detailed_description: CUT 01 | 0.00-5.00s — Leave home. "
                  "CUT 02 | 5.00-10.00s — Cross the bridge. "
                  "CUT 03 | 10.00-15.00s — Reach school.\n"
                  "overall_soundscape: road.\nnon_diegetic_music: none.")
        result = server.optimize_storyboard_scenes({"frames": 360, "scenes": [{"prompt": prompt}]}, use_model=False)
        self.assertEqual(len(result["scenes"]), 3)
        self.assertNotIn("10.00-15.00", result["scenes"][0]["prompt"])
        self.assertIn("Leave home", result["scenes"][0]["prompt"])
        self.assertIn("Reach school", result["scenes"][2]["prompt"])

    def test_magia_uses_constant_five_second_blocks_and_final_remainder(self):
        project = server.new_project()
        result = server.make_magia_storyboard({"idea": "A courier crosses the city and delivers a parcel.",
            "duration_seconds": 63, "optimize_five_seconds": True}, project, use_model=False)
        self.assertEqual(len(result["scenes"]), 13)
        self.assertEqual([scene["params"]["frames"] for scene in result["scenes"][:-1]], [120] * 12)
        self.assertEqual(result["scenes"][-1]["params"]["frames"], 72)
        self.assertFalse(result["scenes"][0]["continue_previous"])
        self.assertTrue(all(scene["continue_previous"] for scene in result["scenes"][1:]))

    def test_magia_defaults_to_fifteen_second_blocks_and_copies_context(self):
        project = server.new_project()
        project["characters"] = [{"id": "hero", "name": "Nima", "description": "", "images": []}]
        result = server.make_magia_storyboard({"idea": "Nima explores a cavern.", "duration_seconds": 33,
            "context": {"character_ids": ["hero"], "character_reference_ids": {"hero": ["front"]},
                        "reference_media_ids": ["map"], "prompt_skill_id": "h3-prompt"}}, project, use_model=False)
        self.assertEqual([scene["params"]["frames"] for scene in result["scenes"]], [360, 360, 72])
        self.assertEqual(result["scenes"][2]["character_ids"], ["hero"])
        self.assertEqual(result["scenes"][1]["prompt_skill_id"], "h3-prompt")

    def test_magia_fallback_builds_complete_story_beats_instead_of_word_fragments(self):
        idea = ("I want a 20s story about a kid that finds her best friend who is a dog in the park. "
                "The dog was sad but then happy to see her. Anime style.")
        result = server.make_magia_storyboard({"idea": idea, "duration_seconds": 20,
            "optimize_five_seconds": True}, server.new_project(), use_model=False)
        prompts = [scene["prompt"] for scene in result["scenes"]]
        self.assertEqual(len(prompts), 4)
        self.assertTrue(all(idea in prompt for prompt in prompts))
        self.assertIn("central encounter and emotional turn", prompts[2])
        self.assertIn("emotional resolution", prompts[-1])
        self.assertIn("do not end mid-action", prompts[-1])

    def test_magia_promotes_unmatched_explicit_style_to_project_style(self):
        result = server.make_magia_storyboard({"idea": "A child finds a dog. Anime style.",
            "duration_seconds": 10, "optimize_five_seconds": True},
            server.new_project(), use_model=False)
        self.assertEqual(result["project_style"], "Anime style.")
        self.assertIsNone(result["selected_skill_id"])
        self.assertTrue(all(scene["prompt_skill_id"] is None for scene in result["scenes"]))

    def test_magia_selects_matching_workflow_skill_instead_of_project_style(self):
        result = server.make_magia_storyboard({"idea": "A playful 3D animation short about two robots.",
            "duration_seconds": 10, "optimize_five_seconds": True},
            server.new_project(), use_model=False)
        self.assertEqual(result["selected_skill_id"], "3d-short")
        self.assertEqual(result["project_style"], "")
        self.assertTrue(all(scene["prompt_skill_id"] == "3d-short" for scene in result["scenes"]))

    def test_magia_manual_skill_is_not_replaced_by_style_detection(self):
        result = server.make_magia_storyboard({"idea": "An anime style first-person walk.",
            "duration_seconds": 10, "optimize_five_seconds": True,
            "context": {"prompt_skill_id": "pov-film"}}, server.new_project(), use_model=False)
        self.assertEqual(result["selected_skill_id"], "pov-film")
        self.assertEqual(result["project_style"], "")

    def test_magia_caps_output_at_twenty_four_scenes(self):
        with self.assertRaisesRegex(ValueError, "up to 24 scenes"):
            server.make_magia_storyboard({"idea": "An extremely long story", "duration_seconds": 121,
                                          "optimize_five_seconds": True}, server.new_project(), use_model=False)

    def test_magia_uses_refiner_output_with_exact_scene_count(self):
        response = mock.Mock(returncode=0, stdout='{"scenes":["Opening action.","Continuation action."]}')
        with mock.patch.object(server, "formatter_available", return_value=True), \
             mock.patch.object(server, "run_formatter_command", return_value=response) as formatter:
            result = server.make_magia_storyboard({"idea": "A ten second journey.", "duration_seconds": 10,
                "optimize_five_seconds": True, "style": "Warm practical light",
                "skill_direction": "Preserve exact product text"}, server.new_project(), use_model=True)
        self.assertTrue(result["used_ai"])
        self.assertEqual([scene["prompt"] for scene in result["scenes"]], ["Opening action.", "Continuation action."])
        instruction = formatter.call_args.args[0][formatter.call_args.args[0].index("-p") + 1]
        self.assertIn("exactly 2 strings", instruction)
        self.assertIn("Warm practical light", instruction)
        self.assertIn("Preserve exact product text", instruction)
        self.assertIn("must be visibly separated before that encounter", instruction)
        self.assertIn("Preserve stated emotional chronology exactly", instruction)

    def test_magia_accepts_answer_after_llama_cli_echoes_requested_json(self):
        response = mock.Mock(returncode=0, stdout=(
            'Return JSON as {"scenes":[exactly 2 strings]}\n'
            '{"scenes":["The child enters the park.","The friends reunite joyfully."]}\n'))
        with mock.patch.object(server, "formatter_available", return_value=True), \
             mock.patch.object(server, "run_formatter_command", return_value=response):
            result = server.make_magia_storyboard({"idea": "Friends reunite.", "duration_seconds": 10,
                "optimize_five_seconds": True}, server.new_project(), use_model=True)
        self.assertTrue(result["used_ai"])
        self.assertEqual(result["scenes"][-1]["prompt"], "The friends reunite joyfully.")

    def test_magia_consolidates_surplus_model_beats(self):
        response = mock.Mock(returncode=0, stdout=json.dumps({"scenes": [
            "Opening.", "Search.", "Discovery.", "Reunion.", "Closing image."]}))
        with mock.patch.object(server, "formatter_available", return_value=True), \
             mock.patch.object(server, "run_formatter_command", return_value=response):
            result = server.make_magia_storyboard({"idea": "Friends reunite.", "duration_seconds": 20,
                "optimize_five_seconds": True}, server.new_project(), use_model=True)
        self.assertTrue(result["used_ai"])
        self.assertEqual(len(result["scenes"]), 4)
        self.assertEqual(result["scenes"][-1]["prompt"], "Reunion. Closing image.")

    def test_magia_accepts_structured_scene_objects_from_small_local_model(self):
        response = mock.Mock(returncode=0, stdout=json.dumps({"scenes": [
            {"id": 1, "description": "The child searches the park."},
            {"id": 2, "description": "The lonely dog waits by a bench."},
            {"id": 3, "description": "They spot each other."},
            {"id": 4, "description": "They reunite and leave together."}]}))
        with mock.patch.object(server, "formatter_available", return_value=True), \
             mock.patch.object(server, "run_formatter_command", return_value=response):
            result = server.make_magia_storyboard({"idea": "Friends reunite.", "duration_seconds": 20,
                "optimize_five_seconds": True}, server.new_project(), use_model=True)
        self.assertTrue(result["used_ai"])
        self.assertEqual(result["scenes"][0]["prompt"], "The child searches the park.")

    def test_recursive_scene_prompt_keeps_only_latest_cut_sequence(self):
        bloated = "subject_definitions: old context " + ("history " * 1600) + "CUT 01 | 0.00–2.00s — New action. Sound: wind. overall_soundscape: clean"
        compact = server.compact_scene_prompt(bloated)
        self.assertLess(len(compact), 10000)
        self.assertTrue(compact.startswith("CUT 01"))
        self.assertNotIn("old context", compact)

    def test_frame_anchor_uses_text_identity_without_picture_references(self):
        project = {"characters": [{"id": "nima", "name": "Nima", "description": "one crimson stilt", "images": []}], "media": []}
        scene = {"prompt": "Cross the bridge.", "character_ids": ["nima"], "reference_media_ids": [], "source_media_id": "source", "params": {"frames": 96}, "guide_answers": {}}
        prompt = server.build_prompt(scene, project, True, Path("last-frame.jpg"))
        self.assertIn("one crimson stilt", prompt)
        self.assertEqual(prompt.count("<Picture"), 1)

    def test_continuation_uses_frame_anchor_and_text_character_lock(self):
        project = {"slug": "film", "characters": [{"id": "nima", "name": "Nima", "description": "one crimson stilt", "images": ["nima-front"]}],
                   "media": [{"id": "nima-front", "kind": "image", "name": "Nima front", "src": "media/nima-front.png"}]}
        scene = {"prompt": "Cross the bridge.", "character_ids": ["nima"], "reference_media_ids": [], "params": {"frames": 96}, "guide_answers": {}}
        prompt = server.build_prompt(scene, project, True, Path("last-frame.jpg"))
        self.assertEqual(prompt.count("<Picture"), 1)
        self.assertIn("For the target video, at 0.00 seconds", prompt)
        self.assertIn("one crimson stilt", prompt)

    def test_hybrid_continuity_reserves_picture_one_and_keeps_cast(self):
        project = {"slug": "film", "characters": [{"id": "nima", "name": "Nima", "description": "one crimson stilt", "images": ["front"]}],
                   "media": [{"id": "front", "kind": "image", "name": "Nima front", "src": "media/front.png"}]}
        scene = {"prompt": "Continue through the market.", "character_ids": ["nima"],
                 "reference_media_ids": [], "source_media_id": "previous", "continuity_mode": "reference",
                 "params": {"frames": 120}, "guide_answers": {}}
        refs = [{"name": "Previous scene final frame", "description": "highest continuity authority",
                 "paths": [Path("last.jpg")], "kind": "continuity_reference"}] + server.scene_all_references(scene, project)
        prompt = server.build_prompt(scene, project, True, None, refs)
        self.assertIn("<Subject 1> is the opening continuity state from <Picture 1>", prompt)
        self.assertIn("<Subject 2> is Nima", prompt)
        self.assertIn("<Picture 2>", prompt)

    def test_magia_scene_compiles_to_official_h3_base_structure(self):
        project = server.new_project()
        magia = server.make_magia_storyboard({"idea": "A child finds a lost dog.",
            "duration_seconds": 10, "optimize_five_seconds": True}, project, use_model=False)
        prompt = server.build_prompt(magia["scenes"][0], project, False)
        self.assertTrue(prompt.startswith("integrated_multimodal_description: [Shot 1]"))
        self.assertLess(prompt.index("integrated_multimodal_description:"), prompt.index("overall_soundscape:"))
        self.assertLess(prompt.index("overall_soundscape:"), prompt.index("non_diegetic_music:"))
        self.assertNotIn("[Shot 1] At", prompt)

    def test_storyboard_hybrid_continuation_keeps_source_and_mode(self):
        project = server.new_project()
        project["slug"] = "hybrid"
        scenes = server.create_storyboard_batch(project, {"scenes": [
            {"prompt": "First scene", "continue_previous": False},
            {"prompt": "Second scene", "continue_previous": True, "continuity_mode": "reference"},
        ]})
        self.assertEqual(scenes[1]["continuity_mode"], "reference")
        self.assertEqual(scenes[1]["source_media_id"], project["media"][0]["id"])

    def test_continuity_audit_flags_unexplained_cast_change_without_model(self):
        project = server.new_project()
        project["characters"] = [{"id": "a", "name": "Ana"}, {"id": "b", "name": "Ben"}]
        result = server.audit_storyboard_continuity({"scenes": [
            {"name": "One", "prompt": "Ana drives.", "character_ids": ["a"]},
            {"name": "Two", "prompt": "Continue driving.", "character_ids": ["a", "b"], "continue_previous": True},
        ]}, project, use_model=False)
        self.assertEqual(result["issues"][0]["category"], "cast")
        self.assertIn("Ben", result["issues"][0]["detail"])
        self.assertFalse(result["used_ai"])

    def test_continuity_audit_blocks_same_prop_without_provenance(self):
        result = server.audit_storyboard_continuity({"scenes": [
            {"prompt": "The family drives. No food is visible."},
            {"prompt": "Continue driving while Miguel holds the same wrapped arepa.", "continue_previous": True},
        ]}, server.new_project(), use_model=False)
        prop = next(issue for issue in result["issues"] if issue["category"] == "prop")
        self.assertEqual(prop["severity"], "block")
        self.assertIn("arepa", prop["detail"])

    def test_complete_h3_prompt_is_detected_before_refinement(self):
        prompt = ("subject_definitions: <Subject 1> is Nima from <Picture 1>.\n\n"
                  "detailed_description: [Shot 1] Nima walks.\n\n"
                  "overall_soundscape: wind.\n\n"
                  "non_diegetic_music: none.")
        self.assertTrue(server.is_structured_h3_prompt(prompt))
        self.assertFalse(server.is_structured_h3_prompt("Nima walks through the city."))
        self.assertEqual(server.structured_h3_description(prompt), "[Shot 1] Nima walks.")

    def test_structured_prompt_recovers_named_cast_in_subject_order(self):
        project = {"characters": [{"id": "n", "name": "Nima"},
                                   {"id": "m", "name": "Mote"},
                                   {"id": "x", "name": "Archivist"}]}
        prompt = ("subject_definitions: <Subject 1> is Nima from <Picture 1>. "
                  "<Subject 2> is Mote from <Picture 2>.\n\n"
                  "detailed_description: [Shot 1] They run.\n\n"
                  "overall_soundscape: wind.\n\nnon_diegetic_music: none.")
        self.assertEqual(server.structured_prompt_character_ids(prompt, project), ["n", "m"])

    def test_continuation_drops_stale_picture_labels_from_structured_prompt(self):
        project = {"characters": [{"id": "nima", "name": "Nima", "description": "one crimson stilt", "images": []}], "media": []}
        scene = {"prompt": ("subject_definitions: <Subject 1> from <Picture 8>.\n\n"
                            "detailed_description: [Shot 1] Cross the bridge.\n\n"
                            "overall_soundscape: wind.\n\nnon_diegetic_music: none."),
                 "character_ids": ["nima"], "reference_media_ids": [],
                 "params": {"frames": 96}, "guide_answers": {}}
        prompt = server.build_prompt(scene, project, True, Path("last-frame.jpg"))
        self.assertEqual(prompt.count("<Picture"), 1)
        self.assertNotIn("<Picture 8>", prompt)

    def test_character_references_share_the_nine_image_budget(self):
        media=[];characters=[]
        labels=("front","three-quarter","left side","back","right side","front face")
        for char_index,name in enumerate(("Nima","Mote")):
            ids=[]
            for index,label in enumerate(labels):
                mid=f"c{char_index}-{index}";ids.append(mid);media.append({"id":mid,"kind":"image","name":f"{name} · {label}","src":f"media/{mid}.png"})
            characters.append({"id":f"c{char_index}","name":name,"images":ids})
        project={"slug":"film","characters":characters,"media":media}
        refs=server.scene_characters({"character_ids":["c0","c1"],"reference_media_ids":[]},project)
        self.assertEqual([len(ref["paths"]) for ref in refs],[5,4])
        self.assertEqual(sum(len(ref["paths"]) for ref in refs),9)
        self.assertTrue(str(refs[0]["paths"][0]).endswith("c0-0.png"))
        self.assertTrue(str(refs[0]["paths"][1]).endswith("c0-1.png"))

    def test_three_characters_receive_three_references_each(self):
        media=[];characters=[]
        for char_index in range(3):
            ids=[]
            for index in range(6):
                mid=f"c{char_index}-{index}";ids.append(mid);media.append({"id":mid,"kind":"image","name":f"Character {char_index} view {index}","src":f"media/{mid}.png"})
            characters.append({"id":f"c{char_index}","name":f"Character {char_index}","images":ids})
        project={"slug":"film","characters":characters,"media":media}
        refs=server.scene_characters({"character_ids":["c0","c1","c2"],"reference_media_ids":[]},project)
        self.assertEqual([len(ref["paths"]) for ref in refs],[3,3,3])

    def test_scene_uses_only_explicitly_selected_character_views(self):
        media = [{"id": f"n-{i}", "kind": "image", "name": f"Nima view {i}", "src": f"media/n-{i}.png"} for i in range(4)]
        project = {"slug": "film", "characters": [{"id": "nima", "name": "Nima", "images": [m["id"] for m in media]}], "media": media}
        refs = server.scene_characters({"character_ids": ["nima"], "character_reference_ids": {"nima": ["n-3", "n-1"]}, "reference_media_ids": []}, project)
        self.assertEqual([Path(path).name for path in refs[0]["paths"]], ["n-3.png", "n-1.png"])

    def test_scene_audio_reference_preserves_type_duration_and_path(self):
        project = {"slug":"film", "characters":[], "media":[
            {"id":"poster", "kind":"image", "name":"Poster", "src":"media/poster.png"},
            {"id":"voice", "kind":"audio", "name":"Voice", "src":"media/voice.wav", "duration":5.0}]}
        refs = server.scene_all_references({"character_ids":[], "reference_media_ids":["poster","voice"]}, project)
        self.assertEqual([item["kind"] for item in refs], ["visual_reference", "audio_reference"])
        self.assertEqual(refs[1]["duration"], 5.0)
        self.assertTrue(str(refs[1]["paths"][0]).endswith("voice.wav"))

    def test_upload_sniffer_recognizes_reference_audio_formats(self):
        self.assertEqual(server.sniff_kind(b"RIFFxxxxWAVEfmt ", ".wav"), "audio")
        self.assertEqual(server.sniff_kind(b"ID3metadata", ".mp3"), "audio")
        self.assertEqual(server.sniff_kind(b"fLaCdata", ".flac"), "audio")

    def test_explicit_character_views_still_obey_nine_reference_limit(self):
        media = [{"id": f"n-{i}", "kind": "image", "name": f"Nima view {i}", "src": f"media/n-{i}.png"} for i in range(9)]
        media.append({"id": "visual", "kind": "image", "name": "Set", "src": "media/visual.png"})
        project = {"slug": "film", "characters": [{"id": "nima", "name": "Nima", "images": [m["id"] for m in media[:9]]}], "media": media}
        with self.assertRaises(ValueError):
            server.scene_characters({"character_ids": ["nima"], "character_reference_ids": {"nima": [m["id"] for m in media[:9]]}, "reference_media_ids": ["visual"]}, project)

    def test_project_save_is_atomic_under_concurrent_writers(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            snapshots = [{"slug": "film", "name": "A", "media": []},
                         {"slug": "film", "name": "B", "media": list(range(200))}]
            with mock.patch.object(server, "PROJECTS", root):
                workers = [threading.Thread(target=server.save_project, args=(snapshot,)) for snapshot in snapshots]
                for worker in workers: worker.start()
                for worker in workers: worker.join()
            saved = json.loads((root / "film" / "project.json").read_text())
            self.assertIn(saved["name"], ("A", "B"))

    def test_completed_sheet_outputs_repair_a_stale_queued_record(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            media_dir = root / "film" / "media"
            media_dir.mkdir(parents=True)
            sheet_id = "mote"
            (media_dir / f"sheet-{sheet_id}.mp4").write_bytes(b"video")
            for _, label in server.sheet_extract_times("turn-6"):
                (media_dir / f"sheet-{sheet_id}-{label.replace(' ', '-')}.png").write_bytes(b"image")
            project = {"slug": "film", "media": [], "sheets": [{"id": sheet_id, "name": "Mote",
                       "recipe": "turn-6", "status": "queued", "frames": [], "videoMediaId": None}]}
            probe = {"duration": 5.0, "w": 768, "h": 768, "hasAudio": False}
            with mock.patch.object(server, "PROJECTS", root), mock.patch.object(server.nle, "probe", return_value=probe):
                self.assertTrue(server.repair_completed_sheets(project))
            self.assertEqual(project["sheets"][0]["status"], "ready")
            self.assertEqual(len(project["sheets"][0]["frames"]), 6)
            self.assertEqual(len(project["media"]), 7)

    def test_legacy_ready_sheet_extractions_upgrade_once_from_spin(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            media_dir = root / "film" / "media"
            media_dir.mkdir(parents=True)
            video = media_dir / "bird-spin.mp4"
            video.write_bytes(b"video")
            labels = [label for _, label in server.sheet_extract_times("turn-6")]
            media = [{"id": "spin", "kind": "video", "src": "media/bird-spin.mp4"}]
            frames = []
            for index, label in enumerate(labels):
                path = media_dir / f"bird-{index}.png"
                path.write_bytes(b"old")
                media.append({"id": f"view-{index}", "kind": "image", "src": f"media/{path.name}"})
                frames.append({"mediaId": f"view-{index}", "label": label, "time": 0})
            project = {"slug": "film", "media": media, "sheets": [{"id": "bird", "name": "Bird",
                       "recipe": "turn-6", "status": "ready", "videoMediaId": "spin", "frames": frames}]}

            def extract(_video, target, at):
                Path(target).write_bytes(str(at).encode())
                return True

            plan = [(at, label) for at, label in server.sheet_extract_times("turn-6")]
            with mock.patch.object(server, "PROJECTS", root), \
                 mock.patch.object(server, "sheet_extract_plan", return_value=(plan, {"adaptive": True})), \
                 mock.patch.object(server.nle, "extract_frame", side_effect=extract) as extraction:
                self.assertTrue(server.upgrade_sheet_extractions(project))
                self.assertFalse(server.upgrade_sheet_extractions(project))
            self.assertEqual(extraction.call_count, 6)
            self.assertEqual(project["sheets"][0]["extractionVersion"], server.SHEET_EXTRACTION_VERSION)
            self.assertEqual(project["sheets"][0]["frames"][3]["time"], plan[3][0])

    def test_sheet_motion_analysis_keeps_semantic_checkpoints_and_avoids_blink_frame(self):
        frame_size = 64 * 64
        frames = []
        # Forty 8-fps samples; sample 1.0s contains a full-frame luminance blink.
        for index in range(40):
            value = min(240, 30 + min(index, abs(index - 28)) * 3)
            if index == 8:
                value = 250
            frames.append(bytes([value]) * frame_size)
        completed = mock.Mock(returncode=0, stdout=b"".join(frames))
        with mock.patch.object(server.subprocess, "run", return_value=completed):
            times, diagnostics = server.analyze_sheet_motion("sheet.mp4")
        self.assertTrue(diagnostics["adaptive"])
        self.assertAlmostEqual(diagnostics["orbitEnd"], 4.0)
        self.assertNotEqual(times["left side"], 1.0)
        self.assertTrue(diagnostics["unstable"])

    def test_overlapping_clip_snaps_to_nearest_sequential_position(self):
        track = {"clips": [{"id": "first", "start": 0, "in": 0, "out": 5.2}]}
        self.assertEqual(server.resolve_clip_start(track, 1.2, 3.6), 5.2)

    def test_legacy_overlaps_are_repaired_in_track_order(self):
        project = {"tracks": [{"clips": [
            {"id": "first", "start": 0, "in": 0, "out": 5.2},
            {"id": "second", "start": 1.2, "in": 0, "out": 3.6},
            {"id": "third", "start": 7, "in": 0, "out": 1},
        ]}]}
        self.assertTrue(server.repair_timeline_overlaps(project))
        self.assertEqual([c["start"] for c in project["tracks"][0]["clips"]], [0, 5.2, 8.8])
        self.assertFalse(server.repair_timeline_overlaps(project))

    def test_scene_can_explicitly_ignore_saved_project_style(self):
        project = {"base_prompt": "LEGACY_STYLE_SHOULD_NOT_LEAK", "characters": [], "media": []}
        scene = {"prompt": "a red kite over an empty beach", "character_ids": [],
                 "reference_media_ids": [], "params": {"frames": 56},
                 "style_profile": {"prompt": "PROJECT_STYLE_SHOULD_NOT_LEAK"},
                 "use_project_style": False}
        prompt = server.build_prompt(scene, project, ref2va=False)
        self.assertNotIn("PROJECT_STYLE_SHOULD_NOT_LEAK", prompt)
        self.assertNotIn("LEGACY_STYLE_SHOULD_NOT_LEAK", prompt)

    def test_image_generation_uses_exact_five_frame_minimum(self):
        params = server.clamp_generation_params({"width": 768, "height": 768, "frames": 56,
                                                  "steps": 30, "layers": 50, "reuse": 1}, "image")
        self.assertEqual(params["frames"], 5)
        self.assertEqual(server.next_image_name({"scenes": [{"name": "Image 1"}, {"name": "Scene 1"}]}), "Image 2")

    def test_generation_clamps_to_h3_minimum_denoising_steps(self):
        params = server.clamp_generation_params({"steps": 1})
        self.assertEqual(params["steps"], 2)

    def test_long_native_video_honors_requested_high_schedule(self):
        params = server.clamp_generation_params({"width": 1344, "height": 768, "frames": 360,
                                                  "steps": 30, "layers": 50, "reuse": 1,
                                                  "quality": "high"})
        self.assertEqual((params["width"], params["height"], params["frames"], params["steps"]),
                         (1344, 768, 360, 30))
        self.assertEqual((params["layers"], params["reuse"]), (50, 1))
        self.assertNotIn("stability_adjusted", params)

    def test_stalled_high_schedule_has_explicit_stable_retry_metadata(self):
        params = server.safe_retry_params({"layers": 50, "reuse": 1, "quality": "high"})
        self.assertEqual((params["layers"], params["reuse"]), (40, 1))
        self.assertEqual((params["requested_layers"], params["requested_reuse"]), (50, 1))
        self.assertTrue(params["stability_adjusted"])
        self.assertEqual(params["effective_quality"], "long-stable-retry")

    def test_macos_process_group_permission_error_does_not_mask_stall(self):
        proc = mock.Mock()
        proc.pid = 12345
        proc.poll.return_value = None
        proc.wait.return_value = 0
        with mock.patch.object(server.os, "name", "posix"), \
             mock.patch.object(server.os, "getpgid", side_effect=PermissionError(1, "Operation not permitted")):
            self.assertTrue(server.terminate_process_tree(proc))
        proc.terminate.assert_called_once()

    def test_default_h3_hard_deadline_is_disabled(self):
        self.assertEqual(server.H3_HARD_TIMEOUT, 0)

    def test_continuation_prompt_cannot_name_unsent_cast_pictures(self):
        with self.assertRaisesRegex(ValueError, "Cast pictures"):
            server.validate_generation_prompt(
                "For the target video, <Picture 1> opens. <Subject 1> comes from <Picture 2>.",
                120, 1, continuation=True)

    def test_generation_prompt_rejects_typography_no_text_conflict(self):
        with self.assertRaisesRegex(ValueError, "forbids visible text"):
            server.validate_generation_prompt(
                'TEXT A = "FAMILY". Visible text: no visible text.', 120, 0)

    def test_generation_prompt_allows_exact_text_whitelist(self):
        self.assertTrue(server.validate_generation_prompt(
            'Text A = "VAMOS TARDE". No visible text other than the exact quoted phrase.',
            120, 0))

    def test_local_refinement_does_not_replace_authored_cut_plan(self):
        authored = "CUT 01 | 0.00-2.50s - Setup.\nCUT 02 | 2.50-5.00s - Payoff."
        expanded, used_ai = server.improve_idea_locally(
            authored, {"_duration_seconds": 5, "continuity": "Continue from Picture 1."})
        self.assertFalse(used_ai)
        self.assertEqual(expanded.count("CUT 01"), 1)
        self.assertIn("Continue from Picture 1.", expanded)

    def test_explicit_prose_duration_is_user_authority(self):
        self.assertEqual(5.0, server.requested_duration_seconds(
            "Make a 5-second tense discovery moment at 24fps."))

    def test_fps_is_not_mistaken_for_duration(self):
        self.assertIsNone(server.requested_duration_seconds(
            "Create an audiovisual target video at 24fps."))

    def test_cut_ledger_end_is_duration_authority(self):
        prompt = "CUT 01 | 0.00-2.50s - Setup. CUT 02 | 2.50-5.00s - Payoff."
        self.assertEqual(5.0, server.requested_duration_seconds(prompt))

    def test_continuity_audit_skips_unrelated_new_sequences(self):
        payload = {"scenes": [
            {"prompt": "First independent test.", "continue_previous": False},
            {"prompt": "Second independent test.", "continue_previous": False},
        ]}
        with mock.patch.object(server, "formatter_available", return_value=True), \
             mock.patch.object(server, "run_formatter_command") as formatter:
            result = server.audit_storyboard_continuity(payload, {"characters": []}, True)
        self.assertEqual([], result["issues"])
        formatter.assert_not_called()

    def test_continuity_audit_rejects_model_schema_echo(self):
        payload = {"scenes": [
            {"prompt": "Scene one.", "continue_previous": False},
            {"prompt": "Continue scene one.", "continue_previous": True},
        ]}
        echoed = mock.Mock(returncode=0, stdout=(
            '{"issues":[{"scene_index":1,"severity":"warning|block",'
            '"category":"cast|prop|vehicle|spatial|text|world|identity",'
            '"title":"short","detail":"specific evidence and correction",'
            '"fact":"concise state to confirm"}]}'))
        with mock.patch.object(server, "formatter_available", return_value=True), \
             mock.patch.object(server, "run_formatter_command", return_value=echoed):
            result = server.audit_storyboard_continuity(payload, {"characters": []}, True)
        self.assertEqual([], result["issues"])

    def test_continuity_audit_requires_and_explains_exact_evidence(self):
        payload = {"scenes": [
            {"prompt": "Scene one ends with both children empty-handed.", "continue_previous": False},
            {"prompt": "Continue immediately. Miguel still holds the wrapped arepa.", "continue_previous": True},
        ]}
        diagnosed = mock.Mock(returncode=0, stdout=(
            '{"issues":[{"scene_index":1,"severity":"block","category":"prop",'
            '"element":"the wrapped arepa",'
            '"previous_evidence":"Scene 1 ends with both children empty-handed",'
            '"current_evidence":"Scene 2 starts with Miguel already holding the wrapped arepa",'
            '"fix":"Show Miguel receiving the arepa first, or remove it from Scene 2"}]}'))
        with mock.patch.object(server, "formatter_available", return_value=True), \
             mock.patch.object(server, "run_formatter_command", return_value=diagnosed):
            result = server.audit_storyboard_continuity(payload, {"characters": []}, True)
        issue = next(item for item in result["issues"] if "wrapped arepa" in item["title"].lower())
        self.assertIn("wrapped arepa", issue["title"])
        self.assertIn("Previous scene:", issue["detail"])
        self.assertIn("This scene:", issue["detail"])
        self.assertIn("Fix:", issue["detail"])

    def test_short_high_quality_video_keeps_full_schedule(self):
        params = server.clamp_generation_params({"width": 1344, "height": 768, "frames": 120,
                                                  "steps": 30, "layers": 50, "reuse": 1,
                                                  "quality": "high"})
        self.assertEqual((params["layers"], params["reuse"]), (50, 1))
        self.assertNotIn("stability_adjusted", params)

    def test_export_base_matches_browser_lane_order(self):
        tracks = [
            {"id": "top", "kind": "video", "clips": [{"id": "overlay"}]},
            {"id": "middle", "kind": "video", "clips": []},
            {"id": "bottom", "kind": "video", "clips": [{"id": "scene"}]},
        ]
        self.assertEqual(nle._base_video_track(tracks)["id"], "bottom")

    def test_muted_bottom_lane_is_not_the_export_base(self):
        tracks = [
            {"id": "top", "kind": "video", "clips": [{"id": "overlay"}]},
            {"id": "bottom", "kind": "video", "muted": True, "clips": [{"id": "scene"}]},
        ]
        self.assertEqual(nle._base_video_track(tracks)["id"], "top")

    def test_current_transition_items_are_read(self):
        clip = {"transition": {"items": [
            {"type": "fade", "edge": "start", "dur": .3, "enabled": True},
            {"type": "wipe", "edge": "start", "dur": .7, "enabled": True},
            {"type": "slide", "edge": "end", "dur": 1.0, "enabled": True},
        ]}}
        self.assertEqual(nle._transition_for(clip, "start")["type"], "wipe")
        self.assertEqual(nle._transition_for(clip, "end")["type"], "slide")

    def test_folder_name_collisions_are_case_insensitive_and_scoped(self):
        project = {"media": [
            {"id": "incoming", "name": "Hero.png", "folder": ""},
            {"id": "same", "name": "hero.PNG", "folder": "Cast"},
            {"id": "elsewhere", "name": "Hero.png", "folder": "Other"},
        ]}
        collisions = server._folder_name_collisions(project, ["incoming"], "Cast")
        self.assertEqual([m["id"] for m in collisions], ["same"])
        self.assertEqual(server._folder_name_collisions(project, ["incoming"], ""), [])

    def test_folder_drop_moves_the_original_record_and_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "film" / "media" / "hero.png"
            source.parent.mkdir(parents=True)
            source.write_bytes(b"image")
            project = {"slug": "film", "media": [{"id": "original", "asset_uid": "asset",
                       "src": "media/hero.png", "name": "Hero", "kind": "image", "folder": ""}]}
            with mock.patch.object(server, "PROJECTS", root):
                self.assertEqual(server._media_fs_move(project, ["original"], "Media 1"), 1)
            self.assertEqual(len(project["media"]), 1)
            moved = project["media"][0]
            self.assertEqual(moved["id"], "original")
            self.assertEqual(moved["asset_uid"], "asset")
            self.assertEqual(moved["folder"], "Media 1")
            self.assertFalse(source.exists())
            self.assertEqual((root / "film" / moved["src"]).read_bytes(), b"image")

    def test_dropping_a_folder_item_back_into_same_folder_is_a_noop(self):
        project = {"media": [{"id": "inside", "name": "Hero.png", "folder": "Media 1"}]}
        self.assertEqual(server._folder_name_collisions(project, ["inside"], "Media 1"), [])

    def test_keep_both_moves_and_renames_the_incoming_item(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "film" / "media" / "Hero.png"
            source.parent.mkdir(parents=True)
            source.write_bytes(b"image")
            existing = root / "film" / "media" / "Media 1" / "Hero.png"
            existing.parent.mkdir(parents=True)
            existing.write_bytes(b"other")
            project = {"slug": "film", "media": [
                {"id": "incoming", "asset_uid": "asset", "src": "media/Hero.png", "name": "Hero", "kind": "image", "folder": ""},
                {"id": "existing", "asset_uid": "other", "src": "media/Media 1/Hero.png", "name": "Hero", "kind": "image", "folder": "Media 1"},
            ]}
            with mock.patch.object(server, "PROJECTS", root):
                server._media_fs_move(project, ["incoming"], "Media 1", keep_both=True)
            self.assertEqual(len(project["media"]), 2)
            self.assertEqual(project["media"][0]["name"], "Hero copy")
            self.assertEqual(project["media"][0]["folder"], "Media 1")
            self.assertFalse(source.exists())
            self.assertTrue((existing.parent / "Hero copy.png").is_file())

    def test_media_rename_updates_backing_file_and_scene_paths(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "film" / "media" / "Old.png"
            source.parent.mkdir(parents=True)
            source.write_bytes(b"image")
            media = {"id": "m1", "src": "media/Old.png", "name": "Old", "kind": "image"}
            project = {"slug": "film", "media": [media], "scenes": [{"id": "s1", "mediaId": "m1",
                       "media": "media/Old.png", "first_frame": "/media/Old.png", "last_frame": "/media/Old.png"}]}
            with mock.patch.object(server, "PROJECTS", root):
                server.rename_media_file(project, media, "New name")
            self.assertEqual(media["name"], "New name")
            self.assertEqual(media["src"], "media/New name.png")
            self.assertFalse(source.exists())
            self.assertTrue((source.parent / "New name.png").is_file())
            self.assertEqual(project["scenes"][0]["first_frame"], "/media/New name.png")

    def test_continuity_evidence_keeps_provenance_and_character_locks(self):
        project = {
            "name": "Film", "order": ["s1"], "style_profile": {"prompt": "amber night lighting"},
            "characters": [{"id": "c1", "name": "Mildred", "description": "purple coat", "images": ["portrait"]}],
            "media": [{"id": "video", "name": "Scene 1", "kind": "video", "scene_id": "s1",
                       "generation": {"prompt": "walk through the station", "params": {"seed": 42}}}],
            "scenes": [{"id": "s1", "name": "Scene 1", "status": "ready", "mediaId": "video",
                        "prompt": "walk through the station", "character_ids": ["c1"],
                        "reference_media_ids": [], "style_profile": {"prompt": "amber night lighting"},
                        "guide_answers": {}, "params": {"seed": 42}}],
        }
        evidence = server.continuity_evidence(project, "video")
        self.assertIn('"anchor": true', evidence)
        self.assertIn("Mildred", evidence)
        self.assertIn("purple coat", evidence)
        self.assertIn("walk through the station", evidence)

    def test_continuity_fallback_is_stable_and_source_scoped(self):
        project = {"slug": "film", "name": "Film", "order": [], "style_profile": {"prompt": ""},
                   "characters": [], "media": [{"id": "m1", "name": "Anchor", "kind": "image"}], "scenes": []}
        first, used_ai = server.build_continuity_style(project, "m1", use_model=False)
        second, _ = server.build_continuity_style(project, "m1", use_model=False)
        self.assertFalse(used_ai)
        self.assertEqual(first["id"], "continuity-film")
        self.assertEqual(first["evidence_hash"], second["evidence_hash"])
        self.assertEqual(first["prompt"], second["prompt"])
        self.assertEqual(first["source_media_ids"], ["m1"])

    def test_continuity_text_locks_preserve_only_explicit_copy(self):
        project = {"slug": "film", "name": "Film", "order": [], "style_profile": {"prompt": ""},
                   "characters": [], "media": [], "scenes": [
                       {"id": "s1", "guide_answers": {"text": "OPEN EVERY DOOR."}},
                       {"id": "s2", "guide_answers": {"text": "no visible text unless explicitly requested"}},
                       {"id": "s3", "guide_answers": {"text": "OPEN EVERY DOOR."}},
                       {"id": "s4", "prompt": "On-screen text reads: KEEP GOING; then fade out.", "guide_answers": {}},
                   ]}
        self.assertEqual(server.continuity_text_locks(project), ["OPEN EVERY DOOR.", "KEEP GOING"])
        profile, _ = server.build_continuity_style(project, use_model=False)
        self.assertEqual(profile["visible_text_locks"], ["OPEN EVERY DOOR.", "KEEP GOING"])
        self.assertIn("OPEN EVERY DOOR.", profile["prompt"])

    def test_continuity_update_combines_prior_evidence_and_new_knowledge(self):
        project = {"name": "Film", "order": [], "characters": [], "media": [], "scenes": [],
                   "style_profile": {"source": "continuity", "skill_id": "continuity-film", "prompt": "bible"},
                   "project_style_skills": [{"id": "continuity-film", "knowledge_updates": [{"prompt": "Maria wears a red coat."}]}]}
        evidence = server.continuity_evidence(project, new_prompt="The coat is explicitly waterproof.",
                                              new_answers={"camera": "50mm"})
        self.assertIn("PRIOR USER-APPROVED STYLE KNOWLEDGE", evidence)
        self.assertIn("Maria wears a red coat", evidence)
        self.assertIn("NEW REFINEMENT KNOWLEDGE", evidence)
        self.assertIn("waterproof", evidence)

    def _timeline_magia_project(self):
        return {"media": [
                    {"id": "m1", "name": "Opening", "kind": "video"},
                    {"id": "m2", "name": "Payoff", "kind": "video"},
                    {"id": "m3", "name": "Logo", "kind": "image"},
                ], "tracks": [
                    {"id": "V1", "kind": "video", "clips": [
                        {"id": "c1", "mediaId": "m1", "start": 0, "in": 0, "out": 5},
                        {"id": "c2", "mediaId": "m2", "start": 5.25, "in": 0, "out": 5},
                    ]},
                    {"id": "V2", "kind": "video", "clips": [
                        {"id": "o1", "mediaId": "m3", "start": 1, "in": 0, "out": 3},
                    ]},
                ]}

    def test_timeline_magia_plan_is_deterministic_and_export_safe(self):
        project = self._timeline_magia_project()
        request = {"seed": 420, "scope": "timeline", "direction": "subtle premium polish"}
        first = server.timeline_magia_plan(project, request)
        second = server.timeline_magia_plan(project, request)
        self.assertEqual(first, second)
        self.assertEqual(first["profile"], "Subtle polish")
        self.assertEqual(first["summary"]["clips"], 3)
        self.assertGreaterEqual(first["summary"]["transitions"], 3)
        self.assertEqual(first["summary"]["overlays"], 1)
        allowed = {"start", "in", "out", "zoom", "position", "motion", "keyframes", "color",
                   "blur", "mask", "audioFade", "volume", "transition", "muted"}
        for update in first["updates"]:
            self.assertLessEqual(set(update["fields"]), allowed)
        overlay = next(item for item in first["updates"] if item["clip_id"] == "o1")
        self.assertTrue(overlay["fields"]["mask"]["enabled"])
        self.assertEqual(overlay["fields"]["mask"]["type"], "rectangle")
        base = next(item for item in first["updates"] if item["clip_id"] == "c2")
        self.assertTrue(base["fields"]["keyframes"]["enabled"])
        self.assertGreaterEqual(len(base["fields"]["keyframes"]["points"]), 3)
        self.assertTrue(any("zoom" in change for change in base["changes"]))

    def test_timeline_magia_direction_adapts_the_color_story(self):
        project = self._timeline_magia_project()
        plan = server.timeline_magia_plan(project, {"seed": 42, "direction": "make it saturated and punchy",
                                                     "options": {"transitions": True, "transforms": True,
                                                                 "color": True, "pacing": False,
                                                                 "overlays": False, "audio": False}})
        self.assertEqual(plan["profile"], "Saturated edit")
        for update in plan["updates"]:
            self.assertGreaterEqual(update["fields"]["color"]["saturation"], 1.3)
            self.assertTrue(any("saturated color" in change for change in update["changes"]))

    def test_timeline_magia_sepia_direction_is_visible_and_controlled(self):
        project = self._timeline_magia_project()
        plan = server.timeline_magia_plan(project, {"seed": 8, "direction": "make the video sepia",
                                                     "options": {"transitions": False, "transforms": False,
                                                                 "color": True, "pacing": False,
                                                                 "overlays": False, "audio": False}})
        self.assertEqual(plan["profile"], "Sepia edit")
        self.assertEqual(plan["direction_note"], "sepia color treatment")
        for update in plan["updates"]:
            color = update["fields"]["color"]
            self.assertGreaterEqual(color["temperature"], .4)
            self.assertLess(color["saturation"], .72)

    def test_timeline_magia_direction_can_use_local_refiner(self):
        server.timeline_magia_direction_cache.clear()
        run = SimpleNamespace(returncode=0, stdout='noise {"intent":"sepia","intensity":"subtle","note":"A restrained antique sepia grade."}')
        with mock.patch.object(server, "formatter_available", return_value=True), \
                mock.patch.object(server, "run_formatter_command", return_value=run) as formatter:
            result = server.interpret_timeline_magia_direction("antique editorial memory", use_ai=True)
        formatter.assert_called_once()
        self.assertTrue(result["used_ai"])
        self.assertEqual(result["intent"], "sepia")
        self.assertEqual(result["note"], "A restrained antique sepia grade.")

    def test_timeline_magia_apply_updates_clips_and_preserves_legal_timing(self):
        project = self._timeline_magia_project()
        plan = server.timeline_magia_plan(project, {"seed": 91, "scope": "timeline"})
        self.assertEqual(server.apply_timeline_magia_plan(project, plan), 3)
        base = project["tracks"][0]["clips"]
        self.assertAlmostEqual(base[1]["start"], base[0]["out"] - base[0]["in"], places=6)
        self.assertGreaterEqual(base[0]["out"] - base[0]["in"], 1)
        self.assertIn("color", base[0])
        self.assertIn("transition", base[1])
        overlay = project["tracks"][1]["clips"][0]
        self.assertIn("mask", overlay)
        self.assertIn("position", overlay)

    def test_timeline_magia_selected_scope_changes_only_selected_clip(self):
        project = self._timeline_magia_project()
        plan = server.timeline_magia_plan(project, {"seed": 12, "scope": "selected",
                                                     "selected_clip_id": "c2"})
        self.assertEqual([item["clip_id"] for item in plan["updates"]], ["c2"])
        self.assertNotIn("start", plan["updates"][0]["fields"])
        self.assertNotIn("out", plan["updates"][0]["fields"])

    def test_timeline_magia_creates_visible_overlays_when_overlay_lane_is_empty(self):
        project = self._timeline_magia_project()
        project["tracks"][1]["clips"] = []
        plan = server.timeline_magia_plan(project, {"seed": 22, "scope": "timeline",
            "options": {"transitions": False, "transforms": False, "color": False,
                        "pacing": False, "overlays": True, "audio": False}})
        created = [item for item in plan["updates"] if item.get("create")]
        self.assertTrue(created)
        self.assertEqual(plan["summary"]["overlays"], len(created))
        self.assertTrue(all(item["create"]["magiaOverlay"] for item in created))
        self.assertTrue(all(item["create"]["position"] == {"x": 0, "y": 0} for item in created))
        self.assertTrue(all(item["create"]["mask"]["type"] in {"split", "ellipse", "cinematic"} for item in created))
        self.assertTrue(all(len(item["create"]["transition"]["items"]) == 2 for item in created))
        self.assertEqual(server.apply_timeline_magia_plan(project, plan), len(created))
        overlays = project["tracks"][1]["clips"]
        self.assertEqual(len(overlays), len(created))
        self.assertTrue(all(clip["magiaOverlay"] for clip in overlays))

        remix = server.timeline_magia_plan(project, {"seed": 23, "scope": "timeline",
            "options": {"transitions": False, "transforms": False, "color": False,
                        "pacing": False, "overlays": True, "audio": False}})
        server.apply_timeline_magia_plan(project, remix)
        overlays = [clip for clip in project["tracks"][1]["clips"] if clip.get("magiaOverlay")]
        self.assertEqual(len(overlays), len([item for item in remix["updates"] if item.get("create")]))
        self.assertTrue(all("-23-" in clip["id"] for clip in overlays))

    def test_unchecked_magia_options_restore_only_magia_owned_effects(self):
        project = self._timeline_magia_project()
        project["tracks"][1]["clips"] = []
        original = project["tracks"][0]["clips"][1]
        original["zoom"] = .82
        original["position"] = {"x": 7, "y": -3}
        original["color"] = {"enabled": True, "saturation": .9}
        plan = server.timeline_magia_plan(project, {"seed": 30, "scope": "timeline"})
        server.apply_timeline_magia_plan(project, plan)
        changed = project["tracks"][0]["clips"][1]
        self.assertNotEqual(changed["zoom"], .82)
        self.assertIn("magiaEffects", changed)
        self.assertTrue(project["tracks"][1]["clips"])

        clear = server.timeline_magia_plan(project, {"seed": 31, "scope": "timeline",
            "options": {"transitions": False, "transforms": False, "color": False,
                        "pacing": False, "overlays": False, "audio": False}})
        restored = server.apply_timeline_magia_plan(project, clear)
        self.assertGreater(restored, 0)
        changed = project["tracks"][0]["clips"][1]
        self.assertEqual(changed["zoom"], .82)
        self.assertEqual(changed["position"], {"x": 7, "y": -3})
        self.assertEqual(changed["color"], {"enabled": True, "saturation": .9})
        self.assertNotIn("transition", changed)
        self.assertNotIn("magiaEffects", changed)
        self.assertFalse(project["tracks"][1]["clips"])

    def test_unchecked_options_remove_legacy_magia_effect_ids(self):
        project = self._timeline_magia_project()
        clip = project["tracks"][0]["clips"][1]
        clip.update({"zoom": 1.1, "position": {"x": 0, "y": 0},
                     "keyframes": {"points": [{"id": "magia-4-c2-start", "at": 0, "zoom": 1.1}]},
                     "transition": {"items": [{"id": "magia-4-c2", "type": "fade", "dur": .4}]},
                     "color": {"enabled": True, "saturation": 1.2}})
        clear = server.timeline_magia_plan(project, {"seed": 5, "scope": "timeline",
            "options": {"transitions": False, "transforms": False, "color": False,
                        "pacing": False, "overlays": False, "audio": False}})
        server.apply_timeline_magia_plan(project, clear)
        for key in ("zoom", "position", "keyframes", "transition", "color", "magiaEffects"):
            self.assertNotIn(key, clip)

    def test_magia_cleanup_cannot_delete_ordinary_video_or_audio_clips(self):
        project = self._timeline_magia_project()
        project["tracks"].append({"id": "A1", "kind": "audio", "clips": [
            {"id": "audio-user", "mediaId": "music", "start": 0, "in": 0, "out": 3,
             "magiaOverlay": True}]})
        project["tracks"][1]["clips"] = [
            {"id": "user-overlay", "mediaId": "m3", "start": 0, "in": 0, "out": 1,
             "magiaOverlay": True},
            {"id": "magia-overlay-1-c1", "mediaId": "m3", "start": 2, "in": 0, "out": 1,
             "magiaOverlay": True},
        ]
        clear = server.timeline_magia_plan(project, {"seed": 9, "scope": "timeline",
            "options": {"transitions": False, "transforms": False, "color": False,
                        "pacing": False, "overlays": False, "audio": False}})
        server.apply_timeline_magia_plan(project, clear)
        self.assertEqual([clip["id"] for clip in project["tracks"][0]["clips"]], ["c1", "c2"])
        self.assertEqual([clip["id"] for clip in project["tracks"][1]["clips"]], ["user-overlay"])
        self.assertEqual([clip["id"] for clip in project["tracks"][2]["clips"]], ["audio-user"])

    def test_timeline_magia_remix_seed_changes_the_edit(self):
        project = self._timeline_magia_project()
        first = server.timeline_magia_plan(project, {"seed": 1})
        second = server.timeline_magia_plan(project, {"seed": 2})
        self.assertNotEqual(first["updates"], second["updates"])


if __name__ == "__main__":
    unittest.main()
