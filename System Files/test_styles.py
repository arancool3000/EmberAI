"""Regression test for the QSS themes (pure strings — no Qt needed).

Guards the bug where the flat (Liquid-Glass-OFF) theme gave the 'Quick tasks' buttons
(#commandTask) DARK text on the dark window, so they were unreadable. Both themes must use
light text. Run: python test_styles.py
"""
import re

import styles


def _rule_color(qss: str, object_name: str) -> str:
    """Return the standalone `color:` of a QPushButton#<name> rule (not background-color)."""
    m = re.search(r"QPushButton#" + re.escape(object_name) + r"\s*\{\{?(.*?)\}\}?", qss, re.S)
    assert m, f"no rule for #{object_name}"
    cm = re.search(r"(?<!background-)color:\s*([^;]+);", m.group(1))
    assert cm, f"no color in #{object_name}"
    return cm.group(1).strip().lower()


def _is_dark(color: str) -> bool:
    """True if a hex/rgba colour is dark (low luminance) — what made the text invisible."""
    nums = re.findall(r"[0-9a-f]{2}", color) if color.startswith("#") else re.findall(r"\d+", color)
    if color.startswith("#") and len(color) >= 7:
        r, g, b = (int(color[1:3], 16), int(color[3:5], 16), int(color[5:7], 16))
    elif not color.startswith("#"):
        vals = [int(n) for n in re.findall(r"\d+", color)][:3]
        if len(vals) < 3:
            return False
        r, g, b = vals
    else:
        return False
    return (0.2126 * r + 0.7152 * g + 0.0722 * b) < 90   # perceived luminance


def test_flat_theme_command_task_is_readable():
    c = _rule_color(styles.STYLE, "commandTask")
    assert not _is_dark(c), f"flat theme #commandTask text is dark/unreadable: {c}"


def test_glass_theme_command_task_is_readable():
    g = styles._glass_style(200, "#58a6ff", see_through=75, blurred=False)
    c = _rule_color(g, "commandTask")
    assert not _is_dark(c), f"glass theme #commandTask text is dark/unreadable: {c}"


def test_command_action_is_readable_in_both():
    assert not _is_dark(_rule_color(styles.STYLE, "commandAction"))
    g = styles._glass_style(200, "#58a6ff", see_through=75, blurred=False)
    assert not _is_dark(_rule_color(g, "commandAction"))


def test_workspace_polish_covers_primary_surfaces():
    """The redesigned shell must stay present whichever material theme is selected."""
    for selector in ("QFrame#workspaceHeader", "QComboBox#modePicker",
                     "QFrame#pointerCard", "QFrame#composer",
                     "QPushButton#commandPalette"):
        assert selector in styles.WORKSPACE_POLISH, selector


def test_workspace_polish_keeps_visible_focus_and_hover_states():
    assert "QPushButton#newTask:hover" in styles.WORKSPACE_POLISH
    assert "QPushButton#composerTool:hover" in styles.WORKSPACE_POLISH
    assert "QSlider#pointerSpeed::handle:horizontal" in styles.WORKSPACE_POLISH


def test_secondary_surfaces_share_the_product_system():
    for selector in ("QFrame#dialogHeader", "QFrame#featureRow",
                     "QTabWidget#settingsTabs", "QPlainTextEdit#codeSurface",
                     "QPushButton#dangerBtn", "QLabel#securityHealth"):
        assert selector in styles.WORKSPACE_POLISH, selector


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    passed = 0
    for t in tests:
        t()
        print(f"  ok  {t.__name__}")
        passed += 1
    print(f"\n{passed}/{len(tests)} styles tests passed")
