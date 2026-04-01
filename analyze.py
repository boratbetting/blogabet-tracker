#!/usr/bin/env python3
"""
Blogabet Tipster Tracker — analyze.py
Scoring + Analysis + Dashboard Generator
"""

import json, os, sys
from datetime import datetime, timezone

BASE_DIR   = os.path.dirname(os.path.abspath(__file__))
DATA_DIR   = os.path.join(BASE_DIR, "data")
DOCS_DIR   = os.path.join(BASE_DIR, "docs")
RAW_FILE   = os.path.join(DATA_DIR, "tipsters_raw.json")
SCORED_FILE= os.path.join(DATA_DIR, "tipsters_scored.json")
HIST_FILE  = os.path.join(DATA_DIR, "history.json")
HTML_FILE  = os.path.join(DOCS_DIR, "index.html")

os.makedirs(DATA_DIR, exist_ok=True)
os.makedirs(DOCS_DIR, exist_ok=True)

# ─────────────────────────────────────────────
#  HELPERS
# ─────────────────────────────────────────────
def grade(score):
    if score >= 85: return "A"
    if score >= 70: return "B"
    if score >= 55: return "C"
    if score >= 40: return "D"
    return "F"

def trend_arrow(val1, val2):
    if val1 is None or val2 is None:
        return "→", "neutral"
    diff = val1 - val2
    if diff > 2:  return "↑", "up"
    if diff < -2: return "↓", "down"
    return "→", "neutral"

# ─────────────────────────────────────────────
#  SCORING ENGINE  (0–100 pts)
# ─────────────────────────────────────────────
def score_tipster(t):
    pts = 0
    flags = []

    y   = t.get("yield_pct", 0)
    pk  = t.get("picks_count", 0)
    vrf = t.get("verification", "free")
    bkm = t.get("bookmaker_profile", "mixed")
    rf  = t.get("recent_form_yield", None)
    sp  = t.get("specialization", "focused_multi")
    rst = t.get("resets", 0)
    aq  = t.get("analysis_quality", "none")
    lp  = t.get("live_pct", 10)
    pmv = t.get("prematch_yield", None)
    liv = t.get("live_yield", None)
    ods = t.get("odds_avg", 1.90)
    sk  = t.get("avg_stake", 5.0)

    if   y > 12: pts += 25
    elif y >  8: pts += 20
    elif y >  5: pts += 15
    elif y >  3: pts += 10
    elif y >  0: pts += 5

    if   pk > 1000: pts += 15
    elif pk >  500: pts += 10
    elif pk >  200: pts += 5

    vmap = {"paid_copytip": 15, "paid": 13, "pro": 10, "free": 0}
    pts += vmap.get(vrf, 0)

    bmap = {"asian_dominant": 10, "mixed": 5, "soft_only": 0}
    pts += bmap.get(bkm, 5)

    if rf is not None:
        if   rf >  6: pts += 10
        elif rf >  3: pts += 6
        elif rf >  0: pts += 3

    smap = {"mono_specialist": 10, "focused_multi": 6, "chaotic_multi": 2}
    pts += smap.get(sp, 6)

    if   rst == 0: pts += 5
    elif rst == 1: pts += 3
    elif rst == 2: pts += 1

    amap = {"detailed_value": 10, "short_desc": 4, "none": 0}
    pts += amap.get(aq, 0)

    if rst >= 3:                          flags.append("RESET_MULTIPLE")
    if vrf == "free":                     flags.append("UNVERIFIED")
    if sk > 8:                            flags.append("HIGH_STAKE")
    if y > 15 and pk > 1000:             flags.append("EXTREME_YIELD")
    if y > 30:                            flags.append("EXTREME_YIELD")
    if lp > 30 and vrf == "free":        flags.append("LIVE_UNVERIFIED")
    if ods < 1.50:                        flags.append("LOW_ODDS")
    if liv and pmv and liv > 20 and pmv < 5: flags.append("YIELD_PUMP")
    if bkm == "soft_only":               flags.append("SOFT_ONLY_EDGE")
    if pk < 50:                           flags.append("TINY_SAMPLE")

    active_flags = [f for f in list(set(flags)) if f != "UNVERIFIED"]
    if len(active_flags) >= 3:
        pts = min(pts, 29)

    g = grade(pts)
    return pts, g, list(set(flags))

