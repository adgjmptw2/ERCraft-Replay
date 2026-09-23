"""Development-only Fenrir W launch target to projectile identity evidence."""
from collections import Counter
from .skill_wire_order import command_order as order
from .skill_partial_cast_lifetimes import ordered_cast_records

def launch_parents(spec,starts,finishes,spawns,actions,player,gaps):
    from .skill_development_effect_metrics import development_policy
    if not development_policy().get('enabled'):return {}
    if (spec.get('characterCode'),spec.get('skillGroup'),spec.get('mode'),spec.get('unit'))!=(86,1086300,'any','skill-cast') or any(x is None for x in (finishes,actions,gaps)):
        return {}
    required={'CmdStartSkill','CmdFinishSkill','CmdSpawn','CmdSpawnBatch','CmdPlaySkillAction','CmdPlaySkillActionWithTargets','CmdProjectileCollision'}
    if any(g.get('count',0) and (g.get('packetName') in required or str(g.get('packetName','')).startswith('ProjectileSnapshot:')) for g in gaps):return {}
    own=[s for s in starts if s.get('playerObjectId')==player and s.get('skillGroup')==1086300]
    if any(s.get('skillIdCode')!=1257 or order(s) is None for s in own):return {}
    records,why=ordered_cast_records(own,finishes,player,allow_same_tick_finishes=True)
    if why or any(r['finish'] is not None and order(r['finish']) is None for r in records):return {}
    launches=[a for a in actions if a.get('sourceObjectId')==player and a.get('skillIdCode')==1257 and a.get('actionNo')==31]
    targets=Counter(t.get('targetObjectId') for a in launches for t in a.get('targets',[]))
    command_counts=Counter(order(a) for a in launches)
    object_counts=Counter(p['projectileObjectId'] for p in spawns)
    result={}
    for a in launches:
        ts=a.get('targets',[]);ao=order(a)
        if ao is None or command_counts[ao]!=1 or len(ts)!=1 or a.get('wireStatus')!='decoded-exact-CmdPlaySkillActionWithTargets':continue
        oid=ts[0].get('targetObjectId')
        if targets[oid]!=1 or object_counts[oid]!=1:continue
        p=next(p for p in spawns if p['projectileObjectId']==oid);po=order(p)
        if p.get('ownerObjectId')!=player or p.get('projectileCode')!=108631 or po is None or not po<ao or p['tick']!=a['tick']:continue
        possible=[r for r in records if order(r['start'])<ao and (r['finish'] is None or ao<order(r['finish']))]
        if len(possible)!=1:continue
        r=possible[0];f=r['finish'];s=r['start']
        if not r['complete'] or f is None or order(f) is None or not order(s)<po<ao<order(f) or not s['tick']<=p['tick']<=f['tick']:continue
        result[oid]=dict(start=s,projectileObjectId=oid,spawnOrder=po,actionOrder=ao,startOrder=order(s),finishOrder=order(f))
    return result
