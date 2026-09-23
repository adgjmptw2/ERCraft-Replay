"""Normal/R Q drop projectile -> synchronous landing -> created Q block."""
from collections import Counter,defaultdict
try:
    from .requested_skill_hit_rates import _result,_unavailable
    from .skill_partial_cast_lifetimes import ordered_cast_records
    from .skill_wire_order import command_order
    from .skill_action_stage_evidence import load_exact_skill_ids,ORDINARY_ACTIONS
    from .skill_projectile_active_end import projectile_active_end_records
    from .skill_owned_child_projectile_metrics import owned_child_projectile_metric
except ImportError:
    from requested_skill_hit_rates import _result,_unavailable
    from skill_partial_cast_lifetimes import ordered_cast_records
    from skill_wire_order import command_order
    from skill_action_stage_evidence import load_exact_skill_ids,ORDINARY_ACTIONS
    from skill_projectile_active_end import projectile_active_end_records
    from skill_owned_child_projectile_metrics import owned_child_projectile_metric


class NiahParentQPreparation:
    """Match-wide Niah Q lineage preparation shared by normal and RQ calls."""
    def __init__(self, catalog, starts, finishes, spawns, collisions, terminals,
                 actions, damages, skill_rows, effect_rows, inputs, skill_ids):
        self.sources=(catalog,tuple(map(id,starts)),tuple(map(id,finishes)),spawns,collisions,terminals,
                      actions,damages,skill_rows,effect_rows)
        self.cast_starts=starts
        self.finishes=finishes
        self.input_sources=tuple(inputs.get(k) for k in
                                 ('objects','summons','nonPlayerSkillStarts','gaps','summon_rows'))
        self._cache={}

    def validate(self, catalog, starts, finishes, spawns, collisions, terminals,
                 actions, damages, skill_rows, effect_rows, inputs, skill_ids):
        if (self.sources[0] is not catalog or self.sources[1] != tuple(map(id,starts))
            or self.sources[2] != tuple(map(id,finishes))
            or any(a is not b for a,b in zip(self.sources[3:],
                (spawns,collisions,terminals,actions,damages,skill_rows,effect_rows)))
            or any(a is not b for a,b in zip(self.input_sources,
                 (inputs.get(k) for k in ('objects','summons','nonPlayerSkillStarts','gaps','summon_rows'))))):
            raise ValueError('Niah parent Q preparation belongs to different match inputs')

    def prepare(self, spec, player, teams, groups, skill_ids):
        key=(player, repr(sorted(spec.items())), tuple(sorted(teams.items())),
             tuple(sorted(groups.items())), tuple(sorted(skill_ids.items())))
        if key in self._cache:
            return self._cache[key],None
        catalog,_,_,spawns,collisions,terminals,actions,damages,skill_rows,effect_rows = self.sources
        starts=self.cast_starts
        inputs=dict((k,v) for k,v in zip(('objects','summons','nonPlayerSkillStarts','gaps','summon_rows'),self.input_sources))
        code_groups={r['code']:r['group'] for r in skill_rows}
        roots=[s for s in starts if s['playerObjectId']==player and s['skillGroup'] in groups]
        if any(code_groups.get(s['skillCode'])!=s['skillGroup'] or s['skillIdCode']!=groups[s['skillGroup']][1] for s in roots):
            return None,'parent wire/skill code mismatch'
        if any(inputs.get(k) is None for k in ('objects','summons','nonPlayerSkillStarts','gaps')):
            return None,'missing parent object/action stream'
        fx=[r for r in effect_rows if r.get('code')==1081201]
        if len(fx)!=1 or fx[0].get('effectPrefabName')!='FX_BI_Niah_Skill01_Hit':
            return None,'pinned landing effect mismatch'
        records,why=ordered_cast_records(roots,self.finishes,player,allow_same_tick_finishes=True)
        if why:return None,why
        own=[p for p in spawns if p.get('ownerObjectId')==player and p['projectileCode']==108121]
        if any(command_order(p) is None for p in own):return None,'missing exact drop spawn order'
        projectiles=defaultdict(list)
        for p in own:
            parents=[i for i,r in enumerate(records) if command_order(r['start'])<command_order(p)
                     and (r['finish'] is None or command_order(p)<command_order(r['finish']))]
            if len(parents)!=1:return None,'drop projectile has no unique normal/R parent'
            projectiles[parents[0]].append(p)
        ends=projectile_active_end_records(terminals);removals={}
        for p in own:
            oid=p['projectileObjectId'];e=ends.get(oid,{})
            ts=[t for t in terminals if t['objectId']==oid and t['event'] in ('CmdDestroyDelayStart','CmdDestroy') and t['tick']==e.get('endTick')]
            if e.get('complete') and ts and all(command_order(t) is not None for t in ts):removals[oid]=min(ts,key=command_order)
        objects=defaultdict(list)
        for o in inputs['objects']:objects[o['objectId']].append(o)
        blocks=[]
        for b in inputs.get('summons',[]):
            if b.get('ownerObjectId')!=player or b.get('summonCode')!=1581:continue
            oo=objects[b['objectId']]
            if b.get('identityVerifiedAgainstGameDb') is not True or len(oo)!=1 or command_order(oo[0]) is None:
                return None,'missing exact Q block creation identity'
            blocks.append({**b,'wireOrder':oo[0]['wireOrder'],'wireCategory':oo[0]['wireCategory']})
        aa=[a for a in actions if a.get('sourceObjectId')==player and a.get('skillIdCode') in (1174,1175) and a.get('actionNo')==22]
        if any(command_order(a) is None or a.get('wireStatus') not in ORDINARY_ACTIONS for a in aa):return None,'missing ordered landing22 marker'
        children=inputs.get('nonPlayerSkillStarts')
        last=max([x['tick'] for rows in (starts,self.finishes,spawns,terminals,actions) for x in rows],default=0)+1
        child_spec={**spec,'skillGroup':1081600}
        child=owned_child_projectile_metric(child_spec,children,self.finishes,inputs.get('summons',[]),spawns,collisions,terminals,
            actions,damages,player,teams,[(0,last)],catalog,skill_rows,inputs.get('summon_rows',[]),effect_rows,inputs.get('gaps'),skill_ids)
        if child['status'] not in ('calculable-observed','calculable-experimental','no-combat-sample'):
            return None,'child lineage: '+str(child.get('reason'))
        by_block=defaultdict(list)
        for evidence,details in zip(child['childProjectileEvidenceByAttempt'],child['contactDetailsByAttempt']):
            by_block[evidence['sourceObjectId']].append((evidence,details))
        expected=Counter(s['sourceObjectId'] for s in children if s.get('skillIdCode')==1184)
        value=dict(records=records,projectiles=projectiles,ends=ends,removals=removals,objects=objects,blocks=blocks,
                   aa=aa,children=children,by_block=by_block,expected=expected)
        self._cache[key]=value
        return value,None


