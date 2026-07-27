"""
scoring.py -- exact Python port of the scoring / market-value engine embedded in
Scouting_App_Prototype.html (see build_html.py).

Everything here is deliberately a line-for-line port of the JavaScript so the
API server (api_server.py) produces numbers identical to the frontend:
  - hash_seed / mulberry32: the seeded PRNG (32-bit integer semantics replicated
    with masking so the float streams are bit-identical to JS). No longer used by
    the scoring engine itself -- kept because the frontend's Transfer Targets
    builder still uses it.
  - league_strength / age_value_factor: the continuous league and age
    coefficients that replaced the old 3-bucket tier multipliers.
  - shrink_rate: empirical-Bayes shrinkage of a per-90 rate toward its
    position-group minutes-weighted mean.
  - estimate_market_value: the estimated-market-value engine.
  - market_history: the deterministic ~15-point value history the modal chart
    draws.
  - compute_scores: per-90 rates -> shrinkage -> position-group percentiles ->
    weighted composite x league strength -> undervalued score + flags +
    system fit + explainability.

The SQL view player_scores in db_schema.py (CUME_DIST convention) reproduces
the same pipeline; test_scores_match_sql_view asserts they agree.
"""

import bisect
import math

DEFAULT_WEIGHTS = {"ga": 25, "prog": 35, "def": 20, "age": 20}
HP_THRESHOLD = 40
WL_THRESHOLD = 20
SF_HI, SF_YOUTH, SF_PASS, SF_LO = 0.65, 0.55, 0.62, 0.45

# ---------------------------------------------------------------------------
# Sample-size handling
# ---------------------------------------------------------------------------
# Empirical-Bayes shrinkage: every per-90 rate is pulled toward its
# position-group minutes-weighted mean with a prior worth SHRINK_K minutes
# (~10 full matches). A player with 900 minutes lands halfway between their own
# rate and the group mean; a player with 2,700 lands 75% of the way to their
# own. This is what stops a small-sample fluke from topping the leaderboard.
SHRINK_K = 900.0

# Below this many minutes a player is marked lowSample and cannot earn the
# High Priority flag (they cap at Watchlist) -- the sample simply isn't big
# enough to act on, even after shrinkage.
LOW_SAMPLE_MINUTES = 900

# ---------------------------------------------------------------------------
# League strength -- continuous, replacing the old tier 2/3/4 -> 1.15/1.0/0.85
# and 1.00/0.85/0.70 buckets.
# ---------------------------------------------------------------------------
# LEAGUE_STRENGTH is the override table, keyed by the exact `league` string.
# It is intentionally empty for now: real per-league coefficients need data we
# do not have yet (cross-league transfer outcomes / rating migration). Until
# then every league falls back to the smooth tier curve below. Populating a
# league here is a one-line change and immediately flows through the estimate,
# the performance score, and the explainability output.
#
# NOTE: entries added here will NOT be picked up by the player_scores SQL view
# until they are materialised into the database (see db_schema.py).
LEAGUE_STRENGTH = {}

TIER_REF = 2.0
TIER_DECAY = 0.155  # exp(-0.155 * (tier - 2)) -> 1.000 / 0.856 / 0.733
LEAGUE_STRENGTH_DEFAULT = 0.80  # used only when tier is missing/unusable


def tier_strength(tier):
    """Smooth continuous league-strength curve over tier.

    Exponential decay rather than a lookup, so a fractional or interpolated
    tier (e.g. a league that sits between the second and third level) gets a
    sensible coefficient instead of snapping to a bucket.
    """
    try:
        t = float(tier)
    except (TypeError, ValueError):
        return LEAGUE_STRENGTH_DEFAULT
    return math.exp(-TIER_DECAY * (t - TIER_REF))


def league_strength(p):
    """Per-league strength coefficient, with the tier curve as the fallback."""
    ls = LEAGUE_STRENGTH.get(p.get("league"))
    if ls is not None and ls > 0:
        return float(ls)
    return tier_strength(p.get("tier"))


