"""Direct, non-deferred damage families proved by restored producer bodies.

An effect is a family identity, never a cast ID. Exact manual command lifetimes
still have to separate uses, including siblings sharing the same producer.
"""
from collections import Counter
try:
    from .requested_skill_hit_rates import _result, _unavailable
    from .skill_wire_order import command_order
    from .skill_partial_cast_lifetimes import ordered_cast_records
    from .skill_action_stage_evidence import load_exact_skill_ids
except ImportError:
    from requested_skill_hit_rates import _result, _unavailable
    from skill_wire_order import command_order
    from skill_partial_cast_lifetimes import ordered_cast_records
    from skill_action_stage_evidence import load_exact_skill_ids


FAMILIES = {
    26: ({1026310: 'BarbaraActive2_ReinforceIonLaser'},
         1026302, 'FX_BI_Barbara_Skill02_Hit_P'),
    47: ({1047410: 'LauraActive3_2'}, 1047401, 'FX_BI_Laura_Skill01_Hit02_S'),
    63: ({1063400: 'LyanhHumanActive3_1', 1063410: 'LyanhHumanActive3_2'},
         1063401, 'FX_BI_Lyanh_Human_skill03_Hit'),
    86: ({1086500: 'FenrirActive4'}, 1086502, 'FX_BI_Fenrir_Skill04_Hit'),
}
PROOF = 'deliverables/static-direct-family-producer-proof-v1.json'


