#!/usr/bin/env python3
"""OpenMagia NLE — media probing, clip pre-rendering, and ffmpeg composition.

The timeline is a small non-linear edit: ordered tracks (video overlay tracks
plus one audio track), each holding clips that reference a media item with a
trim window [in, out], an optional zoom, and (on video) a transition into it.
Export pre-renders every clip to a normalized intermediate, then composes with
ffmpeg: xfade for base-track transitions, overlay for upper video tracks, and
acrossfade/amix for audio.
"""
import json
import math
import os
import re
import subprocess
import shutil
import time
from pathlib import Path

FPS = 24

# UI transition name -> (ffmpeg xfade transition, default seconds)
TRANSITIONS = {
    "cut":      ("fade", 0.0),
    "dissolve": ("dissolve", 0.5),
    "fade":     ("fade", 0.5),
    "wipe":     ("wipeleft", 0.5),
    "slide":    ("slideleft", 0.5),
    "circle":   ("circleopen", 0.5),
}


def run(cmd):
    return subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)


def probe(path):
    """Return {duration, w, h, hasAudio} for a media file."""
    ffprobe = shutil.which("ffprobe")
    if not ffprobe:
        r = run(["ffmpeg", "-hide_banner", "-i", str(path), "-f", "null", "-"])
        text = r.stderr or ""
        duration = re.search(r"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)", text)
        video = re.search(r"Stream[^\n]*Video:[^\n]*?\b(\d{2,5})x(\d{2,5})\b", text)
        has_audio = bool(re.search(r"Stream[^\n]*Audio:", text))
        seconds = ((int(duration.group(1)) * 60 + int(duration.group(2))) * 60 + float(duration.group(3))) if duration else 0.0
        return {"duration": seconds, "w": int(video.group(1)) if video else 0,
                "h": int(video.group(2)) if video else 0, "hasAudio": has_audio}
    r = run([ffprobe, "-v", "error", "-select_streams", "v:0",
             "-show_entries", "stream=width,height", "-show_entries", "format=duration",
             "-of", "json", str(path)])
    try:
        d = json.loads(r.stdout)
    except Exception:
        return {"duration": 0.0, "w": 0, "h": 0, "hasAudio": False}
    st = (d.get("streams") or [{}])[0]
    dur = float((d.get("format") or {}).get("duration") or 0)
    # audio presence
    a = run([ffprobe, "-v", "error", "-select_streams", "a:0",
             "-show_entries", "stream=codec_type", "-of", "csv=p=0", str(path)])
    has_audio = "audio" in (a.stdout or "")
    return {"duration": dur, "w": int(st.get("width") or 0),
            "h": int(st.get("height") or 0), "hasAudio": has_audio}


def make_thumb(src, out, max_w=320):
    """Extract a single poster frame from a video to a small JPEG."""
    out = Path(out)
    info = probe(src)
    dur = info.get("duration") or 0
    t = min(0.2, dur * 0.1) if dur > 0 else 0
    cmd = ["ffmpeg", "-y", "-v", "error", "-ss", f"{t:.3f}", "-i", str(src),
           "-frames:v", "1", "-vf", f"scale={max_w}:-2", "-q:v", "3", str(out)]
    r = run(cmd)
    if r.returncode != 0 or not out.exists():
        return None
    return out


def extract_frame(src, out, at="first"):
    """Extract one frame from a video to an image. at = "first" | "last" | seconds.
    Returns the output Path on success, else None."""
    src = Path(src); out = Path(out)
    dur = probe(src).get("duration") or 0
    if at == "last":
        # -sseof seeks from the end; most reliable way to grab the final frame
        # (a forward -ss can land past the last decodable frame and write nothing)
        cmd = ["ffmpeg", "-y", "-v", "error", "-sseof", "-0.1", "-i", str(src),
               "-frames:v", "1", "-update", "1", str(out)]
    else:
        if at == "first":
            t = 0.0
        else:
            try:
                t = max(0.0, min(float(at), max(0.0, dur - 0.04)))
            except (TypeError, ValueError):
                t = 0.0
        cmd = ["ffmpeg", "-y", "-v", "error", "-ss", f"{t:.3f}", "-i", str(src),
               "-frames:v", "1", "-update", "1", str(out)]
    r = run(cmd)
    return out if (r.returncode == 0 and out.exists()) else None


