"""Scraper subsystem: discovery/fetch/robots are main-thread (network); clean.py is a pure transform.

The fetch path is SSRF-bounded (clients/ssrf.validate_scrape_url) + robots-respecting + raw-storing.
clean.py turns raw HTML into readable text for the LLM extractor and stays pure (no network). See SCRAPING.md.
"""
