#!/usr/bin/env python3
"""
BLOGABET ANALYZER + DASHBOARD GENERATOR
════════════════════════════════════════
Czyta data/tipsters_raw.json (output scrapera), przeprowadza scoring
wg 7-fazowej metodologii, generuje docs/index.html.
"""

import json
from datetime import datetime, timezone, timedelta
from pathlib import Path
from copy import deepcopy

ROOT = Path(__file__).parent
DATA_DIR = ROOT / "data"
DOCS_DIR = ROOT / "docs"
RAW_FILE = DATA_DIR / "tipsters_raw.json"
SCORED_FILE = DATA_DIR / "tipsters_scored.json"
HISTORY_FILE = DATA_DIR / "history.json"


def now_utc():
    return datetime.now(timezone.utc)

def load_json(path, default=None):
    if path.exists():
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    return default if default is not None else []

def save_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2, default=str)


# ── SCORING ──────────────────────────────────────────────────────────
def score_thresholds(value, thresholds):
    for thresh, score in thresholds:
        if value >= thresh:
            return score
    return 0

def calculate_score(t):
    s = {}
    s["yield"] = score_thresholds(t.get("yield_pct", 0),
        [(12,25),(8,20),(5,15),(3,10),(0,5),(-999,0)])
    s["picks"] = score_thresholds(t.get("picks_count", 0),
        [(1000,15),(500,10),(200,5),(0,0)])
    vmap = {"paid_copytip":15,"paid":13,"pro":10,"verified_legacy":6,"free":0}
    s["verification"] = vmap.get(t.get("verification","free"), 0)
    bmap = {"asian_dominant":10,"mixed":5,"soft_only":0}
    s["bookmaker"] = bmap.get(t.get("bookmaker_profile","soft_only"), 0)
    s["form"] = score_thresholds(t.get("recent_form_yield", 0),
        [(6,10),(3,6),(0,3),(-999,0)])
    smap = {"mono_specialist":10,"focused_multi":6,"chaotic_multi":2}
    s["specialization"] = smap.get(t.get("specialization","chaotic_multi"), 2)
    s["resets"] = score_thresholds(-t.get("resets",0),
        [(0,5),(-1,3),(-2,1),(-999,0)])
    amap = {"detailed_value":10,"short_desc":4,"none":0}
    s["analysis"] = amap.get(t.get("analysis_quality","none"), 0)
    return {"total": sum(s.values()), "breakdown": s}

def detect_red_flags(t):
    flags = []
    if t.get("resets",0) >= 2: flags.append("RESET_MULTIPLE")
    if t.get("verification","free") == "free": flags.append("UNVERIFIED")
    if t.get("avg_stake",0) > 15: flags.append("HIGH_STAKE")
    if t.get("yield_pct",0) > 15 and t.get("picks_count",0) > 1000:
        flags.append("EXTREME_YIELD")
    if t.get("live_pct",0) > 50 and t.get("verification") not in ("paid","paid_copytip"):
        flags.append("LIVE_UNVERIFIED")
    if 0 < t.get("odds_avg",0) < 1.30: flags.append("LOW_ODDS")
    ly, py = t.get("live_yield"), t.get("prematch_yield")
    if ly and py and ly > 20 and py < 5: flags.append("YIELD_PUMP")
    piny, softy = t.get("pinnacle_yield"), t.get("soft_bookie_yield")
    if piny is not None and softy is not None:
        if softy > 8 and piny < 0: flags.append("SOFT_ONLY_EDGE")
    return flags

def classify_market(t):
    if t.get("live_pct",0) > 40: return "live"
    if t.get("avg_hours_before_match",24) > 48: return "early"
    return "mature"

