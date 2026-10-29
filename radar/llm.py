"""Large-language-model helpers. Everything here is optional: without a key the radar works exactly as before.

中文简介（推荐，免费）
  ZHIPU_API_KEY      智谱开放平台 API Key（bigmodel.cn），默认用免费模型 glm-4.7-flash，失败时换 glm-4-flash-250414
或者任意 OpenAI 兼容接口
  LLM_API_KEY + LLM_BASE_URL + LLM_MODEL    （只填 LLM_API_KEY 时按智谱处理）

summarize()  给每篇论文写中文标题 + 2–3 句中文简介（只依据标题和摘要；没有摘要时只概括主题并标明“仅据标题”）
review()     旧功能：给相关度打 0–3 分（只在配置了 LLM_BASE_URL + LLM_MODEL 时启用）
"""
from __future__ import annotations

import json
import os
import re
import time

from . import net

ZHIPU_BASE = "https://open.bigmodel.cn/api/paas/v4"
ZHIPU_MODELS = ["glm-4.7-flash", "glm-4-flash-250414"]
_CJK = re.compile(r"[一-鿿]")


def provider() -> dict | None:
    """Which endpoint/model to use for summaries, or None when no key is configured."""
    key = os.environ.get("LLM_API_KEY", "").strip()
    if key:
        base = os.environ.get("LLM_BASE_URL", "").strip() or ZHIPU_BASE
        model = os.environ.get("LLM_MODEL", "").strip()
        models = [model] if model else (ZHIPU_MODELS if "bigmodel" in base else [])
        if models:
            return {"key": key, "base": base, "models": models, "name": "自定义接口" if model else "智谱"}
    key = os.environ.get("ZHIPU_API_KEY", "").strip()
    if key:
        return {"key": key, "base": ZHIPU_BASE, "models": list(ZHIPU_MODELS), "name": "智谱"}
    return None


def enabled() -> bool:
    """The old relevance review only runs with an explicitly configured endpoint + model."""
    return all(os.environ.get(k) for k in ("LLM_API_KEY", "LLM_BASE_URL", "LLM_MODEL"))


def _url(base: str) -> str:
    base = base.rstrip("/")
    return base if base.endswith("/chat/completions") else base + "/chat/completions"


class KeyError_(RuntimeError):
    """Authentication problem: no point trying again in this run."""


def chat(prov: dict, prompt: str, *, system: str = "", max_tokens: int = 2400) -> str:
    """One chat completion. Tries the configured models in order; returns the reply text."""
    msgs = ([{"role": "system", "content": system}] if system else []) + [{"role": "user", "content": prompt}]
    last = ""
    for model in list(prov["models"]):
        payload = {"model": model, "messages": msgs, "temperature": 0.2, "max_tokens": max_tokens}
        if "bigmodel" in prov["base"] or "z.ai" in prov["base"]:
            payload["thinking"] = {"type": "disabled"}
        try:
            resp = net.post_json_retry(_url(prov["base"]), payload, timeout=180,
                                       headers={"Authorization": f"Bearer {prov['key']}"}, retries=4)
        except net.FetchError as e:
            last = str(e)
            if re.search(r"HTTP 40[13]", last):
                raise KeyError_(last)
            if "thinking" in payload and "HTTP 400" in last:
                payload.pop("thinking")
                try:
                    resp = net.post_json_retry(_url(prov["base"]), payload, timeout=180,
                                               headers={"Authorization": f"Bearer {prov['key']}"}, retries=2)
                except net.FetchError as e2:
                    last = str(e2)
                    continue
            else:
                continue  # e.g. model not available on this account -> next model
        try:
            text = resp["choices"][0]["message"]["content"] or ""
        except Exception:
            last = f"unexpected answer: {str(resp)[:200]}"
            continue
        if model != prov["models"][0]:  # remember the model that works
            prov["models"].remove(model)
            prov["models"].insert(0, model)
        prov["used"] = model
        return re.sub(r"<think>.*?</think>", "", text, flags=re.S).strip()
    raise net.FetchError(last or "no model answered")


def usable_abstract(p: dict) -> str:
    ab = (p.get("abstract") or "").strip()
    if len(ab) < 150 or ab.lower()[:80] == (p.get("title") or "").lower()[:80]:
        return ""
    return ab


SYSTEM = ("你是催化、材料与化工方向的科研助理，给研究生写论文速览。只依据给出的标题和摘要，"
          "不得编造摘要里没有的方法、数据或结论。")
