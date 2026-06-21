"""Classifies tool calls into risk levels for confirmation prompts."""
from __future__ import annotations

import re
from pathlib import Path

DANGEROUS_PATTERNS = [
    r"\brm\s+-rf?\b",
    r"\bdel\s+/[sq]\b",
    r"\bformat\s+[a-z]:",
    r"\bdiskpart\b",
    r"\bbcdedit\b",
    r"\bregedit\b",
    r"\breg\s+(delete|add)\b",
    r"\bnet\s+user\s+\w+\s+/delete\b",
    r"\bshutdown\s+/[srgp]\b",
    r"\bSet-ExecutionPolicy\s+Unrestricted\b",
    r"\bRemove-Item\s+.*-Recurse\b",
    r"\bRemove-ItemProperty\b",
    r"\bStop-Computer\b",
    r"\bRestart-Computer\b",
    r"\bClear-EventLog\b",
    r"\bsfc\s+/scannow\b",
    r"\bdism\b",
    r"\bcipher\s+/w\b",
    r"\bcacls\b|\bicacls\b.*\b/grant\b",
    r"\battrib\s+.*[+-]r\s+.*\\Windows\\",
    r"\btake[ -]?own\b",
    r"powercfg\b.*-h\b",
    r"\bnetsh\s+(?:winsock\s+reset|int\s+ip\s+reset)\b",
    r"Invoke-WebRequest.*\|.*iex",
    r"curl.*\|.*sh",
    r"Add-MpPreference.*ExclusionPath",
    r"Set-MpPreference.*Disable",
    # --- macOS / Unix destructive commands (this is the mac build; run_shell is zsh) ---
    r"\brm\s+-[rf]",                       # rm -rf / rm -f / rm -r
    r"\bdd\b.*\bof=/dev/",                 # dd writing to a raw device
    r"\bmkfs\b",                           # format a filesystem
    r":\s*\(\)\s*\{.*\|.*&\s*\}",          # bash fork bomb :(){ :|:& };:
    r"\bsudo\b",                           # privilege escalation
    r">\s*/dev/(disk|rdisk|sd)",           # clobber a disk device
    r"\bchmod\s+-R\b",                     # recursive permission change
    r"\bchown\s+-R\b",                     # recursive ownership change
    r"\bkillall\b",                        # kill processes by name
    r"\b(shutdown|halt|reboot)\b",         # power state
    r"\bdiskutil\s+(erase|reformat|partition|secureerase)",
    r"\blaunchctl\s+(bootout|unload|remove)\b",
    r"\bcurl\b.*\|\s*(ba|z)?sh",           # curl … | sh / bash / zsh
    r"\bwget\b.*\|\s*(ba|z)?sh",
    r"\bdefaults\s+delete\b",
    r"\bnvram\b.*-c",                       # clear NVRAM
    r"\bspctl\s+--master-disable\b",        # disable Gatekeeper
    r"\bcsrutil\s+disable\b",               # disable SIP
]

SENSITIVE_PATHS = [
    # NOTE: matched against a path normalized to lowercase forward slashes.
    # macOS
    r"/system/",
    r"/etc/",
    r"/private/etc",
    r"/usr/bin", r"/usr/sbin", r"/usr/lib",
    r"/library/launchdaemons", r"/library/launchagents",
    r"/library/keychains", r"/keychains/",
    r"/.ssh", r"/.aws", r"/.gnupg",
    # Windows
    r"c:/windows/system32",
    r"c:/windows/syswow64",
    r"c:/program files",
    r"c:/programdata",
    r"/boot/",
    r"hkey_local_machine",
    r"hklm:",
    r"hkcu:/software/microsoft/windows/currentversion/run",
]

# Tools that send local data out over the network or e-mail — real exfiltration
# vectors for an autonomous computer-use agent, so always confirm.
EXFIL_TOOLS = {"send_email", "http_post"}

# macOS key chords that are destructive or disruptive enough to confirm.
MAC_DANGEROUS_CHORDS = {
    "cmd+q": ("medium", "quits the frontmost app"),
    "cmd+w": ("medium", "closes the frontmost window"),
    "cmd+shift+q": ("high", "logs out of macOS"),
    "cmd+option+esc": ("medium", "opens Force Quit"),
    "ctrl+cmd+q": ("medium", "locks the screen"),
    "cmd+ctrl+q": ("medium", "locks the screen"),
}


FILE_OPS_MEDIUM = {"organize_folder", "bulk_rename", "move_matching_files", "unzip_archive",
                    "zip_files", "trash_file"}

