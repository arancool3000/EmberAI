"""SUPRUNO — one generation interface for images, video, and music.

Ember could already ask Gemini for a picture. This adds the other two modalities and,
more importantly, puts all three behind one router that prefers **models running on your
own machine** and falls back to a cloud API only when the hardware can't cope.

    supruno.generate("song", "slow lo-fi piano, rain on a window", seconds=20)
    supruno.generate("video", "a paper boat going over a waterfall", seconds=4)
    supruno.generate("image", "an ember-orange fox curled on a keyboard")

The router picks a model *tier* from the VRAM it can actually see, because the failure
mode that matters here is not "no model" but "a model that loads, thrashes, and hangs the
machine for twenty minutes". A box with 8 GB gets a model sized for 8 GB or it gets the
cloud — never a 24 GB checkpoint it will die on.

**On training our own weights.** ``training_plan()`` returns the real numbers for
fine-tuning open weights (LoRA) on your own material — that is the honest version of "our
own model", and it works: a few hundred images, one consumer GPU, a few hours. Training a
*foundation* video or music model from scratch is a different category of thing — millions
of dollars of compute, a licensed dataset, and a research team — and this module does not
pretend otherwise. See ``training_plan()``'s ``reality`` field.

Selection, tiering, and planning are pure functions with no heavy imports, so they are
unit-tested headlessly (``test_supruno.py``); backends are probed lazily.
"""
from __future__ import annotations

import os
import time
from pathlib import Path
from typing import Optional

KINDS = ("image", "video", "song")

#: Model tiers per modality, largest first. ``vram`` is the GB needed to run it without
#: swapping; ``weights`` is the open checkpoint the local backend pulls.
TIERS: dict[str, tuple[dict, ...]] = {
    "image": (
        {"name": "flux-dev", "weights": "black-forest-labs/FLUX.1-dev", "vram": 20,
         "quality": 5, "note": "best open image quality"},
        {"name": "flux-schnell", "weights": "black-forest-labs/FLUX.1-schnell", "vram": 12,
         "quality": 4, "note": "4-step, very fast"},
        {"name": "sdxl-turbo", "weights": "stabilityai/sdxl-turbo", "vram": 8,
         "quality": 3, "note": "single-step, runs on modest cards"},
        {"name": "sd15", "weights": "runwayml/stable-diffusion-v1-5", "vram": 4,
         "quality": 2, "note": "last resort, fits almost anything"},
    ),
    "video": (
        {"name": "ltx-video", "weights": "Lightricks/LTX-Video", "vram": 24,
         "quality": 5, "note": "fast, coherent short clips"},
        {"name": "cogvideox-5b", "weights": "THUDM/CogVideoX-5b", "vram": 18,
         "quality": 4, "note": "good motion, slower"},
        {"name": "cogvideox-2b", "weights": "THUDM/CogVideoX-2b", "vram": 10,
         "quality": 3, "note": "shorter clips, modest cards"},
    ),
    "song": (
        {"name": "musicgen-large", "weights": "facebook/musicgen-large", "vram": 16,
         "quality": 5, "note": "richest arrangements"},
        {"name": "musicgen-medium", "weights": "facebook/musicgen-medium", "vram": 8,
         "quality": 4, "note": "the sweet spot on consumer GPUs"},
        {"name": "musicgen-small", "weights": "facebook/musicgen-small", "vram": 4,
         "quality": 2, "note": "CPU-viable, noticeably thinner"},
    ),
}

#: Generous caps. Long clips are where a local run silently turns into an overnight job.
LIMITS = {"image": 1, "video": 20, "song": 300}


# ---------------------------------------------------------------------------
# Hardware probing
# ---------------------------------------------------------------------------

def hardware() -> dict:
    """What we can actually run on: ``{"device", "vram_gb", "torch"}``.

    Apple Silicon reports unified memory, which the GPU shares with everything else, so
    only a fraction of it is honestly available to a model.
    """
    info = {"device": "cpu", "vram_gb": 0.0, "torch": False}
    try:
        import torch
    except Exception:
        return info
    info["torch"] = True
    try:
        if torch.cuda.is_available():
            info["device"] = "cuda"
            props = torch.cuda.get_device_properties(0)
            info["vram_gb"] = round(props.total_memory / (1024 ** 3), 1)
            info["gpu"] = props.name
        elif getattr(torch.backends, "mps", None) and torch.backends.mps.is_available():
            info["device"] = "mps"
            try:
                import psutil
                # Unified memory is shared with the OS and every open app; committing all
                # of it to a checkpoint is how these runs wedge a Mac.
                info["vram_gb"] = round(psutil.virtual_memory().total / (1024 ** 3) * 0.6, 1)
            except Exception:
                info["vram_gb"] = 8.0
    except Exception:
        pass
    return info