def _is_image(path):
    return Path(path).suffix.lower() in (".png", ".jpg", ".jpeg", ".webp", ".bmp")


def _image_motion_vf(clip, canvas):
    """ffmpeg -vf chain that reproduces the preview's pan-and-zoom (Ken Burns)
    motion for a still image, using the standard `zoompan` filter. Returns None
    if the clip has no motion (caller uses the static cover path).

    The image is first scaled to a 2x canvas cover frame (zoom headroom), then
    zoompan animates zoom + center over the clip's frames. Expressions use only
    + - * / ( ) so they never contain commas/colons that would break the
    filtergraph. Mirrors app.js motionState exactly."""
    motion = clip.get("motion") or {}
    mtype = motion.get("type")
    if not mtype or mtype == "none":
        return None
    W, H = canvas["width"], canvas["height"]
    bz = max(1.0, float(clip.get("zoom", 1.0)))  # motion only zooms in
    dur = max(0.05, float(clip["out"]) - float(clip["in"]))
    frames = max(1, int(round(dur * FPS)))
    # zoompan's output-frame counter is `on` (0 .. d-1), not `n`
    prog = f"on/{frames}"
    if mtype == "push-in":
        zeff = f"({bz})*(1+0.25*{prog})"; cx = "0.5"; cy = "0.5"
    elif mtype == "pull-out":
        zeff = f"({bz})*(1.25-0.25*{prog})"; cx = "0.5"; cy = "0.5"
    elif mtype == "pan-left":
        zeff = f"({bz})*1.3"; cx = f"(0.6-0.2*{prog})"; cy = "0.5"
    elif mtype == "pan-right":
        zeff = f"({bz})*1.3"; cx = f"(0.4+0.2*{prog})"; cy = "0.5"
    elif mtype == "pan-up":
        zeff = f"({bz})*1.3"; cx = "0.5"; cy = f"(0.6-0.2*{prog})"
    elif mtype == "pan-down":
        zeff = f"({bz})*1.3"; cx = "0.5"; cy = f"(0.4+0.2*{prog})"
    else:
        return None
    # zoompan input is the 2x cover frame (2W x 2H); center the (1/zeff) crop
    # window on (cx, cy)
    x = f"({cx})*{2 * W}-({W})/({zeff})"
    y = f"({cy})*{2 * H}-({H})/({zeff})"
    return [f"scale={2 * W}:{2 * H}:force_original_aspect_ratio=increase",
            f"crop={2 * W}:{2 * H}",
            f"zoompan=z='{zeff}':x='{x}':y='{y}':d={frames}:s={W}x{H}:fps={FPS}"]


def _transform_keyframe_vf(clip, canvas, still=False):
    """Render Inspector transform points for both preview and final export."""
    keyframes = clip.get("keyframes") or {}
    if keyframes.get("enabled", True) is False:
        return None
    raw = keyframes.get("points") or []
    points = []
    for point in raw:
        try:
            points.append({"at": max(0.0, min(1.0, float(point.get("at", 0)))),
                           "zoom": max(.1, float(point.get("zoom", 1))),
                           "x": max(0.0, min(1.0, float(point.get("x", .5)))),
                           "y": max(0.0, min(1.0, float(point.get("y", .5))))})
        except (TypeError, ValueError):
            continue
    points.sort(key=lambda point: point["at"])
    if not points:
        return None
    duration = max(.05, float(clip["out"]) - float(clip["in"]))
    frames = max(1, int(round(duration * FPS)))
    base_zoom = max(.1, float(clip.get("zoom", 1) or 1))

    def expression(key, multiplier=1.0):
        def value(point):
            return point[key] * multiplier
        tail = f"{value(points[-1]):.6f}"
        for index in range(len(points) - 2, -1, -1):
            left, right = points[index], points[index + 1]
            start = left["at"] * max(1, frames - 1)
            end = right["at"] * max(1, frames - 1)
            if end <= start + 1e-6:
                segment = f"{value(right):.6f}"
            else:
                progress = f"max(0,min(1,(on-{start:.6f})/{end-start:.6f}))"
                smooth = f"({progress})*({progress})*(3-2*({progress}))"
                segment = f"({value(left):.6f}+({value(right)-value(left):.6f})*({smooth}))"
            tail = f"if(lte(on,{end:.6f}),{segment},{tail})"
        return tail

    W, H = canvas["width"], canvas["height"]
    zoom = expression("zoom", base_zoom)
    center_x, center_y = expression("x"), expression("y")
    x = f"({center_x})*{2*W}-{W}/({zoom})"
    y = f"({center_y})*{2*H}-{H}/({zoom})"
    return [f"scale={2*W}:{2*H}:force_original_aspect_ratio=increase", f"crop={2*W}:{2*H}",
            f"zoompan=z='{zoom}':x='{x}':y='{y}':d={frames if still else 1}:s={W}x{H}:fps={FPS}"]


