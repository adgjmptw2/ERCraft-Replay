"""Recorded object contacts retain their emitting cast after that cast ends."""
from collections import defaultdict
from .skill_wire_order import command_order


def projectile_contact_parents(records,spawns,collisions,player,codes):
    parents={};seen=set()
    for spawn in spawns:
        if spawn.get('ownerPlayerObjectId',spawn.get('ownerObjectId'))!=player or spawn.get('projectileCode') not in codes:continue
        oid=spawn['projectileObjectId']
        if oid in seen:return None,'duplicate projectile identity in cast contact graph'
        seen.add(oid);at=command_order(spawn)
        if at is None:return None,'missing projectile spawn order in cast contact graph'
        candidates=[i for i,r in enumerate(records) if command_order(r['start'])<=at
            and (r['finish'] is None or at<=command_order(r['finish']))]
        if len(candidates)!=1 or not records[candidates[0]]['complete']:
            return None,'projectile emission lacks unique complete cast in contact graph'
        parents[oid]=(candidates[0],at,spawn['tick'])
    contacts=defaultdict(list)
    for contact in collisions:
        parent=parents.get(contact['projectileObjectId'])
        if parent is None:continue
        at=command_order(contact)
        if at is None or at<parent[1] or contact['tick']<parent[2]:
            return None,'collision precedes spawn or lacks order in cast contact graph'
        contacts[contact['tick'],contact['targetObjectId']].append((parent[0],at))
    return contacts,None
