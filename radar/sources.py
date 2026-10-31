"""Collectors. Each returns a list of normalised paper dicts:
{id, doi, title, abstract, source, date, url, authors, kind, via}
"""
from __future__ import annotations

import datetime as dt
import html
import re
import time
import urllib.parse
import xml.etree.ElementTree as ET

from . import net

PREPRINT_PREFIX = {
    "10.26434": "ChemRxiv",
    "10.21203": "Research Square",
    "10.2139": "SSRN",
    "10.20944": "Preprints.org",
    "10.1101": "bioRxiv",
    "10.48550": "arXiv",
}
OK_TYPES = {"journal-article", "posted-content", "proceedings-article"}
CROSSREF_SELECT = (
    "DOI,title,container-title,created,issued,published-online,published-print,posted,"
    "type,author,abstract,URL,publisher,group-title"
)


def clean(text: str | None) -> str:
    if not text:
        return ""
    text = re.sub(r"<[^>]+>", " ", text)
    text = html.unescape(text)
    return re.sub(r"\s+", " ", text).strip()


def _date_parts(obj) -> str | None:
    """Keep the precision the publisher gave: YYYY-MM-DD, YYYY-MM or YYYY (never invent a day)."""
    try:
        parts = [int(x) for x in obj["date-parts"][0] if x is not None]
        if not parts:
            return None
        if len(parts) >= 3:
            return f"{parts[0]:04d}-{parts[1]:02d}-{parts[2]:02d}"
        if len(parts) == 2:
            return f"{parts[0]:04d}-{parts[1]:02d}"
        return f"{parts[0]:04d}"
    except Exception:
        return None


def good_date(d: str | None, today: str | None = None) -> bool:
    """Month precision or better, and not in the future (print-issue dates are often months ahead)."""
    today = today or dt.date.today().isoformat()
    return bool(d) and len(d) >= 7 and d[:7] <= today[:7]


def best_date(candidates: list[str | None], fallback: str | None) -> str | None:
    """Earliest publication date with at least month precision; else the DOI creation date."""
    ok = sorted(d for d in candidates if good_date(d))
    if ok:
        return ok[0]
    if good_date(fallback):
        return fallback
    return next((d for d in candidates if d), None) or fallback


ANGEW_INT = "Angewandte Chemie International Edition"


def _doi_key(doi: str) -> str:
    doi = doi.strip().lower()
    doi = re.sub(r"^https?://(dx\.)?doi\.org/", "", doi)
    doi = re.sub(r"^doi:\s*", "", doi)
    # Angewandte publishes every paper twice (German edition ange.X / international edition anie.X, same suffix):
    # keep the international edition so the two never show up as separate papers
    return re.sub(r"^10\.1002/ange\.", "10.1002/anie.", doi)


# ---------------------------------------------------------------- Crossref
def crossref(query: str, since: dt.date | None, rows: int, min_year: int,
             pub_from: str | None = None, pub_until: str | None = None) -> list[dict]:
    """since -> DOI created on/after (normal runs); pub_from/pub_until -> publication window (backfill)."""
    filt = []
    if since:
        filt.append(f"from-created-date:{since.isoformat()}")
    if pub_from:
        filt.append(f"from-pub-date:{pub_from}")
    if pub_until:
        filt.append(f"until-pub-date:{pub_until}")
    params = {"query.bibliographic": query, "filter": ",".join(filt), "rows": str(rows), "select": CROSSREF_SELECT}
    url = "https://api.crossref.org/works?" + urllib.parse.urlencode(params)
    try:
        data = net.get_json(url, timeout=60)
    except net.FetchError as e:
        if "HTTP 400" not in str(e):
            raise
        params.pop("select")  # field list rejected -> fall back to full records
        data = net.get_json("https://api.crossref.org/works?" + urllib.parse.urlencode(params), timeout=60)
    out = []
    for it in data.get("message", {}).get("items", []):
        doi = _doi_key(it.get("DOI", ""))
        if not doi or re.search(r"\.s\d{3,}$", doi):  # supporting-information components
            continue
        typ = it.get("type", "")
        if typ not in OK_TYPES:
            continue
        title = clean((it.get("title") or [""])[0])
        if not title:
            continue
        date = best_date([_date_parts(it.get(k, {})) for k in ("published-online", "posted", "published-print", "issued")],
                         _date_parts(it.get("created", {})))
        issued_year = int(((_date_parts(it.get("issued", {})) or date or "0000")[:4]) or 0)
        if issued_year and issued_year < min_year:
            continue  # old paper whose DOI record was (re)created recently
        prefix = doi.split("/")[0]
        if typ == "posted-content":
            source = PREPRINT_PREFIX.get(prefix, "预印本")
            kind = "preprint"
        else:
            source = clean((it.get("container-title") or [""])[0]) or clean(it.get("publisher", ""))
            kind = "journal"
            if doi.startswith("10.1002/anie."):
                source = ANGEW_INT
        authors = []
        for a in it.get("author", [])[:30]:
            name = " ".join(x for x in (a.get("given"), a.get("family")) if x) or a.get("name", "")
            if name:
                authors.append(clean(name))
        out.append({
            "id": doi,
            "doi": doi,
            "title": title,
            "abstract": clean(it.get("abstract")),
            "source": source,
            "date": date,
            "url": f"https://doi.org/{doi}",
            "authors": authors,
            "kind": kind,
            "via": "Crossref",
        })
    return out


