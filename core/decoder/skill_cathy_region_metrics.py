"""Cathy W region is explicitly sent as action 1 (inner) or 2 (outer)."""
from collections import Counter,defaultdict
try:
    from .requested_skill_hit_rates import _result,_unavailable
    from .skill_action_stage_evidence import load_exact_skill_ids
    from .skill_wire_order import event_within_cast,finish_lookup
except ImportError:
    from requested_skill_hit_rates import _result,_unavailable
    from skill_action_stage_evidence import load_exact_skill_ids
    from skill_wire_order import event_within_cast,finish_lookup


def cathy_region_metric(spec,starts,finishes,actions,damages,player,teams,intervals,catalog,skill_rows,gaps,development=False):
    try:
        from .requested_skill_scope import exact_cast_lifetimes
    except ImportError:
        from requested_skill_scope import exact_cast_lifetimes
    def fail(reason):return _unavailable(spec,reason)
    if spec.get('skillGroup')!=1023300 or spec.get('mode') not in {'any','inner-hit','outer-hit'}:return fail('unsupported static action region')
    if actions is None or damages is None or gaps is None or any(g.get('count',0) and g.get('packetName') in {'CmdStartSkill','CmdFinishSkill','CmdPlaySkillAction','CmdPlaySkillActionWithTargets','CmdDamage'} for g in gaps):return fail('complete Cathy W command streams required')
    definition=catalog.get('skillGroups',{}).get('1023300',{})
    if definition.get('skillId')!='CathyActive2' or definition.get('characterCode')!=23:return fail('Cathy W gameDb identity mismatch')
    wire=load_exact_skill_ids()['CathyActive2'];codes={r['code'] for r in skill_rows if r.get('group')==1023300}
    selected=[s for s in starts if s['playerObjectId']==player and s['skillGroup']==1023300]
    if any(s['skillIdCode']!=wire or s['skillCode'] not in codes for s in selected):return fail('Cathy W wire cast identity mismatch')
    lives,reason=exact_cast_lifetimes(selected,finishes,player)
    if reason:return fail(reason)
    lookup=finish_lookup(finishes,player);contacts=[{1:set(),2:set()} for _ in lives];unknown=defaultdict(set)
    damage_targets={(d['tick'],d['targetObjectId']) for d in damages if d.get('attackerObjectId')==player}
    for a in actions:
        if a.get('sourceObjectId')!=player or a.get('skillIdCode')!=wire or a.get('actionNo') not in {1,2}:continue
        ts=[t.get('targetObjectId') for t in a.get('targets',[]) if t.get('targetObjectId') in teams and teams[t['targetObjectId']]!=teams.get(player)]
        if not ts:continue
        candidates=[i for i,(s,end) in enumerate(lives) if event_within_cast(s,end,a,lookup)]
        if len(candidates)!=1:
            for i in candidates:unknown[i].add('ambiguous-region-action-cast')
            if not candidates:return fail('enemy region action outside observed Cathy W lifetime')
            continue
        i=candidates[0]
        for target in ts:
            if (a['tick'],target) not in damage_targets:unknown[i].add('region-action-without-same-event-damage');continue
            contacts[i][a['actionNo']].add((a['tick'],target))
    valid=[];observed=[];estimated=[];excluded=[]
    for i,(s,end) in enumerate(lives):
        if not any(l<=s['tick']<r for l,r in intervals):continue
        observed.append(i);ends=lookup.get((wire,end),[])
        if len(ends)!=1 or ends[0].get('reason')!=0:unknown[i].add('incomplete-or-cancelled-W')
        if {o for t,o in contacts[i][1]} & {o for t,o in contacts[i][2]}:unknown[i].add('one-target-in-conflicting-W-regions')
        if development and unknown[i]=={'incomplete-or-cancelled-W'} and len(ends)==1 and ends[0].get('reason') in set(range(1,15))|{16,17}:
            if contacts[i][1] or contacts[i][2]:unknown[i].clear();estimated.append(i)
            else:excluded.append(i);continue
        if not unknown[i]:valid.append(i)
    chosen_actions={'any':(1,2),'inner-hit':(1,),'outer-hit':(2,)}[spec['mode']]
    combined=[set().union(*(contacts[i][action] for action in chosen_actions)) for i in valid]
    row=_result(spec,combined,'static-Cathy-W-inner1-outer2-explicit-action-target-and-damage',cast_ticks=[lives[i][0]['tick'] for i in valid]) if valid or not observed else fail('no complete unambiguous W uses')
    row.update(observedCombatCastCount=len(observed),verifiedCombatCastCount=len(valid),unresolvedCombatCastCount=len(observed)-len(valid)-len(excluded),
        unresolvedCastReasons=dict(Counter(reason for i in observed if i not in excluded for reason in unknown[i])),
        perUseCompletenessTracked=True,incompleteUsesCountedAsMisses=False,normalFinishRequired=True,
        exactRegionActionNumber=chosen_actions[0] if len(chosen_actions)==1 else None,geometryInferred=False,missingSlowUsedAsInner=False,
        evidenceReview='deliverables/cathy-W-static-region-proof-v1.json')
    if estimated or excluded:
        from .skill_development_cancellation import annotate_provisional
        row.update(provisionallyExcludedCancelledCastCount=len(excluded),provisionallyExcludedCancelledCastTicks=[lives[i][0]['tick'] for i in excluded])
        annotate_provisional(row,'Recorded W inner/outer action and target damage survive later cancellation. A canceled W with no contact is provisionally excluded; absent outer state never implies inner hit.')
    return row
