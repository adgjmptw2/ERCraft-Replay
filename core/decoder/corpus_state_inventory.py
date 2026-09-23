"""Source-derived state inventory for native admission checks.

Keep every state observed since the nearest full snapshot, without extrapolating
timers. A reviewed caller may close an exact group/caster removal from a completed
earlier frame. Removals inside the callback never erase possible presence there.
The inventory alone is not a hit witness.
"""
from bisect import bisect_left
from contextlib import closing
from pathlib import Path
import hashlib,json

try:
    from .corpus_runtime_source import restore
    from .delta_payloads import SchemaDecoder
except ImportError:
    from corpus_runtime_source import restore
    from delta_payloads import SchemaDecoder

STATE_EVENTS=('CmdAddState','CmdAddStateExtended','CmdUpdateState',
              'CmdResetCreateTimeState','CmdPauseState','CmdRemoveState')
RESET_EVENTS=('CmdResetCharacter','CmdResurrection','CmdTeamRevival')
LIFE_EVENTS=('CmdDead','CmdDyingCondition','CmdDyingBlockBeforeDead')
WRAPPER_EVENTS=('CmdSpawn','CmdSpawns','CmdSpawnBatch','CmdChangeToObserver',
                'CmdSpawnCharacterCheat','CmdPracticeSwitchMyCharacter',
                'CmdPracticeSwitchOtherCharacter','CmdSwitchCharacterCheat',
                'CmdChangeObserverToPlayerCheat')


def _wrapper_ids(value):
    """Only structured wrappers; serialized byte arrays are not guessed at."""
    if isinstance(value,dict):
        if 'objectId' in value and 'snapshot' in value:
            yield value['objectId']
        for v in value.values():yield from _wrapper_ids(v)
    elif isinstance(value,list):
        for v in value:yield from _wrapper_ids(v)


