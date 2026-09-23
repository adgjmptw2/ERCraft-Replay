"""Add only independently rechecked pre-episode casts with recorded hit links."""
from collections import Counter
from copy import deepcopy


def episode_at(episodes,tick):
 return next((e for e in episodes if e['startTick']<=tick<e['endTick']),None)


def exact_opening_candidates(row,episodes,starts,source_group):
 if row.get('status') not in {'calculable-observed','calculable-experimental'}:return []
 # Recorded event ticks do not make a nearest-family producer assignment exact.
 # This path cannot supply independent evidence for expanding episode scope.
 if row.get('nearestCastUsed') is True:return []
 assumption=row.get('delayedAttributionAssumption')
 if isinstance(assumption,str) and ('latest preceding' in assumption.lower() or 'nearest' in assumption.lower()):return []
 if (row.get('method')=='development-provisional-family-effects-with-recorded-object-priority'
     and (row.get('recordedCastOutcomesComplete') is False or row.get('binaryCastSuccessComplete') is False)):return []
 outcomes=row.get('outcomes') or [];selected=[]
 estimated={e.get('startTick',e.get('castTick')) for e in row.get('estimatedContactsByCast',[]) if isinstance(e,dict)}
 confirmed=set(row.get('confirmedHitCastTicks',[]))
 for n,o in enumerate(outcomes):
  if len(o)!=4 or o[1]!=1 or type(o[3]) is not int or episode_at(episodes,o[0]):continue
  e=episode_at(episodes,o[3])
  if e is None or o[0]>=e['startTick']:continue
  if row.get('targetCohort') in {'ally','either-team'}:continue
  if any(row.get(k) for k in ('regionEstimateMayMisclassify','attributionEstimateMayMisclassify')):continue
  if (row.get('attributionEstimateMayOvercount') or o[0] in estimated) and o[0] not in confirmed:continue
  if any(row.get(k,0) for k in ('estimatedAttributionCastCount','estimatedRegionCastCount')) and o[0] not in confirmed:continue
  policies=row.get('policySuccessByAttempt')
  if policies is not None and (n>=len(policies) or policies[n] is not None):continue
  actual=[s for s in starts if s.get('tick')==o[0] and s.get('skillGroup')==source_group and isinstance(s.get('wireOrder'),list) and len(s['wireOrder'])==2 and all(type(x) is int for x in s['wireOrder']) and s.get('wireCategory')=='commands']
  if len(actual)!=1:continue
  s=actual[0]
  selected.append(dict(castTick=o[0],firstHitTick=o[3],episodeId=e['episodeId'],outcome=o.copy(),skillGroup=source_group,skillCode=s.get('skillCode'),skillIdCode=s.get('skillIdCode'),castWireOrder=s['wireOrder']))
 # A projectile/phase metric may have several outcomes for one actual cast.
 # One earliest recorded hit admits the whole cast once to one episode.
 by_cast={}
 for u in selected:
  if u['castTick'] not in by_cast or u['firstHitTick']<by_cast[u['castTick']]['firstHitTick']:by_cast[u['castTick']]=u
 return [{**by_cast[o[0]],'outcome':o.copy()} for o in outcomes if o[0] in by_cast]


def _preserves_base(base,new,openings,*,phase=False):
 empty=base.get('status')=='observed-no-combat-cast' and base.get('attemptCount')==0 and base.get('hitCount')==0 and base.get('outcomes')==[]
 if base.get('status') not in {'calculable-observed','calculable-experimental'} and not empty:return base==new if phase else False
 if new.get('status') not in {'calculable-observed','calculable-experimental'}:return False
 old=base.get('outcomes');out=new.get('outcomes')
 if not isinstance(old,list) or not isinstance(out,list):return False
 added=Counter(tuple(x['outcome']) for x in openings)
 if phase:
  old_counts=Counter(map(tuple,old));new_counts=Counter(map(tuple,out))
  if old_counts-new_counts or any(o[0] not in {u['castTick'] for u in openings} for o in (new_counts-old_counts).elements()):return False
 elif Counter(map(tuple,out))!=Counter(map(tuple,old))+added:return False
 # Unknown uses elsewhere remain exactly as before, not converted by admission.
 for k,v in base.items():
  if type(v) is int and ('unresolved' in k.lower() or 'unknown' in k.lower()) and new.get(k)!=v:return False
 # All existing per-attempt evidence must remain aligned and unchanged.
 for key,values in base.items():
  if not isinstance(values,list) or len(values)!=len(old) or not (key.endswith('PerAttempt') or key.endswith('ByAttempt')):continue
  other=new.get(key)
  if not isinstance(other,list) or len(other)!=len(out):return False
  remaining=list(enumerate(out))
  for o,v in zip(old,values):
   match=next(((j,i) for j,(i,x) in enumerate(remaining) if x==o),None)
   if match is None:return False
   j,i=match
   if other[i]!=v:return False
   remaining.pop(j)
 for name,child in (base.get('phaseMetrics') or {}).items():
  other=(new.get('phaseMetrics') or {}).get(name)
  if other is None or not _preserves_base(child,other,openings,phase=True):return False
 return True


