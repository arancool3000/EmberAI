"""Ember's own icon set — original, consistent monoline glyphs we draw ourselves,
to replace the grab-bag of OS emoji across the UI with something that looks deliberate.

Each icon is a hand-authored SVG path (24×24 grid, rounded caps, single weight), so it's
crisp at any size and recolourable to match the theme. The SVG generation + name/emoji
resolution are pure functions (unit-tested with no Qt); qicon()/pixmap() are thin Qt
wrappers that render the SVG, and they degrade to None so callers can fall back to text.
"""
from __future__ import annotations

# Each entry is the inner SVG body. Stroke icons inherit stroke="currentColor" from the
# template; filled glyphs set their own fill="currentColor". "currentColor" is swapped for
# the requested colour at render time (QSvgRenderer doesn't inherit it from outside).
_ICONS: dict[str, str] = {
    "back": '<polyline points="15 18 9 12 15 6"/>',
    "forward": '<polyline points="9 18 15 12 9 6"/>',
    "reload": '<path d="M21 12a9 9 0 1 1-2.64-6.36"/><polyline points="21 3 21 9 15 9"/>',
    "home": '<path d="M3 11l9-8 9 8"/><path d="M5 10v10h14V10"/>',
    "star": '<polygon points="12 2 15 9 22 9.2 16.5 13.6 18.6 21 12 16.6 5.4 21 7.5 13.6 2 9.2 9 9" '
            'fill="currentColor" stroke="none"/>',
    "bookmark": '<path d="M6 4h12v16l-6-4-6 4z"/>',
    "history": '<circle cx="12" cy="12" r="9"/><polyline points="12 7 12 12 16 14"/>',
    "book": '<path d="M4 5a2 2 0 0 1 2-2h6v18H6a2 2 0 0 1-2-2z"/>'
            '<path d="M20 5a2 2 0 0 0-2-2h-6v18h6a2 2 0 0 0 2-2z"/>',
    "moon": '<path d="M21 12.8A8 8 0 1 1 11.2 3 6 6 0 0 0 21 12.8z"/>',
    "search": '<circle cx="11" cy="11" r="7"/><line x1="21" y1="21" x2="16.6" y2="16.6"/>',
    "key": '<circle cx="8" cy="15" r="4"/><path d="M10.8 12.2 20 3"/>'
           '<path d="M16 7l3 3"/><path d="M18.5 4.5l3 3"/>',
    "plus": '<line x1="12" y1="5" x2="12" y2="19"/><line x1="5" y1="12" x2="19" y2="12"/>',
    "sparkle": '<path d="M12 3l1.9 5.1L19 10l-5.1 1.9L12 17l-1.9-5.1L5 10l5.1-1.9z" '
               'fill="currentColor" stroke="none"/>'
               '<path d="M19 14l.8 2.2L22 17l-2.2.8L19 20l-.8-2.2L16 17l2.2-.8z" '
               'fill="currentColor" stroke="none"/>',
    "puzzle": '<path d="M10 4a2 2 0 1 1 4 0h3v3a2 2 0 1 1 0 4v4h-4a2 2 0 1 0-4 0H5v-4a2 2 0 1 1 0-4V4z"/>',
    "shield": '<path d="M12 2l8 3v6c0 5-3.4 8.6-8 11-4.6-2.4-8-6-8-11V5z"/>',
    "shield-check": '<path d="M12 2l8 3v6c0 5-3.4 8.6-8 11-4.6-2.4-8-6-8-11V5z"/>'
                    '<polyline points="9 12 11.3 14.3 15.5 9.7"/>',
    "shield-off": '<path d="M12 2l8 3v6c0 5-3.4 8.6-8 11-4.6-2.4-8-6-8-11V5z"/>'
                  '<line x1="4.5" y1="4.5" x2="19.5" y2="19.5"/>',
    "globe": '<circle cx="12" cy="12" r="9"/><line x1="3" y1="12" x2="21" y2="12"/>'
             '<path d="M12 3a14 14 0 0 1 0 18M12 3a14 14 0 0 0 0 18"/>',
    "mic": '<rect x="9" y="3" width="6" height="11" rx="3"/>'
           '<path d="M5 11a7 7 0 0 0 14 0"/><line x1="12" y1="18" x2="12" y2="22"/>',
    "gear": '<circle cx="12" cy="12" r="3.2"/>'
            '<path d="M12 2v3M12 19v3M22 12h-3M5 12H2M18.4 5.6l-2.1 2.1M7.7 16.3l-2.1 2.1'
            'M18.4 18.4l-2.1-2.1M7.7 7.7 5.6 5.6"/>',
    "camera": '<path d="M4 8h3l2-2h6l2 2h3v11H4z"/><circle cx="12" cy="13" r="3.5"/>',
    "folder": '<path d="M3 7h6l2 2h10v10H3z"/>',
    "lock": '<rect x="5" y="11" width="14" height="9" rx="2"/><path d="M8 11V8a4 4 0 0 1 8 0v3"/>',
    "bell": '<path d="M6 9a6 6 0 0 1 12 0c0 5 2 6 2 6H4s2-1 2-6z"/><path d="M10 20a2 2 0 0 0 4 0"/>',
    "robot": '<rect x="5" y="8" width="14" height="11" rx="2"/>'
             '<circle cx="9.5" cy="13" r="1.1" fill="currentColor" stroke="none"/>'
             '<circle cx="14.5" cy="13" r="1.1" fill="currentColor" stroke="none"/>'
             '<line x1="12" y1="4.5" x2="12" y2="8"/>'
             '<circle cx="12" cy="3.6" r="1.1"/>',
    "chip": '<rect x="7" y="7" width="10" height="10" rx="1.5"/>'
            '<path d="M10 7V4M14 7V4M10 20v-3M14 20v-3M7 10H4M7 14H4M20 10h-3M20 14h-3"/>',
    "download": '<path d="M12 3v12"/><polyline points="7 10 12 15 17 10"/><path d="M5 19h14"/>',
    "trash": '<polyline points="4 7 20 7"/><path d="M6 7l1 13h10l1-13"/>'
             '<path d="M9 7V4h6v3"/>',
    "close": '<line x1="6" y1="6" x2="18" y2="18"/><line x1="18" y1="6" x2="6" y2="18"/>',
    "play": '<polygon points="7 4 20 12 7 20" fill="currentColor" stroke="none"/>',
    "doc": '<path d="M7 3h7l4 4v14H7z"/><polyline points="14 3 14 7 18 7"/>',
}