# ─────────────────────────────────────────────
#  ANALYSIS ENGINE
# ─────────────────────────────────────────────
WEAK_LEAGUE_KEYWORDS = [
    "2nd", "3rd", "division", "lower", "amateur", "cup", "reserve",
    "u21", "u23", "youth", "friendl", "women", "regional", "ii", "iii"
]

STRONG_LEAGUE_NAMES = [
    "Premier League", "La Liga", "Bundesliga", "Serie A", "Ligue 1",
    "Champions League", "Europa League", "NBA", "NFL", "MLB", "NHL",
    "PlusLiga", "CEV", "Euroleague", "ATP", "WTA", "Eredivisie"
]

def weak_leagues_flag(tipster):
    leagues = tipster.get("top_leagues", [])
    if not leagues:
        return False, []
    weak = [l for l in leagues if any(kw in l.lower() for kw in WEAK_LEAGUE_KEYWORDS)]
    return bool(weak), weak

def trend_status(t):
    y_all = t.get("yield_pct")
    y_3m  = t.get("recent_form_yield")
    y_1m  = t.get("yield_1m")

    if y_all is None:
        return "unknown", "No data"

    signals = []
    if y_1m is not None and y_3m is not None:
        diff = y_1m - y_3m
        if diff > 3:   signals.append("hot")
        elif diff < -3: signals.append("cooling")
    if y_3m is not None:
        diff = y_3m - y_all
        if diff > 2:   signals.append("improving")
        elif diff < -2: signals.append("declining")

    if "hot" in signals and "improving" in signals:
        return "hot",       "🔥 On fire — above long-term average"
    if "hot" in signals:
        return "warming",   "📈 Short-term spike — monitor if it holds"
    if "cooling" in signals and "declining" in signals:
        return "cold",      "❄️ Cold streak — well below career average"
    if "cooling" in signals:
        return "cooling",   "📉 Recent dip — watch next 30 days"
    if "declining" in signals:
        return "declining", "⚠️ 3M below all-time — reverting to mean"
    if "improving" in signals:
        return "improving", "✅ 3M above all-time — finding better form"
    return "stable",        "↔️ Stable — in line with long-term average"

def auto_recommendation(t, score, g, flags):
    y   = t.get("yield_pct", 0)
    pk  = t.get("picks_count", 0)
    vrf = t.get("verification", "free")
    bkm = t.get("bookmaker_profile", "mixed")
    rst = t.get("resets", 0)
    sy  = t.get("sport_yields", {})

    if "EXTREME_YIELD" in flags and y > 40:
        return ("AVOID",
                f"Yield of {y}% is statistically impossible to sustain long-term. "
                "Classic profile: cherry-picked entries, retroactive posting, or very low-odds manipulation. "
                "Do not follow regardless of pick count.")

    if "TINY_SAMPLE" in flags:
        return ("WATCH",
                f"Only {pk} picks — far too small to draw any conclusions. "
                f"A {y:.0f}% yield over {pk} picks is within normal variance, not evidence of skill. "
                "Monitor for at least 200+ picks before considering paid access.")

    if rst >= 3:
        return ("AVOID",
                f"{rst} account resets detected. "
                "Multiple resets indicate wiping history after drawdowns — "
                "the visible yield does not represent true long-term performance.")

    if g == "A":
        main = (f"Grade A — statistically robust edge. {pk} picks at {y}% yield "
                f"is highly unlikely to be random variance. ")
        if vrf in ("paid", "paid_copytip"):
            main += "Paid verification confirms odds were realistically available. "
        if bkm == "asian_dominant":
            main += "Asian/Pinnacle focus signals genuine market inefficiency, not soft-book farming. "
        elif bkm == "soft_only":
            main += "⚠️ Warning: soft-book only — you may face account restrictions when copying his lines. "
        if sy:
            best = max(sy, key=sy.get)
            main += f"Strongest sport: {best} ({sy[best]:+.1f}% yield). "
        return ("STRONG FOLLOW", main.strip())

    if g == "B":
        if vrf == "free":
            return ("MONITOR",
                    f"Solid numbers ({y}% yield, {pk} picks) but unverified. "
                    "Cannot confirm odds were available at posted prices. "
                    "Follow with reduced stake (30–50%) and track personally for 60 days.")
        main = "Good risk/reward profile — reduce stake ~30% vs Grade A. "
        if sy:
            worst_sport = min(sy, key=sy.get)
            if sy[worst_sport] < 0:
                main += f"Avoid his {worst_sport} picks (negative yield in that sport). "
        return ("CONSIDER", main.strip())

    if g in ("C", "D"):
        return ("AVOID / OBSERVE",
                f"Grade {g} — risk outweighs potential value. "
                "Numbers are more consistent with luck than repeatable edge. "
                "Observe only; do not invest until Grade improves to B or higher.")

    if "EXTREME_YIELD" in flags:
        return ("AVOID",
                "Flagged for extreme yield with insufficient sample size. "
                "Statistically implausible record. Classic inflated Blogabet profile.")

    return ("AVOID", f"Grade F — active risk factors: {', '.join(flags)}. Do not follow.")

