import json
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).parent
DATA_DIR = ROOT / "data"
RAW_FILE = DATA_DIR / "tipsters_raw.json"

def main():
    print("BLOGABET DATA LOADER")
    if RAW_FILE.exists():
        with open(RAW_FILE) as f:
            data = json.load(f)
        if data and not all(t.get("is_demo") for t in data):
            print(f"OK: {len(data)} typers from Colab")
            return
    print("No Colab data — generating demo")
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(timezone.utc).isoformat()
    demo = [
        {"name":"DEMO_SharpEdge","url":"https://blogabet.com/demo","yield_pct":8.4,"picks_count":1247,"verification":"paid","bookmaker_profile":"asian_dominant","recent_form_yield":6.2,"specialization":"mono_specialist","resets":0,"analysis_quality":"detailed_value","top_sports":["Football"],"top_leagues":["Eng. Premier"],"avg_stake":5.2,"odds_avg":1.95,"win_rate":54.2,"live_pct":10,"avg_hours_before_match":18,"followers":342,"months_active":24,"profitable_months_12":8,"pinnacle_yield":7.8,"soft_bookie_yield":9.1,"live_yield":3.2,"prematch_yield":9.0,"sport_percentages":{},"bookie_percentages":{},"top_bookmakers":["Pinnacle"],"scraped_at":ts,"is_demo":True},
        {"name":"DEMO_IceHockey_Pro","url":"https://blogabet.com/demo2","yield_pct":7.1,"picks_count":2103,"verification":"paid_copytip","bookmaker_profile":"asian_dominant","recent_form_yield":5.8,"specialization":"mono_specialist","resets":0,"analysis_quality":"detailed_value","top_sports":["Ice Hockey"],"top_leagues":["NHL","KHL"],"avg_stake":3.0,"odds_avg":1.88,"win_rate":56.1,"live_pct":8,"avg_hours_before_match":8,"followers":512,"months_active":36,"profitable_months_12":9,"pinnacle_yield":6.8,"soft_bookie_yield":7.5,"live_yield":5.0,"prematch_yield":7.2,"sport_percentages":{},"bookie_percentages":{},"top_bookmakers":["Pinnacle","Bet365"],"scraped_at":ts,"is_demo":True},
    ]
    with open(RAW_FILE, "w") as f:
        json.dump(demo, f, indent=2, ensure_ascii=False)
    print(f"Demo: {len(demo)} tipsters")

if __name__ == "__main__":
    main()
