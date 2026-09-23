"""Versioned projectile-skill catalog and fail-closed hit-rate aggregation.

The replay-version gameDb describes projectile behaviour, but it does not
declare a universal foreign key from ``SkillGroup`` to ``ProjectileSetting``.
For that reason name-derived links are candidates only.  A runtime link is
promoted only when every observed cast and every candidate projectile spawn
form an exclusive, constant-multiplicity relation inside a bounded tick window.
"""

from __future__ import annotations

from bisect import bisect_left, bisect_right
from collections import defaultdict
from copy import deepcopy
from functools import lru_cache
import json
import hashlib
from io import BytesIO
import math
from pathlib import Path
import re
import struct
import zipfile


PROJECTILE_OBJECT_TYPES = {14, 15, 16}
DEFAULT_LINK_WINDOW_TICKS = 12
MIN_LINK_OBSERVATIONS = 3
MANUALLY_AIMED_CAST_WAYS = {
    "Directional",
    "PickPoint",
    "PickPointThenRelease",
}
TARGET_LOCKED_CAST_WAYS = {
    "PickTargetCenter",
    "PickTargetEdge",
    "PickTargetThenRelease",
}
CAST_TIMING_FIELDS = (
    "castingTime1",
    "castingTime2",
    "reservationCastingTime",
    "concentrationTime",
    "chargingTime",
    "castWaitTime",
)

# 12.1 is intentionally separate from the current schema decoder.  Across the
# user-authorized public 12.1 sample, every one of the 5,953 ObjectType
# 14/15/16 nested spawn payloads had this arity and its first two int32 fields
# matched an exact 12.1 ProjectileSetting code and an observed replay owner.
# Callers must still verify both values against the replay-version gameDb and
# the current replay object universe.  Unknown versions fail closed.
VERSIONED_PROJECTILE_SPAWN_PREFIXES = {
    "12.1.0": {
        14: 7,
        15: 8,
        16: 7,
    },
}


def decode_versioned_projectile_spawn_identity(
    client_version: str,
    object_type: int,
    payload: bytes,
    *,
    verified_layout: dict[int, int] | None = None,
    layout_authority: str | None = None,
) -> dict:
    """Read only a replay-version-verified projectile code/owner prefix.

    The parent ``CmdSpawn`` decoder already proves the nested ``byte[]``
    boundary.  This helper deliberately does not pretend to decode the rest of
    the snapshot and never borrows a layout from a different client version.
    """
    if verified_layout is not None:
        if layout_authority != "multi-match-exact-prefix-consensus-v1":
            raise ValueError("runtime projectile layout has no accepted authority")
        if (
            not verified_layout
            or any(
                not isinstance(key, int)
                or key < 0
                or not isinstance(value, int)
                or not 0 <= value <= 255
                for key, value in verified_layout.items()
            )
        ):
            raise ValueError("runtime projectile layout is invalid")
        layouts = verified_layout
        wire_authority = layout_authority
    else:
        layouts = VERSIONED_PROJECTILE_SPAWN_PREFIXES.get(client_version)
        wire_authority = f"promoted-version-table-{client_version}"
    if layouts is None:
        raise ValueError(
            f"no verified projectile spawn prefix for client {client_version}"
        )
    expected_header = layouts.get(object_type)
    if expected_header is None:
        raise ValueError(
            f"ObjectType {object_type} is not a verified projectile spawn type "
            f"for client {client_version}"
        )
    if not isinstance(payload, bytes) or len(payload) < 9:
        raise ValueError("projectile spawn payload is missing or too short")
    if payload[0] != expected_header:
        raise ValueError(
            f"projectile spawn arity changed for {client_version} "
            f"ObjectType {object_type}: {payload[0]} != {expected_header}"
        )
    projectile_code, owner_object_id = struct.unpack_from("<ii", payload, 1)
    if projectile_code <= 0 or owner_object_id <= 0:
        raise ValueError("projectile spawn code/owner prefix is invalid")
    return {
        "projectileCode": projectile_code,
        "ownerObjectId": owner_object_id,
        "memberHeader": payload[0],
        "wireStatus": (
            "verified-versioned-projectile-code-owner-prefix-"
            f"{client_version}"
        ),
        "layoutAuthority": wire_authority,
        "fallbackUsed": False,
    }


def attach_skill_action_anchors(
    skill_starts: list[dict],
    skill_actions: list[dict],
) -> list[dict]:
    """Attach exact action ticks to their most recent same-skill cast.

    ``CmdPlaySkillAction`` can occur after a cast animation or charge, while
    use count remains the number of ``CmdStartSkill`` rows.  Actions are linked
    only by the exact player object and wire ``SkillId`` and only forward to
    the latest preceding start.  Missing or malformed action identity is
    ignored; it never changes the cast count.
    """
    output = [deepcopy(row) for row in skill_starts]
    indexes_by_identity: dict[tuple[int, int], list[int]] = defaultdict(list)
    ticks_by_identity: dict[tuple[int, int], list[int]] = defaultdict(list)
    for index, row in enumerate(output):
        player_id = row.get("playerObjectId")
        skill_id_code = row.get("skillIdCode")
        tick = row.get("tick")
        row["linkAnchorTicks"] = [tick] if isinstance(tick, int) else []
        row["linkActions"] = []
        row["linkAnchorStatus"] = "exact-CmdStartSkill-only"
        if (
            isinstance(player_id, int)
            and isinstance(skill_id_code, int)
            and isinstance(tick, int)
        ):
            identity = (player_id, skill_id_code)
            indexes_by_identity[identity].append(index)
    for identity, indexes in indexes_by_identity.items():
        indexes.sort(key=lambda index: output[index]["tick"])
        ticks_by_identity[identity] = [output[index]["tick"] for index in indexes]
    for action in sorted(skill_actions, key=lambda row: row.get("tick", -1)):
        player_id = action.get("playerObjectId")
        skill_id_code = action.get("skillIdCode")
        tick = action.get("tick")
        if not all(isinstance(value, int) for value in (player_id, skill_id_code, tick)):
            continue
        identity = (player_id, skill_id_code)
        start_ticks = ticks_by_identity.get(identity)
        if not start_ticks:
            continue
        position = bisect_right(start_ticks, tick) - 1
        if position < 0:
            continue
        start_index = indexes_by_identity[identity][position]
        anchors = output[start_index]["linkAnchorTicks"]
        if tick not in anchors:
            anchors.append(tick)
            anchors.sort()
            output[start_index]["linkAnchorStatus"] = (
                "exact-CmdStartSkill-plus-CmdPlaySkillAction"
            )
        output[start_index]["linkActions"].append({
            "tick": tick,
            "wireStatus": action.get("wireStatus"),
            "targets": deepcopy(action.get("targets") or []),
        })
    return output


def _character_code_from_group(group: object, character_codes: set[int]) -> int | None:
    if not isinstance(group, int) or not 1_000_000 <= group < 2_000_000:
        return None
    code = (group - 1_000_000) // 1_000
    return code if code in character_codes else None


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


def _normal(value: object) -> str:
    return _normal_text(value) if isinstance(value,str) else ""


@lru_cache(maxsize=4096)
def _normal_text(value: str) -> str:
    return re.sub(r"[^a-z0-9]", "", value.lower())


def _aim_model(row: dict) -> dict:
    cast_way = row.get("castWaysType")
    target_type = row.get("targetType")
    guideline = row.get("guideline")
    if cast_way in MANUALLY_AIMED_CAST_WAYS:
        kind = "manual-direction-or-point"
        eligible = True
        reason = "castWaysType requires a direction or world point"
    elif cast_way in TARGET_LOCKED_CAST_WAYS:
        kind = "target-locked"
        eligible = False
        reason = "target-selected skill collision is not player aim accuracy"
    elif cast_way == "Instant":
        kind = "instant-or-no-aim"
        eligible = False
        reason = "instant skill has no verified direction/point aim input"
    else:
        kind = "unknown"
        eligible = False
        reason = "castWaysType is missing or unsupported"
    return {
        "kind": kind,
        "castWaysType": cast_way,
        "targetType": target_type,
        "guideline": guideline,
        "hitRateEligible": eligible,
        "status": "exact-replay-version-SkillGroup-cast-model",
        "reason": reason,
    }


def _cast_timing(row: dict) -> dict:
    values = {
        field: value
        for field in CAST_TIMING_FIELDS
        if isinstance((value := row.get(field)), (int, float))
        and not isinstance(value, bool)
        and value >= 0
    }
    maximum_seconds = max(values.values(), default=0)
    return {
        "declaredSeconds": values,
        "maximumDeclaredPreActionSeconds": maximum_seconds,
        "derivedLinkWindowTicksAt60Hz": max(
            DEFAULT_LINK_WINDOW_TICKS,
            math.ceil(maximum_seconds * 60) + (1 if maximum_seconds > 0 else 0),
        ),
        "status": "derived-exact-replay-version-SkillGroup-timing",
    }


def _variant_suffix(skill_id: object) -> str | None:
    if not isinstance(skill_id, str):
        return None
    match = re.search(r"_(\d+)(?:\D*)$", skill_id)
    return match.group(1) if match else None


