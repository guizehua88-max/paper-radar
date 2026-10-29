"""Relevance = two rule sets working together.

1. Topic rules from config.toml (recall on 合成氨 / 焦耳热 / ALD + hard noise exclusion).
2. The old FrontierRadar classifier (radar/legacy_rules.py, rule version 2026-09-30.12), which
   was tuned on 40 hand-labelled cases and knows the six-tier taxonomy:
   cross 交叉 / direct 直接 / transferable 可迁移 / uncertain 待核 / background 背景 / exclude 排除,
   plus Chinese labels for 研究对象、供热方式、用途.

Merge policy (checked against the gold cases and real Crossref samples in tests/):
- old rules say direct/cross -> trust them (my rules can only upgrade direct -> cross)
- my rules hit -> my tier, upgraded to the old tier if that one is higher
- my rules explicitly reject (exclude list hit, or keyword without catalysis context) -> excluded
- otherwise accept old transferable only for catalytic processes, old background/uncertain only with topics
"""
from __future__ import annotations

import re
import unicodedata

from . import legacy_rules

TIER_RANK = {"exclude": 0, "background": 1, "uncertain": 2, "transferable": 3, "direct": 4, "cross": 5}
TIER_ZH = {"cross": "交叉", "direct": "直接", "transferable": "可迁移", "uncertain": "待核", "background": "背景", "exclude": "排除"}
TIER_SCORE = {"cross": 40, "direct": 25, "transferable": 15, "uncertain": 9, "background": 6, "exclude": 0}
LEGACY_TOPIC = {
    "ammonia_synthesis": "nh3", "ammonia_adjacent": "nh3", "ammonia_decomposition": "nh3",
    "joule": "joule", "joule_preparation": "joule", "joule_reaction": "joule",
    "programmable_heating_quenching": "joule", "ald": "ald",
}
CONTEXT_FLAGS = {  # old rules: ammonia appears only as context (teaching, supply chain, plant economics, biology, datasets...)
    "upstream_downstream_context_not_synthesis_result", "education_history_or_general_context",
    "plant_energy_economics_not_catalyst_performance", "biological_route_not_gas_n2_h2",
    "supporting_dataset_not_independent_paper", "background_route_mention_not_primary_target",
}
METHOD_TOPICS = {"joule", "joule_preparation", "joule_reaction", "programmable_heating_quenching", "ald"}
CATALYTIC_PROCESSES = {
    "ammonia_decomposition", "methane_dry_reforming", "reverse_water_gas_shift", "hydrogen_evolution",
    "other_catalytic_reaction", "general_catalysis", "gas_n2_h2_ammonia_synthesis", "ammonia_route_unspecified",
}

_CATAL = re.compile(r"catal|催化", re.IGNORECASE)
_NOT_A_PAPER = re.compile(r"^\s*(corrigendum|erratum|correction to|retraction|retracted|expression of concern|"
                          r"(inside |outside )?(front|back) cover|cover (picture|feature|image|art)|frontispiece|"
                          r"table of contents|graphical abstract)\b", re.IGNORECASE)
_CHARACTERIZATION = re.compile(r"表征温程|材料温程|电化学驱动|电场辅助|光驱动|等离子体驱动")
_DASHES = re.compile("[‐‑‒–—―−­]")  # ‐ ‑ ‒ – — ― − soft-hyphen -> "-"


def _rx(patterns):
    return [re.compile(p, re.IGNORECASE) for p in (patterns or [])]


def _journal_key(name: str) -> str:
    name = re.sub(r"\s*\((asap|in press)\)\s*$", "", name, flags=re.IGNORECASE)
    return re.sub(r"[^a-z0-9]", "", name.lower().replace("&", "and"))


def _norm_name(name: str) -> str:
    name = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z]", "", name.lower())


