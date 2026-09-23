"""Actual removal commands close collision activity before visual retirement.

CmdDestroyDelayStart is emitted after CurInWorldType becomes 2. GameServer's
projectile collision loop processes only type 1. CmdDestroy also definitively
ends collision activity. Neither an arrival nor a predicted duration is an end.
"""
from collections import defaultdict


def collision_only_outcome(definition):
    """Active collision removal does not close independent explosion/follow-up work."""
    return (definition.get('collisionEnabled') is True
            and not definition.get('isExplosion') and not definition.get('isExplosionWithoutCollision')
            and not definition.get('collisionAfterArrival') and not (definition.get('lifeTimeAfterArrival') or 0))


def recorded_no_explosion_end(spawn, events, collisions, gaps):
    """Explicit NoExplosion (8) plus removal; never infer it from silence.

    ProjectileDestroyedResultType.NoExplosion=8; AffectExplosion RVA 0x33238e0
    returns !(resultType & 8). Require the pure reviewed value here.
    """
    from .skill_wire_order import command_order
    required={'CmdSpawn','CmdProjectileCollision','CmdProjectileExplosion',
              'CmdProjectileDestroyedByExternalObject','CmdDestroyDelayStart','CmdDestroy'}
    if gaps is None or any(g.get('count',0) and g.get('packetName') in required for g in gaps):return None
    oid=spawn['projectileObjectId']
    own=[e for e in events if e.get('objectId')==oid]
    if any(e.get('event')=='CmdProjectileExplosion' for e in own):return None
    blocked=[e for e in own if e.get('event')=='CmdProjectileDestroyedByExternalObject']
    if len(blocked)!=1 or blocked[0].get('resultType')!=8:return None
    if any(c['projectileObjectId']==oid for c in collisions):return None
    end=projectile_active_end_records(own).get(oid)
    if not end or not end.get('complete'):return None
    removals=[e for e in own if e.get('event') in {'CmdDestroyDelayStart','CmdDestroy'} and e['tick']==end['endTick']]
    ordered=[spawn,blocked[0],*removals]
    if any(command_order(e) is None for e in ordered):return None
    if not spawn['tick']<=blocked[0]['tick']<=end['endTick']:return None
    if not command_order(spawn)<command_order(blocked[0]):return None
    if any(command_order(e)<=command_order(blocked[0]) for e in removals):return None
    return dict(end,closureKind='recorded-external-no-explosion',resultType=8)


def projectile_active_end_records(terminals):
    # The execution context owns the cache, so inputs never leak across games.
    if __package__:
        from .skill_execution_plan import projectile_end_index
    else:
        from skill_execution_plan import projectile_end_index
    return projectile_end_index(terminals, _build_projectile_active_end_records)


def projectile_gameplay_end_records(terminals,spawns,collisions,game_terminals,gaps):
    """Close gameplay at an explicit winner end, without inventing removal packets."""
    from .skill_ordered_match_end import ordered_winner_match_end
    from .skill_wire_order import command_order
    ends=dict(projectile_active_end_records(terminals))
    end=ordered_winner_match_end(game_terminals,gaps)
    required={'CmdSpawn','CmdProjectileCollision','CmdDestroy','CmdDestroyDelayStart'}
    if end is None or any(g.get('count',0) and g.get('packetName') in required for g in gaps):return ends
    for shot in spawns:
        oid=shot['projectileObjectId']
        if oid in ends:continue  # Conflicting removal evidence is never overridden.
        if command_order(shot) is None or command_order(shot)>=command_order(end) or shot['tick']>end['tick']:continue
        contacts=[c for c in collisions if c['projectileObjectId']==oid]
        if any(command_order(c) is None or command_order(c)>command_order(end) or c['tick']>end['tick'] for c in contacts):continue
        ends[oid]=dict(complete=True,endTick=end['tick'],terminalCommands=[],
                      closureKind='actual-winner-match-end',gameEndWireOrder=end['wireOrder'],
                      syntheticRemovalCreated=False)
    return ends


def _build_projectile_active_end_records(terminals):
    events=defaultdict(lambda:defaultdict(set))
    for t in terminals:
        if t.get('event') in {'CmdDestroyDelayStart','CmdDestroy'}:
            events[t['objectId']][t['event']].add(t['tick'])
    result={}
    for oid,types in events.items():
        if any(len(ticks)!=1 for ticks in types.values()):
            result[oid]=dict(complete=False,reason='multiple-removal-times');continue
        end={name:next(iter(ticks)) for name,ticks in types.items()}
        if ('CmdDestroyDelayStart' in end and 'CmdDestroy' in end and end['CmdDestroy']<end['CmdDestroyDelayStart']):
            result[oid]=dict(complete=False,reason='final-destroy-before-removal-start');continue
        tick=min(end.values())
        result[oid]=dict(complete=True,endTick=tick,terminalCommands=sorted(name for name,t in end.items() if t==tick),
                         observedRemovalTicks=end)
    return result
