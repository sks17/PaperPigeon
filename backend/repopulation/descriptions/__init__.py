"""Phase 4 — grounded RAG node descriptions.

Pure transforms (`prompt.py`, `build_rows.py`) assemble the grounded prompt and turn a validated
`NodeDescription` into a persistable node update; main-thread code (`retrieve.py`, the sibling
`describe_run.py`) gathers evidence over Postgres + pgvector, calls the LLM, and writes the result.
See `../DESCRIPTIONS.md` for the contract.
"""
