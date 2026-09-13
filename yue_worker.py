#!/usr/bin/env python3
"""YuE 2 music worker — a tiny stdlib HTTP front end for one CUDA machine.

OpenMagia generates music with YuE2 (https://github.com/multimodal-art-projection/YuE).
YuE 2 needs a BF16-capable NVIDIA GPU, while OpenMagia's video engine runs on
Apple Silicon, so this file is the documented way to keep one OpenMagia
install in charge of a separate CUDA box. Copy this single file to that machine
and run it inside the environment where YuE 2 is installed:

    python yue_worker.py --host 0.0.0.0 --port 8931 --token SOMETHING_LONG

OpenMagia then points Settings → Music → Connected worker at
``http://that-machine:8931`` with the same token. Nothing leaves the network
except the style tags and lyrics of the song being made.

Protocol (JSON, one song at a time because the GPU has no spare capacity):

    GET    /health                 -> {ok, ready, busy, device, model, vae, version}
    POST   /v1/music               -> {id, status}          (409 when busy)
    GET    /v1/music/<id>          -> {status, progress, result, error}
    GET    /v1/music/<id>/audio    -> the finished audio (audio.flac)
    DELETE /v1/music/<id>          -> cancel the running job

Only Python's standard library is used, so the CUDA machine needs no extra
packages beyond YuE 2 itself.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shlex
import shutil
import signal
import subprocess
import sys
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

DEFAULT_MODEL = os.environ.get("YUE2_MODEL", "m-a-p/YuE2-3B")
DEFAULT_VAE = os.environ.get("YUE2_VAE", "m-a-p/YuE2-Vae")
VERSION = "openmagia-yue-worker/2"
TERMINAL = ("ready", "error", "cancelled")
MAX_BODY = 4 * 1024 * 1024


def classify_audio_quality(window_rms, peak, rms, clipped_fraction):
    """Reject obvious decoder collapse while leaving ordinary dynamics alone."""
    values = [float(value) for value in window_rms if value is not None]
    if not values:
        return {"accepted": True, "reason": "quality analysis unavailable"}
    early = sorted(values[:max(1, len(values) // 4)])
    baseline = early[len(early) // 2]
    loudest = max(values)
    ratio = loudest / max(baseline, 1e-6)
    metrics = {"peak": round(float(peak), 6), "rms": round(float(rms), 6),
               "clipped_fraction": round(float(clipped_fraction), 6),
               "loudest_window_rms": round(loudest, 6), "loudness_jump": round(ratio, 2)}
    if float(rms) < 0.002:
        return {**metrics, "accepted": False,
                "reason": "YuE produced an almost silent candidate."}
    # A quiet tail is not truncation. YuE may append decoder decay after the
    # last scored bar; result.json's truncation flags and the ABC inspector are
    # the authoritative completion checks described by the generation manual.
    if float(clipped_fraction) >= 0.001 and loudest >= 0.45 and ratio >= 4.0:
        return {**metrics, "accepted": False,
                "reason": ("YuE's decoded candidate became unstable and clipped after starting normally. "
                           "Generate another variation with a different seed.")}
    return {**metrics, "accepted": True, "reason": ""}


def analyze_audio_quality(path):
    """Measure the decoded file using YuE's own NumPy/soundfile environment."""
    try:
        import numpy as np
        import soundfile as sf
        audio, sample_rate = sf.read(str(path), always_2d=True)
        if not len(audio) or not np.isfinite(audio).all():
            return {"accepted": False, "reason": "YuE produced invalid audio samples."}
        size = max(1, int(sample_rate) * 2)
        windows = [float(np.sqrt(np.mean(audio[index:index + size] ** 2)))
                   for index in range(0, len(audio), size)]
        return classify_audio_quality(windows, np.max(np.abs(audio)),
                                      np.sqrt(np.mean(audio ** 2)),
                                      np.mean(np.abs(audio) >= 0.999))
    except Exception as exc:
        return {"accepted": True, "reason": "quality analysis unavailable", "detail": str(exc)[:160]}


