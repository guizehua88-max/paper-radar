"""Optional AI double-check (off unless LLM_API_KEY / LLM_BASE_URL / LLM_MODEL are set).

Any OpenAI-compatible endpoint works (DeepSeek, OpenAI, 硅基流动, Kimi, ...).
It scores each rule-matched paper 0-3 for *your* topic and writes a one-line Chinese gist.
"""
from __future__ import annotations

import json
import os
import re

from . import net


def enabled() -> bool:
    return all(os.environ.get(k) for k in ("LLM_API_KEY", "LLM_BASE_URL", "LLM_MODEL"))


def review(papers: list[dict], profile: str, batch: int = 10) -> int:
    """Mutates papers in place: adds llm_score (0-3) and zh. Returns how many were reviewed."""
    if not enabled() or not papers:
        return 0
    base = os.environ["LLM_BASE_URL"].rstrip("/")
    url = base + ("/chat/completions" if not base.endswith("/chat/completions") else "")
    headers = {"Authorization": f"Bearer {os.environ['LLM_API_KEY']}"}
    done = 0
    for i in range(0, len(papers), batch):
        chunk = papers[i:i + batch]
        lines = []
        for j, p in enumerate(chunk):
            lines.append(f"[{j}] 标题: {p['title']}\n来源: {p.get('source','')}\n摘要: {(p.get('abstract') or '（无摘要）')[:900]}")
        prompt = (
            f"研究背景：\n{profile.strip()}\n\n"
            "请判断下面每篇论文与我课题的相关度并给中文一句话要点。相关度 0=无关 1=沾边 2=相关 3=非常相关。\n"
            "只输出 JSON 数组，例如 [{\"i\":0,\"score\":2,\"zh\":\"……\"}]，zh 不超过 40 字。\n\n" + "\n\n".join(lines)
        )
        try:
            resp = net.post_json(url, {
                "model": os.environ["LLM_MODEL"],
                "messages": [{"role": "user", "content": prompt}],
                "temperature": 0.1,
            }, headers=headers, timeout=120)
            text = resp["choices"][0]["message"]["content"]
            arr = json.loads(re.search(r"\[.*\]", text, re.S).group(0))
            for r in arr:
                k = int(r.get("i", -1))
                if 0 <= k < len(chunk):
                    chunk[k]["llm_score"] = max(0, min(3, int(r.get("score", 1))))
                    chunk[k]["zh"] = str(r.get("zh", ""))[:80]
                    done += 1
        except Exception as e:  # AI is a bonus; never let it break the run
            print(f"[llm] batch {i // batch} skipped: {type(e).__name__}: {e}")
    return done