def analyze(raw):
    results = []
    for t in raw:
        t = deepcopy(t)
        sc = calculate_score(t)
        t["score"] = sc["total"]
        t["score_breakdown"] = sc["breakdown"]
        t["red_flags"] = detect_red_flags(t)
        t["red_flag_count"] = len(t["red_flags"])
        t["market_type"] = classify_market(t)

        s = t["score"]
        t["grade"] = "A" if s>=80 else "B" if s>=65 else "C" if s>=50 else "D" if s>=35 else "F"
        if t["red_flag_count"] >= 3:
            t["grade"], t["grade_note"] = "F", "Auto-F: ≥3 red flags"
        elif t["red_flag_count"] >= 2 and t["grade"] in ("A","B"):
            t["grade"] = chr(ord(t["grade"])+1)
            t["grade_note"] = "Degradacja: 2 red flags"

        t["analyzed_at"] = now_utc().isoformat()
        results.append(t)

    results.sort(key=lambda x: x["score"], reverse=True)
    return results


def update_history(scored):
    history = load_json(HISTORY_FILE, [])
    today = now_utc().strftime("%Y-%m-%d")
    history = [h for h in history if h["date"] != today]
    history.append({
        "date": today,
        "count": len(scored),
        "avg_score": round(sum(t["score"] for t in scored)/max(len(scored),1), 1),
        "top5": [{"name":t["name"],"score":t["score"],"yield":t.get("yield_pct",0),
                  "grade":t["grade"]} for t in scored[:5]]
    })
    cutoff = (now_utc() - timedelta(days=90)).strftime("%Y-%m-%d")
    history = [h for h in history if h["date"] >= cutoff]
    save_json(HISTORY_FILE, history)
    return history


