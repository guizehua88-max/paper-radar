"""Bring papers over from the old FrontierRadar sites.

- from_sqlite(path): the old local site's database (D:\\Research\\FrontierRadar\\data\\*.sqlite3),
  read-only; also carries over 收藏 / 已读 / 笔记 from its user_state table.
- from_cloud(base): the old Cloudflare site's public API (/api/items, 50 per page, cursor paging).
Both return items in this project's format; __main__ reclassifies and merges them.
"""
from __future__ import annotations

import json
import re
import sqlite3
import urllib.parse
from pathlib import Path

from . import net
from .sources import PREPRINT_PREFIX, _doi_key, clean

VENUE_KEYS = ("venue", "journal", "container_title", "container-title", "container", "source_title",
              "publication", "journal_name", "host_venue", "source_name")


def _date(published: str | None, precision: str | None) -> str | None:
    d = (published or "").strip()
    if not re.match(r"^\d{4}", d):
        return None
    if precision == "year":
        return d[:4]
    if precision == "month":
        return d[:7]
    return d[:10]


def _venue(d: dict, obs_names: list[str]) -> str:
    for k in VENUE_KEYS:
        v = d.get(k)
        if isinstance(v, list):
            v = v[0] if v else ""
        if isinstance(v, dict):
            v = v.get("display_name") or v.get("name") or ""
        if isinstance(v, str) and v.strip():
            return clean(v)
    return obs_names[0] if obs_names else ""


def _kind(doi: str, d: dict) -> str:
    t = str(d.get("type") or d.get("providerType") or "").lower()
    if "posted" in t or "preprint" in t or doi.split("/")[0] in PREPRINT_PREFIX:
        return "preprint"
    return "journal"


def from_sqlite(path: str) -> tuple[list[dict], dict]:
    uri = Path(path).resolve().as_uri() + "?mode=ro"
    con = sqlite3.connect(uri, uri=True)
    con.row_factory = sqlite3.Row
    try:
        cols = {r[1] for r in con.execute("PRAGMA table_info(records)")}
        if not {"id", "data"} <= cols:
            raise RuntimeError(f"{path} 里没有 records 表，可能不是旧站数据库")
        state = {}
        try:
            for r in con.execute("SELECT record_id, read, favorite, note FROM user_state"):
                state[r["record_id"]] = dict(r)
        except sqlite3.Error:
            pass
        obs: dict[str, list[str]] = {}
        try:
            for r in con.execute("SELECT record_id, data FROM observations"):
                try:
                    nm = json.loads(r["data"]).get("source_name")
                except Exception:
                    nm = None
                if nm:
                    obs.setdefault(r["record_id"], []).append(nm)
        except sqlite3.Error:
            pass
        rows = con.execute("SELECT * FROM records").fetchall()
    finally:
        con.close()
    out = []
    stats = {"rows": len(rows), "user_state": len(state)}
    for r in rows:
        try:
            d = json.loads(r["data"])
        except Exception:
            continue
        title = clean(d.get("title"))
        if not title:
            continue
        raw_doi = d.get("doi") or (r["doi"] if "doi" in r.keys() else "") or ""
        doi = _doi_key(str(raw_doi))
        if doi and not re.match(r"^10\.\d{4,9}/\S+$", doi):
            doi = ""
        url = d.get("url") or (f"https://doi.org/{doi}" if doi else "")
        names = obs.get(r["id"], [])
        st = state.get(r["id"], {})
        published = d.get("published") or (r["published"] if "published" in r.keys() else None)
        precision = d.get("date_precision") or (r["date_precision"] if "date_precision" in r.keys() else None)
        authors = [clean(a if isinstance(a, str) else (a.get("name") or "")) for a in (d.get("authors") or [])][:30]
        out.append({
            "id": doi or f"url:{url}",
            "doi": doi,
            "title": title,
            "abstract": clean(d.get("abstract"))[:3000],
            "source": _venue(d, names),
            "date": _date(published, precision),
            "url": f"https://doi.org/{doi}" if doi else url,
            "authors": [a for a in authors if a],
            "kind": _kind(doi, d),
            "via": "旧站本地库",
            "origin": "旧站本地库",
            "first_seen": (r["first_seen"] if "first_seen" in r.keys() else None),
            "old_tier": r["tier"] if "tier" in r.keys() else None,
            "old_fav": bool(st.get("favorite")),
            "old_read": bool(st.get("read")),
            "old_note": (st.get("note") or "").strip()[:2000],
        })
    return out, stats


