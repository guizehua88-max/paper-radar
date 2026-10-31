"""Bookkeeping regressions: no network and no production data writes."""
import datetime as dt
import pathlib
import sys
import unittest
from unittest.mock import patch
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from radar.__main__ import Radar
from radar.site import summary_progress


class MaintenanceTests(unittest.TestCase):
    def test_only_actually_attempted_papers_receive_timestamp(self):
        r = Radar.__new__(Radar)
        r.now = dt.datetime(2026, 10, 31, tzinfo=dt.timezone.utc)
        r.status = {"sources": {}}
        papers = [{"id": "10.1/a"}, {"id": "10.1/b"}, {"id": "10.1/c"}, {"id": "10.1/d"}, {"id": "10.1/e"}]
        def lookup(dois, **kwargs):
            kwargs["progress"].update({"10.1/a": {"checked": ["Europe PMC"], "found_by": "Europe PMC"},
                                       "10.1/b": {"failed": ["Semantic Scholar"]},
                                       "10.1/d": {"checked": ["Europe PMC"], "complete": False},
                                       "10.1/e": {"checked": ["Europe PMC", "Crossref", "Semantic Scholar"], "complete": True}})
            return {"10.1/a": "An actual abstract. " * 20}, {"Semantic Scholar": "失败", "Europe PMC": "正常"}
        with patch("radar.__main__.net_offline", return_value=False), patch("radar.sources.find_abstracts", side_effect=lookup):
            result = r.fill_abstracts(papers, 9999999999)
        self.assertEqual(result["查询"], 4)
        self.assertEqual(papers[0]["abstract_source"], "Europe PMC")
        self.assertEqual(papers[1]["ab_result"], "retry")
        self.assertNotIn("ab_try", papers[2])
        self.assertNotIn("abstract", papers[1])
        self.assertEqual(papers[3]["ab_result"], "partial")
        self.assertEqual(papers[4]["ab_result"], "not_found")

    def test_title_translation_is_not_counted_as_a_summary(self):
        abstract = "A sufficiently detailed real abstract. " * 10
        papers = [
            {"tier": "direct", "abstract": abstract, "zh_brief": "真实简介", "brief_from": "abstract"},
            {"tier": "direct", "zh_title": "仅中文标题", "brief_from": "title"},
            {"tier": "direct", "abstract": abstract, "zh_title": "摘要刚补上", "brief_from": "title"},
            {"tier": "background"}, {"kind": "wechat", "zh_brief": "公众号正文"},
        ]
        counts = summary_progress(papers)
        self.assertEqual(counts["key"], {"total": 3, "briefs": 1, "titleOnly": 1, "pending": 1})
        self.assertEqual(counts["all"]["total"], 4)
        self.assertEqual(counts["all"]["pending"], 2)


if __name__ == "__main__":
    unittest.main()