def _candidate_projectile_codes(
    character_name: str,
    skill_id: object,
    family: str,
    skill_group: int,
    projectile_rows: list[dict],
) -> list[int]:
    """Return name-family candidates without treating them as authority."""
    character_token = _normal(character_name)
    skill_token = _normal(skill_id)
    if not character_token or family == "Other":
        return []
    family_number = {
        "Active1": "01",
        "Active2": "02",
        "Active3": "03",
        "Active4": "04",
    }.get(family)
    variant = _variant_suffix(skill_id)
    scored: list[tuple[int, int]] = []
    for row in projectile_rows:
        code = row.get("code")
        prefab = row.get("prefabName")
        if not isinstance(code, int) or not isinstance(prefab, str):
            continue
        prefab_token = _normal(prefab)
        if character_token not in prefab_token:
            continue
        score = 0
        numeric_exact = code == skill_group // 10 + 1
        if numeric_exact:
            score = 5
        if skill_token and skill_token in prefab_token:
            score = 5
        if family == "Passive" and "passive" in prefab_token:
            score = max(score, 3)
        if family_number is not None:
            prefab_variant = re.search(
                rf"skill0?{int(family_number)}(?:_(\d+))?",
                prefab,
                re.IGNORECASE,
            )
            if variant is not None and prefab_variant is not None and not numeric_exact:
                observed_variant = prefab_variant.group(1) or "1"
                if observed_variant != variant:
                    continue
            base_tokens = (
                f"{character_token}skill{family_number}",
                f"{character_token}skill{int(family_number)}",
                f"{character_token}active{int(family_number)}",
            )
            if any(token in prefab_token for token in base_tokens):
                score = max(score, 3)
            if variant is not None and any(
                f"{token}{variant}" in prefab_token for token in base_tokens
            ):
                score = max(score, 4)
        if score:
            scored.append((score, code))
    if not scored:
        return []
    highest = max(score for score, _ in scored)
    # A full skill/variant token is more specific than the family-only token.
    # If no specific token exists, retain every family candidate as ambiguous.
    threshold = 4 if highest >= 4 else highest
    return sorted({code for score, code in scored if score >= threshold})


def _definition_shape(row: dict) -> dict:
    values: list[str] = []
    reasons: list[str] = []
    penetration = row.get("penetrationCount")
    if isinstance(penetration, int) and penetration > 1:
        values.append("penetrating")
        reasons.append(f"penetrationCount={penetration}")
    if row.get("isExplosion") is True or row.get("isExplosionWithoutCollision") is True:
        values.append("explosive")
        reasons.append("isExplosion/isExplosionWithoutCollision")
    after_arrival = row.get("enableObjectCollsionCheckAfterArrival") is True
    life_after_arrival = row.get("lifeTimeAfterArrival")
    if after_arrival or (
        isinstance(life_after_arrival, (int, float)) and life_after_arrival > 0
    ):
        values.append("installation")
        reasons.append("collision/lifetime after arrival")
    if "return" in str(row.get("prefabName", "")).lower():
        values.append("returning")
        reasons.append("prefab name contains Return; name-derived only")
    return {
        "values": values,
        "status": (
            "derived-exact-projectile-setting-traits"
            if values and "returning" not in values
            else "mixed-exact-traits-plus-name-candidate"
            if values
            else "unavailable-no-shape-defining-trait"
        ),
        "reason": "; ".join(reasons) if reasons else "single/multi is a runtime cast property",
    }


def _normalize_projectile_definition(row: dict) -> dict:
    code = row.get("code")
    prefab = row.get("prefabName")
    if not isinstance(code, int) or not isinstance(prefab, str):
        raise ValueError("ProjectileSetting.json contains an invalid row")
    return {
        "projectileCode": code,
        "prefabName": prefab,
        "projectileType": row.get("type"),
        "collisionEnabled": row.get("enableObjectCollisionCheck") is True,
        "collisionAfterArrival": row.get(
            "enableObjectCollsionCheckAfterArrival"
        ) is True,
        "penetrationCount": row.get("penetrationCount"),
        "isExplosion": row.get("isExplosion") is True,
        "isExplosionWithoutCollision": row.get(
            "isExplosionWithoutCollision"
        ) is True,
        "explosionRadius": row.get("explosionRadius"),
        "lifeTimeAfterArrival": row.get("lifeTimeAfterArrival"),
        "localMoveType": row.get("localMoveType"),
        "collisionObjectType": row.get("collisionObjectType"),
        "shape": _definition_shape(row),
        "status": "exact-replay-version-ProjectileSetting-row",
    }


def build_projectile_skill_catalog(path: Path) -> dict:
    """Build a six-field entry for every character-owned SkillGroup row."""
    source_bytes = Path(path).read_bytes()
    source_sha256 = hashlib.sha256(source_bytes).hexdigest()
    with zipfile.ZipFile(BytesIO(source_bytes)) as archive:
        required = {"Character.json", "SkillGroup.json", "ProjectileSetting.json"}
        missing = required - set(archive.namelist())
        if missing:
            raise ValueError(f"official gameDb is missing projectile tables: {sorted(missing)}")
        characters = json.loads(archive.read("Character.json"))
        skill_groups = json.loads(archive.read("SkillGroup.json"))
        projectile_rows = json.loads(archive.read("ProjectileSetting.json"))
    if not all(isinstance(rows, list) for rows in (characters, skill_groups, projectile_rows)):
        raise ValueError("official gameDb projectile tables are invalid")

    character_rows: dict[int, dict] = {}
    for row in characters:
        code, name = row.get("code"), row.get("name")
        if not isinstance(code, int) or not isinstance(name, str) or not name:
            raise ValueError("Character.json contains an invalid character row")
        if code in character_rows:
            raise ValueError(f"Character.json contains duplicate code {code}")
        character_rows[code] = row
    definitions: dict[str, dict] = {}
    raw_projectiles_by_code: dict[int, dict] = {}
    for row in projectile_rows:
        normalized = _normalize_projectile_definition(row)
        code = normalized["projectileCode"]
        if str(code) in definitions:
            raise ValueError(f"ProjectileSetting.json contains duplicate code {code}")
        definitions[str(code)] = normalized
        raw_projectiles_by_code[code] = row

    character_codes = set(character_rows)
    skills: dict[str, dict] = {}
    by_character: dict[int, list[dict]] = defaultdict(list)
    for row in skill_groups:
        group = row.get("group")
        character_code = _character_code_from_group(group, character_codes)
        if character_code is None:
            continue
        if str(group) in skills:
            raise ValueError(f"SkillGroup.json contains duplicate character group {group}")
        character = character_rows[character_code]
        family = _skill_family(row.get("skillId"), row.get("skillType"))
        candidates = _candidate_projectile_codes(
            character["name"], row.get("skillId"), family, group, projectile_rows
        )
        candidate_shapes = sorted({
            shape
            for code in candidates
            for shape in definitions[str(code)]["shape"]["values"]
        })
        entry = {
            "characterCode": character_code,
            "characterNameInternal": character["name"],
            "skillGroup": group,
            "skillId": row.get("skillId"),
            "family": family,
            "projectileStatus": (
                "candidate-static-name-only"
                if candidates
                else "unknown-no-declared-skill-projectile-link"
            ),
            "projectileCodes": [],
            "projectileCodeCandidates": candidates,
            "projectileShape": {
                "values": candidate_shapes,
                "status": (
                    "candidate-from-exact-projectile-definitions"
                    if candidates
                    else "unavailable-no-verified-projectile-link"
                ),
                "reason": (
                    "candidate codes are not a declared SkillGroup foreign key"
                    if candidates
                    else "ProjectileSetting has no universal SkillGroup foreign key"
                ),
            },
            "aimModel": _aim_model(row),
            "castTiming": _cast_timing(row),
            "hitEvidencePacket": {
                "packet": None,
                "status": "unavailable-no-verified-projectile-link",
            },
            "useCountEvidence": {
                "packet": "CmdStartSkill",
                "unit": (
                    "one exact character skill-start packet; each charge use "
                    "or recast stage is a separate record"
                ),
                "status": "decoded-exact-character-skill-start-count",
            },
            "attemptDenominator": {
                "event": None,
                "status": "unavailable-no-verified-projectile-link",
            },
            "hitRateCalculable": False,
            "hitRateReason": (
                "runtime cast-to-projectile relation has not been verified"
                if candidates
                else "no declared or observed projectile relation"
            ),
            "fallbackUsed": False,
        }
        skills[str(group)] = entry
        by_character[character_code].append(entry)

    characters_out = {
        str(code): {
            "characterCode": code,
            "characterNameInternal": row["name"],
            "skills": sorted(
                (deepcopy(item) for item in by_character.get(code, [])),
                key=lambda item: item["skillGroup"],
            ),
            "status": (
                "exact-character-skillgroup-coverage"
                if by_character.get(code)
                else "unavailable-no-character-owned-skillgroup"
            ),
            "fallbackUsed": False,
        }
        for code, row in sorted(character_rows.items())
    }
    expected_groups = {
        str(row["group"])
        for row in skill_groups
        if _character_code_from_group(row.get("group"), character_codes) is not None
    }
    if set(skills) != expected_groups:
        raise ValueError("projectile skill catalog coverage mismatch")
    return {
        "status": "exact-replay-version-whole-roster-projectile-skill-catalog",
        "sourceGameDbSha256": source_sha256,
        "characterCount": len(characters_out),
        "skillCount": len(skills),
        "projectileDefinitionCount": len(definitions),
        "characters": characters_out,
        "skillGroups": skills,
        "projectileDefinitions": definitions,
        "linkAuthority": (
            "static names are candidates only; runtime exclusive constant-"
            "multiplicity cast/spawn evidence is required"
        ),
        "fallbackUsed": False,
    }


