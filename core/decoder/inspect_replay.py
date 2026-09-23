"""Create a conservative JSON inspection report for an Eternal Return .er file.

The parser only emits fields whose 12.2.0 layout is structurally validated in the
input snapshot. It intentionally does not guess nicknames, skill events, items,
or delta-packet fields.
"""

from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import json
import math
from pathlib import Path
import struct

import brotli

from delta_payloads import SchemaDecoder
from inspect_deltas import load_definitions


HEADER_OFFSET = 0x410
RECORD_HEADER = struct.Struct("<HHIII")
PLAYER_WRAPPER_SIGNATURE = bytes((7, 2, 0, 0, 0))
RECORD_KIND = {
    0: "eof",
    1: "delta",
    2: "snapshot",
    3: "definitions",
    4: "trailer",
    7: "keyframe_mark",
}


def i32(data: bytes, offset: int) -> int:
    return struct.unpack_from("<i", data, offset)[0]


def u32(data: bytes, offset: int) -> int:
    return struct.unpack_from("<I", data, offset)[0]


def i64(data: bytes, offset: int) -> int:
    return struct.unpack_from("<q", data, offset)[0]


def f32(data: bytes, offset: int) -> float:
    return struct.unpack_from("<f", data, offset)[0]


def parse_records(data: bytes) -> list[dict]:
    position = HEADER_OFFSET
    records: list[dict] = []
    while position + RECORD_HEADER.size <= len(data):
        kind, version, tick, length, aux = RECORD_HEADER.unpack_from(data, position)
        payload_offset = position + RECORD_HEADER.size
        payload_end = payload_offset + length
        if payload_end > len(data):
            raise ValueError(f"record at {position} extends past EOF")
        records.append(
            {
                "kind": kind,
                "version": version,
                "tick": tick,
                "length": length,
                "aux": aux,
                "payload": data[payload_offset:payload_end],
            }
        )
        position = payload_end
    if position != len(data):
        raise ValueError(f"unframed trailing bytes: {len(data) - position}")
    return records


def parse_top_level(raw: bytes) -> dict:
    member_count = raw[0]
    target_frame_rate = i32(raw, 1)
    game_id = i64(raw, 5)
    user_id_count = i32(raw, 13)
    if not 0 <= user_id_count <= 128:
        raise ValueError(f"implausible top-level userId count: {user_id_count}")
    user_ids_offset = 17
    user_ids = [i64(raw, user_ids_offset + index * 8) for index in range(user_id_count)]
    seq_offset = user_ids_offset + user_id_count * 8
    seq = i32(raw, seq_offset)
    game_snapshot_offset = seq_offset + 4
    game_snapshot_members = raw[game_snapshot_offset]
    game_snapshot_user_count = i32(raw, game_snapshot_offset + 1)
    return {
        "memberCount": member_count,
        "targetFrameRate": target_frame_rate,
        "gameId": game_id,
        "userIds": user_ids,
        "seq": seq,
        "gameSnapshotMemberCount": game_snapshot_members,
        "gameSnapshotUserCount": game_snapshot_user_count,
    }


def validate_snapshot_identity_rows(rows: list[tuple[int, dict]]) -> str:
    if not rows:
        raise ValueError("no snapshot identity rows")
    game_ids = {row["gameId"] for _, row in rows}
    if len(game_ids) != 1 or next(iter(game_ids)) <= 0:
        raise ValueError("snapshot gameId is missing or inconsistent")
    mismatches = [
        (record_tick, row["seq"])
        for record_tick, row in rows
        if row["seq"] != record_tick
    ]
    if not mismatches:
        return "all-record-ticks-match-top-level-seq"
    # Cobalt 12.2 can begin with a tick-0 warmup full snapshot whose internal
    # sequence is already advanced. Every later full snapshot must still match
    # its record tick exactly; no arbitrary offset or sequence fallback is used.
    if (
        len(mismatches) == 1
        and mismatches[0][0] == 0
        and all(
            row["seq"] == record_tick
            for record_tick, row in rows
            if record_tick != 0
        )
    ):
        return "initial-tick-zero-warmup-seq-then-exact"
    raise ValueError(f"snapshot identity mismatch: {mismatches[:3]}")


