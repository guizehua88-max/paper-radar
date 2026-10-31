"""Date repair must use verified metadata and preserve publisher precision."""
import copy
import datetime as dt
import json
import pathlib
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from radar import dates, net, store

DOI = "10.1016/j.xinn.2026.101435"
TODAY = dt.date(2026, 10, 31)


def parts(*values):
    return {"date-parts": [list(values)]}


class DateRepairTests(unittest.TestCase):
    def repair(self, papers, item, **kwargs):
        with patch.object(net, "get_json", return_value={"message": item}) as request:
            result = dates.fill_missing_dates(papers, now=TODAY, deadline=time.time()+60, **kwargs)
        return result, request

    def test_issued_month_wins_over_earlier_created_day(self):
        paper = {"id": DOI, "date": None, "first_seen": "2026-10-28", "pushed": True,
                 "title": "KH versus KOH", "source": "The Innovation", "zh_brief": "已有简介"}
        before = copy.deepcopy(paper)
        result, request = self.repair([paper], {"DOI": DOI, "issued": parts(2026, 10),
                                               "created": parts(2026, 5, 29)})
        self.assertEqual(result["updated"], 1)
        self.assertEqual(paper["date"], "2026-10")
        self.assertEqual(paper["date_source"], "Crossref issued")
        self.assertEqual(paper["date_checked_at"], "2026-10-31")
        for key in before.keys() - {"date"}:
            self.assertEqual(paper[key], before[key])
        self.assertIn("10.1016%2Fj.xinn.2026.101435", request.call_args.args[0])

    def test_best_date_selects_earliest_real_publication_without_inventing_day(self):
        paper = {"id": DOI, "date": "2026"}
        self.repair([paper], {"DOI": DOI, "issued": parts(2026, 10),
                             "published-print": parts(2026, 10, 4), "published-online": parts(2026, 9),
                             "created": parts(2026, 5, 29)})
        self.assertEqual(paper["date"], "2026-09")
        self.assertEqual(paper["date_source"], "Crossref published-online")

    def test_invalid_future_and_year_only_dates_preserve_original(self):
        for value in (parts(2026), parts(2026, 11), parts(2026, 10, 32), parts(2026, 13),
                      parts(2026, 2, 29), parts(2026, 10.4), parts(2026, True),
                      parts(2026, None, 15), {"date-parts": "bad"}):
            with self.subTest(value=value):
                paper = {"id": DOI, "date": None}
                before = copy.deepcopy(paper)
                result, _ = self.repair([paper], {"DOI": DOI, "issued": value})
                self.assertEqual(paper, before)
                self.assertEqual(result["missing"], 1)
        # A later day in this same month must also be rejected, unlike a known month.
        paper = {"id": DOI, "date": "2026"}
        with patch.object(net, "get_json", return_value={"message": {"DOI": DOI, "issued": parts(2026, 10, 31)}}):
            dates.fill_missing_dates([paper], now=dt.date(2026, 10, 20), deadline=time.time()+60)
        self.assertEqual(paper, {"id": DOI, "date": "2026"})

    def test_existing_created_fallback_is_explicit_when_publication_only_has_year(self):
        paper = {"id": DOI, "date": "2026"}
        self.repair([paper], {"DOI": DOI, "issued": parts(2026), "created": parts(2026, 5, 29)})
        self.assertEqual(paper["date"], "2026-05-29")
        self.assertEqual(paper["date_source"], "Crossref created")

    def test_existing_precise_dates_wechat_and_non_doi_are_not_requested(self):
        papers = [{"id": DOI, "date": "2026-10"}, {"id": "10.1/second", "date": "2026-09-03"},
                  {"id": DOI, "kind": "wechat"}, {"id": "url:https://example.org", "date": "2026"}]
        original = copy.deepcopy(papers)
        result, request = self.repair(papers, {})
        request.assert_not_called()
        self.assertEqual(result["eligible"], 0)
        self.assertEqual(papers, original)

    def test_identity_or_shape_failure_does_not_attach_date(self):
        for response in ({"message": {"DOI": "10.1/wrong", "issued": parts(2026, 10)}},
                         {"message": []}, [], {"message": {"DOI": None}}):
            with self.subTest(response=response), patch.object(net, "get_json", return_value=response):
                paper = {"id": DOI, "date": None}
                result = dates.fill_missing_dates([paper], now=TODAY, deadline=time.time()+60)
                self.assertEqual(result["failed"], 1)
                self.assertEqual(paper, {"id": DOI, "date": None})

    def test_outage_stops_requests_and_logs_no_secret_or_url(self):
        papers = [{"id": DOI}, {"id": "10.1/second"}]
        with patch.object(net, "get_json", side_effect=net.FetchError("HTTP 429 @ https://x?api_key=SECRET")) as request:
            result = dates.fill_missing_dates(papers, now=TODAY, deadline=time.time()+60)
        self.assertEqual(request.call_count, 1)
        self.assertEqual((result["queried"], result["failed"], result["skipped"]), (1, 1, 1))
        self.assertEqual(result["errors"], ["HTTP 429"])
        self.assertEqual(papers, [{"id": DOI}, {"id": "10.1/second"}])

    def test_expired_deadline_and_request_cap_leave_unattempted_untouched(self):
        papers = [{"id": DOI}, {"id": "10.1/second"}]
        with patch.object(net, "get_json") as request:
            result = dates.fill_missing_dates(papers, now=TODAY, deadline=time.time()-1)
        request.assert_not_called()
        self.assertEqual((result["queried"], result["skipped"]), (0, 2))
        result, request = self.repair(papers, {"DOI": DOI, "issued": parts(2026, 10)}, limit=1)
        self.assertEqual(request.call_count, 1)
        self.assertEqual((result["updated"], result["skipped"]), (1, 1))
        self.assertEqual(papers[1], {"id": "10.1/second"})
        # A request that consumes the budget must prevent the next request too.
        now = [0]
        def response(*args, **kwargs):
            now[0] = 20
            return {"message": {"DOI": DOI, "issued": parts(2026, 10)}}
        with patch.object(dates.time, "time", side_effect=lambda: now[0]), \
             patch.object(net, "get_json", side_effect=response) as request:
            result = dates.fill_missing_dates([{"id": DOI}, {"id": "10.1/second"}], now=TODAY, deadline=10)
        self.assertEqual(request.call_count, 1)
        self.assertEqual((result["queried"], result["skipped"]), (1, 1))

    def test_repaired_month_moves_record_without_duplicate_or_lost_metadata(self):
        paper = {"id": DOI, "date": None, "first_seen": "2026-10-28", "old_fav": True}
        records = {DOI: paper}
        with tempfile.TemporaryDirectory(prefix="radar-date-") as directory:
            data = pathlib.Path(directory)
            with patch.object(store, "SHARDS", data/"papers"), patch.object(store, "DATA", data):
                store.save_papers(records)
                self.assertTrue((data/"papers"/"unknown.json").exists())
                self.repair([paper], {"DOI": DOI, "issued": parts(2026, 10)})
                store.save_papers(records)
                self.assertFalse((data/"papers"/"unknown.json").exists())
                restored = store.load_papers()
                self.assertEqual(restored, records)
                self.assertEqual(list(json.loads((data/"papers"/"2026-10.json").read_text("utf-8"))), [DOI])
                self.assertTrue(restored[DOI]["old_fav"])


if __name__ == "__main__":
    unittest.main()
