import argparse
import json
import math
import sys
from html import escape
from pathlib import Path

SRC_DIR = Path(__file__).resolve().parent
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

DATA_PATH = Path(__file__).resolve().parents[1] / "data" / "legend_trajectory_dataset.json"

CATEGORY_THRESHOLDS = [30, 50, 70, 85]
CATEGORIES = [
    "No leyenda",
    "Multi Slam",
    "Leyenda posible",
    "Territorio leyenda",
    "Leyenda histórica potencial",
]

ARCHETYPES = [
    "Prodigio precoz",
    "Dominador longevo",
    "Crecimiento progresivo",
    "Especialista dominante",
    "Completo multisuelo",
]

# Historical GS trajectories used as background comparison lines.
# Format: {player: [(age, cumulative_gs), ...]}
# Sources: ATP records; Alcaraz verified via Tennis API (ID 68074).
GS_TRAJECTORIES = {
    "Roger Federer": [
        (20, 0), (21, 1), (22, 4), (23, 6), (24, 9), (25, 12),
        (26, 13), (27, 15), (28, 16), (29, 16), (30, 17),
        (31, 17), (32, 17), (33, 17), (34, 17), (35, 19), (36, 20),
        (37, 20), (38, 20), (39, 20), (40, 20),
    ],
    "Rafael Nadal": [
        (18, 0), (19, 1), (20, 2), (21, 3), (22, 5), (23, 6),
        (24, 9), (25, 10), (26, 11), (27, 13), (28, 14),
        (29, 14), (30, 14), (31, 16), (32, 17), (33, 19),
        (34, 20), (35, 20), (36, 22), (37, 22),
    ],
    "Novak Djokovic": [
        (19, 0), (20, 1), (21, 1), (22, 1), (23, 4), (24, 5),
        (25, 6), (26, 8), (27, 11), (28, 13), (29, 13),
        (30, 15), (31, 17), (32, 18), (33, 21), (34, 22),
        (35, 24), (36, 24), (37, 24),
    ],
    "Pete Sampras": [
        (18, 1), (19, 1), (20, 1), (21, 3), (22, 5),
        (23, 7), (24, 9), (25, 11), (26, 12), (27, 13),
        (28, 13), (29, 13), (30, 14),
    ],
    "Carlos Alcaraz": [
        (18, 0), (19, 1), (20, 2), (21, 4), (22, 7),
    ],
    "Jannik Sinner": [
        (20, 0), (21, 0), (22, 2), (23, 3),
    ],
}

LEGEND_COLORS = {
    "Roger Federer":   "#1f77b4",
    "Rafael Nadal":    "#d62728",
    "Novak Djokovic":  "#2ca02c",
    "Pete Sampras":    "#9467bd",
    "Carlos Alcaraz":  "#e377c2",
    "Jannik Sinner":   "#17becf",
}

# Players shown as thin background reference lines (the historical legends)
BACKGROUND_LEGENDS = ["Roger Federer", "Rafael Nadal", "Novak Djokovic", "Pete Sampras"]


def safe_float(value):
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def normalize(value, maximum, default=0.0):
    value = safe_float(value)
    if value is None or maximum is None or maximum <= 0:
        return float(default)
    return max(0.0, min(1.0, value / maximum))


def compute_legend_trajectory_score(player):
    gs_total = safe_float(player.get("grand_slams_total")) or 0.0
    age = safe_float(player.get("age")) or 0.0
    best_ranking = safe_float(player.get("best_ranking_by_age"))
    weeks_at_1 = safe_float(player.get("weeks_at_number_1_by_age")) or 0.0
    masters = safe_float(player.get("masters_1000_titles")) or 0.0
    finals_titles = safe_float(player.get("atp_finals_titles")) or 0.0
    olympic_medals = sum(
        safe_float(player.get(key)) or 0.0
        for key in ["olympic_gold", "olympic_silver", "olympic_bronze"]
    )
    atp_500 = safe_float(player.get("atp_500_titles")) or 0.0
    atp_250 = safe_float(player.get("atp_250_titles")) or 0.0
    gs_finals = safe_float(player.get("grand_slam_finals")) or 0.0
    gs_semis = safe_float(player.get("grand_slam_semifinals")) or 0.0
    top_10_wins = safe_float(player.get("top_10_wins")) or 0.0
    hard_rate = safe_float(player.get("hard_win_rate"))
    clay_rate = safe_float(player.get("clay_win_rate"))
    grass_rate = safe_float(player.get("grass_win_rate"))
    davis = safe_float(player.get("davis_cup_titles")) or 0.0

    expected_pace = max(1.0, age / 4.0)
    gs_pace_score = normalize(gs_total, expected_pace)

    ranking_score = 1.0 - normalize(best_ranking, 20.0) if best_ranking else 0.0
    weeks_score = normalize(weeks_at_1, 52.0)
    ranking_component = (ranking_score * 0.6) + (weeks_score * 0.4)

    big_title_component = (
        normalize(masters, 8.0) * 0.5
        + normalize(finals_titles, 3.0) * 0.3
        + normalize(olympic_medals, 2.0) * 0.2
    )
    titles_component = normalize(atp_500, 8.0) * 0.55 + normalize(atp_250, 10.0) * 0.45
    consistency_component = normalize(gs_finals, 4.0) * 0.55 + normalize(gs_semis, 6.0) * 0.45
    top10_component = normalize(top_10_wins, 25.0)

    surface_scores = [normalize(r, 1.0) for r in (hard_rate, clay_rate, grass_rate) if r is not None]
    versatility_component = sum(surface_scores) / len(surface_scores) if surface_scores else 0.0
    davis_component = normalize(davis, 2.0)

    weighted = (
        gs_pace_score * 30
        + ranking_component * 20
        + big_title_component * 15
        + titles_component * 10
        + consistency_component * 10
        + top10_component * 7.5
        + versatility_component * 5
        + davis_component * 2.5
    )
    return round(weighted, 2)


def classify_category(score):
    for threshold, category in zip(CATEGORY_THRESHOLDS, CATEGORIES):
        if score < threshold:
            return category
    return CATEGORIES[-1]


