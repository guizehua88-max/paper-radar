"""文献雷达 entry point.

  python -m radar                         日常运行：查新 -> 筛选 -> 保存 -> 网页/RSS -> 微信推送
  python -m radar --backfill 2024-01      按发表月份回填（可中断，再运行会接着做）
  python -m radar --import-sqlite 旧库.sqlite3   导入旧本地站数据库（含收藏/已读/笔记）
  python -m radar --import-cloud https://paperresearch.de5.net   导入旧云端站
  python -m radar --render-only           只重新生成网页
"""
from __future__ import annotations

import argparse
import calendar
import datetime as dt
import os
import sys
import time
import tomllib

from . import legacy_import, llm, push, site, sources, store
from .filters import TIER_RANK, TIER_ZH, Classifier, title_key


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
               origin: str | None = None, min_year: int | None = None) -> list[dict]:
        seen_titles = {title_key(p["title"]): pid for pid, p in self.papers.items()}
        new = []
        for p in items:
            pid = p.get("id")
            if not pid or not p.get("title"):
                continue
            tk = title_key(p["title"])
            if pid in self.papers or tk in seen_titles:
                old = self.papers.get(pid) or self.papers.get(seen_titles.get(tk, ""), None)
                if old is not None:  # carry over old-site favourites/notes onto an existing record
                    for k in ("old_fav", "old_read", "old_note"):
                        if p.get(k) and not old.get(k):
                            old[k] = p[k]
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
        store.save_papers(self.papers)
        store.save("state.json", self.state)
        site.render(self.ordered(), self.cfg, self.status, iso(self.now))
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

        new_items = self.ingest(self.found, min_year=min_year, origin="日常")
        print(f"  candidates={len(self.found)} relevant_new={len(new_items)}")
        if llm.enabled():
            self.status["llm"] = llm.review(sorted(new_items, key=lambda p: -p["score"])[:80], S.get("research_profile", ""))
            for p in new_items:
                if "llm_score" in p:
                    p["score"] += 5 * p["llm_score"] - 5

        pushable = sorted([p for p in new_items if p["score"] >= S["min_push_score"] and p.get("llm_score") != 0],
                          key=lambda p: (-TIER_RANK[p["tier"]], -p["score"]))
        results: list[str] = []
        site_url = S.get("site_url", "")
        welcome = first_run or (self.state.get("welcome_pending") and push.channels())
        if welcome and not first_run:
            # push channel was added after the first run: send the start-up message now
            recent = sorted([p for p in self.papers.values() if p.get("tier") in ("cross", "direct", "transferable")
                             and p.get("kind") != "wechat"], key=lambda p: (p.get("date") or "", p.get("score", 0)), reverse=True)
            pushable = pushable or recent[:10]
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
        self.finish(results, {"模式": "日常", "新增相关": len(new_items), "达到推送标准": len(pushable)})

    def backfill(self, start: str, until: str | None) -> None:
        """Month-by-month Crossref backfill by publication date. Resumable within a time budget."""
        end = until or self.now.strftime("%Y-%m")
        months, y, m = [], int(start[:4]), int(start[5:7])
        while f"{y:04d}-{m:02d}" <= end:
            months.append(f"{y:04d}-{m:02d}")
            y, m = (y + 1, 1) if m == 12 else (y, m + 1)
        bf = self.state.setdefault("backfill", {"done": []})
        todo = [mo for mo in months if mo not in bf["done"] or mo >= self.now.strftime("%Y-%m")]
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
                    self.record("Crossref（回填）", lambda q=q: sources.crossref(
                        q, None, rows, 2000, pub_from=f"{mo}-01", pub_until=f"{mo}-{last_day:02d}"))
                    sources.polite_pause(1.0)
            got = self.ingest(self.found, min_month=self.S.get("archive_from", "2024-01"), origin="回填")
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
        if not remaining:
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
        self.finish(results, {"模式": "回填", "本次完成月份": f"{completed[0]}…{completed[-1]}" if completed else "无",
                              "本次新增": added, "剩余月份": len(remaining)})
        if remaining:
            print(f"  还剩 {len(remaining)} 个月没回填，再运行一次会接着做：{remaining[0]} 起")

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
    ap.add_argument("--auto-extras", action="store_true",
                    help="日常运行之后：旧云端站只导入一次；历史回填没做完就接着做（受 BACKFILL_MAX_MINUTES 限时）")
    a = ap.parse_args(argv)
    r = Radar()
    if a.render_only:
        r.finish([], {"模式": "仅生成网页"})
    elif a.auto_extras:
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
        else:
            print("历史回填已完成，旧云端站已导入，无需额外工作")
    elif a.import_sqlite or a.import_cloud or a.import_export:
        r.import_old(a.import_sqlite, a.import_cloud, a.import_export)
    elif a.backfill:
        r.backfill(a.backfill, a.until)
    else:
        r.daily()
    return 0


if __name__ == "__main__":
    sys.exit(main())
