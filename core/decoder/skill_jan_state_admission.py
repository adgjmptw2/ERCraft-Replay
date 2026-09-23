"""Jan reinforce absence from exact retained caster inventory at damage boundary."""
from contextlib import closing
import re
from .corpus_runtime_source import restore
from .corpus_state_inventory import retained_state_inventory

def reinforce_absent(event,player,inventory,identity):
    if not identity:return None
    rows=[r for r in inventory or [] if r.get('playerObjectId')==player and r.get('wireOrder')==event.get('wireOrder')]
    if len(rows)!=1:return None
    r=rows[0]
    if (r.get('sourceProofSha256')!=identity.get('sourceSha256') or r.get('clientVersion')!=identity.get('clientVersion')
        or r.get('gameDataSha256')!=identity.get('gameDataSha256') or r.get('clientVersion')!='12.3.0'
        or not re.fullmatch('[0-9a-f]{64}',str(r.get('sourceProofSha256','')))
        or r.get('sourceAnchorsValidated') is not True or r.get('status')!='recorded-state-inventory'
        or r.get('inventoryScope')!='packet-boundary' or r.get('boundaryTick')!=event.get('tick')
        or r.get('issues')!=[] or r.get('baselineIsAlive') is not True
        or r.get('baselineIsDyingCondition') is not False or r.get('lifeEventsSinceBaseline')!=[]
        or not re.fullmatch('[0-9a-f]{64}',str(r.get('baselinePayloadSha256','')))):return None
    if any(tuple(x.get('wireOrder',()))>=tuple(event['wireOrder']) or x.get('wireOrder',[event['wireOrder'][0]])[0]==event['wireOrder'][0] for x in r.get('completedRemovalAnchors',[])):return None
    states=r.get('possibleStates')
    if not isinstance(states,list) or any(type(s.get('group')) is not int or s['group']==1035310 for s in states):return None
    return dict(method='Jan-reinforce-absence-at-exact-damage-boundary',wireOrder=r['wireOrder'],
        sourceProofSha256=r['sourceProofSha256'],baselineRecord=r['baselineRecord'],baselinePayloadSha256=r['baselinePayloadSha256'])

def unresolved_jan_state_inventory(source,observations,tables,*,client_version,source_sha256,game_db_sha256):
    pending={}
    for row in observations:
        for e in row.get('janReinforceBaselineRequests',[]):
            key=(e['playerObjectId'],tuple(e['damage']['wireOrder']))
            if key in pending and pending[key]!=e:raise ValueError('ambiguous Jan requested anchor')
            pending[key]=e
    if not pending:return []
    if client_version!='12.3.0':return []
    requests=[]
    with closing(source.connect()) as db:
        meta=dict(db.execute('SELECT key,value FROM metadata'))
        actual=meta.get('sourceSha256')
        if meta.get('clientVersion')!=client_version:raise ValueError('Jan retained source version mismatch')
        if actual!=source_sha256:raise ValueError('Jan retained source hash mismatch')
        def anchor(e,name):
            rows=list(db.execute("SELECT category,packet_name,decoded_json_zlib FROM packets WHERE record_id=? AND ordinal=? AND category='commands'",e['wireOrder']))
            if len(rows)!=1 or rows[0][:2]!=('commands',name):raise ValueError('ambiguous Jan source anchor')
            tick=db.execute('SELECT tick FROM records WHERE id=?',(e['wireOrder'][0],)).fetchone()
            if tick!=(e['tick'],):raise ValueError('Jan anchor tick mismatch')
            return restore(rows[0][2])
        for (player,_),e in pending.items():
            s,d=e['start'],e['damage'];raw_s=anchor(s,'CmdStartSkill');raw_d=anchor(d,'CmdDamage')
            if (s.get('playerObjectId')!=player or d.get('attackerObjectId')!=player or s.get('skillIdCode')!=486
                or raw_s.get('skillId')!=486 or raw_s.get('skillCode')!=s['skillCode']
                or raw_s.get('objectId')!=raw_d.get('attackerId') or raw_d.get('effectCode')!=d['effectCode']
                or d['effectCode']!=1035301 or tuple(s['wireOrder'])>=tuple(d['wireOrder'])):raise ValueError('Jan raw/local source identity mismatch')
            requests.append(dict(targetObjectId=raw_s['objectId'],playerObjectId=player,wireOrder=d['wireOrder'],
                castWireOrder=s['wireOrder'],sourceProofSha256=actual,sourceAnchorsValidated=True,
                clientVersion=client_version,gameDataSha256=game_db_sha256,useCompletedRemovals=True))
    return retained_state_inventory(source,requests,tables['CharacterState'],tables['CharacterStateGroup'],client_version=client_version)
