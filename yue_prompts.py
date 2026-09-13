"""YuE2 music request compilation, validation, and progress parsing.

YuE2 (https://github.com/multimodal-art-projection/YuE) accepts exactly one
request shape: ``id``, ``style`` (alias ``tags``), ``lyrics``, ``cot``,
``abc``, ``seed`` and ``cfg_scale``. It has no duration, tempo, reference
audio, negative-prompt, or instrumental argument. This module is the only
place OpenMagia turns a composer's fields into that shape, so the product can
never offer a control the model silently ignores. Correctness never depends
on model output, exactly like ``h3_prompts.py`` for video.
"""
from __future__ import annotations

import hashlib
import json
import re

YUE_MODEL_ID = "m-a-p/YuE2-3B"
YUE_VAE_ID = "m-a-p/YuE2-Vae"
SAMPLE_RATE = 48000
MAX_STYLE_CHARS = 900
MAX_LYRICS_CHARS = 6000
MAX_ABC_CHARS = 20000
MAX_SONG_SECONDS = 480

PLAN_MODES = {
    "full": {"label": "Full plan", "detail": "YuE2 writes melody and chords, then realizes them. Editable ABC."},
    "melody": {"label": "Melody plan", "detail": "Melody only, free accompaniment. Recommended for covers."},
    "off": {"label": "Direct", "detail": "No symbolic plan; straight from style and lyrics."},
}

# The composer's guided fields. Each maps to prose inside YuE2's single
# ``style`` string, which is the only conditioning surface it exposes.
MUSIC_GUIDE_FIELDS = [
    {"id": "language", "label": "Language", "placeholder": "English, Mandarin, instrumental"},
    {"id": "genre", "label": "Genre", "placeholder": "piano pop, orchestral hybrid, lo-fi"},
    {"id": "tempo", "label": "Tempo", "placeholder": "88 BPM, unhurried, driving"},
    {"id": "instruments", "label": "Instruments", "placeholder": "acoustic piano, rounded bass, light drums"},
    {"id": "voice", "label": "Voice", "placeholder": "warm expressive female lead"},
    {"id": "mood", "label": "Mood", "placeholder": "hopeful, late-night, cinematic"},
    {"id": "structure", "label": "Structure", "placeholder": "verse, chorus, short instrumental bridge"},
]

SECTION_TAG = re.compile(r"^\s*\[([^\]\n]{1,40})\]\s*$")
# Lines that are production notes rather than words to sing. They must never
# reach the model as lyrics, because YuE2 would try to sing them.
NOTE_LINE = re.compile(r"(?i)^\s*(?:note|notes|todo|bpm|key|time\s*signature|producer|prompt|style|tags|description)\s*[:=]")
MARKER_LINE = re.compile(r"^\s*(?:```+|---+|#+\s|\*\*[^*]+\*\*\s*$)")
BRACKET_LINE = re.compile(r"^\s*\[([^\]\n]{1,60})\]\s*$")
# Musical facts people write next to a lyric. The request has no BPM/key/meter
# field, so these move into the style prose instead of vanishing or being sung.
METADATA_LINE = re.compile(r"(?i)^\s*(bpm|tempo|key|tonality|time\s*signature|meter|signature)\s*[:=]\s*(.+)$")
SECTION_ORDINALS = {"1", "2", "3", "4", "5", "i", "ii", "iii", "iv", "v", "a", "b", "c", "final", "last"}
SECTION_WORDS = {
    "intro", "verse", "prechorus", "pre-chorus", "chorus", "refrain", "postchorus", "post-chorus",
    "bridge", "interlude", "solo", "breakdown", "outro", "hook", "inst", "instrumental", "ending",
    "verse 1", "verse 2", "chorus 1", "chorus 2",
}
INSTRUMENTAL_SECTIONS = {"instrumental", "inst", "solo", "interlude", "breakdown"}
# YuE has no instrumental switch. Keep the lyric shape minimal and let the chosen
# symbolic plan provide a bounded composition; direct generation has been observed
# to stream past 6,000 tokens without emitting an end token for ordinary prompts.
INSTRUMENTAL_LYRICS = "[Instrumental]"
SEMANTIC_TOKENS_PER_SECOND = 23.0        # measured: 1011 semantic tokens -> 43.5 s of audio
INSTRUMENTAL_STYLE_TAG = "instrumental arrangement, no lead vocal, no sung words"


