"""Fail-closed per-fight HUD evidence from decoded replay events.

Damage is exact only when every resolved PvP ``CmdDamage`` packet in the
interval carries a numeric value. Runtime crowd-control time comes from an
exact gameDb state-code classification plus runtime add/update/remove packets
and the duration carried by those packets; no static skill duration or damage
coefficient is substituted.
"""

from __future__ import annotations

from collections import defaultdict
from typing import Any

try:
    from .build_combat_analysis import EVENT_SPECS
except ImportError:
    from build_combat_analysis import EVENT_SPECS


HUD_STATUS = "derived-exact-pvp-damage-completeness-and-runtime-cc-duration-v3"
DAMAGE_STATUS = "exact-only-when-all-resolved-pvp-CmdDamage-values-are-numeric"
CC_STATUS = "exact-gameDb-cc-state-runtime-lifecycle-in-replay-ticks"
CC_APPLIED_STATUS = "exact-only-with-resolved-enemy-caster-and-complete-state-lifecycle"
CC_UNAVAILABLE_STATUS = "unavailable-missing-exact-gameDb-or-state-event-authority"
NEARBY_RADIUS_METERS = 30
DAMAGE_SAMPLE_FIELDS = [
    "tick",
    "knownDamageDealt",
    "knownDamageTaken",
    "missingDamageDealtPackets",
    "missingDamageTakenPackets",
]


def _event_defs(event_types: dict[Any, Any] | None) -> dict[int, dict[str, Any]]:
    if event_types:
        defs: dict[int, dict[str, Any]] = {}
        for key, value in event_types.items():
            try:
                code = int(key)
            except (TypeError, ValueError):
                continue
            if isinstance(value, dict) and isinstance(value.get("fields"), list):
                defs[code] = value
        if defs:
            return defs
    return {
        code: {"packetName": name, "fields": fields}
        for name, (code, fields) in EVENT_SPECS.items()
    }


def _field(event: list, defs: dict[int, dict[str, Any]], name: str):
    if len(event) < 3:
        return None
    spec = defs.get(event[2])
    if not spec:
        return None
    fields = spec.get("fields") or []
    try:
        index = fields.index(name)
    except ValueError:
        return None
    position = 5 + index
    return event[position] if position < len(event) else None


def _packet_name(event: list, defs: dict[int, dict[str, Any]]) -> str | None:
    if len(event) < 3:
        return None
    spec = defs.get(event[2])
    name = spec.get("packetName") if spec else None
    return name if isinstance(name, str) else None


def _player_ids(players: list[dict[str, Any]]) -> set[int]:
    return {
        player["objectId"]
        for player in players
        if isinstance(player.get("objectId"), int)
    }


def _owner_map(object_owners: dict[Any, Any] | None) -> dict[int, int]:
    owners: dict[int, int] = {}
    for key, value in (object_owners or {}).items():
        try:
            object_id = int(key)
        except (TypeError, ValueError):
            continue
        if isinstance(value, int):
            owners[object_id] = value
    return owners


def _resolved_player(
    object_id: object,
    player_ids: set[int],
    owners: dict[int, int],
) -> int | None:
    if not isinstance(object_id, int):
        return None
    current = object_id
    seen: set[int] = set()
    while current not in seen:
        if current in player_ids:
            return current
        seen.add(current)
        owner = owners.get(current)
        if owner is None:
            return None
        current = owner
    return None


def _interval_bounds(row: object) -> tuple[int, int] | None:
    if not isinstance(row, (list, tuple)) or len(row) < 2:
        return None
    start, end = row[0], row[1]
    if not isinstance(start, int) or not isinstance(end, int) or end <= start:
        return None
    return start, end


def _new_interval(start: int, end: int) -> dict[str, Any]:
    return {
        "startTick": start,
        "endTick": end,
        "damageSamples": [[start, 0, 0, 0, 0]],
        "receivedCcSpans": [],
        "appliedCcSpans": [],
        "receivedCcUnavailableSpans": [],
        "appliedCcUnavailableSpans": [],
        "_dealt": 0,
        "_taken": 0,
        "_nullDealt": 0,
        "_nullTaken": 0,
    }


