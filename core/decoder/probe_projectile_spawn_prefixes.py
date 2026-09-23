"""Build a version-neutral candidate audit for projectile spawn prefixes.

The replay's own embedded definitions decode wrapper boundaries.  The probe
then checks, without publishing object identifiers, whether the first two
int32 values of each nested spawn payload consistently match an exact
ProjectileSetting code and an object that exists in this replay.  Results are
candidates only: no client version is promoted automatically and no hit rate
is calculated here.
"""

from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import hashlib
import json
from pathlib import Path
import struct
from urllib.parse import urlparse
import zipfile

import brotli

try:
    from .character_capabilities import validate_game_data_archive
    from .delta_payloads import SchemaDecoder
    from .inspect_deltas import iter_records, load_definitions, parse_delta_payload
except ImportError:
    from character_capabilities import validate_game_data_archive
    from delta_payloads import SchemaDecoder
    from inspect_deltas import iter_records, load_definitions, parse_delta_payload


HEADER_OFFSET = 0x410
WRAPPER_CATEGORIES = (
    "ignoreOrderPackets",
    "commands",
    "itemBoxPackets",
    "clientPackets",
)
SPAWN_PACKETS = {"CmdSpawn", "CmdSpawns"}
MIN_CANDIDATE_OBSERVATIONS = 3


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def read_candidate_replay_header(data: bytes) -> dict:
    if len(data) < HEADER_OFFSET or data[:16] != b"EternalReturnV1\0":
        raise ValueError("not an EternalReturnV1 replay")
    try:
        client_version = data[16:32].split(b"\0", 1)[0].decode("ascii")
        game_data_url = data[32:HEADER_OFFSET].split(b"\0", 1)[0].decode("ascii")
    except UnicodeDecodeError as error:
        raise ValueError("replay fixed header is not ASCII") from error
    if not client_version:
        raise ValueError("replay header has no client version")
    if not game_data_url:
        raise ValueError("replay header has no gameDb URL")
    return {"clientVersion": client_version, "gameDataUrl": game_data_url}


def exact_known_object_ids(first_snapshot: dict) -> set[int]:
    game_snapshot = first_snapshot.get("gameSnapshot")
    if not isinstance(game_snapshot, dict):
        raise ValueError("first snapshot has no exact gameSnapshot")
    users = game_snapshot.get("userList")
    world = game_snapshot.get("worldSnapshot")
    if not isinstance(users, list) or not isinstance(world, list):
        raise ValueError("first snapshot user/world collections are unavailable")
    known: set[int] = set()
    for user in users:
        character = user.get("characterSnapshot") if isinstance(user, dict) else None
        object_id = character.get("objectId") if isinstance(character, dict) else None
        if isinstance(object_id, int) and object_id > 0:
            known.add(object_id)
    for wrapper in world:
        object_id = wrapper.get("objectId") if isinstance(wrapper, dict) else None
        if isinstance(object_id, int) and object_id > 0:
            known.add(object_id)
    if not known:
        raise ValueError("first snapshot has no exact known objects")
    return known


def exact_player_object_ids(first_snapshot: dict) -> set[int]:
    game_snapshot = first_snapshot.get("gameSnapshot")
    users = game_snapshot.get("userList") if isinstance(game_snapshot, dict) else None
    if not isinstance(users, list) or not users:
        raise ValueError("first snapshot has no exact game user list")
    player_ids: set[int] = set()
    for user in users:
        character = user.get("characterSnapshot") if isinstance(user, dict) else None
        object_id = character.get("objectId") if isinstance(character, dict) else None
        if not isinstance(object_id, int) or object_id <= 0 or object_id in player_ids:
            raise ValueError("first snapshot player object identities are invalid")
        player_ids.add(object_id)
    return player_ids


