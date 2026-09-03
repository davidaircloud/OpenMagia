"""MiniMax H3 prompt templates, validation, and deterministic formatting.

The field names and ordering mirror MiniMax-AI/MiniMax-H3's official
h3-prompt-writing skill.  A local LLM may improve prose before this module
wraps it; correctness never depends on model output.
"""
from __future__ import annotations

import re

FPS = 24
MAX_SECONDS = 15
MAX_FRAMES = FPS * MAX_SECONDS
MAX_REFERENCES = 9
MAX_AUDIO_REFERENCES = 3
MAX_MIXED_REFERENCES = 12

PRESETS = [
    {"id": "motion-graphics", "name": "Motion Graphics", "tagline": "Kinetic type, graphic shapes, beat-snapped cuts", "style": "Premium motion-graphics title sequence with bold kinetic typography, hard-edged geometric shapes, split screens, flat color fields, UI ticks, halftone, speed lines and shutter flashes. Graphic elements move decisively and snap on the beat; preserve subject identity, wardrobe, materials and colors across every shot.", "defaults": {"pacing": "fast", "camera": "snap zooms, whip pans and graphic match cuts", "sound": "impact hits, whooshes and crisp interface ticks", "music": "fast electronic percussion synchronized to every graphic transition"}},
    {"id": "fast-trailer", "name": "Fast Trailer", "tagline": "Escalating action and sharp cinematic reveals", "style": "Premium cinematic trailer with escalating visual scale, high-contrast lighting, tactile atmosphere, decisive compositions and fast clean cuts. Maintain spatial continuity and exact subject identity throughout.", "defaults": {"pacing": "fast", "camera": "dynamic tracking, push-ins and controlled handheld accents", "sound": "cinematic impacts, risers and detailed environmental sound", "music": "driving hybrid orchestral-electronic trailer score"}},
    {"id": "social-reel", "name": "Social Reel", "tagline": "Immediate hook, vertical energy, clean payoff", "style": "Polished short-form social reel with an immediate visual hook, readable central action, punchy transitions, vibrant controlled color and a satisfying final hold. Keep essential subjects inside a vertical-safe central composition.", "defaults": {"pacing": "brisk", "camera": "handheld energy with clean push-ins and match cuts", "sound": "crisp action accents and natural ambience", "music": "modern beat with clear edit points"}},
    {"id": "motion-typography", "name": "Motion Typography", "tagline": "Readable on-screen copy animated with the action", "style": "Typography-led campaign film with large readable on-screen copy, disciplined grids, strong negative space and text animation physically motivated by subject movement. Preserve every supplied word exactly; keep letters unobscured long enough to read.", "defaults": {"pacing": "beat-driven", "camera": "graphic reframing and type-matched transitions", "sound": "type impacts, swishes and tactile clicks", "music": "minimal rhythmic electronic score"}},
    {"id": "cinematic-story", "name": "Cinematic Story", "tagline": "Natural performance and motivated camera", "style": "Cinematic narrative realism with natural performance, motivated camera movement, coherent geography, nuanced lighting, restrained color and detailed production design. Preserve faces, proportions, wardrobe and props continuously.", "defaults": {"pacing": "measured", "camera": "motivated dolly and composed handheld movement", "sound": "layered location ambience and precise physical sounds", "music": "restrained cinematic underscore"}},
    {"id": "anime-action", "name": "Anime Action", "tagline": "Graphic poses, speed lines, expressive impacts", "style": "Premium anime action with stable character design, expressive key poses, clean silhouettes, selective speed lines, impact frames and dynamic perspective. Preserve exact face, hairstyle, outfit construction, accessories and palette throughout.", "defaults": {"pacing": "fast", "camera": "dynamic perspective, tracking and impact reframes", "sound": "stylized impacts, cloth movement and environmental detail", "music": "high-energy anime action score"}},
    {"id": "product-spot", "name": "Product Spot", "tagline": "Material detail, controlled light, hero finish", "style": "Minimal premium product film with immaculate materials, controlled studio lighting, macro detail, deliberate reflections, elegant motion and a clean hero composition. Preserve product geometry, branding, labels and colors exactly.", "defaults": {"pacing": "precise", "camera": "macro glides, turntable arcs and measured push-ins", "sound": "refined tactile foley and subtle mechanical detail", "music": "minimal premium electronic pulse"}},
    {"id": "dreamlike", "name": "Dreamlike", "tagline": "Poetic transitions and atmospheric movement", "style": "Dreamlike cinematic visual poem with soft atmospheric depth, elegant surreal transitions, luminous color separation and fluid camera movement. Keep the subject recognizable and all transformations continuous and physically legible.", "defaults": {"pacing": "flowing", "camera": "slow floating moves and seamless match dissolves", "sound": "airy ambience and delicate environmental textures", "music": "ethereal ambient score"}},
]

