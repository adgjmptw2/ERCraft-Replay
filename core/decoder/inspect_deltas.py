"""Inventory the wrapper layer of Eternal Return kind=1 replay records.

This decoder stops at the command payload boundary except for two movement
commands whose payload shape is proven by the held schema and exact byte size:
CmdWarpTo and CmdStopMove.  It never scans for coincidental byte signatures;
every value is reached by advancing a MemoryPack cursor.
"""

from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import gzip
import hashlib
import json
import math
from pathlib import Path
import struct

import brotli


HEADER_OFFSET = 0x410
RECORD_HEADER = struct.Struct("<HHIII")


class Cursor:
    def __init__(self, data: bytes):
        self.data = data
        self.offset = 0

    def require(self, size: int) -> None:
        if size < 0 or self.offset + size > len(self.data):
            raise ValueError(
                f"read past payload boundary at {self.offset}: "
                f"need {size}, length {len(self.data)}"
            )

    def u8(self) -> int:
        self.require(1)
        value = self.data[self.offset]
        self.offset += 1
        return value

    def i32(self) -> int:
        self.require(4)
        value = struct.unpack_from("<i", self.data, self.offset)[0]
        self.offset += 4
        return value

    def u32(self) -> int:
        self.require(4)
        value = struct.unpack_from("<I", self.data, self.offset)[0]
        self.offset += 4
        return value

    def i64(self) -> int:
        self.require(8)
        value = struct.unpack_from("<q", self.data, self.offset)[0]
        self.offset += 8
        return value

    def bytes(self, size: int) -> bytes:
        self.require(size)
        value = self.data[self.offset:self.offset + size]
        self.offset += size
        return value


def read_collection_count(cursor: Cursor, label: str, maximum: int = 100_000) -> int | None:
    count = cursor.i32()
    if count == -1:
        return None
    if not 0 <= count <= maximum:
        raise ValueError(f"implausible {label} count {count} at {cursor.offset - 4}")
    return count


def read_byte_array(cursor: Cursor) -> bytes | None:
    length = cursor.i32()
    if length == -1:
        return None
    if not 0 <= length <= len(cursor.data) - cursor.offset:
        raise ValueError(f"invalid byte[] length {length} at {cursor.offset - 4}")
    return cursor.bytes(length)


def read_packet_wrapper(cursor: Cursor, wrapper_type: str) -> dict:
    expected_members = {
        "ReplayPacketWrapper": 3,
        "PacketWrapper": 2,
        "ClientPacketWrapper": 3,
    }[wrapper_type]
    members = cursor.u8()
    if members != expected_members:
        raise ValueError(
            f"{wrapper_type} member header {members}, expected {expected_members}, "
            f"at {cursor.offset - 1}"
        )
    packet_type = cursor.i32()
    payload = read_byte_array(cursor)
    if wrapper_type == "ReplayPacketWrapper":
        cursor.u32()  # target; retain no account- or client-specific routing data
    elif wrapper_type == "ClientPacketWrapper":
        cursor.i64()  # userId; deliberately not emitted
    return {"packetType": packet_type, "payload": payload}


def read_wrapper_list(cursor: Cursor, wrapper_type: str, label: str) -> list[dict]:
    count = read_collection_count(cursor, label)
    if count is None:
        return []
    return [read_packet_wrapper(cursor, wrapper_type) for _ in range(count)]


def parse_delta_payload(compressed: bytes, expected_tick: int) -> dict:
    raw = brotli.decompress(compressed)
    cursor = Cursor(raw)
    members = cursor.u8()
    if members != 5:
        raise ValueError(f"ReplayPacketList member header {members}, expected 5")
    seq = cursor.i32()
    if seq != expected_tick:
        raise ValueError(f"ReplayPacketList seq {seq} != record tick {expected_tick}")

    ignore_order = read_wrapper_list(
        cursor, "ReplayPacketWrapper", "ignoreOrderPackets"
    )
    commands = read_wrapper_list(cursor, "ReplayPacketWrapper", "commands")

    item_box_count = read_collection_count(cursor, "itemBoxPackets")
    item_box_packets: list[dict] = []
    if item_box_count is not None:
        for _ in range(item_box_count):
            item_box_object_id = cursor.i32()
            wrappers = read_wrapper_list(
                cursor, "PacketWrapper", "itemBoxPackets[value]"
            )
            for wrapper in wrappers:
                wrapper["itemBoxObjectId"] = item_box_object_id
            item_box_packets.extend(wrappers)

    client_packets = read_wrapper_list(
        cursor, "ClientPacketWrapper", "clientPackets"
    )
    if cursor.offset != len(raw):
        raise ValueError(
            f"delta cursor ended at {cursor.offset}, decompressed length {len(raw)}"
        )
    return {
        "seq": seq,
        "decompressedBytes": len(raw),
        "ignoreOrderPackets": ignore_order,
        "commands": commands,
        "itemBoxPackets": item_box_packets,
        "clientPackets": client_packets,
    }


