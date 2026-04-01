#!/usr/bin/env python3
"""
BLOGABET AUTO-SCRAPER v7 — debug login failure
"""

import asyncio, json, os, re
from datetime import datetime, timezone
from pathlib import Path
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

def now_utc(): return datetime.now(timezone.utc)
def save_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2, default=str)

def pn(text):
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
        self.browser = self.context = self.page = None
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
        if self.browser: await self.browser.close(); log("Przeglądarka zamknięta")

    async def _click(self, sel, timeout=3000):
        try:
            el = self.page.locator(sel).first
            if await el.is_visible(timeout=timeout): await el.click(); await asyncio.sleep(0.5); return True
        except: pass
        return False

    async def _dismiss(self):
        for s in ["button:has-text('Accept')","button:has-text('OK')","button:has-text('Yes')","a:has-text('Yes')"]:
            await self._click(s, 1000)

    # ── LOGIN ────────────────────────────────────────────────────
    async def login(self):
        if not BLOGABET_USER or not BLOGABET_PASS:
            log("Brak credentials","WARN"); return False

        log(f"Logowanie jako: {BLOGABET_USER[:5]}***")

        await self.page.goto(BASE_URL, wait_until="domcontentloaded")
        await asyncio.sleep(2)
        await self._dismiss()

        # Click LOG IN
        for sel in ["a:has-text('LOG IN')","a:has-text('Log In')","button:has-text('LOG IN')"]:
            if await self._click(sel, 2000): log(f"  Kliknięto: {sel}"); break

        # Wait for password field
        log("  Czekam na formularz...")
        pass_field = None
        for _ in range(20):
            await asyncio.sleep(0.5)
            for inp in await self.page.query_selector_all("input[type='password']"):
                if await self.page.evaluate("(el) => el.offsetParent !== null", inp):
                    pass_field = inp; break
            if pass_field: break

        if not pass_field:
            log("  Brak pola password po 10s","WARN"); return False

        # Dump ALL visible inputs in detail
        all_inputs = await self.page.query_selector_all("input")
        visible_inputs = []
        for inp in all_inputs:
            attrs = await self.page.evaluate("""(el) => ({
                type: el.type||'', name: el.name||'', id: el.id||'',
                placeholder: el.placeholder||'', className: el.className||'',
                visible: el.offsetParent !== null,
                rect: el.getBoundingClientRect().toJSON()
            })""", inp)
            if attrs["visible"]:
                visible_inputs.append({"el": inp, **attrs})
                log(f"  VISIBLE INPUT: type={attrs['type']} name={attrs['name']} id={attrs['id']} "
                    f"placeholder='{attrs['placeholder']}' class={attrs['className'][:40]} "
                    f"pos=({int(attrs['rect']['x'])},{int(attrs['rect']['y'])})")

        # Find the EMAIL field specifically — look for type=email, or placeholder with email/user
        email_field = None
        for vi in visible_inputs:
            t = vi["type"].lower()
            ph = vi["placeholder"].lower()
            nm = vi["name"].lower()
            cls = vi["className"].lower()

            if t == "password":
                continue  # skip password fields

            # Priority 1: type=email
            if t == "email":
                email_field = vi["el"]; log(f"  → Email field found (type=email)"); break
            # Priority 2: placeholder/name contains email/user/login
            if any(kw in ph for kw in ["email","mail","user","login","nazwa","e-mail"]):
                email_field = vi["el"]; log(f"  → Email field found (placeholder='{vi['placeholder']}')"); break
            if any(kw in nm for kw in ["email","mail","user","login"]):
                email_field = vi["el"]; log(f"  → Email field found (name={vi['name']})"); break

        # Fallback: first visible text input that's NOT search
        if not email_field:
            for vi in visible_inputs:
                t = vi["type"].lower()
                ph = vi["placeholder"].lower()
                if t in ("text","") and "search" not in ph and "szukaj" not in ph:
                    email_field = vi["el"]; log(f"  → Email field fallback (type={vi['type']})"); break

        if not email_field:
            log("  Nie znaleziono pola email/username","WARN"); return False

        # Fill form
        log("  Wypełniam formularz...")
        await email_field.click()
        await asyncio.sleep(0.2)
        await email_field.fill("")  # clear first
        await email_field.type(BLOGABET_USER, delay=50)  # type char by char
        await asyncio.sleep(0.3)

        await pass_field.click()
        await asyncio.sleep(0.2)
        await pass_field.fill("")
        await pass_field.type(BLOGABET_PASS, delay=50)
        await asyncio.sleep(0.3)

        # Verify what was typed
        email_val = await self.page.evaluate("(el) => el.value", email_field)
        pass_val = await self.page.evaluate("(el) => el.value.length", pass_field)
        log(f"  Wpisano: email='{email_val[:5]}***' pass_len={pass_val}")

        # Submit
        for sel in ["button[type='submit']","button:has-text('Log In')","button:has-text('LOG IN')","button:has-text('Login')","form button"]:
            if await self._click(sel, 2000): log(f"  Submit: {sel}"); break
        else:
            await pass_field.press("Enter"); log("  Submit: Enter")

        # Wait for response
        await asyncio.sleep(5)

        # ── DEBUG: What does the page show now? ──
        page_text = await self.page.evaluate("() => document.body.innerText.substring(0, 500)")
        log(f"  Page po submit: {page_text[:300]}...")

        current_url = self.page.url
        log(f"  URL po submit: {current_url}")

        # Check for error messages
        errors = await self.page.evaluate("""() => {
            const errs = [];
            document.querySelectorAll('.error, .alert, .warning, .message, [class*="error"], [class*="alert"], [class*="invalid"], [class*="wrong"]').forEach(el => {
                if (el.offsetParent !== null && el.textContent.trim()) {
                    errs.push(el.textContent.trim().substring(0, 100));
                }
            });
            return errs;
        }""")
        if errors:
            log(f"  ERROR MESSAGES: {errors}")

        # Check login success
        html = await self.page.content()
        if any(x in html.lower() for x in ["logout","log out","sign out","my profile","my tipsters","seller admin"]):
            self.logged_in = True
            log("  ✓ ZALOGOWANO!")
        else:
            log("  ✗ Login nieudany","WARN")
            # Extra debug: check if "LOG IN" button is still visible (means not logged in)
            still_login = await self.page.evaluate("""() => {
                const links = document.querySelectorAll('a, button');
                for (const l of links) {
                    if (l.textContent.trim().toUpperCase() === 'LOG IN' && l.offsetParent !== null) return true;
                }
                return false;
            }""")
            log(f"  'LOG IN' nadal widoczny: {still_login}")

        return self.logged_in

    # ── DISCOVER ─────────────────────────────────────────────────
    async def discover_tipsters(self):
        log("Odkrywanie typerów...")

        if self.logged_in:
            # Navigate by clicking
            for target in ["All Tipsters","Tipsters","Feed","Tips"]:
                if len(self.collected_urls) >= MAX_TIPSTERS: break
                log(f"  Klikam: '{target}'")
                for sel in [f"a:has-text('{target}')",f"button:has-text('{target}')"]:
                    try:
                        el = self.page.locator(sel).first
                        if await el.is_visible(timeout=2000):
                            await el.click(); await asyncio.sleep(3)
                            log(f"  → {self.page.url}")
                            preview = await self.page.evaluate("() => document.body.innerText.substring(0, 200)")
                            if "please log in" in preview.lower():
                                log("  ⚠ Wymaga logowania","WARN"); continue
                            await self._extract_links()
                            for _ in range(3):
                                await self.page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                                await asyncio.sleep(2)
                                await self._extract_links()
                            break
                    except: pass
        else:
            for url in [f"{BASE_URL}/tipsters", f"{BASE_URL}/feed"]:
                await self.page.goto(url, wait_until="domcontentloaded")
                await asyncio.sleep(3)
                preview = await self.page.evaluate("() => document.body.innerText.substring(0, 200)")
                if "please log in" not in preview.lower():
                    await self._extract_links()

        log(f"Odkryto {len(self.collected_urls)} typerów")

    async def _extract_links(self):
        links = await self.page.evaluate("() => Array.from(document.querySelectorAll('a[href]')).map(a => a.getAttribute('href')||'')")
        excluded = {"","/","/login","/register","/help","/tips","/tipsters","/feed","/betting-guide","/auto-betting","/announcement","/terms","/privacy","/about","/contact","/cashback","/seller-admin","/forgot-password","/bookmakers","/customer-support","/market","/pricing","/academy","/search","/promotions","/blog","/faq"}
        bad = ["/betting-guide/","/announcement/","/cashback/","/help/","/page/","/static/","/assets/","/bookmakers/","/academy/","/blog/","/auto-betting/"]
        before = len(self.collected_urls)
        for href in links:
            if not href: continue
            if href.startswith("http"):
                if "blogabet.com" not in href: continue
                path = "/"+href.split("blogabet.com/")[-1] if "blogabet.com/" in href else ""
            elif href.startswith("/"): path = href
            else: continue
            path = path.split("?")[0].split("#")[0].rstrip("/")
            if not path or path in excluded: continue
            if any(path.startswith(p) for p in bad): continue
            if any(x in path for x in [".png",".jpg",".css",".js"]): continue
            if re.match(r'^/[a-zA-Z0-9][a-zA-Z0-9_\-]{1,60}$', path):
                self.collected_urls.add(f"{BASE_URL}{path}")
        added = len(self.collected_urls) - before
        if added: log(f"    +{added} (total: {len(self.collected_urls)})")

    # ── SCRAPE ───────────────────────────────────────────────────
    async def scrape_profile(self, url):
        try:
            await self.page.goto(url, wait_until="domcontentloaded")
            await asyncio.sleep(2); await self._dismiss()
            if any(x in self.page.url for x in ["/bookmakers","/login"]): return None
            text = await self.page.evaluate("() => document.body.innerText")
            html = await self.page.content()
            if not any(kw in text for kw in ["PICKS","YIELD","PROFIT"]): return None
            t = {"url":url,"name":url.rstrip("/").split("/")[-1],"scraped_at":now_utc().isoformat()}
            m = re.search(r'PICKS\s*\n?\s*([\d,.\s]+)', text)
            t["picks_count"] = int(pn(m.group(1))) if m else 0
            m = re.search(r'YIELD\s*\n?\s*([+\-]?[\d,.]+)', text)
            t["yield_pct"] = pn(m.group(1)) if m else 0
            m = re.search(r'PROFIT\s*\n?\s*([+\-]?[\d,.]+)', text)
            t["profit_units"] = pn(m.group(1)) if m else 0
            m = re.search(r'Win\s*rate\s*\n?\s*([\d,.]+)\s*%', text, re.I)
            t["win_rate"] = pn(m.group(1)) if m else 0
            m = re.search(r'FOLLOWERS\s*\n?\s*([\d,]+)', text)
            t["followers"] = int(pn(m.group(1))) if m else 0
            m = re.search(r'Odds\s*avg\s*\n?\s*([\d,.]+)', text, re.I)
            t["odds_avg"] = pn(m.group(1)) if m else 0
            m = re.search(r'Stake\s*avg\s*\n?\s*([\d,.]+)', text, re.I)
            t["avg_stake"] = pn(m.group(1)) if m else 5.0
            t["verification"] = "free"
            if re.search(r'subscribe|paid\s*service', html, re.I): t["verification"]="paid"
            if re.search(r'copytip|auto.?bet', html, re.I): t["verification"]="paid_copytip"
            if await self.page.query_selector_all("[class*='verified'],[class*='checkmark']"):
                if t["verification"]=="free": t["verification"]="pro"
            m = re.search(r'[Rr]eset\w*\s*[:\(]?\s*(\d+)', text)
            t["resets"] = int(m.group(1)) if m else 0
            sports = re.findall(r'(Football|Basketball|Tennis|Ice Hockey|Esports?|Handball|Volleyball|Baseball|Boxing|MMA|Cricket|Darts|Futsal)', text, re.I)
            t["top_sports"] = list(dict.fromkeys(sports))[:5]
            bookies = re.findall(r'(Pinnacle|Bet365|SBOBet|Dafabet|188bet|AsianConnect|Betfair|Unibet|Bwin|1xBet|22bet|Betway|Sportmarket)', text, re.I)
            t["top_bookmakers"] = list(dict.fromkeys(bookies))[:5]
            asian = {"Pinnacle","SBOBet","Dafabet","188bet","AsianConnect","Sportmarket"}
            t["bookmaker_profile"] = "asian_dominant" if any(b in asian for b in t["top_bookmakers"][:2]) else "mixed" if any(b in asian for b in t["top_bookmakers"]) else "soft_only"
            t["specialization"] = "mono_specialist" if len(t["top_sports"])<=1 else "focused_multi" if len(t["top_sports"])<=3 else "chaotic_multi"
            t.update({"recent_form_yield":t["yield_pct"]*0.8,"live_pct":10,"avg_hours_before_match":24,"months_active":12,"profitable_months_12":6,"pinnacle_yield":None,"soft_bookie_yield":None,"live_yield":None,"prematch_yield":None,"top_leagues":[],"analysis_quality":"short_desc","sport_percentages":{},"bookie_percentages":{}})
            if t["picks_count"] < MIN_PICKS or t["yield_pct"] < MIN_YIELD: return None
            return t
        except Exception as e: log(f"    Error: {e}","ERROR"); return None

    async def run(self):
        await self.start()
        try:
            await self.login()
            await self.discover_tipsters()
            if not self.collected_urls:
                log("Brak typerów — demo","WARN"); self._fallback(); save_json(RAW_FILE, self.tipsters); return
            log(f"\nScrapuję {len(self.collected_urls)} profili...")
            for i, url in enumerate(sorted(self.collected_urls)):
                if len(self.tipsters) >= MAX_TIPSTERS: break
                log(f"  [{i+1}/{len(self.collected_urls)}] {url.split('/')[-1]}")
                t = await self.scrape_profile(url)
                if t: self.tipsters.append(t); log(f"    ✓ yield={t['yield_pct']:+.1f}% picks={t['picks_count']}")
                else: log(f"    ✗ skip")
                await asyncio.sleep(2)
            save_json(RAW_FILE, self.tipsters); log(f"\n✓ {len(self.tipsters)} typerów")
        finally: await self.close()

    def _fallback(self):
        ts = now_utc().isoformat()
        self.tipsters = [
            {"name":"DEMO_SharpEdge","url":f"{BASE_URL}/demo","yield_pct":8.4,"picks_count":1247,"verification":"paid","bookmaker_profile":"asian_dominant","recent_form_yield":6.2,"specialization":"mono_specialist","resets":0,"analysis_quality":"detailed_value","top_sports":["Football"],"top_leagues":["Eng. Premier"],"avg_stake":5.2,"odds_avg":1.95,"win_rate":54.2,"live_pct":10,"avg_hours_before_match":18,"followers":342,"months_active":24,"profitable_months_12":8,"pinnacle_yield":7.8,"soft_bookie_yield":9.1,"live_yield":3.2,"prematch_yield":9.0,"sport_percentages":{},"bookie_percentages":{},"top_bookmakers":["Pinnacle"],"scraped_at":ts,"is_demo":True},
            {"name":"DEMO_IceHockey_Pro","url":f"{BASE_URL}/demo2","yield_pct":7.1,"picks_count":2103,"verification":"paid_copytip","bookmaker_profile":"asian_dominant","recent_form_yield":5.8,"specialization":"mono_specialist","resets":0,"analysis_quality":"detailed_value","top_sports":["Ice Hockey"],"top_leagues":["NHL","KHL"],"avg_stake":3.0,"odds_avg":1.88,"win_rate":56.1,"live_pct":8,"avg_hours_before_match":8,"followers":512,"months_active":36,"profitable_months_12":9,"pinnacle_yield":6.8,"soft_bookie_yield":7.5,"live_yield":5.0,"prematch_yield":7.2,"sport_percentages":{},"bookie_percentages":{},"top_bookmakers":["Pinnacle","Bet365"],"scraped_at":ts,"is_demo":True},
            {"name":"DEMO_LiveKing_FRAUD","url":f"{BASE_URL}/demo3","yield_pct":22.4,"picks_count":456,"verification":"free","bookmaker_profile":"soft_only","recent_form_yield":18.0,"specialization":"chaotic_multi","resets":3,"analysis_quality":"none","top_sports":["Football","Tennis","Basketball"],"top_leagues":["Various"],"avg_stake":8.5,"odds_avg":2.80,"win_rate":42.0,"live_pct":65,"avg_hours_before_match":0,"followers":28,"months_active":6,"profitable_months_12":4,"pinnacle_yield":-3.5,"soft_bookie_yield":25.0,"live_yield":30.0,"prematch_yield":4.0,"sport_percentages":{},"bookie_percentages":{},"top_bookmakers":["Bet365","Unibet"],"scraped_at":ts,"is_demo":True},
        ]

if __name__ == "__main__":
    asyncio.run(BlogabetScraper().run())