def summarize_prefix_candidates(
    spawn_rows: list[dict],
    collision_object_ids: set[int],
    projectile_codes: set[int],
    known_object_ids: set[int],
) -> dict:
    by_type: dict[int, list[dict]] = defaultdict(list)
    object_type_by_spawn_id: dict[int, int] = {}
    malformed_count = 0
    for row in spawn_rows:
        object_type = row.get("objectType")
        object_id = row.get("objectId")
        payload = row.get("snapshot")
        if (
            not isinstance(object_type, int)
            or not isinstance(object_id, int)
            or not isinstance(payload, bytes)
            or len(payload) < 9
        ):
            malformed_count += 1
            continue
        member_header = payload[0]
        projectile_code, owner_object_id = struct.unpack_from("<ii", payload, 1)
        by_type[object_type].append(
            {
                "memberHeader": member_header,
                "projectileCodeValid": projectile_code in projectile_codes,
                "ownerPositive": owner_object_id > 0,
                "ownerKnown": owner_object_id in known_object_ids,
            }
        )
        object_type_by_spawn_id[object_id] = object_type

    collision_counts = Counter(
        object_type_by_spawn_id[object_id]
        for object_id in collision_object_ids
        if object_id in object_type_by_spawn_id
    )
    candidates = []
    rejected = []
    rejected_type_count = 0
    for object_type, rows in sorted(by_type.items()):
        headers = Counter(row["memberHeader"] for row in rows)
        exact_code_count = sum(row["projectileCodeValid"] for row in rows)
        positive_owner_count = sum(row["ownerPositive"] for row in rows)
        known_owner_count = sum(row["ownerKnown"] for row in rows)
        stable_header = len(headers) == 1
        all_codes = exact_code_count == len(rows)
        all_owners_positive = positive_owner_count == len(rows)
        has_known_owner = known_owner_count > 0
        enough = len(rows) >= MIN_CANDIDATE_OBSERVATIONS
        collision_count = collision_counts.get(object_type, 0)
        has_collision_link = collision_count > 0
        if not (
            stable_header
            and all_codes
            and all_owners_positive
            and has_known_owner
            and has_collision_link
            and enough
        ):
            rejected_type_count += 1
            rejected.append({
                "objectType": object_type,
                "occurrenceCount": len(rows),
                "observedMemberHeaders": dict(sorted(headers.items())),
                "exactProjectileCodeCount": exact_code_count,
                "positiveOwnerCount": positive_owner_count,
                "knownOwnerCount": known_owner_count,
                "collisionLinkedObjectCount": collision_count,
                "stableMemberHeader": stable_header,
                "minimumObservationMet": enough,
                "status": "rejected-not-an-exact-projectile-prefix-candidate",
                "fallbackUsed": False,
            })
            continue
        candidates.append(
            {
                "objectType": object_type,
                "occurrenceCount": len(rows),
                "memberHeader": next(iter(headers)),
                "exactProjectileCodeCount": exact_code_count,
                "positiveOwnerCount": positive_owner_count,
                "knownOwnerCount": known_owner_count,
                "collisionLinkedObjectCount": collision_count,
                "status": "candidate-projectile-prefix-with-collision-links",
                "promotionStatus": "candidate-only-needs-multi-match-version-validation",
                "fallbackUsed": False,
            }
        )
    return {
        "candidateObjectTypes": candidates,
        "candidateObjectTypeCount": len(candidates),
        "rejectedObjectTypes": rejected,
        "rejectedObjectTypeCount": rejected_type_count,
        "malformedNestedSpawnCount": malformed_count,
        "collisionSpawnLinkCount": sum(collision_counts.values()),
    }


def _validated_player_object_ids(values: set[int] | None) -> set[int] | None:
    if values is None:
        return None
    if (
        not isinstance(values, set)
        or not 1 <= len(values) <= 36
        or any(
            isinstance(value, bool) or not isinstance(value, int) or value <= 0
            for value in values
        )
    ):
        raise ValueError("external player object identities must be 1..36 positive integers")
    return set(values)