def from_cloud(base: str, max_pages: int = 400) -> tuple[list[dict], dict]:
    base = base.rstrip("/")
    out, cursor, pages, total = [], None, 0, None
    while pages < max_pages:
        q = {"limit": "50", "sort": "published"}
        if cursor:
            q["cursor"] = cursor
        data = net.get_json(f"{base}/api/items?" + urllib.parse.urlencode(q), timeout=60)
        pages += 1
        total = data.get("total", total)
        for it in data.get("items", []):
            if (it.get("contentType") or "paper") != "paper":
                continue  # 公众号推文等非论文内容不导入
            title = clean(it.get("title"))
            if not title:
                continue
            doi = _doi_key(it.get("doi") or "")
            pub = it.get("publication") or {}
            date = _date(pub.get("date") or it.get("publishedAt"), pub.get("precision"))
            src = clean(it.get("journal") or "")
            if not src:
                srcs = [s.get("name") for s in (it.get("sources") or []) if s.get("name")]
                src = srcs[0] if srcs else ""
            out.append({
                "id": doi or (f"arxiv:{it['arxivId']}" if it.get("arxivId") else f"url:{it.get('url')}"),
                "doi": doi,
                "title": title,
                "abstract": clean(it.get("abstract"))[:3000],
                "source": src,
                "date": date,
                "url": it.get("url") or (f"https://doi.org/{doi}" if doi else ""),
                "authors": [clean(a) for a in (it.get("authors") or [])][:30],
                "kind": "preprint" if "posted" in str(it.get("providerType", "")) else "journal",
                "via": "旧站云端",
                "origin": "旧站云端",
                "first_seen": it.get("firstSeen") or it.get("firstSeenAt"),
            })
        cursor = data.get("nextCursor")
        if not cursor:
            break
    return out, {"pages": pages, "total": total, "fetched": len(out)}


def from_export(path: str) -> tuple[list[dict], list[dict], dict]:
    """The compact export (legacy-export.json.gz) made from the old local databases:
    radar.sqlite3 records + research.sqlite3 公众号 documents. Returns (papers, wechat_posts, stats)."""
    import gzip
    opener = gzip.open if str(path).endswith(".gz") else open
    with opener(path, "rt", encoding="utf-8") as f:
        data = json.load(f)
    papers = []
    for r in data.get("records", []):
        title = clean(r.get("title"))
        if not title:
            continue
        doi = _doi_key(str(r.get("doi") or ""))
        if doi and not re.match(r"^10\.\d{4,9}/\S+$", doi):
            doi = ""
        url = r.get("url") or (f"https://doi.org/{doi}" if doi else "")
        if not doi and not url:
            continue
        typ = str(r.get("type") or "")
        papers.append({
            "id": doi or f"url:{url}",
            "doi": doi,
            "title": title,
            "abstract": clean(r.get("abstract"))[:3000],
            "source": clean(r.get("venue") or ""),
            "date": _date(r.get("published"), r.get("date_precision")),
            "url": f"https://doi.org/{doi}" if doi else url,
            "authors": [clean(a) for a in (r.get("authors") or []) if isinstance(a, str)][:30],
            "kind": "preprint" if ("posted" in typ or typ == "preprint") else "journal",
            "via": "旧站本地库",
            "first_seen": r.get("first_seen"),
            "old_tier": r.get("old_tier"),
        })
    posts = []
    for w in data.get("wechat", []):
        title = clean(w.get("title"))
        url = (w.get("url") or "").replace("http://", "https://", 1)
        if not title or not url:
            continue
        pub = (w.get("published") or "")[:10]
        posts.append({
            "id": "wx:" + (re.search(r"__biz=[^&]+&mid=\d+&idx=\d+", url.replace("%3D", "=")) or re.search(r".*", url)).group(0)[:120],
            "doi": "",
            "title": title,
            "abstract": clean((w.get("summary") or "") + " " + (w.get("body") or ""))[:2000],
            "source": w.get("account") or "微信公众号",
            "date": pub if re.match(r"^\d{4}-\d{2}-\d{2}$", pub) else None,
            "url": url,
            "authors": [],
            "kind": "wechat",
            "via": "旧站公众号库",
            "first_seen": w.get("first_seen"),
            "mentions": w.get("dois") or [],
        })
    return papers, posts, {"records": len(data.get("records", [])), "wechat": len(data.get("wechat", []))}
