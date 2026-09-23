"""Build an anonymous, version-gated projectile hit-rate runtime audit.

This is deliberately narrower than the full combat-analysis builder.  It
exists to validate the D-056 cast -> projectile spawn -> collision contract on
an exact replay version without retaining nicknames, user ids, or raw object
ids in the output artifact.
"""

from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from functools import lru_cache
import hashlib
import json
from pathlib import Path
from urllib.parse import urlparse
import zipfile

import brotli

try:
    from .delta_payloads import SchemaDecoder
    from .inspect_deltas import iter_records, load_definitions, parse_delta_payload
    from .projectile_hit_catalog import (
        PROJECTILE_OBJECT_TYPES,
        VERSIONED_PROJECTILE_SPAWN_PREFIXES,
        attach_skill_action_anchors,
        build_projectile_skill_catalog,
        calculate_projectile_hit_rates,
        decode_versioned_projectile_spawn_identity,
    )
except ImportError:
    from delta_payloads import SchemaDecoder
    from inspect_deltas import iter_records, load_definitions, parse_delta_payload
    from projectile_hit_catalog import (
        PROJECTILE_OBJECT_TYPES,
        VERSIONED_PROJECTILE_SPAWN_PREFIXES,
        attach_skill_action_anchors,
        build_projectile_skill_catalog,
        calculate_projectile_hit_rates,
        decode_versioned_projectile_spawn_identity,
    )


HEADER_OFFSET = 0x410
SPAWN_PACKETS = {"CmdSpawn", "CmdSpawns"}
EXACT_RUNTIME_PACKETS = {
    "CmdStartSkill",
    "CmdPlaySkillAction",
    "CmdPlaySkillActionWithTargets",
    "CmdPlayStateSkillAction",
    "CmdUpdateInCombatType",
    "CmdProjectileCollision",
    "CmdDamage",
}
SKILL_CONTEXT_PACKETS = {
    'CmdStartNormalAttackSkill','CmdStartPassiveSkill','CmdPlayPassiveSkill','CmdFinishPassiveSkill',
    'CmdEvolutionSkill','CmdSwitchSkillSet','CmdSetSkillSequence','CmdResetSkillSequence',
}
WRAPPER_CATEGORIES = (
    "ignoreOrderPackets",
    "commands",
    "itemBoxPackets",
    "clientPackets",
)
SCOPE_EVIDENCE = (
    "derived-exact-own-combat-interval-containing-enemy-player-packet"
)


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def public_requested_metric(row: dict) -> dict:
    """Use the service projection; private contact/execution graphs stay in evidence."""
    try:
        from .attach_requested_skill_operation import public_metric
    except ImportError:
        from attach_requested_skill_operation import public_metric
    return public_metric(row, episode=True)


def replay_header(data: bytes, permitted_client_version: str | None = None) -> dict:
    if data[:16] != b"EternalReturnV1\0":
        raise ValueError("not an EternalReturnV1 replay")
    client_version = data[16:32].split(b"\0", 1)[0].decode("ascii")
    game_data_url = data[32:HEADER_OFFSET].split(b"\0", 1)[0].decode("ascii")
    if (
        client_version not in VERSIONED_PROJECTILE_SPAWN_PREFIXES
        and client_version != permitted_client_version
    ):
        raise ValueError(
            f"no versioned projectile runtime audit for {client_version}"
        )
    if not game_data_url:
        raise ValueError("replay header has no gameDb URL")
    return {
        "clientVersion": client_version,
        "gameDataUrl": game_data_url,
    }


def exact_players(first_snapshot: dict) -> tuple[dict[int, dict], dict[int, int]]:
    game_snapshot = first_snapshot.get("gameSnapshot")
    users = game_snapshot.get("userList") if isinstance(game_snapshot, dict) else None
    if not isinstance(users, list) or not users:
        raise ValueError("first snapshot has no exact game user list")
    players: dict[int, dict] = {}
    teammate_sets: dict[int, set[int]] = {}
    for user in users:
        if not isinstance(user, dict):
            raise ValueError("game user row is invalid")
        character_snapshot = user.get("characterSnapshot")
        route = user.get("route")
        route_desc = route.get("routeDesc") if isinstance(route, dict) else None
        object_id = (
            character_snapshot.get("objectId")
            if isinstance(character_snapshot, dict)
            else None
        )
        character_code = (
            route_desc.get("characterCode")
            if isinstance(route_desc, dict)
            else None
        )
        teammate_rows = user.get("teammateRouteList")
        if (
            not isinstance(object_id, int)
            or object_id <= 0
            or not isinstance(character_code, int)
            or character_code <= 0
            or not isinstance(teammate_rows, list)
        ):
            raise ValueError("game user identity/team fields are incomplete")
        teammates = {
            row.get("teammateObjectId")
            for row in teammate_rows
            if isinstance(row, dict)
            and isinstance(row.get("teammateObjectId"), int)
        }
        if object_id in players or len(teammates) != len(teammate_rows):
            raise ValueError("game user object/team rows are duplicate or invalid")
        players[object_id] = {"characterCode": character_code}
        teammate_sets[object_id] = teammates

    all_player_ids = set(players)
    team_by_player: dict[int, int] = {}
    remaining = set(all_player_ids)
    next_team = 0
    while remaining:
        first = min(remaining)
        component = {first, *teammate_sets[first]}
        if (
            not component <= all_player_ids
            or any({member, *teammate_sets[member]} != component for member in component)
        ):
            raise ValueError("teammate graph is not an exact symmetric component")
        next_team += 1
        for member in component:
            team_by_player[member] = next_team
        remaining -= component
    return players, team_by_player


def validated_external_players(
    external_player_map: dict[int, dict] | None,
) -> tuple[dict[int, dict], dict[int, int]] | None:
    if external_player_map is None:
        return None
    if not isinstance(external_player_map, dict) or not 1 <= len(external_player_map) <= 36:
        raise ValueError("external player map must contain 1..36 players")
    players: dict[int, dict] = {}
    team_by_player: dict[int, int] = {}
    for object_id, row in external_player_map.items():
        if (
            isinstance(object_id, bool)
            or not isinstance(object_id, int)
            or object_id <= 0
            or not isinstance(row, dict)
        ):
            raise ValueError("external player map identity is invalid")
        character_code = row.get("characterCode")
        team_number = row.get("teamNumber")
        if (
            isinstance(character_code, bool)
            or not isinstance(character_code, int)
            or character_code <= 0
            or isinstance(team_number, bool)
            or not isinstance(team_number, int)
            or team_number <= 0
        ):
            raise ValueError("external player map character/team is invalid")
        players[object_id] = {"characterCode": character_code}
        team_by_player[object_id] = team_number
    team_sizes = Counter(team_by_player.values())
    if any(size < 1 or size > 6 for size in team_sizes.values()):
        raise ValueError("external player map team size is invalid")
    return players, team_by_player


def build_combat_intervals(
    state_updates: list[tuple[int, int]], end_tick: int
) -> list[list[int]]:
    intervals: list[list[int]] = []
    start = None
    for tick, in_combat_type in sorted(state_updates):
        if in_combat_type != 0 and start is None:
            start = tick
        elif in_combat_type == 0 and start is not None:
            if tick > start:
                intervals.append([start, tick])
            start = None
    if start is not None and end_tick + 1 > start:
        intervals.append([start, end_tick + 1])
    return intervals


