"""Chloe E1 and owned Nina E2 dash contact, bounded by ordered execution.

Each dash checks collisions synchronously and on subsequent frames until its
coroutine ends or the skill is cancelled. A late cancellation preserves damage
already emitted. Nina E skips its attack coroutine while Nina W is playing.
"""
from collections import defaultdict, Counter

try:
    from .requested_skill_hit_rates import _result, _unavailable
    from .skill_partial_cast_lifetimes import ordered_cast_records
    from .skill_wire_order import command_order as order
    from .skill_summon_ownership import live_summon_owner_resolver
    from .skill_action_stage_evidence import load_exact_skill_ids
    from .skill_attempt_timing import exact_outcome
except ImportError:
    from requested_skill_hit_rates import _result, _unavailable
    from skill_partial_cast_lifetimes import ordered_cast_records
    from skill_wire_order import command_order as order
    from skill_summon_ownership import live_summon_owner_resolver
    from skill_action_stage_evidence import load_exact_skill_ids
    from skill_attempt_timing import exact_outcome


def chloe_e_execution_metric(spec, starts, finishes, player, teams, intervals, catalog, inputs):
    group = spec['skillGroup']
    mode = {1040400: 'chloe-dash', 1040410: 'nina-request', 1040450: 'nina-dash'}.get(group)
    combat = lambda s: any(a <= s['tick'] < b for a, b in intervals)
    parent_group = 1040400 if mode == 'chloe-dash' else 1040410
    roots = [s for s in starts if s['playerObjectId'] == player and s['skillGroup'] == parent_group]
    selected_roots = [s for s in roots if s['skillGroup'] == group]
    diag = dict(observedCastCount=len(selected_roots), observedCombatCastCount=sum(map(combat, selected_roots)))
    fail = lambda why: {**_unavailable(spec, why), **diag}
    if mode is None or spec['characterCode'] != 40 or spec['mode'] != 'any' or spec['unit'] != 'skill-cast':
        return fail('unsupported Chloe E scope')
    if any(inputs.get(k) is None for k in ('nonPlayerSkillStarts', 'summons', 'terminals', 'damages', 'movement', 'gaps')):
        return fail('missing ordered Chloe E execution evidence')
    ids = load_exact_skill_ids()
    identities = ((1040400, 'ChloeActive3_1', 563), (1040410, 'ChloeActive3_2', 564), (1040450, 'NinaActive3', 573))
    if any(ids.get(name) != wire or catalog['skillGroups'].get(str(g), {}).get('skillId') != name for g, name, wire in identities) or ids.get('NinaActive2') != 572:
        return fail('pinned Chloe E wire/game data identities differ')
    if any(s['skillIdCode'] != {1040400:563,1040410:564}[s['skillGroup']] or s['skillCode'] not in range(s['skillGroup']+1,s['skillGroup']+6) for s in roots):
        return fail('parent E wire/code mismatch')
    required = {'CmdStartSkill','CmdFinishSkill','CmdDamage','CmdSpawn','CmdSpawnBatch','CmdDestroy','CmdMoveStraight'}
    if any(g.get('count',0) and (g.get('packetName') in required or str(g.get('packetName','')).startswith('SummonSnapshot:')) for g in inputs['gaps']):
        return fail('incomplete E execution stream')
    parents, why = ordered_cast_records(roots, finishes, player, allow_same_tick_finishes=True)
    if why:
        return fail(why)
    resolve = live_summon_owner_resolver(inputs['summons'], inputs['terminals'], set(teams))
    child_starts = defaultdict(list)
    for s in inputs['nonPlayerSkillStarts']:
        if mode == 'chloe-dash':
            break
        if s['skillIdCode'] != 573 and s['skillCode'] != 1040451:
            continue
        owner, path, why = resolve(s['sourceObjectId'], s['tick'])
        if why:
            return fail('Nina E owner: '+why)
        if owner != player:
            continue
        if path != [1191] or s['skillIdCode'] != 573 or s['skillCode'] != 1040451:
            return fail('Nina E actor/code mismatch')
        child_starts[s['sourceObjectId']].append({**s,'playerObjectId':s['sourceObjectId'],'skillGroup':1040450})
    children, w_records = [], {}
    for actor, ss in child_starts.items():
        rs, why = ordered_cast_records(ss, finishes, actor, allow_same_tick_finishes=True)
        if why:
            return fail('Nina E lifetime: '+why)
        children.extend(rs)
        ws = [{**s,'playerObjectId':actor} for s in inputs['nonPlayerSkillStarts'] if s['sourceObjectId']==actor and s['skillIdCode']==572]
        wrs, why = ordered_cast_records(ws, finishes, actor, allow_same_tick_finishes=True)
        if why:
            return fail('Nina W suppression lifetime: '+why)
        w_records[actor] = wrs
    children.sort(key=lambda r:order(r['start']))
    if mode == 'nina-dash':
        diag = dict(observedCastCount=len(children), observedCombatCastCount=sum(combat(r['start']) for r in children))
    child_parent, parent_child = {}, {}
    for i, r in enumerate(children):
        candidates = [j for j,p in enumerate(parents) if p['start']['skillGroup']==1040410 and p['finish'] is not None and order(p['start'])<order(r['start'])<order(p['finish'])]
        if len(candidates)!=1 or candidates[0] in parent_child:
            return fail('Nina E lacks one exact parent request')
        child_parent[i]=candidates[0]; parent_child[candidates[0]]=i
    dash_records = [r for r in parents if r['start']['skillGroup']==1040400] + children
    contacts, damage_rows, moves, unknown, suppressed = {}, {}, {}, {}, set()
    for i,r in enumerate(dash_records):
        s,end=r['start'],r['finish']; actor=s['playerObjectId']; contacts[i]=set();damage_rows[i]=[]
        if not r['complete']:
            unknown[i]='dash lifetime remains open';continue
        ms=[m for m in inputs['movement'] if m['objectId']==actor and m['event']=='CmdMoveStraight' and m['tick']==s['tick']]
        if any(order(m) is None for m in ms):
            unknown[i]='dash movement lacks exact command order';continue
        ms=[m for m in ms if order(s)<order(m)<order(end)]
        if len(ms)!=1 or ms[0].get('ease')!=1:
            unknown[i]='dash execution does not have one exact initial movement';continue
        moves[i]=ms[0]
        if s['skillGroup']==1040450:
            # IsPlayingScript(0x23c == 572), evaluated just after moving Nina.
            # Any W transition later in this same frame is conservatively
            # ambiguous because the branch has no dedicated wire marker.
            wrs=w_records[actor]
            if any(any(e is not None and e['tick']==ms[0]['tick'] and order(e)>order(ms[0]) for e in (w['start'],w['finish'])) for w in wrs):
                unknown[i]='Nina W transition overlaps E suppression check';continue
            if any(order(w['start'])<order(ms[0]) and (w['finish'] is None or order(ms[0])<order(w['finish'])) for w in wrs):
                suppressed.add(i)
        if type(end['reason']) is not int or end['reason'] not in (0,2,3):
            unknown[i]='unreviewed E termination reason'
        elif end['tick']==s['tick']:
            unknown[i]='same-frame E cancellation may precede attack coroutine'
    for d in inputs['damages']:
        fx=d.get('effectCode')
        if fx not in (1040031,1040032):
            continue
        if (fx==1040031) != (mode=='chloe-dash'):
            continue
        actor=d['attackerObjectId']
        if fx==1040031:
            if actor!=player:continue
        else:
            owner,path,why=resolve(actor,d['tick'])
            if why:return fail('Nina E damage owner: '+why)
            if owner!=player:continue
            if path!=[1191]:return fail('Nina E damage from a different summon')
        candidates=[i for i,r in enumerate(dash_records) if r['start']['playerObjectId']==actor and r['start']['skillGroup']==(1040400 if fx==1040031 else 1040450) and order(d) is not None and order(r['start'])<order(d) and (r['finish'] is None or order(d)<order(r['finish']))]
        if len(candidates)!=1:return fail('E damage outside a unique ordered dash')
        i=candidates[0];damage_rows[i].append(d)
        if i in suppressed:return fail('damage contradicts Nina W attack suppression')
        if i not in moves or order(d)<=order(moves[i]) or d.get('damageType')!=2 or type(d.get('damageIsNull')) is not bool:
            unknown[i]='E contact lacks exact movement/type/discriminator';continue
        if unknown.get(i)=='same-frame E cancellation may precede attack coroutine':
            unknown.pop(i)
        if d['targetObjectId'] in teams and teams[d['targetObjectId']]!=teams[player]:contacts[i].add((d['tick'],d['targetObjectId']))
    # The native damagedIds set permits only one contact per target per dash.
    for i,ds in damage_rows.items():
        if len({d['targetObjectId'] for d in ds})!=len(ds):unknown[i]='duplicate E contact contradicts per-target collision guard'
    offset=len(dash_records)-len(children)
    uses=[];nonexecuted=[];source_unknown=[]
    source_records=children if mode=='nina-dash' else [r for r in parents if r['start']['skillGroup']==group]
    for r in source_records:
        if mode=='nina-request':
            j=parents.index(r)
            if not r['complete']:
                source_unknown.append((r,'parent E request remains open'));continue
            if j not in parent_child:
                if r['finish']['reason']==0:nonexecuted.append(r)
                else:source_unknown.append((r,'parent E request has no proved child execution'))
                continue
            i=offset+parent_child[j]
        else:i=dash_records.index(r)
        if i in unknown:source_unknown.append((r,unknown[i]))
        elif i in suppressed:nonexecuted.append(r)
        else:uses.append((r,i))
    selected=[(r,i) for r,i in uses if combat(r['start'])]
    row=_result(spec,[contacts[i] for r,i in selected],'static-Chloe-E-ordered-dash-collision-coroutine',cast_ticks=[r['start']['tick'] for r,i in selected])
    reasons=Counter(why for r,why in source_unknown if combat(r['start']))
    row.update(diag,outcomes=[exact_outcome(r['start']['tick'],moves[i]['tick'],contacts[i]) for r,i in selected],
        unresolvedCombatCastCount=sum(reasons.values()),unresolvedCastReasons=dict(reasons),
        unresolvedNonCombatCastCount=sum(not combat(r['start']) for r,why in source_unknown),
        nonExecutedCastCount=sum(combat(r['start']) for r in nonexecuted),nonExecutedAllCastCount=len(nonexecuted),
        nonExecutionEvidence=nonexecuted,phase=mode,
        postExecutionCancelledCount=sum(dash_records[i]['finish']['reason']!=0 for r,i in selected),
        contactDetailsByAttempt=[[dict(hitTick=t,targetObjectId=who,attackerObjectId=dash_records[i]['start']['playerObjectId']) for t,who in sorted(contacts[i])] for r,i in selected],
        executionEvidenceByAttempt=[dict(request=r,dash=dash_records[i],movement=moves[i],damageCommands=damage_rows[i]) for r,i in selected],
        perUseCompletenessTracked=True,incompleteUsesCountedAsMisses=False,damageAmountInferred=False,
        denominatorMeaning='Actual E collision-capable dash executions; child-request and child cast times remain separate',
        evidenceReview='deliverables/chloe-e-execution-static-proof-v1.json',fixedDurationWindowUsed=False)
    return row