def build_candidate_prefix_probe(
    replay_path: Path,
    game_data_path: Path,
    *,
    known_player_object_ids: set[int] | None = None,
    expected_game_id: int | None = None,
    player_identity_authority: str | None = None,
) -> dict:
    replay_data = replay_path.read_bytes()
    header = read_candidate_replay_header(replay_data)
    game_data_source = validate_game_data_archive(
        game_data_path, header["gameDataUrl"]
    )
    game_data_bytes = game_data_path.read_bytes()
    with zipfile.ZipFile(game_data_path) as archive:
        try:
            projectile_rows = json.loads(archive.read("ProjectileSetting.json"))
        except KeyError as error:
            raise ValueError("official gameDb has no ProjectileSetting.json") from error
    projectile_codes = {
        row.get("code")
        for row in projectile_rows
        if isinstance(row, dict) and isinstance(row.get("code"), int)
    }
    if not projectile_codes:
        raise ValueError("official gameDb has no projectile definitions")

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
    snapshots = [
        record
        for record in records
        if record["kind"] == 2 and record["version"] == 1
    ]
    if not snapshots:
        raise ValueError("replay has no full snapshot")
    external_player_ids = _validated_player_object_ids(known_player_object_ids)
    if external_player_ids is None:
        if expected_game_id is not None or player_identity_authority is not None:
            raise ValueError("partial external player identity input is not allowed")
        first_snapshot = decoder.decode_exact(
            brotli.decompress(snapshots[0]["payload"]), "ReplaySnapshot"
        )
        game_id = first_snapshot.get("gameId")
        if not isinstance(game_id, int) or game_id <= 0:
            raise ValueError("replay snapshot gameId is invalid")
        player_object_ids = exact_player_object_ids(first_snapshot)
        known_object_ids = exact_known_object_ids(first_snapshot)
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
        first_snapshot_bytes = brotli.decompress(snapshots[0]["payload"])
        if len(first_snapshot_bytes) < 13:
            raise ValueError("first full snapshot is too small")
        embedded_game_id = struct.unpack_from("<q", first_snapshot_bytes, 5)[0]
        if embedded_game_id != expected_game_id:
            raise ValueError("external player map gameId does not match replay")
        game_id = expected_game_id
        player_object_ids = set(external_player_ids)
        known_object_ids = set(external_player_ids)
        identity_authority = player_identity_authority
        hybrid_source_used = True

    spawn_rows: list[dict] = []
    collision_object_ids: set[int] = set()
    decoded_counts = Counter()
    for record in records:
        if record["kind"] != 1 or record["version"] != 1:
            continue
        delta = parse_delta_payload(record["payload"], record["tick"])
        for category in WRAPPER_CATEGORIES:
            for wrapper in delta[category]:
                packet_name = packet_names.get(wrapper["packetType"])
                if packet_name not in SPAWN_PACKETS | {"CmdProjectileCollision"}:
                    continue
                payload = wrapper.get("payload")
                if payload is None:
                    raise ValueError(f"{packet_name} has a null payload")
                decoded = decoder.decode_exact(payload, packet_name)
                decoded_counts[packet_name] += 1
                if packet_name == "CmdProjectileCollision":
                    object_id = decoded.get("objectId")
                    if not isinstance(object_id, int) or object_id <= 0:
                        raise ValueError("projectile collision identity is invalid")
                    collision_object_ids.add(object_id)
                    continue
                wrappers = (
                    [decoded.get("snapshot")]
                    if packet_name == "CmdSpawn"
                    else (decoded.get("snapshots") or [])
                )
                for snapshot_wrapper in wrappers:
                    if not isinstance(snapshot_wrapper, dict):
                        continue
                    object_id = snapshot_wrapper.get("objectId")
                    object_type = snapshot_wrapper.get("objectType")
                    if not isinstance(object_id, int) or not isinstance(object_type, int):
                        raise ValueError("spawn wrapper identity is invalid")
                    known_object_ids.add(object_id)
                    spawn_rows.append(
                        {
                            "objectId": object_id,
                            "objectType": object_type,
                            "snapshot": snapshot_wrapper.get("snapshot"),
                        }
                    )

    summary = summarize_prefix_candidates(
        spawn_rows,
        collision_object_ids,
        projectile_codes,
        known_object_ids,
    )
    return {
        "format": "er-projectile-spawn-prefix-candidate-probe.v1",
        "status": "exact-schema-decoded-candidates-not-promoted",
        "clientVersion": header["clientVersion"],
        "source": {
            "gameId": game_id,
            "replaySha256": sha256_bytes(replay_data),
            "replayBytes": len(replay_data),
            "gameDataFilename": Path(urlparse(header["gameDataUrl"]).path).name,
            "gameDataSha256": sha256_bytes(game_data_bytes),
            "gameDataStatus": game_data_source["status"],
            "playerIdentityAuthority": identity_authority,
            "eventAuthority": "official-er-exact-delta-schema",
            "privacy": "No nickname, user id, userNum, or replay object id is retained.",
        },
        "runtime": {
            "playerObjectIdentityCount": len(player_object_ids),
            "knownObjectCount": len(known_object_ids),
            "spawnWrapperCount": len(spawn_rows),
            "projectileCollisionObjectCount": len(collision_object_ids),
            "decodedPacketCounts": dict(sorted(decoded_counts.items())),
            **summary,
        },
        "limitations": [
            "Candidate prefixes do not authorize hit-rate calculation.",
            "A client-version layout requires repeated multi-match validation before promotion.",
            "Object types without three exact observations remain unpromoted.",
        ],
        "hybridSourceUsed": hybrid_source_used,
        "fallbackUsed": False,
    }


