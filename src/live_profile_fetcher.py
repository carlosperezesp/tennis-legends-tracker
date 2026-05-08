"""
Daily player live-profile cache for ATP men's singles.

This fetcher builds data/player_live_profiles.json. It always produces a
derived profile from local Jeff Sackmann CSVs, then optionally enriches it with
RapidAPI providers when RAPIDAPI_KEY is present.

The dashboard reads this file as an override/enrichment layer on top of the
deeper Sackmann and Match Charting stats.
"""

from __future__ import annotations

import argparse
import csv
import io
import json
import os
import re
import sys
import urllib.error
import urllib.request
from datetime import date
from pathlib import Path

SRC_DIR = Path(__file__).resolve().parent
ROOT = SRC_DIR.parent
DATA_DIR = ROOT / "data"
OUT_PATH = DATA_DIR / "player_live_profiles.json"

sys.path.insert(0, str(SRC_DIR))
import stats_fetcher as sf

RAPIDAPI_PROVIDERS = [
    {
        "name": "TennisApi",
        "host": "tennisapi1.p.rapidapi.com",
        "ranking_paths": [
            "/api/tennis/rankings/atp",
            "/api/tennis/rankings/atp/singles",
        ],
    },
    {
        "name": "Tennis API - ATP WTA ITF",
        "host": "tennis-api-atp-wta-itf.p.rapidapi.com",
        "ranking_paths": [
            "/tennis/v2/atp/ranking",
            "/tennis/v2/atp/rankings",
        ],
    },
]


def _load_env_file() -> None:
    """Load local .env files without adding a runtime dependency."""
    for path in (ROOT / ".env", ROOT / ".env.local"):
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


def _safe_int(value, default=0):
    try:
        return int(value) if value not in (None, "", "nan") else default
    except (TypeError, ValueError):
        return default


def _normalise_name(name: str) -> str:
    name = re.sub(r"[^a-zA-ZÀ-ÿ' -]", " ", name or "")
    return re.sub(r"\s+", " ", name).strip().lower()


def _api_get(host: str, path: str, key: str) -> dict | list:
    url = f"https://{host}{path}"
    req = urllib.request.Request(url, headers={
        "Content-Type": "application/json",
        "x-rapidapi-host": host,
        "x-rapidapi-key": key,
    })
    with urllib.request.urlopen(req, timeout=14) as resp:
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
        if key in row and row[key] not in (None, "", [], {}):
            return row[key]
    return None


def _extract_player_name(value) -> str | None:
    if isinstance(value, dict):
        return _first(value, "name", "fullName", "displayName", "shortName")
    if isinstance(value, str):
        return value
    return None


def _empty_record() -> dict:
    return {
        "matches": 0,
        "wins": 0,
        "losses": 0,
        "bySurface": {
            "Hard": {"matches": 0, "wins": 0, "losses": 0},
            "Clay": {"matches": 0, "wins": 0, "losses": 0},
            "Grass": {"matches": 0, "wins": 0, "losses": 0},
        },
    }


def _add_match(record: dict, surface: str, won: bool) -> None:
    record["matches"] += 1
    record["wins" if won else "losses"] += 1
    if surface in record["bySurface"]:
        surf_record = record["bySurface"][surface]
        surf_record["matches"] += 1
        surf_record["wins" if won else "losses"] += 1


def _is_walkover(row: dict) -> bool:
    return (row.get("score") or "").strip().upper() in {"W/O", "WALKOVER"}


def _has_point_stats(row: dict) -> bool:
    return any(_safe_int(row.get(k), 0) > 0 for k in ("w_svpt", "l_svpt", "w_1stIn", "l_1stIn"))


def _derived_profiles(players: list, years_range, use_cache: bool = True) -> dict:
    pid_set = {p["player_id"] for p in players}
    current_year = date.today().year
    profiles = {}

    for p in players:
        profiles[p["name"]] = {
            "asOf": date.today().isoformat(),
            "sources": ["Jeff Sackmann records", "Jeff Sackmann point stats"],
            "careerRecord": _empty_record(),
            "seasonRecord": _empty_record(),
            "statSamples": {"matches": 0, "bySurface": {"Hard": 0, "Clay": 0, "Grass": 0}},
            "latestMatchDate": None,
        }

    pid_to_name = {p["player_id"]: p["name"] for p in players}
    for year in years_range:
        for row in sf._fetch_csv(year, use_cache):
            if _is_walkover(row):
                continue
            surface = (row.get("surface") or "").strip()
            match_date = (row.get("tourney_date") or "").strip()
            wid = _safe_int(row.get("winner_id"), None)
            lid = _safe_int(row.get("loser_id"), None)
            for pid, won in ((wid, True), (lid, False)):
                if pid not in pid_set:
                    continue
                profile = profiles[pid_to_name[pid]]
                _add_match(profile["careerRecord"], surface, won)
                if int(year) == current_year:
                    _add_match(profile["seasonRecord"], surface, won)
                if _has_point_stats(row):
                    profile["statSamples"]["matches"] += 1
                    if surface in profile["statSamples"]["bySurface"]:
                        profile["statSamples"]["bySurface"][surface] += 1
                if match_date and (not profile["latestMatchDate"] or match_date > profile["latestMatchDate"]):
                    profile["latestMatchDate"] = match_date
    return profiles