def _emit_damage(interval: dict[str, Any], tick: int) -> None:
    sample = [
        tick,
        interval["_dealt"],
        interval["_taken"],
        interval["_nullDealt"],
        interval["_nullTaken"],
    ]
    samples = interval["damageSamples"]
    if samples and tick < samples[-1][0]:
        return
    if samples and samples[-1][0] == tick:
        samples[-1] = sample
    else:
        samples.append(sample)


def _interval_at(intervals: list[dict[str, Any]], tick: int) -> dict[str, Any] | None:
    for interval in intervals:
        if interval["startTick"] <= tick < interval["endTick"]:
            return interval
    return None


def _last_sample(samples: list[list[int]], tick: int) -> list[int] | None:
    lo, hi = 0, len(samples)
    while lo < hi:
        mid = (lo + hi) // 2
        if samples[mid][0] <= tick:
            lo = mid + 1
        else:
            hi = mid
    return samples[lo - 1] if lo else None


def hud_sample_at(hud: dict[str, Any] | None, tick: int) -> list[int] | None:
    """Return the last damage-evidence sample inside the active fight."""
    if not hud:
        return None
    interval = _interval_at(hud.get("intervals") or [], tick)
    if interval is None:
        return None
    return _last_sample(interval.get("damageSamples") or [], tick)


def hud_damage_at(
    hud: dict[str, Any] | None,
    tick: int,
    direction: str,
) -> dict[str, Any] | None:
    """Return an exact value or typed unavailable result at ``tick``."""
    sample = hud_sample_at(hud, tick)
    if sample is None:
        return None
    if direction == "dealt":
        known, missing = sample[1], sample[3]
    elif direction == "taken":
        known, missing = sample[2], sample[4]
    else:
        raise ValueError("direction must be dealt or taken")
    return {
        "value": known if missing == 0 else None,
        "knownValue": known,
        "missingPacketCount": missing,
        "status": (
            "decoded-exact-complete-pvp-CmdDamage"
            if missing == 0
            else "unavailable-incomplete-null-pvp-CmdDamage"
        ),
    }


def _merge_spans(spans: list[tuple[int, int]]) -> list[list[int]]:
    merged: list[list[int]] = []
    for start, end in sorted(spans):
        if end <= start:
            continue
        if merged and start <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], end)
        else:
            merged.append([start, end])
    return merged


def _clip_span(start: int, end: int, interval: dict[str, Any]) -> tuple[int, int] | None:
    clipped = max(start, interval["startTick"]), min(end, interval["endTick"])
    return clipped if clipped[0] < clipped[1] else None


def _span_ticks_at(spans: list[list[int]], start: int, tick: int) -> int:
    return sum(
        max(0, min(end, tick) - max(span_start, start))
        for span_start, end in spans
    )


def hud_cc_seconds_at(
    hud: dict[str, Any] | None,
    tick: int,
    direction: str,
) -> dict[str, Any] | None:
    """Return cumulative CC target-seconds inside the active fight."""
    if not hud:
        return None
    interval = _interval_at(hud.get("intervals") or [], tick)
    if interval is None:
        return None
    if direction == "received":
        spans_key = "receivedCcSpans"
        unavailable_key = "receivedCcUnavailableSpans"
    elif direction == "applied":
        spans_key = "appliedCcSpans"
        unavailable_key = "appliedCcUnavailableSpans"
    else:
        raise ValueError("direction must be received or applied")
    unavailable = any(
        start < tick and end > interval["startTick"]
        for start, end in interval[unavailable_key]
    )
    exact_ticks = _span_ticks_at(interval[spans_key], interval["startTick"], tick)
    return {
        "seconds": None if unavailable else round(exact_ticks / 60, 2),
        "knownSeconds": round(exact_ticks / 60, 2),
        "status": (
            "unavailable-incomplete-state-lifecycle-or-caster"
            if unavailable
            else "derived-exact-state-active-replay-ticks-divide-60"
        ),
    }


