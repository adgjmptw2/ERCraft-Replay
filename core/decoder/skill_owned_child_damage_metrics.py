"""Independent owned child uses: exact owner, action and valued effect.

ArdaSkillAction 32/33/45 are Skill02_Damage/Skill02_DecDamage/Skill03_Damage
in the versioned metadata. Action counts are not cast denominators. The nullable damage amount is a wire optimization; a missing
nullable discriminator remains unknown. Arda's restored damage-parameter
branches prove dedicated effect application independently of the optional
display amount. Arda Earth emits its action before its synchronous damage loop;
Stone emits action 45 after that loop. Exact command order separates concurrent
same-owner summons. Other routes retain their prior ambiguity guards.
"""
from collections import defaultdict, Counter
try:
    from .requested_skill_hit_rates import _result, _unavailable
    from .skill_action_stage_evidence import load_exact_skill_ids, ORDINARY_ACTIONS
    from .skill_summon_ownership import live_summon_owner_resolver
    from .skill_partial_cast_lifetimes import ordered_cast_records
    from .skill_wire_order import command_order
except ImportError:
    from requested_skill_hit_rates import _result, _unavailable
    from skill_action_stage_evidence import load_exact_skill_ids, ORDINARY_ACTIONS
    from skill_summon_ownership import live_summon_owner_resolver
    from skill_partial_cast_lifetimes import ordered_cast_records
    from skill_wire_order import command_order

ROUTES = {
    1081710: ('NiahSummonActive2_1',1583,'Object_FX_BI_Niah_Skill02_Area',{211:1081301}),
    1081720: ('NiahSummonActive2_2',1583,'Object_FX_BI_Niah_Skill02_Area',{212:1081302}),
    1066600: ('ArdaSummonActive2',1454,'Arda_Skill02_Earth',{32:1066301,33:1066302}),
    1066610: ('ArdaSummonActive2Reinforce',1455,'Arda_Skill02_Reinforce_Earth',{32:1066301,33:1066302}),
    1066700: ('ArdaSummonActive3',1457,'Arda_Skill03_Stone',{45:1066401}),
    1066710: ('ArdaSummonActive3Reinforce',1458,'Arda_Skill03_Reinforce_Stone',{45:1066401}),
    1015700: ('SisselaWilsonActive1',1030,'Wilson',{1:1015101,2:1015102}),
    1057330: ('MartinaActive2ReinforceSummonAttack',1351,'Martina_Skill02_SignTransmitter',{1:1057302}),
}
EFFECTS = {1081301:'FX_BI_Niah_Skill02_Hit',1081302:'FX_BI_Niah_Skill02_Hit',1066301:'FX_BI_Arda_Skill02_Hit',1066302:'FX_BI_Arda_Skill02_Aftershock_Hit',1066401:'FX_BI_Arda_Skill03_Hit',
           1015101:'FX_BI_Sissela_Skill01_Hit',1015102:'FX_BI_Sissela_Skill01_Hit2',
           1057302:'FX_BI_Martina_Skill02_Hit_Reinforce'}


