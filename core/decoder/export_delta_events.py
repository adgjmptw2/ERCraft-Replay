"""Export a transparent semantic catalog and selected exact replay events.

Wire decoding remains authoritative.  The category assigned to a packet is a
name-based organizational aid (`derived-name-rule-v1`), not a claim that every
numeric enum/code has been mapped to a game-data meaning.

The event stream is JSON Lines so large exports can be inspected incrementally.
Strings originating in replay payloads are redacted by default; generated
schema type names remain visible.  Nested byte arrays are represented by their
length and SHA-256 rather than copied into the report.
"""

from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import hashlib
import json
import math
from pathlib import Path
import struct
from typing import Any

from delta_payloads import DecodeError, SchemaDecoder
from inspect_deltas import iter_records, load_definitions, parse_delta_payload


CATEGORY_STATUS = "derived-name-rule-v1"
CATEGORIES = (
    "combat",
    "skill",
    "state-stat",
    "movement",
    "inventory-item",
    "world-object",
    "communication-ui",
    "system-other",
)


COMBAT_NAMES = {
    "CmdDamage",
    "CmdHeal",
    "CmdHealStateCode",
    "CmdBlock",
    "CmdEvasion",
    "CmdCrowdControl",
    "CmdKill",
    "CmdKillVoice",
    "CmdDead",
    "CmdDyingCondition",
    "CmdResurrection",
    "CmdTeamRevival",
    "CmdTeamRevivalStart",
    "CmdTeamRevivalCancel",
    "CmdAutoResurrectionWaitTime",
    "CmdAutoResurrectionReadyTime",
    "CmdAutoResurrectionCancel",
    "CmdUpdateInAutoResurrection",
    "CmdUpdateLastCorpse",
    "CmdUpdateTeamKillCount",
}

STATE_STAT_NAMES = {
    "CmdResetCharacter",
    "CmdUpdateInCombatType",
}


def classify_packet(name: str) -> tuple[str, str]:
    """Assign every observed packet name to one transparent derived bucket."""
    if name in COMBAT_NAMES:
        return "combat", "explicit-combat-lifecycle"

    if name in STATE_STAT_NAMES:
        return "state-stat", "explicit-state-stat"

    if any(token in name for token in (
        "Skill", "Concentration", "ActionCast", "ConsumeCost", "Combo", "Trait"
    )):
        return "skill", "name-token"

    if any(token in name for token in (
        "State", "Stat", "Level", "Exp", "Shield", "Resource", "ExtraPoint",
        "VFCredit", "Gadget", "Survivable", "Mastery", "Bullet",
        "Cooldown", "Stealth", "Infiltration", "Clutch",
    )):
        return "state-stat", "name-token"

    if any(token in name for token in (
        "Move", "Warp", "Rotate", "Rotation", "LookAt", "VLS", "Vls", "HyperLoop",
        "JumpPaddle", "Walkable", "AccelerationBoundary", "Rope",
    )):
        return "movement", "name-token"

    if any(token in name for token in (
        "Item", "Inventory", "Equipment", "Kiosk", "Console",
    )):
        return "inventory-item", "name-token"

    if any(token in name for token in (
        "Spawn", "Destroy", "Projectile", "Sight", "Bush", "Area", "Barrier",
        "Scratch", "Rift", "Timeline", "Evidence", "BossMonster", "Wickline",
        "Restricted", "FinalArea", "GamePlayPhase", "FinishGame",
        "Summon", "Installation", "TransferDrone", "ChronoSphere",
        "UnknownObjectTimer",
    )):
        return "world-object", "name-token"

    if any(token in name for token in (
        "Chat", "Ping", "Emotion", "Announce", "Notify", "Noise", "Voice",
        "MapIcon", "TimeScale",
    )):
        return "communication-ui", "name-token"

    return "system-other", "catch-all-explicitly-unclassified"