def clean_text(value):
    """Collapse a free-text field into one comma-separated style fragment."""
    text = re.sub(r"\s+", " ", str(value or "")).strip().strip(",")
    return text


def metadata_fragment(head, value):
    """Turn `BPM: 88` or `Key: D minor` into a style-tag fragment."""
    text = re.sub(r"\s+", " ", str(value or "")).strip().rstrip(".;,")
    if not text:
        return ""
    head = str(head or "").strip().lower()
    if head in ("bpm", "tempo"):
        number = re.match(r"^([\d.]+)\s*(-|–|to)?\s*(\d+)?$", text)
        if head == "tempo" and not re.search(r"\d", text):
            return text                                   # "downtempo", "rubato", plain words
        if number and number.group(1):
            low = number.group(1).rstrip("0.")
            high = (number.group(3) or "").rstrip("0.")
            return (low + "-${0} BPM".format(high)) if high else low + " BPM"
        return text
    if head in ("time signature", "meter", "signature"):
        return text if " " in text or "time" in text.lower() else text + " time"
    return text                                          # key, tonality


def section_label(text):
    """True when a bracket-only line labels a section, e.g. ``[Verse]``, ``[Chorus 2]``, ``[Guitar solo]``.

    Section tags are the only structural control YuE 2 accepts, so they are
    part of the lyric and are never stripped as scaffolding.
    """
    match = BRACKET_LINE.match(str(text or ""))
    if not match:
        return False
    inner = re.sub(r"[^a-z0-9 ]+", " ", match.group(1).lower())
    words = [word for word in inner.split() if word not in SECTION_ORDINALS]
    if not words:
        return False
    joined = " ".join(words)
    return joined in SECTION_WORDS or any(
        re.search(r"\b" + re.escape(word) + r"\b", joined) for word in SECTION_WORDS)


def normalize_lyrics(value, report=None):
    """Normalize whitespace and drop scaffolding, never words.

    Line content is preserved verbatim: capitalization, punctuation, section
    tags, and line order are the lyricist's, and OpenMagia must not silently
    rewrite them. Anything removed is appended to ``report`` so the audit can
    say what left the lyric rather than losing it quietly.
    """
    text = str(value or "").replace("\r\n", "\n").replace("\r", "\n")
    lines, kept = text.split("\n"), []
    def drop(reason, line=""):
        if report is not None:
            report.append({"reason": reason, "line": line})
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("```"):
            drop("markdown fence", stripped)      # the fence goes, the words inside stay
            continue
        if not stripped:
            if kept and kept[-1] != "":
                kept.append("")
            continue
        if NOTE_LINE.match(stripped):
            drop("production note", stripped)
            continue
        if MARKER_LINE.match(stripped):
            drop("markdown scaffolding", stripped)
            continue
        if BRACKET_LINE.match(stripped) and not section_label(stripped):
            drop("stage direction", stripped)      # e.g. [sad, whispered] - YuE would sing it
            continue
        kept.append(stripped)
    while kept and kept[0] == "":
        kept.pop(0)
    while kept and kept[-1] == "":
        kept.pop()
    return "\n".join(kept)


def lyric_sections(lyrics):
    """Return the section tags present in normalized lyrics."""
    return [match.group(1).strip() for line in str(lyrics or "").split("\n")
            if (match := SECTION_TAG.match(line))]


def lyric_lines(lyrics):
    """Sung lines only, ignoring section tags and blanks."""
    out = []
    for line in str(lyrics or "").split("\n"):
        stripped = line.strip()
        if not stripped or SECTION_TAG.match(stripped):
            continue
        out.append(stripped)
    return out


def estimate_song_band(lyrics):
    """Rough (low, high) seconds. This is a band, not a prediction.

    Measured on MPS: a four-line lyric rendered 54.8 s while an eight-line lyric
    rendered 43.5 s. YuE 2 has no duration argument and its own arrangement
    decides the length, so anything narrower than a band would be theatre. Use it
    for 'roughly how long' wording, never as a promise or a progress denominator.
    """
    lines = lyric_lines(lyrics)
    sections = lyric_sections(lyrics)
    if not lines:
        return 0.0, 0.0
    low = len(lines) * 4.0 + max(len(sections), 1) * 3.0
    high = len(lines) * 16.0 + max(len(sections), 1) * 8.0
    return (round(min(MAX_SONG_SECONDS, max(20.0, low)), 1),
            round(min(MAX_SONG_SECONDS, max(60.0, high)), 1))


