"""Check complete H3 checkpoint partitions without loading model weights."""
import json
from pathlib import Path
import sys


def partition_ready(root, mode):
    partition = Path(root).expanduser() / mode
    if not (partition / 'transformer/config.json').is_file() or not (partition / 'tokenizer/tokenizer.json').is_file():
        return False
    for component in ('transformer', 'text_encoder', 'video_vae/source', 'audio_vae'):
        folder = partition / component
        indexes = list(folder.glob('*.safetensors.index.json'))
        weights = list(folder.glob('*.safetensors'))
        if indexes:
            try:
                names = set()
                for index in indexes:
                    names.update(json.loads(index.read_text())['weight_map'].values())
                if not names or any(Path(name).name != name for name in names): return False
                weights = [folder / name for name in names]
            except (OSError, ValueError, KeyError, TypeError):
                return False
        try:
            if not weights or any(not path.is_file() or path.stat().st_size == 0 for path in weights):
                return False
        except OSError:
            return False
    return True


if __name__ == '__main__':
    raise SystemExit(0 if partition_ready(sys.argv[1], sys.argv[2]) else 1)
