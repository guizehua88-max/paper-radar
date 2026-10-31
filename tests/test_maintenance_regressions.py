"""Bookkeeping regressions: no network and no production data writes."""
import datetime as dt
import pathlib
import sys
import unittest
import urllib.parse
from unittest.mock import patch, Mock
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from radar.__main__ import Radar
from radar.site import summary_progress
from radar import sources, net


class MaintenanceTests(unittest.TestCase):
    def setUp(self):
        sources.reset_openalex_run()
        self.addCleanup(sources.reset_openalex_run)

    def radar(self, papers):
        r = Radar.__new__(Radar)
        r.now = dt.datetime(2026, 10, 31, tzinfo=dt.timezone.utc)
        r.papers = {p['id']: p for p in papers}
        return r

    def test_abstract_queue_expands_to_500_with_key_recent_and_fair_order(self):
        papers = [{'id': f'10.1/{i:04}', 'tier': 'background', 'date': '2026-10-30'} for i in range(510)]
        papers += [
            {'id':'10.1/key-old','tier':'cross','date':'2024-01'},
            {'id':'10.1/key-recent','tier':'direct','date':'2026-10'},
            {'id':'10.1/round2','tier':'cross','date':'2026-10-31','ab_check_v':2,'ab_try':'2026-10-01',
             'ab_sources_checked':['OpenAlex']},
        ]
        r = self.radar(papers)
        selected = r.abstract_candidates()
        self.assertEqual(len(selected), 500)
        self.assertEqual([p['id'] for p in selected[:2]], ['10.1/key-recent', '10.1/key-old'])
        self.assertNotIn('10.1/round2', [p['id'] for p in selected])  # finish untouched papers before repeating a round

    def test_queue_retains_retry_cooldowns_and_skips_existing_abstracts(self):
        papers = [
            {'id':'10.1/retry-today','ab_check_v':2,'ab_try':'2026-10-31','ab_result':'retry'},
            {'id':'10.1/partial-yesterday','ab_check_v':2,'ab_try':'2026-10-30','ab_result':'partial'},
            {'id':'10.1/miss-six-days','ab_check_v':2,'ab_try':'2026-10-25','ab_result':'not_found'},
            {'id':'10.1/miss-seven-days','ab_check_v':2,'ab_try':'2026-10-24','ab_result':'not_found'},
            {'id':'10.1/complete','abstract':'Real sufficiently detailed abstract. '*15},
            {'id':'10.1/wechat','kind':'wechat'}, {'id':'url:no-doi'},
        ]
        for paper in papers:
            paper['ab_sources_checked'] = ['OpenAlex']
        self.assertEqual({p['id'] for p in self.radar(papers).abstract_candidates()},
                         {'10.1/partial-yesterday', '10.1/miss-seven-days'})

    def test_maintain_uses_500_cap_without_increasing_time_or_epmc_budget(self):
        for minutes, expected_seconds in ((5, 75), (20, 300)):
            with self.subTest(minutes=minutes):
                r = self.radar([{'id':f'10.1/{i}', 'tier':'direct'} for i in range(520)])
                r.state = {'rules_hash':'same'}
                r.rules_hash = Mock(return_value='same')
                r.link_mentions = Mock(return_value=0)
                r.finish = Mock()
                r.fill_abstracts = Mock(return_value={'查询':500, '找到':0})
                ds = {'queried':0,'updated':0,'missing':0,'failed':0,'skipped':0}
                with patch('radar.__main__.time.time', return_value=1000), \
                     patch('radar.__main__.net_offline', return_value=False), \
                     patch('radar.dates.fill_missing_dates', return_value=ds) as date_fill, \
                     patch('radar.llm.provider', return_value=None):
                    r.maintain(minutes)
                args, kwargs = r.fill_abstracts.call_args
                self.assertEqual(len(args[0]),500)
                self.assertEqual(args[1],1000+expected_seconds)
                self.assertEqual(kwargs['epmc_cap'],300)
                self.assertEqual(date_fill.call_args.kwargs['limit'],30)
                self.assertLessEqual(date_fill.call_args.kwargs['deadline'],1000+minutes*60)
                self.assertEqual(r.finish.call_args.args[0],[])  # maintenance never sends a notification

    def test_500_dois_keep_provider_specific_caps(self):
        calls = {'Semantic Scholar':0, 'OpenAlex':0, 'Europe PMC':0, 'Crossref':0}
        def post(url, payload, **kwargs):
            calls['Semantic Scholar'] += 1
            self.assertLessEqual(len(payload['ids']), 100)
            return [None] * len(payload['ids'])
        def get(url, **kwargs):
            if 'api.openalex.org' in url:
                calls['OpenAlex'] += 1
                params = urllib.parse.parse_qs(urllib.parse.urlparse(url).query)
                self.assertNotIn('api_key', params)
                self.assertEqual(params['per_page'], ['50'])
                self.assertLessEqual(len(params['filter'][0][4:].split('|')),50)
                return {'results':[]}
            if '/works/' in url:
                calls['Crossref'] += 1
                return {'message':{'DOI':urllib.parse.unquote(url.split('/works/',1)[1])}}
            calls['Europe PMC'] += 1
            return {'resultList':{'result':[]}}
        with patch.dict('os.environ', {'OPENALEX_API_KEY':''}), \
             patch.object(net,'post_json_retry',side_effect=post), \
             patch.object(net,'get_json',side_effect=get), patch.object(sources,'polite_pause'):
            progress = {}
            found, stats = sources.find_abstracts([f'10.1/{i}' for i in range(500)], progress=progress, epmc_cap=300)
        self.assertEqual(found,{})
        self.assertEqual(len(progress),500)
        self.assertEqual(calls, {'Semantic Scholar':5,'OpenAlex':10,'Europe PMC':15,'Crossref':20})
        self.assertIn('查 500', stats['Semantic Scholar'])
        self.assertIn('查 500 得 0', stats['OpenAlex'])
        self.assertIn('查 300', stats['Europe PMC'])
        self.assertIn('查 20', stats['Crossref'])

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

    def test_unchecked_openalex_key_papers_bypass_old_cooldown_before_background(self):
        papers = [{'id':f'10.1/background-{i}', 'tier':'background','date':'2026-10-31'}
                  for i in range(510)]
        papers += [
            {'id':'10.1/key-old','tier':'direct','date':'2024-01','ab_check_v':2,
             'ab_try':'2026-10-31','ab_result':'not_found',
             'ab_sources_checked':['Semantic Scholar','Europe PMC','Crossref']},
            {'id':'10.1/key-recent','tier':'transferable','date':'2026-10','ab_check_v':2,
             'ab_try':'2026-10-31','ab_result':'partial','ab_sources_checked':['Europe PMC']},
        ]
        selected = self.radar(papers).abstract_candidates()
        self.assertEqual(len(selected),500)
        self.assertEqual([p['id'] for p in selected[:2]],['10.1/key-recent','10.1/key-old'])

    def test_openalex_success_ends_catchup_but_failure_is_not_a_checked_miss(self):
        papers = [
            {'id':'10.1/covered','ab_check_v':2,'ab_try':'2026-10-31','ab_result':'not_found',
             'ab_sources_checked':['OpenAlex']},
            {'id':'10.1/covered-date','ab_check_v':2,'ab_try':'2026-10-31','ab_result':'not_found',
             'ab_source_checked_at':{'OpenAlex':'2026-10-30'}},
            {'id':'10.1/failed','ab_check_v':2,'ab_try':'2026-10-31','ab_result':'retry',
             'ab_sources_checked':['Semantic Scholar']},
        ]
        self.assertEqual([p['id'] for p in self.radar(papers).abstract_candidates()],['10.1/failed'])

    def test_later_failure_preserves_successful_openalex_coverage_and_date(self):
        paper = {'id':'10.1/covered','ab_sources_checked':['OpenAlex'],
                 'ab_source_checked_at':{'OpenAlex':'2026-10-30'}}
        r = self.radar([paper])
        r.status = {'sources':{}}
        def lookup(dois, **kwargs):
            kwargs['progress']['10.1/covered'] = {
                'attempted':['Semantic Scholar','Europe PMC'],
                'checked':['Europe PMC'], 'failed':['Semantic Scholar'], 'complete':False}
            return {}, {'Semantic Scholar':'请求失败','Europe PMC':'查 1 得 0'}
        with patch('radar.__main__.net_offline', return_value=False), \
             patch.object(sources,'find_abstracts',side_effect=lookup):
            r.fill_abstracts([paper],9999999999)
        self.assertEqual(paper['ab_sources_checked'],['Europe PMC','OpenAlex'])
        self.assertEqual(paper['ab_source_checked_at']['OpenAlex'],'2026-10-30')
        self.assertEqual(paper['ab_source_checked_at']['Europe PMC'],'2026-10-31')
        self.assertEqual(paper['ab_result'],'retry')
        self.assertEqual(r.abstract_candidates(),[])  # normal one-day retry cooldown applies

    def test_new_abstract_keeps_record_and_enters_existing_reclassification_and_brief_path(self):
        paper = {'id':'10.1/a','tier':'direct','date':'2026-10','zh_title':'已有标题',
                 'first_seen':'2026-10-01T00:00:00Z'}
        r = self.radar([paper])
        r.state = {'rules_hash':'same'}
        r.rules_hash = Mock(return_value='same')
        r.link_mentions = Mock(return_value=0)
        r.finish = Mock()
        r.clf = Mock()
        r.clf.classify.return_value = {'tier':'background'}
        def fill(items, *args, **kwargs):
            items[0]['abstract'] = 'A real abstract confirming nitrate electroreduction. '*10
            return {'查询':1,'找到':1,'OpenAlex':'查 1 得 1'}
        r.fill_abstracts = Mock(side_effect=fill)
        r.brief = Mock(return_value={'done':1,'err':'','left':0})
        ds = {'queried':0,'updated':0,'missing':0,'failed':0,'skipped':0}
        with patch('radar.__main__.net_offline',return_value=False), \
             patch('radar.dates.fill_missing_dates',return_value=ds), \
             patch('radar.llm.provider',return_value={'name':'mock'}):
            r.maintain(5)
        self.assertIs(r.papers['10.1/a'],paper)
        self.assertEqual(paper['tier'],'background')
        self.assertEqual(paper['zh_title'],'已有标题')
        self.assertEqual(paper['first_seen'],'2026-10-01T00:00:00Z')
        self.assertEqual(r.brief.call_args.args[0],[paper])
        self.assertEqual(r.finish.call_args.args[0],[])

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
