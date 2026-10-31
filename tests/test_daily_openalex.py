"""Keyless OpenAlex daily collection must retain the existing ingest/push path."""
import copy
import datetime as dt
from contextlib import ExitStack
import pathlib
import sys
import tomllib
import unittest
from unittest.mock import Mock, patch

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from radar.__main__ import Radar
from radar.filters import Classifier
from radar import llm, net, push, sources


def paper(doi, title, abstract):
    return {'id':doi,'doi':doi,'title':title,'abstract':abstract,'date':'2026-10-30',
            'authors':['Researcher'],'kind':'journal','source':'Test Journal',
            'url':'https://doi.org/'+doi,'via':'OpenAlex'}


class DailyOpenAlexTests(unittest.TestCase):
    def radar(self):
        r = Radar.__new__(Radar)
        r.cfg = tomllib.loads((ROOT/'config.toml').read_text('utf-8'))
        r.clf = Classifier(r.cfg)
        r.cfg['topics'] = [copy.deepcopy(r.cfg['topics'][0])]
        r.cfg['topics'][0]['crossref_queries'] = ['query1','query2','query3','query4','query5']
        r.cfg['topics'][0].pop('arxiv_query',None)
        r.cfg['rss'] = [{'name':'fixture','url':'https://example.org/rss'}]
        r.S = r.cfg['settings']
        r.now = dt.datetime(2026,10,31,tzinfo=dt.timezone.utc)
        r.state = {'initialized':True,'last_push_at':'2026-10-31T00:00:00Z'}
        r.papers = {}
        r.found = []
        r.status = {'sources':{}}
        r.fill_abstracts = Mock(return_value={'查询':0})
        r.brief = Mock(return_value={'done':0,'err':''})
        r.finish = Mock()
        return r

    def run_daily(self, r, lookup, *, rss_rows=None, disabled=None, api_key=''):
        with ExitStack() as stack:
            stack.enter_context(patch.dict('os.environ',{'OPENALEX_API_KEY':api_key,'LOOKBACK_DAYS':''}))
            stack.enter_context(patch.object(sources,'crossref',return_value=[]))
            rss = stack.enter_context(patch.object(sources,'rss',return_value=rss_rows or []))
            oa = stack.enter_context(patch.object(sources,'openalex',side_effect=lookup))
            stack.enter_context(patch.object(sources,'openalex_disabled_reason',side_effect=lambda: (disabled or [''])[0]))
            stack.enter_context(patch.object(sources,'polite_pause'))
            stack.enter_context(patch.object(llm,'enabled',return_value=False))
            stack.enter_context(patch.object(llm,'provider',return_value=None))
            send = stack.enter_context(patch.object(push,'send',return_value=['test-delivery']))
            stack.enter_context(patch.object(push,'delivered',return_value=False))
            r.daily()
        return oa, rss, send

    def test_keyless_daily_rows_use_existing_filter_and_notification_threshold(self):
        relevant = paper('10.1/thermal','Ruthenium catalyst for thermal ammonia synthesis',
                         'A supported Ru catalyst converts N2 and H2 by thermal catalytic ammonia synthesis. '*5)
        background = paper('10.1/nitrate','Electrochemical nitrate reduction to ammonia',
                           'Nitrate electroreduction is driven by an electrochemical cathode. '*5)
        irrelevant = paper('10.1/irrelevant','A survey of medieval stone castles',
                           'A historical survey of buildings and their architectural development. '*5)
        r = self.radar()
        def lookup(query, since, key):
            self.assertEqual(key,'')
            return [relevant,background,irrelevant] if query=='query1' else []
        oa, rss, send = self.run_daily(r,lookup)
        self.assertEqual([c.args[0] for c in oa.call_args_list],['query1','query2','query3','query4'])
        rss.assert_called_once()
        self.assertIn('10.1/thermal',r.papers)
        self.assertIn('10.1/nitrate',r.papers)
        self.assertNotIn('10.1/irrelevant',r.papers)
        self.assertEqual(r.papers['10.1/nitrate']['tier'],'background')
        send.assert_called_once()
        self.assertIn('新增 1 篇',send.call_args.args[0])
        self.assertIn('Ruthenium',send.call_args.args[1])
        self.assertNotIn('Electrochemical nitrate',send.call_args.args[1])

    def test_auth_or_quota_stop_skips_later_queries_and_keeps_rss_push_flow(self):
        for code in (401,403,429):
            with self.subTest(code=code):
                r = self.radar()
                disabled = ['']
                def lookup(*args):
                    disabled[0] = f'HTTP {code}'
                    raise net.FetchError(disabled[0])
                relevant = paper('10.1/rss','Ruthenium catalyst for thermal ammonia synthesis',
                                 'A Ru catalyst enables thermal ammonia synthesis from nitrogen and hydrogen. '*5)
                oa,rss,send = self.run_daily(r,lookup,rss_rows=[relevant],disabled=disabled)
                oa.assert_called_once()
                rss.assert_called_once()
                send.assert_called_once()
                self.assertIn('10.1/rss',r.papers)
                self.assertEqual(r.status['sources']['OpenAlex']['err'],f'本次停用：HTTP {code}')
                self.assertEqual(r.status['sources']['OpenAlex']['calls'],1)

    def test_key_is_forwarded_and_already_stored_paper_does_not_push_again(self):
        existing = paper('10.1/existing','Ruthenium catalyst for thermal ammonia synthesis',
                         'Thermal ammonia synthesis from N2 and H2 over a supported ruthenium catalyst. '*5)
        existing['first_seen'] = '2026-10-01T00:00:00Z'
        r = self.radar()
        r.papers[existing['id']] = dict(existing)
        oa,rss,send = self.run_daily(r,lambda *args:[dict(existing)],api_key='TEST_ONLY')
        self.assertTrue(all(c.args[2]=='TEST_ONLY' for c in oa.call_args_list))
        self.assertEqual(r.papers[existing['id']]['first_seen'],'2026-10-01T00:00:00Z')
        send.assert_not_called()


if __name__=='__main__':
    unittest.main()