# ---------------------------------------------------------------------------
# Age-value curve -- replaces the old linear youth premium
# (youthFactor = 1 + max(0, 24 - age) * 0.02), which wrongly implied a 17-year-old
# was worth more than a 24-year-old at identical output. Real market values rise
# steeply through the early twenties, plateau across the peak years, and decline
# after, so the curve does the same: a rising limb to 24, a flat 24-27 peak, and
# an accelerating decline after 27.
# ---------------------------------------------------------------------------
AGE_PEAK_LO, AGE_PEAK_HI = 24.0, 27.0
AGE_YOUNG_REF = 16.0
AGE_YOUNG_FLOOR = 0.70  # value factor at AGE_YOUNG_REF, relative to the peak
AGE_YOUNG_EXP = 0.85
AGE_DECLINE_RATE = 0.055
AGE_DECLINE_EXP = 1.35


def age_value_factor(age):
    a = float(age)
    if AGE_PEAK_LO <= a <= AGE_PEAK_HI:
        return 1.0
    if a < AGE_PEAK_LO:
        t = _clamp01((a - AGE_YOUNG_REF) / (AGE_PEAK_LO - AGE_YOUNG_REF))
        return AGE_YOUNG_FLOOR + (1.0 - AGE_YOUNG_FLOOR) * math.pow(t, AGE_YOUNG_EXP)
    return math.exp(-AGE_DECLINE_RATE * math.pow(a - AGE_PEAK_HI, AGE_DECLINE_EXP))


def age_band(age):
    a = float(age)
    if a < AGE_PEAK_LO:
        return "pre-peak"
    if a <= AGE_PEAK_HI:
        return "peak"
    return "post-peak"


# ---------------------------------------------------------------------------
# Value scale. Calibrated against the known market values actually present in
# the dataset (n=3,769: min EUR 8k, median EUR 79k, max EUR 150k) rather than
# against global transfer-market headlines -- these are lower-division players
# and inflating the estimates to millions would make displayMarketValue mix two
# incompatible scales. The old engine was a linear ramp hard-clamped to
# EUR 9k-160k; this is a log-scale (right-skewed) curve with a EUR 10k floor and
# a EUR 500k ceiling, so genuine standouts are no longer truncated.
# ---------------------------------------------------------------------------
VALUE_FLOOR = 10000.0
VALUE_CEIL = 500000.0
VALUE_MEDIAN = 79000.0   # euros for a reference-quality player at peak age
VALUE_SPREAD = 3.15      # log-space sensitivity to the quality composite
VALUE_BASE_REF = 0.490347  # cohort-median composite; calibrated so median(est) = 79k
MINUTES_REF = 2200.0
MINUTES_FACTOR_FLOOR = 0.85

# ---------------------------------------------------------------------------
# Acquirability + Deal Score (see acquirability_spec.md for full rationale).
# "How gettable is this player right now?" crossed with the undervalued gap.
# Deterministic per-row math only (no clock, no cohort iteration) so the JS and
# SQL copies stay bit-identical.
# ---------------------------------------------------------------------------
SEASON_YEAR = 2026            # "now" for contract math; a constant, never a clock call
CONTRACT_URGENCY = {0: 1.00, 1: 0.75, 2: 0.35, 3: 0.15}
CONTRACT_URGENCY_NULL = 0.35  # missing contract year -> assume mid-contract
RESIGN_AGE_LO = 20.0          # at/below: clubs fight to re-sign expiring prospects
RESIGN_AGE_SPAN = 3.0         # ramp 20 -> 23
RESIGN_FLOOR = 0.75           # U20 contract-urgency haircut
REP_NO, REP_UNKNOWN, REP_YES = 1.00, 0.70, 0.35
FEE_REF = 10000.0             # dataset floor: a EUR 10k fee is petty cash -> feasibility 1.0
FEE_EXP = 0.35                # each price doubling cuts feasibility ~21.5%
LS_MAX, LS_SPAN = 1.000, 0.267
LEAGUE_MULT_FLOOR = 0.85      # tier-2 seller leverage costs at most 15%
MINOR_GATE = 0.40             # age<18: FIFA RSTP Art. 19 bars international transfers
W_CONTRACT, W_REP, W_FEE = 0.45, 0.30, 0.25
DEAL_EXP_UV, DEAL_EXP_ACQ = 0.60, 0.40
HOT_DEAL_THRESHOLD = 55

