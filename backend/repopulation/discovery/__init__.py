"""OpenAlex/ROR parsed dataclasses -> ImportRows. Implemented by Cursor (P2 build_rows task).

Pure: consumes already-parsed dataclasses (the main thread's live clients do all HTTP/auth) and
emits the same ImportRows shape the loader ingests. See DISCOVERY.md.
"""
