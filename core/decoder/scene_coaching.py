"""Scene-first coaching derived only from already-verified replay fields.

This module does not invent hit rate, intent, or missing HP/cooldown values.
Positions are last held movement anchors. Snapshot HP is the last full-snapshot
value held to the query tick, not a per-tick current HP packet.
"""

from __future__ import annotations

from collections import defaultdict
import math


FPS = 60
NEARBY_METERS = 30
SUPPORT_METERS = 20
ISOLATED_METERS = 40
SUPPORT_JOIN_SECONDS = 3.0
ISOLATED_JOIN_SECONDS = 4.0
HP_LOW_RATIO = 0.35
WILDLIFE_VISIT_METERS = 20
IDLE_GAP_SECONDS = 45
SIMULTANEOUS_SECONDS = 0.25
CONTEXT_SECONDS = 5
EARLY_COMBAT_SECONDS = 120

FAMILY_KO = {
    "Active1": "Q",
    "Active2": "W",
    "Active3": "E",
    "Active4": "R",
    "WeaponSkill": "무기 스킬",
    "TacticalSkill": "전술 스킬",
}
ACTIVE_FAMILIES = ("Active1", "Active2", "Active3", "Active4")
COOLDOWN_FAMILIES = (*ACTIVE_FAMILIES, "WeaponSkill", "TacticalSkill")

VERDICT_LABELS = {
    "near-simultaneous-normal": "팀과 거의 동시에 들어간 정상 교전",
    "supported-first-entry": "팀이 바로 합류할 수 있어 성립한 선진입",
    "calculated-low-hp-isolated-enemy": "체력이 낮았지만 적도 고립돼 있어 계산 가능한 진입",
    "unsupported-isolated-entry": "팀원이 멀고 지원받기 어려운 고립 진입",
    "late-after-teammate-down": "이미 팀원이 다운된 뒤 들어가 결과를 뒤집기 어려웠던 후속 진입",
    "insufficient-evidence": "자료만으로 정당성을 가르기 어려움",
}
ALLOWED_VERDICTS = frozenset(VERDICT_LABELS)
ALLOWED_CONFIDENCE = frozenset(
    {"confirmed", "observed", "interpreted", "unavailable"}
)


def _held_row(rows: list, tick: int):
    # Timelines are sorted; select the last sample at or before tick.
    left, right = 0, len(rows or [])
    while left < right:
        middle = (left + right) // 2
        if rows[middle][0] <= tick:
            left = middle + 1
        else:
            right = middle
    return rows[left - 1] if left else None


def held_position(player: dict, tick: int):
    row = _held_row(player.get("movementTrack") or [], tick)
    if row is None:
        return None
    return [row[1], row[2]]


def life_state_at(player: dict, tick: int) -> str | None:
    state = None
    for row in player.get("lifeTimeline") or []:
        if row[0] > tick:
            break
        state = row[1]
    return state


def snapshot_at(player: dict, tick: int):
    row = _held_row(player.get("snapshotSeries") or [], tick)
    if row is None:
        return None
    hp, all_shield, normal_shield, skill_shield, level, alive = row[1:7]
    shield = None
    if all(isinstance(value, int) for value in (all_shield, normal_shield, skill_shield)):
        shield = all_shield + normal_shield + skill_shield
    peak = None
    for item in player.get("snapshotSeries") or []:
        if item[0] > tick:
            break
        if isinstance(item[1], (int, float)):
            peak = item[1] if peak is None else max(peak, item[1])
    ratio = None
    band = "unavailable"
    if isinstance(hp, (int, float)) and isinstance(peak, (int, float)) and peak > 0:
        ratio = round(hp / peak, 4)
        if ratio <= HP_LOW_RATIO:
            band = "low-observed"
        elif ratio >= 0.75:
            band = "healthy-observed"
        else:
            band = "mid-observed"
    elif isinstance(hp, (int, float)):
        band = "observed-without-peak"
    return {
        "tick": row[0],
        "hp": hp if isinstance(hp, (int, float)) else None,
        "shield": shield,
        "level": level if isinstance(level, int) else None,
        "aliveFlag": alive,
        "peakObservedHp": peak,
        "hpRatioToPeak": ratio,
        "hpBand": band,
        "status": "derived-held-last-full-snapshot",
    }


def remaining_hundredths(state: dict, tick: int) -> float | None:
    if state.get("kind") != "known":
        return None
    if state.get("held"):
        return float(state["remaining"])
    return max(0.0, state["remaining"] - (tick - state["tick"]) * 100 / FPS)


def cooldown_states_at(timeline: list, tick: int) -> dict[str, dict]:
    states = {family: {"kind": "unobserved"} for family in COOLDOWN_FAMILIES}
    for event in timeline or []:
        if event[0] > tick:
            break
        event_tick, action, family, remaining, maximum, stack, detail = (
            event + [None] * 7
        )[:7]
        if action == "clear":
            for active in ACTIVE_FAMILIES:
                states[active] = {
                    "kind": "known",
                    "tick": event_tick,
                    "remaining": 0,
                    "max": 0,
                    "stack": None,
                    "held": False,
                }
            continue
        if family not in states:
            continue
        if action == "set":
            if isinstance(remaining, int) and remaining >= 0:
                states[family] = {
                    "kind": "known",
                    "tick": event_tick,
                    "remaining": remaining,
                    "max": maximum if isinstance(maximum, int) else None,
                    "stack": stack if isinstance(stack, int) else None,
                    "held": False,
                }
            else:
                states[family] = {"kind": "unknown"}
        elif action == "copy":
            source = states.get(detail) if isinstance(detail, str) else None
            copied = remaining_hundredths(source or {}, event_tick)
            if copied is None:
                states[family] = {"kind": "unknown"}
            else:
                states[family] = {
                    **source,
                    "tick": event_tick,
                    "remaining": copied,
                    "source": "copy",
                }
        elif action == "hold":
            source = states.get(family) or {}
            held = remaining_hundredths(source, event_tick)
            if held is not None:
                states[family] = {
                    **source,
                    "tick": event_tick,
                    "remaining": held,
                    "held": bool(detail),
                }
    return states


