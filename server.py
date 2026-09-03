#!/usr/bin/env python3
"""OpenMagia — local server: generation + a full browser video editor (NLE).

Serves the UI, manages a media library and a multi-track timeline, runs h3
generation (which feeds clips into the base track), and exports the timeline
to a single MP4 via the NLE compositor (nle.py).
"""
import json
import hashlib
import os
import platform
import queue as stream_queue
import re
import signal
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import uuid
import urllib.parse
import urllib.request
from types import SimpleNamespace
from openmagia_plugins import PluginError, PluginRegistry, send_notification
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

# launchd intentionally supplies only Apple's system PATH. OpenMagia also
# invokes Homebrew ffmpeg/ffprobe during generation preflight and postprocess,
# so make those tools discoverable regardless of how the server was launched.
if sys.platform == "darwin":
    tool_paths = ["/opt/homebrew/bin", "/usr/local/bin"]
    current_path = os.environ.get("PATH", "")
    os.environ["PATH"] = os.pathsep.join(tool_paths + ([current_path] if current_path else []))

def physical_memory_gb():
    try:
        if sys.platform == "darwin":
            value = subprocess.run(["/usr/sbin/sysctl", "-n", "hw.memsize"], capture_output=True, text=True, timeout=2)
            if value.returncode == 0: return round(int(value.stdout.strip()) / (1024 ** 3))
        pages, size = os.sysconf("SC_PHYS_PAGES"), os.sysconf("SC_PAGE_SIZE")
        return round((pages * size) / (1024 ** 3))
    except (OSError, ValueError, subprocess.TimeoutExpired):
        return 0

SYSTEM_MEMORY_GB = physical_memory_gb()

import nle
from h3_prompts import (FPS, MAX_FRAMES, MAX_REFERENCES, PRESETS, SHEET_RECIPES,
                        SHEET_STYLES, analyze_cut_timeline, count_references, duration_for_frames,
                        format_prompt, format_image_prompt, format_sheet_prompt, get_sheet_recipe,
                        get_sheet_style, sheet_extract_times, validate_references)

ROOT = Path(__file__).resolve().parent
VERSION_FILE = ROOT / "VERSION"

def application_identity():
    try:
        version = VERSION_FILE.read_text().strip()
    except OSError:
        version = "0.0.0-dev"
    build = os.environ.get("OPENMAGIA_BUILD", "").strip()
    if not build:
        try:
            result = subprocess.run(["git", "rev-parse", "--short=8", "HEAD"], cwd=str(ROOT),
                                    capture_output=True, text=True, timeout=2)
            if result.returncode == 0: build = result.stdout.strip()
        except (OSError, subprocess.TimeoutExpired):
            pass
    return {"version":version, "build":build or "source"}

APP_IDENTITY = application_identity()
MANAGED_FFMPEG_DIR = ROOT / "addons" / "ffmpeg" / "bin"
os.environ["PATH"] = str(MANAGED_FFMPEG_DIR) + os.pathsep + os.environ.get("PATH", "")
FFPROBE_COMPAT = ROOT / "ffprobe_compat.py"

# imageio-ffmpeg deliberately bundles only ffmpeg. h3.c also invokes ffprobe
# to read reference dimensions, so use the host binary when available and the
# bundled, narrowly compatible inspector everywhere else. Explicit overrides
# remain authoritative for packaged distributions and advanced installations.
if not os.environ.get("H3_FFMPEG"):
    managed_ffmpeg = MANAGED_FFMPEG_DIR / "ffmpeg"
    resolved_ffmpeg = str(managed_ffmpeg) if managed_ffmpeg.is_file() else shutil.which("ffmpeg")
    if resolved_ffmpeg:
        os.environ["H3_FFMPEG"] = resolved_ffmpeg
if not os.environ.get("H3_FFPROBE"):
    resolved_ffprobe = shutil.which("ffprobe")
    if resolved_ffprobe:
        os.environ["H3_FFPROBE"] = resolved_ffprobe
    elif FFPROBE_COMPAT.is_file():
        os.environ["H3_FFPROBE"] = str(FFPROBE_COMPAT)
DATA = ROOT / "data"
SKILL_ROOT = ROOT / "skills" / "openmagia"
SKILL_CATALOG_FILE = SKILL_ROOT / "catalog.json"
PROJECTS = ROOT / "projects"
ACTIVE_FILE = DATA / "active.json"
CONFIG_FILE = ROOT / "config.json"

DEFAULTS = {
    "h3_bin": str(ROOT / "h3.c" / "h3"),
    "model_root": str(ROOT / "models" / "MiniMax-H3"),
    "port": 8730,
    "formatter_bin": str(ROOT / "addons" / "llama.cpp" / "build" / "bin" / "llama-cli"),
    "formatter_model": str(ROOT / "addons" / "models" / "qwen2.5-1.5b" / "Qwen2.5-1.5B-Instruct.Q4_K_M.gguf"),
}
def _load_config():
    cfg = dict(DEFAULTS)
    if CONFIG_FILE.exists():
        try:
            cfg.update(json.loads(CONFIG_FILE.read_text()))
        except Exception:
            pass
    cfg["h3_bin"] = os.environ.get("H3_BIN", cfg["h3_bin"])
    cfg["model_root"] = os.environ.get("H3_MODEL", cfg["model_root"])
    cfg["port"] = int(os.environ.get("OPENMAGIA_PORT", cfg["port"]))
    cfg["formatter_bin"] = os.environ.get("FORMATTER_BIN", cfg["formatter_bin"])
    cfg["formatter_model"] = os.environ.get("FORMATTER_MODEL", cfg["formatter_model"])
    return cfg

CFG = _load_config()
H3_BIN = CFG["h3_bin"]
H3_MODEL = CFG["model_root"]
PORT = CFG["port"]
FORMATTER_BIN = CFG["formatter_bin"]
FORMATTER_MODEL = CFG["formatter_model"]
MODEL_SOURCE_FILE = DATA / "model-sources.json"
MODEL_REGISTRY_FILE = DATA / "model-registry.json"
LORA_ROOT = ROOT / "models" / "loras"
SHEET_EXTRACTION_VERSION = 2

MODEL_BACKENDS = [
    {"id":"h3-metal", "name":"MiniMax H3 · Metal", "provider":"h3.c", "platforms":["darwin-arm64"],
     "memory_min":64, "disk_gb":278, "stability":"stable", "install_component":"h3",
     "summary":"Native Apple Silicon generation with text, first/last-frame, and ordered references.",
     "source":"https://github.com/antirez/h3.c", "supports":["t2va","fl2va","ref2va","audio-references"]},
]

def _load_model_registry():
    try:
        value = json.loads(MODEL_REGISTRY_FILE.read_text()) if MODEL_REGISTRY_FILE.exists() else {}
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError):
        return {}

def _save_model_registry(value):
    MODEL_REGISTRY_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = MODEL_REGISTRY_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(value, indent=2) + "\n")
    tmp.replace(MODEL_REGISTRY_FILE)

def hardware_profile():
    machine = platform.machine().lower()
    os_id = "darwin" if sys.platform == "darwin" else "windows" if os.name == "nt" else "linux"
    gpu, vram, cuda = "", 0, ""
    nvidia = shutil.which("nvidia-smi")
    if nvidia:
        try:
            run = subprocess.run([nvidia, "--query-gpu=name,memory.total,driver_version", "--format=csv,noheader,nounits"],
                                 capture_output=True, text=True, timeout=3)
            row = (run.stdout.strip().splitlines() or [""])[0].split(",")
            if run.returncode == 0 and row[0].strip():
                gpu = row[0].strip(); vram = round(float(row[1].strip()) / 1024, 1)
                cuda = row[2].strip() if len(row) > 2 else ""
        except (OSError, ValueError, subprocess.TimeoutExpired):
            pass
    return {"os":os_id, "architecture":machine, "platform":f"{os_id}-{machine}",
            "memory_gb":SYSTEM_MEMORY_GB, "gpu":gpu, "vram_gb":vram, "gpu_driver":cuda,
            "disk_free_gb":round(shutil.disk_usage(ROOT).free / (1024 ** 3), 1)}

def model_management_state():
    registry = _load_model_registry()
    installations = registry.setdefault("installations", [])
    loras = registry.setdefault("loras", [])
    active_path = str(Path(H3_MODEL).expanduser().resolve())
    existing = next((item for item in installations if item.get("path") == active_path), None)
    if _h3_model_valid(active_path) and not existing:
        managed_root = (ROOT / "models").resolve()
        path = Path(active_path)
        try: managed = path.is_relative_to(managed_root)
        except AttributeError: managed = str(path).startswith(str(managed_root) + os.sep)
        existing = {"id":"install-"+hashlib.sha256(active_path.encode()).hexdigest()[:12], "backend_id":"h3-metal",
                    "name":"MiniMax H3 · OpenMagia", "path":active_path, "managed":managed,
                    "receipt":[active_path] if managed else [], "imported":True}
        installations.append(existing); _save_model_registry(registry)
    for item in installations:
        item["active"] = item.get("path") == active_path
        item["available"] = Path(item.get("path") or "").exists()
    hw = hardware_profile(); platform_id = hw["platform"]
    catalog = []
    for raw in MODEL_BACKENDS:
        item = dict(raw); compatible = platform_id in item["platforms"]
        memory_ok = not item.get("memory_min") or hw["memory_gb"] >= item["memory_min"]
        vram_ok = not item.get("vram_min") or hw["vram_gb"] >= item["vram_min"]
        item.update(compatible=compatible, requirements_met=compatible and memory_ok and vram_ok,
                    installed=any(x.get("backend_id")==item["id"] and x.get("available") for x in installations))
        catalog.append(item)
    recommended = ""
    if platform_id == "darwin-arm64":
        recommended = "h3-metal" if hw["memory_gb"] >= 64 else ""
    for item in catalog: item["recommended"] = item["id"] == recommended
    return {"hardware":hw, "catalog":catalog, "installations":installations, "loras":loras}

def uninstall_managed_model(installation_id):
    global H3_MODEL
    registry = _load_model_registry(); items = registry.get("installations") or []
    item = next((x for x in items if x.get("id") == installation_id), None)
    if not item: raise ValueError("Managed installation not found.")
    target = Path(item.get("path") or "").resolve()
    was_active = str(target) == str(Path(H3_MODEL).expanduser().resolve())
    managed_root = (ROOT / "models").resolve()
    try: safe = target.is_relative_to(managed_root)
    except AttributeError: safe = str(target).startswith(str(managed_root) + os.sep)
    receipt = {str(Path(value).resolve()) for value in item.get("receipt") or []}
    if not item.get("managed") or not safe or str(target) not in receipt or str(target) in (str(managed_root), str(ROOT.resolve())):
        raise ValueError("OpenMagia can only uninstall files recorded in a managed installation receipt.")
    if target.exists(): shutil.rmtree(target)
    registry["installations"] = [x for x in items if x.get("id") != installation_id]
    _save_model_registry(registry)
    if was_active:
        # An empty generation slot is valid: the UI will offer installation
        # choices and generation preflight will report the missing backend.
        H3_MODEL = str(DEFAULTS["model_root"])
        _saved_model_sources.pop("h3_model", None)
        MODEL_SOURCE_FILE.parent.mkdir(parents=True, exist_ok=True)
        MODEL_SOURCE_FILE.write_text(json.dumps(_saved_model_sources, indent=2) + "\n")
    return {"ok":True, "removed":str(target), "active_removed":was_active}

def import_lora(path, backend_id=""):
    raise ValueError("The installed h3.c backend does not support LoRA adapters. No file was imported.")

def update_lora(lora_id, enabled=None, strength=None, backend_id=None):
    registry = _load_model_registry(); item = next((x for x in registry.get("loras", []) if x.get("id")==lora_id), None)
    if not item: raise ValueError("LoRA not found.")
    if enabled is not None: item["enabled"] = bool(enabled)
    if strength is not None: item["strength"] = max(0.0, min(2.0, float(strength)))
    if backend_id is not None:
        if backend_id and backend_id not in {x["id"] for x in MODEL_BACKENDS}: raise ValueError("That backend is not registered.")
        item["backend_id"] = backend_id
    _save_model_registry(registry); return item

def remove_lora(lora_id):
    registry = _load_model_registry(); items = registry.get("loras") or []
    item = next((x for x in items if x.get("id")==lora_id), None)
    if not item: raise ValueError("LoRA not found.")
    target = Path(item.get("path") or "").resolve(); root = LORA_ROOT.resolve()
    try: safe = target.is_relative_to(root)
    except AttributeError: safe = str(target).startswith(str(root) + os.sep)
    if not item.get("managed") or not safe: raise ValueError("Only managed LoRA files can be removed.")
    folder = target.parent
    if folder.exists(): shutil.rmtree(folder)
    registry["loras"] = [x for x in items if x.get("id") != lora_id]; _save_model_registry(registry)
    return {"ok":True}

def _load_model_sources():
    try:
        return json.loads(MODEL_SOURCE_FILE.read_text()) if MODEL_SOURCE_FILE.exists() else {}
    except (OSError, ValueError):
        return {}

_saved_model_sources = _load_model_sources()
H3_MODEL = str(_saved_model_sources.get("h3_model") or H3_MODEL)
FORMATTER_MODEL = str(_saved_model_sources.get("formatter_model") or FORMATTER_MODEL)
FORMATTER_ENDPOINT = str(_saved_model_sources.get("formatter_endpoint") or "")
FORMATTER_MODEL_ID = str(_saved_model_sources.get("formatter_model_id") or "")

def formatter_available():
    return bool(FORMATTER_ENDPOINT and FORMATTER_MODEL_ID) or (Path(FORMATTER_BIN).is_file() and Path(FORMATTER_MODEL).is_file())

