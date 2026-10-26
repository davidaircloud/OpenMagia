"""Optional FastVideo MLX backend. Heavy imports stay in a disposable worker.

All downloads, conversions, caches and receipts belong to addons/fastvideo.
Upstream code and model revisions are deliberately reviewed/pinned, not latest.
"""
import json
import os
from pathlib import Path
import platform
import subprocess
import sys

ROOT = Path(__file__).resolve().parent
HOME = ROOT / 'addons' / 'fastvideo'
SOURCE = HOME / 'source'
PYTHON = HOME / 'runtime' / 'bin' / 'python'
REVISION = 'a0e6cf1f5086c9af735d73e6c0b80dbc1d5817b3'
MODELS = {
    'text': ('FastVideo/FastVideo-FastH3-Trim-8-Step-MLX-INT6', 'dc6ab6cde207c283708a106000f5bcd98d3cf39b'),
    'reference': ('FastVideo/FastVideo-FastH3-Omni-8-Step-V1', '53e0d26a1691aa473b6262d8196a8feb2c297df0'),
    'encoder': ('FastVideo/FastH3-OmniRef-NVFP4-QAD', 'f01e6a23ac6066aa9eb622cf94b6259442e70ec2'),
    'decoder': ('FastVideo/FastH3-NVFP4-VAE-Decoder', 'bd0c248f2a0f57ca643b3df2ac1f375fa55101fb'),
    'lightvae': ('lightx2v/LightVAE', '4925ea978147b9de492d7a5fd4f9c6d1854ca1cc'),
}


def managed_home():
    # Never let a symlink turn a receipt into permission to remove another tree.
    expected = ROOT.resolve() / 'addons' / 'fastvideo'
    if HOME.is_symlink() or HOME.resolve() != expected:
        raise ValueError('FastVideo managed directory must not be a symlink or redirected folder.')
    return expected


def environment(offline=False):
    env = dict(os.environ)
    env.update(HF_HOME=str(HOME / 'cache' / 'huggingface'),
               XDG_CACHE_HOME=str(HOME / 'cache'), UV_CACHE_DIR=str(HOME / 'cache' / 'uv'),
               UV_PYTHON_INSTALL_DIR=str(HOME / 'python'), HF_HUB_DISABLE_TELEMETRY='1',
               DO_NOT_TRACK='1', WANDB_MODE='disabled', PYTHONUNBUFFERED='1',
               HF_XET_CACHE=str(HOME / 'cache' / 'xet'), HF_XET_HIGH_PERFORMANCE='1',
               FASTVIDEO_MLX_DQ_GEMM='1')
    env.pop('PYTHONPATH', None)
    if offline:
        env.update(HF_HUB_OFFLINE='1', TRANSFORMERS_OFFLINE='1')
    return env


def state():
    present = False
    try:
        managed_home()
        present = (HOME / '.openmagia-managed').is_file() or (HOME / 'installed.json').is_file()
        receipt = json.loads((HOME / 'installed.json').read_text())
        files = ['source/fastvideo/mlx_runtime/minimax_h3_pipeline.py',
                 'text/fastvideo_inference.json', 'text/transformer/mlx_h3_dit.json',
                 'text/transformer/mlx_h3_dit.safetensors',
                 'text/audio_vae/diffusion_pytorch_model.safetensors',
                 'text/vae/diffusion_pytorch_model.safetensors',
                 'text/tokenizer/tokenizer.json',
                 'text/text_encoder/model.safetensors',
                 'omni-mlx/int6/mlx_h3_dit.safetensors', 'omni-mlx/int6/mlx_h3_dit.json',
                 'omni/text_encoder/model.safetensors',
                 'omni/tokenizer/tokenizer.json', 'omni/audio_vae/config.json',
                 'omni/audio_vae/diffusion_pytorch_model.safetensors',
                 'omni/vae/config.json',
                 'omni/fastvideo_inference.json',
                 'omni/text_encoder/mlx_h3_nvfp4_encoder.json',
                 'decoder/quality-36L/decoder-nvfp4.safetensors',
                 'lightvae/lightvae-lite-h3-encoder.safetensors']
        ready = (receipt.get('revision') == REVISION and PYTHON.is_file()
                 and all((HOME / f).is_file() for f in files))
    except (OSError, ValueError):
        ready = False
    return {'ready': ready, 'present': present, 'path': str(HOME), 'revision': REVISION,
            'text_model': MODELS['text'][0], 'reference_model': MODELS['reference'][0]}


def generation_params(params):
    out = dict(params)
    # Distilled weights have a fixed eight-forward schedule. Native H3 pads
    # time to 17n+5; record that explicitly rather than misreporting duration.
    frames = int(out.get('frames', 120))
    if not 120 <= frames <= 362:
        raise ValueError('FastVideo generates 5–15 seconds.')
    out.update(steps=8, requested_frames=int(out.get('requested_frames', frames)),
               frames=frames + (5 - frames) % 17, backend_adjusted='fastvideo')
    return out


