#!/usr/bin/env bash
# OpenMagia installer — idempotent. Safe to re-run; it skips what exists.
#
# What it does:
#   1. Locate (or build) the h3 engine from h3.c
#   2. Locate (or download) the MiniMax-H3 model checkpoints
#   3. Write config.json pointing OpenMagia at both
#
# The model is kept OUTSIDE the OpenMagia folder by default, so deleting
# OpenMagia never touches the (large) model. Override with -m MODEL_ROOT.
set -euo pipefail

SCRIPT_DIR="${OPENMAGIA_ROOT:-$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)}"

# Standard layout: everything lives inside the OpenMagia folder.
#   OpenMagia/h3.c            the engine
#   OpenMagia/models/MiniMax-H3   FL2VA/ + Ref2VA/
H3C_DIR="${H3C_DIR:-$SCRIPT_DIR/h3.c}"
MODEL_ROOT="${MODEL_ROOT:-$SCRIPT_DIR/models/MiniMax-H3}"
HF_REPO="${HF_REPO:-MiniMaxAI/MiniMax-H3}"
WANT_REF2VA=1
WANT_FORMATTER=1
WANT_MODELS=1
WANT_H3=1
UPDATE_SOURCES=0
LLAMA_DIR="${LLAMA_DIR:-$SCRIPT_DIR/addons/llama.cpp}"
FORMATTER_DIR="${FORMATTER_DIR:-$SCRIPT_DIR/addons/models/qwen2.5-7b}"
FORMATTER_MODEL="$FORMATTER_DIR/qwen2.5-7b-instruct-q4_k_m-00001-of-00002.gguf"
FORMATTER_MODEL_SECOND="$FORMATTER_DIR/qwen2.5-7b-instruct-q4_k_m-00002-of-00002.gguf"
FORMATTER_REPO="${FORMATTER_REPO:-Qwen/Qwen2.5-7B-Instruct-GGUF}"
HF_VENV="${HF_VENV:-$SCRIPT_DIR/addons/huggingface-cli}"
HF_COMMAND=""
BUILD_VENV="${BUILD_VENV:-$SCRIPT_DIR/addons/build-tools}"
CMAKE_COMMAND=""
FFMPEG_VENV="${FFMPEG_VENV:-$SCRIPT_DIR/addons/ffmpeg/runtime}"
FFMPEG_BIN_DIR="${FFMPEG_BIN_DIR:-$SCRIPT_DIR/addons/ffmpeg/bin}"
# YuE 2 (music) is opt-in: it needs Linux + an NVIDIA GPU with BF16 support.
WANT_YUE=0
WANT_YUE_WEIGHTS=1
YUE_DIR="${YUE_DIR:-$SCRIPT_DIR/addons/yue}"
YUE_SRC_DIR="${YUE_SRC_DIR:-$YUE_DIR/YuE}"
YUE_VENV="${YUE_VENV:-$YUE_DIR/runtime}"
YUE_MODEL="${YUE_MODEL:-m-a-p/YuE2-3B}"
YUE_VAE="${YUE_VAE:-m-a-p/YuE2-Vae}"

usage() {
  cat <<USAGE
OpenMagia installer
  -h3 DIR    h3.c checkout (default: $H3C_DIR)
  -m DIR     model root holding FL2VA/ and Ref2VA/ (default: ./models/MiniMax-H3)
  -r REPO    Hugging Face repo (default: $HF_REPO)
  --no-ref2va  skip the 144 GB Ref2VA download (FL2VA-only, first-frame mode)
  --no-formatter  skip the ~4.7 GB local prompt formatter and llama.cpp runtime
  --no-models     skip MiniMax H3 checkpoint downloads
  --no-h3         skip downloading and building the H3 engine
  --update-sources  check configured upstream sources and download changed files
  --with-yue      install the YuE 2 music runtime (Apple Silicon MPS or NVIDIA;
                  ~8 GB of weights; --no-yue-weights skips the download)
  --no-yue-weights  with --with-yue, install the runtime but fetch no checkpoint
  -h         this help
USAGE
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    -h3) H3C_DIR="$2"; shift 2;;
    -m)  MODEL_ROOT="$2"; shift 2;;
    -r)  HF_REPO="$2"; shift 2;;
    --no-ref2va) WANT_REF2VA=0; shift;;
    --no-formatter) WANT_FORMATTER=0; shift;;
    --no-models) WANT_MODELS=0; shift;;
    --no-h3) WANT_H3=0; shift;;
    --update-sources) UPDATE_SOURCES=1; shift;;
    --with-yue) WANT_YUE=1; shift;;
    --no-yue-weights) WANT_YUE_WEIGHTS=0; shift;;
    -h|--help) usage; exit 0;;
    *) echo "unknown arg: $1"; usage; exit 1;;
  esac
