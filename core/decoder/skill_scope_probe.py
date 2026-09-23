"""Anonymous diagnostic counts; timing candidates here are NOT hit-rate proof."""
from collections import Counter, defaultdict


def summarize_scope_evidence(players, teams, starts, finishes, actions, damages,
                             states, skill_rows, state_rows, projectile_owners, intervals):
    skills = defaultdict(set)
    state_groups = defaultdict(set)
    for row in skill_rows:
        skills[row['code']].add(row['group'])
    for row in state_rows:
        state_groups[row['code']].add(row['group'])
    damage_by_owner = defaultdict(list)
    for row in damages:
        owner = row['attackerObjectId']
        if owner not in players:
            owner = projectile_owners.get(owner)
        target = row['targetObjectId']
        if owner in players and target in players and teams[owner] != teams[target]:
            damage_by_owner[owner].append(row)
    result = []
    for player, info in players.items():
        player_starts = [s for s in starts if s['playerObjectId'] == player]
        for group in sorted({s['skillGroup'] for s in player_starts}):
            group_starts = sorted([s for s in player_starts if s['skillGroup'] == group], key=lambda s:s['tick'])
            identities = {s['skillIdCode'] for s in group_starts}
            group_finishes = [f for f in finishes if f['playerObjectId'] == player and f['skillIdCode'] in identities]
            group_actions = [a for a in actions if a['playerObjectId'] == player and a['skillIdCode'] in identities]
            lifetimes = []
            incomplete = 0
            for i, start in enumerate(group_starts):
                next_tick = group_starts[i+1]['tick'] if i+1 < len(group_starts) else float('inf')
                end = [f['tick'] for f in group_finishes if start['tick'] <= f['tick'] < next_tick and f['skillIdCode'] == start['skillIdCode']]
                if len(end) == 1:
                    lifetimes.append((start['tick'], end[0]))
                else:
                    incomplete += 1
            effects = Counter()
            outside = Counter()
            delays = Counter()
            for d in damage_by_owner[player]:
                code = d.get('effectCode')
                if skills.get(code) != {group}:
                    continue
                effects[str(code)] += 1
                candidates = [(l,r) for l,r in lifetimes if l <= d['tick'] <= r]
                if len(candidates) != 1:
                    outside[str(code)] += 1
                else:
                    delays[str(d['tick']-candidates[0][0])] += 1
            target_shapes = Counter()
            near_effects = Counter()
            damage_during_lifetime = Counter()
            field_shapes = Counter()
            for d in damage_by_owner[player]:
                if any(l <= d['tick'] <= r for l,r in lifetimes):
                    damage_during_lifetime[str(d.get('effectCode'))] += 1
            for a in group_actions:
                field_shapes['/'.join(a.get('decodedFieldNames', []))] += 1
                target_shapes[f"{a['wireStatus']}:{a.get('actionNo')}:{len(a.get('targets', []))}"] += 1
                for d in damage_by_owner[player]:
                    if d['tick'] == a['tick']:
                        near_effects[str(d.get('effectCode'))] += 1
            enemy_states = Counter()
            for state in states:
                if state['event'] != 'add' or state['casterObjectId'] != player or state['targetObjectId'] not in players or teams[state['targetObjectId']] == teams[player]:
                    continue
                if any(l <= state['tick'] <= r for l,r in lifetimes):
                    enemy_states[str(state.get('stateCode'))] += 1
            result.append({
                'characterCode': info['characterCode'], 'skillGroup': group,
                'castCount': len(group_starts), 'finishCount': len(group_finishes),
                'completeCastCount': len(lifetimes), 'incompleteCastCount': incomplete,
                'combatCastCount': sum(any(l<=s['tick']<r for l,r in intervals[player]) for s in group_starts),
                'actionShapes': dict(sorted(target_shapes.items())),
                'exactSkillCodeEnemyDamageCounts': dict(sorted(effects.items())),
                'exactSkillCodeDamageOutsideSingleLifetimeCounts': dict(sorted(outside.items())),
                'damageDelayTicksHistogram': dict(sorted(delays.items())),
                'sameActionTickEffectCodeCandidates': dict(sorted(near_effects.items())),
                'effectCodeDuringLifetimeCandidates': dict(sorted(damage_during_lifetime.items())),
                'actionPacketFieldShapes': dict(sorted(field_shapes.items())),
                'enemyStateCodeDuringLifetimeCandidates': dict(sorted(enemy_states.items())),
            })
    return {'format':'er-skill-scope-evidence-probe.v1', 'rows':result,
            'interpretation':'diagnostic counts; temporal candidates are not verified attribution',
            'rawReplayRetained':False,'fallbackUsed':False}
