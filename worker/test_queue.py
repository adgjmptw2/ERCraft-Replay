from concurrent.futures import ThreadPoolExecutor
import os,tempfile,importlib.util,threading,json,urllib.request,urllib.error,http.cookiejar,unittest
from pathlib import Path
TEMP=tempfile.TemporaryDirectory();os.environ['REPLAY_BETA_WORK']=TEMP.name
(Path(TEMP.name)/'64783236').mkdir();(Path(TEMP.name)/'64783236/READY').write_text('64783236')
spec=importlib.util.spec_from_file_location('queue_server',Path(__file__).with_name('server.py'));q=importlib.util.module_from_spec(spec);spec.loader.exec_module(q)
server=q.ThreadingHTTPServer(('127.0.0.1',0),q.Handler);threading.Thread(target=server.serve_forever,daemon=True).start()
BASE=f'http://127.0.0.1:{server.server_port}'
def client():
 c=urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
 c.open(BASE+'/api/replay-beta/status').close()
 return c
def post(c,game):
 try:r=c.open(urllib.request.Request(BASE+'/api/replay-beta/request',data=json.dumps({'gameId':game}).encode(),headers={'Content-Type':'application/json'}))
 except urllib.error.HTTPError as e:r=e
 return r.code,json.load(r)
class Tests(unittest.TestCase):
 def setUp(self):
  with q.db() as c:c.execute('DELETE FROM jobs');c.execute('DELETE FROM requests');c.execute('DELETE FROM request_cooldowns')
 def test_limit_is_server_persisted(self):
  c=client();self.assertEqual(post(c,'65000001')[0],202);self.assertEqual(post(c,'65000002')[0],429)
  self.assertEqual(post(c,'65000001')[0],200)
 def test_cooldown_expires_and_survives_initialize(self):
  c=client();self.assertEqual(post(c,'65000001')[0],202)
  q.initialize();self.assertEqual(post(c,'65000002')[0],429)
  with q.db() as db:db.execute('UPDATE request_cooldowns SET requested=requested-121')
  self.assertEqual(post(c,'65000002')[0],202)
 def test_duplicate_shared_job_and_fifo(self):
  self.assertEqual(post(client(),'65000001')[0],202);self.assertEqual(post(client(),'65000001')[0],200);post(client(),'65000002')
  rows=[r for r in q.catalog()['jobs'] if r['state']=='queued'];self.assertEqual([r['position'] for r in rows],[1,2])
 def test_ready_does_not_consume_quota(self):
  c=client();self.assertEqual(post(c,'64783236')[0],200);self.assertEqual(post(c,'65000001')[0],202)
 def test_invalid_game(self):self.assertEqual(post(client(),'../secrets')[0],400)
 def test_compressed_result_is_served_with_exact_original_bytes(self):
  from payload_storage import seal_viewer
  folder=q.WORK/'65000999';viewer=folder/'viewer-data';viewer.mkdir(parents=True,exist_ok=True)
  raw=b'{"samples":[[1,0.123456789,9.876543210]],"unknown":null}'
  (viewer/'combat-analysis-personal-pvp-v1.json').write_bytes(raw);seal_viewer(viewer);(folder/'READY').touch()
  with urllib.request.urlopen(BASE+'/replay/65000999/public/ui-assets/combat-analysis-personal-pvp-v1.json') as result:
   self.assertEqual(result.read(),raw)
 def test_not_ready_result_is_blocked(self):
  with self.assertRaises(urllib.error.HTTPError) as e:urllib.request.urlopen(BASE+'/replay/65000001/index.html')
  self.assertEqual(e.exception.code,409)
 def test_30_concurrent_browsers_share_one_job(self):
  with ThreadPoolExecutor(max_workers=12) as pool:codes=list(pool.map(lambda _:post(client(),'65000003')[0],range(30)))
  self.assertEqual(codes.count(202),1);self.assertEqual(codes.count(200),29)
  with q.db() as c:self.assertEqual(c.execute('SELECT count(*) FROM jobs').fetchone()[0],1)
 def test_same_browser_simultaneous_cooldown(self):
  opener=client();opener.addheaders=[('Cookie','replay_browser='+'a'*64)]
  with ThreadPoolExecutor(max_workers=8) as pool:codes=list(pool.map(lambda n:post(opener,str(65000100+n))[0],range(12)))
  self.assertEqual(codes.count(202),1);self.assertEqual(codes.count(429),11)
 def test_recovery_preserves_fifo(self):
  post(client(),'65000200');post(client(),'65000201')
  with q.db() as c:c.execute("UPDATE jobs SET state='running' WHERE game='65000200'")
  # A fresh module executes the same initialization as a server restart.
  fresh=importlib.util.module_from_spec(spec);spec.loader.exec_module(fresh)
  fresh.initialize(reset_running=True)
  rows=[r for r in fresh.catalog()['jobs'] if r['state']=='queued']
  self.assertEqual([r['gameId'] for r in rows],['65000200','65000201'])
 def test_status_issues_cookie_and_cross_site_post_is_blocked(self):
  opener=urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
  with opener.open(BASE+'/api/replay-beta/status') as response:
   self.assertIn('replay_browser=',response.headers['Set-Cookie'])
  with self.assertRaises(urllib.error.HTTPError) as e:
   opener.open(urllib.request.Request(BASE+'/api/replay-beta/request',data=b'{"gameId":"65000300"}',headers={'Content-Type':'application/json','Origin':'https://evil.example'}))
  self.assertEqual(e.exception.code,403)
 def test_request_without_browser_cookie_cannot_create_identity(self):
  request=urllib.request.Request(BASE+'/api/replay-beta/request',data=b'{"gameId":"65000301"}',headers={'Content-Type':'application/json'})
  with self.assertRaises(urllib.error.HTTPError) as e:urllib.request.urlopen(request)
  self.assertEqual(e.exception.code,428)
def tearDownModule():
 server.shutdown();server.server_close()
 for h in list(q.LOG.handlers):h.close();q.LOG.removeHandler(h)
 TEMP.cleanup()
if __name__=='__main__':unittest.main()
