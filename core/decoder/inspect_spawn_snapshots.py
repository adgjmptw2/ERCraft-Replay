"""Probe nested snapshot byte arrays in observed replay spawn packets.

For each observed ObjectType, intersect the schema classes that consume every
sampled nested byte array exactly.  This is a candidate finder: ambiguous or
empty intersections remain explicit and are never promoted to a concrete type.
"""

from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import hashlib
import json
from pathlib import Path
import struct

from delta_payloads import Cursor, DecodeError, SchemaDecoder, safe_sample
from inspect_deltas import iter_records, load_definitions, parse_delta_payload


SPAWN_PACKET_NAMES = {
    "CmdSpawn",
    "CmdSpawns",
    "CmdSpawnAirSupplyItemBox",
}
EXCLUDED_CANDIDATES = {
    "ReplaySnapshot",
    "GameSnapshot",
    "ResGameSnapshot",
    "ReqGameSnapshot",
    "UserSnapshot",
    "SnapshotWrapper",
    "SnapshotWrapperBasic",
    "SnapshotWrapperFull",
}
TAGGED_UNIONS = {
    "CorpseSnapshotBase<tagged-union>": {
        0: "CorpseSnapshotPlayer",
        1: "CorpseSnapshotMonster",
        2: "CorpseSnapshotDefault",
    }
}
EXTRA_CANDIDATE_TYPES = {
    # ObjectType.AirSupplyItemBoxSpawnScratch carries this seven-member payload
    # even though its schema type does not end in "Snapshot".
    "AirSupplyInfo",
    # The game schema itself spells this type without the "S" in Snapshot.
    "MobileKiosknapshot",
}
OBJECT_TYPE_SCHEMA_ALIASES = {
    "MobileKiosk": "MobileKiosknapshot",
}
OBJECT_TYPE_SCHEMA_FAMILY_HINTS = {
    "SummonCamera": {
        "candidate": "SummonSnapshot",
        "evidence": "common 18-member summon payload; no SummonCameraSnapshot exists in the versioned schema",
    },
    "SummonTrap": {
        "candidate": "SummonSnapshot",
        "evidence": "common 18-member summon payload; no SummonTrapSnapshot exists in the versioned schema",
    },
    "SummonArtifact": {
        "candidate": "SummonSnapshot",
        "evidence": "common 18-member summon payload; no SummonArtifactSnapshot exists in the versioned schema",
    },
    "ChainProjectile": {
        "candidate": "ProjectileSnapshot",
        "evidence": "common five-member projectile payload with no HookLine-only member; no ChainProjectileSnapshot exists in the versioned schema",
    },
    "RiftSpawnScratch": {
        "candidate": "RiftSnapshot",
        "evidence": "metadata WorldRiftSpawnScratch fields riftType/cooldownUntil/areaCode overlap the exact RiftSnapshot payload",
    },
}
SUMMON_OBJECT_TYPE_NAMES = {
    "SummonCamera",
    "SummonTrap",
    "SummonServant",
    "SummonArtifact",
    "SummonRope",
}
PROJECTILE_MOVEMENT_SNAPSHOTS = {
    "TargetProjectileSnapshot",
    "DirectionProjectileSnapshot",
    "InstantArrivalProjectileSnapshot",
    "AroundProjectileSnapshot",
}


def normalized_snapshot_name(name: str) -> str:
    if name.endswith("Snapshot"):
        name = name[: -len("Snapshot")]
    return "".join(character.lower() for character in name if character.isalnum())


