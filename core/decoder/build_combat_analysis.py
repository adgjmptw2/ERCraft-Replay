"""Build an evidence-drillable combat analysis UI for one held replay.

The output keeps exact decoded values and transparent derived calculations
separate. Replay-originated strings, session data, and request headers are not
included. Character and skill labels come from the exact game-data archive
named by the replay header.
"""

from __future__ import annotations

import argparse
import base64
import brotli
from collections import Counter, defaultdict, OrderedDict
from dataclasses import dataclass
import hashlib
import json
import math
from pathlib import Path
import re
import struct
import sys
import time
import zipfile


ROOT = Path(__file__).resolve().parent.parent
SIMULTANEOUS_ENTRY_WINDOW_SECONDS = 0.25
SIMULTANEOUS_ENTRY_WINDOW_TICKS = round(
    SIMULTANEOUS_ENTRY_WINDOW_SECONDS * 60
)

sys.path.insert(0, str(ROOT / "decoder"))
from delta_payloads import DecodeError, SchemaDecoder  # noqa: E402
from character_capabilities import (  # noqa: E402
    build_character_capability_catalog,
    validate_game_data_archive,
)
from projectile_hit_catalog import (  # noqa: E402
    PROJECTILE_OBJECT_TYPES,
    attach_skill_action_anchors,
    build_projectile_skill_catalog,
    calculate_projectile_hit_rates,
)
from audit_projectile_runtime_replay import (  # noqa: E402
    resolve_exact_skill_damage_events,
    resolve_skill_action_actors,
)
from inspect_deltas import iter_records, load_definitions, parse_delta_payload  # noqa: E402
from inspect_spawn_snapshots import decode_candidate_exact  # noqa: E402
from replay_compatibility import (  # noqa: E402
    classify_match_mode,
    require_mode_decoder_supported,
    require_supported_client_version,
)
from scene_coaching import attach_scene_coaching  # noqa: E402


@dataclass(frozen=True)
class AnalysisConfig:
    game_id: int
    replay_path: Path
    inspect_path: Path
    enum_path: Path
    spawn_path: Path
    game_data_path: Path
    out_dir: Path
    schema_path: Path = ROOT / "schema" / "schema.json"
    map_image_path: Path = ROOT / "data" / "satellite_map.png"
    map_meta_path: Path = ROOT / "data" / "map_meta.json"
    restriction_area_shapes_path: Path = ROOT / "data" / "lumia_restriction_areas.json"
    names_path: Path = ROOT / "data" / "names.json"
    names_supplement_path: Path = ROOT / "data" / "character_names_supplement.json"
    source_root: Path = ROOT
    replay_source_label: str | None = None
    requested_skill_cache_path: Path | None = None
    requested_skill_identity_path: Path | None = None
    full_decode_path: Path | None = None

    @property
    def html_path(self) -> Path:
        return self.out_dir / "combat-analysis.html"

    @property
    def json_path(self) -> Path:
        return self.out_dir / "combat-analysis.json"

    def validate(self) -> None:
        if (self.requested_skill_cache_path is None)!=(self.requested_skill_identity_path is None):
            raise ValueError('requested skill cache and private player map must be provided together')
        for path in (self.requested_skill_cache_path,self.requested_skill_identity_path):
            if path is not None and not path.is_file():raise FileNotFoundError(path)
        if self.game_id <= 0:
            raise ValueError("game_id must be a positive integer")
        required = {
            "replay": self.replay_path,
            "inspect": self.inspect_path,
            "enum catalog": self.enum_path,
            "spawn snapshot report": self.spawn_path,
            "game data": self.game_data_path,
            "schema": self.schema_path,
            "map image": self.map_image_path,
            "map metadata": self.map_meta_path,
            "Lumia restriction area shapes": self.restriction_area_shapes_path,
            "names": self.names_path,
            "names supplement": self.names_supplement_path,
        }
        for label, path in required.items():
            if not path.is_file():
                raise FileNotFoundError(f"{label} input is missing: {path}")
        if self.replay_source_label is not None:
            logical = Path(self.replay_source_label)
            if (
                not self.replay_source_label.strip()
                or logical.is_absolute()
                or ".." in logical.parts
                or "\n" in self.replay_source_label
                or "\r" in self.replay_source_label
            ):
                raise ValueError("replay_source_label must be a safe relative logical path")

    def source_label(self, path: Path) -> str:
        try:
            return str(path.resolve().relative_to(self.source_root.resolve()))
        except ValueError:
            # Temporary acquisition directories must never leak into persisted output.
            return path.name

    def replay_label(self) -> str:
        if self.replay_source_label is None:
            return self.source_label(self.replay_path)
        # Keep logical paths deterministic on the host even if the caller used
        # the other slash style on the command line.
        return str(Path(self.replay_source_label))


WRAPPER_CODES = {
    "ignoreOrderPackets": 0,
    "commands": 1,
    "itemBoxPackets": 2,
    "clientPackets": 3,
}

# The event array prefix is: tick, selected-event index, event type code,
# wrapper category code, wrapper index. Remaining values follow fields order.
EVENT_SPECS = {
    "CmdUpdateInCombatType": (0, ["objectId", "inCombatType"]),
    "CmdStartSkill": (
        1,
        ["objectId", "skillId", "skillCode", "skillEvolutionLevel", "targetObjectId"],
    ),
    "CmdFinishSkill": (2, ["objectId", "skillId", "reason", "skillSlotSet"]),
    "CmdPlaySkillAction": (3, ["objectId", "skillId", "casterId", "actionNo"]),
    "CmdPlaySkillActionWithTargets": (
        35,
        ["objectId", "skillId", "casterId", "actionNo", "targets"],
    ),
    "CmdPlayStateSkillAction": (
        36,
        [
            "objectId", "skillId", "casterId", "actionNo",
            "stateCasterId", "stateGroup", "targets",
        ],
    ),
    "CmdStartCharacterSkillCooldown": (
        4,
        ["objectId", "skillSlotSet", "cooldown", "cooldownMax", "curSkillStack"],
    ),
    "CmdModifyCharacterSkillCooldown": (
        5,
        ["objectId", "skillSlotSet", "rusultCooldown", "maxCooldown", "curSkillStack"],
    ),
    "CmdCopyCharacterSkillCooldown": (6, ["objectId", "skillSlotSet", "from"]),
    "CmdHoldSkillCooldown": (
        7,
        ["objectId", "skillSlotSet", "masteryType", "isHold"],
    ),
    "CmdClearCharacterCooldown": (8, ["objectId"]),
    "CmdDamage": (
        9,
        [
            "objectId", "attackerId", "damage", "curHp", "isCritical",
            "damageFontDisplayType", "effectCode", "damageType",
        ],
    ),
    "CmdHeal": (
        10,
        ["objectId", "addHp", "addVp", "effectCode", "uiDamageType", "showUI", "casterId"],
    ),
    "CmdHealStateCode": (
        11,
        [
            "objectId", "addHp", "addVp", "stateCode", "effectCode",
            "uiDamageType", "showUI", "casterId",
        ],
    ),
    "CmdBlock": (12, ["objectId", "damage", "attackerId"]),
    "CmdEvasion": (13, ["objectId"]),
    "CmdKill": (14, ["objectId", "deadCharacterObjectId", "assistCharacterObjectIds"]),
    "CmdDead": (
        15,
        [
            "objectId", "finishingAttackerObjectId", "finishingAttackerCharacterCode",
            "isDyingBlockDead",
        ],
    ),
    "CmdDyingCondition": (16, ["objectId", "hp", "vp"]),
    "CmdResurrection": (17, ["objectId", "survivalTime", "inventoryCount", "equipmentCount"]),
    "CmdCrowdControl": (18, ["objectId", "stateType"]),
    "CmdStartNormalAttackSkill": (
        19,
        ["objectId", "skillCode", "isCritical", "targetObjectId"],
    ),
    "CmdStartPassiveSkill": (
        20,
        ["objectId", "skillCode", "skillEvolutionLevel", "targetObjectId"],
    ),
    "CmdStartStateSkill": (
        21,
        ["objectId", "skillId", "skillCode", "skillEvolutionLevel", "casterId"],
    ),
    "CmdUpdateEquipment": (22, ["objectId", "updates"]),
    "CmdUpdateInventoryForObserver": (
        23,
        ["objectId", "updates", "updateType"],
    ),
    "CmdStartWeaponSkillCooldown": (
        24,
        ["objectId", "masteryType", "cooldown", "cooldownMax", "curSkillStack"],
    ),
    "CmdModifyWeaponSkillCooldown": (
        25,
        [
            "objectId", "masteryType", "rusultCooldown", "maxCooldown",
            "curSkillStack", "isUpdateHud",
        ],
    ),
    "CmdUpdateShield": (
        26,
        [
            "objectId",
            "blockAllShieldAmount",
            "blockNormalShieldAmount",
            "blockSkillShieldAmount",
        ],
    ),
    "CmdAddState": (
        27,
        ["objectId", "code", "duration", "casterId", "originalDuration"],
    ),
    "CmdAddStateExtended": (
        28,
        [
            "objectId", "code", "duration", "casterId", "originalDuration",
            "stackCount", "power",
        ],
    ),
    "CmdUpdateState": (
        29,
        [
            "objectId", "group", "casterId", "stackCount", "reserveCount",
            "duration", "createdTime",
        ],
    ),
    "CmdResetCreateTimeState": (
        30,
        ["objectId", "group", "casterId", "duration"],
    ),
    "CmdRemoveState": (31, ["objectId", "group", "casterId"]),
    "CmdPauseState": (
        32,
        ["objectId", "group", "casterId", "durationPauseEndTime", "duration"],
    ),
    "CmdUpdateResourceBoxCooldown": (
        33,
        ["objectId", "cooldown", "spawnDate", "remainCollectCount"],
    ),
    "CmdProjectileCollision": (34, ["objectId", "targetId"]),
}

PACKET_KOREAN_LABELS = {
    "CmdUpdateInCombatType": "전투 상태 변경",
    "CmdStartSkill": "스킬 시전 시작",
    "CmdFinishSkill": "스킬 종료",
    "CmdPlaySkillAction": "스킬 동작 실행",
    "CmdPlaySkillActionWithTargets": "대상 지정 스킬 동작 실행",
    "CmdPlayStateSkillAction": "상태 기반 대상 스킬 동작 실행",
    "CmdStartCharacterSkillCooldown": "스킬 쿨다운 시작",
    "CmdModifyCharacterSkillCooldown": "스킬 쿨다운 변경",
    "CmdCopyCharacterSkillCooldown": "스킬 쿨다운 복사",
    "CmdHoldSkillCooldown": "스킬 쿨다운 보류",
    "CmdClearCharacterCooldown": "캐릭터 쿨다운 초기화",
    "CmdDamage": "피해",
    "CmdHeal": "회복",
    "CmdHealStateCode": "상태 기반 회복",
    "CmdBlock": "피해 방어",
    "CmdEvasion": "회피",
    "CmdKill": "처치·다운 귀속",
    "CmdDead": "사망 확정",
    "CmdDyingCondition": "다운",
    "CmdResurrection": "부활",
    "CmdCrowdControl": "군중 제어",
    "CmdStartNormalAttackSkill": "기본 공격 시작",
    "CmdStartPassiveSkill": "패시브 발동",
    "CmdStartStateSkill": "상태 스킬 시작",
    "CmdUpdateEquipment": "장비 변경",
    "CmdUpdateInventoryForObserver": "인벤토리 변경",
    "CmdStartWeaponSkillCooldown": "무기 스킬 쿨다운 시작",
    "CmdModifyWeaponSkillCooldown": "무기 스킬 쿨다운 변경",
    "CmdUpdateShield": "보호막 변경",
    "CmdAddState": "상태 효과 시작",
    "CmdAddStateExtended": "상태 효과 시작·중첩",
    "CmdUpdateState": "상태 효과 갱신",
    "CmdResetCreateTimeState": "상태 효과 기준 시각 변경",
    "CmdRemoveState": "상태 효과 종료",
    "CmdPauseState": "상태 효과 시간 정지",
    "CmdUpdateResourceBoxCooldown": "자원 채집 상태 갱신",
    "CmdProjectileCollision": "투사체 충돌",
}

FIXED_POINT_FIELDS = {
    "cooldown",
    "cooldownMax",
    "rusultCooldown",
    "maxCooldown",
    "survivalTime",
    "duration",
    "originalDuration",
    "createdTime",
    "durationPauseEndTime",
}

REDACTED_RESULT_STRING_FIELDS = {
    "nickname",
    "userTempName",
    "killerName",
    "gimmickEvidenceLockerCount",
    "gimmickEvidenceLockerItem",
}

MOVEMENT_PACKETS = {
    "CmdWarpTo",
    "CmdStopMove",
    "CmdVLSHorizontalMove",
    "CmdMoveStraight",
    "CmdMoveToDestination",
    "CmdMoveToDestinationAvoidance",
    "CmdMoveJump",
    "CmdMoveToStick",
    "CmdMoveToStickLocalPositionChange",
    "CmdMoveVLS",
    "CmdMoveVLSToStick",
    "CmdMoveStraightWithoutNav",
    "CmdActiveHyperLoopExit",
    "CmdMoveByDirection",
    "CmdMoveVLSToStickSeparated",
}

WORLD_OBJECT_CATEGORIES = {
    18: "control-lens",
    19: "cctv",
    20: "hyperloop",
    34: "kiosk",
    42: "campfire",
    46: "kiosk",
    48: "vls",
    49: "vls",
    51: "hyperloop",
    52: "meteor",
    53: "rift",
    54: "rift-warning",
    57: "tree-of-life",
}


def summon_world_category(
    object_type: int | None,
    summon_id: object,
    world_game_data: dict,
) -> str | None:
    """Classify visible player-placed summons from the exact SummonObject table.

    ObjectType 9 is SummonCamera. Replay-version game data separates physical,
    targetable CommonObject cameras from character-created sight helpers that
    merely share TelephotoCamera stats. Recon/EMP drone rows with showMiniMap use
    the control-lens layer. Environment lantern flowers stay unclassified.
    """
    if not isinstance(summon_id, int):
        return None
    if object_type == 9 and summon_id in world_game_data.get("cameraSummonIds", ()):
        return "surveillance-camera"
    if object_type == 9 and summon_id in world_game_data.get("droneSummonIds", ()):
        return "control-lens"
    if object_type == 10 and summon_id in world_game_data.get("reconOrbSummonIds", ()):
        return "recon-orb"
    return None

AREA_RESTRICTION_STATE_NAMES = {
    0: "None",
    1: "Normal",
    2: "Reserved",
    3: "Clearing",
    4: "Restricted",
    5: "ReservedClearing",
}

OBJECT_TIMELINE_CATEGORIES = {
    1: "tree-of-life",
    2: "meteor",
    3: "alpha",
    4: "omega",
    5: "wickeline",
    6: "air-supply-epic",
    7: "air-supply-mythic",
    8: "rift",
    9: "rift",
}

# Exact 12.2 metadata enum values. Epic/Mythic are also checked against the
# same-replay ObjectTimeline correlation before an air-supply row is emitted.
AIR_SUPPLY_GRADE_CATEGORIES = {
    3: "air-supply-rare",
    4: "air-supply-epic",
    5: "air-supply-legend",
}

WILDLIFE_BASE_ASSET_KEYS = {
    1: "chicken",
    2: "bat",
    3: "boar",
    4: "wild-dog",
    5: "wolf",
    6: "bear",
    18: "raven",
    20: "bat",
    21: "bori",
    22: "bori-box",
}
MUTATED_MONSTER_CODES = frozenset({12, 13, 14, 15, 16, 17, 19, 23, 24, 25, 26, 27, 28})
MUTATED_BASE_CODES = {
    12: 1, 13: 2, 14: 3, 15: 4, 16: 5, 17: 6, 19: 18,
    23: 1, 24: 2, 25: 3, 26: 4, 27: 5, 28: 6,
}
SPECIAL_MOBILE_MONSTER_CODES = frozenset({21, 22})
SPECIAL_MONSTER_DISPLAY_NAMES = {21: "보리", 22: "보리 상자"}

# User-confirmed display rules. These never become replay-native recipient
# truth because CmdNoise has no recipient field.
NOISE_VISIBILITY_RULES = {
    "AirSupplyOpen": {"label": "보급 상자 개봉", "radiusMeters": None, "cooldownSeconds": 0},
    "EvidenceItemBoxOpen": {"label": "상자 개봉 (음식·영웅·초월)", "radiusMeters": None, "cooldownSeconds": 0},
    "HyperLoopExit": {"label": "하이퍼루프 도착", "radiusMeters": None, "cooldownSeconds": 0},
    "VLSLanding": {"label": "VLS 착지", "radiusMeters": None, "cooldownSeconds": 0},
    "FlareGunTarget": {"label": "플레어건", "radiusMeters": 100, "cooldownSeconds": 0},
    "MonsterKilled": {"label": "야생동물 처치", "radiusMeters": 80, "cooldownSeconds": 10},
    "MobileSafetyAreaDestroy": {"label": "휴대용 안전지대 파괴", "radiusMeters": 80, "cooldownSeconds": 0},
    "KioskStartSafe": {"label": "키오스크 사용", "radiusMeters": 50, "cooldownSeconds": 0},
    "KioskItemMakingSafe": {"label": "키오스크 제작", "radiusMeters": 50, "cooldownSeconds": 0},
    "KioskCompleteSafe": {"label": "키오스크 완료", "radiusMeters": 50, "cooldownSeconds": 0},
    "KioskSupplyBoxOpenSafe": {"label": "키오스크 상자 개봉", "radiusMeters": 50, "cooldownSeconds": 0},
    "KioskStartRestrict": {"label": "키오스크 사용", "radiusMeters": 50, "cooldownSeconds": 0},
    "KioskItemMakingRestrict": {"label": "키오스크 제작", "radiusMeters": 50, "cooldownSeconds": 0},
    "KioskCompleteRestrict": {"label": "키오스크 완료", "radiusMeters": 50, "cooldownSeconds": 0},
    "KioskSupplyBoxOpenRestrict": {"label": "키오스크 상자 개봉", "radiusMeters": 50, "cooldownSeconds": 0},
    "Crafting": {"label": "아이템 제작", "radiusMeters": 30, "cooldownSeconds": 0},
    "CompleteWeaponCraft": {"label": "장비 제작 완료", "radiusMeters": 30, "cooldownSeconds": 0},
    "BoriCry": {"label": "보리 최초 도망", "radiusMeters": "unknown", "cooldownSeconds": 0},
    "BoriSupplyOpen": {"label": "보리 상자 개봉", "radiusMeters": "unknown", "cooldownSeconds": 0},
}


def wildlife_asset_key(monster_code: int) -> tuple[str | None, bool]:
    normalized = monster_code - 100 if 101 <= monster_code <= 128 else monster_code
    mutated = normalized in MUTATED_MONSTER_CODES
    base_code = MUTATED_BASE_CODES.get(normalized, normalized)
    base_key = WILDLIFE_BASE_ASSET_KEYS.get(base_code)
    if base_key is None:
        return None, mutated
    return (f"mutant-{base_key}" if mutated else base_key), mutated


def noise_visibility_rule(noise_type_name: str) -> dict | None:
    rule = NOISE_VISIBILITY_RULES.get(noise_type_name)
    return dict(rule) if rule is not None else None


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def internal_value(value):
    if isinstance(value, dict) and "internalValue" in value:
        return value["internalValue"]
    return value


def restriction_area_rows(value: dict, enum_values: dict[str, dict[int, str]]) -> list[dict]:
    """Project exact Lumia area-state pairs without secret/rift-area substitution."""
    rows = []
    for pair in value.get("areaStateMap") or []:
        if not (
            isinstance(pair, list)
            and len(pair) == 2
            and isinstance(pair[0], int)
            and isinstance(pair[1], dict)
        ):
            continue
        area_code, area_state = pair
        state = area_state.get("areaRestrictionState")
        if not isinstance(state, int):
            continue
        rows.append({
            "areaCode": area_code,
            "state": state,
            "stateName": enum_values.get("AreaRestrictionState", {}).get(
                state, AREA_RESTRICTION_STATE_NAMES.get(state, f"Unknown{state}")
            ),
            "wireStatus": "decoded-exact-lumia-area-restriction-state",
        })
    return sorted(rows, key=lambda row: row["areaCode"])


def vector2(value, scale: float = 1.0):
    if (
        isinstance(value, list)
        and len(value) == 2
        and all(isinstance(item, (int, float)) for item in value)
    ):
        return [round(value[0] / scale, 5), round(value[1] / scale, 5)]
    return None


def movement_anchors(packet_name: str, tick: int, value: dict) -> list[list]:
    anchors = []

    def add(at_tick: int, position, source: str):
        if position is not None:
            anchors.append([int(at_tick), position[0], position[1], source])

    if packet_name in {
        "CmdMoveToDestination",
        "CmdMoveToDestinationAvoidance",
        "CmdVLSHorizontalMove",
        "CmdMoveToStick",
        "CmdMoveToStickLocalPositionChange",
        "CmdMoveVLS",
        "CmdMoveVLSToStick",
        "CmdMoveByDirection",
        "CmdMoveVLSToStickSeparated",
    }:
        add(tick, vector2(value.get("positionVector2"), 100), packet_name)
    elif packet_name == "CmdStopMove":
        add(tick, vector2(value.get("positionVector2")), packet_name)
    elif packet_name in {"CmdWarpTo", "CmdActiveHyperLoopExit"}:
        add(tick, vector2(value.get("destinationVector2")), packet_name)
    elif packet_name in {"CmdMoveStraight", "CmdMoveJump"}:
        add(tick, vector2(value.get("positionVector2"), 100), packet_name)
        duration = internal_value(value.get("duration"))
        if isinstance(duration, int):
            add(
                tick + round(duration / 100 * 60),
                vector2(value.get("destinationVector2")),
                packet_name + ":destination",
            )
    elif packet_name == "CmdMoveStraightWithoutNav":
        add(tick, vector2(value.get("startPosVector2")), packet_name)
        duration = internal_value(value.get("duration"))
        if isinstance(duration, int):
            add(
                tick + round(duration / 100 * 60),
                vector2(value.get("endPosVector2")),
                packet_name + ":destination",
            )
    return anchors


def planned_path(packet_name: str, tick: int, value: dict) -> list | None:
    if packet_name not in {
        "CmdMoveToDestination",
        "CmdMoveToDestinationAvoidance",
    }:
        return None
    start = vector2(value.get("positionVector2"), 100)
    corners = [
        point
        for raw in (value.get("cornersVector2") or [])
        if (point := vector2(raw, 100)) is not None
    ]
    if start is None or not corners:
        return None
    return [tick, packet_name, [start, *corners]]


def encode_event(
    name: str,
    tick: int,
    event_index: int,
    wrapper_category: str,
    wrapper_index: int,
    value: dict,
) -> list:
    code, fields = EVENT_SPECS[name]
    if name == "CmdResurrection":
        value = {
            **value,
            "inventoryCount": len(value.get("inventoryItems") or []),
            "equipmentCount": len(value.get("equipmentItems") or []),
        }
    encoded = [tick, event_index, code, WRAPPER_CODES[wrapper_category], wrapper_index]
    for field in fields:
        item = value.get(field)
        if field in FIXED_POINT_FIELDS:
            item = internal_value(item)
        encoded.append(item)
    return encoded


def event_field(event: list, event_defs: dict[int, dict], name: str):
    fields = event_defs[event[2]]["fields"]
    try:
        return event[5 + fields.index(name)]
    except ValueError:
        return None


def compact_item_updates(updates: object, *, slot_field: str) -> list[list]:
    if not isinstance(updates, list):
        raise ValueError("item update payload must be a list")
    compact = []
    for update in updates:
        if not isinstance(update, dict):
            raise ValueError("item update row must be an object")
        slot = update.get(slot_field)
        if not isinstance(slot, int) or slot < 0:
            raise ValueError("item update slot must be a non-negative integer")
        item = update.get("item")
        if item is None:
            compact.append([slot, None, 0])
            continue
        if not isinstance(item, dict):
            raise ValueError("item update item must be an object or null")
        item_code = item.get("itemCode")
        amount = item.get("Amount")
        if not isinstance(item_code, int) or item_code <= 0:
            raise ValueError("item update itemCode must be a positive integer")
        if not isinstance(amount, int) or amount < 0:
            raise ValueError("item update Amount must be a non-negative integer")
        compact.append([slot, item_code, amount])
    return compact


def enum_maps(config: AnalysisConfig) -> tuple[dict[str, dict[int, str]], dict[str, str]]:
    report = json.loads(config.enum_path.read_text(encoding="utf-8"))
    wanted = {
        "DamageType", "InCombatType", "SkillId", "SkillSlotSet",
        "SkillStopReason", "StateType", "NoiseType", "TacticalPingType",
        "DayNight", "GamePlayPhase", "ObjectType", "ObjectTimelineType",
        "MonsterType", "RiftState", "VerticalLauncherState", "StatType",
    }
    maps = {}
    statuses = {}
    for enum in report["catalog"]:
        schema_type = enum["schemaType"]
        if schema_type not in wanted:
            continue
        maps[schema_type] = {
            field["default"]["compressedInt32"]: field["name"]
            for field in enum["selected"]["fields"]
            if field["name"] != "value__" and field.get("default") is not None
        }
        statuses[schema_type] = enum["status"]
    return maps, statuses


def load_game_data(
    config: AnalysisConfig,
) -> tuple[dict[int, dict], dict[int, dict], dict[int, dict], dict[int, dict], dict]:
    with zipfile.ZipFile(config.game_data_path) as archive:
        hash_catalog = json.loads(archive.read("hash.json"))
        characters = {
            row["code"]: row
            for row in json.loads(archive.read("Character.json"))
        }
        skills = {
            row["code"]: row
            for row in json.loads(archive.read("Skill.json"))
        }
        groups = {
            row["group"]: row
            for row in json.loads(archive.read("SkillGroup.json"))
        }
        item_tables = (
            "ItemWeapon.json",
            "ItemArmor.json",
            "ItemConsumable.json",
            "ItemSpecial.json",
            "ItemMisc.json",
        )
        items = {}
        for table_name in item_tables:
            for row in json.loads(archive.read(table_name)):
                code = row.get("code")
                if isinstance(code, int):
                    items[code] = {
                        "code": code,
                        "itemType": row.get("itemType"),
                        "subType": (
                            row.get("weaponType")
                            or row.get("armorType")
                            or row.get("consumableType")
                            or row.get("specialItemType")
                            or row.get("miscItemType")
                        ),
                        "itemGrade": row.get("itemGrade"),
                        "stackable": row.get("stackable"),
                        "isCompletedItem": row.get("isCompletedItem"),
                        "sourceTable": table_name,
                    }
    return characters, skills, groups, items, {
        "tableCount": len(hash_catalog),
        "status": "exact-replay-version-game-data",
        "usedTables": [
            "Character.json",
            "Skill.json",
            "SkillGroup.json",
            *item_tables,
        ],
    }


def load_world_game_data(config: AnalysisConfig) -> dict:
    """Load only version-bound labels needed to classify replay world objects."""
    with zipfile.ZipFile(config.game_data_path) as archive:
        summon_objects = {
            row["code"]: row
            for row in json.loads(archive.read("SummonObject.json"))
            if isinstance(row.get("code"), int)
        }
        monsters = {}
        for row in json.loads(archive.read("Monster.json")):
            code = row.get("code")
            if not isinstance(code, int):
                continue
            # The table can contain mode-specific duplicates. The monster kind
            # is invariant for a code; fail closed if the archive disagrees.
            previous = monsters.get(code)
            if previous is not None and previous.get("monster") != row.get("monster"):
                raise ValueError(f"Monster.json code {code} has conflicting monster kinds")
            monsters[code] = row
        touring_objects = [
            row
            for row in json.loads(archive.read("TouringObjectData.json"))
            if isinstance(row.get("code"), int)
        ]
    camera_summon_ids = {
        code
        for code, row in summon_objects.items()
        if row.get("objectType") == "SummonCamera"
        and row.get("objectStatsType") == "TelephotoCamera"
        and row.get("showMiniMap") is True
        # Character-created sight helpers (for example Watcher's Eye - Bat)
        # share TelephotoCamera stats but are not placeable camera objects.
        # The replay-version table distinguishes physical cameras by both
        # targetability and CommonObject victim semantics.
        and row.get("isPickingTarget") is True
        and row.get("summonVictimType") == "CommonObject"
    }
    drone_summon_ids = {
        code
        for code, row in summon_objects.items()
        if row.get("objectType") == "SummonCamera"
        and row.get("objectStatsType") in {"ReconDrone", "EMPDrone"}
        and row.get("showMiniMap") is True
    }
    recon_orb_summon_ids = {
        code
        for code, row in summon_objects.items()
        if row.get("objectType") == "SummonTrap"
        and row.get("objectStatsType") == "Orb"
        and row.get("showMiniMap") is True
    }
    guide_robot_codes = {
        row["code"]
        for row in touring_objects
        if row.get("touringObjectType") == "GuideRobot"
        and row.get("showMapIcon") is True
    }
    if not guide_robot_codes:
        raise ValueError("TouringObjectData.json has no visible GuideRobot code")
    return {
        "summonObjects": summon_objects,
        "monsters": monsters,
        "cameraSummonIds": camera_summon_ids,
        "droneSummonIds": drone_summon_ids,
        "reconOrbSummonIds": recon_orb_summon_ids,
        "guideRobotCodes": guide_robot_codes,
        "status": "exact-replay-version-game-data",
    }


_INNER_SNAPSHOT_MISS = object()


class ExactInnerSnapshotCache:
    """Reuse exact nested snapshot decodes for identical payload bytes.

    Callers must not mutate returned dictionaries. Failed guide-robot probes
    are remembered as None so the same non-Lumi bytes are not retried.
    """

    def __init__(self, decoder: SchemaDecoder, guide_robot_codes: set[int], *, max_entries: int = 4096):
        if type(max_entries) is not int or max_entries < 1:
            raise ValueError("snapshot cache capacity must be a positive integer")
        self.decoder = decoder
        self.guide_robot_codes = frozenset(guide_robot_codes)
        self.max_entries = max_entries
        self._candidates = OrderedDict()
        self._guide = OrderedDict()

    def _store(self, values, key, value):
        values[key] = value
        if len(values) > self.max_entries:
            values.popitem(last=False)

    def decode_candidate(self, payload, candidate_type: str):
        if isinstance(payload, bytearray):
            payload = bytes(payload)
        key = (payload, candidate_type)
        cached = self._candidates.get(key, _INNER_SNAPSHOT_MISS)
        if cached is not _INNER_SNAPSHOT_MISS:
            self._candidates.move_to_end(key)
            return cached
        value = decode_candidate_exact(self.decoder, payload, candidate_type)
        self._store(self._candidates, key, value)
        return value

    def decode_guide_robot(self, payload):
        if not isinstance(payload, (bytes, bytearray)):
            return None
        if isinstance(payload, bytearray):
            payload = bytes(payload)
        cached = self._guide.get(payload, _INNER_SNAPSHOT_MISS)
        if cached is not _INNER_SNAPSHOT_MISS:
            self._guide.move_to_end(payload)
            return cached
        result = decode_exact_guide_robot_snapshot(
            self.decoder,
            payload,
            self.guide_robot_codes,
            cache=self,
        )
        self._store(self._guide, payload, result)
        return result


def decode_exact_guide_robot_snapshot(
    decoder: SchemaDecoder,
    payload: bytes | bytearray | None,
    guide_robot_codes: set[int],
    *,
    cache: ExactInnerSnapshotCache | None = None,
) -> tuple[dict, dict] | None:
    """Decode Lumi without relying on an ambiguous ObjectType name mapping."""
    if not isinstance(payload, (bytes, bytearray)):
        return None
    payload_bytes = payload if isinstance(payload, bytes) else bytes(payload)
    decode = cache.decode_candidate if cache is not None else decode_candidate_exact
    try:
        touring = decode(decoder, payload_bytes, "TouringObjectSnapshot") if cache is None else decode(
            payload_bytes, "TouringObjectSnapshot"
        )
    except (DecodeError, IndexError, KeyError, struct.error, UnicodeError):
        return None
    if isinstance(touring, dict) and "value" in touring and "concreteType" in touring:
        touring = touring["value"]
    if (
        not isinstance(touring, dict)
        or touring.get("touringObjectCode") not in guide_robot_codes
    ):
        return None
    script_snapshot = touring.get("scriptSnapshot")
    if not isinstance(script_snapshot, (bytes, bytearray)):
        raise ValueError("GuideRobot TouringObjectSnapshot has no scriptSnapshot")
    try:
        guide_state = (
            decode(decoder, bytes(script_snapshot), "GuideRobotSnapshot")
            if cache is None
            else decode(
                script_snapshot if isinstance(script_snapshot, bytes) else bytes(script_snapshot),
                "GuideRobotSnapshot",
            )
        )
    except (DecodeError, IndexError, KeyError, struct.error, UnicodeError) as error:
        raise ValueError("GuideRobotSnapshot did not decode exactly") from error
    if (
        isinstance(guide_state, dict)
        and "value" in guide_state
        and "concreteType" in guide_state
    ):
        guide_state = guide_state["value"]
    if not isinstance(guide_state, dict):
        raise ValueError("GuideRobotSnapshot did not decode exactly")
    return touring, guide_state


def skill_family(name: str | None, skill_code: int | None = None) -> str:
    # Replay-version Skill.json / SkillGroup.json reserve the 3,000,000 range
    # for weapon actives (PistolActive, OneHandSwordActive, GloveActive, ...).
    # Their SkillId names do not contain the literal string "WeaponSkill", so
    # the exact skillCode is the authoritative discriminator for this family.
    if isinstance(skill_code, int) and 3_000_000 <= skill_code < 4_000_000:
        return "WeaponSkill"
    if not name:
        return "Other"
    for family in ("Active1", "Active2", "Active3", "Active4"):
        if family in name:
            return family
    if "WeaponSkill" in name:
        return "WeaponSkill"
    if "Tactical" in name:
        return "TacticalSkill"
    if "Gadget" in name:
        return "GadgetSkill"
    if "NormalAttack" in name or name.startswith("Attack_"):
        return "NormalAttack"
    if "Passive" in name:
        return "Passive"
    return "Other"


def overlap_ticks(intervals: list[list[int]], start: int, end: int) -> int:
    return sum(max(0, min(right, end) - max(left, start)) for left, right, *_ in intervals)


def merge_intervals(intervals: list[list[int]]) -> list[list[int]]:
    merged: list[list[int]] = []
    for start, end in sorted(intervals):
        if end <= start:
            continue
        if not merged or start > merged[-1][1]:
            merged.append([start, end])
        else:
            merged[-1][1] = max(merged[-1][1], end)
    return merged


def build_combat_intervals(events: list[list], pid: int, event_defs: dict[int, dict], end_tick: int):
    transitions = [
        event for event in events
        if event[2] == 0 and event_field(event, event_defs, "objectId") == pid
    ]
    intervals = []
    start = None
    max_level = 0
    for event in transitions:
        tick = event[0]
        level = event_field(event, event_defs, "inCombatType") or 0
        if level != 0 and start is None:
            start = tick
            max_level = level
        elif level != 0:
            max_level = max(max_level, level)
        elif start is not None:
            if tick > start:
                intervals.append([start, tick, max_level])
            start = None
            max_level = 0
    if start is not None and end_tick > start:
        intervals.append([start, end_tick, max_level])
    return intervals


def normalize_simultaneous_combat_entries(players: list[dict]) -> None:
    """Reclassify near-same team combat entries without changing exact ticks."""

    grouped_entries: dict[tuple[int, int, int, int], list[tuple[dict, dict]]] = (
        defaultdict(list)
    )
    team_players: dict[int, list[dict]] = defaultdict(list)
    for player in players:
        team_number = player["teamNumber"]
        team_players[team_number].append(player)
        for row in player.get("combatJudgment", {}).get("episodes", []):
            grouped_entries[
                (
                    team_number,
                    row["teamEpisodeNumber"],
                    row["startTick"],
                    row["endTick"],
                )
            ].append((player, row))

    for entries in grouped_entries.values():
        first_tick = min(row["entryTick"] for _, row in entries)
        simultaneous = [
            row
            for _, row in entries
            if row["entryTick"] - first_tick <= SIMULTANEOUS_ENTRY_WINDOW_TICKS
        ]
        for _, row in entries:
            delay_ticks = row["entryTick"] - first_tick
            is_simultaneous = delay_ticks <= SIMULTANEOUS_ENTRY_WINDOW_TICKS
            if is_simultaneous:
                row["entryRole"] = (
                    "co-initiator" if len(simultaneous) > 1 else "initiator"
                )
                row["soloEntry"] = len(simultaneous) == 1
                row["reinforcementDelaySeconds"] = None
            else:
                row["entryRole"] = "joiner"
                row["soloEntry"] = False
                row["reinforcementDelaySeconds"] = None
            row["joinDelaySeconds"] = round(delay_ticks / 60, 3)

        if len(simultaneous) == 1:
            first_row = simultaneous[0]
            later_ticks = [
                row["entryTick"]
                for _, row in entries
                if row is not first_row
            ]
            first_row["reinforcementDelaySeconds"] = (
                round((min(later_ticks) - first_tick) / 60, 3)
                if later_ticks
                else None
            )

    for members in team_players.values():
        for member in members:
            judgment = member["combatJudgment"]
            rows = judgment["episodes"]
            initiations = [row for row in rows if row["entryRole"] != "joiner"]
            joiners = [row for row in rows if row["entryRole"] == "joiner"]
            solo_rows = [row for row in rows if row["soloEntry"]]
            reinforcement_rows = [
                row
                for row in initiations
                if row["reinforcementDelaySeconds"] is not None
            ]
            judgment["initiationCount"] = len(initiations)
            judgment["initiationRate"] = (
                round(len(initiations) / len(rows), 4) if rows else None
            )
            judgment["soloInitiationCount"] = len(solo_rows)
            judgment["soloInitiationRate"] = (
                round(len(solo_rows) / len(initiations), 4)
                if initiations
                else None
            )
            judgment["meanJoinDelaySeconds"] = (
                round(
                    sum(row["joinDelaySeconds"] for row in joiners)
                    / len(joiners),
                    3,
                )
                if joiners
                else None
            )
            judgment["meanReinforcementDelaySeconds"] = (
                round(
                    sum(
                        row["reinforcementDelaySeconds"]
                        for row in reinforcement_rows
                    )
                    / len(reinforcement_rows),
                    3,
                )
                if reinforcement_rows
                else None
            )
            judgment["definitions"]["simultaneousEntry"] = (
                f"팀 최초 진입 뒤 {SIMULTANEOUS_ENTRY_WINDOW_SECONDS:.2f}초 "
                "이내에 시작된 exact 전투 상태"
            )
            judgment["definitions"]["soloEntry"] = (
                f"본인이 팀 최초 진입자이며 {SIMULTANEOUS_ENTRY_WINDOW_SECONDS:.2f}초 "
                "안에 함께 진입한 팀원이 없음"
            )

        max_initiations = max(
            member["combatJudgment"]["initiationCount"] for member in members
        )
        max_solo = max(
            member["combatJudgment"]["soloInitiationCount"] for member in members
        )
        for member in members:
            judgment = member["combatJudgment"]
            if max_solo > 0 and judgment["soloInitiationCount"] == max_solo:
                label = "혼자서도 문을 여는 선봉형"
            elif max_initiations > 0 and judgment["initiationCount"] == max_initiations:
                label = "팀 교전의 문을 여는 선봉형"
            elif judgment["meanJoinDelaySeconds"] is not None:
                label = "호출에 반응하는 합류형"
            else:
                label = "상황을 고르는 대응형"
            judgment["styleLabel"] = label
            judgment["styleAuthority"] = "derived-team-relative-v1"