# ---------------------------------------------------------------------------
# Selection (pure)
# ---------------------------------------------------------------------------

def normalize_kind(kind: object) -> str:
    """Map what a user or an LLM actually says to one of :data:`KINDS`."""
    text = str(kind or "").strip().lower()
    aliases = {
        "picture": "image", "img": "image", "photo": "image", "art": "image",
        "drawing": "image", "images": "image",
        "clip": "video", "movie": "video", "animation": "video", "vid": "video",
        "videos": "video", "gif": "video",
        "music": "song", "audio": "song", "track": "song", "tune": "song",
        "songs": "song", "sound": "song", "melody": "song",
    }
    if text in KINDS:
        return text
    if text in aliases:
        return aliases[text]
    raise ValueError(f"unsupported kind {kind!r}; expected one of {', '.join(KINDS)}")


def pick_tier(kind: str, vram_gb: float, *, headroom: float = 1.0) -> Optional[dict]:
    """Best model for this modality that fits in ``vram_gb``, or None if nothing does.

    ``headroom`` reserves GB for the OS, the desktop, and activation peaks — a model whose
    weights *just* fit is a model that will out-of-memory on the first big batch.
    """
    kind = normalize_kind(kind)
    usable = float(vram_gb) - float(headroom)
    for tier in TIERS[kind]:
        if usable >= tier["vram"]:
            return dict(tier)
    return None


def clamp_duration(kind: str, seconds: object) -> int:
    """Clamp requested length to something this modality can actually deliver."""
    kind = normalize_kind(kind)
    cap = LIMITS[kind]
    try:
        want = int(float(seconds))
    except (TypeError, ValueError):
        want = cap if kind == "image" else min(8, cap)
    return max(1, min(cap, want))


def plan(kind: str, *, hw: Optional[dict] = None, seconds: object = None,
         allow_cloud: bool = True) -> dict:
    """Decide how a request will be served, without running anything.

    Returns the route, the chosen model, and *why* — so the UI can say "your card is too
    small for local video, using the cloud" instead of quietly doing something else.
    """
    kind = normalize_kind(kind)
    hw = hw if hw is not None else hardware()
    length = clamp_duration(kind, seconds if seconds is not None else LIMITS[kind])
    tier = pick_tier(kind, hw.get("vram_gb", 0.0)) if hw.get("torch") else None

    if tier:
        return {"kind": kind, "route": "local", "model": tier["name"],
                "weights": tier["weights"], "device": hw.get("device", "cpu"),
                "seconds": length, "private": True,
                "why": f"{tier['name']} fits your {hw.get('vram_gb', 0)} GB "
                       f"{hw.get('device', 'cpu').upper()} ({tier['note']})."}

    if not allow_cloud:
        return {"kind": kind, "route": "none", "seconds": length, "private": True,
                "why": _no_local_reason(kind, hw) + " Cloud fallback is switched off."}

    if kind == "image":
        return {"kind": kind, "route": "cloud", "model": "gemini-image",
                "seconds": length, "private": False,
                "why": _no_local_reason(kind, hw) + " Falling back to the cloud image model."}

    # Video and music have no cloud path wired into Ember yet; saying so plainly beats
    # returning a confusing failure from three layers down.
    return {"kind": kind, "route": "none", "seconds": length, "private": True,
            "why": _no_local_reason(kind, hw) +
                   f" Ember has no cloud {kind} provider configured, so this needs a "
                   f"GPU with at least {TIERS[kind][-1]['vram']} GB."}


def _no_local_reason(kind: str, hw: dict) -> str:
    if not hw.get("torch"):
        return ("PyTorch isn't installed, so nothing can run locally "
                "(pip install -r requirements-generation.txt).")
    vram = hw.get("vram_gb", 0.0)
    smallest = TIERS[kind][-1]["vram"]
    if hw.get("device") == "cpu":
        return "No GPU was detected, and these models are unusably slow on CPU."
    return f"{vram} GB of VRAM is below the {smallest} GB the smallest {kind} model needs."


