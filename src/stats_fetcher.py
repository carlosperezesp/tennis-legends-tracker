"""
Downloads ATP match stats from Jeff Sackmann's tennis_atp GitHub repo and
computes serve/return profiles per player per year.

Outputs:
  data/player_stats_by_age.json   — stats per player per {year, age, surface}
  data/legend_benchmark.json      — average stats of GS legends by age (benchmark)

Usage:
  python3 src/stats_fetcher.py
  python3 src/stats_fetcher.py --players "Carlos Alcaraz" "Jannik Sinner"
  python3 src/stats_fetcher.py --no-cache   # re-download CSVs even if cached
"""

import argparse
import csv
import io
import json
import math
import urllib.request
import urllib.error
from collections import defaultdict
from datetime import date
from pathlib import Path
import live_results_overlay as lro

DATA_DIR = Path(__file__).resolve().parents[1] / "data"
CACHE_DIR = DATA_DIR / "_csv_cache"

BASE_URL = "https://raw.githubusercontent.com/JeffSackmann/tennis_atp/master"

# Sackmann player IDs + birth dates + years active (for download range)
PLAYERS = {
    # Elite benchmark (14+ GS)
    "Roger Federer":          {"id": 103819, "born": date(1981,  8,  8), "years": range(2001, 2020)},
    "Rafael Nadal":           {"id": 104745, "born": date(1986,  6,  3), "years": range(2004, 2025)},
    "Novak Djokovic":         {"id": 104925, "born": date(1987,  5, 22), "years": range(2004, 2025)},
    "Pete Sampras":           {"id": 101948, "born": date(1971,  8, 12), "years": range(1991, 2003)},
    # One-slam calibration players (1-3 GS, modern era)
    "Juan Carlos Ferrero":    {"id": 103507, "born": date(1980,  2, 12), "years": range(2000, 2009)},
    "Andy Roddick":           {"id": 104053, "born": date(1982,  8, 30), "years": range(2001, 2013)},
    "Gaston Gaudio":          {"id": 103292, "born": date(1978, 12,  9), "years": range(2000, 2008)},
    "Marat Safin":            {"id": 103498, "born": date(1980,  1, 27), "years": range(2000, 2008)},
    "Juan Martin del Potro":  {"id": 105223, "born": date(1988,  9, 23), "years": range(2007, 2020)},
    "Stan Wawrinka":          {"id": 104527, "born": date(1985,  3, 28), "years": range(2008, 2021)},
    "Andy Murray":            {"id": 104918, "born": date(1987,  5, 15), "years": range(2009, 2021)},
    "Marin Cilic":            {"id": 105227, "born": date(1988,  9, 28), "years": range(2012, 2023)},
    "Dominic Thiem":          {"id": 106233, "born": date(1993,  9,  3), "years": range(2016, 2024)},
    "Daniil Medvedev":        {"id": 106421, "born": date(1996,  2, 11), "years": range(2018, 2026)},
    # Active stars
    "Carlos Alcaraz":         {"id": 207989, "born": date(2003,  5,  5), "years": range(2021, 2026)},
    "Jannik Sinner":          {"id": 206173, "born": date(2001,  8, 16), "years": range(2019, 2026)},
}

# ── Benchmark and regression tier definitions ──────────────────────────────────

# Used for the "age benchmark" chart (what GS-winners look like at each age)
LEGEND_PLAYERS = ["Roger Federer", "Rafael Nadal", "Novak Djokovic", "Pete Sampras"]

# Career GS totals for kernel-regression calibration
HISTORICAL_GS = {
    "Roger Federer":         20,
    "Rafael Nadal":          22,
    "Novak Djokovic":        24,
    "Pete Sampras":          14,
    "Juan Carlos Ferrero":    1,
    "Andy Roddick":           1,
    "Gaston Gaudio":          1,
    "Marat Safin":            2,
    "Juan Martin del Potro":  1,
    "Stan Wawrinka":          3,
    "Andy Murray":            3,
    "Marin Cilic":            1,
    "Dominic Thiem":          1,
    "Daniil Medvedev":        1,
}

SURFACES = ("Hard", "Clay", "Grass", "All")


def _age_at_year_end(born: date, year: int) -> int:
    """Age as of December 31 of that year (matches Sackmann convention)."""
    return year - born.year