def _safe_single_definition(definition: dict) -> tuple[bool, str]:
    if definition.get("collisionEnabled") is not True:
        return False, "projectile collision is disabled in ProjectileSetting"
    if definition.get("penetrationCount") != 1:
        return False, "penetrating projectile needs target-level semantics"
    if definition.get("isExplosion") or definition.get("isExplosionWithoutCollision"):
        return False, "explosive projectile needs explosion-target semantics"
    if definition.get("collisionAfterArrival"):
        return False, "installation-like projectile needs lifetime semantics"
    life = definition.get("lifeTimeAfterArrival")
    if isinstance(life, (int, float)) and life > 0:
        return False, "persistent projectile needs lifetime semantics"
    if "return" in str(definition.get("prefabName", "")).lower():
        return False, "returning projectile needs outbound/return-leg semantics"
    return True, "single non-penetrating non-explosive projectile"


def _summarize_action_target_evidence(
    starts: list[dict],
    combat_start_indexes: set[int],
    player_id: int,
    player_teams: dict[int, int],
) -> dict:
    """Summarize exact action targets without calling them projectile hits."""
    linked_action_count = 0
    targeted_action_count = 0
    exact_target_reference_count = 0
    enemy_player_target_reference_count = 0
    ally_player_target_reference_count = 0
    non_player_target_reference_count = 0
    casts_with_any_target_count = 0
    casts_with_linked_action_count = 0
    casts_with_enemy_player_target_count = 0
    combat_linked_action_count = 0
    combat_casts_with_linked_action_count = 0
    combat_targeted_action_count = 0
    combat_enemy_player_target_reference_count = 0
    combat_casts_with_enemy_player_target_count = 0
    enemy_target_same_tick_direct_damage_count = 0
    enemy_target_same_tick_owned_projectile_collision_count = 0
    enemy_target_same_tick_corroborated_count = 0
    combat_casts_with_same_tick_corroborated_enemy_target_count = 0
    wire_statuses: set[str] = set()
    target_wire_statuses: set[str] = set()
    combat_cast_outcomes: list[list[int | None]] = []

    for start_index, start in enumerate(starts):
        actions = start.get("linkActions") or []
        if not isinstance(actions, list):
            raise ValueError("linked skill actions must be a list")
        is_combat = start_index in combat_start_indexes
        cast_has_any_target = False
        cast_has_enemy_player_target = False
        cast_has_same_tick_corroborated_enemy_target = False
        first_hit_tick = None
        if actions:
            casts_with_linked_action_count += 1
            if is_combat:
                combat_casts_with_linked_action_count += 1
        for action in actions:
            if not isinstance(action, dict):
                raise ValueError("linked skill action must be an object")
            linked_action_count += 1
            if is_combat:
                combat_linked_action_count += 1
            wire_status = action.get("wireStatus")
            if isinstance(wire_status, str) and wire_status:
                wire_statuses.add(wire_status)
            targets = action.get("targets") or []
            if not isinstance(targets, list):
                raise ValueError("linked skill action targets must be a list")
            if targets:
                targeted_action_count += 1
                cast_has_any_target = True
                if isinstance(wire_status, str) and wire_status:
                    target_wire_statuses.add(wire_status)
                if is_combat:
                    combat_targeted_action_count += 1
            for target in targets:
                if not isinstance(target, dict):
                    raise ValueError("linked skill action target must be an object")
                target_id = target.get("targetObjectId")
                if not isinstance(target_id, int):
                    raise ValueError("linked skill action target id is invalid")
                exact_target_reference_count += 1
                if target_id not in player_teams:
                    non_player_target_reference_count += 1
                elif player_teams[target_id] == player_teams[player_id]:
                    ally_player_target_reference_count += 1
                else:
                    enemy_player_target_reference_count += 1
                    cast_has_enemy_player_target = True
                    direct_damage = (
                        target.get("sameTickDirectPlayerDamage") is True
                    )
                    projectile_collision = (
                        target.get("sameTickOwnedProjectileCollision") is True
                    )
                    enemy_target_same_tick_direct_damage_count += direct_damage
                    enemy_target_same_tick_owned_projectile_collision_count += (
                        projectile_collision
                    )
                    if direct_damage or projectile_collision:
                        enemy_target_same_tick_corroborated_count += 1
                        cast_has_same_tick_corroborated_enemy_target = True
                        action_tick = action.get("tick")
                        if not isinstance(action_tick, int) or action_tick < start["tick"]:
                            raise ValueError("corroborated skill hit tick is invalid")
                        first_hit_tick = action_tick if first_hit_tick is None else min(first_hit_tick, action_tick)
                    if is_combat:
                        combat_enemy_player_target_reference_count += 1
        if cast_has_any_target:
            casts_with_any_target_count += 1
        if cast_has_enemy_player_target:
            casts_with_enemy_player_target_count += 1
            if is_combat:
                combat_casts_with_enemy_player_target_count += 1
        if cast_has_same_tick_corroborated_enemy_target and is_combat:
            combat_casts_with_same_tick_corroborated_enemy_target_count += 1
        if is_combat:
            combat_cast_outcomes.append([
                start["tick"],
                int(cast_has_same_tick_corroborated_enemy_target),
                start["tick"],
                first_hit_tick,
            ])

    combat_cast_count = len(combat_start_indexes)
    return {
        "status": (
            "decoded-exact-action-target-diagnostic-not-hit-rate"
            if exact_target_reference_count
            else "decoded-exact-linked-actions-no-targets"
            if linked_action_count
            else "unavailable-no-linked-skill-action"
        ),
        "wireStatuses": sorted(wire_statuses),
        "targetWireStatuses": sorted(target_wire_statuses),
        "linkedActionCount": linked_action_count,
        "targetedActionCount": targeted_action_count,
        "exactTargetReferenceCount": exact_target_reference_count,
        "enemyPlayerTargetReferenceCount": enemy_player_target_reference_count,
        "allyPlayerTargetReferenceCount": ally_player_target_reference_count,
        "nonPlayerTargetReferenceCount": non_player_target_reference_count,
        "castsWithAnyTargetCount": casts_with_any_target_count,
        "castsWithLinkedActionCount": casts_with_linked_action_count,
        "castsWithEnemyPlayerTargetCount": casts_with_enemy_player_target_count,
        "combatCastCount": combat_cast_count,
        "combatLinkedActionCount": combat_linked_action_count,
        "combatCastsWithLinkedActionCount": combat_casts_with_linked_action_count,
        "combatTargetedActionCount": combat_targeted_action_count,
        "combatEnemyPlayerTargetReferenceCount": (
            combat_enemy_player_target_reference_count
        ),
        "combatCastsWithEnemyPlayerTargetCount": (
            combat_casts_with_enemy_player_target_count
        ),
        "enemyPlayerTargetSameTickDirectDamageCount": (
            enemy_target_same_tick_direct_damage_count
        ),
        "enemyPlayerTargetSameTickOwnedProjectileCollisionCount": (
            enemy_target_same_tick_owned_projectile_collision_count
        ),
        "enemyPlayerTargetSameTickCorroboratedCount": (
            enemy_target_same_tick_corroborated_count
        ),
        "combatCastsWithSameTickCorroboratedEnemyTargetCount": (
            combat_casts_with_same_tick_corroborated_enemy_target_count
        ),
        "_combatCastOutcomes": combat_cast_outcomes,
        "candidateEnemyTargetedCastShare": (
            round(combat_casts_with_enemy_player_target_count / combat_cast_count, 6)
            if combat_cast_count
            else None
        ),
        "interpretation": (
            "candidate evidence only; an action target can mean acquisition, "
            "state propagation, multi-stage behavior, or a resolved hit"
        ),
        "fallbackUsed": False,
    }