def get_preset(preset_id):
    return next((p for p in PRESETS if p["id"] == preset_id), PRESETS[0])

def duration_for_frames(frames):
    frames = max(8, min(MAX_FRAMES, int(frames or 56)))
    return frames, frames / FPS

def count_references(characters):
    return sum(len(c.get("paths", [])) for c in characters)

def validate_references(characters):
    images = sum(len(c.get("paths", [])) for c in characters if c.get("kind") != "audio_reference")
    audio = [c for c in characters if c.get("kind") == "audio_reference"]
    if images > MAX_REFERENCES:
        raise ValueError(f"MiniMax H3 accepts at most {MAX_REFERENCES} ordered reference images; this scene has {images}.")
    if len(audio) > MAX_AUDIO_REFERENCES:
        raise ValueError(f"MiniMax H3 accepts at most {MAX_AUDIO_REFERENCES} audio references; this scene has {len(audio)}.")
    if images + len(audio) > MAX_MIXED_REFERENCES:
        raise ValueError(f"MiniMax H3 accepts at most {MAX_MIXED_REFERENCES} mixed reference files.")
    if audio and not images:
        raise ValueError("Add at least one image or video reference when using audio as an H3 reference.")
    total_audio = sum(float(c.get("duration") or 0) for c in audio)
    for item in audio:
        duration = float(item.get("duration") or 0)
        if duration < 2 or duration > 15:
            raise ValueError(f"Audio reference '{item.get('name') or 'Audio'}' must be between 2 and 15 seconds.")
    if total_audio > 15.001:
        raise ValueError(f"H3 audio references may total at most 15 seconds; the selected clips total {total_audio:.1f} seconds.")
    return images + len(audio)

def _clean(value, fallback):
    value = re.sub(r"\s+", " ", (value or "").strip())
    return value or fallback

def analyze_cut_timeline(value, seconds=None):
    """Validate an authored CUT timeline without rewriting its creative content."""
    text = str(value or "")
    matches = list(re.finditer(
        r"(?i)\bCUT\s+(\d{1,2})\s*\|\s*(\d+(?:\.\d+)?)\s*[\-–—]\s*(\d+(?:\.\d+)?)s?\b", text))
    if not matches:
        return {"present": False, "count": 0, "errors": []}
    errors, prior_end = [], None
    numbers = [int(m.group(1)) for m in matches]
    expected = list(range(1, len(matches) + 1))
    if numbers != expected:
        errors.append("CUT numbers must start at 01 and remain sequential without duplicates.")
    for index, match in enumerate(matches):
        start, end = float(match.group(2)), float(match.group(3))
        if end <= start:
            errors.append(f"CUT {numbers[index]:02d} must end after it starts.")
        if prior_end is not None and abs(start - prior_end) > .06:
            errors.append(f"CUT {numbers[index]:02d} must begin where the previous cut ends.")
        prior_end = end
    if float(matches[0].group(2)) > .06:
        errors.append("CUT 01 must begin at 0.00 seconds.")
    if seconds is not None and prior_end is not None and abs(prior_end - float(seconds)) > .12:
        errors.append(f"The final CUT must end at {float(seconds):.2f} seconds.")
    return {"present": True, "count": len(matches), "errors": list(dict.fromkeys(errors))}

