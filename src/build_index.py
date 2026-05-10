"""
Generates examples/index.html — a single interactive dashboard for any
player in the current ATP top N.

Usage:
  python3 src/build_index.py             # top 300, 3 years of data
  python3 src/build_index.py --top 100   # faster
  python3 src/build_index.py --no-cache  # force re-download

The generated index.html has all data embedded — no server needed.
Open it directly in a browser and search/click any player to see their
GS trajectory, profile stats, and projections vs the legend benchmark.
"""

from __future__ import annotations

import argparse
import bisect
import csv
import json
import math
import sys
from datetime import date
from pathlib import Path
from html import escape

SRC_DIR = Path(__file__).resolve().parent
ROOT    = SRC_DIR.parent
DATA_DIR    = ROOT / "data"
EXAMPLES_DIR = ROOT / "examples"

sys.path.insert(0, str(SRC_DIR))
import stats_fetcher as sf
import elo_computer as ec
from legend_trajectory import (
    GS_TRAJECTORIES, LEGEND_COLORS, BACKGROUND_LEGENDS,
    _realistic_projection, _gs_at_age, safe_float,
)

# Module-level globals populated in main()
_REGRESSION_TARGETS: dict = {}
_ALL_STATS: dict = {}
_LEGEND_SIM_BY_AGE: dict = {}   # {legend_name: {age: sim_score}}
_ELO_AGE_REFERENCE: dict = {}

# ── Constants ─────────────────────────────────────────────────────────────────

LEGEND_GS_WIN_YEARS = {
    "Roger Federer":  range(2001, 2020),
    "Rafael Nadal":   range(2004, 2025),
    "Novak Djokovic": range(2004, 2025),
    "Andre Agassi":   range(1991, 2007),
    "Pete Sampras":   range(1991, 2003),
}

# Players already in our curated dataset — use known GS counts
KNOWN_GS = {
    "Carlos Alcaraz": 7,
    "Jannik Sinner":  3,
}

STAT_LABELS = {
    "win_rate":           "Win rate %",
    "serve_win_pct":      "Saque ganado %",
    "return_win_pct":     "Resto ganado %",
    "bp_save_pct":        "BP salvados %",
    "vs_top10_win_pct":   "Win % vs Top 10",
}

ELO_REFERENCE_GROUPS = {
    "big3": {
        "label": "Big 3",
        "players": ["Roger Federer", "Rafael Nadal", "Novak Djokovic"],
    },
    "samprassi": {
        "label": "Sampras/Agassi",
        "players": ["Pete Sampras", "Andre Agassi"],
    },
    "slam_winners": {
        "label": "Campeones GS recientes",
        "players": [
            "Juan Carlos Ferrero", "Gaston Gaudio", "Marat Safin",
            "Juan Martin del Potro", "Andy Murray", "Stan Wawrinka",
            "Marin Cilic", "Dominic Thiem", "Daniil Medvedev",
        ],
    },
}

LEGEND_PROFILE_GROUPS = [
    {
        "name": "Perfil Big 3",
        "button": "Big 3",
        "group": "Arquetipo",
        "players": ["Novak Djokovic", "Rafael Nadal", "Roger Federer"],
        "gs": 66,
    },
    {
        "name": "Perfil Sampras/Agassi",
        "button": "Samprassi",
        "group": "Arquetipo",
        "players": ["Pete Sampras", "Andre Agassi"],
        "gs": 22,
    },
    {
        "name": "Perfil campeones 1-3 GS",
        "button": "1-3 GS",
        "group": "Arquetipo",
        "players": [
            "Juan Carlos Ferrero", "Gaston Gaudio", "Marat Safin",
            "Juan Martin del Potro", "Andy Murray", "Stan Wawrinka",
            "Marin Cilic", "Dominic Thiem", "Daniil Medvedev",
        ],
        "gs": 15,
    },
]

LEGEND_COMPARISON_NAMES = [
    "Novak Djokovic",
    "Rafael Nadal",
    "Roger Federer",
    "Pete Sampras",
    "Andre Agassi",
    "Juan Carlos Ferrero",
    "Gaston Gaudio",
    "Marat Safin",
    "Juan Martin del Potro",
    "Andy Murray",
    "Stan Wawrinka",
    "Marin Cilic",
    "Dominic Thiem",
    "Daniil Medvedev",
]

LEGEND_TOTAL_GS = {
    "Novak Djokovic": 24,
    "Rafael Nadal": 22,
    "Roger Federer": 20,
    "Pete Sampras": 14,
    "Andre Agassi": 8,
    "Juan Carlos Ferrero": 1,
    "Gaston Gaudio": 1,
    "Marat Safin": 2,
    "Juan Martin del Potro": 1,
    "Andy Murray": 3,
    "Stan Wawrinka": 3,
    "Marin Cilic": 1,
    "Dominic Thiem": 1,
    "Daniil Medvedev": 1,
}


# ── Helpers ───────────────────────────────────────────────────────────────────

def _age_at(born: date, year: int) -> int:
    return year - born.year


def _proj_str(c, a):
    return f"{c}–{a}" if c != a else str(c)


def _context_phrase(age, gs, expected_gs, proj_c, proj_a):
    eg  = round(expected_gs) if expected_gs else 0
    ps  = _proj_str(proj_c, proj_a)
    if age < 23 and gs == 0:
        return (f"Con solo <b>{age} años</b> y un perfil ya muy prometedor, el modelo ve potencial "
                f"para <b>{eg} GS</b> en una carrera ideal. Proyección de aquí en adelante: "
                f"<b>{ps} GS</b>.")
    if gs > 0:
        return (f"Con <b>{age} años</b> y <b>{gs} GS</b> ya en el bolsillo, el modelo sitúa el "
                f"techo de una carrera ideal en <b>{eg} GS</b>. Proyección real: "
                f"<b>{ps} GS</b> en total.")
    if age < 27:
        return (f"Con <b>{age} años</b> y sin GS todavía, el perfil estadístico tiene nivel de "
                f"candidato real: potencial para <b>{eg} GS</b> en condiciones ideales. "
                f"Proyección real: <b>{ps} GS</b>.")
    if proj_a == 0:
        return (f"Con <b>{age} años</b> y sin GS todavía, la ventana se ha cerrado. "
                f"El modelo no ve margen para ningún GS adicional.")
    return (f"Con <b>{age} años</b> y sin GS aún, la ventana se estrecha. "
            f"El modelo proyecta como máximo <b>{ps} GS</b> de aquí en adelante.")


def _career_adj_score(sim, age, gs):
    """Career-Adjusted Potential Index: stats potential × age discount × GS track record."""
    if sim is None:
        return None
    age_factor = 1.0 if age <= 23 else math.exp(-0.09 * (age - 23))
    gs_bonus   = 1.0 + 0.08 * min(7, gs)
    return min(100, round(sim * age_factor * gs_bonus, 1))


def _near_term_score(sim, age, gs, rank):
    """Probability index for winning a GS in the next ~3 years.
    Uses raw stats quality (sim), current ranking, age window, and GS-drought penalty.
    """
    if sim is None or rank is None:
        return None
    rank_factor    = math.exp(-0.015 * max(0, rank - 1))
    prime_window   = 1.0 if age <= 30 else math.exp(-0.15 * (age - 30))
    drought_factor = 1.0 if (gs > 0 or age <= 25) else math.exp(-0.15 * (age - 25))
    return min(100, round(sim * rank_factor * prime_window * drought_factor, 1))


def _surface_stats(stats_by_year: dict) -> dict:
    """Aggregate win% per surface across all available years (min 8 matches)."""
    result = {}
    for surf in ("Hard", "Clay", "Grass"):
        wins = matches = 0
        for yr_data in stats_by_year.values():
            sd = yr_data.get(surf) or {}
            wins    += sd.get("wins", 0) or 0
            matches += sd.get("matches", 0) or 0
        if matches >= 8:
            result[surf] = {"win_pct": round(wins / matches * 100, 1), "matches": matches}
    return result


def _surface_match_counts(stats_by_year: dict) -> dict:
    counts = {surf: 0 for surf in ("All", "Hard", "Clay", "Grass")}
    for yr_data in stats_by_year.values():
        for surf in counts:
            counts[surf] += ((yr_data.get(surf) or {}).get("matches") or 0)
    return counts


def _confidence_adjusted_score(score: float, matches: float, target: int) -> float:
    if score is None:
        return None
    confidence = min(1.0, math.sqrt(max(0.0, matches) / target)) if target else 1.0
    return round(50 + (score - 50) * confidence, 1)


def _clamp(value: float, lo: float = 0.0, hi: float = 100.0) -> float:
    return max(lo, min(hi, value))


def _rank_strength_score(rank) -> float | None:
    rank = _safe_int(rank, None)
    if rank is None or rank <= 0:
        return None
    # Top 1 ~= 100, top 10 ~= 95, top 100 ~= 50, top 200 ~= 0.
    return round(_clamp((201 - min(rank, 201)) / 200 * 100), 1)


def _elo_strength_score(elo) -> float | None:
    if elo is None:
        return None
    # ATP Elo usually spans roughly 1450-1950 for this top-200 view.
    return round(_clamp((float(elo) - 1450) / 500 * 100), 1)


def _load_player_id_lookup(use_cache: bool = True) -> dict:
    lookup = {}
    for row in sf._load_player_registry(use_cache):
        name = f"{row.get('name_first', '').strip()} {row.get('name_last', '').strip()}".strip()
        try:
            pid = int(row.get("player_id") or 0)
        except (TypeError, ValueError):
            continue
        if name and pid:
            lookup[name] = {
                "id": pid,
                "born": _parse_dob(row.get("dob", "")),
            }
    return lookup


def _parse_dob(raw: str) -> date | None:
    if not raw or len(raw) != 8:
        return None
    try:
        return date(int(raw[:4]), int(raw[4:6]), int(raw[6:8]))
    except ValueError:
        return None


def _build_elo_age_reference(use_cache: bool = True) -> dict:
    """Year-end Elo reference curves by historical achievement tier and surface."""
    lookup = _load_player_id_lookup(use_cache)
    ref_names = {name for group in ELO_REFERENCE_GROUPS.values() for name in group["players"]}
    ref_ids = {
        lookup[name]["id"]: {"name": name, "born": lookup[name]["born"]}
        for name in ref_names
        if lookup.get(name, {}).get("id") and lookup.get(name, {}).get("born")
    }
    if not ref_ids:
        return {}

    surfaces = ("All", "Hard", "Clay", "Grass")
    elo = {surface: {} for surface in surfaces}
    matches = {surface: {} for surface in surfaces}
    yearly = {surface: {name: {} for name in ref_names} for surface in surfaces}
    for path in sorted((DATA_DIR / "_csv_cache").glob("atp_matches_*.csv")):
        try:
            year = int(path.stem.split("_")[-1])
        except ValueError:
            continue
        try:
            with open(path, encoding="utf-8", newline="") as fh:
                for row in csv.DictReader(fh):
                    wid = _safe_int(row.get("winner_id"), None)
                    lid = _safe_int(row.get("loser_id"), None)
                    if wid is None or lid is None:
                        continue
                    k = ec.K_FACTORS.get((row.get("tourney_level") or "").strip(), ec.DEFAULT_K)
                    surface = (row.get("surface") or "").strip()
                    surface_keys = ("All", surface) if surface in ("Hard", "Clay", "Grass") else ("All",)
                    for key in surface_keys:
                        ratings = elo[key]
                        counts = matches[key]
                        ra = ratings.get(wid, ec.INITIAL_ELO)
                        rb = ratings.get(lid, ec.INITIAL_ELO)
                        ea = 1.0 / (1.0 + 10 ** ((rb - ra) / 400.0))
                        ratings[wid] = ra + k * (1.0 - ea)
                        ratings[lid] = rb + k * (0.0 - (1.0 - ea))
                        counts[wid] = counts.get(wid, 0) + 1
                        counts[lid] = counts.get(lid, 0) + 1
        except OSError:
            continue

        for surface in surfaces:
            min_matches = 20 if surface == "All" else 8
            for pid, info in ref_ids.items():
                if pid not in elo[surface] or matches[surface].get(pid, 0) < min_matches:
                    continue
                age = _age_at(info["born"], year)
                yearly[surface][info["name"]][age] = round(elo[surface][pid], 0)

    by_surface = {}
    for surface in surfaces:
        by_group = {}
        for key, group in ELO_REFERENCE_GROUPS.items():
            by_age = {}
            for name in group["players"]:
                for age, value in yearly[surface].get(name, {}).items():
                    by_age.setdefault(age, []).append(value)
            by_group[key] = {
                age: round(sum(values) / len(values), 0)
                for age, values in by_age.items()
                if values
            }
        by_surface[surface] = by_group
    return by_surface


def _reference_elo_for_age(group_key: str, age: int, surface: str = "All") -> float | None:
    by_surface = _ELO_AGE_REFERENCE or {}
    surface_refs = by_surface.get(surface) or by_surface.get("All") or {}
    curve = surface_refs.get(group_key) or {}
    if not curve:
        return None
    for delta in (0, -1, 1, -2, 2, -3, 3):
        if age + delta in curve:
            return curve[age + delta]
    return None


def _age_elo_strength_score(elo, age: int, surface: str = "All") -> tuple[float | None, dict]:
    if elo is None or age is None:
        return None, {}
    elo = float(elo)
    refs = {
        key: _reference_elo_for_age(key, age, surface)
        for key in ("big3", "samprassi", "slam_winners")
    }
    multi = refs.get("slam_winners")
    samprassi = refs.get("samprassi")
    big3 = refs.get("big3")
    if not any(refs.values()):
        return _elo_strength_score(elo), refs
    anchors = sorted(v for v in (multi, samprassi) if v is not None)
    lower = anchors[0] if anchors else None
    upper = anchors[-1] if anchors else None
    if big3 and upper:
        if elo >= big3:
            score = 100
        elif elo >= upper:
            score = 78 + (elo - upper) / max(1, big3 - upper) * 17
        elif lower and elo >= lower:
            score = 62 + (elo - lower) / max(1, upper - lower) * 16
        elif lower:
            score = 25 + (elo - 1450) / max(1, lower - 1450) * 37
        else:
            score = _elo_strength_score(elo)
    elif upper:
        score = 25 + (elo - 1450) / max(1, upper - 1450) * 55
    else:
        score = _elo_strength_score(elo)
    refs = {k: round(v, 0) if v is not None else None for k, v in refs.items()}
    return round(_clamp(score), 1), refs


def _blend_available(values: list[tuple[float | None, float]]) -> float | None:
    total = sum(weight for value, weight in values if value is not None)
    if total <= 0:
        return None
    return sum(value * weight for value, weight in values if value is not None) / total


def _form_score(metrics: dict | None, surface: str) -> float | None:
    by_surface = metrics or {}
    surface_metrics = by_surface.get(surface) or by_surface.get("All")
    rows = (surface_metrics or {}).get("rows", [])
    vals = [row.get("tourPct") for row in rows if row.get("tourPct") is not None]
    if not vals:
        return None
    return round(sum(vals) / len(vals), 1)


def _quality_score(samples: dict, effective_samples: dict, surface: str) -> float | None:
    sample = (samples or {}).get(surface)
    effective = (effective_samples or {}).get(surface)
    if not sample:
        return None
    ratio = (effective or sample) / sample
    opponent_quality = _clamp(50 + (ratio - 1.0) * 90)
    target = 60 if surface == "All" else 28
    confidence = min(1.0, math.sqrt(sample / target)) * 100
    return round(opponent_quality * 0.55 + confidence * 0.45, 1)


def _composite_level(stat_pct: float | None, rank: int, elo: int | None, age: int,
                     samples: dict, effective_samples: dict,
                     performance_metrics: dict | None, surface: str) -> tuple[float | None, dict]:
    """Realistic circuit level: stats + strength anchor + opponent/sample context + form."""
    age_elo, elo_refs = _age_elo_strength_score(elo, age, surface)
    strength = _blend_available([
        (_rank_strength_score(rank), 0.40),
        (age_elo, 0.60),
    ])
    quality = _quality_score(samples, effective_samples, surface)
    form = _form_score(performance_metrics, surface)
    value = _blend_available([
        (stat_pct, 0.40),
        (strength, 0.35),
        (quality, 0.10),
        (form, 0.15),
    ])
    factors = {
        "statPct": round(stat_pct, 1) if stat_pct is not None else None,
        "strength": round(strength, 1) if strength is not None else None,
        "ageElo": age_elo,
        "eloRefs": elo_refs,
        "quality": quality,
        "form": form,
    }
    return (round(value, 1) if value is not None else None), factors


def _surface_sim_scores(stats_by_year: dict, benchmark: dict,
                        effective_counts: dict | None = None) -> tuple[dict, dict]:
    """Latest valid profile-sim score by surface."""
    raw = {}
    counts = effective_counts or _surface_match_counts(stats_by_year)
    for surf in ("All", "Hard", "Clay", "Grass"):
        raw[surf] = None
        for yr in sorted(stats_by_year.keys(), key=int, reverse=True):
            stats = (stats_by_year.get(yr) or {}).get(surf)
            if not stats:
                continue
            sim = sf.compute_profile_similarity(stats, benchmark, surf)
            if sim is not None:
                raw[surf] = sim
                break
    adjusted = {}
    for surf, score in raw.items():
        if score is None:
            adjusted[surf] = None
            continue
        if surf != "All" and counts.get(surf, 0) < 8:
            adjusted[surf] = None
            continue
        target = 40 if surf == "All" else 24
        adjusted[surf] = _confidence_adjusted_score(score, counts.get(surf, 0), target)
    return adjusted, raw


def _level_trend_by_surface(stats_by_year: dict, benchmark: dict) -> dict:
    """Year-by-year profile level by surface for the player detail trend chart."""
    result = {surf: [] for surf in ("All", "Hard", "Clay", "Grass")}
    for year in sorted(stats_by_year.keys(), key=int):
        for surf in result:
            stats = (stats_by_year.get(year) or {}).get(surf)
            if not stats:
                continue
            sim = sf.compute_profile_similarity(stats, benchmark, surf)
            matches = stats.get("matches") or 0
            if sim is None or matches < 3:
                continue
            target = 30 if surf == "All" else 16
            result[surf].append({
                "year": int(year),
                "value": _confidence_adjusted_score(sim, matches, target),
                "raw": round(sim, 1),
                "matches": matches,
            })
    return result


def _trend_only_batch(players: list, years_range, use_cache: bool = True) -> dict:
    """Compute lightweight career-wide stats for trend charts."""
    return sf.compute_all_players_batch(players, years=tuple(years_range), use_cache=use_cache)


def _level_weight(level: str) -> float:
    level = (level or "").strip().upper()
    if level == "G":
        return 1.35
    if level == "M":
        return 1.25
    if level == "A":
        return 1.0
    if level == "F":
        return 1.2
    if level == "D":
        return 0.55
    return 0.8


def _opponent_weight(rank) -> float:
    rank = _safe_int(rank, None)
    if rank is None:
        return 0.5
    if rank <= 10:
        return 1.35
    if rank <= 30:
        return 1.2
    if rank <= 100:
        return 1.0
    if rank <= 250:
        return 0.75
    return 0.45


def _match_weight(row: dict, as_winner: bool) -> float:
    opp_rank = row.get("loser_rank" if as_winner else "winner_rank")
    return round(_level_weight(row.get("tourney_level")) * _opponent_weight(opp_rank), 3)


def _effective_match_counts(players: list, years_range, use_cache: bool = True) -> dict:
    pid_set = {p["player_id"] for p in players}
    result = {pid: {surf: 0.0 for surf in ("All", "Hard", "Clay", "Grass")} for pid in pid_set}
    for year in years_range:
        for row in sf._fetch_csv(year, use_cache):
            surf = (row.get("surface") or "").strip()
            surface_keys = ("All", surf) if surf in ("Hard", "Clay", "Grass") else ("All",)
            wid = _safe_int(row.get("winner_id"), None)
            lid = _safe_int(row.get("loser_id"), None)
            if wid in pid_set:
                weight = _match_weight(row, True)
                for key in surface_keys:
                    result[wid][key] += weight
            if lid in pid_set:
                weight = _match_weight(row, False)
                for key in surface_keys:
                    result[lid][key] += weight
    return result


def _empty_live_record() -> dict:
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


def _add_live_match(record: dict, surface: str, won: bool) -> None:
    record["matches"] += 1
    record["wins" if won else "losses"] += 1
    if surface in record["bySurface"]:
        surf_record = record["bySurface"][surface]
        surf_record["matches"] += 1
        surf_record["wins" if won else "losses"] += 1


def _has_point_stats(row: dict) -> bool:
    return any(_safe_int(row.get(k), 0) > 0 for k in ("w_svpt", "l_svpt", "w_1stIn", "l_1stIn"))


def _is_walkover(row: dict) -> bool:
    return (row.get("score") or "").strip().upper() in {"W/O", "WALKOVER"}


def _merge_live_override(base: dict, override: dict) -> dict:
    """Shallow merge for optional API/manual facts without losing derived samples."""
    result = {**base, **override}
    for key in ("careerRecord", "seasonRecord", "statSamples"):
        if key in base or key in override:
            result[key] = {**base.get(key, {}), **override.get(key, {})}
            if "bySurface" in base.get(key, {}) or "bySurface" in override.get(key, {}):
                result[key]["bySurface"] = {
                    **base.get(key, {}).get("bySurface", {}),
                    **override.get(key, {}).get("bySurface", {}),
                }
    sources = []
    for source in base.get("sources", []) + override.get("sources", []):
        if source not in sources:
            sources.append(source)
    result["sources"] = sources
    return result


def _load_live_profile_overrides() -> dict:
    """Optional API/manual enrichment layer.

    data/player_live_profiles.json can override or enrich the derived Sackmann
    record. This is where a daily API fetcher can write ATP/RapidAPI facts.
    """
    path = DATA_DIR / "player_live_profiles.json"
    if not path.exists():
        return {}
    try:
        with open(path, encoding="utf-8") as fh:
            raw = json.load(fh)
    except (OSError, json.JSONDecodeError) as exc:
        print(f"  Warning: could not read player live profiles: {exc}")
        return {}
    players = raw.get("players", raw)
    return players if isinstance(players, dict) else {}


