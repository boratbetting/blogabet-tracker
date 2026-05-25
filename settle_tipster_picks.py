#!/usr/bin/env python3
"""
BetEdge — settle_tipster_picks.py
Automatic settlement of Blogabet tipster picks using BetsAPI results.
Runs after email_parser.py in GitHub Actions.

Flow:
1. Read email_signals.json (parsed picks)
2. Read existing settlements (tipster_settlements.json)
3. For unsettled volleyball picks, search BetsAPI for match results
4. Settle: WON / LOST / VOID / PENDING
5. Save to docs/data/tipster_settlements.json → GitHub Pages
"""
import json, os, re, time, hashlib
from datetime import datetime, timedelta

BETSAPI_TOKEN = os.environ.get("BETSAPI_TOKEN", "")
SIGNALS_PATH = "data/email_signals.json"
SETTLEMENTS_PATH = "data/tipster_settlements.json"
DOCS_SETTLEMENTS = "docs/data/tipster_settlements.json"

def load_json(path):
    try:
        with open(path) as f:
            return json.load(f)
    except:
        return {}

def save_json(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, 'w') as f:
        json.dump(data, f, indent=2, ensure_ascii=False)

def search_betsapi_match(home, away, date_str=None):
    """Search BetsAPI for a volleyball match by team names."""
    if not BETSAPI_TOKEN:
        return None
    import requests
    # Try searching by team name
    query = f"{home} {away}".replace("(W)", "").replace("(M)", "").strip()
    try:
        # Search ended events
        r = requests.get("https://api.b365api.com/v1/events/search",
            params={"token": BETSAPI_TOKEN, "sport_id": 91, "q": query[:50]},
            timeout=15)
        if r.status_code != 200:
            return None
        data = r.json()
        if data.get("success") != 1:
            return None
        results = data.get("results", [])
        if not results:
            return None
        # Find best match
        best = None
        best_score = 0
        hl = home.lower().replace("(w)", "").strip()
        al = away.lower().replace("(w)", "").strip()
        for ev in results:
            eh = (ev.get("home", {}) or {}).get("name", "").lower()
            ea = (ev.get("away", {}) or {}).get("name", "").lower()
            score = 0
            if hl in eh or eh in hl: score += 40
            if al in ea or ea in al: score += 40
            # Last word match
            hlw = hl.split()[-1] if hl.split() else ""
            alw = al.split()[-1] if al.split() else ""
            if hlw and len(hlw) > 3 and hlw in eh: score += 20
            if alw and len(alw) > 3 and alw in ea: score += 20
            if score > best_score:
                best_score = score
                best = ev
        if best_score < 40:
            return None
        return best
    except Exception as e:
        print(f"  [BetsAPI search] error: {e}")
        return None

def get_event_result(event_id):
    """Get result for a specific BetsAPI event."""
    if not BETSAPI_TOKEN:
        return None
    import requests
    try:
        time.sleep(0.4)
        r = requests.get("https://api.b365api.com/v1/event/view",
            params={"token": BETSAPI_TOKEN, "event_id": event_id},
            timeout=15)
        if r.status_code != 200:
            return None
        data = r.json()
        if data.get("success") != 1:
            return None
        results = data.get("results", [])
        ev = results[0] if results else {}
        ts = str(ev.get("time_status", ""))
        if ts != "3":  # Not finished
            return None
        ss = ev.get("ss", "")
        if not ss or "-" not in ss:
            return None
        parts = ss.split("-")
        return {
            "home_sets": int(parts[0]),
            "away_sets": int(parts[1]),
            "ss": ss,
            "home": (ev.get("home", {}) or {}).get("name", ""),
            "away": (ev.get("away", {}) or {}).get("name", ""),
        }
    except Exception as e:
        print(f"  [BetsAPI result] error: {e}")
        return None

def parse_pick(pick_text):
    """Extract market type and selection from pick text."""
    pick = (pick_text or "").lower()
    result = {"market": "unknown", "selection": "unknown", "line": None}
    
    # Moneyline / Winner
    if "winner" in pick or "moneyline" in pick or ("ml" in pick and "game lines" in pick):
        result["market"] = "moneyline"
    
    # Handicap
    hc = re.search(r'handicap.*?([+-]?\d+\.?\d*)', pick)
    if hc:
        result["market"] = "set_hcp"
        result["line"] = float(hc.group(1))
    
    # Total
    tot = re.search(r'(over|under)\s+(\d+\.?\d*)', pick)
    if tot:
        result["market"] = "total"
        result["selection"] = tot.group(1)
        result["line"] = float(tot.group(2))
    
    return result

def settle_pick(parsed, result):
    """Determine if pick won or lost."""
    if not result:
        return "pending"
    
    hs, as_ = result["home_sets"], result["away_sets"]
    home_won = hs > as_
    
    market = parsed["market"]
    if market == "moneyline":
        # Need to determine which side was picked
        return "won" if home_won else "lost"  # simplified
    
    if market == "set_hcp" and parsed["line"] is not None:
        margin = hs - as_ + parsed["line"]
        return "won" if margin > 0 else "lost" if margin < 0 else "void"
    
    return "pending"

