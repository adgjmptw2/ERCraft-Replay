"""One FIFO worker execution. Never marks results ready before validation."""
import contextlib,json,sys,shutil,hashlib,tempfile,time
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2]
SHARED_CORE=ROOT.parent/'secret_replay-safe'
SHARED_LOCAL=ROOT
PREPARED=None
if '--prepared' in sys.argv:
 from replay_dispatch import validate_prepared
 PREPARED=validate_prepared(Path(sys.argv[sys.argv.index('--prepared')+1]),int(sys.argv[1]),Path(sys.argv[2]))
 CORE=Path(PREPARED['core'])
 sys.path[:0]=[str(CORE),str(CORE/'acquire'),str(CORE/'decoder')]
def main(resources):
 if PREPARED is None:raise RuntimeError('Prepared replay dispatch is required')
 from overlap_evidence import input_version_context
 resources.enter_context(input_version_context(PREPARED['clientVersion']))
 import analyze_replay_once as runner
 if PREPARED['clientVersion']=='12.4.0':
  from acquisition_state import load_service_account
  runner.load_service_account=lambda:load_service_account(SHARED_CORE)
 from decoder.full_replay_corpus import archive_replay
 from decoder.corpus_runtime_source import CorpusRuntimeSource
 from decoder.audit_projectile_runtime_replay import build_runtime_audit,validate_runtime_audit
 from decoder.skill_game_data_contract import archive_for_revision
 from decoder.requested_skill_scope import active_manifest
 from decoder.projectile_prefix_consensus import validate_projectile_prefix_consensus
 from decoder.attach_requested_skill_operation import attach_requested_skill_operation
 from decoder.build_public_combat_analysis import write_public_analysis
 from decoder.validate_public_combat_analysis import validate_public_analysis_object
 from decoder.validate_combat_analysis import validate_analysis
 from decoder.requested_skill_scope import implementation_fingerprint
 implementation_fingerprint()  # Fail stale offline plans before downloading/decoding.
 active_manifest()  # Resolve the current request authority before acquiring raw data.
 from decoder.corpus_decode_equivalence import reviewed_contract
 reviewed_contract()
 from decoder.build_public_combat_analysis import load_map_marker_assets
 load_map_marker_assets()
 from skill_assets import preflight
 preflight(CORE)
 game=int(sys.argv[1]);folder=Path(sys.argv[2]);record={}
 from gc_metrics import observe,large_graph_threshold
 resources.enter_context(large_graph_threshold())
 gc_phase=resources.enter_context(observe(folder))
 from perf_calls import observe as observe_calls
 resources.enter_context(observe_calls(folder))
 # Full decode, evidence, private reports and duplicate exports are job scratch only.
 # The server owns TEMP as well, so a timed-out child cannot retain these files.
 base=Path(resources.enter_context(tempfile.TemporaryDirectory(prefix='replay-analysis-')))
 phase_started=time.perf_counter();phase_name=None;phase_times={}
 def stage(name,patch=None):
  nonlocal phase_started,phase_name
  now=time.perf_counter()
  if phase_name:phase_times[phase_name]=round(now-phase_started,3)
  phase_started=now;phase_name=name
  gc_phase[0]=name
  (folder/'phase-timings.json').write_text(json.dumps(phase_times),encoding='utf8')
  (folder/'stage.json').write_text(json.dumps({'stage':name,'patch':patch}),encoding='utf8')
 stage('acquisition')
 info={k:PREPARED[k] for k in ('path','gameId','bytes','firstSnapshotTick')}
 info['replayArchive']=archive_replay(info['path'],base/'corpus')
 record.update(info)
 @contextlib.contextmanager
 def acquire(gid,user):
  if gid!=info['gameId']:raise ValueError('Prepared replay game mismatch')
  yield info
 runner.acquire_replay=acquire
 from item_assets import prepare
 original_export=runner.write_public_analysis
 def export_with_assets(catalog,*args,**kwargs):
  prepare(CORE,catalog)
  return original_export(catalog,*args,**kwargs)
 runner.write_public_analysis=export_with_assets
 from overlap_evidence import JobEvidence
 overlap=JobEvidence(resources,record,CORE)
 runner.run_analysis=overlap.run
 stage('basic-analysis')
 runner.main([str(game),'--generate-prerequisites','--defer-public','--out-dir',str(base),'--report-out',str(folder/'timings.json')])
 private=json.loads((base/'combat-analysis.json').read_bytes())
 version=private['meta']['clientVersion'];stage('skill-analysis',version)
 evidence_result=overlap.finish(private,folder)
 database=overlap.database;sha=evidence_result['sha']
 audit=evidence_result['audit'];evidence_cache=evidence_result['evidence'];player_identity=evidence_result['identity']
 del evidence_result
 stage('skill-attachment',version)
 (base/'skill-audit.private.json').write_text(json.dumps(audit),encoding='utf8');validate_runtime_audit(audit)
 private['skillOperation']['requestedMetrics']=attach_requested_skill_operation(private['players'],replay_sha256=sha,evidence_cache=evidence_cache,player_identity=player_identity,game_data_path=database,retained_source=CorpusRuntimeSource(CorpusRuntimeSource.path_from_acquisition(record),sha,version))
 del evidence_cache,player_identity
 dest=base/'combat-analysis.requested.json';dest.write_text(json.dumps(private),encoding='utf8');validate_analysis(dest,None,game)
 fields=json.loads((base/'evidence'/f'{game}.delta-fields.inspect.json').read_bytes())
 stage('public-validation',version)
 prepare(CORE,private)
 rendered_public={}
 public_catalog=write_public_analysis(private,fields,base/'public',rendered_output=rendered_public)
 pub=base/'public/combat-analysis.public.json'
 validate_public_analysis_object(public_catalog,rendered_public.pop('html'),private)
 public_bytes=rendered_public.pop('jsonBytes');del rendered_public
 data=public_catalog;viewer=folder/'viewer-data';viewer.mkdir(exist_ok=True)
 shutil.copy2(pub,viewer/'combat-analysis-personal-pvp-v1.json')
 binding={'format':'er-requested-map-metrics.v1','fallbackUsed':False,'sourceFixtureSha256':hashlib.sha256(public_bytes).hexdigest(),'reportId':data['meta']['reportId'],'clientVersion':data['meta']['clientVersion'],'players':[{'publicPlayerId':p['publicPlayerId'],'characterCode':p['characterCode'],'requestedMetrics':p['skillOperation']['requestedMetrics']} for p in data['players']]}
 (viewer/'requested-map-metrics-v18.json').write_text(json.dumps(binding),encoding='utf8')
 stage('viewer-sidecars',version)
 from viewer_sidecars import build_sidecars
 for name,value in build_sidecars(public_bytes,private,CorpusRuntimeSource.path_from_acquisition(record),sha).items():
  (viewer/name).write_text(json.dumps(value,separators=(',',':')),encoding='utf8')
 del public_bytes
 stage('storage',version)
 import gzip
 stats={'gameId':game,'patch':version,'players':[{'characterCode':p['characterCode'],'teamNumber':p['teamNumber'],'metrics':[{'metricId':m.get('metricId'),'attemptCount':m.get('attemptCount'),'hitCount':m.get('hitCount'),'sourceStatus':m.get('status')} for m in p['skillOperation']['requestedMetrics']['metrics']]} for p in data['players']]}
 (folder/'statistics.json.gz').write_bytes(gzip.compress(json.dumps(stats,separators=(',',':')).encode(),mtime=0))
 from payload_storage import seal_viewer
 payloads=seal_viewer(viewer)
 (folder/'retention.json').write_text(json.dumps({'patch':version,'verified':True,'viewerSeparateFromStatistics':True,'payloads':payloads,'policy':'temporary intermediates; viewer current patch only; patch-tagged aggregate retained'}))
 from retention import expire_other_patches
 expire_other_patches(folder.parent,version)
 stage('complete',version)
 (folder/'READY').write_text(str(game),encoding='utf8')
if __name__=='__main__':
 if PREPARED is None:
  from replay_dispatch import main as dispatch
  dispatch()
 else:
  with contextlib.ExitStack() as resources:
   if not (Path(sys.argv[2])/'READY').exists():main(resources)
