from __future__ import annotations

import argparse
import json
import math
from pathlib import Path

try:
    from .character_capabilities import require_official_game_data_url
except ImportError:
    from character_capabilities import require_official_game_data_url


def _assert_calculable_skill_hit_rate(row: dict) -> None:
    attempts = row.get("attemptCount")
    hits = row.get("playerHitAttemptCount")
    rate = row.get("playerHitRate")
    assert isinstance(attempts, int) and not isinstance(attempts, bool)
    assert attempts >= 1
    assert isinstance(hits, int) and not isinstance(hits, bool)
    assert 0 <= hits <= attempts
    assert isinstance(rate, (int, float)) and not isinstance(rate, bool)
    assert rate == round(hits / attempts, 6)
    assert row["aimModel"]["hitRateEligible"] is True

    unit = row.get("hitRateUnit")
    packet = row["hitEvidencePacket"]
    denominator = row["attemptDenominator"]
    if unit == "projectile-shot":
        shapes = row["projectileShape"]["values"]
        assert row["projectileStatus"] == (
            "verified-observed-exclusive-cast-spawn-link"
        )
        assert shapes in (["single"], ["multi"])
        assert packet == {
            "packet": "CmdProjectileCollision.objectId/targetId",
            "status": "decoded-exact-projectile-target-collision",
        }
        assert denominator["event"] == "CmdSpawn projectile snapshot objectId"
        assert row["duplicateCollisionPolicy"] == (
            "unique projectileObjectId/targetObjectId"
        )
        if shapes == ["single"]:
            assert row.get("projectilesPerCast") in (None, 1)
            assert attempts == row["castCount"]
            assert denominator["status"] == (
                "verified-exclusive-one-projectile-spawn-per-cast-"
                "during-confirmed-player-engagement"
            )
        else:
            per_cast = row.get("projectilesPerCast")
            assert isinstance(per_cast, int) and not isinstance(per_cast, bool)
            assert per_cast >= 2
            assert attempts == row["castCount"] * per_cast
            assert denominator["status"] == (
                "verified-constant-multiple-projectile-spawns-per-cast-"
                "during-confirmed-player-engagement"
            )
        return

    assert unit == "skill-cast"
    assert attempts == row["castCount"]
    assert denominator["event"] == "CmdStartSkill"
    assert denominator["status"] == (
        "verified-complete-action-covered-manual-skill-casts-"
        "during-confirmed-player-engagement"
    )
    method = row.get("hitRateMethod")
    if method == "exact-action-target-same-tick-corroborated":
        evidence = row["actionTargetEvidence"]
        assert packet == {
            "packet": (
                "CmdPlaySkillActionWithTargets.targets.targetId + "
                "same-tick CmdDamage/CmdProjectileCollision"
            ),
            "status": (
                "decoded-exact-enemy-target-plus-same-tick-hit-"
                "corroboration"
            ),
        }
        assert evidence["castHitRateCalculable"] is True
        assert evidence["promotionStatus"] == (
            "verified-exact-action-target-cast-hit-rate"
        )
        assert evidence["combatCastsWithSameTickCorroboratedEnemyTargetCount"] == hits
        assert evidence["fallbackUsed"] is False
        return
    assert method == "exact-effect-code-skill-state-group-same-action-tick"
    evidence = row["exactEffectDamageEvidence"]
    assert packet == {
        "packet": (
            "CmdDamage.effectCode/objectId + exact Skill/CharacterState "
            "code/group + CmdPlaySkillAction tick"
        ),
        "status": "verified-exact-versioned-skill-state-code-and-same-action-tick",
    }
    assert evidence["castHitRateCalculable"] is True
    assert evidence["promotionStatus"] == "verified-exact-effect-code-cast-hit-rate"
    assert evidence["combatCastsWithExactDamageCount"] == hits
    assert evidence["fallbackUsed"] is False