def _cc_definitions(runtime_cc: dict[str, Any] | None) -> tuple[dict[int, dict], set[int]]:
    if not isinstance(runtime_cc, dict):
        return {}, set()
    if (
        runtime_cc.get("status")
        != "exact-replay-version-official-gameDb-cc-state-map"
        or runtime_cc.get("fallbackUsed") is not False
    ):
        return {}, set()
    code_defs = {
        int(code): row
        for code, row in (runtime_cc.get("codes") or {}).items()
        if str(code).isdigit() and isinstance(row, dict)
    }
    groups = {
        int(group)
        for group, row in (runtime_cc.get("groups") or {}).items()
        if str(group).isdigit() and isinstance(row, dict)
    }
    return code_defs, groups


def _has_cc_authority(
    defs: dict[int, dict[str, Any]],
    runtime_cc: dict[str, Any] | None,
) -> bool:
    code_defs, groups = _cc_definitions(runtime_cc)
    packet_names = {
        row.get("packetName")
        for row in defs.values()
        if isinstance(row, dict)
    }
    return bool(code_defs and groups) and {
        "CmdAddState",
        "CmdAddStateExtended",
        "CmdRemoveState",
    }.issubset(packet_names)


class _StateEventBatches:
    """One ordered source view, including every original clock tick."""
    def __init__(self, events, defs, batches):
        self.events = events
        self.defs = defs
        self.length = len(events)
        self.batches = batches


def _prepare_state_event_batches(events, defs):
    ordered = sorted(
        (row for row in events if isinstance(row, list) and len(row) >= 3),
        key=lambda row: (row[0], row[1]),
    )
    names = {"CmdAddState", "CmdAddStateExtended", "CmdUpdateState",
             "CmdResetCreateTimeState", "CmdPauseState", "CmdRemoveState"}
    batches = []
    index = 0
    while index < len(ordered):
        tick = ordered[index][0]
        if not isinstance(tick, int):
            index += 1
            continue
        relevant = []
        while index < len(ordered) and ordered[index][0] == tick:
            event = ordered[index]
            index += 1
            name = _packet_name(event, defs)
            if name in names:
                relevant.append((name, event))
        # Even an empty batch closes expiries at this exact original tick.
        # Dropping such ticks changes end-status/order at refresh boundaries.
        batches.append((tick, tuple(relevant)))
    return _StateEventBatches(events, defs, tuple(batches))