done

if [[ -t 1 ]]; then LOG_PREFIX=$'\033[1;32m[openmagia]\033[0m'; WARN_PREFIX=$'\033[1;33m[openmagia]\033[0m'; else LOG_PREFIX='[openmagia]'; WARN_PREFIX='[openmagia]'; fi
if [[ -t 2 ]]; then ERROR_PREFIX=$'\033[1;31m[openmagia]\033[0m'; else ERROR_PREFIX='[openmagia]'; fi
log()  { printf '%s %s\n' "$LOG_PREFIX" "$*"; }
warn() { printf '%s %s\n' "$WARN_PREFIX" "$*"; }
err()  { printf '%s %s\n' "$ERROR_PREFIX" "$*" >&2; }

have() { command -v "$1" >/dev/null 2>&1; }

ensure_hf_cli() {
  [[ -n "$HF_COMMAND" ]] && return
  if have hf; then
    HF_COMMAND="$(command -v hf)"
    return
  fi
  if have huggingface-cli; then
    HF_COMMAND="$(command -v huggingface-cli)"
    return
  fi
  if [[ -x "$HF_VENV/bin/hf" ]]; then
    HF_COMMAND="$HF_VENV/bin/hf"
    return
  fi
  if [[ -x "$HF_VENV/bin/huggingface-cli" ]]; then
    HF_COMMAND="$HF_VENV/bin/huggingface-cli"
    return
  fi
  have python3 || { err "Python 3 is required to install the model downloader"; exit 1; }
  log "installing the Hugging Face downloader locally ..."
  if [[ ! -x "$HF_VENV/bin/python" ]]; then
    python3 -m venv "$HF_VENV"
  fi
  "$HF_VENV/bin/python" -m pip install --disable-pip-version-check --upgrade "huggingface_hub[cli]"
  if [[ -x "$HF_VENV/bin/hf" ]]; then
    HF_COMMAND="$HF_VENV/bin/hf"
  elif [[ -x "$HF_VENV/bin/huggingface-cli" ]]; then
    HF_COMMAND="$HF_VENV/bin/huggingface-cli"
  else
    err "Hugging Face downloader installation did not provide a CLI"
    exit 1
  fi
}

ensure_cmake() {
  [[ -n "$CMAKE_COMMAND" ]] && return
  if have cmake; then
    CMAKE_COMMAND="$(command -v cmake)"
    return
  fi
  if [[ -x "$BUILD_VENV/bin/cmake" ]]; then
    CMAKE_COMMAND="$BUILD_VENV/bin/cmake"
    return
  fi
  have python3 || { err "Python 3 is required to install the formatter build tools"; exit 1; }
  log "installing CMake locally for the prompt formatter ..."
  if [[ ! -x "$BUILD_VENV/bin/python" ]]; then
    python3 -m venv "$BUILD_VENV"
  fi
  "$BUILD_VENV/bin/python" -m pip install --disable-pip-version-check --upgrade "cmake>=3.24,<4"
  [[ -x "$BUILD_VENV/bin/cmake" ]] || { err "local CMake installation failed"; exit 1; }
  CMAKE_COMMAND="$BUILD_VENV/bin/cmake"
}

hf_download() {
  ensure_hf_cli
  "$HF_COMMAND" download "$@"
}