def inspect_abc_score(path, yue_root):
    """Run YuE's shipped native-dialect validator on a planned score."""
    tool = Path(yue_root) / "skills" / "yue2-music" / "scripts" / "abc_tools.py"
    if not tool.is_file():
        return {"accepted": False,
                "reason": "YuE's ABC inspection tool is missing from this runtime."}
    try:
        checked = subprocess.run(
            [sys.executable, str(tool), "inspect", str(path)],
            cwd=str(yue_root), capture_output=True, text=True, timeout=30,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return {"accepted": False, "reason": f"Could not inspect YuE's planned score: {exc}"}
    if checked.returncode:
        detail = (checked.stderr or checked.stdout or "invalid ABC score").strip()
        detail = re.sub(r"^ABC check failed:\s*", "", detail)[:320]
        return {"accepted": False,
                "reason": ("YuE produced an invalid score, so this candidate was discarded before "
                           f"it could be published with a damaged ending: {detail}")}
    return {"accepted": True, "reason": ""}

try:
    from yue_prompts import parse_yue_progress   # one progress parser, one truth
except ImportError as exc:                      # noqa: BLE001
    raise SystemExit("yue_worker.py needs yue_prompts.py next to it; copy both files to the music machine.") from exc


class Song:
    def __init__(self, request: dict, workdir: Path):
        self.song_id = str(request.get("id") or "song")
        self.id = self.song_id + "-" + uuid.uuid4().hex[:6]
        self.request = request
        self.workdir = workdir
        self.status = "queued"
        self.progress = {"phase": "Waiting", "completed": 0, "total": 1}
        self.error = ""
        self.result: dict = {}
        self.proc = None
        self.created = time.time()
        self.finished = 0.0

    def public(self, include_audio=False):
        out = {"id": self.id, "status": self.status, "progress": self.progress,
               "created": self.created, "result": self.result}
        if self.error:
            out["error"] = self.error
        if include_audio:
            out["has_audio"] = bool(self.audio_path and self.audio_path.exists())
        return out

    @property
    def outdir(self):
        """The CLI writes into ``<output>/<request id>/``, not into ``<output>``."""
        nested = self.workdir / self.song_id
        return nested if nested.is_dir() else self.workdir

    @property
    def audio_path(self):
        for base in (self.outdir, self.workdir):
            for name in ("audio.flac", "audio.wav", "audio.mp3"):
                found = base / name
                if found.exists():
                    return found
        return None


class Worker:
    """Runs at most one YuE 2 process at a time and keeps job state in memory."""

    def __init__(self, args):
        self.args = args
        self.jobs: dict[str, Song] = {}
        self.active: Song | None = None
        self.ready = False
        self.lock = threading.Lock()
        self.condition = threading.Condition(self.lock)
        self.log = []
        self.device_label = str(args.device)
        self.versions = {}

    def base_command(self):
        """How to launch the runtime: an explicit command, or the installed ``yue2`` CLI."""
        if self.args.command:
            return shlex.split(self.args.command)
        if self.args.yue_cli:
            return [str(self.args.yue_cli)]
        beside = Path(sys.executable).parent / "yue2"   # installed into this environment
        found = shutil.which("yue2")
        if beside.exists():
            return [str(beside)]
        return [found] if found else [sys.executable, "-m", "yue2.cli"]

    def command(self, song: Song):
        # The request keys YuE 2 actually accepts; nothing else may be invented.
        request = {k: v for k, v in song.request.items()
                   if k in ("id", "style", "tags", "lyrics", "cot", "abc", "seed", "cfg_scale")}
        (song.workdir / "request.json").write_text(json.dumps(request, indent=2) + "\n")
        return self.base_command() + ["generate", "--request", str(song.workdir / "request.json"),
                                      "--output", str(song.workdir), "--device", str(self.args.device),
                                      "--backend", str(self.args.backend), "--budget", str(self.args.budget),
                                      "--model", str(self.args.model), "--vae", str(self.args.vae)] \
               + list(self.args.extra_arg or [])

    def note(self, text):
        stamp = time.strftime("%H:%M:%S")
        self.log.append(f"{stamp} {text}")
        del self.log[:-80]
        print(f"[worker] {text}", flush=True)

    def enqueue(self, song: Song):
        with self.condition:
            if self.active is not None:
                return False
            self.jobs[song.id] = song
            self.active = song
            self.condition.notify_all()
            return True

    def prune(self):
        """Drop finished songs and their folders after ``--keep`` hours.

        Generated audio belongs to OpenMagia's project once it has been
        fetched, so the worker has no reason to accumulate it for months.
        """
        horizon = time.time() - max(1, self.args.keep) * 3600
        for song_id, song in list(self.jobs.items()):
            if song is self.active or song.status not in TERMINAL or song.created > horizon:
                continue
            self.jobs.pop(song_id, None)
            shutil.rmtree(song.workdir, ignore_errors=True)

    def run_forever(self):
        while True:
            with self.condition:
                while self.active is None:
                    self.condition.wait(2.0)
                song = self.active
            self.prune()
            self.execute(song)
            with self.condition:
                self.active = None
                self.condition.notify_all()

    def execute(self, song: Song):
        song.status = "running"
        song.progress = {"phase": "Starting YuE 2", "completed": 0, "total": 1}
        self.note(f"starting {song.id} ({str(song.request.get('cot') or 'full')})")
        tail = []
        try:
            command = self.command(song)
            song.progress["command"] = " ".join(command[:3]) + " …"
            proc = subprocess.Popen(command, cwd=str(self.args.cwd), stdout=subprocess.PIPE,
                                    stderr=subprocess.STDOUT, text=True, errors="replace",
                                    bufsize=1, start_new_session=(os.name != "nt"),
                                    env=dict(os.environ, HF_HOME=str(self.args.hf_home),
                                             YUE_MODEL=str(self.args.model), YUE_VAE=str(self.args.vae)))
            song.proc = proc
            for raw in proc.stdout or []:
                line = re.sub(r"\x1b\[[0-9;]*m", "", raw).strip()
                if not line:
                    continue
                tail.append(line)
                del tail[:-40]
                parsed = parse_yue_progress(line)
                if not parsed:
                    continue
                if parsed.get("audio_seconds") is not None:
                    song.result["seconds"] = parsed["audio_seconds"]
                phase = parsed.get("phase")
                if phase:
                    # ``total`` stays null while the runtime streams counts with no
                    # declared total; a fake denominator would be a fake percentage.
                    song.progress = {"phase": phase, "completed": parsed.get("completed", 0),
                                     "total": parsed.get("total")}
                    limit = int(self.args.max_tokens or 0)
                    counted = int(parsed.get("completed") or 0)
                    if limit and counted > limit and song.status != "cancelled":
                        # YuE 2 has no duration argument. A token ceiling is the only
                        # way to stop a cue that never finds its ending (observed: an
                        # instrumental running past 7,000 tokens until the OS killed
                        # it), and it fails loudly instead of silently.
                        self.note(f"{song.id}: hit the {limit:,} token ceiling at {phase}")
                        song.error = (f"Stopped after {counted:,} tokens (ceiling {limit:,}, "
                                      f"about {round(limit / 23.0)} s of audio). YuE 2 has no "
                                      "duration control: shorten the lyric, supply a score, or "
                                      "raise the worker ceiling.")
                        self.cancel(song)
                    if parsed.get("tokens_per_second"):
                        song.progress["tokens_per_second"] = parsed["tokens_per_second"]
            code = proc.wait()
            if song.status == "cancelled":
                if song.error:
                    # The worker stopped this run on purpose (token ceiling): the reason
                    # must survive, or the user sees a bare "cancelled" with no cause.
                    song.status = "error"
                self.note(f"cancelled {song.id}")
                return
            if code != 0:
                failure = song.outdir / "failure.json"
                detail = ""
                if failure.exists():
                    try:
                        detail = str(json.loads(failure.read_text()).get("message") or "")
                    except (OSError, ValueError):
                        detail = ""
                song.status = "error"
                if detail:
                    song.error = detail
                elif code < 0:
                    song.error = (f"YuE 2 was terminated by signal {-code} before the song completed. "
                                  "Retry with a different seed or a more explicit section structure.")
                else:
                    song.error = (f"YuE 2 exited with code {code} before the song completed. "
                                  "The worker log retains the technical output for diagnosis.")
                self.note(f"failed {song.id}: {song.error[-160:]}")
                return
            audio = song.audio_path
            if not audio:
                song.status = "error"
                song.error = "YuE 2 finished without writing audio.flac"
                self.note(f"failed {song.id}: no audio output")
                return
            score = song.outdir / "score.abc"
            if score.exists():
                song.result["abc"] = score.read_text(encoding="utf-8", errors="replace")[:20000]
            # result.json is the runtime's own receipt: length, truncation, weights, timing.
            receipt = song.outdir / "result.json"
            if receipt.exists():
                try:
                    data = json.loads(receipt.read_text())
                except (OSError, ValueError):
                    data = {}
                truncated = data.get("truncated")
                song.result["truncated"] = bool(truncated.get("abc") or truncated.get("semantic")) \
                    if isinstance(truncated, dict) else bool(truncated)
                song.result["seconds"] = data.get("audio_seconds") or song.result.get("seconds")
                song.result["sample_rate"] = data.get("sample_rate")
                song.result["identity"] = data.get("identity")
                song.result["plan"] = data
            if song.result.get("truncated"):
                song.status = "error"
                song.error = ("YuE stopped before completing this candidate, so the partial ending was discarded. "
                              "Retry to generate a complete variation.")
                self.note(f"rejected {song.id}: truncated output")
                return
            # YuE's editing contract requires every generated score to pass its
            # native-dialect inspector before reuse. This also catches malformed
            # plans that can decode into a plausible opening and a broken ending.
            if score.exists() and str(song.request.get("cot") or "full") != "off":
                score_check = inspect_abc_score(score, self.args.cwd)
                song.result["score_check"] = score_check
                if not score_check.get("accepted"):
                    song.status = "error"
                    song.error = score_check["reason"]
                    self.note(f"rejected {song.id}: {song.error}")
                    return
            quality = analyze_audio_quality(audio)
            song.result["quality"] = quality
            if not quality.get("accepted", True):
                song.status = "error"
                song.error = str(quality.get("reason") or "YuE produced an unusable audio candidate.")
                self.note(f"rejected {song.id}: {song.error}")
                return
            song.result.setdefault("seconds", None)
            song.result.setdefault("truncated", False)
            song.status = "ready"
            song.finished = time.time()
            self.note(f"ready {song.id} in {round(song.finished - song.created)}s")
        except FileNotFoundError as exc:
            song.status = "error"
            song.error = (f"Could not start YuE 2 ({exc}). Run this worker inside the YuE 2 environment, "
                          "or pass --yue-cli.")
            self.note(song.error)
        except Exception as exc:      # a wedged job must never take the worker down
            song.status = "error"
            song.error = f"Worker error: {type(exc).__name__}: {exc}"
            self.note(song.error)
        finally:
            song.proc = None

    def cancel(self, song: Song):
        proc = song.proc
        song.status = "cancelled"
        if proc and proc.poll() is None:
            try:
                if os.name != "nt":
                    os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
                else:
                    proc.terminate()
                proc.wait(timeout=15)
            except Exception:
                try:
                    proc.kill()
                except Exception:
                    pass
        return True

    def warmup(self):
        """Ask the runtime which device it will actually use, once, on startup.

        The label is the truth the runtime reports, never an assumed "cuda":
        YuE 2 falls back cuda -> mps -> cpu, and OpenMagia must not advertise a
        device this machine does not have.
        """
        try:
            command = self.base_command() + ["doctor", "--device", str(self.args.device)]
            run = subprocess.run(command, cwd=str(self.args.cwd), capture_output=True, text=True, timeout=300)
            text = run.stdout if isinstance(run.stdout, str) else bytes(run.stdout or b"").decode("utf-8", "replace")
            report = json.loads(text[text.find("{"):] if "{" in text else "{}")
            self.ready = bool(report.get("dependencies_ready")) and run.returncode == 0
            self.versions = dict(report.get("versions") or {})
            self.device_label = self.resolve_device_label(report)
            self.note("runtime ready on " + self.device_label if self.ready
                      else "runtime check failed: " + ((run.stderr or run.stdout)[-200:] or "dependencies incomplete"))
        except Exception as exc:
            self.ready = False
            self.note("runtime check unavailable: " + str(exc))

    def resolve_device_label(self, report):
        explicit = str(self.args.device or "auto")
        cuda = report.get("cuda") or []
        if explicit not in ("", "auto"):
            if explicit.startswith("cuda") and cuda:
                first = cuda[0] if isinstance(cuda[0], dict) else {}
                return f"cuda {first.get('id', '')} \u00b7 {first.get('name', '')}".strip()
            return explicit
        if cuda:
            first = cuda[0] if isinstance(cuda[0], dict) else {}
            name = str(first.get("name") or "NVIDIA GPU")
            gib = first.get("memory_gib")
            return "cuda \u00b7 " + name + (f" \u00b7 {round(float(gib))} GiB" if gib else "")
        if report.get("mps_available"):
            return "mps (Apple Silicon)"
        return "cpu"


def parse_args():
    parser = argparse.ArgumentParser(description="Serve YuE 2 to OpenMagia over the local network.")
    parser.add_argument("--host", default="127.0.0.1", help="127.0.0.1 keeps this machine local to itself")
    parser.add_argument("--port", type=int, default=8931)
    parser.add_argument("--token", default=os.environ.get("YUE_WORKER_TOKEN", ""),
                        help="Required from clients when set; required when listening off-loopback")
    parser.add_argument("--device", default=os.environ.get("YUE_DEVICE", "auto"),
                        help="auto, cuda, mps, cpu or a cuda index such as cuda:1")
    parser.add_argument("--backend", default=os.environ.get("YUE_BACKEND", "torch"),
                        choices=("torch", "torch-eager", "vllm"))
    parser.add_argument("--budget", type=float, default=float(os.environ.get("YUE_BUDGET", "24")),
                        help="VRAM/GiB budget handed to the runtime")
    parser.add_argument("--workdir", default=str(Path.home() / ".cache" / "openmagia-yue"),
                        help="Where request folders and generated audio are written")
    parser.add_argument("--cwd", default=os.getcwd(), help="Working directory for the YuE 2 CLI")
    parser.add_argument("--hf-home", default=os.environ.get("HF_HOME", str(Path.home() / ".cache" / "huggingface")))
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--vae", default=DEFAULT_VAE)
    parser.add_argument("--yue-cli", default="", help="Path to the yue2 executable, when it is not on PATH")
    parser.add_argument("--command", default=os.environ.get("YUE_WORKER_COMMAND", ""),
                        help="Full override, e.g. 'python -m yue' for a forked runtime")
    parser.add_argument("--max-tokens", type=int, default=0,
                        help="Stop a song whose stage streams more tokens than this (0 = no "
                             "ceiling). YuE 2 has no duration argument, so this is the only guard "
                             "against a cue that never ends. Disabled by default so YuE's native "
                             "token budget and truncation receipt remain authoritative.")
    parser.add_argument("--extra-arg", action="append", default=[],
                        help="Extra argument for 'yue2 generate' (repeatable); never accepted from clients")
    parser.add_argument("--keep", type=int, default=72, help="Hours to keep finished songs")
    parser.add_argument("--no-warmup", action="store_true")
    return parser.parse_args()


class Handler(BaseHTTPRequestHandler):
    worker: Worker = None       # type: ignore[assignment]
    token = ""

    def log_message(self, *args):    # the worker prints its own single-line events
        pass

    def _send(self, code, payload, content_type="application/json"):
        body = payload if isinstance(payload, bytes) else json.dumps(payload).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _authorized(self):
        if not self.token:
            return True
        supplied = str(self.headers.get("Authorization") or "").strip()
        return supplied in (f"Bearer {self.token}", self.token)

    def _job(self, path, prefix):
        song_id = path[len(prefix):].strip("/")
        if song_id.endswith("/audio"):
            song_id = song_id[:-len("/audio")].strip("/")
        song = self.worker.jobs.get(song_id)
        if song is None and song_id in ("", "active"):
            song = self.worker.active
        return song

    def do_GET(self):
        if not self._authorized():
            return self._send(401, {"error": "Missing or incorrect access token."})
        path = self.path.split("?")[0]
        if path == "/health":
            song = self.worker.active
            return self._send(200, {"ok": True, "version": VERSION, "ready": self.worker.ready,
                                    "busy": song is not None, "device": self.worker.device_label,
                                    "versions": self.worker.versions,
                                    "model": self.worker.args.model, "vae": self.worker.args.vae,
                                    "job": song.id if song else None,
                                    "progress": song.progress if song else None})
        if path.startswith("/v1/music/"):
            song = self._job(path, "/v1/music")
            if not song:
                return self._send(404, {"error": "No such song."})
            if path.endswith("/audio"):
                audio = song.audio_path
                if not audio:
                    return self._send(409, {"error": "Audio is not ready."})
                suffix = audio.suffix.lower()
                kind = {".flac": "audio/flac", ".wav": "audio/wav", ".mp3": "audio/mpeg"}.get(suffix, "application/octet-stream")
                return self._send(200, audio.read_bytes(), kind)
            return self._send(200, song.public(include_audio=True))
        if path == "/":
            return self._send(200, {"ok": True, "worker": VERSION, "health": "/health",
                                    "create": "POST /v1/music"})
        return self._send(404, {"error": "Not found."})

    def do_POST(self):
        if not self._authorized():
            return self._send(401, {"error": "Missing or incorrect access token."})
        path = self.path.split("?")[0]
        if path == "/shutdown":
            if self.client_address[0] not in ("127.0.0.1", "::1"):
                return self._send(403, {"error": "Shutdown is available on loopback only."})
            self._send(200, {"ok": True})
            threading.Thread(target=self.server.shutdown, daemon=True).start()
            return
        if path != "/v1/music":
            return self._send(404, {"error": "Not found."})
        try:
            length = min(MAX_BODY, int(self.headers.get("Content-Length") or 0))
            body = json.loads(self.rfile.read(length).decode("utf-8") or "{}") if length else {}
        except (ValueError, OSError):
            return self._send(400, {"error": "Body must be JSON."})
        if not isinstance(body, dict):
            return self._send(400, {"error": "Body must be a JSON object."})
        style = str(body.get("style") or body.get("tags") or "").strip()
        lyrics = str(body.get("lyrics") or "")
        if not style or not lyrics.strip():
            return self._send(400, {"error": "A music request needs style (or tags) and lyrics."})
        request = {k: body[k] for k in ("id", "style", "tags", "lyrics", "cot", "abc", "seed", "cfg_scale") if k in body}
        song = Song(request, self.worker.args.workdir / time.strftime("%Y%m%d") / (str(body.get("id") or "song")[:48] + "-" + uuid.uuid4().hex[:6]))
        song.workdir.mkdir(parents=True, exist_ok=True)
        if not self.worker.enqueue(song):
            busy = self.worker.active
            return self._send(409, {"error": "The GPU is generating another song. Try again when it is free.",
                                    "busy": True, "job": busy.id if busy else ""})
        return self._send(202, {"id": song.id, "status": song.status})

    def do_DELETE(self):
        if not self._authorized():
            return self._send(401, {"error": "Missing or incorrect access token."})
        path = self.path.split("?")[0]
        if not path.startswith("/v1/music/"):
            return self._send(404, {"error": "Not found."})
        song = self._job(path, "/v1/music")
        if not song:
            return self._send(404, {"error": "No such song."})
        if song.status in TERMINAL and song.status != "cancelled":
            return self._send(409, {"error": "That song already finished."})
        self.worker.cancel(song)
        return self._send(200, song.public())


def main():
    args = parse_args()
    args.workdir = Path(args.workdir).expanduser().resolve()
    args.workdir.mkdir(parents=True, exist_ok=True)
    worker = Worker(args)
    Handler.worker = worker
    Handler.token = str(args.token or "")
    if args.host not in ("127.0.0.1", "localhost", "::1") and not Handler.token:
        print("Refusing to listen on " + args.host + " without --token: any device on that network "
              "could submit generations. Pass --token or keep --host 127.0.0.1.", file=sys.stderr)
        return 2
    threading.Thread(target=worker.run_forever, daemon=True).start()
    if not args.no_warmup:
        threading.Thread(target=worker.warmup, daemon=True).start()
    server = ThreadingHTTPServer((args.host, args.port), Handler)
    server.daemon_threads = True
    print(f"YuE 2 worker on http://{args.host}:{args.port} · model {args.model} · vae {args.vae} · "
          f"songs in {args.workdir}" + (" · token required" if Handler.token else " · no token"), flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        active = worker.active
        if active and active.proc:
            worker.cancel(active)
        server.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
