"""
scoring.py -- exact Python port of the scoring / market-value engine embedded in
Scouting_App_Prototype.html (see build_html.py).

Everything here is deliberately a line-for-line port of the JavaScript:
  - hash_seed / mulberry32: the seeded PRNG (32-bit integer semantics replicated
    with masking so the float streams are bit-identical to JS)
  - estimate_market_value: the estimated-market-value engine
  - market_history: the deterministic ~15-point value history the modal chart draws
  - compute_scores: per-90 rates -> position-group percentiles -> weighted
    composite x tier multiplier -> undervalued score + flags + system fit

The API server (api_server.py) uses this so server-side numbers match the
frontend exactly, and the SQL view player_scores (CUME_DIST convention) to
within floating-point noise.
"""

import math

TIER_MULT = {2: 1.00, 3: 0.85, 4: 0.70}
DEFAULT_WEIGHTS = {"ga": 25, "prog": 35, "def": 20, "age": 20}
HP_THRESHOLD = 40
WL_THRESHOLD = 20
SF_HI, SF_YOUTH, SF_PASS, SF_LO = 0.65, 0.55, 0.62, 0.45


# ---------------------------------------------------------------------------
# JS 32-bit integer semantics
# ---------------------------------------------------------------------------
def _u32(x):
    return x & 0xFFFFFFFF


def _i32(x):
    x &= 0xFFFFFFFF
    return x - 0x100000000 if x >= 0x80000000 else x


def _imul(a, b):
    return _i32(_i32(a) * _i32(b))


def hash_seed(s):
    """Port of JS hashSeed(str) -> unsigned 32-bit int."""
    h = _i32(1779033703 ^ len(s))
    for ch in s:
        h = _imul(h ^ ord(ch), 3432918353)
        h = _i32(((_u32(h) << 13) & 0xFFFFFFFF) | (_u32(h) >> 19))
    return _u32(h)


def mulberry32(seed):
    """Port of JS mulberry32(seed) -> callable returning floats in [0,1)."""
    state = {"a": seed}

    def rng():
        a = _i32(state["a"])
        a = _i32(a + 0x6D2B79F5)
        state["a"] = a
        t = _imul(a ^ (_u32(a) >> 15), 1 | a)
        inner = t + _imul(t ^ (_u32(t) >> 7), 61 | t)
        t = _i32(_i32(inner) ^ t)
        return (_u32(t) ^ (_u32(t) >> 14)) / 4294967296

    return rng


def _js_round(x):
    """JS Math.round: half rounds toward +Infinity."""
    return math.floor(x + 0.5)


def _js_tofixed0(x):
    """JS (x).toFixed(0) for non-negative values: half away from zero."""
    return str(int(math.floor(x + 0.5)))


# ---------------------------------------------------------------------------
# Estimated market value engine (port of estimateMarketValue)
# ---------------------------------------------------------------------------
def _clamp01(x):
    return max(0.0, min(1.0, x))


def estimate_market_value(p):
    minutes = p["minutes"] or 1
    m90 = minutes / 90
    if p["position"] == "GK":
        save_pct = p["saves"] / (p["saves"] + p["goalsConceded"]) if (p["saves"] + p["goalsConceded"]) > 0 else 0.6
        gc90 = p["goalsConceded"] / m90 if m90 else 1.5
        cs_rate = (p.get("cleanSheets") or 0) / m90 if m90 else 0
        base = (0.5 * _clamp01((save_pct - 0.55) / 0.35)
                + 0.3 * _clamp01(1 - gc90 / 2.2)
                + 0.2 * _clamp01(cs_rate / 0.45))
    else:
        ga90 = (p["goals"] + p["assists"]) / m90 if m90 else 0
        prog90 = (p["progPasses"] + p["progCarries"]) / m90 if m90 else 0
        def90 = p["tklInt"] / m90 if m90 else 0
        att_w = 0.55 if p["position"] == "FW" else 0.4 if p["position"] == "MF" else 0.25
        prog_w = 0.4 if p["position"] == "MF" else 0.33
        def_w = 0.45 if p["position"] == "DF" else 0.27
        base = (att_w * _clamp01(ga90 / 0.7)
                + prog_w * _clamp01(prog90 / 7)
                + def_w * _clamp01(def90 / 4.5))
    tier_factor = 1.15 if p["tier"] <= 2 else 1.0 if p["tier"] == 3 else 0.85
    youth_factor = 1 + max(0, 24 - p["age"]) * 0.02
    minutes_factor = 0.75 + 0.25 * _clamp01(minutes / 2200)
    jitter = 0.9 + 0.2 * mulberry32(hash_seed(p["name"] + "|mv"))()
    val = (12000 + base * 130000) * tier_factor * youth_factor * minutes_factor * jitter
    val = max(9000, min(160000, val))
    return _js_round(val / 100) * 100