def semantic_candidate_for(
    object_type_name: str | None,
    exact_intersection: set[str],
    promoted_candidate: str | None,
) -> tuple[str | None, str, str]:
    if promoted_candidate is not None:
        return (
            promoted_candidate,
            "decoded-exact-wire",
            "one candidate consumes every observed occurrence exactly",
        )
    if object_type_name is None:
        return (
            None,
            "unresolved-no-versioned-object-type-name",
            "no versioned ObjectType enum report was supplied",
        )
    alias = OBJECT_TYPE_SCHEMA_ALIASES.get(object_type_name)
    if alias in exact_intersection:
        return (
            alias,
            "derived-metadata-schema-alias-v1",
            "versioned metadata and schema use the misspelled MobileKiosknapshot type name",
        )
    target = normalized_snapshot_name(object_type_name)
    matches = [
        candidate
        for candidate in exact_intersection
        if normalized_snapshot_name(candidate) == target
    ]
    if len(matches) == 1:
        return (
            matches[0],
            "derived-name-rule-v1",
            "normalized ObjectType name equals one exact candidate after removing Snapshot suffix",
        )
    family_hint = OBJECT_TYPE_SCHEMA_FAMILY_HINTS.get(object_type_name)
    if family_hint and family_hint["candidate"] in exact_intersection:
        return (
            family_hint["candidate"],
            "derived-metadata-family-rule-v1",
            family_hint["evidence"],
        )
    return (
        None,
        "ambiguous-wire-equivalent",
        "multiple candidates consume the same bytes and no versioned semantic rule selects one",
    )


def load_object_type_names(path: Path | None) -> tuple[dict[int, str], dict | None]:
    if path is None:
        return {}, None
    report = json.loads(path.read_text(encoding="utf-8"))
    matching = [
        enum
        for enum in report.get("enums", [])
        if enum.get("namespace") == "Blis.Common"
        and enum.get("name") == "ObjectType"
    ]
    if len(matching) != 1:
        raise ValueError(
            f"expected one Blis.Common.ObjectType enum in {path}, got {len(matching)}"
        )
    enum = matching[0]
    if enum.get("numericAssignmentStatus") != "decoded-compressed-int32-exact":
        raise ValueError("ObjectType enum numeric assignments are not exact")
    names = {}
    for field in enum.get("fields", []):
        default = field.get("default")
        if default is None or "compressedInt32" not in default:
            continue
        names[default["compressedInt32"]] = field["name"]
    return names, {
        "reportFile": str(path.resolve()),
        "metadataSourceFile": report.get("sourceFile"),
        "metadataSourceSha256": report.get("sourceSha256"),
        "metadataVersion": report.get("metadataVersion"),
        "enumNamespace": enum["namespace"],
        "enumName": enum["name"],
        "numericAssignmentStatus": enum["numericAssignmentStatus"],
    }


def decode_candidate_exact(
    decoder: SchemaDecoder,
    payload: bytes,
    candidate_type: str,
):
    if candidate_type == 'BaseResourceItemBoxSnapshot<concrete-object>':
        if decoder.client_version != '12.4.0':
            raise DecodeError('concrete resource snapshot requires exact 12.4')
        cursor = Cursor(payload)
        value = decoder.read_object(cursor, 'BaseResourceItemBoxSnapshot')
        if cursor.offset != len(payload):
            raise DecodeError('concrete resource snapshot trailing bytes')
        return value
    union_types = TAGGED_UNIONS.get(candidate_type)
    if union_types is None:
        return decoder.decode_exact(payload, candidate_type)
    if not payload:
        raise DecodeError(f"empty {candidate_type} payload")
    tag = payload[0]
    concrete = union_types.get(tag)
    if concrete is None:
        raise DecodeError(f"unsupported {candidate_type} tag {tag}")
    return {
        "__union": candidate_type,
        "tag": tag,
        "concreteType": concrete,
        "value": decoder.decode_exact(payload[1:], concrete),
    }


def exact_candidates(
    decoder: SchemaDecoder,
    payload: bytes,
    candidate_types: list[str],
) -> set[str]:
    candidates = set()
    for type_name in candidate_types:
        try:
            decode_candidate_exact(decoder, payload, type_name)
        except (
            DecodeError,
            IndexError,
            KeyError,
            struct.error,
            UnicodeError,
        ):
            continue
        candidates.add(type_name)
    return candidates


