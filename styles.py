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
QPushButton#primaryBtn {{
    background-color: rgba(59, 130, 246, 235);
    color: #ffffff;
    border: none;
    border-radius: 12px;
    padding: 9px 18px;
    font-size: 13px;
    font-weight: 800;
}}
QPushButton#primaryBtn:hover {{ background-color: rgba(37, 110, 235, 245); }}
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
QPushButton#primaryBtn {
    background-color: rgba(59,130,246,0.95);
    color: #ffffff;
    border: none;
    border-radius: 12px;
    padding: 9px 18px;
    font-size: 13px;
    font-weight: 800;
}
QPushButton#primaryBtn:hover { background-color: rgba(37,110,235,1.0); }
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


# Shared workspace polish layered over both the flat and native-glass themes.  Keeping the
# structural rules in one place means the two themes have identical spacing, hierarchy and
# interaction states; only their materials differ.
WORKSPACE_POLISH = """
QFrame#brandRow { background: transparent; border: none; }
QLabel#brandMark {
    background-color: rgba(122,162,247,0.18);
    color: #dce7ff;
    border: 1px solid rgba(122,162,247,0.36);
    border-radius: 11px;
    font-size: 16px;
    font-weight: 900;
}
QLabel#brandName { color: #f4f6fb; font-size: 15px; font-weight: 850; }
QLabel#brandTagline { color: #81899f; font-size: 10px; font-weight: 600; }
QPushButton#newTask {
    background-color: rgba(122,162,247,0.16);
    color: #edf3ff;
    border: 1px solid rgba(122,162,247,0.38);
    border-radius: 11px;
    padding: 9px 12px;
    font-size: 12px;
    font-weight: 800;
    text-align: left;
}
QPushButton#newTask:hover { background-color: rgba(122,162,247,0.27); border-color: rgba(122,162,247,0.7); }
QLineEdit#historySearch {
    background-color: rgba(255,255,255,0.035);
    border-color: rgba(255,255,255,0.075);
    border-radius: 10px;
    padding: 7px 10px;
    font-size: 11px;
}
QFrame#workspaceHeader {
    background-color: rgba(255,255,255,0.025);
    border: 1px solid rgba(255,255,255,0.07);
    border-radius: 14px;
}
QLabel#workspaceEyebrow { color: #7f889f; font-size: 9px; font-weight: 800; letter-spacing: 1px; }
QLabel#workspaceTitle { color: #f4f6fb; font-size: 15px; font-weight: 850; }
QLabel#modeLabel { color: #8d96aa; font-size: 10px; font-weight: 700; }
QComboBox#modePicker {
    background-color: rgba(255,255,255,0.06);
    border: 1px solid rgba(255,255,255,0.11);
    border-radius: 10px;
    padding: 6px 10px;
    min-width: 102px;
    font-size: 11px;
    font-weight: 750;
}
QLabel#liveDot { color: #4fd1a1; font-size: 16px; }
QLabel#liveLabel { color: #cbd3e3; font-size: 10px; font-weight: 700; }
QFrame#agentControlCard, QFrame#pointerCard {
    background-color: rgba(255,255,255,0.035);
    border: 1px solid rgba(255,255,255,0.075);
    border-radius: 13px;
}
QLabel#controlTitle { color: #eef1f8; font-size: 11px; font-weight: 800; }
QLabel#controlHint { color: #8992a7; font-size: 9px; }
QCheckBox#pointerToggle { color: #d8deea; font-size: 11px; font-weight: 700; }
QSlider#pointerSpeed::groove:horizontal {
    height: 4px; background: rgba(255,255,255,0.10); border-radius: 2px;
}
QSlider#pointerSpeed::sub-page:horizontal { background: #7aa2f7; border-radius: 2px; }
QSlider#pointerSpeed::handle:horizontal {
    width: 13px; height: 13px; margin: -5px 0;
    background: #eef3ff; border: 2px solid #7aa2f7; border-radius: 7px;
}
QLabel#speedValue { color: #dce6fb; font-size: 10px; font-weight: 800; }
QPushButton#commandPalette {
    background-color: rgba(255,255,255,0.055);
    color: #dce2ef;
    border: 1px solid rgba(255,255,255,0.095);
    border-radius: 11px;
    padding: 8px 10px;
    text-align: left;
    font-size: 11px;
    font-weight: 750;
}
QPushButton#commandPalette:hover { background-color: rgba(255,255,255,0.11); border-color: rgba(122,162,247,0.45); }
QFrame#composer {
    background-color: rgba(22,25,38,0.92);
    border: 1px solid rgba(255,255,255,0.12);
    border-radius: 17px;
}
QFrame#composer:focus-within { border-color: rgba(122,162,247,0.72); }
QTextEdit#composerInput {
    background: transparent;
    border: none;
    border-radius: 0;
    padding: 9px 11px 4px 11px;
    font-size: 13px;
}
QLabel#composerHint { color: #737d93; font-size: 9px; padding-left: 4px; }
QPushButton#composerTool {
    background: transparent;
    color: #aeb7c9;
    border: none;
    border-radius: 9px;
    padding: 0;
    font-size: 15px;
}
QPushButton#composerTool:hover { background-color: rgba(255,255,255,0.09); color: #ffffff; }
QPushButton#send {
    border-radius: 12px;
    min-width: 40px;
    min-height: 38px;
}
QPushButton#stopAgent {
    background-color: rgba(248,81,73,0.12);
    color: #ff9c98;
    border: 1px solid rgba(248,81,73,0.24);
    border-radius: 10px;
    padding: 0;
}
QPushButton#stopAgent:hover { background-color: rgba(248,81,73,0.24); color: #ffffff; }
QLabel#emptyKicker { color: #8faeff; font-size: 10px; font-weight: 900; letter-spacing: 1px; }
QLabel#emptyTitle { color: #f5f7fb; font-size: 22px; font-weight: 850; }
QLabel#emptyBody { color: #a2aabd; font-size: 12px; line-height: 1.45; }
QFrame#bubble { border-left: 2px solid rgba(122,162,247,0.46); }
QFrame#bubbleUser { border-right: 2px solid rgba(122,162,247,0.82); }
QFrame#bubbleTool { border-left: 2px solid rgba(79,209,161,0.58); }
/* Secondary windows use the same product grammar as the main workspace. */
QDialog { background-color: #0d0f17; }
QFrame#dialogHeader {
    background-color: rgba(255,255,255,0.028);
    border: 1px solid rgba(255,255,255,0.075);
    border-radius: 15px;
}
QLabel#dialogMark {
    background-color: rgba(122,162,247,0.17);
    color: #e5edff;
    border: 1px solid rgba(122,162,247,0.38);
    border-radius: 12px;
    font-size: 16px;
    font-weight: 900;
}
QLabel#dialogEyebrow { color: #8490a8; font-size: 9px; font-weight: 850; letter-spacing: 1px; }
QLabel#dialogTitle { color: #f5f7fb; font-size: 18px; font-weight: 850; }
QLabel#dialogDescription { color: #9ca6ba; font-size: 11px; }
QLabel#fieldLabel { color: #dce1ec; font-size: 11px; font-weight: 750; }
QLabel#muted, QLabel#dialogStatus { color: #8f99ad; font-size: 10px; }
QLabel#securityHealth {
    background-color: rgba(224,175,104,0.09);
    color: #e7c58f;
    border: 1px solid rgba(224,175,104,0.22);
    border-radius: 11px;
    padding: 9px 12px;
    font-size: 11px;
    font-weight: 800;
}
QLabel#securityHealth[state="protected"] {
    background-color: rgba(79,209,161,0.09);
    color: #94e4c6;
    border-color: rgba(79,209,161,0.24);
}
QLabel#securityHealth[state="limited"] { color: #d6c391; }
QLabel#securityHealth[state="off"] {
    background-color: rgba(248,81,73,0.09);
    color: #ffaaa6;
    border-color: rgba(248,81,73,0.24);
}
QFrame#surfaceCard, QGroupBox {
    background-color: rgba(255,255,255,0.026);
    border: 1px solid rgba(255,255,255,0.075);
    border-radius: 13px;
    margin-top: 9px;
    padding-top: 10px;
}
QGroupBox::title {
    subcontrol-origin: margin;
    left: 12px;
    padding: 0 6px;
    color: #dbe2ef;
    font-size: 11px;
    font-weight: 800;
}
QFrame#featureRow {
    background-color: rgba(255,255,255,0.027);
    border: 1px solid rgba(255,255,255,0.068);
    border-radius: 12px;
}
QFrame#featureRow:hover { background-color: rgba(255,255,255,0.055); border-color: rgba(122,162,247,0.28); }
QLabel#featureIcon {
    background-color: rgba(122,162,247,0.12);
    border: 1px solid rgba(122,162,247,0.22);
    border-radius: 9px;
    font-size: 15px;
}
QLabel#featureName { color: #edf1f8; font-size: 12px; font-weight: 800; }
QLabel#featureDescription { color: #909aae; font-size: 10px; }
QTabWidget#settingsTabs::pane {
    border: 1px solid rgba(255,255,255,0.075);
    border-radius: 13px;
    background-color: rgba(255,255,255,0.018);
    margin-top: 5px;
}
QTabWidget#settingsTabs QTabBar::tab {
    min-width: 72px;
    padding: 9px 8px;
    margin: 0 2px;
    border: none;
    border-radius: 9px;
    text-align: left;
    color: #929caf;
}
QTabWidget#settingsTabs QTabBar::tab:selected {
    background-color: rgba(122,162,247,0.16);
    color: #f3f6fc;
    border: 1px solid rgba(122,162,247,0.28);
}
QTabWidget#settingsTabs QTabBar::tab:hover { background-color: rgba(255,255,255,0.05); color: #eef2f9; }
QListWidget#dialogList {
    background-color: rgba(255,255,255,0.022);
    border: 1px solid rgba(255,255,255,0.075);
    border-radius: 12px;
    padding: 5px;
}
QListWidget#dialogList::item { padding: 9px 8px; margin: 2px; border-radius: 8px; }
QListWidget#dialogList::item:selected { background-color: rgba(122,162,247,0.22); color: #ffffff; }
QPlainTextEdit#codeSurface {
    background-color: #090b11;
    border: 1px solid rgba(255,255,255,0.085);
    border-radius: 12px;
    color: #dbe4f4;
    font-family: 'SF Mono', Menlo, Consolas, monospace;
    font-size: 12px;
}
QFrame#stepRail { background: transparent; border: none; }
QLabel#stepDot { color: #646e82; font-size: 11px; font-weight: 800; }
QLabel#stepDot[active="true"] { color: #8faeff; }
QRadioButton {
    color: #dce2ed;
    background-color: rgba(255,255,255,0.022);
    border: 1px solid rgba(255,255,255,0.065);
    border-radius: 11px;
    padding: 11px 12px;
    spacing: 9px;
}
QRadioButton:hover { background-color: rgba(255,255,255,0.05); border-color: rgba(122,162,247,0.28); }
QRadioButton::indicator { width: 15px; height: 15px; }
QRadioButton::indicator:checked { background: #7aa2f7; border: 4px solid #dce7ff; border-radius: 8px; }
QPushButton#dangerBtn {
    background-color: rgba(248,81,73,0.10);
    color: #ffaaa6;
    border: 1px solid rgba(248,81,73,0.24);
}
QPushButton#dangerBtn:hover { background-color: rgba(248,81,73,0.22); color: #ffffff; }
QPushButton#secondaryBtn { background-color: rgba(255,255,255,0.035); }
QPushButton:disabled {
    background-color: rgba(255,255,255,0.018);
    color: rgba(160,170,190,0.35);
    border-color: rgba(255,255,255,0.035);
}
QProgressBar {
    background-color: rgba(255,255,255,0.06);
    border: none;
    border-radius: 3px;
    min-height: 6px;
}
QProgressBar::chunk { background-color: #7aa2f7; border-radius: 3px; }

/* Unified conversation workspace ---------------------------------------------------- */
QWidget#mainPanel, QScrollArea#chatScroll, QWidget#chatViewport, QWidget#composerShell {
    background: transparent;
    border: none;
}
QWidget#activityShell { background: transparent; border: none; }
QFrame#taskActivity {
    background-color: rgba(255,255,255,0.028);
    border: 1px solid rgba(255,255,255,0.07);
    border-radius: 12px;
}
QPushButton#activityToggle {
    background: transparent;
    border: none;
    color: #aeb7c8;
    padding: 4px 6px;
    text-align: left;
    font-size: 10px;
    font-weight: 750;
}
QPushButton#activityToggle:hover { color: #ffffff; background-color: rgba(255,255,255,0.04); }
QProgressBar#activityProgress {
    background-color: rgba(255,255,255,0.08);
    border: none;
    border-radius: 2px;
}
QProgressBar#activityProgress::chunk { background-color: #7aa2f7; border-radius: 2px; }
QPlainTextEdit#activityDetails {
    background-color: rgba(7,9,14,0.72);
    color: #b9c3d4;
    border: 1px solid rgba(255,255,255,0.055);
    border-radius: 9px;
    padding: 8px 10px;
    font-family: 'SF Mono', Menlo, Consolas, monospace;
    font-size: 9px;
}
QFrame#historyPanel {
    background-color: rgba(255,255,255,0.018);
    border: none;
    border-right: 1px solid rgba(255,255,255,0.065);
    border-radius: 0;
}
QListWidget#historyList {
    background: transparent;
    border: none;
    border-radius: 0;
    padding: 2px 0;
}
QListWidget#historyList::item {
    padding: 9px 10px;
    margin: 1px 0;
    border-radius: 9px;
    color: #cbd2df;
    font-size: 11px;
}
QListWidget#historyList::item:selected {
    background-color: rgba(255,255,255,0.095);
    color: #ffffff;
}
QListWidget#historyList::item:hover { background-color: rgba(255,255,255,0.055); }
QLabel#workspaceTitle {
    color: #f5f7fb;
    font-size: 13px;
    font-weight: 800;
    padding: 0;
}
QLabel#statusBar {
    color: #7f899d;
    font-size: 9px;
    font-weight: 600;
    padding: 1px 0 0 0;
    letter-spacing: 0;
}
QPushButton#modelPickerBtn, QPushButton#toolsBtn {
    background-color: transparent;
    color: #aeb7c8;
    border: 1px solid transparent;
    border-radius: 9px;
    padding: 6px 9px;
    font-size: 10px;
    font-weight: 750;
}
QPushButton#modelPickerBtn:hover, QPushButton#toolsBtn:hover,
QPushButton#toolsBtn[open="true"] {
    background-color: rgba(255,255,255,0.065);
    color: #ffffff;
    border-color: rgba(255,255,255,0.075);
}
QComboBox#modePicker {
    background-color: transparent;
    border: 1px solid transparent;
    border-radius: 9px;
    min-width: 86px;
    padding: 6px 8px;
    color: #aeb7c8;
    font-size: 10px;
    font-weight: 750;
}
QComboBox#modePicker:hover {
    background-color: rgba(255,255,255,0.065);
    border-color: rgba(255,255,255,0.075);
    color: #ffffff;
}
QFrame#commandPanel {
    background-color: #11141d;
    border: 1px solid rgba(255,255,255,0.085);
    border-radius: 14px;
}
QFrame#bubble {
    background: transparent;
    border: none;
    border-radius: 0;
    padding: 0;
    margin: 5px 0 11px 0;
}
QFrame#bubbleUser {
    background-color: rgba(255,255,255,0.105);
    border: 1px solid rgba(255,255,255,0.075);
    border-radius: 18px;
    padding: 0;
    margin: 5px 0 11px 0;
}
QFrame#bubbleUser QLabel { color: #f3f5f9; }
QFrame#bubbleSystem {
    background-color: rgba(255,255,255,0.025);
    border: 1px solid rgba(255,255,255,0.055);
    border-radius: 12px;
    padding: 0;
    margin: 4px 0 9px 0;
}
QFrame#bubbleTool {
    background-color: rgba(79,209,161,0.035);
    border: 1px solid rgba(79,209,161,0.11);
    border-radius: 10px;
    padding: 0;
    margin: 3px 0 8px 0;
}
QFrame#bubbleError, QFrame#bubbleConfirm {
    padding: 0;
    margin: 4px 0 10px 0;
}
QLabel#bubbleBody {
    color: #e8ebf2;
    line-height: 1.45;
}
QFrame#typingIndicator {
    background: transparent;
    border: none;
    padding: 3px 0;
    margin: 3px 0 9px 0;
}
QFrame#emptyState {
    background: transparent;
    border: none;
}
QLabel#emptyMark {
    background-color: rgba(122,162,247,0.13);
    color: #d9e5ff;
    border: 1px solid rgba(122,162,247,0.25);
    border-radius: 15px;
    font-size: 20px;
    font-weight: 900;
}
QLabel#emptyTitle { color: #f5f7fb; font-size: 24px; font-weight: 850; }
QLabel#emptyBody { color: #939caf; font-size: 12px; padding: 0 36px 10px 36px; }
QPushButton#promptCard {
    background-color: rgba(255,255,255,0.028);
    color: #cbd2df;
    border: 1px solid rgba(255,255,255,0.075);
    border-radius: 13px;
    padding: 11px 14px;
    min-width: 164px;
    text-align: left;
    font-size: 11px;
    font-weight: 650;
}
QPushButton#promptCard:hover {
    background-color: rgba(255,255,255,0.065);
    color: #ffffff;
    border-color: rgba(122,162,247,0.34);
}
QFrame#composer {
    background-color: #171a24;
    border: 1px solid rgba(255,255,255,0.115);
    border-radius: 20px;
}
QTextEdit#composerInput {
    padding: 10px 12px 4px 12px;
    font-size: 13px;
}
QPushButton#send {
    background-color: #f0f2f6;
    color: #10121a;
    border-radius: 12px;
}
QPushButton#send:hover { background-color: #ffffff; }
QPushButton#titleBtn, QPushButton#closeBtn {
    background: transparent;
    border-color: transparent;
}
QPushButton#titleBtn:hover { background-color: rgba(255,255,255,0.07); border-color: transparent; }
"""