def _live_profiles_for_players(players: list, years_range, live_schedule: dict,
                               use_cache: bool = True) -> dict:
    """Mix official match records, stats samples, and schedule facts per player.

    The important distinction: official records count played ATP-level matches
    available in Sackmann (walkovers excluded), while stat samples count only
    rows with point stats usable for deeper metrics.
    """
    pid_set = {p["player_id"] for p in players}
    current_year = date.today().year
    profiles = {}
    for p in players:
        profiles[p["player_id"]] = {
            "asOf": date.today().isoformat(),
            "sources": ["Jeff Sackmann records", "Jeff Sackmann point stats"],
            "careerRecord": _empty_live_record(),
            "seasonRecord": _empty_live_record(),
            "statSamples": {"matches": 0, "bySurface": {"Hard": 0, "Clay": 0, "Grass": 0}},
            "latestMatchDate": None,
        }

    for year in years_range:
        for row in sf._fetch_csv(year, use_cache):
            if _is_walkover(row):
                continue
            wid = _safe_int(row.get("winner_id"), None)
            lid = _safe_int(row.get("loser_id"), None)
            surf = (row.get("surface") or "").strip()
            match_date = (row.get("tourney_date") or "").strip()
            for pid, won in ((wid, True), (lid, False)):
                if pid not in pid_set:
                    continue
                profile = profiles[pid]
                _add_live_match(profile["careerRecord"], surf, won)
                if int(year) == current_year:
                    _add_live_match(profile["seasonRecord"], surf, won)
                if _has_point_stats(row):
                    profile["statSamples"]["matches"] += 1
                    if surf in profile["statSamples"]["bySurface"]:
                        profile["statSamples"]["bySurface"][surf] += 1
                if match_date and (not profile["latestMatchDate"] or match_date > profile["latestMatchDate"]):
                    profile["latestMatchDate"] = match_date

    schedule_names = {
        (match.get(side) or "").strip()
        for day in live_schedule.get("days", [])
        for match in day.get("matches", [])
        for side in ("player1", "player2")
    }
    name_to_pid = {p["name"]: p["player_id"] for p in players}
    for name in schedule_names:
        pid = name_to_pid.get(name)
        if pid in profiles and "Daily schedule" not in profiles[pid]["sources"]:
            profiles[pid]["sources"].append("Daily schedule")

    overrides = _load_live_profile_overrides()
    for p in players:
        override = overrides.get(p["name"]) or overrides.get(str(p["player_id"]))
        if isinstance(override, dict):
            profiles[p["player_id"]] = _merge_live_override(profiles[p["player_id"]], override)
    return profiles


def _project(current_age, current_gs, player_stats, end_age=37):
    """
    Kernel-regression projection: expected GS depends on how similar this
    player's stats are to historical players with known career GS totals.

    Age penalty and GS-drought penalty are applied on top.
    player_stats: All-surface stats dict for the most recent year.
    """
    if current_age >= end_age:
        return {"c": int(current_gs), "m": int(current_gs), "a": int(current_gs)}
    years_left = max(0, end_age - current_age)

    # Kernel regression → expected CAREER GS total
    expected_total = sf.compute_expected_gs(
        player_stats, _ALL_STATS, _REGRESSION_TARGETS, current_age
    )
    remaining = max(0.0, expected_total - current_gs)

    # Fraction of career left (assume active 18 → end_age)
    career_remaining = years_left / max(1, end_age - 18)

    # Age factor: peak at ≤25, exponential decay after
    age_factor = 1.0 if current_age <= 25 else math.exp(-0.14 * (current_age - 25))

    # GS drought: no titles after 23 is a strong negative signal
    drought = 1.0 if (current_gs > 0 or current_age <= 23) else math.exp(-0.20 * (current_age - 23))

    expected_additional = remaining * career_remaining * age_factor * drought

    return {
        "c":              int(current_gs + round(min(remaining, expected_additional * 0.50))),
        "m":              int(current_gs + round(min(remaining, expected_additional * 0.75))),
        "a":              int(current_gs + round(min(remaining, expected_additional * 1.00))),
        "expected_total": round(expected_total, 1),
    }


def _curve(current_age, current_gs, target_gs, end_age=37):
    pts = _realistic_projection(current_age, current_gs, end_age, target_gs)
    return [{"x": p["age"], "y": p["gs"]} for p in pts]


def _pct(v):
    return round(v * 100, 1) if v is not None else None


def _safe_int(v, default=0):
    try:
        return int(v) if v not in (None, "", "nan") else default
    except (TypeError, ValueError):
        return default


def _blank_perf_acc():
    return {
        "matches": 0,
        "wins": 0,
        "svpt": 0, "serve_won": 0,
        "first_in": 0, "first_won": 0, "second_won": 0,
        "bp_saved": 0, "bp_faced": 0,
        "retpt": 0, "return_won": 0,
        "ret_bp_won": 0, "ret_bp_opp": 0,
        "sv_gms": 0, "breaks_lost": 0,
        "ret_gms": 0, "breaks_won": 0,
    }


def _add_perf_row(acc: dict, row: dict, as_winner: bool):
    pfx = "w" if as_winner else "l"
    opfx = "l" if as_winner else "w"

    svpt = _safe_int(row.get(f"{pfx}_svpt"))
    first_in = _safe_int(row.get(f"{pfx}_1stIn"))
    first_won = _safe_int(row.get(f"{pfx}_1stWon"))
    second_won = _safe_int(row.get(f"{pfx}_2ndWon"))
    serve_won = _safe_int(row.get(f"{pfx}_1stWon")) + _safe_int(row.get(f"{pfx}_2ndWon"))
    retpt = _safe_int(row.get(f"{opfx}_svpt"))
    opp_serve_won = _safe_int(row.get(f"{opfx}_1stWon")) + _safe_int(row.get(f"{opfx}_2ndWon"))

    sv_gms = _safe_int(row.get(f"{pfx}_SvGms"))
    bp_saved = _safe_int(row.get(f"{pfx}_bpSaved"))
    bp_faced = _safe_int(row.get(f"{pfx}_bpFaced"))
    breaks_lost = max(0, _safe_int(row.get(f"{pfx}_bpFaced")) - _safe_int(row.get(f"{pfx}_bpSaved")))
    ret_gms = _safe_int(row.get(f"{opfx}_SvGms"))
    ret_bp_saved_opp = _safe_int(row.get(f"{opfx}_bpSaved"))
    ret_bp_opp = _safe_int(row.get(f"{opfx}_bpFaced"))
    breaks_won = max(0, _safe_int(row.get(f"{opfx}_bpFaced")) - _safe_int(row.get(f"{opfx}_bpSaved")))

    if svpt:
        acc["svpt"] += svpt
        acc["serve_won"] += serve_won
        acc["first_in"] += first_in
        acc["first_won"] += first_won
        acc["second_won"] += second_won
        acc["bp_saved"] += bp_saved
        acc["bp_faced"] += bp_faced
    if retpt:
        acc["retpt"] += retpt
        acc["return_won"] += max(0, retpt - opp_serve_won)
        acc["ret_bp_won"] += max(0, ret_bp_opp - ret_bp_saved_opp)
        acc["ret_bp_opp"] += ret_bp_opp
    acc["sv_gms"] += sv_gms
    acc["breaks_lost"] += min(breaks_lost, sv_gms) if sv_gms else breaks_lost
    acc["ret_gms"] += ret_gms
    acc["breaks_won"] += min(breaks_won, ret_gms) if ret_gms else breaks_won
    acc["matches"] += 1
    if as_winner:
        acc["wins"] += 1


def _perf_rates(acc: dict):
    total_points = acc["svpt"] + acc["retpt"]
    if acc["matches"] < 3 or total_points < 80:
        return None

    def pct(num, den):
        return round(num / den * 100) if den else None

    service = pct(acc["serve_won"], acc["svpt"])
    ret = pct(acc["return_won"], acc["retpt"])
    total = pct(acc["serve_won"] + acc["return_won"], total_points)
    second_svpt = acc["svpt"] - acc["first_in"]

    return {
        "winRate": pct(acc["wins"], acc["matches"]),
        "totalPtsWon": total,
        "dominanceRatio": round((service / 100) / (1 - (ret / 100)), 2) if service is not None and ret is not None and ret < 100 else None,
        "servicePtsWon": service,
        "firstServePtsWon": pct(acc["first_won"], acc["first_in"]),
        "secondServePtsWon": pct(acc["second_won"], second_svpt),
        "breakPointsSaved": pct(acc["bp_saved"], acc["bp_faced"]),
        "returnPtsWon": ret,
        "breakPointsCreated": pct(acc["ret_bp_opp"], acc["ret_gms"]),
        "breakPointsConverted": pct(acc["ret_bp_won"], acc["ret_bp_opp"]),
        "holdPct": pct(acc["sv_gms"] - acc["breaks_lost"], acc["sv_gms"]),
        "breakPct": pct(acc["breaks_won"], acc["ret_gms"]),
    }


CHARTING_DIR = DATA_DIR / "external" / "match_charting"


def _safe_float(v, default=0.0):
    if v in (None, "", "-", "nan"):
        return default
    try:
        return float(str(v).rstrip("%"))
    except (TypeError, ValueError):
        return default


def _chart_acc():
    return {
        "serve_pts": 0, "unret": 0, "short_serve_won": 0,
        "return_pts": 0, "return_in_play": 0, "return_in_play_won": 0,
        "returnable": 0, "return_deep": 0, "return_very_deep": 0,
        "rally_pts": 0, "rally_shots": 0,
        "short_rally_pts": 0, "short_rally_won": 0,
        "medium_rally_pts": 0, "medium_rally_won": 0,
        "long_rally_pts": 0, "long_rally_won": 0,
        "very_long_rally_pts": 0, "very_long_rally_won": 0,
        "winners": 0, "unforced": 0,
        "fh_shots": 0, "fh_winners": 0, "fh_forced": 0, "fh_unforced": 0,
        "bh_shots": 0, "bh_winners": 0, "bh_forced": 0, "bh_unforced": 0,
        "net_pts": 0, "net_won": 0,
    }


def _add_chart_acc(dst: dict, src: dict):
    for key, value in src.items():
        dst[key] = dst.get(key, 0) + value


def _chart_rates(acc: dict) -> dict:
    def pct(num, den):
        return round(num / den * 100) if den else None

    fh_potency = None
    if acc["fh_shots"]:
        fh_potency = round(max(0, min(100, 50 + (acc["fh_winners"] + acc["fh_forced"] - acc["fh_unforced"]) / acc["fh_shots"] * 100)))
    bh_potency = None
    if acc["bh_shots"]:
        bh_potency = round(max(0, min(100, 50 + (acc["bh_winners"] + acc["bh_forced"] - acc["bh_unforced"]) / acc["bh_shots"] * 100)))

    rally_aggression = None
    if acc["rally_pts"]:
        rally_aggression = round((acc["winners"] - acc["unforced"]) / acc["rally_pts"] * 100)

    return {
        "unreturnedServe": pct(acc["unret"], acc["serve_pts"]),
        "shortServeWon": pct(acc["short_serve_won"], acc["serve_pts"]),
        "returnInPlay": pct(acc["return_in_play"], acc["return_pts"]),
        "returnInPlayWon": pct(acc["return_in_play_won"], acc["return_in_play"]),
        "returnDepthIndex": round((acc["return_deep"] + 2 * acc["return_very_deep"]) / acc["returnable"] * 100, 1) if acc["returnable"] else None,
        "averageRallyLength": round(acc["rally_shots"] / acc["rally_pts"], 1) if acc["rally_pts"] else None,
        "shortRallyWin": pct(acc["short_rally_won"], acc["short_rally_pts"]),
        "mediumRallyWin": pct(acc["medium_rally_won"], acc["medium_rally_pts"]),
        "longRallyWin": pct(acc["long_rally_won"], acc["long_rally_pts"]),
        "veryLongRallyWin": pct(acc["very_long_rally_won"], acc["very_long_rally_pts"]),
        "rallyAggression": rally_aggression,
        "forehandPotency": fh_potency,
        "backhandPotency": bh_potency,
        "forehandWinner": pct(acc["fh_winners"], acc["fh_shots"]),
        "backhandWinner": pct(acc["bh_winners"], acc["bh_shots"]),
        "netFrequency": pct(acc["net_pts"], acc["serve_pts"] + acc["return_pts"]),
        "netWin": pct(acc["net_won"], acc["net_pts"]),
    }


def _charting_metrics_for_players(players: list) -> dict:
    """Aggregate Tennis Abstract Match Charting stats by player and surface."""
    required = [
        "charting-m-matches.csv",
        "charting-m-stats-Overview.csv",
        "charting-m-stats-ServeBasics.csv",
        "charting-m-stats-ReturnOutcomes.csv",
        "charting-m-stats-ReturnDepth.csv",
        "charting-m-stats-Rally.csv",
        "charting-m-stats-NetPoints.csv",
        "charting-m-stats-ShotTypes.csv",
    ]
    if not all((CHARTING_DIR / name).exists() for name in required):
        return {}

    name_to_pid = {p["name"]: p["player_id"] for p in players}
    match_meta = {}
    with open(CHARTING_DIR / "charting-m-matches.csv", encoding="utf-8", newline="") as fh:
        for row in csv.DictReader(fh):
            surf = (row.get("Surface") or "").strip()
            if surf not in ("Hard", "Clay", "Grass"):
                continue
            match_meta[row["match_id"]] = {
                "surface": surf,
                "date": row.get("Date") or row["match_id"][:8],
            }

    per_match = {}

    def stat_for(player, match_id):
        pid = name_to_pid.get(player)
        meta = match_meta.get(match_id)
        if pid is None or meta is None:
            return None
        key = (pid, match_id)
        if key not in per_match:
            per_match[key] = {"date": meta["date"], "surface": meta["surface"], "acc": _chart_acc()}
        return per_match[key]["acc"]

    with open(CHARTING_DIR / "charting-m-stats-Overview.csv", encoding="utf-8", newline="") as fh:
        for row in csv.DictReader(fh):
            if row.get("set") != "Total":
                continue
            acc = stat_for(row.get("player"), row.get("match_id"))
            if not acc:
                continue
            acc["serve_pts"] += _safe_int(row.get("serve_pts"))
            acc["return_pts"] += _safe_int(row.get("return_pts"))
            acc["winners"] += _safe_int(row.get("winners"))
            acc["unforced"] += _safe_int(row.get("unforced"))

    with open(CHARTING_DIR / "charting-m-stats-ServeBasics.csv", encoding="utf-8", newline="") as fh:
        for row in csv.DictReader(fh):
            if row.get("row") != "Total":
                continue
            acc = stat_for(row.get("player"), row.get("match_id"))
            if not acc:
                continue
            acc["unret"] += _safe_int(row.get("unret"))
            acc["short_serve_won"] += _safe_int(row.get("pts_won_lte_3_shots"))

    with open(CHARTING_DIR / "charting-m-stats-ReturnOutcomes.csv", encoding="utf-8", newline="") as fh:
        for row in csv.DictReader(fh):
            if row.get("row") != "Total":
                continue
            acc = stat_for(row.get("player"), row.get("match_id"))
            if not acc:
                continue
            acc["return_in_play"] += _safe_int(row.get("in_play"))
            acc["return_in_play_won"] += _safe_int(row.get("in_play_won"))
            acc["rally_pts"] += _safe_int(row.get("pts"))
            acc["rally_shots"] += _safe_int(row.get("total_shots"))

    with open(CHARTING_DIR / "charting-m-stats-ReturnDepth.csv", encoding="utf-8", newline="") as fh:
        for row in csv.DictReader(fh):
            if row.get("row") != "Total":
                continue
            acc = stat_for(row.get("player"), row.get("match_id"))
            if not acc:
                continue
            acc["returnable"] += _safe_int(row.get("returnable"))
            acc["return_deep"] += _safe_int(row.get("deep"))
            acc["return_very_deep"] += _safe_int(row.get("very_deep"))

    rally_rows = {
        "1-3": ("short_rally_pts", "short_rally_won"),
        "4-6": ("medium_rally_pts", "medium_rally_won"),
        "7-9": ("long_rally_pts", "long_rally_won"),
        "10": ("very_long_rally_pts", "very_long_rally_won"),
    }
    with open(CHARTING_DIR / "charting-m-stats-Rally.csv", encoding="utf-8", newline="") as fh:
        for row in csv.DictReader(fh):
            bucket = rally_rows.get(row.get("row"))
            if not bucket:
                continue
            pts_key, won_key = bucket
            pts = _safe_int(row.get("pts"))
            server = row.get("server")
            returner = row.get("returner")
            s_acc = stat_for(server, row.get("match_id"))
            r_acc = stat_for(returner, row.get("match_id"))
            if s_acc:
                s_acc[pts_key] += pts
                s_acc[won_key] += _safe_int(row.get("pl1_won"))
            if r_acc:
                r_acc[pts_key] += pts
                r_acc[won_key] += _safe_int(row.get("pl2_won"))

    with open(CHARTING_DIR / "charting-m-stats-NetPoints.csv", encoding="utf-8", newline="") as fh:
        for row in csv.DictReader(fh):
            if row.get("row") != "NetPoints":
                continue
            acc = stat_for(row.get("player"), row.get("match_id"))
            if not acc:
                continue
            acc["net_pts"] += _safe_int(row.get("net_pts"))
            acc["net_won"] += _safe_int(row.get("pts_won"))

    with open(CHARTING_DIR / "charting-m-stats-ShotTypes.csv", encoding="utf-8", newline="") as fh:
        for row in csv.DictReader(fh):
            shot_row = row.get("row")
            if shot_row not in ("F", "B"):
                continue
            acc = stat_for(row.get("player"), row.get("match_id"))
            if not acc:
                continue
            prefix = "fh" if shot_row == "F" else "bh"
            acc[f"{prefix}_shots"] += _safe_int(row.get("shots"))
            acc[f"{prefix}_winners"] += _safe_int(row.get("winners"))
            acc[f"{prefix}_forced"] += _safe_int(row.get("induced_forced"))
            acc[f"{prefix}_unforced"] += _safe_int(row.get("unforced"))

    surfaces = ("All", "Hard", "Clay", "Grass")
    by_player = {p["player_id"]: {surf: [] for surf in surfaces} for p in players}
    for (pid, _), item in per_match.items():
        by_player[pid]["All"].append(item)
        by_player[pid][item["surface"]].append(item)

    result = {}
    for pid, surface_items in by_player.items():
        player_result = {}
        for surf, items in surface_items.items():
            if not items:
                continue
            career = _chart_acc()
            recent = _chart_acc()
            for item in items:
                _add_chart_acc(career, item["acc"])
            for item in sorted(items, key=lambda x: x["date"], reverse=True)[:10]:
                _add_chart_acc(recent, item["acc"])
            career_rates = _chart_rates(career)
            recent_rates = _chart_rates(recent)
            if any(v is not None for v in career_rates.values()) and any(v is not None for v in recent_rates.values()):
                player_result[surf] = {
                    "matches": len(items),
                    "recentMatches": min(10, len(items)),
                    "career": career_rates,
                    "recent": recent_rates,
                }
        if player_result:
            result[pid] = player_result
    return result


def _performance_metrics_for_players(players: list, years_range, use_cache: bool = True) -> dict:
    """Compute career vs latest-10-match performance metrics from Sackmann CSVs."""
    pid_set = {p["player_id"] for p in players}
    surfaces = ("All", "Hard", "Clay", "Grass")
    career = {pid: {surf: _blank_perf_acc() for surf in surfaces} for pid in pid_set}
    recent_rows = {pid: {surf: [] for surf in surfaces} for pid in pid_set}
    charting = _charting_metrics_for_players(players)

    for year in years_range:
        rows = sf._fetch_csv(year, use_cache)
        for row in rows:
            wid = _safe_int(row.get("winner_id"), None)
            lid = _safe_int(row.get("loser_id"), None)
            surf = (row.get("surface") or "").strip()
            surface_keys = ("All", surf) if surf in surfaces and surf != "All" else ("All",)
            date_key = (row.get("tourney_date") or "", _safe_int(row.get("match_num")))
            if wid in pid_set:
                for key in surface_keys:
                    _add_perf_row(career[wid][key], row, True)
                    recent_rows[wid][key].append((date_key, row, True))
            if lid in pid_set:
                for key in surface_keys:
                    _add_perf_row(career[lid][key], row, False)
                    recent_rows[lid][key].append((date_key, row, False))

    compact_labels = [
        ("servicePtsWon", "Service Pts Won"),
        ("returnPtsWon", "Return Pts Won"),
        ("holdPct", "Hold %"),
        ("breakPct", "Break %"),
        ("totalPtsWon", "Total Pts Won"),
    ]
    profile_groups = [
        ("Forma", [
            ("totalPtsWon", "Total Points Won %"),
            ("winRate", "Win rate últimos 10"),
            ("dominanceRatio", "Dominance Ratio"),
        ]),
        ("Saque", [
            ("servicePtsWon", "Service Points Won %"),
            ("firstServePtsWon", "1st Serve Points Won %"),
            ("secondServePtsWon", "2nd Serve Points Won %"),
            ("unreturnedServe", "Unreturned Serve %"),
            ("shortServeWon", "<=3 Shots Won %"),
            ("breakPointsSaved", "Break Points Saved %"),
        ]),
        ("Resto", [
            ("returnPtsWon", "Return Points Won %"),
            ("returnInPlay", "Return in Play %"),
            ("returnInPlayWon", "Return in Play Won %"),
            ("returnDepthIndex", "Return Depth Index"),
            ("breakPointsCreated", "Break Points Created %"),
            ("breakPointsConverted", "Break Points Converted %"),
        ]),
        ("Rally", [
            ("averageRallyLength", "Average Rally Length"),
            ("shortRallyWin", "1-3 Shot Win %"),
            ("mediumRallyWin", "4-6 Shot Win %"),
            ("longRallyWin", "7-9 Shot Win %"),
            ("veryLongRallyWin", "10+ Shot Win %"),
        ]),
        ("Armas / estilo", [
            ("rallyAggression", "Rally Aggression"),
            ("forehandPotency", "Forehand Potency / 100"),
            ("backhandPotency", "Backhand Potency / 100"),
            ("forehandWinner", "Forehand Winner %"),
            ("backhandWinner", "Backhand Winner %"),
            ("netFrequency", "Net Frequency"),
            ("netWin", "Net Win %"),
        ]),
    ]

    result = {}
    for pid in pid_set:
        by_surface = {}
        for surf in surfaces:
            recent_acc = _blank_perf_acc()
            latest_rows = sorted(recent_rows[pid][surf], key=lambda x: x[0], reverse=True)[:10]
            for _, row, as_winner in latest_rows:
                _add_perf_row(recent_acc, row, as_winner)

            actual = _perf_rates(recent_acc)
            baseline = _perf_rates(career[pid][surf])
            if not actual or not baseline:
                continue
            chart_surface = (charting.get(pid) or {}).get(surf)
            if chart_surface:
                actual = {**actual, **chart_surface.get("recent", {})}
                baseline = {**baseline, **chart_surface.get("career", {})}

            rows = []
            for key, label in compact_labels:
                a = actual.get(key)
                c = baseline.get(key)
                if a is None or c is None:
                    continue
                rows.append({"key": key, "label": label, "actual": a, "career": c, "diff": a - c})

            groups = []
            for group_name, stats in profile_groups:
                stat_rows = []
                for key, label in stats:
                    a = actual.get(key)
                    c = baseline.get(key)
                    stat_rows.append({
                        "key": key,
                        "label": label,
                        "actual": a,
                        "career": c,
                        "diff": (a - c) if a is not None and c is not None else None,
                        "available": a is not None and c is not None,
                    })
                groups.append({"name": group_name, "rows": stat_rows})

            if rows:
                by_surface[surf] = {
                    "surface": surf,
                    "matches": recent_acc["matches"],
                    "rows": rows,
                    "groups": groups,
                }

        if by_surface:
            result[pid] = by_surface
    return result