def movement_families(player: dict) -> list[str]:
    families = []
    seen = set()
    for row in player.get("characterCapabilities", {}).get("movementSkills") or []:
        family = row.get("family")
        if family in COOLDOWN_FAMILIES and family not in seen:
            seen.add(family)
            families.append(family)
    return families


def cooldown_view(player: dict, tick: int) -> dict:
    states = cooldown_states_at(player.get("skillCooldownTimeline") or [], tick)
    slots = {}
    for family in COOLDOWN_FAMILIES:
        state = states[family]
        remaining = remaining_hundredths(state, tick)
        if state.get("kind") == "unobserved":
            readiness = "unobserved"
        elif state.get("kind") == "unknown" or remaining is None:
            readiness = "unknown"
        elif remaining > 0:
            readiness = "cooldown"
        else:
            readiness = "ready"
        slots[family] = {
            "family": family,
            "label": FAMILY_KO[family],
            "readiness": readiness,
            "remainingSeconds": (
                round(remaining / 100, 2) if remaining is not None else None
            ),
        }
    moving = movement_families(player)
    observed_moving = [
        slots[family] for family in moving if slots[family]["readiness"] != "unobserved"
    ]
    movement_ready = None
    if observed_moving:
        movement_ready = any(row["readiness"] == "ready" for row in observed_moving)
    return {
        "slots": slots,
        "movementFamilies": moving,
        "movementReady": movement_ready,
        "staticMovementFamilyCount": len(moving),
        "staticDefensiveStateCount": len(
            player.get("characterCapabilities", {}).get("defensiveStateTypes") or []
        ),
    }


def meters_between(left, right) -> float | None:
    if left is None or right is None:
        return None
    return round(math.hypot(left[0] - right[0], left[1] - right[1]), 2)


def nearby_players(focus: dict, others: list[dict], tick: int, radius: int):
    origin = held_position(focus, tick)
    rows = []
    if origin is None:
        return None, rows
    for other in others:
        if other is focus or other.get("objectId") == focus.get("objectId"):
            continue
        position = held_position(other, tick)
        distance = meters_between(origin, position)
        if distance is None:
            continue
        rows.append({
            "objectId": other["objectId"],
            "characterName": other["characterName"],
            "teamNumber": other["teamNumber"],
            "lifeState": life_state_at(other, tick) or "alive",
            "distanceMeters": distance,
            "snapshot": snapshot_at(other, tick),
        })
    nearby = [row for row in rows if row["distanceMeters"] <= radius]
    return origin, nearby


def classify_entry_verdict(context: dict) -> tuple[str, str, list[str], str | None]:
    """Return verdict id, confidence, evidence sentences, alternative."""

    evidence: list[str] = []
    alternative = None
    position = context.get("selfPosition")
    if position is None:
        return (
            "insufficient-evidence",
            "unavailable",
            ["진입 시점의 본인 위치 앵커가 없어 합류 거리를 비교하지 않았어요."],
            None,
        )

    closest = context.get("closestAliveTeammateMeters")
    join_delay = context.get("joinDelaySeconds")
    reinforce = context.get("reinforcementDelaySeconds")
    role = context.get("entryRole")
    teammate_down = bool(context.get("teammateDownOrDeadAtEntry"))
    nearby_allies = context.get("nearbyAliveAllyCount")
    nearby_enemies = context.get("nearbyAliveEnemyCount")
    isolated_enemy = bool(context.get("isolatedNearbyEnemy"))
    hp_band = context.get("hpBand")
    movement_ready = context.get("movementReady")
    hp = context.get("hp")
    shield = context.get("shield")

    if closest is None:
        evidence.append("가장 가까운 생존 팀원 거리는 위치 기록이 부족해 비교하지 못했어요.")
    else:
        evidence.append(f"가장 가까운 생존 팀원은 {closest:.1f}m 떨어져 있었어요.")
    if role == "joiner" and isinstance(join_delay, (int, float)):
        evidence.append(f"팀 최초 진입보다 {join_delay:.1f}초 뒤에 합류했어요.")
    if nearby_allies is not None and nearby_enemies is not None:
        evidence.append(
            f"{NEARBY_METERS}m 안에서 생존 팀원 {nearby_allies}명, 적 {nearby_enemies}명이었어요."
        )
    if movement_ready is True:
        evidence.append("정적 이동기 슬롯은 진입 때 준비 상태로 관측됐어요.")
    elif movement_ready is False:
        evidence.append("정적 이동기 슬롯은 진입 때 쿨다운이었어요.")
    else:
        evidence.append("이동기의 준비 여부는 그 시점 쿨다운 패킷이 없어 미확인이에요.")
    if isinstance(hp, (int, float)):
        shield_text = f", 보호막 {shield}" if isinstance(shield, int) else ""
        evidence.append(f"진입 직전 마지막 스냅샷 HP는 {int(hp)}{shield_text}이었어요.")
    else:
        evidence.append("진입 직전 스냅샷 HP는 확인되지 않았어요.")
    if reinforce is not None:
        evidence.append(f"다른 팀원이 전투 상태로 들어온 시간은 {reinforce:.1f}초 뒤였어요.")
    elif role in {"initiator", "co-initiator"}:
        evidence.append("이 싸움에서 다른 팀원이 전투 상태로 들어온 기록은 없었어요.")

    isolated_self = (
        closest is None or closest > ISOLATED_METERS
    ) and (
        reinforce is None or reinforce > ISOLATED_JOIN_SECONDS
    )
    supported = (
        (closest is not None and closest <= SUPPORT_METERS)
        or (reinforce is not None and reinforce <= SUPPORT_JOIN_SECONDS)
        or (nearby_allies is not None and nearby_allies >= 1)
    )

    if teammate_down and role == "joiner":
        alternative = "쓰러진 팀원을 건지려 한 움직임일 수는 있지만, 의도까지 확정하지는 않았어요."
        return "late-after-teammate-down", "observed", evidence[:4], alternative

    if role == "co-initiator":
        if isolated_self:
            alternative = "시간은 거의 같았지만, 살아 있는 팀원과의 거리는 멀었어요."
        return "near-simultaneous-normal", "observed", evidence[:4], alternative

    if isolated_self and role in {"initiator", "joiner"}:
        alternative = "적을 먼저 끊으려 한 선택일 수는 있지만, 당시 거리만 보면 지원이 늦었어요."
        return "unsupported-isolated-entry", "interpreted", evidence[:4], alternative

    if (
        hp_band == "low-observed"
        and isolated_enemy
        and supported
        and nearby_enemies == 1
    ):
        alternative = "체력이 낮아 위험을 감수하는 진입으로 볼 여지도 있어요."
        return (
            "calculated-low-hp-isolated-enemy",
            "interpreted",
            evidence[:4],
            alternative,
        )

    if role == "initiator" and supported:
        return "supported-first-entry", "observed", evidence[:4], None

    if role == "joiner" and isinstance(join_delay, (int, float)):
        if join_delay <= SIMULTANEOUS_SECONDS:
            return "near-simultaneous-normal", "observed", evidence[:4], None
        return "insufficient-evidence", "unavailable", evidence[:4], (
            "팀 싸움이 열린 뒤 합류한 장면이에요. 거리상 고립은 아니지만, "
            "그 합류가 이득이었는지는 결과만으로 가르지 않았어요."
        )

    return "insufficient-evidence", "unavailable", evidence[:4], (
        "선진입이 맞았는지 가르려면 거리·체력·합류 시각이 더 맞물려야 해요."
    )


