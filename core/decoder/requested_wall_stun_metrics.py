"""Exact E stun application during the same victim's knockback lifetime.

An E-specific Stun code is required. A generic Stun or nearby crowd-control
packet cannot establish the wall cause and is retained only as a diagnostic.
"""

from collections import Counter, defaultdict


def calculate_wall_stun_metrics(spec, starts, finishes, states, player, teams,
                               intervals, skill_rows, state_rows, state_groups):
    try:
        from .requested_skill_hit_rates import _result, _unavailable
    except ImportError:
        from requested_skill_hit_rates import _result, _unavailable
    group = spec['skillGroup']
    skill_codes = {s['code'] for s in skill_rows if s.get('group') == group}
    definitions = {s['group']: s for s in state_groups}
    state_by_code = {s['code']: s for s in state_rows}
    stun_codes = {s['code'] for s in state_rows
                  if s.get('group') == group and s['code'] in skill_codes
                  and definitions.get(s['group'], {}).get('stateType') == 'Stun'}
    adds = [s for s in states if s.get('event') == 'add' and s.get('casterObjectId') == player
            and s.get('targetObjectId') in teams and teams[s['targetObjectId']] != teams[player]]
    removes = [s for s in states if s.get('event') == 'remove' and s.get('casterObjectId') == player]
    stun_apps = {(s['tick'], s['targetObjectId'], s['stateCode']) for s in adds if s.get('stateCode') in stun_codes}
    observed_state_types = Counter(definitions.get(state_by_code.get(s.get('stateCode'), {}).get('group'), {}).get('stateType', 'Unknown') for s in adds)
    diagnostics = {'exactEStunCodeCount': len(stun_codes),
                   'exactEStunApplicationCountAllMatch': len(stun_apps),
                   'casterEnemyStateTypeCountsAllMatch': dict(observed_state_types),
                   'physicalWallCoordinateReconstructed': False}
    if not stun_codes:
        row = _unavailable(spec, '정확한 E 전용 Stun 코드 없음; 일반 기절을 E 벽 기절로 귀속하지 않음')
        row['wallEvidence'] = diagnostics
        return row
    if not starts:
        row = _unavailable(spec, 'E 시전이 관측되지 않음')
        row['wallEvidence'] = diagnostics
        return row
    starts = sorted(starts, key=lambda s:s['tick'])
    contacts, linked_stuns, problems = [], set(), Counter()
    cast_ticks=[]
    for i, start in enumerate(starts):
        next_tick = starts[i+1]['tick'] if i+1 < len(starts) else float('inf')
        end_ticks = {f['tick'] for f in finishes if f.get('playerObjectId') == player
                     and f.get('skillIdCode') == start.get('skillIdCode')
                     and start['tick'] <= f['tick'] < next_tick}
        combat = any(l <= start['tick'] < r for l,r in intervals)
        if len(end_ticks) != 1:
            problems['incomplete-or-ambiguous-E-finish'] += 1
            continue
        finish = next(iter(end_ticks))
        target = start.get('targetObjectId')
        if not isinstance(target, int) or isinstance(target, bool) or target <= 0:
            problems['missing-exact-E-target'] += 1
            continue
        if target not in teams:
            # Animal casts do not contribute to the requested PvP denominator.
            # They are still recorded as exact casts outside the eligible target scope.
            if combat:
                contacts.append(set())
                cast_ticks.append(start['tick'])
            continue
        if teams[target] == teams[player]:
            problems['unexpected-ally-E-target'] += 1
            continue
        knockbacks = [s for s in adds if start['tick'] <= s['tick'] <= finish and s['targetObjectId'] == target
                      and definitions.get(state_by_code.get(s.get('stateCode'), {}).get('group'), {}).get('stateType') == 'Airborne']
        unique_knockbacks = {(s['tick'], s['targetObjectId'], s['stateCode']):s for s in knockbacks}
        if len(unique_knockbacks) > 1:
            problems['ambiguous-knockback-state-at-E-finish'] += 1
            continue
        hits = set()
        for knockback in unique_knockbacks.values():
            knockback_tick = knockback['tick']
            knockback_group = state_by_code[knockback['stateCode']]['group']
            end = min((s['tick'] for s in removes if s.get('stateGroup') == knockback_group
                       and s['targetObjectId'] == target and knockback_tick <= s['tick'] < next_tick), default=None)
            if end is None or any(knockback_tick < s['tick'] <= end and s['targetObjectId'] == target
                                  and state_by_code.get(s.get('stateCode'), {}).get('group') == knockback_group
                                  for s in adds):
                problems['incomplete-or-overlapping-knockback-lifetime'] += 1
                continue
            for application in stun_apps:
                tick, victim, _ = application
                if victim == target and knockback_tick <= tick <= end:
                    if application in linked_stuns:
                        problems['stun-linked-to-multiple-casts'] += 1
                    linked_stuns.add(application)
                    hits.add((tick, victim))
        if combat:
            contacts.append(hits)
            cast_ticks.append(start['tick'])
    diagnostics['linkedEStunApplicationCountAllMatch'] = len(linked_stuns)
    diagnostics['unlinkedEStunApplicationCountAllMatch'] = len(stun_apps-linked_stuns)
    diagnostics['castLinkProblems'] = dict(problems)
    if problems or stun_apps-linked_stuns:
        row = _unavailable(spec, 'E 전용 기절은 관측됐지만 모든 시전·밀치기 수명이 배타적으로 연결되지 않음')
    else:
        row = _result(spec, contacts, 'exact-E-target-cast-lifecycle-knockback-and-E-stun-state',cast_ticks=cast_ticks)
    row['wallEvidence'] = diagnostics
    return row