def retained_state_inventory(source,requests,state_rows,group_rows,*,client_version,decoded_state_events=None):
    """Optionally share rows from the caller's same-source full state scan.

    Shared rows are (record_id, packet_id, ordinal, category, name, decoded).
    They must include STATE/RESET/WRAPPER/LIFE_EVENTS, before normalization.
    This path changes only I/O; snapshot selection and admission stay identical.
    """
    if not requests:return []
    if client_version not in ('12.3.0','12.4.0'):raise ValueError('state inventory schema version mismatch')
    for r in requests:
        if (type(r.get('targetObjectId'))is not int or not isinstance(r.get('wireOrder'),list)
                or len(r['wireOrder'])!=2 or any(type(n)is not int or n<0 for n in r['wireOrder'])):
            raise ValueError('invalid state inventory boundary')
    states={s['code']:s for s in state_rows};groups={s['group']:s for s in group_rows}
    from .replay_schema_inputs import schema_path_for_version
    path=schema_path_for_version(client_version)
    schema=json.loads(path.read_text(encoding='utf8'))
    decoder=SchemaDecoder({n:v['base'] for n,v in schema['classes'].items()},schema_path=path,client_version=client_version)
    max_record=max(r['wireOrder'][0] for r in requests)
    with closing(source.connect()) as db:
        snapshots=list(db.execute("SELECT record_id,decoded_json_zlib FROM packets WHERE category='fullSnapshot' AND record_id<? ORDER BY record_id,id",(max_record,)))
        ids=[r[0] for r in snapshots]
        if len(ids)!=len(set(ids)):raise ValueError('ambiguous full snapshot record')
        baseline_indexes=[bisect_left(ids,r['wireOrder'][0])-1 for r in requests]
        used={i for i in baseline_indexes if i>=0}
        decoded={i:restore(snapshots[i][1]) for i in used}
        minimum=min((ids[i] for i in used),default=max_record)
        record_ticks=dict(db.execute('SELECT id,tick FROM records WHERE id>? AND id<=?',(minimum,max_record)))
        names=STATE_EVENTS+RESET_EVENTS+WRAPPER_EVENTS+LIFE_EVENTS
        sql='SELECT record_id,ordinal,category,packet_name,decoded_json_zlib FROM packets WHERE packet_name IN ('+','.join('?' for _ in names)+') AND record_id>? AND record_id<=? ORDER BY record_id,id'
        targets={r['targetObjectId'] for r in requests};events=[]
        if decoded_state_events is None:
            rows=((rid,n,cat,name,restore(blob))
                  for rid,n,cat,name,blob in db.execute(sql,(*names,minimum,max_record)))
        else:
            rows=((rid,n,cat,name,d) for rid,pid,n,cat,name,d in
                  sorted(decoded_state_events,key=lambda r:(r[0],r[1]))
                  if minimum<rid<=max_record and name in names)
        for rid,n,cat,name,d in rows:
            affected=({d.get('objectId')} if name not in WRAPPER_EVENTS else set(_wrapper_ids(d)))
            if name in WRAPPER_EVENTS and not affected and name!='CmdSpawns':
                # Unknown wrapper layout is a barrier, not an empty state set.
                affected=targets
            if targets&affected:events.append((rid,n,cat,name,d,affected))
        results=[]
        for request,i in zip(requests,baseline_indexes):
            at=tuple(request['wireOrder']);target=request['targetObjectId']
            out={**request,'statePresenceIsExact':False,'hitRateWitness':False,
                 'expirationInferred':False,'removalUsedToProveAbsence':False,
                 'inventoryScope':'whole-command-record' if request.get('throughRecordEnd') is True else 'packet-boundary'}
            if i<0:
                results.append({**out,'status':'missing-full-snapshot'});continue
            first=decoded[i]
            boundary=db.execute('SELECT tick,kind,version FROM records WHERE id=?',(at[0],)).fetchone()
            if (boundary is None or boundary[1:]!=(1,1) or type(first.get('seq'))is not int
                    or first['seq']>boundary[0]):
                results.append({**out,'status':'invalid-snapshot-boundary'});continue
            wrappers=[u.get('characterSnapshot') for u in first['gameSnapshot']['userList']]
            wrappers=[w for w in wrappers if isinstance(w,dict) and w.get('objectId')==target]
            if len(wrappers)!=1:
                results.append({**out,'status':'target-not-unique-in-snapshot'});continue
            character=decoder.decode_exact(wrappers[0]['snapshot'],'PlayerCharacterSnapshot')
            seen={};issues=[];anchors=[];life=[];removals=[];all_life=[]
            def remember(code,group,caster,origin):
                if group not in groups or code is not None and (code not in states or states[code]['group']!=group):
                    issues.append(dict(reason='unmapped-state',code=code,group=group,origin=origin));return
                key=(group,0 if groups[group]['notCheckCasterId'] else caster)
                row=seen.setdefault(key,dict(group=group,casterId=key[1],stateType=groups[group]['stateType'],
                                            skillId=groups[group]['skillId'],codes=[],origins=[]))
                if code is not None and code not in row['codes']:row['codes'].append(code)
                row['origins'].append(origin)
            for s in character['initialStateEffect']:
                code=s['code'];remember(code,states.get(code,{}).get('group'),s['CasterId'],dict(snapshotRecord=ids[i]))
            for rid,n,cat,name,d,affected in events:
                if (rid<=ids[i] or rid>at[0] or rid==at[0] and n>at[1] and request.get('throughRecordEnd') is not True
                        or target not in affected):continue
                origin=dict(wireOrder=[rid,n],event=name)
                if cat!='commands':issues.append(dict(reason='unsupported-state-event-category',category=cat,**origin))
                elif name in RESET_EVENTS+WRAPPER_EVENTS:
                    issues.append(dict(reason='state-baseline-replacement-not-reconstructed',**origin))
                elif name in LIFE_EVENTS:
                    all_life.append(origin)
                    if rid==at[0]:life.append(origin)
                elif name=='CmdRemoveState':
                    group=d['group']
                    if group not in groups:
                        issues.append(dict(reason='unmapped-state-removal',group=group,**origin))
                    elif request.get('useCompletedRemovals') is True and rid<at[0] and record_ticks[rid]<boundary[0]:
                        # CmdRemove/Update/Reset/Pause.BeforeAction fddcd0:
                        # wire casterId=0 encodes the target itself.
                        caster=0 if groups[group]['notCheckCasterId'] else (d['casterId'] or target)
                        removed=seen.pop((group,caster),None)
                        if removed is not None:removals.append(dict(group=group,casterId=caster,**origin))
                else:
                    code=d.get('code');group=states.get(code,{}).get('group') if code is not None else d.get('group')
                    # CmdAddState.BeforeAction fdc6a0 has the same self-caster
                    # compression; Extended inherits it. Snapshots are full IDs.
                    remember(code,group,d['casterId'] or target,origin);anchors.append(origin)
            out.update(status='recorded-state-inventory' if not issues else 'incomplete-state-inventory',
                baselineRecord=ids[i],baselineSeq=first['seq'],boundaryTick=boundary[0],
                baselinePayloadSha256=hashlib.sha256(snapshots[i][1]).hexdigest(),
                possibleStates=list(seen.values()),stateEventAnchors=anchors,callbackLifeEvents=life,issues=issues,
                baselineIsAlive=character.get('isAlive'),baselineIsDyingCondition=character.get('isDyingCondition'),
                lifeEventsSinceBaseline=all_life,
                completedRemovalAnchors=removals,removalUsedToProveAbsence=bool(removals))
            results.append(out)
    return results