# ---------------------------------------------------------------------------
# Market value history (port of marketHistory)
# ---------------------------------------------------------------------------
def market_history(p, current_value):
    """p needs: name, age, undervaluedScore (number). Returns list of 15 floats."""
    rng = mulberry32(hash_seed(p["name"] + "|mvhist"))
    n = 15
    uv = p["undervaluedScore"] if isinstance(p.get("undervaluedScore"), (int, float)) else 0
    youth = max(0, 24 - p["age"])
    trend = (uv / 100) * 0.6 + youth * 0.03
    start_frac = max(0.45, min(1.1, 1 - trend))
    start_value = max(6000, current_value * start_frac)
    points = []
    for i in range(n):
        t = i / (n - 1)
        smooth = start_value + (current_value - start_value) * t
        noise = (rng() - 0.5) * current_value * 0.10
        v = max(4000, smooth + noise)
        points.append(v)
    points[n - 1] = current_value
    return points


# ---------------------------------------------------------------------------
# Percentiles + system fit + scoring (port of percentileRank / classifySystem /
# computeScores)
# ---------------------------------------------------------------------------
def percentile_rank(values, value):
    if not values:
        return 0
    count_le = sum(1 for v in values if v <= value)
    return count_le / len(values)


def classify_system(p):
    pct = lambda x: _js_tofixed0(x * 100)

    if p["position"] == "GK":
        if p["defPct"] >= SF_HI and p["progPct"] >= SF_HI:
            return {
                "label": "Sweeper-Keeper / Build-from-the-Back",
                "note": f"High sweeper-actions output ({pct(p['defPct'])}th percentile) combined with strong distribution ({pct(p['progPct'])}th percentile pass completion) -- suited to a high defensive line and playing out from the back.",
            }
        if p["defPct"] >= SF_HI:
            return {
                "label": "Sweeper-Keeper / High Line",
                "note": f"High sweeper-actions output ({pct(p['defPct'])}th percentile defensive actions outside the box) -- comfortable operating off the line behind a high defense, even if distribution isn't the standout trait.",
            }
        if p["gaPct"] >= SF_HI:
            return {
                "label": "Shot-Stopper / Traditional",
                "note": f"Strong shot-stopping output ({pct(p['gaPct'])}th percentile) -- fits a system that asks the keeper to hold the line and make saves rather than sweep or build attacks.",
            }
        return {
            "label": "Flexible / Multi-System",
            "note": "No single standout trait among shot-stopping, distribution, or sweeper activity -- a serviceable, well-rounded goalkeeping profile.",
        }

    if p["defPct"] >= SF_HI and p["youthPct"] >= SF_YOUTH:
        return {
            "label": "High Press / Gegenpressing",
            "note": f"High defensive activity ({pct(p['defPct'])}th percentile) combined with youth (age {p['age']}) is the engine profile a pressing system needs -- energy to win the ball back high up the pitch.",
        }
    if p["progPct"] >= SF_HI and p["passCarryRatio"] >= SF_PASS:
        return {
            "label": "Possession / Build-from-the-Back",
            "note": f"Progresses play mainly through passing ({pct(p['passCarryRatio'])}% of progressive actions are passes) with strong overall progression ({pct(p['progPct'])}th percentile) -- comfortable circulating the ball and building attacks patiently.",
        }
    if p["progPct"] >= SF_HI:
        return {
            "label": "Counter-Attack / Transition",
            "note": f"Progresses play mainly by carrying the ball ({pct(1 - p['passCarryRatio'])}% of progressive actions are carries) with strong overall progression ({pct(p['progPct'])}th percentile) -- a threat in fast transition moments.",
        }
    if p["defPct"] >= SF_HI and p["progPct"] < SF_LO:
        return {
            "label": "Low Block / Park the Bus",
            "note": f"Strong defensive output ({pct(p['defPct'])}th percentile) without needing to progress much play -- suited to a disciplined deep block that absorbs pressure and defends the box.",
        }
    if p["position"] == "FW" and p["gaPct"] >= SF_HI and p["progPct"] < SF_LO:
        return {
            "label": "Direct / Target Approach",
            "note": f"High end product ({pct(p['gaPct'])}th percentile goals + assists) without heavy build-up involvement -- fits systems that get the ball forward quickly and look to finish.",
        }
    return {
        "label": "Flexible / Multi-System",
        "note": "No single standout trait -- percentiles are fairly balanced across attacking, progression, and defensive metrics, so this player could plausibly slot into more than one system.",
    }


