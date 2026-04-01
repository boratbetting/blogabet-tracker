#!/usr/bin/env python3
"""
BLOGABET AUTO-SCRAPER v5 — wait for login modal to render
"""

import asyncio
import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, Dict, List
from playwright.async_api import async_playwright

BLOGABET_USER = os.environ.get("BLOGABET_USER", "")
BLOGABET_PASS = os.environ.get("BLOGABET_PASS", "")
MIN_PICKS = int(os.environ.get("MIN_PICKS", "300"))
MIN_YIELD = float(os.environ.get("MIN_YIELD", "1.0"))
MAX_TIPSTERS = int(os.environ.get("MAX_TIPSTERS", "150"))

BASE_URL = "https://blogabet.com"
ROOT = Path(__file__).parent
DATA_DIR = ROOT / "data"
RAW_FILE = DATA_DIR / "tipsters_raw.json"

def now_utc():
    return datetime.now(timezone.utc)

def save_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2, default=str)

def parse_number(text):
    if not text: return 0.0
    text = text.strip().replace("\xa0","").replace(" ","").rstrip("%").lstrip("+")
    text = re.sub(r'[^\d.,\-]', '', text)
    if "," in text and "." in text:
        if text.index(",") < text.index("."): text = text.replace(",", "")
        else: text = text.replace(".", "").replace(",", ".")
    elif "," in text:
        parts = text.split(",")
        text = text.replace(",", "") if len(parts[-1]) == 3 else text.replace(",", ".")
    try: return float(text)
    except: return 0.0

def log(msg, level="INFO"):
    print(f"[{now_utc().strftime('%H:%M:%S')}] [{level}] {msg}")


