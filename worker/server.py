"""Loopback-only replay beta queue; production API is GET-only proxy."""
import math, gzip, logging, logging.handlers, shutil, contextlib, os, hashlib, json, mimetypes, re, secrets, sqlite3, threading, time, subprocess, tempfile, urllib.request, urllib.error, msvcrt, sys
from pathlib import Path
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlsplit, unquote
from datetime import datetime, timezone, timedelta
from patch_policy import current_retention,blocked_cached_patch,UNSUPPORTED,MESSAGE
ROOT=Path(__file__).resolve().parents[2]
CORE=ROOT.parent/'secret_replay-safe'
UI=ROOT.parent/'ERCraft-replay-ui-sandbox'
VIEWER=Path(os.environ.get('REPLAY_BETA_VIEWER',str(ROOT/'work/replay-viewer')))
WORK=Path(os.environ.get('REPLAY_BETA_WORK',str(ROOT/'work/replay-beta')));WORK.mkdir(parents=True,exist_ok=True)
DB=WORK/'queue.sqlite3'
SERVER_HOST=os.environ.get('REPLAY_BETA_HOST','127.0.0.1')
SERVER_PORT=int(os.environ.get('REPLAY_BETA_PORT','5274'))
PUBLIC_ORIGINS={x.strip().rstrip('/') for x in os.environ.get(
 'REPLAY_BETA_PUBLIC_ORIGINS','http://127.0.0.1:5274,http://localhost:5274').split(',') if x.strip()}
COOKIE_SECURE=os.environ.get('REPLAY_BETA_COOKIE_SECURE','0').lower() in {'1','true','yes'}
PRODUCTION_MODE=os.environ.get('REPLAY_BETA_PRODUCTION_MODE','0').lower() in {'1','true','yes'}
LOCK=threading.RLock()
LOG=logging.getLogger('replay-beta-'+str(WORK));LOG.setLevel(logging.INFO)
handler=logging.handlers.RotatingFileHandler(WORK/'events.jsonl',maxBytes=262144,backupCount=2,encoding='utf8');LOG.addHandler(handler)
def event(kind,game=None,**fields):
 LOG.info(json.dumps(dict(time=datetime.now(timezone.utc).isoformat(),event=kind,gameId=game,**fields),ensure_ascii=False))

@contextlib.contextmanager
def db():
 c=sqlite3.connect(DB);c.row_factory=sqlite3.Row
 try:
  with c:yield c
 finally:c.close()
def initialize(reset_running=False):
 with db() as c:
  c.executescript("CREATE TABLE IF NOT EXISTS jobs(game TEXT PRIMARY KEY,state TEXT NOT NULL,created REAL NOT NULL,message TEXT);CREATE TABLE IF NOT EXISTS requests(browser TEXT,day TEXT,game TEXT,PRIMARY KEY(browser,day));")
  c.execute("CREATE TABLE IF NOT EXISTS request_cooldowns(browser TEXT PRIMARY KEY,requested REAL NOT NULL)")
  if reset_running:c.execute("UPDATE jobs SET state='queued' WHERE state='running'")
initialize()
def ready(game):
 return (WORK/game/'READY').is_file() and current_retention(WORK/game)
def catalog():
 with db() as c: rows=[dict(r) for r in c.execute('SELECT * FROM jobs ORDER BY created,game')]
 known={r['game'] for r in rows}
 rows.extend(dict(game=d.name,state='ready') for d in sorted(WORK.iterdir()) if d.is_dir() and d.name.isdigit() and d.name not in known and ready(d.name))
 n=0;result=[]
 for row in rows:
  state='ready' if ready(row['game']) else 'failed' if (WORK/row['game']/'EXPIRED').exists() else row['state']
  if state not in ('queued','running') and (blocked_cached_patch(WORK/row['game']) or state=='ready' and not ready(row['game'])):
   state='failed';row['message']=MESSAGE
  if state=='queued':n+=1
  result.append(dict(gameId=row['game'],state=state,position=n if state=='queued' else None,url=f"/replay/{row['game']}/index.html" if state=='ready' else None,message=row.get('message')))
 return dict(jobs=result,waiting=sum(r['state'] in ('queued','running') for r in result))