def owned_child_damage_metric(spec, nonplayer_starts, finishes, summons, terminals,
                              actions, damages, player, teams, intervals, catalog,
                              skill_rows, summon_rows, effect_rows, evidence_gaps,
                              skill_ids=None, *, state_scripts=None,state_groups=None,use_evidence=None):
    diag=dict(observedOwnedChildCastCount=0,observedCombatChildCastCount=0,
              parentUsesInferred=False,childCastsCountedAsIndependentUses=True)
    def fail(reason):return {**_unavailable(spec,reason),**diag}
    group=spec['skillGroup'];cfg=ROUTES.get(group)
    static_arda=group in {1066600,1066610,1066700,1066710}
    static_niah=group in {1081710,1081720}
    static_ordered=static_arda or static_niah
    stone=group in {1066700,1066710}
    character={1015700:15,1057330:57,1081710:81,1081720:81}.get(group,66)
    state_channel=group==1057330
    if not cfg or (spec.get('characterCode'),spec.get('mode'),spec.get('unit'))!=(character,'any','skill-cast'):
        return fail('unsupported owned child damage metric')
    name,summon_code,prefab,action_effect=cfg
    definition=catalog['skillGroups'].get(str(group),{})
    sr=[r for r in summon_rows if r.get('code')==summon_code]
    if (definition.get('skillId')!=name or definition.get('characterCode')!=character or len(sr)!=1 or
            sr[0].get('prefabPath')!=prefab or sr[0].get('useAttackerType')!=('None' if static_niah else 'Owner')):
        return fail('exact child skill/summon/Owner attacker definition mismatch')
    for code in set(action_effect.values()):
        fx=[r for r in effect_rows if r.get('code')==code]
        if len(fx)!=1 or fx[0].get('effectPrefabName')!=EFFECTS[code]:return fail('exact child effect identity mismatch')
    required={'CmdStartSkill','CmdFinishSkill','CmdSpawn','CmdDestroy','CmdDamage',
              'CmdPlaySkillAction','CmdPlaySkillActionWithTargets','SummonSnapshot:11'}
    if state_channel:
        required|={'CmdStartStateSkill','CmdFinishStateSkill','CmdPlayStateSkillAction'}
        definitions=[r for r in state_groups or [] if r.get('group')==1057310]
        if state_scripts is None or len(definitions)!=1 or definitions[0].get('skillId')!=name:
            return fail('explicit state-group handler identity unavailable')
    if (not state_channel and nonplayer_starts is None) or evidence_gaps is None or any(g.get('count',0) and g.get('packetName') in required for g in evidence_gaps):
        return fail('required owned child stream completeness unavailable')
    ids=load_exact_skill_ids() if skill_ids is None else skill_ids
    wire=ids.get(name);code_groups={r['code']:r['group'] for r in skill_rows}
    if type(wire) is not int:return fail('exact child wire enum missing')
    resolve=live_summon_owner_resolver(summons,terminals,set(teams))
    by_actor=defaultdict(list)
    input_starts=([s for s in state_scripts if s.get('event')=='CmdStartStateSkill' and s.get('stateGroup')==1057310]
                  if state_channel else nonplayer_starts)
    for s in input_starts:
        if state_channel and s['skillIdCode']!=wire:return fail('state-group and wire handler disagree')
        if s['skillIdCode']!=wire and code_groups.get(s['skillCode'])!=group:continue
        owner,path,reason=resolve(s['sourceObjectId'],s['tick'])
        if reason:return fail('child start ownership unavailable: '+reason)
        if owner!=player:continue
        if path!=[summon_code] or s['skillIdCode']!=wire or (not state_channel and code_groups.get(s['skillCode'])!=group):
            return fail('child wire, Skill.code and summon identity disagree')
        by_actor[s['sourceObjectId']].append({**s,'playerObjectId':s['sourceObjectId'],'skillGroup':group})
    diag['observedOwnedChildCastCount']=sum(map(len,by_actor.values()))
    diag['observedCombatChildCastCount']=sum(any(l<=s['tick']<r for l,r in intervals) for ss in by_actor.values() for s in ss)
    records=[]
    for actor,starts in by_actor.items():
        ends=finishes
        if state_channel:
            casters={s.get('casterObjectId') for s in starts}
            if len(casters)!=1:return fail('multiple state casters require separate child streams')
            ends=[{**s,'playerObjectId':s['sourceObjectId']} for s in state_scripts
                  if s.get('event')=='CmdFinishStateSkill' and s.get('stateGroup')==1057310 and
                  s.get('casterObjectId') in casters and s.get('sourceObjectId')==actor]
        local,reason=ordered_cast_records(starts,ends,actor,allow_same_tick_finishes=True)
        if reason:
            if state_channel and reason=='종료 전에 같은 스킬의 다음 시전이 시작됨':
                # A handler may be started again on the same summon. Without
                # a proven per-execution end, all starts on that exact actor
                # remain unknown. Keep them as attribution candidates; another
                # independently owned summon need not lose its closed uses.
                local=[dict(start=s,finish=None,complete=False,
                            reason='ambiguous-state-handler-restarts') for s in starts]
            else:return fail('child lifetime: '+reason)
        records.extend(local)
    records.sort(key=lambda r:command_order(r['start']))
    if not records:return fail('no explicit owned child starts')
    unknown=defaultdict(set);contacts=[set() for _ in records];use_actions=defaultdict(list)
    contact_packets=[[] for _ in records]
    def within(record,event):
        s,f=record['start'],record['finish']
        if f is None:return event['tick']>=s['tick']
        if not s['tick']<=event['tick']<=f['tick']:return False
        at=command_order(event)
        return at is None or command_order(s)<=at<=command_order(f)
    for i,record in enumerate(records):
        end=record['finish']
        if static_niah and end is not None and end.get('reason')!=0:
            unknown[i].add('cancelled-child-execution')
        if not record['complete'] or end is None:
            unknown[i].add('ambiguous-state-handler-restarts' if record.get('reason')=='ambiguous-state-handler-restarts' else 'incomplete-child-lifetime');continue
        owner,path,reason=resolve(record['start']['sourceObjectId'],end['tick'])
        if reason or owner!=player or path!=[summon_code] or end.get('reason') not in set(range(15))|{16,17}:
            unknown[i].add('unverified-gameplay-finish-or-owner')
    own_action_identities=defaultdict(set)
    boundaries=defaultdict(list);bad_boundary_ticks=set()
    boundary_routes={ids[n]: (g,c,e) for g,(n,c,_,e) in ROUTES.items()
                     if g in ({1081710,1081720} if static_niah else {1066700,1066710} if stone else {1066600,1066610}) and n in ids} if static_ordered else {}
    selected_actions=[]
    for a in actions:
        statuses=ORDINARY_ACTIONS|{'decoded-exact-CmdPlayStateSkillAction'} if state_channel else ORDINARY_ACTIONS
        if a.get('wireStatus') not in statuses:continue
        owner,path,reason=resolve(a['sourceObjectId'],a['tick'])
        if reason or owner!=player:continue
        own_action_identities[a['tick']].add((a['sourceObjectId'],a['skillIdCode']))
        if static_ordered and a['skillIdCode'] in boundary_routes:
            bg,bc,be=boundary_routes[a['skillIdCode']]
            if a['actionNo'] in be:
                if path!=[bc] or command_order(a) is None:bad_boundary_ticks.add(a['tick'])
                else:boundaries[a['tick']].append((command_order(a),a,be[a['actionNo']]))
        if a['sourceObjectId'] in by_actor and a['skillIdCode']==wire and a['actionNo'] in action_effect:
            if state_channel and (a.get('wireStatus')!='decoded-exact-CmdPlayStateSkillAction' or a.get('stateGroup')!=1057310):
                return fail('child state action is in a different command channel')
            selected_actions.append(a)
    emission_uses=defaultdict(list)
    for a in selected_actions:
        candidates=[i for i,r in enumerate(records) if r['start']['sourceObjectId']==a['sourceObjectId'] and within(r,a)
                    and (not state_channel or r['start'].get('casterObjectId')==a.get('casterObjectId'))]
        if len(candidates)!=1:
            for i in candidates:unknown[i].add('action-overlaps-child-use-boundary')
            if not candidates:return fail('owned child damage action has no recorded use')
            continue
        i=candidates[0];use_actions[i].append(a)
        emission_uses[a['tick'],action_effect[a['actionNo']]].append(i)
        if not static_ordered and own_action_identities[a['tick']]!={(a['sourceObjectId'],wire)}:
            unknown[i].add('concurrent-other-owner-action')
    for tick,bs in boundaries.items():
        if len({b[0] for b in bs})!=len(bs):bad_boundary_ticks.add(tick)
        bs.sort(key=lambda b:b[0])
    if static_ordered:
        for i,aa in use_actions.items():
            if any(a['tick'] in bad_boundary_ticks for a in aa):unknown[i].add('missing-or-duplicate-static-emission-order')
    for i,r in enumerate(records):
        counts=Counter(a['actionNo'] for a in use_actions[i])
        if r['complete'] and (r['finish']['reason']==0 or state_channel) and not all(counts[n]>0 for n in action_effect):
            unknown[i].add('normal-finish-without-all-recorded-damage-phases')
        if len({(a['tick'],a['actionNo']) for a in use_actions[i]})!=len(use_actions[i]):
            unknown[i].add('duplicate-damage-action-without-distinct-order')
    for d in damages:
        target=d['targetObjectId'];fx=d.get('effectCode')
        if target not in teams or teams[target]==teams[player] or fx not in action_effect.values():continue
        if d['attackerObjectId'] in by_actor:
            for i,r in enumerate(records):
                if r['start']['sourceObjectId']==d['attackerObjectId'] and within(r,d):
                    unknown[i].add('child-damage-actor-disagrees-with-Owner-policy')
            continue
        if d['attackerObjectId']!=player:continue
        candidates=emission_uses[d['tick'],fx]
        if static_ordered:
            at=command_order(d);bs=boundaries.get(d['tick'],[])
            eligible=[b for b in bs if at is not None and (b[0]>at if stone else b[0]<at)]
            boundary=(eligible[0] if stone else eligible[-1]) if eligible and d['tick'] not in bad_boundary_ticks else None
            if boundary and boundary[2]==fx:
                action=boundary[1]
                # The neighboring boundary is a recorded synchronous producer,
                # not a nearest-time guess. Another child group stays separate.
                if action['skillIdCode']!=wire:continue
                candidates=[i for i,r in enumerate(records) if r['start']['sourceObjectId']==action['sourceObjectId'] and within(r,action)]
            else:candidates=[]
        valid=[i for i in candidates if within(records[i],d)]
        if len(candidates)!=1 or len(valid)!=1:
            for i,r in enumerate(records):
                if within(r,d):unknown[i].add('effect-without-one-exact-child-damage-action')
            continue
        i=valid[0]
        # The common server omits damage when it equals the HP delta.
        # Ownership/action/effect checks above establish the contact.
        if type(d.get('damageIsNull')) is bool:
            contacts[i].add((d['tick'],target))
            if contact_packets is not None:contact_packets[i].append(d)
        else:unknown[i].add('missing-damage-discriminator')
    preserved={};soft_lifetimes=set()
    for i,r in enumerate(records):
        actor=r['start']['sourceObjectId'];start_order=command_order(r['start'])
        if (not static_arda or unknown[i]!={'incomplete-child-lifetime'}
                or r['finish'] is not None or start_order is None
                or len(by_actor[actor])!=1
                or any(f.get('playerObjectId')==actor and f.get('skillIdCode')==wire for f in finishes)
                or any(t.get('objectId')==actor for t in terminals)):continue
        packets=contact_packets[i];aa=use_actions[i]
        if (any(command_order(e) is None or command_order(e)<=start_order
                               or e['tick']<r['start']['tick'] for e in [*packets,*aa])):continue
        if any(resolve(actor,e['tick'])!=(player,[summon_code],None) for e in [*packets,*aa]):continue
        soft_lifetimes.add(i)
        if contacts[i]:preserved[i]=set(unknown[i]);unknown[i].clear()
    combat=[i for i,r in enumerate(records) if any(l<=r['start']['tick']<rr for l,rr in intervals)]
    if use_evidence is not None:
        if not (static_arda or static_niah) or use_evidence:raise ValueError('reviewed static child use evidence requires an empty sink')
        use_evidence.extend(dict(start=r['start'],finish=r['finish'],contacts=set(contacts[i]),unknown=set(unknown[i]),
                                 damagePackets=contact_packets[i],actions=use_actions[i],
                                 incompletePositiveReasons=sorted(preserved.get(i,set())),softLifetimeIncomplete=i in soft_lifetimes) for i,r in enumerate(records))
    valid=[i for i in combat if not unknown[i]]
    reasons=Counter(reason for i in combat for reason in unknown[i])
    diag.update(exactChildLifetimeCount=sum(r['complete'] for r in records),
                unresolvedCombatCastCount=len(combat)-len(valid),unresolvedCastReasons=dict(reasons),
                observedOwnedDamageActionCount=len(selected_actions),verifiedCombatCastCount=len(valid),
                perUseCompletenessTracked=True,incompleteUsesCountedAsMisses=False,
                outcomeScope='independent child casts with complete damage actions and unambiguous valued enemy effects; other uses unknown')
    if static_ordered:
        if combat and not valid:return fail('no complete unambiguous static child uses')
    elif not any(contacts[i] for i in valid):return fail('no unambiguous enemy damage command in complete combat child uses')
    row=_result(spec,[contacts[i] for i in valid],'exact-owned-child-use-damage-action-and-valued-owner-effect',
                cast_ticks=[records[i]['start']['tick'] for i in valid])
    row.update(diag,denominatorMeaning='explicit child skill uses; repeated damage actions remain one use',
               exactSummonCode=summon_code,candidateEffectCodes=sorted(set(action_effect.values())))
    if not static_ordered:
        row.update(method='exact-owned-child-use-action-and-decoded-damage-command',
            damageAmountRequiredForContact=False,damageAmountInferred=False,
            evidenceReview='deliverables/cmd-damage-nullable-semantics-proof-v1.json',
            outcomeScope='independent child casts with complete actions and unambiguous enemy damage commands')
    if static_arda:
        row.update(method='static-Arda-owned-child-damage-action-and-exact-effect-application',
            damageAmountRequiredForContact=False,damageAmountInferred=False,
            emissionBoundary='following ordered action 45' if stone else 'preceding ordered action 32/33',
            outcomeScope='complete independently recorded child uses with dedicated enemy effect application; ambiguous uses excluded',
            evidenceReview='deliverables/arda-static-child-application-proof-v1.json')
    if static_niah:
        row.update(method='static-Niah-child-synchronous-action-and-explicit-custom-owner-damage',
            damageAmountRequiredForContact=False,damageAmountInferred=False,
            emissionBoundary='preceding ordered action 211/212',customCasterIsSummonOwner=True,
            summonDefaultAttackerPolicy='None',
            evidenceReview='deliverables/niah-w-child-static-proof-v1.json')
    recovered=[i for i in combat if i in preserved]
    if recovered:
        try:from .skill_development_cancellation import annotate_provisional
        except ImportError:from decoder.skill_development_cancellation import annotate_provisional
        row=annotate_provisional(row,'Exact owned child action/effect contacts prove a positive despite absent execution finish; target counts remain lower bounds.')
        row.update(targetCountsAreLowerBounds=True,completeTargetCountsAvailable=False,
            incompletePositiveUseEvidence=[dict(start=records[i]['start'],reasons=sorted(preserved[i]),
                contacts=sorted(contacts[i])) for i in recovered])
    return row
