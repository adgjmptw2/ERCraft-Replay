"""Versioned disabled registrations stay distinct from missing replay evidence."""
from functools import lru_cache
from pathlib import Path
import hashlib,json
try:
    from .requested_skill_hit_rates import GAME_DB_SHA256,_unavailable
except ImportError:
    from requested_skill_hit_rates import GAME_DB_SHA256,_unavailable

DISABLED={
    (28,1028500):('deliverables/sua-initial-r-non-executable-proof-v1.json',
        '51e275bd194dcb5f5ae00280a29b904295536b95e5b1e4889c224b8d4fbf83f9'),
    (44,1044500):('deliverables/echion-initial-r-non-executable-proof-v1.json',
        'c1dbcebfe58d0ab9f100498e7873ee21d2694f0f0c71624b1924047d177a283e'),
}


@lru_cache(maxsize=2)
def certificate(key):
    path,digest=DISABLED[key]
    data=(Path(__file__).resolve().parent.parent/path).read_bytes()
    if hashlib.sha256(data).hexdigest()!=digest:raise ValueError('static applicability certificate hash mismatch')
    proof=json.loads(data)
    if (proof['identity']['skillGroup']!=key[1] or proof['gameDb']['sha256']!=GAME_DB_SHA256
            or proof['normalPlayerCastingAvailable'] is not False or proof['hitRateApplicable'] is not False):
        raise ValueError('static applicability certificate identity mismatch')
    return path,digest


def static_applicability_metric(spec,starts,player):
    key=spec['characterCode'],spec['skillGroup']
    if key not in DISABLED:return None
    path,digest=certificate(key)
    observed=[s for s in starts if s.get('playerObjectId')==player and s.get('skillGroup')==key[1]]
    if observed:
        result=_unavailable(spec,'recorded cast contradicts the versioned disabled-registration proof')
        result.update(applicability='source-static-conflict',observedCastCount=len(observed),contradictingCasts=observed)
    else:
        result=_unavailable(spec,'registered initial slot is unconditionally disabled by the server use guard',
                            status='not-applicable-static-non-executable')
        result.update(applicability='not-applicable',hitRateApplicable=False,
            normalPlayerCastingAvailable=False,additionalReplayRequired=False,observedCastCount=0)
    result.update(staticApplicabilityProof=path,staticApplicabilityProofSha256=digest)
    return result
