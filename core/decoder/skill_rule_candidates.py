"""Offline name-based candidate discovery; never promotes a mapping to reviewed."""
import re

def build_effect_candidates(spec,catalog,effect_rows):
    stage=catalog['skillGroups'][str(spec['skillGroup'])]
    family=stage.get('family')
    if family not in {'Active1','Active2','Active3','Active4'}:return set(),set()
    name=catalog['characters'][str(spec['characterCode'])]['characterNameInternal']
    character_pattern=re.compile(r'(?:^|_)'+re.escape(name)+r'(?:_|$)',re.I)
    skill_pattern=re.compile(r'(?:^|_)Skill0?'+family[-1]+r'(?:_|$)',re.I)
    # GameDb also uses ExplosionHit, DashHit, lastHit and Hit02. Omitting
    # those can validate the first impact while silently losing a follow-up.
    # This broadens candidates only; runtime ownership/lifetimes still gate them.
    hit_pattern=re.compile(r'(?:^|_)[A-Za-z]*Hit[0-9]*(?:_|$)',re.I)
    effects=set()
    for row in effect_rows:
        for source in [row.get('effectPrefabName',''),row.get('soundName','')]:
            if character_pattern.search(source) and skill_pattern.search(source) and hit_pattern.search(source):
                effects.add(row['code'])
    groups={int(g) for g,s in catalog['skillGroups'].items() if s['characterCode']==spec['characterCode'] and s['family']==family}
    return groups,effects

def build_projectile_candidates(spec, catalog):
    stage = catalog['skillGroups'][str(spec['skillGroup'])]
    family = stage.get('family')
    if family not in {'Active1', 'Active2', 'Active3', 'Active4'}:
        return set(), set()
    name = catalog['characters'][str(spec['characterCode'])]['characterNameInternal']
    character = re.compile(r'(?:^|_)' + re.escape(name) + r'(?:_|$)', re.I)
    skill = re.compile(r'(?:^|_)Skill0?' + family[-1] + r'(?:_|$)', re.I)
    codes = {int(code) for code, definition in catalog['projectileDefinitions'].items()
             if character.search(definition.get('prefabName', '')) and skill.search(definition.get('prefabName', ''))}
    groups = {int(group) for group, definition in catalog['skillGroups'].items()
              if definition['characterCode'] == spec['characterCode'] and definition['family'] == family}
    return groups, codes


def compile_candidate_families(catalog,effect_rows):
    families={};shared={}
    for group,definition in catalog['skillGroups'].items():
        character=definition['characterCode'];family=definition.get('family')
        key=(character,family)
        if key not in shared:
            spec=dict(characterCode=character,skillGroup=int(group))
            effect_groups,effects=build_effect_candidates(spec,catalog,effect_rows)
            projectile_groups,projectiles=build_projectile_candidates(spec,catalog)
            assert effect_groups==projectile_groups
            shared[key]=dict(characterCode=character,family=family,groups=sorted(effect_groups),
                effectCodes=sorted(effects),projectileCodes=sorted(projectiles),
                mappingStatus='candidate-static-name-only',reviewedMapping=False)
        families[group]=shared[key]
    return families


def compile_development_form_families(catalog,effect_rows,families):
    """Partition explicit form names offline, without promoting candidate FKs.

    Only development routes consume this table. Shared assets with conflicting
    form labels are not silently assigned to either form.
    """
    effects={r['code']:r for r in effect_rows}
    def skill_form(group):
        definition=catalog['skillGroups'][str(group)]
        name=catalog['characters'][str(definition['characterCode'])]['characterNameInternal']
        match=re.match(re.escape(name)+r'(Human|Cat)Active[1-4]',definition.get('skillId',''))
        return match.group(1) if match else None
    def forms(text):
        return set(re.findall(r'(?:^|_)(Human|Cat)(?=_|$)',text,re.I))
    result={}
    for group,family in families.items():
        form=skill_form(group)
        if form is None:
            result[group]=family
            continue
        wanted=form.lower()
        def same_form(strings):
            found={f.lower() for text in strings for f in forms(text)}
            return found=={wanted}
        result[group]={**family,
            'groups':[g for g in family['groups'] if skill_form(g)==form],
            'effectCodes':[c for c in family['effectCodes'] if same_form(
                [effects[c].get('effectPrefabName',''),effects[c].get('soundName','')])],
            'projectileCodes':[c for c in family['projectileCodes'] if same_form(
                [catalog['projectileDefinitions'][str(c)].get('prefabName','')])],
            'form':form,'mappingStatus':'candidate-explicit-form-name-partition',
            'reviewedMapping':False}
    return result