SAFE_READONLY = {
    "take_screenshot", "capture_window", "list_directory", "read_file", "search_files",
    "get_event_logs", "get_reliability_events", "get_minidumps", "get_system_info",
    "get_installed_drivers", "get_running_processes", "get_performance", "get_windows_updates",
    "get_screen_size", "mouse_position", "find_ui_elements", "list_windows",
    "wait", "wait_for_screen_change", "move_mouse", "zoom_screenshot",
    "ask_claude", "recall", "list_quick_fixes",
    "browser_get_page", "browser_check_captcha", "browser_screenshot", "browser_current",
    "browser_list_tabs", "pause_for_human",
    "get_folder_size", "folder_tree", "find_duplicate_files", "find_large_files",
    "list_monitors", "grep_files", "diff_files", "count_lines",
    "speed_test", "define_word", "currency_convert", "stock_quote", "github_search_repos",
    "random_number", "random_choice", "dice_roll", "flip_coin",
    "ocr_image", "ocr_screen", "browser_wait_for_element", "browser_get_text",
    "calculate_text_stats",
    "watch_folder_start", "watch_folder_events", "watch_folder_stop",
    "record_audio",
    "list_desktop_items", "desktop_overview",
    # more_tools - read-only
    "http_get", "public_ip", "dns_lookup", "network_ping", "web_search",
    "wikipedia_summary", "weather_lookup", "translate_text",
    "pdf_extract_text", "excel_read", "csv_read", "json_query",
    "calculator", "generate_password", "generate_uuid",
    "hash_text", "hash_file", "base64_encode", "base64_decode",
    "url_encode", "url_decode", "now",
    "get_battery", "get_volume", "env_get", "env_list",
    "color_at", "clipboard_get", "clipboard_history_get", "clipboard_history_snapshot",
    "git_status", "git_log", "git_diff",
    "scan_file", "list_quarantine", "security_status",
    "check_url", "list_web_policy", "web_status",
    "get_audit_log", "verify_audit_log", "get_security_mode",
    "get_plan", "list_pro_features", "vpn_status", "list_vpn_locations",
    "disk_usage", "list_open_ports", "password_strength", "system_health",
    "list_startup_items", "scan_host_ports", "network_devices", "wifi_info",
    "file_info", "password_pwned_check", "keychain_get",
    "ai_detect_text", "ai_detect_image", "password_generate",
    "read_document", "scan_secrets", "unit_convert", "network_connections",
    "local_ai_status", "local_ai_ask", "list_macros", "get_macro", "run_macro",
    "describe_image", "transcribe_audio", "security_checkup", "free_vpn_configs",
    "url_quote", "url_unquote", "json_pretty", "case_convert", "slugify", "text_stats",
    "word_frequency", "extract_emails", "extract_urls", "regex_find", "find_replace",
    "sort_lines", "dedupe_lines", "reverse_text", "rot13", "uuid4", "random_int",
    "random_pick", "lorem_ipsum", "int_to_roman", "roman_to_int", "hex_to_rgb", "rgb_to_hex",
    "number_to_words", "is_prime", "days_between", "tip_calculator", "bmi_calculator",
    # --- roadmap backlog: read-only feature tools ---
    "vault_status", "vault_get_key", "vault_list_keys",       # key vault (never returns full secret)
    "usage_summary",                                          # usage dashboard
    "download_guard_status", "download_guard_events",         # download protection
    "scan_processes", "scan_command",                        # fileless / behavioral scan (read-only)
    "fileless_guard_status", "fileless_guard_events",         # real-time fileless protection
    "security_center_status", "security_center_events",      # unified Security Center (read-only)
    "scan_network", "scan_persistence",                      # active network / persistence scans
    "list_run_modes", "agent_list", "agent_get",            # run modes / agent profiles (read-only)
    "list_workflows",                                         # workflow recorder
    "snippet_list", "snippet_get", "snippet_expand",         # snippet expander
    "email_breach_check", "pick_screen_color", "screenshot_monitor",
    "screen_record_status",
    "list_plugins",                                           # plugin system
}

SAFE_INTERACTION = {
    "click", "click_element_by_text", "drag", "type_text", "paste_text",
    "press_key", "scroll", "open_url", "open_app", "open_path", "focus_window",
    "remember", "forget",
    "browser_open", "browser_navigate", "browser_click_text", "browser_click_selector",
    "browser_fill", "browser_scroll", "browser_back", "browser_forward", "browser_reload",
    "browser_dismiss_cookies", "browser_new_tab", "browser_switch_tab", "browser_close_tab",
    "clipboard_set", "set_volume", "toggle_mute",
    "image_resize", "image_crop", "image_convert", "qr_generate", "create_calendar_event",
    "excel_write", "http_post", "download_file", "send_email",
    "right_click_element_by_text",
    "snap_window", "move_window", "minimize_all_other_windows", "show_desktop",
    "switch_window", "media_keys", "show_notification", "say_text",
    # --- roadmap backlog: low-risk write/act feature tools ---
    "vault_store_key",                                        # store a secret (reversible)
    "usage_reset",                                            # clears usage counters
    "download_guard_start", "download_guard_stop",           # start/stop the watcher
    "fileless_guard_start", "fileless_guard_stop",           # start/stop fileless protection
    "security_center_start", "security_center_stop",         # start/stop the Security Center
    "set_run_mode", "agent_create", "agent_delete",         # run-mode + agent profile management
    "agent_run", "spawn_agent",                             # launch a saved / ad-hoc sub-agent
    "record_workflow_start", "record_workflow_stop", "delete_workflow",
    "snippet_save", "snippet_delete",                        # snippet CRUD
    "screen_record_start", "screen_record_stop",             # local screen capture file
    "reload_plugins", "create_plugin_template",              # plugin management
}

