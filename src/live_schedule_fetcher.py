"""
Daily live schedule cache for ATP men's singles.

This module intentionally does not store API keys. Set RAPIDAPI_KEY in the
environment, then run:

  RAPIDAPI_KEY=... python3 src/live_schedule_fetcher.py

The app reads data/live_schedule.json, so this fetcher can be run once a day
from cron/GitHub Actions/Vercel Cron without changing the frontend.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, timedelta
from pathlib import Path

DATA_DIR = Path(__file__).resolve().parents[1] / "data"
OUT_PATH = DATA_DIR / "live_schedule.json"

IMPORTANT_LEVELS = {
    "grand slam", "gs", "olympics", "davis cup", "atp finals",
    "masters 1000", "atp 1000", "atp 500", "atp 250",
}

RAPIDAPI_PROVIDERS = [
    {
        "name": "Tennis API - ATP WTA ITF",
        "host": "tennis-api-atp-wta-itf.p.rapidapi.com",
        "paths": [
            "/tennis/v2/atp/events/date/{date}",
            "/tennis/v2/atp/matches/date/{date}",
            "/tennis/v2/atp/calendar/{date}",
        ],
    },
    {
        "name": "FlashScore",
        "host": "flashscore4.p.rapidapi.com",
        "paths": [
            "/api/flashscore/v2/matches/list-by-date?sport_id=tennis&date={date}",
            "/api/flashscore/v2/events/list? sport_id=tennis&date={date}",
        ],
    },
    {
        "name": "SofaScore",
        "host": "sofascore6.p.rapidapi.com",
        "paths": [
            "/api/sofascore/v1/sport/tennis/scheduled-events/{date}",
            "/api/sofascore/v1/events/schedule/tennis/{date}",
        ],
    },
    {
        "name": "Tennisprodata",
        "host": "tennisprodata1.p.rapidapi.com",
        "paths": [
            "/live-results/{date}/",
            "/fixtures/{date}/",
        ],
    },
]


def _api_get(host: str, path: str, key: str) -> dict | list:
    url = f"https://{host}{path}"
    req = urllib.request.Request(url, headers={
        "Content-Type": "application/json",
        "x-rapidapi-host": host,
        "x-rapidapi-key": key,
    })
    with urllib.request.urlopen(req, timeout=12) as resp:
        return json.load(resp)


def _walk(obj):
    if isinstance(obj, dict):
        yield obj
        for value in obj.values():
            yield from _walk(value)
    elif isinstance(obj, list):
        for item in obj:
            yield from _walk(item)


def _first(row: dict, *keys: str):
    for key in keys:
        if key in row and row[key] not in (None, "", []):
            return row[key]
    return None


def _name(value):
    if isinstance(value, dict):
        return _first(value, "name", "shortName", "displayName", "fullName")
    return value


def _looks_like_mens_singles(row: dict) -> bool:
    text = json.dumps(row, ensure_ascii=False).lower()
    if any(token in text for token in ("wta", "women", "doubles", "double", "mixed")):
        return False
    return any(token in text for token in ("atp", "men", "singles", "single", "tennis"))


def _important(row: dict) -> bool:
    text = json.dumps(row, ensure_ascii=False).lower()
    return any(level in text for level in IMPORTANT_LEVELS)


def _normalise_row(row: dict, day: date, provider: str) -> dict | None:
    if not _looks_like_mens_singles(row) or not _important(row):
        return None

    p1 = _name(_first(row, "homeTeam", "home", "player1", "competitor1", "participant1"))
    p2 = _name(_first(row, "awayTeam", "away", "player2", "competitor2", "participant2"))
    if not p1 or not p2:
        name = _first(row, "name", "eventName", "matchName", "title")
        if isinstance(name, str) and " - " in name:
            p1, p2 = [part.strip() for part in name.split(" - ", 1)]
    if not p1 or not p2:
        return None

    tournament = _name(_first(row, "tournament", "competition", "league", "season")) or "Tournament TBD"
    level = _first(row, "level", "category", "series") or "ATP"
    round_name = _first(row, "round", "roundName", "stage", "phase") or "TBD"
    start = _first(row, "startTime", "startTimestamp", "time", "dateTime")
    time = "TBD"
    if isinstance(start, int):
        from datetime import datetime
        time = datetime.fromtimestamp(start).strftime("%H:%M")
    elif isinstance(start, str) and len(start) >= 5:
        time = start[-5:] if start[-3:-2] == ":" else start

    return {
        "tournament": str(tournament),
        "level": str(level),
        "round": str(round_name),
        "surface": _first(row, "surface", "court") or "TBD",
        "time": time,
        "player1": str(p1),
        "player2": str(p2),
        "status": _first(row, "status", "state") or "scheduled",
        "provider": provider,
    }


def fetch_day(day: date, key: str) -> tuple[list[dict], list[str]]:
    errors = []
    for provider in RAPIDAPI_PROVIDERS:
        for path_template in provider["paths"]:
            path = path_template.format(date=day.isoformat())
            path = path.replace(" ", "")
            try:
                payload = _api_get(provider["host"], path, key)
            except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
                errors.append(f"{provider['name']} {path}: {exc}")
                continue
            rows = []
            seen = set()
            for row in _walk(payload):
                match = _normalise_row(row, day, provider["name"])
                if not match:
                    continue
                key_tuple = (match["tournament"], match["round"], match["player1"], match["player2"], match["time"])
                if key_tuple in seen:
                    continue
                seen.add(key_tuple)
                rows.append(match)
            if rows:
                return rows, errors
    return [], errors


def build_schedule(api_key: str | None, today: date) -> dict:
    days = []
    all_errors = []
    for offset, label in ((0, "Hoy"), (1, "Mañana")):
        day = today + timedelta(days=offset)
        matches = []
        if api_key:
            matches, errors = fetch_day(day, api_key)
            all_errors.extend(errors)
        days.append({
            "date": day.isoformat(),
            "label": label,
            "matches": matches,
        })

    return {
        "asOf": today.isoformat(),
        "timezone": "Europe/Amsterdam",
        "source": "RapidAPI provider cascade",
        "errors": all_errors[-8:],
        "days": days,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Fetch ATP men's singles matches for today and tomorrow")
    parser.add_argument("--date", default=date.today().isoformat(), help="YYYY-MM-DD, default today")
    parser.add_argument("--output", default=str(OUT_PATH))
    args = parser.parse_args()

    api_key = os.environ.get("RAPIDAPI_KEY")
    if not api_key:
        print("RAPIDAPI_KEY is not set; no network providers will be called.", file=sys.stderr)

    schedule = build_schedule(api_key, date.fromisoformat(args.date))
    out = Path(args.output)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(schedule, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Wrote {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
