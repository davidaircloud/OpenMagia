#!/usr/bin/env python3
"""Package measured video outputs and a portable, local comparison report."""
import csv
import hashlib
import html
import json
import platform
from pathlib import Path
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
PROJECT = ROOT / 'projects/fastvideo-benchmark-2026-10-26'
OUT = ROOT / 'benchmarks/2026-10-26-fastvideo'
NAMES = {'h3-metal':'h3.c', 'h3mlx':'H3MLX', 'fastvideo':'FastVideo'}


def main():
    raw = json.loads((PROJECT / 'benchmark.json').read_text())
    if len(raw) != 6 or any(r.get('status') != 'ready' for r in raw):
        raise RuntimeError('All six real generations must succeed before packaging.')
    project = json.loads((PROJECT / 'project.json').read_text())
    statuses={s['id']:s['status'] for s in project['scenes']}
    if any(statuses.get(r['case']) != 'ready' for r in raw):
        raise RuntimeError('A benchmark scene is still running or failed.')
    OUT.mkdir(parents=True, exist_ok=True)
    shutil.copy2(PROJECT / 'uploads/reference.png', OUT / 'reference.png')
    results = []
    attention_provenance = None
    for backend in NAMES:
        for mode in ('text', 'image'):
            case = backend + '-' + mode
            r = next(r for r in raw if r['case'] == case)
            filename = case + '.mp4'
            shutil.copy2(r['output'], OUT / filename)
            generation=next((m.get('generation',{}) for m in project['media'] if m.get('scene_id')==case),{})
            runtime=generation.get('runtime') or {}
            peaks=runtime.get('peak_memory_gib') or {}
            if runtime.get('attention_provenance'):attention_provenance=runtime['attention_provenance']
            results.append({'backend':NAMES[backend], 'mode':mode,
                'wall_seconds':r['wall_seconds'], 'duration_seconds':r['probe']['duration'],
                'width':r['probe']['w'], 'height':r['probe']['h'],
                'audio':r['probe']['hasAudio'], 'requested_frames':120,
                'passes':8 if backend == 'fastvideo' else 20,
                'layers':None if backend == 'fastvideo' else r['params']['layers'],
                'reuse':None if backend == 'fastvideo' else r['params']['reuse'],
                'seed':20261026, 'output':filename,
                'model':runtime.get('model') or ('MiniMax-H3 Ref2VA' if mode=='image' else 'MiniMax-H3 FL2VA'),
                'attention':runtime.get('attention_backend') or 'native Metal',
                'peak_mlx_gib':round(max(peaks.values()),3) if peaks else None,
                'sha256':hashlib.sha256((OUT / filename).read_bytes()).hexdigest()})
    revisions={}
    for name,path in [('h3.c',ROOT/'h3.c'),('H3MLX',ROOT/'addons/h3mlx'),('FastVideo',ROOT/'addons/fastvideo/source')]:
        revisions[name]=subprocess.run(['git','-C',str(path),'rev-parse','HEAD'],capture_output=True,text=True,check=True).stdout.strip()
    document = {'source_revisions':revisions, 'fastvideo_attention_provenance':attention_provenance, 'hardware':'Apple M5 Max, 128 GB unified memory',
        'date':'2026-10-26', 'os':'macOS '+platform.mac_ver()[0],
        'run_order':['FastVideo text','FastVideo image','h3.c text','h3.c image','H3MLX text','H3MLX image'], 'method':'One serial process-per-job generation per case. All final timings exclude concurrent model downloads.',
        'prompt':raw[0]['prompt'], 'reference_sha256':hashlib.sha256((OUT / 'reference.png').read_bytes()).hexdigest(),
        'results':results}
    (OUT / 'results.json').write_text(json.dumps(document, indent=2)+'\n')
    with (OUT / 'results.csv').open('w', newline='') as f:
        writer=csv.DictWriter(f,fieldnames=list(results[0]));writer.writeheader();writer.writerows(results)
    rows=[]
    cards=[]
    for mode in ('text','image'):
        cards.append('<h2>'+('Text to video' if mode=='text' else 'One image + text')+'</h2><div class="grid">')
        for r in results:
            if r['mode'] != mode:continue
            minutes, seconds=divmod(round(r['wall_seconds']),60)
            label=f'{minutes}m {seconds:02d}s'
            cards.append(f'<article><h3>{html.escape(r["backend"])} <span>{label}</span></h3><video controls muted preload="metadata" src="{r["output"]}"></video><p>{r["passes"]} passes · {r["duration_seconds"]:.2f}s output</p></article>')
            rows.append(f'| {r["backend"]} | {mode} | {r["wall_seconds"]:.3f} | {r["duration_seconds"]:.2f} | {r["passes"]} |')
        cards.append('</div>')
    caveat=('Single-run measurements of different app presets and distilled models, not equal computational work. '
            'The reference image comes from the original h3.c text output, which may favor that model’s appearance. '
            'The first exploratory native timings were discarded and rerun without downloads. '
            'OS file and shader caches may be warm. Run order: FastVideo text/image, h3.c text/image, then H3MLX text/image. Timings include process startup, conditioning, denoising, decoding and media registration. '
            'FastVideo uses Trim INT6 for text and Omni INT6 for references, with M5 NAX attention and reference pixels capped to the output area. No temporal interpolation or spatial upscaling was enabled. FastVideo memory figures are peak MLX allocations, not total RAM; native peak memory was not measured.')
    report='''<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>OpenMagia video benchmark</title><style>
body{max-width:1320px;margin:40px auto;padding:0 24px;background:#101012;color:#ececf0;font:16px/1.6 system-ui}h1{font-size:36px;line-height:1.1}h2{margin-top:36px}p{color:#bcbcc5}.grid{display:grid;grid-template-columns:repeat(3,1fr);gap:20px}article{padding:16px;background:#1c1c21;border-radius:12px}h3{margin:0 0 14px}h3 span{float:right;color:#cbc4ee;font-weight:500}video{width:100%;border-radius:8px}article p{margin:8px 0 0;font-size:14px}.reference{width:180px;border-radius:10px}button{padding:10px 16px;border:1px solid #555;border-radius:8px;background:#292532;color:white;font:inherit;cursor:pointer}details{margin-top:28px}pre{white-space:pre-wrap;color:#bcbcc5}a{color:#cbc4ee}@media(max-width:800px){.grid{grid-template-columns:1fr}h1{font-size:28px}}</style>
<h1>OpenMagia video benchmark</h1><p>Apple M5 Max · 128 GB · 26 October 2026<br>512 × 512 · approximately five seconds · seed 20261026</p><button onclick="document.querySelectorAll('video').forEach(v=>{v.currentTime=0;v.muted=true;v.play()})">Play all muted</button>'''+''.join(cards)+f'<h2>Common reference</h2><img class="reference" src="reference.png" alt="Red toy robot benchmark reference"><details><summary>Prompt and method</summary><pre>{html.escape(document["prompt"])}</pre><p>{html.escape(caveat)}</p></details><p><a href="results.csv">CSV</a> · <a href="results.json">JSON and file hashes</a></p></html>'
    (OUT / 'comparison.html').write_text(report)
    markdown='# OpenMagia video benchmark — 26 October 2026\n\nApple M5 Max, 128 GB unified memory. 512×512, approximately five seconds, seed 20261026.\n\n| Backend | Input | Wall seconds | Output seconds | Passes |\n|---|---|---:|---:|---:|\n'+'\n'.join(rows)+'\n\n'+caveat+'\n\nPrompt:\n\n> '+document['prompt']+'\n\nOpen [comparison.html](comparison.html) locally to play the six videos. [CSV](results.csv) · [JSON](results.json).\n'
    (OUT / 'README.md').write_text(markdown)
    audit=ROOT / 'addons/fastvideo/dependency-audit-minimal.json'
    if audit.exists():shutil.copy2(audit,OUT / 'dependency-audit.json')
    print(OUT)


if __name__ == '__main__':
    main()