# ---------------------------------------------------------------------------
# Output paths
# ---------------------------------------------------------------------------

EXT = {"image": "png", "video": "mp4", "song": "wav"}


def output_path(kind: str, output: str = "", *, stamp: Optional[str] = None) -> Path:
    """Resolve where a generated file lands, creating the parent directory."""
    kind = normalize_kind(kind)
    if output:
        p = Path(output).expanduser()
    else:
        stamp = stamp or time.strftime("%Y%m%d-%H%M%S")
        p = Path.home() / "Ember" / "SUPRUNO" / f"{kind}-{stamp}.{EXT[kind]}"
    if not p.suffix:
        p = p.with_suffix("." + EXT[kind])
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
    except Exception:
        pass
    return p


# ---------------------------------------------------------------------------
# Generation
# ---------------------------------------------------------------------------

def generate(kind: str, prompt: str, *, output: str = "", seconds: object = None,
             allow_cloud: bool = True, **opts) -> dict:
    """Generate an image, a video clip, or a piece of music.

    Always returns a dict rather than raising, because this is reached from the agent's
    tool layer where an exception becomes an opaque failure.
    """
    try:
        kind = normalize_kind(kind)
    except ValueError as e:
        return {"ok": False, "error": str(e)}
    if not str(prompt or "").strip():
        return {"ok": False, "error": "A prompt is required."}

    route = plan(kind, seconds=seconds, allow_cloud=allow_cloud)
    dest = output_path(kind, output)
    started = time.time()

    if route["route"] == "local":
        result = _run_local(route, prompt, dest, **opts)
    elif route["route"] == "cloud":
        result = _run_cloud(route, prompt, dest)
    else:
        return {"ok": False, "error": route["why"], "plan": route}

    result.setdefault("plan", route)
    result.setdefault("elapsed_s", round(time.time() - started, 1))
    return result


def _run_local(route: dict, prompt: str, dest: Path, **opts) -> dict:
    kind = route["kind"]
    try:
        if kind == "image":
            return _local_image(route, prompt, dest, **opts)
        if kind == "video":
            return _local_video(route, prompt, dest, **opts)
        return _local_song(route, prompt, dest, **opts)
    except ImportError as e:
        return {"ok": False, "error": f"Missing dependency for {route['model']}: {e}. "
                                      f"pip install -r requirements-generation.txt"}
    except Exception as e:
        return {"ok": False, "error": f"{route['model']} failed: {type(e).__name__}: {e}"}


def _local_image(route: dict, prompt: str, dest: Path, **opts) -> dict:
    import torch
    from diffusers import AutoPipelineForText2Image

    dtype = torch.float16 if route["device"] != "cpu" else torch.float32
    pipe = AutoPipelineForText2Image.from_pretrained(route["weights"], torch_dtype=dtype)
    pipe = pipe.to(route["device"])
    # Slicing trades a little speed for a much lower peak, which is the difference
    # between finishing and OOMing on the cards this tier targets.
    try:
        pipe.enable_attention_slicing()
    except Exception:
        pass
    steps = int(opts.get("steps") or (4 if "schnell" in route["model"] or
                                      "turbo" in route["model"] else 28))
    image = pipe(prompt=prompt, num_inference_steps=steps,
                 guidance_scale=float(opts.get("guidance", 0.0 if steps <= 4 else 7.0))
                 ).images[0]
    image.save(dest)
    return {"ok": True, "path": str(dest), "model": route["model"], "steps": steps}


def _local_video(route: dict, prompt: str, dest: Path, **opts) -> dict:
    import torch
    from diffusers import DiffusionPipeline
    from diffusers.utils import export_to_video

    pipe = DiffusionPipeline.from_pretrained(
        route["weights"], torch_dtype=torch.float16).to(route["device"])
    try:
        pipe.enable_model_cpu_offload()   # keeps only the active block resident
    except Exception:
        pass
    fps = int(opts.get("fps", 8))
    frames = max(fps, int(route["seconds"]) * fps)
    out = pipe(prompt=prompt, num_frames=frames,
               num_inference_steps=int(opts.get("steps", 30))).frames[0]
    export_to_video(out, str(dest), fps=fps)
    return {"ok": True, "path": str(dest), "model": route["model"],
            "seconds": route["seconds"], "fps": fps}