QUICK_FIX_RISK = {
    # low / read-only-ish
    "show_startup": "low", "show_services": "low",
    # medium / reversible
    "flush_dns": "medium", "release_renew_ip": "medium", "clear_temp": "medium",
    "restart_explorer": "medium", "check_disk": "medium",
    # high / heavy or system-touching
    "reset_network": "high", "sfc_scan": "high", "dism_restore": "high",
}


def _norm_path(path: str) -> str:
    """Lowercase + forward-slash a path so SENSITIVE_PATHS (mac + win) match uniformly.
    The old code did .replace('/', '\\\\'), which mangled every macOS path."""
    return (path or "").lower().replace("\\", "/")


_CHORD_ALIAS = {
    "command": "cmd", "⌘": "cmd", "control": "ctrl", "⌃": "ctrl",
    "opt": "option", "alt": "option", "⌥": "option",
    "escape": "esc", "⎋": "esc", "return": "enter", "delete": "del", "windows": "win",
}


def _normalize_chord(keys: str) -> str:
    """Normalize a key-combo string token-by-token (split on '+') so aliases collapse
    without substring corruption (e.g. a naive 'opt'->'option' would mangle 'option')."""
    parts = [_CHORD_ALIAS.get(tok, tok)
             for tok in (keys or "").lower().replace(" ", "").split("+") if tok]
    return "+".join(parts)