def resolve_skill_action_actors(
    skill_actions: list[dict],
    skill_starts: list[dict],
    player_ids: set[int],
) -> dict:
    """Resolve an action actor only from exact same-skill player evidence.

    State-skill packets can place the affected object in ``objectId`` while
    the actual caster is carried by ``casterId`` or ``stateCasterId``.  A
    field is accepted only when that exact player also has a decoded
    ``CmdStartSkill`` row with the same wire ``skillId``.  Ambiguous and
    unmatched actions remain unresolved rather than using field priority.
    """
    start_identities = {
        (row.get("playerObjectId"), row.get("skillIdCode"))
        for row in skill_starts
        if row.get("playerObjectId") in player_ids
        and isinstance(row.get("skillIdCode"), int)
    }
    resolved: list[dict] = []
    unresolved_count = 0
    ambiguous_count = 0
    authority_counts = Counter()
    for action in skill_actions:
        if not isinstance(action, dict):
            raise ValueError("skill action must be an object")
        skill_id = action.get("skillIdCode")
        candidates: dict[int, set[str]] = defaultdict(set)
        if isinstance(skill_id, int):
            for field in (
                "sourceObjectId",
                "casterObjectId",
                "stateCasterObjectId",
            ):
                candidate = action.get(field)
                if (
                    candidate in player_ids
                    and (candidate, skill_id) in start_identities
                ):
                    candidates[candidate].add(field)
        if len(candidates) != 1:
            unresolved_count += 1
            ambiguous_count += len(candidates) > 1
            continue
        player_id, fields = next(iter(candidates.items()))
        authority = "+".join(sorted(fields))
        authority_counts[authority] += 1
        resolved.append({
            **action,
            "playerObjectId": player_id,
            "actorAuthority": (
                "exact-same-skill-CmdStartSkill-match:" + authority
            ),
        })
    return {
        "actions": resolved,
        "resolvedCount": len(resolved),
        "unresolvedCount": unresolved_count,
        "ambiguousCount": ambiguous_count,
        "authorityCounts": dict(sorted(authority_counts.items())),
        "status": "exact-same-skill-player-action-actor-resolution",
        "fallbackUsed": False,
    }


def decoded_skill_context_fact(name, decoded, tick, packet_id=None):
    kept={'skillCode','skillEvolutionLevel','isCritical','reason','actionNo','skillSlotSet',
          'skillSlotIndex','pointType','currentPoint','selectCode','masteryType','sequence',
          'duration','maxDuration','sequenceCooldown'}
    return {'tick':tick,'event':name,'sourceObjectId':decoded.get('objectId'),
            'targetObjectId':decoded.get('targetObjectId',decoded.get('targetId')),
            'skillIdCode':decoded.get('skillId'),'targetPosition':decoded.get('targetPos'),
            'sourcePacketId':packet_id,**{k:v for k,v in decoded.items() if k in kept}}


def decoded_skill_start_fact(decoded: dict, tick: int) -> dict:
    """Preserve wire fields; evolution level is not a derived empowerment label."""
    return {'tick':tick,'playerObjectId':decoded.get('objectId'),
            'skillCode':decoded.get('skillCode'),'skillIdCode':decoded.get('skillId'),
            'skillEvolutionLevel':decoded.get('skillEvolutionLevel'),
            'targetObjectId':decoded.get('targetObjectId')}


def decoded_skill_finish_fact(decoded: dict, tick: int, include_details: bool = True) -> dict:
    return {'tick':tick,'playerObjectId':decoded.get('objectId'),'skillIdCode':decoded.get('skillId'),
            **({k:decoded[k] for k in ('reason','skillSlotSet') if k in decoded} if include_details else {})}


def decoded_state_change_fact(packet_name: str, decoded: dict, tick: int, include_details: bool = True) -> dict:
    # Wire duration/power are facts, not measured CC time or damage attribution.
    return {'tick':tick,'targetObjectId':decoded.get('objectId'),'casterObjectId':decoded.get('casterId'),
            'event':'remove' if packet_name=='CmdRemoveState' else 'add',
            'stateCode':decoded.get('code'),'stateGroup':decoded.get('group'),
            **({k:decoded[k] for k in ('stackCount','duration','originalDuration','power') if k in decoded}
               if include_details else {})}


def decoded_projectile_terminal_fact(packet_name: str, decoded: dict, tick: int) -> dict:
    return {'tick':tick,'objectId':decoded.get('objectId'),'event':packet_name,
            'isCollision':decoded.get('isCollision'),
            **{k:decoded[k] for k in ('arrivedPosVector2','destroyerObjectId',
                                      'collisionTargetEffectAndSoundCode','resultType') if k in decoded},
            **({'destroyerObjectId':decoded['destroyerId']} if 'destroyerId' in decoded else {})}


def resolve_exact_skill_damage_events(
    damages: list[dict], skill_rows: list[dict], character_state_rows: list[dict],
    catalog: dict, players: dict[int, dict], team_by_player: dict[int, int],
    direct_projectile_owner: dict[int, int],
) -> dict:
    """Keep enemy damage observable without inventing a cross-table skill FK.

    An EffectAndSound code can numerically equal Skill and CharacterState codes.
    Matching those integers and character groups is not a declared origin link.
    Skill-specific attribution belongs to the reviewed runtime-event routes.
    The arguments are retained for existing direct replay callers.
    """
    events=[];counts=Counter()
    for damage in damages:
        if not isinstance(damage,dict):raise ValueError('damage row must be an object')
        attacker=damage.get('attackerObjectId')
        owner=attacker if attacker in players else direct_projectile_owner.get(attacker)
        target=damage.get('targetObjectId');tick=damage.get('tick')
        if owner not in players or target not in players or type(tick) is not int or team_by_player[owner]==team_by_player[target]:continue
        counts['enemyPlayerDamageCount']+=1
        effect=damage.get('effectCode')
        status='unavailable-effect-code-has-no-skill-origin-FK' if type(effect) is int and effect>0 else 'unavailable-effect-code'
        counts[status]+=1
        events.append(dict(tick=tick,ownerPlayerObjectId=owner,targetPlayerObjectId=target,skillGroup=None,mappingStatus=status))
    return dict(events=events,counts=dict(sorted(counts.items())),
                status='effect-code-namespace-origin-unresolved',fallbackUsed=False)


