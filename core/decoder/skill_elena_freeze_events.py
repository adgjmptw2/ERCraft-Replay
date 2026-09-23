"""Count actual Elena Frozen applications; attribute passive/R only with wire evidence."""
from collections import Counter, defaultdict
try:
    from .skill_wire_order import command_order as order
except ImportError:
    from skill_wire_order import command_order as order


def elena_freeze_events(player,teams,intervals,facts):
    required=('states','stateScripts','damages','allProjectileSpawns','terminals','gaps')
    fail=lambda why:dict(status='unavailable',reason=why,totalSuccessCount=None,fallbackUsed=False)
    if player not in teams or any(facts.get(k) is None for k in required):return fail('missing Elena frozen evidence stream')
    packets={'CmdAddState','CmdAddStateExtended','CmdStartStateSkill','CmdFinishStateSkill','CmdDamage','CmdSpawn','CmdSpawnBatch','CmdDestroyDelayStart'}
    if any(g.get('count',0) and g.get('packetName') in packets for g in facts['gaps']):return fail('incomplete Elena frozen commands')
    frozen=[s for s in facts['states'] if s.get('event')=='add' and s.get('stateCode') in (1050191,1050192,1050193)
            and s.get('casterObjectId')==player and s['targetObjectId'] in teams and teams[s['targetObjectId']]!=teams[player]]
    if any(order(s) is None for s in frozen):return fail('missing exact frozen application order')
    if len({order(s) for s in frozen})!=len(frozen):return fail('duplicate frozen application identity')
    scripts=[s for s in facts['stateScripts'] if s.get('casterObjectId')==player and s.get('skillIdCode') in (729,730)]
    streams=defaultdict(list)
    for s in scripts:
        if s['skillIdCode']==729 and s['stateGroup']==1050120:streams[s['sourceObjectId']].append(s)
    lifetimes=[]
    for target,stream in streams.items():
        if any(order(s) is None for s in stream):continue
        active=None;ambiguous=False
        for s in sorted(stream,key=order):
            if s['event']=='CmdStartStateSkill':
                if active is not None:ambiguous=True
                else:active=s
            elif s['event']=='CmdFinishStateSkill':
                if active is not None and not ambiguous:lifetimes.append((target,active,s))
                active=None;ambiguous=False
    shots=[s for s in facts['allProjectileSpawns'] if s['ownerObjectId']==player and s['projectileCode']==105011]
    inner=[d for d in facts['damages'] if d['attackerObjectId']==player and d['effectCode']==1050502]
    terminals=defaultdict(dict)
    for e in facts['terminals']:
        if e['event']=='CmdDestroyDelayStart' and order(e) is not None:terminals[e['objectId']][order(e)]=e
    events=[]
    for s in sorted(frozen,key=order):
        target=s['targetObjectId'];origin='unresolved';evidence={}
        freeze_scripts=[x for x in scripts if x['skillIdCode']==730 and x['stateGroup']==1050190
                        and x['event']=='CmdStartStateSkill' and x['sourceObjectId']==target
                        and x['tick']==s['tick'] and order(x) is not None and order(x)>order(s)]
        # A later, separate same-target freeze in the frame cannot reuse this
        # state's script start or a previous R damage callback.
        next_add=min([order(x) for x in frozen if x['targetObjectId']==target and order(x)>order(s)],default=None)
        freeze_scripts=[x for x in freeze_scripts if next_add is None or order(x)<next_add]
        r_matches=[]
        candidate_inner=[d for d in inner if d['tick']==s['tick'] and d['targetObjectId']==target
                         and (order(d) is None or (order(d)<order(s) and not any(
                             x['targetObjectId']==target and order(d)<order(x)<order(s) for x in frozen)))]
        for d in candidate_inner:
            if order(d) is None:continue
            for p in shots:
                ends=list(terminals[p['projectileObjectId']].values())
                if len(ends)==1 and order(p) is not None and ends[0]['tick']==s['tick'] and order(p)<order(d)<order(s)<order(ends[0]):
                    r_matches.append((d,p,ends[0]))
        parents=[(start,end) for t,start,end in lifetimes if t==target and end['tick']==s['tick']
                 and end.get('reason')==4 and order(start)<order(s)<order(end)]
        if len(freeze_scripts)==1:
            if len(r_matches)==1:
                d,p,end=r_matches[0]
                if d.get('damageType')==2 and type(d.get('damageIsNull')) is bool:
                    origin='ultimate-inner';evidence=dict(innerDamage=d,projectile=p,projectileEnd=end)
            elif not candidate_inner and len(parents)==1:
                start,end=parents[0]
                # Full-stack Process creates Frozen, whose Start removes the
                # full-stack state. That exact finish follows the new script.
                # R can also remove it, hence an actual R callback takes priority.
                if order(freeze_scripts[0])<order(end):
                    origin='passive-full-stack';evidence=dict(fullStackStart=start,fullStackFinish=end)
            evidence['frozenScriptStart']=freeze_scripts[0]
        events.append(dict(tick=s['tick'],targetObjectId=target,source=origin,state=s,evidence=evidence,
                           inCombat=any(a<=s['tick']<b for a,b in intervals)))
    def counts(rows):
        c=Counter(e['source'] for e in rows)
        return dict(totalSuccessCount=len(rows),passiveSuccessCount=c['passive-full-stack'],
                    ultimateInnerSuccessCount=c['ultimate-inner'],unresolvedSourceSuccessCount=c['unresolved'],
                    uniqueEnemyTargetCount=len({e['targetObjectId'] for e in rows}))
    total=counts(events);combat=counts([e for e in events if e['inCombat']])
    episodes=[dict(episodeIndex=i,startTick=a,endTick=b,**counts([e for e in events if a<=e['tick']<b])) for i,(a,b) in enumerate(intervals)]
    return dict(status='complete' if not total['unresolvedSourceSuccessCount'] else 'partial-source-attribution',**total,
        combat=combat,episodes=episodes,events=events,countUnit='actual-enemy-Frozen-state-application',
        refreshCountedAsNewSuccess=False,oneMultiTargetCastCanProduceMultipleSuccesses=True,
        stateSkillCodeUsedToDistinguishUltimate=False,unresolvedSourceCountedAsPassive=False,fallbackUsed=False)