ensure_ffmpeg() {
  if have ffmpeg; then
    log "FFmpeg found: $(command -v ffmpeg)"
  elif [[ -x "$FFMPEG_BIN_DIR/ffmpeg" ]]; then
    log "managed FFmpeg found: $FFMPEG_BIN_DIR/ffmpeg"
  else
    have python3 || { err "Python 3 is required to install the managed FFmpeg runtime"; exit 1; }
    log "installing the managed FFmpeg runtime ..."
    [[ -x "$FFMPEG_VENV/bin/python" ]] || python3 -m venv "$FFMPEG_VENV"
    "$FFMPEG_VENV/bin/python" -m pip install --disable-pip-version-check --upgrade "imageio-ffmpeg>=0.6,<0.7"
    mkdir -p "$FFMPEG_BIN_DIR"
    bundled="$($FFMPEG_VENV/bin/python -c 'import imageio_ffmpeg; print(imageio_ffmpeg.get_ffmpeg_exe())')"
    [[ -x "$bundled" ]] || { err "managed FFmpeg installation did not provide an executable"; exit 1; }
    ln -sf "$bundled" "$FFMPEG_BIN_DIR/ffmpeg"
    [[ -x "$FFMPEG_BIN_DIR/ffmpeg" ]] || { err "could not activate managed FFmpeg"; exit 1; }
    log "managed FFmpeg ready: $FFMPEG_BIN_DIR/ffmpeg"
  fi
  if have ffprobe; then
    log "FFprobe found: $(command -v ffprobe)"
  elif [[ -x "$SCRIPT_DIR/ffprobe_compat.py" ]]; then
    log "bundled FFprobe-compatible inspector ready: $SCRIPT_DIR/ffprobe_compat.py"
  else
    err "FFprobe is unavailable and the bundled compatibility inspector is missing"
    exit 1
  fi
}

# H3 encodes every generation through FFmpeg. Treat it as an installed
# component, not an undocumented host prerequisite.
ensure_ffmpeg

# --- 1. h3 engine -----------------------------------------------------------
H3_BIN="$H3C_DIR/h3"
if [[ "$WANT_H3" -eq 0 ]]; then
  log "skipping H3 engine"
elif [[ -x "$H3_BIN" && "$UPDATE_SOURCES" -eq 0 ]]; then
  log "h3 engine found: $H3_BIN"
else
  if [[ ! -d "$H3C_DIR" ]]; then
    have git || { err "git is required to install the H3 engine"; exit 1; }
    log "downloading the H3 engine ..."
    git clone --depth 1 https://github.com/antirez/h3.c.git "$H3C_DIR"
  fi
  if [[ "$UPDATE_SOURCES" -eq 1 && -d "$H3C_DIR/.git" ]]; then
    log "checking the H3 engine source for updates ..."
    git -C "$H3C_DIR" pull --ff-only
  fi
  have make || { err "make is required to build h3"; exit 1; }
  log "building h3 from $H3C_DIR ..."
  ( cd "$H3C_DIR" && make -j8 )
  [[ -x "$H3_BIN" ]] || { err "build did not produce $H3_BIN"; exit 1; }
  log "h3 engine built: $H3_BIN"
fi

# --- 2. lightweight local prompt formatter ----------------------------------
FORMATTER_BIN="$LLAMA_DIR/build/bin/llama-cli"
if [[ "$WANT_FORMATTER" -eq 1 ]]; then
  if [[ "$UPDATE_SOURCES" -eq 1 && -d "$LLAMA_DIR/.git" ]]; then
    log "checking the prompt runtime source for updates ..."
    git -C "$LLAMA_DIR" pull --ff-only
  fi
  if [[ ! -x "$FORMATTER_BIN" || "$UPDATE_SOURCES" -eq 1 ]]; then
    if [[ ! -d "$LLAMA_DIR/.git" ]]; then
      have git || { err "git is required to install the local formatter"; exit 1; }
      log "cloning llama.cpp prompt runtime ..."
      git clone --depth 1 https://github.com/ggml-org/llama.cpp.git "$LLAMA_DIR"
    fi
    ensure_cmake
    log "building lightweight prompt formatter runtime ..."
    "$CMAKE_COMMAND" -S "$LLAMA_DIR" -B "$LLAMA_DIR/build" -DGGML_METAL=ON -DLLAMA_BUILD_TESTS=OFF -DLLAMA_BUILD_EXAMPLES=ON -DCMAKE_BUILD_TYPE=Release
    "$CMAKE_COMMAND" --build "$LLAMA_DIR/build" --config Release -j8 --target llama-cli
  fi
  if [[ ! -f "$FORMATTER_MODEL" || ! -f "$FORMATTER_MODEL_SECOND" || "$UPDATE_SOURCES" -eq 1 ]]; then
    mkdir -p "$FORMATTER_DIR"
    log "checking Qwen2.5 7B Q4_K_M files ..."
    hf_download "$FORMATTER_REPO" "qwen2.5-7b-instruct-q4_k_m-00001-of-00002.gguf" --local-dir "$FORMATTER_DIR"
    hf_download "$FORMATTER_REPO" "qwen2.5-7b-instruct-q4_k_m-00002-of-00002.gguf" --local-dir "$FORMATTER_DIR"
  fi
  [[ -x "$FORMATTER_BIN" && -f "$FORMATTER_MODEL" && -f "$FORMATTER_MODEL_SECOND" ]] && log "local prompt formatter ready" || warn "formatter incomplete; deterministic H3 formatting remains available"