def recalculate_opening_scope(cache,catalog,tables,specs,episodes,calculator,kwargs,base_result=None):
 """Discovery is separate; only a scoped recheck can replace a base metric."""
 from .requested_skill_scope import runtime_source_group
 intervals={str(p):[[e['startTick'],e['endTick']] for e in es] for p,es in episodes.items()}
 scoped={**cache,'intervals':intervals}
 if base_result is not None:
  if any(base_result.get(k)!=cache.get(k) for k in ('matchKey','clientVersion','gameDataSha256')):raise ValueError('opening baseline source identity mismatch')
  if set(base_result.get('requestedMetricIds',[]))!={s['metricId'] for s in specs}:raise ValueError('opening baseline metric scope mismatch')
  if {p['playerObjectId'] for p in base_result.get('playerObservations',[])}!=set(episodes):raise ValueError('opening baseline player scope mismatch')
  for p in base_result['playerObservations']:
   for row in p['observations']:
    if any(not episode_at(episodes[p['playerObjectId']],o[0]) for o in row.get('outcomes',[]) or []):raise ValueError('opening baseline contains foreign episode attempts')
    if any(type(v) is int and v and ('unresolved' in k.lower() or 'unknown' in k.lower()) for k,v in row.items()):raise ValueError('baseline unknown scope requires fresh scoped calculation')
  base=base_result
 else:base=calculator(scoped,catalog,tables,metric_specs=specs,**kwargs)
 probe_intervals={}
 for p,es in episodes.items():
  ticks=[s['tick'] for s in cache['facts']['starts'] if s.get('playerObjectId')==p]
  probe_intervals[str(p)]=[[min(ticks),max(e['endTick'] for e in es)]] if ticks and es and min(ticks)<max(e['endTick'] for e in es) else []
 probe_kwargs={**kwargs,'include_player_observations':True,'retained_source':None,'resolve_retained_details':False}
 probe=calculator({**scoped,'intervals':probe_intervals},catalog,tables,metric_specs=specs,**probe_kwargs)
 result=deepcopy(base)
 result['openingBaselineProvenance']=dict(implementationSha256=base.get('implementationSha256'),reused=base_result is not None,intervals=intervals)
 result['implementationSha256']=probe['implementationSha256']
 spec_by_id={s['metricId']:s for s in specs};lookup={(p['playerObjectId'],r['metricId']):r for p in probe.get('playerObservations',[]) for r in p['observations']};receipts=[]
 for player in result.get('playerObservations',[]):
  pid=player['playerObjectId'];starts=[s for s in cache['facts']['starts'] if s.get('playerObjectId')==pid]
  for i,row in enumerate(player['observations']):
   spec=spec_by_id[row['metricId']];found=lookup.get((pid,row['metricId']))
   if found is None:continue
   openings=exact_opening_candidates(found,episodes[pid],starts,runtime_source_group(spec))
   if not openings:continue
   expanded={k:[x.copy() for x in v] for k,v in intervals.items()}
   # Exact cast ticks only. No pre-combat time padding or unrelated-use range.
   expanded[str(pid)]=sorted(expanded[str(pid)]+[[t,t+1] for t in sorted({u['castTick'] for u in openings})])
   checked=calculator({**scoped,'intervals':expanded},catalog,tables,metric_specs=[spec],**{**probe_kwargs,'_output_players':{pid}})
   replacement=next((r for p in checked.get('playerObservations',[]) if p['playerObjectId']==pid for r in p['observations'] if r['metricId']==row['metricId']),None)
   if replacement is None or not _preserves_base(row,replacement,openings):
    receipts.append(dict(playerObjectId=pid,metricId=row['metricId'],status='not-admitted-base-or-attempt-evidence-changed'));continue
   replacement=deepcopy(replacement);replacement['openingEpisodeAssignments']=openings
   player['observations'][i]=replacement
   receipts.append(dict(playerObjectId=pid,metricId=row['metricId'],status='admitted-exact-hit-linked-precast',uses=openings))
 result['observations']=[r for p in result.get('playerObservations',[]) for r in p['observations']]
 result['openingCastScopeEvidence']=receipts
 return result


def skill_usage_with_openings(player,episodes,rows):
 timeline=player.get('skillStartTimeline')
 if not isinstance(timeline,list):raise ValueError('actual skill start timeline required')
 openings={}
 for row in rows:
  for u in row.get('openingEpisodeAssignments',[]):
   matches=[t for t in timeline if t[0]==u['castTick'] and len(t)>3 and t[3]==u['skillCode']]
   if len(matches)!=1:raise ValueError('opening cast lacks unique original skill timeline row')
   t=matches[0];key=tuple(u['castWireOrder'])
   receipt=dict(castTick=t[0],family=t[1],skillId=t[3],skillGroup=u['skillGroup'])
   if key in openings and openings[key]!=(u['episodeId'],receipt):raise ValueError('opening use assigned twice')
   openings[key]=(u['episodeId'],receipt)
 totals=Counter();output={}
 for e in episodes:
  ordinary=[t for t in timeline if e['startTick']<=t[0]<e['endTick']]
  extra=[r for ep,r in openings.values() if ep==e['episodeId']]
  counts=Counter(t[1] for t in ordinary);counts.update(r['family'] for r in extra);totals.update(counts)
  output[e['episodeId']]=dict(familyCounts=dict(counts),skillStartCount=sum(counts.values()),openingSkillUses=sorted(extra,key=lambda r:r['castTick']))
 return dict(familyCounts=dict(totals),skillStartCount=sum(totals.values())),output
