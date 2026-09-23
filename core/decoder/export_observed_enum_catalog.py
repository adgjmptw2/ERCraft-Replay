"""Export enum member names for enum types reached by a field inventory."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re

from replay_schema_inputs import schema_path_for_version
from inspect_il2cpp_metadata import (
    index_size,
    inspect_enum,
    read_field_defaults,
    read_fields,
    read_sections,
    read_string,
    read_type_definitions,
    unpack_index,
)


TYPE_TOKEN = re.compile(r"[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)*")


def observed_enum_names(field_report: dict) -> list[str]:
    schema = json.loads(schema_path_for_version(field_report['clientVersion']).read_bytes())
    ENUMS = schema['enums']
    names = set()
    for field in field_report["observedFieldInventory"]:
        for token in TYPE_TOKEN.findall(field["type"]):
            if token in ENUMS or token.rsplit(".", 1)[-1] in ENUMS:
                names.add(token)
    return sorted(names)


def declaring_type_names(
    data: bytes,
    sections: dict[str, dict[str, int]],
    type_defs: list[dict],
    type_definition_index_size: int,
) -> dict[int, set[str]]:
    section = sections["nestedTypes"]
    actual_item_size = section["itemSize"] or type_definition_index_size
    if actual_item_size not in (1, 2, 4):
        raise ValueError(f"unsupported nestedTypes item size {actual_item_size}")
    cursor = section["offset"]
    nested_indices = []
    for _ in range(section["count"]):
        value, cursor = unpack_index(data, cursor, actual_item_size)
        nested_indices.append(value)
    if cursor - section["offset"] != section["sectionSize"]:
        raise ValueError("nestedTypes section was not consumed exactly")
    strings = sections["strings"]
    owners: dict[int, set[str]] = {}
    for outer in type_defs:
        start = outer["nestedTypesStart"]
        count = outer["nestedTypeCount"]
        if not count:
            continue
        if start < 0 or start + count > len(nested_indices):
            raise ValueError(f"invalid nested type span {start}:{start + count}")
        outer_name = read_string(data, strings, outer["nameIndex"])
        for child_index in nested_indices[start : start + count]:
            owners.setdefault(child_index, set()).add(outer_name)
    return owners


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("metadata", type=Path)
    parser.add_argument("field_report", type=Path)
    parser.add_argument("--out", type=Path)
    args = parser.parse_args()

    metadata_path = args.metadata.resolve()
    field_report_path = args.field_report.resolve()
    data = metadata_path.read_bytes()
    field_report = json.loads(field_report_path.read_text(encoding="utf-8"))
    version, sections = read_sections(data)
    interface_pair_size = (
        sections["interfaceOffsets"]["sectionSize"]
        // sections["interfaceOffsets"]["count"]
        if sections["interfaceOffsets"]["count"]
        else 8
    )
    widths = {
        "typeIndex": {8: 4, 6: 2, 5: 1}.get(interface_pair_size, 4),
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
    owners_by_type_index = declaring_type_names(
        data,
        sections,
        type_defs,
        widths["typeDefinitionIndex"],
    )
    string_section = sections["strings"]
    enum_types_by_name: dict[str, list[dict]] = {}
    for type_def in type_defs:
        if not type_def["isEnum"]:
            continue
        name = read_string(data, string_section, type_def["nameIndex"])
        enum_types_by_name.setdefault(name, []).append(type_def)

    catalog = []
    for schema_name in observed_enum_names(field_report):
        metadata_name = schema_name.rsplit(".", 1)[-1]
        candidates = enum_types_by_name.get(metadata_name, [])
        declaring_name = (
            schema_name.rsplit(".", 1)[0].rsplit(".", 1)[-1]
            if "." in schema_name
            else None
        )
        declaring_candidates = [
            type_def
            for type_def in candidates
            if declaring_name in owners_by_type_index.get(type_def["typeIndex"], set())
        ]
        blis_candidates = [
            type_def
            for type_def in candidates
            if read_string(data, string_section, type_def["namespaceIndex"]).startswith(
                "Blis."
            )
        ]
        if len(declaring_candidates) == 1:
            selected = declaring_candidates[0]
            status = "unique-qualified-declaring-type-enum"
        elif len(candidates) == 1:
            selected = candidates[0]
            status = "unique-metadata-enum"
        elif len(blis_candidates) == 1:
            selected = blis_candidates[0]
            status = "unique-blis-namespace-enum"
        else:
            selected = None
            status = (
                "missing-metadata-enum"
                if not candidates
                else "ambiguous-metadata-enums"
            )
        inspected_candidates = [
            inspect_enum(data, sections, type_def, fields, defaults)
            for type_def in candidates
        ]
        selected_index = selected["typeIndex"] if selected is not None else None
        catalog.append(
            {
                "schemaType": schema_name,
                "metadataTypeName": metadata_name,
                "schemaDeclaringType": declaring_name,
                "status": status,
                "candidateCount": len(candidates),
                "selectedTypeIndex": selected_index,
                "selected": next(
                    (
                        candidate
                        for candidate in inspected_candidates
                        if candidate["typeIndex"] == selected_index
                    ),
                    None,
                ),
                "candidates": inspected_candidates,
            }
        )

    status_counts = {}
    for item in catalog:
        status_counts[item["status"]] = status_counts.get(item["status"], 0) + 1
    report = {
        "metadataSourceFile": str(metadata_path),
        "metadataSourceSha256": hashlib.sha256(data).hexdigest(),
        "metadataVersion": version,
        "fieldReportFile": str(field_report_path),
        "fieldReportSourceFile": field_report.get("sourceFile"),
        "fieldReportClientVersion": field_report.get("clientVersion"),
        "observedEnumTypeCount": len(catalog),
        "statusCounts": status_counts,
        "catalog": catalog,
        "limitations": [
            "This catalog covers enum types reached by the supplied field report, not every enum in the game.",
            "A unique Blis namespace candidate is a namespace/name resolution rule when no qualified declaring type is available.",
            "Qualified nested enum names are resolved through the metadata nestedTypes table before namespace heuristics.",
            "Values use the metadata-v39 compressed-int32 rule and retain their candidate list for audit.",
        ],
    }
    output_path = (
        args.out
        or field_report_path.with_name(
            f"{field_report_path.stem}.enum-catalog.inspect.json"
        )
    ).resolve()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(
        json.dumps(
            {
                "output": str(output_path),
                "observedEnumTypes": len(catalog),
                "statusCounts": status_counts,
            },
            ensure_ascii=True,
        )
    )


if __name__ == "__main__":
    main()