def estimate_song_seconds(lyrics):
    """Midpoint of estimate_song_band, kept for callers that want one number."""
    low, high = estimate_song_band(lyrics)
    return round((low + high) / 2, 1) if high else 0.0


def sanitize_id(value):
    slug = re.sub(r"[^a-z0-9]+", "_", str(value or "").strip().lower()).strip("_")
    if not slug:
        slug = "song"
    return slug[:48]


def compile_style_tags(*, style="", answers=None, skill_direction="", instrumental=False, language=""):
    """Build YuE2's single ``style`` string from the composer's fields.

    Order is fixed so the same composer state always produces the same request
    and a seeded comparison stays meaningful.
    """
    answers = answers or {}
    fragments = []
    for field in ("language", "genre", "tempo", "mood", "instruments", "voice", "structure"):
        value = clean_text(answers.get(field))
        if value:
            fragments.append(value)
    if clean_text(language) and clean_text(language) not in fragments:
        fragments.insert(0, clean_text(language))
    idea = clean_text(style)
    if idea:
        fragments.append(idea)
    direction = clean_text(skill_direction)
    if direction:
        fragments.append(direction)
    if instrumental and INSTRUMENTAL_STYLE_TAG not in fragments:
        fragments.append(INSTRUMENTAL_STYLE_TAG)
    # Keep the distinctive direction first: a long idea must not crowd it out.
    out, total = [], 0
    for fragment in fragments:
        room = MAX_STYLE_CHARS - total
        if room <= 12:
            break
        piece = fragment if len(fragment) <= room else fragment[:room - 1].rstrip() + "…"
        out.append(piece)
        total += len(piece) + 2
    return ", ".join(out)


def normalize_abc(value):
    text = str(value or "").replace("\r\n", "\n").replace("\r", "\n").strip()
    if not text:
        return ""
    return text[:MAX_ABC_CHARS]


def looks_like_abc(value):
    text = str(value or "").strip()
    if not text:
        return False
    if re.search(r"(?m)^\s*[KVTXAMCRLGFQPISH]:", text):
        return True
    return bool(re.search(r"(?m)^\s*(?:V:|M:|Q:|K:)", text))


