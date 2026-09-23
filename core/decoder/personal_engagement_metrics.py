"""Personal PvP episode summaries from retained decoded events; never impute amounts."""
from collections import defaultdict
from .map_combat_hud import _event_defs,_packet_name,_field,_resolved_player,_state_spans,_has_cc_authority,_merge_spans,_prepare_state_event_batches

def value(v,unit,status='available',reason=None,**extra):
 return dict(value=v,unit=unit,status=status,reason=reason,**extra)

def build_personal_engagement_metrics(public,private,damage_recovery=None):
 players=private['players'];pub=public['players'];assert len(players)==len(pub)
 ids={p['objectId'] for p in players};mapping={p['objectId']:q['publicPlayerId'] for p,q in zip(players,pub)}
 assert all(p['characterCode']==q['characterCode'] and p['teamNumber']==q['teamNumber'] for p,q in zip(players,pub))
 teams={p['objectId']:p['teamNumber'] for p in players};owners={int(k):v for k,v in private['objectOwners'].items()};defs=_event_defs(private['eventTypes'])
 events=private['events'];damage=defaultdict(list)
 for e in events:
  if _packet_name(e,defs)!='CmdDamage':continue
  a=_resolved_player(_field(e,defs,'attackerId'),ids,owners);b=_field(e,defs,'objectId')
  if a not in ids or b not in ids or teams[a]==teams[b]:continue
  amount=_field(e,defs,'damage')
  if amount is None and damage_recovery is not None:
   key=(e[0],e[4],b,_field(e,defs,'attackerId'),_field(e,defs,'curHp'),_field(e,defs,'effectCode'))
   amount=damage_recovery.get(key)
  known=isinstance(amount,(int,float)) and not isinstance(amount,bool) and amount>=0
  damage[a].append((e[0],'dealt',amount if known else None));damage[b].append((e[0],'taken',amount if known else None))
 runtime=private.get('characterCapabilityCatalog',{}).get('runtimeCrowdControl');cc=[];authority=_has_cc_authority(defs,runtime)
 if authority:
  state_events=_prepare_state_event_batches(events,defs)
  types={r['stateType'] for r in runtime['codes'].values()}-{'Slow'}
  for typ in sorted(types):
   sub={**runtime,'codes':{k:r for k,r in runtime['codes'].items() if r['stateType']==typ},'groups':{k:r for k,r in runtime['groups'].items() if r['stateType']==typ}}
   for s in _state_spans(events,defs,ids,owners,sub,set(private.get('observedNonPlayerObjectIds',[])),event_batches=state_events):cc.append({**s,'type':typ})
 def damage_result(pid,start,end,direction):
  relevant=[a for t,d,a in damage[pid] if start<=t<end and d==direction];missing=sum(a is None for a in relevant);known=sum(a for a in relevant if a is not None)
  return value(None if missing else known,'damage','unavailable' if missing else 'available','missing-damage-amount' if missing else None,knownValue=known,missingPacketCount=missing,packetCount=len(relevant),targetScope='enemy-player',boundary='start-inclusive/end-exclusive')
 def cc_result(pid,start,end,direction):
  if not authority:return value(None,'seconds','unavailable','missing-cc-state-authority',applicationCount=None,byType=[],slowExcluded=True)
  relevant=[];uncertain=[]
  for s in cc:
   target=s['target'];caster=s['caster'];enemy=caster in ids and teams[caster]!=teams[target]
   applies=(target==pid if direction=='received' else caster==pid) and enemy
   unknown=s['casterStatus']=='unavailable-unresolved-caster-object' and (target==pid if direction=='received' else teams[pid]!=teams[target])
   right=s['endTick'] if s['endTick'] is not None else end
   if max(start,s['startTick'])>=min(end,right):continue
   if unknown or applies and not s['complete']:uncertain.append(s)
   elif applies:relevant.append(s)
  def totals(rows):
   spans=defaultdict(list)
   for s in rows:spans[s['target']].append((max(start,s['startTick']),min(end,s['endTick'])))
   seconds=sum(b-a for xs in spans.values() for a,b in _merge_spans(xs))/60
   return seconds,sum(start<=s['startTick']<end for s in rows)
  seconds,count=totals(relevant);bytype=[]
  for typ in sorted({s['type'] for s in relevant+uncertain}):
   rows=[s for s in relevant if s['type']==typ];secs,n=totals(rows);bad=any(s['type']==typ for s in uncertain)
   bytype.append(dict(type=typ,seconds=None if bad else secs,knownSeconds=secs,applicationCount=n,status='unavailable' if bad else 'available'))
  return value(None if uncertain else seconds,'target-seconds' if direction=='applied' else 'seconds','unavailable' if uncertain else 'available','incomplete-state-lifecycle-or-caster' if uncertain else None,knownValue=seconds,applicationCount=None if uncertain else count,knownApplicationCount=count,byType=bytype,slowExcluded=True,skillBreakdownStatus='unavailable-no-exclusive-skill-source',countDefinition='new-state-lifecycle-starts-in-episode; refreshes-not-extra-hits')
 world=public['worldMap']['staticObjects']
 def appearances(pid,start,end,category):
  rows=list({r['publicWorldObjectId']:r for r in world if r['category']==category and start<=r['firstSeenTick']<end}.values());known=[r for r in rows if r.get('ownerPublicPlayerId')==pid];unknown=[r for r in rows if r.get('ownerPublicPlayerId') is None]
  return value(None if unknown else len(known),'objects','unavailable' if unknown else 'available','unresolved-object-owner' if unknown else None,knownValue=len(known),label='등장',ticks=[r['firstSeenTick'] for r in known],eventMeaning='first-recorded-observation; not exact use/install',detectionStatus='unavailable-no-detection-events',removalStatus='unavailable-no-remover-identity')
 result=[]
 for p,q in zip(players,pub):
  episodes=[]
  for ep in q['combatJudgment']['personalEpisodes']:
   a,b=ep['startTick'],ep['endTick'];pid=p['objectId'];episodes.append(dict(personalEpisodeNumber=ep['personalEpisodeNumber'],startTick=a,endTick=b,damageDealt=damage_result(pid,a,b,'dealt'),damageTaken=damage_result(pid,a,b,'taken'),crowdControl=dict(applied=cc_result(pid,a,b,'applied'),received=cc_result(pid,a,b,'received')),cameraAppearances=appearances(q['publicPlayerId'],a,b,'surveillance-camera'),droneAppearances=appearances(q['publicPlayerId'],a,b,'control-lens')))
  result.append(dict(publicPlayerId=q['publicPlayerId'],characterCode=q['characterCode'],episodes=episodes))
 return dict(format='er-personal-engagement-metrics.v1',fallbackUsed=False,ticksPerSecond=60,players=result,droneScope='recon-and-EMP-combined',damageDefinition='sum of numeric enemy-player CmdDamage amounts; any missing amount makes total unavailable',ccDefinition='Slow excluded; complete recorded state lifecycle only; overlapping time merged per target',sourceEventCount=len(events))


