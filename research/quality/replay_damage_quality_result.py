"""Research result contract distinguishing finish totals from partial episode sums."""

def build_damage_quality_result(ledger, partition,*,ledger_basis='ownerDealtHypothesis'):
    if partition.get('ledgerBasis')!=ledger_basis:
        raise ValueError('Unexpected ledger partition basis')
    if partition.get('partitionConserved') is not True:
        raise ValueError('Unverified episode partition')
    players=ledger['players']
    ids=[r['player'] for r in players]
    if len(ids)!=len(set(ids)):raise ValueError('Duplicate player')
    windows={str(k):v for k,v in partition['players'].items()}
    if set(windows)!={str(p) for p in ids}:raise ValueError('Player scope mismatch')
    unattributed=ledger['unattributedDealtEvents']
    if type(unattributed)is not int or unattributed<0:raise ValueError('Invalid attribution count')
    rows=[]
    for player in players:
        row={'player':player['player']}
        for side in ('dealt','taken'):
            total=player[side];episode=windows[str(player['player'])][side]
            for key in ('insideKnown','outsideKnown','insideUnknown','outsideUnknown'):
                if type(episode[key])is not int or episode[key]<0:raise ValueError('Invalid partition value')
            known=episode['insideKnown']+episode['outsideKnown']
            unknown=episode['insideUnknown']+episode['outsideUnknown']
            if known!=total['knownEventSubtotal'] or unknown!=total['unresolvedEvents']:
                raise ValueError('Episode totals do not conserve ledger')
            finish=total['recordedFinish']
            if finish is not None and (type(finish)is not int or finish<0):raise ValueError('Invalid finish total')
            residual=None if finish is None else known-finish
            if residual!=total['knownSubtotalMinusFinish']:raise ValueError('Residual mismatch')
            attribution_unknown=unattributed if side=='dealt' else 0
            status=('unresolved_events' if unknown or attribution_unknown else
                    'finish_unavailable' if finish is None else
                    'differs_from_finish' if residual else 'matches_finish_unverified')
            row[side]=dict(recordedMatchTotal=finish,insideEpisodeKnownSubtotal=episode['insideKnown'],
                outsideEpisodeKnownSubtotal=episode['outsideKnown'],knownEventSubtotal=known,
                insideEpisodeUnknownEvents=episode['insideUnknown'],outsideEpisodeUnknownEvents=episode['outsideUnknown'],
                unresolvedEvents=unknown,globalUnattributedDealtEvents=attribution_unknown,
                knownSubtotalMinusFinish=residual,status=status,exactEventTotal=None,
                serverDamageAccuracyProven=False)
        rows.append(row)
    return dict(version=1,scope='retained-research',ledgerBasis=ledger_basis,players=rows,
        totalSource='recordedFinish',eventAmounts='experimental_HP_reconstruction_with_supplied_eligibility',
        intervalBasis=partition['intervalBasis'],globalUnattributedDealtEvents=unattributed,
        limits=['Recorded match total and reconstructed episode subtotal have distinct sources.',
                'Matching finish does not establish per-hit accuracy or policy completeness.',
                'Global unattributed count is shared; do not sum it across player rows.'],
        eligibleForDamageTotals=False)
