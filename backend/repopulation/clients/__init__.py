"""Live external-API clients (main-thread integration code).

These wire OpenAlex / ROR / OpenRouter — the ONE tier allowed to do API integration. Lower tiers
(Cursor pure transforms) consume the parsed dataclasses these clients feed them and never import
this package. All clients share the polite, SSRF-bounded, raw-storing HttpClient in http.py.
"""
