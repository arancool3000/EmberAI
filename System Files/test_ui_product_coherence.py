"""Product-level UI guards for Ember's shared navigation and secondary surfaces."""
import ast
from pathlib import Path


ROOT = Path(__file__).parent
UI = (ROOT / "ui.py").read_text(encoding="utf-8")
BROWSER = (ROOT / "ember_browser.py").read_text(encoding="utf-8")
STYLES = (ROOT / "styles.py").read_text(encoding="utf-8")


def _class_source(source: str, name: str) -> str:
    tree = ast.parse(source)
    cls = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == name)
    return ast.get_source_segment(source, cls)


def _func_source(source: str, name: str) -> str:
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
            return ast.get_source_segment(source, node)
    raise AssertionError(f"function {name} not found")


def test_major_dialogs_use_the_shared_product_surface():
    for name in (
        "ManualModeDialog", "SettingsDialog", "FeaturesDialog", "AntivirusDialog",
        "AdBlockerDialog", "SetupTourDialog", "TerminalDialog", "AgentsDialog",
        "RemoteLinkDialog", "DownloadGuardDialog", "UpdateDialog", "StorageInspectorDialog",
        "NetworkInspectorDialog", "ClipboardHistoryDialog",
    ):
        src = _class_source(UI, name)
        assert "_polish_dialog(self)" in src, name


def test_discovery_and_setup_have_clear_navigation():
    features = _class_source(UI, "FeaturesDialog")
    setup = _class_source(UI, "SetupTourDialog")
    assert "returnPressed.connect(self._open_first_visible)" in features
    assert "result" in features and "Esc closes" in features
    assert "1  About you" in setup and "4  Ready" in setup
    assert "Set up later" in setup


def test_main_workspace_is_one_conversation_not_competing_tabs():
    window = _class_source(UI, "EmberWindow")
    assert 'QPushButton("Agent")' not in window
    assert 'QPushButton("Local AI")' not in window
    assert "self.main_stack" not in window
    assert "center_col.addWidget(main_panel, 1)" in window
    assert "Change the model, including local Ollama" in window
    assert "self._tools_open = False" in window


def test_conversation_has_chat_bubbles_and_right_aligned_user_bubbles():
    window = _class_source(UI, "EmberWindow")
    assert 'kind == "user"' in window
    assert "Qt.AlignmentFlag.AlignRight" in window
    assert "Qt.AlignmentFlag.AlignHCenter" in window
    assert "frame_w = min(860, available)" in window
    # Assistant replies sit in a subtle chat bubble (proper bubbles, not transparent document text).
    assert "QFrame#bubble {\n    background-color: rgba(255,255,255,0.045)" in STYLES
    assert "QFrame#bubbleUser" in STYLES
    assert "QFrame#emptyState" in STYLES


def test_task_activity_is_expandable_and_motion_is_user_controllable():
    window = _class_source(UI, "EmberWindow")
    settings = _class_source(UI, "SettingsDialog")
    for required in ("_toggle_activity_details", "_activity_tool_call",
                     "_activity_tool_result", "_activity_complete"):
        assert required in window
    assert 'setObjectName("taskActivity")' in window
    assert 'setObjectName("activityDetails")' in window
    assert "redaction.scrub_obj" in window
    assert "_animate_drawer" in window and "_start_ambient_pulse" in window
    assert "motion_level" in settings and "Dynamic — expressive motion" in settings
    assert "QFrame#taskActivity" in STYLES and "QPlainTextEdit#activityDetails" in STYLES


def test_browser_advanced_tools_live_in_an_overflow_menu():
    browser = _class_source(BROWSER, "EmberBrowser")
    assert "def _show_more_menu" in browser
    for label in ("Bookmarks", "History", "Reader mode", "Passwords and autofill", "Extensions"):
        assert label in browser
    assert 'self._show_more_menu' in browser


def test_destructive_secondary_actions_are_visually_distinct():
    for name in ("AntivirusDialog", "RemoteLinkDialog", "DownloadGuardDialog", "StorageInspectorDialog"):
        assert 'setObjectName("dangerBtn")' in _class_source(UI, name), name


def test_cross_platform_copy_does_not_call_every_computer_a_mac():
    assert "Control this Mac" not in UI
    assert "control this Mac" not in UI


def test_new_standalone_tools_are_discoverable_and_routed():
    window = _class_source(UI, "EmberWindow")
    for token in ("__storage__", "__network_inspector__", "__clipboard__"):
        assert token in UI
        assert token in window


def test_endpoint_security_is_evidence_led_not_scareware():
    security = _class_source(UI, "AntivirusDialog")
    for required in (
        "Endpoint Security", "Findings", "Quarantine", "Activity",
        "Contain confirmed malware", "no known indicators found",
        "AI triage added advisory context", "Export security report",
        "file contents leave this device",
    ):
        assert required in security, required
    for forbidden in ("Threats found", "Quarantine ALL", "Norton-style", "AI cleared"):
        assert forbidden not in security, forbidden
    assert "Direct deletion is disabled" in security


def test_macos_shows_ember_not_python312():
    # The menu bar comes from CFBundleName; the Dock / Activity Monitor / Force-Quit label comes
    # from the PROCESS name (which otherwise defaults to "python3.12"). Override both.
    name_fn = _func_source(UI, "_set_macos_app_name")
    assert 'info["CFBundleName"] = name' in name_fn
    assert "setProcessName_(name)" in name_fn
    assert '_set_macos_app_name("Ember")' in UI


def test_first_run_primes_every_macos_permission_in_one_guide():
    primer = _func_source(UI, "_prime_permissions")
    # All four permissions Ember actually needs — including Input Monitoring for the global
    # hotkey / "Hey Ember" wake word, which the old two-step primer left out.
    for perm in ("Accessibility", "Screen Recording", "Microphone", "Input Monitoring"):
        assert perm in primer, perm
    for fn in ("request_accessibility", "has_screen_recording", "has_microphone",
               "has_input_monitoring"):
        assert fn in primer, fn
    assert "prompt=True" in primer                      # actually fires the OS prompts
    assert "quit and reopen Ember once" in primer       # one clear, consolidated instruction
    # Scheduled once at launch (replacing the old split _check_accessibility / _check_screen_and_mic).
    assert "QTimer.singleShot(900, self._prime_permissions)" in UI
    assert "_check_screen_and_mic_permissions" not in UI


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    for test in tests:
        test()
        print("PASS", test.__name__)
    print(f"{len(tests)}/{len(tests)} UI product-coherence tests passed")
