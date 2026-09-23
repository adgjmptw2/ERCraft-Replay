"""Ordered Charlotte Q installation, source-object projectile and mark execution.

This produces private per-use evidence. It does not promote the scope authority
or silently substitute detection for mark/slow application.
"""
from collections import Counter, defaultdict

try:
    from .skill_partial_cast_lifetimes import ordered_cast_records
    from .skill_wire_order import command_order as order
    from .skill_ordered_match_end import ordered_winner_match_end
    from .requested_skill_hit_rates import _result, _unavailable
    from .skill_action_stage_evidence import load_exact_skill_ids
except ImportError:
    from skill_partial_cast_lifetimes import ordered_cast_records
    from skill_wire_order import command_order as order
    from skill_ordered_match_end import ordered_winner_match_end
    from requested_skill_hit_rates import _result, _unavailable
    from skill_action_stage_evidence import load_exact_skill_ids


class UnassignedApplications(ValueError):
    def __init__(self, events, uses):
        super().__init__('unassigned Charlotte mark/slow applications: ' + str(len(events)))
        self.events = events
        self.partial_uses = uses


def charlotte_q_lineage(facts, player, teams, intervals):
    required = ('starts', 'finishes', 'actions', 'objects', 'summons',
                'projectileMovement', 'collisions', 'terminals', 'states',
                'stateScripts', 'damages', 'deaths', 'gaps')
    if any(facts.get(k) is None for k in required):
        raise ValueError('missing Charlotte Q evidence stream')
    packet_types = {'CmdStartSkill', 'CmdFinishSkill', 'CmdSpawn', 'CmdSpawnBatch',
                    'CmdPlaySkillActionWithTargets', 'CmdProjectileCollision',
                    'CmdProjectileArrived', 'CmdDestroy', 'CmdDead', 'CmdAddState',
                    'CmdAddStateExtended', 'CmdStartStateSkill', 'CmdFinishStateSkill', 'CmdDamage'}
    if any(g.get('count', 0) and g.get('packetName') in packet_types for g in facts['gaps']):
        raise ValueError('incomplete Charlotte Q packet stream')
    teams = {int(k): v for k, v in teams.items()}
    if player not in teams:
        raise ValueError('missing caster team')
    enemy = lambda who: who in teams and teams[who] != teams[player]
    roots = [s for s in facts['starts'] if s['playerObjectId'] == player and s['skillGroup'] == 1073200]
    if any(s['skillIdCode'] != 1070 or s['skillCode'] not in range(1073201, 1073206) for s in roots):
        raise ValueError('Charlotte Q wire/code mismatch')
    records, why = ordered_cast_records(roots, facts['finishes'], player, allow_same_tick_finishes=True)
    if why:
        raise ValueError(why)
    objects = defaultdict(list)
    for o in facts['objects']:
        objects[o['objectId']].append(o)
    cameras = {}
    for c in facts['summons']:
        if c['ownerObjectId'] != player or c['summonCode'] != 1500:
            continue
        os = objects[c['objectId']]
        if c['objectId'] in cameras or c.get('identityVerifiedAgainstGameDb') is not True or len(os) != 1 or order(os[0]) is None or os[0]['tick'] != c['tick']:
            raise ValueError('ambiguous Charlotte installation identity')
        cameras[c['objectId']] = {**c, 'wireOrder': os[0]['wireOrder'], 'wireCategory': 'commands'}
    actions = defaultdict(list)
    for a in facts['actions']:
        if (a['sourceObjectId'], a['skillIdCode'], a['actionNo']) != (player, 1070, 3):
            continue
        if order(a) is None or a.get('wireStatus') != 'decoded-exact-CmdPlaySkillActionWithTargets':
            raise ValueError('unordered Charlotte installation action')
        rr = [i for i, r in enumerate(records) if order(r['start']) < order(a) and
              (r['finish'] is None or order(a) < order(r['finish']))]
        if len(rr) != 1:
            raise ValueError('installation action lacks unique cast')
        actions[rr[0]].append(a)
    shots = defaultdict(list)
    for s in facts['projectileMovement']:
        if s.get('projectileCode') == 107321 and s.get('ownerObjectId') == player:
            if s.get('spawnPositionObjectId') not in cameras:
                raise ValueError('tracking projectile has no explicit installed source')
            shots[s['spawnPositionObjectId']].append(s)
    marks = [s for s in facts['stateScripts'] if s.get('casterObjectId') == player and
             s['skillIdCode'] == 1071 and s.get('stateGroup') == 1073200]
    owned_states = [s for s in facts['states'] if s.get('casterObjectId') == player and
                    s.get('event') == 'add' and s.get('stateCode') in (1073201, *range(1073211, 1073216))]
    if any(order(s) is None for s in marks + owned_states):
        raise ValueError('missing ordered mark/slow evidence')
    contact_by_projectile, contacting_projectiles = defaultdict(list), defaultdict(set)
    owned_projectiles = {s['projectileObjectId'] for ss in shots.values() for s in ss}
    for c in facts['collisions']:
        contact_by_projectile[c['projectileObjectId']].append(c)
        if c['projectileObjectId'] in owned_projectiles:
            contacting_projectiles[(c['tick'], c['targetObjectId'])].add(c['projectileObjectId'])
    finish_frames = Counter(order(s)[0] for s in marks if s['event'] == 'CmdFinishStateSkill' and order(s) is not None)
    match_end = ordered_winner_match_end(facts.get('gameTerminals'), facts['gaps'])
    uses, assigned, assigned_states, assigned_mark_starts = [], set(), set(), set()
    for i, r in enumerate(records):
        start = r['start']
        use = {'start': start, 'finish': r['finish'], 'combat': any(a <= start['tick'] < b for a, b in intervals),
               'camera': None, 'shots': [], 'detectionContacts': [], 'markContacts': [], 'slowContacts': [],
               'unknownReasons': [], 'cancelledBeforeInstallation': False}
        uses.append(use)
        unknown = use['unknownReasons']
        if not r['complete']:
            unknown.append('open-parent-cast')
            continue
        aa = actions[i]
        if not aa and r['finish']['reason'] == 3:
            use['cancelledBeforeInstallation'] = True
            continue
        if len(aa) != 1 or len(aa[0].get('targets', [])) != 1:
            unknown.append('missing-or-ambiguous-installation')
            continue
        a = aa[0]
        cid = a['targets'][0]['targetObjectId']
        cam = cameras.get(cid)
        if cam is None or cid in assigned or not order(start) < order(cam) < order(a) or cam['tick'] != a['tick']:
            raise ValueError('installation target is not its new owned summon')
        assigned.add(cid)
        use.update(camera=cam, installationAction=a)
        ends = [t for t in facts['terminals'] if t['objectId'] == cid and t['event'] == 'CmdDestroy']
        if not ends:
            ends = [d for d in facts['deaths'] if d['deadObjectId'] == cid and d['event'] == 'CmdDead' and d.get('isDyingBlockDead') is False]
        if not ends and match_end is not None:
            ends = [match_end]
        if len(ends) != 1 or ends[0]['tick'] < cam['tick']:
            unknown.append('open-installed-summon')
        else:
            use['cameraEnd'] = ends[0]
        for shot in shots[cid]:
            detail = {'spawn': shot, 'markExecutions': []}
            use['shots'].append(detail)
            if shot.get('fullByteConsumption') is not True or shot.get('fallbackUsed') is not False or order(shot) is None or order(shot) <= order(cam):
                unknown.append('invalid-tracking-projectile-source')
                continue
            if 'cameraEnd' in use and (shot['tick'] > use['cameraEnd']['tick'] or
               shot['tick'] == use['cameraEnd']['tick'] and (order(use['cameraEnd']) is None or order(shot) >= order(use['cameraEnd']))):
                unknown.append('projectile-outside-summon-lifetime')
                continue
            target = shot.get('movementTargetObjectId')
            if enemy(target):
                use['detectionContacts'].append([shot['tick'], target])
            pid = shot['projectileObjectId']
            contacts = contact_by_projectile[pid]
            terminal = [t for t in facts['terminals'] if t['objectId'] == pid and t['event'] in ('CmdProjectileArrived', 'CmdDestroy') and t['tick'] >= shot['tick']]
            if not terminal:
                unknown.append('open-tracking-projectile')
            if any(c['tick'] < shot['tick'] or terminal and c['tick'] > min(t['tick'] for t in terminal) for c in contacts):
                unknown.append('collision-outside-projectile-lifetime')
            detail['collisions'] = contacts
            detail['terminals'] = terminal
            for contact in contacts:
                candidates = [s for s in owned_states if s.get('stateCode') == 1073201 and
                              s.get('casterObjectId') == player and s.get('targetObjectId') == contact['targetObjectId'] and
                              s['tick'] == contact['tick'] and order(s) is not None and order(s) > order(shot)]
                competing = contacting_projectiles[(contact['tick'], contact['targetObjectId'])] - {pid}
                if competing or len(candidates) > 1:
                    unknown.append('ambiguous-mark-application')
                    continue
                if not candidates:
                    continue  # A recorded collision need not successfully apply a state.
                added = candidates[0]
                assigned_states.add(order(added))
                ss = [s for s in marks if s['event'] == 'CmdStartStateSkill' and s['sourceObjectId'] == added['targetObjectId'] and
                      s['tick'] == added['tick'] and order(s) is not None and order(s) > order(added)]
                if len(ss) != 1:
                    unknown.append('missing-mark-state-start')
                    continue
                ms = ss[0]
                assigned_mark_starts.add(order(ms))
                lifecycle = sorted([s for s in marks if s['sourceObjectId'] == ms['sourceObjectId'] and order(s) is not None and order(s) > order(ms)], key=order)
                mf = lifecycle[0] if lifecycle and lifecycle[0]['event'] == 'CmdFinishStateSkill' else None
                execution = dict(markAdd=added, markStart=ms, markFinish=mf, slowApplications=[], explosionDamages=[])
                detail['markExecutions'].append(execution)
                if enemy(added['targetObjectId']):
                    use['markContacts'].append([added['tick'], added['targetObjectId']])
                if mf is None:
                    unknown.append('open-mark-state')
                    continue
                if finish_frames[order(mf)[0]] != 1:
                    unknown.append('ambiguous-same-frame-mark-finishes')
                    continue
                execution['explosionDamages'] = [d for d in facts['damages'] if d.get('attackerObjectId') == player and
                    d.get('effectCode') == 1073202 and d.get('damageType') == 2 and d['tick'] == mf['tick'] and
                    order(d) is not None and order(d)[0] == order(mf)[0] and order(d) > order(mf)]
                slow = [s for s in owned_states if s.get('stateCode') in range(1073211, 1073216) and
                        s.get('casterObjectId') == player and s['tick'] == mf['tick'] and order(s) is not None and
                        order(s)[0] == order(mf)[0] and order(s) > order(mf)]
                execution['slowApplications'] = slow
                assigned_states.update(order(s) for s in slow)
                use['slowContacts'].extend([s['tick'], s['targetObjectId']] for s in slow if enemy(s['targetObjectId']))
    if assigned != set(cameras):
        raise ValueError('unassigned summon prevents complete cast/cancellation proof')
    # An event without a parent cannot disappear into a successful zero count.
    orphan_states = [s for s in owned_states if order(s) not in assigned_states]
    if orphan_states:
        raise UnassignedApplications(orphan_states, uses)
    if any(s['event'] == 'CmdStartStateSkill' and order(s) not in assigned_mark_starts for s in marks):
        raise ValueError('unassigned Charlotte mark state execution')
    return uses