def parse_status(status: bytes) -> dict:
    if len(status) < 45:
        raise ValueError("player status snapshot is too short")
    member_count = status[0]
    if member_count < 11:
        raise ValueError(f"unexpected player status member count: {member_count}")

    result = {
        "memberCount": member_count,
        "hp": i32(status, 1),
        "sp": i32(status, 5),
        "extraPoint": i32(status, 9),
        "level": i32(status, 13),
        "blockAllShield": i32(status, 17),
        "blockNormalShield": i32(status, 21),
        "blockSkillShield": i32(status, 25),
        "moveSpeed": round(f32(status, 29), 4),
        "exp": i32(status, 33),
        "bullet": i32(status, 37),
        "maxBullet": i32(status, 41),
    }

    if member_count < 20:
        return result

    offset = 45
    extra_count = i32(status, offset)
    offset += 4
    if not 0 <= extra_count <= 64:
        raise ValueError(f"implausible extra-resource count: {extra_count}")
    result["extraResourceList"] = [i32(status, offset + index * 4) for index in range(extra_count)]
    offset += extra_count * 4

    names = (
        "swapExtraResource",
        "monsterKill",
        "playerKill",
        "playerKillAssist",
    )
    for name in names:
        result[name] = i32(status, offset)
        offset += 4

    vf_member_count = status[offset]
    offset += 1
    if vf_member_count == 0xFF:
        result["vfCredit"] = None
    elif vf_member_count >= 1:
        result["vfCredit"] = i32(status, offset) / 100
        offset += 4
    else:
        result["vfCredit"] = 0

    for name in ("playerDeaths", "killStreak", "gadgetEnergy"):
        result[name] = i32(status, offset)
        offset += 4
    return result


def compact_initial_stats(value: object) -> list[list[int]]:
    if not isinstance(value, list) or any(
        not isinstance(row, dict)
        or not isinstance(row.get("statType"), int)
        or not isinstance(row.get("value"), int)
        for row in value
    ):
        raise ValueError("player initialStat is not an exact CharacterStatValue list")
    return [[row["statType"], row["value"]] for row in value]


def exact_fixed_point_hundredths(value: object, field_name: str) -> float:
    if not isinstance(value, dict):
        raise ValueError(f"{field_name} is not an exact BlisFixedPoint object")
    internal_value = value.get("internalValue")
    if isinstance(internal_value, bool) or not isinstance(internal_value, int):
        raise ValueError(f"{field_name}.internalValue is not an exact integer")
    return internal_value / 100


def exact_player_status(value: object) -> dict:
    if not isinstance(value, dict):
        raise ValueError("player status snapshot is not an exact object")
    required_ints = (
        "hp",
        "vp",
        "extraPoint",
        "level",
        "blockAllShield",
        "blockNormalShield",
        "blockSkillShield",
        "exp",
        "bullet",
        "maxBullet",
        "swapExtraResource",
        "monsterKill",
        "playerKill",
        "playerKillAssist",
        "playerDeaths",
        "killStreak",
        "gadgetEnergy",
    )
    if any(
        isinstance(value.get(name), bool) or not isinstance(value.get(name), int)
        for name in required_ints
    ):
        raise ValueError("player status snapshot has a missing or non-integer field")
    extra_resources = value.get("extraResourceList")
    if not isinstance(extra_resources, list) or any(
        isinstance(item, bool) or not isinstance(item, int)
        for item in extra_resources
    ):
        raise ValueError("player status extraResourceList is not exact")
    move_speed = value.get("moveSpeed")
    if not isinstance(move_speed, (int, float)) or not math.isfinite(move_speed):
        raise ValueError("player status moveSpeed is not finite")
    return {
        "memberCount": 20,
        "hp": value["hp"],
        "sp": value["vp"],
        "extraPoint": value["extraPoint"],
        "level": value["level"],
        "blockAllShield": value["blockAllShield"],
        "blockNormalShield": value["blockNormalShield"],
        "blockSkillShield": value["blockSkillShield"],
        "moveSpeed": round(float(move_speed), 4),
        "exp": value["exp"],
        "bullet": value["bullet"],
        "maxBullet": value["maxBullet"],
        "extraResourceList": extra_resources,
        "swapExtraResource": value["swapExtraResource"],
        "monsterKill": value["monsterKill"],
        "playerKill": value["playerKill"],
        "playerKillAssist": value["playerKillAssist"],
        "vfCredit": exact_fixed_point_hundredths(value.get("vfCredit"), "vfCredit"),
        "playerDeaths": value["playerDeaths"],
        "killStreak": value["killStreak"],
        "gadgetEnergy": value["gadgetEnergy"],
    }