def _timeline(idea, style, setting, camera, pacing, text, seconds, cuts=None, transitions=""):
    """Build official shot notation: Shot 1 has no timestamp; later cuts do."""
    # A refinement pass may already have authored a complete, timed shot plan.
    # Preserve it instead of wrapping it in generic scaffold beats.
    authored = analyze_cut_timeline(idea, seconds)
    if authored["present"]:
        if authored["errors"]:
            raise ValueError("Invalid authored CUT timeline: " + " ".join(authored["errors"]))
        return f"{style} {idea}".strip()
    fast = any(word in pacing.lower() for word in ("fast", "brisk", "beat", "rapid", "trailer"))
    try:
        requested = int(cuts or 0)
    except (TypeError, ValueError):
        requested = 0
    count = max(1, min(15, requested or (3 if fast and seconds >= 4 else 1)))
    if count > 1:
        transition = _clean(transitions, "motivated hard cuts, match cuts, and movement-led transitions")
        parts = []
        for i in range(count):
            start, end = seconds * i / count, seconds * (i + 1) / count
            if i == 0:
                action = f"Establish {setting} and begin the requested action with immediately readable subject and spatial relationships. {idea}"
            elif i == count - 1:
                action = f"Deliver the decisive payoff of the same action and hold a clear final composition. Visible text: {text}."
            else:
                phase = "escalates" if i < count * .67 else "resolves"
                action = f"The action {phase} through a new, visibly distinct composition and observable intermediate movement; preserve continuity from the previous cut."
            marker = f"[Shot 1] {style} " if i == 0 else f"[Shot {i+1}] At 00:{start:06.3f}, "
            parts.append(f"{marker}{action} The camera follows the action using {camera} with {pacing}. Transitions use {transition}. ")
        return "".join(parts).strip()
    return (f"[Shot 1] {style} A clear opening composition establishes {setting}. {idea} "
            f"The camera follows the action using {camera} as it develops continuously through observable intermediate states with {pacing}, "
            f"then settles into a clear final composition by {seconds:.2f} seconds. Visible text: {text}.")

def format_prompt(*, idea, style, frames, mode="t2va", characters=(), answers=None):
    """Return an official-structure H3 prompt for the selected generation mode."""
    answers = answers or {}
    frames, seconds = duration_for_frames(frames)
    validate_references(characters)
    idea = _clean(idea, "A visually clear subject performs one continuous, purposeful action.")
    continuity = _clean(answers.get("continuity"), "")
    if continuity and continuity not in idea:
        # Continuity is an execution constraint, not optional creative prose.
        # Keep it in the deterministic compiler so a formatter-model rewrite
        # cannot accidentally omit the supplied opening-frame state.
        idea = f"{continuity} {idea}"
    skill_instruction = _clean(answers.get("skill_instruction"), "")
    if skill_instruction:
        idea = f"{idea} Production direction: {skill_instruction}"
    style = _clean(style, "Cinematic, visually coherent, with stable subject identity.")
    setting = _clean(answers.get("setting"), "a visually coherent environment that supports the action")
    camera = _clean(answers.get("camera"), "motivated camera movement with clear composition")
    pacing = _clean(answers.get("pacing"), "purposeful pacing")
    text = _clean(answers.get("text"), "No visible text unless explicitly requested")
    sound = _clean(answers.get("sound"), "detailed environmental ambience and synchronized physical action sounds")
    music = _clean(answers.get("music"), "a restrained score synchronized to the visual rhythm")
    end = f"{seconds:.2f}"
    timeline = _timeline(idea, style, setting, camera, pacing, text, seconds, answers.get("cuts"), answers.get("transitions"))

    if mode == "ref2va":
        definitions, retention, pic, audio, subject = [], [], 0, 0, 0
        for char in characters:
            if char.get("kind") == "audio_reference":
                audio += 1
                role = _clean(char.get("description"), "Use this audio as an audiovisual timing and sound reference where requested by the prompt")
                definitions.append(f"<Audio {audio}> is {char['name']}; {role}.")
                retention.append(f"<Audio {audio}>: reference - preserve or adapt its requested music, rhythm, voice timbre, dialogue timing, or sound characteristics without inventing an unrelated role.")
                continue
            subject += 1
            labels = []
            for _ in char.get("paths", []):
                pic += 1; labels.append(f"<Picture {pic}>")
            if labels:
                sources = " and ".join(labels)
                notes = _clean(char.get("description"), "")
                note_text = f" Identity notes: {notes}." if notes else ""
                if char.get("kind") == "continuity_reference":
                    definitions.append(f"<Subject {subject}> is the opening continuity state from {sources}; it is the highest authority for vehicle geometry, cast count and placement, props, environment, lighting, camera axis, travel direction, spatial relationships, and motion state.{note_text}")
                    retention.append(f"<Subject {subject}> (appears in [Shot 1]): fully_preserved - continue as the immediate next moment without redesigning, duplicating, replacing, or resetting any established subject or object.")
                elif char.get("kind") == "visual_reference":
                    definitions.append(f"<Subject {subject}> is {char['name']} from {sources}; use it only for the subject, object, environment, material, palette, label, or design details explicitly requested in the prompt.{note_text}")
                    retention.append(f"<Subject {subject}> (appears where requested): partially_preserved - preserve prompt-relevant geometry, materials, colors, labels, and design details; do not automatically copy framing, pose, background, or camera angle and do not treat it as a character identity.")
                else:
                    definitions.append(f"<Subject {subject}> is {char['name']}, whose visible identity and design come from {sources}; preserve the exact face, proportions, hairstyle, wardrobe, materials, accessories, and colors.{note_text}")
                    retention.append(f"<Subject {subject}> (appears throughout the target video): fully_preserved - identity, proportions, hairstyle, wardrobe, materials, accessories, and colors remain unchanged; reference backgrounds, poses, and framing are not copied unless requested.")
        defs = " ".join(definitions) or "No external subject references are supplied."
        return (f"subject_definitions: {defs}\n\n"
                f"summary: [reference generation] Create a {end}-second audiovisual target video at 24fps. The defined visual and audio references provide identity, design, sound, voice, music, and timing guidance only in the roles explicitly requested by the prompt.\n\n"
                f"retention_analysis: {' '.join(retention)}\n\n"
                f"detailed_description: {timeline}\n\n"
                f"overall_soundscape: {sound}.\n\n"
                f"non_diegetic_music: {music}.")

    prefix = ""
    if mode == "i2va":
        prefix = "For the target video, at 0.00 seconds into the target video, <Picture 1> (from [Shot 1]) is fully referenced.\n\n"
    elif mode == "fl2va":
        prefix = f"How the reference pictures align with the target video — Picture 1 (from Shot 1) aligns with the 0.00-second mark of the target video; Picture 2 (from Shot 1) aligns with the {end}-second mark of the target video.\n\n"
    elif mode == "l2va":
        prefix = f"How the reference pictures align with the target video — <Picture 1> (from [Shot 1]) aligns with the {end}-second mark of the target video.\n\n"
    return (prefix + f"integrated_multimodal_description: {timeline}\n\n"
            f"overall_soundscape: {sound}.\n\n"
            f"non_diegetic_music: {music}.")


