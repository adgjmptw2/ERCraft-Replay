"""Project recorded Elena freeze counts without publishing private wire graphs."""
from bisect import bisect_right
from collections import Counter
from .skill_scope_episode_projection import project_player_episodes

COUNT_FIELDS=('totalSuccessCount','passiveSuccessCount','ultimateInnerSuccessCount',
              'unresolvedSourceSuccessCount','uniqueEnemyTargetCount')
SOURCES={'passive-full-stack','ultimate-inner','unresolved'}


def _counts(events):
    sources=Counter(e['source'] for e in events)
    return dict(totalSuccessCount=len(events),passiveSuccessCount=sources['passive-full-stack'],
        ultimateInnerSuccessCount=sources['ultimate-inner'],unresolvedSourceSuccessCount=sources['unresolved'],
        uniqueEnemyTargetCount=len({e['targetObjectId'] for e in events}))


def project_elena_freeze_counts(counts,episodes):
    ordered=project_player_episodes([],episodes)['episodes']
    if counts.get('fallbackUsed') is not False:
        raise ValueError('freeze counts cannot use fallback')
    if counts.get('status')=='unavailable':
        return dict(status='unavailable',reason=counts.get('reason'),
                    **{key:None for key in COUNT_FIELDS},fallbackUsed=False)
    if counts.get('status') not in {'complete','partial-source-attribution'}:
        raise ValueError('unsupported freeze count status')
    if (counts.get('countUnit')!='actual-enemy-Frozen-state-application'
            or counts.get('refreshCountedAsNewSuccess') is not False
            or counts.get('unresolvedSourceCountedAsPassive') is not False):
        raise ValueError('unsupported freeze counting contract')
    events=counts.get('events')
    if not isinstance(events,list) or any(not isinstance(e,dict) or type(e.get('tick')) is not int
            or e['tick']<0 or type(e.get('targetObjectId')) is not int
            or e.get('source') not in SOURCES or type(e.get('inCombat')) is not bool for e in events):
        raise ValueError('freeze counts require exact timed application events')
    total=_counts(events);combat=_counts([e for e in events if e['inCombat']])
    if any(type(counts.get(k)) is not int or counts[k]!=v for k,v in total.items()) or any(
            type(counts.get('combat',{}).get(k)) is not int or counts['combat'][k]!=v for k,v in combat.items()):
        raise ValueError('freeze event counts disagree with recorded totals')
    if (counts['status']=='complete') != (total['unresolvedSourceSuccessCount']==0):
        raise ValueError('freeze source attribution status disagrees with events')
    partitions=[[] for _ in ordered];starts=[e['startTick'] for e in ordered]
    for e in events:
        if not e['inCombat']:continue
        index=bisect_right(starts,e['tick'])-1
        if index<0 or e['tick']>=ordered[index]['endTick']:
            raise ValueError('combat freeze has no supplied episode')
        partitions[index].append(e)
    return dict(status=counts['status'],**total,combat=combat,
        episodes=[{**{k:episode[k] for k in ('episodeId','startTick','endTick')},**_counts(local)}
                  for episode,local in zip(ordered,partitions)],
        successEvents=[{k:e[k] for k in ('tick','source','inCombat')} for e in sorted(events,key=lambda e:e['tick'])],
        countUnit='actual-enemy-Frozen-state-application',
        refreshCountedAsNewSuccess=False,oneMultiTargetCastCanProduceMultipleSuccesses=True,
        unresolvedSourceCountedAsPassive=False,sourceUncertaintyDoesNotInvalidateSuccess=True,
        episodeScope='recorded applications in confirmed combat intersection; assigned by application tick',
        fallbackUsed=False)