def format_music_request(*, idea="", lyrics="", answers=None, plan_mode="full", abc="",
                         seed=42, guidance=None, skill_direction="", instrumental=False,
                         song_id="", skill_id=""):
    """Compile one deterministic YuE2 request.

    ``skill_direction`` is the compiled music contract of the selected prompt
    skill; the selected skill therefore survives drafts, saved scenes, and
    refines. Returns the request plus a sidecar OpenMagia keeps for audit.
    """
    mode = str(plan_mode or "full")
    if mode not in PLAN_MODES:
        raise ValueError(f"Unknown music plan mode '{plan_mode}'. Use full, melody, or off.")
    answers = dict(answers or {})
    requested_plan = mode
    lyric_report = []
    clean_lyrics = normalize_lyrics(lyrics, lyric_report)
    idea_text = clean_text(idea)
    kept_out = []
    for item in lyric_report:
        if item.get("reason") != "production note":
            continue
        found = METADATA_LINE.match(item.get("line") or "")
        if not found:
            continue
        fragment = metadata_fragment(found.group(1), found.group(2))
        if fragment and fragment.lower() not in idea_text.lower():
            kept_out.append(fragment)
            item["moved_to"] = "style tags"
    if kept_out:
        idea_text = ", ".join([part for part in [idea_text] + kept_out if part])
    # Sung words and "no lead vocal" are contradictory guidance; words win.
    instrumental = bool(instrumental) and not lyric_lines(clean_lyrics)
    if instrumental and not lyric_sections(clean_lyrics):
        # YuE2 has no instrumental argument. A section tag is the only shape it
        # accepts for "no vocals"; see INSTRUMENTAL_LYRICS for how much structure
        # an endless cue costs, and yue_worker.py --max-tokens for the guard.
        clean_lyrics = INSTRUMENTAL_LYRICS
    requested_plan = mode
    score = normalize_abc(abc)
    # Full planning can create a new melody-and-chord plan without supplied ABC.
    # An ABC input is only needed when the artist wants to provide or preserve a
    # specific composition. Keep the requested mode for new instrumentals too;
    # silently forcing Direct used to discard their planned structure and often
    # produced short, texture-only audio.
    style = compile_style_tags(style=idea_text, answers=answers, skill_direction=skill_direction,
                               instrumental=instrumental)
    try:
        seed_value = int(seed)
    except (TypeError, ValueError):
        seed_value = 42
    request = {
        "id": sanitize_id(song_id or idea or "song"),
        "style": style,
        "lyrics": clean_lyrics,
        "cot": mode,
        "seed": max(0, min(2 ** 31 - 1, seed_value)),
    }
    if score:
        request["abc"] = score
    if guidance not in (None, ""):
        request["cfg_scale"] = float(guidance)
    validate_music_request(request)
    warnings = []
    if instrumental:
        warnings.append({"kind": "duration", "level": "info",
                         "text": ("YuE 2 decides the instrumental length. OpenMagia rejects "
                                  "truncated results rather than keeping a damaged ending.")})
    signature = hashlib.sha256(
        json.dumps({"request": request, "skill": skill_id or ""}, sort_keys=True,
                   ensure_ascii=False).encode("utf-8")).hexdigest()[:12]
    return {
        "request": request,
        "audit": {
            "engine": "yue2",
            "model": YUE_MODEL_ID,
            "plan_mode": mode,
            "plan_mode_requested": requested_plan,
            "warnings": warnings,
            "style": style,
            "sections": lyric_sections(clean_lyrics),
            "sung_lines": len(lyric_lines(clean_lyrics)),
            "estimated_seconds": estimate_song_seconds(clean_lyrics),
            "estimated_band": list(estimate_song_band(clean_lyrics)),
            "instrumental": bool(instrumental),
            "lyric_lines_removed": lyric_report,
            "score_conditioned": bool(score),
            "score_generated": False,
            "skill_id": skill_id or "",
            "signature": signature,
        },
    }


def validate_music_request(request):
    """Reject requests YuE2 would misread or silently degrade."""
    if not isinstance(request, dict):
        raise ValueError("Music request must be an object.")
    mode = request.get("cot")
    if mode not in PLAN_MODES:
        raise ValueError("Music planning mode must be full, melody, or off.")
    style = str(request.get("style") or "").strip()
    if not style:
        raise ValueError("Describe the music first: genre, instruments, voice, or mood.")
    if len(style) > MAX_STYLE_CHARS:
        raise ValueError(f"The style description is too long for reliable guidance ({len(style)} characters).")
    lyrics = str(request.get("lyrics") or "")
    if not lyrics.strip():
        raise ValueError("Add lyrics, or switch to an instrumental with no sung lines.")
    if len(lyrics) > MAX_LYRICS_CHARS:
        raise ValueError("These lyrics are longer than YuE2 can reliably realize. Split them into separate songs.")
    sung = lyric_lines(lyrics)
    sections = lyric_sections(lyrics)
    if sung and not sections:
        raise ValueError("Lyrics need at least one section tag such as [Verse] or [Chorus] before generating.")
    if sung:
        odd = [line for line in sung if re.search(r"(?i)\b\(?(note|todo|placeholder|tbc)\)?\b", line)]
        if odd:
            raise ValueError("Remove production notes from the lyrics: " + odd[0][:80])
    if request.get("abc") and mode == "off":
        raise ValueError("A supplied score needs the Full plan or Melody plan mode, not Direct.")
    if request.get("abc") and not looks_like_abc(request.get("abc")):
        raise ValueError("That score does not look like ABC notation (it needs fields such as K:, M:, or V:).")
    seed = request.get("seed", 0)
    if not isinstance(seed, int) or isinstance(seed, bool) or not (0 <= seed <= 2 ** 31 - 1):
        raise ValueError("The music seed must be a non-negative integer.")
    if "cfg_scale" in request:
        guidance = float(request["cfg_scale"])
        if not (0.5 <= guidance <= 3.0):
            raise ValueError("Text guidance must stay between 0.5 and 3.0.")
        request["cfg_scale"] = guidance
    return request


