"""Provisional denominator policy; original route results remain auditable."""
from .skill_partial_cast_lifetimes import ordered_cast_records
from .skill_wire_order import command_order
from collections import Counter
import hashlib
import json

def annotate_provisional(row,assumption):
    if row['status'] not in {'calculable-observed','calculable-experimental'}:return row
    row.update(status='calculable-experimental',calculationConfidence='experimental',
        developmentLabel='개발 중 · 잠정 적중률',verifiedCompletionCredit=False,
        fullRequestedMetricComplete=False,verifiedCombatCastCount=0,
        provisionalCombatCastCount=row['attemptCount'],binaryCastSuccessComplete=False,
        recordedCastOutcomesComplete=False,hitRateScope='provisional-classified-uses',wholeCombatHitRate=None,
        developmentPolicy='data/development-hit-rate-policy-v1.json',developmentAssumption=assumption)
    return row


def exclude_winner_interrupted_unemitted_uses(row,starts,finishes,spawns,player,gaps,game_terminals,intervals):
    """Provisional denominator policy for an actual game end, not a fake finish."""
    from .skill_ordered_match_end import ordered_winner_match_end
    from .skill_development_effect_metrics import development_policy
    reasons=dict(row.get('unresolvedCastReasons') or {})
    count=reasons.get('open-final-cast',0)
    if not count or row.get('mode')!='any' or row.get('unit')!='skill-cast':return row
    total=row.get('unresolvedCombatCastCount')
    if type(total) is not int or total<count:return row
    if not development_policy().get('lifecycleEstimatesEnabled'):return row
    end=ordered_winner_match_end(game_terminals,gaps)
    if end is None or spawns is None or any(g.get('count',0) and g.get('packetName') in {'CmdStartSkill','CmdFinishSkill','CmdSpawn','CmdDamage'} for g in gaps):return row
    records,reason=ordered_cast_records([s for s in starts if s.get('playerObjectId')==player and s['skillGroup']==row['skillGroup']],finishes,player)
    if reason:return row
    candidates=[r for r in records if r['reason']=='open-final-cast' and any(a<=r['start']['tick']<b for a,b in intervals)]
    if len(candidates)!=count:return row
    excluded=[]
    for r in candidates:
        start=r['start'];left=command_order(start);right=command_order(end)
        if left is None or left>=right or start['tick']>end['tick']:continue
        if any(s.get('ownerPlayerObjectId')==player and (command_order(s) is None or command_order(s)>=left) for s in spawns):continue
        if any(o[0]==start['tick'] for o in row.get('outcomes',[])):continue
        excluded.append(start['tick'])
    if not excluded:return row
    row=dict(row);reasons['open-final-cast']-=len(excluded)
    row.update(unresolvedCombatCastCount=row['unresolvedCombatCastCount']-len(excluded),
        unresolvedCastReasons={k:v for k,v in reasons.items() if v},
        provisionallyExcludedWinnerInterruptedCastCount=len(excluded),
        provisionallyExcludedWinnerInterruptedCastTicks=excluded,
        winnerEndEvidence=end,syntheticSkillFinishCreated=False,
        winnerInterruptionAssumption='An open use without a recorded owned projectile is provisionally excluded when the real winner end interrupts gameplay; an unrecorded direct attack miss may be excluded.')
    if row.get('unresolvedUseEvidence'):
        row['unresolvedUseEvidence']=[u for u in row['unresolvedUseEvidence'] if u.get('startTick') not in excluded]
    return annotate_provisional(row,row['winnerInterruptionAssumption'])


def apply_development_cancellation_tree(row,starts,finishes,spawns,player,gaps, *, intervals,**kwargs):
    """Evaluate each phase's own evidence; never copy a parent's exclusions."""
    current=dict(row,_developmentCombatIntervals=intervals)
    result=apply_development_cancellation(current,starts,finishes,spawns,player,gaps,**kwargs)
    result.pop('_developmentCombatIntervals',None)
    if isinstance(row.get('phaseMetrics'),dict):
        result['phaseMetrics']={name:apply_development_cancellation_tree(
            child,starts,finishes,spawns,player,gaps,intervals=intervals,**kwargs)
            for name,child in row['phaseMetrics'].items()}
    return result


