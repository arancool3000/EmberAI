"""Tests for stable support storage and conservative legacy-data migration."""
from pathlib import Path

import app_data


def test_support_dir_honours_override(monkeypatch, tmp_path):
    target = tmp_path / "support"
    monkeypatch.setenv("EMBER_SUPPORT_DIR", str(target))
    assert app_data.data_dir() == target
    assert target.is_dir()


def test_migration_copies_known_data_without_overwriting(monkeypatch, tmp_path):
    support = tmp_path / "support"
    legacy = tmp_path / "old-version" / "System Files"
    legacy.mkdir(parents=True)
    (legacy / "settings.json").write_text('{"api":"old"}', encoding="utf-8")
    (legacy / "memory.json").write_text('{"facts":{}}', encoding="utf-8")
    (legacy / "workflows").mkdir()
    (legacy / "workflows" / "daily.json").write_text("{}", encoding="utf-8")
    monkeypatch.setenv("EMBER_SUPPORT_DIR", str(support))

    copied = app_data.migrate_legacy_data([legacy])
    assert set(copied) == {"settings.json", "memory.json", "workflows"}
    assert (support / "workflows" / "daily.json").exists()

    (support / "settings.json").write_text('{"api":"new"}', encoding="utf-8")
    app_data.migrate_legacy_data([legacy])
    assert (support / "settings.json").read_text(encoding="utf-8") == '{"api":"new"}'