def _enemy_isolated(focus: dict, players: list[dict], tick: int, nearby: list[dict]) -> bool:
    enemies = [
        row for row in nearby
        if row["teamNumber"] != focus["teamNumber"] and row["lifeState"] == "alive"
    ]
    if len(enemies) != 1:
        return False
    target = next(
        player for player in players if player["objectId"] == enemies[0]["objectId"]
    )
    _, around_target = nearby_players(target, players, tick, NEARBY_METERS)
    if around_target is None:
        return False
    ally_of_target = [
        row for row in around_target
        if row["teamNumber"] == target["teamNumber"]
        and row["lifeState"] == "alive"
        and row["objectId"] != target["objectId"]
    ]
    return not ally_of_target


def build_episode_scene(player: dict, players: list[dict], episode: dict) -> dict:
    tick = episode["entryTick"]
    origin, nearby = nearby_players(player, players, tick, NEARBY_METERS)
    snapshot = snapshot_at(player, tick)
    life = life_state_at(player, tick)
    if snapshot is not None and (
        life in {"down", "dead"}
        or snapshot.get("hp") == 0
    ):
        snapshot = {
            **snapshot,
            "hp": None,
            "hpRatioToPeak": None,
            "hpBand": "unavailable",
        }
    cooldown = cooldown_view(player, tick)
    teammates = [
        other for other in players
        if other["teamNumber"] == player["teamNumber"]
        and other is not player
    ]
    alive_teammate_distances = []
    teammate_down = False
    for mate in teammates:
        state = life_state_at(mate, tick)
        if state in {"down", "dead"}:
            teammate_down = True
        if state == "alive":
            distance = meters_between(origin, held_position(mate, tick))
            if distance is not None:
                alive_teammate_distances.append(distance)
    nearby_alive_allies = [
        row for row in (nearby or [])
        if row["teamNumber"] == player["teamNumber"] and row["lifeState"] == "alive"
    ]
    nearby_alive_enemies = [
        row for row in (nearby or [])
        if row["teamNumber"] != player["teamNumber"] and row["lifeState"] == "alive"
    ]
    isolated_enemy = (
        False if origin is None else _enemy_isolated(player, players, tick, nearby or [])
    )
    context = {
        "selfPosition": origin,
        "closestAliveTeammateMeters": (
            min(alive_teammate_distances) if alive_teammate_distances else None
        ),
        "joinDelaySeconds": episode.get("joinDelaySeconds"),
        "reinforcementDelaySeconds": episode.get("reinforcementDelaySeconds"),
        "entryRole": episode.get("entryRole"),
        "teammateDownOrDeadAtEntry": teammate_down,
        "nearbyAliveAllyCount": None if origin is None else len(nearby_alive_allies),
        "nearbyAliveEnemyCount": None if origin is None else len(nearby_alive_enemies),
        "isolatedNearbyEnemy": isolated_enemy,
        "hpBand": None if snapshot is None else snapshot["hpBand"],
        "hp": None if snapshot is None else snapshot["hp"],
        "shield": None if snapshot is None else snapshot["shield"],
        "movementReady": cooldown["movementReady"],
    }
    verdict, confidence, evidence, alternative = classify_entry_verdict(context)
    before = snapshot_at(player, tick - CONTEXT_SECONDS * FPS)
    after = snapshot_at(player, tick + CONTEXT_SECONDS * FPS)
    target = episode.get("primaryTargetCharacterName")
    follow = episode.get("teamFocusFollowupCount") or 0
    switches = episode.get("targetSwitchCount") or 0
    if target:
        target_line = f"주표적은 {target}이었어요."
        if switches:
            target_line += f" 중간에 대상을 {switches}번 바꿨어요."
        else:
            target_line += " 중간에 다른 대상으로 바꾸지는 않았어요."
        if follow:
            target_line += f" 같은 대상을 팀원이 따라 지정한 기록은 {follow}번이었어요."
        evidence = [*evidence, target_line][:4]
    timeline = [
        {
            "key": "pre",
            "label": "교전 직전",
            "tick": max(0, tick - CONTEXT_SECONDS * FPS),
            "hp": None if before is None else before["hp"],
        },
        {"key": "entry", "label": "진입", "tick": tick, "hp": None if snapshot is None else snapshot["hp"]},
        {
            "key": "join",
            "label": "팀 합류",
            "tick": (
                tick
                if episode.get("entryRole") == "co-initiator"
                else None
                if episode.get("reinforcementDelaySeconds") is None
                and episode.get("entryRole") != "joiner"
                else tick + int(round((episode.get("joinDelaySeconds") or 0) * FPS))
                if episode.get("entryRole") == "joiner"
                else tick + int(round((episode.get("reinforcementDelaySeconds") or 0) * FPS))
            ),
        },
        {
            "key": "result",
            "label": "살아 나옴" if episode.get("survived") else "사망으로 끝남",
            "tick": episode["endTick"] if episode.get("survived") else episode["endTick"],
        },
    ]
    return {
        "teamEpisodeNumber": episode["teamEpisodeNumber"],
        "entryTick": tick,
        "startTick": episode["startTick"],
        "endTick": episode["endTick"],
        "verdictId": verdict,
        "headline": VERDICT_LABELS[verdict],
        "confidence": confidence,
        "evidence": evidence,
        "alternative": alternative,
        "timeline": timeline,
        "entryRole": episode.get("entryRole"),
        "survived": bool(episode.get("survived")),
        "primaryTargetCharacterName": target,
        "targetSwitchCount": switches,
        "teamFocusFollowupCount": follow,
        "closestAliveTeammateMeters": context["closestAliveTeammateMeters"],
        "nearbyAliveAllyCount": context["nearbyAliveAllyCount"],
        "nearbyAliveEnemyCount": context["nearbyAliveEnemyCount"],
        "hp": context["hp"],
        "shield": context["shield"],
        "hpBand": context["hpBand"],
        "movementReady": cooldown["movementReady"],
        "movementFamilies": cooldown["movementFamilies"],
        "cooldownSlots": {
            family: {
                "readiness": row["readiness"],
                "remainingSeconds": row["remainingSeconds"],
                "label": row["label"],
            }
            for family, row in cooldown["slots"].items()
        },
        "snapshotTick": None if snapshot is None else snapshot["tick"],
        "afterSnapshotHp": None if after is None else after["hp"],
        "fallbackUsed": False,
    }


