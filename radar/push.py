"""WeChat push. Configure at least one channel as a GitHub Actions secret:
  WXPUSHER_SPT        WxPusher 极简推送（免费、无需注册：微信扫码得到 SPT_ 开头的 token）——推荐
  PUSHPLUS_TOKEN      pushplus.plus（2024-08 起要求付费实名认证才能发送）
  SERVERCHAN_SENDKEY  Server酱（sct... 或 Server酱³ 的 sctp... 均可）
  WECOM_WEBHOOK       企业微信群机器人 webhook 地址
"""
from __future__ import annotations

import os
import re

from . import net

TIER_HEAD = {"cross": "⭐ 交叉（合成氨 × 焦耳热/ALD）", "direct": "直接相关（合成氨）", "transferable": "可迁移方法（焦耳热 / ALD）",
             "uncertain": "待核", "background": "邻近背景"}
TOPIC_NAMES: dict[str, str] = {}


def channels() -> list[str]:
    return [k for k in ("WXPUSHER_SPT", "PUSHPLUS_TOKEN", "SERVERCHAN_SENDKEY", "WECOM_WEBHOOK") if os.environ.get(k, "").strip()]


def send(title: str, markdown: str) -> list[str]:
    results = []
    if os.environ.get("RADAR_DRY_RUN") or not channels():
        print("=" * 60 + f"\n[dry-run push] {title}\n{markdown}\n" + "=" * 60)
        return ["dry-run"]
    spt = os.environ.get("WXPUSHER_SPT", "").strip()
    if spt:
        try:
            r = net.post_json("https://wxpusher.zjiecode.com/api/send/message/simple-push",
                              {"content": f"## {title}\n\n{markdown}"[:39000], "summary": title[:20],
                               "contentType": 3, "spt": spt})
            ok = r.get("code") == 1000 or r.get("success") is True
            results.append("wxpusher:ok" if ok else f"wxpusher:fail {r.get('code')} {r.get('msg')}")
        except Exception as e:
            results.append(f"wxpusher:error {type(e).__name__}: {e}")
    tok = os.environ.get("PUSHPLUS_TOKEN", "").strip()
    if tok:
        try:
            r = net.post_json("https://www.pushplus.plus/send",
                              {"token": tok, "title": title, "content": markdown, "template": "markdown"})
            results.append("pushplus:ok" if r.get("code") == 200 else f"pushplus:fail {r.get('code')} {r.get('msg')}")
        except Exception as e:
            results.append(f"pushplus:error {type(e).__name__}: {e}")
    key = os.environ.get("SERVERCHAN_SENDKEY", "").strip()
    if key:
        try:
            m = re.match(r"sctp(\d+)t", key)
            url = f"https://{m.group(1)}.push.ft07.com/send/{key}.send" if m else f"https://sctapi.ftqq.com/{key}.send"
            r = net.post_form(url, {"title": title[:32], "desp": markdown})
            ok = r.get("code") == 0 or (r.get("data") or {}).get("errno") == 0
            results.append("serverchan:ok" if ok else f"serverchan:fail {r}")
        except Exception as e:
            results.append(f"serverchan:error {type(e).__name__}: {e}")
    hook = os.environ.get("WECOM_WEBHOOK", "").strip()
    if hook:
        try:
            r = net.post_json(hook, {"msgtype": "markdown", "markdown": {"content": f"**{title}**\n{markdown}"[:4000]}})
            results.append("wecom:ok" if r.get("errcode") == 0 else f"wecom:fail {r}")
        except Exception as e:
            results.append(f"wecom:error {type(e).__name__}: {e}")
    return results


def _line(i: int, p: dict) -> str:
    tags = "+".join(TOPIC_NAMES.get(t, t) for t in p["topics"])
    extra = []
    if p.get("watch"):
        extra.append("关注作者:" + "/".join(p["watch"]))
    if p.get("top"):
        extra.append("顶刊")
    if p.get("kind") == "preprint":
        extra.append("预印本")
    meta = f"{p.get('source') or '未知来源'} · {p.get('date') or ''} · {tags}"
    if extra:
        meta += " · " + " · ".join(extra)
    s = f"{i}. [{p['title']}]({p['url']})\n   {meta}"
    if p.get("zh"):
        s += f"\n   {p['zh']}"
    else:
        from .site import _useful
        label = "｜".join(x for x in (_useful(p.get("zh_obj", "")), _useful(p.get("zh_method", ""))) if x)
        if label:
            s += f"\n   {label}"
    return s


def digest(items: list[dict], site_url: str, header: str = "") -> str:
    parts = [header] if header else []
    n = 1
    for tier in ("cross", "direct", "transferable", "uncertain", "background"):
        group = [p for p in items if p.get("tier") == tier]
        if not group:
            continue
        parts.append(f"**{TIER_HEAD[tier]}**")
        for p in group:
            parts.append(_line(n, p))
            n += 1
    if site_url:
        parts.append(f"\n[👉 打开文献雷达查看全部]({site_url})")
    return "\n\n".join(parts)