def collect_nested_byte_arrays(
    value,
    paths: dict[str, dict],
    path: str = "$",
) -> None:
    if isinstance(value, (bytes, bytearray)):
        payload = bytes(value)
        item = paths.setdefault(
            path,
            {
                "occurrenceCount": 0,
                "lengthCounts": Counter(),
                "sha256Counts": Counter(),
                "payloads": [],
            },
        )
        item["occurrenceCount"] += 1
        item["lengthCounts"][len(payload)] += 1
        item["sha256Counts"][hashlib.sha256(payload).hexdigest()] += 1
        item["payloads"].append(payload)
        return
    if isinstance(value, dict):
        for key, child in value.items():
            if key == "__type":
                continue
            child_path = f"{path}.{key}" if isinstance(key, str) else f"{path}{{value}}"
            collect_nested_byte_arrays(child, paths, child_path)
        return
    if isinstance(value, list):
        for child in value:
            collect_nested_byte_arrays(child, paths, f"{path}[]")


def nested_candidate_summary(
    decoder: SchemaDecoder,
    payloads: list[bytes],
    candidate_types: list[str],
    sample_limit: int = 12,
) -> dict:
    """Find exact schema candidates for an already-decoded nested byte[] path.

    Candidate discovery uses up to ``sample_limit`` distinct payloads, then every
    candidate in that intersection is checked against every observed payload.
    Length groups are reported separately because a byte[] path can carry a
    heterogeneous tagged-by-context family (projectile movement is one example).
    """

    payload_counts = Counter(payloads)
    distinct_payloads = list(payload_counts)
    candidate_cache: dict[bytes, set[str]] = {}

    def candidates_for(payload: bytes) -> set[str]:
        cached = candidate_cache.get(payload)
        if cached is None:
            cached = exact_candidates(decoder, payload, candidate_types)
            candidate_cache[payload] = cached
        return cached

    distinct_samples = distinct_payloads[:sample_limit]
    sample_sets = [candidates_for(payload) for payload in distinct_samples]
    sample_intersection = (
        set.intersection(*sample_sets) if sample_sets else set()
    )
    all_observed = []
    for candidate in sorted(sample_intersection):
        if all(candidate in candidates_for(payload) for payload in distinct_payloads):
            all_observed.append(candidate)

    length_groups = []
    by_length: dict[int, list[bytes]] = defaultdict(list)
    for payload in payloads:
        by_length[len(payload)].append(payload)
    for length, grouped_payloads in sorted(by_length.items()):
        grouped_all_distinct = list(dict.fromkeys(grouped_payloads))
        grouped_distinct = grouped_all_distinct[:sample_limit]
        grouped_sets = [candidates_for(payload) for payload in grouped_distinct]
        grouped_intersection = (
            set.intersection(*grouped_sets) if grouped_sets else set()
        )
        grouped_all = []
        for candidate in sorted(grouped_intersection):
            if all(
                candidate in candidates_for(payload)
                for payload in grouped_all_distinct
            ):
                grouped_all.append(candidate)
        length_groups.append(
            {
                "length": length,
                "occurrenceCount": len(grouped_payloads),
                "distinctPayloadCount": len(grouped_all_distinct),
                "sampleCandidateIntersection": sorted(grouped_intersection),
                "allObservedCandidates": grouped_all,
            }
        )

    signature_counts = Counter()
    signature_distinct_counts = Counter()
    signature_samples: dict[tuple[int, tuple[str, ...]], bytes] = {}
    for payload, occurrence_count in payload_counts.items():
        signature = (len(payload), tuple(sorted(candidates_for(payload))))
        signature_counts[signature] += occurrence_count
        signature_distinct_counts[signature] += 1
        signature_samples.setdefault(signature, payload)
    candidate_signatures = []
    for signature, occurrence_count in sorted(
        signature_counts.items(),
        key=lambda item: (-item[1], item[0][0], item[0][1]),
    ):
        length, candidates = signature
        candidate_signatures.append(
            {
                "length": length,
                "occurrenceCount": occurrence_count,
                "distinctPayloadCount": signature_distinct_counts[signature],
                "exactCandidates": list(candidates),
                "sampleSha256": hashlib.sha256(
                    signature_samples[signature]
                ).hexdigest(),
                "sampleHex": signature_samples[signature].hex(),
                "sampleHexPrefix": signature_samples[signature][:32].hex(),
            }
        )

    return {
        "distinctSampleCount": len(distinct_samples),
        "sampleCandidateIntersection": sorted(sample_intersection),
        "allObservedCandidates": all_observed,
        "lengthGroups": length_groups,
        "candidateSignatures": candidate_signatures,
    }


