"""Explicitly provisional effect attribution, separate from reviewed rules.

Recorded projectile identity wins over cast timing. With no object link the
most recent same-family use is an estimate, never a recovered wire fact.
Cancellation without observed emission is provisionally excluded, not a miss.
"""
from bisect import bisect_right
from collections import Counter,defaultdict
from functools import lru_cache
from pathlib import Path
import json
from .skill_partial_cast_lifetimes import ordered_cast_records
from .skill_wire_order import command_order
from .skill_static_effect_families import candidate_effect_family,_player_damage_events
from .requested_skill_hit_rates import _result,_unavailable

@lru_cache(maxsize=1)
def development_effect_contracts():
    return json.loads((Path(__file__).resolve().parents[1]/'data/development-effect-contracts-v1.json').read_bytes())['contracts']

def development_cast_records(starts,finishes,player):
    """Keep each damaged record local; never invent an end at the next start."""
    ids={s['skillIdCode'] for s in starts};events=[(s,'start') for s in starts]
    events += [(f,'finish') for f in finishes if f['playerObjectId']==player and f['skillIdCode'] in ids]
    if any(command_order(e) is None for e,_ in events):return None,'Missing cast/finish command order'
    ordered=sorted(events,key=lambda p:command_order(p[0]));active={};records=[]
    for e,kind in ordered:
        k=e['skillIdCode']
        if kind=='start':
            if k in active:records.append(dict(start=active[k],finish=None,complete=False,reason='missing-finish-before-next-recorded-start'))
            active[k]=e
        elif k in active:
            start=active.pop(k)
            if e['tick']<start['tick']:return None,'Cast/finish clock conflict'
            records.append(dict(start=start,finish=e,complete=e.get('reason')!=15,
                reason='replay-end-is-not-gameplay-finish' if e.get('reason')==15 else None))
    records.extend(dict(start=s,finish=None,complete=False,reason='open-final-cast') for s in active.values())
    return sorted(records,key=lambda r:(r['start']['tick'],command_order(r['start']))),None


