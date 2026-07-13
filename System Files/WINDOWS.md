# Ember on Windows

This folder is the macOS build of Ember, but the codebase is largely cross-platform.
Here is the honest state of the Windows version.

## Install & run (Windows)

```bat
install.bat        REM installs deps (PyQt6, Gemini/Anthropic SDKs, uiautomation, ...)
run.bat            REM launches Ember
```

Build a standalone `Ember.exe`:

```powershell
./build-windows.ps1     # PyInstaller -> dist\Ember\Ember.exe
```

`requirements.txt` and `Ember.spec` are both platform-aware: Windows-only packages
(`pywin32`, `uiautomation`, `comtypes`, `keyboard`, `pycaw`) and macOS-only packages
(`pyobjc-framework-Vision`, `pyobjc-framework-Quartz`) install/bundle only on their
own OS, so a plain `pip install -r requirements.txt` works on either platform.

## What already works cross-platform

These run on Windows today with no porting:

- **Screenshots** (`take_screenshot`, `zoom_screenshot`) — use `mss` + Pillow.
- **Mouse / keyboard** (`click`, `move_mouse`, `drag`, `type_text`, `press_key`,
  `scroll`, `paste_text`) — use `pyautogui`.
- **The entire new screen-vision layer** (`screen_vision.py`):
  - `read_screen_text`, `locate_text`, `select_screen_text`, `wait_for_text`,
    `assert_text_visible`, `smart_click`.
  - OCR uses **Apple Vision on macOS** and **Windows.Media.Ocr on Windows**, selected
    automatically — no extra install on either OS.
  - Retina/HiDPI coordinate scaling is measured at runtime, so clicks land correctly.
- **`smart_click`** degrades gracefully: on Windows the accessibility lookup is a no-op,
  so it resolves targets via OCR — still exact, still no grid guessing.
- All the non-UI tools (`more_tools.py`, `file_ops.py`, web/network/document/image tools).

## What still needs a Windows implementation for full parity

`tools.py` currently implements a few functions with macOS AppleScript (`osascript`).
On Windows these return errors (callers fall back where possible). To reach full parity,
port these to Windows UI Automation (`uiautomation`, already a Windows dependency):

- `find_ui_elements` — enumerate the UIA tree (the Windows equivalent already shipped in
  earlier Ember builds; restore the `uiautomation`-based version here).
- `click_element_by_text`, `right_click_element_by_text` — click by accessible name via UIA.
- `list_windows`, `focus_window`, `capture_window` — enumerate top-level windows via `win32gui`.

Until that port lands, Windows users get full **OCR-based** clicking (which is what the
"no blind guessing" upgrade is built on) plus all keyboard/browser automation. The
accessibility-tree path is the only macOS-specific piece.