def _local_song(route: dict, prompt: str, dest: Path, **opts) -> dict:
    import torch
    import scipy.io.wavfile
    from transformers import AutoProcessor, MusicgenForConditionalGeneration

    processor = AutoProcessor.from_pretrained(route["weights"])
    model = MusicgenForConditionalGeneration.from_pretrained(route["weights"])
    model = model.to(route["device"])
    inputs = processor(text=[prompt], padding=True, return_tensors="pt").to(route["device"])
    # MusicGen counts tokens, not seconds; its frame rate is the conversion.
    rate = int(getattr(model.config, "audio_encoder", model.config).frame_rate or 50)
    tokens = int(route["seconds"]) * rate
    with torch.no_grad():
        audio = model.generate(**inputs, max_new_tokens=tokens,
                               guidance_scale=float(opts.get("guidance", 3.0)))
    sr = int(model.config.audio_encoder.sampling_rate)
    scipy.io.wavfile.write(str(dest), rate=sr, data=audio[0, 0].cpu().numpy())
    return {"ok": True, "path": str(dest), "model": route["model"],
            "seconds": route["seconds"], "sample_rate": sr}


def _run_cloud(route: dict, prompt: str, dest: Path) -> dict:
    """Reuse Ember's existing cloud image path rather than duplicating key handling."""
    try:
        import creative
        res = creative.generate_image(prompt, output=str(dest))
        if isinstance(res, dict):
            res.setdefault("model", route.get("model", "cloud"))
        return res
    except Exception as e:
        return {"ok": False, "error": f"Cloud image generation failed: {e}"}


# ---------------------------------------------------------------------------
# Training our own weights
# ---------------------------------------------------------------------------

def training_plan(kind: str, *, examples: int = 0, hw: Optional[dict] = None) -> dict:
    """Concrete requirements for fine-tuning open weights on your own material.

    This is the achievable form of "our own model": a LoRA adapter over open weights,
    trained on a few hundred of your own examples, on one consumer GPU, in hours. The
    ``reality`` field states plainly what this is *not*.
    """
    kind = normalize_kind(kind)
    hw = hw if hw is not None else hardware()
    vram = hw.get("vram_gb", 0.0)

    spec = {
        "image": {"min_examples": 20, "good_examples": 300, "min_vram": 12,
                  "steps_per_example": 100, "sec_per_step": 0.9,
                  "data": "square-cropped images with one-line captions"},
        "video": {"min_examples": 200, "good_examples": 2000, "min_vram": 24,
                  "steps_per_example": 60, "sec_per_step": 3.5,
                  "data": "2-5s clips at a consistent fps, each with a caption"},
        "song": {"min_examples": 100, "good_examples": 1000, "min_vram": 16,
                 "steps_per_example": 80, "sec_per_step": 1.6,
                 "data": "10-30s mono clips with genre/instrument/mood tags"},
    }[kind]

    have = int(examples or 0)
    steps = max(500, min(8000, (have or spec["good_examples"]) * spec["steps_per_example"] // 10))
    hours = round(steps * spec["sec_per_step"] / 3600, 1)
    blockers = []
    if have and have < spec["min_examples"]:
        blockers.append(f"{have} examples is below the {spec['min_examples']} minimum; "
                        f"the adapter will overfit and reproduce your inputs.")
    if vram < spec["min_vram"]:
        blockers.append(f"{vram} GB VRAM is under the {spec['min_vram']} GB a {kind} LoRA "
                        f"needs; rent a GPU or train the next tier down.")

    return {
        "kind": kind,
        "method": "LoRA adapter over open weights",
        "base": TIERS[kind][0]["weights"],
        "dataset": spec["data"],
        "min_examples": spec["min_examples"],
        "recommended_examples": spec["good_examples"],
        "min_vram_gb": spec["min_vram"],
        "steps": steps,
        "est_hours": hours,
        "feasible": not blockers,
        "blockers": blockers,
        "reality": (
            "This fine-tunes open weights so the output carries your style, subjects, or "
            "sound — genuinely 'ours', and reachable on one GPU. It is not a foundation "
            "model trained from scratch: matching Sora or Suno means tens of millions of "
            "dollars of compute, a licensed dataset at web scale, and a full-time research "
            "team. Anything claiming otherwise on a laptop is fiction."
        ),
    }


def status() -> dict:
    """One call for the UI: hardware, and what each modality can do right now."""
    hw = hardware()
    return {"hardware": hw,
            "modalities": {k: plan(k, hw=hw) for k in KINDS}}