def _state_spans(
    events: list[list],
    defs: dict[int, dict[str, Any]],
    player_ids: set[int],
    owners: dict[int, int],
    runtime_cc: dict[str, Any] | None,
    non_player_object_ids: set[int],
    *,
    event_batches: _StateEventBatches | None = None,
) -> list[dict[str, Any]]:
    code_defs, cc_groups = _cc_definitions(runtime_cc)
    active: dict[tuple[int, int, int], dict[str, Any]] = {}
    spans: list[dict[str, Any]] = []
    if event_batches is None:
        event_batches = _prepare_state_event_batches(events, defs)
    elif (event_batches.events is not events or event_batches.defs is not defs
          or event_batches.length != len(events)):
        raise ValueError("CC event batches do not belong to this source")

    def duration_end_tick(start_tick: int, raw_duration: object) -> int | None:
        if not isinstance(raw_duration, int) or raw_duration <= 0:
            return None
        return start_tick + round(raw_duration / 100 * 60)

    def close_expired(limit_tick: int, *, inclusive: bool) -> None:
        for key, row in list(active.items()):
            expiry = row.get("expiryTick")
            expired = (
                isinstance(expiry, int)
                and (expiry <= limit_tick if inclusive else expiry < limit_tick)
            )
            if expired:
                active.pop(key)
                if row["startTick"] < expiry:
                    spans.append({
                        **row,
                        "endTick": expiry,
                        "complete": True,
                        "endStatus": "decoded-exact-runtime-duration-expiry",
                    })

    for tick, same_tick_events in event_batches.batches:
        close_expired(tick, inclusive=False)
        for name, event in same_tick_events:
            if name in {"CmdAddState", "CmdAddStateExtended"}:
                target = _field(event, defs, "objectId")
                code = _field(event, defs, "code")
                definition = code_defs.get(code) if isinstance(code, int) else None
                if target not in player_ids or definition is None:
                    continue
                group = definition.get("stateGroup")
                raw_caster = _field(event, defs, "casterId")
                if not isinstance(group, int) or not isinstance(raw_caster, int):
                    continue
                key = (target, group, raw_caster)
                expiry_tick = duration_end_tick(
                    tick, _field(event, defs, "duration")
                )
                if key in active:
                    active[key]["expiryTick"] = expiry_tick
                    continue
                resolved_caster = _resolved_player(raw_caster, player_ids, owners)
                active[key] = {
                    "startTick": tick,
                    "expiryTick": expiry_tick,
                    "target": target,
                    "caster": resolved_caster,
                    "casterStatus": (
                        "decoded-exact-player-owner-chain"
                        if resolved_caster is not None
                        else (
                            "decoded-exact-non-player-caster"
                            if raw_caster == 0 or raw_caster in non_player_object_ids
                            else "unavailable-unresolved-caster-object"
                        )
                    ),
                }
            elif name in {"CmdUpdateState", "CmdResetCreateTimeState"}:
                target = _field(event, defs, "objectId")
                group = _field(event, defs, "group")
                raw_caster = _field(event, defs, "casterId")
                if (
                    target not in player_ids
                    or group not in cc_groups
                    or not isinstance(raw_caster, int)
                ):
                    continue
                row = active.get((target, group, raw_caster))
                if row is None:
                    continue
                duration = _field(event, defs, "duration")
                if name == "CmdUpdateState":
                    created_time = _field(event, defs, "createdTime")
                    if (
                        isinstance(created_time, int)
                        and isinstance(duration, int)
                        and duration > 0
                    ):
                        row["expiryTick"] = round(
                            (created_time + duration) / 100 * 60
                        )
                    else:
                        row["expiryTick"] = None
                else:
                    row["expiryTick"] = duration_end_tick(tick, duration)
            elif name == "CmdPauseState":
                target = _field(event, defs, "objectId")
                group = _field(event, defs, "group")
                raw_caster = _field(event, defs, "casterId")
                row = active.get((target, group, raw_caster))
                if row is not None:
                    # Pause fields are retained, but their resume semantics are
                    # not yet proven against an exact fixture.  An explicit
                    # remove can still close the span; natural expiry cannot.
                    row["expiryTick"] = None
            elif name == "CmdRemoveState":
                target = _field(event, defs, "objectId")
                group = _field(event, defs, "group")
                raw_caster = _field(event, defs, "casterId")
                if (
                    target not in player_ids
                    or group not in cc_groups
                    or not isinstance(raw_caster, int)
                ):
                    continue
                row = active.pop((target, group, raw_caster), None)
                if row is not None:
                    expiry_tick = row.get("expiryTick")
                    end_tick = (
                        min(tick, expiry_tick)
                        if isinstance(expiry_tick, int)
                        else tick
                    )
                    if row["startTick"] < end_tick:
                        spans.append({
                            **row,
                            "endTick": end_tick,
                            "complete": True,
                            "endStatus": "decoded-exact-remove-or-earlier-runtime-expiry",
                        })
        close_expired(tick, inclusive=True)

    for row in active.values():
        expiry_tick = row.get("expiryTick")
        spans.append({
            **row,
            "endTick": expiry_tick if isinstance(expiry_tick, int) else None,
            "complete": isinstance(expiry_tick, int),
            "endStatus": (
                "decoded-exact-runtime-duration-expiry"
                if isinstance(expiry_tick, int)
                else "unavailable-no-exact-state-end"
            ),
        })
    return spans


