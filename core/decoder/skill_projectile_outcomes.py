"""Explosion target lists verified against their exact runtime lifecycle.

Collision-disabled physical flight does not imply that explosion target lists
are absent. An explosion route is accepted only when actual target packets,
same-tick enemy damage, explosion and destruction all corroborate the outcome.
"""
from collections import defaultdict
try:
    from .skill_attempt_timing import exact_outcome
    from .requested_skill_hit_rates import _matches,_result,_unavailable
except ImportError:
    from skill_attempt_timing import exact_outcome
    from requested_skill_hit_rates import _matches,_result,_unavailable


def _magnus_w_damage_receipts(spec, starts, all_starts, finishes, damages, catalog, player, candidates, gaps):
    """Reviewed native Q effect0 versus W1004006; only exact recorded W uses."""
    if (spec.get('skillGroup') != 1004200 or candidates != {4}
            or catalog.get('sourceGameDbSha256') != '5ef9cb5459a2e908099655bf9ffc70ac16b134d1db282fb97b25fe065e7c8e4f'
            or not starts or any(s.get('skillIdCode') != 80 for s in starts)
            or gaps is None or any(g.get('count', 0) for g in gaps)):
        return {}
    from .skill_partial_cast_lifetimes import ordered_cast_records
    from .skill_wire_order import command_order
    from .skill_development_effect_metrics import development_policy
    if not development_policy().get('enabled'): return {}
    w = [s for s in all_starts if s.get('playerObjectId') == player and s.get('skillIdCode') == 81]
    if not w or any(s.get('skillGroup') != 1004300 for s in w): return {}
    records, why = ordered_cast_records(w, finishes, player, allow_same_tick_finishes=True)
    if why: return {}
    receipts = {}
    for d in damages:
        if (d.get('attackerObjectId') != player or d.get('effectCode') != 1004006
                or d.get('isCritical') is not False or d.get('damageType') != 2): continue
        order = command_order(d)
        if order is None: continue
        parents = [r for r in records if r['finish'] is not None
                   and type(r['finish'].get('reason')) is int and r['finish']['reason'] in set(range(15)) | {16,17}
                   and command_order(r['start']) < order < command_order(r['finish'])
                   and r['start']['tick'] <= d['tick'] <= r['finish']['tick']]
        if len(parents) != 1: continue
        r = parents[0]
        receipts[id(d)] = dict(tick=d['tick'], order=order, targetObjectId=d['targetObjectId'],
            attackerObjectId=player, effectCode=1004006, isCritical=False, damageType=2,
            wStartTick=r['start']['tick'], wStartOrder=command_order(r['start']),
            wFinishTick=r['finish']['tick'], wFinishOrder=command_order(r['finish']),
            wFinishReason=r['finish']['reason'], proof='work/magnus-q-unrelated-damage-review.json',
            damageForwardingProof='work/magnus-damage-effect-forwarding-proof.json')
    return receipts


