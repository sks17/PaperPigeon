"""Lab-scrape orchestration (main-thread). Discovers lab pages, fetches (SSRF+robots gated), cleans,
extracts (grounded LLM), reconciles into ImportRows, loads them into the SAME repopulation run (so
researchers + labs share one run/snapshot), and persists quarantined records for audit. Additive:
the run is not published here, so the default served graph is untouched.
"""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.repopulation.discovery.build_lab_rows import build_lab_rows
from backend.repopulation.extraction.extract_labs import extract_lab
from backend.repopulation.loader import load_import_rows
from backend.repopulation.models.membership import Quarantine
from backend.repopulation.models.nodes import Node, RepopulationRun
from backend.repopulation.scraping.clean import clean_html
from backend.repopulation.scraping.discovery import discover_lab_urls

SCRAPE_SOURCE_KEYS = {"scrape": "scrape"}


def run_lab_scrape(
    session: Session,
    *,
    repop_seed: dict,
    run_key: str,
    institution: dict,
    researcher_set: list[dict],
    homepage_url: str,
    allowed_domains: set[str],
    fetcher,
    llm,
    max_pages: int = 40,
    extra_seeds: tuple[str, ...] = (),
) -> dict:
    urls = discover_lab_urls(homepage_url, fetcher, allowed_domains=allowed_domains,
                             max_pages=max_pages, extra_seeds=extra_seeds)

    extractions: list[dict] = []
    for url in urls:
        record = fetcher.fetch(url)
        if record is None:
            continue
        page = clean_html(record["body"], url)
        extraction = extract_lab(page, llm)
        if extraction is None or not extraction.lab_name:
            continue
        extractions.append(
            {
                "extraction": extraction,
                "source_url": url,
                "raw_key": record.get("content_hash"),
                "anchor": extraction.source_anchor,
            }
        )

    # Legacy labs come from the already-loaded lab nodes in the DB (avoids importing the legacy
    # boto3-backed graph_core); build_lab_rows merges scraped labs onto these existing lab_ids.
    legacy_labs = [(n.id, n.name) for n in session.scalars(select(Node).where(Node.kind == "lab")).all()]
    result = build_lab_rows(
        extractions, institution, researcher_set, legacy_labs, run_key, SCRAPE_SOURCE_KEYS
    )
    accepted = result["accepted"]
    # Bind the lab rows to the EXISTING repopulation run (same seed → loader reuses that run).
    accepted["runs"] = [{"key": run_key, "seed": repop_seed, "status": "succeeded"}]

    counts = load_import_rows(session, accepted)

    run_id = session.scalar(select(RepopulationRun.id).where(RepopulationRun.seed == repop_seed))
    for item in result["quarantined"]:
        session.add(
            Quarantine(
                run_id=run_id, kind=item["kind"], payload=item["payload"], reason=item["reason"]
            )
        )
    session.commit()

    return {
        "run_id": run_id,
        "candidate_urls": len(urls),
        "labs_extracted": len(extractions),
        "lab_nodes_loaded": sum(1 for n in accepted["nodes"] if n["kind"] == "lab"),
        "quarantined": len(result["quarantined"]),
        "skipped_fetches": len(fetcher.skipped),
        "counts": counts,
    }
