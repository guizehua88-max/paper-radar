"""文献雷达 entry point.

  python -m radar                         日常运行：查新 -> 筛选 -> 保存 -> 网页/RSS -> 微信推送
  python -m radar --backfill 2024-01      按发表月份回填（可中断，再运行会接着做）
  python -m radar --import-sqlite 旧库.sqlite3   导入旧本地站数据库（含收藏/已读/笔记）
  python -m radar --import-cloud https://paperresearch.de5.net   导入旧云端站
  python -m radar --render-only           只重新生成网页
  python -m radar --maintain              补摘要、写中文简介（有 ZHIPU_API_KEY 时）
"""
from __future__ import annotations

import argparse
import calendar
import datetime as dt
import json
import os
import sys
import time
import tomllib

from . import legacy_import, llm, net, push, site, sources, store
from .filters import TIER_RANK, TIER_ZH, Classifier, title_key


# Bump this when a rule fix means old candidates were wrongly rejected: the whole archive is searched again once
# (spread over several runs). 2026-10-29: exclusion words were matching inside other words ("cement" in "enhancement").
REBACKFILL_ID = "2026-10-29"


def net_offline() -> bool:
    return bool(net.FIXTURES) and not os.environ.get("RADAR_FIXTURE_ENRICH")


def now_utc() -> dt.datetime:
    fixed = os.environ.get("RADAR_NOW")
    if fixed:
        return dt.datetime.fromisoformat(fixed.replace("Z", "+00:00"))
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0)