def _fetch_csv(year: int, use_cache: bool) -> list[dict]:
    """Downloads (or loads from cache) atp_matches_{year}.csv."""
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    cache_path = CACHE_DIR / f"atp_matches_{year}.csv"

    if use_cache and cache_path.exists():
        raw = cache_path.read_text(encoding="utf-8")
    else:
        url = f"{BASE_URL}/atp_matches_{year}.csv"
        try:
            with urllib.request.urlopen(url, timeout=20) as resp:
                raw = resp.read().decode("utf-8")
            cache_path.write_text(raw, encoding="utf-8")
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return []
            raise
        except Exception as e:
            print(f"  Warning: could not fetch {year}: {e}")
            return []

    rows = list(csv.DictReader(io.StringIO(raw)))
    return lro.merge_overlay_rows(rows, year)


def _safe(val, cast=float, default=None):
    try:
        return cast(val) if val not in (None, "", "nan") else default
    except (ValueError, TypeError):
        return default


class _AccumStats:
    """Accumulates raw serve/return point counts across matches."""

    def __init__(self):
        # Serve side (player serving)
        self.svpt = 0       # total serve points
        self.first_in = 0   # 1st serves in
        self.first_won = 0  # 1st serve points won
        self.second_won = 0 # 2nd serve points won
        self.aces = 0
        self.dfs = 0
        self.sv_gms = 0     # serve games
        self.bp_saved = 0
        self.bp_faced = 0
        # Return side (player returning; computed from opponent serve stats)
        self.ret_svpt = 0   # opponent serve points (= player return points)
        self.ret_first_in = 0
        self.ret_first_won_opp = 0   # opp first serve won (player lost these)
        self.ret_second_won_opp = 0  # opp second serve won (player lost these)
        self.ret_bp_won = 0          # break points converted
        self.ret_bp_opp = 0          # break points faced (as returner)
        self.matches = 0
        self.wins = 0
        # Head-to-head vs top-10 ranked opponents
        self.vs_top10_wins = 0
        self.vs_top10_total = 0

    def add_as_winner(self, row: dict):
        """Add stats from a row where this player is the winner."""
        self.matches += 1
        self.wins += 1
        opp_rank = _safe(row.get("loser_rank"), int)
        if opp_rank is not None and 1 <= opp_rank <= 10:
            self.vs_top10_total += 1
            self.vs_top10_wins += 1
        svpt   = _safe(row.get("w_svpt"),   int, 0)
        first  = _safe(row.get("w_1stIn"),  int, 0)
        fw     = _safe(row.get("w_1stWon"), int, 0)
        sw     = _safe(row.get("w_2ndWon"), int, 0)
        ace    = _safe(row.get("w_ace"),    int, 0)
        df     = _safe(row.get("w_df"),     int, 0)
        svgms  = _safe(row.get("w_SvGms"),  int, 0)
        bps    = _safe(row.get("w_bpSaved"),int, 0)
        bpf    = _safe(row.get("w_bpFaced"),int, 0)
        if svpt:
            self.svpt        += svpt
            self.first_in    += first
            self.first_won   += fw
            self.second_won  += sw
            self.aces        += ace
            self.dfs         += df
            self.sv_gms      += svgms
            self.bp_saved    += bps
            self.bp_faced    += bpf
        # Return: opponent is loser
        opp_svpt  = _safe(row.get("l_svpt"),    int, 0)
        opp_first = _safe(row.get("l_1stIn"),   int, 0)
        opp_fw    = _safe(row.get("l_1stWon"),  int, 0)
        opp_sw    = _safe(row.get("l_2ndWon"),  int, 0)
        opp_bps   = _safe(row.get("l_bpSaved"), int, 0)
        opp_bpf   = _safe(row.get("l_bpFaced"), int, 0)
        if opp_svpt:
            self.ret_svpt           += opp_svpt
            self.ret_first_in       += opp_first
            self.ret_first_won_opp  += opp_fw
            self.ret_second_won_opp += opp_sw
            # Player won the break points that opponent did NOT save
            self.ret_bp_won += (opp_bpf - opp_bps)
            self.ret_bp_opp += opp_bpf

    def add_as_loser(self, row: dict):
        """Add stats from a row where this player is the loser."""
        self.matches += 1
        opp_rank = _safe(row.get("winner_rank"), int)
        if opp_rank is not None and 1 <= opp_rank <= 10:
            self.vs_top10_total += 1
        svpt   = _safe(row.get("l_svpt"),    int, 0)
        first  = _safe(row.get("l_1stIn"),   int, 0)
        fw     = _safe(row.get("l_1stWon"),  int, 0)
        sw     = _safe(row.get("l_2ndWon"),  int, 0)
        ace    = _safe(row.get("l_ace"),     int, 0)
        df     = _safe(row.get("l_df"),      int, 0)
        svgms  = _safe(row.get("l_SvGms"),   int, 0)
        bps    = _safe(row.get("l_bpSaved"), int, 0)
        bpf    = _safe(row.get("l_bpFaced"), int, 0)
        if svpt:
            self.svpt        += svpt
            self.first_in    += first
            self.first_won   += fw
            self.second_won  += sw
            self.aces        += ace
            self.dfs         += df
            self.sv_gms      += svgms
            self.bp_saved    += bps
            self.bp_faced    += bpf
        # Return: opponent is winner
        opp_svpt  = _safe(row.get("w_svpt"),    int, 0)
        opp_first = _safe(row.get("w_1stIn"),   int, 0)
        opp_fw    = _safe(row.get("w_1stWon"),  int, 0)
        opp_sw    = _safe(row.get("w_2ndWon"),  int, 0)
        opp_bps   = _safe(row.get("w_bpSaved"), int, 0)
        opp_bpf   = _safe(row.get("w_bpFaced"), int, 0)
        if opp_svpt:
            self.ret_svpt           += opp_svpt
            self.ret_first_in       += opp_first
            self.ret_first_won_opp  += opp_fw
            self.ret_second_won_opp += opp_sw
            self.ret_bp_won += (opp_bpf - opp_bps)
            self.ret_bp_opp += opp_bpf

    def to_dict(self):
        """Convert accumulated counts to rate stats. Returns None if insufficient data."""
        if self.svpt < 50 or self.matches < 3:
            return None
        d: dict = {"matches": self.matches, "wins": self.wins}
        d["win_rate"] = round(self.wins / self.matches, 4) if self.matches else None

        # Serve stats
        d["first_serve_in_pct"]  = round(self.first_in / self.svpt, 4) if self.svpt else None
        d["first_serve_win_pct"] = round(self.first_won / self.first_in, 4) if self.first_in else None
        second_svpt = self.svpt - self.first_in
        d["second_serve_win_pct"] = round(self.second_won / second_svpt, 4) if second_svpt > 0 else None
        d["serve_win_pct"] = round((self.first_won + self.second_won) / self.svpt, 4) if self.svpt else None
        d["ace_pct"]  = round(self.aces / self.svpt, 4) if self.svpt else None
        d["df_pct"]   = round(self.dfs  / self.svpt, 4) if self.svpt else None
        d["bp_save_pct"] = round(self.bp_saved / self.bp_faced, 4) if self.bp_faced else None

        # Return stats
        if self.ret_svpt >= 50:
            ret_won = self.ret_svpt - self.ret_first_won_opp - self.ret_second_won_opp
            d["return_win_pct"] = round(ret_won / self.ret_svpt, 4)
            ret_1st_lost = self.ret_first_won_opp
            d["return_1st_win_pct"] = round(
                (self.ret_first_in - ret_1st_lost) / self.ret_first_in, 4
            ) if self.ret_first_in else None
            ret_2nd_svpt = self.ret_svpt - self.ret_first_in
            d["return_2nd_win_pct"] = round(
                (ret_2nd_svpt - self.ret_second_won_opp) / ret_2nd_svpt, 4
            ) if ret_2nd_svpt > 0 else None
            d["bp_conversion_pct"] = round(
                self.ret_bp_won / self.ret_bp_opp, 4
            ) if self.ret_bp_opp else None
        else:
            d["return_win_pct"] = None
            d["return_1st_win_pct"] = None
            d["return_2nd_win_pct"] = None
            d["bp_conversion_pct"] = None

        # Composite dominance (serve + return win%)
        srv = d["serve_win_pct"]
        ret = d["return_win_pct"]
        if srv is not None and ret is not None:
            d["dominance_ratio"] = round(srv / (1 - ret) if ret < 1 else 0, 4)
        else:
            d["dominance_ratio"] = None

        # Win % vs top-10 ranked opponents (rate + raw counts for multi-year aggregation)
        d["vs_top10_win_pct"] = (
            round(self.vs_top10_wins / self.vs_top10_total, 4)
            if self.vs_top10_total >= 3 else None
        )
        d["vs_top10_wins_n"]  = self.vs_top10_wins
        d["vs_top10_total_n"] = self.vs_top10_total

        return d