def iter_records(data: bytes):
    offset = HEADER_OFFSET
    while offset + RECORD_HEADER.size <= len(data):
        kind, version, tick, length, aux = RECORD_HEADER.unpack_from(data, offset)
        payload_offset = offset + RECORD_HEADER.size
        payload_end = payload_offset + length
        if payload_end > len(data):
            raise ValueError(f"record at {offset} extends past EOF")
        yield {
            "kind": kind,
            "version": version,
            "tick": tick,
            "length": length,
            "aux": aux,
            "payload": data[payload_offset:payload_end],
        }
        offset = payload_end
    if offset != len(data):
        raise ValueError(f"unframed trailing bytes: {len(data) - offset}")


def load_definitions(records: list[dict]) -> tuple[list[dict], dict[int, str]]:
    blocks = [
        record for record in records
        if record["kind"] == 3 and record["version"] == 2
    ]
    if len(blocks) != 1:
        raise ValueError(f"expected one definitions block, found {len(blocks)}")
    body = json.loads(gzip.decompress(blocks[0]["payload"]))
    definitions = body.get("definitions")
    if not isinstance(definitions, list):
        raise ValueError("definitions block has no definitions list")
    names: dict[int, str] = {}
    for definition in definitions:
        packet_type = definition.get("packetType")
        name = definition.get("packetTypeName") or definition.get("name")
        if isinstance(packet_type, int) and packet_type > 0 and isinstance(name, str):
            previous = names.get(packet_type)
            if previous is not None and previous != name:
                raise ValueError(
                    f"packet type {packet_type} has conflicting names "
                    f"{previous!r} and {name!r}"
                )
            names[packet_type] = name
    return definitions, names


