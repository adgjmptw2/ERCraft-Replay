"""Research-only coverage gate against admitted decoded damage identities.

This proves equality to the admitted stream, not completeness of the replay or
server damage. Keep the existing builder and immutable qualifications unchanged.
"""

def require_damage_source_coverage(catalog, observations, recorded, metadata):
    players = {p['objectId'] for p in catalog['players']}
    expected = {pid for pid, event in metadata['events'].items()
                if event['victim'] in players}
    for label, rows, key in (
        ('hp', observations['observations'], 'sourceSequence'),
        ('recorded', recorded['events'], 'packetId'),
    ):
        ids = [row[key] for row in rows]
        if any(type(pid) is not int for pid in ids):
            raise ValueError(f'{label}: invalid damage identity')
        actual = set(ids)
        if len(ids) != len(actual):
            raise ValueError(f'{label}: duplicate damage identity')
        if actual != expected:
            raise ValueError(f'{label}: admitted damage coverage mismatch; '
                             f'missing={len(expected-actual)}, extra={len(actual-expected)}')

