"""Describe retained region gaps without altering any hit/unknown outcome."""
def attach_region_blockers(output, proof):
    for player in output.get('playerObservations',[]):
        for row in player.get('observations',[]):
            if row.get('skillGroup') not in (1028200,1028510):continue
            for unknown in row.get('phaseMetrics',{}).get('center',{}).get('unknownUseEvidence',[]):
                cast=unknown['cast']['wireOrder']
                failures=[r for r in proof.get('unknown',[]) if r.get('castWireOrder')==cast]
                geometries=[r['evidence'] for r in proof.get('proofs',[]) if r.get('evidence',{}).get('castWireOrder')==cast]
                inventories=[r for r in proof.get('stateInventory',[]) if r.get('castWireOrder')==cast and r.get('playerObjectId')==player['playerObjectId']]
                reasons=sorted({r['reason'] for r in failures})
                if geometries:reasons.append('recorded-position-envelope-does-not-separate-regions')
                if not failures and not geometries:reasons.append('no-retained-geometry-witness')
                unknown['inputBlockers']=dict(geometryReasons=reasons,
                    inventoryStatuses=sorted({r.get('status','missing') for r in inventories}),
                    observedStateTypes=sorted({s['stateType'] for r in inventories for s in r.get('possibleStates',[])}),
                    acceptedBookmarkObserved=any(r.get('acceptedBookmarkStates') for r in inventories),
                    sameInputRecalculationAddsEvidence=False,newReplayRequiredEstablished=False)
    return output