def apply_development_cancellation(row,starts,finishes,spawns,player,gaps, *, catalog=None,game_db_sha256=None,raw_actions=None):
    from .skill_development_effect_metrics import development_policy
    policy=development_policy()
    if not policy.get('lifecycleEstimatesEnabled') or row.get('mode')!='any' or row.get('unit')!='skill-cast':return row
    reasons=row.get('unresolvedCastReasons') or {}
    count=reasons.get('cast-not-normally-complete',0)
    use_evidence=row.get('unresolvedUseEvidence')
    if not count and not use_evidence:return row
    if row.get('status') not in {'calculable-observed','calculable-experimental','unresolved-evidence'}:return row
    if use_evidence is not None:
        # Exact per-use evidence avoids guessing which overlapping aggregate
        # reasons describe one cancelled use (e.g. parent+missing child).
        # Other routes expose diagnostic rows under the same public key.
        # An optional cancellation policy must not reinterpret those rows.
        if not isinstance(use_evidence,list) or any(
                not isinstance(u,dict) or not isinstance(u.get('reasons'),list)
                or any(not isinstance(r,str) for r in u['reasons'])
                or type(u.get('startTick')) is not int
                or not isinstance(u.get('startOrder'),(list,tuple))
                for u in use_evidence):return row
        if len(use_evidence)!=row.get('unresolvedCombatCastCount'):return row
        if Counter(x for u in use_evidence for x in u['reasons'])!=Counter(reasons):return row
        keys=[(u['startTick'],tuple(u['startOrder'] or ())) for u in use_evidence]
        if any(not k[1] for k in keys) or len(set(keys))!=len(keys):return row
        allowed={'cast-not-normally-complete','missing-recorded-projectile-emission',
                 'parent-not-normally-complete','missing-declared-owned-child'}
        def eligible_absence(u):
            reasons = set(u['reasons'])
            if u.get('hasRecordedEmission') is not False:
                return False
            if reasons <= allowed:
                return True
            # A missing aim is expected when a parent is cancelled before its
            # first aim action. Only a complete owned-child producer graph can
            # establish the absence of its later emissions across parent finish.
            return (u.get('emissionKind') == 'owned-child'
                    and u.get('emissionEvidenceComplete') is True
                    and u.get('hasRecordedProducerAction') is False
                    and 'parent-not-normally-complete' in reasons
                    and reasons <= allowed | {'incomplete-parent-aim-slots'})
        eligible={key:u for key,u in zip(keys,use_evidence)
                  if eligible_absence(u)
                  # Direct effects cannot assert absence of an emission from
                  # absent damage. Preserve the existing provisional policy:
                  # exact cancellation + no hit, then check projectile evidence
                  # below. Do not treat this as a proven pre-emission cancel.
                  or (u.get('emissionKind')=='direct-effect'
                      and u.get('hasRecordedEffectEvidence') is False
                      and set(u['reasons'])=={'cast-not-normally-complete'})
                  or (raw_actions is not None and set(u['reasons'])=={'missing-recorded-skill-action'})}
    else:eligible=None
    if gaps is None or spawns is None or any(g.get('count',0) and g.get('packetName') in {'CmdStartSkill','CmdFinishSkill','CmdSpawn','CmdDamage'} for g in gaps):return row
    group=row['skillGroup'];own=[s for s in starts if s.get('playerObjectId')==player]
    ids={s['skillIdCode'] for s in own if s['skillGroup']==group}
    records,reason=ordered_cast_records([s for s in own if s['skillIdCode'] in ids],finishes,player,allow_same_tick_finishes=True)
    if reason:return row
    outcomes={o[0] for o in row.get('outcomes',[])}
    def key(e):return e['tick'],command_order(e) or ()
    candidates=[r for r in records if r['start']['skillGroup']==group and r['complete'] and r['finish'].get('reason') in set(range(1,15))|{16,17} and r['start']['tick'] not in outcomes]
    # Exact number and combat membership are checked by the caller-provided
    # combat intervals below; never infer which unknown uses a reason denotes.
    intervals=row.get('_developmentCombatIntervals')
    if intervals is None:return row
    candidates=[r for r in candidates if any(a<=r['start']['tick']<b for a,b in intervals)]
    if eligible is None:
        if len(candidates)!=count:return row
    else:
        candidates=[r for r in candidates if key(r['start']) in eligible]
    from .skill_execution_plan import planned_family_candidates
    family=planned_family_candidates(row,'projectileCodes') if 'characterCode' in row else None
    candidate_codes=family[1] if family is not None else None
    reviewed={}
    for rule in policy.get('reviewedNonAttackProjectiles',[]):
        if (game_db_sha256 is None or rule['gameDataSha256']!=game_db_sha256
                or rule['skillGroup']!=group or rule['characterCode']!=row.get('characterCode')):continue
        code=rule['nonAttackProjectileCode']
        definition=(catalog or {}).get('projectileDefinitions',{}).get(str(code))
        if definition is None:continue
        digest=hashlib.sha256(json.dumps(definition,sort_keys=True,separators=(',',':')).encode()).hexdigest()
        if digest==rule['definitionSha256']:reviewed[code]=rule
    def inert_non_candidate(s):
        code=s.get('projectileCode')
        if candidate_codes is None or type(code) is not int or code in candidate_codes:return False
        definition=(catalog or {}).get('projectileDefinitions',{}).get(str(code),{})
        # An unnamed attack is absent from name-based families. Absence alone
        # cannot exclude it: require a non-colliding, non-explosive object.
        return (all(definition.get(k) is False for k in ('collisionEnabled','collisionAfterArrival','isExplosion','isExplosionWithoutCollision'))
                and definition.get('lifeTimeAfterArrival')==0)
    excluded=[]
    excluded_keys=set()
    ignored=set()
    reviewed_ignored=set()
    disjoint_evidence=[]
    for r in candidates:
        use=eligible.get(key(r['start'])) if eligible is not None else None
        if use and use.get('emissionKind')=='owned-child' and use.get('emissionEvidenceComplete') is True:
            # The producer has already searched the complete exact aim/spawn
            # graph, including children appearing after the parent's finish.
            # A concurrent projectile from another skill is not this channel.
            excluded.append(r['start']['tick']);excluded_keys.add(key(r['start']))
            continue
        if use and set(use['reasons'])=={'missing-recorded-skill-action'}:
            if any(g.get('count',0) and g.get('packetName') in {'CmdPlaySkillAction','CmdPlaySkillActionWithTargets'} for g in gaps):continue
            later=[key(s) for s in own if s.get('skillIdCode')==r['start']['skillIdCode'] and key(s)>key(r['start'])]
            next_use=min(later) if later else None
            aa=[a for a in raw_actions if a.get('sourceObjectId')==player and a.get('skillIdCode')==r['start']['skillIdCode']]
            # A delayed action after cancellation still counts as emission.
            # Missing command identity is not evidence of absent action.
            if any(command_order(a) is None or (key(a)>=key(r['start']) and (next_use is None or key(a)<next_use)) for a in aa):continue
        owned=[s for s in spawns if s.get('ownerPlayerObjectId')==player and key(r['start'])<=key(s)<=key(r['finish'])]
        if use and use.get('hasRecordedEmission') is False:
            from .skill_disjoint_cancel_candidates import disjoint_cancel_evidence
            evidence=disjoint_cancel_evidence(row,r,owned,spawns,own,raw_actions,player,gaps,catalog,game_db_sha256,policy)
            if evidence is not None:
                excluded.append(r['start']['tick']);excluded_keys.add(key(r['start']))
                disjoint_evidence.append(evidence)
                continue
        # Collision flags alone cannot distinguish sound dummies from attacks.
        # A reviewed exact-version role can, but a real attack emitted after
        # cancellation must still block exclusion. Search through the next
        # same-skill use, not a guessed post-cast time window.
        used_rules=[reviewed[s['projectileCode']] for s in owned if s.get('projectileCode') in reviewed]
        next_keys=[key(s) for s in own if s['skillGroup']==group and key(s)>key(r['start'])]
        next_key=min(next_keys) if next_keys else None
        attack_codes={c for rule in used_rules for c in rule['attackProjectileCodes']}
        if attack_codes and any(s.get('ownerPlayerObjectId')==player and s.get('projectileCode') in attack_codes
                and key(s)>=key(r['start']) and (next_key is None or key(s)<next_key) for s in spawns):continue
        # This is candidate-based denominator policy, not a static proof that
        # the skill cannot emit another code. Missing identity still blocks it.
        unrelated=[s for s in owned if s.get('projectileCode') in reviewed or inert_non_candidate(s)]
        reviewed_ignored.update(s['projectileCode'] for s in unrelated if s.get('projectileCode') in reviewed)
        ignored.update(s['projectileCode'] for s in unrelated)
        emitted=any(s not in unrelated for s in owned)
        if not emitted:
            excluded.append(r['start']['tick']);excluded_keys.add(key(r['start']))
    if not excluded:return row
    if row.get('attemptCount') is None and row['unresolvedCombatCastCount']!=len(excluded):return row
    row=dict(row);remaining=dict(reasons)
    if disjoint_evidence:
        row['experimentalDisjointCancellationEvidence']=disjoint_evidence
        row['disjointCancellationIsVerified']=False
    if eligible is None:
        remaining['cast-not-normally-complete']-=len(excluded)
    else:
        for k in excluded_keys:
            for why in eligible[k]['reasons']:remaining[why]-=1
        row['unresolvedUseEvidence']=[u for u in use_evidence if (u['startTick'],tuple(u['startOrder'])) not in excluded_keys]
    remaining={k:v for k,v in remaining.items() if v}
    if row.get('attemptCount') is None:
        # A fully cancelled denominator has no rate. Preserve unavailable
        # numerical fields if any genuinely unresolved use still remains.
        from .requested_skill_hit_rates import _result
        empty=_result(row,[],'provisionally-excluded-pre-emission-cancellations',cast_ticks=[])
        row.update(empty)
    row.update(status='calculable-experimental',calculationConfidence='experimental',developmentLabel=policy['label'],
        verifiedCompletionCredit=False,fullRequestedMetricComplete=False,verifiedCombatCastCount=0,
        provisionalCombatCastCount=row['attemptCount'],binaryCastSuccessComplete=False,recordedCastOutcomesComplete=False,
        hitRateScope='provisional-classified-uses',
        developmentPolicy='data/development-hit-rate-policy-v1.json',
        unresolvedCombatCastCount=row['unresolvedCombatCastCount']-len(excluded),unresolvedCastReasons=remaining,
        beforeDevelopmentCancellation=dict(unresolvedCombatCastCount=row['unresolvedCombatCastCount'],unresolvedCastReasons=reasons),
        provisionallyExcludedCancelledCastCount=len(excluded),provisionallyExcludedCancelledCastTicks=excluded,
        cancellationIgnoredNonCandidateProjectileCodes=sorted(ignored),
        cancellationReviewedNonAttackProjectileCodes=sorted(reviewed_ignored),
        cancellationNonAttackRoleEvidence=[reviewed[c] for c in sorted(reviewed_ignored)],
        cancellationProjectileCandidateMappingUsed=candidate_codes is not None,
        cancellationPolicyAssumption='Canceled use without a recorded attack projectile and without a classified hit is provisionally pre-emission. Ignore inert non-candidates or exact-version reviewed non-attack roles; reviewed real attack emission before the next same-skill use blocks exclusion. A direct attack miss may be excluded incorrectly.',
        cancelledWithoutConfirmedHitCountedAsMiss=False,wholeCombatHitRate=None,
        interpretation=row.get('interpretation','')+' 개발 중: 발사체·확정 타격 없이 취소된 사용은 잠정적으로 분모에서 제외.')
    if not row['attemptCount']:
        row.update(status='no-combat-sample',denominatorStatus='all-recorded-combat-uses-provisionally-cancelled',
            reason='관측된 교전 시전은 모두 발사 전 취소로 잠정 제외되어 적중률 분모가 없음')
    return row