def format_image_prompt(*, idea, style="", mode="t2va", characters=(), answers=None):
    """Format a five-frame H3 run as a still-image composition experiment."""
    answers = answers or {}
    validate_references(characters)
    idea = _clean(idea, "A visually clear, precisely composed still image.")
    style = _clean(style, "Visually coherent, detailed, and compositionally precise.")
    setting = _clean(answers.get("setting"), "the environment described or implied by the request")
    camera = _clean(answers.get("camera"), "a deliberate still-image composition with a clearly defined viewpoint and lens character")
    text = _clean(answers.get("text"), "no visible text unless explicitly requested")
    description = (f"{style} Create one finished still image, not a moving sequence. Subject and intent: {idea} "
                   f"Environment: {setting}. Composition and camera: {camera}. Visible text: {text}. "
                   "Hold the same composition across all five decoded frames; no cuts, transitions, camera movement, animation, duplicate subjects, or temporal progression.")
    if mode == "ref2va":
        definitions, retention, pic = [], [], 0
        for subject, char in enumerate(characters, 1):
            labels = []
            for _ in char.get("paths", []):
                pic += 1; labels.append(f"<Picture {pic}>")
            if not labels: continue
            sources = " and ".join(labels)
            notes = _clean(char.get("description"), "")
            note_text = f" Notes: {notes}." if notes else ""
            if char.get("kind") == "visual_reference":
                definitions.append(f"<Reference {subject}> is {char['name']} from {sources}; use only prompt-relevant geometry, materials, palette, labels, or design details.{note_text}")
                retention.append(f"<Reference {subject}>: selectively_preserved - preserve requested design details without copying unrelated framing or background.")
            else:
                definitions.append(f"<Subject {subject}> is {char['name']} from {sources}; preserve exact visible identity, face, proportions, hairstyle, wardrobe, materials, accessories, and colors.{note_text}")
                retention.append(f"<Subject {subject}>: fully_preserved - identity and design remain exact in the still image.")
        return (f"subject_definitions: {' '.join(definitions)}\n\n"
                f"retention_analysis: {' '.join(retention)}\n\n"
                f"detailed_description: {description}")
    prefix = "For the target image, <Picture 1> is fully referenced as the composition and appearance anchor.\n\n" if mode == "i2va" else ""
    return prefix + "integrated_multimodal_description: " + description


