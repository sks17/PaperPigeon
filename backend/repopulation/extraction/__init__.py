"""Grounded, injection-safe lab extraction. lab_schema.py is pure (schema + validate); extract_labs.py
is main-thread glue around the live LLM call (no tools — the model output is data, never an action).
See SCRAPING.md §2.
"""