def _attach_cc_spans(
    players: list[dict[str, Any]],
    by_id: dict[int, dict[str, Any]],
    spans: list[dict[str, Any]],
) -> None:
    teams = {
        player["objectId"]: player.get("teamNumber")
        for player in players
        if isinstance(player.get("objectId"), int)
    }
    grouped_received: dict[tuple[int, int], list[tuple[int, int]]] = defaultdict(list)
    grouped_applied: dict[tuple[int, int, int], list[tuple[int, int]]] = defaultdict(list)
    unavailable_received: dict[tuple[int, int], list[tuple[int, int]]] = defaultdict(list)
    unavailable_applied: dict[tuple[int, int], list[tuple[int, int]]] = defaultdict(list)

    for span in spans:
        target = span["target"]
        caster = span["caster"]
        caster_status = span["casterStatus"]
        target_team = teams.get(target)
        caster_team = teams.get(caster)
        resolved_enemy = (
            caster in by_id
            and target_team is not None
            and caster_team is not None
            and target_team != caster_team
        )
        for interval_index, interval in enumerate(by_id[target]["intervals"]):
            span_end = span["endTick"] or interval["endTick"]
            clipped = _clip_span(span["startTick"], span_end, interval)
            if clipped is None:
                continue
            key = (target, interval_index)
            if span["complete"] and resolved_enemy:
                grouped_received[key].append(clipped)
            elif caster_status == "unavailable-unresolved-caster-object" or (
                resolved_enemy and not span["complete"]
            ):
                unavailable_received[key].append(clipped)

        if resolved_enemy:
            for interval_index, interval in enumerate(by_id[caster]["intervals"]):
                span_end = span["endTick"] or interval["endTick"]
                clipped = _clip_span(span["startTick"], span_end, interval)
                if clipped is None:
                    continue
                if span["complete"]:
                    grouped_applied[(caster, interval_index, target)].append(clipped)
                else:
                    unavailable_applied[(caster, interval_index)].append(clipped)
        elif caster_status == "unavailable-unresolved-caster-object":
            for player in players:
                pid = player.get("objectId")
                if pid not in by_id or teams.get(pid) == target_team:
                    continue
                for interval_index, interval in enumerate(by_id[pid]["intervals"]):
                    span_end = span["endTick"] or interval["endTick"]
                    clipped = _clip_span(span["startTick"], span_end, interval)
                    if clipped is not None:
                        unavailable_applied[(pid, interval_index)].append(clipped)

    for (pid, index), raw in grouped_received.items():
        by_id[pid]["intervals"][index]["receivedCcSpans"] = _merge_spans(raw)
    for (pid, index), raw in unavailable_received.items():
        by_id[pid]["intervals"][index]["receivedCcUnavailableSpans"] = _merge_spans(raw)
    applied_by_interval: dict[tuple[int, int], list[list[int]]] = defaultdict(list)
    for (pid, index, _target), raw in grouped_applied.items():
        applied_by_interval[(pid, index)].extend(_merge_spans(raw))
    for (pid, index), target_spans in applied_by_interval.items():
        # Spans are merged per target, not across targets: simultaneous control
        # of two enemies represents two target-seconds.
        by_id[pid]["intervals"][index]["appliedCcSpans"] = sorted(target_spans)
    for (pid, index), raw in unavailable_applied.items():
        by_id[pid]["intervals"][index]["appliedCcUnavailableSpans"] = _merge_spans(raw)


