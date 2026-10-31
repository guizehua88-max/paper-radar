"""Bounded repair of imprecise publication dates from exact Crossref DOI metadata."""
from __future__ import annotations

import datetime as dt
import re
import time
import urllib.parse
from collections.abc import Iterable

from . import net, sources

_PUBLICATION_FIELDS = ("published-online", "posted", "published-print", "issued")


def _metadata_date(value: object, today: dt.date) -> str | None:
    if not isinstance(value, dict):
        return None
    date_parts = value.get("date-parts")
    if (not isinstance(date_parts, list) or not date_parts or not isinstance(date_parts[0], list)
            or len(date_parts[0]) not in (2, 3) or any(type(part) is not int for part in date_parts[0])):
        return None
    return _precise_date(sources._date_parts(value), today)


def _precise_date(value: str | None, today: dt.date) -> str | None:
    """Validate the supplied precision; never fill in an unknown month or day."""
    if not value or not re.fullmatch(r"\d{4}-\d{2}(?:-\d{2})?", value):
        return None
    try:
        actual = dt.date.fromisoformat(value if len(value) == 10 else value + "-01")
    except ValueError:
        return None
    if (len(value) == 10 and actual > today) or (len(value) == 7 and value > today.isoformat()[:7]):
        return None
    return value


def _publication_date(item: dict, today: dt.date) -> tuple[str | None, str | None]:
    candidates = {
        field: _metadata_date(item.get(field, {}), today)
        for field in _PUBLICATION_FIELDS
    }
    # Created is only the existing best_date fallback. It must not replace a
    # real publication month merely because it contains a more precise day.
    publication = sorted(value for value in candidates.values() if value)
    created = _metadata_date(item.get("created", {}), today)
    chosen = sources.best_date(publication, None if publication else created)
    if not _precise_date(chosen, today):
        return None, None
    field = next((key for key, value in candidates.items() if value == chosen), "created")
    return chosen, f"Crossref {field}"


def fill_missing_dates(papers: Iterable[dict], *, now: dt.datetime | dt.date,
                       deadline: float, limit: int = 30) -> dict:
    """Update only empty/year-only dates; leave failed or unresolvable records intact.

    Query counts are unique DOIs. Updated counts are paper records (normally one
    per DOI). No push, title, first_seen, or identifier fields are changed.
    """
    today = now.date() if isinstance(now, dt.datetime) else now
    want: dict[str, list[dict]] = {}
    for paper in papers:
        current = str(paper.get("date") or "").strip()
        if paper.get("kind") == "wechat" or (current and not re.fullmatch(r"\d{4}", current)):
            continue
        doi = sources._doi_key(str(paper.get("doi") or paper.get("id") or ""))
        if doi.startswith("10."):
            want.setdefault(doi, []).append(paper)
    stats = {"eligible": len(want), "queried": 0, "updated": 0, "missing": 0,
             "failed": 0, "skipped": 0, "errors": [], "outcomes": {}}
    for doi, rows in list(want.items())[:max(0, limit)]:
        if time.time() >= deadline - 1:
            break
        stats["queried"] += 1
        url = "https://api.crossref.org/works/" + urllib.parse.quote(doi, safe="")
        try:
            response = net.get_json(url, timeout=sources._timeout(deadline, maximum=12), retries=1)
        except Exception as error:
            label = sources._error_label(error)
            if label == "HTTP 404":
                stats["missing"] += 1
                stats["outcomes"][doi] = "not_found"
                continue  # a DOI from another registration agency is not an outage
            stats["failed"] += 1
            stats["errors"].append(label)
            stats["outcomes"][doi] = "request_failed"
            break  # do not repeatedly query a failing or throttled source
        item = response.get("message") if isinstance(response, dict) else None
        if not isinstance(item, dict) or not isinstance(item.get("DOI"), str):
            stats["failed"] += 1
            stats["errors"].append("Crossref response format")
            stats["outcomes"][doi] = "invalid_response"
            break
        if sources._doi_key(item["DOI"]) != doi:
            stats["failed"] += 1
            stats["errors"].append("Crossref DOI identity mismatch")
            stats["outcomes"][doi] = "identity_mismatch"
            continue
        date, source = _publication_date(item, today)
        if date is None:
            stats["missing"] += 1
            stats["outcomes"][doi] = "no_precise_date"
            continue
        for paper in rows:
            paper.update(date=date, date_source=source, date_checked_at=today.isoformat())
            stats["updated"] += 1
        stats["outcomes"][doi] = "updated"
    stats["skipped"] = len(want) - stats["queried"]
    return stats
