"""Offline response-parser regressions: no credentials, network or publication."""
import copy
import json
from pathlib import Path
import sys
import time
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from radar import llm


class SummaryParserTests(unittest.TestCase):
    def test_title_prompt_contains_full_glossary_and_no_omission_requirement(self):
        prompt = llm._prompt([{"title": "Anti-perovskite catalyst with a complete subtitle"}])
        terms = {"anti-perovskite": "反钙钛矿", "electride": "电子化合物（电子盐）",
                 "hydride": "氢化物", "oxynitride": "氮氧化物", "flash Joule heating": "闪蒸焦耳热",
                 "carbothermal shock": "碳热冲击", "atomic layer deposition": "原子层沉积",
                 "single-atom": "单原子", "promoter": "助剂", "support": "载体",
                 "chemical looping": "化学链", "Haber–Bosch": "哈伯-博施", "spillover": "溢流",
                 "metal–support interaction": "金属-载体相互作用"}
        for english, chinese in terms.items():
            self.assertIn(f"{english}：{chinese}", prompt)
        self.assertIn("完整翻译，不得省略任何短语", prompt)
        self.assertIn("Anti-perovskite catalyst with a complete subtitle", prompt)

    def test_v4_targets_only_bad_titles_without_rerunning_completed_v3(self):
        abstract = "A real abstract about metal nitride ammonia synthesis. " * 6
        good = [{"title": "Catalysts", "zh_title": "催化剂", "abstract": abstract,
                 "zh_brief": "已有真实简介", "brief_from": "abstract", "brief_v": 3},
                {"title": "Catalysts", "zh_title": "催化剂", "brief_from": "title", "brief_v": 3}]
        self.assertEqual(llm.BRIEF_V, 4)
        self.assertFalse(any(llm.needs_brief(p) for p in good))
        target = {**good[0], "zh_title": "抗钙钛矿催化剂"}
        self.assertTrue(llm.needs_brief(target))
        self.assertTrue(llm.needs_brief({"title": "An unprocessed new paper"}))
        self.assertFalse(llm.needs_brief({**target, "kind": "wechat"}))

    def test_targeted_title_repair_preserves_existing_brief_and_metadata(self):
        p = {"title": "Anti-perovskite catalysts", "zh_title": "抗钙钛矿催化剂",
             "abstract": "A real abstract about ammonia synthesis. " * 6,
             "zh_brief": "已有可信中文简介，内容应逐字保留。", "brief_from": "abstract",
             "brief_by": "original-model", "brief_v": 3}
        expected = {**p, "zh_title": "反钙钛矿催化剂", "brief_v": 4}
        self.assertTrue(llm._apply(p, {"t": "反钙钛矿催化剂", "b": "不应覆盖原有简介"}, "fixture"))
        self.assertEqual(p, expected)
        self.assertFalse(llm.needs_brief(p))

    def test_failed_targeted_repair_stays_pending_and_does_not_mutate_paper(self):
        for title in ("抗钙钛矿催化剂", "催化剂", "An untranslated title"):
            with self.subTest(title=title):
                p = {"title": "Anti-perovskite catalysts", "zh_title": "抗钙钛矿催化剂",
                     "brief_v": 3, "brief_from": "title"}
                before = copy.deepcopy(p)
                self.assertFalse(llm._apply(p, {"t": title, "b": ""}, "fixture"))
                self.assertEqual(p, before)
                self.assertTrue(llm.needs_brief(p))

    def test_long_translated_titles_keep_their_final_phrase(self):
        title = "反钙钛矿催化剂的制备及研究：" + "完整限定条件与关键方法，" * 22 + "并保留这个末尾短语"
        self.assertGreater(len(title), 200)
        for old_title in (None, "抗钙钛矿催化剂"):
            with self.subTest(old_title=old_title):
                p = {"title": "Anti-perovskite catalyst with a very long subtitle"}
                if old_title:
                    p.update(zh_title=old_title, brief_from="title", brief_v=3)
                self.assertTrue(llm._apply(p, {"t": title, "b": ""}, "fixture"))
                self.assertEqual(p["zh_title"], title)

    def test_mixed_summary_flow_rewrites_only_the_target_title(self):
        abstract = "A real abstract with catalyst preparation and reaction results. " * 6
        good = {"title": "Unchanged completed paper", "zh_title": "保留的正确标题", "abstract": abstract,
                "zh_brief": "原有摘要简介", "brief_from": "abstract", "brief_v": 3}
        target = {"title": "Anti-perovskite catalysts", "zh_title": "抗钙钛矿催化剂", "abstract": abstract,
                  "zh_brief": "目标论文原有简介", "brief_from": "abstract", "brief_v": 3}
        before = copy.deepcopy(good)
        reply = '[{"i":0,"t":"反钙钛矿催化剂","b":""}]'
        with patch.object(llm, "provider", return_value={"used": "fixture"}), patch.object(llm, "chat", return_value=reply) as chat:
            stats = llm.summarize([good, target], deadline=time.time() + 5)
        self.assertEqual(good, before)
        self.assertEqual(target["zh_brief"], "目标论文原有简介")
        self.assertEqual(target["zh_title"], "反钙钛矿催化剂")
        self.assertEqual(stats["done"], 1)
        self.assertEqual(stats["left"], 0)
        self.assertNotIn("Unchanged completed paper", chat.call_args.args[1])
        self.assertIn("本篇仅修正标题", chat.call_args.args[1])

    def test_old_title_based_brief_cleanup_is_unchanged(self):
        p = {"title": "Catalysts", "zh_title": "已翻译标题", "zh_brief": "旧版无依据简介",
             "brief_from": "title", "brief_v": 2}
        self.assertEqual(llm.drop_title_briefs([p]), 1)
        self.assertNotIn("zh_brief", p)
        self.assertEqual(p["zh_title"], "已翻译标题")
        self.assertFalse(llm.needs_brief(p))

    def test_newly_available_abstract_does_not_retranslate_existing_normal_title(self):
        p = {"title": "A complete English title with a subtitle", "zh_title": "已有完整正确中文标题：保留副标题",
             "abstract": "A newly recovered abstract about catalyst preparation and ammonia synthesis. " * 4,
             "brief_from": "title", "brief_v": 3}
        self.assertTrue(llm.needs_brief(p))
        self.assertTrue(llm._apply(p, {"t": "模型这次另写的标题", "b": "根据新补摘要介绍催化剂制备方法和合成氨性能。"}, "fixture"))
        self.assertEqual(p["zh_title"], "已有完整正确中文标题：保留副标题")
        self.assertEqual(p["zh_brief"], "根据新补摘要介绍催化剂制备方法和合成氨性能。")
        self.assertEqual(p["brief_from"], "abstract")
        self.assertFalse(llm.needs_brief(p))

    def test_invalid_scientific_escape_is_preserved_as_literal_text(self):
        result = llm._parse(r'[{"i":0,"t":"Ru 催化","b":"速率 4.2 \mathrm{mmol}，温度 300\°C。"}]')
        self.assertEqual(result[0]["b"], r"速率 4.2 \mathrm{mmol}，温度 300\°C。")

    def test_valid_escapes_survive_repair_of_another_escape(self):
        result = llm._parse(r'[{"i":0,"t":"\u50ac化\n反应","b":"\"Ru\" \\ 配位 \mathrm{x}"}]')
        self.assertEqual(result[0]["t"], "催化\n反应")
        self.assertEqual(result[0]["b"], '"Ru" \\ 配位 \\mathrm{x}')

    def test_trailing_comma_repair_does_not_rewrite_text(self):
        result = llm._parse('[{"i":0,"t":"催化,}","b":"区间 [,] 保留",},]')
        self.assertEqual(result[0]["t"], "催化,}")
        self.assertEqual(result[0]["b"], "区间 [,] 保留")

    def test_fences_and_brackets_inside_strings(self):
        expected = [{"i": 0, "t": "[Ru] 催化剂", "b": "包含 ] 和 [ 的科学文字"}]
        self.assertEqual(llm._parse("```json\n" + json.dumps(expected) + "\n```"), expected)

    def test_ambiguous_malformed_and_truncated_replies_are_rejected(self):
        for text in ('[{"i":0}] [{"i":1}]', '[{"i":0,"i":1}]',
                     r'[{"i":0,"t":"\uZZZZ"}]', '[{"i":0,"t":"尚未结束'):
            with self.subTest(text=text), self.assertRaises(ValueError):
                llm._parse(text)

    def test_duplicate_and_coerced_ids_never_map_to_papers(self):
        rows = [{"i": 0}, {"i": 0}, {"i": "1"}, {"i": 1.0}, {"i": True},
                {"i": -1}, {"i": 5}, {"i": 2}]
        self.assertEqual(llm._indexed_rows(rows, 3), [(2, {"i": 2})])

    def test_no_abstract_means_title_only_even_if_model_supplies_claims(self):
        p = {"title": "Ru catalyst", "abstract": "", "zh_brief": "旧的无依据简介"}
        self.assertTrue(llm._apply(p, {"t": "Ru 催化剂", "b": "凭空编造比其他催化剂高一百倍"}, "fixture"))
        self.assertNotIn("zh_brief", p)
        self.assertEqual(p["brief_from"], "title")

    def test_malformed_field_values_are_not_stringified(self):
        p = {"title": "Ru catalyst", "abstract": "An experimental catalytic abstract. " * 10}
        before = copy.deepcopy(p)
        self.assertFalse(llm._apply(p, {"t": "催化", "b": {"claim": "不应接受"}}, "fixture"))
        self.assertEqual(p, before)

    def test_real_summarize_flow_recovers_escaped_unit_without_extra_call(self):
        papers = [{"title": "Ru catalysts", "abstract": "Catalytic ammonia synthesis over Ru was investigated. " * 6},
                  {"title": "ALD thin films", "abstract": ""}]
        reply = r'[{"i":0,"t":"Ru 催化剂","b":"Ru 催化剂在 300\°C 下用于合成氨，报告了反应活性。"},{"i":1,"t":"ALD 薄膜","b":"虚构内容不应写入"},]'
        with patch.object(llm, "provider", return_value={"used": "fixture"}), patch.object(llm, "chat", return_value=reply) as chat:
            stats = llm.summarize(papers, deadline=time.time() + 5)
        self.assertEqual(stats["done"], 2)
        self.assertEqual(stats["failed"], 0)
        self.assertEqual(chat.call_count, 1)
        self.assertIn(r"300\°C", papers[0]["zh_brief"])
        self.assertNotIn("zh_brief", papers[1])

    def test_ambiguous_id_leaves_both_papers_untouched(self):
        papers = [{"title": "One"}, {"title": "Two"}]
        reply = '[{"i":0,"t":"一","b":""},{"i":0,"t":"二","b":""}]'
        with patch.object(llm, "provider", return_value={"used": "fixture"}), patch.object(llm, "chat", return_value=reply):
            stats = llm.summarize(papers, deadline=time.time() + 5)
        self.assertEqual(stats["done"], 0)
        self.assertEqual(stats["left"], 2)
        self.assertTrue(all("zh_title" not in p for p in papers))

    def test_summary_deadline_bounds_request_and_stops_without_sleep(self):
        clock = [100.0]
        prov = {"key": "fixture", "base": llm.ZHIPU_BASE, "models": [llm.ZHIPU_MODELS[0]]}
        def timeout(*args, **kwargs):
            self.assertLessEqual(kwargs["timeout"], 4)
            clock[0] = 104.0
            raise TimeoutError("fixture timeout")
        with patch.object(llm, "provider", return_value=prov), \
             patch.object(llm.time, "time", side_effect=lambda: clock[0]), \
             patch.object(llm.time, "sleep") as sleep, \
             patch.object(llm.net, "post_json", side_effect=timeout) as post, \
             patch.object(llm.net, "post_json_retry") as old_retry:
            stats = llm.summarize([{"title": "Ru catalyst"}], deadline=104.0)
        self.assertEqual(post.call_count, 1)
        old_retry.assert_not_called()
        sleep.assert_not_called()
        self.assertEqual(stats["done"], 0)
        self.assertEqual(stats["left"], 1)

    def test_budget_reserves_free_fallback_and_preserves_unbudgeted_chat(self):
        clock = [100.0]
        prov = {"key": "fixture", "base": llm.ZHIPU_BASE, "models": list(llm.ZHIPU_MODELS)}
        reply = {"choices": [{"message": {"content": "[]"}}]}
        def request(url, payload, **kwargs):
            self.assertLessEqual(kwargs["timeout"], 10)
            if payload["model"] == llm.ZHIPU_MODELS[0]:
                clock[0] += kwargs["timeout"]
                raise TimeoutError("first free model stalled")
            return reply
        with patch.object(llm.time, "time", side_effect=lambda: clock[0]), \
             patch.object(llm.time, "sleep") as sleep, \
             patch.object(llm.net, "post_json", side_effect=request) as post:
            self.assertEqual(llm.chat(prov, "fixture", deadline=120.0), "[]")
        self.assertEqual(post.call_count, 2)
        self.assertEqual(prov["used"], llm.ZHIPU_MODELS[1])
        sleep.assert_not_called()
        with patch.object(llm.net, "post_json_retry", return_value=reply) as old_retry, \
             patch.object(llm.net, "post_json") as budget_request:
            self.assertEqual(llm.chat(prov, "unchanged review call"), "[]")
        budget_request.assert_not_called()
        self.assertEqual(old_retry.call_args.kwargs["timeout"], 180)
        self.assertEqual(old_retry.call_args.kwargs["retries"], 4)

    def test_budget_pause_is_not_failure_but_real_errors_remain_visible(self):
        cases = [(llm.net.FetchError("TimeoutError: read timed out"), 104.5, True),
                 (TimeoutError("read timed out"), 105.0, True),
                 (ValueError("invalid JSON"), 105.0, False),
                 (llm.net.FetchError("HTTP 503"), 101.0, False)]
        for error, finished_at, paused in cases:
            with self.subTest(error=type(error).__name__, finished_at=finished_at):
                clock = [100.0]
                def fail(*args, **kwargs):
                    clock[0] = finished_at
                    raise error
                with patch.object(llm, "provider", return_value={"used": "fixture"}), \
                     patch.object(llm, "chat", side_effect=fail), \
                     patch.object(llm.time, "time", side_effect=lambda: clock[0]), \
                     patch.object(llm.time, "sleep") as sleep:
                    stats = llm.summarize([{"title": "Ru catalyst"}], deadline=105.0)
                self.assertEqual(stats["left"], 1)
                if paused:
                    self.assertEqual(stats["paused"], "时间预算已用完，下次继续")
                    self.assertEqual(stats["failed"], 0)
                    self.assertEqual(stats["err"], "")
                    sleep.assert_not_called()
                else:
                    self.assertNotIn("paused", stats)
                    self.assertGreater(stats["failed"], 0)
                    self.assertTrue(stats["err"])


if __name__ == "__main__":
    unittest.main()