fi

# --- 3. config.json (early, so the app works while checkpoints download) ----
cat > "$SCRIPT_DIR/config.json" <<CFG
{
  "h3_bin": "$H3_BIN",
  "model_root": "$MODEL_ROOT",
  "formatter_bin": "$FORMATTER_BIN",
  "formatter_model": "$FORMATTER_MODEL",
  "port": 8730
}
CFG
log "wrote $SCRIPT_DIR/config.json"

# --- 4. model checkpoints ---------------------------------------------------
mkdir -p "$MODEL_ROOT"
log "note: re-running this script resumes any interrupted download"
fl2va_ok=0; ref2va_ok=0
[[ -f "$MODEL_ROOT/FL2VA/transformer/config.json" ]] && fl2va_ok=1
[[ -f "$MODEL_ROOT/Ref2VA/transformer/config.json" ]] && ref2va_ok=1

if [[ "$WANT_MODELS" -eq 0 ]]; then
  log "skipping MiniMax H3 checkpoints"
elif [[ "$fl2va_ok" -eq 1 ]]; then
  log "FL2VA checkpoint present"
else
  log "downloading FL2VA (~134 GB) from $HF_REPO ..."
  hf_download "$HF_REPO" --include "FL2VA/*" --local-dir "$MODEL_ROOT"
  [[ -f "$MODEL_ROOT/FL2VA/transformer/config.json" ]] || { err "FL2VA download incomplete"; exit 1; }
  log "FL2VA checkpoint ready"
fi

if [[ "$WANT_MODELS" -eq 1 && "$WANT_REF2VA" -eq 1 && "$ref2va_ok" -eq 0 ]]; then
  log "downloading Ref2VA (~144 GB) from $HF_REPO ..."
  hf_download "$HF_REPO" --include "Ref2VA/*" --local-dir "$MODEL_ROOT"
  [[ -f "$MODEL_ROOT/Ref2VA/transformer/config.json" ]] && ref2va_ok=1
  [[ "$ref2va_ok" -eq 1 ]] && log "Ref2VA checkpoint ready" || { err "Ref2VA download incomplete"; exit 1; }
elif [[ "$ref2va_ok" -eq 1 ]]; then
  log "Ref2VA checkpoint present"
fi

# --- 5. YuE 2 music runtime (opt-in) ----------------------------------------
# Upstream validates Linux + NVIDIA + 24 GB, and the runtime itself resolves
# cuda -> mps -> cpu, so this also installs on Apple Silicon (measured: MPS,
# torch-eager, 23-26 semantic tokens/s). Weights are 7.3 GB + 0.53 GB and resolve
# from the Hugging Face cache; downloading them here is a warm-up, not a move.
if [[ "$WANT_YUE" -eq 0 ]]; then
  log "skipping the YuE 2 music runtime (add --with-yue to make music here)"