def _link_episode(player: dict, tick: int) -> dict | None:
    matches = [
        row for row in player["combatJudgment"]["personalEpisodes"]
        if row["startTick"] <= tick <= row["endTick"]
    ]
    if not matches:
        return None
    return min(matches, key=lambda row: abs(row["entryTick"] - tick))


def combat_interval_start(player: dict, tick: int) -> int | None:
    start = None
    for row in player.get("combatIntervals") or []:
        begin, end = row[0], row[1]
        if begin <= tick <= end:
            if start is None or begin > start:
                start = begin
    return start


def death_headline(episode_scene: dict | None, risks: list[str], *, down_missing: bool) -> str:
    isolated = "퇴로·합류 거리" in risks
    entry_id = None if episode_scene is None else episode_scene["verdictId"]
    if episode_scene is None:
        return "교전 시작 기록을 잇지 못한 사망"
    if down_missing:
        return "다운 시각이 확인되지 않은 사망"
    if entry_id == "late-after-teammate-down":
        return VERDICT_LABELS["late-after-teammate-down"]
    if entry_id == "calculated-low-hp-isolated-enemy":
        return VERDICT_LABELS["calculated-low-hp-isolated-enemy"]
    if isolated and entry_id == "supported-first-entry":
        return "들어가서는 팀이 있었지만, 쓰러질 때는 떨어진 장면"
    if isolated:
        return "팀과 떨어진 채 쓰러진 장면"
    return episode_scene["headline"]


