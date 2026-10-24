import tempfile
import json
import unittest
from pathlib import Path
from unittest import mock
import server
import qwen_image

class QwenImageTests(unittest.TestCase):
    def test_installed_image_model_joins_shared_model_registry(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); home = root / 'qwen-image'; home.mkdir()
            with mock.patch.object(server, 'MODEL_REGISTRY_FILE', root / 'registry.json'), \
                 mock.patch.object(qwen_image, 'HOME', home), \
                 mock.patch.object(qwen_image, 'state', return_value={'ready': True}):
                state = server.model_management_state()
            item = next(x for x in state['installations'] if x['backend_id'] == 'qwen-image')
            self.assertTrue(item['available'])
            self.assertEqual(item['media'], 'image')
            self.assertTrue(next(x for x in state['catalog'] if x['id'] == 'qwen-image')['installed'])

    def test_image_uninstall_requires_exact_managed_receipt(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); home = root / 'qwen-image'; home.mkdir()
            other = root / 'other'; other.mkdir(); registry = root / 'registry.json'
            def receipt(path):
                registry.write_text(json.dumps({'installations':[{'id':'image', 'backend_id':'qwen-image',
                    'path':str(path), 'managed':True, 'receipt':[str(path)]}]}))
            with mock.patch.object(server, 'MODEL_REGISTRY_FILE', registry), mock.patch.object(qwen_image, 'HOME', home):
                receipt(other)
                with self.assertRaisesRegex(ValueError, 'safe managed'):
                    server.uninstall_managed_model('image')
                self.assertTrue(other.exists())
                receipt(home); server.uninstall_managed_model('image')
                self.assertFalse(home.exists()); self.assertTrue(other.exists())

    def test_qwen_dimensions_and_schedule_survive_worker_normalization(self):
        params = server.clamp_generation_params({'image_model':'qwen-image', 'width':1024, 'height':1824, 'steps':50}, 'image')
        self.assertEqual((params['width'],params['height'],params['steps']), (1024,1824,8))
        self.assertEqual(params,server.clamp_generation_params(params,'image'))

    def test_failed_qwen_job_marks_media_and_releases_queue(self):
        project={'slug':'test','scenes':[{'id':'q','prompt':'test','params':{'width':512,'height':512}}], 'media':[{'scene_id':'q'}]}
        with tempfile.TemporaryDirectory() as temp, mock.patch.object(server,'proj_dir',return_value=Path(temp)), mock.patch.object(server,'scene_all_references',side_effect=ValueError('missing image')), mock.patch.object(server,'load_project_slug',return_value=project), mock.patch.object(server,'save_project'), mock.patch.object(server,'pump_queue') as pump:
            server.active_job='q'
            server.run_qwen_image_job('q',project)
            self.assertIsNone(server.active_job)
            self.assertEqual(project['media'][0]['status'],'error')
            self.assertIn('missing image',project['scenes'][0]['error'])
            pump.assert_called_once()

    def test_runtime_readiness_requires_completed_install(self):
        with tempfile.TemporaryDirectory() as temp, mock.patch.object(qwen_image,'HOME',Path(temp)), mock.patch.object(qwen_image,'PYTHON',Path(temp)/'python'):
            self.assertFalse(qwen_image.state()['ready'])
