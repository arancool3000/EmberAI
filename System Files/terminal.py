"""In-app terminal + code runner — a real REPL inside Ember (beats Open Interpreter's terminal:
it's built in, runs both shell AND Python, and keeps a live Python session between runs).

The Python REPL (`PyRepl`) is a PURE, dependency-light core: it execs code in a persistent
namespace and captures stdout / stderr / exceptions, so it's fully unit-tested with no GUI and no
subprocess. Shell execution reuses Ember's existing OS-aware runner (lazy-imported so this module
stays importable without the GUI deps).
"""
from __future__ import annotations

import contextlib
import io
import traceback


class PyRepl:
    """A persistent Python REPL. ``run(code)`` execs it in a namespace that survives across calls
    (so you can define a variable in one run and use it in the next), capturing all output."""

    def __init__(self):
        self.ns: dict = {"__name__": "__ember_repl__", "__builtins__": __builtins__}

    def reset(self) -> None:
        self.ns = {"__name__": "__ember_repl__", "__builtins__": __builtins__}

    def run(self, code: str) -> dict:
        """Execute `code`. A bare expression echoes its repr (like a REPL); statements run as-is.
        Returns {ok, stdout, stderr}. Never raises — exceptions are captured into stderr."""
        out, err = io.StringIO(), io.StringIO()
        ok = True
        src = code if code is not None else ""
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            try:
                try:
                    compiled = compile(src, "<ember>", "eval")
                except SyntaxError:
                    exec(compile(src, "<ember>", "exec"), self.ns)
                else:
                    val = eval(compiled, self.ns)
                    if val is not None:
                        self.ns["_"] = val
                        print(repr(val))
            except SystemExit:
                pass
            except BaseException:
                ok = False
                err.write(traceback.format_exc())
        return {"ok": ok, "stdout": out.getvalue(), "stderr": err.getvalue()}


def classify(line: str) -> str:
    """Decide whether a unified-input line is 'shell' or 'python' (for a single input box).
    Rules: leading '!' or '$' -> shell; leading '>>> ' or 'py ' -> python; otherwise default to
    shell (a terminal's natural default). The leading marker is stripped by `strip_marker`."""
    s = (line or "").lstrip()
    if s[:1] in ("!", "$"):
        return "shell"
    if s.startswith(">>>") or s[:3].lower() == "py ":
        return "python"
    return "shell"


def strip_marker(line: str) -> str:
    """Remove a leading shell/python marker ('!', '$', '>>>', 'py ') from a unified-input line."""
    s = (line or "").lstrip()
    if s[:1] in ("!", "$"):
        return s[1:].lstrip()
    if s.startswith(">>>"):
        return s[3:].lstrip()
    if s[:3].lower() == "py ":
        return s[3:].lstrip()
    return line


def run_shell(cmd: str, timeout: float = 60.0) -> dict:
    """Run a shell command via Ember's OS-aware runner (zsh/PowerShell). Returns its result dict.
    Lazy-imports tools so this module loads without the GUI deps."""
    cmd = (cmd or "").strip()
    if not cmd:
        return {"ok": False, "error": "empty command"}
    try:
        import tools
        return tools.run_powershell(cmd, timeout=timeout)
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}