def _apply_corroborated_action_target_cast_rate(
    row: dict,
    minimum_observations: int,
) -> None:
    """Promote only fully observed, same-tick-corroborated manual casts.

    This is an independent exact evidence route, not a fallback for missing
    projectile links.  A miss is countable only when every observed cast has
    at least one resolved ordinary skill-action packet.  Every positive enemy
    target must also have same-tick direct damage or an owned projectile
    collision.  State-skill target packets are intentionally excluded.
    """
    evidence = row.get("actionTargetEvidence")
    if not isinstance(evidence, dict):
        return
    evidence["castHitRateCalculable"] = False
    evidence["castHitRateReason"] = "action-target cast evidence is unavailable"
    evidence["promotionStatus"] = "not-promoted"

    aim_model = row.get("aimModel") or {}
    all_casts = row.get("allCastCount")
    combat_casts = row.get("castCount")
    linked_casts = evidence.get("castsWithLinkedActionCount")
    combat_linked_casts = evidence.get("combatCastsWithLinkedActionCount")
    target_refs = evidence.get("enemyPlayerTargetReferenceCount")
    corroborated_refs = evidence.get(
        "enemyPlayerTargetSameTickCorroboratedCount"
    )
    hit_casts = evidence.get("combatCastsWithEnemyPlayerTargetCount")
    ordinary_statuses = {
        "decoded-exact-CmdPlaySkillAction",
        "decoded-exact-CmdPlaySkillActionWithTargets",
    }
    wire_statuses = set(evidence.get("wireStatuses") or [])
    target_wire_statuses = set(evidence.get("targetWireStatuses") or [])

    if aim_model.get("hitRateEligible") is not True:
        reason = "skill is not a manual direction/point aim"
    elif not all(
        isinstance(value, int) and not isinstance(value, bool) and value >= 0
        for value in (
            all_casts,
            combat_casts,
            linked_casts,
            combat_linked_casts,
            target_refs,
            corroborated_refs,
            hit_casts,
        )
    ):
        reason = "action-target evidence counts are invalid"
    elif all_casts < minimum_observations:
        reason = (
            f"minimum repeated cast observations are insufficient: "
            f"{all_casts} observed, {minimum_observations} required"
        )
    elif combat_casts == 0:
        reason = "no manual cast occurred during a confirmed player engagement"
    elif linked_casts != all_casts or combat_linked_casts != combat_casts:
        reason = (
            "ordinary skill-action coverage is incomplete, so an absent target "
            "cannot be counted as a miss"
        )
    elif not wire_statuses or not wire_statuses <= ordinary_statuses:
        reason = "linked actions include a state-skill or unsupported packet"
    elif target_wire_statuses != {
        "decoded-exact-CmdPlaySkillActionWithTargets"
    }:
        reason = "enemy targets are not exclusively exact target-list skill actions"
    elif target_refs == 0:
        reason = "no enemy-player target was observed for same-skill validation"
    elif corroborated_refs != target_refs:
        reason = (
            "not every enemy action target has same-tick direct damage or an "
            "owned projectile collision"
        )
    elif hit_casts > combat_casts:
        reason = "enemy-target cast count exceeds its exact cast denominator"
    else:
        reason = (
            "enemy-targeted manual casts, with every positive target corroborated "
            "by same-tick damage or owned projectile collision"
        )
        evidence.update({
            "castHitRateCalculable": True,
            "castHitRateReason": reason,
            "promotionStatus": (
                "verified-exact-action-target-cast-hit-rate"
            ),
        })
        if row.get("hitRateCalculable") is not True:
            row.update({
                "hitEvidencePacket": {
                    "packet": (
                        "CmdPlaySkillActionWithTargets.targets.targetId + "
                        "same-tick CmdDamage/CmdProjectileCollision"
                    ),
                    "status": (
                        "decoded-exact-enemy-target-plus-same-tick-hit-"
                        "corroboration"
                    ),
                },
                "attemptDenominator": {
                    "event": "CmdStartSkill",
                    "unit": (
                        "one exact manual-aim skill cast during a confirmed "
                        "player engagement"
                    ),
                    "status": (
                        "verified-complete-action-covered-manual-skill-casts-"
                        "during-confirmed-player-engagement"
                    ),
                },
                "hitRateCalculable": True,
                "hitRateReason": reason,
                "attemptCount": combat_casts,
                "playerHitAttemptCount": hit_casts,
                "playerHitRate": round(hit_casts / combat_casts, 6),
                "hitRateUnit": "skill-cast",
                "hitRateMethod": "exact-action-target-same-tick-corroborated",
                "_combatAttemptOutcomes": deepcopy(
                    evidence["_combatCastOutcomes"]
                ),
            })
        return

    evidence["castHitRateReason"] = reason


def _summarize_exact_effect_damage_evidence(
    starts: list[dict],
    combat_start_indexes: set[int],
    player_id: int,
    skill_group: int,
    exact_skill_damage_events: list[dict] | None,
) -> dict:
    """Summarize exact skill/state-code damage at same-skill action ticks."""
    ordinary_statuses = {
        "decoded-exact-CmdPlaySkillAction",
        "decoded-exact-CmdPlaySkillActionWithTargets",
    }
    ordinary_action_count = 0
    casts_with_ordinary_action_count = 0
    combat_casts_with_ordinary_action_count = 0
    unsupported_action_count = 0
    cast_indexes_by_action_tick: dict[int, set[int]] = defaultdict(set)
    for start_index, start in enumerate(starts):
        actions = start.get("linkActions") or []
        if not isinstance(actions, list):
            raise ValueError("linked skill actions must be a list")
        cast_has_ordinary_action = False
        for action in actions:
            if not isinstance(action, dict):
                raise ValueError("linked skill action must be an object")
            tick = action.get("tick")
            status = action.get("wireStatus")
            if status not in ordinary_statuses:
                unsupported_action_count += 1
                continue
            if not isinstance(tick, int):
                raise ValueError("ordinary skill action tick is invalid")
            ordinary_action_count += 1
            cast_has_ordinary_action = True
            cast_indexes_by_action_tick[tick].add(start_index)
        if cast_has_ordinary_action:
            casts_with_ordinary_action_count += 1
            if start_index in combat_start_indexes:
                combat_casts_with_ordinary_action_count += 1

    base = {
        "status": (
            "decoded-exact-skill-damage-diagnostic"
            if exact_skill_damage_events is not None
            else "unavailable-no-exact-skill-damage-input"
        ),
        "ordinaryActionCount": ordinary_action_count,
        "castsWithOrdinaryActionCount": casts_with_ordinary_action_count,
        "combatCastsWithOrdinaryActionCount": (
            combat_casts_with_ordinary_action_count
        ),
        "unsupportedOrStateActionCount": unsupported_action_count,
        "exactMappedEnemyDamageCount": 0,
        "sameSkillActionTickDamageCount": 0,
        "unlinkedOrDelayedSkillDamageCount": 0,
        "ambiguousActionTickSkillDamageCount": 0,
        "unresolvedEnemyDamageAtSkillActionTickCount": 0,
        "combatCastsWithExactDamageCount": 0,
        "castHitRateCalculable": False,
        "castHitRateReason": "exact skill damage evidence is unavailable",
        "promotionStatus": "not-promoted",
        "fallbackUsed": False,
    }
    if exact_skill_damage_events is None:
        return base
    if not isinstance(exact_skill_damage_events, list):
        raise ValueError("exact skill damage events must be a list")

    hit_combat_cast_indexes: set[int] = set()
    first_hit_ticks: dict[int, int] = {}
    for event in exact_skill_damage_events:
        if not isinstance(event, dict):
            raise ValueError("exact skill damage event must be an object")
        if event.get("ownerPlayerObjectId") != player_id:
            continue
        tick = event.get("tick")
        if not isinstance(tick, int):
            raise ValueError("exact skill damage event tick is invalid")
        mapping_status = event.get("mappingStatus")
        event_group = event.get("skillGroup")
        linked_indexes = cast_indexes_by_action_tick.get(tick, set())
        if mapping_status != "verified-exact-skill-state-code-identity":
            if linked_indexes:
                base["unresolvedEnemyDamageAtSkillActionTickCount"] += 1
            continue
        if event_group != skill_group:
            continue
        base["exactMappedEnemyDamageCount"] += 1
        if len(linked_indexes) == 1:
            start_index = next(iter(linked_indexes))
            base["sameSkillActionTickDamageCount"] += 1
            if start_index in combat_start_indexes:
                hit_combat_cast_indexes.add(start_index)
                first_hit_ticks[start_index] = min(first_hit_ticks.get(start_index, tick), tick)
        elif len(linked_indexes) > 1:
            base["ambiguousActionTickSkillDamageCount"] += 1
        else:
            base["unlinkedOrDelayedSkillDamageCount"] += 1
    base["combatCastsWithExactDamageCount"] = len(hit_combat_cast_indexes)
    base["_combatCastOutcomes"] = [
        [start["tick"], int(start_index in hit_combat_cast_indexes),
         start["tick"], first_hit_ticks.get(start_index)]
        for start_index, start in enumerate(starts)
        if start_index in combat_start_indexes
    ]
    return base


