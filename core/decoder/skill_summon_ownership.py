"""Exact summon wire types and time-bounded owner chains; no nearest owner.

ObjectType and declared snapshot fields agree with local metadata SHA256
ede0935bbc00dcd93707d279f5a3f63570b8f09fcd9119d4582637f957afed61.
"""
from collections import defaultdict

SUMMON_TYPES={
    9:('SummonCamera','SummonSnapshot'),
    10:('SummonTrap','SummonSnapshot'),
    11:('SummonServant','SummonServantSnapshot'),
    21:('SummonArtifact','SummonSnapshot'),
    25:('SummonRope','SummonRopeSnapshot'),
}


def decode_summon_fact(decoder, wrapper, tick, summon_definitions):
    object_type=wrapper['objectType']
    if object_type not in SUMMON_TYPES:
        raise ValueError('unsupported summon object type')
    enum_name,snapshot_type=SUMMON_TYPES[object_type]
    value=decoder.decode_exact(wrapper.get('snapshot'),snapshot_type)
    owner,code=value.get('ownerId'),value.get('summonId')
    if type(owner) is not int or owner<=0 or type(code) is not int:
        raise ValueError('summon owner/code unavailable')
    definition=summon_definitions.get(code)
    if not definition or definition.get('objectType')!=enum_name:
        raise ValueError('summon code and object type disagree with exact gameDb')
    result={'tick':tick,'objectId':wrapper['objectId'],'objectType':object_type,
            'ownerObjectId':owner,'summonCode':code,'expireTimer':value.get('expireTimer'),
            'timeAfterCreated':value.get('timeAfterCreated'),'snapshotType':snapshot_type,
            'identityVerifiedAgainstGameDb':True}
    if object_type==25:
        for source,target in [('fromId','fromObjectId'),('toId','toObjectId'),
                              ('fromAnchorIdx','fromAnchorIndex'),('toAnchorIdx','toAnchorIndex')]:
            if type(value.get(source)) is not int:raise ValueError('rope endpoint identity unavailable')
            result[target]=value[source]
    return result


def live_summon_owner_resolver(summons, terminals, player_ids):
    """Build one time-aware owner resolver for actual attacks or actions.

Missing old-cache ownership proof remains unavailable. Chains require one spawn
per object and never connect an emission after an observed destruction.
"""
    by_object=defaultdict(list)
    for summon in summons:by_object[summon['objectId']].append(summon)
    destroys=defaultdict(set)
    for event in terminals:
        if event['event']=='CmdDestroy':destroys[event['objectId']].add(event['tick'])
    def owner_chain(object_id,tick,seen):
        if object_id in player_ids:return object_id,[],None
        if object_id in seen:return None,[],'cyclic-summon-owner-chain'
        rows=by_object[object_id]
        if len(rows)!=1:return None,[],'missing-or-ambiguous-summon-owner'
        summon=rows[0]
        if summon.get('identityVerifiedAgainstGameDb') is not True:
            return None,[],'summon-identity-not-verified-against-gameDb'
        if tick<summon['tick'] or any(end<tick for end in destroys[object_id]):
            return None,[],'projectile-outside-observed-owner-lifetime'
        owner,path,reason=owner_chain(summon['ownerObjectId'],tick,seen|{object_id})
        return owner,[summon['summonCode'],*path],reason
    return lambda object_id,tick: owner_chain(object_id,tick,set())


def resolve_projectile_owner_chains(projectiles, summons, terminals, player_ids):
    owner_chain=live_summon_owner_resolver(summons,terminals,player_ids)
    resolved=[];unresolved=[]
    for projectile in projectiles:
        player,path,reason=owner_chain(projectile['ownerObjectId'],projectile['tick'])
        if reason:
            unresolved.append({'projectileObjectId':projectile['projectileObjectId'],'reason':reason})
        else:
            resolved.append({**projectile,'ownerPlayerObjectId':player,'summonCodeChain':path,
                'ownerAttribution':'exact-direct-player' if not path else 'exact-live-summon-owner-chain'})
    return resolved,unresolved