def command(prompt, output, params, references=()):
    if not state()['ready']:
        raise ValueError('Install FastVideo in Settings → Modules first.')
    request = {'prompt': prompt, 'output': str(output), 'params': params,
               'references': list(references)}
    # JSON travels on stdin: no shell, no prompt/path interpolation.
    return [str(PYTHON), str(Path(__file__).resolve()), 'generate'], request


def _run(command, report, cwd=None):
    with subprocess.Popen(command, cwd=cwd or str(ROOT), env=environment(),
                          stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                          text=True, errors='replace') as process:
        tail = []
        for line in process.stdout:
            clean = line.strip()[-500:]
            if clean:
                report(clean)
                tail.append(clean)
                tail = tail[-8:]
        if process.wait():
            raise RuntimeError('FastVideo setup failed: ' + '\n'.join(tail))


def install(report):
    if sys.platform != 'darwin' or platform.machine().lower() != 'arm64':
        raise ValueError('This FastVideo module requires Apple Silicon macOS.')
    managed_home().mkdir(parents=True, exist_ok=True)
    (HOME / '.openmagia-managed').touch()
    # Bootstrap only the installer; no changes to the editor's Python packages.
    uv = HOME / 'installer' / 'bin' / 'uv'
    if not uv.is_file():
        _run([sys.executable, '-m', 'venv', str(HOME / 'installer')], report)
        _run([str(HOME / 'installer' / 'bin' / 'python'), '-m', 'pip', 'install', 'uv==0.13.5'], report)
    if not (SOURCE / '.git').exists():
        _run(['git', 'clone', '--no-checkout', '--filter=blob:none',
              'https://github.com/hao-ai-lab/FastVideo.git', str(SOURCE)], report)
    _run(['git', '-C', str(SOURCE), 'fetch', '--depth', '1', 'origin', REVISION], report)
    _run(['git', '-C', str(SOURCE), 'checkout', '--detach', REVISION], report)
    if not PYTHON.is_file():
        _run([str(uv), 'venv', '--python', '3.12', str(HOME / 'runtime')], report)
    report('Installing the isolated Apple Silicon runtime…')
    lock = ROOT / 'requirements-fastvideo.lock'
    _run([str(uv), 'pip', 'sync', '--require-hashes', '--python', str(PYTHON), str(lock)], report)
    report('Downloading pinned FastH3 text and reference weights…')
    _run([str(PYTHON), str(Path(__file__).resolve()), 'prepare'], report)
    (HOME / 'installed.json').write_text(json.dumps({'revision': REVISION, 'models': MODELS}, indent=2) + '\n')
    report('FastVideo installed. Select Use for video to activate it.')


def activate_mlx_source():
    """Load the pinned MLX subpackage without CUDA/training eager imports.

    Upstream fastvideo.__init__ imports the full distributed Torch API. Its
    MLX implementation is independent; a package namespace lets Python load
    those original submodules directly, without modifying their source.
    """
    import importlib.machinery
    import types
    if 'fastvideo' in sys.modules:
        raise RuntimeError('FastVideo MLX must run in a fresh isolated worker.')
    package = types.ModuleType('fastvideo')
    package.__path__ = [str(SOURCE / 'fastvideo')]
    package.__package__ = 'fastvideo'
    package.__spec__ = importlib.machinery.ModuleSpec('fastvideo', loader=None, is_package=True)
    package.__spec__.submodule_search_locations = package.__path__
    sys.modules['fastvideo'] = package


