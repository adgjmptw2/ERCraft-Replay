"""Unique recorded callback candidates, not proof of exclusive attribution."""
from collections import defaultdict
from .skill_wire_order import command_order


def unique_damage_marker_action_candidates(damages, markers, actions):
    buckets = defaultdict(lambda: [[], [], []])
    for index, rows in enumerate((damages, markers, actions)):
        for row in rows:
            order = command_order(row)
            if order is None:
                return {}
            buckets[(row['tick'], order[0])][index].append(row)
    result = {}
    for damage_rows, marker_rows, action_rows in buckets.values():
        if len(damage_rows) != 1 or len(marker_rows) != 1 or len(action_rows) != 1:
            continue
        damage, marker, action = damage_rows[0], marker_rows[0], action_rows[0]
        if (marker.get('targetObjectId') != action.get('sourceObjectId') or
                not command_order(damage) < command_order(marker) < command_order(action)):
            continue
        result[command_order(damage)] = dict(method='unique-damage-marker-action-candidate',
            damageWireOrder=damage['wireOrder'], markerWireOrder=marker['wireOrder'],
            actionWireOrder=action['wireOrder'], attributionEstimated=True,
            exclusiveAttributionProven=False)
    return result