# ---------------------------------------------------------------------------
# Quality-composite reference points.
#
# The old engine divided every per-90 rate by a single global constant
# (ga90/0.7, prog90/7, def90/4.5) and clamped to 1. Measured against the actual
# roster those constants were badly miscalibrated, so the terms saturated and
# stopped carrying any information at all:
#     MF progression  100% clamped (median 11.3 vs a reference of 7)
#     GK distribution 100% clamped (pass completion is 0-100, reference was 7)
#     FW attacking     87% clamped
#     DF defending     74% clamped
# For most of the roster the composite was therefore a near-constant, which is
# why estimated values bunched up. References are now position-relative: a
# player at POS_REF_MULT times their position group's minutes-weighted mean
# scores a full 1.0 on that term, so every term discriminates within its own
# group. compute_scores derives them from the cohort; DEFAULT_POS_REFS is the
# fallback for calling estimate_market_value on a single player.
#
# NOTE: this affects the value estimate only. The performance score ranks on
# percentiles, which are order-based and were never touched by the clamping.
# ---------------------------------------------------------------------------
POS_REF_MULT = 1.8
DEFAULT_POS_REFS = {
    "FW": {"ga": 1.7064, "prog": 11.6933, "def": 0.9194},
    "MF": {"ga": 1.3554, "prog": 20.2545, "def": 3.9751},
    "DF": {"ga": 0.3672, "prog": 8.3270, "def": 9.5443},
    "GK": {"ga": 1.6178, "prog": 131.7499, "def": 1.4240},
}


# ---------------------------------------------------------------------------
# JS 32-bit integer semantics (retained for the frontend's Transfer Targets
# builder; the scoring engine no longer uses randomness anywhere).
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


def _clamp01(x):
    return max(0.0, min(1.0, x))


def _ordinal(n):
    """1 -> '1st', 12 -> '12th', 23 -> '23rd'. Identical logic in the JS copy."""
    n = int(n)
    rem100 = n % 100
    if 11 <= rem100 <= 13:
        return str(n) + "th"
    rem10 = n % 10
    if rem10 == 1:
        return str(n) + "st"
    if rem10 == 2:
        return str(n) + "nd"
    if rem10 == 3:
        return str(n) + "rd"
    return str(n) + "th"


def _group_thousands(n):
    """1740 -> '1,740'. Hand-rolled so Python and JS agree regardless of locale."""
    s = str(int(n))
    neg = s.startswith("-")
    if neg:
        s = s[1:]
    out = ""
    while len(s) > 3:
        out = "," + s[-3:] + out
        s = s[:-3]
    out = s + out
    return ("-" + out) if neg else out


# ---------------------------------------------------------------------------
# Empirical-Bayes shrinkage
# ---------------------------------------------------------------------------
def shrink_rate(rate, minutes, pos_mean):
    """Pull a per-90 rate toward the position-group mean by sample size.

    shrunk = (rate * minutes + posMean * K) / (minutes + K)
    """
    m = float(minutes) if minutes else 0.0
    return (rate * m + pos_mean * SHRINK_K) / (m + SHRINK_K)


def shrink_weight(minutes):
    """Fraction of a player's own rate that survives shrinkage (0..1)."""
    m = float(minutes) if minutes else 0.0
    return m / (m + SHRINK_K)


def _weighted_mean(pairs):
    """Minutes-weighted mean of (rate, minutes). Summed in list order so the
    Python and JS floating-point results are bit-identical."""
    num = 0.0
    den = 0.0
    for rate, minutes in pairs:
        m = float(minutes) if minutes else 0.0
        num += rate * m
        den += m
    return (num / den) if den > 0 else 0.0