def sport_yield_analysis(t):
    sy = t.get("sport_yields", {})
    top_sports = t.get("top_sports", [])
    result = []
    for sport, yld in sorted(sy.items(), key=lambda x: -x[1]):
        result.append({"sport": sport, "yield": yld})
    for sport in top_sports:
        if sport not in sy:
            result.append({"sport": sport, "yield": None})
    return result

# ─────────────────────────────────────────────
#  MAIN PROCESSING
# ─────────────────────────────────────────────
def process_tipsters(raw):
    out = []
    for t in raw:
        score, g, flags = score_tipster(t)
        trend_key, trend_text = trend_status(t)
        rec_label, rec_text   = auto_recommendation(t, score, g, flags)
        has_weak, weak        = weak_leagues_flag(t)
        sport_analysis        = sport_yield_analysis(t)

        enriched = dict(t)
        enriched.update({
            "score": score,
            "grade": g,
            "red_flags": flags,
            "trend_status": trend_key,
            "trend_text": trend_text,
            "recommendation_label": rec_label,
            "recommendation_text": rec_text,
            "weak_leagues_flag": has_weak,
            "weak_leagues": weak,
            "sport_analysis": sport_analysis,
            "analyzed_at": datetime.now(timezone.utc).isoformat(),
        })
        out.append(enriched)

    out.sort(key=lambda x: -x["score"])
    return out

# ─────────────────────────────────────────────
#  HTML GENERATION
# ─────────────────────────────────────────────
GRADE_COLORS = {
    "A": ("#16a34a", "#dcfce7"),
    "B": ("#2563eb", "#dbeafe"),
    "C": ("#d97706", "#fef3c7"),
    "D": ("#ea580c", "#ffedd5"),
    "F": ("#dc2626", "#fee2e2"),
}

TREND_COLORS = {
    "hot":       "#16a34a",
    "warming":   "#65a30d",
    "improving": "#0d9488",
    "stable":    "#6b7280",
    "declining": "#d97706",
    "cooling":   "#ea580c",
    "cold":      "#dc2626",
    "unknown":   "#9ca3af",
}

REC_COLORS = {
    "STRONG FOLLOW": "#15803d",
    "CONSIDER":      "#2563eb",
    "MONITOR":       "#7c3aed",
    "WATCH":         "#d97706",
    "AVOID / OBSERVE":"#ea580c",
    "AVOID":         "#dc2626",
}

FLAG_INFO = {
    "EXTREME_YIELD":   ("🚨", "#dc2626", "Yield is statistically implausible — cherry-picking suspected"),
    "UNVERIFIED":      ("🔓", "#d97706", "Odds not confirmed by Blogabet — may not have been available"),
    "HIGH_STAKE":      ("💰", "#d97706", "High avg stake — aggressive bankroll risk"),
    "RESET_MULTIPLE":  ("♻️", "#dc2626", "Multiple account resets — history wiped after losses"),
    "LIVE_UNVERIFIED": ("⚡", "#d97706", "Heavy live betting without verification"),
    "LOW_ODDS":        ("📉", "#6b7280", "Very low avg odds — inflated win-rate"),
    "YIELD_PUMP":      ("📊", "#dc2626", "Live yield crushes prematch — suspicious pattern"),
    "SOFT_ONLY_EDGE":  ("🏦", "#d97706", "Soft bookies only — account restriction risk for followers"),
    "TINY_SAMPLE":     ("🔬", "#7c3aed", "Sample too small — statistically meaningless"),
}

