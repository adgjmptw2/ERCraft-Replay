"""Actual R world lifetime: edge stun application and accumulated damage."""
from collections import Counter,defaultdict
try:
    from .requested_skill_hit_rates import _result,_unavailable
    from .skill_partial_cast_lifetimes import ordered_cast_records
    from .skill_wire_order import command_order as order
    from .skill_action_stage_evidence import load_exact_skill_ids
except ImportError:
    from requested_skill_hit_rates import _result,_unavailable
    from skill_partial_cast_lifetimes import ordered_cast_records
    from skill_wire_order import command_order as order
    from skill_action_stage_evidence import load_exact_skill_ids

GAMEPLAY_FINISHES=set(range(15))|{16,17}
STUN_CODES={1081531,1081532,1081533}

def inside(child,event):
    """Recorded lifetime, with exact order required on either boundary tick."""
    s,e=child['start'],child['finish']
    if not s['tick']<=event['tick']<=e['tick']:return False
    at=order(event)
    if at is None:return True if s['tick']<event['tick']<e['tick'] else None
    return order(s)<at<order(e)

def niah_parent_r_metric(spec,starts,finishes,actions,damages,player,teams,intervals,
                         catalog,skill_rows,state_rows,state_groups,effect_rows,inputs,skill_ids=None):
    selected=[s for s in starts if s['playerObjectId']==player and s['skillGroup']==1081500]
    combat=lambda s:any(a<=s['tick']<b for a,b in intervals)
    diag=dict(observedCastCount=len(selected),observedCombatCastCount=sum(map(combat,selected)))
    def fail(why):return {**_unavailable(spec,why),**diag}
    if spec['characterCode']!=81 or spec['skillGroup']!=1081500 or spec['mode'] not in ('any','world-edge-stun') or spec['unit']!='skill-cast':return fail('unsupported Niah R metric')
    ids=load_exact_skill_ids() if skill_ids is None else skill_ids
    for group,name,wire in [(1081500,'NiahActive4',1182),(1081800,'NiahSummonActive4',1189)]:
        if ids.get(name)!=wire or catalog['skillGroups'].get(str(group),{}).get('skillId')!=name:return fail('pinned R parent/child identity mismatch')
    if any(inputs.get(k) is None for k in ('summons','objects','nonPlayerSkillStarts','states','gaps','summon_rows')) or actions is None or damages is None:return fail('missing R world evidence stream')
    gaps={'CmdStartSkill','CmdFinishSkill','CmdSpawn','CmdSpawnBatch'}
    gaps|={'CmdAddState','CmdAddStateExtended'} if spec['mode']=='world-edge-stun' else {'CmdPlaySkillAction','CmdDamage'}
    if any(g.get('count',0) and g.get('packetName') in gaps for g in inputs['gaps']):return fail('incomplete R world command stream')
    sr=[s for s in inputs['summon_rows'] if s.get('code')==1585]
    if len(sr)!=1 or sr[0].get('prefabPath')!='Object_FX_BI_Niah_Skill04_World' or sr[0].get('useAttackerType')!='None':return fail('pinned R world definition mismatch')
    if spec['mode']=='world-edge-stun':
        if state_rows is None or state_groups is None:return fail('missing R stun definitions')
        if {s['code'] for s in state_rows if s.get('group')==1081530}!=STUN_CODES:return fail('pinned R stun codes mismatch')
        sg=[s for s in state_groups if s.get('group')==1081530]
        if len(sg)!=1 or sg[0].get('stateType')!='Stun':return fail('pinned R stun type mismatch')
    else:
        fx=[f for f in effect_rows or [] if f.get('code')==1081503]
        if len(fx)!=1 or fx[0].get('effectPrefabName')!='FX_BI_Niah_Skill04_Damage_Hit':return fail('pinned R range effect mismatch')
    code_groups={s['code']:s['group'] for s in skill_rows}
    if any(s['skillIdCode']!=1182 or code_groups.get(s['skillCode'])!=1081500 for s in selected):return fail('R parent wire/skill mismatch')
    parents,why=ordered_cast_records(selected,finishes,player,allow_same_tick_finishes=True)
    if why:return fail(why)
    objects=defaultdict(list)
    for o in inputs['objects']:objects[o['objectId']].append(o)
    worlds=defaultdict(list)
    for w in inputs['summons']:
        if w['ownerObjectId']!=player or w['summonCode']!=1585:continue
        oo=objects[w['objectId']]
        if len(oo)!=1 or order(oo[0]) is None or oo[0]['tick']!=w['tick'] or w.get('identityVerifiedAgainstGameDb') is not True:return fail('missing exact R world spawn')
        matches=[i for i,r in enumerate(parents) if order(r['start'])<order(oo[0]) and (r['finish'] is None or order(oo[0])<order(r['finish']))]
        if len(matches)!=1:return fail('R world has no unique parent cast')
        worlds[matches[0]].append({**w,'wireCategory':'commands','wireOrder':oo[0]['wireOrder']})
    unknown={};linked={};open_worlds={}
    for i,r in enumerate(parents):
        if not r['complete'] or r['finish'].get('reason') not in GAMEPLAY_FINISHES:unknown[i]='open-R-parent';continue
        if len(worlds[i])!=1:unknown[i]='missing-or-multiple-R-worlds';continue
        w=worlds[i][0];bid=w['objectId'];cs=[{**s,'playerObjectId':bid} for s in inputs['nonPlayerSkillStarts'] if s['sourceObjectId']==bid]
        if len(cs)!=1 or cs[0]['skillIdCode']!=1189 or code_groups.get(cs[0]['skillCode'])!=1081800:unknown[i]='missing-or-invalid-R-world-child';continue
        children,why=ordered_cast_records(cs,finishes,bid,allow_same_tick_finishes=True)
        if why or len(children)!=1:unknown[i]='open-R-world-child';continue
        ch=children[0]
        if not order(w)<order(ch['start'])<order(r['finish']):unknown[i]='R-child-outside-parent-creation';continue
        if not ch['complete'] or ch['finish'].get('reason') not in GAMEPLAY_FINISHES:
            unknown[i]='open-R-world-child'
            if ch['reason'] in ('open-final-cast','replay-end-is-not-gameplay-finish'):
                open_worlds[i]=dict(world=w,child=ch)
            continue
        linked[i]=dict(world=w,child=ch)
    # One owner may have multiple R uses; never choose a world by proximity.
    links=list(linked.items())
    for n,(i,a) in enumerate(links):
        for j,b in links[n+1:]:
            if max(order(a['child']['start']),order(b['child']['start']))<min(order(a['child']['finish']),order(b['child']['finish'])):return fail('overlapping R worlds from one owner')
    # An unclosed world remains a possible producer after its exact start.
    # It cannot donate hits, but must not erase earlier disjoint closed worlds.
    for i,v in linked.items():
        if any(order(w['child']['start'])<order(v['child']['finish']) for w in open_worlds.values()):
            unknown[i]='overlapping-open-R-world'
    contacts=defaultdict(set);details=defaultdict(list);emissions=defaultdict(list)
    if spec['mode']=='any':
        for i,v in linked.items():
            bid=v['world']['objectId']
            for a in actions:
                if a['sourceObjectId']!=bid or a['skillIdCode']!=1189 or a['actionNo']!=242:continue
                if order(a) is None or a.get('wireStatus')!='decoded-exact-CmdPlaySkillAction' or inside(v['child'],a) is not True:unknown[i]='invalid-R-range-emission';continue
                emissions[i].append(a)
        events=[d for d in damages if d['attackerObjectId']==player and d['effectCode']==1081503]
    else:events=[s for s in inputs['states'] if s['event']=='add' and s.get('casterObjectId')==player and s.get('stateCode') in STUN_CODES]
    for event in events:
        target=event['targetObjectId']
        if target not in teams or teams[target]==teams[player]:continue
        possibilities=[(i,inside(v['child'],event)) for i,v in linked.items()]
        uncertain=[i for i,present in possibilities if present is None]
        certain=[i for i,present in possibilities if present is True]
        pending=[i for i,v in open_worlds.items()
                 if event['tick']>=v['child']['start']['tick']
                 and (order(event) is None or order(v['child']['start'])<order(event))]
        if pending:
            for i in uncertain+certain:unknown[i]='overlapping-open-R-world'
            continue
        if uncertain:
            for i in uncertain+certain:unknown[i]='missing-R-contact-boundary-order'
            continue
        if len(certain)!=1:
            return fail('R contact outside every exact world lifetime')
        i=certain[0]
        if spec['mode']=='any' and (order(event) is None or not any(a['tick']==event['tick'] and order(a)<order(event) for a in emissions[i])):
            unknown[i]='R-damage-without-earlier-synchronous-emission';continue
        contacts[i].add((event['tick'],target))
        details[i].append(dict(hitTick=event['tick'],targetObjectId=target,worldObjectId=linked[i]['world']['objectId'],
            phase='world-edge-stun' if spec['mode']=='world-edge-stun' else 'accumulated-area-damage',
            evidenceCode=event['stateCode'] if spec['mode']=='world-edge-stun' else event['effectCode']))
    recovered={};recovery_evidence=[]
    # An existential positive can be settled before the world closes. Keep
    # this independent of a complete negative or complete future target list.
    if not any(g.get('count',0) for g in inputs['gaps']):
        for i,v in open_worlds.items():
            ch=v['child'];cs=ch['start'];bid=v['world']['objectId']
            if ch.get('reason')!='open-final-cast' or unknown.get(i)!='open-R-world-child':continue
            if order(cs) is None or parents[i]['start']['tick']>v['world']['tick'] or v['world']['tick']>cs['tick']:continue
            if any(j!=i and (j not in linked or linked[j]['child']['finish']['tick']>=cs['tick'] or order(linked[j]['child']['finish'])>=order(cs)) for j in worlds):continue
            # Unknown owners of an R world cannot be silently filtered out.
            if any(w.get('summonCode')==1585 and w.get('ownerObjectId') not in teams for w in inputs['summons']):continue
            possible_events=[e for e in events if e.get('targetObjectId') in teams and teams[e['targetObjectId']]!=teams[player]
                and type(e.get('tick')) is int and e['tick']>=cs['tick'] and order(e) is not None and order(cs)<order(e)]
            for event in possible_events:
                eo=order(event)
                # No other owned world may be an active producer at this event;
                # missing or contradictory boundaries remain candidates.
                rivals=[]
                for j,ww in worlds.items():
                    if j==i:continue
                    for w in ww:
                        wo=order(w)
                        if wo is None or (w['tick']<=event['tick'] or wo<=eo):
                            other=linked.get(j)
                            if other and order(other['child']['finish']) is not None and other['child']['finish']['tick']<event['tick'] and order(other['child']['finish'])<eo:continue
                            rivals.append(j)
                if rivals:continue
                emission=None
                if spec['mode']=='any':
                    aa=[a for a in actions if a.get('sourceObjectId')==bid and a.get('skillIdCode')==1189 and a.get('actionNo')==242 and a.get('tick')==event['tick']]
                    if len(aa)!=1 or aa[0].get('wireStatus')!='decoded-exact-CmdPlaySkillAction' or order(aa[0]) is None or not order(cs)<order(aa[0])<eo or order(aa[0])[0]!=eo[0]:continue
                    emission=aa[0]
                elif event.get('stateGroup') not in (None,1081530):continue
                contacts[i].add((event['tick'],event['targetObjectId']))
                details[i].append(dict(hitTick=event['tick'],targetObjectId=event['targetObjectId'],worldObjectId=bid,phase='world-edge-stun' if spec['mode']=='world-edge-stun' else 'accumulated-area-damage',evidenceCode=event.get('stateCode') if spec['mode']=='world-edge-stun' else event['effectCode']))
                recovered[i]=v
                recovery_evidence.append(dict(startTick=parents[i]['start']['tick'],worldObjectId=bid,worldSpawnOrder=order(v['world']),childStartOrder=order(cs),contactTick=event['tick'],contactOrder=eo,targetObjectId=event['targetObjectId'],rangeEmissionOrder=order(emission) if emission else None,childFinishInvented=False))
        for i in recovered:unknown.pop(i)
    chosen=[i for i,r in enumerate(parents) if combat(r['start']) and i not in unknown]
    reasons=Counter(reason for i,reason in unknown.items() if combat(parents[i]['start']))
    diag.update(unresolvedCombatCastCount=sum(reasons.values()),unresolvedCastReasons=dict(reasons),
        unresolvedCastTicks=[r['start']['tick'] for i,r in enumerate(parents) if i in unknown and combat(r['start'])],
        openWorldCastTicks=[parents[i]['start']['tick'] for i in open_worlds],
        openWorldContactsAssignedAsHits=False,perUseCompletenessTracked=True,incompleteUsesCountedAsMisses=False)
    if diag['observedCombatCastCount'] and not chosen:return fail('no closed R world outcomes')
    row=_result(spec,[contacts[i] for i in chosen],
        'static-R-world-owned-child-lifetime-and-exact-enemy-stun' if spec['mode']=='world-edge-stun' else 'static-R-world-owned-child-synchronous-range-damage',
        cast_ticks=[parents[i]['start']['tick'] for i in chosen])
    row.update(diag,contactDetailsByAttempt=[details[i] for i in chosen],
        parentWorldEvidenceByAttempt=[dict(worldObjectId=linked[i]['world']['objectId'],worldSpawnOrder=linked[i]['world']['wireOrder'],
            childStartOrder=linked[i]['child']['start']['wireOrder'],childFinishOrder=linked[i]['child']['finish']['wireOrder'],
            childEndTick=linked[i]['child']['finish']['tick'],childFinishReason=linked[i]['child']['finish']['reason'],rangeEmissionCount=len(emissions[i])) if i in linked else dict(worldObjectId=recovered[i]['world']['objectId'],worldSpawnOrder=recovered[i]['world']['wireOrder'],childStartOrder=recovered[i]['child']['start']['wireOrder'],childFinishOrder=None,childEndTick=None,childFinishReason=None,childFinishInvented=False) for i in chosen],
        evidenceReview='deliverables/niah-r-world-static-proof-v1.json',fixedDurationWindowUsed=False)
    if recovered:
        try:
            from .skill_development_cancellation import annotate_provisional
        except ImportError:
            from decoder.skill_development_cancellation import annotate_provisional
        annotate_provisional(row,'Exact owned open-world positive contact settles binary success only; future targets and world completion remain unknown.')
        row.update(openWorldPositiveEvidence=recovery_evidence,openWorldContactsAssignedAsHits=True,
            recordedPositiveDespiteOpenWorldCount=len(recovered),targetCountsAreLowerBounds=True,
            completeTargetCountsAvailable=False,fullRequestedMetricComplete=False,verifiedCompletionCredit=False,
            verifiedCombatCastCount=0)
    return row