def unresolved_sua_state_inventory(source,observations,tables,*,client_version):
    """Resolve private source IDs from exact cast/damage wire anchors once."""
    from .skill_sua_odyssey_profiles import PROFILES
    contacts=[]
    for row in observations:
        if row.get('skillGroup') not in (1028200,1028510):continue
        unresolved={tuple(e['cast']['wireOrder']) for e in row.get('phaseMetrics',{}).get('center',{}).get('unknownUseEvidence',[])}
        for u in row.get('executionEvidenceByAttempt',[]):
            if tuple(u['start']['wireOrder']) in unresolved:
                contacts.extend((u['start'],d) for d in u['damages'])
    if not contacts:return []
    ids=sorted({e['wireOrder'][0] for s,d in contacts for e in (s,d)})
    with closing(source.connect()) as db:
        sql="SELECT record_id,ordinal,packet_name,category,decoded_json_zlib FROM packets WHERE packet_name IN ('CmdStartSkill','CmdDamage') AND record_id IN ("+','.join('?' for _ in ids)+')'
        anchors={}
        for rid,n,name,cat,blob in db.execute(sql,ids):
            key=rid,n
            if key in anchors:raise ValueError('ambiguous source state anchor')
            anchors[key]=(name,cat,restore(blob))
        source_sha=db.execute("SELECT value FROM metadata WHERE key='sourceSha256'").fetchone()[0]
        callback_states={}
        for rid,n,name,cat,blob in db.execute("SELECT record_id,ordinal,packet_name,category,decoded_json_zlib FROM packets WHERE packet_name IN ('CmdAddState','CmdAddStateExtended') AND record_id IN ("+','.join('?' for _ in ids)+')',ids):
            if cat=='commands':callback_states.setdefault(rid,[]).append((n,name,restore(blob)))
        # A synchronous callback must fit one recorded command frame. Multiple
        # delta records sharing its tick are not silently treated as complete.
        frames={rid:(tick,count) for rid,tick,count in db.execute('SELECT r.id,r.tick,(SELECT count(*) FROM records p WHERE p.kind=1 AND p.version=1 AND p.tick=r.tick) FROM records r WHERE r.id IN ('+','.join('?' for _ in ids)+')',ids)}
    requests=[];mapped={}
    for start,damage in contacts:
        sn,sc,s=anchors[tuple(start['wireOrder'])];dn,dc,d=anchors[tuple(damage['wireOrder'])]
        if (sn!='CmdStartSkill' or dn!='CmdDamage' or sc!='commands' or dc!='commands'
                or (s['skillCode'],s['skillId'])!=(start['skillCode'],start['skillIdCode'])
                or d['attackerId']!=s['objectId'] or d['effectCode']!=damage['effectCode']):
            raise ValueError('state inventory cast/damage anchor mismatch')
        local=damage['targetObjectId'];raw=d['objectId']
        if local in mapped and mapped[local]!=raw:raise ValueError('conflicting state inventory target identity')
        mapped[local]=raw
        accepted_codes={1028121,*[pair[1] for pair in PROFILES[start['skillGroup']]['states'].values()]}
        accepted=[dict(wireOrder=[damage['wireOrder'][0],n],event=name,stateCode=e['code'])
                  for n,name,e in callback_states.get(damage['wireOrder'][0],[])
                  if e['objectId']==raw and e['casterId']==s['objectId']
                  and e['code'] in accepted_codes and n>damage['wireOrder'][1]]
        requests.append(dict(targetObjectId=raw,localTargetObjectId=local,
            playerObjectId=start['playerObjectId'],sourceProofSha256=source_sha,
            wireOrder=damage['wireOrder'],castWireOrder=start['wireOrder'],throughRecordEnd=True,
            useCompletedRemovals=True,
            callbackFrameUnique=frames[damage['wireOrder'][0]]==(damage['tick'],1),
            acceptedBookmarkStates=accepted))
    return retained_state_inventory(source,requests,tables['CharacterState'],tables['CharacterStateGroup'],client_version=client_version)
