#!/usr/bin/env python3
"""Repair the managed H3MLX pipeline registry without editing dispatch calls."""
from pathlib import Path
import sys

PAIRS = {
    'h3_video_qkv_rope_f32': ['h3_video_qkv_rope_bf16'],
    'h3_scale_add_f32': ['h3_scale_add_bf16', 'h3_bias_add_bf16'],
}

def patch_source(source):
    # Repair calls changed by the old installer's global sed replacements.
    for base, extras in PAIRS.items():
        expanded = ', '.join('@"'+name+'"' for name in [base]+extras)
        for dimensions in ('1d', '2d', '3d'):
            source = source.replace('h3_gpu_dispatch_'+dimensions+'(gpu, '+expanded+',',
                                    'h3_gpu_dispatch_'+dimensions+'(gpu, @"'+base+'",')
    start = source.index('NSMutableArray<NSString *> *names')
    end = source.index('gpu.pipelines = pipelines', start)
    registry = source[start:end]
    for base, extras in PAIRS.items():
        missing = [name for name in extras if '@"'+name+'"' not in registry]
        if missing:
            needle = '@"'+base+'"'
            if needle not in registry:
                raise ValueError('H3MLX registry layout changed: missing '+base)
            registry = registry.replace(needle, ', '.join([needle]+['@"'+name+'"' for name in missing]), 1)
    return source[:start]+registry+source[end:]

if __name__ == '__main__':
    path = Path(sys.argv[1]); original = path.read_text(); updated = patch_source(original)
    if updated != original: path.write_text(updated)
    print('H3MLX pipeline registry patch verified')
