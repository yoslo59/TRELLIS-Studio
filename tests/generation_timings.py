"""Generation graph measurements across repeated reports, polls and terminal states."""
from copy import deepcopy
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from generation_timings import GenerationTimings, project_timings, stopped_generation


class GenerationTimingTests(unittest.TestCase):
    def setUp(self):
        self.clock = self.enterContext(patch('generation_timings.monotonic', return_value=100))
        self.timer = GenerationTimings()

    def record(self, seconds, stage, state='running', progress=84):
        self.clock.return_value = 100 + seconds
        return dict(state=state, **self.timer.record(state, stage, progress))

    def test_repeated_progress_keeps_one_interval_per_actual_view(self):
        self.record(1, 'Preparing albedo at 0 degrees')
        self.record(2, 'UltraTex memory estimate 100 MiB, available 300 MiB')
        self.record(3, 'albedo 0 degrees, step 1/8')
        self.record(5, 'albedo 0 degrees, step 8/8')
        self.record(7, 'Preparing albedo at 45 degrees')
        self.record(9, 'albedo 45 degrees, step 2/8')
        self.record(12, 'Preparing mr at 0 degrees')
        final = self.record(16.25, 'Model ready', state='done')['stage_timings']
        self.assertEqual([row['duration_s'] for row in final['entries']], [1, 6, 5, 4.25])
        self.assertEqual(sum(row['duration_s'] for row in final['entries']), final['elapsed_s'])

    def test_geometry_steps_batches_and_wtivo_heartbeats(self):
        self.record(1, 'Sampling the sparse structure (1/24)')
        self.record(2, 'Sampling the sparse structure (2/24)')
        self.record(10, 'Filtering inner faces with QuadDC (0/64 batches complete)')
        self.record(11, 'Filtering inner faces with QuadDC (1/64 batches complete, about 4 min 03 s remaining)')
        self.record(12, 'WTiVo · Building tetrahedra · 0m 00s elapsed · 40m limit')
        self.record(1800, 'WTiVo · Building tetrahedra · 29m 48s elapsed · 40m limit')
        self.record(1801, 'WTiVo · Sampling the volume · 29m 49s elapsed · 40m limit')
        final = self.record(1803, 'Generation failed', 'error')['stage_timings']
        self.assertEqual([row['duration_s'] for row in final['entries']], [1, 9, 2, 1789, 2])
        self.assertTrue(final['entries'][-1]['incomplete'])

    def test_long_stage_live_polls_dont_count_twice(self):
        stored = self.record(4.321, 'Preparing the UV layout')
        for tick in (110, 111, 150, 151):
            response = deepcopy(stored)
            project_timings(response, at=tick)
            self.assertNotIn('_timing_monotonic', response)
            self.assertEqual(response['stage_timings']['elapsed_s'], tick - 100)
            self.assertIsNone(response['stage_timings']['entries'][-1]['duration_s'])
        self.assertEqual(stored['stage_timings']['elapsed_s'], 4.321)

    def test_cancel_restart_and_fresh_retry(self):
        self.record(3, 'Generating geometry')
        previous = self.record(12, 'Generating geometry')
        cancelled = stopped_generation(deepcopy(previous), 'cancelled', 'Generation cancelled', at=124.25)
        self.assertEqual(cancelled['stage_timings']['entries'][-1]['duration_s'], 21.25)
        self.assertNotIn('_timing_monotonic', cancelled)
        self.assertNotIn('partial', cancelled['stage_timings'])
        crashed = stopped_generation(previous, 'error', 'Interrupted')
        self.assertEqual(crashed['stage_timings']['entries'][-1]['duration_s'], 9)
        self.assertTrue(crashed['stage_timings']['partial'])
        project_timings(crashed, at=18000)
        self.assertEqual(crashed['stage_timings']['elapsed_s'], 12)
        retry = GenerationTimings().record('running', 'Starting generation', 1)
        self.assertEqual(len(retry['stage_timings']['entries']), 1)
        self.assertEqual(retry['stage_timings']['elapsed_s'], 0)

    def test_legacy_and_queue_have_no_fabricated_intervals(self):
        for state in ('queued', 'running', 'done', 'cancelled', 'error'):
            record = dict(state=state)
            project_timings(record, at=150)
            self.assertNotIn('stage_timings', record)
        self.assertNotIn('stage_timings', stopped_generation({'state': 'queued'}, 'cancelled', 'Removed'))

    def test_malformed_restored_telemetry_keeps_the_job_readable(self):
        stored = self.record(3, 'Preparing the UV layout')
        for broken in [None, [], {}, {'entries': []}, {'elapsed_s': '3'}, {'version': 2},
                       {'entries': [{}]}, {'entries': [False]}, {'entries': [stored['stage_timings']['entries'][-1]] * 2}]:
            with self.subTest(broken=broken):
                record = deepcopy(stored)
                record['stage_timings'] = {**record['stage_timings'], **broken} if isinstance(broken, dict) and broken else broken
                project_timings(record, at=123)
                self.assertEqual(record, {'state': 'running'})
        for bad in (True, '3', [], -1, float('inf'), float('nan'), 10**350):
            with self.subTest(value=bad):
                record = deepcopy(stored)
                record['stage_timings']['entries'][-1]['duration_s'] = bad
                project_timings(record)
                self.assertEqual(record, {'state': 'running'})
                record = deepcopy(stored)
                record['_timing_monotonic'] = bad
                project_timings(record, at=125)
                self.assertEqual(record, {'state': 'running'})


if __name__ == '__main__':
    unittest.main()