# ---------------------------------------------------------------- arXiv
ATOM = "{http://www.w3.org/2005/Atom}"


def arxiv(query: str, since: dt.date) -> list[dict]:
    url = (
        "https://export.arxiv.org/api/query?search_query=" + urllib.parse.quote_plus(query)
        + "&sortBy=submittedDate&sortOrder=descending&max_results=50"
    )
    root = ET.fromstring(net.get(url))
    out = []
    for e in root.findall(f"{ATOM}entry"):
        pub = (e.findtext(f"{ATOM}published") or "")[:10]
        if pub and pub < since.isoformat():
            continue
        aid = (e.findtext(f"{ATOM}id") or "").rsplit("/abs/", 1)[-1]
        aid_base = re.sub(r"v\d+$", "", aid)
        doi = ""
        for child in e:
            if child.tag.endswith("}doi"):
                doi = _doi_key(child.text or "")
        out.append({
            "id": doi or f"arxiv:{aid_base}",
            "doi": doi,
            "title": clean(e.findtext(f"{ATOM}title")),
            "abstract": clean(e.findtext(f"{ATOM}summary")),
            "source": "arXiv",
            "date": pub or None,
            "url": f"https://arxiv.org/abs/{aid_base}",
            "authors": [clean(a.findtext(f"{ATOM}name")) for a in e.findall(f"{ATOM}author")][:30],
            "kind": "preprint",
            "via": "arXiv",
        })
    return out


# ---------------------------------------------------------------- OpenAlex (optional, needs free API key)
def _undo_inverted(inv: dict | None) -> str:
    if not inv:
        return ""
    pos = {}
    for word, idxs in inv.items():
        for i in idxs:
            pos[i] = word
    return " ".join(pos[i] for i in sorted(pos))


def openalex(query: str, since: dt.date, api_key: str) -> list[dict]:
    q = query.replace(",", " ")
    params = {
        "filter": f"title_and_abstract.search:{q},from_publication_date:{since.isoformat()}",
        "per_page": "100",
        "select": "id,doi,display_name,publication_date,primary_location,authorships,abstract_inverted_index,type",
        "api_key": api_key,
    }
    data = net.get_json("https://api.openalex.org/works?" + urllib.parse.urlencode(params))
    out = []
    for w in data.get("results", []):
        doi = _doi_key(w.get("doi") or "")
        title = clean(w.get("display_name"))
        if not title:
            continue
        loc = w.get("primary_location") or {}
        src = ((loc.get("source") or {}).get("display_name")) or ""
        kind = "preprint" if w.get("type") in ("preprint", "posted-content") else "journal"
        out.append({
            "id": doi or w.get("id", ""),
            "doi": doi,
            "title": title,
            "abstract": clean(_undo_inverted(w.get("abstract_inverted_index"))),
            "source": src,
            "date": w.get("publication_date"),
            "url": f"https://doi.org/{doi}" if doi else (loc.get("landing_page_url") or w.get("id")),
            "authors": [((a.get("author") or {}).get("display_name") or "") for a in (w.get("authorships") or [])][:30],
            "kind": kind,
            "via": "OpenAlex",
        })
    return out


