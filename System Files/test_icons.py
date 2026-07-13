"""Hermetic tests for icons.py — Ember's own monoline icon set. Tests the pure SVG
generation + name/emoji resolution (no Qt needed). Run: python test_icons.py"""
import icons


def test_every_icon_renders_nonempty_svg():
    for name in icons.names():
        s = icons.svg(name)
        assert s.startswith("<svg") and s.endswith("</svg>"), name
        assert 'viewBox="0 0 24 24"' in s, name
        assert len(s) > 60, name


def test_color_is_substituted_everywhere():
    s = icons.svg("shield-check", color="#ff8800")
    assert "#ff8800" in s
    assert "currentColor" not in s   # all currentColor tokens replaced


def test_stroke_width_passthrough():
    assert 'stroke-width="3"' in icons.svg("home", stroke=3)
    assert 'stroke-width="1.5"' in icons.svg("home", stroke=1.5)


def test_resolve_name_and_emoji():
    assert icons.resolve("shield-check") == "shield-check"
    assert icons.resolve("🛡️") == "shield-check"
    assert icons.resolve("🌐") == "globe"
    assert icons.resolve("🧩") == "puzzle"
    assert icons.resolve("✨") == "sparkle"
    assert icons.resolve("🔑") == "key"
    assert icons.resolve("nonsense-xyz") is None
    assert icons.resolve("") is None


def test_emoji_svg_routes_through_alias():
    # Asking for an emoji yields the mapped icon's body, identical to the named one.
    assert icons.svg("🔎") == icons.svg("search")
    assert icons.svg("🎙️") == icons.svg("mic")


def test_unknown_name_falls_back_to_circle_not_crash():
    s = icons.svg("totally-unknown")
    assert "<circle" in s and s.startswith("<svg")


def test_has_and_names():
    assert icons.has("globe") and icons.has("mic")
    assert not icons.has("dragon")
    n = icons.names()
    assert "shield-check" in n and "puzzle" in n and len(n) >= 20


def test_filled_glyphs_use_currentcolor_fill():
    # Filled icons (star/play/sparkle) must carry a fill so they aren't hollow.
    for name in ("star", "play", "sparkle"):
        assert 'fill="currentColor"' in icons._ICONS[name], name


def test_brand_star_svg_is_a_valid_scalable_vector():
    s = icons.brand_star_svg()
    assert s.startswith("<svg") and s.endswith("</svg>")
    assert 'viewBox="0 0 24 24"' in s
    assert "linearGradient" in s and "url(#emberStar)" in s
    for colour in icons._BRAND_STAR_STOPS:
        assert colour in s


def test_brand_star_svg_accepts_custom_colors():
    s = icons.brand_star_svg(
        colors=("#111111", "#222222", "#333333", "#444444", "#555555"),
        edge="#eeeeee")
    assert "#111111" in s and "#555555" in s and "#eeeeee" in s


def test_brand_star_svg_has_no_text_glyph():
    s = icons.brand_star_svg()
    assert ">E<" not in s and "<text" not in s


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    passed = 0
    for t in tests:
        t()
        print(f"  ok  {t.__name__}")
        passed += 1
    print(f"\n{passed}/{len(tests)} icons tests passed")