def _apply_exact_effect_damage_cast_rate(
    row: dict,
    minimum_observations: int,
) -> None:
    """Promote a cast rate only from exact versioned code/action identity."""
    evidence = row.get("exactEffectDamageEvidence")
    if not isinstance(evidence, dict):
        return
    evidence["castHitRateCalculable"] = False
    evidence["castHitRateReason"] = "exact skill damage evidence is unavailable"
    evidence["promotionStatus"] = "not-promoted"

    aim_model = row.get("aimModel") or {}
    all_casts = row.get("allCastCount")
    combat_casts = row.get("castCount")
    all_covered = evidence.get("castsWithOrdinaryActionCount")
    combat_covered = evidence.get("combatCastsWithOrdinaryActionCount")
    mapped = evidence.get("exactMappedEnemyDamageCount")
    same_tick = evidence.get("sameSkillActionTickDamageCount")
    delayed = evidence.get("unlinkedOrDelayedSkillDamageCount")
    ambiguous = evidence.get("ambiguousActionTickSkillDamageCount")
    unresolved_at_action = evidence.get(
        "unresolvedEnemyDamageAtSkillActionTickCount"
    )
    unsupported = evidence.get("unsupportedOrStateActionCount")
    hit_casts = evidence.get("combatCastsWithExactDamageCount")
    counts = (
        all_casts,
        combat_casts,
        all_covered,
        combat_covered,
        mapped,
        same_tick,
        delayed,
        ambiguous,
        unresolved_at_action,
        unsupported,
        hit_casts,
    )
    if evidence.get("status") != "decoded-exact-skill-damage-diagnostic":
        reason = "exact replay-version skill damage input is unavailable"
    elif aim_model.get("hitRateEligible") is not True:
        reason = "skill is not a manual direction/point aim"
    elif not all(
        isinstance(value, int) and not isinstance(value, bool) and value >= 0
        for value in counts
    ):
        reason = "exact skill damage evidence counts are invalid"
    elif all_casts < minimum_observations:
        reason = (
            f"minimum repeated cast observations are insufficient: "
            f"{all_casts} observed, {minimum_observations} required"
        )
    elif combat_casts == 0:
        reason = "no manual cast occurred during a confirmed player engagement"
    elif all_covered != all_casts or combat_covered != combat_casts:
        reason = (
            "ordinary skill-action coverage is incomplete, so an absent exact "
            "damage code cannot be counted as a miss"
        )
    elif unsupported:
        reason = "linked actions include a state-skill or unsupported packet"
    elif mapped == 0:
        reason = "no exact enemy damage code was observed for same-skill validation"
    elif delayed or ambiguous or same_tick != mapped:
        reason = (
            "exact skill damage includes delayed, unlinked, or ambiguous action "
            "timing and cannot define one cast result"
        )
    elif unresolved_at_action:
        reason = (
            "an unresolved enemy damage code shares a skill-action tick, so a "
            "missing exact code cannot safely be counted as a miss"
        )
    elif hit_casts > combat_casts:
        reason = "exact-damage hit casts exceed the exact cast denominator"
    else:
        reason = (
            "manual casts with enemy damage whose exact effect code maps to the "
            "same Skill and CharacterState group at the same skill-action tick"
        )
        evidence.update({
            "castHitRateCalculable": True,
            "castHitRateReason": reason,
            "promotionStatus": "verified-exact-effect-code-cast-hit-rate",
        })
        if row.get("hitRateCalculable") is not True:
            row.update({
                "hitEvidencePacket": {
                    "packet": (
                        "CmdDamage.effectCode/objectId + exact Skill/"
                        "CharacterState code/group + CmdPlaySkillAction tick"
                    ),
                    "status": (
                        "verified-exact-versioned-skill-state-code-and-"
                        "same-action-tick"
                    ),
                },
                "attemptDenominator": {
                    "event": "CmdStartSkill",
                    "unit": (
                        "one exact manual-aim skill cast during a confirmed "
                        "player engagement"
                    ),
                    "status": (
                        "verified-complete-action-covered-manual-skill-casts-"
                        "during-confirmed-player-engagement"
                    ),
                },
                "hitRateCalculable": True,
                "hitRateReason": reason,
                "attemptCount": combat_casts,
                "playerHitAttemptCount": hit_casts,
                "playerHitRate": round(hit_casts / combat_casts, 6),
                "hitRateUnit": "skill-cast",
                "hitRateMethod": (
                    "exact-effect-code-skill-state-group-same-action-tick"
                ),
                "_combatAttemptOutcomes": deepcopy(
                    evidence["_combatCastOutcomes"]
                ),
            })
        return

    evidence["castHitRateReason"] = reason


class ProjectilePreparation:
    """Match-local links only; engagement denominators are always recalculated."""
    def __init__(self, catalog, skill_starts, projectile_spawns, projectile_collisions, player_teams, link_window_ticks):
        self.inputs=(catalog,skill_starts,projectile_spawns,projectile_collisions)
        self.teams=player_teams.copy()
        self.window=link_window_ticks
        self.windows={}
        self.links={}
        # Per-player/group candidate links are independent of engagement
        # intervals.  Keep only the small index relations and ambiguity count;
        # hit-rate rows remain invocation-local.
        self.group_links={}
        skill_defs=catalog['skillGroups']
        starts_by_player: dict[int, list[dict]] = defaultdict(list)
        for row in skill_starts:
            player_id, group, tick = (
                row.get("playerObjectId"), row.get("skillGroup"), row.get("tick")
            )
            if player_id not in player_teams or not isinstance(group, int) or not isinstance(tick, int):
                continue
            if str(group) not in skill_defs:
                continue
            starts_by_player[player_id].append(row)
        spawns_by_player: dict[int, list[dict]] = defaultdict(list)
        for row in projectile_spawns:
            player_id, code, object_id, tick = (
                row.get("ownerPlayerObjectId"), row.get("projectileCode"),
                row.get("projectileObjectId"), row.get("tick"),
            )
            if (
                player_id in player_teams
                and isinstance(code, int)
                and isinstance(object_id, int)
                and isinstance(tick, int)
            ):
                spawns_by_player[player_id].append(row)

        collisions_by_projectile: dict[int, set[tuple[int, int]]] = defaultdict(set)
        for row in projectile_collisions:
            projectile_id, target_id, tick = (
                row.get("projectileObjectId"),
                row.get("targetObjectId"),
                row.get("tick"),
            )
            if (
                isinstance(projectile_id, int)
                and isinstance(target_id, int)
                and isinstance(tick, int)
            ):
                collisions_by_projectile[projectile_id].add((tick, target_id))

        self.starts=starts_by_player
        self.spawns=spawns_by_player
        self.collisions=collisions_by_projectile

    def validate(self,catalog,starts,spawns,collisions,teams,window):
        if any(a is not b for a,b in zip(self.inputs,(catalog,starts,spawns,collisions))) or self.teams!=teams or self.window!=window:
            raise ValueError('projectile preparation belongs to different match inputs or policy')

    def player(self,player_id,spawn_matches_start):
        if player_id in self.links:return self.links[player_id]
        skill_defs=self.inputs[0]['skillGroups']
        link_window_ticks=self.window
        # These are our own index lists, never the input fact arrays.
        player_starts=self.starts.get(player_id,[])
        player_starts.sort(key=lambda row:row['tick'])
        spawns_by_player=self.spawns
        player_spawns = spawns_by_player.get(player_id, [])
        player_spawns.sort(key=lambda row: row["tick"])
        # Keep ALL starts and spawns for ownership/exclusivity checks. Only
        # result construction is restricted; competing skills still veto links.
        # Index the bounded start/anchor windows before exact matching.  The
        # previous nested scan compared every spawn with every start, which
        # dominated offline recalculation on large replays.  The exact
        # predicate below still decides every link; this only narrows the
        # candidates by time and cannot broaden attribution.
        start_tick_entries = []
        anchor_tick_entries = []
        max_start_window = link_window_ticks
        for start_index, start in enumerate(player_starts):
            definition = skill_defs.get(str(start.get("skillGroup")), {})
            declared_window = definition.get("castTiming", {}).get(
                "derivedLinkWindowTicksAt60Hz"
            )
            effective_window = max(
                link_window_ticks,
                declared_window if isinstance(declared_window, int) else 0,
            )
            max_start_window = max(max_start_window, effective_window)
            start_tick_entries.append((start.get("tick"), start_index))
            for anchor in start.get("linkAnchorTicks") or []:
                if isinstance(anchor, int) and anchor != start.get("tick"):
                    anchor_tick_entries.append((anchor, start_index))
        start_tick_entries.sort()
        anchor_tick_entries.sort()
        start_ticks = [tick for tick, _ in start_tick_entries]
        anchor_ticks = [tick for tick, _ in anchor_tick_entries]

        def compatible_start_indexes(spawn_tick):
            indexes = set()
            left = bisect_left(start_ticks, spawn_tick - max_start_window)
            right = bisect_right(start_ticks, spawn_tick)
            indexes.update(index for _, index in start_tick_entries[left:right])
            left = bisect_left(anchor_ticks, spawn_tick - link_window_ticks)
            right = bisect_right(anchor_ticks, spawn_tick)
            indexes.update(index for _, index in anchor_tick_entries[left:right])
            return indexes
        # Discover a projectile code without relying on its prefab name only
        # when every spawn of that code has exactly one preceding skill start
        # in the bounded window and every such start belongs to the same skill
        # group.  One ambiguous spawn invalidates runtime discovery for that
        # code; it is never assigned by nearest-time guessing.
        uniquely_timed_group_by_spawn: dict[int, int] = {}
        spawn_indexes_by_code: dict[int, list[int]] = defaultdict(list)
        for spawn_index, spawn in enumerate(player_spawns):
            spawn_indexes_by_code[spawn["projectileCode"]].append(spawn_index)
            compatible = [
                player_starts[index]
                for index in compatible_start_indexes(spawn["tick"])
                if spawn_matches_start(spawn, player_starts[index])
            ]
            if len(compatible) == 1:
                uniquely_timed_group_by_spawn[spawn_index] = compatible[0][
                    "skillGroup"
                ]
        runtime_codes_by_group: dict[int, set[int]] = defaultdict(set)
        for projectile_code, spawn_indexes in spawn_indexes_by_code.items():
            groups = {
                uniquely_timed_group_by_spawn.get(spawn_index)
                for spawn_index in spawn_indexes
            }
            if None not in groups and len(groups) == 1:
                runtime_codes_by_group[next(iter(groups))].add(projectile_code)
        result=player_starts,player_spawns,runtime_codes_by_group
        self.links[player_id]=result
        return result

    def group_link_candidates(
        self,
        player_id,
        group,
        candidates,
        candidate_authority,
        spawn_matches_start,
    ):
        """Return cached cast/spawn indexes for one immutable match group."""
        candidate_key = tuple(sorted(candidates))
        key = (player_id, group, candidate_authority, candidate_key)
        cached = self.group_links.get(key)
        if cached is not None:
            return cached

        player_starts = self.starts.get(player_id, [])
        starts = [row for row in player_starts if row["skillGroup"] == group]
        relevant_spawns = [
            row for row in self.spawns.get(player_id, [])
            if row["projectileCode"] in candidates
        ]
        potential: dict[int, list[int]] = {}
        reverse: dict[int, list[int]] = defaultdict(list)
        for start_index, start in enumerate(starts):
            matches = []
            for spawn_index, spawn in enumerate(relevant_spawns):
                if spawn_matches_start(spawn, start):
                    matches.append(spawn_index)
                    reverse[spawn_index].append(start_index)
            potential[start_index] = matches

        # Preserve the original object-id keyed map semantics: duplicate
        # object ids overwrite the prior entry before ambiguity is counted.
        global_compatible_starts_by_spawn: dict[int, list[dict]] = {}
        for spawn in relevant_spawns:
            compatible_starts = []
            for any_start in player_starts:
                if candidate_authority != (
                    "verified-runtime-exclusive-code-discovery"
                ):
                    if spawn["projectileCode"] not in set(
                        self.inputs[0]["skillGroups"][
                            str(any_start["skillGroup"])
                        ].get("projectileCodeCandidates") or []
                    ):
                        continue
                if spawn_matches_start(spawn, any_start):
                    compatible_starts.append(any_start)
            global_compatible_starts_by_spawn[
                spawn["projectileObjectId"]
            ] = compatible_starts
        globally_ambiguous_spawn_count = sum(
            len(compatible_starts) != 1
            or compatible_starts[0]["skillGroup"] != group
            for compatible_starts in global_compatible_starts_by_spawn.values()
        )

        cached = (potential, reverse, globally_ambiguous_spawn_count)
        self.group_links[key] = cached
        return cached