def detect_archetype(player):
    age = safe_float(player.get("age")) or 0.0
    gs_total = safe_float(player.get("grand_slams_total")) or 0.0
    best_ranking = safe_float(player.get("best_ranking_by_age"))
    top_10_wins = safe_float(player.get("top_10_wins")) or 0.0
    hard_rate = safe_float(player.get("hard_win_rate"))
    clay_rate = safe_float(player.get("clay_win_rate"))
    grass_rate = safe_float(player.get("grass_win_rate"))

    if age <= 21 and gs_total >= 1 and (best_ranking == 1 or gs_total >= 2):
        return "Prodigio precoz"
    if best_ranking == 1 and top_10_wins >= 20 and gs_total >= 3:
        return "Dominador longevo"
    if gs_total <= 2 and top_10_wins >= 10 and (safe_float(player.get("atp_500_titles")) or 0) >= 2:
        return "Crecimiento progresivo"
    surface_values = [v for v in (hard_rate, clay_rate, grass_rate) if v is not None]
    if surface_values and max(surface_values) >= 0.85:
        if sum(1 for v in surface_values if v >= 0.75) == 1:
            return "Especialista dominante"
    if len(surface_values) == 3 and all(v >= 0.70 for v in surface_values):
        return "Completo multisuelo"
    return "Crecimiento progresivo"


def compare_against_history(player, dataset):
    age = player.get("age")
    comparisons = []
    for record in dataset:
        if record.get("age") == age and record.get("player_name") != player.get("player_name"):
            score = compute_legend_trajectory_score(record)
            comparisons.append({
                "player_name": record["player_name"],
                "year": record.get("year"),
                "score": score,
                "grand_slams_total": record.get("grand_slams_total"),
            })
    comparisons.sort(key=lambda x: x["score"], reverse=True)
    return comparisons[:5]


def project_grand_slams(player, score):
    base = safe_float(player.get("grand_slams_total")) or 0.0
    age = safe_float(player.get("age")) or 0.0
    years_left = max(0.0, 35.0 - age)
    if years_left <= 0:
        return {"conservadora": int(base), "media": int(base), "agresiva": int(base)}

    factor = max(0.0, min(1.0, score / 100.0))
    conservative_delta = round(min(25.0 - base, factor * 0.40 * years_left))
    medium_delta = round(min(25.0 - base, factor * 0.65 * years_left))
    aggressive_delta = round(min(30.0 - base, factor * 0.90 * years_left))

    return {
        "conservadora": int(base + conservative_delta),
        "media": int(base + medium_delta),
        "agresiva": int(base + aggressive_delta),
    }


def _realistic_projection(
    current_age: float,
    current_gs: float,
    end_age: int,
    target_gs: int,
    peak_age: float = 27.0,
) -> list:
    """
    Builds a projection curve where GS gains are always front-loaded:
    more wins per year now, fewer as the player ages.
    Uses exponential decay from current_age, independent of peak_age
    (which is kept as a parameter for future flexibility).

    Returns a list of {age, gs} dicts from current_age to end_age.
    """
    if target_gs <= current_gs or end_age <= current_age:
        return [{"age": float(a), "gs": float(current_gs)}
                for a in range(int(current_age), end_age + 1)]

    total_gain = target_gs - current_gs
    # Half-year steps for a smooth curve
    n_steps = int((end_age - current_age) * 2)
    step = (end_age - current_age) / n_steps
    future_ages = [current_age + (i + 1) * step for i in range(n_steps)]

    # Age-dependent decay: older players concentrate gains in the near term
    # and plateau faster. At 22 → gentle slope; at 32 → near-step then flat.
    decay_rate = 0.07 + 0.020 * max(0, current_age - 20)
    weights = [math.exp(-decay_rate * (a - current_age)) for a in future_ages]
    total_w = sum(weights)

    points = [{"age": round(current_age, 1), "gs": round(float(current_gs), 2)}]
    gs = float(current_gs)
    for a, w in zip(future_ages, weights):
        gs += total_gain * (w / total_w)
        points.append({"age": round(a, 1), "gs": round(min(gs, float(target_gs)), 2)})
    return points


def _gs_at_age(trajectory: list, age: float):
    """Returns the GS total for a player at a given age (floor match)."""
    traj_dict = dict(trajectory)
    ages = sorted(traj_dict.keys())
    result = None
    for a in ages:
        if a <= age:
            result = traj_dict[a]
    return result


def _project_from_profile(current_age, current_gs, profile_similarity, end_age=37):
    """
    Returns (conservadora, media, agresiva) GS projections driven by profile
    similarity, with age and GS-drought penalties for older/titleless players.
    """
    if current_age >= end_age:
        return {"conservadora": int(current_gs), "media": int(current_gs), "agresiva": int(current_gs)}
    years_left = max(0, end_age - current_age)
    f = max(0.0, min(1.0, (profile_similarity or 0) / 100.0))

    # Age factor: peak at ≤25, exponential decay after
    age_factor = 1.0 if current_age <= 25 else math.exp(-0.14 * (current_age - 25))

    # GS drought: no titles after 23 is a strong negative signal
    drought = 1.0 if (current_gs > 0 or current_age <= 23) else math.exp(-0.20 * (current_age - 23))

    eff = f * age_factor * drought
    return {
        "conservadora": int(current_gs + round(min(25 - current_gs, eff * 0.35 * years_left))),
        "media":        int(current_gs + round(min(25 - current_gs, eff * 0.60 * years_left))),
        "agresiva":     int(current_gs + round(min(30 - current_gs, eff * 0.88 * years_left))),
    }


