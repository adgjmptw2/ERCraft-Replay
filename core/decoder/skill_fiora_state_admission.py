"""Fiora Q inner contacts from admitted, mandatory outer Slow absence.

Reuse the reviewed complete-frame inventory, never absence of AddState alone.
"""
from contextlib import closing
import hashlib
import re
from pathlib import Path

try:
    from .corpus_runtime_source import restore
    from .corpus_state_inventory import retained_state_inventory
    from .skill_adela_state_admission import SLOW_BLOCKED_TYPES,ENUM_CORRECTION_EVIDENCE,SLOW_IMMUNITY_EVIDENCE
except ImportError:
    from corpus_runtime_source import restore
    from corpus_state_inventory import retained_state_inventory
    from skill_adela_state_admission import SLOW_BLOCKED_TYPES,ENUM_CORRECTION_EVIDENCE,SLOW_IMMUNITY_EVIDENCE

RULE='schema/fiora-q-slow-admission-v1.json'
RULE_SHA256=hashlib.sha256((Path(__file__).resolve().parent.parent/RULE).read_bytes()).hexdigest()


def admitted_inner_contact(start,damage,player,inputs):
    matches=[s for s in inputs or [] if s.get('playerObjectId')==player
        and s.get('localTargetObjectId')==damage.get('targetObjectId')
        and s.get('wireOrder')==damage.get('wireOrder')
        and s.get('castWireOrder')==start.get('wireOrder')]
    if len(matches)!=1:return None
    s=matches[0]
    if (s.get('clientVersion')!='12.3.0' or s.get('sourceAnchorsValidated') is not True
            or s.get('callbackFrameUnique') is not True or s.get('boundaryTick')!=damage.get('tick')
            or s.get('status')!='recorded-state-inventory' or s.get('inventoryScope')!='whole-command-record'
            or s.get('baselineIsAlive') is not True or s.get('baselineIsDyingCondition') is not False
            or s.get('lifeEventsSinceBaseline')!=[] or s.get('issues')!=[]
            or not re.fullmatch('[0-9a-f]{64}',str(s.get('sourceProofSha256','')))):return None
    states=s.get('possibleStates')
    if not isinstance(states,list) or any(not x.get('stateType') or x['stateType'] in SLOW_BLOCKED_TYPES
                                          or x.get('group')==1003200 for x in states):return None
    return dict(method='native-mandatory-outer-Slow-admission',rule=RULE,ruleSha256=RULE_SHA256,
        admissionEnumCorrection=ENUM_CORRECTION_EVIDENCE,
        incomingSlowImmunityEvidence=SLOW_IMMUNITY_EVIDENCE,
        sourceProofSha256=s['sourceProofSha256'],damageWireOrder=damage['wireOrder'],
        castWireOrder=start['wireOrder'],baselineRecord=s['baselineRecord'],
        baselinePayloadSha256=s['baselinePayloadSha256'],boundaryTick=s['boundaryTick'])


def unresolved_fiora_state_inventory(source,observations,tables,*,client_version):
    """Batch exact raw identity anchors and reuse the complete state inventory."""
    contacts={}
    for row in observations:
        if row.get('skillGroup')!=1003200:continue
        for use in row.get('unresolvedUseEvidence',[]):
            for c in use.get('contacts',[]):
                contacts[(tuple(use['start']['wireOrder']),tuple(c['damageWireOrder']))]=(use['start'],c)
    if not contacts:return []
    ticks=sorted({s['tick'] for s,c in contacts.values()}|{c['tick'] for s,c in contacts.values()})
    placeholders=','.join('?' for _ in ticks)
    with closing(source.connect()) as db:
        anchors={}
        sql="SELECT record_id,ordinal,tick,packet_name,category,decoded_json_zlib FROM packets WHERE category='commands' AND packet_name IN ('CmdStartSkill','CmdDamage') AND tick IN ("+placeholders+')'
        for rid,n,tick,name,cat,blob in db.execute(sql,ticks):
            key=(rid,n)
            if key in anchors:raise ValueError('ambiguous Fiora source anchor')
            anchors[key]=(tick,name,cat,restore(blob))
        source_sha=db.execute("SELECT value FROM metadata WHERE key='sourceSha256'").fetchone()[0]
        frames={tick:count for tick,count in db.execute('SELECT tick,count(*) FROM records WHERE kind=1 AND version=1 AND tick IN ('+placeholders+') GROUP BY tick',ticks)}
    requests=[];mapped={}
    for (so,do),(start,contact) in contacts.items():
        st,sn,sc,s=anchors[so];dt,dn,dc,d=anchors[do]
        if (sn!='CmdStartSkill' or dn!='CmdDamage' or sc!='commands' or dc!='commands'
                or (st,s['skillCode'],s['skillId'])!=(start['tick'],start['skillCode'],65)
                or start['skillCode'] not in range(1003201,1003206)
                or dt!=contact['tick'] or d['attackerId']!=s['objectId'] or d['effectCode']!=1003201):
            raise ValueError('Fiora cast/damage source mismatch')
        local=contact['targetObjectId'];raw=d['objectId']
        if local in mapped and mapped[local]!=raw:raise ValueError('conflicting Fiora target identity')
        mapped[local]=raw
        requests.append(dict(targetObjectId=raw,localTargetObjectId=local,
            playerObjectId=start['playerObjectId'],sourceProofSha256=source_sha,
            wireOrder=list(do),castWireOrder=list(so),throughRecordEnd=True,useCompletedRemovals=True,
            sourceAnchorsValidated=True,clientVersion=client_version,callbackFrameUnique=frames[dt]==1))
    return retained_state_inventory(source,requests,tables['CharacterState'],tables['CharacterStateGroup'],client_version=client_version)