# ---------------------------------------------------------------------------
# Estimated market value engine (port of estimateMarketValue)
# ---------------------------------------------------------------------------
def quality_composite(p, rates=None, refs=None):
    """The 0..1 quality signal the value engine is built on.

    `rates` optionally supplies shrunk per-90 rates (gaPer90 / progPer90 /
    defPer90 for outfielders; savePct / gcPer90 / csRate for keepers). `refs`
    optionally supplies the position group's reference rates. When either is
    omitted the player's own raw rates / the DEFAULT_POS_REFS fallback are
    used, so this stays callable for a single player outside compute_scores.
    """
    minutes = p["minutes"] or 1
    m90 = minutes / 90
    if p["position"] == "GK":
        if rates is not None:
            save_pct = rates["savePct"]
            gc90 = rates["gcPer90"]
            cs_rate = rates["csRate"]
        else:
            save_pct = p["saves"] / (p["saves"] + p["goalsConceded"]) if (p["saves"] + p["goalsConceded"]) > 0 else 0.6
            gc90 = p["goalsConceded"] / m90 if m90 else 1.5
            cs_rate = (p.get("cleanSheets") or 0) / m90 if m90 else 0
        return (0.5 * _clamp01((save_pct - 0.55) / 0.35)
                + 0.3 * _clamp01(1 - gc90 / 2.2)
                + 0.2 * _clamp01(cs_rate / 0.45))
    if rates is not None:
        ga90 = rates["gaPer90"]
        prog90 = rates["progPer90"]
        def90 = rates["defPer90"]
    else:
        ga90 = (p["goals"] + p["assists"]) / m90 if m90 else 0
        prog90 = (p["progPasses"] + p["progCarries"]) / m90 if m90 else 0
        def90 = p["tklInt"] / m90 if m90 else 0
    att_w = 0.55 if p["position"] == "FW" else 0.4 if p["position"] == "MF" else 0.25
    prog_w = 0.4 if p["position"] == "MF" else 0.33
    def_w = 0.45 if p["position"] == "DF" else 0.27
    r = refs or DEFAULT_POS_REFS.get(p["position"]) or DEFAULT_POS_REFS["MF"]
    return (att_w * _clamp01(ga90 / r["ga"])
            + prog_w * _clamp01(prog90 / r["prog"])
            + def_w * _clamp01(def90 / r["def"]))


def _round_value(val):
    """Round to a sensible display granularity (identical logic in JS)."""
    if val < 250000:
        return _js_round(val / 100) * 100
    if val < 1000000:
        return _js_round(val / 1000) * 1000
    return _js_round(val / 10000) * 10000


def estimate_market_value(p, rates=None, refs=None):
    """Estimated market value, in euros.

    Log-scale: a reference-quality player at peak age in a tier-2 league with a
    full season of minutes lands on VALUE_MEDIAN, and every factor moves that
    multiplicatively. No random jitter -- the old jitter term added +-10% of
    pure noise to a number users read as an estimate.
    """
    minutes = p["minutes"] or 1
    base = quality_composite(p, rates, refs)
    ls = league_strength(p)
    age_f = age_value_factor(p["age"])
    minutes_f = MINUTES_FACTOR_FLOOR + (1.0 - MINUTES_FACTOR_FLOOR) * _clamp01(minutes / MINUTES_REF)
    val = (VALUE_MEDIAN
           * math.exp(VALUE_SPREAD * (base - VALUE_BASE_REF))
           * ls * age_f * minutes_f)
    val = max(VALUE_FLOOR, min(VALUE_CEIL, val))
    return _round_value(val)


# ---------------------------------------------------------------------------
# Market value history (port of marketHistory)
# ---------------------------------------------------------------------------
def market_history(p, current_value):
    """Deterministic 15-point value history ending at current_value.

    The previous version added seeded random noise to every point. That noise
    was demo scaffolding -- it looked like market volatility but carried no
    information, and users read it as real month-to-month movement. The line is
    now a smooth geometric trajectory whose slope is set by the player's
    Undervalued Score and age: undervalued and young -> a rising line.
    """
    n = 15
    if not current_value or current_value <= 0:
        return [0.0] * n
    uv = p["undervaluedScore"] if isinstance(p.get("undervaluedScore"), (int, float)) else 0
    youth = max(0, 24 - p["age"])
    trend = (uv / 100) * 0.6 + youth * 0.03
    start_frac = max(0.45, min(1.1, 1 - trend))
    start_value = max(6000.0, current_value * start_frac)
    ratio = current_value / start_value
    points = []
    for i in range(n):
        t = i / (n - 1)
        ease = t * t * (3 - 2 * t)  # smoothstep: flat start, flat finish
        points.append(start_value * math.pow(ratio, ease))
    points[n - 1] = current_value
    return points


# ---------------------------------------------------------------------------
# Percentiles + system fit + scoring
# ---------------------------------------------------------------------------
def percentile_rank(values, value):
    if not values:
        return 0
    count_le = sum(1 for v in values if v <= value)
    return count_le / len(values)