def attach_combat_judgment(
    players: list[dict],
    events: list[list],
    event_defs: dict[int, dict],
    resolved_player,
) -> dict:
    """Attach deterministic team-fight decision features to every player.

    A confirmed player engagement is one maximal interval covered continuously
    by at least one teammate's exact ``CmdUpdateInCombatType`` interval that
    itself contains enemy-player target selection, damage or block evidence.
    Both attacker/selector and enemy recipient qualify; unconfirmed spans do
    not extend or bridge team episodes. Original combat intervals are retained.
    Target actions use only exact player-targeted ``CmdStartSkill`` and
    ``CmdStartNormalAttackSkill`` packets; they are selections, not hit claims.
    A team-focus follow-up is another teammate selecting the same target within
    two replay seconds of the player's action.
    """

    player_by_id = {player["objectId"]: player for player in players}
    team_players: dict[int, list[dict]] = defaultdict(list)
    for player in players:
        team_players[player["teamNumber"]].append(player)

    target_actions: dict[int, list[tuple[int, int]]] = defaultdict(list)
    pvp_evidence_ticks: dict[int, set[int]] = defaultdict(set)
    for event in events:
        packet_name = event_defs[event[2]]["packetName"]
        target_selection = packet_name in {"CmdStartSkill", "CmdStartNormalAttackSkill"}
        if target_selection:
            actor = resolved_player(event_field(event, event_defs, "objectId"))
            target = resolved_player(event_field(event, event_defs, "targetObjectId"))
        elif packet_name in {"CmdDamage", "CmdBlock"}:
            actor = resolved_player(event_field(event, event_defs, "attackerId"))
            target = resolved_player(event_field(event, event_defs, "objectId"))
        else:
            continue
        if actor not in player_by_id or target not in player_by_id:
            continue
        if player_by_id[actor]["teamNumber"] == player_by_id[target]["teamNumber"]:
            continue
        pvp_evidence_ticks[actor].add(event[0])
        pvp_evidence_ticks[target].add(event[0])
        if target_selection:
            target_actions[actor].append((event[0], target))
    for rows in target_actions.values():
        rows.sort()

    followup_window_ticks = 2 * 60
    attached = 0
    total_team_episodes = 0
    for team_number, members in team_players.items():
        spans = sorted(
            (left, right, member["objectId"])
            for member in members
            for left, right, *_ in member["combatIntervals"]
            if right > left and any(left <= tick < right
                                    for tick in pvp_evidence_ticks[member["objectId"]])
        )
        episodes: list[dict] = []
        for left, right, pid in spans:
            if not episodes or left > episodes[-1]["endTick"]:
                episodes.append({
                    "startTick": left,
                    "endTick": right,
                    "spans": [[left, right, pid]],
                })
            else:
                episodes[-1]["endTick"] = max(episodes[-1]["endTick"], right)
                episodes[-1]["spans"].append([left, right, pid])
        total_team_episodes += len(episodes)

        for member in members:
            pid = member["objectId"]
            rows = []
            for team_episode_number, episode in enumerate(episodes, 1):
                own_spans = [
                    span for span in episode["spans"] if span[2] == pid
                ]
                if not own_spans:
                    continue
                entry_by_player = {
                    other_pid: min(
                        span[0]
                        for span in episode["spans"]
                        if span[2] == other_pid
                    )
                    for other_pid in {span[2] for span in episode["spans"]}
                }
                entry_tick = min(span[0] for span in own_spans)
                team_entry_tick = min(entry_by_player.values())
                entry_ties = [
                    other_pid
                    for other_pid, tick in entry_by_player.items()
                    if tick == team_entry_tick
                ]
                is_initiator = entry_tick == team_entry_tick
                active_teammates_at_entry = [
                    other_pid
                    for left, right, other_pid in episode["spans"]
                    if other_pid != pid and left <= entry_tick < right
                ]
                solo_entry = is_initiator and not active_teammates_at_entry
                other_entries = [
                    tick
                    for other_pid, tick in entry_by_player.items()
                    if other_pid != pid
                ]
                reinforcement_ticks = (
                    min(other_entries) - entry_tick
                    if is_initiator and other_entries
                    else None
                )
                died = any(
                    state == "dead"
                    and episode["startTick"] <= tick <= episode["endTick"]
                    for tick, state, *_ in member["lifeTimeline"]
                )
                actions = [
                    action
                    for action in target_actions.get(pid, [])
                    if episode["startTick"] <= action[0] < episode["endTick"]
                ]
                target_counts = Counter(target for _, target in actions)
                target_switches = sum(
                    target != previous
                    for (_, previous), (_, target) in zip(actions, actions[1:])
                )
                focus_followups = 0
                for tick, target in actions:
                    if any(
                        abs(other_tick - tick) <= followup_window_ticks
                        and other_target == target
                        for teammate in members
                        if teammate["objectId"] != pid
                        for other_tick, other_target in target_actions.get(
                            teammate["objectId"], []
                        )
                    ):
                        focus_followups += 1
                primary_target_id = (
                    target_counts.most_common(1)[0][0] if target_counts else None
                )
                primary_target = player_by_id.get(primary_target_id)
                rows.append({
                    "teamEpisodeNumber": team_episode_number,
                    "startTick": episode["startTick"],
                    "endTick": episode["endTick"],
                    "durationSeconds": round(
                        (episode["endTick"] - episode["startTick"]) / 60, 3
                    ),
                    "entryTick": entry_tick,
                    "entryRole": (
                        "co-initiator"
                        if is_initiator and len(entry_ties) > 1
                        else "initiator" if is_initiator else "joiner"
                    ),
                    "joinDelaySeconds": round(
                        (entry_tick - team_entry_tick) / 60, 3
                    ),
                    "reinforcementDelaySeconds": (
                        round(reinforcement_ticks / 60, 3)
                        if reinforcement_ticks is not None
                        else None
                    ),
                    "soloEntry": solo_entry,
                    "survived": not died,
                    "targetActionCount": len(actions),
                    "targetSwitchCount": target_switches,
                    "primaryTargetCharacterCode": (
                        primary_target["characterCode"] if primary_target else None
                    ),
                    "primaryTargetCharacterName": (
                        primary_target["characterName"] if primary_target else None
                    ),
                    "primaryTargetActionCount": (
                        target_counts[primary_target_id]
                        if primary_target_id is not None
                        else 0
                    ),
                    "teamFocusFollowupCount": focus_followups,
                })

            initiations = [row for row in rows if row["entryRole"] != "joiner"]
            joiner_rows = [row for row in rows if row["entryRole"] == "joiner"]
            solo_rows = [row for row in rows if row["soloEntry"]]
            reinforcement_rows = [
                row for row in initiations
                if row["reinforcementDelaySeconds"] is not None
            ]
            target_action_count = sum(row["targetActionCount"] for row in rows)
            target_switch_count = sum(row["targetSwitchCount"] for row in rows)
            episode_primary_target_action_count = sum(
                row["primaryTargetActionCount"] for row in rows
            )
            focus_followup_count = sum(
                row["teamFocusFollowupCount"] for row in rows
            )
            member["combatJudgment"] = {
                "status": (
                    "derived-exact-combat-state-and-target-selection-v1"
                ),
                "fallbackUsed": False,
                "teamEpisodeCount": len(episodes),
                "unconfirmedPvPIntervalCount": sum(
                    right > left and not any(left <= tick < right
                                             for tick in pvp_evidence_ticks[pid])
                    for left, right, *_ in member["combatIntervals"]
                ),
                "combatIntervalScope": "per-player-enemy-evidence-before-team-union",
                "participatedEpisodeCount": len(rows),
                "initiationCount": len(initiations),
                "initiationRate": (
                    round(len(initiations) / len(rows), 4) if rows else None
                ),
                "soloInitiationCount": len(solo_rows),
                "soloInitiationRate": (
                    round(len(solo_rows) / len(initiations), 4)
                    if initiations else None
                ),
                "meanJoinDelaySeconds": (
                    round(
                        sum(row["joinDelaySeconds"] for row in joiner_rows)
                        / len(joiner_rows),
                        3,
                    )
                    if joiner_rows else None
                ),
                "meanReinforcementDelaySeconds": (
                    round(
                        sum(
                            row["reinforcementDelaySeconds"]
                            for row in reinforcement_rows
                        ) / len(reinforcement_rows),
                        3,
                    )
                    if reinforcement_rows else None
                ),
                "survivedEpisodeCount": sum(row["survived"] for row in rows),
                "survivalRate": (
                    round(sum(row["survived"] for row in rows) / len(rows), 4)
                    if rows else None
                ),
                "targetActionCount": target_action_count,
                "targetSwitchCount": target_switch_count,
                "targetSwitchesPer10Actions": (
                    round(target_switch_count * 10 / target_action_count, 3)
                    if target_action_count else None
                ),
                "episodePrimaryTargetActionCount": (
                    episode_primary_target_action_count
                ),
                "episodePrimaryTargetShare": (
                    round(
                        episode_primary_target_action_count / target_action_count,
                        4,
                    )
                    if target_action_count else None
                ),
                "teamFocusFollowupCount": focus_followup_count,
                "teamFocusFollowupRate": (
                    round(focus_followup_count / target_action_count, 4)
                    if target_action_count else None
                ),
                "episodes": rows,
                "definitions": {
                    "teamEpisode": (
                        "개인별 적 플레이어 대상 지정·피해·방어 증거가 있는 "
                        "exact 전투 상태 구간만 합친 최대 연속 팀 구간"
                    ),
                    "soloEntry": (
                        "본인이 팀 최초 진입자이며 그 tick에 다른 팀원이 전투 상태가 아님"
                    ),
                    "targetAction": (
                        "적 플레이어를 targetObjectId로 지정한 스킬/기본 공격 시작; 적중 아님"
                    ),
                    "teamFocusFollowup": (
                        "다른 팀원이 같은 적을 ±2초 안에 targetObjectId로 지정"
                    ),
                    "survived": "팀 교전 구간 안에 본인의 CmdDead가 없음",
                },
            }
            attached += 1

        # Add a team-relative, deterministic style label without inventing a
        # cross-match grade or population percentile.
        if members:
            max_initiations = max(
                member["combatJudgment"]["initiationCount"] for member in members
            )
            max_solo = max(
                member["combatJudgment"]["soloInitiationCount"] for member in members
            )
            for member in members:
                judgment = member["combatJudgment"]
                if max_solo > 0 and judgment["soloInitiationCount"] == max_solo:
                    label = "혼자서도 문을 여는 선봉형"
                elif max_initiations > 0 and judgment["initiationCount"] == max_initiations:
                    label = "팀 교전의 문을 여는 선봉형"
                elif judgment["meanJoinDelaySeconds"] is not None:
                    label = "호출에 반응하는 합류형"
                else:
                    label = "상황을 고르는 대응형"
                judgment["styleLabel"] = label
                judgment["styleAuthority"] = "derived-team-relative-v1"

    normalize_simultaneous_combat_entries(players)
    for player in players:
        pid = player["objectId"]
        confirmed = merge_intervals([
            [left, right] for left, right, *_ in player["combatIntervals"]
            if right > left and any(left <= tick < right for tick in pvp_evidence_ticks[pid])
        ])
        personal = []
        for team_episode in player["combatJudgment"]["episodes"]:
            for left, right in confirmed:
                start = max(left, team_episode["startTick"])
                end = min(right, team_episode["endTick"])
                if start >= end:
                    continue
                personal.append({
                    "teamEpisodeNumber": len(personal) + 1,
                    "personalEpisodeNumber": len(personal) + 1,
                    "sourceTeamEpisodeNumber": team_episode["teamEpisodeNumber"],
                    "startTick": start, "endTick": end,
                    "entryTick": start,
                    "durationSeconds": round((end - start) / 60, 3),
                    "survived": not any(state == "dead" and start <= tick <= end
                                        for tick, state, *_ in player["lifeTimeline"]),
                    "scope": "personal-confirmed-pvp-interval",
                })
        player["combatJudgment"]["personalEpisodes"] = personal
        player["combatJudgment"]["personalEpisodeCount"] = len(personal)
    return {
        "status": "derived-exact-combat-state-and-target-selection-v1",
        "playerCount": attached,
        "teamEpisodeCount": total_team_episodes,
        "teamFocusWindowSeconds": 2,
        "simultaneousEntryWindowSeconds": SIMULTANEOUS_ENTRY_WINDOW_SECONDS,
        "fallbackUsed": False,
    }


def attach_growth_tempo(players: list[dict], item_rows: dict[int, dict]) -> dict:
    """Attach exact observed level/equipment/credit growth milestones."""

    milestones = (6, 9, 12, 15, 18, 20)
    purple_or_higher_grades = {"Epic", "Legend", "Mythic"}
    team_players: dict[int, list[dict]] = defaultdict(list)
    for player in players:
        team_players[player["teamNumber"]].append(player)

        level_changes = []
        last_level = None
        for row in sorted(player["snapshotSeries"], key=lambda value: value[0]):
            level = row[5]
            if not isinstance(level, int) or level == last_level:
                continue
            level_changes.append([row[0], level])
            last_level = level
        level_milestones = [
            {
                "level": level,
                "tick": next(
                    (tick for tick, observed in level_changes if observed >= level),
                    None,
                ),
            }
            for level in milestones
        ]

        equipment: dict[int, int] = {}
        equipment_progress = []
        previous_count = None
        for tick, updates, *_ in player["equipmentTimeline"]:
            for slot, code, amount in updates:
                if code is None or amount == 0:
                    equipment.pop(slot, None)
                else:
                    equipment[slot] = code
            completed_count = sum(
                item_rows.get(code, {}).get("itemGrade")
                in purple_or_higher_grades
                for code in equipment.values()
            )
            if completed_count != previous_count:
                equipment_progress.append([tick, completed_count])
                previous_count = completed_count
        equipment_milestones = [
            {
                "completedSlots": count,
                "tick": next(
                    (tick for tick, observed in equipment_progress if observed >= count),
                    None,
                ),
            }
            for count in (3, 4, 5)
        ]

        credit_rows = player["observerStatusTimeline"]
        observed_credits = [row[1] for row in credit_rows]
        result = player["gameResult"]
        purple_build_tick = next(
            (
                row["tick"]
                for row in equipment_milestones
                if row["completedSlots"] == 5
            ),
            None,
        )
        credit_counter_gap = None
        if observed_credits:
            credit_counter_gap = round(
                (observed_credits[-1] - observed_credits[0])
                - (
                    result["totalGainVFCredit"]
                    - result["totalUseVFCredit"]
                ),
                2,
            )
        player["growthTempo"] = {
            "status": "derived-exact-snapshot-and-equipment-growth-v1",
            "fallbackUsed": False,
            "levelTimeline": level_changes,
            "levelMilestones": level_milestones,
            "equipmentProgress": equipment_progress,
            "equipmentMilestones": equipment_milestones,
            "fullBuildTick": purple_build_tick,
            "purpleBuildTick": purple_build_tick,
            "peakObservedCredit": max(observed_credits) if observed_credits else None,
            "lastObservedCredit": observed_credits[-1] if observed_credits else None,
            "creditObservationCount": len(credit_rows),
            "creditCounterAudit": {
                "status": "derived-exact-result-counters-vs-observed-balance-v1",
                "startObservedCredit": (
                    observed_credits[0] if observed_credits else None
                ),
                "endObservedCredit": (
                    observed_credits[-1] if observed_credits else None
                ),
                "counterBalanceGap": credit_counter_gap,
                "ledgerCompatible": (
                    credit_counter_gap is not None
                    and abs(credit_counter_gap) < 0.01
                ),
                "interpretation": (
                    "result gain/use counters are not treated as a transaction ledger"
                ),
            },
            "totalGainCredit": result["totalGainVFCredit"],
            "totalUseCredit": result["totalUseVFCredit"],
            "finalLevel": result["characterLevel"],
            "finalMasteryLevel": result["bestWeaponLevel"],
            "definitions": {
                "levelMilestone": "full snapshot에서 해당 레벨 이상이 처음 관측된 tick",
                "equipmentMilestone": (
                    "exact 장비 업데이트에서 Epic(보라) 이상 장비 슬롯 수가 "
                    "처음 도달한 tick"
                ),
                "purpleBuild": (
                    "exact 장비 업데이트에서 5부위가 모두 Epic(보라) 이상이 "
                    "처음 된 tick"
                ),
                "observedCredit": (
                    "full snapshot의 보유 VF 크레딧; 누적 획득/사용은 종료 결과 exact"
                ),
            },
        }

    for members in team_players.values():
        full_ticks = [
            member["growthTempo"]["fullBuildTick"]
            for member in members
            if member["growthTempo"]["fullBuildTick"] is not None
        ]
        level_15_ticks = [
            next(
                row["tick"]
                for row in member["growthTempo"]["levelMilestones"]
                if row["level"] == 15
            )
            for member in members
            if next(
                row["tick"]
                for row in member["growthTempo"]["levelMilestones"]
                if row["level"] == 15
            ) is not None
        ]
        max_mastery = max(
            member["growthTempo"]["finalMasteryLevel"] for member in members
        )
        for member in members:
            growth = member["growthTempo"]
            level_15_tick = next(
                row["tick"]
                for row in growth["levelMilestones"]
                if row["level"] == 15
            )
            if full_ticks and growth["fullBuildTick"] == min(full_ticks):
                style = "남들보다 먼저 차려입는 장비 완성형"
            elif level_15_ticks and level_15_tick == min(level_15_ticks):
                style = "레벨 곡선을 앞당긴 선성장형"
            elif growth["finalMasteryLevel"] == max_mastery:
                style = "숙련도를 끝까지 쌓는 누적형"
            else:
                style = "교전 속에서 따라붙는 성장형"
            growth["styleLabel"] = style
            growth["styleAuthority"] = "derived-team-relative-v1"

    return {
        "status": "derived-exact-snapshot-and-equipment-growth-v1",
        "playerCount": len(players),
        "levelMilestones": list(milestones),
        "equipmentMilestones": [3, 4, 5],
        "fallbackUsed": False,
    }


OBJECTIVE_KIND_LABELS = {
    "meteor": "운석",
    "tree-of-life": "생명의 나무",
    "alpha": "알파",
    "omega": "오메가",
    "wickeline": "위클라인",
    "rift": "균열",
    "air-supply-rare": "음식 보급 상자",
    "air-supply-epic": "보라 보급 상자",
    "air-supply-legend": "전설 보급 상자",
    "air-supply-mythic": "초월 보급 상자",
}


def _held_track_position(track: list[list], tick: int):
    left, right = 0, len(track)
    while left < right:
        middle = (left + right) // 2
        if track[middle][0] <= tick:
            left = middle + 1
        else:
            right = middle
    return [track[left - 1][1], track[left - 1][2]] if left else None


def _life_state_at(player: dict, tick: int) -> str | None:
    state = None
    for row in player["lifeTimeline"]:
        if row[0] > tick:
            break
        state = row[1]
    return state


def attach_death_review(
    players: list[dict],
    events: list[list],
    event_defs: dict[int, dict],
    resolved_player,
    state_type_names: dict[int, str],
    *,
    context_seconds: int = 5,
    target_window_seconds: int = 4,
    nearby_radius_meters: int = 30,
    collapse_window_seconds: int = 15,
) -> dict:
    """Attach bounded, deterministic context for each exact death.

    This deliberately describes observations around a down/death; it never
    promotes correlation into a causal "death reason". Positions are the last
    decoded anchors held to the focus tick, while target selection, CC, heal,
    shield and life-state events remain exact packets.
    """

    player_by_id = {player["objectId"]: player for player in players}
    target_actions: dict[int, list[tuple[int, int]]] = defaultdict(list)
    crowd_control: dict[int, list[tuple[int, str]]] = defaultdict(list)
    healing: dict[int, list[tuple[int, int]]] = defaultdict(list)
    shield_increases: dict[int, list[tuple[int, int]]] = defaultdict(list)
    recorded_damage: dict[int, list[tuple[int, int, int]]] = defaultdict(list)
    death_events: dict[int, list[list]] = defaultdict(list)
    previous_shield: dict[int, int] = defaultdict(int)

    for event in events:
        packet_name = event_defs[event[2]]["packetName"]
        target_id = event_field(event, event_defs, "objectId")
        if packet_name in {"CmdStartSkill", "CmdStartNormalAttackSkill"}:
            actor_id = resolved_player(target_id)
            selected_id = resolved_player(
                event_field(event, event_defs, "targetObjectId")
            )
            if (
                actor_id in player_by_id
                and selected_id in player_by_id
                and player_by_id[actor_id]["teamNumber"]
                != player_by_id[selected_id]["teamNumber"]
            ):
                target_actions[selected_id].append((event[0], actor_id))
        elif packet_name == "CmdCrowdControl" and target_id in player_by_id:
            state_type = event_field(event, event_defs, "stateType")
            if state_type not in state_type_names:
                raise ValueError(
                    f"CmdCrowdControl has unresolved StateType value {state_type}"
                )
            crowd_control[target_id].append((event[0], state_type_names[state_type]))
        elif packet_name in {"CmdHeal", "CmdHealStateCode"} and target_id in player_by_id:
            amount = event_field(event, event_defs, "addHp")
            if isinstance(amount, (int, float)) and amount > 0:
                healing[target_id].append((event[0], round(amount)))
        elif packet_name == "CmdUpdateShield" and target_id in player_by_id:
            amounts = [
                event_field(event, event_defs, field)
                for field in (
                    "blockAllShieldAmount",
                    "blockNormalShieldAmount",
                    "blockSkillShieldAmount",
                )
            ]
            if any(not isinstance(amount, int) or amount < 0 for amount in amounts):
                raise ValueError("CmdUpdateShield contains an invalid shield amount")
            current = sum(amounts)
            increase = max(0, current - previous_shield[target_id])
            if increase:
                shield_increases[target_id].append((event[0], increase))
            previous_shield[target_id] = current
        elif packet_name == "CmdDamage" and target_id in player_by_id:
            attacker_id = resolved_player(event_field(event, event_defs, "attackerId"))
            amount = event_field(event, event_defs, "damage")
            if (
                attacker_id in player_by_id
                and attacker_id != target_id
                and isinstance(amount, (int, float))
                and amount >= 0
            ):
                recorded_damage[target_id].append(
                    (event[0], attacker_id, round(amount))
                )
        elif packet_name == "CmdDead" and target_id in player_by_id:
            death_events[target_id].append(event)

    for rows in (
        target_actions,
        crowd_control,
        healing,
        shield_increases,
        recorded_damage,
    ):
        for values in rows.values():
            values.sort(key=lambda row: row[0])

    context_ticks = context_seconds * 60
    target_ticks = target_window_seconds * 60
    collapse_ticks = collapse_window_seconds * 60
    total_deaths = 0
    for player in players:
        player_id = player["objectId"]
        team_number = player["teamNumber"]
        pending_down_tick = None
        paired_deaths = []
        for tick, state, *_ in player["lifeTimeline"]:
            if state == "alive":
                pending_down_tick = None
            elif state == "down":
                pending_down_tick = tick
            elif state == "dead":
                paired_deaths.append((pending_down_tick, tick))
                pending_down_tick = None

        reviews = []
        for death_number, (down_tick, death_tick) in enumerate(paired_deaths, 1):
            focus_tick = down_tick if down_tick is not None else death_tick
            focus_position = _held_track_position(player["movementTrack"], focus_tick)
            nearby_allies = []
            nearby_enemies = []
            teammate_distances = []
            positioned_player_count = 0
            for other in players:
                if other["objectId"] == player_id:
                    continue
                other_position = _held_track_position(other["movementTrack"], focus_tick)
                if focus_position is None or other_position is None:
                    continue
                positioned_player_count += 1
                distance = math.dist(focus_position, other_position)
                if other["teamNumber"] == team_number:
                    if _life_state_at(other, focus_tick) == "alive":
                        teammate_distances.append(distance)
                        if distance <= nearby_radius_meters:
                            nearby_allies.append(other)
                elif (
                    _life_state_at(other, focus_tick) == "alive"
                    and distance <= nearby_radius_meters
                ):
                    nearby_enemies.append(other)

            selected_rows = [
                row
                for row in target_actions.get(player_id, [])
                if focus_tick - target_ticks <= row[0] <= focus_tick
            ]
            cc_rows = [
                row
                for row in crowd_control.get(player_id, [])
                if focus_tick - context_ticks <= row[0] <= focus_tick
            ]
            heal_rows = [
                row
                for row in healing.get(player_id, [])
                if focus_tick - context_ticks <= row[0] <= focus_tick
            ]
            shield_rows = [
                row
                for row in shield_increases.get(player_id, [])
                if focus_tick - context_ticks <= row[0] <= focus_tick
            ]
            damage_rows = [
                row
                for row in recorded_damage.get(player_id, [])
                if focus_tick - context_ticks <= row[0] <= focus_tick
            ]
            cc_counts = Counter(state_type for _, state_type in cc_rows)

            collapsed_teammates = []
            for teammate in players:
                if (
                    teammate["objectId"] == player_id
                    or teammate["teamNumber"] != team_number
                ):
                    continue
                transitions = [
                    row
                    for row in teammate["lifeTimeline"]
                    if focus_tick <= row[0] <= focus_tick + collapse_ticks
                    and row[1] in {"down", "dead"}
                ]
                if transitions:
                    collapsed_teammates.append({
                        "characterName": teammate["characterName"],
                        "firstState": transitions[0][1],
                        "delaySeconds": round((transitions[0][0] - focus_tick) / 60, 3),
                    })

            nearby_enemy_tools = []
            for enemy in sorted(nearby_enemies, key=lambda row: row["characterName"]):
                capabilities = enemy["characterCapabilities"]
                nearby_enemy_tools.append({
                    "characterName": enemy["characterName"],
                    "crowdControlStateCount": len(capabilities["crowdControlStates"]),
                    "shieldOrHealingStateCount": (
                        len(capabilities["shieldStates"])
                        + len(capabilities["namedHealingStates"])
                    ),
                    "defensiveStateCount": len(capabilities["defensiveStateTypes"]),
                    "movementSkillCount": len(capabilities["movementSkills"]),
                    "status": "exact-replay-version-static-capability-counts",
                })

            flags = []
            closest_teammate = min(teammate_distances, default=None)
            if nearby_enemies and len(nearby_enemies) > len(nearby_allies):
                flags.append("local-number-disadvantage")
            if closest_teammate is not None and closest_teammate > nearby_radius_meters:
                flags.append("nearest-alive-teammate-beyond-nearby-radius")
            if len({actor for _, actor in selected_rows}) >= 2:
                flags.append("multi-enemy-target-selection-pressure")
            if cc_rows:
                flags.append("crowd-control-observed-before-down")
            if collapsed_teammates:
                flags.append("team-collapse-followed-within-window")

            matching_death_event = next(
                (
                    event
                    for event in death_events.get(player_id, [])
                    if event[0] == death_tick
                ),
                None,
            )
            finisher_id = (
                resolved_player(
                    event_field(
                        matching_death_event,
                        event_defs,
                        "finishingAttackerObjectId",
                    )
                )
                if matching_death_event is not None
                else None
            )
            finisher = player_by_id.get(finisher_id)
            reviews.append({
                "deathNumber": death_number,
                "focusTick": focus_tick,
                "focusTickSource": (
                    "exact-CmdDyingCondition" if down_tick is not None else "exact-CmdDead"
                ),
                "downTick": down_tick,
                "deathTick": death_tick,
                "downToDeathSeconds": (
                    round((death_tick - down_tick) / 60, 3)
                    if down_tick is not None
                    else None
                ),
                "finisherCharacterName": (
                    finisher["characterName"]
                    if finisher is not None and finisher_id != player_id
                    else None
                ),
                "finisherStatus": (
                    "decoded-exact-resolved-player-owner"
                    if finisher is not None and finisher_id != player_id
                    else "unavailable-no-resolved-other-player-finisher"
                ),
                "focusPositionStatus": (
                    "derived-held-last-decoded-anchor"
                    if focus_position is not None
                    else "unavailable-no-position-anchor"
                ),
                "positionedOtherPlayerCount": positioned_player_count,
                "nearbyAliveAllyCount": len(nearby_allies),
                "nearbyAliveEnemyCount": len(nearby_enemies),
                "closestAliveTeammateMeters": (
                    round(closest_teammate, 2)
                    if closest_teammate is not None
                    else None
                ),
                "enemyTargetActionCount": len(selected_rows),
                "enemyTargeterCount": len({actor for _, actor in selected_rows}),
                "crowdControlReceived": [
                    {"stateType": state_type, "count": count}
                    for state_type, count in sorted(cc_counts.items())
                ],
                "crowdControlCount": len(cc_rows),
                "positiveShieldGain": sum(amount for _, amount in shield_rows),
                "healingReceived": sum(amount for _, amount in heal_rows),
                "recordedDamageTaken": sum(amount for _, _, amount in damage_rows),
                "recordedDamageAttackerCount": len(
                    {attacker for _, attacker, _ in damage_rows}
                ),
                "recordedDamageStatus": "partial-non-null-CmdDamage-only",
                "teammateCollapseWithinWindow": collapsed_teammates,
                "nearbyEnemyStaticTools": nearby_enemy_tools,
                "feedbackFlags": flags,
                "status": "exact-events-plus-derived-held-position-context",
            })

        player["deathReview"] = {
            "status": "derived-exact-event-and-held-position-death-review-v1",
            "fallbackUsed": False,
            "deathCount": len(reviews),
            "positionComparableDeathCount": sum(
                row["focusPositionStatus"] == "derived-held-last-decoded-anchor"
                for row in reviews
            ),
            "localNumberDisadvantageCount": sum(
                "local-number-disadvantage" in row["feedbackFlags"]
                for row in reviews
            ),
            "multiTargeterPressureCount": sum(
                "multi-enemy-target-selection-pressure" in row["feedbackFlags"]
                for row in reviews
            ),
            "crowdControlPressureCount": sum(
                "crowd-control-observed-before-down" in row["feedbackFlags"]
                for row in reviews
            ),
            "teamCollapseCount": sum(
                "team-collapse-followed-within-window" in row["feedbackFlags"]
                for row in reviews
            ),
            "deaths": reviews,
            "definitions": {
                "causality": "사망 원인 단정이 아닌 다운/사망 직전 관측 맥락",
                "position": "마지막 decoded 위치 앵커를 focus tick까지 유지한 파생 좌표",
                "targetAction": "적이 본인을 exact 대상으로 지정한 스킬/기본 공격 시작; 적중 아님",
                "staticTools": "근처 적 실험체의 replay-version gameDb 기본 도구 수; 실제 사용 아님",
            },
        }
        total_deaths += len(reviews)

    return {
        "status": "derived-exact-event-and-held-position-death-review-v1",
        "playerCount": len(players),
        "deathCount": total_deaths,
        "contextSeconds": context_seconds,
        "targetWindowSeconds": target_window_seconds,
        "nearbyRadiusMeters": nearby_radius_meters,
        "collapseWindowSeconds": collapse_window_seconds,
        "fallbackUsed": False,
    }


def attach_objective_preparation(
    players: list[dict], world_map: dict, *, preparation_radius_meters: int = 20
) -> dict:
    """Attach objective arrival evidence from exact held movement anchors."""

    positioned = [
        row
        for row in world_map.get("events", [])
        if row.get("kind") in OBJECTIVE_KIND_LABELS
        and isinstance(row.get("position"), list)
        and len(row["position"]) == 2
        and isinstance(row.get("activeTick"), int)
    ]
    team_players: dict[int, list[dict]] = defaultdict(list)
    for player in players:
        team_players[player["teamNumber"]].append(player)
        rows = []
        for index, objective in enumerate(positioned, 1):
            warning_tick = objective.get("warningTick")
            if not isinstance(warning_tick, int):
                raise ValueError("positioned objective is missing an exact warning tick")
            active_tick = objective["activeTick"]
            end_tick = objective.get("endTick")
            if not isinstance(end_tick, int):
                raise ValueError("positioned objective is missing an exact end tick")
            ox, oz = objective["position"]

            def distance_at(tick: int):
                position = _held_track_position(player["movementTrack"], tick)
                if position is None:
                    return None
                return math.hypot(position[0] - ox, position[1] - oz)

            warning_distance = distance_at(warning_tick)
            active_distance = distance_at(active_tick)
            observed = []
            if warning_distance is not None:
                observed.append([warning_tick, warning_distance])
            observed.extend(
                [anchor[0], math.hypot(anchor[1] - ox, anchor[2] - oz)]
                for anchor in player["movementTrack"]
                if warning_tick < anchor[0] <= end_tick
            )
            arrival_tick = next(
                (
                    tick
                    for tick, distance in observed
                    if distance <= preparation_radius_meters
                ),
                None,
            )
            lead_seconds = (
                round((active_tick - arrival_tick) / 60, 3)
                if arrival_tick is not None
                else None
            )
            rows.append({
                "objectiveNumber": index,
                "kind": objective["kind"],
                "label": OBJECTIVE_KIND_LABELS[objective["kind"]],
                "warningTick": warning_tick,
                "activeTick": active_tick,
                "endTick": end_tick,
                "warningDistanceMeters": (
                    round(warning_distance, 2)
                    if warning_distance is not None else None
                ),
                "activeDistanceMeters": (
                    round(active_distance, 2)
                    if active_distance is not None else None
                ),
                "arrivalTick": arrival_tick,
                "arrivalLeadSeconds": lead_seconds,
                "arrivalStatus": (
                    "before-active"
                    if lead_seconds is not None and lead_seconds >= 0
                    else "after-active" if lead_seconds is not None
                    else "not-observed-in-range"
                ),
                "minimumObservedDistanceMeters": (
                    round(min(distance for _, distance in observed), 2)
                    if observed else None
                ),
            })
        before_rows = [row for row in rows if row["arrivalStatus"] == "before-active"]
        after_rows = [row for row in rows if row["arrivalStatus"] == "after-active"]
        player["objectivePreparation"] = {
            "status": "derived-exact-objective-clock-and-held-anchor-distance-v1",
            "fallbackUsed": False,
            "preparationRadiusMeters": preparation_radius_meters,
            "objectiveCount": len(rows),
            "beforeActiveCount": len(before_rows),
            "afterActiveCount": len(after_rows),
            "notObservedCount": len(rows) - len(before_rows) - len(after_rows),
            "meanArrivalLeadSeconds": (
                round(
                    sum(row["arrivalLeadSeconds"] for row in before_rows)
                    / len(before_rows),
                    3,
                )
                if before_rows else None
            ),
            "objectives": rows,
            "definitions": {
                "arrival": (
                    "예고 tick부터 종료 tick까지 마지막 exact 이동 앵커가 "
                    f"오브젝트 {preparation_radius_meters}m 안에서 처음 관측된 시점"
                ),
                "beforeActive": "arrival tick이 exact 활성 tick보다 빠르거나 같음",
                "claimBoundary": "오브젝트 획득·처치·소유권은 이 지표가 판정하지 않음",
            },
        }

    for members in team_players.values():
        max_before = max(
            member["objectivePreparation"]["beforeActiveCount"]
            for member in members
        )
        max_arrived = max(
            member["objectivePreparation"]["beforeActiveCount"]
            + member["objectivePreparation"]["afterActiveCount"]
            for member in members
        )
        for member in members:
            prep = member["objectivePreparation"]
            arrived = prep["beforeActiveCount"] + prep["afterActiveCount"]
            if max_before > 0 and prep["beforeActiveCount"] == max_before:
                style = "등장 전에 자리를 잡는 선점형"
            elif max_arrived > 0 and arrived == max_arrived:
                style = "발생 뒤에도 놓치지 않는 추적형"
            else:
                style = "필요한 오브젝트를 골라 가는 선택형"
            prep["styleLabel"] = style
            prep["styleAuthority"] = "derived-team-relative-v1"

    return {
        "status": "derived-exact-objective-clock-and-held-anchor-distance-v1",
        "playerCount": len(players),
        "objectiveCount": len(positioned),
        "preparationRadiusMeters": preparation_radius_meters,
        "fallbackUsed": False,
    }


