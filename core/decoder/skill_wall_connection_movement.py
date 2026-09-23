"""Laura E1 wall connection and actual arrival, with no damage proxy."""
from collections import Counter
import math
try:
    from .requested_skill_hit_rates import _unavailable
    from .skill_partial_cast_lifetimes import ordered_cast_records
    from .skill_wire_order import command_order
    from .skill_action_stage_evidence import load_exact_skill_ids
except ImportError:
    from requested_skill_hit_rates import _unavailable
    from skill_partial_cast_lifetimes import ordered_cast_records
    from skill_wire_order import command_order
    from skill_action_stage_evidence import load_exact_skill_ids


def wall_connection_movement_metric(spec,starts,finishes,spawns,terminals,actions,movement,
                                    player,intervals,catalog,skill_rows,gaps,skill_ids=None):
    selected=[s for s in starts if s['playerObjectId']==player and s['skillGroup']==1047400]
    combat=lambda s:any(l<=s['tick']<r for l,r in intervals)
    diag=dict(observedCastCount=len(selected),observedCombatCastCount=sum(map(combat,selected)))
    def fail(reason):return {**_unavailable(spec,reason),**diag}
    if (spec['characterCode'],spec['skillGroup'],spec['mode'],spec['unit'])!=(47,1047400,'movement-success','skill-cast'):
        return fail('unsupported wall movement identity')
    ids=load_exact_skill_ids() if skill_ids is None else skill_ids
    wire=ids.get('LauraActive3_1');codes={r['code'] for r in skill_rows if r.get('group')==1047400}
    if (wire!=674 or not codes or catalog['skillGroups'].get('1047400',{}).get('skillId')!='LauraActive3_1'
            or not catalog.get('projectileDefinitions',{}).get('104741')
            or movement is None or actions is None or gaps is None):return fail('missing exact wall movement schema or stream')
    required={'CmdStartSkill','CmdFinishSkill','CmdPlaySkillAction','CmdPlaySkillActionWithTargets',
              'CmdSpawn','CmdDestroy','CmdMoveStraight','CmdStopMove'}
    if any(g.get('count',0) and g.get('packetName') in required for g in gaps):return fail('wall movement command decode gap')
    if any(s.get('skillIdCode')!=wire or s.get('skillCode') not in codes for s in selected):return fail('wire skill identity mismatch')
    records,reason=ordered_cast_records(selected,finishes,player,allow_same_tick_finishes=True)
    if reason:return fail(reason)
    own=[a for a in actions if a.get('sourceObjectId')==player and a.get('skillIdCode')==wire]
    if any(command_order(a) is None for a in own):return fail('missing exact wall action order')
    if len({command_order(a) for a in own})!=len(own):return fail('duplicate wall action identity')
    attached=[[] for _ in records]
    for a in own:
        owners=[i for i,r in enumerate(records) if command_order(r['start'])<=command_order(a) and
                (r['finish'] is None or command_order(a)<=command_order(r['finish']))]
        if len(owners)!=1:return fail('wall action outside a unique recorded cast')
        attached[owners[0]].append(a)
    def target(a):
        ts=a.get('targets',[])
        return ts[0].get('targetObjectId') if len(ts)==1 else None
    def vector(v):return isinstance(v,list) and len(v)==2 and all(type(x) in (int,float) and math.isfinite(x) for x in v)
    outcomes=[];proofs=[];unknown=Counter()
    for i,r in enumerate(records):
        if not combat(r['start']):continue
        s,end=r['start'],r['finish'];aa=attached[i]
        def reject(why):unknown.update([why])
        # E1 remains open after arriving; E2 or another skill can then cancel
        # that script. The measured subphase is the recorded wall movement,
        # so a later cancellation must not erase an already completed arrival.
        if not r['complete']:reject('cancelled-or-open-cast');continue
        phases={n:[a for a in aa if a['actionNo']==n] for n in (1,2,3,4,5)}
        if len(phases[1])!=1:reject('missing-or-multiple-launch-actions');continue
        launch=phases[1][0];oid=target(launch)
        pp=[p for p in spawns if p.get('projectileObjectId')==oid and p.get('projectileCode')==104741
            and p.get('ownerObjectId')==player and p.get('ownerPlayerObjectId')==player
            and command_order(p) is not None and command_order(s)<=command_order(p)<=command_order(launch)]
        ends={t['tick'] for t in terminals if t.get('objectId')==oid and t.get('event')=='CmdDestroy'}
        if len(pp)!=1:reject('missing-owned-projectile');continue
        if any(t<pp[0]['tick'] for t in ends):reject('projectile-terminal-before-spawn');continue
        # Arrival and the projectile-dead callback each close the measured
        # subphase. A later projectile Destroy is not needed to prove it.
        if len(phases[2])==1 and not any(phases[n] for n in (3,4,5)):
            if not command_order(launch)<command_order(phases[2][0]):reject('failure-action-before-launch');continue
            outcomes.append([s['tick'],0,s['tick'],None]);proofs.append(dict(projectileObjectId=oid,status='projectile-ended-without-wall'));continue
        if phases[2] or any(len(phases[n])!=1 for n in (3,4,5)):reject('incomplete-wall-movement-phases');continue
        wall,anchor,done=(phases[n][0] for n in (4,5,3))
        if not command_order(launch)<command_order(wall)<command_order(anchor)<command_order(done):reject('wall-phase-order-mismatch');continue
        if type(target(anchor)) is not int or target(anchor)<=0 or target(anchor)!=target(done):reject('anchor-identity-mismatch');continue
        moves=[m for m in movement if m.get('objectId')==player and m.get('event')=='CmdMoveStraight'
               and m['tick']==anchor['tick']]
        if len(moves)!=1 or not vector(moves[0].get('destinationVector2')):reject('missing-unique-actual-wall-movement');continue
        move=moves[0]
        stops=[m for m in movement if m.get('objectId')==player and m.get('event')=='CmdStopMove'
               and move['tick']<=m['tick']<=done['tick']]
        if len(stops)!=1 or stops[0].get('positionFieldType')!='Vector2' or stops[0].get('positionVector2')!=move['destinationVector2']:
            reject('actual-arrival-position-not-confirmed');continue
        outcomes.append([s['tick'],1,s['tick'],stops[0]['tick']])
        proofs.append(dict(projectileObjectId=oid,anchorObjectId=target(anchor),status='arrived-at-recorded-destination',
                           moveTick=move['tick'],arrivalTick=stops[0]['tick'],completionActionTick=done['tick']))
    count=len(outcomes);hits=sum(o[1] for o in outcomes)
    diag.update(unresolvedCombatCastCount=sum(unknown.values()),unresolvedCastReasons=dict(unknown),
                perUseCompletenessTracked=True,incompleteUsesCountedAsMisses=False)
    if diag['observedCombatCastCount'] and not count:return fail('no complete wall movement outcomes')
    return dict(spec,**diag,status='calculable-observed' if count else 'no-combat-sample',method='static-wall-actions-exact-movement-arrival',
                attemptCount=count,hitCount=hits,hitRate=round(hits/count,6) if count else None,outcomes=outcomes,
                outcomeScope='wall connection movement success; not enemy hit rate',movementEvidenceByAttempt=proofs,
                reason=None,fallbackUsed=False,multiTargetAttemptCount=None,multiTargetAttemptRate=None,
                fixedDurationWindowUsed=False,evidenceReview='deliverables/laura-e1-wall-movement-static-proof-v1.json')
