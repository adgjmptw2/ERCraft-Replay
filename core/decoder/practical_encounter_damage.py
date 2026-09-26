"""Export existing encounter estimates to a byte-bound public viewer sidecar.

Offline only. No acquisition, event reconstruction or finish-total calibration.
"""
import argparse
import hashlib
import json
from pathlib import Path


def build_damage_sidecar(public_bytes, private, practical):
    public = json.loads(public_bytes)
    runs = practical.get('runs', [])
    if practical.get('format') != 'ercraft-practical-encounters.v1' or len(runs) != 1:
        raise ValueError('One practical encounter run required')
    run = runs[0]
    hashes = {s['sha256'] for s in private['sources'] if s.get('file', '').lower().endswith('.er')}
    if run['sourceSha256'] not in hashes or public['meta']['clientVersion'] != private['meta']['clientVersion']:
        raise ValueError('Different replay source or version')
    sources = {p['player']: p for p in run['players']}
    if len(sources) != len(run['players']) or set(sources) != {p['objectId'] for p in private['players']}:
        raise ValueError('Incomplete damage roster')
    targets = {p['publicPlayerId']: p for p in public['players']}
    if len(targets) != len(public['players']) or len(targets) != len(sources):
        raise ValueError('Incomplete public roster')
    output = []
    for public_id, player in enumerate(private['players'], 1):
        target = targets.get(public_id)
        if target is None or any(target[k] != player[k] for k in ('characterCode', 'teamNumber')):
            raise ValueError('Public identity mapping differs')
        source = sources[player['objectId']]
        episodes = source['episodes']
        expected = target['sceneCoaching']['episodes']
        private_windows = [(e['startTick'], e['endTick']) for e in player['sceneCoaching']['episodes']]
        windows = [(e['startTick'], e['endTick']) for e in expected]
        if windows != private_windows or windows != [(e['startTick'], e['endTick']) for e in episodes] or len(set(windows)) != len(windows):
            raise ValueError('Encounter windows differ')
        rows = []
        for ep, wanted in zip(episodes, expected):
            row = dict(episodeNumber=wanted['teamEpisodeNumber'], startTick=ep['startTick'], endTick=ep['endTick'])
            for side in ('dealt', 'taken'):
                cell = ep[side]
                if any(type(cell[k]) is not int or cell[k] < 0 for k in ('knownSubtotal', 'unknownEvents')):
                    raise ValueError('Invalid damage estimate')
                row[side] = {k: cell[k] for k in ('knownSubtotal', 'unknownEvents')}
            rows.append(row)
        for side in ('dealt', 'taken'):
            total = source['totals'][side]
            for key, inside, outside, whole in (
                ('knownSubtotal', 'insideEpisodeKnownSubtotal', 'outsideEpisodeKnownSubtotal', 'knownEventSubtotal'),
                ('unknownEvents', 'insideEpisodeUnknownEvents', 'outsideEpisodeUnknownEvents', 'unresolvedEvents'),
            ):
                if sum(e[side][key] for e in rows) != total[inside] or source['outside'][side][key] != total[outside] or total[inside] + total[outside] != total[whole]:
                    raise ValueError('Encounter sum differs from analysis')
        output.append(dict(publicPlayerId=public_id, characterCode=player['characterCode'], episodes=rows))
    return dict(format='ercraft-encounter-damage.v1', sourceFixtureSha256=hashlib.sha256(public_bytes).hexdigest(),
                reportId=public['meta']['reportId'], clientVersion=public['meta']['clientVersion'],
                basis='recorded-events-estimate', calibratedToMatchTotal=False, players=output)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('public', 'private', 'practical', 'output'):
        parser.add_argument(name, type=Path)
    args = parser.parse_args()
    result = build_damage_sidecar(args.public.read_bytes(), json.loads(args.private.read_bytes()), json.loads(args.practical.read_bytes()))
    with args.output.open('x', encoding='utf8') as stream:
        json.dump(result, stream, ensure_ascii=False, separators=(',', ':'))


if __name__ == '__main__':
    main()
