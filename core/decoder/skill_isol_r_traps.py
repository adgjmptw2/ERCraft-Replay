"""Installation -> owned trap identity -> exact initial burst target packet."""
from collections import Counter, defaultdict

try:
    from .requested_skill_hit_rates import _result, _unavailable
    from .skill_partial_cast_lifetimes import ordered_cast_records
    from .skill_wire_order import command_order as order
    from .skill_ordered_match_end import ordered_winner_match_end
    from .skill_attempt_timing import exact_outcome
    from .skill_action_stage_evidence import load_exact_skill_ids
except ImportError:
    from requested_skill_hit_rates import _result, _unavailable
    from skill_partial_cast_lifetimes import ordered_cast_records
    from skill_wire_order import command_order as order
    from skill_ordered_match_end import ordered_winner_match_end
    from skill_attempt_timing import exact_outcome
    from skill_action_stage_evidence import load_exact_skill_ids


def isol_r_trap_metric(spec, starts, finishes, player, teams, intervals, catalog, inputs):
    return owned_trap_contact_metric(spec, starts, finishes, player, teams, intervals, catalog, inputs)


def owned_trap_contact_metric(spec, starts, finishes, player, teams, intervals, catalog, inputs):
    cfg = {
        (9, 1009500): ('IsolActive4', 135, 1012, range(1009501, 1009504)),
        (25, 1025300): ('BerniceActive2', 337, 1091, range(1025301, 1025306)),
    }.get((spec['characterCode'], spec['skillGroup']))
    if cfg is None or (spec['mode'], spec['unit']) != ('any', 'skill-cast'):
        return _unavailable(spec, 'unsupported owned trap scope')
    name, skill_id, summon_code, skill_codes = cfg
    bernice = name == 'BerniceActive2'
    roots = [s for s in starts if s['playerObjectId'] == player and s['skillGroup'] == spec['skillGroup']]
    combat = lambda s: any(a <= s['tick'] < b for a, b in intervals)
    diag = dict(observedCastCount=len(roots), observedCombatCastCount=sum(map(combat, roots)))
    fail = lambda why: {**_unavailable(spec, why), **diag}
    if any(inputs.get(k) is None for k in ('summons', 'objects', 'trapEvents', 'terminals', 'deaths', 'gaps')):
        return fail('missing exact trap lifetime/burst stream')
    if load_exact_skill_ids().get(name) != skill_id or catalog['skillGroups'].get(str(spec['skillGroup']), {}).get('skillId') != name:
        return fail('pinned owned trap skill identity mismatch')
    if any(s['skillIdCode'] != skill_id or s['skillCode'] not in skill_codes for s in roots):
        return fail('owned trap cast wire/code mismatch')
    required = {'CmdStartSkill', 'CmdFinishSkill', 'CmdSpawn', 'CmdSpawnBatch', 'CmdActiveTrap', 'CmdBurstTrap', 'CmdDead', 'CmdDestroy'}
    if any(g.get('count', 0) and (g.get('packetName') in required or str(g.get('packetName', '')).startswith('SummonSnapshot:')) for g in inputs['gaps']):
        return fail('incomplete Isol R trap evidence')
    records, why = ordered_cast_records(roots, finishes, player, allow_same_tick_finishes=True)
    if why:
        return fail(why)
    objects = defaultdict(list)
    for obj in inputs['objects']:
        objects[obj['objectId']].append(obj)
    traps, by_cast = {}, defaultdict(list)
    for trap in inputs['summons']:
        if trap['ownerObjectId'] != player or trap['summonCode'] != summon_code:
            continue
        tid = trap['objectId']
        os = objects[tid]
        if tid in traps or trap.get('identityVerifiedAgainstGameDb') is not True or len(os) != 1 or order(os[0]) is None or os[0]['tick'] != trap['tick']:
            return fail('ambiguous owned R trap identity')
        t = {**trap, 'wireOrder': os[0]['wireOrder'], 'wireCategory': os[0]['wireCategory']}
        rr = [i for i, r in enumerate(records) if order(r['start']) < order(t) and
              (r['finish'] is None or order(t) < order(r['finish']))]
        if len(rr) != 1:
            return fail('owned trap has no unique installation cast')
        traps[tid] = t
        by_cast[rr[0]].append(t)
    events = defaultdict(list)
    for e in inputs['trapEvents']:
        if e['objectId'] not in traps:
            continue
        if order(e) is None or order(e) <= order(traps[e['objectId']]):
            return fail('unordered trap event or event before installation')
        if e['event'] not in ('CmdActiveTrap', 'CmdBurstTrap'):
            return fail('unexpected trap event')
        events[e['objectId']].append(e)
    match_end = ordered_winner_match_end(inputs.get('gameTerminals'), inputs['gaps'])
    contacts, ticks, attempted, details, evidence = [], [], [], [], []
    unknown, cancelled = {}, []
    for i, r in enumerate(records):
        s = r['start']
        if not r['complete']:
            unknown[i] = 'open-R-cast'
            continue
        tt = by_cast[i]
        if not tt and r['finish']['reason'] == 3:
            cancelled.append(s)
            continue
        if len(tt) != 1:
            unknown[i] = 'missing-or-multiple-R-installations'
            continue
        trap = tt[0]
        tid = trap['objectId']
        active = [e for e in events[tid] if e['event'] == 'CmdActiveTrap']
        bursts = [e for e in events[tid] if e['event'] == 'CmdBurstTrap']
        if len(active) > 1 or len(bursts) > 1:
            unknown[i] = 'multiple-trap-activations-or-bursts'
            continue
        hit = set()
        end, end_method = None, None
        if bursts:
            burst = bursts[0]
            if len(active) != 1 or order(active[0]) >= order(burst):
                unknown[i] = 'burst-without-prior-activation'
                continue
            targets = burst.get('targets')
            if not isinstance(targets, list) or not targets or any(type(t.get('targetObjectId')) is not int for t in targets):
                unknown[i] = 'missing-exact-burst-targets'
                continue
            hit = {(burst['tick'], t['targetObjectId']) for t in targets if t['targetObjectId'] in teams and teams[t['targetObjectId']] != teams[player]}
            end, end_method = burst, 'recorded-single-trap-burst'
        else:
            ends = [t for t in inputs['terminals'] if t['objectId'] == tid and t['event'] == 'CmdDestroy']
            if not ends:
                ends = [d for d in inputs['deaths'] if d['deadObjectId'] == tid and d['event'] == 'CmdDead' and d.get('isDyingBlockDead') is False]
            if not ends and match_end is not None:
                ends = [match_end]
            if len(ends) != 1 or ends[0]['tick'] < trap['tick']:
                unknown[i] = 'open-trap-with-no-burst-targets'
                continue
            end = ends[0]
            end_method = 'recorded-match-completion' if end is match_end else end['event']
            if active and (active[0]['tick'] > end['tick'] or active[0]['tick'] == end['tick'] and
                           (order(end) is None or order(active[0]) >= order(end))):
                unknown[i] = 'activation-outside-closed-trap-lifetime'
                continue
        if not combat(s):
            continue
        contacts.append(hit)
        ticks.append(s['tick'])
        attempted.append(trap['tick'])
        details.append([dict(hitTick=t, targetObjectId=who, trapObjectId=tid,
            phase='initial-trap-contact' if bernice else 'trap-explosion') for t, who in sorted(hit)])
        evidence.append(dict(start=s, finish=r['finish'], trap=trap, activation=active[0] if active else None,
                             burst=bursts[0] if bursts else None, end=end, endMethod=end_method))
    row = _result(spec, contacts, 'static-Bernice-W-owned-trap-initial-targets' if bernice else 'static-Isol-R-owned-trap-burst-targets', cast_ticks=ticks)
    row['outcomes'] = [exact_outcome(s, t, hits) for s, t, hits in zip(ticks, attempted, contacts)]
    reasons = Counter(why for i, why in unknown.items() if combat(records[i]['start']))
    row.update(diag, unresolvedCombatCastCount=sum(reasons.values()), unresolvedCastReasons=dict(reasons),
               unresolvedNonCombatCastCount=sum(not combat(records[i]['start']) for i in unknown),
               cancelledBeforeAttackCount=sum(map(combat, cancelled)), incompleteUsesCountedAsMisses=False,
               perUseCompletenessTracked=True, contactDetailsByAttempt=details, executionEvidenceByAttempt=evidence,
               evidenceReview='deliverables/bernice-w-initial-trap-static-v1.json' if bernice else 'deliverables/isol-r-trap-static-proof-v1.json', fixedDurationWindowUsed=False,
               contactScope='Enemy characters explicitly listed in the owned installed trap burst packet; not inferred damage amounts')
    if bernice:
        row.update(measuredOutcome='initial-trap-contact', subsequentBleedDamageRequired=False,
                   subsequentBleedDamageCounted=False)
    return row
