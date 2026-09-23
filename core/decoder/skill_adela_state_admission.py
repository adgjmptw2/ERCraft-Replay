"""Exclude a pushed pawn producer only when its mandatory new CC would record.

The wire damage effect is shared with Q. This excludes a producer; it does not
assign Q by itself and never treats missing CC alone as a miss.
"""
from contextlib import closing
import hashlib,re
from pathlib import Path
try:
    from .corpus_runtime_source import restore
    from .corpus_state_inventory import retained_state_inventory
except ImportError:
    from corpus_runtime_source import restore
    from corpus_state_inventory import retained_state_inventory

BLOCKED_TYPES={'SlowImmunity','Untargetability','Protectability','Invulnerability',
    'Unstoppable','DebuffImmunity','CCImmunity','CCMovementImmunity',
    'CCStopImmunity','DisplacementImmunity','DanceImmunity','Stasis','VLS'}
ENUM_CORRECTION='schema/state-admission-enum-correction-v1.json'
ENUM_CORRECTION_EVIDENCE=dict(path=ENUM_CORRECTION,sha256=hashlib.sha256(
    (Path(__file__).resolve().parent.parent/ENUM_CORRECTION).read_bytes()).hexdigest())
# Native immunity dispatch is specific to the incoming state. These two
# immunity types reject neither Slow nor its admission; retain them for Airborne.
SLOW_BLOCKED_TYPES=BLOCKED_TYPES-{'DisplacementImmunity','DanceImmunity'}
SLOW_IMMUNITY_RULE='schema/slow-specific-immunity-admission-v1.json'
SLOW_IMMUNITY_EVIDENCE=dict(path=SLOW_IMMUNITY_RULE,sha256=hashlib.sha256(
    (Path(__file__).resolve().parent.parent/SLOW_IMMUNITY_RULE).read_bytes()).hexdigest())
RULE='schema/adela-pushed-pawn-state-admission-v1.json'
RULE_SHA256=hashlib.sha256((Path(__file__).resolve().parent.parent/RULE).read_bytes()).hexdigest()


def excluded_pushed_pawn(damage,player,inputs):
    if damage.get('effectCode')!=1024110:return None
    candidates=[s for s in inputs or [] if s.get('playerObjectId')==player
        and s.get('localTargetObjectId')==damage.get('targetObjectId')
        and s.get('wireOrder')==damage.get('wireOrder')]
    if len(candidates)!=1:return None
    s=candidates[0]
    if (s.get('clientVersion')!='12.3.0' or s.get('sourceAnchorsValidated') is not True
            or s.get('callbackFrameUnique') is not True or s.get('boundaryTick')!=damage.get('tick')
            or s.get('status')!='recorded-state-inventory' or s.get('inventoryScope')!='whole-command-record'
            or s.get('baselineIsAlive') is not True or s.get('baselineIsDyingCondition') is not False
            or s.get('lifeEventsSinceBaseline')!=[] or s.get('issues')!=[]
            or not re.fullmatch('[0-9a-f]{64}',str(s.get('sourceProofSha256','')))):return None
    states=s.get('possibleStates')
    if not isinstance(states,list) or any(not x.get('stateType') or x['stateType'] in BLOCKED_TYPES or x.get('group')==1024280 for x in states):return None
    return dict(method='native-mandatory-pawn-airborne-producer-exclusion',rule=RULE,ruleSha256=RULE_SHA256,
        admissionEnumCorrection=ENUM_CORRECTION_EVIDENCE,
        excludedHandlerGroup=1024220,damageWireOrder=damage['wireOrder'],
        playerObjectId=player,targetObjectId=damage['targetObjectId'],
        sourceProofSha256=s['sourceProofSha256'],baselineRecord=s['baselineRecord'],
        baselinePayloadSha256=s['baselinePayloadSha256'],boundaryTick=s['boundaryTick'])


def unresolved_adela_state_inventory(source,observations,tables,*,client_version):
    candidates={}
    for row in observations:
        for c in row.get('competingDamageEvidence',[]):
            if 1024220 in c['possibleHandlerGroups'] and c['damage']['effectCode']==1024110:
                candidates[(c['playerObjectId'],tuple(c['damage']['wireOrder']))]=c
    if not candidates:return []
    requests=[]
    with closing(source.connect()) as db:
        source_sha=db.execute("SELECT value FROM metadata WHERE key='sourceSha256'").fetchone()[0]
        for (player,order),c in candidates.items():
            d=c['damage']
            rows=list(db.execute("SELECT category,packet_name,decoded_json_zlib FROM packets WHERE record_id=? AND ordinal=?",order))
            if len(rows)!=1 or rows[0][:2]!=('commands','CmdDamage'):raise ValueError('ambiguous Adela source damage anchor')
            raw=restore(rows[0][2])
            if raw['effectCode']!=d['effectCode']:raise ValueError('Adela damage effect mismatch')
            # Bind the private player through an actual start, not a guessed ID map.
            starts=[e['parent']['start'] for r in observations for e in r.get('unresolvedUseEvidence',[])
                    if e['parent']['start'].get('playerObjectId')==player]
            # Optional producer exclusion needs a recorded identity anchor.
            # Without one, retain the original ambiguity and omit this proof.
            if not starts:continue
            for start in starts:
                aa=list(db.execute("SELECT category,packet_name,decoded_json_zlib FROM packets WHERE record_id=? AND ordinal=?",start['wireOrder']))
                if len(aa)!=1 or aa[0][:2]!=('commands','CmdStartSkill'):raise ValueError('Adela parent source anchor unavailable')
                a=restore(aa[0][2])
                if (a['objectId'],a['skillCode'],a['skillId'])!=(raw['attackerId'],start['skillCode'],start['skillIdCode']):raise ValueError('Adela parent source identity mismatch')
            frame=db.execute('SELECT r.tick,(SELECT count(*) FROM records p WHERE p.kind=1 AND p.version=1 AND p.tick=r.tick) FROM records r WHERE r.id=?',(order[0],)).fetchone()
            requests.append(dict(targetObjectId=raw['objectId'],localTargetObjectId=d['targetObjectId'],
                playerObjectId=player,wireOrder=list(order),sourceProofSha256=source_sha,
                sourceAnchorsValidated=True,clientVersion=client_version,callbackFrameUnique=frame==(d['tick'],1),
                throughRecordEnd=True,useCompletedRemovals=True))
    return retained_state_inventory(source,requests,tables['CharacterState'],tables['CharacterStateGroup'],client_version=client_version)
