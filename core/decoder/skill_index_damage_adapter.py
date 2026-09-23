"""Connect damage SQL rows to calculator identities using exact wire anchors."""
import sqlite3
from pathlib import Path


def bind_identity(mapping, reverse, native, local):
    if native is None or local is None:
        raise ValueError('missing object identity at an exact packet anchor')
    if mapping.get(native,local)!=local or reverse.get(local,native)!=native:
        raise ValueError('conflicting identity at exact packet anchors')
    mapping[native]=local; reverse[local]=native


def indexed_damage_facts(cache,index_path):
    """No cache mutation; refuse partial coverage or ambiguous object joins."""
    db=sqlite3.connect(Path(index_path).resolve().as_uri()+'?mode=ro',uri=True)
    try:
        meta=dict(db.execute('SELECT key,value FROM metadata'))
        if meta.get('sourceSha256')!=cache['matchKey']:
            raise ValueError('index/cache replay identity mismatch')
        rows=db.execute('SELECT packetId,tick,attackerId,targetId,effectCode,damageType,damageIsNull,'
                        'wireOrderRecord,wireOrderOrdinal FROM damagesV2 ORDER BY packetId').fetchall()
        by_order={(r[7],r[8]):r for r in rows}
        if len(by_order)!=len(rows):raise ValueError('duplicate damage packet order')
        native_to_local={}; local_to_native={}; old_by_order={}
        for old in cache['facts']['damages']:
            if old.get('wireCategory')!='commands' or len(old.get('wireOrder',[]))!=2:
                raise ValueError('cache damage has no exact command order')
            key=tuple(old['wireOrder'])
            if key in old_by_order:raise ValueError('duplicate cache damage order')
            row=by_order.get(key)
            if row is None or (old['tick'],old['effectCode'],old.get('damageType'))!=(row[1],row[4],row[5]):
                raise ValueError('cache damage semantics differ at exact packet anchor')
            bind_identity(native_to_local,local_to_native,row[2],old['attackerObjectId'])
            bind_identity(native_to_local,local_to_native,row[3],old['targetObjectId'])
            old_by_order[key]=old
        if len(old_by_order)!=len(rows):
            raise ValueError('damage stream coverage differs; collect missing identity anchors before merging')
        # Validate player identities independently of damage anchors. In
        # particular, identical character/team pairs never authorize an ID join.
        cached_players={p['objectId']:p for p in cache['players']}
        for native,character,team in db.execute('SELECT objectId,characterCode,team FROM players'):
            if native not in native_to_local:continue
            local=native_to_local[native]; player=cached_players.get(local)
            if player is None or player['characterCode']!=character or cache['teams'].get(str(local))!=team:
                raise ValueError('packet identity disagrees with player snapshot')
        result=[]
        for row in rows:
            old=old_by_order[(row[7],row[8])]
            new=dict(old,attackerObjectId=native_to_local[row[2]],targetObjectId=native_to_local[row[3]],
                     effectCode=row[4],damageType=row[5])
            if row[6] is not None:new['damageIsNull']=bool(row[6])
            else:new.pop('damageIsNull',None)
            result.append(new)
        return result,{'projection':'damagesV2','sourceMatchKey':cache['matchKey'],
                       'packetCount':len(result),'identityAnchorCount':len(native_to_local),
                       'completePacketCoverage':True,'identityMapping':'exact-command-order'}
    finally:
        db.close()