def calculate_projectile_hit_rates(
    catalog: dict,
    skill_starts: list[dict],
    projectile_spawns: list[dict],
    projectile_collisions: list[dict],
    player_teams: dict[int, int],
    confirmed_pvp_intervals: dict[int, list[list[int]]],
    *,
    _preparation=None,
    exact_skill_damage_events: list[dict] | None = None,
    output_skill_groups: set[int] | None = None,
    link_window_ticks: int = DEFAULT_LINK_WINDOW_TICKS,
    minimum_observations: int = MIN_LINK_OBSERVATIONS,
    scope_evidence: str = (
        "derived-exact-team-pvp-episode-intersect-own-combat-state"
    ),
) -> dict:
    """Calculate only exclusive one-projectile hits during confirmed PvP.

    The cast-to-projectile relation is verified against all match observations,
    while the displayed denominator contains only exact projectile spawn
    objects whose linked cast began inside a confirmed player engagement.  It
    is never actionCount, nearby damage, or an inferred button-press count.
    """
    if (
        link_window_ticks < 0
        or minimum_observations < 1
        or not isinstance(scope_evidence, str)
        or not scope_evidence
    ):
        raise ValueError("invalid projectile runtime-link policy")
    skill_defs = catalog.get("skillGroups")
    projectile_defs = catalog.get("projectileDefinitions")
    if not isinstance(skill_defs, dict) or not isinstance(projectile_defs, dict):
        raise ValueError("invalid projectile skill catalog")
    if not isinstance(confirmed_pvp_intervals, dict):
        raise ValueError("confirmed PvP intervals are required")
    if output_skill_groups is not None and (
            not isinstance(output_skill_groups, (set, frozenset)) or
            any(type(group) is not int for group in output_skill_groups)):
        raise ValueError('output skill groups require an explicit set of integer identities')

    start_link_windows = {}

    def start_link_window(start: dict):
        # Local to this invocation: starts and versioned rules are immutable
        # during calculation, so the same window need not be rebuilt per shot.
        key = id(start)
        if key in start_link_windows:
            return start_link_windows[key]
        anchors = start.get("linkAnchorTicks")
        if not isinstance(anchors, list) or not anchors:
            anchors = [start.get("tick")]
        definition = skill_defs.get(str(start.get("skillGroup")), {})
        declared_window = definition.get("castTiming", {}).get(
            "derivedLinkWindowTicksAt60Hz"
        )
        effective_window = max(
            link_window_ticks,
            declared_window if isinstance(declared_window, int) else 0,
        )
        start_tick = start.get("tick")
        action_anchors = sorted(
            anchor
            for anchor in anchors
            if isinstance(anchor, int) and anchor != start_tick
        )
        start_window = effective_window
        if isinstance(start_tick, int) and action_anchors:
            start_window = min(
                start_window,
                max(0, action_anchors[0] - start_tick),
            )
        value = (start_tick, start_window, action_anchors)
        start_link_windows[key] = value
        return value

    def spawn_matches_start(spawn: dict, start: dict) -> bool:
        start_tick, start_window, action_anchors = start_link_window(start)
        if (
            isinstance(start_tick, int)
            and 0 <= spawn["tick"] - start_tick <= start_window
        ):
            return True
        return any(
            0 <= spawn["tick"] - anchor <= link_window_ticks
            for anchor in action_anchors
        )
    normalized_pvp_intervals: dict[int, list[tuple[int, int]]] = {}
    for player_id in player_teams:
        normalized = []
        for interval in confirmed_pvp_intervals.get(player_id, []):
            if (
                not isinstance(interval, (list, tuple))
                or len(interval) != 2
                or not all(isinstance(value, int) for value in interval)
                or interval[1] <= interval[0]
            ):
                raise ValueError("invalid confirmed PvP interval")
            normalized.append((interval[0], interval[1]))
        normalized_pvp_intervals[player_id] = sorted(normalized)

    preparation = _preparation or ProjectilePreparation(catalog,skill_starts,projectile_spawns,projectile_collisions,player_teams,link_window_ticks)
    preparation.validate(catalog,skill_starts,projectile_spawns,projectile_collisions,player_teams,link_window_ticks)
    starts_by_player=preparation.starts
    collisions_by_projectile=preparation.collisions
    start_link_windows=preparation.windows

    player_rows: dict[str, list[dict]] = {}
    verified_links: dict[str, dict] = {}
    for player_id in sorted(player_teams):
        player_starts = starts_by_player.get(player_id, [])
        selected_groups = {row['skillGroup'] for row in player_starts}
        if output_skill_groups is not None:
            selected_groups.intersection_update(output_skill_groups)
        if not selected_groups:
            player_rows[str(player_id)] = []
            continue
        player_starts,player_spawns,runtime_codes_by_group = preparation.player(player_id,spawn_matches_start)
        rows = []
        for group in sorted(selected_groups):
            definition = skill_defs[str(group)]
            static_candidates = set(
                definition.get("projectileCodeCandidates") or []
            )
            runtime_candidates = runtime_codes_by_group.get(group, set())
            candidates = runtime_candidates or static_candidates
            candidate_authority = (
                "verified-runtime-exclusive-code-discovery"
                if runtime_candidates
                else "candidate-static-name-only"
                if static_candidates
                else "unavailable-no-code-candidate"
            )
            starts = [row for row in player_starts if row["skillGroup"] == group]
            combat_start_indexes = {
                index
                for index, row in enumerate(starts)
                if any(
                    left <= row["tick"] < right
                    for left, right in normalized_pvp_intervals[player_id]
                )
            }
            relevant_spawns = [row for row in player_spawns if row["projectileCode"] in candidates]
            use_count_evidence = deepcopy(definition.get("useCountEvidence"))
            if isinstance(use_count_evidence, dict):
                use_count_evidence["scope"] = "confirmed-player-engagement-only"
                use_count_evidence["unit"] = (
                    "one exact character skill-start packet whose tick is inside "
                    "a confirmed player engagement; each charge use or recast "
                    "stage is a separate record"
                )
            base = {
                "skillGroup": group,
                "skillId": definition.get("skillId"),
                "family": definition.get("family"),
                "projectileStatus": definition.get("projectileStatus"),
                "projectileCodes": [],
                "projectileShape": deepcopy(definition.get("projectileShape")),
                "aimModel": deepcopy(definition.get("aimModel")),
                "castTiming": deepcopy(definition.get("castTiming")),
                "hitEvidencePacket": deepcopy(definition.get("hitEvidencePacket")),
                "useCountEvidence": use_count_evidence,
                "attemptDenominator": deepcopy(definition.get("attemptDenominator")),
                "hitRateCalculable": False,
                "hitRateReason": definition.get("hitRateReason"),
                "scope": "confirmed-player-engagement-only",
                "scopeEvidence": scope_evidence,
                "projectileCodeCandidateEvidence": {
                    "authority": candidate_authority,
                    "staticCandidates": sorted(static_candidates),
                    "runtimeExclusiveCandidates": sorted(runtime_candidates),
                    "exactSkillActionAnchorCount": sum(
                        max(0, len(row.get("linkAnchorTicks") or [row["tick"]]) - 1)
                        for row in starts
                    ),
                    "effectiveLinkWindowTicks": max(
                        link_window_ticks,
                        definition.get("castTiming", {}).get(
                            "derivedLinkWindowTicksAt60Hz", 0
                        ),
                    ),
                    "actionAnchorLinkWindowTicks": link_window_ticks,
                },
                "allCastCount": len(starts),
                "castCount": len(combat_start_indexes),
                "excludedOutsideCombatCastCount": len(starts) - len(combat_start_indexes),
                "confirmedPvpIntervalCount": len(
                    normalized_pvp_intervals[player_id]
                ),
                "actionTargetEvidence": _summarize_action_target_evidence(
                    starts,
                    combat_start_indexes,
                    player_id,
                    player_teams,
                ),
                "exactEffectDamageEvidence": (
                    _summarize_exact_effect_damage_evidence(
                        starts,
                        combat_start_indexes,
                        player_id,
                        group,
                        exact_skill_damage_events,
                    )
                ),
                "fallbackUsed": False,
            }
            if not candidates:
                base["castSpawnLinkDiagnostics"] = {
                    "castCount": len(starts),
                    "candidateSpawnCount": 0,
                    "unmatchedCastCount": len(starts),
                    "multipleSpawnCastCount": 0,
                    "unmatchedSpawnCount": 0,
                    "multipleCastSpawnCount": 0,
                    "globallyAmbiguousSpawnCount": 0,
                    "status": "unavailable-no-projectile-code-candidate",
                }
                rows.append(base)
                continue
            potential, reverse, globally_ambiguous_spawn_count = (
                preparation.group_link_candidates(
                    player_id,
                    group,
                    candidates,
                    candidate_authority,
                    spawn_matches_start,
                )
            )
            globally_exclusive = globally_ambiguous_spawn_count == 0
            link_diagnostics = {
                "castCount": len(starts),
                "candidateSpawnCount": len(relevant_spawns),
                "unmatchedCastCount": sum(
                    len(indexes) == 0 for indexes in potential.values()
                ),
                "multipleSpawnCastCount": sum(
                    len(indexes) > 1 for indexes in potential.values()
                ),
                "unmatchedSpawnCount": sum(
                    len(reverse.get(index, [])) == 0
                    for index in range(len(relevant_spawns))
                ),
                "multipleCastSpawnCount": sum(
                    len(reverse.get(index, [])) > 1
                    for index in range(len(relevant_spawns))
                ),
                "globallyAmbiguousSpawnCount": globally_ambiguous_spawn_count,
                "status": "derived-exact-bounded-cast-action-spawn-link-check",
            }
            base["castSpawnLinkDiagnostics"] = link_diagnostics
            multiplicities = {len(indexes) for indexes in potential.values()}
            multi_exclusive = (
                len(starts) >= minimum_observations
                and len(multiplicities) == 1
                and next(iter(multiplicities), 0) > 1
                and all(len(indexes) == 1 for indexes in reverse.values())
                and len(reverse) == len(relevant_spawns)
                and globally_exclusive
            )
            if multi_exclusive:
                projectiles_per_cast = next(iter(multiplicities))
                observed_codes = sorted({
                    row["projectileCode"] for row in relevant_spawns
                })
                all_used_spawns = [
                    relevant_spawns[spawn_index]
                    for start_index in range(len(starts))
                    for spawn_index in potential[start_index]
                ]
                combat_used_spawns = [
                    relevant_spawns[spawn_index]
                    for start_index in sorted(combat_start_indexes)
                    for spawn_index in potential[start_index]
                ]
                first_unsafe = None
                for projectile_code in observed_codes:
                    projectile_definition = projectile_defs.get(str(projectile_code))
                    if not isinstance(projectile_definition, dict):
                        first_unsafe = (
                            "observed projectile code is absent from ProjectileSetting"
                        )
                        break
                    safe, reason = _safe_single_definition(projectile_definition)
                    if not safe:
                        first_unsafe = reason
                        break
                shape = {
                    "values": ["multi"],
                    "status": "verified-constant-multiple-projectiles-per-cast",
                    "reason": f"{projectiles_per_cast} exact projectile spawns per cast",
                }
                base.update({
                    "projectileStatus": "verified-observed-exclusive-cast-spawn-link",
                    "projectileCodes": observed_codes,
                    "projectileShape": shape,
                    "projectilesPerCast": projectiles_per_cast,
                })
                if first_unsafe is not None:
                    base["projectileShape"]["reason"] += f"; {first_unsafe}"
                    base["hitRateReason"] = first_unsafe
                    rows.append(base)
                    continue
                aim_model = definition.get("aimModel")
                if (
                    not isinstance(aim_model, dict)
                    or aim_model.get("hitRateEligible") is not True
                ):
                    base.update({
                        "aimModel": deepcopy(aim_model),
                        "hitEvidencePacket": {
                            "packet": "CmdProjectileCollision.objectId/targetId",
                            "status": "decoded-exact-projectile-target-collision",
                        },
                        "attemptDenominator": {
                            "event": "CmdSpawn projectile snapshot objectId",
                            "unit": (
                                "one exact projectile; "
                                f"{projectiles_per_cast} verified spawns per cast"
                            ),
                            "status": (
                                "verified-constant-multiple-projectile-spawns-per-cast-"
                                "during-confirmed-player-engagement"
                            ),
                        },
                        "hitRateReason": (
                            aim_model.get("reason")
                            if isinstance(aim_model, dict)
                            and isinstance(aim_model.get("reason"), str)
                            else "skill aim model is unavailable"
                        ),
                    })
                    rows.append(base)
                    continue
                attempt_outcomes = []
                enemy_collision_pairs = 0
                ally_collision_pairs = 0
                non_player_collision_pairs = 0
                for start_index in sorted(combat_start_indexes):
                    for spawn_index in potential[start_index]:
                        spawn = relevant_spawns[spawn_index]
                        targets = {
                            target_id
                            for collision_tick, target_id in collisions_by_projectile.get(
                                spawn["projectileObjectId"], set()
                            )
                            if collision_tick >= spawn["tick"]
                        }
                        hit_enemy = False
                        for target_id in targets:
                            if target_id not in player_teams:
                                non_player_collision_pairs += 1
                            elif player_teams[target_id] == player_teams[player_id]:
                                ally_collision_pairs += 1
                            else:
                                enemy_collision_pairs += 1
                                hit_enemy = True
                        attempt_outcomes.append([
                            starts[start_index]["tick"],
                            int(hit_enemy),
                            spawn["tick"],
                            min((tick for tick, target in collisions_by_projectile.get(
                                spawn["projectileObjectId"], set()
                            ) if tick >= spawn["tick"] and target in player_teams
                                and player_teams[target] != player_teams[player_id]), default=None),
                        ])
                attempts = len(attempt_outcomes)
                hit_projectiles = sum(outcome[1] for outcome in attempt_outcomes)
                rate = hit_projectiles / attempts if attempts else None
                base.update({
                    "hitEvidencePacket": {
                        "packet": "CmdProjectileCollision.objectId/targetId",
                        "status": "decoded-exact-projectile-target-collision",
                    },
                    "attemptDenominator": {
                        "event": "CmdSpawn projectile snapshot objectId",
                        "unit": (
                            "one exact projectile; "
                            f"{projectiles_per_cast} verified spawns per cast"
                        ),
                        "status": (
                            "verified-constant-multiple-projectile-spawns-per-cast-"
                            "during-confirmed-player-engagement"
                        ),
                    },
                    "hitRateCalculable": attempts > 0,
                    "hitRateReason": (
                        "enemy-player collision per verified projectile in a constant "
                        "multi-projectile cast during confirmed player engagement"
                        if attempts
                        else "no verified multi-projectile spawn during confirmed player engagement"
                    ),
                    "attemptCount": attempts,
                    "playerHitAttemptCount": hit_projectiles,
                    "playerHitRate": round(rate, 6) if rate is not None else None,
                    "enemyPlayerCollisionPairCount": enemy_collision_pairs,
                    "allyPlayerCollisionPairCount": ally_collision_pairs,
                    "nonPlayerCollisionPairCount": non_player_collision_pairs,
                    "duplicateCollisionPolicy": "unique projectileObjectId/targetObjectId",
                    "hitRateUnit": "projectile-shot",
                    **({"_combatAttemptOutcomes": attempt_outcomes} if attempts else {}),
                })
                rows.append(base)
                link_key = f"{player_id}:{group}"
                verified_links[link_key] = {
                    "playerObjectId": player_id,
                    "skillGroup": group,
                    "projectileCodes": observed_codes,
                    "projectilesPerCast": projectiles_per_cast,
                    "allCastCount": len(starts),
                    "combatCastCount": len(combat_start_indexes),
                    "allSpawnCount": len(all_used_spawns),
                    "combatSpawnCount": attempts,
                    "projectileCodeAuthority": candidate_authority,
                    "status": "verified-observed-exclusive-cast-spawn-link",
                }
                continue
            exclusive = (
                len(starts) >= minimum_observations
                and len(relevant_spawns) == len(starts)
                and all(len(indexes) == 1 for indexes in potential.values())
                and all(len(indexes) == 1 for indexes in reverse.values())
                and len(reverse) == len(relevant_spawns)
                and globally_exclusive
            )
            if not exclusive:
                if len(starts) < minimum_observations:
                    base["hitRateReason"] = (
                        "minimum repeated cast observations are insufficient: "
                        f"{len(starts)} observed, {minimum_observations} required"
                    )
                else:
                    base["hitRateReason"] = (
                        "cast/projectile observations are not an exclusive one-to-one relation: "
                        f"{len(starts)} casts, {len(relevant_spawns)} candidate spawns, "
                        f"{link_diagnostics['unmatchedCastCount']} unmatched casts, "
                        f"{link_diagnostics['multipleSpawnCastCount']} multi-spawn casts, "
                        f"{link_diagnostics['unmatchedSpawnCount']} unmatched spawns, "
                        f"{link_diagnostics['multipleCastSpawnCount']} multi-cast spawns, "
                        f"{link_diagnostics['globallyAmbiguousSpawnCount']} globally ambiguous spawns"
                    )
                rows.append(base)
                continue
            all_used_spawns = [
                relevant_spawns[potential[index][0]] for index in range(len(starts))
            ]
            combat_used_spawns = [
                relevant_spawns[potential[index][0]]
                for index in sorted(combat_start_indexes)
            ]
            observed_codes = sorted({row["projectileCode"] for row in all_used_spawns})
            safety_results = []
            for projectile_code in observed_codes:
                projectile_definition = projectile_defs.get(str(projectile_code))
                if not isinstance(projectile_definition, dict):
                    safety_results.append((False, "observed projectile code is absent from ProjectileSetting"))
                else:
                    safety_results.append(_safe_single_definition(projectile_definition))
            first_unsafe = next((reason for safe, reason in safety_results if not safe), None)
            if first_unsafe is not None:
                combined_shapes = sorted({
                    shape
                    for projectile_code in observed_codes
                    for shape in projectile_defs.get(str(projectile_code), {}).get(
                        "shape", {}
                    ).get("values", [])
                })
                base.update({
                    "projectileStatus": "verified-observed-exclusive-cast-spawn-link",
                    "projectileCodes": observed_codes,
                    "projectileShape": {
                        "values": combined_shapes,
                        "status": "derived-exact-linked-projectile-setting-traits",
                        "reason": first_unsafe,
                    },
                    "hitRateReason": first_unsafe,
                })
                rows.append(base)
                continue
            reason = "single non-penetrating non-explosive projectile"

            aim_model = definition.get("aimModel")
            if (
                not isinstance(aim_model, dict)
                or aim_model.get("hitRateEligible") is not True
            ):
                aim_reason = (
                    aim_model.get("reason")
                    if isinstance(aim_model, dict)
                    and isinstance(aim_model.get("reason"), str)
                    else "skill aim model is unavailable"
                )
                base.update({
                    "projectileStatus": (
                        "verified-observed-exclusive-cast-spawn-link"
                    ),
                    "projectileCodes": observed_codes,
                    "projectileShape": {
                        "values": ["single"],
                        "status": (
                            "verified-exclusive-one-projectile-per-cast-"
                            "plus-exact-setting"
                        ),
                        "reason": reason,
                    },
                    "aimModel": deepcopy(aim_model),
                    "hitEvidencePacket": {
                        "packet": "CmdProjectileCollision.objectId/targetId",
                        "status": (
                            "decoded-exact-projectile-target-collision"
                        ),
                    },
                    "attemptDenominator": {
                        "event": "CmdSpawn projectile snapshot objectId",
                        "status": (
                            "verified-exclusive-one-projectile-spawn-per-cast-"
                            "during-confirmed-player-engagement"
                        ),
                    },
                    "hitRateReason": aim_reason,
                })
                rows.append(base)
                continue

            attempt_outcomes = []
            enemy_collision_pairs = 0
            ally_collision_pairs = 0
            non_player_collision_pairs = 0
            for start_index in sorted(combat_start_indexes):
                spawn = relevant_spawns[potential[start_index][0]]
                targets = {
                    target_id
                    for collision_tick, target_id in collisions_by_projectile.get(
                        spawn["projectileObjectId"], set()
                    )
                    if collision_tick >= spawn["tick"]
                }
                hit_enemy = False
                for target_id in targets:
                    if target_id not in player_teams:
                        non_player_collision_pairs += 1
                    elif player_teams[target_id] == player_teams[player_id]:
                        ally_collision_pairs += 1
                    else:
                        enemy_collision_pairs += 1
                        hit_enemy = True
                attempt_outcomes.append([
                    starts[start_index]["tick"],
                    int(hit_enemy),
                    spawn["tick"],
                    min((tick for tick, target in collisions_by_projectile.get(
                        spawn["projectileObjectId"], set()
                    ) if tick >= spawn["tick"] and target in player_teams
                        and player_teams[target] != player_teams[player_id]), default=None),
                ])
            attempts = len(attempt_outcomes)
            hit_projectiles = sum(outcome[1] for outcome in attempt_outcomes)
            rate = hit_projectiles / attempts if attempts else None
            base.update({
                "projectileStatus": "verified-observed-exclusive-cast-spawn-link",
                "projectileCodes": observed_codes,
                "projectileShape": {
                    "values": ["single"],
                    "status": "verified-exclusive-one-projectile-per-cast-plus-exact-setting",
                    "reason": reason,
                },
                "hitEvidencePacket": {
                    "packet": "CmdProjectileCollision.objectId/targetId",
                    "status": "decoded-exact-projectile-target-collision",
                },
                "attemptDenominator": {
                    "event": "CmdSpawn projectile snapshot objectId",
                    "status": (
                        "verified-exclusive-one-projectile-spawn-per-cast-"
                        "during-confirmed-player-engagement"
                    ),
                },
                "hitRateCalculable": attempts > 0,
                "hitRateReason": (
                    "enemy-player collision per verified single projectile spawn "
                    "during confirmed player engagement"
                    if attempts
                    else "no verified single projectile spawn during confirmed player engagement"
                ),
                "attemptCount": attempts,
                "playerHitAttemptCount": hit_projectiles,
                "playerHitRate": round(rate, 6) if rate is not None else None,
                "enemyPlayerCollisionPairCount": enemy_collision_pairs,
                "allyPlayerCollisionPairCount": ally_collision_pairs,
                "nonPlayerCollisionPairCount": non_player_collision_pairs,
                "duplicateCollisionPolicy": "unique projectileObjectId/targetObjectId",
                "hitRateUnit": "projectile-shot",
                **({"_combatAttemptOutcomes": attempt_outcomes} if attempts else {}),
            })
            rows.append(base)
            link_key = f"{player_id}:{group}"
            verified_links[link_key] = {
                "playerObjectId": player_id,
                "skillGroup": group,
                "projectileCodes": observed_codes,
                "allCastCount": len(starts),
                "combatCastCount": len(combat_start_indexes),
                "allSpawnCount": len(all_used_spawns),
                "combatSpawnCount": attempts,
                "projectileCodeAuthority": candidate_authority,
                "status": "verified-observed-exclusive-cast-spawn-link",
            }
        for row in rows:
            _apply_corroborated_action_target_cast_rate(
                row, minimum_observations
            )
            _apply_exact_effect_damage_cast_rate(
                row, minimum_observations
            )
        player_rows[str(player_id)] = rows
    return {
        "status": "derived-exact-projectile-spawn-and-collision-hit-rates",
        "linkWindowTicks": link_window_ticks,
        "minimumObservations": minimum_observations,
        "scope": "confirmed-player-engagement-only",
        "scopeEvidence": scope_evidence,
        "players": player_rows,
        "verifiedLinks": verified_links,
        "fallbackUsed": False,
    }
