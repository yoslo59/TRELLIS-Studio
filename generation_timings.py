"""Elapsed wall time between generation worker events, independent of queue time."""
import re
from sys import float_info
from time import monotonic


def _stage_name(stage):
    # Sampler steps, batch counters and the WTiVo heartbeat describe ongoing work.
    # Keep their original labels in status and the log, but one interval per phase.
    view = re.fullmatch(r'(albedo|mr) ([\d.\-]+) degrees, step \d+/\d+', stage)
    if view:
        return f'Preparing {view[1]} at {view[2]} degrees'
    if stage.startswith('WTiVo · '):
        return ' · '.join(stage.split(' · ')[:2])
    if stage.startswith('UltraTex memory estimate '):
        return None
    return re.sub(r' \(\d+/\d+(?: batches complete[^)]*)?\)$', '', stage)


def _close(entry, elapsed, *, incomplete=False):
    entry['duration_s'] = round(max(0, elapsed - entry['start_s']), 3)
    if incomplete:
        entry['incomplete'] = True


class GenerationTimings:
    def __init__(self):
        self.started = monotonic()
        self.entries = [dict(stage='Starting generation', progress=1, start_s=0, duration_s=None)]

    def record(self, state, stage, progress):
        now = monotonic()
        elapsed = round(now - self.started, 3)
        current = self.entries[-1]
        if state == 'running':
            name = _stage_name(stage)
            if name and current['stage'] != name:
                _close(current, elapsed)
                self.entries.append(dict(stage=name, progress=progress, start_s=elapsed, duration_s=None))
        else:
            _close(current, elapsed, incomplete=state != 'done')
        data = dict(version=1, elapsed_s=elapsed, entries=self.entries)
        # Python's monotonic clock is shared between the worker and its host process.
        # Only extend from this anchor while the host can confirm the worker is alive.
        return dict(stage_timings=data, **({'_timing_monotonic': now} if state == 'running' else {}))


def _seconds(value):
    return type(value) in (int, float) and 0 <= value <= float_info.max


def _valid_timing(timing):
    if not isinstance(timing, dict) or type(timing.get('version')) is not int or timing['version'] != 1:
        return False
    if not _seconds(timing.get('elapsed_s')):
        return False
    entries = timing.get('entries')
    if not isinstance(entries, list) or not entries:
        return False
    for index, entry in enumerate(entries):
        if not isinstance(entry, dict) or not isinstance(entry.get('stage'), str) or not entry['stage']:
            return False
        if not _seconds(entry.get('progress')) or entry['progress'] > 100:
            return False
        if not _seconds(entry.get('start_s')) or entry['start_s'] > timing['elapsed_s']:
            return False
        if 'duration_s' not in entry:
            return False
        duration = entry['duration_s']
        if duration is None:
            if index != len(entries) - 1:
                return False
        elif not _seconds(duration) or duration > timing['elapsed_s'] - entry['start_s'] + 0.001:
            return False
    return True


def project_timings(status, *, at=None):
    """Consume the private clock anchor on a freshly loaded status dictionary.

    When termination was observed, 'at' is a monotonic timestamp. After a crash
    it is unknown: stop at the last worker report instead of counting downtime.
    """
    anchor = status.pop('_timing_monotonic', None)
    if 'stage_timings' not in status:
        return
    timing = status['stage_timings']
    # Restored archives and older clients can supply partial or foreign
    # telemetry. A bad measurement must never hide a usable job or its model.
    if not _valid_timing(timing):
        del status['stage_timings']
        return
    if timing['entries'][-1]['duration_s'] is not None:
        return
    if status['state'] == 'running' and not _seconds(anchor):
        del status['stage_timings']
        return
    if _seconds(anchor) and _seconds(at):
        timing['elapsed_s'] = round(timing['elapsed_s'] + max(0, at - anchor), 3)
    if status['state'] != 'running':
        _close(timing['entries'][-1], timing['elapsed_s'], incomplete=True)
        if not _seconds(at) or not _seconds(anchor):
            timing['partial'] = True


def stopped_generation(previous, state, stage, *, at=None, **extra):
    """Preserve measured intervals when the host, rather than the worker, stops a job."""
    record = dict(state=state, stage=stage, progress=0, **extra)
    if 'stage_timings' in previous:
        record['stage_timings'] = previous['stage_timings']
        record['_timing_monotonic'] = previous.get('_timing_monotonic')
        project_timings(record, at=at)
    return record
