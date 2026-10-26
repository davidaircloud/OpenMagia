#!/usr/bin/env python3
"""Run real local video jobs through OpenMagia without changing the active project.

Outputs and raw logs stay in ignored projects/fastvideo-benchmark-2026-10-26.
Resume skips completed cases. Execute only one copy and no other generation.
"""
import argparse
import json
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import server

PROMPT = ('A small red tin toy robot stands on a pale wooden table against a plain teal backdrop. '
          'The robot slowly raises its right arm, waves twice, then lowers it. '
          'One continuous locked-off medium shot, soft studio light, clear natural motion, '
          'no text, no cuts. Quiet mechanical clicks, no speech or music.')
SLUG = 'fastvideo-benchmark-2026-10-26'


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--backends', nargs='+', default=['h3-metal', 'h3mlx', 'fastvideo'])
    parser.add_argument('--force', action='store_true', help='Rerun completed cases without deleting project media')
    args = parser.parse_args()
    directory = server.proj_dir(SLUG)
    directory.mkdir(parents=True, exist_ok=True)
    (directory / 'media').mkdir(exist_ok=True)
    (directory / 'uploads').mkdir(exist_ok=True)
    if not (directory / 'project.json').exists():
        p = server.new_project()
        p.update(slug=SLUG, name='Video backend benchmark · 26 October 2026')
        server.save_project(p)
    report_path = directory / 'benchmark.json'
    results = json.loads(report_path.read_text()) if report_path.exists() else []
    for backend in args.backends:
        server.H3_BIN = str({'h3-metal':Path(server.DEFAULTS['h3_bin']),
                             'h3mlx':server.H3MLX_BIN, 'fastvideo':server.fast_video.PYTHON}[backend])
        for mode in ['text', 'image']:
            case = backend + '-' + mode
            if not args.force and any(r['case'] == case and r.get('status') == 'ready' for r in results):
                continue
            project = server.load_project_slug(SLUG)
            reference = directory / 'uploads/reference.png'
            if mode == 'image' and not reference.exists():
                raise RuntimeError('The common reference image must come from the first text render.')
            if mode == 'image' and not any(m['id'] == 'benchmark-reference' for m in project['media']):
                media = server.add_media(project, reference, 'Common robot reference', 'image', 'upload')
                media['id'] = 'benchmark-reference'
            scene = {'id':case, 'name':case, 'prompt':PROMPT, 'generation_type':'video',
                     'character_ids':[], 'reference_media_ids':['benchmark-reference'] if mode == 'image' else [],
                     'style_profile':{}, 'use_project_style':False, 'status':'queued',
                     'optimize_references':False,
                     'params':{'width':512, 'height':512, 'frames':120, 'steps':20,
                               'layers':35 if backend == 'h3mlx' else 45, 'reuse':2,
                               'seed':20261026, 'audio_mode':'effects', 'quality':'balanced'}}
            project['scenes'] = [s for s in project['scenes'] if s['id'] != case] + [scene]
            server.save_project(project)
            original_monitor = server.monitor_h3_process
            log_path = directory / (case + '.log')
            with log_path.open('a') as log:
                log.write('\nRUN ' + time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()) + '\n')
            def monitor(*a, **kw):
                with log_path.open('a') as log:
                    for line in original_monitor(*a, **kw):
                        log.write(line + '\n'); log.flush()
                        yield line
            server.monitor_h3_process = monitor
            server.active_job = case
            start = time.perf_counter()
            print('START', case, flush=True)
            server.run_job_guarded(case, project)
            elapsed = time.perf_counter() - start
            server.monitor_h3_process = original_monitor
            project = server.load_project_slug(SLUG)
            scene = next(s for s in project['scenes'] if s['id'] == case)
            output = directory / 'media' / ('gen-' + case + '.mp4')
            record = {'case':case, 'status':scene['status'], 'wall_seconds':round(elapsed, 3),
                      'params':scene['params'], 'prompt':PROMPT,
                      'execution_prompt':scene.get('execution_prompt'), 'error':scene.get('error'),
                      'output':str(output), 'probe':server.nle.probe(output) if output.exists() else None}
            if mode == 'text' and backend == 'h3-metal' and scene['status'] == 'ready' and not reference.exists():
                server.nle.extract_frame(output, reference, 'first')
            results = [r for r in results if r['case'] != case] + [record]
            report_path.write_text(json.dumps(results, indent=2) + '\n')
            print('RESULT', json.dumps({k:v for k,v in record.items() if k not in ('prompt','execution_prompt')}), flush=True)
            if scene['status'] != 'ready':
                raise RuntimeError(scene.get('error') or 'Generation failed')


if __name__ == '__main__':
    main()