# ---------------------------------------------------------------- RSS / Atom / RDF (journals, or any RSS you add)
def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1].lower()


def _parse_date(s: str | None) -> str | None:
    if not s:
        return None
    s = s.strip()
    m = re.match(r"(\d{4})-(\d{2})-(\d{2})", s)
    if m:
        return m.group(0)
    try:
        from email.utils import parsedate_to_datetime
        return parsedate_to_datetime(s).date().isoformat()
    except Exception:
        return None


_RSS_PREFIX = re.compile(
    r"^(.{0,90}?(Published online|Volume \d+, Issue)[^;]{0,60};\s*(<?a[^>]*>)?\s*doi:\s*\S+\s*"   # Nature: "Nature Catalysis, Published online: 28 October 2026; doi:10.1038/..."
    r"|.{0,90}?Page \w+-?\w*, \w+ \d+\.?\s*"                                              # Science / Wiley issue lines
    r"|Publication date:.{0,80}?Source:.{0,120}?Author\(s\):[^.]{0,400}\.?\s*)",              # ScienceDirect
    re.IGNORECASE)


def rss_abstract(text: str, title: str) -> str:
    """Strip feed boilerplate ("Journal, Published online: …; doi:…") and drop it if nothing but the title is left."""
    text = _RSS_PREFIX.sub("", text or "", count=1).strip()
    if text.lower().startswith(title.lower()[:60]) and len(text) < len(title) + 40:
        return ""
    return text if len(text) >= 20 else ""


def rss(name: str, url: str) -> list[dict]:
    root = ET.fromstring(net.get(url, accept="application/rss+xml, application/xml, text/xml, */*"))
    out = []
    for node in root.iter():
        if _local(node.tag) not in ("item", "entry"):
            continue
        f: dict[str, str] = {}
        link = ""
        for c in node:
            ln = _local(c.tag)
            if ln == "link":
                link = link or (c.get("href") or (c.text or "")).strip()
            elif ln not in f and (c.text or "").strip():
                f[ln] = c.text.strip()
        link = link or node.get("{http://www.w3.org/1999/02/22-rdf-syntax-ns#}about", "")
        doi = ""
        for key in ("doi", "identifier"):
            v = f.get(key, "")
            if "10." in v:
                doi = _doi_key(v[v.index("10."):])
                break
        if not doi:
            m = re.search(r"(10\.\d{4,9}/[^\s?#\"<>]+)", link)
            if m:
                doi = _doi_key(m.group(1))
        title = clean(f.get("title"))
        if not title:
            continue
        abstract = rss_abstract(clean(f.get("encoded") or f.get("description") or f.get("summary") or f.get("content")), title)
        authors = [a.strip() for a in re.split(r",|;| and ", clean(f.get("creator", ""))) if a.strip()][:30]
        out.append({
            "id": doi or f"url:{link}",
            "doi": doi,
            "title": title,
            "abstract": abstract[:3000],
            "source": name,
            "date": _parse_date(f.get("date") or f.get("pubdate") or f.get("published") or f.get("updated")),
            "url": f"https://doi.org/{doi}" if doi else link,
            "authors": authors,
            "kind": "journal",
            "via": f"RSS·{name}",
        })
    return out


# ---------------------------------------------------------------- abstracts for records that came without one
# Crossref only has an abstract when the publisher deposits it (Elsevier, ACS and Springer Nature often don't).
# These free services fill some of the gaps; none needs a key (OpenAlex is used only if OPENALEX_API_KEY is set).
def _strip_abstract(text: str) -> str:
    text = clean(text)
    return re.sub(r"^(abstract|summary)[\s:.\-–]+", "", text, flags=re.IGNORECASE).strip()


def _error_label(error: Exception) -> str:
    # Provider URLs can carry API keys. Never persist an exception URL/body.
    match = re.search(r"HTTP\s+(\d{3})", str(error))
    return "HTTP " + match.group(1) if match else type(error).__name__


def _timeout(deadline: float | None, maximum: int = 30) -> int:
    return max(1, min(maximum, int(deadline - time.time()))) if deadline else maximum