AUTH_PAUSED=False

def worker_event(kind,**fields):
 # Diagnostics must not kill recovery or expose arbitrary exception messages.
 try:event(kind,**fields)
 except Exception:
  try:print(json.dumps(dict(event=kind,**fields)),file=sys.stderr,flush=True)
  except Exception:pass

def _worker_loop():
 global AUTH_PAUSED
 paused=None
 while True:
  reason='SESSION_UNAVAILABLE' if AUTH_PAUSED else 'LOW_DISK' if shutil.disk_usage(WORK).free<3*1024**3 else None
  if reason:
   if paused!=reason:worker_event('worker-paused',code=reason)
   paused=reason;time.sleep(30);continue
  with LOCK,db() as c:
   running=c.execute("SELECT 1 FROM jobs WHERE state='running' LIMIT 1").fetchone()
   row=None
   if not running:
    cutoff=(datetime.now(timezone(timedelta(hours=9)))-timedelta(days=7)).date().isoformat()
    c.execute('DELETE FROM requests WHERE day<?',(cutoff,))
    c.execute('DELETE FROM request_cooldowns WHERE requested<?',(time.time()-7*86400,))
    row=c.execute("SELECT game FROM jobs WHERE state='queued' ORDER BY created,game LIMIT 1").fetchone()
    if row:c.execute("UPDATE jobs SET state='running',message=NULL WHERE game=?",(row['game'],))
  if running:
   if paused!='RUNNING_UNCERTAIN':worker_event('worker-paused',code='RUNNING_UNCERTAIN')
   paused='RUNNING_UNCERTAIN';time.sleep(30);continue
  if paused:worker_event('worker-resumed',code=paused);paused=None
  if not row:time.sleep(1);continue
  game=row['game'];folder=WORK/game;folder.mkdir(exist_ok=True);started=time.monotonic();event('started',game)
  try:
   # Parent owns the temporary source lifetime too: timeout kills the child before cleanup.
   with tempfile.TemporaryDirectory(prefix='replay-job-',dir=folder) as raw_temp, (folder/'worker.log').open('wb') as log:
    environment={**os.environ,'TEMP':raw_temp,'TMP':raw_temp}
    from process_tree import run_process_tree
    r=run_process_tree([str(CORE/'.venv/Scripts/python.exe'),'-X','utf8',str(ROOT/'scripts/replay-beta/analyze.py'),game,str(folder)],cwd=CORE,env=environment,stdout=log,stderr=log,timeout=900)
   if r.returncode or not ready(game):raise RuntimeError('ANALYSIS_VALIDATION_FAILED')
   state,message='ready',None
  except Exception:
   state,message='failed','분석 또는 결과 검증에 실패했어요. 관리자가 확인해야 해요.'
  tail=(folder/'worker.log').read_bytes()[-8192:] if (folder/'worker.log').exists() else b''
  # Raw traceback is private; only a bounded diagnostic code is kept in the small log.
  from failure_code import classify,message_for
  code=classify(tail)
  # Survives loop retries, including failures during terminal logging/DB writes.
  # Only a normal process restart after an auth canary clears this latch.
  if state=='failed' and code=='SESSION_UNAVAILABLE':AUTH_PAUSED=True
  if state=='failed':message=message_for(code)
  stage_path=folder/'stage.json'
  stage=json.loads(stage_path.read_text()) if stage_path.exists() else {}
  event(state,game,seconds=round(time.monotonic()-started,3),stage=stage.get('stage','analysis'),patch=stage.get('patch'),code=None if state=='ready' else code)
  if (folder/'worker.log').exists():(folder/'worker.log').write_bytes(tail)
  with LOCK,db() as c:c.execute('UPDATE jobs SET state=?,message=? WHERE game=?',(state,message,game))