def _color_vf(clip):
    """Native FFmpeg equivalent of the browser's non-destructive color panel."""
    c = clip.get("color") or {}
    if not c or c.get("enabled", True) is False:
        return []
    exposure = max(-2.0, min(2.0, float(c.get("exposure", 0))))
    contrast = max(.0, min(2.0, float(c.get("contrast", 1))))
    saturation = max(0.0, min(2.0, float(c.get("saturation", 1))))
    temperature = max(-1.0, min(1.0, float(c.get("temperature", 0))))
    tint = max(-1.0, min(1.0, float(c.get("tint", 0))))
    highlights = max(-1.0, min(1.0, float(c.get("highlights", 0))))
    shadows = max(-1.0, min(1.0, float(c.get("shadows", 0))))
    # eq provides deterministic exposure/contrast/saturation and tonal shaping.
    brightness = max(-1.0, min(1.0, (2 ** exposure - 1) * .35 + highlights * .08 + shadows * .05))
    gamma = max(.1, min(3.0, 1 + shadows * .22 - highlights * .12))
    filters = [f"eq=brightness={brightness:.4f}:contrast={contrast:.4f}:saturation={saturation:.4f}:gamma={gamma:.4f}"]
    if abs(temperature) > .001 or abs(tint) > .001:
        r = temperature * .12 + tint * .025
        g = tint * .10
        b = -temperature * .12 - tint * .025
        filters.append(f"colorbalance=rs={r:.4f}:gs={g:.4f}:bs={b:.4f}:rm={r:.4f}:gm={g:.4f}:bm={b:.4f}")
    return filters


def _blur_vf(clip):
    """Native blur matching the inspector's 0–40px Canvas blur control."""
    blur = clip.get("blur") or {}
    if not blur or blur.get("enabled", True) is False:
        return []
    amount = max(0.0, min(40.0, float(blur.get("amount", 0) or 0)))
    if amount < .05:
        return []
    radius = max(1, min(40, int(round(amount))))
    return [f"boxblur=luma_radius={radius}:luma_power=1:chroma_radius={max(1, radius//2)}:chroma_power=1"]


def _mask_condition(clip, canvas):
    """Return an FFmpeg geq expression for the inspector's normalized mask."""
    mask = clip.get("mask") or {}
    kind = str(mask.get("type") or "none")
    if mask.get("enabled", True) is False or kind == "none":
        return None
    W, H = canvas["width"], canvas["height"]
    cx = W * max(0.0, min(100.0, float(mask.get("x", 50)))) / 100.0
    cy = H * max(0.0, min(100.0, float(mask.get("y", 50)))) / 100.0
    mw = W * max(1.0, min(100.0, float(mask.get("width", 70)))) / 100.0
    mh = H * max(1.0, min(100.0, float(mask.get("height", 70)))) / 100.0
    if kind == "circle":
        rx = ry = max(1.0, min(mw, mh) / 2.0)
    else:
        rx, ry = max(1.0, mw / 2.0), max(1.0, mh / 2.0)
    if kind == "split":
        condition = f"lte(X,{cx:.6f})"
    elif kind == "cinematic":
        condition = f"gte(Y,{cy-ry:.6f})*lte(Y,{cy+ry:.6f})"
    elif kind == "diamond":
        condition = f"lte(abs((X-{cx:.6f})/{rx:.6f})+abs((Y-{cy:.6f})/{ry:.6f}),1)"
    elif kind == "heart":
        nx, ny = f"((X-{cx:.6f})/{rx:.6f})", f"(-1*(Y-{cy:.6f})/{ry:.6f})"
        condition = f"lte((({nx})^2+({ny})^2-1)^3-({nx})^2*({ny})^3,0)"
    elif kind == "star":
        nx, ny = f"((X-{cx:.6f})/{rx:.6f})", f"((Y-{cy:.6f})/{ry:.6f})"
        condition = f"lte(hypot({nx},{ny}),0.62+0.38*cos(5*atan2({ny},{nx})))"
    elif kind in ("ellipse", "circle"):
        condition = f"lte(((X-{cx:.6f})/{rx:.6f})^2+((Y-{cy:.6f})/{ry:.6f})^2,1)"
    else:
        condition = (f"gte(X,{cx-rx:.6f})*lte(X,{cx+rx:.6f})*"
                     f"gte(Y,{cy-ry:.6f})*lte(Y,{cy+ry:.6f})")
    return f"1-({condition})" if mask.get("invert", False) else condition