def compute_player_stats(player_name: str, config: dict, use_cache: bool) -> dict:
    """
    Returns {year: {surface: stats_dict}} for a player, across all their active years.
    """
    pid = config["id"]
    born = config["born"]
    years = config["years"]
    result: dict = {}

    for year in years:
        print(f"  {player_name} {year}...", end=" ", flush=True)
        rows = _fetch_csv(year, use_cache)
        if not rows:
            print("(no data)")
            continue

        # accumulators keyed by surface ("All" is always included)
        accum: dict[str, _AccumStats] = {s: _AccumStats() for s in SURFACES}

        for row in rows:
            wid = _safe(row.get("winner_id"), int)
            lid = _safe(row.get("loser_id"),  int)
            if wid != pid and lid != pid:
                continue
            surf = row.get("surface", "").strip() or "Unknown"
            as_winner = (wid == pid)
            for surface_key in (surf, "All"):
                if surface_key not in accum:
                    accum[surface_key] = _AccumStats()
                if as_winner:
                    accum[surface_key].add_as_winner(row)
                else:
                    accum[surface_key].add_as_loser(row)

        year_stats: dict = {}
        for surf, acc in accum.items():
            stats = acc.to_dict()
            if stats:
                stats["age"] = _age_at_year_end(born, year)
                year_stats[surf] = stats

        if year_stats:
            result[year] = year_stats
            print(f"ok ({year_stats.get('All', {}).get('matches', '?')} matches)")
        else:
            print("(insufficient data)")

    return result