def percentile_ranker(values):
    """Sorted-once version of percentile_rank.

    Returns a callable giving count(v <= value) / n, which is exactly what
    percentile_rank computes -- but the linear scan per player made
    compute_scores O(n^2) (150M comparisons on the 5,000-player roster, several
    seconds server-side and a visible stall in the browser on every weight
    change). Counting via binary search over a sorted copy is integer-exact, so
    the numbers are bit-identical to the scan; only the cost changes.
    """
    ordered = sorted(values)
    n = len(ordered)
    if n == 0:
        return lambda value: 0
    return lambda value: bisect.bisect_right(ordered, value) / n


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


def acquirability(p):
    """Acquirability 0-100: contract window x representation x fee feasibility,
    with league-friction and minor-transfer multipliers. Multiplicative
    (weighted geometric mean) because real-world blockers compound: an agented
    player with 3 years left is NOT 'somewhat acquirable'."""
    ce = p.get("contractExpires")
    if ce is None:
        yrs = None
        c = CONTRACT_URGENCY_NULL
    else:
        yrs = min(3, max(0, int(ce) - SEASON_YEAR))
        c = CONTRACT_URGENCY[yrs]
    r = RESIGN_FLOOR + (1.0 - RESIGN_FLOOR) * _clamp01((p["age"] - RESIGN_AGE_LO) / RESIGN_AGE_SPAN)
    contract_comp = c * r
    rep = REP_NO if p["hasAgent"] == "No" else REP_YES if p["hasAgent"] == "Yes" else REP_UNKNOWN
    v_eff = max(p.get("displayMarketValue") or 79000.0, FEE_REF)
    fee = math.pow(FEE_REF / v_eff, FEE_EXP)
    inv_ls = _clamp01((LS_MAX - p["leagueStrength"]) / LS_SPAN)
    lmult = LEAGUE_MULT_FLOOR + (1.0 - LEAGUE_MULT_FLOOR) * inv_ls
    minor = MINOR_GATE if p["age"] < 18 else 1.0
    score = (100.0 * math.pow(contract_comp, W_CONTRACT) * math.pow(rep, W_REP)
             * math.pow(fee, W_FEE) * lmult * minor)
    return {"score": score, "yearsRemaining": yrs, "contractComp": contract_comp,
            "repComp": rep, "feeComp": fee, "leagueFriction": lmult, "minorGate": minor}


def deal_score(uv, acq):
    """0-100. uv <= 0 -> exactly 0: an overvalued player is never a deal."""
    u = max(0.0, uv) / 100.0
    return 100.0 * math.pow(u, DEAL_EXP_UV) * math.pow(acq / 100.0, DEAL_EXP_ACQ)


def _fmt_signed(x):
    n = _js_round(x)
    return ("+" if n >= 0 else "") + str(n)