def sport_bar_html(yld, max_abs=45):
    if yld is None:
        return '<span style="color:#9ca3af;font-size:11px;font-style:italic;">no data</span>'
    clamp = max(-max_abs, min(max_abs, yld))
    w = int(abs(clamp) / max_abs * 64)
    color = "#16a34a" if yld >= 0 else "#dc2626"
    sign = "+" if yld >= 0 else ""
    return (f'<span style="display:inline-flex;align-items:center;gap:6px;">'
            f'<span style="display:inline-block;width:{w}px;height:8px;'
            f'background:{color};border-radius:3px;"></span>'
            f'<span style="font-size:12px;font-weight:700;color:{color};">{sign}{yld:.1f}%</span>'
            f'</span>')

def render_card(t):
    name  = t.get("name","?")
    url   = t.get("url","#")
    score = t.get("score", 0)
    g     = t.get("grade","F")
    flags = t.get("red_flags", [])
    gc, gbg = GRADE_COLORS.get(g, ("#6b7280","#f3f4f6"))

    y_all = t.get("yield_pct")
    y_3m  = t.get("recent_form_yield")
    y_1m  = t.get("yield_1m")
    pk    = t.get("picks_count", 0)
    vrf   = t.get("verification","free")
    bkm   = t.get("bookmaker_profile","mixed")
    sp    = t.get("specialization","focused_multi")
    rst   = t.get("resets", 0)
    wr    = t.get("win_rate")
    ods   = t.get("odds_avg")

    trend_key  = t.get("trend_status","unknown")
    trend_text = t.get("trend_text","")
    trend_col  = TREND_COLORS.get(trend_key,"#9ca3af")

    rec_label  = t.get("recommendation_label","")
    rec_text   = t.get("recommendation_text","")
    rec_col    = REC_COLORS.get(rec_label,"#6b7280")

    has_weak   = t.get("weak_leagues_flag", False)
    weak_lgs   = t.get("weak_leagues",[])
    sports     = t.get("sport_analysis",[])
    top_leagues= t.get("top_leagues",[])

    arrow_col = {"up":"#16a34a","down":"#dc2626","neutral":"#9ca3af"}

    def ypill(label, val):
        if val is None: s, c = "N/A", "#9ca3af"
        else: s, c = (f"+{val:.1f}%" if val >= 0 else f"{val:.1f}%"), ("#16a34a" if val >= 0 else "#dc2626")
        return (f'<div style="text-align:center;">'
                f'<div style="font-size:10px;color:#9ca3af;text-transform:uppercase;letter-spacing:.05em;margin-bottom:3px;">{label}</div>'
                f'<div style="font-size:17px;font-weight:800;color:{c};">{s}</div>'
                f'</div>')

    a1, c1 = trend_arrow(y_3m, y_all)
    a2, c2 = trend_arrow(y_1m, y_3m)

    trend_html = (
        f'<div style="display:flex;align-items:center;justify-content:center;gap:12px;'
        f'background:#f8fafc;border-radius:10px;padding:12px 16px;margin-bottom:14px;flex-wrap:wrap;">'
        f'{ypill("All-time", y_all)}'
        f'<span style="font-size:22px;font-weight:900;color:{arrow_col[c1]};">{a1}</span>'
        f'{ypill("3 Months", y_3m)}'
        f'<span style="font-size:22px;font-weight:900;color:{arrow_col[c2]};">{a2}</span>'
        f'{ypill("1 Month", y_1m)}'
        f'<span style="margin-left:6px;font-size:11px;padding:4px 10px;background:{trend_col}22;'
        f'color:{trend_col};border-radius:20px;font-weight:700;white-space:nowrap;">'
        f'{trend_text}</span>'
        f'</div>'
    )

    sport_html = ""
    if sports:
        rows = ""
        for s in sports:
            rows += (
                f'<div style="display:flex;align-items:center;justify-content:space-between;'
                f'padding:5px 0;border-bottom:1px solid #f1f5f9;">'
                f'<span style="font-size:13px;color:#374151;font-weight:500;">{s["sport"]}</span>'
                f'{sport_bar_html(s.get("yield"))}'
                f'</div>'
            )
        sport_html = (
            f'<div style="margin-bottom:14px;">'
            f'<div style="font-size:11px;font-weight:700;color:#64748b;text-transform:uppercase;'
            f'letter-spacing:.06em;margin-bottom:8px;">📊 Yield by Sport</div>'
            f'{rows}</div>'
        )

    flag_html = ""
    if flags:
        pills = ""
        for f in flags:
            icon, col, desc = FLAG_INFO.get(f, ("⚠️","#d97706",f))
            pills += (f'<span title="{desc}" style="display:inline-flex;align-items:center;gap:4px;'
                      f'font-size:11px;font-weight:700;padding:3px 9px;border-radius:12px;'
                      f'background:{col}18;color:{col};border:1px solid {col}40;">{icon} {f}</span>')
        flag_html = f'<div style="display:flex;flex-wrap:wrap;gap:6px;margin-bottom:14px;">{pills}</div>'

    rec_html = (
        f'<div style="background:{rec_col}12;border-left:4px solid {rec_col};'
        f'border-radius:0 10px 10px 0;padding:12px 14px;margin-bottom:14px;">'
        f'<div style="font-size:10px;font-weight:800;color:{rec_col};text-transform:uppercase;'
        f'letter-spacing:.08em;margin-bottom:6px;">⚡ Recommendation: {rec_label}</div>'
        f'<div style="font-size:13px;color:#374151;line-height:1.55;">{rec_text}</div>'
        f'</div>'
    )

    weak_html = ""
    if has_weak:
        wls = ", ".join(weak_lgs)
        weak_html = (
            f'<div style="background:#fef9c3;border:1px solid #fde047;border-radius:8px;'
            f'padding:10px 12px;margin-bottom:14px;font-size:12px;color:#713f12;">'
            f'<strong>⚠️ Weak League Alert:</strong> Posts picks in lower-tier markets ({wls}). '
            f'Yield in these leagues may be inflated — check per-sport breakdown above.'
            f'</div>'
        )

    leagues_html = ""
    if top_leagues:
        strong = [l for l in top_leagues if any(s.lower() in l.lower() for s in STRONG_LEAGUE_NAMES)]
        others = [l for l in top_leagues if l not in strong]
        def pill(l, bg): return f'<span style="font-size:11px;padding:3px 9px;background:{bg};border-radius:10px;margin:2px;display:inline-block;">{l}</span>'
        lp = "".join([pill(l,"#dcfce7") for l in strong] + [pill(l,"#f1f5f9") for l in others])
        leagues_html = (
            f'<div style="margin-bottom:12px;">'
            f'<span style="font-size:11px;font-weight:700;color:#64748b;text-transform:uppercase;">'
            f'Active Leagues</span><div style="margin-top:5px;">{lp}</div></div>'
        )

    vrf_d = {"paid_copytip":"✅ Paid+CopyTip","paid":"✅ Paid","pro":"🔵 Pro","free":"⬜ Free"}.get(vrf,vrf)
    bkm_d = {"asian_dominant":"🏆 Asian/Pinnacle","mixed":"🔀 Mixed","soft_only":"🏦 Soft only"}.get(bkm,bkm)
    sp_d  = {"mono_specialist":"🎯 Specialist","focused_multi":"📋 Focused","chaotic_multi":"🎲 Chaotic"}.get(sp,sp)
    rst_c = "#dc2626" if rst > 0 else "#16a34a"

    def sbox(label, val, color="#374151"):
        return (f'<div style="text-align:center;padding:10px 6px;background:#f8fafc;border-radius:8px;">'
                f'<div style="font-size:10px;color:#9ca3af;text-transform:uppercase;letter-spacing:.04em;">{label}</div>'
                f'<div style="font-size:13px;font-weight:700;color:{color};margin-top:3px;">{val}</div>'
                f'</div>')

    stats_row = (
        f'<div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(80px,1fr));gap:8px;margin-bottom:14px;">'
        f'{sbox("Picks", f"{pk:,}")}'
        f'{sbox("Win Rate", f"{wr}%" if wr else "N/A")}'
        f'{sbox("Avg Odds", f"{ods:.2f}" if ods else "N/A")}'
        f'{sbox("Resets", str(rst), rst_c)}'
        f'{sbox("Verification", vrf_d)}'
        f'{sbox("Style", sp_d)}'
        f'</div>'
    )

    scraped = t.get("scraped_at","")[:10]

    return (
        f'<div class="tipster-card" data-grade="{g}" data-rec="{rec_label.lower()}" '
        f'data-flags="{"yes" if flags else "no"}" '
        f'style="background:#fff;border-radius:16px;box-shadow:0 2px 14px rgba(0,0,0,.07);'
        f'padding:24px;margin-bottom:24px;border:1px solid #e2e8f0;">'

        # Header
        f'<div style="display:flex;align-items:flex-start;justify-content:space-between;margin-bottom:18px;">'
        f'<div>'
        f'<a href="{url}" target="_blank" style="font-size:22px;font-weight:900;color:#1e293b;text-decoration:none;">{name}</a>'
        f'<div style="font-size:12px;color:#64748b;margin-top:3px;">{bkm_d}</div>'
        f'</div>'
        f'<div style="text-align:center;">'
        f'<div style="width:58px;height:58px;border-radius:12px;background:{gbg};display:flex;'
        f'align-items:center;justify-content:center;font-size:30px;font-weight:900;color:{gc};">{g}</div>'
        f'<div style="font-size:11px;color:#9ca3af;margin-top:4px;">{score}/100</div>'
        f'</div></div>'

        + flag_html
        + rec_html
        + trend_html
        + sport_html
        + stats_row
        + leagues_html
        + weak_html

        + f'<div style="text-align:right;font-size:10px;color:#cbd5e1;margin-top:6px;">Data: {scraped}</div>'
        f'</div>'
    )

