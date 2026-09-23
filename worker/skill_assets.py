"""Validate the complete installed skill icon manifest before acquiring a replay."""
import json

def validate_skill_icons(assets):
    for code, row in assets['icons'].items():
        slots = row.get('slots')
        if not isinstance(slots, dict) or not slots:
            raise ValueError(f'SKILL_ASSET_INVALID character={code}: empty slots')
        for name, slot in slots.items():
            if not slot.get('imageDataUrl', '').startswith('data:image/'):
                raise ValueError(f'SKILL_ASSET_INVALID character={code} slot={name}: image missing')
        if any(s not in {'passive', 'q', 'w', 'e', 'r'} for s in row.get('unavailableSemanticSlots', [])):
            raise ValueError(f'SKILL_ASSET_INVALID character={code}: gap invalid')

def preflight(core):
    from decoder.build_public_combat_analysis import load_skill_assets
    manifest = json.loads((core / 'data/skills.provenance.json').read_text(encoding='utf8'))
    codes = {row['characterCode'] for row in manifest['characters']}
    if not codes:
        raise ValueError('SKILL_ASSET_INVALID: empty manifest')
    validate_skill_icons(load_skill_assets(codes))
