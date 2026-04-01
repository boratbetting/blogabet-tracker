#!/usr/bin/env python3
"""
BLOGABET AUTO-SCRAPER v2 — naprawiony login + discovery
"""

import asyncio
import json
import os
import re
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Optional, Dict, List
from playwright.async_api import async_playwright, Page, Browser

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

def load_json(path, default=None):
    if path.exists():
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    return default if default is not None else []

def parse_number(text: str) -> float:
    if not text:
        return 0.0
    text = text.strip().replace("\xa0", "").replace(" ", "").rstrip("%").lstrip("+")
    text = re.sub(r'[^\d.,\-]', '', text)
    if "," in text and "." in text:
        if text.index(",") < text.index("."):
            text = text.replace(",", "")
        else:
            text = text.replace(".", "").replace(",", ".")
    elif "," in text:
        parts = text.split(",")
        if len(parts[-1]) == 3:
            text = text.replace(",", "")
        else:
            text = text.replace(",", ".")
    try:
        return float(text)
    except ValueError:
        return 0.0

def log(msg, level="INFO"):
    ts = now_utc().strftime("%H:%M:%S")
    print(f"[{ts}] [{level}] {msg}")


class BlogabetScraper:
    def __init__(self):
        self.browser: Optional[Browser] = None
        self.page: Optional[Page] = None
        self.collected_urls: set = set()
        self.tipsters: List[Dict] = []
        self.logged_in = False

    async def start(self):
        pw = await async_playwright().start()
        self.browser = await pw.chromium.launch(
            headless=True,
            args=["--no-sandbox", "--disable-dev-shm-usage", "--disable-blink-features=AutomationControlled"]
        )
        context = await self.browser.new_context(
            viewport={"width": 1366, "height": 768},
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
            locale="en-US"
        )
        self.page = await context.new_page()
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

    async def _handle_age_and_cookies(self):
        """Handle all popups: age verification, cookies, modals."""
        await asyncio.sleep(1)
        # Age verification — Blogabet shows this on first visit
        for sel in [
            "text=Yes", "text=YES", "text=I am 18",
            "button:has-text('Yes')", "a:has-text('Yes')",
            ".age-verify-yes", "#age-yes", "[data-age='yes']",
            "text=Enter", "text=ENTER",
            "button:has-text('Enter')", "a:has-text('Enter')",
        ]:
            if await self._safe_click(sel):
                log("  Age verification handled")
                break
        await asyncio.sleep(1)

        # Cookie consent
        for sel in [
            "text=Accept", "text=Agree", "text=Got it",
            "button:has-text('Accept')", ".cookie-accept",
            "#cookie-accept", "text=OK",
        ]:
            if await self._safe_click(sel):
                log("  Cookie consent handled")
                break

        # Close any modal overlays
        for sel in [".modal-close", ".close-modal", "button.close", "[aria-label='Close']"]:
            await self._safe_click(sel, timeout=1000)

    # ── LOGIN ────────────────────────────────────────────────────
    async def login(self) -> bool:
        if not BLOGABET_USER or not BLOGABET_PASS:
            log("Brak BLOGABET_USER / BLOGABET_PASS — tryb bez logowania", "WARN")
            await self.page.goto(BASE_URL, wait_until="domcontentloaded")
            await self._handle_age_and_cookies()
            return False

        log(f"Logowanie jako: {BLOGABET_USER[:3]}***")

        # Step 1: Go to main page first to handle age verification
        await self.page.goto(BASE_URL, wait_until="domcontentloaded")
        await self._handle_age_and_cookies()
        await asyncio.sleep(2)

        # Step 2: Navigate to login page
        await self.page.goto(f"{BASE_URL}/login", wait_until="domcontentloaded")
        await asyncio.sleep(3)
        await self._handle_age_and_cookies()

        # Step 3: Debug — log what we see
        page_url = self.page.url
        page_title = await self.page.title()
        log(f"  URL: {page_url}, Title: {page_title}")

        # Step 4: Try to find ANY input fields on the page
        all_inputs = await self.page.query_selector_all("input")
        log(f"  Znaleziono {len(all_inputs)} inputów na stronie")
        for inp in all_inputs[:10]:
            inp_type = await inp.get_attribute("type") or "?"
            inp_name = await inp.get_attribute("name") or "?"
            inp_id = await inp.get_attribute("id") or "?"
            inp_ph = await inp.get_attribute("placeholder") or "?"
            log(f"    input: type={inp_type}, name={inp_name}, id={inp_id}, placeholder={inp_ph}")

        # Step 5: Try multiple strategies to find and fill login form
        login_success = False

        # Strategy A: Find by input type
        try:
            text_inputs = await self.page.query_selector_all("input[type='text'], input[type='email'], input:not([type='hidden']):not([type='password']):not([type='submit']):not([type='checkbox'])")
            pass_inputs = await self.page.query_selector_all("input[type='password']")

            if text_inputs and pass_inputs:
                log(f"  Strategia A: {len(text_inputs)} text input(s), {len(pass_inputs)} password input(s)")
                await text_inputs[0].fill(BLOGABET_USER)
                await asyncio.sleep(0.5)
                await pass_inputs[0].fill(BLOGABET_PASS)
                await asyncio.sleep(0.5)

                # Try to submit
                submitted = False
                for sel in [
                    "button[type='submit']", "input[type='submit']",
                    "button:has-text('Login')", "button:has-text('Log in')",
                    "button:has-text('Sign in')", "button:has-text('Submit')",
                    "a:has-text('Login')", ".btn-login", ".login-btn",
                    "#login-btn", "form button",
                ]:
                    if await self._safe_click(sel, timeout=2000):
                        submitted = True
                        log(f"  Submit via: {sel}")
                        break

                if not submitted:
                    # Try pressing Enter
                    await pass_inputs[0].press("Enter")
                    log("  Submit via Enter key")

                await asyncio.sleep(5)

                # Check if login worked
                current_url = self.page.url
                if "/login" not in current_url.lower():
                    login_success = True
                    log("  ✓ Login pomyślny (URL changed)")
                else:
                    # Check for logged-in indicators
                    html = await self.page.content()
                    if any(x in html.lower() for x in ["logout", "log out", "my profile", "my tipsters", "sign out"]):
                        login_success = True
                        log("  ✓ Login pomyślny (detected logout link)")
        except Exception as e:
            log(f"  Strategia A failed: {e}", "WARN")

        # Strategy B: Try using JavaScript to submit
        if not login_success:
            try:
                log("  Strategia B: JavaScript fill...")
                await self.page.evaluate(f"""
                    () => {{
                        const inputs = document.querySelectorAll('input');
                        for (const inp of inputs) {{
                            const t = (inp.type || '').toLowerCase();
                            const n = (inp.name || '').toLowerCase();
                            const p = (inp.placeholder || '').toLowerCase();
                            if (t === 'password') {{
                                inp.value = '{BLOGABET_PASS}';
                                inp.dispatchEvent(new Event('input', {{bubbles: true}}));
                            }} else if (t === 'text' || t === 'email' || n.includes('user') || n.includes('email') || n.includes('login') || p.includes('user') || p.includes('email')) {{
                                inp.value = '{BLOGABET_USER}';
                                inp.dispatchEvent(new Event('input', {{bubbles: true}}));
                            }}
                        }}
                    }}
                """)
                await asyncio.sleep(1)

                # Submit form
                await self.page.evaluate("""
                    () => {
                        const form = document.querySelector('form');
                        if (form) form.submit();
                        else {
                            const btn = document.querySelector("button[type='submit'], input[type='submit'], .btn-login, .login-btn");
                            if (btn) btn.click();
                        }
                    }
                """)
                await asyncio.sleep(5)

                current_url = self.page.url
                if "/login" not in current_url.lower():
                    login_success = True
                    log("  ✓ Login pomyślny (Strategy B)")
            except Exception as e:
                log(f"  Strategia B failed: {e}", "WARN")

        self.logged_in = login_success
        if not login_success:
            log("  ✗ Login nie powiódł się — kontynuuję w trybie publicznym", "WARN")

        return login_success

    # ── DISCOVER TIPSTERS ────────────────────────────────────────
    async def discover_tipsters(self):
        log(f"Odkrywanie typerów (min {MIN_PICKS} picks, min {MIN_YIELD}% yield)...")

        # Try the tipsters listing page
        pages_to_try = [
            f"{BASE_URL}/tipsters",
            f"{BASE_URL}/tips",
            f"{BASE_URL}/feed",
            f"{BASE_URL}/auto-betting",
        ]

        for url in pages_to_try:
            if len(self.collected_urls) >= MAX_TIPSTERS:
                break
            log(f"  Próbuję: {url}")
            await self._scrape_listing(url)

        log(f"Odkryto {len(self.collected_urls)} unikalnych typerów")

    async def _scrape_listing(self, url: str):
        try:
            await self.page.goto(url, wait_until="domcontentloaded")
            await asyncio.sleep(3)
            await self._handle_age_and_cookies()
            await asyncio.sleep(2)

            # Debug: log page title and URL
            log(f"    Loaded: {self.page.url}")

            # Extract all links from the page
            links = await self.page.evaluate("""
                () => {
                    const results = [];
                    const anchors = document.querySelectorAll('a[href]');
                    for (const a of anchors) {
                        const href = a.getAttribute('href') || '';
                        const text = (a.textContent || '').trim().substring(0, 50);
                        results.push({href, text});
                    }
                    return results;
                }
            """)

            log(f"    Znaleziono {len(links)} linków na stronie")

            # Filter: Blogabet tipster profile URLs
            # Typical pattern: /tipster/username or just /username
            excluded = {
                "/login", "/register", "/help", "/tips", "/tipsters", "/feed",
                "/betting-guide", "/auto-betting", "/announcement", "/",
                "/terms", "/privacy", "/about", "/contact", "/cashback",
                "/seller-admin", "/forgot-password", "/reset-password"
            }

            for link in links:
                href = link["href"]
                if not href:
                    continue

                # Normalize
                if href.startswith("http"):
                    if "blogabet.com" not in href:
                        continue
                    path = href.split("blogabet.com")[-1]
                else:
                    path = href

                path = path.split("?")[0].split("#")[0].rstrip("/")

                if not path or path in excluded:
                    continue

                # Skip paths with multiple segments (not tipster profiles)
                if path.count("/") > 2:
                    continue

                # Skip non-tipster paths
                skip_patterns = [
                    "/betting-guide", "/announcement", "/cashback", "/auto-betting",
                    "/help", "/tips", "/tipsters", "/feed", "/login", "/register",
                    ".png", ".jpg", ".css", ".js", "/page/", "/category/",
                    "/static/", "/assets/", "/betting-", "/hacks/"
                ]
                if any(p in path.lower() for p in skip_patterns):
                    continue

                # Tipster profile pattern: /username or /tipster/username
                if re.match(r'^/[a-zA-Z0-9_\-]+$', path) or re.match(r'^/tipster/[a-zA-Z0-9_\-]+$', path):
                    full_url = f"{BASE_URL}{path}"
                    self.collected_urls.add(full_url)

            log(f"    → {len(self.collected_urls)} typerów (po filtrze)")

            # Try to paginate — scroll down or click "load more"
            for _ in range(3):
                try:
                    await self.page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                    await asyncio.sleep(2)
                    # Try clicking load more / next page
                    for sel in ["text=Load more", "text=Next", ".load-more", ".pagination .next", "a:has-text('»')"]:
                        if await self._safe_click(sel, timeout=2000):
                            await asyncio.sleep(3)
                            break
                except:
                    break

            # Re-extract after scrolling
            links2 = await self.page.evaluate("""
                () => {
                    const results = [];
                    const anchors = document.querySelectorAll('a[href]');
                    for (const a of anchors) {
                        const href = a.getAttribute('href') || '';
                        results.push(href);
                    }
                    return results;
                }
            """)

            for href in links2:
                if not href:
                    continue
                path = href.split("blogabet.com")[-1] if "blogabet.com" in href else href
                path = path.split("?")[0].split("#")[0].rstrip("/")
                if re.match(r'^/[a-zA-Z0-9_\-]+$', path):
                    skip = any(p in path.lower() for p in ["/betting", "/help", "/tips", "/login", "/feed", "/register", "/announcement", "/auto", "/cashback"])
                    if not skip and path not in excluded:
                        self.collected_urls.add(f"{BASE_URL}{path}")

            log(f"    → {len(self.collected_urls)} typerów (po scroll)")

        except Exception as e:
            log(f"    Błąd: {e}", "WARN")

    # ── SCRAPE PROFILE ───────────────────────────────────────────
    async def scrape_tipster_profile(self, url: str) -> Optional[Dict]:
        try:
            await self.page.goto(url, wait_until="domcontentloaded")
            await asyncio.sleep(2)
            await self._handle_age_and_cookies()

            html = await self.page.content()
            text_content = await self.page.evaluate("() => document.body.innerText")

            tipster = {
                "url": url,
                "name": url.rstrip("/").split("/")[-1],
                "scraped_at": now_utc().isoformat(),
            }

            # Extract numbers from page text
            # Look for patterns like "PICKS\n1,247" or "1,247 picks"
            picks_match = re.search(r'(?:PICKS|picks|Tips)[:\s]*\n?\s*([\d,.\s]+)', text_content)
            if picks_match:
                tipster["picks_count"] = int(parse_number(picks_match.group(1)))
            else:
                nums = re.findall(r'([\d,]+)\s*(?:picks|tips)', text_content, re.IGNORECASE)
                if nums:
                    tipster["picks_count"] = int(parse_number(nums[0]))

            # Yield
            yield_match = re.search(r'(?:YIELD|yield|ROI)[:\s]*\n?\s*([+\-]?[\d,.]+)\s*%?', text_content)
            if yield_match:
                tipster["yield_pct"] = parse_number(yield_match.group(1))
            else:
                ylds = re.findall(r'([+\-]?\d+\.?\d*)\s*%?\s*(?:yield|roi)', text_content, re.IGNORECASE)
                if ylds:
                    tipster["yield_pct"] = parse_number(ylds[0])

            # Profit
            profit_match = re.search(r'(?:PROFIT|profit|Units)[:\s]*\n?\s*([+\-]?[\d,.]+)', text_content)
            if profit_match:
                tipster["profit_units"] = parse_number(profit_match.group(1))

            # Win rate
            wr_match = re.search(r'(?:Win\s*rate|WIN\s*RATE|Winrate)[:\s]*\n?\s*([\d,.]+)\s*%', text_content, re.IGNORECASE)
            if wr_match:
                tipster["win_rate"] = parse_number(wr_match.group(1))

            # Followers
            fol_match = re.search(r'(?:FOLLOWERS|followers)[:\s]*\n?\s*([\d,]+)', text_content)
            if fol_match:
                tipster["followers"] = int(parse_number(fol_match.group(1)))
            else:
                tipster["followers"] = 0

            # Odds average
            odds_match = re.search(r'(?:Odds?\s*(?:avg|average)|AVG\s*ODDS)[:\s]*\n?\s*([\d,.]+)', text_content, re.IGNORECASE)
            if odds_match:
                tipster["odds_avg"] = parse_number(odds_match.group(1))
            else:
                tipster["odds_avg"] = 0

            # Stake average
            stake_match = re.search(r'(?:Stake\s*(?:avg|average))[:\s]*\n?\s*([\d,.]+)', text_content, re.IGNORECASE)
            if stake_match:
                tipster["avg_stake"] = parse_number(stake_match.group(1))
            else:
                tipster["avg_stake"] = 5.0

            # Verification
            tipster["verification"] = "free"
            if re.search(r'subscribe|paid\s*service|buy\s*now', html, re.IGNORECASE):
                tipster["verification"] = "paid"
            if re.search(r'copytip|auto.?bet', html, re.IGNORECASE):
                tipster["verification"] = "paid_copytip"
            verified_el = await self.page.query_selector_all("[class*='verified'], [class*='checkmark'], [class*='pro-badge']")
            if verified_el and tipster["verification"] == "free":
                tipster["verification"] = "pro"
            if re.search(r'un-?verified', html, re.IGNORECASE):
                tipster["verification"] = "free"

            # Resets
            reset_match = re.search(r'[Rr]eset[s]?\s*[:\(]?\s*(\d+)', text_content)
            tipster["resets"] = int(reset_match.group(1)) if reset_match else 0
            if not reset_match:
                reset_el = await self.page.query_selector_all("[class*='reset']")
                if reset_el:
                    tipster["resets"] = 1

            # Sports
            sports_found = re.findall(
                r'(Football|Basketball|Tennis|Ice Hockey|Esports?|Handball|Volleyball|Baseball|Am\.\s*Football|Boxing|MMA|Cricket|Darts|Futsal|Table Tennis)',
                text_content, re.IGNORECASE
            )
            tipster["top_sports"] = list(dict.fromkeys([s.strip() for s in sports_found]))[:5]
            tipster["sport_percentages"] = {}

            # Bookmakers
            bookies_found = re.findall(
                r'(Pinnacle|Bet365|SBOBet|Dafabet|188bet|AsianConnect|Betfair|Unibet|William\s*Hill|Ladbrokes|Bwin|Marathonbet|1xBet|22bet|Betway|Sportmarket)',
                text_content, re.IGNORECASE
            )
            tipster["top_bookmakers"] = list(dict.fromkeys([b.strip() for b in bookies_found]))[:5]
            tipster["bookie_percentages"] = {}

            # Classify
            asian = {"Pinnacle", "SBOBet", "Dafabet", "188bet", "AsianConnect", "Sportmarket"}
            if any(b in asian for b in tipster["top_bookmakers"][:2]):
                tipster["bookmaker_profile"] = "asian_dominant"
            elif any(b in asian for b in tipster["top_bookmakers"]):
                tipster["bookmaker_profile"] = "mixed"
            else:
                tipster["bookmaker_profile"] = "soft_only"

            if len(tipster["top_sports"]) <= 1:
                tipster["specialization"] = "mono_specialist"
            elif len(tipster["top_sports"]) <= 3:
                tipster["specialization"] = "focused_multi"
            else:
                tipster["specialization"] = "chaotic_multi"

            # Defaults for fields we can't easily scrape
            tipster.setdefault("picks_count", 0)
            tipster.setdefault("yield_pct", 0)
            tipster.setdefault("win_rate", 0)
            tipster.setdefault("recent_form_yield", tipster.get("yield_pct", 0) * 0.8)
            tipster.setdefault("live_pct", 10)
            tipster.setdefault("avg_hours_before_match", 24)
            tipster.setdefault("months_active", 12)
            tipster.setdefault("profitable_months_12", 6)
            tipster.setdefault("pinnacle_yield", None)
            tipster.setdefault("soft_bookie_yield", None)
            tipster.setdefault("live_yield", None)
            tipster.setdefault("prematch_yield", None)
            tipster.setdefault("top_leagues", [])
            tipster.setdefault("analysis_quality", "short_desc")

            # Filter
            if tipster["picks_count"] < MIN_PICKS:
                return None
            if tipster["yield_pct"] < MIN_YIELD:
                return None

            return tipster

        except Exception as e:
            log(f"    Błąd profilu {url}: {e}", "ERROR")
            return None

    # ── MAIN FLOW ────────────────────────────────────────────────
    async def run(self):
        await self.start()
        try:
            await self.login()
            await self.discover_tipsters()

            if not self.collected_urls:
                log("Brak typerów — generuję dane demo", "WARN")
                self._generate_fallback_data()
                save_json(RAW_FILE, self.tipsters)
                return self.tipsters

            log(f"\nScrapuję profile {len(self.collected_urls)} typerów...")
            for i, url in enumerate(sorted(self.collected_urls)):
                if len(self.tipsters) >= MAX_TIPSTERS:
                    break
                log(f"  [{i+1}/{len(self.collected_urls)}] {url.split('/')[-1]}")
                tipster = await self.scrape_tipster_profile(url)
                if tipster:
                    self.tipsters.append(tipster)
                    log(f"    ✓ yield={tipster.get('yield_pct',0):+.1f}%, picks={tipster.get('picks_count',0)}")
                else:
                    log(f"    ✗ Pominięty")
                await asyncio.sleep(2.5)

            save_json(RAW_FILE, self.tipsters)
            log(f"\n✓ Zapisano {len(self.tipsters)} typerów → {RAW_FILE}")
            return self.tipsters
        finally:
            await self.close()

    def _generate_fallback_data(self):
        log("Generuję dane demo jako fallback...")
        self.tipsters = [
            {"name":"DEMO_SharpEdge","url":f"{BASE_URL}/demo","yield_pct":8.4,"picks_count":1247,"verification":"paid","bookmaker_profile":"asian_dominant","recent_form_yield":6.2,"specialization":"mono_specialist","resets":0,"analysis_quality":"detailed_value","top_sports":["Football"],"top_leagues":["Eng. Premier"],"avg_stake":5.2,"odds_avg":1.95,"win_rate":54.2,"live_pct":10,"avg_hours_before_match":18,"followers":342,"months_active":24,"profitable_months_12":8,"pinnacle_yield":7.8,"soft_bookie_yield":9.1,"live_yield":3.2,"prematch_yield":9.0,"sport_percentages":{},"bookie_percentages":{},"top_bookmakers":["Pinnacle"],"scraped_at":now_utc().isoformat(),"is_demo":True},
            {"name":"DEMO_IceHockey_Pro","url":f"{BASE_URL}/demo2","yield_pct":7.1,"picks_count":2103,"verification":"paid_copytip","bookmaker_profile":"asian_dominant","recent_form_yield":5.8,"specialization":"mono_specialist","resets":0,"analysis_quality":"detailed_value","top_sports":["Ice Hockey"],"top_leagues":["NHL","KHL"],"avg_stake":3.0,"odds_avg":1.88,"win_rate":56.1,"live_pct":8,"avg_hours_before_match":8,"followers":512,"months_active":36,"profitable_months_12":9,"pinnacle_yield":6.8,"soft_bookie_yield":7.5,"live_yield":5.0,"prematch_yield":7.2,"sport_percentages":{},"bookie_percentages":{},"top_bookmakers":["Pinnacle","Bet365"],"scraped_at":now_utc().isoformat(),"is_demo":True},
            {"name":"DEMO_LiveKing_FRAUD","url":f"{BASE_URL}/demo3","yield_pct":22.4,"picks_count":456,"verification":"free","bookmaker_profile":"soft_only","recent_form_yield":18.0,"specialization":"chaotic_multi","resets":3,"analysis_quality":"none","top_sports":["Football","Tennis","Basketball"],"top_leagues":["Various"],"avg_stake":8.5,"odds_avg":2.80,"win_rate":42.0,"live_pct":65,"avg_hours_before_match":0,"followers":28,"months_active":6,"profitable_months_12":4,"pinnacle_yield":-3.5,"soft_bookie_yield":25.0,"live_yield":30.0,"prematch_yield":4.0,"sport_percentages":{},"bookie_percentages":{},"top_bookmakers":["Bet365","Unibet"],"scraped_at":now_utc().isoformat(),"is_demo":True},
        ]

async def main():
    scraper = BlogabetScraper()
    await scraper.run()

if __name__ == "__main__":
    asyncio.run(main())
