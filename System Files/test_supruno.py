"""Tests for SUPRUNO's routing, tiering, and training planner (supruno.py).

Pure logic — no torch, no GPU, no network. Runnable:
    pytest test_supruno.py
    python test_supruno.py
"""
import supruno as sp


# --- kind normalization -------------------------------------------------------

def test_kinds_and_aliases():
    assert sp.normalize_kind("image") == "image"
    assert sp.normalize_kind("Picture") == "image"
    assert sp.normalize_kind("music") == "song"
    assert sp.normalize_kind("clip") == "video"
    assert sp.normalize_kind("  MOVIE ") == "video"


def test_unknown_kind_names_the_valid_ones():
    try:
        sp.normalize_kind("hologram")
    except ValueError as e:
        assert "image" in str(e) and "song" in str(e)
    else:
        raise AssertionError("expected ValueError")


# --- VRAM tiering -------------------------------------------------------------

def test_big_card_gets_the_best_model():
    assert sp.pick_tier("image", 24)["name"] == "flux-dev"
    assert sp.pick_tier("song", 24)["name"] == "musicgen-large"


def test_modest_card_gets_a_model_that_fits():
    tier = sp.pick_tier("image", 9)
    assert tier["name"] == "sdxl-turbo" and tier["vram"] <= 9


def test_tiny_card_gets_nothing_rather_than_a_model_it_cannot_run():
    # The failure that matters is a checkpoint that loads, thrashes, and wedges the box.
    assert sp.pick_tier("video", 6) is None
    assert sp.pick_tier("image", 2) is None


def test_headroom_is_reserved():
    # 12 GB of VRAM must not be handed a model whose weights need exactly 12 GB.
    assert sp.pick_tier("image", 12)["name"] != "flux-schnell"
    assert sp.pick_tier("image", 13)["name"] == "flux-schnell"


def test_tiers_are_ordered_largest_first():
    for kind, tiers in sp.TIERS.items():
        vram = [t["vram"] for t in tiers]
        assert vram == sorted(vram, reverse=True), (kind, vram)


def test_every_tier_is_fully_specified():
    for kind, tiers in sp.TIERS.items():
        for t in tiers:
            assert set(("name", "weights", "vram", "quality", "note")) <= set(t), (kind, t)


# --- duration clamping --------------------------------------------------------

def test_duration_is_clamped_per_modality():
    assert sp.clamp_duration("video", 999) == sp.LIMITS["video"]
    assert sp.clamp_duration("song", 999) == sp.LIMITS["song"]
    assert sp.clamp_duration("video", 4) == 4
    assert sp.clamp_duration("image", 30) == 1


def test_duration_survives_junk_input():
    assert sp.clamp_duration("song", None) >= 1
    assert sp.clamp_duration("song", "banana") >= 1
    assert sp.clamp_duration("video", -5) == 1
    assert sp.clamp_duration("song", "12") == 12


# --- routing ------------------------------------------------------------------

def _hw(device="cuda", vram=24.0, torch=True):
    return {"device": device, "vram_gb": vram, "torch": torch}


def test_capable_gpu_routes_local_and_private():
    p = sp.plan("song", hw=_hw(vram=24.0), seconds=10)
    assert p["route"] == "local" and p["private"] is True
    assert p["model"] == "musicgen-large" and p["seconds"] == 10


def test_image_without_a_gpu_falls_back_to_cloud():
    p = sp.plan("image", hw=_hw(device="cpu", vram=0.0))
    assert p["route"] == "cloud" and p["private"] is False
    assert "cloud" in p["why"].lower()


def test_video_without_a_gpu_says_so_instead_of_failing_deep_in_a_pipeline():
    p = sp.plan("video", hw=_hw(device="cpu", vram=0.0))
    assert p["route"] == "none"
    assert "GPU" in p["why"] or "gpu" in p["why"]


def test_no_torch_is_explained_with_the_fix():
    p = sp.plan("image", hw=_hw(torch=False, vram=0.0))
    assert "PyTorch" in p["why"] and "pip install" in p["why"]


