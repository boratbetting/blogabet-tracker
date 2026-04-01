#!/usr/bin/env python3
"""
BLOGABET AUTO-SCRAPER v4 — login via modal, age gate via cookies
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

def parse_number(text: str) -> float:
    if not text:
        return 0.0
    text = text.strip().replace("\xa0","").replace(" ","").rstrip("%").lstrip("+")
    text = re.sub(r'[^\d.,\-]', '', text)
    if "," in text and "." in text:
        if text.index(",") < text.index("."):
            text = text.replace(",", "")
        else:
            text = text.replace(".", "").replace(",", ".")
    elif "," in text:
        parts = text.split(",")
        text = text.replace(",", "") if len(parts[-1]) == 3 else text.replace(",", ".")
    try:
        return float(text)
    except ValueError:
        return 0.0

def log(msg, level="INFO"):
    print(f"[{now_utc().strftime('%H:%M:%S')}] [{level}] {msg}")


class BlogabetScraper:
    def __init__(self):
        self.browser = None
        self.context = None
        self.page = None
        self.collected_urls: set = set()
        self.tipsters: List[Dict] = []
        self.logged_in = False

    async def start(self):
        pw = await async_playwright().start()
        self.browser = await pw.chromium.launch(
            headless=True,
            args=["--no-sandbox", "--disable-dev-shm-usage"]
        )
        self.context = await self.browser.new_context(
            viewport={"width": 1366, "height": 768},
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
            locale="en-US",
        )
        # Age verification + cookie consent via cookies
        await self.context.add_cookies([
            {"name": "age_verified", "value": "1", "domain": ".blogabet.com", "path": "/"},
            {"name": "age_gate", "value": "passed", "domain": ".blogabet.com", "path": "/"},
            {"name": "ageverify", "value": "1", "domain": ".blogabet.com", "path": "/"},
            {"name": "is_adult", "value": "1", "domain": ".blogabet.com", "path": "/"},
            {"name": "over18", "value": "1", "domain": ".blogabet.com", "path": "/"},
            {"name": "cookie_consent", "value": "1", "domain": ".blogabet.com", "path": "/"},
            {"name": "cookies_accepted", "value": "1", "domain": ".blogabet.com", "path": "/"},
            {"name": "cookiesDirective", "value": "1", "domain": ".blogabet.com", "path": "/"},
        ])
        self.page = await self.context.new_page()
        self.page.set_default_timeout(20000)
        log("Przeglądarka uruchomiona")

    async def close(self):
        if self.browser:
            await self.browser.close()
            log("Przeglądarka zamknięta")

    async def _safe_click(self, selector, timeout=3000):
        try:
            el = self.page.locator(selector).first
            if await el.is_visible(timeout=timeout):
                await el.click()
                await asyncio.sleep(0.5)
                return True
        except:
            pass
        return False

    async def _dismiss_popups(self):
        for sel in [
            "button:has-text('Accept')", "button:has-text('Agree')",
            "button:has-text('OK')", "button:has-text('Got it')",
            "button:has-text('Yes')", "a:has-text('Yes')",
            "button:has-text('Enter')", "button:has-text('Confirm')",
            ".modal-close", "button.close", "[aria-label='Close']",
        ]:
            await self._safe_click(sel, timeout=1000)
        # JS fallback
        try:
            await self.page.evaluate("""() => {
                document.querySelectorAll('.modal, .overlay, .popup, [class*="cookie"]').forEach(el => el.remove());
                document.body.style.overflow = 'auto';
            }""")
        except:
            pass

    # ── LOGIN VIA MODAL ──────────────────────────────────────────
    async def login(self) -> bool:
        if not BLOGABET_USER or not BLOGABET_PASS:
            log("Brak credentials — tryb publiczny", "WARN")
            return False

        log(f"Logowanie jako: {BLOGABET_USER[:3]}***")

        # Go to main page
        await self.page.goto(BASE_URL, wait_until="domcontentloaded")
        await asyncio.sleep(2)
        await self._dismiss_popups()

        log(f"  Main page loaded: {self.page.url}")

        # Click "LOG IN" button/link to open modal
        login_clicked = False
        for sel in [
            "a:has-text('LOG IN')", "a:has-text('Log In')", "a:has-text('Log in')",
            "button:has-text('LOG IN')", "button:has-text('Log In')",
            "a:has-text('Login')", "button:has-text('Login')",
            "a:has-text('Sign In')", "button:has-text('Sign In')",
            ".login-link", ".login-btn", "#login-link", "#login-btn",
            "a[href*='login']", "a[href*='signin']",
            "nav a:has-text('LOG IN')",
        ]:
            if await self._safe_click(sel, timeout=2000):
                login_clicked = True
                log(f"  Kliknięto: {sel}")
                break

        if not login_clicked:
            log("  Nie znaleziono przycisku LOG IN", "WARN")
            # Dump navigation links for debugging
            nav_links = await self.page.evaluate("""() => {
                return Array.from(document.querySelectorAll('a, button')).slice(0, 30).map(el => ({
                    tag: el.tagName, text: el.textContent.trim().substring(0, 40),
                    href: el.getAttribute('href') || '', class: el.className.substring(0, 40)
                }));
            }""")
            for nl in nav_links[:15]:
                log(f"    {nl['tag']} text='{nl['text']}' href={nl['href']} class={nl['class']}")
            return False

        await asyncio.sleep(2)

        # Now find the login form (should be in a modal/popup)
        all_inputs = await self.page.query_selector_all("input")
        log(f"  Inputy po kliknięciu LOG IN: {len(all_inputs)}")

        for inp in all_inputs[:15]:
            attrs = await self.page.evaluate("""(el) => ({
                type: el.type || '?', name: el.name || '?', id: el.id || '?',
                placeholder: el.placeholder || '?', visible: el.offsetParent !== null
            })""", inp)
            if attrs['visible']:
                log(f"    [VISIBLE] type={attrs['type']} name={attrs['name']} id={attrs['id']} ph={attrs['placeholder']}")

        # Fill login form
        login_success = False
        try:
            # Find visible password field
            pass_inputs = []
            for inp in all_inputs:
                inp_type = await inp.get_attribute("type") or ""
                is_visible = await self.page.evaluate("(el) => el.offsetParent !== null", inp)
                if inp_type.lower() == "password" and is_visible:
                    pass_inputs.append(inp)

            # Find visible text/email field
            text_inputs = []
            for inp in all_inputs:
                inp_type = await inp.get_attribute("type") or ""
                is_visible = await self.page.evaluate("(el) => el.offsetParent !== null", inp)
                if inp_type.lower() in ("text", "email", "") and is_visible:
                    text_inputs.append(inp)

            log(f"  Visible: {len(text_inputs)} text, {len(pass_inputs)} password")

            if text_inputs and pass_inputs:
                await text_inputs[0].fill(BLOGABET_USER)
                await asyncio.sleep(0.3)
                await pass_inputs[0].fill(BLOGABET_PASS)
                await asyncio.sleep(0.3)

                # Try submit button in modal
                submitted = False
                for sel in [
                    "button[type='submit']", "input[type='submit']",
                    ".modal button:has-text('Log')", ".modal button:has-text('Sign')",
                    "button:has-text('Log In')", "button:has-text('LOG IN')",
                    "button:has-text('Login')", "button:has-text('Submit')",
                    "button:has-text('Sign in')",
                    "form button", ".login-form button",
                ]:
                    if await self._safe_click(sel, timeout=2000):
                        submitted = True
                        log(f"  Submit: {sel}")
                        break

                if not submitted:
                    await pass_inputs[0].press("Enter")
                    log("  Submit: Enter key")

                await asyncio.sleep(5)

                # Check login
                html = await self.page.content()
                has_logout = any(x in html.lower() for x in [
                    "logout", "log out", "sign out", "my profile",
                    "my tipsters", "my tracking", "seller admin"
                ])
                if has_logout:
                    login_success = True
                    log("  ✓ Login pomyślny!")
                else:
                    log("  Login: brak wskaźników zalogowania")

                    # Debug: check what the page shows now
                    preview = await self.page.evaluate("() => document.body.innerText.substring(0, 300)")
                    log(f"  Page preview: {preview[:150]}...")
        except Exception as e:
            log(f"  Login error: {e}", "WARN")

        self.logged_in = login_success
        if not login_success:
            log("  ✗ Login nie powiódł się — kontynuuję publicznie", "WARN")
        return login_success

    # ── DISCOVER TIPSTERS ────────────────────────────────────────
    async def discover_tipsters(self):
        log("Odkrywanie typerów...")

        for url in [f"{BASE_URL}/tipsters", f"{BASE_URL}/feed", f"{BASE_URL}/tips"]:
            if len(self.collected_urls) >= MAX_TIPSTERS:
                break
            log(f"  Strona: {url}")

            await self.page.goto(url, wait_until="domcontentloaded")
            await asyncio.sleep(3)
            await self._dismiss_popups()

            final_url = self.page.url
            log(f"  Final URL: {final_url}")

            # Page text preview
            preview = await self.page.evaluate("() => document.body.innerText.substring(0, 500)")
            log(f"  Preview: {preview[:200]}...")

            # Extract links
            await self._extract_links()

            # Scroll and try to load more
            for scroll in range(5):
                await self.page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                await asyncio.sleep(2)
                for sel in [".load-more", "button:has-text('Load more')", "a:has-text('Load more')", ".pagination .next", "a:has-text('»')", "a:has-text('Next')"]:
                    await self._safe_click(sel, timeout=1500)
                await self._extract_links()

        log(f"Odkryto {len(self.collected_urls)} typerów")

    async def _extract_links(self):
        links = await self.page.evaluate("""() => {
            return Array.from(document.querySelectorAll('a[href]')).map(a => ({
                href: a.getAttribute('href') || '',
                text: a.textContent.trim().substring(0, 50),
                parent: (a.closest('[class]') || {}).className || ''
            }));
        }""")

        excluded = {
            "", "/", "/login", "/register", "/help", "/tips", "/tipsters",
            "/feed", "/betting-guide", "/auto-betting", "/announcement",
            "/terms", "/privacy", "/about", "/contact", "/cashback",
            "/seller-admin", "/forgot-password", "/bookmakers",
            "/customer-support", "/market", "/pricing", "/academy",
            "/search", "/promotions", "/blog"
        }

        excluded_prefixes = [
            "/betting-guide/", "/announcement/", "/cashback/", "/auto-betting/",
            "/help/", "/page/", "/category/", "/static/", "/assets/",
            "/hacks/", "/tutorials/", "/bookmakers/", "/academy/",
            "/betting-", "/blog/", "/promotions/",
        ]

        before = len(self.collected_urls)

        for link in links:
            href = link["href"]
            if not href:
                continue

            # Normalize to path
            if href.startswith("http"):
                if "blogabet.com" not in href:
                    continue
                path = "/" + href.split("blogabet.com/")[-1] if "blogabet.com/" in href else ""
            elif href.startswith("/"):
                path = href
            else:
                continue

            path = path.split("?")[0].split("#")[0].rstrip("/")

            if not path or path in excluded:
                continue
            if any(path.startswith(p) for p in excluded_prefixes):
                continue
            if any(x in path for x in [".png", ".jpg", ".css", ".js", ".svg", ".ico"]):
                continue

            # Valid tipster profile: /username (single segment, alphanumeric)
            if re.match(r'^/[a-zA-Z0-9][a-zA-Z0-9_\-\s]{1,60}$', path):
                # Extra check: exclude common non-tipster words
                name = path[1:].lower()
                skip_names = [
                    "login", "register", "help", "tips", "tipsters", "feed",
                    "terms", "privacy", "about", "contact", "bookmakers",
                    "market", "pricing", "academy", "search", "promotions",
                    "cashback", "blog", "customer-support", "seller-admin",
                    "forgot-password", "announcement", "auto-betting",
                    "betting-guide", "faq", "affiliates", "partners",
                    "mobile", "app", "api", "sitemap", "robots",
                ]
                if name not in skip_names:
                    self.collected_urls.add(f"{BASE_URL}{path}")

        added = len(self.collected_urls) - before
        if added > 0:
            log(f"    +{added} nowych (total: {len(self.collected_urls)})")

    # ── SCRAPE PROFILE ───────────────────────────────────────────
    async def scrape_tipster_profile(self, url: str) -> Optional[Dict]:
        try:
            await self.page.goto(url, wait_until="domcontentloaded")
            await asyncio.sleep(2)
            await self._dismiss_popups()

            final_url = self.page.url
            if any(x in final_url for x in ["/bookmakers", "/login", "/register"]):
                return None

            text = await self.page.evaluate("() => document.body.innerText")
            html = await self.page.content()

            t = {
                "url": url,
                "name": url.rstrip("/").split("/")[-1],
                "scraped_at": now_utc().isoformat(),
            }

            # Check if this is actually a tipster profile (has picks/yield/profit stats)
            has_stats = any(kw in text for kw in ["PICKS", "YIELD", "PROFIT", "picks", "yield"])
            if not has_stats:
                return None

            # PICKS
            m = re.search(r'PICKS\s*\n?\s*([\d,.\s]+)', text) or re.search(r'([\d,]+)\s*picks', text, re.IGNORECASE)
            t["picks_count"] = int(parse_number(m.group(1))) if m else 0

            # YIELD
            m = re.search(r'YIELD\s*\n?\s*([+\-]?[\d,.]+)', text) or re.search(r'([+\-]?\d+\.?\d*)\s*%?\s*yield', text, re.IGNORECASE)
            t["yield_pct"] = parse_number(m.group(1)) if m else 0

            # PROFIT
            m = re.search(r'PROFIT\s*\n?\s*([+\-]?[\d,.]+)', text)
            t["profit_units"] = parse_number(m.group(1)) if m else 0

            # WIN RATE
            m = re.search(r'Win\s*rate\s*\n?\s*([\d,.]+)\s*%', text, re.IGNORECASE)
            t["win_rate"] = parse_number(m.group(1)) if m else 0

            # FOLLOWERS
            m = re.search(r'FOLLOWERS\s*\n?\s*([\d,]+)', text) or re.search(r'([\d,]+)\s*followers', text, re.IGNORECASE)
            t["followers"] = int(parse_number(m.group(1))) if m else 0

            # ODDS AVG
            m = re.search(r'Odds\s*avg\s*\n?\s*([\d,.]+)', text, re.IGNORECASE)
            t["odds_avg"] = parse_number(m.group(1)) if m else 0

            # STAKE AVG
            m = re.search(r'Stake\s*avg\s*\n?\s*([\d,.]+)', text, re.IGNORECASE)
            t["avg_stake"] = parse_number(m.group(1)) if m else 5.0

            # VERIFICATION
            t["verification"] = "free"
            if re.search(r'subscribe|paid\s*service|buy\s*now', html, re.IGNORECASE):
                t["verification"] = "paid"
            if re.search(r'copytip|auto.?bet', html, re.IGNORECASE):
                t["verification"] = "paid_copytip"
            if await self.page.query_selector_all("[class*='verified'], [class*='checkmark'], [class*='pro-badge']"):
                if t["verification"] == "free":
                    t["verification"] = "pro"

            # RESETS
            m = re.search(r'[Rr]eset[s]?\s*[:\(]?\s*(\d+)', text)
            t["resets"] = int(m.group(1)) if m else 0

            # SPORTS
            sports = re.findall(r'(Football|Basketball|Tennis|Ice Hockey|Esports?|Handball|Volleyball|Baseball|Boxing|MMA|Cricket|Darts|Futsal)', text, re.IGNORECASE)
            t["top_sports"] = list(dict.fromkeys([s.strip() for s in sports]))[:5]

            # BOOKMAKERS
            bookies = re.findall(r'(Pinnacle|Bet365|SBOBet|Dafabet|188bet|AsianConnect|Betfair|Unibet|Bwin|Marathonbet|1xBet|22bet|Betway|Sportmarket)', text, re.IGNORECASE)
            t["top_bookmakers"] = list(dict.fromkeys([b.strip() for b in bookies]))[:5]

            # CLASSIFY
            asian = {"Pinnacle","SBOBet","Dafabet","188bet","AsianConnect","Sportmarket"}
            t["bookmaker_profile"] = "asian_dominant" if any(b in asian for b in t["top_bookmakers"][:2]) else "mixed" if any(b in asian for b in t["top_bookmakers"]) else "soft_only"
            t["specialization"] = "mono_specialist" if len(t["top_sports"]) <= 1 else "focused_multi" if len(t["top_sports"]) <= 3 else "chaotic_multi"

            # DEFAULTS
            t.setdefault("recent_form_yield", t["yield_pct"] * 0.8)
            t.setdefault("live_pct", 10)
            t.setdefault("avg_hours_before_match", 24)
            t.setdefault("months_active", 12)
            t.setdefault("profitable_months_12", 6)
            for k in ["pinnacle_yield","soft_bookie_yield","live_yield","prematch_yield"]:
                t.setdefault(k, None)
            t.setdefault("top_leagues", [])
            t.setdefault("analysis_quality", "short_desc")
            t.setdefault("sport_percentages", {})
            t.setdefault("bookie_percentages", {})

            # FILTER
            if t["picks_count"] < MIN_PICKS or t["yield_pct"] < MIN_YIELD:
                return None

            return t
        except Exception as e:
            log(f"    Error: {e}", "ERROR")
            return None

    # ── MAIN ─────────────────────────────────────────────────────
    async def run(self):
        await self.start()
        try:
            await self.login()
            await self.discover_tipsters()

            if not self.collected_urls:
                log("Brak typerów — dane demo", "WARN")
                self._generate_fallback()
                save_json(RAW_FILE, self.tipsters)
                return

            log(f"\nScrapuję {len(self.collected_urls)} profili...")
            for i, url in enumerate(sorted(self.collected_urls)):
                if len(self.tipsters) >= MAX_TIPSTERS:
                    break
                name = url.split('/')[-1]
                log(f"  [{i+1}/{len(self.collected_urls)}] {name}")
                t = await self.scrape_tipster_profile(url)
                if t:
                    self.tipsters.append(t)
                    log(f"    ✓ yield={t['yield_pct']:+.1f}% picks={t['picks_count']} sport={t['top_sports']}")
                else:
                    log(f"    ✗ skip")
                await asyncio.sleep(2)

            save_json(RAW_FILE, self.tipsters)
            log(f"\n✓ Zapisano {len(self.tipsters)} typerów")
        finally:
            await self.close()

    def _generate_fallback(self):
        log("Generuję dane demo...")
        ts = now_utc().isoformat()
        self.tipsters = [
            {"name":"DEMO_SharpEdge","url":f"{BASE_URL}/demo","yield_pct":8.4,"picks_count":1247,"verification":"paid","bookmaker_profile":"asian_dominant","recent_form_yield":6.2,"specialization":"mono_specialist","resets":0,"analysis_quality":"detailed_value","top_sports":["Football"],"top_leagues":["Eng. Premier"],"avg_stake":5.2,"odds_avg":1.95,"win_rate":54.2,"live_pct":10,"avg_hours_before_match":18,"followers":342,"months_active":24,"profitable_months_12":8,"pinnacle_yield":7.8,"soft_bookie_yield":9.1,"live_yield":3.2,"prematch_yield":9.0,"sport_percentages":{},"bookie_percentages":{},"top_bookmakers":["Pinnacle"],"scraped_at":ts,"is_demo":True},
            {"name":"DEMO_IceHockey_Pro","url":f"{BASE_URL}/demo2","yield_pct":7.1,"picks_count":2103,"verification":"paid_copytip","bookmaker_profile":"asian_dominant","recent_form_yield":5.8,"specialization":"mono_specialist","resets":0,"analysis_quality":"detailed_value","top_sports":["Ice Hockey"],"top_leagues":["NHL","KHL"],"avg_stake":3.0,"odds_avg":1.88,"win_rate":56.1,"live_pct":8,"avg_hours_before_match":8,"followers":512,"months_active":36,"profitable_months_12":9,"pinnacle_yield":6.8,"soft_bookie_yield":7.5,"live_yield":5.0,"prematch_yield":7.2,"sport_percentages":{},"bookie_percentages":{},"top_bookmakers":["Pinnacle","Bet365"],"scraped_at":ts,"is_demo":True},
            {"name":"DEMO_LiveKing_FRAUD","url":f"{BASE_URL}/demo3","yield_pct":22.4,"picks_count":456,"verification":"free","bookmaker_profile":"soft_only","recent_form_yield":18.0,"specialization":"chaotic_multi","resets":3,"analysis_quality":"none","top_sports":["Football","Tennis","Basketball"],"top_leagues":["Various"],"avg_stake":8.5,"odds_avg":2.80,"win_rate":42.0,"live_pct":65,"avg_hours_before_match":0,"followers":28,"months_active":6,"profitable_months_12":4,"pinnacle_yield":-3.5,"soft_bookie_yield":25.0,"live_yield":30.0,"prematch_yield":4.0,"sport_percentages":{},"bookie_percentages":{},"top_bookmakers":["Bet365","Unibet"],"scraped_at":ts,"is_demo":True},
        ]

if __name__ == "__main__":
    asyncio.run(BlogabetScraper().run())
