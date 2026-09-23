"""Normal E celestial identity and initial/fall contacts from owned objects."""
from collections import Counter, defaultdict

try:
    from .skill_adina_star_e_execution import _caster, _unique
    from .requested_skill_hit_rates import _result, _unavailable
    from .skill_partial_cast_lifetimes import ordered_cast_records
    from .skill_wire_order import command_order as order
    from .skill_attempt_timing import exact_outcome
    from .skill_action_stage_evidence import load_exact_skill_ids
except ImportError:
    from skill_adina_star_e_execution import _caster, _unique
    from requested_skill_hit_rates import _result, _unavailable
    from skill_partial_cast_lifetimes import ordered_cast_records
    from skill_wire_order import command_order as order
    from skill_attempt_timing import exact_outcome
    from skill_action_stage_evidence import load_exact_skill_ids


# Explicit assignments in AdinaSkillActive3Data::.ctor (0x121d1f0),
# selected by SetProjectileValues/GetAttachStateCode and Attach*.AttackProcess.
ROUTES = {
    105241: dict(name='sun', state=764, enemy=1052410, ally=1052460, fall=105245, direct=105249),
    105242: dict(name='moon', state=765, enemy=1052420, ally=1052470, fall=105246, direct=105250),
    105243: dict(name='star', state=766, enemy=1052430, ally=1052480, fall=105247, direct=105251),
}


