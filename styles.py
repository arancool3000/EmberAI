"""Ember UI stylesheets (Qt QSS), extracted from ui.py to keep that module focused on
behaviour. `_glass_style()` builds the Liquid-Glass theme; `STYLE` is the neutral
fallback theme. Pure strings — no imports needed."""


def _glass_style(alpha: int = 180, accent: str = "#ffffff", see_through: int = 70,
                 blurred: bool = False) -> str:
    """Neutral Liquid Glass stylesheet.

    Dark *frosted* glass (light text needs a dark-ish veil), but dressed with real glass
    cues so it reads as glass instead of a flat tint: a top-down light-falloff gradient,
    a bright specular rim ("water-droplet" edge), and a generous corner radius. When a
    native NSVisualEffectView blur is mounted behind (blurred=True) the veil is thinned so
    the real blur shows through.
    """
    if blurred:
        # A real desktop blur sits behind the window — keep the veil light so it shows through.
        win_a = max(12, int((100 - see_through) * 0.45))
    else:
        # No native blur (the default): the window must stay essentially opaque, or the
        # desktop shows through and the UI becomes unreadable. The glass look then comes from
        # the gradient, the bright specular rim, and the frosted side panels — not from
        # see-through. glass_opacity still nudges it within a safe, readable band.
        win_a = max(232, min(250, 252 - int(see_through * 0.2)))
    top_a = max(8, win_a - 8)                          # glass catches light at the top…
    mid_a = win_a
    bot_a = min(235, win_a + 36)                       # …and deepens at the bottom for legibility
    bubble_a = max(118, int(alpha * 0.70))
    input_a = max(145, int(alpha * 0.82))
    bg = (f"qlineargradient(x1:0, y1:0, x2:0, y2:1,"
          f" stop:0 rgba(40, 43, 54, {top_a}),"
          f" stop:0.5 rgba(17, 19, 26, {mid_a}),"
          f" stop:1 rgba(9, 10, 14, {bot_a}))")
    bg_bubble = f"rgba(255, 255, 255, {bubble_a})"
    bg_input = f"rgba(255, 255, 255, {input_a})"
    bg_control = "rgba(255, 255, 255, 34)"
    bg_control_hover = "rgba(255, 255, 255, 56)"
    rim = "rgba(255, 255, 255, 145)"                    # bright specular edge — the droplet rim
    edge = "rgba(255, 255, 255, 72)"
    edge_soft = "rgba(255, 255, 255, 36)"
    return f"""
QMessageBox, QInputDialog, QDialog {{ background-color: rgba(18, 18, 20, 236); }}
QMessageBox QLabel, QInputDialog QLabel {{ color: #f6f6f4; background-color: transparent; font-size: 13px; }}
QWidget#root {{
    background: {bg};
    border: 1.5px solid {rim};
    border-radius: 26px;
}}
QFrame#historyPanel {{
    background-color: rgba(255, 255, 255, 28);
    border: 1px solid rgba(255, 255, 255, 34);
    border-radius: 16px;
}}
QFrame#commandPanel {{
    background-color: rgba(255, 255, 255, 24);
    border: 1px solid rgba(255, 255, 255, 34);
    border-radius: 16px;
}}
QLabel#sideTitle {{
    color: #f6f6f4;
    font-size: 13px;
    font-weight: 800;
    padding: 2px 4px;
}}
QLabel#sectionTitle {{
    color: #f6f6f4;
    font-size: 12px;
    font-weight: 850;
    padding: 4px 4px 2px 4px;
}}
QLabel#sideHint {{
    color: rgba(246, 246, 244, 145);
    font-size: 10px;
    padding: 2px 4px;
}}
QLabel#panelHint {{
    color: rgba(246, 246, 244, 150);
    font-size: 10px;
    padding: 0 4px 4px 4px;
}}
QFrame#statusStrip {{
    background-color: rgba(0, 0, 0, 34);
    border: 1px solid rgba(255, 255, 255, 30);
    border-radius: 12px;
}}
QLabel#statusMetric {{
    color: rgba(246, 246, 244, 210);
    font-size: 10px;
    font-weight: 700;
}}
QListWidget#historyList {{
    background-color: rgba(255, 255, 255, 20);
    color: #f6f6f4;
    border: 1px solid rgba(255, 255, 255, 30);
    border-radius: 12px;
    padding: 4px;
    outline: none;
}}
QListWidget#historyList::item {{
    padding: 8px 7px;
    border-radius: 9px;
    margin: 2px;
}}
QListWidget#historyList::item:selected {{
    background-color: rgba(255, 255, 255, 76);
}}
QListWidget#historyList::item:hover {{
    background-color: rgba(255, 255, 255, 46);
}}
QLabel#title {{
    color: #f6f6f4;
    font-weight: 750;
    font-size: 15px;
    padding: 6px 8px;
    letter-spacing: 0.2px;
}}
QLabel#statusBar {{
    color: rgba(246, 246, 244, 170);
    font-size: 11px;
    padding: 0 12px 6px 12px;
    font-weight: 600;
}}
QScrollArea, QScrollArea > QWidget, QScrollArea > QWidget > QWidget {{
    background: transparent;
    border: none;
}}
QScrollBar:vertical {{
    background: transparent;
    width: 8px;
    margin: 4px 2px;
}}
QScrollBar::handle:vertical {{
    background: rgba(255, 255, 255, 60);
    border-radius: 4px;
    min-height: 24px;
}}
QScrollBar::handle:vertical:hover {{ background: rgba(255, 255, 255, 110); }}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}
QScrollBar:horizontal {{ background: transparent; height: 6px; margin: 2px 4px; }}
QScrollBar::handle:horizontal {{ background: rgba(255, 255, 255, 60); border-radius: 3px; min-width: 24px; }}
QScrollBar::handle:horizontal:hover {{ background: rgba(255, 255, 255, 110); }}
QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {{ width: 0; height: 0; }}
QScrollBar::add-page, QScrollBar::sub-page {{ background: transparent; }}
QTextEdit, QPlainTextEdit {{
    background-color: {bg_input};
    color: #f7f7f5;
    border: 1px solid {edge_soft};
    border-radius: 15px;
    padding: 10px 12px;
    font-family: -apple-system, 'SF Pro Text', 'Segoe UI', system-ui, sans-serif;
    font-size: 13px;
    selection-background-color: rgba(255, 255, 255, 82);
}}
QLineEdit {{
    background-color: {bg_input};
    color: #f7f7f5;
    border: 1px solid {edge_soft};
    border-radius: 15px;
    padding: 8px 12px;
    font-size: 13px;
}}
QLineEdit:focus, QTextEdit:focus, QPlainTextEdit:focus {{
    border: 1px solid rgba(255, 255, 255, 132);
}}
QPushButton {{
    background-color: {bg_control};
    color: #f6f6f4;
    border: 1px solid {edge_soft};
    border-radius: 12px;
    padding: 6px 14px;
    font-size: 12px;
    font-weight: 650;
}}
QPushButton:hover {{
    background-color: {bg_control_hover};
    border-color: rgba(255, 255, 255, 100);
}}
QPushButton:pressed {{ background-color: rgba(255, 255, 255, 180); color: #08080a; }}
QPushButton#send {{
    background-color: rgba(255, 255, 255, 218);
    color: #08080a;
    font-weight: 700;
    font-size: 13px;
    border: 1px solid rgba(255, 255, 255, 190);
}}
QPushButton#send:hover {{
    background-color: rgba(255, 255, 255, 238);
}}
QPushButton#approve {{ background-color: #3fb950; color: #ffffff; font-weight: bold; }}
QPushButton#deny    {{ background-color: #f85149; color: #ffffff; font-weight: bold; }}
QPushButton#titleBtn {{
    background-color: {bg_control};
    color: rgba(246, 246, 244, 220);
    border: 1px solid {edge_soft};
    border-radius: 10px;
    padding: 0;
    font-size: 14px;
    font-weight: 700;
}}
QPushButton#titleBtn:hover {{
    background-color: {bg_control_hover};
    color: #ffffff;
    border-color: rgba(255, 255, 255, 120);
}}
QPushButton#closeBtn {{
    background-color: {bg_control};
    color: rgba(246, 246, 244, 220);
    border: 1px solid {edge_soft};
    border-radius: 10px;
    padding: 0;
    font-size: 14px;
    font-weight: 700;
}}
QPushButton#closeBtn:hover {{
    background-color: #f85149;
    color: #ffffff;
    border-color: #f85149;
}}
QPushButton#chip {{
    background-color: {bg_control};
    color: rgba(246, 246, 244, 210);
    border: 1px solid {edge_soft};
    border-radius: 15px;
    padding: 4px 12px;
    font-size: 11px;
    font-weight: 650;
}}
QPushButton#chip:hover {{
    background-color: {bg_control_hover};
    color: #ffffff;
    border-color: rgba(255, 255, 255, 100);
}}
QPushButton#commandAction {{
    background-color: rgba(255, 255, 255, 30);
    color: rgba(246, 246, 244, 225);
    border: 1px solid rgba(255, 255, 255, 42);
    border-radius: 11px;
    padding: 7px 10px;
    font-size: 11px;
    font-weight: 750;
    text-align: left;
}}
QPushButton#commandAction:hover {{
    background-color: rgba(255, 255, 255, 58);
    color: #ffffff;
    border-color: rgba(255, 255, 255, 106);
}}
QPushButton#commandTask {{
    background-color: rgba(255, 255, 255, 10);
    color: rgba(246, 246, 244, 200);
    border: 1px solid rgba(255, 255, 255, 26);
    border-left: 3px solid rgba(122, 162, 247, 170);
    border-radius: 9px;
    padding: 6px 10px;
    font-size: 11px;
    font-weight: 600;
    font-style: italic;
    text-align: left;
}}
QPushButton#commandTask:hover {{
    background-color: rgba(255, 255, 255, 30);
    color: #ffffff;
}}
QFrame#segToggle {{
    background-color: rgba(255, 255, 255, 26);
    border: 1px solid rgba(255, 255, 255, 40);
    border-radius: 15px;
}}
QPushButton#segBtn {{
    background: transparent;
    color: #c8d0e0;
    border: none;
    border-radius: 12px;
    padding: 7px 20px;
    font-size: 13px;
    font-weight: 800;
}}
QPushButton#segBtn:checked {{
    background-color: rgba(255, 255, 255, 235);
    color: #08080a;
}}
QPushButton#voiceToggle {{
    background-color: rgba(255, 255, 255, 220);
    color: #08080a;
    border: 1px solid rgba(255, 255, 255, 180);
    border-radius: 14px;
    padding: 10px 12px;
    font-size: 13px;
    font-weight: 850;
}}
QPushButton#voiceToggleOn {{
    background-color: rgba(46, 160, 120, 230);
    color: #ffffff;
    border: 1px solid rgba(155, 255, 210, 180);
    border-radius: 14px;
    padding: 10px 12px;
    font-size: 13px;
    font-weight: 850;
}}
QFrame#bubble {{
    background-color: {bg_bubble};
    border: 1px solid {edge_soft};
    border-radius: 18px;
    padding: 10px 14px;
    margin: 4px 2px;
}}
QFrame#bubbleUser {{
    background-color: rgba(255, 255, 255, 218);
    border: 1px solid rgba(255, 255, 255, 190);
    border-radius: 18px;
    padding: 10px 14px;
    margin: 4px 2px;
}}
QFrame#bubbleUser QLabel {{ color: #08080a; }}
QFrame#bubbleTool {{
    background-color: rgba(255, 255, 255, 24);
    border: 1px solid rgba(255, 255, 255, 34);
    border-radius: 12px;
    padding: 6px 10px;
    margin: 2px 4px;
}}
QFrame#bubbleError {{
    background-color: rgba(56, 32, 32, 200);
    border: 1px solid #f85149;
    border-radius: 12px;
    padding: 10px 14px;
    margin: 4px 2px;
}}
QFrame#bubbleConfirm {{
    background-color: rgba(56, 48, 22, 200);
    border: 1px solid #d29922;
    border-radius: 12px;
    padding: 10px 14px;
    margin: 4px 2px;
}}
QFrame#typingIndicator {{
    background-color: {bg_bubble};
    border: 1px solid {edge_soft};
    border-radius: 18px;
    padding: 8px 14px;
    margin: 4px 2px;
}}
QLabel#typingDots {{
    color: rgba(255, 255, 255, 220);
    font-size: 14px;
    font-weight: bold;
    letter-spacing: 3px;
}}
QLabel {{ color: #f6f6f4; font-size: 13px; }}
QLabel#meta {{ color: rgba(246, 246, 244, 160); font-size: 10px; font-weight: 650; }}
QMenu {{
    background-color: {bg_bubble};
    border: 1px solid {edge_soft};
    border-radius: 16px;
    padding: 7px;
}}
QMenu::item {{
    padding: 9px 18px 9px 16px;
    border-radius: 11px;
    margin: 1px 4px;
    color: #f6f6f4;
}}
QMenu::item:selected {{ background-color: rgba(255, 255, 255, 30); }}
QMenu::separator {{ height: 1px; background: {edge_soft}; margin: 6px 12px; }}
QFrame#pillRoot {{
    background-color: {bg};
    border: 1px solid {edge};
    border-radius: 19px;
}}
QFrame#pillRoot:hover {{ border-color: rgba(255, 255, 255, 130); }}
QTabBar::tab {{
    background-color: transparent;
    color: rgba(246, 246, 244, 150);
    padding: 8px 14px;
    min-width: 92px;
    border: none;
    font-size: 12px;
    font-weight: 500;
}}
QTabBar::tab:selected {{
    color: #ffffff;
    border-bottom: 2px solid rgba(255, 255, 255, 210);
}}
QTabBar::tab:hover {{ color: #f6f6f4; }}
QTabWidget::pane {{ border: none; }}
QCheckBox {{ color: #f6f6f4; font-size: 12px; spacing: 8px; }}
QCheckBox::indicator {{
    width: 16px; height: 16px;
    border: 1px solid {edge_soft};
    background: rgba(255, 255, 255, 26);
    border-radius: 4px;
}}
QCheckBox::indicator:checked {{
    background: rgba(255, 255, 255, 220);
    border-color: rgba(255, 255, 255, 220);
}}
QComboBox {{
    background-color: {bg_input};
    color: #f6f6f4;
    border: 1px solid {edge_soft};
    border-radius: 12px;
    padding: 6px 10px;
    font-size: 12px;
}}
QComboBox:focus, QComboBox:hover {{ border-color: rgba(255, 255, 255, 120); }}
QComboBox::drop-down {{ border: none; width: 20px; }}
"""


