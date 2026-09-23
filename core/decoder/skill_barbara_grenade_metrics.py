"""Exact retained-evidence route for Barbara's Magnet Grenade stage."""

from __future__ import annotations

from collections import defaultdict


GROUP = 1026400
EFFECT_CODE = 1026401
FINISH_SKILL_ID = 355
MAX_EFFECT_DELAY_TICKS = 60
MIN_OBSERVATIONS = 3


def _unavailable(spec, reason):
    return {**spec, "status": "unresolved-evidence", "attemptCount": None,
            "hitCount": None, "hitRate": None, "multiTargetAttemptCount": None,
            "multiTargetAttemptRate": None,
            "distinctEnemyTargetsSummedAcrossAttempts": None,
            "meanDistinctEnemyTargetsPerAttempt": None,
            "deduplicatedEnemyContactEventCount": None,
            "exactDamagePacketCount": None, "methods": [], "reason": reason,
            "fallbackUsed": False}


def barbara_grenade_metric(spec, starts, finishes, damages, player, teams,
                           intervals, effect_rows):
    return _unavailable(spec, 'withdrawn: 60-tick parent/effect proximity does not bind the owned explosion; exact parent-projectile-child route required')
    # Historical implementation retained only for the audit counterexample.
    if spec.get("skillGroup") != GROUP:
        return _unavailable(spec, "Barbara grenade route received an unexpected skill group")
    if EFFECT_CODE not in {row.get("code") for row in effect_rows or []}:
        return _unavailable(spec, "exact Barbara grenade effect code is absent from this gameDb")
    own_starts = sorted((s for s in starts if s.get("skillGroup") == GROUP),
                        key=lambda s: s.get("tick", -1))
    if len(own_starts) < MIN_OBSERVATIONS:
        return _unavailable(spec, "fewer than three Barbara grenade starts")
    combat = [s for s in own_starts if any(left <= s["tick"] < right
                                           for left, right in intervals)]
    if len(combat) < MIN_OBSERVATIONS:
        return _unavailable(spec, "fewer than three combat Barbara grenade starts")
    own_effects = [d for d in damages or []
                   if d.get("attackerObjectId") == player
                   and d.get("effectCode") == EFFECT_CODE]
    links = defaultdict(list)
    for event in own_effects:
        candidates = [s for s in own_starts
                      if s["tick"] <= event.get("tick", -1)
                      <= s["tick"] + MAX_EFFECT_DELAY_TICKS]
        if len(candidates) != 1:
            return _unavailable(spec, "Barbara grenade effect packets are not exclusive to one stage start")
        links[candidates[0]["tick"]].append(event)
    finish_ticks = {f.get("tick") for f in finishes or []
                    if f.get("playerObjectId") == player
                    and f.get("skillIdCode") == FINISH_SKILL_ID}
    if any(not any(s["tick"] <= tick <= s["tick"] + MAX_EFFECT_DELAY_TICKS
                   for tick in finish_ticks) for s in combat):
        return _unavailable(spec, "Barbara grenade combat starts lack complete decoded finish coverage")
    normalized_teams = {int(key): value for key, value in teams.items()}
    attempts = len(combat)
    contacts = set()
    hit_count = 0
    for start in combat:
        enemy = {(event.get("tick"), event.get("targetObjectId"))
                 for event in links.get(start["tick"], [])
                 if event.get("targetObjectId") in normalized_teams
                 and normalized_teams[event["targetObjectId"]] != normalized_teams[player]}
        if enemy:
            hit_count += 1
            contacts.update(enemy)
    method = "reviewed-Barbara-MagnetGrenade-exclusive-1026401-effect-lifetime"
    return {**spec, "status": "calculable-observed", "attemptCount": attempts,
            "hitCount": hit_count, "hitRate": round(hit_count / attempts, 6),
            "multiTargetAttemptCount": 0, "multiTargetAttemptRate": 0.0,
            "distinctEnemyTargetsSummedAcrossAttempts": len(contacts),
            "meanDistinctEnemyTargetsPerAttempt": round(len(contacts) / attempts, 6),
            "deduplicatedEnemyContactEventCount": len(contacts),
            "exactDamagePacketCount": sum(len(links.get(s["tick"], [])) for s in combat),
            "method": method, "methods": [method], "fallbackUsed": False}
