"""Fail-closed replay layout and match-mode compatibility checks.

This module deliberately contains no fallback mapping.  A replay is decoded
only when its header names a layout that has been structurally validated, and
the finish-result rows all agree on one known matching-mode pair.
"""

from __future__ import annotations

from pathlib import Path


SUPPORTED_CLIENT_VERSIONS = frozenset({"12.2.0", "12.3.0", "12.4.0"})

MODE_BY_WIRE_PAIR = {
    (2, 3): {
        "key": "normal",
        "label": "일반전 스쿼드",
        "analysisStatus": "supported",
        "decoderEnabled": True,
        "mapStatus": "reference-lumia-map-available",
    },
    (3, 3): {
        "key": "rank",
        "label": "랭크 스쿼드",
        "analysisStatus": "supported",
        "decoderEnabled": True,
        "mapStatus": "reference-lumia-map-available",
    },
    (6, 4): {
        "key": "cobalt",
        "label": "코발트 프로토콜",
        "analysisStatus": "supported-combat-map-unavailable",
        "decoderEnabled": True,
        "mapStatus": "unavailable-mode-specific-map-and-bounds",
    },
    (9, 1): {
        "key": "lone-wolf",
        "label": "론 울프",
        "analysisStatus": "unverified-no-retrievable-12.2-fixture",
        "decoderEnabled": False,
        "mapStatus": "reference-lumia-map-available",
    },
}


def read_replay_client_version(path: Path) -> str:
    """Read the fixed replay header version without parsing replay payloads."""

    with path.open("rb") as replay:
        header = replay.read(32)
    if len(header) < 32:
        raise ValueError("invalid-replay-header:shorter-than-32-bytes")
    raw = header[16:32].split(b"\0", 1)[0]
    try:
        version = raw.decode("ascii")
    except UnicodeDecodeError as error:
        raise ValueError("invalid-replay-header:client-version-not-ascii") from error
    if not version:
        raise ValueError("invalid-replay-header:client-version-empty")
    return version


def require_supported_client_version(version: str) -> dict:
    if version not in SUPPORTED_CLIENT_VERSIONS:
        raise ValueError(f"unsupported-client-version:{version}")
    return {
        "status": "exact-supported-layout",
        "clientVersion": version,
        "supportedClientVersions": sorted(SUPPORTED_CLIENT_VERSIONS),
        "fallbackUsed": False,
    }


def classify_match_mode(rows: list[dict], *, client_version: str | None = None) -> dict:
    if not rows:
        raise ValueError("matching-mode-unavailable:no-finish-result-rows")
    pairs = {
        (row.get("matchingMode"), row.get("matchingTeamMode"))
        for row in rows
        if isinstance(row, dict)
    }
    if len(pairs) != 1:
        ordered = sorted((repr(left), repr(right)) for left, right in pairs)
        raise ValueError(f"matching-mode-inconsistent:{ordered}")
    matching_mode, matching_team_mode = next(iter(pairs))
    if not isinstance(matching_mode, int) or not isinstance(matching_team_mode, int):
        raise ValueError("matching-mode-unavailable:non-integer-wire-value")
    if client_version == '12.4.0' and (matching_mode, matching_team_mode) != (3, 3):
        raise ValueError('unsupported-12.4-mode:ranked-squad-only')
    definition = MODE_BY_WIRE_PAIR.get((matching_mode, matching_team_mode))
    if definition is None:
        raise ValueError(
            f"unsupported-matching-mode:{matching_mode}/{matching_team_mode}"
        )
    return {
        **definition,
        "matchingMode": matching_mode,
        "matchingTeamMode": matching_team_mode,
        "status": "exact-finish-result-consensus",
        "playerRowsChecked": len(rows),
        "fallbackUsed": False,
    }


def require_mode_decoder_supported(mode: dict) -> dict:
    if mode.get("decoderEnabled") is not True:
        raise ValueError(
            "unsupported-matching-mode-unverified:"
            f"{mode.get('matchingMode')}/{mode.get('matchingTeamMode')}"
        )
    return mode
