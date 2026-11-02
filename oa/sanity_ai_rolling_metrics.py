"""Estimate one rolling stage by scheduling remaining work onto bounded lanes."""
import math

from common.sanity_common import ReviewError


def eta(event, state, wave, timing, measured, samples, records):
    from oa.sanity_ai_jobs import _timestamp
    try:
        width = state.get('allocated_concurrency',event['data']['batch']['max_concurrency'])
        baseline = timing['base_seconds']
        seconds = state['round_seconds'] - baseline
        if (type(width) is not int or not 1 <= width <= 1000000 or
                type(baseline) not in (int,float) or baseline < 0 or
                not math.isfinite(seconds) or seconds <= 0):
            return None
        leaf = state.get('phase') in ('CLASSIFY','REVIEW')
        work = records if leaf else samples
        if work <= 0:
            return None
        per_unit = seconds / work
        active = [slot for slot in wave if slot['status']=='RESERVED']
        if len(active) > width:
            return None
        idle = width-len(active)
        lanes = []
        for slot in active:
            if type(slot.get('work')) is not int or slot['work'] <= 0:
                return None
            age = (measured-_timestamp(slot['reserved_at'])).total_seconds()
            # Refill reservations can be newer than the last completion sample.
            # Their delay is included relative to that fixed measurement time.
            if abs(age) > 604800:
                return None
            lanes.append(max(0.0,per_unit*slot['work']-age))
        pending = state['round_total'] - state['next_index']
        if type(pending) is not int or pending < 0:
            return None
        if not pending:
            return max(lanes,default=0.0)
        remaining = event['data']['batch']['total_records']-state['records']-sum(slot['work'] for slot in active) if leaf else pending
        if remaining < pending:
            return None
        duration = per_unit * remaining / pending
        # Queued descriptor sizes are approximated by their remaining average.
        # Binary search avoids iterating over an arbitrarily large input plan
        # on every progress snapshot. Empty lanes are counted in aggregate.
        low = 0.0 if idle else min(lanes)
        high = max(lanes,default=0.0) + math.ceil(pending/width)*duration
        for _ in range(48):
            middle = (low+high)/2
            capacity = idle*max(0,math.floor(middle/duration)) + sum(
                max(0,math.floor((middle-lane)/duration)) for lane in lanes)
            if capacity >= pending:
                high = middle
            else:
                low = middle
        result = max(max(lanes,default=0.0),high)
        return result if math.isfinite(result) and 0 <= result <= 604800 else None
    except (ReviewError,KeyError,TypeError,ValueError,OverflowError,ZeroDivisionError):
        return None
