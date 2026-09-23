"""Build a fail-closed, replay-version character capability catalog.

The official gameDb archive is static/base data.  It can prove that a
character definition contains a CC/shield/immunity/movement state and expose
the table's base duration/cooldown/range values.  It cannot by itself prove
who applied an observed state or the final duration after runtime modifiers.
"""

from __future__ import annotations

from collections import defaultdict
import json
import math
from pathlib import Path
import re
import urllib.parse
import zipfile


OFFICIAL_GAME_DB_HOST = "cdn.eternalreturn.io"
OFFICIAL_GAME_DB_PATH = re.compile(
    r"^/gameDb/(?P<filename>gamedata-[0-9]{14}\.zip)$"
)

CC_STATE_TYPES = {
    "Airborne",
    "Blind",
    "Charm",
    "Disarmed",
    "Drowse",
    "Fear",
    "Fetter",
    "Frozen",
    "Grounding",
    "Polymorph",
    "Silence",
    "Sleep",
    "Slow",
    "Stun",
    "Suppressed",
    "Taunt",
}

DEFENSIVE_STATE_TYPES = {
    "CCImmunity",
    "CCMovementImmunity",
    "CCStopImmunity",
    "DebuffImmunity",
    "DisplacementImmunity",
    "DyingImmunity",
    "EvasionNormalAttack",
    "Invulnerability",
    "Protectability",
    "SlowImmunity",
    "Stasis",
    "Unstoppable",
    "Untargetability",
    "protectability",
}

HEALING_NAME_PATTERN = re.compile(
    r"(?:heal|recover|recovery|regen|restore)", re.IGNORECASE
)

REQUIRED_TABLES = {
    "Character",
    "CharacterState",
    "CharacterStateGroup",
    "Skill",
    "SkillGroup",
}


