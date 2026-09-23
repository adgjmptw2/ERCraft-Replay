#!/usr/bin/env python3
"""Build a public, anonymous projection of a verified private combat analysis.

The private catalog is the evidence drill-down artifact and deliberately keeps
wire identities.  This module never edits it.  It emits a separate whitelist-
only document without exact match/account/object identifiers, source files,
hashes, timestamps, or raw event packets.
"""

from __future__ import annotations

import argparse
import base64
from collections import defaultdict
from copy import deepcopy
import hashlib
import json
import math
from pathlib import Path
import re
import secrets
from typing import Any

try:
    from .build_combat_analysis import (
        EVENT_SPECS,
        SIMULTANEOUS_ENTRY_WINDOW_SECONDS,
        normalize_simultaneous_combat_entries,
    )
    from .character_capabilities import require_official_game_data_url
    from .export_delta_events import classify_packet
    from .map_combat_hud import attach_map_combat_hud
    from .scene_coaching import attach_scene_coaching
except ImportError:
    from build_combat_analysis import (
        EVENT_SPECS,
        SIMULTANEOUS_ENTRY_WINDOW_SECONDS,
        normalize_simultaneous_combat_entries,
    )
    from character_capabilities import require_official_game_data_url
    from export_delta_events import classify_packet
    from map_combat_hud import attach_map_combat_hud
    from scene_coaching import attach_scene_coaching


ROOT = Path(__file__).resolve().parent.parent
PUBLIC_FORMAT = "er-replay-public-combat-analysis.v1"
DEFAULT_DELETION_CONTACT_URL = "https://ercraft.net/contact"
REPORT_ID_PATTERN = re.compile(r"^report-[a-f0-9]{16,64}$")
WILDLIFE_PROVENANCE_PATH = ROOT / "data" / "wildlife.provenance.json"
CHARACTER_PROVENANCE_PATH = ROOT / "data" / "characters.provenance.json"
ITEM_PROVENANCE_PATH = ROOT / "data" / "items.provenance.json"
NAMES_PATH = ROOT / "data" / "names.json"
SKILL_PROVENANCE_PATH = ROOT / "data" / "skills.provenance.json"
UTILITY_SKILL_PROVENANCE_PATH = ROOT / "data" / "utility-skills.provenance.json"
MAP_MARKER_PROVENANCE_PATH = (
    ROOT / "deliverables" / "map-marker-icon-draft" / "provenance.json"
)
RESTRICTION_AREA_PATHS_PATH = ROOT / "data" / "lumia_restriction_areas.json"
RIFT_MAP_CALIBRATION_BY_CLIENT_VERSION = {
    "12.2.0": {
        "statusVersion": "12.2",
        "roomCenters": ((-188.0, 397.0), (-8.0, 397.0)),
        "pixelOrigin": (178.0, 178.0),
        "pixelPerWorldUnit": 3.48,
        "image": {"w": 363, "h": 364},
        "validationStatus": (
            "validated-against-exact-12.2-command-anchors-and-"
            "hash-verified-client-map"
        ),
    },
    "12.4.0": {        "statusVersion": "12.4",        "roomCenters": ((-188.0, 397.0), (-8.0, 397.0)),        "pixelOrigin": (178.0, 178.0),        "pixelPerWorldUnit": 3.48,        "image": {"w": 363, "h": 364},        "validationStatus": "validated-against-byte-identical-12.4-native-map-and-exact-command-anchors",    },    "12.3.0": {
        "statusVersion": "12.3",
        "roomCenters": ((-188.0, 397.0), (-8.0, 397.0)),
        "pixelOrigin": (178.0, 178.0),
        "pixelPerWorldUnit": 3.48,
        "image": {"w": 363, "h": 364},
        "validationStatus": (
            "validated-against-exact-12.3-command-anchors-and-"
            "hash-verified-client-map"
        ),
    },
}
GADGET_POINT_ICON_DATA_URL = (
    "data:image/png;base64,"
    "iVBORw0KGgoAAAANSUhEUgAAABcAAAAVCAIAAAAigOL8AAAEVUlEQVR4AVRUXWxURRQ+"
    "c+b+dW/Z7kK7LYSKtpVSCt2FaqVQf5qYpiQmxtpEH7Ax0RcfTHzz1cSo774YjQ9Eow8+"
    "iSRqE0jAtpBa0VIE2kILCbZKYQvsz91779yZ8dzdttHNt3Pmnjnnm3O/OXNxb1e2c1+W"
    "xrbubGs2R2g7mHuyO9fVnd13INvek308l32sN7fncK4jl+vsyXUdjEeat+c2PD3dOVRMa"
    "VQ0AiisgibAFPznxzTg/1Fb1DUDgIKrgCthRBojSyoCUWhiQc2Y5kwboG0JlgRTAldAjJ"
    "u5oBhECAEHRB07aaRlGmsgF0UQyFnLJD85NQOJMShJIyMPxRARak+kmNNgbjO1qTgn2Gj"
    "7xQoAbiEIAsVZEaISVZ0whW2EoLQmKiqCwgBTiR0WuCCsUNsPuOXVJx+EynHTmqRgtBn"
    "Q/oJpD1XBVJVt1pr285GnDIw0iampWIpCk9WpyJG8Hht39r85NvTeu36q0cO4LsYYMK6"
    "QW9uT60x4SevtD94/8OJAASMPZKSUoZmpgPTCSuAXo0C6jtO669lXRqYWbg6MjDZ27ve"
    "5LZilGArEh2DsPfr84OiJn6dnR956p6mt0wuFZVgMYr1JMvSx5Nf5901x/MTrE1O/prf"
    "vPDQ0FLU0N7R1seSOgi/ATe/pG3zotPQPj5V186X5/ODwa8m67dVTZaQOKYSeKJehbGa"
    "SbktmdnbumSPHfjh39tDg4PCro6WKsCxHavbyG2ON7fvPTv7RP3D82vydtvZuGWopRCxb"
    "9Y+GTRKg25R2Utvu5fMJ1729cqetq2Py4iSjhgiF9v3x8Z9eGHzu2tUrzU07Fm8sZjKZ"
    "SAjGsMoAwBSmGlKmYTiOQ2ohxgvFYjHhJpZvLUoQpsN8UV64ucA4eH6Zm7xSKAgRV2Fw"
    "DgDUL/GBaylRMm/9UVQoE1cYhg2J+kI+fyDbHbAgsCTWG/0DfcVKIbO7KQSRad1lmqZt"
    "20RBolTBkDoKAlFaWXPBeGJ369LijWNP9U2On3E1S3AuRIgc6hk7f/r7o32Hrl+9fLCn"
    "Z3V1lRtGJCUR1YBSRDZwuFdYvPBbX+/TMzMzR/ZnV+cWZk6PJ0qBi4YslM59+525dn9v"
    "Y+OlqfNdHe3T09O1ZM1qlhpUKRtZ2rImTp3qSCd7Wpq/+PjD4q0lF8CmICHrNBaXb6/N"
    "Xjn5yUcv9R5u0tH1ixdAhBxjEUkUkgapNtAyKOe9f5Ymvvx8n6oYK8t6fRUiT2pN18WU"
    "kLZMvr5e+XM++ffdXz77NHH3LzsKNsqomiqfposa2Sr8/cz4+Ddfs0f5lM3pm0DKIXBqT"
    "RLIZbouCH786uTq3GXLoyulKJ2qALq0GpHejUAuQjKZLJdKNf2lop6keun06UwZyezUGf"
    "TFkTKkyC1UiYhpywHg+75hmpv5Gwt0FiE1CFO+71X8Ej1uLGyYuCgEXcWGC5AxEca70W"
    "TTF1t6OzK1fqUia6CmI6dm8C8AAAD//1W9Wu8AAAAGSURBVAMAPSEriW+4oSgAAAAASUVORK5CYII="
)


AREA_UI = {
    "combat": ("전투", "피해·회복·방어·CC·다운·처치·부활"),
    "skill": ("스킬", "일반 공격·스킬 시작과 종료·액션·쿨다운"),
    "movement": ("이동·동선", "이동·정지·워프·하이퍼루프·경로 명령"),
    "state-stat": ("상태·성장", "레벨·경험치·숙련도·상태 효과·보호막"),
    "inventory-item": ("운영·유틸", "아이템·장비·키오스크·콘솔·제작"),
    "world-object": ("시야·오브젝트", "시야·부시·생성·파괴·보스·균열·구역"),
    "communication-ui": ("소통·핑", "핑·시스템 메시지·감정표현·경기 알림"),
    "system-other": ("시스템", "랭킹·휴식·접속 상태·기타 시스템 명령"),
}

AREA_ORDER = (
    "combat",
    "skill",
    "movement",
    "state-stat",
    "inventory-item",
    "world-object",
    "communication-ui",
    "system-other",
)

WILDLIFE_MARKER_COLORS = {
    "chicken": "#ffd84d",
    "bat": "#ff4d5a",
    "boar": "#8b5a3c",
    "wild-dog": "#111318",
    "wolf": "#f3f5f7",
    "bear": "#ff9f43",
    "raven": "#63d4ff",
}
UNKNOWN_WILDLIFE_MARKER_COLOR = "#8b8f98"


PACKET_LABELS = {
    "CmdDamage": "피해",
    "CmdHeal": "회복",
    "CmdHealStateCode": "상태 기반 회복",
    "CmdDead": "사망",
    "CmdKill": "처치",
    "CmdBlock": "피해 방어",
    "CmdCrowdControl": "군중 제어",
    "CmdUpdateTeamKillCount": "팀 처치 수 갱신",
    "CmdDyingCondition": "다운",
    "CmdResurrection": "부활",
    "CmdStartSkill": "스킬 시작",
    "CmdFinishSkill": "스킬 종료",
    "CmdStartNormalAttackSkill": "일반 공격 시작",
    "CmdStartStateSkill": "상태 스킬 시작",
    "CmdFinishStateSkill": "상태 스킬 종료",
    "CmdStartCharacterSkillCooldown": "스킬 재사용 대기 시작",
    "CmdPlaySkillAction": "스킬 동작",
    "CmdPlaySkillActionWithTargets": "대상 지정 스킬 동작",
    "CmdMoveToDestination": "목적지 이동",
    "CmdStopMove": "이동 정지",
    "CmdWarpTo": "워프",
    "CmdLookAtSmoothly": "방향 전환",
    "CmdUpdateMoveSpeedWhenMoving": "이동 중 속도 변경",
    "CmdUpdateMoveSpeed": "이동 속도 변경",
    "CmdLookAtInstance": "대상 바라보기",
    "CmdLockRotation": "회전 고정",
    "CmdMoveStraightWithoutNav": "직선 이동",
    "CmdSetExtraPoint": "추가 자원 변경",
    "CmdAddState": "상태 효과 추가",
    "CmdRemoveState": "상태 효과 제거",
    "CmdUpdateStat": "능력치 갱신",
    "CmdChangeVFCredit": "크레딧 변경",
    "CmdBroadcastUpdateStat": "능력치 공유 갱신",
    "CmdResetCreateTimeState": "상태 시간 갱신",
    "CmdUpdateState": "상태 효과 갱신",
    "CmdItemBoxAdd": "아이템 상자 추가",
    "CmdItemBoxRemove": "아이템 상자 제거",
    "CmdUpdateEquipment": "장비 변경",
    "CmdUpdateInventoryForObserver": "관전자용 인벤토리 갱신",
    "CmdObserverNotifyItem": "관전자용 아이템 알림",
    "CmdCancelConsoleAction": "콘솔 사용 취소",
    "CmdSupplyItemBoxSetCanOpen": "보급 상자 개방 상태",
    "CmdKioskInteract": "키오스크 상호작용",
    "CmdConsoleAction": "콘솔 사용",
    "CmdSpawn": "오브젝트 생성",
    "CmdDestroy": "오브젝트 파괴",
    "CmdDestroyDelayStart": "오브젝트 파괴 대기",
    "CmdProjectileArrived": "투사체 도착",
    "CmdProjectileCollision": "투사체 충돌",
    "CmdAddSubSight": "추가 시야 생성",
    "CmdInSightRange": "시야 진입",
    "CmdInBush": "부시 진입",
    "CmdOutBush": "부시 이탈",
    "CmdPing": "전술 핑",
    "CmdSystemChat": "시스템 핑·메시지",
    "CmdGameAnnounce": "경기 알림",
    "CmdEmotionIcon": "감정표현",
    "CmdNoise": "소음 표시",
    "CmdUpdateMapIcon": "맵 아이콘 갱신",
    "CmdUsingSystemNotify": "시스템 사용 알림",
    "CmdStartCommonEmotionDance": "감정표현 춤 시작",
    "CmdRanking": "순위 갱신",
    "CmdRest": "휴식",
    "CmdReady": "준비 완료",
    "CmdUserDisconnected": "연결 해제",
    "CmdBattleResultKey": "전투 결과 확인",
    "CmdReduceObjectTimeForCheat": "시스템 시간 보정",
}


TACTICAL_PING_TYPES = (
    (1, "이동"),
    (2, "경고"),
    (3, "탈출"),
    (4, "도움 요청"),
    (5, "합류"),
    (6, "적 시야"),
    (7, "시야 필요"),
    (8, "올인"),
    (9, "대상 지정"),
    (10, "선택"),
    (11, "하이퍼루프"),
    (12, "부활"),
    (13, "후퇴"),
    (14, "생존자 하이퍼루프"),
    (15, "연구소 생존자 하이퍼루프"),
)
TACTICAL_PING_LABELS = dict(TACTICAL_PING_TYPES)
TACTICAL_PING_ENUM_NAMES = {
    1: "Run",
    2: "Warning",
    3: "Escape",
    4: "Help",
    5: "LetsJoin",
    6: "EnemyVision",
    7: "NeedVision",
    8: "AllIn",
    9: "Target",
    10: "Select",
    11: "HyperLoop",
    12: "Resurrection",
    13: "FallBack",
    14: "PingHyperLoopSurvivor",
    15: "PingSecretLaboratoryHyperloopSurvivor",
}
TACTICAL_PING_ASSET_KEYS = {
    1: "tactical-ping-run",
    2: "tactical-ping-warning",
    3: "tactical-ping-escape",
    4: "tactical-ping-help",
    5: "tactical-ping-lets-join",
    6: "tactical-ping-enemy-vision",
    7: "tactical-ping-need-vision",
    8: "tactical-ping-all-in",
    9: "tactical-ping-target",
    10: "tactical-ping-select",
    13: "tactical-ping-fallback",
}
HYPERLOOP_TACTICAL_PING_TYPES = {11, 14, 15}
TACTICAL_PING_DISPLAY_SECONDS = 5
MOVEMENT_PING_DISPLAY_SECONDS = 5
TEAM_COMBAT_LOG_DISPLAY_SECONDS = 8
MOVEMENT_PING_LABELS = {
    "HyperLoopExit": "하이퍼루프 도착",
    "VLSLanding": "VLS 착지",
}

BORI_GRADE_ASSET_KEYS = {
    3: "bori-rare",
    4: "bori-epic",
    5: "bori-legend",
    6: "bori-mythic",
}
BORI_GRADE_LABELS = {
    3: "희귀",
    4: "영웅",
    5: "전설",
    6: "초월",
}


def merge_retained_bori_supply_box_evidence(
    private_catalog: dict[str, Any],
    spawn_report: dict[str, Any],
    *,
    spawn_report_sha256: str,
) -> dict[str, Any]:
    """Upgrade an older retained private catalog with exact Bori grade rows.

    Older 12.2 private catalogs predate ObjectType 56 projection, while their
    separately retained spawn report still contains the complete 13-byte
    BoriSupplyBoxSnapshot payload. This path accepts only hashes already
    recorded by the private catalog and never guesses a missing grade.
    """
    if private_catalog.get("meta", {}).get("clientVersion") != spawn_report.get(
        "clientVersion"
    ):
        raise ValueError("retained spawn report client version does not match")
    sources = private_catalog.get("sources")
    if not isinstance(sources, list):
        raise ValueError("private catalog has no retained source manifest")
    replay_hashes = {
        row.get("sha256")
        for row in sources
        if isinstance(row, dict) and row.get("role") == "wire source; not embedded"
    }
    if spawn_report.get("sourceSha256") not in replay_hashes:
        raise ValueError("retained spawn report replay hash does not match")
    spawn_report_hashes = {
        row.get("sha256")
        for row in sources
        if isinstance(row, dict)
        and row.get("role") == "spawn semantic candidate/owner evidence"
    }
    if spawn_report_sha256 not in spawn_report_hashes:
        raise ValueError("retained spawn report file hash does not match")
    object_rows = [
        row
        for row in spawn_report.get("objectTypes", [])
        if row.get("objectType") == 56
    ]
    if len(object_rows) != 1:
        raise ValueError("retained spawn report has no unique ObjectType 56 row")
    object_row = object_rows[0]
    samples = object_row.get("samples")
    if (
        object_row.get("objectTypeName") != "BoriSupplyBox"
        or object_row.get("semanticCandidate") != "BoriSupplyBoxSnapshot"
        or object_row.get("semanticAllObserved") is not True
        or object_row.get("semanticExactCount") != object_row.get("occurrenceCount")
        or not isinstance(samples, list)
        or len(samples) != object_row.get("occurrenceCount")
    ):
        raise ValueError("retained BoriSupplyBoxSnapshot evidence is not exact")
    upgraded = deepcopy(private_catalog)
    world_map = upgraded.get("worldMap")
    if not isinstance(world_map, dict) or not isinstance(
        world_map.get("staticObjects"), list
    ):
        raise ValueError("private catalog has no world-map static object list")
    existing_by_id = {
        row.get("objectId"): row
        for row in world_map["staticObjects"]
        if row.get("objectType") == 56
    }
    for sample in samples:
        if (
            not isinstance(sample, dict)
            or not isinstance(sample.get("tick"), int)
            or not isinstance(sample.get("objectId"), int)
            or sample.get("nestedBytes") != 13
            or not isinstance(sample.get("nestedHexPrefix"), str)
        ):
            raise ValueError("retained BoriSupplyBoxSnapshot sample is malformed")
        try:
            payload = bytes.fromhex(sample["nestedHexPrefix"])
        except ValueError as error:
            raise ValueError(
                "retained BoriSupplyBoxSnapshot hex is malformed"
            ) from error
        if len(payload) != 13 or payload[0] != 3:
            raise ValueError("retained BoriSupplyBoxSnapshot payload is incomplete")
        open_player_count = int.from_bytes(payload[1:5], "little", signed=True)
        capacity = int.from_bytes(payload[5:9], "little", signed=True)
        grade = int.from_bytes(payload[9:13], "little", signed=True)
        if open_player_count not in {-1, 0} or capacity < 0:
            raise ValueError("retained BoriSupplyBoxSnapshot layout is unsupported")
        if grade not in BORI_GRADE_ASSET_KEYS:
            raise ValueError(f"unsupported exact Bori box grade: {grade}")
        exact_row = {
            "objectId": sample["objectId"],
            "objectType": 56,
            "category": "bori-supply-box",
            "firstSeenTick": sample["tick"],
            "destroyTick": None,
            "position": None,
            "positionStatus": "unavailable-no-position-evidence",
            "capacity": capacity,
            "boxGrade": grade,
            "wireStatus": "decoded-exact-retained-BoriSupplyBoxSnapshot-evidence",
        }
        existing = existing_by_id.get(sample["objectId"])
        if existing is not None:
            if any(
                existing.get(key) != exact_row[key]
                for key in ("firstSeenTick", "boxGrade", "category", "objectType")
            ):
                raise ValueError(
                    "retained BoriSupplyBoxSnapshot conflicts with private catalog"
                )
            continue
        world_map["staticObjects"].append(exact_row)
    world_map["staticObjects"].sort(
        key=lambda row: (row.get("firstSeenTick", -1), row.get("objectId", -1))
    )
    return upgraded


RESULT_FIELDS = (
    "characterLevel",
    "gameRank",
    "teamKill",
    "playerDeaths",
    "playerKill",
    "playerAssistant",
    "masteryLevel",
    "bestWeapon",
    "bestWeaponLevel",
    "duration",
    "craftUncommon",
    "craftRare",
    "craftEpic",
    "craftLegend",
    "damageToPlayer",
    "damageToPlayer_trap",
    "damageToPlayer_basic",
    "damageToPlayer_skill",
    "damageToPlayer_itemSkill",
    "damageToPlayer_direct",
    "damageToMonster_trap",
    "damageToMonster_basic",
    "damageToMonster_skill",
    "damageToMonster_itemSkill",
    "damageToMonster_direct",
    "killMonsters",
    "monsterKill",
    "damageFromPlayer",
    "damageFromPlayer_trap",
    "damageFromPlayer_basic",
    "damageFromPlayer_skill",
    "damageFromPlayer_itemSkill",
    "damageFromPlayer_direct",
    "damageFromMonster",
    "viewContribution",
    "addTelephotoCamera",
    "removeTelephotoCamera",
    "useHyperLoop",
    "useSecurityConsole",
    "useReconDrone",
    "useEmpDrone",
    "useOrb",
    "removeOrb",
    "teamRecover",
    "protectAbsorb",
    "tacticalSkillGroup",
    "tacticalSkillLevel",
    "totalGainVFCredit",
    "totalUseVFCredit",
    "clutchCount",
    "totalFieldKill",
    "elimination",
    "downCanNotEliminate",
    "repeatDownCanNotEliminate",
    "downCanEliminate",
    "repeatDownCanEliminate",
    "useGadget",
    "getBoriReward",
    "restrictedAreaDeathWithoutBattle",
)


FORBIDDEN_KEYS = {
    "gameid",
    "userid",
    "usernum",
    "objectid",
    "nickname",
    "usernickname",
    "usertempname",
    "killername",
    "startdtm",
    "dataindex",
    "sourcefile",
    "sha256",
    "sources",
    "events",
    "eventtypes",
    "playereventindexes",
    "objectowners",
    "objectownerstatuses",
}


def new_report_id() -> str:
    return f"report-{secrets.token_hex(12)}"


def _copy_result(private_result: dict[str, Any]) -> dict[str, Any]:
    return {
        field: deepcopy(private_result[field])
        for field in RESULT_FIELDS
        if field in private_result
    }


def _exact_skill_family(family: str, skill_code: int | None) -> str:
    """Normalize weapon actives from exact replay-version skill codes."""
    if isinstance(skill_code, int) and 3_000_000 <= skill_code < 4_000_000:
        return "WeaponSkill"
    return family


PURPLE_OR_HIGHER_ITEM_GRADES = {"Epic", "Legend", "Mythic"}


def _derive_purple_build_tick(
    equipment_timeline: list[list], item_catalog: dict[str, dict[str, Any]]
) -> int | None:
    equipment: dict[int, int] = {}
    for tick, updates, *_ in equipment_timeline:
        for slot, code, amount in updates:
            if code is None or amount == 0:
                equipment.pop(slot, None)
                continue
            item = item_catalog.get(str(code))
            if item is None:
                raise ValueError("equipment item is missing from exact item catalog")
            equipment[slot] = code
        purple_or_higher = sum(
            item_catalog[str(code)].get("itemGrade")
            in PURPLE_OR_HIGHER_ITEM_GRADES
            for code in equipment.values()
        )
        if purple_or_higher >= 5:
            return tick
    return None


def _derive_credit_counter_audit(private_player: dict[str, Any]) -> dict[str, Any]:
    observations = private_player["observerStatusTimeline"]
    start_credit = observations[0][1] if observations else None
    end_credit = observations[-1][1] if observations else None
    growth = private_player["growthTempo"]
    gain = growth["totalGainCredit"]
    use = growth["totalUseCredit"]
    gap = None
    if start_credit is not None and end_credit is not None:
        gap = round((end_credit - start_credit) - (gain - use), 2)
    return {
        "status": "derived-exact-result-counters-vs-observed-balance-v1",
        "startObservedCredit": start_credit,
        "endObservedCredit": end_credit,
        "counterBalanceGap": gap,
        "ledgerCompatible": gap is not None and abs(gap) < 0.01,
        "interpretation": (
            "result gain/use counters are not treated as a transaction ledger"
        ),
    }


def _copy_player(
    private_player: dict[str, Any],
    public_id: int,
    item_catalog: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    skill_start_timeline = deepcopy(private_player["skillStartTimeline"])
    for row in skill_start_timeline:
        row[1] = _exact_skill_family(row[1], row[3])
    skills = deepcopy(private_player["skills"])
    for row in skills:
        codes = row.get("codes") or []
        primary_code = codes[0].get("code") if codes else None
        row["family"] = _exact_skill_family(row["family"], primary_code)
    family_usage: dict[str, int] = {}
    for row in skill_start_timeline:
        family_usage[row[1]] = family_usage.get(row[1], 0) + 1
    cooldowns = deepcopy(private_player["cooldowns"])
    for row in cooldowns:
        row["startCount"] = family_usage.get(row["family"], 0)
    combat_judgment = deepcopy(private_player["combatJudgment"])
    combat_judgment["definitions"]["targetAction"] = (
        "적 플레이어를 exact 대상으로 지정한 스킬/기본 공격 시작; 적중 아님"
    )
    combat_judgment["definitions"]["teamFocusFollowup"] = (
        "다른 팀원이 같은 적을 ±2초 안에 exact 대상으로 지정"
    )
    growth_tempo = deepcopy(private_player["growthTempo"])
    purple_build_tick = _derive_purple_build_tick(
        private_player["equipmentTimeline"], item_catalog
    )
    if (
        growth_tempo.get("purpleBuildTick") is not None
        and growth_tempo["purpleBuildTick"] != purple_build_tick
    ):
        raise ValueError("private purple build tick disagrees with exact item grades")
    if growth_tempo.get("fullBuildTick") != purple_build_tick:
        raise ValueError("private full build tick disagrees with exact item grades")
    growth_tempo["purpleBuildTick"] = purple_build_tick
    growth_tempo["creditCounterAudit"] = _derive_credit_counter_audit(
        private_player
    )
    growth_tempo["definitions"]["purpleBuild"] = (
        "exact 장비 업데이트에서 5부위가 모두 Epic(보라) 이상이 처음 된 tick"
    )
    skill_operation = deepcopy(private_player["skillOperation"])
    skill_operation["definitions"]["startRecord"] = (
        "각 CmdStartSkill 패킷 1건; 충전 스킬의 각 사용과 재시전 단계는 "
        "별도 시작으로 집계하며 입력 횟수는 아님"
    )
    skill_operation["definitions"]["sustainedActivation"] = (
        "활성 유지 시간은 시작 횟수에 더하지 않고, "
        "CmdStartPassiveSkill/CmdStartStateSkill은 제외"
    )
    skill_operation.setdefault("hitRates", [])
    for episode in skill_operation.get("episodes", []):
        episode.setdefault("hitRates", [])
    # Older evidence catalogs remain valid for the map/timeline surfaces but
    # predate the projectile spawn/collision join.  Keep that boundary typed
    # and empty instead of inventing hit-rate rows or blocking unrelated map
    # regeneration.
    projectile_hit_rates = deepcopy(private_player.get("projectileHitRates", []))
    for row in projectile_hit_rates:
        private_hit_packet = row.get("hitEvidencePacket", {}).get("packet")
        public_hit_packets = {
            "CmdProjectileCollision.objectId/targetId": (
                "CmdProjectileCollision 투사체·대상 번호"
            ),
            (
                "CmdPlaySkillActionWithTargets.targets.targetId + "
                "same-tick CmdDamage/CmdProjectileCollision"
            ): "스킬 대상 기록 + 같은 시각의 대인 피해·투사체 충돌",
            (
                "CmdDamage.effectCode/objectId + exact Skill/"
                "CharacterState code/group + CmdPlaySkillAction tick"
            ): (
                "CmdDamage 효과 코드 + 같은 버전 Skill·CharacterState 그룹 + "
                "같은 시각 스킬 동작"
            ),
        }
        if private_hit_packet in public_hit_packets:
            row["hitEvidencePacket"]["packet"] = public_hit_packets[
                private_hit_packet
            ]
        if row.get("attemptDenominator", {}).get("event") == (
            "CmdSpawn projectile snapshot objectId"
        ):
            row["attemptDenominator"]["event"] = "CmdSpawn 투사체 생성 번호"
        if row.get("duplicateCollisionPolicy") == (
            "unique projectileObjectId/targetObjectId"
        ):
            row["duplicateCollisionPolicy"] = "unique projectile-target pair"
    return {
        "publicPlayerId": public_id,
        "publicLabel": f"P{public_id:02d}",
        "teamNumber": private_player["teamNumber"],
        "characterCode": private_player["characterCode"],
        "characterName": private_player["characterName"],
        "maxLevel": private_player["maxLevel"],
        "result": _copy_result(private_player["gameResult"]),
        "movementTrack": deepcopy(private_player["movementTrack"]),
        "movementTrackStatus": private_player["movementTrackStatus"],
        "movementAnchorCount": private_player["movementAnchorCount"],
        "plannedPathCommands": deepcopy(private_player["plannedPathCommands"]),
        "plannedPathCommandCount": private_player["plannedPathCommandCount"],
        "plannedPathNodeCount": private_player["plannedPathNodeCount"],
        "plannedPathStatus": private_player["plannedPathStatus"],
        "lifeTimeline": deepcopy(private_player["lifeTimeline"]),
        "kdaTimeline": deepcopy(private_player["kdaTimeline"]),
        "kdaTimelineStatus": private_player["kdaTimelineStatus"],
        **({"kdaCrosscheck": deepcopy(private_player["kdaCrosscheck"])} if private_player.get("kdaCrosscheck") else {}),
        "observerStatusTimeline": deepcopy(
            private_player["observerStatusTimeline"]
        ),
        "observerStatusTimelineStatus": private_player[
            "observerStatusTimelineStatus"
        ],
        "survivableTimeTimeline": deepcopy(
            private_player["survivableTimeTimeline"]
        ),
        "survivableTimeTimelineStatus": private_player[
            "survivableTimeTimelineStatus"
        ],
        "deathLocations": [
            {
                "tick": row["tick"],
                "position": deepcopy(row["position"]),
                "positionStatus": row["positionStatus"],
            }
            for row in private_player.get("exactDeathLocations", [])
        ],
        "stats": deepcopy(private_player["stats"]),
        "combatIntervals": deepcopy(private_player["combatIntervals"]),
        "sessions": deepcopy(private_player["sessions"]),
        "cooldowns": cooldowns,
        "skillCooldownTimeline": deepcopy(private_player["skillCooldownTimeline"]),
        "skillCooldownTimelineStatus": private_player["skillCooldownTimelineStatus"],
        "skills": skills,
        "skillStartTimeline": skill_start_timeline,
        "equipmentTimeline": deepcopy(private_player["equipmentTimeline"]),
        "equipmentTimelineStatus": private_player["equipmentTimelineStatus"],
        "inventoryTimeline": deepcopy(private_player["inventoryTimeline"]),
        "inventoryTimelineStatus": private_player["inventoryTimelineStatus"],
        "timelineBins": deepcopy(private_player["timelineBins"]),
        "skillDamageAttribution": deepcopy(private_player["skillDamageAttribution"]),
        "projectileHitRates": projectile_hit_rates,
        "characterCapabilities": deepcopy(
            private_player["characterCapabilities"]
        ),
        "observedCapabilityEvidence": deepcopy(
            private_player["observedCapabilityEvidence"]
        ),
        "combatJudgment": combat_judgment,
        "deathReview": deepcopy(private_player["deathReview"]),
        "growthTempo": growth_tempo,
        "objectivePreparation": deepcopy(private_player["objectivePreparation"]),
        "skillOperation": skill_operation,
        "sceneCoaching": deepcopy(private_player["sceneCoaching"]),
        "mapCombatHud": deepcopy(private_player["mapCombatHud"]),
    }


def _copy_item_catalog(private_catalog: dict[str, Any]) -> dict[str, Any]:
    names_payload = json.loads(NAMES_PATH.read_text(encoding="utf-8"))
    item_names = names_payload.get("items", {})
    if not isinstance(item_names, dict):
        raise ValueError("local item name map is invalid")
    item_asset_manifest = json.loads(ITEM_PROVENANCE_PATH.read_text(encoding="utf-8"))
    if item_asset_manifest.get("format") != "ercraft-item-icon-provenance.v1":
        raise ValueError("item icon provenance format is invalid")
    item_asset_names = {
        str(row["code"]): row["sourceName"]
        for row in item_asset_manifest.get("assets", [])
        if isinstance(row, dict)
        and isinstance(row.get("code"), int)
        and isinstance(row.get("sourceName"), str)
        and row["sourceName"].strip()
    }
    public = {}
    for key, row in private_catalog.items():
        if not isinstance(key, str) or not key.isdigit() or not isinstance(row, dict):
            raise ValueError("private item catalog is invalid")
        code = int(key)
        if row.get("code") != code:
            raise ValueError("private item catalog code mismatch")
        item_name = item_names.get(key)
        item_name_status = "exact-local-name-map"
        if not isinstance(item_name, str) or not item_name.strip():
            item_name = item_asset_names.get(key)
            item_name_status = "exact-item-asset-provenance-name"
        if not isinstance(item_name, str) or not item_name.strip():
            item_name = None
            item_name_status = "unavailable-no-localized-item-name"
        public[key] = {
            "code": code,
            "itemName": item_name,
            "itemNameStatus": item_name_status,
            "itemType": row.get("itemType"),
            "subType": row.get("subType"),
            "itemGrade": row.get("itemGrade"),
            "stackable": row.get("stackable"),
            "isCompletedItem": row.get("isCompletedItem"),
            "status": "exact-replay-version-game-data",
        }
    return public


def build_event_area_summary(
    field_report: dict[str, Any], *, client_version: str
) -> dict[str, Any]:
    if field_report.get("clientVersion") != client_version:
        raise ValueError("delta-field report client version does not match analysis")
    inventory = field_report.get("inventory")
    if not isinstance(inventory, list) or not inventory:
        raise ValueError("delta-field report inventory is missing")
    if any(row.get("status") != "decoded-exact-all" for row in inventory):
        raise ValueError("event-area summary requires exact decoding for every packet type")

    grouped: dict[str, list[dict[str, Any]]] = {name: [] for name in AREA_ORDER}
    for row in inventory:
        name = row.get("name")
        count = row.get("payloadCount")
        exact_count = row.get("exactCount")
        if not isinstance(name, str) or not isinstance(count, int) or count < 0:
            raise ValueError("invalid packet inventory row")
        if exact_count != count:
            raise ValueError(f"packet {name} is not exact for the event-area summary")
        category, _ = classify_packet(name)
        grouped[category].append(
            {
                "packetName": name,
                "label": PACKET_LABELS.get(name, name.removeprefix("Cmd")),
                "count": count,
            }
        )

    areas = []
    for category in AREA_ORDER:
        rows = sorted(
            grouped[category],
            key=lambda row: (-row["count"], row["packetName"]),
        )
        label, description = AREA_UI[category]
        areas.append(
            {
                "key": category,
                "label": label,
                "description": description,
                "packetTypeCount": len(rows),
                "eventCount": sum(row["count"] for row in rows),
                "topEvents": rows[:8],
                "status": "decoded-exact-counts-derived-name-category",
            }
        )

    by_name = {row["name"]: row for row in inventory}
    ping = by_name.get("CmdPing")
    system_chat = by_name.get("CmdSystemChat")
    ping_fields = {
        member.get("name")
        for member in (ping or {}).get("members", [])
        if isinstance(member, dict)
    }
    expected_ping_fields = {
        "type",
        "pingObjectId",
        "senderObjectId",
        "pingPositionVector2",
    }
    if ping is not None and not expected_ping_fields.issubset(ping_fields):
        raise ValueError("CmdPing does not expose the expected exact field set")

    packet_total = sum(area["eventCount"] for area in areas)
    if packet_total != field_report.get("packetPayloadCount"):
        raise ValueError("event-area packet total does not match the field report")
    return {
        "status": "decoded-exact-counts-with-derived-name-categories",
        "categoryAuthority": "derived-name-rule-v1",
        "packetPayloadCount": packet_total,
        "packetTypeCount": sum(area["packetTypeCount"] for area in areas),
        "areas": areas,
        "ping": {
            "status": "decoded-exact-fields" if ping is not None else "not-observed",
            "eventCount": ping["payloadCount"] if ping is not None else 0,
            "exactCount": ping["exactCount"] if ping is not None else 0,
            "systemMessageCount": (
                system_chat["payloadCount"] if system_chat is not None else 0
            ),
            "availableFields": (
                ["종류", "대상 오브젝트", "보낸 플레이어", "좌표 X/Z"]
                if ping is not None
                else []
            ),
            "types": [
                {"value": value, "label": label}
                for value, label in TACTICAL_PING_TYPES
                if value not in HYPERLOOP_TACTICAL_PING_TYPES
            ],
            "publicBoundary": (
                "실제 전술 핑의 시각·좌표·익명 발신자는 지도용으로 포함하지만 "
                "원본 플레이어·대상 오브젝트 식별자는 포함하지 않습니다."
            ),
        },
    }


def load_wildlife_assets(
    used_keys: set[str],
    provenance_path: Path = WILDLIFE_PROVENANCE_PATH,
) -> dict[str, Any]:
    if not used_keys:
        return {
            "status": "not-required-no-supported-wildlife-icons",
            "sourceFolderUrl": None,
            "icons": {},
        }
    manifest = json.loads(provenance_path.read_text(encoding="utf-8"))
    if manifest.get("format") != "ercraft-wildlife-icon-provenance.v1":
        raise ValueError("wildlife icon provenance format is invalid")
    rows = {row["key"]: row for row in manifest.get("assets", [])}
    missing = sorted(used_keys - rows.keys())
    if missing:
        raise ValueError(f"wildlife icons are missing from provenance: {missing}")
    icons = {}
    for key in sorted(used_keys):
        row = rows[key]
        path = provenance_path.parent / row["file"]
        payload = path.read_bytes()
        if hashlib.sha256(payload).hexdigest() != row["sha256"]:
            raise ValueError(f"wildlife icon hash mismatch: {key}")
        if not payload.startswith(b"\x89PNG\r\n\x1a\n"):
            raise ValueError(f"wildlife icon is not PNG: {key}")
        icons[key] = {
            "width": row["width"],
            "height": row["height"],
            "imageDataUrl": "data:image/png;base64,"
            + base64.b64encode(payload).decode("ascii"),
            "status": "verified-user-provided-drive-icon",
        }
    return {
        "status": manifest["status"],
        "sourceFolderUrl": manifest["sourceFolderUrl"],
        "icons": icons,
    }


def load_character_assets(
    character_codes: set[int],
    provenance_path: Path = CHARACTER_PROVENANCE_PATH,
) -> dict[str, Any]:
    manifest = json.loads(provenance_path.read_text(encoding="utf-8"))
    if manifest.get("format") != "ercraft-character-icon-provenance.v1":
        raise ValueError("character icon provenance format is invalid")
    rows = {row["characterCode"]: row for row in manifest.get("assets", [])}
    missing = sorted(character_codes - rows.keys())
    if missing:
        raise ValueError(f"character icons are missing from provenance: {missing}")
    icons = {}
    for code in sorted(character_codes):
        row = rows[code]
        path = provenance_path.parent / row["file"]
        if not path.is_file():
            raise ValueError(f"character icon is missing: {code}")
        payload = path.read_bytes()
        if hashlib.sha256(payload).hexdigest() != row["sha256"]:
            raise ValueError(f"character icon hash mismatch: {code}")
        if len(payload) < 12 or payload[:4] != b"RIFF" or payload[8:12] != b"WEBP":
            raise ValueError(f"character icon is not WebP: {code}")
        icons[str(code)] = {
            "sourceAssetFolder": row["sourceAssetFolder"],
            "imageDataUrl": "data:image/webp;base64,"
            + base64.b64encode(payload).decode("ascii"),
            "status": "hash-verified-character-code-to-fankit-folder-icon",
        }
    return {
        "status": "hash-verified-character-code-to-fankit-folder-icons",
        "icons": icons,
    }


def _verified_image_data_url(path: Path, row: dict[str, Any], label: str) -> str:
    if not path.is_file():
        raise ValueError(f"{label} is missing")
    payload = path.read_bytes()
    if hashlib.sha256(payload).hexdigest() != row.get("sha256"):
        raise ValueError(f"{label} hash mismatch")
    mime_type = row.get("mimeType")
    if mime_type == "image/png":
        if not payload.startswith(b"\x89PNG\r\n\x1a\n"):
            raise ValueError(f"{label} is not PNG")
    elif mime_type == "image/webp":
        if len(payload) < 12 or payload[:4] != b"RIFF" or payload[8:12] != b"WEBP":
            raise ValueError(f"{label} is not WebP")
    elif mime_type == "image/jpeg":
        if len(payload) < 4 or payload[:3] != b"\xff\xd8\xff":
            raise ValueError(f"{label} is not JPEG")
    else:
        raise ValueError(f"{label} mime type is invalid")
    return f"data:{mime_type};base64," + base64.b64encode(payload).decode("ascii")


def load_item_assets(
    item_codes: set[int],
    provenance_path: Path = ITEM_PROVENANCE_PATH,
) -> dict[str, Any]:
    manifest = json.loads(provenance_path.read_text(encoding="utf-8"))
    if manifest.get("format") != "ercraft-item-icon-provenance.v1":
        raise ValueError("item icon provenance format is invalid")
    rows = {row["code"]: row for row in manifest.get("assets", [])}
    unavailable_rows = {
        row["code"]: row for row in manifest.get("unavailable", [])
    }
    undeclared = sorted(item_codes - rows.keys() - unavailable_rows.keys())
    if undeclared:
        raise ValueError(f"item asset status is undeclared: {undeclared}")
    icons = {}
    for code in sorted(item_codes & rows.keys()):
        row = rows[code]
        path = ROOT / row["file"]
        icons[str(code)] = {
            "imageDataUrl": _verified_image_data_url(path, row, f"item icon {code}"),
            "status": "exact-code-hash-verified-item-icon",
        }
        if "displayScale" in row:
            scale = row["displayScale"]
            if not isinstance(scale, (int, float)) or not 0.5 <= scale <= 1.25:
                raise ValueError(f"item icon {code} display scale is invalid")
            icons[str(code)]["displayScale"] = float(scale)
        if "backgroundTreatment" in row:
            treatment = row["backgroundTreatment"]
            if treatment != "white-to-transparent":
                raise ValueError(f"item icon {code} background treatment is invalid")
            icons[str(code)]["backgroundTreatment"] = treatment
    unavailable = {
        str(code): unavailable_rows[code]["status"]
        for code in sorted(item_codes & unavailable_rows.keys())
    }
    return {
        "status": manifest["status"],
        "sourceFolderUrl": manifest["sourceFolderUrl"],
        "icons": icons,
        "unavailable": unavailable,
    }


def load_skill_assets(
    character_codes: set[int],
    provenance_path: Path = SKILL_PROVENANCE_PATH,
) -> dict[str, Any]:
    manifest = json.loads(provenance_path.read_text(encoding="utf-8"))
    if manifest.get("format") != "ercraft-skill-icon-provenance.v1":
        raise ValueError("skill icon provenance format is invalid")
    rows = {row["characterCode"]: row for row in manifest.get("characters", [])}
    missing = sorted(character_codes - rows.keys())
    if missing:
        raise ValueError(f"skill icon character status is missing: {missing}")
    icons = {}
    for code in sorted(character_codes):
        row = rows[code]
        slots = {}
        for slot_row in row.get("slots", []):
            slot = slot_row["slot"]
            if slot in slots:
                raise ValueError(f"duplicate skill icon slot: {code}/{slot}")
            path = ROOT / slot_row["file"]
            slots[slot] = {
                "semanticSlot": slot_row.get("semanticSlot", slot),
                "imageDataUrl": _verified_image_data_url(
                    path, slot_row, f"skill icon {code}/{slot}"
                ),
                "status": slot_row["status"],
            }
        icons[str(code)] = {
            "status": row["status"],
            "unavailableSemanticSlots": deepcopy(
                row.get("unavailableSemanticSlots", [])
            ),
            "slots": slots,
        }
    return {
        "status": manifest["status"],
        "icons": icons,
    }


def load_utility_skill_assets(
    players: list[dict[str, Any]],
    provenance_path: Path = UTILITY_SKILL_PROVENANCE_PATH,
) -> dict[str, Any]:
    manifest = json.loads(provenance_path.read_text(encoding="utf-8"))
    if manifest.get("format") != "ercraft-utility-skill-icon-provenance.v1":
        raise ValueError("utility skill icon provenance format is invalid")

    weapon = manifest["weapon"]
    weapon_rows = {row["masteryType"]: row for row in weapon.get("assets", [])}
    weapon_unavailable = {
        row["masteryType"]: row for row in weapon.get("unavailable", [])
    }
    override_rows = {
        row["characterCode"]: row
        for row in weapon.get("characterOverrides", [])
    }
    used_masteries = {
        player.get("result", {}).get("bestWeapon") for player in players
    }
    used_masteries = {
        value for value in used_masteries if isinstance(value, int) and value > 0
    }
    undeclared = used_masteries - weapon_rows.keys() - weapon_unavailable.keys()
    if undeclared:
        raise ValueError(f"weapon skill asset status is undeclared: {sorted(undeclared)}")
    weapon_icons = {}
    for mastery_type in sorted(used_masteries & weapon_rows.keys()):
        row = weapon_rows[mastery_type]
        weapon_icons[str(mastery_type)] = {
            "name": row["name"],
            "imageDataUrl": _verified_image_data_url(
                ROOT / row["file"], row, f"weapon skill icon {mastery_type}"
            ),
            "status": row["status"],
        }
    character_overrides = {}
    for code in sorted({player["characterCode"] for player in players} & override_rows.keys()):
        row = override_rows[code]
        character_overrides[str(code)] = {
            "name": row["name"],
            "imageDataUrl": _verified_image_data_url(
                ROOT / row["file"], row, f"weapon skill override {code}"
            ),
            "status": row["status"],
        }

    tactical = manifest["tactical"]
    tactical_rows = {row["slug"]: row for row in tactical.get("assets", [])}
    code_to_slug = tactical.get("codeToSlug", {})
    observed_groups = {
        player.get("result", {}).get("tacticalSkillGroup") for player in players
    }
    observed_groups = {
        value for value in observed_groups if isinstance(value, int) and value > 0
    }
    tactical_icons = {}
    tactical_unavailable = {}
    for group in sorted(observed_groups):
        normalized = group // 10 * 10
        slug = code_to_slug.get(str(normalized))
        row = tactical_rows.get(slug)
        if row is None:
            tactical_unavailable[str(group)] = (
                "unavailable-no-exact-tactical-group-code-asset-mapping"
            )
            continue
        tactical_icons[str(group)] = {
            "slug": slug,
            "imageDataUrl": _verified_image_data_url(
                ROOT / row["file"], row, f"tactical skill icon {group}"
            ),
            "status": row["status"],
        }
    return {
        "status": "exact-replay-result-to-provenance-bound-utility-skill-icons",
        "weapon": {
            "status": weapon["status"],
            "icons": weapon_icons,
            "characterOverrides": character_overrides,
            "unavailable": {
                str(code): weapon_unavailable[code]["status"]
                for code in sorted(used_masteries & weapon_unavailable.keys())
            },
        },
        "tactical": {
            "status": tactical["status"],
            "icons": tactical_icons,
            "unavailable": tactical_unavailable,
        },
    }


def load_map_marker_assets(
    provenance_path: Path = MAP_MARKER_PROVENANCE_PATH,
) -> dict[str, Any]:
    manifest = json.loads(provenance_path.read_text(encoding="utf-8"))
    if manifest.get("format") != "ercraft-map-marker-draft-provenance.v1":
        raise ValueError("map marker provenance format is invalid")
    if manifest.get("status") != "approved-local-integration":
        raise ValueError("map marker set has not been approved for local integration")
    base = provenance_path.parent
    rows = {
        Path(row["file"]).name: row
        for group in ("dakAssets", "bossAssets", "clientAssets")
        for row in manifest.get(group, [])
    }

    def verified_png(file_name: str) -> str:
        row = rows.get(file_name)
        if row is None:
            raise ValueError(f"map marker provenance is missing {file_name}")
        path = base / row["file"]
        payload = path.read_bytes()
        if hashlib.sha256(payload).hexdigest() != row["sha256"]:
            raise ValueError(f"map marker hash mismatch: {file_name}")
        if not payload.startswith(b"\x89PNG\r\n\x1a\n"):
            raise ValueError(f"map marker is not PNG: {file_name}")
        return "data:image/png;base64," + base64.b64encode(payload).decode("ascii")

    symbol_source = (base / "icons.svg").read_text(encoding="utf-8")

    def vector_symbol(symbol_id: str) -> str:
        match = re.search(
            rf'<symbol id="{re.escape(symbol_id)}" viewBox="([^"]+)">(.*?)</symbol>',
            symbol_source,
            re.DOTALL,
        )
        if match is None:
            raise ValueError(f"approved vector marker is missing: {symbol_id}")
        svg = (
            f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="{match.group(1)}">'
            f"{match.group(2)}</svg>"
        ).encode("utf-8")
        return "data:image/svg+xml;base64," + base64.b64encode(svg).decode("ascii")

    file_icons = {
        "cctv": "Ico_Map_SecurityConsole.png",
        "vls": "Ico_Map_VLS_Idle.png",
        "meteor-warning": "Ico_Map_Meteor_01.png",
        "meteor": "Ico_Map_Meteor_02.png",
        "tree-of-life-warning": "Ico_Map_TreeOfLife_01.png",
        "tree-of-life": "Ico_Map_TreeOfLife_02.png",
        "air-supply-rare-warning": "Ico_Map_AirDrop_Rare_01.png",
        "air-supply-rare": "Ico_Map_AirDrop_Rare_02.png",
        "air-supply-epic-warning": "Ico_Map_AirDrop_Epic_01.png",
        "air-supply-epic": "Ico_Map_AirDrop_Epic_02.png",
        "air-supply-legend-warning": "Ico_Map_AirDrop_Legend_01.png",
        "air-supply-legend": "Ico_Map_AirDrop_Legend_02.png",
        "air-supply-mythic-warning": "Ico_Map_AirDrop_Mythic_01.png",
        "air-supply-mythic": "Ico_Map_AirDrop_Mythic_02.png",
        "rift-warning": "Ico_Map_Rift_Dimension_Neutral_Expected.png",
        "rift": "Ico_Map_Rift_Dimension_Neutral.png",
        "gold-cube": "Ico_Map_BuffCube_Gold.png",
        "wickeline-warning": "MonsterIcon_Wickline_01_Expected.png",
        "alpha-warning": "MonsterIcon_Alpha_01_Expected.png",
        "omega-warning": "MonsterIcon_Omega_01_Expected.png",
        "wickeline": "wickeline-main.png",
        "alpha": "alpha-mini.png",
        "omega": "omega-mini.png",
        "surveillance-camera": "Ico_Map_Ally_SummonCamera.png",
        "recon-orb": "SummonProfile_Orb_Base.png",
        "movement-ping": "Ico_Map_Ping_Run.png",
        "lumi-normal": "Ico_Map_GuideRobot_Normal.png",
        "lumi-battle": "Ico_Map_GuideRobot_Battle.png",
        "lumi-credit-rich": "Ico_Map_GuideRobot_CreditRich.png",
        "bori-base": "Ico_Map_Bori.png",
        "bori-rare": "Ico_Map_Bori_Rare.png",
        "bori-epic": "Ico_Map_Bori_Epic.png",
        "bori-legend": "Ico_Map_Bori_Legend.png",
        "bori-mythic": "Ico_Map_Bori_Mythic.png",
        "rift-map-background": "Img_Map_Rift_Labolatory_01_All.png",
        "tactical-ping-run": "Ico_Map_Ping_Run.png",
        "tactical-ping-warning": "Ico_Map_Ping_Warning.png",
        "tactical-ping-escape": "Ico_Map_Ping_Escape.png",
        "tactical-ping-help": "Ico_Map_Ping_Help.png",
        "tactical-ping-lets-join": "Ico_Map_Ping_LetsJoin.png",
        "tactical-ping-enemy-vision": "Ico_Map_Ping_EnemyVision.png",
        "tactical-ping-need-vision": "Ico_Map_Ping_NeedVision.png",
        "tactical-ping-all-in": "Ico_Map_Ping_AllIn.png",
        "tactical-ping-target": "Ico_Map_Ping_Target.png",
        "tactical-ping-select": "Ico_Map_Ping_Select.png",
        "tactical-ping-fallback": "Ico_Map_Ping_FallBack.png",
    }
    icons = {
        key: {
            "imageDataUrl": verified_png(file_name),
            "status": "approved-hash-verified-map-marker",
        }
        for key, file_name in file_icons.items()
    }
    for key in ("control-lens", "campfire", "kiosk", "hyperloop"):
        icons[key] = {
            "imageDataUrl": vector_symbol(key),
            "status": "approved-code-native-vector-marker",
        }
    icons["wickeline"]["crop"] = {"x": 0.139, "yByWidth": 0.0032, "sizeByWidth": 0.3226}
    for key in ("alpha", "omega"):
        icons[key]["crop"] = {"x": 0.0536, "yByWidth": 0.0357, "sizeByWidth": 0.8929}
    png_geometry = {
        "meteor-warning": ([25, 28], [12.5, 22]),
        "meteor": ([25, 20], [12.5, 10]),
        "tree-of-life-warning": ([23, 30], [11.5, 24]),
        "tree-of-life": ([23, 23], [12, 10.5]),
        "air-supply-rare-warning": ([19, 29], [9.5, 23]),
        "air-supply-rare": ([19, 20], [9, 10]),
        "air-supply-epic-warning": ([19, 29], [9.5, 23]),
        "air-supply-epic": ([19, 20], [9.5, 10.5]),
        "air-supply-legend-warning": ([19, 29], [9.5, 23]),
        "air-supply-legend": ([19, 20], [9.5, 10]),
        "air-supply-mythic-warning": ([19, 29], [9.5, 23]),
        "air-supply-mythic": ([19, 20], [8.5, 9.5]),
        "rift-warning": ([22, 33], [11, 27]),
        "rift": ([24, 26], [11.5, 14]),
        "gold-cube": ([19, 18], [10.5, 8.5]),
        "cctv": ([18, 17], [9, 8.5]),
        "vls": ([23, 23], [11.5, 11.5]),
        "wickeline-warning": ([24, 32], [12, 26]),
        "alpha-warning": ([20, 32], [10, 26]),
        "omega-warning": ([20, 32], [10, 26]),
        "surveillance-camera": ([14, 15], [7, 7.5]),
        "recon-orb": ([128, 128], [64, 64]),
        "movement-ping": ([26, 26], [13, 13]),
        "lumi-normal": ([30, 30], [15, 15]),
        "lumi-battle": ([30, 30], [15, 15]),
        "lumi-credit-rich": ([30, 30], [15, 15]),
        "bori-base": ([24, 20], [12, 10]),
        "bori-rare": ([25, 21], [12.5, 10.5]),
        "bori-epic": ([25, 21], [12.5, 10.5]),
        "bori-legend": ([25, 21], [12.5, 10.5]),
        "bori-mythic": ([25, 21], [12.5, 10.5]),
        "tactical-ping-run": ([26, 26], [13, 13]),
        "tactical-ping-warning": ([26, 26], [13, 13]),
        "tactical-ping-escape": ([26, 26], [13, 13]),
        "tactical-ping-help": ([26, 26], [13, 13]),
        "tactical-ping-lets-join": ([26, 26], [13, 13]),
        "tactical-ping-enemy-vision": ([26, 26], [13, 13]),
        "tactical-ping-need-vision": ([26, 26], [13, 13]),
        "tactical-ping-all-in": ([26, 26], [13, 13]),
        "tactical-ping-target": ([34, 34], [17, 17]),
        "tactical-ping-select": ([21, 28], [10.5, 14]),
        "tactical-ping-fallback": ([26, 26], [13, 13]),
    }
    for key, (pixel_size, anchor_pixel) in png_geometry.items():
        icons[key]["pixelSize"] = pixel_size
        icons[key]["anchorPixel"] = anchor_pixel
    return {
        "status": "approved-hash-verified-local-map-marker-set",
        "icons": icons,
    }


def load_restriction_area_shapes(
    path: Path = RESTRICTION_AREA_PATHS_PATH,
) -> dict[str, Any]:
    source = json.loads(path.read_text(encoding="utf-8"))
    expected_codes = {str(code) for code in range(10, 201, 10)}
    if (
        source.get("format") != "ercraft-lumia-area-paths.v1"
        or source.get("status") != "reference-exact-area-shapes-local-only"
        or source.get("viewBox") != [0, 0, 1544, 1962]
        or source.get("sourceMap") != {"width": 772, "height": 981}
        or set(source.get("paths", {})) != expected_codes
        or any(
            not isinstance(value, str) or not value.startswith("M")
            for value in source.get("paths", {}).values()
        )
    ):
        raise ValueError("Lumia restriction-area shape source is invalid")
    return {
        "status": "reference-exact-lumia-area-shapes-local-only",
        "viewBox": deepcopy(source["viewBox"]),
        "sourceMap": deepcopy(source["sourceMap"]),
        "paths": deepcopy(source["paths"]),
    }


def derive_secondary_map_spaces(
    players: list[dict[str, Any]],
    private_map: dict[str, Any],
    field_report: dict[str, Any],
    client_version: str,
) -> list[dict[str, Any]]:
    """Project exact rift coordinates with a version-bound client-map calibration."""
    rift_row = next(
        (
            row
            for row in field_report.get("inventory", [])
            if row.get("name") == "CmdDimensionalRiftBattleState"
            and row.get("status") == "decoded-exact-all"
            and int(row.get("exactCount", 0)) > 0
        ),
        None,
    )
    calibration = RIFT_MAP_CALIBRATION_BY_CLIENT_VERSION.get(client_version)
    if (
        rift_row is None
        or calibration is None
        or str(private_map.get("playbackStatus", "")).startswith("unavailable")
    ):
        return []
    image = private_map["coordinateSpace"]
    status_version = calibration["statusVersion"]
    projection = private_map["projection"]
    pixel_x = projection["pixelX"]
    pixel_y = projection["pixelY"]

    def on_lumia(x: float, z: float) -> bool:
        px = pixel_x[0] * x + pixel_x[1] * z + pixel_x[2]
        py = pixel_y[0] * x + pixel_y[1] * z + pixel_y[2]
        return 0 <= px <= image["w"] and 0 <= py <= image["h"]

    from decoder.azure_map_space import derive_azure_space, contains
    azure_spaces = derive_azure_space(players, client_version)
    samples: list[tuple[float, float, int, int, str]] = []
    for player in players:
        for tick, x, z, _source in player.get("movementTrack", []):
            if not on_lumia(x, z) and not any(contains(a["bounds"], x, z) for a in azure_spaces):
                samples.append((float(x), float(z), int(tick), player["teamNumber"], str(_source)))
    if len(samples) < 40:
        return []

    # The held replay tiles simultaneous rift rooms far apart on world X.  A
    # split separates every empty 25-unit gap. Apply room support requirements
    # afterwards, so sparse distant warps cannot contaminate a supported room.
    ordered = sorted(samples, key=lambda row: row[0])
    cuts = []
    for index in range(1, len(ordered)):
        if (
            ordered[index][0] - ordered[index - 1][0] >= 25
        ):
            cuts.append(index)
    groups = []
    start = 0
    for cut in cuts + [len(ordered)]:
        group = ordered[start:cut]
        start = cut
        if len(group) >= 40:
            groups.append(group)

    spaces = []
    available_centers = list(calibration["roomCenters"])
    for index, group in enumerate(groups, start=1):
        xs = [row[0] for row in group]
        zs = [row[1] for row in group]
        # A repeated off-map warp location is not evidence for another room.
        # Keep the recorded movement anchors; exclude only this room candidate.
        if len(set(zip(xs, zs))) == 1 and {row[4] for row in group} <= {"CmdWarpTo", "full-snapshot"} and any(row[4] == "CmdWarpTo" for row in group):
            ox, oy = calibration["pixelOrigin"]
            scale = calibration["pixelPerWorldUnit"]
            if not any(0 <= ox + scale * (-(xs[0]-cx)+(zs[0]-cz)) <= calibration["image"]["w"]
                       and 0 <= oy + scale * (-(xs[0]-cx)-(zs[0]-cz)) <= calibration["image"]["h"]
                       for cx, cz in calibration["roomCenters"]):
                continue
        if not available_centers:
            raise ValueError(
                f"{status_version} rift coordinate group has no calibrated room center"
            )
        mean_x = sum(xs) / len(xs)
        mean_z = sum(zs) / len(zs)
        room_center = min(
            available_centers,
            key=lambda center: (mean_x - center[0]) ** 2 + (mean_z - center[1]) ** 2,
        )
        local_xs = [value - room_center[0] for value in xs]
        local_zs = [value - room_center[1] for value in zs]
        # Calibration defines an affine transform, not a movement envelope
        # inferred from one match. Validate the projected image coordinates.
        origin_x, origin_y = calibration["pixelOrigin"]
        scale = calibration["pixelPerWorldUnit"]
        if any(
            not (0 <= origin_x + scale * (-x + z) <= calibration["image"]["w"]
                 and 0 <= origin_y + scale * (-x - z) <= calibration["image"]["h"])
            for x, z in zip(local_xs, local_zs)
        ):
            raise ValueError(
                f"{status_version} rift coordinates exceed the pixel-validated room calibration"
            )
        available_centers.remove(room_center)
        x_span = max(xs) - min(xs)
        z_span = max(zs) - min(zs)
        x_pad = max(2.0, x_span * 0.08)
        z_pad = max(2.0, z_span * 0.08)
        spaces.append({
            "spaceId": f"rift-{index}",
            "label": "균열 연구 구역",
            "status": (
                f"calibrated-{status_version}-rift-space-with-exact-rift-packets"
            ),
            "backgroundStatus": (
                "hash-verified-client-rift-art-with-pixel-validated-"
                f"{status_version}-coordinate-registration"
            ),
            "backgroundAssetKey": "rift-map-background",
            "firstTick": min(row[2] for row in group),
            "lastTick": max(row[2] for row in group),
            "teamNumbers": sorted({row[3] for row in group}),
            "observedAnchorCount": len(group),
            "exactRiftBattleStatePacketCount": int(rift_row["exactCount"]),
            "bounds": {
                "xMin": min(xs) - x_pad,
                "xMax": max(xs) + x_pad,
                "zMin": min(zs) - z_pad,
                "zMax": max(zs) + z_pad,
            },
            "projection": {
                "type": (
                    f"calibrated-rift-world-to-client-pixel-{status_version}.v1"
                ),
                "formula": (
                    "pixelX=178+3.48*(-localX+localZ); "
                    "pixelY=178+3.48*(-localX-localZ)"
                ),
                "originWorld": list(room_center),
                "pixelOrigin": list(calibration["pixelOrigin"]),
                "pixelPerWorldUnit": calibration["pixelPerWorldUnit"],
                "validationStatus": calibration["validationStatus"],
            },
            "image": deepcopy(calibration["image"]),
        })
    return spaces


def build_public_team_combat_log(
    private_catalog: dict[str, Any],
    private_to_public: dict[int, int],
) -> dict[str, Any]:
    """Project exact player life/kill packets into an anonymous team feed.

    CmdKill is a credit packet rather than a guaranteed death transition.  A
    same-tick CmdDyingCondition therefore becomes a down row; an unpaired
    CmdKill remains a kill-credit row.  CmdDead is always published separately
    as the exact death transition.  Missing attacker evidence stays null.
    """

    event_types = private_catalog.get("eventTypes") or {}
    events = private_catalog.get("events") or []
    if not isinstance(event_types, dict) or not isinstance(events, list):
        raise ValueError("private event stream is invalid")

    definitions: dict[int, tuple[str, list[str]]] = {}
    for name in ("CmdKill", "CmdDead", "CmdDyingCondition", "CmdResurrection"):
        fallback_code, fallback_fields = EVENT_SPECS[name]
        row = event_types.get(str(fallback_code))
        if row is None:
            definitions[fallback_code] = (name, list(fallback_fields))
            continue
        if (
            not isinstance(row, dict)
            or row.get("packetName") != name
            or row.get("fields") != fallback_fields
        ):
            raise ValueError(f"private {name} event definition is invalid")
        definitions[fallback_code] = (name, list(fallback_fields))

    def value(event: list[Any], field: str) -> Any:
        definition = definitions.get(event[2])
        if definition is None or field not in definition[1]:
            return None
        index = 5 + definition[1].index(field)
        return event[index] if index < len(event) else None

    player_ids = set(private_to_public)
    relevant = [
        event
        for event in events
        if isinstance(event, list)
        and len(event) >= 6
        and isinstance(event[0], int)
        and isinstance(event[1], int)
        and event[2] in definitions
    ]
    relevant.sort(key=lambda event: (event[0], event[1]))

    down_keys = {
        (event[0], value(event, "objectId"))
        for event in relevant
        if definitions[event[2]][0] == "CmdDyingCondition"
        and value(event, "objectId") in player_ids
    }
    kill_by_tick_victim: dict[tuple[int, int], int] = {}
    for event in relevant:
        if definitions[event[2]][0] != "CmdKill":
            continue
        killer = value(event, "objectId")
        victim = value(event, "deadCharacterObjectId")
        if killer not in player_ids or victim not in player_ids or killer == victim:
            continue
        key = (event[0], victim)
        previous = kill_by_tick_victim.setdefault(key, killer)
        if previous != killer:
            raise ValueError("conflicting exact CmdKill attackers at one tick")

    rows: list[dict[str, Any]] = []
    pending_down_attacker: dict[int, int | None] = {}

    def append_row(
        *,
        event: list[Any],
        kind: str,
        victim: int,
        attacker: int | None,
        status: str,
        down_attacker: int | None = None,
    ) -> None:
        rows.append({
            "publicEventId": len(rows) + 1,
            "tick": event[0],
            "sequence": event[1],
            "kind": kind,
            "attackerPublicPlayerId": private_to_public.get(attacker),
            "victimPublicPlayerId": private_to_public[victim],
            "downAttackerPublicPlayerId": private_to_public.get(down_attacker),
            "attributionStatus": status,
        })

    for event in relevant:
        name = definitions[event[2]][0]
        if name == "CmdKill":
            killer = value(event, "objectId")
            victim = value(event, "deadCharacterObjectId")
            if killer not in player_ids or victim not in player_ids or killer == victim:
                continue
            if (event[0], victim) in down_keys:
                pending_down_attacker[victim] = killer
                append_row(
                    event=event,
                    kind="down",
                    victim=victim,
                    attacker=killer,
                    status="decoded-exact-same-tick-CmdKill-and-CmdDyingCondition",
                )
            else:
                append_row(
                    event=event,
                    kind="kill",
                    victim=victim,
                    attacker=killer,
                    status="decoded-exact-CmdKill-credit-without-same-tick-down",
                )
        elif name == "CmdDyingCondition":
            victim = value(event, "objectId")
            if victim not in player_ids or (event[0], victim) in kill_by_tick_victim:
                continue
            pending_down_attacker[victim] = None
            append_row(
                event=event,
                kind="down",
                victim=victim,
                attacker=None,
                status="decoded-exact-CmdDyingCondition-attacker-unavailable",
            )
        elif name == "CmdDead":
            victim = value(event, "objectId")
            if victim not in player_ids:
                continue
            finisher = value(event, "finishingAttackerObjectId")
            same_tick_killer = kill_by_tick_victim.get((event[0], victim))
            attacker = (
                finisher
                if finisher in player_ids and finisher != victim
                else same_tick_killer
            )
            append_row(
                event=event,
                kind="death",
                victim=victim,
                attacker=attacker,
                down_attacker=pending_down_attacker.get(victim),
                status=(
                    "decoded-exact-CmdDead-with-player-finisher"
                    if attacker is not None
                    else "decoded-exact-CmdDead-finisher-unavailable"
                ),
            )
            pending_down_attacker.pop(victim, None)
        elif name == "CmdResurrection":
            victim = value(event, "objectId")
            if victim in player_ids:
                pending_down_attacker.pop(victim, None)

    return {
        "status": "decoded-exact-team-kill-down-death-feed",
        "displaySeconds": TEAM_COMBAT_LOG_DISPLAY_SECONDS,
        "items": rows,
        "fallbackUsed": False,
    }


def point_is_in_public_map_space(map_data, position):
    if map_data.get("playbackStatus") == "unavailable-mode-specific-map-and-bounds":
        return True
    if (
        not isinstance(position, list)
        or len(position) != 2
        or not all(isinstance(value, (int, float)) and math.isfinite(value) for value in position)
    ):
        return False
    projection = map_data.get("projection", {})
    pixel_x = projection.get("pixelX", [])
    pixel_y = projection.get("pixelY", [])
    space = map_data.get("coordinateSpace", {})
    if len(pixel_x) == 3 and len(pixel_y) == 3:
        world_x, world_z = position
        image_x = pixel_x[0] * world_x + pixel_x[1] * world_z + pixel_x[2]
        image_y = pixel_y[0] * world_x + pixel_y[1] * world_z + pixel_y[2]
        if 0 <= image_x <= space.get("w", -1) and 0 <= image_y <= space.get("h", -1):
            return True
    for secondary in map_data.get("secondarySpaces", []):
        bounds = secondary.get("bounds", {})
        if (
            bounds.get("xMin", math.inf) <= position[0] <= bounds.get("xMax", -math.inf)
            and bounds.get("zMin", math.inf) <= position[1] <= bounds.get("zMax", -math.inf)
        ):
            return True
    return False

def build_public_catalog(
    private_catalog: dict[str, Any],
    field_report: dict[str, Any],
    *,
    report_id: str | None = None,
    deletion_contact_url: str = DEFAULT_DELETION_CONTACT_URL,
) -> dict[str, Any]:
    if private_catalog.get("format") != "er-replay-combat-analysis.v1":
        raise ValueError("private combat analysis format is invalid")
    report_id = report_id or new_report_id()
    if not REPORT_ID_PATTERN.fullmatch(report_id):
        raise ValueError("public report id must be an opaque report-<hex> value")
    if deletion_contact_url != DEFAULT_DELETION_CONTACT_URL:
        raise ValueError("public deletion contact must use the verified ERCraft contact route")

    private_meta = private_catalog["meta"]
    private_map = private_catalog["map"]
    private_players = deepcopy(private_catalog["players"])
    normalize_simultaneous_combat_entries(private_players)
    scene_coaching_summary = attach_scene_coaching(
        private_players,
        private_catalog.get("wildlife"),
        first_tick=private_catalog["meta"].get("firstTick", 0),
        phase_clock=private_catalog.get("phaseClock"),
    )
    attach_map_combat_hud(
        private_players,
        events=private_catalog.get("events") or [],
        event_types=private_catalog.get("eventTypes") or {},
        object_owners=private_catalog.get("objectOwners") or {},
        runtime_cc=(
            private_catalog.get("characterCapabilityCatalog", {})
            .get("runtimeCrowdControl")
        ),
        non_player_object_ids={
            value
            for value in private_catalog.get("observedNonPlayerObjectIds", [])
            if isinstance(value, int)
        }
        | {
            row["objectId"]
            for row in private_catalog.get("wildlife", [])
            if isinstance(row.get("objectId"), int)
        }
        | {
            row["objectId"]
            for row in private_catalog.get("worldMap", {}).get(
                "staticObjects", []
            )
            if isinstance(row.get("objectId"), int)
            and not isinstance(row.get("ownerId"), int)
        },
    )
    private_to_public = {
        player["objectId"]: public_id
        for public_id, player in enumerate(private_players, start=1)
    }
    team_combat_log = build_public_team_combat_log(
        private_catalog,
        private_to_public,
    )
    private_object_owners = private_catalog.get("objectOwners", {})
    if not isinstance(private_object_owners, dict):
        raise ValueError("private object owner map is invalid")

    def owner_public_id(
        object_id: int,
        snapshot_owner_id: int | None = None,
    ) -> tuple[int | None, str]:
        chain_owner = private_object_owners.get(str(object_id))
        if chain_owner is not None:
            public_id = private_to_public.get(chain_owner)
            if public_id is not None:
                return public_id, "decoded-exact-spawn-owner-chain"
        if isinstance(snapshot_owner_id, int):
            public_id = private_to_public.get(snapshot_owner_id)
            if public_id is not None:
                return public_id, "decoded-exact-snapshot-owner-id"
        return None, "unavailable-no-resolved-player-owner"
    item_catalog = _copy_item_catalog(private_catalog["itemCatalog"])
    players = [
        _copy_player(player, public_id, item_catalog)
        for public_id, player in enumerate(private_players, start=1)
    ]
    secondary_map_spaces = derive_secondary_map_spaces(
        players,
        private_map,
        field_report,
        str(private_meta.get("clientVersion", "")),
    )
    from decoder.azure_map_space import derive_azure_space
    secondary_map_spaces += derive_azure_space(players, str(private_meta.get("clientVersion", "")))
    item_assets = load_item_assets({int(code) for code in item_catalog})
    character_assets = load_character_assets({
        player["characterCode"] for player in players
    })
    skill_assets = load_skill_assets({
        player["characterCode"] for player in players
    })
    utility_skill_assets = load_utility_skill_assets(players)
    map_marker_assets = load_map_marker_assets()
    restriction_area_shapes = load_restriction_area_shapes()
    private_world_map = private_catalog.get("worldMap")
    if not isinstance(private_world_map, dict) or private_world_map.get(
        "fallbackUsed"
    ) is not False:
        raise ValueError("private analysis is missing the exact world-map projection")
    bori_supply_boxes = [
        row
        for row in private_world_map.get("staticObjects", [])
        if row.get("category") == "bori-supply-box"
        and row.get("objectType") == 56
    ]

    def lifecycle_end(row: dict[str, Any]) -> int | None:
        ticks = [
            row.get("deathTick"), row.get("destroyTick"), row.get("despawnTick")
        ]
        exact = sorted(tick for tick in ticks if isinstance(tick, int))
        return exact[0] if exact else None

    bori_companions_by_spawn: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for row in private_catalog.get("wildlife", []):
        if row.get("monsterCode") == 21 and isinstance(row.get("spawnTick"), int):
            bori_companions_by_spawn[row["spawnTick"]].append(row)
    used_bori_supply_box_ids: set[int] = set()

    def exact_bori_supply_box(
        wildlife: dict[str, Any],
    ) -> dict[str, Any] | None:
        """Link the grade box through the observed Bori lifecycle.

        In held 12.2 evidence the Bori/BoriBox monster pair spawns first.  The
        ObjectType 56 BoriSupplyBox snapshot is observed only after the 1-HP
        Bori guide disappears and before the moving BoriBox disappears.  It is
        therefore incorrect to require a same-tick spawn.  We publish a grade
        only when this bracket selects exactly one supply box; zero candidates
        stays explicitly ungraded and multiple candidates fail closed.
        """
        companions = bori_companions_by_spawn.get(wildlife.get("spawnTick"), [])
        if len(companions) != 1:
            return None
        companion_end = lifecycle_end(companions[0])
        box_end = lifecycle_end(wildlife)
        if companion_end is None or box_end is None or companion_end > box_end:
            return None
        candidates = [
            row
            for row in bori_supply_boxes
            if isinstance(row.get("firstSeenTick"), int)
            and companion_end <= row["firstSeenTick"] <= box_end
        ]
        if len(candidates) > 1:
            raise ValueError(
                "Bori lifecycle contains multiple BoriSupplyBoxSnapshot rows"
            )
        if not candidates:
            return None
        candidate = candidates[0]
        if candidate["objectId"] in used_bori_supply_box_ids:
            raise ValueError("BoriSupplyBoxSnapshot was linked to multiple Bori encounters")
        used_bori_supply_box_ids.add(candidate["objectId"])
        grade = candidate.get("boxGrade")
        if grade not in BORI_GRADE_ASSET_KEYS:
            raise ValueError(f"unsupported exact Bori box grade: {grade}")
        return candidate

    public_wildlife = []
    public_wildlife_group_ids: dict[tuple[str, int], int] = {}
    excluded_bori_companion_count = 0
    for wildlife in private_catalog.get("wildlife", []):
        # Monster 21 is the one-hit-point Bori guide entity paired with the
        # moving, targetable box (monster 22). The minimap has one graded Bori
        # marker, so publishing both would duplicate the same encounter.
        if wildlife.get("monsterCode") == 21:
            excluded_bori_companion_count += 1
            continue
        public_id = len(public_wildlife) + 1
        required_map_fields = {
            "displayGroupObjectId", "displayGroupStatus", "mapPositionTrack",
            "mapPositionAnchorCount", "mapMovementMode",
        }
        missing_map_fields = required_map_fields - wildlife.keys()
        if missing_map_fields:
            raise ValueError(
                f"private wildlife map grouping is missing: {sorted(missing_map_fields)}"
            )
        private_group_id = wildlife["displayGroupObjectId"]
        group_key = (
            ("scratch", private_group_id)
            if isinstance(private_group_id, int)
            else ("single", wildlife["objectId"])
        )
        public_group_id = public_wildlife_group_ids.setdefault(
            group_key, len(public_wildlife_group_ids) + 1
        )
        owner_id, owner_status = owner_public_id(
            wildlife["objectId"], wildlife.get("ownerId")
        )
        base_asset_key = (
            wildlife["assetKey"].removeprefix("mutant-")
            if isinstance(wildlife.get("assetKey"), str)
            else None
        )
        marker_color = WILDLIFE_MARKER_COLORS.get(base_asset_key)
        bori_supply_box = (
            exact_bori_supply_box(wildlife)
            if wildlife["monsterCode"] == 22
            else None
        )
        bori_grade = (
            bori_supply_box["boxGrade"]
            if bori_supply_box is not None
            else None
        )
        grade_marker_asset = (
            BORI_GRADE_ASSET_KEYS[bori_grade]
            if bori_grade is not None
            else None
        )
        special_marker_asset = (
            grade_marker_asset
            if grade_marker_asset is not None
            else "bori-base"
            if wildlife["monsterCode"] == 22
            else None
        )
        map_position_track = deepcopy(wildlife["mapPositionTrack"])
        map_movement_mode = wildlife["mapMovementMode"]
        if special_marker_asset is not None:
            observed_mobile_track = (
                wildlife.get("displayPositionTrack")
                or wildlife.get("movementTrack")
                or []
            )
            if observed_mobile_track:
                map_position_track = deepcopy(observed_mobile_track)
                map_movement_mode = "special-mobile-observed-track"
        marker_color_for_display = (
            "#ff344d"
            if wildlife["monsterCode"] == 11
            else "#ffffff"
            if special_marker_asset is not None
            else marker_color
            if marker_color is not None
            else UNKNOWN_WILDLIFE_MARKER_COLOR
        )
        public_wildlife.append({
            "publicWildlifeId": public_id,
            "publicWildlifeGroupId": public_group_id,
            "publicLabel": f"W{public_id:04d}",
            "monsterCode": wildlife["monsterCode"],
            "monsterName": (
                f"보리 · {BORI_GRADE_LABELS[bori_grade]}"
                if bori_grade is not None
                else "보리"
                if wildlife["monsterCode"] == 22
                else wildlife["monsterName"]
            ),
            "boriGrade": bori_grade,
            "boriGradeLabel": (
                BORI_GRADE_LABELS[bori_grade]
                if bori_grade is not None
                else None
            ),
            "boriGradeStatus": (
                "decoded-exact-Bori-lifecycle-bracket-BoriSupplyBoxSnapshot-boxGrade"
                if bori_grade is not None
                else "unavailable-no-unique-Bori-lifecycle-grade-snapshot"
                if wildlife["monsterCode"] == 22
                else "not-applicable"
            ),
            "boriGradeRevealTick": (
                bori_supply_box["firstSeenTick"]
                if bori_supply_box is not None
                else None
            ),
            "mutated": wildlife["mutated"],
            "assetKey": wildlife["assetKey"],
            "assetStatus": wildlife["assetStatus"],
            "spawnTick": wildlife["spawnTick"],
            "initialAlive": wildlife["initialAlive"],
            "initialHp": wildlife["initialHp"],
            "deathTick": wildlife["deathTick"],
            "destroyTick": wildlife["destroyTick"],
            "despawnTick": wildlife["despawnTick"],
            "killerPublicPlayerId": private_to_public.get(
                wildlife.get("killerPlayerObjectId")
            ),
            "movementTrack": deepcopy(wildlife["movementTrack"]),
            "movementAnchorCount": wildlife["movementAnchorCount"],
            "movementStatus": wildlife["movementStatus"],
            "displayPositionTrack": deepcopy(wildlife["displayPositionTrack"]),
            "displayPositionAnchorCount": wildlife["displayPositionAnchorCount"],
            "displayPositionStatus": wildlife["displayPositionStatus"],
            "displayGroupStatus": wildlife["displayGroupStatus"],
            "mapPositionTrack": map_position_track,
            "mapPositionAnchorCount": len(map_position_track),
            "mapMovementMode": map_movement_mode,
            "ownerPublicPlayerId": owner_id,
            "ownerStatus": owner_status,
            "mapMarkerStyle": (
                "neutral-monster-red-dot"
                if wildlife["monsterCode"] == 11
                else "special-icon"
                if special_marker_asset is not None
                else "mutant-purple-outline"
                if wildlife["mutated"]
                else "species-triangle"
            ),
            "mapMarkerAssetKey": special_marker_asset,
            "mapMarkerAssetTimeline": (
                [[wildlife["spawnTick"], "bori-base"]]
                + (
                    [[bori_supply_box["firstSeenTick"], grade_marker_asset]]
                    if bori_supply_box is not None
                    else []
                )
                if wildlife["monsterCode"] == 22
                else []
            ),
            "mapMarkerColor": marker_color_for_display,
            "mapMarkerColorStatus": (
                "user-confirmed-neutral-monster-red"
                if wildlife["monsterCode"] == 11
                else "hash-verified-client-special-marker"
                if special_marker_asset is not None
                else "user-confirmed-species-palette"
                if marker_color is not None
                else "unavailable-no-exact-species-marker-palette"
            ),
            "mapMarkerPositionMeaning": (
                "presentation-only-separated-shared-group-center"
                if map_movement_mode == "fixed-scratch-group-center"
                else "same-as-map-position-track"
            ),
            "wireStatus": wildlife["wireStatus"],
        })
    wildlife_assets = {
        "status": "not-embedded-shape-markers-only",
        "icons": {},
    }
    public_tactical_pings = []
    excluded_tactical_ping_count = 0
    for ping in private_catalog.get("tacticalPings", []):
        expected_type_name = TACTICAL_PING_ENUM_NAMES.get(ping.get("type"))
        if expected_type_name is None or ping.get("typeName") != expected_type_name:
            raise ValueError(
                "CmdPing type does not match the exact TacticalPingType enum"
            )
        if ping["type"] in HYPERLOOP_TACTICAL_PING_TYPES:
            excluded_tactical_ping_count += 1
            continue
        position = deepcopy(ping["position"])
        map_position_status = None
        if (
            isinstance(position, list)
            and len(position) == 2
            and all(
                isinstance(value, (int, float)) and math.isfinite(float(value))
                for value in position
            )
            and not point_is_in_public_map_space(
                {**private_map, "secondarySpaces": secondary_map_spaces}, position
            )
        ):
            map_position_status = "unavailable-outside-calibrated-map-spaces"
        public_tactical_pings.append({
            "publicPingId": len(public_tactical_pings) + 1,
            "tick": ping["tick"],
            "type": ping["type"],
            "label": TACTICAL_PING_LABELS.get(ping["type"], ping["typeName"]),
            "assetKey": TACTICAL_PING_ASSET_KEYS.get(ping["type"]),
            "assetStatus": (
                "hash-verified-client-map-ping"
                if ping["type"] in TACTICAL_PING_ASSET_KEYS
                else "unavailable-no-exact-client-map-ping"
            ),
            "sourceClass": "player-issued-tactical-ping",
            "senderPublicPlayerId": private_to_public.get(
                ping.get("senderObjectId")
            ),
            "position": position,
            "status": "decoded-exact-whole-match-tactical-ping",
            **(
                {"mapPositionStatus": map_position_status}
                if map_position_status is not None
                else {}
            ),
        })
    fps = private_meta.get("targetFrameRate", 60)
    if not isinstance(fps, (int, float)) or not math.isfinite(float(fps)) or fps <= 0:
        fps = 60
    def static_object_expire_tick(row: dict[str, Any]) -> int | None:
        if row.get("category") not in {"surveillance-camera", "control-lens", "recon-orb"}:
            return None
        expire_timer = row.get("expireTimer")
        if (
            not isinstance(expire_timer, (int, float))
            or not math.isfinite(float(expire_timer))
            or expire_timer <= 0
        ):
            return None
        first_seen_tick = row.get("firstSeenTick")
        if not isinstance(first_seen_tick, int):
            return None
        expire_tick = int(round(first_seen_tick + float(expire_timer) * float(fps)))
        if expire_tick < first_seen_tick:
            return None
        return expire_tick

    def static_object_visible_end(
        row: dict[str, Any],
    ) -> tuple[int | None, str, int | None]:
        expire_tick = static_object_expire_tick(row)
        death_tick = row.get("deathTick")
        if row.get("category") == "recon-orb" and isinstance(death_tick, int):
            other_ends = [v for v in (row.get("destroyTick"), expire_tick) if isinstance(v, int)]
            if not other_ends or death_tick <= min(other_ends):
                return death_tick, "decoded-exact-CmdDead", expire_tick
        destroy_tick = row.get("destroyTick")
        if isinstance(destroy_tick, int) and expire_tick is not None:
            if expire_tick <= destroy_tick:
                return (
                    expire_tick,
                    "derived-exact-first-seen-plus-expireTimer-before-CmdDestroy",
                    expire_tick,
                )
            return (
                destroy_tick,
                "decoded-exact-CmdDestroy-before-expireTimer",
                expire_tick,
            )
        if isinstance(destroy_tick, int):
            return destroy_tick, "decoded-exact-CmdDestroy", expire_tick
        if expire_tick is not None:
            return (
                expire_tick,
                "derived-exact-first-seen-plus-expireTimer-no-CmdDestroy",
                expire_tick,
            )
        return None, "unavailable-no-visible-end-evidence", expire_tick

    public_world_objects = []
    for row in private_world_map.get("staticObjects", []):
        if row.get("position") is None:
            continue
        if row.get("category") == "bori-supply-box":
            # Its exact grade has already been joined to the one visible Bori
            # box monster marker. Drawing this snapshot separately would
            # duplicate the same moving encounter.
            continue
        owner_id, owner_status = owner_public_id(
            row["objectId"], row.get("ownerId")
        )
        visible_end_tick, visible_end_status, expire_tick = (
            static_object_visible_end(row)
        )
        public_world_objects.append({
            "publicWorldObjectId": len(public_world_objects) + 1,
            "category": row["category"],
            "firstSeenTick": row["firstSeenTick"],
            "destroyTick": row.get("destroyTick"),
            "expireTick": expire_tick,
            "visibleEndTick": visible_end_tick,
            "visibleEndStatus": visible_end_status,
            "position": deepcopy(row["position"]),
            "positionStatus": row["positionStatus"],
            "movementTrack": deepcopy(row.get("movementTrack", [])),
            "movementAnchorCount": int(row.get("movementAnchorCount", 0)),
            "movementStatus": row.get(
                "movementStatus", "not-applicable-static-world-object"
            ),
            "assetKey": (
                "lumi-credit-rich"
                if row.get("category") == "lumi"
                and row.get("creditRich") is True
                else "lumi-normal"
                if row.get("category") == "lumi"
                else row["category"]
            ),
            "guideRobotState": row.get("guideRobotState"),
            "creditRich": row.get("creditRich"),
            "guideRobotStateTimeline": deepcopy(
                row.get("guideRobotStateTimeline", [])
            ),
            "guideRobotStatus": row.get("guideRobotStatus"),
            "ownerPublicPlayerId": owner_id,
            "ownerStatus": owner_status,
            "status": row["wireStatus"],
        })
    vision_counts: dict[int, dict[str, int]] = {
        player["publicPlayerId"]: {
            "physicalCameraCount": 0,
            "physicalCameraPositionedCount": 0,
            "dronePositionedCount": 0,
        }
        for player in players
    }
    for row in private_world_map.get("staticObjects", []):
        category = row.get("category")
        if category not in {"surveillance-camera", "control-lens"}:
            continue
        owner_id, _owner_status = owner_public_id(
            row["objectId"], row.get("ownerId")
        )
        if owner_id not in vision_counts:
            continue
        if category == "surveillance-camera":
            vision_counts[owner_id]["physicalCameraCount"] += 1
            if row.get("position") is not None:
                vision_counts[owner_id]["physicalCameraPositionedCount"] += 1
        elif row.get("position") is not None:
            vision_counts[owner_id]["dronePositionedCount"] += 1
    vision_object_rows = []
    for player in players:
        public_id = player["publicPlayerId"]
        result = player["result"]
        counts = vision_counts[public_id]
        camera_result_count = int(result.get("addTelephotoCamera") or 0)
        drone_result_count = int(result.get("useReconDrone") or 0) + int(
            result.get("useEmpDrone") or 0
        )
        auxiliary_sight_count = camera_result_count - counts[
            "physicalCameraCount"
        ]
        if auxiliary_sight_count < 0:
            raise ValueError(
                "physical camera count cannot exceed addTelephotoCamera result"
            )
        if counts["dronePositionedCount"] > drone_result_count:
            raise ValueError(
                "control-lens marker count cannot exceed recon/EMP result"
            )
        vision_object_rows.append({
            "publicPlayerId": public_id,
            "cameraResultCounter": camera_result_count,
            "physicalCameraCount": counts["physicalCameraCount"],
            "physicalCameraPositionedCount": counts[
                "physicalCameraPositionedCount"
            ],
            "auxiliarySightCount": auxiliary_sight_count,
            "droneUseCounter": drone_result_count,
            "dronePositionedCount": counts["dronePositionedCount"],
            "unpositionedPhysicalCameraCount": (
                counts["physicalCameraCount"]
                - counts["physicalCameraPositionedCount"]
            ),
            "unpositionedDroneCount": (
                drone_result_count - counts["dronePositionedCount"]
            ),
            "status": "derived-exact-vision-counter-to-map-marker-summary",
        })
    public_world_events = []
    for row in private_world_map.get("events", []):
        public_world_events.append({
            "publicWorldEventId": len(public_world_events) + 1,
            "kind": row["kind"],
            "warningTick": row["warningTick"],
            "activeTick": row["activeTick"],
            "endTick": row["endTick"],
            "endStatus": row.get("endStatus"),
            "position": deepcopy(row["position"]),
            "positionStatus": row["positionStatus"],
            "warningStatus": row["warningStatus"],
            "activeStatus": row["activeStatus"],
            "classificationStatus": row["classificationStatus"],
        })
    movement_pings = [
        {
            "publicMovementPingId": index,
            "tick": row["tick"],
            "kind": row["noiseTypeName"],
            "label": MOVEMENT_PING_LABELS[row["noiseTypeName"]],
            "assetKey": "movement-ping",
            "sourceClass": "automatic-movement-notice",
            "position": deepcopy(row["sourcePosition"]),
            "status": "decoded-exact-automatic-movement-noise",
        }
        for index, row in enumerate(
            (
                row
                for row in private_catalog.get("noiseNotifications", [])
                if row.get("noiseTypeName") in {"HyperLoopExit", "VLSLanding"}
            ),
            start=1,
        )
    ]
    for ping in movement_pings:
        if not point_is_in_public_map_space({**private_map, "secondarySpaces": secondary_map_spaces}, ping["position"]):
            ping["mapPositionStatus"] = "unavailable-outside-calibrated-map-spaces"
    private_phase_clock = private_catalog.get("phaseClock", {})
    public_restriction_updates = [
        {
            "tick": row["tick"],
            "day": row["day"],
            "dayNight": row["dayNight"],
            "dayNightName": row["dayNightName"],
            "phase": row["phase"],
            "remainSeconds": row["remainSeconds"],
            "areas": [
                {
                    "areaCode": area["areaCode"],
                    "state": area["state"],
                    "stateName": area["stateName"],
                    "status": "decoded-exact-lumia-area-restriction-state",
                }
                for area in row.get("areas", [])
            ],
            "status": "decoded-exact-restricted-area-clock",
        }
        for row in private_phase_clock.get("restrictionUpdates", [])
    ]
    public_gameplay_phase_updates = [
        {
            "tick": row["tick"],
            "gamePlayPhase": row["gamePlayPhase"],
            "gamePlayPhaseName": row["gamePlayPhaseName"],
            "remainSeconds": row["remainSeconds"],
            "startSeconds": row["startSeconds"],
            "endSeconds": row["endSeconds"],
            "status": "decoded-exact-gameplay-phase-clock",
        }
        for row in private_phase_clock.get("gamePlayPhaseUpdates", [])
    ]
    compatibility = private_meta["compatibility"]
    private_capability_catalog = private_catalog.get("characterCapabilityCatalog", {})
    private_projectile_catalog = private_catalog.get("projectileSkillCatalog", {})
    private_projectile_runtime = private_catalog.get(
        "projectileHitRateRuntime", {}
    )
    projectile_authority_absent = (
        not private_projectile_catalog
        and not private_projectile_runtime
        and all(
            "projectileHitRates" not in player
            for player in private_players
        )
    )
    private_static_game_data = private_catalog.get("dataSources", {}).get(
        "staticGameData", {}
    )
    if (
        private_capability_catalog.get("status")
        != "exact-replay-version-official-gameDb-character-capabilities"
        or private_capability_catalog.get("fallbackUsed") is not False
        or private_static_game_data.get("status")
        != "exact-replay-header-official-gameDb"
        or private_static_game_data.get("fallbackUsed") is not False
        or private_static_game_data.get("characterCapabilityCount")
        != private_capability_catalog.get("characterCount")
    ):
        raise ValueError("private character capability authority is invalid")
    if not projectile_authority_absent and (
        private_projectile_catalog.get("status")
        != "exact-replay-version-whole-roster-projectile-skill-catalog"
        or private_projectile_catalog.get("fallbackUsed") is not False
        or private_projectile_runtime.get("status")
        != "derived-exact-projectile-spawn-and-collision-hit-rates"
        or private_projectile_runtime.get("fallbackUsed") is not False
        or private_projectile_runtime.get("scope")
        != "confirmed-player-engagement-only"
        or private_projectile_runtime.get("scopeEvidence")
        != "derived-exact-team-pvp-episode-intersect-own-combat-state"
        or private_static_game_data.get("projectileSkillCount")
        != private_projectile_catalog.get("skillCount")
        or private_static_game_data.get("projectileDefinitionCount")
        != private_projectile_catalog.get("projectileDefinitionCount")
    ):
        raise ValueError("private projectile hit-rate authority is invalid")
    if projectile_authority_absent:
        public_projectile_hit_rates = {
            "status": "unavailable-private-evidence-predates-projectile-runtime",
            "catalogStatus": "unavailable",
            "catalogCharacterCount": 0,
            "catalogSkillCount": 0,
            "projectileDefinitionCount": 0,
            "projectileSpawnCount": 0,
            "projectileCollisionCount": 0,
            "linkWindowTicks": None,
            "minimumObservations": None,
            "scope": "confirmed-player-engagement-only",
            "scopeEvidence": "unavailable-private-evidence-predates-projectile-runtime",
            "calculableSkillCount": 0,
            "interpretationBoundary": (
                "not-calculated-without-projectile-runtime-evidence"
            ),
            "fallbackUsed": False,
        }
    else:
        public_projectile_hit_rates = {
            "status": private_projectile_runtime["status"],
            "catalogStatus": private_projectile_catalog["status"],
            "catalogCharacterCount": private_projectile_catalog[
                "characterCount"
            ],
            "catalogSkillCount": private_projectile_catalog["skillCount"],
            "projectileDefinitionCount": private_projectile_catalog[
                "projectileDefinitionCount"
            ],
            "projectileSpawnCount": private_projectile_runtime[
                "projectileSpawnCount"
            ],
            "projectileCollisionCount": private_projectile_runtime[
                "projectileCollisionCount"
            ],
            "linkWindowTicks": private_projectile_runtime["linkWindowTicks"],
            "minimumObservations": private_projectile_runtime[
                "minimumObservations"
            ],
            "scope": private_projectile_runtime["scope"],
            "scopeEvidence": private_projectile_runtime["scopeEvidence"],
            "calculableSkillCount": sum(
                row.get("hitRateCalculable") is True
                for player in players
                for row in player["projectileHitRates"]
            ),
            "interpretationBoundary": (
                "verified projectile-shot or skill-cast hit evidence during "
                "confirmed player engagement only; denominator units never mixed"
            ),
            "fallbackUsed": False,
        }
    event_areas = build_event_area_summary(
        field_report,
        client_version=private_meta["clientVersion"],
    )
    combat_judgment_summary = deepcopy(private_catalog["combatJudgment"])
    combat_judgment_summary["simultaneousEntryWindowSeconds"] = (
        SIMULTANEOUS_ENTRY_WINDOW_SECONDS
    )
    public_catalog = {
        "format": PUBLIC_FORMAT,
        "meta": {
            "reportId": report_id,
            "clientVersion": private_meta["clientVersion"],
            "matchingMode": private_meta["matchingMode"],
            "matchingTeamMode": private_meta["matchingTeamMode"],
            "matchMode": private_meta["matchMode"],
            "matchModeLabel": private_meta["matchModeLabel"],
            "compatibility": {
                "clientLayoutStatus": compatibility["clientLayout"]["status"],
                "matchModeStatus": compatibility["matchMode"]["status"],
                "fallbackUsed": False,
            },
            "firstTick": private_meta["firstTick"],
            "lastTick": private_meta["lastTick"],
            "targetFrameRate": private_meta["targetFrameRate"],
            "playerCount": len(players),
        },
        "privacy": {
            "status": "public-derived-anonymized-v1",
            "reportIdRandomized": True,
            "nicknameIncluded": False,
            "accountIdentifiersIncluded": False,
            "matchIdentifierIncluded": False,
            "rawEventStreamIncluded": False,
            "sourceFilenamesIncluded": False,
            "sourceHashesIncluded": False,
            "rawReplayIncluded": False,
            "deletionReference": report_id,
            "deletionContactUrl": deletion_contact_url,
            "unofficialServiceNotice": (
                "ERCraft는 님블뉴런의 공식 서비스가 아니며, 분석 정확도나 "
                "리플레이 형식 호환성을 님블뉴런이 보증하지 않습니다."
            ),
        },
        "map": {
            "image": deepcopy(private_map["image"]),
            "coordinateSpace": deepcopy(private_map["coordinateSpace"]),
            "imageContentRect": deepcopy(private_map["imageContentRect"]),
            "imageAlignment": deepcopy(private_map["imageAlignment"]),
            "imageDataUrl": private_map["imageDataUrl"],
            "imageStatus": private_map["imageStatus"],
            "playbackStatus": private_map["playbackStatus"],
            "movementStatus": private_map["movementStatus"],
            "movementAnchorCount": private_map["movementAnchorCount"],
            "plannedPathCommandCount": private_map["plannedPathCommandCount"],
            "plannedPathNodeCount": private_map["plannedPathNodeCount"],
            "projection": deepcopy(private_map["projection"]),
            "secondarySpaces": secondary_map_spaces,
        },
        "wildlife": {
            "status": (
                "exact-individual-lifecycle-with-one-graded-bori-marker-"
                "per-paired-encounter-and-crow-movement"
            ),
            "instanceCount": len(public_wildlife),
            "excludedBoriCompanionCount": excluded_bori_companion_count,
            "groupCount": len(public_wildlife_group_ids),
            "movementAnchorCount": sum(
                row["movementAnchorCount"] for row in public_wildlife
            ),
            "displayPositionAnchorCount": sum(
                row["displayPositionAnchorCount"] for row in public_wildlife
            ),
            "mapPositionAnchorCount": sum(
                row["mapPositionAnchorCount"] for row in public_wildlife
            ),
            "positionedInstanceCount": sum(
                bool(row["displayPositionTrack"]) for row in public_wildlife
            ),
            "deadGhostSeconds": 0,
            "renderMode": "individual-living-instance-markers",
            "assets": wildlife_assets,
            "instances": public_wildlife,
        },
        "characterAssets": character_assets,
        "itemAssets": item_assets,
        "skillAssets": skill_assets,
        "utilitySkillAssets": utility_skill_assets,
        "mapMarkerAssets": map_marker_assets,
        "uiAssets": {
            "gadgetPoint": {
                "imageDataUrl": GADGET_POINT_ICON_DATA_URL,
                "status": "user-supplied-exact-icon",
            },
        },
        "worldMap": {
            "status": private_world_map["status"],
            "fallbackUsed": False,
            "renderLayerOrder": [
                "wildlife", "players", "world-objects", "tactical-pings"
            ],
            "warningLeadSeconds": private_world_map["warningLeadSeconds"],
            "staticObjects": public_world_objects,
            "timeline": public_world_events,
            "movementPings": movement_pings,
            "movementPingDisplaySeconds": MOVEMENT_PING_DISPLAY_SECONDS,
            "transportModeTransitions": deepcopy(
                private_world_map.get("transportModeTransitions", [])
            ),
            "restrictionAreaShapes": restriction_area_shapes,
            "unpositionedTimelineCount": private_world_map[
                "unpositionedTimelineCount"
            ],
        },
        "visionObjects": {
            "status": "derived-exact-vision-counter-to-map-marker-summary",
            "fallbackUsed": False,
            "interpretation": (
                "addTelephotoCamera can include auxiliary sight summons; "
                "physical camera markers are counted separately"
            ),
            "players": vision_object_rows,
        },
        "tacticalPings": {
            "status": "decoded-exact-whole-match-tactical-pings",
            "selectedPlayerFilter": False,
            "automaticNoiseIncluded": False,
            "hyperloopPingIncluded": False,
            "excludedHyperloopPingCount": excluded_tactical_ping_count,
            "displaySeconds": TACTICAL_PING_DISPLAY_SECONDS,
            "count": len(public_tactical_pings),
            "items": public_tactical_pings,
        },
        "phaseClock": {
            "status": "decoded-exact-day-night-phase-and-remain-time",
            "restrictionUpdates": public_restriction_updates,
            "gamePlayPhaseUpdates": public_gameplay_phase_updates,
        },
        "eventAreas": event_areas,
        "characterCapabilities": {
            "status": private_capability_catalog["status"],
            "sourceUrl": private_static_game_data["url"],
            "replayClientVersion": private_meta["clientVersion"],
            "catalogCharacterCount": private_capability_catalog[
                "characterCount"
            ],
            "matchCharacterCount": len({
                player["characterCode"] for player in players
            }),
            "interpretationBoundary": (
                "static-base-capability-not-runtime-source-or-final-duration"
            ),
            "fallbackUsed": False,
        },
        "projectileHitRates": public_projectile_hit_rates,
        "teamCombatLog": team_combat_log,
        "itemCatalog": item_catalog,
        "combatJudgment": combat_judgment_summary,
        "deathReview": deepcopy(private_catalog["deathReview"]),
        "growthTempo": deepcopy(private_catalog["growthTempo"]),
        "objectivePreparation": deepcopy(private_catalog["objectivePreparation"]),
        "skillOperation": deepcopy(private_catalog["skillOperation"]),
        "sceneCoaching": scene_coaching_summary,
        "players": players,
        "limitations": [
            "경기 종료 피해 합계와 유형별 분해는 exact 결과입니다. 교전별 피해는 그 교전의 모든 대인 피해 패킷에 숫자가 있을 때만 확정값을 표시하고, 하나라도 비어 있으면 확인 불가로 표시합니다. 숫자가 남은 일부 합계는 검증 근거로만 보존합니다.",
            "실험체 전투 도구는 리플레이 헤더가 직접 지정한 공식 gameDb 버전의 정적 기본 정의입니다. CC 기본 시간은 실제 적용 시간이 아니며 스킬 레벨·강화·상태 저항·모드 보정을 임의로 계산하지 않습니다.",
            "교전 중 CC 시간은 같은 리플레이 버전 gameDb의 CC 상태 코드와 상태 추가 패킷의 런타임 지속시간, 갱신·초기화·해제 시각을 연결해 계산합니다. 상대 시전자와 exact 종료 근거를 모두 확인할 수 있을 때만 받은 CC·넣은 CC를 초 단위로 표시하고, 하나라도 빠지면 확인 불가로 표시합니다.",
            "CmdUpdateShield에는 시전자 필드가 없으므로 관측된 보호막을 특정 실험체나 스킬의 기여로 귀속하지 않습니다.",
            "개별 스킬 총피해는 피해 패킷과 스킬 시작을 정확히 연결할 식별자가 없어 계산하지 않습니다.",
            "지도 마커는 마지막 exact 위치를 다음 관측까지 유지하며 앵커 사이 이동을 만들지 않습니다.",
            "점선 경로는 이동 명령의 의도 경로이며 경로 노드별 도착 시각을 뜻하지 않습니다.",
            "야생동물은 개체마다 마커 하나와 독립된 생존 주기를 유지합니다. 같은 무리 중심 좌표를 공유한 개체는 화면에서 겹치지 않도록 표시만 조금 벌리며, 이를 실제 이동 좌표로 저장하지 않습니다.",
            "까마귀·루미·보리는 관측 이동을 이어 표시하고, 나머지는 무리 중심 또는 첫 근거 위치에 고정합니다. 사망하거나 파괴된 개체는 즉시 사라지며 끝까지 좌표 근거가 없는 개체는 지도에 만들지 않습니다.",
            f"전술 핑 목록은 전체 경기의 exact CmdPing만 사용하며 선택 플레이어 수신 여부를 추정하지 않습니다. 사람이 찍은 핑은 종류별 hash 검증 클라이언트 지도 아이콘으로 구분하고, exact HyperLoopExit/VLSLanding 위치는 별도의 이동 아이콘으로 {MOVEMENT_PING_DISPLAY_SECONDS}초간 표시합니다.",
            "운석·생명의 나무는 일정과 전체 스냅샷의 오브젝트 번호가 정확히 일치할 때만 표시하고, 같은 오브젝트의 CmdDestroy가 확인된 시각에 없앱니다. 파괴 기록이 없으면 획득을 추측하지 않고 경기 종료까지 표시한 채 확인 불가 상태를 남깁니다.",
            "모든 캐릭터 스킬은 투사체 여부·코드 후보·형태·적중 패킷·사용 분모·계산 가능 사유를 같은 리플레이 버전 기준으로 분류합니다. 이름만 비슷한 후보는 적중률로 승격하지 않습니다.",
            "적중률은 최소 3회 반복 관측과 완전한 분모가 확인된 수동 조준 스킬만 제공합니다. 배타적으로 연결된 단일·고정 다발 투사체는 적 플레이어와 충돌한 발사체 수/전체 발사체 수를 쓰고, 대상 목록이나 같은 버전 Skill·CharacterState에 유일하게 일치하는 피해 코드는 맞힌 시전/전체 교전 시전을 씁니다. 두 분모 단위는 합치지 않습니다. 교전 밖 사용과 야생동물·아군·지형 충돌은 제외하며 관통·폭발·설치·왕복·지속/지연 피해나 불완전한 액션 관측은 계산 불가입니다.",
            "교전별 적중률은 검증된 각 시도와 연결된 스킬 시작 시각을 해당 대인 교전 구간에 넣어 계산합니다. 경기 전체 적중률을 개별 교전에 복사하거나 추정하지 않습니다.",
            "교전 판단력은 exact 전투 상태와 적 대상 지정이 함께 확인된 대인 교전만 사용한 단일 경기 파생 지표입니다. 표적 지정은 적중을 뜻하지 않으며, 다른 경기나 전체 이용자 대비 등급으로 해석하지 않습니다.",
            "성장 템포의 레벨·장비 시각은 최초 exact 관측 시각입니다. 관측 사이의 실제 도달 시각을 보간하거나 다른 이용자 평균으로 대체하지 않습니다.",
            "오브젝트 준비의 20m 진입은 exact 오브젝트 시각·위치와 마지막 exact 이동 앵커를 사용한 관측입니다. 오브젝트 획득·처치·소유권을 뜻하지 않습니다.",
            "스킬 시작 기록은 CmdStartSkill 패킷 수입니다. 충전 스킬의 각 사용과 재시전 단계는 각각 한 건이며, 활성 유지 시간과 passive/state 시작은 더하지 않습니다.",
            "스킬 습관의 전투 중 쿨다운 비율은 exact 쿨다운 타이머와 게임 전투 상태 구간의 겹침입니다. 야생동물 전투를 포함할 수 있고, 남은 충전 수·CC·침묵·사망·대상·사거리·hold·copy 조건은 실제 사용 가능 여부로 보정하지 않습니다.",
            "지도 우측 장비·인벤토리는 CmdUpdateEquipment와 CmdUpdateInventoryForObserver의 exact 슬롯 변경만 현재 커서까지 적용합니다. 첫 관측 전 상태는 비어 있다고 추정하지 않고 관측 전으로 표시합니다.",
            "아이템 이미지는 exact 코드 매핑과 해시가 확인된 파일만 표시합니다. 매핑되지 않은 신규·변형 아이템은 비슷한 이미지로 대체하지 않고 이미지 미확인으로 남깁니다.",
            "스킬 남은 쿨다운은 exact 시작·수정·초기화 패킷 이후의 값을 재생 시간으로 감소시킨 파생 상태입니다. 첫 관측 전과 해석하지 않은 hold/copy 공백은 준비로 추정하지 않습니다.",
            "지도 우측 전투 로그는 선택한 실험체의 팀이 관여한 exact CmdKill·CmdDyingCondition·CmdDead만 시간순으로 표시합니다. CmdKill은 사망 그 자체가 아니므로 같은 시각의 다운과 결합되면 다운으로, 그렇지 않으면 처치 귀속으로 구분하며 공격자 근거가 없으면 비워 둡니다.",
        ],
    }
    assert_public_catalog(public_catalog)
    return public_catalog


def _walk_keys(value: Any, path: tuple[str, ...] = ()):
    if isinstance(value, dict):
        for key, child in value.items():
            yield path + (str(key),), str(key)
            yield from _walk_keys(child, path + (str(key),))
    elif isinstance(value, list):
        for index, child in enumerate(value):
            yield from _walk_keys(child, path + (str(index),))


def _valid_calculable_skill_hit_rate(row: dict[str, Any]) -> bool:
    attempts = row.get("attemptCount")
    hits = row.get("playerHitAttemptCount")
    rate = row.get("playerHitRate")
    if (
        not isinstance(attempts, int)
        or isinstance(attempts, bool)
        or attempts < 1
        or not isinstance(hits, int)
        or isinstance(hits, bool)
        or not 0 <= hits <= attempts
        or not isinstance(rate, (int, float))
        or isinstance(rate, bool)
        or not 0 <= rate <= 1
        or abs(float(rate) - round(hits / attempts, 6)) > 1e-9
    ):
        return False

    unit = row.get("hitRateUnit")
    packet = row.get("hitEvidencePacket", {})
    denominator = row.get("attemptDenominator", {})
    if unit == "projectile-shot":
        shapes = row.get("projectileShape", {}).get("values")
        per_cast = row.get("projectilesPerCast")
        if (
            row.get("projectileStatus")
            != "verified-observed-exclusive-cast-spawn-link"
            or shapes not in (["single"], ["multi"])
            or packet.get("packet")
            != "CmdProjectileCollision 투사체·대상 번호"
            or packet.get("status")
            != "decoded-exact-projectile-target-collision"
            or denominator.get("event") != "CmdSpawn 투사체 생성 번호"
            or row.get("duplicateCollisionPolicy")
            != "unique projectile-target pair"
        ):
            return False
        if shapes == ["single"]:
            return (
                per_cast in (None, 1)
                and attempts == row.get("castCount")
                and denominator.get("status")
                == (
                    "verified-exclusive-one-projectile-spawn-per-cast-"
                    "during-confirmed-player-engagement"
                )
            )
        return (
            isinstance(per_cast, int)
            and not isinstance(per_cast, bool)
            and per_cast >= 2
            and attempts == row.get("castCount") * per_cast
            and denominator.get("status")
            == (
                "verified-constant-multiple-projectile-spawns-per-cast-"
                "during-confirmed-player-engagement"
            )
        )

    if unit != "skill-cast" or attempts != row.get("castCount"):
        return False
    if (
        denominator.get("event") != "CmdStartSkill"
        or denominator.get("status")
        != (
            "verified-complete-action-covered-manual-skill-casts-"
            "during-confirmed-player-engagement"
        )
    ):
        return False
    method = row.get("hitRateMethod")
    if method == "exact-action-target-same-tick-corroborated":
        evidence = row.get("actionTargetEvidence", {})
        return (
            packet.get("packet")
            == "스킬 대상 기록 + 같은 시각의 대인 피해·투사체 충돌"
            and packet.get("status")
            == "decoded-exact-enemy-target-plus-same-tick-hit-corroboration"
            and evidence.get("castHitRateCalculable") is True
            and evidence.get("promotionStatus")
            == "verified-exact-action-target-cast-hit-rate"
            and evidence.get("combatCastsWithSameTickCorroboratedEnemyTargetCount")
            == hits
            and evidence.get("fallbackUsed") is False
        )
    if method == "exact-effect-code-skill-state-group-same-action-tick":
        evidence = row.get("exactEffectDamageEvidence", {})
        return (
            packet.get("packet")
            == (
                "CmdDamage 효과 코드 + 같은 버전 Skill·CharacterState 그룹 + "
                "같은 시각 스킬 동작"
            )
            and packet.get("status")
            == "verified-exact-versioned-skill-state-code-and-same-action-tick"
            and evidence.get("castHitRateCalculable") is True
            and evidence.get("promotionStatus")
            == "verified-exact-effect-code-cast-hit-rate"
            and evidence.get("combatCastsWithExactDamageCount") == hits
            and evidence.get("fallbackUsed") is False
        )
    return False


def _valid_compact_engagement_hit_rate(
    row: dict[str, Any], expected_status: str
) -> bool:
    attempts = row.get("attemptCount")
    hits = row.get("hitCount")
    rate = row.get("hitRate")
    base_valid = (
        isinstance(row.get("skillGroup"), int)
        and isinstance(row.get("skillId"), str)
        and bool(row["skillId"])
        and isinstance(row.get("family"), str)
        and bool(row["family"])
        and row.get("unit") in {"projectile-shot", "skill-cast"}
        and isinstance(attempts, int)
        and not isinstance(attempts, bool)
        and attempts > 0
        and isinstance(hits, int)
        and not isinstance(hits, bool)
        and 0 <= hits <= attempts
        and isinstance(rate, (int, float))
        and not isinstance(rate, bool)
        and abs(float(rate) - round(hits / attempts, 6)) <= 1e-9
        and row.get("status") == expected_status
        and row.get("fallbackUsed") is False
    )
    if not base_valid:
        return False
    outcomes = row.get("outcomes")
    if expected_status == "verified-exact-single-engagement-hit-rate":
        return (
            isinstance(outcomes, list)
            and len(outcomes) == attempts
            and all(
                isinstance(outcome, list)
                and len(outcome) == 4
                and type(outcome[0]) is int
                and type(outcome[1]) is int
                and outcome[1] in {0, 1}
                and type(outcome[2]) is int
                and outcome[2] >= outcome[0]
                and (
                    (outcome[1] == 1 and type(outcome[3]) is int and outcome[3] >= outcome[2])
                    or (outcome[1] == 0 and outcome[3] is None)
                )
                for outcome in outcomes
            )
            and sum(outcome[1] for outcome in outcomes) == hits
        )
    return outcomes is None


def assert_public_catalog(catalog: dict[str, Any]) -> None:
    if catalog.get("format") != PUBLIC_FORMAT:
        raise ValueError("public combat analysis format is invalid")
    for path, key in _walk_keys(catalog):
        if key.lower() in FORBIDDEN_KEYS:
            raise ValueError(f"forbidden private field in public catalog: {'.'.join(path)}")
    meta = catalog["meta"]
    if not REPORT_ID_PATTERN.fullmatch(meta["reportId"]):
        raise ValueError("public report id is invalid")
    if meta["playerCount"] != len(catalog["players"]) or not catalog["players"]:
        raise ValueError("public player count is invalid")
    if meta["compatibility"]["fallbackUsed"] is not False:
        raise ValueError("public projection cannot use a compatibility fallback")
    capability_meta = catalog.get("characterCapabilities", {})
    require_official_game_data_url(capability_meta.get("sourceUrl"))
    if (
        capability_meta.get("status")
        != "exact-replay-version-official-gameDb-character-capabilities"
        or capability_meta.get("fallbackUsed") is not False
        or capability_meta.get("replayClientVersion") != meta["clientVersion"]
        or not isinstance(capability_meta.get("catalogCharacterCount"), int)
        or capability_meta["catalogCharacterCount"] < meta["playerCount"]
        or not isinstance(capability_meta.get("matchCharacterCount"), int)
        or not 1 <= capability_meta["matchCharacterCount"] <= meta["playerCount"]
        or capability_meta.get("interpretationBoundary")
        != "static-base-capability-not-runtime-source-or-final-duration"
    ):
        raise ValueError("public character capability authority is invalid")
    projectile_meta = catalog.get("projectileHitRates", {})
    projectile_unavailable = (
        projectile_meta.get("status")
        == "unavailable-private-evidence-predates-projectile-runtime"
    )
    if projectile_unavailable:
        if (
            projectile_meta.get("catalogStatus") != "unavailable"
            or projectile_meta.get("fallbackUsed") is not False
            or projectile_meta.get("calculableSkillCount") != 0
            or projectile_meta.get("scope") != "confirmed-player-engagement-only"
            or projectile_meta.get("scopeEvidence")
            != "unavailable-private-evidence-predates-projectile-runtime"
            or projectile_meta.get("interpretationBoundary")
            != "not-calculated-without-projectile-runtime-evidence"
            or any(player.get("projectileHitRates") for player in catalog["players"])
        ):
            raise ValueError("public unavailable projectile boundary is invalid")
    elif (
        projectile_meta.get("status")
        != "derived-exact-projectile-spawn-and-collision-hit-rates"
        or projectile_meta.get("catalogStatus")
        != "exact-replay-version-whole-roster-projectile-skill-catalog"
        or projectile_meta.get("fallbackUsed") is not False
        or not isinstance(projectile_meta.get("catalogCharacterCount"), int)
        or projectile_meta["catalogCharacterCount"] < meta["playerCount"]
        or not isinstance(projectile_meta.get("catalogSkillCount"), int)
        or projectile_meta["catalogSkillCount"] <= 0
        or not isinstance(projectile_meta.get("projectileDefinitionCount"), int)
        or projectile_meta["projectileDefinitionCount"] <= 0
        or not isinstance(projectile_meta.get("projectileSpawnCount"), int)
        or projectile_meta["projectileSpawnCount"] < 0
        or not isinstance(projectile_meta.get("projectileCollisionCount"), int)
        or projectile_meta["projectileCollisionCount"] < 0
        or not isinstance(projectile_meta.get("linkWindowTicks"), int)
        or projectile_meta["linkWindowTicks"] < 0
        or not isinstance(projectile_meta.get("minimumObservations"), int)
        or projectile_meta["minimumObservations"] < 1
        or projectile_meta.get("scope") != "confirmed-player-engagement-only"
        or projectile_meta.get("scopeEvidence")
        != "derived-exact-team-pvp-episode-intersect-own-combat-state"
        or projectile_meta.get("interpretationBoundary")
        != (
            "verified projectile-shot or skill-cast hit evidence during confirmed "
            "player engagement only; denominator units never mixed"
        )
    ):
        raise ValueError("public projectile hit-rate authority is invalid")
    expected_ids = list(range(1, len(catalog["players"]) + 1))
    team_log = catalog.get("teamCombatLog", {})
    team_log_rows = team_log.get("items", [])
    allowed_team_log_statuses = {
        "decoded-exact-same-tick-CmdKill-and-CmdDyingCondition",
        "decoded-exact-CmdKill-credit-without-same-tick-down",
        "decoded-exact-CmdDyingCondition-attacker-unavailable",
        "decoded-exact-CmdDead-with-player-finisher",
        "decoded-exact-CmdDead-finisher-unavailable",
    }
    if (
        team_log.get("status") != "decoded-exact-team-kill-down-death-feed"
        or team_log.get("displaySeconds") != TEAM_COMBAT_LOG_DISPLAY_SECONDS
        or team_log.get("fallbackUsed") is not False
        or not isinstance(team_log_rows, list)
        or team_log_rows
        != sorted(team_log_rows, key=lambda row: (row["tick"], row["sequence"]))
        or [row.get("publicEventId") for row in team_log_rows]
        != list(range(1, len(team_log_rows) + 1))
        or any(
            row.get("kind") not in {"down", "kill", "death"}
            or not isinstance(row.get("tick"), int)
            or not meta["firstTick"] <= row["tick"] <= meta["lastTick"]
            or not isinstance(row.get("sequence"), int)
            or row.get("victimPublicPlayerId") not in expected_ids
            or (
                row.get("attackerPublicPlayerId") is not None
                and row["attackerPublicPlayerId"] not in expected_ids
            )
            or (
                row.get("downAttackerPublicPlayerId") is not None
                and row["downAttackerPublicPlayerId"] not in expected_ids
            )
            or row.get("attributionStatus") not in allowed_team_log_statuses
            for row in team_log_rows
        )
    ):
        raise ValueError("public team combat log is invalid")
    marker_assets = catalog.get("mapMarkerAssets", {})
    required_marker_keys = {
        "surveillance-camera", "control-lens", "recon-orb", "movement-ping", "campfire",
        "kiosk", "hyperloop", "cctv", "vls", "meteor-warning", "meteor",
        "tree-of-life-warning", "tree-of-life", "air-supply-rare-warning",
        "air-supply-rare", "air-supply-epic-warning", "air-supply-epic",
        "air-supply-legend-warning", "air-supply-legend",
        "air-supply-mythic-warning", "air-supply-mythic", "rift-warning",
        "rift", "gold-cube", "wickeline-warning", "wickeline",
        "alpha-warning", "alpha", "omega-warning", "omega",
        "lumi-normal", "lumi-battle", "lumi-credit-rich",
        "bori-base", "bori-rare", "bori-epic", "bori-legend", "bori-mythic",
        "rift-map-background",
        "tactical-ping-run", "tactical-ping-warning",
        "tactical-ping-escape", "tactical-ping-help",
        "tactical-ping-lets-join", "tactical-ping-enemy-vision",
        "tactical-ping-need-vision", "tactical-ping-all-in",
        "tactical-ping-target", "tactical-ping-select",
        "tactical-ping-fallback",
    }
    if (
        marker_assets.get("status")
        != "approved-hash-verified-local-map-marker-set"
        or set(marker_assets.get("icons", {})) != required_marker_keys
        or any(
            not row.get("imageDataUrl", "").startswith("data:image/")
            for row in marker_assets.get("icons", {}).values()
        )
        or any(
            ("pixelSize" in row or "anchorPixel" in row)
            and (
                not isinstance(row.get("pixelSize"), list)
                or not isinstance(row.get("anchorPixel"), list)
                or len(row["pixelSize"]) != 2
                or len(row["anchorPixel"]) != 2
                or not all(
                    isinstance(value, (int, float)) and value >= 0
                    for value in row["pixelSize"] + row["anchorPixel"]
                )
            )
            for row in marker_assets.get("icons", {}).values()
        )
    ):
        raise ValueError("public map marker assets are invalid")
    world_map = catalog.get("worldMap", {})
    if world_map.get("fallbackUsed") is not False:
        raise ValueError("public world map cannot use fallback data")
    def point_is_in_known_map_space(position):
        return point_is_in_public_map_space(catalog.get("map", {}), position)
    if world_map.get("renderLayerOrder") != [
        "wildlife", "players", "world-objects", "tactical-pings"
    ]:
        raise ValueError("public world map render-layer order is invalid")
    if world_map.get("warningLeadSeconds") != 60:
        raise ValueError("public world map warning lead is invalid")
    static_rows = world_map.get("staticObjects", [])
    if any(
        row.get("category") not in {
            "surveillance-camera", "control-lens", "recon-orb", "cctv", "hyperloop",
            "kiosk", "campfire", "vls", "gold-cube", "lumi",
        }
        or not isinstance(row.get("firstSeenTick"), int)
        or (
            row.get("destroyTick") is not None
            and (
                not isinstance(row["destroyTick"], int)
                or row["destroyTick"] < row["firstSeenTick"]
            )
        )
        or (
            row.get("expireTick") is not None
            and (
                not isinstance(row["expireTick"], int)
                or row["expireTick"] < row["firstSeenTick"]
            )
        )
        or (
            row.get("visibleEndTick") is not None
            and (
                not isinstance(row["visibleEndTick"], int)
                or row["visibleEndTick"] < row["firstSeenTick"]
            )
        )
        or row.get("visibleEndStatus") not in {
            "decoded-exact-CmdDestroy",
            "decoded-exact-CmdDestroy-before-expireTimer",
            "decoded-exact-CmdDead",
            "derived-exact-first-seen-plus-expireTimer-before-CmdDestroy",
            "derived-exact-first-seen-plus-expireTimer-no-CmdDestroy",
            "unavailable-no-visible-end-evidence",
        }
        or not isinstance(row.get("position"), list)
        or len(row["position"]) != 2
        or not all(isinstance(value, (int, float)) for value in row["position"])
        or row.get("ownerStatus") not in {
            "decoded-exact-spawn-owner-chain",
            "decoded-exact-snapshot-owner-id",
            "unavailable-no-resolved-player-owner",
        }
        or (
            row.get("ownerPublicPlayerId") is not None
            and row["ownerPublicPlayerId"] not in expected_ids
        )
        for row in static_rows
    ):
        raise ValueError("public static world object is invalid")
    event_rows = world_map.get("timeline", [])
    if any(
        row.get("kind") not in {
            "meteor", "tree-of-life", "air-supply-rare", "air-supply-epic",
            "air-supply-legend", "air-supply-mythic", "rift", "wickeline",
            "alpha", "omega",
        }
        or not all(isinstance(row.get(key), int) for key in (
            "warningTick", "activeTick", "endTick"
        ))
        or not row["warningTick"] <= row["activeTick"] < row["endTick"]
        or not isinstance(row.get("position"), list)
        or len(row["position"]) != 2
        or not all(isinstance(value, (int, float)) for value in row["position"])
        or (
            row.get("kind") in {"meteor", "tree-of-life"}
            and row.get("endStatus") not in {
                "decoded-exact-CmdDestroy-for-same-resource-object",
                "decoded-exact-same-resource-object-collected-snapshot-at-first-observation",
                "unavailable-no-exact-resource-collection-lifecycle-visible-until-match-end",
            }
        )
        for row in event_rows
    ):
        raise ValueError("public world event is invalid")
    movement_rows = world_map.get("movementPings", [])
    if movement_rows != sorted(movement_rows, key=lambda row: row["tick"]):
        raise ValueError("public movement pings must be chronological")
    if world_map.get("movementPingDisplaySeconds") != MOVEMENT_PING_DISPLAY_SECONDS or any(
        not isinstance(row.get("tick"), int)
        or row.get("kind") not in MOVEMENT_PING_LABELS
        or row.get("label") != MOVEMENT_PING_LABELS[row["kind"]]
        or row.get("assetKey") != "movement-ping"
        or row.get("sourceClass") != "automatic-movement-notice"
        or not isinstance(row.get("position"), list)
        or len(row["position"]) != 2
        or not all(isinstance(value, (int, float)) and math.isfinite(value) for value in row["position"])
        or (not point_is_in_known_map_space(row["position"]) and row.get("mapPositionStatus") != "unavailable-outside-calibrated-map-spaces")
        for row in movement_rows
    ):
        raise ValueError("public movement ping is invalid")
    transitions = world_map.get("transportModeTransitions", [])
    if transitions != sorted(transitions, key=lambda row: row["tick"]) or any(
        not isinstance(row.get("tick"), int) or row.get("mode") != "vls"
        for row in transitions
    ):
        raise ValueError("public transport transition is invalid")
    restriction_shapes = world_map.get("restrictionAreaShapes", {})
    if (
        restriction_shapes.get("status")
        != "reference-exact-lumia-area-shapes-local-only"
        or restriction_shapes.get("viewBox") != [0, 0, 1544, 1962]
        or restriction_shapes.get("sourceMap") != {"width": 772, "height": 981}
        or set(restriction_shapes.get("paths", {}))
        != {str(code) for code in range(10, 201, 10)}
    ):
        raise ValueError("public restriction-area shapes are invalid")
    rift_status_version = (
        RIFT_MAP_CALIBRATION_BY_CLIENT_VERSION
        .get(catalog.get("meta", {}).get("clientVersion"), {})
        .get("statusVersion")
    )
    for space in catalog["map"].get("secondarySpaces", []):
        if space.get("spaceId") == "azure-1080":
            from decoder.azure_map_space import validate_azure_space
            validate_azure_space(space, catalog["meta"]["clientVersion"])
            continue
        bounds = space.get("bounds", {})
        projection = space.get("projection", {})
        if (
            not re.fullmatch(r"rift-\d+", space.get("spaceId", ""))
            or space.get("status")
            != f"calibrated-{rift_status_version}-rift-space-with-exact-rift-packets"
            or space.get("backgroundStatus")
            != (
                "hash-verified-client-rift-art-with-pixel-validated-"
                f"{rift_status_version}-coordinate-registration"
            )
            or space.get("backgroundAssetKey") != "rift-map-background"
            or not space.get("teamNumbers")
            or space.get("observedAnchorCount", 0) < 40
            or space.get("exactRiftBattleStatePacketCount", 0) <= 0
            or space.get("firstTick", 0) > space.get("lastTick", -1)
            or bounds.get("xMin", 0) >= bounds.get("xMax", 0)
            or bounds.get("zMin", 0) >= bounds.get("zMax", 0)
            or projection.get("type")
            != f"calibrated-rift-world-to-client-pixel-{rift_status_version}.v1"
            or projection.get("formula")
            != (
                "pixelX=178+3.48*(-localX+localZ); "
                "pixelY=178+3.48*(-localX-localZ)"
            )
            or projection.get("originWorld")
            not in [[-188.0, 397.0], [-8.0, 397.0]]
            or projection.get("pixelOrigin") != [178.0, 178.0]
            or projection.get("pixelPerWorldUnit") != 3.48
            or projection.get("validationStatus")
            != (
                RIFT_MAP_CALIBRATION_BY_CLIENT_VERSION[catalog["meta"]["clientVersion"]]["validationStatus"]
            )
            or space.get("image") != {"w": 363, "h": 364}
        ):
            raise ValueError("public secondary map space is invalid")
    privacy = catalog["privacy"]
    required_false = (
        "nicknameIncluded",
        "accountIdentifiersIncluded",
        "matchIdentifierIncluded",
        "rawEventStreamIncluded",
        "sourceFilenamesIncluded",
        "sourceHashesIncluded",
        "rawReplayIncluded",
    )
    if any(privacy[key] is not False for key in required_false):
        raise ValueError("public privacy boundary is not fail-closed")
    if privacy["deletionReference"] != meta["reportId"]:
        raise ValueError("deletion reference must equal the opaque report id")
    if privacy["deletionContactUrl"] != DEFAULT_DELETION_CONTACT_URL:
        raise ValueError("deletion contact route is not the verified ERCraft route")
    actual_ids = [player["publicPlayerId"] for player in catalog["players"]]
    if actual_ids != expected_ids:
        raise ValueError("public player ids must be dense and local to the report")
    expected_labels = [f"P{value:02d}" for value in expected_ids]
    if [player["publicLabel"] for player in catalog["players"]] != expected_labels:
        raise ValueError("public player labels are invalid")
    combat_summary = catalog.get("combatJudgment", {})
    if (
        combat_summary.get("status")
        != "derived-exact-combat-state-and-target-selection-v1"
        or combat_summary.get("fallbackUsed") is not False
        or combat_summary.get("playerCount") != len(catalog["players"])
        or combat_summary.get("teamFocusWindowSeconds") != 2
        or combat_summary.get("simultaneousEntryWindowSeconds")
        != SIMULTANEOUS_ENTRY_WINDOW_SECONDS
        or not isinstance(combat_summary.get("teamEpisodeCount"), int)
        or combat_summary["teamEpisodeCount"] < 0
    ):
        raise ValueError("public combat-judgment summary is invalid")
    derived_summaries = {
        "deathReview": "derived-exact-event-and-held-position-death-review-v1",
        "growthTempo": "derived-exact-snapshot-and-equipment-growth-v1",
        "objectivePreparation": (
            "derived-exact-objective-clock-and-held-anchor-distance-v1"
        ),
        "skillOperation": "derived-exact-confirmed-pvp-skill-sequence-v1",
    }
    for key, status in derived_summaries.items():
        summary = catalog.get(key, {})
        if (
            summary.get("status") != status
            or summary.get("fallbackUsed") is not False
            or summary.get("playerCount") != len(catalog["players"])
        ):
            raise ValueError(f"public {key} summary is invalid")
    if catalog["objectivePreparation"].get("preparationRadiusMeters") != 20:
        raise ValueError("public objective preparation radius is invalid")
    if catalog["skillOperation"].get("chainWindowSeconds") != 3:
        raise ValueError("public skill-operation chain window is invalid")
    item_catalog = catalog["itemCatalog"]
    if any(
        not isinstance(key, str)
        or not key.isdigit()
        or not isinstance(row, dict)
        or row.get("code") != int(key)
        or row.get("status") != "exact-replay-version-game-data"
        or row.get("itemNameStatus") not in {
            "exact-local-name-map",
            "exact-item-asset-provenance-name",
            "unavailable-no-localized-item-name",
        }
        or (
            row.get("itemName") is not None
            and (
                not isinstance(row.get("itemName"), str)
                or not row["itemName"].strip()
            )
        )
        for key, row in item_catalog.items()
    ):
        raise ValueError("public item catalog is invalid")
    gadget_asset = catalog.get("uiAssets", {}).get("gadgetPoint", {})
    if (
        gadget_asset.get("status") != "user-supplied-exact-icon"
        or not gadget_asset.get("imageDataUrl", "").startswith(
            "data:image/png;base64,"
        )
    ):
        raise ValueError("public gadget point icon is invalid")
    item_assets = catalog["itemAssets"]
    item_icon_codes = set(item_assets["icons"])
    item_unavailable_codes = set(item_assets["unavailable"])
    if (
        item_icon_codes & item_unavailable_codes
        or item_icon_codes | item_unavailable_codes != set(item_catalog)
        or any(code not in item_catalog for code in item_icon_codes)
        or any(
            not row.get("imageDataUrl", "").startswith("data:image/")
            or row.get("status") != "exact-code-hash-verified-item-icon"
            for row in item_assets["icons"].values()
        )
        or any(
            reason != "unavailable-no-exact-item-code-asset-mapping"
            for reason in item_assets["unavailable"].values()
        )
    ):
        raise ValueError("public item asset projection is invalid")
    for player in catalog["players"]:
        team_kill = player.get("result", {}).get("teamKill")
        if not isinstance(team_kill, int) or team_kill < 0:
            raise ValueError("public player team kill is invalid")
        projectile_rows = player.get("projectileHitRates")
        if not isinstance(projectile_rows, list):
            raise ValueError("public player projectile hit rates are invalid")
        for row in projectile_rows:
            required_keys = {
                "skillGroup", "skillId", "family", "projectileStatus",
                "projectileCodes", "projectileShape", "aimModel", "castTiming", "hitEvidencePacket",
                "useCountEvidence", "attemptDenominator", "hitRateCalculable", "hitRateReason",
                "scope", "scopeEvidence", "allCastCount", "castCount",
                "excludedOutsideCombatCastCount", "confirmedPvpIntervalCount",
                "fallbackUsed",
            }
            if (
                not isinstance(row, dict)
                or not required_keys.issubset(row)
                or not isinstance(row["skillGroup"], int)
                or not isinstance(row["projectileCodes"], list)
                or row.get("aimModel", {}).get("status")
                != "exact-replay-version-SkillGroup-cast-model"
                or not isinstance(
                    row.get("aimModel", {}).get("hitRateEligible"), bool
                )
                or row.get("castTiming", {}).get("status")
                != "derived-exact-replay-version-SkillGroup-timing"
                or not isinstance(
                    row.get("castTiming", {}).get(
                        "derivedLinkWindowTicksAt60Hz"
                    ),
                    int,
                )
                or row.get("useCountEvidence", {}).get("packet")
                != "CmdStartSkill"
                or row.get("useCountEvidence", {}).get("status")
                != "decoded-exact-character-skill-start-count"
                or row.get("useCountEvidence", {}).get("scope")
                != "confirmed-player-engagement-only"
                or row.get("scope") != "confirmed-player-engagement-only"
                or row.get("scopeEvidence")
                != "derived-exact-team-pvp-episode-intersect-own-combat-state"
                or not isinstance(row.get("allCastCount"), int)
                or row["allCastCount"] < 1
                or not isinstance(row.get("castCount"), int)
                or not 0 <= row["castCount"] <= row["allCastCount"]
                or row.get("excludedOutsideCombatCastCount")
                != row["allCastCount"] - row["castCount"]
                or not isinstance(row.get("confirmedPvpIntervalCount"), int)
                or row["confirmedPvpIntervalCount"] < 0
                or not isinstance(row["hitRateCalculable"], bool)
                or not isinstance(row["hitRateReason"], str)
                or not row["hitRateReason"]
                or row["fallbackUsed"] is not False
            ):
                raise ValueError("public player projectile hit-rate row is invalid")
            if row["hitRateCalculable"]:
                if (
                    row.get("aimModel", {}).get("hitRateEligible") is not True
                    or not _valid_calculable_skill_hit_rate(row)
                ):
                    raise ValueError("public calculable projectile hit rate is invalid")
        capabilities = player.get("characterCapabilities", {})
        evidence = player.get("observedCapabilityEvidence", {})
        if (
            capabilities.get("characterCode") != player.get("characterCode")
            or capabilities.get("fallbackUsed") is not False
            or capabilities.get("coverageStatus") not in {
                "exact-static-definitions-present",
                "unavailable-no-character-skill-or-state-definition",
            }
            or capabilities.get("interpretationBoundary")
            != "static-base-capability-not-runtime-source-or-final-duration"
            or any(
                not isinstance(capabilities.get(key), list)
                for key in (
                    "crowdControlStates",
                    "shieldStates",
                    "namedHealingStates",
                    "defensiveStateTypes",
                    "movementSkills",
                    "skillProfiles",
                )
            )
            or evidence.get("status")
            != "decoded-exact-runtime-capability-evidence"
            or evidence.get("fallbackUsed") is not False
            or evidence.get("crowdControlSourceStatus")
            != "unavailable-CmdCrowdControl-has-no-source-field"
            or evidence.get("shieldSourceStatus")
            != "unavailable-CmdUpdateShield-has-no-caster-field"
            or not isinstance(evidence.get("maxObservedShield"), int)
            or evidence["maxObservedShield"] < 0
            or not isinstance(evidence.get("positiveShieldUpdateCount"), int)
            or evidence["positiveShieldUpdateCount"] < 0
            or not isinstance(evidence.get("healingReceived"), int)
            or evidence["healingReceived"] < 0
            or not isinstance(evidence.get("alliedHealingGiven"), int)
            or evidence["alliedHealingGiven"] < 0
            or any(
                not isinstance(row.get("stateType"), str)
                or not isinstance(row.get("count"), int)
                or row["count"] <= 0
                for row in evidence.get("crowdControlReceived", [])
            )
            or any(
                not isinstance(row, list)
                or len(row) != 5
                or not isinstance(row[0], int)
                or any(not isinstance(value, int) or value < 0 for value in row[1:])
                for row in evidence.get("shieldTimeline", [])
            )
        ):
            raise ValueError("public player character capability evidence is invalid")
        hud = player.get("mapCombatHud", {})
        hud_intervals = hud.get("intervals", [])
        hud_error = None
        if hud.get("status") != (
            "derived-exact-pvp-damage-completeness-and-runtime-cc-duration-v3"
        ):
            hud_error = "status"
        elif hud.get("fallbackUsed") is not False:
            hud_error = "fallback"
        elif hud.get("ccAppliedStatus") not in {
            "exact-only-with-resolved-enemy-caster-and-complete-state-lifecycle",
            "unavailable-missing-exact-gameDb-or-state-event-authority",
        }:
            hud_error = "ccApplied"
        elif hud.get("ccStatus") not in {
            "exact-gameDb-cc-state-runtime-lifecycle-in-replay-ticks",
            "unavailable-missing-exact-gameDb-or-state-event-authority",
        }:
            hud_error = "ccStatus"
        elif hud.get("ccStatus") != hud.get("ccAppliedStatus") and not (
            hud.get("ccStatus")
            == "exact-gameDb-cc-state-runtime-lifecycle-in-replay-ticks"
            and hud.get("ccAppliedStatus")
            == "exact-only-with-resolved-enemy-caster-and-complete-state-lifecycle"
        ):
            hud_error = "cc-authority-pair"
        elif hud.get("ccTimeBase") != "replay-ticks-divide-60":
            hud_error = "ccTimeBase"
        elif hud.get("damageStatus") != (
            "exact-only-when-all-resolved-pvp-CmdDamage-values-are-numeric"
        ):
            hud_error = "damageStatus"
        elif hud.get("damageSampleFields") != [
            "tick", "knownDamageDealt", "knownDamageTaken",
            "missingDamageDealtPackets", "missingDamageTakenPackets",
        ]:
            hud_error = "damageSampleFields"
        elif hud.get("nearbyRadiusMeters") != 30:
            hud_error = "radius"
        elif not isinstance(hud_intervals, list):
            hud_error = "intervals-type"
        else:
            for index, row in enumerate(hud_intervals):
                if (
                    not isinstance(row, dict)
                    or not isinstance(row.get("startTick"), int)
                    or not isinstance(row.get("endTick"), int)
                    or row["endTick"] <= row["startTick"]
                    or not isinstance(row.get("damageSamples"), list)
                    or not row["damageSamples"]
                    or row["damageSamples"][0][0] != row["startTick"]
                    or row["damageSamples"] != sorted(
                        row["damageSamples"], key=lambda sample: sample[0]
                    )
                ):
                    hud_error = f"interval-{index}-shape"
                    break
                for sample in row["damageSamples"]:
                    if (
                        not isinstance(sample, list)
                        or len(sample) != 5
                        or not isinstance(sample[0], int)
                        or sample[0] < row["startTick"]
                        or sample[0] >= row["endTick"]
                        or any(
                            not isinstance(value, int) or value < 0
                            for value in sample[1:]
                        )
                    ):
                        hud_error = f"interval-{index}-sample"
                        break
                for spans_key in (
                    "receivedCcSpans", "appliedCcSpans",
                    "receivedCcUnavailableSpans", "appliedCcUnavailableSpans",
                ):
                    spans = row.get(spans_key)
                    if (
                        not isinstance(spans, list)
                        or any(
                            not isinstance(span, list)
                            or len(span) != 2
                            or not all(isinstance(value, int) for value in span)
                            or not row["startTick"] <= span[0] < span[1] <= row["endTick"]
                            for span in spans
                        )
                    ):
                        hud_error = f"interval-{index}-{spans_key}"
                        break
                if hud_error:
                    break
                if hud.get("ccStatus") == (
                    "unavailable-missing-exact-gameDb-or-state-event-authority"
                ) and (
                    row["receivedCcUnavailableSpans"]
                    != [[row["startTick"], row["endTick"]]]
                    or row["appliedCcUnavailableSpans"]
                    != [[row["startTick"], row["endTick"]]]
                ):
                    hud_error = f"interval-{index}-cc-authority-not-fail-closed"
                    break
        if hud_error:
            raise ValueError(f"public map combat HUD is invalid: {hud_error}")
        judgment = player.get("combatJudgment", {})
        episodes = judgment.get("episodes", [])
        rates = (
            "initiationRate", "soloInitiationRate", "survivalRate",
            "episodePrimaryTargetShare", "teamFocusFollowupRate",
        )
        if (
            judgment.get("status")
            != "derived-exact-combat-state-and-target-selection-v1"
            or judgment.get("fallbackUsed") is not False
            or judgment.get("styleAuthority") != "derived-team-relative-v1"
            or not isinstance(judgment.get("styleLabel"), str)
            or not judgment["styleLabel"]
            or judgment.get("participatedEpisodeCount") != len(episodes)
            or judgment.get("teamEpisodeCount", -1) < len(episodes)
            or episodes != sorted(episodes, key=lambda row: row["startTick"])
            or any(
                judgment.get(key) is not None
                and not 0 <= judgment[key] <= 1
                for key in rates
            )
            or any(
                row.get("entryRole") not in {"initiator", "co-initiator", "joiner"}
                or not isinstance(row.get("startTick"), int)
                or not isinstance(row.get("entryTick"), int)
                or not isinstance(row.get("endTick"), int)
                or not row["startTick"] <= row["entryTick"] <= row["endTick"]
                or (
                    row.get("entryRole") == "joiner"
                    and row.get("joinDelaySeconds", 0)
                    <= SIMULTANEOUS_ENTRY_WINDOW_SECONDS
                )
                or (
                    row.get("entryRole") != "joiner"
                    and row.get("joinDelaySeconds", 0)
                    > SIMULTANEOUS_ENTRY_WINDOW_SECONDS
                )
                or row.get("targetActionCount", -1) < 0
                or row.get("targetSwitchCount", -1) < 0
                or row.get("targetSwitchCount", 0)
                > max(0, row.get("targetActionCount", 0) - 1)
                or row.get("teamFocusFollowupCount", -1) < 0
                or row.get("teamFocusFollowupCount", 0)
                > row.get("targetActionCount", 0)
                for row in episodes
            )
        ):
            raise ValueError("public player combat judgment is invalid")
        growth = player.get("growthTempo", {})
        credit_audit = growth.get("creditCounterAudit", {})
        audit_start = credit_audit.get("startObservedCredit")
        audit_end = credit_audit.get("endObservedCredit")
        expected_credit_gap = (
            None
            if audit_start is None or audit_end is None
            else round(
                (audit_end - audit_start)
                - (growth.get("totalGainCredit") - growth.get("totalUseCredit")),
                2,
            )
        )
        if (
            growth.get("status")
            != "derived-exact-snapshot-and-equipment-growth-v1"
            or growth.get("fallbackUsed") is not False
            or growth.get("styleAuthority") != "derived-team-relative-v1"
            or growth.get("levelTimeline", [])
            != sorted(growth.get("levelTimeline", []), key=lambda row: row[0])
            or growth.get("equipmentProgress", [])
            != sorted(growth.get("equipmentProgress", []), key=lambda row: row[0])
            or [row.get("level") for row in growth.get("levelMilestones", [])]
            != [6, 9, 12, 15, 18, 20]
            or [
                row.get("completedSlots")
                for row in growth.get("equipmentMilestones", [])
            ] != [3, 4, 5]
            or (
                growth.get("purpleBuildTick") is not None
                and not isinstance(growth["purpleBuildTick"], int)
            )
            or credit_audit.get("status")
            != "derived-exact-result-counters-vs-observed-balance-v1"
            or (
                audit_start is not None
                and not isinstance(audit_start, (int, float))
            )
            or (
                audit_end is not None
                and not isinstance(audit_end, (int, float))
            )
            or credit_audit.get("counterBalanceGap") != expected_credit_gap
            or credit_audit.get("ledgerCompatible")
            is not (
                expected_credit_gap is not None
                and abs(expected_credit_gap) < 0.01
            )
            or not isinstance(growth.get("finalMasteryLevel"), int)
            or growth["finalMasteryLevel"] < 0
        ):
            raise ValueError("public player growth tempo is invalid")
        objective = player.get("objectivePreparation", {})
        objective_rows = objective.get("objectives", [])
        if (
            objective.get("status")
            != "derived-exact-objective-clock-and-held-anchor-distance-v1"
            or objective.get("fallbackUsed") is not False
            or objective.get("styleAuthority") != "derived-team-relative-v1"
            or objective.get("preparationRadiusMeters") != 20
            or objective.get("objectiveCount") != len(objective_rows)
            or objective.get("beforeActiveCount")
            + objective.get("afterActiveCount")
            + objective.get("notObservedCount") != len(objective_rows)
            or any(
                row.get("arrivalStatus")
                not in {"before-active", "after-active", "not-observed-in-range"}
                or not isinstance(row.get("warningTick"), int)
                or not isinstance(row.get("activeTick"), int)
                or not isinstance(row.get("endTick"), int)
                or row["warningTick"] > row["activeTick"]
                or row["activeTick"] > row["endTick"]
                for row in objective_rows
            )
        ):
            raise ValueError("public player objective preparation is invalid")
        operation = player.get("skillOperation", {})
        operation_rows = operation.get("episodes", [])
        operation_hit_rates = operation.get("hitRates", [])
        if (
            operation.get("status")
            != "derived-exact-confirmed-pvp-skill-sequence-v1"
            or operation.get("fallbackUsed") is not False
            or operation.get("styleAuthority") != "derived-team-relative-v1"
            or operation_rows
            != sorted(operation_rows, key=lambda row: row["startTick"])
            or operation.get("pvpSkillStartCount")
            != sum(row["skillStartCount"] for row in operation_rows)
            or operation.get("pvpNormalAttackStartCount")
            != sum(row["normalAttackStartCount"] for row in operation_rows)
            or not isinstance(operation_hit_rates, list)
            or any(
                not _valid_compact_engagement_hit_rate(
                    row, "verified-exact-confirmed-engagement-hit-rate"
                )
                for row in operation_hit_rates
            )
            or any(
                row.get("startTick", 0) > row.get("endTick", -1)
                or row.get("skillStartCount", -1) < 0
                or row.get("normalAttackStartCount", -1) < 0
                or not isinstance(row.get("hitRates"), list)
                or any(
                    not _valid_compact_engagement_hit_rate(
                        hit_rate, "verified-exact-single-engagement-hit-rate"
                    )
                    or any(
                        not row["startTick"] <= outcome[0] < row["endTick"]
                        for outcome in hit_rate.get("outcomes", [])
                    )
                    for hit_rate in row.get("hitRates", [])
                )
                for row in operation_rows
            )
        ):
            raise ValueError("public player skill operation is invalid")
        from decoder.kda_source_discrepancy import validate_summary, EXACT, DISCREPANCY
        proof = player.get('kdaCrosscheck')
        validate_summary(player.get('kdaTimeline'),
                         [player['result']['playerKill'], player['result']['playerDeaths'],
                          player['result']['playerAssistant']], proof)
        if player.get('kdaTimelineStatus') != (DISCREPANCY if proof else EXACT):
            raise ValueError('public KDA source status invalid')

        observer_timeline = player.get("observerStatusTimeline", [])
        if (
            player.get("observerStatusTimelineStatus")
            != (
                "decoded-exact-full-snapshot-vf-credit-and-gadget-energy-"
                "held-until-next-snapshot"
            )
            or observer_timeline
            != sorted(observer_timeline, key=lambda row: row[0])
            or any(
                not isinstance(row, list)
                or len(row) != 3
                or not isinstance(row[0], int)
                or not isinstance(row[1], (int, float))
                or row[1] < 0
                or not isinstance(row[2], int)
                or row[2] < 0
                for row in observer_timeline
            )
        ):
            raise ValueError("public observer status timeline is invalid")
        survivable_timeline = player.get("survivableTimeTimeline", [])
        if (
            player.get("survivableTimeTimelineStatus")
            != "decoded-exact-team-survivable-time-update-held-until-next-update"
            or survivable_timeline
            != sorted(survivable_timeline, key=lambda row: row[0])
            or any(
                not isinstance(row, list)
                or len(row) != 2
                or not all(isinstance(value, int) and value >= 0 for value in row)
                for row in survivable_timeline
            )
        ):
            raise ValueError("public survivable-time timeline is invalid")
        death_locations = player.get("deathLocations", [])
        if death_locations != sorted(death_locations, key=lambda row: row["tick"]) or any(
            not isinstance(row.get("tick"), int)
            or not isinstance(row.get("position"), list)
            or len(row["position"]) != 2
            or not all(isinstance(value, (int, float)) for value in row["position"])
            or row.get("positionStatus")
            != "decoded-exact-player-position-same-tick-as-CmdDead"
            for row in death_locations
        ):
            raise ValueError("public exact death locations are invalid")
        if player["equipmentTimelineStatus"] != "decoded-exact-observer-equipment-updates":
            raise ValueError("public equipment timeline status is invalid")
        if player["inventoryTimelineStatus"] != "decoded-exact-observer-inventory-updates":
            raise ValueError("public inventory timeline status is invalid")
        if player["skillCooldownTimelineStatus"] != (
            "decoded-exact-cooldown-packets-with-derived-clock"
        ):
            raise ValueError("public skill cooldown timeline status is invalid")
        for timeline_name in ("equipmentTimeline", "inventoryTimeline"):
            timeline = player[timeline_name]
            if timeline != sorted(timeline, key=lambda row: row[0]):
                raise ValueError(f"public {timeline_name} must be chronological")
            for row in timeline:
                if (
                    not isinstance(row, list)
                    or len(row) not in {2, 3}
                    or not isinstance(row[0], int)
                    or not isinstance(row[1], list)
                ):
                    raise ValueError(f"public {timeline_name} row is invalid")
                for update in row[1]:
                    if (
                        not isinstance(update, list)
                        or len(update) != 3
                        or not isinstance(update[0], int)
                        or update[0] < 0
                        or not isinstance(update[2], int)
                        or update[2] < 0
                        or (
                            update[1] is not None
                            and str(update[1]) not in item_catalog
                        )
                    ):
                        raise ValueError(f"public {timeline_name} update is invalid")
        skill_timeline = player["skillStartTimeline"]
        if skill_timeline != sorted(skill_timeline, key=lambda row: row[0]):
            raise ValueError("public skill start timeline must be chronological")
        if any(
            not isinstance(row, list)
            or len(row) != 4
            or not isinstance(row[0], int)
            or not isinstance(row[1], str)
            or not isinstance(row[2], str)
            or not isinstance(row[3], int)
            for row in skill_timeline
        ):
            raise ValueError("public skill start timeline row is invalid")
        cooldown_timeline = player["skillCooldownTimeline"]
        if cooldown_timeline != sorted(cooldown_timeline, key=lambda row: row[0]):
            raise ValueError("public skill cooldown timeline must be chronological")
        valid_families = {
            "Active1", "Active2", "Active3", "Active4",
            "WeaponSkill", "TacticalSkill",
        }
        for row in cooldown_timeline:
            if (
                not isinstance(row, list)
                or len(row) != 7
                or not isinstance(row[0], int)
                or row[1] not in {"set", "copy", "hold", "clear"}
                or (row[1] == "clear" and row[2] is not None)
                or (row[1] != "clear" and row[2] not in valid_families)
                or (
                    row[1] == "set"
                    and (
                        not isinstance(row[3], int)
                        or row[3] < 0
                        or (
                            row[4] is not None
                            and (not isinstance(row[4], int) or row[4] < 0)
                        )
                    )
                )
                or (
                    row[1] == "copy"
                    and row[6] is not None
                    and row[6] not in valid_families
                )
                or (row[1] == "hold" and not isinstance(row[6], bool))
            ):
                raise ValueError("public skill cooldown timeline row is invalid")
    wildlife = catalog["wildlife"]
    instances = wildlife["instances"]
    if wildlife["instanceCount"] != len(instances):
        raise ValueError("public wildlife count is invalid")
    if [row["publicWildlifeId"] for row in instances] != list(
        range(1, len(instances) + 1)
    ):
        raise ValueError("public wildlife ids must be dense and report-local")
    if wildlife["movementAnchorCount"] != sum(
        row["movementAnchorCount"] for row in instances
    ):
        raise ValueError("public wildlife movement count is invalid")
    if wildlife["displayPositionAnchorCount"] != sum(
        row["displayPositionAnchorCount"] for row in instances
    ):
        raise ValueError("public wildlife display-position count is invalid")
    if wildlife["positionedInstanceCount"] != sum(
        bool(row["displayPositionTrack"]) for row in instances
    ):
        raise ValueError("public wildlife positioned-instance count is invalid")
    if wildlife["groupCount"] != len({
        row["publicWildlifeGroupId"] for row in instances
    }):
        raise ValueError("public wildlife group count is invalid")
    if wildlife["mapPositionAnchorCount"] != sum(
        row["mapPositionAnchorCount"] for row in instances
    ):
        raise ValueError("public wildlife map-position count is invalid")
    if wildlife["deadGhostSeconds"] != 0:
        raise ValueError("dead wildlife must disappear immediately")
    if wildlife.get("renderMode") != "individual-living-instance-markers":
        raise ValueError("public wildlife render mode is invalid")
    if any(
        row["displayPositionAnchorCount"] != len(row["displayPositionTrack"])
        or row["displayPositionTrack"] != sorted(
            row["displayPositionTrack"], key=lambda anchor: anchor[0]
        )
        or (
            bool(row["displayPositionTrack"])
            == (row["displayPositionStatus"] == "unavailable-no-position-evidence")
        )
        for row in instances
    ):
        raise ValueError("public wildlife display positions are invalid")

    def _valid_public_bori_marker(row: dict[str, Any]) -> bool:
        if row["monsterCode"] != 22:
            return (
                row.get("boriGrade") is None
                and row.get("boriGradeLabel") is None
                and row.get("boriGradeStatus") == "not-applicable"
                and row.get("boriGradeRevealTick") is None
                and row.get("mapMarkerAssetTimeline") == []
            )
        grade = row.get("boriGrade")
        reveal_tick = row.get("boriGradeRevealTick")
        timeline = row.get("mapMarkerAssetTimeline")
        expected_timeline = [[row["spawnTick"], "bori-base"]]
        if grade is None:
            return (
                row.get("mapMarkerAssetKey") == "bori-base"
                and row.get("boriGradeLabel") is None
                and row.get("boriGradeStatus")
                == "unavailable-no-unique-Bori-lifecycle-grade-snapshot"
                and reveal_tick is None
                and timeline == expected_timeline
            )
        expected_asset = BORI_GRADE_ASSET_KEYS.get(grade)
        if expected_asset is None or not isinstance(reveal_tick, int):
            return False
        end_ticks = sorted(
            tick
            for tick in (
                row.get("deathTick"), row.get("destroyTick"), row.get("despawnTick")
            )
            if isinstance(tick, int)
        )
        expected_timeline.append([reveal_tick, expected_asset])
        return (
            row.get("mapMarkerAssetKey") == expected_asset
            and row.get("boriGradeLabel") == BORI_GRADE_LABELS.get(grade)
            and row.get("boriGradeStatus")
            == "decoded-exact-Bori-lifecycle-bracket-BoriSupplyBoxSnapshot-boxGrade"
            and row["spawnTick"] <= reveal_tick
            and (not end_ticks or reveal_tick <= end_ticks[0])
            and timeline == expected_timeline
        )

    if any(
        not isinstance(row.get("publicWildlifeGroupId"), int)
        or row["publicWildlifeGroupId"] < 1
        or row["mapPositionAnchorCount"] != len(row["mapPositionTrack"])
        or row["mapPositionTrack"] != sorted(
            row["mapPositionTrack"], key=lambda anchor: anchor[0]
        )
        or row["mapMovementMode"] not in {
            "crow-held-observed-track",
            "special-mobile-observed-track",
            "fixed-scratch-group-center",
            "fixed-first-supported-position",
            "unavailable-no-position-evidence",
        }
        or row["mapMarkerStyle"] not in {
            "neutral-monster-red-dot", "species-triangle",
            "mutant-purple-outline",
            "special-icon",
        }
        or (row["monsterCode"] == 11) != (
            row["mapMarkerStyle"] == "neutral-monster-red-dot"
        )
        or (
            row["monsterCode"] not in {11, 22}
            and row["mapMarkerStyle"]
            != (
                "mutant-purple-outline"
                if row["mutated"]
                else "species-triangle"
            )
        )
        or (row["monsterCode"] == 22) != (
            row["mapMarkerStyle"] == "special-icon"
        )
        or row["monsterCode"] == 21
        or not _valid_public_bori_marker(row)
        or row.get("mapMarkerColorStatus") not in {
            "user-confirmed-neutral-monster-red",
            "user-confirmed-species-palette",
            "hash-verified-client-special-marker",
            "unavailable-no-exact-species-marker-palette",
        }
        or not isinstance(row.get("mapMarkerColor"), str)
        or (
            row.get("mapMarkerColorStatus")
            == "unavailable-no-exact-species-marker-palette"
            and row.get("mapMarkerColor") != UNKNOWN_WILDLIFE_MARKER_COLOR
        )
        or row.get("mapMarkerPositionMeaning") not in {
            "presentation-only-separated-shared-group-center",
            "same-as-map-position-track",
        }
        or row["ownerStatus"] not in {
            "decoded-exact-spawn-owner-chain",
            "decoded-exact-snapshot-owner-id",
            "unavailable-no-resolved-player-owner",
        }
        or (
            row["ownerPublicPlayerId"] is not None
            and row["ownerPublicPlayerId"] not in expected_ids
        )
        for row in instances
    ):
        raise ValueError("public wildlife map grouping is invalid")
    if (
        wildlife.get("assets", {}).get("status")
        != "not-embedded-shape-markers-only"
        or wildlife.get("assets", {}).get("icons") != {}
    ):
        raise ValueError("public wildlife assets must stay unembedded")
    if any(
        killer is not None and killer not in expected_ids
        for killer in (row["killerPublicPlayerId"] for row in instances)
    ):
        raise ValueError("public wildlife killer id is invalid")
    character_icons = catalog["characterAssets"]["icons"]
    if any(str(player["characterCode"]) not in character_icons for player in catalog["players"]):
        raise ValueError("public character icon projection is incomplete")
    skill_icons = catalog["skillAssets"]["icons"]
    if any(str(player["characterCode"]) not in skill_icons for player in catalog["players"]):
        raise ValueError("public skill icon projection is incomplete")
    if any(
        not isinstance(row.get("slots"), dict)
        or not row["slots"]
        or any(
            not slot.get("imageDataUrl", "").startswith("data:image/")
            for slot in row["slots"].values()
        )
        or any(
            semantic not in {"passive", "q", "w", "e", "r"}
            for semantic in row.get("unavailableSemanticSlots", [])
        )
        for row in skill_icons.values()
    ):
        raise ValueError("public skill assets are invalid")
    utility_assets = catalog["utilitySkillAssets"]
    weapon_icons = utility_assets["weapon"]["icons"]
    weapon_overrides = utility_assets["weapon"]["characterOverrides"]
    weapon_unavailable = utility_assets["weapon"]["unavailable"]
    tactical_icons = utility_assets["tactical"]["icons"]
    tactical_unavailable = utility_assets["tactical"]["unavailable"]
    for player in catalog["players"]:
        character_code = str(player["characterCode"])
        mastery_type = str(player["result"].get("bestWeapon"))
        tactical_group = str(player["result"].get("tacticalSkillGroup"))
        if (
            character_code not in weapon_overrides
            and mastery_type not in weapon_icons
            and mastery_type not in weapon_unavailable
        ):
            raise ValueError("public weapon skill icon status is incomplete")
        if tactical_group not in tactical_icons and tactical_group not in tactical_unavailable:
            raise ValueError("public tactical skill icon status is incomplete")
    pings = catalog["tacticalPings"]
    if pings["selectedPlayerFilter"] is not False:
        raise ValueError("whole-match pings cannot use a selected-player filter")
    if pings["automaticNoiseIncluded"] is not False:
        raise ValueError("automatic noise must not be included in the public ping UI")
    if pings["hyperloopPingIncluded"] is not False:
        raise ValueError("hyperloop pings must not be included in the public ping UI")
    if pings["count"] != len(pings["items"]):
        raise ValueError("public tactical ping count is invalid")
    if pings.get("displaySeconds") != TACTICAL_PING_DISPLAY_SECONDS:
        raise ValueError("public tactical ping display span is invalid")
    if pings["items"] != sorted(
        pings["items"], key=lambda row: (row["tick"], row["publicPingId"])
    ):
        raise ValueError("public tactical pings must be chronological")
    if any(
        row["type"] in HYPERLOOP_TACTICAL_PING_TYPES
        or row["senderPublicPlayerId"] not in expected_ids
        or row.get("assetKey") != TACTICAL_PING_ASSET_KEYS.get(row["type"])
        or row.get("assetStatus")
        != (
            "hash-verified-client-map-ping"
            if row["type"] in TACTICAL_PING_ASSET_KEYS
            else "unavailable-no-exact-client-map-ping"
        )
        or row.get("sourceClass") != "player-issued-tactical-ping"
        or not isinstance(row["position"], list)
        or len(row["position"]) != 2
        or not all(
            isinstance(value, (int, float)) and math.isfinite(float(value))
            for value in row["position"]
        )
        or (
            not point_is_in_known_map_space(row["position"])
            and row.get("mapPositionStatus")
            != "unavailable-outside-calibrated-map-spaces"
        )
        or (
            point_is_in_known_map_space(row["position"])
            and row.get("mapPositionStatus") is not None
        )
        for row in pings["items"]
    ):
        raise ValueError("public tactical ping projection is invalid")
    phase_clock = catalog["phaseClock"]
    if phase_clock["status"] != "decoded-exact-day-night-phase-and-remain-time":
        raise ValueError("public phase clock status is invalid")
    for key in ("restrictionUpdates", "gamePlayPhaseUpdates"):
        if phase_clock[key] != sorted(phase_clock[key], key=lambda row: row["tick"]):
            raise ValueError("public phase clock must be chronological")
    for update in phase_clock["restrictionUpdates"]:
        areas = update.get("areas", [])
        if areas != sorted(areas, key=lambda row: row["areaCode"]) or any(
            not isinstance(row.get("areaCode"), int)
            or row["areaCode"] <= 0
            or row.get("state") not in {0, 1, 2, 3, 4, 5}
            or row.get("stateName") not in {
                "None", "Normal", "Reserved", "Clearing", "Restricted",
                "ReservedClearing",
            }
            or row.get("status")
            != "decoded-exact-lumia-area-restriction-state"
            for row in areas
        ):
            raise ValueError("public Lumia restriction states are invalid")


HTML_TEMPLATE = r'''<!doctype html>
<html lang="ko">
<head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>익명 리플레이 전투 분석</title>
<style>
:root{
color-scheme:dark;
--bg:oklch(0.13 0.01 280);
--panel:oklch(0.19 0.012 280);
--card:oklch(0.225 0.012 280);
--card-hi:oklch(0.255 0.014 285);
--well:oklch(0.165 0.01 280);
--text:oklch(0.985 0 0);
--muted:oklch(0.708 0 0);
--faint:oklch(0.62 0 0);
--line:oklch(1 0 0 / 10%);
--line-2:oklch(1 0 0 / 16%);
--accent:oklch(0.72 0.15 300);
--accent-soft:oklch(0.72 0.15 300 / 14%);
--accent-line:oklch(0.72 0.15 300 / 34%);
--good:oklch(0.8 0.14 165);
--warn:oklch(0.83 0.13 78);
--bad:oklch(0.72 0.18 22);
--r:11px;
--r-sm:8px;
--r-xs:7px;
}
*{box-sizing:border-box}
html{-webkit-text-size-adjust:100%}
body{margin:0;background:var(--bg);color:var(--text);font-family:system-ui,-apple-system,"Segoe UI Variable Text","Segoe UI","Noto Sans KR",sans-serif;font-size:14px;line-height:1.5;-webkit-font-smoothing:antialiased;text-rendering:optimizeLegibility}
button,select,input{font:inherit;color:inherit}
button{cursor:pointer}
:focus-visible{outline:2px solid var(--accent);outline-offset:2px;border-radius:6px}
button:focus,select:focus,input:focus,summary:focus{outline:2px solid var(--accent);outline-offset:2px}
button:focus:not(:focus-visible),select:focus:not(:focus-visible),input:focus:not(:focus-visible),summary:focus:not(:focus-visible){outline:0}
a{color:oklch(0.8 0.1 300);text-underline-offset:3px}
code{font-family:ui-monospace,SFMono-Regular,Menlo,monospace;font-size:.92em;color:var(--muted)}
h2{margin:0 0 10px;font-size:14px;font-weight:650;letter-spacing:-.01em}
h3{margin:0 0 8px;font-size:12.5px;font-weight:650;color:var(--muted)}
p{margin:0 0 8px}
ul{margin:0;padding-left:18px}
li{margin:4px 0;font-size:12.5px;color:var(--muted)}
.wrap{width:100%;max-width:1152px;margin:0 auto;padding:16px 16px 40px}
.tnum{font-variant-numeric:tabular-nums}

.topbar{display:flex;align-items:center;justify-content:space-between;gap:12px;flex-wrap:wrap;min-height:44px}
.topbar h1{margin:0;font-size:17px;font-weight:650;letter-spacing:-.015em}
.topbar p{margin:2px 0 0;font-size:11.5px;color:var(--muted);overflow-wrap:anywhere}
.badges{display:flex;gap:6px;flex-wrap:wrap}
.badge{display:inline-flex;align-items:center;height:24px;padding:0 9px;border:1px solid var(--line);border-radius:999px;background:var(--panel);color:var(--muted);font-size:11px;white-space:nowrap}
.badge.ok{color:var(--good);border-color:oklch(0.8 0.14 165 / 30%)}
.ping-label{display:inline-flex;align-items:center;gap:7px}.ping-label img{width:20px;height:20px;object-fit:contain}

.toolbar{display:flex;align-items:center;justify-content:space-between;gap:10px 14px;flex-wrap:wrap;margin:12px 0 0}
.toolbar .picker{margin-left:auto}
.tabs{display:flex;gap:3px;flex-wrap:wrap;background:var(--panel);border:1px solid var(--line);border-radius:var(--r);padding:3px}
.tabs button{height:30px;padding:0 11px;border:0;border-radius:var(--r-sm);background:transparent;color:var(--muted);font-size:12.5px}
.tabs button:hover{background:var(--card);color:var(--text)}
.tabs button.active{background:var(--accent-soft);color:var(--text);box-shadow:inset 0 0 0 1px var(--accent-line)}
.picker{display:inline-flex;align-items:center;gap:8px;font-size:12px;color:var(--muted);min-width:0}
select{height:32px;max-width:220px;padding:0 8px;border:1px solid var(--line);background:var(--card);border-radius:var(--r-sm);font-size:12px}
select:hover{border-color:var(--line-2)}
.hint{margin:0 0 8px;font-size:11.5px;color:var(--faint);line-height:1.6}
.pick-hint{flex:1 1 200px;margin:0;text-align:right;font-size:11.5px;color:var(--faint);overflow:hidden;text-overflow:ellipsis;white-space:nowrap}

.panel{background:var(--panel);border:1px solid var(--line);border-radius:var(--r);padding:14px;margin-top:10px}
.notice{border:1px solid var(--line);background:var(--card);border-radius:var(--r-sm);padding:11px 12px;font-size:12.5px;color:var(--muted);overflow-wrap:anywhere}
.notice b{color:var(--text)}
.panel.privacy{border-color:oklch(0.8 0.14 165 / 22%)}
.grid{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:8px}
.stat{background:var(--card);border:1px solid var(--line);border-radius:var(--r-sm);padding:10px 11px;min-width:0}
.stat span{display:block;font-size:11px;color:var(--muted);overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.stat b{display:block;margin-top:4px;font-size:19px;font-weight:650;font-variant-numeric:tabular-nums;letter-spacing:-.015em}
.stat small{display:block;margin-top:3px;font-size:10.5px;color:var(--faint)}
.btn{display:inline-flex;align-items:center;justify-content:center;height:32px;padding:0 12px;border:1px solid var(--line);background:var(--card);color:var(--text);border-radius:var(--r-sm);font-size:12.5px}
.btn:hover{background:var(--card-hi);border-color:var(--line-2)}
.table-wrap{overflow:auto;border:1px solid var(--line);border-radius:var(--r-sm)}
table{width:100%;border-collapse:collapse;min-width:620px;font-size:12.5px}
th,td{padding:8px 10px;border-bottom:1px solid var(--line);text-align:left;white-space:nowrap}
th{background:var(--card);color:var(--muted);font-size:11px;font-weight:600}
tbody tr:last-child td{border-bottom:0}
th.num,td.num{text-align:right;font-variant-numeric:tabular-nums}
tr.selected{background:var(--accent-soft)}
details{border-top:1px solid var(--line);padding:6px 2px}
summary{padding:5px 6px;border-radius:var(--r-xs);font-size:12.5px;cursor:pointer}
summary:hover{background:var(--card)}
.jump{height:26px;padding:0 9px;font-size:11.5px;font-variant-numeric:tabular-nums}
.skill-grid{display:grid;grid-template-columns:1fr 1fr;gap:12px}
.judgment-hero{display:flex;align-items:flex-start;justify-content:space-between;gap:12px;padding:13px 14px;border:1px solid var(--accent-line);border-radius:var(--r-sm);background:linear-gradient(135deg,var(--accent-soft),transparent 70%)}
.judgment-hero strong{display:block;font-size:17px;letter-spacing:-.02em}
.judgment-hero p{margin:4px 0 0;color:var(--muted);font-size:12.5px}
.judgment-role{flex:none;color:var(--accent);font-size:11px;font-weight:700}
.judgment-note{margin-top:9px;color:var(--faint);font-size:11.5px;line-height:1.65}
.human-lead{display:flex;align-items:flex-start;justify-content:space-between;gap:14px;padding:16px;border:1px solid var(--accent-line);border-radius:var(--r-sm);background:linear-gradient(135deg,var(--accent-soft),transparent 72%)}
.human-lead strong{display:block;font-size:18px;line-height:1.4;letter-spacing:-.025em}
.human-lead p{max-width:780px;margin:6px 0 0;color:var(--muted);font-size:13.5px;line-height:1.7}
.human-kicker{flex:none;color:var(--accent);font-size:11px;font-weight:700}
.human-story{margin-top:9px;padding:11px 12px;border:1px solid var(--line);border-radius:var(--r-sm);background:var(--card);color:var(--muted);font-size:13px;line-height:1.65}
.human-story b{color:var(--text)}
.playstyle-grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:9px;margin-top:9px}
.playstyle-card{min-width:0;padding:13px 14px;border:1px solid var(--line);border-radius:var(--r-sm);background:var(--card)}
.playstyle-card span{display:block;margin-bottom:6px;color:var(--accent);font-size:11px;font-weight:750;letter-spacing:.02em}
.playstyle-card p{margin:0;color:var(--muted);font-size:13px;line-height:1.68}
.playstyle-card p b{color:var(--text);font-weight:720}
.analysis-prose{display:grid;gap:9px;margin-top:10px}
.analysis-paragraph{padding:13px 14px;border:1px solid var(--line);border-radius:var(--r-sm);background:var(--card)}
.analysis-paragraph h3{margin:0 0 5px;color:var(--accent);font-size:11.5px;letter-spacing:.01em}
.analysis-paragraph p{margin:0;color:var(--muted);font-size:13.5px;line-height:1.72}
.analysis-paragraph p b{color:var(--text);font-weight:720}
.life-flow{display:grid;grid-template-columns:minmax(0,1fr) auto minmax(0,1fr) auto minmax(0,1fr) auto minmax(0,1fr);align-items:stretch;gap:7px;margin-top:10px}
.life-step{display:flex;flex-direction:column;justify-content:center;min-width:0;padding:10px 12px;border:1px solid var(--line);border-radius:var(--r-sm);background:var(--card)}
.life-step span{color:var(--faint);font-size:10.5px;font-weight:700}
.life-step b{margin-top:3px;font-size:14px;font-variant-numeric:tabular-nums}
.life-step.fight b{color:#b9a8ff}.life-step.alive b{color:#58d6ad}.life-step.down b{color:#f6bf64}.life-step.dead b{color:#ff7b86}
.life-arrow{display:flex;flex-direction:column;align-items:center;justify-content:center;min-width:46px;color:var(--faint);font-size:13px}
.life-arrow b{color:var(--muted);font-size:10.5px;font-variant-numeric:tabular-nums;white-space:nowrap}
.human-grid{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:8px;margin-top:10px}
.human-fact{min-width:0;padding:11px 12px;border:1px solid var(--line);border-radius:var(--r-sm);background:var(--card)}
.human-fact span{display:block;color:var(--muted);font-size:11.5px}
.human-fact b{display:block;margin-top:4px;font-size:18px;line-height:1.35;font-variant-numeric:tabular-nums;letter-spacing:-.02em}
.human-fact small{display:block;margin-top:3px;color:var(--faint);font-size:10.5px;line-height:1.45}
.analysis-details{margin-top:10px;padding:0;border:1px solid var(--line);border-radius:var(--r-sm);background:var(--well);overflow:hidden}
.analysis-details>summary{padding:11px 12px;color:var(--text);font-weight:650;list-style-position:inside}
.analysis-details[open]>summary{border-bottom:1px solid var(--line);background:var(--card)}
.analysis-details .detail-body{padding:10px}
.analysis-details .judgment-note{margin:0;padding:2px 3px}
.story-list{display:grid;gap:7px}
.story-row{display:grid;grid-template-columns:auto minmax(0,1fr);gap:10px;align-items:start;padding:9px 10px;border:1px solid var(--line);border-radius:var(--r-sm);background:var(--card)}
.story-row b{display:block;font-size:12.5px}
.story-row span{display:block;margin-top:2px;color:var(--muted);font-size:12px;line-height:1.55}
.section-label{margin:13px 0 7px;font-size:12.5px;color:var(--text);font-weight:650}
.scene-card{margin-top:10px;padding:12px;border:1px solid var(--line);border-radius:var(--r);background:var(--well)}
.scene-card .human-lead{margin:0}
.scene-evidence{margin:10px 0 0;padding-left:18px}
.scene-evidence li{margin:4px 0;color:var(--muted);font-size:13px;line-height:1.55;overflow-wrap:anywhere}
.scene-alt{margin:8px 0 0;padding:10px 12px;border:1px solid var(--line);border-radius:var(--r-sm);background:var(--card);color:var(--muted);font-size:12.5px;line-height:1.65;overflow-wrap:anywhere}
.footer{margin:16px 0 0;font-size:11.5px;color:var(--faint);line-height:1.7}

.stage{display:grid;grid-template-columns:minmax(0,1fr) 330px;gap:10px;align-items:start}
.stage>.panel{margin-top:10px}
.map-panel{padding:10px}
.map-shell{position:relative;display:flex;align-items:center;justify-content:center;background:oklch(0.155 0.01 280);border:1px solid var(--line);border-radius:10px;overflow:hidden;touch-action:none}
#mapCanvas{cursor:grab;touch-action:none}
.map-shell.is-dragging #mapCanvas{cursor:grabbing}
.map-zoom{position:absolute;right:8px;bottom:8px;display:flex;flex-direction:column;gap:4px;z-index:2}
.map-zoom button{width:32px;height:32px;padding:0;border:1px solid var(--line);border-radius:8px;background:var(--panel);color:var(--text);font-size:16px;font-weight:700;line-height:1}
.map-zoom button.reset{font-size:10.5px;font-weight:650}
.map-team-log{position:absolute;top:7px;right:7px;z-index:4;display:grid;gap:1px;width:max-content;max-width:min(186px,46%);pointer-events:none}
.map-team-log[hidden]{display:none!important}
.team-log-line{display:grid;grid-template-columns:23px minmax(0,1fr);align-items:center;gap:3px;min-height:18px;padding:2px 4px;border:1px solid var(--line);border-radius:4px;background:oklch(0.14 0.012 280 / 88%);box-shadow:0 1px 4px rgba(0,0,0,.2);font-size:9.5px;line-height:1.05}
.team-log-line .kind{font-size:8.5px;font-weight:800;text-align:center}
.team-log-line .actors{min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;font-weight:650}
.team-log-line.down{border-color:oklch(0.72 0.13 78 / 48%)}
.team-log-line.down .kind{color:#f6bf64}
.team-log-line.kill{border-color:oklch(0.72 0.13 158 / 48%)}
.team-log-line.kill .kind{color:#58d6ad}
.team-log-line.death{border-color:oklch(0.68 0.16 24 / 48%)}
.team-log-line.death .kind{color:#ff7b86}
.team-log-line .arrow{padding:0 2px;color:var(--faint)}
.map-objective-announce{position:absolute;left:8px;top:8px;z-index:4;display:grid;gap:4px;max-width:min(300px,62%);pointer-events:none}
.map-objective-announce[hidden]{display:none!important}
.objective-announce-line{padding:7px 10px;border:1px solid oklch(0.78 0.14 74 / 42%);border-radius:8px;background:oklch(0.14 0.015 280 / 93%);box-shadow:0 5px 18px rgba(0,0,0,.34);color:var(--text);font-size:12px;font-weight:750;line-height:1.25;white-space:nowrap}
.objective-announce-line .arrow{padding:0 5px;color:var(--warn)}
.map-wildlife-guide{position:absolute;left:8px;bottom:8px;z-index:4}
.map-wildlife-guide>summary{display:flex;align-items:center;height:30px;padding:0 10px;border:1px solid var(--line-2);border-radius:8px;background:oklch(0.15 0.012 280 / 94%);box-shadow:0 4px 14px rgba(0,0,0,.28);color:var(--text);font-size:11.5px;font-weight:700;cursor:pointer;list-style:none;user-select:none}
.map-wildlife-guide>summary::-webkit-details-marker{display:none}
.map-wildlife-guide>summary::after{content:'⌃';margin-left:7px;color:var(--muted);font-size:11px}
.map-wildlife-guide[open]>summary::after{content:'⌄'}
.wildlife-guide-popover{position:absolute;left:0;bottom:calc(100% + 6px);width:226px;padding:9px;border:1px solid var(--line-2);border-radius:10px;background:oklch(0.14 0.012 280 / 97%);box-shadow:0 10px 28px rgba(0,0,0,.42)}
.wildlife-guide-grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:5px 8px}
.wildlife-guide-row{display:flex;align-items:center;gap:6px;min-width:0;color:var(--muted);font-size:11px;line-height:1.2;white-space:nowrap}
.wildlife-guide-marker{--guide-color:#fff;display:inline-block;flex:none;width:10px;height:10px;background:var(--guide-color);filter:drop-shadow(0 0 1px rgba(0,0,0,.9))}
.wildlife-guide-marker.triangle{clip-path:polygon(50% 0,100% 100%,0 100%)}
.wildlife-guide-marker.dot{width:7px;height:7px;margin:0 1.5px;border-radius:50%}
.wildlife-guide-marker.mutant{position:relative;clip-path:polygon(50% 0,100% 100%,0 100%);background:#9d6ed1}
.wildlife-guide-marker.mutant::after{content:"";position:absolute;left:2px;top:2px;width:6px;height:6px;background:#aeb8c6;clip-path:polygon(50% 0,100% 100%,0 100%)}
.wildlife-guide-image{display:block;flex:none;width:16px;height:14px;object-fit:contain}
.wildlife-guide-note{margin:8px 0 0;padding-top:7px;border-top:1px solid var(--line);color:var(--text);font-size:10.5px;line-height:1.35}
canvas{display:block;border:0;background:transparent}
#mapControls{margin-top:10px}
.transport{display:flex;align-items:center;gap:10px;flex-wrap:wrap}
.transport .play{min-width:66px;font-weight:600}
.transport input[type=range]{flex:1 1 240px;min-width:150px;height:28px;margin:0;padding:0;background:transparent;accent-color:oklch(0.72 0.15 300)}
.clock{flex:none;min-width:48px;text-align:right;font-size:13px;font-weight:650;font-variant-numeric:tabular-nums}
.transport2{display:flex;align-items:center;gap:10px;flex-wrap:wrap;margin-top:8px}
.seg{display:inline-flex;gap:2px;padding:2px;background:var(--card);border:1px solid var(--line);border-radius:var(--r-sm)}
.seg button{height:26px;padding:0 9px;border:0;border-radius:6px;background:transparent;color:var(--muted);font-size:11.5px;font-variant-numeric:tabular-nums}
.seg button:hover{background:var(--card-hi);color:var(--text)}
.seg button.active{background:var(--accent-soft);color:var(--text);font-weight:650;box-shadow:inset 0 0 0 1px var(--accent-line)}
.phase-field{display:inline-flex;align-items:center;gap:6px;font-size:11.5px;color:var(--muted);min-width:0}
.phase-field select{height:28px;font-size:11.5px}
.phase-note{font-size:11.5px;color:var(--faint);font-variant-numeric:tabular-nums;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
#visionGap{white-space:normal;overflow:visible;text-overflow:unset;line-height:1.45;margin:8px 0 0}
details.map-deaths{margin-top:12px;padding:12px 0 0;border-top:1px solid var(--line)}
.map-deaths-head{display:flex;align-items:baseline;gap:10px;cursor:pointer;list-style:none}
.map-deaths-head::-webkit-details-marker{display:none}
.map-deaths-head::after{content:'접기';margin-left:auto;color:var(--faint);font-size:11px;font-weight:650}
details.map-deaths:not([open])>.map-deaths-head::after{content:'펼치기'}
.map-deaths-head:hover{background:transparent}
.map-deaths h2{margin:0;font-size:13.5px;font-weight:650}
.map-deaths-lead{margin:6px 0 10px;font-size:12px;color:var(--muted);line-height:1.55}
.death-empty{margin:8px 0 0;padding:10px 12px;border:1px solid var(--line);border-radius:var(--r-sm);background:var(--card);color:var(--muted);font-size:12.5px}
.death-list{display:grid;gap:8px}
.death-card{display:block;width:100%;margin:0;padding:11px 12px;border:1px solid var(--line);border-radius:10px;background:var(--card);color:var(--text);text-align:left;cursor:pointer;font:inherit}
.death-card:hover{background:var(--card-hi);border-color:var(--line-2)}
.death-card.is-active{border-color:var(--accent-line);background:var(--accent-soft)}
.death-card-head{display:flex;align-items:center;gap:8px;flex-wrap:wrap}
.death-card-head b{font-size:16px;font-weight:700;letter-spacing:-.03em;font-variant-numeric:tabular-nums}
.death-card-head .idx{font-size:12px;color:var(--muted)}
.death-card-head .early{display:inline-flex;align-items:center;height:18px;padding:0 7px;border-radius:999px;border:1px solid oklch(0.83 0.13 78 / 35%);background:oklch(0.83 0.13 78 / 12%);color:var(--warn);font-size:10px;font-weight:650;line-height:1}
.map-deaths .life-flow{grid-template-columns:repeat(4,minmax(0,1fr));gap:4px;margin-top:8px}
.map-deaths .life-arrow{display:none}
.map-deaths .life-step{padding:8px 9px}
.map-deaths .life-step b{font-size:13px}
.death-facts{margin:8px 0 0;color:var(--muted);font-size:12.5px;line-height:1.5}
.death-cd{margin:6px 0 0;color:var(--text);font-size:12.5px;line-height:1.45;font-variant-numeric:tabular-nums;letter-spacing:-.01em}
.death-cd .is-ready{color:var(--good)}
.death-cd .is-cd{color:var(--warn)}
.death-cd .is-unknown{color:var(--faint)}
.scene-note{margin:8px 0 0;font-size:12.5px;color:var(--muted);line-height:1.5}
.map-filters{display:flex;align-items:center;gap:6px;flex-wrap:wrap;margin-top:8px}
.map-toggle{display:inline-flex;align-items:center;gap:6px;height:28px;padding:0 9px;border:1px solid var(--line);border-radius:var(--r-sm);background:var(--card);color:var(--muted);font-size:11.5px;user-select:none}
.map-toggle:hover{background:var(--card-hi);color:var(--text)}
.map-toggle input{width:13px;height:13px;margin:0;accent-color:oklch(0.72 0.15 300)}
.map-presence{display:inline-flex;align-items:center;height:28px;padding:0 9px;border:1px solid var(--line);border-radius:var(--r-sm);background:var(--well);color:var(--faint);font-size:11.5px;white-space:nowrap}
.map-presence.available{border-color:oklch(0.79 0.11 210 / 34%);color:#7deaff}

.rail{padding:10px;position:sticky;top:10px}
.rail-title{margin:0 0 8px;font-size:12.5px;font-weight:650;color:var(--muted);padding-left:2px}
.cards{display:flex;flex-direction:column;gap:10px}
.pcard{position:relative;background:var(--card);border:1px solid var(--line);border-radius:10px;padding:6px 7px 7px;overflow:hidden;cursor:pointer}
.pcard.is-selected{background:var(--card-hi)}
.pcard.is-selected::before{content:"";position:absolute;left:0;top:0;bottom:0;width:2px;background:var(--accent)}
.pcard-head{display:flex;align-items:center;justify-content:space-between;gap:6px;height:18px;padding-left:2px}
.pcard-head b{font-size:12px;font-weight:650;letter-spacing:-.01em;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.chip{flex:none;height:16px;padding:0 7px;border-radius:999px;border:1px solid var(--line);font-size:10px;font-weight:600;line-height:14px;color:var(--muted)}
.chip.alive{color:var(--good);background:oklch(0.8 0.14 165 / 12%);border-color:oklch(0.8 0.14 165 / 30%)}
.chip.down{color:var(--warn);background:oklch(0.83 0.13 78 / 12%);border-color:oklch(0.83 0.13 78 / 30%)}
.chip.dead{color:var(--faint);background:oklch(1 0 0 / 5%)}
.live-status{display:flex;align-items:center;gap:7px;height:17px;margin:2px 2px 0;color:var(--muted);font-size:10.5px;line-height:1;font-variant-numeric:tabular-nums;white-space:nowrap}
.live-status .kda{color:var(--text);font-weight:700;letter-spacing:.01em}
.live-status .metric{display:inline-flex;align-items:center;gap:2px;min-width:0}
.live-status img{display:block;width:12px;height:12px;object-fit:contain}
.live-status .restricted{display:inline-block;width:10px;height:10px;border-radius:2px;background:#e2474f;box-shadow:inset 0 0 0 1px rgba(255,255,255,.18)}
.gear{display:grid;grid-template-columns:repeat(5,minmax(0,1fr));gap:3px;margin-top:4px;background:#050507;border-radius:var(--r-xs)}
.bag{display:grid;grid-template-columns:repeat(5,minmax(0,1fr));gap:3px;margin-top:8px;background:#050507;border-radius:var(--r-xs)}
.cell{position:relative;display:grid;place-items:center;height:32px;background:#050507;border-radius:var(--r-xs);border:1px solid rgba(118,121,132,.34);overflow:hidden}
.cell{--asset-art-scale:1;--grade-core:rgba(78,80,88,.42);--grade-mid:rgba(34,36,44,.74);--grade-edge:rgba(5,5,8,.98);--grade-border:rgba(118,121,132,.46)}
.cell .item-bg{position:absolute;inset:1px;border-radius:6px;background:radial-gradient(circle at 50% 40%,var(--grade-core) 0,var(--grade-mid) 54%,var(--grade-edge) 100%);box-shadow:inset 0 0 0 1px rgba(255,255,255,.07),inset 0 -8px 16px rgba(0,0,0,.42);filter:brightness(.74) saturate(.86);z-index:0}
.cell .item-bg[hidden]{display:none!important}
.cell.has-item{border-color:var(--grade-border);background:#07070b}
.cell.has-item::after{content:"";position:absolute;inset:0;border-radius:inherit;background:linear-gradient(180deg,rgba(255,255,255,.045),rgba(0,0,0,.26));pointer-events:none;z-index:2}
.cell .art{display:block;object-fit:contain;transform-origin:center;clip-path:inset(1px);z-index:1}
.cell .art[hidden]{display:none!important}
.cell .art.white-bg-art{filter:url(#item-white-to-transparent);clip-path:inset(2px)}
.gear .cell .art{width:32px;height:32px;transform:scale(1.14)}
.bag .cell .art{width:32px;height:32px;transform:scale(var(--asset-art-scale))}
.cell .gap{font-size:10px;color:oklch(0.62 0 0);line-height:1;z-index:3}
.cell.unobserved,.cell.empty{opacity:1;outline:none;background:#050507;border-color:rgba(118,121,132,.34)}
.cell.unobserved .gap,.cell.empty .gap{display:none}
.cell.grade-Common{--grade-core:rgba(105,108,119,.42);--grade-mid:rgba(43,45,54,.76);--grade-edge:rgba(6,7,11,.98);--grade-border:rgba(128,132,145,.52)}
.cell.grade-Uncommon{--grade-core:rgba(64,166,104,.52);--grade-mid:rgba(31,84,59,.74);--grade-edge:rgba(7,20,17,.98);--grade-border:rgba(86,205,130,.58)}
.cell.grade-Rare{--grade-core:rgba(72,132,218,.56);--grade-mid:rgba(35,68,128,.76);--grade-edge:rgba(8,14,29,.98);--grade-border:rgba(91,162,250,.62)}
.cell.grade-Epic{--grade-core:rgba(143,94,255,.62);--grade-mid:rgba(80,48,150,.78);--grade-edge:rgba(18,9,35,.98);--grade-border:rgba(165,120,255,.72)}
.cell.grade-Legend{--grade-core:rgba(245,184,69,.62);--grade-mid:rgba(129,82,25,.78);--grade-edge:rgba(33,18,6,.98);--grade-border:rgba(247,196,88,.76)}
.cell.grade-Mythic{--grade-core:rgba(237,80,74,.64);--grade-mid:rgba(142,37,44,.80);--grade-edge:rgba(36,8,12,.98);--grade-border:rgba(255,104,96,.78)}
.qty{position:absolute;top:0;right:0;min-width:14px;height:13px;padding:0 3px;border-bottom-left-radius:6px;background:oklch(0.11 0.012 280 / 92%);color:var(--text);font-size:9px;font-weight:750;line-height:13px;text-align:center;font-variant-numeric:tabular-nums;text-shadow:0 1px 2px rgba(0,0,0,.75);z-index:4}
.skills{display:grid;grid-template-columns:repeat(5,minmax(0,1fr));gap:3px;margin-top:4px}
.util{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:3px;margin-top:3px}
.pcard:not(.is-selected) .skills,.pcard:not(.is-selected) .util{display:none}
.sk{display:grid;grid-template-rows:22px 16px 12px;justify-items:stretch;height:56px;padding:3px 4px;background:var(--well);border-radius:var(--r-xs);outline:1px solid var(--line);outline-offset:-1px;min-width:0}
.sk .ico{display:grid;place-items:center;min-width:0}
.sk .ico img{display:block;width:22px;height:22px;object-fit:contain}
.sk .pair{display:flex;justify-content:center}
.sk .pair img{width:17px;height:22px;margin:0 -2px 0 0}
.sk .ico .none{display:grid;place-items:center;width:22px;height:22px;border:1px dashed var(--line-2);border-radius:6px;font-size:10px;color:var(--faint)}
.sk .row{display:flex;align-items:center;justify-content:space-between;gap:4px;min-width:0}
.sk .k{flex:none;font-size:10.5px;font-weight:700;color:var(--muted);line-height:1}
.sk .cd{font-size:12px;font-weight:700;line-height:1;font-variant-numeric:tabular-nums;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.sk .n{font-size:9.5px;color:var(--faint);line-height:1;font-variant-numeric:tabular-nums;white-space:nowrap}
.sk .last{display:none}
.sk .cd.is-ready{color:var(--good)}
.sk .cd.is-cooldown{color:var(--warn)}
.sk .cd.is-unknown{color:var(--muted);font-weight:600}
.sk-util{display:grid;grid-template-columns:28px minmax(0,1fr);grid-template-rows:1fr;height:36px;align-items:center;gap:6px;padding:3px 7px}
.sk-util .ico img{width:26px;height:26px}
.sk-util .row{gap:5px;justify-content:flex-start}
.sk-util .cd{flex:0 1 auto;text-align:left;font-size:12px}
.sk-util .n{font-size:9.5px}

@media(max-width:1050px){
.stage{grid-template-columns:minmax(0,1fr)}
.rail{position:static}
.cards{display:grid;grid-template-columns:repeat(auto-fit,minmax(300px,1fr));gap:10px}
}
@media(max-width:760px){
.wrap{padding:12px 10px 28px}
.grid{grid-template-columns:repeat(2,minmax(0,1fr))}
.human-grid{grid-template-columns:repeat(2,minmax(0,1fr))}
.playstyle-grid{grid-template-columns:1fr}
.life-flow{grid-template-columns:1fr;gap:5px}.life-arrow{min-height:22px}.life-arrow span{transform:rotate(90deg)}
.map-deaths .life-flow{grid-template-columns:repeat(2,minmax(0,1fr));gap:4px}
.map-deaths .life-arrow{display:none}
.skill-grid{grid-template-columns:minmax(0,1fr)}
.topbar h1{font-size:16px}
.human-lead{display:block}
.human-kicker{display:block;margin-top:8px}
}
@media(prefers-reduced-motion:reduce){
*{transition-duration:.001ms!important;animation-duration:.001ms!important}
}
</style>
</head>
<body><svg width="0" height="0" aria-hidden="true" style="position:absolute"><filter id="item-white-to-transparent" color-interpolation-filters="sRGB"><feColorMatrix type="matrix" values="1 0 0 0 0 0 1 0 0 0 0 0 1 0 0 -1 -1 -1 0 2.95"/></filter></svg><main class="wrap">
<header class="topbar">
<div class="brand"><h1>익명 리플레이 전투 분석</h1><p>__CLIENT_VERSION__ 패치 · __MATCH_MODE__ 리플레이</p></div>
<div class="badges"><span class="badge ok">닉네임·게임 식별 정보 제거</span><span class="badge">리플레이 기록으로 분석</span></div>
</header>
<div class="toolbar">
<nav class="tabs" aria-label="분석 보기"><button type="button" class="active" data-view="overview">요약</button><button type="button" data-view="skillop">플레이 분석</button><button type="button" data-view="judgment">싸움 장면</button><button type="button" data-view="growth">성장 흐름</button><button type="button" data-view="objectives">오브젝트 동선</button><button type="button" data-view="map">이동 지도</button><button type="button" data-view="pings">핑 기록</button><button type="button" data-view="skills">스킬 기록</button><button type="button" data-view="privacy">개인정보 안내</button></nav>
<label class="picker">익명 플레이어 <select id="playerSelect"></select></label>
<p id="selectionHint" class="pick-hint"></p>
</div>
<div id="content"></div>
<p class="footer">ERCraft는 님블뉴런의 공식 서비스가 아닙니다. 분석 정확도와 리플레이 형식 호환성을 님블뉴런이 보증하지 않습니다.</p>
</main>
<script>const data=__DATA__;
const $=s=>document.querySelector(s),fmt=n=>new Intl.NumberFormat('ko-KR').format(Number(n||0)),pct=n=>`${(Number(n||0)*100).toFixed(1)}%`,esc=s=>String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const objectName=value=>{const raw=String(value||''),last=raw.charCodeAt(raw.length-1),hasBatchim=0xac00<=last&&last<=0xd7a3&&(last-0xac00)%28!==0;return `${esc(raw)}${hasBatchim?'을':'를'}`};
const state={view:'overview',playerId:data.players[0].publicPlayerId,cursor:data.meta.firstTick,playing:false,speed:1,layers:{wildlife:true,pings:true,cameras:true,controlLens:true},mapView:{zoom:1,ox:0,oy:0},deathsOpen:true,wildlifeGuideOpen:false,cameraLock:'free'};
const selected=()=>data.players.find(p=>p.publicPlayerId===state.playerId)||data.players[0];
const clock=t=>{const s=Math.max(0,Math.round((t-data.meta.firstTick)/60));return `${Math.floor(s/60)}:${String(s%60).padStart(2,'0')}`};
const teamColor=t=>['#8f7cff','#55c2ff','#65d8b0','#ffb35c','#ff7f98','#d88cff'][(Number(t)-1)%6];
function lowerBound(rows,value,key){let lo=0,hi=rows.length;while(lo<hi){const m=(lo+hi)>>1;if(key(rows[m])<value)lo=m+1;else hi=m}return lo}
 function trackAnchorAt(a,t){if(!a?.length||a[0][0]>t)return null;const i=lowerBound(a,t+1,row=>row[0])-1;return i<0?null:a[i]}
 function positionAt(p,t){const a=p.movementTrack||[];if(!a.length||a[0][0]>t)return null;const i=lowerBound(a,t+1,row=>row[0])-1,row=a[i],next=a[i+1];if(!next)return[row[1],row[2]];const dt=next[0]-row[0],source=String(row[3]||''),nextSource=String(next[3]||''),dx=next[1]-row[1],dz=next[2]-row[2],distance=Math.hypot(dx,dz),continuous=source.startsWith('CmdMove')&&(nextSource.startsWith('CmdMove')||nextSource==='CmdStopMove');if(!continuous||dt<=0||dt>120||distance>(dt/data.meta.targetFrameRate)*18+.75)return[row[1],row[2]];const q=Math.max(0,Math.min(1,(t-row[0])/dt));return[row[1]+dx*q,row[2]+dz*q]}
function worldMovementPositionAt(row,t){const a=row.movementTrack||[];if(!a.length||a[0][0]>t)return null;const i=lowerBound(a,t+1,item=>item[0])-1,current=a[i],next=a[i+1];if(!next)return[current[1],current[2]];const dt=next[0]-current[0],source=String(current[3]||''),nextSource=String(next[3]||''),moving=source.startsWith('CmdMove')&&(nextSource.startsWith('CmdMove')||nextSource==='CmdStopMove');if(!moving||dt<=0)return[current[1],current[2]];const q=Math.max(0,Math.min(1,(t-current[0])/dt));return[current[1]+(next[1]-current[1])*q,current[2]+(next[2]-current[2])*q]}
function displayPositionAt(w,t){const row=trackAnchorAt(w.displayPositionTrack||[],t);return row?[row[1],row[2]]:null}
function mapPositionAt(w,t){const a=w.mapPositionTrack||[];if(!a.length||a[0][0]>t)return null;const i=lowerBound(a,t+1,row=>row[0])-1,row=a[i],next=a[i+1],mode=String(w.mapMovementMode||''),mobile=mode.startsWith('crow-')||mode.startsWith('special-mobile-');if(!mobile||!next)return[row[1],row[2]];const source=String(row[3]||''),nextSource=String(next[3]||''),dt=next[0]-row[0],moving=source.startsWith('CmdMove')&&(nextSource.startsWith('CmdMove')||nextSource==='CmdStopMove');if(!moving||dt<=0)return[row[1],row[2]];const q=Math.max(0,Math.min(1,(t-row[0])/dt));return[row[1]+(next[1]-row[1])*q,row[2]+(next[2]-row[2])*q]}
function lifeAt(p,t){let v='alive';for(const row of p.lifeTimeline){if(row[0]>t)break;v=row[1]}return v}
function wildlifeAt(w,t){if(t<w.spawnTick||!w.initialAlive)return null;const end=[w.deathTick,w.destroyTick,w.despawnTick].filter(Number.isFinite).sort((a,b)=>a-b)[0];if(end!==undefined&&t>=end)return null;return {state:'alive',pos:mapPositionAt(w,t)}}
const restrictionUpdates=data.phaseClock.restrictionUpdates||[],rawPhaseChanges=restrictionUpdates.filter((row,i,all)=>!i||row.day!==all[i-1].day||row.dayNight!==all[i-1].dayNight||row.phase!==all[i-1].phase),phaseChanges=rawPhaseChanges.map((row,i)=>({...row,nextChangeTick:rawPhaseChanges[i+1]?.tick??null}));
function restrictionAt(t){let found=null;for(const row of restrictionUpdates){if(row.tick>t)break;found=row}return found}
function phaseAt(t){let found=null;for(const row of phaseChanges){if(row.tick>t)break;found=row}return found}
function phaseName(row){if(!row)return '페이즈 정보 대기';const light=row.dayNightName==='Day'?'낮':row.dayNightName==='Night'?'밤':row.dayNightName;return `${row.day}일차 ${light} · 페이즈 ${row.phase}`}
function phaseRemain(row,t){return row?.nextChangeTick===null||row?.nextChangeTick===undefined?null:Math.max(0,(row.nextChangeTick-t)/data.meta.targetFrameRate)}
function remainClock(value){if(value===null)return '—';const s=Math.max(0,Math.ceil(value));return `${Math.floor(s/60)}:${String(s%60).padStart(2,'0')}`}
function itemStateAt(timeline,t){const slots=new Map();let seen=false;for(const row of timeline||[]){if(row[0]>t)break;seen=true;for(const update of row[1]){const [slot,code,amount]=update;if(code===null||amount===0)slots.delete(slot);else slots.set(slot,{code,amount})}}return{seen,slots}}
function itemMeta(code){return data.itemCatalog[String(code)]||null}
const equipmentSlotNames=['무기','옷','머리','팔','다리'];
const activeCooldownFamilies=['Active1','Active2','Active3','Active4'],skillKey={Active1:'Q',Active2:'W',Active3:'E',Active4:'R'};
const cooldownFamilies=['Active1','Active2','Active3','Active4','WeaponSkill','TacticalSkill'];
const utilityFamilies=['WeaponSkill','TacticalSkill'],railFamilies=['Active1','Active2','Active3','Active4','Passive','WeaponSkill','TacticalSkill'];
const familyLabel=f=>f==='WeaponSkill'?'무기':f==='TacticalSkill'?'전술':f==='Passive'?'T':skillKey[f];
function cooldownRemaining(row,t){if(!row||row.kind!=='known')return null;if(row.held)return row.remaining;return Math.max(0,row.remaining-(t-row.tick)*100/data.meta.targetFrameRate)}
function cooldownStatesAt(p,t){const out=new Map(cooldownFamilies.map(f=>[f,{kind:'unobserved'}]));for(const event of p.skillCooldownTimeline||[]){if(event[0]>t)break;const [tick,action,family,remaining,max,stack,detail]=event;if(action==='clear'){for(const f of activeCooldownFamilies)out.set(f,{kind:'known',tick,remaining:0,max:0,stack:null,source:'character-clear'});continue}if(action==='set'){if(Number.isInteger(remaining)&&remaining>=0&&(max===null||(Number.isInteger(max)&&max>=0)))out.set(family,{kind:'known',tick,remaining,max,stack:Number.isInteger(stack)?stack:null,held:false,source:'packet'});else out.set(family,{kind:'unknown',reason:'값 미확인'});continue}if(action==='copy'){const source=detail&&out.get(detail),copied=cooldownRemaining(source,tick);if(copied===null)out.set(family,{kind:'unknown',reason:'원본 미관측'});else out.set(family,{kind:'known',tick,remaining:copied,max:source.max,stack:source.stack,held:source.held,source:'copy'});continue}if(action==='hold'){const source=out.get(family),heldRemaining=cooldownRemaining(source,tick);if(heldRemaining!==null)out.set(family,{...source,tick,remaining:heldRemaining,held:Boolean(detail),source:'hold'})}}return out}
function skillVisual(p,family){const character=data.skillAssets.icons[String(p.characterCode)],semantic={Active1:'q',Active2:'w',Active3:'e',Active4:'r'}[family];if(!semantic)return '<span class="none">?</span>';const slots=Object.entries(character?.slots||{}),exactBase=character?.slots?.[semantic],semanticCandidates=slots.filter(([,row])=>row.semanticSlot===semantic),candidates=exactBase?[[semantic,exactBase]]:semanticCandidates;if(!candidates.length)return '<span class="none">?</span>';const multiState=(p.characterCode===90&&family==='Active1')||(p.characterCode===89&&family==='Active2'),picked=multiState?semanticCandidates.slice(0,2):candidates.slice(0,2);if(picked.length===1)return `<img src="${picked[0][1].imageDataUrl}" alt="">`;return `<span class="pair">${picked.map(([,row])=>`<img src="${row.imageDataUrl}" alt="">`).join('')}</span>`}
function passiveVisual(p){const character=data.skillAssets.icons[String(p.characterCode)],slots=Object.entries(character?.slots||{}),base=character?.slots?.passive,candidates=base?[['passive',base]]:slots.filter(([,row])=>row.semanticSlot==='passive').slice(0,2);if(!candidates.length)return '<span class="none">?</span>';if(candidates.length===1)return `<img src="${candidates[0][1].imageDataUrl}" alt="">`;return `<span class="pair">${candidates.map(([,row])=>`<img src="${row.imageDataUrl}" alt="">`).join('')}</span>`}
function utilityVisual(p,family){let asset=null;if(family==='WeaponSkill')asset=data.utilitySkillAssets.weapon.characterOverrides[String(p.characterCode)]||data.utilitySkillAssets.weapon.icons[String(p.result.bestWeapon)];else if(family==='TacticalSkill')asset=data.utilitySkillAssets.tactical.icons[String(p.result.tacticalSkillGroup)];return asset?`<img src="${asset.imageDataUrl}" alt="${familyLabel(family)} 스킬">`:'<span class="none">?</span>'}
function itemArtScale(code,meta,asset){if(Number.isFinite(asset?.displayScale))return asset.displayScale;const name=meta?.itemName||'';if(name.includes('카메라')||name.includes('드론'))return 1.55;if(name.includes('스테이크'))return 1.35;if(meta?.subType==='Material'||meta?.itemType==='Misc')return 1.18;return 1}
function itemTitle(item,slot,observed){if(!item)return observed?'빈칸':'관측 전';const meta=itemMeta(item.code);return meta?.itemName||'이름 미확인'}
 function skillInfoAt(p,t){const counts=new Map();for(const row of p.skillStartTimeline||[]){if(row[0]>t)break;const family=Number.isInteger(row[3])&&row[3]>=3000000&&row[3]<4000000?'WeaponSkill':row[1];const prev=counts.get(family);counts.set(family,{count:(prev?.count||0)+1,lastTick:row[0],name:row[2]})}const states=cooldownStatesAt(p,t);return railFamilies.map(family=>{if(family==='Passive')return {status:'준비',cls:'is-ready',count:'패시브',last:'',title:'T · 패시브 · 쿨다운 없음'};const hit=counts.get(family),current=states.get(family),remaining=cooldownRemaining(current,t);let status,cls,reason='';if(current?.kind==='unobserved'){status=hit?'—':'미사용';cls='is-unknown';reason=hit?'숫자 쿨다운 미관측':'첫 사용 전'}else if(current?.kind==='unknown'){status='—';cls='is-unknown';reason=current.reason}else if(remaining<=0){status='준비';cls='is-ready'}else{status=`${(remaining/100).toFixed(1)}초`;cls='is-cooldown'}const stack=current?.kind==='known'&&Number.isInteger(current.stack)&&current.stack>0?` · ${current.stack}스택`:'';return {status,cls,count:hit?`${hit.count}회`:'0회',last:'',title:`${hit?.name||familyLabel(family)}${reason?` · ${reason}`:''}${stack} · 마지막 사용 ${hit?clock(hit.lastTick):'없음'} · 실제 시전 가능 여부 아님`}})}
function itemCellHtml(){return '<div class="cell"><span class="item-bg" aria-hidden="true" hidden></span><img class="art item-image" alt="" hidden><span class="gap">…</span><span class="qty" hidden></span></div>'}
function skillCellHtml(p,family){const label=familyLabel(family);if(utilityFamilies.includes(family))return `<div class="sk sk-util"><span class="ico">${utilityVisual(p,family)}</span><span class="row"><b class="cd is-unknown">—</b><span class="n"></span></span><span class="last" hidden></span></div>`;return `<div class="sk"><span class="ico">${family==='Passive'?passiveVisual(p):skillVisual(p,family)}</span><span class="row"><span class="k">${label}</span><b class="cd is-unknown">—</b></span><span class="row"><span class="n"></span><span class="last" hidden></span></span></div>`}
function cellRefs(el){return {el,bg:el.querySelector('.item-bg'),art:el.querySelector('.art'),gap:el.querySelector('.gap'),qty:el.querySelector('.qty'),code:undefined,grade:'',glyph:'',title:''}}
function skillRefs(el){return {el,cd:el.querySelector('.cd'),n:el.querySelector('.n'),last:el.querySelector('.last'),cls:'is-unknown',title:''}}
let rail=null,railTeamKey='',railLast=0,railStats={n:0,sum:0,max:0};
function buildTeamRail(team){const host=$('#teamLoadout');if(!host)return;const kiosk=data.mapMarkerAssets.icons.kiosk?.imageDataUrl||'',gadget=data.uiAssets.gadgetPoint.imageDataUrl;host.innerHTML=team.map((p,i)=>`<article class="pcard${i===0?' is-selected':''}"><div class="pcard-head"><b>팀${p.teamNumber} · ${esc(p.characterName)}</b><span class="chip"></span></div><div class="live-status"><span class="kda">0/0/0</span><span class="metric credit"><img src="${kiosk}" alt="크레딧"><b>—</b></span><span class="metric survive"><span class="restricted" aria-hidden="true"></span><b>—</b></span><span class="metric gadget"><img src="${gadget}" alt="가젯 포인트"><b>—</b></span></div><div class="gear">${equipmentSlotNames.map(itemCellHtml).join('')}</div><div class="bag">${Array.from({length:10},itemCellHtml).join('')}</div><div class="skills">${['Active1','Active2','Active3','Active4','Passive'].map(f=>skillCellHtml(p,f)).join('')}</div><div class="util">${utilityFamilies.map(f=>skillCellHtml(p,f)).join('')}</div></article>`).join('');rail={cards:Array.from(host.children).map((el,i)=>({el,player:team[i],chip:el.querySelector('.chip'),life:'',live:{el:el.querySelector('.live-status'),kda:el.querySelector('.kda'),credit:el.querySelector('.credit b'),survive:el.querySelector('.survive b'),gadget:el.querySelector('.gadget b'),value:''},gear:Array.from(el.querySelectorAll('.gear .cell')).map(cellRefs),bag:Array.from(el.querySelectorAll('.bag .cell')).map(cellRefs),skills:Array.from(el.querySelectorAll('.sk')).map(skillRefs)}))};
 for(const card of rail.cards){card.el.dataset.playerId=String(card.player.publicPlayerId);card.el.setAttribute('role','button');card.el.tabIndex=0;card.el.addEventListener('click',()=>focusRailPlayer(card.player.publicPlayerId));card.el.addEventListener('keydown',e=>{if(e.key==='Enter'||e.key===' '){e.preventDefault();focusRailPlayer(card.player.publicPlayerId)}})}
}
function syncSelectedPicker(){const s=$('#playerSelect');if(s)s.value=String(state.playerId);const hint=$('#selectionHint');if(hint)hint.textContent=`${selected().publicLabel}은 이 보고서 안에서만 쓰는 임시 번호입니다.`}
function focusRailPlayer(id){state.playerId=Number(id);state.cameraLock='focus';if(state.view!=='map'){render();return}syncSelectedPicker();updateTeamRail();updateVisionGap();const deaths=$('#mapDeaths');if(deaths){deaths.outerHTML=mapDeathReviewHtml();bindMapDeaths()}centerOnSelectedPlayer();syncTransport();drawMap();syncPhaseNote()}
function timelineRowAt(rows,t){const row=trackAnchorAt(rows||[],t);return row||null}
function liveStatusAt(p,t){const kda=timelineRowAt(p.kdaTimeline,t)||[t,0,0,0],observer=timelineRowAt(p.observerStatusTimeline,t),survivable=timelineRowAt(p.survivableTimeTimeline,t);return{kda:`${kda[1]}/${kda[2]}/${kda[3]}`,credit:observer?String(Math.floor(observer[1])):'—',creditExact:observer?observer[1]:null,gadget:observer?String(observer[2]):'—',survive:survivable?String(survivable[1]):'—',observerTick:observer?.[0]??null,survivableTick:survivable?.[0]??null}}
function paintLiveStatus(ref,info){const value=`${info.kda}|${info.credit}|${info.survive}|${info.gadget}|${info.observerTick}|${info.survivableTick}`;if(value===ref.value)return;ref.value=value;ref.kda.textContent=info.kda;ref.credit.textContent=info.credit;ref.survive.textContent=info.survive;ref.gadget.textContent=info.gadget;ref.el.title=`K/D/A ${info.kda} · 크레딧 ${info.creditExact===null?'미관측':info.creditExact} · 금지구역 ${info.survive==='—'?'미관측':`${info.survive}초`} · 가젯 ${info.gadget}`}
 function paintItemCell(ref,item,observed,title){const code=item?item.code:null;if(code!==ref.code){ref.code=code;const asset=code===null?null:data.itemAssets.icons[String(code)],meta=code===null?null:itemMeta(code);ref.el.style.setProperty('--asset-art-scale',String(itemArtScale(code,meta,asset)));ref.art.classList.toggle('white-bg-art',asset?.backgroundTreatment==='white-to-transparent');if(asset){ref.art.src=asset.imageDataUrl;ref.art.alt=meta?.itemName||'';ref.art.hidden=false;ref.gap.hidden=true}else{ref.art.removeAttribute('src');ref.art.alt='';ref.art.hidden=true;ref.gap.hidden=false}if(ref.bg)ref.bg.hidden=code===null;ref.el.classList.toggle('has-item',code!==null);const grade=code===null?'':(meta?.itemGrade||'');if(grade!==ref.grade){if(ref.grade)ref.el.classList.remove(`grade-${ref.grade}`);if(grade)ref.el.classList.add(`grade-${grade}`);ref.grade=grade}}
 const glyph=code===null?(observed?'—':'…'):(data.itemAssets.icons[String(code)]?'':'?');if(glyph&&glyph!==ref.glyph){ref.gap.textContent=glyph;ref.glyph=glyph}
 const amount=item&&item.amount>1?String(item.amount):'';if(amount){if(ref.qty.textContent!==amount)ref.qty.textContent=amount;ref.qty.hidden=false}else if(!ref.qty.hidden)ref.qty.hidden=true;
 ref.el.classList.toggle('unobserved',!observed);ref.el.classList.toggle('empty',code===null);if(title!==ref.title){ref.el.title=title;ref.title=title}}
function paintSkillCell(ref,info){if(ref.cd.textContent!==info.status)ref.cd.textContent=info.status;if(ref.cls!==info.cls){ref.cd.className=`cd ${info.cls}`;ref.cls=info.cls}if(ref.n.textContent!==info.count)ref.n.textContent=info.count;if(ref.last.textContent!==info.last)ref.last.textContent=info.last;if(ref.title!==info.title){ref.el.title=info.title;ref.title=info.title}}
function updateTeamRail(){const host=$('#teamLoadout');if(!host)return;const focus=selected(),team=data.players.filter(p=>p.teamNumber===focus.teamNumber).sort((a,b)=>(a.publicPlayerId===focus.publicPlayerId?-1:b.publicPlayerId===focus.publicPlayerId?1:a.publicPlayerId-b.publicPlayerId)),key=team.map(p=>p.publicPlayerId).join(',');
 if(key!==railTeamKey||!rail||host.childElementCount!==team.length){railTeamKey=key;buildTeamRail(team)}
 const t=state.cursor;
 for(const card of rail.cards){const p=card.player,life=lifeAt(p,t);card.el.classList.toggle('is-selected',p.publicPlayerId===focus.publicPlayerId);
 if(life!==card.life){card.life=life;card.chip.textContent=life==='alive'?'생존':life==='down'?'다운':'사망';card.chip.className=`chip ${life}`}
  paintLiveStatus(card.live,liveStatusAt(p,t));
  const equipment=itemStateAt(p.equipmentTimeline,t),inventory=itemStateAt(p.inventoryTimeline,t);
  for(let slot=0;slot<card.gear.length;slot++){const item=equipment.slots.get(slot);paintItemCell(card.gear[slot],item,equipment.seen,itemTitle(item,equipmentSlotNames[slot],equipment.seen))}
  for(let slot=0;slot<card.bag.length;slot++){const item=inventory.slots.get(slot);paintItemCell(card.bag[slot],item,inventory.seen,itemTitle(item,String(slot+1),inventory.seen))}
  if(p.publicPlayerId===focus.publicPlayerId){const info=skillInfoAt(p,t);for(let i=0;i<card.skills.length&&i<info.length;i++)paintSkillCell(card.skills[i],info[i])}}}
function maybeUpdateTeamRail(){const now=performance.now();if(state.playing&&now-railLast<200)return;railLast=now;updateTeamRail();updateTeamCombatLog();const cost=performance.now()-now;railStats.n++;railStats.sum+=cost;railStats.max=Math.max(railStats.max,cost);const host=$('#teamLoadout');if(host){host.dataset.lastUpdateMs=cost.toFixed(2);host.dataset.avgUpdateMs=(railStats.sum/railStats.n).toFixed(2);host.dataset.updateHz=state.playing?'5':'on-demand'}}
const playerByPublicId=id=>data.players.find(row=>row.publicPlayerId===id)||null;
function teamLogRowsAt(t){const rows=data.teamCombatLog?.items||[],team=selected().teamNumber,windowTicks=(data.teamCombatLog?.displaySeconds||8)*data.meta.targetFrameRate,start=lowerBound(rows,t-windowTicks,row=>row.tick),visible=[];for(let i=start;i<rows.length&&rows[i].tick<=t;i++){const row=rows[i],ids=[row.attackerPublicPlayerId,row.victimPublicPlayerId,row.downAttackerPublicPlayerId].filter(Number.isInteger);if(ids.some(id=>playerByPublicId(id)?.teamNumber===team))visible.push(row)}return visible.slice(-3)}
function teamLogActor(id){const p=playerByPublicId(id);return p?`팀${p.teamNumber} ${p.characterName}`:'공격자 미확인'}
let teamCombatLogKey='',visionGapKey='';
function updateTeamCombatLog(){const host=$('#mapTeamCombatLog');if(!host)return;const rows=teamLogRowsAt(state.cursor),key=`${selected().teamNumber}|${rows.map(row=>row.publicEventId).join(',')}`;if(key===teamCombatLogKey)return;teamCombatLogKey=key;if(!rows.length){host.hidden=true;host.replaceChildren();return}host.hidden=false;host.innerHTML=rows.map(row=>{const kind=row.kind==='down'?'다운':row.kind==='death'?'사망':'처치',attacker=row.attackerPublicPlayerId??row.downAttackerPublicPlayerId,victim=teamLogActor(row.victimPublicPlayerId),actors=attacker?`${esc(teamLogActor(attacker))}<span class="arrow">→</span>${esc(victim)}`:esc(victim);return `<div class="team-log-line ${row.kind}" data-event-id="${row.publicEventId}"><span class="kind">${kind}</span><span class="actors">${actors}</span></div>`}).join('')}
function renderOptions(){const s=$('#playerSelect');s.innerHTML=data.players.map(p=>`<option value="${p.publicPlayerId}">${p.publicLabel} · 팀 ${p.teamNumber} · ${esc(p.characterName)}</option>`).join('');s.value=state.playerId;s.onchange=()=>{state.playerId=Number(s.value);render()};$('#selectionHint').textContent=`${selected().publicLabel}은 이 보고서 안에서만 쓰는 임시 번호입니다.`}
function renderOverview(){const p=selected(),r=p.result,team=data.players.filter(x=>x.teamNumber===p.teamNumber),known=p.stats.damageTimelineCoverage?.outgoing?.knownTimelineDamage||0,exact=r.damageToPlayer||0;$('#content').innerHTML=`<section class="panel"><h2>${p.publicLabel} · ${esc(p.characterName)} · 팀 ${p.teamNumber}</h2><div class="grid"><article class="stat"><span>최종 순위</span><b>${r.gameRank}위</b></article><article class="stat"><span>TK / 킬 / 어시 / 사망</span><b>${r.teamKill} / ${r.playerKill} / ${r.playerAssistant} / ${r.playerDeaths}</b></article><article class="stat"><span>플레이어 피해</span><b>${fmt(exact)}</b></article><article class="stat"><span>받은 피해</span><b>${fmt(r.damageFromPlayer)}</b></article><article class="stat"><span>전투 시간</span><b>${p.stats.combatSeconds.toFixed(1)}초</b></article><article class="stat"><span>전투 세션</span><b>${fmt(p.stats.combatSessions)}</b></article><article class="stat"><span>야생동물</span><b>${fmt(r.monsterKill??r.killMonsters)}</b></article><article class="stat"><span>EMP 드론</span><b>${fmt(r.useEmpDrone)}</b></article></div><div class="notice" style="margin-top:12px">종료 결과 총피해 ${fmt(exact)}는 exact입니다. 개별 숫자가 남은 피해 패킷 합 ${fmt(known)}과의 차이는 null 패킷이나 특정 스킬에 나눠 붙이지 않았습니다.</div></section><section class="panel"><h2>같은 팀 비교</h2><div class="table-wrap"><table><thead><tr><th>익명 표기</th><th>실험체</th><th class="num">킬</th><th class="num">어시</th><th class="num">사망</th><th class="num">준 피해</th><th class="num">받은 피해</th><th class="num">시야 기여</th></tr></thead><tbody>${team.map(x=>{const q=x.result;return `<tr class="${x.publicPlayerId===p.publicPlayerId?'selected':''}"><td><b>${x.publicLabel}</b></td><td>${esc(x.characterName)}</td><td class="num">${q.playerKill}</td><td class="num">${q.playerAssistant}</td><td class="num">${q.playerDeaths}</td><td class="num">${fmt(q.damageToPlayer)}</td><td class="num">${fmt(q.damageFromPlayer)}</td><td class="num">${fmt(q.viewContribution)}</td></tr>`}).join('')}</tbody></table></div></section><section class="panel"><h2>피해·운영 세부</h2><div class="grid"><article class="stat"><span>기본 공격 피해</span><b>${fmt(r.damageToPlayer_basic)}</b></article><article class="stat"><span>스킬 계열 피해</span><b>${fmt(r.damageToPlayer_skill)}</b></article><article class="stat"><span>직접 유형 피해</span><b>${fmt(r.damageToPlayer_direct)}</b><small>공식 피해 분류값 · 횟수 아님 · 세부 기준 미공개</small></article><article class="stat"><span>보호 흡수</span><b>${fmt(r.protectAbsorb)}</b></article><article class="stat"><span>보안 콘솔</span><b>${fmt(r.useSecurityConsole)}</b></article><article class="stat"><span>정찰 드론</span><b>${fmt(r.useReconDrone)}</b></article><article class="stat"><span>획득 크레딧</span><b>${fmt(r.totalGainVFCredit)}</b></article><article class="stat"><span>사용 크레딧</span><b>${fmt(r.totalUseVFCredit)}</b></article></div></section>`}
function renderAreas(){const a=data.eventAreas,ping=a.ping,mapPing=data.tacticalPings;$('#content').innerHTML=`<section class="panel"><h2>이벤트 영역</h2><p class="hint" style="white-space:normal">${fmt(a.packetPayloadCount)}개 패킷을 이름 기준 8개 영역으로 정리했습니다. 분류명은 보기 위한 파생값이고 각 패킷 수는 exact입니다.</p><div class="grid">${a.areas.map(x=>`<article class="stat"><span>${esc(x.description)}</span><b>${esc(x.label)}</b><small>${fmt(x.eventCount)}건 · ${fmt(x.packetTypeCount)}종</small></article>`).join('')}</div></section><section class="panel"><h2>영역별 많이 나온 이벤트</h2>${a.areas.map(x=>`<details ${x.key==='communication-ui'?'open':''}><summary><b>${esc(x.label)}</b> · ${fmt(x.eventCount)}건</summary><div class="table-wrap" style="margin-top:8px"><table><thead><tr><th>이벤트</th><th>원본 패킷</th><th class="num">건수</th></tr></thead><tbody>${x.topEvents.map(e=>`<tr><td>${esc(e.label)}</td><td><code>${esc(e.packetName)}</code></td><td class="num">${fmt(e.count)}</td></tr>`).join('')}</tbody></table></div></details>`).join('')}</section><section class="panel"><h2>핑</h2><div class="grid"><article class="stat"><span>지도에 표시하는 전체 핑</span><b>${fmt(mapPing.count)}건</b><small>선택 플레이어 필터 없음</small></article><article class="stat"><span>제외한 하이퍼루프 계열</span><b>${fmt(mapPing.excludedHyperloopPingCount)}건</b><small>자동 소음도 미포함</small></article><article class="stat"><span>원본 전술 핑</span><b>${fmt(ping.eventCount)}건</b><small>exact decode ${fmt(ping.exactCount)}건</small></article><article class="stat"><span>시스템 핑·메시지</span><b>${fmt(ping.systemMessageCount)}건</b><small>지도에는 표시하지 않음</small></article></div><div class="notice" style="margin-top:12px"><b>확인 가능한 값:</b> ${ping.availableFields.map(esc).join(' · ')||'이 경기에서는 전술 핑 미관측'}<br>${esc(ping.publicBoundary)}</div><div class="badges" style="margin-top:12px">${ping.types.map(x=>`<span class="badge">${esc(x.label)}</span>`).join('')}</div></section>`}
function teamRankText(p,getValue,label){
 const team=data.players.filter(row=>row.teamNumber===p.teamNumber),ranked=[...team].sort((a,b)=>Number(getValue(b)||0)-Number(getValue(a)||0)),rank=ranked.findIndex(row=>row.publicPlayerId===p.publicPlayerId)+1;
 if(team.length<=1)return `${label} ${fmt(getValue(p))}`;
 return `${label}는 ${fmt(getValue(p))}로 팀 ${fmt(team.length)}명 중 ${rank===1?'가장 많았어요':rank===team.length?'가장 적었어요':`${fmt(rank)}번째였어요`}.`;
}
function renderOverviewHuman(){
 const p=selected(),r=p.result,story=playstyleNarrative(p),team=data.players.filter(x=>x.teamNumber===p.teamNumber),known=p.stats.damageTimelineCoverage?.outgoing?.knownTimelineDamage||0,exact=r.damageToPlayer||0;
 const involvement=r.teamKill>0?Math.min(1,(Number(r.playerKill||0)+Number(r.playerAssistant||0))/Number(r.teamKill)):null;
 const teamStory=`${teamRankText(p,row=>row.result.damageToPlayer,'준 피해')} ${teamRankText(p,row=>row.result.damageFromPlayer,'받은 피해')} ${involvement===null?'팀 처치 관여율은 계산할 수 없었어요.':`팀 처치 ${fmt(r.teamKill)}회 중 킬 또는 어시스트로 연결된 비율은 ${pct(involvement)}였어요.`}`;
 const operation=[];if(r.viewContribution)operation.push(`시야 기여 ${fmt(r.viewContribution)}`);if(r.useSecurityConsole)operation.push(`보안 콘솔 ${fmt(r.useSecurityConsole)}회`);if(r.useReconDrone)operation.push(`정찰 드론 ${fmt(r.useReconDrone)}회`);if(r.useEmpDrone)operation.push(`EMP 드론 ${fmt(r.useEmpDrone)}회`);if(r.protectAbsorb)operation.push(`보호 흡수 ${fmt(r.protectAbsorb)}`);
 const teamRows=team.map(x=>{const q=x.result;return `<tr class="${x.publicPlayerId===p.publicPlayerId?'selected':''}"><td><b>${x.publicLabel}</b> · ${esc(x.characterName)}</td><td class="num">${q.playerKill}/${q.playerAssistant}/${q.playerDeaths}</td><td class="num">${fmt(q.damageToPlayer)}</td><td class="num">${fmt(q.damageFromPlayer)}</td><td class="num">${fmt(q.viewContribution)}</td></tr>`}).join('');

 $('#content').innerHTML=`<section class="panel"><h2>${p.publicLabel} · ${esc(p.characterName)} 경기 요약</h2><div class="human-lead"><div><strong>${esc((sceneCoaching()?.feedback?.nextPlay||{}).headline||story.title)}</strong><p>${esc((sceneCoaching()?.feedback?.nextPlay||{}).doThis||'')}</p></div><span class="human-kicker">다음에 같은 장면이면</span></div><div class="analysis-prose"><article class="analysis-paragraph"><h3>이번에 유지할 선택</h3><p>${esc((sceneCoaching()?.feedback?.nextPlay||{}).keep||'')}</p></article><article class="analysis-paragraph"><h3>이렇게 읽은 근거</h3><p>${esc(((sceneCoaching()?.feedback?.nextPlay||{}).because||[]).join(' '))} ${esc((sceneCoaching()?.feedback?.nextPlay||{}).excludedNote||'')}</p></article><article class="analysis-paragraph"><h3>결과만 보면</h3><p>${r.gameRank}위, 팀 처치 ${fmt(r.teamKill)}회, 개인 ${fmt(r.playerKill)}킬 ${fmt(r.playerAssistant)}어시스트 ${fmt(r.playerDeaths)}사망이었어요. ${teamStory}</p></article></div><details class="analysis-details"><summary>종료 결과와 팀 수치 확인하기</summary><div class="detail-body"><div class="grid"><article class="stat"><span>플레이어 피해</span><b>${fmt(exact)}</b></article><article class="stat"><span>받은 피해</span><b>${fmt(r.damageFromPlayer)}</b></article><article class="stat"><span>전투 시간</span><b>${p.stats.combatSeconds.toFixed(1)}초</b></article><article class="stat"><span>전투 장면</span><b>${fmt(p.stats.combatSessions)}번</b></article><article class="stat"><span>기본 공격 피해</span><b>${fmt(r.damageToPlayer_basic)}</b></article><article class="stat"><span>스킬 피해</span><b>${fmt(r.damageToPlayer_skill)}</b></article><article class="stat"><span>직접 유형 피해</span><b>${fmt(r.damageToPlayer_direct)}</b></article><article class="stat"><span>야생동물 처치</span><b>${fmt(r.monsterKill??r.killMonsters)}</b></article></div><div class="table-wrap"><table><thead><tr><th>팀원</th><th class="num">킬/어시/사망</th><th class="num">준 피해</th><th class="num">받은 피해</th><th class="num">시야 기여</th></tr></thead><tbody>${teamRows}</tbody></table></div><p class="judgment-note">피해 패킷에서 수치가 비어 있는 부분은 특정 스킬에 임의로 나누지 않았습니다. 팀 내 비교는 이 경기의 세 명만 놓고 본 순서이며 등급이 아닙니다. 시작 후 2분 안 교전은 대표 장면과 코칭에 넣지 않았습니다.</p></div></details></section>`;
}
 const lumiaSpace={spaceId:'lumia',label:'루미아 섬',image:data.map.coordinateSpace,sourceRect:data.map.imageContentRect},secondarySpaces=data.map.secondarySpaces||[];let activeMapSpace=lumiaSpace;
 function lumiaProject(pos){if(!pos)return null;const x=data.map.projection.pixelX,y=data.map.projection.pixelY;return[x[0]*pos[0]+x[1]*pos[1]+x[2],y[0]*pos[0]+y[1]*pos[1]+y[2]]}
 function rawInsideSpace(pos,space){if(!pos||space.spaceId==='lumia')return false;const b=space.bounds;return b.xMin<=pos[0]&&pos[0]<=b.xMax&&b.zMin<=pos[1]&&pos[1]<=b.zMax}
 function mapSpaceAt(t){const p=selected(),raw=positionAt(p,t),lumia=lumiaProject(raw),space=lumiaSpace.image;if(lumia&&0<=lumia[0]&&lumia[0]<=space.w&&0<=lumia[1]&&lumia[1]<=space.h)return lumiaSpace;return secondarySpaces.find(space=>space.teamNumbers.includes(p.teamNumber)&&rawInsideSpace(raw,space))||lumiaSpace}
 function project(pos){if(!pos)return null;if(activeMapSpace.spaceId==='lumia')return lumiaProject(pos);if(activeMapSpace.projection.type==='affine-secondary-world-xz-to-pixel.v1'){const p=activeMapSpace.projection;return[p.pixelX[0]*pos[0]+p.pixelX[1]*pos[1]+p.pixelX[2],p.pixelY[0]*pos[0]+p.pixelY[1]*pos[1]+p.pixelY[2]]}const p=activeMapSpace.projection,lx=pos[0]-p.originWorld[0],lz=pos[1]-p.originWorld[1];return[p.pixelOrigin[0]+p.pixelPerWorldUnit*(-lx+lz),p.pixelOrigin[1]+p.pixelPerWorldUnit*(-lx-lz)]}
 let mapLayout=null,mapBackground=null,wildlifeSweep=null,paintStats={n:0,sum:0,max:0},resizeQueued=false;
 const MAP_ZOOM_MIN=1,MAP_ZOOM_MAX=6;
 function mapView(){if(!state.mapView)state.mapView={zoom:1,ox:0,oy:0};return state.mapView}
 function markerScale(){return Math.min(2.35,Math.max(0.92,Math.pow(mapView().zoom,0.78)))}
 function clampMapPan(layout){const v=mapView();if(!layout)return;if(v.zoom<=MAP_ZOOM_MIN){v.zoom=MAP_ZOOM_MIN;v.ox=0;v.oy=0;return}v.ox=Math.min(0,Math.max(layout.w-layout.w*v.zoom,v.ox));v.oy=Math.min(0,Math.max(layout.h-layout.h*v.zoom,v.oy))}
 function freeCamera(){state.cameraLock='free'}
 function centerOnSelectedPlayer(layout){if(!layout)layout=mapLayout||layoutMap();if(!layout)return false;const p=selected(),raw=positionAt(p,state.cursor),pos=project(raw),mapImage=activeMapSpace.image||data.map.image;if(!pos||pos[0]<0||pos[0]>mapImage.w||pos[1]<0||pos[1]>mapImage.h)return false;const v=mapView();v.zoom=MAP_ZOOM_MAX;v.ox=layout.w/2-(pos[0]/mapImage.w*layout.w)*v.zoom;v.oy=layout.h/2-(pos[1]/mapImage.h*layout.h)*v.zoom;clampMapPan(layout);return true}
 function zoomMapAt(next,cx,cy){const layout=mapLayout||layoutMap();if(!layout)return;const v=mapView(),z=v.zoom,nz=Math.min(MAP_ZOOM_MAX,Math.max(MAP_ZOOM_MIN,next));const wx=(cx-v.ox)/z,wy=(cy-v.oy)/z;v.zoom=nz;v.ox=cx-wx*nz;v.oy=cy-wy*nz;clampMapPan(layout)}
 function wildlifeRenderEnd(animal){const end=[animal.deathTick,animal.destroyTick,animal.despawnTick].filter(Number.isFinite).sort((a,b)=>a-b)[0];return end===undefined?Infinity:end}
 function resetWildlifeSweep(t){const rows=window.__wildlifeBySpawn||data.wildlife.instances,active=new Map();for(const animal of rows){if(animal.spawnTick>t)break;if(wildlifeRenderEnd(animal)>=t)active.set(animal.publicWildlifeId,animal)}wildlifeSweep={last:t,next:lowerBound(rows,t+Number.EPSILON,row=>row.spawnTick),active};return active.values()}
 function wildlifeCandidatesAt(t){const rows=window.__wildlifeBySpawn||data.wildlife.instances;if(!wildlifeSweep||t<wildlifeSweep.last||t-wildlifeSweep.last>180)return resetWildlifeSweep(t);while(wildlifeSweep.next<rows.length&&rows[wildlifeSweep.next].spawnTick<=t){const animal=rows[wildlifeSweep.next++];if(wildlifeRenderEnd(animal)>=t)wildlifeSweep.active.set(animal.publicWildlifeId,animal)}for(const [id,animal] of wildlifeSweep.active){if(wildlifeRenderEnd(animal)<t)wildlifeSweep.active.delete(id)}wildlifeSweep.last=t;return wildlifeSweep.active.values()}
 function wildlifeMarkersAt(t){const visible=[],groupCounts=new Map(),groupSeen=new Map();let aliveInstances=0,unmappedInstances=0;for(const animal of wildlifeCandidatesAt(t)){const life=wildlifeAt(animal,t);if(!life)continue;aliveInstances++;if(!life.pos){unmappedInstances++;continue}const groupId=animal.publicWildlifeGroupId;groupCounts.set(groupId,(groupCounts.get(groupId)||0)+1);visible.push({animal,pos:life.pos,groupId,offsetIndex:0,offsetCount:1})}for(const row of visible){const used=groupSeen.get(row.groupId)||0;groupSeen.set(row.groupId,used+1);row.offsetIndex=used;row.offsetCount=groupCounts.get(row.groupId)||1}return{visible,aliveInstances,unmappedInstances}}
 function wildlifeScreenOffset(index,count,size){if(count<=1)return[0,0];const radius=Math.min(size*.62,4+count*.55),angle=index*Math.PI*2/count-Math.PI/2;return[Math.cos(angle)*radius,Math.sin(angle)*radius]}
 function wildlifeAssetKeyAt(animal,t){let key=animal.mapMarkerAssetKey;for(const row of animal.mapMarkerAssetTimeline||[]){if(row[0]>t)break;key=row[1]}return key}
 function drawWildlifeMarker(g,animal,x,y,size,alpha,t){if(animal.mapMarkerStyle==='special-icon')return drawMarkerAsset(g,wildlifeAssetKeyAt(animal,t),x,y,size,false,alpha);const color=animal.mapMarkerColor,drone=animal.mapMarkerStyle==='neutral-monster-red-dot';if(!color)return false;g.save();g.globalAlpha=alpha;g.lineJoin='round';g.lineCap='round';g.strokeStyle='rgba(8,10,16,.9)';g.fillStyle=color;if(drone){g.lineWidth=1;g.beginPath();g.arc(x,y,Math.max(2.2,size*.32),0,Math.PI*2);g.fill();g.stroke();g.restore();return true}const h=size*.72,w=size*.82,mutant=animal.mapMarkerStyle==='mutant-purple-outline',dark=color.toLowerCase()==='#111318';g.lineWidth=Math.max(mutant?2:size*.1,mutant?size*.16:1.15);g.strokeStyle=mutant?'#9d6ed1':dark?'rgba(243,245,247,.82)':'rgba(8,10,16,.9)';g.beginPath();g.moveTo(x,y-h*.5);g.lineTo(x+w*.5,y+h*.42);g.lineTo(x-w*.5,y+h*.42);g.closePath();g.fill();g.stroke();g.restore();return true}
 function ensureMapBackground(layout,space){const key=`${space.spaceId}:${layout.w}:${layout.h}:${layout.d}`;if(mapBackground?.key===key)return mapBackground.canvas;const bg=document.createElement('canvas');bg.width=Math.round(layout.w*layout.d);bg.height=Math.round(layout.h*layout.d);const gg=bg.getContext('2d');gg.setTransform(layout.d,0,0,layout.d,0,0);if(space.spaceId==='lumia'){const img=window.__mapImage,r=space.sourceRect;if(!img?.complete||!img.naturalWidth)return null;gg.drawImage(img,r.x,r.y,r.w,r.h,0,0,layout.w,layout.h)}else{const img=window.__mapMarkerImages?.[space.backgroundAssetKey];if(!img?.complete||!img.naturalWidth)return null;gg.drawImage(img,0,0,img.naturalWidth,img.naturalHeight,0,0,layout.w,layout.h)}mapBackground={key,canvas:bg};return bg}
 function layoutMap(){const c=$('#mapCanvas');if(!c)return null;const shell=c.parentElement,availW=Math.max(260,shell.clientWidth||600),controls=$('#mapControls'),controlsH=controls?controls.getBoundingClientRect().height:0,image=activeMapSpace.image||data.map.image,ratio=image.h/image.w,stacked=window.innerWidth<=1050,budget=stacked?Math.max(320,window.innerHeight*0.8):Math.max(300,window.innerHeight-shell.getBoundingClientRect().top-controlsH-28);let h=Math.min(budget,availW*ratio),w=h/ratio;if(w>availW){w=availW;h=w*ratio}w=Math.round(w);h=Math.round(h);const d=Math.min(2,window.devicePixelRatio||1),pw=Math.round(w*d),ph=Math.round(h*d);c.style.width=`${w}px`;c.style.height=`${h}px`;if(c.width!==pw)c.width=pw;if(c.height!==ph)c.height=ph;mapLayout={w,h,d,spaceId:activeMapSpace.spaceId};return mapLayout}
 const worldStatic=data.worldMap.staticObjects||[],lumiRows=worldStatic.filter(row=>row.category==='lumi'),worldEvents=data.worldMap.timeline||[],movementPings=data.worldMap.movementPings||[],transportTransitions=data.worldMap.transportModeTransitions||[];
 const wildlifeRows=data.wildlife?.instances||[],majorObjectiveCodes=new Set([7,8,9]),majorObjectiveAnnouncements=wildlifeRows.filter(row=>majorObjectiveCodes.has(Number(row.monsterCode))&&Number.isFinite(row.deathTick)&&Number.isFinite(row.killerPublicPlayerId)).map(row=>{const killer=data.players.find(player=>player.publicPlayerId===row.killerPublicPlayerId);return killer?{tick:row.deathTick,teamNumber:killer.teamNumber,characterName:killer.characterName,objectiveName:row.monsterName}:null}).filter(Boolean).sort((a,b)=>a.tick-b.tick),objectiveAnnounceTicks=5*data.meta.targetFrameRate;
 function wildlifeGuideHtml(){const seen=new Set(),rows=[];for(const animal of wildlifeRows){const style=String(animal.mapMarkerStyle||'');if(!['species-triangle','mutant-purple-outline','neutral-monster-red-dot','special-icon'].includes(style)||majorObjectiveCodes.has(Number(animal.monsterCode))||/^Monster\s/i.test(String(animal.monsterName||'')))continue;const key=`${style}|${animal.monsterName}`;if(seen.has(key))continue;seen.add(key);const color=/^#[0-9a-f]{6}$/i.test(String(animal.mapMarkerColor||''))?animal.mapMarkerColor:'#ffffff',asset=animal.mapMarkerAssetKey?data.mapMarkerAssets.icons[animal.mapMarkerAssetKey]?.imageDataUrl:null;rows.push({code:Number(animal.monsterCode)||9999,name:animal.monsterName,style,color,asset})}rows.sort((a,b)=>a.code-b.code||String(a.name).localeCompare(String(b.name),'ko'));return `<div class="wildlife-guide-popover"><div class="wildlife-guide-grid">${rows.map(row=>`<span class="wildlife-guide-row">${row.style==='special-icon'?`<img class="wildlife-guide-image" src="${row.asset}" alt="">`:`<i class="wildlife-guide-marker ${row.style==='neutral-monster-red-dot'?'dot':'triangle'}" style="--guide-color:${row.color}"></i>`}${esc(row.name)}</span>`).join('')}</div><p class="wildlife-guide-note"><i class="wildlife-guide-marker mutant" aria-hidden="true"></i> 보라색 외곽선은 변이체입니다.</p></div>`}
 let objectiveAnnounceKey='';
 function updateObjectiveAnnouncements(t){const host=$('#mapObjectiveAnnounce');if(!host)return;const rows=majorObjectiveAnnouncements.filter(row=>row.tick<=t&&t-row.tick<objectiveAnnounceTicks),key=rows.map(row=>`${row.tick}:${row.teamNumber}:${row.characterName}:${row.objectiveName}`).join('|');if(key===objectiveAnnounceKey)return;objectiveAnnounceKey=key;host.hidden=!rows.length;host.innerHTML=rows.map(row=>`<div class="objective-announce-line">팀${row.teamNumber} ${esc(row.characterName)}<span class="arrow">→</span>${esc(row.objectiveName)}</div>`).join('')}
 function transportModeAt(t){let mode='hyperloop';for(const row of transportTransitions){if(row.tick>t)break;mode=row.mode}return mode}
 function worldObjectEndTick(row){return Number.isFinite(row.visibleEndTick)?row.visibleEndTick:row.destroyTick}
 function worldObjectVisible(row,t){const end=worldObjectEndTick(row);if(row.firstSeenTick>t||Number.isFinite(end)&&end<=t)return false;const mode=transportModeAt(t);if(row.category==='hyperloop'&&mode==='vls')return false;if(row.category==='vls'&&mode!=='vls')return false;return true}
 function worldEventPhase(row,t){if(t<row.warningTick||t>=row.endTick)return null;return t<row.activeTick?'warning':'active'}
 function markerAssetKey(kind,phase){const warning=`${kind}-warning`;return phase==='warning'&&data.mapMarkerAssets.icons[warning]?warning:kind}
 function drawMarkerAsset(g,key,x,y,size,warning=false,alpha=1){const row=data.mapMarkerAssets.icons[key],image=window.__mapMarkerImages?.[key];if(!row||!image?.complete||!image.naturalWidth)return false;g.save();g.globalAlpha=alpha;const crop=row.crop;if(crop){const sw=image.naturalWidth*crop.sizeByWidth,sx=image.naturalWidth*crop.x,sy=image.naturalWidth*crop.yByWidth;g.beginPath();g.arc(x,y,size/2,0,Math.PI*2);g.clip();g.drawImage(image,sx,sy,sw,sw,x-size/2,y-size/2,size,size)}else{const pixel=row.pixelSize||[image.naturalWidth,image.naturalHeight],anchor=row.anchorPixel||[pixel[0]/2,pixel[1]/2],scale=size/Math.max(pixel[0],pixel[1]),dw=pixel[0]*scale,dh=pixel[1]*scale;g.drawImage(image,x-anchor[0]*scale,y-anchor[1]*scale,dw,dh)}g.restore();return true}
 function drawDroneMarker(g,x,y,size,alpha=1){const radius=size*.4,halo=Math.max(2,size*.14),line=Math.max(1.6,size*.12);g.save();g.globalAlpha=alpha;g.lineCap='round';g.lineJoin='round';g.strokeStyle='rgba(3,6,12,.7)';g.lineWidth=line+halo;g.beginPath();g.arc(x,y,radius,0,Math.PI*2);g.stroke();g.strokeStyle='#63e8ff';g.lineWidth=line;g.beginPath();g.arc(x,y,radius,0,Math.PI*2);g.stroke();g.restore();return true}
 function drawWorldMarker(g,key,x,y,size,warning=false,alpha=1){if(key==='control-lens')return drawDroneMarker(g,x,y,size,alpha);return drawMarkerAsset(g,key,x,y,size,warning,alpha)}
 function lumiMovingAt(row,t){const now=worldMovementPositionAt(row,t),before=worldMovementPositionAt(row,Math.max(Number(row.firstSeenTick)||data.meta.firstTick,t-6));return Boolean(now&&before&&Math.hypot(now[0]-before[0],now[1]-before[1])>.03)}
 function drawLumiMovementTrail(g,row,t,projectPoint,toX,toY,insideMap,onScreen,sizeMultiplier){const track=row.movementTrack||[],from=Math.max(Number(row.firstSeenTick)||data.meta.firstTick,t-180),points=[],start=worldMovementPositionAt(row,from),current=worldMovementPositionAt(row,t);if(start)points.push(start);for(let i=lowerBound(track,from,row=>row[0]);i<track.length&&track[i][0]<=t;i++)points.push([track[i][1],track[i][2]]);if(current)points.push(current);if(points.length<2)return false;g.save();g.strokeStyle='rgba(99,232,255,.72)';g.lineWidth=1.45*Math.min(sizeMultiplier,1.6);g.lineCap='round';g.lineJoin='round';g.beginPath();let started=false;for(const raw of points){const pos=projectPoint(raw);if(!insideMap(pos))continue;const x=toX(pos[0]),y=toY(pos[1]);if(!onScreen(x,y))continue;started?(g.lineTo(x,y)):(g.moveTo(x,y),started=true)}if(started)g.stroke();g.restore();return started}
 let restrictionPathCache=null;
 function drawRestrictionAreas(g,w,h,t){const counts={reserved:0,temporary:0,restricted:0},update=restrictionAt(t),meta=data.worldMap.restrictionAreaShapes;if(!update?.areas?.length||!meta?.paths||typeof Path2D==='undefined')return counts;if(!restrictionPathCache){restrictionPathCache={};for(const [code,path] of Object.entries(meta.paths))restrictionPathCache[code]=new Path2D(path)}const sx=w/meta.viewBox[2],sy=h/meta.viewBox[3],unit=1/Math.max(.0001,Math.min(sx,sy));g.save();g.translate(mapView().ox,mapView().oy);g.scale(mapView().zoom*sx,mapView().zoom*sy);for(const area of update.areas){if(!['Reserved','Clearing','Restricted','ReservedClearing'].includes(area.stateName))continue;const path=restrictionPathCache[String(area.areaCode)];if(!path)continue;const pending=area.stateName==='Reserved',temporary=area.stateName==='Clearing'||area.stateName==='ReservedClearing';counts[pending?'reserved':temporary?'temporary':'restricted']++;g.fillStyle=pending?'rgba(245,174,66,.12)':temporary?'rgba(255,104,64,.22)':'rgba(226,42,54,.30)';g.strokeStyle=pending?'rgba(255,199,95,.82)':temporary?'rgba(255,119,74,.92)':'rgba(255,55,72,.96)';g.lineWidth=(temporary?2.1:1.5)*unit;g.setLineDash(pending?[7*unit,5*unit]:temporary?[3*unit,3*unit]:[]);g.fill(path);g.stroke(path)}g.setLineDash([]);g.restore();return counts}
 function drawMap(){
  const c=$('#mapCanvas');if(!c)return;
  activeMapSpace=mapSpaceAt(state.cursor);if(mapLayout&&(mapLayout.d!==Math.min(2,window.devicePixelRatio||1)||mapLayout.spaceId!==activeMapSpace.spaceId)){if(mapLayout.spaceId!==activeMapSpace.spaceId&&state.cameraLock!=='focus')state.mapView={zoom:1,ox:0,oy:0};mapLayout=null}
  const layout=mapLayout||layoutMap();if(!layout)return;
  if(state.cameraLock==='focus')centerOnSelectedPlayer(layout);
  clampMapPan(layout);
  const started=performance.now(),w=layout.w,h=layout.h,d=layout.d,v=mapView(),ms=markerScale();
  const g=c.getContext('2d'),background=ensureMapBackground(layout,activeMapSpace);g.setTransform(d,0,0,d,0,0);g.clearRect(0,0,w,h);if(background)g.drawImage(background,0,0,background.width,background.height,v.ox,v.oy,w*v.zoom,h*v.zoom);
  const mapImage=activeMapSpace.image||data.map.image,X=x=>v.ox+(x/mapImage.w*w)*v.zoom,Y=y=>v.oy+(y/mapImage.h*h)*v.zoom,on=p=>p&&0<=p[0]&&p[0]<=mapImage.w&&0<=p[1]&&p[1]<=mapImage.h,onScreen=(x,y)=>x>-72&&x<w+72&&y>-72&&y<h+72;
 const restrictionPaint=activeMapSpace.spaceId==='lumia'?drawRestrictionAreas(g,w,h,state.cursor):{reserved:0,temporary:0,restricted:0};
 const s=selected(),track=s.movementTrack||[],trailFrom=lowerBound(track,state.cursor-900,row=>row[0]);
 g.fillStyle=teamColor(s.teamNumber);g.globalAlpha=.5;
 for(let i=trailFrom;i<track.length&&track[i][0]<=state.cursor;i++){const q=project([track[i][1],track[i][2]]);if(!on(q))continue;g.beginPath();g.arc(X(q[0]),Y(q[1]),1.5*Math.sqrt(v.zoom),0,Math.PI*2);g.fill()}
 g.globalAlpha=1;
 const cmds=s.plannedPathCommands||[],ci=lowerBound(cmds,state.cursor+1,row=>row[0])-1,path=ci>=0&&state.cursor-cmds[ci][0]<=600?cmds[ci]:null;
 if(path){g.strokeStyle=teamColor(s.teamNumber);g.setLineDash([6,5]);g.lineWidth=2*Math.sqrt(v.zoom);g.beginPath();let started2=false;for(const pos of path[2].map(project)){if(!on(pos))continue;started2?(g.lineTo(X(pos[0]),Y(pos[1]))):(g.moveTo(X(pos[0]),Y(pos[1])),started2=true)}if(started2)g.stroke();g.setLineDash([])}
 c.__worldMarkers=[];c.__lumiMarkers=[];const normalWorldMarkers=[],bossWorldMarkers=[];
 if(activeMapSpace.spaceId==='lumia'){
   for(const row of worldStatic){if(!worldObjectVisible(row,state.cursor))continue;if(row.category==='surveillance-camera'&&(!state.layers.cameras||row.ownerPublicPlayerId!==state.playerId))continue;if(row.category==='control-lens'&&(!state.layers.controlLens||row.ownerPublicPlayerId!=null&&row.ownerPublicPlayerId!==state.playerId))continue;const isLumi=row.category==='lumi',raw=isLumi?(worldMovementPositionAt(row,state.cursor)||row.position):row.position,pos=project(raw);if(!on(pos))continue;const x=X(pos[0]),y=Y(pos[1]);if(!onScreen(x,y))continue;const size=(row.category==='control-lens'||row.category==='surveillance-camera'?18:isLumi?20:15)*ms,moving=isLumi&&lumiMovingAt(row,state.cursor);if(isLumi)drawLumiMovementTrail(g,row,state.cursor,project,X,Y,on,onScreen,ms);normalWorldMarkers.push({row,phase:'active',x,y,size,key:row.assetKey||row.category,alpha:1,moving})}
   for(const row of worldEvents){const phase=worldEventPhase(row,state.cursor);if(!phase)continue;const pos=project(row.position);if(!on(pos))continue;const x=X(pos[0]),y=Y(pos[1]);if(!onScreen(x,y))continue;const boss=['wickeline','alpha','omega'].includes(row.kind),size=(boss?(phase==='warning'?24:row.kind==='wickeline'?21:16):18)*ms,key=markerAssetKey(row.kind,phase),alpha=phase==='warning'?0.9:1;if(boss){bossWorldMarkers.push({row,phase,x,y,size,key,alpha});continue}normalWorldMarkers.push({row,phase,x,y,size,key,alpha})}
 }
  let aliveWildlife=0,deadWildlife=0,unmappedAlive=0;c.__wildlifeMarkers=[];c.__droneMarkers=[];
  if(state.layers.wildlife){const markerSet=wildlifeMarkersAt(state.cursor);unmappedAlive=markerSet.unmappedInstances;for(const marker of markerSet.visible){const animal=marker.animal,pos=project(marker.pos);if(!on(pos))continue;const baseX=X(pos[0]),baseY=Y(pos[1]),size=(animal.mapMarkerStyle==='neutral-monster-red-dot'?7:animal.mapMarkerStyle==='special-icon'?18:w<520?12:14)*ms,offset=wildlifeScreenOffset(marker.offsetIndex,marker.offsetCount,size),x=baseX+offset[0],y=baseY+offset[1];if(!onScreen(x,y))continue;const isDrone=animal.mapMarkerStyle==='neutral-monster-red-dot',mobile=animal.mapMovementMode.startsWith('crow-')||animal.mapMovementMode.startsWith('special-mobile-'),derived=!mobile&&animal.mapMovementMode!=='fixed-scratch-group-center',alpha=derived?.82:1;if(!drawWildlifeMarker(g,animal,x,y,size,alpha,state.cursor))continue;if(isDrone)c.__droneMarkers.push({publicWildlifeId:animal.publicWildlifeId,publicWildlifeGroupId:animal.publicWildlifeGroupId,x,y,movementMode:animal.mapMovementMode});else{aliveWildlife++;c.__wildlifeMarkers.push({publicWildlifeId:animal.publicWildlifeId,publicWildlifeGroupId:animal.publicWildlifeGroupId,x,y,movementMode:animal.mapMovementMode,style:animal.mapMarkerStyle,assetKey:wildlifeAssetKeyAt(animal,state.cursor)})}}}
 c.__markers=[];
  for(const p of data.players){const life=lifeAt(p,state.cursor);if(life==='dead')continue;const raw=positionAt(p,state.cursor),pos=project(raw);if(!on(pos))continue;const x=X(pos[0]),y=Y(pos[1]);if(!onScreen(x,y))continue;const chosen=p.publicPlayerId===state.playerId,size=(w<520?20:27)*ms,icon=window.__characterImages?.[p.characterCode];g.save();g.beginPath();g.arc(x,y,size/2,0,Math.PI*2);g.clip();if(icon?.complete&&icon.naturalWidth)g.drawImage(icon,x-size/2,y-size/2,size,size);else{g.fillStyle=teamColor(p.teamNumber);g.fillRect(x-size/2,y-size/2,size,size)}g.restore();g.strokeStyle=teamColor(p.teamNumber);g.lineWidth=1.25*Math.min(ms,1.6);g.beginPath();g.arc(x,y,size/2+.5,0,Math.PI*2);g.stroke();if(chosen){g.strokeStyle='#fff';g.lineWidth=.8*Math.min(ms,1.6);g.beginPath();g.arc(x,y,size/2+2.5,0,Math.PI*2);g.stroke()}g.fillStyle='#fff';g.font=`${chosen?'800':'700'} ${Math.round((chosen?11:10)*Math.min(ms,1.85))}px system-ui`;g.textAlign='center';g.fillText(`팀${p.teamNumber} · ${p.characterName}`,x,Math.max(12,y-size/2-5));c.__markers.push({publicPlayerId:p.publicPlayerId,x,y})}
  for(const marker of normalWorldMarkers){const {row,phase,x,y,size,key,alpha,moving}=marker;if(row.category==='lumi'&&moving){const pulse=size*.62+(state.cursor%60)/60*2;g.save();g.strokeStyle='rgba(99,232,255,.48)';g.lineWidth=1.25*Math.min(ms,1.6);g.beginPath();g.arc(x,y,pulse,0,Math.PI*2);g.stroke();g.restore()}if(drawWorldMarker(g,key,x,y,size,phase==='warning',alpha)){c.__worldMarkers.push({kind:row.kind||row.category,x,y,phase,ownerPublicPlayerId:row.ownerPublicPlayerId});if(row.category==='lumi'){g.save();g.fillStyle='#b9f6ff';g.font=`750 ${Math.round(10*Math.min(ms,1.6))}px system-ui`;g.textAlign='center';g.fillText('루미',x,Math.max(11,y-size/2-4));g.restore();c.__lumiMarkers.push({x,y,moving:Boolean(moving),movementAnchorCount:Number(row.movementAnchorCount)||0})}}}
  for(const marker of bossWorldMarkers){const {row,phase,x,y,size,key,alpha}=marker;if(drawWorldMarker(g,key,x,y,size,phase==='warning',alpha))c.__worldMarkers.push({kind:row.kind,x,y,phase})}
 c.__deathMarkers=[];for(const row of s.deathLocations||[]){if(row.tick>state.cursor)break;const pos=project(row.position);if(!on(pos))continue;const x=X(pos[0]),y=Y(pos[1]);if(!onScreen(x,y))continue;const mark=3*ms;g.save();g.strokeStyle='#ff3d4f';g.lineWidth=1.5*Math.min(ms,1.6);g.lineCap='round';g.beginPath();g.moveTo(x-mark,y-mark);g.lineTo(x+mark,y+mark);g.moveTo(x+mark,y-mark);g.lineTo(x-mark,y+mark);g.stroke();g.restore();c.__deathMarkers.push({tick:row.tick,x,y})}
 const pings=data.tacticalPings.items,pingSpan=data.meta.targetFrameRate*data.tacticalPings.displaySeconds,pingFrom=lowerBound(pings,state.cursor-pingSpan,row=>row.tick);
 c.__pingMarkers=[];c.__movementPingMarkers=[];
 if(state.layers.pings){
  for(let i=pingFrom;i<pings.length&&pings[i].tick<=state.cursor;i++){const ping=pings[i],pos=project(ping.position);if(!on(pos)||!ping.assetKey)continue;const x=X(pos[0]),y=Y(pos[1]);if(!onScreen(x,y))continue;const age=(state.cursor-ping.tick)/pingSpan;if(drawMarkerAsset(g,ping.assetKey,x,y,(17+age)*ms,false,Math.max(.42,1-age*.85)))c.__pingMarkers.push({publicPingId:ping.publicPingId,x,y,type:ping.type,assetKey:ping.assetKey})}
  const moveSpan=data.meta.targetFrameRate*data.worldMap.movementPingDisplaySeconds,moveFrom=lowerBound(movementPings,state.cursor-moveSpan,row=>row.tick);for(let i=moveFrom;i<movementPings.length&&movementPings[i].tick<=state.cursor;i++){const ping=movementPings[i];if(ping.mapPositionStatus)continue;const pos=project(ping.position);if(!on(pos))continue;const x=X(pos[0]),y=Y(pos[1]);if(!onScreen(x,y))continue;const age=(state.cursor-ping.tick)/moveSpan;if(drawMarkerAsset(g,ping.assetKey,x,y,12*ms,false,Math.max(.35,1-age*.8)))c.__movementPingMarkers.push({publicMovementPingId:ping.publicMovementPingId,x,y,kind:ping.kind,assetKey:ping.assetKey})}
 }
  c.dataset.mapZoom=v.zoom.toFixed(2);c.dataset.mapSpaceId=activeMapSpace.spaceId;c.dataset.cameraLock=state.cameraLock||'free';c.dataset.markerCount=String(c.__markers.length);c.dataset.deathMarkerCount=String(c.__deathMarkers.length);c.dataset.worldMarkerCount=String(c.__worldMarkers.length);c.dataset.worldMarkerKinds=c.__worldMarkers.map(row=>`${row.kind}:${row.phase}`).join(',');c.dataset.lumiMarkerCount=String(c.__lumiMarkers.length);c.dataset.lumiMarkerPositions=c.__lumiMarkers.map(row=>`${row.x.toFixed(2)},${row.y.toFixed(2)},${row.moving?'moving':'stopped'}`).join(';');c.dataset.restrictionReserved=String(restrictionPaint.reserved);c.dataset.restrictionTemporary=String(restrictionPaint.temporary);c.dataset.restrictionRestricted=String(restrictionPaint.restricted);c.dataset.wildlifeMarkerCount=String(c.__wildlifeMarkers.length);c.dataset.droneMarkerCount=String(c.__droneMarkers.length);c.dataset.pingMarkerCount=String(c.__pingMarkers.length);c.dataset.movementPingMarkerCount=String(c.__movementPingMarkers.length);c.dataset.wildlifeAlive=String(aliveWildlife);c.dataset.wildlifeDead=String(deadWildlife);c.dataset.wildlifeUnmapped=String(unmappedAlive);
  updateVisionGap();
  const place=activeMapSpace.spaceId==='lumia'?'':` · ${activeMapSpace.label}`;g.textAlign='left';g.font='650 11.5px system-ui';g.fillStyle='rgba(9,11,18,.75)';g.fillText(`${clock(state.cursor)} · 팀${s.teamNumber} · ${s.characterName}${place}`,11,19);g.fillStyle='#fff';g.fillText(`${clock(state.cursor)} · 팀${s.teamNumber} · ${s.characterName}${place}`,10,18);
 const cost=performance.now()-started;paintStats.n++;paintStats.sum+=cost;paintStats.max=Math.max(paintStats.max,cost);
  c.dataset.lastDrawMs=cost.toFixed(2);c.dataset.avgDrawMs=(paintStats.sum/paintStats.n).toFixed(2);c.dataset.maxDrawMs=paintStats.max.toFixed(2);c.dataset.paintSamples=String(paintStats.n);
 updateObjectiveAnnouncements(state.cursor);
 maybeUpdateTeamRail();
 markActiveDeath();
}
 let playbackFrame=0,playbackLast=0,playbackPaintLast=0;
function syncTransport(){const slider=$('#time'),label=$('#timeLabel');if(slider)slider.value=state.cursor;if(label)label.textContent=clock(state.cursor)}
function syncPhaseNote(){const note=$('#phaseSummary');if(!note)return;const phase=phaseAt(state.cursor),text=`${phaseName(phase)} · 변화까지 ${remainClock(phaseRemain(phase,state.cursor))}`;if(note.textContent!==text)note.textContent=text}
function updateVisionGap(){const host=$('#visionGap');if(!host)return;const v=(data.visionObjects?.players||[]).find(row=>row.publicPlayerId===state.playerId),cameraExpected=Number(v?.physicalCameraCount||0),cameraMapped=Number(v?.physicalCameraPositionedCount||0),droneExpected=Number(v?.droneUseCounter||0),droneMapped=Number(v?.dronePositionedCount||0),key=`${state.playerId}|${state.layers.cameras?'1':'0'}|${state.layers.controlLens?'1':'0'}|${cameraExpected}|${cameraMapped}|${droneExpected}|${droneMapped}`;if(key===visionGapKey)return;visionGapKey=key;const parts=[];if(state.layers.cameras&&cameraExpected>cameraMapped)parts.push(`감시 카메라 ${fmt(cameraExpected)}개 중 지도 좌표 ${fmt(cameraMapped)}개만 확인됐어요.`);if(state.layers.controlLens&&droneExpected>droneMapped)parts.push(`정찰·EMP 드론 ${fmt(droneExpected)}회 중 지도 좌표 ${fmt(droneMapped)}개만 확인됐어요.`);host.hidden=!parts.length;host.textContent=parts.join(' ')}
 function advancePlayback(now){if(!state.playing){playbackFrame=0;return}if(!playbackLast)playbackLast=now;const elapsed=Math.min(120,Math.max(0,now-playbackLast));playbackLast=now;state.cursor+=elapsed/1000*data.meta.targetFrameRate*state.speed;if(state.cursor>data.meta.lastTick)state.cursor=data.meta.firstTick;if(!playbackPaintLast||now-playbackPaintLast>=1000/60-1){playbackPaintLast=now;syncTransport();drawMap();syncPhaseNote()}playbackFrame=requestAnimationFrame(advancePlayback)}
 function setPlaying(on){const button=$('#play');state.playing=Boolean(on);if(playbackFrame){cancelAnimationFrame(playbackFrame);playbackFrame=0}playbackLast=0;playbackPaintLast=0;if(button){button.textContent=state.playing?'정지':'재생';button.setAttribute('aria-pressed',String(state.playing))}if(state.playing){paintStats={n:0,sum:0,max:0};railStats={n:0,sum:0,max:0};playbackFrame=requestAnimationFrame(advancePlayback)}}
 function stop(){if(playbackFrame)cancelAnimationFrame(playbackFrame);playbackFrame=0;playbackLast=0;playbackPaintLast=0;state.playing=false}
function renderMap(){stop();if(data.map.playbackStatus==='unavailable-mode-specific-map-and-bounds'){$('#content').innerHTML=`<section class="panel"><h2>${esc(data.meta.matchModeLabel)} 지도</h2><div class="notice">이 모드에서 사용할 수 있는 지도와 좌표 기준을 아직 확인하지 못했습니다. 결과·피해·스킬 지표는 계속 볼 수 있습니다.</div></section>`;return}
 mapLayout=null;
 teamCombatLogKey='';
 visionGapKey='';
 objectiveAnnounceKey='';
 $('#content').innerHTML=`<div class="stage"><section class="panel map-panel"><div class="map-shell"><canvas id="mapCanvas" aria-label="이동 지도 재생"></canvas><aside id="mapObjectiveAnnounce" class="map-objective-announce" hidden aria-live="polite"></aside><aside id="mapTeamCombatLog" class="map-team-log" hidden aria-live="polite" aria-label="선택 팀 전투 로그"></aside><details id="wildlifeGuide" class="map-wildlife-guide" ${state.wildlifeGuideOpen?'open':''}><summary>동물 표식</summary>${wildlifeGuideHtml()}</details><div class="map-zoom" role="group" aria-label="지도 확대"><button type="button" id="mapZoomIn" aria-label="확대">+</button><button type="button" id="mapZoomOut" aria-label="축소">−</button><button type="button" id="mapZoomReset" class="reset" aria-label="지도 맞춤">맞춤</button></div></div><div id="mapControls"><div class="transport"><button type="button" class="btn play" id="play" aria-pressed="false">재생</button><input id="time" type="range" min="${data.meta.firstTick}" max="${data.meta.lastTick}" step="1" value="${state.cursor}" aria-label="재생 시간"><b id="timeLabel" class="clock">${clock(state.cursor)}</b></div><div class="transport2"><div class="seg" role="group" aria-label="재생 속도">${[.5,1,2,4,8].map(v=>`<button type="button" class="speed ${state.speed===v?'active':''}" data-speed="${v}" aria-pressed="${state.speed===v}">${v}×</button>`).join('')}</div><label class="phase-field">페이즈 이동 <select id="phaseJump"><option value="">선택</option>${phaseChanges.map(row=>`<option value="${row.tick}">${clock(row.tick)} · ${phaseName(row)}</option>`).join('')}</select></label><span id="phaseSummary" class="phase-note"></span></div><div class="map-filters" role="group" aria-label="지도 표시"><label class="map-toggle"><input type="checkbox" data-layer="wildlife" ${state.layers.wildlife?'checked':''}>야생동물</label><label class="map-toggle"><input type="checkbox" data-layer="pings" ${state.layers.pings?'checked':''}>핑</label><label class="map-toggle"><input type="checkbox" data-layer="cameras" ${state.layers.cameras?'checked':''}>감시카메라</label><label class="map-toggle"><input type="checkbox" data-layer="controlLens" ${state.layers.controlLens?'checked':''}>드론</label><span id="lumiPresence" class="map-presence ${lumiRows.length?'available':'empty'}">${lumiRows.length?'루미 이동 추적':'루미 기록 없음'}</span></div><p id="visionGap" class="phase-note" hidden></p></div>${mapDeathReviewHtml()}</section><aside class="panel rail"><h2 class="rail-title">우리 팀 현황</h2><div id="teamLoadout" class="cards"></div></aside></div>`;
  window.__mapImage=new Image();window.__mapImage.onload=()=>{mapLayout=null;mapBackground=null;drawMap()};window.__mapImage.src=data.map.imageDataUrl;
  window.__wildlifeBySpawn=[...data.wildlife.instances].sort((a,b)=>a.spawnTick-b.spawnTick);wildlifeSweep=null;
 window.__characterImages={};
 for(const [key,row] of Object.entries(data.characterAssets.icons)){const image=new Image();image.onload=()=>{if(!state.playing)drawMap()};image.src=row.imageDataUrl;window.__characterImages[key]=image}
 window.__mapMarkerImages={};
 for(const space of secondarySpaces){if(!space.backgroundDataUrl)continue;const image=new Image();image.onload=()=>{if(!state.playing)drawMap()};image.src=space.backgroundDataUrl;window.__mapMarkerImages[space.backgroundAssetKey]=image}
 for(const [key,row] of Object.entries(data.mapMarkerAssets.icons)){const image=new Image();image.onload=()=>{if(!state.playing)drawMap()};image.src=row.imageDataUrl;window.__mapMarkerImages[key]=image}
 $('#time').oninput=e=>{state.cursor=Number(e.target.value);$('#timeLabel').textContent=clock(state.cursor);drawMap();syncPhaseNote()};
 $('#phaseJump').onchange=e=>{if(e.target.value){state.cursor=Number(e.target.value);syncTransport();drawMap();syncPhaseNote()}};
 document.querySelectorAll('[data-speed]').forEach(b=>b.onclick=()=>{state.speed=Number(b.dataset.speed);document.querySelectorAll('[data-speed]').forEach(x=>{const active=Number(x.dataset.speed)===state.speed;x.classList.toggle('active',active);x.setAttribute('aria-pressed',String(active))})});
 document.querySelectorAll('[data-layer]').forEach(input=>input.onchange=()=>{state.layers[input.dataset.layer]=input.checked;drawMap()});
 const wildlifeGuide=$('#wildlifeGuide');if(wildlifeGuide)wildlifeGuide.ontoggle=()=>{state.wildlifeGuideOpen=wildlifeGuide.open};
 $('#play').onclick=()=>setPlaying(!state.playing);
 bindMapNavigation();
 bindMapDeaths();
 drawMap();syncPhaseNote()
}
function bindMapNavigation(){
 const c=$('#mapCanvas'),shell=c&&c.parentElement;if(!c||!shell)return;
 let drag=null;
 c.addEventListener('wheel',e=>{e.preventDefault();freeCamera();const layout=mapLayout||layoutMap();if(!layout)return;const rect=c.getBoundingClientRect(),factor=e.deltaY<0?1.18:1/1.18;zoomMapAt(mapView().zoom*factor,e.clientX-rect.left,e.clientY-rect.top);drawMap()},{passive:false});
 c.addEventListener('pointerdown',e=>{if(e.button!==0||mapView().zoom<=1)return;drag={id:e.pointerId,x:e.clientX,y:e.clientY,ox:mapView().ox,oy:mapView().oy};c.setPointerCapture(e.pointerId);shell.classList.add('is-dragging')});
 c.addEventListener('pointermove',e=>{if(!drag||e.pointerId!==drag.id)return;if(e.clientX!==drag.x||e.clientY!==drag.y)freeCamera();mapView().ox=drag.ox+(e.clientX-drag.x);mapView().oy=drag.oy+(e.clientY-drag.y);clampMapPan(mapLayout||layoutMap());drawMap()});
 const endDrag=e=>{if(!drag||e.pointerId!==drag.id)return;drag=null;shell.classList.remove('is-dragging')};
 c.addEventListener('pointerup',endDrag);c.addEventListener('pointercancel',endDrag);
 const centerZoom=next=>{const layout=mapLayout||layoutMap();if(!layout)return;freeCamera();zoomMapAt(next,layout.w/2,layout.h/2);drawMap()};
 $('#mapZoomIn').onclick=()=>centerZoom(mapView().zoom*1.25);
 $('#mapZoomOut').onclick=()=>centerZoom(mapView().zoom/1.25);
 $('#mapZoomReset').onclick=()=>{freeCamera();state.mapView={zoom:1,ox:0,oy:0};drawMap()}
}
window.addEventListener('resize',()=>{if(resizeQueued)return;resizeQueued=true;requestAnimationFrame(()=>{resizeQueued=false;mapLayout=null;if(!state.playing)drawMap()})});
function renderPings(){
 const rows=data.tacticalPings.items,moves=data.worldMap.movementPings;
 const span=Math.max(data.tacticalPings.displaySeconds||0,data.worldMap.movementPingDisplaySeconds||0);
 const pingIcon=p=>p.assetKey?`<img src="${data.mapMarkerAssets.icons[p.assetKey].imageDataUrl}" alt="">`:'';
 $('#content').innerHTML=`<section class="panel"><h2>전체 핑 기록</h2><div class="notice"><b>선택 플레이어 기준이 아닙니다.</b> 사람이 찍은 핑은 리플레이의 exact 종류와 같은 클라이언트 지도 아이콘으로 구분합니다. 하이퍼루프 도착과 VLS 착지는 사람이 찍은 핑과 섞지 않고 별도의 이동 알림으로 표시합니다.</div><div class="grid" style="margin-top:12px"><article class="stat"><span>사람이 찍은 핑</span><b>${fmt(rows.length)}</b></article><article class="stat"><span>이동 알림</span><b>${fmt(moves.length)}</b></article><article class="stat"><span>목록 제외 계열</span><b>${fmt(data.tacticalPings.excludedHyperloopPingCount)}</b></article><article class="stat"><span>표시 시간</span><b>${span}초</b></article></div></section><section class="panel"><h2>사람이 찍은 핑</h2><div class="table-wrap"><table><thead><tr><th>시간</th><th>핑</th><th>발신자</th></tr></thead><tbody>${rows.map(p=>`<tr><td><button type="button" class="btn jump" data-jump="${p.tick}">${clock(p.tick)}</button></td><td><span class="ping-label">${pingIcon(p)}${esc(p.label)}</span></td><td>${esc(data.players.find(x=>x.publicPlayerId===p.senderPublicPlayerId)?.publicLabel||'익명')}</td></tr>`).join('')}</tbody></table></div></section><section class="panel"><h2>자동 이동 알림</h2><div class="table-wrap"><table><thead><tr><th>시간</th><th>종류</th><th>발신</th></tr></thead><tbody>${moves.map(p=>`<tr><td><button type="button" class="btn jump" data-jump="${p.tick}">${clock(p.tick)}</button></td><td><span class="ping-label">${pingIcon(p)}${esc(p.label)}</span></td><td>시스템</td></tr>`).join('')}</tbody></table></div></section>`;
 document.querySelectorAll('[data-jump]').forEach(b=>b.onclick=()=>{state.cursor=Number(b.dataset.jump);state.view='map';render()})
}
const humanStyle=value=>({
 '혼자서도 문을 여는 선봉형':'팀원보다 먼저 싸움을 시작한 플레이',
 '팀 교전의 문을 여는 선봉형':'팀 싸움을 먼저 연 플레이',
 '호출에 반응하는 합류형':'팀원이 싸우면 뒤따라 합류한 플레이',
 '남들보다 먼저 차려입는 장비 완성형':'장비부터 빠르게 갖춘 플레이',
 '레벨 곡선을 앞당긴 선성장형':'레벨을 빠르게 끌어올린 플레이',
 '숙련도를 끝까지 쌓는 누적형':'끝까지 무기 숙련도를 쌓은 플레이',
 '교전 속에서 따라붙는 성장형':'싸우면서 성장을 따라잡은 플레이',
 '등장 전에 자리를 잡는 선점형':'오브젝트 출현 전에 주변을 먼저 확인한 플레이',
 '발생 뒤에도 놓치지 않는 추적형':'나온 뒤라도 오브젝트를 확인하러 간 플레이',
 '필요한 오브젝트를 골라 가는 선택형':'20m 안까지 간 오브젝트가 적었던 플레이',
 '손이 쉬지 않는 연계형':'스킬을 쉼 없이 이어 쓴 플레이',
 '평타를 사이에 엮는 혼합형':'스킬 사이에 기본 공격을 자주 섞은 플레이',
 '자주 쓰는 순서를 다듬은 루틴형':'필요한 순간마다 스킬을 활용한 플레이',
 '필요할 때 한 장씩 꺼내는 선택형':'스킬을 몰아 쓰기보다 나눠 쓴 플레이',
 '필요한 순간마다 스킬을 꺼내는 활용형':'필요한 순간마다 스킬을 활용한 플레이',
 '전투 중 스킬 사용 기록이 적은 관망형':'전투 중 스킬 사용 기록이 적었던 플레이'
}[value]||value);
function renderCombatJudgment(){
 const p=selected(),j=p.combatJudgment,rows=j.episodes||[];
 const ratio=v=>v===null||v===undefined?'—':pct(v),seconds=v=>v===null||v===undefined?'—':`${Number(v).toFixed(1)}초`;
 const roleName=v=>v==='initiator'?'먼저 들어감':v==='co-initiator'?'팀원과 함께 들어감':'나중에 합류';
 const summary=rows.length
  ?`팀에서 싸움이 ${fmt(j.teamEpisodeCount)}번 벌어졌고, 그중 ${fmt(j.participatedEpisodeCount)}번 함께했어요. ${fmt(j.initiationCount)}번은 누구보다 먼저 들어갔고, 그중 ${fmt(j.soloInitiationCount)}번은 팀원이 뒤에서 따라왔어요.`
  :'리플레이 기록에서 플레이어끼리 싸운 장면을 확인하지 못했어요.';
 const targetStory=j.targetSwitchesPer10Actions===null
  ?'공격 대상을 얼마나 자주 바꿨는지는 기록이 부족해 계산하지 못했어요.'
  :`적을 직접 지정한 공격 10번마다 대상을 평균 ${Number(j.targetSwitchesPer10Actions).toFixed(1)}번 바꿨어요.`;
 const focusStory=j.teamFocusFollowupRate===null
  ?'팀원과 같은 적을 함께 노린 정도는 기록이 부족해 계산하지 못했어요.'
  :`내 공격 앞뒤 ${data.combatJudgment.teamFocusWindowSeconds}초 안에 팀원도 같은 적을 노린 비율은 ${ratio(j.teamFocusFollowupRate)}였어요.`;
 const body=rows.length?rows.map(row=>{
  const timing=row.entryRole==='joiner'?`팀보다 ${seconds(row.joinDelaySeconds)} 늦게`:row.entryRole==='co-initiator'?'팀과 거의 동시에':'가장 먼저';
  const support=row.entryRole==='joiner'?'해당 없음':row.entryRole==='co-initiator'?'거의 동시에':seconds(row.reinforcementDelaySeconds);
  const target=row.primaryTargetCharacterName?`${esc(row.primaryTargetCharacterName)} · ${fmt(row.primaryTargetActionCount)}번`:'직접 노린 적 기록 없음';
  return `<tr><td><button type="button" class="btn jump" data-judgment-jump="${row.entryTick}">${clock(row.entryTick)}</button></td><td>${roleName(row.entryRole)}</td><td>${timing}</td><td>${support}</td><td>${target}</td><td class="num">${fmt(row.targetActionCount)}</td><td class="num">${fmt(row.targetSwitchCount)}</td><td class="num">${fmt(row.teamFocusFollowupCount)}</td><td>${row.survived?'<span class="badge ok">살아 나옴</span>':'<span class="badge">사망</span>'}</td></tr>`
 }).join(''):'<tr><td colspan="9">표시할 교전 근거가 없습니다.</td></tr>';
 $('#content').innerHTML=`<section class="panel"><h2>${p.publicLabel} · 싸움 판단</h2><div class="human-lead"><div><strong>${esc(humanStyle(j.styleLabel))}</strong><p>${summary}</p></div><span class="human-kicker">이 경기에서 보인 모습</span></div><div class="human-grid"><article class="human-fact"><span>팀 싸움에 함께한 횟수</span><b>${fmt(j.participatedEpisodeCount)} / ${fmt(j.teamEpisodeCount)}번</b></article><article class="human-fact"><span>먼저 또는 거의 같이 들어간 싸움</span><b>${fmt(j.initiationCount)}번</b><small>참여한 싸움의 ${ratio(j.initiationRate)}</small></article><article class="human-fact"><span>혼자 먼저 들어갔을 때 팀원이 온 시간</span><b>${seconds(j.meanReinforcementDelaySeconds)}</b></article><article class="human-fact"><span>살아서 나온 싸움</span><b>${fmt(j.survivedEpisodeCount)} / ${fmt(j.participatedEpisodeCount)}번</b></article></div><div class="human-story"><b>공격 대상을 고른 방식</b><br>${targetStory} ${focusStory}</div><details class="analysis-details"><summary>싸움 ${fmt(rows.length)}번을 하나씩 보기</summary><div class="detail-body"><div class="table-wrap"><table><thead><tr><th>싸움 시작</th><th>참여 방식</th><th>팀과의 시간 차</th><th>팀원이 온 시간</th><th>가장 많이 공격한 적</th><th class="num">적을 지정한 공격</th><th class="num">대상 변경</th><th class="num">팀과 같은 적을 공격</th><th>결과</th></tr></thead><tbody>${body}</tbody></table></div></div></details><details class="analysis-details"><summary>이 숫자는 어떻게 셌나요?</summary><div class="detail-body"><p class="judgment-note">리플레이에서 팀이 플레이어와 싸운 상태가 이어지고, 실제로 적 플레이어를 대상으로 삼은 기록이 함께 있을 때만 한 번의 싸움으로 셌어요. 팀 최초 진입 뒤 ${data.combatJudgment.simultaneousEntryWindowSeconds}초 이내에 전투 상태가 시작되면 ‘팀과 거의 동시에’로 봅니다. 원래 진입 tick은 바꾸지 않아요. 공격 대상을 고른 횟수는 스킬이나 기본 공격을 시작할 때 직접 지정한 적 기준이며 적중률이나 피해량을 뜻하지 않습니다. 이 경기 안에서 같은 팀끼리만 비교했으며 티어·등급·전체 이용자 순위는 아닙니다.</p></div></details></section>`;
 document.querySelectorAll('[data-judgment-jump]').forEach(b=>b.onclick=()=>{state.cursor=Number(b.dataset.judgmentJump);state.view='map';render()})
}
const familyKo=value=>({Active1:'Q',Active2:'W',Active3:'E',Active4:'R',Passive:'패시브',WeaponSkill:'무기 스킬',TacticalSkill:'전술 스킬',Other:'기타'}[value]||value||'기록 없음');
const projectileShapeKo=value=>({single:'단일탄',multi:'다발탄',penetrating:'관통',returning:'왕복',explosive:'폭발',installation:'설치형'}[value]||value);
function projectileReasonKo(value){const text=String(value||'');if(text.includes('no verified single projectile spawn during confirmed')||text.includes('no verified multi-projectile spawn during confirmed'))return '이번 대인 교전에서는 이 스킬을 발사한 기록이 없어요.';if(text.includes('target-selected skill'))return '대상을 지정하면 따라가는 스킬이라 조준 적중률로 보지 않아요.';if(text.includes('instant skill')||text.includes('no verified direction/point'))return '방향이나 지점을 조준하는 스킬이 아니라 적중률 대상에서 뺐어요.';if(text.includes('coverage is incomplete'))return '모든 사용의 스킬 동작을 확인하지 못해 빗나감으로 세지 않았어요.';if(text.includes('delayed, unlinked, or ambiguous'))return '지속·지연 피해가 섞여 한 번의 시전 결과로 나눌 수 없어요.';if(text.includes('unresolved enemy damage code'))return '같은 순간 출처를 확정하지 못한 피해가 있어 계산에서 뺐어요.';if(text.includes('no exact enemy damage code'))return '이 경기에는 같은 스킬로 확인된 대인 피해 표본이 없어요.';if(text.includes('exclusive one-to-one'))return '시전과 투사체 생성이 일정한 수로 정확히 이어지지 않았어요.';if(text.includes('minimum')||text.includes('not been verified'))return '같은 연결을 확정할 반복 기록이 부족해요.';if(text.includes('penetrating'))return '한 발이 여러 적을 맞힐 수 있는 관통형이에요.';if(text.includes('explosive'))return '폭발 범위에 맞은 대상을 별도로 연결해야 해요.';if(text.includes('installation')||text.includes('persistent'))return '설치 뒤 유지되는 동안의 적중 기준이 달라요.';if(text.includes('returning'))return '나갈 때와 돌아올 때를 나눠 세어야 해요.';if(text.includes('no declared')||text.includes('no verified')||text.includes('no exact'))return '이 스킬의 사용과 적중을 잇는 확정 근거가 없어요.';return text||'정확한 계산 기준을 아직 확정하지 못했어요.'}
const hitRateUnitKo=row=>row.hitRateUnit==='skill-cast'?'회':'발';
function projectileAccuracyLead(p){const rows=p.projectileHitRates||[],exact=rows.filter(row=>row.hitRateCalculable);if(exact.length)return `확인된 대인 교전에서 `+exact.map(row=>`${familyKo(row.family)} ${pct(row.playerHitRate)} (${fmt(row.playerHitAttemptCount)}/${fmt(row.attemptCount)}${hitRateUnitKo(row)}${row.projectilesPerCast>1?` · 시전당 ${fmt(row.projectilesPerCast)}발`:''})`).join(' · ')+`로 확인됐어요. 발사체 적중률과 맞힌 시전 비율은 서로 다른 단위이며 합치지 않습니다.`;return '이번 경기에서는 사용과 적중을 끝까지 안전하게 연결할 수 있는 수동 조준 스킬이 없었어요.'}
function projectileAccuracyTable(p){const rows=p.projectileHitRates||[];if(!rows.length)return '<div class="notice">관측된 캐릭터 스킬 시작 기록이 없습니다.</div>';const body=rows.map(row=>{const shapes=(row.projectileShape?.values||[]).map(projectileShapeKo).join('·')||(row.hitRateUnit==='skill-cast'?'시전 적중':'형태 미확정'),codes=(row.projectileCodes||[]).length?row.projectileCodes.join(', '):(row.hitRateUnit==='skill-cast'?'스킬·상태 코드 일치':'코드 미확정'),unit=hitRateUnitKo(row),rate=row.hitRateCalculable?`${pct(row.playerHitRate)} · ${fmt(row.playerHitAttemptCount)}/${fmt(row.attemptCount)}${unit}`:'계산 불가',uses=`교전 ${fmt(row.castCount)}회 · 전체 ${fmt(row.allCastCount)}회${row.projectilesPerCast>1?` · 시전당 ${fmt(row.projectilesPerCast)}발`:''}`,attempt=row.attemptDenominator?.unit||row.attemptDenominator?.event||'분모 미확정';return `<tr><td>${familyKo(row.family)}</td><td>${esc(row.skillId||'스킬 이름 미확인')}</td><td>${esc(codes)}</td><td>${esc(shapes)}</td><td>${esc(uses)}</td><td>${esc(row.hitEvidencePacket?.packet||'패킷 미확정')}</td><td>${esc(attempt)}</td><td><b>${rate}</b><br><small>${esc(projectileReasonKo(row.hitRateReason))}</small></td></tr>`}).join('');return `<div class="table-wrap"><table><thead><tr><th>슬롯</th><th>스킬</th><th>연결 근거</th><th>판정 형태</th><th>사용 횟수</th><th>적중 근거</th><th>교전 분모</th><th>교전 적중률·이유</th></tr></thead><tbody>${body}</tbody></table></div>`}
const chainKo=value=>value?value.split('>').map(familyKo).join(' → '):'기록 없음';
const capabilityStateKo=value=>({Airborne:'공중 제어',Blind:'실명',Charm:'매혹',Disarmed:'무장 해제',Drowse:'졸음',Fear:'공포',Fetter:'속박',Frozen:'빙결',Grounding:'이동기 봉쇄',Polymorph:'변이',Silence:'침묵',Sleep:'수면',Slow:'감속',Stun:'기절',Suppressed:'제압',Taunt:'도발',CCImmunity:'군중 제어 면역',CCMovementImmunity:'이동 방해 면역',CCStopImmunity:'행동 정지 면역',DebuffImmunity:'약화 효과 면역',DisplacementImmunity:'강제 이동 면역',DyingImmunity:'빈사 면역',EvasionNormalAttack:'기본 공격 회피',Invulnerability:'무적',Protectability:'피해 보호',protectability:'피해 보호',SlowImmunity:'감속 면역',Stasis:'경직 정지',Unstoppable:'저지 불가',Untargetability:'대상 지정 불가'}[value]||value);
function capabilityDuration(row){const values=[...new Set((row.baseDurationSecondsByLevel||[]).map(x=>Number(x.value)).filter(Number.isFinite))].sort((a,b)=>a-b);return values.length?`${values.map(v=>Number.isInteger(v)?v:v.toFixed(2).replace(/0+$/,'').replace(/\.$/,'')).join('/')}초`:'기본 시간 수치 없음'}
function capabilityCcSummary(rows){const entries=[],seen=new Set();for(const row of rows||[]){const family=familyKo(row.family),state=capabilityStateKo(row.stateType),duration=capabilityDuration(row),key=`${family}|${state}|${duration}`;if(seen.has(key))continue;seen.add(key);entries.push(`${family} 계열 ${state} 기본 ${duration}`)}return entries.length?entries.join(' · '):'자동 분류된 CC 상태 없음'}
function movementCapabilitySummary(rows){const groups=new Map();for(const row of rows||[]){const label=familyKo(row.family),items=groups.get(label)||[];items.push(row);groups.set(label,items)}const labels=[...groups].map(([label,items])=>items.length>1?`${label}(${items.length}개 그룹)`:label);return labels.length?labels.join(' · '):'이동 판정 슬롯 없음'}
function renderDeathReview(){
 const p=selected(),d=p.deathReview,rows=d.deaths||[],meta=data.deathReview;
 if(!rows.length){$('#content').innerHTML=`<section class="panel"><h2>${p.publicLabel} · 사망 복기</h2><div class="notice">이 경기에는 이 플레이어의 <code>CmdDead</code>가 없어 복기할 사망 장면이 없습니다.</div></section>`;return}
 const metric=value=>value===null||value===undefined?'확인 불가':`${Number(value).toFixed(1)}m`;
 const ccText=row=>(row.crowdControlReceived||[]).map(item=>`${capabilityStateKo(item.stateType)} ${fmt(item.count)}회`).join(' · ')||'기록 없음';
 const lead=d.localNumberDisadvantageCount>=2?`${fmt(d.localNumberDisadvantageCount)}번은 다운 시점 ${meta.nearbyRadiusMeters}m 안에서 생존 팀원보다 적이 더 많았어요.`:d.multiTargeterPressureCount>=2?`${fmt(d.multiTargeterPressureCount)}번은 다운 직전 여러 적이 직접 이 플레이어를 대상으로 지정했어요.`:`사망 ${fmt(d.deathCount)}번의 다운 직전 ${meta.contextSeconds}초를 같은 기준으로 나눠 봤어요.`;
 const cards=rows.map(row=>{
  const positionKnown=row.focusPositionStatus==='derived-held-last-decoded-anchor',flags=row.feedbackFlags||[],find=key=>flags.includes(key),feedback=[];
  if(positionKnown)feedback.push(`${meta.nearbyRadiusMeters}m 안에서 생존 팀원 ${fmt(row.nearbyAliveAllyCount)}명, 적 ${fmt(row.nearbyAliveEnemyCount)}명이었어요.`);else feedback.push('다운 시점 위치 앵커가 없어 주변 인원 비교는 하지 않았어요.');
  if(find('multi-enemy-target-selection-pressure'))feedback.push(`직전 ${meta.targetWindowSeconds}초에 적 ${fmt(row.enemyTargeterCount)}명이 ${fmt(row.enemyTargetActionCount)}번 직접 대상으로 지정했어요.`);else feedback.push(`직전 ${meta.targetWindowSeconds}초의 직접 대상 지정은 적 ${fmt(row.enemyTargeterCount)}명·${fmt(row.enemyTargetActionCount)}번이에요.`);
  if(row.crowdControlCount)feedback.push(`직전 ${meta.contextSeconds}초에 CC ${fmt(row.crowdControlCount)}회(${ccText(row)})가 기록됐어요.`);
  if(row.positiveShieldGain||row.healingReceived)feedback.push(`같은 구간에 보호막 증가 ${fmt(row.positiveShieldGain)}, 회복 ${fmt(row.healingReceived)}이 관측됐어요.`);else feedback.push('같은 구간에는 보호막 증가나 양수 회복 패킷이 기록되지 않았어요.');
  if(row.teammateCollapseWithinWindow.length){const names=row.teammateCollapseWithinWindow.map(item=>`${esc(item.characterName)} ${Number(item.delaySeconds).toFixed(1)}초 뒤`).join(' · ');feedback.push(`이후 ${meta.collapseWindowSeconds}초 안에 팀원 흐름도 이어졌어요: ${names}.`)}
  const tools=(row.nearbyEnemyStaticTools||[]).map(tool=>`<li><b>${esc(tool.characterName)}</b> · CC 정의 ${fmt(tool.crowdControlStateCount)} · 이동 스킬 ${fmt(tool.movementSkillCount)} · 방어 상태 ${fmt(tool.defensiveStateCount)} · 보호막/회복 상태 ${fmt(tool.shieldOrHealingStateCount)}</li>`).join('');
  return `<article class="panel"><div class="human-lead"><div><strong>${clock(row.focusTick)} · ${fmt(row.deathNumber)}번째 사망 복기</strong><p>${feedback.join(' ')}</p></div><button type="button" class="btn jump" data-death-jump="${row.focusTick}">지도에서 보기</button></div><div class="human-grid"><article class="human-fact"><span>다운→사망</span><b>${row.downToDeathSeconds===null?'다운 기록 없음':Number(row.downToDeathSeconds).toFixed(1)+'초'}</b><small>${row.finisherCharacterName?`마지막 처치 판정 ${esc(row.finisherCharacterName)}`:'마지막 처치자 연결 불가'}</small></article><article class="human-fact"><span>가장 가까운 생존 팀원</span><b>${metric(row.closestAliveTeammateMeters)}</b><small>마지막 관측 위치 기준</small></article><article class="human-fact"><span>직전 ${meta.contextSeconds}초 숫자가 남은 피해</span><b>${fmt(row.recordedDamageTaken)}</b><small>공격자 ${fmt(row.recordedDamageAttackerCount)}명 · 전체 피해 아님</small></article><article class="human-fact"><span>CC · 보호막 증가 · 회복</span><b>${fmt(row.crowdControlCount)} · ${fmt(row.positiveShieldGain)} · ${fmt(row.healingReceived)}</b><small>발신자 없는 패킷은 특정 실험체에 귀속하지 않음</small></article></div>${tools?`<details class="analysis-details"><summary>당시 30m 안 적 실험체의 기본 도구 보기</summary><div class="detail-body"><ul>${tools}</ul><p class="judgment-note">이 수치는 이 패치 공식 gameDb의 정적 정의예요. 그 순간 스킬이 준비됐거나 실제 사용됐다는 뜻은 아닙니다.</p></div></details>`:''}</article>`
 }).join('');
 $('#content').innerHTML=`<section class="panel"><h2>${p.publicLabel} · 사망 직전 상황 복기</h2><div class="human-lead"><div><strong>원인을 지어내지 않고, 확인된 장면만 봤어요</strong><p>${lead}</p></div><span class="human-kicker">다운·사망 주변 근거</span></div><div class="human-grid"><article class="human-fact"><span>복기한 사망</span><b>${fmt(d.deathCount)}번</b></article><article class="human-fact"><span>근거리 수적 열세</span><b>${fmt(d.localNumberDisadvantageCount)}번</b></article><article class="human-fact"><span>여러 적의 직접 대상 지정</span><b>${fmt(d.multiTargeterPressureCount)}번</b></article><article class="human-fact"><span>직전 CC 관측</span><b>${fmt(d.crowdControlPressureCount)}번</b></article></div></section>${cards}<section class="panel"><details class="analysis-details"><summary>어디까지 믿을 수 있나요?</summary><div class="detail-body"><p class="judgment-note">대상 지정·CC·보호막·회복·다운·사망은 exact 패킷입니다. 주변 거리는 마지막으로 해석된 위치를 다운 시점까지 유지해 계산한 파생값이며, 위치가 없으면 비교하지 않습니다. 피해는 숫자가 실제로 남은 <code>CmdDamage</code>만 더한 부분합입니다. 따라서 이 화면은 사망 원인을 단정하거나 특정 팀원의 실수로 귀속하지 않습니다.</p></div></details></section>`;
 document.querySelectorAll('[data-death-jump]').forEach(b=>b.onclick=()=>{state.cursor=Number(b.dataset.deathJump);state.view='map';render()})
}
function renderGrowthTempo(){
 const p=selected(),g=p.growthTempo,team=data.players.filter(row=>row.teamNumber===p.teamNumber);
 const milestone=level=>g.levelMilestones.find(row=>row.level===level)?.tick??null;
 const time=t=>t===null?'기록에서 찾지 못함':clock(t);
 const credit=v=>v===null||v===undefined?'기록 없음':Number(v).toLocaleString('ko-KR',{maximumFractionDigits:2});
 const audit=g.creditCounterAudit||{},gap=audit.counterBalanceGap;
 const growthSummary=[milestone(15)!==null?`15레벨은 ${clock(milestone(15))}에 찍었어요`:'15레벨을 찍은 기록은 찾지 못했어요',g.purpleBuildTick!==null?`보라장비 5부위는 ${clock(g.purpleBuildTick)}에 완성됐어요`:'보라장비 5부위를 완성한 기록은 찾지 못했어요',`경기 마지막 주무기 숙련도는 ${fmt(g.finalMasteryLevel)}이었어요`].join('. ')+'.';
 const creditStory=gap===null||gap===undefined
  ?`종료 결과의 두 누계는 획득 ${fmt(g.totalGainCredit)}, 사용 ${fmt(g.totalUseCredit)}예요. 두 누계를 빼서 순수익으로 해석하지 않았어요.`
  :`종료 결과의 두 누계는 획득 ${fmt(g.totalGainCredit)}, 사용 ${fmt(g.totalUseCredit)}예요. 관측된 보유 크레딧은 ${credit(audit.startObservedCredit)}에서 ${credit(audit.endObservedCredit)}로 끝났고, 두 결과 누계로 계산한 잔액과 ${credit(Math.abs(gap))}의 집계 차이가 남습니다. 결과의 정수 누계와 소수 단위 보유액이 같은 거래 원장으로 맞아떨어지지 않으므로, 획득에서 사용을 빼 순수익으로 해석하지 않았어요.`;
 const teamRows=team.map(row=>{const growth=row.growthTempo,l15=growth.levelMilestones.find(x=>x.level===15)?.tick??null;return `<tr class="${row.publicPlayerId===p.publicPlayerId?'selected':''}"><td><b>${row.publicLabel}</b> · ${esc(row.characterName)}</td><td>${time(l15)}</td><td>${time(growth.purpleBuildTick)}</td><td class="num">${fmt(growth.finalMasteryLevel)}</td><td class="num">${fmt(growth.totalGainCredit)} / ${fmt(growth.totalUseCredit)}</td></tr>`}).join('');
 const levelRows=g.levelMilestones.map(row=>`<tr><td>레벨 ${row.level}</td><td>${row.tick===null?'기록에서 찾지 못함':`<button type="button" class="btn jump" data-growth-jump="${row.tick}">${clock(row.tick)}</button>`}</td><td>플레이어 상태 기록</td></tr>`).join('');
 $('#content').innerHTML=`<section class="panel"><h2>${p.publicLabel} · 성장 과정</h2><div class="human-lead"><div><strong>${esc(humanStyle(g.styleLabel))}</strong><p>${growthSummary}</p></div><span class="human-kicker">이 경기에서 보인 모습</span></div><div class="human-grid"><article class="human-fact"><span>15 레벨 찍은 시각</span><b>${time(milestone(15))}</b></article><article class="human-fact"><span>보라장비 완성 시각</span><b>${time(g.purpleBuildTick)}</b></article><article class="human-fact"><span>경기에서 쓴 크레딧</span><b>${fmt(g.totalUseCredit)}</b></article><article class="human-fact"><span>마지막 주무기 숙련도</span><b>${fmt(g.finalMasteryLevel)}</b></article></div><div class="human-story"><b>크레딧 결과 누계</b><br>${creditStory}</div><details class="analysis-details"><summary>레벨 기록 모두 보기</summary><div class="detail-body"><div class="table-wrap"><table><thead><tr><th>무엇이 보였나</th><th>리플레이에서 처음 보인 시각</th><th>어디서 확인했나</th></tr></thead><tbody>${levelRows}</tbody></table></div></div></details><details class="analysis-details"><summary>팀원 3명의 성장 과정을 나란히 보기</summary><div class="detail-body"><div class="table-wrap"><table><thead><tr><th>플레이어</th><th>15 레벨 찍은 시각</th><th>보라장비 완성 시각</th><th class="num">마지막 주무기 숙련도</th><th class="num">종료 결과 획득 / 사용</th></tr></thead><tbody>${teamRows}</tbody></table></div></div></details><details class="analysis-details"><summary>시각을 읽을 때 알아둘 점</summary><div class="detail-body"><p class="judgment-note">레벨 시각은 해당 상태가 처음 관측된 때이고, 보라장비 완성 시각은 보라 등급 이상 장비가 5부위에서 처음 확인된 때예요. 실제 도달 순간은 이보다 조금 빠를 수 있으며, 기록 사이의 시간을 임의로 채우지는 않았어요.</p></div></details></section>`;
 document.querySelectorAll('[data-growth-jump]').forEach(b=>b.onclick=()=>{state.cursor=Number(b.dataset.growthJump);state.view='map';render()})
}
function renderObjectivePreparation(){
 const p=selected(),o=p.objectivePreparation,rows=o.objectives;
 const arrival=row=>row.arrivalStatus==='before-active'?`나오기 ${Math.abs(row.arrivalLeadSeconds).toFixed(1)}초 전`:row.arrivalStatus==='after-active'?`나온 뒤 ${Math.abs(row.arrivalLeadSeconds).toFixed(1)}초 후`:`${o.preparationRadiusMeters}m 안까지 간 기록 없음`;
 const arrivalSentence=row=>{if(row.arrivalStatus==='before-active'){const lead=Math.abs(row.arrivalLeadSeconds).toFixed(1);if(row.activeDistanceMeters===null)return `나오기 ${lead}초 전에 20m 안까지 갔어요.`;return row.activeDistanceMeters<=o.preparationRadiusMeters?`나오기 ${lead}초 전에 20m 안까지 갔고, 나올 때도 ${distance(row.activeDistanceMeters)} 안에 있었어요.`:`나오기 ${lead}초 전에 20m 안까지 주변을 확인한 뒤, 나올 때는 ${distance(row.activeDistanceMeters)} 떨어진 곳으로 이동해 있었어요.`}if(row.arrivalStatus==='after-active'){const delay=Math.abs(row.arrivalLeadSeconds).toFixed(1);return `나올 때는 ${distance(row.activeDistanceMeters)} 떨어져 있었고, ${delay}초 뒤에 20m 안으로 들어갔어요.`}return '20m 안까지 간 기록은 없어요.'};
 const distance=value=>value===null?'기록 없음':`${Number(value).toFixed(1)}m`;
 const reachedRows=rows.filter(row=>row.arrivalTick!==null);
 const beforeRows=rows.filter(row=>row.arrivalStatus==='before-active');
 const stayedCloseCount=beforeRows.filter(row=>row.activeDistanceMeters!==null&&row.activeDistanceMeters<=o.preparationRadiusMeters).length;
 const checkedThenMovedCount=beforeRows.filter(row=>row.activeDistanceMeters!==null&&row.activeDistanceMeters>o.preparationRadiusMeters).length;
 const objectiveSummary=`오브젝트 ${fmt(o.objectiveCount)}개 중 ${fmt(o.beforeActiveCount)}개는 나오기 전에 주변을 확인했어요. 그중 ${fmt(stayedCloseCount)}개는 출현 때도 근처를 지켰고, ${fmt(checkedThenMovedCount)}개는 먼저 확인한 뒤 다른 곳으로 이동했어요. 나머지 ${fmt(o.notObservedCount)}개는 20m 안에 들어간 기록을 찾지 못했어요.`;
 const reachedList=reachedRows.length?`<div class="story-list">${reachedRows.map(row=>`<div class="story-row"><button type="button" class="btn jump" data-objective-jump="${row.arrivalTick}">${clock(row.arrivalTick)}</button><div><b>${esc(row.label)}</b><span>${arrivalSentence(row)}</span></div></div>`).join('')}</div>`:'<div class="notice">이 경기에서는 오브젝트가 나오기 전후로 20m 안까지 간 기록을 찾지 못했어요.</div>';
 const body=reachedRows.map(row=>`<tr><td><button type="button" class="btn jump" data-objective-jump="${row.arrivalTick}">${clock(row.arrivalTick)}</button></td><td><button type="button" class="btn jump" data-objective-jump="${row.activeTick}">${clock(row.activeTick)}</button></td><td>${esc(row.label)}</td><td>${arrival(row)}</td><td class="num">${distance(row.activeDistanceMeters)}</td><td class="num">${distance(row.minimumObservedDistanceMeters)}</td></tr>`).join('');
 const reachedDetail=reachedRows.length?`<details class="analysis-details"><summary>20m 안까지 간 ${fmt(reachedRows.length)}개를 표로 보기</summary><div class="detail-body"><div class="table-wrap"><table><thead><tr><th>20m 안에 들어간 때</th><th>나타난 때</th><th>오브젝트</th><th>얼마나 일찍 또는 늦게</th><th class="num">나타났을 때 거리</th><th class="num">가장 가까웠던 거리</th></tr></thead><tbody>${body}</tbody></table></div></div></details>`:'';
 $('#content').innerHTML=`<section class="panel"><h2>${p.publicLabel} · 오브젝트에 미리 갔나?</h2><div class="human-lead"><div><strong>${esc(humanStyle(o.styleLabel))}</strong><p>${objectiveSummary}</p></div><span class="human-kicker">이 경기에서 보인 모습</span></div><div class="human-grid"><article class="human-fact"><span>출현 전에 주변을 확인함</span><b>${fmt(o.beforeActiveCount)} / ${fmt(o.objectiveCount)}개</b></article><article class="human-fact"><span>출현할 때도 근처를 지킴</span><b>${fmt(stayedCloseCount)}개</b></article><article class="human-fact"><span>사전 확인 후 다른 곳으로 이동</span><b>${fmt(checkedThenMovedCount)}개</b></article><article class="human-fact"><span>출현 뒤에 처음 접근</span><b>${fmt(o.afterActiveCount)}개</b></article></div><div class="human-story"><b>사전 확인 후 이동</b><br>출현 전에 들렀다가 멀어진 움직임은 준비를 포기한 것으로 보지 않았어요. 팀원 한 명이 주변에 사람이 없는지 먼저 확인한 뒤 다른 곳으로 이동하는 정찰 흐름에서도 나타날 수 있습니다.</div><h3 class="section-label">출현 전에 확인하거나 나중에 접근한 오브젝트</h3>${reachedList}${reachedDetail}<details class="analysis-details"><summary>어떻게 판단했나요?</summary><div class="detail-body"><p class="judgment-note">오브젝트가 예고된 때부터 사라질 때까지, 리플레이에 남은 이동 위치가 오브젝트에서 20m 안으로 처음 들어온 시점을 찾았어요. 이동 기록이 없는 사이를 임의로 채우지는 않았습니다. 가까이 갔다는 뜻일 뿐, 실제로 획득했거나 처치했다는 뜻은 아니에요. 출현 전에 들렀다가 멀어진 경우는 ‘사전 확인 후 이동’으로 따로 표시하지만, 실제 플레이어의 의도까지 확정하지는 않습니다.</p></div></details></section>`;
 document.querySelectorAll('[data-objective-jump]').forEach(b=>b.onclick=()=>{state.cursor=Number(b.dataset.objectiveJump);state.view='map';render()})
}
function teamTimingStory(p,tick,getTick,label){
 if(tick===null||tick===undefined)return `${label} 시각은 기록에서 찾지 못했어요.`;
 const peers=data.players.filter(row=>row.teamNumber===p.teamNumber&&row.publicPlayerId!==p.publicPlayerId).map(row=>({row,tick:getTick(row)})).filter(item=>item.tick!==null&&item.tick!==undefined&&Number.isFinite(Number(item.tick)));
 const together=peers.filter(item=>Math.abs(Number(item.tick)-Number(tick))<=1);
 if(together.length)return `${label}은 ${clock(tick)}이었고, ${together.map(item=>esc(item.row.characterName)).join('·')}와 거의 동시에 도달했어요.`;
 if(!peers.length)return `${label}은 ${clock(tick)}이었어요.`;
 const all=[{self:true,tick:Number(tick)},...peers.map(item=>({self:false,tick:Number(item.tick)}))].sort((a,b)=>a.tick-b.tick),rank=all.findIndex(item=>item.self)+1;
 return `${label}은 ${clock(tick)}이었고, 팀 ${fmt(all.length)}명 중 ${rank===1?'가장 빨랐어요':rank===all.length?'가장 늦었어요':`${fmt(rank)}번째였어요`}.`;
}
function fightSceneText(row){
 const entry=row.entryRole==='initiator'?'팀보다 먼저 싸움에 들어갔어요.':row.entryRole==='co-initiator'?'팀원과 거의 동시에 싸움에 들어갔어요.':`팀보다 ${Number(row.joinDelaySeconds||0).toFixed(1)}초 뒤에 합류했어요.`;
 let target='직접 노린 상대 기록은 남지 않았어요.';
 if(row.primaryTargetCharacterName){const changed=row.targetSwitchCount?`중간에 대상을 ${fmt(row.targetSwitchCount)}번 바꿨어요.`:'중간에 다른 상대로 바꾸지 않았어요.';target=`${objectName(row.primaryTargetCharacterName)} 가장 자주 노렸고, ${changed}`}
 const result=row.survived?'마지막에는 살아서 빠져나왔어요.':'이 싸움에서는 끝내 사망했어요.';
 return `${entry} ${target} ${result}`;
}
function representativeFightScenes(p){
 const cut=data.meta.firstTick+120*(data.meta.targetFrameRate||60);
 const rows=(p.combatJudgment?.episodes||[]).filter(row=>row.entryTick>=cut),picked=[];
 const add=row=>{if(row&&!picked.some(item=>item.teamEpisodeNumber===row.teamEpisodeNumber))picked.push(row)};
 add(rows.find(row=>!row.survived));
 add([...rows].filter(row=>row.targetActionCount>0).sort((a,b)=>b.targetActionCount-a.targetActionCount||b.durationSeconds-a.durationSeconds)[0]);
 add(rows.find(row=>row.targetActionCount>0));
 add([...rows].reverse().find(row=>row.targetActionCount>0&&row.survived));
 return picked.sort((a,b)=>a.entryTick-b.entryTick).slice(0,3);
}
function fightSceneTitle(row){if(!row.survived)return '끝내 살아 나오지 못한 싸움';if(row.entryRole==='initiator')return '먼저 들어가 살아 나온 싸움';if(row.entryRole==='co-initiator')return '팀원과 함께 들어간 싸움';return '뒤이어 합류해 마친 싸움'}
function sceneCoaching(){return selected().sceneCoaching||null}
function coachingMix(){const counts=sceneCoaching()?.feedback?.verdictCounts||{};return [counts['near-simultaneous-normal']?`팀과 거의 동시에 들어간 싸움 ${fmt(counts['near-simultaneous-normal'])}번`:'',counts['supported-first-entry']?`팀이 바로 올 수 있어 성립한 선진입 ${fmt(counts['supported-first-entry'])}번`:'',counts['calculated-low-hp-isolated-enemy']?`체력이 낮았지만 적도 고립돼 계산 가능했던 진입 ${fmt(counts['calculated-low-hp-isolated-enemy'])}번`:'',counts['unsupported-isolated-entry']?`지원받기 어려운 고립 진입 ${fmt(counts['unsupported-isolated-entry'])}번`:'',counts['late-after-teammate-down']?`팀원이 다운된 뒤의 후속 진입 ${fmt(counts['late-after-teammate-down'])}번`:'',counts['insufficient-evidence']?`자료만으로 가르기 어려운 진입 ${fmt(counts['insufficient-evidence'])}번`:''].filter(Boolean).join(', ')}
function sceneFlow(row){const steps=row.timeline||[];if(!steps.length)return '';return `<div class="life-flow">${steps.map((step,index)=>`${index?'<div class="life-arrow"><span>→</span></div>':''}<div class="life-step ${step.key||''}"><span>${esc(step.label)}</span><b>${step.tick==null?'기록 없음':clock(step.tick)}</b></div>`).join('')}</div>`}
function sceneCard(row,jumpTick){const evidence=(row.evidence||[]).map(item=>`<li>${esc(item)}</li>`).join('');const alt=row.alternative?`<p class="scene-alt">${esc(row.alternative)}</p>`:'';const slots=row.cooldownSlots||{};const cd=Object.values(slots).filter(item=>item.readiness&&item.readiness!=='unobserved').map(item=>`${item.label} ${item.readiness==='ready'?'준비':item.readiness==='cooldown'?`${item.remainingSeconds??'—'}초`:'미확인'}`).join(' · ');return `<article class="scene-card"><div class="human-lead"><div><strong>${esc(row.headline)}</strong></div><button type="button" class="btn jump" data-scene-jump="${jumpTick}">이 시점 보기</button></div>${sceneFlow(row)}<ul class="scene-evidence">${evidence}</ul>${alt}<details class="analysis-details"><summary>상세 수치와 계산 경계 보기</summary><div class="detail-body"><p class="judgment-note">${cd||'슬롯 쿨다운은 아직 관측되지 않았어요.'} HP와 거리는 마지막 관측값을 그 시각까지 유지한 파생값이며, 의도·적중·막타 스킬은 단정하지 않습니다.</p></div></details></article>`}
function deathSeekTick(start,fallback){const fps=data.meta.targetFrameRate||60,origin=start!=null?Number(start):Number(fallback);if(!Number.isFinite(origin))return data.meta.firstTick;return Math.max(data.meta.firstTick,Math.min(data.meta.lastTick,origin-3*fps))}
function deathFlow(row){const start=row.combatStartTick,down=row.downTick,death=row.deathTick,alive=start!=null&&down!=null?`${clock(start)}~${clock(down)}`:down!=null?`${clock(down)} 직전까지`:'확인 불가';return `<div class="life-flow"><div class="life-step fight"><span>교전 시작</span><b>${start==null?'기록 없음':clock(start)}</b></div><div class="life-arrow"><span>→</span></div><div class="life-step alive"><span>생존 · 교전 중</span><b>${alive}</b></div><div class="life-arrow"><span>→</span></div><div class="life-step down"><span>다운</span><b>${down==null?'다운 기록 없음':clock(down)}</b></div><div class="life-arrow"><span>→</span></div><div class="life-step dead"><span>사망</span><b>${death==null?'기록 없음':clock(death)}</b></div></div>`}
function deathCooldownLine(p,tick){
 const order=[['Active1','Q'],['Active2','W'],['Active3','E'],['Active4','R'],['WeaponSkill','무기'],['TacticalSkill','전술']];
 const states=cooldownStatesAt(p,tick);
 return order.map(([family,label])=>{
  const current=states.get(family),remaining=cooldownRemaining(current,tick);
  let text='미사용',cls='is-unknown';
  if(current?.kind==='unknown'||(current?.kind==='known'&&remaining===null)){text='미확인';cls='is-unknown'}
  else if(current?.kind==='known'&&remaining<=0){text='준비';cls='is-ready'}
  else if(current?.kind==='known'){const sec=remaining/100;text=`${Number.isInteger(sec)||Math.abs(sec-Math.round(sec))<0.05?String(Math.round(sec)):sec.toFixed(1)}초`;cls='is-cd'}
  return `<span class="${cls}">${label}-${text}</span>`;
 }).join(' ');
}
function mapDeathReviewHtml(){
 const p=selected(),rows=(p.deathReview||{}).deaths||[],scenes=sceneCoaching()?.deaths||[];
 const byNo=new Map(scenes.map(row=>[row.deathNumber,row]));
 if(!rows.length)return `<details class="map-deaths" id="mapDeaths"${state.deathsOpen?' open':''}><summary class="map-deaths-head"><h2>사망 복기</h2><span class="human-kicker">교전 시작부터 사망까지</span></summary><p class="death-empty">이 경기에는 복기할 사망 장면이 없어요.</p></details>`;
 const cards=rows.map(row=>{
  const scene=byNo.get(row.deathNumber)||{};
  const start=scene.combatStartTick,down=row.downTick,death=row.deathTick,focus=row.focusTick,cdTick=down!=null?down:focus,seek=deathSeekTick(start,cdTick),end=death!=null?death:cdTick;
  const facts=[];
  if(row.focusPositionStatus==='derived-held-last-decoded-anchor')facts.push(`30m 아군 ${fmt(row.nearbyAliveAllyCount)} · 적 ${fmt(row.nearbyAliveEnemyCount)}`);
  if(row.closestAliveTeammateMeters!=null)facts.push(`가까운 팀원 ${Number(row.closestAliveTeammateMeters).toFixed(1)}m`);
  if(row.enemyTargeterCount)facts.push(`직접 지정 ${fmt(row.enemyTargeterCount)}명`);
  if(row.crowdControlCount)facts.push(`CC ${fmt(row.crowdControlCount)}회`);
  if(row.finisherCharacterName)facts.push(`마지막 처치 ${esc(row.finisherCharacterName)}`);
  const early=scene.earlyCombat?'<span class="early" title="시작 후 2분 안 장면이라, 다음 플레이 조언에서는 빼 두었어요.">2분 전</span>':'';
  return `<button type="button" class="death-card" data-death-seek="${seek}" data-death-end="${end}"><div class="death-card-head"><b>${down!=null?clock(down):clock(focus)}</b><span class="idx">${fmt(row.deathNumber)}번째</span>${early}</div>${deathFlow({combatStartTick:start,downTick:down,deathTick:death})}<p class="death-cd">${deathCooldownLine(p,cdTick)}</p><p class="death-facts">${facts.join(' · ')||'다운 직전 주변 신호는 따로 묶지 않았어요.'}</p></button>`;
 }).join('');
 return `<details class="map-deaths" id="mapDeaths"${state.deathsOpen?' open':''}><summary class="map-deaths-head"><h2>사망 복기</h2><span class="human-kicker">교전 시작부터 사망까지</span></summary><p class="map-deaths-lead">카드를 누르면 교전 시작 3초 전으로 옮기고, 그 사람을 가운데 최대 확대로 봅니다. 지도를 끌거나 맞춤을 누르면 다시 자유롭게 봅니다.</p><div class="death-list">${cards}</div></details>`;
}
function bindMapDeaths(){const host=$('#mapDeaths');if(host)host.addEventListener('toggle',()=>{state.deathsOpen=host.open});document.querySelectorAll('#mapDeaths [data-death-seek]').forEach(card=>card.onclick=()=>{state.cursor=Number(card.dataset.deathSeek);state.cameraLock='focus';syncTransport();drawMap();syncPhaseNote();const shell=$('#mapCanvas')&&$('#mapCanvas').parentElement;if(shell)shell.scrollIntoView({block:'nearest'})})}
function markActiveDeath(){const cards=[...document.querySelectorAll('#mapDeaths [data-death-seek]')];if(!cards.length)return;const t=state.cursor,pad=(data.meta.targetFrameRate||60)*8;let best=null,bestDist=Infinity;for(const card of cards){const from=Number(card.dataset.deathSeek),to=Number(card.dataset.deathEnd||from);if(t>=from&&t<=to){if(bestDist>0||from>=Number(best&&best.dataset.deathSeek||-Infinity)){best=card;bestDist=0}continue}const dist=t<from?from-t:t-to;if(bestDist>0&&dist<bestDist){bestDist=dist;best=card}}cards.forEach(card=>card.classList.toggle('is-active',card===best&&bestDist<=pad))}
function bindSceneJumps(){document.querySelectorAll('[data-scene-jump]').forEach(button=>button.onclick=()=>{state.cursor=Number(button.dataset.sceneJump);state.view='map';render()})}
function playstyleNarrative(p){
 const j=p.combatJudgment||{},s=p.skillOperation||{},o=p.objectivePreparation||{},d=p.deathReview||{},g=p.growthTempo||{},r=p.result||{},next=p.sceneCoaching?.feedback?.nextPlay,habit=p.sceneCoaching?.feedback?.habitStory;
 const divide=(a,b)=>Number.isFinite(Number(a))&&Number(b)>0?Number(a)/Number(b):null;
 const joinRate=divide(j.participatedEpisodeCount,j.teamEpisodeCount),initRate=divide(j.initiationCount,j.participatedEpisodeCount);
 const basicShare=divide(r.damageToPlayer_basic,r.damageToPlayer),skillShare=divide(r.damageToPlayer_skill,r.damageToPlayer);
 let title=next?.headline||'이 경기에서 반복해서 나온 선택을 장면으로 묶어 봤어요';
 if(!next&&joinRate!==null&&initRate!==null){
  if(joinRate>=.9&&initRate>=.5)title='싸움을 거의 놓치지 않았고, 절반 이상은 먼저 또는 팀과 함께 부딪혔어요';
  else if(joinRate>=.9&&initRate<.3)title='팀 싸움은 거의 따라갔지만, 먼저 열기보다 팀이 만든 틈을 이어받았어요';
  else if(joinRate<.75)title='모든 싸움을 따라가기보다 일부 교전은 건너뛰고 합류했어요';
  else if(initRate>=.5)title='합류한 싸움에서는 먼저 또는 팀과 함께 들어간 장면이 많았어요';
  else title='팀이 연 싸움에 뒤이어 들어가 화력을 보탠 장면이 많았어요';
 }
 let damageLead='피해를 만든 방식은 분류할 기록이 부족해요.';
 if(basicShare!==null&&skillShare!==null){
  if(basicShare>=.45&&basicShare>skillShare)damageLead=`대인 피해는 기본 공격 비중이 ${pct(basicShare)}로 가장 컸고, 스킬 비중은 ${pct(skillShare)}였어요.`;
  else if(skillShare>=.65)damageLead=`대인 피해는 스킬 비중이 ${pct(skillShare)}로 뚜렷했고, 기본 공격 비중은 ${pct(basicShare)}였어요.`;
  else damageLead=`대인 피해는 기본 공격 ${pct(basicShare)}, 스킬 ${pct(skillShare)}로 한쪽에만 치우치지 않았어요.`;
  const damageMode=basicShare>=.45&&basicShare>skillShare?'기본 공격으로':skillShare>=.65?'스킬로':'기본 공격과 스킬을 섞어';
  if(!next)title=`${title}. 화력은 ${damageMode} 이어 갔어요.`;
 }
 const fightStory=habit||(j.teamEpisodeCount
  ?`${joinRate>=.9?'팀에서 싸움이 열리면 거의 빠지지 않고 함께 들어갔어요.':joinRate<.75?'모든 싸움을 따라가기보다 합류할 장면을 나눠 가져갔어요.':'팀 싸움에는 대체로 함께했어요.'} ${initRate>=.5?'뒤따라가기보다 먼저 또는 팀원과 나란히 들어간 장면이 더 많았어요.':initRate<.3?'먼저 여는 쪽보다는 팀원이 시작한 싸움에 뒤이어 화력을 보탰어요.':'먼저 들어간 장면과 뒤이어 합류한 장면이 섞여 있었어요.'}`
  :'진입 방식을 읽을 만큼 이어진 팀 싸움 기록은 없었어요.');
 const damageStory=basicShare===null||skillShare===null?'피해를 어떤 방식으로 만들었는지 나눠 볼 기록이 부족했어요.':basicShare>=.45&&basicShare>skillShare?`기본 공격이 대인 피해의 ${pct(basicShare)}를 차지해, 스킬 사이에도 평타로 압박을 이어 간 경기였어요.`:skillShare>=.65?`대인 피해의 ${pct(skillShare)}가 스킬에서 나와, 화력이 스킬 피해에 뚜렷하게 실린 경기였어요.`:`기본 공격 ${pct(basicShare)}, 스킬 ${pct(skillShare)}로 어느 한쪽에만 기대지 않고 피해를 이어 갔어요.`;
 const beforeRows=(o.objectives||[]).filter(row=>row.arrivalStatus==='before-active'),afterRows=(o.objectives||[]).filter(row=>row.arrivalStatus==='after-active');
 const stayed=beforeRows.filter(row=>row.activeDistanceMeters!==null&&row.activeDistanceMeters<=o.preparationRadiusMeters).length,moved=beforeRows.filter(row=>row.activeDistanceMeters!==null&&row.activeDistanceMeters>o.preparationRadiusMeters).length;
 const purpleStory=teamTimingStory(p,g.purpleBuildTick,row=>row.growthTempo.purpleBuildTick,'보라장비 완성');
 const level15Tick=(g.levelMilestones||[]).find(row=>row.level===15)?.tick??null;
 const levelStory=teamTimingStory(p,level15Tick,row=>(row.growthTempo.levelMilestones||[]).find(item=>item.level===15)?.tick??null,'15레벨');
 const objectiveStory=beforeRows.length
  ?`오브젝트 ${fmt(beforeRows.length)}곳은 나오기 전에 20m 안까지 갔어요. ${o.meanArrivalLeadSeconds===null?'':`평균 ${Number(o.meanArrivalLeadSeconds).toFixed(1)}초 먼저였고, `}${fmt(stayed)}곳은 출현 때도 근처에, ${fmt(moved)}곳은 확인 뒤 다른 곳에 있었어요.`
  :afterRows.length?`출현 전에 20m 안까지 간 오브젝트는 없었고, ${fmt(afterRows.length)}곳은 나온 뒤에 접근했어요.`:'오브젝트 출현 전후 20m 안 접근이 없어 이 경기의 오브젝트 동선은 따로 해석하지 않았어요.';
 let riskStory=j.participatedEpisodeCount?`참여한 싸움 가운데 ${fmt(j.survivedEpisodeCount)}번은 살아서 마쳤어요. `:'';
 if(!d.deathCount)riskStory+='사망으로 이어진 장면은 없어서 억지로 위험 원인을 붙이지 않았어요.';
 else{
  const signals=[];
  if(d.localNumberDisadvantageCount)signals.push(`근거리 수적 열세 ${fmt(d.localNumberDisadvantageCount)}번`);
  if(d.multiTargeterPressureCount)signals.push(`여러 적의 직접 대상 지정 ${fmt(d.multiTargeterPressureCount)}번`);
  if(d.crowdControlPressureCount)signals.push(`직전 CC ${fmt(d.crowdControlPressureCount)}번`);
  if(d.teamCollapseCount)signals.push(`뒤이은 팀원 사망 ${fmt(d.teamCollapseCount)}번`);
  riskStory+=signals.length?`사망 장면에서는 ${signals.join(', ')}이 겹쳐 보였어요. 이동 지도 아래 사망 복기에서 다운과 사망 시각을 따로 볼 수 있어요.`:`사망은 확인됐지만, 수적 열세나 여러 적의 집중 공격, 직전 CC 가운데 하나로 묶을 만한 반복 신호는 없었어요.`;
 }
 const lead=next?.doThis||`${fightStory} ${damageStory} ${beforeRows.length?`오브젝트가 나오기 전에 먼저 들른 움직임도 확인됐어요.`:''}`;
 return {title,lead,fightStory,damageStory,growthStory:`${purpleStory} ${levelStory} ${objectiveStory}`,riskStory};
}
function renderSkillOperation(){
 const p=selected(),s=p.skillOperation,team=data.players.filter(row=>row.teamNumber===p.teamNumber),story=playstyleNarrative(p);
 const cap=p.characterCapabilities,observed=p.observedCapabilityEvidence,coaching=sceneCoaching();
 const ccSummary=capabilityCcSummary(cap.crowdControlStates),defenseSummary=(cap.defensiveStateTypes||[]).map(capabilityStateKo).join(' · ')||'자동 분류된 방어 상태 없음';
 const supportParts=[];if(cap.shieldStates.length)supportParts.push(`보호막 상태 ${fmt(cap.shieldStates.length)}개`);if(cap.namedHealingStates.length)supportParts.push(`이름으로 확인된 회복 상태 ${fmt(cap.namedHealingStates.length)}개`);const supportSummary=supportParts.join(' · ')||'자동 분류된 보호막·회복 상태 없음';
 const observedCc=(observed.crowdControlReceived||[]).reduce((sum,row)=>sum+row.count,0),observedStory=`이번 경기에는 CC를 받은 기록 ${fmt(observedCc)}회, 최대 보호막 ${fmt(observed.maxObservedShield)}, 아군 회복 ${fmt(observed.alliedHealingGiven)}이 남아 있어요. 누가 만들었는지 확인되지 않은 기록은 특정 실험체의 행동으로 돌리지 않았습니다.`;
 const ccRows=(cap.crowdControlStates||[]).map(row=>`<tr><td>${esc(capabilityStateKo(row.stateType))}</td><td>${esc(familyKo(row.family))}</td><td>${esc(capabilityDuration(row))}</td><td>${esc(row.skillId||'스킬 이름 연결 없음')}</td></tr>`).join('')||'<tr><td colspan="4">이 패치의 정적 상태표에서 자동 분류된 CC가 없습니다.</td></tr>';
 const cooldownRatio=row=>row.readyCombatRatio===null||row.readyCombatRatio===undefined?null:Math.max(0,1-Number(row.readyCombatRatio));
 const cooldownSeconds=row=>row.cooldownCombatSeconds===null||row.cooldownCombatSeconds===undefined?'기록 부족':`${Number(row.cooldownCombatSeconds).toFixed(1)}초 / 추적 ${Number(row.trackedCombatSeconds).toFixed(1)}초`;
 const cooldownData=['Active1','Active2','Active3','Active4','WeaponSkill','TacticalSkill'].map(family=>{const row=p.cooldowns.find(x=>x.family===family);return {family,row,ratio:row?cooldownRatio(row):null}});
 const cooldownCards=cooldownData.map(item=>{const caveat=item.row&&(item.row.holdEvents>0||item.row.copyEvents>0)?' · 재사용/유지형 주의':'';return `<article class="stat"><span>${familyKo(item.family)} · 싸우는 동안 쿨다운</span><b>${item.ratio===null?'기록 부족':pct(item.ratio)}</b><small>${item.row?cooldownSeconds(item.row)+caveat:'기록 부족'}</small></article>`}).join('');
 const rankedCooldown=cooldownData.filter(item=>item.ratio!==null&&['Active1','Active2','Active3','Active4'].includes(item.family)).sort((a,b)=>b.ratio-a.ratio);
 const cooldownStory=rankedCooldown.length?`싸우는 동안 ${familyKo(rankedCooldown[0].family)}의 쿨다운 타이머가 가장 오래 돌았어요. 적중률이나 사용 기회를 놓친 시간이 아니라, 사용 뒤 다시 준비되기를 기다린 시간만 뜻합니다.`:'싸우는 동안의 쿨다운 기록이 부족해 슬롯별 흐름은 비교하지 않았어요.';
 const familyCounts=['Active1','Active2','Active3','Active4','WeaponSkill','TacticalSkill'].map(family=>`<article class="stat"><span>${familyKo(family)} 시작</span><b>${fmt(s.familyCounts[family]||0)}건</b></article>`).join('');
 const episodeRows=s.episodes.map(row=>{const counts=row.familyCounts||{};return `<tr><td><button type="button" class="btn jump" data-skillop-jump="${row.startTick}">${clock(row.startTick)}</button></td><td class="num">${fmt(counts.Active1||0)}</td><td class="num">${fmt(counts.Active2||0)}</td><td class="num">${fmt(counts.Active3||0)}</td><td class="num">${fmt(counts.Active4||0)}</td><td class="num">${fmt(counts.WeaponSkill||0)}</td><td class="num">${fmt(counts.TacticalSkill||0)}</td><td class="num">${fmt(row.skillStartCount)}</td><td class="num">${fmt(row.normalAttackStartCount)}</td></tr>`}).join('')||'<tr><td colspan="9">확인된 대인 교전 스킬 기록이 없습니다.</td></tr>';
 const teamCooldown=(row,family)=>{const found=row.cooldowns.find(x=>x.family===family),value=found?cooldownRatio(found):null;return value===null?'—':pct(value)};
 const teamRows=team.map(row=>{const op=row.skillOperation;return `<tr class="${row.publicPlayerId===p.publicPlayerId?'selected':''}"><td><b>${row.publicLabel}</b> · ${esc(row.characterName)}</td><td class="num">${fmt(op.pvpSkillStartCount)}</td><td class="num">${fmt(op.pvpNormalAttackStartCount)}</td><td class="num">${teamCooldown(row,'Active1')}</td><td class="num">${teamCooldown(row,'Active2')}</td><td class="num">${teamCooldown(row,'Active3')}</td><td class="num">${teamCooldown(row,'Active4')}</td></tr>`}).join('');

 $('#content').innerHTML=`<section class="panel"><h2>${p.publicLabel} · 플레이 분석</h2><div class="human-lead"><div><strong>${esc(story.title)}</strong><p>${esc(story.lead)}</p></div><span class="human-kicker">장면으로 읽는 경기 복기</span></div><div class="analysis-prose"><article class="analysis-paragraph"><h3>싸움에 들어간 방식</h3><p>${story.fightStory}</p></article><article class="analysis-paragraph"><h3>화력을 이어 간 방식</h3><p>${story.damageStory} ${cooldownStory}</p></article><article class="analysis-paragraph"><h3>성장 뒤 움직인 곳</h3><p>${esc(sceneCoaching()?.wildlifeFlow?.story||'')} ${story.growthStory}</p></article><article class="analysis-paragraph"><h3>위험해진 순간</h3><p>${story.riskStory}</p></article></div><h3 class="section-label">이 경기를 설명하는 대표 장면</h3>${(coaching?.pickedEpisodeNumbers||[]).map(number=>(coaching.episodes||[]).find(row=>row.teamEpisodeNumber===number)).filter(Boolean).map(row=>sceneCard(row,row.entryTick)).join('')||'<div class="notice">시작 후 2분 이후의 대인 싸움이 없어 대표 장면을 고르지 않았어요.</div>'}<details class="analysis-details"><summary>분석에 사용한 수치 확인하기</summary><div class="detail-body"><div class="grid">${cooldownCards}${familyCounts}</div><p class="judgment-note">스킬 시작은 적중이나 키 입력 횟수가 아니며, 쿨다운 비율은 전투 상태와 exact 타이머가 겹친 시간입니다. 상세 수치는 해석을 확인하는 근거로만 두었습니다.</p></div></details><details class="analysis-details"><summary>이 실험체의 기본 도구와 실제 관측 기록 보기</summary><div class="detail-body"><div class="human-grid"><article class="human-fact"><span>CC 기본값</span><b>${esc(ccSummary)}</b></article><article class="human-fact"><span>보호막·회복 정의</span><b>${esc(supportSummary)}</b></article><article class="human-fact"><span>방어 상태 정의</span><b>${esc(defenseSummary)}</b></article><article class="human-fact"><span>이동 판정 슬롯</span><b>${esc(movementCapabilitySummary(cap.movementSkills))}</b></article></div><div class="human-story"><b>이번 경기에서 확인된 기록</b><br>${observedStory}</div><div class="table-wrap"><table><thead><tr><th>상태</th><th>슬롯 계열</th><th>공식 기본 시간</th><th>정적 스킬 이름</th></tr></thead><tbody>${ccRows}</tbody></table></div><p class="judgment-note">공식 gameDb의 정적 정의와 이번 경기에서 실제로 관측된 행동은 서로 다른 근거입니다.</p></div></details><div class="human-story"><b>스킬 적중</b><br>${projectileAccuracyLead(p)}</div><details class="analysis-details"><summary>스킬별 적중 기준 확인하기</summary><div class="detail-body">${projectileAccuracyTable(p)}<p class="judgment-note">발사체 스킬은 발 단위, 나머지 검증된 스킬은 맞힌 시전 단위입니다. 두 단위를 서로 합치지 않으며, 지속·지연·관통·폭발·왕복·설치형은 판정이 불완전하면 숫자를 만들지 않습니다.</p></div></details><details class="analysis-details"><summary>싸움별 스킬 기록과 팀원 비교 보기</summary><div class="detail-body"><div class="table-wrap"><table><thead><tr><th>싸움 시작</th><th class="num">Q</th><th class="num">W</th><th class="num">E</th><th class="num">R</th><th class="num">무기</th><th class="num">전술</th><th class="num">스킬 시작</th><th class="num">기본 공격</th></tr></thead><tbody>${episodeRows}</tbody></table></div><div class="table-wrap"><table><thead><tr><th>플레이어</th><th class="num">스킬 시작</th><th class="num">기본 공격</th><th class="num">Q 쿨다운</th><th class="num">W 쿨다운</th><th class="num">E 쿨다운</th><th class="num">R 쿨다운</th></tr></thead><tbody>${teamRows}</tbody></table></div></div></details></section>`;
 bindSceneJumps();
 document.querySelectorAll('[data-skillop-jump]').forEach(b=>b.onclick=()=>{state.cursor=Number(b.dataset.skillopJump);state.view='map';render()})
}
function renderSkills(){const p=selected(),a=p.skillDamageAttribution;$('#content').innerHTML=`<section class="panel"><h2>${p.publicLabel} · 스킬과 쿨다운</h2><div class="notice">종료 결과의 스킬 계열 총피해 ${fmt(a.exactSkillCategoryDamage)}는 exact입니다. 개별 스킬 피해는 ${a.perSkillDamageTotals===null?'계산 불가':'제공'}입니다.</div><div class="human-story"><b>스킬 적중</b><br>${projectileAccuracyLead(p)}</div><details class="analysis-details" open><summary>스킬별 적중 기준</summary><div class="detail-body">${projectileAccuracyTable(p)}</div></details><div class="skill-grid" style="margin-top:12px"><div><h3>스킬 시작</h3><div class="table-wrap"><table><thead><tr><th>스킬</th><th>슬롯</th><th class="num">시작</th><th class="num">전투 중</th></tr></thead><tbody>${p.skills.map(s=>`<tr><td>${esc(s.skillName)}</td><td>${esc(s.family)}</td><td class="num">${fmt(s.startCount)}</td><td class="num">${fmt(s.inCombatStartCount)}</td></tr>`).join('')}</tbody></table></div></div><div><h3>쿨다운 준비 상태</h3><div class="table-wrap"><table><thead><tr><th>슬롯</th><th class="num">시작</th><th class="num">준비 비율</th></tr></thead><tbody>${p.cooldowns.map(s=>`<tr><td>${esc(s.family)}</td><td class="num">${fmt(s.startCount)}</td><td class="num">${s.readyCombatRatio===null?'—':pct(s.readyCombatRatio)}</td></tr>`).join('')}</tbody></table></div></div></div></section><section class="panel"><h2>교전 세션</h2><div class="table-wrap"><table><thead><tr><th>#</th><th>구간</th><th class="num">시간</th><th class="num">준 피해</th><th class="num">받은 피해</th><th class="num">스킬 시작</th></tr></thead><tbody>${p.sessions.map(s=>`<tr><td>${s.number}</td><td>${clock(s.startTick)}–${clock(s.endTick)}</td><td class="num">${s.durationSeconds.toFixed(1)}초</td><td class="num">${fmt(s.damageDealt)}</td><td class="num">${fmt(s.damageTaken)}</td><td class="num">${fmt(s.activeSkillStarts)}</td></tr>`).join('')}</tbody></table></div></section>`}
function renderPrivacy(){const p=data.privacy;$('#content').innerHTML=`<section class="panel privacy"><h2>공개본에서 제거한 정보</h2><div class="grid"><article class="stat"><span>닉네임</span><b>미포함</b></article><article class="stat"><span>계정 식별자</span><b>미포함</b></article><article class="stat"><span>경기 식별자·시각</span><b>미포함</b></article><article class="stat"><span>원본 이벤트·파일·해시</span><b>미포함</b></article></div><p style="margin-top:12px;font-size:12.5px;color:var(--muted)">표시되는 <b style="color:var(--text)">${esc(p.deletionReference)}</b>와 P01 형식 번호는 이 공개 보고서 안에서만 쓰는 임의 표기입니다. 원본 <code>.er</code> 파일은 포함하거나 보관하지 않습니다.</p><p style="font-size:12.5px;color:var(--muted)">삭제·정정 요청은 보고서 ID를 적어 <a href="${esc(p.deletionContactUrl)}">ERCraft 문의</a>로 보내 주세요.</p><p class="hint" style="white-space:normal">${esc(p.unofficialServiceNotice)}</p></section><section class="panel"><h2>해석 경계</h2><ul>${data.limitations.map(x=>`<li>${esc(x)}</li>`).join('')}</ul></section>`}
function renderSkillsHuman(){
 const p=selected(),s=p.skillOperation,a=p.skillDamageAttribution,families=['Active1','Active2','Active3','Active4','WeaponSkill','TacticalSkill'];
 const accuracyLead=projectileAccuracyLead(p);
 const counts=families.map(family=>({family,count:Number(s.familyCounts[family]||0)})),ranked=[...counts].filter(row=>row.count>0).sort((a,b)=>b.count-a.count);
 const rhythm=ranked.length?`${familyKo(ranked[0].family)} 시작이 ${fmt(ranked[0].count)}건으로 가장 많았어요.${ranked[1]?` 다음은 ${familyKo(ranked[1].family)} ${fmt(ranked[1].count)}건이었어요.`:''}`:'대인 교전에서 슬롯별 스킬 시작을 확인하지 못했어요.';
 const utility=[];utility.push(s.weaponSkillStartCount?`무기 스킬은 ${fmt(s.weaponSkillStartCount)}번 시작했어요.`:'무기 스킬 시작 기록은 없었어요.');utility.push(s.tacticalSkillStartCount?`전술 스킬은 ${fmt(s.tacticalSkillStartCount)}번 시작했어요.`:'전술 스킬 시작 기록은 없었어요.');
 const countCards=counts.map(row=>`<article class="stat"><span>${familyKo(row.family)}</span><b>${fmt(row.count)}번</b><small>대인 교전에서 시작된 기록</small></article>`).join('');
 const cooldownRows=families.map(family=>{const row=p.cooldowns.find(item=>item.family===family);return `<tr><td>${familyKo(family)}</td><td class="num">${row?fmt(row.startCount):'—'}</td><td class="num">${row&&row.readyCombatRatio!==null?pct(Math.max(0,1-Number(row.readyCombatRatio))):'—'}</td><td>${row?(row.holdEvents||row.copyEvents?'재사용·유지형 기록 포함':'일반 쿨다운 기록'):'해당 슬롯 기록 없음'}</td></tr>`}).join('');
 const rawRows=p.skills.map(row=>`<tr><td>${familyKo(row.family)}</td><td><code>${esc(row.skillName)}</code></td><td class="num">${fmt(row.startCount)}</td><td class="num">${fmt(row.inCombatStartCount)}</td></tr>`).join('');

 $('#content').innerHTML=`<section class="panel"><h2>${p.publicLabel} · 스킬 기록</h2><div class="human-lead"><div><strong>${esc(rhythm)}</strong><p>스킬 횟수를 성적표처럼 늘어놓지 않고, 싸움에서 어떤 공격 수단을 반복했는지 읽어 봤어요.</p></div><span class="human-kicker">사용 흐름 먼저</span></div><div class="analysis-prose"><article class="analysis-paragraph"><h3>기본 공격과 스킬의 리듬</h3><p>대인 교전에서 액티브 스킬 시작은 ${fmt(s.pvpSkillStartCount)}건, 기본 공격 시작은 ${fmt(s.pvpNormalAttackStartCount)}건이었어요. 유지형이나 재시전형은 단계가 다시 시작될 때 별도 기록될 수 있어, 실제 키 입력 횟수로 보지는 않았습니다.</p></article><article class="analysis-paragraph"><h3>무기·전술 스킬</h3><p>${utility.join(' ')}</p></article><article class="analysis-paragraph"><h3>피해가 남은 방식</h3><p>종료 결과에서 스킬 계열 대인 피해는 ${fmt(a.exactSkillCategoryDamage)}이었어요. Q·W·E·R별 피해로 나눌 연결 근거는 아직 없습니다.</p></article><article class="analysis-paragraph"><h3>스킬 적중</h3><p>${accuracyLead}</p></article></div><details class="analysis-details"><summary>스킬별 적중 기준 확인하기</summary><div class="detail-body">${projectileAccuracyTable(p)}</div></details><details class="analysis-details"><summary>슬롯별 시작 횟수와 쿨다운 확인하기</summary><div class="detail-body"><div class="grid">${countCards}</div><div class="table-wrap"><table><thead><tr><th>슬롯</th><th class="num">전체 시작</th><th class="num">싸우는 동안 쿨다운</th><th>주의점</th></tr></thead><tbody>${cooldownRows}</tbody></table></div><p class="judgment-note">쿨다운 비율이 높다는 사실만으로 스킬을 잘 썼다고 판단할 수는 없습니다. 충전 수·사거리·CC와 실제 사용 가능 여부는 포함하지 않습니다.</p></div></details><details class="analysis-details"><summary>내부 스킬 이름 확인하기</summary><div class="detail-body"><div class="table-wrap"><table><thead><tr><th>슬롯</th><th>리플레이 스킬 이름</th><th class="num">전체 시작</th><th class="num">대인 교전 중</th></tr></thead><tbody>${rawRows}</tbody></table></div></div></details></section>`;
}
function renderCombatHuman(){
 const p=selected(),j=p.combatJudgment,story=playstyleNarrative(p),rows=j.episodes||[],seconds=value=>value===null||value===undefined?'확인 불가':`${Number(value).toFixed(1)}초`;
 const focus=j.episodePrimaryTargetShare===null?'한 명에게 공격이 얼마나 모였는지는 기록이 부족해요.':`한 싸움에서 가장 많이 노린 한 명에게 직접 지정 공격의 <b>${pct(j.episodePrimaryTargetShare)}</b>가 모였어요.`;
 const follow=j.teamFocusFollowupRate===null?'팀과 같은 적을 노린 정도는 계산하지 못했어요.':`내 공격 앞뒤 ${data.combatJudgment.teamFocusWindowSeconds}초 안에 팀원도 같은 적을 노린 비율은 <b>${pct(j.teamFocusFollowupRate)}</b>였어요.`;
 const body=rows.map(row=>{const role=row.entryRole==='initiator'?'가장 먼저':row.entryRole==='co-initiator'?'팀과 거의 같이':'뒤이어 합류',timing=row.entryRole==='joiner'?`팀보다 ${seconds(row.joinDelaySeconds)} 뒤`:row.entryRole==='co-initiator'?'1초 안 차이':'팀 최초',target=row.primaryTargetCharacterName?`${esc(row.primaryTargetCharacterName)}를 ${fmt(row.primaryTargetActionCount)}번 직접 지정`:'직접 지정한 주대상 없음';return `<tr><td><button type="button" class="btn jump" data-combat-human-jump="${row.entryTick}">${clock(row.entryTick)}</button></td><td>${role}</td><td>${timing}</td><td>${target}</td><td class="num">${fmt(row.targetSwitchCount)}</td><td>${row.survived?'<span class="badge ok">살아 나옴</span>':'<span class="badge">사망</span>'}</td></tr>`}).join('')||'<tr><td colspan="6">플레이어 간 싸움으로 묶인 장면이 없습니다.</td></tr>';
 const coaching=sceneCoaching(),picked=(coaching?.pickedEpisodeNumbers||[]).map(number=>(coaching.episodes||[]).find(row=>row.teamEpisodeNumber===number)).filter(Boolean);
 const cards=picked.map(row=>sceneCard(row,row.entryTick)).join('')||'<div class="notice">시작 후 2분 이후의 대인 싸움이 없어 대표 장면을 고르지 않았어요.</div>';
 const mix=coaching?.feedback?.nextPlay?.headline||coachingMix();

 $('#content').innerHTML=`<section class="panel"><h2>${p.publicLabel} · 싸움 장면</h2><div class="human-lead"><div><strong>${esc(mix||story.title)}</strong><p>${story.fightStory}</p></div><span class="human-kicker">한 줄 결론 다음 장면</span></div>${cards}<div class="analysis-prose"><article class="analysis-paragraph"><h3>상대를 고른 방식</h3><p>${focus} ${follow}</p></article></div><details class="analysis-details"><summary>싸움 ${fmt(rows.length)}번을 시간순으로 확인하기</summary><div class="detail-body"><div class="table-wrap"><table><thead><tr><th>시작</th><th>들어간 방식</th><th>팀과의 차이</th><th>가장 많이 노린 적</th><th class="num">대상 변경</th><th>결과</th></tr></thead><tbody>${body}</tbody></table></div><p class="judgment-note">팀 전투 상태와 적을 직접 대상으로 삼은 기록이 이어질 때만 한 장면으로 묶었습니다. 대상 지정은 적중이나 피해를 뜻하지 않습니다.</p></div></details></section>`;
 bindSceneJumps();
 document.querySelectorAll('[data-combat-human-jump]').forEach(button=>button.onclick=()=>{state.cursor=Number(button.dataset.combatHumanJump);state.view='map';render()})
}
function renderGrowthHuman(){
 const p=selected(),g=p.growthTempo,team=data.players.filter(row=>row.teamNumber===p.teamNumber),level15=(g.levelMilestones||[]).find(row=>row.level===15)?.tick??null;
 const purpleStory=teamTimingStory(p,g.purpleBuildTick,row=>row.growthTempo.purpleBuildTick,'보라장비 완성'),levelStory=teamTimingStory(p,level15,row=>(row.growthTempo.levelMilestones||[]).find(item=>item.level===15)?.tick??null,'15레벨');
 const first=g.levelTimeline?.[0],last=g.levelTimeline?.[g.levelTimeline.length-1],growthArc=first&&last?`처음 관측된 레벨 ${fmt(first[1])}에서 마지막 ${fmt(last[1])}까지 올라갔고, 주무기 숙련도는 ${fmt(g.finalMasteryLevel)}으로 끝났어요.`:`마지막 레벨 ${fmt(g.finalLevel)}, 주무기 숙련도 ${fmt(g.finalMasteryLevel)}이 확인됐어요.`;
 const audit=g.creditCounterAudit||{},gap=Number(audit.counterBalanceGap||0),creditStory=audit.ledgerCompatible===true?`관측된 보유 크레딧 흐름과 종료 결과 누계가 맞았어요. 경기에서 ${fmt(g.totalUseCredit)} 크레딧을 사용했어요.`:`종료 결과에는 획득 ${fmt(g.totalGainCredit)}, 사용 ${fmt(g.totalUseCredit)}이 남았지만 보유 크레딧 흐름과 ${fmt(Math.abs(gap))}만큼 차이가 있어, 둘을 빼 순수익으로 해석하지 않았어요.`;
 const teamRows=team.map(row=>{const other=row.growthTempo,l15=(other.levelMilestones||[]).find(item=>item.level===15)?.tick??null;return `<tr class="${row.publicPlayerId===p.publicPlayerId?'selected':''}"><td><b>${row.publicLabel}</b> · ${esc(row.characterName)}</td><td>${other.purpleBuildTick===null?'기록 없음':clock(other.purpleBuildTick)}</td><td>${l15===null?'기록 없음':clock(l15)}</td><td class="num">${fmt(other.finalMasteryLevel)}</td><td class="num">${fmt(other.totalUseCredit)}</td></tr>`}).join('');
 const levelRows=(g.levelMilestones||[]).map(row=>`<tr><td>${fmt(row.level)}레벨</td><td>${row.tick===null?'기록 없음':`<button type="button" class="btn jump" data-growth-human-jump="${row.tick}">${clock(row.tick)}</button>`}</td></tr>`).join('');

 $('#content').innerHTML=`<section class="panel"><h2>${p.publicLabel} · 성장 흐름</h2><div class="human-lead"><div><strong>${esc(purpleStory)}</strong><p>${levelStory} ${growthArc}</p></div><span class="human-kicker">성장이 이어진 순서</span></div><div class="analysis-prose"><article class="analysis-paragraph"><h3>야생동물 동선</h3><p>${esc(sceneCoaching()?.wildlifeFlow?.story||'야생동물 동선을 이을 기록이 없어요.')}</p></article><article class="analysis-paragraph"><h3>성장이 싸움으로 이어진 때</h3><p>${esc(sceneCoaching()?.growthLink?.story||'')} ${purpleStory} 이 시각부터 다섯 부위가 모두 보라 등급 이상으로 확인됐어요.</p></article><article class="analysis-paragraph"><h3>레벨과 숙련도</h3><p>${levelStory} ${growthArc}</p></article><article class="analysis-paragraph"><h3>크레딧 사용</h3><p>${creditStory}</p></article></div><details class="analysis-details"><summary>팀원 셋의 성장 시각 확인하기</summary><div class="detail-body"><div class="table-wrap"><table><thead><tr><th>팀원</th><th>보라장비 완성</th><th>15레벨</th><th class="num">마지막 숙련도</th><th class="num">사용 크레딧</th></tr></thead><tbody>${teamRows}</tbody></table></div></div></details><details class="analysis-details"><summary>레벨별 최초 관측 시각 확인하기</summary><div class="detail-body"><div class="table-wrap"><table><thead><tr><th>레벨</th><th>처음 관측된 때</th></tr></thead><tbody>${levelRows}</tbody></table></div><p class="judgment-note">실제 레벨 달성 순간은 표시 시각보다 조금 빠를 수 있습니다. 기록 사이의 시간을 임의로 채우지는 않았습니다.</p></div></details></section>`;
 document.querySelectorAll('[data-growth-human-jump]').forEach(button=>button.onclick=()=>{state.cursor=Number(button.dataset.growthHumanJump);state.view='map';render()})
}
function renderObjectiveHuman(){
 const p=selected(),o=p.objectivePreparation,rows=o.objectives||[],before=rows.filter(row=>row.arrivalStatus==='before-active'),after=rows.filter(row=>row.arrivalStatus==='after-active'),reached=[...before,...after].sort((a,b)=>a.arrivalTick-b.arrivalTick);
 const meters=value=>value===null||value===undefined?'확인 불가':`${Number(value).toFixed(1)}m`;
 const stayed=before.filter(row=>row.activeDistanceMeters!==null&&row.activeDistanceMeters<=o.preparationRadiusMeters),moved=before.filter(row=>row.activeDistanceMeters!==null&&row.activeDistanceMeters>o.preparationRadiusMeters);
 let title='이 경기에서는 오브젝트 근처 20m 안까지 간 장면이 없었어요';
 if(before.length&&moved.length>stayed.length)title=`오브젝트 ${fmt(before.length)}곳을 미리 확인했고, 출현 전에는 다른 곳으로 움직인 장면이 더 많았어요`;
 else if(before.length&&stayed.length)title=`오브젝트 ${fmt(before.length)}곳을 미리 확인했고, 그중 ${fmt(stayed.length)}곳은 나올 때도 근처를 지켰어요`;
 else if(before.length)title=`오브젝트 ${fmt(before.length)}곳은 나오기 전에 먼저 확인했어요`;
 else if(after.length)title=`미리 간 곳은 없었고, 나온 뒤 ${fmt(after.length)}곳에 접근했어요`;
 const mean=o.meanArrivalLeadSeconds===null?'':`미리 간 곳은 평균 ${Number(o.meanArrivalLeadSeconds).toFixed(1)}초 먼저 20m 안으로 들어왔어요. `;
 const movement=moved.length?`출현 전에 들렀다가 멀어진 ${fmt(moved.length)}곳은 ‘포기’로 단정하지 않았어요. 주변을 확인하고 팀에 합류하는 정찰 동선에서도 같은 움직임이 나올 수 있기 때문이에요.`:'출현 전에 확인한 뒤 멀어진 장면은 없었어요.';
 const list=reached.length?`<div class="story-list">${reached.map(row=>{const beforeActive=row.arrivalStatus==='before-active',timing=beforeActive?`나오기 ${Math.abs(row.arrivalLeadSeconds).toFixed(1)}초 전`:`나온 뒤 ${Math.abs(row.arrivalLeadSeconds).toFixed(1)}초 후`,atActive=row.activeDistanceMeters===null?'출현 때 위치 비교 불가':row.activeDistanceMeters<=o.preparationRadiusMeters?`출현 때도 ${meters(row.activeDistanceMeters)} 안`:`출현 때는 ${meters(row.activeDistanceMeters)} 떨어짐`;return `<div class="story-row"><button type="button" class="btn jump" data-objective-human-jump="${row.arrivalTick}">${clock(row.arrivalTick)}</button><div><b>${esc(row.label)}</b><span>${timing}에 20m 안으로 접근 · ${atActive}</span></div></div>`}).join('')}</div>`:'<div class="notice">가까이 간 오브젝트가 없어 시간순 장면 목록은 만들지 않았어요.</div>';
 const tableRows=reached.map(row=>`<tr><td>${esc(row.label)}</td><td>${row.arrivalStatus==='before-active'?'출현 전':'출현 후'}</td><td class="num">${Math.abs(row.arrivalLeadSeconds).toFixed(1)}초</td><td class="num">${meters(row.activeDistanceMeters)}</td><td class="num">${meters(row.minimumObservedDistanceMeters)}</td></tr>`).join('');

 $('#content').innerHTML=`<section class="panel"><h2>${p.publicLabel} · 오브젝트 동선</h2><div class="human-lead"><div><strong>${esc(title)}</strong><p>${mean}${movement}</p></div><span class="human-kicker">언제 들렀고 언제 떠났나</span></div><h3 class="section-label">시간순으로 본 이동</h3>${list}${reached.length?`<details class="analysis-details"><summary>거리와 시간 수치 확인하기</summary><div class="detail-body"><div class="table-wrap"><table><thead><tr><th>오브젝트</th><th>접근 시점</th><th class="num">시간 차</th><th class="num">출현 때 거리</th><th class="num">가장 가까운 거리</th></tr></thead><tbody>${tableRows}</tbody></table></div><p class="judgment-note">예고 뒤 처음 20m 안으로 들어온 시각을 사용했습니다. 가까이 갔다는 사실만 확인할 수 있으며, 획득·처치 여부나 플레이어의 의도까지 뜻하지는 않습니다.</p></div></details>`:''}</section>`;
 document.querySelectorAll('[data-objective-human-jump]').forEach(button=>button.onclick=()=>{state.cursor=Number(button.dataset.objectiveHumanJump);state.view='map';render()})
}
function renderDeathHuman(){state.view='map';state.deathsOpen=true;document.querySelectorAll('[data-view]').forEach(b=>b.classList.toggle('active',b.dataset.view==='map'));renderMap();const host=$('#mapDeaths');if(host)host.scrollIntoView({block:'nearest'})}
function render(){stop();renderOptions();document.querySelectorAll('[data-view]').forEach(b=>b.classList.toggle('active',b.dataset.view===state.view));if(state.view==='overview')renderOverviewHuman();else if(state.view==='judgment')renderCombatHuman();else if(state.view==='deathreview')renderDeathHuman();else if(state.view==='growth')renderGrowthHuman();else if(state.view==='objectives')renderObjectiveHuman();else if(state.view==='skillop')renderSkillOperation();else if(state.view==='map')renderMap();else if(state.view==='pings')renderPings();else if(state.view==='skills')renderSkillsHuman();else renderPrivacy()}
document.querySelectorAll('[data-view]').forEach(b=>b.onclick=()=>{state.view=b.dataset.view;render()});render();
</script></body></html>'''


def render_public_html(catalog: dict[str, Any]) -> str:
    assert_public_catalog(catalog)
    compact = json.dumps(catalog, ensure_ascii=False, separators=(",", ":")).replace(
        "</", "<\\/"
    )
    return (
        HTML_TEMPLATE.replace("__REPORT_ID__", catalog["meta"]["reportId"])
        .replace("__CLIENT_VERSION__", catalog["meta"]["clientVersion"])
        .replace("__MATCH_MODE__", catalog["meta"]["matchModeLabel"])
        .replace("__DATA__", compact)
    )


def write_public_analysis(
    private_catalog: dict[str, Any],
    field_report: dict[str, Any],
    out_dir: Path,
    *,
    report_id: str | None = None,
    rendered_output: dict[str, Any] | None = None,
) -> dict[str, Any]:
    catalog = build_public_catalog(
        private_catalog,
        field_report,
        report_id=report_id,
    )
    out_dir.mkdir(parents=True, exist_ok=True)
    json_bytes = json.dumps(catalog, ensure_ascii=False, separators=(",", ":")).encode(
        "utf-8"
    )
    html = render_public_html(catalog)
    (out_dir / "combat-analysis.public.json").write_bytes(json_bytes)
    (out_dir / "combat-analysis.public.html").write_bytes(html.encode("utf-8"))
    if rendered_output is not None:
        rendered_output["jsonBytes"] = json_bytes
        rendered_output["html"] = html
    return catalog


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Build a public anonymous combat report")
    parser.add_argument("--private-json", required=True, type=Path)
    parser.add_argument("--delta-fields", required=True, type=Path)
    parser.add_argument("--spawn-snapshots", type=Path)
    parser.add_argument("--out-dir", required=True, type=Path)
    parser.add_argument("--report-id", default=None)
    args = parser.parse_args(argv)
    private_catalog = json.loads(args.private_json.read_text(encoding="utf-8"))
    field_report = json.loads(args.delta_fields.read_text(encoding="utf-8"))
    if args.spawn_snapshots is not None:
        spawn_payload = args.spawn_snapshots.read_bytes()
        private_catalog = merge_retained_bori_supply_box_evidence(
            private_catalog,
            json.loads(spawn_payload.decode("utf-8")),
            spawn_report_sha256=hashlib.sha256(spawn_payload).hexdigest(),
        )
    catalog = write_public_analysis(
        private_catalog,
        field_report,
        args.out_dir.resolve(),
        report_id=args.report_id,
    )
    print(json.dumps({"status": "ok", "reportId": catalog["meta"]["reportId"]}))


if __name__ == "__main__":
    main()
