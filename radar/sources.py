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


def _doi_key(doi: str) -> str:
    doi = doi.strip().lower()
    doi = re.sub(r"^https?://(dx\.)?doi\.org/", "", doi)
    return re.sub(r"^doi:\s*", "", doi)


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
        abstract = clean(f.get("encoded") or f.get("description") or f.get("summary") or f.get("content"))
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


def polite_pause(seconds: float) -> None:
    if not net.FIXTURES:
        time.sleep(seconds)
