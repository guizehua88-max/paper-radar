"""Keyless OpenAlex lookup, exact identity, quota isolation, and source order."""
import datetime as dt
import os
from pathlib import Path
import sys
import unittest
from unittest.mock import patch
from urllib.parse import parse_qs, urlparse

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from radar import net, sources

AB = ('Ammonia synthesis over supported metal catalysts was investigated with spectroscopy. '
      'The catalyst structure and reaction conditions were compared in a controlled study. ')
D1, D2 = '10.1000/one', '10.1000/two'


def inverted(text):
    result = {}
    for position, word in enumerate(text.split()):
        result.setdefault(word, []).append(position)
    return result


def params(url):
    return parse_qs(urlparse(url).query)


def requested(url):
    return params(url)['filter'][0].removeprefix('doi:').split('|')


def empty_s2(url, payload, **kwargs):
    return [None] * len(payload['ids'])


class OpenAlexTests(unittest.TestCase):
    def setUp(self):
        sources.reset_openalex_run()
        self.environment = patch.dict(os.environ, {}, clear=True)
        self.environment.start()
        self.pause = patch.object(sources, 'polite_pause')
        self.pause.start()

    def tearDown(self):
        self.pause.stop()
        self.environment.stop()
        sources.reset_openalex_run()

    def test_keyless_batches_50_exact_identity_and_short_abstract(self):
        dois = [f'10.1000/paper-{i}' for i in range(101)]
        calls = []
        def response(url, **kwargs):
            calls.append(url)
            ds = requested(url)
            rows = [{'doi': 'https://doi.org/'+d.upper(), 'abstract_inverted_index': inverted(AB+d)}
                    for d in reversed(ds)]
            if len(calls) == 1:
                rows[0]['doi'] = 'https://doi.org/10.1000/wrong'
                rows[1]['abstract_inverted_index'] = inverted('Too short')
            return {'results': rows}
        errors, progress, counts = [], {}, {}
        with patch.object(net, 'get_json', side_effect=response):
            found = sources.abstracts_openalex(dois, '', errors, progress=progress, counts=counts)
        self.assertEqual([len(requested(url)) for url in calls], [50, 50, 1])
        self.assertTrue(all('api_key' not in params(url) for url in calls))
        self.assertTrue(all(params(url)['per_page'] == ['50'] for url in calls))
        self.assertEqual(len(found), 99)
        self.assertEqual(found[dois[0]], AB+dois[0])
        self.assertNotIn(dois[49], found)
        self.assertNotIn(dois[48], found)
        self.assertNotIn('10.1000/wrong', found)
        self.assertEqual(counts['identity_mismatches'], 1)
        self.assertEqual(counts['batches_attempted'], 3)
        self.assertFalse(errors)
        self.assertTrue(all(row['checked'] == ['OpenAlex'] for row in progress.values()))

    def test_optional_key_is_supplied_only_when_nonempty(self):
        for key in ('', 'KEY-PRIVATE'):
            with self.subTest(key=bool(key)), patch.object(net, 'get_json', return_value={'results': []}) as request:
                sources.abstracts_openalex([D1], key, [])
                query = params(request.call_args.args[0])
                self.assertEqual(query.get('api_key'), [key] if key else None)
                self.assertEqual(request.call_args.kwargs['retries'], 1)

    def test_150_character_threshold_after_index_reconstruction(self):
        for length in (149, 150):
            with self.subTest(length=length), patch.object(net, 'get_json', return_value={
                    'results': [{'doi': D1, 'abstract_inverted_index': {'x'*length: [0]}}]}):
                found = sources.abstracts_openalex([D1], '', [])
                self.assertEqual(D1 in found, length == 150)

    def test_malformed_batch_is_atomic_and_does_not_disable_future_requests(self):
        good = {'doi': D1, 'abstract_inverted_index': inverted(AB)}
        bad = [[], {'results': {}}, {'results': [good, 42]},
               {'results': [good, {'doi': D2, 'abstract_inverted_index': []}]},
               {'results': [good, {'doi': D2, 'abstract_inverted_index': {'word': ['bad']}}]},
               {'results': [good, {'doi': D2, 'abstract_inverted_index': {'a': [0], 'b': [0]}}]}]
        for response in bad:
            with self.subTest(response=response), patch.object(net, 'get_json', return_value=response):
                errors, progress = [], {}
                found = sources.abstracts_openalex([D1, D2], '', errors, progress=progress)
                self.assertFalse(found)
                self.assertTrue(errors)
                self.assertTrue(all(p['failed'] == ['OpenAlex'] and not p['checked'] for p in progress.values()))
                self.assertFalse(sources.openalex_disabled_reason())

    def test_quota_and_auth_rejection_stop_at_failed_batch_and_across_calls(self):
        for status in (401, 403, 429):
            sources.reset_openalex_run()
            dois = [f'10.1000/paper-{i}' for i in range(101)]
            calls = []
            def response(url, **kwargs):
                calls.append(url)
                if len(calls) == 2:
                    raise net.FetchError(f'HTTP {status} @ https://api.openalex.org?api_key=SECRET')
                return {'results': [{'doi': d, 'abstract_inverted_index': inverted(AB)} for d in requested(url)]}
            errors, progress, counts = [], {}, {}
            with self.subTest(status=status), patch.object(net, 'get_json', side_effect=response) as request:
                found = sources.abstracts_openalex(dois, 'SECRET', errors, progress=progress, counts=counts)
                self.assertEqual(len(found), 50)
                self.assertEqual(request.call_count, 2)
                self.assertEqual(counts['failed_batch'], 2)
                self.assertEqual(errors, [f'HTTP {status}'])
                reason = sources.openalex_disabled_reason()
                self.assertIn('第 2 批', reason)
                self.assertNotIn('SECRET', reason)
                self.assertEqual(progress[dois[0]]['checked'], ['OpenAlex'])
                self.assertEqual(progress[dois[50]]['failed'], ['OpenAlex'])
                self.assertNotIn(dois[100], progress)
                later = {}
                self.assertEqual(sources.abstracts_openalex([dois[100]], '', [], progress=later), {})
                self.assertEqual(later, {})
                with self.assertRaises(net.FetchError):
                    sources.openalex('ammonia', dt.date(2026, 10, 1))
                self.assertEqual(request.call_count, 2)

    def test_server_failure_stops_current_batch_but_does_not_latch_auth_block(self):
        with patch.object(net, 'get_json', side_effect=[net.FetchError('HTTP 503'), {'results': []}]) as request:
            errors = []
            sources.abstracts_openalex([D1]*60, '', errors)
            self.assertEqual(errors, ['HTTP 503'])
            self.assertFalse(sources.openalex_disabled_reason())
            sources.abstracts_openalex([D2], '', [])
            self.assertEqual(request.call_count, 2)

    def test_find_abstracts_always_second_and_checks_whole_candidate_cap(self):
        calls = []
        def response(url, **kwargs):
            if 'openalex' in url:
                calls.append(('OpenAlex', len(requested(url))))
                return {'results': []}
            if 'europepmc' in url:
                calls.append(('Europe PMC', 20))
                return {'resultList': {'result': []}}
            calls.append(('Crossref', 1))
            return {'message': {'DOI': sources.urllib.parse.unquote(url.rsplit('/', 1)[-1])}}
        def s2(url, payload, **kwargs):
            calls.append(('Semantic Scholar', len(payload['ids'])))
            return [None]*len(payload['ids'])
        with patch.object(net, 'post_json_retry', side_effect=s2), patch.object(net, 'get_json', side_effect=response):
            found, stats = sources.find_abstracts([f'10.1000/paper-{i}' for i in range(500)])
        self.assertEqual(calls[:5], [('Semantic Scholar', 100)]*5)
        self.assertEqual(calls[5:15], [('OpenAlex', 50)]*10)
        self.assertEqual(calls[15:30], [('Europe PMC', 20)]*15)
        self.assertEqual(calls[30:], [('Crossref', 1)]*20)
        self.assertFalse(found)
        self.assertIn('查 500 得 0', stats['OpenAlex'])

    def test_openalex_checks_s2_found_records_but_preserves_first_abstract_provenance(self):
        progress = {}
        with patch.object(net, 'post_json_retry', return_value=[{'externalIds': {'DOI': D1}, 'abstract': AB+'S2'}, None]), \
             patch.object(net, 'get_json', return_value={'results': [
                 {'doi': D1, 'abstract_inverted_index': inverted(AB+'OA')},
                 {'doi': D2, 'abstract_inverted_index': inverted(AB+'Other')} ]}) as request:
            found, stats = sources.find_abstracts([D1, D2], progress=progress)
        self.assertEqual(requested(request.call_args.args[0]), [D1, D2])
        self.assertEqual(found[D1], AB+'S2')
        self.assertEqual(progress[D1]['found_by'], 'Semantic Scholar')
        self.assertEqual(progress[D2]['found_by'], 'OpenAlex')
        self.assertIn('OpenAlex', progress[D1]['checked'])
        self.assertIn('查 2 得 2', stats['OpenAlex'])
        self.assertIn('新补 1 篇', stats['OpenAlex'])

    def test_blocked_openalex_leaves_other_fallbacks_and_records_no_fake_checks(self):
        def response(url, **kwargs):
            if 'openalex' in url:
                raise net.FetchError('HTTP 429 @ ?api_key=PRIVATE')
            return {'resultList': {'result': [{'doi': D1, 'abstractText': AB}]}}
        with patch.object(net, 'post_json_retry', side_effect=empty_s2), patch.object(net, 'get_json', side_effect=response) as request:
            progress = {}
            found, stats = sources.find_abstracts([D1, D2], progress=progress, epmc_cap=2, crossref_cap=0)
            self.assertEqual(found, {D1: AB.strip()})
            self.assertEqual(progress[D1]['found_by'], 'Europe PMC')
            self.assertEqual(progress[D2]['failed'], ['OpenAlex'])
            self.assertNotIn('PRIVATE', str(stats))
            next_progress = {}
            _, later = sources.find_abstracts([D1], progress=next_progress, crossref_cap=0)
            self.assertIn('查 0 得 0', later['OpenAlex'])
            self.assertIn('已停用', later['OpenAlex'])
            self.assertNotIn('OpenAlex', next_progress[D1]['attempted'])
            self.assertEqual(sum('openalex' in c.args[0] for c in request.call_args_list), 1)

    def test_daily_queries_are_keyless_or_keyed_and_share_rejection_state(self):
        record = {'doi': 'https://doi.org/'+D1, 'display_name': 'Ammonia synthesis',
                  'abstract_inverted_index': inverted(AB), 'publication_date': '2026-10-01'}
        for key in ('', 'PRIVATE'):
            with self.subTest(key=bool(key)), patch.object(net, 'get_json', return_value={'results': [record]}) as request:
                found = sources.openalex('ammonia, synthesis', dt.date(2026, 9, 1), key)
                query = params(request.call_args.args[0])
                self.assertEqual(query.get('api_key'), [key] if key else None)
                self.assertIn('from_publication_date:2026-09-01', query['filter'][0])
                self.assertEqual(found[0]['id'], D1)
                self.assertEqual(found[0]['abstract'], AB.strip())
                self.assertEqual(request.call_args.kwargs['retries'], 1)
        with patch.object(net, 'get_json', side_effect=net.FetchError('HTTP 403 @ ?api_key=PRIVATE')) as request:
            with self.assertRaises(net.FetchError) as error:
                sources.openalex('ammonia', dt.date(2026, 9, 1), 'PRIVATE')
            self.assertNotIn('PRIVATE', str(error.exception))
            progress = {}
            self.assertEqual(sources.abstracts_openalex([D1], '', [], progress=progress), {})
            self.assertEqual(progress, {})
            self.assertEqual(request.call_count, 1)


if __name__ == '__main__':
    unittest.main()