class Classifier:
    def __init__(self, cfg: dict):
        self.topics = []
        self.names = {}
        for t in cfg["topics"]:
            self.names[t["id"]] = t["name"]
            self.topics.append({
                "id": t["id"],
                "include": _rx(t.get("include")),
                "require": _rx(t.get("require_any")),
                "exclude": _rx(t.get("exclude")),
                "secondary": _rx(t.get("secondary")),
                "keep": _rx(t.get("title_keep")),
                "context": _rx(t.get("context")),
                "material": _rx(t.get("material_cues")),
            })
        wx = cfg.get("wechat", {})
        self.wx_cues = _rx(wx.get("research_cues"))
        self.wx_noise = _rx(wx.get("title_noise"))
        tj = cfg.get("top_journals") or cfg.get("settings", {}).get("top_journals", [])
        self.top_journals = {_journal_key(j) for j in tj}
        self.watch = {}
        for a in cfg.get("watch", {}).get("authors", []):
            parts = a.replace("-", " ").split()
            if not parts:
                continue
            given, family = " ".join(parts[:-1]), parts[-1]
            given_core = " ".join(p for p in given.split() if not re.fullmatch(r"[A-Za-z]\.?", p)) or given
            for g in {given, given_core}:
                self.watch[_norm_name(g + family)] = a
                self.watch[_norm_name(family + g)] = a

    # ---- my topic rules
    def _topic(self, t, text: str, title: str):
        """Returns (hit-dict | None, rejected: bool). rejected = keyword present but ruled out."""
        inc = next((m.group(0) for r in t["include"] if (m := r.search(text))), None)
        if not inc:
            return None, False
        in_title = any(r.search(title) for r in t["include"])
        if ((t["require"] and not any(r.search(text) for r in t["require"]))
                or any(r.search(text) for r in t["exclude"])):
            if any(r.search(title) for r in t["keep"]):  # the title itself says it is this method -> keep, as 背景
                return {"id": t["id"], "level": "keep", "in_title": True, "kw": inc}, False
            return None, True
        if t["id"] in ("joule", "ald") and not in_title and not _CATAL.search(text):
            return None, True  # method keyword only in the abstract, with no catalysis context -> incidental mention
        level = "secondary" if any(r.search(text) for r in t["secondary"]) else "core"
        if (level == "core" and t["context"] and any(r.search(title) for r in t["context"])
                and not any(r.search(title) for r in t["material"])):
            level = "context"  # plant / energy-system / control / economics paper: ammonia is the product, not the research object
        return {"id": t["id"], "level": level, "in_title": in_title, "kw": inc}, False

    def classify(self, p: dict) -> dict | None:
        title = _DASHES.sub("-", p.get("title", ""))
        abstract = _DASHES.sub("-", p.get("abstract", "") or "")
        text = f"{title} . {abstract}"
        if _NOT_A_PAPER.search(title):
            return None  # corrigenda, retractions, journal cover pages
        if p.get("kind") == "wechat":
            if any(r.search(title) for r in self.wx_noise):
                return None
            if not p.get("mentions") and not any(r.search(text) for r in self.wx_cues):
                return None
        hits, rejected = [], False
        for t in self.topics:
            h, rej = self._topic(t, text, title)
            if h:
                hits.append(h)
            rejected |= rej
        mine = {h["id"]: h for h in hits}

        nh3_core = "nh3" in mine and mine["nh3"]["level"] == "core"
        method = [k for k in ("joule", "ald") if k in mine and mine[k]["level"] != "keep"]
        kept_only = bool(mine) and all(h["level"] == "keep" for h in mine.values())
        if nh3_core and method:
            my_tier = "cross"
        elif nh3_core:
            my_tier = "direct"
        elif method:
            my_tier = "transferable"
        elif mine:
            my_tier = "background"
        else:
            my_tier = None

        try:
            old = legacy_rules.classify(title, abstract or None)
        except Exception:
            old = {"tier": "exclude", "topics": [], "process": "unknown"}
        old_tier = old.get("tier", "exclude")

        context_only = any(f in CONTEXT_FLAGS for f in old.get("flags", []))
        if my_tier in ("direct", "cross") and "nh3" in mine and not mine["nh3"]["in_title"] and old_tier not in ("direct", "cross"):
            # ammonia only mentioned in the abstract (e.g. as motivation) -> not a direct hit
            my_tier = "transferable" if method else "uncertain"
        if context_only and old_tier == "background":
            tier = "background"
        elif old_tier in ("direct", "cross"):
            tier = "cross" if (old_tier == "cross" or my_tier == "cross") else "direct"
        elif my_tier:
            old_method = (any(t in METHOD_TOPICS for t in old.get("topics", []))
                          and not _CHARACTERIZATION.search(old.get("heating_zh", "")))
            upgrade = old_tier == "transferable" and old_method and not kept_only
            tier = max(my_tier, old_tier, key=lambda x: TIER_RANK[x]) if upgrade else my_tier
        elif rejected:
            return None
        elif (old_tier == "transferable" and old.get("process") in CATALYTIC_PROCESSES
              and any(t in METHOD_TOPICS for t in old.get("topics", []))
              and not _CHARACTERIZATION.search(old.get("heating_zh", ""))
              and old.get("heating") in ("reaction_joule", "preparation_joule", "reaction_programmable")):
            tier = "transferable"
        elif old_tier in ("background", "uncertain") and any(t in LEGACY_TOPIC for t in old.get("topics", [])):
            tier = old_tier
        else:
            return None

        topics = list(mine)
        for lt in old.get("topics", []):
            if lt == "programmable_heating_quenching" and _CHARACTERIZATION.search(old.get("heating_zh", "")):
                continue
            tid = LEGACY_TOPIC.get(lt)
            if tid and tid not in topics:
                topics.append(tid)
        if not topics:
            topics = ["nh3"]  # legacy-only ammonia-adjacent items

        score = TIER_SCORE[tier] + sum(3 for h in hits if h["in_title"] and h["level"] != "keep")
        watch_hits = sorted({self.watch[k] for a in p.get("authors", []) if (k := _norm_name(a)) in self.watch})
        if watch_hits:
            score += 8
        top = _journal_key(p.get("source") or "") in self.top_journals
        if top:
            score += 4
        ss = old.get("structured_summary") or {}
        return {
            "topics": topics,
            "tier": tier,
            "cross": tier == "cross",
            "watch": watch_hits,
            "top": top,
            "score": score,
            "why": [h["kw"] for h in hits][:4],
            "zh_obj": ss.get("object", ""),
            "zh_method": ss.get("method", ""),
        }


def title_key(title: str) -> str:
    """Duplicate detector. Keeps CJK so Chinese titles don't collapse to a few ASCII letters."""
    k = re.sub(r"[^a-z0-9\u4e00-\u9fff]", "", _DASHES.sub("-", title).lower())[:140]
    return k if len(k) >= 12 else "short:" + title.strip().lower()
