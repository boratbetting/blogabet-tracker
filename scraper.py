#!/usr/bin/env python3
"""
BLOGABET AUTO-SCRAPER
═════════════════════
Pełna automatyzacja: logowanie → odkrywanie typerów → scraping profili
→ ekstrakcja statystyk → scoring → generacja dashboardu.

Wymaga:
  pip install playwright beautifulsoup4 lxml
  playwright install chromium

Zmienne środowiskowe:
  BLOGABET_USER     — login do Blogabet
  BLOGABET_PASS     — hasło do Blogabet
  MIN_PICKS         — min. liczba tipów (domyślnie 300)
  MIN_YIELD         — min. yield % (domyślnie 1.0)
  MAX_TIPSTERS      — max typerów do analizy (domyślnie 150)
"""

import asyncio
import json
import os
import re
import sys
import traceback
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Optional, Dict, List, Any

from playwright.async_api import async_playwright, Page, Browser

# ─── KONFIGURACJA ────────────────────────────────────────────────────
BLOGABET_USER = os.environ.get("BLOGABET_USER", "")
BLOGABET_PASS = os.environ.get("BLOGABET_PASS", "")

MIN_PICKS = int(os.environ.get("MIN_PICKS", "300"))
MIN_YIELD = float(os.environ.get("MIN_YIELD", "1.0"))
MAX_TIPSTERS = int(os.environ.get("MAX_TIPSTERS", "150"))

BASE_URL = "https://blogabet.com"
ROOT = Path(__file__).parent
DATA_DIR = ROOT / "data"
RAW_FILE = DATA_DIR / "tipsters_raw.json"

SORT_OPTIONS = ["yield", "profit", "picks"]
PICK_TYPES = ["prematch", "inplay"]

# ─── HELPERS ─────────────────────────────────────────────────────────
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
    """Parse '1,234.56' or '1.234,56' or '+8.4%' → float."""
    if not text:
        return 0.0
    text = text.strip().replace(" ", "").rstrip("%").lstrip("+")
    # Handle comma as thousands separator
    if "," in text and "." in text:
        if text.index(",") < text.index("."):
            text = text.replace(",", "")
        else:
            text = text.replace(".", "").replace(",", ".")
    elif "," in text:
        # Ambiguous — try context
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