def _annotate_performance_context(players_data: list) -> None:
    """Attach current-tour percentile context to each available player metric."""
    import bisect

    surface_keys = ("All", "Hard", "Clay", "Grass")
    value_keys_by_surface = {surf: {} for surf in surface_keys}

    def iter_metric_rows(record: dict, surf: str):
        metrics = (record.get("performanceBySurface") or {}).get(surf)
        if not metrics:
            return
        for row in metrics.get("rows") or []:
            yield row
        for group in metrics.get("groups") or []:
            for row in group.get("rows") or []:
                yield row

    for surf in surface_keys:
        for record in players_data:
            seen = set()
            for row in iter_metric_rows(record, surf) or []:
                key = row.get("key")
                value = row.get("actual")
                if key in seen or value is None or not isinstance(value, (int, float)) or not math.isfinite(value):
                    continue
                seen.add(key)
                value_keys_by_surface[surf].setdefault(key, []).append(value)

    distributions = {
        surf: {key: sorted(values) for key, values in value_map.items() if len(values) >= 8}
        for surf, value_map in value_keys_by_surface.items()
    }

    for surf in surface_keys:
        for record in players_data:
            for row in iter_metric_rows(record, surf) or []:
                key = row.get("key")
                value = row.get("actual")
                values = distributions.get(surf, {}).get(key)
                if value is None or not values:
                    continue
                pct = bisect.bisect_right(values, value) / len(values) * 100
                row["tourPct"] = round(pct)
                row["tourSample"] = len(values)
                row["tourMedian"] = round(values[len(values) // 2], 1)


# ── Build player record ───────────────────────────────────────────────────────

def build_player_record(p: dict, stats_by_year: dict, benchmark: dict,
                         gs_wins: dict, elo_ratings: dict = None,
                         surface_elo_ratings: dict = None,
                         performance_metrics: dict = None,
                         next_matches: dict = None,
                         effective_counts: dict = None,
                         live_profile: dict = None,
                         trend_stats_by_year: dict = None) -> dict:
    pid  = p["player_id"]
    name = p["name"]
    born = p["born"]
    rank = p["rank"]

    # Elo rating (from full match history)
    elo_data = (elo_ratings or {}).get(pid)
    elo      = int(elo_data["elo"]) if elo_data else None
    elo_by_surface = {}
    for surface in ("All", "Hard", "Clay", "Grass"):
        surface_data = ((surface_elo_ratings or {}).get(surface) or {}).get(pid)
        if surface_data:
            elo_by_surface[surface] = int(surface_data["elo"])

    # Latest year stats (All surfaces) — fall back to most recent year with valid sim
    if not stats_by_year:
        return None
    latest_year = max(stats_by_year.keys(), key=int)
    latest_all  = stats_by_year[latest_year].get("All", {})
    sim = sf.compute_profile_similarity(latest_all, benchmark)
    if sim is None and len(stats_by_year) > 1:
        for yr in sorted(stats_by_year.keys(), key=int, reverse=True)[1:]:
            fb_all = stats_by_year[yr].get("All", {})
            fb_sim = sf.compute_profile_similarity(fb_all, benchmark)
            if fb_sim is not None:
                latest_year = yr
                latest_all  = fb_all
                sim = fb_sim
                break
    age = latest_all.get("age") or _age_at(born, int(latest_year))

    # GS wins — KNOWN_GS is authoritative; only fall back to CSV scan for others
    if name in KNOWN_GS:
        gs_total = KNOWN_GS[name]
        gs_by_year_raw = {}
    else:
        gs_total = 0
        gs_by_year_raw = gs_wins.get(pid, {})
        for yr, cnt in gs_by_year_raw.items():
            gs_total += cnt

    # GS trajectory
    if name in GS_TRAJECTORIES:
        traj_pts = [{"x": a, "y": g} for a, g in GS_TRAJECTORIES[name]]
        # Bridge gap: stats data may be for a newer year than the last trajectory point
        if traj_pts and age > traj_pts[-1]["x"]:
            traj_pts.append({"x": age, "y": gs_total})
    elif gs_by_year_raw:
        cumul = 0
        traj_pts = []
        for yr in sorted(gs_by_year_raw):
            cumul += gs_by_year_raw[yr]
            traj_pts.append({"x": _age_at(born, int(yr)), "y": cumul})
    else:
        traj_pts = [{"x": age, "y": gs_total}]

    # vs_top10: aggregate raw counts across all available years (avoids noise from partial/single years)
    agg_wins  = sum((yr_data.get("All") or {}).get("vs_top10_wins_n")  or 0 for yr_data in stats_by_year.values())
    agg_total = sum((yr_data.get("All") or {}).get("vs_top10_total_n") or 0 for yr_data in stats_by_year.values())
    vs_top10_agg = round(agg_wins / agg_total * 100, 1) if agg_total >= 5 else None

    # Projections via kernel regression
    proj = _project(age, gs_total, latest_all)
    proj_curves = {
        "c": _curve(age, gs_total, proj["c"]),
        "m": _curve(age, gs_total, proj["m"]),
        "a": _curve(age, gs_total, proj["a"]),
    }

    # Comparison vs legends at same age (exclude the player themselves)
    comparison = []
    for leg in BACKGROUND_LEGENDS:
        if leg == name:
            continue
        gs_leg = _gs_at_age(GS_TRAJECTORIES[leg], age)
        if gs_leg is not None:
            leg_sims = _LEGEND_SIM_BY_AGE.get(leg, {})
            leg_sim_at_age = leg_sims.get(age) or leg_sims.get(age - 1) or leg_sims.get(age + 1)
            comparison.append({
                "name":       leg,
                "gs":         gs_leg,
                "diff":       int(gs_leg - gs_total),
                "color":      LEGEND_COLORS.get(leg, "#aaa"),
                "simAtAge":   round(leg_sim_at_age, 1) if leg_sim_at_age else None,
            })
    comparison.sort(key=lambda x: x["gs"], reverse=True)

    # Benchmark at this age
    bench_at = (benchmark.get(str(age)) or benchmark.get(age) or {}).get("All", {})

    color = LEGEND_COLORS.get(name, _rank_color(rank))

    eg         = proj.get("expected_total")
    phrase     = _context_phrase(age, gs_total, eg, proj["c"], proj["a"]) if eg is not None else None
    capi       = _career_adj_score(sim, age, gs_total)
    near_term  = _near_term_score(sim, age, gs_total, rank)
    is_legend  = name in BACKGROUND_LEGENDS
    surface_samples = _surface_match_counts(stats_by_year)
    effective_samples = effective_counts or surface_samples
    sim_by_surface, raw_sim_by_surface = _surface_sim_scores(
        stats_by_year, benchmark, effective_samples
    )
    surface_samples = _surface_match_counts(stats_by_year)
    performance_by_surface = (performance_metrics or {}).get(pid, {})

    return {
        "name":       name,
        "rank":       rank,
        "age":        age,
        "gs":         gs_total,
        "sim":        sim,
        "capi":       capi,
        "nearTerm":   near_term,
        "elo":        elo,
        "eloBySurface": elo_by_surface,
        "isLegend":   is_legend,
        "color":      color,
        "trajectory": traj_pts,
        "proj":       proj,
        "curves":     proj_curves,
        "comparison": comparison,
        "expectedGs": eg,
        "phrase":     phrase,
        "gameStats": {
            "player": {
                **{k: _pct(latest_all.get(k)) for k in STAT_LABELS if k != "vs_top10_win_pct"},
                "vs_top10_win_pct": vs_top10_agg,
            },
            "benchmark": {k: _pct(bench_at.get(k)) for k in STAT_LABELS},
        },
        "performanceMetrics": performance_by_surface.get("All"),
        "performanceBySurface": performance_by_surface,
        "simBySurface": sim_by_surface,
        "rawSimBySurface": raw_sim_by_surface,
        "levelTrendBySurface": _level_trend_by_surface(trend_stats_by_year or stats_by_year, benchmark),
        "surfaceSamples": surface_samples,
        "effectiveSamples": {k: round(v, 1) for k, v in effective_samples.items()},
        "liveProfile": live_profile or {},
        "playerId": pid,
        "tourPctBySurface": {},
        "nextMatch": (next_matches or {}).get(name),
        "latestYear":   int(latest_year),
        "yearsOfData":  len(stats_by_year),
        "surfaceStats": _surface_stats(stats_by_year),
    }


def _rank_color(rank: int) -> str:
    if rank <= 10:  return "#119822"
    if rank <= 30:  return "#f59e0b"
    if rank <= 100: return "#00ABE7"
    return "#6b7280"


# ── HTML generation ───────────────────────────────────────────────────────────

def _recent_matches() -> list:
    """Read most recent notable ATP matches (GS/Masters/500/250/Davis)."""
    seen: set = set()
    matches = []
    cache_dir = DATA_DIR / "_csv_cache"
    for path in sorted(cache_dir.glob("atp_matches_*.csv"))[-3:]:
        try:
            with open(path, encoding="utf-8", newline="") as fh:
                for row in csv.DictReader(fh):
                    d = (row.get("tourney_date") or "").strip()
                    if not d:
                        continue
                    lvl = (row.get("tourney_level") or "").strip()
                    if lvl not in ("G", "M", "F", "A", "D"):
                        continue
                    key = d + row.get("tourney_id", "") + row.get("match_num", "")
                    if key in seen:
                        continue
                    seen.add(key)
                    matches.append({
                        "date":       d,
                        "tournament": row.get("tourney_name", "").title(),
                        "level":      lvl,
                        "round":      row.get("round", ""),
                        "winner":     row.get("winner_name", ""),
                        "loser":      row.get("loser_name", ""),
                        "score":      row.get("score", ""),
                    })
        except Exception:
            continue
    matches.sort(key=lambda x: x["date"], reverse=True)
    return matches[:200]


def _load_next_matches() -> dict:
    path = DATA_DIR / "live_next_matches.json"
    if not path.exists():
        return {}
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, json.JSONDecodeError) as exc:
        print(f"  Warning: could not read live next matches: {exc}")
        return {}


def _load_live_schedule() -> dict:
    path = DATA_DIR / "live_schedule.json"
    if not path.exists():
        return {"asOf": date.today().isoformat(), "days": []}
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, json.JSONDecodeError) as exc:
        print(f"  Warning: could not read live schedule: {exc}")
        return {"asOf": date.today().isoformat(), "days": []}


def _legend_elo_history(names: list[str], use_cache: bool = True) -> dict:
    lookup = _load_player_id_lookup(use_cache)
    ids = {
        lookup[name]["id"]: name
        for name in names
        if lookup.get(name, {}).get("id")
    }
    elo = {}
    counts = {}
    history = {name: {} for name in names}
    for path in sorted((DATA_DIR / "_csv_cache").glob("atp_matches_*.csv")):
        try:
            year = int(path.stem.split("_")[-1])
        except ValueError:
            continue
        try:
            with open(path, encoding="utf-8", newline="") as fh:
                for row in csv.DictReader(fh):
                    wid = _safe_int(row.get("winner_id"), None)
                    lid = _safe_int(row.get("loser_id"), None)
                    if wid is None or lid is None:
                        continue
                    k = ec.K_FACTORS.get((row.get("tourney_level") or "").strip(), ec.DEFAULT_K)
                    ra = elo.get(wid, ec.INITIAL_ELO)
                    rb = elo.get(lid, ec.INITIAL_ELO)
                    ea = 1.0 / (1.0 + 10 ** ((rb - ra) / 400.0))
                    elo[wid] = ra + k * (1.0 - ea)
                    elo[lid] = rb + k * (0.0 - (1.0 - ea))
                    counts[wid] = counts.get(wid, 0) + 1
                    counts[lid] = counts.get(lid, 0) + 1
        except OSError:
            continue

        for pid, name in ids.items():
            if counts.get(pid, 0) >= 20 and pid in elo:
                history[name][year] = round(elo[pid], 0)
    return history


def _legend_group(name: str) -> str:
    if name in {"Novak Djokovic", "Rafael Nadal", "Roger Federer"}:
        return "Big 3"
    if name in {"Pete Sampras", "Andre Agassi"}:
        return "Sampras/Agassi"
    return "Campeones 1-3 GS"


def _legend_elo_level_score(elo) -> float | None:
    if elo is None:
        return None
    # Historical peak Elo needs more headroom than current top-200 Elo.
    return round(_clamp(60 + (float(elo) - 1800) / 600 * 40), 1)


def _historical_level_point(stats: dict, benchmark: dict, valid_sims: list[float], elo,
                            surface: str = "All") -> dict | None:
    """Closed-year level used by the legend comparator.

    The current season gets injected separately from the active-player model so
    partial-year form can move without rewriting the historical curve.
    """
    sim = sf.compute_profile_similarity(stats or {}, benchmark, surface)
    if sim is None:
        return None
    stat_pct = None
    if valid_sims:
        stat_pct = round(bisect.bisect_right(valid_sims, sim) / len(valid_sims) * 100, 1)
    elo_score = _legend_elo_level_score(elo)
    volume_score = round(_clamp(math.sqrt(((stats or {}).get("matches") or 0) / 70) * 100), 1)
    level = _blend_available([
        (stat_pct, 0.50),
        (elo_score, 0.35),
        (volume_score, 0.15),
    ])
    if level is None:
        return None
    return {
        "level": round(level, 1),
        "statPct": stat_pct,
        "sim": round(sim, 1),
        "elo": int(elo) if elo is not None else None,
        "matches": (stats or {}).get("matches") or 0,
    }


def _attach_comparison_level_trends(players_data: list[dict], trend_stats: dict,
                                    benchmark: dict, valid_sims_by_surface: dict,
                                    use_cache: bool = True) -> None:
    """Attach one comparator series per active player.

    Every season uses the same historical comparator formula. The current year
    is still live/partial, but it does not switch scale to the active card
    formula, which keeps the curve coherent.
    """
    current_year = date.today().year
    lookup = _load_player_id_lookup(use_cache)
    elo_history = _legend_elo_history([p.get("name") for p in players_data if p.get("name")], use_cache)
    for record in players_data:
        name = record.get("name")
        pid = record.get("playerId")
        born = sf.PLAYERS.get(name, {}).get("born") or lookup.get(name, {}).get("born")
        by_year = trend_stats.get(pid) if pid is not None else None
        by_surface = {}
        for surf in ("All", "Hard", "Clay", "Grass"):
            series = []
            for year_key, surfaces in sorted((by_year or {}).items(), key=lambda item: int(item[0])):
                year = int(year_key)
                stats = (surfaces or {}).get(surf) or {}
                point = _historical_level_point(
                    stats,
                    benchmark,
                    valid_sims_by_surface.get(surf) or [],
                    (elo_history.get(name) or {}).get(year),
                    surf,
                )
                if not point:
                    continue
                age = stats.get("age") or (_age_at(born, year) if born else None)
                if age is None:
                    continue
                point.update({
                    "year": year,
                    "age": age,
                    "surface": surf,
                    "source": "liveHistorical" if year == current_year else "historical",
                    "current": year == current_year,
                })
                series.append(point)
            by_surface[surf] = series
        record["comparisonLevelTrend"] = by_surface.get("All") or []
        record["comparisonLevelTrendBySurface"] = by_surface


def _aggregate_legend_profile(group: dict, individual_legends: list[dict], active_top: list[dict]) -> dict | None:
    by_name = {legend["name"]: legend for legend in individual_legends}
    sources = [by_name[name] for name in group["players"] if name in by_name]
    if not sources:
        return None

    def aggregate_series(surface: str) -> list[dict]:
        by_age = {}
        for source in sources:
            source_series = (
                (source.get("yearlyBySurface") or {}).get(surface)
                if surface != "All"
                else source.get("yearly")
            ) or []
            for point in source_series:
                age = point.get("age")
                if age is None:
                    continue
                by_age.setdefault(age, []).append(point)

        yearly = []
        for age in sorted(by_age):
            points = by_age[age]
            levels = [p["level"] for p in points if p.get("level") is not None]
            if not levels:
                continue
            stat_pcts = [p.get("statPct") for p in points if p.get("statPct") is not None]
            sims = [p.get("sim") for p in points if p.get("sim") is not None]
            elos = [p.get("elo") for p in points if p.get("elo") is not None]
            matches = [p.get("matches") for p in points if p.get("matches") is not None]
            years = [p.get("year") for p in points if p.get("year") is not None]
            yearly.append({
                "year": min(years) if years else None,
                "age": age,
                "level": round(sum(levels) / len(levels), 1),
                "statPct": _avg(stat_pcts),
                "sim": _avg(sims),
                "elo": round(sum(elos) / len(elos)) if elos else None,
                "matches": round(sum(matches), 1) if matches else 0,
                "sample": len(levels),
                "surface": surface,
            })
        return yearly

    yearly = aggregate_series("All")
    yearly_by_surface = {
        surf: aggregate_series(surf)
        for surf in ("All", "Hard", "Clay", "Grass")
    }

    if not yearly:
        return None
    peak = max(yearly, key=lambda row: row["level"])
    latest = yearly[-1]
    ahead = sum(1 for p in active_top if p["level"] is not None and p["level"] > peak["level"])
    return {
        "name": group["name"],
        "button": group["button"],
        "group": "Arquetipo",
        "gs": group["gs"],
        "peak": peak,
        "latest": latest,
        "yearly": yearly,
        "yearlyBySurface": yearly_by_surface,
        "rankVsActive": ahead + 1,
        "type": "profile",
        "members": group["players"],
    }


def _avg(values: list) -> float | None:
    nums = [float(v) for v in values if v is not None]
    if not nums:
        return None
    return round(sum(nums) / len(nums), 1)


def _build_legend_comparison(all_historical_stats: dict, benchmark: dict,
                             players_data: list, valid_sims_by_surface: dict,
                             use_cache: bool = True) -> dict:
    lookup = _load_player_id_lookup(use_cache)
    elo_history = _legend_elo_history(LEGEND_COMPARISON_NAMES, use_cache)
    current_year = date.today().year
    active_by_name = {p.get("name"): p for p in players_data if p.get("name")}
    active_top = sorted(
        [
            {
                "name": p["name"],
                "rank": p.get("rank"),
                "age": p.get("age"),
                "level": (p.get("tourPctBySurface") or {}).get("All") or p.get("tourPct"),
            }
            for p in players_data
            if ((p.get("tourPctBySurface") or {}).get("All") or p.get("tourPct")) is not None
        ],
        key=lambda row: row["level"],
        reverse=True,
    )[:8]

    legends = []
    for name in LEGEND_COMPARISON_NAMES:
        born = sf.PLAYERS.get(name, {}).get("born") or lookup.get(name, {}).get("born")
        year_data = all_historical_stats.get(name, {})
        active_surface_series = (active_by_name.get(name) or {}).get("comparisonLevelTrendBySurface") or {}
        active_series = active_surface_series.get("All") or (active_by_name.get(name) or {}).get("comparisonLevelTrend") or []
        if active_series:
            yearly = [dict(point) for point in active_series]
            yearly_by_surface = {
                surf: [dict(point) for point in (active_surface_series.get(surf) or [])]
                for surf in ("All", "Hard", "Clay", "Grass")
            }
        else:
            yearly_by_surface = {}
            for surf in ("All", "Hard", "Clay", "Grass"):
                surface_yearly = []
                for year_key, surfaces in sorted(year_data.items(), key=lambda item: int(item[0])):
                    year = int(year_key)
                    if year >= current_year:
                        continue
                    stats = (surfaces or {}).get(surf) or {}
                    age = stats.get("age") or (_age_at(born, year) if born else None)
                    if age is None:
                        continue
                    point = _historical_level_point(
                        stats,
                        benchmark,
                        valid_sims_by_surface.get(surf) or [],
                        (elo_history.get(name) or {}).get(year),
                        surf,
                    )
                    if not point:
                        continue
                    point.update({"year": year, "age": age, "surface": surf, "source": "historical"})
                    surface_yearly.append({**point})
                yearly_by_surface[surf] = surface_yearly
            yearly = yearly_by_surface.get("All") or []
        if not yearly:
            continue
        peak = max(yearly, key=lambda row: row["level"])
        latest = yearly[-1]
        ahead = sum(1 for p in active_top if p["level"] is not None and p["level"] > peak["level"])
        legends.append({
            "name": name,
            "button": name.split(" ")[-1],
            "group": _legend_group(name),
            "gs": LEGEND_TOTAL_GS.get(name),
            "peak": peak,
            "latest": latest,
            "yearly": yearly,
            "yearlyBySurface": yearly_by_surface,
            "rankVsActive": ahead + 1,
            "type": "player",
        })
    legends.sort(key=lambda row: row["peak"]["level"], reverse=True)
    profiles = [
        profile for profile in (
            _aggregate_legend_profile(group, legends, active_top)
            for group in LEGEND_PROFILE_GROUPS
        )
        if profile
    ]
    return {
        "legends": profiles + legends,
        "activeTop": active_top,
    }


def _next_matches_from_schedule(live_schedule: dict) -> dict:
    result = {}
    for day in live_schedule.get("days", []):
        for idx, match in enumerate(day.get("matches", [])):
            match_ref = f"match-{day.get('date', 'tbd')}-{idx}"
            for side in ("player1", "player2"):
                player_name = (match.get(side) or "").strip()
                if not player_name or "/" in player_name or player_name.lower().startswith("atp "):
                    continue
                result.setdefault(player_name, {
                    "status": match.get("status", "scheduled"),
                    "tournament": match.get("tournament", "Torneo TBD"),
                    "round": match.get("round", "TBD"),
                    "opponent": match.get("player2") if side == "player1" else match.get("player1"),
                    "surface": match.get("surface", "TBD"),
                    "time": match.get("time", "TBD"),
                    "asOf": live_schedule.get("asOf"),
                    "matchRef": match_ref,
                })
    return result


