"""Static guard against a release-pipeline regression: every release from 1.9.2 through 1.9.6
silently failed to publish ANYTHING (not even the successful macOS/Windows builds) because the
`release` job's `needs: [build-macos, build-windows, build-linux]` meant GitHub Actions skips it
entirely if build-linux fails alone - which it did, on an unrelated appimagetool architecture-
detection issue. Users stayed stuck on 1.9.1 with no visible error anywhere.

No PyYAML in CI (only rapidfuzz + stdlib is installed) - this greps the raw workflow text rather
than parsing YAML, matching the hermetic-stdlib-only convention every other test in this repo
follows.
Run: python test_release_workflow.py"""
import os
import re

_PATH = os.path.join(os.path.dirname(__file__), ".github", "workflows", "release.yml")
_SRC = open(_PATH, encoding="utf-8").read()


def _release_job_block() -> str:
    """The `release:` top-level job's YAML block (from its header to the next top-level key,
    or EOF)."""
    m = re.search(r"\n  release:\n(.*)$", _SRC, re.S)
    assert m, "release: job not found in release.yml"
    block = m.group(1)
    # Stop at the next top-level (2-space-indented) job key, if any follow.
    end = re.search(r"\n  [A-Za-z_-]+:\n", "\n" + block)
    return block[:end.start()] if end else block


def test_release_job_does_not_hard_require_build_linux():
    block = _release_job_block()
    assert "if:" in block, "release job must gate on an if: condition, not just needs:"
    if_clause = block.split("if:", 1)[1].split("runs-on:", 1)[0]
    assert "build-linux.result" not in if_clause, (
        "release job's if: condition hard-requires build-linux to succeed again - a Linux "
        "packaging failure would silently throw away working macOS/Windows builds and publish "
        "nothing, exactly like the 1.9.2-1.9.6 outage.")
    assert "build-macos.result == 'success'" in if_clause
    assert "build-windows.result == 'success'" in if_clause
    assert "always()" in if_clause


def test_release_job_still_waits_on_build_linux_artifact():
    # It should still be listed in needs (so the DAG waits for it and its artifact, if any, is
    # downloadable) - just not treated as a hard gate via `if:`.
    block = _release_job_block()
    needs_line = block.split("\n", 1)[0]
    assert "build-linux" in needs_line


def test_asset_collection_tolerates_a_missing_linux_artifact():
    block = _release_job_block()
    assert "cp artifacts/Ember-Linux/Ember-Linux.AppImage out/ 2>/dev/null || true" in block


def test_appimagetool_arch_is_set():
    assert "ARCH=x86_64 ./appimagetool" in _SRC, (
        "appimagetool needs an explicit ARCH when it can't auto-detect a single architecture "
        "from the AppDir, or it refuses to build at all with exit code 1.")


def _run():
    fns = [v for k, v in sorted(globals().items())
           if k.startswith("test_") and callable(v)]
    ok = 0
    for fn in fns:
        try:
            fn(); print("PASS", fn.__name__); ok += 1
        except Exception as e:
            print("FAIL", fn.__name__, e)
    print(f"{ok}/{len(fns)} passed")
    return ok == len(fns)


if __name__ == "__main__":
    import sys
    sys.exit(0 if _run() else 1)