def test_allow_cloud_false_keeps_everything_local():
    p = sp.plan("image", hw=_hw(device="cpu", vram=0.0), allow_cloud=False)
    assert p["route"] == "none" and p["private"] is True


def test_low_vram_reason_names_the_shortfall():
    p = sp.plan("video", hw=_hw(vram=6.0))
    assert "6.0 GB" in p["why"] and str(sp.TIERS["video"][-1]["vram"]) in p["why"]


def test_every_plan_explains_itself():
    for kind in sp.KINDS:
        for hw in (_hw(), _hw(vram=8.0), _hw(device="cpu", vram=0.0), _hw(torch=False)):
            p = sp.plan(kind, hw=hw)
            assert p["why"].strip(), (kind, hw)
            assert p["route"] in ("local", "cloud", "none")


# --- output paths -------------------------------------------------------------

def test_default_paths_are_typed_and_stamped():
    p = sp.output_path("song", stamp="20260101-000000")
    assert p.suffix == ".wav" and "SUPRUNO" in str(p)
    assert sp.output_path("video", stamp="x").suffix == ".mp4"
    assert sp.output_path("image", stamp="x").suffix == ".png"


def test_explicit_output_is_honoured_and_given_an_extension(tmpdir=None):
    import tempfile
    with tempfile.TemporaryDirectory() as d:
        assert sp.output_path("image", f"{d}/art.png").name == "art.png"
        assert sp.output_path("song", f"{d}/track").name == "track.wav"


# --- generate() guard rails ---------------------------------------------------

def test_generate_rejects_an_empty_prompt():
    r = sp.generate("image", "   ")
    assert r["ok"] is False and "prompt" in r["error"].lower()


def test_generate_rejects_an_unknown_kind_without_raising():
    r = sp.generate("hologram", "a cat")
    assert r["ok"] is False and "unsupported" in r["error"]


def test_generate_reports_why_it_could_not_run():
    # No torch in the test environment, so video must fail with the routing reason
    # attached rather than an opaque exception from inside a pipeline.
    r = sp.generate("video", "a paper boat", allow_cloud=False)
    assert r["ok"] is False and r["error"] and "plan" in r


# --- training planner ---------------------------------------------------------

def test_training_plan_is_lora_over_open_weights():
    p = sp.training_plan("image", examples=300, hw=_hw(vram=24.0))
    assert p["method"].startswith("LoRA")
    assert p["feasible"] is True and p["est_hours"] > 0
    assert p["steps"] >= 500


def test_training_plan_flags_too_few_examples():
    p = sp.training_plan("image", examples=3, hw=_hw(vram=24.0))
    assert p["feasible"] is False
    assert any("overfit" in b for b in p["blockers"])


def test_training_plan_flags_insufficient_vram():
    p = sp.training_plan("video", examples=2000, hw=_hw(vram=8.0))
    assert p["feasible"] is False
    assert any("VRAM" in b for b in p["blockers"])


def test_training_plan_is_honest_about_foundation_models():
    # The whole point of this field: no plan may imply a laptop can out-train Sora.
    for kind in sp.KINDS:
        reality = sp.training_plan(kind)["reality"]
        assert "not a foundation model" in reality
        assert "Sora" in reality and "Suno" in reality


def test_training_plan_covers_every_modality():
    for kind in sp.KINDS:
        p = sp.training_plan(kind, examples=500, hw=_hw(vram=24.0))
        assert p["dataset"] and p["base"] and p["min_examples"] > 0


# --- status -------------------------------------------------------------------

def test_status_reports_every_modality_without_raising():
    s = sp.status()
    assert set(s["modalities"]) == set(sp.KINDS)
    assert "device" in s["hardware"]


def _run():
    failures = 0
    names = [n for n in globals() if n.startswith("test_")]
    for name in sorted(names):
        try:
            globals()[name]()
            print(f"PASS  {name}")
        except Exception as e:
            failures += 1
            print(f"FAIL  {name}: {type(e).__name__}: {e}")
    print(f"\n{len(names) - failures}/{len(names)} passed")
    return failures


if __name__ == "__main__":
    import sys
    sys.exit(1 if _run() else 0)
