"""Isolated Qwen Image runtime; the editor server never imports torch."""
import json
import os
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parent
HOME = ROOT / "addons" / "qwen-image"
PYTHON = HOME / "runtime" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
MODEL = HOME / "model"
MODEL_ID = "Qwen/Qwen-Image-2.1-Turbo"

def state():
    return {"ready": PYTHON.is_file() and (HOME / "installed.json").is_file(),
            "model": MODEL_ID, "path": str(HOME)}

def install(report):
    import shutil
    uv = shutil.which("uv") or str(Path.home() / ".local/bin/uv")
    HOME.mkdir(parents=True, exist_ok=True)
    commands = [[uv, "venv", "--python", "3.12", str(HOME / "runtime")],
                [uv, "pip", "install", "--python", str(PYTHON), "torch", "torchvision", "accelerate", "pillow", "transformers>=5.17.0", "git+https://github.com/huggingface/diffusers.git@d42ebf9c6e0b46103b74283f5324357a67acaa04"],
                [str(PYTHON), str(Path(__file__)), "download"]]
    for command in commands:
        report("Installing runtime and downloading Qwen image weights…")
        with subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True) as process:
            for line in process.stdout:
                report(line.strip()[-400:])
            if process.wait():
                raise RuntimeError("Qwen installation failed. " + " ".join(command[:3]))
    (HOME / "installed.json").write_text(json.dumps({"model": MODEL_ID}))

def generate(request):
    import torch
    from PIL import Image
    from diffusers import QwenImage21Pipeline
    device = "mps" if torch.backends.mps.is_available() else "cuda" if torch.cuda.is_available() else "cpu"
    dtype = torch.bfloat16 if device != "cpu" else torch.float32
    pipe = QwenImage21Pipeline.from_pretrained(str(MODEL), dtype=dtype).to(device)
    images = [Image.open(p).convert("RGBA") for p in request.get("references", [])]
    kwargs = {"prompt": request["prompt"], "width": request["width"], "height": request["height"],
              "generator": torch.Generator("cpu").manual_seed(request.get("seed", 42)), "use_kv_cache": True}
    if images:
        kwargs["image"] = images
    # Turbo's eight-step sigma schedule is stored in the checkpoint.
    pipe(**kwargs).images[0].save(request["output"])

if __name__ == "__main__":
    if sys.argv[1] == "download":
        from diffusers import QwenImage21Pipeline
        from huggingface_hub import snapshot_download
        snapshot_download(MODEL_ID, revision="fc13ba1986fcb905854dc8de2f52eee395f49b5b", local_dir=str(MODEL), ignore_patterns=["assets/*", "*.md"])
    else:
        generate(json.loads(Path(sys.argv[1]).read_text()))