def extract_players_exact(
    replay_snapshot: object,
    decoder: SchemaDecoder,
    character_names: dict[str, str],
) -> list[dict]:
    """Extract players through the exact 12.3 schema path, never byte scanning."""

    game_snapshot = (
        replay_snapshot.get("gameSnapshot")
        if isinstance(replay_snapshot, dict)
        else None
    )
    users = game_snapshot.get("userList") if isinstance(game_snapshot, dict) else None
    if not isinstance(users, list) or not users:
        raise ValueError("exact gameSnapshot.userList is missing")

    rows = []
    object_ids: set[int] = set()
    for user in users:
        if not isinstance(user, dict):
            raise ValueError("exact UserSnapshot row is not an object")
        user_id = user.get("userId")
        wrapper = user.get("characterSnapshot")
        if isinstance(user_id, bool) or not isinstance(user_id, int):
            raise ValueError("exact UserSnapshot userId is invalid")
        if not isinstance(wrapper, dict):
            raise ValueError("exact UserSnapshot characterSnapshot is missing")

        object_id = wrapper.get("objectId")
        nested = wrapper.get("snapshot")
        position = wrapper.get("positionXZ")
        if (
            isinstance(object_id, bool)
            or not isinstance(object_id, int)
            or object_id <= 0
            or object_id in object_ids
        ):
            raise ValueError("exact player objectId is invalid or duplicated")
        if not isinstance(nested, bytes) or not nested:
            raise ValueError("exact player snapshot payload is missing")
        if (
            not isinstance(position, list)
            or len(position) != 2
            or any(
                not isinstance(item, (int, float)) or not math.isfinite(item)
                for item in position
            )
        ):
            raise ValueError("exact player positionXZ is missing or invalid")

        character = decoder.decode_exact(nested, "PlayerCharacterSnapshot")
        status_bytes = character.get("statusSnapshot")
        if not isinstance(status_bytes, bytes) or not status_bytes:
            raise ValueError("exact player status payload is missing")
        status = exact_player_status(
            decoder.decode_exact(status_bytes, "PlayerStatusSnapshot")
        )

        required_ints = ("characterCode", "skinIndex", "teamNumber")
        if any(
            isinstance(character.get(name), bool)
            or not isinstance(character.get(name), int)
            for name in required_ints
        ):
            raise ValueError("exact player identity has a missing integer field")
        if character["characterCode"] <= 0 or character["teamNumber"] <= 0:
            raise ValueError("exact player character/team identity is implausible")
        if not isinstance(character.get("isAlive"), bool) or not isinstance(
            character.get("isDyingCondition"), bool
        ):
            raise ValueError("exact player life state is missing")

        world_x, world_z = (float(position[0]), float(position[1]))
        character_code = character["characterCode"]
        rows.append(
            {
                "objectId": object_id,
                "userId": user_id,
                "characterCode": character_code,
                "characterName": character_names.get(str(character_code)),
                "skinIndex": character["skinIndex"],
                "teamNumber": character["teamNumber"],
                "isAlive": character["isAlive"],
                "isDyingCondition": character["isDyingCondition"],
                "inWorldType": wrapper.get("inWorldType"),
                "position": {
                    "worldX": round(world_x, 3),
                    "worldZ": round(world_z, 3),
                    "worldYRaw": wrapper.get("positionY"),
                    "rotationRaw": wrapper.get("blisLiteRotation"),
                    "approxGrid": world_to_grid(world_x, world_z),
                },
                "status": status,
                "initialStats": compact_initial_stats(character.get("initialStat")),
            }
        )
        object_ids.add(object_id)
    return rows


def parse_identity_prefix(inner: bytes) -> dict:
    # Only the legacy scan uses this decoder and its legacy schema.
    import er_mempack as mp
    reader = mp.R(inner)
    member_count = reader.u8()
    values = {}
    for order, field_type, name in mp.members_of("PlayerCharacterSnapshot")[:member_count]:
        values[name] = mp.read(reader, field_type, 1)
        if name == "isDyingCondition":
            break
    required = (
        "initialStat",
        "isAlive",
        "characterCode",
        "skinIndex",
        "teamNumber",
        "isDyingCondition",
    )
    if any(name not in values for name in required):
        raise ValueError("player identity prefix ended before required fields")
    return {
        **{name: values[name] for name in required if name != "initialStat"},
        "initialStats": compact_initial_stats(values["initialStat"]),
    }


