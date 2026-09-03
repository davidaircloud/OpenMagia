#!/usr/bin/env python3
"""Minimal FFprobe-compatible visual-size inspector for h3.c.

OpenMagia's managed imageio-ffmpeg runtime ships FFmpeg but not FFprobe.
h3.c only asks FFprobe for the first visual stream's width and height, so this
small adapter obtains the same information from FFmpeg's media summary.
"""
import os
import re
import shutil
import subprocess
import sys


def main():
    if len(sys.argv) < 2:
        return 2
    media_path = sys.argv[-1]
    ffmpeg = os.environ.get("H3_FFMPEG") or shutil.which("ffmpeg")
    if not ffmpeg:
        print("ffprobe compatibility adapter: ffmpeg is unavailable", file=sys.stderr)
        return 1
    try:
        result = subprocess.run(
            [ffmpeg, "-hide_banner", "-i", media_path],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            text=True,
            errors="replace",
            timeout=30,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        print(f"ffprobe compatibility adapter: {exc}", file=sys.stderr)
        return 1
    for line in result.stderr.splitlines():
        if "Video:" not in line:
            continue
        match = re.search(r"(?<![\w.])(\d{1,6})x(\d{1,6})(?![\w.])", line)
        if match:
            print(f"{match.group(1)}x{match.group(2)}")
            return 0
    print(f"ffprobe compatibility adapter: no visual stream in {media_path}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
