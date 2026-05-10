"""
Audit freshness and confidence of the embedded tennis dashboard data.

Run:
  python3 src/data_audit.py

This does not rebuild anything. It reads the cached data + generated HTML and
prints warnings when a source is stale or a player's level rests on a thin
sample.
"""

from __future__ import annotations

import csv
import json
import re
from datetime import date, datetime
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
CACHE = DATA / "_csv_cache"
HTML = ROOT / "examples" / "index.html"


def parse_yyyymmdd(raw: str) -> date | None:
    if not raw:
        return None
    try:
        return datetime.strptime(raw, "%Y%m%d").date()
    except ValueError:
        return None


def parse_iso(raw: str) -> date | None:
    if not raw:
        return None
    try:
        return date.fromisoformat(raw)
    except ValueError:
        return None


def age_days(day: date | None, today: date) -> int | None:
    return (today - day).days if day else None


def status_from_age(days: int | None, warn_after: int, fail_after: int) -> str:
    if days is None:
        return "FAIL"
    if days > fail_after:
        return "FAIL"
    if days > warn_after:
        return "WARN"
    return "OK"


def load_embedded_players() -> list[dict]:
    text = HTML.read_text(encoding="utf-8")
    match = re.search(r"const ALL_PLAYERS = (.*?);\nconst LIVE_SCHEDULE", text, re.S)
    if not match:
        raise RuntimeError("Could not find embedded ALL_PLAYERS in examples/index.html")
    return json.loads(match.group(1))


def latest_ranking_date() -> tuple[date | None, int]:
    path = CACHE / "atp_rankings_current.csv"
    if not path.exists():
        return None, 0
    rows = list(csv.DictReader(path.open(encoding="utf-8", newline="")))
    latest = max((r.get("ranking_date", "") for r in rows), default="")
    return parse_yyyymmdd(latest), sum(1 for r in rows if r.get("ranking_date") == latest)


def latest_match_tournament_start(year: int) -> tuple[date | None, int]:
    path = CACHE / f"atp_matches_{year}.csv"
    if not path.exists():
        return None, 0
    rows = list(csv.DictReader(path.open(encoding="utf-8", newline="")))
    latest = max((r.get("tourney_date", "") for r in rows), default="")
    return parse_yyyymmdd(latest), len(rows)


def profile_status() -> tuple[date | None, int, int]:
    path = DATA / "player_live_profiles.json"
    if not path.exists():
        return None, 0, 0
    raw = json.loads(path.read_text(encoding="utf-8"))
    players = raw.get("players", {})
    errors = raw.get("errors", [])
    return parse_iso(raw.get("asOf")), len(players), len(errors)


def schedule_status() -> tuple[date | None, int]:
    path = DATA / "live_schedule.json"
    if not path.exists():
        return None, 0
    raw = json.loads(path.read_text(encoding="utf-8"))
    matches = sum(len(day.get("matches", [])) for day in raw.get("days", []))
    return parse_iso(raw.get("asOf")), matches


def overlay_status() -> tuple[date | None, int]:
    path = DATA / "live_results_overlay.json"
    if not path.exists():
        return None, 0
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None, 0
    rows = raw.get("rows", raw if isinstance(raw, list) else [])
    return parse_iso(raw.get("asOf")), len(rows) if isinstance(rows, list) else 0


def player_warnings(players: list[dict]) -> tuple[list[dict], list[dict]]:
    thin_sample = []
    high_confidence_risk = []
    for player in players:
        sample = (player.get("surfaceSamples") or {}).get("All") or 0
        effective = (player.get("effectiveSamples") or {}).get("All") or 0
        level = (player.get("tourPctBySurface") or {}).get("All")
        profile = player.get("liveProfile") or {}
        official = ((profile.get("careerRecord") or {}).get("matches"))
        if sample < 15 or (official is not None and official < 15):
            thin_sample.append({
                "name": player.get("name"),
                "rank": player.get("rank"),
                "sample": sample,
                "official": official,
                "level": level,
            })
        if level is not None and level >= 80 and (sample < 20 or effective < 12):
            high_confidence_risk.append({
                "name": player.get("name"),
                "rank": player.get("rank"),
                "sample": sample,
                "effective": round(effective, 1),
                "level": round(level, 1),
            })
    return thin_sample, high_confidence_risk


def line(status: str, label: str, detail: str) -> None:
    print(f"[{status}] {label}: {detail}")


def main() -> int:
    today = date.today()
    players = load_embedded_players()

    rank_day, rank_rows = latest_ranking_date()
    rank_age = age_days(rank_day, today)
    line(
        status_from_age(rank_age, warn_after=7, fail_after=21),
        "ATP ranking",
        f"{rank_day or 'missing'} ({rank_age if rank_age is not None else '?'} days old, {rank_rows} rows)",
    )

    match_day, match_rows = latest_match_tournament_start(today.year)
    line(
        "OK" if match_rows else "FAIL",
        "Sackmann matches",
        f"latest tournament start {match_day or 'missing'} ({match_rows} rows; tourney_date is not match date)",
    )

    profile_day, profile_count, profile_errors = profile_status()
    profile_age = age_days(profile_day, today)
    profile_state = status_from_age(profile_age, warn_after=2, fail_after=7)
    if profile_errors:
        profile_state = "WARN" if profile_state == "OK" else profile_state
    line(
        profile_state,
        "Live profiles",
        f"{profile_day or 'missing'} ({profile_age if profile_age is not None else '?'} days old, {profile_count} players, {profile_errors} errors)",
    )

    schedule_day, schedule_matches = schedule_status()
    schedule_age = age_days(schedule_day, today)
    schedule_state = status_from_age(schedule_age, warn_after=1, fail_after=3)
    if schedule_matches == 0:
        schedule_state = "WARN" if schedule_state == "OK" else schedule_state
    line(
        schedule_state,
        "Live schedule",
        f"{schedule_day or 'missing'} ({schedule_age if schedule_age is not None else '?'} days old, {schedule_matches} matches)",
    )

    overlay_day, overlay_rows = overlay_status()
    overlay_age = age_days(overlay_day, today)
    overlay_state = "OK" if overlay_rows else "WARN"
    if overlay_rows:
        overlay_state = status_from_age(overlay_age, warn_after=3, fail_after=10)
    line(
        overlay_state,
        "Result overlay",
        f"{overlay_day or 'missing'} ({overlay_age if overlay_age is not None else '?'} days old, {overlay_rows} rows)",
    )

    thin_sample, high_risk = player_warnings(players)
    line("OK" if len(players) >= 180 else "WARN", "Embedded players", f"{len(players)} players in examples/index.html")
    line("WARN" if thin_sample else "OK", "Thin samples", f"{len(thin_sample)} players below confidence threshold")
    for row in thin_sample[:10]:
        print(f"  - {row['name']} ATP {row['rank']}: sample={row['sample']}, official={row['official']}, level={row['level']}")
    line("WARN" if high_risk else "OK", "High-level thin samples", f"{len(high_risk)} players")
    for row in high_risk[:10]:
        print(f"  - {row['name']} ATP {row['rank']}: level={row['level']}, sample={row['sample']}, effective={row['effective']}")

    failed = any(token == "FAIL" for token in [])
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