def attach_skill_operation(
    players: list[dict],
    events: list[list],
    event_defs: dict[int, dict],
    resolved_player,
) -> dict:
    """Attach exact PvP skill use and separately verified hit attribution."""

    normal_by_player: dict[int, list[int]] = defaultdict(list)
    player_ids = {player["objectId"] for player in players}
    for event in events:
        if event_defs[event[2]]["packetName"] != "CmdStartNormalAttackSkill":
            continue
        pid = resolved_player(event_field(event, event_defs, "objectId"))
        if pid in player_ids:
            normal_by_player[pid].append(event[0])

    team_players: dict[int, list[dict]] = defaultdict(list)
    for player in players:
        team_players[player["teamNumber"]].append(player)
        judgment = player["combatJudgment"]
        episodes = judgment["personalEpisodes"]
        family_counts: Counter[str] = Counter()
        opener_counts: Counter[str] = Counter()
        chain_counts: Counter[str] = Counter()
        episode_rows = []
        pvp_normal_count = 0
        for episode in episodes:
            starts = [
                row
                for row in player["skillStartTimeline"]
                if episode["startTick"] <= row[0] < episode["endTick"]
            ]
            normals = [
                tick
                for tick in normal_by_player.get(player["objectId"], [])
                if episode["startTick"] <= tick < episode["endTick"]
            ]
            pvp_normal_count += len(normals)
            family_counts.update(row[1] for row in starts)
            if starts:
                opener_counts[starts[0][1]] += 1
            local_chains = Counter(
                f"{left[1]}>{right[1]}"
                for left, right in zip(starts, starts[1:])
                if 0 <= right[0] - left[0] <= 3 * 60
            )
            chain_counts.update(local_chains)
            episode_rows.append({
                "teamEpisodeNumber": episode["teamEpisodeNumber"],
                **({"personalEpisodeNumber": episode["personalEpisodeNumber"],
                    "sourceTeamEpisodeNumber": episode["sourceTeamEpisodeNumber"]}
                   if "personalEpisodeNumber" in episode else {}),
                "startTick": episode["startTick"],
                "endTick": episode["endTick"],
                "openerFamily": starts[0][1] if starts else None,
                "skillStartCount": len(starts),
                "normalAttackStartCount": len(normals),
                "familyCounts": dict(sorted(Counter(row[1] for row in starts).items())),
                "topChain": (
                    local_chains.most_common(1)[0][0] if local_chains else None
                ),
                "topChainCount": (
                    local_chains.most_common(1)[0][1] if local_chains else 0
                ),
                "hitRates": [],
            })
        exact_hit_rates = []
        for hit_row in player.get("projectileHitRates", []):
            outcomes = hit_row.pop("_combatAttemptOutcomes", None)
            for evidence_key in ("actionTargetEvidence", "exactEffectDamageEvidence"):
                evidence = hit_row.get(evidence_key)
                if isinstance(evidence, dict):
                    evidence.pop("_combatCastOutcomes", None)
            if hit_row.get("hitRateCalculable") is not True:
                if outcomes is not None:
                    raise ValueError(
                        "non-calculable skill unexpectedly contains hit outcomes"
                    )
                continue
            if (
                not isinstance(outcomes, list)
                or len(outcomes) != hit_row.get("attemptCount")
                or any(
                    not isinstance(outcome, list)
                    or len(outcome) != 4
                    or type(outcome[0]) is not int
                    or type(outcome[1]) is not int
                    or outcome[1] not in {0, 1}
                    or type(outcome[2]) is not int
                    or outcome[2] < outcome[0]
                    or (outcome[1] == 1 and (
                        type(outcome[3]) is not int or outcome[3] < outcome[2]
                    ))
                    or (outcome[1] == 0 and outcome[3] is not None)
                    for outcome in outcomes
                )
                or sum(outcome[1] for outcome in outcomes)
                != hit_row.get("playerHitAttemptCount")
            ):
                raise ValueError("calculable skill hit outcome timeline is invalid")
            base_hit_rate = {
                "skillGroup": hit_row["skillGroup"],
                "skillId": hit_row["skillId"],
                "family": hit_row["family"],
                "unit": hit_row["hitRateUnit"],
                "attemptCount": len(outcomes),
                "hitCount": sum(outcome[1] for outcome in outcomes),
                "hitRate": hit_row["playerHitRate"],
                "projectilesPerCast": hit_row.get("projectilesPerCast"),
                "status": "verified-exact-confirmed-engagement-hit-rate",
                "fallbackUsed": False,
            }
            exact_hit_rates.append(base_hit_rate)
            assigned_attempts = 0
            for episode_row in episode_rows:
                episode_outcomes = [
                    outcome
                    for outcome in outcomes
                    if episode_row["startTick"] <= outcome[0] < episode_row["endTick"]
                ]
                if not episode_outcomes:
                    continue
                attempt_count = len(episode_outcomes)
                hit_count = sum(outcome[1] for outcome in episode_outcomes)
                assigned_attempts += attempt_count
                episode_row["hitRates"].append({
                    "skillGroup": hit_row["skillGroup"],
                    "skillId": hit_row["skillId"],
                    "family": hit_row["family"],
                    "unit": hit_row["hitRateUnit"],
                    "attemptCount": attempt_count,
                    "hitCount": hit_count,
                    "hitRate": round(hit_count / attempt_count, 6),
                    "outcomes": episode_outcomes,
                    "projectilesPerCast": hit_row.get("projectilesPerCast"),
                    "status": "verified-exact-single-engagement-hit-rate",
                    "fallbackUsed": False,
                })
            if assigned_attempts != len(outcomes):
                raise ValueError(
                    "skill hit outcome could not be assigned to exactly one engagement"
                )
        skill_count = sum(family_counts.values())
        engagement_seconds = sum(
            episode["endTick"] - episode["startTick"] for episode in episodes
        ) / 60
        player["skillOperation"] = {
            "status": "derived-exact-confirmed-pvp-skill-sequence-v1",
            "fallbackUsed": False,
            "confirmedEngagementSeconds": round(engagement_seconds, 3),
            "pvpSkillStartCount": skill_count,
            "pvpNormalAttackStartCount": pvp_normal_count,
            "skillStartsPerMinute": (
                round(skill_count * 60 / engagement_seconds, 3)
                if engagement_seconds > 0 else None
            ),
            "normalAttacksPerSkillStart": (
                round(pvp_normal_count / skill_count, 3)
                if skill_count else None
            ),
            "familyCounts": dict(sorted(family_counts.items())),
            "hitRates": exact_hit_rates,
            "openerCounts": dict(sorted(opener_counts.items())),
            "mostCommonOpener": (
                opener_counts.most_common(1)[0][0] if opener_counts else None
            ),
            "mostCommonOpenerCount": (
                opener_counts.most_common(1)[0][1] if opener_counts else 0
            ),
            "topChain": chain_counts.most_common(1)[0][0] if chain_counts else None,
            "topChainCount": chain_counts.most_common(1)[0][1] if chain_counts else 0,
            "weaponSkillStartCount": family_counts["WeaponSkill"],
            "tacticalSkillStartCount": family_counts["TacticalSkill"],
            "episodes": episode_rows,
            "definitions": {
                "pvpWindow": "교전 판단력에서 확인된 대인 교전 참여 구간",
                "startRecord": (
                    "각 CmdStartSkill 패킷 1건; 충전 스킬의 각 사용과 "
                    "재시전 단계는 별도 시작으로 집계하며 입력 횟수는 아님"
                ),
                "sustainedActivation": (
                    "활성 유지 시간은 시작 횟수에 더하지 않고, "
                    "CmdStartPassiveSkill/CmdStartStateSkill은 제외"
                ),
                "opener": "각 참여 구간에서 처음 관측된 CmdStartSkill 계열",
                "chain": "같은 참여 구간에서 3초 안에 이어진 인접 스킬 시작 계열",
                "hitBoundary": "스킬 시작 순서이며 적중·피해 연계 판정이 아님",
                "hitRate": (
                    "검증된 스킬만 교전별 분모·적중 수로 집계하며 "
                    "투사체 발 단위와 스킬 시전 단위는 합치지 않음"
                ),
            },
        }

    for members in team_players.values():
        rates = [
            member["skillOperation"]["skillStartsPerMinute"]
            for member in members
            if member["skillOperation"]["skillStartsPerMinute"] is not None
        ]
        normal_ratios = [
            member["skillOperation"]["normalAttacksPerSkillStart"]
            for member in members
            if member["skillOperation"]["normalAttacksPerSkillStart"] is not None
        ]
        for member in members:
            operation = member["skillOperation"]
            if rates and operation["skillStartsPerMinute"] == max(rates):
                style = "손이 쉬지 않는 연계형"
            elif (
                normal_ratios
                and operation["normalAttacksPerSkillStart"] == max(normal_ratios)
            ):
                style = "평타를 사이에 엮는 혼합형"
            elif operation["pvpSkillStartCount"] > 0:
                style = "필요한 순간마다 스킬을 꺼내는 활용형"
            else:
                style = "전투 중 스킬 사용 기록이 적은 관망형"
            operation["styleLabel"] = style
            operation["styleAuthority"] = "derived-team-relative-v1"

    return {
        "status": "derived-exact-confirmed-pvp-skill-sequence-v1",
        "playerCount": len(players),
        "chainWindowSeconds": 3,
        "fallbackUsed": False,
    }


def cooldown_stats(
    events: list[list],
    pid: int,
    combat_intervals: list[list[int]],
    enum_values: dict[str, dict[int, str]],
    event_defs: dict[int, dict],
    end_tick: int,
):
    slot_names = enum_values["SkillSlotSet"]
    families = ("Active1", "Active2", "Active3", "Active4", "WeaponSkill", "TacticalSkill")
    # Keep source order, including clears at the same tick as a cooldown update.
    events_by_family = {family: [] for family in families}
    for event in events:
        if event_field(event, event_defs, "objectId") != pid:
            continue
        packet_name = event_defs[event[2]]["packetName"]
        if packet_name == "CmdClearCharacterCooldown":
            for slot_events in events_by_family.values():
                slot_events.append(event)
        elif packet_name in {
            "CmdStartWeaponSkillCooldown", "CmdModifyWeaponSkillCooldown",
        }:
            events_by_family["WeaponSkill"].append(event)
        elif packet_name in {
            "CmdStartCharacterSkillCooldown", "CmdModifyCharacterSkillCooldown",
            "CmdCopyCharacterSkillCooldown", "CmdHoldSkillCooldown",
        }:
            slot = event_field(event, event_defs, "skillSlotSet")
            family = skill_family(slot_names.get(slot))
            if family in events_by_family:
                events_by_family[family].append(event)

    results = []
    for family in families:
        slot_events = events_by_family[family]
        cooldown_intervals = []
        current_start = None
        current_end = None
        first_track = None
        starts = []
        modify_count = 0
        hold_count = 0
        copy_count = 0
        for event in slot_events:
            tick = event[0]
            packet_name = event_defs[event[2]]["packetName"]
            if current_end is not None and tick >= current_end:
                cooldown_intervals.append([current_start, current_end])
                current_start = None
                current_end = None
            if packet_name in {
                "CmdStartCharacterSkillCooldown", "CmdStartWeaponSkillCooldown",
            }:
                first_track = tick if first_track is None else min(first_track, tick)
                raw = event_field(event, event_defs, "cooldown") or 0
                starts.append(raw)
                if current_end is not None:
                    cooldown_intervals.append([current_start, min(tick, current_end)])
                if raw > 0:
                    current_start = tick
                    current_end = tick + round(raw * 0.6)
                else:
                    current_start = None
                    current_end = None
            elif packet_name in {
                "CmdModifyCharacterSkillCooldown", "CmdModifyWeaponSkillCooldown",
            }:
                first_track = tick if first_track is None else min(first_track, tick)
                modify_count += 1
                raw = event_field(event, event_defs, "rusultCooldown") or 0
                if raw <= 0:
                    if current_end is not None:
                        cooldown_intervals.append([current_start, tick])
                    current_start = None
                    current_end = None
                else:
                    if current_start is None:
                        current_start = tick
                    current_end = tick + round(raw * 0.6)
            elif packet_name == "CmdCopyCharacterSkillCooldown":
                copy_count += 1
            elif packet_name == "CmdHoldSkillCooldown":
                hold_count += 1
            elif packet_name == "CmdClearCharacterCooldown" and current_end is not None:
                cooldown_intervals.append([current_start, tick])
                current_start = None
                current_end = None
        if current_end is not None:
            cooldown_intervals.append([current_start, min(current_end, end_tick)])

        merged = merge_intervals(cooldown_intervals)
        if first_track is None:
            tracked_combat = 0
            cooldown_combat = 0
        else:
            tracked_combat = overlap_ticks(combat_intervals, first_track, end_tick)
            cooldown_combat = sum(
                overlap_ticks(combat_intervals, left, right)
                for left, right in merged
            )
        ready_combat = max(0, tracked_combat - cooldown_combat)
        results.append({
            "family": family,
            "cooldownStarts": len(starts),
            "cooldownModifies": modify_count,
            "holdEvents": hold_count,
            "copyEvents": copy_count,
            "meanStartCooldownSeconds": round(sum(starts) / len(starts) / 100, 3) if starts else None,
            "trackedCombatSeconds": round(tracked_combat / 60, 3),
            "cooldownCombatSeconds": round(cooldown_combat / 60, 3),
            "readyCombatSeconds": round(ready_combat / 60, 3),
            "readyCombatRatio": round(ready_combat / tracked_combat, 4) if tracked_combat else None,
            "status": "derived-cooldown-timeline-v1",
            "caveat": (
                "hold/copy 이벤트가 있어 실제 사용 가능 상태와 다를 수 있음"
                if hold_count or copy_count
                else "CC·침묵·사망·대상·사거리 조건을 보정하지 않은 준비 상태 비율"
            ),
        })
    return results


def skill_cooldown_timeline(
    events: list[list],
    pid: int,
    enum_values: dict[str, dict[int, str]],
    event_defs: dict[int, dict],
) -> list[list]:
    """Return exact cooldown packets in a compact, identity-free state stream.

    Row layout is ``tick, action, family, remainingHundredths,
    maxHundredths, stack, detail``.  ``detail`` is the source family for copy,
    the wire boolean for hold, the exact mastery type for weapon set packets,
    and otherwise null.  Decrementing a known
    remaining value between packets is a derived clock operation performed by
    the viewer.  This function does not infer ready state before first evidence.
    """

    slot_names = enum_values["SkillSlotSet"]
    families = {
        "Active1", "Active2", "Active3", "Active4",
        "WeaponSkill", "TacticalSkill",
    }
    rows: list[list] = []
    for event in events:
        if event_field(event, event_defs, "objectId") != pid:
            continue
        packet_name = event_defs[event[2]]["packetName"]
        if packet_name == "CmdClearCharacterCooldown":
            rows.append([event[0], "clear", None, 0, 0, None, None])
            continue
        if packet_name not in {
            "CmdStartCharacterSkillCooldown",
            "CmdModifyCharacterSkillCooldown",
            "CmdCopyCharacterSkillCooldown",
            "CmdHoldSkillCooldown",
            "CmdStartWeaponSkillCooldown",
            "CmdModifyWeaponSkillCooldown",
        }:
            continue
        if packet_name in {
            "CmdStartWeaponSkillCooldown", "CmdModifyWeaponSkillCooldown",
        }:
            if packet_name == "CmdStartWeaponSkillCooldown":
                remaining_field = "cooldown"
                max_field = "cooldownMax"
            else:
                remaining_field = "rusultCooldown"
                max_field = "maxCooldown"
            rows.append([
                event[0], "set", "WeaponSkill",
                event_field(event, event_defs, remaining_field),
                event_field(event, event_defs, max_field),
                event_field(event, event_defs, "curSkillStack"),
                event_field(event, event_defs, "masteryType"),
            ])
            continue
        raw_slot = event_field(event, event_defs, "skillSlotSet")
        family = skill_family(slot_names.get(raw_slot))
        if family not in families:
            continue
        if packet_name == "CmdStartCharacterSkillCooldown":
            rows.append([
                event[0], "set", family,
                event_field(event, event_defs, "cooldown"),
                event_field(event, event_defs, "cooldownMax"),
                event_field(event, event_defs, "curSkillStack"),
                None,
            ])
        elif packet_name == "CmdModifyCharacterSkillCooldown":
            rows.append([
                event[0], "set", family,
                event_field(event, event_defs, "rusultCooldown"),
                event_field(event, event_defs, "maxCooldown"),
                event_field(event, event_defs, "curSkillStack"),
                None,
            ])
        elif packet_name == "CmdCopyCharacterSkillCooldown":
            source_slot = event_field(event, event_defs, "from")
            source_family = skill_family(slot_names.get(source_slot))
            rows.append([
                event[0], "copy", family, None, None, None,
                source_family if source_family in families else None,
            ])
        else:
            rows.append([
                event[0], "hold", family, None, None, None,
                event_field(event, event_defs, "isHold"),
            ])
    return rows


def attach_damage_timeline_coverage(events, event_defs, players):
    definitions = {str(key): value for key, value in event_defs.items()}
    damage_code = next(
        int(key)
        for key, definition in definitions.items()
        if definition["packetName"] == "CmdDamage"
    )
    fields = definitions[str(damage_code)]["fields"]
    damage_index = 5 + fields.index("damage")
    damage_events = [event for event in events if event[2] == damage_code]
    numeric_count = sum(event[damage_index] is not None for event in damage_events)
    for player in players:
        stats = player["stats"]
        final = player["gameResult"]
        outgoing_total = stats["damageToPlayerPackets"]
        outgoing_known = stats["damageToPlayerKnownPackets"]
        incoming_total = stats["damageFromPlayerPackets"]
        incoming_known = stats["damageFromPlayerKnownPackets"]
        stats["damageTimelineCoverage"] = {
            "outgoing": {
                "packetCount": outgoing_total,
                "numericFieldCount": outgoing_known,
                "nullFieldCount": outgoing_total - outgoing_known,
                "numericFieldRatio": (
                    round(outgoing_known / outgoing_total, 6)
                    if outgoing_total else None
                ),
                "knownTimelineDamage": stats["damageToPlayers"],
                "exactFinishDamage": final["damageToPlayer"],
                "unattributedResidual": final["damageToPlayer"] - stats["damageToPlayers"],
            },
            "incoming": {
                "packetCount": incoming_total,
                "numericFieldCount": incoming_known,
                "nullFieldCount": incoming_total - incoming_known,
                "numericFieldRatio": (
                    round(incoming_known / incoming_total, 6)
                    if incoming_total else None
                ),
                "knownTimelineDamage": stats["damageFromPlayers"],
                "exactFinishDamage": final["damageFromPlayer"],
                "unattributedResidual": final["damageFromPlayer"] - stats["damageFromPlayers"],
            },
            "status": "exact-numeric-field-coverage; null values are not imputed",
        }
    return {
        "payloadCount": len(damage_events),
        "numericDamageFieldCount": numeric_count,
        "nullDamageFieldCount": len(damage_events) - numeric_count,
        "numericDamageFieldRatio": (
            round(numeric_count / len(damage_events), 6) if damage_events else None
        ),
        "status": "all CmdDamage payloads retained; nullable damage values are not imputed",
    }


def attach_skill_damage_boundary(players):
    for player in players:
        player["skillDamageAttribution"] = {
            "exactSkillCategoryDamage": player["gameResult"]["damageToPlayer_skill"],
            "perSkillDamageTotals": None,
            "observedSkillStarts": sum(
                skill["startCount"] for skill in player["skills"]
            ),
            "status": "unavailable-no-skill-code-on-damage-and-null-damage-values",
            "reasons": [
                "CmdDamage has damageType and effectCode but no skillCode or SkillId.",
                "CmdStartSkill identifies skillCode but does not carry a damage-event identity.",
                "Nullable damage values are not inferred or distributed across nearby skill starts.",
            ],
        }
    return {
        "status": "per-skill-totals-unavailable",
        "exactAvailableLevel": "per-player total skill-category damage from CmdFinishGameResult",
        "unavailableLevel": "individual skill damage totals",
        "joinBoundary": "effectCode and temporal proximity are not authoritative skill provenance",
    }


def _svg_path_polygon(path_data: str) -> list[list[float]]:
    """Decode the approved Lumia M/L/H/V/Z area paths without SVG fallback."""
    tokens = re.findall(
        r"[MLHVZ]|[-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][-+]?\d+)?",
        path_data,
    )
    points: list[list[float]] = []
    command: str | None = None
    cursor = [0.0, 0.0]
    start: list[float] | None = None
    index = 0
    while index < len(tokens):
        token = tokens[index]
        if token in {"M", "L", "H", "V", "Z"}:
            command = token
            index += 1
            if command == "Z":
                if start is None:
                    raise ValueError("restriction SVG path closes before it starts")
                if points[-1] != start:
                    points.append(list(start))
                command = None
            continue
        if command in {"M", "L"}:
            if index + 1 >= len(tokens) or tokens[index + 1] in {"M", "L", "H", "V", "Z"}:
                raise ValueError("restriction SVG path has an incomplete point")
            cursor = [float(tokens[index]), float(tokens[index + 1])]
            points.append(list(cursor))
            if start is None:
                start = list(cursor)
            if command == "M":
                command = "L"
            index += 2
            continue
        if command == "H":
            cursor = [float(token), cursor[1]]
            points.append(list(cursor))
            index += 1
            continue
        if command == "V":
            cursor = [cursor[0], float(token)]
            points.append(list(cursor))
            index += 1
            continue
        raise ValueError("restriction SVG path contains an unsupported command")
    if len(points) < 4 or points[0] != points[-1]:
        raise ValueError("restriction SVG path is not a closed polygon")
    return points


def _polygon_centroid(points: list[list[float]]) -> list[float]:
    twice_area = 0.0
    x_sum = 0.0
    y_sum = 0.0
    for left, right in zip(points, points[1:]):
        cross = left[0] * right[1] - right[0] * left[1]
        twice_area += cross
        x_sum += (left[0] + right[0]) * cross
        y_sum += (left[1] + right[1]) * cross
    if abs(twice_area) < 1e-9:
        raise ValueError("restriction SVG polygon has zero area")
    return [x_sum / (3 * twice_area), y_sum / (3 * twice_area)]


def load_lumia_area_centers(path: Path, map_meta: dict) -> dict[int, list[float]]:
    """Project exact area-code polygons to deterministic world-space centroids."""
    shapes = json.loads(path.read_text(encoding="utf-8"))
    if (
        shapes.get("format") != "ercraft-lumia-area-paths.v1"
        or shapes.get("status") != "reference-exact-area-shapes-local-only"
        or shapes.get("viewBox") != [0, 0, 1544, 1962]
        or shapes.get("sourceMap") != {
            "width": (map_meta.get("coordinateSpace") or {}).get("w"),
            "height": (map_meta.get("coordinateSpace") or {}).get("h"),
        }
    ):
        raise ValueError("Lumia restriction area shape provenance is invalid")
    projection = map_meta.get("projection") or {}
    pixel_x = projection.get("pixelX")
    pixel_y = projection.get("pixelY")
    if (
        projection.get("type") != "affine-world-xz-to-source-pixel"
        or not isinstance(pixel_x, list)
        or not isinstance(pixel_y, list)
        or len(pixel_x) != 3
        or len(pixel_y) != 3
    ):
        raise ValueError("Lumia affine projection is invalid")
    a, b, c = pixel_x
    d, e, f = pixel_y
    determinant = a * e - b * d
    if abs(determinant) < 1e-9:
        raise ValueError("Lumia affine projection is not invertible")
    centers: dict[int, list[float]] = {}
    for raw_code, path_data in (shapes.get("paths") or {}).items():
        if not isinstance(raw_code, str) or not raw_code.isdigit() or not isinstance(path_data, str):
            raise ValueError("Lumia restriction area row is invalid")
        area_code = int(raw_code)
        view_x, view_y = _polygon_centroid(_svg_path_polygon(path_data))
        pixel_center_x = view_x / 2
        pixel_center_y = view_y / 2
        px = pixel_center_x - c
        py = pixel_center_y - f
        world_x = (px * e - b * py) / determinant
        world_z = (a * py - px * d) / determinant
        centers[area_code] = [round(world_x, 5), round(world_z, 5)]
    if set(centers) != set(range(10, 201, 10)):
        raise ValueError("Lumia restriction area codes are incomplete")
    return centers


def build_world_map_timeline(
    *,
    world_objects_by_id: dict[int, dict],
    spawn_positions_by_object: dict[int, list[list]],
    destroy_ticks_by_object: dict[int, int],
    object_timeline_events: list[dict],
    object_timeline_notice_observations: list[dict],
    unknown_object_timers: list[dict],
    wildlife: list[dict],
    boss_code_categories: dict[int, str],
    transport_mode_transitions: list[dict],
    area_centers: dict[int, list[float]],
    first_tick: int,
    end_tick: int,
    resource_box_updates: list[dict] | None = None,
    resource_item_box_events: list[dict] | None = None,
    resource_world_state_snapshots: list[dict] | None = None,
) -> dict:
    """Build exact-position map objects plus explicitly derived warning windows."""

    def observed_position(object_id: int) -> tuple[list[float] | None, str]:
        row = world_objects_by_id.get(object_id)
        if row is not None and row.get("position") is not None:
            return list(row["position"]), row["positionStatus"]
        positions = spawn_positions_by_object.get(object_id) or []
        if positions:
            return [positions[0][1], positions[0][2]], "decoded-exact-spawn-position"
        return None, "unavailable-no-exact-position"

    def exact_timeline_target(
        row: dict,
    ) -> tuple[int, list[float], str] | None:
        position, position_status = observed_position(row["objectId"])
        if position is not None:
            return row["objectId"], position, position_status
        if row.get("category") not in {"meteor", "tree-of-life"}:
            return None
        candidates = [
            item
            for item in world_objects_by_id.values()
            if item.get("category") == row["category"]
            and item.get("position") is not None
        ]
        scratch_linked = [
            item
            for item in candidates
            if item.get("spawnScratchObjectId") == row["objectId"]
        ]
        if len(scratch_linked) == 1:
            target = scratch_linked[0]
            return (
                target["objectId"],
                list(target["position"]),
                "decoded-exact-spawn-scratch-object-position-correlation",
            )
        same_tick = [
            item for item in candidates if item.get("firstSeenTick") == row["tick"]
        ]
        if len(same_tick) == 1:
            target = same_tick[0]
            return (
                target["objectId"],
                list(target["position"]),
                "decoded-exact-same-tick-world-object-position-correlation",
            )
        if isinstance(row.get("spawnDate"), int) and isinstance(
            row.get("areaCode"), int
        ):
            same_metadata = [
                item
                for item in candidates
                if item.get("spawnDate") == row["spawnDate"]
                and item.get("areaCode") == row["areaCode"]
            ]
            if len(same_metadata) == 1:
                target = same_metadata[0]
                return (
                    target["objectId"],
                    list(target["position"]),
                    "decoded-exact-area-date-world-object-position-correlation",
                )
        return None

    static_objects = []
    for row in world_objects_by_id.values():
        if row["category"] not in {
            "surveillance-camera", "control-lens", "cctv", "hyperloop",
            "kiosk", "campfire", "vls", "gold-cube", "lumi", "recon-orb",
            "bori-supply-box",
        }:
            continue
        # BoriSupplyBoxSnapshot carries the exact grade used to identify its
        # Bori encounter. Keep it even when this particular snapshot wrapper
        # has no position; the public layer never renders it as a second map
        # object and links it only through the exact Bori lifecycle window.
        if row.get("position") is None and row["category"] != "bori-supply-box":
            continue
        static_objects.append({
            **row,
            "destroyTick": destroy_ticks_by_object.get(row["objectId"]),
            "wireStatus": "decoded-exact-world-object-lifecycle",
        })

    timeline_by_object = {
        row["objectId"]: row for row in object_timeline_events
    }
    grade_categories: dict[int, str] = dict(AIR_SUPPLY_GRADE_CATEGORIES)
    air_rows = [
        row for row in world_objects_by_id.values()
        if row["objectType"] == 41 and isinstance(row.get("linkedObjectId"), int)
    ]
    for plan in air_rows:
        notice = timeline_by_object.get(plan["objectId"]) or timeline_by_object.get(
            plan["linkedObjectId"]
        )
        category = notice.get("category") if notice else None
        if category in {"air-supply-epic", "air-supply-mythic"} and isinstance(
            plan.get("itemGrade"), int
        ):
            previous = grade_categories.get(plan["itemGrade"])
            if previous is not None and previous != category:
                raise ValueError(
                    "air-supply grade metadata disagrees with same-replay "
                    f"timeline correlation: {plan['itemGrade']} "
                    f"{previous}->{category}"
                )
            grade_categories[plan["itemGrade"]] = category

    events = []
    consumed_timeline_ids: set[int] = set()
    resource_schedules: dict[int, dict] = {}
    resource_world_state_by_id: dict[int, list[dict]] = defaultdict(list)
    for row in resource_world_state_snapshots or []:
        object_id = row.get("objectId")
        if isinstance(object_id, int):
            resource_world_state_by_id[object_id].append(row)
    for observation in object_timeline_notice_observations:
        if (
            observation.get("objectId", 0) <= 0
            or observation.get("objectTimelineType") not in {1, 2}
            or observation.get("areaCode") not in range(10, 201, 10)
            or not isinstance(observation.get("spawnDate"), int)
        ):
            continue
        schedule = {
            "objectId": observation["objectId"],
            "objectTimelineType": observation["objectTimelineType"],
            "spawnDate": observation["spawnDate"],
            "areaCode": observation["areaCode"],
        }
        previous = resource_schedules.setdefault(observation["objectId"], schedule)
        if previous != schedule:
            raise ValueError(
                "tree/meteor timeline metadata changed for "
                f"{observation['objectId']}"
            )
    for schedule in resource_schedules.values():
        resource_position, resource_position_status = observed_position(
            schedule["objectId"]
        )
        if resource_position is None:
            continue
        timers = [
            row
            for row in unknown_object_timers
            if row.get("objectType") == 52
            and row.get("spawnDate") == schedule["spawnDate"]
        ]
        if len(timers) != 1:
            continue
        timer = timers[0]
        active_tick = timer["tick"] + round(
            timer["cooldownHundredths"] / 100 * 60
        )
        exact_destroy_tick = destroy_ticks_by_object.get(schedule["objectId"])
        same_object_collected_ticks = [
            row["tick"]
            for row in resource_world_state_by_id.get(schedule["objectId"], [])
            if isinstance(row.get("tick"), int)
            and row["tick"] >= active_tick
            and (
                row.get("isCollected") is True
                or row.get("remainCollectCount") == 0
            )
        ]
        exact_collection_ticks = [
            tick
            for tick in [
                exact_destroy_tick,
                (
                    min(same_object_collected_ticks)
                    if same_object_collected_ticks
                    else None
                ),
            ]
            if isinstance(tick, int)
        ]
        resource_end_tick = (
            min(exact_collection_ticks) if exact_collection_ticks else end_tick + 1
        )
        if not first_tick <= active_tick < resource_end_tick:
            raise ValueError(
                f"tree/meteor timer lifecycle is invalid for {schedule['objectId']}"
            )
        category = (
            "tree-of-life"
            if schedule["objectTimelineType"] == 1
            else "meteor"
        )
        events.append({
            "kind": category,
            "objectId": schedule["objectId"],
            "warningTick": max(first_tick, active_tick - 60 * 60),
            "activeTick": active_tick,
            "endTick": resource_end_tick,
            "endStatus": (
                "decoded-exact-CmdDestroy-for-same-resource-object"
                if exact_destroy_tick == resource_end_tick
                else (
                    "decoded-exact-same-resource-object-collected-snapshot-at-first-observation"
                    if same_object_collected_ticks
                    and min(same_object_collected_ticks) == resource_end_tick
                    else "unavailable-no-exact-resource-collection-lifecycle-visible-until-match-end"
                )
            ),
            "position": resource_position,
            "positionStatus": resource_position_status,
            "warningStatus": (
                "derived-user-requested-60-seconds-before-exact-timer-expiry"
            ),
            "activeStatus": (
                "derived-exact-unknown-object-timer-expiry"
            ),
            "classificationStatus": (
                "decoded-exact-object-timeline-id-type-date-area-timer-and-snapshot-position"
            ),
        })
        consumed_timeline_ids.add(schedule["objectId"])
    for plan in air_rows:
        target_id = plan["linkedObjectId"]
        actual = world_objects_by_id.get(target_id)
        category = grade_categories.get(plan.get("itemGrade"))
        if category is None or actual is None or actual.get("position") is None:
            continue
        notice = timeline_by_object.get(plan["objectId"]) or timeline_by_object.get(
            target_id
        )
        if notice:
            consumed_timeline_ids.add(notice["objectId"])
        events.append({
            "kind": category,
            "objectId": target_id,
            "warningTick": plan["firstSeenTick"],
            "activeTick": actual["firstSeenTick"],
            "endTick": destroy_ticks_by_object.get(target_id, end_tick + 1),
            "position": list(actual["position"]),
            "positionStatus": actual["positionStatus"],
            "warningStatus": "decoded-exact-air-supply-scratch-lifecycle",
            "activeStatus": "decoded-exact-air-supply-spawn-lifecycle",
            "classificationStatus": (
                "decoded-exact-object-timeline-grade-correlation"
                if notice
                else "unavailable-no-grade-label-correlation"
            ),
        })

    rift_warnings = [
        row for row in world_objects_by_id.values()
        if row["objectType"] == 54 and row.get("position") is not None
    ]
    rift_actuals = [
        row for row in world_objects_by_id.values()
        if row["objectType"] == 53 and row.get("position") is not None
    ]
    for warning in rift_warnings:
        candidates = [
            row for row in rift_actuals
            if row.get("areaCode") == warning.get("areaCode")
            and row.get("riftType") == warning.get("riftType")
            and row["firstSeenTick"] >= warning["firstSeenTick"]
        ]
        if not candidates:
            continue
        actual = min(candidates, key=lambda row: row["firstSeenTick"])
        events.append({
            "kind": "rift",
            "objectId": actual["objectId"],
            "warningTick": warning["firstSeenTick"],
            "activeTick": actual["firstSeenTick"],
            "endTick": destroy_ticks_by_object.get(actual["objectId"], end_tick + 1),
            "position": list(actual["position"]),
            "positionStatus": actual["positionStatus"],
            "warningStatus": "decoded-exact-rift-scratch-lifecycle",
            "activeStatus": "decoded-exact-rift-spawn-lifecycle",
            "classificationStatus": "decoded-exact-rift-type-and-area-pair",
        })

    for row in object_timeline_events:
        if row["objectId"] in consumed_timeline_ids or row.get("category") in {
            None, "alpha", "omega", "wickeline", "rift",
        }:
            continue
        target = exact_timeline_target(row)
        if target is None:
            continue
        target_id, position, position_status = target
        events.append({
            "kind": row["category"],
            "objectId": target_id,
            "warningTick": max(first_tick, row["tick"] - 60 * 60),
            "activeTick": row["tick"],
            "endTick": destroy_ticks_by_object.get(target_id, end_tick + 1),
            "position": position,
            "positionStatus": position_status,
            "warningStatus": "derived-user-requested-60-seconds-before-exact-notice",
            "activeStatus": row["wireStatus"],
            "classificationStatus": "decoded-exact-object-timeline-type",
        })

    for monster in wildlife:
        kind = boss_code_categories.get(monster.get("monsterCode"))
        track = monster.get("movementTrack") or []
        if kind is None or not track:
            continue
        active_tick = monster["spawnTick"]
        events.append({
            "kind": kind,
            "objectId": monster["objectId"],
            "warningTick": max(first_tick, active_tick - 60 * 60),
            "activeTick": active_tick,
            "endTick": (
                monster.get("deathTick")
                or monster.get("destroyTick")
                or monster.get("despawnTick")
                or end_tick + 1
            ),
            "position": [track[0][1], track[0][2]],
            "positionStatus": "decoded-exact-monster-spawn-position",
            "warningStatus": "derived-user-requested-60-seconds-before-exact-spawn",
            "activeStatus": "decoded-exact-monster-lifecycle",
            "classificationStatus": "exact-replay-version-Monster.json-code",
        })

    static_objects.sort(
        key=lambda row: (row["category"], row["firstSeenTick"], row["objectId"])
    )
    events.sort(key=lambda row: (row["warningTick"], row["activeTick"], row["kind"]))
    unpositioned_timeline = [
        {
            "tick": row["tick"],
            "objectId": row["objectId"],
            "objectTimelineType": row.get("objectTimelineType"),
            "category": row.get("category"),
            "spawnDate": row.get("spawnDate"),
            "areaCode": row.get("areaCode"),
            "areaCodeStatus": row.get("areaCodeStatus"),
            "wireStatus": row["wireStatus"],
        }
        for row in object_timeline_events
        if row.get("category") not in {"alpha", "omega", "wickeline", "rift"}
        and exact_timeline_target(row) is None
    ]
    return {
        "status": "decoded-exact-positions-and-lifecycles-with-explicit-derived-warning-windows",
        "warningLeadSeconds": 60,
        "staticObjects": static_objects,
        "events": events,
        "transportModeTransitions": sorted(
            transport_mode_transitions, key=lambda row: row["tick"]
        ),
        "unpositionedTimelineCount": len(unpositioned_timeline),
        "unpositionedTimeline": unpositioned_timeline,
        "unpositionedTimelineStatus": (
            "private-diagnostic-exact-timeline-metadata-without-map-position"
        ),
        "objectTimelineNoticeObservations": object_timeline_notice_observations,
        "unknownObjectTimers": unknown_object_timers,
        "resourceBoxUpdates": sorted(
            resource_box_updates or [], key=lambda row: row["tick"]
        ),
        "resourceBoxUpdateStatus": (
            "private-diagnostic-exact-CmdUpdateResourceBoxCooldown"
        ),
        "resourceItemBoxEvents": sorted(
            resource_item_box_events or [], key=lambda row: row["tick"]
        ),
        "resourceItemBoxEventStatus": (
            "private-diagnostic-exact-item-box-dictionary-object-id"
        ),
        "resourceWorldStateSnapshots": sorted(
            resource_world_state_snapshots or [], key=lambda row: row["tick"]
        ),
        "resourceWorldStateSnapshotStatus": (
            "private-diagnostic-exact-same-object-BaseResourceItemBoxSnapshot"
        ),
        "timelineScheduleDiagnosticStatus": (
            "private-diagnostic-exact-object-timeline-and-timer-metadata"
        ),
        "fallbackUsed": False,
    }


