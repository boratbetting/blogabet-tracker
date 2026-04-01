#!/usr/bin/env python3
"""
BLOGABET AUTO-SCRAPER v3 — fix age gate + redirect loop
"""

import asyncio
import json
import os
import re
from datetime import datetime, timezone
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
            args=["--no-sandbox", "--disable-dev-shm-usage",
                  "--disable-blink-features=AutomationControlled"]
        )

        # Set age verification cookie BEFORE creating page
        self.context = await self.browser.new_context(
            viewport={"width": 1366, "height": 768},
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                       "AppleWebKit/537.36 (KHTML, like Gecko) "
                       "Chrome/122.0.0.0 Safari/537.36",
            locale="en-US",
        )

        # Pre-set cookies that Blogabet checks for age verification
        await self.context.add_cookies([
            {"name": "age_verified", "value": "1", "domain": ".blogabet.com", "path": "/"},
            {"name": "age_gate", "value": "passed", "domain": ".blogabet.com", "path": "/"},
            {"name": "ageverify", "value": "1", "domain": ".blogabet.com", "path": "/"},
            {"name": "is_adult", "value": "1", "domain": ".blogabet.com", "path": "/"},
            {"name": "over18", "value": "1", "domain": ".blogabet.com", "path": "/"},
            {"name": "cookie_consent", "value": "1", "domain": ".blogabet.com", "path": "/"},
            {"name": "cookies_accepted", "value": "1", "domain": ".blogabet.com", "path": "/"},
        ])

        self.page = await self.context.new_page()
        self.page.set_default_timeout(20000)
        log("Przeglądarka uruchomiona (z cookies age verification)")

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

    async def _handle_popups(self):
        """Aggressively close all popups, modals, age gates."""
        await asyncio.sleep(1)

        # Click ALL possible age/cookie/modal buttons
        buttons_to_try = [
            # Age verification variants
            "button:has-text('Yes')", "a:has-text('Yes')",
            "button:has-text('YES')", "a:has-text('YES')",
            "button:has-text('I am 18')", "a:has-text('I am 18')",
            "button:has-text('Enter')", "a:has-text('Enter')",
            "button:has-text('ENTER')", "a:has-text('ENTER')",
            "button:has-text('Confirm')", "a:has-text('Confirm')",
            "button:has-text('I agree')",
            # Cookie consent
            "button:has-text('Accept')", "button:has-text('Accept All')",
            "button:has-text('Agree')", "button:has-text('Got it')",
            "button:has-text('OK')", "button:has-text('Continue')",
            # Modal close
            ".modal-close", "button.close", "[aria-label='Close']",
            ".close-btn", "#close-popup",
        ]

        clicked = []
        for sel in buttons_to_try:
            if await self._safe_click(sel, timeout=1500):
                clicked.append(sel)
                await asyncio.sleep(0.5)

        if clicked:
            log(f"  Popups zamknięte: {len(clicked)}")

        # Also try JS to dismiss overlays
        try:
            await self.page.evaluate("""
                () => {
                    // Remove overlay elements
                    document.querySelectorAll('.modal, .overlay, .popup, [class*="age"], [class*="cookie"], [class*="consent"]').forEach(el => {
                        el.style.display = 'none';
                        el.remove();
                    });
                    // Remove body scroll locks
                    document.body.style.overflow = 'auto';
                    document.documentElement.style.overflow = 'auto';
                }
            """)
        except:
            pass

    async def _goto(self, url: str) -> str:
        """Navigate to URL, handle popups, return final URL."""
        await self.page.goto(url, wait_until="domcontentloaded")
        await asyncio.sleep(2)
        await self._handle_popups()
        await asyncio.sleep(1)

        final_url = self.page.url
        # If redirected to bookmakers, try clicking age gate again
        if "/bookmakers" in final_url and "/bookmakers" not in url:
            log(f"  Redirect detected → {final_url}, trying age gate fix...")
            await self._handle_popups()
            await asyncio.sleep(1)

            # Try navigating again
            await self.page.goto(url, wait_until="domcontentloaded")
            await asyncio.sleep(3)
            await self._handle_popups()
            final_url = self.page.url

            if "/bookmakers" in final_url and "/bookmakers" not in url:
                log(f"  Still redirected → {final_url}", "WARN")

                # Last resort: try JS navigation
                await self.page.evaluate(f"window.location.href = '{url}'")
                await asyncio.sleep(3)
                await self._handle_popups()
                final_url = self.page.url

        log(f"  Loaded: {final_url}")
        return final_url

    # ── LOGIN ────────────────────────────────────────────────────
    async def login(self) -> bool:
        if not BLOGABET_USER or not BLOGABET_PASS:
            log("Brak credentials — tryb publiczny", "WARN")
            await self._goto(BASE_URL)
            return False

        log(f"Logowanie jako: {BLOGABET_USER[:3]}***")

        # Step 1: Visit main page to set session cookies
        final = await self._goto(BASE_URL)
        log(f"  Main page: {final}")

        # Capture current cookies to debug
        cookies = await self.context.cookies()
        cookie_names = [c["name"] for c in cookies]
        log(f"  Cookies aktywne: {len(cookies)} ({', '.join(cookie_names[:10])})")

        # Step 2: Try login page
        final = await self._goto(f"{BASE_URL}/login")
        title = await self.page.title()
        log(f"  Login page: {final}, Title: {title}")

        # Step 3: Screenshot-style debug — dump page text
        page_text = await self.page.evaluate("() => document.body.innerText.substring(0, 500)")
        log(f"  Page text preview: {page_text[:200]}...")

        # Step 4: Find ALL inputs and log them
        all_inputs = await self.page.query_selector_all("input")
        log(f"  Inputy na stronie: {len(all_inputs)}")
        for inp in all_inputs[:15]:
            attrs = await self.page.evaluate("""
                (el) => ({
                    type: el.type || '?',
                    name: el.name || '?',
                    id: el.id || '?',
                    placeholder: el.placeholder || '?',
                    className: el.className || '?',
                    visible: el.offsetParent !== null
                })
            """, inp)
            log(f"    [{attrs['type']}] name={attrs['name']} id={attrs['id']} "
                f"placeholder={attrs['placeholder']} class={attrs['className'][:30]} visible={attrs['visible']}")

        # Step 5: Find ALL forms
        forms = await self.page.query_selector_all("form")
        log(f"  Forms na stronie: {len(forms)}")
        for i, form in enumerate(forms[:5]):
            action = await form.get_attribute("action") or "?"
            method = await form.get_attribute("method") or "?"
            form_id = await form.get_attribute("id") or "?"
            log(f"    form[{i}]: action={action} method={method} id={form_id}")

        # Step 6: Try login with multiple strategies
        login_success = False

        # Strategy A: Fill visible text + password inputs
        try:
            pass_inputs = await self.page.query_selector_all("input[type='password']")
            if pass_inputs:
                # Find the text/email input that's near the password field
                visible_text_inputs = []
                for inp in all_inputs:
                    is_visible = await self.page.evaluate("(el) => el.offsetParent !== null", inp)
                    inp_type = await inp.get_attribute("type") or ""
                    if is_visible and inp_type.lower() in ("text", "email", ""):
                        visible_text_inputs.append(inp)

                if visible_text_inputs:
                    log(f"  Strategy A: {len(visible_text_inputs)} visible text, {len(pass_inputs)} password")
                    await visible_text_inputs[0].fill(BLOGABET_USER)
                    await asyncio.sleep(0.3)
                    await pass_inputs[0].fill(BLOGABET_PASS)
                    await asyncio.sleep(0.3)
                    await pass_inputs[0].press("Enter")
                    await asyncio.sleep(5)

                    new_url = self.page.url
                    html = await self.page.content()
                    has_logout = any(x in html.lower() for x in ["logout", "log out", "sign out", "my profile", "my tipsters"])

                    if "/login" not in new_url and has_logout:
                        login_success = True
                        log(f"  ✓ Login A pomyślny → {new_url}")
                    elif has_logout:
                        login_success = True
                        log(f"  ✓ Login A pomyślny (logout link found)")
                    else:
                        log(f"  Strategy A: no login detected (url={new_url})")
            else:
                log("  Strategy A: brak pól password")
        except Exception as e:
            log(f"  Strategy A error: {e}", "WARN")

        # Strategy B: JavaScript
        if not login_success:
            try:
                log("  Strategy B: JS fill + submit...")
                await self._goto(f"{BASE_URL}/login")

                js_result = await self.page.evaluate(f"""
                    () => {{
                        let filled = 0;
                        const inputs = document.querySelectorAll('input');
                        for (const inp of inputs) {{
                            if (inp.type === 'password' && inp.offsetParent !== null) {{
                                inp.value = '{BLOGABET_PASS}';
                                inp.dispatchEvent(new Event('input', {{bubbles:true}}));
                                inp.dispatchEvent(new Event('change', {{bubbles:true}}));
                                filled++;
                            }}
                            if ((inp.type === 'text' || inp.type === 'email' || inp.type === '') && inp.offsetParent !== null) {{
                                const ph = (inp.placeholder || '').toLowerCase();
                                const nm = (inp.name || '').toLowerCase();
                                if (inp.type === 'text' || inp.type === 'email' || nm.includes('user') || nm.includes('mail') || ph.includes('user') || ph.includes('mail') || ph.includes('login')) {{
                                    inp.value = '{BLOGABET_USER}';
                                    inp.dispatchEvent(new Event('input', {{bubbles:true}}));
                                    inp.dispatchEvent(new Event('change', {{bubbles:true}}));
                                    filled++;
                                }}
                            }}
                        }}
                        // Try submitting
                        const form = document.querySelector('form');
                        if (form) {{ form.submit(); return 'form_submitted_' + filled; }}
                        const btn = document.querySelector("button[type='submit'], input[type='submit']");
                        if (btn) {{ btn.click(); return 'button_clicked_' + filled; }}
                        return 'no_submit_found_' + filled;
                    }}
                """)
                log(f"  Strategy B result: {js_result}")
                await asyncio.sleep(5)

                html = await self.page.content()
                if any(x in html.lower() for x in ["logout", "log out", "my profile"]):
                    login_success = True
                    log(f"  ✓ Login B pomyślny")
            except Exception as e:
                log(f"  Strategy B error: {e}", "WARN")

        self.logged_in = login_success
        if not login_success:
            log("  ✗ Login nie powiódł się — kontynuuję publicznie", "WARN")

        # Log final state
        final_cookies = await self.context.cookies()
        log(f"  Final cookies: {len(final_cookies)}")
        return login_success

    # ── DISCOVER TIPSTERS ────────────────────────────────────────
    async def discover_tipsters(self):
        log(f"Odkrywanie typerów...")

        urls_to_try = [
            f"{BASE_URL}/tipsters",
            f"{BASE_URL}/feed",
            f"{BASE_URL}/tips",
        ]

        for url in urls_to_try:
            if len(self.collected_urls) >= MAX_TIPSTERS:
                break
            log(f"  Próbuję: {url}")
            final = await self._goto(url)

            # If still redirected, log it and try next
            page_text_preview = await self.page.evaluate("() => document.body.innerText.substring(0, 300)")
            log(f"  Text preview: {page_text_preview[:150]}...")

            await self._extract_tipster_links()

        log(f"Odkryto {len(self.collected_urls)} unikalnych typerów")

    async def _extract_tipster_links(self):
        """Extract tipster profile links from current page."""
        links = await self.page.evaluate("""
            () => {
                const results = [];
                document.querySelectorAll('a[href]').forEach(a => {
                    const href = a.getAttribute('href') || '';
                    const text = (a.textContent || '').trim();
                    const parent = a.closest('[class]');
                    const parentClass = parent ? parent.className : '';
                    results.push({href, text: text.substring(0, 60), parentClass: parentClass.substring(0, 80)});
                });
                return results;
            }
        """)

        log(f"    Linków na stronie: {len(links)}")

        # Log first 20 links for debugging
        for link in links[:20]:
            log(f"      href={link['href'][:60]}  text={link['text'][:30]}  parent={link['parentClass'][:40]}")

        # Known non-tipster paths
        excluded_paths = {
            "", "/", "/login", "/register", "/help", "/tips", "/tipsters",
            "/feed", "/betting-guide", "/auto-betting", "/announcement",
            "/terms", "/privacy", "/about", "/contact", "/cashback",
            "/seller-admin", "/forgot-password", "/reset-password",
            "/bookmakers", "/customer-support", "/market", "/pricing",
            "/academy", "/blog", "/promotions", "/search"
        }

        excluded_prefixes = [
            "/betting-guide/", "/announcement/", "/cashback/", "/auto-betting/",
            "/help/", "/page/", "/category/", "/static/", "/assets/",
            "/hacks/", "/tutorials/", "/bookmakers/", "/academy/",
        ]

        for link in links:
            href = link["href"]
            if not href:
                continue

            # Normalize path
            if href.startswith("http"):
                if "blogabet.com" not in href:
                    continue
                path = "/" + href.split("blogabet.com/")[-1] if "blogabet.com/" in href else ""
            elif href.startswith("/"):
                path = href
            else:
                continue

            path = path.split("?")[0].split("#")[0].rstrip("/")

            if not path or path in excluded_paths:
                continue
            if any(path.startswith(p) for p in excluded_prefixes):
                continue
            if any(ext in path for ext in [".png", ".jpg", ".css", ".js", ".svg"]):
                continue

            # Tipster profile: single segment like /username
            if re.match(r'^/[a-zA-Z0-9][a-zA-Z0-9_\-]{1,50}$', path):
                self.collected_urls.add(f"{BASE_URL}{path}")

            # Or /tipster/username
            elif re.match(r'^/tipster/[a-zA-Z0-9_\-]+$', path):
                self.collected_urls.add(f"{BASE_URL}{path}")

        log(f"    → {len(self.collected_urls)} typerów po filtrze")

    # ── SCRAPE PROFILE ───────────────────────────────────────────
    async def scrape_tipster_profile(self, url: str) -> Optional[Dict]:
        try:
            final = await self._goto(url)

            # If redirected to bookmakers, skip
            if "/bookmakers" in final and "/bookmakers" not in url:
                log(f"    Redirect → bookmakers, skip")
                return None

            text_content = await self.page.evaluate("() => document.body.innerText")
            html = await self.page.content()

            tipster = {
                "url": url,
                "name": url.rstrip("/").split("/")[-1],
                "scraped_at": now_utc().isoformat(),
            }

            # PICKS
            m = re.search(r'(?:PICKS|picks|Tips)\s*\n?\s*([\d,.\s]+)', text_content)
            if m:
                tipster["picks_count"] = int(parse_number(m.group(1)))
            else:
                nums = re.findall(r'([\d,]+)\s*(?:picks|tips)', text_content, re.IGNORECASE)
                tipster["picks_count"] = int(parse_number(nums[0])) if nums else 0

            # YIELD
            m = re.search(r'(?:YIELD|yield|ROI)\s*\n?\s*([+\-]?[\d,.]+)\s*%?', text_content)
            tipster["yield_pct"] = parse_number(m.group(1)) if m else 0

            # PROFIT
            m = re.search(r'(?:PROFIT|profit)\s*\n?\s*([+\-]?[\d,.]+)', text_content)
            tipster["profit_units"] = parse_number(m.group(1)) if m else 0

            # WIN RATE
            m = re.search(r'(?:Win\s*rate|WIN\s*RATE)\s*\n?\s*([\d,.]+)\s*%', text_content, re.IGNORECASE)
            tipster["win_rate"] = parse_number(m.group(1)) if m else 0

            # FOLLOWERS
            m = re.search(r'(?:FOLLOWERS|followers)\s*\n?\s*([\d,]+)', text_content)
            tipster["followers"] = int(parse_number(m.group(1))) if m else 0

            # ODDS AVG
            m = re.search(r'(?:Odds?\s*avg|AVG\s*ODDS)\s*\n?\s*([\d,.]+)', text_content, re.IGNORECASE)
            tipster["odds_avg"] = parse_number(m.group(1)) if m else 0

            # STAKE AVG
            m = re.search(r'(?:Stake\s*avg)\s*\n?\s*([\d,.]+)', text_content, re.IGNORECASE)
            tipster["avg_stake"] = parse_number(m.group(1)) if m else 5.0

            # VERIFICATION
            tipster["verification"] = "free"
            if re.search(r'subscribe|paid\s*service|buy\s*now', html, re.IGNORECASE):
                tipster["verification"] = "paid"
            if re.search(r'copytip|auto.?bet', html, re.IGNORECASE):
                tipster["verification"] = "paid_copytip"
            v_el = await self.page.query_selector_all("[class*='verified'], [class*='checkmark'], [class*='pro-badge']")
            if v_el and tipster["verification"] == "free":
                tipster["verification"] = "pro"

            # RESETS
            m = re.search(r'[Rr]eset[s]?\s*[:\(]?\s*(\d+)', text_content)
            tipster["resets"] = int(m.group(1)) if m else 0

            # SPORTS
            sports = re.findall(r'(Football|Basketball|Tennis|Ice Hockey|Esports?|Handball|Volleyball|Baseball|Boxing|MMA|Cricket|Darts|Futsal)', text_content, re.IGNORECASE)
            tipster["top_sports"] = list(dict.fromkeys([s.strip() for s in sports]))[:5]

            # BOOKMAKERS
            bookies = re.findall(r'(Pinnacle|Bet365|SBOBet|Dafabet|188bet|AsianConnect|Betfair|Unibet|Bwin|Marathonbet|1xBet|22bet|Betway|Sportmarket)', text_content, re.IGNORECASE)
            tipster["top_bookmakers"] = list(dict.fromkeys([b.strip() for b in bookies]))[:5]

            # CLASSIFY
            asian = {"Pinnacle","SBOBet","Dafabet","188bet","AsianConnect","Sportmarket"}
            if any(b in asian for b in tipster["top_bookmakers"][:2]):
                tipster["bookmaker_profile"] = "asian_dominant"
            elif any(b in asian for b in tipster["top_bookmakers"]):
                tipster["bookmaker_profile"] = "mixed"
            else:
                tipster["bookmaker_profile"] = "soft_only"

            tipster["specialization"] = (
                "mono_specialist" if len(tipster["top_sports"]) <= 1
                else "focused_multi" if len(tipster["top_sports"]) <= 3
                else "chaotic_multi"
            )

            # DEFAULTS
            tipster.setdefault("recent_form_yield", tipster["yield_pct"] * 0.8)
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
            tipster.setdefault("sport_percentages", {})
            tipster.setdefault("bookie_percentages", {})

            # FILTER
            if tipster["picks_count"] < MIN_PICKS or tipster["yield_pct"] < MIN_YIELD:
                return None

            return tipster

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
                log(f"  [{i+1}/{len(self.collected_urls)}] {url.split('/')[-1]}")
                t = await self.scrape_tipster_profile(url)
                if t:
                    self.tipsters.append(t)
                    log(f"    ✓ yield={t['yield_pct']:+.1f}% picks={t['picks_count']}")
                else:
                    log(f"    ✗ Pominięty")
                await asyncio.sleep(2.5)

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
