"""Base E regions from owned explosion lifetimes and native pivot predicates."""
from collections import Counter
import math
try:
    from .requested_skill_hit_rates import _result, _unavailable
    from .skill_partial_cast_lifetimes import ordered_cast_records
    from .skill_wire_order import command_order as order
    from .skill_pose_events import StationaryPoseIndex, f32
    from .skill_evaluation_position_bounds import evaluation_bounds
except ImportError:
    from requested_skill_hit_rates import _result, _unavailable
    from skill_partial_cast_lifetimes import ordered_cast_records
    from skill_wire_order import command_order as order
    from skill_pose_events import StationaryPoseIndex, f32
    from skill_evaluation_position_bounds import evaluation_bounds


def bounded_region(center, bounds, radius):
    """Interval image of the native SUBSS/MULSS/ADDSS comparison.

    A bounded pivot never becomes a fabricated point. Return no answer when
    both native branches are possible. Contact itself must already be proved.
    """
    if len(center)!=2 or any(type(v) not in (int,float) or not math.isfinite(v) for v in center):
        raise ValueError('invalid explosion pivot')
    squared=[]
    for c,key in zip(center,('x','z')):
        lo,hi=bounds[key]
        if not all(type(v) in (int,float) and math.isfinite(v) for v in (lo,hi)) or lo>hi:
            raise ValueError('invalid target pivot bounds')
        a,b=f32(lo-f32(c)),f32(hi-f32(c))
        endpoints=(f32(a*a),f32(b*b))
        squared.append((0.0 if a<=0<=b else min(endpoints),max(endpoints)))
    low=f32(squared[0][0]+squared[1][0]);high=f32(squared[0][1]+squared[1][1])
    limit=f32(f32(radius)*f32(radius))
    return ('inner' if high<=limit else 'outer' if low>limit else None),[low,high],limit


