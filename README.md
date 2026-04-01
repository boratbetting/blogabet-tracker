# 🎯 Blogabet Tipster Tracker

**W pełni automatyczny** system analizy typerów z Blogabet.
Zero ręcznej roboty — scraping, analiza i dashboard aktualizują się codziennie.

## Jak to działa

```
GitHub Actions (codziennie 06:00 UTC)
│
├─ 1. scraper.py          ← loguje się na Blogabet, zbiera dane typerów
│     • odkrywa typerów z /tipsters (różne sortowania i filtry)
│     • scrapuje profil każdego typera (yield, picks, win rate, bookmaker, sport...)
│     • wykrywa status weryfikacji (PRO/Paid/Free)
│     • zapisuje → data/tipsters_raw.json
│
├─ 2. analyze.py           ← scoring 0–100 wg 7-fazowej metodologii
│     • yield (25 pkt) + picks (15) + weryfikacja (15) + bukmacher (10)
│     • forma 3M (10) + specjalizacja (10) + resety (5) + analizy (10)
│     • wykrywanie red flags (delay exploit, yield pump, soft-only edge...)
│     • klasyfikacja: early market / mature / live
│     • grade: A / B / C / D / F
│     • zapisuje → data/tipsters_scored.json + data/history.json
│
├─ 3. Dashboard HTML       ← generuje docs/index.html
│     • ranking, filtry, wykresy, red flags
│     • historia scoringu (90 dni)
│
└─ 4. Git commit + push    ← automatyczny deploy na GitHub Pages
      • strona dostępna: https://TWOJ_NICK.github.io/blogabet-tracker/
```

---

## Setup (10 minut, jednorazowo)

### 1. Fork / Stwórz repo

```bash
# Opcja A: Sklonuj i push
git clone <ten-projekt>
cd blogabet-tracker
# Zmień remote na swoje repo:
git remote set-url origin https://github.com/TWOJ_NICK/blogabet-tracker.git
git push -u origin main

# Opcja B: Wrzuć pliki ręcznie przez GitHub UI → "Upload files"
```

Repo musi być **publiczne** (GitHub Pages za darmo wymaga public repo).

### 2. Dodaj Secrets (credentials Blogabet)

W repo → **Settings** → **Secrets and variables** → **Actions** → **New repository secret**:

| Secret Name | Wartość |
|---|---|
| `BLOGABET_USER` | Twój login na Blogabet |
| `BLOGABET_PASS` | Twoje hasło na Blogabet |

> ⚠️ Bez tych secrets scraper wygeneruje dane demo.
> Z credentials — pełny automatyczny scraping.

### 3. (Opcjonalnie) Dostosuj parametry

W repo → **Settings** → **Secrets and variables** → **Actions** → **Variables** → **New repository variable**:

| Variable Name | Domyślnie | Opis |
|---|---|---|
| `MIN_PICKS` | `300` | Min. liczba tipów typera |
| `MIN_YIELD` | `1.0` | Min. yield % |
| `MAX_TIPSTERS` | `150` | Max typerów do analizy |

### 4. Włącz GitHub Pages

1. **Settings** → **Pages**
2. Source: **Deploy from a branch**
3. Branch: `main` / folder: `/docs`
4. **Save**

### 5. Włącz Actions permissions

1. **Settings** → **Actions** → **General**
2. Workflow permissions: **Read and write permissions** ✓
3. **Save**

### 6. Pierwszy test

1. Przejdź do **Actions** → **🎯 Daily Blogabet Scrape + Analysis**
2. Kliknij **Run workflow** → **Run workflow**
3. Poczekaj 5–15 minut
4. Dashboard: `https://TWOJ_NICK.github.io/blogabet-tracker/`

Od teraz system działa sam — codziennie o 06:00 UTC (08:00 CEST).

---

## Co system wykrywa (Red Flags)

| Flag | Opis | Jak wykrywa |
|---|---|---|
| `UNVERIFIED` | Konto FREE, samodzielne rozliczanie tipów | Brak checkmark PRO/Paid |
| `RESET_MULTIPLE` | ≥2 resety historii | Ikona/tooltip na profilu |
| `HIGH_STAKE` | Avg stake >15/10 | Parsowanie profilu |
| `EXTREME_YIELD` | >15% yield przy >1000 picks | Wymaga dodatkowej weryfikacji |
| `LIVE_UNVERIFIED` | >50% tipów live bez PRO statusu | Możliwy delay exploit |
| `LOW_ODDS` | Avg odds <1.30 | Farming niskich kursów |
| `YIELD_PUMP` | Live yield >20% + prematch <5% | Manipulacja yieldu stakeem |
| `SOFT_ONLY_EDGE` | Profit na Bet365 + strata na Pinnacle | Fałszywy edge |

**≥3 red flags → automatyczna degradacja do Grade F**

---

## Struktura plików

```
blogabet-tracker/
├── .github/workflows/
│   └── daily-update.yml      ← cron + workflow (jedyny plik do konfiguracji)
├── data/
│   ├── tipsters_raw.json     ← surowe dane ze scrapera (auto-generowane)
│   ├── tipsters_scored.json  ← wyniki analizy (auto-generowane)
│   └── history.json          ← historia 90 dni (auto-generowane)
├── docs/
│   └── index.html            ← dashboard (auto-generowany)
├── scraper.py                ← Playwright scraper
├── analyze.py                ← scoring + dashboard generator
├── pipeline.py               ← scraper + analyzer w jednym
├── requirements.txt
└── README.md
```

---

## Testowanie lokalne

```bash
pip install playwright beautifulsoup4 lxml
playwright install chromium

# Ustaw zmienne:
export BLOGABET_USER="twoj_login"
export BLOGABET_PASS="twoje_haslo"

# Uruchom pipeline:
python pipeline.py

# Lub osobno:
python scraper.py      # → data/tipsters_raw.json
python analyze.py      # → data/tipsters_scored.json + docs/index.html

# Otwórz dashboard:
open docs/index.html   # macOS
xdg-open docs/index.html  # Linux
```

---

## FAQ

**Czy scraper może zostać zablokowany?**
Blogabet ma ratelimiting. Scraper czeka 2.5s między requestami i używa standardowego User-Agent. Przy 150 typerach to ~6–7 minut. Nie powinno być problemu przy jednym uruchomieniu dziennie.

**Czy muszę cokolwiek klikać na co dzień?**
Nie. Po jednorazowym setupie (10 min) system działa sam. Ty tylko wchodzisz na stronę i patrzysz na ranking.

**Co jeśli Blogabet zmieni strukturę HTML?**
Scraper jest napisany z wieloma fallbackami i regexami. Ale jeśli Blogabet całkowicie przebuduje frontend, trzeba będzie zaktualizować selektory w `scraper.py`.

**Jak zmienić godzinę aktualizacji?**
Edytuj `cron: '0 6 * * *'` w `.github/workflows/daily-update.yml`. Format: `minuta godzina * * *` (UTC).

**Ile kosztuje?**
Zero. GitHub Actions free tier daje 2000 minut/miesiąc. Ten workflow zużywa ~10 minut/dzień = ~300 minut/miesiąc.
