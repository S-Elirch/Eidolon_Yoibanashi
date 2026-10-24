"""Pure visit policy: no database, model, HTTP or test-clock access."""
import hashlib
import json
import math
from dataclasses import dataclass
from datetime import timedelta

DWELL_RANGES = ((5, 8), (10, 12), (15, 18))
OPPORTUNITY_PROBABILITIES = {'intimate': .85, 'close': .70, 'trust': .50, 'distant': 0, 'cold': 0}

def validate_probabilities(values):
    if (len(values) != 3 or any(not math.isfinite(x) or x < 0 for x in values)
            or not math.isclose(sum(values), 100, abs_tol=1e-8)):
        raise ValueError('Fast/medium/slow probabilities must be finite, nonnegative and total 100')

def validate_ranges(ranges):
    if len(ranges) != 3 or any(len(r) != 2 or not all(math.isfinite(x) for x in r)
                              or not 0 <= r[0] <= r[1] <= 3600 for r in ranges):
        raise ValueError('Provide three finite [min,max] second ranges within 0..3600')

@dataclass(frozen=True)
class WaitProfile:
    """Extended local hours: 27=next-day 03:00; percentages interpolate linearly.

    Only waiting is configured here. Eligibility and opportunity periods are separate.
    """
    ranges: tuple = DWELL_RANGES
    anchors: tuple = ((22, (40, 60, 0)), (27, (20, 40, 40)), (29, (0, 0, 100)))

    def __post_init__(self):
        validate_ranges(self.ranges)
        if not self.anchors or not 0 <= self.anchors[0][0] < 24:
            raise ValueError('First anchor hour must be within 0..24')
        previous = -1
        for hour, values in self.anchors:
            if not math.isfinite(hour) or not previous < hour <= self.anchors[0][0] + 24:
                raise ValueError('Anchor hours must increase within a single 24-hour cycle')
            validate_probabilities(values)
            previous = hour

    def probabilities_at(self, now):
        hour = now.hour + now.minute / 60 + now.second / 3600 + now.microsecond / 3_600_000_000
        if hour < self.anchors[0][0]:
            hour += 24
        for (left, a), (right, b) in zip(self.anchors, self.anchors[1:]):
            if hour <= right:
                fraction = (hour-left)/(right-left)
                return tuple(x+(y-x)*fraction for x,y in zip(a,b))
        return self.anchors[-1][1]

DEFAULT_WAIT_PROFILE = WaitProfile()

@dataclass(frozen=True)
class ScheduleDecision:
    """Stable schedule phase changes waiting only; existing tasks resume first."""
    allowed: bool = True
    draw_key: str = ''
    probabilities: tuple | None = None
    ranges: tuple | None = None

    def __post_init__(self):
        if self.probabilities is not None:
            validate_probabilities(self.probabilities)
        if self.ranges is not None:
            validate_ranges(self.ranges)

def probability(tier):
    return 1 if tier is None else OPPORTUNITY_PROBABILITIES.get(tier, 0)

def night_key(now):
    return (now - timedelta(hours=5)).date().isoformat()

def first_body_deadline(now):
    return now.replace(hour=5, minute=0, second=0, microsecond=0) + (timedelta(days=1) if now.hour >= 22 else timedelta())

def eligibility(now, utc_now, relationship, current, busy):
    if relationship is not None and (relationship.cooling or relationship.tier not in ('trust','close','intimate')):
        return 'relationship_blocked'
    if 5 <= now.hour < 22:
        return 'outside_window'
    if current is None or current.ended or not current.has_turns or current.last_contact is None:
        return 'conversation_unavailable'
    if current.reached_out_at and current.reached_out_at >= current.last_contact:
        return 'unanswered'
    if utc_now-current.last_contact < timedelta(minutes=30):
        return 'recent_contact'
    if busy:
        return 'busy'
    return None

def dwell(c, night, decision, override=-1, *, now, profile=DEFAULT_WAIT_PROFILE):
    if not decision.allowed:
        return dict(seconds=None, deferred=True, reason='schedule')
    seed = json.dumps([c.user_id, c.character_id, night, decision.draw_key]).encode()
    tier_draw = int.from_bytes(hashlib.sha256(seed + b':tier').digest()[:4], 'big') / 2**32
    probabilities = decision.probabilities if decision.probabilities is not None else profile.probabilities_at(now)
    ranges = decision.ranges if decision.ranges is not None else profile.ranges
    if override not in (-1, 0, 1, 2):
        raise ValueError('Unknown wait tier')
    cumulative = 0
    tier = 2
    for index, percent in enumerate(probabilities):
        cumulative += percent / 100
        if tier_draw < cumulative:
            tier = index
            break
    if override >= 0:
        tier = override
    low, high = ranges[tier]
    # Keep the within-range position even when time moves the draw into a slower tier.
    fraction = int.from_bytes(hashlib.sha256(seed + b':seconds').digest()[:4], 'big') / 2**32
    return dict(seconds=low + (high-low)*fraction, tier=tier, deferred=False)