def build_death_scene(player: dict, death: dict, episode_scene: dict | None) -> dict:
    flags = death.get("feedbackFlags") or []
    focus = (
        death["downTick"]
        if death.get("downTick") is not None
        else death["focusTick"]
    )
    cooldown = cooldown_view(player, focus)
    snapshot = snapshot_at(player, focus)
    risks = []
    if "local-number-disadvantage" in flags:
        risks.append("고립·수적 열세")
    if death.get("closestAliveTeammateMeters") is not None and death["closestAliveTeammateMeters"] > NEARBY_METERS:
        risks.append("퇴로·합류 거리")
    if "multi-enemy-target-selection-pressure" in flags:
        risks.append("여러 적의 집중")
    if "crowd-control-observed-before-down" in flags:
        risks.append("다운 직전 CC")
    if "team-collapse-followed-within-window" in flags:
        risks.append("같은 시간대 팀 붕괴")
    if snapshot and snapshot["hpBand"] == "low-observed":
        risks.append("관측 HP 열세")
    if cooldown["movementReady"] is False:
        risks.append("이동기 쿨다운")
    responses = []
    if (
        death.get("closestAliveTeammateMeters") is not None
        and death["closestAliveTeammateMeters"] > SUPPORT_METERS
    ):
        responses.append("팀과 거리를 좁힌 뒤 들어가는 선택")
    if episode_scene and episode_scene["verdictId"] == "unsupported-isolated-entry":
        responses.append("진입을 늦추는 선택")
    if cooldown["movementReady"] is False or (
        episode_scene and episode_scene.get("movementReady") is False
    ):
        responses.append("이동기가 돌아온 뒤 움직이는 선택")
    alternatives = []
    if episode_scene and episode_scene.get("nearbyAliveEnemyCount") == 1:
        alternatives.append("적 한 명이 떨어져 있어 기회를 본 장면일 수도 있어요.")
    alternatives.append("정확한 막타 스킬과 플레이어의 의도는 이 자료만으로 확정하지 않았어요.")
    if death.get("recordedDamageStatus") == "partial-non-null-CmdDamage-only" and not death.get("recordedDamageTaken"):
        confidence = "unavailable"
        damage_note = "피해 수치가 비어 있어 막타와 피해 귀속은 확인 불가예요."
    else:
        confidence = "observed" if risks else "unavailable"
        damage_note = None
    extra = []
    closest = death.get("closestAliveTeammateMeters")
    if closest is None:
        extra.append("쓰러질 때 가까운 팀원 거리는 위치 기록이 부족해 비교하지 못했어요.")
    else:
        extra.append(f"쓰러질 때 가장 가까운 생존 팀원은 {closest:.1f}m 떨어져 있었어요.")
    ally = death.get("nearbyAliveAllyCount")
    enemy = death.get("nearbyAliveEnemyCount")
    if ally is not None and enemy is not None:
        extra.append(f"당시 {NEARBY_METERS}m 안 생존 팀원 {ally}명, 적 {enemy}명이었어요.")
    if cooldown["movementReady"] is True:
        extra.append("다운 직전 정적 이동기 슬롯은 준비 상태로 관측됐어요.")
    elif cooldown["movementReady"] is False:
        extra.append("다운 직전 정적 이동기 슬롯은 쿨다운이었어요.")
    else:
        extra.append("다운 직전 이동기 준비 여부는 그 시점 쿨다운 패킷이 없어 미확인이에요.")
    if death.get("downTick") is None:
        extra.append("다운 시각은 이 사망에 연결되지 않아 표시하지 않았어요.")
    if episode_scene is not None:
        extra.append(f"이 싸움에 들어갈 때의 판단은 “{episode_scene['headline']}”이었어요.")
    if damage_note:
        extra.append(damage_note)
    interval_start = combat_interval_start(player, focus)
    if interval_start is not None:
        combat_start = interval_start
        combat_status = "exact-player-combat-interval"
    elif episode_scene is not None:
        combat_start = episode_scene["startTick"]
        combat_status = "exact-linked-team-episode"
    else:
        combat_start = None
        combat_status = "unavailable-no-linked-combat-episode"
    return {
        "deathNumber": death["deathNumber"],
        "combatStartTick": combat_start,
        "combatStartStatus": combat_status,
        "entryTick": None if episode_scene is None else episode_scene["entryTick"],
        "downTick": death.get("downTick"),
        "deathTick": death["deathTick"],
        "focusTick": death["focusTick"],
        "headline": death_headline(
            episode_scene,
            risks,
            down_missing=death.get("downTick") is None,
        ),
        "verdictId": (
            episode_scene["verdictId"]
            if episode_scene is not None
            else "insufficient-evidence"
        ),
        "confidence": confidence,
        "evidence": extra[:4],
        "riskSignals": risks,
        "responses": responses,
        "alternative": " ".join(alternatives),
        "finisherCharacterName": death.get("finisherCharacterName"),
        "cooldownSlots": {
            family: {
                "readiness": row["readiness"],
                "remainingSeconds": row["remainingSeconds"],
                "label": row["label"],
            }
            for family, row in cooldown["slots"].items()
        },
        "movementReady": cooldown["movementReady"],
        "hp": None if snapshot is None else snapshot["hp"],
        "hpBand": None if snapshot is None else snapshot["hpBand"],
        "linkedEpisodeNumber": (
            None if episode_scene is None else episode_scene["teamEpisodeNumber"]
        ),
        "fallbackUsed": False,
    }


def _phase_label(phase_clock: dict | None, tick: int) -> str | None:
    if not phase_clock:
        return None
    found = None
    for row in phase_clock.get("restrictionUpdates") or []:
        if row.get("tick", 0) > tick:
            break
        found = row
    if not found:
        return None
    light = {"Day": "낮", "Night": "밤"}.get(found.get("dayNightName"), found.get("dayNightName"))
    day = found.get("day")
    if day is None or not light:
        return None
    return f"{day}일차 {light}"


