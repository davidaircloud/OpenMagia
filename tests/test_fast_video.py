import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock

import fast_video
import server


class FastVideoTests(unittest.TestCase):
    def test_fixed_schedule_records_temporal_padding(self):
        params = fast_video.generation_params({'frames':120, 'steps':50, 'width':512, 'seed':9})
        self.assertEqual((params['steps'], params['frames'], params['requested_frames']), (8,124,120))
        self.assertEqual(fast_video.generation_params(params), params)
        self.assertEqual(params['seed'],9)

    def test_progress_uses_the_eight_forward_schedule(self):
        parsed = server.parse_h3_progress_line('INFO H3 denoise step 3/8 (sigma_video=0.9) in 14.0s')
        self.assertEqual(parsed, {'phase':'denoising','completed':3,'total':8})

    def test_runtime_lock_excludes_unused_training_and_web_servers(self):
        lock = (fast_video.ROOT / 'requirements-fastvideo.lock').read_text().lower()
        for package in ('torch==', 'gradio==', 'accelerate==', 'starlette=='):
            self.assertNotIn(package, lock)
        self.assertIn('--hash=sha256:', lock)

    def test_unsupported_duration_is_explicit(self):
        for frames in (56, 119, 363):
            with self.assertRaisesRegex(ValueError, '5–15 seconds'):
                fast_video.generation_params({'frames':frames})

    def test_command_keeps_prompt_out_of_shell_arguments(self):
        prompt = 'wave; $(touch /tmp/should-not-exist) `whoami`'
        with mock.patch.object(fast_video, 'state', return_value={'ready':True}):
            cmd, request = fast_video.command(prompt, '/tmp/out file.mp4', {'frames':122})
        self.assertNotIn(prompt, cmd)
        self.assertEqual(request['prompt'], prompt)
        self.assertEqual(request['output'], '/tmp/out file.mp4')

    def test_offline_worker_and_all_caches_are_managed(self):
        env = fast_video.environment(offline=True)
        self.assertEqual(env['HF_HUB_OFFLINE'],'1')
        self.assertEqual(env['TRANSFORMERS_OFFLINE'],'1')
        self.assertNotIn('PYTHONPATH',env)
        for key in ('HF_HOME','XDG_CACHE_HOME','UV_CACHE_DIR','UV_PYTHON_INSTALL_DIR'):
            self.assertTrue(Path(env[key]).is_relative_to(fast_video.HOME))

    def test_partial_install_is_not_ready(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td).resolve(); home = root / 'addons/fastvideo'; home.mkdir(parents=True)
            (home / 'installed.json').write_text(json.dumps({'revision':fast_video.REVISION}))
            with mock.patch.object(fast_video,'ROOT',root), mock.patch.object(fast_video,'HOME',home):
                self.assertFalse(fast_video.state()['ready'])

    def test_incomplete_owned_download_remains_visible_for_removal(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td).resolve(); home=root / 'addons/fastvideo'; home.mkdir(parents=True)
            (home / '.openmagia-managed').touch()
            with mock.patch.object(fast_video,'ROOT',root), mock.patch.object(fast_video,'HOME',home):
                state=fast_video.state()
                self.assertTrue(state['present'])
                self.assertFalse(state['ready'])

    def test_connecting_base_h3_weights_switches_away_from_fastvideo(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td).resolve(); native=root / 'h3'; native.touch()
            with mock.patch.object(server,'H3_BIN',str(fast_video.PYTHON)), \
                 mock.patch.object(server,'H3_MODEL','original-model'), \
                 mock.patch.object(server,'_h3_model_valid',return_value=True), \
                 mock.patch.object(server,'DEFAULTS',dict(server.DEFAULTS,h3_bin=str(native))), \
                 mock.patch.object(server,'MODEL_SOURCE_FILE',root / 'sources.json'), \
                 mock.patch.object(server,'_saved_model_sources',{}), \
                 mock.patch.object(server,'active_job',None), mock.patch.object(server,'queue',[]):
                server.select_model_source('h3',path=str(root / 'weights'),role='video_generation')
                self.assertEqual(server.H3_BIN,str(native))
                self.assertEqual(server.H3_MODEL,str(root / 'weights'))

    def test_symlink_install_is_rejected(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td).resolve(); outside = root / 'outside'; outside.mkdir()
            home = root / 'addons/fastvideo'; home.parent.mkdir(); home.symlink_to(outside)
            with mock.patch.object(fast_video,'ROOT',root), mock.patch.object(fast_video,'HOME',home):
                with self.assertRaisesRegex(ValueError,'symlink'):
                    fast_video.managed_home()
                self.assertFalse(fast_video.state()['ready'])

    def test_selecting_fastvideo_preserves_weights_and_venv_path(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td).resolve(); python = root / 'runtime/bin/python'; python.parent.mkdir(parents=True)
            python.symlink_to('/usr/bin/python3')
            with mock.patch.object(server.fast_video,'PYTHON',python), \
                 mock.patch.object(server.fast_video,'state',return_value={'ready':True}), \
                 mock.patch.object(server,'H3_BIN',server.H3_BIN), \
                 mock.patch.object(server,'MODEL_SOURCE_FILE',root / 'sources.json'), \
                 mock.patch.object(server,'_saved_model_sources',{}), \
                 mock.patch.object(server,'active_job',None), mock.patch.object(server,'queue',[]):
                weights = server.H3_MODEL
                server.select_model_source('h3_backend', model_id='fastvideo', role='video_generation')
                self.assertEqual(server.H3_BIN,str(python))
                self.assertTrue(server.using_fastvideo())
                self.assertEqual(server.H3_MODEL,weights)

    def test_switching_during_generation_is_rejected(self):
        with mock.patch.object(server,'active_job','running-scene'):
            with self.assertRaisesRegex(ValueError,'finish'):
                server.select_model_source('h3_backend',model_id='fastvideo')

    def test_uninstall_removes_only_its_receipted_tree_and_falls_back(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td).resolve(); home=root / 'addons/fastvideo'; home.mkdir(parents=True)
            sentinel = root / 'models/h3.safetensors'; sentinel.parent.mkdir(); sentinel.write_bytes(b'keep')
            registry = root / 'registry.json'
            registry.write_text(json.dumps({'installations':[{'id':'fv','backend_id':'fastvideo',
                'path':str(home),'managed':True,'receipt':[str(home)]}]}))
            with mock.patch.object(fast_video,'ROOT',root), mock.patch.object(fast_video,'HOME',home), \
                 mock.patch.object(server,'MODEL_REGISTRY_FILE',registry), \
                 mock.patch.object(server,'MODEL_SOURCE_FILE',root / 'sources.json'), \
                 mock.patch.object(server,'H3_BIN',str(fast_video.PYTHON)), \
                 mock.patch.object(server,'_saved_model_sources',{}), \
                 mock.patch.object(server,'active_job',None), mock.patch.object(server,'queue',[]), \
                 mock.patch.object(server,'model_installs',{}):
                result=server.uninstall_managed_model('fv')
                self.assertTrue(result['active_removed'])
                self.assertEqual(server.H3_BIN,server.DEFAULTS['h3_bin'])
            self.assertFalse(home.exists())
            self.assertEqual(sentinel.read_bytes(),b'keep')
            self.assertEqual(json.loads(registry.read_text())['installations'],[])

    def test_forged_uninstall_receipt_cannot_remove_outside_tree(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td).resolve(); outside=root / 'keep'; outside.mkdir()
            registry=root / 'registry.json'
            registry.write_text(json.dumps({'installations':[{'id':'fv','backend_id':'fastvideo',
                'path':str(outside),'managed':True,'receipt':[str(outside)]}]}))
            with mock.patch.object(server,'MODEL_REGISTRY_FILE',registry), \
                 mock.patch.object(server,'active_job',None), mock.patch.object(server,'queue',[]), \
                 mock.patch.object(server,'model_installs',{}):
                with self.assertRaisesRegex(ValueError,'safe managed'):
                    server.uninstall_managed_model('fv')
            self.assertTrue(outside.exists())

    def test_uninstall_during_install_is_rejected(self):
        with mock.patch.object(server,'_load_model_registry',return_value={'installations':[{'id':'fv','backend_id':'fastvideo'}]}), \
             mock.patch.object(server,'active_job',None), mock.patch.object(server,'queue',[]), \
             mock.patch.object(server,'model_installs',{'fastvideo':{'status':'running'}}):
            with self.assertRaisesRegex(ValueError,'finish'):
                server.uninstall_managed_model('fv')


if __name__ == '__main__':
    unittest.main()