def adjacent_hp_damage_recovery(retained):
 """Only exact original-frame adjacent damage commands; no missing-writer inference.

 A previous CmdDamage.curHp supplies the same target's immediate pre-command HP.
 Heal/revive/snapshot or any intervening command leaves an ordinal gap, so cannot
 cross this boundary. Explicit damage is consumed independently by the caller.
 """
 if retained.get('gaps') is None or any(g.get('count',0) for g in retained['gaps']):return {},[]
 mapping={r['cacheObjectId']:r['replayObjectId'] for r in retained['playerMap']}
 by_order=defaultdict(list)
 for d in retained['damages']:
  o=d.get('wireOrder')
  if d.get('wireCategory')=='commands' and isinstance(o,list) and len(o)==2:by_order[tuple(o)].append(d)
 recovery={};proof=[];conflicts=set()
 for order,ds in by_order.items():
  if len(ds)!=1:continue
  d=ds[0];prior=by_order.get((order[0],order[1]-1),[])
  if d.get('damageIsNull') is not True or len(prior)!=1:continue
  p=prior[0]
  if p.get('targetObjectId')!=d.get('targetObjectId') or p['tick']!=d['tick']:continue
  hp0,hp1=p.get('curHp'),d.get('curHp')
  if type(hp0) is not int or type(hp1) is not int or not 0<=hp1<=hp0:continue
  target=mapping.get(d['targetObjectId']);attacker=mapping.get(d.get('attackerObjectId'))
  if target is None or attacker is None:continue
  key=(d['tick'],order[1],target,attacker,hp1,d.get('effectCode'))
  if key in recovery:conflicts.add(key);continue
  recovery[key]=hp0-hp1
  proof.append(dict(tick=d['tick'],priorOrder=p['wireOrder'],damageOrder=list(order),targetObjectId=target,attackerObjectId=attacker,priorHp=hp0,curHp=hp1,recoveredDamage=hp0-hp1,effectCode=d.get('effectCode'),authority='same-original-frame-adjacent-CmdDamage; prior-curHp minus current-curHp'))
 for key in conflicts:recovery.pop(key,None)
 return recovery,proof
