"""Observe requested stages through exact wire identities, without inventing casts."""
from collections import Counter,defaultdict
import hashlib
import json
from pathlib import Path
try:
    from .skill_summon_ownership import live_summon_owner_resolver
except ImportError:
    from skill_summon_ownership import live_summon_owner_resolver

ENUM_PATH=Path(__file__).resolve().parent.parent/'data/skill_id_enum_12_3_0.json'
ENUM_SHA256='ec71beb0e579e666f3bb1abd4850a88c7b880fae4f67e57dfb2677ddd9e85a28'
METADATA_SHA256='ede0935bbc00dcd93707d279f5a3f63570b8f09fcd9119d4582637f957afed61'
ORDINARY_ACTIONS={'decoded-exact-CmdPlaySkillAction','decoded-exact-CmdPlaySkillActionWithTargets'}


def load_exact_skill_ids():
    try:
        from .replay_input_context import current_input_version
    except ImportError:
        from replay_input_context import current_input_version
    version=current_input_version()
    if version == '12.4.0':
        path=ENUM_PATH.with_name('skill_id_enum_12_4_0.json')
        digest='d748d3ba79a60024824381d966d5f10c6fc3bfe95256093a36dc5f2ad5384a2c'
        metadata='70abb9a153c6c43109a3478aba4e960ce843b418d03aacc9ab61985c93270256'
    elif version in (None,'12.3.0'):
        # Preserve callers of the existing, explicitly 12.3 API.
        version='12.3.0';path=ENUM_PATH;digest=ENUM_SHA256;metadata=METADATA_SHA256
    else:
        raise ValueError('Unreviewed SkillId input version')
    data=path.read_bytes()
    if hashlib.sha256(data).hexdigest()!=digest:raise ValueError('exact SkillId enum content hash mismatch')
    value=json.loads(data)
    if (value['clientVersion']!=version or value['metadataSha256']!=metadata or
        value['namespace']!='Blis.Common' or value['enumName']!='SkillId'):
        raise ValueError('SkillId enum is not the exact replay version authority')
    return value['skillIds']


def observe_action_stages(catalog, skill_ids, requested_groups, players, starts, actions,
                          nonplayer_starts, summons, terminals, intervals):
    """Return count-only evidence per player/group; action count is not attempts."""
    player_characters={p['objectId']:p['characterCode'] for p in players}
    resolve=live_summon_owner_resolver(summons,terminals,set(player_characters))
    wire_groups=defaultdict(set)
    for group in requested_groups:
        definition=catalog['skillGroups'].get(str(group),{})
        wire=skill_ids.get(definition.get('skillId'))
        if type(wire) is int:wire_groups[wire].add(group)
    casts=Counter((s['playerObjectId'],s['skillGroup']) for s in starts)
    counts=defaultdict(Counter);actors=defaultdict(set);ticks=defaultdict(set);numbers=defaultdict(set)
    diagnostics=Counter()
    events=[('action',a) for a in actions if a.get('wireStatus') in ORDINARY_ACTIONS]
    events += [('nonplayer-start',s) for s in (nonplayer_starts or [])]
    for kind,event in events:
        groups=wire_groups.get(event['skillIdCode'])
        if not groups:continue
        source,tick=event['sourceObjectId'],event['tick']
        owner,path,reason=resolve(source,tick)
        if reason:diagnostics[reason]+=1;continue
        groups={g for g in groups if catalog['skillGroups'][str(g)]['characterCode']==player_characters[owner]}
        if len(groups)!=1:
            diagnostics['ambiguous-or-foreign-character-skill-identity']+=1;continue
        group=next(iter(groups));key=(owner,group)
        actors[key].add(source)
        if kind=='nonplayer-start':
            counts[key]['nonPlayerSkillStartPacketCount']+=1
            continue
        counts[key]['actionPacketCount']+=1
        counts[key]['summonActionPacketCount' if path else 'directPlayerActionPacketCount']+=1
        ticks[key].add((source,tick,event['actionNo']))
        numbers[key].add(event['actionNo'])
        if any(left<=tick<right for left,right in intervals.get(owner,[])):
            counts[key]['combatActionPacketCount']+=1
    output={}
    for (player,group),count in counts.items():
        output.setdefault(player,{})[group]={
            'status':'observed-exact-skill-action-not-a-hit-rate',
            'recordedPlayerCastStartCount':casts[player,group],
            'actionPacketCount':count['actionPacketCount'],
            'directPlayerActionPacketCount':count['directPlayerActionPacketCount'],
            'summonActionPacketCount':count['summonActionPacketCount'],
            'combatActionPacketCount':count['combatActionPacketCount'],
            'nonPlayerSkillStartPacketCount':count['nonPlayerSkillStartPacketCount'] if nonplayer_starts is not None else None,
            'nonPlayerSkillStartsCapability':nonplayer_starts is not None,
            'distinctSourceCount':len(actors[player,group]),
            'distinctSourceTickActionCount':len(ticks[player,group]),
            'actionNumbers':sorted(numbers[player,group]),
            'actionCountUsedAsAttemptCount':False,
        }
    return output,dict(diagnostics)


def callback_damage_corroborated(damages):
    """Binary target corroboration inside an already exact callback boundary.

    Multiple packets do not establish exact damage attribution. The explicit
    development policy can accept their shared target as a provisional hit.
    """
    if not damages or any(type(d.get('damageIsNull')) is not bool for d in damages):return False
    if len(damages)==1:return True
    from .skill_wire_order import command_order
    orders=[command_order(d) for d in damages]
    if None in orders or len(set(orders))!=len(orders):return False
    from .skill_development_effect_metrics import development_policy
    policy=development_policy()
    return policy['enabled'] and policy.get('lifecycleEstimatesEnabled',False)


def annotate_multiple_callback_damage(row, count):
    if not count:return row
    from .skill_development_effect_metrics import annotate_provisional_rate,development_policy
    row=annotate_provisional_rate(row,development_policy())
    row.update(multipleDamageCallbackUseCount=count,exactDamageAttributionEstablished=False,
               developmentAssumption='같은 적·동기 콜백 구간의 복수 피해를 잠정 적중으로 집계; 피해량의 스킬별 귀속은 확정하지 않음')
    return row


def development_terminal_damage(damage):
    """Accept only as corroboration inside a caller-proven attack callback.

    Zero effect is not a skill mapping. The callback and noncompeting attacker
    must be established by the caller before this provisional contact is used.
    """
    from .skill_wire_order import command_order
    from .skill_development_effect_metrics import development_policy
    policy=development_policy()
    return (policy['enabled'] and policy.get('lifecycleEstimatesEnabled',False)
        and damage.get('effectCode')==0 and damage.get('curHp')==0
        and damage.get('damageIsNull') is False and damage.get('damageType')==2
        and command_order(damage) is not None)
