#!/usr/bin/env python3
"""Quota freshness and safe rendering, without using an account or real home."""
import contextlib
import io
import json
from pathlib import Path
import tempfile
import unittest

script = Path(__file__).resolve().parents[1] / 'statusline-agy.sh'
body = script.read_text().split("3<<'PY'\n", 1)[1].rsplit('\nPY', 1)[0]
namespace = {'__name__': 'statusline_fixture'}
exec(compile(body, str(script), 'exec'), namespace)


class StatuslineTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.home = Path(directory.name)
        self.cache = self.home / '.gemini/antigravity-cli'
        self.now = 1791060000

    def frame(self, payload, seconds=0):
        now = self.now + seconds
        limits, fresh = namespace['save_limits'](payload, self.cache, now)
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            namespace['render'](payload, self.home, limits, fresh, now)
        persisted = json.loads((self.cache / 'last-limits.json').read_text())
        return persisted, output.getvalue()

    def test_actual_cli_shape_and_independent_bucket_timestamps(self):
        payload = {'version': '1.2.16', 'model': {'display_name': 'Gemini'},
                   'quota': {'gemini-weekly': {'remaining_fraction': .1032816,
                       'reset_time': '2026-10-10T20:17:23Z'},
                       '3p-weekly': {'remaining_fraction': .300829,
                       'reset_time': '2026-10-10T20:37:18Z'}},
                   'context_window': {'used_percentage': 4.56, 'total_input_tokens': 47842}}
        saved, text = self.frame(payload)
        self.assertEqual(saved['gemini_pct_remaining'], 10.33)
        self.assertEqual(saved['third_party_pct_remaining'], 30.08)
        self.assertEqual(saved['third_party_resets_at'], '2026-10-10T20:37:18Z')
        self.assertEqual(saved['third_party_seen_at'], self.now)
        self.assertIn('ctx:5%', text)
        self.assertIn('47k toks', text)
        saved, text = self.frame({'quota': {'3p-weekly': {'remaining_fraction': 0,
                                'reset_time': '2026-10-10T20:37:18Z'}}}, 60)
        self.assertEqual(saved['gemini_seen_at'], self.now)
        self.assertEqual(saved['third_party_seen_at'], self.now + 60)
        self.assertEqual(saved['third_party_pct_remaining'], 0)
        self.assertIn('Gemini:10% (cached)', text)
        self.assertIn('3P:0%', text)

    def test_context_only_frame_cannot_refresh_quota(self):
        first, _ = self.frame({'quota': {'gemini-weekly': {'remaining_fraction': .2}}})
        saved, text = self.frame({'context_window': {'used_percentage': 0}}, 3600)
        self.assertEqual(saved['gemini_seen_at'], first['gemini_seen_at'])
        self.assertEqual(saved['updated_at'], self.now + 3600)
        self.assertEqual(saved['context_pct'], 0)
        self.assertIn('(cached)', text)

    def test_third_party_only_observation_is_saved(self):
        saved, text = self.frame({'quota': {'3p-weekly': {'remaining_fraction': .75}}})
        self.assertEqual(saved['third_party_pct_remaining'], 75)
        self.assertIn('3P:75%', text)
        self.assertNotIn('gemini_seen_at', saved)

    def test_paths_and_model_names_are_data(self):
        model = 'Model "quoted" \\name\nsecond line'
        saved, text = self.frame({'model': {'display_name': model},
            'workspace': {'project_dir': '/work', 'current_dir': "/work/quote');raise Exception('oops"}})
        self.assertEqual(saved['model'], 'Model "quoted" \\namesecond line')
        self.assertIn("quote');raise Exception('oops", text)
        self.assertEqual(len(text.splitlines()), 3)

    def test_invalid_measurements_cannot_replace_valid_quota(self):
        self.frame({'quota': {'gemini-weekly': {'remaining_fraction': .5}}})
        for value in ('0.99', True, -1, 2, 10**400, float('nan')):
            saved, text = self.frame({'quota': {'gemini-weekly': {'remaining_fraction': value}}}, 60)
            self.assertEqual(saved['gemini_pct_remaining'], 50)
            self.assertEqual(saved['gemini_seen_at'], self.now)
            self.assertIn('(cached)', text)

    def test_installed_plugin_does_not_claim_connection(self):
        (self.home / '.gemini/config/plugins/knowledge-graph').mkdir(parents=True)
        _, text = self.frame({})
        self.assertIn('kg installed', text)
        _, text = self.frame({'mcpServers': [{'name': 'kg', 'enabled': True, 'status': 'failed'}]})
        self.assertIn('none connected', text)
        _, text = self.frame({'mcpServers': {'kg': {'status': 'connected'}}})
        self.assertIn('🔗 kg\033', text)

    def test_expired_cached_window_is_not_a_live_percentage(self):
        self.frame({'quota': {'gemini-weekly': {'remaining_fraction': .1,
                                'reset_time': '2026-10-02T00:00:00Z'}}})
        _, text = self.frame({}, 60)
        self.assertIn('Gemini:– (reset passed)', text)
        self.assertNotIn('Gemini:10%', text)

    def test_atomic_file_and_no_raw_account_payload(self):
        self.frame({'email': 'private@example.invalid', 'quota': {'gemini-weekly': {'remaining_fraction': .8}}})
        self.assertEqual([p.name for p in self.cache.iterdir()], ['last-limits.json'])
        self.assertNotIn('private@', (self.cache / 'last-limits.json').read_text())


if __name__ == '__main__':
    unittest.main(verbosity=2)