def build_deal_explain(p, a):
    """Why this Deal Score -- drivers, phrases, and caveats. Mirrored in JS."""
    uv = p["undervaluedScore"]
    acq = p["acquirabilityScore"]
    deal = p["dealScore"]
    yrs = a["yearsRemaining"]

    uv_phrase = ("strongly undervalued" if uv >= 40 else "undervalued" if uv >= 20
                 else "mildly undervalued" if uv > 0 else "not undervalued")
    acq_phrase = ("highly gettable" if acq >= 70 else "gettable" if acq >= 45
                  else "hard to get" if acq >= 25 else "locked away")

    parts = []
    if yrs == 0:
        parts.append("contract expires this window")
    elif yrs == 1:
        parts.append("final contract year")
    if p["hasAgent"] == "No":
        parts.append("unrepresented")
    if a["feeComp"] >= 0.65:
        parts.append("modest fee")
    top_driver = ", ".join(parts) if parts else "no dominant access driver"

    if uv <= 0:
        summary = ("Deal 0: not undervalued (UV " + _fmt_signed(uv)
                   + ") -- excluded from call list regardless of acquirability.")
    else:
        summary = ("Deal " + str(_js_round(deal)) + ": " + uv_phrase + " (UV " + _fmt_signed(uv)
                   + ") x " + acq_phrase + " (" + str(_js_round(acq)) + "/100) -- " + top_driver + ".")

    ce = p.get("contractExpires")
    if yrs is None:
        contract_txt = "Contract year unknown -- assumed two years remaining"
    elif yrs == 0:
        contract_txt = "Contract expires this window (" + str(ce) + ") -- free-transfer territory"
    elif yrs == 1:
        contract_txt = "Final contract year (expires " + str(ce) + ") -- selling club under pressure"
    elif yrs == 2:
        contract_txt = "Two years remaining (" + str(ce) + ") -- club holds leverage"
    else:
        contract_txt = "Under contract to " + str(ce) + " -- locked down"

    rep_txt = ("Unrepresented -- direct club/federation route" if p["hasAgent"] == "No"
               else "Agented -- approach runs through representation" if p["hasAgent"] == "Yes"
               else "Representation unknown -- likely approachable at this level")
    fee_band = "high" if a["feeComp"] >= 0.65 else "moderate" if a["feeComp"] >= 0.40 else "low"
    fee_txt = "Est. fee EUR " + _group_thousands(p["displayMarketValue"]) + " -- " + fee_band + " feasibility"
    inv_ls = _clamp01((LS_MAX - p["leagueStrength"]) / LS_SPAN)
    lev_band = "little" if inv_ls >= 0.67 else "moderate" if inv_ls >= 0.33 else "strong"
    league_txt = "Tier " + str(p["tier"]) + " seller -- " + lev_band + " holding leverage"

    drivers = [
        {"key": "contractComp", "label": "Contract window", "value": a["contractComp"], "text": contract_txt},
        {"key": "repComp", "label": "Representation", "value": a["repComp"], "text": rep_txt},
        {"key": "feeComp", "label": "Fee feasibility", "value": a["feeComp"], "text": fee_txt},
        {"key": "leagueFriction", "label": "League friction", "value": a["leagueFriction"], "text": league_txt},
    ]

    notes = []
    if p["age"] <= 20 and yrs is not None and yrs <= 1:
        notes.append("Age " + str(p["age"]) + ": club likely to fight re-signing")
    if p["age"] < 18:
        notes.append("Under 18 -- international transfer barred (FIFA Art. 19); domestic route only")
    if yrs is None:
        notes.append("Contract year unknown -- assumed two years remaining")
    if p["lowSample"]:
        notes.append("Low sample (" + _group_thousands(p["minutes"]) + " mins): blocked from the call list")
    if p["marketValueEstimated"]:
        notes.append("Fee based on the model's value estimate, not a recorded market value")

    return {"summary": summary, "uvPhrase": uv_phrase, "acqPhrase": acq_phrase,
            "topDriver": top_driver, "drivers": drivers, "notes": notes}