def render_summary(tipsters):
    counts = {"A":0,"B":0,"C":0,"D":0,"F":0}
    for t in tipsters:
        g = t.get("grade","F")
        counts[g] = counts.get(g,0) + 1
    total = len(tipsters)
    bars = ""
    for g, cnt in counts.items():
        if cnt == 0: continue
        gc, _ = GRADE_COLORS[g]
        bars += f'<div title="Grade {g}: {cnt}" style="flex:{cnt};background:{gc};height:100%;"></div>'
    pills = ""
    for g, cnt in counts.items():
        if cnt == 0: continue
        gc, gbg = GRADE_COLORS[g]
        pills += f'<span style="font-size:12px;padding:4px 12px;border-radius:12px;background:{gbg};color:{gc};font-weight:700;">Grade {g}: {cnt}</span>'
    return (
        f'<div style="background:#fff;border-radius:14px;padding:20px 24px;margin-bottom:28px;box-shadow:0 2px 8px rgba(0,0,0,.06);">'
        f'<div style="display:flex;height:12px;border-radius:6px;overflow:hidden;margin-bottom:12px;">{bars}</div>'
        f'<div style="display:flex;flex-wrap:wrap;gap:8px;">{pills}</div>'
        f'</div>'
    )

def generate_html(tipsters):
    updated = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    cards   = "\n".join(render_card(t) for t in tipsters)
    summary = render_summary(tipsters)
    n       = len(tipsters)
    top3    = [t for t in tipsters if t.get("grade") in ("A","B") and "EXTREME_YIELD" not in t.get("red_flags",[])][:3]
    top3_str= " · ".join(f'<strong>{t["name"]}</strong> ({t["grade"]})' for t in top3) or "—"

    return f'''<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>BetEdge — Tipster Tracker</title>
<style>
*,*::before,*::after{{box-sizing:border-box}}
body{{margin:0;font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;background:#f1f5f9;color:#1e293b}}
.hdr{{background:linear-gradient(135deg,#0f172a 0%,#1e3a5f 100%);padding:36px 20px;text-align:center;color:#fff}}
.hdr h1{{margin:0 0 6px;font-size:26px;font-weight:900;letter-spacing:-.5px}}
.hdr p{{margin:0;font-size:13px;opacity:.6}}
.hdr .tag{{margin-top:8px;font-size:11px;opacity:.45}}
.wrap{{max-width:840px;margin:0 auto;padding:28px 16px}}
.info{{background:#e0f2fe;border-radius:10px;padding:12px 18px;font-size:13px;color:#0369a1;margin-bottom:24px}}
.filters{{display:flex;flex-wrap:wrap;gap:8px;margin-bottom:20px}}
.fbtn{{padding:6px 16px;border-radius:20px;border:2px solid #e2e8f0;background:#fff;font-size:13px;cursor:pointer;font-weight:600;transition:all .15s}}
.fbtn:hover,.fbtn.active{{background:#0f172a;color:#fff;border-color:#0f172a}}
.foot{{text-align:center;font-size:11px;color:#94a3b8;padding:30px 0}}
</style>
</head>
<body>
<div class="hdr">
  <h1>🎯 BetEdge — Tipster Intelligence</h1>
  <p>{n} tipsters tracked · Updated {updated}</p>
  <div class="tag">Scoring: yield · sample · verification · bookmaker profile · form · specialization</div>
</div>
<div class="wrap">
  <div class="info">ℹ️ Best current bets: {top3_str}</div>
  {summary}
  <div class="filters">
    <button class="fbtn active" onclick="filter(this,'all')">All ({n})</button>
    <button class="fbtn" onclick="filter(this,'A')">Grade A</button>
    <button class="fbtn" onclick="filter(this,'B')">Grade B</button>
    <button class="fbtn" onclick="filter(this,'follow')">✅ Follow</button>
    <button class="fbtn" onclick="filter(this,'avoid')">❌ Avoid</button>
    <button class="fbtn" onclick="filter(this,'flags')">🚩 Has Flags</button>
  </div>
  <div id="cards">{cards}</div>
</div>
<div class="foot">BetEdge external info source · Manual data entry · Not financial advice<br>Dashboard auto-regenerates on every GitHub push</div>
<script>
function filter(btn,type){{
  document.querySelectorAll('.fbtn').forEach(b=>b.classList.remove('active'));
  btn.classList.add('active');
  document.querySelectorAll('.tipster-card').forEach(c=>{{
    let show=true;
    if(type==='A') show=c.dataset.grade==='A';
    else if(type==='B') show=c.dataset.grade==='B';
    else if(type==='follow') show=c.dataset.rec.includes('follow')||c.dataset.rec.includes('consider');
    else if(type==='avoid') show=c.dataset.rec.includes('avoid');
    else if(type==='flags') show=c.dataset.flags==='yes';
    c.style.display=show?'':'none';
  }});
}}
</script>
</body>
</html>'''

