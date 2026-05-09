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
import html
import json
import os
import re
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

ATP_OFFICIAL_EVENTS = [
    {
        "name": "Internazionali BNL d'Italia",
        "slug": "rome",
        "tournament_id": "416",
        "level": "ATP 1000",
        "surface": "Clay",
        # ATP daily-schedule day=5 maps to 2026-05-08.
        "schedule_day_1": date(2026, 5, 4),
        "start": date(2026, 5, 4),
        "end": date(2026, 5, 17),
    },
]


def _load_env_file() -> None:
    """Load local .env files without adding a runtime dependency."""
    root = Path(__file__).resolve().parents[1]
    for path in (root / ".env", root / ".env.local"):
        if not path.exists():
            continue
        for raw_line in path.read_text(encoding="utf-8").splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            key = key.strip()
            value = value.strip().strip('"').strip("'")
            if key and key not in os.environ:
                os.environ[key] = value


def _api_get(host: str, path: str, key: str) -> dict | list:
    url = f"https://{host}{path}"
    req = urllib.request.Request(url, headers={
        "Content-Type": "application/json",
        "x-rapidapi-host": host,
        "x-rapidapi-key": key,
    })
    with urllib.request.urlopen(req, timeout=12) as resp:
        return json.load(resp)


def _http_text(url: str) -> str:
    req = urllib.request.Request(url, headers={
        "User-Agent": "Mozilla/5.0 (compatible; tennis-legends/1.0)",
        "Accept": "text/html,application/xhtml+xml",
    })
    with urllib.request.urlopen(req, timeout=18) as resp:
        return resp.read().decode("utf-8", errors="replace")


def _html_lines(raw: str) -> list[str]:
    raw = re.sub(r"(?is)<script.*?</script>|<style.*?</style>", " ", raw)
    raw = re.sub(r"(?i)<br\s*/?>|</(?:div|li|p|h[1-6]|tr|td|span|a)>", "\n", raw)
    raw = re.sub(r"<[^>]+>", " ", raw)
    raw = html.unescape(raw)
    return [re.sub(r"\s+", " ", line).strip() for line in raw.splitlines() if line.strip()]


def _is_seed_or_entry(line: str) -> bool:
    return bool(re.fullmatch(r"\((?:\d+|WC|Q|LL|SE|PR|Alt|ALT)\)", line, flags=re.I))


def _is_schedule_noise(line: str) -> bool:
    low = line.lower()
    if not line or _is_seed_or_entry(line):
        return True
    if low in {"r64", "r32", "r16", "qf", "sf", "f", "rr", "vs", "h2h", "wta", "atp"}:
        return True
    if low in {"player photo", "image: player photo", "followed by"}:
        return True
    if "image:" in low or "starts at" in low or "not before" in low:
        return True
    if "–––" in line or "---" in line:
        return True
    return False


def _player_before(lines: list[str], idx: int) -> str | None:
    for cursor in range(idx - 1, max(-1, idx - 8), -1):
        line = lines[cursor]
        if _is_schedule_noise(line):
            continue
        return line
    return None


def _player_after(lines: list[str], idx: int) -> str | None:
    for cursor in range(idx + 1, min(len(lines), idx + 10)):
        line = lines[cursor]
        if _is_schedule_noise(line):
            continue
        return line
    return None


def _clean_player_name(name: str) -> str:
    name = re.sub(r"^\((?:\d+|WC|Q|LL|SE|PR|Alt|ALT)\)\s*", "", name).strip()
    return re.sub(r"\s+", " ", name)


def _atp_daily_schedule(event: dict, day: date) -> list[dict]:
    if day < event["start"] or day > event["end"]:
        return []
    day_number = (day - event["schedule_day_1"]).days + 1
    if day_number < 1:
        return []

    url = (
        "https://www.atptour.com/en/scores/current/"
        f"{event['slug']}/{event['tournament_id']}/daily-schedule?day={day_number}"
    )
    lines = _html_lines(_http_text(url))
    rows = []
    current_time = "TBD"
    seen = set()

    for idx, line in enumerate(lines):
        starts = re.search(r"Starts At\s+(\d{1,2}:\d{2})", line, flags=re.I)
        if starts:
            current_time = starts.group(1)
            continue
        not_before = re.search(r"Not Before\s+(\d{1,2}:\d{2})", line, flags=re.I)
        if not_before:
            current_time = not_before.group(1)
            continue
        if line.lower() == "followed by":
            current_time = "TBD"
            continue
        if line.lower() != "vs":
            continue

        nearby_after = " ".join(lines[idx: idx + 8]).lower()
        nearby_before = " ".join(lines[max(0, idx - 5): idx]).lower()
        if "h2h" not in nearby_after or "wta" in nearby_after:
            continue
        if not any(round_token in f"{nearby_before} {nearby_after}" for round_token in ("r64", "r32", "r16", "qf", "sf")):
            continue

        p1 = _player_before(lines, idx)
        p2 = _player_after(lines, idx)
        if not p1 or not p2:
            continue
        p1 = _clean_player_name(p1)
        p2 = _clean_player_name(p2)
        if not p1 or not p2 or p1 == p2:
            continue

        key_tuple = (p1, p2, current_time)
        if key_tuple in seen:
            continue
        seen.add(key_tuple)
        rows.append({
            "time": current_time,
            "player1": p1,
            "player2": p2,
            "tournament": event["name"],
            "level": event["level"],
            "round": "R64",
            "surface": event["surface"],
            "status": "scheduled",
            "provider": "ATP Tour",
            "sourceUrl": url,
        })
    return rows


def fetch_atp_official_day(day: date) -> tuple[list[dict], list[str]]:
    rows = []
    errors = []
    for event in ATP_OFFICIAL_EVENTS:
        try:
            rows.extend(_atp_daily_schedule(event, day))
        except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError, OSError) as exc:
            errors.append(f"ATP Tour {event['slug']} {day.isoformat()}: {exc}")
    return rows, errors


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
    source = "ATP Tour official schedule"
    for offset, label in ((0, "Hoy"), (1, "Mañana")):
        day = today + timedelta(days=offset)
        matches, errors = fetch_atp_official_day(day)
        all_errors.extend(errors)
        if not matches and api_key:
            matches, errors = fetch_day(day, api_key)
            all_errors.extend(errors)
            if matches:
                source = "RapidAPI provider cascade"
        days.append({
            "date": day.isoformat(),
            "label": label,
            "matches": matches,
        })

    return {
        "asOf": today.isoformat(),
        "timezone": "Europe/Rome",
        "source": source,
        "errors": all_errors[-8:],
        "days": days,
    }


def main() -> int:
    _load_env_file()
    parser = argparse.ArgumentParser(description="Fetch ATP men's singles matches for today and tomorrow")
    parser.add_argument("--date", default=date.today().isoformat(), help="YYYY-MM-DD, default today")
    parser.add_argument("--output", default=str(OUT_PATH))
    args = parser.parse_args()

    api_key = os.environ.get("RAPIDAPI_KEY")
    if not api_key:
        print("RAPIDAPI_KEY is not set; using ATP official fallback only.", file=sys.stderr)

    schedule = build_schedule(api_key, date.fromisoformat(args.date))
    total_matches = sum(len(day.get("matches", [])) for day in schedule.get("days", []))
    if total_matches == 0:
        print("No matches fetched; keeping existing schedule cache.", file=sys.stderr)
        return 2

    out = Path(args.output)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(schedule, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Wrote {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