def _build_all_players_js(players: list, dataset: list, game_stats_map: dict = None) -> str:
    """
    Builds a JS object with all analysis-player data for the interactive report.
    game_stats_map: optional {player_name: lookup_result} from stats_fetcher.lookup_player()
    """
    if game_stats_map is None:
        game_stats_map = {}

    player_js_entries = {}
    for player in players:
        name = player.get("player_name", "Jugador")
        score = compute_legend_trajectory_score(player)
        category = classify_category(score)
        archetype = detect_archetype(player)
        current_age = safe_float(player.get("age")) or 0.0
        current_gs = safe_float(player.get("grand_slams_total")) or 0.0
        color = LEGEND_COLORS.get(name, "#888888")
        end_age = 37

        # Game stats enrichment from stats_fetcher lookup
        gs_lookup = game_stats_map.get(name)
        profile_sim = gs_lookup["profile_similarity"] if gs_lookup else None

        # Projections: blend LTS-based and profile-based when both available
        lts_proj  = project_grand_slams(player, score)
        if profile_sim is not None and score < 30:
            # Low LTS (few achievements) but we have game stats — trust profile more
            projections = _project_from_profile(current_age, current_gs, profile_sim, end_age)
        elif profile_sim is not None:
            # Blend: 60% LTS, 40% profile
            prof_proj = _project_from_profile(current_age, current_gs, profile_sim, end_age)
            projections = {
                k: int(round(lts_proj[k] * 0.6 + prof_proj[k] * 0.4))
                for k in ("conservadora", "media", "agresiva")
            }
        else:
            projections = lts_proj

        trajectory = GS_TRAJECTORIES.get(name)
        if trajectory is None and gs_lookup and gs_lookup.get("gs_trajectory"):
            trajectory = gs_lookup["gs_trajectory"]
        if not trajectory:
            trajectory = [(int(current_age), int(current_gs))]
        traj_points = [{"x": age, "y": gs} for age, gs in trajectory]

        proj_conservative = _realistic_projection(current_age, current_gs, end_age, projections["conservadora"])
        proj_medium       = _realistic_projection(current_age, current_gs, end_age, projections["media"])
        proj_aggressive   = _realistic_projection(current_age, current_gs, end_age, projections["agresiva"])

        # Comparison at same age vs legends
        comparison = []
        for leg_name in BACKGROUND_LEGENDS:
            if leg_name == name:
                continue
            gs_val = _gs_at_age(GS_TRAJECTORIES[leg_name], current_age)
            if gs_val is not None:
                comparison.append({
                    "name": leg_name,
                    "gs": gs_val,
                    "diff": int(gs_val - current_gs),
                    "color": LEGEND_COLORS.get(leg_name, "#aaa"),
                })
        comparison.sort(key=lambda x: x["gs"], reverse=True)

        # Game stats for the panel (latest year, All surfaces)
        game_stats_js = None
        if gs_lookup:
            ls = gs_lookup.get("latest_stats", {})
            bk = gs_lookup.get("benchmark_at_age", {})

            def pct(v):
                return round(v * 100, 1) if v is not None else None

            game_stats_js = {
                "profile_similarity": profile_sim,
                "latest_year":        gs_lookup.get("latest_year"),
                "years_of_data":      len(gs_lookup.get("stats_by_year", {})),
                "player": {
                    "serve_win_pct":      pct(ls.get("serve_win_pct")),
                    "return_win_pct":     pct(ls.get("return_win_pct")),
                    "bp_save_pct":        pct(ls.get("bp_save_pct")),
                    "bp_conversion_pct":  pct(ls.get("bp_conversion_pct")),
                    "first_serve_in_pct": pct(ls.get("first_serve_in_pct")),
                    "dominance_ratio":    round(ls.get("dominance_ratio") or 0, 3),
                    "win_rate":           pct(ls.get("win_rate")),
                },
                "benchmark": {
                    "serve_win_pct":      pct(bk.get("serve_win_pct")),
                    "return_win_pct":     pct(bk.get("return_win_pct")),
                    "bp_save_pct":        pct(bk.get("bp_save_pct")),
                    "bp_conversion_pct":  pct(bk.get("bp_conversion_pct")),
                    "first_serve_in_pct": pct(bk.get("first_serve_in_pct")),
                    "dominance_ratio":    round(bk.get("dominance_ratio") or 0, 3),
                    "win_rate":           pct(bk.get("win_rate")),
                },
            }

        player_js_entries[name] = {
            "score":     score,
            "category":  category,
            "archetype": archetype,
            "age":       int(current_age),
            "gs":        int(current_gs),
            "color":     color,
            "trajectory": traj_points,
            "projections": {
                "conservative": [{"x": p["age"], "y": p["gs"]} for p in proj_conservative],
                "medium":       [{"x": p["age"], "y": p["gs"]} for p in proj_medium],
                "aggressive":   [{"x": p["age"], "y": p["gs"]} for p in proj_aggressive],
            },
            "projFinal":  projections,
            "comparison": comparison,
            "gameStats":  game_stats_js,
        }

    return json.dumps(player_js_entries, ensure_ascii=False)


