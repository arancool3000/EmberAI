"""Make charts/graphs with matplotlib (headless). Saves a PNG Ember can show or attach."""
from __future__ import annotations

from pathlib import Path


def make_chart(kind: str = "bar", values=None, labels=None, title: str = "",
               xlabel: str = "", ylabel: str = "", output: str = "") -> dict:
    """Render a chart to PNG.

    kind: bar | line | pie | scatter
    values: list of numbers (bar/line/pie) or list of [x, y] pairs (scatter)
    labels: category names (bar/line x-axis, pie slices)
    """
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except Exception:
        return {"ok": False, "error": "matplotlib not installed (uv pip install matplotlib)"}

    vals = values or []
    if not vals:
        return {"ok": False, "error": "values required"}
    kind = (kind or "bar").lower()

    base = Path.home() / "Desktop"
    if not base.exists():
        base = Path.home()
    out = Path(output).expanduser() if output else base / "ember_chart.png"

    try:
        out.parent.mkdir(parents=True, exist_ok=True)
        fig, ax = plt.subplots(figsize=(7.2, 4.6), dpi=130)
        accent = "#e2562a"
        if kind == "bar":
            ax.bar([str(x) for x in (labels or range(len(vals)))], vals, color=accent)
        elif kind == "line":
            ax.plot([str(x) for x in (labels or range(len(vals)))], vals, marker="o", color=accent)
        elif kind == "pie":
            ax.pie(vals, labels=[str(x) for x in (labels or range(len(vals)))], autopct="%1.0f%%")
        elif kind == "scatter":
            if vals and isinstance(vals[0], (list, tuple)):
                xs = [p[0] for p in vals]
                ys = [p[1] for p in vals]
            else:
                xs, ys = list(range(len(vals))), vals
            ax.scatter(xs, ys, color=accent)
        else:
            plt.close(fig)
            return {"ok": False, "error": f"unknown kind: {kind} (use bar/line/pie/scatter)"}
        if title:
            ax.set_title(title)
        if xlabel:
            ax.set_xlabel(xlabel)
        if ylabel:
            ax.set_ylabel(ylabel)
        fig.tight_layout()
        fig.savefig(str(out))
        plt.close(fig)
        return {"ok": True, "output": str(out), "kind": kind, "points": len(vals)}
    except Exception as e:
        try:
            plt.close("all")
        except Exception:
            pass
        return {"ok": False, "error": str(e)}