def main():
    print("=" * 50)
    print("  TIPSTER SETTLEMENT ENGINE")
    print(f"  {datetime.utcnow().strftime('%Y-%m-%d %H:%M UTC')}")
    print("=" * 50)
    
    # Load signals
    signals = load_json(SIGNALS_PATH)
    if isinstance(signals, dict):
        signals = []
    print(f"📬 Loaded {len(signals)} signals")
    
    # Load existing settlements
    settlements = load_json(SETTLEMENTS_PATH)
    if not isinstance(settlements, dict):
        settlements = {}
    print(f"📦 Existing settlements: {len(settlements)}")
    
    # Filter: only volleyball, only unsettled
    volleyball = [s for s in signals if s.get("is_volleyball")]
    unsettled = [s for s in volleyball if s.get("id") and s["id"] not in settlements
                 or (s.get("id") and settlements.get(s["id"], {}).get("status") == "pending")]
    print(f"🏐 Volleyball signals: {len(volleyball)}, unsettled: {len(unsettled)}")
    
    if not BETSAPI_TOKEN:
        print("⚠️ No BETSAPI_TOKEN — skipping BetsAPI lookups")
        # Still save stats
        save_json(SETTLEMENTS_PATH, settlements)
        save_json(DOCS_SETTLEMENTS, settlements)
        return
    
    new_settled = 0
    api_calls = 0
    
    for s in unsettled[:30]:  # Cap at 30 per run (rate limit)
        sid = s["id"]
        match_name = (s.get("match") or "").replace("<br/>", " ").strip()
        home = (s.get("home") or "").replace("<br/>", " ").strip()
        away = (s.get("away") or "").replace("<br/>", " ").strip()
        
        if not match_name and not (home and away):
            settlements[sid] = {"status": "unmatched", "reason": "no match name",
                "tipster": s.get("tipster", ""), "timestamp": s.get("timestamp", "")}
            continue
        
        # Check age — only settle matches older than 4 hours
        try:
            ts = datetime.fromisoformat(s["timestamp"].replace("Z", "+00:00"))
            age_hours = (datetime.now(ts.tzinfo) - ts).total_seconds() / 3600
            if age_hours < 4:
                continue  # Too recent, match might still be in progress
        except:
            pass
        
        print(f"  🔍 {s.get('tipster','?')}: {home or '?'} vs {away or '?'}")
        
        # Search BetsAPI
        ev = search_betsapi_match(home or match_name.split(" vs ")[0] if " vs " in match_name else match_name,
                                   away or match_name.split(" vs ")[1] if " vs " in match_name else "")
        api_calls += 1
        time.sleep(0.4)
        
        if not ev:
            settlements[sid] = {"status": "unmatched", "reason": "not found in BetsAPI",
                "tipster": s.get("tipster", ""), "match": match_name,
                "timestamp": s.get("timestamp", "")}
            print(f"    ❌ Not found")
            continue
        
        # Get result
        event_id = str(ev.get("id", ""))
        result = get_event_result(event_id)
        api_calls += 1
        
        if not result:
            settlements[sid] = {"status": "pending", "reason": "match not finished yet",
                "tipster": s.get("tipster", ""), "match": match_name,
                "event_id": event_id, "timestamp": s.get("timestamp", "")}
            print(f"    ⏳ Pending (not finished)")
            continue
        
        # Parse pick and settle
        parsed = parse_pick(s.get("pick", ""))
        
        # Determine which side was picked for ML
        if parsed["market"] == "moneyline":
            pick_text = (s.get("pick", "") + " " + match_name).lower()
            if home and home.lower().split()[-1] in pick_text:
                home_picked = True
            elif away and away.lower().split()[-1] in pick_text:
                home_picked = False
            else:
                home_picked = True  # Default
            
            home_won = result["home_sets"] > result["away_sets"]
            status = "won" if (home_picked == home_won) else "lost"
        elif parsed["market"] == "set_hcp":
            hs, as_ = result["home_sets"], result["away_sets"]
            margin = hs - as_ + parsed["line"]
            status = "won" if margin > 0 else "lost" if margin < 0 else "void"
        elif parsed["market"] == "total":
            # Would need total points — skip for now
            status = "pending"
        else:
            status = "pending"
        
        # Calculate profit
        odds = s.get("odds", 1.0)
        stake = s.get("stake", 1)
        profit = 0
        if status == "won":
            profit = round((odds - 1) * stake, 2)
        elif status == "lost":
            profit = -stake
        
        settlements[sid] = {
            "status": status, "tipster": s.get("tipster", ""),
            "match": f"{result['home']} vs {result['away']}",
            "ss": result["ss"], "event_id": event_id,
            "market": parsed["market"], "odds": odds, "stake": stake,
            "profit": profit, "sport": "volleyball",
            "settled_at": datetime.utcnow().isoformat(),
            "timestamp": s.get("timestamp", "")
        }
        
        icon = "✅" if status == "won" else "❌" if status == "lost" else "🔄"
        print(f"    {icon} {status.upper()} — {result['ss']} — profit: {profit:+.1f}u")
        if status in ("won", "lost"):
            new_settled += 1
    
    # Save
    save_json(SETTLEMENTS_PATH, settlements)
    save_json(DOCS_SETTLEMENTS, settlements)
    
    # Stats
    stats = {"won": 0, "lost": 0, "void": 0, "pending": 0, "unmatched": 0}
    total_profit = 0
    for sid, s in settlements.items():
        st = s.get("status", "pending")
        stats[st] = stats.get(st, 0) + 1
        total_profit += s.get("profit", 0)
    
    print(f"\n{'='*50}")
    print(f"📊 Settlement Summary")
    print(f"  New settled: {new_settled}")
    print(f"  Won: {stats['won']} · Lost: {stats['lost']} · Void: {stats.get('void',0)}")
    print(f"  Pending: {stats['pending']} · Unmatched: {stats['unmatched']}")
    print(f"  Total profit: {total_profit:+.1f}u")
    print(f"  API calls: {api_calls}")

if __name__ == "__main__":
    main()