def _chunks(dois: list[str], size: int, fn, errs: list[str], *, source: str,
            progress: dict | None = None, deadline: float | None = None) -> None:
    """Check the deadline per batch; after a source error continue with other providers.

    Only requests actually made are recorded. A successful empty response is
    different from a timeout, a skipped cap, or a deadline that prevented a call.
    """
    for i in range(0, len(dois), size):
        if deadline and time.time() >= deadline - 1:
            break
        chunk = dois[i:i + size]
        for d in chunk:
            if progress is not None:
                progress.setdefault(d, {"attempted": [], "checked": [], "failed": []})["attempted"].append(source)
        try:
            fn(chunk)
        except Exception as e:
            errs.append(_error_label(e))
            for d in chunk:
                if progress is not None:
                    progress[d]["failed"].append(source)
            break  # no repeated batches against a failing/rate-limited source
        else:
            for d in chunk:
                if progress is not None:
                    progress[d]["checked"].append(source)


def abstracts_semantic_scholar(dois: list[str], errs: list[str], *, progress=None, deadline=None) -> dict[str, str]:
    out: dict[str, str] = {}

    def one(chunk):
        res = net.post_json_retry("https://api.semanticscholar.org/graph/v1/paper/batch?fields=abstract,externalIds",
                                  {"ids": [f"DOI:{d}" for d in chunk]}, timeout=_timeout(deadline), retries=1)
        if not isinstance(res, list) or len(res) != len(chunk):
            raise net.FetchError("Unexpected Semantic Scholar batch shape")
        got = {}
        for r in res:
            if r is None:
                continue
            if not isinstance(r, dict):
                raise net.FetchError("Unexpected Semantic Scholar record")
            ab = _strip_abstract(r.get("abstract") or "")
            if len(ab) < 150:
                continue
            # Do not attach by response position. Exact DOI identity is required,
            # including when an upstream batch reorders or omits records.
            d = _doi_key((r.get("externalIds") or {}).get("DOI") or "")
            if d not in chunk:
                raise net.FetchError("Semantic Scholar abstract identity mismatch")
            got[d] = ab
        out.update(got)
        polite_pause(1)
    _chunks(dois, 100, one, errs, source="Semantic Scholar", progress=progress, deadline=deadline)
    return out


def abstracts_europepmc(dois: list[str], errs: list[str], *, progress=None, deadline=None) -> dict[str, str]:
    out: dict[str, str] = {}

    def one(chunk):
        q = " OR ".join(f'DOI:"{d}"' for d in chunk)
        url = ("https://www.ebi.ac.uk/europepmc/webservices/rest/search?"
               + urllib.parse.urlencode({"query": q, "resultType": "core", "format": "json", "pageSize": "100"}))
        data = net.get_json(url, timeout=_timeout(deadline), retries=1)
        if not isinstance(data, dict) or not isinstance((data.get("resultList") or {}).get("result"), list):
            raise net.FetchError("Unexpected Europe PMC response")
        for r in data["resultList"]["result"]:
            d = _doi_key(r.get("doi") or "")
            ab = _strip_abstract(r.get("abstractText") or "")
            if d in chunk and len(ab) >= 150:
                out[d] = ab
        polite_pause(0.4)
    _chunks(dois, 20, one, errs, source="Europe PMC", progress=progress, deadline=deadline)
    return out


def abstracts_openalex(dois: list[str], api_key: str, errs: list[str], *, progress=None, deadline=None) -> dict[str, str]:
    out: dict[str, str] = {}

    def one(chunk):
        params = {"filter": "doi:" + "|".join(chunk), "select": "doi,abstract_inverted_index", "per_page": "50",
                  "api_key": api_key}
        data = net.get_json("https://api.openalex.org/works?" + urllib.parse.urlencode(params), timeout=_timeout(deadline), retries=1)
        if not isinstance(data, dict) or not isinstance(data.get("results"), list):
            raise net.FetchError("Unexpected OpenAlex response")
        for w in data["results"]:
            d = _doi_key(w.get("doi") or "")
            ab = _strip_abstract(_undo_inverted(w.get("abstract_inverted_index")))
            if d in chunk and len(ab) >= 150:
                out[d] = ab
        polite_pause(0.3)
    _chunks(dois, 50, one, errs, source="OpenAlex", progress=progress, deadline=deadline)
    return out