else
  have git || { err "git is required to install the YuE 2 runtime"; exit 1; }
  if [[ ! -f "$YUE_SRC_DIR/pyproject.toml" ]]; then
    log "downloading the YuE 2 runtime ..."
    git clone --depth 1 https://github.com/multimodal-art-projection/YuE.git "$YUE_SRC_DIR"
  elif [[ "$UPDATE_SOURCES" -eq 1 && -d "$YUE_SRC_DIR/.git" ]]; then
    log "checking the YuE 2 runtime source for updates ..."
    git -C "$YUE_SRC_DIR" pull --ff-only
  elif [[ "$UPDATE_SOURCES" -eq 1 ]]; then
    # Older managed installs were copied without Git metadata. Refresh through
    # a complete temporary checkout so a failed download leaves the live source.
    YUE_REFRESH="$YUE_DIR/YuE.refresh"
    YUE_PREVIOUS="$YUE_DIR/YuE.previous"
    rm -rf "$YUE_REFRESH" "$YUE_PREVIOUS"
    log "refreshing the YuE 2 runtime source ..."
    git clone --depth 1 https://github.com/multimodal-art-projection/YuE.git "$YUE_REFRESH"
    mv "$YUE_SRC_DIR" "$YUE_PREVIOUS"
    mv "$YUE_REFRESH" "$YUE_SRC_DIR"
    rm -rf "$YUE_PREVIOUS"
  fi
  if [[ ! -x "$YUE_VENV/bin/python" ]]; then
    # The upstream quickstart uses 3.12; anything 3.10+ imports fine.
    if have uv; then
      log "creating the YuE 2 environment with uv ..."
      uv venv --python 3.12 "$YUE_VENV"
    else
      YUE_PYTHON=""
      for candidate in python3.12 python3.11 python3.10 python3; do
        if have "$candidate" && "$candidate" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)'; then
          YUE_PYTHON="$candidate"; break
        fi
      done
      [[ -n "$YUE_PYTHON" ]] || { err "YuE 2 needs CPython 3.10+ (3.12 recommended), or install uv"; exit 1; }
      log "creating the YuE 2 environment with $YUE_PYTHON ..."
      "$YUE_PYTHON" -m venv "$YUE_VENV"
    fi
  fi
  if [[ ! -x "$YUE_VENV/bin/yue2" || "$UPDATE_SOURCES" -eq 1 ]] || ! "$YUE_VENV/bin/python" -c 'import peft' 2>/dev/null; then
    log "installing the YuE 2 runtime (torch + transformers, a few GB) ..."
    if have uv; then
      uv pip install --python "$YUE_VENV/bin/python" "$YUE_SRC_DIR" "peft>=0.19.1,<0.20"
    else
      "$YUE_VENV/bin/python" -m pip install --disable-pip-version-check --upgrade pip
      "$YUE_VENV/bin/python" -m pip install --disable-pip-version-check "$YUE_SRC_DIR" "peft>=0.19.1,<0.20"
    fi
  else
    log "YuE 2 runtime present: $YUE_VENV/bin/yue2"
  fi
  [[ -x "$YUE_VENV/bin/yue2" ]] || { err "YuE 2 installation did not provide the yue2 CLI"; exit 1; }
  # Ask the runtime, never guess: this prints the device the machine really has.
  YUE_DOCTOR="$("$YUE_VENV/bin/yue2" doctor --device auto 2>/dev/null || true)"
  if printf '%s' "$YUE_DOCTOR" | grep -q '"name"'; then YUE_DEVICE="cuda (see yue2 doctor)"
  elif printf '%s' "$YUE_DOCTOR" | grep -q '"mps_available": true'; then YUE_DEVICE="mps (Apple Silicon)"
  elif printf '%s' "$YUE_DOCTOR" | grep -q '"torch"'; then YUE_DEVICE="cpu"
  else YUE_DEVICE=""; fi
  [[ -n "$YUE_DEVICE" ]] || warn "YuE 2 doctor reported no usable device; the runtime will still try cuda -> mps -> cpu"
  [[ -n "$YUE_DEVICE" ]] && log "YuE 2 will run on: $YUE_DEVICE"
  if [[ "$WANT_YUE_WEIGHTS" -eq 1 ]]; then
    log "downloading the $YUE_MODEL song model (~7.3 GB) ..."
    hf_download "$YUE_MODEL" || warn "$YUE_MODEL download incomplete; the runtime fetches it on first use"
    log "downloading the $YUE_VAE decoder (~0.53 GB) ..."
    hf_download "$YUE_VAE" || warn "$YUE_VAE download incomplete; the runtime fetches it on first use"
  else
    log "skipping YuE 2 weights (--with-yue fetches them unless --no-yue-weights is given)"
  fi
  log "Music is ready to use from OpenMagia: Settings > Models selects this runtime,"
  log "and the app starts the worker itself. To serve songs from another machine instead:"
  log "  YUE_WORKER_TOKEN=\"pick-a-secret\" $YUE_VENV/bin/python \"$SCRIPT_DIR/yue_worker.py\" --host 0.0.0.0"
fi

echo
log "Done."
[[ "$fl2va_ok" -eq 1 ]] || err "FL2VA missing — OpenMagia cannot generate yet."
[[ "$ref2va_ok" -eq 1 ]] && log "Character mode: Ref2VA ordered references (best identity)." \
                         || warn "Character mode: first-frame anchoring (install Ref2VA for stronger identity)."
log "Start it with:  python3 \"$SCRIPT_DIR/server.py\""
