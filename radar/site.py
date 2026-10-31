"""Static site for GitHub Pages (/docs). Works from file:// too (data is loaded as <script> files).

docs/index.html          page + app (static, rarely changes)
docs/data/index.js       window.RADAR_INDEX = {months, latest, favs, status, ...}   (small, every run)
docs/data/m/YYYY-MM.js   RADAR_LOAD("YYYY-MM", [...])  one file per publication month (only changed months rewrite)
docs/feed.xml            RSS of recent 重点 papers (Zotero 订阅 / any reader)
"""
from __future__ import annotations

import datetime as dt
import html
import json
import re
from email.utils import format_datetime

from .filters import TIER_RANK
from .llm import usable_abstract
from .store import DOCS, month_of

PUSH_TIERS = ("cross", "direct", "transferable")
IMPORTED = ("旧站本地库", "旧站云端", "旧站公众号库", "回填")


def summary_progress(papers: list[dict]) -> dict:
    """Title translations are useful, but are not completed abstract summaries."""
    def count(items):
        briefs = sum(bool(usable_abstract(p) and p.get("zh_brief") and p.get("brief_from") != "title") for p in items)
        title_only = sum(bool(not usable_abstract(p) and p.get("zh_title")) for p in items)
        return {"total": len(items), "briefs": briefs, "titleOnly": title_only,
                "pending": len(items) - briefs - title_only}
    real = [p for p in papers if p.get("kind") != "wechat"]
    return {"key": count([p for p in real if p.get("tier") in PUSH_TIERS]), "all": count(real)}


_VAGUE = ("未明确", "未交代", "待核", "待定", "未见", "不详")


def _useful(label: str) -> str:
    """Keep only informative parts of the old rule's Chinese labels (drop 'route unclear' style filler)."""
    parts = [x for x in re.split(r"[；;]", label or "") if x.strip() and not any(v in x for v in _VAGUE)]
    return "；".join(parts)


def slim(p: dict) -> dict:
    o = {
        "id": p["id"], "t": p["title"], "u": p["url"], "s": p.get("source") or "", "d": p.get("date") or "",
        "f": p.get("first_seen") or "", "tp": p.get("topics", []), "tr": p.get("tier", "uncertain"),
        "sc": p.get("score", 0), "ab": (p.get("abstract") or "")[:900], "au": (p.get("authors") or [])[:6],
        "o": p.get("origin") or "",
    }
    for k_src, k in (("watch", "w"), ("zh", "zh"), ("old_note", "n"), ("why", "why"), ("zh_title", "zt"), ("zh_brief", "zb")):
        if p.get(k_src):
            o[k] = p[k_src]
    label = "｜".join(x for x in (_useful(p.get("zh_obj", "")), _useful(p.get("zh_method", ""))) if x)
    if label:
        o["ob"] = label
    if p.get("brief_from") == "title" and not p.get("zh_brief"):
        o["bt"] = 1
    if p.get("preprint"):
        o["pp"] = p["preprint"].get("source", "预印本")
    if p.get("top"):
        o["top"] = 1
    if p.get("kind") == "preprint":
        o["pre"] = 1
    if p.get("kind") == "wechat":
        o["wx"] = 1
        if p.get("mentions"):
            o["mt"] = p["mentions"][:3]
    if p.get("reported_by"):
        o["rb"] = p["reported_by"][:12]
    if p.get("old_fav"):
        o["fv"] = 1
    if p.get("old_read"):
        o["rd"] = 1
    if p.get("llm_score") is not None:
        o["llm"] = p["llm_score"]
    return o