def build_legend_benchmark(all_stats: dict) -> dict:
    """
    Averages stats across LEGEND_PLAYERS at each age to create a benchmark.
    Returns {age: {surface: {stat: value}}} where values are averages.
    """
    # Collect all (age, surface, stat_value) across legends
    by_age: dict[int, dict[str, dict[str, list]]] = defaultdict(lambda: defaultdict(lambda: defaultdict(list)))

    stat_keys = [
        "first_serve_in_pct", "first_serve_win_pct", "second_serve_win_pct",
        "serve_win_pct", "ace_pct", "df_pct", "bp_save_pct",
        "return_win_pct", "return_1st_win_pct", "return_2nd_win_pct",
        "bp_conversion_pct", "dominance_ratio", "win_rate", "vs_top10_win_pct",
    ]

    for name in LEGEND_PLAYERS:
        player_data = all_stats.get(name, {})
        for year, year_data in player_data.items():
            for surface, stats in year_data.items():
                age = stats.get("age")
                if age is None:
                    continue
                for key in stat_keys:
                    val = stats.get(key)
                    if val is not None:
                        by_age[age][surface][key].append(val)

    benchmark: dict = {}
    for age in sorted(by_age):
        benchmark[age] = {}
        for surface, stat_lists in by_age[age].items():
            avg_stats: dict = {"age": age, "n_datapoints": 0}
            for key, vals in stat_lists.items():
                if vals:
                    avg_stats[key] = round(sum(vals) / len(vals), 4)
                    avg_stats["n_datapoints"] = max(avg_stats["n_datapoints"], len(vals))
                else:
                    avg_stats[key] = None
            benchmark[age][surface] = avg_stats

    return benchmark


