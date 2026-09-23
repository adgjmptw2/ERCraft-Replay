"""Read only: small replay queue/error report, no raw traceback or identifiers."""
import json,sqlite3,shutil
from pathlib import Path
root=Path(__file__).resolve().parents[2]/'work/replay-beta'
c=sqlite3.connect(f'file:{(root/"queue.sqlite3").as_posix()}?mode=ro',uri=True);c.row_factory=sqlite3.Row
rows=[dict(r) for r in c.execute('SELECT game,state,created,message FROM jobs ORDER BY created')];c.close()
logs=[]
for p in [root/'events.jsonl.2',root/'events.jsonl.1',root/'events.jsonl']:
 if p.exists():
  for line in p.read_text(encoding='utf8').splitlines():
   try:logs.append(json.loads(line))
   except json.JSONDecodeError:pass
print(json.dumps({'queue':rows,'recentEvents':logs[-30:],'diskFreeBytes':shutil.disk_usage(root).free},ensure_ascii=False,indent=2))
