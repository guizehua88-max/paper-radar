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

    def tearDown(self):
        self.pause.stop()
        self.env.stop()

    def test_semantic_scholar_uses_exact_doi_not_position(self):
        rows = [{'externalIds': {'DOI': D2}, 'abstract': AB+'Two'},
                {'externalIds': {'DOI': D1}, 'abstract': AB+'One'}]
        with patch.object(net, 'post_json_retry', return_value=rows):
            progress = {}; errors = []
            got = sources.abstracts_semantic_scholar([D1,D2], errors, progress=progress)
        self.assertTrue(got[D1].endswith('One'))
        self.assertTrue(got[D2].endswith('Two'))
        self.assertEqual(progress[D1]['checked'], ['Semantic Scholar'])

    def test_missing_wrong_or_truncated_identity_never_attaches_abstract(self):
        for rows in ([{'abstract': AB}], [{'abstract': AB, 'externalIds': {'DOI': D2}}], []):
            with self.subTest(rows=rows), patch.object(net, 'post_json_retry', return_value=rows):
                errors = []; progress = {}
                got = sources.abstracts_semantic_scholar([D1], errors, progress=progress)
                self.assertEqual(got,{})
                self.assertEqual(progress[D1]['failed'], ['Semantic Scholar'])
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
        self.assertNotIn('10.1000/104',progress)
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
        self.assertEqual(timeouts, [10, 10, 10])
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
        self.assertEqual(progress[D2]['checked'],['Semantic Scholar'])


if __name__ == '__main__':
    unittest.main()