def render_html_report(players: list, dataset: list, game_stats_map: dict = None) -> str:
    if not players:
        return "<p>No hay jugadores para mostrar.</p>"

    if game_stats_map is None:
        game_stats_map = {}

    first_player = players[0].get("player_name", "Jugador")

    # Legend background datasets (always visible)
    legend_datasets = []
    for name in BACKGROUND_LEGENDS:
        traj = GS_TRAJECTORIES.get(name, [])
        color = LEGEND_COLORS.get(name, "#aaaaaa")
        legend_datasets.append({
            "label": name,
            "data": [{"x": age, "y": gs} for age, gs in traj],
            "borderColor": color,
            "backgroundColor": color,
            "borderWidth": 1.5,
            "pointRadius": 3,
            "pointHoverRadius": 6,
            "tension": 0.35,
            "fill": False,
            "order": 5,
        })

    legend_datasets_json = json.dumps(legend_datasets, ensure_ascii=False)
    all_players_json = _build_all_players_js(players, dataset, game_stats_map)
    first_player_esc = escape(first_player)
    first_player_json = json.dumps(first_player)

    # Tab buttons HTML
    tab_buttons = ""
    for p in players:
        name = p.get("player_name", "Jugador")
        color = LEGEND_COLORS.get(name, "#888")
        active = 'class="player-tab active"' if name == first_player else 'class="player-tab"'
        tab_buttons += (
            f'<button {active} onclick="switchPlayer({json.dumps(name)})" '
            f'style="--tab-color:{color}">{escape(name)}</button>'
        )

    return f"""<!DOCTYPE html>
<html lang="es">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>Legend Trajectory Tennis</title>
  <script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.4/dist/chart.umd.min.js"></script>
  <style>
    *, *::before, *::after {{ box-sizing: border-box; margin: 0; padding: 0; }}
    body {{ font-family: 'Segoe UI', system-ui, sans-serif; line-height: 1.5; background: #f0f2f8; color: #222; }}
    .container {{ max-width: 980px; margin: 0 auto; padding: 28px 20px; }}
    h1 {{ font-size: 1.9rem; color: #1a1a2e; margin-bottom: 4px; }}
    .app-subtitle {{ color: #666; margin-bottom: 24px; font-size: 0.95rem; }}
    .card {{ background: #fff; border: 1px solid #dde1f0; border-radius: 14px; padding: 22px; box-shadow: 0 4px 18px rgba(0,0,0,0.07); }}

    /* Player tabs */
    .player-tabs {{ display: flex; gap: 10px; flex-wrap: wrap; margin-bottom: 20px; }}
    .player-tab {{
      padding: 8px 18px; border-radius: 24px; border: 2px solid var(--tab-color, #888);
      background: #fff; color: #333; cursor: pointer; font-size: 0.9rem; font-weight: 600;
      transition: all 0.18s;
    }}
    .player-tab:hover {{ background: color-mix(in srgb, var(--tab-color) 15%, white); }}
    .player-tab.active {{ background: var(--tab-color, #888); color: #fff; }}
    .add-player-hint {{ font-size: 0.82rem; color: #888; margin-left: 6px; align-self: center; }}

    /* Player header */
    .player-header {{ display: flex; flex-wrap: wrap; align-items: center; gap: 10px; margin-bottom: 18px; }}
    .player-header h2 {{ font-size: 1.5rem; color: #1a1a2e; }}
    .badge {{ padding: 4px 12px; border-radius: 20px; font-size: 0.83rem; font-weight: 600; }}
    .badge-score {{ background: #e8ecff; color: #2233aa; }}
    .badge-category {{ background: #fff3e0; color: #e65c00; }}
    .badge-archetype {{ background: #e8f5e9; color: #2e7d32; }}
    .badge-gs {{ background: #1a1a2e; color: #fff; font-size: 0.9rem; }}

    /* Panels */
    .panels-row {{ display: grid; grid-template-columns: 1fr 1fr; gap: 14px; margin-bottom: 18px; }}
    @media (max-width: 640px) {{ .panels-row {{ grid-template-columns: 1fr; }} }}
    .panel {{ background: #f8f9ff; border: 1px solid #e2e5f5; border-radius: 10px; padding: 14px; }}
    .panel h3 {{ font-size: 0.92rem; color: #444; margin-bottom: 10px; }}
    .proj-grid {{ display: flex; gap: 10px; }}
    .proj-item {{ flex: 1; text-align: center; background: #fff; border-radius: 8px; padding: 8px 4px; border: 1px solid #e0e3f5; }}
    .proj-label {{ display: block; font-size: 0.72rem; color: #777; margin-bottom: 3px; text-transform: uppercase; letter-spacing: 0.03em; }}
    .proj-value {{ font-size: 1.6rem; font-weight: 700; }}
    .blue {{ color: #1f77b4; }} .orange {{ color: #e07b00; }} .green {{ color: #2ca02c; }}

    /* Chart */
    .chart-panel {{ background: #f8f9ff; border: 1px solid #e2e5f5; border-radius: 10px; padding: 16px; }}
    .chart-panel h3 {{ font-size: 0.92rem; color: #444; margin-bottom: 4px; }}
    .chart-subtitle {{ font-size: 0.82rem; color: #777; margin-bottom: 12px; }}
    .chart-controls {{ display: flex; gap: 8px; margin-bottom: 10px; flex-wrap: wrap; align-items: center; }}
    .ctrl-btn {{
      padding: 4px 13px; border: 1px solid #bbb; border-radius: 20px;
      background: #fff; cursor: pointer; font-size: 0.80rem; color: #555; transition: all 0.15s;
    }}
    .ctrl-btn.active {{ background: #1a1a2e; color: #fff; border-color: #1a1a2e; }}
    .chart-wrapper {{ position: relative; height: 380px; }}
    .chart-legend-row {{ display: flex; flex-wrap: wrap; gap: 8px; margin-top: 10px; }}
    .legend-item {{
      display: inline-flex; align-items: center; gap: 5px; font-size: 0.79rem;
      color: #444; cursor: pointer; padding: 2px 0; transition: opacity 0.15s;
    }}
    .legend-dot {{ width: 10px; height: 10px; border-radius: 50%; flex-shrink: 0; }}

    /* Tables */
    table {{ width: 100%; border-collapse: collapse; font-size: 0.86rem; margin-top: 6px; }}
    th, td {{ padding: 6px 8px; border-bottom: 1px solid #e8eaf0; text-align: left; }}
    th {{ background: #eef0ff; font-weight: 600; color: #333; }}
    .td-center {{ text-align: center; }}
    .color-dot {{ display: inline-block; width: 9px; height: 9px; border-radius: 50%; margin-right: 4px; vertical-align: middle; }}
    .diff-pos {{ color: #d62728; font-weight: 600; }}
    .diff-neg {{ color: #2ca02c; font-weight: 600; }}

    .data-note {{ font-size: 0.76rem; color: #999; margin-top: 10px; font-style: italic; }}

    /* Game stats panel */
    .game-stats-panel {{ background: #f8f9ff; border: 1px solid #e2e5f5; border-radius: 10px; padding: 16px; margin-bottom: 14px; }}
    .game-stats-panel h3 {{ font-size: 0.92rem; color: #444; margin-bottom: 12px; }}
    .potential-row {{ display: flex; align-items: center; gap: 12px; margin-bottom: 14px; }}
    .potential-score {{
      font-size: 2rem; font-weight: 800; line-height: 1;
      background: linear-gradient(135deg, #1a1a2e, #4455cc);
      -webkit-background-clip: text; -webkit-text-fill-color: transparent;
    }}
    .potential-label {{ font-size: 0.78rem; color: #777; }}
    .stat-bar-grid {{ display: flex; flex-direction: column; gap: 7px; }}
    .stat-bar-row {{ display: grid; grid-template-columns: 130px 1fr 60px; gap: 8px; align-items: center; }}
    .stat-bar-label {{ font-size: 0.78rem; color: #555; white-space: nowrap; }}
    .stat-bar-track {{ background: #e8eaf0; border-radius: 4px; height: 10px; position: relative; overflow: visible; }}
    .stat-bar-fill {{ height: 100%; border-radius: 4px; transition: width 0.4s; }}
    .stat-bar-bench {{ position: absolute; top: -3px; width: 2px; height: 16px; background: #1a1a2e; border-radius: 1px; }}
    .stat-bar-val {{ font-size: 0.80rem; font-weight: 600; text-align: right; }}
    .bar-above {{ background: #2ca02c; }}
    .bar-below {{ background: #e377c2; }}
    .no-game-stats {{ color: #aaa; font-size: 0.85rem; font-style: italic; padding: 8px 0; }}
  </style>
</head>
<body>
<div class="container">
  <h1>Legend Trajectory Tennis</h1>
  <p class="app-subtitle">Análisis de trayectorias hacia la leyenda del tenis masculino.</p>

  <div class="card">
    <div class="player-tabs" id="player-tabs">
      {tab_buttons}
      <span class="add-player-hint">+ <code>--input-json jugador.json --output-html informe.html</code></span>
    </div>

    <!-- Dynamic player header -->
    <div class="player-header" id="player-header"></div>

    <!-- Projections + comparison table side by side -->
    <div class="panels-row">
      <div class="panel">
        <h3>Proyección de Grand Slams</h3>
        <div class="proj-grid" id="proj-grid"></div>
      </div>
      <div class="panel">
        <h3>A la misma edad — leyendas históricas</h3>
        <table>
          <thead><tr><th>Jugador</th><th class="td-center">GS</th><th class="td-center">Diferencia</th></tr></thead>
          <tbody id="comparison-tbody"></tbody>
        </table>
      </div>
    </div>

    <!-- Game stats panel -->
    <div class="game-stats-panel" id="game-stats-panel">
      <h3>Perfil de juego vs benchmark de leyenda</h3>
      <div id="game-stats-content"></div>
    </div>

    <!-- Chart -->
    <div class="chart-panel">
      <h3>Trayectoria por edad</h3>
      <p class="chart-subtitle">
        Leyendas históricas como referencia (líneas finas). Jugador analizado en negrita. Proyecciones punteadas.
        Curva realista: más Grand Slams en los años de pico, menos con la edad.
      </p>
      <div class="chart-controls">
        <button class="ctrl-btn active" id="btn-legends" onclick="toggleGroup(this,'legends')">Leyendas</button>
        <button class="ctrl-btn active" id="btn-proj" onclick="toggleGroup(this,'proj')">Proyecciones</button>
      </div>
      <div class="chart-wrapper"><canvas id="main-chart"></canvas></div>
      <div class="chart-legend-row" id="chart-legend-row"></div>
      <p class="data-note">
        Datos verificados via Tennis API (Alcaraz) y registros ATP históricos.
        Trayectorias de jugadores retirados son definitivas.
      </p>
    </div>
  </div>
</div>

<script>
const LEGEND_DATASETS = {legend_datasets_json};
const ALL_PLAYERS = {all_players_json};
const LEGEND_NAMES = {json.dumps(BACKGROUND_LEGENDS)};
const N_LEGENDS = LEGEND_DATASETS.length;

let activePlayer = {first_player_json};
let legendsVisible = true;
let projVisible = true;

// ── Chart setup ──────────────────────────────────────────────────────────────
function makePlayerDatasets(name) {{
  const p = ALL_PLAYERS[name];
  if (!p) return [];
  const c = p.color;
  return [
    {{
      label: name + ' (real)',
      data: p.trajectory,
      borderColor: c, backgroundColor: c,
      borderWidth: 3, pointRadius: 5, pointHoverRadius: 8,
      tension: 0.3, fill: false, order: 1,
    }},
    {{
      label: 'Proyección conservadora',
      data: p.projections.conservative,
      borderColor: c, backgroundColor: 'transparent',
      borderWidth: 2, borderDash: [7, 5], pointRadius: 0, pointHoverRadius: 4,
      tension: 0.2, fill: false, order: 2,
    }},
    {{
      label: 'Proyección media',
      data: p.projections.medium,
      borderColor: '#e07b00', backgroundColor: 'transparent',
      borderWidth: 2, borderDash: [7, 5], pointRadius: 0, pointHoverRadius: 4,
      tension: 0.2, fill: false, order: 2,
    }},
    {{
      label: 'Proyección agresiva',
      data: p.projections.aggressive,
      borderColor: '#2ca02c', backgroundColor: 'transparent',
      borderWidth: 2, borderDash: [7, 5], pointRadius: 0, pointHoverRadius: 4,
      tension: 0.2, fill: false, order: 2,
    }},
  ];
}}

const initialDatasets = [...LEGEND_DATASETS, ...makePlayerDatasets(activePlayer)];

const chart = new Chart(document.getElementById('main-chart'), {{
  type: 'line',
  data: {{ datasets: initialDatasets }},
  options: {{
    responsive: true,
    maintainAspectRatio: false,
    interaction: {{ mode: 'index', intersect: false }},
    plugins: {{
      legend: {{ display: false }},
      tooltip: {{
        callbacks: {{
          title: items => `Edad: ${{items[0].parsed.x}}`,
          label: item => {{
            const v = Math.round(item.parsed.y * 10) / 10;
            return ` ${{item.dataset.label}}: ${{v}} GS`;
          }},
        }},
        backgroundColor: 'rgba(15,15,25,0.88)',
        padding: 10, titleFont: {{ size: 13, weight: 'bold' }},
        bodyFont: {{ size: 12 }}, boxPadding: 4,
      }},
    }},
    scales: {{
      x: {{
        type: 'linear',
        title: {{ display: true, text: 'Edad', font: {{ size: 13 }} }},
        min: 17, max: 42,
        ticks: {{ stepSize: 1, callback: v => Number.isInteger(v) ? v : '' }},
        grid: {{ color: 'rgba(0,0,0,0.05)' }},
      }},
      y: {{
        title: {{ display: true, text: 'Grand Slams acumulados', font: {{ size: 13 }} }},
        min: 0,
        ticks: {{ stepSize: 2 }},
        grid: {{ color: 'rgba(0,0,0,0.05)' }},
      }},
    }},
  }},
}});

// ── Custom legend ─────────────────────────────────────────────────────────────
function buildLegend() {{
  const el = document.getElementById('chart-legend-row');
  el.innerHTML = '';
  chart.data.datasets.forEach((ds, i) => {{
    if (ds.label.startsWith('Proyección')) return;
    const item = document.createElement('span');
    item.className = 'legend-item';
    const dot = `<span class="legend-dot" style="background:${{ds.borderColor}}"></span>`;
    item.innerHTML = dot + ds.label;
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

// ── Toggle groups ─────────────────────────────────────────────────────────────
window.toggleGroup = function(btn, group) {{
  btn.classList.toggle('active');
  const hide = !btn.classList.contains('active');
  if (group === 'legends') {{
    legendsVisible = !hide;
    for (let i = 0; i < N_LEGENDS; i++) {{
      chart.getDatasetMeta(i).hidden = hide;
    }}
  }} else {{
    projVisible = !hide;
    // projection datasets are indices N+1, N+2, N+3
    for (let i = N_LEGENDS + 1; i < N_LEGENDS + 4; i++) {{
      if (chart.data.datasets[i]) chart.getDatasetMeta(i).hidden = hide;
    }}
  }}
  chart.update();
}};

// ── Player switch ─────────────────────────────────────────────────────────────
function updateInfoPanel(name) {{
  const p = ALL_PLAYERS[name];
  if (!p) return;

  // Header
  document.getElementById('player-header').innerHTML = `
    <h2>${{name}}</h2>
    <span class="badge badge-gs">${{p.gs}} GS · Edad ${{p.age}}</span>
    <span class="badge badge-score">Score: ${{p.score}}</span>
    <span class="badge badge-category">${{p.category}}</span>
    <span class="badge badge-archetype">${{p.archetype}}</span>
  `;

  // Projections
  document.getElementById('proj-grid').innerHTML = `
    <div class="proj-item"><span class="proj-label">Conservadora</span><span class="proj-value blue">${{p.projFinal.conservadora}}</span></div>
    <div class="proj-item"><span class="proj-label">Media</span><span class="proj-value orange">${{p.projFinal.media}}</span></div>
    <div class="proj-item"><span class="proj-label">Agresiva</span><span class="proj-value green">${{p.projFinal.agresiva}}</span></div>
  `;

  // Comparison table
  const rows = p.comparison.map(c => {{
    const diffClass = c.diff > 0 ? 'diff-pos' : c.diff < 0 ? 'diff-neg' : '';
    const diffStr = c.diff > 0 ? `+${{c.diff}}` : String(c.diff);
    return `<tr>
      <td><span class="color-dot" style="background:${{c.color}}"></span>${{c.name}}</td>
      <td class="td-center">${{c.gs}}</td>
      <td class="td-center ${{diffClass}}">${{diffStr}}</td>
    </tr>`;
  }}).join('');
  document.getElementById('comparison-tbody').innerHTML = rows || '<tr><td colspan="3">Sin comparaciones disponibles</td></tr>';
}}

window.switchPlayer = function(name) {{
  if (name === activePlayer) return;
  activePlayer = name;

  // Update tab buttons
  document.querySelectorAll('.player-tab').forEach(btn => {{
    btn.classList.toggle('active', btn.textContent === name);
  }});

  // Replace analysis datasets (idx N onwards)
  const newDs = makePlayerDatasets(name);
  while (chart.data.datasets.length > N_LEGENDS) chart.data.datasets.pop();
  newDs.forEach(ds => chart.data.datasets.push(ds));

  // Reapply legend/proj visibility to new datasets
  if (!legendsVisible) {{
    for (let i = 0; i < N_LEGENDS; i++) chart.getDatasetMeta(i).hidden = true;
  }}
  chart.update();
  buildLegend();
  updateInfoPanel(name);
  renderGameStats(name);
}};

// ── Game stats panel ─────────────────────────────────────────────────────────
function renderGameStats(name) {{
  const p = ALL_PLAYERS[name];
  const el = document.getElementById('game-stats-content');
  const panel = document.getElementById('game-stats-panel');
  if (!p || !p.gameStats) {{
    panel.style.display = 'none';
    return;
  }}
  panel.style.display = '';
  const gs = p.gameStats;
  const pData = gs.player;
  const bData = gs.benchmark;

  const STATS = [
    {{ key: 'serve_win_pct',      label: 'Saque ganado %',    unit: '%' }},
    {{ key: 'return_win_pct',     label: 'Resto ganado %',    unit: '%' }},
    {{ key: 'bp_save_pct',        label: 'BP salvados %',     unit: '%' }},
    {{ key: 'bp_conversion_pct',  label: 'BP convertidos %',  unit: '%' }},
    {{ key: 'first_serve_in_pct', label: '1er saque dentro %',unit: '%' }},
    {{ key: 'win_rate',           label: 'Win rate %',        unit: '%' }},
  ];

  // score color
  const sim = gs.profile_similarity;
  const simColor = sim >= 80 ? '#2ca02c' : sim >= 65 ? '#e07b00' : '#d62728';

  let html = `
    <div class="potential-row">
      <div>
        <div class="potential-score" style="-webkit-text-fill-color:${{simColor}}">${{sim != null ? sim.toFixed(1) : '—'}}<span style="font-size:1rem;font-weight:400;color:#777">/100</span></div>
        <div class="potential-label">Potential Score (similitud perfil leyenda)</div>
      </div>
      <div style="font-size:0.78rem;color:#888;line-height:1.5">
        Datos: ${{gs.years_of_data}} año(s) · último: ${{gs.latest_year}}<br>
        100 = idéntico al promedio Federer/Nadal/Djokovic/Agassi a esta edad
      </div>
    </div>
    <div class="stat-bar-grid">`;

  for (const s of STATS) {{
    const pVal = pData[s.key];
    const bVal = bData[s.key];
    if (pVal == null) continue;
    // Bar fills up to max(pVal, bVal) * 1.1
    const maxVal = Math.max(pVal, bVal || pVal) * 1.1 || 100;
    const pPct   = Math.min(100, (pVal / maxVal) * 100);
    const bPct   = bVal != null ? Math.min(100, (bVal / maxVal) * 100) : null;
    const above  = bVal == null || pVal >= bVal;
    const fillClass = above ? 'bar-above' : 'bar-below';
    const diff = bVal != null ? (pVal - bVal).toFixed(1) : '';
    const diffStr = diff ? (parseFloat(diff) >= 0 ? `+${{diff}}` : diff) : '';
    const diffColor = parseFloat(diff) >= 0 ? '#2ca02c' : '#d62728';
    html += `
      <div class="stat-bar-row">
        <span class="stat-bar-label">${{s.label}}</span>
        <div class="stat-bar-track">
          <div class="stat-bar-fill ${{fillClass}}" style="width:${{pPct.toFixed(1)}}%"></div>
          ${{bPct != null ? `<div class="stat-bar-bench" style="left:${{bPct.toFixed(1)}}%"></div>` : ''}}
        </div>
        <span class="stat-bar-val">${{pVal}}${{s.unit}} <span style="font-size:0.70rem;color:${{diffColor}}">${{diffStr}}</span></span>
      </div>`;
  }}

  html += `</div>
    <p class="data-note" style="margin-top:8px">
      Barra negra = benchmark leyenda a esta edad · Verde = por encima · Rosa = por debajo.
      Fuente: Jeff Sackmann / tennis_atp.
    </p>`;

  el.innerHTML = html;
}}

// ── Init ──────────────────────────────────────────────────────────────────────
updateInfoPanel(activePlayer);
renderGameStats(activePlayer);
buildLegend();
</script>
</body>
</html>"""


