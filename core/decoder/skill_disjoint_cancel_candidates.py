"""Explicitly provisional foreign-attack exclusion, never a non-attack claim."""
import hashlib
import json
from pathlib import Path
from .skill_wire_order import command_order


def disjoint_cancel_evidence(row,record,owned,spawns,starts,actions,player,gaps,catalog,db,policy):
    if (not policy.get('enabled') or
        (row.get('characterCode'),row.get('skillGroup'),record['start'].get('skillIdCode'),record['finish'].get('reason'))!=(25,1025200,336,3)
        or actions is None or gaps is None):return None
    if any(g.get('count',0) and any(str(g.get('packetName','')).startswith(n)
           for n in ('CmdStartSkill','CmdFinishSkill','CmdSpawn','CmdPlaySkillAction','CmdDamage')) for g in gaps):return None
    cfg=policy.get('experimentalDisjointAttackProjectiles')
    if not cfg:return None
    path=Path(__file__).resolve().parents[1]/cfg['proofPath']
    raw=path.read_bytes()
    if hashlib.sha256(raw).hexdigest()!=cfg['proofSha256']:return None
    proof=json.loads(raw)
    if proof.get('verifiedCompletionCredit') is not False:return None
    binding=next((b for b in proof['gameDbBindings'] if b['gameDataSha256']==db),None)
    if binding is None:return None
    for code,digest in binding['definitionHashes'].items():
        definition=(catalog or {}).get('projectileDefinitions',{}).get(code)
        if definition is None or hashlib.sha256(json.dumps(definition,sort_keys=True,separators=(',',':')).encode()).hexdigest()!=digest:return None
    left=command_order(record['start']);end=command_order(record['finish'])
    later=[s for s in starts if s.get('playerObjectId')==player and s.get('skillIdCode')==336 and s['tick']>record['start']['tick']]
    # A final use needs a separately proven observation end. This exception
    # does not invent one or fall back to an arbitrary delay.
    if not later or any(command_order(s) is None for s in later):return None
    next_use=min(later,key=command_order);right=command_order(next_use)
    if left is None or end is None or not left<=end<right:return None
    if not owned or any(s.get('projectileCode')!=102502 for s in owned):return None
    candidates=[]
    for s in spawns:
        if s.get('ownerPlayerObjectId')!=player:continue
        at=command_order(s)
        if at is None or type(s.get('projectileCode')) is not int:return None
        if left<=at<right and s['projectileCode']==102503:return None
        if left<=at<right:candidates.append(dict(s))
    aa=[a for a in actions if a.get('sourceObjectId')==player and a.get('skillIdCode')==335]
    if any(command_order(a) is None for a in aa):return None
    matching=[a for a in aa if left<=command_order(a)<=end and a.get('actionNo')==1]
    if not matching or any(a.get('wireStatus') not in ('decoded-exact-CmdPlaySkillAction','decoded-exact-CmdPlaySkillActionWithTargets') for a in matching):return None
    return dict(start=dict(record['start']),finish=dict(record['finish']),nextSameSkillStart=dict(next_use),
        excludedProjectileRecords=[dict(s) for s in owned],normalAttackActions=[dict(a) for a in matching],
        searchedProjectileRecords=candidates,proofPath=cfg['proofPath'],proofSha256=cfg['proofSha256'],
        gameDataSha256=db,assumedDisjointProjectileCode=102502,qCandidateProjectileCode=102503,
        nativeReplayRevisionMatched=False,verifiedCompletionCredit=False,
        assumption='Studied native normal-attack/Q separation is provisionally reused; a revision-specific Q emission of 102502 would make this exclusion wrong.')