def compute_profile_similarity(player_year_stats: dict, benchmark: dict, surface: str = "All"):
    """
    Returns a 0-100 score: how similar is this player's profile to the legend benchmark
    at the same age. Higher = closer to a GS-winner profile.

    Weights reflect predictive value for GS success.
    """
    age = player_year_stats.get("age")
    if age is None:
        return None
    bench_entry = benchmark.get(str(age)) or benchmark.get(age)
    if not bench_entry:
        return None
    bench = bench_entry.get(surface) or bench_entry.get("All")
    if not bench:
        return None

    # (player_key, benchmark_key, weight, higher_is_better)
    comparisons = [
        ("serve_win_pct",       "serve_win_pct",       25, True),
        ("return_win_pct",      "return_win_pct",       25, True),
        ("bp_save_pct",         "bp_save_pct",          15, True),
        ("bp_conversion_pct",   "bp_conversion_pct",    15, True),
        ("first_serve_in_pct",  "first_serve_in_pct",   10, True),
        ("dominance_ratio",     "dominance_ratio",       5, True),
        ("win_rate",            "win_rate",              5, True),
    ]

    total_weight = 0
    weighted_sum = 0.0

    for p_key, b_key, weight, higher_better in comparisons:
        p_val = player_year_stats.get(p_key)
        b_val = bench.get(b_key)
        if p_val is None or b_val is None or b_val == 0:
            continue
        # Ratio: how close is the player to the legend benchmark
        ratio = p_val / b_val if higher_better else b_val / p_val
        # Clamp ratio to [0, 1.2] then normalize to [0, 1]
        normalized = min(ratio, 1.2) / 1.2
        weighted_sum += normalized * weight
        total_weight += weight

    if total_weight == 0:
        return None
    return round(weighted_sum / total_weight * 100, 1)


def get_top_n_players(n: int = 200, use_cache: bool = True) -> list:
    """
    Returns the top-N ATP ranked players from atp_rankings_current.csv,
    enriched with name and birth date from atp_players.csv.
    Each entry: {player_id, rank, rank_points, name, born}
    """
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    rank_cache = CACHE_DIR / "atp_rankings_current.csv"
    if use_cache and rank_cache.exists():
        raw = rank_cache.read_text(encoding="utf-8")
    else:
        url = f"{BASE_URL}/atp_rankings_current.csv"
        with urllib.request.urlopen(url, timeout=20) as resp:
            raw = resp.read().decode("utf-8")
        rank_cache.write_text(raw, encoding="utf-8")

    rankings = list(csv.DictReader(io.StringIO(raw)))
    # File contains multiple dates — keep only the most recent date
    if rankings:
        latest_date = max(r.get("ranking_date", "") for r in rankings)
        rankings = [r for r in rankings if r.get("ranking_date") == latest_date]
    # Sort by rank ascending, take top N
    rankings.sort(key=lambda r: int(r.get("rank", 9999) or 9999))
    top = rankings[:n]

    # Build player_id → (name, born) lookup
    registry = _load_player_registry(use_cache)
    pid_to_info = {}
    for row in registry:
        try:
            pid = int(row["player_id"])
        except (KeyError, ValueError):
            continue
        first = row.get("name_first", "").strip()
        last  = row.get("name_last",  "").strip()
        dob   = row.get("dob", "")
        born  = None
        if dob and len(dob) == 8:
            try:
                born = date(int(dob[:4]), int(dob[4:6]), int(dob[6:8]))
            except ValueError:
                pass
        pid_to_info[pid] = {"name": f"{first} {last}".strip(), "born": born}

    result = []
    for r in top:
        try:
            pid = int(r.get("player_id") or r.get("player") or 0)
        except (KeyError, ValueError):
            continue
        if not pid:
            continue
        info = pid_to_info.get(pid, {})
        result.append({
            "player_id":   pid,
            "rank":        int(r.get("rank", 0) or 0),
            "rank_points": int(r.get("points", 0) or 0),
            "name":        info.get("name", f"Player {pid}"),
            "born":        info.get("born"),
        })
    return result