def require_official_game_data_url(url: str) -> str:
    """Return the exact archive filename or reject a non-official source."""
    if not isinstance(url, str) or not url:
        raise ValueError("inspect report is missing gameDataUrl")
    parsed = urllib.parse.urlparse(url)
    if (
        parsed.scheme not in {"http", "https"}
        or parsed.hostname != OFFICIAL_GAME_DB_HOST
        or parsed.username is not None
        or parsed.password is not None
        or parsed.port is not None
        or parsed.params
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError("gameDataUrl is not the exact official gameDb CDN route")
    match = OFFICIAL_GAME_DB_PATH.fullmatch(parsed.path)
    if match is None:
        raise ValueError("gameDataUrl is not a versioned official gameDb archive")
    return match.group("filename")


def validate_game_data_archive(path: Path, url: str) -> dict:
    """Validate local identity and required table membership without fallback."""
    expected_filename = require_official_game_data_url(url)
    if path.name != expected_filename:
        raise ValueError(
            "local game data filename does not match replay gameDataUrl: "
            f"{path.name} != {expected_filename}"
        )
    try:
        with zipfile.ZipFile(path) as archive:
            if archive.testzip() is not None:
                raise ValueError("official gameDb archive contains a corrupt entry")
            hash_catalog = json.loads(archive.read("hash.json"))
            if not isinstance(hash_catalog, dict):
                raise ValueError("official gameDb hash catalog is invalid")
            missing = REQUIRED_TABLES - hash_catalog.keys()
            if missing:
                raise ValueError(
                    f"official gameDb is missing required tables: {sorted(missing)}"
                )
            for table in REQUIRED_TABLES:
                if f"{table}.json" not in archive.namelist():
                    raise ValueError(f"official gameDb is missing {table}.json")
    except zipfile.BadZipFile as exc:
        raise ValueError("official gameDb archive is not a valid ZIP") from exc
    return {
        "status": "exact-replay-header-official-gameDb",
        "url": url,
        "filename": expected_filename,
        "tableCount": len(hash_catalog),
        "fallbackUsed": False,
    }


def _character_code_from_group(group: object, character_codes: set[int]) -> int | None:
    if not isinstance(group, int) or not 1_000_000 <= group < 2_000_000:
        return None
    character_code = (group - 1_000_000) // 1_000
    return character_code if character_code in character_codes else None


def _skill_family(skill_id: object, skill_type: object) -> str:
    text = skill_id if isinstance(skill_id, str) else ""
    for family in ("Active1", "Active2", "Active3", "Active4"):
        if family in text:
            return family
    if skill_type == "Passive" or "Passive" in text:
        return "Passive"
    if skill_type == "UltimateActive":
        return "Active4"
    return "Other"


def _finite_number(value: object) -> float | int | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    if not math.isfinite(value):
        return None
    return value


def _level_values(rows: list[dict], field: str, *, positive: bool) -> list[dict]:
    values = []
    seen = set()
    for row in sorted(rows, key=lambda item: (item.get("level", 0), item.get("code", 0))):
        value = _finite_number(row.get(field))
        if value is None or (positive and value <= 0):
            continue
        level = row.get("level")
        if not isinstance(level, int):
            raise ValueError(f"{field} row has no integer level")
        key = (level, value)
        if key in seen:
            continue
        seen.add(key)
        values.append({"level": level, "value": value})
    return values


def _state_entry(
    state_group: dict,
    state_rows: dict[int, list[dict]],
    skill_groups: dict[int, dict],
) -> dict:
    group = state_group.get("group")
    if not isinstance(group, int):
        raise ValueError("CharacterStateGroup row has no integer group")
    skill_group = skill_groups.get(group, {})
    skill_id = state_group.get("skillId")
    if not isinstance(skill_id, str) or skill_id == "None":
        skill_id = skill_group.get("skillId")
    durations = _level_values(state_rows.get(group, []), "duration", positive=True)
    return {
        "stateGroup": group,
        "stateType": state_group.get("stateType"),
        "effectType": state_group.get("effectType"),
        "skillId": skill_id if isinstance(skill_id, str) and skill_id != "None" else None,
        "family": _skill_family(skill_id, skill_group.get("skillType")),
        "baseDurationSecondsByLevel": durations,
        "durationStatus": (
            "exact-replay-version-character-state-duration"
            if durations
            else "unavailable-no-positive-static-duration"
        ),
        "status": "exact-replay-version-character-state-definition",
    }


def _exact_integer_code(value: object, table: str) -> int:
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    if isinstance(value, str) and value.isascii() and value.isdigit():
        return int(value)
    raise ValueError(f"{table} contains a non-integer code")


def build_character_capability_catalog(path: Path) -> dict:
    """Normalize exact static capabilities for every Character.json row."""
    with zipfile.ZipFile(path) as archive:
        characters = json.loads(archive.read("Character.json"))
        skill_rows_raw = json.loads(archive.read("Skill.json"))
        skill_groups_raw = json.loads(archive.read("SkillGroup.json"))
        state_rows_raw = json.loads(archive.read("CharacterState.json"))
        state_groups_raw = json.loads(archive.read("CharacterStateGroup.json"))

    if not all(isinstance(rows, list) for rows in (
        characters,
        skill_rows_raw,
        skill_groups_raw,
        state_rows_raw,
        state_groups_raw,
    )):
        raise ValueError("official gameDb capability tables are invalid")

    character_rows = {}
    for row in characters:
        code = row.get("code")
        name = row.get("name")
        if not isinstance(code, int) or not isinstance(name, str) or not name:
            raise ValueError("Character.json contains an invalid character row")
        if code in character_rows:
            raise ValueError(f"Character.json contains duplicate code {code}")
        character_rows[code] = row
    character_codes = set(character_rows)

    skill_groups = {}
    for row in skill_groups_raw:
        group = row.get("group")
        if not isinstance(group, int):
            raise ValueError("SkillGroup.json contains a non-integer group")
        if group in skill_groups:
            raise ValueError(f"SkillGroup.json contains duplicate group {group}")
        skill_groups[group] = row

    skill_rows: dict[int, list[dict]] = defaultdict(list)
    for row in skill_rows_raw:
        group = row.get("group")
        if not isinstance(group, int):
            raise ValueError("Skill.json contains a non-integer group")
        skill_rows[group].append(row)

    state_rows: dict[int, list[dict]] = defaultdict(list)
    state_rows_by_code: dict[int, dict] = {}
    for row in state_rows_raw:
        group = row.get("group")
        if not isinstance(group, int):
            raise ValueError("CharacterState.json contains a non-integer group")
        code = _exact_integer_code(row.get("code"), "CharacterState.json")
        if code in state_rows_by_code:
            raise ValueError(f"CharacterState.json contains duplicate code {code}")
        normalized_row = {**row, "code": code}
        state_rows_by_code[code] = normalized_row
        state_rows[group].append(normalized_row)

    state_groups: dict[int, dict] = {}
    state_groups_by_character: dict[int, list[dict]] = defaultdict(list)
    for row in state_groups_raw:
        group = row.get("group")
        if not isinstance(group, int):
            raise ValueError("CharacterStateGroup.json contains a non-integer group")
        if group in state_groups:
            raise ValueError(
                f"CharacterStateGroup.json contains duplicate group {group}"
            )
        state_groups[group] = row
        character_code = _character_code_from_group(group, character_codes)
        if character_code is not None:
            state_groups_by_character[character_code].append(row)

    runtime_cc_groups = {
        str(group): {
            "stateGroup": group,
            "stateType": row["stateType"],
            "effectType": row.get("effectType"),
            "status": "exact-replay-version-character-state-group",
        }
        for group, row in sorted(state_groups.items())
        if row.get("stateType") in CC_STATE_TYPES
    }
    runtime_cc_codes = {}
    for code, row in sorted(state_rows_by_code.items()):
        group = row["group"]
        group_definition = runtime_cc_groups.get(str(group))
        if group_definition is None:
            continue
        runtime_cc_codes[str(code)] = {
            "stateCode": code,
            "stateGroup": group,
            "stateType": group_definition["stateType"],
            "status": "exact-replay-version-character-state-code",
        }

    skill_groups_by_character: dict[int, list[dict]] = defaultdict(list)
    for row in skill_groups_raw:
        character_code = _character_code_from_group(row.get("group"), character_codes)
        if character_code is not None:
            skill_groups_by_character[character_code].append(row)

    catalog = {}
    for code, character in sorted(character_rows.items()):
        character_state_groups = state_groups_by_character.get(code, [])
        normalized_states = [
            _state_entry(row, state_rows, skill_groups)
            for row in sorted(character_state_groups, key=lambda item: item["group"])
        ]
        crowd_control = [
            row for row in normalized_states if row["stateType"] in CC_STATE_TYPES
        ]
        shields = [row for row in normalized_states if row["stateType"] == "Shield"]
        healing = [
            row
            for row in normalized_states
            if row["effectType"] != "Debuff"
            and row["stateType"] not in {"BlockHeal", "HpHealDecrease"}
            and isinstance(row["skillId"], str)
            and HEALING_NAME_PATTERN.search(row["skillId"])
        ]
        defensive_types = sorted({
            row["stateType"]
            for row in normalized_states
            if row["stateType"] in DEFENSIVE_STATE_TYPES
        })

        skill_profiles = []
        for group_row in sorted(
            skill_groups_by_character.get(code, []), key=lambda item: item["group"]
        ):
            group = group_row["group"]
            rows = skill_rows.get(group, [])
            skill_profiles.append({
                "skillGroup": group,
                "skillId": group_row.get("skillId"),
                "family": _skill_family(
                    group_row.get("skillId"), group_row.get("skillType")
                ),
                "skillType": group_row.get("skillType"),
                "targetType": group_row.get("targetType"),
                "castWaysType": group_row.get("castWaysType"),
                "movementSkill": group_row.get("movementSkill") is True,
                "additionalAgainInput": group_row.get("additionalAgainInput") is True,
                "cooldownSecondsByLevel": _level_values(rows, "cooldown", positive=False),
                "rangeByLevel": _level_values(rows, "range", positive=False),
                "status": "exact-replay-version-skill-definition",
            })

        has_definitions = bool(character_state_groups or skill_profiles)
        catalog[str(code)] = {
            "characterCode": code,
            "characterNameInternal": character["name"],
            "crowdControlStates": crowd_control,
            "shieldStates": shields,
            "namedHealingStates": healing,
            "defensiveStateTypes": defensive_types,
            "movementSkills": [row for row in skill_profiles if row["movementSkill"]],
            "skillProfiles": skill_profiles,
            "coverageStatus": (
                "exact-static-definitions-present"
                if has_definitions
                else "unavailable-no-character-skill-or-state-definition"
            ),
            "interpretationBoundary": (
                "static-base-capability-not-runtime-source-or-final-duration"
            ),
            "fallbackUsed": False,
        }

    if set(catalog) != {str(code) for code in character_codes}:
        raise ValueError("character capability catalog coverage mismatch")
    return {
        "status": "exact-replay-version-official-gameDb-character-capabilities",
        "characterCount": len(catalog),
        "characters": catalog,
        "runtimeCrowdControl": {
            "status": "exact-replay-version-official-gameDb-cc-state-map",
            "codeCount": len(runtime_cc_codes),
            "groupCount": len(runtime_cc_groups),
            "codes": runtime_cc_codes,
            "groups": runtime_cc_groups,
            "fallbackUsed": False,
        },
        "fallbackUsed": False,
    }
