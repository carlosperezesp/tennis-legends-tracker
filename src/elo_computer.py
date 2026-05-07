"""
Computes Elo ratings for all ATP players from the cached match CSVs
(Jeff Sackmann's tennis_atp dataset).

Processes all available years chronologically so the final rating reflects
a player's current strength. Only reports players with >= MIN_MATCHES.
"""

import csv
from pathlib import Path

CACHE_DIR = Path(__file__).resolve().parents[1] / "data" / "_csv_cache"

# K-factor by tournament level (higher = more volatile)
K_FACTORS = {
    'G': 45,   # Grand Slam
    'M': 35,   # Masters 1000
    'F': 30,   # ATP Finals / Nitto
    'A': 20,   # ATP 500 / 250
    'D': 12,   # Davis Cup
    'C': 10,   # Challenger
}
DEFAULT_K    = 15
INITIAL_ELO  = 1500.0
MIN_MATCHES  = 30   # below this the rating is too noisy to surface


def compute_elo_ratings() -> dict[int, dict]:
    """
    Return {player_id: {'elo': float, 'matches': int}} for all players
    with at least MIN_MATCHES recorded.
    """
    csv_files = sorted(CACHE_DIR.glob("atp_matches_*.csv"))

    elo: dict[int, float]  = {}
    n_matches: dict[int, int] = {}

    for path in csv_files:
        try:
            with open(path, encoding="utf-8", newline="") as fh:
                for row in csv.DictReader(fh):
                    wid = _int(row.get("winner_id"))
                    lid = _int(row.get("loser_id"))
                    if wid is None or lid is None:
                        continue

                    ra = elo.get(wid, INITIAL_ELO)
                    rb = elo.get(lid, INITIAL_ELO)
                    k  = K_FACTORS.get((row.get("tourney_level") or "").strip(), DEFAULT_K)

                    ea = 1.0 / (1.0 + 10 ** ((rb - ra) / 400.0))
                    elo[wid] = ra + k * (1.0 - ea)
                    elo[lid] = rb + k * (0.0 - (1.0 - ea))

                    n_matches[wid] = n_matches.get(wid, 0) + 1
                    n_matches[lid] = n_matches.get(lid, 0) + 1
        except Exception:
            continue

    return {
        pid: {"elo": round(rating, 0), "matches": n_matches.get(pid, 0)}
        for pid, rating in elo.items()
        if n_matches.get(pid, 0) >= MIN_MATCHES
    }


def _int(v):
    try:
        return int(v)
    except (TypeError, ValueError):
        return None