def _base_mask_vf(clip, canvas):
    condition = _mask_condition(clip, canvas)
    if not condition:
        return []
    return [f"geq=r='r(X,Y)*({condition})':g='g(X,Y)*({condition})':b='b(X,Y)*({condition})'"]


def _cover_vf(zoom, canvas):
    """Match canvas drawCover for a centered, static clip transform."""
    W, H = canvas["width"], canvas["height"]
    zoom = max(0.1, float(zoom or 1.0))
    filters = [f"scale={W}:{H}:force_original_aspect_ratio=increase", f"crop={W}:{H}"]
    if abs(zoom - 1.0) < 0.0001:
        return filters
    zw = max(2, int(round(W * zoom)) // 2 * 2)
    zh = max(2, int(round(H * zoom)) // 2 * 2)
    filters.append(f"scale={zw}:{zh}")
    if zoom > 1.0:
        filters.append(f"crop={W}:{H}:(iw-{W})/2:(ih-{H})/2")
    else:
        filters.append(f"pad={W}:{H}:(ow-iw)/2:(oh-ih)/2:black")
    return filters


def render_video_pre(clip, media, canvas, outdir, mode="cover"):
    """Render one video-track clip to a normalized intermediate mp4.

    mode "cover"   (base track): fill the canvas, zoom scales up + crops center.
    mode "contain" (overlays):   picture-in-picture — width = canvas*zoom, height
                                 proportional, centered by the overlay filter.
    Freeze (image) media is looped for the clip duration.
    """
    out = Path(outdir) / f"{clip['id']}.mp4"
    dur = max(0.04, float(clip["out"]) - float(clip["in"]))
    W, H = canvas["width"], canvas["height"]
    zoom = max(0.1, float(clip.get("zoom", 1.0)))
    src = media["src"]

    if mode == "contain":
        # PiP: width scales with zoom, height keeps aspect (even dimension)
        scale = f"scale={max(2, int(round(W*zoom)) // 2 * 2)}:-2"
        tail = [f"fps={FPS}", "setsar=1", "format=yuv420p"]
    else:
        # First make the plain cover frame, then apply the user's zoom to that
        # frame. Scaling and cropping at the same enlarged size cancels zoom;
        # this order matches the browser canvas compositor instead.
        scale = ",".join(_cover_vf(zoom, canvas))
        tail = [f"fps={FPS}", "setsar=1", "format=yuv420p"]

    if _is_image(src):
        mvf = (_transform_keyframe_vf(clip, canvas, still=True) or _image_motion_vf(clip, canvas)) if mode == "cover" else None
        if mvf:
            # motion: feed a single frame; zoompan emits the whole clip (d frames)
            vf = mvf + _color_vf(clip) + _blur_vf(clip) + _position_vf(clip, canvas) + _base_mask_vf(clip, canvas) + tail
            cmd = ["ffmpeg", "-y", "-v", "error", "-i", str(src),
                   "-vf", ",".join(vf),
                   "-c:v", "libx264", "-crf", "18", "-preset", "veryfast",
                   "-pix_fmt", "yuv420p", str(out)]
        else:
            vf = [scale] + _color_vf(clip) + _blur_vf(clip) + (_position_vf(clip, canvas) if mode == "cover" else []) + (_base_mask_vf(clip, canvas) if mode == "cover" else []) + tail
            cmd = ["ffmpeg", "-y", "-v", "error", "-loop", "1", "-i", str(src),
                   "-t", f"{dur:.3f}", "-vf", ",".join(vf),
                   "-c:v", "libx264", "-crf", "18", "-preset", "veryfast",
                   "-pix_fmt", "yuv420p", str(out)]
    else:
        keyframe_vf = _transform_keyframe_vf(clip, canvas) if mode == "cover" else None
        vf = [f"trim=start={clip['in']:.3f}:end={clip['out']:.3f}", "setpts=PTS-STARTPTS"] + (
              keyframe_vf if keyframe_vf else [scale]) + _color_vf(clip) + _blur_vf(clip) + (
              _position_vf(clip, canvas) if mode == "cover" else []) + (
              _base_mask_vf(clip, canvas) if mode == "cover" else []) + tail
        cmd = ["ffmpeg", "-y", "-v", "error", "-i", str(src),
               "-vf", ",".join(vf),
               "-c:v", "libx264", "-crf", "18", "-preset", "veryfast",
               "-pix_fmt", "yuv420p", "-an", str(out)]
    r = run(cmd)
    if r.returncode != 0 or not out.exists():
        raise RuntimeError(f"pre-render video failed: {r.stderr.strip()[-400:]}")
    return out


def render_audio_pre(clip, media, outdir):
    """Render one audio clip (or a video clip's audio) to a normalized m4a."""
    out = Path(outdir) / f"{clip['id']}.m4a"
    src = media["src"]
    duration = _clip_dur(clip)
    fades = clip.get("audioFade") or {}
    fade_in = min(duration, max(0.0, float(fades.get("in", 0.0))))
    fade_out = min(duration, max(0.0, float(fades.get("out", 0.0))))
    filters = [f"atrim=start={clip['in']:.3f}:end={clip['out']:.3f}", "asetpts=PTS-STARTPTS"]
    volume = max(0.0, min(2.0, float(clip.get("volume", 1.0))))
    if volume != 1.0:
        filters.append(f"volume={volume:.3f}")
    if fade_in > 0:
        filters.append(f"afade=t=in:st=0:d={fade_in:.3f}")
    if fade_out > 0:
        filters.append(f"afade=t=out:st={max(0.0, duration-fade_out):.3f}:d={fade_out:.3f}")
    cmd = ["ffmpeg", "-y", "-v", "error", "-i", str(src), "-vn",
           "-af", ",".join(filters), "-c:a", "aac", "-b:a", "192k", str(out)]
    r = run(cmd)
    if r.returncode != 0 or not out.exists():
        raise RuntimeError(f"pre-render audio failed: {r.stderr.strip()[-400:]}")
    return out


def _clip_dur(clip):
    return max(0.04, float(clip["out"]) - float(clip["in"]))


def _transition_for(clip, edge="start"):
    """Return the strongest enabled transition in the current UI schema."""
    transition = clip.get("transition") or {}
    items = transition.get("items")
    if isinstance(items, list):
        candidates = [x for x in items if x and x.get("enabled", True)
                      and x.get("type") != "cut" and x.get("edge", "start") == edge
                      and float(x.get("dur", 0) or 0) > 0]
        if candidates:
            return max(candidates, key=lambda x: float(x.get("dur", 0) or 0))
        return {"type": "cut", "dur": 0.0}
    if edge == "start":
        return transition
    return {"type": "cut", "dur": 0.0}


def _transition_duration(clip, edge):
    """Match the preview: strongest enabled transition controls each edge."""
    transition = _transition_for(clip, edge)
    if transition.get("type", "cut") == "cut" or transition.get("enabled", True) is False:
        return 0.0
    return min(_clip_dur(clip), max(0.0, float(transition.get("dur", 0.0) or 0.0)))


def _position_vf(clip, canvas):
    """Translate a full-canvas base clip exactly like Canvas 2D drawClipCover."""
    position = clip.get("position") or {}
    x = float(position.get("x", 0) or 0) / 100.0
    y = float(position.get("y", 0) or 0) / 100.0
    if abs(x) < 0.000001 and abs(y) < 0.000001:
        return []
    W, H = canvas["width"], canvas["height"]
    dx, dy = W * x, H * y
    return [f"pad={3*W}:{3*H}:{W+dx:.6f}:{H+dy:.6f}:black", f"crop={W}:{H}:{W}:{H}"]


def _base_video_track(tracks):
    """Match the browser compositor: lowest occupied, unmuted video lane."""
    visible = [t for t in tracks if t.get("kind") == "video" and not t.get("muted")]
    return next((t for t in reversed(visible) if t.get("clips")), None)


def export_project(project, root):
    """Compose the timeline to a uniquely named project export. Returns its URL."""
    root = Path(root)
    # Media src is stored relative to the project folder; resolve to absolute
    # (shallow copies so the project dict is never mutated).
    media = {}
    for m in project.get("media", []):
        mm = dict(m)
        src = Path(m["src"])
        mm["src"] = str(src if src.is_absolute() else (root / src))
        media[m["id"]] = mm
    tracks = project.get("tracks", [])
    canvas = project.get("canvas", {"width": 512, "height": 512})
    media_dir = root / "media"
    pre = media_dir / "pre"
    if pre.exists():
        shutil.rmtree(pre)
    pre.mkdir(parents=True, exist_ok=True)

    vtracks = [t for t in tracks if t["kind"] == "video" and not t.get("muted")]
    atracks = [t for t in tracks if t["kind"] == "audio" and not t.get("muted")]

    # Determine total timeline duration.
    total = 0.0
    for t in vtracks + atracks:
        for c in t["clips"]:
            total = max(total, c["start"] + _clip_dur(c))
    if total <= 0:
        raise RuntimeError("nothing to export — add clips to the timeline")

    # Timeline order is camera order: the first video lane is visually on top.
    # Playback therefore uses the lowest occupied lane as its base and paints
    # upward. Export must use the identical rule or a top overlay replaces the
    # actual scene sequence.
    base = _base_video_track(tracks)
    if base is None:
        raise RuntimeError("no video clips on the timeline")

    # Pre-render every clip.
    for t in vtracks:
        for c in t["clips"]:
            render_video_pre(c, media[c["mediaId"]], canvas, pre,
                             mode="cover" if t is base else "contain")
    for t in atracks:
        for c in t["clips"]:
            m = media[c["mediaId"]]
            if _is_image(m["src"]) or not m.get("hasAudio"):
                continue
            render_audio_pre(c, m, pre)

    inputs = []
    fc = []
    n_in = [0]
    def add_input(path):
        i = n_in[0]; n_in[0] += 1
        inputs.extend(["-i", str(path)])
        return i

    # base track chain: concat for cuts (robust, no offset math), xfade for
    # real transitions. Offsets are driven from the ACTUAL pre-rendered
    # durations (frame-quantized) and kept safely inside the running stream.
    base_clips = sorted(base["clips"], key=lambda c: c["start"])
    base_durs = [probe(pre / f"{c['id']}.mp4")["duration"] or _clip_dur(c)
                 for c in base_clips]
    prev = None
    combined = 0.0
    for i, c in enumerate(base_clips):
        idx = add_input(pre / f"{c['id']}.mp4")
        # normalize timebase + fps so concat/xfade see identical streams.
        # mp4 pre-renders can carry different container timebases (1/1000000
        # vs 1/12288); xfade rejects mismatched timebases, so force a common one.
        norm = f"[b{i}]"
        fc.append(f"[{idx}:v]fps={FPS},settb=AVTB,format=yuv420p{norm}")
        if prev is None:
            initial_gap = max(0.0, float(c["start"]))
            if initial_gap > 1.0 / FPS:
                gap = f"[bgap{i}]"
                out = f"[v{i}]"
                fc.append(f"color=c=black:s={canvas['width']}x{canvas['height']}:r={FPS}:d={initial_gap:.6f},format=yuv420p{gap}")
                fc.append(f"{gap}{norm}concat=n=2:v=1:a=0{out}")
                prev = out
                combined = initial_gap + base_durs[i]
            else:
                prev = norm
                combined = base_durs[i]
            continue
        tr = _transition_for(c, "start")
        ttype = tr.get("type", "cut")
        tname = TRANSITIONS.get(ttype, TRANSITIONS["cut"])[0]
        tdur = float(tr.get("dur", TRANSITIONS.get(ttype, TRANSITIONS["cut"])[1]))
        out = f"[v{i}]"
        if ttype == "cut" or tdur <= 0:
            gap_seconds = max(0.0, float(c["start"]) - combined)
            if gap_seconds > 1.0 / FPS:
                gap = f"[bgap{i}]"
                filled = f"[vfill{i}]"
                fc.append(f"color=c=black:s={canvas['width']}x{canvas['height']}:r={FPS}:d={gap_seconds:.6f},format=yuv420p{gap}")
                fc.append(f"{prev}{gap}concat=n=2:v=1:a=0{filled}")
                fc.append(f"{filled}{norm}concat=n=2:v=1:a=0{out}")
                combined += gap_seconds + base_durs[i]
            else:
                fc.append(f"{prev}{norm}concat=n=2:v=1:a=0{out}")
                combined += base_durs[i]
        else:
            # keep the transition fully inside the running stream
            tdur = min(tdur, max(0.05, combined - 0.05))
            offset = combined - tdur
            fc.append(f"{prev}{norm}xfade=transition={tname}:duration={tdur:.3f}:offset={offset:.3f}{out}")
            combined = offset + base_durs[i]
        prev = out
    base_label = prev

    # The lowest video lane can end before a later clip on an upper lane. Pad
    # the base to the true last active timeline endpoint so overlays are not
    # truncated when FFmpeg's main overlay input reaches EOF.
    if total > combined + 1.0 / FPS:
        padded = "[baseTimeline]"
        fc.append(f"{base_label}tpad=stop_mode=add:stop_duration={total-combined:.6f},trim=duration={total:.6f}{padded}")
        base_label = padded

    # overlay upper video tracks
    cur = base_label
    for t in vtracks:
        if t is base:
            continue
        for c in sorted(t["clips"], key=lambda c: c["start"]):
            idx = add_input(pre / f"{c['id']}.mp4")
            a = c["start"]
            clip_dur = _clip_dur(c)
            b = c["start"] + clip_dur
            out = f"[o{c['id']}]"
            overlay_in = f"[ov{c['id']}]"
            overlay_filters = ["format=rgba"]
            mask_condition = _mask_condition(c, canvas)
            if mask_condition:
                overlay_filters.append(
                    "geq=r='r(X,Y)':g='g(X,Y)':b='b(X,Y)':a='alpha(X,Y)*(" + mask_condition + ")'")
            overlay_filters.append("format=yuva420p")
            fade_in = _transition_duration(c, "start")
            fade_out = _transition_duration(c, "end")
            if fade_in > 0:
                overlay_filters.append(f"fade=t=in:st=0:d={fade_in:.6f}:alpha=1")
            if fade_out > 0:
                overlay_filters.append(f"fade=t=out:st={max(0.0, clip_dur-fade_out):.6f}:d={fade_out:.6f}:alpha=1")
            overlay_filters.append(f"setpts=PTS-STARTPTS+{a:.6f}/TB")
            fc.append(f"[{idx}:v]{','.join(overlay_filters)}{overlay_in}")
            pos = c.get("position") or {}
            x = float(pos.get("x", 0) or 0) / 100.0
            y = float(pos.get("y", 0) or 0) / 100.0
            fc.append(f"{cur}{overlay_in}overlay=x=(W-w)/2+W*{x:.6f}:y=(H-h)/2+H*{y:.6f}:"
                      f"enable='between(t,{a:.3f},{b:.3f})':shortest=0{out}")
            cur = out
    video_out = cur

    # audio
    # Base-track audio is chained to mirror the video chain exactly (concat for
    # cuts, acrossfade for transitions). Placing it at original timeline offsets
    # with adelay+amix drifts out of sync once xfade compresses the video.
    base_audio_ids = set()
    if not base.get("muted"):
        for c in sorted(base_clips, key=lambda c: c["start"]):
            m = media[c["mediaId"]]
            if c.get("detached") or c.get("muted"):
                continue
            if _is_image(m["src"]) or not m.get("hasAudio"):
                continue
            if not (pre / f"{c['id']}.m4a").exists():
                render_audio_pre(c, m, pre)
            base_audio_ids.add(c["id"])

    base_audio_label = None
    if base_audio_ids:
        prev = None
        for c in sorted(base_clips, key=lambda c: c["start"]):
            if c["id"] not in base_audio_ids:
                continue
            idx = add_input(pre / f"{c['id']}.m4a")
            tr = _transition_for(c, "start")
            ttype = tr.get("type", "cut")
            tdur = float(tr.get("dur", TRANSITIONS.get(ttype, TRANSITIONS["cut"])[1]))
            out = f"[ba{c['id']}]"
            if prev is None:
                prev = f"[{idx}:a]"
                continue
            if ttype == "cut" or tdur <= 0:
                fc.append(f"{prev}[{idx}:a]concat=n=2:v=0:a=1{out}")
            else:
                tdur = min(tdur, 1.0)
                fc.append(f"{prev}[{idx}:a]acrossfade=d={tdur:.3f}:c1=tri:c2=tri{out}")
            prev = out
        base_audio_label = prev

    # Independent audio-track clips (A1, A2, ...) placed at their timeline offset.
    audio_items = []
    # Upper video lanes carry audio in the browser preview too. Keep those
    # sources at their absolute positions instead of silently dropping them.
    for t in vtracks:
        if t is base:
            continue
        for c in sorted(t["clips"], key=lambda c: c["start"]):
            m = media[c["mediaId"]]
            if c.get("detached") or c.get("muted") or _is_image(m["src"]) or not m.get("hasAudio"):
                continue
            if not (pre / f"{c['id']}.m4a").exists():
                render_audio_pre(c, m, pre)
            audio_items.append((c["start"], pre / f"{c['id']}.m4a", False))
    for t in atracks:
        if t.get("muted"):
            continue
        for c in sorted(t["clips"], key=lambda c: c["start"]):
            m = media[c["mediaId"]]
            if _is_image(m["src"]) or not m.get("hasAudio"):
                continue
            if not (pre / f"{c['id']}.m4a").exists():
                render_audio_pre(c, m, pre)
            audio_items.append((c["start"], pre / f"{c['id']}.m4a", bool(c.get("muted"))))

    mix_labels = []
    if base_audio_label:
        mix_labels.append(base_audio_label)
    ainputs = []
    for i, (start, f, muted) in enumerate(audio_items):
        if muted:
            continue
        idx = add_input(f)
        delay = max(0, int(start * 1000))
        ainputs.append(f"[{idx}:a]adelay={delay}|{delay},apad[a{i}]")
        mix_labels.append(f"[a{i}]")
    fc.extend(ainputs)

    have_audio = bool(mix_labels)
    if have_audio:
        if len(mix_labels) == 1:
            fc.append(f"{mix_labels[0]}anull,apad[aout]")
        else:
            fc.append(f"{''.join(mix_labels)}amix=inputs={len(mix_labels)}:normalize=0,alimiter,apad[aout]")
        audio_map = "[aout]"
    else:
        audio_map = None

    # Target output duration = the last active timeline element, rounded UP to
    # a whole frame so a clip on any visible lane can never be chopped.
    video_dur = math.ceil(max(0.04, total) * FPS) / FPS

    # ---- Mux ----
    project_label = re.sub(r"[^A-Za-z0-9]+", "-", str(project.get("name") or "project")).strip("-")[:48] or "project"
    stamp = time.strftime("%Y%m%d-%H%M%S")
    stem = f"OpenMagia-{project_label}-{stamp}"
    out_mp4 = media_dir / f"{stem}.mp4"
    suffix = 2
    while out_mp4.exists():
        out_mp4 = media_dir / f"{stem}-{suffix}.mp4"
        suffix += 1
    # The base video may be a raw input stream (single clip, no transition or
    # overlay) rather than a filter output. Map it as an input in that case.
    video_map = video_out[1:-1] if re.match(r"^\[\d+:v\]$", video_out) else video_out
    cmd = ["ffmpeg", "-y", "-v", "error"] + inputs
    if fc:
        cmd += ["-filter_complex", ";".join(fc)]
    cmd += ["-map", video_map]
    if audio_map:
        cmd += ["-map", audio_map, "-c:a", "aac", "-b:a", "192k"]
    cmd += ["-t", f"{video_dur:.3f}", "-c:v", "libx264", "-crf", "18", "-preset", "medium",
            "-pix_fmt", "yuv420p", "-movflags", "+faststart", str(out_mp4)]
    r = run(cmd)
    if r.returncode != 0 or not out_mp4.exists():
        raise RuntimeError(f"compose failed: {r.stderr.strip()[-700:]}")
    return "/media/" + out_mp4.name


def system_ram():
    """Read macOS RAM usage from vm_stat (no external deps)."""
    try:
        page = int(subprocess.check_output(["sysctl", "-n", "hw.pagesize"]).strip())
        total = int(subprocess.check_output(["sysctl", "-n", "hw.memsize"]).strip())
        out = subprocess.check_output(["vm_stat"], text=True)
        def pages(key):
            m = re.search(key + r":\s+(\d+)", out)
            return int(m.group(1)) if m else 0
        free = pages("Pages free")
        inactive = pages("Pages inactive")
        wired = pages("Pages wired down")
        # used = total - free - inactive (inactive is reclaimable)
        used = max(0, total - (free + inactive) * page)
        return {
            "total": total,
            "used": used,
            "free": total - used,
            "percent": round(100 * used / total, 1) if total else 0,
        }
    except Exception as e:
        return {"error": str(e)}