def build_explain(p):
    """Why this player carries the flag they carry.

    Turns the scoring internals into an inspectable object plus a one-line
    summary, so a flag is never an unexplained verdict.
    """
    out_pct = _js_round(p["performancePct"] * 100)
    val_pct = _js_round(p["marketPct"] * 100)
    parts = [
        _ordinal(out_pct) + " pct output",
        _ordinal(val_pct) + " pct value",
        "age " + str(p["age"]),
        _group_thousands(p["minutes"]) + " mins",
    ]
    if p["lowSample"]:
        parts.append("low sample")
    if p["marketValueEstimated"]:
        parts.append("value estimated")

    if p["flag"]:
        summary = "Flagged because: " + ", ".join(parts) + "."
    else:
        summary = "Not flagged: " + ", ".join(parts) + "."

    drivers = [
        {"key": "performancePct", "label": "Output percentile",
         "value": p["performancePct"], "pct": out_pct},
        {"key": "marketPct", "label": "Value percentile",
         "value": p["marketPct"], "pct": val_pct},
        {"key": "gaPct", "label": "Attacking output" if p["position"] != "GK" else "Shot-stopping",
         "value": p["gaPct"], "pct": _js_round(p["gaPct"] * 100)},
        {"key": "progPct", "label": "Progression" if p["position"] != "GK" else "Distribution",
         "value": p["progPct"], "pct": _js_round(p["progPct"] * 100)},
        {"key": "defPct", "label": "Defensive actions" if p["position"] != "GK" else "Sweeper activity",
         "value": p["defPct"], "pct": _js_round(p["defPct"] * 100)},
        {"key": "youthPct", "label": "Youth",
         "value": p["youthPct"], "pct": _js_round(p["youthPct"] * 100)},
    ]

    notes = []
    if p["lowSample"]:
        notes.append(
            f"Only {_group_thousands(p['minutes'])} minutes ({LOW_SAMPLE_MINUTES} needed): rates are heavily "
            "regressed to the position average and this player cannot be rated High Priority.")
    if p["marketValueEstimated"]:
        notes.append(
            "No market value on record -- the value percentile uses the model's estimate, "
            "so the undervalued gap is more tentative than for a player with a known value.")
    if p["shrinkWeight"] < 0.75:
        notes.append(
            f"{_js_round(p['shrinkWeight'] * 100)}% of this player's own per-90 rates survive shrinkage; "
            "the rest is the position-group average.")

    return {
        "summary": summary,
        "performancePct": p["performancePct"],
        "marketPct": p["marketPct"],
        "undervaluedScore": p["undervaluedScore"],
        "gaPct": p["gaPct"],
        "progPct": p["progPct"],
        "defPct": p["defPct"],
        "youthPct": p["youthPct"],
        "age": p["age"],
        "ageBand": age_band(p["age"]),
        "ageValueFactor": p["ageValueFactor"],
        "minutes": p["minutes"],
        "lowSample": p["lowSample"],
        "shrinkWeight": p["shrinkWeight"],
        "leagueStrength": p["leagueStrength"],
        "marketValueEstimated": p["marketValueEstimated"],
        "drivers": drivers,
        "notes": notes,
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

    # ---- pass 1: raw per-90 rates -------------------------------------------
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
        p["rawGaPer90"] = ((p["saves"] - 1.5 * p["goalsConceded"]) / p["minutes"] * 90 if p["minutes"] else 0) if is_gk \
            else ((p["goals"] + p["assists"]) / p["minutes"] * 90 if p["minutes"] else 0)
        p["rawProgPer90"] = (p.get("passCompletionPct") or 0) if is_gk \
            else ((p["progPasses"] + p["progCarries"]) / p["minutes"] * 90 if p["minutes"] else 0)
        p["rawDefPer90"] = ((p.get("sweeperActions") or 0) / p["minutes"] * 90 if p["minutes"] else 0) if is_gk \
            else (p["tklInt"] / p["minutes"] * 90 if p["minutes"] else 0)
        with_rates.append(p)

    by_pos = {}
    for p in with_rates:
        by_pos.setdefault(p["position"], []).append(p)

    # ---- pass 2: position-group minutes-weighted means -----------------------
    pos_means = {}
    for pos, peers in by_pos.items():
        pos_means[pos] = {
            "ga": _weighted_mean([(x["rawGaPer90"], x["minutes"]) for x in peers]),
            "prog": _weighted_mean([(x["rawProgPer90"], x["minutes"]) for x in peers]),
            "def": _weighted_mean([(x["rawDefPer90"], x["minutes"]) for x in peers]),
            "savePct": _weighted_mean([(x.get("savePct", 0), x["minutes"]) for x in peers]),
            "gc": _weighted_mean([(x.get("gcPer90", 0), x["minutes"]) for x in peers]),
            "cs": _weighted_mean([(x.get("csRate", 0), x["minutes"]) for x in peers]),
        }
        # Position-relative reference points for the value composite.
        pos_means[pos]["refs"] = {
            "ga": pos_means[pos]["ga"] * POS_REF_MULT,
            "prog": pos_means[pos]["prog"] * POS_REF_MULT,
            "def": pos_means[pos]["def"] * POS_REF_MULT,
        }

    # ---- pass 3: shrink rates, then value the player -------------------------
    for p in with_rates:
        pm = pos_means[p["position"]]
        mins = p["minutes"]
        p["gaPer90"] = shrink_rate(p["rawGaPer90"], mins, pm["ga"])
        p["progPer90"] = shrink_rate(p["rawProgPer90"], mins, pm["prog"])
        p["defPer90"] = shrink_rate(p["rawDefPer90"], mins, pm["def"])
        p["shrinkWeight"] = shrink_weight(mins)
        p["lowSample"] = mins < LOW_SAMPLE_MINUTES
        p["leagueStrength"] = league_strength(p)
        p["ageValueFactor"] = age_value_factor(p["age"])

        if p["position"] == "GK":
            rates = {
                "savePct": shrink_rate(p["savePct"], mins, pm["savePct"]),
                "gcPer90": shrink_rate(p["gcPer90"], mins, pm["gc"]),
                "csRate": shrink_rate(p["csRate"], mins, pm["cs"]),
            }
        else:
            rates = {
                "gaPer90": p["gaPer90"],
                "progPer90": p["progPer90"],
                "defPer90": p["defPer90"],
            }
        p["qualityComposite"] = quality_composite(p, rates, pm["refs"])
        est = estimate_market_value(p, rates, pm["refs"])
        known = bool(p["marketValue"] and p["marketValue"] > 0)
        p["estimatedMarketValue"] = est
        p["displayMarketValue"] = p["marketValue"] if known else est
        p["marketValueEstimated"] = not known

    # ---- pass 4: percentiles + composite ------------------------------------
    rank_by_pos = {}
    for pos, peers in by_pos.items():
        rank_by_pos[pos] = {
            "ga": percentile_ranker([x["gaPer90"] for x in peers]),
            "prog": percentile_ranker([x["progPer90"] for x in peers]),
            "def": percentile_ranker([x["defPer90"] for x in peers]),
            "youth": percentile_ranker([-x["age"] for x in peers]),
        }

    for p in with_rates:
        rk = rank_by_pos[p["position"]]
        p["gaPct"] = rk["ga"](p["gaPer90"])
        p["progPct"] = rk["prog"](p["progPer90"])
        p["defPct"] = rk["def"](p["defPer90"])
        p["youthPct"] = rk["youth"](-p["age"])
        # tierMult kept as an alias so existing consumers keep working; it is
        # now the continuous league-strength coefficient, not a 3-way bucket.
        p["tierMult"] = p["leagueStrength"]
        p["performanceScore"] = (p["gaPct"] * norm["ga"] + p["progPct"] * norm["prog"]
                                 + p["defPct"] * norm["def"] + p["youthPct"] * norm["age"]) * p["leagueStrength"]
        p["passCarryRatio"] = p["progPasses"] / (p["progPasses"] + p["progCarries"]) if (p["progPasses"] + p["progCarries"]) else 0
        fit = classify_system(p)
        p["systemFit"] = fit["label"]
        p["systemNote"] = fit["note"]

    # ---- pass 5: undervalued gap, flags, explainability ---------------------
    final_by_pos = {}
    for pos, peers in by_pos.items():
        final_by_pos[pos] = {
            "perf": percentile_ranker([x["performanceScore"] for x in peers]),
            "value": percentile_ranker([x["displayMarketValue"] for x in peers]),
        }

    for p in with_rates:
        rk = final_by_pos[p["position"]]
        p["performancePct"] = rk["perf"](p["performanceScore"])
        # displayMarketValue, NOT the raw marketValue: ranking on the raw field
        # sorted every unknown-value player (marketValue = 0) to the bottom of
        # the value distribution, handing them a near-maximal undervalued gap
        # and flooding the High Priority list with players whose only
        # distinguishing feature was missing data.
        p["marketPct"] = rk["value"](p["displayMarketValue"])
        p["undervaluedScore"] = (p["performancePct"] - p["marketPct"]) * 100

        unrep = p["hasAgent"] == "No"
        if p["undervaluedScore"] >= HP_THRESHOLD and not p["lowSample"]:
            p["flag"] = "High Priority - Unrepresented" if unrep else "High Priority"
        elif p["undervaluedScore"] >= WL_THRESHOLD or (p["undervaluedScore"] >= HP_THRESHOLD and p["lowSample"]):
            p["flag"] = "Watchlist - Unrepresented" if unrep else "Watchlist"
        else:
            p["flag"] = ""

        p["explain"] = build_explain(p)

        # Acquirability + Deal Score (needs displayMarketValue, leagueStrength,
        # and undervaluedScore, so it lives at the end of pass 5).
        a = acquirability(p)
        p["acquirabilityScore"] = a["score"]
        p["dealScore"] = deal_score(p["undervaluedScore"], a["score"])
        p["hotProspect"] = bool(p["dealScore"] >= HOT_DEAL_THRESHOLD and not p["lowSample"])
        p["contractYearsRemaining"] = a["yearsRemaining"]
        p["dealExplain"] = build_deal_explain(p, a)

    return with_rates
