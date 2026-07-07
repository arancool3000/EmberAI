# PyInstaller spec for Ember (cross-platform: macOS + Windows + Linux)
# Build with:  pyinstaller --noconfirm Ember.spec
import sys
from PyInstaller.utils.hooks import collect_all, collect_submodules

IS_MAC = sys.platform == "darwin"
IS_WIN = sys.platform.startswith("win")
IS_LINUX = sys.platform.startswith("linux")

datas = []
binaries = []
hiddenimports = []

# Packages common to both platforms.
common_pkgs = ["google.genai", "google.ai", "google.api_core", "google.auth",
               "google.protobuf", "google.rpc", "mss", "pyautogui",
               "rapidfuzz", "psutil", "websocket", "send2trash", "anthropic",
               "openai",   # OpenAI/ChatGPT + OpenAI-compatible providers (openai_agent)
               "mcp",      # Model Context Protocol server so the app can run `--mcp-server`
               "speech_recognition", "pyttsx3", "pyaudio", "pynput",
               "PyPDF2", "openpyxl", "qrcode",
               "certifi"]   # bundles cacert.pem so HTTPS (auto-updater) verifies in the frozen app
win_pkgs = ["uiautomation", "comtypes", "keyboard", "pycaw"]
mac_pkgs = ["Vision", "Quartz", "Foundation", "objc", "ApplicationServices"]

for pkg in common_pkgs + (win_pkgs if IS_WIN else []) + (mac_pkgs if IS_MAC else []):
    try:
        d, b, h = collect_all(pkg)
        datas += d
        binaries += b
        hiddenimports += h
    except Exception:
        pass

hiddenimports += collect_submodules("google")
# Ember's own modules.
hiddenimports += ["single_instance", "automation", "manual_mode", "more_tools",
                  "extra_tools", "screen_vision", "tools", "browser", "file_ops",
                  "memory", "safety", "antivirus", "web_policy", "audit", "redaction",
                  "plan", "vpn", "utilities", "cleanup", "nettools", "mediatools", "privacy",
                  "models", "voice", "scheduled_tasks", "ember_browser", "ai_detect", "quick_tools",
                  "power_tools", "chart_tools", "local_ai", "macros", "creative", "security_extras",
                  "claude_bridge", "claude_agent", "openai_agent", "ollama_agent",
                  "ember_bridge", "ember_mcp_server", "mcp_setup", "update_signing", "av_static",
                  "tool_args", "sign_release",
                  "mac_permissions",
                  "PyQt6.QtCore", "PyQt6.QtGui", "PyQt6.QtWidgets",
                  "PyQt6.QtWebEngineWidgets", "PyQt6.QtWebEngineCore",
                  # QtWebEngineCore depends on QtWebChannel internally (the JS<->Python bridge
                  # Chromium's embedded renderer uses) even though no Ember source file imports
                  # it directly - PyInstaller's static analysis can't see that C++-level
                  # dependency, and it used to be in `excludes` below (trimmed as "unused"),
                  # which broke Ember Browser entirely: "ModuleNotFoundError: No module named
                  # 'PyQt6.QtWebChannel'" the first time QWebEngineView was imported.
                  "PyQt6.QtWebChannel",
                  "PyPDF2", "openpyxl", "qrcode", "qrcode.image.pil"]

if IS_WIN:
    hiddenimports += collect_submodules("comtypes")
    hiddenimports += [
        "mss.windows", "pyautogui._pyautogui_win",
        "win32api", "win32con", "win32gui", "win32process",
        "pythoncom", "pywintypes", "comtypes.client", "comtypes.gen",
        "psutil._pswindows", "pyttsx3.drivers", "pyttsx3.drivers.sapi5",
        "pynput.keyboard._win32", "pynput.mouse._win32", "pycaw",
    ]
elif IS_MAC:
    hiddenimports += [
        "mss.darwin", "pyautogui._pyautogui_osx",
        "psutil._psosx", "pyttsx3.drivers", "pyttsx3.drivers.nsss",
        "pynput.keyboard._darwin", "pynput.mouse._darwin",
        "Vision", "Quartz", "Foundation", "objc", "ApplicationServices",
    ]
elif IS_LINUX:
    hiddenimports += [
        "mss.linux", "pyautogui._pyautogui_x11",
        "psutil._pslinux", "pyttsx3.drivers", "pyttsx3.drivers.espeak",
        "pynput.keyboard._xorg", "pynput.mouse._xorg",
    ]

icon_file = "icon.ico" if IS_WIN else "icon.png"

a = Analysis(
    ["main.py"],
    pathex=[],
    binaries=binaries,
    datas=datas + [("icon.ico", "."), ("icon.png", ".")],
    hiddenimports=hiddenimports,
    hookspath=[],
    runtime_hooks=[],
    excludes=[
        "tkinter", "matplotlib", "numpy", "pandas", "scipy",
        "notebook", "jupyter", "IPython",
        "PyQt6.Qt3DAnimation", "PyQt6.Qt3DCore", "PyQt6.Qt3DExtras", "PyQt6.Qt3DInput",
        "PyQt6.Qt3DLogic", "PyQt6.Qt3DRender", "PyQt6.QtBluetooth", "PyQt6.QtDesigner",
        "PyQt6.QtHelp", "PyQt6.QtMultimedia", "PyQt6.QtMultimediaWidgets", "PyQt6.QtNfc",
        "PyQt6.QtOpenGL", "PyQt6.QtOpenGLWidgets", "PyQt6.QtPdf", "PyQt6.QtPdfWidgets",
        "PyQt6.QtPositioning", "PyQt6.QtQml", "PyQt6.QtQuick", "PyQt6.QtQuick3D",
        "PyQt6.QtQuickWidgets", "PyQt6.QtRemoteObjects", "PyQt6.QtSensors",
        "PyQt6.QtSerialPort", "PyQt6.QtSpatialAudio", "PyQt6.QtSql", "PyQt6.QtSvg",
        "PyQt6.QtSvgWidgets", "PyQt6.QtTest", "PyQt6.QtTextToSpeech",
        "PyQt6.QtWebSockets", "PyQt6.QtXml",
    ],
    noarchive=False,
)
pyz = PYZ(a.pure, a.zipped_data)

exe = EXE(
    pyz,
    a.scripts,
    [],
    name="Ember",
    icon=icon_file,
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    uac_admin=False,
    exclude_binaries=True,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=False,
    name="Ember",
)

# On macOS, wrap the binary in a proper .app bundle so Screen Recording /
# Accessibility permissions attach to a stable bundle identity.
if IS_MAC:
    app = BUNDLE(
        coll,
        name="Ember.app",
        icon="icon.png",
        bundle_identifier="com.ember.aiagent",
        info_plist={
            "CFBundleName": "Ember",
            "CFBundleDisplayName": "Ember",
            "CFBundleShortVersionString": "2.0.0",
            "NSHighResolutionCapable": True,
            "LSUIElement": False,
            # Permission usage strings macOS shows on first prompt.
            "NSCameraUsageDescription": "Ember does not use the camera.",
            "NSMicrophoneUsageDescription": "Ember uses the microphone for voice commands.",
            "NSAppleEventsUsageDescription": "Ember controls other apps to automate tasks.",
        },
    )
