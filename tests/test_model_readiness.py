import json
from pathlib import Path
import tempfile
import unittest
from model_readiness import partition_ready

class CheckpointReadiness(unittest.TestCase):
    def test_metadata_and_one_shard_do_not_count_as_an_installed_checkpoint(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); part = root / 'FL2VA'
            for name in ['transformer', 'text_encoder', 'video_vae/source', 'audio_vae', 'tokenizer']:
                folder = part / name; folder.mkdir(parents=True)
                (folder / 'config.json').write_text('{}')
                if name != 'tokenizer': (folder / 'model.safetensors').write_bytes(b'test')
            (part / 'tokenizer/tokenizer.json').write_text('{}')
            self.assertTrue(partition_ready(root, 'FL2VA'))
            index = part / 'transformer/model.safetensors.index.json'
            index.write_text(json.dumps({'weight_map':{'a':'one.safetensors','b':'two.safetensors'}}))
            (part / 'transformer/one.safetensors').write_bytes(b'test')
            self.assertFalse(partition_ready(root, 'FL2VA'))
            (part / 'transformer/two.safetensors').write_bytes(b'test')
            self.assertTrue(partition_ready(root, 'FL2VA'))
            (part / 'audio_vae/model.safetensors').write_bytes(b'')
            self.assertFalse(partition_ready(root, 'FL2VA'))