def prepare():
    os.environ.update(environment())
    from huggingface_hub import snapshot_download
    filters = {
        'text': ['*.json', '*.safetensors', '*.txt', 'LICENSE*', 'NOTICE*', 'README.md'],
        'reference': ['transformer_ref/*', '*scheduler/*', '*.json', 'LICENSE*', 'NOTICE*', 'README.md'],
        'encoder': ['text_encoder/*', 'tokenizer/*', 'processor/*', 'vae/config.json', 'audio_vae/*', 'LICENSE*', 'NOTICE*', 'README.md'],
        'decoder': ['quality-36L/decoder-nvfp4.safetensors', 'quality-36L/config.json', 'LICENSE*', 'NOTICE*', 'README.md'],
        'lightvae': ['lightvae-lite-h3-encoder.safetensors', 'LICENSE*', 'NOTICE*', 'README.md'],
    }
    from concurrent.futures import ThreadPoolExecutor
    def download(item):
        key, (model, revision) = item
        print('Downloading ' + model, flush=True)
        snapshot_download(model, revision=revision, local_dir=str(HOME / key),
                          allow_patterns=filters[key], max_workers=4)
    with ThreadPoolExecutor(max_workers=3) as pool:
        list(pool.map(download, MODELS.items()))
    # Successful snapshots supersede abandoned Xet transfer fragments from
    # interrupted installs. Keep cleanup within this module's download caches.
    for key in MODELS:
        cache = HOME / key / '.cache/huggingface/download'
        for fragment in cache.rglob('*.incomplete'):
            fragment.unlink(missing_ok=True)
    # Convert only safetensors, never pickle files or Hub-provided Python code.
    subprocess.run([str(PYTHON), str(Path(__file__).resolve()), 'convert', '--model-root', str(HOME / 'reference/transformer_ref'),
                    '--out', str(HOME / 'omni-mlx'), '--formats', 'int6', '--include-vsa',
                    '--nvfp4-conditioner-root', str(HOME / 'encoder/text_encoder')],
                   cwd=str(SOURCE), env=environment(), check=True)
    omni = HOME / 'omni'
    omni.mkdir(exist_ok=True)
    links = {key: HOME / 'reference' / key for key in
             ['transformer_ref', 'scheduler', 'audio_scheduler', 'fastvideo_inference.json']}
    links.update({key: HOME / 'encoder' / key for key in ['tokenizer', 'processor', 'audio_vae', 'vae']})
    links['text_encoder'] = HOME / 'omni-mlx/nvfp4-encoder'
    for name, target in links.items():
        link = omni / name
        if not link.is_symlink():
            link.symlink_to(os.path.relpath(target, omni), target_is_directory=target.is_dir())
    # Verify imports and checkpoint manifests before publishing the receipt.
    activate_mlx_source()
    from fastvideo.mlx_runtime.minimax_h3_pipeline import MiniMaxH3MLXPipeline
    assert MiniMaxH3MLXPipeline


def attention_backend():
    """Use validated TensorOps on M5; keep upstream auto on other Macs."""
    import mlx.core as mx
    if str(mx.device_info().get('device_name', '')).startswith('Apple M5'):
        from fastvideo.mlx_runtime.minimax_h3_vsa_nax import get_nax_provenance
        # Changed private MLX headers fail explicitly, as upstream requires.
        return 'nax', get_nax_provenance()
    return 'auto', None


def generate(request):
    os.environ.update(environment(offline=True))
    activate_mlx_source()
    from fastvideo.mlx_runtime.minimax_h3_pipeline import MiniMaxH3MLXPipeline
    from fastvideo.mlx_runtime.minimax_h3_reference import MiniMaxH3Reference
    refs = request.get('references') or []
    params = request['params']
    reference = bool(refs)
    attention, provenance = attention_backend()
    kwargs = {}
    if reference:
        kwargs.update(vae_encoder_path=HOME / 'lightvae/lightvae-lite-h3-encoder.safetensors',
                      vae_decoder_path=HOME / 'decoder/quality-36L/decoder-nvfp4.safetensors')
    pipeline = MiniMaxH3MLXPipeline(
        model_root=HOME / ('omni' if reference else 'text'),
        mlx_dit_checkpoint=HOME / ('omni-mlx/int6' if reference else 'text/transformer'),
        conditioner_mode='nvfp4', resident=False, vae_dtype='bf16',
        prompt_cache_dir=HOME / 'cache/prompts', mlx_cache_limit_gib=8, **kwargs)
    try:
        result = pipeline.generate(
            request['prompt'], output_path=request['output'],
            width=params['width'], height=params['height'], num_frames=params['frames'],
            seed=int(params.get('seed', 42)), num_steps=8,
            references=[MiniMaxH3Reference(source=Path(r['path']), media_type=r['type']) for r in refs] if reference else None,
            vsa=True, vsa_impl=attention, vae_tile_height=256, vae_tile_width=256,
            reference_max_pixels=params['width'] * params['height'] if reference else None)
        print('OPENMAGIA_RESULT ' + json.dumps({
            'model': MODELS['reference' if reference else 'text'][0], 'revision': REVISION,
            'attention_backend':attention, 'attention_provenance':provenance,
            'reference_max_pixels':params['width'] * params['height'] if reference else None,
            'timings_s': result.timings, 'peak_memory_gib': result.peak_memory_gib}), flush=True)
    finally:
        pipeline.close()


if __name__ == '__main__':
    if sys.argv[1] == 'prepare':
        prepare()
    elif sys.argv[1] == 'convert':
        import runpy
        activate_mlx_source()
        sys.argv = [str(SOURCE / 'scripts/checkpoint_conversion/convert_minimax_h3_mlx.py'), *sys.argv[2:]]
        runpy.run_path(sys.argv[0], run_name='__main__')
    else:
        generate(json.load(sys.stdin))