def worker():
 while True:
  try:_worker_loop()
  except Exception as error:
   worker_event('worker-error',code='WORKER_LOOP_EXCEPTION',exceptionType=type(error).__name__[:64])
   time.sleep(5)

@contextlib.contextmanager
def server_lock():
 WORK.mkdir(parents=True,exist_ok=True)
 lock_path=WORK/'server.lock'
 with lock_path.open('a+b') as stream:
  if lock_path.stat().st_size==0:stream.write(b'0');stream.flush()
  stream.seek(0)
  try:msvcrt.locking(stream.fileno(),msvcrt.LK_NBLCK,1)
  except OSError:raise SystemExit('another replay-beta server is already running')
  try:yield
  finally:
   stream.seek(0);msvcrt.locking(stream.fileno(),msvcrt.LK_UNLCK,1)
class Handler(BaseHTTPRequestHandler):
 def log_message(self,*args):pass
 def output(self,status,data,ctype='application/json',cookie=None):
  body=data if isinstance(data,bytes) else json.dumps(data,ensure_ascii=False).encode()
  self.send_response(status);self.send_header('Content-Type',ctype);self.send_header('Content-Length',str(len(body)));self.send_header('Cache-Control','no-store');self.send_header('X-Content-Type-Options','nosniff')
  if cookie:
   secure='; Secure' if COOKIE_SECURE else ''
   self.send_header('Set-Cookie',f'replay_browser={cookie}; HttpOnly; SameSite=Strict; Path=/; Max-Age=31536000{secure}')
  self.end_headers();self.wfile.write(body)
 def do_POST(self):
  origin=self.headers.get('Origin')
  if origin is not None and origin.rstrip('/') not in PUBLIC_ORIGINS:return self.output(403,{'message':'다른 사이트에서 신청할 수 없어요.'})
  if self.path=='/api/replay-beta/report':
   try:
    size=int(self.headers.get('Content-Length',0));assert 0<size<256
    game=str(json.loads(self.rfile.read(size))['gameId']);assert re.fullmatch(r'[1-9][0-9]{5,10}',game)
    stage=WORK/game/'stage.json';details=json.loads(stage.read_text()) if stage.exists() else {}
    event('user-report',game,stage=details.get('stage'),patch=details.get('patch'),analysisVersion='replay-beta-v1')
    return self.output(200,{'recorded':True})
   except Exception:return self.output(400,{'message':'올바른 경기 번호가 필요해요.'})
  if self.path!='/api/replay-beta/request':return self.output(405,{'message':'허용되지 않은 요청'})
  if origin is not None and origin.rstrip('/') not in PUBLIC_ORIGINS:return self.output(403,{'message':'다른 사이트에서 신청할 수 없어요.'})
  try:
   size=int(self.headers.get('Content-Length',0));assert 0<size<256
   game=str(json.loads(self.rfile.read(size))['gameId']);assert re.fullmatch(r'[1-9][0-9]{5,10}',game)
  except Exception:return self.output(400,{'message':'올바른 경기 번호가 필요해요.'})
  token=re.search(r'(?:^|; *)replay_browser=([a-f0-9]{64})(?:;|$)',self.headers.get('Cookie',''))
  if not token:return self.output(428,{'message':'먼저 상태를 조회해 브라우저 식별 쿠키를 받아야 해요.'})
  token=token.group(1)
  now=time.time()
  with LOCK,db() as c:
   row=c.execute('SELECT state,message FROM jobs WHERE game=?',(game,)).fetchone()
   if blocked_cached_patch(WORK/game) or row and (row['message']==MESSAGE or row['state']=='ready' and not ready(game)):
    return self.output(409,{'code':UNSUPPORTED,'message':MESSAGE},cookie=token)
   if ready(game):return self.output(200,{'state':'ready'},cookie=token)
   if row and row['state'] in ('queued','running'):return self.output(200,{'state':row['state']},cookie=token)
   previous=c.execute('SELECT requested FROM request_cooldowns WHERE browser=?',(token,)).fetchone()
   remaining=max(0,math.ceil(120-(now-previous['requested']))) if previous else 0
   if remaining:return self.output(429,{'message':f'{remaining}초 후 다시 신청할 수 있어요.','retryAfterSeconds':remaining},cookie=token)
   c.execute('INSERT INTO request_cooldowns VALUES(?,?) ON CONFLICT(browser) DO UPDATE SET requested=excluded.requested',(token,now))
   c.execute("INSERT INTO jobs VALUES(?,'queued',?,NULL) ON CONFLICT(game) DO UPDATE SET state='queued',created=excluded.created,message=NULL",(game,time.time()))
  event('queued',game)
  self.output(202,{'state':'queued'},cookie=token)
 def file(self,base,relative):
  path=(base/unquote(relative)).resolve()
  if path.suffix.lower() in ('.er','.part'):return self.output(403,{'message':'원본 리플레이는 제공하지 않아요.'})
  if path.is_relative_to(base.resolve()) and path.suffix=='.json' and not path.exists():
   packed=path.with_suffix('.json.gz')
   if packed.is_file() and packed.resolve().is_relative_to(base.resolve()):
    return self.output(200,gzip.decompress(packed.read_bytes()),'application/json')
  if not path.is_relative_to(base.resolve()) or not path.is_file():return self.output(404,{'message':'파일 없음'})
  return self.output(200,path.read_bytes(),mimetypes.guess_type(path.name)[0] or 'application/octet-stream')
 def do_GET(self):
  path=urlsplit(self.path).path
  if path=='/api/replay-beta/status':
   token=re.search(r'(?:^|; *)replay_browser=([a-f0-9]{64})(?:;|$)',self.headers.get('Cookie',''))
   return self.output(200,catalog(),cookie=token.group(1) if token else secrets.token_hex(32))
  if path.startswith('/api/'):
   if PRODUCTION_MODE and not path.startswith('/api/replay-beta/'):
    return self.output(404,{'message':'허용되지 않은 조회 경로'})
   try:
    req=urllib.request.Request('https://api.ercraft.net'+self.path,headers={'User-Agent':'Mozilla/5.0','Accept':'application/json'})
    with urllib.request.urlopen(req,timeout=40) as r:return self.output(r.status,r.read(),r.headers.get('Content-Type','application/json')+'; charset=utf-8' if False else 'application/octet-stream')
   except Exception:return self.output(502,{'message':'운영 조회 API 연결 실패'})
  m=re.fullmatch(r'/replay/([0-9]+)/(.+)',path)
  if m:
   game,rel=m.groups()
   if blocked_cached_patch(WORK/game):return self.output(409,{'code':UNSUPPORTED,'message':MESSAGE})
   if not ready(game):return self.output(409,{'message':'리플레이가 아직 준비되지 않았어요.'})
   if rel.startswith('public/ui-assets/'):
    name=rel.removeprefix('public/ui-assets/')
    match_files={'combat-analysis-personal-pvp-v1.json','skill-levels-v1.json','transport-visual-v1.json','requested-map-metrics-v18.json','personal-engagement-metrics-v3.json'}
    if name in match_files:return self.file(WORK/game/'viewer-data',name)
    asset_root=VIEWER/'public/ui-assets' if (VIEWER/'public/ui-assets').is_dir() else UI/'public/ui-assets'
    return self.file(asset_root,name)
   return self.file(VIEWER,rel)
  if path.startswith('/assets/'):return self.file(ROOT/'dist',path.lstrip('/'))
  target=(ROOT/'dist'/path.lstrip('/'))
  return self.file(ROOT/'dist',path.lstrip('/') if target.is_file() else 'index.html')
if __name__=='__main__':
 with server_lock():
  initialize(reset_running=True)
  threading.Thread(target=worker,daemon=True).start()
  ThreadingHTTPServer((SERVER_HOST,SERVER_PORT),Handler).serve_forever()
