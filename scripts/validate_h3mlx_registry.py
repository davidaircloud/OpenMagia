#!/usr/bin/env python3
"""Verify every Metal pipeline dispatched by H3MLX is registered at startup."""

import re
import sys
from pathlib import Path


def missing_pipelines(source):
    registry = source[source.index("NSMutableArray<NSString *> *names"):
                      source.index("gpu.pipelines = pipelines")]
    registered = set(re.findall(r'@"(h3_[A-Za-z0-9_]+)"', registry))
    dispatched = set(re.findall(
        r'(?:pipeline\(gpu,\s*|h3_gpu_dispatch_[^(]*\([^,]+,\s*)@"(h3_[A-Za-z0-9_]+)"',
        source))
    return sorted(dispatched - registered)


def main():
    path = Path(sys.argv[1])
    missing = missing_pipelines(path.read_text())
    if missing:
        print("Unregistered H3MLX Metal pipelines: " + ", ".join(missing), file=sys.stderr)
        return 1
    print("H3MLX Metal pipeline registry verified")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