def _parse_ranking_rows(payload) -> dict:
    """Return name -> API facts from a provider ranking response."""
    result = {}
    for row in _walk(payload):
        player = _first(row, "player", "participant", "competitor", "team")
        name = (
            _extract_player_name(player)
            or _first(row, "playerName", "player_name", "name", "fullName")
        )
        rank = _safe_int(_first(row, "rank", "position", "ranking"), None)
        if not name or not rank:
            continue
        player_id = None
        if isinstance(player, dict):
            player_id = _first(player, "id", "player_id", "slug", "url")
        player_id = player_id or _first(row, "player_id", "playerId", "id")
        points = _safe_int(_first(row, "points", "rankingPoints", "rank_points"), None)
        result[_normalise_name(name)] = {
            "rank": rank,
            "rankPoints": points,
            "apiPlayerId": player_id,
        }
    return result


def _fetch_rankings(api_key: str) -> tuple[dict, list[str]]:
    facts = {}
    errors = []
    for provider in RAPIDAPI_PROVIDERS:
        host = provider["host"]
        for path in provider["ranking_paths"]:
            try:
                payload = _api_get(host, path, api_key)
                parsed = _parse_ranking_rows(payload)
                if parsed:
                    for name_key, item in parsed.items():
                        item["provider"] = provider["name"]
                        facts.setdefault(name_key, item)
                    return facts, errors
            except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError, OSError, json.JSONDecodeError) as exc:
                errors.append(f"{provider['name']} {path}: {exc}")
    return facts, errors


def _merge_api_facts(profiles: dict, players: list, api_key: str | None) -> list[str]:
    if not api_key:
        return ["RAPIDAPI_KEY not set; using derived profile cache only."]

    ranking_facts, errors = _fetch_rankings(api_key)
    if not ranking_facts:
        return errors or ["No ranking facts returned by RapidAPI providers."]

    for p in players:
        profile = profiles.get(p["name"])
        facts = ranking_facts.get(_normalise_name(p["name"]))
        if not profile or not facts:
            continue
        profile["liveRank"] = facts.get("rank")
        profile["liveRankPoints"] = facts.get("rankPoints")
        profile.setdefault("apiIds", {})[facts.get("provider", "RapidAPI")] = facts.get("apiPlayerId")
        source = facts.get("provider", "RapidAPI")
        if source not in profile["sources"]:
            profile["sources"].append(source)
    return errors


def write_profiles(top_n: int = 200, years_back: int = 20, use_cache: bool = True) -> int:
    _load_env_file()
    players = sf.get_top_n_players(top_n, use_cache=use_cache)
    current_year = date.today().year
    years = range(max(1991, current_year - years_back), current_year + 1)
    profiles = _derived_profiles(players, years, use_cache=use_cache)
    errors = _merge_api_facts(profiles, players, os.environ.get("RAPIDAPI_KEY"))

    payload = {
        "asOf": date.today().isoformat(),
        "players": profiles,
        "errors": errors[-20:],
    }
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    OUT_PATH.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    enriched = sum(1 for p in profiles.values() if any(s in ("TennisApi", "Tennis API - ATP WTA ITF") for s in p.get("sources", [])))
    print(f"Wrote {OUT_PATH} ({len(profiles)} players, {enriched} API-enriched)")
    if errors:
        print("Warnings:")
        for error in errors[-6:]:
            print(f"  - {error}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--top", type=int, default=200)
    parser.add_argument("--years-back", type=int, default=20)
    parser.add_argument("--no-cache", action="store_true")
    args = parser.parse_args()
    return write_profiles(args.top, args.years_back, use_cache=not args.no_cache)


if __name__ == "__main__":
    raise SystemExit(main())