def charlotte_q_metric(spec, facts, player, teams, intervals, catalog):
    method = 'static-Charlotte-Q-installation-tracking-mark-and-slow-lineage'
    if (spec['characterCode'], spec['skillGroup'], spec['mode'], spec['unit']) != (73, 1073200, 'any', 'skill-cast'):
        return _unavailable(spec, 'unsupported Charlotte Q scope')
    ids = load_exact_skill_ids()
    if ids.get('CharlotteActive1') != 1070 or ids.get('CharlotteActive1MarkState') != 1071 or catalog['skillGroups'].get('1073200', {}).get('skillId') != 'CharlotteActive1':
        return _unavailable(spec, 'pinned Charlotte Q identity mismatch')
    try:
        uses = charlotte_q_lineage(facts, player, teams, intervals)
    except ValueError as error:
        return _unavailable(spec, str(error))
    combat = [u for u in uses if u['combat']]
    closed = [u for u in combat if not u['unknownReasons'] and not u['cancelledBeforeInstallation']]
    unknown = Counter(reason for u in combat for reason in set(u['unknownReasons']))
    ticks = [u['start']['tick'] for u in closed]
    phase_fields = {'detection': 'detectionContacts', 'mark': 'markContacts', 'slow': 'slowContacts'}
    contacts = [{tuple(c) for field in phase_fields.values() for c in u[field]} for u in closed]
    row = _result(spec, contacts, method, cast_ticks=ticks)
    row['phaseMetrics'] = {phase: _result(spec, [{tuple(c) for c in u[field]} for u in closed], method + ':' + phase, cast_ticks=ticks)
                           for phase, field in phase_fields.items()}
    phase_outcomes = []
    for u in closed:
        executions = [e for shot in u['shots'] for e in shot['markExecutions']]
        executed = {'detection': bool(u['shots']), 'mark': any(s.get('collisions') for s in u['shots']),
                    'slow': any(e['explosionDamages'] or e['slowApplications'] for e in executions)}
        phase_outcomes.append({phase: dict(executed=executed[phase], hitCount=int(bool(u[field])),
            distinctEnemyCount=len({target for tick, target in u[field]}),
            firstHitTick=min((tick for tick, target in u[field]), default=None)) for phase, field in phase_fields.items()})
    row.update(observedCastCount=len(uses), observedCombatCastCount=len(combat),
               unresolvedCombatCastCount=sum(bool(u['unknownReasons']) for u in combat),
               unresolvedCastReasons=dict(unknown), perUseCompletenessTracked=True,
               incompleteUsesCountedAsMisses=False,
               cancelledBeforeAttackCount=sum(u['cancelledBeforeInstallation'] for u in combat),
               unresolvedNonCombatCastCount=sum(bool(u['unknownReasons']) for u in uses if not u['combat']),
               executionEvidenceByAttempt=closed,
               phaseOutcomesByAttempt=phase_outcomes,
               contactDetailsByAttempt=[[dict(hitTick=t, targetObjectId=target, phase=phase)
                   for phase, field in phase_fields.items() for t, target in sorted({tuple(c) for c in u[field]})] for u in closed],
               contactScope='Union of actual enemy detection, mark application and slow application; each phase also reported separately',
               evidenceReview='deliverables/charlotte-q-static-proof-v1.json', fixedDurationWindowUsed=False)
    return row