def attach_nested_semantics(
    decoder: SchemaDecoder,
    object_type_name: str | None,
    path: str,
    summary: dict,
) -> None:
    """Attach context-derived semantic candidates without hiding wire ambiguity."""

    resolved = 0
    for signature in summary["candidateSignatures"]:
        exact = set(signature["exactCandidates"])
        sample_payload = bytes.fromhex(signature["sampleHex"])
        candidate = None
        status = "unresolved-wire-equivalent"
        evidence = "no context rule selects one exact candidate"

        if path == "$.statusSnapshot" and object_type_name == "Monster":
            candidate = "MonsterStatusSnapshot"
            status = "derived-object-family-rule-v1"
            evidence = "MonsterSnapshot.statusSnapshot and exact MonsterStatusSnapshot candidate"
        elif path == "$.statusSnapshot" and object_type_name in SUMMON_OBJECT_TYPE_NAMES:
            candidate = "SummonStatusSnapshot"
            status = "derived-object-family-rule-v1"
            evidence = "summon-family statusSnapshot and exact SummonStatusSnapshot candidate"
        elif path == "$.value.statusSnapshot" and object_type_name == "Corpse":
            candidate = "BaseCharacterStatusSnapshot"
            status = "derived-common-base-rule-v1"
            evidence = "corpse union contains player and monster characters; the shared eight-member status base is wire-exact"
        elif path == "$.snapshot" and object_type_name in {"Projectile", "ChainProjectile"}:
            movement = sorted(exact & PROJECTILE_MOVEMENT_SNAPSHOTS)
            full_arity = [
                movement_candidate
                for movement_candidate in movement
                if sample_payload
                and sample_payload[0]
                == len(decoder.members_of(movement_candidate))
            ]
            if len(full_arity) == 1:
                movement = full_arity
            if len(movement) == 1:
                candidate = movement[0]
                status = (
                    "decoded-exact-wire"
                    if len(exact) == 1
                    else "derived-owner-family-rule-v1"
                )
                evidence = "ProjectileSnapshot.snapshot and one exact projectile-movement family candidate"
        elif path == "$.snapshot" and object_type_name == "ProjectileDeflector":
            candidate = "TargetProjectileDeflectorSnapshot"
            status = "derived-owner-family-rule-v1"
            evidence = "ProjectileDeflectorSnapshot.snapshot and exact deflector-specific candidate"
        elif path == "$.serializeScript" and object_type_name == "Rift":
            candidate = "DimensionalRiftSnapshot"
            status = "derived-metadata-family-rule-v1"
            evidence = "RiftSnapshot.serializeScript and versioned RiftScriptSnapshot subtype metadata"

        if candidate not in exact:
            candidate = None
            status = "unresolved-rule-candidate-not-exact"
            evidence = "context rule candidate did not consume this signature exactly"

        sample = None
        if candidate is not None:
            sample = safe_sample(
                decode_candidate_exact(
                    decoder,
                    sample_payload,
                    candidate,
                )
            )
            resolved += 1
        signature["semanticCandidate"] = candidate
        signature["semanticCandidateStatus"] = status
        signature["semanticCandidateEvidence"] = evidence
        signature["semanticCandidateSample"] = sample
        del signature["sampleHex"]

    summary["semanticResolvedSignatureCount"] = resolved
    summary["semanticSignatureCount"] = len(summary["candidateSignatures"])
    summary["semanticAllSignaturesResolved"] = (
        resolved == len(summary["candidateSignatures"])
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("replay", type=Path)
    parser.add_argument("--out", type=Path)
    parser.add_argument("--samples-per-type", type=int, default=12)
    parser.add_argument("--object-type-enum-report", type=Path)
    args = parser.parse_args()
    if args.samples_per_type < 1:
        raise ValueError("--samples-per-type must be positive")

    replay_path = args.replay.resolve()
    output_path = (
        args.out
        or replay_path.with_suffix(".spawn-snapshots.inspect.json")
    ).resolve()
    data = replay_path.read_bytes()
    client_version = data[16:32].split(b"\0", 1)[0].decode("ascii")
    records = list(iter_records(data))
    definitions, packet_names = load_definitions(records)
    base_types = {
        definition["name"]: definition.get("baseType")
        for definition in definitions
        if isinstance(definition.get("name"), str)
    }
    decoder = SchemaDecoder(base_types, client_version=client_version)
    object_type_names, object_type_enum_source = load_object_type_names(
        args.object_type_enum_report.resolve()
        if args.object_type_enum_report
        else None
    )
    candidate_types = sorted(
        type_name
        for type_name in decoder.classes
        if type_name.endswith("Snapshot")
        and type_name not in EXCLUDED_CANDIDATES
    )
    candidate_types.extend(sorted(EXTRA_CANDIDATE_TYPES))
    candidate_types.extend(sorted(TAGGED_UNIONS))

    counts = Counter()
    wrapper_kinds: dict[int, Counter] = defaultdict(Counter)
    length_counts: dict[int, Counter] = defaultdict(Counter)
    samples: dict[int, list[bytes]] = defaultdict(list)
    all_nested_payloads: dict[int, list[bytes]] = defaultdict(list)
    sample_meta: dict[int, list[dict]] = defaultdict(list)
    null_nested = Counter()

    for record in records:
        if record["kind"] != 1 or record["version"] != 1:
            continue
        delta = parse_delta_payload(record["payload"], record["tick"])
        for wrapper_category in (
            "ignoreOrderPackets",
            "commands",
            "itemBoxPackets",
            "clientPackets",
        ):
            for wrapper in delta[wrapper_category]:
                packet_type = wrapper["packetType"]
                packet_name = packet_names.get(packet_type)
                if packet_name not in SPAWN_PACKET_NAMES:
                    continue
                payload = wrapper["payload"]
                if payload is None:
                    raise DecodeError(f"spawn packet {packet_type} has null payload")
                decoded = decoder.decode_exact(payload, packet_name)
                if packet_name == "CmdSpawn":
                    snapshot_wrappers = [decoded["snapshot"]]
                elif packet_name == "CmdSpawns":
                    snapshot_wrappers = decoded["snapshots"]
                else:
                    snapshot_wrappers = decoded["spawnSnapshots"]
                for snapshot_wrapper in snapshot_wrappers or []:
                    if snapshot_wrapper is None:
                        continue
                    object_type = snapshot_wrapper["objectType"]
                    nested = snapshot_wrapper["snapshot"]
                    counts[object_type] += 1
                    wrapper_kinds[object_type][snapshot_wrapper["__type"]] += 1
                    if nested is None:
                        null_nested[object_type] += 1
                        continue
                    length_counts[object_type][len(nested)] += 1
                    all_nested_payloads[object_type].append(nested)
                    if len(samples[object_type]) < args.samples_per_type:
                        samples[object_type].append(nested)
                        sample_meta[object_type].append(
                            {
                                "tick": record["tick"],
                                "packetType": packet_type,
                                "wrapperCategory": wrapper_category,
                                "objectId": snapshot_wrapper["objectId"],
                                "inWorldType": snapshot_wrapper["inWorldType"],
                                "nestedBytes": len(nested),
                                "nestedSha256": hashlib.sha256(nested).hexdigest(),
                                "nestedHexPrefix": nested[:32].hex(),
                            }
                        )

    object_types = []
    for object_type in sorted(counts):
        sample_candidate_sets = [
            exact_candidates(decoder, sample, candidate_types)
            for sample in samples[object_type]
        ]
        intersection = (
            set.intersection(*sample_candidate_sets)
            if sample_candidate_sets
            else set()
        )
        union = set().union(*sample_candidate_sets) if sample_candidate_sets else set()
        promoted = next(iter(intersection)) if len(intersection) == 1 else None
        promoted_sample = None
        promoted_exact_count = 0
        promoted_union_concrete_counts = Counter()
        if promoted is not None and samples[object_type]:
            promoted_sample = safe_sample(
                decode_candidate_exact(decoder, samples[object_type][0], promoted)
            )
            for nested in all_nested_payloads[object_type]:
                try:
                    decoded_promoted = decode_candidate_exact(
                        decoder, nested, promoted
                    )
                except (
                    DecodeError,
                    IndexError,
                    KeyError,
                    struct.error,
                    UnicodeError,
                ):
                    continue
                promoted_exact_count += 1
                if isinstance(decoded_promoted, dict) and "concreteType" in decoded_promoted:
                    promoted_union_concrete_counts[
                        decoded_promoted["concreteType"]
                    ] += 1
        promoted_all_observed = (
            promoted is not None
            and promoted_exact_count == len(all_nested_payloads[object_type])
        )
        object_type_name = object_type_names.get(object_type)
        (
            semantic_candidate,
            semantic_candidate_status,
            semantic_candidate_evidence,
        ) = semantic_candidate_for(
            object_type_name,
            intersection,
            promoted if promoted_all_observed else None,
        )
        semantic_exact_count = 0
        semantic_sample = None
        semantic_byte_paths: dict[str, dict] = {}
        if semantic_candidate is not None:
            for nested in all_nested_payloads[object_type]:
                try:
                    semantic_decoded = decode_candidate_exact(
                        decoder, nested, semantic_candidate
                    )
                except (
                    DecodeError,
                    IndexError,
                    KeyError,
                    struct.error,
                    UnicodeError,
                ):
                    continue
                semantic_exact_count += 1
                if semantic_sample is None:
                    semantic_sample = safe_sample(semantic_decoded)
                collect_nested_byte_arrays(
                    semantic_decoded,
                    semantic_byte_paths,
                )
        semantic_all_observed = (
            semantic_candidate is not None
            and semantic_exact_count == len(all_nested_payloads[object_type])
        )
        if semantic_candidate is not None and not semantic_all_observed:
            semantic_candidate_status = (
                f"{semantic_candidate_status}-sampled-only"
            )
            semantic_candidate_evidence += (
                "; candidate did not consume every observed occurrence"
            )
        nested_byte_array_inventory = []
        if semantic_all_observed:
            for byte_path, item in sorted(semantic_byte_paths.items()):
                most_common_hashes = item["sha256Counts"].most_common(5)
                candidate_summary = nested_candidate_summary(
                    decoder,
                    item["payloads"],
                    candidate_types,
                    args.samples_per_type,
                )
                attach_nested_semantics(
                    decoder,
                    object_type_name,
                    byte_path,
                    candidate_summary,
                )
                nested_byte_array_inventory.append(
                    {
                        "path": byte_path,
                        "occurrenceCount": item["occurrenceCount"],
                        "lengthCounts": {
                            str(length): count
                            for length, count in sorted(item["lengthCounts"].items())
                        },
                        "distinctSha256Count": len(item["sha256Counts"]),
                        "topSha256Counts": [
                            {"sha256": sha256, "count": count}
                            for sha256, count in most_common_hashes
                        ],
                        "candidateAnalysis": candidate_summary,
                    }
                )
        object_types.append(
            {
                "objectType": object_type,
                "objectTypeName": object_type_name,
                "occurrenceCount": counts[object_type],
                "nullNestedSnapshotCount": null_nested[object_type],
                "wrapperTypeCounts": dict(wrapper_kinds[object_type]),
                "nestedLengthCounts": {
                    str(length): count
                    for length, count in sorted(length_counts[object_type].items())
                },
                "sampleCount": len(samples[object_type]),
                "candidateIntersection": sorted(intersection),
                "candidateUnion": sorted(union),
                "candidateCountsPerSample": [
                    len(candidates) for candidates in sample_candidate_sets
                ],
                "status": (
                    "unique-exact-candidate-all-observed"
                    if promoted_all_observed
                    else "unique-exact-candidate-sampled-only"
                    if promoted is not None
                    else "ambiguous-exact-candidates"
                    if intersection
                    else "no-common-exact-candidate"
                ),
                "promotedCandidate": promoted,
                "promotedExactCount": promoted_exact_count,
                "promotedAllObserved": promoted_all_observed,
                "promotedUnionConcreteCounts": dict(
                    promoted_union_concrete_counts
                ),
                "promotedCandidateSample": promoted_sample,
                "semanticCandidate": semantic_candidate,
                "semanticCandidateStatus": semantic_candidate_status,
                "semanticCandidateEvidence": semantic_candidate_evidence,
                "semanticExactCount": semantic_exact_count,
                "semanticAllObserved": semantic_all_observed,
                "semanticCandidateSample": semantic_sample,
                "nestedByteArrayInventory": nested_byte_array_inventory,
                "samples": sample_meta[object_type],
            }
        )

    report = {
        "sourceFile": str(replay_path),
        "sourceSha256": hashlib.sha256(data).hexdigest(),
        "clientVersion": client_version,
        "spawnPacketTypes": {
            str(packet_type): packet_name
            for packet_type, packet_name in sorted(packet_names.items())
            if packet_name in SPAWN_PACKET_NAMES
        },
        "candidateTypeCount": len(candidate_types),
        "samplesPerObjectTypeLimit": args.samples_per_type,
        "spawnedSnapshotCount": sum(counts.values()),
        "objectTypeCount": len(object_types),
        "namedObjectTypeCount": sum(
            item["objectTypeName"] is not None for item in object_types
        ),
        "objectTypeEnumSource": object_type_enum_source,
        "semanticCandidateStatusCounts": dict(
            Counter(item["semanticCandidateStatus"] for item in object_types)
        ),
        "semanticAllObservedCount": sum(
            item["semanticAllObserved"] for item in object_types
        ),
        "nestedByteArrayPathCount": sum(
            len(item["nestedByteArrayInventory"]) for item in object_types
        ),
        "nestedByteArrayAllObservedUniqueCandidateCount": sum(
            len(nested["candidateAnalysis"]["allObservedCandidates"]) == 1
            for item in object_types
            for nested in item["nestedByteArrayInventory"]
        ),
        "nestedByteArraySemanticResolvedPathCount": sum(
            nested["candidateAnalysis"]["semanticAllSignaturesResolved"]
            for item in object_types
            for nested in item["nestedByteArrayInventory"]
        ),
        "wireOverrides": dict(sorted(decoder.wire_overrides.items())),
        "objectTypes": object_types,
        "limitations": [
            "A unique candidate means exact schema consumption across sampled payloads, not an authoritative ObjectType enum label.",
            "Multiple concrete classes can share a wire layout and remain ambiguous.",
            "Nested byte[] members are probed recursively against versioned schema candidates; multiple wire-equivalent or context-dependent candidates remain explicit.",
            f"Only exact replay spawn-packet occurrences in the held {data[16:32].split(bytes([0]), 1)[0].decode('ascii')} replay are sampled.",
        ],
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(
        json.dumps(
            {
                "output": str(output_path),
                "spawnedSnapshots": report["spawnedSnapshotCount"],
                "objectTypes": report["objectTypeCount"],
                "statuses": dict(Counter(item["status"] for item in object_types)),
            },
            ensure_ascii=True,
        )
    )


if __name__ == "__main__":
    main()
