"""Read the small IL2CPP metadata-v38+ subset needed for enum inspection.

This is intentionally not a general Il2CppDumper replacement.  It reads the
sectioned metadata header, type definitions, fields, and field default values
without touching GameAssembly.dll or the installed game files.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import struct


SANITY = 0xFAB11BAF
SECTION_NAMES_V38 = (
    "stringLiterals",
    "stringLiteralData",
    "strings",
    "events",
    "properties",
    "methods",
    "parameterDefaultValues",
    "fieldDefaultValues",
    "fieldAndParameterDefaultValueData",
    "fieldMarshaledSizes",
    "parameters",
    "fields",
    "genericParameters",
    "genericParameterConstraints",
    "genericContainers",
    "nestedTypes",
    "interfaces",
    "vtableMethods",
    "interfaceOffsets",
    "typeDefinitions",
    "images",
    "assemblies",
    "fieldRefs",
    "referencedAssemblies",
    "attributeData",
    "attributeDataRanges",
    "unresolvedIndirectCallParameterTypes",
    "unresolvedIndirectCallParameterRanges",
    "windowsRuntimeTypeNames",
    "windowsRuntimeStrings",
    "exportedTypeDefinitions",
)
SOURCE_REVISION = "7c4a19426e2c17cfaa6e0a5b37f35869a93fad21"
SOURCE_URLS = (
    "https://github.com/Perfare/Il2CppDumper/blob/"
    f"{SOURCE_REVISION}/Il2CppDumper/Il2Cpp/Metadata.cs",
    "https://github.com/Perfare/Il2CppDumper/blob/"
    f"{SOURCE_REVISION}/Il2CppDumper/Il2Cpp/MetadataClass.cs",
)


class MetadataError(ValueError):
    pass


def index_size(count: int) -> int:
    if count < 0xFF:
        return 1
    if count < 0xFFFF:
        return 2
    return 4


def unpack_index(data: bytes, offset: int, size: int) -> tuple[int, int]:
    if size == 4:
        return struct.unpack_from("<i", data, offset)[0], offset + 4
    if size == 2:
        value = struct.unpack_from("<H", data, offset)[0]
        return (-1 if value == 0xFFFF else value), offset + 2
    if size == 1:
        value = data[offset]
        return (-1 if value == 0xFF else value), offset + 1
    raise MetadataError(f"unsupported metadata index size {size}")


def read_compressed_uint32(
    data: bytes, offset: int, limit: int
) -> tuple[int, int]:
    if offset >= limit:
        raise MetadataError("compressed integer is truncated")
    first = data[offset]
    offset += 1
    if first & 0x80 == 0:
        return first, offset
    if first & 0xC0 == 0x80:
        if offset + 1 > limit:
            raise MetadataError("two-byte compressed integer is truncated")
        return ((first & 0x7F) << 8) | data[offset], offset + 1
    if first & 0xE0 == 0xC0:
        if offset + 3 > limit:
            raise MetadataError("four-byte compressed integer is truncated")
        value = (first & 0x3F) << 24
        value |= data[offset] << 16
        value |= data[offset + 1] << 8
        value |= data[offset + 2]
        return value, offset + 3
    if first == 0xF0:
        if offset + 4 > limit:
            raise MetadataError("five-byte compressed integer is truncated")
        return struct.unpack_from("<I", data, offset)[0], offset + 4
    if first == 0xFE:
        return 0xFFFFFFFE, offset
    if first == 0xFF:
        return 0xFFFFFFFF, offset
    raise MetadataError(f"invalid compressed integer prefix 0x{first:02x}")


def read_compressed_int32(
    data: bytes, offset: int, limit: int
) -> tuple[int, int]:
    encoded, end = read_compressed_uint32(data, offset, limit)
    if encoded == 0xFFFFFFFF:
        return -0x80000000, end
    is_negative = bool(encoded & 1)
    magnitude = encoded >> 1
    return (-(magnitude + 1) if is_negative else magnitude), end


def read_sections(data: bytes) -> tuple[int, dict[str, dict[str, int]]]:
    if len(data) < 8:
        raise MetadataError("metadata header is truncated")
    sanity, version = struct.unpack_from("<Ii", data, 0)
    if sanity != SANITY:
        raise MetadataError(f"bad metadata sanity 0x{sanity:08x}")
    if version < 38 or version >= 104:
        raise MetadataError(
            f"this focused reader supports sectioned metadata 38..103, got {version}"
        )
    header_size = 8 + 12 * len(SECTION_NAMES_V38)
    if len(data) < header_size:
        raise MetadataError("sectioned metadata header is truncated")
    sections = {}
    offset = 8
    for name in SECTION_NAMES_V38:
        section_offset, section_size, count = struct.unpack_from("<iii", data, offset)
        offset += 12
        if section_offset < 0 or section_size < 0 or count < 0:
            raise MetadataError(f"negative bounds in {name}")
        if section_offset + section_size > len(data):
            raise MetadataError(f"{name} extends beyond file")
        sections[name] = {
            "offset": section_offset,
            "sectionSize": section_size,
            "count": count,
            "itemSize": section_size // count if count else 0,
        }
    first_nonempty = min(
        section["offset"] for section in sections.values() if section["sectionSize"]
    )
    if first_nonempty < header_size:
        raise MetadataError(
            f"first section begins at {first_nonempty}, inside {header_size}-byte header"
        )
    return version, sections


def read_string(data: bytes, section: dict[str, int], index: int) -> str:
    if index < 0 or index >= section["sectionSize"]:
        raise MetadataError(f"string index {index} outside string section")
    start = section["offset"] + index
    end_limit = section["offset"] + section["sectionSize"]
    end = data.find(b"\0", start, end_limit)
    if end < 0:
        raise MetadataError(f"unterminated string at index {index}")
    return data[start:end].decode("utf-8")


def read_type_definitions(
    data: bytes,
    sections: dict[str, dict[str, int]],
    widths: dict[str, int],
) -> list[dict]:
    section = sections["typeDefinitions"]
    cursor = section["offset"]
    result = []
    for type_index in range(section["count"]):
        start = cursor
        name_index, namespace_index = struct.unpack_from("<II", data, cursor)
        cursor += 8
        byval_type_index, cursor = unpack_index(data, cursor, widths["typeIndex"])
        declaring_type_index, cursor = unpack_index(data, cursor, widths["typeIndex"])
        parent_index, cursor = unpack_index(data, cursor, widths["typeIndex"])
        generic_container_index, cursor = unpack_index(
            data, cursor, widths["genericContainerIndex"]
        )
        flags = struct.unpack_from("<I", data, cursor)[0]
        cursor += 4
        starts = {}
        for name, width in (
            ("fieldStart", widths["fieldIndex"]),
            ("methodStart", widths["methodIndex"]),
            ("eventStart", widths["eventIndex"]),
            ("propertyStart", widths["propertyIndex"]),
            ("nestedTypesStart", widths["nestedTypeIndex"]),
            ("interfacesStart", widths["interfacesIndex"]),
        ):
            starts[name], cursor = unpack_index(data, cursor, width)
        vtable_start = struct.unpack_from("<i", data, cursor)[0]
        cursor += 4
        interface_offsets_start, cursor = unpack_index(
            data, cursor, widths["interfacesIndex"]
        )
        counts = struct.unpack_from("<8H", data, cursor)
        cursor += 16
        bitfield, token = struct.unpack_from("<II", data, cursor)
        cursor += 8
        result.append(
            {
                "typeIndex": type_index,
                "nameIndex": name_index,
                "namespaceIndex": namespace_index,
                "byvalTypeIndex": byval_type_index,
                "declaringTypeIndex": declaring_type_index,
                "parentIndex": parent_index,
                "genericContainerIndex": generic_container_index,
                "flags": flags,
                **starts,
                "vtableStart": vtable_start,
                "interfaceOffsetsStart": interface_offsets_start,
                "methodCount": counts[0],
                "propertyCount": counts[1],
                "fieldCount": counts[2],
                "eventCount": counts[3],
                "nestedTypeCount": counts[4],
                "vtableCount": counts[5],
                "interfacesCount": counts[6],
                "interfaceOffsetsCount": counts[7],
                "bitfield": bitfield,
                "token": token,
                "isValueType": bool(bitfield & 0x1),
                "isEnum": bool((bitfield >> 1) & 0x1),
                "bytes": cursor - start,
            }
        )
    consumed = cursor - section["offset"]
    if consumed != section["sectionSize"]:
        raise MetadataError(
            f"typeDefinitions consumed {consumed}, section has {section['sectionSize']}"
        )
    return result


def read_fields(
    data: bytes,
    sections: dict[str, dict[str, int]],
    widths: dict[str, int],
) -> list[dict]:
    section = sections["fields"]
    cursor = section["offset"]
    result = []
    for field_index in range(section["count"]):
        name_index = struct.unpack_from("<I", data, cursor)[0]
        cursor += 4
        type_index, cursor = unpack_index(data, cursor, widths["typeIndex"])
        token = struct.unpack_from("<I", data, cursor)[0]
        cursor += 4
        result.append(
            {
                "fieldIndex": field_index,
                "nameIndex": name_index,
                "typeIndex": type_index,
                "token": token,
            }
        )
    consumed = cursor - section["offset"]
    if consumed != section["sectionSize"]:
        raise MetadataError(
            f"fields consumed {consumed}, section has {section['sectionSize']}"
        )
    return result


def read_field_defaults(
    data: bytes,
    sections: dict[str, dict[str, int]],
    widths: dict[str, int],
) -> dict[int, dict]:
    section = sections["fieldDefaultValues"]
    cursor = section["offset"]
    result = {}
    for _ in range(section["count"]):
        field_index, cursor = unpack_index(data, cursor, widths["fieldIndex"])
        type_index, cursor = unpack_index(data, cursor, widths["typeIndex"])
        data_index, cursor = unpack_index(data, cursor, widths["defaultValueDataIndex"])
        result.setdefault(
            field_index,
            {"typeIndex": type_index, "dataIndex": data_index},
        )
    consumed = cursor - section["offset"]
    if consumed != section["sectionSize"]:
        raise MetadataError(
            f"field defaults consumed {consumed}, section has {section['sectionSize']}"
        )
    return result


def inspect_enum(
    data: bytes,
    sections: dict[str, dict[str, int]],
    type_def: dict,
    fields: list[dict],
    defaults: dict[int, dict],
) -> dict:
    string_section = sections["strings"]
    default_section = sections["fieldAndParameterDefaultValueData"]
    enum_fields = []
    start = type_def["fieldStart"]
    end = start + type_def["fieldCount"]
    if start < 0 or end > len(fields):
        raise MetadataError(f"field span {start}:{end} is outside field table")
    default_spans = []
    for field in fields[start:end]:
        default = defaults.get(field["fieldIndex"])
        raw = None
        decoded_value = None
        decoded_width = None
        if default is not None and default["dataIndex"] >= 0:
            value_offset = default_section["offset"] + default["dataIndex"]
            value_limit = default_section["offset"] + default_section["sectionSize"]
            if value_offset < value_limit:
                raw = data[value_offset : min(value_offset + 8, value_limit)]
                decoded_value, decoded_end = read_compressed_int32(
                    data, value_offset, value_limit
                )
                decoded_width = decoded_end - value_offset
                default_spans.append((default["dataIndex"], decoded_width))
        enum_fields.append(
            {
                "fieldIndex": field["fieldIndex"],
                "name": read_string(data, string_section, field["nameIndex"]),
                "typeIndex": field["typeIndex"],
                "token": f"0x{field['token']:08x}",
                "default": (
                    {
                        **default,
                        "rawHexPrefix": raw.hex() if raw is not None else None,
                        "compressedInt32": decoded_value,
                        "compressedBytes": decoded_width,
                    }
                    if default is not None
                    else None
                ),
            }
        )
    sorted_spans = sorted(set(default_spans))
    deltas = [right[0] - left[0] for left, right in zip(sorted_spans, sorted_spans[1:])]
    return {
        "typeIndex": type_def["typeIndex"],
        "namespace": read_string(data, string_section, type_def["namespaceIndex"]),
        "name": read_string(data, string_section, type_def["nameIndex"]),
        "token": f"0x{type_def['token']:08x}",
        "fieldStart": start,
        "fieldCount": type_def["fieldCount"],
        "defaultDataIndexDeltas": sorted(set(deltas)),
        "compressedValueWidths": sorted({width for _, width in default_spans}),
        "numericAssignmentStatus": (
            "decoded-compressed-int32-exact"
            if default_spans
            and all(
                right[0] - left[0] == left[1]
                for left, right in zip(sorted_spans, sorted_spans[1:])
            )
            else "decoded-compressed-int32-unvalidated-stride"
        ),
        "fields": enum_fields,
    }


def inspect_type_fields(
    data: bytes,
    sections: dict[str, dict[str, int]],
    type_def: dict,
    fields: list[dict],
) -> dict:
    string_section = sections["strings"]
    start = type_def["fieldStart"]
    end = start + type_def["fieldCount"]
    if type_def["fieldCount"] == 0:
        selected_fields = []
    elif start < 0 or end > len(fields):
        raise MetadataError(f"field span {start}:{end} is outside field table")
    else:
        selected_fields = fields[start:end]
    return {
        "typeIndex": type_def["typeIndex"],
        "namespace": read_string(data, string_section, type_def["namespaceIndex"]),
        "name": read_string(data, string_section, type_def["nameIndex"]),
        "token": f"0x{type_def['token']:08x}",
        "isValueType": type_def["isValueType"],
        "isEnum": type_def["isEnum"],
        "fieldStart": start,
        "fieldCount": type_def["fieldCount"],
        "fields": [
            {
                "fieldIndex": field["fieldIndex"],
                "name": read_string(data, string_section, field["nameIndex"]),
                "typeIndex": field["typeIndex"],
                "token": f"0x{field['token']:08x}",
            }
            for field in selected_fields
        ],
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("metadata", type=Path)
    parser.add_argument("--type-name", default="ObjectType")
    parser.add_argument("--name-contains")
    parser.add_argument("--out", type=Path)
    args = parser.parse_args()

    metadata_path = args.metadata.resolve()
    data = metadata_path.read_bytes()
    version, sections = read_sections(data)
    actual_interface_pair_size = (
        sections["interfaceOffsets"]["sectionSize"]
        // sections["interfaceOffsets"]["count"]
        if sections["interfaceOffsets"]["count"]
        else 8
    )
    type_index_size = {8: 4, 6: 2, 5: 1}.get(actual_interface_pair_size, 4)
    widths = {
        "typeIndex": type_index_size,
        "typeDefinitionIndex": index_size(sections["typeDefinitions"]["count"]),
        "genericContainerIndex": index_size(sections["genericContainers"]["count"]),
        "parameterIndex": index_size(sections["parameters"]["count"]),
        "eventIndex": 4,
        "interfacesIndex": 4,
        "nestedTypeIndex": 4,
        "propertyIndex": 4,
        "methodIndex": 4,
        "genericParameterIndex": 4,
        "fieldIndex": 4,
        "defaultValueDataIndex": 4,
    }
    type_defs = read_type_definitions(data, sections, widths)
    fields = read_fields(data, sections, widths)
    defaults = read_field_defaults(data, sections, widths)
    string_section = sections["strings"]
    matching = [
        type_def
        for type_def in type_defs
        if type_def["isEnum"]
        and read_string(data, string_section, type_def["nameIndex"])
        == args.type_name
    ]
    fragment = args.name_contains or args.type_name
    matching_types = [
        type_def
        for type_def in type_defs
        if fragment.lower()
        in read_string(data, string_section, type_def["nameIndex"]).lower()
    ]
    enums = [
        inspect_enum(data, sections, type_def, fields, defaults)
        for type_def in matching
    ]
    report = {
        "sourceFile": str(metadata_path),
        "sourceSha256": hashlib.sha256(data).hexdigest(),
        "metadataVersion": version,
        "parserScope": "sectioned-header/type-definitions/fields/field-defaults only",
        "structureSources": list(SOURCE_URLS),
        "indexWidths": widths,
        "sectionSummary": sections,
        "typeDefinitionCount": len(type_defs),
        "fieldCount": len(fields),
        "fieldDefaultCount": len(defaults),
        "queryTypeName": args.type_name,
        "queryNameContains": fragment,
        "matchingEnumCount": len(enums),
        "enums": enums,
        "matchingTypeCount": len(matching_types),
        "matchingTypes": [
            inspect_type_fields(data, sections, type_def, fields)
            for type_def in matching_types
        ],
        "limitations": [
            "compressedInt32 follows the IL2CPP v29+ I4 default-value rule; the script does not independently resolve the default type index from GameAssembly.dll.",
            "This reader intentionally does not parse custom attributes or native code registration.",
            "Metadata version 104+ uses additional compact index widths and is rejected.",
        ],
    }
    output_path = (
        args.out
        or Path.cwd() / f"{metadata_path.name}.{args.type_name}.inspect.json"
    ).resolve()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(
        json.dumps(
            {
                "output": str(output_path),
                "metadataVersion": version,
                "typeDefinitions": len(type_defs),
                "fields": len(fields),
                "matchingEnums": len(enums),
                "matchingTypes": len(matching_types),
                "matches": [f"{item['namespace']}.{item['name']}" for item in enums],
            },
            ensure_ascii=True,
        )
    )


if __name__ == "__main__":
    main()
