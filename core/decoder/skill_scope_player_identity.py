"""Bind source-replay player episodes to a byte-identified private evidence cache."""
import hashlib
import json
from pathlib import Path

try:
    from .skill_scope_evidence_cache import memory_handoff_matches, read_evidence_cache
except ImportError:
    from skill_scope_evidence_cache import memory_handoff_matches, read_evidence_cache


def _memory_cache_identity(cache):
    if type(cache) is not dict:
        raise ValueError('memory evidence handoff must be a dictionary')
    if cache.get('format') != 'er-skill-scope-facts.v1':
        raise ValueError('invalid memory evidence authority')
    if any(cache.get(k) is not False for k in
           ('originalObjectIdsRetained','accountIdentifiersRetained','rawReplayRetained')):
        raise ValueError('memory evidence identity/retention contract mismatch')


def load_bound_player_episodes(cache_path=None, identity_path=None, *, analysis_match_key,
                               players, evidence_cache=None, player_identity=None):
    """Players use replay objectId, characterCode, combatJudgment.personalEpisodes.

    All identities are recorded by the cache builder. No name, character-only,
    or roster-order joins are allowed. The returned IDs are cache-local.
    """
    memory=(evidence_cache is not None or player_identity is not None)
    if memory != (evidence_cache is not None and player_identity is not None):
        raise ValueError('memory evidence and player identity must be handed off together')
    if memory:
        if cache_path is not None or identity_path is not None:
            raise ValueError('memory evidence cannot be mixed with disk paths')
        cache=evidence_cache
        identity=player_identity
        _memory_cache_identity(cache)
        if not memory_handoff_matches(cache, identity):
            raise ValueError('memory evidence is not the exact builder handoff')
    else:
        if cache_path is None or identity_path is None:
            raise ValueError('disk evidence requires both cache and player map paths')
        identity=json.loads(Path(identity_path).read_text(encoding='utf-8'))
    if (identity.get('format')!='private-replay-player-map.v1' or
            identity.get('mappingAuthority')!='captured-during-evidence-cache-object-remapping' or
            identity.get('fallbackUsed') is not False or identity.get('accountIdentifiersRetained') is not False):
        raise ValueError('invalid private player identity authority')
    if memory:
        if identity.get('evidenceStorageMode')!='in-memory':
            raise ValueError('memory player map authority is invalid')
        if identity.get('decoderContractSha256')!=cache.get('decoderContractSha256'):
            raise ValueError('memory player map decoder contract mismatch')
        if identity.get('collectorContractSha256')!=cache.get('collectorContractSha256'):
            raise ValueError('memory player map collector contract mismatch')
    else:
        if hashlib.sha256(Path(cache_path).read_bytes()).hexdigest()!=identity.get('evidenceCacheSha256'):
            raise ValueError('player map belongs to different cache bytes')
        cache=read_evidence_cache(cache_path)
    if identity.get('matchKey')!=analysis_match_key or cache['matchKey']!=analysis_match_key:
        raise ValueError('analysis and player map must reference the same replay SHA-256')
    for field in ('clientVersion','gameDataSha256'):
        if cache[field]!=identity.get(field):raise ValueError('player map version mismatch')
    local={p['objectId']:p['characterCode'] for p in cache['players']}
    mapping={}; mapped=set()
    for row in identity['players']:
        original,cached=row['replayObjectId'],row['cacheObjectId']
        if (type(original) is not int or original<=0 or type(cached) is not int or
                original in mapping or cached in mapped or
                local.get(cached)!=row['characterCode']):
            raise ValueError('non-bijective or foreign player mapping')
        mapping[original]=row; mapped.add(cached)
    if mapped!=set(local):raise ValueError('incomplete cache player map')
    if len({p['objectId'] for p in players})!=len(players) or {p['objectId'] for p in players}!=set(mapping):
        raise ValueError('analysis roster differs from recorded replay player map')
    episodes={}
    for player in players:
        row=mapping[player['objectId']]
        if player['characterCode']!=row['characterCode']:
            raise ValueError('analysis character disagrees with exact player mapping')
        episodes[row['cacheObjectId']]=[dict(episodeId=e.get('episodeId',str(i)),
            startTick=e['startTick'],endTick=e['endTick'])
            for i,e in enumerate(player['combatJudgment']['personalEpisodes'])]
    return cache,episodes