def render_index(players_data: list, legend_datasets: list, recent_matches: list,
                 live_schedule: dict, legend_comparison: dict, source_count: int) -> str:
    players_json = json.dumps(players_data, ensure_ascii=False)
    live_schedule_json = json.dumps(live_schedule, ensure_ascii=False)
    legend_comparison_json = json.dumps(legend_comparison, ensure_ascii=False)

    return f"""<!DOCTYPE html>
<html class="light" lang="es">
<head>
  <meta charset="UTF-8"/>
  <meta name="viewport" content="width=device-width,initial-scale=1"/>
  <title>Legend Tracker · ATP Rankings</title>
  <link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&family=Lora:wght@400;600&family=JetBrains+Mono:wght@400&display=swap" rel="stylesheet"/>
  <style>
    :root {{
      --slate-dark: #141413;
      --ivory-light: #faf9f5;
      --ivory-medium: #f0eee6;
      --ivory-dark: #e8e6dc;
      --oat: #e3dacc;
      --cloud-medium: #b0aea5;
      --cloud-light: #d1cfc5;
      --cloud-dark: #87867f;
      --slate-light: #5e5d59;
      --clay: #d97757;
      --olive: #788c5d;
      --sky: #6a9bcc;
      --fig: #c46686;
    }}
    html {{ scroll-behavior: smooth; }}
    body {{ margin: 0; font-family: 'Inter', system-ui, sans-serif; color: var(--slate-dark); background: var(--ivory-light); }}
    * {{ box-sizing: border-box; }}
    ::-webkit-scrollbar {{ display: none; }}
    body {{ padding-bottom: 74px; }}
    .tabs-viewport {{
      width: 100%; overflow-x: auto; overflow-y: hidden; scroll-snap-type: x mandatory;
      overscroll-behavior-x: contain;
    }}
    .tabs-track {{ display: flex; width: 400%; align-items: flex-start; }}
    .tab-panel {{ width: 25%; min-width: 25%; scroll-snap-align: start; }}
    .tab-panel.app-shell {{ max-width: none; margin: 0; }}
    .tab-panel.app-shell > * {{ max-width: 1200px; margin-left: auto; margin-right: auto; }}
    .app-shell {{ max-width: 1200px; margin: 0 auto; padding: 0 24px 48px; }}
    .slider-nav {{
      position: fixed; left: 50%; bottom: 14px; transform: translateX(-50%); z-index: 60;
      display: grid; grid-template-columns: repeat(4, minmax(94px, 1fr)); gap: 8px;
      width: min(620px, calc(100vw - 24px)); padding: 8px;
      background: rgba(240, 238, 230, 0.92); border: 1px solid var(--slate-dark);
      backdrop-filter: blur(10px);
    }}
    .slider-nav-btn {{
      appearance: none; border: 1px solid var(--slate-dark); background: var(--ivory-light);
      color: var(--slate-dark); font: 12px 'JetBrains Mono', monospace; text-transform: uppercase;
      padding: 10px 8px; cursor: pointer;
    }}
    .slider-nav-btn.active {{ background: var(--slate-dark); color: var(--ivory-light); }}
    .topbar {{
      position: sticky; top: 0; z-index: 50; height: 68px;
      background: var(--ivory-medium); border-bottom: 1px solid var(--slate-dark);
      display: flex; align-items: center; justify-content: space-between;
      padding: 0 24px;
    }}
    .wordmark {{
      font-weight: 700; font-size: 16px; letter-spacing: 0; color: var(--slate-dark);
      text-transform: uppercase;
    }}
    .search-toggle {{
      background: var(--ivory-light); color: var(--slate-dark); border: 1px solid var(--slate-dark);
      border-radius: 0 0 8px 8px; cursor: pointer; width: 56px; height: 44px;
      display: inline-flex; align-items: center; justify-content: center;
    }}
    .header-search {{
      display: flex; align-items: stretch; justify-content: flex-end; max-width: min(460px, 58vw);
    }}
    .header-search .search-input {{
      width: 0; min-width: 0; padding-left: 0; padding-right: 0;
      border-right: 0; opacity: 0; pointer-events: none;
      transition: width 0.18s ease, padding 0.18s ease, opacity 0.12s ease;
    }}
    .header-search.open .search-input {{
      width: min(360px, 48vw); padding-left: 14px; padding-right: 14px;
      opacity: 1; pointer-events: auto;
    }}
    .header-search.open .search-toggle {{ border-radius: 0 0 8px 0; }}
    .hero {{
      display: grid; grid-template-columns: minmax(0, 1.4fr) minmax(260px, 0.65fr);
      gap: 48px; padding: 76px 0 32px; align-items: end;
    }}
    .hero h1 {{
      margin: 0; font-size: clamp(42px, 7vw, 82px); line-height: 1.03;
      letter-spacing: 0; font-weight: 700; color: var(--slate-dark);
    }}
    .hero u {{ text-decoration-thickness: 0.08em; text-underline-offset: 0.12em; }}
    .hero p {{ margin: 0; font-size: 18px; line-height: 1.4; color: var(--slate-dark); max-width: 360px; }}
    .toolbar {{
      background: var(--ivory-medium); border: 1px solid var(--slate-dark); border-radius: 24px;
      padding: 31px; margin-bottom: 16px;
    }}
    .search-input {{
      width:100%; background: var(--ivory-light); border:1px solid var(--slate-dark); border-radius:0;
      padding: 14px 16px; font-size: 15px; outline: none; font-family: inherit; color: var(--slate-dark);
    }}
    .search-input:focus {{ border-color: var(--clay); }}
    .toolbar-row {{ display:flex; align-items:center; gap:16px; }}
    .surface-row {{ margin-top: 12px; }}
    .sort-group {{ display:flex; gap:8px; overflow-x:auto; flex:1; }}
    .sort-btn {{
      font-size: 15px; font-weight: 500; padding: 12px 18px; border-radius: 0;
      border: 1px solid var(--slate-dark); color: var(--slate-dark); background: transparent;
      cursor: pointer; transition: all 0.15s; white-space: nowrap; flex-shrink: 0;
    }}
    .sort-btn.active {{ background: var(--slate-dark); color: var(--ivory-light); }}
    .sort-btn:hover:not(.active) {{ background: var(--oat); }}
    .player-count {{
      font-family: 'JetBrains Mono', monospace; font-size: 12px; color: var(--cloud-dark);
      text-transform: uppercase; flex-shrink: 0;
    }}
    .list-panel {{ background: var(--ivory-medium); border-radius: 8px; overflow: hidden; }}
    .player-row {{
      display: grid; grid-template-columns: 48px minmax(0, 1fr) auto; align-items: center; gap: 16px;
      padding: 18px 24px; cursor: pointer; transition: background 0.12s;
      border-bottom: 1px solid var(--cloud-light);
    }}
    .player-row:hover, .player-row.selected {{ background: var(--oat); }}
    .rank-cell {{
      font-family: 'JetBrains Mono', monospace; font-size: 13px; color: var(--cloud-dark);
      text-align: right; font-variant-numeric: tabular-nums;
    }}
    .player-name {{ font-weight: 600; font-size: 20px; color: var(--slate-dark); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }}
    .player-meta {{ font-family: 'JetBrains Mono', monospace; font-size: 12px; color: var(--cloud-dark); text-transform: uppercase; margin-top: 5px; }}
    .gs-mark {{ border-bottom: 2px solid var(--slate-dark); padding-bottom: 1px; margin-left: 8px; font-size: 13px; font-weight: 600; white-space: nowrap; }}
    .score-cell {{ text-align:right; min-width: 76px; }}
    .score-value {{ font-family: 'Lora', serif; font-weight: 600; font-size: 30px; line-height: 1; color: var(--slate-dark); }}
    .score-label {{ font-family: 'JetBrains Mono', monospace; font-size: 12px; color: var(--cloud-dark); text-transform: uppercase; margin-top: 4px; }}
    .player-page {{
      min-height: calc(100vh - 68px); display: grid; place-items: center;
      padding-top: 48px; padding-bottom: 48px;
    }}
    .player-card {{
      width: min(100%, 760px); background: var(--slate-dark); color: var(--ivory-light);
      border-radius: 24px; padding: clamp(28px, 5vw, 64px);
      display: grid; justify-items: center; text-align: center; gap: 24px;
    }}
    .player-card h2 {{
      margin: 0; font-family: 'Lora', serif; font-size: clamp(46px, 8vw, 92px);
      font-weight: 400; line-height: 1.05; letter-spacing: 0; color: var(--ivory-light);
    }}
    .player-age {{
      font-family: 'JetBrains Mono', monospace; color: var(--ivory-dark);
      font-size: 14px; text-transform: uppercase; margin-top: 8px;
    }}
    .live-profile-chip {{
      display: flex; flex-wrap: wrap; justify-content: center; gap: 8px; margin-top: 12px;
      font-family: 'JetBrains Mono', monospace; font-size: 11px; text-transform: uppercase;
      color: var(--ivory-dark);
    }}
    .live-profile-chip span {{
      border: 1px solid var(--slate-medium, #3d3d3a); padding: 6px 8px;
      background: rgba(250,249,245,0.05);
    }}
    .next-match-chip {{
      width: 100%; border: 1px solid var(--slate-medium, #3d3d3a); background: rgba(250,249,245,0.06);
      color: var(--ivory-light); padding: 14px 16px; text-align: left;
      display: grid; grid-template-columns: minmax(0, 1fr) auto; gap: 12px; align-items: center;
      font: inherit;
    }}
    button.next-match-chip {{ cursor: pointer; }}
    button.next-match-chip:hover {{ background: rgba(250,249,245,0.11); }}
    .next-match-kicker {{
      font-family: 'JetBrains Mono', monospace; color: var(--ivory-dark);
      font-size: 11px; text-transform: uppercase; margin-bottom: 5px;
    }}
    .next-match-main {{ font-weight: 700; font-size: 15px; line-height: 1.25; }}
    .next-match-meta {{
      font-family: 'JetBrains Mono', monospace; color: var(--ivory-dark);
      font-size: 11px; text-transform: uppercase; margin-top: 6px;
    }}
    .next-match-time {{
      border: 1px solid var(--ivory-dark); padding: 8px 10px; font-family: 'JetBrains Mono', monospace;
      font-size: 12px; text-transform: uppercase; white-space: nowrap;
    }}
    .tour-ring {{
      --pct: 0; width: clamp(190px, 34vw, 280px); aspect-ratio: 1; border-radius: 50%;
      background: conic-gradient(var(--ivory-light) calc(var(--pct) * 1%), var(--slate-medium, #3d3d3a) 0);
      display: grid; place-items: center; margin-top: 8px;
    }}
    .tour-ring-inner {{
      width: 76%; aspect-ratio: 1; border-radius: 50%; background: var(--slate-dark);
      display: grid; place-items: center;
    }}
    .tour-score {{ font-family: 'Lora', serif; font-size: clamp(48px, 8vw, 72px); line-height: 1; }}
    .tour-label {{
      font-family: 'JetBrains Mono', monospace; color: var(--ivory-dark);
      font-size: 13px; text-transform: uppercase; margin-top: 8px;
    }}
    .surface-switcher {{
      display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 8px;
      width: 100%; margin-top: -4px;
    }}
    .surface-btn {{
      border: 1px solid var(--ivory-dark); background: transparent; color: var(--ivory-light);
      min-height: 40px; padding: 9px 8px; font-family: 'JetBrains Mono', monospace;
      font-size: 11px; text-transform: uppercase; cursor: pointer;
      transition: background 0.15s, color 0.15s, border-color 0.15s;
    }}
    .surface-btn:hover {{ background: var(--slate-medium, #3d3d3a); }}
    .surface-btn.active {{ background: var(--ivory-light); color: var(--slate-dark); border-color: var(--ivory-light); }}
    .surface-btn:disabled {{ opacity: 0.42; cursor: not-allowed; }}
    .level-trend {{
      width: 100%; display: grid; gap: 10px; border-top: 1px solid var(--slate-medium, #3d3d3a);
      border-bottom: 1px solid var(--slate-medium, #3d3d3a); padding: 18px 0 16px;
    }}
    .level-trend-head {{ display: flex; align-items: baseline; justify-content: space-between; gap: 12px; }}
    .level-trend-title {{
      font-family: 'JetBrains Mono', monospace; color: var(--ivory-light);
      font-size: 12px; text-transform: uppercase;
    }}
    .level-trend-delta {{
      font-family: 'JetBrains Mono', monospace; font-size: 11px; text-transform: uppercase;
      color: var(--ivory-dark); text-align: right;
    }}
    .level-trend-delta.up {{ color: var(--olive); }}
    .level-trend-delta.down {{ color: var(--clay); }}
    .level-trend svg {{ width: 100%; height: 118px; display: block; overflow: visible; }}
    .trend-grid {{ stroke: rgba(250,249,245,0.14); stroke-width: 1; }}
    .trend-line {{ fill: none; stroke: var(--ivory-light); stroke-width: 3; stroke-linecap: round; stroke-linejoin: round; }}
    .trend-area {{ fill: rgba(250,249,245,0.08); }}
    .trend-dot {{ fill: var(--ivory-light); stroke: var(--slate-dark); stroke-width: 2; }}
    .trend-labels {{
      display: flex; justify-content: space-between; gap: 8px;
      font-family: 'JetBrains Mono', monospace; font-size: 10px; color: var(--ivory-dark); text-transform: uppercase;
    }}
    .performance-panel {{
      width: 100%; margin-top: 8px; border-top: 1px solid var(--slate-medium, #3d3d3a);
      padding-top: 24px; text-align: left;
    }}
    .performance-title {{
      font-family: 'JetBrains Mono', monospace; color: var(--ivory-dark);
      font-size: 13px; text-transform: uppercase; margin-bottom: 14px;
    }}
    .metric-table {{ display: grid; gap: 0; width: 100%; }}
    .metric-row {{
      display: grid; grid-template-columns: minmax(104px, 1fr) 72px 72px 76px;
      align-items: center; gap: 10px; padding: 12px 0;
      border-top: 1px solid var(--slate-medium, #3d3d3a);
    }}
    .metric-row:first-child {{ border-top: 0; }}
    .metric-head {{
      padding-top: 0; color: var(--ivory-dark); font-family: 'JetBrains Mono', monospace;
      font-size: 11px; text-transform: uppercase;
    }}
    .metric-name {{ color: var(--ivory-light); font-size: 15px; line-height: 1.25; }}
    .metric-context {{
      display: block; margin-top: 5px; font-family: 'JetBrains Mono', monospace;
      font-size: 10px; text-transform: uppercase; color: var(--ivory-dark);
    }}
    .metric-context.elite, .metric-actual.elite {{ color: var(--olive); }}
    .metric-context.good, .metric-actual.good {{ color: #d7e789; }}
    .metric-context.weak, .metric-actual.weak {{ color: #e19b71; }}
    .metric-context.low, .metric-actual.low {{ color: var(--clay); }}
    .metric-bar {{
      display: block; width: min(132px, 100%); height: 4px; margin-top: 7px;
      background: rgba(250,249,245,0.14); overflow: hidden;
    }}
    .metric-bar span {{ display: block; height: 100%; background: var(--ivory-dark); }}
    .metric-bar span.elite {{ background: var(--olive); }}
    .metric-bar span.good {{ background: #d7e789; }}
    .metric-bar span.weak {{ background: #e19b71; }}
    .metric-bar span.low {{ background: var(--clay); }}
    .metric-val {{
      font-family: 'JetBrains Mono', monospace; color: var(--ivory-light);
      font-size: 14px; font-variant-numeric: tabular-nums; text-align: right;
    }}
    .metric-diff {{ font-weight: 600; }}
    .metric-diff.up {{ color: var(--olive); }}
    .metric-diff.down {{ color: var(--clay); }}
    .metric-diff.flat {{ color: var(--ivory-dark); }}
    .identity-card {{
      width: 100%; margin-top: 8px; background: var(--ivory-medium); color: var(--slate-dark);
      border-radius: 16px; padding: clamp(18px, 4vw, 32px); text-align: left;
    }}
    .identity-card h3 {{
      margin: 0 0 8px; font-size: clamp(26px, 5vw, 44px); line-height: 1.05;
      letter-spacing: 0; font-weight: 700;
    }}
    .identity-intro {{ margin: 0 0 18px; color: var(--slate-light); font-size: 15px; line-height: 1.45; }}
    .tag-cloud {{ display: flex; flex-wrap: wrap; gap: 8px; margin-bottom: 20px; }}
    .auto-tag {{
      border: 1px solid var(--slate-dark); padding: 8px 10px; font-size: 12px;
      font-family: 'JetBrains Mono', monospace; text-transform: uppercase; background: var(--ivory-light);
    }}
    .auto-tag.good {{ border-color: var(--olive); color: var(--olive); }}
    .auto-tag.warn {{ border-color: var(--clay); color: var(--clay); }}
    .profile-groups {{ display: grid; gap: 10px; }}
    .profile-group {{ border: 1px solid var(--cloud-light); background: var(--ivory-light); }}
    .profile-summary {{
      list-style: none; cursor: pointer; padding: 14px 14px; display: flex; align-items: center;
      justify-content: space-between; gap: 12px; font-weight: 700; font-size: 16px;
    }}
    .profile-summary::-webkit-details-marker {{ display: none; }}
    .group-status {{ font-family: 'JetBrains Mono', monospace; font-size: 11px; color: var(--cloud-dark); text-transform: uppercase; font-weight: 400; }}
    .profile-stat-row {{
      display: grid; grid-template-columns: minmax(112px, 1fr) 58px 58px 62px 92px;
      gap: 8px; align-items: center; padding: 10px 14px; border-top: 1px solid var(--cloud-light);
    }}
    .profile-stat-head {{ color: var(--cloud-dark); font-family: 'JetBrains Mono', monospace; font-size: 10px; text-transform: uppercase; }}
    .profile-stat-name {{ font-size: 13px; line-height: 1.25; }}
    .profile-stat-val {{ font-family: 'JetBrains Mono', monospace; font-size: 12px; text-align: right; font-variant-numeric: tabular-nums; }}
    .profile-stat-label {{ font-family: 'JetBrains Mono', monospace; font-size: 10px; text-align: right; text-transform: uppercase; }}
    .profile-stat-label.up {{ color: var(--olive); }}
    .profile-stat-label.down {{ color: var(--clay); }}
    .profile-stat-label.flat, .profile-stat-label.na {{ color: var(--cloud-dark); }}
    .profile-stat-context {{
      display: block; margin-top: 6px; font-family: 'JetBrains Mono', monospace;
      font-size: 9px; color: var(--cloud-dark); text-transform: uppercase;
    }}
    .profile-stat-context.elite {{ color: var(--olive); }}
    .profile-stat-context.good {{ color: #6f8552; }}
    .profile-stat-context.weak {{ color: #b06945; }}
    .profile-stat-context.low {{ color: var(--clay); }}
    .profile-stat-bar {{
      display: block; width: min(118px, 100%); height: 4px; margin-top: 6px;
      background: var(--cloud-light); overflow: hidden;
    }}
    .profile-stat-bar span {{ display: block; height: 100%; background: var(--cloud-dark); }}
    .profile-stat-bar span.elite {{ background: var(--olive); }}
    .profile-stat-bar span.good {{ background: #8fa866; }}
    .profile-stat-bar span.weak {{ background: #d78c5f; }}
    .profile-stat-bar span.low {{ background: var(--clay); }}
    .schedule-page {{ padding-top: 48px; padding-bottom: 48px; }}
    .schedule-hero {{
      display: flex; justify-content: space-between; gap: 24px; align-items: end;
      padding: 24px 0 28px; border-bottom: 1px solid var(--slate-dark);
    }}
    .schedule-hero h2 {{
      margin: 0; font-family: 'Lora', serif; font-size: clamp(44px, 7vw, 84px);
      line-height: 1; font-weight: 400; letter-spacing: 0;
    }}
    .schedule-meta {{
      font-family: 'JetBrains Mono', monospace; font-size: 12px; color: var(--cloud-dark);
      text-transform: uppercase; text-align: right;
    }}
    .schedule-days {{ display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 16px; padding-top: 18px; }}
    .schedule-day {{ background: var(--ivory-medium); border: 1px solid var(--slate-dark); }}
    .schedule-day-head {{
      display: flex; justify-content: space-between; align-items: baseline; gap: 12px;
      padding: 16px 18px; border-bottom: 1px solid var(--slate-dark);
    }}
    .schedule-day-title {{ font-weight: 700; font-size: 20px; }}
    .schedule-date {{ font-family: 'JetBrains Mono', monospace; font-size: 11px; color: var(--cloud-dark); text-transform: uppercase; }}
    .match-list {{ display: grid; }}
    .match-card {{
      display: grid; border-top: 1px solid var(--cloud-light);
    }}
    .match-card:first-child {{ border-top: 0; }}
    .match-card summary {{
      display: grid; grid-template-columns: 58px minmax(0, 1fr) auto; gap: 12px;
      padding: 14px 18px; align-items: center; cursor: pointer; list-style: none;
    }}
    .match-card summary:hover {{ background: var(--oat); }}
    .match-card summary::-webkit-details-marker {{ display: none; }}
    .match-time {{ font-family: 'JetBrains Mono', monospace; font-size: 12px; color: var(--cloud-dark); text-transform: uppercase; }}
    .match-names {{ font-weight: 700; font-size: 15px; line-height: 1.25; }}
    .match-info {{ font-family: 'JetBrains Mono', monospace; font-size: 11px; color: var(--cloud-dark); text-transform: uppercase; margin-top: 5px; }}
    .match-glance {{
      display: flex; flex-wrap: wrap; gap: 6px; margin-top: 8px;
      font-family: 'JetBrains Mono', monospace; font-size: 10px; text-transform: uppercase;
    }}
    .match-glance-pill {{
      display: inline-flex; align-items: baseline; gap: 6px;
      border: 1px solid var(--cloud-light); background: rgba(250,249,245,0.58);
      padding: 4px 6px; color: var(--cloud-dark);
    }}
    .match-glance-score {{ color: var(--slate-dark); font-size: 13px; font-weight: 700; }}
    .match-level {{
      border: 1px solid var(--slate-dark); padding: 6px 8px; font-family: 'JetBrains Mono', monospace;
      font-size: 10px; text-transform: uppercase; white-space: nowrap;
    }}
    .match-compare {{
      grid-column: 1 / -1; display: grid; gap: 10px; padding-top: 12px; margin-top: 2px;
      border-top: 1px solid var(--cloud-light);
    }}
    .compare-ring-row {{ display: grid; grid-template-columns: 1fr auto 1fr; gap: 10px; align-items: center; }}
    .compare-player {{
      background: var(--ivory-light); border: 1px solid var(--cloud-light); padding: 10px;
    }}
    .compare-player-name {{ font-weight: 700; font-size: 13px; }}
    .compare-score {{ font-family: 'Lora', serif; font-size: 30px; line-height: 1; margin-top: 4px; }}
    .compare-vs {{ font-family: 'JetBrains Mono', monospace; font-size: 10px; color: var(--cloud-dark); text-transform: uppercase; }}
    .compare-table {{ display: grid; border: 1px solid var(--cloud-light); background: var(--ivory-light); }}
    .compare-row {{
      display: grid; grid-template-columns: 1fr 72px 72px; gap: 8px; align-items: center;
      padding: 9px 10px; border-top: 1px solid var(--cloud-light);
    }}
    .compare-row:first-child {{ border-top: 0; }}
    .compare-row.head {{ font-family: 'JetBrains Mono', monospace; font-size: 10px; color: var(--cloud-dark); text-transform: uppercase; }}
    .compare-stat {{ font-size: 12px; line-height: 1.25; }}
    .compare-val {{ font-family: 'JetBrains Mono', monospace; font-size: 12px; text-align: right; font-variant-numeric: tabular-nums; }}
    .compare-val.win {{ color: var(--olive); font-weight: 700; }}
    .empty-schedule {{ padding: 18px; color: var(--cloud-dark); font-size: 14px; }}
    .legends-page {{ padding-top: 48px; padding-bottom: 48px; }}
    .legends-hero {{
      display: grid; grid-template-columns: minmax(0, 1fr) auto; gap: 24px; align-items: end;
      padding: 24px 0 28px; border-bottom: 1px solid var(--slate-dark);
    }}
    .legends-hero h2 {{
      margin: 0; font-family: 'Lora', serif; font-size: clamp(44px, 7vw, 84px);
      line-height: 1; font-weight: 400; letter-spacing: 0;
    }}
    .legends-grid {{ display: grid; grid-template-columns: minmax(0, 1.3fr) minmax(260px, 0.7fr); gap: 16px; padding-top: 18px; }}
    .legend-card, .active-benchmark {{
      background: var(--ivory-medium); border: 1px solid var(--slate-dark);
    }}
    .legend-row {{
      display: grid; grid-template-columns: minmax(0, 1fr) 72px 76px;
      gap: 12px; align-items: center; padding: 16px 18px; border-top: 1px solid var(--cloud-light);
    }}
    .legend-row:first-child {{ border-top: 0; }}
    .legend-name {{ font-weight: 700; font-size: 18px; }}
    .legend-meta {{
      font-family: 'JetBrains Mono', monospace; font-size: 11px; color: var(--cloud-dark);
      text-transform: uppercase; margin-top: 5px;
    }}
    .legend-score {{ font-family: 'Lora', serif; font-size: 34px; text-align: right; line-height: 1; }}
    .legend-rank {{
      font-family: 'JetBrains Mono', monospace; font-size: 11px; text-align: right;
      color: var(--cloud-dark); text-transform: uppercase;
    }}
    .legend-spark {{ grid-column: 1 / -1; height: 54px; margin-top: 8px; }}
    .legend-spark svg {{ width: 100%; height: 54px; display: block; overflow: visible; }}
    .legend-spark-line {{ fill: none; stroke: var(--slate-dark); stroke-width: 2.5; stroke-linecap: round; stroke-linejoin: round; }}
    .legend-spark-area {{ fill: rgba(20,20,19,0.07); }}
    .legend-spark-dot {{ fill: var(--slate-dark); }}
    .active-benchmark h3 {{
      margin: 0; padding: 16px 18px; border-bottom: 1px solid var(--slate-dark);
      font-size: 20px;
    }}
    .active-benchmark-row {{
      display: grid; grid-template-columns: 28px minmax(0, 1fr) 44px;
      gap: 10px; padding: 12px 18px; border-top: 1px solid var(--cloud-light); align-items: center;
    }}
    .active-benchmark-row:first-of-type {{ border-top: 0; }}
    .active-benchmark-rank {{ font-family: 'JetBrains Mono', monospace; font-size: 11px; color: var(--cloud-dark); }}
    .active-benchmark-name {{ font-weight: 700; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }}
    .active-benchmark-score {{ font-family: 'Lora', serif; font-size: 24px; text-align: right; }}
    .legend-comparator {{
      grid-column: 1 / -1; background: var(--ivory-medium); border: 1px solid var(--slate-dark);
      padding: 18px 18px 22px; display: grid; gap: 18px;
    }}
    .legend-comparator-head {{
      display: flex; justify-content: space-between; align-items: baseline; gap: 16px; flex-wrap: wrap;
    }}
    .legend-comparator h3 {{ margin: 0; font-size: 22px; }}
    .legend-picker {{ display: flex; flex-wrap: wrap; gap: 8px; }}
    .legend-picker button {{
      border: 1px solid var(--slate-dark); background: transparent; color: var(--slate-dark);
      padding: 8px 10px; font-family: 'JetBrains Mono', monospace; font-size: 10px;
      text-transform: uppercase; cursor: pointer;
    }}
    .legend-picker button.active {{ background: var(--slate-dark); color: var(--ivory-light); }}
    .legend-compare-chart {{ width: 100%; height: 300px; }}
    .legend-compare-chart svg {{ width: 100%; height: 300px; display: block; overflow: visible; }}
    .legend-axis {{ stroke: var(--cloud-light); stroke-width: 1; }}
    .legend-band {{ fill: rgba(20,20,19,0.06); }}
    .legend-line {{ fill: none; stroke: var(--slate-dark); stroke-width: 4; stroke-linecap: round; stroke-linejoin: round; }}
    .player-line {{ fill: none; stroke: var(--clay); stroke-width: 4; stroke-linecap: round; stroke-linejoin: round; }}
    .legend-dot {{ fill: var(--slate-dark); }}
    .player-dot {{ fill: var(--clay); }}
    .legend-compare-legend {{
      display: grid; gap: 12px; font-family: 'JetBrains Mono', monospace;
      font-size: 18px; color: var(--cloud-dark); text-transform: uppercase;
    }}
    .legend-key {{ display: inline-flex; gap: 12px; align-items: center; }}
    .legend-key::before {{ content: ""; width: 36px; height: 4px; background: var(--slate-dark); display: inline-block; flex: 0 0 auto; }}
    .legend-key.player::before {{ background: var(--clay); }}
    .active-compare-controls {{
      display: flex; gap: 10px; flex-wrap: wrap; align-items: center;
    }}
    .active-compare-controls input {{
      border: 1px solid var(--slate-dark); background: var(--ivory-light); color: var(--slate-dark);
      padding: 9px 10px; font-family: 'JetBrains Mono', monospace; font-size: 11px;
      text-transform: uppercase; min-width: 210px;
    }}
    .active-compare-summary {{
      display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 10px;
    }}
    .active-compare-chip {{
      border: 1px solid var(--cloud-light); padding: 10px; background: rgba(255,255,255,0.32);
    }}
    .active-compare-chip b {{ display: block; font-size: 22px; line-height: 1; }}
    .active-compare-grid {{ display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 12px; }}
    .active-current-card {{ border: 1px solid var(--cloud-light); padding: 14px; background: rgba(255,255,255,0.32); }}
    .active-current-head {{ display: flex; justify-content: space-between; gap: 12px; align-items: baseline; margin-bottom: 12px; }}
    .active-current-name {{ font-weight: 800; font-size: 20px; }}
    .active-current-score {{ font-family: 'Lora', serif; font-size: 36px; line-height: 1; }}
    .active-current-row {{
      display: grid; grid-template-columns: 74px minmax(0, 1fr) 38px; gap: 8px; align-items: center;
      font-family: 'JetBrains Mono', monospace; font-size: 11px; text-transform: uppercase; margin-top: 9px;
    }}
    .active-current-bar {{ height: 8px; background: var(--cloud-light); position: relative; overflow: hidden; }}
    .active-current-fill {{ height: 100%; background: var(--slate-dark); }}
    .active-current-card.alt .active-current-fill {{ background: var(--clay); }}
    @media (max-width: 720px) {{
      .app-shell {{ padding: 0 12px 32px; }}
      .topbar {{ padding: 0 16px; }}
      .hero {{ grid-template-columns: 1fr; gap: 16px; padding: 40px 0 24px; }}
      .hero p {{ font-size: 16px; }}
      .toolbar {{ padding: 16px; border-radius: 16px; }}
      .toolbar-row {{ align-items: flex-start; flex-direction: column; }}
      .sort-group {{ width: 100%; }}
      .player-row {{ grid-template-columns: 36px minmax(0, 1fr) auto; padding: 16px; gap: 12px; }}
      .player-name {{ font-size: 16px; }}
      .score-value {{ font-size: 24px; }}
      .player-page {{ padding-top: 28px; }}
      .player-card {{ min-height: 70vh; justify-content: center; }}
      .next-match-chip {{ grid-template-columns: 1fr; }}
      .next-match-time {{ justify-self: start; }}
      .surface-switcher {{ gap: 6px; }}
      .surface-btn {{ font-size: 10px; padding: 8px 4px; }}
      .metric-row {{ grid-template-columns: minmax(86px, 1fr) 52px 58px 62px; gap: 8px; }}
      .metric-name {{ font-size: 13px; }}
      .metric-val {{ font-size: 12px; }}
      .identity-card {{ padding: 18px 14px; }}
      .profile-stat-row {{ grid-template-columns: minmax(88px, 1fr) 44px 48px 48px 58px; padding: 10px; gap: 6px; }}
      .profile-stat-name {{ font-size: 12px; }}
      .profile-stat-val {{ font-size: 11px; }}
      .profile-stat-label {{ font-size: 9px; }}
      .slider-nav {{ grid-template-columns: repeat(4, 1fr); gap: 5px; bottom: 8px; width: calc(100vw - 16px); padding: 6px; }}
      .slider-nav-btn {{ font-size: 9px; padding: 9px 3px; }}
      .schedule-page {{ padding-top: 28px; }}
      .schedule-hero {{ align-items: flex-start; flex-direction: column; }}
      .schedule-meta {{ text-align: left; }}
      .schedule-days {{ grid-template-columns: 1fr; }}
      .match-card summary {{ grid-template-columns: 46px minmax(0, 1fr); }}
      .match-level {{ grid-column: 2; justify-self: start; }}
      .compare-row {{ grid-template-columns: minmax(0, 1fr) 56px 56px; }}
      .compare-score {{ font-size: 24px; }}
      .legends-page {{ padding-top: 28px; }}
      .legends-hero {{ grid-template-columns: 1fr; }}
      .legends-grid {{ grid-template-columns: 1fr; }}
      .legend-row {{ grid-template-columns: minmax(0, 1fr) 58px 62px; padding: 14px; }}
      .legend-score {{ font-size: 28px; }}
      .legend-comparator {{ padding: 14px; }}
      .legend-compare-chart, .legend-compare-chart svg {{ height: 230px; }}
      .legend-compare-legend {{ font-size: 13px; gap: 8px; }}
      .legend-key::before {{ width: 26px; }}
      .active-compare-summary {{ grid-template-columns: repeat(2, minmax(0, 1fr)); }}
      .active-compare-grid {{ grid-template-columns: 1fr; }}
    }}
  </style>
</head>
<body>

  <!-- Header -->
  <header class="topbar">
    <div class="wordmark">Legend Tracker</div>
    <div id="header-search" class="header-search">
      <input id="search-input" class="search-input" type="search" placeholder="Buscar jugador..." oninput="refresh()"/>
      <button class="search-toggle" onclick="toggleSearch()" title="Buscar">
        <svg width="20" height="20" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2">
          <path stroke-linecap="round" stroke-linejoin="round" d="M21 21l-6-6m2-5a7 7 0 11-14 0 7 7 0 0114 0z"/>
        </svg>
      </button>
    </div>
  </header>

  <div id="tabs-viewport" class="tabs-viewport">
    <div class="tabs-track">
      <main id="ranking-tab" class="tab-panel app-shell">
        <section class="hero">
          <h1>ATP <u>ranking</u> and circuit <u>level</u></h1>
          <p>Una lectura editorial del circuito: ranking actual, nivel competitivo, edad y estado de forma en una sola lista compacta.</p>
        </section>

        <!-- Sort tabs + count bar -->
        <section class="toolbar">
          <div class="toolbar-row">
            <div class="sort-group">
              <button class="sort-btn active" id="sort-rank"  onclick="setSort('rank')">Ranking</button>
              <button class="sort-btn"        id="sort-tour"  onclick="setSort('tour')">Circuito</button>
              <button class="sort-btn"        id="sort-age"   onclick="setSort('age')">Edad</button>
            </div>
            <span id="player-count" class="player-count"></span>
          </div>
          <div class="toolbar-row surface-row">
            <div id="ranking-surface-group" class="sort-group"></div>
          </div>
        </section>

        <!-- Player list -->
        <div id="player-list" class="list-panel"></div>
      </main>

      <main id="player-tab" class="tab-panel app-shell player-page">
        <section id="player-detail" class="player-card"></section>
      </main>

      <main id="schedule-tab" class="tab-panel app-shell schedule-page">
        <section class="schedule-hero">
          <h2>Partidos</h2>
          <div class="schedule-meta">
            <div>Hoy y ma&ntilde;ana</div>
            <div id="schedule-asof"></div>
          </div>
        </section>
        <section id="schedule-days" class="schedule-days"></section>
      </main>

      <main id="legends-tab" class="tab-panel app-shell legends-page">
        <section class="legends-hero">
          <h2>Comparador</h2>
          <div class="schedule-meta">
            <div>Control de nivel</div>
            <div>pico historico vs activos</div>
          </div>
        </section>
        <section id="legends-view" class="legends-grid"></section>
      </main>
    </div>
  </div>

  <nav class="slider-nav" aria-label="Secciones">
    <button id="nav-ranking" class="slider-nav-btn active" type="button" onclick="goToTab('ranking-tab')">Ranking</button>
    <button id="nav-player" class="slider-nav-btn" type="button" onclick="goToTab('player-tab')">Jugador</button>
    <button id="nav-schedule" class="slider-nav-btn" type="button" onclick="goToTab('schedule-tab')">Partidos</button>
    <button id="nav-legends" class="slider-nav-btn" type="button" onclick="goToTab('legends-tab')">Comparador</button>
  </nav>

  <script>
const ALL_PLAYERS = {players_json};
const LIVE_SCHEDULE = {live_schedule_json};
const LEGEND_COMPARISON = {legend_comparison_json};
const RANKING_SOURCE_COUNT = {source_count};
const SURFACES = [
  {{ key: 'All', label: 'Global' }},
  {{ key: 'Hard', label: 'R&aacute;pida' }},
  {{ key: 'Clay', label: 'Tierra' }},
  {{ key: 'Grass', label: 'Hierba' }},
];

let sortKey = 'rank', sortDir = 1, activePlayer = null, activeSurface = 'All', rankingSurface = 'All', activeLegend = null;
let activeComparePlayerA = null, activeComparePlayerB = null, activeCompareMode = 'age', activeCompareSurface = 'All';
let activeCompareQueryA = '', activeCompareQueryB = '';

function getQ() {{
  return (document.getElementById('search-input')?.value || '').toLowerCase().trim();
}}

function sortPlayers(arr) {{
  return arr.slice().sort((a, b) => {{
    let va, vb;
    if (sortKey === 'rank')       {{ va = a.rank;          vb = b.rank; }}
    else if (sortKey === 'tour')  {{
      va = a.tourPctBySurface?.[rankingSurface] ?? (rankingSurface === 'All' ? a.tourPct : -1) ?? -1;
      vb = b.tourPctBySurface?.[rankingSurface] ?? (rankingSurface === 'All' ? b.tourPct : -1) ?? -1;
    }}
    else                          {{ va = a.age;            vb = b.age; }}
    return sortDir * (va - vb);
  }});
}}

function scoreColor(v) {{
  if (v == null) return '#87867f';
  if (v >= 70) return '#141413';
  if (v >= 45) return '#788c5d';
  return '#d97757';
}}

function getBadge(p) {{
  const v = p.tourPctBySurface?.[rankingSurface] ?? (rankingSurface === 'All' ? p.tourPct : null);
  const label = rankingSurface === 'All' ? 'Nivel' : surfaceLabel(rankingSurface);
  return {{ val: v != null ? v.toFixed(0) : '&#8212;', label, color: scoreColor(v) }};
}}

function renderRankingSurfaceButtons() {{
  const wrap = document.getElementById('ranking-surface-group');
  if (!wrap) return;
  wrap.innerHTML = SURFACES.map(s =>
    '<button class="sort-btn ' + (rankingSurface === s.key ? 'active' : '') + '" onclick="setRankingSurface(\\'' + s.key + '\\')">' + s.label + '</button>'
  ).join('');
}}

function buildList(players) {{
  const list = document.getElementById('player-list');
  const frag = document.createDocumentFragment();
  players.forEach(p => {{
    const row = document.createElement('div');
    row.className = 'player-row' + (p.name === activePlayer ? ' selected' : '');
    row.dataset.name = p.name;
    const b = getBadge(p);
    row.innerHTML =
      '<span class="rank-cell">' +
        (p.isLegend ? '&#9733;' : p.rank) +
      '</span>' +
      '<div style="min-width:0">' +
        '<div style="display:flex;align-items:baseline;gap:0;min-width:0">' +
          '<span class="player-name">' + p.name + '</span>' +
          (p.gs > 0 ? '<span class="gs-mark">' + p.gs + ' GS</span>' : '') +
        '</div>' +
        '<div class="player-meta">' + p.age + ' a&#241;os</div>' +
      '</div>' +
      '<div class="score-cell">' +
        '<div class="score-value" style="color:' + b.color + '">' + b.val + '</div>' +
        '<div class="score-label">' + b.label + '</div>' +
      '</div>';
    frag.appendChild(row);
  }});
  list.innerHTML = '';
  list.appendChild(frag);
  document.getElementById('player-count').textContent = players.length + ' con datos · top ' + RANKING_SOURCE_COUNT;
}}

function defaultPlayer() {{
  return ALL_PLAYERS.slice().sort((a, b) => a.rank - b.rank)[0] || null;
}}

function currentPlayer() {{
  return ALL_PLAYERS.find(p => p.name === activePlayer) || defaultPlayer();
}}

function fallbackPerformanceMetrics(p) {{
  const gs = p.gameStats?.player || {{}};
  const capiDelta = Math.max(-6, Math.min(6, Math.round(((p.nearTerm ?? p.capi ?? p.sim ?? 50) - (p.capi ?? p.sim ?? 50)) / 4)));
  const careerServe = Math.round(gs.serve_win_pct ?? 0);
  const careerReturn = Math.round(gs.return_win_pct ?? 0);
  const careerTotal = careerServe && careerReturn ? Math.round((careerServe + careerReturn) / 2) : null;
  const baseRows = [
    ['servicePtsWon', 'Service Pts Won', careerServe],
    ['returnPtsWon', 'Return Pts Won', careerReturn],
    ['holdPct', 'Hold %', careerServe ? Math.min(98, Math.round(careerServe + 16)) : null],
    ['breakPct', 'Break %', careerReturn ? Math.max(0, Math.round(careerReturn - 12)) : null],
    ['totalPtsWon', 'Total Pts Won', careerTotal],
  ];
  const rows = baseRows
    .filter(([, , career]) => career != null && Number.isFinite(career))
    .map(([key, label, career], idx) => {{
      const diff = capiDelta + (idx === 3 ? Math.sign(capiDelta || 1) : 0);
      return {{ key, label, career, actual: Math.max(0, Math.min(100, career + diff)), diff }};
    }});
  return rows.length ? {{ matches: 10, rows, estimated: true }} : null;
}}

function activeSurfaceLabel() {{
  return SURFACES.find(s => s.key === activeSurface)?.label || 'Global';
}}

function surfaceMetrics(p) {{
  return p.performanceBySurface?.[activeSurface] ||
    (activeSurface === 'All' ? (p.performanceMetrics || fallbackPerformanceMetrics(p)) : null);
}}

function surfaceTourPct(p) {{
  return p.tourPctBySurface?.[activeSurface] ?? (activeSurface === 'All' ? p.tourPct : null);
}}

function activeSurfaceSample(p) {{
  return p.surfaceSamples?.[activeSurface] ?? null;
}}

function activeEffectiveSample(p) {{
  return p.effectiveSamples?.[activeSurface] ?? null;
}}

function activeOfficialSample(p) {{
  const record = p.liveProfile?.careerRecord;
  if (!record) return null;
  if (activeSurface === 'All') return record.matches ?? null;
  return record.bySurface?.[activeSurface]?.matches ?? null;
}}

function activeDeepStatsSample(p) {{
  const samples = p.liveProfile?.statSamples;
  if (!samples) return activeSurfaceSample(p);
  if (activeSurface === 'All') return samples.matches ?? activeSurfaceSample(p);
  return samples.bySurface?.[activeSurface] ?? activeSurfaceSample(p);
}}

function renderSurfaceSwitcher(p) {{
  return '<div class="surface-switcher" role="tablist" aria-label="Superficie">' +
    SURFACES.map(s => {{
      const isActive = s.key === activeSurface;
      const hasPct = p.tourPctBySurface?.[s.key] != null || s.key === 'All';
      return '<button class="surface-btn ' + (isActive ? 'active' : '') + '" ' +
        'onclick="setPlayerSurface(\\'' + s.key + '\\')" ' +
        'aria-selected="' + (isActive ? 'true' : 'false') + '" ' +
        (hasPct ? '' : 'title="Sin muestra suficiente"') + '>' + s.label + '</button>';
    }}).join('') +
  '</div>';
}}

function renderNextMatch(p) {{
  const match = p.nextMatch;
  if (!match) {{
    return '<div class="next-match-chip">' +
      '<div>' +
        '<div class="next-match-kicker">Siguiente partido</div>' +
        '<div class="next-match-main">TBD</div>' +
        '<div class="next-match-meta">Sin partido confirmado</div>' +
      '</div>' +
      '<div class="next-match-time">TBD</div>' +
    '</div>';
  }}
  const opponent = match.opponent || 'TBD';
  const tournament = match.tournament || 'Torneo TBD';
  const round = match.round ? match.round + ' · ' : '';
  const surface = match.surface ? ' · ' + match.surface : '';
  const time = match.time || 'TBD';
  const tag = match.matchRef ? 'button' : 'div';
  const action = match.matchRef ? ' type="button" onclick="goToMatch(\\'' + match.matchRef + '\\')"' : '';
  return '<' + tag + ' class="next-match-chip"' + action + '>' +
    '<div>' +
      '<div class="next-match-kicker">Siguiente partido</div>' +
      '<div class="next-match-main">vs ' + opponent + '</div>' +
      '<div class="next-match-meta">' + round + tournament + surface + '</div>' +
    '</div>' +
    '<div class="next-match-time">' + time + '</div>' +
  '</' + tag + '>';
}}

function renderLiveProfile(p) {{
  const profile = p.liveProfile || {{}};
  const career = profile.careerRecord || {{}};
  const season = profile.seasonRecord || {{}};
  const statSample = profile.statSamples?.matches;
  const careerText = career.matches
    ? 'Record ATP disponible ' + (career.wins || 0) + '-' + (career.losses || 0) + ' · ' + career.matches + ' partidos'
    : 'Record ATP disponible TBD';
  const seasonText = season.matches
    ? 'Temporada ' + (season.wins || 0) + '-' + (season.losses || 0)
    : 'Temporada TBD';
  const sampleText = statSample != null ? 'Stats profundas ' + statSample : 'Stats profundas TBD';
  return '<div class="live-profile-chip">' +
    '<span>' + careerText + '</span>' +
    '<span>' + seasonText + '</span>' +
    '<span>' + sampleText + '</span>' +
  '</div>';
}}

function renderLevelTrend(p) {{
  const series = (p.levelTrendBySurface || {{}})[activeSurface] || [];
  const usable = series
    .filter(d => d && (d.pct != null || d.value != null))
    .map(d => ({{ ...d, displayValue: d.pct != null ? d.pct : d.value }}));
  if (!usable.length) {{
    return '<section class="level-trend">' +
      '<div class="level-trend-head">' +
        '<div class="level-trend-title">Evolución nivel · ' + activeSurfaceLabel() + '</div>' +
        '<div class="level-trend-delta">Sin histórico suficiente</div>' +
      '</div>' +
    '</section>';
  }}
  const latest = usable[usable.length - 1];
  const previous = usable.length > 1 ? usable[usable.length - 2] : null;
  const delta = previous ? latest.displayValue - previous.displayValue : null;
  const deltaClass = delta == null ? '' : (delta > 1 ? ' up' : (delta < -1 ? ' down' : ''));
  const latestLabel = latest.current ? 'Actual' : latest.year;
  const deltaText = delta == null
    ? latestLabel + ' · ' + latest.matches + ' partidos'
    : (delta >= 0 ? '+' : '') + delta.toFixed(1) + ' vs ' + previous.year + ' · ' + latest.matches + ' partidos';
  const w = 360, h = 118, padX = 16, padY = 14;
  const minY = Math.max(0, Math.min(...usable.map(d => d.displayValue)) - 8);
  const maxY = Math.min(100, Math.max(...usable.map(d => d.displayValue)) + 8);
  const spanY = Math.max(1, maxY - minY);
  const xFor = (idx) => usable.length === 1 ? w / 2 : padX + idx * ((w - padX * 2) / (usable.length - 1));
  const yFor = (v) => h - padY - ((v - minY) / spanY) * (h - padY * 2);
  const pts = usable.map((d, idx) => [xFor(idx), yFor(d.displayValue)]);
  const line = pts.map(p => p[0].toFixed(1) + ',' + p[1].toFixed(1)).join(' ');
  const area = usable.length > 1
    ? 'M ' + pts[0][0].toFixed(1) + ' ' + (h - padY) + ' L ' + pts.map(p => p[0].toFixed(1) + ' ' + p[1].toFixed(1)).join(' L ') + ' L ' + pts[pts.length - 1][0].toFixed(1) + ' ' + (h - padY) + ' Z'
    : '';
  const dots = pts.map((pt, idx) =>
    '<circle class="trend-dot" cx="' + pt[0].toFixed(1) + '" cy="' + pt[1].toFixed(1) + '" r="' + (idx === pts.length - 1 ? 4.5 : 3.5) + '"></circle>'
  ).join('');
  const peak = usable.reduce((best, d) => d.displayValue > best.displayValue ? d : best, usable[0]);
  const labelPoints = [usable[0], peak, latest].filter((d, idx, arr) =>
    d && arr.findIndex(x => x.year === d.year) === idx
  );
  const labels = labelPoints.map(d => '<span>' + (d.current ? 'Actual' : d.year) + ' · ' + d.displayValue.toFixed(0) + '</span>').join('');
  return '<section class="level-trend">' +
    '<div class="level-trend-head">' +
      '<div class="level-trend-title">Evolución nivel · ' + activeSurfaceLabel() + '</div>' +
      '<div class="level-trend-delta' + deltaClass + '">' + deltaText + '</div>' +
    '</div>' +
    '<svg viewBox="0 0 ' + w + ' ' + h + '" role="img" aria-label="Evolución de nivel">' +
      '<line class="trend-grid" x1="0" y1="' + yFor(50).toFixed(1) + '" x2="' + w + '" y2="' + yFor(50).toFixed(1) + '"></line>' +
      (area ? '<path class="trend-area" d="' + area + '"></path>' : '') +
      '<polyline class="trend-line" points="' + line + '"></polyline>' +
      dots +
    '</svg>' +
    '<div class="trend-labels">' + labels + '</div>' +
  '</section>';
}}

function renderPerformanceMetrics(p) {{
  const metrics = surfaceMetrics(p);
  if (!metrics?.rows?.length) {{
    return '<section class="performance-panel">' +
      '<div class="performance-title">' + activeSurfaceLabel() + ' · muestra insuficiente</div>' +
      '<div class="metric-table"><div class="metric-row"><div class="metric-name">Sin datos suficientes para esta superficie</div><div></div><div></div><div></div></div></div>' +
    '</section>';
  }}
  const rows = metrics.rows.map(r => {{
    const cls = r.diff > 0 ? 'up' : r.diff < 0 ? 'down' : 'flat';
    const sign = r.diff > 0 ? '+' : '';
    const mark = r.diff > 0 ? '&#9650;' : r.diff < 0 ? '&#9660;' : '&#8212;';
    const ctx = metricTourContext(r);
    return '<div class="metric-row">' +
      '<div class="metric-name">' + r.label +
        '<span class="metric-context ' + ctx.cls + '">' + ctx.label + '</span>' +
        '<span class="metric-bar" aria-hidden="true"><span class="' + ctx.cls + '" style="width:' + ctx.width + '%"></span></span>' +
      '</div>' +
      '<div class="metric-val">' + r.career + '%</div>' +
      '<div class="metric-val metric-actual ' + ctx.cls + '">' + r.actual + '%</div>' +
      '<div class="metric-val metric-diff ' + cls + '">' + mark + ' ' + sign + r.diff + '</div>' +
    '</div>';
  }}).join('');
  const source = (metrics.estimated ? 'Actual estimado' : '&#218;ltimos ' + (metrics.matches || 10)) + ' · ' + activeSurfaceLabel();
  return '<section class="performance-panel">' +
    '<div class="performance-title">' + source + ' vs carrera</div>' +
    '<div class="metric-table">' +
      '<div class="metric-row metric-head"><div>Stat</div><div class="metric-val">Carrera</div><div class="metric-val">Actual</div><div class="metric-val">Dif</div></div>' +
      rows +
    '</div>' +
  '</section>';
}}

function metricTourContext(row) {{
  if (row.tourPct == null || !Number.isFinite(row.tourPct)) {{
    return {{ cls: 'flat', label: 'Sin contexto ATP', width: 0 }};
  }}
  const pct = Math.max(0, Math.min(100, Math.round(row.tourPct)));
  let cls = 'flat';
  if (pct >= 85) cls = 'elite';
  else if (pct >= 65) cls = 'good';
  else if (pct <= 20) cls = 'low';
  else if (pct <= 40) cls = 'weak';
  return {{ cls, label: 'Mejor que ' + pct + '% ATP', width: pct }};
}}

function profileMetricMap(p) {{
  const metrics = surfaceMetrics(p);
  const map = {{}};
  (metrics?.groups || []).forEach(group => {{
    (group.rows || []).forEach(row => {{
      map[row.key] = row;
    }});
  }});
  return map;
}}

function profileThreshold(row) {{
  if (!row) return 2;
  if (row.key === 'dominanceRatio') return 0.05;
  if (row.key === 'averageRallyLength' || row.key === 'returnDepthIndex') return 0.25;
  return 2;
}}

function profileTrend(row) {{
  if (!row?.available || row.diff == null || !Number.isFinite(row.diff)) {{
    return {{ cls: 'na', label: 'Sin datos', mark: '&#8212;' }};
  }}
  const threshold = profileThreshold(row);
  if (row.diff >= threshold) return {{ cls: 'up', label: 'Mejora', mark: '&#9650;' }};
  if (row.diff <= -threshold) return {{ cls: 'down', label: 'Caida', mark: '&#9660;' }};
  return {{ cls: 'flat', label: 'Estable', mark: '&#8212;' }};
}}

function formatProfileValue(row, value) {{
  if (!row?.available || value == null || !Number.isFinite(value)) return '&#8212;';
  if (row.key === 'dominanceRatio') return value.toFixed(2);
  if (row.key === 'averageRallyLength' || row.key === 'returnDepthIndex') return value.toFixed(1);
  return Math.round(value) + '%';
}}

function formatProfileDiff(row) {{
  if (!row?.available || row.diff == null || !Number.isFinite(row.diff)) return '&#8212;';
  const sign = row.diff > 0 ? '+' : '';
  if (row.key === 'dominanceRatio') return sign + row.diff.toFixed(2);
  if (row.key === 'averageRallyLength' || row.key === 'returnDepthIndex') return sign + row.diff.toFixed(1);
  return sign + Math.round(row.diff);
}}

function automaticTags(p) {{
  const m = profileMetricMap(p);
  const has = key => m[key]?.available;
  const up = (key, by) => has(key) && m[key].diff >= (by ?? profileThreshold(m[key]));
  const down = (key, by) => has(key) && m[key].diff <= -(by ?? profileThreshold(m[key]));
  const high = (key, value) => has(key) && m[key].actual >= value;
  const tags = [];

  if (up('servicePtsWon') && (up('firstServePtsWon') || up('breakPointsSaved'))) {{
    tags.push({{ text: 'Saque dominante', cls: 'good' }});
  }} else if (down('servicePtsWon') && down('secondServePtsWon')) {{
    tags.push({{ text: 'Saque vulnerable', cls: 'warn' }});
  }} else if (has('servicePtsWon')) {{
    tags.push({{ text: 'Saque funcional', cls: 'neutral' }});
  }}

  if (up('returnPtsWon') && (up('breakPointsConverted') || up('breakPointsCreated'))) {{
    tags.push({{ text: 'Restador agresivo', cls: 'good' }});
  }}
  if (up('returnDepthIndex')) tags.push({{ text: 'Restador profundo', cls: 'good' }});
  if (up('returnInPlay') && down('rallyAggression')) tags.push({{ text: 'Restador conservador', cls: 'neutral' }});

  if (up('shortRallyWin', 4)) tags.push({{ text: 'Matador de puntos cortos', cls: 'good' }});
  if (up('mediumRallyWin') || up('longRallyWin')) tags.push({{ text: 'Jugador de rallies medios', cls: 'good' }});
  if (up('veryLongRallyWin')) tags.push({{ text: 'Maratoniano', cls: 'good' }});

  if (has('forehandPotency') && has('backhandPotency') && m.forehandPotency.actual >= m.backhandPotency.actual + 8) {{
    tags.push({{ text: 'Forehand-heavy', cls: 'good' }});
  }}
  if (up('backhandPotency')) tags.push({{ text: 'Backhand weapon', cls: 'good' }});
  if (high('netFrequency', 16) && high('netWin', 64)) tags.push({{ text: 'All-court player', cls: 'good' }});
  if ((high('servicePtsWon', 69) && high('totalPtsWon', 53)) || (high('servicePtsWon', 68) && up('totalPtsWon'))) {{
    tags.push({{ text: 'First-strike attacker', cls: 'good' }});
  }}
  if (high('returnPtsWon', 40) && up('returnPtsWon')) {{
    tags.push({{ text: 'Counterpuncher', cls: 'good' }});
  }}

  const unavailable = Object.values(m).filter(row => !row.available).length;
  if (unavailable) tags.push({{ text: 'Charting pendiente', cls: 'neutral' }});
  if (!tags.length) tags.push({{ text: 'Perfil estable', cls: 'neutral' }});
  return tags.slice(0, 6);
}}

function renderIdentityCard(p) {{
  const metrics = surfaceMetrics(p);
  if (!metrics?.groups?.length) return '';
  const tags = automaticTags(p).map(tag =>
    '<span class="auto-tag ' + tag.cls + '">' + tag.text + '</span>'
  ).join('');
  const groups = metrics.groups.map((group, idx) => {{
    const availableRows = (group.rows || []).filter(row => row.available);
    const improving = availableRows.filter(row => profileTrend(row).cls === 'up').length;
    const falling = availableRows.filter(row => profileTrend(row).cls === 'down').length;
    const status = availableRows.length
      ? improving + ' mejora / ' + falling + ' caida'
      : 'Datos pendientes';
    const rows = [
      '<div class="profile-stat-row profile-stat-head"><div>Stat</div><div class="profile-stat-val">Carr.</div><div class="profile-stat-val">Rec.</div><div class="profile-stat-val">Dif</div><div class="profile-stat-label">Estado</div></div>'
    ].concat((group.rows || []).map(row => {{
      const trend = profileTrend(row);
      const ctx = metricTourContext(row);
      return '<div class="profile-stat-row">' +
        '<div class="profile-stat-name">' + row.label +
          '<span class="profile-stat-context ' + ctx.cls + '">' + ctx.label + '</span>' +
          '<span class="profile-stat-bar" aria-hidden="true"><span class="' + ctx.cls + '" style="width:' + ctx.width + '%"></span></span>' +
        '</div>' +
        '<div class="profile-stat-val">' + formatProfileValue(row, row.career) + '</div>' +
        '<div class="profile-stat-val">' + formatProfileValue(row, row.actual) + '</div>' +
        '<div class="profile-stat-val">' + formatProfileDiff(row) + '</div>' +
        '<div class="profile-stat-label ' + trend.cls + '">' + trend.mark + ' ' + trend.label + '</div>' +
      '</div>';
    }})).join('');
    return '<details class="profile-group" ' + (idx < 2 ? 'open' : '') + '>' +
      '<summary class="profile-summary"><span>' + group.name + '</span><span class="group-status">' + status + '</span></summary>' +
      rows +
    '</details>';
  }}).join('');
  return '<section class="identity-card">' +
    '<h3>Perfil actual</h3>' +
    '<p class="identity-intro">Lectura de los ultimos 10 partidos en ' + activeSurfaceLabel().toLowerCase() + ' contra su media de carrera en esa superficie. Las metricas de punto a punto o charting quedan marcadas hasta importar esas fuentes.</p>' +
    '<div class="tag-cloud">' + tags + '</div>' +
    '<div class="profile-groups">' + groups + '</div>' +
  '</section>';
}}

function renderPlayerDetail() {{
  const p = currentPlayer();
  const el = document.getElementById('player-detail');
  if (!p || !el) return;
  const tourRaw = surfaceTourPct(p);
  const tour = tourRaw == null ? null : Math.max(0, Math.min(100, tourRaw));
  const sample = activeDeepStatsSample(p);
  const effectiveSample = activeEffectiveSample(p);
  const officialSample = activeOfficialSample(p);
  const sampleText = sample != null
    ? ' · oficial ' + (officialSample ?? 'TBD') + ' · stats ' + sample + (effectiveSample != null ? ' · peso ' + effectiveSample : '')
    : '';
  el.innerHTML =
    '<div>' +
      '<h2>' + p.name + '</h2>' +
      '<div class="player-age">' + p.age + ' a&#241;os</div>' +
      renderLiveProfile(p) +
    '</div>' +
    renderNextMatch(p) +
    '<div class="tour-ring" style="--pct:' + (tour == null ? '0' : tour.toFixed(1)) + '">' +
      '<div class="tour-ring-inner">' +
        '<div>' +
          '<div class="tour-score">' + (tour == null ? '&#8212;' : tour.toFixed(0)) + '</div>' +
          '<div class="tour-label">Nivel circuito · ' + activeSurfaceLabel() + sampleText + '</div>' +
        '</div>' +
      '</div>' +
    '</div>' +
    renderSurfaceSwitcher(p) +
    renderLevelTrend(p) +
    renderPerformanceMetrics(p) +
    renderIdentityCard(p);
}}

function renderSchedule() {{
  const wrap = document.getElementById('schedule-days');
  const asOf = document.getElementById('schedule-asof');
  if (!wrap) return;
  if (asOf) {{
    asOf.textContent = LIVE_SCHEDULE?.asOf ? 'Actualizado ' + LIVE_SCHEDULE.asOf : 'Actualizado TBD';
  }}
  const days = LIVE_SCHEDULE?.days || [];
  if (!days.length) {{
    wrap.innerHTML = '<div class="schedule-day"><div class="empty-schedule">Sin calendario disponible.</div></div>';
    return;
  }}
  wrap.innerHTML = days.map(day => {{
    const matches = day.matches || [];
    const body = matches.length
      ? '<div class="match-list">' + matches.map((match, idx) => {{
          const ref = 'match-' + (day.date || 'tbd') + '-' + idx;
          const p1 = findPlayerByMatchName(match.player1);
          const p2 = findPlayerByMatchName(match.player2);
          const names = schedulePlayerName(match.player1, p1) + ' <span style="color:var(--cloud-dark)">vs</span> ' + schedulePlayerName(match.player2, p2);
          const info = [match.round, match.tournament, match.surface].filter(Boolean).join(' · ');
          return '<details class="match-card" id="' + ref + '" data-match-ref="' + ref + '">' +
            '<summary>' +
              '<div class="match-time">' + (match.time || 'TBD') + '</div>' +
              '<div><div class="match-names">' + names + '</div><div class="match-info">' + info + '</div>' + renderMatchGlance(p1, p2) + '</div>' +
              '<div class="match-level">' + (match.level || 'ATP') + '</div>' +
            '</summary>' +
            renderMatchComparison(match) +
          '</details>';
        }}).join('') + '</div>'
      : '<div class="empty-schedule">Sin partidos masculinos individuales importantes.</div>';
    return '<section class="schedule-day">' +
      '<div class="schedule-day-head">' +
        '<div class="schedule-day-title">' + (day.label || 'Día') + '</div>' +
        '<div class="schedule-date">' + (day.date || '') + '</div>' +
      '</div>' +
      body +
    '</section>';
  }}).join('');
}}

function sparkline(points) {{
  const usable = (points || []).filter(p => p.level != null);
  if (!usable.length) return '';
  const w = 320, h = 54, pad = 5;
  const minY = Math.max(0, Math.min(...usable.map(p => p.level)) - 6);
  const maxY = Math.min(100, Math.max(...usable.map(p => p.level)) + 4);
  const spanY = Math.max(1, maxY - minY);
  const xFor = idx => usable.length === 1 ? w / 2 : pad + idx * ((w - pad * 2) / (usable.length - 1));
  const yFor = value => h - pad - ((value - minY) / spanY) * (h - pad * 2);
  const coords = usable.map((p, idx) => [xFor(idx), yFor(p.level)]);
  const line = coords.map(p => p[0].toFixed(1) + ',' + p[1].toFixed(1)).join(' ');
  const area = 'M ' + coords[0][0].toFixed(1) + ' ' + (h - pad) + ' L ' +
    coords.map(p => p[0].toFixed(1) + ' ' + p[1].toFixed(1)).join(' L ') +
    ' L ' + coords[coords.length - 1][0].toFixed(1) + ' ' + (h - pad) + ' Z';
  const peakIndex = usable.reduce((best, p, idx) => p.level > usable[best].level ? idx : best, 0);
  const peak = coords[peakIndex];
  return '<div class="legend-spark">' +
    '<svg viewBox="0 0 ' + w + ' ' + h + '" aria-hidden="true">' +
      '<path class="legend-spark-area" d="' + area + '"></path>' +
      '<polyline class="legend-spark-line" points="' + line + '"></polyline>' +
      '<circle class="legend-spark-dot" cx="' + peak[0].toFixed(1) + '" cy="' + peak[1].toFixed(1) + '" r="3.5"></circle>' +
    '</svg>' +
  '</div>';
}}

function surfaceLabel(surface) {{
  return (SURFACES.find(s => s.key === surface) || SURFACES[0]).label;
}}

function activePlayerLegendSeries(p, surface = 'All') {{
  const source = surface === 'All'
    ? (p?.comparisonLevelTrend || [])
    : ((p?.comparisonLevelTrendBySurface || {{}})[surface] || []);
  const comparisonTrend = source
    .filter(d => d && d.age != null && d.level != null)
    .map(d => ({{ age: d.age, level: d.level, year: d.year, current: !!d.current }}));
  if (comparisonTrend.length) {{
    return comparisonTrend.sort((a, b) => a.age - b.age);
  }}
  const byAge = new Map();
  ((p?.levelTrendBySurface || {{}})[surface] || [])
    .filter(d => d && (d.pct != null || d.value != null))
    .forEach(d => {{
      const age = d.year === 'Actual' ? p.age : (p.age - ((p.latestYear || new Date().getFullYear()) - d.year));
      const level = d.pct != null ? d.pct : d.value;
      if (!Number.isFinite(age) || !Number.isFinite(level)) return;
      const existing = byAge.get(age);
      if (!existing || d.current || (!existing.current && String(d.year) > String(existing.year))) {{
        byAge.set(age, {{ age, level, year: d.year, current: !!d.current }});
      }}
    }});
  return Array.from(byAge.values()).sort((a, b) => a.age - b.age);
}}

function samePlayerName(a, b) {{
  return String(a || '').trim().toLowerCase() === String(b || '').trim().toLowerCase();
}}

function compareLegendChart(legend, player) {{
  const legendSeries = (
    activeCompareSurface === 'All'
      ? (legend?.yearly || [])
      : ((legend?.yearlyBySurface || {{}})[activeCompareSurface] || [])
  ).filter(d => d.age != null && d.level != null);
  const comparesSamePlayer = legend?.type === 'player' && samePlayerName(legend?.name, player?.name);
  const playerSeries = comparesSamePlayer
    ? legendSeries.map(d => ({{ ...d, current: d.year === legend?.latest?.year }}))
    : activePlayerLegendSeries(player, activeCompareSurface);
  if (!legendSeries.length || !playerSeries.length) {{
    return '<div class="empty-schedule">Sin histórico suficiente para comparar.</div>';
  }}
  const all = legendSeries.concat(playerSeries);
  const w = 720, h = 300, left = 36, right = 18, top = 24, bottom = 42;
  const minAge = Math.min(...all.map(d => d.age));
  const maxAge = Math.max(...all.map(d => d.age));
  const minLevel = Math.max(0, Math.min(...all.map(d => d.level)) - 8);
  const maxLevel = Math.min(100, Math.max(...all.map(d => d.level)) + 4);
  const ageSpan = Math.max(1, maxAge - minAge);
  const levelSpan = Math.max(1, maxLevel - minLevel);
  const xFor = age => left + ((age - minAge) / ageSpan) * (w - left - right);
  const yFor = level => h - bottom - ((level - minLevel) / levelSpan) * (h - top - bottom);
  const lineFor = series => series.map(d => xFor(d.age).toFixed(1) + ',' + yFor(d.level).toFixed(1)).join(' ');
  const areaFor = series => {{
    const coords = series.map(d => [xFor(d.age), yFor(d.level)]);
    return 'M ' + coords[0][0].toFixed(1) + ' ' + (h - bottom) + ' L ' +
      coords.map(p => p[0].toFixed(1) + ' ' + p[1].toFixed(1)).join(' L ') +
      ' L ' + coords[coords.length - 1][0].toFixed(1) + ' ' + (h - bottom) + ' Z';
  }};
  const dotsFor = (series, cls) => series.map(d =>
    '<circle class="' + cls + '" cx="' + xFor(d.age).toFixed(1) + '" cy="' + yFor(d.level).toFixed(1) + '" r="' + (d.current ? 4.5 : 3) + '"></circle>'
  ).join('');
  const legendPeak = legendSeries.reduce((best, d) => d.level > best.level ? d : best, legendSeries[0]);
  const playerLatest = playerSeries[playerSeries.length - 1];
  const playerName = player?.name || 'Jugador';
  return '<div class="legend-compare-chart">' +
    '<svg viewBox="0 0 ' + w + ' ' + h + '" role="img" aria-label="Comparador de nivel por edad">' +
      '<path class="legend-band" d="' + areaFor(legendSeries) + '"></path>' +
      '<line class="legend-axis" x1="' + left + '" y1="' + yFor(50).toFixed(1) + '" x2="' + (w - right) + '" y2="' + yFor(50).toFixed(1) + '"></line>' +
      '<line class="legend-axis" x1="' + left + '" y1="' + (h - bottom) + '" x2="' + (w - right) + '" y2="' + (h - bottom) + '"></line>' +
      '<polyline class="legend-line" points="' + lineFor(legendSeries) + '"></polyline>' +
      '<polyline class="player-line" points="' + lineFor(playerSeries) + '"></polyline>' +
      dotsFor(legendSeries, 'legend-dot') +
      dotsFor(playerSeries, 'player-dot') +
      '<circle class="legend-dot" cx="' + xFor(legendPeak.age).toFixed(1) + '" cy="' + yFor(legendPeak.level).toFixed(1) + '" r="6"></circle>' +
      '<circle class="player-dot" cx="' + xFor(playerLatest.age).toFixed(1) + '" cy="' + yFor(playerLatest.level).toFixed(1) + '" r="6"></circle>' +
      '<text x="' + left + '" y="' + (h - 12) + '" font-size="11" fill="#87867f" font-family="JetBrains Mono">edad ' + minAge + '</text>' +
      '<text x="' + (w - right) + '" y="' + (h - 12) + '" text-anchor="end" font-size="11" fill="#87867f" font-family="JetBrains Mono">edad ' + maxAge + '</text>' +
      '<text x="' + (xFor(legendPeak.age) + 8).toFixed(1) + '" y="' + (yFor(legendPeak.level) - 10).toFixed(1) + '" font-size="12" fill="#141413" font-family="JetBrains Mono">' + Math.round(legendPeak.level) + '</text>' +
      '<text x="' + (xFor(playerLatest.age) + 8).toFixed(1) + '" y="' + (yFor(playerLatest.level) + 18).toFixed(1) + '" font-size="12" fill="#d97757" font-family="JetBrains Mono">' + Math.round(playerLatest.level) + '</text>' +
    '</svg>' +
  '</div>' +
  '<div class="legend-compare-legend">' +
    '<span class="legend-key">' + legend.name + ' · pico ' + Math.round(legendPeak.level) + ' a los ' + legendPeak.age + '</span>' +
    '<span class="legend-key player">' + playerName + ' · actual ' + Math.round(playerLatest.level) + ' a los ' + playerLatest.age + '</span>' +
  '</div>';
}}

function playerByName(name) {{
  return ALL_PLAYERS.find(p => p.name === name) || null;
}}

function playerSearchOptions(excludeName) {{
  return ALL_PLAYERS
    .slice()
    .sort((a, b) => a.rank - b.rank)
    .filter(p => p.name !== excludeName)
    .map(p => '<option value="' + p.name.replace(/"/g, '&quot;') + '">ATP ' + p.rank + '</option>')
    .join('');
}}

function findPlayerFromQuery(query, excludeName) {{
  const q = normaliseName(query);
  if (!q) return null;
  const pool = ALL_PLAYERS.filter(p => p.name !== excludeName);
  return pool.find(p => normaliseName(p.name) === q)
    || pool.find(p => normaliseName(p.name).includes(q))
    || null;
}}

function defaultComparePlayer(excludeName) {{
  return ALL_PLAYERS
    .slice()
    .sort((a, b) => a.rank - b.rank)
    .find(p => p.name !== excludeName) || null;
}}

function currentComparePlayers() {{
  if (!activeComparePlayerA) activeComparePlayerA = (currentPlayer() || defaultPlayer())?.name || null;
  let playerA = playerByName(activeComparePlayerA) || defaultPlayer();
  if (!activeComparePlayerB || activeComparePlayerB === playerA?.name) {{
    activeComparePlayerB = defaultComparePlayer(playerA?.name)?.name || null;
  }}
  let playerB = playerByName(activeComparePlayerB);
  if (playerA && playerB && playerA.name === playerB.name) {{
    playerB = defaultComparePlayer(playerA.name);
    activeComparePlayerB = playerB?.name || null;
  }}
  return [playerA, playerB];
}}

function nearestAgePoint(series, age) {{
  if (!series.length || age == null) return null;
  return series.reduce((best, point) =>
    Math.abs(point.age - age) < Math.abs(best.age - age) ? point : best, series[0]
  );
}}

function renderActiveAgeComparison(playerA, playerB) {{
  const seriesA = activePlayerLegendSeries(playerA, activeCompareSurface);
  const seriesB = activePlayerLegendSeries(playerB, activeCompareSurface);
  if (!seriesA.length || !seriesB.length) {{
    return '<div class="empty-schedule">Sin histórico suficiente para comparar activos en ' + surfaceLabel(activeCompareSurface) + '.</div>';
  }}
  const all = seriesA.concat(seriesB);
  const w = 720, h = 300, left = 36, right = 18, top = 24, bottom = 42;
  const minAge = Math.min(...all.map(d => d.age));
  const maxAge = Math.max(...all.map(d => d.age));
  const minLevel = Math.max(0, Math.min(...all.map(d => d.level)) - 8);
  const maxLevel = Math.min(100, Math.max(...all.map(d => d.level)) + 4);
  const ageSpan = Math.max(1, maxAge - minAge);
  const levelSpan = Math.max(1, maxLevel - minLevel);
  const xFor = age => left + ((age - minAge) / ageSpan) * (w - left - right);
  const yFor = level => h - bottom - ((level - minLevel) / levelSpan) * (h - top - bottom);
  const lineFor = series => series.map(d => xFor(d.age).toFixed(1) + ',' + yFor(d.level).toFixed(1)).join(' ');
  const dotsFor = (series, cls) => series.map(d =>
    '<circle class="' + cls + '" cx="' + xFor(d.age).toFixed(1) + '" cy="' + yFor(d.level).toFixed(1) + '" r="' + (d.current ? 4.5 : 3) + '"></circle>'
  ).join('');
  const latestA = seriesA[seriesA.length - 1];
  const latestB = seriesB[seriesB.length - 1];
  const sameAgeA = nearestAgePoint(seriesA, playerB.age);
  const sameAgeB = nearestAgePoint(seriesB, playerA.age);
  const peakA = seriesA.reduce((best, d) => d.level > best.level ? d : best, seriesA[0]);
  const peakB = seriesB.reduce((best, d) => d.level > best.level ? d : best, seriesB[0]);
  const chips = [
    [playerA.name + ' ahora', latestA],
    [playerB.name + ' ahora', latestB],
    [playerA.name + ' a los ' + playerB.age, sameAgeA],
    [playerB.name + ' a los ' + playerA.age, sameAgeB],
  ].map(([label, point]) =>
    '<div class="active-compare-chip"><div class="legend-meta">' + label + '</div><b>' + (point ? Math.round(point.level) : '&#8212;') + '</b></div>'
  ).join('');
  return '<div class="legend-compare-chart">' +
    '<svg viewBox="0 0 ' + w + ' ' + h + '" role="img" aria-label="Comparador de activos por edad">' +
      '<line class="legend-axis" x1="' + left + '" y1="' + yFor(50).toFixed(1) + '" x2="' + (w - right) + '" y2="' + yFor(50).toFixed(1) + '"></line>' +
      '<line class="legend-axis" x1="' + left + '" y1="' + (h - bottom) + '" x2="' + (w - right) + '" y2="' + (h - bottom) + '"></line>' +
      '<polyline class="legend-line" points="' + lineFor(seriesA) + '"></polyline>' +
      '<polyline class="player-line" points="' + lineFor(seriesB) + '"></polyline>' +
      dotsFor(seriesA, 'legend-dot') +
      dotsFor(seriesB, 'player-dot') +
      '<circle class="legend-dot" cx="' + xFor(peakA.age).toFixed(1) + '" cy="' + yFor(peakA.level).toFixed(1) + '" r="6"></circle>' +
      '<circle class="player-dot" cx="' + xFor(peakB.age).toFixed(1) + '" cy="' + yFor(peakB.level).toFixed(1) + '" r="6"></circle>' +
      '<text x="' + left + '" y="' + (h - 12) + '" font-size="11" fill="#87867f" font-family="JetBrains Mono">edad ' + minAge + '</text>' +
      '<text x="' + (w - right) + '" y="' + (h - 12) + '" text-anchor="end" font-size="11" fill="#87867f" font-family="JetBrains Mono">edad ' + maxAge + '</text>' +
    '</svg>' +
  '</div>' +
  '<div class="active-compare-summary">' + chips + '</div>' +
  '<div class="legend-compare-legend">' +
    '<span class="legend-key">' + playerA.name + ' · pico ' + Math.round(peakA.level) + ' a los ' + peakA.age + '</span>' +
    '<span class="legend-key player">' + playerB.name + ' · pico ' + Math.round(peakB.level) + ' a los ' + peakB.age + '</span>' +
  '</div>';
}}

function currentLevelFor(p, surface) {{
  return p?.tourPctBySurface?.[surface] ?? (surface === 'All' ? p?.tourPct : null);
}}

function currentTrendSeries(p, surface) {{
  const key = surface || 'All';
  const source = p?.comparisonLevelTrendBySurface?.[key] || (key === 'All' ? p?.comparisonLevelTrend : []);
  const byYear = new Map();
  (source || [])
    .filter(d => d.year != null && d.level != null)
    .forEach(d => {{
      const year = Number(d.year);
      if (!Number.isFinite(year)) return;
      const existing = byYear.get(year);
      if (!existing || d.current || year > existing.year) {{
        byYear.set(year, {{ year, level: d.level, age: d.age, current: !!d.current }});
      }}
    }});
  const currentLevel = currentLevelFor(p, key);
  if (currentLevel != null) {{
    const fallbackYear = new Date().getFullYear();
    const currentYear = Number(p?.latestYear) || Math.max(...Array.from(byYear.keys()), fallbackYear);
    byYear.set(currentYear, {{
      year: currentYear,
      level: currentLevel,
      age: p?.age,
      current: true,
    }});
  }}
  return Array.from(byYear.values()).sort((a, b) => a.year - b.year);
}}

function renderActiveCurrentChart(playerA, playerB) {{
  const seriesA = currentTrendSeries(playerA, activeCompareSurface);
  const seriesB = currentTrendSeries(playerB, activeCompareSurface);
  if (!seriesA.length || !seriesB.length) {{
    return '<div class="empty-schedule">Sin evolución suficiente para ' + surfaceLabel(activeCompareSurface) + '.</div>';
  }}
  const all = seriesA.concat(seriesB);
  const w = 720, h = 300, left = 42, right = 18, top = 24, bottom = 42;
  const minYear = Math.min(...all.map(d => d.year));
  const maxYear = Math.max(...all.map(d => d.year));
  const minLevel = Math.max(0, Math.min(...all.map(d => d.level)) - 8);
  const maxLevel = Math.min(100, Math.max(...all.map(d => d.level)) + 4);
  const yearSpan = Math.max(1, maxYear - minYear);
  const levelSpan = Math.max(1, maxLevel - minLevel);
  const xFor = year => left + ((year - minYear) / yearSpan) * (w - left - right);
  const yFor = level => h - bottom - ((level - minLevel) / levelSpan) * (h - top - bottom);
  const lineFor = series => series.map(d => xFor(d.year).toFixed(1) + ',' + yFor(d.level).toFixed(1)).join(' ');
  const dotsFor = (series, cls) => series.map(d =>
    '<circle class="' + cls + '" cx="' + xFor(d.year).toFixed(1) + '" cy="' + yFor(d.level).toFixed(1) + '" r="' + (d.current ? 5 : 3) + '"></circle>'
  ).join('');
  const latestA = seriesA[seriesA.length - 1];
  const latestB = seriesB[seriesB.length - 1];
  return '<div class="legend-compare-chart">' +
    '<svg viewBox="0 0 ' + w + ' ' + h + '" role="img" aria-label="Comparador actual por año">' +
      '<line class="legend-axis" x1="' + left + '" y1="' + yFor(50).toFixed(1) + '" x2="' + (w - right) + '" y2="' + yFor(50).toFixed(1) + '"></line>' +
      '<line class="legend-axis" x1="' + left + '" y1="' + (h - bottom) + '" x2="' + (w - right) + '" y2="' + (h - bottom) + '"></line>' +
      '<polyline class="legend-line" points="' + lineFor(seriesA) + '"></polyline>' +
      '<polyline class="player-line" points="' + lineFor(seriesB) + '"></polyline>' +
      dotsFor(seriesA, 'legend-dot') +
      dotsFor(seriesB, 'player-dot') +
      '<circle class="legend-dot" cx="' + xFor(latestA.year).toFixed(1) + '" cy="' + yFor(latestA.level).toFixed(1) + '" r="6"></circle>' +
      '<circle class="player-dot" cx="' + xFor(latestB.year).toFixed(1) + '" cy="' + yFor(latestB.level).toFixed(1) + '" r="6"></circle>' +
      '<text x="' + left + '" y="' + (h - 12) + '" font-size="11" fill="#87867f" font-family="JetBrains Mono">' + minYear + '</text>' +
      '<text x="' + (w - right) + '" y="' + (h - 12) + '" text-anchor="end" font-size="11" fill="#87867f" font-family="JetBrains Mono">' + maxYear + '</text>' +
      '<text x="' + (xFor(latestA.year) + 8).toFixed(1) + '" y="' + (yFor(latestA.level) - 10).toFixed(1) + '" font-size="12" fill="#141413" font-family="JetBrains Mono">' + Math.round(latestA.level) + '</text>' +
      '<text x="' + (xFor(latestB.year) + 8).toFixed(1) + '" y="' + (yFor(latestB.level) + 18).toFixed(1) + '" font-size="12" fill="#d97757" font-family="JetBrains Mono">' + Math.round(latestB.level) + '</text>' +
    '</svg>' +
  '</div>' +
  '<div class="legend-compare-legend">' +
    '<span class="legend-key">' + playerA.name + ' · ' + Math.round(latestA.level) + ' en ' + latestA.year + '</span>' +
    '<span class="legend-key player">' + playerB.name + ' · ' + Math.round(latestB.level) + ' en ' + latestB.year + '</span>' +
  '</div>';
}}

function activeCurrentCard(p, other, alt) {{
  const surfaces = SURFACES.map(s => {{
    const value = currentLevelFor(p, s.key);
    const otherValue = currentLevelFor(other, s.key);
    const winner = value != null && otherValue != null && value >= otherValue;
    return '<div class="active-current-row">' +
      '<div>' + s.label + '</div>' +
      '<div class="active-current-bar"><div class="active-current-fill" style="width:' + Math.max(0, Math.min(100, value || 0)) + '%"></div></div>' +
      '<div style="' + (winner ? 'font-weight:800' : '') + '">' + (value != null ? Math.round(value) : '&#8212;') + '</div>' +
    '</div>';
  }}).join('');
  const selected = currentLevelFor(p, activeCompareSurface);
  return '<div class="active-current-card' + (alt ? ' alt' : '') + '">' +
    '<div class="active-current-head">' +
      '<div><div class="active-current-name">' + p.name + '</div><div class="legend-meta">ATP ' + p.rank + ' · ' + p.age + ' años · ' + surfaceLabel(activeCompareSurface) + '</div></div>' +
      '<div class="active-current-score">' + (selected != null ? Math.round(selected) : '&#8212;') + '</div>' +
    '</div>' +
    surfaces +
  '</div>';
}}

function renderActiveCurrentComparison(playerA, playerB) {{
  const levelA = currentLevelFor(playerA, activeCompareSurface);
  const levelB = currentLevelFor(playerB, activeCompareSurface);
  const diff = levelA != null && levelB != null ? levelA - levelB : null;
  const headline = diff == null
    ? 'Foto actual sin datos suficientes en ' + surfaceLabel(activeCompareSurface)
    : (Math.abs(diff) < 1 ? 'Empate técnico actual' : (diff > 0 ? playerA.name : playerB.name) + ' llega mejor ahora en ' + surfaceLabel(activeCompareSurface));
  return '<div class="legend-meta">' + headline + (diff == null ? '' : ' · diferencia ' + (diff >= 0 ? '+' : '') + diff.toFixed(1)) + '</div>' +
    renderActiveCurrentChart(playerA, playerB) +
    '<div class="active-compare-grid">' +
      activeCurrentCard(playerA, playerB, false) +
      activeCurrentCard(playerB, playerA, true) +
    '</div>';
}}

function renderActiveComparator() {{
  const [player, other] = currentComparePlayers();
  if (!player || !other) return '';
  if (!activeCompareQueryA) activeCompareQueryA = player.name;
  if (!activeCompareQueryB) activeCompareQueryB = other.name;
  const optionsA = playerSearchOptions(other.name);
  const optionsB = playerSearchOptions(player.name);
  const surfaceButtons = SURFACES.map(s =>
    '<button class="' + (activeCompareSurface === s.key ? 'active' : '') + '" onclick="setActiveCompareSurface(\\'' + s.key + '\\')">' + s.label + '</button>'
  ).join('');
  const body = activeCompareMode === 'current'
    ? renderActiveCurrentComparison(player, other)
    : renderActiveAgeComparison(player, other);
  return '<section class="legend-comparator">' +
    '<div class="legend-comparator-head">' +
      '<div><h3>Comparador de activos</h3><div class="legend-meta">Trayectoria por edad y foto actual</div></div>' +
      '<div class="active-compare-controls">' +
        '<input aria-label="Jugador A" list="active-player-options-a" value="' + activeCompareQueryA.replace(/"/g, '&quot;') + '" onfocus="this.select()" oninput="setActiveCompareQuery(\\'A\\', this.value)" onchange="commitActiveCompareQuery(\\'A\\', this.value)">' +
        '<datalist id="active-player-options-a">' + optionsA + '</datalist>' +
        '<input aria-label="Jugador B" list="active-player-options-b" value="' + activeCompareQueryB.replace(/"/g, '&quot;') + '" onfocus="this.select()" oninput="setActiveCompareQuery(\\'B\\', this.value)" onchange="commitActiveCompareQuery(\\'B\\', this.value)">' +
        '<datalist id="active-player-options-b">' + optionsB + '</datalist>' +
        '<div class="legend-picker">' +
          '<button class="' + (activeCompareMode === 'age' ? 'active' : '') + '" onclick="setActiveCompareMode(\\'age\\')">Por edad</button>' +
          '<button class="' + (activeCompareMode === 'current' ? 'active' : '') + '" onclick="setActiveCompareMode(\\'current\\')">Actual</button>' +
        '</div>' +
        '<div class="legend-picker">' + surfaceButtons + '</div>' +
      '</div>' +
    '</div>' +
    body +
  '</section>';
}}

function renderLegendComparator(legends) {{
  const player = currentPlayer();
  if (!activeLegend && legends.length) activeLegend = legends[0].name;
  const legend = legends.find(l => l.name === activeLegend) || legends[0];
  const buttons = legends.map(l =>
    '<button class="' + (l.name === legend.name ? 'active' : '') + '" onclick="setActiveLegend(\\'' + l.name.replace(/'/g, "\\\\'") + '\\')">' + (l.button || l.name.split(' ').slice(-1)[0]) + '</button>'
  ).join('');
  const surfaceButtons = SURFACES.map(s =>
    '<button class="' + (activeCompareSurface === s.key ? 'active' : '') + '" onclick="setActiveCompareSurface(\\'' + s.key + '\\')">' + s.label + '</button>'
  ).join('');
  return '<section class="legend-comparator">' +
    '<div class="legend-comparator-head">' +
      '<div><h3>' + (player?.name || 'Jugador') + ' vs leyendas</h3><div class="legend-meta">Curva de nivel por edad · ' + surfaceLabel(activeCompareSurface) + '</div></div>' +
      '<div class="active-compare-controls"><div class="legend-picker">' + buttons + '</div><div class="legend-picker">' + surfaceButtons + '</div></div>' +
    '</div>' +
    compareLegendChart(legend, player) +
  '</section>';
}}

function renderLegends() {{
  const wrap = document.getElementById('legends-view');
  if (!wrap) return;
  const legends = LEGEND_COMPARISON?.legends || [];
  const activeTop = LEGEND_COMPARISON?.activeTop || [];
  const legendRows = legends.map(l => {{
    const peak = l.peak || {{}};
    const latest = l.latest || {{}};
    const drift = latest.level != null && peak.level != null ? latest.level - peak.level : null;
    const driftText = drift == null ? '' : ' · final ' + Math.round(latest.level) + ' (' + (drift >= 0 ? '+' : '') + drift.toFixed(0) + ')';
    const memberText = l.type === 'profile' && l.members?.length ? ' · ' + l.members.length + ' jugadores' : '';
    return '<div class="legend-row">' +
      '<div>' +
        '<div class="legend-name">' + l.name + '</div>' +
        '<div class="legend-meta">' + l.group + memberText + ' · ' + l.gs + ' GS · pico ' + peak.year + ' · ' + peak.age + ' años · Elo ' + (peak.elo || '&#8212;') + driftText + '</div>' +
      '</div>' +
      '<div class="legend-score">' + Math.round(peak.level || 0) + '</div>' +
      '<div class="legend-rank">#' + l.rankVsActive + '<br>vs activos</div>' +
      sparkline(l.yearly) +
    '</div>';
  }}).join('');
  const activeRows = activeTop.map((p, idx) =>
    '<div class="active-benchmark-row">' +
      '<div class="active-benchmark-rank">' + (idx + 1) + '</div>' +
      '<div><div class="active-benchmark-name">' + p.name + '</div><div class="legend-meta">ATP ' + p.rank + ' · ' + p.age + ' años</div></div>' +
      '<div class="active-benchmark-score">' + Math.round(p.level) + '</div>' +
    '</div>'
  ).join('');
  wrap.innerHTML =
    renderLegendComparator(legends) +
    renderActiveComparator() +
    '<section class="legend-card">' + legendRows + '</section>' +
    '<aside class="active-benchmark"><h3>Top activo por nivel</h3>' + activeRows + '</aside>';
}}

function normaliseName(name) {{
  return (name || '')
    .toLowerCase()
    .normalize('NFD').replace(/[\\u0300-\\u036f]/g, '')
    .replace(/[^a-z ]/g, ' ')
    .replace(/\\s+/g, ' ')
    .trim();
}}

function findPlayerByMatchName(name) {{
  const clean = normaliseName(name);
  if (!clean || clean === 'tbd' || clean.includes('order of play')) return null;
  return ALL_PLAYERS.find(p => normaliseName(p.name) === clean) ||
    ALL_PLAYERS.find(p => {{
      const pn = normaliseName(p.name);
      return pn.includes(clean) || clean.includes(pn);
    }}) || null;
}}

function surfaceKeyForMatch(match) {{
  const surface = normaliseName(match.surface);
  if (surface.includes('clay') || surface.includes('tierra')) return 'Clay';
  if (surface.includes('grass') || surface.includes('hierba')) return 'Grass';
  if (surface.includes('hard') || surface.includes('rapida')) return 'Hard';
  return 'All';
}}

function globalLevel(p) {{
  const value = p?.tourPctBySurface?.All ?? p?.tourPct ?? null;
  return value == null ? null : Math.round(value);
}}

function schedulePlayerName(raw, p) {{
  const name = raw || 'TBD';
  const level = globalLevel(p);
  if (level == null) return name;
  return name + ' <span style="color:var(--cloud-dark)">(' + level + ')</span>';
}}

function renderMatchGlance(p1, p2) {{
  const level1 = globalLevel(p1);
  const level2 = globalLevel(p2);
  if (level1 == null && level2 == null) return '';
  const pill1 = '<span class="match-glance-pill">' + (p1?.name?.split(' ').slice(-1)[0] || 'P1') + '<span class="match-glance-score">' + (level1 ?? '&#8212;') + '</span></span>';
  const pill2 = '<span class="match-glance-pill">' + (p2?.name?.split(' ').slice(-1)[0] || 'P2') + '<span class="match-glance-score">' + (level2 ?? '&#8212;') + '</span></span>';
  return '<div class="match-glance">' + pill1 + pill2 + '</div>';
}}

function compareValue(p, surface, key) {{
  if (!p) return null;
  if (key === 'tourPct') return p.tourPctBySurface?.[surface] ?? p.tourPctBySurface?.All ?? p.tourPct ?? null;
  const metrics = p.performanceBySurface?.[surface] || p.performanceBySurface?.All || null;
  const compactRow = (metrics?.rows || []).find(r => r.key === key);
  if (compactRow?.actual != null) return compactRow.actual;
  const groups = metrics?.groups || [];
  for (const group of groups) {{
    const row = (group.rows || []).find(r => r.key === key);
    if (row?.available && row.actual != null) return row.actual;
  }}
  return null;
}}

function formatCompareVal(value, key) {{
  if (value == null || !Number.isFinite(value)) return '&#8212;';
  if (key === 'dominanceRatio') return value.toFixed(2);
  if (key === 'averageRallyLength' || key === 'returnDepthIndex') return value.toFixed(1);
  return Math.round(value) + '%';
}}

function renderCompareCell(value, other, key) {{
  const winner = value != null && other != null && value > other;
  return '<div class="compare-val ' + (winner ? 'win' : '') + '">' + formatCompareVal(value, key) + '</div>';
}}

function renderComparePlayer(p, surface) {{
  if (!p) {{
    return '<div class="compare-player"><div class="compare-player-name">TBD</div><div class="compare-score">&#8212;</div><div class="match-info">Sin datos</div></div>';
  }}
  const pct = compareValue(p, surface, 'tourPct');
  return '<div class="compare-player">' +
    '<div class="compare-player-name">' + p.name + '</div>' +
    '<div class="compare-score">' + (pct == null ? '&#8212;' : Math.round(pct)) + '</div>' +
    '<div class="match-info">Nivel circuito · ' + activeSurfaceName(surface) + '</div>' +
  '</div>';
}}

function activeSurfaceName(surface) {{
  return SURFACES.find(s => s.key === surface)?.label || 'Global';
}}

function renderMatchComparison(match) {{
  const surface = surfaceKeyForMatch(match);
  const p1 = findPlayerByMatchName(match.player1);
  const p2 = findPlayerByMatchName(match.player2);
  const stats = [
    ['tourPct', 'Nivel circuito'],
    ['totalPtsWon', 'Total Points Won'],
    ['servicePtsWon', 'Service Pts Won'],
    ['returnPtsWon', 'Return Pts Won'],
    ['holdPct', 'Hold %'],
    ['breakPct', 'Break %'],
    ['unreturnedServe', 'Unreturned Serve'],
    ['shortServeWon', '<=3 Shots Won'],
    ['returnDepthIndex', 'Return Depth'],
    ['shortRallyWin', '1-3 Shot Win'],
    ['forehandPotency', 'FH Potency'],
    ['backhandPotency', 'BH Potency'],
    ['netWin', 'Net Win'],
  ];
  const rows = stats.map(([key, label]) => {{
    const v1 = compareValue(p1, surface, key);
    const v2 = compareValue(p2, surface, key);
    return '<div class="compare-row">' +
      '<div class="compare-stat">' + label + '</div>' +
      renderCompareCell(v1, v2, key) +
      renderCompareCell(v2, v1, key) +
    '</div>';
  }}).join('');
  return '<div class="match-compare">' +
    '<div class="compare-ring-row">' +
      renderComparePlayer(p1, surface) +
      '<div class="compare-vs">vs</div>' +
      renderComparePlayer(p2, surface) +
    '</div>' +
    '<div class="compare-table">' +
      '<div class="compare-row head"><div>Stat</div><div class="compare-val">' + (p1?.name?.split(' ').slice(-1)[0] || 'P1') + '</div><div class="compare-val">' + (p2?.name?.split(' ').slice(-1)[0] || 'P2') + '</div></div>' +
      rows +
    '</div>' +
  '</div>';
}}

function goToMatch(ref) {{
  goToTab('schedule-tab');
  window.setTimeout(() => {{
    const el = document.querySelector('[data-match-ref="' + ref + '"]');
    if (!el) return;
    el.open = true;
    el.scrollIntoView({{ behavior: 'smooth', block: 'center' }});
  }}, 280);
}}

function goToPlayerTab() {{
  goToTab('player-tab');
}}

function goToTab(id) {{
  document.getElementById(id)?.scrollIntoView({{ behavior: 'smooth', inline: 'start', block: 'nearest' }});
}}

function updateSliderNav() {{
  const viewport = document.getElementById('tabs-viewport');
  if (!viewport) return;
  const ids = ['ranking-tab', 'player-tab', 'schedule-tab', 'legends-tab'];
  const index = Math.max(0, Math.min(ids.length - 1, Math.round(viewport.scrollLeft / Math.max(1, viewport.clientWidth))));
  const activeId = ids[index];
  for (const id of ids) {{
    const nav = document.getElementById('nav-' + id.replace('-tab', ''));
    if (nav) nav.classList.toggle('active', id === activeId);
  }}
}}

// Event delegation — attached once to static container
document.getElementById('player-list').addEventListener('click', e => {{
  const row = e.target.closest('[data-name]');
  if (row) selectPlayer(row.dataset.name);
}});

window.setSort = function(key) {{
  if (sortKey === key) {{ sortDir = -sortDir; }}
  else {{ sortKey = key; sortDir = (key === 'age') ? 1 : -1; }}
  document.querySelectorAll('.sort-btn').forEach(btn =>
    btn.classList.toggle('active', btn.id === 'sort-' + key)
  );
  refresh();
}};

function refresh() {{
  const q = getQ();
  const filtered = q
    ? ALL_PLAYERS.filter(p => p.name.toLowerCase().includes(q))
    : ALL_PLAYERS;
  if (q && filtered.length) {{
    const current = currentPlayer();
    const currentVisible = current && current.name.toLowerCase().includes(q);
    if (!currentVisible) activePlayer = sortPlayers(filtered)[0].name;
  }}
  buildList(sortPlayers(filtered));
  renderRankingSurfaceButtons();
  renderPlayerDetail();
  renderSchedule();
  renderLegends();
}}

window.selectPlayer = function(name) {{
  activePlayer = name;
  refresh();
  goToPlayerTab();
}};

window.setPlayerSurface = function(surface) {{
  activeSurface = surface;
  renderPlayerDetail();
}};

window.setRankingSurface = function(surface) {{
  rankingSurface = surface;
  if (sortKey === 'tour') sortDir = -1;
  refresh();
}};

window.setActiveLegend = function(name) {{
  activeLegend = name;
  renderLegends();
}};

window.setActiveComparePlayerA = function(name) {{
  activeComparePlayerA = name;
  activeCompareQueryA = name;
  if (activeComparePlayerB === name) {{
    activeComparePlayerB = defaultComparePlayer(name)?.name || null;
    activeCompareQueryB = activeComparePlayerB || '';
  }}
  renderLegends();
}};

window.setActiveComparePlayerB = function(name) {{
  activeComparePlayerB = name;
  activeCompareQueryB = name;
  if (activeComparePlayerA === name) {{
    activeComparePlayerA = defaultComparePlayer(name)?.name || null;
    activeCompareQueryA = activeComparePlayerA || '';
  }}
  renderLegends();
}};

window.setActiveCompareQuery = function(slot, value) {{
  if (slot === 'A') activeCompareQueryA = value;
  else activeCompareQueryB = value;
}};

window.commitActiveCompareQuery = function(slot, value) {{
  const exclude = slot === 'A' ? activeComparePlayerB : activeComparePlayerA;
  const player = findPlayerFromQuery(value, exclude);
  if (!player) {{
    renderLegends();
    return;
  }}
  if (slot === 'A') setActiveComparePlayerA(player.name);
  else setActiveComparePlayerB(player.name);
}};

window.setActiveCompareMode = function(mode) {{
  activeCompareMode = mode;
  renderLegends();
}};

window.setActiveCompareSurface = function(surface) {{
  activeCompareSurface = surface;
  renderLegends();
}};

window.toggleSearch = function() {{
  const wrap = document.getElementById('header-search');
  const input = document.getElementById('search-input');
  const wasClosed = !wrap.classList.contains('open');
  wrap.classList.toggle('open', wasClosed);
  if (wasClosed) input.focus();
  else {{ input.value = ''; refresh(); }}
}};

refresh();
updateSliderNav();
document.getElementById('tabs-viewport')?.addEventListener('scroll', () => {{
  window.requestAnimationFrame(updateSliderNav);
}});
  </script>
</body>
</html>"""


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Build the ATP scout index.html")
    parser.add_argument("--top",       type=int, default=300, help="Number of ranked players (default: 300)")
    parser.add_argument("--years-back",type=int, default=3,   help="Years of match data to analyse (default: 3)")
    parser.add_argument("--no-cache",  action="store_true",   help="Re-download all CSVs")
    parser.add_argument("--output",    default=str(EXAMPLES_DIR / "index.html"), help="Output path")
    args = parser.parse_args()

    use_cache  = not args.no_cache
    years_back = args.years_back
    top_n      = args.top

    # 1. Get current top-N players
    print(f"Fetching current ATP top {top_n}...")
    players = sf.get_top_n_players(top_n, use_cache=use_cache)
    print(f"  Got {len(players)} players")

    # 2. Load legend benchmark + player stats for regression
    bench_path = DATA_DIR / "legend_benchmark.json"
    stats_path = DATA_DIR / "player_stats_by_age.json"
    if not bench_path.exists() or not stats_path.exists():
        print("ERROR: Run stats_fetcher.py first to generate benchmark data.")
        sys.exit(1)
    with open(bench_path, encoding="utf-8") as f:
        benchmark = json.load(f)
    with open(stats_path, encoding="utf-8") as f:
        all_historical_stats = json.load(f)

    # Populate module-level regression globals
    global _REGRESSION_TARGETS, _ALL_STATS, _LEGEND_SIM_BY_AGE, _ELO_AGE_REFERENCE
    _ALL_STATS = all_historical_stats
    _REGRESSION_TARGETS = sf.build_regression_targets(all_historical_stats)
    print(f"  Regression targets: {len(_REGRESSION_TARGETS)} historical players")
    print("Computing age-based Elo references...")
    _ELO_AGE_REFERENCE = _build_elo_age_reference(use_cache=use_cache)

    # Compute legend sim scores by age (for comparison table)
    _LEGEND_SIM_BY_AGE = {}
    for leg_name in BACKGROUND_LEGENDS:
        leg_data = all_historical_stats.get(leg_name, {})
        born     = sf.PLAYERS.get(leg_name, {}).get("born")
        if not born:
            continue
        age_sim = {}
        for yr_str, yr_data in leg_data.items():
            la = yr_data.get("All") or {}
            s  = sf.compute_profile_similarity(la, benchmark)
            if s is not None:
                age_sim[int(yr_str) - born.year] = round(s, 1)
        _LEGEND_SIM_BY_AGE[leg_name] = age_sim

    # 3. Batch compute stats for all players
    current_year = date.today().year
    years = tuple(range(current_year - years_back, current_year + 1))
    batch_stats = sf.compute_all_players_batch(players, years=years, use_cache=use_cache)
    trend_years = tuple(range(1991, current_year + 1))
    print("Computing career-wide level trends...")
    trend_stats = _trend_only_batch(players, trend_years, use_cache=use_cache)

    # 3b. Synthetic 0-GS anchor: average stats of below-average players (sim < 45).
    # Grounds the kernel regression so players unlike any calibration player get pulled to 0.
    _accs = {k: [] for k in sf._REGRESSION_STAT_KEYS}
    for p in players:
        sty = batch_stats.get(p["player_id"])
        if not sty:
            continue
        ly = max(sty.keys(), key=int)
        la = sty[ly].get("All") or {}
        sim_val = sf.compute_profile_similarity(la, benchmark)
        if sim_val is not None and sim_val < 45:
            for k in sf._REGRESSION_STAT_KEYS:
                v = la.get(k)
                if v is not None:
                    _accs[k].append(v)
    anchor = {k: sum(v) / len(v) for k, v in _accs.items() if len(v) >= 5}
    if len(anchor) >= 4:
        _REGRESSION_TARGETS["_zero_gs_anchor"] = {"career_gs": 0, "avg_stats": anchor}
        print(f"  Zero-GS anchor added ({len(_accs.get('win_rate', []))} below-avg players)")

    # 4. Count GS wins across relevant years (scan once per year)
    pid_set = {p["player_id"] for p in players}
    gs_year_range = range(current_year - 15, current_year + 1)
    print("Counting Grand Slam wins...")
    gs_wins = sf.count_gs_wins_batch(pid_set, gs_year_range, use_cache=use_cache)
    print(f"  Found GS wins for {len(gs_wins)} players")

    # 5. Compute Elo ratings from full match history
    print("Computing Elo ratings from match history...")
    elo_ratings = ec.compute_elo_ratings()
    surface_elo_ratings = ec.compute_elo_ratings_by_surface()
    print(
        "  Elo computed for "
        f"{len(elo_ratings)} players "
        f"(surface: H {len(surface_elo_ratings.get('Hard', {}))}, "
        f"C {len(surface_elo_ratings.get('Clay', {}))}, "
        f"G {len(surface_elo_ratings.get('Grass', {}))})"
    )

    print("Computing last-10 vs career performance metrics...")
    performance_metrics = _performance_metrics_for_players(
        players, range(1991, current_year + 1), use_cache=use_cache
    )
    print(f"  Performance metrics for {len(performance_metrics)} players")
    print("Computing tournament/opponent quality weights...")
    effective_counts = _effective_match_counts(players, trend_years, use_cache=use_cache)
    live_schedule = _load_live_schedule()
    print("Mixing live player profiles...")
    live_profiles = _live_profiles_for_players(
        players, range(1991, current_year + 1), live_schedule, use_cache=use_cache
    )
    schedule_next_matches = _next_matches_from_schedule(live_schedule)
    manual_next_matches = _load_next_matches()
    next_matches = {
        **schedule_next_matches,
        **manual_next_matches,
    }
    for name, match in manual_next_matches.items():
        schedule_match = schedule_next_matches.get(name, {})
        if schedule_match.get("matchRef") and not match.get("matchRef"):
            next_matches[name]["matchRef"] = schedule_match["matchRef"]
    print(f"  Live next-match chips: {len(next_matches)}")

    # 6. Build player records
    print("Building player records...")
    players_data = []
    skipped = 0
    for p in players:
        pid  = p["player_id"]
        stats_by_year = trend_stats.get(pid) or batch_stats.get(pid)
        if not stats_by_year:
            skipped += 1
            continue
        record = build_player_record(
            p, stats_by_year, benchmark, gs_wins, elo_ratings, surface_elo_ratings, performance_metrics,
            next_matches, effective_counts.get(pid), live_profiles.get(pid),
            trend_stats.get(pid)
        )
        if record:
            players_data.append(record)

    print(f"  {len(players_data)} players with data ({skipped} skipped — insufficient ATP match data)")

    _annotate_performance_context(players_data)

    # 6b. Tour percentile: rank each player's sim among current players (0-100)
    surface_keys = ("All", "Hard", "Clay", "Grass")
    valid_sims_by_surface = {
        surf: sorted(
            (r.get("simBySurface") or {}).get(surf)
            for r in players_data
            if (r.get("simBySurface") or {}).get(surf) is not None
        )
        for surf in surface_keys
    }
    for r in players_data:
        pct_by_surface = {}
        for surf in surface_keys:
            sim_val = (r.get("simBySurface") or {}).get(surf)
            valid_sims = valid_sims_by_surface[surf]
            if sim_val is not None and valid_sims:
                idx = bisect.bisect_left(valid_sims, sim_val)
                pct_by_surface[surf] = round(idx / len(valid_sims) * 100, 1)
            else:
                pct_by_surface[surf] = None
        r["statPctBySurface"] = pct_by_surface
        level_by_surface = {}
        level_factors = {}
        for surf in surface_keys:
            level, factors = _composite_level(
                pct_by_surface.get(surf),
                r.get("rank"),
                (r.get("eloBySurface") or {}).get(surf) if surf != "All" else r.get("elo"),
                r.get("age"),
                r.get("surfaceSamples") or {},
                r.get("effectiveSamples") or {},
                r.get("performanceBySurface") or {},
                surf,
            )
            level_by_surface[surf] = level
            level_factors[surf] = factors
        r["tourPctBySurface"] = level_by_surface
        r["levelFactorsBySurface"] = level_factors
        for surf, points in (r.get("levelTrendBySurface") or {}).items():
            valid_sims = valid_sims_by_surface.get(surf) or []
            for point in points:
                value = point.get("value")
                if value is None or not valid_sims:
                    point["pct"] = None
                    continue
                idx = bisect.bisect_left(valid_sims, value)
                point["pct"] = round(idx / len(valid_sims) * 100, 1)
            current_pct = level_by_surface.get(surf)
            current_sample = (r.get("surfaceSamples") or {}).get(surf)
            current_value = (r.get("simBySurface") or {}).get(surf)
            if current_pct is not None:
                points.append({
                    "year": "Actual",
                    "value": current_value,
                    "pct": current_pct,
                    "matches": current_sample,
                    "current": True,
                })
        r["tourPct"] = level_by_surface.get("All")

    _attach_comparison_level_trends(
        players_data,
        trend_stats,
        benchmark,
        valid_sims_by_surface,
        use_cache=use_cache,
    )

    print("Building legend level comparison...")
    legend_comparison = _build_legend_comparison(
        all_historical_stats,
        benchmark,
        players_data,
        valid_sims_by_surface,
        use_cache=use_cache,
    )
    print(f"  Legend comparison: {len(legend_comparison.get('legends', []))} players")

    # 7. Build legend background datasets
    legend_datasets = []
    for name in BACKGROUND_LEGENDS:
        traj  = GS_TRAJECTORIES.get(name, [])
        color = LEGEND_COLORS.get(name, "#aaaaaa")
        legend_datasets.append({
            "label": name,
            "data":  [{"x": a, "y": g} for a, g in traj],
            "borderColor": color, "backgroundColor": color,
            "borderWidth": 1.5, "pointRadius": 3, "pointHoverRadius": 6,
            "tension": 0.35, "fill": False, "order": 5,
        })

    # 8. Collect recent notable matches (screen 3)
    print("Reading recent matches...")
    recent_matches = _recent_matches()
    print(f"  {len(recent_matches)} recent matches (GS/Masters/500/250/Davis)")
    live_match_count = sum(len(day.get("matches", [])) for day in live_schedule.get("days", []))
    print(f"  Live schedule matches: {live_match_count}")

    # 9. Render and save
    EXAMPLES_DIR.mkdir(parents=True, exist_ok=True)
    html = render_index(players_data, legend_datasets, recent_matches, live_schedule, legend_comparison, top_n)
    out_path = Path(args.output)
    out_path.write_text(html, encoding="utf-8")
    print(f"\nDone! → {out_path}")
    print(f"  {len(players_data)} jugadores · abre el archivo en tu navegador")


if __name__ == "__main__":
    main()
