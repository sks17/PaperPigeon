"""Pure parsers over scholarly-API response fixtures. Implemented by Cursor task P1-T04.

These parse SAVED JSON fixtures into internal dataclasses. They do NOT make HTTP requests,
hold API keys, or know about rate limits/auth — the main thread owns all live API integration
(OpenAlex/ROR clients) and feeds responses in. This keeps the "no API integration" wall intact.
"""
