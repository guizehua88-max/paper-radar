"""Data lives in data/papers/<YYYY-MM>.json, one file per *publication* month.

Sharding keeps git history small: a normal run only rewrites the few months that changed,
unchanged months produce byte-identical files (sorted keys/order), so git stores nothing new.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

ROOT = Path(os.environ.get("RADAR_ROOT", Path(__file__).resolve().parent.parent))
DATA = ROOT / "data"
SHARDS = DATA / "papers"
DOCS = ROOT / "docs"


def month_of(p: dict) -> str:
    d = (p.get("date") or "").strip()
    if len(d) >= 7 and d[:4].isdigit() and d[5:7].isdigit():
        return d[:7]
    if len(d) >= 4 and d[:4].isdigit():
        return f"{d[:4]}-00"  # year known, month unknown
    return "unknown"


def load(name: str, default):
    p = DATA / name
    if not p.exists():
        return default
    try:
        return json.loads(p.read_text("utf-8"))
    except json.JSONDecodeError:
        return default


def save(name: str, obj) -> None:
    DATA.mkdir(parents=True, exist_ok=True)
    _write(DATA / name, obj)


def _write(path: Path, obj) -> None:
    text = json.dumps(obj, ensure_ascii=False, indent=1, sort_keys=True)
    if path.exists() and path.read_text("utf-8") == text:
        return
    tmp = path.with_suffix(".tmp")
    tmp.write_text(text, "utf-8")
    tmp.replace(path)


def load_papers() -> dict:
    papers = {}
    if SHARDS.exists():
        for f in sorted(SHARDS.glob("*.json")):
            try:
                papers.update(json.loads(f.read_text("utf-8")))
            except json.JSONDecodeError:
                print(f"[store] skipped unreadable shard {f.name}")
    legacy = DATA / "papers.json"  # single-file layout from the first version
    if legacy.exists():
        try:
            for k, v in json.loads(legacy.read_text("utf-8")).items():
                papers.setdefault(k, v)
        except json.JSONDecodeError:
            pass
    return papers


def save_papers(papers: dict) -> None:
    SHARDS.mkdir(parents=True, exist_ok=True)
    by_month: dict[str, dict] = {}
    for k, p in papers.items():
        by_month.setdefault(month_of(p), {})[k] = p
    for month, items in by_month.items():
        _write(SHARDS / f"{month}.json", items)
    for f in SHARDS.glob("*.json"):
        if f.stem not in by_month:
            f.unlink()
    legacy = DATA / "papers.json"
    if legacy.exists():
        legacy.unlink()