def format_player_summary(player, dataset):
    score = compute_legend_trajectory_score(player)
    category = classify_category(score)
    archetype = detect_archetype(player)
    comparisons = compare_against_history(player, dataset)
    projections = project_grand_slams(player, score)

    lines = [
        f"Jugador: {player.get('player_name')} ({player.get('age')} años, {player.get('year')})",
        f"Legend Trajectory Score: {score}",
        f"Categoría: {category}",
        f"Arquetipo: {archetype}",
        "",
        "Comparación por edad contra leyendas históricas:",
    ]
    if comparisons:
        for item in comparisons:
            lines.append(
                f"  - {item['player_name']} ({item['year']}): score {item['score']} | GS {item['grand_slams_total']}"
            )
    else:
        lines.append("  No hay comparaciones directas de leyendas con la misma edad en el dataset.")

    lines.extend([
        "",
        "Proyección de Grand Slams:",
        f"  - Conservadora: {projections['conservadora']}",
        f"  - Media: {projections['media']}",
        f"  - Agresiva: {projections['agresiva']}",
        "",
        "Limitaciones:",
        "  - Datos incompletos pueden bajar la puntuación de jugadores con métricas parciales.",
        "  - El modelo es heurístico y no predice resultados exactos.",
        "  - No captura completamente lesiones, rivalidades o evolución de juego.",
    ])
    return "\n".join(lines)


