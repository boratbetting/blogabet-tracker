#!/usr/bin/env python3
"""
Master pipeline: scraper → analyzer → dashboard.
Użyj tego do testowania lokalnie:
  python pipeline.py
"""
import asyncio
import subprocess
import sys

async def main():
    print("\n" + "═"*60)
    print("  BLOGABET TRACKER — FULL PIPELINE")
    print("═"*60)

    # 1. Scraper
    print("\n▶ FAZA 1: Scraping danych z Blogabet...\n")
    from scraper import BlogabetScraper
    scraper = BlogabetScraper()
    await scraper.run()

    # 2. Analyzer
    print("\n▶ FAZA 2: Analiza + scoring + dashboard...\n")
    from analyze import main as analyze_main
    analyze_main()

    print("\n" + "═"*60)
    print("  PIPELINE ZAKOŃCZONY")
    print("═"*60)

if __name__ == "__main__":
    asyncio.run(main())