def compute_all_players_batch(players: list, years: tuple = (2023, 2024, 2025),
                               use_cache: bool = True) -> dict:
    """
    Efficiently computes serve/return stats for many players by loading each
    year's CSV only once (instead of once per player).

    Returns {player_id: {year: {surface: stats_dict}}}
    """
    print(f"Loading {len(years)} year(s) of match data...", flush=True)
    # Load all CSV rows, keyed by (player_id, year, is_winner)
    by_pid: dict = defaultdict(lambda: defaultdict(list))
    for year in years:
        rows = _fetch_csv(year, use_cache)
        print(f"  {year}: {len(rows)} matches", flush=True)
        for row in rows:
            wid = _safe(row.get("winner_id"), int)
            lid = _safe(row.get("loser_id"),  int)
            if wid:
                by_pid[wid][year].append((row, True))
            if lid:
                by_pid[lid][year].append((row, False))

    # Build player lookup
    pid_to_born = {p["player_id"]: p["born"] for p in players if p.get("born")}

    print(f"Computing stats for {len(players)} players...", flush=True)
    results = {}
    for p in players:
        pid  = p["player_id"]
        born = p.get("born")
        if not born:
            continue

        year_stats = {}
        for year in years:
            p_rows = by_pid.get(pid, {}).get(year, [])
            if not p_rows:
                continue
            accum = {s: _AccumStats() for s in SURFACES}
            for row, as_winner in p_rows:
                surf = row.get("surface", "").strip() or "Unknown"
                for skey in (surf, "All"):
                    if skey not in accum:
                        accum[skey] = _AccumStats()
                    if as_winner:
                        accum[skey].add_as_winner(row)
                    else:
                        accum[skey].add_as_loser(row)

            surface_stats = {}
            for surf, acc in accum.items():
                stats = acc.to_dict()
                if stats:
                    stats["age"] = _age_at_year_end(born, year)
                    surface_stats[surf] = stats

            if surface_stats:
                year_stats[str(year)] = surface_stats

        if year_stats:
            results[pid] = year_stats

    return results


def count_gs_wins_batch(player_ids: set, years_range, use_cache: bool = True) -> dict:
    """
    Counts Grand Slam wins per player_id across the given years range.
    Returns {player_id: {year: win_count}}
    Efficient: each CSV is loaded once.
    """
    gs_by_pid: dict = defaultdict(lambda: defaultdict(int))
    for year in years_range:
        rows = _fetch_csv(year, use_cache)
        for row in rows:
            if row.get("tourney_level") == "G" and row.get("round") == "F":
                wid = _safe(row.get("winner_id"), int)
                if wid and wid in player_ids:
                    gs_by_pid[wid][year] += 1
    return dict(gs_by_pid)


_REGRESSION_STAT_KEYS = [
    "serve_win_pct", "return_win_pct", "bp_save_pct",
    "bp_conversion_pct", "first_serve_in_pct", "win_rate",
]


def _avg_stats_for_player(player_year_data: dict) -> dict:
    """Average All-surface stats across all available years for a player."""
    sums  = {k: 0.0 for k in _REGRESSION_STAT_KEYS}
    counts = {k: 0   for k in _REGRESSION_STAT_KEYS}
    for year_data in player_year_data.values():
        all_surf = year_data.get("All", {})
        for k in _REGRESSION_STAT_KEYS:
            v = all_surf.get(k)
            if v is not None:
                sums[k]   += v
                counts[k] += 1
    return {k: sums[k] / counts[k] for k in _REGRESSION_STAT_KEYS if counts[k] > 0}


def _stats_at_age_range(player_year_data: dict, target_age: int,
                         window: int = 3) -> dict:
    """
    Average All-surface stats for years where the player's age is within
    [target_age - window, target_age + window].  Falls back to career average.
    """
    sums   = {k: 0.0 for k in _REGRESSION_STAT_KEYS}
    counts = {k: 0   for k in _REGRESSION_STAT_KEYS}
    for year_data in player_year_data.values():
        all_surf = year_data.get("All", {})
        age = all_surf.get("age")
        if age is None or abs(age - target_age) > window:
            continue
        for k in _REGRESSION_STAT_KEYS:
            v = all_surf.get(k)
            if v is not None:
                sums[k]   += v
                counts[k] += 1
    result = {k: sums[k] / counts[k] for k in _REGRESSION_STAT_KEYS if counts[k] > 0}
    # Fall back to career average if too few age-matched data points
    if len(result) < 3:
        result = _avg_stats_for_player(player_year_data)
    return result


def _gaussian_similarity(a: dict, b: dict, bandwidth: float = 0.004) -> float:
    """
    Gaussian kernel similarity between two stat vectors.
    Returns 0–1 (1 = identical profiles).
    Bandwidth ~0.004 → half-similarity at ~6% absolute difference in stats.
    """
    keys = [k for k in _REGRESSION_STAT_KEYS
            if k in a and k in b and a[k] is not None and b[k] is not None]
    if not keys:
        return 0.0
    sq_dist = sum((a[k] - b[k]) ** 2 for k in keys) / len(keys)
    return math.exp(-sq_dist / bandwidth)