def build_catalog(config: AnalysisConfig) -> dict:
    config.validate()
    inspect = json.loads(config.inspect_path.read_text(encoding="utf-8"))
    client_version = inspect.get("format", {}).get("clientVersion")
    if not isinstance(client_version, str) or not client_version.strip():
        raise ValueError("inspect report is missing clientVersion")
    client_compatibility = require_supported_client_version(client_version)
    if client_version == '12.4.0':
        from decoder.replay_schema_inputs import schema_path_for_version
        if sha256(config.schema_path) != sha256(schema_path_for_version(client_version)):
            raise ValueError('12.4 analysis schema identity mismatch')
    game_data_authority = validate_game_data_archive(
        config.game_data_path,
        inspect.get("format", {}).get("gameDataUrl"),
    )
    character_capability_catalog = build_character_capability_catalog(
        config.game_data_path
    )
    projectile_skill_catalog = build_projectile_skill_catalog(
        config.game_data_path
    )
    spawn_report = json.loads(config.spawn_path.read_text(encoding="utf-8"))
    names = json.loads(config.names_path.read_text(encoding="utf-8"))
    names_supplement = json.loads(
        config.names_supplement_path.read_text(encoding="utf-8")
    )
    character_korean_names = {
        int(code): label for code, label in names["characters"].items()
    }
    character_korean_names.update({
        int(code): label
        for code, label in names_supplement["characters"].items()
    })
    enum_values, enum_statuses = enum_maps(config)
    stat_type_names = enum_values.get("StatType")
    if not isinstance(stat_type_names, dict) or not stat_type_names:
        raise ValueError("exact StatType enum catalog is missing")

    def require_stat_type_code(name: str) -> int:
        codes = [code for code, label in stat_type_names.items() if label == name]
        if len(codes) != 1:
            raise ValueError(f"exact StatType {name} is missing or ambiguous")
        return codes[0]

    max_hp_stat_code = require_stat_type_code("MaxHp")
    max_hp_bonus_stat_code = require_stat_type_code("MaxHpBonus")
    max_hp_ratio_stat_code = require_stat_type_code("MaxHpRatio")
    max_hp_stat_family = {
        max_hp_stat_code,
        max_hp_bonus_stat_code,
        max_hp_ratio_stat_code,
    }
    characters, skill_rows, skill_groups, item_rows, game_data_meta = load_game_data(config)
    with zipfile.ZipFile(config.game_data_path) as archive:
        character_state_rows = json.loads(archive.read("CharacterState.json"))
    world_game_data = load_world_game_data(config)
    player_ids = {row["objectId"] for row in inspect["playerSummary"]}
    first_tick = inspect["tickRange"]["firstDelta"]
    end_tick = inspect["tickRange"]["lastDelta"]

    replay = config.replay_path.read_bytes()
    replay_sha256 = hashlib.sha256(replay).hexdigest()
    inspect_sha256 = inspect.get("sha256")
    if isinstance(inspect_sha256, str) and inspect_sha256.lower() != replay_sha256:
        raise ValueError("inspect report replay SHA-256 mismatch")
    corpus_source = None
    if config.full_decode_path is not None:
        from decoder.corpus_runtime_source import CorpusRuntimeSource, restore as restore_corpus_packet
        corpus_source = CorpusRuntimeSource(config.full_decode_path, replay_sha256, client_version)
    def decode_wrapper(wrapper, name):
        if corpus_source is not None:
            return restore_corpus_packet(wrapper['corpusDecoded'])
        return decoder.decode_exact(wrapper['payload'], name)
    records = list(iter_records(replay))
    definitions, packet_names = load_definitions(records)
    decoder = SchemaDecoder(
        {
            definition["name"]: definition.get("baseType")
            for definition in definitions
            if isinstance(definition.get("name"), str)
        },
        schema_path=config.schema_path,
        client_version=client_version,
    )
    inner_snapshots = ExactInnerSnapshotCache(decoder, world_game_data["guideRobotCodes"])
    events = []
    decoded_counts = Counter()
    movement_by_player = defaultdict(list)
    movement_by_world_object = defaultdict(list)
    planned_paths_by_player = defaultdict(list)
    movement_packet_counts = Counter()
    monster_instances: list[dict] = []
    monster_index_by_object: dict[int, int] = {}
    monster_movement_packet_counts = Counter()
    noise_notifications: list[dict] = []
    noise_packet_count = 0
    tactical_pings: list[dict] = []
    restriction_updates: list[dict] = []
    gameplay_phase_updates: list[dict] = []
    spawn_positions_by_object: dict[int, list[list]] = defaultdict(list)
    world_objects_by_id: dict[int, dict] = {}
    summon_camera_diagnostics_by_id: dict[int, dict] = {}
    summon_trap_diagnostics_by_id: dict[int, dict] = {}
    installation_activations: list[dict] = []
    item_skill_actions: list[dict] = []
    destroy_ticks_by_object: dict[int, int] = {}
    object_timeline_events: list[dict] = []
    object_timeline_notices: list[dict] = []
    object_timeline_notice_observations: list[dict] = []
    object_timeline_notices_by_id: dict[int, dict] = {}
    unknown_object_timers: list[dict] = []
    resource_box_updates: list[dict] = []
    resource_item_box_events: list[dict] = []
    resource_world_state_snapshots: list[dict] = []
    boss_spawn_notices: list[dict] = []
    transport_mode_transitions: list[dict] = []
    scratch_group_updates: list[dict] = []
    survivable_time_updates_by_player: dict[int, list[list[int]]] = defaultdict(list)
    max_hp_stat_updates_by_player: dict[int, list[dict]] = defaultdict(list)
    projectile_spawns: list[dict] = []
    projectile_collisions: list[dict] = []
    finish_game_result = None
    finish_game_result_evidence = None
    spawn_semantics = {
        row["objectType"]: {
            "candidate": row["semanticCandidate"],
            "status": row["semanticCandidateStatus"],
        }
        for row in spawn_report["objectTypes"]
        if row.get("semanticCandidate") and row.get("semanticAllObserved")
    }
    object_owners: dict[int, int] = {}
    object_owner_statuses: dict[int, str] = {}
    observed_non_player_object_ids: set[int] = set()

    def record_world_wrapper(tick: int, snapshot_wrapper: dict, source: str) -> None:
        if not isinstance(snapshot_wrapper, dict):
            return
        object_id = snapshot_wrapper.get("objectId")
        object_type = snapshot_wrapper.get("objectType")
        if not isinstance(object_id, int) or not isinstance(object_type, int):
            return
        if object_id not in player_ids:
            # SnapshotWrapper.objectId/objectType is exact wire evidence.  Keep
            # every known non-player object, even when its nested snapshot is
            # wire-equivalent across several semantic candidates.
            observed_non_player_object_ids.add(object_id)
        position = vector2(snapshot_wrapper.get("positionXZ"))
        if position is not None:
            spawn_positions_by_object[object_id].append([
                tick,
                position[0],
                position[1],
                source,
            ])
        semantic = spawn_semantics.get(object_type)
        if object_type in {52, 57}:
            semantic = {
                "candidate": ("BaseResourceItemBoxSnapshot<concrete-object>"
                              if client_version == '12.4.0' else "BaseResourceItemBoxSnapshot"),
                "status": "decoded-exact-common-resource-snapshot-layout",
            }
        inner = None
        guide_state = None
        exact_guide_robot = inner_snapshots.decode_guide_robot(snapshot_wrapper.get("snapshot"))
        if exact_guide_robot is not None:
            inner, guide_state = exact_guide_robot
        elif semantic is not None and snapshot_wrapper.get("snapshot") is not None:
            inner = inner_snapshots.decode_candidate(
                snapshot_wrapper["snapshot"],
                semantic["candidate"],
            )
            if isinstance(inner, dict) and "value" in inner and "concreteType" in inner:
                inner = inner["value"]
        category = (
            "lumi"
            if exact_guide_robot is not None
            else WORLD_OBJECT_CATEGORIES.get(object_type)
        )
        if (
            object_type == 28
            and isinstance(inner, dict)
            and inner.get("buffCubeType") == 3
        ):
            category = "gold-cube"
        if object_type == 56:
            category = "bori-supply-box"
        summon_id = inner.get("summonId") if isinstance(inner, dict) else None
        if object_type == 10 and isinstance(inner, dict):
            owner_id = inner.get("ownerId")
            diagnostic = summon_trap_diagnostics_by_id.setdefault(object_id, {
                "objectId": object_id,
                "objectType": object_type,
                "summonId": summon_id,
                "ownerId": owner_id,
                "firstSeenTick": tick,
                "lastSeenTick": tick,
                "expireTimer": internal_value(inner.get("expireTimer")),
                "position": position,
                "positionTrack": [],
                "wireStatus": "decoded-exact-SummonTrap-wrapper-and-SummonSnapshot",
            })
            if diagnostic.get("summonId") != summon_id:
                raise ValueError(
                    f"SummonTrap {object_id} changed summonId "
                    f"{diagnostic.get('summonId')}->{summon_id}"
                )
            if diagnostic.get("ownerId") is None and owner_id is not None:
                diagnostic["ownerId"] = owner_id
            diagnostic["firstSeenTick"] = min(diagnostic["firstSeenTick"], tick)
            diagnostic["lastSeenTick"] = max(diagnostic["lastSeenTick"], tick)
            if diagnostic.get("position") is None and position is not None:
                diagnostic["position"] = position
            if position is not None:
                anchor = [tick, position[0], position[1], source]
                if not diagnostic["positionTrack"] or diagnostic["positionTrack"][-1] != anchor:
                    diagnostic["positionTrack"].append(anchor)
        if object_type == 9:
            owner_id = inner.get("ownerId") if isinstance(inner, dict) else None
            summon_definition = (
                world_game_data["summonObjects"].get(summon_id)
                if isinstance(summon_id, int)
                else None
            )
            diagnostic = summon_camera_diagnostics_by_id.setdefault(object_id, {
                "objectId": object_id,
                "objectType": object_type,
                "summonId": summon_id,
                "ownerId": owner_id,
                "firstSeenTick": tick,
                "lastSeenTick": tick,
                "position": position,
                "positionTrack": (
                    [[tick, position[0], position[1], source]]
                    if position is not None
                    else []
                ),
                "positionStatus": (
                    "decoded-exact-snapshot-wrapper-position"
                    if position is not None
                    else "unavailable-no-position-in-wrapper"
                ),
                "firstSource": source,
                "lastSource": source,
                "gameData": (
                    {
                        "name": summon_definition.get("name"),
                        "objectStatsType": summon_definition.get("objectStatsType"),
                        "showMiniMap": summon_definition.get("showMiniMap"),
                    }
                    if isinstance(summon_definition, dict)
                    else None
                ),
                "wireStatus": (
                    "decoded-exact-SummonCamera-wrapper-and-SummonSnapshot"
                ),
            })
            if diagnostic.get("summonId") != summon_id:
                raise ValueError(
                    f"SummonCamera {object_id} changed summonId "
                    f"{diagnostic.get('summonId')}->{summon_id}"
                )
            if (
                diagnostic.get("ownerId") is not None
                and owner_id is not None
                and diagnostic["ownerId"] != owner_id
            ):
                raise ValueError(
                    f"SummonCamera {object_id} changed ownerId "
                    f"{diagnostic.get('ownerId')}->{owner_id}"
                )
            if diagnostic.get("ownerId") is None and owner_id is not None:
                diagnostic["ownerId"] = owner_id
            if tick < diagnostic["firstSeenTick"]:
                diagnostic["firstSeenTick"] = tick
                diagnostic["firstSource"] = source
            if tick >= diagnostic["lastSeenTick"]:
                diagnostic["lastSeenTick"] = tick
                diagnostic["lastSource"] = source
            if diagnostic.get("position") is None and position is not None:
                diagnostic["position"] = position
                diagnostic["positionStatus"] = (
                    "decoded-exact-snapshot-wrapper-position"
                )
            if position is not None and not any(
                anchor[0] == tick
                and anchor[1] == position[0]
                and anchor[2] == position[1]
                and anchor[3] == source
                for anchor in diagnostic["positionTrack"]
            ):
                diagnostic["positionTrack"].append(
                    [tick, position[0], position[1], source]
                )
        summon_category = summon_world_category(
            object_type, summon_id, world_game_data
        )
        if summon_category is not None:
            category = summon_category
        if object_type in {8, 41}:
            category = "air-supply"
        if category is None:
            return
        row = world_objects_by_id.setdefault(object_id, {
            "objectId": object_id,
            "objectType": object_type,
            "objectTypeName": enum_values.get("ObjectType", {}).get(
                object_type, f"Unknown{object_type}"
            ),
            "category": category,
            "firstSeenTick": tick,
            "position": position,
            "positionStatus": (
                "decoded-exact-snapshot-wrapper-position"
                if position is not None
                else "unavailable-no-position-in-wrapper"
            ),
            "source": source,
        })
        if tick < row["firstSeenTick"]:
            row["firstSeenTick"] = tick
            row["source"] = source
        if row.get("position") is None and position is not None:
            row["position"] = position
            row["positionStatus"] = "decoded-exact-snapshot-wrapper-position"
        if isinstance(inner, dict):
            for key in (
                "ownerId", "summonId", "itemGrade", "objectId", "riftType",
                "areaCode", "cooldownUntil", "maxItemGrade", "buffCubeType",
                "spawnScratchObjectId", "supportPackType", "expireTimer",
                "respawnTime", "respawnCooldownTime", "spawnDate", "dateUntil",
                "isCollected", "remainCollectCount", "itemSpawnPointCode",
                "touringObjectCode", "touringTargetAreaCode", "movePointGroup",
                "touringReverse", "destroyReserve", "boxGrade", "capacity",
            ):
                value = inner.get(key)
                if value is not None:
                    output_key = "linkedObjectId" if key == "objectId" else key
                    row[output_key] = internal_value(value)
            if category == "lumi":
                if not isinstance(guide_state, dict):
                    raise ValueError("GuideRobotSnapshot did not decode exactly")
                row["guideRobotState"] = guide_state.get("guideRobotState")
                row["creditRich"] = guide_state.get("creditRich")
                row["stopStartPhase"] = guide_state.get("stopStartPhase")
                state_anchor = [
                    tick,
                    guide_state.get("guideRobotState"),
                    guide_state.get("creditRich"),
                ]
                state_timeline = row.setdefault("guideRobotStateTimeline", [])
                if not state_timeline or state_timeline[-1] != state_anchor:
                    state_timeline.append(state_anchor)
                row["guideRobotStatus"] = (
                    "decoded-exact-TouringObjectSnapshot-and-GuideRobotSnapshot"
                )
            if object_type in {52, 57}:
                resource_world_state_snapshots.append({
                    "tick": tick,
                    "objectId": object_id,
                    "objectType": object_type,
                    "isCollected": inner.get("isCollected"),
                    "remainCollectCount": inner.get("remainCollectCount"),
                    "cooldownUntilHundredths": internal_value(
                        inner.get("cooldownUntil")
                    ),
                    "dateUntil": inner.get("dateUntil"),
                    "itemSpawnPointCode": inner.get("itemSpawnPointCode"),
                    "wireStatus": (
                        "decoded-exact-same-object-BaseResourceItemBoxSnapshot"
                    ),
                })
            drop_position = inner.get("dropPosition")
            if (
                row.get("position") is None
                and isinstance(drop_position, list)
                and len(drop_position) >= 3
                and all(isinstance(item, (int, float)) for item in drop_position[:3])
            ):
                row["position"] = [round(drop_position[0], 5), round(drop_position[2], 5)]
                row["positionStatus"] = "decoded-exact-air-supply-drop-position"

    def record_monster_wrapper(
        tick: int,
        snapshot_wrapper: dict,
        source: str,
    ) -> None:
        if not isinstance(snapshot_wrapper, dict):
            return
        object_type = snapshot_wrapper.get("objectType")
        semantic = spawn_semantics.get(object_type)
        if (
            semantic is None
            or semantic.get("candidate") != "MonsterSnapshot"
            or snapshot_wrapper.get("snapshot") is None
        ):
            return
        inner = inner_snapshots.decode_candidate(
            snapshot_wrapper["snapshot"],
            semantic["candidate"],
        )
        if isinstance(inner, dict) and "value" in inner and "concreteType" in inner:
            inner = inner["value"]
        if not isinstance(inner, dict):
            return
        object_id = snapshot_wrapper.get("objectId")
        if not isinstance(object_id, int):
            return
        previous_index = monster_index_by_object.get(object_id)
        if previous_index is not None:
            previous = monster_instances[previous_index]
            if source == "first-full-snapshot":
                return
            if previous.get("despawnTick") is None:
                previous["despawnTick"] = tick
        status = {}
        status_bytes = inner.get("statusSnapshot")
        if isinstance(status_bytes, (bytes, bytearray)):
            status = inner_snapshots.decode_candidate(
                status_bytes,
                "MonsterStatusSnapshot",
            )
            if (
                isinstance(status, dict)
                and "value" in status
                and "concreteType" in status
            ):
                status = status["value"]
        position = vector2(snapshot_wrapper.get("positionXZ"))
        movement_track = []
        if position is not None:
            movement_track.append([
                tick,
                position[0],
                position[1],
                source,
            ])
        monster_instances.append({
            "objectId": object_id,
            "monsterCode": inner.get("monsterCode"),
            "spawnTick": tick,
            "initialAlive": bool(inner.get("isAlive")),
            "initialHp": status.get("hp") if isinstance(status, dict) else None,
            "movementTrack": movement_track,
            "deathTick": None,
            "deathKillerObjectId": None,
            "destroyTick": None,
            "despawnTick": None,
            "wireStatus": (
                "decoded-exact-initial-full-snapshot-monster"
                if source == "first-full-snapshot"
                else "decoded-exact-monster-snapshot"
            ),
        })
        monster_index_by_object[object_id] = len(monster_instances) - 1

    # Full snapshots carry SnapshotWrapperFull positionXZ. CmdSpawn often uses
    # SnapshotWrapperBasic, which has no wrapper position. Scan every keyframe
    # so later Full wrappers can attach exact coordinates to already-classified
    # player cameras and drones.
    full_snapshot_records = [
        record
        for record in records
        if record["kind"] == 2 and record["version"] == 1
    ]
    if not full_snapshot_records:
        raise ValueError("replay has no full snapshot for world-object projection")
    corpus_snapshots = iter(corpus_source.snapshots()) if corpus_source is not None else None
    for snapshot_index, snapshot_record in enumerate(full_snapshot_records):
        if corpus_snapshots is not None:
            saved = next(corpus_snapshots, None)
            if saved is None or saved[0] != snapshot_record['tick']:
                raise ValueError('full decode snapshot sequence differs from replay')
            full_snapshot = saved[1]
        else:
            full_snapshot = decoder.decode_exact(brotli.decompress(snapshot_record["payload"]), "ReplaySnapshot")
        game_snapshot = full_snapshot.get("gameSnapshot") or {}
        source = (
            "first-full-snapshot"
            if snapshot_index == 0
            else "later-full-snapshot"
        )
        for snapshot_wrapper in game_snapshot.get("worldSnapshot") or []:
            record_world_wrapper(
                snapshot_record["tick"],
                snapshot_wrapper,
                source,
            )
            if snapshot_index == 0:
                record_monster_wrapper(
                    snapshot_record["tick"],
                    snapshot_wrapper,
                    source,
                )

    if corpus_snapshots is not None and next(corpus_snapshots, None) is not None:
        raise ValueError('full decode has extra snapshots')
    corpus_deltas = iter(corpus_source.deltas(packet_names.values(), runtime_only=True,
                                             retain_item_box_envelopes=True)) if corpus_source is not None else None
    for record in records:
        if record["kind"] != 1 or record["version"] != 1:
            continue
        if corpus_deltas is not None:
            saved = next(corpus_deltas, None)
            if saved is None or saved['tick'] != record['tick']:
                raise ValueError('full decode delta sequence differs from replay')
            delta = saved['corpusDelta']
        else:
            delta = parse_delta_payload(record["payload"], record["tick"])
        for wrapper_category in WRAPPER_CODES:
            for wrapper_index, wrapper in enumerate(delta[wrapper_category]):
                packet_name = packet_names.get(wrapper["packetType"])
                if packet_name == "CmdInSightRange":
                    sight_value = decode_wrapper(wrapper, packet_name)
                    sight_object_ids = sight_value.get("objectIds")
                    sight_positions = sight_value.get("xzPositions")
                    if not isinstance(sight_object_ids, list) or not isinstance(
                        sight_positions, list
                    ):
                        raise ValueError(
                            "CmdInSightRange objectIds/xzPositions are not exact lists"
                        )
                    if len(sight_object_ids) != len(sight_positions):
                        raise ValueError(
                            "CmdInSightRange objectIds/xzPositions length mismatch"
                        )
                    for sight_object_id, sight_position_raw in zip(
                        sight_object_ids, sight_positions
                    ):
                        diagnostic = summon_camera_diagnostics_by_id.get(
                            sight_object_id
                        )
                        if diagnostic is None:
                            continue
                        sight_position = vector2(sight_position_raw)
                        if sight_position is None:
                            raise ValueError(
                                "CmdInSightRange camera position is not an exact Vector2"
                            )
                        source = "CmdInSightRange"
                        anchor = [
                            record["tick"],
                            sight_position[0],
                            sight_position[1],
                            source,
                        ]
                        diagnostic["positionTrack"].append(anchor)
                        if diagnostic.get("position") is None:
                            diagnostic["position"] = sight_position
                            diagnostic["positionStatus"] = (
                                "decoded-exact-CmdInSightRange-position"
                            )
                        spawn_positions_by_object[sight_object_id].append(anchor)
                        world_object = world_objects_by_id.get(sight_object_id)
                        if world_object is not None and world_object.get(
                            "position"
                        ) is None:
                            world_object["position"] = sight_position
                            world_object["positionStatus"] = (
                                "decoded-exact-CmdInSightRange-position"
                            )
                    decoded_counts[packet_name] += 1
                if (
                    wrapper_category == "itemBoxPackets"
                    and packet_name in {"CmdItemBoxRemove", "CmdItemBoxUpdate"}
                    and isinstance(wrapper.get("itemBoxObjectId"), int)
                ):
                    item_box_value = decode_wrapper(wrapper, packet_name)
                    resource_item_box_events.append({
                        "tick": record["tick"],
                        "itemBoxObjectId": wrapper["itemBoxObjectId"],
                        "packetName": packet_name,
                        "itemId": item_box_value.get("itemId"),
                        "remainCount": item_box_value.get("remainCount"),
                        "wireStatus": (
                            "decoded-exact-item-box-dictionary-key-and-payload"
                        ),
                    })
                if packet_name in MOVEMENT_PACKETS:
                    movement_value = decode_wrapper(wrapper, packet_name)
                    movement_packet_counts[packet_name] += 1
                    movement_object_id = movement_value.get("objectId")
                    if movement_object_id in player_ids:
                        movement_by_player[movement_object_id].extend(
                            movement_anchors(
                                packet_name, record["tick"], movement_value
                            )
                        )
                        path = planned_path(
                            packet_name, record["tick"], movement_value
                        )
                        if path is not None:
                            planned_paths_by_player[movement_object_id].append(path)
                    monster_index = monster_index_by_object.get(movement_object_id)
                    if monster_index is not None:
                        monster_anchors = movement_anchors(
                            packet_name, record["tick"], movement_value
                        )
                        if monster_anchors:
                            monster_instances[monster_index]["movementTrack"].extend(
                                monster_anchors
                            )
                            monster_movement_packet_counts[packet_name] += 1
                    elif (
                        isinstance(movement_object_id, int)
                        and movement_object_id not in player_ids
                    ):
                        movement_by_world_object[movement_object_id].extend(
                            movement_anchors(
                                packet_name, record["tick"], movement_value
                            )
                        )
                if packet_name in {
                    "CmdSpawn", "CmdSpawns", "CmdSpawnAirSupplyItemBox"
                }:
                    spawn_value = decode_wrapper(wrapper, packet_name)
                    if packet_name == "CmdSpawn":
                        snapshot_wrappers = [spawn_value["snapshot"]]
                    elif packet_name == "CmdSpawns":
                        snapshot_wrappers = spawn_value["snapshots"]
                    else:
                        snapshot_wrappers = spawn_value["spawnSnapshots"]
                    for snapshot_wrapper in snapshot_wrappers or []:
                        if snapshot_wrapper is None:
                            continue
                        record_world_wrapper(
                            record["tick"], snapshot_wrapper, packet_name
                        )
                        if snapshot_wrapper.get("snapshot") is None:
                            continue
                        semantic = spawn_semantics.get(snapshot_wrapper["objectType"])
                        if semantic is None:
                            continue
                        record_monster_wrapper(
                            record["tick"], snapshot_wrapper, packet_name
                        )
                        inner = inner_snapshots.decode_candidate(
                            snapshot_wrapper["snapshot"],
                            semantic["candidate"],
                        )
                        if isinstance(inner, dict) and "value" in inner and "concreteType" in inner:
                            inner = inner["value"]
                        owner_id = inner.get("ownerId") if isinstance(inner, dict) else None
                        if not isinstance(owner_id, int) or owner_id == 0:
                            continue
                        object_id = snapshot_wrapper["objectId"]
                        previous = object_owners.setdefault(object_id, owner_id)
                        if previous != owner_id:
                            raise ValueError(
                                f"spawn object {object_id} changed owner {previous}->{owner_id}"
                            )
                        object_owner_statuses[object_id] = semantic["status"]
                        if snapshot_wrapper.get("objectType") in PROJECTILE_OBJECT_TYPES:
                            projectile_code = (
                                inner.get("code") if isinstance(inner, dict) else None
                            )
                            if not isinstance(projectile_code, int):
                                raise ValueError(
                                    f"projectile spawn {object_id} has no exact code"
                                )
                            projectile_spawns.append({
                                "tick": record["tick"],
                                "projectileObjectId": object_id,
                                "projectileCode": projectile_code,
                                "ownerObjectId": owner_id,
                                "objectType": snapshot_wrapper.get("objectType"),
                                "wireStatus": (
                                    "decoded-exact-CmdSpawn-projectile-code-owner"
                                ),
                            })
                if packet_name == "CmdNoticeObjectTimeline":
                    timeline_notice_value = decode_wrapper(wrapper, packet_name)
                    for timeline_notice in (
                        timeline_notice_value.get("objectTimelineDatas") or []
                    ):
                        if not isinstance(timeline_notice, dict):
                            continue
                        notice_object_id = timeline_notice.get("objectId")
                        notice_type = timeline_notice.get("objectTimelineType")
                        if not isinstance(notice_object_id, int) or not isinstance(
                            notice_type, int
                        ):
                            continue
                        normalized_notice = {
                            "objectId": notice_object_id,
                            "objectTimelineType": notice_type,
                            "spawnDate": timeline_notice.get("spawnDate"),
                            "areaCode": timeline_notice.get("areaCode"),
                        }
                        object_timeline_notice_observations.append({
                            "tick": record["tick"],
                            **normalized_notice,
                            "isCollected": bool(
                                timeline_notice.get("isCollected")
                            ),
                        })
                        if normalized_notice not in object_timeline_notices:
                            object_timeline_notices.append(normalized_notice)
                        if notice_object_id > 0:
                            previous_notice = object_timeline_notices_by_id.setdefault(
                                notice_object_id, normalized_notice
                            )
                            if previous_notice != normalized_notice:
                                raise ValueError(
                                    "object timeline notice metadata changed for "
                                    f"{notice_object_id}"
                                )
                if packet_name == "CmdNoticeObjectTimelineSpawned":
                    timeline_value = decode_wrapper(wrapper, packet_name).get("objectTimelineData") or {}
                    timeline_type = timeline_value.get("objectTimelineType")
                    if isinstance(timeline_value.get("objectId"), int) and isinstance(
                        timeline_type, int
                    ):
                        timeline_object_id = timeline_value["objectId"]
                        notice = object_timeline_notices_by_id.get(
                            timeline_object_id
                        )
                        area_code = timeline_value.get("areaCode")
                        area_status = "decoded-exact-spawned-timeline-area-code"
                        if (
                            area_code not in range(10, 201, 10)
                            and notice is not None
                            and notice.get("objectTimelineType") == timeline_type
                            and notice.get("areaCode") in range(10, 201, 10)
                        ):
                            area_code = notice["areaCode"]
                            area_status = (
                                "decoded-exact-same-object-timeline-notice-area-code"
                            )
                        object_timeline_events.append({
                            "tick": record["tick"],
                            "objectId": timeline_object_id,
                            "objectTimelineType": timeline_type,
                            "objectTimelineTypeName": enum_values.get(
                                "ObjectTimelineType", {}
                            ).get(timeline_type, f"Unknown{timeline_type}"),
                            "category": OBJECT_TIMELINE_CATEGORIES.get(timeline_type),
                            "spawnDate": timeline_value.get("spawnDate"),
                            "areaCode": area_code,
                            "areaCodeStatus": area_status,
                            "isCollected": bool(timeline_value.get("isCollected")),
                            "wireStatus": "decoded-exact-object-timeline-notice",
                        })
                if packet_name == "CmdAddUnknownObjectTimer":
                    timer_value = decode_wrapper(wrapper, packet_name)
                    timer_object_type = timer_value.get("objectType")
                    timer_cooldown = internal_value(timer_value.get("cooldown"))
                    timer_spawn_date = timer_value.get("spawnDate")
                    if (
                        isinstance(timer_object_type, int)
                        and isinstance(timer_cooldown, int)
                        and timer_cooldown >= 0
                        and isinstance(timer_spawn_date, int)
                    ):
                        unknown_object_timers.append({
                            "tick": record["tick"],
                            "objectType": timer_object_type,
                            "cooldownHundredths": timer_cooldown,
                            "spawnDate": timer_spawn_date,
                            "wireStatus": (
                                "decoded-exact-unknown-object-timer"
                            ),
                        })
                if packet_name == "CmdUpdateResourceBoxCooldown":
                    resource_value = decode_wrapper(wrapper, packet_name)
                    resource_object_id = resource_value.get("objectId")
                    resource_cooldown = internal_value(
                        resource_value.get("cooldown")
                    )
                    resource_spawn_date = resource_value.get("spawnDate")
                    remain_collect_count = resource_value.get(
                        "remainCollectCount"
                    )
                    if (
                        isinstance(resource_object_id, int)
                        and isinstance(resource_cooldown, int)
                        and resource_cooldown >= 0
                        and isinstance(resource_spawn_date, int)
                        and isinstance(remain_collect_count, int)
                        and remain_collect_count >= 0
                    ):
                        resource_box_updates.append({
                            "tick": record["tick"],
                            "objectId": resource_object_id,
                            "cooldownHundredths": resource_cooldown,
                            "spawnDate": resource_spawn_date,
                            "remainCollectCount": remain_collect_count,
                            "wireStatus": (
                                "decoded-exact-resource-box-cooldown"
                            ),
                        })
                if packet_name == "CmdNoticeBossMonsterSpawnStart":
                    notice_value = decode_wrapper(wrapper, packet_name)
                    for monster_type, schedules in notice_value.get("lifeCycles") or []:
                        if not isinstance(monster_type, int):
                            continue
                        for schedule in schedules or []:
                            boss_spawn_notices.append({
                                "tick": record["tick"],
                                "monsterType": monster_type,
                                "monsterTypeName": enum_values.get(
                                    "MonsterType", {}
                                ).get(monster_type, f"Unknown{monster_type}"),
                                "scheduledTime": internal_value(schedule.get("time")),
                                "spawnDate": schedule.get("spawnDate"),
                                "position": schedule.get("pos"),
                                "spawnAreaCode": schedule.get("spawnAreaCode"),
                                "wireStatus": "decoded-exact-boss-spawn-notice",
                            })
                if packet_name == "CmdChangeHyperLoopToVLS":
                    decode_wrapper(wrapper, packet_name)
                    transport_mode_transitions.append({
                        "tick": record["tick"],
                        "mode": "vls",
                        "wireStatus": "decoded-exact-global-transport-transition",
                    })
                if packet_name == "CmdScratchGroupUpdate":
                    group_value = decode_wrapper(wrapper, packet_name)
                    group_object_id = group_value.get("objectId")
                    member_object_ids = group_value.get("spawnObjectIds") or []
                    if (
                        isinstance(group_object_id, int)
                        and all(isinstance(item, int) for item in member_object_ids)
                    ):
                        scratch_group_updates.append({
                            "tick": record["tick"],
                            "groupObjectId": group_object_id,
                            "spawnObjectIds": list(member_object_ids),
                            "isRestore": bool(group_value.get("isRestore")),
                        })
                if packet_name == "CmdNoise":
                    noise_packet_count += 1
                    noise_value = decode_wrapper(wrapper, packet_name)
                    noise_type = noise_value.get("noiseType")
                    noise_type_name = enum_values.get("NoiseType", {}).get(noise_type)
                    rule = (
                        noise_visibility_rule(noise_type_name)
                        if isinstance(noise_type_name, str)
                        else None
                    )
                    source_position = vector2([
                        noise_value.get("noisePosX"),
                        noise_value.get("noisePosZ"),
                    ], 100)
                    if rule is not None and source_position is not None:
                        noise_notifications.append({
                            "tick": record["tick"],
                            "noiseType": noise_type,
                            "noiseTypeName": noise_type_name,
                            "label": rule["label"],
                            "radiusMeters": rule["radiusMeters"],
                            "cooldownSeconds": rule["cooldownSeconds"],
                            "sourcePosition": source_position,
                            "creatorObjectId": noise_value.get("creatorObjectId"),
                            "wireStatus": "decoded-exact-source-derived-recipient-rule",
                        })
                if packet_name == "CmdPing":
                    ping_value = decode_wrapper(wrapper, packet_name)
                    ping_position = vector2(ping_value.get("pingPositionVector2"))
                    ping_type = ping_value.get("type")
                    if ping_position is not None and isinstance(ping_type, int):
                        tactical_pings.append({
                            "tick": record["tick"],
                            "type": ping_type,
                            "typeName": enum_values.get("TacticalPingType", {}).get(
                                ping_type, f"Unknown{ping_type}"
                            ),
                            "senderObjectId": ping_value.get("senderObjectId"),
                            "targetObjectId": ping_value.get("pingObjectId"),
                            "position": ping_position,
                            "wireStatus": "decoded-exact-tactical-ping",
                        })
                if packet_name == "CmdUpdateRestrictedArea":
                    restricted_value = decode_wrapper(wrapper, packet_name)
                    day_night = restricted_value.get("dayNight")
                    restriction_updates.append({
                        "tick": record["tick"],
                        "day": restricted_value.get("day"),
                        "dayNight": day_night,
                        "dayNightName": enum_values.get("DayNight", {}).get(
                            day_night, f"Unknown{day_night}"
                        ),
                        "phase": restricted_value.get("phase"),
                        "remainSeconds": restricted_value.get("remainTime"),
                        "areas": restriction_area_rows(
                            restricted_value, enum_values
                        ),
                        "wireStatus": "decoded-exact-restricted-area-clock",
                    })
                if packet_name == "CmdUpdateGamePlayPhase":
                    phase_value = decode_wrapper(wrapper, packet_name)
                    game_phase = phase_value.get("gamePlayPhase")
                    gameplay_phase_updates.append({
                        "tick": record["tick"],
                        "gamePlayPhase": game_phase,
                        "gamePlayPhaseName": enum_values.get(
                            "GamePlayPhase", {}
                        ).get(game_phase, f"Unknown{game_phase}"),
                        "remainSeconds": (
                            internal_value(
                                phase_value.get("gamePlayPhaseRemainTime")
                            ) / 100
                            if isinstance(
                                internal_value(
                                    phase_value.get("gamePlayPhaseRemainTime")
                                ),
                                (int, float),
                            )
                            else None
                        ),
                        "startSeconds": (
                            internal_value(
                                phase_value.get("gamePlayPhaseStartTime")
                            ) / 100
                            if isinstance(
                                internal_value(
                                    phase_value.get("gamePlayPhaseStartTime")
                                ),
                                (int, float),
                            )
                            else None
                        ),
                        "endSeconds": (
                            internal_value(
                                phase_value.get("gamePlayPhaseEndTime")
                            ) / 100
                            if isinstance(
                                internal_value(
                                    phase_value.get("gamePlayPhaseEndTime")
                                ),
                                (int, float),
                            )
                            else None
                        ),
                        "wireStatus": "decoded-exact-gameplay-phase-clock",
                    })
                if packet_name in {"CmdDead", "CmdDestroy"}:
                    lifecycle_value = decode_wrapper(wrapper, packet_name)
                    monster_index = monster_index_by_object.get(
                        lifecycle_value.get("objectId")
                    )
                    if monster_index is not None:
                        monster = monster_instances[monster_index]
                        if packet_name == "CmdDead" and monster["deathTick"] is None:
                            monster["deathTick"] = record["tick"]
                            monster["deathKillerObjectId"] = lifecycle_value.get(
                                "finishingAttackerObjectId"
                            )
                        elif packet_name == "CmdDestroy" and monster["destroyTick"] is None:
                            monster["destroyTick"] = record["tick"]
                    if packet_name == "CmdDead":
                        dead_object = world_objects_by_id.get(lifecycle_value.get("objectId"))
                        if dead_object and dead_object.get("category") == "recon-orb":
                            dead_object.setdefault("deathTick", record["tick"])
                    if packet_name == "CmdDestroy" and isinstance(
                        lifecycle_value.get("objectId"), int
                    ):
                        destroy_ticks_by_object.setdefault(
                            lifecycle_value["objectId"], record["tick"]
                        )
                if packet_name == "CmdFinishGameResult":
                    if finish_game_result is not None:
                        raise ValueError("multiple CmdFinishGameResult packets observed")
                    finish_game_result = decode_wrapper(wrapper, packet_name)
                    finish_game_result_evidence = {
                        "packetType": wrapper["packetType"],
                        "packetName": packet_name,
                        "tick": record["tick"],
                        "wrapperCategory": wrapper_category,
                        "wrapperIndex": wrapper_index,
                        "status": "decoded-exact-wire",
                    }
                    decoded_counts[packet_name] += 1
                if packet_name == "CmdUpdateSurvivableTimeForTeam":
                    survivable_value = decode_wrapper(wrapper, packet_name)
                    survivable_object_id = survivable_value.get("objectId")
                    survivable_seconds = survivable_value.get("survivalTime")
                    if (
                        survivable_object_id in player_ids
                        and isinstance(survivable_seconds, int)
                        and survivable_seconds >= 0
                    ):
                        survivable_time_updates_by_player[
                            survivable_object_id
                        ].append([record["tick"], survivable_seconds])
                    decoded_counts[packet_name] += 1
                if packet_name in {
                    "CmdUpdateStat",
                    "CmdBroadcastUpdateStat",
                    "CmdUpdateSubtractAdditionalStat",
                }:
                    stat_update_value = decode_wrapper(wrapper, packet_name)
                    stat_object_id = stat_update_value.get("objectId")
                    updates = stat_update_value.get("updates")
                    if not isinstance(updates, list):
                        raise ValueError(f"{packet_name} updates are not an exact list")
                    relevant_updates = []
                    for update in updates:
                        if not isinstance(update, dict):
                            raise ValueError(
                                f"{packet_name} contains an invalid CharacterStatValue"
                            )
                        stat_type = update.get("statType")
                        stat_value = update.get("value")
                        if not isinstance(stat_type, int) or not isinstance(
                            stat_value, int
                        ):
                            raise ValueError(
                                f"{packet_name} contains a non-exact CharacterStatValue"
                            )
                        if stat_type in max_hp_stat_family:
                            relevant_updates.append({
                                "statType": stat_type,
                                "statTypeName": stat_type_names[stat_type],
                                "value": stat_value,
                            })
                    if stat_object_id in player_ids and relevant_updates:
                        max_hp_stat_updates_by_player[stat_object_id].append({
                            "tick": record["tick"],
                            "packetName": packet_name,
                            "updates": relevant_updates,
                            "wireStatus": "decoded-exact-CharacterStatValue-update",
                        })
                    decoded_counts[packet_name] += 1
                if packet_name == "CmdInstallationActivate":
                    installation_value = decode_wrapper(wrapper, packet_name)
                    installation_activations.append({
                        "tick": record["tick"],
                        "objectId": installation_value.get("objectId"),
                        "characterObjectId": installation_value.get(
                            "characterObjectId"
                        ),
                        "wireStatus": "decoded-exact-CmdInstallationActivate",
                    })
                    decoded_counts[packet_name] += 1
                if packet_name == "CmdPlayItemSkillAction":
                    item_action_value = decode_wrapper(wrapper, packet_name)
                    target_position = item_action_value.get("targetPos")
                    item_skill_actions.append({
                        "tick": record["tick"],
                        "objectId": item_action_value.get("objectId"),
                        "skillId": item_action_value.get("skillId"),
                        "itemId": item_action_value.get("itemId"),
                        "itemSkillCode": item_action_value.get("itemSkillCode"),
                        "actionNo": item_action_value.get("actionNo"),
                        "targetId": item_action_value.get("targetId"),
                        "targetPosition": (
                            [
                                round(target_position[0], 5),
                                round(target_position[2], 5),
                            ]
                            if isinstance(target_position, list)
                            and len(target_position) >= 3
                            and all(
                                isinstance(item, (int, float))
                                for item in target_position[:3]
                            )
                            else None
                        ),
                        "wireStatus": "decoded-exact-CmdPlayItemSkillAction",
                    })
                    decoded_counts[packet_name] += 1
                if packet_name not in EVENT_SPECS:
                    continue
                value = decode_wrapper(wrapper, packet_name)
                decoded_counts[packet_name] += 1
                if packet_name == "CmdProjectileCollision":
                    projectile_object_id = value.get("objectId")
                    target_object_id = value.get("targetId")
                    if not isinstance(projectile_object_id, int) or not isinstance(
                        target_object_id, int
                    ):
                        raise ValueError(
                            "CmdProjectileCollision has no exact objectId/targetId"
                        )
                    projectile_collisions.append({
                        "tick": record["tick"],
                        "projectileObjectId": projectile_object_id,
                        "targetObjectId": target_object_id,
                        "wireStatus": (
                            "decoded-exact-CmdProjectileCollision-object-target"
                        ),
                    })
                related = {
                    item for item in (
                        value.get("objectId"), value.get("attackerId"), value.get("casterId"),
                        value.get("targetObjectId"), value.get("deadCharacterObjectId"),
                        value.get("finishingAttackerObjectId"), value.get("targetId"),
                    )
                    if isinstance(item, int)
                }
                # Preserve every CmdDamage payload so nullable-field coverage is
                # auditable across the whole replay. Other event families stay
                # player-related to keep the interactive catalog bounded.
                if packet_name not in {
                    "CmdDamage", "CmdUpdateResourceBoxCooldown"
                } and not related.intersection(player_ids):
                    continue
                events.append(encode_event(
                    packet_name,
                    record["tick"],
                    len(events) + 1,
                    wrapper_category,
                    wrapper_index,
                    value,
                ))

    if corpus_deltas is not None and next(corpus_deltas, None) is not None:
        raise ValueError('full decode has extra delta records')
    for timeline_event in object_timeline_events:
        if timeline_event.get("areaCode") in range(10, 201, 10):
            continue
        notice = object_timeline_notices_by_id.get(timeline_event["objectId"])
        if (
            notice is not None
            and notice.get("objectTimelineType")
            == timeline_event["objectTimelineType"]
            and notice.get("areaCode") in range(10, 201, 10)
        ):
            timeline_event["areaCode"] = notice["areaCode"]
            timeline_event["areaCodeStatus"] = (
                "decoded-exact-same-object-timeline-notice-area-code"
            )
            continue
        candidates = [
            row
            for row in object_timeline_notices
            if row.get("objectTimelineType")
            == timeline_event["objectTimelineType"]
            and row.get("spawnDate") == timeline_event.get("spawnDate")
            and row.get("areaCode") in range(10, 201, 10)
        ]
        if len(candidates) == 1:
            timeline_event["areaCode"] = candidates[0]["areaCode"]
            timeline_event["areaCodeStatus"] = (
                "decoded-exact-unique-timeline-type-spawn-date-notice-area-code"
            )

    if finish_game_result is None or finish_game_result_evidence is None:
        raise ValueError("CmdFinishGameResult was not found")
    if finish_game_result.get("gameId") != config.game_id:
        raise ValueError(
            f"CmdFinishGameResult gameId mismatch: "
            f"expected {config.game_id}, got {finish_game_result.get('gameId')}"
        )
    battle_user_games = finish_game_result.get("battleUserGames") or []
    mode_compatibility = require_mode_decoder_supported(
        classify_match_mode(battle_user_games, client_version=inspect['format']['clientVersion'])
    )
    result_by_user = {
        row["userNum"]: row
        for row in battle_user_games
        if isinstance(row, dict) and isinstance(row.get("userNum"), int)
    }
    expected_player_count = len(inspect["playerSummary"])
    if (
        len(battle_user_games) != expected_player_count
        or len(result_by_user) != expected_player_count
    ):
        raise ValueError(
            f"unexpected CmdFinishGameResult player count: "
            f"{len(battle_user_games)}/{len(result_by_user)} "
            f"(expected {expected_player_count})"
        )

    event_defs = {
        code: {
            "packetName": name,
            "displayName": PACKET_KOREAN_LABELS[name],
            "packetType": next(
                packet_type for packet_type, packet_name in packet_names.items()
                if packet_name == name
            ),
            "fields": fields,
            "fixedPointFields": [field for field in fields if field in FIXED_POINT_FIELDS],
            "wireStatus": "decoded-exact-wire",
        }
        for name, (code, fields) in EVENT_SPECS.items()
    }

    def resolved_player(object_id):
        if not isinstance(object_id, int):
            return None
        current = object_id
        seen = set()
        while current not in seen:
            if current in player_ids:
                return current
            seen.add(current)
            owner = object_owners.get(current)
            if owner is None:
                return None
            current = owner
        return None

    resolved_object_owners = {
        object_id: player_id
        for object_id in object_owners
        if (player_id := resolved_player(object_id)) is not None
    }

    summon_camera_objects = []
    for object_id, row in sorted(summon_camera_diagnostics_by_id.items()):
        owner_player_id = resolved_player(row.get("ownerId"))
        summon_camera_objects.append({
            **row,
            "destroyTick": destroy_ticks_by_object.get(object_id),
            "resolvedOwnerPlayerObjectId": owner_player_id,
            "ownerStatus": (
                "decoded-exact-spawn-owner-chain"
                if owner_player_id is not None
                else "unavailable-no-resolved-player-owner"
            ),
        })
    summon_camera_counts = []
    for summon_id in sorted({
        row["summonId"]
        for row in summon_camera_objects
        if isinstance(row.get("summonId"), int)
    }):
        rows = [
            row for row in summon_camera_objects
            if row.get("summonId") == summon_id
        ]
        definition = rows[0].get("gameData")
        summon_camera_counts.append({
            "summonId": summon_id,
            "objectCount": len(rows),
            "positionedObjectCount": sum(
                row.get("position") is not None for row in rows
            ),
            "resolvedOwnerCount": sum(
                row.get("resolvedOwnerPlayerObjectId") is not None
                for row in rows
            ),
            "gameData": definition,
        })
    correlated_installation_activations = []
    for row in installation_activations:
        target_id = row.get("objectId")
        actor_id = row.get("characterObjectId")
        camera = summon_camera_diagnostics_by_id.get(target_id)
        world_object = world_objects_by_id.get(target_id)
        positions = spawn_positions_by_object.get(target_id) or []
        correlated_installation_activations.append({
            **row,
            "actorPlayerObjectId": resolved_player(actor_id),
            "targetSummonId": camera.get("summonId") if camera else None,
            "targetWorldCategory": (
                world_object.get("category") if world_object else None
            ),
            "targetPosition": (
                list(world_object["position"])
                if world_object and world_object.get("position") is not None
                else [positions[0][1], positions[0][2]]
                if positions
                else None
            ),
            "correlationStatus": (
                "decoded-exact-same-object-id-world-object"
                if camera is not None or world_object is not None
                else "unavailable-target-object-not-retained-as-map-object"
            ),
        })

    kda_state = {pid: [0, 0, 0] for pid in player_ids}
    kda_timelines = {
        pid: [[first_tick, 0, 0, 0]] for pid in player_ids
    }
    for event in events:
        changed: set[int] = set()
        if event[2] == 14:
            dead_character = event_field(
                event, event_defs, "deadCharacterObjectId"
            )
            if dead_character not in player_ids:
                continue
            killer = event_field(event, event_defs, "objectId")
            if killer in player_ids:
                kda_state[killer][0] += 1
                changed.add(killer)
            for raw_assist in event_field(
                event, event_defs, "assistCharacterObjectIds"
            ) or []:
                if raw_assist not in player_ids or raw_assist == killer:
                    continue
                kda_state[raw_assist][2] += 1
                changed.add(raw_assist)
        elif event[2] == 15:
            dead = event_field(event, event_defs, "objectId")
            if dead in player_ids:
                kda_state[dead][1] += 1
                changed.add(dead)
        for pid in sorted(changed):
            kda_timelines[pid].append([event[0], *kda_state[pid]])

    monster_korean_names = {
        int(code): label for code, label in names.get("monsters", {}).items()
    }
    # The user supplied the exact client marker pair for the two linked special
    # entities. The older local name table omitted code 21 and named only 22.
    monster_korean_names.update(SPECIAL_MONSTER_DISPLAY_NAMES)
    scratch_groups_by_monster: dict[int, list[dict]] = defaultdict(list)
    for update in scratch_group_updates:
        centers = spawn_positions_by_object.get(update["groupObjectId"], [])
        if not centers:
            continue
        before = [row for row in centers if row[0] <= update["tick"]]
        center = before[-1] if before else centers[0]
        for monster_object_id in update["spawnObjectIds"]:
            scratch_groups_by_monster[monster_object_id].append({
                "tick": update["tick"],
                "center": center,
                "groupObjectId": update["groupObjectId"],
            })
    kill_noise_by_tick: dict[int, list[dict]] = defaultdict(list)
    for notification in noise_notifications:
        if notification["noiseTypeName"] == "MonsterKilled":
            kill_noise_by_tick[notification["tick"]].append(notification)
    wildlife = []
    for instance_number, monster in enumerate(monster_instances, start=1):
        monster_code = monster.get("monsterCode")
        if not isinstance(monster_code, int):
            continue
        movement_by_tick = {}
        for anchor in monster["movementTrack"]:
            movement_by_tick[anchor[0]] = anchor
        movement_track = [
            movement_by_tick[tick] for tick in sorted(movement_by_tick)
        ]
        asset_key, mutated = wildlife_asset_key(monster_code)
        killer_player_id = resolved_player(monster.get("deathKillerObjectId"))
        display_track = [list(row) for row in movement_track]
        display_position_status = "exact-observed-position"
        group_candidates = scratch_groups_by_monster.get(monster["objectId"], [])
        group_candidate = group_candidates[-1] if group_candidates else None
        if display_track and display_track[0][0] > monster["spawnTick"]:
            if group_candidate is not None:
                center = group_candidate["center"]
                display_track.insert(0, [
                    monster["spawnTick"], center[1], center[2],
                    "derived-scratch-group-center",
                ])
                display_position_status = "derived-group-center-until-first-exact-position"
            else:
                first = display_track[0]
                display_track.insert(0, [
                    monster["spawnTick"], first[1], first[2],
                    "derived-first-exact-position-backfilled",
                ])
                display_position_status = "derived-first-exact-position-backfilled-to-life-start"
        elif not display_track and group_candidate is not None:
            center = group_candidate["center"]
            display_track.append([
                monster["spawnTick"], center[1], center[2],
                "derived-scratch-group-center",
            ])
            display_position_status = "derived-scratch-group-center-only"
        elif not display_track:
            matching_noise = []
            if isinstance(monster.get("deathTick"), int):
                matching_noise = [
                    row for row in kill_noise_by_tick.get(monster["deathTick"], [])
                    if resolved_player(row.get("creatorObjectId")) == killer_player_id
                ]
            if len(matching_noise) == 1:
                death_position = matching_noise[0]["sourcePosition"]
                display_track.append([
                    monster["spawnTick"], death_position[0], death_position[1],
                    "derived-unique-kill-noise-position-backfilled",
                ])
                display_position_status = "derived-unique-kill-noise-position-backfilled"
            else:
                display_position_status = "unavailable-no-position-evidence"
        display_group_object_id = (
            group_candidate["groupObjectId"] if group_candidate is not None else None
        )
        display_group_center = None
        if group_candidate is not None:
            center = group_candidate["center"]
            display_group_center = [center[1], center[2]]
        if asset_key in {"raven", "mutant-raven"}:
            map_position_track = [list(row) for row in display_track]
            map_movement_mode = "crow-held-observed-track"
        elif monster_code in SPECIAL_MOBILE_MONSTER_CODES:
            map_position_track = [list(row) for row in display_track]
            map_movement_mode = "special-mobile-observed-track"
        elif display_group_center is not None:
            map_position_track = [[
                monster["spawnTick"],
                display_group_center[0],
                display_group_center[1],
                "fixed-exact-scratch-group-center",
            ]]
            map_movement_mode = "fixed-scratch-group-center"
        elif display_track:
            map_position_track = [[
                monster["spawnTick"],
                display_track[0][1],
                display_track[0][2],
                "fixed-first-supported-display-position",
            ]]
            map_movement_mode = "fixed-first-supported-position"
        else:
            map_position_track = []
            map_movement_mode = "unavailable-no-position-evidence"
        wildlife.append({
            "instanceNumber": instance_number,
            "objectId": monster["objectId"],
            "monsterCode": monster_code,
            "monsterName": monster_korean_names.get(
                monster_code, f"Monster {monster_code}"
            ),
            "mutated": mutated,
            "assetKey": asset_key,
            "assetStatus": (
                "user-provided-icon-available"
                if asset_key is not None
                else "icon-unavailable-for-monster-code"
            ),
            "spawnTick": monster["spawnTick"],
            "initialAlive": monster["initialAlive"],
            "initialHp": monster["initialHp"],
            "deathTick": monster["deathTick"],
            "destroyTick": monster["destroyTick"],
            "despawnTick": monster["despawnTick"],
            "killerPlayerObjectId": killer_player_id,
            "movementTrack": movement_track,
            "movementAnchorCount": len(movement_track),
            "movementStatus": "exact-anchor-held-until-next-observation",
            "displayPositionTrack": display_track,
            "displayPositionAnchorCount": len(display_track),
            "displayPositionStatus": display_position_status,
            "displayGroupObjectId": display_group_object_id,
            "displayGroupCenter": display_group_center,
            "displayGroupStatus": (
                "decoded-exact-scratch-group-membership"
                if display_group_object_id is not None
                else "single-instance-no-scratch-group"
            ),
            "mapPositionTrack": map_position_track,
            "mapPositionAnchorCount": len(map_position_track),
            "mapMovementMode": map_movement_mode,
            "wireStatus": monster["wireStatus"],
        })

    for world_object in world_objects_by_id.values():
        if world_object.get("category") not in {"lumi", "recon-orb"}:
            continue
        anchors_by_tick: dict[int, list] = {}
        for anchor in (
            spawn_positions_by_object.get(world_object["objectId"], [])
            + movement_by_world_object.get(world_object["objectId"], [])
        ):
            anchors_by_tick[anchor[0]] = list(anchor)
        world_object["movementTrack"] = [
            anchors_by_tick[tick] for tick in sorted(anchors_by_tick)
        ]
        world_object["movementAnchorCount"] = len(world_object["movementTrack"])
        world_object["movementStatus"] = (
            "decoded-exact-wrapper-and-movement-command-anchors"
            if world_object["movementTrack"]
            else "unavailable-no-exact-guide-robot-position"
        )

    boss_name_categories = {
        "Wickline": "wickeline",
        "Alpha": "alpha",
        "Omega": "omega",
    }
    boss_code_categories = {
        code: boss_name_categories[row["monster"]]
        for code, row in world_game_data["monsters"].items()
        if row.get("monster") in boss_name_categories
    }
    map_meta = json.loads(config.map_meta_path.read_text(encoding="utf-8"))
    area_centers = load_lumia_area_centers(
        config.restriction_area_shapes_path,
        map_meta,
    )
    world_map = build_world_map_timeline(
        world_objects_by_id=world_objects_by_id,
        spawn_positions_by_object=spawn_positions_by_object,
        destroy_ticks_by_object=destroy_ticks_by_object,
        object_timeline_events=object_timeline_events,
        object_timeline_notice_observations=object_timeline_notice_observations,
        unknown_object_timers=unknown_object_timers,
        wildlife=wildlife,
        boss_code_categories=boss_code_categories,
        transport_mode_transitions=transport_mode_transitions,
        area_centers=area_centers,
        first_tick=first_tick,
        end_tick=end_tick,
        resource_box_updates=resource_box_updates,
        resource_item_box_events=resource_item_box_events,
        resource_world_state_snapshots=resource_world_state_snapshots,
    )

    derived_noise_notifications = []
    for notification in noise_notifications:
        creator_player_id = resolved_player(notification.get("creatorObjectId"))
        derived_noise_notifications.append({
            key: value
            for key, value in {
                **notification,
                "creatorPlayerObjectId": creator_player_id,
            }.items()
            if key != "creatorObjectId"
        })

    player_event_indexes = {pid: [] for pid in player_ids}
    for index, event in enumerate(events):
        code = event[2]
        fields = event_defs[code]["fields"]
        values = {name: event[5 + offset] for offset, name in enumerate(fields)}
        related = {
            item for name, item in values.items()
            if name in {
                "objectId", "attackerId", "casterId", "targetObjectId",
                "deadCharacterObjectId", "finishingAttackerObjectId", "targetId",
            } and isinstance(item, int)
        }
        related.update(
            player_id
            for item in list(related)
            if (player_id := resolved_player(item)) is not None
        )
        for pid in related.intersection(player_ids):
            player_event_indexes[pid].append(index)

    skill_id_names = enum_values["SkillId"]
    observed_skill_codes = {
        event_field(event, event_defs, "skillCode")
        for event in events
        if event[2] in {1, 19, 20, 21}
    }
    skill_code_map = {}
    for code in sorted(item for item in observed_skill_codes if isinstance(item, int)):
        skill = skill_rows.get(code)
        group = skill_groups.get(skill["group"]) if skill else None
        skill_code_map[code] = {
            "group": skill.get("group") if skill else None,
            "skillId": group.get("skillId") if group else None,
            "skillType": group.get("skillType") if group else None,
            "tableCooldownSeconds": skill.get("cooldown") if skill else None,
            "gameDataStatus": "exact-replay-version-game-data" if skill and group else "unresolved-code",
        }

    snapshots_by_player = defaultdict(list)
    position_snapshots_by_player = defaultdict(list)
    observer_status_by_player = defaultdict(list)
    for snapshot in inspect["snapshotTimeline"]:
        for player in snapshot["players"]:
            status = player.get("status") or {}
            initial_stats = player.get("initialStats")
            if not isinstance(initial_stats, list):
                raise ValueError("snapshot player initialStats are missing")
            exact_stats = {
                row[0]: row[1]
                for row in initial_stats
                if isinstance(row, list)
                and len(row) == 2
                and isinstance(row[0], int)
                and isinstance(row[1], int)
            }
            if len(exact_stats) != len(initial_stats):
                raise ValueError("snapshot player initialStats contain duplicates")
            max_hp_base_raw = exact_stats.get(max_hp_stat_code)
            # CharacterStatValue is sparse: absent modifier rows mean that the
            # snapshot carries no modifier for that StatType.
            max_hp_bonus_raw = exact_stats.get(max_hp_bonus_stat_code, 0)
            max_hp_ratio_raw = exact_stats.get(max_hp_ratio_stat_code, 0)
            if (
                not isinstance(max_hp_base_raw, int)
                or not isinstance(max_hp_bonus_raw, int)
                or not isinstance(max_hp_ratio_raw, int)
                or max_hp_base_raw <= 0
                or max_hp_bonus_raw < 0
            ):
                raise ValueError("snapshot player MaxHp stats are missing or invalid")
            if max_hp_ratio_raw != 0:
                raise ValueError(
                    "snapshot player MaxHpRatio is nonzero; exact max-HP formula is unavailable"
                )
            max_hp_raw = max_hp_base_raw + max_hp_bonus_raw
            if max_hp_raw % 100:
                raise ValueError("snapshot player MaxHp is not exact hundredth fixed-point")
            max_hp = max_hp_raw // 100
            if not isinstance(status.get("hp"), int) or not 0 <= status["hp"] <= max_hp:
                raise ValueError("snapshot player HP is outside exact MaxHp")
            snapshots_by_player[player["objectId"]].append([
                snapshot["tick"], status.get("hp"), status.get("blockAllShield", 0),
                status.get("blockNormalShield", 0), status.get("blockSkillShield", 0),
                status.get("level"), 1 if player.get("isAlive") else 0, max_hp,
            ])
            if (
                isinstance(status.get("vfCredit"), (int, float))
                and isinstance(status.get("gadgetEnergy"), int)
            ):
                observer_status_by_player[player["objectId"]].append([
                    snapshot["tick"],
                    status["vfCredit"],
                    status["gadgetEnergy"],
                ])
            position = player.get("position") or {}
            if isinstance(position.get("worldX"), (int, float)) and isinstance(
                position.get("worldZ"), (int, float)
            ):
                position_snapshots_by_player[player["objectId"]].append([
                    snapshot["tick"],
                    position["worldX"],
                    position["worldZ"],
                    "full-snapshot",
                ])

    players = []
    observed_item_codes: set[int] = set()
    all_events_by_player = {
        pid: [events[index] for index in indexes]
        for pid, indexes in player_event_indexes.items()
    }
    team_by_player = {
        row["objectId"]: row["teamNumber"] for row in inspect["playerSummary"]
    }
    projectile_skill_starts = []
    raw_projectile_skill_actions = []
    unresolved_character_skill_start_count = 0
    for event in events:
        if event[2] in {
            EVENT_SPECS["CmdPlaySkillAction"][0],
            EVENT_SPECS["CmdPlaySkillActionWithTargets"][0],
            EVENT_SPECS["CmdPlayStateSkillAction"][0],
        }:
            packet_name = event_defs[event[2]]["packetName"]
            raw_targets = event_field(event, event_defs, "targets")
            targets = []
            if isinstance(raw_targets, list):
                for target in raw_targets:
                    if not isinstance(target, dict):
                        raise ValueError(
                            f"{packet_name} has an invalid exact target row"
                        )
                    target_id = target.get("targetId")
                    if not isinstance(target_id, int):
                        raise ValueError(
                            f"{packet_name} target has no exact targetId"
                        )
                    targets.append({
                        "targetObjectId": target_id,
                        "hasTargetPosition": target.get("targetPos") is not None,
                    })
            raw_projectile_skill_actions.append({
                "tick": event[0],
                "sourceObjectId": event_field(event, event_defs, "objectId"),
                "skillIdCode": event_field(event, event_defs, "skillId"),
                "casterObjectId": event_field(event, event_defs, "casterId"),
                "stateCasterObjectId": event_field(
                    event, event_defs, "stateCasterId"
                ),
                "stateGroup": event_field(event, event_defs, "stateGroup"),
                "targets": targets,
                "wireStatus": f"decoded-exact-{packet_name}",
            })
        if event[2] != EVENT_SPECS["CmdStartSkill"][0]:
            continue
        player_object_id = event_field(event, event_defs, "objectId")
        skill_id_code = event_field(event, event_defs, "skillId")
        skill_code = event_field(event, event_defs, "skillCode")
        skill_row = skill_rows.get(skill_code)
        skill_group = skill_row.get("group") if isinstance(skill_row, dict) else None
        skill_definition = projectile_skill_catalog["skillGroups"].get(
            str(skill_group)
        )
        if (
            player_object_id not in team_by_player
            or not isinstance(skill_group, int)
            or not isinstance(skill_definition, dict)
            or skill_definition.get("characterCode")
            != next(
                row["characterCode"]
                for row in inspect["playerSummary"]
                if row["objectId"] == player_object_id
            )
        ):
            unresolved_character_skill_start_count += 1
            continue
        projectile_skill_starts.append({
            "tick": event[0],
            "playerObjectId": player_object_id,
            "skillGroup": skill_group,
            "skillCode": skill_code,
            "skillIdCode": skill_id_code,
            "wireStatus": "decoded-exact-CmdStartSkill",
        })
    projectile_skill_action_resolution = resolve_skill_action_actors(
        raw_projectile_skill_actions,
        projectile_skill_starts,
        set(team_by_player),
    )
    projectile_skill_actions = projectile_skill_action_resolution["actions"]
    runtime_projectile_spawns = [
        {
            **row,
            "ownerPlayerObjectId": resolved_player(row.get("ownerObjectId")),
        }
        for row in projectile_spawns
        if resolved_player(row.get("ownerObjectId")) is not None
    ]
    direct_projectile_owner = {
        row["projectileObjectId"]: row["ownerObjectId"]
        for row in projectile_spawns
        if row.get("ownerObjectId") in team_by_player
    }
    exact_direct_damage_targets = {
        (
            event[0],
            event_field(event, event_defs, "attackerId"),
            event_field(event, event_defs, "objectId"),
        )
        for event in events
        if event[2] == EVENT_SPECS["CmdDamage"][0]
        and event_field(event, event_defs, "attackerId") in team_by_player
        and event_field(event, event_defs, "objectId") in team_by_player
    }
    exact_owned_projectile_collision_targets = {
        (
            row["tick"],
            direct_projectile_owner.get(row["projectileObjectId"]),
            row["targetObjectId"],
        )
        for row in projectile_collisions
        if direct_projectile_owner.get(row.get("projectileObjectId"))
        in team_by_player
        and row.get("targetObjectId") in team_by_player
    }
    for action in projectile_skill_actions:
        actor = action["playerObjectId"]
        for target in action.get("targets") or []:
            key = (action["tick"], actor, target["targetObjectId"])
            target["sameTickDirectPlayerDamage"] = (
                key in exact_direct_damage_targets
            )
            target["sameTickOwnedProjectileCollision"] = (
                key in exact_owned_projectile_collision_targets
            )
    projectile_skill_starts = attach_skill_action_anchors(
        projectile_skill_starts, projectile_skill_actions
    )
    exact_skill_damage_resolution = resolve_exact_skill_damage_events(
        [
            {
                "tick": event[0],
                "attackerObjectId": event_field(
                    event, event_defs, "attackerId"
                ),
                "targetObjectId": event_field(event, event_defs, "objectId"),
                "effectCode": event_field(event, event_defs, "effectCode"),
            }
            for event in events
            if event[2] == EVENT_SPECS["CmdDamage"][0]
        ],
        list(skill_rows.values()),
        character_state_rows,
        projectile_skill_catalog,
        {
            row["objectId"]: {"characterCode": row["characterCode"]}
            for row in inspect["playerSummary"]
        },
        team_by_player,
        direct_projectile_owner,
    )
    state_type_names = enum_values.get("StateType")
    if not isinstance(state_type_names, dict) or not state_type_names:
        raise ValueError("exact StateType enum catalog is missing")
    for summary in sorted(inspect["playerSummary"], key=lambda row: (row["teamNumber"], row["objectId"])):
        pid = summary["objectId"]
        player_events = all_events_by_player[pid]
        combat_intervals = build_combat_intervals(events, pid, event_defs, end_tick)
        total_combat_ticks = sum(right - left for left, right, _ in combat_intervals)

        dealt_packets = [
            event for event in player_events
            if event[2] == 9
            and resolved_player(event_field(event, event_defs, "attackerId")) == pid
            and event_field(event, event_defs, "objectId") in player_ids - {pid}
        ]
        taken_packets = [
            event for event in player_events
            if event[2] == 9
            and event_field(event, event_defs, "objectId") == pid
            and resolved_player(event_field(event, event_defs, "attackerId")) in player_ids - {pid}
        ]
        damage_dealt = sum(
            event_field(event, event_defs, "damage") or 0
            for event in dealt_packets
        )
        damage_taken = sum(
            event_field(event, event_defs, "damage") or 0
            for event in taken_packets
        )
        healing_received = sum(
            max(0, event_field(event, event_defs, "addHp") or 0)
            for event in player_events
            if event[2] in {10, 11} and event_field(event, event_defs, "objectId") == pid
        )
        allied_healing_given = sum(
            max(0, event_field(event, event_defs, "addHp") or 0)
            for event in player_events
            if event[2] in {10, 11}
            and resolved_player(event_field(event, event_defs, "casterId")) == pid
            and event_field(event, event_defs, "objectId") in player_ids - {pid}
            and team_by_player.get(event_field(event, event_defs, "objectId"))
            == summary["teamNumber"]
        )
        crowd_control_received = Counter()
        for event in player_events:
            if event[2] != 18 or event_field(event, event_defs, "objectId") != pid:
                continue
            state_type = event_field(event, event_defs, "stateType")
            if state_type not in state_type_names:
                raise ValueError(
                    f"CmdCrowdControl has unresolved StateType value {state_type}"
                )
            crowd_control_received[state_type_names[state_type]] += 1
        shield_timeline = []
        previous_shield = 0
        positive_shield_updates = 0
        for event in player_events:
            if event[2] != 26 or event_field(event, event_defs, "objectId") != pid:
                continue
            amounts = [
                event_field(event, event_defs, field)
                for field in (
                    "blockAllShieldAmount",
                    "blockNormalShieldAmount",
                    "blockSkillShieldAmount",
                )
            ]
            if any(not isinstance(amount, int) or amount < 0 for amount in amounts):
                raise ValueError("CmdUpdateShield contains an invalid shield amount")
            total_shield = sum(amounts)
            if total_shield > previous_shield:
                positive_shield_updates += 1
            previous_shield = total_shield
            shield_timeline.append([event[0], *amounts, total_shield])
        deaths = sum(
            event[2] == 15 and event_field(event, event_defs, "objectId") == pid
            for event in player_events
        )
        kills = sum(
            event[2] == 15
            and event_field(event, event_defs, "finishingAttackerObjectId") == pid
            and event_field(event, event_defs, "objectId") in player_ids - {pid}
            for event in player_events
        )
        active_starts = [
            event for event in player_events
            if event[2] == 1 and event_field(event, event_defs, "objectId") == pid
        ]
        normal_starts = [
            event for event in player_events
            if event[2] == 19 and event_field(event, event_defs, "objectId") == pid
        ]
        passive_starts = [
            event for event in player_events
            if event[2] == 20 and event_field(event, event_defs, "objectId") == pid
        ]
        state_starts = [
            event for event in player_events
            if event[2] == 21 and event_field(event, event_defs, "objectId") == pid
        ]
        equipment_timeline = []
        inventory_timeline = []
        for event in player_events:
            packet_name = event_defs[event[2]]["packetName"]
            if packet_name == "CmdUpdateEquipment" and event_field(
                event, event_defs, "objectId"
            ) == pid:
                updates = compact_item_updates(
                    event_field(event, event_defs, "updates"),
                    slot_field="slotType",
                )
                equipment_timeline.append([event[0], updates])
            elif packet_name == "CmdUpdateInventoryForObserver" and event_field(
                event, event_defs, "objectId"
            ) == pid:
                updates = compact_item_updates(
                    event_field(event, event_defs, "updates"),
                    slot_field="slot",
                )
                update_type = event_field(event, event_defs, "updateType")
                if not isinstance(update_type, int):
                    raise ValueError("inventory updateType must be an integer")
                inventory_timeline.append([event[0], updates, update_type])
        for timeline in (equipment_timeline, inventory_timeline):
            for row in timeline:
                observed_item_codes.update(
                    item_code
                    for _, item_code, _ in row[1]
                    if isinstance(item_code, int)
                )

        exact_skills = defaultdict(lambda: {
            "count": 0, "inCombatCount": 0, "codes": Counter(), "firstTick": None, "lastTick": None,
        })
        for event in active_starts:
            skill_id = event_field(event, event_defs, "skillId")
            skill_code = event_field(event, event_defs, "skillCode")
            row = exact_skills[skill_id]
            row["count"] += 1
            row["codes"][skill_code] += 1
            row["firstTick"] = event[0] if row["firstTick"] is None else min(row["firstTick"], event[0])
            row["lastTick"] = event[0] if row["lastTick"] is None else max(row["lastTick"], event[0])
            if any(left <= event[0] < right for left, right, _ in combat_intervals):
                row["inCombatCount"] += 1
        finish_counts = Counter(
            event_field(event, event_defs, "skillId")
            for event in player_events
            if event[2] == 2 and event_field(event, event_defs, "objectId") == pid
        )
        action_counts = Counter(
            event_field(event, event_defs, "skillId")
            for event in player_events
            if event[2] == 3 and event_field(event, event_defs, "objectId") == pid
        )
        skill_table = []
        for skill_id, row in exact_skills.items():
            name = skill_id_names.get(skill_id, f"SkillId {skill_id}")
            primary_skill_code = (
                row["codes"].most_common(1)[0][0] if row["codes"] else None
            )
            skill_table.append({
                "skillId": skill_id,
                "skillName": name,
                "family": skill_family(name, primary_skill_code),
                "startCount": row["count"],
                "inCombatStartCount": row["inCombatCount"],
                "finishCount": finish_counts[skill_id],
                "actionCount": action_counts[skill_id],
                "codes": [{"code": code, "count": count} for code, count in row["codes"].most_common()],
                "firstTick": row["firstTick"],
                "lastTick": row["lastTick"],
                "status": "decoded-exact-event-count",
            })
        skill_table.sort(key=lambda row: (-row["startCount"], row["skillId"]))
        skill_start_timeline = [
            [
                event[0],
                skill_family(
                    skill_id_names.get(event_field(event, event_defs, "skillId")),
                    event_field(event, event_defs, "skillCode"),
                ),
                skill_id_names.get(
                    event_field(event, event_defs, "skillId"),
                    f"SkillId {event_field(event, event_defs, 'skillId')}",
                ),
                event_field(event, event_defs, "skillCode"),
            ]
            for event in active_starts
        ]

        cooldown_table = cooldown_stats(
            player_events, pid, combat_intervals, enum_values, event_defs, end_tick
        )
        cooldown_timeline = skill_cooldown_timeline(
            player_events, pid, enum_values, event_defs
        )
        family_usage = Counter(skill_family(
            skill_id_names.get(event_field(event, event_defs, "skillId")),
            event_field(event, event_defs, "skillCode"),
        ) for event in active_starts)
        family_combat_usage = Counter(skill_family(
            skill_id_names.get(event_field(event, event_defs, "skillId")),
            event_field(event, event_defs, "skillCode"),
        ) for event in active_starts if any(
            left <= event[0] < right for left, right, _ in combat_intervals
        ))
        for row in cooldown_table:
            row["startCount"] = family_usage[row["family"]]
            row["inCombatStartCount"] = family_combat_usage[row["family"]]

        sessions = []
        session_event_slice = exact_tick_slices(player_events)
        for number, (left, right, level) in enumerate(combat_intervals, 1):
            session_events = session_event_slice(left, right)
            sessions.append({
                "number": number,
                "startTick": left,
                "endTick": right,
                "durationSeconds": round((right - left) / 60, 3),
                "maxCombatLevel": level,
                "damageDealt": sum(
                    event_field(event, event_defs, "damage") or 0
                    for event in session_events
                    if event[2] == 9
                    and resolved_player(event_field(event, event_defs, "attackerId")) == pid
                    and event_field(event, event_defs, "objectId") in player_ids - {pid}
                ),
                "damageTaken": sum(
                    event_field(event, event_defs, "damage") or 0
                    for event in session_events
                    if event[2] == 9
                    and event_field(event, event_defs, "objectId") == pid
                    and resolved_player(event_field(event, event_defs, "attackerId")) in player_ids - {pid}
                ),
                "activeSkillStarts": sum(
                    event[2] == 1 and event_field(event, event_defs, "objectId") == pid
                    for event in session_events
                ),
                "kills": sum(
                    event[2] == 15 and event_field(event, event_defs, "finishingAttackerObjectId") == pid
                    for event in session_events
                ),
                "deaths": sum(
                    event[2] == 15 and event_field(event, event_defs, "objectId") == pid
                    for event in session_events
                ),
            })

        bin_ticks = 300
        bin_count = (end_tick - first_tick) // bin_ticks + 1
        bins = [[first_tick + i * bin_ticks, 0, 0, 0, 0] for i in range(bin_count)]
        for event in player_events:
            index = min(bin_count - 1, max(0, (event[0] - first_tick) // bin_ticks))
            if event[2] == 9:
                amount = event_field(event, event_defs, "damage") or 0
                if resolved_player(event_field(event, event_defs, "attackerId")) == pid and event_field(event, event_defs, "objectId") in player_ids - {pid}:
                    bins[index][1] += amount
                if event_field(event, event_defs, "objectId") == pid and resolved_player(event_field(event, event_defs, "attackerId")) in player_ids - {pid}:
                    bins[index][2] += amount
            elif event[2] in {10, 11} and event_field(event, event_defs, "objectId") == pid:
                bins[index][3] += max(0, event_field(event, event_defs, "addHp") or 0)
            elif event[2] == 1 and event_field(event, event_defs, "objectId") == pid:
                bins[index][4] += 1

        character = characters.get(summary["characterCode"], {})
        game_result = result_by_user.get(summary["userId"])
        if game_result is None:
            raise ValueError(
                f"missing CmdFinishGameResult row for user {summary['userId']}"
            )
        if (
            game_result.get("characterNum") != summary["characterCode"]
            or game_result.get("teamNumber") != summary["teamNumber"]
        ):
            raise ValueError(
                f"CmdFinishGameResult identity mismatch for user {summary['userId']}"
            )
        damage_to_parts = sum(
            game_result[key]
            for key in (
                "damageToPlayer_trap",
                "damageToPlayer_basic",
                "damageToPlayer_skill",
                "damageToPlayer_itemSkill",
                "damageToPlayer_direct",
            )
        )
        damage_from_parts = sum(
            game_result[key]
            for key in (
                "damageFromPlayer_trap",
                "damageFromPlayer_basic",
                "damageFromPlayer_skill",
                "damageFromPlayer_itemSkill",
                "damageFromPlayer_direct",
            )
        )
        if damage_to_parts != game_result["damageToPlayer"]:
            raise ValueError(
                f"outgoing damage components mismatch for user {summary['userId']}"
            )
        if damage_from_parts != game_result["damageFromPlayer"]:
            raise ValueError(
                f"incoming damage components mismatch for user {summary['userId']}"
            )
        sanitized_game_result = {
            key: value
            for key, value in game_result.items()
            if key not in REDACTED_RESULT_STRING_FIELDS
        }
        final_kda = kda_timelines[pid][-1][1:]
        expected_kda = [
            game_result["playerKill"],
            game_result["playerDeaths"],
            game_result["playerAssistant"],
        ]
        from decoder.kda_source_discrepancy import crosscheck, EXACT, DISCREPANCY
        kda_crosscheck = crosscheck(kda_timelines[pid], expected_kda, pid, player_ids,
                                   events, event_defs, finish_game_result_evidence)
        movement_by_tick = {}
        for point in (
            position_snapshots_by_player[pid] + movement_by_player[pid]
        ):
            tick, world_x, world_z, source = point
            if not (
                isinstance(world_x, (int, float))
                and isinstance(world_z, (int, float))
                and -500 <= world_x <= 500
                and -500 <= world_z <= 500
            ):
                continue
            movement_by_tick[tick] = [
                tick,
                round(world_x, 4),
                round(world_z, 4),
                source,
            ]
        movement_track = [
            movement_by_tick[tick]
            for tick in sorted(movement_by_tick)
        ]
        if not movement_track:
            raise ValueError(f"no movement anchors for player {pid}")
        life_timeline = [[first_tick, "alive", "first-full-snapshot"]]
        for event in player_events:
            if event_field(event, event_defs, "objectId") != pid:
                continue
            if event[2] == 16:
                life_timeline.append([event[0], "down", "CmdDyingCondition"])
            elif event[2] == 15:
                life_timeline.append([event[0], "dead", "CmdDead"])
            elif event[2] == 17:
                life_timeline.append([event[0], "alive", "CmdResurrection"])
        exact_death_locations = []
        for tick, life, source in life_timeline:
            if life != "dead" or tick not in movement_by_tick:
                continue
            anchor = movement_by_tick[tick]
            exact_death_locations.append({
                "tick": tick,
                "position": [anchor[1], anchor[2]],
                "positionStatus": (
                    "decoded-exact-player-position-same-tick-as-CmdDead"
                ),
                "source": source,
            })
        character_capabilities = character_capability_catalog["characters"].get(
            str(summary["characterCode"])
        )
        if character_capabilities is None:
            raise ValueError(
                "replay character is missing from exact capability catalog: "
                f"{summary['characterCode']}"
            )
        players.append({
            "objectId": pid,
            "userId": summary["userId"],
            "teamNumber": summary["teamNumber"],
            "characterCode": summary["characterCode"],
            "characterName": character_korean_names.get(
                summary["characterCode"],
                character.get("name", f"Character {summary['characterCode']}"),
            ),
            "characterNameInternal": character.get(
                "name", f"Character {summary['characterCode']}"
            ),
            "characterDataStatus": (
                "repository-korean-name-map-plus-exact-replay-version-game-data"
                if character and summary["characterCode"] in character_korean_names
                else "unresolved-code"
            ),
            "characterCapabilities": character_capabilities,
            "observedCapabilityEvidence": {
                "crowdControlReceived": [
                    {"stateType": state_type, "count": count}
                    for state_type, count in sorted(crowd_control_received.items())
                ],
                "crowdControlSourceStatus": (
                    "unavailable-CmdCrowdControl-has-no-source-field"
                ),
                "shieldTimeline": shield_timeline,
                "maxObservedShield": max(
                    (row[-1] for row in shield_timeline), default=0
                ),
                "positiveShieldUpdateCount": positive_shield_updates,
                "shieldSourceStatus": (
                    "unavailable-CmdUpdateShield-has-no-caster-field"
                ),
                "healingReceived": healing_received,
                "alliedHealingGiven": allied_healing_given,
                "healingStatus": "decoded-exact-positive-CmdHeal-values",
                "status": "decoded-exact-runtime-capability-evidence",
                "fallbackUsed": False,
            },
            "maxLevel": summary["maxLevel"],
            "lastStatus": summary["lastStatus"],
            "gameResult": sanitized_game_result,
            "gameResultStatus": "decoded-exact-wire",
            "gameResultComponentSumsExact": True,
            "movementTrack": movement_track,
            "movementTrackStatus": "exact-anchor-held-until-next-observation",
            "movementAnchorCount": len(movement_track),
            "plannedPathCommands": planned_paths_by_player[pid],
            "plannedPathCommandCount": len(planned_paths_by_player[pid]),
            "plannedPathNodeCount": sum(
                len(path[2]) for path in planned_paths_by_player[pid]
            ),
            "plannedPathStatus": "exact-intended-nav-corners-without-node-timing",
            "lifeTimeline": life_timeline,
            "exactDeathLocations": exact_death_locations,
            "kdaTimeline": kda_timelines[pid],
            "kdaTimelineStatus": DISCREPANCY if kda_crosscheck else EXACT,
            **({"kdaCrosscheck": kda_crosscheck} if kda_crosscheck else {}),
            "observerStatusTimeline": observer_status_by_player[pid],
            "observerStatusTimelineStatus": (
                "decoded-exact-full-snapshot-vf-credit-and-gadget-energy-"
                "held-until-next-snapshot"
            ),
            "survivableTimeTimeline": survivable_time_updates_by_player[pid],
            "survivableTimeTimelineStatus": (
                "decoded-exact-team-survivable-time-update-"
                "held-until-next-update"
            ),
            "maxHpStatUpdates": max_hp_stat_updates_by_player[pid],
            "maxHpStatUpdatesStatus": (
                "private-diagnostic-exact-CharacterStatValue-MaxHp-family-updates-"
                "not-yet-interpreted"
            ),
            "stats": {
                "combatSeconds": round(total_combat_ticks / 60, 3),
                "combatSessions": len(combat_intervals),
                "damageToPlayers": damage_dealt,
                "damageFromPlayers": damage_taken,
                "damageToPlayerPackets": len(dealt_packets),
                "damageToPlayerKnownPackets": sum(
                    event_field(event, event_defs, "damage") is not None
                    for event in dealt_packets
                ),
                "damageFromPlayerPackets": len(taken_packets),
                "damageFromPlayerKnownPackets": sum(
                    event_field(event, event_defs, "damage") is not None
                    for event in taken_packets
                ),
                "recordedHpAdded": healing_received,
                "killsFromDeadPacket": kills,
                "deathsFromDeadPacket": deaths,
                "activeSkillStarts": len(active_starts),
                "normalAttackStarts": len(normal_starts),
                "passiveSkillStarts": len(passive_starts),
                "stateSkillStarts": len(state_starts),
            },
            "combatIntervals": combat_intervals,
            "sessions": sessions,
            "cooldowns": cooldown_table,
            "skillCooldownTimeline": cooldown_timeline,
            "skillCooldownTimelineStatus": (
                "decoded-exact-cooldown-packets-with-derived-clock"
            ),
            "skills": skill_table,
            "skillStartTimeline": skill_start_timeline,
            "projectileHitRates": [],
            "equipmentTimeline": equipment_timeline,
            "equipmentTimelineStatus": "decoded-exact-observer-equipment-updates",
            "inventoryTimeline": inventory_timeline,
            "inventoryTimelineStatus": "decoded-exact-observer-inventory-updates",
            "timelineBins": bins,
            "snapshotSeries": snapshots_by_player[pid],
        })

    # Same-player and near-tick pairs make the fixed-point scale auditable.
    starts_by_player = defaultdict(list)
    for event in events:
        if event[2] == 1:
            starts_by_player[event_field(event, event_defs, "objectId")].append(event)
    pair_count = 0
    base_table_matches = 0
    for event in events:
        if event[2] != 4:
            continue
        pid = event_field(event, event_defs, "objectId")
        candidates = [
            start for start in starts_by_player[pid]
            if 0 <= event[0] - start[0] <= 5
        ]
        if not candidates:
            continue
        pair_count += 1
        start = max(candidates, key=lambda item: item[0])
        skill_code = event_field(start, event_defs, "skillCode")
        skill = skill_rows.get(skill_code)
        raw = event_field(event, event_defs, "cooldown")
        if skill and raw is not None and abs(skill["cooldown"] - raw / 100) <= 0.011:
            base_table_matches += 1

    cobalt_map_unavailable = mode_compatibility["key"] == "cobalt"
    map_image_data_url = ""
    if not cobalt_map_unavailable:
        map_image_data_url = (
            "data:image/png;base64,"
            + base64.b64encode(config.map_image_path.read_bytes()).decode("ascii")
        )
    damage_timeline_coverage = attach_damage_timeline_coverage(
        events, event_defs, players
    )
    skill_damage_boundary = attach_skill_damage_boundary(players)
    combat_judgment_summary = attach_combat_judgment(
        players, events, event_defs, resolved_player
    )
    confirmed_pvp_intervals = {
        player["objectId"]: [[episode["startTick"], episode["endTick"]]
                             for episode in player["combatJudgment"]["personalEpisodes"]]
        for player in players
    }
    projectile_hit_rate_runtime = calculate_projectile_hit_rates(
        projectile_skill_catalog,
        projectile_skill_starts,
        runtime_projectile_spawns,
        projectile_collisions,
        team_by_player,
        confirmed_pvp_intervals,
        exact_skill_damage_events=exact_skill_damage_resolution["events"],
    )
    for player in players:
        player["projectileHitRates"] = projectile_hit_rate_runtime["players"].get(
            str(player["objectId"]), []
        )
    growth_tempo_summary = attach_growth_tempo(players, item_rows)
    objective_preparation_summary = attach_objective_preparation(
        players, world_map
    )
    skill_operation_summary = attach_skill_operation(
        players, events, event_defs, resolved_player
    )
    if config.requested_skill_cache_path is not None:
        from attach_requested_skill_operation import attach_requested_skill_operation
        skill_operation_summary['requestedMetrics']=attach_requested_skill_operation(
            players,replay_sha256=replay_sha256,cache_path=config.requested_skill_cache_path,
            identity_path=config.requested_skill_identity_path,game_data_path=config.game_data_path)
    death_review_summary = attach_death_review(
        players,
        events,
        event_defs,
        resolved_player,
        state_type_names,
    )
    scene_coaching_summary = attach_scene_coaching(
        players,
        wildlife,
        first_tick=first_tick,
        phase_clock={
            "restrictionUpdates": restriction_updates,
        },
    )

    missing_item_codes = sorted(observed_item_codes - item_rows.keys())
    if missing_item_codes:
        raise ValueError(
            f"replay item codes are missing from exact game data: {missing_item_codes}"
        )
    item_catalog = {
        str(code): item_rows[code]
        for code in sorted(observed_item_codes)
    }

    return {
        "format": "er-replay-combat-analysis.v1",
        "meta": {
            "gameId": config.game_id,
            "clientVersion": client_version,
            "matchingMode": mode_compatibility["matchingMode"],
            "matchingTeamMode": mode_compatibility["matchingTeamMode"],
            "matchMode": mode_compatibility["key"],
            "matchModeLabel": mode_compatibility["label"],
            "compatibility": {
                "clientLayout": client_compatibility,
                "matchMode": mode_compatibility,
                "fallbackUsed": False,
            },
            "firstTick": first_tick,
            "lastTick": end_tick,
            "targetFrameRate": 60,
            "selectedEventCount": len(events),
            "playerCount": len(players),
            "wildlifeInstanceCount": len(wildlife),
            "supportedNoiseNotificationCount": len(derived_noise_notifications),
            "tacticalPingCount": len(tactical_pings),
            "projectileSpawnCount": len(runtime_projectile_spawns),
            "projectileCollisionCount": len(projectile_collisions),
            "projectileHitRateCalculableSkillCount": sum(
                row.get("hitRateCalculable") is True
                for rows in projectile_hit_rate_runtime["players"].values()
                for row in rows
            ),
            "resolvedSkillActionActorCount": (
                projectile_skill_action_resolution["resolvedCount"]
            ),
            "unresolvedSkillActionActorCount": (
                projectile_skill_action_resolution["unresolvedCount"]
            ),
            "exactSkillDamageResolutionStatus": (
                exact_skill_damage_resolution["status"]
            ),
            "exactSkillDamageResolutionCounts": (
                exact_skill_damage_resolution["counts"]
            ),
            "worldMapObjectCount": len(world_map["staticObjects"]),
            "worldMapEventCount": len(world_map["events"]),
            "restrictionClockUpdateCount": len(restriction_updates),
            "finishResultPlayerCount": len(battle_user_games),
            "wireStatus": "decoded-exact-wire",
            "timeStatus": "derived-tick-divide-60",
            "privacy": "Replay-originated strings, tokens, headers, and raw replay bytes are excluded.",
        },
        "dataSources": {
            "staticGameData": {
                **game_data_meta,
                **game_data_authority,
                "sha256": sha256(config.game_data_path),
                "characterCapabilityCount": character_capability_catalog[
                    "characterCount"
                ],
                "projectileSkillCount": projectile_skill_catalog["skillCount"],
                "projectileDefinitionCount": projectile_skill_catalog[
                    "projectileDefinitionCount"
                ],
                "role": "base stats, level growth, skill cooldown/range, character CC/shield/immunity/movement capabilities, item stats, mode modifiers, code labels",
            },
            "finishGameResult": {
                **finish_game_result_evidence,
                "playerCount": len(battle_user_games),
                "componentSumChecks": len(battle_user_games),
                "role": "final per-player combat totals and result fields",
            },
            "timelineEvents": {
                "status": "decoded-exact-wire-plus-explicit-derived-aggregates",
                "selectedEventCount": len(events),
                "noisePacketCount": noise_packet_count,
                "supportedNoiseNotificationCount": len(derived_noise_notifications),
                "tacticalPingCount": len(tactical_pings),
                "restrictionClockUpdateCount": len(restriction_updates),
                "wildlifeInstanceCount": len(wildlife),
                "worldMapObjectCount": len(world_map["staticObjects"]),
                "worldMapEventCount": len(world_map["events"]),
                "role": "all damage packets plus player-related combat state, skill, cooldown, heal, wildlife lifecycle, exact tactical pings, phase clocks, world-object lifecycles, and automatic-noise sources retained only for private evidence",
                "damageCoverage": damage_timeline_coverage,
                "skillDamageAttribution": skill_damage_boundary,
            },
        },
        "map": {
            "world": map_meta["world"],
            "image": map_meta["img"],
            "coordinateSpace": map_meta["coordinateSpace"],
            "imageContentRect": map_meta["imageContentRect"],
            "imageAlignment": map_meta["imageAlignment"],
            "projection": map_meta["projection"],
            "source": map_meta["source"],
            "imageDataUrl": map_image_data_url,
            "imageSha256": (
                None if cobalt_map_unavailable else sha256(config.map_image_path)
            ),
            "imageStatus": (
                "unavailable-mode-specific-map"
                if cobalt_map_unavailable
                else "user-provided-satellite-map-not-client-asset-authority"
            ),
            "playbackStatus": mode_compatibility["mapStatus"],
            "movementStatus": "decoded-exact-command-anchors-held-until-next-observation-plus-intended-nav-corners",
            "movementPacketCounts": dict(sorted(movement_packet_counts.items())),
            "movementAnchorCount": sum(
                player["movementAnchorCount"] for player in players
            ),
            "plannedPathCommandCount": sum(
                player["plannedPathCommandCount"] for player in players
            ),
            "plannedPathNodeCount": sum(
                player["plannedPathNodeCount"] for player in players
            ),
            "wildlifeMovementPacketCounts": dict(
                sorted(monster_movement_packet_counts.items())
            ),
            "wildlifeMovementAnchorCount": sum(
                item["movementAnchorCount"] for item in wildlife
            ),
            "positionScale": "Vector2Int / 100; Vector2 as stored",
        },
        "sources": [
            {"file": config.replay_label(), "sha256": replay_sha256, "role": "wire source; not embedded"},
            {"file": config.source_label(config.inspect_path), "sha256": sha256(config.inspect_path), "role": "player/snapshot source"},
            {"file": config.source_label(config.enum_path), "sha256": sha256(config.enum_path), "role": "version-matched enum labels"},
            {"file": config.source_label(config.spawn_path), "sha256": sha256(config.spawn_path), "role": "spawn semantic candidate/owner evidence"},
            {"file": config.source_label(config.schema_path), "sha256": sha256(config.schema_path), "role": "MemoryPack field order/type"},
            {"file": config.source_label(config.game_data_path), "sha256": sha256(config.game_data_path), "role": "replay-referenced Character/Skill labels"},
            {"file": config.source_label(config.map_image_path), "sha256": sha256(config.map_image_path), "role": ("packaged Lumia satellite map; deliberately unused for cobalt" if cobalt_map_unavailable else "user-provided public shared satellite map; not exact client asset authority")},
            {"file": config.source_label(config.map_meta_path), "sha256": sha256(config.map_meta_path), "role": ("packaged Lumia bounds; deliberately unused for cobalt" if cobalt_map_unavailable else "reference map world bounds")},
            {"file": config.source_label(config.names_path), "sha256": sha256(config.names_path), "role": "Korean character display labels"},
            {"file": config.source_label(config.names_supplement_path), "sha256": sha256(config.names_supplement_path), "role": "official Korean label supplement for client 12.2"},
        ],
        "eventTypes": event_defs,
        "wrappers": {value: key for key, value in WRAPPER_CODES.items()},
        "enums": {name: {str(key): value for key, value in mapping.items()} for name, mapping in enum_values.items()},
        "enumStatuses": enum_statuses,
        "skillCodes": {str(code): value for code, value in skill_code_map.items()},
        "itemCatalog": item_catalog,
        "combatJudgment": combat_judgment_summary,
        "deathReview": death_review_summary,
        "growthTempo": growth_tempo_summary,
        "objectivePreparation": objective_preparation_summary,
        "skillOperation": skill_operation_summary,
        "sceneCoaching": scene_coaching_summary,
        "characterCapabilityCatalog": character_capability_catalog,
        "projectileSkillCatalog": projectile_skill_catalog,
        "projectileHitRateRuntime": {
            **projectile_hit_rate_runtime,
            "projectileSpawnCount": len(runtime_projectile_spawns),
            "projectileCollisionCount": len(projectile_collisions),
            "resolvedCharacterSkillStartCount": len(projectile_skill_starts),
            "excludedNonCharacterOrUnresolvedSkillStartCount": (
                unresolved_character_skill_start_count
            ),
        },
        "objectOwners": {str(key): value for key, value in resolved_object_owners.items()},
        "objectOwnerStatuses": {
            str(key): object_owner_statuses[key]
            for key in resolved_object_owners
        },
        "observedNonPlayerObjectIds": sorted(observed_non_player_object_ids),
        "observedNonPlayerObjectIdsStatus": (
            "decoded-exact-SnapshotWrapper-objectIds-excluding-player-summary"
        ),
        "summonCameraDiagnostics": {
            "status": (
                "private-diagnostic-exact-SummonCamera-and-installation-packets"
            ),
            "objects": summon_camera_objects,
            "countsBySummonId": summon_camera_counts,
            "installationActivations": correlated_installation_activations,
            "itemSkillActions": [
                {
                    **row,
                    "actorPlayerObjectId": resolved_player(row.get("objectId")),
                }
                for row in item_skill_actions
            ],
            "publicExposure": False,
        },
        "summonTrapDiagnostics": {
            "status": "private-diagnostic-exact-SummonTrap-and-SummonSnapshot",
            "objects": [
                {
                    **row,
                    "movementTrack": movement_by_world_object.get(row["objectId"], []),
                }
                for row in sorted(
                    summon_trap_diagnostics_by_id.values(),
                    key=lambda item: (item["firstSeenTick"], item["objectId"]),
                )
            ],
            "publicExposure": False,
        },
        "packetDecodedCounts": dict(sorted(decoded_counts.items())),
        "fixedPointEvidence": {
            "formula": "seconds = BlisFixedPoint.internalValue / 100",
            "samePlayerNearTickStartCooldownPairs": pair_count,
            "exactBaseTableCooldownMatches": base_table_matches,
            "status": "derived-scale-crosscheck-v1",
            "caveat": "쿨다운 감소·레벨·상태 효과로 게임 데이터 기본값과 달라질 수 있음",
        },
        "players": players,
        "wildlife": wildlife,
        "worldMap": world_map,
        "noiseNotifications": derived_noise_notifications,
        "tacticalPings": tactical_pings,
        "phaseClock": {
            "status": "decoded-exact-day-night-phase-and-remain-time",
            "restrictionUpdates": restriction_updates,
            "gamePlayPhaseUpdates": gameplay_phase_updates,
        },
        "events": events,
        "playerEventIndexes": {str(pid): indexes for pid, indexes in player_event_indexes.items()},
        "limitations": [
            "전투 시간은 CmdUpdateInCombatType의 0/비0 전환으로 재구성한 값이다.",
            "실험체 전투 도구는 리플레이 헤더가 직접 지정한 공식 gameDb 버전의 정적 기본 정의다. CC 기본 시간은 실제 적용 시간이 아니며 스킬 레벨·강화·상태 저항·모드 보정을 임의로 계산하지 않는다.",
            "교전 중 CC 시간은 같은 리플레이 버전 gameDb의 상태 코드와 CmdAddState 계열의 런타임 duration, 갱신·초기화·해제 수명주기를 사용한다. 상대 시전자나 exact 종료 근거가 빠진 상태는 받은·넣은 CC의 확정값에 넣지 않는다. CmdUpdateShield에는 시전자 필드가 없으므로 보호막은 특정 실험체나 스킬에 귀속하지 않는다.",
            f"경기 종료 총 피해와 기본공격·스킬·아이템·직접·함정 분해는 CmdFinishGameResult의 exact wire 값이다. {expected_player_count}명 모두 분해 합이 총합과 일치한다.",
            "교전별 피해는 대상과 공격자가 플레이어로 확인된 모든 CmdDamage가 숫자일 때만 확정값이다. 하나라도 null이면 일부 숫자 합계를 총피해로 표시하지 않고 확인 불가로 남긴다.",
            "projectile/summon attackerId는 선택된 spawn schema의 ownerId로 player에 귀속한다.",
            "spawn owner 연결이 없는 attackerId의 피해는 플레이어별 준/받은 피해 합계에 귀속하지 않는다.",
            "CmdDamage에는 매 피해의 skillCode가 없으므로 스킬별 피해량은 이 화면에서 만들지 않는다.",
            "모든 캐릭터 스킬은 투사체 여부·코드 후보·형태·적중 패킷·분모·계산 가능 사유를 보존한다. 이름과 번호가 비슷한 정적 후보만으로는 적중률을 만들지 않는다.",
            "적중률은 최소 3회 반복 관측과 완전한 분모가 확인된 수동 조준 스킬만 제공한다. 배타적으로 연결된 단일·고정 다발 투사체는 적 플레이어와 충돌한 발사체 수/전체 발사체 수를 쓰고, 대상 목록 스킬은 모든 양성 대상이 같은 tick 피해·충돌로 확인된 맞힌 시전/전체 교전 시전을 쓴다. 그 밖에는 CmdDamage.effectCode가 같은 버전 Skill·CharacterState의 동일 캐릭터 스킬 그룹과 유일하게 일치하고 모든 피해가 같은 스킬 액션 tick일 때만 맞힌 시전/전체 교전 시전을 쓴다. 관통·폭발·설치·왕복·지속/지연 피해나 불완전한 액션 관측은 unavailable이다.",
            "교전 판단력의 확인된 대인 교전은 팀원 중 한 명 이상의 exact 전투 상태가 끊기지 않고 이어지면서 팀의 적 플레이어 대상 지정이 하나 이상 있는 구간이다. 대상 지정은 표적 선택 근거이며 적중으로 해석하지 않는다.",
            "성장 템포의 레벨은 full snapshot 최초 관측, 장비 완성 수는 exact 장비 업데이트와 같은 버전 item table의 isCompletedItem을 사용한다. snapshot 사이의 미관측 도달 시각은 만들지 않는다.",
            "오브젝트 준비는 exact 예고·활성 시각과 위치, 마지막 exact 이동 앵커를 다음 관측까지 유지한 거리만 사용한다. 20m 진입은 준비 위치 관측이며 획득·처치·소유권 판정이 아니다.",
            "스킬 운영은 확인된 대인 교전 안의 스킬·기본 공격 시작 순서다. 3초 연계는 인접 시작 패킷의 시간 관계이며 적중·피해 콤보를 뜻하지 않는다.",
            "스킬 사용 횟수는 CmdStartSkill 시작 패킷 수이며 다단·재시전·패시브를 한 번의 입력으로 합치지 않는다.",
            "쿨다운 준비 비율은 전투 상태와 시작/수정 패킷으로 만든 파생값이며 CC·침묵·사망·대상·사거리 조건을 보정하지 않는다.",
            "맵 마커는 exact 이동 명령·정지·워프·스냅샷의 마지막 관측 위치를 다음 관측까지 유지한다. CmdMoveToDestination corners는 의도된 경로 형상만 제공하며 노드별 도달 tick은 만들지 않는다.",
            "야생동물 마커도 마지막 exact 생성·이동 위치를 다음 관측까지 유지하며, 사망은 CmdDead와 파괴는 CmdDestroy로 구분한다.",
            "운석·생명의 나무는 같은 objectId의 CmdDestroy 시각에만 제거한다. 파괴 기록이 없으면 다음 일정의 isCollected 값이나 인벤토리 근접으로 획득을 추측하지 않는다.",
            "자동 알림은 CmdNoise의 exact 종류·발생 좌표·발생자를 사용한다. 수신자 필드가 없으므로 외부 거리·쿨타임 규칙을 적용한 플레이어별 결과는 실제 수신 기록이 아닌 표시 추정이다.",
            (
                "코발트 전용 지도 이미지와 좌표 경계를 검증하지 못했으므로 루미아 섬 배경·경계로 대체하지 않고 맵 재생을 unavailable로 유지한다. 전투 결과와 이벤트 분석에는 영향을 주지 않는다."
                if cobalt_map_unavailable
                else "맵 배경은 사용자가 제공한 public shared Satellite Map이다. 리플레이 world X/Z는 별도로 검증된 affine 식으로 원본 772×1000 픽셀에 투영한다. 이 그림을 exact 12.2 client asset이라고 주장하지 않으며 좌표·이벤트 수치의 권위 소스로 사용하지 않는다."
            ),
            "BSER WITHHELD_ACQUISITION_DETAIL 응답은 취득 코드에 존재하지만 현재 세션 상태가 없어 live field-by-field 비교는 실행하지 않았다.",
            "tick/60은 60Hz 환산이며 공식 UI 경기 시간이라고 단정하지 않는다.",
        ],
    }


HTML_TEMPLATE = r'''<!doctype html>
<html lang="ko">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>ER 리플레이 전투 검증기 · __GAME_ID__</title>
<style>
:root{color-scheme:light dark;--bg:light-dark(#f5f7fb,#0b1020);--surface:light-dark(#fff,#121a2b);--surface2:light-dark(#eef2f8,#182238);--text:light-dark(#142033,#edf3ff);--muted:light-dark(#5f6d80,#9ba9bf);--line:light-dark(#dce3ed,#2a3650);--accent:light-dark(#315bea,#7aa2ff);--accent-soft:light-dark(#e8efff,#17264b);--combat:light-dark(#315bea,#7aa2ff);--dealt:light-dark(#b65f00,#ffad5a);--taken:light-dark(#c3364b,#ff7084);--heal:light-dark(#087f5b,#54d6a3);--hp:light-dark(#5b35c9,#b18cff);--team1:light-dark(#2563eb,#60a5fa);--team2:light-dark(#ea580c,#fb923c);--team3:light-dark(#16a34a,#4ade80);--team4:light-dark(#9333ea,#c084fc);--team5:light-dark(#ca8a04,#facc15);--team6:light-dark(#db2777,#f472b6);--team7:light-dark(#dc2626,#f87171);--shadow:0 8px 30px color-mix(in srgb,var(--text) 8%,transparent)}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--text);font:14px/1.5 system-ui,-apple-system,"Segoe UI",sans-serif}button,input,select{font:inherit;color:inherit}.app{max-width:1480px;margin:auto;padding:22px}.top{display:flex;gap:18px;justify-content:space-between;align-items:flex-end;flex-wrap:wrap}.top h1{font-size:24px;margin:0 0 5px}.top p{margin:0;color:var(--muted)}.picker{display:grid;gap:5px;min-width:min(100%,380px)}select,input[type=search],input[type=number]{background:var(--surface);border:1px solid var(--line);border-radius:8px;padding:9px 10px}.tabs{display:flex;gap:5px;overflow:auto;margin:18px 0 14px;padding-bottom:2px}.tabs button,.btn{border:1px solid var(--line);background:var(--surface);border-radius:8px;padding:8px 11px;cursor:pointer;white-space:nowrap}.tabs button.active,.btn.primary{background:var(--accent);border-color:var(--accent);color:white}.stats{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:10px;margin-bottom:12px}.stat{background:var(--surface);border:1px solid var(--line);border-radius:11px;padding:13px;box-shadow:var(--shadow)}.stat span{display:block;color:var(--muted);font-size:12px}.stat b{display:block;font-size:21px;margin-top:3px}.panel{background:var(--surface);border:1px solid var(--line);border-radius:12px;padding:14px;margin-bottom:12px;box-shadow:var(--shadow)}.panel h2{font-size:16px;margin:0 0 10px}.panel h3{font-size:14px;margin:14px 0 7px}.muted{color:var(--muted)}.small{font-size:12px}.badge{display:inline-flex;align-items:center;padding:2px 7px;border-radius:999px;background:var(--surface2);color:var(--muted);font-size:11px;white-space:nowrap}.badge.exact{background:var(--accent-soft);color:var(--accent)}.timeline-wrap{position:relative}.timeline-wrap canvas{display:block;width:100%;height:270px;border:1px solid var(--line);border-radius:8px;background:var(--surface2);touch-action:none}.timeline-controls{display:grid;grid-template-columns:auto 100px minmax(180px,1fr) auto;gap:8px;align-items:center;margin-top:9px}.timeline-controls input{width:100%}.legend{display:flex;gap:14px;flex-wrap:wrap;margin:7px 0;color:var(--muted);font-size:12px}.swatch{width:10px;height:10px;display:inline-block;border-radius:2px;margin-right:4px}.table-wrap{overflow:auto;border:1px solid var(--line);border-radius:8px}table{width:100%;border-collapse:collapse;min-width:720px}th,td{text-align:left;padding:8px 9px;border-bottom:1px solid var(--line);vertical-align:top}th{position:sticky;top:0;background:var(--surface2);font-size:12px;color:var(--muted);z-index:1}tbody tr:last-child td{border-bottom:0}.num{text-align:right;font-variant-numeric:tabular-nums}.mono,code,pre{font-family:ui-monospace,SFMono-Regular,Consolas,monospace}tr.clickable{cursor:pointer}tr.clickable:hover{background:var(--accent-soft)}tr.selected{background:var(--accent-soft)}.split{display:grid;grid-template-columns:minmax(0,1.55fr) minmax(300px,.85fr);gap:12px}.split>*{min-width:0}.detail{min-width:0}.detail pre{margin:0;max-height:540px;overflow:auto;white-space:pre-wrap;word-break:break-word;background:var(--surface2);padding:11px;border-radius:8px;font-size:12px}.controls{display:flex;gap:8px;align-items:end;flex-wrap:wrap;margin-bottom:10px}.control{display:grid;gap:4px}.control label{font-size:12px;color:var(--muted)}.pager{display:flex;justify-content:space-between;align-items:center;gap:10px;margin-top:9px}.method-grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:10px}.method{min-width:0;background:var(--surface2);border-radius:9px;padding:12px}.method code{overflow-wrap:anywhere;word-break:break-word}.method .badge{white-space:normal;overflow-wrap:anywhere;word-break:break-word}.method h3{margin:0 0 6px}.method p{margin:4px 0}.notice{padding:10px;border-left:3px solid var(--accent);background:var(--accent-soft);border-radius:4px}.empty{padding:22px;text-align:center;color:var(--muted)}.sr-only{position:absolute;width:1px;height:1px;padding:0;margin:-1px;overflow:hidden;clip:rect(0,0,0,0);white-space:nowrap;border:0}
.picker select{width:100%;min-width:0;max-width:100%}.split{align-items:start}.map-layout{display:grid;grid-template-columns:minmax(0,1.65fr) minmax(320px,.75fr);gap:12px;align-items:start}.map-layout>*{min-width:0}.map-wrap{position:relative}.map-wrap canvas{display:block;width:100%;min-height:280px;background:var(--surface2);border-radius:8px;touch-action:none}.map-feed{max-height:660px;overflow:auto}.feed-row{display:grid;grid-template-columns:64px 1fr;gap:8px;padding:7px 2px;border-bottom:1px solid var(--line);cursor:pointer}.feed-row:last-child{border-bottom:0}.feed-row:hover{background:var(--accent-soft)}.damage-parts{display:grid;grid-template-columns:repeat(5,minmax(0,1fr));gap:8px;margin:10px 0}.damage-part{background:var(--surface2);border-radius:8px;padding:9px}.damage-part span{display:block;color:var(--muted);font-size:12px}.damage-part b{font-size:16px}.team-legend{display:flex;flex-wrap:wrap;gap:8px 14px;margin:9px 0;color:var(--muted);font-size:12px}.team-dot{display:inline-block;width:11px;height:11px;border-radius:50%;margin-right:5px;vertical-align:-1px}.analysis-grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:12px}.analysis-block{min-width:0}.analysis-block h3{margin-top:0}.analysis-list{margin:0;padding-left:20px}.analysis-list li+li{margin-top:8px}.analysis-facts{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:8px}.analysis-fact{background:var(--surface2);border-radius:8px;padding:10px}.analysis-fact span{display:block;color:var(--muted);font-size:12px}.analysis-fact b{font-size:17px}.event-jump{white-space:normal;text-align:left}.team-row-selected{background:var(--accent-soft)}details pre{max-height:520px;overflow:auto;white-space:pre-wrap;word-break:break-word;background:var(--surface2);padding:11px;border-radius:8px;font-size:12px}
@media(max-width:900px){.stats{grid-template-columns:repeat(2,minmax(0,1fr))}.split,.map-layout,.method-grid,.analysis-grid{grid-template-columns:1fr}.map-feed{max-height:420px}.timeline-controls{grid-template-columns:auto 90px 1fr}.timeline-controls .cursor-label{grid-column:1/-1}}
@media(max-width:520px){.app{padding:12px}.stats{grid-template-columns:1fr 1fr}.stat b{font-size:18px}.timeline-controls{grid-template-columns:1fr 1fr}.timeline-controls input{grid-column:1/-1}.damage-parts,.analysis-facts{grid-template-columns:1fr 1fr}.tabs button,.btn{min-height:42px}.top h1{font-size:20px}}
</style>
</head>
<body>
<main class="app">
  <header class="top">
    <div><h1>리플레이 전투 검증기</h1><p>game __GAME_ID__ · client __CLIENT_VERSION__ · __MATCH_MODE__ · 계산값에서 근거 이벤트까지 직접 확인</p></div>
    <label class="picker">플레이어<select id="playerSelect"></select></label>
  </header>
  <nav class="tabs" aria-label="분석 화면">
    <button data-view="map" class="active">맵 재생</button>
    <button data-view="overview">전투 타임라인</button>
    <button data-view="result">경기 결과</button>
    <button data-view="analysis">경기 상세 분석</button>
    <button data-view="skills">스킬·쿨다운</button>
    <button data-view="events">원본 이벤트</button>
    <button data-view="method">계산·근거</button>
  </nav>
  <section id="stats" class="stats" aria-live="polite"></section>
  <section id="content"></section>
</main>
<script>
const data=__DATA__;
const $=s=>document.querySelector(s), esc=v=>String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const playerMap=new Map(data.players.map(p=>[p.objectId,p]));
const state={playerId:data.players[0].objectId,view:'map',cursor:data.meta.firstTick,playing:false,speed:4,mapFeedMode:'major',eventScope:'player',eventType:'all',eventPage:1,eventQuery:'',eventFrom:data.meta.firstTick,eventTo:data.meta.lastTick,selectedEvent:null,session:null};
let animationStart=null,animationTick=null,resizeObserver=null,lastNearRender=0;
const mapImage=new Image();mapImage.src=data.map.imageDataUrl;
const colorProbe=document.createElement('span');colorProbe.setAttribute('aria-hidden','true');colorProbe.style.cssText='position:absolute;visibility:hidden;pointer-events:none';document.body.appendChild(colorProbe);
const colorCache=new Map();
function css(name){if(!colorCache.has(name)){colorProbe.style.color=`var(${name})`;colorCache.set(name,getComputedStyle(colorProbe).color)}return colorCache.get(name)}
const themeQuery=matchMedia('(prefers-color-scheme: dark)');themeQuery.addEventListener?.('change',()=>{colorCache.clear();drawTimeline();drawMap()});
const fmt=n=>new Intl.NumberFormat('ko-KR').format(n??0);
const clock=t=>{const s=t/60,m=Math.floor(s/60),r=s-m*60;return `${String(m).padStart(2,'0')}:${r.toFixed(1).padStart(4,'0')}`};
const playerLabel=id=>{const p=playerMap.get(id);return p?`${p.characterName} · T${p.teamNumber} · #${id}`:`object #${id}`};
const actorLabel=id=>{const owner=data.objectOwners[String(id)];return owner&&owner!==id?`${playerLabel(owner)} 소유 object #${id}`:playerLabel(id)};
const enumLabel=(name,value)=>data.enums[name]?.[String(value)]??String(value);
const selectedPlayer=()=>playerMap.get(state.playerId);
const eventIndexes=()=>data.playerEventIndexes[String(state.playerId)]||[];
const eventDef=e=>data.eventTypes[String(e[2])];
const packetLabel=e=>{const d=eventDef(e);return `${d.displayName} (${d.packetName})`};
function lowerEventTick(tick){let lo=0,hi=data.events.length;while(lo<hi){const mid=(lo+hi)>>1;if(data.events[mid][0]<tick)lo=mid+1;else hi=mid}return lo}
function eventsBetween(from,to){const out=[];for(let i=lowerEventTick(from);i<data.events.length&&data.events[i][0]<=to;i++)out.push(data.events[i]);return out}
function positionAt(p,tick){const a=p.movementTrack,b=data.map.world,visible=pos=>b.minx<=pos[0]&&pos[0]<=b.maxx&&b.minz<=pos[1]&&pos[1]<=b.maxz?pos:null;if(!a.length)return null;let lo=0,hi=a.length;while(lo<hi){const m=(lo+hi)>>1;if(a[m][0]<=tick)lo=m+1;else hi=m}const last=a[Math.max(0,lo-1)];return visible([last[1],last[2]])}
function lifeAt(p,tick){let status='alive';for(const row of p.lifeTimeline){if(row[0]>tick)break;status=row[1]}return status}
function eventObject(e){const d=eventDef(e),fields={};d.fields.forEach((name,i)=>{let value=e[5+i];if(d.fixedPointFields.includes(name)&&value!==null)value={internalValue:value};fields[name]=value});return {tick:e[0],secondsAt60Hz:+(e[0]/60).toFixed(6),selectedEventIndex:e[1],wrapperCategory:data.wrappers[String(e[3])],wrapperIndex:e[4],packetType:d.packetType,packetName:d.packetName,displayName:d.displayName,wireStatus:d.wireStatus,fields}}
function value(e,name){const d=eventDef(e),i=d.fields.indexOf(name);return i<0?null:e[5+i]}
function inCombat(p,tick){return p.combatIntervals.some(x=>x[0]<=tick&&tick<x[1])}
function summary(e){const n=eventDef(e).packetName;switch(n){case'CmdDamage':return `${actorLabel(value(e,'attackerId'))} → ${playerLabel(value(e,'objectId'))} · ${value(e,'damage')??'null'} 피해 · ${enumLabel('DamageType',value(e,'damageType'))}`;case'CmdHeal':case'CmdHealStateCode':return `${playerLabel(value(e,'objectId'))} · HP +${value(e,'addHp')} · caster ${actorLabel(value(e,'casterId'))}`;case'CmdStartSkill':return `${enumLabel('SkillId',value(e,'skillId'))} · skillCode ${value(e,'skillCode')} · target ${playerLabel(value(e,'targetObjectId'))}`;case'CmdStartNormalAttackSkill':return `${data.skillCodes[String(value(e,'skillCode'))]?.skillId||'normal attack'} · target ${playerLabel(value(e,'targetObjectId'))}`;case'CmdStartPassiveSkill':case'CmdStartStateSkill':return `${data.skillCodes[String(value(e,'skillCode'))]?.skillId||enumLabel('SkillId',value(e,'skillId'))} · skillCode ${value(e,'skillCode')}`;case'CmdStartCharacterSkillCooldown':return `${enumLabel('SkillSlotSet',value(e,'skillSlotSet'))} · ${(value(e,'cooldown')/100).toFixed(2)}초`;case'CmdModifyCharacterSkillCooldown':return `${enumLabel('SkillSlotSet',value(e,'skillSlotSet'))} · 남은 ${(value(e,'rusultCooldown')/100).toFixed(2)}초`;case'CmdUpdateInCombatType':return `${playerLabel(value(e,'objectId'))} · ${enumLabel('InCombatType',value(e,'inCombatType'))}`;case'CmdFinishSkill':return `${enumLabel('SkillId',value(e,'skillId'))} · ${enumLabel('SkillStopReason',value(e,'reason'))}`;case'CmdDyingCondition':return `${playerLabel(value(e,'objectId'))} 다운 · HP ${value(e,'hp')}`;case'CmdResurrection':return `${playerLabel(value(e,'objectId'))} 부활 · 생존시간 ${(value(e,'survivalTime')/100).toFixed(1)}초`;case'CmdDead':{const victim=value(e,'objectId'),finisher=value(e,'finishingAttackerObjectId');return victim===finisher?`${playerLabel(victim)} 사망 · finishingAttacker=self (다운 종료/시스템 기록 가능)`:`${playerLabel(victim)} 사망 · finishingAttacker ${actorLabel(finisher)}`};case'CmdKill':return `${playerLabel(value(e,'objectId'))} → ${playerLabel(value(e,'deadCharacterObjectId'))} 처치/다운 귀속 기록`;case'CmdBlock':return `${playerLabel(value(e,'objectId'))} · ${value(e,'damage')} 방어`;case'CmdCrowdControl':return `${playerLabel(value(e,'objectId'))} · ${enumLabel('StateType',value(e,'stateType'))}`;default:return eventDef(e).fields.map(f=>`${f}=${JSON.stringify(value(e,f))}`).join(' · ')}}
function badge(text,exact=false){return `<span class="badge ${exact?'exact':''}">${esc(text)}</span>`}
function renderPlayerOptions(){const select=$('#playerSelect');select.innerHTML=data.players.map(p=>`<option value="${p.objectId}">팀 ${p.teamNumber} · ${esc(p.characterName)} · object ${p.objectId} · user ${p.userId}</option>`).join('');select.value=state.playerId;select.onchange=()=>{state.playerId=Number(select.value);state.session=null;state.selectedEvent=null;state.eventPage=1;render()}}
function renderStats(){const p=selectedPlayer(),g=p.gameResult;$('#stats').innerHTML=`<article class="stat"><span>전투 상태 시간</span><b>${p.stats.combatSeconds.toFixed(1)}초</b><small class="muted">${p.stats.combatSessions}개 구간 · 전환 패킷 기반</small></article><article class="stat"><span>경기 총 준 피해</span><b>${fmt(g.damageToPlayer)}</b><small class="muted">CmdFinishGameResult · exact</small></article><article class="stat"><span>경기 총 받은 피해</span><b>${fmt(g.damageFromPlayer)}</b><small class="muted">CmdFinishGameResult · exact</small></article><article class="stat"><span>액티브 스킬 시작</span><b>${fmt(p.stats.activeSkillStarts)}</b><small class="muted">CmdStartSkill 패킷 수</small></article>`}
function stopPlay(){state.playing=false;animationStart=null;animationTick=null;for(const id of ['#playButton','#mapPlayButton']){const b=$(id);if(b)b.textContent='재생'}}
function setCursor(tick,refreshEvidence=true){state.cursor=Math.max(data.meta.firstTick,Math.min(data.meta.lastTick,Math.round(tick)));for(const id of ['#timeSlider','#mapTimeSlider']){const slider=$(id);if(slider)slider.value=state.cursor}for(const id of ['#cursorLabel','#mapCursorLabel']){const label=$(id);if(label)label.textContent=`${clock(state.cursor)} · tick ${state.cursor}`}drawTimeline();drawMap();if(refreshEvidence){renderNearEvents();renderMapFeed()}}
function togglePlay(){state.playing=!state.playing;for(const id of ['#playButton','#mapPlayButton']){const b=$(id);if(b)b.textContent=state.playing?'일시정지':'재생'}if(state.playing){if(state.cursor>=data.meta.lastTick)state.cursor=data.meta.firstTick;animationStart=performance.now();animationTick=state.cursor;requestAnimationFrame(stepPlay)}}
function stepPlay(now){if(!state.playing)return;const elapsed=(now-animationStart)/1000,refreshEvidence=now-lastNearRender>=125;setCursor(animationTick+elapsed*state.speed*60,refreshEvidence);if(refreshEvidence)lastNearRender=now;if(state.cursor>=data.meta.lastTick){stopPlay();return}requestAnimationFrame(stepPlay)}
function mapEventAllowed(e){const n=eventDef(e).packetName;if(state.mapFeedMode==='major'){if(['CmdDyingCondition','CmdDead','CmdResurrection'].includes(n))return playerMap.has(value(e,'objectId'));if(n==='CmdKill')return playerMap.has(value(e,'deadCharacterObjectId'));return false}if(state.mapFeedMode==='combat')return ['CmdDamage','CmdHeal','CmdHealStateCode','CmdBlock','CmdCrowdControl','CmdDyingCondition','CmdDead','CmdKill','CmdResurrection','CmdStartSkill','CmdStartNormalAttackSkill'].includes(n);return true}
function renderMapFeed(){const box=$('#mapFeed');if(!box)return;const rows=eventsBetween(Math.max(data.meta.firstTick,state.cursor-720),state.cursor).filter(mapEventAllowed).slice(-80).reverse();box.innerHTML=rows.length?rows.map(e=>`<div class="feed-row" data-map-event="${e[1]}"><span class="mono">${clock(e[0])}</span><span><b>${esc(packetLabel(e))}</b><br><span class="small muted">${esc(summary(e))}</span></span></div>`).join(''):'<div class="empty">직전 12초에 선택한 종류의 로그가 없습니다.</div>';box.querySelectorAll('[data-map-event]').forEach(row=>row.onclick=()=>{state.selectedEvent=Number(row.dataset.mapEvent);state.eventScope='all';state.eventType='all';state.eventQuery='';state.eventFrom=Math.max(data.meta.firstTick,state.cursor-300);state.eventTo=Math.min(data.meta.lastTick,state.cursor+300);state.eventPage=1;state.view='events';syncTabs();renderContent()})}
function teamColor(n){const exact=css(`--team${n}`);return exact||`hsl(${(n*137.508)%360} 72% 62%)`}
function drawMap(){const canvas=$('#mapCanvas');if(!canvas)return;const rect=canvas.getBoundingClientRect(),dpr=devicePixelRatio||1,w=Math.max(280,rect.width),h=Math.max(280,Math.min(680,w*data.map.image.h/data.map.image.w));canvas.style.height=`${h}px`;canvas.width=Math.round(w*dpr);canvas.height=Math.round(h*dpr);const c=canvas.getContext('2d');c.setTransform(dpr,0,0,dpr,0,0);c.clearRect(0,0,w,h);if(mapImage.complete&&mapImage.naturalWidth)c.drawImage(mapImage,0,0,w,h);else{c.fillStyle=css('--surface2');c.fillRect(0,0,w,h)}const b=data.map.world,X=x=>(x-b.minx)/(b.maxx-b.minx)*w,Y=z=>(b.maxz-z)/(b.maxz-b.minz)*h;const selected=selectedPlayer(),trail=selected.movementTrack.filter(p=>state.cursor-900<=p[0]&&p[0]<=state.cursor);if(trail.length){c.strokeStyle=teamColor(selected.teamNumber);c.globalAlpha=.75;c.lineWidth=2;c.beginPath();trail.forEach((p,i)=>i?c.lineTo(X(p[1]),Y(p[2])):c.moveTo(X(p[1]),Y(p[2])));const cur=positionAt(selected,state.cursor);if(cur)c.lineTo(X(cur[0]),Y(cur[1]));c.stroke();c.globalAlpha=1}canvas.__markers=[];for(const p of data.players){const pos=positionAt(p,state.cursor);if(!pos)continue;const x=X(pos[0]),y=Y(pos[1]),life=lifeAt(p,state.cursor),color=teamColor(p.teamNumber),chosen=p.objectId===state.playerId;c.globalAlpha=life==='dead'?.28:1;c.fillStyle=color;c.beginPath();c.arc(x,y,chosen?8:6,0,Math.PI*2);c.fill();c.strokeStyle=chosen?css('--text'):css('--surface');c.lineWidth=chosen?3:1.5;c.stroke();if(life==='down'){c.strokeStyle=css('--taken');c.lineWidth=2;c.beginPath();c.arc(x,y,11,0,Math.PI*2);c.stroke()}if(life==='dead'){c.strokeStyle=css('--taken');c.lineWidth=2;c.beginPath();c.moveTo(x-6,y-6);c.lineTo(x+6,y+6);c.moveTo(x+6,y-6);c.lineTo(x-6,y+6);c.stroke()}c.globalAlpha=1;if(w>=520||chosen){c.fillStyle=css('--text');c.font=chosen?'500 12px system-ui':'11px system-ui';c.textAlign='center';c.fillText(`${p.characterName} T${p.teamNumber}`,x,Math.max(11,y-11))}canvas.__markers.push({id:p.objectId,x,y})}canvas.dataset.markerCount=String(canvas.__markers.length);canvas.setAttribute('aria-label',`${canvas.__markers.length}명 플레이어 위치와 생존 상태를 보여주는 루미아 섬 재생 지도`);c.fillStyle=css('--text');c.globalAlpha=.85;c.textAlign='left';c.font='500 13px system-ui';c.fillText(`${clock(state.cursor)} · ${selected.characterName} · ${lifeAt(selected,state.cursor)}`,10,20);c.globalAlpha=1}
function renderMap(){const p=selectedPlayer(),teams=[...new Set(data.players.map(x=>x.teamNumber))].sort((a,b)=>a-b);$('#content').innerHTML=`<section class="map-layout"><section class="panel"><h2>${data.meta.playerCount}명 맵 이동 재생</h2><div class="notice small">위치는 이동·정지·워프 명령과 full snapshot의 정확한 좌표를 사용하고, 명령 사이만 선형 보간합니다. 배경 지도는 저장소 reference export라 ${esc(data.meta.clientVersion)} asset 해시는 아직 대조되지 않았습니다.</div><div class="team-legend" aria-label="팀 색상 범례">${teams.map(n=>`<span><i class="team-dot" style="background:${teamColor(n)}"></i>${n}팀</span>`).join('')}</div><div class="map-wrap"><canvas id="mapCanvas" role="img" aria-label="${data.meta.playerCount}명 플레이어 위치와 생존 상태를 보여주는 루미아 섬 재생 지도"></canvas></div><div class="timeline-controls"><button class="btn primary" id="mapPlayButton">재생</button><select id="mapSpeedSelect" aria-label="맵 재생 속도"><option value="1">1×</option><option value="4">4×</option><option value="16">16×</option><option value="60">60×</option></select><input id="mapTimeSlider" type="range" min="${data.meta.firstTick}" max="${data.meta.lastTick}" step="1" value="${state.cursor}" aria-label="맵 시간 커서"><span class="cursor-label mono" id="mapCursorLabel"></span></div><p class="small muted">선택: ${esc(p.characterName)} · 팀 ${p.teamNumber} · ${fmt(p.movementAnchorCount)}개 위치 앵커 · ${badge(p.movementTrackStatus)}</p></section><aside class="panel"><h2>현재 시점 로그</h2><div class="controls"><label class="control">범위<select id="mapFeedMode"><option value="major">킬·다운·사망·부활</option><option value="combat">전투 주요 로그</option><option value="all">모든 선택 패킷</option></select></label><button class="btn" id="openAllEvents">전체 원본 로그</button></div><div id="mapFeed" class="map-feed" aria-live="polite"></div></aside></section>`;$('#mapPlayButton').onclick=togglePlay;$('#mapSpeedSelect').value=String(state.speed);$('#mapSpeedSelect').onchange=e=>state.speed=Number(e.target.value);$('#mapTimeSlider').oninput=e=>{stopPlay();setCursor(Number(e.target.value))};$('#mapFeedMode').value=state.mapFeedMode;$('#mapFeedMode').onchange=e=>{state.mapFeedMode=e.target.value;renderMapFeed()};$('#openAllEvents').onclick=()=>{state.eventScope='all';state.eventType='all';state.eventQuery='';state.eventFrom=data.meta.firstTick;state.eventTo=data.meta.lastTick;state.eventPage=1;state.view='events';syncTabs();renderContent()};const canvas=$('#mapCanvas');canvas.onclick=e=>{const r=canvas.getBoundingClientRect(),sx=canvas.width/(devicePixelRatio||1)/r.width,sy=canvas.height/(devicePixelRatio||1)/r.height,x=(e.clientX-r.left)*sx,y=(e.clientY-r.top)*sy;let best=null;for(const m of canvas.__markers||[]){const d=Math.hypot(m.x-x,m.y-y);if(d<=18&&(!best||d<best.d))best={...m,d}}if(best){state.playerId=best.id;$('#playerSelect').value=String(best.id);renderStats();renderMap()}};if(resizeObserver)resizeObserver.disconnect();resizeObserver=new ResizeObserver(drawMap);resizeObserver.observe(canvas);mapImage.onload=drawMap;setCursor(state.cursor)}
function renderResult(){const p=selectedPlayer(),g=p.gameResult,parts=[['기본공격',g.damageToPlayer_basic],['스킬',g.damageToPlayer_skill],['아이템 스킬',g.damageToPlayer_itemSkill],['직접',g.damageToPlayer_direct],['함정',g.damageToPlayer_trap]];const rows=[...data.players].sort((a,b)=>a.gameResult.gameRank-b.gameResult.gameRank||a.teamNumber-b.teamNumber||b.gameResult.damageToPlayer-a.gameResult.damageToPlayer);$('#content').innerHTML=`<section class="panel"><h2>${esc(p.characterName)} 경기 종료 결과</h2><div class="notice small"><code>CmdFinishGameResult</code> packet ${data.dataSources.finishGameResult.packetType} · tick ${data.dataSources.finishGameResult.tick} · ${data.meta.playerCount}명 exact wire. 총 피해와 5개 분해 합은 ${data.dataSources.finishGameResult.componentSumChecks}/${data.meta.playerCount}명 모두 일치합니다.</div><div class="damage-parts">${parts.map(x=>`<div class="damage-part"><span>${x[0]}</span><b>${fmt(x[1])}</b></div>`).join('')}</div><p>순위 <b>${g.gameRank}</b> · 킬 <b>${g.playerKill}</b> · 어시스트 <b>${g.playerAssistant}</b> · 사망 <b>${g.playerDeaths}</b> · 몬스터 피해 <b>${fmt(g.damageFromMonster)}</b> · 보호 흡수 <b>${fmt(g.protectAbsorb)}</b></p><details><summary>선택 플레이어의 정제된 BattleUserGame 원본 보기</summary><pre>${esc(JSON.stringify(g,null,2))}</pre></details></section><section class="panel"><h2>전체 ${data.meta.playerCount}명 결과</h2><div class="table-wrap"><table><thead><tr><th>순위·팀</th><th>플레이어</th><th class="num">킬</th><th class="num">어시</th><th class="num">사망</th><th class="num">준 피해</th><th class="num">받은 피해</th><th class="num">기본</th><th class="num">스킬</th><th class="num">아이템</th><th class="num">직접</th></tr></thead><tbody>${rows.map(x=>{const r=x.gameResult;return `<tr class="clickable ${x.objectId===state.playerId?'selected':''}" data-result-player="${x.objectId}"><td>${r.gameRank}위 · T${x.teamNumber}</td><td><b>${esc(x.characterName)}</b><div class="small muted">user ${x.userId}</div></td><td class="num">${r.playerKill}</td><td class="num">${r.playerAssistant}</td><td class="num">${r.playerDeaths}</td><td class="num"><b>${fmt(r.damageToPlayer)}</b></td><td class="num">${fmt(r.damageFromPlayer)}</td><td class="num">${fmt(r.damageToPlayer_basic)}</td><td class="num">${fmt(r.damageToPlayer_skill)}</td><td class="num">${fmt(r.damageToPlayer_itemSkill)}</td><td class="num">${fmt(r.damageToPlayer_direct)}</td></tr>`}).join('')}</tbody></table></div></section>`;document.querySelectorAll('[data-result-player]').forEach(row=>row.onclick=()=>{state.playerId=Number(row.dataset.resultPlayer);$('#playerSelect').value=String(state.playerId);renderStats();renderResult()})}
const pct=n=>`${(n*100).toFixed(1)}%`;
function exactRank(players,field,value){return 1+players.filter(x=>(x.gameResult[field]??0)>value).length}
function playerMoments(p){const moments=[];for(const e of data.events){const n=eventDef(e).packetName;if(n==='CmdKill'){const killer=value(e,'objectId'),victim=value(e,'deadCharacterObjectId'),assists=value(e,'assistCharacterObjectIds')||[];if(!playerMap.has(victim))continue;if(killer===p.objectId)moments.push({tick:e[0],event:e[1],kind:'처치·다운 귀속',text:`${playerLabel(victim)} 상대`});if(Array.isArray(assists)&&assists.includes(p.objectId))moments.push({tick:e[0],event:e[1],kind:'어시스트 귀속',text:`${playerLabel(killer)} → ${playerLabel(victim)}`});if(victim===p.objectId)moments.push({tick:e[0],event:e[1],kind:'피다운 귀속',text:`${playerLabel(killer)}에게 귀속`})}else if(n==='CmdDyingCondition'&&value(e,'objectId')===p.objectId)moments.push({tick:e[0],event:e[1],kind:'다운',text:`HP ${value(e,'hp')}`});else if(n==='CmdDead'&&value(e,'objectId')===p.objectId)moments.push({tick:e[0],event:e[1],kind:'사망',text:value(e,'objectId')===value(e,'finishingAttackerObjectId')?'finishingAttacker=self · 다운 종료/시스템 기록 가능':`마무리 ${actorLabel(value(e,'finishingAttackerObjectId'))}`});else if(n==='CmdResurrection'&&value(e,'objectId')===p.objectId)moments.push({tick:e[0],event:e[1],kind:'부활',text:`생존시간 ${(value(e,'survivalTime')/100).toFixed(1)}초`})}return moments.sort((a,b)=>a.tick-b.tick||a.event-b.event)}
function renderAnalysis(){const p=selectedPlayer(),g=p.gameResult,team=data.players.filter(x=>x.teamNumber===p.teamNumber),teamDamage=team.reduce((s,x)=>s+x.gameResult.damageToPlayer,0),teamTaken=team.reduce((s,x)=>s+x.gameResult.damageFromPlayer,0),teamDeaths=team.reduce((s,x)=>s+x.gameResult.playerDeaths,0),teamKills=Math.max(g.teamKill||0,team.reduce((s,x)=>s+x.gameResult.playerKill,0)),damageRank=exactRank(data.players,'damageToPlayer',g.damageToPlayer),killRank=exactRank(data.players,'playerKill',g.playerKill),teamDamageRank=exactRank(team,'damageToPlayer',g.damageToPlayer),teamDeathRank=exactRank(team,'playerDeaths',g.playerDeaths),damageShare=teamDamage?g.damageToPlayer/teamDamage:0,takenShare=teamTaken?g.damageFromPlayer/teamTaken:0,deathShare=teamDeaths?g.playerDeaths/teamDeaths:0,participation=teamKills?(g.playerKill+g.playerAssistant)/teamKills:0,exchange=g.damageFromPlayer?g.damageToPlayer/g.damageFromPlayer:null,basicShare=g.damageToPlayer?g.damageToPlayer_basic/g.damageToPlayer:0,monsterDamage=['trap','basic','skill','itemSkill','direct'].reduce((s,k)=>s+(g[`damageToMonster_${k}`]||0),0),combatRatio=g.duration?p.stats.combatSeconds/g.duration:0,moments=playerMoments(p),deathMoments=moments.filter(x=>x.kind==='사망'),ready=p.cooldowns.filter(x=>x.readyCombatRatio!==null).sort((a,b)=>b.readyCombatRatio-a.readyCombatRatio)[0],bow=p.skills.find(x=>x.skillName==='BowActive'),strengths=[],checks=[];
if(g.gameRank===1)strengths.push(`경기 <b>1위</b>로 끝냈습니다. 종료 결과의 exact wire 값입니다.`);else strengths.push(`경기 최종 순위는 <b>${g.gameRank}위</b>입니다.`);
strengths.push(`준 피해 <b>${fmt(g.damageToPlayer)}</b>로 로비 <b>${damageRank}위</b>, 팀 <b>${teamDamageRank}위</b>입니다. 팀 전체 피해의 <b>${pct(damageShare)}</b>를 담당했습니다.`);
strengths.push(`<b>${g.playerKill}킬 ${g.playerAssistant}어시스트</b>로 팀 ${teamKills}킬 중 ${g.playerKill+g.playerAssistant}회(<b>${pct(participation)}</b>)에 관여했습니다. 킬 수는 로비 ${killRank}위입니다.`);
if(exchange!==null)strengths.push(`플레이어에게 준 피해/받은 피해 비율은 <b>${exchange.toFixed(2)}</b>입니다 (${fmt(g.damageToPlayer)} / ${fmt(g.damageFromPlayer)}). 생존·포지셔닝 원인까지 뜻하는 값은 아닙니다.`);
strengths.push(`시야·운영 기록은 망원 카메라 설치 ${g.addTelephotoCamera}, 제거 ${g.removeTelephotoCamera}, 보안 콘솔 ${g.useSecurityConsole}, 정찰 드론 ${g.useReconDrone}, EMP 드론 ${g.useEmpDrone}회입니다.`);
checks.push(`사망 <b>${g.playerDeaths}회</b>로 팀 내 ${teamDeathRank}위(동률 포함), 팀 사망의 <b>${pct(deathShare)}</b>입니다. 사망 시점은 ${deathMoments.length?deathMoments.map(x=>clock(x.tick)).join(', '):'기록 없음'}이며 아래 행에서 지도와 원본 패킷을 열 수 있습니다.`);
if(ready)checks.push(`${ready.family}은 관측 전투 중 쿨다운 준비 상태가 <b>${pct(ready.readyCombatRatio)}</b>였습니다. 다만 ${esc(ready.caveat)}이므로 “놀린 스킬”로 확정하지 않고 영상·위치와 대조할 검토 신호로만 봐야 합니다.`);
checks.push(`전투 상태 플래그는 ${p.stats.combatSessions}개 구간, ${p.stats.combatSeconds.toFixed(1)}초(<b>${pct(combatRatio)}</b>)였습니다. 이 플래그에는 야생동물 전투가 섞일 수 있어 PvP 교전율로 단정할 수 없습니다.`);
checks.push(`기본 공격 피해 비중은 <b>${pct(basicShare)}</b>입니다. 리오의 공격 구조와 맞는 수치지만, <code>CmdDamage</code>에 매 타격의 skillCode가 없어 스킬별 적중률·피해량은 아직 확정할 수 없습니다.`);
const damageParts=[['기본 공격',g.damageToPlayer_basic],['스킬',g.damageToPlayer_skill],['아이템 스킬',g.damageToPlayer_itemSkill],['직접',g.damageToPlayer_direct],['함정',g.damageToPlayer_trap]];
$('#content').innerHTML=`<section class="panel"><h2>${esc(p.characterName)} · 이 경기 자체 분석</h2><div class="notice small">6축 점수나 외부 평균이 아닙니다. 이 한 경기의 종료 결과, 팀 내 상대값, 킬·다운·사망·부활 패킷과 전투/쿨다운 타임라인만 사용했습니다. “왜” 잘못했는지는 지도 재생과 원본 이벤트로 다시 확인해야 합니다.</div><div class="analysis-grid" style="margin-top:12px"><section class="analysis-block"><h3>잘한 점 · 객관적 성과</h3><ul class="analysis-list">${strengths.map(x=>`<li>${x}</li>`).join('')}</ul></section><section class="analysis-block"><h3>못한 점 후보 · 직접 확인 필요</h3><ul class="analysis-list">${checks.map(x=>`<li>${x}</li>`).join('')}</ul></section></div></section><section class="panel"><h2>팀 ${p.teamNumber} 비교</h2><div class="table-wrap"><table><thead><tr><th>실험체</th><th class="num">킬</th><th class="num">어시</th><th class="num">사망</th><th class="num">준 피해</th><th class="num">팀 피해 비중</th><th class="num">받은 피해</th><th class="num">팀 피격 비중</th></tr></thead><tbody>${team.sort((a,b)=>b.gameResult.damageToPlayer-a.gameResult.damageToPlayer).map(x=>{const r=x.gameResult;return `<tr class="${x.objectId===p.objectId?'team-row-selected':''}"><td><b>${esc(x.characterName)}</b></td><td class="num">${r.playerKill}</td><td class="num">${r.playerAssistant}</td><td class="num">${r.playerDeaths}</td><td class="num"><b>${fmt(r.damageToPlayer)}</b></td><td class="num">${pct(r.damageToPlayer/teamDamage)}</td><td class="num">${fmt(r.damageFromPlayer)}</td><td class="num">${pct(r.damageFromPlayer/teamTaken)}</td></tr>`}).join('')}</tbody></table></div><p class="small muted">팀 합계: ${teamKills}킬 · ${team.reduce((s,x)=>s+x.gameResult.playerAssistant,0)}어시스트 · ${teamDeaths}사망 · 준 피해 ${fmt(teamDamage)} · 받은 피해 ${fmt(teamTaken)}</p></section><section class="panel"><h2>피해·운영 구조</h2><div class="analysis-facts">${damageParts.map(([name,n])=>`<div class="analysis-fact"><span>${name}</span><b>${fmt(n)}</b><small class="muted">${pct(n/Math.max(1,g.damageToPlayer))}</small></div>`).join('')}<div class="analysis-fact"><span>몬스터 피해</span><b>${fmt(monsterDamage)}</b><small class="muted">몬스터 ${fmt(g.monsterKill)}마리</small></div><div class="analysis-fact"><span>보호 흡수</span><b>${fmt(g.protectAbsorb)}</b><small class="muted">종료 결과 exact</small></div><div class="analysis-fact"><span>VF 크레딧</span><b>${fmt(g.totalGainVFCredit)} / ${fmt(g.totalUseVFCredit)}</b><small class="muted">획득 / 사용</small></div><div class="analysis-fact"><span>기본 공격 시작</span><b>${fmt(p.stats.normalAttackStarts)}</b><small class="muted">패킷 수</small></div><div class="analysis-fact"><span>액티브 스킬 시작</span><b>${fmt(p.stats.activeSkillStarts)}</b><small class="muted">패킷 수</small></div><div class="analysis-fact"><span>무기 스킬 시작</span><b>${fmt(bow?.startCount||0)}</b><small class="muted">${bow?'BowActive · 별도 SkillId':'관측 없음'}</small></div></div></section><section class="panel"><h2>킬·다운·사망·부활 근거 타임라인</h2><div class="table-wrap"><table><thead><tr><th>시간</th><th>기록</th><th>상대·세부</th><th>확인</th></tr></thead><tbody>${moments.map(m=>`<tr><td class="mono">${clock(m.tick)}<div class="small muted">tick ${m.tick}</div></td><td><b>${esc(m.kind)}</b></td><td>${esc(m.text)}</td><td><button class="btn event-jump" data-analysis-map="${m.tick}">지도</button> <button class="btn event-jump" data-analysis-event="${m.event}">원본</button></td></tr>`).join('')||'<tr><td colspan="4" class="empty">관련 이벤트 없음</td></tr>'}</tbody></table></div></section><section class="panel"><h2>스킬·쿨다운 검토표</h2><div class="table-wrap"><table><thead><tr><th>슬롯 계열</th><th class="num">전체 시작</th><th class="num">전투 중 시작</th><th class="num">준비 상태</th><th>해석 경계</th></tr></thead><tbody>${p.cooldowns.map(r=>`<tr><td>${r.family}</td><td class="num">${r.startCount}</td><td class="num">${r.inCombatStartCount}</td><td class="num">${r.readyCombatRatio===null?'—':pct(r.readyCombatRatio)}</td><td class="small">${esc(r.caveat)}</td></tr>`).join('')}</tbody></table></div><p class="small muted">WeaponSkill 계열이 0이어도 무기 스킬 미사용을 뜻하지 않습니다. 리오의 활 무기 스킬은 별도 SkillId <code>BowActive</code>로 ${bow?.startCount||0}회 관측됐습니다.</p></section>`;
document.querySelectorAll('[data-analysis-map]').forEach(b=>b.onclick=()=>{state.cursor=Number(b.dataset.analysisMap);state.view='map';syncTabs();renderContent()});document.querySelectorAll('[data-analysis-event]').forEach(b=>b.onclick=()=>{state.selectedEvent=Number(b.dataset.analysisEvent);const e=data.events.find(x=>x[1]===state.selectedEvent);state.eventScope='all';state.eventType='all';state.eventQuery='';state.eventFrom=Math.max(data.meta.firstTick,e[0]-300);state.eventTo=Math.min(data.meta.lastTick,e[0]+300);state.eventPage=1;state.view='events';syncTabs();renderContent()})}
function renderOverview(){const p=selectedPlayer();$('#content').innerHTML=`<section class="panel"><h2>${esc(p.characterName)} 전투·피해·HP 타임라인</h2><div class="legend"><span><i class="swatch" style="background:var(--combat)"></i>전투 상태</span><span><i class="swatch" style="background:var(--hp)"></i>스냅샷 HP</span><span><i class="swatch" style="background:var(--dealt)"></i>기록된 준 피해</span><span><i class="swatch" style="background:var(--taken)"></i>기록된 받은 피해</span><span><i class="swatch" style="background:var(--heal)"></i>HP 추가</span></div><div class="timeline-wrap"><canvas id="timeline" role="img" aria-label="선택 플레이어의 전투 상태, HP, 기록된 피해와 스킬 사용 시간축"></canvas></div><div class="timeline-controls"><button class="btn primary" id="playButton">재생</button><select id="speedSelect" aria-label="재생 속도"><option value="1">1×</option><option value="4" selected>4×</option><option value="16">16×</option><option value="60">60×</option></select><input id="timeSlider" type="range" min="${data.meta.firstTick}" max="${data.meta.lastTick}" step="1" value="${state.cursor}" aria-label="시간 커서"><span class="cursor-label mono" id="cursorLabel"></span></div></section><section class="split"><div><section class="panel"><h2>전투 구간</h2><div class="table-wrap"><table><thead><tr><th>#</th><th>시간</th><th class="num">길이</th><th class="num">기록된 준 피해</th><th class="num">기록된 받은 피해</th><th class="num">스킬 시작</th><th>근거</th></tr></thead><tbody>${p.sessions.map(s=>`<tr><td>${s.number}</td><td class="mono">${clock(s.startTick)}–${clock(s.endTick)}</td><td class="num">${s.durationSeconds.toFixed(1)}s</td><td class="num">${fmt(s.damageDealt)}</td><td class="num">${fmt(s.damageTaken)}</td><td class="num">${s.activeSkillStarts}</td><td><button class="btn" data-session="${s.number}">이 구간 보기</button></td></tr>`).join('')||'<tr><td colspan="7" class="empty">전투 전환 구간 없음</td></tr>'}</tbody></table></div></section></div><aside class="panel"><h2>커서 주변 근거 이벤트</h2><div id="nearEvents"></div></aside></section>`;
$('#content .panel h2').insertAdjacentHTML('afterend','<div class="notice small">상단 총피해는 종료 결과의 정확한 합계입니다. 아래 피해 막대는 개별 <code>CmdDamage.damage</code>가 실제 기록된 일부 이벤트만 표시합니다.</div>');$('#playButton').onclick=togglePlay;$('#speedSelect').value=String(state.speed);$('#speedSelect').onchange=e=>state.speed=Number(e.target.value);$('#timeSlider').oninput=e=>{stopPlay();setCursor(Number(e.target.value))};document.querySelectorAll('[data-session]').forEach(b=>b.onclick=()=>{const s=p.sessions.find(x=>x.number===Number(b.dataset.session));state.session=s;state.cursor=s.startTick;state.eventFrom=s.startTick;state.eventTo=s.endTick;setCursor(s.startTick)});const canvas=$('#timeline');canvas.onclick=e=>{const r=canvas.getBoundingClientRect(),x=e.clientX-r.left,pad=48;setCursor(data.meta.firstTick+(Math.max(pad,Math.min(r.width-18,x))-pad)/(r.width-pad-18)*(data.meta.lastTick-data.meta.firstTick))};if(resizeObserver)resizeObserver.disconnect();resizeObserver=new ResizeObserver(drawTimeline);resizeObserver.observe(canvas);setCursor(state.cursor)}
function drawTimeline(){const canvas=$('#timeline');if(!canvas)return;const p=selectedPlayer(),r=canvas.getBoundingClientRect(),dpr=devicePixelRatio||1,w=Math.max(320,r.width),h=270;canvas.width=Math.round(w*dpr);canvas.height=Math.round(h*dpr);const c=canvas.getContext('2d');c.setTransform(dpr,0,0,dpr,0,0);c.clearRect(0,0,w,h);const padL=48,padR=18,plotW=w-padL-padR,x=t=>padL+(t-data.meta.firstTick)/(data.meta.lastTick-data.meta.firstTick)*plotW;c.fillStyle=css('--muted');c.font='11px system-ui';c.textAlign='right';c.fillText('전투',padL-7,27);c.fillText('HP',padL-7,94);c.fillText('피해',padL-7,188);c.strokeStyle=css('--line');c.lineWidth=1;for(let minute=2;minute<=23;minute+=3){const tick=minute*60*60;if(tick<data.meta.firstTick||tick>data.meta.lastTick)continue;const xx=x(tick);c.beginPath();c.moveTo(xx,12);c.lineTo(xx,238);c.stroke();c.fillStyle=css('--muted');c.textAlign='center';c.fillText(`${minute}m`,xx,256)}c.fillStyle=css('--combat');for(const [a,b] of p.combatIntervals)c.fillRect(x(a),17,Math.max(1,x(b)-x(a)),15);const maxHp=Math.max(1,...p.snapshotSeries.map(s=>s[1]||0));c.strokeStyle=css('--hp');c.lineWidth=2;c.beginPath();p.snapshotSeries.forEach((s,i)=>{const yy=142-(s[1]||0)/maxHp*68;i?c.lineTo(x(s[0]),yy):c.moveTo(x(s[0]),yy)});c.stroke();const maxDamage=Math.max(1,...p.timelineBins.flatMap(b=>[b[1],b[2],b[3]]));for(const b of p.timelineBins){const bw=Math.max(1,plotW/p.timelineBins.length-1),xx=x(b[0]);c.fillStyle=css('--dealt');c.fillRect(xx,181-b[1]/maxDamage*36,bw,b[1]/maxDamage*36);c.fillStyle=css('--taken');c.fillRect(xx,184,bw,b[2]/maxDamage*36);if(b[3]){c.fillStyle=css('--heal');c.fillRect(xx,224-b[3]/maxDamage*18,bw,Math.max(1,b[3]/maxDamage*18))}}c.strokeStyle=css('--text');c.lineWidth=1;c.beginPath();c.moveTo(x(state.cursor),8);c.lineTo(x(state.cursor),238);c.stroke();c.fillStyle=css('--text');c.textAlign='center';c.fillText(clock(state.cursor),Math.max(70,Math.min(w-42,x(state.cursor))),11)}
function renderNearEvents(){const box=$('#nearEvents');if(!box)return;const near=eventIndexes().map(i=>data.events[i]).filter(e=>Math.abs(e[0]-state.cursor)<=300).sort((a,b)=>Math.abs(a[0]-state.cursor)-Math.abs(b[0]-state.cursor)).slice(0,14).sort((a,b)=>a[0]-b[0]||a[1]-b[1]);box.innerHTML=near.length?`<div class="table-wrap"><table style="min-width:0"><thead><tr><th>시간</th><th>패킷·값</th></tr></thead><tbody>${near.map(e=>`<tr class="clickable" data-near="${e[1]}"><td class="mono">${clock(e[0])}</td><td><b>${esc(packetLabel(e))}</b><div class="small muted">${esc(summary(e))}</div></td></tr>`).join('')}</tbody></table></div>`:'<div class="empty">±5초 안에 관련 이벤트가 없습니다.</div>';box.querySelectorAll('[data-near]').forEach(row=>row.onclick=()=>{state.selectedEvent=Number(row.dataset.near);state.eventScope='player';state.view='events';state.eventFrom=Math.max(data.meta.firstTick,state.cursor-300);state.eventTo=Math.min(data.meta.lastTick,state.cursor+300);syncTabs();renderContent()})}
function renderSkills(){const p=selectedPlayer();$('#content').innerHTML=`<section class="panel"><h2>스킬 슬롯과 쿨다운 준비 상태</h2><div class="notice small">‘준비 상태 비율’은 쿨다운이 끝나 있었던 관측 전투 시간입니다. 못 쓴 이유(CC·침묵·사거리·대상)는 아직 제외하지 않았습니다.</div><div class="table-wrap" style="margin-top:10px"><table><thead><tr><th>슬롯 계열</th><th class="num">시작</th><th class="num">전투 중 시작</th><th class="num">쿨다운 시작</th><th class="num">평균 시작 CD</th><th class="num">추적 전투</th><th class="num">준비 상태</th><th>경계</th></tr></thead><tbody>${p.cooldowns.map(r=>`<tr><td><b>${r.family}</b></td><td class="num">${r.startCount}</td><td class="num">${r.inCombatStartCount}</td><td class="num">${r.cooldownStarts}</td><td class="num">${r.meanStartCooldownSeconds===null?'—':r.meanStartCooldownSeconds.toFixed(2)+'s'}</td><td class="num">${r.trackedCombatSeconds.toFixed(1)}s</td><td class="num"><b>${r.readyCombatRatio===null?'—':(r.readyCombatRatio*100).toFixed(1)+'%'}</b><div class="small muted">${r.readyCombatSeconds.toFixed(1)}s</div></td><td class="small">${esc(r.caveat)}<div>${badge(r.status)}</div></td></tr>`).join('')}</tbody></table></div></section><section class="panel"><h2>정확한 CmdStartSkill 횟수</h2><p class="muted small">한 행은 wire의 SkillId입니다. 다단·재시전은 합치지 않았고, 패킷 수를 입력 횟수라고 단정하지 않습니다.</p><div class="table-wrap"><table><thead><tr><th>SkillId</th><th>슬롯</th><th class="num">시작</th><th class="num">전투 중</th><th class="num">종료</th><th class="num">액션</th><th>skillCode</th><th>근거</th></tr></thead><tbody>${p.skills.map(r=>`<tr><td><code>${esc(r.skillName)}</code><div class="small muted">${r.skillId}</div></td><td>${r.family}</td><td class="num">${r.startCount}</td><td class="num">${r.inCombatStartCount}</td><td class="num">${r.finishCount}</td><td class="num">${r.actionCount}</td><td>${r.codes.map(c=>`<code>${c.code}</code>×${c.count}`).join('<br>')}</td><td><button class="btn" data-skill-id="${r.skillId}">이벤트 보기</button></td></tr>`).join('')||'<tr><td colspan="8" class="empty">액티브 스킬 시작 없음</td></tr>'}</tbody></table></div></section><section class="panel"><h2>별도 시작 패킷</h2><div class="stats"><article class="stat"><span>기본 공격 시작</span><b>${p.stats.normalAttackStarts}</b><small class="muted">CmdStartNormalAttackSkill</small></article><article class="stat"><span>패시브 트리거</span><b>${p.stats.passiveSkillStarts}</b><small class="muted">CmdStartPassiveSkill</small></article><article class="stat"><span>상태 스킬 시작</span><b>${p.stats.stateSkillStarts}</b><small class="muted">CmdStartStateSkill</small></article><article class="stat"><span>기록된 HP 추가</span><b>${fmt(p.stats.recordedHpAdded)}</b><small class="muted">CmdHeal + HealStateCode</small></article></div></section>`;document.querySelectorAll('[data-skill-id]').forEach(b=>b.onclick=()=>{state.eventScope='player';state.eventType='CmdStartSkill';state.eventQuery=enumLabel('SkillId',Number(b.dataset.skillId));state.eventPage=1;state.view='events';syncTabs();renderContent()})}
function scopedEvents(){return state.eventScope==='all'?data.events:eventIndexes().map(i=>data.events[i])}
function filteredEvents(){let rows=scopedEvents();if(state.eventType!=='all')rows=rows.filter(e=>eventDef(e).packetName===state.eventType);rows=rows.filter(e=>state.eventFrom<=e[0]&&e[0]<=state.eventTo);const q=state.eventQuery.trim().toLowerCase();if(q)rows=rows.filter(e=>(packetLabel(e)+' '+summary(e)+' '+JSON.stringify(eventObject(e).fields)).toLowerCase().includes(q));return rows}
function renderEvents(){const rows=filteredEvents(),pageSize=50,pages=Math.max(1,Math.ceil(rows.length/pageSize));state.eventPage=Math.min(state.eventPage,pages);const visible=rows.slice((state.eventPage-1)*pageSize,state.eventPage*pageSize);let selected=state.selectedEvent?data.events.find(e=>e[1]===state.selectedEvent):visible[0];if(selected&&!rows.includes(selected))selected=visible[0];state.selectedEvent=selected?.[1]??null;const typeNames=[...new Set(scopedEvents().map(e=>eventDef(e).packetName))].sort();$('#content').innerHTML=`<section class="panel"><h2>${state.eventScope==='all'?'전체 '+data.meta.playerCount+'명':'선택 플레이어 관련'} 원본 이벤트</h2><div class="controls"><label class="control">대상 범위<select id="eventScope"><option value="player">선택 플레이어</option><option value="all">전체 ${data.meta.playerCount}명</option></select></label><label class="control">패킷<select id="eventType"><option value="all">전체</option>${typeNames.map(n=>{const d=Object.values(data.eventTypes).find(x=>x.packetName===n);return `<option value="${n}">${esc(d.displayName)} (${n})</option>`}).join('')}</select></label><label class="control">검색<input id="eventQuery" type="search" placeholder="명령, SkillId, objectId, 값" value="${esc(state.eventQuery)}"></label><label class="control">시작 tick<input id="eventFrom" type="number" value="${state.eventFrom}"></label><label class="control">끝 tick<input id="eventTo" type="number" value="${state.eventTo}"></label><button class="btn" id="clearEventFilter">전체 시간</button></div><div class="split"><div><div class="table-wrap"><table><thead><tr><th>시간·순서</th><th>패킷</th><th>해석 가능한 값</th><th>상태</th></tr></thead><tbody>${visible.map(e=>`<tr class="clickable ${e[1]===state.selectedEvent?'selected':''}" data-event="${e[1]}"><td class="mono">${clock(e[0])}<div class="small muted">tick ${e[0]} · #${e[1]}</div></td><td><b>${esc(packetLabel(e))}</b><div class="small muted">packet ${eventDef(e).packetType}</div></td><td>${esc(summary(e))}</td><td>${badge('exact wire',true)} ${inCombat(selectedPlayer(),e[0])?badge('선택 플레이어 전투 상태'):''}</td></tr>`).join('')||'<tr><td colspan="4" class="empty">조건에 맞는 이벤트가 없습니다.</td></tr>'}</tbody></table></div><div class="pager"><span>${fmt(rows.length)}개 · ${state.eventPage}/${pages}</span><span><button class="btn" id="prevPage" ${state.eventPage<=1?'disabled':''}>이전</button> <button class="btn" id="nextPage" ${state.eventPage>=pages?'disabled':''}>다음</button></span></div></div><aside class="detail"><h3>선택 이벤트 구조</h3><p>${selected?`${badge(eventDef(selected).wireStatus,true)} ${badge(data.meta.timeStatus)}`:''}</p><pre id="eventDetail">${selected?esc(JSON.stringify(eventObject(selected),null,2)):'선택된 이벤트 없음'}</pre></aside></div></section>`;$('#eventScope').value=state.eventScope;$('#eventScope').onchange=e=>{state.eventScope=e.target.value;state.eventType='all';state.selectedEvent=null;state.eventPage=1;renderEvents()};$('#eventType').value=state.eventType;$('#eventType').onchange=e=>{state.eventType=e.target.value;state.eventPage=1;renderEvents()};$('#eventQuery').oninput=e=>{state.eventQuery=e.target.value;state.eventPage=1;clearTimeout(window.__eventSearchTimer);window.__eventSearchTimer=setTimeout(renderEvents,120)};$('#eventFrom').onchange=e=>{state.eventFrom=Math.max(data.meta.firstTick,Number(e.target.value));state.eventPage=1;renderEvents()};$('#eventTo').onchange=e=>{state.eventTo=Math.min(data.meta.lastTick,Number(e.target.value));state.eventPage=1;renderEvents()};$('#clearEventFilter').onclick=()=>{state.eventFrom=data.meta.firstTick;state.eventTo=data.meta.lastTick;state.eventPage=1;renderEvents()};$('#prevPage').onclick=()=>{state.eventPage--;renderEvents()};$('#nextPage').onclick=()=>{state.eventPage++;renderEvents()};document.querySelectorAll('[data-event]').forEach(row=>row.onclick=()=>{state.selectedEvent=Number(row.dataset.event);renderEvents()})}
function renderMethod(){const f=data.fixedPointEvidence,s=data.dataSources;$('#content').innerHTML=`<section class="panel"><h2>데이터 출처와 역할</h2><div class="method-grid"><article class="method"><h3>gameDb CDN · 기준 수치</h3><p><code>${esc(s.staticGameData.url)}</code></p><p>리플레이 버전과 같은 ${fmt(s.staticGameData.tableCount)}개 테이블에서 캐릭터 기본·성장 능력치, 스킬 쿨다운·사거리, 아이템 능력치, 모드 보정과 코드 이름을 읽습니다.</p><p>${badge(s.staticGameData.status,true)} ${badge('static/base values')}</p></article><article class="method"><h3>리플레이 종료 결과 · 실제 경기 합계</h3><p><code>CmdFinishGameResult</code> tick ${s.finishGameResult.tick}의 <code>BattleUserGame</code> ${s.finishGameResult.playerCount}명입니다. 킬·어시·사망, 준 피해·받은 피해와 유형별 분해가 들어 있습니다.</p><p>${badge(s.finishGameResult.status,true)} ${badge(`${s.finishGameResult.componentSumChecks}/${data.meta.playerCount} sum checks`)}</p></article><article class="method"><h3>BSER 백엔드 · 취득 경로</h3><p><code>${esc(s.bserBackend.gameResultEndpoint)}</code>는 경기 결과, <code>${esc(s.bserBackend.replayLookupEndpoint)}</code>는 S3 리플레이 경로를 찾는 용도로 취득 코드에서 확인됐습니다.</p><p>${badge(s.bserBackend.status)} ${badge(s.bserBackend.liveCrosscheck)}</p></article><article class="method"><h3>맵 위치 · 리플레이 명령</h3><p>${fmt(data.map.movementAnchorCount)}개 이동·정지·워프·full snapshot 좌표를 시간순으로 연결합니다. 배경 그림은 저장소 reference export라 현재 클라이언트 asset과 별도로 대조해야 합니다.</p><p>${badge(data.map.movementStatus,true)} ${badge(data.map.imageStatus)}</p></article></div></section><section class="panel"><h2>계산과 증거 경계</h2><div class="method-grid"><article class="method"><h3>전투 구간</h3><p><code>CmdUpdateInCombatType</code>에서 <code>NotInCombat(0)</code>이 아닌 구간을 연결했습니다.</p><p>${badge(data.enumStatuses.InCombatType,true)} ${badge('derived interval')}</p></article><article class="method"><h3>개별 피해 타임라인</h3><p><code>CmdDamage.damage</code>가 null이 아닌 패킷만 합산합니다. 대상은 player objectId로 제한하고 projectile/summon 공격자는 선택된 spawn schema의 <code>ownerId</code>를 따라 플레이어에 귀속합니다.</p><p>${badge('decoded nullable damage',true)} ${badge('partial coverage')} ${badge('derived spawn owner')}</p></article><article class="method"><h3>스킬 횟수</h3><p><code>CmdStartSkill</code> 패킷 수입니다. <code>SkillId</code>는 메타데이터, <code>skillCode</code>는 리플레이가 지정한 게임 데이터로 이름을 연결했습니다.</p><p>${badge('exact event count',true)} ${badge('not input-count')}</p></article><article class="method"><h3>쿨다운 준비 상태</h3><p><code>${esc(f.formula)}</code>. 같은 플레이어의 ±5 tick 시작/쿨다운 쌍 ${fmt(f.samePlayerNearTickStartCooldownPairs)}개, 기본 테이블과 정확히 같은 값 ${fmt(f.exactBaseTableCooldownMatches)}개를 교차 확인했습니다.</p><p>${badge(f.status)} ${badge('CC/range unadjusted')}</p></article></div></section><section class="panel"><h2>남은 제한</h2><ol>${data.limitations.map(x=>`<li>${esc(x)}</li>`).join('')}</ol></section><section class="panel"><h2>입력 파일 SHA-256</h2><div class="table-wrap"><table><thead><tr><th>파일</th><th>역할</th><th>SHA-256</th></tr></thead><tbody>${data.sources.map(s=>`<tr><td><code>${esc(s.file)}</code></td><td>${esc(s.role)}</td><td><code>${s.sha256}</code></td></tr>`).join('')}</tbody></table></div><p class="small muted">${esc(data.meta.privacy)}</p></section>`}
function syncTabs(){document.querySelectorAll('[data-view]').forEach(b=>b.classList.toggle('active',b.dataset.view===state.view))}
function drawMapExact(){
 const canvas=$('#mapCanvas');if(!canvas)return;
 const rect=canvas.getBoundingClientRect(),dpr=devicePixelRatio||1,w=Math.max(280,rect.width),h=Math.max(280,Math.min(680,w*data.map.image.h/data.map.image.w));
 canvas.style.height=`${h}px`;canvas.width=Math.round(w*dpr);canvas.height=Math.round(h*dpr);
 const c=canvas.getContext('2d');c.setTransform(dpr,0,0,dpr,0,0);c.clearRect(0,0,w,h);
 if(mapImage.complete&&mapImage.naturalWidth)c.drawImage(mapImage,0,0,w,h);else{c.fillStyle=css('--surface2');c.fillRect(0,0,w,h)}
 const b=data.map.world,X=x=>(x-b.minx)/(b.maxx-b.minx)*w,Y=z=>(b.maxz-z)/(b.maxz-b.minz)*h,onMap=p=>b.minx<=p[0]&&p[0]<=b.maxx&&b.minz<=p[1]&&p[1]<=b.maxz;
 const selected=selectedPlayer(),anchors=selected.movementTrack.filter(p=>state.cursor-900<=p[0]&&p[0]<=state.cursor&&onMap([p[1],p[2]]));
 c.fillStyle=teamColor(selected.teamNumber);c.globalAlpha=.6;for(const p of anchors){c.beginPath();c.arc(X(p[1]),Y(p[2]),1.6,0,Math.PI*2);c.fill()}c.globalAlpha=1;
 const intended=(selected.plannedPathCommands||[]).filter(p=>p[0]<=state.cursor&&state.cursor-p[0]<=600).at(-1);
 if(intended&&intended[2].length>1){c.strokeStyle=teamColor(selected.teamNumber);c.globalAlpha=.85;c.lineWidth=2;c.setLineDash([6,5]);c.beginPath();intended[2].forEach((p,i)=>{if(!onMap(p))return;i?c.lineTo(X(p[0]),Y(p[1])):c.moveTo(X(p[0]),Y(p[1]))});c.stroke();c.setLineDash([]);c.globalAlpha=1}
 canvas.__markers=[];for(const p of data.players){const pos=positionAt(p,state.cursor);if(!pos)continue;const x=X(pos[0]),y=Y(pos[1]),life=lifeAt(p,state.cursor),color=teamColor(p.teamNumber),chosen=p.objectId===state.playerId;c.globalAlpha=life==='dead'?.28:1;c.fillStyle=color;c.beginPath();c.arc(x,y,chosen?8:6,0,Math.PI*2);c.fill();c.strokeStyle=chosen?css('--text'):css('--surface');c.lineWidth=chosen?3:1.5;c.stroke();if(life==='down'){c.strokeStyle=css('--taken');c.lineWidth=2;c.beginPath();c.arc(x,y,11,0,Math.PI*2);c.stroke()}if(life==='dead'){c.strokeStyle=css('--taken');c.lineWidth=2;c.beginPath();c.moveTo(x-6,y-6);c.lineTo(x+6,y+6);c.moveTo(x+6,y-6);c.lineTo(x-6,y+6);c.stroke()}c.globalAlpha=1;if(w>=520||chosen){c.fillStyle=css('--text');c.font=chosen?'500 12px system-ui':'11px system-ui';c.textAlign='center';c.fillText(`${p.characterName} T${p.teamNumber}`,x,Math.max(11,y-11))}canvas.__markers.push({id:p.objectId,x,y})}
 canvas.dataset.markerCount=String(canvas.__markers.length);canvas.setAttribute('aria-label',`${canvas.__markers.length}명 플레이어의 마지막 exact 위치를 보여주는 루미아 섬 지도`);c.fillStyle=css('--text');c.globalAlpha=.85;c.textAlign='left';c.font='500 13px system-ui';c.fillText(`${clock(state.cursor)} · ${selected.characterName} · ${lifeAt(selected,state.cursor)}`,10,20);c.globalAlpha=1;
}
function projectWorldToImage(point){const px=data.map.projection.pixelX,py=data.map.projection.pixelY;return [px[0]*point[0]+px[1]*point[1]+px[2],py[0]*point[0]+py[1]*point[1]+py[2]]}
function drawMapProjected(){
 const canvas=$('#mapCanvas');if(!canvas)return;
 const rect=canvas.getBoundingClientRect(),dpr=devicePixelRatio||1,w=Math.max(280,rect.width),h=Math.max(360,Math.min(960,w*data.map.image.h/data.map.image.w));
 canvas.style.height=`${h}px`;canvas.width=Math.round(w*dpr);canvas.height=Math.round(h*dpr);
 const c=canvas.getContext('2d');c.setTransform(dpr,0,0,dpr,0,0);c.clearRect(0,0,w,h);
 if(mapImage.complete&&mapImage.naturalWidth)c.drawImage(mapImage,0,0,w,h);else{c.fillStyle=css('--surface2');c.fillRect(0,0,w,h)}
 const X=x=>x/data.map.image.w*w,Y=y=>y/data.map.image.h*h,onMap=p=>0<=p[0]&&p[0]<=data.map.image.w&&0<=p[1]&&p[1]<=data.map.image.h;
 const selected=selectedPlayer(),anchors=selected.movementTrack.filter(p=>state.cursor-900<=p[0]&&p[0]<=state.cursor).map(p=>[p[0],...projectWorldToImage([p[1],p[2]])]).filter(p=>onMap([p[1],p[2]]));
 c.fillStyle=teamColor(selected.teamNumber);c.globalAlpha=.6;for(const p of anchors){c.beginPath();c.arc(X(p[1]),Y(p[2]),1.6,0,Math.PI*2);c.fill()}c.globalAlpha=1;
 const intended=(selected.plannedPathCommands||[]).filter(p=>p[0]<=state.cursor&&state.cursor-p[0]<=600).at(-1);
 if(intended&&intended[2].length>1){const projected=intended[2].map(projectWorldToImage);c.strokeStyle=teamColor(selected.teamNumber);c.globalAlpha=.85;c.lineWidth=2;c.setLineDash([6,5]);c.beginPath();let started=false;for(const p of projected){if(!onMap(p))continue;if(started)c.lineTo(X(p[0]),Y(p[1]));else{c.moveTo(X(p[0]),Y(p[1]));started=true}}if(started)c.stroke();c.setLineDash([]);c.globalAlpha=1}
 canvas.__markers=[];for(const p of data.players){const pos=positionAt(p,state.cursor);if(!pos)continue;const pixel=projectWorldToImage(pos);if(!onMap(pixel))continue;const x=X(pixel[0]),y=Y(pixel[1]),life=lifeAt(p,state.cursor),color=teamColor(p.teamNumber),chosen=p.objectId===state.playerId;c.globalAlpha=life==='dead'?.28:1;c.fillStyle=color;c.beginPath();c.arc(x,y,chosen?8:6,0,Math.PI*2);c.fill();c.strokeStyle=chosen?css('--text'):css('--surface');c.lineWidth=chosen?3:1.5;c.stroke();if(life==='down'){c.strokeStyle=css('--taken');c.lineWidth=2;c.beginPath();c.arc(x,y,11,0,Math.PI*2);c.stroke()}if(life==='dead'){c.strokeStyle=css('--taken');c.lineWidth=2;c.beginPath();c.moveTo(x-6,y-6);c.lineTo(x+6,y+6);c.moveTo(x+6,y-6);c.lineTo(x-6,y+6);c.stroke()}c.globalAlpha=1;if(w>=520||chosen){c.fillStyle=css('--text');c.font=chosen?'500 12px system-ui':'11px system-ui';c.textAlign='center';c.fillText(`${p.characterName} T${p.teamNumber}`,x,Math.max(11,y-11))}canvas.__markers.push({id:p.objectId,x,y})}
 canvas.dataset.markerCount=String(canvas.__markers.length);canvas.setAttribute('aria-label',`${canvas.__markers.length}명 플레이어의 마지막 exact 위치를 Satellite Map에 affine 투영한 지도`);c.fillStyle=css('--text');c.globalAlpha=.85;c.textAlign='left';c.font='500 13px system-ui';c.fillText(`${clock(state.cursor)} · ${selected.characterName} · ${lifeAt(selected,state.cursor)}`,10,20);c.globalAlpha=1;
}
drawMap=drawMapProjected;
const renderSupportedMap=renderMap;
renderMap=function(){if(data.map.playbackStatus==='unavailable-mode-specific-map-and-bounds'){$('#content').innerHTML=`<section class="panel"><h2>${esc(data.meta.matchModeLabel)} 맵 재생</h2><div class="notice"><b>이 모드의 지도 재생은 현재 제공하지 않습니다.</b><p>검증된 코발트 전용 지도 이미지와 좌표 경계가 없어 루미아 섬 지도로 대체하지 않았습니다. 경기 결과·전투 타임라인·스킬·이벤트 분석은 그대로 확인할 수 있습니다.</p><p>${badge(data.map.playbackStatus)} ${badge('fallbackUsed: false')}</p></div></section>`;return}renderSupportedMap()};
function renderMovementBoundaryNotice(){if(state.view!=='map'||data.map.playbackStatus==='unavailable-mode-specific-map-and-bounds')return;const p=selectedPlayer(),notice=$('#content .panel .notice');if(!notice)return;notice.textContent=`마커는 마지막 exact 위치를 다음 관측까지 유지하며 앵커 사이를 직선 이동으로 만들지 않습니다. 작은 점은 최근 exact 앵커, 점선은 CmdMoveToDestination corners가 제공한 의도 경로입니다. ${fmt(p.plannedPathCommandCount)}개 경로 명령·${fmt(p.plannedPathNodeCount)}개 노드에는 노드별 도달 tick이 없습니다. 배경은 사용자가 제공한 Satellite Map이며 world X/Z를 검증 affine 식으로 772×1000 원본 픽셀에 투영합니다. exact 12.2 client asset authority는 아닙니다.`}
function renderCompatibilityNotice(){if(state.view!=='method')return;const host=$('#content');if(!host)return;const section=document.createElement('section');section.className='panel';section.innerHTML=`<h2>파일·모드 호환성</h2><p>${badge(data.meta.clientVersion,true)} ${badge(data.meta.matchModeLabel,true)} ${badge(data.meta.compatibility.matchMode.status,true)} ${badge('fallbackUsed: false')}</p><p class="small muted">matchingMode ${data.meta.matchingMode} · matchingTeamMode ${data.meta.matchingTeamMode}. 지원 목록에 없는 패치나 모드 조합은 기존 12.2 스키마로 해석하지 않습니다.</p>`;host.insertBefore(section,host.firstChild)}
function renderDamageCoverageNotice(){if(state.view!=='overview')return;const p=selectedPlayer(),c=p.stats.damageTimelineCoverage?.outgoing,host=$('#content .panel');if(!c||!host)return;const note=document.createElement('div');note.className='notice small';note.textContent=`개별 숫자가 있는 CmdDamage는 준 피해 ${fmt(c.numericFieldCount)}/${fmt(c.packetCount)}건이며 숫자 합은 ${fmt(c.knownTimelineDamage)}입니다. 경기 종료 총 준 피해 ${fmt(c.exactFinishDamage)}는 exact지만, 종료 총피해와 타임라인 합의 차이는 ${fmt(c.unattributedResidual)}이며 이를 null 패킷이나 특정 스킬에 임의 배분하지 않습니다.`;host.insertBefore(note,host.children[1]||null)}
function renderSkillDamageBoundaryNotice(){if(state.view!=='skills')return;const p=selectedPlayer(),a=p.skillDamageAttribution,host=$('#content .panel');if(!a||!host)return;const note=document.createElement('div');note.className='notice small';note.textContent=`종료 결과에서 확인되는 정확한 스킬 계열 총피해는 ${fmt(a.exactSkillCategoryDamage)}입니다. CmdDamage에는 skillCode가 없고 damage 값 대부분이 null이므로 아래 스킬 시작 횟수에 피해를 나눠 붙이지 않으며, 개별 스킬 총피해는 계산 불가로 유지합니다.`;host.insertBefore(note,host.children[1]||null)}
function renderContent(){stopPlay();if(resizeObserver)resizeObserver.disconnect();if(state.view==='map')renderMap();else if(state.view==='overview')renderOverview();else if(state.view==='result')renderResult();else if(state.view==='analysis')renderAnalysis();else if(state.view==='skills')renderSkills();else if(state.view==='events')renderEvents();else renderMethod();renderMovementBoundaryNotice();renderDamageCoverageNotice();renderSkillDamageBoundaryNotice();renderCompatibilityNotice()}
function render(){renderStats();syncTabs();renderContent()}
document.querySelectorAll('[data-view]').forEach(b=>b.onclick=()=>{state.view=b.dataset.view;state.session=null;syncTabs();renderContent()});
renderPlayerOptions();render();
</script>
</body>
</html>'''


def render_html(catalog: dict) -> str:
    compact = json.dumps(catalog, ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/")
    return (
        HTML_TEMPLATE
        .replace("__GAME_ID__", str(catalog["meta"]["gameId"]))
        .replace("__CLIENT_VERSION__", catalog["meta"]["clientVersion"])
        .replace("__MATCH_MODE__", catalog["meta"].get("matchModeLabel", "mode unavailable"))
        .replace("__DATA__", compact)
        .replace(
            "위치는 이동·정지·워프 명령과 full snapshot의 정확한 좌표를 사용하고, "
            "명령 사이만 선형 보간합니다.",
            "마커는 마지막 exact 위치를 다음 관측까지 유지하며 앵커 사이의 이동을 "
            "만들지 않습니다.",
        )
        .replace(
            "개 이동·정지·워프·full snapshot 좌표를 시간순으로 연결합니다.",
            "개 exact 위치 앵커를 보존하고 마지막 관측 위치를 다음 관측까지 유지합니다.",
        )
        .replace(
            "배경 지도는 저장소 reference export라 ${esc(data.meta.clientVersion)} asset "
            "해시는 아직 대조되지 않았습니다.",
            "배경은 legacy reference render이며 exact 12.2 client image asset이 아닙니다.",
        )
        .replace(
            "배경 그림은 저장소 reference export라 현재 클라이언트 asset과 별도로 대조해야 합니다.",
            "배경 그림은 위치 이해용 reference render이며 exact client asset authority가 아닙니다.",
        )
    )


def exact_tick_slices(events):
    """Reuse sorted tick positions; unordered input keeps its original scan order."""
    from bisect import bisect_left
    ticks = [event[0] for event in events]
    ordered = all(type(tick) is int for tick in ticks) and all(
        a <= b for a, b in zip(ticks, ticks[1:]))
    if not ordered:
        return lambda left, right: [e for e in events if left <= e[0] < right]
    return lambda left, right: events[bisect_left(ticks, left):bisect_left(ticks, right)]


def run_analysis(
    config: AnalysisConfig,
    *,
    write_private_html: bool = True,
    timing_sink: dict[str, float] | None = None,
) -> dict:
    config.out_dir.mkdir(parents=True, exist_ok=True)
    catalog_started = time.perf_counter()
    catalog = build_catalog(config)
    if timing_sink is not None:
        timing_sink["privateCatalogBuild"] = round(
            time.perf_counter() - catalog_started, 3
        )
    json_started = time.perf_counter()
    config.json_path.write_text(
        json.dumps(catalog, ensure_ascii=False, separators=(",", ":")),
        encoding="utf-8",
    )
    if timing_sink is not None:
        timing_sink["privateJsonWrite"] = round(
            time.perf_counter() - json_started, 3
        )
    if write_private_html:
        html_started = time.perf_counter()
        config.html_path.write_text(render_html(catalog), encoding="utf-8")
        if timing_sink is not None:
            timing_sink["privateHtmlWrite"] = round(
                time.perf_counter() - html_started, 3
            )
    return catalog


def parse_args(argv: list[str] | None = None) -> AnalysisConfig:
    parser = argparse.ArgumentParser(
        description="Build one evidence-drillable Eternal Return replay analysis"
    )
    parser.add_argument("--game-id", required=True, type=int)
    parser.add_argument("--replay", required=True, type=Path)
    parser.add_argument("--inspect", required=True, type=Path)
    parser.add_argument("--enum-catalog", required=True, type=Path)
    parser.add_argument("--spawn-snapshots", required=True, type=Path)
    parser.add_argument("--game-data", required=True, type=Path)
    parser.add_argument("--out-dir", required=True, type=Path)
    parser.add_argument("--requested-skill-cache",type=Path)
    parser.add_argument("--requested-skill-player-map",type=Path)
    parser.add_argument("--schema", type=Path, default=ROOT / "schema" / "schema.json")
    parser.add_argument(
        "--map-image",
        type=Path,
        default=ROOT / "data" / "satellite_map.png",
    )
    parser.add_argument("--map-meta", type=Path, default=ROOT / "data" / "map_meta.json")
    parser.add_argument("--names", type=Path, default=ROOT / "data" / "names.json")
    parser.add_argument(
        "--names-supplement",
        type=Path,
        default=ROOT / "data" / "character_names_supplement.json",
    )
    parser.add_argument(
        "--source-root",
        type=Path,
        default=ROOT,
        help="root used only for non-secret source labels in derived output",
    )
    parser.add_argument(
        "--replay-source-label",
        default=None,
        help="optional non-secret logical label instead of the temporary replay path",
    )
    args = parser.parse_args(argv)
    return AnalysisConfig(
        game_id=args.game_id,
        replay_path=args.replay.resolve(),
        inspect_path=args.inspect.resolve(),
        enum_path=args.enum_catalog.resolve(),
        spawn_path=args.spawn_snapshots.resolve(),
        game_data_path=args.game_data.resolve(),
        out_dir=args.out_dir.resolve(),
        schema_path=args.schema.resolve(),
        map_image_path=args.map_image.resolve(),
        map_meta_path=args.map_meta.resolve(),
        names_path=args.names.resolve(),
        names_supplement_path=args.names_supplement.resolve(),
        source_root=args.source_root.resolve(),
        replay_source_label=args.replay_source_label,
        requested_skill_cache_path=args.requested_skill_cache.resolve() if args.requested_skill_cache else None,
        requested_skill_identity_path=args.requested_skill_player_map.resolve() if args.requested_skill_player_map else None,
    )


def main(argv: list[str] | None = None) -> None:
    config = parse_args(argv)
    catalog = run_analysis(config)
    print(json.dumps({
        "html": str(config.html_path),
        "htmlBytes": config.html_path.stat().st_size,
        "json": str(config.json_path),
        "jsonBytes": config.json_path.stat().st_size,
        "players": len(catalog["players"]),
        "selectedEvents": len(catalog["events"]),
        "decodedCounts": catalog["packetDecodedCounts"],
        "fixedPointEvidence": catalog["fixedPointEvidence"],
    }, ensure_ascii=False))


if __name__ == "__main__":
    main()
