"""
Fetch recent ATP men's singles results into data/live_results_overlay.json.

The overlay is deliberately conservative: it only writes completed matches that
can be mapped to known ATP player IDs. Rows are shaped like Jeff Sackmann CSV
rows, but without point-level serve/return columns unless the provider supplies
them. That means the overlay improves Elo, win/loss form, sample sizes and
recency while not fabricating detailed point stats.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import re
import sys
import unicodedata
import urllib.error
import urllib.request
from datetime import date, timedelta
from pathlib import Path

import live_results_overlay as lro


ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
CACHE = DATA / "_csv_cache"

PROVIDERS = [
    {
        "name": "Tennis API - ATP WTA ITF",
        "host": "tennis-api-atp-wta-itf.p.rapidapi.com",
        "paths": [
            "/tennis/v2/atp/results/date/{date}",
            "/tennis/v2/atp/matches/date/{date}",
            "/tennis/v2/atp/events/date/{date}",
        ],
    },
    {
        "name": "FlashScore",
        "host": "flashscore4.p.rapidapi.com",
        "paths": [
            "/api/flashscore/v2/matches/list-by-date?sport_id=tennis&date={date}",
            "/api/flashscore/v2/events/list?sport_id=tennis&date={date}",
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
]


def _load_env_file() -> None:
    for path in (ROOT / ".env", ROOT / ".env.local"):
        if not path.exists():
            continue
        for raw in path.read_text(encoding="utf-8").splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            if key.strip() and key.strip() not in os.environ:
                os.environ[key.strip()] = value.strip().strip('"').strip("'")


def _api_get(host: str, path: str, key: str):
    req = urllib.request.Request(
        f"https://{host}{path}",
        headers={
            "Content-Type": "application/json",
            "x-rapidapi-host": host,
            "x-rapidapi-key": key,
        },
    )
    with urllib.request.urlopen(req, timeout=15) as resp:
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


def _norm(value: str) -> str:
    value = unicodedata.normalize("NFKD", str(value or ""))
    value = "".join(ch for ch in value if not unicodedata.combining(ch))
    value = re.sub(r"[^a-z0-9]+", " ", value.lower())
    return re.sub(r"\s+", " ", value).strip()


def _player_lookup() -> dict:
    path = CACHE / "atp_players.csv"
    lookup = {}
    if not path.exists():
        return lookup
    with path.open(encoding="utf-8", newline="") as fh:
        for row in csv.DictReader(fh):
            first = (row.get("name_first") or "").strip()
            last = (row.get("name_last") or "").strip()
            pid = row.get("player_id")
            if first and last and pid:
                name = f"{first} {last}"
                lookup[_norm(name)] = {"id": pid, "name": name}
    return lookup


def _rank_lookup() -> dict:
    path = CACHE / "atp_rankings_current.csv"
    if not path.exists():
        return {}
    rows = list(csv.DictReader(path.open(encoding="utf-8", newline="")))
    latest = max((row.get("ranking_date", "") for row in rows), default="")
    return {
        str(row.get("player") or ""): row.get("rank")
        for row in rows
        if row.get("ranking_date") == latest and row.get("player")
    }


def _looks_like_mens_singles(row: dict) -> bool:
    text = json.dumps(row, ensure_ascii=False).lower()
    if any(token in text for token in ("wta", "women", "doubles", "double", "mixed")):
        return False
    return any(token in text for token in ("atp", "men", "singles", "single", "tennis"))


def _is_completed(row: dict) -> bool:
    text = json.dumps(row, ensure_ascii=False).lower()
    return any(token in text for token in ("finished", "ended", "complete", "completed", "final"))


def _winner_loser(row: dict) -> tuple[str | None, str | None]:
    winner = _name(_first(row, "winner", "winnerTeam", "winnerPlayer"))
    loser = _name(_first(row, "loser", "loserTeam", "loserPlayer"))
    if winner and loser:
        return str(winner), str(loser)

    home = _name(_first(row, "homeTeam", "home", "player1", "competitor1", "participant1"))
    away = _name(_first(row, "awayTeam", "away", "player2", "competitor2", "participant2"))
    if not home or not away:
        name = _first(row, "name", "eventName", "matchName", "title")
        if isinstance(name, str) and " - " in name:
            home, away = [part.strip() for part in name.split(" - ", 1)]
    if not home or not away:
        return None, None

    winner_code = str(_first(row, "winnerCode", "winnerSide", "winner")) .lower()
    if winner_code in {"1", "home", "home_team", "player1"}:
        return str(home), str(away)
    if winner_code in {"2", "away", "away_team", "player2"}:
        return str(away), str(home)

    hs = _first(row, "homeScore", "home_score", "scoreHome")
    as_ = _first(row, "awayScore", "away_score", "scoreAway")
    try:
        if isinstance(hs, dict):
            hs = _first(hs, "current", "display", "total")
        if isinstance(as_, dict):
            as_ = _first(as_, "current", "display", "total")
        if float(hs) > float(as_):
            return str(home), str(away)
        if float(as_) > float(hs):
            return str(away), str(home)
    except (TypeError, ValueError):
        pass
    return None, None


def _normalise_row(row: dict, day: date, provider: str, players: dict, ranks: dict) -> dict | None:
    if not _looks_like_mens_singles(row) or not _is_completed(row):
        return None
    winner_name, loser_name = _winner_loser(row)
    if not winner_name or not loser_name:
        return None
    winner = players.get(_norm(winner_name))
    loser = players.get(_norm(loser_name))
    if not winner or not loser:
        return None

    tournament = _name(_first(row, "tournament", "competition", "league", "season")) or "Tournament TBD"
    level = _first(row, "level", "category", "series")
    level_text = json.dumps(row, ensure_ascii=False).lower()
    if not level:
        if "masters" in level_text or "1000" in level_text:
            level = "M"
        elif "grand slam" in level_text:
            level = "G"
        elif "atp 500" in level_text or "atp 250" in level_text:
            level = "A"
        else:
            level = "A"
    score = _first(row, "score", "displayScore", "result") or ""
    surface = _first(row, "surface", "court") or ""
    return {
        "tourney_id": f"live-{day.isoformat()}",
        "tourney_name": str(tournament),
        "surface": str(surface).title() if surface else "",
        "tourney_level": str(level)[0].upper(),
        "tourney_date": day.strftime("%Y%m%d"),
        "match_num": "999",
        "winner_id": str(winner["id"]),
        "winner_name": winner["name"],
        "winner_rank": ranks.get(str(winner["id"]), ""),
        "loser_id": str(loser["id"]),
        "loser_name": loser["name"],
        "loser_rank": ranks.get(str(loser["id"]), ""),
        "score": str(score),
        "round": str(_first(row, "round", "roundName", "stage") or ""),
        "source": provider,
    }


def fetch_results(api_key: str, start: date, end: date) -> tuple[list[dict], list[str]]:
    players = _player_lookup()
    ranks = _rank_lookup()
    if not players:
        return [], ["No ATP player registry available."]
    rows = []
    errors = []
    seen = set()
    day = start
    while day <= end:
        for provider in PROVIDERS:
            for template in provider["paths"]:
                path = template.format(date=day.isoformat())
                try:
                    payload = _api_get(provider["host"], path, api_key)
                except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
                    errors.append(f"{provider['name']} {path}: {exc}")
                    continue
                for item in _walk(payload):
                    match = _normalise_row(item, day, provider["name"], players, ranks)
                    if not match:
                        continue
                    key = (
                        match["tourney_date"],
                        match["winner_id"],
                        match["loser_id"],
                        match.get("score", ""),
                    )
                    if key in seen:
                        continue
                    seen.add(key)
                    rows.append(match)
            if rows:
                break
        day += timedelta(days=1)
    return rows, errors[-12:]


def main() -> int:
    _load_env_file()
    parser = argparse.ArgumentParser(description="Fetch recent ATP result overlay")
    parser.add_argument("--days-back", type=int, default=14)
    parser.add_argument("--date", default=date.today().isoformat())
    args = parser.parse_args()

    api_key = os.environ.get("RAPIDAPI_KEY")
    if not api_key:
        print("RAPIDAPI_KEY is not set; keeping existing result overlay.", file=sys.stderr)
        return 0

    today = date.fromisoformat(args.date)
    start = today - timedelta(days=max(0, args.days_back))
    rows, errors = fetch_results(api_key, start, today)
    if not rows:
        print("No live result rows fetched; keeping existing result overlay.", file=sys.stderr)
        for error in errors[-6:]:
            print(f"  - {error}", file=sys.stderr)
        return 0
    lro.write_overlay(rows, today.isoformat(), "RapidAPI result cascade", errors)
    print(f"Wrote {lro.OVERLAY_PATH} ({len(rows)} rows)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
