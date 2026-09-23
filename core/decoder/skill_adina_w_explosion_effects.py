"""Reviewed configured explosion effects; no collision or nearest-cast inference."""
from pathlib import Path
import hashlib
import json
from collections import defaultdict, Counter
from .skill_wire_order import command_order

ROOT=Path(__file__).resolve().parents[1]
CONTRACT_PATH=ROOT/'data/adina-w-explosion-effects-v1.json'
CONTRACT_SHA256='9655f9e56917bec78244600ecff179b066b8c7ecc82f47f34b227dbb0e8ea4ad'

def load_contract(catalog):
    raw=CONTRACT_PATH.read_bytes()
    if hashlib.sha256(raw).hexdigest()!=CONTRACT_SHA256:raise ValueError('Adina explosion contract hash mismatch')
    rule=json.loads(raw)
    for name in ('nativeProof','revisionProof','exactRevisionEvidence'):
        if hashlib.sha256((ROOT/rule[name]).read_bytes()).hexdigest()!=rule[name+'Sha256']:
            raise ValueError('Adina explosion source proof mismatch: '+name)
    if catalog.get('sourceGameDbSha256') not in rule['sourceGameDbSha256']:
        raise ValueError('Adina explosion exact gameDb unavailable')
    return rule

def explosion_ledger(records,spawns,terminals,player,rule):
    """Return all candidate objects, retaining invalid parents as blockers."""
    mapping={int(k):v for k,v in rule['projectileEffects'].items()}
    ledger=[];counts=Counter(s.get('projectileObjectId') for s in spawns)
    for s in spawns:
        if s.get('projectileCode') not in mapping or s.get('ownerPlayerObjectId')!=player:continue
        so=command_order(s);owners=[]
        for i,r in enumerate(records):
            a=r['start'];f=r['finish'];ao=command_order(a);fo=command_order(f) if f else None
            expected=761 if s['projectileCode']==105234 else 760
            if (a.get('skillIdCode')==expected and a.get('skillGroup')==(1052310 if expected==761 else 1052300)
                and f and f.get('reason') in set(range(15))|{16,17} and ao is not None and fo is not None and so is not None
                and ao<so<fo and a['tick']<=s['tick']<=f['tick']):owners.append(i)
        # Any overlapping family lifetime makes the parent ambiguous, even on another wire.
        overlaps=[i for i,r in enumerate(records) if command_order(r['start']) is not None and so is not None
            and command_order(r['start'])<so and (r['finish'] is None or command_order(r['finish']) is None or so<command_order(r['finish']))]
        parent=owners[0] if len(owners)==1 and overlaps==owners and counts[s['projectileObjectId']]==1 else None
        explosions=[e for e in terminals if e.get('objectId')==s['projectileObjectId'] and e.get('event')=='CmdProjectileExplosion']
        ledger.append(dict(spawn=s,parent=parent,effectCode=mapping[s['projectileCode']],explosions=explosions))
    return ledger

def assign_damage(d,ledger,records):
    candidates=[x for x in ledger if x['effectCode']==d.get('effectCode') and any(e.get('tick')==d['tick'] for e in x['explosions'])]
    parents={x['parent'] for x in candidates};do=command_order(d)
    valid=(len(candidates)==1 and None not in parents and do is not None)
    if valid:
        x=candidates[0];s=x['spawn'];es=x['explosions'];so=command_order(s)
        valid=(len(es)==1 and so is not None and command_order(es[0]) is not None
            and so<command_order(es[0])<do and s['tick']<=es[0]['tick']==d['tick'])
    proof=dict(damageTick=d['tick'],damageOrder=do,effectCode=d.get('effectCode'),targetObjectId=d.get('targetObjectId'),
        candidateObjects=[x['spawn']['projectileObjectId'] for x in candidates],
        candidateParents=[records[i]['start']['tick'] for i in parents if i is not None])
    if valid:
        x=candidates[0];proof.update(projectileCode=x['spawn']['projectileCode'],spawnOrder=command_order(x['spawn']),explosionOrder=command_order(x['explosions'][0]),castTick=records[x['parent']]['start']['tick'])
        return x['parent'],set(),proof
    # An unlinked effect cannot become a negative for a possibly responsible use.
    affected={i for i,r in enumerate(records) if r['start']['tick']<=d['tick'] and (not candidates or i in parents)}
    if None in parents:affected.update(i for i,r in enumerate(records) if r['start']['tick']<=d['tick'])
    proof['reason']='adina-explosion-effect-parent-unresolved'
    return None,affected,proof