def iso(t: dt.datetime) -> str:
    return t.astimezone(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _valid_iso(s) -> str | None:
    try:
        t = dt.datetime.fromisoformat(str(s).strip().replace("Z", "+00:00"))
        if t.tzinfo is None:
            t = t.replace(tzinfo=dt.timezone.utc)
        return iso(t)
    except Exception:
        return None


class Radar:
    def __init__(self):
        self.cfg = tomllib.loads((store.ROOT / "config.toml").read_text("utf-8"))
        self.S = self.cfg["settings"]
        self.now = now_utc()
        self.papers: dict = store.load_papers()
        self.state: dict = store.load("state.json", {})
        self.clf = Classifier(self.cfg)
        push.TOPIC_NAMES.update({t["id"]: t["name"] for t in self.cfg["topics"]})
        self.status = {"sources": {}}
        self.found: list[dict] = []

    # ------------------------------------------------------------ helpers
    def record(self, name: str, fn):
        st = self.status["sources"].setdefault(name, {"n": 0, "err": "", "calls": 0, "fails": 0})
        st["calls"] += 1
        try:
            got = fn()
            st["n"] += len(got)
            self.found.extend(got)
        except Exception as e:
            st["fails"] += 1
            st["err"] = f"{type(e).__name__}: {str(e)[:140]}"
            print(f"  ! {name}: {st['err']}")

    def settle_status(self):
        for st in self.status["sources"].values():
            if st["fails"] < st["calls"]:
                st["err"] = "" if st["fails"] == 0 else f"{st['fails']}/{st['calls']} 次请求失败（其余正常）"

    def ingest(self, items: list[dict], *, keep_first_seen=False, min_month: str | None = None,
               origin: str | None = None, min_year: int | None = None, refresh_dates: bool = False) -> list[dict]:
        seen_titles = {title_key(p["title"]): pid for pid, p in self.papers.items()}
        new = []
        for p in items:
            pid = p.get("id")
            if not pid or not p.get("title"):
                continue
            tk = title_key(p["title"])
            if pid in self.papers or tk in seen_titles:
                old_id = pid if pid in self.papers else seen_titles.get(tk, "")
                old = self.papers.get(old_id)
                if old is not None:  # carry over old-site favourites/notes onto an existing record
                    for k in ("old_fav", "old_read", "old_note"):
                        if p.get(k) and not old.get(k):
                            old[k] = p[k]
                    if refresh_dates and sources.good_date(p.get("date")) and not sources.good_date(old.get("date")):
                        old["date"] = p["date"]
                    if not llm.usable_abstract(old) and llm.usable_abstract(p):
                        old["abstract"] = p["abstract"]
                    if (old_id != pid and old.get("kind") == "preprint" and p.get("kind") == "journal"
                            and str(pid).startswith("10.") and pid not in self.papers):
                        seen_titles[tk] = self.promote(old_id, p)
                continue
            d = p.get("date") or ""
            if min_month and d and d[:7] < min_month and not (len(d) == 4 and d >= min_month[:4]):
                continue
            if min_year and d[:4].isdigit() and int(d[:4]) < min_year:
                continue
            c = self.clf.classify(p)
            if not c:
                continue
            p.update(c)
            fs = _valid_iso(p.get("first_seen")) if keep_first_seen else None
            p["first_seen"] = fs or iso(self.now)
            if origin:
                p["origin"] = origin
            seen_titles[tk] = pid
            self.papers[pid] = p
            new.append(p)
        return new

    def promote(self, old_id: str, p: dict) -> str:
        """A preprint we already have came out in a journal: keep one record, pointing at the journal version."""
        rec = self.papers.pop(old_id)
        rec["preprint"] = {"id": old_id, "source": rec.get("source", ""), "url": rec.get("url", "")}
        for k in ("id", "doi", "url", "source", "date", "kind", "via"):
            if p.get(k):
                rec[k] = p[k]
        if p.get("authors"):
            rec["authors"] = p["authors"]
        if len(p.get("abstract") or "") > len(rec.get("abstract") or ""):
            rec["abstract"] = p["abstract"]
        c = self.clf.classify(rec)
        if c:
            rec.update(c)
        rec["published_seen"] = iso(self.now)
        self.papers[rec["id"]] = rec
        return rec["id"]

    def fill_abstracts(self, items: list[dict], deadline: float, *, epmc_cap: int = 300, label: str = "补摘要") -> dict:
        """Look up abstracts for papers that came without one (Semantic Scholar / Europe PMC / OpenAlex)."""
        want = {}
        for p in items:
            doi = str(p.get("doi") or p.get("id") or "")
            if p.get("kind") != "wechat" and doi.startswith("10.") and not llm.usable_abstract(p):
                want.setdefault(doi, []).append(p)
        if not want or net_offline():
            return {"查询": 0}
        found, st = sources.find_abstracts(list(want), epmc_cap=epmc_cap, deadline=deadline)
        for doi, ab in found.items():
            for p in want.get(doi, []):
                p["abstract"] = ab
        bad = [k for k, v in st.items() if "失败" in v]
        self.status["sources"][label] = {"n": len(found), "calls": 1, "fails": 0,
                                         "err": ("部分请求失败（其余正常）：" + "、".join(bad)) if bad else ""}
        print(f"  abstracts: asked {len(want)} found {len(found)} {st}")
        return {"查询": len(want), "找到": len(found), **st}

    def brief(self, items: list[dict], deadline: float) -> dict:
        st = llm.summarize(items, deadline=deadline)
        if st["done"] or st["err"]:
            print(f"  中文简介: {st}")
        return st

    def ordered(self):
        return sorted(self.papers.values(),
                      key=lambda p: (p["first_seen"], TIER_RANK.get(p.get("tier", "exclude"), 0), p.get("score", 0)),
                      reverse=True)

    def finish(self, results=None, extra=None):
        cutoff_month = self.S.get("archive_from", "2024-01")
        def keep(v):
            mo = store.month_of(v)
            return mo == "unknown" or mo >= cutoff_month or (mo.endswith("-00") and mo[:4] >= cutoff_month[:4])
        self.papers = {k: v for k, v in self.papers.items() if keep(v)}
        self.status.update(extra or {})
        self.status["push"] = results or []
        self.status["at"] = iso(self.now)
        status = self.status
        if (extra or {}).get("模式") == "日常":
            self.state["last_daily_status"] = self.status
        elif self.state.get("last_daily_status"):  # page footer keeps the last daily run's source table
            status = dict(self.state["last_daily_status"])
            status["补充运行"] = "；".join(f"{k} {v}" for k, v in (extra or {}).items() if k != "模式") or "—"
        store.save_papers(self.papers)
        store.save("state.json", self.state)
        site.render(self.ordered(), self.cfg, status, iso(self.now))
        summary = os.environ.get("GITHUB_STEP_SUMMARY")
        if summary:
            with open(summary, "a", encoding="utf-8") as f:
                f.write(f"### 文献雷达 {iso(self.now)}\n\n")
                for k, v in (extra or {}).items():
                    f.write(f"- {k}: {v}\n")
                f.write(f"- 推送: {results or '无'}\n- 库内共 {len(self.papers)} 篇\n\n")
                if self.status["sources"]:
                    f.write("| 来源 | 状态 | 条数 |\n|---|---|---|\n")
                    for k, v in self.status["sources"].items():
                        f.write(f"| {k} | {v['err'] or '正常'} | {v['n']} |\n")

    # ------------------------------------------------------------ modes
    def daily(self) -> None:
        S, now = self.S, self.now
        first_run = not self.state.get("initialized")
        days = int(os.environ.get("LOOKBACK_DAYS") or (S["first_run_lookback_days"] if first_run else S["lookback_days"]))
        since = (now - dt.timedelta(days=days)).date()
        rows = 300 if days > 10 else 100
        min_year = now.year - 1
        print(f"radar daily {iso(now)} lookback={days}d since={since} first_run={first_run}")
        for t in self.cfg["topics"]:
            for q in t.get("crossref_queries", []):
                self.record("Crossref", lambda q=q: sources.crossref(q, since, rows, min_year))
                sources.polite_pause(1.2)
            if t.get("arxiv_query"):
                self.record("arXiv", lambda t=t: sources.arxiv(t["arxiv_query"], since))
                sources.polite_pause(3.5)
        oa_key = os.environ.get("OPENALEX_API_KEY", "").strip()
        if oa_key:
            for t in self.cfg["topics"]:
                for q in t.get("crossref_queries", [])[:4]:
                    self.record("OpenAlex", lambda q=q: sources.openalex(q, since, oa_key))
                    sources.polite_pause(0.5)
        for r in self.cfg.get("rss", []):
            self.record(f"RSS · {r['name']}", lambda r=r: sources.rss(r["name"], r["url"]))
            sources.polite_pause(0.5)
        self.settle_status()

        t0 = time.time()
        fresh = [p for p in {p["id"]: p for p in self.found if p.get("id")}.values() if p["id"] not in self.papers]
        ab = self.fill_abstracts(fresh, t0 + 240, epmc_cap=200)
        for p in self.found:  # same DOI may appear several times in self.found
            if not llm.usable_abstract(p):
                q = next((x for x in fresh if x["id"] == p.get("id") and llm.usable_abstract(x)), None)
                if q:
                    p["abstract"] = q["abstract"]
        new_items = self.ingest(self.found, min_year=min_year, origin="日常")
        print(f"  candidates={len(self.found)} relevant_new={len(new_items)}")
        if llm.enabled():
            self.status["llm"] = llm.review(sorted(new_items, key=lambda p: -p["score"])[:80], S.get("research_profile", ""))
            for p in new_items:
                if "llm_score" in p:
                    p["score"] += 5 * p["llm_score"] - 5

        pushable = sorted([p for p in new_items if p["score"] >= S["min_push_score"] and p.get("llm_score") != 0],
                          key=lambda p: (-TIER_RANK[p["tier"]], -p["score"]))
        rest = sorted([p for p in new_items if p not in pushable], key=lambda p: (-TIER_RANK[p["tier"]], -p["score"]))
        bst = self.brief(pushable[: S["max_push_items"]] + rest, time.time() + 480)
        results: list[str] = []
        site_url = S.get("site_url", "")
        welcome = first_run or (self.state.get("welcome_pending") and push.channels())
        if welcome and not first_run:
            # push channel was added after the first run: send the start-up message now
            recent = sorted([p for p in self.papers.values() if p.get("tier") in ("cross", "direct", "transferable")
                             and p.get("kind") != "wechat"], key=lambda p: (p.get("date") or "", p.get("score", 0)), reverse=True)
            pushable = pushable or recent[:10]
            self.brief(pushable[:10], time.time() + 240)
        if welcome and not pushable:
            results = push.send("文献雷达已启动", f"日常追踪已开始运行，资料库里现有 {len(self.papers)} 篇（{S.get('archive_from', '2024-01')} 起）。"
                                "以后每天检查两次，有新论文就推送到这里。" + (f"\n\n[打开文献雷达]({site_url})" if site_url else ""))
        elif welcome:
            head = (f"文献雷达已开始运行：资料库现有 {len(self.papers)} 篇（{S.get('archive_from', '2024-01')} 起）。"
                    "以后每天检查两次，只推送新出现的论文。最近的重点论文：")
            results = push.send(f"文献雷达已启动：重点 {len(pushable)} 篇", push.digest(pushable[:10], site_url, head))
        elif pushable:
            top = pushable[: S["max_push_items"]]
            cnt: dict[str, int] = {}
            for p in pushable:
                cnt[p["tier"]] = cnt.get(p["tier"], 0) + 1
            summary = " / ".join(f"{TIER_ZH[t]}{n}" for t, n in sorted(cnt.items(), key=lambda x: -TIER_RANK[x[0]]))
            head = f"新增 {len(pushable)} 篇（{summary}）" + (f"，先列 {len(top)} 篇" if len(pushable) > len(top) else "")
            star = "⭐" if any(p["tier"] == "cross" for p in top) else ""
            results = push.send(f"{star}文献雷达：新增 {len(pushable)} 篇", push.digest(top, site_url, head))
        else:
            last = self.state.get("last_push_at")
            idle = (now - dt.datetime.fromisoformat(last.replace("Z", "+00:00"))).days if last else 999
            if idle >= S["heartbeat_days"]:
                ok = sum(1 for v in self.status["sources"].values() if not v["err"])
                results = push.send("文献雷达：运行正常，暂无新论文",
                                    f"过去 {min(idle, S['heartbeat_days']) if idle < 999 else S['heartbeat_days']} 天没有达到推送标准的新论文。"
                                    f"本次 {ok}/{len(self.status['sources'])} 个来源正常。"
                                    + (f"\n\n[打开文献雷达]({site_url})" if site_url else ""))
        cr = self.status["sources"].get("Crossref", {})
        if cr.get("calls", 0) > 0 and cr.get("fails") == cr.get("calls"):
            la = self.state.get("last_alert_at")
            if not la or (now - dt.datetime.fromisoformat(la.replace("Z", "+00:00"))).total_seconds() > 86400:
                results += push.send("⚠️ 文献雷达：主数据源异常",
                                     f"Crossref 本次全部请求失败：{cr['err']}\n\n通常是临时故障，下次运行会自动重试；连续几天收到再看 GitHub Actions 日志。")
                self.state["last_alert_at"] = iso(now)
        if welcome:
            self.state["welcome_pending"] = not push.delivered(results)
        if results and push.delivered(results):
            self.state["last_push_at"] = iso(now)
        self.state["initialized"] = True
        self.state["last_run"] = iso(now)
        hist = self.state.get("history", [])
        hist.append({"at": iso(now), "new": len(new_items), "push": results,
                     "errors": {k: v["err"] for k, v in self.status["sources"].items() if v["err"]}})
        self.state["history"] = hist[-60:]
        extra = {"模式": "日常", "新增相关": len(new_items), "达到推送标准": len(pushable)}
        if ab.get("查询"):
            extra["补摘要"] = f"{ab.get('找到', 0)}/{ab['查询']}"
        if llm.provider():
            extra["中文简介"] = f"新写 {bst['done']} 篇" + (f"（出错：{bst['err']}）" if bst["err"] else "")
        self.finish(results, extra)

    def backfill(self, start: str, until: str | None, *, state_key: str = "backfill", label: str = "回填") -> None:
        """Month-by-month Crossref backfill by publication date. Resumable within a time budget."""
        end = until or self.now.strftime("%Y-%m")
        months, y, m = [], int(start[:4]), int(start[5:7])
        while f"{y:04d}-{m:02d}" <= end:
            months.append(f"{y:04d}-{m:02d}")
            y, m = (y + 1, 1) if m == 12 else (y, m + 1)
        bf = self.state.setdefault(state_key, {"done": []})
        todo = [mo for mo in months if mo not in bf["done"] or (state_key == "backfill" and mo >= self.now.strftime("%Y-%m"))]
        budget = float(os.environ.get("BACKFILL_MAX_MINUTES", "100")) * 60
        rows = int(self.S.get("backfill_rows", 200))
        t0, added, completed = time.time(), 0, []
        print(f"radar backfill {start}..{end}: {len(todo)} months to do, rows/query={rows}")
        for mo in todo:
            if time.time() - t0 > budget:
                print(f"  time budget reached before {mo}")
                break
            yy, mm = int(mo[:4]), int(mo[5:7])
            last_day = calendar.monthrange(yy, mm)[1]
            self.found = []
            for t in self.cfg["topics"]:
                for q in t.get("crossref_queries", []):
                    self.record(f"Crossref（{label}）", lambda q=q: sources.crossref(
                        q, None, rows, 2000, pub_from=f"{mo}-01", pub_until=f"{mo}-{last_day:02d}"))
                    sources.polite_pause(1.0)
            for p in self.found:  # Crossref matched this month's publication window
                if not sources.good_date(p.get("date"), self.now.date().isoformat()):
                    p["date"] = mo
            got = self.ingest(self.found, min_month=self.S.get("archive_from", "2024-01"), origin="回填", refresh_dates=True)
            added += len(got)
            completed.append(mo)
            if mo not in bf["done"]:
                bf["done"].append(mo)
            print(f"  {mo}: +{len(got)} (candidates {len(self.found)})")
            store.save_papers(self.papers)  # checkpoint each month
            store.save("state.json", self.state)
        self.settle_status()
        remaining = [mo for mo in months if mo not in bf["done"]]
        results = []
        if not remaining and state_key != "backfill":
            bf["finished"] = iso(self.now)
        elif not remaining:
            by = {}
            for p in self.papers.values():
                by[p["tier"]] = by.get(p["tier"], 0) + 1
            msg = (f"回填完成：{start} 至 {end}，库内现有 {len(self.papers)} 篇（"
                   + " / ".join(f"{TIER_ZH[t]}{n}" for t, n in sorted(by.items(), key=lambda x: -TIER_RANK[x[0]])) + "）。"
                   "回填的论文不会逐篇推送，已按月份放进网页“按月浏览”。")
            if self.S.get("site_url"):
                msg += f"\n\n[打开文献雷达]({self.S['site_url']})"
            if not bf.get("announced"):
                results = push.send("文献雷达：历史回填完成", msg)
                bf["announced"] = push.delivered(results)
        self.finish(results, {"模式": label, "本次完成月份": f"{completed[0]}…{completed[-1]}" if completed else "无",
                              "本次新增": added, "剩余月份": len(remaining)})
        if remaining:
            print(f"  还剩 {len(remaining)} 个月没回填，再运行一次会接着做：{remaining[0]} 起")

    def rules_hash(self) -> str:
        import hashlib
        h = hashlib.sha1(json.dumps(self.cfg.get("topics"), sort_keys=True, ensure_ascii=False).encode())
        for f in ("filters.py", "legacy_rules.py"):
            h.update((store.ROOT / "radar" / f).read_bytes())
        return h.hexdigest()[:12]

    def reclassify(self) -> dict:
        """Apply the current rules to the whole library. Papers the rules now reject are removed,
        except ones carrying the user's marks (old-site favourites/notes) or a 公众号 report."""
        moved, dropped = {}, 0
        for k, p in list(self.papers.items()):
            before = p.get("tier")
            c = self.clf.classify(p)
            if c:
                p.update(c)
                if before != p["tier"]:
                    moved[f"{TIER_ZH.get(before, before)}→{TIER_ZH[p['tier']]}"] = moved.get(f"{TIER_ZH.get(before, before)}→{TIER_ZH[p['tier']]}", 0) + 1
            elif not (p.get("old_fav") or p.get("old_note") or p.get("reported_by")):
                del self.papers[k]
                dropped += 1
        return {"调整": moved, "移除": dropped}

    def maintain(self, minutes: float) -> None:
        """Slow background work, a slice per run: fix duplicate DOIs, find missing abstracts, write Chinese briefs."""
        deadline = time.time() + minutes * 60
        self.status = {"sources": {}}
        extra: dict = {"模式": "维护"}
        # Angewandte German-edition DOIs -> international edition (same suffix)
        moved = 0
        for k in [k for k in self.papers if k.startswith("10.1002/ange.")]:
            p = self.papers.pop(k)
            nk = "10.1002/anie." + k.split("10.1002/ange.", 1)[1]
            p.update({"id": nk, "doi": nk, "url": f"https://doi.org/{nk}"})
            if (p.get("source") or "").lower().startswith("angewandte"):
                p["source"] = sources.ANGEW_INT
            if nk in self.papers:
                for kk, v in p.items():
                    self.papers[nk].setdefault(kk, v)
            else:
                self.papers[nk] = p
            moved += 1
        if moved:
            extra["Angewandte 德文版 DOI 合并"] = moved
        rh = self.rules_hash()
        if self.state.get("rules_hash") != rh:  # rules changed since the library was classified
            rc = self.reclassify()
            self.state["rules_hash"] = rh
            extra["按新规则重筛"] = (f"移除 {rc['移除']} 篇；" + "，".join(f"{k} {v}" for k, v in rc["调整"].items())) or "无变化"
            print(f"  reclassify: {rc}")
        # abstracts for stored papers (each paper is looked up at most once a month)
        today = self.now.date()
        def tried_recently(p):
            try:
                return (today - dt.date.fromisoformat(p.get("ab_try", "")[:10])).days < 30
            except ValueError:
                return False
        cands = [p for p in self.papers.values() if p.get("kind") != "wechat" and not llm.usable_abstract(p)
                 and str(p.get("doi") or p["id"]).startswith("10.") and not tried_recently(p)]
        cands.sort(key=lambda p: (TIER_RANK.get(p.get("tier"), 0), p.get("date") or ""), reverse=True)
        cands = cands[:1200]
        if cands:
            before = {p["id"]: p.get("tier") for p in cands}
            ab = self.fill_abstracts(cands, min(deadline, time.time() + 300), epmc_cap=300)
            changed = 0
            for p in cands:
                p["ab_try"] = today.isoformat()
                if llm.usable_abstract(p):
                    c = self.clf.classify(p)
                    if c:  # refine the tier with the abstract; never drop a stored paper here
                        p.update(c)
                        changed += before[p["id"]] != p.get("tier")
            extra["补摘要"] = f"查 {ab.get('查询', 0)} 篇，找到 {ab.get('找到', 0)} 篇，相关度随之调整 {changed} 篇"
        # Chinese briefs, most relevant + newest first
        todo = [p for p in self.papers.values() if llm.needs_brief(p)]
        todo.sort(key=lambda p: (TIER_RANK.get(p.get("tier"), 0), p.get("date") or ""), reverse=True)
        if llm.provider():
            st = self.brief(todo, deadline)
            extra["中文简介"] = f"本次 {st['done']} 篇，还剩 {st.get('left', len(todo))} 篇" + (f"（{st['err']}）" if st["err"] else "")
        extra["公众号报道关联"] = f"{self.link_mentions()} 处"
        self.finish([], extra)

    def link_mentions(self) -> int:
        """公众号推文里提到的 DOI -> 给对应论文打上“公众号报道”标签。"""
        n = 0
        for p in self.papers.values():
            if p.get("kind") != "wechat":
                continue
            for doi in p.get("mentions", []):
                q = self.papers.get(doi)
                if q is not None:
                    rb = q.setdefault("reported_by", [])
                    if p["source"] not in rb:
                        rb.append(p["source"])
                        n += 1
        return n

    def import_old(self, sqlite_path: str | None, cloud: str | None, export_path: str | None = None) -> None:
        extra = {"模式": "导入旧站"}
        since = self.S.get("archive_from", "2024-01")
        if export_path:
            papers, posts, st = legacy_import.from_export(export_path)
            got = self.ingest(papers, keep_first_seen=True, min_month=since, origin="旧站本地库")
            gotw = self.ingest(posts, keep_first_seen=True, min_month=since, origin="旧站公众号库")
            extra["旧站本地库"] = f"读取 {st['records']} 篇 → 收录 {len(got)} 篇相关论文"
            extra["旧站公众号库"] = f"读取 {st['wechat']} 篇推文 → 收录 {len(gotw)} 篇科研报道"
            print(f"  export: papers {st['records']}->{len(got)}  wechat {st['wechat']}->{len(gotw)}")
        if sqlite_path:
            items, st = legacy_import.from_sqlite(sqlite_path)
            got = self.ingest(items, keep_first_seen=True, min_month=since, origin="旧站本地库")
            extra["旧站本地库"] = f"读取 {st['rows']} 条 → 新收录 {len(got)} 篇（{since} 以后且相关）"
            print(f"  sqlite: rows={st['rows']} imported={len(got)}")
        if cloud:
            try:
                items, st = legacy_import.from_cloud(cloud)
                got = self.ingest(items, keep_first_seen=True, min_month=since, origin="旧站云端")
                extra["旧站云端"] = f"读取 {st['fetched']}/{st['total']} 条 → 新收录 {len(got)} 篇"
                print(f"  cloud: {st} imported={len(got)}")
            except Exception as e:
                extra["旧站云端"] = f"失败：{type(e).__name__}: {e}"
                print(f"  ! cloud import failed: {e}")
        extra["公众号报道关联"] = f"{self.link_mentions()} 处"
        self.finish([], extra)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="radar")
    ap.add_argument("--backfill", metavar="YYYY-MM")
    ap.add_argument("--until", metavar="YYYY-MM")
    ap.add_argument("--import-sqlite", metavar="PATH")
    ap.add_argument("--import-cloud", metavar="URL")
    ap.add_argument("--import-export", metavar="legacy-export.json.gz")
    ap.add_argument("--render-only", action="store_true")
    ap.add_argument("--reclassify", action="store_true", help="改了规则之后：用当前规则重新筛一遍整个资料库（不联网）")
    ap.add_argument("--maintain", action="store_true", help="补摘要、写中文简介（受 MAINTAIN_MAX_MINUTES 限时）")
    ap.add_argument("--auto-extras", action="store_true",
                    help="日常运行之后：旧云端站只导入一次；历史回填没做完就接着做（受 BACKFILL_MAX_MINUTES 限时）")
    a = ap.parse_args(argv)
    r = Radar()
    if a.render_only:
        r.finish([], {"模式": "仅生成网页"})
    elif a.reclassify:
        before = len(r.papers)
        rc = r.reclassify()
        r.state["rules_hash"] = r.rules_hash()
        r.link_mentions()
        r.finish([], {"模式": "按当前规则重筛", "重筛前": before, "重筛后": len(r.papers)})
        print(f"reclassify: {before} -> {len(r.papers)} {rc}")
    elif a.maintain:
        r.maintain(float(os.environ.get("MAINTAIN_MAX_MINUTES", "20")))
    elif a.auto_extras:
        t_start = time.time()
        cloud = r.S.get("old_cloud_url", "").strip()
        if cloud and not r.state.get("cloud_imported"):
            r.import_old(None, cloud)
            if "失败" not in str(r.status.get("旧站云端", "")):
                r.state["cloud_imported"] = iso(r.now)
                store.save("state.json", r.state)
            r = Radar()
        start = r.S.get("archive_from", "2024-01")
        bf = r.state.get("backfill", {})
        months_needed = []
        y, mo = int(start[:4]), int(start[5:7])
        while f"{y:04d}-{mo:02d}" < r.now.strftime("%Y-%m"):
            months_needed.append(f"{y:04d}-{mo:02d}")
            y, mo = (y + 1, 1) if mo == 12 else (y, mo + 1)
        if any(x not in bf.get("done", []) for x in months_needed):
            r.backfill(start, (r.now.replace(day=1) - dt.timedelta(days=1)).strftime("%Y-%m"))
        elif not bf.get("announced") and push.channels():
            r.backfill(start, (r.now.replace(day=1) - dt.timedelta(days=1)).strftime("%Y-%m"))  # nothing left: only sends the summary
        elif REBACKFILL_ID and r.state.get("rebackfill", {}).get("id") != REBACKFILL_ID:
            r.state["rebackfill"] = {"id": REBACKFILL_ID, "done": []}
            r.backfill(start, r.now.strftime("%Y-%m"), state_key="rebackfill", label="规则修正后重新回填")
        elif REBACKFILL_ID and not r.state["rebackfill"].get("finished"):
            r.backfill(start, r.now.strftime("%Y-%m"), state_key="rebackfill", label="规则修正后重新回填")
        else:
            print("历史回填已完成，旧云端站已导入")
        busy = (time.time() - t_start) / 60
        minutes = float(os.environ.get("MAINTAIN_MAX_MINUTES", "20"))
        minutes = min(minutes, max(2.0, 45 - busy)) if busy > 1 else minutes
        Radar().maintain(minutes)
    elif a.import_sqlite or a.import_cloud or a.import_export:
        r.import_old(a.import_sqlite, a.import_cloud, a.import_export)
    elif a.backfill:
        r.backfill(a.backfill, a.until)
    else:
        r.daily()
    return 0


if __name__ == "__main__":
    sys.exit(main())