def parse_position_command(payload: bytes | None) -> dict | None:
    if payload is None or len(payload) != 13 or payload[0] != 2:
        return None
    object_id, world_x, world_z = struct.unpack_from("<iff", payload, 1)
    if object_id <= 0 or not math.isfinite(world_x) or not math.isfinite(world_z):
        return None
    return {
        "objectId": object_id,
        "worldX": round(world_x, 5),
        "worldZ": round(world_z, 5),
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("replay", type=Path)
    parser.add_argument("--out", type=Path)
    parser.add_argument("--sample-limit", type=int, default=12)
    args = parser.parse_args()

    replay_path = args.replay.resolve()
    output_path = (
        args.out or replay_path.with_suffix(".deltas.inspect.json")
    ).resolve()
    data = replay_path.read_bytes()
    if data[:16] != b"EternalReturnV1\0":
        raise ValueError("not an EternalReturnV1 replay")
    client_version = data[16:32].split(b"\0", 1)[0].decode("ascii")
    records = list(iter_records(data))
    definitions, packet_names = load_definitions(records)
    delta_records = [
        record for record in records
        if record["kind"] == 1 and record["version"] == 1
    ]
    if not delta_records:
        raise ValueError("no kind=1 version=1 delta records")

    category_counts = Counter()
    type_counts: dict[str, Counter] = defaultdict(Counter)
    payload_lengths: dict[int, Counter] = defaultdict(Counter)
    payload_headers: dict[int, Counter] = defaultdict(Counter)
    movement_samples: dict[int, list[dict]] = {228: [], 229: []}
    movement_invalid = Counter()
    decompressed_total = 0

    for record in delta_records:
        parsed = parse_delta_payload(record["payload"], record["tick"])
        decompressed_total += parsed["decompressedBytes"]
        for category in (
            "ignoreOrderPackets", "commands", "itemBoxPackets", "clientPackets"
        ):
            wrappers = parsed[category]
            category_counts[category] += len(wrappers)
            for wrapper in wrappers:
                packet_type = wrapper["packetType"]
                payload = wrapper["payload"]
                type_counts[category][packet_type] += 1
                payload_lengths[packet_type][
                    -1 if payload is None else len(payload)
                ] += 1
                if payload:
                    payload_headers[packet_type][payload[0]] += 1
                if packet_type in movement_samples:
                    position = parse_position_command(payload)
                    if position is None:
                        movement_invalid[packet_type] += 1
                    elif len(movement_samples[packet_type]) < args.sample_limit:
                        movement_samples[packet_type].append(
                            {"tick": record["tick"], **position}
                        )

    all_packet_types = sorted(
        set(packet_type for counts in type_counts.values() for packet_type in counts)
    )
    packet_inventory = []
    for packet_type in all_packet_types:
        packet_inventory.append(
            {
                "packetType": packet_type,
                "name": packet_names.get(packet_type),
                "countsByCategory": {
                    category: counts[packet_type]
                    for category, counts in type_counts.items()
                    if counts[packet_type]
                },
                "payloadLengthCounts": dict(
                    sorted(payload_lengths[packet_type].items())
                ),
                "payloadMemberHeaderCounts": dict(
                    sorted(payload_headers[packet_type].items())
                ),
            }
        )

    movement = {}
    for packet_type in (228, 229):
        movement[str(packet_type)] = {
            "name": packet_names.get(packet_type),
            "schema": (
                "MemoryPack object header=2, inherited objectId<int32>, "
                "destinationVector2<Vector2>"
                if packet_type == 228 else
                "MemoryPack object header=2, inherited objectId<int32>, "
                "positionVector2<Vector2>"
            ),
            "validExactPayloads": sum(
                counts[packet_type] for counts in type_counts.values()
            ) - movement_invalid[packet_type],
            "invalidPayloads": movement_invalid[packet_type],
            "samples": movement_samples[packet_type],
        }

    report = {
        "sourceFile": str(replay_path),
        "sha256": hashlib.sha256(data).hexdigest(),
        "clientVersion": client_version,
        "definitions": {
            "encoding": "gzip-compressed JSON",
            "count": len(definitions),
            "namedPacketTypes": len(packet_names),
        },
        "deltaStructure": {
            "encoding": "Brotli-compressed MemoryPack",
            "topType": "ReplayPacketList",
            "fields": [
                "seq<int32>",
                "ignoreOrderPackets<List<ReplayPacketWrapper>>",
                "commands<List<ReplayPacketWrapper>>",
                "itemBoxPackets<Dictionary<int,List<PacketWrapper>>>",
                "clientPackets<List<ClientPacketWrapper>>",
            ],
            "wrapperType": "ReplayPacketWrapper",
            "wrapperFields": [
                "packetType<int32>", "data<byte[]>", "target<uint32>"
            ],
            "exactBoundaryRecords": len(delta_records),
        },
        "recordCount": len(delta_records),
        "compressedBytes": sum(record["length"] for record in delta_records),
        "decompressedBytes": decompressed_total,
        "packetCountsByCategory": dict(category_counts),
        "movementCommands": movement,
        "packetInventory": packet_inventory,
        "limitations": [
            "Only ReplayPacketList and packet-wrapper boundaries are decoded generally.",
            "CmdWarpTo and CmdStopMove are the only inner payloads decoded here.",
            "Definitions name packet types; they do not prove every inner command schema.",
            "No client userId or target-routing values are emitted.",
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
                "clientVersion": client_version,
                "deltaRecords": len(delta_records),
                "packetTypes": len(packet_inventory),
                "packetCountsByCategory": dict(category_counts),
                "movement": {
                    key: {
                        "name": value["name"],
                        "valid": value["validExactPayloads"],
                        "invalid": value["invalidPayloads"],
                    }
                    for key, value in movement.items()
                },
            },
            ensure_ascii=True,
        )
    )


if __name__ == "__main__":
    main()
