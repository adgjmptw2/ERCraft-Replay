"""Preserve exact nested projectile fields without inferring a skill parent.

Identity prefixes are validated by the caller. Unsupported/malformed nested
shapes stay explicit; their original bytes remain in the retained corpus.
"""
try:
    from .delta_payloads import DecodeError
except ImportError:
    from delta_payloads import DecodeError

MOVEMENT_TYPES={'Target':'TargetProjectileSnapshot','Direction':'DirectionProjectileSnapshot',
                'InstantArrival':'InstantArrivalProjectileSnapshot','Around':'AroundProjectileSnapshot'}
MOVEMENT_FIELDS={'targetId':'movementTargetObjectId','spawnPosId':'spawnPositionObjectId',
    'accumulatedMoveAmountForTarget':'accumulatedMoveAmountForTarget',
    'timeAfterCreated':'timeAfterCreated','duration':'duration','arriveRate':'arriveRate',
    'targetDirectionEndPos':'targetDirectionEndPos','useOriginalDurationForArrival':'useOriginalDurationForArrival',
    'projectileDirection':'projectileDirection','createdAngle':'createdAngle','distance':'distance',
    'aroundTargetId':'aroundTargetObjectId','aroundTargetPosition':'aroundTargetPosition',
    'totalElapsedAngle':'totalElapsedAngle'}


def projectile_movement_fact(wrapper,identity,definition,decoder):
    row=dict(tick=wrapper['tick'],projectileObjectId=wrapper['objectId'],objectType=wrapper['objectType'],
             projectileCode=identity['projectileCode'],ownerObjectId=identity['ownerObjectId'],
             wireCategory=wrapper.get('wireCategory'),wireOrder=wrapper.get('wireOrder'),
             definitionMovementType=definition.get('type'),fallbackUsed=False)
    def unknown(reason):
        return {**row,'wireStatus':'unresolved-projectile-movement','reasonCode':reason}
    payload=wrapper.get('snapshot')
    if not isinstance(payload,bytes) or not payload or payload[0]!=len(decoder.members_of('ProjectileSnapshot')):
        return unknown('unsupported-projectile-outer-shape')
    kind=MOVEMENT_TYPES.get(definition.get('type'))
    if kind is None and definition.get('type')!='Point':return unknown('unsupported-gameDb-movement-type')
    try:
        outer=decoder.decode_exact(payload,'ProjectileSnapshot')
        if outer.get('code')!=identity['projectileCode'] or outer.get('ownerId')!=identity['ownerObjectId']:
            return unknown('outer-snapshot-identity-disagrees-with-verified-prefix')
        nested=outer['snapshot']
        if not isinstance(nested,bytes):return unknown('nested-movement-payload-missing')
        if definition.get('type')=='Point':
            # WorldProjectile.OverwriteSnapShotByType branches on the runtime
            # IsInstantArrival flag. Point is not universally instant. Its two
            # native branches have distinct MemoryPack member counts (1/5).
            kinds=[name for name in ('InstantArrivalProjectileSnapshot','DirectionProjectileSnapshot')
                   if nested and nested[0]==len(decoder.members_of(name))]
            if len(kinds)!=1:return unknown('point-movement-branch-shape-unrecognized')
            kind=kinds[0]
        if not nested or nested[0]!=len(decoder.members_of(kind)):
            return unknown('movement-member-count-differs-from-exact-definition')
        movement=decoder.decode_exact(nested,kind)
        if any(k not in MOVEMENT_FIELDS and k!='__type' for k in movement):
            return unknown('unreviewed-movement-field')
        optional=outer.get('projectileOptionalSnapshot')
        if optional is not None and (not isinstance(optional,dict) or set(optional)-{'__type','createdPosition','ConvexVertexs','moveToObjectId'}):
            return unknown('unreviewed-projectile-optional-shape')
    except (DecodeError,KeyError,TypeError,ValueError):
        return unknown('exact-nested-snapshot-decode-failed')
    fields={MOVEMENT_FIELDS[k]:v for k,v in movement.items() if k!='__type'}
    if optional is not None:
        fields.update(createdPosition=optional.get('createdPosition'),convexVertices=optional.get('ConvexVertexs'),
                      moveToObjectId=optional.get('moveToObjectId'))
    return {**row,**fields,'wireStatus':'decoded-exact-projectile-movement',
            'outerSnapshotType':'ProjectileSnapshot','movementSnapshotType':kind,
            'outerPayloadByteCount':len(payload),'movementPayloadByteCount':len(nested),
            'optionalFieldsPresent':optional is not None,'projectileSpeed':outer.get('projectileSpeed'),
            'fullByteConsumption':True,'parentSkillInferred':False}