def wildlife_flow(player: dict, wildlife: list[dict], *, first_tick: int, phase_clock=None) -> dict:
    if not wildlife:
        return {
            "status": "derived-exact-wildlife-proximity-and-killer-packet-v1",
            "fallbackUsed": False,
            "visitCount": 0,
            "exactKillCount": 0,
            "nearbyDeathCount": 0,
            "camps": [],
            "idleGaps": [],
            "story": "야생동물 기록이 없어 파밍 동선을 잇지 않았어요.",
        }
    groups: dict[tuple, dict] = {}
    grid: dict[tuple[int, int], list[tuple]] = defaultdict(list)
    exact_kills = 0
    nearby_deaths = 0
    for animal in wildlife:
        if animal.get("monsterCode") == 11:
            continue
        if animal.get("killerPlayerObjectId") == player["objectId"]:
            exact_kills += 1
        track = animal.get("mapPositionTrack") or animal.get("displayPositionTrack") or []
        if not track:
            continue
        center = [track[0][1], track[0][2]]
        key = (
            animal.get("displayGroupObjectId")
            if animal.get("displayGroupObjectId") is not None
            else ("single", animal.get("objectId"))
        )
        group = groups.setdefault(key, {
            "label": (
                str(animal.get("monsterName") or "야생동물")
                if not animal.get("mutated")
                or str(animal.get("monsterName") or "").startswith("변이")
                else "변이 " + str(animal.get("monsterName") or "야생동물")
            ),
            "center": center,
            "spawnTick": animal.get("spawnTick"),
            "deathTick": animal.get("deathTick"),
            "firstApproachTick": None,
            "exactKill": False,
            "nearbyDeath": False,
        })
        group["spawnTick"] = min(
            group["spawnTick"], animal.get("spawnTick")
        ) if group["spawnTick"] is not None and animal.get("spawnTick") is not None else (
            group["spawnTick"] or animal.get("spawnTick")
        )
        if animal.get("killerPlayerObjectId") == player["objectId"]:
            group["exactKill"] = True
        cell = (math.floor(center[0] / WILDLIFE_VISIT_METERS), math.floor(center[1] / WILDLIFE_VISIT_METERS))
        grid[cell].append(key)

    pid = player["objectId"]
    combat = player.get("combatIntervals") or []

    def in_combat(tick: int) -> bool:
        return any(start <= tick < end for start, end, *_ in combat)

    for anchor in player.get("movementTrack") or []:
        tick, x, z = anchor[0], anchor[1], anchor[2]
        cell = (
            math.floor(x / WILDLIFE_VISIT_METERS),
            math.floor(z / WILDLIFE_VISIT_METERS),
        )
        keys = []
        for dx in (-1, 0, 1):
            for dz in (-1, 0, 1):
                keys.extend(grid.get((cell[0] + dx, cell[1] + dz), []))
        for key in keys:
            group = groups[key]
            spawn = group["spawnTick"]
            death = group["deathTick"]
            if spawn is not None and tick < spawn:
                continue
            if death is not None and tick >= death:
                # This branch can only set nearbyDeath once and only through
                # the inclusive two-second window. Keep the same distance
                # calculation at the boundary; never approximate coordinates.
                if group["nearbyDeath"] or tick > death + 2 * FPS:
                    continue
                distance = meters_between([x, z], group["center"])
                if (
                    distance is not None
                    and distance <= WILDLIFE_VISIT_METERS
                    and death - 2 * FPS <= tick <= death + 2 * FPS
                ):
                    if not group["nearbyDeath"]:
                        group["nearbyDeath"] = True
                        nearby_deaths += 1
                continue
            if group["firstApproachTick"] is not None:
                continue
            distance = meters_between([x, z], group["center"])
            if distance is not None and distance <= WILDLIFE_VISIT_METERS:
                if group["firstApproachTick"] is None:
                    group["firstApproachTick"] = tick

    visits = sorted(
        (
            {
                "label": group["label"],
                "firstApproachTick": group["firstApproachTick"],
                "exactKill": group["exactKill"],
                "nearbyDeath": group["nearbyDeath"],
            }
            for group in groups.values()
            if group["firstApproachTick"] is not None
        ),
        key=lambda row: row["firstApproachTick"],
    )
    idle = []
    for left, right in zip(visits, visits[1:]):
        gap = (right["firstApproachTick"] - left["firstApproachTick"]) / FPS
        if gap < IDLE_GAP_SECONDS:
            continue
        mid = (left["firstApproachTick"] + right["firstApproachTick"]) // 2
        idle.append({
            "fromTick": left["firstApproachTick"],
            "toTick": right["firstApproachTick"],
            "seconds": round(gap, 1),
            "combatDuringGap": in_combat(mid),
        })
    if not visits:
        story = "야생동물 캠프 20m 안에 들어간 기록이 없어 파밍 동선은 따로 잇지 않았어요."
        if exact_kills:
            story += f" exact 처치 패킷으로 이 플레이어에게 연결된 야생동물은 {exact_kills}마리예요."
        else:
            story += " 죽은 야생동물 근처에 있었다는 사실만으로 처치 주체는 단정하지 않았어요."
    else:
        labels = []
        for row in visits:
            if row["label"] not in labels:
                labels.append(row["label"])
        phase = _phase_label(phase_clock, visits[0]["firstApproachTick"])
        lead = f"{phase}에 " if phase else ""
        route = " → ".join(labels[:4])
        if len(labels) > 4:
            route += f" 등 {len(labels)}곳"
        story = f"{lead}{route} 캠프 20m 안까지 들어가는 동선이 관측됐어요."
        empty = [row for row in idle if not row["combatDuringGap"]]
        if empty:
            story += (
                f" 전투 없이 {empty[0]['seconds']:.0f}초 이상 빈 구간도 있어, "
                "그 시간은 성장으로 쓴 흐름으로 읽었어요."
            )
        if exact_kills:
            story += f" exact 처치 패킷으로 이 플레이어에게 연결된 야생동물은 {exact_kills}마리예요."
        else:
            story += " 죽은 야생동물 근처에 있었다는 사실만으로 처치 주체는 단정하지 않았어요."
        if nearby_deaths and not exact_kills:
            story += f" 죽을 때 20m 안에 있었던 경우는 {nearby_deaths}번 관측됐어요."
    return {
        "status": "derived-exact-wildlife-proximity-and-killer-packet-v1",
        "fallbackUsed": False,
        "visitCount": len(visits),
        "exactKillCount": exact_kills,
        "nearbyDeathCount": nearby_deaths,
        "camps": visits[:12],
        "idleGaps": idle[:8],
        "story": story,
        "playerObjectBound": pid,
    }


def growth_link(player: dict) -> dict:
    growth = player.get("growthTempo") or {}
    purple = growth.get("purpleBuildTick")
    level15 = next(
        (
            row["tick"]
            for row in growth.get("levelMilestones") or []
            if row["level"] == 15
        ),
        None,
    )
    episodes = player["combatJudgment"]["personalEpisodes"]

    def after(tick):
        if tick is None:
            return None
        return next((row for row in episodes if row["entryTick"] >= tick), None)

    purple_fight = after(purple)
    level_fight = after(level15)
    parts = []
    if purple is not None and purple_fight is not None:
        delay = round((purple_fight["entryTick"] - purple) / FPS, 1)
        parts.append(
            f"보라장비 완성 뒤 {delay:.0f}초에 다음 대인 싸움 진입이 관측됐어요."
        )
    elif purple is not None:
        parts.append("보라장비는 완성됐지만, 그 뒤 대인 싸움 진입은 이어지지 않았어요.")
    if level15 is not None and level_fight is not None:
        delay = round((level_fight["entryTick"] - level15) / FPS, 1)
        parts.append(f"15레벨 이후 다음 싸움까지 {delay:.0f}초가 있었어요.")
    return {
        "purpleBuildTick": purple,
        "level15Tick": level15,
        "nextFightAfterPurpleTick": (
            None if purple_fight is None else purple_fight["entryTick"]
        ),
        "nextFightAfterLevel15Tick": (
            None if level_fight is None else level_fight["entryTick"]
        ),
        "story": " ".join(parts) if parts else "성장 시각과 이어진 다음 싸움은 관측되지 않았어요.",
        "fallbackUsed": False,
    }


def early_combat_cutoff_tick(first_tick: int) -> int:
    return int(first_tick) + EARLY_COMBAT_SECONDS * FPS


def is_early_combat(entry_tick, first_tick: int) -> bool:
    if not isinstance(entry_tick, (int, float)):
        return False
    return entry_tick < early_combat_cutoff_tick(first_tick)