# Common OS emoji -> our icon name, so existing call sites can be swapped 1:1.
_EMOJI_ALIASES: dict[str, str] = {
    "🛡️": "shield-check", "🛡": "shield-check", "🌐": "globe", "🕸️": "globe", "🕸": "globe",
    "🧩": "puzzle", "🎙️": "mic", "🎙": "mic", "🔊": "mic", "⚙️": "gear", "⚙": "gear",
    "★": "star", "⭐": "star", "📑": "bookmark", "🔖": "bookmark", "📜": "history",
    "📖": "book", "📚": "book", "🌙": "moon", "🔎": "search", "🔍": "search",
    "🔑": "key", "➕": "plus", "✨": "sparkle", "📸": "camera", "🗂️": "folder", "🗂": "folder",
    "📁": "folder", "📂": "folder", "🔒": "lock", "🔐": "lock", "🔔": "bell", "🤖": "robot",
    "🧠": "chip", "⬇️": "download", "📥": "download", "🗑️": "trash", "🗑": "trash",
    "✕": "close", "✖️": "close", "❌": "close", "▶️": "play", "▶": "play", "🚫": "shield-off",
    "📄": "doc", "📝": "doc",
}

_TEMPLATE = (
    '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" '
    'stroke="currentColor" stroke-width="{stroke}" stroke-linecap="round" '
    'stroke-linejoin="round">{body}</svg>'
)

