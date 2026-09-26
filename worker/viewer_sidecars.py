"""Generate every viewer sidecar from the current match, with byte-exact binding."""
import json,hashlib,sqlite3
from contextlib import closing
from pathlib import Path
from decoder.corpus_runtime_source import restore
from decoder import corpus_runtime_source
from decoder.delta_payloads import SchemaDecoder
from decoder.transport_visual_timeline import correlate_transport_states
from decoder.personal_engagement_metrics import build_personal_engagement_metrics
from patch_inputs import resolve_sidecar_inputs

CORE=Path(corpus_runtime_source.__file__).resolve().parents[1]

def build_sidecars(raw,private,decode_path,source_sha256,*,practical_damage=None):
 public=json.loads(raw);version=public['meta']['clientVersion']
 assert private['meta']['clientVersion']==version
 schema_path,calibration_path=resolve_sidecar_inputs(CORE,version)
 binding=dict(sourceFixtureSha256=hashlib.sha256(raw).hexdigest(),reportId=public['meta']['reportId'],clientVersion=version)
 ids={}
 # Repeat the exporter's explicit enumerate(private_players, start=1) mapping;
 # never join players using character names or team/character similarity.
 for public_id,p in enumerate(private['players'],1):
  matches=[q for q in public['players'] if q['publicPlayerId']==public_id]
  if len(matches)!=1 or (matches[0]['teamNumber'],matches[0]['characterCode'])!=(p['teamNumber'],p['characterCode']):raise ValueError('Public player identity mismatch')
  ids[p['objectId']]=public_id
 schema=json.loads(schema_path.read_bytes());bases={k:v['base'] for k,v in schema['classes'].items()}
 if version=='12.3.0':
  from decoder.corpus_cooldown_clock_inputs import CooldownSnapshotDecoder
  dec=CooldownSnapshotDecoder(bases,schema_path=schema_path,client_version=version)
 elif version=='12.4.0':
  dec=SchemaDecoder(bases,schema_path=schema_path,client_version=version)
 else:raise ValueError('Unsupported exact sidecar version')
 packet_dec=SchemaDecoder(bases,schema_path=schema_path,client_version=version)
 # 12.4 Blis.Common.SkillSlotIndex (metadata SHA256 70abb9a153c6c43109a3478aba4e960ce843b418d03aacc9ab61985c93270256).
 # Level slots are distinct from cooldown/use SkillSlotSet; keep 12.3 unchanged.
 families=({1:'Passive',2:'Active1',3:'Active2',4:'Active3',5:'Active4'} if version=='12.4.0'
           else {1:'Active1',2:'Active2',3:'Active3',4:'Active4',5:'Passive'});state={};levels={p:[] for p in ids.values()};snapshots=[];notifications=[];changes={};checks=0
 with closing(sqlite3.connect(Path(decode_path).resolve().as_uri()+'?mode=ro',uri=True)) as db:
  meta=dict(db.execute('select key,value from metadata'))
  if meta['clientVersion']!=version or meta['sourceSha256']!=source_sha256:raise ValueError('Sidecar source identity mismatch')
  for tick,name,blob in db.execute("select tick,packet_name,decoded_json_zlib from packets where category='fullSnapshot' or packet_name='CmdUpgradeSkill' order by id"):
   d=restore(blob)
   if name=='CmdUpgradeSkill':
    key=(d['objectId'],d['skillSlotIndex'])
    if key in state:state[key]+=1;levels[ids[key[0]]].append([tick,families[key[1]],state[key]])
   else:
    snap=d['gameSnapshot'];snapshots.append((tick,snap['quickTransitDeviceSnapshot']))
    for u in snap['userList']:
     actor=u['characterSnapshot']['objectId']
     if actor not in ids:continue
     ps=dec.decode_exact(u['playerSnapshot'],'BasePlayerSnapshot');skill=dec.decode_exact(ps['characterSkillSnapshot'],'CharacterSkillSnapshot')['skillLevelSnapshot']
     for slot,level in skill['skillLevelMap']:
      if slot not in families:continue
      key=(actor,slot)
      if key in state:
       checks+=1
       if state[key]!=level:raise ValueError('Skill upgrade/snapshot level mismatch')
      if key not in state:levels[ids[actor]].append([tick,families[slot],level])
      state[key]=level
  for name,tick,payload in db.execute("select packet_name,tick,raw_payload from packets where packet_name in ('CmdModifyQuickTransitDeviceState','CmdChangeHyperLoopToVLS') order by id"):
   v=packet_dec.decode_exact(payload,name)
   if name=='CmdChangeHyperLoopToVLS':notifications.append(tick)
   elif v['CanUseQuickTransitDeviceType'] in (1,2):changes[tick]=v['changeDeviceIds']
 states=correlate_transport_states(snapshots,notifications,changes,allow_redundant_device_ids=True,allow_snapshot_gaps=True)
 area_data=json.loads(calibration_path.read_bytes())
 areas={tuple(r['position']):r['areaCodes'] for r in area_data if len(r['areaCodes'])==1};objects=[];fixed={}
 for q in public['worldMap']['staticObjects']:
  if q['category']!='vls':continue
  matches=[r for r in private['worldMap']['staticObjects'] if r['category']=='vls' and r['position']==q['position'] and r['firstSeenTick']==q['firstSeenTick']]
  if len(matches)!=1:raise ValueError('Transport identity ambiguous')
  source=matches[0]
  if source['objectType']==49:continue
  if source['objectType']!=48 or tuple(q['position']) not in areas:raise ValueError('Fixed transport area unavailable')
  area=areas[tuple(q['position'])][0];fixed[area]=source['objectId']
  objects.append(dict(publicWorldObjectId=q['publicWorldObjectId'],areaCode=area,transportModeTimeline=[[s['tick'],s['areaModes'][area]] for s in states]))
 for old,new in zip(states,states[1:]):
  if new['source'] in ('unavailable-transition-between-snapshots','observed-QuickTransitDeviceSnapshot'):continue
  changed=[a for a in new['areaModes'] if new['areaModes'][a]!=old['areaModes'][a]]
  expected={fixed[a] for a in changed if new['areaModes'][a]=='vls'}
  actual=set(changes[new['tick']])&set(fixed.values())
  active={fixed[a] for a in new['areaModes'] if new['areaModes'][a]=='vls'}
  if not expected<=actual<=active:raise ValueError('Transport mode identity mismatch')
 personal=build_personal_engagement_metrics(public,private);personal.update(binding,version=3)
 # Only a matching exact owned spawn can upgrade observation to installation/use.
 for p in personal['players']:
  for ep in p['episodes']:
   for field,category in [('cameraAppearances','surveillance-camera'),('droneAppearances','control-lens')]:
    relevant=[q for q in public['worldMap']['staticObjects'] if q['category']==category and q.get('ownerPublicPlayerId')==p['publicPlayerId'] and ep['startTick']<=q['firstSeenTick']<ep['endTick']]
    exact=all(any(r.get('source')=='CmdSpawn' and ids.get(r.get('ownerId',private['objectOwners'].get(str(r['objectId']))))==p['publicPlayerId'] and r['category']==category and r['position']==q['position'] and r['firstSeenTick']==q['firstSeenTick'] for r in private['worldMap']['staticObjects']) for q in relevant)
    if exact and ep[field]['status']=='available':ep[field].update(label='배치',eventMeaning='exact-owned-CmdSpawn',sourceStatus='verified-owned-spawn',itemUseStatus='unavailable-no-exclusive-item-use-command-link')
 result = {
  'skill-levels-v1.json':dict(binding,**({'skillSlotMappingRevision':'12.4.0.SkillSlotIndex.v1'} if version=='12.4.0' else {}),status='snapshot-and-upgrade-verified',snapshotChecks=checks,players=[dict(publicPlayerId=p,skillLevelTimeline=rows) for p,rows in levels.items()]),
  'transport-visual-v1.json':dict(binding,format='ercraft-transport-visual.v1',status=('snapshot-confirmed-with-unavailable-transitions' if any(s['source']=='unavailable-transition-between-snapshots' for s in states) else 'decoded-state-command-correlated'),objects=objects,unavailableTransitionIntervals=sum(s['source']=='unavailable-transition-between-snapshots' for s in states)),
  'personal-engagement-metrics-v3.json':personal,
 }

 if practical_damage is not None:
  from decoder.practical_encounter_damage import build_damage_sidecar
  if len(practical_damage.get('runs',[])) != 1 or practical_damage['runs'][0].get('sourceSha256') != source_sha256:
   raise ValueError('Damage sidecar source mismatch')
  result['encounter-damage-v1.json']=build_damage_sidecar(raw,private,practical_damage)
 return result
