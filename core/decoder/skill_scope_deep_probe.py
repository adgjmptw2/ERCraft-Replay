"""Count-only diagnostics for the unresolved first five; no metric promotion."""
from collections import Counter,defaultdict
try:
    from .requested_skill_scope import exact_cast_lifetimes
except ImportError:
    from requested_skill_scope import exact_cast_lifetimes


def summarize_deep_scope_evidence(players,teams,starts,finishes,states,damages,
                                  spawns,collisions,terminals,projectile_owners,state_rows,
                                  *,state_scripts=(),heals=(),raw_actions=()):
    state_group_by_code={r['code']:r['group'] for r in state_rows}
    output=[]
    for player,info in players.items():
        if info['characterCode'] not in {1,2,3,4,5}: continue
        own_starts=[s for s in starts if s['playerObjectId']==player]
        def kind(obj):
            if obj==player: return 'self'
            if obj in teams: return 'ally' if teams[obj]==teams[player] else 'enemy'
            if obj in {None,0,-1}: return 'none'
            if projectile_owners.get(obj)==player: return 'own-projectile'
            return 'other'
        own_scripts=[s for s in state_scripts if s.get('casterObjectId')==player or s.get('sourceObjectId')==player]
        script_shapes=Counter((s['event'],s['skillIdCode'],s['skillCode'],s['stateGroup'],kind(s['sourceObjectId']),kind(s['casterObjectId']),s['reason']) for s in own_scripts)
        own_raw_actions=[a for a in raw_actions if player in {a.get('casterObjectId'),a.get('stateCasterObjectId'),a.get('sourceObjectId')}]
        action_shapes=Counter((a['skillIdCode'],a.get('stateGroup'),a['wireStatus'],a.get('actionNo'),
            kind(a.get('sourceObjectId')),kind(a.get('casterObjectId')),kind(a.get('stateCasterObjectId')),
            tuple(sorted((kind(t['targetObjectId']),t['hasTargetPosition']) for t in a['targets']))) for a in own_raw_actions)
        own_damage=[]
        for d in damages:
            owner=d['attackerObjectId'] if d['attackerObjectId'] in players else projectile_owners.get(d['attackerObjectId'])
            if owner==player and d['targetObjectId'] in teams and teams[d['targetObjectId']]!=teams[player]: own_damage.append(d)
        own_states=[s for s in states if s.get('casterObjectId')==player and s.get('targetObjectId') in teams and teams[s['targetObjectId']]!=teams[player]]
        stage_rows=[]
        q_damage_keys={(d['tick'],d['targetObjectId']) for d in own_damage if d.get('effectCode')==1003201}
        passive_deltas=Counter()
        passive_state=defaultdict(lambda:None)
        passive_changes=defaultdict(list)
        for s in own_states:
            if (s.get('stateGroup') or state_group_by_code.get(s.get('stateCode')))==1003100:
                passive_changes[(s['tick'],s['targetObjectId'])].append(s)
        for key,changes in sorted(passive_changes.items()):
            tick,target=key
            before=passive_state[target]
            explicit={s.get('stackCount') for s in changes if type(s.get('stackCount')) is int and s['event'] in {'add','CmdUpdateState'}}
            if key in q_damage_keys:
                passive_deltas[(before,tuple(sorted(explicit)),any(s['event']=='remove' for s in changes))]+=1
            if any(s['event']=='remove' for s in changes): passive_state[target]=0
            elif len(explicit)==1: passive_state[target]=next(iter(explicit))
            elif any(s['event']=='add' for s in changes): passive_state[target]=None
        for group in sorted({s['skillGroup'] for s in own_starts}):
            selected=[s for s in own_starts if s['skillGroup']==group]
            lifetimes,reason=exact_cast_lifetimes(selected,finishes,player)
            if reason:
                stage_rows.append({'skillGroup':group,'reason':reason})
                continue
            damage_shapes=Counter()
            state_shapes=Counter()
            attribution_shapes=Counter()
            for d in own_damage:
                if not any(s['tick']<=d['tick']<=end for s,end in lifetimes): continue
                exact_scripts=tuple(sorted({(s['skillIdCode'],s['skillCode'],s['stateGroup'])
                    for s in own_scripts if s['event']=='CmdStartStateSkill' and s['tick']==d['tick']
                    and s['sourceObjectId']==d['targetObjectId'] and s['casterObjectId']==player}))
                target_states=tuple(sorted({s.get('stateGroup') or state_group_by_code.get(s.get('stateCode'))
                    for s in own_states if s['tick']==d['tick'] and s['targetObjectId']==d['targetObjectId'] and s['event']=='add'} - {None}))
                relative_actions=tuple(sorted({(a.get('actionNo'),d['tick']-a['tick'])
                    for a in own_raw_actions if a['sourceObjectId']==player and a['skillIdCode'] in {s['skillIdCode'] for s in selected}
                    and any(start['tick']<=a['tick']<=end and start['tick']<=d['tick']<=end for start,end in lifetimes)}))
                attribution_shapes[(d.get('effectCode'),exact_scripts,target_states,relative_actions)]+=1
                same_tick_slow=[s for s in own_states if s['tick']==d['tick'] and s['targetObjectId']==d['targetObjectId']
                    and (s.get('stateGroup') or state_group_by_code.get(s.get('stateCode')))==1003200]
                shape=(d.get('effectCode'),d.get('isCritical'),d.get('damageFontDisplayType'),d.get('damageType'),d.get('damageIsNull'),
                       ','.join(sorted({s['event'] for s in same_tick_slow})))
                damage_shapes[shape]+=1
            for s in own_states:
                if any(start['tick']<=s['tick']<=end for start,end in lifetimes):
                    state_shapes[(s['event'],s.get('stateCode'),s.get('stateGroup'))]+=1
            stage_rows.append({'skillGroup':group,'castCount':len(selected),
                'damageAttributionCandidates':[dict(effectCode=k[0],sameTickTargetStateSkillStarts=list(k[1]),
                    sameTickTargetStateAddGroups=list(k[2]),sameCastActionRelativeTicks=list(k[3]),count=v)
                    for k,v in sorted(attribution_shapes.items(),key=lambda kv:str(kv[0]))],
                'selfHealSourceCandidates':[dict(stateCode=k[0],effectCode=k[1],count=v) for k,v in sorted(Counter(
                    (h.get('stateCode'),h.get('effectCode')) for h in heals if h.get('targetObjectId')==player
                    and any(s['tick']<=h['tick']<=end for s,end in lifetimes)).items(),key=lambda kv:str(kv[0]))],
                'finishReasonCounts':dict(Counter(str(f.get('reason')) for f in finishes if f.get('playerObjectId')==player and f.get('skillIdCode') in {s['skillIdCode'] for s in selected})),
                'damageShapeCounts':[dict(effectCode=k[0],isCritical=k[1],damageFontDisplayType=k[2],damageType=k[3],
                    damageIsNull=k[4],sameTickFioraQSlowEvents=k[5],count=v) for k,v in sorted(damage_shapes.items(),key=lambda kv:str(kv[0]))],
                'stateChangeCounts':[dict(event=k[0],stateCode=k[1],stateGroup=k[2],count=v) for k,v in sorted(state_shapes.items(),key=lambda kv:str(kv[0]))]})
        projectile_rows=[]
        own_spawns=[s for s in spawns if s['ownerPlayerObjectId']==player]
        own_code_by_object={s['projectileObjectId']:s['projectileCode'] for s in own_spawns}
        own_spawns_by_tick=defaultdict(list)
        own_terminals_by_tick=defaultdict(list)
        for s in own_spawns: own_spawns_by_tick[s['tick']].append(s)
        for t in terminals:
            if t['objectId'] in own_code_by_object: own_terminals_by_tick[t['tick']].append(t)
        all_lifetimes=[]
        for group in {s['skillGroup'] for s in own_starts}:
            life,reason=exact_cast_lifetimes([s for s in own_starts if s['skillGroup']==group],finishes,player)
            if not reason: all_lifetimes.extend(life)
        for code in sorted({s['projectileCode'] for s in own_spawns}):
            objects=[s for s in own_spawns if s['projectileCode']==code]
            ids={s['projectileObjectId'] for s in objects}
            terminal_events=[t for t in terminals if t['objectId'] in ids]
            damage_at_terminal=Counter()
            for terminal in terminal_events:
                for d in own_damage:
                    if d['tick']==terminal['tick']:
                        damage_at_terminal[(terminal['event'],d.get('effectCode'))]+=1
            projectile_rows.append({'projectileCode':code,'spawnCount':len(objects),
                'insideCastLifetimeCounts':dict(Counter(str(start['skillGroup'])
                    for spawn in objects for start,end in all_lifetimes if start['tick']<=spawn['tick']<=end)),
                'sameTickOtherProjectileSpawnCounts':dict(Counter(str(other['projectileCode'])
                    for spawn in objects for other in own_spawns_by_tick[spawn['tick']] if other['projectileCode']!=code)),
                'sameTickOtherProjectileTerminalCounts':[dict(projectileCode=k[0],event=k[1],count=v)
                    for k,v in sorted(Counter((own_code_by_object[t['objectId']],t['event'])
                        for spawn in objects for t in own_terminals_by_tick[spawn['tick']]
                        if own_code_by_object[t['objectId']]!=code).items(),key=lambda kv:str(kv[0]))],
                'sameTickStateScriptCandidates':[dict(event=k[0],skillIdCode=k[1],skillCode=k[2],stateGroup=k[3],count=v)
                    for k,v in sorted(Counter((s['event'],s['skillIdCode'],s['skillCode'],s['stateGroup'])
                        for spawn in objects for s in own_scripts if s['tick']==spawn['tick']).items(),key=lambda kv:str(kv[0]))],
                'sameTickRawActionCandidates':[dict(skillIdCode=k[0],stateGroup=k[1],wireStatus=k[2],count=v)
                    for k,v in sorted(Counter((a['skillIdCode'],a.get('stateGroup'),a['wireStatus'])
                        for spawn in objects for a in own_raw_actions if a['tick']==spawn['tick']).items(),key=lambda kv:str(kv[0]))],
                'terminalEventCounts':dict(Counter(t['event'] for t in terminal_events)),
                'enemyCollisionCount':sum(c['projectileObjectId'] in ids and c['targetObjectId'] in teams and teams[c['targetObjectId']]!=teams[player] for c in collisions),
                'effectCodeAtTerminalCandidates':[dict(event=k[0],effectCode=k[1],count=v) for k,v in sorted(damage_at_terminal.items(),key=lambda kv:str(kv[0]))]})
        output.append({'characterCode':info['characterCode'],'stages':stage_rows,'projectiles':projectile_rows,
                       'stateScriptShapes':[dict(event=k[0],skillIdCode=k[1],skillCode=k[2],stateGroup=k[3],sourceKind=k[4],casterKind=k[5],reason=k[6],count=v)
                           for k,v in sorted(script_shapes.items(),key=lambda kv:str(kv[0]))],
                       'rawActionShapes':[dict(skillIdCode=k[0],stateGroup=k[1],wireStatus=k[2],actionNo=k[3],sourceKind=k[4],casterKind=k[5],stateCasterKind=k[6],targetShapes=list(k[7]),count=v)
                           for k,v in sorted(action_shapes.items(),key=lambda kv:str(kv[0]))],
                       'fioraQPassiveStackObservations':[dict(before=k[0],explicitCounts=list(k[1]),removedSameTick=k[2],count=v)
                           for k,v in sorted(passive_deltas.items(),key=lambda kv:str(kv[0]))]})
    return {'format':'er-first-five-deep-scope-probe.v1','rows':output,
            'interpretation':'anonymous co-occurrence diagnostics; not verified hit attribution','rawReplayRetained':False}