def compute_scores(players, w=None):
    """Port of the JS computeScores(players, weights).

    `players` is a list of dicts with the camelCase JSON fields (plus optionally
    `id`); returns NEW dicts with all derived fields the frontend uses added.
    """
    w = w or DEFAULT_WEIGHTS
    total = w["ga"] + w["prog"] + w["def"] + w["age"]
    if total > 0:
        norm = {k: w[k] / total for k in ("ga", "prog", "def", "age")}
    else:
        norm = {"ga": 0, "prog": 0, "def": 0, "age": 0}

    with_rates = []
    for p0 in players:
        p = dict(p0)
        is_gk = p["position"] == "GK"
        m90 = p["minutes"] / 90 if p["minutes"] else 0
        if is_gk:
            p.update({
                "savePct": p["saves"] / (p["saves"] + p["goalsConceded"]) if (p["saves"] + p["goalsConceded"]) > 0 else 0,
                "gcPer90": p["goalsConceded"] / m90 if m90 else 0,
                "savesPer90": p["saves"] / m90 if m90 else 0,
                "csRate": (p.get("cleanSheets") or 0) / m90 if m90 else 0,
                "sweepPer90": (p.get("sweeperActions") or 0) / m90 if m90 else 0,
                "distributionPct": p.get("passCompletionPct") or 0,
            })
        else:
            p.update({
                "goalsPer90": p["goals"] / m90 if m90 else 0,
                "assistsPer90": p["assists"] / m90 if m90 else 0,
                "progPassPer90": p["progPasses"] / m90 if m90 else 0,
                "progCarryPer90": p["progCarries"] / m90 if m90 else 0,
                "tklIntPer90": p["tklInt"] / m90 if m90 else 0,
                "gaShare": p["goals"] / (p["goals"] + p["assists"]) if (p["goals"] + p["assists"]) > 0 else 0,
            })
        est = estimate_market_value(p)
        known = bool(p["marketValue"] and p["marketValue"] > 0)
        p["gaPer90"] = ((p["saves"] - 1.5 * p["goalsConceded"]) / p["minutes"] * 90 if p["minutes"] else 0) if is_gk \
            else ((p["goals"] + p["assists"]) / p["minutes"] * 90 if p["minutes"] else 0)
        p["progPer90"] = (p.get("passCompletionPct") or 0) if is_gk \
            else ((p["progPasses"] + p["progCarries"]) / p["minutes"] * 90 if p["minutes"] else 0)
        p["defPer90"] = ((p.get("sweeperActions") or 0) / p["minutes"] * 90 if p["minutes"] else 0) if is_gk \
            else (p["tklInt"] / p["minutes"] * 90 if p["minutes"] else 0)
        p["estimatedMarketValue"] = est
        p["displayMarketValue"] = p["marketValue"] if known else est
        p["marketValueEstimated"] = not known
        with_rates.append(p)

    by_pos = {}
    for p in with_rates:
        by_pos.setdefault(p["position"], []).append(p)

    for p in with_rates:
        peers = by_pos[p["position"]]
        p["gaPct"] = percentile_rank([x["gaPer90"] for x in peers], p["gaPer90"])
        p["progPct"] = percentile_rank([x["progPer90"] for x in peers], p["progPer90"])
        p["defPct"] = percentile_rank([x["defPer90"] for x in peers], p["defPer90"])
        p["youthPct"] = percentile_rank([-x["age"] for x in peers], -p["age"])
        p["tierMult"] = TIER_MULT.get(p["tier"], 1)
        p["performanceScore"] = (p["gaPct"] * norm["ga"] + p["progPct"] * norm["prog"]
                                 + p["defPct"] * norm["def"] + p["youthPct"] * norm["age"]) * p["tierMult"]
        p["passCarryRatio"] = p["progPasses"] / (p["progPasses"] + p["progCarries"]) if (p["progPasses"] + p["progCarries"]) else 0
        fit = classify_system(p)
        p["systemFit"] = fit["label"]
        p["systemNote"] = fit["note"]

    for p in with_rates:
        peers = by_pos[p["position"]]
        p["performancePct"] = percentile_rank([x["performanceScore"] for x in peers], p["performanceScore"])
        p["marketPct"] = percentile_rank([x["marketValue"] for x in peers], p["marketValue"])
        p["undervaluedScore"] = (p["performancePct"] - p["marketPct"]) * 100

        if p["undervaluedScore"] >= HP_THRESHOLD:
            p["flag"] = "High Priority - Unrepresented" if p["hasAgent"] == "No" else "High Priority"
        elif p["undervaluedScore"] >= WL_THRESHOLD:
            p["flag"] = "Watchlist - Unrepresented" if p["hasAgent"] == "No" else "Watchlist"
        else:
            p["flag"] = ""

    return with_rates
