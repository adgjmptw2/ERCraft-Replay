"""Read CmdHeal player-target events omitted by historical state-heal caches."""
import sqlite3
from pathlib import Path
try:
    from .corpus_runtime_source import restore
    from .skill_index_damage_adapter import bind_identity
except ImportError:
    from corpus_runtime_source import restore
    from skill_index_damage_adapter import bind_identity


def indexed_player_heals(cache,index_path):
    db=sqlite3.connect(Path(index_path).resolve().as_uri()+'?mode=ro',uri=True)
    try:
        if dict(db.execute('SELECT key,value FROM metadata')).get('sourceSha256')!=cache['matchKey']:
            raise ValueError('heal index replay mismatch')
        if db.execute("SELECT count(*) FROM packets WHERE packetName='CmdHeal' AND decodedZlib IS NULL").fetchone()[0]:
            raise ValueError('CmdHeal stream has undecoded packets')
        anchors={(r[4],r[5]):r for r in db.execute(
            'SELECT objectId,tick,skillCode,skillId,wireOrderRecord,wireOrderOrdinal FROM skillStarts')}
        mapping={};reverse={}
        for s in cache['facts']['starts']:
            # Older evidence caches predate exact command-order anchors. They
            # remain valid for their original streams, but cannot authorize an
            # index identity join; augmented indexed starts carry the anchors.
            if s.get('wireCategory') != 'commands' or len(s.get('wireOrder', [])) != 2:
                continue
            r=anchors.get(tuple(s.get('wireOrder',[])))
            if r is None or (r[1],r[2],r[3])!=(s['tick'],s['skillCode'],s['skillIdCode']):
                raise ValueError('exact skill-start identity anchor missing or conflicting')
            bind_identity(mapping,reverse,r[0],s['playerObjectId'])
        players={p['objectId']:p for p in cache['players']}
        native_players=set()
        for native,char,team in db.execute('SELECT objectId,characterCode,team FROM players'):
            local=mapping.get(native)
            if local not in players or players[local]['characterCode']!=char or cache['teams'][str(local)]!=team:
                raise ValueError('player snapshot disagrees with exact skill-start anchors')
            native_players.add(native)
        result=[]
        for pid,tick,rid,ordinal,blob in db.execute(
            "SELECT id,tick,recordId,ordinal,decodedZlib FROM packets WHERE packetName='CmdHeal' AND decodedZlib IS NOT NULL ORDER BY id"):
            d=restore(blob)
            if d.get('objectId') not in native_players:continue
            caster=d.get('casterId')
            result.append(dict(tick=tick,targetObjectId=mapping[d['objectId']],
                casterObjectId=mapping.get(caster),effectCode=d.get('effectCode'),
                wireCategory='commands',wireOrder=[rid,ordinal],sourcePacketId=pid,
                addHp=d.get('addHp'),sourcePacket='CmdHeal'))
        return result
    finally:db.close()
