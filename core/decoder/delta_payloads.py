"""Strict, definition-aware MemoryPack reader for replay command payloads.

The reader has no skip/guess fallback.  Unknown wire types raise an error, and
callers must require exact cursor consumption before treating a payload as
decoded.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
import json
import math
from pathlib import Path
import struct
from typing import Any


SCHEMA_PATH = Path(__file__).resolve().parent.parent / "schema" / "schema.json"
_NO_FIXED_COLLECTION = object()


class DecodeError(ValueError):
    pass


@dataclass(frozen=True)
class Member:
    order: int
    field_type: str
    name: str
    declaring_type: str


class Cursor:
    # Payload formats are drawn from a small, fixed set.  Constructing a
    # Struct and calculating its size for every scalar read was measurable in
    # large replays (millions of calls).  The cache is immutable after warmup
    # and keyed only by the caller's format string.
    _STRUCT_CACHE: dict[str, struct.Struct] = {}

    def __init__(self, data: bytes):
        self.data = data
        self.offset = 0
        self._length = len(data)

    def require(self, size: int) -> None:
        if size < 0 or self.offset + size > self._length:
            raise DecodeError(
                f"read past payload boundary at {self.offset}: "
                f"need {size}, length {self._length}"
            )

    def raw(self, size: int) -> bytes:
        self.require(size)
        value = self.data[self.offset:self.offset + size]
        self.offset += size
        return value

    def unpack(self, fmt: str):
        unpacker = self._STRUCT_CACHE.get(fmt)
        if unpacker is None:
            unpacker = struct.Struct("<" + fmt)
            self._STRUCT_CACHE[fmt] = unpacker
        size = unpacker.size
        self.require(size)
        values = unpacker.unpack_from(self.data, self.offset)
        self.offset += size
        return values[0] if len(values) == 1 else list(values)


def split_generic(value: str) -> tuple[str, str]:
    depth = 0
    for index, char in enumerate(value):
        if char == "<":
            depth += 1
        elif char == ">":
            depth -= 1
        elif char == "," and depth == 0:
            return value[:index], value[index + 1:]
    raise DecodeError(f"cannot split generic arguments: {value}")


def align_up(value: int, alignment: int) -> int:
    return (value + alignment - 1) // alignment * alignment


class SchemaDecoder:
    primitive_formats = {
        "bool": "B",
        "byte": "B",
        "sbyte": "b",
        "short": "h",
        "ushort": "H",
        "char": "H",
        "int": "i",
        "uint": "I",
        "long": "q",
        "ulong": "Q",
        "float": "f",
        "double": "d",
    }
    struct_formats = {
        "Vector2Int": "ii",
        "Vector2": "ff",
        "Vector3": "fff",
        "Vector3Int": "iii",
        "Quaternion": "ffff",
        "Color": "ffff",
        "Color32": "BBBB",
    }

    def __init__(
        self,
        base_types: dict[str, str | None],
        schema_path: Path | None = None,
        *,
        client_version: str | None = None,
    ):
        if schema_path is None:
            if __package__:
                from .replay_schema_inputs import schema_path_for_version
            else:
                from replay_schema_inputs import schema_path_for_version
            schema_path = schema_path_for_version(client_version)
        schema = json.loads(schema_path.read_text(encoding="utf-8"))
        self.classes = schema["classes"]
        self.enums = schema["enums"]
        self.unions = schema.get("unions", {}) if client_version == "12.4.0" else {}
        self.base_types = dict(base_types)
        # Definitions belong to this replay/decoder. Cache layouts only within
        # this instance; return list copies so callers cannot change the plan.
        self._member_layouts: dict[str, tuple[Member, ...]] = {}
        self._wire_layouts: dict[tuple[str | None, str], tuple[Member, ...]] = {}
        self._unmanaged_layout_cache: dict[str, tuple[int, int]] = {}
        self._type_plans: dict[str, tuple[str, object]] = {}
        self._fixed_object_plans = {}
        self.client_version = client_version
        self.type_reads = Counter()
        self.field_reads = Counter()
        self.wire_overrides = Counter()
        self.last_snapshot_wrapper_summary: dict[str, Any] | None = None

    def next_base(self, type_name: str) -> str | None:
        # The definitions block is replay-version specific.  Prefer it when it
        # names a base for the current type; schema.json can come from a newer
        # client and, for example, 12.1 rotation packets have different bases
        # from 12.2.
        definition_base = self.base_types.get(type_name)
        if definition_base:
            return definition_base
        schema_type = self.classes.get(type_name)
        if schema_type and schema_type.get("base"):
            return schema_type["base"]
        return definition_base

    def supports_object_type(self, type_name: str) -> bool:
        if type_name in self.classes:
            return True
        return (
            self.client_version == "12.3.0"
            and type_name == "CmdPlayStateSkillAction"
            and type_name in self.base_types
        )

    def members_of(self, type_name: str) -> list[Member]:
        if type_name not in self._member_layouts:
            self._member_layouts[type_name] = tuple(self._build_members(type_name))
        return list(self._member_layouts[type_name])

    def _build_members(self, type_name: str) -> list[Member]:
        chain = []
        current = type_name
        seen = set()
        while current:
            if current in seen:
                raise DecodeError(f"inheritance cycle at {current}")
            seen.add(current)
            chain.append(current)
            current = self.next_base(current)

        members: dict[int, Member] = {}
        for declaring_type in reversed(chain):
            schema_type = self.classes.get(declaring_type)
            if not schema_type:
                continue
            declared_members = list(schema_type.get("members", []))
            order_shift = 0
            if declaring_type == type_name and declared_members and members:
                schema_base = schema_type.get("base")
                definition_base = self.base_types.get(type_name)
                declared_orders = {order for order, _, _ in declared_members}
                if (
                    definition_base
                    and definition_base != schema_base
                    and declared_orders.intersection(members)
                ):
                    # A replay-version definition can replace the schema's
                    # base class with a wider one.  Preserve the declared
                    # member sequence but move it after the actual base.  The
                    # resulting wire order is still accepted only when every
                    # payload consumes exactly.
                    order_shift = max(members) + 1 - min(declared_orders)
            for order, field_type, name in declared_members:
                order += order_shift
                member = Member(order, field_type, name, declaring_type)
                previous = members.get(order)
                if previous is not None and previous != member:
                    raise DecodeError(
                        f"conflicting member order {order}: {previous} vs {member}"
                    )
                members[order] = member
        return [members[order] for order in sorted(members)]

    def wire_members_of(self, type_name: str) -> list[Member]:
        key = (self.client_version, type_name)
        if key not in self._wire_layouts:
            self._wire_layouts[key] = tuple(self._build_wire_members(type_name))
        return list(self._wire_layouts[key])

    def _wire_members_cached(self, type_name: str) -> tuple[Member, ...]:
        """Return the immutable layout without allocating a list per object."""
        key = (self.client_version, type_name)
        if key not in self._wire_layouts:
            self._wire_layouts[key] = tuple(self._build_wire_members(type_name))
        return self._wire_layouts[key]

    def _build_wire_members(self, type_name: str) -> list[Member]:
        if self.client_version == "12.3.0" and type_name in {
            "CmdPlaySkillActionBase", "CmdPlaySkillAction", "CmdPlaySkillActionWithTargets", "CmdPlayStateSkillAction"
        }:
            # Metadata ede0935bbc00...: the base declares skillId/actionNo,
            # with no casterId. State actions alone append casterId/stateGroup.
            # Target collections are ordinary length-prefixed Lists. Treating
            # their count as actionNo consumed the bytes but corrupted meaning.
            base = self.members_of("CmdPlaySkillActionBase")
            if [m.name for m in base] != ["objectId", "skillId", "casterId", "actionNo"]:
                raise DecodeError("12.3 skill-action schema baseline is not the reviewed layout")
            members = [base[0], base[1], Member(2,"int","actionNo","CmdPlaySkillActionBase")]
            if type_name == "CmdPlayStateSkillAction":
                members += [Member(3,"int","casterId",type_name), Member(4,"int","stateGroup",type_name),
                            Member(5,"List<SkillActionTarget>","targets",type_name)]
            elif type_name == "CmdPlaySkillActionWithTargets":
                members.append(Member(3,"List<SkillActionTarget>","targets",type_name))
            return members
        members = self.members_of(type_name)
        if self.client_version == "12.3.0" and type_name == "StateSkillScriptSnapshot":
            if len(members) != 6 or [member.order for member in members] != list(range(6)):
                raise DecodeError(
                    "12.3 StateSkillScriptSnapshot base shape is not the verified six-member layout"
                )
            members = [
                *members,
                Member(6, "int", "stateGroup", "StateSkillScriptSnapshot"),
            ]
        if self.client_version == "12.3.0" and type_name == "CmdStartStateSkill":
            if len(members) != 5 or [member.order for member in members] != list(range(5)):
                raise DecodeError(
                    "12.3 CmdStartStateSkill base shape is not the verified five-member layout"
                )
            members = [
                *members,
                Member(5, "int", "stateGroup", "CmdStartStateSkill"),
            ]
        if self.client_version == "12.3.0" and type_name == "CmdFinishStateSkill":
            if len(members) != 4 or [member.order for member in members] != list(range(4)):
                raise DecodeError(
                    "12.3 CmdFinishStateSkill base shape is not the verified four-member layout"
                )
            members = [
                *members,
                Member(4, "int", "stateGroup", "CmdFinishStateSkill"),
            ]
        return members

    def read_string(self, cursor: Cursor) -> str | None:
        first = cursor.unpack("i")
        if first == 0:
            return ""
        if first == -1:
            return None
        if first < 0:
            byte_length = ~first
            utf16_length = cursor.unpack("i")
            value = cursor.raw(byte_length).decode("utf-8", "strict")
            if len(value.encode("utf-16-le")) // 2 != utf16_length:
                raise DecodeError(
                    f"string UTF-16 length mismatch at {cursor.offset - byte_length}"
                )
            return value
        return cursor.raw(first * 2).decode("utf-16-le", "strict")

    def unmanaged_layout(self, type_name: str) -> tuple[int, int]:
        cached = self._unmanaged_layout_cache.get(type_name)
        if cached is not None:
            return cached
        if type_name in self.primitive_formats:
            size = struct.calcsize("<" + self.primitive_formats[type_name])
            result = size, min(size, 8)
            self._unmanaged_layout_cache[type_name] = result
            return result
        enum_size = self.enum_size(type_name)
        if enum_size is not None:
            size = enum_size
            result = size, min(size, 8)
            self._unmanaged_layout_cache[type_name] = result
            return result
        if type_name in self.struct_formats:
            size = struct.calcsize("<" + self.struct_formats[type_name])
            alignment = 1 if type_name == "Color32" else 4
            result = size, alignment
            self._unmanaged_layout_cache[type_name] = result
            return result
        raise DecodeError(f"unsupported unmanaged nullable value type {type_name}")

    def enum_size(self, type_name: str) -> int | None:
        cache_key = "enum:" + type_name
        if cache_key in self._type_plans:
            plan = self._type_plans[cache_key]
            return plan[1] if plan[0] == "enum" else None
        size = self.enums.get(type_name)
        if size is not None:
            self._type_plans[cache_key] = ("enum", size)
            return size
        # build_schema.py records nested C# enums by their terminal name while
        # member types retain the declaring-type prefix.  Accept the terminal
        # name only when that exact generated enum key exists.
        if "." in type_name:
            size = self.enums.get(type_name.rsplit(".", 1)[1])
            self._type_plans[cache_key] = ("enum", size) if size is not None else ("other", None)
            return size
        self._type_plans[cache_key] = ("other", None)
        return None

    def _type_plan(self, type_name: str) -> tuple[str, object]:
        """Classify a type once; read() is called millions of times per replay."""
        plan = self._type_plans.get(type_name)
        if plan is not None:
            return plan
        primitive = self.primitive_formats.get(type_name)
        if primitive is not None:
            plan = ("primitive", primitive)
        elif type_name == "byte[]":
            plan = ("byte-array", None)
        elif type_name.endswith("[]"):
            plan = ("array", type_name[:-2])
        elif type_name.startswith("List<") and type_name.endswith(">"):
            plan = ("list", type_name[5:-1])
        elif type_name.startswith("HashSet<") and type_name.endswith(">"):
            plan = ("hash-set", type_name[8:-1])
        elif type_name.startswith("Dictionary<") and type_name.endswith(">"):
            plan = ("dictionary", split_generic(type_name[11:-1]))
        elif type_name.startswith("Nullable<") and type_name.endswith(">"):
            plan = ("nullable", type_name[9:-1])
        elif type_name == "string":
            plan = ("string", None)
        else:
            enum_size = self.enum_size(type_name)
            if enum_size is not None:
                plan = ("enum", enum_size)
            elif type_name in self.struct_formats:
                plan = ("struct", self.struct_formats[type_name])
            elif type_name == "SnapshotWrapper":
                plan = ("snapshot-wrapper", None)
            elif type_name in self.classes:
                plan = ("object", type_name)
            else:
                plan = ("unknown", None)
        self._type_plans[type_name] = plan
        return plan

    def read_nullable(self, cursor: Cursor, value_type: str, depth: int) -> Any:
        value_size, alignment = self.unmanaged_layout(value_type)
        value_offset = align_up(1, alignment)
        total_size = align_up(value_offset + value_size, alignment)
        raw = cursor.raw(total_size)
        has_value = raw[0]
        if has_value not in (0, 1):
            raise DecodeError(f"nullable hasValue byte is {has_value}, expected 0/1")
        if not has_value:
            return None
        nested = Cursor(raw[value_offset:value_offset + value_size])
        value = self.read(nested, value_type, depth + 1)
        if nested.offset != value_size:
            raise DecodeError(f"nullable {value_type} did not consume native value")
        return value

    def read_count(self, cursor: Cursor, label: str, maximum: int = 100_000) -> int | None:
        count = cursor.unpack("i")
        if count == -1:
            return None
        if not 0 <= count <= maximum:
            raise DecodeError(f"implausible {label} count {count}")
        return count

    def _fixed_wire_spec(self, type_name):
        """Exact fixed-width wire types only; variable/nullable types stay strict."""
        fmt = self.primitive_formats.get(type_name)
        if fmt is not None:
            if len(fmt) != 1 or fmt not in "bBhHiIqQfd":
                return None
            return fmt, False, type_name == "bool"
        if (type_name == "string" or type_name.endswith("[]")
                or any(type_name.startswith(p) for p in ("List<", "HashSet<", "Dictionary<", "Nullable<"))):
            return None
        size = self.enum_size(type_name)
        if size is not None:
            fmt = {1: "B", 2: "H", 4: "I", 8: "Q"}.get(size)
            return (fmt, False, False) if fmt is not None else None
        fmt = self.struct_formats.get(type_name)
        if fmt and all(c in "bBhHiIqQfd" for c in fmt):
            return fmt, True, False
        return None

    def _can_batch_fixed(self, cursor, depth):
        # Preserve extension readers and boundary/depth error behavior.
        return (depth < 128 and type(cursor) is Cursor
                and type(self).read is SchemaDecoder.read and "read" not in self.__dict__)

    def _fixed_object_plan(self, type_name, members, count):
        key = self.client_version, type_name, count
        if key in self._fixed_object_plans:
            return self._fixed_object_plans[key]
        fields = members[:count]
        specs = [self._fixed_wire_spec(m.field_type) for m in fields]
        plan = None
        if all(s is not None for s in specs):
            unpacker = struct.Struct("<" + "".join(s[0] for s in specs))
            keys = tuple((m.declaring_type, m.order, m.name, m.field_type) for m in fields)
            names = tuple(m.name for m in fields)
            scalar = all(not vector for _, vector, _ in specs)
            booleans = tuple(i for i, (_, _, boolean) in enumerate(specs) if boolean)
            plan = unpacker, names, specs, keys, scalar, booleans
        self._fixed_object_plans[key] = plan
        return plan

    def _read_fixed_collection(self, cursor, types, count, depth):
        if not count:
            return []
        if not self._can_batch_fixed(cursor, depth):
            return _NO_FIXED_COLLECTION
        specs = [self._fixed_wire_spec(t) for t in types]
        if any(spec is None for spec in specs):
            return _NO_FIXED_COLLECTION
        fmt = "".join(s[0] for s in specs)
        size = struct.calcsize("<" + fmt) * count
        if cursor.offset + size > cursor._length:
            # Let the original per-value path locate the exact failing element.
            return _NO_FIXED_COLLECTION
        cursor.require(size)
        values = struct.unpack_from("<" + fmt * count, cursor.data, cursor.offset)
        cursor.offset += size
        if len(specs) == 1 and not specs[0][1]:
            return list(map(bool, values)) if specs[0][2] else list(values)
        result = []
        offset = 0
        for _ in range(count):
            parts = []
            for item_fmt, vector, boolean in specs:
                width = len(item_fmt)
                value = list(values[offset:offset + width]) if vector else values[offset]
                parts.append(bool(value) if boolean else value)
                offset += width
            result.append(parts if len(specs) > 1 else parts[0])
        return result

    def read(self, cursor: Cursor, type_name: str, depth: int = 0) -> Any:
        if type_name in self.unions:
            if depth > 128:
                raise DecodeError("union nesting exceeds depth limit")
            tag = cursor.unpack("B")
            if tag == 255:
                return None
            # This exact schema only declares one-byte tags below 250.
            if tag >= 250:
                raise DecodeError(f"unsupported {type_name} union encoding")
            concrete = self.unions[type_name].get(str(tag))
            if concrete not in self.classes or concrete == type_name:
                raise DecodeError(f"unknown {type_name} union tag {tag}")
            return self.read_object(cursor, concrete, depth + 1)
        if depth > 128:
            raise DecodeError("maximum nested depth exceeded")
        # Scalars dominate reads; do not route them through another method and
        # a type-plan tuple on every value.
        primitive = self.primitive_formats.get(type_name)
        if primitive is not None:
            value = cursor.unpack(primitive)
            return bool(value) if type_name == "bool" else value
        kind, detail = self._type_plan(type_name)
        if kind == "primitive":
            value = cursor.unpack(detail)
            return bool(value) if type_name == "bool" else value
        if kind == "byte-array":
            count = self.read_count(cursor, "byte[]", len(cursor.data))
            return None if count is None else cursor.raw(count)
        if kind == "array":
            element_type = detail
            count = self.read_count(cursor, type_name)
            if count is not None and count >= 8:
                fixed = self._read_fixed_collection(cursor, (element_type,), count, depth)
                if fixed is not _NO_FIXED_COLLECTION:
                    return fixed
            return None if count is None else [
                self.read(cursor, element_type, depth + 1) for _ in range(count)
            ]
        if kind == "list" or kind == "hash-set":
            element_type = detail
            count = self.read_count(cursor, type_name)
            if count is not None and count >= 8:
                fixed = self._read_fixed_collection(cursor, (element_type,), count, depth)
                if fixed is not _NO_FIXED_COLLECTION:
                    return fixed
            return None if count is None else [
                self.read(cursor, element_type, depth + 1) for _ in range(count)
            ]
        if kind == "dictionary":
            key_type, value_type = detail
            count = self.read_count(cursor, type_name)
            if count is None:
                return None
            if self.client_version == "12.4.0" and (key_type, value_type) == ("long", "bool"):
                # MemoryPack KeyValuePairFormatter writes reference-free pairs
                # using DangerousWriteUnmanaged. Int64 + bool is a 16-byte
                # aligned pair, not a concatenated 9-byte key and value.
                cursor.require(count * 16)
                result = []
                for _ in range(count):
                    key, value = struct.unpack("<qB7x", cursor.raw(16))
                    if value not in (0, 1):
                        raise DecodeError("invalid 12.4 unmanaged dictionary boolean")
                    result.append([key, bool(value)])
                return result
            if count >= 8:
                fixed = self._read_fixed_collection(cursor, (key_type, value_type), count, depth)
                if fixed is not _NO_FIXED_COLLECTION:
                    return fixed
            return [
                [
                    self.read(cursor, key_type, depth + 1),
                    self.read(cursor, value_type, depth + 1),
                ]
                for _ in range(count)
            ]
        if kind == "nullable":
            return self.read_nullable(cursor, detail, depth)
        if kind == "string":
            return self.read_string(cursor)
        if kind == "enum":
            size = detail
            return int.from_bytes(cursor.raw(size), "little", signed=False)
        if kind == "struct":
            return cursor.unpack(detail)
        if kind == "snapshot-wrapper":
            return self.read_snapshot_wrapper(cursor, depth)
        if kind == "object":
            return self.read_object(cursor, type_name, depth)
        raise DecodeError(f"unknown type {type_name} at {cursor.offset}")

    def read_object(self, cursor: Cursor, type_name: str, depth: int = 0) -> Any:
        member_count = cursor.unpack("B")
        if member_count == 0xFF:
            return None
        members = self._wire_members_cached(type_name)
        if self.client_version == "12.3.0" and type_name == "StateSkillScriptSnapshot":
            self.wire_overrides[
                "StateSkillScriptSnapshot:12.3-appended-int-stateGroup"
            ] += 1
        if self.client_version == "12.3.0" and type_name == "CmdStartStateSkill":
            self.wire_overrides[
                "CmdStartStateSkill:12.3-appended-int-stateGroup"
            ] += 1
        if self.client_version == "12.3.0" and type_name == "CmdFinishStateSkill":
            self.wire_overrides[
                "CmdFinishStateSkill:12.3-appended-int-stateGroup"
            ] += 1
        if self.client_version == "12.3.0" and type_name == "CmdPlayStateSkillAction":
            self.wire_overrides[
                "CmdPlayStateSkillAction:12.3-metadata-direct-state-fields"
            ] += 1
        if member_count > len(members):
            raise DecodeError(
                f"{type_name} header {member_count} exceeds {len(members)} schema members"
            )
        self.type_reads[type_name] += 1
        values: dict[str, Any] = {"__type": type_name}
        plan = self._fixed_object_plan(type_name, members, member_count) if member_count >= 3 else None
        if (plan is not None and self._can_batch_fixed(cursor, depth)
                and cursor.offset + plan[0].size <= cursor._length):
            unpacker, names, specs, keys, scalar, booleans = plan
            cursor.require(unpacker.size)
            unpacked = unpacker.unpack_from(cursor.data, cursor.offset)
            cursor.offset += unpacker.size
            self.field_reads.update(keys)
            if scalar:
                if booleans:
                    unpacked = list(unpacked)
                    for index in booleans:
                        unpacked[index] = bool(unpacked[index])
                values.update(zip(names, unpacked))
            else:
                offset = 0
                for name, (fmt, vector, boolean) in zip(names, specs):
                    width = len(fmt)
                    value = list(unpacked[offset:offset + width]) if vector else unpacked[offset]
                    values[name] = bool(value) if boolean else value
                    offset += width
            return values
        for member in members[:member_count]:
            member_offset = cursor.offset
            self.field_reads[
                (
                    member.declaring_type,
                    member.order,
                    member.name,
                    member.field_type,
                )
            ] += 1
            try:
                values[member.name] = self.read(
                    cursor, member.field_type, depth + 1
                )
            except DecodeError as error:
                diagnostic = ""
                if self.client_version == "12.3.0" and type_name == "UserSnapshot":
                    prior_shape = {
                        "memberCount": member_count,
                        "characterObjectId": (
                            values.get("characterSnapshot", {}).get("objectId")
                            if isinstance(values.get("characterSnapshot"), dict)
                            else None
                        ),
                        "playerSnapshotBytes": (
                            len(values["playerSnapshot"])
                            if isinstance(values.get("playerSnapshot"), bytes)
                            else None
                        ),
                        "equipCount": (
                            len(values["equips"])
                            if isinstance(values.get("equips"), list)
                            else None
                        ),
                        "walkableNavMask": values.get("walkableNavMask"),
                        "exp": values.get("exp"),
                        "survivalTime": values.get("survivalTime"),
                        "characterWrapper": self.last_snapshot_wrapper_summary,
                    }
                    diagnostic = (
                        f" at {member_offset}, previous12-next16="
                        f"{cursor.data[max(0, member_offset - 12):member_offset + 16].hex()}, "
                        f"priorShape={prior_shape}"
                    )
                raise DecodeError(
                    f"{error} while reading {type_name}.{member.name}{diagnostic}"
                ) from error
        return values

    def read_snapshot_wrapper(self, cursor: Cursor, depth: int) -> Any:
        if self.client_version == "12.4.0":
            expected = [
                (0, "ObjectType", "objectType"), (1, "int", "objectId"),
                (2, "InWorldType", "inWorldType"), (3, "byte[]", "snapshot"),
                (4, "Vector2", "positionXZ"), (5, "int", "positionY"),
                (6, "uint", "blisLiteRotation"),
            ]
            actual = [(m.order, m.field_type, m.name) for m in self.members_of("SnapshotWrapper")]
            if actual != expected or self.enums.get("InWorldType") != 4 or self.enums.get("ObjectType") != 4:
                raise DecodeError("exact 12.4 SnapshotWrapper schema mismatch")
            cursor.require(1)
            if cursor.data[cursor.offset] not in (7, 0xFF):
                raise DecodeError("exact 12.4 SnapshotWrapper header mismatch")
            return self.read_object(cursor, "SnapshotWrapper", depth)
        cursor.require(1)
        header = cursor.data[cursor.offset]
        if header in (0, 1):
            # MemoryPack union tag, observed on command payload fields.
            cursor.offset += 1
            concrete = "SnapshotWrapperBasic" if header == 0 else "SnapshotWrapperFull"
            return self.read_object(cursor, concrete, depth)
        if header == 4:
            return self.read_object(cursor, "SnapshotWrapperBasic", depth)
        if header == 7:
            # Some replay versions serialize the concrete full wrapper
            # directly. Held 12.1 uses derived position members before the
            # base inWorldType/snapshot members. Exact 12.3 command samples
            # instead use base-first order, widen InWorldType to int32, and
            # append the three position/rotation members after the byte[].
            cursor.offset += 1
            type_name = "SnapshotWrapperFull"
            members = {member.name: member for member in self.members_of(type_name)}
            wire_order = (
                (
                    "objectType",
                    "objectId",
                    "inWorldType",
                    "snapshot",
                    "positionXZ",
                    "positionY",
                    "blisLiteRotation",
                )
                if self.client_version == "12.3.0"
                else (
                    "objectType",
                    "objectId",
                    "positionXZ",
                    "positionY",
                    "blisLiteRotation",
                    "inWorldType",
                    "snapshot",
                )
            )
            self.type_reads[type_name] += 1
            values: dict[str, Any] = {"__type": type_name}
            for name in wire_order:
                member = members[name]
                self.field_reads[
                    (
                        member.declaring_type,
                        member.order,
                        member.name,
                        member.field_type,
                    )
                ] += 1
                if name == "inWorldType" and self.client_version == "12.3.0":
                    values[name] = cursor.unpack("i")
                    self.wire_overrides[
                        "SnapshotWrapperFull.inWorldType:12.3-int32"
                    ] += 1
                elif name == "positionY" and self.client_version != "12.3.0":
                    position_y_members = cursor.unpack("B")
                    if position_y_members != 1:
                        raise DecodeError(
                            "legacy SnapshotWrapperFull positionY header "
                            f"{position_y_members}, expected 1"
                        )
                    values[name] = cursor.unpack("i")
                    self.wire_overrides[
                        "SnapshotWrapperFull.positionY:legacy-BlisFixedPoint-object"
                    ] += 1
                else:
                    values[name] = self.read(
                        cursor, member.field_type, depth + 1
                    )
            self.last_snapshot_wrapper_summary = {
                "objectType": values.get("objectType"),
                "objectId": values.get("objectId"),
                "positionY": values.get("positionY"),
                "rotation": values.get("blisLiteRotation"),
                "inWorldType": values.get("inWorldType"),
                "snapshotBytes": (
                    len(values["snapshot"])
                    if isinstance(values.get("snapshot"), bytes)
                    else None
                ),
            }
            return values
        if header == 0xFF:
            cursor.offset += 1
            return None
        raise DecodeError(f"unsupported SnapshotWrapper concrete header {header}")

    def decode_exact(self, payload: bytes, type_name: str) -> dict[str, Any]:
        cursor = Cursor(payload)
        value = self.read(cursor, type_name) if type_name in self.unions else self.read_object(cursor, type_name)
        if cursor.offset != len(payload):
            raise DecodeError(
                f"{type_name} ended at {cursor.offset}, payload length {len(payload)}"
            )
        return value


def safe_sample(value: Any, depth: int = 0) -> Any:
    """Return a bounded, string-redacted sample suitable for an audit report."""
    if depth > 5:
        return "<depth-limit>"
    if isinstance(value, str):
        return {"type": "string", "length": len(value)}
    if isinstance(value, bytes):
        return {"type": "bytes", "length": len(value)}
    if isinstance(value, list):
        return {
            "type": "list",
            "length": len(value),
            "sample": [safe_sample(item, depth + 1) for item in value[:3]],
        }
    if isinstance(value, dict):
        return {
            key: safe_sample(item, depth + 1)
            for key, item in value.items()
        }
    if isinstance(value, float) and not math.isfinite(value):
        return str(value)
    return value