STYLE = """
/* ===== Ember — neutral liquid interface fallback ===== */
/* Palette: graphite glass, frosted white controls, no colored glass tint. */

/* Dialogs: dark panel + light text so native QMessageBox text is always readable. */
QMessageBox, QInputDialog, QDialog { background-color: #161926; }
QMessageBox QLabel, QInputDialog QLabel {
    color: #eef1f8; background-color: transparent; font-size: 13px;
}

QWidget#root {
    background-color: #0c0e16;
    border: 1px solid rgba(255, 255, 255, 0.09);
    border-radius: 20px;
}
QFrame#historyPanel {
    background-color: rgba(255,255,255,0.035);
    border: 1px solid rgba(255,255,255,0.08);
    border-radius: 16px;
}
QFrame#commandPanel {
    background-color: rgba(255,255,255,0.035);
    border: 1px solid rgba(255,255,255,0.08);
    border-radius: 16px;
}
QLabel#sideTitle {
    color: #eef1f8;
    font-size: 13px;
    font-weight: 800;
    padding: 2px 4px;
}
QLabel#sectionTitle {
    color: #eef1f8;
    font-size: 12px;
    font-weight: 800;
    padding: 4px 4px 2px 4px;
}
QLabel#sideHint {
    color: #9298ad;
    font-size: 10px;
    padding: 2px 4px;
}
QLabel#panelHint {
    color: #a7adbd;
    font-size: 10px;
    padding: 0 4px 4px 4px;
}
QFrame#statusStrip {
    background-color: rgba(255,255,255,0.025);
    border: 1px solid rgba(255,255,255,0.07);
    border-radius: 12px;
}
QLabel#statusMetric {
    color: #cbd1df;
    font-size: 10px;
    font-weight: 700;
}
QListWidget#historyList {
    background-color: rgba(255,255,255,0.025);
    color: #eef1f8;
    border: 1px solid rgba(255,255,255,0.07);
    border-radius: 12px;
    padding: 4px;
    outline: none;
}
QListWidget#historyList::item {
    padding: 8px 7px;
    border-radius: 9px;
    margin: 2px;
}
QListWidget#historyList::item:selected { background-color: rgba(255,255,255,0.16); }
QListWidget#historyList::item:hover { background-color: rgba(255,255,255,0.10); }
QLabel#title {
    color: #eef1f8;
    font-weight: 700;
    font-size: 15px;
    padding: 6px 8px;
    letter-spacing: 0.4px;
}
QLabel#statusBar {
    color: #9298ad;
    font-size: 11px;
    padding: 0 12px 6px 12px;
    font-weight: 500;
    letter-spacing: 0.2px;
}

QScrollArea, QScrollArea > QWidget, QScrollArea > QWidget > QWidget {
    background: transparent; border: none;
}
QScrollBar:vertical { background: transparent; width: 9px; margin: 4px 2px; }
QScrollBar::handle:vertical { background: rgba(255,255,255,0.13); border-radius: 4px; min-height: 28px; }
QScrollBar::handle:vertical:hover { background: rgba(255,255,255,0.24); }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
QScrollBar:horizontal { background: transparent; height: 6px; margin: 2px 4px; }
QScrollBar::handle:horizontal { background: rgba(255,255,255,0.13); border-radius: 3px; min-width: 28px; }
QScrollBar::handle:horizontal:hover { background: rgba(255,255,255,0.24); }
QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal { width: 0; height: 0; }
QScrollBar::add-page, QScrollBar::sub-page { background: transparent; }

QTextEdit, QPlainTextEdit {
    background-color: #161926;
    color: #eef1f8;
    border: 1px solid rgba(255,255,255,0.10);
    border-radius: 14px;
    padding: 11px 14px;
    font-family: -apple-system, 'SF Pro Text', 'Segoe UI', system-ui, sans-serif;
    font-size: 13px;
    selection-background-color: rgba(255,255,255,0.32);
}
QLineEdit {
    background-color: #161926;
    color: #eef1f8;
    border: 1px solid rgba(255,255,255,0.10);
    border-radius: 12px;
    padding: 9px 13px;
    font-size: 13px;
    selection-background-color: rgba(255,255,255,0.32);
}
QLineEdit:focus, QTextEdit:focus, QPlainTextEdit:focus { border: 1px solid rgba(255,255,255,0.56); }

QPushButton {
    background-color: #1e2233;
    color: #eef1f8;
    border: 1px solid rgba(255,255,255,0.08);
    border-radius: 10px;
    padding: 7px 15px;
    font-size: 12px;
    font-weight: 600;
}
QPushButton:hover { background-color: rgba(255,255,255,0.12); border-color: rgba(255,255,255,0.42); }
QPushButton:pressed { background-color: #14172180; }

QPushButton#send {
    background-color: rgba(255,255,255,0.92);
    color: #08080a; font-weight: 700; font-size: 14px; border: none; border-radius: 11px;
}
QPushButton#send:hover {
    background-color: rgba(255,255,255,0.98);
}
QPushButton#approve { background-color: #2ea043; color: #ffffff; font-weight: 700; border: none; }
QPushButton#approve:hover { background-color: #3fb950; }
QPushButton#deny    { background-color: #e5484d; color: #ffffff; font-weight: 700; border: none; }
QPushButton#deny:hover { background-color: #f85149; }

QPushButton#titleBtn {
    background-color: rgba(255,255,255,0.05);
    color: #c9cee0;
    border: 1px solid rgba(255,255,255,0.07);
    border-radius: 9px;
    padding: 0;
    font-size: 15px;
    font-weight: 600;
}
QPushButton#titleBtn:hover { background-color: rgba(255,255,255,0.14); color: #ffffff; border-color: rgba(255,255,255,0.44); }
QPushButton#closeBtn {
    background-color: rgba(255,255,255,0.05);
    color: #c9cee0;
    border: 1px solid rgba(255,255,255,0.07);
    border-radius: 9px;
    padding: 0;
    font-size: 15px;
    font-weight: 600;
}
QPushButton#closeBtn:hover { background-color: #e5484d; color: #ffffff; border-color: #e5484d; }

QFrame#pillRoot {
    background-color: #0c0e16;
    border: 1px solid rgba(255,255,255,0.54);
    border-radius: 20px;
}
QFrame#pillRoot:hover { border-color: rgba(255,255,255,0.82); }

QPushButton#chip {
    background-color: rgba(255,255,255,0.04);
    color: #b9c2e0;
    border: 1px solid rgba(255,255,255,0.09);
    border-radius: 15px;
    padding: 5px 14px;
    font-size: 11px;
    font-weight: 600;
}
QPushButton#chip:hover {
    background-color: rgba(255,255,255,0.14);
    color: #ffffff;
    border-color: rgba(255,255,255,0.42);
}
QPushButton#commandAction {
    background-color: rgba(255,255,255,0.045);
    color: #d6dae5;
    border: 1px solid rgba(255,255,255,0.08);
    border-radius: 11px;
    padding: 7px 10px;
    font-size: 11px;
    font-weight: 700;
    text-align: left;
}
QPushButton#commandAction:hover {
    background-color: rgba(255,255,255,0.12);
    color: #ffffff;
    border-color: rgba(255,255,255,0.36);
}
QPushButton#commandTask {
    background-color: rgba(255,255,255,0.045);
    color: #c3c9db;
    border: 1px solid rgba(255,255,255,0.10);
    border-left: 3px solid rgba(122,162,247,0.85);
    border-radius: 9px;
    padding: 6px 10px;
    font-size: 11px;
    font-weight: 600;
    font-style: italic;
    text-align: left;
}
QPushButton#commandTask:hover {
    background-color: rgba(122,162,247,0.18);
    color: #ffffff;
    border-color: rgba(122,162,247,0.55);
}
QFrame#segToggle {
    background-color: rgba(255,255,255,0.10);
    border: 1px solid rgba(255,255,255,0.16);
    border-radius: 15px;
}
QPushButton#segBtn {
    background: transparent;
    color: #c8d0e0;
    border: none;
    border-radius: 12px;
    padding: 7px 20px;
    font-size: 13px;
    font-weight: 800;
}
QPushButton#segBtn:checked {
    background-color: rgba(238,241,248,0.95);
    color: #08080a;
}
QPushButton#voiceToggle {
    background-color: rgba(238,241,248,0.92);
    color: #08080a;
    border: none;
    border-radius: 14px;
    padding: 10px 12px;
    font-size: 13px;
    font-weight: 800;
}
QPushButton#voiceToggle:hover {
    background-color: rgba(255,255,255,0.98);
}
QPushButton#voiceToggleOn {
    background-color: #2fa678;
    color: #ffffff;
    border: 1px solid rgba(153,255,209,0.55);
    border-radius: 14px;
    padding: 10px 12px;
    font-size: 13px;
    font-weight: 800;
}
QPushButton#voiceToggleOn:hover {
    background-color: #38bd8a;
}

QFrame#typingIndicator {
    background-color: #161926;
    border: 1px solid rgba(255,255,255,0.08);
    border-radius: 16px;
    padding: 9px 15px;
    margin: 4px 2px;
}
QLabel#typingDots { color: rgba(255,255,255,0.86); font-size: 14px; font-weight: bold; letter-spacing: 3px; }
QMenu {
    background-color: #161926;
    border: 1px solid rgba(255,255,255,0.10);
    border-radius: 16px;
    padding: 7px;
}
QMenu::item { padding: 9px 18px; border-radius: 11px; margin: 1px 4px; color: #e6e6ea; }
QMenu::item:selected { background-color: rgba(255,255,255,0.10); }
QMenu::separator { height: 1px; background: rgba(255,255,255,0.10); margin: 6px 12px; }

QFrame#bubble {
    background-color: #161926;
    border: 1px solid rgba(255,255,255,0.08);
    border-radius: 16px;
    padding: 12px 16px;
    margin: 5px 2px;
}
QFrame#bubbleUser {
    background-color: rgba(255,255,255,0.9);
    border: 1px solid rgba(255,255,255,0.72);
    border-radius: 16px;
    padding: 12px 16px;
    margin: 5px 2px;
}
QFrame#bubbleUser QLabel { color: #08080a; }
QFrame#bubbleTool {
    background-color: rgba(255,255,255,0.03);
    border: 1px solid rgba(255,255,255,0.07);
    border-radius: 10px;
    padding: 7px 10px;
    margin: 2px;
}
QFrame#bubbleError {
    background-color: #2e1719;
    border: 1px solid #e5484d;
    border-radius: 14px;
    padding: 12px 16px;
    margin: 5px 2px;
}
QFrame#bubbleConfirm {
    background-color: #2c2614;
    border: 1px solid #d29922;
    border-radius: 14px;
    padding: 12px 16px;
    margin: 5px 2px;
}

QLabel { color: #eef1f8; font-size: 13px; }
QLabel#meta { color: #9298ad; font-size: 10px; font-weight: 600; letter-spacing: 0.3px; }

QTabBar::tab {
    background-color: transparent;
    color: #9298ad;
    padding: 8px 12px;
    min-width: 92px;
    border: none;
    font-size: 12px;
    font-weight: 600;
}
QTabBar::tab:selected { color: #ffffff; border-bottom: 2px solid rgba(255,255,255,0.82); }
QTabBar::tab:hover { color: #eef1f8; }
QTabWidget::pane { border: none; }

QCheckBox { color: #eef1f8; font-size: 12px; spacing: 9px; }
QCheckBox::indicator {
    width: 17px; height: 17px;
    border: 1px solid rgba(255,255,255,0.18);
    background: #161926;
    border-radius: 5px;
}
QCheckBox::indicator:checked { background: rgba(255,255,255,0.86); border-color: rgba(255,255,255,0.86); }

QComboBox {
    background-color: #161926;
    color: #eef1f8;
    border: 1px solid rgba(255,255,255,0.10);
    border-radius: 10px;
    padding: 7px 12px;
    font-size: 12px;
}
QComboBox:focus, QComboBox:hover { border-color: rgba(255,255,255,0.5); }
QComboBox::drop-down { border: none; width: 22px; }
QComboBox QAbstractItemView {
    background-color: #1e2233; color: #eef1f8;
    border: 1px solid rgba(255,255,255,0.12);
    border-radius: 10px; selection-background-color: rgba(255,255,255,0.28);
}
"""