def parse_yue_progress(line):
    """Translate one YuE2 stderr line into OpenMagia progress state.

    Upstream writes ``[YuE2] Running <label>: 512/4096 tokens (12%) | ...`` on
    a pipe, plus ``[YuE2] Completed: 187.3s audio in 240.1s`` at the end. When
    the shape is unknown this returns ``None`` so the caller keeps the last
    known phase instead of inventing progress.
    """
    text = re.sub(r"\x1b\[[0-9;]*m", "", str(line or "")).strip()
    if not text.startswith("[YuE2]"):
        return None
    body = text[len("[YuE2]"):].strip()
    # The one line that states what was actually made: "Completed: 36.0s audio in 49.7s".
    final = re.match(r"^(?P<verb>Completed|Limit reached|Finished):\s*(?P<audio>[\d.]+)s\s+audio\s+in\s+(?P<wall>[\d.]+)s", body)
    if final:
        return {"phase": "Decoding audio", "completed": 1, "total": 1,
                "audio_seconds": float(final.group("audio")), "wall_seconds": float(final.group("wall"))}
    aborted = re.match(r"^(?P<verb>Failed|Cancelled|Error)\b(?::|\s+(?P<label>.+?))?(?:\s*:\s*(?P<payload>.*))?$", body, re.I)
    if aborted:
        label = re.sub(r"\s+", " ", (aborted.group("label") or "")).strip()
        return {"phase": label or body[:60] or "Failed", "completed": 0, "total": 1, "failed": True,
                "detail": (aborted.group("payload") or "").strip()[:300]}
    step = re.match(r"^(?P<verb>Starting|Running|Completed)\s+(?P<label>.+?):\s*"
                    r"(?P<completed>\d+)\s*/\s*(?P<total>\d+)\s*(?P<unit>tokens?|steps?|chunks?)?"
                    r"\s*(?:\((?P<pct>\d+)%\))?(?P<rest>.*)$", body)
    if step:
        out = {"phase": re.sub(r"\s+", " ", step.group("label")).strip() or "Generating",
               "completed": int(step.group("completed")), "total": max(1, int(step.group("total")))}
        _progress_metrics(step.group("rest"), out)
        return out
    counted = re.match(r"^(?P<verb>Starting|Running|Completed)\s+(?P<label>.+?):\s*(?P<payload>.*)$", body)
    if counted:
        label = re.sub(r"\s+", " ", counted.group("label")).strip() or "Generating"
        payload = counted.group("payload")
        out = {"phase": label}
        count = re.match(r"^(\d+)\s*(tokens?|steps?|chunks?)\b", payload)
        if count:
            out["completed"] = int(count.group(1))
            out["total"] = None            # the runtime streams counts with no declared total
        elif verb_done(counted.group("verb")):
            out["completed"], out["total"] = 1, 1
        else:
            out["completed"], out["total"] = 0, 1
        _progress_metrics(payload, out)
        return out
    return {"phase": body[:60] or "Generating"}


def verb_done(verb):
    return str(verb or "").lower() == "completed"


def _progress_metrics(tail, out):
    """Pull the tokens/s and elapsed terms out of a progress line, when present."""
    tail = str(tail or "")
    rate = re.search(r"([\d.]+)\s*tokens/s", tail)
    elapsed = re.search(r"elapsed\s+([\d.]+)s", tail)
    if rate:
        out["tokens_per_second"] = float(rate.group(1))
    if elapsed:
        out["elapsed_seconds"] = float(elapsed.group(1))
    return out


def music_request_summary(request):
    """Short human line for the composer footer and the media card."""
    sung = len(lyric_lines(request.get("lyrics", "")))
    sections = lyric_sections(request.get("lyrics", ""))
    parts = [PLAN_MODES.get(request.get("cot", "full"), {}).get("label", "Full plan")]
    if sung:
        parts.append(f"{sung} sung line{'s' if sung != 1 else ''}")
    if sections:
        parts.append(str(len(sections)) + " section" + ("s" if len(sections) != 1 else ""))
    if request.get("abc"):
        parts.append("score-conditioned")
    return " · ".join(parts)