class BlogabetScraper:
    def __init__(self):
        self.browser = None
        self.context = None
        self.page = None
        self.collected_urls = set()
        self.tipsters = []
        self.logged_in = False

    async def start(self):
        pw = await async_playwright().start()
        self.browser = await pw.chromium.launch(headless=True, args=["--no-sandbox","--disable-dev-shm-usage"])
        self.context = await self.browser.new_context(
            viewport={"width":1366,"height":768},
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
            locale="en-US",
        )
        await self.context.add_cookies([
            {"name":"age_verified","value":"1","domain":".blogabet.com","path":"/"},
            {"name":"age_gate","value":"passed","domain":".blogabet.com","path":"/"},
            {"name":"ageverify","value":"1","domain":".blogabet.com","path":"/"},
            {"name":"is_adult","value":"1","domain":".blogabet.com","path":"/"},
            {"name":"over18","value":"1","domain":".blogabet.com","path":"/"},
            {"name":"cookie_consent","value":"1","domain":".blogabet.com","path":"/"},
            {"name":"cookies_accepted","value":"1","domain":".blogabet.com","path":"/"},
            {"name":"cookiesDirective","value":"1","domain":".blogabet.com","path":"/"},
        ])
        self.page = await self.context.new_page()
        self.page.set_default_timeout(20000)
        log("Przeglądarka uruchomiona")

    async def close(self):
        if self.browser:
            await self.browser.close()
            log("Przeglądarka zamknięta")

    async def _safe_click(self, sel, timeout=3000):
        try:
            el = self.page.locator(sel).first
            if await el.is_visible(timeout=timeout):
                await el.click()
                await asyncio.sleep(0.5)
                return True
        except: pass
        return False

    async def _dismiss_popups(self):
        for sel in ["button:has-text('Accept')","button:has-text('Agree')","button:has-text('OK')","button:has-text('Yes')","a:has-text('Yes')","button:has-text('Enter')"]:
            await self._safe_click(sel, 1000)

    # ── LOGIN ────────────────────────────────────────────────────
    async def login(self):
        if not BLOGABET_USER or not BLOGABET_PASS:
            log("Brak credentials","WARN")
            return False

        log(f"Logowanie jako: {BLOGABET_USER[:3]}***")

        await self.page.goto(BASE_URL, wait_until="domcontentloaded")
        await asyncio.sleep(2)
        await self._dismiss_popups()
        log(f"  Strona główna: {self.page.url}")

        # Click LOG IN to open modal
        clicked = False
        for sel in ["a:has-text('LOG IN')","a:has-text('Log In')","a:has-text('Login')","button:has-text('LOG IN')","button:has-text('Log In')"]:
            if await self._safe_click(sel, 2000):
                clicked = True
                log(f"  Kliknięto: {sel}")
                break

        if not clicked:
            log("  Brak przycisku LOG IN","WARN")
            return False

        # WAIT for modal to load — check every 0.5s for up to 10s
        log("  Czekam na formularz logowania...")
        password_found = False
        for attempt in range(20):  # 20 x 0.5s = 10 seconds max
            await asyncio.sleep(0.5)
            inputs = await self.page.query_selector_all("input[type='password']")
            visible_pass = []
            for inp in inputs:
                vis = await self.page.evaluate("(el) => el.offsetParent !== null", inp)
                if vis:
                    visible_pass.append(inp)
            if visible_pass:
                password_found = True
                log(f"  Formularz załadowany po {(attempt+1)*0.5:.1f}s ({len(visible_pass)} password fields)")
                break

        if not password_found:
            log("  Formularz nie załadował się po 10s","WARN")
            # Debug: dump all inputs
            all_inp = await self.page.query_selector_all("input")
            log(f"  Inputy na stronie: {len(all_inp)}")
            for inp in all_inp[:10]:
                attrs = await self.page.evaluate("""(el) => ({
                    type:el.type||'?', name:el.name||'?', id:el.id||'?',
                    ph:el.placeholder||'?', vis:el.offsetParent!==null
                })""", inp)
                log(f"    [{attrs['type']}] name={attrs['name']} id={attrs['id']} ph={attrs['ph']} visible={attrs['vis']}")
            
            # Also dump page HTML around any form or modal
            modal_html = await self.page.evaluate("""() => {
                const modal = document.querySelector('.modal, [class*="modal"], [class*="login"], [class*="popup"], [role="dialog"]');
                return modal ? modal.outerHTML.substring(0, 1000) : 'NO MODAL FOUND';
            }""")
            log(f"  Modal HTML: {modal_html[:300]}")
            return False

        # Find and fill text/email field
        all_inputs = await self.page.query_selector_all("input")
        text_field = None
        pass_field = None

        for inp in all_inputs:
            vis = await self.page.evaluate("(el) => el.offsetParent !== null", inp)
            if not vis:
                continue
            inp_type = (await inp.get_attribute("type") or "").lower()
            if inp_type == "password" and not pass_field:
                pass_field = inp
            elif inp_type in ("text", "email", "") and not text_field:
                text_field = inp

        if text_field and pass_field:
            log("  Wypełniam formularz...")
            await text_field.fill(BLOGABET_USER)
            await asyncio.sleep(0.3)
            await pass_field.fill(BLOGABET_PASS)
            await asyncio.sleep(0.3)

            # Submit
            submitted = False
            for sel in [
                "button[type='submit']","input[type='submit']",
                "button:has-text('Log In')","button:has-text('LOG IN')",
                "button:has-text('Login')","button:has-text('Sign in')",
                "button:has-text('Submit')","form button",
                ".modal button","[class*='modal'] button",
                "[class*='login'] button",
            ]:
                if await self._safe_click(sel, 2000):
                    submitted = True
                    log(f"  Submit: {sel}")
                    break

            if not submitted:
                await pass_field.press("Enter")
                log("  Submit: Enter")

            await asyncio.sleep(5)

            # Verify login
            html = await self.page.content()
            indicators = ["logout","log out","sign out","my profile","my tipsters","my tracking","seller admin"]
            if any(x in html.lower() for x in indicators):
                self.logged_in = True
                log("  ✓ ZALOGOWANO!")
            else:
                log("  Login: brak wskaźników zalogowania")
                preview = await self.page.evaluate("() => document.body.innerText.substring(0, 200)")
                log(f"  Page: {preview[:150]}...")
        else:
            log(f"  Nie znaleziono pól (text={text_field is not None}, pass={pass_field is not None})","WARN")

        if not self.logged_in:
            log("  ✗ Login nieudany — tryb publiczny","WARN")
        return self.logged_in

    # ── DISCOVER ─────────────────────────────────────────────────
    async def discover_tipsters(self):
        log("Odkrywanie typerów...")

        for url in [f"{BASE_URL}/tipsters", f"{BASE_URL}/feed", f"{BASE_URL}/tips"]:
            if len(self.collected_urls) >= MAX_TIPSTERS:
                break

            log(f"  → {url}")
            await self.page.goto(url, wait_until="domcontentloaded")
            await asyncio.sleep(3)
            await self._dismiss_popups()

            final = self.page.url
            log(f"  URL: {final}")

            preview = await self.page.evaluate("() => document.body.innerText.substring(0, 300)")
            log(f"  Preview: {preview[:200]}...")

            # Check if login required
            if "please log in" in preview.lower():
                log("  ⚠ Wymaga logowania — pominięto","WARN")
                continue

            await self._extract_links()

            # Scroll + load more
            for _ in range(5):
                await self.page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                await asyncio.sleep(2)
                for sel in [".load-more","button:has-text('Load more')","a:has-text('Next')","a:has-text('»')"]:
                    await self._safe_click(sel, 1500)
                await self._extract_links()

        log(f"Odkryto {len(self.collected_urls)} typerów")

    async def _extract_links(self):
        links = await self.page.evaluate("""() =>
            Array.from(document.querySelectorAll('a[href]')).map(a => a.getAttribute('href') || '')
        """)

        excluded = {
            "","/","/login","/register","/help","/tips","/tipsters","/feed",
            "/betting-guide","/auto-betting","/announcement","/terms","/privacy",
            "/about","/contact","/cashback","/seller-admin","/forgot-password",
            "/bookmakers","/customer-support","/market","/pricing","/academy",
            "/search","/promotions","/blog","/faq"
        }
        bad_prefixes = ["/betting-guide/","/announcement/","/cashback/","/help/",
            "/page/","/static/","/assets/","/bookmakers/","/academy/","/blog/"]

        before = len(self.collected_urls)
        for href in links:
            if not href: continue
            if href.startswith("http"):
                if "blogabet.com" not in href: continue
                path = "/"+href.split("blogabet.com/")[-1] if "blogabet.com/" in href else ""
            elif href.startswith("/"):
                path = href
            else:
                continue

            path = path.split("?")[0].split("#")[0].rstrip("/")
            if not path or path in excluded: continue
            if any(path.startswith(p) for p in bad_prefixes): continue
            if any(x in path for x in [".png",".jpg",".css",".js",".svg"]): continue

            if re.match(r'^/[a-zA-Z0-9][a-zA-Z0-9_\-]{1,60}$', path):
                self.collected_urls.add(f"{BASE_URL}{path}")

        added = len(self.collected_urls) - before
        if added: log(f"    +{added} (total: {len(self.collected_urls)})")

    # ── SCRAPE PROFILE ───────────────────────────────────────────
    async def scrape_profile(self, url):
        try:
            await self.page.goto(url, wait_until="domcontentloaded")
            await asyncio.sleep(2)
            await self._dismiss_popups()

            if any(x in self.page.url for x in ["/bookmakers","/login"]):
                return None

            text = await self.page.evaluate("() => document.body.innerText")
            html = await self.page.content()

            if not any(kw in text for kw in ["PICKS","YIELD","PROFIT","picks","yield"]):
                return None

            t = {"url":url,"name":url.rstrip("/").split("/")[-1],"scraped_at":now_utc().isoformat()}

            m = re.search(r'PICKS\s*\n?\s*([\d,.\s]+)', text) or re.search(r'([\d,]+)\s*picks', text, re.I)
            t["picks_count"] = int(parse_number(m.group(1))) if m else 0

            m = re.search(r'YIELD\s*\n?\s*([+\-]?[\d,.]+)', text) or re.search(r'([+\-]?\d+\.?\d*)\s*%?\s*yield', text, re.I)
            t["yield_pct"] = parse_number(m.group(1)) if m else 0

            m = re.search(r'PROFIT\s*\n?\s*([+\-]?[\d,.]+)', text)
            t["profit_units"] = parse_number(m.group(1)) if m else 0

            m = re.search(r'Win\s*rate\s*\n?\s*([\d,.]+)\s*%', text, re.I)
            t["win_rate"] = parse_number(m.group(1)) if m else 0

            m = re.search(r'FOLLOWERS\s*\n?\s*([\d,]+)', text) or re.search(r'([\d,]+)\s*followers', text, re.I)
            t["followers"] = int(parse_number(m.group(1))) if m else 0

            m = re.search(r'Odds\s*avg\s*\n?\s*([\d,.]+)', text, re.I)
            t["odds_avg"] = parse_number(m.group(1)) if m else 0

            m = re.search(r'Stake\s*avg\s*\n?\s*([\d,.]+)', text, re.I)
            t["avg_stake"] = parse_number(m.group(1)) if m else 5.0

            t["verification"] = "free"
            if re.search(r'subscribe|paid\s*service', html, re.I): t["verification"] = "paid"
            if re.search(r'copytip|auto.?bet', html, re.I): t["verification"] = "paid_copytip"
            if await self.page.query_selector_all("[class*='verified'],[class*='checkmark']"):
                if t["verification"]=="free": t["verification"]="pro"

            m = re.search(r'[Rr]eset\w*\s*[:\(]?\s*(\d+)', text)
            t["resets"] = int(m.group(1)) if m else 0

            sports = re.findall(r'(Football|Basketball|Tennis|Ice Hockey|Esports?|Handball|Volleyball|Baseball|Boxing|MMA|Cricket|Darts|Futsal)', text, re.I)
            t["top_sports"] = list(dict.fromkeys(sports))[:5]

            bookies = re.findall(r'(Pinnacle|Bet365|SBOBet|Dafabet|188bet|AsianConnect|Betfair|Unibet|Bwin|Marathonbet|1xBet|22bet|Betway|Sportmarket)', text, re.I)
            t["top_bookmakers"] = list(dict.fromkeys(bookies))[:5]

            asian = {"Pinnacle","SBOBet","Dafabet","188bet","AsianConnect","Sportmarket"}
            t["bookmaker_profile"] = "asian_dominant" if any(b in asian for b in t["top_bookmakers"][:2]) else "mixed" if any(b in asian for b in t["top_bookmakers"]) else "soft_only"
            t["specialization"] = "mono_specialist" if len(t["top_sports"])<=1 else "focused_multi" if len(t["top_sports"])<=3 else "chaotic_multi"

            t.update({"recent_form_yield":t["yield_pct"]*0.8,"live_pct":10,"avg_hours_before_match":24,
                "months_active":12,"profitable_months_12":6,"pinnacle_yield":None,"soft_bookie_yield":None,
                "live_yield":None,"prematch_yield":None,"top_leagues":[],"analysis_quality":"short_desc",
                "sport_percentages":{},"bookie_percentages":{}})

            if t["picks_count"] < MIN_PICKS or t["yield_pct"] < MIN_YIELD:
                return None
            return t
        except Exception as e:
            log(f"    Error: {e}","ERROR")
            return None

    async def run(self):
        await self.start()
        try:
            await self.login()
            await self.discover_tipsters()

            if not self.collected_urls:
                log("Brak typerów — demo","WARN")
                self._fallback()
                save_json(RAW_FILE, self.tipsters)
                return

            log(f"\nScrapuję {len(self.collected_urls)} profili...")
            for i, url in enumerate(sorted(self.collected_urls)):
                if len(self.tipsters) >= MAX_TIPSTERS: break
                log(f"  [{i+1}/{len(self.collected_urls)}] {url.split('/')[-1]}")
                t = await self.scrape_profile(url)
                if t:
                    self.tipsters.append(t)
                    log(f"    ✓ yield={t['yield_pct']:+.1f}% picks={t['picks_count']}")
                else:
                    log(f"    ✗ skip")
                await asyncio.sleep(2)

            save_json(RAW_FILE, self.tipsters)
            log(f"\n✓ {len(self.tipsters)} typerów zapisano")
        finally:
            await self.close()

    def _fallback(self):
        ts = now_utc().isoformat()
        self.tipsters = [
            {"name":"DEMO_SharpEdge","url":f"{BASE_URL}/demo","yield_pct":8.4,"picks_count":1247,"verification":"paid","bookmaker_profile":"asian_dominant","recent_form_yield":6.2,"specialization":"mono_specialist","resets":0,"analysis_quality":"detailed_value","top_sports":["Football"],"top_leagues":["Eng. Premier"],"avg_stake":5.2,"odds_avg":1.95,"win_rate":54.2,"live_pct":10,"avg_hours_before_match":18,"followers":342,"months_active":24,"profitable_months_12":8,"pinnacle_yield":7.8,"soft_bookie_yield":9.1,"live_yield":3.2,"prematch_yield":9.0,"sport_percentages":{},"bookie_percentages":{},"top_bookmakers":["Pinnacle"],"scraped_at":ts,"is_demo":True},
            {"name":"DEMO_IceHockey_Pro","url":f"{BASE_URL}/demo2","yield_pct":7.1,"picks_count":2103,"verification":"paid_copytip","bookmaker_profile":"asian_dominant","recent_form_yield":5.8,"specialization":"mono_specialist","resets":0,"analysis_quality":"detailed_value","top_sports":["Ice Hockey"],"top_leagues":["NHL","KHL"],"avg_stake":3.0,"odds_avg":1.88,"win_rate":56.1,"live_pct":8,"avg_hours_before_match":8,"followers":512,"months_active":36,"profitable_months_12":9,"pinnacle_yield":6.8,"soft_bookie_yield":7.5,"live_yield":5.0,"prematch_yield":7.2,"sport_percentages":{},"bookie_percentages":{},"top_bookmakers":["Pinnacle","Bet365"],"scraped_at":ts,"is_demo":True},
            {"name":"DEMO_LiveKing_FRAUD","url":f"{BASE_URL}/demo3","yield_pct":22.4,"picks_count":456,"verification":"free","bookmaker_profile":"soft_only","recent_form_yield":18.0,"specialization":"chaotic_multi","resets":3,"analysis_quality":"none","top_sports":["Football","Tennis","Basketball"],"top_leagues":["Various"],"avg_stake":8.5,"odds_avg":2.80,"win_rate":42.0,"live_pct":65,"avg_hours_before_match":0,"followers":28,"months_active":6,"profitable_months_12":4,"pinnacle_yield":-3.5,"soft_bookie_yield":25.0,"live_yield":30.0,"prematch_yield":4.0,"sport_percentages":{},"bookie_percentages":{},"top_bookmakers":["Bet365","Unibet"],"scraped_at":ts,"is_demo":True},
        ]

if __name__ == "__main__":
    asyncio.run(BlogabetScraper().run())