def world_to_grid(world_x: float, world_z: float) -> list[float]:
    return [
        round(-1.645 * (world_x + world_z) + 290.1, 2),
        round(-1.653 * world_x + 1.652 * world_z + 555.0, 2),
    ]


def extract_players(raw: bytes, character_names: dict[str, str]) -> list[dict]:
    rows = []
    search_offset = 0
    while True:
        wrapper_offset = raw.find(PLAYER_WRAPPER_SIGNATURE, search_offset)
        if wrapper_offset < 0:
            break
        search_offset = wrapper_offset + 1
        try:
            object_id = i32(raw, wrapper_offset + 5)
            in_world_type = i32(raw, wrapper_offset + 9)
            nested_length = i32(raw, wrapper_offset + 13)
            if not 1 <= nested_length <= 16 * 1024 * 1024:
                continue
            nested_offset = wrapper_offset + 17
            position_offset = nested_offset + nested_length
            if position_offset + 16 > len(raw):
                continue
            inner = raw[nested_offset:position_offset]
            if not inner or inner[0] < 14:
                continue

            status_length = i32(inner, 1)
            if not 1 <= status_length <= len(inner) - 5:
                continue
            status = parse_status(inner[5:5 + status_length])
            identity = parse_identity_prefix(inner)
            world_x = f32(raw, position_offset)
            world_z = f32(raw, position_offset + 4)
            if not math.isfinite(world_x) or not math.isfinite(world_z):
                continue

            user_id = None
            # GameSnapshot.userList: object header, userId, union tag, then wrapper.
            if wrapper_offset >= 10 and raw[wrapper_offset - 10] == 14:
                user_id = i64(raw, wrapper_offset - 9)

            character_code = identity["characterCode"]
            rows.append(
                {
                    "objectId": object_id,
                    "userId": user_id,
                    "characterCode": character_code,
                    "characterName": character_names.get(str(character_code)),
                    "skinIndex": identity["skinIndex"],
                    "teamNumber": identity["teamNumber"],
                    "isAlive": bool(identity["isAlive"]),
                    "isDyingCondition": bool(identity["isDyingCondition"]),
                    "inWorldType": in_world_type,
                    "position": {
                        "worldX": round(world_x, 3),
                        "worldZ": round(world_z, 3),
                        "worldYRaw": i32(raw, position_offset + 8),
                        "rotationRaw": u32(raw, position_offset + 12),
                        "approxGrid": world_to_grid(world_x, world_z),
                    },
                    "status": status,
                    "initialStats": identity["initialStats"],
                }
            )
        except (IndexError, KeyError, struct.error, UnicodeDecodeError, ValueError):
            continue
    return rows


