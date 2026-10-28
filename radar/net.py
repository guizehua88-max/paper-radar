"""HTTP helpers (stdlib only) with retry and an offline fixture mode for tests."""
from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

FIXTURES = os.environ.get("RADAR_FIXTURES")
_CONTACT = os.environ.get("CONTACT_EMAIL", "").strip()
UA = "PaperRadar/1.0 (+https://github.com)" + (f" mailto:{_CONTACT}" if _CONTACT else "")


class FetchError(RuntimeError):
    pass


def _fixture_for(url: str) -> bytes:
    """Map a URL to a fixture file so the whole pipeline can be tested offline."""
    base = Path(FIXTURES)
    host = urllib.parse.urlparse(url).netloc
    if "crossref" in host:
        q = urllib.parse.parse_qs(urllib.parse.urlparse(url).query).get("query.bibliographic", [""])[0]
        slug = "".join(c if c.isalnum() else "_" for c in q.lower())[:60]
        cand = [base / f"crossref_{slug}.json", base / "crossref.json"]
    elif "arxiv" in host:
        cand = [base / "arxiv.xml"]
    elif "openalex" in host:
        cand = [base / "openalex.json"]
    elif "/api/items" in url:
        cand = [base / "cloud.json"]
    else:
        slug = "".join(c if c.isalnum() else "_" for c in host)
        cand = [base / f"rss_{slug}.xml", base / "rss_missing.xml"]
    for p in cand:
        if p.exists():
            return p.read_bytes()
    raise FetchError(f"fixture missing for {url}")


def get(url: str, *, timeout: int = 40, retries: int = 3, accept: str = "*/*") -> bytes:
    if FIXTURES:
        return _fixture_for(url)
    last = None
    for attempt in range(retries):
        req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": accept})
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return r.read()
        except urllib.error.HTTPError as e:  # retry only on throttling / server errors
            last = f"HTTP {e.code}"
            if e.code not in (429, 500, 502, 503, 504):
                break
        except Exception as e:  # network hiccup
            last = f"{type(e).__name__}: {e}"
        time.sleep(3 * (attempt + 1))
    raise FetchError(f"{last} @ {url[:160]}")


def get_json(url: str, **kw):
    return json.loads(get(url, accept="application/json", **kw).decode("utf-8"))


def post_json(url: str, payload: dict, *, timeout: int = 30, headers: dict | None = None) -> dict:
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    h = {"Content-Type": "application/json; charset=utf-8", "User-Agent": UA}
    h.update(headers or {})
    req = urllib.request.Request(url, data=data, headers=h, method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as r:
        body = r.read().decode("utf-8", "replace")
    try:
        return json.loads(body)
    except json.JSONDecodeError:
        return {"raw": body[:500]}


def post_form(url: str, fields: dict, *, timeout: int = 30) -> dict:
    data = urllib.parse.urlencode(fields).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers={"User-Agent": UA}, method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as r:
        body = r.read().decode("utf-8", "replace")
    try:
        return json.loads(body)
    except json.JSONDecodeError:
        return {"raw": body[:500]}