def abstracts_crossref(dois: list[str], errs: list[str], *, progress=None, deadline=None) -> dict[str, str]:
    """Bounded refresh of exact DOI metadata, useful when a deposit later gains an abstract."""
    out = {}
    def one(chunk):
        d = chunk[0]
        url = "https://api.crossref.org/works/" + urllib.parse.quote(d, safe="")
        try:
            data = net.get_json(url, timeout=_timeout(deadline), retries=1)
        except net.FetchError as e:
            if "HTTP 404" in str(e):
                return  # a DOI from another registration agency is a checked miss
            raise
        item = data.get("message") if isinstance(data, dict) else None
        if not isinstance(item, dict) or _doi_key(item.get("DOI") or "") != d:
            raise net.FetchError("Crossref singleton identity mismatch")
        ab = _strip_abstract(item.get("abstract") or "")
        if len(ab) >= 150:
            out[d] = ab
        polite_pause(0.3)
    _chunks(dois, 1, one, errs, source="Crossref", progress=progress, deadline=deadline)
    return out


def find_abstracts(dois: list[str], *, epmc_cap: int = 300, deadline: float | None = None,
                   progress: dict | None = None, crossref_cap: int = 20,
                   crossref_checked_at: dict[str, str] | None = None) -> tuple[dict[str, str], dict]:
    """Exact-DOI free metadata fallback with bounded work and per-DOI outcome evidence.

    Optional progress[d] contains attempted/checked/failed source names, complete,
    and found_by. An untouched DOI has no entry. Public stats remain strings for
    existing UI callers. The caller decides when successful misses should retry.
    """
    import os
    todo = [d for d in dict.fromkeys(_doi_key(d) for d in dois) if d.startswith("10.")]
    found: dict[str, str] = {}
    stats: dict[str, str] = {}
    evidence = progress if progress is not None else {}
    steps = [("Semantic Scholar", abstracts_semantic_scholar, None), ("Europe PMC", abstracts_europepmc, epmc_cap)]
    key = os.environ.get("OPENALEX_API_KEY", "").strip()
    if key:
        steps.insert(1, ("OpenAlex", lambda ds, errs, **kw: abstracts_openalex(ds, key, errs, **kw), None))
    if crossref_cap:
        steps.append(("Crossref", abstracts_crossref, crossref_cap))
    expected = {name for name, _, _ in steps}
    for index, (name, fn, cap) in enumerate(steps):
        rest = [d for d in todo if d not in found]
        if name == "Crossref" and crossref_checked_at:
            # Stable sorting keeps input order for equal dates. The bounded
            # singleton fallback must rotate beyond the same first 20 DOIs.
            rest.sort(key=lambda d: crossref_checked_at.get(d) or "")
        rest = rest[:cap if cap is not None else None]
        if not rest:
            continue
        if deadline and time.time() >= deadline - 1:
            stats[name] = "跳过（时间用完）"
            continue
        # Reserve a fair share for later independent providers. A slow/rate-
        # limited first source must not consume the whole enrichment window.
        source_deadline = deadline
        if deadline:
            now = time.time()
            remaining_sources = sum(next_cap is None or next_cap > 0 for _, _, next_cap in steps[index:])
            source_deadline = now + (deadline - now) / max(1, remaining_sources)
        errs: list[str] = []
        got = fn(rest, errs, progress=evidence, deadline=source_deadline)
        found.update(got)
        for d in got:
            evidence[d]["found_by"] = name
        attempted = sum(name in evidence.get(d, {}).get("attempted", []) for d in rest)
        stats[name] = f"查 {attempted} 得 {len(got)}"
        if attempted < len(rest):
            stats[name] += f"（未请求 {len(rest) - attempted}）"
        if errs:
            stats[name] += f"（{len(errs)} 批请求失败：{errs[-1]}）"
    for d in todo:
        if d in evidence:
            evidence[d]["complete"] = bool(evidence[d].get("found_by")) or expected.issubset(evidence[d]["checked"])
    return found, stats

def polite_pause(seconds: float) -> None:
    if not net.FIXTURES:
        time.sleep(seconds)