def save_html_report(path, html_content):
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(html_content)


def load_dataset(path=DATA_PATH):
    with open(path, "r", encoding="utf-8") as handle:
        return json.load(handle)


def load_player_input(path):
    with open(path, "r", encoding="utf-8") as handle:
        payload = json.load(handle)
    if isinstance(payload, dict):
        return [payload]
    if isinstance(payload, list):
        return payload
    raise ValueError("El archivo JSON debe contener un objeto o una lista de objetos de jugador.")


def _latest_entry_per_player(dataset: list) -> list:
    """Returns the most recent dataset entry for each player."""
    latest = {}
    for record in dataset:
        name = record.get("player_name")
        if name and (name not in latest or record.get("year", 0) > latest[name].get("year", 0)):
            latest[name] = record
    return list(latest.values())


def _build_synthetic_player(lookup_result: dict) -> dict:
    """
    Builds a minimal player dict from a stats_fetcher lookup result,
    compatible with compute_legend_trajectory_score and render_html_report.
    """
    ls = lookup_result.get("latest_stats", {})
    return {
        "player_name":             lookup_result["name"],
        "age":                     lookup_result["age"],
        "year":                    lookup_result["latest_year"],
        "grand_slams_total":       lookup_result.get("gs_total", 0),
        "grand_slam_finals":       0,
        "grand_slam_semifinals":   0,
        "atp_titles_total":        0,
        "atp_250_titles":          0,
        "atp_500_titles":          0,
        "masters_1000_titles":     0,
        "atp_finals_titles":       0,
        "olympic_gold":            0,
        "olympic_silver":          0,
        "olympic_bronze":          0,
        "davis_cup_titles":        0,
        "ranking_end_year":        None,
        "best_ranking_by_age":     None,
        "weeks_at_number_1_by_age":0,
        "top_10_wins":             0,
        "win_rate":                ls.get("win_rate"),
        "hard_win_rate":           None,
        "clay_win_rate":           None,
        "grass_win_rate":          None,
        "major_injuries_or_interruptions": "unknown",
        "age_first_grand_slam":    None,
        "age_fifth_grand_slam":    None,
        "age_tenth_grand_slam":    None,
        "age_last_grand_slam":     None,
    }


