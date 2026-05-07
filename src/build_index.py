"""
Generates examples/index.html — a single interactive dashboard for any
player in the current ATP top N.

Usage:
  python3 src/build_index.py             # top 200, 3 years of data
  python3 src/build_index.py --top 100   # faster
  python3 src/build_index.py --no-cache  # force re-download

The generated index.html has all data embedded — no server needed.
Open it directly in a browser and search/click any player to see their
GS trajectory, profile stats, and projections vs the legend benchmark.
"""

import argparse
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


# ── Build player record ───────────────────────────────────────────────────────

def build_player_record(p: dict, stats_by_year: dict, benchmark: dict,
                         gs_wins: dict, elo_ratings: dict = None) -> dict:
    pid  = p["player_id"]
    name = p["name"]
    born = p["born"]
    rank = p["rank"]

    # Elo rating (from full match history)
    elo_data = (elo_ratings or {}).get(pid)
    elo      = int(elo_data["elo"]) if elo_data else None

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

    return {
        "name":       name,
        "rank":       rank,
        "age":        age,
        "gs":         gs_total,
        "sim":        sim,
        "capi":       capi,
        "nearTerm":   near_term,
        "elo":        elo,
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

def render_index(players_data: list, legend_datasets: list) -> str:
    legend_datasets_json = json.dumps(legend_datasets, ensure_ascii=False)
    players_json         = json.dumps(players_data,    ensure_ascii=False)
    legend_names_json    = json.dumps(BACKGROUND_LEGENDS)

    return f"""<!DOCTYPE html>
<html lang="es">
<head>
  <meta charset="UTF-8"/>
  <meta name="viewport" content="width=device-width,initial-scale=1"/>
  <title>Legend Trajectory — ATP Scout</title>
  <link rel="preconnect" href="https://fonts.googleapis.com"/>
  <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin/>
  <link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600&family=Lexend:wght@400;500;600;700;800&display=swap" rel="stylesheet"/>
  <script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.4/dist/chart.umd.min.js"></script>
  <style>
    *, *::before, *::after {{ box-sizing: border-box; margin: 0; padding: 0; }}

    :root {{
      --lime:   #D6FF00; --teal:   #08605F; --teal2:  #0a7a79;
      --green:  #119822; --sky:    #00ABE7; --aqua:   #2DC7FF;
      --red:    #e63946; --orange: #f59e0b;
      --bg:     #f3f6f9; --white:  #ffffff;
      --text:   #111827; --muted:  #6b7280; --border: #e5e7eb;
    }}

    body {{ font-family: 'Inter', system-ui, sans-serif; background: var(--bg); color: var(--text); height: 100vh; overflow: hidden; }}

    /* Layout */
    .app {{ display: grid; grid-template-columns: 290px 1fr; height: 100vh; }}

    /* Sidebar */
    .sidebar {{ background: var(--teal); color: #d4f0ef; display: flex; flex-direction: column; overflow: hidden; }}
    .sidebar-header {{ padding: 20px 16px 14px; border-bottom: 1px solid rgba(255,255,255,0.1); }}
    .sidebar-header h1 {{ font-family: 'Lexend', sans-serif; font-size: 1rem; font-weight: 700; color: var(--lime); letter-spacing: 0.01em; margin-bottom: 12px; }}
    .search-box {{
      width: 100%; padding: 8px 12px; border-radius: 8px;
      border: 1px solid rgba(255,255,255,0.15);
      background: rgba(255,255,255,0.08); color: #d4f0ef;
      font-size: 0.85rem; outline: none; font-family: 'Inter', sans-serif;
    }}
    .search-box::placeholder {{ color: rgba(255,255,255,0.35); }}
    .search-box:focus {{ border-color: var(--lime); background: rgba(255,255,255,0.12); }}
    .player-count {{ font-size: 0.70rem; color: rgba(255,255,255,0.38); margin-top: 5px; }}
    .sort-controls {{ display: flex; gap: 4px; margin-top: 10px; flex-wrap: wrap; }}
    .sort-btn {{
      flex: 1; min-width: 44px; padding: 5px 2px; border-radius: 6px;
      border: 1px solid rgba(255,255,255,0.15); background: transparent;
      color: rgba(255,255,255,0.5); cursor: pointer; font-size: 0.64rem;
      font-family: 'Inter', sans-serif; text-align: center; transition: all 0.14s; white-space: nowrap;
    }}
    .sort-btn:hover {{ background: rgba(255,255,255,0.1); color: #fff; }}
    .sort-btn.active {{ background: var(--lime); color: var(--teal); border-color: var(--lime); font-weight: 700; }}

    /* Player list */
    .player-list {{ overflow-y: auto; flex: 1; }}
    .player-list::-webkit-scrollbar {{ width: 3px; }}
    .player-list::-webkit-scrollbar-thumb {{ background: rgba(255,255,255,0.18); border-radius: 2px; }}
    .player-item {{
      display: grid; grid-template-columns: 28px 1fr auto; gap: 8px;
      align-items: center; padding: 10px 16px; cursor: pointer;
      border-bottom: 1px solid rgba(255,255,255,0.06);
      border-left: 3px solid transparent; transition: all 0.12s;
    }}
    .player-item:hover {{ background: rgba(255,255,255,0.07); }}
    .player-item.active {{ background: rgba(214,255,0,0.08); border-left-color: var(--lime); }}
    .p-rank {{ font-size: 0.70rem; color: rgba(255,255,255,0.38); text-align: right; font-family: 'Lexend', sans-serif; }}
    .p-name {{ font-size: 0.85rem; font-weight: 500; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; color: #d4f0ef; }}
    .p-age  {{ font-size: 0.67rem; color: rgba(255,255,255,0.42); }}
    .p-sim  {{
      font-size: 0.70rem; font-weight: 700; padding: 2px 7px;
      border-radius: 20px; border: 1px solid; text-align: center;
      min-width: 36px; font-family: 'Lexend', sans-serif;
    }}

    /* Main panel */
    .main {{ overflow-y: auto; padding: 24px; display: flex; flex-direction: column; gap: 16px; }}
    .main::-webkit-scrollbar {{ width: 4px; }}
    .main::-webkit-scrollbar-thumb {{ background: #d1d5db; border-radius: 2px; }}

    /* Cards */
    .card {{ background: var(--white); border: 1px solid var(--border); border-radius: 16px; padding: 20px; box-shadow: 0 1px 6px rgba(0,0,0,0.05); }}
    .player-header {{ display: flex; flex-wrap: wrap; align-items: center; gap: 10px; }}
    .player-header h2 {{ font-family: 'Lexend', sans-serif; font-size: 1.6rem; font-weight: 700; color: var(--text); letter-spacing: -0.02em; }}
    .badge {{ padding: 4px 12px; border-radius: 20px; font-size: 0.76rem; font-weight: 600; font-family: 'Lexend', sans-serif; }}
    .badge-rank {{ background: var(--teal); color: var(--lime); }}
    .badge-gs {{ background: #ecfdf5; color: var(--green); border: 1px solid #a7f3d0; }}
    .badge-sim {{ color: #fff; }}

    /* Two-col panels */
    .panels-row {{ display: grid; grid-template-columns: 1fr 1fr; gap: 14px; }}
    @media (max-width: 800px) {{ .panels-row {{ grid-template-columns: 1fr; }} }}
    .panel {{ background: var(--white); border: 1px solid var(--border); border-radius: 14px; padding: 16px; box-shadow: 0 1px 4px rgba(0,0,0,0.04); }}
    .panel h3 {{ font-family: 'Lexend', sans-serif; font-size: 0.68rem; font-weight: 600; color: var(--muted); text-transform: uppercase; letter-spacing: 0.08em; margin-bottom: 12px; }}

    /* Projections */
    .proj-grid {{ display: flex; gap: 8px; }}
    .proj-item {{ flex: 1; text-align: center; background: var(--bg); border-radius: 10px; padding: 12px 4px; border: 1px solid var(--border); }}
    .proj-label {{ display: block; font-size: 0.58rem; color: var(--muted); text-transform: uppercase; letter-spacing: 0.08em; margin-bottom: 4px; font-family: 'Inter', sans-serif; }}
    .proj-value {{ font-family: 'Lexend', sans-serif; font-size: 1.9rem; font-weight: 800; }}
    .blue {{ color: var(--sky); }} .orange {{ color: var(--orange); }} .green {{ color: var(--green); }}

    /* Comparison table */
    table {{ width: 100%; border-collapse: collapse; font-size: 0.81rem; }}
    th {{ padding: 7px 8px; background: var(--bg); font-weight: 600; color: var(--muted); font-size: 0.66rem; text-transform: uppercase; letter-spacing: 0.06em; font-family: 'Inter', sans-serif; text-align: left; }}
    td {{ padding: 8px 8px; border-bottom: 1px solid var(--border); font-family: 'Inter', sans-serif; }}
    .td-c {{ text-align: center; }}
    .color-dot {{ display: inline-block; width: 8px; height: 8px; border-radius: 50%; margin-right: 5px; }}
    .diff-pos {{ color: var(--red); font-weight: 600; }}
    .diff-neg {{ color: var(--green); font-weight: 600; }}

    /* Chart */
    .chart-panel {{ background: var(--white); border: 1px solid var(--border); border-radius: 16px; padding: 20px; box-shadow: 0 1px 6px rgba(0,0,0,0.05); }}
    .chart-panel h3 {{ font-family: 'Lexend', sans-serif; font-size: 0.68rem; font-weight: 600; color: var(--muted); text-transform: uppercase; letter-spacing: 0.08em; margin-bottom: 4px; }}
    .chart-sub {{ font-size: 0.75rem; color: var(--muted); margin-bottom: 12px; }}
    .chart-controls {{ display: flex; gap: 6px; margin-bottom: 10px; flex-wrap: wrap; }}
    .ctrl-btn {{
      padding: 4px 12px; border: 1px solid var(--border); border-radius: 20px;
      background: var(--white); cursor: pointer; font-size: 0.74rem;
      color: var(--muted); transition: all 0.14s; font-family: 'Inter', sans-serif;
    }}
    .ctrl-btn.active {{ background: var(--teal); color: var(--lime); border-color: var(--teal); font-weight: 600; }}
    .chart-wrap {{ position: relative; height: 340px; }}
    .chart-legend-row {{ display: flex; flex-wrap: wrap; gap: 8px; margin-top: 10px; }}
    .legend-item {{ display: inline-flex; align-items: center; gap: 5px; font-size: 0.73rem; color: var(--muted); cursor: pointer; transition: opacity 0.13s; font-family: 'Inter', sans-serif; }}
    .legend-dot {{ width: 8px; height: 8px; border-radius: 50%; flex-shrink: 0; }}

    /* Game stats bars */
    .stat-bar-grid {{ display: flex; flex-direction: column; gap: 8px; }}
    .stat-bar-row {{ display: grid; grid-template-columns: 130px 1fr 70px; gap: 8px; align-items: center; }}
    .stat-bar-label {{ font-size: 0.73rem; color: var(--muted); font-family: 'Inter', sans-serif; }}
    .stat-bar-track {{ background: #f0f3f8; border-radius: 6px; height: 8px; position: relative; overflow: visible; }}
    .stat-bar-fill {{ height: 100%; border-radius: 6px; transition: width 0.4s; }}
    .stat-bar-bench {{ position: absolute; top: -4px; width: 2px; height: 16px; background: var(--text); border-radius: 1px; opacity: 0.5; }}
    .stat-bar-val {{ font-size: 0.74rem; font-weight: 600; text-align: right; white-space: nowrap; font-family: 'Lexend', sans-serif; }}
    .potential-row {{ display: flex; align-items: flex-start; gap: 0; margin-bottom: 16px; }}
    .potential-score {{ font-family: 'Lexend', sans-serif; font-size: 2rem; font-weight: 800; line-height: 1.1; }}
    .potential-label {{ font-size: 0.70rem; color: var(--muted); line-height: 1.5; margin-top: 3px; font-family: 'Inter', sans-serif; font-weight: 500; }}

    .no-player {{ color: var(--muted); text-align: center; padding: 60px 20px; font-size: 1rem; font-family: 'Lexend', sans-serif; }}
    .data-note {{ font-size: 0.68rem; color: #9ca3af; margin-top: 10px; font-style: italic; font-family: 'Inter', sans-serif; }}

    /* ── Mobile ──────────────────────────────────────────────────────────── */
    .sidebar-toggle {{
      display: none; position: fixed; bottom: 20px; left: 20px; z-index: 200;
      background: var(--teal); color: var(--lime); border: none; border-radius: 50%;
      width: 54px; height: 54px; font-size: 1.3rem; cursor: pointer;
      box-shadow: 0 4px 18px rgba(0,0,0,0.45); align-items: center; justify-content: center;
    }}
    .sidebar-overlay {{
      display: none; position: fixed; inset: 0; background: rgba(0,0,0,0.45); z-index: 90;
    }}
    @media (max-width: 680px) {{
      body {{ overflow: auto; height: auto; }}
      .app {{ grid-template-columns: 1fr; height: auto; overflow: visible; }}
      .sidebar {{
        position: fixed; left: -310px; top: 0; height: 100dvh;
        width: 300px; z-index: 100; transition: left 0.22s ease;
      }}
      .sidebar.open {{ left: 0; box-shadow: 4px 0 28px rgba(0,0,0,0.4); }}
      .sidebar-overlay.visible {{ display: block; }}
      .sidebar-toggle {{ display: flex; }}
      .main {{ height: auto; overflow: visible; padding: 12px 12px 80px; }}
      .panels-row {{ grid-template-columns: 1fr; }}

      /* Projection cards — 3 equal columns, smaller font */
      .proj-value {{ font-size: 1.3rem; }}
      .proj-label {{ font-size: 0.60rem; }}

      /* Metrics row — 2-column grid instead of horizontal scroll */
      .potential-row {{
        display: grid; grid-template-columns: 1fr 1fr;
        overflow: visible; flex-wrap: unset; gap: 10px;
      }}
      .potential-row > div {{
        border-left: none !important; padding-left: 0 !important;
        border-top: 1px solid var(--border); padding-top: 8px; min-width: unset;
      }}
      .potential-row > div:nth-child(-n+2) {{ border-top: none; padding-top: 0; }}
      /* Last child (data note) spans full width */
      .potential-row > div:last-child {{ grid-column: 1 / -1; border-top: 1px solid var(--border); padding-top: 8px; }}
      .potential-score {{ font-size: 1.55rem; }}
      /* Hide multi-line subtitles — just keep the main label */
      .potential-label > span {{ display: none; }}
      .potential-label {{ font-size: 0.72rem; }}

      .chart-wrap {{ height: 250px; }}
      .stat-bar-row {{ grid-template-columns: 96px 1fr 56px; }}
      .stat-bar-label {{ font-size: 0.70rem; }}
      .player-header h2 {{ font-size: 1.15rem; }}
      .badge {{ font-size: 0.70rem; padding: 3px 7px; }}
    }}
  </style>
</head>
<body>
<button class="sidebar-toggle" id="sidebar-toggle" onclick="toggleSidebar()" aria-label="Lista de jugadores">☰</button>
<div class="sidebar-overlay" id="sidebar-overlay" onclick="toggleSidebar()"></div>
<div class="app">

  <!-- Sidebar -->
  <aside class="sidebar" id="sidebar">
    <div class="sidebar-header">
      <h1>🎾 Legend Trajectory</h1>
      <input class="search-box" id="search" type="text" placeholder="Buscar jugador…" oninput="filterPlayers(this.value)"/>
      <div class="sort-controls">
        <button class="sort-btn active" id="sort-rank"     onclick="setSort('rank')">Ranking ↑</button>
        <button class="sort-btn"        id="sort-tour"     onclick="setSort('tour')">Circuito</button>
        <button class="sort-btn"        id="sort-nearterm" onclick="setSort('nearterm')">Próximo</button>
        <button class="sort-btn"        id="sort-score"    onclick="setSort('score')">CAPI</button>
        <button class="sort-btn"        id="sort-age"      onclick="setSort('age')">Edad</button>
      </div>
      <div class="player-count" id="player-count"></div>
    </div>
    <div class="player-list" id="player-list"></div>
  </aside>

  <!-- Main panel -->
  <main class="main" id="main-panel">
    <div class="no-player">← Selecciona un jugador</div>
  </main>

</div>

<script>
// ── Data ──────────────────────────────────────────────────────────────────────
const LEGEND_DATASETS = {legend_datasets_json};
const ALL_PLAYERS     = {players_json};
const LEGEND_NAMES    = {legend_names_json};
const N_LEG           = LEGEND_DATASETS.length;

let activePlayer = null;

window.toggleSidebar = function() {{
  document.getElementById('sidebar').classList.toggle('open');
  document.getElementById('sidebar-overlay').classList.toggle('visible');
}};
let legendsVisible = true;
let projVisible    = true;
let chart = null;
let sortKey = 'rank';
let sortDir = 1;  // 1 = asc, -1 = desc

// ── Sidebar ───────────────────────────────────────────────────────────────────
function simColor(sim) {{
  if (sim == null) return '#888';
  if (sim >= 80)   return '#119822';
  if (sim >= 65)   return '#f59e0b';
  return '#e63946';
}}

function sortPlayers(arr) {{
  return [...arr].sort((a, b) => {{
    let va, vb;
    if      (sortKey === 'rank')     {{ va = a.rank;              vb = b.rank; }}
    else if (sortKey === 'tour')     {{ va = a.tourPct   ?? -1;  vb = b.tourPct   ?? -1; }}
    else if (sortKey === 'score')    {{ va = a.capi      ?? -1;  vb = b.capi      ?? -1; }}
    else if (sortKey === 'nearterm') {{ va = a.nearTerm  ?? -1;  vb = b.nearTerm  ?? -1; }}
    else if (sortKey === 'elo')      {{ va = a.elo       ?? -1;  vb = b.elo       ?? -1; }}
    else                             {{ va = a.age;               vb = b.age; }}
    return sortDir * (va - vb);
  }});
}}

window.setSort = function(key) {{
  if (sortKey === key) {{
    sortDir = -sortDir;
  }} else {{
    sortKey = key;
    sortDir = (key === 'score' || key === 'elo' || key === 'nearterm' || key === 'tour') ? -1 : 1;
  }}
  ['rank','tour','score','nearterm','elo','age'].forEach(k => {{
    const btn = document.getElementById('sort-' + k);
    if (!btn) return;
    const arrow = sortKey === k ? (sortDir === 1 ? ' ↑' : ' ↓') : '';
    const labels = {{ rank: 'Ranking', tour: 'Circuito', score: 'CAPI', nearterm: 'Próximo', elo: 'Elo', age: 'Edad' }};
    btn.textContent = labels[k] + arrow;
    btn.classList.toggle('active', k === sortKey);
  }});
  const q = document.getElementById('search').value.toLowerCase().trim();
  const filtered = q ? ALL_PLAYERS.filter(p => p.name.toLowerCase().includes(q)) : ALL_PLAYERS;
  buildSidebar(sortPlayers(filtered));
}};

function buildSidebar(players) {{
  const list = document.getElementById('player-list');
  list.innerHTML = '';
  players.forEach(p => {{
    const div = document.createElement('div');
    div.className = 'player-item' + (p.name === activePlayer ? ' active' : '');
    div.dataset.name = p.name;
    let sc, badge;
    if (p.isLegend) {{
      sc = '#2DC7FF'; badge = '∞';
    }} else if (sortKey === 'elo') {{
      sc = eloColor(p.elo);       badge = p.elo      != null ? String(p.elo)              : '—';
    }} else if (sortKey === 'nearterm') {{
      sc = capiColor(p.nearTerm); badge = p.nearTerm != null ? p.nearTerm.toFixed(0)      : '—';
    }} else if (sortKey === 'tour') {{
      sc = simColor(p.tourPct);   badge = p.tourPct  != null ? p.tourPct.toFixed(0) + '%' : '—';
    }} else {{
      const v = p.capi ?? p.sim;  sc = simColor(v);   badge = v != null ? v.toFixed(0) : '—';
    }}
    div.innerHTML = `
      <span class="p-rank">${{p.rank}}</span>
      <div>
        <div class="p-name">${{p.name}}</div>
        <div class="p-age">Edad ${{p.age}} · ${{p.gs}} GS</div>
      </div>
      <div class="p-sim" style="color:${{sc}};border-color:${{sc}}">${{badge}}</div>
    `;
    div.onclick = () => selectPlayer(p.name);
    list.appendChild(div);
  }});
  document.getElementById('player-count').textContent = `${{players.length}} jugadores`;
}}

window.filterPlayers = function(query) {{
  const q = query.toLowerCase().trim();
  const filtered = q ? ALL_PLAYERS.filter(p => p.name.toLowerCase().includes(q)) : ALL_PLAYERS;
  buildSidebar(sortPlayers(filtered));
}};

// ── Chart ─────────────────────────────────────────────────────────────────────
function makePlayerDatasets(p) {{
  const c = p.color;
  return [
    {{ label: p.name + ' (real)', data: p.trajectory,
       borderColor: c, backgroundColor: c, borderWidth: 3,
       pointRadius: 5, pointHoverRadius: 8, tension: 0.3, fill: false, order: 1 }},
    {{ label: 'Proyección conservadora', data: p.curves.c,
       borderColor: c, backgroundColor: 'transparent',
       borderWidth: 2, borderDash: [7,5], pointRadius: 0, tension: 0.2, fill: false, order: 2 }},
    {{ label: 'Proyección media', data: p.curves.m,
       borderColor: '#f59e0b', backgroundColor: 'transparent',
       borderWidth: 2, borderDash: [7,5], pointRadius: 0, tension: 0.2, fill: false, order: 2 }},
    {{ label: 'Proyección agresiva', data: p.curves.a,
       borderColor: '#119822', backgroundColor: 'transparent',
       borderWidth: 2, borderDash: [7,5], pointRadius: 0, tension: 0.2, fill: false, order: 2 }},
  ];
}}

function initChart(p) {{
  const canvas = document.getElementById('main-chart');
  if (!canvas) return;
  if (chart) chart.destroy();
  chart = new Chart(canvas, {{
    type: 'line',
    data: {{ datasets: [...LEGEND_DATASETS, ...makePlayerDatasets(p)] }},
    options: {{
      responsive: true, maintainAspectRatio: false,
      interaction: {{ mode: 'index', intersect: false }},
      plugins: {{
        legend: {{ display: false }},
        tooltip: {{
          callbacks: {{
            title: items => `Edad: ${{items[0].parsed.x}}`,
            label: item => ` ${{item.dataset.label}}: ${{Math.round(item.parsed.y * 10) / 10}} GS`,
          }},
          backgroundColor: 'rgba(15,15,25,0.88)', padding: 10,
          titleFont: {{ size: 13, weight: 'bold' }}, bodyFont: {{ size: 12 }}, boxPadding: 4,
        }},
      }},
      scales: {{
        x: {{
          type: 'linear',
          title: {{ display: true, text: 'Edad', font: {{ size: 12 }} }},
          min: 16, max: 42,
          ticks: {{ stepSize: 1, callback: v => Number.isInteger(v) ? v : '' }},
          grid: {{ color: 'rgba(0,0,0,0.05)' }},
        }},
        y: {{
          title: {{ display: true, text: 'Grand Slams acumulados', font: {{ size: 12 }} }},
          min: 0, ticks: {{ stepSize: 2 }},
          grid: {{ color: 'rgba(0,0,0,0.05)' }},
        }},
      }},
    }},
  }});
  buildChartLegend(p);
}}

function buildChartLegend(p) {{
  const el = document.getElementById('chart-legend-row');
  if (!el || !chart) return;
  el.innerHTML = '';
  chart.data.datasets.forEach((ds, i) => {{
    if (ds.label.startsWith('Proyección')) return;
    const item = document.createElement('span');
    item.className = 'legend-item';
    item.innerHTML = `<span class="legend-dot" style="background:${{ds.borderColor}}"></span>${{ds.label}}`;
    item.dataset.idx = i;
    item.onclick = () => {{
      const meta = chart.getDatasetMeta(i);
      meta.hidden = !meta.hidden;
      item.style.opacity = meta.hidden ? '0.3' : '1';
      chart.update();
    }};
    el.appendChild(item);
  }});
}}

window.toggleGroup = function(btn, group) {{
  btn.classList.toggle('active');
  const hide = !btn.classList.contains('active');
  if (!chart) return;
  if (group === 'legends') {{
    legendsVisible = !hide;
    for (let i = 0; i < N_LEG; i++) chart.getDatasetMeta(i).hidden = hide;
  }} else {{
    projVisible = !hide;
    for (let i = N_LEG + 1; i < N_LEG + 4; i++) {{
      if (chart.data.datasets[i]) chart.getDatasetMeta(i).hidden = hide;
    }}
  }}
  chart.update();
}};

// ── Game stats bars ───────────────────────────────────────────────────────────
const STAT_LABELS = {{
  win_rate:          'Win rate %',
  serve_win_pct:     'Saque ganado %',
  return_win_pct:    'Resto ganado %',
  bp_save_pct:       'BP salvados %',
  vs_top10_win_pct:  'Win % vs Top 10',
}};

// Fixed scale ranges per stat — consistent across all players
const STAT_RANGES = {{
  win_rate:         [45, 85],
  serve_win_pct:    [55, 75],
  return_win_pct:   [28, 48],
  bp_save_pct:      [50, 80],
  vs_top10_win_pct: [20, 80],
}};

function toPct(val, key) {{
  const r = STAT_RANGES[key];
  if (!r) return Math.min(100, val);
  return Math.max(0, Math.min(100, (val - r[0]) / (r[1] - r[0]) * 100));
}}

function capiColor(v) {{
  if (v == null) return '#888';
  if (v >= 75) return '#119822';
  if (v >= 55) return '#f59e0b';
  return '#e63946';
}}

function eloColor(v) {{
  if (v == null) return '#888';
  if (v >= 2000) return '#119822';
  if (v >= 1750) return '#f59e0b';
  return '#e63946';
}}

function renderGameStats(p) {{
  const el = document.getElementById('game-stats-content');
  if (!el || !p.gameStats) return;
  const gs   = p.gameStats;
  const pD   = gs.player;
  const bD   = gs.benchmark;
  const tourPct  = p.tourPct;
  const sim      = p.sim;
  const capi     = p.capi;
  const nt       = p.nearTerm;
  const elo      = p.elo;
  const isLegend = p.isLegend;
  const tc   = isLegend ? '#2DC7FF' : simColor(tourPct);
  const sc   = simColor(sim);
  const cc   = isLegend ? '#2DC7FF' : capiColor(capi);
  const ntc  = isLegend ? '#2DC7FF' : capiColor(nt);
  const ec   = eloColor(elo);
  const tourStr = isLegend ? '∞' : (tourPct != null ? tourPct.toFixed(0) : '—');
  const capiStr = isLegend ? '∞' : (capi != null ? capi.toFixed(1) : '—');
  const ntStr   = isLegend ? '∞' : (nt   != null ? nt.toFixed(1)   : '—');

  let html = `
    <div class="potential-row">
      <div>
        <div class="potential-score" style="color:${{tc}}">${{tourStr}}<span style="font-size:1rem;font-weight:400;color:#888">${{isLegend ? '' : '/100'}}</span></div>
        <div class="potential-label">Calidad en el circuito<br><span style="font-size:0.68rem">Percentil entre los jugadores<br>actuales del top 200</span></div>
      </div>
      <div style="border-left:1px solid var(--border);padding-left:14px">
        <div class="potential-score" style="color:${{sc}}">${{sim != null ? sim.toFixed(1) : '—'}}<span style="font-size:1rem;font-weight:400;color:#888">/100</span></div>
        <div class="potential-label">Índice leyenda<br><span style="font-size:0.68rem">Perfil vs los 4 grandes a esa edad<br>(ellos puntuaban 79–87)</span></div>
      </div>
      <div style="border-left:1px solid var(--border);padding-left:14px">
        <div class="potential-score" style="color:${{cc}}">${{capiStr}}<span style="font-size:1rem;font-weight:400;color:#888">${{isLegend ? '' : '/100'}}</span></div>
        <div class="potential-label">Potencial de carrera<br><span style="font-size:0.68rem">Índice leyenda descontado<br>por edad y GS ganados</span></div>
      </div>
      <div style="border-left:1px solid var(--border);padding-left:14px">
        <div class="potential-score" style="color:${{ntc}}">${{ntStr}}<span style="font-size:1rem;font-weight:400;color:#888">${{isLegend ? '' : '/100'}}</span></div>
        <div class="potential-label">Prob. próxima<br><span style="font-size:0.68rem">Probabilidad de ganar un GS<br>en los próximos 3 años</span></div>
      </div>
      <div style="border-left:1px solid var(--border);padding-left:14px">
        <div class="potential-score" style="color:${{ec}}">${{elo != null ? elo : '—'}}</div>
        <div class="potential-label">Elo rating<br><span style="font-size:0.68rem">Historial desde 1991<br>top: Sinner ~2275</span></div>
      </div>
      <div style="font-size:0.73rem;color:#888;line-height:1.6;margin-left:auto">
        Datos: ${{p.yearsOfData}} año(s)<br>último año: ${{p.latestYear}}
      </div>
    </div>
    ${{p.phrase ? `<p style="font-size:0.83rem;color:#333;line-height:1.7;margin:0 0 14px;padding:10px 14px;background:rgba(8,96,95,0.06);border-radius:8px;border-left:3px solid #08605F">${{p.phrase}}</p>` : ''}}
    <div class="stat-bar-grid">`;

  for (const [key, label] of Object.entries(STAT_LABELS)) {{
    const pVal = pD[key];
    const bVal = bD[key];
    if (pVal == null) continue;
    const pPct = toPct(pVal, key);
    const bPct = bVal != null ? toPct(bVal, key) : null;
    const diff    = bVal != null ? (pVal - bVal).toFixed(1) : null;
    const diffStr = diff != null ? (parseFloat(diff) >= 0 ? `+${{diff}}` : diff) : '';
    const barFill = bVal == null || pVal >= bVal ? '#119822'
                  : (pVal / bVal >= 0.96)        ? '#f59e0b'
                  :                                '#e63946';
    const diffColor = barFill;
    html += `
      <div class="stat-bar-row">
        <span class="stat-bar-label">${{label}}</span>
        <div class="stat-bar-track">
          <div class="stat-bar-fill" style="width:${{pPct.toFixed(1)}}%;background:${{barFill}}"></div>
          ${{bPct != null ? `<div class="stat-bar-bench" style="left:${{bPct.toFixed(1)}}%"></div>` : ''}}
        </div>
        <span class="stat-bar-val">${{pVal}}% ${{diffStr ? `<span style="font-size:0.68rem;color:${{diffColor}}">${{diffStr}}</span>` : ''}}</span>
      </div>`;
  }}
  html += `</div>
    <p class="data-note">Línea negra = benchmark de leyenda · Verde/naranja/rojo = encima/cerca/debajo · Fuente: Jeff Sackmann / tennis_atp</p>`;

  // Surface splits
  const surfLabels = {{ Hard: 'Pista rápida', Clay: 'Tierra batida', Grass: 'Hierba' }};
  const ss = p.surfaceStats || {{}};
  const surfKeys = ['Hard','Clay','Grass'].filter(k => ss[k]);
  if (surfKeys.length > 0) {{
    let rows = '';
    for (const k of surfKeys) {{
      const d = ss[k];
      const c = d.win_pct >= 68 ? '#119822' : d.win_pct >= 52 ? '#f59e0b' : '#e63946';
      rows += `<tr>
        <td style="padding:4px 8px">${{surfLabels[k]}}</td>
        <td style="padding:4px 8px;text-align:center;color:${{c}};font-weight:600">${{d.win_pct}}%</td>
        <td style="padding:4px 8px;text-align:center;color:#888">${{d.matches}}p</td>
      </tr>`;
    }}
    html += `<div style="margin-top:12px;padding-top:10px;border-top:1px solid #e8eaf5">
      <div style="font-size:0.78rem;font-weight:600;color:#555;margin-bottom:6px">Por superficie (últimos ${{p.yearsOfData}} años)</div>
      <table style="width:100%;border-collapse:collapse;font-size:0.78rem">
        <thead><tr>
          <th style="padding:4px 8px;background:var(--bg);color:var(--muted);text-align:left">Superficie</th>
          <th style="padding:4px 8px;background:var(--bg);color:var(--muted);text-align:center">Win %</th>
          <th style="padding:4px 8px;background:var(--bg);color:var(--muted);text-align:center">Partidos</th>
        </tr></thead>
        <tbody>${{rows}}</tbody>
      </table>
    </div>`;
  }}

  el.innerHTML = html;
}}

// ── Select player ─────────────────────────────────────────────────────────────
function selectPlayer(name) {{
  activePlayer = name;
  const p = ALL_PLAYERS.find(x => x.name === name);
  if (!p) return;

  // Update sidebar highlight
  document.querySelectorAll('.player-item').forEach(el => {{
    el.classList.toggle('active', el.dataset.name === name);
  }});

  // Build main panel HTML
  const simC = simColor(p.sim);
  const compRows = p.comparison.map(c => {{
    const dc       = c.diff > 0 ? 'diff-pos' : c.diff < 0 ? 'diff-neg' : '';
    const ds       = c.diff > 0 ? `+${{c.diff}}` : String(c.diff);
    const rawDiff  = c.simAtAge != null ? (p.sim - c.simAtAge) : null;
    const scoreTxt = c.simAtAge != null ? c.simAtAge.toFixed(1) : '—';
    const diffTxt  = rawDiff != null ? (rawDiff >= 0 ? `+${{rawDiff.toFixed(1)}}` : rawDiff.toFixed(1)) : '';
    const diffCol  = rawDiff == null ? '#888' : rawDiff >= 0 ? '#119822' : '#e63946';
    return `<tr>
      <td><span class="color-dot" style="background:${{c.color}}"></span>${{c.name}}</td>
      <td class="td-c">${{c.gs}}</td>
      <td class="td-c ${{dc}}">${{ds}}</td>
      <td class="td-c" style="font-size:0.78rem;white-space:nowrap">
        <span style="color:#888">${{scoreTxt}}</span>
        ${{diffTxt ? `<span style="color:${{diffCol}};margin-left:3px;font-weight:600">${{diffTxt}}</span>` : ''}}
      </td>
    </tr>`;
  }}).join('');

  document.getElementById('main-panel').innerHTML = `
    <div class="card">
      <div class="player-header">
        <h2>${{p.name}}</h2>
        <span class="badge badge-rank">#${{p.rank}}</span>
        <span class="badge badge-gs">${{p.gs}} GS · Edad ${{p.age}}</span>
        <span class="badge badge-sim" style="background:${{p.isLegend ? '#2DC7FF' : simC}}">${{p.isLegend ? '∞ Leyenda' : (p.sim != null ? p.sim.toFixed(1) + '/100' : '?')}}</span>
      </div>
    </div>

    <div class="panels-row">
      <div class="panel">
        <h3>Proyección Grand Slams</h3>
        <div class="proj-grid">
          <div class="proj-item"><span class="proj-label">Conservadora</span><span class="proj-value blue">${{p.proj.c}}</span></div>
          <div class="proj-item"><span class="proj-label">Media</span><span class="proj-value orange">${{p.proj.m}}</span></div>
          <div class="proj-item"><span class="proj-label">Agresiva</span><span class="proj-value green">${{p.proj.a}}</span></div>
        </div>
      </div>
      <div class="panel">
        <h3>A la misma edad — leyendas históricas</h3>
        <div style="overflow-x:auto">
          <table style="min-width:280px">
            <thead><tr><th>Jugador</th><th class="td-c">GS</th><th class="td-c">Dif.</th><th class="td-c">Score</th></tr></thead>
            <tbody>${{compRows || '<tr><td colspan="4" style="color:#aaa">Sin datos</td></tr>'}}</tbody>
          </table>
        </div>
      </div>
    </div>

    <div class="card">
      <h3 style="font-size:0.88rem;color:#555;margin-bottom:12px">Perfil de juego vs benchmark de leyenda</h3>
      <div id="game-stats-content"></div>
    </div>

    <div class="chart-panel card" style="padding:16px">
      <h3 style="font-size:0.88rem;color:#555;margin-bottom:4px">Trayectoria por edad</h3>
      <p class="chart-sub">Leyendas como referencia. Proyecciones punteadas basadas en el Potential Score.</p>
      <div class="chart-controls">
        <button class="ctrl-btn active" id="btn-legends" onclick="toggleGroup(this,'legends')">Leyendas</button>
        <button class="ctrl-btn active" id="btn-proj"    onclick="toggleGroup(this,'proj')">Proyecciones</button>
      </div>
      <div class="chart-wrap"><canvas id="main-chart"></canvas></div>
      <div class="chart-legend-row" id="chart-legend-row"></div>
      <p class="data-note">Datos verificados via Jeff Sackmann / tennis_atp · Regresión: 14 jugadores históricos (Federer, Nadal, Djokovic, Sampras + 10 ganadores de 1-3 GS)</p>
    </div>
  `;

  initChart(p);
  renderGameStats(p);
  // Re-apply toggle states
  if (!legendsVisible) for (let i = 0; i < N_LEG; i++) chart.getDatasetMeta(i).hidden = true;
  if (!projVisible)    for (let i = N_LEG+1; i < N_LEG+4; i++) if (chart.data.datasets[i]) chart.getDatasetMeta(i).hidden = true;
  if (!legendsVisible || !projVisible) chart.update();
  // Auto-close sidebar on mobile
  if (window.innerWidth <= 680 && document.getElementById('sidebar').classList.contains('open')) {{
    toggleSidebar();
  }}
}}

// ── Init ──────────────────────────────────────────────────────────────────────
buildSidebar(sortPlayers(ALL_PLAYERS));
if (ALL_PLAYERS.length > 0) selectPlayer(sortPlayers(ALL_PLAYERS)[0].name);
</script>
</body>
</html>"""


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Build the ATP scout index.html")
    parser.add_argument("--top",       type=int, default=200, help="Number of ranked players (default: 200)")
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
    global _REGRESSION_TARGETS, _ALL_STATS, _LEGEND_SIM_BY_AGE
    _ALL_STATS = all_historical_stats
    _REGRESSION_TARGETS = sf.build_regression_targets(all_historical_stats)
    print(f"  Regression targets: {len(_REGRESSION_TARGETS)} historical players")

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
    print(f"  Elo computed for {len(elo_ratings)} players")

    # 6. Build player records
    print("Building player records...")
    players_data = []
    skipped = 0
    for p in players:
        pid  = p["player_id"]
        stats_by_year = batch_stats.get(pid)
        if not stats_by_year:
            skipped += 1
            continue
        record = build_player_record(p, stats_by_year, benchmark, gs_wins, elo_ratings)
        if record:
            players_data.append(record)

    print(f"  {len(players_data)} players with data ({skipped} skipped — no recent matches)")

    # 6b. Tour percentile: rank each player's sim among current players (0-100)
    import bisect
    valid_sims = sorted(r["sim"] for r in players_data if r.get("sim") is not None)
    n_valid = len(valid_sims)
    for r in players_data:
        if r.get("sim") is not None:
            idx = bisect.bisect_left(valid_sims, r["sim"])
            r["tourPct"] = round(idx / n_valid * 100, 1)
        else:
            r["tourPct"] = None

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

    # 8. Render and save
    EXAMPLES_DIR.mkdir(parents=True, exist_ok=True)
    html = render_index(players_data, legend_datasets)
    out_path = Path(args.output)
    out_path.write_text(html, encoding="utf-8")
    print(f"\nDone! → {out_path}")
    print(f"  {len(players_data)} jugadores · abre el archivo en tu navegador")


if __name__ == "__main__":
    main()
