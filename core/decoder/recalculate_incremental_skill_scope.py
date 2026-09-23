"""Recalculate explicitly reviewed changed groups; preserve all other rows exactly.

Use only for changes whose effects are isolated to the declared groups. A shared
decoder/lifetime/aggregation semantic change still requires a full calculation.
At least one affected cache is checked against the full calculator each run.
"""
import argparse
from collections import Counter
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import sys
import zipfile

ROOT=Path(__file__).resolve().parent.parent
sys.path[:0]=[str(ROOT/'decoder'),str(ROOT/'acquire')]
from recalculate_skill_scope import recalculate
from requested_skill_scope import manifest,implementation_fingerprint,validate_scope_result,POLICY_ID
from requested_skill_hit_rates import GAME_DB_SHA256,CLIENT_VERSION
from skill_scope_evidence_cache import read_evidence_cache
from projectile_hit_catalog import build_projectile_skill_catalog
from audit_requested_skills_once import summarize,write_json


def canonical(rows):
    return Counter(json.dumps(r,sort_keys=True,ensure_ascii=False) for r in rows)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('baseline',type=Path)
    parser.add_argument('caches',nargs='+',type=Path)
    parser.add_argument('--changed-groups',nargs='+',type=int,required=True)
    parser.add_argument('--out-dir',type=Path,required=True)
    args=parser.parse_args()
    load=lambda p:json.loads(p.read_text(encoding='utf-8'))
    baseline=load(args.baseline/'summary.json')
    validation=load(args.baseline/'validation.json')
    if (validation['summarySha256']!=hashlib.sha256((args.baseline/'summary.json').read_bytes()).hexdigest() or
            validation['implementationSha256']!=baseline['implementationSha256'] or
            baseline['clientVersion']!=CLIENT_VERSION or baseline['gameDataSha256']!=GAME_DB_SHA256 or
            baseline['policyId']!=POLICY_ID):
        raise ValueError('baseline must have a validated exact-version summary')
    paths=sorted(args.baseline.glob('match-*.json'))
    if len(paths)!=baseline['matchCount'] or len(paths)!=len(args.caches):
        raise ValueError('provide baseline caches in the same complete match order')
    if args.out_dir.exists() and any(args.out_dir.iterdir()):raise ValueError('choose an empty new output directory')
    args.out_dir.mkdir(parents=True,exist_ok=True)
    groups=set(args.changed_groups);specs=manifest();affected=[s for s in specs if s['skillGroup'] in groups]
    unchanged=[s for s in specs if s['skillGroup'] not in groups]
    previous_unchanged=[m for m in baseline['metrics'] if m['skillGroup'] not in groups]
    if {s['metricId'] for s in unchanged}!={s['metricId'] for s in previous_unchanged}:
        raise ValueError('undeclared scope change outside changed groups')
    for s in unchanged:
        prior=next(m for m in previous_unchanged if m['metricId']==s['metricId'])
        if any(prior.get(k)!=v for k,v in s.items()):raise ValueError('undeclared metric contract change')
    characters={s['characterCode'] for s in affected}
    game_db=ROOT/'acquire/gamedata-20260903071248.zip'
    if hashlib.sha256(game_db.read_bytes()).hexdigest()!=GAME_DB_SHA256:raise ValueError('gameDb hash mismatch')
    catalog=build_projectile_skill_catalog(game_db)
    with zipfile.ZipFile(game_db) as z:
        tables={name:json.loads(z.read(name+'.json')) for name in
            ['Skill','CharacterState','CharacterStateGroup','EffectAndSound','SummonObject','ProjectileSetting']}
    fingerprint=implementation_fingerprint();matches=[];preserved=0;recalculated=0;checked_full=False
    for i,(path,cache_path) in enumerate(zip(paths,args.caches),1):
        old=load(path)
        if old['implementationSha256']!=baseline['implementationSha256']:raise ValueError('mixed baseline revisions')
        keep=[r for r in old['observations'] if r['skillGroup'] not in groups]
        for r in keep:validate_scope_result(r)
        new_rows=[]
        if characters.intersection(old['observedCharacterCodes']):
            cache=read_evidence_cache(cache_path)
            if cache['matchKey']!=old['matchKey']:raise ValueError('cache order does not match baseline')
            new_rows=recalculate(cache,catalog,tables,metric_specs=affected)['observations']
            recalculated+=1
            if not checked_full:
                full=recalculate(cache,catalog,tables)['observations']
                if canonical(full)!=canonical(keep+new_rows):
                    raise ValueError('full-calculator comparison contradicts isolated changed-group reuse')
                checked_full=True
        elif any(r['skillGroup'] in groups for r in old['observations']):
            raise ValueError('baseline character inventory contradicts observed skill rows')
        m=deepcopy(old)
        m.update(observations=keep+new_rows,implementationSha256=fingerprint,inputMode='validated-group-incremental',
            incrementalSource=dict(batch=str(args.baseline),matchSha256=hashlib.sha256(path.read_bytes()).hexdigest(),
                sourceImplementationSha256=baseline['implementationSha256'],changedGroups=sorted(groups)))
        matches.append(m);preserved+=len(keep)
        write_json(args.out_dir/path.name,m)
        if i%5==0 or i==len(paths):
            print(json.dumps(dict(completed=i,total=len(paths),recalculatedMatches=recalculated,fullCrosscheck=checked_full)),flush=True)
    if not checked_full:raise ValueError('no affected cache available for full-calculator equivalence check')
    if implementation_fingerprint()!=fingerprint:raise ValueError('implementation changed during calculation')
    summary={**baseline,'metrics':summarize(matches,policy_id=POLICY_ID,specs=specs),
        'implementationSha256':fingerprint,'inputMode':'validated-group-incremental','sourceBatch':str(args.baseline),
        'recalculatedMatchCount':recalculated,'changedGroups':sorted(groups),'networkCalls':0}
    summary.pop('retainedRowsRecalculated',None)
    write_json(args.out_dir/'summary.json',summary)
    write_json(args.out_dir/'preservation-validation.json',dict(status='passed',sourceBatch=str(args.baseline),
        preservedRows=preserved,allRetainedRowsExactlyEqual=True,changedGroups=sorted(groups),
        recalculatedMatchCount=recalculated,fullCalculatorEquivalenceChecked=True,networkCalls=0))
    print(json.dumps(dict(result=str(args.out_dir/'summary.json'),preservedRows=preserved,recalculatedMatches=recalculated)))


if __name__=='__main__':main()