# ─── SCRAPER ─────────────────────────────────────────────────────────
class BlogabetScraper:
    def __init__(self):
        self.browser: Optional[Browser] = None
        self.page: Optional[Page] = None
        self.collected_urls: set = set()
        self.tipsters: List[Dict] = []

    async def start(self):
        pw = await async_playwright().start()
        self.browser = await pw.chromium.launch(
            headless=True,
            args=["--no-sandbox", "--disable-dev-shm-usage"]
        )
        context = await self.browser.new_context(
            viewport={"width": 1366, "height": 768},
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                       "AppleWebKit/537.36 (KHTML, like Gecko) "
                       "Chrome/120.0.0.0 Safari/537.36",
            locale="en-US"
        )
        self.page = await context.new_page()
        self.page.set_default_timeout(30000)
        log("Przeglądarka uruchomiona")

    async def close(self):
        if self.browser:
            await self.browser.close()
            log("Przeglądarka zamknięta")

    # ── LOGIN ────────────────────────────────────────────────────────
    async def login(self) -> bool:
        if not BLOGABET_USER or not BLOGABET_PASS:
            log("Brak BLOGABET_USER / BLOGABET_PASS — próbuję bez logowania", "WARN")
            await self.page.goto(BASE_URL, wait_until="domcontentloaded")
            return False

        log(f"Logowanie jako: {BLOGABET_USER}")
        await self.page.goto(f"{BASE_URL}/login", wait_until="domcontentloaded")
        await asyncio.sleep(2)

        # Handle cookie consent if present
        try:
            cookie_btn = self.page.locator("button:has-text('Accept'), button:has-text('Agree'), .cookie-accept, #cookie-accept")
            if await cookie_btn.count() > 0:
                await cookie_btn.first.click()
                await asyncio.sleep(1)
        except Exception:
            pass

        # Age verification if present
        try:
            age_btn = self.page.locator("button:has-text('Yes'), a:has-text('Yes'), .age-verify-yes")
            if await age_btn.count() > 0:
                await age_btn.first.click()
                await asyncio.sleep(1)
        except Exception:
            pass

        # Fill login form
        try:
            await self.page.fill('input[name="username"], input[name="email"], #username, #email', BLOGABET_USER)
            await self.page.fill('input[name="password"], #password', BLOGABET_PASS)
            await self.page.click('button[type="submit"], input[type="submit"], .login-btn, .btn-login')
            await asyncio.sleep(3)

            # Verify login
            if "/login" not in self.page.url:
                log("Zalogowano pomyślnie ✓")
                return True
            else:
                log("Login nie powiódł się — kontynuuję bez logowania", "WARN")
                return False
        except Exception as e:
            log(f"Błąd logowania: {e}", "WARN")
            return False

    # ── DISCOVER TIPSTERS ────────────────────────────────────────────
    async def discover_tipsters(self):
        """
        Przeszukuje stronę /tipsters z różnymi filtrami i sortowaniami,
        zbierając URL-e profili typerów do dalszej analizy.
        """
        log(f"Odkrywanie typerów (min {MIN_PICKS} picks, min {MIN_YIELD}% yield)...")

        # Strategia: odwiedzamy /tipsters z różnymi sortowaniami
        # i zbieramy URL-e typerów
        pages_to_visit = [
            f"{BASE_URL}/tipsters?sort=yield&min_picks={MIN_PICKS}",
            f"{BASE_URL}/tipsters?sort=profit&min_picks={MIN_PICKS}",
            f"{BASE_URL}/tipsters?sort=followers&min_picks={MIN_PICKS}",
            f"{BASE_URL}/tipsters?sort=picks&min_picks={MIN_PICKS}",
            # Filtrowane wg typu zakładu
            f"{BASE_URL}/tipsters?sort=yield&min_picks={MIN_PICKS}&pick_type=prematch",
            f"{BASE_URL}/tipsters?sort=yield&min_picks={MIN_PICKS}&pick_type=inplay",
            # Paid tipsters
            f"{BASE_URL}/tipsters?sort=yield&min_picks={MIN_PICKS}&market=paid",
        ]

        for url in pages_to_visit:
            if len(self.collected_urls) >= MAX_TIPSTERS:
                break
            await self._scrape_tipster_list_page(url)

        log(f"Odkryto {len(self.collected_urls)} unikalnych typerów")

    async def _scrape_tipster_list_page(self, url: str, max_pages: int = 5):
        """Scrape jednej strony listingu typerów (z paginacją)."""
        for page_num in range(1, max_pages + 1):
            if len(self.collected_urls) >= MAX_TIPSTERS:
                return

            page_url = f"{url}&page={page_num}" if "?" in url else f"{url}?page={page_num}"
            try:
                await self.page.goto(page_url, wait_until="domcontentloaded")
                await asyncio.sleep(2)

                # Handle age verification popup
                await self._dismiss_popups()

                # Extract tipster links
                links = await self.page.query_selector_all('a[href*="/tipster/"], a.tipster-link, .tipster-row a, .blog-link')

                if not links:
                    # Fallback: find any links that look like tipster profiles
                    all_links = await self.page.query_selector_all("a[href]")
                    for link in all_links:
                        href = await link.get_attribute("href") or ""
                        # Blogabet tipster URLs are typically /username or /tipster/username
                        if re.match(r"^/(tipster/)?[a-zA-Z0-9_-]+$", href.split("?")[0]):
                            if href not in ("/login", "/register", "/help", "/tips", "/tipsters",
                                           "/feed", "/betting-guide", "/auto-betting", "/announcement"):
                                full_url = f"{BASE_URL}{href}" if href.startswith("/") else href
                                self.collected_urls.add(full_url)
                else:
                    for link in links:
                        href = await link.get_attribute("href") or ""
                        full_url = f"{BASE_URL}{href}" if href.startswith("/") else href
                        self.collected_urls.add(full_url)

                found_on_page = len(links) if links else 0
                log(f"  Strona {page_num}: +{found_on_page} linków (total: {len(self.collected_urls)})")

                # If no results on page, stop pagination
                if found_on_page == 0:
                    break

            except Exception as e:
                log(f"  Błąd na {page_url}: {e}", "WARN")
                break

    async def _dismiss_popups(self):
        """Zamyka typowe popupy (cookie consent, age verification)."""
        selectors = [
            "button:has-text('Accept')", "button:has-text('Agree')",
            "button:has-text('Yes')", "a:has-text('Yes')",
            ".cookie-accept", "#cookie-accept",
            ".age-verify-yes", ".modal-close",
            "button:has-text('I am 18')", "button:has-text('Enter')"
        ]
        for sel in selectors:
            try:
                el = self.page.locator(sel)
                if await el.count() > 0:
                    await el.first.click(timeout=2000)
                    await asyncio.sleep(0.5)
            except Exception:
                pass

    # ── SCRAPE PROFILE ───────────────────────────────────────────────
    async def scrape_tipster_profile(self, url: str) -> Optional[Dict]:
        """
        Pobiera pełne statystyki z profilu typera.
        Analizuje: yield, picks, win rate, odds avg, sport, bookmaker,
        verification, resets, followers, stake avg, live %.
        """
        try:
            await self.page.goto(url, wait_until="domcontentloaded")
            await asyncio.sleep(2)
            await self._dismiss_popups()

            html = await self.page.content()

            tipster = {
                "url": url,
                "scraped_at": now_utc().isoformat(),
            }

            # ── Nazwa typera ──
            tipster["name"] = await self._extract_text(
                ".blog-name, .tipster-name, h1.name, .profile-name, h1"
            ) or url.rstrip("/").split("/")[-1]

            # ── Główne statystyki (PICKS, PROFIT, YIELD, FOLLOWERS) ──
            tipster.update(await self._extract_main_stats())

            # ── Win Rate, Odds Average, Stake Average ──
            tipster.update(await self._extract_box_stats())

            # ── Verification Status ──
            tipster["verification"] = await self._detect_verification()

            # ── Resets ──
            tipster["resets"] = await self._detect_resets()

            # ── Top Sports & Bookmakers (wykresy) ──
            tipster.update(await self._extract_charts())

            # ── Specialization (oblicz z top_sports) ──
            tipster["specialization"] = self._classify_specialization(
                tipster.get("top_sports", []),
                tipster.get("sport_percentages", {})
            )

            # ── Bookmaker Profile ──
            tipster["bookmaker_profile"] = self._classify_bookmaker(
                tipster.get("top_bookmakers", []),
                tipster.get("bookie_percentages", {})
            )

            # ── Followers count ──
            if not tipster.get("followers"):
                tipster["followers"] = await self._extract_followers()

            # ── Przejdź do Statistics page ──
            tipster.update(await self._scrape_statistics_page(url))

            # ── Filtruj: min picks + min yield ──
            if tipster.get("picks_count", 0) < MIN_PICKS:
                return None
            if tipster.get("yield_pct", 0) < MIN_YIELD:
                return None

            # ── Analysis quality (heurystyka) ──
            tipster["analysis_quality"] = await self._assess_analysis_quality()

            return tipster

        except Exception as e:
            log(f"  Błąd scrapingu {url}: {e}", "ERROR")
            return None

    async def _extract_text(self, selector: str) -> Optional[str]:
        """Bezpiecznie wyciąga tekst z pierwszego pasującego elementu."""
        try:
            el = self.page.locator(selector).first
            if await el.count() > 0:
                text = (await el.inner_text()).strip()
                return text if text else None
        except Exception:
            pass
        return None

    async def _extract_main_stats(self) -> Dict:
        """Wyciąga PICKS, PROFIT, YIELD, FOLLOWERS z boxów na profilu."""
        stats = {"picks_count": 0, "profit_units": 0, "yield_pct": 0, "followers": 0}

        # Strategia: szukaj elementów z liczbami obok etykiet
        # Blogabet używa różnych struktur, więc szukamy elastycznie
        page_text = await self.page.content()

        # Picks
        picks_match = re.search(
            r'(?:picks|tips)[:\s]*</?\w[^>]*>\s*([\d,.\s]+)',
            page_text, re.IGNORECASE
        )
        if picks_match:
            stats["picks_count"] = int(parse_number(picks_match.group(1)))

        # Profit
        profit_match = re.search(
            r'(?:profit|units)[:\s]*</?\w[^>]*>\s*([+\-]?[\d,.\s]+)',
            page_text, re.IGNORECASE
        )
        if profit_match:
            stats["profit_units"] = parse_number(profit_match.group(1))

        # Yield
        yield_match = re.search(
            r'(?:yield|roi)[:\s]*</?\w[^>]*>\s*([+\-]?[\d,.\s]+)\s*%',
            page_text, re.IGNORECASE
        )
        if yield_match:
            stats["yield_pct"] = parse_number(yield_match.group(1))

        # Followers
        followers_match = re.search(
            r'(?:followers|follow)[:\s]*</?\w[^>]*>\s*([\d,.\s]+)',
            page_text, re.IGNORECASE
        )
        if followers_match:
            stats["followers"] = int(parse_number(followers_match.group(1)))

        # Fallback: szukaj w elementach z klasami
        try:
            stat_elements = await self.page.query_selector_all(
                ".stat-value, .blog-stat-value, .stats-number, [class*='stat'] [class*='value']"
            )
            values = []
            for el in stat_elements:
                text = (await el.inner_text()).strip()
                if text:
                    values.append(text)

            # Typowy układ: [picks_count, profit, yield%, followers]
            if len(values) >= 3 and stats["picks_count"] == 0:
                stats["picks_count"] = int(parse_number(values[0]))
                stats["profit_units"] = parse_number(values[1])
                stats["yield_pct"] = parse_number(values[2].rstrip("%"))
            if len(values) >= 4 and stats["followers"] == 0:
                stats["followers"] = int(parse_number(values[3]))
        except Exception:
            pass

        return stats

    async def _extract_box_stats(self) -> Dict:
        """Win rate, odds average, stake average z boxów statystyk."""
        stats = {"win_rate": 0, "odds_avg": 0, "avg_stake": 0}
        page_text = await self.page.content()

        wr_match = re.search(r'win\s*rate[:\s]*</?\w[^>]*>\s*([\d,.]+)\s*%', page_text, re.IGNORECASE)
        if wr_match:
            stats["win_rate"] = parse_number(wr_match.group(1))

        odds_match = re.search(r'odds?\s*(?:avg|average)[:\s]*</?\w[^>]*>\s*([\d,.]+)', page_text, re.IGNORECASE)
        if odds_match:
            stats["odds_avg"] = parse_number(odds_match.group(1))

        stake_match = re.search(r'stake\s*(?:avg|average)[:\s]*</?\w[^>]*>\s*([\d,.]+)', page_text, re.IGNORECASE)
        if stake_match:
            stats["avg_stake"] = parse_number(stake_match.group(1))

        return stats

    async def _detect_verification(self) -> str:
        """Wykryj status weryfikacji typera."""
        html = await self.page.content()

        # Paid tipster in Market
        if re.search(r'(paid\s*service|subscribe|buy|market)', html, re.IGNORECASE):
            # Check if copytip is available
            if re.search(r'(copytip|auto-?bet|copy\s*strategy)', html, re.IGNORECASE):
                return "paid_copytip"
            return "paid"

        # PRO checkmark
        try:
            verified = await self.page.query_selector_all(
                ".verified-icon, .checkmark, .pro-badge, [class*='verified'], [class*='pro-badge']"
            )
            if verified:
                return "pro"
        except Exception:
            pass

        # Green checkmark via title/tooltip
        if re.search(r'verified\s*(since|by|tipster)', html, re.IGNORECASE):
            return "pro"

        # Unverified badge
        if re.search(r'un-?verified', html, re.IGNORECASE):
            return "free"

        return "free"

    async def _detect_resets(self) -> int:
        """Wykryj liczbę resetów."""
        html = await self.page.content()
        reset_match = re.search(r'reset[s]?\s*[:\(]?\s*(\d+)', html, re.IGNORECASE)
        if reset_match:
            return int(reset_match.group(1))

        # Check for reset icon
        try:
            reset_icons = await self.page.query_selector_all(
                ".reset-icon, [class*='reset'], [title*='reset']"
            )
            if reset_icons:
                for icon in reset_icons:
                    title = await icon.get_attribute("title") or ""
                    nums = re.findall(r'\d+', title)
                    if nums:
                        return int(nums[0])
                return 1  # Has reset icon but can't determine count
        except Exception:
            pass
        return 0

    async def _extract_charts(self) -> Dict:
        """Wyciąga dane z wykresów Top Sports i Top Bookmakers."""
        result = {
            "top_sports": [],
            "sport_percentages": {},
            "top_bookmakers": [],
            "bookie_percentages": {}
        }

        html = await self.page.content()

        # Sport extraction (from chart labels, text, or structured data)
        sport_pattern = re.findall(
            r'(Football|Basketball|Tennis|Ice Hockey|Esports?|Handball|Volleyball|'
            r'Baseball|Am\.\s*Football|Boxing|MMA|Cricket|Darts|Futsal|Rugby|'
            r'Table Tennis|Badminton|Snooker|Golf|Motorsport)'
            r'[:\s]*(\d+(?:\.\d+)?)\s*%?',
            html, re.IGNORECASE
        )
        for sport, pct in sport_pattern:
            sport = sport.strip()
            if sport not in result["top_sports"]:
                result["top_sports"].append(sport)
                result["sport_percentages"][sport] = parse_number(pct)

        # Bookmaker extraction
        bookie_pattern = re.findall(
            r'(Pinnacle|Bet365|SBOBet|Dafabet|188bet|AsianConnect|'
            r'Betfair|Unibet|William\s*Hill|Ladbrokes|Bwin|Marathonbet|'
            r'1xBet|22bet|Betway|BetVictor|Betsson|Sportmarket|10Bet|Paddy\s*Power)'
            r'[:\s]*(\d+(?:\.\d+)?)\s*%?',
            html, re.IGNORECASE
        )
        for bookie, pct in bookie_pattern:
            bookie = bookie.strip()
            if bookie not in result["top_bookmakers"]:
                result["top_bookmakers"].append(bookie)
                result["bookie_percentages"][bookie] = parse_number(pct)

        return result

    async def _extract_followers(self) -> int:
        html = await self.page.content()
        m = re.search(r'(\d[\d,]*)\s*followers?', html, re.IGNORECASE)
        return int(parse_number(m.group(1))) if m else 0

    def _classify_specialization(self, sports: list, pcts: dict) -> str:
        if not sports:
            return "chaotic_multi"
        if len(sports) == 1:
            return "mono_specialist"
        top_pct = max(pcts.values()) if pcts else 0
        if top_pct >= 75:
            return "mono_specialist"
        elif top_pct >= 50 or len(sports) <= 3:
            return "focused_multi"
        return "chaotic_multi"

    def _classify_bookmaker(self, bookies: list, pcts: dict) -> str:
        asian = {"Pinnacle", "SBOBet", "Dafabet", "188bet", "AsianConnect", "Sportmarket"}
        asian_pct = sum(pcts.get(b, 0) for b in bookies if b in asian)
        total_pct = sum(pcts.values()) if pcts else 0

        if total_pct > 0 and (asian_pct / total_pct) >= 0.7:
            return "asian_dominant"
        elif total_pct > 0 and (asian_pct / total_pct) >= 0.3:
            return "mixed"
        # Check by name presence
        if any(b in asian for b in bookies[:2]):
            return "asian_dominant"
        return "soft_only"

    # ── STATISTICS PAGE ──────────────────────────────────────────────
    async def _scrape_statistics_page(self, profile_url: str) -> Dict:
        """Przejdź do podstrony Statistics i wyciągnij szczegóły."""
        result = {
            "recent_form_yield": 0,
            "live_pct": 0,
            "prematch_yield": None,
            "live_yield": None,
            "pinnacle_yield": None,
            "soft_bookie_yield": None,
            "avg_hours_before_match": 24,
            "months_active": 0,
            "profitable_months_12": 0,
            "top_leagues": [],
        }

        try:
            # Try to find and click Statistics tab/link
            stats_link = self.page.locator("a:has-text('Statistics'), a:has-text('Stats'), a[href*='stat']").first
            if await stats_link.count() > 0:
                await stats_link.click()
                await asyncio.sleep(2)

            html = await self.page.content()

            # Recent form: look for monthly breakdown
            # Try to find yield values in recent months
            month_yields = re.findall(
                r'(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\w*\s*\d{2,4}[:\s]*([+\-]?[\d,.]+)\s*%',
                html, re.IGNORECASE
            )
            if month_yields:
                recent = [parse_number(y) for y in month_yields[-3:]]
                result["recent_form_yield"] = sum(recent) / len(recent) if recent else 0
                positive_months = sum(1 for y in month_yields[-12:] if parse_number(y) > 0)
                result["profitable_months_12"] = positive_months
                result["months_active"] = len(month_yields)

            # Live percentage
            live_match = re.search(r'(?:in-?play|live)[:\s]*(\d+(?:\.\d+)?)\s*%', html, re.IGNORECASE)
            if live_match:
                result["live_pct"] = parse_number(live_match.group(1))

            # League extraction
            league_pattern = re.findall(
                r'(Eng\.\s*Premier|Spa\.\s*Primera|Ger\.\s*Bundesliga|Fra\.\s*Ligue|'
                r'Ita\.\s*Serie\s*[ABC]|NBA|NHL|Euroleague|Champions\s*L|'
                r'Eng\.\s*Championship|Spa\.\s*Segunda|Ger\.\s*Bundesliga\s*II|'
                r'MLS|KHL|SHL|WC\s*Qual|AFC|UEFA)',
                html, re.IGNORECASE
            )
            result["top_leagues"] = list(set(l.strip() for l in league_pattern))[:5]

        except Exception as e:
            log(f"    Stats page error: {e}", "WARN")

        return result

    async def _assess_analysis_quality(self) -> str:
        """Oceń jakość analiz na podstawie ostatnich tipów."""
        try:
            # Look at pick descriptions
            picks_text = await self.page.query_selector_all(
                ".pick-analysis, .pick-text, .pick-description, .tip-text, "
                ".analysis, [class*='analysis'], [class*='description']"
            )
            if not picks_text:
                return "none"

            total_len = 0
            count = 0
            for el in picks_text[:10]:
                text = (await el.inner_text()).strip()
                total_len += len(text)
                count += 1

            if count == 0:
                return "none"
            avg_len = total_len / count
            if avg_len > 200:
                return "detailed_value"
            elif avg_len > 50:
                return "short_desc"
            return "none"
        except Exception:
            return "none"

    # ── MAIN FLOW ────────────────────────────────────────────────────
    async def run(self):
        """Pełny pipeline: login → discover → scrape → save."""
        await self.start()

        try:
            # 1. Login
            await self.login()

            # 2. Discover tipster URLs
            await self.discover_tipsters()

            if not self.collected_urls:
                log("Nie znaleziono żadnych typerów — generuję dane przykładowe", "WARN")
                self._generate_fallback_data()
                save_json(RAW_FILE, self.tipsters)
                return self.tipsters

            # 3. Scrape each tipster profile
            log(f"\nScrapuję profile {len(self.collected_urls)} typerów...")
            for i, url in enumerate(sorted(self.collected_urls)):
                if len(self.tipsters) >= MAX_TIPSTERS:
                    break

                log(f"  [{i+1}/{len(self.collected_urls)}] {url}")
                tipster = await self.scrape_tipster_profile(url)

                if tipster:
                    self.tipsters.append(tipster)
                    log(f"    ✓ {tipster['name']}: yield={tipster.get('yield_pct',0):.1f}%, "
                        f"picks={tipster.get('picks_count',0)}, "
                        f"verification={tipster.get('verification','?')}")
                else:
                    log(f"    ✗ Pominięty (nie spełnia kryteriów)")

                # Rate limiting: 2-3 sec between requests
                await asyncio.sleep(2.5)

            # 4. Save
            save_json(RAW_FILE, self.tipsters)
            log(f"\n✓ Zapisano {len(self.tipsters)} typerów → {RAW_FILE}")
            return self.tipsters

        finally:
            await self.close()

    def _generate_fallback_data(self):
        """Dane fallback gdy scraping nie działa (np. brak loginu)."""
        log("Generuję dane przykładowe jako fallback...")
        self.tipsters = [
            {
                "name": "DEMO_SharpEdge",
                "url": f"{BASE_URL}/demo",
                "yield_pct": 8.4, "picks_count": 1247,
                "verification": "paid", "bookmaker_profile": "asian_dominant",
                "recent_form_yield": 6.2, "specialization": "mono_specialist",
                "resets": 0, "analysis_quality": "detailed_value",
                "top_sports": ["Football"], "top_leagues": ["Eng. Premier"],
                "avg_stake": 5.2, "odds_avg": 1.95, "win_rate": 54.2,
                "live_pct": 10, "avg_hours_before_match": 18,
                "followers": 342, "months_active": 24,
                "profitable_months_12": 8, "pinnacle_yield": 7.8,
                "soft_bookie_yield": 9.1, "live_yield": 3.2,
                "prematch_yield": 9.0, "sport_percentages": {"Football": 92},
                "bookie_percentages": {"Pinnacle": 85}, "top_bookmakers": ["Pinnacle"],
                "scraped_at": now_utc().isoformat(),
                "is_demo": True
            },
            {
                "name": "DEMO_IceHockey_Pro",
                "url": f"{BASE_URL}/demo2",
                "yield_pct": 7.1, "picks_count": 2103,
                "verification": "paid_copytip", "bookmaker_profile": "asian_dominant",
                "recent_form_yield": 5.8, "specialization": "mono_specialist",
                "resets": 0, "analysis_quality": "detailed_value",
                "top_sports": ["Ice Hockey"], "top_leagues": ["NHL", "KHL"],
                "avg_stake": 3.0, "odds_avg": 1.88, "win_rate": 56.1,
                "live_pct": 8, "avg_hours_before_match": 8,
                "followers": 512, "months_active": 36,
                "profitable_months_12": 9, "pinnacle_yield": 6.8,
                "soft_bookie_yield": 7.5, "live_yield": 5.0,
                "prematch_yield": 7.2, "sport_percentages": {"Ice Hockey": 95},
                "bookie_percentages": {"Pinnacle": 80, "Bet365": 15},
                "top_bookmakers": ["Pinnacle", "Bet365"],
                "scraped_at": now_utc().isoformat(),
                "is_demo": True
            },
            {
                "name": "DEMO_LiveKing_FRAUD",
                "url": f"{BASE_URL}/demo3",
                "yield_pct": 22.4, "picks_count": 456,
                "verification": "free", "bookmaker_profile": "soft_only",
                "recent_form_yield": 18.0, "specialization": "chaotic_multi",
                "resets": 3, "analysis_quality": "none",
                "top_sports": ["Football", "Tennis", "Basketball"],
                "top_leagues": ["Various"], "avg_stake": 8.5,
                "odds_avg": 2.80, "win_rate": 42.0, "live_pct": 65,
                "avg_hours_before_match": 0, "followers": 28,
                "months_active": 6, "profitable_months_12": 4,
                "pinnacle_yield": -3.5, "soft_bookie_yield": 25.0,
                "live_yield": 30.0, "prematch_yield": 4.0,
                "sport_percentages": {"Football": 40, "Tennis": 30, "Basketball": 30},
                "bookie_percentages": {"Bet365": 70, "Unibet": 30},
                "top_bookmakers": ["Bet365", "Unibet"],
                "scraped_at": now_utc().isoformat(),
                "is_demo": True
            }
        ]


# ─── ENTRY POINT ─────────────────────────────────────────────────────
async def main():
    scraper = BlogabetScraper()
    await scraper.run()


if __name__ == "__main__":
    asyncio.run(main())