def _write_if_changed(path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists() and path.read_text("utf-8") == text:
        return
    path.write_text(text, "utf-8")


def _js(obj) -> str:
    return json.dumps(obj, ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/")


def month_label(m: str) -> str:
    if m == "unknown":
        return "日期不详"
    if m.endswith("-00"):
        return f"{m[:4]}年（月份不详）"
    return f"{m[:4]}年{int(m[5:7])}月"


def render(papers: list[dict], cfg: dict, status: dict, generated: str) -> None:
    S = cfg["settings"]
    now = dt.datetime.fromisoformat(generated.replace("Z", "+00:00"))
    by_month: dict[str, list] = {}
    for p in papers:
        by_month.setdefault(month_of(p), []).append(p)

    mdir = DOCS / "data" / "m"
    months = []
    for m, ps in by_month.items():
        ps.sort(key=lambda p: (p.get("date") or "", TIER_RANK.get(p.get("tier"), 0), p.get("score", 0), p["id"]), reverse=True)
        _write_if_changed(mdir / f"{m}.js", f"RADAR_LOAD({_js(m)},{_js([slim(p) for p in ps])});\n")
        months.append({
            "m": m, "label": month_label(m), "n": len(ps),
            "key": sum(1 for p in ps if p.get("tier") in PUSH_TIERS and p.get("kind") != "wechat"),
            "cross": sum(1 for p in ps if p.get("tier") == "cross" and p.get("kind") != "wechat"),
            "wx": sum(1 for p in ps if p.get("kind") == "wechat"),
        })
    if mdir.exists():
        for f in mdir.glob("*.js"):
            if f.stem not in by_month:
                f.unlink()
    months.sort(key=lambda x: ("0000" if x["m"] == "unknown" else x["m"]), reverse=True)  # 日期不详 last

    def recent(p, days):
        try:
            return (now - dt.datetime.fromisoformat(p["first_seen"].replace("Z", "+00:00"))).days < days
        except Exception:
            return False

    daily = [p for p in papers if p.get("origin") not in IMPORTED]
    latest = [p for p in daily if recent(p, 21)][:400]
    if len(latest) < 30:  # brand-new install: show the most recently published instead
        latest = sorted(papers, key=lambda p: (p.get("date") or "", p.get("score", 0)), reverse=True)[:150]
    favs = [p for p in papers if p.get("old_fav") or p.get("old_note")][:600]
    tiers, topics = {}, {}
    for p in papers:
        tiers[p.get("tier")] = tiers.get(p.get("tier"), 0) + 1
        for t in p.get("topics", []):
            topics[t] = topics.get(t, 0) + 1
    index = {
        "title": S["site_title"], "generated": generated, "ver": generated.replace(":", "").replace("-", ""),
        "topics": {t["id"]: t["name"] for t in cfg["topics"]}, "minPush": S["min_push_score"],
        "archiveFrom": S.get("archive_from", "2024-01"), "total": len(papers), "tiers": tiers, "topicCounts": topics,
        "week": sum(1 for p in daily if recent(p, 7)), "months": months,
        "latest": [slim(p) for p in latest], "favs": [slim(p) for p in favs],
        "imported": {o: sum(1 for p in papers if p.get("origin") == o) for o in IMPORTED},
        "wechat": sum(1 for p in papers if p.get("kind") == "wechat"),
        "briefs": sum(1 for p in papers if p.get("zh_brief") or p.get("zh_title")),
        "briefKey": sum(1 for p in papers if (p.get("zh_brief") or p.get("zh_title")) and p.get("tier") in PUSH_TIERS and p.get("kind") != "wechat"),
        "noAbstractKey": sum(1 for p in papers if p.get("brief_from") == "title" and p.get("tier") in PUSH_TIERS and p.get("kind") != "wechat"),
        "keyTotal": sum(1 for p in papers if p.get("tier") in PUSH_TIERS and p.get("kind") != "wechat"),
        "summaryProgress": summary_progress(papers),
        "status": status,
    }
    _write_if_changed(DOCS / "data" / "index.js", f"window.RADAR_INDEX={_js(index)};\n")
    _write_if_changed(DOCS / "index.html", TEMPLATE.replace("__TITLE__", html.escape(S["site_title"])))
    _write_if_changed(DOCS / ".nojekyll", "")
    _feed(papers, cfg, generated)


def _feed(papers: list[dict], cfg: dict, generated: str) -> None:
    names = {t["id"]: t["name"] for t in cfg["topics"]}
    tz = {"cross": "⭐交叉", "direct": "直接", "transferable": "可迁移"}
    site = cfg["settings"].get("site_url") or ""
    keep = [p for p in papers if p.get("score", 0) >= cfg["settings"]["min_push_score"] and p.get("llm_score") != 0
            and p.get("origin") not in IMPORTED and p.get("kind") != "wechat"][:150]
    items = []
    for p in keep:
        try:
            seen = dt.datetime.fromisoformat(p["first_seen"].replace("Z", "+00:00"))
        except Exception:
            continue
        tag = "+".join(names.get(t, t) for t in p["topics"])
        desc = f"{p.get('source', '')} · {p.get('date') or ''} · {tag}"
        if p.get("zh_title"):
            desc = f"{html.escape(p['zh_title'])}<br>" + desc
        if p.get("zh_brief"):
            desc += f"<br><br>{html.escape(p['zh_brief'])}"
        if p.get("zh_obj"):
            desc += f"<br>{html.escape(p['zh_obj'])}｜{html.escape(p.get('zh_method', ''))}"
        if p.get("abstract"):
            desc += f"<br><br>{html.escape(p['abstract'][:1200])}"
        items.append(
            "<item>"
            f"<title>{html.escape('[' + tz.get(p.get('tier'), '') + '·' + tag + '] ' + p['title'])}</title>"
            f"<link>{html.escape(p['url'])}</link>"
            f"<guid isPermaLink=\"false\">{html.escape(p['id'])}</guid>"
            f"<pubDate>{format_datetime(seen)}</pubDate>"
            f"<description>{html.escape(desc)}</description>"
            "</item>"
        )
    now = dt.datetime.fromisoformat(generated.replace("Z", "+00:00"))
    xml = (
        '<?xml version="1.0" encoding="UTF-8"?>\n<rss version="2.0"><channel>'
        f"<title>{html.escape(cfg['settings']['site_title'])}</title>"
        f"<link>{html.escape(site)}</link><description>合成氨 / 焦耳热 / ALD 新论文</description>"
        f"<lastBuildDate>{format_datetime(now)}</lastBuildDate>" + "".join(items) + "</channel></rss>\n"
    )
    (DOCS / "feed.xml").write_text(xml, "utf-8")


TEMPLATE = r"""<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>__TITLE__</title>
<link rel="alternate" type="application/rss+xml" title="RSS" href="feed.xml">
<style>
:root{
  --bg:#f6f4ef; --card:#fffdf9; --ink:#1c1b19; --muted:#6b675f; --line:#e4e0d6; --soft:#efebe2; --hi:#fff6d6;
  --nh3:#0f766e; --joule:#c2410c; --ald:#4338ca; --star:#b7791f; --link:#1d4ed8; --new:#dc2626;
  --serif:"Iowan Old Style","Source Han Serif SC","Noto Serif SC","Songti SC",Georgia,serif;
  --sans:-apple-system,BlinkMacSystemFont,"PingFang SC","Microsoft YaHei","Segoe UI",Roboto,sans-serif;
}
@media (prefers-color-scheme: dark){
  :root{--bg:#141412;--card:#1c1c1a;--ink:#ecebe6;--muted:#a19d93;--line:#2e2d29;--soft:#252420;--hi:#2c2817;
    --nh3:#2dd4bf;--joule:#fb923c;--ald:#a5b4fc;--star:#f6c453;--link:#93c5fd;--new:#ef4444;}
}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--ink);font:15px/1.6 var(--sans);-webkit-text-size-adjust:100%}
.wrap{max-width:920px;margin:0 auto;padding:26px 16px 60px}
.kicker{font:600 11px/1 var(--sans);letter-spacing:.18em;color:var(--muted);text-transform:uppercase}
h1{font:600 26px/1.25 var(--serif);margin:8px 0 6px}
.sub{color:var(--muted);font-size:13px}.sub b{color:var(--ink);font-weight:600}
a{color:var(--link)}
.views{display:flex;gap:4px;margin-top:16px;border-bottom:1px solid var(--line)}
.view{border:0;background:none;color:var(--muted);font:600 14.5px var(--sans);padding:9px 12px;cursor:pointer;border-bottom:2px solid transparent;margin-bottom:-1px}
.view[aria-pressed=true]{color:var(--ink);border-bottom-color:var(--ink)}
.months{margin:14px 0 4px}
.yr{display:flex;gap:8px;align-items:flex-start;margin:6px 0}
.yr b{font:600 13px var(--sans);min-width:44px;padding-top:5px;color:var(--muted)}
.mm{display:flex;flex-wrap:wrap;gap:5px}
.mon{border:1px solid var(--line);background:var(--card);color:var(--ink);border-radius:8px;padding:3px 9px;font:500 12.5px var(--sans);cursor:pointer;line-height:1.5}
.mon small{color:var(--muted);margin-left:3px}
.mon .x{color:var(--star)}
.mon[aria-pressed=true]{background:var(--ink);color:var(--bg);border-color:var(--ink)}
.mon[aria-pressed=true] small{color:inherit;opacity:.75}
.controls{position:sticky;top:0;z-index:5;background:var(--bg);padding:10px 0 8px;border-bottom:1px solid var(--line)}
.row{display:flex;gap:6px;flex-wrap:wrap;align-items:center}
.chips{display:flex;gap:6px;overflow-x:auto;scrollbar-width:none;padding-bottom:6px}
.chips::-webkit-scrollbar{display:none}
.chip{flex:none;border:1px solid var(--line);background:var(--card);color:var(--ink);border-radius:999px;padding:4px 11px;font:500 13px var(--sans);cursor:pointer}
.chip .n{color:var(--muted);margin-left:4px;font-weight:400}
.chip[aria-pressed=true]{background:var(--ink);color:var(--bg);border-color:var(--ink)}
.chip[aria-pressed=true] .n{color:inherit;opacity:.7}
.chip.sm{font-size:12px;padding:3px 9px}
select,input[type=search]{border:1px solid var(--line);background:var(--card);color:var(--ink);border-radius:9px;padding:7px 10px;font:14px var(--sans);outline:none}
input[type=search]{flex:1;min-width:160px}
.tool{border:1px solid var(--line);background:var(--card);color:var(--ink);border-radius:9px;padding:7px 11px;font:13px var(--sans);cursor:pointer}
.hint{color:var(--muted);font-size:12.5px;margin-top:6px}
.day{margin:22px 0 6px;font:600 12px var(--sans);color:var(--muted);letter-spacing:.05em}
.card{background:var(--card);border:1px solid var(--line);border-left:3px solid var(--c,var(--line));border-radius:10px;padding:11px 13px;margin:8px 0}
.card.read{opacity:.6}
.badges{display:flex;flex-wrap:wrap;gap:5px;margin-bottom:5px}
.b{font:600 11px/1 var(--sans);padding:4px 7px;border-radius:6px;background:var(--soft);color:var(--muted)}
.b.new{background:var(--new);color:#fff}
.b.cross{color:var(--star);background:transparent;border:1px solid var(--star)}
.b.direct{color:var(--nh3);background:transparent;border:1px solid var(--nh3)}
.b.transferable{color:var(--ink);background:transparent;border:1px solid var(--line)}
.b.t-nh3{color:var(--nh3)}.b.t-joule{color:var(--joule)}.b.t-ald{color:var(--ald)}
.b.fav{background:var(--hi);color:var(--star)}
.card h2{font:600 16.5px/1.4 var(--serif);margin:0}
.card h2 a{color:var(--ink);text-decoration:none}
.card h2 a:hover{color:var(--link);text-decoration:underline}
.meta{color:var(--muted);font-size:12.5px;margin-top:4px}
.zh{margin-top:6px;font-size:13px;color:var(--ink);opacity:.85}
.zt{font:500 14.5px/1.45 var(--serif);margin-top:3px;color:var(--ink);opacity:.88}
.brief{margin:8px 0 0;font-size:14px;line-height:1.7;color:var(--ink)}
.brief.en{opacity:.82;font-size:13.5px}
.bl{display:inline-block;font:600 11px/1 var(--sans);color:var(--muted);border:1px solid var(--line);border-radius:4px;padding:2px 5px;margin-right:6px;vertical-align:1px}
.dim{color:var(--muted);font-size:12.5px}
.lab{margin-top:5px;font-size:12px;color:var(--muted)}
.note{margin-top:6px;font-size:13px;background:var(--hi);border-radius:6px;padding:5px 8px}
.acts{display:flex;gap:12px;margin-top:6px;align-items:center;flex-wrap:wrap}
.act{border:0;background:none;color:var(--muted);font:12.5px var(--sans);cursor:pointer;padding:0}
.act.on{color:var(--star)}
details{display:inline}
summary{cursor:pointer;color:var(--muted);font-size:12.5px;list-style:none}
summary::-webkit-details-marker{display:none}
summary::before{content:"▸ "} details[open] summary::before{content:"▾ "}
details p{margin:6px 0 0;font-size:13.5px}
.empty{color:var(--muted);text-align:center;padding:40px 0}
.more{display:block;margin:16px auto;border:1px solid var(--line);background:var(--card);color:var(--ink);border-radius:8px;padding:8px 18px;cursor:pointer;font:14px var(--sans)}
footer{margin-top:40px;border-top:1px solid var(--line);padding-top:14px;color:var(--muted);font-size:12.5px}
footer table{border-collapse:collapse;width:100%;margin-top:8px}
footer td{padding:3px 6px 3px 0;border-bottom:1px dashed var(--line);vertical-align:top}
.ok{color:#15803d}.err{color:#dc2626}
.menu{position:relative;display:inline-block}
.menu-list{position:absolute;right:0;top:110%;background:var(--card);border:1px solid var(--line);border-radius:9px;padding:4px;display:none;z-index:9;min-width:190px;box-shadow:0 6px 24px rgba(0,0,0,.12)}
.menu-list.open{display:block}
.menu-list button{display:block;width:100%;text-align:left;border:0;background:none;color:var(--ink);font:13.5px var(--sans);padding:7px 10px;border-radius:6px;cursor:pointer}
.menu-list button:hover{background:var(--soft)}
</style>
</head>
<body>
<div class="wrap">
<header>
  <div class="kicker">Frontier Radar</div>
  <h1 id="title">文献雷达</h1>
  <div class="sub" id="sub"></div>
  <nav class="views" role="tablist">
    <button class="view" data-v="latest">最新动态</button>
    <button class="view" data-v="month">按月浏览</button>
    <button class="view" data-v="fav">我的收藏</button>
  </nav>
</header>
<section class="months" id="months" hidden></section>
<div class="controls">
  <div class="chips" id="tiers"></div>
  <div class="row">
    <select id="kind" aria-label="类型"><option value="paper">论文</option><option value="wx">公众号推文</option><option value="all">论文 + 公众号</option></select>
    <select id="topic" aria-label="方向"></select>
    <select id="sort" aria-label="排序">
      <option value="seen">按收录时间</option><option value="date">按发表日期</option><option value="score">按相关度</option>
    </select>
    <input type="search" id="q" placeholder="搜索标题、简介、摘要、期刊、作者…" aria-label="搜索">
    <span class="menu"><button class="tool" id="exp">导出 ▾</button>
      <span class="menu-list" id="expmenu">
        <button data-f="ris">RIS（导入 Zotero / EndNote）</button>
        <button data-f="csv">CSV（Excel）</button>
      </span></span>
  </div>
  <div class="chips" id="flags" style="margin-top:6px"></div>
  <div class="hint" id="hint"></div>
</div>
<main id="list"></main>
<footer id="foot"></footer>
</div>
<script src="data/index.js"></script>
<script>
(function(){
const I = window.RADAR_INDEX;
const $ = s => document.querySelector(s);
if (!I){ $('#list').innerHTML = '<div class="empty">还没有数据。到 GitHub 仓库的 Actions 页运行一次“文献雷达”，几分钟后刷新这里。</div>'; return; }
const T = I.topics, DAY = 864e5, now = Date.now();
const TIERS = {cross:'⭐ 交叉', direct:'直接', transferable:'可迁移', uncertain:'待核', background:'背景'};
const RANK = {cross:5, direct:4, transferable:3, uncertain:2, background:1};
const excerpt = (t, n) => { t = String(t || '').replace(/^\s*(abstract|summary)[\s:.\-–]+/i, '').trim(); if (t.length <= n + 40) return t;
  const cut = t.slice(0, n + 60), m = [...cut.matchAll(/[.!?。](\s|$)/g)].map(x => x.index + 1).filter(e => e >= n * 0.5);
  return m.length ? cut.slice(0, m[0]) : t.slice(0, n).replace(/\s+\S*$/, '') + ' …'; };
const esc = s => String(s ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const store = { get(k, d){ try { return JSON.parse(localStorage.getItem(k)) ?? d; } catch(e){ return d; } },
                set(k, v){ try { localStorage.setItem(k, JSON.stringify(v)); } catch(e){} } };
let favs = store.get('radar.fav', {}), reads = store.get('radar.read', {});
const pref = store.get('radar.pref', {});
let view = pref.view || 'latest', month = pref.month || (I.months[0] && I.months[0].m), tierSel = pref.tier || 'key',
    topic = 'all', kind = pref.kind || 'paper', sort = null, q = '', flags = {}, limit = 100, allLoaded = false;
const cache = {}, waiting = {};
window.RADAR_LOAD = (m, items) => { cache[m] = items; (waiting[m] || []).forEach(f => f()); delete waiting[m]; };
function loadMonth(m){
  return new Promise(res => {
    if (cache[m]) return res(cache[m]);
    (waiting[m] = waiting[m] || []).push(() => res(cache[m]));
    const s = document.createElement('script'); s.src = 'data/m/' + m + '.js?v=' + I.ver;
    s.onerror = () => { cache[m] = []; res([]); }; document.head.appendChild(s);
  });
}

$('#title').textContent = I.title;
const imp = I.imported || {}, impTxt = [imp['旧站本地库'] && `旧站本地库 ${imp['旧站本地库']}`, imp['旧站云端'] && `旧站云端 ${imp['旧站云端']}`, imp['旧站公众号库'] && `公众号推文 ${imp['旧站公众号库']}`, imp['回填'] && `历史回填 ${imp['回填']}`].filter(Boolean).join('、');
$('#sub').innerHTML = `更新于 <b>${new Date(I.generated).toLocaleString('zh-CN',{hour12:false})}</b> · 共 <b>${I.total}</b> 篇（${esc(I.archiveFrom)} 起）· 近 7 天新增 <b>${I.week}</b> 篇${impTxt ? ' · 含 ' + esc(impTxt) : ''} · <a href="feed.xml">RSS</a>`;

$('#topic').innerHTML = '<option value="all">全部方向</option>' + Object.keys(T).map(id => `<option value="${id}">${esc(T[id])}（${I.topicCounts[id]||0}）</option>`).join('');
$('#topic').onchange = e => { topic = e.target.value; limit = 100; draw(); };
$('#kind').value = kind;
$('#kind').onchange = e => { kind = e.target.value; limit = 100; savePref(); draw(); };
$('#sort').onchange = e => { sort = e.target.value; draw(); };
$('#q').addEventListener('input', e => { q = e.target.value.trim().toLowerCase(); limit = 100; draw(); });
document.querySelectorAll('.view').forEach(b => b.onclick = () => setView(b.dataset.v));
$('#exp').onclick = e => { e.stopPropagation(); $('#expmenu').classList.toggle('open'); };
document.addEventListener('click', () => $('#expmenu').classList.remove('open'));
$('#expmenu').onclick = e => { const f = e.target.dataset.f; if (f) exportList(f); };

function savePref(){ store.set('radar.pref', {view, month, tier: tierSel, kind}); }
function base(){
  if (view === 'latest') return I.latest;
  if (view === 'fav'){
    const m = new Map(); I.favs.forEach(p => m.set(p.id, p)); Object.values(favs).forEach(p => m.set(p.id, p));
    return [...m.values()];
  }
  if (allLoaded && q) return Object.values(cache).flat();
  return cache[month] || [];
}
const tierFilters = [
  ['key','重点', p => RANK[p.tr] >= 3], ['cross','⭐ 交叉', p => p.tr === 'cross'], ['direct','直接', p => p.tr === 'direct'],
  ['transferable','可迁移', p => p.tr === 'transferable'], ['low','待核 / 背景', p => RANK[p.tr] <= 2], ['all','全部', () => true],
];
const flagFilters = [
  ['top','顶刊', p => p.top], ['watch','关注作者', p => p.w && p.w.length], ['pre','预印本', p => p.pre],
  ['unread','未读', p => !reads[p.id]], ['rb','公众号报道过', p => p.rb && p.rb.length], ['old','旧站收录', p => (p.o||'').startsWith('旧站')], ['note','有笔记', p => p.n],
];
function filtered(list, skipTier){
  let ps = list;
  if (!skipTier) ps = ps.filter(tierFilters.find(t => t[0] === tierSel)[2]);
  if (topic !== 'all') ps = ps.filter(p => p.tp.includes(topic));
  if (kind === 'paper') ps = ps.filter(p => !p.wx); else if (kind === 'wx') ps = ps.filter(p => p.wx);
  for (const [k,,f] of flagFilters) if (flags[k]) ps = ps.filter(f);
  if (q) ps = ps.filter(p => (p.t + ' ' + (p.ab||'') + ' ' + p.s + ' ' + p.au.join(' ') + ' ' + (p.zh||'') + ' ' + (p.zt||'') + ' ' + (p.zb||'') + ' ' + (p.ob||'') + ' ' + (p.n||'')).toLowerCase().includes(q));
  const s = sort || (view === 'month' ? 'date' : 'seen');
  const key = s === 'date' ? (p => p.d) : s === 'seen' ? (p => p.f) : (p => String(1000 + p.sc));
  return ps.slice().sort((a, b) => key(b) < key(a) ? -1 : key(b) > key(a) ? 1 : (RANK[b.tr] - RANK[a.tr]) || (b.sc - a.sc));
}
function drawChips(){
  const list = base(), pre = filtered(list, true);
  $('#tiers').innerHTML = tierFilters.map(([id, name, f]) =>
    `<button class="chip" data-t="${id}" aria-pressed="${id === tierSel}">${name}<span class="n">${pre.filter(f).length}</span></button>`).join('');
  $('#flags').innerHTML = flagFilters.map(([id, name]) => `<button class="chip sm" data-f="${id}" aria-pressed="${!!flags[id]}">${name}</button>`).join('')
    + (view === 'month' ? `<button class="chip sm" id="all-months">${allLoaded ? '✓ 已载入全部月份' : '在全部月份中搜索'}</button>` : '');
}
$('#tiers').onclick = e => { const b = e.target.closest('.chip'); if (!b) return; tierSel = b.dataset.t; limit = 100; savePref(); draw(); };
$('#flags').onclick = async e => {
  const b = e.target.closest('.chip'); if (!b) return;
  if (b.id === 'all-months'){ b.textContent = '载入中…'; await Promise.all(I.months.map(m => loadMonth(m.m))); allLoaded = true; draw(); return; }
  flags[b.dataset.f] = !flags[b.dataset.f]; limit = 100; draw();
};

function drawMonths(){
  const el = $('#months'); el.hidden = view !== 'month';
  if (view !== 'month') return;
  const years = {}; I.months.forEach(m => (years[m.m.slice(0,4)] = years[m.m.slice(0,4)] || []).push(m));
  el.innerHTML = Object.keys(years).sort((a, b) => (a === 'unkn') - (b === 'unkn') || b.localeCompare(a)).map(y => `<div class="yr"><b>${y === 'unkn' ? '其他' : y}</b><div class="mm">` +
    years[y].map(m => `<button class="mon" data-m="${m.m}" aria-pressed="${m.m === month}" title="${esc(m.label)}：共 ${m.n} 条（其中公众号 ${m.wx || 0}），重点论文 ${m.key}，交叉 ${m.cross}">${m.m.endsWith('-00') ? '月份不详' : m.m === 'unknown' ? '日期不详' : (+m.m.slice(5)) + '月'}<small>${m.n}${m.cross ? '<span class="x"> ⭐' + m.cross + '</span>' : ''}</small></button>`).join('') + '</div></div>').join('');
}
$('#months').onclick = async e => { const b = e.target.closest('.mon'); if (!b) return; month = b.dataset.m; limit = 100; savePref(); drawMonths(); await loadMonth(month); draw(); };

async function setView(v){
  view = v; limit = 100; savePref();
  document.querySelectorAll('.view').forEach(b => b.setAttribute('aria-pressed', b.dataset.v === v));
  sort = null; $('#sort').value = v === 'month' ? 'date' : 'seen';
  if (v === 'month' && !I.months.some(m => m.m === month)) month = I.months[0] && I.months[0].m;
  drawMonths();
  if (v === 'month' && month) await loadMonth(month);
  draw();
}

function brief(p){
  if (p.zb) return `<p class="brief"><span class="bl">简介</span>${esc(p.zb)}</p>`;
  if (p.bt && !p.ab) return `<p class="brief dim">当前来源尚未获取可用摘要，暂仅翻译标题；请点标题看原文</p>`;
  if (p.ab) return `<p class="brief${p.wx ? '' : ' en'}"><span class="bl">${p.wx ? '摘要' : '摘要节选'}</span>${esc(excerpt(p.ab, 220))}</p>`;
  return '';
}
function card(p){
  const c = p.tr === 'cross' ? 'var(--star)' : `var(--${p.tp[0] || 'line'})`;
  const isNew = p.f && now - Date.parse(p.f) < 3*DAY && !(p.o||'').startsWith('旧站') && p.o !== '回填';  // imported items keep their old dates
  const b = [];
  if (isNew) b.push('<span class="b new">NEW</span>');
  b.push(`<span class="b ${p.tr}">${TIERS[p.tr] || p.tr}</span>`);
  p.tp.forEach(t => b.push(`<span class="b t-${esc(t)}">${esc(T[t]||t)}</span>`));
  if (p.top) b.push('<span class="b">顶刊</span>');
  if (p.pre) b.push('<span class="b">预印本</span>');
  if (p.pp) b.push(`<span class="b" title="之前收录的是预印本版本">已正式发表（原 ${esc(p.pp)}）</span>`);
  if (p.wx) b.push('<span class="b">公众号</span>');
  if (p.rb) b.push(`<span class="b fav" title="${esc(p.rb.join('、'))}">公众号报道 ×${p.rb.length}</span>`);
  (p.w || []).forEach(w => b.push(`<span class="b">关注 · ${esc(w)}</span>`));
  if (p.fv) b.push('<span class="b fav">旧站收藏</span>');
  if (p.llm === 0) b.push('<span class="b">AI 判定低相关</span>');
  const au = p.au.length ? ' · ' + esc(p.au.slice(0,3).join(', ')) + (p.au.length > 3 ? ' 等' : '') : '';
  const seen = p.f ? ` · ${esc(p.o && p.o !== '日常' ? p.o : '收录')} ${new Date(p.f).toLocaleDateString('zh-CN')}` : '';
  const fav = !!favs[p.id];
  return `<article class="card${reads[p.id] ? ' read' : ''}" style="--c:${c}">
    <div class="badges">${b.join('')}</div>
    <h2><a href="${esc(p.u)}" target="_blank" rel="noopener" data-read="${esc(p.id)}">${esc(p.t)}</a></h2>
    ${p.zt ? `<div class="zt">${esc(p.zt)}</div>` : ''}
    <div class="meta">${p.wx ? '公众号「' + esc(p.s) + '」' : esc(p.s || '未知来源')}${p.d ? (p.wx ? ' · 发布 ' : ' · 发表 ') + esc(p.d) : ''}${au}${seen}${p.mt ? ' · 原论文 ' + p.mt.map(d => `<a href="https://doi.org/${esc(d)}" target="_blank" rel="noopener">${esc(d)}</a>`).join(' ') : ''}</div>
    ${brief(p)}
    ${p.zh && !p.zb ? `<div class="zh">${esc(p.zh)}</div>` : ''}${p.ob ? `<div class="lab">规则标签：${esc(p.ob)}</div>` : ''}
    ${p.n ? `<div class="note">旧站笔记：${esc(p.n)}</div>` : ''}
    <div class="acts">
      <button class="act${fav ? ' on' : ''}" data-fav="${esc(p.id)}">${fav ? '★ 已收藏' : '☆ 收藏'}</button>
      <button class="act" data-rd="${esc(p.id)}">${reads[p.id] ? '标为未读' : '标为已读'}</button>
      ${p.ab && (p.zb || excerpt(p.ab, 220).length < p.ab.length) ? `<details><summary>${p.wx ? '全文摘要' : '英文摘要'}</summary><p>${esc(p.ab)}</p></details>` : ''}
    </div>
  </article>`;
}
let shown = [];
function draw(){
  drawChips();
  const ps = filtered(base());
  shown = ps;
  const el = $('#list');
  const m = I.months.find(x => x.m === month);
  $('#hint').textContent = view === 'latest' ? '最近 3 周新收录的论文（首次部署时显示最近发表的论文）。'
    : view === 'fav' ? '收藏保存在这台设备的浏览器里；带“旧站收藏/笔记”的来自旧网站。'
    : (allLoaded && q ? `在全部 ${I.months.length} 个月里搜索。` : m ? `${m.label}：论文 ${m.n - (m.wx || 0)} 篇（重点 ${m.key}，交叉 ${m.cross}）${m.wx ? '，公众号推文 ' + m.wx + ' 篇' : ''}，按发表日期归档。` : '');
  if (!ps.length){ el.innerHTML = '<div class="empty">这里没有符合条件的论文。试试切换到“全部”或换个月份。</div>'; return; }
  let out = '', last = '';
  const s = sort || (view === 'month' ? 'date' : 'seen');
  ps.slice(0, limit).forEach(p => {
    const g = s === 'seen' ? (p.f ? new Date(p.f).toLocaleDateString('zh-CN',{year:'numeric',month:'long',day:'numeric',weekday:'short'}) + ' 收录' : '收录日期不详')
            : s === 'date' ? (p.d ? (p.d.length >= 10 ? p.d.slice(0,4) + '年' + (+p.d.slice(5,7)) + '月' + (+p.d.slice(8,10)) + '日' : p.d.length >= 7 ? p.d.slice(0,4) + '年' + (+p.d.slice(5,7)) + '月（日不详）' : p.d + '年') + ' 发表' : '发表日期不详') : '';
    if (g && g !== last){ out += `<div class="day">${g}</div>`; last = g; }
    out += card(p);
  });
  if (ps.length > limit) out += `<button class="more" id="more">再显示 ${Math.min(100, ps.length - limit)} 篇（共 ${ps.length}）</button>`;
  el.innerHTML = out;
  const mo = $('#more'); if (mo) mo.onclick = () => { limit += 100; draw(); };
}
$('#list').addEventListener('click', e => {
  const f = e.target.closest('[data-fav]'), r = e.target.closest('[data-rd]'), a = e.target.closest('[data-read]');
  const find = id => shown.find(p => p.id === id);
  if (f){ const id = f.dataset.fav; if (favs[id]) delete favs[id]; else favs[id] = find(id); store.set('radar.fav', favs); draw(); }
  if (r){ const id = r.dataset.rd; if (reads[id]) delete reads[id]; else reads[id] = 1; store.set('radar.read', reads); draw(); }
  if (a){ reads[a.dataset.read] = 1; store.set('radar.read', reads); setTimeout(draw, 300); }
});

function download(name, text, type){
  const blob = new Blob([text], {type}); const u = URL.createObjectURL(blob);
  const a = document.createElement('a'); a.href = u; a.download = name; document.body.appendChild(a); a.click();
  setTimeout(() => { URL.revokeObjectURL(u); a.remove(); }, 500);
}
function exportList(fmt){
  const ps = shown, stamp = new Date().toISOString().slice(0,10);
  if (!ps.length) return;
  if (fmt === 'ris'){
    const out = ps.map(p => {
      const l = [`TY  - ${p.pre ? 'GEN' : 'JOUR'}`, `TI  - ${p.t}`];
      p.au.forEach(a => l.push(`AU  - ${a}`));
      if (p.s) l.push(`JO  - ${p.s}`);
      if (p.d) { l.push(`PY  - ${p.d.slice(0,4)}`); l.push(`DA  - ${p.d.replace(/-/g,'/')}`); }
      const doi = p.id.startsWith('10.') ? p.id : ''; if (doi) l.push(`DO  - ${doi}`);
      l.push(`UR  - ${p.u}`);
      if (p.ab) l.push(`AB  - ${p.ab}`);
      if (p.zt) l.push(`TT  - ${p.zt}`);
      l.push(`KW  - 文献雷达:${TIERS[p.tr] || p.tr}`); p.tp.forEach(t => l.push(`KW  - ${T[t] || t}`));
      if (p.zb) l.push(`N1  - 中文简介（AI）：${p.zb}`);
      if (p.ob) l.push(`N1  - ${p.ob}`);
      if (p.n) l.push(`N1  - 旧站笔记：${p.n}`);
      l.push('ER  - '); return l.join('\r\n');
    }).join('\r\n\r\n');
    download(`文献雷达-${stamp}.ris`, out, 'application/x-research-info-systems');
  } else {
    const cols = ['相关度','方向','标题','中文标题','中文简介','期刊','发表日期','收录时间','DOI/链接','作者','规则说明','旧站笔记'];
    const cell = v => { let s = String(v ?? ''); if (/^[=+\-@]/.test(s)) s = "'" + s; return '"' + s.replace(/"/g,'""') + '"'; };
    const rows = ps.map(p => [TIERS[p.tr]||p.tr, p.tp.map(t => T[t]||t).join('+'), p.t, p.zt||'', p.zb||'', p.s, p.d, p.f, p.u, p.au.join('; '), p.ob||'', p.n||''].map(cell).join(','));
    download(`文献雷达-${stamp}.csv`, '﻿' + cols.join(',') + '\r\n' + rows.join('\r\n'), 'text/csv;charset=utf-8');
  }
}

function foot(){
  const s = I.status || {};
  const rows = Object.entries(s.sources || {}).map(([k,v]) =>
    `<tr><td>${esc(k)}</td><td class="${v.err ? 'err' : 'ok'}">${v.err ? '失败 ' + esc(v.err) : '正常'}</td><td>${v.n ?? 0} 条</td></tr>`).join('');
  const ex = Object.entries(s).filter(([k]) => !['sources','push','at','llm'].includes(k)).map(([k,v]) => `${esc(k)}：${esc(v)}`).join('；');
  $('#foot').innerHTML = `<div>最近一次运行（${s.at ? new Date(s.at).toLocaleString('zh-CN',{hour12:false}) : '—'}）：${ex || '—'}；推送：${esc((s.push||[]).join(', ') || '无')}。</div>
    <div style="margin-top:6px">${I.summaryProgress ? `重点论文 ${I.summaryProgress.key.total} 篇：有中文简介 ${I.summaryProgress.key.briefs} 篇，缺摘要仅译标题 ${I.summaryProgress.key.titleOnly} 篇，待处理 ${I.summaryProgress.key.pending} 篇。全库论文 ${I.summaryProgress.all.total} 篇：有简介 ${I.summaryProgress.all.briefs} 篇，仅译标题 ${I.summaryProgress.all.titleOnly} 篇，待处理 ${I.summaryProgress.all.pending} 篇。简介依据获取到的摘要生成，请以原文为准` : `中文内容已处理 ${I.briefs || 0} 篇`}。</div>
    <div style="margin-top:6px">相关度：⭐交叉＝合成氨同时涉及焦耳热/程序升温或 ALD；直接＝热催化合成氨；可迁移＝焦耳热/ALD 用于其他反应或材料；待核＝路线不明；背景＝电/光/等离子体制氨、氨分解等。规则筛选，未读全文，请以原文为准。</div>
    ${rows ? `<table>${rows}</table>` : ''}`;
}
setView(view); foot();
})();
</script>
</body>
</html>
"""