def adina_e_celestial_execution_metric(spec, starts, finishes, player, teams, intervals, catalog, inputs,development=False):
    roots = [s for s in starts if s['playerObjectId'] == player and s['skillGroup'] == 1052400]
    combat = lambda i: any(a <= parents[i]['start']['tick'] < b for a, b in intervals)
    diag = dict(observedCastCount=len(roots), observedCombatCastCount=sum(any(a <= s['tick'] < b for a, b in intervals) for s in roots))
    fail = lambda reason: {**_unavailable(spec, reason), **diag}
    if (spec['characterCode'], spec['skillGroup'], spec['unit'], spec['mode']) != (52, 1052400, 'skill-cast', 'any'):
        return fail('unsupported normal Adina E scope')
    if player not in teams or any(inputs.get(k) is None for k in ('allProjectileSpawns', 'projectileMovement', 'collisions', 'terminals', 'stateScripts', 'gaps')):
        return fail('missing normal Adina E lineage evidence')
    ids = load_exact_skill_ids()
    if ids.get('AdinaActive3') != 762 or any(ids.get('AdinaActive3Attach' + r['name'].title()) != r['state'] for r in ROUTES.values()):
        return fail('version-locked normal Adina E identities differ')
    if catalog['skillGroups'].get('1052400', {}).get('skillId') != 'AdinaActive3' or any(str(c) not in catalog['projectileDefinitions'] for r in ROUTES.values() for c in (r['fall'], r['direct'])):
        return fail('version-locked normal Adina E definitions missing')
    required = {'CmdStartSkill', 'CmdFinishSkill', 'CmdSpawn', 'CmdProjectileCollision', 'CmdProjectileArrived', 'CmdProjectileExplosion', 'CmdDestroyDelayStart', 'CmdDestroy', 'CmdStartStateSkill', 'CmdFinishStateSkill'}
    if any(g.get('count', 0) and g.get('packetName') in required for g in inputs['gaps']):
        return fail('incomplete normal Adina E command stream')
    if any(s.get('skillIdCode') != 762 or s.get('skillCode') not in range(1052401, 1052406) for s in roots):
        return fail('normal Adina E parent code mismatch')
    parents, why = ordered_cast_records(roots, finishes, player, allow_same_tick_finishes=True)
    if why:
        return fail(why)
    codes = set(ROUTES) | {r[k] for r in ROUTES.values() for k in ('fall', 'direct')}
    shots = [s for s in inputs['allProjectileSpawns'] if s.get('ownerObjectId') == player and s.get('projectileCode') in codes]
    if any(order(s) is None for s in shots) or len({s['projectileObjectId'] for s in shots}) != len(shots):
        return fail('normal E projectile identity/order is ambiguous')
    shotids = {s['projectileObjectId'] for s in shots}
    cs, ts = defaultdict(list), defaultdict(list)
    try:
        for c in _unique([c for c in inputs['collisions'] if c['projectileObjectId'] in shotids], ('tick', 'projectileObjectId', 'targetObjectId')):
            cs[c['projectileObjectId']].append(c)
        for t in _unique([t for t in inputs['terminals'] if t['objectId'] in shotids], ('tick', 'objectId', 'event', 'isCollision')):
            ts[t['objectId']].append(t)
    except ValueError as exc:
        return fail(str(exc))
    by_state = defaultdict(list)
    for s in inputs['stateScripts']:
        if s.get('skillIdCode') in (764, 765, 766) and _caster(s) == player:
            by_state[s['sourceObjectId'], s['skillIdCode'], s['stateGroup']].append(s)
    states = []
    for (target, _, _), rows in by_state.items():
        ss = [{**s, 'playerObjectId': target} for s in rows if s['event'] == 'CmdStartStateSkill']
        fs = [{**s, 'playerObjectId': target} for s in rows if s['event'] == 'CmdFinishStateSkill']
        records, why = ordered_cast_records(ss, fs, target, allow_same_tick_finishes=True)
        if why:
            return fail('celestial attachment: ' + why)
        states.extend(records)
    enemy = lambda who: who in teams and teams[who] != teams[player]
    unknown, initial, attached, follow, route, phases, terminals = {}, {}, {}, {}, {}, {}, {}
    unused, direct, closed_without_fall = set(), set(), set()
    for i, p in enumerate(parents):
        phases[i] = {'initial': set(), 'fall': set()}
        if not p['complete']:
            unknown[i] = 'parent cast remains open'; continue
        candidates = [s for s in shots if s['projectileCode'] in ROUTES and order(p['start']) < order(s) < order(p['finish'])]
        if not candidates and p['finish']['reason'] in (2, 3, 11):
            unused.add(i); continue
        if len(candidates) != 1:
            unknown[i] = 'cast does not launch one exact celestial projectile'; continue
        s = initial[i] = candidates[0]; r = route[i] = ROUTES[s['projectileCode']]; pid = s['projectileObjectId']
        moves = [m for m in inputs['projectileMovement'] if m.get('projectileObjectId') == pid]
        if len(moves) != 1 or moves[0].get('wireStatus') != 'decoded-exact-projectile-movement' or moves[0].get('movementTargetObjectId') != p['start'].get('targetObjectId') or moves[0].get('ownerObjectId') != player or moves[0].get('projectileCode') != s['projectileCode'] or order(moves[0]) != order(s):
            unknown[i] = 'initial target/owner movement key is not exact'; continue
        arrivals = [t for t in ts[pid] if t['event'] == 'CmdProjectileArrived']
        stops = [t for t in ts[pid] if t['event'] in ('CmdDestroyDelayStart', 'CmdDestroy')]
        if len(arrivals) != 1 or not stops or any(order(t) <= order(s) for t in [*arrivals, *stops]):
            unknown[i] = 'initial projectile lacks arrival and closure'; continue
        a = arrivals[0]; cc = cs[pid]; terminals[i] = {'initial': ts[pid]}
        if len(cc) > 1 or any(c['targetObjectId'] != p['start']['targetObjectId'] or c['tick'] != a['tick'] or not order(s) < order(c) < order(a) for c in cc):
            unknown[i] = 'initial collision differs from exact target/arrival'; continue
        if cc and enemy(cc[0]['targetObjectId']):
            phases[i]['initial'].add((cc[0]['tick'], cc[0]['targetObjectId']))
        matches = [m for m in states if cc and m['start']['skillIdCode'] == r['state'] and m['start']['sourceObjectId'] == cc[0]['targetObjectId'] and m['start'].get('skillCode') == p['start']['skillCode'] and m['start']['tick'] == a['tick'] and order(cc[0]) < order(m['start']) < order(a)]
        # DirectAttackProcess is invoked in the target callback before Arrived,
        # or in the Arrived callback before initial DestroyDelayStart.
        end = min((t for t in stops if order(t) > order(a)), key=order, default=None)
        bypass = [x for x in shots if x['projectileCode'] == r['direct'] and x['tick'] == a['tick'] and end and (order(cc[0]) if cc else order(a)) < order(x) < order(end)]
        if len(matches) == 1 and not bypass:
            m = attached[i] = matches[0]
            ally = cc[0]['targetObjectId'] in teams and teams[cc[0]['targetObjectId']] == teams[player]
            if m['start']['stateGroup'] != r['ally' if ally else 'enemy'] or not m['complete'] or m['finish']['reason'] not in (0, 3):
                unknown[i] = 'attachment branch or termination is not exact'; continue
        elif len(bypass) == 1 and not matches:
            follow[i] = bypass[0]; direct.add(i)
        elif not cc and not matches and not bypass and end:
            closed_without_fall.add(i)
        else:
            unknown[i] = 'initial callback has no unique attachment or direct-ground branch'; continue
        if cc and enemy(cc[0]['targetObjectId']):
            phases[i]['initial'].add((cc[0]['tick'], cc[0]['targetObjectId']))
    for s in sorted((s for s in shots if s['projectileCode'] in {r['fall'] for r in ROUTES.values()}), key=order):
        eligible = [i for i, m in attached.items() if i not in follow and m['complete'] and route[i]['fall'] == s['projectileCode'] and order(m['finish']) < order(s)]
        if not eligible:
            return fail('orphan celestial fall projectile')
        if len(eligible) != 1:
            for i in eligible: unknown[i] = 'multiple pending celestial attachments'
            continue
        i = eligible[0]; follow[i] = s; m = attached[i]
        if m['finish']['reason'] == 0 and m['finish']['tick'] != s['tick']:
            unknown[i] = 'normal attachment did not launch synchronously'
        if any(x['projectileCode'] == initial[i]['projectileCode'] and order(initial[i]) < order(x) < order(s) for x in shots):
            unknown[i] = 'another same-celestial launch overlaps pending attachment'
    for i, s in follow.items():
        pid = s['projectileObjectId']; explosions = [t for t in ts[pid] if t['event'] == 'CmdProjectileExplosion']; ends = [t for t in ts[pid] if t['event'] in ('CmdDestroyDelayStart', 'CmdDestroy')]
        if len(explosions) != 1 or not ends or not order(s) < order(explosions[0]) < order(min(ends, key=order)):
            unknown[i] = 'fall lacks exact explosion and destruction start'; continue
        e, end = explosions[0], min(ends, key=order)
        if any(c['tick'] != e['tick'] or not order(e) < order(c) < order(end) for c in cs[pid]):
            unknown[i] = 'contact outside synchronous celestial explosion'; continue
        phases[i]['fall'] = {(c['tick'], c['targetObjectId']) for c in cs[pid] if enemy(c['targetObjectId'])}
        terminals[i]['fall'] = ts[pid]
    for i in initial:
        if i not in follow and i not in closed_without_fall: unknown.setdefault(i, 'celestial follow-up remains unclosed')
    if any(m not in attached.values() for m in states) or any(s not in initial.values() and s not in follow.values() for s in shots):
        return fail('unattributed celestial state or projectile')
    recovered={i:unknown[i] for i in initial if development and i in unknown and phases[i]['initial'] and combat(i)}
    for i in recovered:del unknown[i]
    chosen = [i for i in initial if (i in follow or i in closed_without_fall or i in recovered) and i not in unknown and combat(i)]
    contacts = lambda i: phases[i]['initial'] | phases[i]['fall']
    def result_for(indices, phase=None):
        if phase == 'fall': indices = [i for i in indices if i in follow and i not in recovered]
        values = [phases[i][phase] if phase else contacts(i) for i in indices]
        result = _result(spec, values, 'static-Adina-celestial-attachment-fall' + (':' + phase if phase else ''), cast_ticks=[parents[i]['start']['tick'] for i in indices])
        result['outcomes'] = [exact_outcome(parents[i]['start']['tick'], (follow[i] if phase == 'fall' else initial[i])['tick'], v) for i, v in zip(indices, values)]
        return result
    result = result_for(chosen)
    result.update(diag, phaseMetrics={k: result_for(chosen, k) for k in ('initial', 'fall')},
        celestialMetrics={name: {'combined': result_for([i for i in chosen if route[i]['name'] == name]), **{k: result_for([i for i in chosen if route[i]['name'] == name], k) for k in ('initial', 'fall')}} for name in ('sun', 'moon', 'star')},
        unresolvedCombatCastCount=sum(combat(i) for i in unknown), unresolvedNonCombatCastCount=sum(not combat(i) for i in unknown),
        unresolvedCastReasons=dict(Counter(v for i, v in unknown.items() if combat(i))),
        nonExecutedCastCount=sum(combat(i) for i in unused), nonExecutedAllCastCount=len(unused),
        initialAttachmentAppliedCount=sum(i in attached for i in chosen), closedInitialWithoutFallCount=sum(i in closed_without_fall for i in chosen), directGroundExecutionCount=sum(i in direct for i in chosen),
        executionEvidenceByAttempt=[dict(request=parents[i], celestial=route[i]['name'], initialProjectile=initial[i], attachment=attached.get(i), directGround=i in direct, fallProjectile=follow.get(i), terminalCommands=terminals[i], phaseContacts={k: sorted(v) for k, v in phases[i].items()}) for i in chosen],
        denominatorMeaning='One actual normal E launch; celestial and phase detail preserve the same parent lineage',
        hitMeaning='Enemy collision on initial or fall projectile; ally healing and NPC contact excluded',
        perUseCompletenessTracked=True, incompleteUsesCountedAsMisses=False, fixedDurationWindowUsed=False, damageAmountInferred=False,
        evidenceReview='deliverables/adina-normal-e-celestial-static-proof-v1.json')
    if recovered:
        from .skill_development_cancellation import annotate_provisional
        result.update(initialContactSurvivesIncompleteFollowup=len(recovered),unresolvedFollowupDetails={str(parents[i]['start']['tick']):v for i,v in recovered.items()},completeTargetCountsAvailable=False,targetCountsAreLowerBounds=True)
        annotate_provisional(result,'Exact initial enemy contact is sufficient for requested E success. Unresolved follow-up detail is preserved separately and never fabricated.')
    return result
