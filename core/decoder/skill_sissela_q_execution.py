"""Wilson Q pass loop and stop burst, linked to the exact parent request."""
from collections import Counter, defaultdict
try:
    from .requested_skill_hit_rates import _result, _unavailable
    from .skill_partial_cast_lifetimes import ordered_cast_records
    from .skill_summon_ownership import live_summon_owner_resolver
    from .skill_wire_order import command_order as order
    from .skill_ordered_match_end import ordered_winner_match_end
    from .skill_attempt_timing import exact_outcome
    from .skill_action_stage_evidence import load_exact_skill_ids, ORDINARY_ACTIONS
except ImportError:
    from requested_skill_hit_rates import _result, _unavailable
    from skill_partial_cast_lifetimes import ordered_cast_records
    from skill_summon_ownership import live_summon_owner_resolver
    from skill_wire_order import command_order as order
    from skill_ordered_match_end import ordered_winner_match_end
    from skill_attempt_timing import exact_outcome
    from skill_action_stage_evidence import load_exact_skill_ids, ORDINARY_ACTIONS


def sissela_q_execution_metric(spec, starts, finishes, player, teams, intervals, catalog, inputs):
    parent_scope = spec['skillGroup'] == 1015200
    roots = [s for s in starts if s['playerObjectId'] == player and s['skillGroup'] == 1015200]
    in_combat = lambda t: any(a <= t < b for a, b in intervals)
    diag = dict(observedCastCount=len(roots) if parent_scope else 0,
                observedCombatCastCount=sum(in_combat(s['tick']) for s in roots) if parent_scope else 0)
    fail = lambda reason: {**_unavailable(spec, reason), **diag}
    if (spec['characterCode'], spec['mode'], spec['unit']) != (15, 'any', 'skill-cast') or spec['skillGroup'] not in (1015200, 1015700):
        return fail('unsupported Sissela Q scope')
    required = ('nonPlayerSkillStarts', 'summons', 'terminals', 'actions', 'damages', 'gaps')
    if player not in teams or any(inputs.get(k) is None for k in required):
        return fail('missing Wilson Q execution evidence')
    ids = load_exact_skill_ids()
    if ids.get('SisselaActive1') != 199 or ids.get('SisselaWilsonActive1') != 207 or any(catalog['skillGroups'].get(str(g), {}).get('skillId') != name for g, name in ((1015200, 'SisselaActive1'), (1015700, 'SisselaWilsonActive1'))):
        return fail('version-locked Wilson Q identities differ')
    packets = {'CmdStartSkill', 'CmdFinishSkill', 'CmdSpawn', 'CmdDestroy', 'CmdDamage', 'CmdPlaySkillAction', 'CmdPlaySkillActionWithTargets', 'SummonSnapshot:11'}
    if any(g.get('count', 0) and g.get('packetName') in packets for g in inputs['gaps']):
        return fail('incomplete Wilson Q command stream')
    resolve = live_summon_owner_resolver(inputs['summons'], inputs['terminals'], set(teams))
    children = defaultdict(list)
    for s in inputs['nonPlayerSkillStarts']:
        if s.get('skillIdCode') != 207: continue
        owner, path, why = resolve(s['sourceObjectId'], s['tick'])
        if why: return fail('Wilson ownership: ' + why)
        if owner != player: continue
        if path != [1030] or s.get('skillCode') != 1015701:
            return fail('Wilson Q skill/summon identity mismatch')
        children[s['sourceObjectId']].append({**s, 'playerObjectId': s['sourceObjectId'], 'skillGroup': 1015700})
    records = []
    for actor, ss in children.items():
        rr, why = ordered_cast_records(ss, finishes, actor, allow_same_tick_finishes=True)
        if why: return fail('Wilson Q lifetime: ' + why)
        records.extend(rr)
    if not parent_scope:
        diag.update(observedCastCount=len(records), observedCombatCastCount=sum(in_combat(r['start']['tick']) for r in records))
    match_end = ordered_winner_match_end(inputs.get('gameTerminals'), inputs['gaps'])
    parents, why = ordered_cast_records(roots, finishes, player, allow_same_tick_finishes=True)
    if why: return fail('Sissela Q parent: ' + why)
    if match_end:
        for r in [*records, *parents]:
            if not r['complete'] and order(r['start']) < order(match_end):
                r.update(finish=match_end, complete=True, reason=None, closureKind='actual-winner-match-end')
    unknown, stages, contacts, damage_packets = {}, {}, {}, {}
    unused = set()
    for i, r in enumerate(records):
        contacts[i] = {'path': set(), 'arrival': set()}; damage_packets[i] = []
        if not r['complete']:
            unknown[i] = 'child lifetime remains open'; continue
        if any(s['objectId'] == r['start']['sourceObjectId'] and s.get('wireOrder') and order(r['start']) < order(s) < order(r['finish']) for s in inputs['summons']):
            unknown[i] = 'Wilson identity replaced during child use'; continue
        aa = [a for a in inputs['actions'] if a.get('sourceObjectId') == r['start']['sourceObjectId'] and a.get('skillIdCode') == 207 and a.get('actionNo') in (1, 2) and r['start']['tick'] <= a['tick'] <= r['finish']['tick']]
        if any(order(a) is None or a.get('wireStatus') not in ORDINARY_ACTIONS for a in aa):
            unknown[i] = 'Wilson stage action lacks exact order'; continue
        aa = [a for a in aa if order(r['start']) < order(a) < order(r['finish'])]
        a1 = [a for a in aa if a['actionNo'] == 1]; a2 = [a for a in aa if a['actionNo'] == 2]
        if not a1 and not a2 and r['finish'].get('reason') in (1, 2, 3, 4, 11):
            unused.add(i); continue
        if len(a1) != 1 or len(a2) > 1 or (a2 and order(a2[0]) <= order(a1[0])):
            unknown[i] = 'Wilson path/arrival phase markers are ambiguous'; continue
        if not a2 and r['finish'].get('reason') == 0:
            unknown[i] = 'normal Wilson finish lacks completed stop burst'; continue
        stages[i] = {'path': a1[0], 'arrival': a2[0] if a2 else None}
    for d in inputs['damages']:
        if d.get('attackerObjectId') != player or d.get('effectCode') not in (1015101, 1015102): continue
        candidates = [i for i, r in enumerate(records) if r['start']['tick'] <= d['tick'] and (not r['complete'] or d['tick'] <= r['finish']['tick'])]
        if order(d) is None:
            for i in candidates: unknown[i] = 'Wilson damage lacks exact command order'
            continue
        candidates = [i for i in candidates if order(records[i]['start']) < order(d) and (not records[i]['complete'] or order(d) < order(records[i]['finish']))]
        if len(candidates) != 1:
            if not candidates: return fail('dedicated Wilson Q damage has no owned child use')
            for i in candidates: unknown[i] = 'dedicated damage overlaps multiple Wilson uses'
            continue
        i = candidates[0]; stage = stages.get(i)
        if not stage: unknown[i] = 'damage has no executed Wilson phase'; continue
        stop = stage['arrival']; phase = 'path' if d['effectCode'] == 1015101 else 'arrival'
        if order(d) <= order(stage['path']) or (stop and order(d) >= order(stop)) or (phase == 'arrival' and (not stop or d['tick'] != stop['tick'])) or type(d.get('damageIsNull')) is not bool:
            unknown[i] = 'damage contradicts static path/stop execution order'; continue
        damage_packets[i].append(d)
        target = d['targetObjectId']
        if target in teams and teams[target] != teams[player]: contacts[i][phase].add((d['tick'], target))
    parent_children = defaultdict(list); child_parents = {}
    for i, r in enumerate(records):
        matches = [j for j, p in enumerate(parents) if p['complete'] and order(p['start']) < order(r['start']) < order(p['finish'])]
        if len(matches) == 1:
            child_parents[i] = matches[0]; parent_children[matches[0]].append(i)
        elif parent_scope:
            return fail('Wilson Q child lacks one exact parent request')
    mapped, out_unknown, out_unused = [], {}, set()
    if parent_scope:
        for j, p in enumerate(parents):
            cc = parent_children[j]
            if not p['complete']: out_unknown[j] = 'parent Q remains open'
            elif not cc: out_unused.add(j)
            elif len(cc) != 1: out_unknown[j] = 'parent Q owns multiple child requests'
            elif cc[0] in unknown: out_unknown[j] = unknown[cc[0]]
            elif cc[0] in unused: out_unused.add(j)
            else: mapped.append((j, cc[0], p['start']['tick']))
        scope_records = parents
    else:
        mapped = [(i, i, r['start']['tick']) for i, r in enumerate(records) if i not in unknown and i not in unused]
        out_unknown, out_unused, scope_records = unknown, unused, records
    chosen = [(j, i, t) for j, i, t in mapped if in_combat(t)]
    def result_for(phase=None):
        selected = [(j, i, t) for j, i, t in chosen if phase != 'arrival' or stages[i]['arrival']]
        cc = [contacts[i][phase] if phase else contacts[i]['path'] | contacts[i]['arrival'] for _, i, _ in selected]
        r = _result(spec, cc, 'static-Wilson-Q-path-loop-and-stop-burst' + (':' + phase if phase else ''), cast_ticks=[t for _, _, t in selected])
        r['outcomes'] = [exact_outcome(t, stages[i]['arrival' if phase == 'arrival' else 'path']['tick'], c) for (_, i, t), c in zip(selected, cc)]
        return r
    result = result_for()
    result.update(diag, phaseMetrics={k: result_for(k) for k in ('path', 'arrival')},
        unresolvedCombatCastCount=sum(in_combat(scope_records[i]['start']['tick']) for i in out_unknown),
        unresolvedNonCombatCastCount=sum(not in_combat(scope_records[i]['start']['tick']) for i in out_unknown),
        unresolvedCastReasons=dict(Counter(v for i, v in out_unknown.items() if in_combat(scope_records[i]['start']['tick']))),
        nonExecutedCastCount=sum(in_combat(scope_records[i]['start']['tick']) for i in out_unused), nonExecutedAllCastCount=len(out_unused),
        executionEvidenceByAttempt=[dict(request=scope_records[j], child=records[i], phaseActions=stages[i], phaseContacts={k: sorted(v) for k, v in contacts[i].items()}, damageCommands=damage_packets[i]) for j, i, t in chosen],
        parentAndChildAreSeparateViews=True, matchEndClosedChildCount=sum(records[i].get('closureKind')=='actual-winner-match-end' for _,i,_ in chosen), recordedEnemyDamageContactCount=sum(len(contacts[i]['path']) + len(contacts[i]['arrival']) for _, i, _ in chosen),
        denominatorMeaning='One executed Wilson Q; path and actual arrival burst have separate phase views',
        hitMeaning='Explicit dedicated pass1015101 or stop1015102 enemy damage command inside the exact owned child execution',
        perUseCompletenessTracked=True, incompleteUsesCountedAsMisses=False, fixedDurationWindowUsed=False, damageAmountInferred=False,
        evidenceReview='deliverables/sissela-q-execution-static-proof-v1.json')
    return result