def feedback_scenes(scenes: list[dict]) -> list[dict]:
    return [row for row in scenes if not row.get("earlyCombat")]


def pick_scenes(scenes: list[dict], deaths: list[dict]) -> list[int]:
    picked: list[int] = []
    eligible = feedback_scenes(scenes)

    def add(scene):
        if scene and scene["teamEpisodeNumber"] not in picked:
            picked.append(scene["teamEpisodeNumber"])

    death_numbers = {
        row.get("linkedEpisodeNumber")
        for row in deaths
        if not row.get("earlyCombat")
    }
    for scene in eligible:
        if scene["teamEpisodeNumber"] in death_numbers:
            add(scene)
            break
    for verdict in (
        "unsupported-isolated-entry",
        "supported-first-entry",
        "near-simultaneous-normal",
        "late-after-teammate-down",
        "calculated-low-hp-isolated-enemy",
        "insufficient-evidence",
    ):
        add(next((row for row in eligible if row["verdictId"] == verdict), None))
        if len(picked) >= 5:
            break
    if not picked and eligible:
        add(eligible[0])
    order = {row["teamEpisodeNumber"]: row["entryTick"] for row in eligible}
    return sorted(picked, key=lambda number: order.get(number, 0))


def later_habit_story(later: list[dict]) -> str:
    if not later:
        return (
            "시작 후 2분 이후의 대인 싸움이 없어, 싸움 습관은 그 이후 기록으로 읽지 않았어요."
        )
    isolated = sum(
        1 for row in later if row["verdictId"] == "unsupported-isolated-entry"
    )
    late = sum(1 for row in later if row["verdictId"] == "late-after-teammate-down")
    supported = sum(
        1 for row in later if row["verdictId"] == "supported-first-entry"
    )
    simultaneous = sum(
        1 for row in later if row["verdictId"] == "near-simultaneous-normal"
    )
    n = len(later)
    if isolated:
        return (
            f"2분 이후 싸움 {n}번 가운데 지원받기 어려운 고립 진입이 {isolated}번이었어요. "
            "들어가기 전에 가장 가까운 팀원 거리를 보는 쪽이 맞아요."
        )
    if late:
        return (
            f"2분 이후 싸움 {n}번 가운데 팀원이 다운된 뒤의 후속 진입이 {late}번이었어요. "
            "그 장면은 들어가도 결과를 뒤집기 어려웠습니다."
        )
    if supported or simultaneous:
        return (
            f"2분 이후 싸움 {n}번은 팀이 바로 올 수 있거나 거의 동시에 들어간 장면이 "
            f"{supported + simultaneous}번 포함돼, 그 거리를 유지하는 쪽으로 읽었어요."
        )
    return (
        f"2분 이후 싸움 {n}번은 있었지만, 고립이나 다운 뒤 진입처럼 "
        "바로 고칠 습관으로 묶이지는 않았어요."
    )


def hangul_has_coda(text: str) -> bool:
    for char in reversed(text or ""):
        if char.isspace():
            continue
        code = ord(char)
        if 0xAC00 <= code <= 0xD7A3:
            return (code - 0xAC00) % 28 != 0
        return True
    return True


def topic_particle(text: str) -> str:
    return "은" if hangul_has_coda(text) else "는"


def conjunctive_particle(text: str) -> str:
    return "과" if hangul_has_coda(text) else "와"


def keep_sentence(parts: list[str]) -> str:
    if not parts:
        return "2분 이후에서 팀과 붙어 들어간 장면은 따로 묶이지 않았어요."
    if len(parts) == 1:
        return (
            f"{parts[0]}{topic_particle(parts[0])} "
            "다음에 같은 거리가 보이면 그대로 가져가면 좋아요."
        )
    joined = "".join(
        f"{part}{conjunctive_particle(part)} " for part in parts[:-1]
    ) + parts[-1]
    return (
        f"{joined}{topic_particle(parts[-1])} "
        "다음에 같은 거리가 보이면 그대로 가져가면 좋아요."
    )


def build_next_play(
    later: list[dict],
    *,
    early_count: int,
) -> dict:
    counts = {
        key: sum(1 for row in later if row["verdictId"] == key)
        for key in VERDICT_LABELS
    }
    isolated = counts["unsupported-isolated-entry"]
    late = counts["late-after-teammate-down"]
    supported = counts["supported-first-entry"]
    simultaneous = counts["near-simultaneous-normal"]
    calculated = counts["calculated-low-hp-isolated-enemy"]
    excluded = (
        f"시작 후 2분 안 교전 {early_count}번은 대표 장면과 코칭에 넣지 않았어요."
        if early_count
        else "시작 후 2분 안 대인 교전은 없어서 초반을 따로 빼지 않았어요."
    )
    because: list[str] = []
    keep_parts: list[str] = []
    if supported:
        keep_parts.append(f"팀이 바로 올 수 있어 성립한 선진입 {supported}번")
    if simultaneous:
        keep_parts.append(f"팀과 거의 동시에 들어간 싸움 {simultaneous}번")
    keep = keep_sentence(keep_parts)
    if not later:
        return {
            "headline": "2분 이후 대인 싸움이 없어, 다음에 고칠 습관은 이 기록만으로 가르지 않았어요.",
            "doThis": "초반 2분 교전은 대표 장면으로 쓰지 않았고, 그 이후 싸움 기록이 없습니다.",
            "keep": keep,
            "because": [],
            "excludedNote": excluded,
        }
    if isolated:
        because.append(f"2분 이후 고립 진입이 {isolated}번 있었어요.")
    if late:
        because.append(f"팀원이 다운된 뒤의 후속 진입이 {late}번 있었어요.")
    if calculated:
        because.append(
            f"체력이 낮았지만 적도 고립돼 계산 가능했던 진입이 {calculated}번 있었어요."
        )
    if isolated and late:
        headline = "다음엔 혼자 들어가지 말고, 팀원이 넘어진 뒤의 진입도 거르세요."
        do_this = (
            "들어가기 전에 가장 가까운 팀원이 40m보다 멀면 합류를 기다리고, "
            "이미 넘어진 뒤에는 그 싸움에 들어가지 않는 편이 나아요."
        )
    elif isolated:
        headline = "다음엔 들어가기 전에 팀 거리부터 보세요."
        do_this = (
            "가장 가까운 생존 팀원이 40m 밖이거나 4초 안에 붙기 어려우면 "
            "먼저 합류를 기다리세요."
        )
    elif late:
        headline = "다음엔 팀원이 넘어진 뒤의 진입은 거르세요."
        do_this = (
            "팀원이 이미 다운된 뒤에는 들어가도 결과를 뒤집기 어려웠으니, "
            "그 싸움에 들어가지 않는 편이 나아요."
        )
    elif supported or simultaneous:
        headline = "2분 이후에는 팀과 붙어 들어간 장면이 많아서, 그 거리를 유지하세요."
        do_this = (
            "다음에 싸움을 열 때도 팀원이 20m 안이거나 3초 안에 붙을 수 있는지부터 보세요."
        )
    else:
        headline = (
            "2분 이후 싸움은 있었지만, 바로 고칠 습관으로 묶이지는 않았어요."
        )
        do_this = (
            "자료만으로 정당성을 가르기 어려운 장면이 많아, "
            "억지로 습관을 단정하지 않았어요."
        )
    return {
        "headline": headline,
        "doThis": do_this,
        "keep": keep,
        "because": because,
        "excludedNote": excluded,
    }