def niah_parent_q_metric(spec,starts,finishes,spawns,collisions,terminals,actions,damages,player,teams,intervals,
                         catalog,skill_rows,effect_rows,inputs,skill_ids=None,_preparation=None):
    if any(v is None for v in (starts,finishes,spawns,collisions,terminals,actions,damages,inputs)):
        return _unavailable(spec,'missing original Q streams')
    selected=[s for s in starts if s['playerObjectId']==player and s['skillGroup']==spec['skillGroup']]
    combat=lambda s:any(a<=s['tick']<b for a,b in intervals)
    diag=dict(observedCastCount=len(selected),observedCombatCastCount=sum(map(combat,selected)))
    def fail(why):return {**_unavailable(spec,why),**diag}
    groups={1081200:('NiahActive1',1174),1081210:('NiahActive1_R',1175)}
    if spec['characterCode']!=81 or spec['skillGroup'] not in groups or spec['mode']!='any' or spec['unit']!='skill-cast':
        return fail('unsupported Niah parent Q')
    ids=load_exact_skill_ids() if skill_ids is None else skill_ids
    if any(ids.get(n)!=w or catalog['skillGroups'].get(str(g),{}).get('skillId')!=n for g,(n,w) in groups.items()):
        return fail('pinned normal/R Q identity mismatch')
    preparation = _preparation or NiahParentQPreparation(
        catalog,starts,finishes,spawns,collisions,terminals,actions,damages,
        skill_rows,effect_rows,inputs,ids)
    preparation.validate(catalog,starts,finishes,spawns,collisions,terminals,
                        actions,damages,skill_rows,effect_rows,inputs,ids)
    prepared,why=preparation.prepare(spec,player,teams,groups,ids)
    if why:return fail(why)
    records=prepared['records'];projectiles=prepared['projectiles'];ends=prepared['ends'];removals=prepared['removals']
    objects=prepared['objects'];blocks=prepared['blocks'];aa=prepared['aa'];children=prepared['children']
    by_block=prepared['by_block'];expected=prepared['expected']
    winner_evidence=[];winner_blocked=[];block_winner_evidence=[];block_winner_blocked=[]
    unknown=Counter();contacts=[];ticks=[];details=[];phases=[];proofs=[];assigned=set()
    incomplete_positive=[];phase_unknown=[];block_contacts=[];block_ticks=[];block_unknown_ticks=[]
    for i,r in enumerate(records):
        s=r['start']
        if s['skillGroup']!=spec['skillGroup'] or not combat(s):continue
        if not r['complete']:
            import json
            from pathlib import Path
            from .skill_ordered_match_end import ordered_winner_match_end
            enabled=json.loads((Path(__file__).resolve().parents[1]/'data/user-hit-rate-scope-20260912.json').read_text(encoding='utf-8'))['recordedUseMissPolicy'].get('enabled') is True
            winner=ordered_winner_match_end(inputs.get('gameTerminals'),inputs['gaps']) if enabled else None
            reasons=[];pp=projectiles[i];left=command_order(s);right=command_order(winner) if winner else None
            raw=inputs.get('allProjectileSpawns')
            if not enabled or r.get('reason')!='open-final-cast':reasons.append('not-user-policy-open-final')
            if raw is None or any(g.get('count',0) for g in inputs['gaps']):reasons.append('original-stream-completeness-unavailable')
            if left is None or right is None or left>=right or s['tick']>winner['tick']:reasons.append('actual-winner-unavailable')
            def after(e):
                at=command_order(e);tick=e.get('tick')
                if at is None and e.get('objectId') in objects:
                    births=objects[e['objectId']]
                    if len(births)==1 and births[0].get('tick')==tick:at=command_order(births[0])
                return at is None or left is None or type(tick) is not int or tick>=s['tick'] or at>=left
            if len(pp)!=1:reasons.append('unique-drop-unavailable')
            arrival=None;p=pp[0] if len(pp)==1 else None
            if p:
                oid=p['projectileObjectId'];po=command_order(p)
                rr=[x for x in raw or [] if x.get('projectileObjectId')==oid]
                tt=[t for t in terminals if t.get('objectId')==oid]
                if len(rr)!=1 or rr[0].get('ownerObjectId')!=player or rr[0].get('projectileCode')!=108121 or rr[0].get('tick')!=p['tick'] or command_order(rr[0])!=po:reasons.append('raw-drop-identity-unavailable')
                if len(tt)==1 and tt[0].get('event')=='CmdProjectileArrived' and tt[0].get('isCollision') is False:arrival=tt[0]
                if arrival is None or command_order(arrival) is None or po is None or left is None or right is None or not left<po<command_order(arrival)<right or not s['tick']<=p['tick']<=arrival['tick']<=winner['tick']:reasons.append('exact-noncollision-arrival-unavailable')
                if any(c.get('projectileObjectId')==oid for c in collisions):reasons.append('recorded-drop-contact')
                if any(after(x) and x.get('projectileObjectId')!=oid and x.get('projectileCode') in {108121,108122} and (x.get('ownerObjectId')==player or x.get('ownerObjectId') not in teams) for x in raw or []):reasons.append('later-unassigned-projectile')
            if any(after(b) and b.get('summonCode')==1581 and (b.get('ownerObjectId')==player or b.get('ownerObjectId') not in teams) for b in inputs['summons']):reasons.append('later-block-not-excluded')
            if any(after(a) and a.get('sourceObjectId')==player and a.get('skillIdCode') in (1174,1175) and a.get('actionNo')==22 for a in actions):reasons.append('later-landing-not-excluded')
            if any(after(d) and d.get('effectCode') in (1081201,1081202) and (d.get('attackerObjectId')==player or d.get('attackerObjectId') not in teams) for d in damages):reasons.append('later-Q-damage-not-excluded')
            proof=dict(startTick=s['tick'],startOrder=left,dropProjectile=p,arrival=arrival,winnerEnd=winner,parentFinishInvented=False,phaseExecutionObserved=False)
            if reasons:
                winner_blocked.append(dict(proof,reasons=sorted(set(reasons))));unknown['open-parent-Q']+=1;continue
            winner_evidence.append(proof);contacts.append(set());ticks.append(s['tick']);details.append([]);proofs.append(proof);phase_unknown.append({})
            phases.append({name:dict(executed=False,phaseExecutionObserved=False,hitCount=0,distinctEnemyCount=0,firstHitTick=None) for name in ('landing','block-path')})
            continue
        pp=projectiles[i]
        if len(pp)!=1:
            unknown['missing-unique-parent-drop']+=1;continue
        p=pp[0];oid=p['projectileObjectId'];end=removals.get(oid)
        if end is None or end['tick']<p['tick']:
            unknown['missing-ordered-drop-removal']+=1;continue
        previous=[t for key,t in removals.items() if key!=oid and t['tick']==end['tick'] and command_order(t)<command_order(end)]
        lower=max((command_order(t) for t in previous),default=(-1,-1))
        landing=[a for a in aa if a['tick']==end['tick'] and lower<command_order(a)<command_order(end)]
        bb=[b for b in blocks if b['tick']==end['tick'] and lower<command_order(b)<command_order(end)]
        if len(landing)!=1 or landing[0]['skillIdCode']!=s['skillIdCode'] or len(bb)!=1:
            unknown['missing-unique-synchronous-drop-and-block']+=1;continue
        a=landing[0];b=bb[0];bid=b['objectId']
        if not command_order(a)<command_order(b) or bid in assigned:
            unknown['invalid-block-creation-order']+=1;continue
        assigned.add(bid)
        dd=[d for d in damages if d.get('attackerObjectId')==player and d['tick']==a['tick'] and d.get('effectCode')==1081201]
        if any(command_order(d) is None for d in dd):unknown['missing-ordered-landing-damage']+=1;continue
        dd=[d for d in dd if command_order(a)<command_order(d)<command_order(b)]
        if any(d.get('damageType')!=2 or type(d.get('damageIsNull')) is not bool for d in dd):
            unknown['invalid-landing-damage-wire']+=1;continue
        ds=[dict(hitTick=d['tick'],targetObjectId=d['targetObjectId'],projectileObjectId=oid,phase='landing')
            for d in dd if d['targetObjectId'] in teams and teams[d['targetObjectId']]!=teams[player]]
        block_end=ends.get(bid,{})
        soft_missing=(not any(t['objectId']==bid for t in terminals) and bool(ds)
            and not any(c.get('sourceObjectId')==bid for c in children)
            and not any(g.get('count',0) for g in inputs['gaps']))
        if (not block_end.get('complete') or block_end['endTick']<b['tick']) and not soft_missing:
            unknown['missing-actual-block-end']+=1;continue
        end_tick=block_end.get('endTick')
        close_method='final-destruction'
        if not soft_missing and not any(t['objectId']==bid and t['event']=='CmdDestroy' for t in terminals):
            # DelayStart alone does not close a servant's skill execution.
            # NiahSummonActive1.Finish synchronously calls DestroySelf, which
            # sets characterDeadReason before removal. Unused blocks instead
            # need a real death packet. W's selection predicate rejects that
            # dead state. Already launched projectiles remain independently open.
            delays=[t for t in terminals if t['objectId']==bid and t['event']=='CmdDestroyDelayStart' and t['tick']==end_tick]
            orders={command_order(t) for t in delays}
            if len(orders)!=1 or None in orders:
                unknown['missing-ordered-block-removal']+=1;continue
            removal_order=next(iter(orders))
            ff=[f for f in finishes if f.get('playerObjectId')==bid and f.get('skillIdCode')==1184]
            child_closed=(len(ff)==1 and ff[0]['tick']==end_tick and command_order(ff[0]) is not None
                          and command_order(ff[0])<removal_order)
            deaths=inputs.get('deaths')
            death_closed=(deaths is not None and not any(g.get('count',0) and g.get('packetName')=='CmdDead' for g in inputs['gaps'])
                          and any(d.get('event')=='CmdDead' and d.get('deadObjectId')==bid and d['tick']==end_tick
                                  and d.get('isDyingBlockDead') is False for d in deaths))
            if not (child_closed or death_closed):
                unknown['block-removal-without-execution-end-or-death']+=1;continue
            if any(command_order(c) is None or command_order(c)>=removal_order for c in children if c['sourceObjectId']==bid):
                unknown['child-start-after-block-removal']+=1;continue
            close_method='child-finish-destroys-block' if child_closed else 'actual-block-death'
        if soft_missing:close_method='unrecorded-block-end-positive-landing'
        block_user_miss=False
        if soft_missing:
            import json
            from pathlib import Path
            from .skill_ordered_match_end import ordered_winner_match_end
            enabled=json.loads((Path(__file__).resolve().parents[1]/'data/user-hit-rate-scope-20260912.json').read_text(encoding='utf-8'))['recordedUseMissPolicy'].get('enabled') is True
            winner=ordered_winner_match_end(inputs.get('gameTerminals'),inputs['gaps']) if enabled else None
            raw=inputs.get('allProjectileSpawns');bo=command_order(b);wo=command_order(winner) if winner else None;why=[];other_shots=[]
            if not enabled or raw is None or any(g.get('count',0) for g in inputs['gaps']):why.append('original-stream-or-policy-unavailable')
            if wo is None or bo is None or bo>=wo or b['tick']>winner['tick']:why.append('actual-winner-after-block-unavailable')
            if any(c.get('sourceObjectId')==bid for c in children) or any(f.get('playerObjectId')==bid for f in finishes):why.append('block-child-execution-present')
            if any(x.get('sourceObjectId')==bid or any(t.get('targetObjectId')==bid for t in x.get('targets',[])) for x in actions):why.append('block-referenced-by-action')
            for shot in raw or []:
                if shot.get('projectileCode')!=108122:continue
                so=command_order(shot);tick=shot.get('tick')
                if type(tick) is int and tick<b['tick'] and so is not None and bo is not None and so<bo:continue
                if so is None or bo is None or type(tick) is not int or ((tick<b['tick']) != (so<bo)):
                    why.append('projectile-clock-conflict');continue
                owner=shot.get('ownerObjectId')
                if owner in teams and owner!=player:continue
                candidates=[e for pairs in by_block.values() for e,_ in pairs if e.get('projectileObjectId')==shot.get('projectileObjectId')]
                if (owner!=player or len(candidates)!=1 or candidates[0].get('sourceObjectId')==bid
                        or so is None or candidates[0].get('projectileSpawnOrder')!=shot.get('wireOrder')
                        or not any(c.get('sourceObjectId')==candidates[0].get('sourceObjectId') and c.get('wireOrder')==candidates[0].get('childStartOrder') for c in children)):
                    why.append('later-projectile-without-exclusive-other-block');continue
                other_shots.append(dict(projectileObjectId=shot['projectileObjectId'],sourceObjectId=candidates[0]['sourceObjectId'],projectileSpawnOrder=shot['wireOrder'],childStartOrder=candidates[0]['childStartOrder']))
            bp=dict(startTick=s['tick'],blockObjectId=bid,blockSpawnOrder=b['wireOrder'],winnerEnd=winner,otherBlockProjectileEvidence=other_shots,phaseExecutionObserved=False,blockTerminalInvented=False)
            if why:block_winner_blocked.append(dict(bp,reasons=sorted(set(why))))
            else:block_winner_evidence.append(bp);block_user_miss=True
        incomplete_children=(soft_missing and not block_user_miss) or len(by_block[bid])!=expected[bid]
        partial_child_positive=any(e.get('positiveBeforeRecordedEnd') for e,_ in by_block[bid])
        # Exact synchronous landing is independent of a later unfinished
        # child projectile. Do not recover absent landing evidence or claim
        # that the child's target set/negative outcome is complete.
        if incomplete_children and not ds:
            unknown['incomplete-block-child-executions']+=1;continue
        for evidence,child_details in by_block[bid]:
            ds.extend({**d,'phase':'block-path','blockObjectId':bid} for d in child_details)
        first={}
        for d in sorted(ds,key=lambda d:d['hitTick']):first.setdefault((d['phase'],d['targetObjectId']),d)
        ds=list(first.values());ps={}
        for name,executed in [('landing',True),('block-path',any(e.get('launched',True) for e,_ in by_block[bid]))]:
            if incomplete_children and name=='block-path':continue
            part=[d for d in ds if d['phase']==name];ps[name]=dict(executed=executed,hitCount=int(bool(part)),
                distinctEnemyCount=len(part),firstHitTick=min((d['hitTick'] for d in part),default=None))
        if block_user_miss:ps['block-path']['phaseExecutionObserved']=False
        contacts.append({(d['hitTick'],d['targetObjectId']) for d in ds});ticks.append(s['tick']);details.append(ds);phases.append(ps)
        phase_unknown.append({'block-path':'missing-actual-block-end' if soft_missing else 'incomplete-block-child-executions'} if incomplete_children else {})
        if incomplete_children or partial_child_positive:incomplete_positive.append(len(contacts)-1)
        if incomplete_children:block_unknown_ticks.append(s['tick'])
        elif expected[bid] or block_user_miss:
            block_contacts.append({(d['hitTick'],d['targetObjectId']) for d in ds if d['phase']=='block-path'})
            block_ticks.append(s['tick'])
        proofs.append(dict(dropProjectileObjectId=oid,blockObjectId=bid,landingActionOrder=a['wireOrder'],
                           blockSpawnOrder=b['wireOrder'],dropRemovalOrder=end['wireOrder'],childUseCount=len(by_block[bid]),
                           blockEndTick=end_tick,blockEndMethod=close_method))
    diag.update(userPolicyWinnerMissEvidence=winner_evidence,userPolicyWinnerBlockedEvidence=winner_blocked,unresolvedCombatCastCount=sum(unknown.values()),unresolvedCastReasons=dict(unknown),perUseCompletenessTracked=True,incompleteUsesCountedAsMisses=False)
    if diag['observedCombatCastCount'] and not contacts:return fail('no complete normal/R parent Q outcomes')
    result=_result(spec,contacts,'static-drop-callback-created-block-and-child-projectile-lineage',cast_ticks=ticks)
    result.update(diag,contactDetailsByAttempt=details,phaseOutcomesByAttempt=phases,parentBlockEvidenceByAttempt=proofs,
                  evidenceReview='deliverables/niah-parent-q-static-proof-v2.json',fixedDurationWindowUsed=False)
    if incomplete_positive or block_winner_evidence:
        from .skill_lifecycle_result_policy import finalize_lifecycle_result
        result=finalize_lifecycle_result(result,list(range(len(contacts))),{},incomplete_positive=incomplete_positive)
        # Preserve parent diagnostics; lifecycle helper's selected set above
        # contains classified outcomes only, not every observed parent.
        result.update(diag,phaseUnknownReasonsByAttempt=phase_unknown,
            incompleteChildPositiveCastTicks=[ticks[i] for i in incomplete_positive],
            phaseOutcomesComplete=False,fullRequestedMetricComplete=False)
        block=_result({**spec,'metricId':spec['metricId']+':block-path','label':'블록 경로'},block_contacts,
            'complete-child-projectile-contact-per-parent-cast',cast_ticks=block_ticks)
        block.update(observedCombatCastCount=len(block_contacts)+len(block_unknown_ticks),
            unresolvedCombatCastCount=len(block_unknown_ticks),unresolvedCastTicks=block_unknown_ticks,
            unresolvedCastReasons=dict(Counter(v['block-path'] for v in phase_unknown if v)),
            incompleteUsesCountedAsMisses=False,perUseCompletenessTracked=True,
            fullRequestedMetricComplete=False,wholeCombatHitRate=None)
        if not block_contacts:block.update(status='unresolved-evidence',attemptCount=None,hitCount=None,hitRate=None,
            reason='incomplete-block-child-executions')
        if any(e.get('positiveBeforeRecordedEnd') for pairs in by_block.values() for e,_ in pairs):
            block.update(targetCountsAreLowerBounds=True,completeTargetCountsAvailable=False)
        if block_winner_evidence:
            from .skill_development_cancellation import annotate_provisional
            annotate_provisional(block,'Explicit user parent-use denominator: unlaunched owned block at actual winner is a phase miss, not an observed child execution.')
            block.update(userPolicyMissEvidence=block_winner_evidence,verifiedCombatCastCount=0,verifiedCompletionCredit=False,fullRequestedMetricComplete=False)
        result['phaseMetrics']={'blockPath':block}
    result.update(userPolicyBlockPathMissEvidence=block_winner_evidence,userPolicyBlockPathBlockedEvidence=block_winner_blocked)
    if winner_evidence or block_winner_evidence:
        from .skill_development_cancellation import annotate_provisional
        annotate_provisional(result,'Explicit user attempt policy for the unexecuted Q phase before actual winner; recorded landing hits remain intact and no callback, child execution or finish is invented.')
        result.update(verifiedCombatCastCount=0,verifiedCompletionCredit=False,fullRequestedMetricComplete=False)
    return result