def player_summary(timeline: list[dict]) -> list[dict]:
    by_object: dict[int, list[tuple[int, dict]]] = {}
    for snapshot in timeline:
        for player in snapshot["players"]:
            by_object.setdefault(player["objectId"], []).append((snapshot["tick"], player))

    summaries = []
    for object_id, samples in sorted(by_object.items()):
        first_tick, first = samples[0]
        last_tick, last = samples[-1]
        statuses = [sample[1]["status"] for sample in samples]
        summaries.append(
            {
                "objectId": object_id,
                "userId": first["userId"],
                "characterCode": first["characterCode"],
                "characterName": first["characterName"],
                "skinIndex": first["skinIndex"],
                "teamNumber": first["teamNumber"],
                "snapshotSamples": len(samples),
                "firstTick": first_tick,
                "lastTick": last_tick,
                "maxLevel": max(status["level"] for status in statuses),
                "minHp": min(status["hp"] for status in statuses),
                "maxHpObserved": max(status["hp"] for status in statuses),
                "lastStatus": last["status"],
                "lastPosition": last["position"],
                "aliveInLastSnapshot": last["isAlive"],
            }
        )
    return summaries


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("replay", type=Path)
    parser.add_argument("--out", type=Path)
    args = parser.parse_args()

    replay_path = args.replay.resolve()
    output_path = (args.out or replay_path.with_suffix(".inspect.json")).resolve()
    data = replay_path.read_bytes()
    if data[:16] != b"EternalReturnV1\0":
        raise ValueError("not an EternalReturnV1 replay")

    version = data[16:32].split(b"\0", 1)[0].decode("ascii")
    game_data_url = data[32:HEADER_OFFSET].split(b"\0", 1)[0].decode("ascii")
    records = parse_records(data)
    definitions, _packet_names = load_definitions(records)
    base_types = {
        definition["name"]: definition.get("baseType")
        for definition in definitions
        if isinstance(definition.get("name"), str)
    }
    exact_decoder = SchemaDecoder(base_types, client_version=version)
    snapshots = [record for record in records if record["kind"] == 2 and record["version"] == 1]
    if not snapshots:
        raise ValueError("no full-state snapshots found")

    root = Path(__file__).resolve().parent.parent
    names = json.loads((root / "data" / "names.json").read_text(encoding="utf-8"))
    character_names = names.get("characters", {})
    timeline = []
    top_level = None
    identity_rows = []
    for record in snapshots:
        raw = brotli.decompress(record["payload"])
        current_top = parse_top_level(raw)
        identity_rows.append((record["tick"], current_top))
        if top_level is None:
            top_level = current_top
        if version in ("12.3.0", "12.4.0"):
            exact_snapshot = exact_decoder.decode_exact(raw, "ReplaySnapshot")
            players = extract_players_exact(
                exact_snapshot,
                exact_decoder,
                character_names,
            )
            if len(players) != current_top["gameSnapshotUserCount"]:
                raise ValueError(
                    "exact player count does not match GameSnapshot.userList: "
                    f"{len(players)} != {current_top['gameSnapshotUserCount']}"
                )
        else:
            players = extract_players(raw, character_names)
        timeline.append(
            {
                "tick": record["tick"],
                "secondsAt60Hz": round(record["tick"] / 60, 3),
                "decompressedBytes": len(raw),
                "players": players,
            }
        )

    snapshot_identity_status = validate_snapshot_identity_rows(identity_rows)

    record_counts = Counter(
        f"{RECORD_KIND.get(record['kind'], 'kind_' + str(record['kind']))}:v{record['version']}"
        for record in records
    )
    delta_ticks = [record["tick"] for record in records if record["kind"] == 1]
    report = {
        "sourceFile": str(replay_path),
        "sha256": hashlib.sha256(data).hexdigest(),
        "fileBytes": len(data),
        "format": {
            "magic": "EternalReturnV1",
            "clientVersion": version,
            "snapshotIdentityStatus": snapshot_identity_status,
            "playerExtractionStatus": (
                "exact-schema-gameSnapshot-userList"
                if version in ("12.3.0", "12.4.0")
                else "legacy-validated-player-wrapper-scan"
            ),
            "gameDataUrl": game_data_url,
            "recordHeader": "little-endian <uint16 kind, uint16 version, uint32 tick, uint32 length, uint32 aux>",
            "payloadEncoding": "Brotli-compressed MemoryPack",
        },
        "recordCounts": dict(sorted(record_counts.items())),
        "tickRange": {
            "firstSnapshot": snapshots[0]["tick"],
            "lastSnapshot": snapshots[-1]["tick"],
            "firstDelta": min(delta_ticks) if delta_ticks else None,
            "lastDelta": max(delta_ticks) if delta_ticks else None,
            "lastTickSecondsAt60Hz": round(max(record["tick"] for record in records) / 60, 3),
        },
        "topLevelFromFirstSnapshot": top_level,
        "playerSummary": player_summary(timeline),
        "snapshotTimeline": timeline,
        "decodedFields": [
            "gameId, targetFrameRate, seq, top-level userIds",
            "objectId, userId, characterCode/name, skinIndex, teamNumber",
            "world position, approximate minimap grid, raw Y and rotation",
            "HP, SP, extraPoint, level, shields, moveSpeed, EXP, bullets",
            "exact CharacterStatValue initial-stat pairs for each player snapshot",
            "extra resources, monster/player kills, assists, VF credit, deaths, kill streak, gadget energy",
        ],
        "limitations": [
            "Character names come from the repository's local names.json; a null name means the table is older than the replay.",
            "Nickname strings, inventory/equipment, buffs, skills, global area state, and clean delta-packet fields are not decoded by this conservative report.",
            "approxGrid uses the repository's previously validated affine transform; world coordinates are the directly decoded values.",
            "secondsAt60Hz is tick/60 and is not asserted to be the official displayed match duration.",
        ],
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(
        json.dumps(
            {
                "output": str(output_path),
                "gameId": top_level["gameId"],
                "snapshots": len(timeline),
                "players": len(report["playerSummary"]),
                "records": len(records),
            },
            ensure_ascii=True,
        )
    )


if __name__ == "__main__":
    main()
