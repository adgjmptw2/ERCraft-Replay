"""Star conjunction E: target projectile, attached state, then one area attack."""
from collections import Counter, defaultdict

try:
    from .requested_skill_hit_rates import _result, _unavailable
    from .skill_partial_cast_lifetimes import ordered_cast_records
    from .skill_wire_order import command_order as order
    from .skill_attempt_timing import exact_outcome
    from .skill_action_stage_evidence import load_exact_skill_ids
except ImportError:
    from requested_skill_hit_rates import _result, _unavailable
    from skill_partial_cast_lifetimes import ordered_cast_records
    from skill_wire_order import command_order as order
    from skill_attempt_timing import exact_outcome
    from skill_action_stage_evidence import load_exact_skill_ids


def _caster(row):
    return row['sourceObjectId'] if row.get('casterObjectId') == 0 else row.get('casterObjectId')


def _unique(rows, fields):
    found = {}
    for row in rows:
        at = order(row)
        if at is None:
            raise ValueError('projectile contact or terminal lacks exact command order')
        identity = tuple(row.get(k) for k in fields)
        if at in found and identity != tuple(found[at].get(k) for k in fields):
            raise ValueError('conflicting facts share one command order')
        found[at] = row
    return [found[at] for at in sorted(found)]


def adina_star_e_execution_metric(spec, starts, finishes, player, teams, intervals, catalog, inputs):
    roots = [s for s in starts if s['playerObjectId'] == player and s['skillGroup'] == 1052410]
    in_combat = lambda s: any(a <= s['tick'] < b for a, b in intervals)
    diag = dict(observedCastCount=len(roots), observedCombatCastCount=sum(map(in_combat, roots)))
    fail = lambda why: {**_unavailable(spec, why), **diag}
    if (spec['characterCode'], spec['skillGroup'], spec['unit'], spec['mode']) != (52, 1052410, 'skill-cast', 'any'):
        return fail('unsupported Adina star conjunction scope')
    if player not in teams or any(inputs.get(k) is None for k in ('allProjectileSpawns', 'projectileMovement', 'collisions', 'terminals', 'stateScripts', 'damages', 'gaps')):
        return fail('missing star conjunction lineage evidence')
    ids = load_exact_skill_ids()
    if ids.get('AdinaActive3ReinforceStar') != 763 or ids.get('AdinaActive3AttachReinforceStar') != 767 or catalog['skillGroups'].get('1052410', {}).get('skillId') != 'AdinaActive3ReinforceStar':
        return fail('version-locked star conjunction identities differ')
    definitions = catalog.get('projectileDefinitions', {})
    if any(str(code) not in definitions for code in (105244, 105248, 105252)):
        return fail('version-locked conjunction projectile definitions missing')
    required = {'CmdStartSkill', 'CmdFinishSkill', 'CmdSpawn', 'CmdProjectileCollision', 'CmdProjectileArrived', 'CmdProjectileExplosion', 'CmdDestroyDelayStart', 'CmdDestroy', 'CmdStartStateSkill', 'CmdFinishStateSkill'}
    if any(g.get('count', 0) and g.get('packetName') in required for g in inputs['gaps']):
        return fail('incomplete star conjunction command stream')
    if any(s.get('skillIdCode') != 763 or s.get('skillCode') not in range(1052411, 1052416) for s in roots):
        return fail('star conjunction parent code mismatch')
    parents, why = ordered_cast_records(roots, finishes, player, allow_same_tick_finishes=True)
    if why:
        return fail(why)
    shots = [s for s in inputs['allProjectileSpawns'] if s.get('ownerObjectId') == player and s.get('projectileCode') in (105244, 105248, 105252)]
    if any(order(s) is None for s in shots) or len({s['projectileObjectId'] for s in shots}) != len(shots):
        return fail('star projectile spawn identity/order is ambiguous')
    shotids = {s['projectileObjectId'] for s in shots}
    try:
        collisions = _unique([c for c in inputs['collisions'] if c['projectileObjectId'] in shotids], ('tick', 'projectileObjectId', 'targetObjectId'))
        terminals = _unique([t for t in inputs['terminals'] if t['objectId'] in shotids], ('tick', 'objectId', 'event', 'isCollision'))
    except ValueError as exc:
        return fail(str(exc))
    cs = defaultdict(list)
    ts = defaultdict(list)
    for c in collisions:
        cs[c['projectileObjectId']].append(c)
    for t in terminals:
        ts[t['objectId']].append(t)
    scripts = [s for s in inputs['stateScripts'] if s.get('skillIdCode') == 767 and _caster(s) == player]
    if any(s.get('stateGroup') not in (1052440, 1052490) for s in scripts):
        return fail('conjunction attachment state group mismatch')
    by_state = defaultdict(list)
    for s in scripts:
        by_state[s['sourceObjectId'], s['stateGroup']].append(s)
    states = []
    for (target, _), rows in by_state.items():
        ss = [{**s, 'playerObjectId': target} for s in rows if s['event'] == 'CmdStartStateSkill']
        fs = [{**s, 'playerObjectId': target} for s in rows if s['event'] == 'CmdFinishStateSkill']
        records, why = ordered_cast_records(ss, fs, target, allow_same_tick_finishes=True)
        if why:
            return fail('star attachment: ' + why)
        states.extend(records)
    enemy = lambda who: who in teams and teams[who] != teams[player]
    unknown = {}
    unused = []
    initial = {}
    attached = {}
    follow = {}
    direct = {}
    policy_hits = {}
    contacts = {}
    phases = {}
    terminal_evidence = {}
    for i, p in enumerate(parents):
        contacts[i] = set()
        phases[i] = {'initial': set(), 'fall': set()}
        if not p['complete']:
            unknown[i] = 'parent cast remains open'
            continue
        candidates = [s for s in shots if s['projectileCode'] == 105244 and order(p['start']) < order(s) < order(p['finish'])]
        if not candidates and p['finish']['reason'] in (2, 3):
            unused.append(i)
            continue
        if len(candidates) != 1:
            unknown[i] = 'cast does not launch one exact initial projectile'
            continue
        s = candidates[0]
        initial[i] = s
        pid = s['projectileObjectId']
        moves = [m for m in inputs['projectileMovement'] if m.get('projectileObjectId') == pid]
        if len(moves) != 1 or moves[0].get('wireStatus') != 'decoded-exact-projectile-movement' or moves[0].get('movementTargetObjectId') != p['start'].get('targetObjectId') or moves[0].get('ownerObjectId') != player or moves[0].get('projectileCode') != 105244 or order(moves[0]) != order(s):
            unknown[i] = 'initial target/owner movement key is not exact'
            continue
        # Native105252 is created only by this initial projectile's target/dead
        # callback. Bound it to one live owned initial and its same-frame
        # destruction start; an overlapping initial is ambiguous, not nearest.
        closures = [t for t in ts[pid] if t['event'] in ('CmdDestroyDelayStart', 'CmdDestroy') and order(t) > order(s)]
        closure = min(closures, key=order, default=None)
        bypass = [x for x in shots if closure and x['projectileCode'] == 105252 and x['tick'] == closure['tick'] and order(s) < order(x) < order(closure)]
        if bypass:
            if len(bypass) != 1:
                unknown[i] = 'initial callback has multiple direct-ground spawns'; continue
            x = bypass[0]
            others = [v for v in shots if v['projectileCode'] == 105244 and v != s and order(v) < order(x) and not any(z['event'] in ('CmdDestroyDelayStart', 'CmdDestroy') and order(z) < order(x) for z in ts[v['projectileObjectId']])]
            cc = cs[pid]
            conflicting_states = [m for m in states if m['start'].get('skillCode') == p['start']['skillCode'] and order(s) < order(m['start']) < order(closure)]
            if others or conflicting_states or len(cc) > 1 or any(c['targetObjectId'] != p['start']['targetObjectId'] or c['tick'] != x['tick'] or not order(s) < order(c) < order(x) for c in cc):
                unknown[i] = 'direct-ground callback lineage is ambiguous'; continue
            direct[i] = x
            if (spec.get('directGroundTargetDeathPolicy')=='count-identified-enemy-branch-as-hit'
                    and enemy(p['start']['targetObjectId'])):
                policy_hits[i] = dict(policy='adina-enemy-direct-ground-success-20260909',
                    tick=x['tick'],branchWireOrder=x['wireOrder'],targetObjectId=p['start']['targetObjectId'])
            phases[i]['initial'] = {(c['tick'], c['targetObjectId']) for c in cc if enemy(c['targetObjectId'])}
            terminal_evidence[i] = {'initial': ts[pid]}
            continue
        arrived = [t for t in ts[pid] if t['event'] == 'CmdProjectileArrived']
        stops = [t for t in ts[pid] if t['event'] in ('CmdDestroyDelayStart', 'CmdDestroy')]
        if len(arrived) != 1 or not stops or any(order(t) <= order(s) for t in [*arrived, *stops]):
            unknown[i] = 'initial projectile lacks actual arrival and closure'
            continue
        a = arrived[0]
        cc = cs[pid]
        if len(cc) != 1 or cc[0]['targetObjectId'] != p['start']['targetObjectId'] or cc[0]['tick'] != a['tick'] or not order(s) < order(cc[0]) < order(a):
            unknown[i] = 'initial collision/direct-ground branch requires separate evidence'
            continue
        c = cc[0]
        matches = [m for m in states if m['start']['sourceObjectId'] == c['targetObjectId'] and m['start'].get('skillCode') == p['start']['skillCode'] and c['tick'] == m['start']['tick'] and order(c) < order(m['start']) < order(a)]
        if len(matches) != 1:
            unknown[i] = 'initial collision does not create one exact attachment'
            continue
        m = matches[0]
        attached[i] = m
        ally = c['targetObjectId'] in teams and teams[c['targetObjectId']] == teams[player]
        if (m['start']['stateGroup'] == 1052490) != ally:
            unknown[i] = 'attachment branch differs from official team identity'
            continue
        if not m['complete'] or m['finish']['reason'] not in (0, 3):
            unknown[i] = 'attachment has open or unreviewed termination'
            continue
        if enemy(c['targetObjectId']):
            phases[i]['initial'].add((c['tick'], c['targetObjectId']))
        terminal_evidence[i] = {'initial': ts[pid]}
    # A cancelled attachment may schedule its own AttackProcess after Finish.
    # Consume a unique pending instance, with no elapsed-time cutoff. Any other
    # conjunction launch in between makes that delayed parent ambiguous.
    consumed = set()
    for s in sorted((s for s in shots if s['projectileCode'] in (105248, 105252)), key=order):
        if s['projectileCode'] == 105252:
            matches = [i for i, x in direct.items() if x == s]
            if len(matches) != 1:
                return fail('orphan or ambiguous conjunction direct-ground projectile')
            i = matches[0]
            follow[i] = s
            if i in policy_hits:
                # The user counts the identified target-death branch as a
                # successful use; later ground contacts are not required.
                continue
        else:
            eligible = [i for i, m in attached.items() if i not in consumed and m['complete'] and order(m['finish']) < order(s)]
            if len(eligible) != 1:
                if not eligible:
                    return fail('orphan conjunction follow-up projectile')
                for i in eligible:
                    unknown[i] = 'follow-up projectile has multiple pending attachments'
                continue
            i = eligible[0]
            m = attached[i]
            consumed.add(i)
            follow[i] = s
            if m['finish']['reason'] == 0 and m['finish']['tick'] != s['tick']:
                unknown[i] = 'normal attachment finish did not launch synchronously'
            if any(x['projectileObjectId'] != initial[i]['projectileObjectId'] and order(initial[i]) < order(x) < order(s) for x in shots if x['projectileCode'] == 105244):
                unknown[i] = 'another conjunction launch overlaps pending follow-up'
        pid = s['projectileObjectId']
        explosions = [t for t in ts[pid] if t['event'] == 'CmdProjectileExplosion']
        destroys = [t for t in ts[pid] if t['event'] == 'CmdDestroy']
        if len(explosions) != 1 or len(destroys) != 1 or not order(s) < order(explosions[0]) < order(destroys[0]):
            unknown[i] = 'fall projectile has no exact explosion and final destruction'
            continue
        e, end = explosions[0], destroys[0]
        if any(c['tick'] != e['tick'] or not order(e) < order(c) < order(end) for c in cs[pid]):
            unknown[i] = 'contact lies outside the synchronous fall explosion'
            continue
        phases[i]['fall'] = {(c['tick'], c['targetObjectId']) for c in cs[pid] if enemy(c['targetObjectId'])}
        terminal_evidence.setdefault(i, {})['fall'] = ts[pid]
    for i in initial:
        if i not in follow:
            unknown.setdefault(i, 'attachment follow-up remains unclosed')
        contacts[i] = phases[i]['initial'] | phases[i]['fall']
    if any(m not in attached.values() for m in states):
        return fail('attachment state has no unique initial conjunction parent')
    if any(s['projectileCode']==105244 and s not in initial.values() for s in shots):
        return fail('orphan initial conjunction projectile')
    chosen = [i for i in follow if i not in unknown and in_combat(parents[i]['start'])]
    phase_chosen = [i for i in chosen if i not in policy_hits]
    result = _result(spec, [contacts[i] for i in chosen], 'static-Adina-star-target-attachment-fall-projectile', cast_ticks=[parents[i]['start']['tick'] for i in chosen])
    result['phaseMetrics']={k:_result(spec,[phases[i][k] for i in phase_chosen],
        'static-Adina-star-target-attachment-fall-projectile:'+k,
        cast_ticks=[parents[i]['start']['tick'] for i in phase_chosen]) for k in ('initial','fall')}
    for k,r in result['phaseMetrics'].items():
        r['outcomes']=[exact_outcome(parents[i]['start']['tick'],initial[i]['tick'] if k=='initial' else follow[i]['tick'],phases[i][k]) for i in phase_chosen]
    reasons = Counter(why for i, why in unknown.items() if in_combat(parents[i]['start']))
    result.update(diag,
        outcomes=[exact_outcome(parents[i]['start']['tick'], initial[i]['tick'], contacts[i]) for i in chosen],
        unresolvedCombatCastCount=sum(reasons.values()), unresolvedCastReasons=dict(reasons),
        unresolvedNonCombatCastCount=sum(not in_combat(parents[i]['start']) for i in unknown),
        nonExecutedCastCount=sum(in_combat(parents[i]['start']) for i in unused), nonExecutedAllCastCount=len(unused),
        phaseCounts={k: {'hitAttemptCount': sum(bool(phases[i][k]) for i in phase_chosen), 'recordedEnemyContactCount': sum(len(phases[i][k]) for i in phase_chosen)} for k in ('initial', 'fall')},
        initialEnemyTargetedAttemptCount=sum(enemy(parents[i]['start']['targetObjectId']) for i in chosen),
        allyOrSelfAttachmentCount=sum(i in attached and attached[i]['start']['stateGroup'] == 1052490 for i in chosen),
        initialAttachmentAppliedCount=sum(i in attached for i in chosen), directGroundExecutionCount=sum(i in direct for i in chosen), fallExecutionCount=len(chosen),
        lateCancelledAttachmentFallCount=sum(i in attached and attached[i]['finish']['reason'] != 0 for i in chosen),
        phaseContactsByAttempt=[{k: sorted(v) for k, v in phases[i].items()} for i in chosen],
        executionEvidenceByAttempt=[dict(request=parents[i], initialProjectile=initial[i], attachment=attached.get(i), directGround=i in direct, fallProjectile=follow[i], terminalCommands=terminal_evidence[i],
            damageCommandsSharingContactTickTarget=[d for d in inputs['damages'] if d.get('attackerObjectId') == player and (d['tick'], d.get('targetObjectId')) in contacts[i]]) for i in chosen],
        damageCommandAssociation='Tick/target companions retained for review only; the hit proof is the exact owned projectile collision, not FX0 or inferred damage amount',
        denominatorMeaning='One launched conjunction cast with both attachment and fall lineage closed; self/ally attachment is separate from enemy contact',
        hitMeaning='Enemy contact by the exact initial or fall projectile; allied healing and NPC contacts do not count',
        attemptTickMeaning='Actual initial projectile spawn; phase contacts use recorded collision ticks',
        perUseCompletenessTracked=True, incompleteUsesCountedAsMisses=False, fixedDurationWindowUsed=False, damageAmountInferred=False,
        directGroundBypassVerifiedByRetainedSample=False, directGroundLineageSupported=True, evidenceReview='deliverables/adina-star-e-execution-static-proof-v1.json')
    result['policySuccessByAttempt']=[policy_hits.get(i) for i in chosen]
    result['policyClassifiedCastCount']=sum(i in policy_hits for i in chosen)
    result['policyOnlyHitCount']=sum(i in policy_hits and not contacts[i] for i in chosen)
    result['recordedContactHitCount']=result['hitCount']
    result['directGroundSampleVerificationRequired']=spec.get('directGroundTargetDeathPolicy')!='count-identified-enemy-branch-as-hit'
    if result['policyClassifiedCastCount']:
        for outcome,i in zip(result['outcomes'],chosen):
            if i in policy_hits:
                outcome[1]=1
                outcome[3]=min(outcome[3],policy_hits[i]['tick']) if outcome[3] is not None else policy_hits[i]['tick']
        result['hitCount']=sum(o[1] for o in result['outcomes'])
        result['hitRate']=round(result['hitCount']/result['attemptCount'],6)
        # Do not invent a collision or its target-count timeline to implement
        # a user-defined binary success. Preserve the measured contact counts.
        for key in ('distinctEnemyTargetsPerAttempt','distinctEnemyTargetFirstHitTicksPerAttempt'):
            result['recorded'+key[0].upper()+key[1:]]=result.pop(key)
        result.update(outcomeScope='recorded-contact-or-user-defined-direct-ground-success',
            targetCountsAreLowerBounds=True,phaseDetailsExcludePolicyBranches=True,
            hitMeaning='Recorded enemy contact or user-defined success of the identified enemy-target direct-ground branch',
            denominatorMeaning='Launched conjunction uses with closed normal lineage or identified enemy-target direct-ground branch',
            attemptTickMeaning='Initial spawn; policy success tick is the recorded direct-ground branch spawn, not an inferred collision')
    return result