def build_map_combat_hud(
    players: list[dict[str, Any]],
    events: list[list] | None,
    event_types: dict[Any, Any] | None,
    object_owners: dict[Any, Any] | None,
    runtime_cc: dict[str, Any] | None = None,
    non_player_object_ids: set[int] | None = None,
) -> None:
    """Attach fail-closed per-interval HUD evidence to players in place."""
    defs = _event_defs(event_types)
    cc_authority_available = _has_cc_authority(defs, runtime_cc)
    player_ids = _player_ids(players)
    owners = _owner_map(object_owners)
    exact_non_player_ids = {
        value
        for value in (non_player_object_ids or set())
        if isinstance(value, int) and value not in player_ids
    }
    by_id: dict[int, dict[str, Any]] = {}
    for player in players:
        intervals = [
            _new_interval(*bounds)
            for row in player.get("combatIntervals") or []
            if (bounds := _interval_bounds(row)) is not None
        ]
        player["mapCombatHud"] = {
            "status": HUD_STATUS,
            "fallbackUsed": False,
            "damageStatus": DAMAGE_STATUS,
            "damageSampleFields": DAMAGE_SAMPLE_FIELDS,
            "ccStatus": CC_STATUS if cc_authority_available else CC_UNAVAILABLE_STATUS,
            "ccAppliedStatus": (
                CC_APPLIED_STATUS if cc_authority_available else CC_UNAVAILABLE_STATUS
            ),
            "ccTimeBase": "replay-ticks-divide-60",
            "nearbyRadiusMeters": NEARBY_RADIUS_METERS,
            "intervals": intervals,
        }
        pid = player.get("objectId")
        if isinstance(pid, int):
            by_id[pid] = player["mapCombatHud"]

    for event in events or []:
        if not isinstance(event, list) or len(event) < 3:
            continue
        tick = event[0]
        if not isinstance(tick, int) or _packet_name(event, defs) != "CmdDamage":
            continue
        target = _resolved_player(_field(event, defs, "objectId"), player_ids, owners)
        attacker = _resolved_player(_field(event, defs, "attackerId"), player_ids, owners)
        if (
            target is None
            or attacker is None
            or attacker == target
            or attacker not in by_id
            or target not in by_id
        ):
            continue
        amount = _field(event, defs, "damage")
        attacker_hud = _interval_at(by_id[attacker]["intervals"], tick)
        target_hud = _interval_at(by_id[target]["intervals"], tick)
        if isinstance(amount, (int, float)) and not isinstance(amount, bool) and amount >= 0:
            value = int(round(amount))
            if attacker_hud is not None:
                attacker_hud["_dealt"] += value
                _emit_damage(attacker_hud, tick)
            if target_hud is not None:
                target_hud["_taken"] += value
                _emit_damage(target_hud, tick)
        elif amount is None:
            if attacker_hud is not None:
                attacker_hud["_nullDealt"] += 1
                _emit_damage(attacker_hud, tick)
            if target_hud is not None:
                target_hud["_nullTaken"] += 1
                _emit_damage(target_hud, tick)

    _attach_cc_spans(
        players,
        by_id,
        (
            _state_spans(
                events or [], defs, player_ids, owners, runtime_cc,
                exact_non_player_ids,
            )
            if cc_authority_available
            else []
        ),
    )
    for player in players:
        for interval in player["mapCombatHud"]["intervals"]:
            if not cc_authority_available:
                unavailable_span = [interval["startTick"], interval["endTick"]]
                interval["receivedCcUnavailableSpans"] = [unavailable_span]
                interval["appliedCcUnavailableSpans"] = [unavailable_span]
            for key in ("_dealt", "_taken", "_nullDealt", "_nullTaken"):
                interval.pop(key, None)


def attach_map_combat_hud(
    players: list[dict[str, Any]],
    events: list[list] | None,
    event_types: dict[Any, Any] | None,
    object_owners: dict[Any, Any] | None,
    runtime_cc: dict[str, Any] | None = None,
    non_player_object_ids: set[int] | None = None,
) -> None:
    build_map_combat_hud(
        players,
        events,
        event_types,
        object_owners,
        runtime_cc,
        non_player_object_ids,
    )