# ── DASHBOARD HTML ───────────────────────────────────────────────────
def generate_dashboard(scored, history):
    top = scored[:30]
    updated = now_utc().strftime("%Y-%m-%d %H:%M UTC")
    total = len(scored)
    avg_sc = sum(t["score"] for t in scored)/max(total,1)
    gc = {}
    for t in scored: gc[t["grade"]] = gc.get(t["grade"],0)+1
    flagged = sum(1 for t in scored if t["red_flag_count"]>0)
    has_demo = any(t.get("is_demo") for t in scored)

    grade_colors = {"A":"#10B981","B":"#06B6D4","C":"#F59E0B","D":"#F97316","F":"#EF4444"}
    market_icons = {"early":"🕐","mature":"📊","live":"⚡"}
    flag_names = {
        "RESET_MULTIPLE":"Resety","UNVERIFIED":"Niezweryfikowany",
        "HIGH_STAKE":"Wysoki stake","EXTREME_YIELD":"Ekstremalny yield",
        "LIVE_UNVERIFIED":"Live bez werf.","LOW_ODDS":"Niskie kursy",
        "YIELD_PUMP":"Pompowanie yield","SOFT_ONLY_EDGE":"Soft-only edge"
    }

    rows = ""
    for i,t in enumerate(top):
        clr = grade_colors.get(t["grade"],"#71717a")
        flags_h = "".join(f'<span class="fl">{flag_names.get(f,f)}</span>' for f in t.get("red_flags",[]))
        sports_h = "".join(f'<span class="sp">{s}</span>' for s in t.get("top_sports",[])[:2])
        mi = market_icons.get(t.get("market_type",""),"")
        yld = t.get("yield_pct",0)
        rf = t.get("recent_form_yield",0)
        demo = ' class="demo-row"' if t.get("is_demo") else ""
        rows += f"""<tr{demo}>
<td class="rk">#{i+1}</td>
<td class="nm"><div class="tn">{t['name']}</div><div class="tm">{mi} {sports_h}</div></td>
<td><span class="gr" style="background:{clr}18;color:{clr};border:1px solid {clr}40">{t['grade']}</span></td>
<td class="n">{t['score']}</td>
<td class="n {'p' if yld>0 else 'ng'}">{yld:+.1f}%</td>
<td class="n">{t.get('picks_count',0):,}</td>
<td class="n {'p' if rf>0 else 'ng'}">{rf:+.1f}%</td>
<td class="n">{t.get('odds_avg',0):.2f}</td>
<td class="n">{t.get('win_rate',0):.0f}%</td>
<td class="vr">{t.get('verification','?')}</td>
<td class="fc">{flags_h or '<span class="ok">✓</span>'}</td>
</tr>"""

    # History chart data
    hist_json = json.dumps(history[-30:], ensure_ascii=False)

    demo_banner = ""
    if has_demo:
        demo_banner = """<div class="demo-banner">
⚠️ <strong>TRYB DEMO</strong> — wyświetlane są dane przykładowe.
Ustaw BLOGABET_USER i BLOGABET_PASS w GitHub Secrets, aby uruchomić prawdziwy scraping.
</div>"""

    html = f"""<!DOCTYPE html>
<html lang="pl"><head>
<meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Blogabet Tipster Tracker</title>
<style>
@import url('https://fonts.googleapis.com/css2?family=JetBrains+Mono:wght@400;600;700&family=Plus+Jakarta+Sans:wght@400;500;600;700;800&display=swap');
:root{{--b0:#07070b;--b1:#0f0f17;--b2:#171720;--b3:#1f1f2c;--t1:#ededf0;--t2:#a0a0aa;--t3:#62626e;
--bd:rgba(255,255,255,.06);--g:#10B981;--c:#06B6D4;--am:#F59E0B;--o:#F97316;--r:#EF4444;--p:#8B5CF6;}}
*{{margin:0;padding:0;box-sizing:border-box}}
body{{background:var(--b0);color:var(--t1);font-family:'Plus Jakarta Sans',system-ui,sans-serif;line-height:1.5}}
.w{{max-width:1440px;margin:0 auto;padding:20px}}
header{{display:flex;justify-content:space-between;align-items:flex-end;padding:20px 0;border-bottom:1px solid var(--bd);margin-bottom:20px}}
h1{{font-size:21px;font-weight:800;letter-spacing:-.4px}}h1 em{{color:var(--g);font-style:normal}}
.up{{font:600 11px/1 'JetBrains Mono',monospace;color:var(--t3)}}
.demo-banner{{background:rgba(245,158,11,.1);border:1px solid rgba(245,158,11,.25);border-radius:10px;padding:12px 16px;margin-bottom:16px;font-size:13px;color:var(--am)}}
.demo-row{{opacity:.6}}
.sg{{display:grid;grid-template-columns:repeat(auto-fit,minmax(155px,1fr));gap:10px;margin-bottom:20px}}
.sc{{background:var(--b1);border:1px solid var(--bd);border-radius:10px;padding:14px}}
.sc .l{{font:600 10px/1.2 'Plus Jakarta Sans';color:var(--t3);text-transform:uppercase;letter-spacing:1px;margin-bottom:4px}}
.sc .v{{font:800 26px/1.1 'JetBrains Mono',monospace}}
.fb{{display:flex;gap:6px;flex-wrap:wrap;margin-bottom:14px}}
.fb button{{padding:5px 12px;border-radius:7px;border:1px solid var(--bd);background:var(--b1);
color:var(--t2);font:600 11px/1.4 'Plus Jakarta Sans';cursor:pointer;transition:.15s}}
.fb button:hover,.fb button.a{{border-color:var(--g);color:var(--g);background:rgba(16,185,129,.07)}}
.tw{{background:var(--b1);border:1px solid var(--bd);border-radius:10px;overflow-x:auto}}
.th{{padding:14px 18px;border-bottom:1px solid var(--bd);display:flex;justify-content:space-between;align-items:center}}
.th h2{{font-size:14px;font-weight:700}}.th span{{font-size:11px;color:var(--t3)}}
table{{width:100%;border-collapse:collapse;font-size:12px}}
thead th{{padding:10px 12px;text-align:left;font:600 10px/1 'Plus Jakarta Sans';color:var(--t3);
text-transform:uppercase;letter-spacing:.5px;border-bottom:1px solid var(--bd);white-space:nowrap}}
tbody tr{{border-bottom:1px solid var(--bd);transition:background .1s}}
tbody tr:hover{{background:rgba(255,255,255,.02)}}
td{{padding:10px 12px;vertical-align:middle}}
.rk{{font:600 12px 'JetBrains Mono';color:var(--t3);width:36px}}
.nm .tn{{font-weight:700;color:#fff;white-space:nowrap}}.nm .tm{{font-size:10px;color:var(--t3);margin-top:2px;display:flex;gap:3px;align-items:center;flex-wrap:wrap}}
.n{{font-family:'JetBrains Mono',monospace;text-align:right}}.p{{color:var(--g)}}.ng{{color:var(--r)}}
.gr{{display:inline-block;padding:2px 9px;border-radius:5px;font:700 11px 'JetBrains Mono'}}
.sp{{display:inline-block;padding:0 5px;border-radius:3px;background:rgba(139,92,246,.12);color:var(--p);font:600 9px/1.5 sans-serif}}
.fl{{display:inline-block;padding:1px 6px;border-radius:3px;background:rgba(239,68,68,.1);color:var(--r);font:600 9px/1.5 sans-serif;margin:1px}}
.ok{{color:var(--g);font-size:11px;font-weight:600}}
.vr{{font:600 10px 'JetBrains Mono';white-space:nowrap}}
.mn{{margin-top:20px;padding:14px;border-radius:10px;background:rgba(139,92,246,.05);border:1px solid rgba(139,92,246,.12);font-size:11px;color:var(--t2);line-height:1.6}}
.mn strong{{color:var(--p)}}
.hc{{margin-top:20px;background:var(--b1);border:1px solid var(--bd);border-radius:10px;padding:16px}}
.hc h3{{font-size:13px;margin-bottom:12px}}
.hb{{display:flex;gap:3px;align-items:flex-end;height:100px}}
.hb .bar{{flex:1;border-radius:3px 3px 0 0;background:var(--g);min-width:6px;position:relative;transition:.2s}}
.hb .bar:hover{{opacity:.8}}.hb .bar:hover::after{{content:attr(data-tip);position:absolute;bottom:calc(100% + 4px);
left:50%;transform:translateX(-50%);background:var(--b3);color:var(--t1);padding:3px 7px;border-radius:4px;
font:600 10px 'JetBrains Mono';white-space:nowrap;z-index:10}}
footer{{margin-top:24px;padding-top:12px;border-top:1px solid var(--bd);text-align:center;font-size:10px;color:var(--t3)}}
@media(max-width:768px){{.w{{padding:10px}}.sg{{grid-template-columns:repeat(2,1fr)}}table{{font-size:10px}}td,th{{padding:6px 4px}}}}
</style></head><body>
<div class="w">
<header><div><h1>Blogabet <em>Tipster Tracker</em></h1>
<p style="font-size:11px;color:var(--t3);margin-top:3px">Automatyczna analiza · Scoring 0–100 · Aktualizacja co 24h · 1# BetEdge external info source</p>
</div><div class="up">{updated}</div></header>

{demo_banner}

<div class="sg">
<div class="sc"><div class="l">Analizowanych</div><div class="v">{total}</div></div>
<div class="sc"><div class="l">Śr. score</div><div class="v" style="color:var(--c)">{avg_sc:.0f}</div></div>
<div class="sc"><div class="l">Grade A</div><div class="v" style="color:var(--g)">{gc.get('A',0)}</div></div>
<div class="sc"><div class="l">Grade B</div><div class="v" style="color:var(--c)">{gc.get('B',0)}</div></div>
<div class="sc"><div class="l">Red flags</div><div class="v" style="color:var(--r)">{flagged}</div></div>
<div class="sc"><div class="l">Disqualified</div><div class="v" style="color:var(--t3)">{gc.get('F',0)}</div></div>
</div>

<div class="fb">
<button class="a" onclick="ft('all',this)">Wszyscy</button>
<button onclick="ft('A',this)">Grade A</button>
<button onclick="ft('B',this)">Grade B</button>
<button onclick="ft('early',this)">🕐 Early</button>
<button onclick="ft('mature',this)">📊 Mature</button>
<button onclick="ft('live',this)">⚡ Live</button>
<button onclick="ft('flag',this)">🚩 Flags</button>
<button onclick="ft('clean',this)">✓ Clean</button>
</div>

<div class="tw"><div class="th"><h2>Ranking typerów</h2><span>Score ↓ · top {len(top)}</span></div>
<table id="mt"><thead><tr>
<th>#</th><th>Typer</th><th>Grade</th><th>Score</th><th>Yield</th><th>Picks</th>
<th>Forma 3M</th><th>Avg Odds</th><th>Win%</th><th>Weryfikacja</th><th>Flagi</th>
</tr></thead><tbody>{rows}</tbody></table></div>

<div class="hc"><h3>📈 Historia średniego score (ostatnie 30 dni)</h3><div class="hb" id="hist"></div></div>

<div class="mn">
<strong>Scoring:</strong> Yield (25) + Picks (15) + Weryfikacja (15) + Bukmacher (10) +
Forma 3M (10) + Specjalizacja (10) + Resety (5) + Analizy (10) = max 100.
≥3 red flags → auto Grade F. Scraping + analiza uruchamiane codziennie o 06:00 UTC przez GitHub Actions.
</div>
<footer>Blogabet Tipster Tracker · w pełni automatyczny · wyniki historyczne nie gwarantują zysków</footer>
</div>

<script>
const H={hist_json};
!function(){{const c=document.getElementById('hist');if(!H.length)return;
const mx=Math.max(...H.map(h=>h.avg_score||0),1);
H.forEach(h=>{{const b=document.createElement('div');b.className='bar';
const pct=((h.avg_score||0)/mx)*100;b.style.height=pct+'%';
b.dataset.tip=h.date+': '+Math.round(h.avg_score||0);c.appendChild(b)}})}}();

function ft(f,el){{
document.querySelectorAll('.fb button').forEach(b=>b.classList.remove('a'));
el.classList.add('a');
document.querySelectorAll('#mt tbody tr').forEach(r=>{{
const g=r.querySelector('.gr')?.textContent?.trim();
const fl=r.querySelector('.fc')?.textContent||'';
const m=r.querySelector('.tm')?.textContent||'';
let s=true;
if(f==='A'||f==='B')s=g===f;
else if(f==='early')s=m.includes('🕐');
else if(f==='mature')s=m.includes('📊');
else if(f==='live')s=m.includes('⚡');
else if(f==='flag')s=!fl.includes('✓');
else if(f==='clean')s=fl.includes('✓');
r.style.display=s?'':'none'}})}}
</script></body></html>"""

    DOCS_DIR.mkdir(parents=True, exist_ok=True)
    (DOCS_DIR / "index.html").write_text(html, encoding="utf-8")
    print(f"  ✓ Dashboard → docs/index.html")