def build_regression_targets(all_stats: dict) -> dict:
    """
    For each player in HISTORICAL_GS, compute career-average stats and pair
    with their known career GS total.

    Returns {name: {"career_gs": int, "avg_stats": {stat: float}}}
    """
    targets = {}
    for name, career_gs in HISTORICAL_GS.items():
        p_data = all_stats.get(name, {})
        if not p_data:
            continue
        avg = _avg_stats_for_player(p_data)
        if len(avg) >= 3:
            targets[name] = {"career_gs": career_gs, "avg_stats": avg}
    return targets


def compute_expected_gs(player_recent_stats: dict, all_stats: dict,
                         regression_targets: dict, current_age: int) -> float:
    """
    Kernel regression: expected CAREER GS total for a player based on their
    current stats profile compared against all historical calibration players.

    Uses age-matched stats for historical players (±3 years around current_age),
    so comparison is always apple-to-apple.
    """
    total_w = 0.0
    weighted_gs = 0.0
    for name, target in regression_targets.items():
        h_data = all_stats.get(name, {})
        if h_data:
            h_stats = _stats_at_age_range(h_data, current_age, window=3)
        else:
            h_stats = target["avg_stats"]
        if not h_stats:
            continue
        sim = _gaussian_similarity(player_recent_stats, h_stats)
        weighted_gs += sim * target["career_gs"]
        total_w     += sim
    return (weighted_gs / total_w) if total_w > 0 else 0.0


def _load_player_registry(use_cache: bool = True) -> list:
    """Downloads (or loads from cache) atp_players.csv."""
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    cache_path = CACHE_DIR / "atp_players.csv"
    if use_cache and cache_path.exists():
        raw = cache_path.read_text(encoding="utf-8")
    else:
        url = f"{BASE_URL}/atp_players.csv"
        with urllib.request.urlopen(url, timeout=20) as resp:
            raw = resp.read().decode("utf-8")
        cache_path.write_text(raw, encoding="utf-8")
    return list(csv.DictReader(io.StringIO(raw)))


def find_player(name_query: str, use_cache: bool = True):
    """
    Searches atp_players.csv for best match to name_query.
    Returns dict {player_id, name, born} or None.
    Matches are tried as "First Last" and "Last First".
    Higher player_id wins ties (more recent player).
    """
    registry = _load_player_registry(use_cache)
    query = name_query.lower().strip()

    candidates = []
    for row in registry:
        first = row.get("name_first", "").strip()
        last  = row.get("name_last",  "").strip()
        full      = f"{first} {last}".strip().lower()
        full_rev  = f"{last} {first}".strip().lower()

        if query in (full, full_rev):
            score = 3
        elif full.startswith(query) or full_rev.startswith(query):
            score = 2
        elif all(w in full for w in query.split()):
            score = 1
        else:
            continue

        dob_str = row.get("dob", "")
        born = None
        if dob_str and len(dob_str) == 8:
            try:
                born = date(int(dob_str[:4]), int(dob_str[4:6]), int(dob_str[6:8]))
            except ValueError:
                pass

        try:
            pid = int(row["player_id"])
        except (KeyError, ValueError):
            continue

        candidates.append({"score": score, "player_id": pid,
                            "name": f"{first} {last}".strip(), "born": born})

    if not candidates:
        return None
    candidates.sort(key=lambda x: (-x["score"], -x["player_id"]))
    return candidates[0]


def _count_gs_wins(player_id: int, rows: list) -> int:
    """Count Grand Slam wins (tourney_level='G', round='F', player won) in a set of rows."""
    wins = 0
    for row in rows:
        if row.get("tourney_level") == "G" and row.get("round") == "F":
            wid = _safe(row.get("winner_id"), int)
            if wid == player_id:
                wins += 1
    return wins