# ---------------------------------------------------------------------------
# Character sheet composition
#
# One frozen-subject orbit generation yields inherently consistent views, so a
# character sheet is a single Ref2VA generation whose staging script is fully
# deterministic. Inspired by PoopMan333's H3_Character_Sheet_Generator
# workflow (https://huggingface.co/PoopMan333/H3_Character_Sheet_Generator);
# the keep/ignore discipline for rough references comes from the same source.
# ---------------------------------------------------------------------------

SHEET_RECIPES = [
    {"id": "turn-6", "name": "Full turn · 6 views",
     "tagline": "Front, three-quarter, sides, back, and a face shot (~5 s)",
     "frames": 120,
     "script": ("[0.00-4.00 seconds] Tight full shot of the subject. The camera makes exactly one smooth fixed-speed "
                "clockwise 360-degree orbit while the subject remains frozen: square front at 0.00 seconds; a clear "
                "front three-quarter view at 0.33 seconds; the subject's left profile at 1.00 seconds; exact square back "
                "at 2.00 seconds; the subject's right profile at 3.00 seconds; and square front again at 4.00 seconds. "
                "Do not slow down, reverse direction, pause, cut, or change the subject between checkpoints. "
                "[4.00-5.00 seconds] The camera pushes straight in from the returned front view. Locked-off close-up "
                "with the subject square to camera, both sides visually balanced, and the defining front features fully "
                "visible. Ends on an unmistakable front-on identity close-up. Do not attempt another angle here; the "
                "three-quarter reference comes from the verified orbit."),
     "extract": [(0.05, "front"), (0.33, "three-quarter"), (1.00, "left side"), (2.00, "back"),
                 (3.00, "right side"), (4.82, "front face")]},
    {"id": "turn-4", "name": "Quick turn · 4 views",
     "tagline": "Back, side, front, and a face shot (~3 s)",
     "frames": 72,
     "script": ("[0.00-2.00 seconds] Tight full shot of the subject. The camera makes one smooth fixed-speed orbit around "
                "it, sweeping 180 degrees: starting square on the back, passing the left side a third of the way through "
                "this move, and ending square on the front at 2 seconds. The subject does not move at all. Ends on the "
                "front view at 2 seconds. "
                "[2.00-3.00 seconds] The camera snaps into a fast push-in on the character's face. Locked-off "
                "head-and-shoulders close-up, face square to camera, eyes into the lens. Ends on a sharp front-on face."),
     "extract": [(0.05, "back"), (0.67, "left side"), (2.00, "front"), (2.90, "front face")]},
]

SHEET_STYLES = [
    {"id": "match", "name": "Match first reference",
     "label": "Match first reference \u00b7 adopts the art style of your first rough image",
     "style": "[STYLE] The output matches the style of <Picture 1>. Sharp detail on eyes and face. The style never "
              "changes and never drifts between shots."},
    {"id": "live-action", "name": "Photoreal live action",
     "label": "Photoreal live action \u00b7 renders a realistic studio-photo look",
     "style": "[STYLE] Fully photorealistic live-action, unretouched studio photograph. Completely natural bare face "
              "and body, zero makeup. Natural lip color, natural eyelashes and eyebrows only. Skin shows real texture. "
              "Mild natural film grain. Style never drifts."},
]