def run_formatter_command(cmd, timeout=90):
    """Run the configured formatter through llama.cpp or a local OpenAI API."""
    if not (FORMATTER_ENDPOINT and FORMATTER_MODEL_ID):
        return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout,
                              cwd=str(Path(FORMATTER_BIN).parent))
    def value(flag, default=None):
        return cmd[cmd.index(flag)+1] if flag in cmd and cmd.index(flag)+1 < len(cmd) else default
    payload = {"model":FORMATTER_MODEL_ID, "messages":[{"role":"user", "content":value("-p", "")}],
               "max_tokens":int(value("-n", 512)), "temperature":float(value("--temp", .2)), "stream":False}
    if value("--seed") is not None: payload["seed"] = int(value("--seed"))
    req = urllib.request.Request(FORMATTER_ENDPOINT.rstrip("/") + "/chat/completions",
        data=json.dumps(payload).encode("utf-8"), headers={"Content-Type":"application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as response:
        result = json.loads(response.read().decode("utf-8"))
    content = (((result.get("choices") or [{}])[0].get("message") or {}).get("content") or "")
    return SimpleNamespace(stdout=content, stderr="", returncode=0)

def _h3_model_valid(path):
    root = Path(path).expanduser()
    return all((root / mode / "transformer" / "config.json").exists() for mode in ("FL2VA", "Ref2VA"))

_skill_catalog_cache = {"signature": None, "report": None}

def _skill_frontmatter(path):
    """Read authoritative display fields without adding a YAML dependency."""
    text = path.read_text(encoding="utf-8")
    if not text.startswith("---"):
        raise ValueError("SKILL.md has no YAML frontmatter")
    end = text.find("\n---", 3)
    if end < 0:
        raise ValueError("SKILL.md has incomplete YAML frontmatter")
    header, values = text[3:end], {}
    for key in ("name", "description"):
        match = re.search(rf"(?m)^{key}:\s*(.+?)\s*$", header)
        if match:
            values[key] = match.group(1).strip().strip("\"'")
    if not values.get("name") or not values.get("description"):
        raise ValueError("SKILL.md frontmatter needs name and description")
    return values

def skill_catalog_report():
    """Return valid skills plus isolated diagnostics for invalid entries."""
    try:
        entries = json.loads(SKILL_CATALOG_FILE.read_text(encoding="utf-8"))
        if not isinstance(entries, list):
            raise ValueError("catalog root must be an array")
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        return {"skills": [], "errors": [f"Bundled skill catalog is unavailable: {exc}"]}
    signature = [SKILL_CATALOG_FILE.stat().st_mtime_ns]
    for entry in entries:
        path = SKILL_ROOT / str(entry.get("id") or "") / "SKILL.md"
        signature.append(path.stat().st_mtime_ns if path.is_file() else -1)
    signature = tuple(signature)
    if _skill_catalog_cache["signature"] == signature:
        return _skill_catalog_cache["report"]
    valid, errors, seen = [], [], set()
    for entry in entries:
        skill_id = str(entry.get("id") or "").strip()
        try:
            if not re.fullmatch(r"[a-z0-9-]+", skill_id):
                raise ValueError("invalid or missing id")
            if skill_id in seen:
                raise ValueError("duplicate id")
            seen.add(skill_id)
            spec = SKILL_ROOT / skill_id / "SKILL.md"
            if not spec.is_file():
                raise ValueError("no readable SKILL.md")
            contract = entry.get("contract") or {}
            if not str(entry.get("instruction") or "").strip() or not contract.get("invariants"):
                raise ValueError("no machine contract")
            merged = dict(entry)
            merged.update(_skill_frontmatter(spec))
            valid.append(merged)
        except (OSError, ValueError) as exc:
            errors.append(f"Bundled skill {skill_id or '<missing id>'}: {exc}")
    report = {"skills": valid, "errors": errors}
    _skill_catalog_cache.update(signature=signature, report=report)
    return report

def skill_catalog():
    return skill_catalog_report()["skills"]

def skill_by_id(skill_id):
    skill_id = str(skill_id or "").strip()
    if not skill_id:
        return None
    skill = next((item for item in skill_catalog() if item["id"] == skill_id), None)
    if not skill:
        raise ValueError(f"Selected skill '{skill_id}' is unavailable. Repair its catalog entry or choose another skill.")
    return skill

_PROCESS_CONTRACT_TERMS = (
    "duration", "authored cut", "timing", "reference role", "official h3 field",
    "chronological beat map", "copy ledger", "character bible", "route ledger",
    "piece inventory", "asset provenance", "incoming state", "outgoing state",
)

def compile_skill_contract(skill_id):
    """Compile authoring rules separately from visual instructions sent to H3."""
    skill = skill_by_id(skill_id)
    if not skill:
        return None
    contract = skill.get("contract") or {}
    lists = {key: [str(v).strip() for v in contract.get(key) or [] if str(v).strip()]
             for key in ("invariants", "required", "forbidden")}
    refinement = [str(skill.get("instruction") or "").strip()]
    for label, key in (("Must preserve", "invariants"), ("Must include", "required"), ("Must not", "forbidden")):
        if lists[key]:
            refinement.append(label + ": " + "; ".join(lists[key]) + ".")
    visual = [str(skill.get("instruction") or "").strip()]
    for label, key in (("Preserve visually", "invariants"), ("Include on screen", "required"), ("Do not depict", "forbidden")):
        values = [v for v in lists[key] if not any(term in v.lower() for term in _PROCESS_CONTRACT_TERMS)]
        if values:
            visual.append(label + ": " + "; ".join(values) + ".")
    payload = {"id": skill["id"], "instruction": skill.get("instruction"), **lists}
    version = hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()[:12]
    return {"id": skill["id"], "name": skill["name"], "version": version,
            "refinement_direction": " ".join(refinement),
            "visual_direction": " ".join(visual), "validators": lists}

def compiled_skill_direction(skill_id):
    """Return the concise invariant contract used by refinement and formatting."""
    compiled = compile_skill_contract(skill_id)
    return compiled["refinement_direction"] if compiled else ""

def discover_model_sources():
    """Inventory compatible local files and running local inference servers."""
    found, seen = [], set()
    def add(item):
        key = (item.get("kind"), item.get("path") or item.get("endpoint"), item.get("id"))
        if key not in seen:
            seen.add(key); found.append(item)

    h3_candidates = [Path(H3_MODEL), ROOT / "models" / "MiniMax-H3"]
    hf = Path(os.environ.get("HF_HOME", Path.home() / ".cache" / "huggingface")) / "hub"
    for repo in (hf / "models--MiniMaxAI--MiniMax-H3", hf / "models--Comfy-Org--MiniMax-H3"):
        snapshots = repo / "snapshots"
        if snapshots.is_dir(): h3_candidates.extend(p for p in snapshots.iterdir() if p.is_dir())
    for path in h3_candidates:
        resolved = path.expanduser().resolve()
        if _h3_model_valid(resolved):
            add({"kind":"h3", "provider":"OpenMagia" if str(resolved).startswith(str(ROOT)) else "Hugging Face cache",
                 "name":"MiniMax H3", "path":str(resolved), "compatible":True,
                 "roles":[{"id":"video_generation", "label":"Video generation"}],
                 "active_role":"video_generation" if str(resolved)==str(Path(H3_MODEL).expanduser().resolve()) else "",
                 "active":str(resolved)==str(Path(H3_MODEL).expanduser().resolve())})

    gguf_roots = [Path.home()/".lmstudio"/"models", hf]
    for base in gguf_roots:
        if not base.is_dir(): continue
        pattern = "*/*/*.gguf" if ".lmstudio" in str(base) else "models--*/snapshots/*/*.gguf"
        for path in list(base.glob(pattern))[:250]:
            lower = path.name.lower()
            if lower.startswith("mmproj-") or "embedding" in lower or "embed-" in lower or "bge-" in lower: continue
            add({"kind":"formatter_file", "provider":"LM Studio" if ".lmstudio" in str(path) else "Hugging Face cache",
                 "name":path.stem, "path":str(path.absolute()), "compatible":True,
                 "roles":[{"id":"prompt_refinement", "label":"Prompt refinement"}],
                 "active_role":"prompt_refinement" if str(path.resolve())==str(Path(FORMATTER_MODEL).expanduser().resolve()) else "",
                 "active":str(path.resolve())==str(Path(FORMATTER_MODEL).expanduser().resolve())})
    configured = Path(FORMATTER_MODEL).expanduser()
    if configured.is_file():
        add({"kind":"formatter_file", "provider":"OpenMagia", "name":configured.stem,
             "path":str(configured.resolve()), "compatible":True,
             "roles":[{"id":"prompt_refinement", "label":"Prompt refinement"}],
             "active_role":"prompt_refinement", "active":True})

    for provider, endpoint in (("LM Studio", "http://127.0.0.1:1234/v1"), ("MLX-LM", "http://127.0.0.1:8080/v1")):
        try:
            req = urllib.request.Request(endpoint + "/models", headers={"Accept":"application/json"})
            with urllib.request.urlopen(req, timeout=.35) as response:
                payload = json.loads(response.read().decode("utf-8"))
            for model in payload.get("data", []):
                model_id = str(model.get("id") or "")
                if any(token in model_id.lower() for token in ("embed", "bge-")): continue
                add({"kind":"formatter_server", "provider":provider, "name":str(model.get("id") or "Local model"),
                     "id":model_id, "endpoint":endpoint, "compatible":True,
                     "roles":[{"id":"prompt_refinement", "label":"Prompt refinement"}],
                     "active_role":"prompt_refinement" if endpoint==FORMATTER_ENDPOINT and model_id==FORMATTER_MODEL_ID else "",
                     "active":endpoint==FORMATTER_ENDPOINT and model_id==FORMATTER_MODEL_ID,
                     "note":"Available through an OpenAI-compatible local server"})
        except Exception:
            pass
    return found

def select_model_source(kind, path="", endpoint="", model_id="", role=""):
    global H3_MODEL, FORMATTER_MODEL, FORMATTER_ENDPOINT, FORMATTER_MODEL_ID
    supplied = Path(path).expanduser()
    candidate = supplied.resolve()
    expected_role = "video_generation" if kind == "h3" else "prompt_refinement"
    if role and role != expected_role:
        raise ValueError("That model is not compatible with the selected OpenMagia role.")
    if kind == "h3":
        if not _h3_model_valid(candidate): raise ValueError("That folder is not a complete MiniMax H3 FL2VA + Ref2VA model.")
        H3_MODEL = str(candidate); _saved_model_sources["h3_model"] = H3_MODEL
    elif kind == "formatter_file":
        if not candidate.is_file() or supplied.suffix.lower() != ".gguf": raise ValueError("Prompt refinement requires a readable GGUF model file.")
        FORMATTER_MODEL = str(supplied.absolute()); FORMATTER_ENDPOINT = ""; FORMATTER_MODEL_ID = ""
        _saved_model_sources.update({"formatter_model":FORMATTER_MODEL, "formatter_endpoint":"", "formatter_model_id":""})
    elif kind == "formatter_server":
        parsed = urllib.parse.urlparse(endpoint)
        if parsed.hostname not in ("localhost", "127.0.0.1", "::1") or parsed.scheme != "http" or not model_id:
            raise ValueError("Only a named model on a localhost HTTP server can be connected.")
        FORMATTER_ENDPOINT = endpoint.rstrip("/"); FORMATTER_MODEL_ID = model_id
        _saved_model_sources.update({"formatter_endpoint":FORMATTER_ENDPOINT, "formatter_model_id":FORMATTER_MODEL_ID})
    else:
        raise ValueError("This source cannot be selected for that OpenMagia role yet.")
    MODEL_SOURCE_FILE.parent.mkdir(parents=True, exist_ok=True)
    MODEL_SOURCE_FILE.write_text(json.dumps(_saved_model_sources, indent=2) + "\n")
    return {"ok":True, "kind":kind, "path":str(candidate)}

def ref2va_available():
    return (Path(H3_MODEL) / "Ref2VA" / "transformer" / "config.json").exists()

for d in (DATA, PROJECTS):
    d.mkdir(parents=True, exist_ok=True)

lock = threading.Lock()
job_lock = threading.Lock()
project_save_lock = threading.Lock()
active_job = None
scene_proc = None
queue = []
progress = {}
# character-sheet composition shares the h3 process pattern but keeps its own
# worker state so scene jobs are never blocked by (or blockers of) a sheet.
sheet_queue = []
sheet_active = None
sheet_proc = None      # live h3 subprocess for the active sheet, if any
sheet_progress = {}
model_installs = {}
model_install_lock = threading.Lock()
MODEL_INSTALL_STALL_TIMEOUT = max(60, int(os.environ.get("OPENMAGIA_INSTALL_STALL_TIMEOUT", "1800")))
undo_stacks = {}
plugin_registry = PluginRegistry(DATA / "plugins.json", DATA / "plugin.log")

# H3 normally writes a progress line for every load or denoising step. If it
# remains silent for this long while still alive, Metal/CUDA has wedged rather
# than merely doing expensive inference. Keep this configurable for unusually
# slow hardware, but never let one dead child block the persistent queue
# forever.
H3_STALL_TIMEOUT = max(60, int(os.environ.get("OPENMAGIA_H3_STALL_TIMEOUT", "1200")))
# A progressing local render has no arbitrary wall-clock deadline. Users may
# opt into one through the environment, but the default of zero disables it.
# Inactivity is still guarded separately by H3_STALL_TIMEOUT.
_configured_hard_timeout = int(os.environ.get("OPENMAGIA_H3_HARD_TIMEOUT", "0"))
H3_HARD_TIMEOUT = max(H3_STALL_TIMEOUT, _configured_hard_timeout) if _configured_hard_timeout > 0 else 0


class GenerationStalled(RuntimeError):
    pass


def safe_retry_params(params):
    """Return a less memory-aggressive H3 schedule without lowering output resolution.

    Reuse=1 with all 50 transformer layers is the most failure-prone mode for
    long native-resolution clips. A single automatic retry retains frames,
    dimensions, steps and seed while using the stable streamed schedule.
    """
    fallback = dict(params or {})
    fallback["requested_layers"] = int(fallback.get("requested_layers", fallback.get("layers", 40)))
    fallback["requested_reuse"] = int(fallback.get("requested_reuse", fallback.get("reuse", 1)))
    fallback["layers"] = min(int(fallback.get("layers", 40)), 40)
    fallback["reuse"] = 1
    fallback["stability_adjusted"] = True
    fallback["effective_quality"] = "long-stable-retry"
    fallback["stability_reason"] = "The requested schedule stalled, so OpenMagia retried once with the stable memory schedule."
    return fallback

def terminate_process_tree(proc, timeout=5):
    """Stop a generation process and every subprocess it may have spawned."""
    if proc is None or proc.poll() is not None:
        return False
    if os.name != "nt":
        # On macOS a child can cross the exit boundary between poll() and
        # getpgid()/killpg(). launchd can also deny group signalling even
        # though signalling the direct child is permitted. Cleanup must never
        # replace the real generation error (notably GenerationStalled) with
        # an opaque EPERM, otherwise the worker cannot enter its stable retry.
        try:
            pgid = os.getpgid(proc.pid)
        except (ProcessLookupError, PermissionError, OSError):
            pgid = None
        group_stopped = False
        if pgid is not None:
            try:
                os.killpg(pgid, signal.SIGTERM)
                group_stopped = True
            except (ProcessLookupError, PermissionError, OSError):
                pass
        try:
            if not group_stopped:
                proc.terminate()
            proc.wait(timeout=timeout)
        except (ProcessLookupError, PermissionError, OSError, subprocess.TimeoutExpired):
            try:
                proc.kill()
                proc.wait(timeout=timeout)
            except (ProcessLookupError, PermissionError, OSError, subprocess.TimeoutExpired):
                pass
        # The parent may exit before a decoder/worker child. A final group kill
        # guarantees that removing a generation cannot leave compute behind.
        if pgid is not None:
            try:
                os.killpg(pgid, signal.SIGKILL)
            except (ProcessLookupError, PermissionError, OSError):
                pass
    else:
        try:
            proc.terminate()
            proc.wait(timeout=timeout)
        except Exception:
            try:
                proc.kill()
            except Exception:
                pass
    return True

def install_model_component(component):
    """Run an immutable snapshot of the idempotent installer in the background."""
    model_installs[component] = {"status": "running", "message": "Preparing download…"}
    flags = ["--no-formatter"] if component == "h3" else ["--no-models", "--no-h3"]
    snapshot = None
    try:
        # Bash may read a long-running script incrementally. Running a private
        # snapshot prevents an app update from changing offsets underneath an
        # active, resumable Hugging Face download.
        with tempfile.NamedTemporaryFile(prefix="openmagia-install-", suffix=".sh", delete=False) as tmp:
            tmp.write((ROOT / "install.sh").read_bytes())
            snapshot = tmp.name
        env = dict(os.environ, OPENMAGIA_ROOT=str(ROOT))
        run = subprocess.Popen(["/bin/bash", snapshot, *flags], cwd=str(ROOT), env=env,
                               stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                               text=True, errors="replace", bufsize=1,
                               start_new_session=(os.name != "nt"))
        output = []
        last_activity = [time.monotonic()]
        def read_output():
            current = ""
            while True:
                char = run.stdout.read(1)
                if not char: break
                last_activity[0] = time.monotonic()
                if char in "\r\n":
                    if current:
                        output.append(current); del output[:-80]
                        clean = re.sub(r"\x1b\[[0-9;]*m", "", current).strip()
                        if clean and not re.search(r"(?<!\d)\d{1,3}%", clean):
                            model_installs[component] = {"status":"running", "message":clean}
                    current = ""
                    continue
                current += char
                match = re.search(r"(?<!\d)(\d{1,3})%", current[-80:])
                if match:
                    percent = min(100, int(match.group(1)))
                    model_installs[component] = {"status":"running", "message":"Downloading…", "progress":percent}
            if current: output.append(current)
        reader = threading.Thread(target=read_output, daemon=True)
        reader.start()
        while True:
            try:
                returncode = run.wait(timeout=min(30, MODEL_INSTALL_STALL_TIMEOUT))
                break
            except subprocess.TimeoutExpired:
                if time.monotonic() - last_activity[0] < MODEL_INSTALL_STALL_TIMEOUT:
                    continue
                terminate_process_tree(run)
                raise RuntimeError(
                    f"Installation stopped making progress for {MODEL_INSTALL_STALL_TIMEOUT // 60} minutes. "
                    "Check the network connection, then choose Try again; completed files will be reused."
                )
        reader.join(timeout=2)
        lines = output[-8:]
        tail = "\n".join(re.sub(r"\x1b\[[0-9;]*m", "", line) for line in lines)
        model_installs[component] = {"status": "ready" if returncode == 0 else "error", "message": tail or "Installation finished"}
    except (OSError, subprocess.TimeoutExpired, RuntimeError) as exc:
        model_installs[component] = {"status": "error", "message": str(exc)}
    finally:
        if snapshot:
            try: Path(snapshot).unlink()
            except OSError: pass


def push_timeline_undo(project):
    slug = project.get("slug") or active_slug()
    stack = undo_stacks.setdefault(slug, [])
    stack.append(json.loads(json.dumps(project.get("tracks", []))))
    del stack[:-50]

def undo_timeline(project):
    stack = undo_stacks.get(project.get("slug") or active_slug(), [])
    if not stack:
        return False
    project["tracks"] = stack.pop()
    save_project(project)
    return True

BASE_TRACK = "V1"
OVERLAY_TRACK = "V2"
AUDIO_TRACK = "A1"


def next_track_id(project, kind):
    """Generate a unique track id like V3, A2."""
    prefix = "V" if kind == "video" else "A"
    existing = {t["id"] for t in project["tracks"]}
    n = 1
    while f"{prefix}{n}" in existing:
        n += 1
    return f"{prefix}{n}"


def default_tracks():
    return [
        {"id": OVERLAY_TRACK, "kind": "video", "name": "Overlay", "muted": False, "clips": []},
        {"id": BASE_TRACK, "kind": "video", "name": "Video", "muted": False, "clips": []},
        {"id": AUDIO_TRACK, "kind": "audio", "name": "Audio", "muted": False, "clips": []},
    ]


def new_project():
    return {
        "slug": None,
        "created": time.time(),
        "name": "Untitled",
        "canvas": {"width": 512, "height": 512},
        "base_prompt": "",
        "style_profile": {"name": "No project style", "prompt": "", "skill_id": None, "source": "custom"},
        "style_enabled": True,
        "project_style_skills": [],
        "storyboard_draft": None,
        "ui_layout": {},
        "characters": [],
        "sheets": [],     # character-sheet composition drafts (transient)
        "mediaFolders": [],  # media bin folders (path strings; root is implicit)
        "media": [],      # {id, src, name, kind, duration, w, h, hasAudio, source}
        "tracks": default_tracks(),
        "scenes": [],     # generation records (kept for re-generate + provenance)
        "order": [],
    }


def slugify(name):
    """Turn a project name into a stable, filesystem-safe folder slug."""
    import unicodedata
    t = unicodedata.normalize("NFKD", name or "").encode("ascii", "ignore").decode()
    t = re.sub(r"[^a-zA-Z0-9]+", "-", t).strip("-").lower()
    return t or "project"


def proj_dir(slug):
    return PROJECTS / slug


def proj_media_dir(project):
    d = proj_dir(project["slug"]) / "media"
    d.mkdir(parents=True, exist_ok=True)
    return d


def proj_uploads_dir(project):
    d = proj_dir(project["slug"]) / "uploads"
    d.mkdir(parents=True, exist_ok=True)
    return d


def abs_media(project, m):
    """Absolute path for a media item (its src is relative to the project folder)."""
    return proj_dir(project["slug"]) / m["src"].lstrip("/")


def _unique_target(d, name):
    """A path in d that does not collide with an existing file."""
    cand = d / name
    i = 1
    stem, suf = os.path.splitext(name)
    while cand.exists():
        cand = d / ("%s-%d%s" % (stem, i, suf))
        i += 1
    return cand


def _safe_media_stem(name):
    """A Finder/Explorer-safe visible filename stem."""
    stem = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "-", str(name or "Media")).strip().rstrip(". ")
    return stem or "Media"


def _unique_folder_copy_name(project, name, folder):
    used = {str(m.get("name") or "").strip().casefold() for m in project.get("media", [])
            if str(m.get("folder") or "") == folder}
    candidate = f"{name} copy"
    if candidate.casefold() not in used:
        return candidate
    index = 2
    while f"{name} copy {index}".casefold() in used:
        index += 1
    return f"{name} copy {index}"


def _move_media_record(project, media, folder, display_name=None):
    """Move one media record and its backing file while preserving its identity."""
    source = abs_media(project, media)
    if not source.is_file():
        raise FileNotFoundError(f"Media file is missing: {media.get('name') or media.get('id')}")
    current_folder = str(media.get("folder") or "")
    visible_name = str(display_name if display_name is not None else media.get("name") or source.stem).strip()
    if current_folder == folder and visible_name == str(media.get("name") or "").strip():
        return media
    target_dir = proj_media_dir(project) / folder if folder else proj_media_dir(project)
    target_dir.mkdir(parents=True, exist_ok=True)
    desired = target_dir / (_safe_media_stem(visible_name) + source.suffix.lower())
    target = desired if desired == source else _unique_target(target_dir, desired.name)
    if target != source:
        shutil.move(str(source), str(target))
    old_rel = str(media.get("src") or "").lstrip("/")
    new_rel = target.relative_to(proj_dir(project["slug"])).as_posix()
    media["src"] = new_rel
    media["name"] = target.stem
    media["folder"] = folder
    media.pop("folder_link", None)
    media.pop("folder_unique", None)
    replacements = {old_rel: new_rel, "/" + old_rel: "/" + new_rel}
    for scene in project.get("scenes", []):
        if scene.get("mediaId") != media.get("id"):
            continue
        for field in ("media", "first_frame", "last_frame"):
            if scene.get(field) in replacements:
                scene[field] = replacements[scene[field]]
    return media


def _media_fs_move(project, ids, folder, keep_both=False):
    """Relocate existing media into one folder without creating duplicates."""
    byid = {m.get("id"): m for m in project.get("media", [])}
    moved = 0
    for mid in ids:
        m = byid.get(mid)
        if not m:
            continue
        copy_name = _unique_folder_copy_name(project, str(m.get("name") or "Media"), folder) if keep_both else None
        _move_media_record(project, m, folder, display_name=copy_name)
        moved += 1
    return moved


def _folder_name_collisions(project, ids, folder):
    """Find destination references whose visible names collide with incoming media."""
    if not folder:
        return []
    byid = {m.get("id"): m for m in project.get("media", [])}
    incoming_names = {str(byid[i].get("name") or "").strip().casefold()
                      for i in ids if i in byid}
    incoming_ids = set(ids)
    return [m for m in project.get("media", []) if m.get("id") not in incoming_ids
            and str(m.get("folder") or "") == folder
            and str(m.get("name") or "").strip().casefold() in incoming_names]


def rename_media_file(project, media, requested_name):
    """Rename a media label and its backing file without breaking shared legacy paths."""
    source = abs_media(project, media)
    if not source.is_file():
        raise FileNotFoundError(f"Media file is missing: {media.get('name') or media.get('id')}")
    suffix = source.suffix
    visible = str(requested_name or "").strip()
    if suffix and visible.casefold().endswith(suffix.casefold()):
        visible = visible[:-len(suffix)]
    desired = source.with_name(_safe_media_stem(visible) + suffix.lower())
    target = source if desired == source else _unique_target(source.parent, desired.name)
    old_rel = str(media.get("src") or "").lstrip("/")
    if target != source:
        shared = any(other is not media and str(other.get("src") or "").lstrip("/") == old_rel
                     for other in project.get("media", []))
        if shared:
            shutil.copy2(source, target)
        else:
            source.rename(target)
    new_rel = target.relative_to(proj_dir(project["slug"])).as_posix()
    media["src"] = new_rel
    media["name"] = target.stem
    replacements = {old_rel: new_rel, "/" + old_rel: "/" + new_rel}
    for scene in project.get("scenes", []):
        if scene.get("mediaId") != media.get("id"):
            continue
        for field in ("media", "first_frame", "last_frame"):
            if scene.get(field) in replacements:
                scene[field] = replacements[scene[field]]
    return media


def repair_media_paths(project):
    """Repair legacy records left stale by an older folder rename."""
    base = proj_dir(project["slug"])
    changed = False
    for media in project.get("media", []):
        src = str(media.get("src") or "").lstrip("/")
        if not src or (base / src).is_file():
            continue
        name = Path(src).name
        folder = str(media.get("folder") or "")
        preferred = base / "media" / folder / name if folder else None
        matches = [preferred] if preferred and preferred.is_file() else list((base / "media").rglob(name))
        if len(matches) == 1:
            media["src"] = matches[0].relative_to(base).as_posix()
            changed = True
    return changed


def repair_generation_placeholders(project):
    """Keep every pending or failed generation visible in the Media panel."""
    changed = False
    media = project.setdefault("media", [])
    represented = {m.get("scene_id") for m in media if m.get("scene_id")}
    for scene in project.get("scenes", []):
        status = scene.get("status")
        if status not in ("queued", "running", "error") or scene.get("id") in represented:
            continue
        media.append({
            "id": uuid.uuid4().hex[:10], "asset_uid": uuid.uuid4().hex[:16], "src": "",
            "name": scene.get("name", "Generated scene"),
            "kind": "image" if scene.get("generation_type") == "image" else "video",
            "duration": 0, "w": scene.get("params", {}).get("width", 0),
            "h": scene.get("params", {}).get("height", 0), "hasAudio": False,
            "source": "generated", "status": status, "scene_id": scene.get("id"),
            "error": scene.get("error"), "style_profile": dict(scene.get("style_profile") or {}),
            "generation": {"prompt": scene.get("prompt", ""), "params": dict(scene.get("params") or {}),
                           "prompt_skill_id": scene.get("prompt_skill_id"),
                           "type": scene.get("generation_type", "video")},
        })
        changed = True
    return changed


def set_active(slug):
    ACTIVE_FILE.write_text(json.dumps({"slug": slug}))


def active_slug():
    if ACTIVE_FILE.exists():
        try:
            slug = json.loads(ACTIVE_FILE.read_text())["slug"]
            if (PROJECTS / slug / "project.json").exists():
                return slug
        except Exception:
            pass
    slugs = [d.name for d in sorted(PROJECTS.iterdir())
             if d.is_dir() and (d / "project.json").exists()] if PROJECTS.exists() else []
    if slugs:
        return slugs[0]
    return create_project("Untitled")["slug"]


def load_project():
    slug = active_slug()
    pfile = PROJECTS / slug / "project.json"
    if pfile.exists():
        p = json.loads(pfile.read_text())
        if "tracks" not in p:
            p["tracks"] = default_tracks()
        if "media" not in p:
            p["media"] = []
        if "sheets" not in p:
            p["sheets"] = []
        if "mediaFolders" not in p:
            p["mediaFolders"] = []
        if "style_profile" not in p:
            legacy = str(p.get("base_prompt") or "")
            p["style_profile"] = {"name": "Custom project style" if legacy else "No project style",
                                  "prompt": legacy, "skill_id": None, "source": "legacy"}
        p.setdefault("style_enabled", True)
        p.setdefault("project_style_skills", [])
        p.setdefault("storyboard_draft", None)
        if not p.get("slug"):
            p["slug"] = slug
        media_repaired = repair_media_paths(p)
        generation_media_repaired = repair_generation_placeholders(p)
        sheets_repaired = repair_completed_sheets(p)
        sheet_views_upgraded = upgrade_sheet_extractions(p)
        timeline_repaired = repair_timeline_overlaps(p)
        if media_repaired or generation_media_repaired or sheets_repaired or sheet_views_upgraded or timeline_repaired:
            save_project(p)
        return p
    p = new_project()
    p["slug"] = slug
    save_project(p)
    return p


def load_project_slug(slug):
    pfile = PROJECTS / slug / "project.json"
    if not pfile.exists():
        raise FileNotFoundError(slug)
    p = json.loads(pfile.read_text())
    p["slug"] = slug
    p.setdefault("sheets", [])
    p.setdefault("mediaFolders", [])
    p.setdefault("project_style_skills", [])
    p.setdefault("storyboard_draft", None)
    media_repaired = repair_media_paths(p)
    generation_media_repaired = repair_generation_placeholders(p)
    sheets_repaired = repair_completed_sheets(p)
    sheet_views_upgraded = upgrade_sheet_extractions(p)
    changed = False
    media = p.get("media", [])
    stamp = max([float(m.get("created") or 0) for m in media], default=0.0)
    if not stamp:
        stamp = max(0.0, time.time() - len(media))
    for m in media:
        if not m.get("asset_uid"):
            m["asset_uid"] = uuid.uuid4().hex[:16]
            changed = True
        if not m.get("created"):
            stamp += 1
            m["created"] = stamp
            changed = True
    if changed or media_repaired or generation_media_repaired or sheets_repaired or sheet_views_upgraded:
        save_project(p)
    return p


def asset_library():
    """Aggregate stable media assets and their project assignments."""
    by_uid = {}
    for summary in list_projects():
        try:
            project = load_project_slug(summary["slug"])
        except Exception:
            continue
        for media in project.get("media", []):
            uid = media["asset_uid"]
            item = by_uid.setdefault(uid, {
                "asset_uid": uid, "name": media.get("name", "Asset"),
                "kind": media.get("kind", "image"), "source": media.get("source", "media"),
                "duration": media.get("duration", 0), "w": media.get("w", 0), "h": media.get("h", 0),
                "status": media.get("status", "ready"), "scene_id": media.get("scene_id"),
                "style_profile": media.get("style_profile"), "generation": media.get("generation"),
                "assignments": [], "preview": f"/api/assets/{uid}/preview",
            })
            item["assignments"].append({"slug": project["slug"], "name": project.get("name", project["slug"]), "media_id": media["id"]})
    return list(by_uid.values())


def media_is_used(project, media_id):
    if any(c.get("mediaId") == media_id for t in project.get("tracks", []) for c in t.get("clips", [])):
        return True
    if any(s.get("mediaId") == media_id or s.get("media") == media_id for s in project.get("scenes", [])):
        return True
    return any(media_id in character_image_ids(c) for c in project.get("characters", []))


def assign_asset(asset_uid, desired_slugs):
    desired = list(dict.fromkeys(desired_slugs))
    if not desired:
        raise ValueError("Assign the asset to at least one project.")
    projects = {p["slug"]: load_project_slug(p["slug"]) for p in list_projects()}
    unknown = [slug for slug in desired if slug not in projects]
    if unknown:
        raise ValueError("Unknown project: " + ", ".join(unknown))
    source_project = source_media = None
    for project in projects.values():
        match = next((m for m in project.get("media", []) if m.get("asset_uid") == asset_uid), None)
        if match:
            source_project, source_media = project, match
            break
    if not source_media:
        raise FileNotFoundError(asset_uid)
    source_path = abs_media(source_project, source_media)
    protected = []
    # Add first, so the library always retains at least one usable copy.
    for slug in desired:
        project = projects[slug]
        if any(m.get("asset_uid") == asset_uid for m in project.get("media", [])):
            continue
        ext = source_path.suffix or ".bin"
        target = proj_media_dir(project) / f"shared-{asset_uid}{ext}"
        shutil.copy2(source_path, target)
        copied = dict(source_media)
        copied.update({"id": uuid.uuid4().hex[:10], "src": str(target.relative_to(proj_dir(slug))),
                       "source": "shared", "asset_uid": asset_uid})
        copied.pop("thumb", None)
        project.setdefault("media", []).append(copied)
        save_project(project)
    for slug, project in projects.items():
        if slug in desired:
            continue
        matches = [m for m in project.get("media", []) if m.get("asset_uid") == asset_uid]
        for media in matches:
            if media_is_used(project, media["id"]):
                protected.append(project.get("name", slug))
            else:
                project["media"].remove(media)
        save_project(project)
    return {"asset_uid": asset_uid, "projects": desired, "protected": protected}


def save_project(p):
    """Persist one complete snapshot without exposing a partial JSON file."""
    slug = p.get("slug") or active_slug()
    d = PROJECTS / slug
    d.mkdir(parents=True, exist_ok=True)
    target = d / "project.json"
    temporary = d / f".project-{uuid.uuid4().hex}.tmp"
    payload = json.dumps(p, indent=2)
    with project_save_lock:
        try:
            temporary.write_text(payload)
            os.replace(temporary, target)
        finally:
            temporary.unlink(missing_ok=True)


def list_projects():
    """Summary of every project for the gallery, including creation time."""
    out = []
    if not PROJECTS.exists():
        return out
    for d in sorted(PROJECTS.iterdir()):
        if not d.is_dir():
            continue
        pfile = d / "project.json"
        if not pfile.exists():
            continue
        try:
            p = json.loads(pfile.read_text())
        except Exception:
            continue
        cover = None
        for m in p.get("media", []):
            if m.get("thumb"):
                cover = m["thumb"]; break
        if not cover:
            for sc in p.get("scenes", []):
                if sc.get("first_frame"):
                    cover = sc["first_frame"]; break
        directory_stat = d.stat()
        created = float(p.get("created") or getattr(directory_stat, "st_birthtime", directory_stat.st_ctime))
        out.append({
            "slug": d.name,
            "name": p.get("name", d.name),
            "cover": cover,
            "created": round(created, 3),
            "updated": round(pfile.stat().st_mtime, 3),
            "active": d.name == active_slug(),
        })
    return out


def project_cover_path(slug):
    """Absolute path to a project's cover image (first media thumb, else first
    scene first-frame), or None. Used to serve covers for non-active projects."""
    pfile = PROJECTS / slug / "project.json"
    if not pfile.exists():
        return None
    try:
        p = json.loads(pfile.read_text())
    except Exception:
        return None
    cover = None
    for m in p.get("media", []):
        if m.get("thumb"):
            cover = m["thumb"]; break
    if not cover:
        for sc in p.get("scenes", []):
            if sc.get("first_frame"):
                cover = sc["first_frame"]; break
    if not cover:
        return None
    f = (PROJECTS / slug / cover.lstrip("/"))
    return f if f.exists() else None


def create_project(name):
    base = slugify(name)
    slug, n = base, 2
    while (PROJECTS / slug).exists():
        slug = f"{base}-{n}"; n += 1
    (PROJECTS / slug / "media").mkdir(parents=True)
    (PROJECTS / slug / "uploads").mkdir(parents=True)
    p = new_project()
    p["slug"] = slug
    p["name"] = name or "Untitled"
    save_project(p)
    set_active(slug)
    return p


def rename_project(slug, name):
    pfile = PROJECTS / slug / "project.json"
    if not pfile.exists():
        raise FileNotFoundError(slug)
    p = json.loads(pfile.read_text())
    p["name"] = (name or "").strip() or p.get("name", "Untitled")
    save_project(p)
    return p


def delete_project(slug):
    d = PROJECTS / slug
    if not d.exists():
        raise FileNotFoundError(slug)
    shutil.rmtree(d)
    if active_slug() == slug:
        slugs = [x["slug"] for x in list_projects()]
        if slugs:
            set_active(slugs[0])


def default_params(canvas):
    return {"width": canvas["width"], "height": canvas["height"],
            "frames": 56, "steps": 20, "layers": 45, "reuse": 2, "seed": 42}


def next_scene_name(project):
    """Return the next Scene N label across persisted and pending media."""
    used = []
    for item in [*project.get("scenes", []), *project.get("media", [])]:
        match = re.fullmatch(r"Scene\s+(\d+)", str(item.get("name") or "").strip(), re.IGNORECASE)
        if match:
            used.append(int(match.group(1)))
    return f"Scene {(max(used) + 1) if used else 1}"


def available_storyboard_scene_name(project, requested):
    """Keep authored titles, but never reuse an automatic Scene N label."""
    requested = str(requested or "").strip()
    if not requested:
        return next_scene_name(project)
    if not re.fullmatch(r"Scene\s+\d+", requested, re.IGNORECASE):
        return requested
    # Scene N is an automatic draft label, not an authored title. Rebase it at
    # submission time because another batch may have been queued since the
    # storyboard editor was opened.
    return next_scene_name(project)


def next_image_name(project):
    used = {int(match.group(1)) for scene in project.get("scenes", [])
            if (match := re.fullmatch(r"Image\s+(\d+)", str(scene.get("name") or "").strip(), re.IGNORECASE))}
    number = 1
    while number in used: number += 1
    return f"Image {number}"


def create_storyboard_batch(project, payload):
    """Create a complete storyboard and its dependency media atomically.

    Each continuation points at the predecessor's persisted pending-media ID.
    The serial queue guarantees that the source is ready before the dependent
    worker resolves its last frame.
    """
    cards = list(payload.get("scenes") or [])
    if len(cards) < 2:
        raise ValueError("A storyboard needs at least two scenes.")
    if len(cards) > 24:
        raise ValueError("A storyboard can contain at most 24 scenes.")
    output = dict(payload.get("output") or {})
    shared_params = clamp_generation_params({**default_params(project.get("canvas") or {"width": 768, "height": 768}), **output})
    audio_notes = str(output.get("audio_notes") or "").strip()
    audio_mode = shared_params.get("audio_mode", "effects")
    audio_answers = {
        "effects": {"sound": audio_notes or "clean synchronized physical sound effects and natural ambience", "music": "no non-diegetic music"},
        "full": {"sound": audio_notes or "clean synchronized physical sound effects and natural ambience", "music": "a restrained coherent score beneath the physical sound effects"},
        "dialogue": {"sound": audio_notes or "clear intelligible foreground dialogue with synchronized ambience", "music": "no music, or very low music beneath dialogue"},
        "silent": {"sound": "silence", "music": "none"},
    }[audio_mode]
    incoming_style = dict(payload.get("style_profile") or project.get("style_profile") or {})
    style_profile = {"name": str(incoming_style.get("name") or "Storyboard project style"),
                     "prompt": str(incoming_style.get("prompt") or ""),
                     "skill_id": incoming_style.get("skill_id"),
                     "source": str(incoming_style.get("source") or "custom")}
    use_style = payload.get("use_project_style", True) is not False and bool(style_profile["prompt"].strip())
    created, previous_media_id = [], None
    for index, card in enumerate(cards):
        prompt = str(card.get("prompt") or "").strip()
        if not prompt:
            raise ValueError(f"Scene {index + 1} needs a prompt.")
        char_ids = list(dict.fromkeys(card.get("character_ids") or []))
        reference_media_ids = list(dict.fromkeys(card.get("reference_media_ids") or []))
        character_reference_ids = {str(cid): list(dict.fromkeys(ids or []))
                                   for cid, ids in dict(card.get("character_reference_ids") or {}).items()}
        continue_previous = index > 0 and card.get("continue_previous", True) is not False
        source_media_id = previous_media_id if continue_previous else card.get("source_media_id")
        continuity_mode = str(card.get("continuity_mode") or ("frame" if source_media_id else "none")).lower()
        has_audio_reference = any(
            media.get("id") in reference_media_ids and media.get("kind") == "audio"
            for media in project.get("media", []))
        # Audio is Ref2VA-only. When a storyboard continues from the previous
        # scene, keep continuity by sending that final frame as Picture 1.
        if source_media_id and has_audio_reference:
            continuity_mode = "reference"
        if continuity_mode not in {"frame", "reference", "none"}:
            continuity_mode = "frame" if source_media_id else "none"
        if continuity_mode == "none":
            source_media_id = None
        params = clamp_generation_params({**shared_params, **dict(card.get("params") or {})})
        proposed = {"character_ids": char_ids,
                    "character_reference_ids": character_reference_ids if "character_reference_ids" in card else None,
                    "reference_media_ids": reference_media_ids,
                    "source_media_id": source_media_id,
                    "continuity_mode": continuity_mode}
        validation_refs = scene_all_references(proposed, project)
        if source_media_id and continuity_mode == "reference":
            validation_refs = [{"name": "Previous scene final frame", "paths": [Path("__continuity__.png")],
                                "kind": "continuity_reference"}] + validation_refs
        validate_references(validation_refs)
        actual_reference_count = (visual_reference_count(validation_refs)
                                  if continuity_mode == "reference"
                                  else 1 if source_media_id else visual_reference_count(validation_refs))
        validate_generation_prompt(prompt, params["frames"], actual_reference_count,
                                   continuation=bool(source_media_id))
        sid = uuid.uuid4().hex[:10]
        scene = {"id": sid, "name": available_storyboard_scene_name(project, card.get("name")),
                 "prompt": prompt,
                 "original_prompt": str(card.get("original_prompt") or prompt),
                 "refined_prompt": str(card.get("refined_prompt") or prompt),
                 "skill_compilation": dict(card.get("skill_compilation") or {}),
                 "character_ids": char_ids,
                 "character_reference_ids": character_reference_ids,
                 "reference_media_ids": reference_media_ids, "params": params,
                 "guide_answers": {**audio_answers, **dict(card.get("guide_answers") or {})},
                 "template_id": card.get("template_id"), "generation_type": "video",
                 "prompt_skill_id": card.get("prompt_skill_id"),
                 "style_profile": dict(style_profile) if use_style else {},
                 "use_project_style": use_style, "chain": False,
                 "source_media_id": source_media_id, "source_frame": "last",
                 "continuity_mode": continuity_mode,
                 "depends_on_scene_id": created[-1]["id"] if continue_previous else None,
                 "storyboard_id": str(payload.get("id") or ""), "storyboard_index": index,
                 "status": "queued", "error": None, "media": None, "mediaId": None,
                 "clipId": None, "first_frame": None, "last_frame": None}
        project.setdefault("scenes", []).append(scene)
        project.setdefault("order", []).append(sid)
        pending = {"id": uuid.uuid4().hex[:10], "asset_uid": uuid.uuid4().hex[:16], "src": "",
                   "name": scene["name"], "kind": "video", "duration": 0,
                   "w": params["width"], "h": params["height"], "hasAudio": False,
                   "source": "generated", "status": "queued", "scene_id": sid,
                   "style_profile": dict(scene["style_profile"]), "created": time.time(),
                   "generation": {"prompt": prompt, "params": dict(params),
                                  "prompt_skill_id": card.get("prompt_skill_id"), "type": "video"}}
        project.setdefault("media", []).append(pending)
        previous_media_id = pending["id"]
        created.append(scene)
    project["style_profile"] = style_profile
    project["base_prompt"] = style_profile["prompt"]
    project["style_enabled"] = use_style
    project["canvas"] = {"width": shared_params["width"], "height": shared_params["height"]}
    project["storyboard_draft"] = None
    return created


def _fallback_storyboard_split(prompt, part_count):
    """Return non-overlapping prose slices without retaining old timestamps."""
    source = structured_h3_description(prompt) if is_structured_h3_prompt(prompt) else str(prompt or "")
    source = re.sub(r"\b(?:CUT|SHOT)\s*\d+\s*(?:\||:|-)?\s*\d+(?:\.\d+)?\s*[-–—]\s*\d+(?:\.\d+)?s?\s*(?:—|-|:)?", "", source, flags=re.IGNORECASE)
    sentences = [item.strip() for item in re.split(r"(?<=[.!?])\s+|\n+", source) if item.strip()]
    if len(sentences) >= part_count:
        return [" ".join(sentences[(index * len(sentences)) // part_count:((index + 1) * len(sentences)) // part_count]) for index in range(part_count)]
    words = source.split()
    return [" ".join(words[(index * len(words)) // part_count:((index + 1) * len(words)) // part_count]) or "Continue the established action." for index in range(part_count)]


def _last_json_object(text):
    """Return the last complete JSON object from noisy, prompt-echoing CLI output."""
    decoder, objects = json.JSONDecoder(), []
    for match in re.finditer(r"\{", text):
        try:
            value, _ = decoder.raw_decode(text[match.start():])
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            objects.append(value)
    if not objects:
        return {}
    return next((item for item in reversed(objects) if any(key in item for key in ("scenes", "clips", "issues"))), objects[-1])


def detect_magia_style_intent(idea, use_model=True):
    """Extract explicit visual style and choose only a clearly matching workflow skill."""
    text = str(idea or "").strip()
    skills = [item for item in skill_catalog_report().get("skills", [])
              if item.get("id") != "h3-prompt"]
    allowed_ids = {str(item.get("id")) for item in skills}
    # This deterministic pass guarantees that a small model failure cannot
    # discard an explicit direction such as "Anime style."
    style_terms = re.compile(
        r"\b(?:style|styled|aesthetic|look|visual language|anime|animation|animated|live[- ]action|"
        r"photoreal(?:istic)?|cinematic|watercolou?r|claymation|stop[- ]motion|collage|papercraft|"
        r"motion graphics?|hand[- ]drawn|3d|2d|pixel art|comic(?: book)?)\b", re.IGNORECASE)
    sentences = [part.strip(" ,;:-") for part in re.split(r"(?<=[.!?])\s+|\n+", text) if part.strip()]
    fallback_style = " ".join(part for part in sentences if style_terms.search(part))[:1200]
    if not fallback_style:
        return "", None, False
    if not use_model or not formatter_available():
        return fallback_style, _match_magia_style_skill(fallback_style, skills), False
    choices = [{"id": item.get("id"), "name": item.get("name"),
                "description": item.get("description")} for item in skills]
    instruction = (
        "Analyze the user's idea only for explicit visual medium, rendering style, aesthetic, or named production workflow. "
        "Return JSON only with style_direction as a concise string copied or faithfully normalized from explicit user wording, and skill_id as one listed ID or null. "
        "Choose a skill only when the request clearly asks for that skill's whole workflow; generic anime, cinematic, realistic, watercolor, 2D, or other unmatched looks must use null. "
        "Do not infer a style from story subject matter and do not add style facts.\n"
        "AVAILABLE SKILLS:\n" + json.dumps(choices, ensure_ascii=False) + "\nIDEA:\n" + text[:12000])
    cmd = [FORMATTER_BIN, "-m", FORMATTER_MODEL, "-p", instruction, "-n", "500", "--temp", "0",
           "--seed", "0", "--no-display-prompt", "--log-disable", "--single-turn", "--simple-io"]
    try:
        run = run_formatter_command(cmd, timeout=75)
        raw = re.sub(r"\x1b\[[0-9;?]*[ -/]*[@-~]", "", run.stdout)
        data = _last_json_object(raw)
        extracted = str(data.get("style_direction") or "").strip()[:1200] if isinstance(data, dict) else ""
        style = extracted if extracted and style_terms.search(extracted) else fallback_style
        skill_id = str(data.get("skill_id") or "").strip() if isinstance(data, dict) else ""
        if skill_id not in allowed_ids or skill_id != _match_magia_style_skill(style or text, skills):
            skill_id = _match_magia_style_skill(style or text, skills)
        return style, skill_id or None, run.returncode == 0
    except (OSError, subprocess.TimeoutExpired, ValueError, json.JSONDecodeError):
        return fallback_style, _match_magia_style_skill(fallback_style, skills), False


def _match_magia_style_skill(style, skills):
    value = str(style or "").lower()
    aliases = (
        ("papercraft-stop-motion", ("papercraft", "folded paper", "paper stop motion")),
        ("paper-collage", ("paper collage", "cut-paper collage", "halftone collage")),
        ("3d-short", ("3d animation", "3d animated", "3d short")),
        ("motion-graphics", ("motion graphics", "kinetic graphics")),
        ("handdrawn-live", ("hand-drawn live", "hand drawn live", "drawn animation with live")),
        ("pov-film", ("pov film", "first-person film", "first person film")),
        ("fpv-tour", ("fpv tour", "first-person tour", "first person tour")),
    )
    available = {str(item.get("id")) for item in skills}
    return next((skill_id for skill_id, terms in aliases
                 if skill_id in available and any(term in value for term in terms)), None)


def optimize_storyboard_scenes(payload, use_model=True):
    """Split long storyboard cards into clips of no more than five seconds.

    The local model handles only creative temporal decomposition. OpenMagia
    owns durations, ordering, references, and continuation metadata.
    """
    cards = list(payload.get("scenes") or [])
    default_frames = max(8, min(360, int(payload.get("frames") or 56)))
    optimized, used_ai = [], False
    for card_index, card in enumerate(cards):
        frames = max(8, min(360, int((card.get("params") or {}).get("frames") or default_frames)))
        part_count = max(1, (frames + 119) // 120)
        if part_count == 1:
            copy = dict(card)
            copy["params"] = {**dict(card.get("params") or {}), "frames": frames}
            optimized.append(copy)
            continue
        durations = [min(120, frames - index * 120) for index in range(part_count)]
        prompt = str(card.get("prompt") or "").strip()
        split_prompts = []
        if use_model and formatter_available():
            seconds = [round(value / FPS, 3) for value in durations]
            instruction = (
                "Split one authored MiniMax H3 scene into a chronological continuation sequence. "
                f"Return JSON only as {{\"clips\":[exactly {part_count} strings]}}. Clip durations in order are {seconds} seconds. "
                "Each string must describe only the action visible during that interval, preserve every named character, identity, vehicle, prop, seating position, screen direction, environment, visible text, HUD state, and audio continuity, and end in a concrete state the next clip can inherit. "
                "Do not add people, props, dialogue, text, events, or outcomes. Do not repeat an action in multiple clips. "
                "Clip 2 onward must say it continues from the exact prior final frame. Use concise production-ready prose without H3 field labels or markdown.\nSCENE:\n" + prompt[:16000])
            cmd = [FORMATTER_BIN, "-m", FORMATTER_MODEL, "-p", instruction, "-n", "1400", "--temp", "0.1", "--seed", "0", "--no-display-prompt", "--log-disable", "--single-turn", "--simple-io"]
            try:
                run = run_formatter_command(cmd, timeout=90)
                raw = re.sub(r"\x1b\[[0-9;?]*[ -/]*[@-~]", "", run.stdout)
                data = _last_json_object(raw)
                candidate = data.get("clips") if isinstance(data, dict) else None
                if isinstance(candidate, list) and len(candidate) == part_count and all(str(item).strip() for item in candidate):
                    split_prompts = [str(item).strip() for item in candidate]
                    used_ai = run.returncode == 0
            except (OSError, subprocess.TimeoutExpired, ValueError, json.JSONDecodeError):
                pass
        if not split_prompts:
            fallback_parts = _fallback_storyboard_split(prompt, part_count)
            split_prompts = [
                (f"Part {index + 1} of {part_count}, lasting {durations[index] / FPS:.2f} seconds. "
                 + ("Begin with the established opening state. " if index == 0 else "Continue from the exact final frame of the previous clip without a visual or audio reset. ")
                 + "Perform only this chronological action; do not replay earlier action or introduce unestablished elements: " + fallback_parts[index])
                for index in range(part_count)
            ]
        for part_index, (part_prompt, part_frames) in enumerate(zip(split_prompts, durations)):
            split = dict(card)
            split["id"] = f"{card.get('id') or 'scene'}-opt-{part_index + 1}"
            split["name"] = f"{str(card.get('name') or f'Scene {card_index + 1}')} · {part_index + 1}/{part_count}"
            split["prompt"] = part_prompt
            split["params"] = {**dict(card.get("params") or {}), "frames": part_frames}
            if part_index:
                split["continue_previous"] = True
                split["source_media_id"] = None
                split["source_name"] = ""
            optimized.append(split)
    if len(optimized) > 24:
        raise ValueError("Optimization would create more than 24 scenes. Shorten the storyboard or optimize fewer scenes.")
    return {"scenes": optimized, "used_ai": used_ai, "max_frames": 120,
            "message": "Long scenes were split into chronological clips of at most five seconds. Review every split before generation."}


def make_magia_storyboard(payload, project, use_model=True):
    """Turn one idea into a deterministic, continuity-aware storyboard draft."""
    idea = str(payload.get("idea") or "").strip()
    if not idea:
        raise ValueError("Describe the idea first.")
    seconds = max(1.0, float(payload.get("duration_seconds") or 15))
    block_seconds = 5 if payload.get("optimize_five_seconds") else 15
    total_frames = max(8, round(seconds * FPS))
    block_frames = block_seconds * FPS
    part_count = (total_frames + block_frames - 1) // block_frames
    if part_count < 2:
        raise ValueError(f"Increase the duration to at least {block_seconds + 1} seconds to create a storyboard.")
    if part_count > 24:
        limit = 24 * block_seconds
        raise ValueError(f"This mode supports up to 24 scenes ({limit} seconds with the selected scene length).")
    durations = [block_frames] * (part_count - 1)
    durations.append(total_frames - block_frames * (part_count - 1))
    context = dict(payload.get("context") or {})
    character_ids = list(context.get("character_ids") or [])
    character_names = [str(item.get("name") or "") for item in project.get("characters", []) if item.get("id") in character_ids]
    reference_ids = list(context.get("reference_media_ids") or [])
    reference_names = [str(item.get("name") or "") for item in project.get("media", []) if item.get("id") in reference_ids]
    style = str(payload.get("style") or "").strip()
    skill_direction = str(payload.get("skill_direction") or "").strip()
    manual_skill_id = str(context.get("prompt_skill_id") or "").strip() or None
    detected_style, detected_skill_id, style_used_ai = detect_magia_style_intent(
        idea, use_model=use_model and not style and not manual_skill_id)
    selected_skill_id = manual_skill_id or detected_skill_id
    catalog_skill = next((item for item in skill_catalog_report().get("skills", [])
                          if item.get("id") == selected_skill_id), None)
    if catalog_skill and not skill_direction:
        skill_direction = str(catalog_skill.get("instruction") or "").strip()
    project_style = style
    if detected_style and not selected_skill_id:
        project_style = (style + " " + detected_style).strip() if style and detected_style.lower() not in style.lower() else detected_style
    split_prompts, used_ai = [], False
    if use_model and formatter_available():
        exact_seconds = [round(value / FPS, 3) for value in durations]
        instruction = (
            "Act as a creative storyboard writer. Expand one idea into a complete chronological MiniMax H3 story while preserving its subjects, relationships, setting, style, emotional intent, and promised outcome. Return JSON only as "
            f"{{\"scenes\":[exactly {part_count} strings]}}. Scene durations in order are {exact_seconds} seconds. "
            "Build a clear dramatic arc: establish the situation, develop it through distinct causal beats, deliver the requested emotional turn or climax, and use the final scene to complete the promised outcome with a satisfying closing image. "
            "Be visually inventive with supporting actions, reactions, staging, camera, atmosphere, and sound, but never contradict or replace the user's core idea. Do not divide the user's sentence or merely distribute its words between scenes. "
            "First infer the idea's immutable causal milestones and keep them in order. A character who finds, discovers, meets, or reunites with another must be visibly separated before that encounter; do not show them already together in the opening. Preserve stated emotional chronology exactly: for example, 'sad, then happy to see her' means the subject remains sad before seeing her and becomes happy because the reunion occurs. Never move the outcome into an earlier scene. "
            "Every scene must stand alone as a complete production-ready shot description, use complete sentences, describe only action that fits its allotted time, and end in a concrete state inherited by the next scene. "
            "Scene 2 onward must begin from the exact prior final frame and preserve identity, wardrobe, props, positions, screen direction, lighting, weather, text, and audio continuity. "
            "Do not mention these instructions, JSON, scene planning, or unavailable references inside a scene.\n"
            f"CAST: {', '.join(character_names) or 'none supplied'}\nREFERENCES: {', '.join(reference_names) or 'none supplied'}\n"
            f"PROJECT STYLE: {project_style or 'none supplied'}\nSKILL DIRECTION: {skill_direction or 'none supplied'}\nIDEA:\n{idea[:16000]}"
        )
        cmd = [FORMATTER_BIN, "-m", FORMATTER_MODEL, "-p", instruction, "-n", "6000", "--temp", "0.25", "--seed", "0",
               "--no-display-prompt", "--log-disable", "--single-turn", "--simple-io"]
        try:
            run = run_formatter_command(cmd, timeout=240)
            raw = re.sub(r"\x1b\[[0-9;?]*[ -/]*[@-~]", "", run.stdout)
            data = _last_json_object(raw)
            candidate = data.get("scenes") if isinstance(data, dict) else None
            if isinstance(candidate, list):
                candidate = [str((item.get("description") or item.get("prompt") or item.get("action") or "")
                                 if isinstance(item, dict) else item).strip() for item in candidate]
            if isinstance(candidate, list) and len(candidate) >= part_count and all(candidate):
                # Some OpenAI-compatible local servers ignore the supplied JSON
                # schema. Preserve their creative work by consolidating adjacent
                # surplus beats into the exact number of duration-owned scenes.
                split_prompts = [" ".join(str(item).strip() for item in candidate[
                    (index * len(candidate)) // part_count:((index + 1) * len(candidate)) // part_count])
                    for index in range(part_count)]
                used_ai = run.returncode == 0
        except (OSError, subprocess.TimeoutExpired, ValueError, json.JSONDecodeError):
            pass
    if not split_prompts:
        beat_directions = []
        for index in range(part_count):
            progress = index / max(1, part_count - 1)
            if index == 0:
                beat = "Establish the protagonist, setting, visual style, and initial emotional situation through a specific opening action."
            elif index == part_count - 1:
                beat = "Complete the promised outcome and emotional resolution, then finish on a satisfying final image; do not end mid-action."
            elif progress < .5:
                beat = "Develop the discovery or pursuit through a new causal action that moves the protagonist closer to the central encounter."
            elif progress < .8:
                beat = "Deliver the central encounter and emotional turn through visible behavior and reaction, building directly from the prior scene."
            else:
                beat = "Let the consequences of the emotional turn unfold through a distinct action that prepares the final resolution."
            beat_directions.append(beat)
        split_prompts = [
            (f"Scene {index + 1} of {part_count}, {durations[index] / FPS:.2f} seconds. "
             + ("Begin the story. " if index == 0 else "Continue from the exact final frame of the previous scene with no visual or audio reset. ")
             + beat_directions[index] + " Preserve this complete story intent without trying to show later beats early: " + idea)
            for index in range(part_count)
        ]
    scenes = []
    for index, (prompt, frames) in enumerate(zip(split_prompts, durations)):
        scenes.append({
            "id": f"magia-{uuid.uuid4().hex[:10]}", "name": f"Scene {index + 1}", "prompt": prompt,
            "character_ids": character_ids, "character_reference_ids": dict(context.get("character_reference_ids") or {}),
            "reference_media_ids": reference_ids, "prompt_skill_id": selected_skill_id,
            "continue_previous": index > 0, "source_media_id": None, "source_name": "",
            "params": {"frames": frames},
        })
    return {"scenes": scenes, "used_ai": used_ai, "style_used_ai": style_used_ai,
            "project_style": project_style if detected_style and not selected_skill_id else "",
            "detected_style": detected_style, "selected_skill_id": selected_skill_id,
            "total_frames": total_frames,
            "scene_seconds": [round(value / FPS, 3) for value in durations]}


def clamp_generation_params(params, generation_type="video"):
    """Normalize user parameters at the trust boundary."""
    out = dict(params)
    out["frames"] = 5 if generation_type == "image" else max(8, min(MAX_FRAMES, int(out.get("frames", 56))))
    # h3.c rejects fewer than two denoising steps, so clamp at the API
    # boundary instead of letting a queued job fail after process startup.
    out["steps"] = max(2, min(60, int(out.get("steps", 20))))
    allowed_sizes = {(512, 512), (512, 896), (896, 512),
                     (768, 768), (768, 1344), (1344, 768),
                     (1024, 768), (768, 1024)}
    size = (int(out.get("width", 768)), int(out.get("height", 768)))
    if size not in allowed_sizes:
        size = (768, 768)
    out["width"], out["height"] = size
    # h3.c rejects DiT layers outside [35, 50] at process start, so a smaller
    # value used to survive validation and only fail after queueing the job.
    out["layers"] = max(35, min(50, int(out.get("layers", 45))))
    out["reuse"] = max(1, min(3, int(out.get("reuse", 2))))
    out["quality"] = out.get("quality") if out.get("quality") in ("balanced", "high", "reference") else "balanced"
    out["audio_mode"] = out.get("audio_mode") if out.get("audio_mode") in ("effects", "full", "dialogue", "silent") else "effects"
    # Honor the selected quality schedule. If a long native render actually
    # stalls, the worker performs one explicit stable retry and records that
    # fallback in scene metadata instead of lowering quality pre-emptively.
    return out


def validate_generation_prompt(prompt, frames, actual_reference_count, continuation=False):
    """Reject prompts whose timeline or Picture labels cannot match the H3 command."""
    text = str(prompt or "").strip()
    # Long project-style and Ref2VA identity blocks legitimately push a
    # multi-character prompt beyond 9k. Structural validation below catches
    # the harmful nested timelines; reserve this ceiling for truly runaway
    # recursive prompts.
    if len(text) > 14000:
        raise ValueError("This scene prompt is too dense for reliable H3 generation. Split it into shorter scenes or remove repeated instructions.")
    timeline = analyze_cut_timeline(text, max(8, min(MAX_FRAMES, int(frames or 56))) / FPS)
    if timeline["errors"]:
        raise ValueError(" ".join(timeline["errors"]))
    picture_numbers = [int(x) for x in re.findall(r"<Picture\s+(\d+)>", text, re.IGNORECASE)]
    if picture_numbers and max(picture_numbers) > actual_reference_count:
        if continuation:
            raise ValueError("This continuation prompt names Cast pictures that cannot be sent with the previous-frame anchor. Refine it again so Picture 1 is only the opening frame and Cast is carried as identity notes.")
        raise ValueError(f"This prompt refers to Picture {max(picture_numbers)}, but only {actual_reference_count} reference image(s) will be sent.")
    exact_text = bool(re.search(r"(?i)(exact (?:text|title)|text\s+(?:a|b)\s*=|文字列|typography\s*:)", text))
    no_text = bool(re.search(r"(?i)visible text:\s*(?:no|none)|no visible text", text))
    # “No visible text other than the exact quoted phrases” is a whitelist,
    # not a contradiction. Reject only a blanket prohibition with no explicit
    # exception for the requested kinetic copy.
    text_whitelist = bool(re.search(
        r"(?is)(?:only|except(?:\s+for)?|other\s+than|apart\s+from|beyond|outside)"
        r".{0,100}(?:quoted|specified|exact).{0,60}(?:text|title|phrase|string|typograph)",
        text,
    ))
    if exact_text and no_text and not text_whitelist:
        raise ValueError("The prompt both requests exact typography and forbids visible text. Remove the conflicting no-text instruction.")
    return True


def sniff_kind(data: bytes, ext: str):
    """Classify an upload as image, video, or audio from magic bytes/extension."""
    if data[:4] == b"\x89PNG":
        return "image"
    if data[:3] == b"\xff\xd8\xff":
        return "image"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image"
    if data[:4] in (b"GIF8",):
        return "image"
    audio_exts = (".wav", ".mp3", ".m4a", ".aac", ".flac", ".ogg", ".opus")
    if ext.lower() in audio_exts or data[:4] in (b"fLaC", b"OggS") or data[:3] == b"ID3":
        return "audio"
    if data[:4] == b"RIFF" and data[8:12] == b"WAVE":
        return "audio"
    if data[4:8] == b"ftyp":
        return "audio" if ext.lower() in audio_exts else "video"
    # fallback to extension
    return "image" if ext.lower() in (".png", ".jpg", ".jpeg", ".webp", ".bmp") else "video"


def media_url(m):
    return m["src"]


def character_image_ids(c):
    """Ordered media ids for a character (new 'images' list, legacy 'image' fallback)."""
    ids = c.get("images") or []
    if not ids and c.get("image"):
        ids = [c["image"]]
    return list(ids)


def prioritized_character_image_ids(character, project):
    """Put the most identity-useful views first when a scene must share H3's budget."""
    media = {m["id"]: m for m in project.get("media", [])}
    def priority(media_id):
        name = str(media.get(media_id, {}).get("name") or "").casefold()
        if "three-quarter" in name:
            return 1
        if "front face" in name:
            return 2
        if "front" in name:
            return 0
        if "left" in name:
            return 3
        if "right" in name:
            return 4
        if "back" in name:
            return 5
        return 6
    ids = [mid for mid in character_image_ids(character) if mid in media]
    return sorted(ids, key=lambda mid: (priority(mid), ids.index(mid)))


def scene_characters(scene, project):
    """Attached characters sharing the remaining nine-image budget fairly."""
    char_ids = scene.get("character_ids", [])
    characters = [next((item for item in project["characters"] if item["id"] == cid), None) for cid in char_ids]
    characters = [c for c in characters if c and prioritized_character_image_ids(c, project)]
    visual_count = sum(1 for mid in scene.get("reference_media_ids", [])
                       if any(m["id"] == mid and m.get("kind") == "image" for m in project.get("media", [])))
    # Hybrid continuation sends the predecessor's final frame as Picture 1
    # alongside Cast. Reserve that slot before distributing the remaining
    # eight images so H3's nine-image limit cannot be exceeded silently.
    if scene.get("source_media_id") and scene.get("continuity_mode") == "reference":
        visual_count += 1
    budget = max(0, MAX_REFERENCES - visual_count)
    if len(characters) > budget:
        raise ValueError("The nine-reference budget needs at least one reference image per attached character.")
    explicit = scene.get("character_reference_ids")
    if isinstance(explicit, dict):
        out = []
        total = 0
        for c in characters:
            allowed = prioritized_character_image_ids(c, project)
            requested = list(dict.fromkeys(explicit.get(c["id"]) or []))
            chosen = [mid for mid in requested if mid in allowed]
            if not chosen:
                raise ValueError(f"Select at least one reference image for {c['name']}.")
            total += len(chosen)
            paths = [abs_media(project, next(m for m in project["media"] if m["id"] == mid)) for mid in chosen]
            out.append({"name": c["name"], "description": c.get("description", ""), "paths": paths})
        if total > budget:
            raise ValueError("MiniMax H3 supports at most 9 reference images across cast and visual references.")
        return out
    allocations = {c["id"]: 0 for c in characters}
    remaining = budget
    advanced = True
    while remaining and advanced:
        advanced = False
        for c in characters:
            available = len(prioritized_character_image_ids(c, project))
            if remaining and allocations[c["id"]] < available:
                allocations[c["id"]] += 1
                remaining -= 1
                advanced = True
    out = []
    for c in characters:
        paths = []
        for mid in prioritized_character_image_ids(c, project)[:allocations[c["id"]]]:
            m = next((x for x in project["media"] if x["id"] == mid), None)
            if m:
                paths.append(abs_media(project, m))
        if paths:
            out.append({"name": c["name"], "description": c.get("description", ""), "paths": paths})
    return out


def scene_visual_references(scene, project):
    """Ordered non-cast visual and audio references for Ref2VA."""
    out = []
    for mid in scene.get("reference_media_ids", []):
        m = next((x for x in project.get("media", []) if x["id"] == mid and x.get("kind") in ("image", "audio")), None)
        if m and m.get("kind") == "image":
            out.append({"name": m.get("name") or "Visual reference", "description": "Use this image as a visual reference; preserve relevant subject geometry, materials, palette, labels, and design details requested by the prompt, but do not treat it as a character identity or opening frame.", "paths": [abs_media(project, m)], "kind": "visual_reference"})
        elif m:
            out.append({"name": m.get("name") or "Audio reference",
                        "description": "Use this audio only for the music, rhythm, voice timbre, dialogue timing, lip sync, ambience, or sound characteristics explicitly requested in the prompt.",
                        "paths": [abs_media(project, m)], "kind": "audio_reference",
                        "duration": float(m.get("duration") or 0)})
    return out


def scene_all_references(scene, project):
    return scene_characters(scene, project) + scene_visual_references(scene, project)

def visual_reference_count(references):
    """Count H3 Picture inputs without treating Ref2VA audio as a picture."""
    return sum(len(item.get("paths", [])) for item in references
               if item.get("kind") != "audio_reference")

def h3_reference_args(references):
    """Build ordered native Ref2VA inputs with an explicit flag per media type."""
    args = []
    for item in references:
        flag = "--ref-audio" if item.get("kind") == "audio_reference" else "--ref-image"
        for path in item.get("paths", []):
            args.extend([flag, str(path)])
    return args

def scene_identity_text(scene, project):
    """Text-only identity locks used when I2VA cannot accept reference images."""
    locks = []
    for cid in scene.get("character_ids", []):
        character = next((c for c in project.get("characters", []) if c.get("id") == cid), None)
        if character:
            locks.append(f"{character.get('name', 'Character')}: {character.get('description') or 'Preserve the established identity exactly.'}")
    if not locks:
        return ""
    topology = (
        " FRAME-ANCHOR TOPOLOGY LOCK: the opening frame supplies composition and motion state, while these text locks "
        "supply identity facts that may be unclear from that single view. Every named body part, limb, face, mask, prop, "
        "and connection is a persistent object for the entire shot: do not add, remove, merge, split, swap, invert, "
        "reorient, or reinterpret any of them. Rear or occluded views are not alternate anatomy. If a part passes behind "
        "another object, preserve it off-screen and return the same part with the same material, orientation, and connection. "
        "Keep heads and masks upright relative to the established body and ground. Prefer simpler motion over violating "
        "topology; never satisfy an action by transforming the character. During the opening second, continue the existing "
        "gait and silhouette without a pose reset before beginning the new action."
    )
    return " Character identity locks (text authority while the continuation frame is used): " + " | ".join(locks) + topology

def compact_scene_prompt(prompt, force=False):
    """Remove recursively embedded H3/style history while retaining the latest scene beats."""
    text = str(prompt or "").strip()
    if len(text) <= 10000 and not force:
        return text
    start = text.rfind("CUT 01")
    end = text.find("overall_soundscape:", start)
    if start >= 0:
        compact = text[start:end if end > start else None].strip(" .\n")
        return compact[:9000]
    return text[-9000:]


def is_structured_h3_prompt(value):
    """Return whether the user already supplied an official H3 prompt."""
    text = str(value or "").strip()
    video = all(field in text for field in
                ("detailed_description:", "overall_soundscape:", "non_diegetic_music:"))
    image = "integrated_multimodal_description:" in text
    prefix = text.startswith(("subject_definitions:", "integrated_multimodal_description:",
                              "For the target video,", "For the target image,",
                              "How the reference pictures align"))
    return prefix and (video or image)


def requested_duration_seconds(value):
    """Read an explicit authored duration without confusing fps or timestamps.

    The prompt is user authority during refinement. Historically the client
    could submit its untouched 56-frame default beside "make a 5-second
    video", silently turning the request into 2.33 seconds.
    """
    text = str(value or "")
    cuts = list(re.finditer(
        r"(?i)\bCUT\s+\d{1,2}\s*\|\s*\d+(?:\.\d+)?\s*[\-–—]\s*(\d+(?:\.\d+)?)s?\b",
        text,
    ))
    if cuts:
        return float(cuts[-1].group(1))
    match = re.search(
        r"(?i)\b(?:create\s+(?:a\s+)?)?(\d+(?:\.\d+)?)\s*(?:-\s*)?(?:second|seconds|sec|secs)\b",
        text,
    )
    if not match:
        return None
    seconds = float(match.group(1))
    return seconds if .33 <= seconds <= (MAX_FRAMES / FPS) else None


def structured_prompt_character_ids(value, project):
    """Recover Cast named in a structured prompt when UI state was lost."""
    if not is_structured_h3_prompt(value):
        return []
    header = re.split(r"\b(?:summary|retention_analysis|detailed_description):", str(value),
                      maxsplit=1, flags=re.IGNORECASE)[0]
    return [c["id"] for c in project.get("characters", [])
            if re.search(rf"<Subject\s+\d+>[^\n.]{{0,220}}\b{re.escape(c.get('name', ''))}\b",
                         header, re.IGNORECASE)]


def structured_h3_description(value):
    """Extract creative action from a complete prompt without stale schema."""
    text = str(value or "").strip()
    marker = "detailed_description:"
    start = text.rfind(marker)
    if start < 0:
        marker = "integrated_multimodal_description:"
        start = text.rfind(marker)
    if start < 0:
        return text
    body = text[start + len(marker):]
    stops = [body.find(field) for field in
             ("overall_soundscape:", "non_diegetic_music:") if body.find(field) >= 0]
    if stops:
        body = body[:min(stops)]
    return body.strip()

def apply_skill_contract_to_structured(value, skill_id, seconds):
    """Validate a complete H3 prompt and attach the immutable skill contract."""
    text = str(value or "").strip()
    compiled = compile_skill_contract(skill_id)
    direction = compiled["visual_direction"] if compiled else ""
    if not direction:
        return text
    body = structured_h3_description(text)
    validate_skill_prompt_integrity(body, skill_id)
    authored = analyze_cut_timeline(body, seconds)
    if authored["present"] and authored["errors"]:
        raise ValueError("The structured prompt conflicts with its selected duration: " + " ".join(authored["errors"]))
    marker = "detailed_description:" if "detailed_description:" in text else "integrated_multimodal_description:"
    signature = f"Skill direction ({skill_id}@{compiled['version']}):"
    if signature in text:
        return text
    return text.replace(marker, marker + " " + signature + " " + direction, 1)


_POV_EXTERNAL_CAMERA_PATTERNS = (
    r"\bcamera\s+(?:follows?|tracks?)\s+(?:her|him|them|the\s+(?:courier|viewer|protagonist|character))\b",
    r"\bcamera\s+pulls?\s+back[^.\n]{0,120}\b(?:show|frame|reveal)(?:s|ing)?\s+(?:her|him|them|the\s+(?:courier|viewer|protagonist|character)|(?:her|his|their)\s+body)\b",
    r"\b(?:wide|medium|full[- ]body|over[- ]the[- ]shoulder|selfie|third[- ]person)\s+(?:shot|view|angle|coverage)\b",
    r"\b(?:show|frame|reveal)(?:s|ing)?\s+the\s+(?:viewer|camera wearer|camera-wearer|protagonist)(?:'s)?\s+(?:face|body|figure)\b",
)


def validate_skill_prompt_integrity(value, skill_id):
    """Reject visual instructions that directly contradict a hard skill contract."""
    if str(skill_id or "").strip() != "pov-film":
        return
    text = str(value or "")
    matches = [m.group(0).strip() for pattern in _POV_EXTERNAL_CAMERA_PATTERNS
               if (m := re.search(pattern, text, re.IGNORECASE))]
    if matches:
        raise ValueError(
            "POV refinement attempted to show the camera wearer from outside the first-person view "
            f"({matches[0]}). Keep the camera as the wearer’s eyes and describe only plausible hands, "
            "forearms, feet, or body-edge cues."
        )


def enforce_skill_expansion(value, original_idea, skill_id, seconds):
    """Keep a weak local formatter from overriding an attached skill's hard invariants."""
    try:
        validate_skill_prompt_integrity(value, skill_id)
        return value
    except ValueError:
        if str(skill_id or "").strip() != "pov-film":
            raise
    idea = str(original_idea or "").strip()
    return (
        f"One continuous {seconds:.2f}-second literal first-person shot from the camera wearer’s eyes. "
        "The camera wearer is never visible externally; no third-person, selfie, reverse, "
        "over-the-shoulder, or pull-back coverage. Preserve the opening reference’s camera axis, "
        "handedness, sleeves or gloves, held objects, and geography. Show only anatomically plausible "
        "hands, forearms, knees, feet, or body-edge cues entering from the wearer’s position. "
        f"Execute this requested action without changing viewpoint: {idea}"
    )


def build_prompt(scene, project, ref2va, chain_frame=None, generation_refs=None):
    """Compose the exact official H3 field structure for the active mode."""
    base = ((scene.get("style_profile") or {}).get("prompt") or "").strip() if scene.get("use_project_style", True) else ""
    raw_prompt = str(scene.get("prompt") or "").strip()
    formatted_input = raw_prompt.startswith(("integrated_multimodal_description:", "subject_definitions:", "For the target video,", "How the reference pictures align"))
    prompt = (structured_h3_description(raw_prompt) if chain_frame and formatted_input
              else compact_scene_prompt(raw_prompt, force=False))
    if not chain_frame and prompt.startswith(("integrated_multimodal_description:", "subject_definitions:", "For the target video,", "How the reference pictures align")):
        # Never trust the client to have compiled the selected skill. This
        # execution-time pass makes structured prompts and older saved scenes
        # obey the same immutable contract as newly refined prompts.
        return apply_skill_contract_to_structured(
            prompt,
            scene.get("prompt_skill_id"),
            max(.33, float((scene.get("params") or {}).get("frames", 56)) / 24),
        )
    chars = scene_all_references(scene, project) if generation_refs is None else generation_refs
    if chain_frame:
        # h3.c rejects frame anchors combined with Ref2VA. Keep the selected
        # frame exact via I2VA/FL2VA and carry Cast as text identity locks.
        chars = []
        prompt += scene_identity_text(scene, project)
    mode = "ref2va" if ref2va and chars else ("i2va" if chain_frame else "t2va")
    answers = dict(scene.get("guide_answers") or {})
    direction = compiled_skill_direction(scene.get("prompt_skill_id"))
    if direction:
        answers["skill_instruction"] = direction
    return format_prompt(idea=prompt, style=base,
                         frames=scene.get("params", {}).get("frames", 56),
                         mode=mode, characters=chars,
                         answers=answers)


def improve_idea_locally(idea, answers, project_style=""):
    """Use the optional small local model for prose expansion, never schema."""
    # A user-authored chronological CUT plan is already the creative schema.
    # The formatter may validate and wrap it, but must never replace it with a
    # second generic timeline (the previous behavior buried the real sequence
    # inside CUT 01 and invented unrelated beats afterward).
    authored = analyze_cut_timeline(idea, float(answers.get("_duration_seconds") or 2.33))
    if authored["present"]:
        if authored["errors"]:
            raise ValueError("Invalid authored CUT timeline: " + " ".join(authored["errors"]))
        continuity = str(answers.get("continuity") or "").strip()
        return (((continuity + " ") if continuity else "") + idea.strip(), False)
    context_keys = {"setting", "camera", "transitions", "pacing", "text", "sound", "music", "continuity", "continuity_review", "reference_audio"}
    context = "; ".join(f"{k}: {v}" for k, v in answers.items() if k in context_keys and v)
    skill_direction = str(answers.get("skill_instruction") or "").strip()
    seconds = max(.33, float(answers.get("_duration_seconds") or 2.33))
    try: cuts = max(1, min(15, int(answers.get("cuts") or max(1, round(seconds)))))
    except (TypeError, ValueError): cuts = max(1, round(seconds))
    setting = answers.get("setting") or "the location described or implied by the idea"
    camera = answers.get("camera") or "motivated framing that progresses from a clear establishing view to the decisive detail"
    transitions = answers.get("transitions") or "clean motivated cuts"
    pacing = answers.get("pacing") or "purposeful pacing"
    text = answers.get("text") or "no visible text"
    beats = []
    for i in range(cuts):
        start, end = seconds * i / cuts, seconds * (i + 1) / cuts
        action = (f"Show the immediate setup and physical motivation for the action in {setting}: {idea.strip()}" if i == 0 else
                  f"Show the concrete aftermath, the subject’s reaction, and the environment responding to the completed action while preserving continuity" if i == cuts - 1 else
                  f"Show the action at its most visually expressive midpoint, including body mechanics, momentum, and a specific environmental reaction")
        beats.append(f"CUT {i+1:02d} | {start:.2f}-{end:.2f}s — {action}.")
    continuity = str(answers.get("continuity") or "").strip()
    fallback = ((continuity + " ") if continuity else "") + (f"Create a {seconds:.2f}-second video at 24fps with exactly {cuts} distinct cut{'s' if cuts != 1 else ''}. " +
                " ".join(beats) + f" Camera: {camera}. Transitions: {transitions}. Pacing: {pacing}. Visible text: {text}. " +
                f"Sound: {answers.get('sound') or 'synchronized natural ambience and physical action sounds'}. Music: {answers.get('music') or 'a restrained score synchronized to the visual rhythm'}.")
    if not (formatter_available()):
        return fallback, False
    timing = ", ".join(f"CUT {i+1:02d} {seconds*i/cuts:.2f}-{seconds*(i+1)/cuts:.2f}s" for i in range(cuts))
    style_contract = ("The following PROJECT STYLE is established continuity authority. Use it to resolve every visual, identity, spatial, text, camera, lighting, and sound decision, but do not copy or restate the bible in the CUT lines. Add only the new scene action and scene-specific details; if the request conflicts, preserve the project style unless the user explicitly changes a fact. PROJECT STYLE: " + project_style + " " if project_style else "")
    skill_contract = ("The following SKILL CONTRACT is immutable. Improve the user's idea inside it; do not summarize, weaken, replace, or contradict it. SKILL CONTRACT: " + skill_direction + " " if skill_direction else "")
    instruction = (style_contract + skill_contract + "Expand this idea as a creative film director into one coherent, physically plausible short scene. "
                   f"It lasts only {seconds:.2f} seconds. Output exactly {cuts} compact CUT lines with these exact ranges: {timing}. "
                   "In every line specify concrete visible action plus camera framing or motion. Invent useful setting, lighting, atmosphere, body mechanics, environmental reaction, and a memorable final image when missing. "
                   "Use anticipation/setup, the action at peak motion, then landing/aftermath. Keep the subject in one continuous geography and land them on the nearest physically reachable surface; never teleport them or invent a rooftop. "
                   "Do not repeat the action, add impossible travel, or use placeholders such as intermediate beat, clear payoff, establish the location, or choose a setting. "
                   "Preserve all names, quoted text, dialogue, counts, and times. No markdown, screenplay headings, analysis, H3 fields, or instructions. Keep it concise but include every skill invariant and required production fact. "
                   f"Direction: {context or 'make strong directorial choices'}. Idea: {idea}")
    cmd = [FORMATTER_BIN, "-m", FORMATTER_MODEL, "-p", instruction,
           "-n", "480", "--temp", "0.3", "--no-display-prompt", "--log-disable", "--single-turn", "--simple-io"]
    try:
        run = run_formatter_command(cmd, timeout=60)
        raw = re.sub(r"\x1b\[[0-9;?]*[ -/]*[@-~]", "", run.stdout)
        text = ""
        start = raw.rfind("OPENMAGIA_RESULT_BEGIN")
        end = raw.find("OPENMAGIA_RESULT_END", start + len("OPENMAGIA_RESULT_BEGIN")) if start >= 0 else -1
        if start >= 0 and end > start:
            text = raw[start + len("OPENMAGIA_RESULT_BEGIN"):end].strip()
        elif "\n> " in raw:
            tail = raw.rsplit("\n> ", 1)[1]
            text = tail.split("\n", 1)[1].split("[ Prompt:", 1)[0].strip() if "\n" in tail else ""
        text = text.replace("OPENMAGIA_RESULT_BEGIN", "").replace("OPENMAGIA_RESULT_END", "").strip()
        # Small local models often follow the requested beats but omit or alter
        # punctuation around timestamps. Canonicalize those labels rather than
        # allowing timing drift into the generation prompt.
        for i in range(cuts):
            exact = f"CUT {i+1:02d} | {seconds*i/cuts:.2f}-{seconds*(i+1)/cuts:.2f}s -"
            text = re.sub(rf"CUT\s+0?{i+1}(?:\s*\|\s*\d+(?:\.\d+)?\s*-\s*\d+(?:\.\d+)?s?)?\s*[:\-]?", exact, text,
                          count=1, flags=re.IGNORECASE)
        forbidden = ("Loading model", "available commands", "Rewrite the user's", "build :", "model :", "llama", "subject_definitions:", "retention_analysis:",
                     "intermediate beat", "clear payoff", "establish the location", "choose an appropriate setting", "**INT.", "**EXT.")
        has_exact_cuts = all(f"CUT {i+1:02d} | {seconds*i/cuts:.2f}-{seconds*(i+1)/cuts:.2f}s" in text for i in range(cuts))
        if run.returncode == 0 and 40 <= len(text) <= 5000 and has_exact_cuts and not any(x.lower() in text.lower() for x in forbidden):
            return text, True
    except (OSError, subprocess.TimeoutExpired):
        pass
    return fallback, False


def audit_storyboard_continuity(payload, project, use_model=True):
    """Review scene-to-scene state without silently rewriting user prompts.

    The result is advisory: the small local text model can find contradictions
    in authored text, but it cannot verify what is visible in generated pixels.
    """
    scenes = list(payload.get("scenes") or [])
    style = str((payload.get("style_profile") or {}).get("prompt") or "").strip()
    issues = []
    for index, scene in enumerate(scenes):
        skill_compilation = None
        try:
            skill_compilation = compile_skill_contract(scene.get("prompt_skill_id"))
        except ValueError as exc:
            issues.append({"scene_index": index, "severity": "block", "category": "skill",
                           "title": "Selected skill is unavailable", "detail": str(exc),
                           "fact": "Choose a valid skill or repair its bundled contract"})
        cast_ids = list(scene.get("character_ids") or [])
        cast_names = [str(c.get("name") or c.get("id")) for c in project.get("characters", [])
                      if c.get("id") in cast_ids]
        if index and scene.get("continue_previous", True) is not False:
            current_text = str(scene.get("prompt") or "")
            prior_text = " ".join(str(x.get("prompt") or "") for x in scenes[:index]).lower()
            # Words such as "same" and "still" assert provenance. Catch those
            # claims deterministically because a small language model can miss
            # even an obvious newly-materialized prop.
            asserted = re.findall(
                r"\b(?:the\s+same|same|still\s+(?:holds?|carries?|wears?|has)|continues?\s+(?:with|holding|carrying))\s+(?:the\s+|a\s+|an\s+|one\s+)?([a-z][a-z0-9-]*(?:\s+[a-z][a-z0-9-]*){0,2})",
                current_text.lower())
            stop_words = {"scene", "shot", "camera", "motion", "direction", "character", "person", "people"}
            for phrase in asserted:
                words = [w for w in phrase.split() if len(w) > 2 and w not in stop_words]
                if not words or any(w in prior_text for w in words):
                    continue
                title = "Element is called persistent before it is established"
                if any(x["scene_index"] == index and x["title"] == title for x in issues):
                    continue
                issues.append({"scene_index": index, "severity": "block", "category": "prop",
                               "title": title,
                               "detail": f"“{phrase.strip()}” is described as same or continuing, but no earlier scene establishes it. Show its source and acquisition, or remove it.",
                               "fact": f"Provenance of {phrase.strip()}"})
            previous_ids = set(scenes[index - 1].get("character_ids") or [])
            current_ids = set(cast_ids)
            added = current_ids - previous_ids
            removed = previous_ids - current_ids
            if added or removed:
                names = {c.get("id"): c.get("name") for c in project.get("characters", [])}
                detail = []
                if added:
                    detail.append("added " + ", ".join(names.get(x, x) for x in sorted(added)))
                if removed:
                    detail.append("removed " + ", ".join(names.get(x, x) for x in sorted(removed)))
                issues.append({"scene_index": index, "severity": "warning", "category": "cast",
                               "title": "Cast changes across a continued shot",
                               "detail": ("; ".join(detail)[:1].upper() + "; ".join(detail)[1:]) + ". Show the entrance or exit, or restore the prior cast.",
                               "fact": "Cast change: " + "; ".join(detail)})
        scene["_audit_cast_names"] = cast_names
        scene["_audit_skill"] = ({"id": skill_compilation["id"],
                                   "version": skill_compilation["version"],
                                   "required": skill_compilation["validators"]["required"],
                                   "forbidden": skill_compilation["validators"]["forbidden"]}
                                  if skill_compilation else None)

    used_ai = False
    has_continued_transition = any(
        index and scene.get("continue_previous", True) is not False
        for index, scene in enumerate(scenes)
    )
    if use_model and has_continued_transition and formatter_available():
        evidence = []
        for index, scene in enumerate(scenes):
            evidence.append({
                "scene": index + 1,
                "name": scene.get("name") or f"Scene {index + 1}",
                "continues_previous": bool(index and scene.get("continue_previous", True) is not False),
                "continuity_mode": scene.get("continuity_mode") or ("frame" if index else "new"),
                "cast": scene.get("_audit_cast_names") or [],
                "skill_contract": scene.get("_audit_skill"),
                "prompt": str(scene.get("prompt") or "")[:7000],
            })
        instruction = (
            "Audit this MiniMax H3 storyboard for continuity. Compare only adjacent scenes. Find concrete text-level risks: "
            "a person, animal, prop, food, vehicle part, wardrobe item, HUD value, visible text, location, weather state, "
            "passenger count, seating position, screen direction, or action state that appears without an entrance/acquisition; "
            "something called 'same', 'still', or 'continued' that was not established; an established element that disappears; "
            "or conflicting identity authorities. Project style may contain stable design facts, but it must not make a scene-local "
            "prop retroactively present. Do not flag ordinary new background scenery at a motivated location change. "
            "For every issue, identify one exact named element and quote or closely paraphrase the conflicting evidence from both adjacent scene prompts. "
            "Do not return headings, placeholders, category lists, generic advice, or an issue that cannot be supported by both prompts. "
            "Return one JSON object with an issues array. Every issue object must contain scene_index as an integer; severity as warning or block; category as cast, prop, vehicle, spatial, text, world, or identity; and nonempty element, previous_evidence, current_evidence, and fix strings grounded only in the supplied scenes. "
            "scene_index is zero-based and normally points to the later scene. Never rewrite the prompts. If no issue exists return {\"issues\":[]}.\n"
            "PROJECT STYLE:\n" + style[:9000] + "\nSCENES:\n" + json.dumps(evidence, ensure_ascii=False)
        )
        cmd = [FORMATTER_BIN, "-m", FORMATTER_MODEL, "-p", instruction, "-n", "900", "--temp", "0",
               "--seed", "0", "--no-display-prompt", "--log-disable", "--single-turn", "--simple-io"]
        try:
            run = run_formatter_command(cmd, timeout=90)
            raw = re.sub(r"\x1b\[[0-9;?]*[ -/]*[@-~]", "", run.stdout)
            parsed = _last_json_object(raw)
            for item in parsed.get("issues") or []:
                idx = item.get("scene_index")
                if not isinstance(idx, int) or idx < 0 or idx >= len(scenes):
                    continue
                # A small formatter can echo the illustrative JSON schema.
                # Schema placeholders are not continuity evidence.
                allowed_categories = {"cast", "prop", "vehicle", "spatial", "text", "world", "identity"}
                category = str(item.get("category") or "").strip().lower()
                element = str(item.get("element") or "").strip()
                previous_evidence = str(item.get("previous_evidence") or "").strip()
                current_evidence = str(item.get("current_evidence") or "").strip()
                fix = str(item.get("fix") or "").strip()
                placeholders = {
                    "short", "specific evidence and correction", "concise state to confirm",
                    "warning|block", "cast|prop|vehicle|spatial|text|world|identity",
                    "the exact element", "previous scene evidence", "current scene evidence",
                    "specific edit", "element", "previous_evidence", "current_evidence", "fix",
                }
                evidence_fields = (element, previous_evidence, current_evidence, fix)
                if (category not in allowed_categories
                        or any(not value or value.lower() in placeholders for value in evidence_fields)
                        or len(element) < 3 or len(previous_evidence) < 8
                        or len(current_evidence) < 8 or len(fix) < 8):
                    continue
                if idx == 0 or scenes[idx].get("continue_previous", True) is False:
                    continue
                title = f"{element[:1].upper() + element[1:]} changes without an explained transition"
                detail = (f"Previous scene: {previous_evidence}. This scene: {current_evidence}. "
                          f"Fix: {fix}.")
                clean = {"scene_index": idx,
                         "severity": "block" if item.get("severity") == "block" else "warning",
                         "category": category,
                         "title": title[:160],
                         "detail": detail[:1000],
                         "fact": f"{element}: {previous_evidence} -> {current_evidence}"[:500]}
                if not any(x["scene_index"] == idx and x["title"] == clean["title"] for x in issues):
                    issues.append(clean)
            used_ai = run.returncode == 0
        except (OSError, subprocess.TimeoutExpired, ValueError, json.JSONDecodeError):
            pass
    for scene in scenes:
        scene.pop("_audit_cast_names", None)
        scene.pop("_audit_skill", None)
    issues.sort(key=lambda x: (x["scene_index"], x["severity"] != "block", x["category"]))
    return {"issues": issues, "used_ai": used_ai,
            "limitations": "This audit compares prompt text and metadata. Confirm the prior rendered frame visually before continuing."}


def improve_style_locally(idea, answers, use_model=True):
    """Refine reusable project art direction without inventing scene events."""
    defaults = {"medium": "premium cinematic finish", "palette": "a controlled coherent palette with motivated lighting",
                "camera": "consistent lens character and motivated movement", "graphics": "graphics and typography only when appropriate",
                "invariants": "preserve exact identity, proportions, wardrobe, materials, accessories, product geometry, labels, and colors"}
    resolved = {k: str(answers.get(k) or v) for k, v in defaults.items()}
    fallback = (f"{idea.strip()} Medium and finish: {resolved['medium']}. Palette and lighting: {resolved['palette']}. "
                f"Camera language: {resolved['camera']}. Graphics and typography: {resolved['graphics']}. "
                f"Continuity rules: {resolved['invariants']}. Apply these rules consistently to every scene; do not invent actions, locations, dialogue, visible text, or shot timing.")
    if not use_model or not (formatter_available()):
        return fallback, False
    instruction = ("Rewrite this as a detailed reusable PROJECT STYLE specification for MiniMax H3. Describe only stable visual identity: medium, finish, palette, lighting, materials, lens and camera language, graphics, typography, and continuity invariants. "
                   "Do not create a scene, story, action, location, dialogue, timing, cuts, or visible copy. Preserve every user constraint. Return one production-ready paragraph between OPENMAGIA_RESULT_BEGIN and OPENMAGIA_RESULT_END. "
                   f"Direction: {idea}. Requirements: {json.dumps(resolved, ensure_ascii=False)}")
    cmd = [FORMATTER_BIN, "-m", FORMATTER_MODEL, "-p", instruction, "-n", "320", "--temp", "0.2", "--no-display-prompt", "--log-disable", "--single-turn", "--simple-io"]
    try:
        run = run_formatter_command(cmd, timeout=60)
        raw = re.sub(r"\x1b\[[0-9;?]*[ -/]*[@-~]", "", run.stdout)
        start = raw.rfind("OPENMAGIA_RESULT_BEGIN")
        end = raw.find("OPENMAGIA_RESULT_END", start + len("OPENMAGIA_RESULT_BEGIN")) if start >= 0 else -1
        if start >= 0 and end > start:
            text = raw[start + len("OPENMAGIA_RESULT_BEGIN"):end].strip()
        else:
            text = ""
        text = text.replace("OPENMAGIA_RESULT_BEGIN", "").replace("OPENMAGIA_RESULT_END", "").strip()
        if run.returncode == 0 and 40 <= len(text) <= 6000 and not any(x.lower() in text.lower() for x in ("Loading model", "available commands", "build :", "model :", "llama")):
            return text, True
    except (OSError, subprocess.TimeoutExpired):
        pass
    return fallback, False


def improve_image_locally(idea, answers, project_style=""):
    """Expand a still-image brief without leaking video timing or motion grammar."""
    still_keys = {"setting", "camera", "text", "skill_instruction"}
    context = "; ".join(f"{k}: {v}" for k, v in answers.items() if k in still_keys and v)
    fallback = (f"{idea.strip()} Still-image direction: {context}. " if context else idea.strip() + " ") + \
               "Create one decisive frozen composition with stable identity, precise materials, coherent lighting, and no temporal progression."
    if not (formatter_available()):
        return fallback.strip(), False
    instruction = (("Use this established project style as authority without restating it: " + project_style + " " if project_style else "") +
                   "Expand the request into one concise production-ready still-image description. Specify subject, frozen pose or moment, composition, lens/viewpoint, environment, lighting, palette, materials, and exact visible text when supplied. Preserve all names and facts. Do not write cuts, timestamps, transitions, motion sequences, sound, music, analysis, headings, or H3 field labels. Keep it below 140 words. Return only the description between OPENMAGIA_RESULT_BEGIN and OPENMAGIA_RESULT_END. "
                   f"Direction: {context or 'make strong still-image choices'}. Request: {idea}")
    cmd = [FORMATTER_BIN, "-m", FORMATTER_MODEL, "-p", instruction, "-n", "240", "--temp", "0.25",
           "--no-display-prompt", "--log-disable", "--single-turn", "--simple-io"]
    try:
        run = run_formatter_command(cmd, timeout=60)
        raw = re.sub(r"\x1b\[[0-9;?]*[ -/]*[@-~]", "", run.stdout)
        start, end = raw.rfind("OPENMAGIA_RESULT_BEGIN"), raw.rfind("OPENMAGIA_RESULT_END")
        text = raw[start + len("OPENMAGIA_RESULT_BEGIN"):(end if end > start else len(raw))].split("[ Prompt:", 1)[0].strip() if start >= 0 else ""
        forbidden = ("cut 01", "overall_soundscape", "non_diegetic_music", "loading model", "available commands")
        if run.returncode == 0 and 40 <= len(text) <= 4000 and not any(x in text.lower() for x in forbidden):
            return text, True
    except (OSError, subprocess.TimeoutExpired):
        pass
    return fallback.strip(), False


def continuity_evidence(project, media_id=None, new_prompt="", new_answers=None):
    """Return deterministic, provenance-rich evidence for a reusable continuity profile."""
    media = next((m for m in project.get("media", []) if m.get("id") == media_id), None)
    anchor_scene = next((s for s in project.get("scenes", [])
                         if s.get("mediaId") == media_id or s.get("media") == media_id
                         or (media and s.get("id") == media.get("scene_id"))), None)
    by_id = {s.get("id"): s for s in project.get("scenes", [])}
    ordered = [by_id[sid] for sid in project.get("order", []) if sid in by_id]
    ordered.extend(s for s in project.get("scenes", []) if s not in ordered)
    lines = [f"PROJECT: {project.get('name') or 'Untitled'}"]
    active = project.get("style_profile") or {}
    # Never feed a previous generated bible back into its own update: recursive
    # generic wording can crowd concrete provenance out of a small model's context.
    if active.get("prompt") and active.get("source") != "continuity":
        lines.append(f"CURRENT STYLE: {active['prompt']}")
    elif active.get("source") == "continuity" and active.get("skill_id"):
        previous = next((s for s in project.get("project_style_skills", []) if s.get("id") == active.get("skill_id")), None)
        if previous and previous.get("knowledge_updates"):
            lines.append("PRIOR USER-APPROVED STYLE KNOWLEDGE: " + json.dumps(previous["knowledge_updates"], ensure_ascii=False, sort_keys=True))
    if media:
        generation = media.get("generation") or {}
        lines.append("SELECTED MEDIA: " + json.dumps({
            "id": media.get("id"), "name": media.get("name"), "kind": media.get("kind"),
            "dimensions": [media.get("w"), media.get("h")], "duration": media.get("duration"),
            "source": media.get("source"), "scene_id": media.get("scene_id"),
            "generation_prompt": generation.get("prompt"), "generation_params": generation.get("params"),
        }, ensure_ascii=False, sort_keys=True))
    characters = []
    for c in project.get("characters", []):
        characters.append({"id": c.get("id"), "name": c.get("name"),
                           "description": c.get("description", ""),
                           "reference_media_ids": character_image_ids(c)})
    lines.append("CHARACTER LOCKS: " + json.dumps(characters, ensure_ascii=False, sort_keys=True))
    for index, scene in enumerate(ordered, 1):
        if scene.get("status") not in ("ready", "running", "queued", "idle"):
            continue
        refs = [next((m.get("name") for m in project.get("media", []) if m.get("id") == mid), mid)
                for mid in scene.get("reference_media_ids", [])]
        record = {
            "sequence": index, "anchor": bool(anchor_scene and scene.get("id") == anchor_scene.get("id")),
            "name": scene.get("name"), "prompt": scene.get("prompt"),
            "characters": scene.get("character_ids", []), "visual_references": refs,
            "style_at_generation": (scene.get("style_profile") or {}).get("prompt", ""),
            "guide_answers": scene.get("guide_answers", {}), "params": scene.get("params", {}),
        }
        lines.append("SCENE EVIDENCE: " + json.dumps(record, ensure_ascii=False, sort_keys=True))
    if str(new_prompt or "").strip():
        lines.append("NEW REFINEMENT KNOWLEDGE (extract stable facts only; the action remains scene-specific): " + json.dumps({
            "prompt": str(new_prompt).strip(), "answers": dict(new_answers or {})
        }, ensure_ascii=False, sort_keys=True))
    return "\n".join(lines)


def continuity_text_locks(project):
    """Collect only explicitly requested visible copy, preserving it verbatim."""
    locks = []
    ignored = ("no visible text", "none", "no text", "unless explicitly requested")
    for scene in project.get("scenes", []):
        candidates = [str((scene.get("guide_answers") or {}).get("text") or "").strip()]
        prompt = str(scene.get("prompt") or "")
        # Recover explicit copy from older, pre-metadata prompts without
        # mistaking dialogue or arbitrary quoted prose for on-screen text.
        candidates.extend(m.group(1).strip(" \t:\"'") for m in re.finditer(
            r"(?:visible|on[- ]screen)\s+text\s*(?:reads|is|:)?\s*[\"']?([^\n.;]+)", prompt, re.IGNORECASE))
        for value in candidates:
            if value and not any(term in value.lower() for term in ignored) and value not in locks:
                locks.append(value)
    return locks


def build_continuity_style(project, media_id=None, use_model=True, new_prompt="", new_answers=None):
    """Build a stable project bible from stored generation provenance without inventing facts."""
    evidence = continuity_evidence(project, media_id, new_prompt, new_answers)
    character_parts = []
    for c in project.get("characters", []):
        description = str(c.get("description") or "Use the ordered character reference images as the identity authority.").strip()
        character_parts.append(f"{c.get('name') or 'Character'}: {description}")
    ready_scenes = [s for s in project.get("scenes", []) if s.get("status") == "ready"]
    active_profile = project.get("style_profile") or {}
    active = str(active_profile.get("prompt") or "").strip() if active_profile.get("source") != "continuity" else ""
    text_locks = continuity_text_locks(project)
    fallback = (
        "CONTINUITY BIBLE — Treat the selected opening frame and attached reference images as the highest visual authority. "
        + (f"Established project style: {active} " if active else "")
        + ("Character identity locks: " + " | ".join(character_parts) + ". " if character_parts else "")
        + ("Exact visible-text locks (preserve spelling, punctuation, capitalization and placement intent): " + " | ".join(text_locks) + ". " if text_locks else "No persistent visible copy is currently locked; do not carry incidental or generated text into later shots. ")
        + "Across every new shot preserve established character identity, anatomy, facial structure, hairstyle, wardrobe construction, accessories, materials and colors; preserve environment geometry, recurring objects, spatial relationships, time of day, weather, palette, lighting direction, lens character, camera axis, texture and rendering finish whenever established by the source frame or prior generated media. "
        + "The chosen continuation frame is the exact opening-state authority. Continue motion, screen direction, pose, eyeline, object placement, illumination and atmosphere without a visual reset. Use prior prompts only as provenance for stable facts; never repeat an old action or invent a character, prop, location, wardrobe change, text, logo or story event unless the new scene explicitly requests it. "
        + "When evidence conflicts, use this priority: selected opening frame; attached character/reference images; selected media generation metadata; latest ready scene; earlier scene history; text-only style guidance. "
        + f"Evidence coverage: {len(ready_scenes)} ready generated scene(s), {len(project.get('characters', []))} character lock(s)."
    )
    used_ai = False
    prompt = fallback
    if use_model and formatter_available():
        instruction = (
            "Create one concrete reusable CONTINUITY BIBLE for future MiniMax H3 video generations from the evidence below. Write 250-450 words. Name every supported subject, location, wardrobe item, weather or lighting condition, camera convention, sound convention, and exact visible-text lock; omit categories with no evidence. Separate stable facts from historical actions. Never turn a prior action into a future instruction. Never invent facts. State this priority: selected opening frame, attached references, selected-media metadata, latest scene, earlier history, text guidance. Do not write generic commentary about having evidence. Return one production-ready paragraph between OPENMAGIA_RESULT_BEGIN and OPENMAGIA_RESULT_END.\nEVIDENCE:\n"
            + evidence
        )
        cmd = [FORMATTER_BIN, "-m", FORMATTER_MODEL, "-p", instruction, "-n", "1100",
               "--temp", "0", "--seed", "0", "--no-display-prompt", "--log-disable",
               "--single-turn", "--simple-io"]
        try:
            run = run_formatter_command(cmd, timeout=90)
            raw = re.sub(r"\x1b\[[0-9;?]*[ -/]*[@-~]", "", run.stdout)
            start, end = raw.rfind("OPENMAGIA_RESULT_BEGIN"), raw.rfind("OPENMAGIA_RESULT_END")
            candidate = raw[start + len("OPENMAGIA_RESULT_BEGIN"):(end if end > start else len(raw))].strip() if start >= 0 else ""
            # Small models can append runtime statistics or start retelling the
            # numbered scenes despite the instruction. Keep the stable-fact
            # section and categorically prevent historical actions becoming
            # future generation instructions.
            candidate = re.split(r"\n\s*\d+\.\s+\*\*Scene\b", candidate, maxsplit=1, flags=re.IGNORECASE)[0]
            candidate = re.split(r"\n\s*\[\s*Prompt:", candidate, maxsplit=1, flags=re.IGNORECASE)[0].strip()
            if candidate:
                candidate = (
                    "CONTINUITY AUTHORITY — Resolve conflicts in this order: selected opening frame; attached character and visual references; selected-media generation metadata; latest completed scene; earlier scene history; text-only guidance. "
                    "The scene history below was used only to extract stable facts; never repeat its actions unless the new prompt explicitly asks for them. "
                    + candidate
                    + (" Exact visible-text locks: " + " | ".join(text_locks) + ". Preserve spelling, punctuation and capitalization exactly." if text_locks else " No persistent visible copy is locked; never preserve incidental or malformed generated text.")
                )
            forbidden = ("loading model", "available commands", "build :", "model :", "llama")
            if run.returncode == 0 and 180 <= len(candidate) <= 10000 and not any(x in candidate.lower() for x in forbidden):
                prompt, used_ai = candidate, True
        except (OSError, subprocess.TimeoutExpired):
            pass
    profile_id = "continuity-" + slugify(project.get("slug") or project.get("name") or "project")
    digest = hashlib.sha256(evidence.encode("utf-8")).hexdigest()[:16]
    anchor_media = next((m for m in project.get("media", []) if m.get("id") == media_id), None)
    project_name = project.get("name") or "Untitled"
    previous_profile = next((s for s in project.get("project_style_skills", []) if s.get("id") == profile_id), None) or {}
    knowledge_updates = list(previous_profile.get("knowledge_updates") or [])
    if str(new_prompt or "").strip():
        knowledge_updates.append({"prompt": str(new_prompt).strip(), "answers": dict(new_answers or {}), "added": time.time()})
    profile = {
        "id": profile_id, "name": f"Continuity · {project_name}",
        "description": f"Created for {project_name} from {len(ready_scenes)} generated scene(s), {len(project.get('characters', []))} character lock(s), and the selected continuity frame.",
        "prompt": prompt, "skill_id": profile_id, "source": "continuity",
        "type": "video", "scope": "project-style", "custom": True,
        "updated": time.time(), "evidence_hash": digest,
        "project_name": project_name, "project_slug": project.get("slug"),
        "scene_count": len(ready_scenes), "character_count": len(project.get("characters", [])),
        "anchor_media_id": media_id, "anchor_media_name": (anchor_media or {}).get("name"),
        "source_media_ids": [media_id] if media_id else [],
        "visible_text_locks": text_locks,
        "knowledge_updates": knowledge_updates,
        "evidence_snapshot": evidence,
    }
    return profile, used_ai


def compose_custom_skill_locally(fields):
    """Create a reusable prompt-skill specification, with a deterministic fallback."""
    name = str(fields.get("name") or "Custom prompt skill").strip()
    purpose = str(fields.get("purpose") or "Create a production-ready generation prompt").strip()
    trigger = str(fields.get("trigger") or "Use when the requested outcome matches this specialty.").strip()
    inputs = str(fields.get("inputs") or "The user brief and any supplied references.").strip()
    workflow = str(fields.get("workflow") or "Resolve missing details, plan the result in order, then validate it.").strip()
    constraints = str(fields.get("constraints") or "Preserve user facts, exact text, identity, and reference roles; do not invent unsupported claims.").strip()
    output = str(fields.get("output") or "A concise, generation-ready prompt plus a final validation pass.").strip()
    slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")[:48] or "custom-skill"
    description = purpose.rstrip(".") + "."
    fallback = (f"---\nname: openmagia-{slug}\ndescription: {description}\n---\n\n# {name}\n\n"
                f"Use this skill when: {trigger}\n\n## Intake\n\nGather: {inputs}\n\n## Workflow\n\n{workflow}\n\n"
                f"## Constraints\n\n{constraints}\n\n## Output and validation\n\n{output}\n\n"
                "Keep the result compatible with OpenMagia's prompt box. Ask only questions whose answers materially change the result, preserve supplied facts verbatim, and verify the final prompt before generation.")
    steps = [x.strip(" -•\t") for x in re.split(r"[\n;]+", workflow) if x.strip()][:4]
    if not steps:
        steps = ["Resolve the brief", "Plan the result", "Compose the prompt", "Validate constraints"]
    if not (formatter_available()):
        return slug, description, fallback, steps, False
    instruction = ("Write one reusable OpenMagia prompt-skill specification in Markdown. Match this structure exactly: YAML front matter with name and description; H1 title; sections for When to use, Intake, Workflow, Constraints, and Output and validation. "
                   "This is a prompt-box skill, never a persistent project style. Ask only material questions. Preserve user facts and forbid unsupported invention. Return only the specification between OPENMAGIA_RESULT_BEGIN and OPENMAGIA_RESULT_END. "
                   f"Brief: {json.dumps({'name':name,'purpose':purpose,'trigger':trigger,'inputs':inputs,'workflow':workflow,'constraints':constraints,'output':output}, ensure_ascii=False)}")
    cmd = [FORMATTER_BIN, "-m", FORMATTER_MODEL, "-p", instruction, "-n", "650", "--temp", "0.25", "--no-display-prompt", "--log-disable", "--single-turn", "--simple-io"]
    try:
        run = run_formatter_command(cmd, timeout=75)
        raw = re.sub(r"\x1b\[[0-9;?]*[ -/]*[@-~]", "", run.stdout)
        start, end = raw.rfind("OPENMAGIA_RESULT_BEGIN"), raw.rfind("OPENMAGIA_RESULT_END")
        text = raw[start + len("OPENMAGIA_RESULT_BEGIN"):end].strip() if start >= 0 and end > start else ""
        if run.returncode == 0 and 180 <= len(text) <= 10000 and "# " in text and not any(x.lower() in text.lower() for x in ("loading model", "available commands", "build :", "model :")):
            return slug, description, text, steps, True
    except (OSError, subprocess.TimeoutExpired):
        pass
    return slug, description, fallback, steps, False


def enrich_character_locally(name, description):
    seed = (description or "").strip()
    fallback = (seed + " " if seed else "") + "Preserve the character’s exact visible identity from the ordered reference images: face, proportions, hairstyle, wardrobe construction, materials, accessories, and colors. Treat pose, framing, lighting, and background as non-binding unless explicitly requested."
    if not (formatter_available()):
        return fallback.strip(), False
    instruction = ("Rewrite the supplied notes as a compact MiniMax H3 CHARACTER IDENTITY specification. Preserve all supplied facts. Add only continuity rules; never infer unseen traits or claim to inspect images. Do not add story, action, camera, setting, or timing. Return one paragraph between OPENMAGIA_RESULT_BEGIN and OPENMAGIA_RESULT_END. "
                   f"Character: {name}. Notes: {seed or 'No written notes; rely on ordered reference images.'}")
    cmd = [FORMATTER_BIN, "-m", FORMATTER_MODEL, "-p", instruction, "-n", "220", "--temp", "0.2", "--no-display-prompt", "--log-disable", "--single-turn", "--simple-io"]
    try:
        run = run_formatter_command(cmd, timeout=60)
        raw = re.sub(r"\x1b\[[0-9;?]*[ -/]*[@-~]", "", run.stdout)
        start, end = raw.rfind("OPENMAGIA_RESULT_BEGIN"), raw.rfind("OPENMAGIA_RESULT_END")
        text = raw[start + len("OPENMAGIA_RESULT_BEGIN"):end].strip() if start >= 0 and end > start else ""
        if run.returncode == 0 and 30 <= len(text) <= 3000 and not any(x.lower() in text.lower() for x in ("loading model", "available commands", "build :", "model :")):
            return text, True
    except (OSError, subprocess.TimeoutExpired):
        pass
    return fallback.strip(), False


def add_media(project, src, name, kind, source):
    p = Path(src)
    info = nle.probe(p)
    # store a path relative to the project folder so the project is portable
    base = proj_dir(project["slug"])
    try:
        rel = str(p.resolve().relative_to(base.resolve()))
    except ValueError:
        rel = str(Path("media") / p.name)
    m = {"id": uuid.uuid4().hex[:10], "asset_uid": uuid.uuid4().hex[:16], "src": rel, "name": name,
         "kind": kind, "source": source, "duration": info["duration"],
         "w": info["w"], "h": info["h"], "hasAudio": info["hasAudio"]}
    project["media"].append(m)
    return m


def analyze_sheet_motion(video, recipe_id="turn-6", sample_fps=8):
    """Return adaptive extraction times and cheap temporal-stability diagnostics.

    H3 does not always complete a requested camera orbit at precisely the same
    instant.  Sampling tiny grayscale frames is inexpensive, requires no ML
    model, and avoids assigning semantic view labels from one brittle timestamp.
    """
    fallback = dict((label, at) for at, label in sheet_extract_times(recipe_id))
    if recipe_id != "turn-6":
        return fallback, {"adaptive": False, "reason": "recipe uses fixed checkpoints"}
    frame_size = 64 * 64
    try:
        run = subprocess.run(
            ["ffmpeg", "-v", "error", "-i", str(video), "-vf",
             f"fps={sample_fps},scale=64:64,format=gray", "-f", "rawvideo", "-"],
            capture_output=True, timeout=30)
        if run.returncode or len(run.stdout) < frame_size * 8:
            raise RuntimeError("insufficient motion samples")
        samples = [run.stdout[i:i + frame_size]
                   for i in range(0, len(run.stdout) - frame_size + 1, frame_size)]

        def mae(a, b):
            return sum(abs(x - y) for x, y in zip(a, b)) / frame_size

        # The prompt defines explicit semantic checkpoints over four seconds.
        # Do not infer orientation from pixel similarity: symmetric clothing,
        # coats, masks, and backs can look more like the first frame than the
        # actual returning front view and would silently swap view labels.
        orbit_end = 4.0

        targets = {
            "front": 0.05,
            "three-quarter": orbit_end / 12.0,
            "left side": orbit_end / 4.0,
            "back": orbit_end / 2.0,
            "right side": orbit_end * 3.0 / 4.0,
            "front face": min((len(samples) - 1) / sample_fps, 4.82),
        }

        # Within a narrow semantic window choose the frame with the quietest
        # neighbours. This avoids extracting on a one-frame full-image blink.
        chosen = {}
        stability = {}
        radius = max(1, round(sample_fps * 0.18))
        for label, target in targets.items():
            if label in ("front", "three-quarter"):
                chosen[label] = target
                stability[label] = round(mae(samples[max(0, round(target * sample_fps) - 1)],
                                             samples[min(len(samples) - 1, round(target * sample_fps) + 1)]) / 2.0, 2)
                continue
            center = max(1, min(len(samples) - 2, round(target * sample_fps)))
            window = range(max(1, center - radius), min(len(samples) - 2, center + radius) + 1)
            best = min(window, key=lambda i: mae(samples[i - 1], samples[i]) + mae(samples[i], samples[i + 1]))
            chosen[label] = best / sample_fps
            stability[label] = round((mae(samples[best - 1], samples[best]) +
                                      mae(samples[best], samples[best + 1])) / 2.0, 2)

        luminance = [sum(frame) / frame_size for frame in samples]
        largest_jump = max((abs(b - a) for a, b in zip(luminance, luminance[1:])), default=0.0)
        diagnostics = {"adaptive": True, "sampleFps": sample_fps,
                       "orbitEnd": round(orbit_end, 3),
                       "stability": stability,
                       "maxLuminanceJump": round(largest_jump, 2),
                       "unstable": largest_jump >= 18 or max(stability.values(), default=0) >= 24}
        return chosen, diagnostics
    except (OSError, subprocess.SubprocessError, RuntimeError, ValueError) as exc:
        return fallback, {"adaptive": False, "reason": str(exc)}


def sheet_extract_plan(video, recipe_id="turn-6"):
    times, diagnostics = analyze_sheet_motion(video, recipe_id)
    ordered = [(times.get(label, at), label) for at, label in sheet_extract_times(recipe_id)]
    return ordered, diagnostics


def repair_completed_sheets(project):
    """Adopt complete on-disk sheet outputs left behind by a stale state write."""
    changed = False
    media = project.setdefault("media", [])
    by_src = {str(m.get("src") or "").lstrip("/"): m for m in media}
    for sheet in project.get("sheets", []):
        if sheet.get("status") not in ("queued", "running") or sheet.get("id") == sheet_active:
            continue
        sheet_id = sheet["id"]
        video = proj_media_dir(project) / f"sheet-{sheet_id}.mp4"
        extracts = [(at, label, proj_media_dir(project) / f"sheet-{sheet_id}-{label.replace(' ', '-')}.png")
                    for at, label in sheet_extract_times(sheet.get("recipe", "turn-6"))]
        if not video.is_file() or not extracts or not all(path.is_file() for _, _, path in extracts):
            continue
        label_name = (sheet.get("name") or "Character").strip() or "Character"
        video_rel = video.relative_to(proj_dir(project["slug"])).as_posix()
        video_media = by_src.get(video_rel)
        if video_media is None:
            video_media = add_media(project, video, f"{label_name} · spin", "video", "generated")
            by_src[video_rel] = video_media
        video_media.update({"status": "ready", "hasAudio": False})
        thumb = proj_media_dir(project) / "thumbs" / f"sheet-{sheet_id}.jpg"
        if thumb.is_file():
            video_media["thumb"] = "/media/thumbs/" + thumb.name
        frames = []
        for at, frame_label, path in extracts:
            rel = path.relative_to(proj_dir(project["slug"])).as_posix()
            frame_media = by_src.get(rel)
            if frame_media is None:
                frame_media = add_media(project, path, f"{label_name} · {frame_label}", "image", "sheet")
                by_src[rel] = frame_media
            frames.append({"mediaId": frame_media["id"], "label": frame_label, "time": at})
        sheet["videoMediaId"] = video_media["id"]
        sheet["frames"] = frames
        sheet["status"] = "ready"
        sheet["error"] = None
        sheet["extractionVersion"] = SHEET_EXTRACTION_VERSION
        changed = True
    return changed


def upgrade_sheet_extractions(project):
    """Upgrade legacy ready sheets from their retained spin video exactly once.

    View images may have been moved into folders, so the migration resolves
    every target through its media record rather than assuming root filenames.
    Replacements are atomic and the source turnaround remains untouched.
    """
    changed = False
    media_by_id = {m.get("id"): m for m in project.get("media", [])}
    for sheet in project.get("sheets", []):
        if sheet.get("status") != "ready" or int(sheet.get("extractionVersion") or 0) >= SHEET_EXTRACTION_VERSION:
            continue
        video_media = media_by_id.get(sheet.get("videoMediaId"))
        video = abs_media(project, video_media) if video_media else None
        if not video or not video.is_file():
            continue
        plan, diagnostics = sheet_extract_plan(video, sheet.get("recipe", "turn-6"))
        frames_by_label = {frame.get("label"): frame for frame in sheet.get("frames", [])}
        replacements = []
        try:
            for at, label in plan:
                frame = frames_by_label.get(label)
                frame_media = media_by_id.get(frame.get("mediaId")) if frame else None
                target = abs_media(project, frame_media) if frame_media else None
                if not target or not target.parent.is_dir():
                    raise FileNotFoundError(f"missing stored {label} view")
                temp = target.with_name(target.stem + f"-extract-v{SHEET_EXTRACTION_VERSION}" + target.suffix)
                if not nle.extract_frame(video, temp, at):
                    raise RuntimeError(f"could not extract {label} view")
                replacements.append((temp, target, frame, at))
            for temp, target, frame, at in replacements:
                temp.replace(target)
                frame["time"] = at
            sheet["sheetDiagnostics"] = diagnostics
            sheet["extractionVersion"] = SHEET_EXTRACTION_VERSION
            sheet.pop("extractionUpgradeError", None)
            changed = True
        except Exception as exc:
            for temp, _, _, _ in replacements:
                try:
                    temp.unlink(missing_ok=True)
                except OSError:
                    pass
            # Record the failure so it is inspectable, but leave the version
            # untouched so a repaired/moved source can be upgraded later.
            message = str(exc)
            if sheet.get("extractionUpgradeError") != message:
                sheet["extractionUpgradeError"] = message
                changed = True
    return changed


def base_track(project):
    return next(t for t in project["tracks"] if t["id"] == BASE_TRACK)


def timeline_end(project, track_id=None):
    end = 0.0
    for t in project["tracks"]:
        if track_id and t["id"] != track_id:
            continue
        for c in t["clips"]:
            end = max(end, c["start"] + (c["out"] - c["in"]))
    return end


def clip_duration(clip):
    return max(0.05, float(clip.get("out", 0)) - float(clip.get("in", 0)))


def clip_ranges_overlap(start, duration, other):
    other_start = max(0.0, float(other.get("start", 0)))
    other_end = other_start + clip_duration(other)
    return start < other_end - 1e-6 and start + duration > other_start + 1e-6


def resolve_clip_start(track, desired, duration, exclude_id=None):
    """Nearest legal start that cannot overlap another clip on this track."""
    desired = max(0.0, float(desired))
    duration = max(0.05, float(duration))
    others = [c for c in track.get("clips", []) if c.get("id") != exclude_id]
    if not any(clip_ranges_overlap(desired, duration, c) for c in others):
        return desired
    candidates = {0.0}
    for other in others:
        start = max(0.0, float(other.get("start", 0)))
        candidates.add(start + clip_duration(other))
        candidates.add(max(0.0, start - duration))
    legal = [candidate for candidate in candidates
             if not any(clip_ranges_overlap(candidate, duration, c) for c in others)]
    return min(legal, key=lambda candidate: (abs(candidate - desired), candidate))


def repair_timeline_overlaps(project):
    """Sequence legacy overlapping clips while preserving their relative order."""
    changed = False
    for track in project.get("tracks", []):
        cursor = 0.0
        for clip in sorted(track.get("clips", []), key=lambda item: (float(item.get("start", 0)), item.get("id", ""))):
            start = max(0.0, float(clip.get("start", 0)))
            if start < cursor - 1e-6:
                start = cursor
                clip["start"] = round(start, 6)
                changed = True
            cursor = start + clip_duration(clip)
    return changed


def _timeline_magia_number(seed, key, modulo=10000):
    digest = hashlib.sha256(f"{int(seed)}:{key}".encode()).hexdigest()
    return int(digest[:12], 16) % modulo


timeline_magia_direction_cache = {}


def interpret_timeline_magia_direction(direction, use_ai=False):
    """Convert free prose into bounded editor choices; optionally ask the local refiner."""
    text = str(direction or "").strip()
    lower = text.lower()
    intensity = "subtle" if any(word in lower for word in ("subtle", "minimal", "gentle")) else (
        "dynamic" if any(word in lower for word in ("bold", "dynamic", "fast", "energetic")) else "balanced")
    intent = "balanced"
    if any(word in lower for word in ("sepia", "antique brown", "old photograph")):
        intent = "sepia"
    elif any(word in lower for word in ("saturated", "vibrant", "colorful", "colourful", "punchy")):
        intent = "saturated"
    elif any(word in lower for word in ("desaturated", "muted", "washed", "monochrome")):
        intent = "muted"
    elif any(word in lower for word in ("warm", "golden", "sunset", "amber")):
        intent = "warm"
    elif any(word in lower for word in ("cool", "cold", "blue", "icy", "teal")):
        intent = "cool"
    elif any(word in lower for word in ("high contrast", "contrasty", "crisp")):
        intent = "contrast"
    elif any(word in lower for word in ("bright", "airy", "luminous")):
        intent = "bright"
    elif any(word in lower for word in ("dark", "moody", "low key")):
        intent = "dark"
    note, used_ai = intent + " color treatment", False
    if not text or not use_ai or not formatter_available():
        return {"intent": intent, "intensity": intensity, "note": note, "used_ai": used_ai}
    cache_key = text.casefold()
    if cache_key in timeline_magia_direction_cache:
        return dict(timeline_magia_direction_cache[cache_key])
    instruction = (
        "Interpret one video-editing direction for OpenMagia. Return JSON only with intent, intensity, and note. "
        "intent must be exactly one of balanced, saturated, muted, warm, cool, sepia, contrast, bright, dark. "
        "intensity must be exactly subtle, balanced, or dynamic. note must be one short plain-language sentence describing the chosen grade or rhythm. "
        "Do not invent story facts or effects the user did not request. Direction: " + text[:1200])
    cmd = [FORMATTER_BIN, "-m", FORMATTER_MODEL, "-p", instruction, "-n", "120", "--temp", "0",
           "--seed", "0", "--no-display-prompt", "--log-disable", "--single-turn", "--simple-io"]
    try:
        run = run_formatter_command(cmd, timeout=45)
        result = _last_json_object(run.stdout)
        allowed_intents = {"balanced", "saturated", "muted", "warm", "cool", "sepia", "contrast", "bright", "dark"}
        allowed_intensities = {"subtle", "balanced", "dynamic"}
        if result.get("intent") in allowed_intents: intent = result["intent"]
        if result.get("intensity") in allowed_intensities: intensity = result["intensity"]
        note = str(result.get("note") or (intent + " color treatment"))[:240]
        used_ai = run.returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        pass
    value = {"intent": intent, "intensity": intensity, "note": note, "used_ai": used_ai}
    timeline_magia_direction_cache[cache_key] = dict(value)
    return value


def timeline_magia_plan(project, request=None):
    """Build an editable, export-safe effect plan from existing Inspector fields."""
    request = request or {}
    options = {"transitions": True, "transforms": True, "color": True,
               "pacing": True, "overlays": True, "audio": True,
               **(request.get("options") or {})}
    seed = int(request.get("seed") or time.time_ns() % 2147483647)
    direction = str(request.get("direction") or "").strip()
    interpreted = interpret_timeline_magia_direction(direction, bool(request.get("use_ai")))
    intensity = {"subtle": .72, "dynamic": 1.22}.get(interpreted["intensity"], 1.0)
    color_intent = interpreted["intent"]
    profile = ("Subtle polish" if intensity < 1 else ("Dynamic remix" if intensity > 1 else "Balanced edit"))
    if color_intent != "balanced" and options.get("color", True):
        profile = color_intent.capitalize() + " edit"
    tracks = project.get("tracks", [])
    media = {item.get("id"): item for item in project.get("media", [])}
    video_tracks = [track for track in tracks if track.get("kind") == "video"]
    occupied = [track for track in video_tracks if track.get("clips")]
    base = next((track for track in video_tracks if track.get("id") == BASE_TRACK), None)
    if not base or not base.get("clips"):
        base = occupied[-1] if occupied else None
    selected_id = str(request.get("selected_clip_id") or "")
    scope = "selected" if request.get("scope") == "selected" and selected_id else "timeline"
    targets = []
    for track in video_tracks:
        for clip in sorted(track.get("clips", []), key=lambda item: (float(item.get("start", 0)), item.get("id", ""))):
            if clip.get("magiaOverlay"):
                continue
            if scope == "selected" and clip.get("id") != selected_id:
                continue
            targets.append((track, clip))
    updates, counts = [], {"clips": 0, "transitions": 0, "transforms": 0, "color": 0,
                           "trimmed": 0, "overlays": 0, "audio": 0}
    transition_types = ["dissolve", "fade", "wipe", "slide"]
    motion_types = ["push-in", "pull-out", "pan-left", "pan-right", "pan-up", "pan-down"]
    base_order = sorted((base or {}).get("clips", []), key=lambda item: (float(item.get("start", 0)), item.get("id", "")))
    base_index = {clip.get("id"): index for index, clip in enumerate(base_order)}
    def transition_recipe(clip):
        clip_id = clip.get("id")
        token = _timeline_magia_number(seed, clip_id)
        kind = transition_types[token % len(transition_types)]
        value = round(min(clip_duration(clip) * .2, (.28 + (token % 29) / 100.0) * intensity), 3)
        return kind, value
    can_retime_base = scope == "timeline" and bool(base_order) and options.get("pacing", True)
    retimed = {}
    if can_retime_base:
        cursor = max(0.0, float(base_order[0].get("start", 0)))
        for index, clip in enumerate(base_order):
            duration = clip_duration(clip)
            token = _timeline_magia_number(seed, f"trim:{clip.get('id')}")
            trim = 0.0 if duration < 2.2 else min(duration - 1.0, (0.08 + (token % 17) / 100.0) * intensity)
            trim = max(0.0, trim)
            retimed[clip.get("id")] = {"start": round(cursor, 6), "out": round(float(clip.get("out", 0)) - trim, 6), "trim": trim}
            cursor += max(0.05, duration - trim)
    for track, clip in targets:
        clip_id = clip.get("id")
        token = _timeline_magia_number(seed, clip_id)
        duration = clip_duration(clip)
        item = media.get(clip.get("mediaId"), {})
        fields, labels = {}, []
        is_overlay = bool(base and track.get("id") != base.get("id"))
        if clip_id in retimed:
            timing = retimed[clip_id]
            fields.update(start=timing["start"], out=timing["out"])
            if timing["trim"] > 0.001:
                labels.append(f"trim {timing['trim']:.2f}s")
                counts["trimmed"] += 1
        if options.get("transitions", True):
            transition_items = []
            if is_overlay:
                fade = round(min(duration * .22, (.24 + (token % 24) / 100.0) * intensity), 3)
                transition_items = [
                    {"id": f"magia-in-{seed}-{clip_id}", "type": "fade", "edge": "start", "dur": fade, "enabled": True},
                    {"id": f"magia-out-{seed}-{clip_id}", "type": "fade", "edge": "end", "dur": fade, "enabled": True}]
            elif base_index.get(clip_id, 0) > 0:
                kind, duration_value = transition_recipe(clip)
                transition_items = [{"id": f"magia-{seed}-{clip_id}", "type": kind, "edge": "start",
                                     "dur": duration_value, "enabled": True}]
            if transition_items:
                fields["transition"] = {"items": transition_items}
                labels.append(" + ".join(f"{item['type']} {item['dur']:.2f}s" for item in transition_items))
                counts["transitions"] += len(transition_items)
        if is_overlay and options.get("overlays", True):
            corners = [(-27, -24), (27, -24), (-27, 24), (27, 24)]
            x, y = corners[token % len(corners)]
            fields.update(zoom=round((.68 + (token % 19) / 100.0) * min(1.0, intensity), 3),
                          position={"x": x, "y": y},
                          mask={"enabled": True, "type": "rectangle", "x": 50, "y": 50,
                                "width": 92, "height": 92, "invert": False})
            labels.append("overlay layout")
            counts["overlays"] += 1
        elif options.get("transforms", True):
            index = base_index.get(clip_id, 0)
            incoming = transition_recipe(clip)[1] / duration if index > 0 else 0
            outgoing = transition_recipe(base_order[index + 1])[1] / duration if index < len(base_order) - 1 else 0
            start_zoom = round(1.10 + (token % 4) * .015 * intensity, 3) if incoming else 1.0
            end_zoom = round(.94 - (token % 3) * .01 * intensity, 3) if outgoing else round(1.02 + (token % 3) * .01, 3)
            points = [{"id": f"magia-{seed}-{clip_id}-start", "at": 0, "zoom": start_zoom, "x": .5, "y": .5}]
            if incoming:
                points.append({"id": f"magia-{seed}-{clip_id}-settle", "at": round(min(.35, max(.08, incoming)), 4), "zoom": 1.0, "x": .5, "y": .5})
            if outgoing:
                points.append({"id": f"magia-{seed}-{clip_id}-exit", "at": round(max(.55, 1 - outgoing), 4), "zoom": 1.0, "x": .5, "y": .5})
            points.append({"id": f"magia-{seed}-{clip_id}-end", "at": 1, "zoom": end_zoom, "x": .5, "y": .5})
            fields.update(zoom=1.0, position={"x": 0, "y": 0}, motion={"type": "none"},
                          keyframes={"enabled": True, "points": points})
            labels.append(f"zoom {round(start_zoom*100):d}→100→{round(end_zoom*100):d}%")
            counts["transforms"] += 1
        if options.get("color", True):
            warm = ((token // 7) % 9 - 4) / 100.0 * intensity
            saturation = {"saturated": 1.30, "muted": .74, "sepia": .62}.get(color_intent, 1.08)
            temperature = {"warm": .20, "cool": -.20, "sepia": .42}.get(color_intent, warm)
            contrast = 1.18 if color_intent == "contrast" else (1.12 if color_intent in ("saturated", "warm", "cool", "sepia") else 1.07)
            exposure_bias = {"bright": .16, "dark": -.16, "sepia": -.025}.get(color_intent, 0)
            tint_bias = .08 if color_intent == "sepia" else -temperature * .18
            fields["color"] = {"enabled": True, "exposure": round(exposure_bias + ((token % 5) - 2) * .035 * intensity, 3),
                               "contrast": round(contrast + (token % 3) * .012 * intensity, 3),
                               "saturation": round(saturation + (token % 4) * .025 * intensity, 3),
                               "temperature": round(temperature, 3), "tint": round(tint_bias, 3),
                               "highlights": round(-.06 * intensity, 3), "shadows": round(.045 * intensity, 3)}
            labels.append(color_intent + " color")
            counts["color"] += 1
        if options.get("audio", True):
            fade = round(min(.18 * intensity, duration * .1), 3)
            fields["audioFade"] = {"in": fade, "out": fade}
            counts["audio"] += 1
        if fields:
            categories = []
            if "transition" in fields: categories.append("transitions")
            if clip_id in retimed: categories.append("pacing")
            if is_overlay and any(key in fields for key in ("zoom", "position", "mask")): categories.append("overlays")
            elif any(key in fields for key in ("zoom", "position", "motion", "keyframes")): categories.append("transforms")
            if "color" in fields: categories.append("color")
            if "audioFade" in fields: categories.append("audio")
            updates.append({"clip_id": clip_id, "track_id": track.get("id"),
                            "name": item.get("name") or clip_id, "fields": fields,
                            "categories": categories, "changes": labels})
            counts["clips"] += 1
    existing_overlays = [(track, clip) for track, clip in targets if base and track.get("id") != base.get("id")]
    if scope == "timeline" and options.get("overlays", True) and base_order and not existing_overlays:
        overlay_track = next((track for track in video_tracks if track.get("id") == OVERLAY_TRACK), None)
        overlay_track_id = (overlay_track or {}).get("id") or OVERLAY_TRACK
        candidate_indexes = sorted(set((1, max(1, len(base_order) // 2), max(1, len(base_order) - 2))))
        treatments = [
            {"name": "split-screen preview", "zoom": 1.0, "position": {"x": 0, "y": 0},
             "mask": {"enabled": True, "type": "split", "x": 50, "y": 50, "width": 100, "height": 100, "invert": False}},
            {"name": "center echo", "zoom": .58, "position": {"x": 0, "y": 0},
             "mask": {"enabled": True, "type": "ellipse", "x": 50, "y": 50, "width": 72, "height": 72, "invert": False}},
            {"name": "cinematic window", "zoom": 1.0, "position": {"x": 0, "y": 0},
             "mask": {"enabled": True, "type": "cinematic", "x": 50, "y": 50, "width": 100, "height": 38, "invert": False}},
        ]
        for overlay_index, source_index in enumerate(candidate_indexes):
            if source_index >= len(base_order):
                continue
            source = base_order[source_index]
            duration = min(1.1, max(.72, clip_duration(source) * .19))
            token = _timeline_magia_number(seed, f"overlay:{source.get('id')}")
            treatment = treatments[(token + overlay_index) % len(treatments)]
            fade = round(min(.2, duration * .2), 3)
            overlay_id = f"magia-overlay-{seed}-{source.get('id')}"
            fields = {"id": overlay_id, "mediaId": source.get("mediaId"),
                      "start": round(max(0, float(source.get("start", 0)) - duration * .68), 6),
                      "in": float(source.get("in", 0)),
                      "out": round(min(float(source.get("out", 0)), float(source.get("in", 0)) + duration), 6),
                      "zoom": treatment["zoom"], "position": treatment["position"],
                      "motion": {"type": "none"}, "keyframes": None,
                      "mask": treatment["mask"],
                      "transition": {"items": [
                          {"id": f"{overlay_id}-in", "type": "fade", "edge": "start", "dur": fade, "enabled": True},
                          {"id": f"{overlay_id}-out", "type": "fade", "edge": "end", "dur": fade, "enabled": True}]},
                      "color": {"enabled": True, "exposure": .04, "contrast": 1.1,
                                "saturation": 1.08, "temperature": 0, "tint": 0,
                                "highlights": -.04, "shadows": .04},
                      "magiaOverlay": True}
            item = media.get(source.get("mediaId"), {})
            updates.append({"clip_id": None, "track_id": overlay_track_id,
                            "name": (item.get("name") or source.get("id")) + " overlay",
                            "create": fields,
                            "categories": ["overlays"],
                            "changes": [treatment["name"], f"next-scene preview {duration:.2f}s", f"fade {fade:.2f}s"]})
            counts["clips"] += 1
            counts["overlays"] += 1
    return {"seed": seed, "profile": profile, "direction": direction,
            "direction_note": interpreted["note"], "scope": scope,
            "selected_clip_id": selected_id,
            "options": options, "updates": updates, "summary": counts, "used_ai": interpreted["used_ai"]}


def apply_timeline_magia_plan(project, plan):
    by_id = {clip.get("id"): clip for track in project.get("tracks", []) for clip in track.get("clips", [])}
    allowed = {"start", "in", "out", "zoom", "position", "motion", "keyframes", "color", "blur",
               "mask", "audioFade", "volume", "transition", "muted"}
    category_keys = {"transitions": ("transition",),
                     "transforms": ("zoom", "position", "motion", "keyframes"),
                     "color": ("color",), "pacing": ("start", "out"),
                     "overlays": ("zoom", "position", "mask"), "audio": ("audioFade",)}
    options = plan.get("options") or {}
    scope = plan.get("scope") or "timeline"
    selected_id = str(plan.get("selected_clip_id") or "")
    applied = 0
    for track in project.get("tracks", []):
        # Generated overlays are wholly owned by Magia. Every apply replaces
        # them when enabled or removes them when the option is unchecked.
        before_count = len(track.get("clips", []))
        if track.get("id") == OVERLAY_TRACK:
            track["clips"] = [clip for clip in track.get("clips", [])
                              if not (clip.get("magiaOverlay") is True and str(clip.get("id") or "").startswith("magia-overlay-"))]
        applied += before_count - len(track["clips"])
        for clip in track.get("clips", []):
            if scope == "selected" and clip.get("id") != selected_id:
                continue
            provenance = clip.get("magiaEffects") or {}
            # Adopt effects written by the first Magia implementation, which
            # used recognizable IDs but predated explicit provenance storage.
            transition_ids = [str(item.get("id") or "") for item in (clip.get("transition") or {}).get("items", [])]
            keyframe_ids = [str(item.get("id") or "") for item in (clip.get("keyframes") or {}).get("points", [])]
            legacy_magia = any(value.startswith("magia-") for value in transition_ids + keyframe_ids)
            if legacy_magia and not provenance:
                if any(value.startswith("magia-") for value in transition_ids): provenance["transitions"] = {}
                if any(value.startswith("magia-") for value in keyframe_ids): provenance["transforms"] = {}
                if "color" in clip: provenance["color"] = {}
                if "audioFade" in clip: provenance["audio"] = {}
            restored = False
            for category, keys in category_keys.items():
                if options.get(category, True) or category not in provenance:
                    continue
                original = provenance.pop(category) or {}
                for key in keys: clip.pop(key, None)
                for key, value in original.items(): clip[key] = json.loads(json.dumps(value))
                restored = True
            if provenance: clip["magiaEffects"] = provenance
            else: clip.pop("magiaEffects", None)
            if restored: applied += 1
    for update in plan.get("updates", []):
        create = update.get("create")
        if create:
            track = next((item for item in project.get("tracks", []) if item.get("id") == update.get("track_id")), None)
            if not track:
                track = {"id": update.get("track_id") or next_track_id(project, "video"), "kind": "video",
                         "name": "Overlay", "muted": False, "clips": []}
                project.setdefault("tracks", []).insert(0, track)
            track.setdefault("clips", []).append(json.loads(json.dumps(create)))
            applied += 1
            continue
        clip = by_id.get(update.get("clip_id"))
        if not clip:
            continue
        provenance = clip.setdefault("magiaEffects", {})
        for category in update.get("categories") or []:
            if category in provenance:
                continue
            keys = category_keys.get(category, ())
            provenance[category] = {key: json.loads(json.dumps(clip[key])) for key in keys if key in clip}
        for key, value in (update.get("fields") or {}).items():
            if key in allowed:
                clip[key] = value
        applied += 1
    repair_timeline_overlaps(project)
    return applied


def prev_scene_last_frame(scene_id, project):
    """Path to the last frame of the scene immediately before `scene_id` (for
    'continue from previous' chaining), or None if there is no usable one."""
    order = project.get("order", [])
    if scene_id not in order:
        return None
    idx = order.index(scene_id)
    if idx == 0:
        return None
    prev = next((x for x in project["scenes"] if x["id"] == order[idx - 1]), None)
    if not prev:
        return None
    pdir = proj_dir(project["slug"])
    lf = prev.get("last_frame")
    if lf:
        p = pdir / lf.lstrip("/")
        if p.exists():
            return p
    if prev.get("mediaId"):
        m = next((x for x in project["media"] if x["id"] == prev["mediaId"]), None)
        if m:
            mp = abs_media(project, m)
            if mp.exists():
                out = pdir / "media" / "thumbs" / f"gen-{prev['id']}-last.jpg"
                if nle.extract_frame(mp, out, "last"):
                    return out
    return None


def selected_source_frame(scene, project):
    """Resolve an explicitly selected still, or extract the requested edge frame from video."""
    mid = scene.get("source_media_id")
    if not mid:
        return None
    media = next((m for m in project.get("media", []) if m.get("id") == mid), None)
    if not media:
        return None
    path = abs_media(project, media)
    if not path.exists():
        return None
    if media.get("kind") == "image":
        return path
    which = "first" if scene.get("source_frame") == "first" else "last"
    out = proj_dir(project["slug"]) / "media" / "thumbs" / f"source-{scene['id']}-{which}.jpg"
    out.parent.mkdir(parents=True, exist_ok=True)
    return out if nle.extract_frame(path, out, which) else None


def monitor_h3_process(proc, scene_id, stall_timeout=H3_STALL_TIMEOUT,
                       hard_timeout=H3_HARD_TIMEOUT):
    """Yield stderr lines while enforcing an inactivity timeout.

    Reading ``proc.stderr`` directly blocks the worker forever when the native
    H3 process wedges. A tiny daemon reader lets the worker keep checking the
    child's liveness without polling or taxing inference.
    """
    messages = stream_queue.Queue()

    def read_stderr():
        try:
            for raw in proc.stderr:
                messages.put(raw)
        finally:
            messages.put(None)

    threading.Thread(target=read_stderr, daemon=True).start()
    last_progress = time.monotonic()
    process_started = last_progress
    current = progress.get(scene_id, {})
    last_signature = (current.get("phase"), current.get("completed"), current.get("total"))
    while True:
        current = progress.get(scene_id, {})
        signature = (current.get("phase"), current.get("completed"), current.get("total"))
        if signature != last_signature:
            last_signature = signature
            last_progress = time.monotonic()
        silent_for = time.monotonic() - last_progress
        elapsed = time.monotonic() - process_started
        if hard_timeout and proc.poll() is None and elapsed >= hard_timeout:
            terminate_process_tree(proc)
            raise GenerationStalled(
                f"H3 exceeded the {int(hard_timeout)}-second generation limit"
            )
        if proc.poll() is None and silent_for >= stall_timeout:
            phase = progress.get(scene_id, {}).get("phase", "generation")
            terminate_process_tree(proc)
            raise GenerationStalled(
                f'H3 stopped advancing during "{phase}" for '
                f"{int(silent_for)} seconds"
            )
        try:
            line = messages.get(timeout=1.0)
        except stream_queue.Empty:
            if proc.poll() is not None:
                break
            continue
        if line is None:
            break
        yield line


def run_job(scene_id, project):
    global active_job, scene_proc
    # HTTP requests each load their own project snapshot. A later enqueue may
    # therefore add scenes/media while this worker is running. Always begin
    # from disk and merge completion back into the latest snapshot so an older
    # job can never overwrite newer queued work.
    project = load_project_slug(project["slug"])
    with lock:
        scene = next((s for s in project["scenes"] if s["id"] == scene_id), None)
        if scene is None:
            return
        scene["status"] = "running"
        scene["error"] = None
        scene["params"] = clamp_generation_params(
            scene.get("params") or {}, scene.get("generation_type", "video"))
        pending = next((x for x in project.get("media", []) if x.get("scene_id") == scene_id), None)
        if pending:
            pending["status"] = "running"
            if pending.get("generation"):
                pending["generation"]["params"] = dict(scene["params"])
        save_project(project)

    pdir = proj_dir(project["slug"])
    pdir.mkdir(parents=True, exist_ok=True)
    is_image = scene.get("generation_type") == "image"
    out = pdir / "media" / f"gen-{scene_id}.png" if is_image else pdir / "media" / f"gen-{scene_id}.mp4"
    frames_dir = pdir / "media" / f".image-frames-{scene_id}"
    ref2va = ref2va_available()
    chars = scene_all_references(scene, project)
    # "continue from previous": open this shot on the previous shot's last frame
    source_frame = selected_source_frame(scene, project) or (prev_scene_last_frame(scene_id, project) if scene.get("chain") else None)
    hybrid_continuity = bool(source_frame and scene.get("continuity_mode") == "reference")
    chain_frame = None if hybrid_continuity else source_frame
    image_idea = str(scene.get("prompt") or "").strip()
    image_formatted = image_idea.startswith(("integrated_multimodal_description:", "subject_definitions:", "For the target image,"))
    if scene.get("source_media_id") and not source_frame:
        source = next((m for m in project.get("media", []) if m.get("id") == scene.get("source_media_id")), None)
        if not source or source.get("status") != "ready":
            raise RuntimeError("The selected continuation video did not finish successfully. Choose a ready video or remove the continuation frame before retrying.")
    # Frame anchors and Ref2VA references are mutually exclusive in h3.c.
    generation_refs = [] if chain_frame else chars
    if hybrid_continuity:
        generation_refs = [{
            "name": "Previous scene final frame",
            "description": (
                "Highest continuity authority. Preserve the exact vehicle geometry, cast count and seating, "
                "props, environment, lighting, camera axis, travel direction, spatial relationships, and motion "
                "state established here. Begin as the immediate next moment; do not redesign, duplicate, or reset them."
            ),
            "paths": [source_frame],
            "kind": "continuity_reference",
        }] + chars
    prompt = ((image_idea if image_formatted and not chain_frame else format_image_prompt(
                  idea=image_idea, style=(scene.get("style_profile") or {}).get("prompt", ""),
                  mode="ref2va" if generation_refs else ("i2va" if chain_frame else "t2va"),
                  characters=generation_refs, answers=scene.get("guide_answers") or {}))
              if is_image else build_prompt(scene, project, ref2va, chain_frame, generation_refs))
    # Persist exactly what was sent, independently from the editable source
    # prompt. This makes every generation auditable and reproducible.
    with lock:
        latest = load_project_slug(project["slug"])
        stored = next((item for item in latest.get("scenes", []) if item.get("id") == scene_id), None)
        if stored is not None:
            stored["execution_prompt"] = prompt
            stored["formatter_model"] = FORMATTER_MODEL_ID or (Path(FORMATTER_MODEL).name if formatter_available() else "deterministic")
            if stored.get("prompt_skill_id"):
                stored["skill_compilation"] = compile_skill_contract(stored["prompt_skill_id"])
            save_project(latest)
    cmd = [H3_BIN, "-d", H3_MODEL, "-p", prompt,
           "-o", "" if is_image else str(out),
           "--width", str(scene["params"]["width"]),
           "--height", str(scene["params"]["height"]),
           "--frames", str(scene["params"]["frames"]),
           "--steps", str(scene["params"]["steps"]),
           "--layers", str(scene["params"]["layers"]),
           "--reuse", str(scene["params"]["reuse"]),
           "--seed", str(scene["params"]["seed"])]
    if is_image:
        frames_dir.mkdir(parents=True, exist_ok=True)
        cmd += ["--frames-dir", str(frames_dir)]
    # identity: Ref2VA uses ordered reference images (works with any framing)
    if ref2va and generation_refs:
        cmd += h3_reference_args(generation_refs)
    # first-frame: chaining takes priority; otherwise FL2VA anchors the face
    if chain_frame:
        cmd += ["--first-frame", str(chain_frame)]
    elif (not ref2va) and chars:
        cmd += ["--first-frame", str(chars[0]["paths"][0])]
    if scene.get("params", {}).get("stability_adjusted"):
        # Native h3.c controls specifically intended for long temporal runs:
        # refresh the expensive core less often and halve middle-block video
        # tokens. They make 10-15s native renders practical without changing
        # requested dimensions, frame count, denoising steps, or seed.
        cmd += ["--core-reuse", "6", "--token-reduction"]

    progress[scene_id] = {"phase": "starting", "completed": 0, "total": 1,
                          "started_at": time.time(), "first_step_at": None}
    try:
        proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
                                text=True, cwd=str(Path(H3_BIN).parent),
                                start_new_session=(os.name != "nt"))
        scene_proc = proc
        pat = re.compile(r"([A-Za-z_ ]+?)\s+(\d+)/(\d+)")
        tail = []
        long_native = bool(scene.get("params", {}).get("stability_adjusted"))
        for line in monitor_h3_process(
                proc, scene_id,
                stall_timeout=10800 if long_native else H3_STALL_TIMEOUT,
                hard_timeout=H3_HARD_TIMEOUT):
            line = line.rstrip("\n")
            m = pat.search(line)
            if m:
                previous = progress.get(scene_id, {})
                progress[scene_id] = {"phase": m.group(1).strip(),
                                      "completed": int(m.group(2)), "total": int(m.group(3)),
                                      "started_at": previous.get("started_at", time.time()),
                                      "first_step_at": previous.get("first_step_at") or time.time()}
            else:
                tail.append(line); del tail[:-30]
        rc = proc.wait()
        if rc != 0:
            raise RuntimeError(f"h3 exited with code {rc}: " +
                               " | ".join(t for t in tail if t.strip())[-800:])
        if is_image:
            frames = sorted(frames_dir.glob("frame-*.ppm"))
            if not frames:
                raise RuntimeError("H3 finished without writing the five experimental image frames")
            chosen = frames[len(frames) // 2]
            converted = subprocess.run(["ffmpeg", "-y", "-v", "error", "-i", str(chosen),
                                        "-frames:v", "1", str(out)], capture_output=True, text=True)
            if converted.returncode != 0 or not out.exists():
                raise RuntimeError("Could not save the selected H3 frame as a lossless PNG: " + converted.stderr[-500:])
        elif not out.exists():
            raise RuntimeError("H3 finished without writing the generated video")
        # H3 always synthesizes an audio stream. A silent scene is made truly
        # silent after inference instead of relying on the prompt alone.
        if not is_image and scene.get("params", {}).get("audio_mode") == "silent":
            silent_out = out.with_name(out.stem + "-silent.mp4")
            stripped = subprocess.run(
                ["ffmpeg", "-y", "-v", "error", "-i", str(out),
                 "-map", "0:v:0", "-c:v", "copy", "-an", str(silent_out)],
                capture_output=True, text=True)
            if stripped.returncode == 0 and silent_out.exists():
                silent_out.replace(out)
        # register as media + a clip on the base track at the end
        info = nle.probe(out) if not is_image else {"duration": 0, "w": scene["params"]["width"], "h": scene["params"]["height"], "hasAudio": False}
        thumb_url = None
        first_url = last_url = None
        tdir = pdir / "media" / "thumbs"
        tdir.mkdir(parents=True, exist_ok=True)
        tpath = tdir / f"gen-{scene_id}.jpg"
        try:
            if is_image:
                thumb_url = f"/media/gen-{scene_id}.png"
            else:
                if nle.make_thumb(out, tpath):
                    thumb_url = f"/media/thumbs/gen-{scene_id}.jpg"
        except Exception:
            pass
        # first/last frame stills (used for chaining + shown in the UI)
        try:
            if is_image:
                first_url = last_url = f"/media/gen-{scene_id}.png"
            else:
                if nle.extract_frame(out, tdir / f"gen-{scene_id}-first.jpg", "first"):
                    first_url = f"/media/thumbs/gen-{scene_id}-first.jpg"
                if nle.extract_frame(out, tdir / f"gen-{scene_id}-last.jpg", "last"):
                    last_url = f"/media/thumbs/gen-{scene_id}-last.jpg"
        except Exception:
            pass
        with lock:
            project = load_project_slug(project["slug"])
            scene = next((s for s in project["scenes"] if s["id"] == scene_id), None)
            if scene is None:
                raise RuntimeError("generated scene disappeared from the project")
            m = next((x for x in project["media"] if x.get("scene_id") == scene_id), None)
            if m:
                m.update({"src": str(out.relative_to(pdir)), "name": scene["name"], "kind": "image" if is_image else "video",
                          "source": "generated", "status": "ready", "duration": info["duration"],
                          "w": info.get("w", 0), "h": info.get("h", 0),
                          "hasAudio": info.get("hasAudio", False)})
                m.pop("error", None)
            else:
                m = add_media(project, out, scene["name"], "image" if is_image else "video", "generated")
                m["scene_id"] = scene_id
                m["style_profile"] = dict(scene.get("style_profile") or {})
                m["generation"] = {"prompt": scene.get("prompt", ""), "params": dict(scene.get("params") or {}),
                                   "prompt_skill_id": scene.get("prompt_skill_id"),
                                   "type": scene.get("generation_type", "video"),
                                   "frame_strategy": "middle_of_5" if is_image else None}
                m["status"] = "ready"
            if thumb_url:
                m["thumb"] = thumb_url
            scene["first_frame"] = first_url
            scene["last_frame"] = last_url
            scene["status"] = "ready"
            scene.pop("error", None)
            scene["mediaId"] = m["id"]
            if not is_image:
                start = timeline_end(project, BASE_TRACK)
                clip = {"id": uuid.uuid4().hex[:10], "mediaId": m["id"], "start": start,
                        "in": 0.0, "out": info["duration"], "zoom": 1.0,
                        "transition": {"type": "cut", "dur": 0.0}, "muted": False,
                        "detached": False, "sceneId": scene_id}
                base_track(project)["clips"].append(clip)
                scene["clipId"] = clip["id"]
            scene["media"] = media_url(m)
            save_project(project)
    except Exception as e:
        with lock:
            latest = load_project_slug(project["slug"])
            failed_scene = next((s for s in latest["scenes"] if s["id"] == scene_id), None)
            pending = next((x for x in latest["media"] if x.get("scene_id") == scene_id), None)
            retry_stall = (isinstance(e, GenerationStalled) and failed_scene and
                           int(failed_scene.get("stall_retries", 0)) < 1)
            if retry_stall:
                failed_scene["stall_retries"] = int(failed_scene.get("stall_retries", 0)) + 1
                failed_scene["params"] = safe_retry_params(failed_scene.get("params"))
                failed_scene["status"] = "queued"
                failed_scene["error"] = None
                failed_scene["warning"] = str(e) + "; retrying with the stable memory schedule"
                if pending:
                    pending["status"] = "queued"
                    pending.pop("error", None)
                    pending["warning"] = failed_scene["warning"]
                    if pending.get("generation"):
                        pending["generation"]["params"] = dict(failed_scene["params"])
                if scene_id not in queue:
                    queue.insert(0, scene_id)
            else:
                if failed_scene:
                    failed_scene["status"] = "error"
                    failed_scene["error"] = str(e)
                if pending:
                    pending["status"] = "error"
                    pending["error"] = str(e)
            save_project(latest)
            project = latest
    finally:
        if is_image and frames_dir.exists():
            shutil.rmtree(frames_dir, ignore_errors=True)
        progress.pop(scene_id, None)
        scene_proc = None
        active_job = None
        pump_queue(load_project_slug(project["slug"]))


def run_job_guarded(scene_id, project):
    """Record preflight failures and release the queue.

    ``run_job`` historically entered its try/finally only after resolving the
    continuation frame and constructing the H3 command. A missing helper such
    as ffmpeg could therefore kill the worker thread while leaving the scene
    permanently marked running and every later scene blocked behind it.
    """
    global active_job, scene_proc
    try:
        run_job(scene_id, project)
    except Exception as exc:
        slug = project.get("slug")
        latest = load_project_slug(slug)
        message = f"Generation preflight failed: {exc}"
        with lock:
            failed_scene = next((s for s in latest.get("scenes", []) if s.get("id") == scene_id), None)
            if failed_scene:
                failed_scene["status"] = "error"
                failed_scene["error"] = message
            pending = next((m for m in latest.get("media", []) if m.get("scene_id") == scene_id), None)
            if pending:
                pending["status"] = "error"
                pending["error"] = message
            save_project(latest)
        progress.pop(scene_id, None)
        scene_proc = None
        active_job = None
        pump_queue(latest)

def cancel_scene_tree(scene_id):
    """Cancel a scene and queued continuations that can no longer obtain its frame."""
    global active_job, scene_proc
    with job_lock:
        with lock:
            project = load_project()
            ids = {scene_id}
            changed = True
            while changed:
                changed = False
                media_ids = {m.get("id") for m in project.get("media", []) if m.get("scene_id") in ids}
                media_ids.update(s.get("mediaId") for s in project.get("scenes", []) if s.get("id") in ids)
                for scene in project.get("scenes", []):
                    missing_source = scene.get("source_media_id") and not any(m.get("id") == scene.get("source_media_id") for m in project.get("media", []))
                    if scene.get("id") not in ids and scene.get("status") in ("queued", "running") and (scene.get("source_media_id") in media_ids or missing_source):
                        ids.add(scene["id"]); changed = True
            cancelled = [s.get("name") or s["id"] for s in project.get("scenes", []) if s.get("id") in ids]
            owns_worker = active_job in ids
            queue[:] = [sid for sid in queue if sid not in ids]
            project["scenes"] = [s for s in project.get("scenes", []) if s.get("id") not in ids]
            project["order"] = [sid for sid in project.get("order", []) if sid not in ids]
            project["media"] = [m for m in project.get("media", []) if m.get("scene_id") not in ids]
            save_project(project)
        if owns_worker:
            terminate_process_tree(scene_proc)
    return cancelled


def pump_queue(project):
    global active_job
    if active_job is not None or not queue:
        return
    scene_id = queue.pop(0)
    active_job = scene_id
    threading.Thread(target=run_job_guarded, args=(scene_id, project), daemon=True).start()


def enqueue(scene_id, project):
    global active_job
    with job_lock:
        if scene_id != active_job and scene_id not in queue:
            queue.append(scene_id)
        with lock:
            scene = next((s for s in project["scenes"] if s["id"] == scene_id), None)
            if scene:
                if scene["status"] not in ("running", "queued"):
                    scene["status"] = "queued"
                    scene["error"] = None
                pending = next((m for m in project.get("media", []) if m.get("scene_id") == scene_id), None)
                if pending:
                    pending["status"] = "running" if scene["status"] == "running" else "queued"
                    pending["error"] = None
                else:
                    project["media"].append({
                        "id": uuid.uuid4().hex[:10], "asset_uid": uuid.uuid4().hex[:16], "src": "",
                        "name": scene.get("name", "Generating image" if scene.get("generation_type") == "image" else "Generating scene"), "kind": "image" if scene.get("generation_type") == "image" else "video", "duration": 0,
                        "w": scene.get("params", {}).get("width", 0), "h": scene.get("params", {}).get("height", 0),
                        "hasAudio": False, "source": "generated",
                        "status": "running" if scene["status"] == "running" else "queued", "scene_id": scene_id,
                        "style_profile": dict(scene.get("style_profile") or {}),
                        "generation": {"prompt": scene.get("prompt", ""), "params": dict(scene.get("params") or {}),
                                       "prompt_skill_id": scene.get("prompt_skill_id"),
                                       "type": scene.get("generation_type", "video"),
                                       "frame_strategy": "middle_of_5" if scene.get("generation_type") == "image" else None},
                    })
                save_project(project)
        if active_job is None:
            pump_queue(project)


def recover_queue(project):
    """Rebuild the volatile worker queue from persisted scene state.

    A backend restart clears the Python queue while project.json correctly
    retains queued jobs. A job recorded as running also becomes queued again
    because no inference process survived the restart. Scene-list order is the
    authoritative generation order.
    """
    global active_job
    with job_lock:
        if active_job is not None:
            return
        latest = load_project_slug(project["slug"])
        changed = False
        persisted = []
        with lock:
            for scene in latest.get("scenes", []):
                if scene.get("status") not in ("queued", "running"):
                    continue
                persisted.append(scene["id"])
                if scene.get("status") != "queued":
                    scene["status"] = "queued"
                    changed = True
                pending = next((m for m in latest.get("media", []) if m.get("scene_id") == scene["id"]), None)
                if pending and pending.get("status") != "queued":
                    pending["status"] = "queued"
                    changed = True
            # Discard orphaned in-memory entries and restore exact scene order.
            if queue != persisted:
                queue[:] = persisted
            # A running sheet belongs to a live worker; only heal when no
            # worker holds one, or every poll would reset an active job.
            if sheet_active is None:
                sheet_ids = []
                for sh in latest.get("sheets", []):
                    if sh.get("status") not in ("queued", "running"):
                        continue
                    sheet_ids.append(sh["id"])
                    if sh.get("status") != "queued":
                        sh["status"] = "queued"
                        changed = True
                if sheet_queue != sheet_ids:
                    sheet_queue[:] = sheet_ids
            if changed:
                save_project(latest)
        if queue:
            pump_queue(latest)
        if sheet_queue and sheet_active is None:
            pump_sheet_queue(latest)


def run_sheet_job(sheet_id, project):
    """Compose a character sheet: one silent Ref2VA turnaround + beat frames."""
    global sheet_active, sheet_proc
    project = load_project_slug(project["slug"])
    with lock:
        sheet = next((s for s in project.get("sheets", []) if s["id"] == sheet_id), None)
        if sheet is None:
            return
        sheet["status"] = "running"
        sheet["error"] = None
        save_project(project)

    pdir = proj_dir(project["slug"])
    pdir.mkdir(parents=True, exist_ok=True)
    out = pdir / "media" / f"sheet-{sheet_id}.mp4"
    try:
        with lock:
            refs = []
            for r in sheet.get("references", []):
                m = next((x for x in project["media"] if x["id"] == r.get("mediaId")), None)
                if m and m.get("kind") == "image" and m.get("src"):
                    refs.append({"path": abs_media(project, m), "keep": str(r.get("keep") or "")})
        if not refs:
            raise ValueError("Add at least one rough reference image before composing.")
        recipe = get_sheet_recipe(sheet.get("recipe", "turn-6"))
        params = clamp_generation_params({"width": 768, "height": 768,
                                          "frames": recipe["frames"],
                                          "steps": int(sheet.get("steps", 30)),
                                          # Character sheets are identity assets. Favor
                                          # temporal stability over the faster scene preset.
                                          "layers": 50, "reuse": 1,
                                          "seed": int(sheet.get("seed", 42)),
                                          "audio_mode": "silent"})
        prompt = format_sheet_prompt(name=sheet.get("name", ""),
                                     identity=sheet.get("identity", ""),
                                     references=[r["keep"] for r in refs],
                                     recipe=sheet.get("recipe", "turn-6"),
                                     style=sheet.get("style", "match"))
        cmd = [H3_BIN, "-d", H3_MODEL, "-p", prompt, "-o", str(out),
               "--width", str(params["width"]), "--height", str(params["height"]),
               "--frames", str(params["frames"]), "--steps", str(params["steps"]),
               "--layers", str(params["layers"]), "--reuse", str(params["reuse"]),
               "--seed", str(params["seed"])]
        for r in refs:
            cmd += ["--ref-image", str(r["path"])]

        sheet_progress[sheet_id] = {"phase": "starting", "completed": 0, "total": 1}
        proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
                                text=True, cwd=str(Path(H3_BIN).parent),
                                start_new_session=(os.name != "nt"))
        sheet_proc = proc
        pat = re.compile(r"([A-Za-z_ ]+?)\s+(\d+)/(\d+)")
        tail = []
        for line in proc.stderr:
            line = line.rstrip("\n")
            m = pat.search(line)
            if m:
                sheet_progress[sheet_id] = {"phase": m.group(1).strip(),
                                            "completed": int(m.group(2)), "total": int(m.group(3))}
            else:
                tail.append(line)
                del tail[:-30]
        rc = proc.wait()
        if rc != 0 or not out.exists():
            raise RuntimeError(f"h3 exited with code {rc}: " +
                               " | ".join(t for t in tail if t.strip())[-800:])
        # A sheet is silent by contract; strip whatever the encoder synthesized.
        stripped = subprocess.run(
            ["ffmpeg", "-y", "-v", "error", "-i", str(out),
             "-map", "0:v:0", "-c:v", "copy", "-an", str(out.with_name(out.stem + "-silent.mp4"))],
            capture_output=True, text=True)
        if stripped.returncode == 0 and out.with_name(out.stem + "-silent.mp4").exists():
            out.with_name(out.stem + "-silent.mp4").replace(out)
        info = nle.probe(out)

        with lock:
            project = load_project_slug(project["slug"])
            sheet = next((s for s in project.get("sheets", []) if s["id"] == sheet_id), None)
            if sheet is None:
                raise RuntimeError("composed sheet disappeared from the project")
            label_name = (sheet.get("name") or "Character").strip() or "Character"
            m = add_media(project, out, f"{label_name} · spin", "video", "generated")
            m.update({"status": "ready", "duration": info["duration"],
                      "w": info.get("w", 0), "h": info.get("h", 0), "hasAudio": False})
            tdir = proj_media_dir(project) / "thumbs"
            tdir.mkdir(parents=True, exist_ok=True)
            try:
                if nle.make_thumb(out, tdir / f"sheet-{sheet_id}.jpg"):
                    m["thumb"] = f"/media/thumbs/sheet-{sheet_id}.jpg"
            except Exception:
                pass
            frames = []
            extract_plan, diagnostics = sheet_extract_plan(out, sheet.get("recipe", "turn-6"))
            for t, flabel in extract_plan:
                fpath = proj_media_dir(project) / f"sheet-{sheet_id}-{flabel.replace(' ', '-')}.png"
                try:
                    if nle.extract_frame(out, fpath, t):
                        fm = add_media(project, fpath, f"{label_name} · {flabel}", "image", "sheet")
                        frames.append({"mediaId": fm["id"], "label": flabel, "time": t})
                except Exception:
                    continue
            if not frames:
                raise RuntimeError("generation finished but no reference frames could be extracted")
            sheet["videoMediaId"] = m["id"]
            sheet["frames"] = frames
            sheet["sheetDiagnostics"] = diagnostics
            sheet["extractionVersion"] = SHEET_EXTRACTION_VERSION
            sheet["status"] = "ready"
            save_project(project)
    except Exception as e:
        with lock:
            latest = load_project_slug(project["slug"])
            failed = next((s for s in latest.get("sheets", []) if s["id"] == sheet_id), None)
            if failed:
                failed["status"] = "error"
                failed["error"] = str(e)
            save_project(latest)
            project = latest
    finally:
        sheet_progress.pop(sheet_id, None)
        sheet_proc = None
        sheet_active = None
        pump_sheet_queue(load_project_slug(project["slug"]))


def pump_sheet_queue(project):
    global sheet_active
    if sheet_active is not None or not sheet_queue:
        return
    sid = sheet_queue.pop(0)
    sheet_active = sid
    threading.Thread(target=run_sheet_job, args=(sid, project), daemon=True).start()


def enqueue_sheet(sheet_id, project):
    global sheet_active
    with job_lock:
        if sheet_id not in sheet_queue:
            sheet_queue.append(sheet_id)
        with lock:
            sh = next((s for s in project.get("sheets", []) if s["id"] == sheet_id), None)
            if sh and sh.get("status") not in ("running", "queued"):
                sh["status"] = "queued"
                sh["error"] = None
                save_project(project)
        if sheet_active is None:
            pump_sheet_queue(project)


def freeze_frame(project, media_id, at, out_dur=2.0):
    """Extract a single frame from a media item at time `at` -> new image media."""
    m = next((x for x in project["media"] if x["id"] == media_id), None)
    if not m:
        raise RuntimeError("no such media")
    if m["kind"] == "image":
        return m
    p = abs_media(project, m)
    out = proj_media_dir(project) / f"freeze-{uuid.uuid4().hex[:8]}.png"
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-ss", f"{at:.3f}", "-i", str(p),
                    "-frames:v", "1", "-update", "1", str(out)], check=True)
    return add_media(project, out, f"{m['name']} (freeze)", "image", "freeze")


def export_timeline(project):
    return nle.export_project(project, proj_dir(project["slug"]))


def reveal_in_finder(path_str):
    """Open Finder at `path_str`. Only allows paths under the project's media/
    uploads dirs so a client can't reveal an arbitrary location on disk."""
    raw = Path(path_str).expanduser()
    target = raw.resolve() if raw.is_absolute() else (proj_dir(load_project()["slug"]) / raw).resolve()
    allowed = [ROOT.resolve()]
    try:
        allowed.append(proj_dir(load_project()["slug"]).resolve())
    except Exception:
        pass
    if not any(target == a or a in target.parents for a in allowed):
        raise PermissionError("path is outside the OpenMagia project")
    if not target.exists():
        raise FileNotFoundError(str(target))
    if sys.platform == "darwin":
        subprocess.Popen(["open", "-R", str(target)])
    elif os.name == "nt":
        subprocess.Popen(["explorer", "/select,", str(target)])
    else:
        subprocess.Popen(["xdg-open", str(target.parent)])
    return {"ok": True, "path": str(target)}


# Plugins render inside a sandboxed iframe, so the editor's own stylesheet can
# never cross into their document, and an opaque-origin frame cannot be styled
# or scripted from the host. Without these rules a plugin falls back to the
# browser's native light scrollbar, which reads as a broken panel against the
# dark plugin window. So the editor's scrollbar treatment is injected as the
# page is served: every plugin matches the app, and no plugin has to opt in.
# Mirrors the ::-webkit-scrollbar rules at the top of style.css.
PLUGIN_THEME_SHIM = ('<style id="openmagia-theme">'
                     ':root{color-scheme:dark}'
                     '::-webkit-scrollbar{width:10px;height:10px}'
                     '::-webkit-scrollbar-thumb{background:#3f3f46;border-radius:6px;'
                     'border:2px solid transparent;background-clip:content-box}'
                     '::-webkit-scrollbar-track{background:transparent}'
                     '::-webkit-scrollbar-corner{background:transparent}'
                     '</style>')


def plugin_theme_html(html: str) -> str:
    """Inject the editor scrollbar theme into a plugin page (idempotent)."""
    if "openmagia-theme" in html:
        return html
    anchor = re.search(r"<head[^>]*>", html, re.I) or re.search(r"<html[^>]*>", html, re.I)
    at = anchor.end() if anchor else 0
    return html[:at] + PLUGIN_THEME_SHIM + html[at:]


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _json(self, obj, code=200):
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _body(self):
        n = int(self.headers.get("Content-Length") or 0)
        return json.loads(self.rfile.read(n) or b"{}")

    def _serve_file(self, path: Path, ctype):
        if not path.exists():
            self.send_error(404)
            return
        size = path.stat().st_size
        start, end, partial = 0, max(0, size - 1), False
        range_header = self.headers.get("Range")
        if range_header and range_header.startswith("bytes=") and size:
            match = re.fullmatch(r"bytes=(\d*)-(\d*)", range_header.strip())
            if match:
                left, right = match.groups()
                try:
                    if left:
                        start = int(left)
                        end = min(int(right), size - 1) if right else size - 1
                    elif right:
                        length = min(int(right), size)
                        start, end = size - length, size - 1
                    if start < 0 or start >= size or end < start:
                        self.send_response(416)
                        self.send_header("Content-Range", f"bytes */{size}")
                        self.end_headers()
                        return
                    partial = True
                except ValueError:
                    partial = False
                    start, end = 0, size - 1
        length = max(0, end - start + 1)
        self.send_response(206 if partial else 200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(length))
        self.send_header("Accept-Ranges", "bytes")
        if partial:
            self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
        # never cache the app shell so code fixes reach the browser immediately
        self.send_header("Cache-Control", "no-store, no-cache, must-revalidate, max-age=0")
        self.send_header("Pragma", "no-cache")
        self.end_headers()
        with path.open("rb") as source:
            source.seek(start)
            remaining = length
            while remaining:
                chunk = source.read(min(1024 * 256, remaining))
                if not chunk:
                    break
                try:
                    self.wfile.write(chunk)
                except (BrokenPipeError, ConnectionResetError):
                    # Browsers routinely cancel speculative video range reads
                    # after metadata is available. That is not a server error.
                    break
                remaining -= len(chunk)

    def _serve_plugin_asset(self, path: Path):
        """Serve a plugin asset, theme-hooking HTML so it inherits the editor look."""
        ctype = self._ctype(path.suffix.lower())
        if not ctype.startswith("text/html"):
            return self._serve_file(path, ctype)
        if not path.exists():
            self.send_error(404)
            return
        body = plugin_theme_html(path.read_text(encoding="utf-8", errors="replace")).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store, no-cache, must-revalidate, max-age=0")
        self.end_headers()
        self.wfile.write(body)

    def _ctype(self, suffix):
        return {".html": "text/html; charset=utf-8", ".htm": "text/html; charset=utf-8",
                ".json": "application/json; charset=utf-8", ".txt": "text/plain; charset=utf-8",
                ".mp4": "video/mp4", ".png": "image/png", ".jpg": "image/jpeg",
                ".jpeg": "image/jpeg", ".webp": "image/webp", ".m4a": "audio/mp4",
                ".wav": "audio/wav", ".mp3": "audio/mpeg", ".aac": "audio/aac",
                ".flac": "audio/flac", ".ogg": "audio/ogg", ".opus": "audio/ogg",
                ".svg": "image/svg+xml", ".css": "text/css; charset=utf-8",
                ".js": "application/javascript; charset=utf-8", ".mjs": "application/javascript; charset=utf-8"}.get(
                suffix, "application/octet-stream")

    def do_GET(self):
        p = urllib.parse.unquote(self.path.split("?")[0])
        if p == "/":
            html = (ROOT / "index.html").read_text()
            stamp = str(int(max((ROOT / f).stat().st_mtime for f in ("app.js", "style.css", "index.html"))))
            html = re.sub(r'src="app\.js(?:\?[^"#]*)?"', 'src="app.js?v=' + stamp + '"', html)
            html = re.sub(r'href="style\.css(?:\?[^"#]*)?"', 'href="style.css?v=' + stamp + '"', html)
            body = html.encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store, no-cache, must-revalidate, max-age=0")
            self.end_headers()
            self.wfile.write(body)
            return
        if p.startswith("/media/") or p.startswith("/uploads/"):
            # media lives inside the active project folder
            base = proj_dir(load_project()["slug"]).resolve()
            bucket = "media" if p.startswith("/media/") else "uploads"
            media_root = (base / bucket).resolve()
            f = (media_root / p[len(bucket) + 2:]).resolve()
            if f != media_root and media_root not in f.parents:
                self.send_error(403)
                return
            return self._serve_file(f, self._ctype(f.suffix.lower()))
        if p == "/api/plugins":
            return self._json({"plugins": plugin_registry.list(), "store": {"status": "coming-soon"}})
        if p == "/api/plugins/logs":
            qs = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
            return self._json({"logs": plugin_registry.logs((qs.get("pluginId") or [None])[0], (qs.get("limit") or [200])[0])})
        plugin_asset = re.match(r"^/api/plugins/([a-z0-9._-]+)/assets/(.+)$", p)
        if plugin_asset:
            try:
                asset = plugin_registry.asset(plugin_asset.group(1), plugin_asset.group(2))
                return self._serve_plugin_asset(asset)
            except PluginError as exc:
                return self._json({"error": str(exc)}, 404)
        if p == "/api/assets/library":
            with lock:
                return self._json({"assets": asset_library(), "projects": list_projects()})
        mapreview = re.match(r"^/api/assets/([\w-]+)/preview$", p)
        if mapreview:
            uid = mapreview.group(1)
            with lock:
                for summary in list_projects():
                    project = load_project_slug(summary["slug"])
                    media = next((m for m in project.get("media", []) if m.get("asset_uid") == uid), None)
                    if media:
                        if media.get("kind") == "video" and not media.get("thumb"):
                            source = abs_media(project, media)
                            target = proj_media_dir(project) / "thumbs" / f"asset-{uid}.jpg"
                            target.parent.mkdir(parents=True, exist_ok=True)
                            try:
                                if nle.make_thumb(source, target):
                                    media["thumb"] = str(target.relative_to(proj_dir(project["slug"])))
                                    save_project(project)
                            except Exception:
                                pass
                        # Image assets must use their original source. Some
                        # generated/sheet records carry a thumbnail intended
                        # for the compact media bin; serving that derivative
                        # here permanently crops the reusable asset before CSS
                        # gets a chance to fit it inside the Asset Center card.
                        rel = (media.get("src") if media.get("kind") == "image"
                               else media.get("thumb") or media.get("src"))
                        f = proj_dir(project["slug"]) / str(rel).lstrip("/")
                        return self._serve_file(f, self._ctype(f.suffix.lower()))
            return self._json({"error": "no such asset"}, 404)
        if p == "/api/state":
            # Polling state also heals a queue lost to an app/backend restart.
            # Once a worker is active this is a cheap no-op.
            recover_queue(load_project())
            with lock:
                proj = load_project()
                out = json.loads(json.dumps(proj))
                for s in out["scenes"]:
                    if s["id"] in progress:
                        raw = progress[s["id"]]
                        now = time.time()
                        public = {"phase": raw.get("phase", "starting"),
                                  "completed": raw.get("completed", 0), "total": raw.get("total", 1),
                                  "elapsed_seconds": max(0, round(now - raw.get("started_at", now)))}
                        completed, total = public["completed"], public["total"]
                        first_step = raw.get("first_step_at")
                        if first_step and completed > 0 and total > completed:
                            seconds_per_step = max(0.1, (now - first_step) / completed)
                            public["eta_seconds"] = round(seconds_per_step * (total - completed))
                        s["progress"] = public
                for s in out.get("sheets", []):
                    if s["id"] in sheet_progress:
                        s["progress"] = sheet_progress[s["id"]]
                out["slug"] = proj.get("slug")
                out["projects"] = list_projects()
                out["engine"] = {
                    "app_version": APP_IDENTITY["version"], "app_build": APP_IDENTITY["build"],
                    "h3_bin": H3_BIN, "h3_bin_ok": Path(H3_BIN).exists(),
                    "model_root": H3_MODEL,
                    "fl2va": (Path(H3_MODEL) / "FL2VA" / "transformer" / "config.json").exists(),
                    "ref2va": ref2va_available(),
                    "media_dir": str(proj_dir(proj["slug"]) / "media"),
                    "timeline_end": round(timeline_end(proj), 3),
                    "formatter": formatter_available(),
                    "formatter_model": FORMATTER_MODEL_ID or Path(FORMATTER_MODEL).name,
                    "formatter_source": FORMATTER_ENDPOINT or str(Path(FORMATTER_MODEL)),
                    "ffmpeg": bool(shutil.which("ffmpeg")),
                    "memory_gb": SYSTEM_MEMORY_GB,
                    "model_installs": dict(model_installs),
                    "can_undo": bool(undo_stacks.get(proj.get("slug"), [])),
                }
                return self._json(out)
        if p == "/api/prompt/templates":
            return self._json({"templates": PRESETS, "max_frames": MAX_FRAMES,
                               "max_seconds": 15, "fps": 24,
                               "max_references": MAX_REFERENCES})
        if p == "/api/skills":
            return self._json(skill_catalog_report())
        if p == "/api/models/discover":
            return self._json({"sources": discover_model_sources()})
        if p == "/api/models/manage":
            return self._json(model_management_state())
        if p == "/api/sheets/recipes":
            return self._json({"recipes": SHEET_RECIPES, "styles": SHEET_STYLES,
                               "max_references": MAX_REFERENCES})
        if p == "/api/projects":
            return self._json({"projects": list_projects(), "active": active_slug()})
        mpc = re.match(r"^/api/projects/([\w-]+)/cover$", p)
        if mpc:
            f = project_cover_path(mpc.group(1))
            if f:
                return self._serve_file(f, self._ctype(f.suffix.lower()))
            return self._json({"error": "no cover"}, 404)
        if p == "/api/export":
            with lock:
                proj = load_project()
            try:
                url = export_timeline(proj)
            except Exception as e:
                return self._json({"error": str(e)}, 500)
            return self._json({"url": url})
        if p == "/api/reveal":
            qs = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
            path = (qs.get("path") or [""])[0]
            try:
                return self._json(reveal_in_finder(path))
            except Exception as e:
                return self._json({"error": str(e)}, 400)
        f = ROOT / p.lstrip("/")
        return self._serve_file(f, self._ctype(f.suffix.lower()))

    def do_POST(self):
        p = self.path.split("?")[0]
        if p == "/api/plugins/load":
            try:
                return self._json(plugin_registry.install(self._body().get("path") or ""))
            except PluginError as exc:
                return self._json({"error": str(exc)}, 400)
        plugin_update = re.match(r"^/api/plugins/([a-z0-9._-]+)$", p)
        if plugin_update:
            b = self._body()
            try:
                return self._json(plugin_registry.update(plugin_update.group(1), enabled=b.get("enabled"),
                    grants=b.get("grants") if "grants" in b else None,
                    settings=b.get("settings") if "settings" in b else None))
            except PluginError as exc:
                return self._json({"error": str(exc)}, 400)
        plugin_log = re.match(r"^/api/plugins/([a-z0-9._-]+)/log$", p)
        if plugin_log:
            b = self._body()
            try:
                plugin_registry.authorize(plugin_log.group(1), "generation.events")
                return self._json(plugin_registry.log(plugin_log.group(1), str(b.get("level") or "info"),
                    str(b.get("message") or "Plugin event"), b.get("detail")))
            except PluginError as exc:
                return self._json({"error": str(exc)}, 403)
        plugin_notify = re.match(r"^/api/plugins/([a-z0-9._-]+)/notify$", p)
        if plugin_notify:
            b = self._body(); channel = str(b.get("channel") or "")
            permission = "notifications." + channel
            try:
                plugin = plugin_registry.authorize(plugin_notify.group(1), permission)
                result = send_notification(channel, plugin.get("settings") or {}, str(b.get("title") or "OpenMagia"), str(b.get("message") or ""))
                plugin_registry.log(plugin["id"], "info", "Notification sent" if not result.get("dryRun") else "Notification dry run", {"channel": channel})
                return self._json(result)
            except PluginError as exc:
                plugin_registry.log(plugin_notify.group(1), "error", str(exc), {"channel": channel})
                return self._json({"error": str(exc)}, 400)
        plugin_settings = re.match(r"^/api/plugins/([a-z0-9._-]+)/settings$", p)
        if plugin_settings:
            try:
                plugin_registry.authorize(plugin_settings.group(1), "storage")
                # A missing "settings" key used to fall through to {}, which
                # silently erased a plugin's whole stored configuration --
                # including saved SMTP credentials. Replaces must be explicit.
                body = self._body()
                if "settings" not in body:
                    return self._json({"error": "Missing 'settings' object; nothing was changed."}, 400)
                if not isinstance(body["settings"], dict):
                    return self._json({"error": "'settings' must be an object."}, 400)
                return self._json(plugin_registry.update(plugin_settings.group(1), settings=body["settings"]))
            except PluginError as exc:
                return self._json({"error": str(exc)}, 403)
        if p == "/api/models/install":
            b = self._body()
            component = str(b.get("component") or "")
            if component not in ("h3", "formatter", "runtime"):
                return self._json({"error": "unknown model component"}, 400)
            if component == "h3" and not b.get("accepted_license"):
                return self._json({"error": "Accept the MiniMax H3 license before downloading."}, 400)
            with model_install_lock:
                if model_installs.get(component, {}).get("status") == "running":
                    return self._json({"ok": True, "status": "running"})
                # Claim the component before starting the worker. Previously
                # quick clicks could launch installers into the same files.
                model_installs[component] = {"status": "running", "message": "Preparing download…"}
            threading.Thread(target=install_model_component, args=(component,), daemon=True).start()
            return self._json({"ok": True, "status": "running"})
        if p == "/api/models/select":
            b = self._body()
            try:
                return self._json(select_model_source(str(b.get("kind") or ""), str(b.get("path") or ""),
                                                      str(b.get("endpoint") or ""), str(b.get("model_id") or ""),
                                                      str(b.get("role") or "")))
            except (ValueError, OSError) as exc:
                return self._json({"error": str(exc)}, 400)
        if p == "/api/models/loras/import":
            return self._json({"error": "The installed h3.c backend does not support LoRA adapters."}, 400)
        lora_update = re.match(r"^/api/models/loras/([a-z0-9-]+)$", p)
        if lora_update:
            b = self._body()
            try:
                return self._json(update_lora(lora_update.group(1), b.get("enabled") if "enabled" in b else None,
                                              b.get("strength") if "strength" in b else None,
                                              b.get("backend_id") if "backend_id" in b else None))
            except (ValueError, OSError) as exc:
                return self._json({"error": str(exc)}, 400)
        if p == "/api/assets/assign":
            b = self._body()
            try:
                with lock:
                    result = assign_asset(str(b.get("asset_uid") or ""), list(b.get("projects") or []))
                return self._json(result)
            except (ValueError, FileNotFoundError) as e:
                return self._json({"error": str(e)}, 400)
        with lock:
            proj = load_project()

        if p == "/api/media/folders/create":
            b = self._body()
            name = str(b.get("name") or "").strip().strip("/")
            parent = str(b.get("parent") or "").strip("/")
            desc = str(b.get("description") or "").strip()
            if not name or "/" in name or name in (".", "..") or "\\" in name:
                return self._json({"error": "Folder name cannot be empty, '.', '..' or contain slashes."}, 400)
            with lock:
                proj = load_project()
                folders = proj.setdefault("mediaFolders", [])
                if parent:
                    return self._json({"error": "Nested folders aren't supported yet \u2014 folders live at the top level for now."}, 400)
                path = (parent + "/" + name) if parent else name
                if path in folders:
                    return self._json({"error": "A folder with that name already exists here."}, 400)
                folders.append(path)
                if desc:
                    proj.setdefault("mediaFolderMeta", {})[path] = {"description": desc}
                (proj_dir(proj["slug"]) / "media" / path).mkdir(parents=True, exist_ok=True)
                save_project(proj)
                if b.get("ids"):
                    _media_fs_move(proj, list(b.get("ids") or []), path)
                    save_project(proj)
            return self._json({"ok": True, "path": path})

        if p == "/api/media/folders/delete":
            b = self._body()
            path = str(b.get("path") or "").strip("/")
            with lock:
                proj = load_project()
                folders = proj.setdefault("mediaFolders", [])
                if path not in folders:
                    return self._json({"error": "Folder not found."}, 404)
                parent = path.rsplit("/", 1)[0] if "/" in path else ""
                doomed = [f for f in folders if f == path or f.startswith(path + "/")]
                meta = proj.setdefault("mediaFolderMeta", {})
                for f in doomed:
                    meta.pop(f, None)
                # re-point records at the surviving parent, remembering originals
                retargeted = []
                for m in proj.get("media", []):
                    f = str(m.get("folder") or "")
                    if f == path or f.startswith(path + "/"):
                        m["folder"] = parent
                        retargeted.append(m)
                # physically lift the files out, deepest first
                pdir = proj_dir(proj["slug"])
                root = pdir / "media"
                for f in sorted(doomed, key=lambda x: -x.count("/")):
                    d = root / f
                    up = root / f.rsplit("/", 1)[0] if "/" in f else root
                    if d.is_dir():
                        for child in sorted(d.iterdir()):
                            dest = _unique_target(up, child.name)
                            shutil.move(str(child), str(dest))
                            old_rel = (d.relative_to(pdir) / child.name).as_posix()
                            new_rel = dest.relative_to(pdir).as_posix()
                            for m in retargeted:
                                if str(m.get("src") or "").lstrip("/") == old_rel:
                                    m["src"] = new_rel
                        try:
                            d.rmdir()
                        except OSError:
                            pass
                folders[:] = [f for f in folders if f not in doomed]
                save_project(proj)
            return self._json({"ok": True})

        if p == "/api/media/move":
            b = self._body()
            ids = list(b.get("ids") or [])
            folder = str(b.get("folder") or "").strip("/")
            conflict = str(b.get("conflict") or "ask")
            with lock:
                proj = load_project()
                if folder and folder not in proj.setdefault("mediaFolders", []):
                    return self._json({"error": "Target folder does not exist."}, 400)
                byid = {m.get("id"): m for m in proj.get("media", [])}
                valid = [i for i in ids if i in byid]
                collisions = _folder_name_collisions(proj, valid, folder)
                if collisions and conflict == "ask":
                    return self._json({"error": "Same-name media already exists in this folder.",
                                       "conflicts": [{"id": m.get("id"), "name": m.get("name")} for m in collisions]}, 409)
                if collisions and conflict == "overwrite":
                    moved = 0
                    for mid in valid:
                        incoming = byid[mid]
                        target = next((m for m in collisions
                                       if str(m.get("name") or "").strip().casefold() == str(incoming.get("name") or "").strip().casefold()), None)
                        if target:
                            target_path = abs_media(proj, target)
                            proj["media"].remove(target)
                            if target_path.is_file() and target_path != abs_media(proj, incoming) and not any(
                                    abs_media(proj, other) == target_path for other in proj.get("media", [])):
                                target_path.unlink()
                        _move_media_record(proj, incoming, folder)
                        moved += 1
                elif collisions and conflict not in ("keep-both", "ask"):
                    return self._json({"error": "Unknown conflict choice."}, 400)
                else:
                    moved = _media_fs_move(proj, valid, folder, keep_both=conflict == "keep-both")
                save_project(proj)
            return self._json({"ok": True, "moved": moved, "replaced": len(collisions) if conflict == "overwrite" else 0})

        if p == "/api/media/folders/update":
            b = self._body()
            path = str(b.get("path") or "").strip("/")
            name = str(b.get("name") or "").strip().strip("/")
            desc = str(b.get("description") or "").strip()
            if not name or "/" in name or name in (".", "..") or "\\" in name:
                return self._json({"error": "Folder name cannot be empty, '.', '..' or contain slashes."}, 400)
            with lock:
                proj = load_project()
                folders = proj.setdefault("mediaFolders", [])
                if path not in folders:
                    return self._json({"error": "Folder not found."}, 404)
                if name != path and name in folders:
                    return self._json({"error": "A folder with that name already exists."}, 400)
                pdir = proj_dir(proj["slug"]) / "media"
                src_dir = pdir / path
                src_dir.mkdir(parents=True, exist_ok=True)
                meta = proj.setdefault("mediaFolderMeta", {})
                if name != path:
                    dest_dir = pdir / name
                    if src_dir.exists() and src_dir != dest_dir:
                        os.rename(src_dir, dest_dir)
                    folders[:] = [name if f == path else f for f in folders]
                    if path in meta:
                        meta[name] = meta.pop(path)
                    for m in proj.get("media", []):
                        if m.get("folder") == path:
                            m["folder"] = name
                        src = str(m.get("src") or "").lstrip("/")
                        old_prefix = "media/" + path + "/"
                        if src.startswith(old_prefix):
                            m["src"] = "media/" + name + "/" + src[len(old_prefix):]
                if desc:
                    meta[name] = {"description": desc}
                else:
                    meta.pop(name, None)
                save_project(proj)
            return self._json({"ok": True, "path": name})

        if p in ("/api/timeline/magia/plan", "/api/timeline/magia/apply"):
            b = self._body()
            with lock:
                proj = load_project()
                plan = timeline_magia_plan(proj, b)
                if p.endswith("/apply"):
                    push_timeline_undo(proj)
                    plan["applied"] = apply_timeline_magia_plan(proj, plan)
                    save_project(proj)
            return self._json(plan)

        if p == "/api/undo":
            with lock:
                ok = undo_timeline(proj)
            return self._json({"ok": ok, "can_undo": bool(undo_stacks.get(proj.get("slug"), []))})

        if p == "/api/clips" or p == "/api/tracks":
            push_timeline_undo(proj)

        if p == "/api/upload":
            n = int(self.headers.get("Content-Length") or 0)
            data = self.rfile.read(n)
            # filename arrives URL-encoded so any Unicode (e.g. macOS U+202F)
            # survives the HTTP header, which is limited to Latin-1.
            raw_name = self.headers.get("X-File-Name", "upload")
            try:
                name = urllib.parse.unquote(raw_name)
            except Exception:
                name = raw_name
            if not name:
                name = "upload"
            ext = Path(name).suffix or ".png"
            uid = uuid.uuid4().hex[:10]
            target = proj_uploads_dir(proj) / f"{uid}{ext}"
            target.write_bytes(data)
            kind = sniff_kind(data, ext)
            thumb_url = None
            if kind == "video":
                tdir = proj_media_dir(proj) / "thumbs"
                tdir.mkdir(parents=True, exist_ok=True)
                tpath = tdir / f"{uid}.jpg"
                try:
                    if nle.make_thumb(target, tpath):
                        thumb_url = f"/media/thumbs/{uid}.jpg"
                except Exception:
                    pass
            with lock:
                m = add_media(proj, target, Path(name).stem, kind, "import")
                if thumb_url:
                    m["thumb"] = thumb_url
                save_project(proj)
            return self._json(m)

        if p == "/api/characters":
            b = self._body()
            images = b.get("images") or []
            if not images and b.get("image"):
                images = [b["image"]]
            if len(images) > MAX_REFERENCES:
                return self._json({"error": f"A scene supports at most {MAX_REFERENCES} reference images."}, 400)
            c = {"id": uuid.uuid4().hex[:10], "name": b.get("name", "Character"), "description": str(b.get("description") or ""),
                 "images": list(images), "image": (images[0] if images else None)}
            proj["characters"].append(c)
            save_project(proj)
            return self._json(c)

        me = re.match(r"^/api/characters/([\w-]+)/enrich$", p)
        if me:
            c = next((x for x in proj["characters"] if x["id"] == me.group(1)), None)
            if not c:
                return self._json({"error": "no such character"}, 404)
            b = self._body()
            text, used_ai = enrich_character_locally(c.get("name", "Character"), str(b.get("description") or c.get("description") or ""))
            c["description"] = text
            save_project(proj)
            return self._json({"description": text, "used_ai": used_ai})

        # ---- character sheet composition ---------------------------------
        # Deterministic by design: the staging script and field structure are
        # code-owned; user keep/ignore notes reach the engine verbatim.
        if p == "/api/sheets":
            b = self._body()
            if not ref2va_available():
                return self._json({"error": "Composing sheets needs Ref2VA ordered references. Run install.sh to add it."}, 400)
            raw_refs = list(b.get("references") or [])
            if not raw_refs:
                return self._json({"error": "Add at least one rough reference image."}, 400)
            if len(raw_refs) > MAX_REFERENCES:
                return self._json({"error": f"MiniMax H3 accepts at most {MAX_REFERENCES} ordered reference images; this sheet has {len(raw_refs)}."}, 400)
            refs = []
            for r in raw_refs:
                mid = r.get("mediaId") if isinstance(r, dict) else r
                m = next((x for x in proj["media"] if x["id"] == mid and x.get("kind") == "image"), None)
                if not m or not m.get("src"):
                    return self._json({"error": "a rough reference image is missing from the media bin"}, 400)
                refs.append({"mediaId": m["id"], "keep": str(r.get("keep") if isinstance(r, dict) else "") or ""})
            recipe = b.get("recipe") if b.get("recipe") in [r_["id"] for r_ in SHEET_RECIPES] else SHEET_RECIPES[0]["id"]
            style = b.get("style") if b.get("style") in [s_["id"] for s_ in SHEET_STYLES] else SHEET_STYLES[0]["id"]
            try:
                seed = int(b.get("seed", 42))
            except (TypeError, ValueError):
                seed = 42
            try:
                steps = max(1, min(60, int(b.get("steps", 30))))
            except (TypeError, ValueError):
                steps = 30
            sheet = {"id": uuid.uuid4().hex[:10],
                     "name": str(b.get("name") or "").strip() or "Character",
                     "identity": str(b.get("identity") or "").strip(),
                     "recipe": recipe, "style": style, "seed": seed, "steps": steps,
                     "references": refs, "status": "idle", "error": None,
                     "frames": [], "videoMediaId": None}
            proj.setdefault("sheets", []).append(sheet)
            save_project(proj)
            enqueue_sheet(sheet["id"], proj)
            return self._json(sheet)

        ms = re.match(r"^/api/sheets/([\w-]+)/generate$", p)
        if ms:
            sh = next((s for s in proj.get("sheets", []) if s["id"] == ms.group(1)), None)
            if not sh:
                return self._json({"error": "no such sheet"}, 404)
            enqueue_sheet(sh["id"], proj)
            return self._json({"ok": True})

        ms = re.match(r"^/api/sheets/([\w-]+)/save$", p)
        if ms:
            b = self._body()
            with lock:
                latest = load_project()
                sh = next((s for s in latest.get("sheets", []) if s["id"] == ms.group(1)), None)
                if not sh:
                    return self._json({"error": "no such sheet"}, 404)
                if sh.get("status") != "ready":
                    return self._json({"error": "this sheet is not ready yet"}, 400)
                images = list(dict.fromkeys(b.get("images") or []))
                if not images:
                    return self._json({"error": "Select at least one view before saving."}, 400)
                if len(images) > MAX_REFERENCES:
                    return self._json({"error": f"A character supports at most {MAX_REFERENCES} reference images."}, 400)
                for mid in images:
                    m = next((x for x in latest["media"] if x["id"] == mid and x.get("kind") == "image"), None)
                    if not m:
                        return self._json({"error": "a selected frame is missing from the media bin"}, 400)
                c = {"id": uuid.uuid4().hex[:10],
                     "name": str(b.get("name") or "").strip() or sh.get("name") or "Character",
                     "description": str(b.get("description") if b.get("description") is not None else (sh.get("identity") or "")),
                     "images": images, "image": images[0]}
                c["composed"] = {"sheet_id": sh["id"], "recipe": sh.get("recipe"), "style": sh.get("style")}
                latest["characters"].append(c)
                latest["sheets"] = [s for s in latest["sheets"] if s["id"] != sh["id"]]
                save_project(latest)
            return self._json(c)

        if p == "/api/continuity/audit":
            b = self._body()
            return self._json(audit_storyboard_continuity(b, proj, bool(b.get("use_ai", True))))

        if p == "/api/storyboards/optimize":
            b = self._body()
            try:
                return self._json(optimize_storyboard_scenes(b, bool(b.get("use_ai", True))))
            except (TypeError, ValueError) as error:
                return self._json({"error": str(error)}, 400)

        if p == "/api/storyboards/magia":
            b = self._body()
            try:
                return self._json(make_magia_storyboard(b, proj, bool(b.get("use_ai", True))))
            except (TypeError, ValueError) as error:
                return self._json({"error": str(error)}, 400)

        if p == "/api/continuity/style":
            b = self._body()
            media_id = str(b.get("media_id") or "") or None
            if media_id and not any(m.get("id") == media_id for m in proj.get("media", [])):
                return self._json({"error": "The selected media is no longer available."}, 404)
            profile, used_ai = build_continuity_style(proj, media_id, bool(b.get("use_ai", True)),
                                                      str(b.get("new_prompt") or ""), b.get("new_answers") or {})
            with lock:
                latest = load_project()
                existing = next((x for x in latest.setdefault("project_style_skills", [])
                                 if x.get("id") == profile["id"]), None)
                if existing:
                    previous_sources = list(existing.get("source_media_ids") or [])
                    profile["source_media_ids"] = list(dict.fromkeys(previous_sources + profile["source_media_ids"]))
                    existing.clear(); existing.update(profile)
                else:
                    latest["project_style_skills"].append(profile)
                latest["style_profile"] = {k: profile[k] for k in ("name", "prompt", "skill_id", "source")}
                latest["base_prompt"] = profile["prompt"]
                latest["style_enabled"] = True
                save_project(latest)
            return self._json({"profile": profile, "used_ai": used_ai})

        if p == "/api/storyboards/generate":
            b = self._body()
            try:
                with job_lock:
                    with lock:
                        latest = load_project()
                        scenes = create_storyboard_batch(latest, b)
                        save_project(latest)
                    for scene in scenes:
                        if scene["id"] not in queue:
                            queue.append(scene["id"])
                    if active_job is None:
                        pump_queue(latest)
                return self._json({"ok": True, "scenes": scenes}, 201)
            except ValueError as error:
                return self._json({"error": str(error)}, 400)

        if p == "/api/scenes":
            b = self._body()
            sid = uuid.uuid4().hex[:10]
            # default to every cast member so the reference image is sent; the
            # client can still send an explicit (possibly empty) list to opt out
            if "character_ids" in b:
                char_ids = list(b.get("character_ids") or [])
            else:
                char_ids = [c["id"] for c in proj["characters"]]
            inferred_char_ids = structured_prompt_character_ids(b.get("prompt", ""), proj) if not char_ids else []
            if inferred_char_ids:
                char_ids = inferred_char_ids
            reference_media_ids = list(dict.fromkeys(b.get("reference_media_ids") or []))
            character_reference_ids = {str(cid): list(dict.fromkeys(ids or [])) for cid, ids in dict(b.get("character_reference_ids") or {}).items()}
            if inferred_char_ids and not any(character_reference_ids.values()):
                character_reference_ids = None
            proposed = {"character_ids": char_ids, "character_reference_ids": character_reference_ids if "character_reference_ids" in b else None, "reference_media_ids": reference_media_ids}
            try:
                # Exact continuation uses a frame anchor. h3.c cannot combine
                # that anchor with Ref2VA, so Cast images do not consume the
                # nine-reference budget in this mode; identity notes persist.
                selected_refs = scene_all_references(proposed, proj)
                if b.get("source_media_id") and any(item.get("kind") == "audio_reference" for item in selected_refs):
                    raise ValueError("Audio references require Ref2VA and cannot be combined with an exact opening-frame anchor. Remove the opening frame, or use storyboard reference continuity.")
                references = [] if b.get("source_media_id") else selected_refs
                validate_references(references)
            except ValueError as e:
                return self._json({"error": str(e)}, 400)
            generation_type = "image" if b.get("generation_type") == "image" else "video"
            if generation_type == "image" and any(item.get("kind") == "audio_reference" for item in selected_refs):
                return self._json({"error": "Audio references are supported for H3 video generation only. Remove the audio reference or switch to Video."}, 400)
            use_project_style = b.get("use_project_style", proj.get("style_enabled", True)) is not False
            params = clamp_generation_params({**default_params(proj["canvas"]), **b.get("params", {})}, generation_type)
            s = {"id": sid, "name": b.get("name") or (next_image_name(proj) if generation_type == "image" else next_scene_name(proj)),
                 "prompt": b.get("prompt", ""),
                 "original_prompt": str(b.get("original_prompt") or b.get("prompt") or ""),
                 "refined_prompt": str(b.get("refined_prompt") or b.get("prompt") or ""),
                 "skill_compilation": dict(b.get("skill_compilation") or {}),
                 "character_ids": char_ids, "character_reference_ids": character_reference_ids, "reference_media_ids": reference_media_ids,
                 "params": params, "guide_answers": dict(b.get("guide_answers") or {}),
                 "template_id": b.get("template_id"), "generation_type": generation_type,
                 "prompt_skill_id": b.get("prompt_skill_id"),
                 "style_profile": dict(proj.get("style_profile") or {}) if use_project_style else {},
                 "use_project_style": use_project_style,
                 "chain": bool(b.get("chain")), "source_media_id": b.get("source_media_id"),
                 "source_frame": str(b.get("source_frame") or "last"), "status": "idle", "error": None,
                 "media": None, "mediaId": None, "clipId": None,
                 "first_frame": None, "last_frame": None}
            proj["scenes"].append(s)
            proj["order"].append(sid)
            save_project(proj)
            return self._json(s)

        if p == "/api/prompt/custom-skill":
            b = self._body()
            if not str(b.get("name") or "").strip() or not str(b.get("purpose") or "").strip():
                return self._json({"error": "Add a skill name and describe what it should create."}, 400)
            skill_id, description, specification, steps, used_ai = compose_custom_skill_locally(b)
            return self._json({"id": skill_id, "description": description, "specification": specification,
                               "steps": steps, "used_ai": used_ai})

        if p == "/api/prompt/format":
            b = self._body()
            idea = str(b.get("idea") or "").strip()
            if not idea:
                return self._json({"error": "Describe the video idea first."}, 400)
            answers = dict(b.get("answers") or {})
            skill_id = str(b.get("prompt_skill_id") or answers.get("prompt_skill_id") or "").strip()
            try:
                skill_compilation = compile_skill_contract(skill_id)
            except ValueError as exc:
                return self._json({"error": str(exc)}, 400)
            bundled_direction = skill_compilation["refinement_direction"] if skill_compilation else ""
            if bundled_direction:
                answers["skill_instruction"] = bundled_direction
            # Complete H3 prompts keep their schema and reference labels, but
            # an attached skill is still validated and compiled into the
            # detailed description. Structured authoring no longer bypasses
            # the selected production workflow.
            if is_structured_h3_prompt(idea):
                explicit_seconds = requested_duration_seconds(idea)
                structured_frames = (round(explicit_seconds * FPS)
                                     if explicit_seconds is not None else b.get("frames", 56))
                resolved_frames, structured_seconds = duration_for_frames(structured_frames)
                try:
                    prepared = apply_skill_contract_to_structured(idea, skill_id, structured_seconds)
                except ValueError as exc:
                    return self._json({"error": str(exc)}, 400)
                return self._json({"prompt": prepared, "expanded_idea": prepared,
                                   "mode": "structured", "used_ai": False,
                                   "references": 0, "unchanged": prepared == idea,
                                   "duration_seconds": structured_seconds,
                                   "frames": resolved_frames,
                                   "skill_applied": bool(bundled_direction),
                                   "skill_compilation": skill_compilation})
            char_ids = list(b.get("character_ids") or [])
            reference_media_ids = list(dict.fromkeys(b.get("reference_media_ids") or []))
            character_reference_ids = {str(cid): list(dict.fromkeys(ids or [])) for cid, ids in dict(b.get("character_reference_ids") or {}).items()}
            chars = scene_all_references({"character_ids": char_ids,
                                         "character_reference_ids": character_reference_ids if "character_reference_ids" in b else None,
                                         "reference_media_ids": reference_media_ids}, proj)
            audio_refs = [item for item in chars if item.get("kind") == "audio_reference"]
            if audio_refs:
                answers["reference_audio"] = (
                    "Available ordered H3 audio references: " + "; ".join(
                        f"<Audio {index}> {item.get('name')}: {item.get('description')}"
                        for index, item in enumerate(audio_refs, 1)) +
                    ". Assign only roles supported by the user's idea or answers: music reuse, rhythm, voice timbre, dialogue timing/lip sync, ambience, or sound characteristics."
                )
            if b.get("continuity_reference"):
                chars = [{"name": "Previous scene final frame",
                          "description": "highest opening continuity authority",
                          "paths": [Path("__pending_previous_scene_final_frame__.png")],
                          "kind": "continuity_reference"}] + chars
            try:
                validate_references(chars)
            except ValueError as e:
                return self._json({"error": str(e)}, 400)
            if len(str(answers.get("continuity") or "")) > 8000:
                answers["continuity"] = compact_scene_prompt(answers["continuity"])
            explicit_seconds = requested_duration_seconds(idea)
            requested_frames = (round(explicit_seconds * FPS)
                                if explicit_seconds is not None else b.get("frames", 56))
            resolved_frames, prompt_seconds = duration_for_frames(requested_frames)
            answers["_duration_seconds"] = round(prompt_seconds, 3)
            if str(b.get("task") or "scene") == "style":
                expanded, used_ai = improve_style_locally(idea, answers, bool(b.get("use_ai", True)))
                return self._json({"prompt": expanded, "expanded_idea": expanded,
                                   "mode": "style", "used_ai": used_ai, "references": 0})
            style = str(b.get("style") or "")
            task = str(b.get("task") or "scene")
            if task == "image" and audio_refs:
                return self._json({"error": "Audio references are supported for H3 video generation only. Remove the audio reference or switch to Video."}, 400)
            if task == "image":
                expanded, used_ai = improve_image_locally(idea, answers, style) if b.get("use_ai", True) else (idea, False)
            else:
                expanded, used_ai = improve_idea_locally(idea, answers, style) if b.get("use_ai", True) else (idea, False)
            expanded = enforce_skill_expansion(expanded, idea, skill_id, prompt_seconds)
            # Refinement sees every authoring rule; H3 receives visual
            # direction only, so workflow prose cannot leak into the render.
            if skill_compilation:
                answers["skill_instruction"] = skill_compilation["visual_direction"]
            requested_mode = str(b.get("mode") or "t2va")
            if requested_mode == "i2va":
                idea += scene_identity_text({"character_ids": char_ids}, proj)
                chars = []
            mode = "ref2va" if requested_mode != "i2va" and ref2va_available() and chars else requested_mode
            if task == "image":
                formatted = format_image_prompt(idea=expanded, style=style, mode=mode,
                                                characters=chars, answers=answers)
                return self._json({"prompt": formatted, "expanded_idea": expanded,
                                   "mode": mode, "used_ai": used_ai,
                                   "references": count_references(chars),
                                   "skill_compilation": skill_compilation})
            formatted = format_prompt(idea=expanded, style=style,
                                      frames=resolved_frames, mode=mode,
                                      characters=chars, answers=answers)
            return self._json({"prompt": formatted, "expanded_idea": expanded,
                               "mode": mode, "used_ai": used_ai,
                               "references": count_references(chars),
                               "duration_seconds": prompt_seconds,
                               "frames": resolved_frames,
                               "skill_compilation": skill_compilation})

        if p == "/api/project":
            b = self._body()
            if "canvas" in b and isinstance(b["canvas"], dict):
                proj["canvas"] = {"width": int(b["canvas"]["width"]),
                                  "height": int(b["canvas"]["height"])}
            if "name" in b:
                proj["name"] = b["name"]
            if "base_prompt" in b:
                proj["base_prompt"] = str(b.get("base_prompt") or "")
                proj.setdefault("style_profile", {})["prompt"] = proj["base_prompt"]
            if "style_profile" in b and isinstance(b["style_profile"], dict):
                incoming = b["style_profile"]
                proj["style_profile"] = {
                    "name": str(incoming.get("name") or "Custom project style"),
                    "prompt": str(incoming.get("prompt") or ""),
                    "skill_id": incoming.get("skill_id"),
                    "source": str(incoming.get("source") or "custom"),
                }
                proj["base_prompt"] = proj["style_profile"]["prompt"]
            if "style_enabled" in b:
                proj["style_enabled"] = bool(b["style_enabled"])
            if "storyboard_draft" in b:
                draft = b.get("storyboard_draft")
                proj["storyboard_draft"] = draft if isinstance(draft, dict) else None
            if "ui_layout" in b and isinstance(b["ui_layout"], dict):
                allowed = ("media_width", "inspector_width", "timeline_height", "timeline_maximized",
                           "timeline_zoom", "lane_header_width", "prompt_height", "style_height",
                           "inspector_clip_tab")
                proj["ui_layout"] = {k: b["ui_layout"][k] for k in allowed if k in b["ui_layout"]}
            save_project(proj)
            return self._json({"ok": True})

        if p == "/api/projects":
            b = self._body()
            pr = create_project(b.get("name") or "Untitled")
            return self._json({"slug": pr["slug"], "name": pr["name"]})

        if p == "/api/projects/switch":
            b = self._body()
            slug = b.get("slug")
            if not (PROJECTS / slug / "project.json").exists():
                return self._json({"error": "no such project"}, 404)
            set_active(slug)
            return self._json({"ok": True, "active": slug})

        m = re.match(r"^/api/scenes/([\w-]+)/generate$", p)
        if m:
            sid = m.group(1)
            target_scene = next((s for s in proj["scenes"] if s["id"] == sid), None)
            if not target_scene:
                return self._json({"error": "no such scene"}, 404)
            try:
                validate_references(scene_all_references(target_scene, proj))
            except ValueError as e:
                return self._json({"error": str(e)}, 400)
            retry_ids = [sid]
            # Retrying the failed head of a storyboard must also revive its
            # continuity dependents; otherwise they remain permanent errors
            # even after their source frame becomes available.
            changed = True
            while changed:
                changed = False
                for scene in proj.get("scenes", []):
                    if scene.get("id") not in retry_ids and scene.get("depends_on_scene_id") in retry_ids:
                        retry_ids.append(scene["id"])
                        changed = True
            # Persist the complete dependency chain before starting its head.
            # Starting the first worker earlier can race its fresh disk load
            # against later per-scene saves and restore stale error statuses.
            with lock:
                latest = load_project_slug(proj["slug"])
                for retry_id in retry_ids:
                    retry_scene = next((s for s in latest.get("scenes", []) if s.get("id") == retry_id), None)
                    if retry_scene:
                        retry_scene["status"] = "running" if retry_id == active_job else "queued"
                        retry_scene["error"] = None
                    retry_media = next((item for item in latest.get("media", []) if item.get("scene_id") == retry_id), None)
                    if retry_media:
                        retry_media["status"] = "running" if retry_id == active_job else "queued"
                        retry_media.pop("error", None)
                save_project(latest)
                proj = latest
            for retry_id in retry_ids:
                enqueue(retry_id, proj)
            return self._json({"ok": True, "queued": retry_ids})

        m = re.match(r"^/api/scenes/([\w-]+)/cancel$", p)
        if m:
            cancelled = cancel_scene_tree(m.group(1))
            return self._json({"ok": True, "cancelled": cancelled})

        if p == "/api/tracks":
            b = self._body()
            kind = b.get("kind", "video")
            if kind not in ("video", "audio"):
                return self._json({"error": "kind must be video or audio"}, 400)
            tid = next_track_id(proj, kind)
            name = b.get("name") or ("Video" if kind == "video" else "Audio")
            track = {"id": tid, "kind": kind, "name": name, "muted": False, "clips": []}
            # insert audio tracks after video tracks for a stable visual order
            if kind == "audio":
                proj["tracks"].append(track)
            else:
                # keep video tracks before the first audio track
                idx = next((i for i, t in enumerate(proj["tracks"]) if t["kind"] == "audio"), len(proj["tracks"]))
                proj["tracks"].insert(idx, track)
            save_project(proj)
            return self._json(track)

        # ---- NLE clip operations ----
        if p == "/api/clips":
            b = self._body()
            track = next((t for t in proj["tracks"] if t["id"] == b.get("trackId")), None)
            if not track:
                return self._json({"error": "no such track"}, 404)
            media = next((x for x in proj["media"] if x["id"] == b.get("mediaId")), None)
            if not media:
                return self._json({"error": "no such media"}, 404)
            clip = {"id": uuid.uuid4().hex[:10], "mediaId": media["id"],
                    "start": float(b.get("start", 0.0)),
                    "in": float(b.get("in", 0.0)),
                    "out": float(b.get("out", media["duration"])),
                    "zoom": float(b.get("zoom", 1.0)),
                    "position": b.get("position", {"x": 0, "y": 0}),
                    "motion": b.get("motion"),
                    "keyframes": b.get("keyframes"),
                    "color": b.get("color"),
                    "blur": b.get("blur"),
                    "mask": b.get("mask"),
                    "audioFade": b.get("audioFade", {"in": 0.0, "out": 0.0}),
                    "volume": float(b.get("volume", 1.0)),
                    "transition": b.get("transition", {"type": "cut", "dur": 0.0}),
                    "muted": bool(b.get("muted", False)),
                    "detached": bool(b.get("detached", False))}
            clip["start"] = resolve_clip_start(track, clip["start"], clip_duration(clip))
            track["clips"].append(clip)
            save_project(proj)
            return self._json(clip)

        if p == "/api/freeze":
            b = self._body()
            try:
                m = freeze_frame(proj, b["mediaId"], float(b.get("at", 0.0)),
                                 float(b.get("dur", 2.0)))
            except Exception as e:
                return self._json({"error": str(e)}, 500)
            save_project(proj)
            return self._json(m)

        # extract a single frame (first | last | seconds) from a video as a new
        # image in the media bin — usable as a character ref, a still, or a
        # first/last-frame for chaining
        if p == "/api/frame":
            b = self._body()
            m = next((x for x in proj["media"] if x["id"] == b.get("mediaId")), None)
            if not m:
                return self._json({"error": "no such media"}, 404)
            if m["kind"] == "image":
                return self._json(m)
            at = b.get("at", "first")
            if at not in ("first", "last"):
                try:
                    at = float(at)
                except (TypeError, ValueError):
                    at = "first"
            out = proj_media_dir(proj) / f"frame-{uuid.uuid4().hex[:8]}.png"
            try:
                if not nle.extract_frame(abs_media(proj, m), out, at):
                    raise RuntimeError("could not extract frame")
                if at == "first":
                    label = "first frame"
                elif at == "last":
                    label = "last frame"
                else:
                    label = f"{float(at):.2f}s frame"
                mm = add_media(proj, out, f"{m['name']} · {label}", "image", "frame")
            except Exception as e:
                return self._json({"error": str(e)}, 500)
            save_project(proj)
            return self._json(mm)

        if p == "/api/export":
            try:
                url = export_timeline(proj)
            except Exception as e:
                return self._json({"error": str(e)}, 500)
            return self._json({"url": url})

        self._json({"error": "not found"}, 404)

    def do_PUT(self):
        p = self.path.split("?")[0]
        b = self._body()
        mp = re.match(r"^/api/projects/([\w-]+)$", p)
        if mp:
            try:
                pr = rename_project(mp.group(1), b.get("name"))
                return self._json({"ok": True, "name": pr["name"]})
            except Exception as e:
                return self._json({"error": str(e)}, 404)
        with lock:
            proj = load_project()
            if re.match(r"^/api/(?:clips|tracks)/[\w-]+$", p):
                push_timeline_undo(proj)
            # scene updates
            ms = re.match(r"^/api/scenes/([\w-]+)$", p)
            if ms:
                s = next((x for x in proj["scenes"] if x["id"] == ms.group(1)), None)
                if not s:
                    return self._json({"error": "no such scene"}, 404)
                reference_fields = {"character_ids", "character_reference_ids", "reference_media_ids"}
                if s.get("status") in {"queued", "running"} and reference_fields.intersection(b):
                    return self._json({"error": "Cast and visual references are locked while a scene is queued or generating."}, 409)
                for k in ("name", "prompt", "chain", "source_media_id", "source_frame"):
                    if k in b:
                        s[k] = b[k]
                if "character_ids" in b:
                    proposed = {**s, "character_ids": list(b["character_ids"])}
                    try:
                        validate_references(scene_all_references(proposed, proj))
                    except ValueError as e:
                        return self._json({"error": str(e)}, 400)
                    s["character_ids"] = proposed["character_ids"]
                if "character_reference_ids" in b:
                    proposed = {**s, "character_reference_ids": {str(cid): list(dict.fromkeys(ids or [])) for cid, ids in dict(b.get("character_reference_ids") or {}).items()}}
                    try:
                        validate_references(scene_all_references(proposed, proj))
                    except ValueError as e:
                        return self._json({"error": str(e)}, 400)
                    s["character_reference_ids"] = proposed["character_reference_ids"]
                if "reference_media_ids" in b:
                    proposed = {**s, "reference_media_ids": list(dict.fromkeys(b["reference_media_ids"] or []))}
                    try:
                        validate_references(scene_all_references(proposed, proj))
                    except ValueError as e:
                        return self._json({"error": str(e)}, 400)
                    s["reference_media_ids"] = proposed["reference_media_ids"]
                if "params" in b:
                    s["params"] = clamp_generation_params({**s.get("params", {}), **b["params"]})
                if "params" in b:
                    s["params"].update(b["params"])
                # renaming a scene renames its generated media too, so the
                # timeline clip label and media bin stay in sync
                if "name" in b and s.get("mediaId"):
                    m = next((x for x in proj["media"] if x["id"] == s["mediaId"]), None)
                    if m:
                        m["name"] = b["name"]
                save_project(proj)
                return self._json(s)
            # character updates (name / images)
            mch = re.match(r"^/api/characters/([\w-]+)$", p)
            if mch:
                cid = mch.group(1)
                c = next((x for x in proj["characters"] if x["id"] == cid), None)
                if not c:
                    return self._json({"error": "no such character"}, 404)
                if "name" in b:
                    c["name"] = b["name"]
                if "description" in b:
                    c["description"] = str(b["description"] or "")
                if "images" in b:
                    if len(b["images"]) > MAX_REFERENCES:
                        return self._json({"error": f"A scene supports at most {MAX_REFERENCES} reference images."}, 400)
                    c["images"] = list(b["images"])
                    c["image"] = (c["images"][0] if c["images"] else None)
                save_project(proj)
                return self._json(c)
            # track updates (muted / name)
            mt = re.match(r"^/api/tracks/([\w-]+)$", p)
            if mt:
                t = next((x for x in proj["tracks"] if x["id"] == mt.group(1)), None)
                if not t:
                    return self._json({"error": "no such track"}, 404)
                for k in ("muted", "name"):
                    if k in b:
                        t[k] = b[k]
                if "index" in b:
                    old_index = proj["tracks"].index(t)
                    new_index = max(0, min(len(proj["tracks"]) - 1, int(b["index"])))
                    target = proj["tracks"][new_index]
                    if target["kind"] != t["kind"]:
                        return self._json({"error": "Video and audio lanes must remain in their own groups."}, 400)
                    proj["tracks"].pop(old_index)
                    proj["tracks"].insert(new_index, t)
                save_project(proj)
                return self._json(t)
            # media rename
            mm = re.match(r"^/api/media/([\w-]+)$", p)
            if mm:
                mid = mm.group(1)
                m = next((x for x in proj["media"] if x["id"] == mid), None)
                if not m:
                    return self._json({"error": "no such media"}, 404)
                if "name" in b and b["name"].strip():
                    try:
                        rename_media_file(proj, m, b["name"])
                    except FileNotFoundError as error:
                        return self._json({"error": str(error)}, 409)
                save_project(proj)
                return self._json(m)
            # clip updates
            mc = re.match(r"^/api/clips/([\w-]+)$", p)
            if mc:
                cid = mc.group(1)
                for t in proj["tracks"]:
                    c = next((x for x in t["clips"] if x["id"] == cid), None)
                    if c:
                        destination = t
                        for k in ("start", "in", "out", "zoom", "position", "muted", "detached", "audioClipId", "audioFade", "volume"):
                            if k in b:
                                c[k] = b[k]
                        if "motion" in b:
                            c["motion"] = b["motion"]
                        if "keyframes" in b:
                            c["keyframes"] = b["keyframes"]
                        if "color" in b:
                            c["color"] = b["color"]
                        if "blur" in b:
                            c["blur"] = b["blur"]
                        if "mask" in b:
                            c["mask"] = b["mask"]
                        if "transition" in b:
                            c["transition"] = b["transition"]
                        if "trackId" in b and b["trackId"] != t["id"]:
                            target = next((x for x in proj["tracks"] if x["id"] == b["trackId"]), None)
                            if not target:
                                return self._json({"error": "no such target track"}, 404)
                            if target["kind"] != t["kind"]:
                                return self._json({"error": "Clips can only move between compatible lanes."}, 400)
                            t["clips"].remove(c)
                            target["clips"].append(c)
                            destination = target
                        c["start"] = resolve_clip_start(destination, c.get("start", 0), clip_duration(c), c["id"])
                        save_project(proj)
                        return self._json(c)
                return self._json({"error": "no such clip"}, 404)
        self._json({"error": "not found"}, 404)

    def do_DELETE(self):
        p = self.path.split("?")[0]
        model_installation = re.match(r"^/api/models/installations/([a-z0-9-]+)$", p)
        if model_installation:
            try: return self._json(uninstall_managed_model(model_installation.group(1)))
            except (ValueError, OSError) as exc: return self._json({"error":str(exc)}, 400)
        model_lora = re.match(r"^/api/models/loras/([a-z0-9-]+)$", p)
        if model_lora:
            try: return self._json(remove_lora(model_lora.group(1)))
            except (ValueError, OSError) as exc: return self._json({"error":str(exc)}, 400)
        plugin = re.match(r"^/api/plugins/([a-z0-9._-]+)$", p)
        if plugin:
            try:
                plugin_registry.remove(plugin.group(1)); return self._json({"ok": True})
            except PluginError as exc:
                return self._json({"error": str(exc)}, 404)
        mp = re.match(r"^/api/projects/([\w-]+)$", p)
        if mp:
            try:
                delete_project(mp.group(1))
                return self._json({"ok": True})
            except Exception as e:
                return self._json({"error": str(e)}, 404)
        # Discard a character-sheet draft. If it is queued or rendering, the
        # pending entry is dropped and the live h3 process is terminated.
        msheet = re.match(r"^/api/sheets/([\w-]+)$", p)
        if msheet:
            sid = msheet.group(1)
            cancelled = False
            with job_lock:
                with lock:
                    proj = load_project()
                    sh = next((s for s in proj.get("sheets", []) if s["id"] == sid), None)
                    if not sh:
                        return self._json({"error": "no such sheet"}, 404)
                    owns_worker = (sheet_active == sid)
                    proj["sheets"] = [s for s in proj["sheets"] if s["id"] != sid]
                    save_project(proj)
                    if sid in sheet_queue:
                        sheet_queue.remove(sid)
                if owns_worker:
                    cancelled = terminate_process_tree(sheet_proc)
            return self._json({"ok": True, "cancelled": cancelled})
        # Treat deleting a queued/running generated-media placeholder as a
        # scene cancellation even if the browser has stale status metadata.
        # This keeps every removal entry point attached to the worker queue and
        # process-tree shutdown path.
        pending_media = re.match(r"^/api/media/([\w-]+)$", p)
        if pending_media:
            with lock:
                current = load_project()
                media = next((x for x in current.get("media", []) if x.get("id") == pending_media.group(1)), None)
                scene = next((x for x in current.get("scenes", []) if media and x.get("id") == media.get("scene_id")), None)
                must_cancel = bool(media and media.get("scene_id") and (
                    media.get("status") in ("queued", "running") or
                    (scene and scene.get("status") in ("queued", "running"))))
                scene_id = media.get("scene_id") if must_cancel else None
            if scene_id:
                return self._json({"ok": True, "cancelled": cancel_scene_tree(scene_id)})
        with lock:
            proj = load_project()
            if re.match(r"^/api/(?:clips|tracks)/[\w-]+$", p):
                push_timeline_undo(proj)
            ms = re.match(r"^/api/scenes/([\w-]+)$", p)
            if ms:
                sid = ms.group(1)
                scene = next((s for s in proj["scenes"] if s["id"] == sid), None)
                proj["scenes"] = [s for s in proj["scenes"] if s["id"] != sid]
                proj["order"] = [i for i in proj["order"] if i != sid]
                if scene and scene.get("clipId"):
                    for t in proj["tracks"]:
                        t["clips"] = [c for c in t["clips"] if c["id"] != scene["clipId"]]
                save_project(proj)
                return self._json({"ok": True})
            mstyle = re.match(r"^/api/project/styles/([\w-]+)$", p)
            if mstyle:
                style_id = mstyle.group(1)
                before = len(proj.setdefault("project_style_skills", []))
                proj["project_style_skills"] = [x for x in proj["project_style_skills"] if x.get("id") != style_id]
                if len(proj["project_style_skills"]) == before:
                    return self._json({"error": "no such project style"}, 404)
                if (proj.get("style_profile") or {}).get("skill_id") == style_id:
                    proj["style_profile"] = {"name": "No project style", "prompt": "", "skill_id": None, "source": "custom"}
                    proj["base_prompt"] = ""
                save_project(proj)
                return self._json({"ok": True})
            mc = re.match(r"^/api/clips/([\w-]+)$", p)
            if mc:
                cid = mc.group(1)
                for t in proj["tracks"]:
                    t["clips"] = [c for c in t["clips"] if c["id"] != cid]
                save_project(proj)
                return self._json({"ok": True})
            mch = re.match(r"^/api/characters/([\w-]+)$", p)
            if mch:
                cid = mch.group(1)
                proj["characters"] = [c for c in proj["characters"] if c["id"] != cid]
                for s in proj["scenes"]:
                    s["character_ids"] = [i for i in s.get("character_ids", []) if i != cid]
                save_project(proj)
                return self._json({"ok": True})
            mt = re.match(r"^/api/tracks/([\w-]+)$", p)
            if mt:
                tid = mt.group(1)
                if tid in (BASE_TRACK, OVERLAY_TRACK, AUDIO_TRACK):
                    return self._json({"error": "cannot delete a core track"}, 400)
                before = len(proj["tracks"])
                proj["tracks"] = [t for t in proj["tracks"] if t["id"] != tid]
                if len(proj["tracks"]) == before:
                    return self._json({"error": "no such track"}, 404)
                save_project(proj)
                return self._json({"ok": True})
            mm = re.match(r"^/api/media/([\w-]+)$", p)
            if mm:
                mid = mm.group(1)
                m = next((x for x in proj["media"] if x["id"] == mid), None)
                proj["media"] = [x for x in proj["media"] if x["id"] != mid]
                for t in proj["tracks"]:
                    t["clips"] = [c for c in t["clips"] if c["mediaId"] != mid]
                # drop the deleted image from any character's reference list
                for c in proj["characters"]:
                    imgs = [i for i in (c.get("images") or []) if i != mid]
                    if len(imgs) != len(c.get("images") or []):
                        c["images"] = imgs
                        c["image"] = (imgs[0] if imgs else None)
                    if c.get("image") == mid:
                        c["image"] = (imgs[0] if imgs else None)
                for s in proj.get("scenes", []):
                    s["reference_media_ids"] = [i for i in s.get("reference_media_ids", []) if i != mid]
                if m:
                    f = abs_media(proj, m)
                    pdir = proj_dir(proj["slug"]).resolve()
                    if f.exists() and (f.resolve() == pdir or pdir in f.resolve().parents):
                        try:
                            f.unlink()
                        except Exception:
                            pass
                    if m.get("thumb"):
                        tf = proj_dir(proj["slug"]) / m["thumb"].lstrip("/")
                        if tf.exists():
                            try:
                                tf.unlink()
                            except Exception:
                                pass
                save_project(proj)
                return self._json({"ok": True})
        self._json({"error": "not found"}, 404)


def migrate_legacy_project():
    """One-time: move the old single-project layout (data/project.json + media/
    + uploads/) into projects/<slug>/ with relative media paths, so every
    project lives in its own portable folder."""
    old_proj = DATA / "project.json"
    if not old_proj.exists():
        return
    p = json.loads(old_proj.read_text())
    name = p.get("name") or "Untitled"
    slug = p.get("slug") or slugify(name)
    dest = PROJECTS / slug
    if (dest / "project.json").exists():
        # already migrated
        return
    dest.mkdir(parents=True, exist_ok=True)
    (dest / "media").mkdir(exist_ok=True)
    (dest / "uploads").mkdir(exist_ok=True)
    old_media = ROOT / "media"
    old_uploads = ROOT / "uploads"
    # move files
    if old_media.exists():
        for f in old_media.iterdir():
            shutil.move(str(f), str(dest / "media" / f.name))
    if old_uploads.exists():
        for f in old_uploads.iterdir():
            shutil.move(str(f), str(dest / "uploads" / f.name))
    # rewrite media src to project-relative
    for m in p.get("media", []):
        src = m.get("src", "")
        if "/media/" in src:
            m["src"] = "media/" + src.split("/media/")[-1]
        elif "/uploads/" in src:
            m["src"] = "uploads/" + src.split("/uploads/")[-1]
    p["slug"] = slug
    (dest / "project.json").write_text(json.dumps(p, indent=2))
    # remove the old project file (keep the now-empty dirs for safety)
    try:
        old_proj.unlink()
    except Exception:
        pass
    set_active(slug)


def backfill_thumbs():
    """One-time: generate poster frames for video media that lack them."""
    with lock:
        proj = load_project()
        changed = False
        pdir = proj_dir(proj["slug"])
        for m in proj["media"]:
            if m["kind"] == "video" and not m.get("thumb"):
                uid = Path(m["src"]).name
                tdir = pdir / "media" / "thumbs"
                tdir.mkdir(parents=True, exist_ok=True)
                tpath = tdir / f"{uid}.jpg"
                try:
                    if nle.make_thumb(abs_media(proj, m), tpath):
                        m["thumb"] = f"/media/thumbs/{uid}.jpg"
                        changed = True
                except Exception:
                    pass
        if changed:
            save_project(proj)

def backfill_scene_frames():
    """One-time: give existing generated scenes their first/last frame stills."""
    with lock:
        proj = load_project()
        changed = False
        pdir = proj_dir(proj["slug"])
        tdir = pdir / "media" / "thumbs"
        tdir.mkdir(parents=True, exist_ok=True)
        for sc in proj["scenes"]:
            m = next((x for x in proj["media"] if x["id"] == sc.get("mediaId")), None)
            if not m:
                continue
            src = abs_media(proj, m)
            if not src.exists():
                continue
            if not sc.get("first_frame"):
                if nle.extract_frame(src, tdir / f"gen-{sc['id']}-first.jpg", "first"):
                    sc["first_frame"] = f"/media/thumbs/gen-{sc['id']}-first.jpg"
                    changed = True
            if not sc.get("last_frame"):
                if nle.extract_frame(src, tdir / f"gen-{sc['id']}-last.jpg", "last"):
                    sc["last_frame"] = f"/media/thumbs/gen-{sc['id']}-last.jpg"
                    changed = True
        if changed:
            save_project(proj)


if __name__ == "__main__":
    migrate_legacy_project()
    backfill_thumbs()
    backfill_scene_frames()
    recover_queue(load_project())
    print(f"OpenMagia on http://localhost:{PORT}  (h3: {H3_BIN}, model: {H3_MODEL})")
    ThreadingHTTPServer(("127.0.0.1", PORT), Handler).serve_forever()