def main():
    parser = argparse.ArgumentParser(description="Prototipo Legend Trajectory Tennis")
    parser.add_argument("--player-name", help="Nombre exacto del jugador del dataset")
    parser.add_argument("--input-json", help="JSON de uno o varios jugadores a analizar")
    parser.add_argument("--all", action="store_true", help="Incluir todos los jugadores del dataset")
    parser.add_argument("--lookup", nargs="+", metavar="NOMBRE",
                        help="Buscar cualquier jugador ATP por nombre y analizar su perfil de juego")
    parser.add_argument("--years-back", type=int, default=3,
                        help="Años de datos a analizar en --lookup (default: 3)")
    parser.add_argument("--output-html", help="Ruta del archivo HTML de salida")
    args = parser.parse_args()

    dataset = load_dataset()

    # ── --lookup mode ─────────────────────────────────────────────────────────
    if args.lookup:
        from stats_fetcher import lookup_player, find_player

        players = []
        game_stats_map = {}

        for query in args.lookup:
            result = lookup_player(query, years_back=args.years_back, use_cache=True)
            if result is None:
                # Try to give a helpful error
                candidate = find_player(query, use_cache=True)
                if candidate:
                    print(f"Jugador encontrado: '{candidate['name']}' pero sin datos de partidos recientes.")
                else:
                    print(f"No se encontró ningún jugador para: '{query}'")
                continue

            name = result["name"]
            # Check if we have more complete data in the dataset
            dataset_entries = [r for r in dataset if r["player_name"] == name]
            if dataset_entries:
                player = max(dataset_entries, key=lambda x: x.get("year", 0))
                # Update GS count from live data if higher
                if result["gs_total"] > (player.get("grand_slams_total") or 0):
                    player = dict(player)
                    player["grand_slams_total"] = result["gs_total"]
            else:
                player = _build_synthetic_player(result)

            players.append(player)
            game_stats_map[name] = result

        if not players:
            print("No se pudo analizar ningún jugador.")
            return

        if args.output_html:
            html = render_html_report(players, dataset, game_stats_map)
            save_html_report(args.output_html, html)
            print(f"HTML generado en: {args.output_html}")
        else:
            for name, result in game_stats_map.items():
                sim = result.get("profile_similarity")
                print(f"\n{name} (edad {result['age']}, {result['latest_year']})")
                print(f"  GS titles: {result['gs_total']}")
                print(f"  Potential Score: {sim}/100")
                ls = result.get("latest_stats", {})
                print(f"  Saque ganado: {round((ls.get('serve_win_pct') or 0)*100, 1)}%")
                print(f"  Resto ganado: {round((ls.get('return_win_pct') or 0)*100, 1)}%")
                print(f"  BP salvados:  {round((ls.get('bp_save_pct') or 0)*100, 1)}%")
                print(f"  BP conv:      {round((ls.get('bp_conversion_pct') or 0)*100, 1)}%")
        return

    # ── Standard modes ────────────────────────────────────────────────────────
    if args.input_json:
        try:
            players = load_player_input(args.input_json)
        except (ValueError, json.JSONDecodeError) as error:
            print(f"Error leyendo el archivo JSON: {error}")
            return
        if args.output_html:
            html = render_html_report(players, dataset)
            save_html_report(args.output_html, html)
            print(f"HTML generado en: {args.output_html}")
        else:
            for p in players:
                print(format_player_summary(p, dataset))
                print("\n" + "-" * 60 + "\n")
        return

    if args.all:
        players = _latest_entry_per_player(dataset)
        players.sort(key=lambda x: x.get("year", 0), reverse=True)
    elif args.player_name:
        matching = [r for r in dataset if r["player_name"] == args.player_name]
        if not matching:
            print(f"Jugador no encontrado: {args.player_name}")
            return
        players = [max(matching, key=lambda x: x.get("year", 0))]
    else:
        # Default: Alcaraz + Sinner
        active = ["Carlos Alcaraz", "Jannik Sinner"]
        players = []
        for name in active:
            matching = [r for r in dataset if r["player_name"] == name]
            if matching:
                players.append(max(matching, key=lambda x: x.get("year", 0)))

    if not players:
        print("No se encontraron jugadores.")
        return

    # For default/standard mode, enrich with game stats if available
    game_stats_map = {}
    stats_path = Path(__file__).resolve().parents[1] / "data" / "player_stats_by_age.json"
    bench_path  = Path(__file__).resolve().parents[1] / "data" / "legend_benchmark.json"
    if stats_path.exists() and bench_path.exists():
        try:
            from stats_fetcher import (compute_profile_similarity,
                                       _age_at_year_end, PLAYERS as SF_PLAYERS)
            with open(stats_path, encoding="utf-8") as f:
                all_stats = json.load(f)
            with open(bench_path, encoding="utf-8") as f:
                benchmark = json.load(f)
            for p in players:
                name = p.get("player_name")
                if name in all_stats and name in SF_PLAYERS:
                    p_stats = all_stats[name]
                    latest_year = max(p_stats.keys(), key=int)
                    latest_all  = p_stats[latest_year].get("All", {})
                    sim = compute_profile_similarity(latest_all, benchmark)
                    born = SF_PLAYERS[name]["born"]
                    game_stats_map[name] = {
                        "profile_similarity": sim,
                        "latest_year":        int(latest_year),
                        "years_of_data":      len(p_stats),
                        "latest_stats":       latest_all,
                        "benchmark_at_age":   benchmark.get(str(latest_all.get("age", "")), {}).get("All", {}),
                        "gs_total":           p.get("grand_slams_total", 0),
                        "gs_trajectory":      None,
                        "stats_by_year":      p_stats,
                    }
        except Exception as exc:
            print(f"Warning: no se pudo cargar game stats: {exc}")

    if args.output_html:
        html = render_html_report(players, dataset, game_stats_map)
        save_html_report(args.output_html, html)
        print(f"HTML generado en: {args.output_html}")
    else:
        for p in players:
            print(format_player_summary(p, dataset))
            print("\n" + "-" * 60 + "\n")


if __name__ == "__main__":
    main()