def explosion_contact_metric(spec,legacy,starts,all_starts,spawns,collisions,terminals,
                             damages,teams,player,catalog,intervals,window,projectile_owners,finishes,*,gaps=None,game_terminals=None):
    # Initial promotion scope: single emitted object with a single explosion.
    # Bike/body alternatives and multi-wave skills need their own denominator.
    if spec['unit']!='skill-cast':
        return _unavailable(spec,'이 스킬의 폭발 시전 단위는 아직 검증되지 않음')
    evidence=legacy.get('projectileCodeCandidateEvidence',{})
    runtime_codes=evidence.get('runtimeExclusiveCandidates') or []
    candidates=set(runtime_codes or evidence.get('staticCandidates') or [])
    relevant=[s for s in spawns if s['projectileCode'] in candidates]
    if not starts or not relevant:
        return _unavailable(spec,'시전 또는 연결할 실제 폭발 발사체가 관측되지 않음')
    linked=defaultdict(list)
    for spawn in relevant:
        compatible=[]
        for start in all_starts:
            definition=catalog['skillGroups'][str(start['skillGroup'])]
            if not runtime_codes and spawn['projectileCode'] not in definition.get('projectileCodeCandidates',[]): continue
            if _matches(spawn,start,catalog['skillGroups'],window): compatible.append(start)
        if len(compatible)!=1 or compatible[0]['skillGroup']!=spec['skillGroup']:
            return _unavailable(spec,'폭발 발사체가 한 시전에 배타적으로 연결되지 않음')
        linked[id(compatible[0])].append(spawn)
    try:
        from .requested_skill_scope import exact_cast_lifetimes
    except ImportError:
        from requested_skill_scope import exact_cast_lifetimes
    lifetimes,reason=exact_cast_lifetimes(starts,finishes,player)
    if reason: return _unavailable(spec,reason)
    cancelled=set()
    for start,end in lifetimes:
        objects=linked[id(start)]
        if len(objects)==1: continue
        finish=next(f for f in finishes if f.get('playerObjectId')==player and
                    f.get('skillIdCode')==start['skillIdCode'] and f['tick']==end)
        # 15 is CancelByReplay: a reconstruction/end marker, not gameplay failure.
        if not objects and finish.get('reason') in set(range(1,15))|{16,17}:
            cancelled.add(id(start))
        else:
            return _unavailable(spec,'발사체가 없는 시전의 실제 취소 사유 또는 한 발 연결이 미확정')
    excluded = _magnus_w_damage_receipts(spec, starts, all_starts, finishes, damages, catalog, player, candidates, gaps)
    blocker_damage=defaultdict(set)
    applied_exclusions=[]
    own_damage=defaultdict(set)
    for d in damages:
        owner=d['attackerObjectId'] if d['attackerObjectId'] in teams else projectile_owners.get(d['attackerObjectId'])
        target=d['targetObjectId']
        if owner==player and target in teams and teams[target]!=teams[player]:
            own_damage[d['tick']].add(target)
            if id(d) not in excluded: blocker_damage[d['tick']].add(target)
    own_collisions=defaultdict(set)
    collisions_by_id=defaultdict(set)
    for c in collisions:
        target=c['targetObjectId']
        if target not in teams or teams[target]==teams[player]: continue
        if projectile_owners.get(c['projectileObjectId'])==player:
            own_collisions[c['tick']].add(target)
        collisions_by_id[c['projectileObjectId']].add((c['tick'],target))
    terminal_by_id=defaultdict(list)
    for event in terminals: terminal_by_id[event['objectId']].append(event)
    from .skill_projectile_active_end import projectile_active_end_records, recorded_no_explosion_end
    active_ends=projectile_active_end_records(terminals)
    contacts=[]
    timings=[]
    unknown={};incomplete_positive=set()
    combat=[i for i,s in enumerate(starts) if any(l<=s["tick"]<r for l,r in intervals)]
    observed_contacts=0
    diagnostics={'explosionTargetListChecked':True,'completeExplosionObjectCount':0,
                 'sameTickDamageCorroboratedEnemyContactCount':0,'unmatchedEnemyDamageTargetCount':0}
    for i,start in enumerate(starts):
        if id(start) in cancelled:
            if any(l<=start['tick']<r for l,r in intervals):
                contacts.append(set());timings.append(exact_outcome(start['tick'],start['tick'],set()))
            continue
        spawn=linked[id(start)][0]
        definition=catalog['projectileDefinitions'].get(str(spawn['projectileCode']),{})
        # Arrival-to-explosion delay is allowed: the actual explosion tick and
        # final destruction, not a static timer, delimit this one-shot outcome.
        if not definition.get('isExplosion') or definition.get('collisionAfterArrival'):
            return _unavailable(spec,'단발 폭발 이후 지속 판정이 있어 별도 수명 검증 필요')
        events=terminal_by_id[spawn['projectileObjectId']]
        blocked_end=recorded_no_explosion_end(spawn,events,collisions,gaps)
        if blocked_end:
            diagnostics['recordedExternalNoExplosionCount']=diagnostics.get('recordedExternalNoExplosionCount',0)+1
            if i in combat:
                contacts.append(set());timings.append(exact_outcome(start['tick'],spawn['tick'],set()))
            continue
        explosions={t['tick'] for t in events if t['event']=='CmdProjectileExplosion'}
        destroys={t['tick'] for t in events if t['event']=='CmdDestroy'}
        if len(explosions)!=1 or len(destroys)>1 or not spawn['tick']<=min(explosions) or (destroys and min(explosions)>min(destroys)):
            unknown[i]='explosion-or-destruction-lifetime-incomplete';continue
        tick=next(iter(explosions))
        active_end=active_ends.get(spawn['projectileObjectId'])
        if active_end and (not active_end.get('complete') or active_end['endTick']<tick):
            unknown[i]='explosion-or-destruction-lifetime-incomplete';continue
        hits=collisions_by_id[spawn['projectileObjectId']]
        if any(t!=tick for t,_ in hits):
            return _unavailable(spec,'직접 충돌과 폭발 타격 시점이 달라 별도 단계 연결 필요')
        if any(target not in own_damage[tick] for _,target in hits):
            return _unavailable(spec,'폭발 대상 패킷 일부에 같은 시전자·대상의 실제 피해 근거 없음')
        unmatched=blocker_damage[tick]-own_collisions[tick]
        if own_damage[tick]-own_collisions[tick] != unmatched:
            applied_exclusions.extend(v for v in excluded.values() if v['tick']==tick and v['targetObjectId'] not in own_collisions[tick])
        if unmatched:
            diagnostics['unmatchedEnemyDamageTargetCount']+=len(unmatched)
            # Same-owner damage at this tick may belong to another producer.
            # Do not invent its parent, or discard an independently recorded
            # collision+damage hit. An empty target list cannot prove a miss.
            if not hits:
                unknown[i]='explosion-damage-target-parent-unresolved'
                continue
            incomplete_positive.add(i)
        # A recorded single explosion followed by removal is complete even
        # when the later visual destruction is outside the replay stream.
        if not destroys and not (active_end and active_end.get('complete')):
            if hits:incomplete_positive.add(i)
            else:
                unknown[i]='projectile-retirement-missing-without-positive';continue
        observed_contacts+=len(hits)
        diagnostics['completeExplosionObjectCount']+=1
        diagnostics['sameTickDamageCorroboratedEnemyContactCount']+=len(hits)
        if any(l<=start['tick']<r for l,r in intervals):
            contacts.append(hits);timings.append(exact_outcome(start['tick'],spawn['tick'],hits))
    from .skill_lifecycle_result_policy import finalize_lifecycle_result,reviewed_rule_reuse_policy
    reuse=reviewed_rule_reuse_policy(gaps,{'CmdSpawn','CmdProjectileCollision','CmdProjectileExplosion','CmdDestroy','CmdDestroyDelayStart','CmdProjectileDestroyedByExternalObject','CmdDamage'},spawns,collisions,terminals,damages)
    if not observed_contacts and reuse is None:
        return _unavailable(spec,'폭발 대상 스트림 완전성 확인 필요')
    row=_result(spec,contacts,'exact-explosion-target-list-damage-and-destruction')
    row['outcomes']=timings
    row['explosionEvidence']=diagnostics
    row['unresolvedCastTicks']=[starts[i]['tick'] for i in combat if i in unknown]
    if row['unresolvedCastTicks']:
        row.update(hitRateScope='verified-uses-only',wholeCombatHitRate=None,fullRequestedMetricComplete=False)
    row['cancelledBeforeAttackCount']=sum(id(s) in cancelled and any(l<=s['tick']<r for l,r in intervals) for s in starts)
    row=finalize_lifecycle_result(row,combat,unknown,incomplete_positive=incomplete_positive,observed_positive=bool(observed_contacts))
    if applied_exclusions:
        from .skill_development_cancellation import annotate_provisional
        row=annotate_provisional(row,'Reviewed Magnus Q producer excludes independently attributed noncritical W damage from unmatched damage only.')
        row.update(magnusUnrelatedWDamageEvidence=applied_exclusions, originalDamageCorroborationPreserved=True)
    return row
