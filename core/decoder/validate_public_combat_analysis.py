from __future__ import annotations

import argparse
import json
import math
from pathlib import Path

from build_public_combat_analysis import (
    BORI_GRADE_ASSET_KEYS,
    BORI_GRADE_LABELS,
    DEFAULT_DELETION_CONTACT_URL,
    MOVEMENT_PING_LABELS,
    MOVEMENT_PING_DISPLAY_SECONDS,
    RIFT_MAP_CALIBRATION_BY_CLIENT_VERSION,
    TACTICAL_PING_ASSET_KEYS,
    TACTICAL_PING_DISPLAY_SECONDS,
    TEAM_COMBAT_LOG_DISPLAY_SECONDS,
    UNKNOWN_WILDLIFE_MARKER_COLOR,
    assert_public_catalog,
)


def validate_public_analysis_object(
    data: dict,
    html: str,
    private_data: dict | None = None,
) -> dict:
    assert_public_catalog(data)

    players = data["players"]
    event_areas = data["eventAreas"]
    assert event_areas["status"] == (
        "decoded-exact-counts-with-derived-name-categories"
    )
    assert event_areas["categoryAuthority"] == "derived-name-rule-v1"
    assert len(event_areas["areas"]) == 8
    assert {area["key"] for area in event_areas["areas"]} == {
        "combat",
        "skill",
        "state-stat",
        "movement",
        "inventory-item",
        "world-object",
        "communication-ui",
        "system-other",
    }
    assert event_areas["packetPayloadCount"] == sum(
        area["eventCount"] for area in event_areas["areas"]
    )
    assert event_areas["packetTypeCount"] == sum(
        area["packetTypeCount"] for area in event_areas["areas"]
    )
    ping = event_areas["ping"]
    assert ping["exactCount"] == ping["eventCount"]
    assert ping["status"] in {"decoded-exact-fields", "not-observed"}
    assert data["meta"]["playerCount"] == len(players)
    capabilities_meta = data["characterCapabilities"]
    assert capabilities_meta["status"] == (
        "exact-replay-version-official-gameDb-character-capabilities"
    )
    assert capabilities_meta["replayClientVersion"] == data["meta"]["clientVersion"]
    assert capabilities_meta["fallbackUsed"] is False
    assert capabilities_meta["catalogCharacterCount"] >= len(players)
    assert capabilities_meta["interpretationBoundary"] == (
        "static-base-capability-not-runtime-source-or-final-duration"
    )
    combat_summary = data["combatJudgment"]
    assert combat_summary["status"] == (
        "derived-exact-combat-state-and-target-selection-v1"
    )
    assert combat_summary["fallbackUsed"] is False
    assert combat_summary["playerCount"] == len(players)
    assert combat_summary["teamFocusWindowSeconds"] == 2
    assert data["growthTempo"]["status"] == (
        "derived-exact-snapshot-and-equipment-growth-v1"
    )
    assert data["growthTempo"]["playerCount"] == len(players)
    assert data["growthTempo"]["fallbackUsed"] is False
    assert data["objectivePreparation"]["status"] == (
        "derived-exact-objective-clock-and-held-anchor-distance-v1"
    )
    assert data["objectivePreparation"]["playerCount"] == len(players)
    assert data["objectivePreparation"]["preparationRadiusMeters"] == 20
    assert data["objectivePreparation"]["fallbackUsed"] is False
    assert data["skillOperation"]["status"] == (
        "derived-exact-confirmed-pvp-skill-sequence-v1"
    )
    assert data["skillOperation"]["playerCount"] == len(players)
    assert data["skillOperation"]["chainWindowSeconds"] == 3
    assert data["skillOperation"]["fallbackUsed"] is False
    assert data["sceneCoaching"]["status"] == "derived-exact-scene-coaching-v1"
    assert data["sceneCoaching"]["fallbackUsed"] is False
    assert data["sceneCoaching"]["playerCount"] == len(players)
    assert data["deathReview"]["status"] == (
        "derived-exact-event-and-held-position-death-review-v1"
    )
    assert data["deathReview"]["playerCount"] == len(players)
    assert data["deathReview"]["contextSeconds"] == 5
    assert data["deathReview"]["targetWindowSeconds"] == 4
    assert data["deathReview"]["nearbyRadiusMeters"] == 30
    assert data["deathReview"]["collapseWindowSeconds"] == 15
    assert data["deathReview"]["fallbackUsed"] is False
    movement_count = 0
    path_count = 0
    path_node_count = 0
    for player in players:
        capabilities = player["characterCapabilities"]
        evidence = player["observedCapabilityEvidence"]
        assert capabilities["characterCode"] == player["characterCode"]
        assert capabilities["fallbackUsed"] is False
        assert capabilities["interpretationBoundary"] == (
            "static-base-capability-not-runtime-source-or-final-duration"
        )
        assert capabilities["coverageStatus"] in {
            "exact-static-definitions-present",
            "unavailable-no-character-skill-or-state-definition",
        }
        assert all(
            isinstance(capabilities[key], list)
            for key in (
                "crowdControlStates",
                "shieldStates",
                "namedHealingStates",
                "defensiveStateTypes",
                "movementSkills",
                "skillProfiles",
            )
        )
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
        judgment = player["combatJudgment"]
        episodes = judgment["episodes"]
        assert judgment["status"] == (
            "derived-exact-combat-state-and-target-selection-v1"
        )
        assert judgment["fallbackUsed"] is False
        assert judgment["styleAuthority"] == "derived-team-relative-v1"
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
        assert judgment["teamFocusFollowupCount"] == sum(
            row["teamFocusFollowupCount"] for row in episodes
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
            and row["enemyTargeterCount"] <= row["enemyTargetActionCount"]
            and row["crowdControlCount"]
            == sum(item["count"] for item in row["crowdControlReceived"])
            and all(
                tool["status"]
                == "exact-replay-version-static-capability-counts"
                for tool in row["nearbyEnemyStaticTools"]
            )
            for row in death_rows
        )
        growth = player["growthTempo"]
        assert growth["status"] == "derived-exact-snapshot-and-equipment-growth-v1"
        assert growth["fallbackUsed"] is False
        assert growth["styleAuthority"] == "derived-team-relative-v1"
        assert [row["level"] for row in growth["levelMilestones"]] == [
            6, 9, 12, 15, 18, 20,
        ]
        assert isinstance(growth["finalMasteryLevel"], int)
        assert growth["finalMasteryLevel"] >= 0
        objective = player["objectivePreparation"]
        assert objective["status"] == (
            "derived-exact-objective-clock-and-held-anchor-distance-v1"
        )
        assert objective["fallbackUsed"] is False
        assert objective["objectiveCount"] == len(objective["objectives"])
        assert (
            objective["beforeActiveCount"]
            + objective["afterActiveCount"]
            + objective["notObservedCount"]
            == objective["objectiveCount"]
        )
        operation = player["skillOperation"]
        assert operation["status"] == "derived-exact-confirmed-pvp-skill-sequence-v1"
        assert operation["fallbackUsed"] is False
        assert operation["pvpSkillStartCount"] == sum(
            row["skillStartCount"] for row in operation["episodes"]
        )
        assert operation["pvpNormalAttackStartCount"] == sum(
            row["normalAttackStartCount"] for row in operation["episodes"]
        )
        coaching = player["sceneCoaching"]
        assert coaching["status"] == "derived-exact-scene-coaching-v1"
        assert coaching["fallbackUsed"] is False
        assert coaching["nearbyRadiusMeters"] == 30
        assert coaching["earlyCombatSeconds"] == 120
        assert coaching["feedback"]["earlyCombatSeconds"] == 120
        assert coaching["feedback"]["laterEpisodeCount"] == sum(
            not row.get("earlyCombat") for row in coaching["episodes"]
        )
        cutoff = data["meta"]["firstTick"] + 120 * data["meta"]["targetFrameRate"]
        picked = {
            row["teamEpisodeNumber"]: row
            for row in coaching["episodes"]
        }
        for number in coaching["pickedEpisodeNumbers"]:
            scene = picked[number]
            assert scene["entryTick"] >= cutoff
            assert scene["earlyCombat"] is False
        assert isinstance(coaching["feedback"]["nextPlay"]["headline"], str)
        assert coaching["feedback"]["nextPlay"]["headline"]
        assert "2분" in coaching["feedback"]["nextPlay"]["excludedNote"]
        hud = player["mapCombatHud"]
        assert hud["status"] == (
            "derived-exact-pvp-damage-completeness-and-runtime-cc-duration-v3"
        )
        assert hud["fallbackUsed"] is False
        assert hud["ccAppliedStatus"] in {
            "exact-only-with-resolved-enemy-caster-and-complete-state-lifecycle",
            "unavailable-missing-exact-gameDb-or-state-event-authority",
        }
        assert hud["damageStatus"] == (
            "exact-only-when-all-resolved-pvp-CmdDamage-values-are-numeric"
        )
        assert hud["ccStatus"] in {
            "exact-gameDb-cc-state-runtime-lifecycle-in-replay-ticks",
            "unavailable-missing-exact-gameDb-or-state-event-authority",
        }
        assert (
            hud["ccStatus"]
            == "exact-gameDb-cc-state-runtime-lifecycle-in-replay-ticks"
            and hud["ccAppliedStatus"]
            == "exact-only-with-resolved-enemy-caster-and-complete-state-lifecycle"
        ) or hud["ccStatus"] == hud["ccAppliedStatus"]
        assert hud["ccTimeBase"] == "replay-ticks-divide-60"
        assert hud["damageSampleFields"] == [
            "tick", "knownDamageDealt", "knownDamageTaken",
            "missingDamageDealtPackets", "missingDamageTakenPackets",
        ]
        assert hud["nearbyRadiusMeters"] == 30
        assert all(
            row["damageSamples"][0][0] == row["startTick"]
            and len(row["damageSamples"][0]) == 5
            and all(
                isinstance(span, list)
                and len(span) == 2
                and row["startTick"] <= span[0] < span[1] <= row["endTick"]
                for key in (
                    "receivedCcSpans", "appliedCcSpans",
                    "receivedCcUnavailableSpans", "appliedCcUnavailableSpans",
                )
                for span in row[key]
            )
            for row in hud["intervals"]
        )
        if hud["ccStatus"] == (
            "unavailable-missing-exact-gameDb-or-state-event-authority"
        ):
            assert all(
                row["receivedCcUnavailableSpans"]
                == [[row["startTick"], row["endTick"]]]
                and row["appliedCcUnavailableSpans"]
                == [[row["startTick"], row["endTick"]]]
                for row in hud["intervals"]
            )
        assert [(row["startTick"], row["endTick"]) for row in coaching["episodes"]] == [
            (row["startTick"], row["endTick"]) for row in judgment["personalEpisodes"]
        ]
        assert len(coaching["deaths"]) == death_review["deathCount"]
        assert all(
            row["verdictId"] in {
                "near-simultaneous-normal",
                "supported-first-entry",
                "calculated-low-hp-isolated-enemy",
                "unsupported-isolated-entry",
                "late-after-teammate-down",
                "insufficient-evidence",
            }
            and row["confidence"] in {
                "confirmed", "observed", "interpreted", "unavailable"
            }
            and row["fallbackUsed"] is False
            and 1 <= len(row["evidence"]) <= 4
            for row in coaching["episodes"]
        )
        assert coaching["wildlifeFlow"]["fallbackUsed"] is False
        assert isinstance(coaching["wildlifeFlow"]["story"], str) and coaching["wildlifeFlow"]["story"]
        result = player["result"]
        track = player["movementTrack"]
        movement_count += len(track)
        path_count += len(player["plannedPathCommands"])
        path_node_count += sum(len(row[2]) for row in player["plannedPathCommands"])
        assert player["movementAnchorCount"] == len(track) > 0
        assert track == sorted(track, key=lambda row: row[0])
        assert all(
            len(row) >= 3
            and isinstance(row[0], int)
            and all(isinstance(value, (int, float)) and math.isfinite(value) for value in row[1:3])
            for row in track
        )
        assert player["plannedPathCommandCount"] == len(player["plannedPathCommands"])
        assert player["plannedPathNodeCount"] == sum(
            len(row[2]) for row in player["plannedPathCommands"]
        )
        assert result["damageToPlayer"] == sum(
            result[f"damageToPlayer_{kind}"]
            for kind in ("trap", "basic", "skill", "itemSkill", "direct")
        )
        assert result["damageFromPlayer"] == sum(
            result[f"damageFromPlayer_{kind}"]
            for kind in ("trap", "basic", "skill", "itemSkill", "direct")
        )
        assert all(
            0 <= row["readyCombatRatio"] <= 1
            for row in player["cooldowns"]
            if row["readyCombatRatio"] is not None
        )
        assert player["skillCooldownTimelineStatus"] == (
            "decoded-exact-cooldown-packets-with-derived-clock"
        )
        assert player["skillCooldownTimeline"] == sorted(
            player["skillCooldownTimeline"], key=lambda row: row[0]
        )
        assert all(
            row[1] == "WeaponSkill"
            for row in player["skillStartTimeline"]
            if 3_000_000 <= row[3] < 4_000_000
        )

    assert data["map"]["movementAnchorCount"] == movement_count
    assert data["map"]["plannedPathCommandCount"] == path_count
    assert data["map"]["plannedPathNodeCount"] == path_node_count
    rift_status_version = (
        RIFT_MAP_CALIBRATION_BY_CLIENT_VERSION
        .get(data.get("meta", {}).get("clientVersion"), {})
        .get("statusVersion")
    )
    for space in data["map"].get("secondarySpaces", []):
        if space.get("spaceId") == "azure-1080":
            from decoder.azure_map_space import validate_azure_space
            validate_azure_space(space, data["meta"]["clientVersion"])
            continue
        assert space["status"] == (
            f"calibrated-{rift_status_version}-rift-space-with-exact-rift-packets"
        )
        assert space["backgroundStatus"] == (
            "hash-verified-client-rift-art-with-pixel-validated-"
            f"{rift_status_version}-coordinate-registration"
        )
        assert space["backgroundAssetKey"] == "rift-map-background"
        assert space["image"] == {"w": 363, "h": 364}
        assert space["observedAnchorCount"] >= 40
        assert space["exactRiftBattleStatePacketCount"] > 0
        assert len(space["teamNumbers"]) >= 1
        assert space["projection"]["type"] == (
            f"calibrated-rift-world-to-client-pixel-{rift_status_version}.v1"
        )
        assert space["projection"]["formula"] == (
            "pixelX=178+3.48*(-localX+localZ); "
            "pixelY=178+3.48*(-localX-localZ)"
        )
        assert space["projection"]["originWorld"] in (
            [-188.0, 397.0], [-8.0, 397.0]
        )
        assert space["projection"]["pixelOrigin"] == [178.0, 178.0]
        assert space["projection"]["pixelPerWorldUnit"] == 3.48
        assert space["projection"]["validationStatus"] == (
            RIFT_MAP_CALIBRATION_BY_CLIENT_VERSION[data["meta"]["clientVersion"]]["validationStatus"]
        )
    wildlife = data["wildlife"]
    assert wildlife["instanceCount"] == len(wildlife["instances"])
    assert wildlife["deadGhostSeconds"] == 0
    assert wildlife["renderMode"] == "individual-living-instance-markers"
    assert wildlife["groupCount"] == len({
        row["publicWildlifeGroupId"] for row in wildlife["instances"]
    })
    assert wildlife["movementAnchorCount"] == sum(
        row["movementAnchorCount"] for row in wildlife["instances"]
    )
    assert wildlife["displayPositionAnchorCount"] == sum(
        row["displayPositionAnchorCount"] for row in wildlife["instances"]
    )
    assert wildlife["positionedInstanceCount"] == sum(
        bool(row["displayPositionTrack"]) for row in wildlife["instances"]
    )
    assert wildlife["mapPositionAnchorCount"] == sum(
        row["mapPositionAnchorCount"] for row in wildlife["instances"]
    )
    assert all(
        row["publicWildlifeId"] == index
        and row["publicLabel"] == f"W{index:04d}"
        for index, row in enumerate(wildlife["instances"], start=1)
    )
    assert wildlife["assets"]["status"] == "not-embedded-shape-markers-only"
    assert wildlife["assets"]["icons"] == {}

    def valid_bori_marker(row: dict) -> bool:
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
        expected_timeline = [[row["spawnTick"], "bori-base"]]
        if grade is None:
            return (
                row.get("mapMarkerAssetKey") == "bori-base"
                and row.get("boriGradeLabel") is None
                and row.get("boriGradeStatus")
                == "unavailable-no-unique-Bori-lifecycle-grade-snapshot"
                and reveal_tick is None
                and row.get("mapMarkerAssetTimeline") == expected_timeline
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
            and row.get("mapMarkerAssetTimeline") == expected_timeline
        )

    assert all(
        row["mapPositionAnchorCount"] == len(row["mapPositionTrack"])
        and row["mapPositionTrack"] == sorted(
            row["mapPositionTrack"], key=lambda anchor: anchor[0]
        )
        and row["mapMovementMode"] in {
            "crow-held-observed-track",
            "special-mobile-observed-track",
            "fixed-scratch-group-center",
            "fixed-first-supported-position",
            "unavailable-no-position-evidence",
        }
        and row["mapMarkerStyle"] == (
            "neutral-monster-red-dot"
            if row["monsterCode"] == 11
            else "special-icon"
            if row["monsterCode"] == 22
            else "mutant-purple-outline"
            if row["mutated"]
            else "species-triangle"
        )
        and row["monsterCode"] != 21
        and valid_bori_marker(row)
        and row["mapMarkerColorStatus"] in {
            "user-confirmed-neutral-monster-red",
            "user-confirmed-species-palette",
            "hash-verified-client-special-marker",
            "unavailable-no-exact-species-marker-palette",
        }
        and isinstance(row.get("mapMarkerColor"), str)
        and (
            row["mapMarkerColorStatus"]
            != "unavailable-no-exact-species-marker-palette"
            or row["mapMarkerColor"] == UNKNOWN_WILDLIFE_MARKER_COLOR
        )
        and row["mapMarkerPositionMeaning"] in {
            "presentation-only-separated-shared-group-center",
            "same-as-map-position-track",
        }
        for row in wildlife["instances"]
    )
    character_icons = data["characterAssets"]["icons"]
    assert all(str(player["characterCode"]) in character_icons for player in players)
    assert all(
        isinstance(row["sourceAssetFolder"], int)
        and row["sourceAssetFolder"] > 0
        and row["status"] == "hash-verified-character-code-to-fankit-folder-icon"
        for row in character_icons.values()
    )
    item_assets = data["itemAssets"]
    assert set(item_assets["icons"]) | set(item_assets["unavailable"]) == set(
        data["itemCatalog"]
    )
    assert all(
        row.get("itemNameStatus")
        in {
            "exact-local-name-map",
            "exact-item-asset-provenance-name",
            "unavailable-no-localized-item-name",
        }
        and (
            row.get("itemName") is None
            or isinstance(row.get("itemName"), str)
        )
        for row in data["itemCatalog"].values()
    )
    assert not (set(item_assets["icons"]) & set(item_assets["unavailable"]))
    skill_icons = data["skillAssets"]["icons"]
    assert all(str(player["characterCode"]) in skill_icons for player in players)
    assert all(row["slots"] for row in skill_icons.values())
    utility_assets = data["utilitySkillAssets"]
    weapon_icons = utility_assets["weapon"]["icons"]
    weapon_overrides = utility_assets["weapon"]["characterOverrides"]
    weapon_unavailable = utility_assets["weapon"]["unavailable"]
    tactical_icons = utility_assets["tactical"]["icons"]
    tactical_unavailable = utility_assets["tactical"]["unavailable"]
    for player in players:
        character_code = str(player["characterCode"])
        mastery_type = str(player["result"].get("bestWeapon"))
        tactical_group = str(player["result"].get("tacticalSkillGroup"))
        assert (
            character_code in weapon_overrides
            or mastery_type in weapon_icons
            or mastery_type in weapon_unavailable
        )
        assert tactical_group in tactical_icons or tactical_group in tactical_unavailable
    pings = data["tacticalPings"]
    assert pings["selectedPlayerFilter"] is False
    assert pings["automaticNoiseIncluded"] is False
    assert pings["hyperloopPingIncluded"] is False
    assert pings["count"] == len(pings["items"])
    assert pings["displaySeconds"] == TACTICAL_PING_DISPLAY_SECONDS
    assert all(row["type"] not in {11, 14, 15} for row in pings["items"])
    assert pings["items"] == sorted(
        pings["items"], key=lambda row: (row["tick"], row["publicPingId"])
    )
    assert all(
        row["label"]
        and row["assetKey"] == TACTICAL_PING_ASSET_KEYS.get(row["type"])
        and row["assetStatus"] == "hash-verified-client-map-ping"
        and row["assetKey"] in data["mapMarkerAssets"]["icons"]
        and row["sourceClass"] == "player-issued-tactical-ping"
        for row in pings["items"]
    )
    assert all(
        row["kind"] in MOVEMENT_PING_LABELS
        and row["label"] == MOVEMENT_PING_LABELS[row["kind"]]
        and row["assetKey"] == "movement-ping"
        and row["sourceClass"] == "automatic-movement-notice"
        for row in data["worldMap"]["movementPings"]
    )
    phase_clock = data["phaseClock"]
    assert phase_clock["status"] == "decoded-exact-day-night-phase-and-remain-time"
    assert phase_clock["restrictionUpdates"] == sorted(
        phase_clock["restrictionUpdates"], key=lambda row: row["tick"]
    )
    if data["meta"]["matchMode"] == "cobalt":
        assert data["map"]["playbackStatus"] == "unavailable-mode-specific-map-and-bounds"
        assert data["map"]["imageDataUrl"] == ""
    else:
        assert data["map"]["playbackStatus"] == "reference-lumia-map-available"
        assert data["map"]["imageDataUrl"].startswith("data:image/png;base64,")
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
        assert data["map"]["projection"]["type"] == "affine-world-xz-to-source-pixel"
        assert (
            data["worldMap"]["movementPingDisplaySeconds"]
            == MOVEMENT_PING_DISPLAY_SECONDS
        )
        assert all(
            row.get("visibleEndStatus")
            in {
                "decoded-exact-CmdDestroy",
                "decoded-exact-CmdDestroy-before-expireTimer",
                "decoded-exact-CmdDead",
                "derived-exact-first-seen-plus-expireTimer-before-CmdDestroy",
                "derived-exact-first-seen-plus-expireTimer-no-CmdDestroy",
                "unavailable-no-visible-end-evidence",
            }
            and (
                row.get("visibleEndTick") is None
                or row["visibleEndTick"] >= row["firstSeenTick"]
            )
            for row in data["worldMap"]["staticObjects"]
        )
        assert all(
            row.get("assetKey") in {
                "lumi-normal", "lumi-battle", "lumi-credit-rich"
            }
            and row.get("movementAnchorCount")
            == len(row.get("movementTrack", []))
            and row.get("guideRobotStatus")
            == "decoded-exact-TouringObjectSnapshot-and-GuideRobotSnapshot"
            for row in data["worldMap"]["staticObjects"]
            if row.get("category") == "lumi"
        )

    report_id = data["meta"]["reportId"]
    assert report_id in html
    assert data["meta"]["clientVersion"] in html
    assert data["meta"]["matchModeLabel"] in html
    assert "닉네임·게임 식별 정보 제거" in html
    assert 'data-view="areas"' not in html
    assert "싸움 장면" in html
    assert "시간순으로 확인하기" in html
    assert "성장 흐름" in html
    assert "오브젝트 동선" in html
    assert "플레이 분석" in html
    assert "스킬 기록" in html
    assert "핑 기록" in html
    assert 'data-view="pings"' in html
    assert "선택 플레이어 기준이 아닙니다" in html
    assert (
        "사람이 찍은 핑은 리플레이의 exact 종류와 같은 클라이언트 "
        "지도 아이콘으로 구분합니다" in html
    )
    assert "하이퍼루프 도착과 VLS 착지는 사람이 찍은 핑과 섞지 않고" in html
    assert "<h2>사람이 찍은 핑</h2>" in html
    assert "<h2>자동 이동 알림</h2>" in html
    assert "페이즈 이동" in html
    assert "스킬 적중" in html
    assert "스킬별 적중 기준 확인하기" in html
    assert "발사체 스킬은 발 단위" in html
    assert "우리 팀 현황" in html
    assert "장비·인벤토리·스킬" not in html
    assert "Array.from({length:10}" in html
    assert '<span>장비</span>' not in html
    assert '<span>인벤토리</span>' not in html
    assert '<span>스킬 쿨다운</span>' not in html
    assert "f==='WeaponSkill'?'무기'" in html
    assert "f==='TacticalSkill'?'전술'" in html
    assert "f==='Passive'?'T'" in html
    assert "function utilityVisual(p,family)" in html
    assert "data.utilitySkillAssets.weapon.characterOverrides" in html
    assert "data.utilitySkillAssets.tactical.icons" in html
    assert "<b>P</b>" not in html
    assert "function cooldownStatesAt" in html
    assert "status=hit?'—':'미사용'" in html
    assert "count:hit?`${hit.count}회`:'0회',last:''" in html
    assert "last:hit?clock(hit.lastTick):''" not in html
    assert ".sk .last{display:none}" in html
    assert "최근 ${clock(hit.lastTick)}" not in html
    assert 'data-layer="wildlife"' in html
    assert 'data-layer="pings"' in html
    assert 'data-layer="cameras"' in html
    assert 'data-layer="controlLens"' in html
    assert "function drawMarkerAsset" in html
    assert "function drawDroneMarker" in html
    assert "function drawWorldMarker" in html
    assert "function worldObjectEndTick" in html
    assert "movementPingMarkerCount" in html
    assert "const poly=(points" not in html
    assert "function drawWildlifeMarker(g,animal,x,y,size,alpha,t)" in html
    assert "function wildlifeAssetKeyAt(animal,t)" in html
    assert "drawWildlifeMarker(g,animal,x,y,size,alpha,state.cursor)" in html
    assert 'id="wildlifeGuide" class="map-wildlife-guide"' in html
    assert "<summary>동물 표식</summary>" in html
    assert "보라색 외곽선은 변이체입니다." in html
    assert (
        "['species-triangle','mutant-purple-outline','neutral-monster-red-dot','special-icon'].includes(style)"
        in html
    )
    assert "majorObjectiveCodes.has(Number(animal.monsterCode))" in html
    assert 'id="mapObjectiveAnnounce" class="map-objective-announce"' in html
    assert "majorObjectiveCodes=new Set([7,8,9])" in html
    assert "Number.isFinite(row.deathTick)" in html
    assert "Number.isFinite(row.killerPublicPlayerId)" in html
    assert "objectiveAnnounceTicks=5*data.meta.targetFrameRate" in html
    assert "팀${row.teamNumber} ${esc(row.characterName)}" in html
    assert "updateObjectiveAnnouncements(state.cursor)" in html
    assert "state.wildlifeGuideOpen=wildlifeGuide.open" in html
    assert "max-width:1152px" in html
    assert "<b>${esc(slot)}</b>" not in html
    assert 'id="teamLoadout"' in html
    assert "window.__wildlifeImages" not in html
    assert "window.__wildlifeMasks" not in html
    assert "window.__wildlifeOutlined" not in html
    assert "animal.mapMarkerStyle==='mutant-purple-outline'" in html
    assert "g.strokeStyle=mutant?'#9d6ed1'" in html
    assert "g.lineTo(x-w*.5,y+h*.42)" in html
    assert "mutant-star" not in html
    assert "if(animal.mutated){g.strokeStyle='#b05cff'" not in html

    # map stage order: canvas, then transport, then speed/phase, then the team rail
    assert 'class="stage"' in html
    assert 'class="map-stage-grid"' not in html
    assert (
        html.index('<canvas id="mapCanvas"')
        < html.index('<div id="mapControls">')
        < html.index('<div class="transport">')
        < html.index('<div class="transport2">')
        < html.index('<aside class="panel rail">')
    )
    assert '<input id="time" type="range"' in html
    assert 'class="seg" role="group" aria-label="재생 속도"' in html
    assert "[.5,1,2,4,8]" in html

    # removed map prose, legend, and wildlife/ping summaries
    for removed in (
        "기본 1× 실시간 재생입니다",
        "점선은 이동 명령의 의도 경로입니다",
        "변이체는 이미지 실루엣 자체에",
        "페이즈 남은 시간은 다음 exact",
        "wildlife-legend",
        "wildlife-key",
        'id="wildlifeSummary"',
        'id="wildlifeUnknown"',
        "위치 미확인 생존",
        "변이 · 이미지 외곽선",
        "사망 · 회색 반투명 5초",
        "전체 전술 핑 · 3초",
        "현재 생존 야생동물은 모두 지도에 표시 중",
        "선택 실험체와 같은 팀 2명",
    ):
        assert removed not in html, removed

    # Human tactical pings use the exact per-type client images.  Automatic
    # hyperloop/VLS movement notices remain a separate marker lineage.
    assert "function drawTacticalPing(g,x,y,size,alpha)" not in html
    assert "drawMarkerAsset(g,ping.assetKey,x,y,(17+age)*ms" in html
    assert "drawMarkerAsset(g,ping.assetKey,x,y,12*ms" in html

    # readable persistent team cards
    assert '<div class="gear">' in html
    assert '<div class="bag">' in html
    assert '<div class="skills">' in html
    assert '<div class="util">' in html
    assert 'class="sk sk-util"' in html
    assert 'class="qty"' in html
    assert "--asset-art-scale:1" in html
    assert ".gear .cell .art{width:32px;height:32px;transform:scale(1.14)}" in html
    assert (
        ".bag .cell .art{width:32px;height:32px;"
        "transform:scale(var(--asset-art-scale))}" in html
    )
    assert (
        ".bag{display:grid;grid-template-columns:repeat(5,minmax(0,1fr));"
        "gap:3px;margin-top:8px;background:#050507" in html
    )
    assert "repeat(10,minmax(0,1fr))" not in html
    assert (
        ".pcard:not(.is-selected) .skills,.pcard:not(.is-selected) .util{display:none}"
        in html
    )
    assert (
        "if(p.publicPlayerId===focus.publicPlayerId){const info=skillInfoAt(p,t)"
        in html
    )
    assert (
        ".cell.unobserved,.cell.empty{opacity:1;outline:none;background:#050507;"
        "border-color:rgba(118,121,132,.34)}" in html
    )
    assert (
        ".cell.unobserved,.cell.empty{opacity:1;outline:none;background:#050507;"
        "border-radius:0;box-shadow:none}" not in html
    )
    assert ".cell.unobserved .gap,.cell.empty .gap{display:none}" in html
    assert ".cell .art[hidden]{display:none!important}" in html
    assert "이 실험체의 기본 도구와 실제 관측 기록 보기" in html
    assert (
        ".cell{position:relative;display:grid;place-items:center;"
        "height:32px;background:#050507;border-radius:var(--r-xs);"
        "border:1px solid rgba(118,121,132,.34);overflow:hidden}" in html
    )
    assert '<span class="item-bg" aria-hidden="true" hidden>' in html
    assert ".cell .item-bg{position:absolute;inset:1px;" in html
    assert ".cell.has-item{border-color:var(--grade-border);" in html
    assert "clip-path:inset(1px)" in html
    assert (
        ".cell .art.white-bg-art{filter:url(#item-white-to-transparent);"
        "clip-path:inset(2px)}" in html
    )
    assert (
        ".cell.grade-Epic{--grade-core:rgba(143,94,255,.62);" in html
    )
    assert ".cell.grade-Epic{outline-color:" not in html
    assert ".cell.grade-Uncommon{--grade-core:rgba(64,166,104,.52);" in html
    assert ".cell.grade-Rare{--grade-core:rgba(72,132,218,.56);" in html
    assert ".cell.grade-Legend{--grade-core:rgba(245,184,69,.62);" in html
    assert ".cell.grade-Mythic{--grade-core:rgba(237,80,74,.64);" in html
    assert '<filter id="item-white-to-transparent"' in html
    assert "asset?.backgroundTreatment==='white-to-transparent'" in html
    assert "function normalizeBagArt(ref,code)" not in html
    assert "height:32px;background:#050507" in html
    assert "function itemArtScale(code,meta,asset)" in html
    assert "name.includes('카메라')||name.includes('드론')" in html
    assert "name.includes('스테이크')" in html
    assert "meta?.subType==='Material'||meta?.itemType==='Misc'" in html
    assert "return meta?.itemName||'이름 미확인'" in html
    assert "detail=`${itemGradeKo" not in html
    assert "meta?.itemName" in html
    assert ".bag .cell{height:31px}" not in html
    assert "grid-template-rows:22px 16px 12px" in html
    assert (
        ".sk-util{display:grid;grid-template-columns:28px minmax(0,1fr);"
        "grid-template-rows:1fr;height:36px" in html
    )
    assert ".sk-util .ico img{width:26px;height:26px}" in html
    assert "g.fillText(`팀${p.teamNumber} · ${p.characterName}`" in html
    assert "g.fillText(`T${p.teamNumber}" not in html
    assert ".sk .cd{font-size:12px;font-weight:700" in html
    assert ".sk .n{font-size:9.5px" in html
    assert "font-size:6px" not in html
    assert "font-size:7px" not in html
    assert "font-size:8px" not in html
    assert "function buildTeamRail(team)" in html
    assert "const normalWorldMarkers=[],bossWorldMarkers=[]" in html
    assert html.index("c.__markers=[]") < html.index(
        "for(const marker of normalWorldMarkers)"
    )
    assert html.index("c.__markers=[]") < html.index(
        "for(const marker of bossWorldMarkers)"
    )
    assert "function paintItemCell(ref,item,observed,title)" in html
    assert "String(itemArtScale(code,meta,asset))" in html
    assert "function paintSkillCell(ref,info)" in html
    assert "function updateTeamRail()" in html
    assert "if(state.playing&&now-railLast<200)return" in html
    assert "function renderTeamLoadout" not in html
    assert "paintNow-lastPlaybackPaint<32" not in html
    assert "requestAnimationFrame(advancePlayback)" in html
    assert "now-playbackPaintLast>=1000/60-1" in html
    assert "function wildlifeCandidatesAt(t)" in html
    assert "function wildlifeMarkersAt(t)" in html
    assert (
        "mode.startsWith('crow-')||mode.startsWith('special-mobile-')" in html
    )
    assert "source.startsWith('CmdMove')" in html
    assert "nextSource==='CmdStopMove'" in html
    assert "function drawWildlifeMarker(g,animal,x,y,size,alpha,t)" in html
    assert "function wildlifeGroupsAt(t)" not in html
    assert "c.__droneMarkers=[]" in html
    assert "drawWildlifeMarker(g,animal,x,y,size,alpha,state.cursor)" in html
    assert "row.ownerPublicPlayerId!==state.playerId" in html
    assert "droneMarkerCount" in html
    assert "grayscale(1)" not in html
    assert "function wildlifeScreenOffset(index,count,size)" in html
    assert "function ensureMapBackground(layout,space)" in html
    assert "function drawRiftBackdrop(g,w,h)" not in html
    assert "window.__mapMarkerImages?.[space.backgroundAssetKey]" in html
    assert "size=(w<520?20:27)*ms" in html
    assert "function bindMapNavigation" in html
    assert 'id="mapZoomIn"' in html
    assert data["teamCombatLog"]["displaySeconds"] == TEAM_COMBAT_LOG_DISPLAY_SECONDS
    assert data["teamCombatLog"]["fallbackUsed"] is False
    assert 'id="mapTeamCombatLog"' in html
    assert "function updateTeamCombatLog" in html
    assert "function teamLogRowsAt" in html
    assert 'id="mapCombatHud"' not in html
    assert "function updateCombatHud" not in html
    assert "<dt>준 피해</dt>" not in html
    assert "<dt>받은 CC</dt>" not in html
    assert "<dt>넣은 CC</dt>" not in html
    assert "<dt>경기 전체</dt>" not in html
    assert 'data-k="official"' not in html
    assert "확인 불가" in html
    assert "function updateVisionGap" in html
    assert "감시 카메라 ${fmt(cameraExpected)}개" in html
    assert "정찰·EMP 드론 ${fmt(droneExpected)}회" in html
    assert ".map-team-log{position:absolute;top:7px;right:7px" in html
    assert "max-width:min(186px,46%)" in html
    assert "visible.slice(-3)" in html
    assert "다음에 같은 장면이면" in html
    assert "count:hit?`${hit.count}회`:'0회',last:''" in html
    assert "last:hit?clock(hit.lastTick):''" not in html
    assert "setInterval(" not in html
    assert ".pcard.is-selected::before" in html

    # production dark tokens, focus states, and no heavy gradients
    assert "--bg:oklch(0.13 0.01 280)" in html
    assert "--panel:oklch(0.19 0.012 280)" in html
    assert "--card:oklch(0.225 0.012 280)" in html
    assert "--muted:oklch(0.708 0 0)" in html
    assert "--line:oklch(1 0 0 / 10%)" in html
    assert "grid-template-columns:minmax(0,1fr) 330px" in html
    assert ":focus-visible{outline:2px solid var(--accent)" in html
    assert "@media(prefers-reduced-motion:reduce)" in html
    assert "body{margin:0;background:radial-gradient" not in html
    assert DEFAULT_DELETION_CONTACT_URL in html
    assert "const data=" in html
    assert "publicPlayerId" in html
    assert "objectId" not in html
    assert "userNum" not in html
    assert "userId" not in html
    assert 'data-view="events"' not in html
    assert "playerEventIndexes" not in html
    assert "__REPORT_ID__" not in html
    assert "__CLIENT_VERSION__" not in html
    assert "__MATCH_MODE__" not in html
    assert "__DATA__" not in html
    assert "fetch(" not in html
    assert "XMLHttpRequest" not in html
    assert "WebSocket" not in html

    if private_data is not None:
        private = private_data
        private_game_id = str(private["meta"]["gameId"])
        assert private_game_id not in json.dumps(
            data, ensure_ascii=False, separators=(",", ":")
        )
        assert private_game_id not in html

    return {
        "status": "ok",
        "reportId": report_id,
        "players": len(players),
        "movementAnchors": movement_count,
        "rawEventsIncluded": False,
        "privateIdentifiersIncluded": False,
        "fallbackUsed": False,
    }


def validate_public_analysis(
    json_path: Path,
    html_path: Path,
    private_json_path: Path | None = None,
) -> dict:
    """Validate persisted public files for independent file-based checks."""

    return validate_public_analysis_object(
        json.loads(json_path.read_text(encoding="utf-8")),
        html_path.read_text(encoding="utf-8"),
        json.loads(private_json_path.read_text(encoding="utf-8"))
        if private_json_path is not None
        else None,
    )


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Validate a public anonymous combat report")
    parser.add_argument("--json", required=True, type=Path)
    parser.add_argument("--html", required=True, type=Path)
    parser.add_argument("--private-json", type=Path)
    args = parser.parse_args(argv)
    print(
        json.dumps(
            validate_public_analysis(
                args.json.resolve(),
                args.html.resolve(),
                args.private_json.resolve() if args.private_json else None,
            ),
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