def static_direct_family_metric(spec, starts, finishes, damages, player, teams,
                                intervals, catalog, skill_rows, effect_rows, gaps,
                                skill_ids=None, actions=None):
    selected = [s for s in starts if s['playerObjectId'] == player
                and s['skillGroup'] == spec['skillGroup']]
    combat_start = lambda s: any(l <= s['tick'] < r for l, r in intervals)
    diag = dict(observedCastCount=len(selected),
                observedCombatCastCount=sum(map(combat_start, selected)))
    def fail(reason):
        return {**_unavailable(spec, reason), **diag}
    family = FAMILIES.get(spec.get('characterCode'))
    if (family is None or spec['skillGroup'] not in family[0]
            or spec.get('mode') != 'any' or spec.get('unit') != 'skill-cast'):
        return fail('unsupported statically proved direct family')
    groups, effect, prefab = family
    # Laura's Finish invokes the finish callback before its synchronous damage
    # loop. The recorded finish therefore precedes damage in the SAME frame.
    # This is a proved emission order, not a grace period for delayed damage.
    finish_frame_emission = spec['characterCode'] == 47
    ids = load_exact_skill_ids() if skill_ids is None else skill_ids
    codes = {g: {r['code'] for r in skill_rows if r.get('group') == g} for g in groups}
    if (any(type(ids.get(name)) is not int or not codes[g]
            or catalog['skillGroups'].get(str(g), {}).get('skillId') != name
            for g, name in groups.items())
            or [r.get('effectPrefabName') for r in effect_rows if r.get('code') == effect] != [prefab]):
        return fail('direct producer identity differs from pinned gameDb')
    if player not in teams:
        return fail('missing caster team')
    if gaps is None or any(g.get('count', 0) and g.get('packetName') in
                          {'CmdStartSkill', 'CmdFinishSkill', 'CmdDamage'} for g in gaps):
        return fail('complete start/finish/damage streams required')
    if not selected:
        return fail('no observed direct-family casts')
    family_starts = [s for s in starts if s['playerObjectId'] == player and s['skillGroup'] in groups]
    if any(s.get('skillCode') not in codes[s['skillGroup']]
           or s.get('skillIdCode') != ids[groups[s['skillGroup']]] for s in family_starts):
        return fail('direct-family wire skill identity mismatch')
    records, reason = ordered_cast_records(family_starts, finishes, player,
                                           allow_same_tick_finishes=True)
    if reason:
        return fail(reason)
    contacts = [set() for _ in records]
    unknown = {i: 'cast-not-normally-complete' for i, r in enumerate(records)
               if not r['complete'] or r['finish'] is None or r['finish'].get('reason') != 0}
    launch_orders={}
    if spec['characterCode']==26:
        # Native BarbaraActive2Base.Process emits action 31 before its
        # synchronous swept-beam loop. The burn state is a separate producer.
        if gaps is None or any(g.get('count',0) and g.get('packetName') in
                              {'CmdPlaySkillAction','CmdPlaySkillActionWithTargets'} for g in gaps):
            return fail('complete Barbara beam action stream required')
        for i,r in enumerate(records):
            lo=command_order(r['start']);hi=command_order(r['finish']) if r['finish'] else None
            launches=[command_order(a) for a in actions or []
                if a.get('sourceObjectId')==player and a.get('skillIdCode')==ids[groups[1026310]]
                and a.get('actionNo')==31 and command_order(a) is not None
                and lo<command_order(a) and (hi is None or command_order(a)<hi)]
            if len(launches)!=1:unknown[i]='missing-or-ambiguous-Barbara-beam-launch'
            else:launch_orders[i]=launches[0]
    candidates = [d for d in damages if d.get('attackerObjectId') == player and d.get('effectCode') == effect]
    for d in candidates:
        at = command_order(d)
        owners = []
        for i, r in enumerate(records):
            s, f = r['start'], r['finish']
            if d['tick'] < s['tick'] or (f is not None and d['tick'] > f['tick']):
                continue
            finish_tail = (finish_frame_emission and f is not None and f.get('reason') == 0
                           and d['tick'] == f['tick'])
            if at is not None and (at <= command_order(s) or
                                  (f is not None and at >= command_order(f) and not finish_tail)):
                continue
            owners.append(i)
        if not owners:
            return fail('dedicated effect outside every recorded producer lifetime')
        if len(owners) != 1 or at is None:
            for i in owners:
                unknown[i] = 'ambiguous-or-unordered-direct-family-damage'
            continue
        i = owners[0]
        if i in launch_orders and at<=launch_orders[i]:
            unknown[i]='Barbara-damage-before-recorded-beam-launch'
            continue
        if type(d.get('damageIsNull')) is not bool:
            unknown[i] = 'missing-damage-discriminator'
            continue
        target = d.get('targetObjectId')
        if target in teams and teams[target] != teams[player]:
            contacts[i].add((d['tick'], target))
    combat = [i for i, r in enumerate(records)
              if r['start']['skillGroup'] == spec['skillGroup'] and combat_start(r['start'])]
    valid = [i for i in combat if i not in unknown]
    diag.update(unresolvedCombatCastCount=len(combat)-len(valid),
                unresolvedCastReasons=dict(Counter(unknown[i] for i in combat if i in unknown)),
                perUseCompletenessTracked=True, incompleteUsesCountedAsMisses=False)
    if combat and not valid:
        return fail('no complete unambiguous direct-family uses')
    method = ('static-direct-family-ordered-cast-and-synchronous-finish-frame-damage'
              if finish_frame_emission else 'static-direct-family-ordered-cast-damage')
    result = _result(spec, [contacts[i] for i in valid], method,
                     cast_ticks=[records[i]['start']['tick'] for i in valid])
    result.update(diag, candidateEffectCodes=[effect], producerFamilyGroups=sorted(groups),
                  exactDamagePacketCount=len(candidates), minimumRepeatedCastsRequired=False,
                  staticSynchronousFinishFrameEmission=finish_frame_emission,
                  effectSelectedByPrefabName=False, damageAmountInferred=False,
                  evidenceReview=('deliverables/barbara-reinforced-w-direct-producer-proof-v1.json'
                                  if spec['characterCode']==26 else
                                  'deliverables/laura-e2-direct-producer-proof-v1.json'
                                  if spec['characterCode'] == 47 else PROOF))
    return result