def provisional_family_lifecycle_metric(spec,starts,finishes,damages,spawns,terminals,
        player,teams,intervals,effect_rows,catalog,projectile_owners,states=None,collisions=None,summons=None,actions=None,gaps=None,game_terminals=None,all_projectile_spawns=None):
    adina=spec.get('characterCode')==52 and spec.get('skillGroup') in {1052300,1052310} and spec.get('mode')=='any'
    adina_rule=None
    if adina:
        from .skill_adina_w_explosion_effects import load_contract,explosion_ledger,assign_damage,CONTRACT_SHA256
        if any(v is None for v in (starts,finishes,damages,spawns,terminals,gaps)) or any(g.get('count',0) for g in gaps):
            return _unavailable(spec,'Adina explosion attribution requires complete original streams')
        try:adina_rule=load_contract(catalog)
        except (OSError,ValueError,KeyError,TypeError):return _unavailable(spec,'Adina explosion contract unavailable; no fallback')
    garnet=(spec.get('characterCode'),spec.get('skillGroup'),spec.get('mode'),spec.get('unit'))==(76,1076300,'fetter','skill-cast')
    if garnet and any(v is None for v in (starts,finishes,damages,spawns,terminals,states,collisions,summons,actions,projectile_owners,all_projectile_spawns)):
        return _unavailable(spec,'Garnet winner policy requires original complete event streams')
    groups,effects=candidate_effect_family(spec,catalog,effect_rows)
    contracts=development_effect_contracts()
    contract=contracts.get(f"{spec['skillGroup']}:{spec['mode']}",contracts.get(str(spec['skillGroup'])))
    if adina:groups,effects={1052300,1052310},set(adina_rule['projectileEffects'].values())
    state_codes=set(contract.get('stateCodes',[])) if contract else set()
    if contract:groups,effects=set(contract.get('groups',[spec['skillGroup']])),set(contract['effectCodes'])
    collision_success=bool(contract and contract.get('projectileCollisionIsSuccess'))
    collision_states=set(contract.get('collisionRequiresStateCodes',[])) if contract else set()
    if collision_states and states is None:return _unavailable(spec,'Projectile contact requires the recorded target state stream')
    action_success=bool(contract and contract.get('actionTargetDamageIsSuccess'))
    if adina:
        groups,effects={1052300,1052310},set(adina_rule['projectileEffects'].values())
        collision_success=action_success=False
    if not effects and not state_codes and not collision_success and not action_success:return _unavailable(spec,'No candidate damage mapping; development attribution cannot invent effect codes')
    if state_codes and states is None:return _unavailable(spec,'Oil contact requires the recorded state stream')
    if collision_success and collisions is None:return _unavailable(spec,'Projectile contact requires the recorded collision stream')
    if action_success and actions is None:return _unavailable(spec,'Targeted hit requires the recorded skill action stream')
    own=[s for s in starts if s['playerObjectId']==player and s['skillGroup'] in groups]
    records,reason=development_cast_records(own,finishes,player)
    if reason:return _unavailable(spec,reason)
    selected=[i for i,r in enumerate(records) if r['start']['skillGroup']==spec['skillGroup']]
    if not selected:return _unavailable(spec,'해당 단계 시전이 관측되지 않음')
    def key(e):return e['tick'],command_order(e) or ()
    positions=[key(r['start']) for r in records]
    def recent(e):
        i=bisect_right(positions,key(e))-1
        return i if i>=0 else None
    def inside(i,e):
        r=records[i]
        return key(r['start'])<=key(e) and (r['finish'] is None or key(e)<=key(r['finish']))
    from .skill_execution_plan import planned_family_candidates
    planned=planned_family_candidates(spec,'projectileCodes')
    if planned is None:
        from .skill_rule_candidates import build_projectile_candidates
        _,projectile_codes=build_projectile_candidates(spec,catalog)
    else:_,projectile_codes=planned
    if contract and 'projectileCodes' in contract:projectile_codes=set(contract['projectileCodes'])
    # Irem sight objects persist after its ball impact; they are not attacks.
    if spec['skillGroup']==1061200:projectile_codes=set(projectile_codes)&{106111,106112,106113,106114}
    contacts=[set() for _ in records];shots=defaultdict(set);parents={};object_inferred=set()
    for shot in spawns:
        if shot.get('ownerPlayerObjectId')!=player or shot['projectileCode'] not in projectile_codes:continue
        exact=[j for j,r in enumerate(records) if r['complete'] and inside(j,shot)]
        anchored=[j for j in exact if any(a['tick']==shot['tick'] for a in records[j]['start'].get('linkActions',[]))]
        i=exact[0] if len(exact)==1 else anchored[0] if len(anchored)==1 else recent(shot)
        if i is None:continue
        obj=shot['projectileObjectId'];parents[obj]=i;shots[i].add(obj)
        if not inside(i,shot):object_inferred.add(obj)
    if contract and contract.get('summonCodes'):
        if summons is None:return _unavailable(spec,'Owned-object profile requires recorded summons')
        ticks=[r['start']['tick'] for r in records]
        for obj in summons:
            if obj.get('ownerObjectId')!=player or obj.get('summonCode') not in contract['summonCodes']:continue
            if contract.get('summonRequiresCastActionTarget'):
                exact=[j for j,r in enumerate(records) if r['start']['tick']==obj['tick'] and any(
                    a.get('tick')==obj['tick'] and any(t.get('targetObjectId')==obj['objectId'] for t in a.get('targets',[]))
                    for a in r['start'].get('linkActions',[]))]
                if len(exact)!=1:continue
                i=exact[0]
            else:i=bisect_right(ticks,obj['tick'])-1
            if i<0:continue
            parents[obj['objectId']]=i;shots[i].add(obj['objectId'])
    end_by_tick=defaultdict(set);closed=set()
    terminal_names={'CmdProjectileExplosion','CmdProjectileArrived','CmdDestroyDelayStart','CmdDestroy','CmdProjectileCollisionWall'}
    for e in terminals:
        obj=e.get('objectId')
        if obj in parents and e.get('event') in terminal_names:
            closed.add(obj)
            if e['event'] in {'CmdProjectileExplosion','CmdProjectileArrived','CmdDestroyDelayStart'}:
                end_by_tick[e['tick']].add(parents[obj])
    evidence=Counter();unassigned=[];estimated=[]
    adina_ledger=explosion_ledger(records,spawns,terminals,player,adina_rule) if adina else []
    adina_unknown=set();adina_proofs=[];adina_blocked=[]
    if action_success:
        actual_damage={(d['tick'],d['targetObjectId']) for d in _player_damage_events(damages,player,teams,projectile_owners)}
        for action in actions:
            if action.get('sourceObjectId')!=player:continue
            owners=[i for i,r in enumerate(records) if r['start']['skillIdCode']==action.get('skillIdCode') and inside(i,action)]
            if len(owners)!=1:continue
            for target in {t.get('targetObjectId') for t in action.get('targets',[])}:
                if target in teams and teams[target]!=teams[player] and (action['tick'],target) in actual_damage:
                    contacts[owners[0]].add((action['tick'],target));evidence['recorded-skill-action-target-and-damage']+=1
    marker_parents=defaultdict(set)
    if contract and contract.get('markerStateCode'):
        for s in states:
            if s.get('event')=='add' and s.get('casterObjectId')==player and s.get('stateCode')==contract['markerStateCode'] and s.get('targetObjectId') in parents:
                marker_parents[s['tick']].add(parents[s['targetObjectId']])
    if collision_success:
        same_damage={(d['tick'],d['targetObjectId']) for d in _player_damage_events(damages,player,teams,projectile_owners)}
        same_state={(s['tick'],s.get('targetObjectId')) for s in (states or []) if s.get('event')=='add' and s.get('casterObjectId')==player and s.get('stateCode') in collision_states}
        for event in collisions:
            obj=event.get('projectileObjectId');target=event.get('targetObjectId')
            if obj in parents and target in teams and teams[target]!=teams[player]:
                if contract.get('collisionRequiresDamage') and (event['tick'],target) not in same_damage:continue
                if collision_states and (event['tick'],target) not in same_state:continue
                contacts[parents[obj]].add((event['tick'],target));evidence['owned-projectile-enemy-collision']+=1
    events=_player_damage_events(damages,player,teams,projectile_owners)
    if state_codes:
        state_damage={(d['tick'],d['targetObjectId']) for d in events if d.get('effectCode') in contract.get('stateRequiresDamageEffects',[])}
        events=[{**s,'attackerObjectId':s.get('casterObjectId'),'evidenceCode':s.get('stateCode')} for s in states
            if s.get('event')=='add' and s.get('stateCode') in state_codes
            and (s.get('casterObjectId') in parents if contract.get('stateCasterIsOwnedSummon') else s.get('casterObjectId')==player)
            and (not contract.get('stateRequiresDamageEffects') or (s['tick'],s.get('targetObjectId')) in state_damage)]
    for d in events:
        target=d['targetObjectId']
        if target not in teams or teams[target]==teams[player] or (not state_codes and d.get('effectCode') not in effects):continue
        code=d.get('effectCode',d.get('evidenceCode'))
        attacker=d['attackerObjectId']
        if adina:
            i,blocked,proof=assign_damage(d,adina_ledger,records)
            adina_unknown.update(blocked)
            if i is None:
                adina_blocked.append(proof);continue
            adina_proofs.append(proof);kind='configured-explosion-effect-unique-parent'
        elif contract and contract.get('markerStateCode'):
            if len(marker_parents[d['tick']])!=1:continue
            i=next(iter(marker_parents[d['tick']]));kind='recorded-owned-object-marker-and-enemy-state'
        elif attacker in parents:
            i=parents[attacker];kind='recorded-projectile-attacker'
        elif len(end_by_tick[d['tick']])==1:
            i=next(iter(end_by_tick[d['tick']]));kind='projectile-terminal-same-tick-candidate-effect'
        else:
            i=recent(d)
            if i is None:
                unassigned.append(dict(tick=d['tick'],effectCode=code,reason='effect-before-first-family-cast'));continue
            if contract and not contract['allowDelayedInference'] and not inside(i,d):continue
            kind='within-latest-family-cast' if inside(i,d) else 'estimated-latest-family-cast-after-finish'
        contacts[i].add((d['tick'],target));evidence[kind]+=1
        if kind=='estimated-latest-family-cast-after-finish':
            estimated.append(dict(castTick=records[i]['start']['tick'],effectTick=d['tick'],effectCode=code,targetObjectId=target))
    unknown={i:'adina-explosion-effect-parent-unresolved' for i in adina_unknown if not contacts[i]};excluded=[]
    for i in selected:
        r=records[i]
        if contacts[i] or i in unknown:continue
        if not r['complete']:
            if shots[i] and shots[i]<=closed:continue
            unknown[i]=r['reason'];continue
        if r['finish'].get('reason')!=0:
            if shots[i] and shots[i]<=closed:continue
            if not shots[i]:excluded.append(i)
            else:unknown[i]='emitted-cancelled-attack-has-no-observed-terminal'
    combat=[i for i in selected if any(lo<=records[i]['start']['tick']<hi for lo,hi in intervals)]
    user_misses=[];user_blocked=[]
    if garnet:
        from .skill_ordered_match_end import ordered_winner_match_end
        enabled=json.loads((Path(__file__).resolve().parents[1]/'data/user-hit-rate-scope-20260912.json').read_text(encoding='utf-8'))['recordedUseMissPolicy'].get('enabled') is True
        winner=ordered_winner_match_end(game_terminals,gaps) if enabled else None
        for i in combat:
            if unknown.get(i)!='open-final-cast' or contacts[i] or not enabled:continue
            start=records[i]['start'];left=command_order(start);right=command_order(winner) if winner else None
            reasons=[]
            if gaps is None or any(g.get('count',0) for g in gaps):reasons.append('stream-completeness-unavailable')
            if left is None or right is None or left>=right or start['tick']>winner['tick']:reasons.append('ordered-actual-winner-unavailable')
            def after(e):
                if e.get('tick') is not None and e['tick']<start['tick']:return False
                return command_order(e) is None or left is None or e.get('tick',start['tick'])>start['tick'] or command_order(e)>=left
            own_actions=[a for a in actions if a.get('sourceObjectId')==player and a.get('skillIdCode')==start['skillIdCode'] and after(a)]
            if start.get('skillIdCode')!=1105 or not own_actions or any(a.get('wireStatus')!='decoded-exact-CmdPlaySkillAction' or a.get('actionNo')!=2 or a.get('targets') or command_order(a) is None or right is None or command_order(a)>=right or a['tick']>winner['tick'] for a in own_actions):reasons.append('exact-preparation-action-not-proven')
            if any(after(d) for d in damages if d.get('attackerObjectId') in {None,player} or projectile_owners.get(d.get('attackerObjectId'))==player):reasons.append('later-owner-damage')
            if any(after(e) for e in states if e.get('casterObjectId') in {None,player}):reasons.append('later-state-ownership-unresolved')
            if any(after(e) for e in all_projectile_spawns if e.get('ownerObjectId') in {None,player} or projectile_owners.get(e.get('ownerObjectId'))==player):reasons.append('later-projectile-emission')
            if any(after(e) for e in summons if e.get('ownerObjectId') in {None,player}):reasons.append('later-owned-object')
            if shots[i]:reasons.append('candidate-projectile-recorded')
            proof=dict(startTick=start['tick'],startOrder=left,winnerEnd=winner,skillFinishInvented=False,
                actions=[{k:a[k] for k in ('tick','wireOrder','wireCategory','skillIdCode','actionNo','sourceObjectId','wireStatus','targets') if k in a} for a in own_actions])
            if reasons:user_blocked.append(dict(proof,reasons=reasons));continue
            unknown.pop(i);user_misses.append(proof)
    valid=[i for i in combat if i not in unknown and i not in excluded]
    row=_result(spec,[contacts[i] for i in valid],'provisional-family-effects-with-recorded-object-priority',cast_ticks=[records[i]['start']['tick'] for i in valid])
    if adina:row.update(adinaExplosionEffectEvidence=adina_proofs,adinaExplosionEffectBlockedEvidence=adina_blocked,
        adinaExplosionRuleSha256=CONTRACT_SHA256,
        nativeReplayBuildIdentityProven=False,unresolvedCastTicks=[records[i]['start']['tick'] for i in combat if i in unknown])
    if garnet:row.update(userPolicyMissEvidence=user_misses,userPolicyBlockedEvidence=user_blocked,
        userPolicyMissCastCount=len(user_misses),userPolicyMissCastTicks=[e['startTick'] for e in user_misses],
        userPolicyMissAuthority='explicit-user-rule')
    row.update(perUseCompletenessTracked=True,observedCombatCastCount=len(combat),
        verifiedCombatCastCount=0,unresolvedCombatCastCount=sum(i in unknown for i in combat),
        unresolvedCastReasons=dict(Counter(unknown[i] for i in combat if i in unknown)),
        candidateEffectCodes=sorted(effects),candidateProjectileCodes=sorted(projectile_codes),
        provisionalAttributionEvidence=dict(evidence),estimatedDelayedEffectEvents=estimated,
        unassignedPreCastEffectEvents=unassigned,estimatedObjectParentCount=len(object_inferred),
        provisionallyExcludedCancelledCastCount=sum(i in excluded for i in combat),
        provisionallyExcludedCancelledCastTicks=[records[i]['start']['tick'] for i in combat if i in excluded],
        cancelledExclusionAssumption='No candidate projectile or target damage observed: treat as pre-emission cancellation; direct-attack misses can be excluded in error.',
        delayedAttributionAssumption='Without an object link, attribute to latest preceding same-family cast. Repeated/shared effects can attach to a later use.',
        nearestCastUsed=bool(estimated),fixedTimeWindowUsed=False,verifiedCompletionCredit=False,
        incompleteUsesCountedAsMisses=False,negativeOutcomesAreProvisional=True,
        normalFinishProvesAllEffectsClosed=False,wholeCombatHitRate=None,
        interpretation='개발 중 잠정 적중률. 객체 연결 우선, 귀속이 없는 후속 피해는 같은 계열의 최근 시전으로 추정. 발사·피해가 없는 취소는 잠정 분모 제외.')
    if adina:
        row.update(delayedAttributionAssumption='Only a unique ordered explosion object with matching configured effect; no nearest-cast fallback.',
            interpretation='개발 중 폭발 객체·효과별 귀속. 충돌만으로 적중을 세지 않으며 공유 효과의 모호한 부모는 미확정으로 유지.',
            normalFinishProvesAllEffectsClosed=False)
    if not valid and any(i in unknown for i in combat):
        row.update(status='unresolved-evidence',reason='Replay ends before attack outcome and no completed classified uses',attemptCount=None,hitCount=None,hitRate=None)
    if contract:row.update(developmentEffectContract=contract,evidenceInputType='CmdAddState' if state_codes else 'CmdDamage',candidateStateCodes=sorted(state_codes))
    return row
