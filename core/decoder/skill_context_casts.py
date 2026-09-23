"""Read explicit alternate start commands through exact gameDb identities.

CmdStartNormalAttackSkill carries the enhanced attack's Skill.code. Its enum
identity comes from the exact SkillGroup table, not a wire field. Passive
commands are retained separately: their skillCode can name the triggering
active skill, while their finish identifies a different passive script.
"""

START_PACKETS={'CmdStartNormalAttackSkill'}


def context_cast_facts(contexts, players, catalog, skill_rows, skill_ids):
    by_code={r['code']:r for r in skill_rows}
    characters={p['objectId']:p['characterCode'] for p in players}
    starts=[];finishes=[];seen=set()
    for event in contexts or []:
        source=event.get('sourceObjectId');kind=event.get('event')
        if source not in characters or kind not in START_PACKETS:continue
        # Only genuine command order is copied. Old contexts retain conservative
        # tick-only lifetime checks rather than inventing an ordinal.
        order={k:event[k] for k in ('wireCategory','wireOrder') if k in event}
        packet_id=event.get('sourcePacketId')
        if packet_id is not None:
            if packet_id in seen:raise ValueError('duplicate explicit context packet identity')
            seen.add(packet_id)
        skill=by_code.get(event.get('skillCode'))
        if not skill:continue
        group=skill['group'];definition=catalog['skillGroups'].get(str(group))
        if (not definition or definition.get('characterCode')!=characters[source] or
                definition.get('family') not in {'Active1','Active2','Active3','Active4'}):continue
        identity=skill_ids.get(definition.get('skillId'))
        if type(identity) is not int:continue
        if event.get('skillIdCode') is not None and event['skillIdCode']!=identity:
            raise ValueError('context skill code and enum disagree')
        starts.append(dict(tick=event['tick'],playerObjectId=source,skillGroup=group,
            skillCode=event['skillCode'],skillIdCode=identity,targetObjectId=event.get('targetObjectId'),
            skillEvolutionLevel=event.get('skillEvolutionLevel'),sourceEvent=kind,
            skillIdIdentitySource='exact-gameDb-Skill.code-to-SkillGroup.skillId-to-versioned-enum',
            wireStatus='decoded-exact-'+kind,**order))
    return starts,finishes
