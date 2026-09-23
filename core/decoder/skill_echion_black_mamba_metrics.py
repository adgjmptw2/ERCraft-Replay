"""Exact retained-evidence route for Echion's Black Mamba stage.

The replay does not expose a universal SkillGroup -> EffectAndSound foreign
key.  This route therefore requires an exclusive runtime relation: every
observed effect 1044520 must map to exactly one 1044530 start, every combat
start must have a decoded finish, and the effect stream must be complete.
It never treats a missing effect as a hit and never uses a time-only fallback.
"""

from __future__ import annotations

from collections import defaultdict


GROUP = 1044530
EFFECT_CODE = 1044520
MIN_OBSERVATIONS = 3
MAX_EFFECT_DELAY_TICKS = 60
SIDEWINDER_GROUP = 1044540
SIDEWINDER_EFFECT_CODE = 1044540
SIDEWINDER_FINISH_SKILL_ID = 618


def _unavailable(spec, reason):
    return {
        **spec,
        "status": "unresolved-evidence",
        "attemptCount": None,
        "hitCount": None,
        "hitRate": None,
        "multiTargetAttemptCount": None,
        "multiTargetAttemptRate": None,
        "distinctEnemyTargetsSummedAcrossAttempts": None,
        "meanDistinctEnemyTargetsPerAttempt": None,
        "deduplicatedEnemyContactEventCount": None,
        "exactDamagePacketCount": None,
        "methods": [],
        "reason": reason,
        "fallbackUsed": False,
    }


def echion_black_mamba_metric(spec, starts, finishes, damages, player,
                              teams, intervals, effect_rows):
    return _unavailable(spec, 'withdrawn: 60-tick proximity is not a cast/projectile identity; exact producer link required')
    # Historical implementation retained only for the audit counterexample.
    if spec.get("skillGroup") != GROUP:
        return _unavailable(spec, "Echion Black Mamba route received an unexpected skill group")
    effects = {row.get("code") for row in effect_rows or []}
    if EFFECT_CODE not in effects:
        return _unavailable(spec, "exact Black Mamba effect code is absent from this gameDb")
    own_starts = sorted((s for s in starts if s.get("skillGroup") == GROUP),
                        key=lambda s: s.get("tick", -1))
    if len(own_starts) < MIN_OBSERVATIONS:
        return _unavailable(spec, f"fewer than {MIN_OBSERVATIONS} Black Mamba starts")
    combat = [s for s in own_starts if any(left <= s["tick"] < right
                                           for left, right in intervals)]
    if len(combat) < MIN_OBSERVATIONS:
        return _unavailable(spec, "fewer than three combat Black Mamba starts")
    own_effects = [d for d in damages or []
                   if d.get("attackerObjectId") == player
                   and d.get("effectCode") == EFFECT_CODE]
    # Every effect packet must have one and only one preceding Black Mamba
    # start in the explicit stage lifetime.  Ambiguity rejects the route.
    links = defaultdict(list)
    for event in own_effects:
        tick = event.get("tick")
        candidates = [s for s in own_starts
                      if s["tick"] <= tick <= s["tick"] + MAX_EFFECT_DELAY_TICKS]
        if len(candidates) != 1:
            return _unavailable(spec, "Black Mamba effect packets are not exclusive to one stage start")
        links[candidates[0]["tick"]].append(event)
    # Require a decoded finish for every combat start before absent effects
    # can count as misses.
    finish_ticks = {f.get("tick") for f in finishes or []
                    if f.get("playerObjectId") == player and f.get("skillIdCode") == 617}
    if any(not any(tick >= s["tick"] and tick <= s["tick"] + MAX_EFFECT_DELAY_TICKS
                   for tick in finish_ticks) for s in combat):
        return _unavailable(spec, "Black Mamba combat starts lack complete decoded finish coverage")
    attempts = len(combat)
    contacts = set()
    hit_count = 0
    for start in combat:
        events = links.get(start["tick"], [])
        enemy = {(e.get("tick"), e.get("targetObjectId")) for e in events
                 if e.get("targetObjectId") in teams
                 and teams[e["targetObjectId"]] != teams[player]}
        if enemy:
            hit_count += 1
            contacts.update(enemy)
    method = "reviewed-Echion-BlackMamba-exclusive-1044520-effect-lifetime"
    return {
        **spec,
        "status": "calculable-observed",
        "attemptCount": attempts,
        "hitCount": hit_count,
        "hitRate": round(hit_count / attempts, 6),
        "multiTargetAttemptCount": 0,
        "multiTargetAttemptRate": 0.0,
        "distinctEnemyTargetsSummedAcrossAttempts": len(contacts),
        "meanDistinctEnemyTargetsPerAttempt": round(len(contacts) / attempts, 6),
        "deduplicatedEnemyContactEventCount": len(contacts),
        "exactDamagePacketCount": sum(len(links.get(s["tick"], [])) for s in combat),
        "method": method,
        "methods": [method],
        "fallbackUsed": False,
    }


