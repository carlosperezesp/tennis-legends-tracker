"""
Fetches live Grand Slam data from Tennis API (ATP WTA ITF) via RapidAPI.
Usage:
    from api_fetcher import fetch_player_gs_history, KNOWN_PLAYER_IDS
    history = fetch_player_gs_history('Carlos Alcaraz', api_key='YOUR_KEY')
    # Returns dict: {year: gs_count_that_year, ...}
"""

import json
import urllib.request
import urllib.error

RAPIDAPI_HOST = "tennis-api-atp-wta-itf.p.rapidapi.com"
BASE_URL = f"https://{RAPIDAPI_HOST}/tennis/v2/atp"

# Internal API player IDs (discovered via exploration)
KNOWN_PLAYER_IDS = {
    "Carlos Alcaraz": 68074,
}

# Grand Slam series IDs in this API
GRAND_SLAM_IDS = {
    "Australian Open": 21305,
    "Roland Garros": 21329,
    "Wimbledon": 21337,
    "US Open": 60,
}


def _api_get(path: str, api_key: str) -> dict:
    url = f"{BASE_URL}/{path}"
    req = urllib.request.Request(url, headers={
        "x-rapidapi-host": RAPIDAPI_HOST,
        "x-rapidapi-key": api_key,
        "Content-Type": "application/json",
    })
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return json.load(resp)
    except urllib.error.HTTPError as exc:
        if exc.code == 429:
            raise RuntimeError("Monthly API quota exceeded. Upgrade plan or wait for reset.")
        raise RuntimeError(f"API error {exc.code}: {exc.reason}")
    except Exception as exc:
        raise RuntimeError(f"Request failed: {exc}")


def fetch_player_gs_history(player_name: str, api_key: str) -> dict[int, int]:
    """
    Returns a dict mapping year -> GS wins in that year for the given player.
    Fetches all 4 Grand Slams and merges results.
    Only counts tournaments where bestRound == 'Winner'.
    """
    player_id = KNOWN_PLAYER_IDS.get(player_name)
    if not player_id:
        raise ValueError(
            f"No API ID found for '{player_name}'. "
            f"Known players: {list(KNOWN_PLAYER_IDS)}"
        )

    wins_by_year: dict[int, int] = {}
    for gs_name, tournament_id in GRAND_SLAM_IDS.items():
        path = f"player/tournament-record/{player_id}/{tournament_id}"
        data = _api_get(path, api_key)
        for entry in data.get("data", []):
            if entry.get("bestRound") == "Winner":
                year = entry["year"]
                wins_by_year[year] = wins_by_year.get(year, 0) + 1

    return wins_by_year


def build_cumulative_gs_by_year(wins_by_year: dict[int, int]) -> dict[int, int]:
    """Converts per-year wins to cumulative totals."""
    if not wins_by_year:
        return {}
    cumulative = {}
    total = 0
    for year in sorted(wins_by_year):
        total += wins_by_year[year]
        cumulative[year] = total
    return cumulative


def fetch_player_profile(player_name: str, api_key: str) -> dict:
    """Returns raw profile data for a player from the API."""
    player_id = KNOWN_PLAYER_IDS.get(player_name)
    if not player_id:
        raise ValueError(f"No API ID found for '{player_name}'.")
    return _api_get(f"player/profile/{player_id}", api_key)


if __name__ == "__main__":
    import os
    import sys

    key = os.environ.get("RAPIDAPI_KEY") or (sys.argv[1] if len(sys.argv) > 1 else None)
    if not key:
        print("Usage: python api_fetcher.py <RAPIDAPI_KEY>")
        sys.exit(1)

    player = "Carlos Alcaraz"
    print(f"Fetching GS history for {player}...")
    wins = fetch_player_gs_history(player, key)
    cumulative = build_cumulative_gs_by_year(wins)
    print(f"  Wins per year: {wins}")
    print(f"  Cumulative:    {cumulative}")
    print(f"  Total GS: {max(cumulative.values()) if cumulative else 0}")
