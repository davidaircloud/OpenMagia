import tempfile
import unittest
from pathlib import Path
from unittest import mock
import server
import qwen_image

class QwenImageTests(unittest.TestCase):
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