def barbara_region_execution(spec,player,teams,intervals,catalog,skill_rows,inputs):
    def fail(reason):return _unavailable(spec,reason)
    if spec.get('skillGroup')!=1026400 or spec.get('mode') not in {'inner-hit','outer-hit'}:
        return fail('unsupported Barbara region metric')
    if not inputs:return fail('retained Barbara region inputs unavailable')
    actor=inputs['rawPlayers'].get(str(player))
    if actor is None:return fail('exact Barbara player identity unavailable')
    if set(map(int,inputs['rawPlayers']))!=set(teams):
        return fail('complete player team identity mapping required')
    if catalog['skillGroups'].get('1026400',{}).get('skillId')!='BarbaraActive3_MagneticForce':
        return fail('pinned Barbara E identity mismatch')
    definitions={r['code']:r for r in skill_rows if r.get('group') in {1026400,1026420}}
    selected=[s for s in inputs['starts'] if s['playerObjectId']==actor and s['skillIdCode']==355]
    parents,why=ordered_cast_records(selected,inputs['finishes'],actor,allow_same_tick_finishes=True)
    if why:return fail(why)
    raw_teams={raw:teams[int(local)] for local,raw in inputs['rawPlayers'].items() if int(local) in teams}
    poses=StationaryPoseIndex(inputs['poses']);uses=[]
    for parent in parents:
        start=parent['start'];finish=parent['finish'];unknown=set();details=[]
        if not parent['complete'] or not finish or finish.get('reason')!=0:unknown.add('parent-not-normally-complete')
        projectiles=[s for s in inputs['projectiles'] if s['ownerId']==actor and s['code']==102641
            and order(start)<order(s) and (finish is None or order(s)<order(finish))]
        child=None;end=None
        if len(projectiles)!=1:unknown.add('parent-projectile-not-unique')
        else:
            projectile=projectiles[0]
            arrivals=[e for e in inputs['arrivals'] if e['objectId']==projectile['objectId'] and order(e)>order(projectile)]
            if len(arrivals)!=1:unknown.add('projectile-arrival-not-unique')
            else:
                arrival=arrivals[0]
                summons=[s for s in inputs['summons'] if s['ownerId']==actor and s['code']==1102
                    and s['tick']==arrival['tick'] and order(s)>order(arrival)]
                competing=[e for e in inputs['arrivals'] if e['tick']==arrival['tick'] and any(
                    p['objectId']==e['objectId'] and p['ownerId']==actor for p in inputs['projectiles'])]
                if len(summons)!=1 or len(competing)!=1:unknown.add('arrival-explosion-not-unique')
                else:
                    child=summons[0]
                    ss=[s for s in inputs['starts'] if s['playerObjectId']==child['objectId'] and s['skillIdCode']==356]
                    fs=[f for f in inputs['finishes'] if f['playerObjectId']==child['objectId'] and f['skillIdCode']==356]
                    if len(ss)!=1 or len(fs)!=1 or fs[0].get('reason')!=0 or ss[0]['tick']!=arrival['tick'] or fs[0]['tick']!=arrival['tick'] or not order(child)<order(ss[0])<order(fs[0]):
                        unknown.add('explosion-lifetime-not-closed')
                    elif ss[0]['skillCode'] not in definitions or definitions[ss[0]['skillCode']].get('group')!=1026420:
                        unknown.add('explosion-skill-definition-mismatch')
                    else:
                        begin,end=ss[0],fs[0]
                        radius=definitions[begin['skillCode']].get('innerRange')
                        if type(radius) not in (int,float) or radius!=1.25:unknown.add('inner-radius-definition-mismatch')
                        moving_center=any(order(child)<order(e)<order(end) for e in poses.rows.get((child['objectId'],'position'),[]))
                        for d in inputs['damages']:
                            if d['attackerId']!=actor or d['effectCode']!=1026401 or not order(begin)<order(d)<order(end):continue
                            target=d['objectId']
                            if target not in raw_teams or raw_teams[target]==raw_teams.get(actor):continue
                            witnesses=[s for s in inputs['states'] if s['objectId']==target and s['casterId']==actor
                                and s['code'] in range(1026401,1026406) and order(d)<order(s)<order(end)]
                            detail=dict(damageWireOrder=d['wireOrder'],targetObjectId=target,tick=d['tick'],region=None)
                            region=None;geometry_reason=None
                            if not moving_center and radius==1.25:
                                try:
                                    bounds,witness=evaluation_bounds(inputs,poses,target,d,end,current_position_only=True)
                                    region,d2,r2=bounded_region(child['positionXZ'],bounds,radius)
                                    detail.update(positionEvidence=witness,targetBounds=bounds,squaredDistanceBounds=d2,squaredInnerRange=r2)
                                except (ValueError,TypeError,KeyError,OverflowError) as error:geometry_reason=str(error)
                            else:geometry_reason='explosion-pivot-not-static'
                            if len(witnesses)==1:
                                if region=='outer':unknown.add('central-state-geometry-conflict')
                                else:region='inner';detail['stateWireOrder']=witnesses[0]['wireOrder']
                            if region is None:detail['reason']=geometry_reason or 'region-bound-crosses-inner-edge'
                            detail['region']=region;details.append(detail)
        desired=spec['mode'].split('-')[0]
        # Retain a known binary hit separately when another contact's region
        # is unknown. Public target-count statistics require complete details.
        contacts={(d['tick'],d['targetObjectId']) for d in details if d['region']==desired}
        unknown_details=[d for d in details if d['region'] is None]
        outcome_known=not unknown and (bool(contacts) or not unknown_details)
        if unknown_details:unknown.add('region-contact-unresolved')
        uses.append(dict(start=start,child=child,finish=end,contacts=contacts,details=details,unknown=sorted(unknown),
            targetDetailsComplete=not unknown_details,regionOutcomeKnown=outcome_known,
            knownRegionHit=bool(contacts) if outcome_known else None))
    combat=[u for u in uses if any(a<=u['start']['tick']<b for a,b in intervals)]
    valid=[u for u in combat if not u['unknown']]
    row=_result(spec,[u['contacts'] for u in valid],'static-Barbara-owned-explosion-native-region',cast_ticks=[u['start']['tick'] for u in valid]) if valid or not combat else fail('no classified Barbara region uses')
    row.update(observedCombatCastCount=len(combat),verifiedCombatCastCount=len(valid),unresolvedCombatCastCount=len(combat)-len(valid),
        unresolvedCastReasons=dict(Counter(r for u in combat for r in u['unknown'])),
        perUseCompletenessTracked=True,incompleteUsesCountedAsMisses=False,missingStunUsedAsOuter=False,
        knownBinaryRegionOutcomeCount=sum(u['regionOutcomeKnown'] for u in combat),
        knownBinaryRegionHitCount=sum(u['knownRegionHit'] is True for u in combat),
        executionEvidence=[{**u,'contacts':sorted(u['contacts'])} for u in combat],
        targetCountsAreLowerBounds=any(not u['targetDetailsComplete'] for u in valid),
        evidenceReview='schema/barbara-base-region-runtime-v1.json')
    return row
