"""Raw-payload store — every API response is persisted BEFORE transform (replayability + provenance).

Phase 1/2 use a local filesystem store (`.raw_cache/`, gitignored); an S3-backed impl drops in
later behind the same `RawStore` interface (the `raw_s3_key` provenance field already anticipates
it). The store also serves as a read-through cache, which is central to staying inside the OpenAlex
free budget: a re-run of the same seed hits the cache instead of re-billing the API.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Protocol


def cache_key(url: str, params: dict | None) -> str:
    """Stable key for a GET (url + sorted params). Used as the storage key / `raw_s3_key`."""
    canonical = url + "?" + json.dumps(params or {}, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


class RawStore(Protocol):
    def get(self, key: str) -> Any | None: ...
    def get_record(self, key: str) -> dict | None: ...
    def put(self, key: str, record: dict) -> str: ...


class LocalRawStore:
    """Filesystem RawStore. Stored record = {url, status, body, ...} (HTML records also carry
    etag / last_modified / content_hash for conditional re-fetching)."""

    def __init__(self, root: str | Path = ".raw_cache") -> None:
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    def _path(self, key: str) -> Path:
        return self.root / f"{key}.json"

    def get(self, key: str) -> Any | None:
        record = self.get_record(key)
        return record.get("body") if record is not None else None

    def get_record(self, key: str) -> dict | None:
        path = self._path(key)
        if not path.exists():
            return None
        return json.loads(path.read_text(encoding="utf-8"))

    def put(self, key: str, record: dict) -> str:
        self._path(key).write_text(
            json.dumps(record, ensure_ascii=False), encoding="utf-8"
        )
        return key