def attach_scene_coaching(
    players: list[dict],
    wildlife: list[dict] | None = None,
    *,
    first_tick: int = 0,
    phase_clock: dict | None = None,
) -> dict:
    """Attach compact scene coaching to every player. Mutates players in place."""

    scenes_total = 0
    death_total = 0
    verdict_counts: dict[str, int] = defaultdict(int)
    for player in players:
        episode_scenes = [
            build_episode_scene(player, players, episode)
            for episode in player["combatJudgment"]["personalEpisodes"]
        ]
        for scene in episode_scenes:
            scene["earlyCombat"] = is_early_combat(scene.get("entryTick"), first_tick)
            scene["feedbackEligible"] = not scene["earlyCombat"]
        scene_by_number = {row["teamEpisodeNumber"]: row for row in episode_scenes}
        death_scenes = []
        for death in player.get("deathReview", {}).get("deaths") or []:
            focus = death.get("downTick") if death.get("downTick") is not None else death["focusTick"]
            linked = _link_episode(player, focus)
            episode_scene = (
                scene_by_number.get(linked["teamEpisodeNumber"]) if linked else None
            )
            death_scene = build_death_scene(player, death, episode_scene)
            death_tick = (
                death_scene.get("downTick")
                if death_scene.get("downTick") is not None
                else death_scene.get("deathTick", death_scene.get("focusTick"))
            )
            death_scene["earlyCombat"] = is_early_combat(death_tick, first_tick)
            death_scene["feedbackEligible"] = not death_scene["earlyCombat"]
            death_scenes.append(death_scene)
        wildlife_story = wildlife_flow(
            player,
            wildlife or [],
            first_tick=first_tick,
            phase_clock=phase_clock,
        )
        # Public output must not carry the private object id used only for kill matching.
        wildlife_story.pop("playerObjectBound", None)
        growth = growth_link(player)
        later = feedback_scenes(episode_scenes)
        later_deaths = [row for row in death_scenes if row.get("feedbackEligible")]
        early_count = sum(1 for row in episode_scenes if row.get("earlyCombat"))
        for scene in episode_scenes:
            verdict_counts[scene["verdictId"]] += 1
        player["sceneCoaching"] = {
            "status": "derived-exact-scene-coaching-v1",
            "fallbackUsed": False,
            "nearbyRadiusMeters": NEARBY_METERS,
            "supportRadiusMeters": SUPPORT_METERS,
            "isolatedRadiusMeters": ISOLATED_METERS,
            "earlyCombatSeconds": EARLY_COMBAT_SECONDS,
            "episodes": episode_scenes,
            "deaths": death_scenes,
            "pickedEpisodeNumbers": pick_scenes(episode_scenes, later_deaths),
            "wildlifeFlow": wildlife_story,
            "growthLink": growth,
            "verdictCounts": {
                key: sum(row["verdictId"] == key for row in episode_scenes)
                for key in VERDICT_LABELS
            },
            "feedback": {
                "earlyCombatSeconds": EARLY_COMBAT_SECONDS,
                "earlyEpisodeCount": early_count,
                "laterEpisodeCount": len(later),
                "verdictCounts": {
                    key: sum(row["verdictId"] == key for row in later)
                    for key in VERDICT_LABELS
                },
                "habitStory": later_habit_story(later),
                "nextPlay": build_next_play(later, early_count=early_count),
            },
            "definitions": {
                "hp": "마지막 전체 스냅샷 HP를 질의 tick까지 유지한 파생값",
                "position": "마지막 decoded 이동 앵커를 질의 tick까지 유지한 파생 좌표",
                "cooldown": "exact 쿨다운 패킷의 남은 시간을 tick 차이로 줄인 파생 시계",
                "wildlifeKill": "exact 처치 패킷 연결만 처치로 집계. 근접은 처치가 아님",
                "verdict": "코칭 해석이며 의도·적중·막타 단정이 아님",
            },
        }
        scenes_total += len(episode_scenes)
        death_total += len(death_scenes)

    return {
        "status": "derived-exact-scene-coaching-v1",
        "playerCount": len(players),
        "episodeSceneCount": scenes_total,
        "deathSceneCount": death_total,
        "verdictCounts": dict(verdict_counts),
        "fallbackUsed": False,
    }


def attach_scene_coaching_to_catalog(catalog: dict) -> dict:
    summary = attach_scene_coaching(
        catalog["players"],
        catalog.get("wildlife"),
        first_tick=catalog.get("meta", {}).get("firstTick", 0),
        phase_clock=catalog.get("phaseClock"),
    )
    catalog["sceneCoaching"] = summary
    return summary