def lookup_player(name_query: str, years_back: int = 3, use_cache: bool = True):
    """
    Full on-demand pipeline for any ATP player:
      1. Finds player_id + birth date in atp_players.csv
      2. Downloads recent years' match data (uses cache)
      3. Computes serve/return stats + profile similarity vs legend benchmark
      4. Counts Grand Slam wins from match history

    Returns dict or None if not found.
    """
    info = find_player(name_query, use_cache)
    if not info or not info["born"]:
        return None

    pid  = info["player_id"]
    born = info["born"]
    name = info["name"]

    current_year = date.today().year
    years = range(max(current_year - years_back, 2000), current_year + 1)
    config = {"id": pid, "born": born, "years": years}

    print(f"Looking up {name} (ID {pid}, born {born})...")
    stats_by_year = compute_player_stats(name, config, use_cache)

    if not stats_by_year:
        return None

    # Load legend benchmark
    bench_path = DATA_DIR / "legend_benchmark.json"
    benchmark = {}
    if bench_path.exists():
        with open(bench_path, encoding="utf-8") as f:
            benchmark = json.load(f)

    latest_year = max(stats_by_year.keys(), key=int)
    latest_stats = stats_by_year[latest_year].get("All", {})
    age = latest_stats.get("age") or _age_at_year_end(born, int(latest_year))

    # GS wins from full match history (scan all cached years)
    all_years_for_gs = range(born.year + 14, current_year + 1)
    gs_total = 0
    gs_by_year = {}
    cumulative = 0
    for yr in all_years_for_gs:
        rows = _fetch_csv(yr, use_cache)
        wins = _count_gs_wins(pid, rows)
        if wins:
            gs_by_year[yr] = wins
            cumulative += wins
    gs_total = cumulative

    # GS trajectory list (age, cumulative_gs)
    gs_trajectory = []
    cumul = 0
    for yr in sorted(gs_by_year):
        cumul += gs_by_year[yr]
        age_yr = _age_at_year_end(born, yr)
        gs_trajectory.append((age_yr, cumul))

    sim = compute_profile_similarity(latest_stats, benchmark) if benchmark else None
    bench_at_age = (benchmark.get(str(age)) or benchmark.get(age) or {}).get("All", {})

    return {
        "player_id":        pid,
        "name":             name,
        "born":             born,
        "age":              age,
        "latest_year":      int(latest_year),
        "stats_by_year":    stats_by_year,
        "latest_stats":     latest_stats,
        "profile_similarity": sim,
        "benchmark_at_age": bench_at_age,
        "gs_total":         gs_total,
        "gs_by_year":       gs_by_year,
        "gs_trajectory":    gs_trajectory,
    }


def main():
    parser = argparse.ArgumentParser(description="Compute tennis player serve/return profiles from ATP match data")
    parser.add_argument("--players", nargs="*", help="Player names to process (default: all)")
    parser.add_argument("--no-cache", action="store_true", help="Re-download CSVs even if cached")
    args = parser.parse_args()

    use_cache = not args.no_cache
    target_names = args.players or list(PLAYERS.keys())

    all_stats: dict = {}

    # Load existing output if present (so we can add new players incrementally)
    out_path = DATA_DIR / "player_stats_by_age.json"
    if out_path.exists():
        with open(out_path, encoding="utf-8") as f:
            all_stats = json.load(f)

    for name in target_names:
        if name not in PLAYERS:
            print(f"Unknown player: {name}. Known: {list(PLAYERS)}")
            continue
        print(f"\n=== {name} ===")
        all_stats[name] = compute_player_stats(name, PLAYERS[name], use_cache)

    # Save player stats
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(all_stats, f, ensure_ascii=False, indent=2)
    print(f"\nSaved: {out_path}")

    # Build and save legend benchmark
    benchmark = build_legend_benchmark(all_stats)
    bench_path = DATA_DIR / "legend_benchmark.json"
    with open(bench_path, "w", encoding="utf-8") as f:
        json.dump(benchmark, f, ensure_ascii=False, indent=2)
    print(f"Saved: {bench_path}")

    # Print a quick summary for active players
    print("\n--- Profile similarity vs legend benchmark (All surfaces) ---")
    for name in ["Carlos Alcaraz", "Jannik Sinner"]:
        player_data = all_stats.get(name, {})
        if not player_data:
            continue
        latest_year = max(player_data.keys(), key=int)
        latest_stats = player_data[latest_year].get("All")
        if not latest_stats:
            continue
        score = compute_profile_similarity(latest_stats, benchmark)
        age = latest_stats.get("age")
        print(f"  {name} (age {age}, {latest_year}): {score}/100")


if __name__ == "__main__":
    main()