_SHEET_STAGING = (
    "[STAGING] Solid light grey seamless backdrop, one flat uniform tone edge to edge, with no gradient, no vignette, "
    "no texture and no floor line. Nothing else is in frame. The subject casts no shadow onto the backdrop and no "
    "contact shadow on the ground beneath it. Soft form shading on the subject itself is fine and should read its "
    "shape. Long telephoto lens, near-orthographic. "
    "The subject holds one anatomically appropriate neutral reference stance throughout. A humanoid stands in a "
    "relaxed A-pose with arms slightly away from the body. A quadruped stands naturally and evenly on exactly its "
    "established limbs, with its spine level and head forward. A creature, object, or unusual body plan keeps exactly "
    "the anatomy, limb count, connections, proportions, and resting construction established by the references. "
    "The subject is completely frozen, as rigid and motionless as a statue. Only the camera moves. Hair, fabric, and "
    "every accessory sit in exactly the same position in every frame. There is no wind, no breeze, no air movement, "
    "no breathing, no settling, no sway, no secondary motion of any kind. Orientation, surfaces and lighting are "
    "identical in every shot, and the subject stays the same size in frame. The entire image is temporally stable: no "
    "flicker, pulsing exposure, color shift, texture crawl, geometry change, coat change, identity drift, or morphing."
)
_SHEET_CAMERA = ("[CAMERA] One constant-speed orbit, then locked off and static. The camera is the only thing in the "
                 "scene that moves at any point. No zoom beyond the scripted push-in, no dolly, no tilt, no roll, no "
                 "handheld shake, no motion blur, no dissolves.")
_SHEET_AUDIO = "[AUDIO] Silence. No music, no room tone, no voices."


def get_sheet_recipe(recipe_id):
    return next((r for r in SHEET_RECIPES if r["id"] == recipe_id), SHEET_RECIPES[0])


def get_sheet_style(style_id):
    return next((s for s in SHEET_STYLES if s["id"] == style_id), SHEET_STYLES[0])


def sheet_extract_times(recipe_id):
    """[(seconds, label)] clamped just inside the clip for reliable seeking."""
    recipe = get_sheet_recipe(recipe_id)
    limit = recipe["frames"] / FPS - 0.08
    return [(min(t, limit), label) for t, label in recipe["extract"]]


def format_sheet_prompt(*, name="", identity="", references=(), recipe="turn-6", style="match"):
    """Return an official-structure Ref2VA prompt for a character turnaround.

    `references` are rough design inputs, one keep/ignore note each. Notes are
    preserved verbatim — paraphrasing wardrobe or material words is exactly how
    identity drift sneaks in — and correctness never depends on model output.
    """
    rec = get_sheet_recipe(recipe)
    sty = get_sheet_style(style)
    refs = list(references)
    if len(refs) > MAX_REFERENCES:
        raise ValueError(f"MiniMax H3 accepts at most {MAX_REFERENCES} ordered reference images; this sheet has {len(refs)}.")
    who = _clean(name, "the character")
    notes = ""
    definitions, retention = [], []
    for i, ref in enumerate(refs, 1):
        keep = _clean(ref.get("keep") if isinstance(ref, dict) else ref, "")
        note = f" Keep only what its definition line states: {keep}." if keep \
            else " Take only what the surrounding prompt states from this image."
        definitions.append(f"<Picture {i}> depicts the same <Subject 1>, {who}, and is an ordered identity reference.{note}")
        retention.append(f"<Picture {i}>: fully_preserved for <Subject 1>'s visible identity, anatomy, proportions, "
                         "markings, surface or coat, wardrobe, materials, accessories, and colors; ignore only framing, "
                         "pose, background, and lighting unless the prompt explicitly requests them.")
    if identity:
        notes += f" {identity.strip()}"
    defs = ((f"<Subject 1> is {who}. " + " ".join(definitions)) if definitions
            else f"<Subject 1> is {who}; no external subject references are supplied.")
    seconds = rec["frames"] / FPS
    detailed = (f"{sty['style']} {_SHEET_STAGING} {rec['script']} {_SHEET_CAMERA} {_SHEET_AUDIO}")
    return (f"subject_definitions: {defs}\n\n"
            f"summary: [reference generation] Design {who} as one coherent character{notes}. "
            f"Then create a {seconds:.2f}-second silent audiovisual target video at 24fps that locks the final design "
            f"into a reusable multi-view reference sheet on a seamless backdrop.\n\n"
            f"retention_analysis: {' '.join(retention)}\n\n"
            f"detailed_description: {detailed}\n\n"
            f"overall_soundscape: Silence. No room tone, no voices, no sound effects.\n\n"
            f"non_diegetic_music: None.")
