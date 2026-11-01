import tempfile
import time
import unittest
from unittest.mock import patch

import app as web


class WorkflowTest(unittest.TestCase):
    def setUp(self):
        web._clear_task_data()
        self.client = web.app.test_client()
        self.client.environ_base.update({'CONTENT_TYPE': 'application/json'})
        self.tmp = tempfile.TemporaryDirectory()
        self.cache = patch.object(web, 'TMDB_CACHE_DIR', self.tmp.name)
        self.cache.start()

    def tearDown(self):
        self.cache.stop()
        self.tmp.cleanup()
        web._clear_task_data()

    def wait(self):
        for _ in range(300):
            status = self.client.get('/api/status').get_json()
            if not status['running']:
                return status
            time.sleep(.01)
        self.fail('task did not finish')

    def test_refetch_invalidates_results_and_match_cache_is_reused(self):
        torrent = {'hash': 'abc', 'name': 'Example.2020.1080p', 'category': 'movies'}
        result = {'tmdb_id': '123', 'tmdb_title_cn': 'Film', 'tmdb_title_en': 'Film', 'tmdb_year': '2020', 'tmdb_rating': 8}
        with patch.object(web, 'QBClient') as qb, patch.object(web, '_update_collection_flags'), patch.object(web, 'TMDBClient') as tmdb:
            qb.return_value.get_torrents.return_value = [torrent]
            tmdb.return_value.match_entry.return_value = result
            self.assertEqual(self.client.post('/api/analyze/start').status_code, 400)
            self.client.post('/api/torrents/fetch', json={'categories': ['movies']})
            self.assertTrue(self.wait()['completed']['fetch'])
            self.client.post('/api/tmdb/match')
            self.assertTrue(self.wait()['completed']['tmdb'])
            self.assertEqual(tmdb.return_value.match_entry.call_count, 1)
            with web._task_state['lock']:
                web._task_state['profiles'] = [web.MediaProfile(torrent_hash='abc')]
                web._task_state['dedup_results'] = [{'group_key': 'old'}]
                web._task_state['completed']['analyze'] = True
                web._task_state['completed']['dedup'] = True
            self.client.post('/api/torrents/fetch', json={'categories': ['movies']})
            status = self.wait()
            self.assertFalse(status['completed']['tmdb'])
            self.assertFalse(status['completed']['analyze'])
            self.assertFalse(status['completed']['dedup'])
            self.assertEqual(self.client.get('/api/analyze/profiles').get_json()['count'], 0)
            self.assertEqual(self.client.get('/api/dedup/results').get_json()['groups'], [])
            self.client.post('/api/tmdb/match')
            self.assertTrue(self.wait()['completed']['tmdb'])
            self.assertEqual(tmdb.return_value.match_entry.call_count, 1)
            self.assertEqual(self.client.delete('/api/cache/tmdb').get_json()['cleared'], 1)
            self.client.post('/api/tmdb/match')
            self.wait()
            self.assertEqual(tmdb.return_value.match_entry.call_count, 2)

    def test_pause_state_is_exposed_and_requires_the_right_running_task(self):
        self.assertEqual(self.client.post('/api/tmdb/pause').status_code, 409)
        with web._task_state['lock']:
            web._task_state['running'] = True
            web._task_state['current_step'] = 'tmdb'
        self.assertEqual(self.client.post('/api/analyze/pause').status_code, 409)
        self.assertTrue(self.client.post('/api/tmdb/pause').get_json()['paused'])
        self.assertTrue(self.client.get('/api/status').get_json()['paused'])
        self.assertTrue(self.client.get('/api/progress').get_json()['paused'])
        self.assertFalse(self.client.post('/api/tmdb/pause').get_json()['paused'])

    def test_anonymous_access_and_origin_protection(self):
        anonymous = web.app.test_client()
        self.assertEqual(anonymous.get('/api/status').status_code, 200)
        self.assertEqual(self.client.post('/api/reset', headers={'Origin': 'http://attacker.invalid'}).status_code, 403)
        self.assertEqual(self.client.post('/api/reset', content_type='application/x-www-form-urlencoded').status_code, 415)

    def test_delete_verifies_results_and_invalidates_dedup(self):
        with web._task_state['lock']:
            web._task_state['torrents'] = [{'hash': 'a'}, {'hash': 'b'}, {'hash': 'c'}]
            web._task_state['profiles'] = [web.MediaProfile(torrent_hash=h) for h in 'abc']
            web._task_state['tmdb_matches'] = [{'torrent_hash': h} for h in 'abc']
            web._task_state['dedup_results'] = [{'group_key': 'stale'}]
            web._task_state['completed']['dedup'] = True
        with patch.object(web, 'QBClient') as qb:
            qb.return_value.get_torrents.side_effect = [[{'hash': 'a'}, {'hash': 'b'}], [{'hash': 'b'}]]
            result = self.client.post('/api/torrents/delete', json={'hashes': ['a', 'b', 'c']}).get_json()
            self.assertEqual((result['deleted'], result['remaining'], result['missing']), (1, 1, 1))
            qb.return_value.delete_torrents.assert_called_once_with(['a', 'b'], delete_files=True)
            self.assertEqual([t['hash'] for t in web._task_state['torrents']], ['b'])
            self.assertEqual(web._task_state['dedup_results'], [])
            self.assertFalse(web._task_state['completed']['dedup'])

    def test_delete_verification_failure_requires_refetch(self):
        with web._task_state['lock']:
            web._task_state['torrents'] = [{'hash': 'a'}]
            web._task_state['completed'].update(dict.fromkeys(('fetch', 'tmdb', 'analyze', 'dedup'), True))
        with patch.object(web, 'QBClient') as qb:
            qb.return_value.get_torrents.side_effect = [[{'hash': 'a'}], RuntimeError('offline')]
            response = self.client.post('/api/torrents/delete', json={'hashes': ['a']})
            self.assertEqual(response.status_code, 500)
            self.assertTrue(response.get_json()['requires_refresh'])
            self.assertFalse(web._task_state['completed']['fetch'])

    def test_analysis_failure_is_visible_and_blocks_dedup(self):
        with web._task_state['lock']:
            web._task_state['torrents'] = [{'hash': 'a', 'name': 'movie'}]
            web._task_state['completed']['tmdb'] = True
        with patch.object(web, 'analyze_torrents', side_effect=RuntimeError('media unavailable')):
            self.client.post('/api/analyze/start')
            status = self.wait()
            self.assertIn('media unavailable', status['error'])
            self.assertFalse(status['completed']['analyze'])
            self.assertEqual(self.client.post('/api/dedup/run').status_code, 400)

    def test_cache_clear_rejected_while_running(self):
        with web._task_state['lock']:
            web._task_state['running'] = True
        self.assertEqual(self.client.delete('/api/cache/tmdb').status_code, 409)


if __name__ == '__main__':
    unittest.main()