def validate_candidate_prefix_probe(report: dict) -> None:
    if (
        report.get("format") != "er-projectile-spawn-prefix-candidate-probe.v1"
        or report.get("status") != "exact-schema-decoded-candidates-not-promoted"
        or report.get("fallbackUsed") is not False
        or not isinstance(report.get("hybridSourceUsed"), bool)
    ):
        raise ValueError("candidate prefix probe header is invalid")

    def keys(value):
        if isinstance(value, dict):
            for key, child in value.items():
                yield str(key).lower()
                yield from keys(child)
        elif isinstance(value, list):
            for child in value:
                yield from keys(child)

    forbidden = {
        "nickname",
        "userid",
        "usernum",
        "playerobjectid",
        "ownerobjectid",
        "projectileobjectid",
        "targetobjectid",
    }
    if forbidden & set(keys(report)):
        raise ValueError("candidate prefix probe contains private identity keys")
    runtime = report.get("runtime")
    source = report.get("source")
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
        or (
            report["hybridSourceUsed"]
            != (identity_authority == "dak-transformed-same-game-users-v1")
        )
    ):
        raise ValueError("candidate prefix identity authority is invalid")
    candidates = runtime.get("candidateObjectTypes") if isinstance(runtime, dict) else None
    if not isinstance(candidates, list) or runtime.get("candidateObjectTypeCount") != len(candidates):
        raise ValueError("candidate prefix probe coverage is invalid")
    for row in candidates:
        if (
            not isinstance(row.get("objectType"), int)
            or row.get("occurrenceCount", 0) < MIN_CANDIDATE_OBSERVATIONS
            or row.get("exactProjectileCodeCount") != row.get("occurrenceCount")
            or row.get("positiveOwnerCount") != row.get("occurrenceCount")
            or not 0 < row.get("knownOwnerCount", 0) <= row.get("occurrenceCount")
            or row.get("collisionLinkedObjectCount", 0) <= 0
            or row.get("promotionStatus")
            != "candidate-only-needs-multi-match-version-validation"
            or row.get("fallbackUsed") is not False
        ):
            raise ValueError("candidate prefix row is invalid")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Build an anonymous new-version projectile prefix candidate audit"
    )
    parser.add_argument("replay", type=Path)
    parser.add_argument("game_data", type=Path)
    parser.add_argument("--out", required=True, type=Path)
    args = parser.parse_args()
    report = build_candidate_prefix_probe(args.replay.resolve(), args.game_data.resolve())
    validate_candidate_prefix_probe(report)
    output = args.out.resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(
        json.dumps(
            {
                "output": str(output),
                "clientVersion": report["clientVersion"],
                "candidateObjectTypes": report["runtime"]["candidateObjectTypeCount"],
                "fallbackUsed": False,
            }
        )
    )


if __name__ == "__main__":
    main()