def validate_analysis(
    json_path: Path,
    html_path: Path | None,
    expected_game_id: int | None = None,
) -> dict:
    data = json.loads(json_path.read_text(encoding="utf-8"))
    html = html_path.read_text(encoding="utf-8") if html_path is not None else None

    assert data["format"] == "er-replay-combat-analysis.v1"
    game_id = data["meta"]["gameId"]
    assert isinstance(game_id, int) and game_id > 0
    if expected_game_id is not None:
        assert game_id == expected_game_id
    assert isinstance(data["meta"]["clientVersion"], str)
    assert data["meta"]["clientVersion"].strip()
    compatibility = data["meta"]["compatibility"]
    assert compatibility["clientLayout"]["status"] == "exact-supported-layout"
    assert compatibility["clientLayout"]["clientVersion"] == data["meta"]["clientVersion"]
    assert compatibility["matchMode"]["status"] == "exact-finish-result-consensus"
    assert compatibility["matchMode"]["matchingMode"] == data["meta"]["matchingMode"]
    assert compatibility["matchMode"]["matchingTeamMode"] == data["meta"]["matchingTeamMode"]
    assert compatibility["matchMode"]["key"] == data["meta"]["matchMode"]
    assert compatibility["fallbackUsed"] is False
    assert compatibility["clientLayout"]["fallbackUsed"] is False
    assert compatibility["matchMode"]["fallbackUsed"] is False

    players = data["players"]
    events = data["events"]
    player_count = len(players)
    assert player_count > 0
    assert data["meta"]["playerCount"] == player_count
    assert data["meta"]["finishResultPlayerCount"] == player_count
    assert data["dataSources"]["finishGameResult"]["playerCount"] == player_count
    assert data["dataSources"]["finishGameResult"]["componentSumChecks"] == player_count
    assert data["meta"]["selectedEventCount"] == len(events)
    assert data["dataSources"]["timelineEvents"]["selectedEventCount"] == len(events)
    assert data["dataSources"]["finishGameResult"]["status"] == "decoded-exact-wire"
    event_types = data["eventTypes"]
    event_types_by_name = {
        row.get("packetName"): row
        for row in event_types.values()
        if isinstance(row, dict)
    }
    required_state_fields = {
        "CmdAddState": {"objectId", "code", "casterId"},
        "CmdAddStateExtended": {"objectId", "code", "casterId"},
        "CmdUpdateState": {"objectId", "group", "casterId"},
        "CmdResetCreateTimeState": {"objectId", "group", "casterId"},
        "CmdRemoveState": {"objectId", "group", "casterId"},
        "CmdPauseState": {"objectId", "group", "casterId"},
        "CmdProjectileCollision": {"objectId", "targetId"},
    }
    for packet_name, required_fields in required_state_fields.items():
        definition = event_types_by_name[packet_name]
        assert definition["wireStatus"] == "decoded-exact-wire"
        assert required_fields.issubset(definition["fields"])
    capability_catalog = data["characterCapabilityCatalog"]
    static_game_data = data["dataSources"]["staticGameData"]
    require_official_game_data_url(static_game_data["url"])
    assert static_game_data["status"] == "exact-replay-header-official-gameDb"
    assert static_game_data["fallbackUsed"] is False
    assert capability_catalog["status"] == (
        "exact-replay-version-official-gameDb-character-capabilities"
    )
    assert capability_catalog["fallbackUsed"] is False
    assert capability_catalog["characterCount"] == len(capability_catalog["characters"])
    runtime_cc = capability_catalog["runtimeCrowdControl"]
    assert runtime_cc["status"] == (
        "exact-replay-version-official-gameDb-cc-state-map"
    )
    assert runtime_cc["fallbackUsed"] is False
    assert runtime_cc["codeCount"] == len(runtime_cc["codes"]) > 0
    assert runtime_cc["groupCount"] == len(runtime_cc["groups"]) > 0
    assert all(
        row["stateCode"] == int(code)
        and str(row["stateGroup"]) in runtime_cc["groups"]
        and row["stateType"]
        == runtime_cc["groups"][str(row["stateGroup"])]["stateType"]
        and row["status"] == "exact-replay-version-character-state-code"
        for code, row in runtime_cc["codes"].items()
    )
    assert static_game_data["characterCapabilityCount"] == capability_catalog[
        "characterCount"
    ]
    projectile_catalog = data["projectileSkillCatalog"]
    assert projectile_catalog["status"] == (
        "exact-replay-version-whole-roster-projectile-skill-catalog"
    )
    assert projectile_catalog["fallbackUsed"] is False
    assert projectile_catalog["characterCount"] == len(
        projectile_catalog["characters"]
    ) > 0
    assert projectile_catalog["skillCount"] == len(
        projectile_catalog["skillGroups"]
    ) > 0
    assert projectile_catalog["projectileDefinitionCount"] == len(
        projectile_catalog["projectileDefinitions"]
    ) > 0
    assert static_game_data["projectileSkillCount"] == projectile_catalog[
        "skillCount"
    ]
    assert static_game_data["projectileDefinitionCount"] == projectile_catalog[
        "projectileDefinitionCount"
    ]
    required_projectile_skill_fields = {
        "projectileStatus", "projectileCodes", "projectileShape", "aimModel", "castTiming",
        "hitEvidencePacket", "useCountEvidence", "attemptDenominator", "hitRateCalculable",
        "hitRateReason",
    }
    assert all(
        row["skillGroup"] == int(group)
        and required_projectile_skill_fields.issubset(row)
        and row["projectileStatus"] in {
            "candidate-static-name-only",
            "unknown-no-declared-skill-projectile-link",
        }
        and row["projectileCodes"] == []
        and row["aimModel"]["status"]
        == "exact-replay-version-SkillGroup-cast-model"
        and isinstance(row["aimModel"]["hitRateEligible"], bool)
        and row["castTiming"]["status"]
        == "derived-exact-replay-version-SkillGroup-timing"
        and isinstance(row["castTiming"]["derivedLinkWindowTicksAt60Hz"], int)
        and row["useCountEvidence"]["packet"] == "CmdStartSkill"
        and row["useCountEvidence"]["status"]
        == "decoded-exact-character-skill-start-count"
        and isinstance(row["projectileCodeCandidates"], list)
        and all(
            isinstance(code, int)
            and str(code) in projectile_catalog["projectileDefinitions"]
            for code in row["projectileCodeCandidates"]
        )
        and row["hitRateCalculable"] is False
        and isinstance(row["hitRateReason"], str)
        and row["hitRateReason"]
        and row["fallbackUsed"] is False
        for group, row in projectile_catalog["skillGroups"].items()
    )
    catalog_character_skill_groups = {
        str(row["skillGroup"])
        for character in projectile_catalog["characters"].values()
        for row in character["skills"]
    }
    assert catalog_character_skill_groups == set(projectile_catalog["skillGroups"])
    projectile_runtime = data["projectileHitRateRuntime"]
    assert projectile_runtime["status"] == (
        "derived-exact-projectile-spawn-and-collision-hit-rates"
    )
    assert projectile_runtime["fallbackUsed"] is False
    assert projectile_runtime["scope"] == "confirmed-player-engagement-only"
    assert projectile_runtime["scopeEvidence"] == (
        "derived-exact-team-pvp-episode-intersect-own-combat-state"
    )
    assert projectile_runtime["linkWindowTicks"] >= 0
    assert projectile_runtime["minimumObservations"] >= 1
    assert projectile_runtime["projectileSpawnCount"] == data["meta"][
        "projectileSpawnCount"
    ]
    assert projectile_runtime["projectileCollisionCount"] == data["meta"][
        "projectileCollisionCount"
    ]
    assert set(projectile_runtime["players"]) == {
        str(player["objectId"]) for player in players
    }
    assert all(
        isinstance(projectile_runtime[key], int)
        and projectile_runtime[key] >= 0
        for key in (
            "resolvedCharacterSkillStartCount",
            "excludedNonCharacterOrUnresolvedSkillStartCount",
        )
    )
    assert data["meta"]["projectileHitRateCalculableSkillCount"] == sum(
        row["hitRateCalculable"] is True
        for rows in projectile_runtime["players"].values()
        for row in rows
    )
    combat_summary = data["combatJudgment"]
    assert combat_summary["status"] == (
        "derived-exact-combat-state-and-target-selection-v1"
    )
    assert combat_summary["fallbackUsed"] is False
    assert combat_summary["playerCount"] == player_count
    assert combat_summary["teamFocusWindowSeconds"] == 2
    assert combat_summary["teamEpisodeCount"] >= 0
    assert data["growthTempo"] == {
        "status": "derived-exact-snapshot-and-equipment-growth-v1",
        "playerCount": player_count,
        "levelMilestones": [6, 9, 12, 15, 18, 20],
        "equipmentMilestones": [3, 4, 5],
        "fallbackUsed": False,
    }
    assert data["objectivePreparation"]["status"] == (
        "derived-exact-objective-clock-and-held-anchor-distance-v1"
    )
    assert data["objectivePreparation"]["playerCount"] == player_count
    assert data["objectivePreparation"]["preparationRadiusMeters"] == 20
    assert data["objectivePreparation"]["fallbackUsed"] is False
    assert data["skillOperation"]["status"] == (
        "derived-exact-confirmed-pvp-skill-sequence-v1"
    )
    assert data["skillOperation"]["playerCount"] == player_count
    assert data["skillOperation"]["chainWindowSeconds"] == 3
    assert data["skillOperation"]["fallbackUsed"] is False
    assert data["sceneCoaching"]["status"] == "derived-exact-scene-coaching-v1"
    assert data["sceneCoaching"]["fallbackUsed"] is False
    assert data["sceneCoaching"]["playerCount"] == player_count
    assert data["deathReview"]["status"] == (
        "derived-exact-event-and-held-position-death-review-v1"
    )
    assert data["deathReview"]["playerCount"] == player_count
    assert data["deathReview"]["contextSeconds"] == 5
    assert data["deathReview"]["targetWindowSeconds"] == 4
    assert data["deathReview"]["nearbyRadiusMeters"] == 30
    assert data["deathReview"]["collapseWindowSeconds"] == 15
    assert data["deathReview"]["fallbackUsed"] is False

    event_count = len(events)
    player_ids = {str(player["objectId"]) for player in players}
    assert set(data["playerEventIndexes"]) == player_ids
    assert all(
        0 <= event_index < event_count
        for indexes in data["playerEventIndexes"].values()
        for event_index in indexes
    )
    item_catalog = data["itemCatalog"]
    assert item_catalog
    assert all(
        isinstance(code, str)
        and code.isdigit()
        and row["code"] == int(code)
        and row["sourceTable"] in {
            "ItemWeapon.json",
            "ItemArmor.json",
            "ItemConsumable.json",
            "ItemSpecial.json",
            "ItemMisc.json",
        }
        for code, row in item_catalog.items()
    )

    bounds = data["map"]["world"]
    movement_count = 0
    planned_path_count = 0
    planned_path_node_count = 0
    for player in players:
        capabilities = player["characterCapabilities"]
        evidence = player["observedCapabilityEvidence"]
        assert capabilities == capability_catalog["characters"][
            str(player["characterCode"])
        ]
        assert capabilities["fallbackUsed"] is False
        assert capabilities["interpretationBoundary"] == (
            "static-base-capability-not-runtime-source-or-final-duration"
        )
        assert capabilities["coverageStatus"] in {
            "exact-static-definitions-present",
            "unavailable-no-character-skill-or-state-definition",
        }
        assert evidence["status"] == "decoded-exact-runtime-capability-evidence"
        assert evidence["fallbackUsed"] is False
        assert evidence["crowdControlSourceStatus"] == (
            "unavailable-CmdCrowdControl-has-no-source-field"
        )
        assert evidence["shieldSourceStatus"] == (
            "unavailable-CmdUpdateShield-has-no-caster-field"
        )
        assert evidence["healingStatus"] == "decoded-exact-positive-CmdHeal-values"
        assert evidence["shieldTimeline"] == sorted(
            evidence["shieldTimeline"], key=lambda row: row[0]
        )
        assert all(
            isinstance(evidence[key], int) and evidence[key] >= 0
            for key in (
                "maxObservedShield",
                "positiveShieldUpdateCount",
                "healingReceived",
                "alliedHealingGiven",
            )
        )
        projectile_rows = player["projectileHitRates"]
        assert projectile_rows == projectile_runtime["players"][str(player["objectId"])]
        for row in projectile_rows:
            assert required_projectile_skill_fields.issubset(row)
            assert row["skillGroup"] in {
                item["skillGroup"] for item in capabilities["skillProfiles"]
            }
            assert row["fallbackUsed"] is False
            assert row["scope"] == "confirmed-player-engagement-only"
            assert row["scopeEvidence"] == (
                "derived-exact-team-pvp-episode-intersect-own-combat-state"
            )
            assert row["useCountEvidence"]["scope"] == (
                "confirmed-player-engagement-only"
            )
            assert isinstance(row["allCastCount"], int) and row["allCastCount"] >= 1
            assert isinstance(row["castCount"], int) and row["castCount"] >= 0
            assert row["allCastCount"] >= row["castCount"]
            assert row["excludedOutsideCombatCastCount"] == (
                row["allCastCount"] - row["castCount"]
            )
            assert row["aimModel"]["status"] == (
                "exact-replay-version-SkillGroup-cast-model"
            )
            assert isinstance(row["aimModel"]["hitRateEligible"], bool)
            assert row["castTiming"]["status"] == (
                "derived-exact-replay-version-SkillGroup-timing"
            )
            assert isinstance(
                row["castTiming"]["derivedLinkWindowTicksAt60Hz"], int
            )
            assert isinstance(row["confirmedPvpIntervalCount"], int)
            assert row["confirmedPvpIntervalCount"] >= 0
            if row["hitRateCalculable"]:
                _assert_calculable_skill_hit_rate(row)
        judgment = player["combatJudgment"]
        episodes = judgment["episodes"]
        assert judgment["status"] == (
            "derived-exact-combat-state-and-target-selection-v1"
        )
        assert judgment["fallbackUsed"] is False
        assert judgment["styleAuthority"] == "derived-team-relative-v1"
        assert judgment["styleLabel"]
        assert judgment["participatedEpisodeCount"] == len(episodes)
        assert judgment["teamEpisodeCount"] >= len(episodes)
        assert episodes == sorted(episodes, key=lambda row: row["startTick"])
        assert all(
            judgment[key] is None or 0 <= judgment[key] <= 1
            for key in (
                "initiationRate", "soloInitiationRate", "survivalRate",
                "episodePrimaryTargetShare", "teamFocusFollowupRate",
            )
        )
        assert judgment["targetActionCount"] == sum(
            row["targetActionCount"] for row in episodes
        )
        assert judgment["targetSwitchCount"] == sum(
            row["targetSwitchCount"] for row in episodes
        )
        assert judgment["teamFocusFollowupCount"] == sum(
            row["teamFocusFollowupCount"] for row in episodes
        )
        assert all(
            row["entryRole"] in {"initiator", "co-initiator", "joiner"}
            and row["startTick"] <= row["entryTick"] <= row["endTick"]
            and 0 <= row["targetSwitchCount"]
            <= max(0, row["targetActionCount"] - 1)
            and 0 <= row["teamFocusFollowupCount"] <= row["targetActionCount"]
            for row in episodes
        )
        death_review = player["deathReview"]
        death_rows = death_review["deaths"]
        assert death_review["status"] == (
            "derived-exact-event-and-held-position-death-review-v1"
        )
        assert death_review["fallbackUsed"] is False
        assert death_review["deathCount"] == len(death_rows)
        assert death_rows == sorted(death_rows, key=lambda row: row["deathTick"])
        assert all(
            row["status"] == "exact-events-plus-derived-held-position-context"
            and row["focusTick"] <= row["deathTick"]
            and row["enemyTargeterCount"] <= row["enemyTargetActionCount"]
            and row["nearbyAliveAllyCount"] >= 0
            and row["nearbyAliveEnemyCount"] >= 0
            and row["crowdControlCount"]
            == sum(item["count"] for item in row["crowdControlReceived"])
            and row["recordedDamageStatus"] == "partial-non-null-CmdDamage-only"
            for row in death_rows
        )
        growth = player["growthTempo"]
        assert growth["status"] == "derived-exact-snapshot-and-equipment-growth-v1"
        assert growth["fallbackUsed"] is False
        assert growth["styleAuthority"] == "derived-team-relative-v1"
        assert growth["levelTimeline"] == sorted(
            growth["levelTimeline"], key=lambda row: row[0]
        )
        assert [row["level"] for row in growth["levelMilestones"]] == [
            6, 9, 12, 15, 18, 20,
        ]
        assert [
            row["completedSlots"] for row in growth["equipmentMilestones"]
        ] == [3, 4, 5]
        assert isinstance(growth["finalMasteryLevel"], int)
        assert growth["finalMasteryLevel"] >= 0
        objective = player["objectivePreparation"]
        assert objective["status"] == (
            "derived-exact-objective-clock-and-held-anchor-distance-v1"
        )
        assert objective["fallbackUsed"] is False
        assert objective["styleAuthority"] == "derived-team-relative-v1"
        assert objective["objectiveCount"] == len(objective["objectives"])
        assert (
            objective["beforeActiveCount"]
            + objective["afterActiveCount"]
            + objective["notObservedCount"]
            == objective["objectiveCount"]
        )
        assert all(
            row["arrivalStatus"]
            in {"before-active", "after-active", "not-observed-in-range"}
            and row["warningTick"] <= row["activeTick"] <= row["endTick"]
            for row in objective["objectives"]
        )
        operation = player["skillOperation"]
        operation_rows = operation["episodes"]
        assert operation["status"] == "derived-exact-confirmed-pvp-skill-sequence-v1"
        assert operation["fallbackUsed"] is False
        assert operation["styleAuthority"] == "derived-team-relative-v1"
        assert operation_rows == sorted(operation_rows, key=lambda row: row["startTick"])
        assert operation["pvpSkillStartCount"] == sum(
            row["skillStartCount"] for row in operation_rows
        )
        assert operation["pvpNormalAttackStartCount"] == sum(
            row["normalAttackStartCount"] for row in operation_rows
        )
        coaching = player["sceneCoaching"]
        assert coaching["status"] == "derived-exact-scene-coaching-v1"
        assert coaching["fallbackUsed"] is False
        assert coaching["earlyCombatSeconds"] == 120
        assert coaching["feedback"]["earlyCombatSeconds"] == 120
        assert [(row["startTick"], row["endTick"]) for row in coaching["episodes"]] == [
            (row["startTick"], row["endTick"]) for row in judgment["personalEpisodes"]
        ]
        assert len(coaching["deaths"]) == len(death_rows)
        assert all(
            row["verdictId"] in {
                "near-simultaneous-normal",
                "supported-first-entry",
                "calculated-low-hp-isolated-enemy",
                "unsupported-isolated-entry",
                "late-after-teammate-down",
                "insufficient-evidence",
            }
            and row["fallbackUsed"] is False
            for row in coaching["episodes"]
        )
        stats = player["stats"]
        result = player["gameResult"]
        track = player["movementTrack"]
        movement_count += len(track)
        assert player["gameResultStatus"] == "decoded-exact-wire"
        assert player["gameResultComponentSumsExact"] is True
        assert player["movementTrackStatus"] == "exact-anchor-held-until-next-observation"
        assert player["plannedPathStatus"] == "exact-intended-nav-corners-without-node-timing"
        assert player["equipmentTimelineStatus"] == "decoded-exact-observer-equipment-updates"
        assert player["inventoryTimelineStatus"] == "decoded-exact-observer-inventory-updates"
        assert player["equipmentTimeline"] == sorted(
            player["equipmentTimeline"], key=lambda row: row[0]
        )
        assert player["inventoryTimeline"] == sorted(
            player["inventoryTimeline"], key=lambda row: row[0]
        )
        from decoder.kda_source_discrepancy import crosscheck, EXACT, DISCREPANCY
        expected_kda = [result['playerKill'], result['playerDeaths'], result['playerAssistant']]
        proof = crosscheck(player['kdaTimeline'], expected_kda, player['objectId'],
                           {p['objectId'] for p in players}, events, event_types,
                           data['dataSources']['finishGameResult'])
        assert player.get('kdaCrosscheck') == proof
        assert player['kdaTimelineStatus'] == (DISCREPANCY if proof else EXACT)
        assert all(
            isinstance(row, list)
            and len(row) == 4
            and all(isinstance(value, int) and value >= 0 for value in row)
            for row in player["kdaTimeline"]
        )
        assert player["observerStatusTimelineStatus"] == (
            "decoded-exact-full-snapshot-vf-credit-and-gadget-energy-"
            "held-until-next-snapshot"
        )
        assert player["observerStatusTimeline"] == sorted(
            player["observerStatusTimeline"], key=lambda row: row[0]
        )
        assert all(
            isinstance(row, list)
            and len(row) == 3
            and isinstance(row[0], int)
            and isinstance(row[1], (int, float))
            and row[1] >= 0
            and isinstance(row[2], int)
            and row[2] >= 0
            for row in player["observerStatusTimeline"]
        )
        assert player["survivableTimeTimelineStatus"] == (
            "decoded-exact-team-survivable-time-update-held-until-next-update"
        )
        assert player["survivableTimeTimeline"] == sorted(
            player["survivableTimeTimeline"], key=lambda row: row[0]
        )
        assert all(
            isinstance(row, list)
            and len(row) == 2
            and all(isinstance(value, int) and value >= 0 for value in row)
            for row in player["survivableTimeTimeline"]
        )
        assert player["skillStartTimeline"] == sorted(
            player["skillStartTimeline"], key=lambda row: row[0]
        )
        assert player["skillCooldownTimelineStatus"] == (
            "decoded-exact-cooldown-packets-with-derived-clock"
        )
        cooldown_timeline = player["skillCooldownTimeline"]
        assert cooldown_timeline == sorted(cooldown_timeline, key=lambda row: row[0])
        assert all(
            isinstance(row, list)
            and len(row) == 7
            and isinstance(row[0], int)
            and row[1] in {"set", "copy", "hold", "clear"}
            for row in cooldown_timeline
        )
        assert all(
            str(item_code) in item_catalog
            for timeline in (
                player["equipmentTimeline"],
                player["inventoryTimeline"],
            )
            for row in timeline
            for _, item_code, amount in row[1]
            if item_code is not None and amount > 0
        )
        assert player["movementAnchorCount"] == len(track) > 0
        assert track == sorted(track, key=lambda row: row[0])
        # Exact warp/resurrection anchors can be outside the playable map
        # rectangle. Retain them as wire evidence; the renderer clips them.
        assert all(
            isinstance(row[1], (int, float))
            and isinstance(row[2], (int, float))
            and math.isfinite(row[1])
            and math.isfinite(row[2])
            for row in track
        ), f"player {player['objectId']} has a non-finite movement anchor"
        planned_paths = player["plannedPathCommands"]
        planned_path_count += len(planned_paths)
        planned_path_node_count += sum(len(path[2]) for path in planned_paths)
        assert player["plannedPathCommandCount"] == len(planned_paths)
        assert player["plannedPathNodeCount"] == sum(
            len(path[2]) for path in planned_paths
        )
        assert planned_paths == sorted(planned_paths, key=lambda row: row[0])
        assert all(
            len(path) == 3
            and isinstance(path[0], int)
            and isinstance(path[1], str)
            and len(path[2]) >= 2
            and all(
                isinstance(point, list)
                and len(point) == 2
                and all(isinstance(value, (int, float)) and math.isfinite(value) for value in point)
                for point in path[2]
            )
            for path in planned_paths
        ), f"player {player['objectId']} has an invalid intended path"
        assert stats["damageToPlayerKnownPackets"] <= stats["damageToPlayerPackets"]
        assert stats["damageFromPlayerKnownPackets"] <= stats["damageFromPlayerPackets"]
        assert all(
            0 <= row["readyCombatRatio"] <= 1
            for row in player["cooldowns"]
            if row["readyCombatRatio"] is not None
        )
        assert result["damageToPlayer"] == sum(
            result[f"damageToPlayer_{kind}"]
            for kind in ("trap", "basic", "skill", "itemSkill", "direct")
        )
        assert result["damageFromPlayer"] == sum(
            result[f"damageFromPlayer_{kind}"]
            for kind in ("trap", "basic", "skill", "itemSkill", "direct")
        )

    assert data["map"]["movementAnchorCount"] == movement_count
    assert data["map"]["movementStatus"] == (
        "decoded-exact-command-anchors-held-until-next-observation-"
        "plus-intended-nav-corners"
    )
    if data["meta"]["matchMode"] == "cobalt":
        assert data["map"]["playbackStatus"] == "unavailable-mode-specific-map-and-bounds"
        assert data["map"]["imageStatus"] == "unavailable-mode-specific-map"
        assert data["map"]["imageDataUrl"] == ""
        assert data["map"]["imageSha256"] is None
    else:
        assert data["map"]["playbackStatus"] == "reference-lumia-map-available"
        assert data["map"]["imageStatus"] == (
            "user-provided-satellite-map-not-client-asset-authority"
        )
        assert data["map"]["imageDataUrl"].startswith("data:image/png;base64,")
        assert isinstance(data["map"]["imageSha256"], str)
        assert data["map"]["image"] == {"w": 772, "h": 1000}
        assert data["map"]["coordinateSpace"] == {"w": 772, "h": 981}
        assert data["map"]["imageContentRect"] == {
            "x": 0,
            "y": 10,
            "w": 772,
            "h": 981,
        }
        assert data["map"]["imageAlignment"]["status"] == (
            "pixel-registered-satellite-content-to-dak-coordinate-space"
        )
        projection = data["map"]["projection"]
        assert projection["type"] == "affine-world-xz-to-source-pixel"
        assert projection["pixelX"] == [-1.645, -1.645, 290.1]
        assert projection["pixelY"] == [-1.653, 1.652, 555.0]
        assert data["map"]["source"]["status"] == (
            "user-provided-public-shared-map-not-client-asset-authority"
        )
    assert data["map"]["plannedPathCommandCount"] == planned_path_count
    assert data["map"]["plannedPathNodeCount"] == planned_path_node_count
    wildlife = data["wildlife"]
    assert data["meta"]["wildlifeInstanceCount"] == len(wildlife)
    assert data["dataSources"]["timelineEvents"]["wildlifeInstanceCount"] == len(
        wildlife
    )
    world_map = data["worldMap"]
    assert world_map["unpositionedTimelineStatus"] == (
        "private-diagnostic-exact-timeline-metadata-without-map-position"
    )
    assert world_map["unpositionedTimelineCount"] == len(
        world_map["unpositionedTimeline"]
    )
    assert world_map["resourceBoxUpdateStatus"] == (
        "private-diagnostic-exact-CmdUpdateResourceBoxCooldown"
    )
    assert world_map["resourceBoxUpdates"] == sorted(
        world_map["resourceBoxUpdates"], key=lambda row: row["tick"]
    )
    assert all(
        isinstance(row["tick"], int)
        and isinstance(row["objectId"], int)
        and isinstance(row["cooldownHundredths"], int)
        and row["cooldownHundredths"] >= 0
        and isinstance(row["spawnDate"], int)
        and isinstance(row["remainCollectCount"], int)
        and row["remainCollectCount"] >= 0
        and row["wireStatus"] == "decoded-exact-resource-box-cooldown"
        for row in world_map["resourceBoxUpdates"]
    )
    assert world_map["resourceItemBoxEventStatus"] == (
        "private-diagnostic-exact-item-box-dictionary-object-id"
    )
    assert world_map["resourceItemBoxEvents"] == sorted(
        world_map["resourceItemBoxEvents"], key=lambda row: row["tick"]
    )
    assert all(
        isinstance(row["tick"], int)
        and isinstance(row["itemBoxObjectId"], int)
        and row["packetName"] in {"CmdItemBoxRemove", "CmdItemBoxUpdate"}
        and (row["itemId"] is None or isinstance(row["itemId"], int))
        and (row["remainCount"] is None or isinstance(row["remainCount"], int))
        and row["wireStatus"]
        == "decoded-exact-item-box-dictionary-key-and-payload"
        for row in world_map["resourceItemBoxEvents"]
    )
    assert world_map["resourceWorldStateSnapshotStatus"] == (
        "private-diagnostic-exact-same-object-BaseResourceItemBoxSnapshot"
    )
    assert all(
        row["objectType"] in {52, 57}
        and isinstance(row["tick"], int)
        and isinstance(row["objectId"], int)
        and isinstance(row["isCollected"], bool)
        and isinstance(row["remainCollectCount"], int)
        and row["wireStatus"]
        == "decoded-exact-same-object-BaseResourceItemBoxSnapshot"
        for row in world_map["resourceWorldStateSnapshots"]
    )
    for row in world_map["events"]:
        if row.get("kind") not in {"tree-of-life", "meteor"}:
            continue
        assert row["endStatus"] in {
            "decoded-exact-CmdDestroy-for-same-resource-object",
            "decoded-exact-same-resource-object-collected-snapshot-at-first-observation",
            "unavailable-no-exact-resource-collection-lifecycle-visible-until-match-end",
        }
        if row["endStatus"] in {
            "decoded-exact-CmdDestroy-for-same-resource-object",
            "decoded-exact-same-resource-object-collected-snapshot-at-first-observation",
        }:
            assert row["endTick"] <= data["meta"]["lastTick"]
        else:
            assert row["endTick"] == data["meta"]["lastTick"] + 1
    for animal in wildlife:
        assert animal["movementAnchorCount"] == len(animal["movementTrack"])
        assert animal["movementTrack"] == sorted(
            animal["movementTrack"], key=lambda row: row[0]
        )
        assert all(
            len(row) >= 3
            and isinstance(row[0], int)
            and all(
                isinstance(value, (int, float)) and math.isfinite(value)
                for value in row[1:3]
            )
            for row in animal["movementTrack"]
        )
        assert animal["movementStatus"] == "exact-anchor-held-until-next-observation"
        assert animal["displayPositionAnchorCount"] == len(
            animal["displayPositionTrack"]
        )
        assert animal["displayPositionTrack"] == sorted(
            animal["displayPositionTrack"], key=lambda row: row[0]
        )
        assert isinstance(animal["displayPositionStatus"], str)
        assert animal["displayPositionStatus"]
        assert bool(animal["displayPositionTrack"]) != (
            animal["displayPositionStatus"] == "unavailable-no-position-evidence"
        )
        assert animal["mapPositionAnchorCount"] == len(animal["mapPositionTrack"])
        assert animal["mapPositionTrack"] == sorted(
            animal["mapPositionTrack"], key=lambda row: row[0]
        )
        assert animal["mapMovementMode"] in {
            "crow-held-observed-track",
            "special-mobile-observed-track",
            "fixed-scratch-group-center",
            "fixed-first-supported-position",
            "unavailable-no-position-evidence",
        }
        if animal["assetKey"] in {"raven", "mutant-raven"}:
            assert animal["mapMovementMode"] == "crow-held-observed-track"
        elif animal["assetKey"] in {"bori", "bori-box"}:
            assert animal["mapMovementMode"] == "special-mobile-observed-track"
            assert animal["mapPositionAnchorCount"] > 0
        elif animal["displayGroupCenter"] is not None:
            assert animal["mapMovementMode"] == "fixed-scratch-group-center"
            assert animal["mapPositionAnchorCount"] == 1
        elif animal["mapPositionTrack"]:
            assert animal["mapMovementMode"] == "fixed-first-supported-position"
            assert animal["mapPositionAnchorCount"] == 1
        assert animal["deathTick"] is None or animal["deathTick"] >= animal["spawnTick"]
        assert animal["destroyTick"] is None or animal["destroyTick"] >= animal["spawnTick"]
        assert animal["despawnTick"] is None or animal["despawnTick"] >= animal["spawnTick"]

    notifications = data["noiseNotifications"]
    assert data["meta"]["supportedNoiseNotificationCount"] == len(notifications)
    assert data["dataSources"]["timelineEvents"]["supportedNoiseNotificationCount"] == len(
        notifications
    )
    assert [row["tick"] for row in notifications] == sorted(
        row["tick"] for row in notifications
    )
    assert not any("Cnot" in row["noiseTypeName"] for row in notifications)
    assert all(
        row["wireStatus"] == "decoded-exact-source-derived-recipient-rule"
        and isinstance(row["sourcePosition"], list)
        and len(row["sourcePosition"]) == 2
        and all(isinstance(value, (int, float)) and math.isfinite(value) for value in row["sourcePosition"])
        for row in notifications
    )
    tactical_pings = data["tacticalPings"]
    assert data["meta"]["tacticalPingCount"] == len(tactical_pings)
    assert data["dataSources"]["timelineEvents"]["tacticalPingCount"] == len(
        tactical_pings
    )
    assert tactical_pings == sorted(tactical_pings, key=lambda row: row["tick"])
    assert all(
        row["wireStatus"] == "decoded-exact-tactical-ping"
        and isinstance(row["type"], int)
        and isinstance(row["position"], list)
        and len(row["position"]) == 2
        and all(
            isinstance(value, (int, float)) and math.isfinite(value)
            for value in row["position"]
        )
        for row in tactical_pings
    )
    phase_clock = data["phaseClock"]
    restriction_updates = phase_clock["restrictionUpdates"]
    gameplay_updates = phase_clock["gamePlayPhaseUpdates"]
    assert phase_clock["status"] == "decoded-exact-day-night-phase-and-remain-time"
    assert data["meta"]["restrictionClockUpdateCount"] == len(restriction_updates)
    assert data["dataSources"]["timelineEvents"][
        "restrictionClockUpdateCount"
    ] == len(restriction_updates)
    assert restriction_updates == sorted(
        restriction_updates, key=lambda row: row["tick"]
    )
    assert gameplay_updates == sorted(gameplay_updates, key=lambda row: row["tick"])
    assert all(
        isinstance(row["day"], int)
        and isinstance(row["phase"], int)
        and isinstance(row["remainSeconds"], (int, float))
        and row["remainSeconds"] >= 0
        and row["wireStatus"] == "decoded-exact-restricted-area-clock"
        for row in restriction_updates
    )
    for source in data["sources"]:
        label = source["file"]
        assert isinstance(label, str) and label
        assert not Path(label).is_absolute()
        assert ".." not in Path(label).parts

    if html is not None:
        assert f"game {game_id} · client {data['meta']['clientVersion']}" in html
        assert data["meta"]["matchModeLabel"] in html
        assert "__GAME_ID__" not in html
        assert "__CLIENT_VERSION__" not in html
        assert "__DATA__" not in html
        assert "const data=" in html
        assert "CmdFinishGameResult" in html
        assert "mapCanvas" in html
        assert "fetch(" not in html
        assert "XMLHttpRequest" not in html
        assert "WebSocket" not in html

    return {
        "status": "ok",
        "gameId": game_id,
        "clientVersion": data["meta"]["clientVersion"],
        "players": player_count,
        "selectedEvents": event_count,
        "movementAnchors": movement_count,
    }


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Validate a generic replay combat analysis")
    parser.add_argument("--json", required=True, type=Path)
    parser.add_argument("--html", required=True, type=Path)
    parser.add_argument("--expected-game-id", type=int, default=None)
    args = parser.parse_args(argv)
    print(
        json.dumps(
            validate_analysis(
                args.json.resolve(),
                args.html.resolve(),
                args.expected_game_id,
            ),
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