# ─────────────────────────────────────────────
#  HISTORY
# ─────────────────────────────────────────────
def update_history(scored):
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    hist = {}
    if os.path.exists(HIST_FILE):
        with open(HIST_FILE) as f:
            loaded = json.load(f)
            # Guard: old history.json may have been a list instead of dict
            hist = loaded if isinstance(loaded, dict) else {}
    for t in scored:
        name = t["name"]
        if name not in hist: hist[name] = []
        hist[name] = [e for e in hist[name] if e["date"] != today]
        hist[name].append({"date": today, "score": t["score"], "grade": t["grade"],
                           "yield": t.get("yield_pct"), "picks": t.get("picks_count")})
        hist[name] = sorted(hist[name], key=lambda e: e["date"])[-90:]
    with open(HIST_FILE, "w") as f:
        json.dump(hist, f, indent=2)

# ─────────────────────────────────────────────
#  ENTRY POINT
# ─────────────────────────────────────────────
def main():
    if not os.path.exists(RAW_FILE):
        print("No tipsters_raw.json — using demo data")
        raw = [{"name":"Demo","url":"https://blogabet.com/demo","yield_pct":7.5,"picks_count":450,
                "verification":"paid","bookmaker_profile":"mixed","recent_form_yield":8.2,
                "yield_1m":9.1,"specialization":"focused_multi","resets":0,
                "analysis_quality":"short_desc","top_sports":["Football"],
                "top_leagues":["Bundesliga"],"sport_yields":{"Football":7.5},
                "odds_avg":1.90,"win_rate":52,"live_pct":8,"avg_hours_before_match":18,
                "followers":120,"avg_stake":4.0,"pinnacle_yield":None,"soft_bookie_yield":None,
                "live_yield":None,"prematch_yield":None,"sport_percentages":{},
                "bookie_percentages":{},"top_bookmakers":[],
                "scraped_at":datetime.now(timezone.utc).isoformat()}]
    else:
        with open(RAW_FILE) as f:
            raw = json.load(f)

    print(f"Loaded {len(raw)} tipsters")
    scored = process_tipsters(raw)

    with open(SCORED_FILE,"w") as f:
        json.dump(scored, f, indent=2, ensure_ascii=False)
    print(f"Saved → {SCORED_FILE}")

    update_history(scored)
    print(f"History → {HIST_FILE}")

    html = generate_html(scored)
    with open(HTML_FILE,"w",encoding="utf-8") as f:
        f.write(html)
    print(f"Dashboard → {HTML_FILE}")

    print("\n── RESULTS ──")
    for t in scored:
        fs = (" 🚩" + ",".join(t["red_flags"])) if t["red_flags"] else ""
        print(f"  [{t['grade']}] {t['score']:>3}/100  {t['name']:<25} yield={t.get('yield_pct','?')}%  {t['recommendation_label']}{fs}")

if __name__ == "__main__":
    main()
