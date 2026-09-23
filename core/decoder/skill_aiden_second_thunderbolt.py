"""Aiden's automatic or warp-accelerated second lightning, per real projectile.

104652 is created by the first explosion. Optional Warp663 accelerates that
existing object, so neither a fresh Warp spawn nor a Warp cast is required.
"""
from collections import Counter, defaultdict

try:
    from .requested_skill_hit_rates import _result, _unavailable
    from .skill_wire_order import command_order as order
    from .skill_attempt_timing import exact_outcome
    from .skill_ordered_match_end import ordered_winner_match_end
except ImportError:
    from requested_skill_hit_rates import _result, _unavailable
    from skill_wire_order import command_order as order
    from skill_attempt_timing import exact_outcome
    from skill_ordered_match_end import ordered_winner_match_end


def aiden_second_thunderbolt_metric(spec, player, teams, intervals, catalog, inputs):
    """inputs uses normal facts: allProjectileSpawns/actions/collisions/terminals/damages/gaps."""
    combat = lambda e: any(a <= e['tick'] < b for a, b in intervals)
    spawns = [s for s in inputs.get('allProjectileSpawns', [])
              if s['ownerObjectId'] == player and s['projectileCode'] == 104652]
    diag = dict(observedCastCount=len(spawns), observedCombatCastCount=sum(map(combat, spawns)))
    fail = lambda reason: {**_unavailable(spec, reason), **diag}
    if (spec['characterCode'],spec['skillGroup'],spec['mode'],spec['unit']) != (46,1046510,'any','skill-cast'):
        return fail('unsupported Aiden second-lightning scope')
    if player not in teams or any(inputs.get(k) is None for k in ('allProjectileSpawns','actions','collisions','terminals','damages','gaps')):
        return fail('missing second-lightning event stream or caster team')
    definitions = catalog.get('projectileDefinitions', {})
    definition = definitions.get('104652', {})
    if (catalog.get('skillGroups',{}).get('1046500',{}).get('skillId') != 'AidenActive4'
            or catalog.get('skillGroups',{}).get('1046510',{}).get('skillId') != 'AidenActive4Warp'
            or definition.get('prefabName') != 'Projectile_FX_BI_Aiden_Skill04_02'
            or not definition.get('isExplosion') or not definition.get('isExplosionWithoutCollision')):
        return fail('pinned second-lightning identities differ')
    required = {'CmdSpawn','CmdSpawnBatch','CmdProjectileExplosion','CmdProjectileCollision','CmdDestroy',
                'CmdDestroyDelayStart','CmdDamage','CmdPlaySkillActionWithTargets'}
    if any(g.get('count',0) and (g.get('packetName') in required or str(g.get('packetName','')).startswith('ProjectileSnapshot:')) for g in inputs['gaps']):
        return fail('incomplete second-lightning command stream')
    if len({s['projectileObjectId'] for s in spawns}) != len(spawns):
        return fail('second-lightning projectile identity repeated')
    terminal_by_id = defaultdict(list)
    collision_by_id = defaultdict(list)
    for e in inputs['terminals']: terminal_by_id[e['objectId']].append(e)
    for e in inputs['collisions']: collision_by_id[e['projectileObjectId']].append(e)
    markers = [a for a in inputs['actions'] if a['sourceObjectId'] == player and a['skillIdCode'] == 662 and a['actionNo'] == 1]
    own_ids = {s['projectileObjectId'] for s in spawns}
    explosions = [e for e in inputs['terminals'] if e['objectId'] in own_ids and e['event'] == 'CmdProjectileExplosion']
    unknown = {}; uses = []; used_markers = set()
    match_end = ordered_winner_match_end(inputs.get('gameTerminals'),inputs['gaps'])
    for i, spawn in enumerate(spawns):
        oid = spawn['projectileObjectId']; ev = terminal_by_id[oid]
        xs = [e for e in ev if e['event'] == 'CmdProjectileExplosion']
        ends = [e for e in ev if e['event'] in ('CmdDestroyDelayStart','CmdDestroy')]
        cs = collision_by_id[oid]
        if not xs and not ends and not cs and match_end is not None:
            if order(spawn) is not None and order(spawn)<order(match_end):
                uses.append((i,spawn,match_end,set(),dict(projectile=spawn,matchEnd=match_end,
                             closedByActualMatchEnd=True,contacts=[])))
                continue
        if len(xs) != 1 or not ends:
            unknown[i] = 'second-lightning explosion/removal is not closed'; continue
        x = xs[0]
        if any(order(e) is None for e in [spawn,x,*ends,*cs]):
            unknown[i] = 'second-lightning command order missing'; continue
        if (len({(e['event'],e['tick']) for e in ends}) != len(ends)
                or any(sum(e['event'] == name for e in ends)>1 for name in ('CmdDestroyDelayStart','CmdDestroy'))):
            unknown[i] = 'ambiguous second-lightning removal'; continue
        end = min(ends,key=order)
        if not order(spawn) < order(x) < order(end) or not spawn['tick'] <= x['tick'] <= end['tick']:
            unknown[i] = 'second-lightning lifecycle order differs'; continue
        frame_markers = [a for a in markers if a['tick'] == x['tick']]
        frame_explosions = [e for e in explosions if e['tick'] == x['tick']]
        if any(order(e) is None for e in [*frame_markers,*frame_explosions]):
            unknown[i] = 'second-lightning callback boundary order missing'; continue
        # Explosion invokes its OnExplosion marker synchronously before the
        # target loop. A later same-owner explosion begins another callback.
        next_explosion = min((order(e) for e in frame_explosions if order(e)>order(x)),default=None)
        possible = [a for a in frame_markers if order(x)<order(a) and (next_explosion is None or order(a)<next_explosion)]
        if len(possible) != 1:
            unknown[i] = 'second-lightning callback marker is not unique'; continue
        marker = possible[0]; targets = marker.get('targets')
        if (marker.get('wireStatus') != 'decoded-exact-CmdPlaySkillActionWithTargets'
                or not isinstance(targets,list) or len(targets)!=1
                or targets[0].get('targetObjectId') != 0 or targets[0].get('hasTargetPosition') is not True
                or order(marker) in used_markers or order(marker)>=order(end)):
            unknown[i] = 'second-lightning position marker differs'; continue
        used_markers.add(order(marker))
        if (len({c['targetObjectId'] for c in cs}) != len(cs) or len({order(c) for c in cs}) != len(cs)
                or any(c['tick']!=x['tick'] or not order(marker)<order(c)<order(end)
                       or (next_explosion is not None and order(c)>=next_explosion) for c in cs)):
            unknown[i] = 'second-lightning collision loop differs'; continue
        cs = sorted(cs,key=order)
        contacts = set(); evidence = []; bad = None
        for j,c in enumerate(cs):
            target = c['targetObjectId']
            if target not in teams or teams[target] == teams[player]:
                continue
            upper = order(cs[j+1]) if j+1<len(cs) else next_explosion
            damage = [d for d in inputs['damages'] if d['attackerObjectId']==player
                      and d['targetObjectId']==target and d['tick']==x['tick']]
            if any(order(d) is None for d in damage):
                bad = 'second-lightning target damage order missing'; break
            damage = [d for d in damage if order(c)<order(d) and (upper is None or order(d)<upper)
                      and order(d)<order(end) and d.get('effectCode')==0 and d.get('damageType')==2]
            # FX0 alone never attributes an attack. The same projectile's
            # exact per-target collision precedes this callback's DamageTo.
            damage=sorted(damage,key=order)
            adjacent=(bool(damage) and damage[0]['wireOrder']==[c['wireOrder'][0],c['wireOrder'][1]+1])
            if (not damage or (len(damage)!=1 and not adjacent)
                    or type(damage[0].get('damageIsNull')) is not bool):
                bad = 'second-lightning target damage is missing or ambiguous'; break
            competing = [s for s in inputs.get('starts',[]) if s['playerObjectId']==player and s['tick']==x['tick']
                         and (order(s) is None or order(c)<order(s)<order(damage[0]))]
            if competing:
                bad = 'competing cast interrupts second-lightning target callback'; break
            contacts.add((c['tick'],target)); evidence.append(dict(collision=c,damage=damage[0],
                immediatelyFollowingCollision=adjacent,additionalDamageCommandsNotAttributed=damage[1:]))
        if bad:
            unknown[i] = bad; continue
        uses.append((i,spawn,x,contacts,dict(projectile=spawn,explosion=x,marker=marker,removal=end,contacts=evidence)))
    selected = [u for u in uses if combat(u[1])]
    row = _result(spec,[u[3] for u in selected],'static-Aiden-second-lightning-existing-projectile-callback',cast_ticks=[u[1]['tick'] for u in selected])
    reasons = Counter(why for i,why in unknown.items() if combat(spawns[i]))
    row.update(diag,outcomes=[exact_outcome(u[1]['tick'],u[1]['tick'],u[3]) for u in selected],
        unresolvedCombatCastCount=sum(reasons.values()),unresolvedCastReasons=dict(reasons),
        unresolvedNonCombatCastCount=sum(not combat(spawns[i]) for i in unknown),
        executionEvidenceByAttempt=[u[4] for u in selected],
        projectileObjectIds=[u[1]['projectileObjectId'] for u in selected],
        closedByActualMatchEndCount=sum(u[4].get('closedByActualMatchEnd',False) for u in selected),
        denominatorMeaning='Every actual owned104652 second-lightning projectile; automatic and warp-accelerated explosions both included',
        optionalWarpCreatesAttempt=False,optionalWarpRequired=False,
        phase='second-lightning',perUseCompletenessTracked=True,incompleteUsesCountedAsMisses=False,
        fixedDurationWindowUsed=False,effectCodeUsedAsStage=False,damageAmountInferred=False,
        evidenceReview='deliverables/aiden-r2-static-leads-v1.json')
    return row
