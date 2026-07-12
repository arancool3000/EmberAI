"""Cross-platform offscreen smoke test for the product's primary Qt surfaces."""
import os
import tempfile
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("EMBER_SAFE_MODE", "1")
os.environ.setdefault("EMBER_SUPPORT_DIR", tempfile.mkdtemp(prefix="ember_ui_security_"))

from PyQt6.QtWidgets import QApplication

import ui


def main() -> int:
    data = Path(tempfile.mkdtemp(prefix="ember_ui_smoke_"))
    ui._data_dir = lambda: data
    app = QApplication(["ember-ui-smoke"])
    settings = ui.load_settings()
    widgets = [
        ui.FeaturesDialog(lambda _action: None),
        ui.SettingsDialog(settings),
        ui.StorageInspectorDialog(),
        ui.NetworkInspectorDialog(),
        ui.ClipboardHistoryDialog(),
        ui.TerminalDialog(),
        ui.AntivirusDialog(),
    ]
    try:
        for widget in widgets:
            widget.show()
            app.processEvents()
            assert widget.isVisible(), type(widget).__name__
            assert widget.minimumWidth() > 0 and widget.minimumHeight() > 0
            widget.close()
    finally:
        for widget in widgets:
            widget.close()
        app.quit()
    print(f"PASS: {len(widgets)} Qt surfaces opened offscreen")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
