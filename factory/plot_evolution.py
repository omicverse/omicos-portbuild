"""Render an SVG evolution plot from `_trajectory/timeline.jsonl`.

The plot mirrors omicverse-rebuildr's two-panel pattern but adapted for skill
optimization:

  Panel 1 (top):     selection-split score vs iteration (with score_best line)
  Panel 2 (bottom):  edit economy — n_applied per step (bars) + cumulative
                     accepted edits (line)

Stdlib-only — emits hand-crafted SVG so no matplotlib dependency is required.
Run:
    python -m factory.plot_evolution targets/<id>
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any

from factory.timeline import Event, read_events


def render_svg(target_dir: Path) -> str:
    events = read_events(target_dir)
    rows = _materialize_rows(events)
    return _svg_from_rows(rows, capability_id=target_dir.name)


def write_svg(target_dir: Path) -> Path:
    svg = render_svg(target_dir)
    out = target_dir / "EVOLUTION_PLOT.svg"
    out.write_text(svg, encoding="utf-8")
    return out


# --------------------------------------------------------------------------- #
# Row materialization                                                         #
# --------------------------------------------------------------------------- #


def _materialize_rows(events: list[Event]) -> list[dict[str, Any]]:
    """Walk timeline.jsonl into a per-iteration table.

    One row per `candidate_evaluated` event. Indexed by global iteration
    counter (0, 1, 2, ...) — easier to plot than (epoch, step).
    """
    rows: list[dict[str, Any]] = []
    applied_lookup: dict[tuple[int, int], int] = {}
    for ev in events:
        if ev.kind == "candidate_applied":
            applied_lookup[(ev.epoch, ev.step)] = int(ev.payload.get("n_applied", 0))
    score_best_so_far = None
    for ev in events:
        if ev.kind == "candidate_evaluated":
            sc = ev.payload.get("score_cand")
            decision = ev.payload.get("decision", "reject")
            if decision != "reject" and sc is not None:
                if score_best_so_far is None or sc > score_best_so_far:
                    score_best_so_far = sc
            rows.append({
                "iter": len(rows),
                "epoch": ev.epoch,
                "step": ev.step,
                "score": float(sc) if sc is not None else None,
                "score_best": score_best_so_far,
                "decision": decision,
                "n_applied": applied_lookup.get((ev.epoch, ev.step), 0),
            })
    return rows


# --------------------------------------------------------------------------- #
# SVG emission                                                                #
# --------------------------------------------------------------------------- #


_WIDTH = 800
_HEIGHT = 480
_PAD_L, _PAD_R = 60, 20
_PAD_T, _PAD_GAP, _PAD_B = 40, 30, 50
_PANEL_H = (_HEIGHT - _PAD_T - _PAD_GAP - _PAD_B) // 2


def _svg_from_rows(rows: list[dict[str, Any]], *, capability_id: str) -> str:
    plot_w = _WIDTH - _PAD_L - _PAD_R
    top_y0 = _PAD_T
    bot_y0 = _PAD_T + _PANEL_H + _PAD_GAP

    if not rows:
        return _empty_svg(capability_id)

    n = len(rows)
    xs = [_PAD_L + (i + 0.5) * (plot_w / max(n, 1)) for i in range(n)]

    scores = [r["score"] for r in rows if r["score"] is not None]
    score_min = min(scores) if scores else 0.0
    score_max = max(scores) if scores else 1.0
    if score_max - score_min < 1e-6:
        score_max = score_min + 1.0

    n_apps = [r["n_applied"] for r in rows]
    n_apps_max = max(n_apps + [1])

    parts: list[str] = []
    parts.append(
        f'<svg xmlns="http://www.w3.org/2000/svg" '
        f'viewBox="0 0 {_WIDTH} {_HEIGHT}" '
        f'font-family="ui-monospace, SFMono-Regular, monospace" font-size="11">'
    )
    parts.append('<rect width="100%" height="100%" fill="white"/>')
    parts.append(
        f'<text x="{_PAD_L}" y="20" font-size="13" font-weight="bold">'
        f'Skill evolution — {capability_id}'
        f'</text>'
    )

    # Top panel: score vs iteration
    parts.extend(_axes(
        x0=_PAD_L, y0=top_y0, w=plot_w, h=_PANEL_H,
        title="selection score vs iteration",
        ylabel=f"score ({score_min:.2f}–{score_max:.2f})",
    ))
    # Score line (accepts in green, rejects as hollow squares).
    score_path: list[str] = []
    accept_pts: list[tuple[float, float]] = []
    reject_pts: list[tuple[float, float]] = []
    best_path: list[str] = []
    for i, r in enumerate(rows):
        if r["score"] is None:
            continue
        x = xs[i]
        y = _y_for(r["score"], score_min, score_max, top_y0, _PANEL_H)
        score_path.append(f"{'M' if not score_path else 'L'} {x:.1f} {y:.1f}")
        if r["decision"] == "reject":
            reject_pts.append((x, y))
        else:
            accept_pts.append((x, y))
        if r["score_best"] is not None:
            yb = _y_for(r["score_best"], score_min, score_max, top_y0, _PANEL_H)
            best_path.append(f"{'M' if not best_path else 'L'} {x:.1f} {yb:.1f}")
    parts.append(
        f'<path d="{" ".join(score_path)}" '
        f'fill="none" stroke="#2563eb" stroke-width="1.5"/>'
    )
    parts.append(
        f'<path d="{" ".join(best_path)}" '
        f'fill="none" stroke="#15803d" stroke-width="2" stroke-dasharray="4 3"/>'
    )
    for x, y in accept_pts:
        parts.append(
            f'<circle cx="{x:.1f}" cy="{y:.1f}" r="3.5" fill="#15803d"/>'
        )
    for x, y in reject_pts:
        parts.append(
            f'<rect x="{x-3:.1f}" y="{y-3:.1f}" width="6" height="6" '
            f'fill="none" stroke="#b91c1c" stroke-width="1.2"/>'
        )

    # Legend for top panel.
    legend_y = top_y0 - 8
    parts.append(
        f'<g font-size="10">'
        f'<circle cx="{_PAD_L + 110}" cy="{legend_y}" r="3.5" fill="#15803d"/>'
        f'<text x="{_PAD_L + 120}" y="{legend_y + 3}">accept</text>'
        f'<rect x="{_PAD_L + 170}" y="{legend_y - 3}" width="6" height="6" '
        f'fill="none" stroke="#b91c1c" stroke-width="1.2"/>'
        f'<text x="{_PAD_L + 180}" y="{legend_y + 3}">reject</text>'
        f'<line x1="{_PAD_L + 230}" y1="{legend_y}" x2="{_PAD_L + 250}" y2="{legend_y}" '
        f'stroke="#15803d" stroke-width="2" stroke-dasharray="4 3"/>'
        f'<text x="{_PAD_L + 255}" y="{legend_y + 3}">score_best</text>'
        f'</g>'
    )

    # Bottom panel: n_applied (bars) + cumulative accepted edits (line)
    parts.extend(_axes(
        x0=_PAD_L, y0=bot_y0, w=plot_w, h=_PANEL_H,
        title=f"edit economy — n_applied per step (max={n_apps_max})",
        ylabel="edits / cumulative",
    ))
    bar_w = max(2.0, (plot_w / max(n, 1)) * 0.55)
    cum_path: list[str] = []
    cum = 0
    for i, r in enumerate(rows):
        x = xs[i]
        h_bar = (r["n_applied"] / n_apps_max) * _PANEL_H
        y_top = bot_y0 + _PANEL_H - h_bar
        # bar
        fill = "#2563eb" if r["decision"] != "reject" else "#fca5a5"
        parts.append(
            f'<rect x="{x - bar_w/2:.1f}" y="{y_top:.1f}" '
            f'width="{bar_w:.1f}" height="{h_bar:.1f}" fill="{fill}"/>'
        )
        if r["decision"] != "reject":
            cum += r["n_applied"]
        cum_y = bot_y0 + _PANEL_H - (cum / max(cum, 1, n_apps_max)) * _PANEL_H
        cum_path.append(f"{'M' if not cum_path else 'L'} {x:.1f} {cum_y:.1f}")
    parts.append(
        f'<path d="{" ".join(cum_path)}" '
        f'fill="none" stroke="#7c3aed" stroke-width="1.5"/>'
    )
    # x-axis tick labels (iteration index, sparsely)
    tick_every = max(1, n // 10)
    for i in range(0, n, tick_every):
        x = xs[i]
        parts.append(
            f'<text x="{x:.1f}" y="{bot_y0 + _PANEL_H + 14}" '
            f'text-anchor="middle" font-size="10" fill="#475569">{i}</text>'
        )
    parts.append(
        f'<text x="{_WIDTH/2:.0f}" y="{_HEIGHT - 15}" '
        f'text-anchor="middle" font-size="11">iteration</text>'
    )

    parts.append("</svg>")
    return "\n".join(parts)


def _y_for(score: float, smin: float, smax: float, y0: int, h: int) -> float:
    return y0 + h - ((score - smin) / (smax - smin)) * h


def _axes(*, x0: int, y0: int, w: int, h: int, title: str, ylabel: str) -> list[str]:
    return [
        f'<text x="{x0}" y="{y0 - 6}" font-size="11" fill="#0f172a">{title}</text>',
        f'<text x="{x0 - 50}" y="{y0 + h/2:.0f}" transform="rotate(-90 {x0 - 50} {y0 + h/2:.0f})" '
        f'text-anchor="middle" font-size="10" fill="#475569">{ylabel}</text>',
        f'<line x1="{x0}" y1="{y0}" x2="{x0}" y2="{y0 + h}" stroke="#94a3b8" stroke-width="0.7"/>',
        f'<line x1="{x0}" y1="{y0 + h}" x2="{x0 + w}" y2="{y0 + h}" stroke="#94a3b8" stroke-width="0.7"/>',
    ]


def _empty_svg(capability_id: str) -> str:
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {_WIDTH} 120" '
        f'font-family="ui-monospace, monospace" font-size="12">'
        f'<rect width="100%" height="100%" fill="white"/>'
        f'<text x="20" y="40" font-weight="bold">Skill evolution — {capability_id}</text>'
        f'<text x="20" y="80" fill="#64748b">No iterations recorded yet. '
        f'Drive the optimizer loop to populate _trajectory/timeline.jsonl.</text>'
        f'</svg>'
    )


# --------------------------------------------------------------------------- #
# CLI                                                                         #
# --------------------------------------------------------------------------- #


def _main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="factory.plot_evolution")
    p.add_argument("target_dir")
    args = p.parse_args(argv)
    out = write_svg(Path(args.target_dir).resolve())
    print(str(out))
    return 0


if __name__ == "__main__":
    sys.exit(_main())