INSTRUCTIONS = """为下面每篇论文写两项：
- t：标题的中文译名，准确简洁；常见缩写保留英文（如 NH3、ALD、FJH、DFT、Ru、MOF）
- b：中文简介
  · 有摘要：2–3 句，80–150 字。依次说明研究对象/体系、关键方法（制备、加热方式、表征或计算）、主要结果；摘要里有关键数字（温度、压力、产率、选择性、稳定时长等）就写上
  · 摘要写着“（无摘要）”：只写 1 句、不超过 50 字，概括标题反映的研究主题，不要推测结果或数据
  · 直接写内容，不要用“本文”“该研究”“作者”开头，不要加评价
只输出 JSON 数组，不要任何其他文字，格式：[{"i":0,"t":"…","b":"…"}]
"""


def _prompt(chunk: list[dict]) -> str:
    parts = []
    for j, p in enumerate(chunk):
        ab = usable_abstract(p)
        parts.append(f"[{j}] 标题：{p['title']}\n期刊：{p.get('source') or '未知'}\n摘要：{ab[:1800] if ab else '（无摘要）'}")
    return INSTRUCTIONS + "\n" + "\n\n".join(parts)


def _parse(text: str) -> list[dict]:
    m = re.search(r"\[.*\]", text, re.S)
    if not m:
        raise ValueError("no JSON array in reply")
    raw = m.group(0)
    try:
        arr = json.loads(raw)
    except json.JSONDecodeError:
        arr = json.loads(re.sub(r",\s*([\]}])", r"\1", raw))  # trailing commas
    return [x for x in arr if isinstance(x, dict)]


def _apply(p: dict, r: dict, model: str) -> bool:
    t = re.sub(r"\s+", " ", str(r.get("t", ""))).strip().strip("“”\"")
    b = re.sub(r"\s+", " ", str(r.get("b", ""))).strip().strip("“”\"")
    if not _CJK.search(b) or len(b) < 8:
        return False
    from_title = not usable_abstract(p)
    if from_title and len(b) > 90:
        b = b[:88].rstrip("，、；") + "…"
    p["zh_brief"] = b[:400]
    if _CJK.search(t):
        p["zh_title"] = t[:200]
    p["brief_from"] = "title" if from_title else "abstract"
    p["brief_by"] = model
    return True


def needs_brief(p: dict) -> bool:
    if p.get("kind") == "wechat":
        return False
    if not p.get("zh_brief"):
        return True
    return p.get("brief_from") == "title" and bool(usable_abstract(p))  # an abstract turned up later -> redo


def summarize(papers: list[dict], *, deadline: float, batch: int = 6) -> dict:
    """Writes zh_title / zh_brief into the paper dicts (in place) until the deadline. Never raises."""
    stats = {"done": 0, "failed": 0, "err": "", "model": ""}
    prov = provider()
    todo = [p for p in papers if needs_brief(p)]
    if not prov or not todo:
        return stats
    i = 0
    strikes = 0
    while i < len(todo) and time.time() < deadline:
        chunk = todo[i:i + batch]
        i += len(chunk)
        try:
            arr = _parse(chat(prov, _prompt(chunk), system=SYSTEM))
            ok = set()
            for r in arr:
                try:
                    k = int(r.get("i", -1))
                except (TypeError, ValueError):
                    continue
                if 0 <= k < len(chunk) and k not in ok and _apply(chunk[k], r, prov.get("used", "")):
                    ok.add(k)
            stats["done"] += len(ok)
            stats["failed"] += len(chunk) - len(ok)
            strikes = 0
        except KeyError_ as e:
            stats["err"] = f"API Key 无效或没有权限：{str(e)[:160]}"
            stats["failed"] += len(chunk)
            break
        except Exception as e:
            stats["err"] = f"{type(e).__name__}: {str(e)[:160]}"
            stats["failed"] += len(chunk)
            strikes += 1
            if strikes >= 3:  # service is down / rate-limited: try again next run
                break
            time.sleep(10)
    stats["model"] = prov.get("used", "")
    stats["left"] = sum(1 for p in todo if needs_brief(p))
    return stats


def review(papers: list[dict], profile: str, batch: int = 10) -> int:
    """Mutates papers in place: adds llm_score (0-3) and zh. Returns how many were reviewed."""
    if not enabled() or not papers:
        return 0
    prov = provider()
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
            arr = _parse(chat(prov, prompt))
            for r in arr:
                k = int(r.get("i", -1))
                if 0 <= k < len(chunk):
                    chunk[k]["llm_score"] = max(0, min(3, int(r.get("score", 1))))
                    chunk[k]["zh"] = str(r.get("zh", ""))[:80]
                    done += 1
        except Exception as e:  # AI is a bonus; never let it break the run
            print(f"[llm] batch {i // batch} skipped: {type(e).__name__}: {e}")
    return done