def echion_sidewinder_metric(spec, starts, finishes, damages, player,
                             teams, intervals, effect_rows):
    return _unavailable(spec, 'withdrawn: 60-tick proximity is not a cast/projectile identity; exact producer link required')
    """Exact retained-evidence route for the SideWinder stage.

    The route is deliberately separate from Black Mamba because the replay
    uses a different stage/effect pair.  Every observed effect must belong to
    exactly one stage start and every combat start must have its decoded
    skill-finish event before an absent effect can be counted as a miss.
    """
    if spec.get("skillGroup") != SIDEWINDER_GROUP:
        return _unavailable(spec, "Echion SideWinder route received an unexpected skill group")
    effects = {row.get("code") for row in effect_rows or []}
    if SIDEWINDER_EFFECT_CODE not in effects:
        return _unavailable(spec, "exact SideWinder effect code is absent from this gameDb")
    own_starts = sorted((s for s in starts if s.get("skillGroup") == SIDEWINDER_GROUP),
                        key=lambda s: s.get("tick", -1))
    if len(own_starts) < MIN_OBSERVATIONS:
        return _unavailable(spec, f"fewer than {MIN_OBSERVATIONS} SideWinder starts")
    combat = [s for s in own_starts if any(left <= s["tick"] < right
                                           for left, right in intervals)]
    if len(combat) < MIN_OBSERVATIONS:
        return _unavailable(spec, "fewer than three combat SideWinder starts")
    own_effects = [d for d in damages or []
                   if d.get("attackerObjectId") == player
                   and d.get("effectCode") == SIDEWINDER_EFFECT_CODE]
    links = defaultdict(list)
    for event in own_effects:
        tick = event.get("tick")
        candidates = [s for s in own_starts
                      if s["tick"] <= tick <= s["tick"] + MAX_EFFECT_DELAY_TICKS]
        if len(candidates) != 1:
            return _unavailable(spec, "SideWinder effect packets are not exclusive to one stage start")
        links[candidates[0]["tick"]].append(event)
    finish_ticks = {f.get("tick") for f in finishes or []
                    if f.get("playerObjectId") == player
                    and f.get("skillIdCode") == SIDEWINDER_FINISH_SKILL_ID}
    if any(not any(tick >= s["tick"] and tick <= s["tick"] + MAX_EFFECT_DELAY_TICKS
                   for tick in finish_ticks) for s in combat):
        return _unavailable(spec, "SideWinder combat starts lack complete decoded finish coverage")
    attempts = len(combat)
    contacts = set()
    hit_count = 0
    for start in combat:
        events = links.get(start["tick"], [])
        enemy = {(e.get("tick"), e.get("targetObjectId")) for e in events
                 if e.get("targetObjectId") in teams
                 and teams[e["targetObjectId"]] != teams[player]}
        if enemy:
            hit_count += 1
            contacts.update(enemy)
    method = "reviewed-Echion-SideWinder-exclusive-1044540-effect-lifetime"
    return {
        **spec,
        "status": "calculable-observed",
        "attemptCount": attempts,
        "hitCount": hit_count,
        "hitRate": round(hit_count / attempts, 6),
        "multiTargetAttemptCount": 0,
        "multiTargetAttemptRate": 0.0,
        "distinctEnemyTargetsSummedAcrossAttempts": len(contacts),
        "meanDistinctEnemyTargetsPerAttempt": round(len(contacts) / attempts, 6),
        "deduplicatedEnemyContactEventCount": len(contacts),
        "exactDamagePacketCount": sum(len(links.get(s["tick"], [])) for s in combat),
        "method": method,
        "methods": [method],
        "fallbackUsed": False,
    }