def build_runtime_audit(
    replay_path: Path,
    game_data_path: Path,
    *,
    verified_prefix_layout: dict[int, int] | None = None,
    prefix_layout_authority: str | None = None,
    external_player_map: dict[int, dict] | None = None,
    expected_game_id: int | None = None,
    player_identity_authority: str | None = None,
    include_requested_skill_metrics: bool = False,
    include_skill_scope_probe: bool = False,
    include_full_requested_skill_metrics: bool = False,
    skill_scope_evidence_out_path: Path | None = None,
    calculate_full_requested_skill_metrics: bool = True,
    full_decode_path: Path | None = None,
    requested_metric_specs: list[dict] | None = None,
    skill_scope_identity_out_path: Path | None = None,
    skill_scope_evidence_result_out: dict | None = None,
    skill_scope_identity_result_out: dict | None = None,
    calculate_projectile_metrics: bool = True,
) -> dict:
    if not calculate_projectile_metrics and (include_requested_skill_metrics or
            (include_full_requested_skill_metrics and calculate_full_requested_skill_metrics)):
        raise ValueError('in-audit skill calculation requires its projectile metrics')
    if skill_scope_evidence_result_out is not None:
        if type(skill_scope_evidence_result_out) is not dict or skill_scope_evidence_result_out:
            raise ValueError('evidence handoff requires a new empty dict')
        if skill_scope_evidence_out_path is None and full_decode_path is None:
            raise ValueError('memory evidence requires the retained full decoded corpus')
    if skill_scope_identity_result_out is not None:
        if type(skill_scope_identity_result_out) is not dict or skill_scope_identity_result_out:
            raise ValueError('identity handoff requires a new empty dict')
        if skill_scope_evidence_result_out is None:
            raise ValueError('memory player identity requires memory evidence')
    if skill_scope_identity_out_path is not None:
        if (skill_scope_evidence_out_path is None and skill_scope_evidence_result_out is None) or not include_full_requested_skill_metrics:
            raise ValueError('private player map requires the exact newly built full-scope cache')
        if Path(skill_scope_identity_out_path).exists() or (skill_scope_evidence_out_path is not None and Path(skill_scope_identity_out_path).resolve()==Path(skill_scope_evidence_out_path).resolve()):
            raise ValueError('choose a new separate private player map path')
    # The new policy has its own evidence and output; legacy metrics stay opt-in.
    # Full-scope runs keep the historical probe artifacts. Target-only runs
    # still need the rich packet facts in the evidence cache (actionNo,
    # movement, state and terminal fields), but skip the expensive all-90
    # scope/deep-scope summaries because they are not used by the filtered
    # metric calculation.
    emit_scope_probe = include_skill_scope_probe or (
        include_full_requested_skill_metrics and requested_metric_specs is None
    )
    include_skill_scope_probe = include_skill_scope_probe or include_full_requested_skill_metrics
    replay_data = replay_path.read_bytes()
    permitted_version = None
    if verified_prefix_layout is not None:
        permitted_version = replay_data[16:32].split(b"\0", 1)[0].decode("ascii")
    header = replay_header(replay_data, permitted_version)
    corpus_source = None
    if full_decode_path is not None:
        try:
            from .corpus_runtime_source import CorpusRuntimeSource, restore as restore_corpus_packet
        except ImportError:
            from corpus_runtime_source import CorpusRuntimeSource, restore as restore_corpus_packet
        corpus_source = CorpusRuntimeSource(full_decode_path, sha256_bytes(replay_data), header['clientVersion'])
    expected_game_data_name = Path(
        urlparse(header["gameDataUrl"]).path
    ).name
    if game_data_path.name != expected_game_data_name:
        raise ValueError(
            "gameDb filename does not match the exact replay header URL: "
            f"{game_data_path.name} != {expected_game_data_name}"
        )
    game_data_bytes = game_data_path.read_bytes()
    catalog = build_projectile_skill_catalog(game_data_path)
    with zipfile.ZipFile(game_data_path) as archive:
        skill_rows = json.loads(archive.read("Skill.json"))
        character_state_rows = json.loads(archive.read("CharacterState.json"))
        requested_state_groups = (json.loads(archive.read("CharacterStateGroup.json"))
                                  if include_requested_skill_metrics or include_skill_scope_probe else None)
        requested_effect_rows = (json.loads(archive.read('EffectAndSound.json'))
                                 if include_full_requested_skill_metrics else None)
        projectile_rows = json.loads(archive.read("ProjectileSetting.json"))
        summon_definitions = ({row['code']:row for row in json.loads(archive.read('SummonObject.json'))}
                              if include_skill_scope_probe else {})
    skill_to_group = {
        row["code"]: row["group"]
        for row in skill_rows
        if isinstance(row.get("code"), int)
        and isinstance(row.get("group"), int)
    }
    projectile_codes = {
        row["code"]
        for row in projectile_rows
        if isinstance(row.get("code"), int)
    }

    records = list(iter_records(replay_data))
    definitions, packet_names = load_definitions(records)
    decoder = SchemaDecoder(
        {
            definition["name"]: definition.get("baseType")
            for definition in definitions
            if isinstance(definition.get("name"), str)
        },
        client_version=header["clientVersion"],
    )
    # This collector-owned decoder keeps its version/base/schema fixed for
    # the whole call. Reuse field lists instead of rebuilding inheritance
    # and Member objects for every nested snapshot and projectile movement.
    decoder.members_of = lru_cache(maxsize=None)(decoder.members_of)
    decoder.wire_members_of = lru_cache(maxsize=None)(decoder.wire_members_of)
    full_snapshots = [
        record
        for record in records
        if record["kind"] == 2 and record["version"] == 1
    ]
    if not full_snapshots:
        raise ValueError("replay has no full snapshot")
    external_players = validated_external_players(external_player_map)
    if external_players is None:
        if expected_game_id is not None or player_identity_authority is not None:
            raise ValueError("partial external player identity input is not allowed")
        first_snapshot = corpus_source.first_snapshot() if corpus_source is not None else decoder.decode_exact(
            brotli.decompress(full_snapshots[0]["payload"]), "ReplaySnapshot"
        )
        game_id = first_snapshot.get("gameId")
        if not isinstance(game_id, int) or game_id <= 0:
            raise ValueError("replay snapshot gameId is invalid")
        players, team_by_player = exact_players(first_snapshot)
        identity_authority = "official-er-first-full-snapshot"
        hybrid_source_used = False
    else:
        if (
            isinstance(expected_game_id, bool)
            or not isinstance(expected_game_id, int)
            or expected_game_id <= 0
            or player_identity_authority
            != "dak-transformed-same-game-users-v1"
        ):
            raise ValueError("external player identity authority is incomplete")
        first_snapshot_bytes = brotli.decompress(full_snapshots[0]["payload"])
        if len(first_snapshot_bytes) < 13:
            raise ValueError("first full snapshot is too small")
        embedded_game_id = int.from_bytes(
            first_snapshot_bytes[5:13], "little", signed=True
        )
        if embedded_game_id != expected_game_id:
            raise ValueError("external player map gameId does not match replay")
        game_id = expected_game_id
        players, team_by_player = external_players
        identity_authority = player_identity_authority
        hybrid_source_used = True
    player_ids = set(players)
    for object_id, row in players.items():
        character = catalog["characters"].get(str(row["characterCode"]))
        if not isinstance(character, dict):
            raise ValueError(
                f"replay character {row['characterCode']} is absent from gameDb"
            )
        row["characterNameInternal"] = character["characterNameInternal"]

    known_object_ids = set(player_ids)
    if external_players is None:
        game_snapshot = first_snapshot.get("gameSnapshot") or {}
        for wrapper in game_snapshot.get("worldSnapshot") or []:
            if isinstance(wrapper, dict) and isinstance(wrapper.get("objectId"), int):
                known_object_ids.add(wrapper["objectId"])

    spawn_wrappers: list[dict] = []
    raw_skill_starts: list[dict] = []
    raw_skill_actions: list[dict] = []
    requested_skill_finishes: list[dict] = []
    requested_states: list[dict] = []
    requested_projectile_terminals: list[dict] = []
    requested_state_scripts: list[dict] = []
    requested_heals: list[dict] = []
    requested_deaths: list[dict] = []
    requested_game_terminals: list[dict] = []
    requested_trap_events: list[dict] = []
    requested_rotation_events: list[dict] = []
    requested_pose_events: list[dict] = []
    requested_evasion_events: list[dict] = []
    requested_movement: list[dict] = []
    requested_summons: list[dict] = []
    requested_skill_contexts: list[dict] = []
    requested_direct_heals: list[dict] = []
    optional_scope_decode_gaps=Counter()
    extra_scope_packets={'CmdEvasion','CmdLockRotation','CmdActiveTrap','CmdBurstTrap','CmdDead','CmdKill','CmdDyingCondition','CmdFinishGame','CmdFinishGameResult','CmdMoveToDestination','CmdMoveToDestinationAvoidance',
                         'CmdStopMove','CmdMoveStraight','CmdMoveStraightWithoutNav'}
    try:
        from .skill_pose_events import pose_event_facts,pose_packet_channels,FULL_POSE_EVENT_SHAPE
    except ImportError:
        from skill_pose_events import pose_event_facts,pose_packet_channels,FULL_POSE_EVENT_SHAPE
    pose_packets=set(pose_packet_channels())
    state_updates: dict[int, list[tuple[int, int]]] = defaultdict(list)
    collisions: list[dict] = []
    damages: list[dict] = []
    decoded_packet_counts = Counter()
    delta_ticks = []

    requested_packets = ({"CmdFinishSkill", "CmdAddState", "CmdAddStateExtended", "CmdRemoveState"}
                         if include_requested_skill_metrics or include_skill_scope_probe else set())
    if include_skill_scope_probe:
        requested_packets |= SKILL_CONTEXT_PACKETS
        requested_packets |= {'CmdUpdateState','CmdResetCreateTimeState','CmdPauseState',
            'CmdProjectileExplosion','CmdProjectileArrived','CmdProjectileCollisionWall',
            'CmdProjectileDestroyedByExternalObject','CmdDestroy','CmdDestroyDelayStart',
            'CmdStartStateSkill','CmdFinishStateSkill','CmdHealStateCode','CmdHeal'}
        requested_packets |= extra_scope_packets
        requested_packets |= pose_packets
    selected_packet_names = SPAWN_PACKETS | EXACT_RUNTIME_PACKETS | requested_packets
    input_records = corpus_source.deltas(selected_packet_names,runtime_only=True) if corpus_source is not None else records

    for record_number,record in enumerate(input_records,1):
        if record["kind"] != 1 or record["version"] != 1:
            continue
        delta_ticks.append(record["tick"])
        delta = record['corpusDelta'] if corpus_source is not None else parse_delta_payload(record["payload"], record["tick"])
        for category in WRAPPER_CATEGORIES:
            for packet_ordinal,wrapper in enumerate(delta[category]):
                packet_name = packet_names.get(wrapper["packetType"])
                payload = wrapper.get("payload")
                if packet_name not in selected_packet_names:
                    continue
                if (wrapper['corpusPayloadPresent'] is not True if corpus_source is not None else payload is None):
                    raise ValueError(f"{packet_name} has a null payload")
                try:
                    decoded = restore_corpus_packet(wrapper['corpusDecoded']) if corpus_source is not None else decoder.decode_exact(payload, packet_name)
                except ValueError:
                    if include_skill_scope_probe and packet_name in extra_scope_packets | pose_packets:
                        optional_scope_decode_gaps[packet_name]+=1
                        continue
                    raise
                decoded_packet_counts[packet_name] += 1
                if include_skill_scope_probe and packet_name in pose_packets:
                    requested_pose_events.extend(pose_event_facts(packet_name,decoded,record['tick'],category,
                        wrapper.get('corpusWireOrder',[record_number,packet_ordinal])))
                if include_skill_scope_probe and packet_name in SKILL_CONTEXT_PACKETS:
                    requested_skill_contexts.append({**decoded_skill_context_fact(packet_name,decoded,record['tick'],wrapper.get('corpusPacketId')),
                        'wireCategory':category,'wireOrder':wrapper.get('corpusWireOrder',[record_number,packet_ordinal])})
                elif packet_name in SPAWN_PACKETS:
                    wrappers = (
                        [decoded["snapshot"]]
                        if packet_name == "CmdSpawn"
                        else (decoded["snapshots"] or [])
                    )
                    for snapshot_wrapper in wrappers:
                        if snapshot_wrapper is None:
                            continue
                        object_id = snapshot_wrapper.get("objectId")
                        object_type = snapshot_wrapper.get("objectType")
                        if not isinstance(object_id, int) or not isinstance(
                            object_type, int
                        ):
                            raise ValueError("spawn wrapper identity is invalid")
                        known_object_ids.add(object_id)
                        spawn_wrappers.append({
                            'wireCategory':category,'wireOrder':wrapper.get('corpusWireOrder',[record_number,packet_ordinal]),
                            "tick": record["tick"],
                            "objectId": object_id,
                            "objectType": object_type,
                            "snapshot": snapshot_wrapper.get("snapshot"),
                            "positionXZ":snapshot_wrapper.get('positionXZ'),
                            "positionY":snapshot_wrapper.get('positionY'),
                        })
                        if include_skill_scope_probe and object_type in {9,10,11,21,25}:
                            try:
                                try:
                                    from .skill_summon_ownership import decode_summon_fact
                                except ImportError:
                                    from skill_summon_ownership import decode_summon_fact
                                requested_summons.append(decode_summon_fact(decoder,snapshot_wrapper,record['tick'],summon_definitions))
                            except (ValueError,TypeError):
                                optional_scope_decode_gaps['SummonSnapshot:'+str(object_type)]+=1
                elif include_skill_scope_probe and packet_name in {'CmdDead','CmdKill','CmdDyingCondition'}:
                    requested_deaths.append({'tick':record['tick'],'event':packet_name,
                        'deadObjectId':decoded.get('deadCharacterObjectId') if packet_name=='CmdKill' else decoded.get('objectId'),
                        'killerObjectId':decoded.get('objectId') if packet_name=='CmdKill' else decoded.get('finishingAttackerObjectId'),
                        'isDyingBlockDead':decoded.get('isDyingBlockDead'),
                        'wireCategory':category,'wireOrder':wrapper.get('corpusWireOrder',[record_number,packet_ordinal])})
                elif include_skill_scope_probe and packet_name in {'CmdFinishGame','CmdFinishGameResult'}:
                    try:
                        from .skill_ordered_match_end import game_terminal_fact
                    except ImportError:
                        from skill_ordered_match_end import game_terminal_fact
                    requested_game_terminals.append(game_terminal_fact(packet_name,record['tick'],category,
                        wrapper.get('corpusWireOrder',[record_number,packet_ordinal]),decoded))
                elif include_skill_scope_probe and packet_name == 'CmdEvasion':
                    try:
                        from .skill_evasion_events import evasion_event_fact
                    except ImportError:
                        from skill_evasion_events import evasion_event_fact
                    requested_evasion_events.append(evasion_event_fact(record['tick'],category,
                        wrapper.get('corpusWireOrder',[record_number,packet_ordinal]),decoded))
                elif include_skill_scope_probe and packet_name == 'CmdLockRotation':
                    try:
                        from .skill_rotation_events import rotation_event_fact
                    except ImportError:
                        from skill_rotation_events import rotation_event_fact
                    requested_rotation_events.append(rotation_event_fact(record['tick'],category,
                        wrapper.get('corpusWireOrder',[record_number,packet_ordinal]),decoded))
                elif include_skill_scope_probe and packet_name in {'CmdActiveTrap','CmdBurstTrap'}:
                    try:
                        from .skill_trap_events import trap_event_fact
                    except ImportError:
                        from skill_trap_events import trap_event_fact
                    requested_trap_events.append(trap_event_fact(packet_name,record['tick'],category,
                        wrapper.get('corpusWireOrder',[record_number,packet_ordinal]),decoded))
                elif include_skill_scope_probe and packet_name in extra_scope_packets:
                    fields={'positionVector2','destinationVector2','relativeDestinationVector2','cornersVector2',
                            'startPosVector2','endPosVector2','duration','ease'}
                    requested_movement.append({'tick':record['tick'],'event':packet_name,'objectId':decoded.get('objectId'),
                        'wireCategory':category,'wireOrder':wrapper.get('corpusWireOrder',[record_number,packet_ordinal]),
                        'positionFieldType':next((m.field_type for m in decoder.wire_members_of(packet_name) if m.name=='positionVector2'),None),
                        **{k:v for k,v in decoded.items() if k in fields}})
                elif packet_name == "CmdStartSkill":
                    raw_skill_starts.append({**decoded_skill_start_fact(decoded,record["tick"]),
                        'wireCategory':category,'wireOrder':wrapper.get('corpusWireOrder',[record_number,packet_ordinal])})
                elif (include_requested_skill_metrics or include_skill_scope_probe) and packet_name == "CmdFinishSkill":
                    requested_skill_finishes.append({**decoded_skill_finish_fact(decoded,record['tick'],include_skill_scope_probe),
                        'wireCategory':category,'wireOrder':wrapper.get('corpusWireOrder',[record_number,packet_ordinal])})
                elif (include_requested_skill_metrics or include_skill_scope_probe) and packet_name in {"CmdAddState", "CmdAddStateExtended", "CmdRemoveState"}:
                    requested_states.append({**decoded_state_change_fact(packet_name,decoded,record['tick'],include_skill_scope_probe),
                        'wireCategory':category,'wireOrder':wrapper.get('corpusWireOrder',[record_number,packet_ordinal])})
                elif include_skill_scope_probe and packet_name in {'CmdUpdateState','CmdResetCreateTimeState','CmdPauseState'}:
                    requested_states.append({
                        'tick':record['tick'],'targetObjectId':decoded.get('objectId'),
                        'casterObjectId':decoded.get('casterId'),'event':packet_name,
                        'stateCode':None,'stateGroup':decoded.get('group'),
                        'stackCount':decoded.get('stackCount'),'reserveCount':decoded.get('reserveCount'),
                        'createdTime':decoded.get('createdTime'),'duration':decoded.get('duration'),
                        'wireCategory':category,'wireOrder':wrapper.get('corpusWireOrder',[record_number,packet_ordinal])})
                elif include_skill_scope_probe and packet_name in {'CmdProjectileExplosion','CmdProjectileArrived',
                        'CmdProjectileCollisionWall','CmdProjectileDestroyedByExternalObject','CmdDestroy','CmdDestroyDelayStart'}:
                    requested_projectile_terminals.append({**decoded_projectile_terminal_fact(packet_name,decoded,record['tick']),
                        'wireCategory':category,'wireOrder':wrapper.get('corpusWireOrder',[record_number,packet_ordinal])})
                elif include_skill_scope_probe and packet_name in {'CmdStartStateSkill','CmdFinishStateSkill'}:
                    requested_state_scripts.append({'tick':record['tick'],'event':packet_name,
                        'wireCategory':category,'wireOrder':wrapper.get('corpusWireOrder',[record_number,packet_ordinal]),
                        'sourceObjectId':decoded.get('objectId'),'casterObjectId':decoded.get('casterId'),
                        'skillIdCode':decoded.get('skillId'),'skillCode':decoded.get('skillCode'),
                        'skillEvolutionLevel':decoded.get('skillEvolutionLevel'),
                        'stateGroup':decoded.get('stateGroup'),'reason':decoded.get('reason')})
                elif include_skill_scope_probe and packet_name=='CmdHeal':
                    requested_direct_heals.append({'tick':record['tick'],
                        'targetObjectId':decoded.get('objectId'),'casterObjectId':decoded.get('casterId'),
                        'effectCode':decoded.get('effectCode'),'addHp':decoded.get('addHp'),'addVp':decoded.get('addVp'),
                        'wireCategory':category,'wireOrder':wrapper.get('corpusWireOrder',[record_number,packet_ordinal])})
                elif include_skill_scope_probe and packet_name=='CmdHealStateCode':
                    requested_heals.append({'tick':record['tick'],'targetObjectId':decoded.get('objectId'),
                        'casterObjectId':decoded.get('casterId'),'stateCode':decoded.get('stateCode'),
                        'effectCode':decoded.get('effectCode')})
                elif packet_name in {
                    "CmdPlaySkillAction",
                    "CmdPlaySkillActionWithTargets",
                    "CmdPlayStateSkillAction",
                }:
                    raw_targets = decoded.get("targets")
                    targets = []
                    if isinstance(raw_targets, list):
                        for target in raw_targets:
                            if not isinstance(target, dict):
                                raise ValueError(
                                    f"{packet_name} has an invalid skill-action target"
                                )
                            target_id = target.get("targetId")
                            target_position = target.get("targetPos")
                            if not isinstance(target_id, int):
                                raise ValueError(
                                    f"{packet_name} target has no exact targetId"
                                )
                            targets.append({
                                "targetObjectId": target_id,
                                "hasTargetPosition": target_position is not None,
                                **({'targetPosition':target_position} if include_skill_scope_probe else {}),
                            })
                    raw_skill_actions.append({
                        "tick": record["tick"],
                        "sourceObjectId": decoded.get("objectId"),
                        "skillIdCode": decoded.get("skillId"),
                        "casterObjectId": decoded.get("casterId"),
                        "stateCasterObjectId": decoded.get("stateCasterId"),
                        "stateGroup": decoded.get("stateGroup"),
                        **({"actionNo": decoded.get("actionNo"),
                            "wireCategory": category,
                            "wireOrder": wrapper.get('corpusWireOrder',[record_number,packet_ordinal]),
                            "decodedFieldNames": sorted(decoded)} if include_skill_scope_probe else {}),
                        "targets": targets,
                        "wireStatus": f"decoded-exact-{packet_name}",
                    })
                elif packet_name == "CmdUpdateInCombatType":
                    object_id = decoded.get("objectId")
                    in_combat_type = decoded.get("inCombatType")
                    if object_id in player_ids and isinstance(in_combat_type, int):
                        state_updates[object_id].append(
                            (record["tick"], in_combat_type)
                        )
                elif packet_name == "CmdProjectileCollision":
                    collisions.append({
                        'wireCategory':category,'wireOrder':wrapper.get('corpusWireOrder',[record_number,packet_ordinal]),
                        "tick": record["tick"],
                        "projectileObjectId": decoded.get("objectId"),
                        "targetObjectId": decoded.get("targetId"),
                    })
                elif packet_name == "CmdDamage":
                    damages.append({
                        'wireCategory':category,'wireOrder':wrapper.get('corpusWireOrder',[record_number,packet_ordinal]),
                        "tick": record["tick"],
                        "attackerObjectId": decoded.get("attackerId"),
                        "targetObjectId": decoded.get("objectId"),
                        "effectCode": decoded.get("effectCode"),
                        **({'isCritical':decoded.get('isCritical'),
                            'damageFontDisplayType':decoded.get('damageFontDisplayType'),
                            'damageType':decoded.get('damageType'),
                            'damageIsNull':decoded.get('damage') is None,
                            'curHp':decoded.get('curHp')} if include_skill_scope_probe else {}),
                    })
    if not delta_ticks:
        raise ValueError("replay has no delta records")

    projectile_spawns_all: list[dict] = []
    projectile_movement_facts=[]
    if include_full_requested_skill_metrics:
        try:
            from .projectile_movement_facts import projectile_movement_fact
        except ImportError:
            from projectile_movement_facts import projectile_movement_fact
        projectile_definitions_by_code={p['code']:p for p in projectile_rows}
    prefix_counts = Counter()
    unobserved_owner_projectile_count = 0
    projectile_object_types = (
        set(verified_prefix_layout)
        if verified_prefix_layout is not None
        else PROJECTILE_OBJECT_TYPES
    )
    for wrapper in spawn_wrappers:
        if wrapper["objectType"] not in projectile_object_types:
            continue
        nested = wrapper["snapshot"]
        if nested is None:
            raise ValueError("projectile spawn has no nested snapshot")
        identity = decode_versioned_projectile_spawn_identity(
            header["clientVersion"],
            wrapper["objectType"],
            nested,
            verified_layout=verified_prefix_layout,
            layout_authority=prefix_layout_authority,
        )
        if identity["projectileCode"] not in projectile_codes:
            raise ValueError(
                "versioned projectile prefix code is absent from exact gameDb"
            )
        owner_object_id = identity["ownerObjectId"]
        if include_full_requested_skill_metrics:
            projectile_movement_facts.append(projectile_movement_fact(wrapper,identity,
                projectile_definitions_by_code[identity['projectileCode']],decoder))
        if not isinstance(owner_object_id, int) or owner_object_id <= 0:
            raise ValueError("versioned projectile prefix owner is invalid")
        if owner_object_id not in known_object_ids:
            # With an externally supplied same-game player map, the 12.3 full
            # world snapshot is deliberately not decoded.  A valid projectile
            # can therefore be owned by an initial summon/world object that is
            # absent from the observed delta-spawn set.  Keep it unresolved;
            # only an exact direct player id is eligible for hit-rate linkage.
            unobserved_owner_projectile_count += 1
        prefix_counts[wrapper["objectType"]] += 1
        projectile_spawns_all.append({
            'wireCategory':wrapper.get('wireCategory'),'wireOrder':wrapper.get('wireOrder'),
            "tick": wrapper["tick"],
            "projectileObjectId": wrapper["objectId"],
            "projectileCode": identity["projectileCode"],
            "ownerObjectId": owner_object_id,
        })
    projectile_spawn_ids = {
        row["projectileObjectId"] for row in projectile_spawns_all
    }
    if any(
        row["projectileObjectId"] not in projectile_spawn_ids
        for row in collisions
    ):
        raise ValueError("projectile collision has no exact versioned spawn")

    direct_player_projectiles = [
        {
            **row,
            "ownerPlayerObjectId": row["ownerObjectId"],
        }
        for row in projectile_spawns_all
        if row["ownerObjectId"] in player_ids
    ]
    direct_projectile_owner = {
        row["projectileObjectId"]: row["ownerPlayerObjectId"]
        for row in direct_player_projectiles
    }

    skill_starts = []
    unresolved_skill_start_count = 0
    for row in raw_skill_starts:
        player_id = row["playerObjectId"]
        group = skill_to_group.get(row["skillCode"])
        definition = catalog["skillGroups"].get(str(group))
        if (
            player_id not in player_ids
            or not isinstance(group, int)
            or not isinstance(definition, dict)
            or definition["characterCode"] != players[player_id]["characterCode"]
        ):
            unresolved_skill_start_count += 1
            continue
        skill_starts.append({
            **row,
            "skillGroup": group,
            "wireStatus": "decoded-exact-CmdStartSkill",
        })
    action_actor_resolution = resolve_skill_action_actors(
        raw_skill_actions,
        skill_starts,
        player_ids,
    )
    skill_actions = action_actor_resolution["actions"]
    exact_direct_damage_targets = {
        (
            row["tick"],
            row["attackerObjectId"],
            row["targetObjectId"],
        )
        for row in damages
        if row.get("attackerObjectId") in player_ids
        and row.get("targetObjectId") in player_ids
    }
    exact_owned_projectile_collision_targets = {
        (
            row["tick"],
            direct_projectile_owner.get(row["projectileObjectId"]),
            row["targetObjectId"],
        )
        for row in collisions
        if direct_projectile_owner.get(row.get("projectileObjectId"))
        in player_ids
        and row.get("targetObjectId") in player_ids
    }
    for action in skill_actions:
        actor = action["playerObjectId"]
        for target in action.get("targets") or []:
            target_id = target["targetObjectId"]
            key = (action["tick"], actor, target_id)
            target["sameTickDirectPlayerDamage"] = (
                key in exact_direct_damage_targets
            )
            target["sameTickOwnedProjectileCollision"] = (
                key in exact_owned_projectile_collision_targets
            )
    skill_starts = attach_skill_action_anchors(skill_starts, skill_actions)

    exact_skill_damage_resolution = resolve_exact_skill_damage_events(
        damages,
        skill_rows,
        character_state_rows,
        catalog,
        players,
        team_by_player,
        direct_projectile_owner,
    )

    combat_intervals = {
        player_id: build_combat_intervals(
            state_updates[player_id], max(delta_ticks)
        )
        for player_id in player_ids
    }
    pvp_signal_ticks: dict[int, list[int]] = defaultdict(list)
    for collision in collisions:
        owner = direct_projectile_owner.get(
            collision["projectileObjectId"]
        )
        target = collision["targetObjectId"]
        if (
            owner in player_ids
            and target in player_ids
            and team_by_player[owner] != team_by_player[target]
        ):
            pvp_signal_ticks[owner].append(collision["tick"])
            pvp_signal_ticks[target].append(collision["tick"])
    for damage in damages:
        attacker = damage["attackerObjectId"]
        owner = (
            attacker
            if attacker in player_ids
            else direct_projectile_owner.get(attacker)
        )
        target = damage["targetObjectId"]
        if (
            owner in player_ids
            and target in player_ids
            and team_by_player[owner] != team_by_player[target]
        ):
            pvp_signal_ticks[owner].append(damage["tick"])
            pvp_signal_ticks[target].append(damage["tick"])
    exact_skill_action_target_count = sum(
        len(action.get("targets") or []) for action in raw_skill_actions
    )
    targeted_skill_action_count = sum(
        bool(action.get("targets")) for action in raw_skill_actions
    )
    resolved_skill_action_target_count = 0
    resolved_targeted_skill_action_count = 0
    enemy_player_skill_action_target_count = 0
    for action in skill_actions:
        actor = action.get("playerObjectId")
        targets = action.get("targets") or []
        if targets:
            resolved_targeted_skill_action_count += 1
        resolved_skill_action_target_count += len(targets)
        if actor not in player_ids:
            continue
        for target_row in targets:
            target = target_row["targetObjectId"]
            if (
                target in player_ids
                and team_by_player[actor] != team_by_player[target]
            ):
                enemy_player_skill_action_target_count += 1
                pvp_signal_ticks[actor].append(action["tick"])
                pvp_signal_ticks[target].append(action["tick"])
    confirmed_pvp_intervals = {
        player_id: [
            interval
            for interval in combat_intervals[player_id]
            if any(
                interval[0] <= tick < interval[1]
                for tick in pvp_signal_ticks[player_id]
            )
        ]
        for player_id in player_ids
    }

    runtime = calculate_projectile_hit_rates(
        catalog,
        skill_starts,
        direct_player_projectiles,
        collisions,
        team_by_player,
        confirmed_pvp_intervals,
        exact_skill_damage_events=exact_skill_damage_resolution["events"],
        scope_evidence=SCOPE_EVIDENCE,
        output_skill_groups=None if calculate_projectile_metrics else set(),
    )
    evidence_cache_storage=None
    if skill_scope_evidence_out_path is not None or skill_scope_evidence_result_out is not None:
        if not include_full_requested_skill_metrics:
            raise ValueError('evidence cache needs complete opt-in skill packet decoding')
        try:
            from .skill_scope_evidence_cache import build_evidence_handoff
        except ImportError:
            from skill_scope_evidence_cache import build_evidence_handoff
        private_player_map=(skill_scope_identity_result_out if skill_scope_identity_result_out is not None
                            else {} if skill_scope_identity_out_path is not None else None)
        evidence_cache_storage=build_evidence_handoff(path=skill_scope_evidence_out_path,
            pose_event_shape=FULL_POSE_EVENT_SHAPE,
            result_out=skill_scope_evidence_result_out,
            player_identity_result_out=private_player_map,
            client_version=header['clientVersion'],
            player_identity_map_out=private_player_map,
            game_db_sha256=sha256_bytes(game_data_bytes),match_key=sha256_bytes(replay_data),
            players=players,teams=team_by_player,intervals=confirmed_pvp_intervals,
            starts=skill_starts,actions=raw_skill_actions,spawns=direct_player_projectiles,
            collisions=collisions,finishes=requested_skill_finishes,states=requested_states,
            damages=damages,terminals=requested_projectile_terminals,
            stateScripts=requested_state_scripts,heals=requested_heals,objects=spawn_wrappers,
            deaths=requested_deaths,movement=requested_movement,summons=requested_summons,gameTerminals=requested_game_terminals,trapEvents=requested_trap_events,rotationEvents=requested_rotation_events,evasionEvents=requested_evasion_events,poseEvents=requested_pose_events,
            allProjectileSpawns=projectile_spawns_all,
            nonPlayerSkillStarts=[{'tick':s['tick'],'sourceObjectId':s['playerObjectId'],
                'skillCode':s['skillCode'],'skillIdCode':s['skillIdCode'],'skillEvolutionLevel':s.get('skillEvolutionLevel'),'targetObjectId':s.get('targetObjectId'),
                'wireCategory':s.get('wireCategory'),'wireOrder':s.get('wireOrder')}
                for s in raw_skill_starts if s['playerObjectId'] not in player_ids],
            gaps=[{'packetName':k,'count':v,'reasonCode':'exact-schema-decoding-unavailable'} for k,v in sorted(optional_scope_decode_gaps.items())],
            skillContexts=requested_skill_contexts,projectileMovement=projectile_movement_facts,
            **({'directHeals':requested_direct_heals} if not optional_scope_decode_gaps.get('CmdHeal') else {}))
        if private_player_map is not None:
            if skill_scope_evidence_out_path is not None:
                private_player_map['evidenceCacheSha256']=evidence_cache_storage['sha256']
            else:
                private_player_map['evidenceStorageMode']='in-memory'
                private_player_map['collectorContractSha256']=evidence_cache_storage['collectorContractSha256']
                private_player_map['decoderContractSha256']=evidence_cache_storage['decoderContractSha256']
            if skill_scope_identity_result_out is not None:
                skill_scope_identity_result_out.update(private_player_map)
            if skill_scope_identity_out_path is not None:
                with Path(skill_scope_identity_out_path).open('x',encoding='utf-8') as handle:
                    json.dump(private_player_map,handle,ensure_ascii=False,indent=2)
                    handle.write('\n')
    requested_metrics = None
    scope_probe = None
    deep_scope_probe = None
    full_requested_metrics = None
    if emit_scope_probe and calculate_full_requested_skill_metrics:
        try:
            from .skill_scope_probe import summarize_scope_evidence
        except ImportError:
            from skill_scope_probe import summarize_scope_evidence
        scope_probe = summarize_scope_evidence(
            players, team_by_player, skill_starts, requested_skill_finishes,
            skill_actions, damages, requested_states, skill_rows,
            character_state_rows, direct_projectile_owner, confirmed_pvp_intervals)
        try:
            from .skill_scope_deep_probe import summarize_deep_scope_evidence
        except ImportError:
            from skill_scope_deep_probe import summarize_deep_scope_evidence
        deep_scope_probe = summarize_deep_scope_evidence(players,team_by_player,skill_starts,
            requested_skill_finishes,requested_states,damages,direct_player_projectiles,
            collisions,requested_projectile_terminals,direct_projectile_owner,character_state_rows,
            state_scripts=requested_state_scripts,heals=requested_heals,raw_actions=raw_skill_actions)
    if include_full_requested_skill_metrics and calculate_full_requested_skill_metrics:
        try:
            from .requested_skill_scope import calculate_scope_metrics
        except ImportError:
            from requested_skill_scope import calculate_scope_metrics
        full_requested_metrics = calculate_scope_metrics(
            catalog, runtime, skill_starts, direct_player_projectiles, collisions,
            team_by_player, confirmed_pvp_intervals, client_version=header['clientVersion'],
            game_db_sha256=sha256_bytes(game_data_bytes),
            wall_inputs={'finishes':requested_skill_finishes,'states':requested_states,'state_scripts':requested_state_scripts,
                         'skill_rows':skill_rows,'state_rows':character_state_rows,
                         'state_groups':requested_state_groups,'movement':requested_movement,'deaths':requested_deaths,
                         'gameTerminals':requested_game_terminals,'trapEvents':requested_trap_events,'rotationEvents':requested_rotation_events,'evasionEvents':requested_evasion_events,'skillContexts':requested_skill_contexts,'poseEvents':requested_pose_events},
            damages=damages,effect_rows=requested_effect_rows,projectile_owners=direct_projectile_owner,
            projectile_terminals=requested_projectile_terminals,raw_actions=raw_skill_actions,
            direct_heals=requested_direct_heals if not optional_scope_decode_gaps.get('CmdHeal') else None,
            metric_specs=requested_metric_specs,
            summon_inputs={'summons':requested_summons,'objects':spawn_wrappers,'allProjectileSpawns':projectile_spawns_all,
                           'projectileMovement':projectile_movement_facts,
                           'gaps':[{'packetName':k,'count':v} for k,v in optional_scope_decode_gaps.items()],
                           'players':[{'objectId':object_id,'characterCode':info['characterCode']} for object_id,info in players.items()],
                           'nonPlayerSkillStarts':[{'tick':s['tick'],'sourceObjectId':s['playerObjectId'],
                               'skillIdCode':s['skillIdCode'],'skillCode':s['skillCode'],'skillEvolutionLevel':s.get('skillEvolutionLevel'),'targetObjectId':s.get('targetObjectId'),
                               'wireCategory':s.get('wireCategory'),'wireOrder':s.get('wireOrder')}
                               for s in raw_skill_starts if s['playerObjectId'] not in players],
                           'summon_rows':list(summon_definitions.values()),'projectile_rows':projectile_rows})
    if include_requested_skill_metrics:
        try:
            from .requested_skill_hit_rates import calculate_requested_metrics
        except ImportError:
            from requested_skill_hit_rates import calculate_requested_metrics
        requested_metrics = calculate_requested_metrics(
            catalog, runtime, skill_starts, direct_player_projectiles, collisions,
            team_by_player, confirmed_pvp_intervals,
            exact_skill_damage_resolution["events"],
            client_version=header["clientVersion"],
            game_db_sha256=sha256_bytes(game_data_bytes),
            wall_inputs={"finishes": requested_skill_finishes, "states": requested_states,
                         "skill_rows": skill_rows, "state_rows": character_state_rows,
                         "state_groups": requested_state_groups},
        )
    ordered_players = sorted(
        player_ids, key=lambda player_id: (team_by_player[player_id], player_id)
    )
    public_id_by_player = {
        player_id: f"P{index:02d}"
        for index, player_id in enumerate(ordered_players, start=1)
    }
    output_players = []
    verified_count = 0
    calculable_count = 0
    for player_id in ordered_players:
        rows = runtime["players"][str(player_id)]
        for row in rows:
            row.pop("_combatAttemptOutcomes", None)
            for evidence_key in ("actionTargetEvidence", "exactEffectDamageEvidence"):
                evidence = row.get(evidence_key)
                if isinstance(evidence, dict):
                    evidence.pop("_combatCastOutcomes", None)
        verified_count += sum(
            row["projectileStatus"]
            == "verified-observed-exclusive-cast-spawn-link"
            for row in rows
        )
        calculable_count += sum(row["hitRateCalculable"] for row in rows)
        output_players.append({
            "publicPlayerId": public_id_by_player[player_id],
            "teamNumber": team_by_player[player_id],
            "characterCode": players[player_id]["characterCode"],
            "characterNameInternal": players[player_id][
                "characterNameInternal"
            ],
            "confirmedPvpIntervalCount": len(
                confirmed_pvp_intervals[player_id]
            ),
            "skills": rows,
            **({"requestedSkillMetrics": requested_metrics[str(player_id)]}
               if requested_metrics is not None else {}),
            **({"fullRequestedSkillMetrics": [public_requested_metric(metric)
                                               for metric in full_requested_metrics[str(player_id)]]}
               if full_requested_metrics is not None else {}),
        })

    return {
        "format": "er-projectile-runtime-audit.v1",
        "status": "verified-versioned-projectile-runtime-audit",
        "projectileMetricsCalculated": calculate_projectile_metrics,
        "clientVersion": header["clientVersion"],
        **({"skillScopeProbe": scope_probe} if scope_probe is not None else {}),
        **({'deepSkillScopeProbe':deep_scope_probe} if deep_scope_probe is not None else {}),
        **({'skillScopeEvidenceCacheStorage':evidence_cache_storage} if evidence_cache_storage is not None else {}),
        "source": {
            "replaySha256": sha256_bytes(replay_data),
            "replayBytes": len(replay_data),
            "gameDataSha256": sha256_bytes(game_data_bytes),
            "privacy": (
                "anonymous derived output; no nickname, user id, userNum, "
                "or raw replay object id"
            ),
            "projectilePrefixLayoutAuthority": (
                prefix_layout_authority
                if verified_prefix_layout is not None
                else f"promoted-version-table-{header['clientVersion']}"
            ),
            "projectilePrefixLayout": (
                {
                    str(object_type): member_header
                    for object_type, member_header in sorted(
                        verified_prefix_layout.items()
                    )
                }
                if verified_prefix_layout is not None
                else {
                    str(object_type): member_header
                    for object_type, member_header in sorted(
                        VERSIONED_PROJECTILE_SPAWN_PREFIXES[
                            header["clientVersion"]
                        ].items()
                    )
                }
            ),
            "playerIdentityAuthority": identity_authority,
            "eventAuthority": "official-er-exact-delta-schema",
        },
        "catalog": {
            "characterCount": catalog["characterCount"],
            "skillCount": catalog["skillCount"],
            "projectileDefinitionCount": catalog[
                "projectileDefinitionCount"
            ],
            "sixRequiredFields": [
                "projectileStatus",
                "projectileCodes",
                "projectileShape",
                "hitEvidencePacket",
                "useCountEvidence",
                "hitRateCalculable/hitRateReason",
            ],
        },
        "runtime": {
            "scope": runtime["scope"],
            "scopeEvidence": runtime["scopeEvidence"],
            "playerCount": len(player_ids),
            "teamCount": len(set(team_by_player.values())),
            "exactSkillStartCount": len(skill_starts),
            "exactSkillActionCount": len(raw_skill_actions),
            "resolvedSkillActionActorCount": action_actor_resolution[
                "resolvedCount"
            ],
            "unresolvedSkillActionActorCount": action_actor_resolution[
                "unresolvedCount"
            ],
            "ambiguousSkillActionActorCount": action_actor_resolution[
                "ambiguousCount"
            ],
            "skillActionActorAuthorityCounts": action_actor_resolution[
                "authorityCounts"
            ],
            "skillActionActorResolutionStatus": action_actor_resolution[
                "status"
            ],
            "exactTargetedSkillActionCount": targeted_skill_action_count,
            "exactSkillActionTargetCount": exact_skill_action_target_count,
            "resolvedTargetedSkillActionCount": (
                resolved_targeted_skill_action_count
            ),
            "resolvedSkillActionTargetCount": (
                resolved_skill_action_target_count
            ),
            "enemyPlayerSkillActionTargetCount": (
                enemy_player_skill_action_target_count
            ),
            "exactStateSkillActionCount": decoded_packet_counts.get(
                "CmdPlayStateSkillAction", 0
            ),
            "unresolvedSkillStartCount": unresolved_skill_start_count,
            "projectileSpawnCount": len(projectile_spawns_all),
            "directPlayerProjectileSpawnCount": len(
                direct_player_projectiles
            ),
            "unresolvedNonPlayerOwnerProjectileSpawnCount": (
                len(projectile_spawns_all) - len(direct_player_projectiles)
            ),
            "unobservedOwnerProjectileSpawnCount": (
                unobserved_owner_projectile_count
            ),
            "projectileSpawnCountByObjectType": {
                str(key): value for key, value in sorted(prefix_counts.items())
            },
            "projectileCollisionCount": len(collisions),
            "exactSkillDamageResolutionStatus": (
                exact_skill_damage_resolution["status"]
            ),
            "exactSkillDamageResolutionCounts": (
                exact_skill_damage_resolution["counts"]
            ),
            "combatIntervalCount": sum(
                len(rows) for rows in combat_intervals.values()
            ),
            "confirmedPvpIntervalCount": sum(
                len(rows) for rows in confirmed_pvp_intervals.values()
            ),
            "verifiedSkillLinkCount": verified_count,
            "calculableSkillCount": calculable_count,
            "decodedPacketCounts": dict(sorted(decoded_packet_counts.items())),
        },
        "players": output_players,
        "limitations": [
            "This one match can verify only skills actually used by its 24 players.",
            "Projectiles owned by summons or other non-player objects remain unresolved rather than being assigned by proximity.",
            "Single or constant-count multi-shot, non-penetrating, non-explosive, non-persistent, non-returning projectile links can produce a per-shot percentage.",
            "The 12.1 spawn prefix is never used as a 12.2 fallback.",
        ],
        "hybridSourceUsed": hybrid_source_used,
        "fallbackUsed": False,
    }


