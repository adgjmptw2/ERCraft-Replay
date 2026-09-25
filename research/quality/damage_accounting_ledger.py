"""Shared research ledger. Attribution/eligibility must be supplied as evidence.

No character exceptions, lifecycle guesses, or finish-total balancing. This is
not a production damage reducer: its caller must establish each event policy.
"""
from dataclasses import dataclass
from collections import Counter

@dataclass(frozen=True)
class DamageFact:
    source_id: tuple  # stable raw packet identity, never rounded time/amount
    attacker: int
    victim: int
    amount: int | None
    dealt: bool | None
    taken: bool | None
    owner: int | None = None
    owner_verified: bool = False
    dealt_reason: str = "caller_supplied"
    taken_reason: str = "caller_supplied"

class DamageLedger:
    def __init__(self, players):
        self.players=frozenset(players)
        self.facts={};self.duplicates=0;self.conflicts=[]

    def add(self, fact):
        if not isinstance(fact.source_id,tuple) or not fact.source_id:
            raise ValueError('Stable raw source identity required')
        if fact.amount is not None and (type(fact.amount) is not int or fact.amount<0):
            raise ValueError('Nonnegative integer amount or explicit unknown required')
        if any(x is not None and type(x) is not bool for x in (fact.dealt,fact.taken)):
            raise ValueError('Side eligibility must be bool or unknown')
        old=self.facts.get(fact.source_id)
        if old is not None:
            if old!=fact:
                self.conflicts.append(fact.source_id)
                raise ValueError('Conflicting records for one raw event')
            self.duplicates+=1
            return
        self.facts[fact.source_id]=fact

    def report(self, finish):
        known={'dealt':Counter(),'taken':Counter()}
        missing={'dealt':Counter(),'taken':Counter()}
        classes=Counter();unattributed=0
        exclusions={}
        def exclude(side, player, reason, kind, amount):
            key=(side,player,reason,kind)
            row=exclusions.setdefault(key,dict(side=side,player=player,reason=reason,eventClass=kind,events=0,knownAmount=0,unknownAmountEvents=0))
            row["events"]+=1
            if amount is None:row["unknownAmountEvents"]+=1
            else:row["knownAmount"]+=amount
        for f in self.facts.values():
            if f.victim not in self.players:
                classes['non_player_target']+=1
                exclude('dealt',f.attacker if f.attacker in self.players else f.owner if f.owner_verified and f.owner in self.players else None,'scope:non_player_target','non_player_target',f.amount)
                continue
            actor=f.attacker if f.attacker in self.players else f.owner if f.owner_verified and f.owner in self.players else None
            kind=('direct_self' if f.attacker==f.victim else 'direct_player') if f.attacker in self.players else ('owned_object_self' if actor==f.victim else 'verified_owned_object') if actor is not None else 'attacker_attribution_not_supplied'
            classes[kind]+=1
            if actor is None and f.dealt is not False:unattributed+=1
            for side,player,eligible in [('dealt',actor,f.dealt),('taken',f.victim,f.taken)]:
                if eligible is False:
                    exclude(side,player,f.dealt_reason if side=="dealt" else f.taken_reason,kind,f.amount)
                    continue
                if player is None:continue
                if eligible is None or f.amount is None:missing[side][player]+=1
                else:known[side][player]+=f.amount
        rows=[]
        for player in sorted(self.players):
            row={'player':player}
            for side in ('dealt','taken'):
                recorded=finish.get(player,{}).get(side)
                row[side]={'recordedFinish':recorded,'knownEventSubtotal':known[side][player],
                    'unresolvedEvents':missing[side][player],
                    'knownSubtotalMinusFinish':None if recorded is None else known[side][player]-recorded,
                    'completeUnderSuppliedPolicy':not missing[side][player] and not self.conflicts and not(side=='dealt' and unattributed)}
            rows.append(row)
        return {'players':rows,'rawEvents':len(self.facts),'playerTargetRawEvents':len(self.facts)-classes['non_player_target'],
                'excludedByPolicyOrScope':list(exclusions.values()),
                'contract':{'scope':'Player-target damage; supplied side eligibility only.',
                    'unresolvedEvents':'Policy-specific; excluded unknown amounts are not missing eligible damage.',
                    'unattributedDealtEvents':'Global count; each event blocks all players dealt completeness, not one event per player.',
                    'invalidInput':'Invalid values and conflicting identities raise immediately; catch explicitly to inspect partial report.',
                    'attribution':'Missing caller attribution is not proof that source ownership is unresolved.',
                    'completeness':'Does not establish that the supplied policy includes every server-credited event.'},'duplicateReferencesIgnored':self.duplicates,
                'conflictingEventIds':self.conflicts,'classes':dict(classes),'unattributedDealtEvents':unattributed,
                'serverDamageAccuracyProven':False,'productionChanged':False}
