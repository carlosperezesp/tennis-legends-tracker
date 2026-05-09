"""
Recent-result overlay for matches that are not yet in Jeff Sackmann CSVs.

The overlay stores Sackmann-shaped rows in data/live_results_overlay.json. Other
modules append these rows to the relevant year after loading the canonical CSV,
deduplicating by date/tournament/players/score. This lets the dashboard stay
fresh between upstream tennis_atp updates without permanently forking the data.
"""

from __future__ import annotations

import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "data"
OVERLAY_PATH = DATA_DIR / "live_results_overlay.json"


def _match_key(row: dict) -> tuple:
    winner = _norm(row.get("winner_name"))
    loser = _norm(row.get("loser_name"))
    players = tuple(sorted([winner, loser]))
    return (
        str(row.get("tourney_date") or ""),
        _norm(row.get("tourney_name")),
        players,
        _norm(row.get("score")),
    )


def _norm(value) -> str:
    return " ".join(str(value or "").lower().split())


def load_overlay_rows(year: int | None = None) -> list[dict]:
    if not OVERLAY_PATH.exists():
        return []
    try:
        raw = json.loads(OVERLAY_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    rows = raw.get("rows", raw if isinstance(raw, list) else [])
    if not isinstance(rows, list):
        return []
    if year is None:
        return [row for row in rows if isinstance(row, dict)]
    prefix = str(year)
    return [
        row for row in rows
        if isinstance(row, dict) and str(row.get("tourney_date") or "").startswith(prefix)
    ]


def merge_overlay_rows(base_rows: list[dict], year: int | None = None) -> list[dict]:
    overlay_rows = load_overlay_rows(year)
    if not overlay_rows:
        return base_rows
    seen = {_match_key(row) for row in base_rows}
    merged = list(base_rows)
    for row in overlay_rows:
        key = _match_key(row)
        if key in seen:
            continue
        seen.add(key)
        merged.append(row)
    merged.sort(key=lambda row: (
        str(row.get("tourney_date") or ""),
        str(row.get("tourney_id") or ""),
        int(row.get("match_num") or 9999),
    ))
    return merged


def write_overlay(rows: list[dict], as_of: str, source: str, errors: list[str] | None = None) -> None:
    OVERLAY_PATH.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "asOf": as_of,
        "source": source,
        "rows": rows,
        "errors": errors or [],
    }
    OVERLAY_PATH.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
