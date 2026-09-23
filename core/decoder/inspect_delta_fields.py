"""Attempt strict schema decoding for every encountered replay packet type."""

from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import json
from pathlib import Path
import struct

from delta_payloads import DecodeError, SchemaDecoder, safe_sample
from inspect_deltas import iter_records, load_definitions, parse_delta_payload


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("replay", type=Path)
    parser.add_argument("--out", type=Path)
    args = parser.parse_args()

    replay_path = args.replay.resolve()
    output_path = (
        args.out or replay_path.with_suffix(".delta-fields.inspect.json")
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

    counts = Counter()
    exact_counts = Counter()
    null_payload_counts = Counter()
    failures: dict[int, Counter] = defaultdict(Counter)
    samples = {}
    categories: dict[int, Counter] = defaultdict(Counter)

    delta_records = [
        record for record in records
        if record["kind"] == 1 and record["version"] == 1
    ]
    for record in delta_records:
        delta = parse_delta_payload(record["payload"], record["tick"])
        for category in (
            "ignoreOrderPackets", "commands", "itemBoxPackets", "clientPackets"
        ):
            for wrapper in delta[category]:
                packet_type = wrapper["packetType"]
                payload = wrapper["payload"]
                counts[packet_type] += 1
                categories[packet_type][category] += 1
                if payload is None:
                    null_payload_counts[packet_type] += 1
                    continue
                type_name = packet_names.get(packet_type)
                if type_name is None:
                    failures[packet_type]["definition-name-missing"] += 1
                    continue
                if not decoder.supports_object_type(type_name):
                    failures[packet_type]["schema-class-missing"] += 1
                    continue
                try:
                    value = decoder.decode_exact(payload, type_name)
                except (DecodeError, IndexError, KeyError, struct.error, UnicodeError) as error:
                    failures[packet_type][f"{type(error).__name__}: {error}"] += 1
                    continue
                exact_counts[packet_type] += 1
                if packet_type not in samples:
                    samples[packet_type] = safe_sample(value)

    inventory = []
    for packet_type in sorted(counts):
        type_name = packet_names.get(packet_type)
        members = decoder.wire_members_of(type_name) if type_name else []
        failure_count = sum(failures[packet_type].values())
        if exact_counts[packet_type] == counts[packet_type]:
            status = "decoded-exact-all"
        elif exact_counts[packet_type] > 0:
            status = "decoded-partial"
        elif failure_count:
            status = "structural-only"
        else:
            status = "unknown"
        inventory.append(
            {
                "packetType": packet_type,
                "name": type_name,
                "status": status,
                "payloadCount": counts[packet_type],
                "exactCount": exact_counts[packet_type],
                "nullPayloadCount": null_payload_counts[packet_type],
                "countsByCategory": dict(categories[packet_type]),
                "members": [
                    {
                        "order": member.order,
                        "declaringType": member.declaring_type,
                        "name": member.name,
                        "type": member.field_type,
                    }
                    for member in members
                ],
                "sample": samples.get(packet_type),
                "failures": [
                    {"reason": reason, "count": count}
                    for reason, count in failures[packet_type].most_common(5)
                ],
            }
        )

    statuses = Counter(item["status"] for item in inventory)
    observed_fields = [
        {
            "declaringType": declaring_type,
            "order": order,
            "name": name,
            "type": field_type,
            "decodedSampleCount": sample_count,
            "status": "decoded-exact-wire",
        }
        for (
            declaring_type,
            order,
            name,
            field_type,
        ), sample_count in sorted(decoder.field_reads.items())
    ]
    report = {
        "sourceFile": str(replay_path),
        "clientVersion": client_version,
        "deltaRecordCount": len(delta_records),
        "packetPayloadCount": sum(counts.values()),
        "packetTypeCount": len(inventory),
        "statusCounts": dict(statuses),
        "observedSchemaTypeCount": len(decoder.type_reads),
        "observedFieldDefinitionCount": len(observed_fields),
        "observedSchemaTypeReads": dict(sorted(decoder.type_reads.items())),
        "wireOverrides": dict(sorted(decoder.wire_overrides.items())),
        "observedFieldInventory": observed_fields,
        "privacy": "String samples are redacted to type and character length.",
        "inventory": inventory,
        "limitations": [
            "decoded-exact-all means byte consumption and schema type decoding, not semantic plausibility for every value.",
            "Unmanaged nullable layout follows MemoryPack native layout for the held Windows/Unity build and is accepted only when exact payload boundaries agree.",
            "Nested byte[] snapshots remain opaque bytes unless their owning packet schema names a separately decoded type.",
            "Union types other than SnapshotWrapper are unsupported and fail closed.",
            "Version-specific definition bases and the observed direct legacy SnapshotWrapperFull layout are reported explicitly in wireOverrides.",
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
                "packetPayloads": report["packetPayloadCount"],
                "packetTypes": report["packetTypeCount"],
                "statusCounts": report["statusCounts"],
            },
            ensure_ascii=True,
        )
    )


if __name__ == "__main__":
    main()