def validate_runtime_audit(report: dict) -> None:
    if (
        report.get("format") != "er-projectile-runtime-audit.v1"
        or report.get("status")
        != "verified-versioned-projectile-runtime-audit"
        or report.get("fallbackUsed") is not False
        or not isinstance(report.get("hybridSourceUsed"), bool)
    ):
        raise ValueError("projectile runtime audit header is invalid")
    source = report.get("source")
    client_version = report.get("clientVersion")
    source_layout = source.get("projectilePrefixLayout") if isinstance(source, dict) else None
    source_authority = (
        source.get("projectilePrefixLayoutAuthority")
        if isinstance(source, dict)
        else None
    )
    promoted_layout = VERSIONED_PROJECTILE_SPAWN_PREFIXES.get(client_version)
    identity_authority = (
        source.get("playerIdentityAuthority") if isinstance(source, dict) else None
    )
    if (
        identity_authority
        not in {
            "official-er-first-full-snapshot",
            "dak-transformed-same-game-users-v1",
        }
        or not isinstance(source, dict)
        or source.get("eventAuthority") != "official-er-exact-delta-schema"
        or report["hybridSourceUsed"]
        != (identity_authority == "dak-transformed-same-game-users-v1")
    ):
        raise ValueError("projectile runtime player identity authority is invalid")
    normalized_source_layout = (
        {
            int(object_type): member_header
            for object_type, member_header in source_layout.items()
        }
        if isinstance(source_layout, dict)
        and source_layout
        and all(
            str(object_type).isdigit()
            and isinstance(member_header, int)
            and not isinstance(member_header, bool)
            and 0 <= member_header <= 255
            for object_type, member_header in source_layout.items()
        )
        else None
    )
    version_layout_valid = (
        promoted_layout is not None
        and normalized_source_layout == promoted_layout
        and source_authority == f"promoted-version-table-{client_version}"
    ) or (
        normalized_source_layout is not None
        and source_authority == "multi-match-exact-prefix-consensus-v1"
    )
    if not isinstance(client_version, str) or not version_layout_valid:
        raise ValueError("projectile runtime audit version/layout authority is invalid")

    def all_keys(value):
        if isinstance(value, dict):
            for key, child in value.items():
                yield str(key).lower()
                yield from all_keys(child)
        elif isinstance(value, list):
            for child in value:
                yield from all_keys(child)

    forbidden_identity_keys = {
        "nickname",
        "userid",
        "usernum",
        "playerobjectid",
        "ownerobjectid",
        "projectileobjectid",
        "targetobjectid",
    }
    if (
        not isinstance(source, dict)
        or not isinstance(source.get("replaySha256"), str)
        or len(source["replaySha256"]) != 64
        or not isinstance(source.get("gameDataSha256"), str)
        or len(source["gameDataSha256"]) != 64
        or forbidden_identity_keys & set(all_keys(report))
    ):
        raise ValueError("projectile runtime audit source/privacy is invalid")
    runtime = report.get("runtime")
    players = report.get("players")
    if (
        not isinstance(runtime, dict)
        or runtime.get("scope") != "confirmed-player-engagement-only"
        or runtime.get("scopeEvidence") != SCOPE_EVIDENCE
        or not isinstance(players, list)
        or runtime.get("playerCount") != len(players)
        or len({row.get("publicPlayerId") for row in players}) != len(players)
    ):
        raise ValueError("projectile runtime audit coverage is invalid")
    required = {
        "projectileStatus",
        "projectileCodes",
        "projectileShape",
        "aimModel",
        "castTiming",
        "hitEvidencePacket",
        "useCountEvidence",
        "hitRateCalculable",
        "hitRateReason",
        "scope",
        "scopeEvidence",
        "fallbackUsed",
    }
    rows = [skill for player in players for skill in player.get("skills", [])]
    if any(
        not isinstance(row, dict)
        or not required <= set(row)
        or row.get("fallbackUsed") is not False
        for row in rows
    ):
        raise ValueError("projectile runtime skill row is invalid")
    if runtime.get("calculableSkillCount") != sum(
        row["hitRateCalculable"] for row in rows
    ):
        raise ValueError("projectile runtime calculable count is invalid")
    damage_resolution_fields = {
        "exactSkillDamageResolutionStatus",
        "exactSkillDamageResolutionCounts",
    }
    if damage_resolution_fields & set(runtime):
        if not damage_resolution_fields <= set(runtime):
            raise ValueError("exact skill damage resolution is incomplete")
        damage_counts = runtime["exactSkillDamageResolutionCounts"]
        if (
            runtime["exactSkillDamageResolutionStatus"]
            != "effect-code-namespace-origin-unresolved"
            or not isinstance(damage_counts, dict)
            or not all(
                isinstance(key, str)
                and key
                and isinstance(value, int)
                and not isinstance(value, bool)
                and value >= 0
                for key, value in damage_counts.items()
            )
        ):
            raise ValueError("exact skill damage resolution is invalid")
    if any(
        row["hitRateCalculable"]
        and row.get("aimModel", {}).get("hitRateEligible") is not True
        for row in rows
    ):
        raise ValueError("non-aimed skill cannot expose a hit rate")
    resolution_fields = {
        "resolvedSkillActionActorCount",
        "unresolvedSkillActionActorCount",
        "ambiguousSkillActionActorCount",
        "skillActionActorAuthorityCounts",
        "skillActionActorResolutionStatus",
        "resolvedTargetedSkillActionCount",
        "resolvedSkillActionTargetCount",
    }
    if resolution_fields & set(runtime):
        if not resolution_fields <= set(runtime):
            raise ValueError("skill action actor resolution is incomplete")
        resolved_count = runtime["resolvedSkillActionActorCount"]
        unresolved_count = runtime["unresolvedSkillActionActorCount"]
        ambiguous_count = runtime["ambiguousSkillActionActorCount"]
        authority_counts = runtime["skillActionActorAuthorityCounts"]
        if (
            not all(
                isinstance(value, int) and not isinstance(value, bool) and value >= 0
                for value in (resolved_count, unresolved_count, ambiguous_count)
            )
            or resolved_count + unresolved_count
            != runtime.get("exactSkillActionCount")
            or ambiguous_count > unresolved_count
            or not isinstance(authority_counts, dict)
            or sum(authority_counts.values()) != resolved_count
            or runtime.get("skillActionActorResolutionStatus")
            != "exact-same-skill-player-action-actor-resolution"
        ):
            raise ValueError("skill action actor resolution is invalid")
        for row in rows:
            evidence = row.get("actionTargetEvidence")
            if (
                not isinstance(evidence, dict)
                or evidence.get("fallbackUsed") is not False
                or not isinstance(evidence.get("interpretation"), str)
                or evidence.get("status")
                not in {
                    "decoded-exact-action-target-diagnostic-not-hit-rate",
                    "decoded-exact-linked-actions-no-targets",
                    "unavailable-no-linked-skill-action",
                }
            ):
                raise ValueError("skill action target diagnostic is invalid")
            effect_evidence = row.get("exactEffectDamageEvidence")
            if (
                effect_evidence is not None
                and (
                    not isinstance(effect_evidence, dict)
                    or effect_evidence.get("fallbackUsed") is not False
                    or effect_evidence.get("status")
                    not in {
                        "decoded-exact-skill-damage-diagnostic",
                        "unavailable-no-exact-skill-damage-input",
                    }
                )
            ):
                raise ValueError("exact effect damage diagnostic is invalid")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Build a versioned anonymous projectile runtime audit"
    )
    parser.add_argument("replay", type=Path)
    parser.add_argument("game_data", type=Path)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    report = build_runtime_audit(
        args.replay.resolve(), args.game_data.resolve()
    )
    validate_runtime_audit(report)
    output = args.out.resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps({
        "output": str(output),
        "clientVersion": report["clientVersion"],
        "players": report["runtime"]["playerCount"],
        "verifiedSkillLinks": report["runtime"]["verifiedSkillLinkCount"],
        "calculableSkills": report["runtime"]["calculableSkillCount"],
        "fallbackUsed": report["fallbackUsed"],
    }))


if __name__ == "__main__":
    main()
