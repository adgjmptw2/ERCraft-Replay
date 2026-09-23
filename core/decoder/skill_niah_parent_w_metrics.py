"""W normal/R actual drop callback -> persistent block -> pull and explosions."""
from collections import Counter,defaultdict
try:
    from .requested_skill_hit_rates import _result,_unavailable
    from .skill_partial_cast_lifetimes import ordered_cast_records
    from .skill_wire_order import command_order as order
    from .skill_projectile_active_end import projectile_active_end_records
    from .skill_owned_child_damage_metrics import owned_child_damage_metric
    from .skill_action_stage_evidence import load_exact_skill_ids
    from .skill_niah_pull_disposition import pull_disposition
    from .skill_ordered_match_end import ordered_winner_match_end
except ImportError:
    from requested_skill_hit_rates import _result,_unavailable
    from skill_partial_cast_lifetimes import ordered_cast_records
    from skill_wire_order import command_order as order
    from skill_projectile_active_end import projectile_active_end_records
    from skill_owned_child_damage_metrics import owned_child_damage_metric
    from skill_action_stage_evidence import load_exact_skill_ids
    from skill_niah_pull_disposition import pull_disposition
    from skill_ordered_match_end import ordered_winner_match_end

def niah_parent_w_metric(spec,starts,finishes,spawns,terminals,actions,damages,player,teams,intervals,
                         catalog,skill_rows,effect_rows,inputs,skill_ids=None):
    selected=[s for s in starts if s['playerObjectId']==player and s['skillGroup']==spec['skillGroup']]
    combat=lambda s:any(a<=s['tick']<b for a,b in intervals)
    diag=dict(observedCastCount=len(selected),observedCombatCastCount=sum(map(combat,selected)))
    def fail(why):return {**_unavailable(spec,why),**diag}
    groups={1081300:('NiahActive2',1176),1081310:('NiahActive2_R',1177)}
    if spec['characterCode']!=81 or spec['skillGroup'] not in groups or spec['mode']!='any' or spec['unit']!='skill-cast':return fail('unsupported Niah W parent')
    ids=load_exact_skill_ids() if skill_ids is None else skill_ids
    if any(ids.get(n)!=w or catalog['skillGroups'].get(str(g),{}).get('skillId')!=n for g,(n,w) in groups.items()):return fail('pinned Niah W identity mismatch')
    if any(inputs.get(k) is None for k in ('objects','summons','gaps','states','deaths','nonPlayerSkillStarts')) or actions is None:return fail('missing W object/state/child stream')
    if any(g.get('count',0) and g.get('packetName') in {'CmdSpawn','CmdSpawnBatch','CmdDestroy','CmdDestroyDelayStart','CmdDead','CmdKill','CmdDyingCondition'} for g in inputs['gaps']):return fail('incomplete W lineage stream')
    code_groups={r['code']:r['group'] for r in skill_rows}
    roots=[s for s in starts if s['playerObjectId']==player and s['skillGroup'] in groups]
    if any(code_groups.get(s['skillCode'])!=s['skillGroup'] or s['skillIdCode']!=groups[s['skillGroup']][1] for s in roots):return fail('parent W wire/skill code mismatch')
    records,why=ordered_cast_records(roots,finishes,player,allow_same_tick_finishes=True)
    if why:return fail(why)
    pp=[p for p in spawns if p.get('ownerObjectId')==player and p['projectileCode']==108131]
    if any(order(p) is None for p in pp):return fail('missing W point spawn order')
    parents=defaultdict(list)
    for p in pp:
        hits=[i for i,r in enumerate(records) if order(r['start'])<order(p) and (r['finish'] is None or order(p)<order(r['finish']))]
        if len(hits)!=1:return fail('W point has no unique normal/R parent')
        parents[hits[0]].append(p)
    ends=projectile_active_end_records(terminals);removes={}
    for p in pp:
        oid=p['projectileObjectId'];e=ends.get(oid,{})
        ts=[t for t in terminals if t['objectId']==oid and t['event'] in ('CmdDestroy','CmdDestroyDelayStart') and t['tick']==e.get('endTick')]
        if e.get('complete') and ts and all(order(t) is not None for t in ts):removes[oid]=min(ts,key=order)
    objects=defaultdict(list)
    for o in inputs['objects']:objects[o['objectId']].append(o)
    blocks=[]
    for b in inputs['summons']:
        if b['ownerObjectId']!=player or b['summonCode']!=1583:continue
        oo=objects[b['objectId']]
        if b.get('identityVerifiedAgainstGameDb') is not True or len(oo)!=1 or order(oo[0]) is None:return fail('missing exact W block spawn identity')
        blocks.append({**b,'wireCategory':'commands','wireOrder':oo[0]['wireOrder']})
    blocks.sort(key=order);next_block={b['objectId']:n for b,n in zip(blocks,blocks[1:])}
    block_for_parent={};used=set()
    for i,ps in parents.items():
        if len(ps)!=1:continue
        end=removes.get(ps[0]['projectileObjectId'])
        if end is None:continue
        lower=max((order(t) for k,t in removes.items() if k!=ps[0]['projectileObjectId'] and t['tick']==end['tick'] and order(t)<order(end)),default=(-1,-1))
        bb=[b for b in blocks if b['tick']==end['tick'] and lower<order(b)<order(end)]
        if len(bb)==1 and bb[0]['objectId'] not in used:block_for_parent[i]=(bb[0],end);used.add(bb[0]['objectId'])
    if used!={b['objectId'] for b in blocks}:return fail('unassigned or ambiguous W block callback')
    child_rows=defaultdict(list)
    last=max((x['tick'] for rows in (starts,finishes,spawns,terminals,actions) for x in rows),default=0)+1
    for group in (1081710,1081720):
        sink=[]
        result=owned_child_damage_metric({**spec,'skillGroup':group},inputs['nonPlayerSkillStarts'],finishes,inputs['summons'],terminals,actions,damages,
            player,teams,[(0,last)],catalog,skill_rows,inputs['summon_rows'],effect_rows,inputs['gaps'],ids,use_evidence=sink)
        if result['status'] not in ('calculable-observed','no-combat-sample'):
            if result.get('reason')=='no explicit owned child starts' and result.get('observedOwnedChildCastCount')==0:continue
            return fail('W child lineage: '+str(result.get('reason')))
        for r in sink:child_rows[r['start']['sourceObjectId']].append(r)
    match_end=ordered_winner_match_end(inputs.get('gameTerminals'),inputs['gaps'])
    unknown=Counter();contacts=[];ticks=[];details=[];phases=[];proofs=[];pull_status=Counter()
    for i,r in enumerate(records):
        s=r['start']
        if s['skillGroup']!=spec['skillGroup'] or not combat(s):continue
        if not r['complete'] or i not in block_for_parent:unknown['open-parent-or-missing-W-block']+=1;continue
        b,end=block_for_parent[i];bid=b['objectId'];be=ends.get(bid,{})
        closure=None
        if not be.get('complete'):
            if match_end is None or order(match_end)<=order(b):unknown['missing-actual-W-block-end']+=1;continue
            endtick=match_end['tick'];closure='recorded-match-completion-with-live-W-block'
        else:endtick=be['endTick']
        if endtick<b['tick']:unknown['block-end-before-spawn']+=1;continue
        if closure is not None:pass
        elif any(t['objectId']==bid and t['event']=='CmdDestroy' for t in terminals):closure='final-block-destruction'
        elif any(d['event']=='CmdDead' and d['deadObjectId']==bid and d['tick']==endtick and d.get('isDyingBlockDead') is False for d in inputs['deaths']):closure='actual-block-death'
        else:
            n=next_block.get(bid)
            tt=[t for t in terminals if t['objectId']==bid and t['event']=='CmdDestroyDelayStart' and t['tick']==endtick]
            if n and n['tick']==endtick and tt and all(order(t) is not None and order(t)<order(n) for t in tt):closure='synchronous-next-W-replacement'
            elif any(d['deadObjectId']==player and d['tick']==endtick and d['event']=='CmdDead' and d.get('isDyingBlockDead') is False for d in inputs['deaths']):closure='owner-death-clears-W-block'
            elif any(d['deadObjectId']==player and d['tick']==endtick and d['event']=='CmdDyingCondition' and order(d) is not None
                     and tt and all(order(t) is not None and order(d)<order(t) for t in tt) for d in inputs['deaths']):closure='ordered-owner-dying-clears-W-block'
        if closure is None:unknown['W-block-removal-without-closed-owner-lifecycle']+=1;continue
        children=child_rows[bid]
        if any(c['unknown'] for c in children):unknown['incomplete-W-child-execution']+=1;continue
        if any(c['start']['tick']<b['tick'] or c['finish']['tick']>endtick for c in children):unknown['W-child-outside-block-lifetime']+=1;continue
        if closure=='recorded-match-completion-with-live-W-block' and any(order(c['finish']) is None or order(c['finish'])>=order(match_end) for c in children):
            unknown['W-child-after-match-completion']+=1;continue
        ds=[]
        for c in children:
            phase='pull-impact' if c['start']['skillIdCode']==1186 else 'block-explosion'
            for d in c['damagePackets']:
                row=dict(hitTick=d['tick'],targetObjectId=d['targetObjectId'],blockObjectId=bid,phase=phase,childStartOrder=c['start']['wireOrder'])
                if phase=='pull-impact':
                    row.update(pull_disposition(d,c['finish'],inputs['states'],inputs['deaths'],inputs.get('state_groups'),inputs.get('state_rows'),inputs['gaps']))
                    pull_status[row['pullApplication']]+=1
                ds.append(row)
        unique={}
        for d in ds:unique.setdefault((d['phase'],d['hitTick'],d['targetObjectId']),d)
        ds=list(unique.values());ps={}
        for phase,wire in [('pull-impact',1186),('block-explosion',1187)]:
            part=[d for d in ds if d['phase']==phase];ps[phase]=dict(executed=any(c['start']['skillIdCode']==wire for c in children),
                hitCount=int(bool(part)),distinctEnemyCount=len({d['targetObjectId'] for d in part}),firstHitTick=min((d['hitTick'] for d in part),default=None))
        contacts.append({(d['hitTick'],d['targetObjectId']) for d in ds});ticks.append(s['tick']);details.append(ds);phases.append(ps)
        proofs.append(dict(blockObjectId=bid,pointProjectileObjectId=parents[i][0]['projectileObjectId'],blockSpawnOrder=b['wireOrder'],pointRemovalOrder=end['wireOrder'],
                           blockEndTick=endtick,blockEndMethod=closure,childUseCount=len(children)))
    diag.update(unresolvedCombatCastCount=sum(unknown.values()),unresolvedCastReasons=dict(unknown),perUseCompletenessTracked=True,incompleteUsesCountedAsMisses=False)
    if diag['observedCombatCastCount'] and not contacts:return fail('no closed W parent outcomes')
    result=_result(spec,contacts,'static-W-point-created-block-and-owned-child-damage-lineage',cast_ticks=ticks)
    result.update(diag,contactDetailsByAttempt=details,phaseOutcomesByAttempt=phases,parentBlockEvidenceByAttempt=proofs,pullApplicationCounts=dict(pull_status),
                  evidenceReview='deliverables/niah-w-parent-and-pull-static-proof-v2.json',fixedDurationWindowUsed=False)
    return result
