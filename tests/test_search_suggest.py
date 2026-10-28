"""Live search contract; database queries are mocked, permissions are exercised."""
import importlib
import os
import unittest
from types import SimpleNamespace
from unittest.mock import patch


class SearchSuggestTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from aggregator import create_app
        with patch.dict(os.environ, DATABASE_URL='sqlite://', SECRET_KEY='search-test-key'):
            cls.app = create_app()
        cls.app.config.update(TESTING=True, WTF_CSRF_ENABLED=False)
        cls.module = importlib.import_module('aggregator.blueprints.admin.search_page')

    def setUp(self):
        self.client = self.app.test_client()
        self.user = patch('aggregator.permissions.current_user',
                          SimpleNamespace(is_authenticated=True, has_role=lambda role: role == 'reader'))
        self.user.start()
        self.addCleanup(self.user.stop)

    def test_short_query_does_not_search(self):
        with patch.object(self.module, 'search_story_ids') as search:
            response = self.client.get('/admin/search/suggest?q=a')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json['stories'], [])
        search.assert_not_called()

    def test_window_and_ranked_results(self):
        story = dict(id=7, headline='<img src=x onerror=alert(1)>', articles=3,
                     sources=2, left=1, center=1, right=1, updated=None)
        with patch.object(self.module, 'search_story_ids', return_value=[7]) as search, \
             patch.object(self.module, 'search_article_ids', return_value=[]), \
             patch.object(self.module, '_story_rows', return_value=([story], SimpleNamespace(total=1))) as rows, \
             patch.object(self.module, '_article_rows', return_value=([], None)):
            response = self.client.get('/admin/search/suggest?q=names&range=custom&start=2026-10-01&end=2026-10-02')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json['stories'][0]['title'], story['headline'])
        self.assertEqual(response.json['stories'][0]['url'], '/story/7')
        self.assertIn('start=2026-10-01', response.json['all_url'])
        self.assertEqual(search.call_args.kwargs['limit'], 1000)
        self.assertIsNotNone(search.call_args.kwargs['until'])
        self.assertEqual(rows.call_args.args[2], 'relevance')
        self.assertEqual(rows.call_args.kwargs['per_page'], 5)

    def test_backend_failure_uses_sql_fallback(self):
        with patch.object(self.module, 'search_story_ids', side_effect=self.module.SearchUnavailableError('offline')), \
             patch.object(self.module, '_story_rows', return_value=([], SimpleNamespace(total=0))) as rows, \
             patch.object(self.module, '_article_rows', return_value=([], None)):
            response = self.client.get('/admin/search/suggest?q=names')
        self.assertEqual(response.status_code, 200)
        self.assertIsNone(rows.call_args.args[0])
        self.assertEqual(rows.call_args.args[2], 'updated')

    def test_anonymous_cannot_access_private_search(self):
        with patch('aggregator.permissions.current_user', SimpleNamespace(is_authenticated=False)):
            self.assertEqual(self.client.get('/admin/search/suggest?q=names').status_code, 302)

    def test_insufficient_role_forbidden(self):
        with patch('aggregator.permissions.current_user',
                   SimpleNamespace(is_authenticated=True, has_role=lambda role: False)), \
             patch('flask.render_template', return_value='Forbidden'):
            self.assertEqual(self.client.get('/admin/search/suggest?q=names').status_code, 403)
