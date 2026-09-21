import copy
import math
import unittest
from unittest.mock import patch
import app_v2 as app

class DgbTests(unittest.TestCase):
    def miner(self, pool='DGB SHA-256d', th=2, online=True):
        return dict(name='Test', pool=pool, coin=app.POOL_COINS[pool], th=th,
                    online=online, location_scope='LOCAL', best_diff=1e8, best_session_diff=2e7)

    def test_assignment(self):
        self.assertEqual(app.validate_miner_identity(dict(name='A', ip='10.0.0.2', type='axeos', pool='DGB SHA-256d'))['coin'], 'DGB')
        miners = [self.miner(), self.miner(th=99, online=False)]
        summary = app.solo_pool_summary(miners, 'DGB SHA-256d')
        self.assertEqual((summary['assigned_count'], summary['active_count'], summary['current_hashrate_th']), (2, 1, 2))
        self.assertEqual(summary['assigned_miners'][1]['hashrate_th'], 0)
        self.assertEqual(app.solo_pool_summary(miners, 'Umbrel Solo')['assigned_count'], 0)
        miners[0]['pool'] = 'Umbrel Solo'
        self.assertEqual(app.solo_pool_summary(miners, 'DGB SHA-256d')['current_hashrate_th'], 0)
        self.assertEqual(app.solo_pool_summary(miners, 'Umbrel Solo')['current_hashrate_th'], 2)

    def test_odds(self):
        with patch.object(app, 'get_network_difficulty', return_value=1e9):
            odds = app.build_odds([self.miner()], {})
        share = 2e12 / (1e9 * 2**32 / 75)
        for field, seconds in [('day_den', 86400), ('month_den', 86400*30)]:
            self.assertAlmostEqual(odds['DGB SHA-256d'][field], 1 / -math.expm1(seconds/75 * math.log1p(-share)))
        page = app.build_page3_payload(dict(miners=[self.miner()], odds=odds))
        self.assertEqual(page['dgb_solo']['best_network_pct'], 10)
        self.assertEqual(page['dgb_solo']['session_best'], 2e7)

    def test_stability(self):
        p = app.dgb_probability_for_hashrate(1e-12, 86400, 1e25)
        self.assertGreater(p, 0)
        self.assertAlmostEqual(p / (86400 / (1e25 * 2**32)), 1)
        self.assertEqual(app.dgb_probability_for_hashrate(1e10, 86400, 1), 1)
        for th, diff in [(0, 1), (-1, 1), (1, None), (1, 0), (1, float('nan')), (float('inf'), 1)]:
            self.assertEqual(app.dgb_probability_for_hashrate(th, 86400, diff), 0)

    def test_source_cache_failure(self):
        valid = {'time': 1000, 'pools': {'digibyte-sha256': {'symbol': 'DGB', 'algorithm': 'sha256d', 'poolStats': {'networkDiff': '1234'}}}}
        with patch.dict(app.DGB_NETWORK_CACHE, timestamp=None, difficulty=None), patch.object(app.time, 'time', return_value=1000), patch.object(app, 'fetch_json_url', return_value=valid) as fetch:
            self.assertEqual(app.get_network_difficulty('DGB'), 1234)
            self.assertEqual(app.get_network_difficulty('DGB'), 1234)
            self.assertEqual(fetch.call_count, 1)
            fetch.return_value = None
            with patch.object(app.time, 'time', return_value=1061):
                self.assertIsNone(app.get_network_difficulty('DGB'))
                self.assertIsNone(app.get_network_difficulty('DGB'))
            self.assertEqual(fetch.call_count, 2)
        invalid = [None, {'difficulty': 99}, {'time': 0, 'pools': valid['pools']}]
        for key, value in [('algorithm', 'scrypt'), ('symbol', 'BTC'), ('poolStats', {'networkDiff': 'NaN'}), ('poolStats', {'networkDiff': -1})]:
            item = copy.deepcopy(valid)
            item['pools']['digibyte-sha256'][key] = value
            invalid.append(item)
        for item in invalid:
            with self.subTest(item=item), patch.dict(app.DGB_NETWORK_CACHE, timestamp=None), patch.object(app.time, 'time', return_value=1000), patch.object(app, 'fetch_json_url', return_value=item):
                self.assertIsNone(app.get_network_difficulty('DGB'))

    def test_allocation_regression_and_unavailable(self):
        old = [self.miner(pool=pool) for pool in ['Umbrel Solo', 'BCH SoloPool', 'Braiins']]
        dgb = [self.miner(th=4), self.miner(th=100, online=False)]
        with patch.object(app, 'get_network_difficulty', side_effect=lambda coin: None if coin == 'DGB' else 1e12):
            old_odds = app.build_odds(old, {})
            new_odds = app.build_odds(old+dgb, {})
        for pool in old_odds:
            self.assertEqual(old_odds[pool], new_odds[pool])
        before = app.build_page3_payload(dict(miners=old, odds=old_odds))
        after = app.build_page3_payload(dict(miners=old+dgb, odds=new_odds))
        for pool in ['btc_solo', 'bch_solo', 'braiins']:
            self.assertEqual(before[pool], after[pool])
        self.assertEqual(after['total_th'], 10)
        self.assertEqual(after['allocation'], dict(btc_solo_pct=20, bch_solo_pct=20, dgb_solo_pct=40, braiins_pct=20))
        self.assertIsNone(after['dgb_solo']['best_network_pct'])
        for field in ['difficulty', 'day_den', 'month_den']:
            self.assertIsNone(after['dgb_solo']['odds'][field])

    def test_collector_publishes_dgb_summary(self):
        from contextlib import ExitStack
        miners = [self.miner(), self.miner(online=False, th=0)]
        with ExitStack() as stack:
            for name, value in [('load_miners', miners), ('collect_miners', miners),
                                ('record_history', None), ('update_pool_runs', {}),
                                ('fetch_solopool_stats', {'blocks': []}),
                                ('fetch_braiins_stats', {}), ('get_network_difficulty', 1e9)]:
                stack.enter_context(patch.object(app, name, return_value=value))
            stack.enter_context(patch.object(app.ALERT_MANAGER, 'process'))
            snapshot = app.collect_dashboard_snapshot()
        summary = snapshot['solo_pools']['DGB SHA-256d']
        self.assertEqual(summary['assigned_count'], 2)
        self.assertEqual(summary['active_count'], 1)
        self.assertEqual(summary['current_hashrate_th'], 2)
        self.assertGreater(snapshot['odds']['DGB SHA-256d']['day_den'], 0)
