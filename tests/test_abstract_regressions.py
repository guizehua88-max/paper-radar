"""Bounded, exact-DOI abstract enrichment; no real network in this suite."""
import os
from pathlib import Path
import sys
import time
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from radar import sources, net

AB = 'Ammonia synthesis over supported Ru catalysts was investigated under mild conditions. ' * 3
D1, D2 = '10.1000/one', '10.1000/two'


class AbstractTests(unittest.TestCase):
    def setUp(self):
        self.env = patch.dict(os.environ, {}, clear=True)
        self.env.start()
        self.pause = patch.object(sources, 'polite_pause')
        self.pause.start()
        sources.reset_openalex_run()
        # This suite isolates the older providers; OpenAlex has its own real
        # adapter regressions. Enabling keyless OA must not cause live requests.
        def empty_oa(dois, key, errs, *, progress=None, deadline=None, counts=None):
            sources._chunks(dois, 50, lambda chunk: None, errs, source='OpenAlex',
                            progress=progress, deadline=deadline)
            return {}
        self.oa = patch.object(sources, 'abstracts_openalex', side_effect=empty_oa)
        self.oa.start()

    def tearDown(self):
        self.pause.stop()
        self.env.stop()
        self.oa.stop()
        sources.reset_openalex_run()

    def test_semantic_scholar_uses_exact_doi_not_position(self):
        rows = [{'externalIds': {'DOI': D2}, 'abstract': AB+'Two'},
                {'externalIds': {'DOI': D1}, 'abstract': AB+'One'}]
        with patch.object(net, 'post_json_retry', return_value=rows):
            progress = {}; errors = []
            got = sources.abstracts_semantic_scholar([D1,D2], errors, progress=progress)
        self.assertTrue(got[D1].endswith('One'))
        self.assertTrue(got[D2].endswith('Two'))
        self.assertEqual(progress[D1]['checked'], ['Semantic Scholar'])

    def test_missing_or_wrong_identity_skips_one_record_without_batch_failure(self):
        for rows in ([{'abstract': AB}], [{'abstract': AB, 'externalIds': {'DOI': D2}}]):
            with self.subTest(rows=rows), patch.object(net, 'post_json_retry', return_value=rows):
                errors = []; progress = {}; counts = {}
                got = sources.abstracts_semantic_scholar([D1], errors, progress=progress, counts=counts)
                self.assertEqual(got,{})
                self.assertEqual(progress[D1]['failed'], [])
                self.assertEqual(progress[D1]['checked'], ['Semantic Scholar'])
                self.assertEqual(errors, [])
                self.assertEqual(counts, {'identity_mismatches': 1})

    def test_one_mismatched_doi_keeps_other_abstracts_and_subsequent_batches(self):
        dois = [f'10.1000/paper-{i}' for i in range(101)]
        first = [{'externalIds': {'DOI': d}, 'abstract': AB+d} for d in dois[:100]]
        first[17]['externalIds']['DOI'] = '10.1000/not-requested'
        second = [{'externalIds': {'DOI': dois[100]}, 'abstract': AB+dois[100]}]
        progress = {}
        with patch.object(net, 'post_json_retry', side_effect=[first, second]) as post:
            found, stats = sources.find_abstracts(dois, epmc_cap=0, crossref_cap=0, progress=progress)
        self.assertEqual(post.call_count, 2)
        self.assertEqual(len(found), 100)
        self.assertNotIn(dois[17], found)
        self.assertNotIn('10.1000/not-requested', found)
        self.assertEqual(found[dois[0]], AB+dois[0])
        self.assertEqual(found[dois[100]], AB+dois[100])
        self.assertEqual(stats['Semantic Scholar'], '查 101 得 100（身份不符跳过 1 条）')
        self.assertTrue(all(p['failed'] == [] and p['checked'] == ['Semantic Scholar', 'OpenAlex'] for p in progress.values()))

    def test_malformed_batch_remains_an_atomic_failure(self):
        good = {'externalIds': {'DOI': D1}, 'abstract': AB}
        mismatch = {'externalIds': {'DOI': '10.1000/not-requested'}, 'abstract': AB}
        cases = [[], {'error': 'bad format'}, [good, mismatch, 'not a record'],
                 [good, mismatch, {'externalIds': [], 'abstract': AB}],
                 [good, mismatch, {'externalIds': {'DOI': 42}, 'abstract': AB}],
                 [good, mismatch, {'externalIds': {'DOI': D2}, 'abstract': {'text': AB}}]]
        for rows in cases:
            with self.subTest(rows=rows), patch.object(net, 'post_json_retry', return_value=rows):
                errors = []; progress = {}; counts = {}
                got = sources.abstracts_semantic_scholar([D1, D2, '10.1000/three'], errors,
                                                        progress=progress, counts=counts)
                self.assertEqual(got, {})
                self.assertEqual(counts, {'identity_mismatches': 0})
                self.assertTrue(all(p['failed'] == ['Semantic Scholar'] and p['checked'] == []
                                    for p in progress.values()))
                self.assertTrue(errors)

    def test_one_failed_source_does_not_retry_other_chunks_or_block_fallback(self):
        progress = {}
        with patch.object(net, 'post_json_retry', side_effect=net.FetchError('HTTP 429')) as post, \
             patch.object(net, 'get_json', return_value={'resultList': {'result': [{'doi': D1,'abstractText': AB}]}}):
            found, stats = sources.find_abstracts([D1,D2]+[f'10.1000/{i}' for i in range(105)],
                progress=progress, epmc_cap=2, crossref_cap=0)
        self.assertEqual(post.call_count,1)
        self.assertEqual(found,{D1: AB.strip()})
        self.assertEqual(progress[D1]['found_by'],'Europe PMC')
        self.assertTrue(progress[D1]['complete'])
        self.assertFalse(progress[D2]['complete'])
        self.assertEqual(progress['10.1000/104']['checked'], ['OpenAlex'])
        self.assertNotIn('Semantic Scholar', progress['10.1000/104']['attempted'])
        self.assertIn('HTTP 429',stats['Semantic Scholar'])

    def test_deadline_skipped_records_not_marked_checked(self):
        progress = {}
        with patch.object(net,'post_json_retry') as post, patch.object(net,'get_json') as get:
            found, stats = sources.find_abstracts([D1,D2], deadline=time.time()-1, progress=progress)
        self.assertEqual(progress,{})
        self.assertEqual(found,{})
        post.assert_not_called(); get.assert_not_called()

    def test_deadline_checked_between_batches(self):
        progress = {}; now = [0]
        def response(*args, **kwargs):
            now[0] = 99
            return {'resultList': {'result': []}}
        with patch.object(sources.time,'time',side_effect=lambda: now[0]), patch.object(net,'get_json',side_effect=response) as get:
            sources.abstracts_europepmc([f'10.1000/{i}' for i in range(45)],[],progress=progress,deadline=10)
        self.assertEqual(get.call_count,1)
        self.assertEqual(len(progress),20)

    def test_slow_first_source_reserves_time_for_later_fallbacks(self):
        progress = {}; now = [100.0]; timeouts = []
        def post(*args, **kwargs):
            timeouts.append(kwargs['timeout'])
            now[0] += kwargs['timeout']
            raise net.FetchError('HTTP 429')
        def get(url, **kwargs):
            timeouts.append(kwargs['timeout'])
            if 'europepmc' in url:
                now[0] += kwargs['timeout']
                return {'resultList': {'result': []}}
            return {'message': {'DOI': D1, 'abstract': AB}}
        with patch.object(sources.time, 'time', side_effect=lambda: now[0]), \
             patch.object(net, 'post_json_retry', side_effect=post), \
             patch.object(net, 'get_json', side_effect=get):
            found, stats = sources.find_abstracts([D1], deadline=130, progress=progress)
        self.assertEqual(timeouts, [7, 11, 12])
        self.assertEqual(found, {D1: AB.strip()})
        self.assertEqual(progress[D1]['found_by'], 'Crossref')
        self.assertTrue(progress[D1]['complete'])

    def test_crossref_singleton_rechecks_metadata_with_identity_and_cap(self):
        progress = {}
        def get(url,**kwargs):
            if '/works/' in url:
                return {'message': {'DOI': D1, 'abstract': '<jats:p>'+AB+'</jats:p>'}}
            return {'resultList': {'result': []}}
        with patch.object(net,'post_json_retry',return_value=[None,None]), patch.object(net,'get_json',side_effect=get) as req:
            found, stats = sources.find_abstracts([D1,D2],progress=progress,crossref_cap=1)
        self.assertEqual(found[D1],AB.strip())
        self.assertEqual(progress[D1]['found_by'],'Crossref')
        self.assertFalse(progress[D2]['complete'])
        self.assertNotIn('Crossref',progress[D2]['attempted'])

    def test_crossref_wrong_doi_and_error_url_not_saved(self):
        self.oa.stop()
        with patch.object(net,'get_json',return_value={'message': {'DOI': D2,'abstract': AB}}):
            self.assertEqual(sources.abstracts_crossref([D1],[]),{})
        with patch.object(net,'get_json',side_effect=net.FetchError('HTTP 429 @ https://api.openalex.org?api_key=PRIVATE')):
            errors = []
            self.assertEqual(sources.abstracts_openalex([D1],'PRIVATE',errors),{})
            self.assertEqual(errors,['HTTP 429'])

    def test_crossref_cap_prioritizes_never_checked_then_oldest_stably(self):
        d3, d4 = '10.1000/three', '10.1000/four'
        crossref_calls = []
        def get(url, **kwargs):
            if '/works/' in url:
                doi = sources.urllib.parse.unquote(url.rsplit('/', 1)[-1])
                crossref_calls.append(doi)
                return {'message': {'DOI': doi}}
            return {'resultList': {'result': []}}
        with patch.object(net, 'post_json_retry', return_value=[None] * 4), \
             patch.object(net, 'get_json', side_effect=get):
            sources.find_abstracts([D1, D2, d3, d4], crossref_cap=3,
                crossref_checked_at={D1: '2026-10-31', D2: '2026-10-20'})
        self.assertEqual(crossref_calls, [d3, d4, D2])

    def test_successful_empty_differs_from_failed_and_capped(self):
        progress={}
        with patch.object(net,'post_json_retry',return_value=[None,None]), \
             patch.object(net,'get_json',return_value={'resultList':{'result':[]}}):
            sources.find_abstracts([D1,D2],progress=progress,epmc_cap=1,crossref_cap=0)
        self.assertTrue(progress[D1]['complete'])
        self.assertFalse(progress[D2]['complete'])
        self.assertEqual(progress[D2]['checked'],['Semantic Scholar', 'OpenAlex'])


if __name__ == '__main__':
    unittest.main()
