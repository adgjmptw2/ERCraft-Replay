"""Partition eligible facts once per player; overlapping windows never multiply hits."""
def partition(ledger,ticks,intervals,*,ledger_basis='ownerDealtHypothesis'):
    rows={p:{s:dict(insideKnown=0,outsideKnown=0,insideUnknown=0,outsideUnknown=0,multipleWindowEvents=0) for s in ('dealt','taken')} for p in ledger.players}
    for fact in ledger.facts.values():
        if fact.victim not in ledger.players:continue
        actor=fact.attacker if fact.attacker in ledger.players else fact.owner if fact.owner_verified and fact.owner in ledger.players else None
        tick=ticks[fact.source_id]
        for side,player,eligible in [('dealt',actor,fact.dealt),('taken',fact.victim,fact.taken)]:
            if player is None or eligible is False:continue
            matches=sum(start<=tick<end for start,end,*_ in intervals.get(player,[]))
            row=rows[player][side];prefix='inside' if matches else 'outside'
            row['multipleWindowEvents']+=int(matches>1)
            if fact.amount is None or eligible is None:row[prefix+'Unknown']+=1
            else:row[prefix+'Known']+=fact.amount
    report=ledger.report({})
    for player in report['players']:
        for side in ('dealt','taken'):
            row=rows[player['player']][side];total=player[side]
            assert row['insideKnown']+row['outsideKnown']==total['knownEventSubtotal']
            assert row['insideUnknown']+row['outsideUnknown']==total['unresolvedEvents']
    return dict(ledgerBasis=ledger_basis,players=rows,partitionConserved=True,intervalBasis='Recorded damage episodes; startTick <= tick < endTick',limits=['Partition conservation does not prove encounter boundaries or damage values.','Unattributed dealt events remain global ledger issues and cannot be assigned to player windows.'])