def privacy_safe(value: Any, *, key: str | None = None) -> Any:
    """Preserve decoded structure while redacting replay-originated strings."""
    if isinstance(value, str):
        if key == "__type":
            return value
        return {"type": "string", "length": len(value)}
    if isinstance(value, bytes):
        return {
            "type": "bytes",
            "length": len(value),
            "sha256": hashlib.sha256(value).hexdigest(),
        }
    if isinstance(value, list):
        return [privacy_safe(item) for item in value]
    if isinstance(value, dict):
        return {
            item_key: privacy_safe(item_value, key=item_key)
            for item_key, item_value in value.items()
        }
    if isinstance(value, float) and not math.isfinite(value):
        return {"type": "non-finite-float", "value": str(value)}
    return value


def default_output_path(replay_path: Path, suffix: str) -> Path:
    return replay_path.with_suffix(suffix)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("replay", type=Path)
    parser.add_argument("--catalog-out", type=Path)
    parser.add_argument("--events-out", type=Path)
    parser.add_argument(
        "--include-category",
        action="append",
        choices=CATEGORIES,
        dest="included_categories",
        help="Category to emit to JSONL; repeat for multiple categories. Default: combat.",
    )
    args = parser.parse_args()

    replay_path = args.replay.resolve()
    catalog_path = (
        args.catalog_out
        or default_output_path(replay_path, ".event-catalog.inspect.json")
    ).resolve()
    events_path = (
        args.events_out
        or default_output_path(replay_path, ".events.inspect.ndjson")
    ).resolve()
    included_categories = set(args.included_categories or ["combat"])

    data = replay_path.read_bytes()
    if data[:16] != b"EternalReturnV1\0":
        raise ValueError("not an EternalReturnV1 replay")
    client_version = data[16:32].split(b"\0", 1)[0].decode("ascii")
    records = list(iter_records(data))
    definitions, packet_names = load_definitions(records)
    base_types = {
        definition["name"]: definition.get("baseType")
        for definition in definitions
        if isinstance(definition.get("name"), str)
    }
    decoder = SchemaDecoder(base_types, client_version=client_version)

    delta_records = [
        record for record in records
        if record["kind"] == 1 and record["version"] == 1
    ]
    if not delta_records:
        raise ValueError("no kind=1 version=1 delta records")

    packet_counts = Counter()
    category_counts = Counter()
    category_packet_types: dict[str, set[int]] = defaultdict(set)
    packet_categories: dict[int, tuple[str, str]] = {}
    exact_counts = Counter()
    emitted_counts = Counter()
    total_payloads = 0
    emitted_total = 0

    events_path.parent.mkdir(parents=True, exist_ok=True)
    events_temp_path = events_path.with_suffix(events_path.suffix + ".tmp")
    with events_temp_path.open("w", encoding="utf-8", newline="\n") as event_file:
        metadata = {
            "recordType": "metadata",
            "sourceFile": str(replay_path),
            "sourceSha256": hashlib.sha256(data).hexdigest(),
            "clientVersion": client_version,
            "targetFrameRate": 60,
            "includedCategories": sorted(included_categories),
            "wireStatus": "decoded-exact-wire",
            "semanticCategoryStatus": CATEGORY_STATUS,
            "privacy": (
                "Replay-originated strings are redacted to type/length; "
                "nested byte arrays are length+SHA-256 only."
            ),
            "timeCaveat": (
                "secondsAt60Hz is tick/60 and is not asserted to be the "
                "official displayed match duration."
            ),
        }
        event_file.write(json.dumps(metadata, ensure_ascii=False) + "\n")

        event_index = 0
        for record in delta_records:
            delta = parse_delta_payload(record["payload"], record["tick"])
            for wrapper_category in (
                "ignoreOrderPackets",
                "commands",
                "itemBoxPackets",
                "clientPackets",
            ):
                for wrapper_index, wrapper in enumerate(delta[wrapper_category]):
                    packet_type = wrapper["packetType"]
                    payload = wrapper["payload"]
                    packet_name = packet_names.get(packet_type)
                    if packet_name is None:
                        raise ValueError(f"packet type {packet_type} has no definition name")
                    category, category_rule = classify_packet(packet_name)
                    previous = packet_categories.setdefault(
                        packet_type, (category, category_rule)
                    )
                    if previous != (category, category_rule):
                        raise ValueError(
                            f"packet type {packet_type} changed semantic category"
                        )
                    packet_counts[packet_type] += 1
                    category_counts[category] += 1
                    category_packet_types[category].add(packet_type)
                    total_payloads += 1

                    if payload is None:
                        raise DecodeError(
                            f"{packet_name} packet {packet_type} has null payload"
                        )
                    value = decoder.decode_exact(payload, packet_name)
                    exact_counts[packet_type] += 1

                    if category not in included_categories:
                        continue
                    event_index += 1
                    emitted_total += 1
                    emitted_counts[packet_type] += 1
                    event = {
                        "recordType": "event",
                        "eventIndex": event_index,
                        "tick": record["tick"],
                        "secondsAt60Hz": round(record["tick"] / 60, 6),
                        "wrapperCategory": wrapper_category,
                        "wrapperIndex": wrapper_index,
                        "packetType": packet_type,
                        "packetName": packet_name,
                        "semanticCategory": category,
                        "semanticCategoryStatus": CATEGORY_STATUS,
                        "semanticCategoryRule": category_rule,
                        "wireStatus": "decoded-exact-wire",
                        "fields": privacy_safe(value),
                    }
                    event_file.write(json.dumps(event, ensure_ascii=False) + "\n")

    if exact_counts != packet_counts:
        raise ValueError("one or more packet payloads did not decode exactly")
    events_temp_path.replace(events_path)

    packet_catalog = []
    for packet_type in sorted(packet_counts):
        packet_name = packet_names[packet_type]
        category, category_rule = packet_categories[packet_type]
        packet_catalog.append(
            {
                "packetType": packet_type,
                "name": packet_name,
                "semanticCategory": category,
                "semanticCategoryStatus": CATEGORY_STATUS,
                "semanticCategoryRule": category_rule,
                "payloadCount": packet_counts[packet_type],
                "exactCount": exact_counts[packet_type],
                "emittedEventCount": emitted_counts[packet_type],
                "wireStatus": "decoded-exact-all",
                "directFieldCount": len(decoder.members_of(packet_name)),
            }
        )

    catalog = {
        "sourceFile": str(replay_path),
        "sourceSha256": hashlib.sha256(data).hexdigest(),
        "clientVersion": client_version,
        "deltaRecordCount": len(delta_records),
        "packetPayloadCount": total_payloads,
        "packetTypeCount": len(packet_catalog),
        "observedSchemaTypeCount": len(decoder.type_reads),
        "observedFieldDefinitionCount": len(decoder.field_reads),
        "wireOverrides": dict(sorted(decoder.wire_overrides.items())),
        "exactPacketTypeCount": sum(
            exact_counts[item["packetType"]] == packet_counts[item["packetType"]]
            for item in packet_catalog
        ),
        "semanticCategoryStatus": CATEGORY_STATUS,
        "semanticCategories": [
            {
                "name": category,
                "packetTypeCount": len(category_packet_types[category]),
                "payloadCount": category_counts[category],
            }
            for category in CATEGORIES
        ],
        "eventExport": {
            "path": str(events_path),
            "format": "ndjson",
            "includedCategories": sorted(included_categories),
            "eventCount": emitted_total,
            "stringPolicy": "redacted-type-and-length",
            "byteArrayPolicy": "length-and-sha256",
        },
        "packetCatalog": packet_catalog,
        "limitations": [
            "Semantic categories are derived from packet names and are not wire truth.",
            "Exact decoding preserves raw enum/code numbers; game-data labels are not assigned here.",
            "Nested byte[] values are not recursively decoded by this exporter.",
            "The held replay proves observed client 12.2.0 payloads, not absent modes or versions.",
        ],
    }
    catalog_path.parent.mkdir(parents=True, exist_ok=True)
    catalog_path.write_text(
        json.dumps(catalog, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(
        json.dumps(
            {
                "catalog": str(catalog_path),
                "events": str(events_path),
                "packetPayloads": total_payloads,
                "packetTypes": len(packet_catalog),
                "categories": dict(category_counts),
                "emittedEvents": emitted_total,
                "includedCategories": sorted(included_categories),
            },
            ensure_ascii=True,
        )
    )


if __name__ == "__main__":
    main()