DEFAULT_COLOR = "#cdd1db"


def names() -> list[str]:
    return sorted(_ICONS)


def has(name: str) -> bool:
    return name in _ICONS


def resolve(name_or_emoji: str) -> str | None:
    """Map a registered name OR a known emoji to a canonical icon name (else None)."""
    if not name_or_emoji:
        return None
    if name_or_emoji in _ICONS:
        return name_or_emoji
    return _EMOJI_ALIASES.get(name_or_emoji.strip())


def svg(name_or_emoji: str, color: str = DEFAULT_COLOR, stroke: float = 2.0) -> str:
    """Return a complete, recoloured SVG string for an icon (pure; no Qt).
    Unknown names fall back to a neutral circle so the UI never shows a broken glyph."""
    name = resolve(name_or_emoji) or name_or_emoji
    body = _ICONS.get(name, '<circle cx="12" cy="12" r="8"/>')
    out = _TEMPLATE.format(stroke=stroke, body=body)
    return out.replace("currentColor", color)


# ---------------------------------------------------------------------------
# Brand mark — Ember's four-point star
# ---------------------------------------------------------------------------

# A lively spectrum that also matches Ember's compact action pointer.
_BRAND_STAR_STOPS = ("#ff3b63", "#ffb020", "#45e38c", "#27bfff", "#b64cff")


def brand_star_svg(colors: "tuple[str, str, str, str, str]" = _BRAND_STAR_STOPS,
                   edge: str = "#ffffff") -> str:
    """Return Ember's scalable rainbow four-point star identity mark."""
    c0, c1, c2, c3, c4 = colors
    return (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none">'
        '<defs>'
        '<linearGradient id="emberStar" x1="2" y1="2" x2="22" y2="22" '
        'gradientUnits="userSpaceOnUse">'
        f'<stop offset="0" stop-color="{c0}"/>'
        f'<stop offset="0.24" stop-color="{c1}"/>'
        f'<stop offset="0.48" stop-color="{c2}"/>'
        f'<stop offset="0.72" stop-color="{c3}"/>'
        f'<stop offset="1" stop-color="{c4}"/>'
        '</linearGradient>'
        '</defs>'
        '<path d="M12 1.5 L14.4 9.6 L22.5 12 L14.4 14.4 L12 22.5 '
        'L9.6 14.4 L1.5 12 L9.6 9.6 Z" fill="url(#emberStar)" '
        f'stroke="{edge}" stroke-width="0.7" stroke-linejoin="round"/>'
        f'<circle cx="12" cy="12" r="1.3" fill="{edge}" opacity="0.92"/>'
        '</svg>'
    )


# ---------------------------------------------------------------------------
# Qt wrappers (lazy import; return None on any failure so callers fall back)
# ---------------------------------------------------------------------------

def pixmap(name_or_emoji: str, size: int = 18, color: str = DEFAULT_COLOR, stroke: float = 2.0):
    try:
        from PyQt6.QtCore import QByteArray, Qt
        from PyQt6.QtGui import QPixmap, QPainter
        from PyQt6.QtSvg import QSvgRenderer
        data = QByteArray(svg(name_or_emoji, color, stroke).encode("utf-8"))
        renderer = QSvgRenderer(data)
        scale = 2  # render at 2x for crispness on HiDPI
        pm = QPixmap(size * scale, size * scale)
        pm.fill(Qt.GlobalColor.transparent)
        p = QPainter(pm)
        renderer.render(p)
        p.end()
        pm.setDevicePixelRatio(float(scale))
        return pm
    except Exception:
        return None


def qicon(name_or_emoji: str, size: int = 18, color: str = DEFAULT_COLOR, stroke: float = 2.0):
    try:
        from PyQt6.QtGui import QIcon
        pm = pixmap(name_or_emoji, size, color, stroke)
        if pm is None:
            return None
        return QIcon(pm)
    except Exception:
        return None