# ── MAIN ─────────────────────────────────────────────────────────────
def main():
    print("="*55)
    print("  BLOGABET ANALYZER")
    print(f"  {now_utc().strftime('%Y-%m-%d %H:%M UTC')}")
    print("="*55)

    raw = load_json(RAW_FILE, [])
    if not raw:
        print(f"\n✗ Brak danych w {RAW_FILE}")
        print("  Uruchom najpierw: python scraper.py")
        return

    print(f"\n→ Wczytano {len(raw)} typerów")
    print("→ Scoring...")
    scored = analyze(raw)
    save_json(SCORED_FILE, scored)
    print(f"  ✓ → {SCORED_FILE}")

    print("\n→ TOP 5:")
    for i,t in enumerate(scored[:5]):
        fl = f" ⚠ {','.join(t['red_flags'])}" if t['red_flags'] else ""
        print(f"  {i+1}. [{t['grade']}] {t['name']}: score={t['score']}, "
              f"yield={t.get('yield_pct',0):+.1f}%, picks={t.get('picks_count',0)}{fl}")

    print("\n→ Historia...")
    history = update_history(scored)
    print(f"  ✓ {len(history)} wpisów")

    print("\n→ Dashboard...")
    generate_dashboard(scored, history)
    print("\n" + "="*55)
    print("  GOTOWE!")
    print("="*55)


if __name__ == "__main__":
    main()
