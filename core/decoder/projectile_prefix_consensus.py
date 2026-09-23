"""Aggregate anonymous replay projectile-prefix candidates by client version.

One match can expose a plausible nested spawn prefix, but cannot establish a
version layout.  This module requires the same object-type/member-header pair
in several distinct matches and keeps the result as a review candidate.  It
never mutates the parser's promoted version table.
"""

from __future__ import annotations

from collections import Counter, defaultdict


DEFAULT_MINIMUM_MATCHES = 3


def build_projectile_prefix_consensus(
    games: list[dict], *, minimum_matches: int = DEFAULT_MINIMUM_MATCHES
) -> dict:
    if not isinstance(games, list) or minimum_matches < 2:
        raise ValueError("invalid projectile prefix consensus input")
    by_version: dict[str, list[dict]] = defaultdict(list)
    seen_games: set[tuple[str, int]] = set()
    unavailable_count = 0
    for game in games:
        if not isinstance(game, dict):
            raise ValueError("projectile prefix game row is invalid")
        game_id = game.get("gameId")
        version = game.get("clientVersion")
        probe = game.get("projectilePrefixCandidateProbe")
        if not isinstance(game_id, int) or game_id <= 0 or not isinstance(version, str):
            raise ValueError("projectile prefix game identity is invalid")
        identity = (version, game_id)
        if identity in seen_games:
            raise ValueError("projectile prefix consensus contains a duplicate game")
        seen_games.add(identity)
        if (
            not isinstance(probe, dict)
            or probe.get("status") != "exact-schema-decoded-candidates-not-promoted"
            or probe.get("clientVersion") != version
            or probe.get("fallbackUsed") is not False
        ):
            unavailable_count += 1
            continue
        runtime = probe.get("runtime")
        rows = runtime.get("candidateObjectTypes") if isinstance(runtime, dict) else None
        if not isinstance(rows, list):
            raise ValueError("projectile prefix candidate rows are missing")
        by_version[version].append({"gameId": game_id, "rows": rows})

    versions = []
    for version, matches in sorted(by_version.items()):
        by_object_type: dict[int, list[dict]] = defaultdict(list)
        for match in matches:
            seen_types: set[int] = set()
            for row in match["rows"]:
                object_type = row.get("objectType")
                header = row.get("memberHeader")
                count = row.get("occurrenceCount")
                if (
                    not isinstance(object_type, int)
                    or object_type < 0
                    or object_type in seen_types
                    or not isinstance(header, int)
                    or not 0 <= header <= 255
                    or not isinstance(count, int)
                    or count < 3
                    or row.get("exactProjectileCodeCount") != count
                    or row.get("positiveOwnerCount") != count
                    or not 0 < row.get("knownOwnerCount", 0) <= count
                    or row.get("collisionLinkedObjectCount", 0) <= 0
                    or row.get("fallbackUsed") is not False
                ):
                    raise ValueError("projectile prefix candidate row is invalid")
                seen_types.add(object_type)
                by_object_type[object_type].append(row)

        object_rows = []
        candidate_layout: dict[str, int] = {}
        for object_type, rows in sorted(by_object_type.items()):
            headers = Counter(row["memberHeader"] for row in rows)
            stable = len(headers) == 1
            enough = len(rows) >= minimum_matches
            ready = stable and enough
            header = next(iter(headers)) if stable else None
            if ready:
                candidate_layout[str(object_type)] = header
            object_rows.append({
                "objectType": object_type,
                "supportingMatchCount": len(rows),
                "totalOccurrenceCount": sum(row["occurrenceCount"] for row in rows),
                "memberHeader": header,
                "observedMemberHeaders": dict(sorted(headers.items())),
                "collisionLinkedObjectCount": sum(
                    row.get("collisionLinkedObjectCount", 0) for row in rows
                ),
                "status": (
                    "candidate-layout-consensus-ready-for-explicit-review"
                    if ready
                    else "insufficient-or-inconsistent-multi-match-evidence"
                ),
                "fallbackUsed": False,
            })
        all_observed_ready = bool(object_rows) and all(
            row["status"] == "candidate-layout-consensus-ready-for-explicit-review"
            for row in object_rows
        )
        versions.append({
            "clientVersion": version,
            "exactCandidateMatchCount": len(matches),
            "minimumMatches": minimum_matches,
            "observedCandidateObjectTypeCount": len(object_rows),
            "objectTypes": object_rows,
            "candidateLayout": candidate_layout,
            "status": (
                "candidate-layout-consensus-ready-for-explicit-version-promotion"
                if len(matches) >= minimum_matches and all_observed_ready
                else "insufficient-or-inconsistent-multi-match-evidence"
            ),
            "promotionStatus": "not-promoted",
            "coverageCaveat": (
                "Only object types with at least three qualifying spawns in at "
                "least one input match can appear; absence is not proof that a "
                "projectile object type does not exist."
            ),
            "fallbackUsed": False,
        })
    return {
        "format": "er-projectile-prefix-multi-match-consensus.v1",
        "status": "candidate-consensus-only-not-promoted",
        "minimumMatchesPerObjectType": minimum_matches,
        "inputGameCount": len(games),
        "unavailableGameCount": unavailable_count,
        "versions": versions,
        "fallbackUsed": False,
    }


def validate_projectile_prefix_consensus(report: dict) -> None:
    if (
        report.get("format") != "er-projectile-prefix-multi-match-consensus.v1"
        or report.get("status") != "candidate-consensus-only-not-promoted"
        or report.get("fallbackUsed") is not False
    ):
        raise ValueError("projectile prefix consensus header is invalid")
    versions = report.get("versions")
    if not isinstance(versions, list):
        raise ValueError("projectile prefix consensus versions are invalid")
    for version in versions:
        rows = version.get("objectTypes")
        layout = version.get("candidateLayout")
        if (
            not isinstance(rows, list)
            or not isinstance(layout, dict)
            or version.get("promotionStatus") != "not-promoted"
            or version.get("fallbackUsed") is not False
        ):
            raise ValueError("projectile prefix consensus version is invalid")
        ready_layout = {
            str(row["objectType"]): row["memberHeader"]
            for row in rows
            if row.get("status")
            == "candidate-layout-consensus-ready-for-explicit-review"
        }
        if layout != ready_layout:
            raise ValueError("projectile prefix consensus layout is inconsistent")