def classify(tool_name: str, args: dict) -> tuple[str, str]:
    """Return (risk_level, reason). risk_level in {'low','medium','high'}."""
    a = {k: (v if isinstance(v, str) else str(v)) for k, v in args.items()}

    if tool_name in SAFE_READONLY:
        return "low", "read-only / safe"

    if tool_name in EXFIL_TOOLS:
        return "high", "sends data over the network / e-mail"

    if tool_name in SAFE_INTERACTION:
        if tool_name in ("type_text", "paste_text"):
            text = a.get("text", "")
            if any(s in text.lower() for s in ["password", "credit card", "ssn", "social security"]):
                return "high", "typing sensitive-looking text"
        if tool_name == "press_key":
            raw = (a.get("keys", "") or "").lower().replace(" ", "")
            if raw in {"ctrl+alt+del", "win+l"}:
                return "high", "system-level chord"
            chord = _normalize_chord(a.get("keys", ""))
            if chord in MAC_DANGEROUS_CHORDS:
                return MAC_DANGEROUS_CHORDS[chord]
        if tool_name == "open_path":
            path = _norm_path(a.get("path", ""))
            for sensitive in SENSITIVE_PATHS:
                if sensitive in path:
                    return "medium", f"opens sensitive path"
        return "low", "screen interaction"

    if tool_name == "do_sequence":
        worst = "low"
        actions = []
        try:
            import json as _json
            raw = args.get("actions")
            if isinstance(raw, str):
                actions = _json.loads(raw)
            elif isinstance(raw, list):
                actions = raw
        except Exception:
            actions = []
        for act in actions if isinstance(actions, list) else []:
            if not isinstance(act, dict):
                continue
            sub_risk, _ = classify(act.get("tool", ""), act.get("args", {}) or {})
            order = {"low": 0, "medium": 1, "high": 2}
            if order.get(sub_risk, 0) > order.get(worst, 0):
                worst = sub_risk
        return worst, f"sequence (worst step risk: {worst})"

    if tool_name == "quick_fix":
        name = a.get("name", "")
        risk = QUICK_FIX_RISK.get(name, "high")
        return risk, f"quick_fix:{name}"

    if tool_name in {"run_shell", "run_powershell", "run_cmd"}:
        cmd = a.get("command", "")
        for pat in DANGEROUS_PATTERNS:
            if re.search(pat, cmd, re.IGNORECASE):
                return "high", f"matches dangerous pattern: {pat}"
        if re.search(r"\b(install|uninstall|update)\b", cmd, re.IGNORECASE):
            return "high", "install/uninstall/update operation"
        if re.search(r"\b(Set-|New-|Remove-|Stop-|Start-Service)\b", cmd):
            return "high", "system modification cmdlet"
        return "medium", "shell command"

    if tool_name == "browser_evaluate":
        return "medium", "arbitrary JS in browser"

    if tool_name == "kill_process":
        return "high", "terminating a process"

    if tool_name == "service_action":
        action = a.get("action", "status")
        return ("low" if action == "status" else "high"), f"service: {action}"

    if tool_name == "power_action":
        action = a.get("action", "")
        if action in ("lock", "logoff", "sleep", "hibernate"):
            return "medium", f"power: {action}"
        if action in ("restart", "shutdown"):
            return "high", f"power: {action} (closes everything)"
        return "high", "unknown power action"

    if tool_name in FILE_OPS_MEDIUM:
        if str(args.get("dry_run", "")).lower() == "true" or args.get("dry_run") is True:
            return "low", "file op (dry run)"
        return "medium", "file move/rename/extract"

    if tool_name == "write_file":
        path = _norm_path(a.get("path", ""))
        for sensitive in SENSITIVE_PATHS:
            if sensitive in path:
                return "high", f"writing to sensitive path: {sensitive}"
        return "medium", "file write"

    if tool_name == "run_in_sandbox":
        return "medium", "runs a program inside an isolated sandbox"
    if tool_name == "restore_quarantined":
        return "high", "restores a quarantined (malicious) file"
    if tool_name == "delete_quarantined":
        return "medium", "permanently deletes a quarantined file"
    if tool_name in {"add_web_block", "add_web_allow", "remove_web_block"}:
        return "medium", "changes the website block/allow policy"
    if tool_name == "set_agent_mode":
        return "high", "changes Ember's capability mode"
    if tool_name == "vpn_connect":
        return "high", "changes your network routing (VPN connect)"
    if tool_name in {"vpn_disconnect", "add_vpn_location", "remove_vpn_location", "set_plan"}:
        return "medium", "VPN / plan configuration change"
    if tool_name == "scan_directory":
        return "medium", "scans a folder and may quarantine malware"
    if tool_name == "secure_delete":
        return "high", "permanently shreds a file (irreversible)"
    if tool_name == "clean_temp":
        if str(a.get("dry_run", "true")).lower() == "false":
            return "high", "deletes temp/cache files"
        return "low", "temp/cache scan (dry run)"
    if tool_name in {"keychain_store", "encrypt_file", "decrypt_file", "media_convert",
                     "qr_make", "strip_metadata", "make_chart", "generate_image"}:
        return "medium", "writes a file / stores a secret"

    # --- roadmap backlog: tools that need a clearer / higher risk than the sets imply ---
    if tool_name == "replay_workflow":
        return "high", "replays recorded mouse/keyboard input (can drive any app)"
    if tool_name == "vault_delete_key":
        return "medium", "deletes a stored secret from the key vault"

    return "medium", "unclassified tool"


def needs_confirmation(risk: str) -> bool:
    """User said: only confirm very risky items."""
    return risk == "high"


# --- Agent capability modes -------------------------------------------------
# A blast-radius cap the user can set. Enforced in the agent dispatch loop.
VALID_MODES = ("full", "restricted", "read_only")

# Non-mutating tools allowed even in read-only mode (in addition to SAFE_READONLY).
_READ_ONLY_EXTRA = {
    "scan_file", "list_quarantine", "security_status",
    "check_url", "list_web_policy", "web_status",
    "get_audit_log", "verify_audit_log", "get_security_mode",
    "get_plan", "list_pro_features", "vpn_status", "list_vpn_locations",
    "disk_usage", "list_open_ports", "password_strength", "system_health",
}


def current_mode() -> str:
    try:
        import antivirus
        m = antivirus.get_config().get("agent_mode", "full")
        return m if m in VALID_MODES else "full"
    except Exception:
        return "full"


def get_mode() -> dict:
    return {"ok": True, "mode": current_mode()}


def set_mode(mode: str) -> dict:
    if mode not in VALID_MODES:
        return {"ok": False, "error": f"mode must be one of {VALID_MODES}"}
    try:
        import antivirus
        antivirus.set_config(agent_mode=mode)
        return {"ok": True, "mode": mode}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def mode_allows(tool_name: str, risk: str) -> tuple[bool, str]:
    """Whether the current capability mode permits a tool. Returns (allowed, reason)."""
    mode = current_mode()
    if mode == "full":
        return True, ""
    if mode == "read_only":
        if tool_name in SAFE_READONLY or tool_name in _READ_ONLY_EXTRA:
            return True, ""
        return False, "blocked: Ember is in read-only mode (only safe, read-only tools allowed)"
    if mode == "restricted":
        if risk == "high":
            return False, "blocked: Ember is in restricted mode (high-risk actions are disabled)"
        return True, ""
    return True, ""
