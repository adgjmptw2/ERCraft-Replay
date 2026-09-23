// User scoring policy. Retain source classifications; do not invent unknown usage counts.
export function applyMissPolicy(value) {
 if (!value || typeof value !== 'object') return;
 if (Array.isArray(value)) {value.forEach(applyMissPolicy);return;}
 if (value.metricId && ['skill-cast','projectile-shot'].includes(value.unit)) {
  const unknown=Number.isInteger(value.unresolvedCombatCastCount)?value.unresolvedCombatCastCount:0;
  const original={status:value.status,attemptCount:value.attemptCount,hitCount:value.hitCount,unresolvedCombatCastCount:value.unresolvedCombatCastCount};
  let attempts=value.attemptCount,hits=value.hitCount;
  const classified=Number.isInteger(attempts)&&Number.isInteger(hits)&&attempts>=hits&&hits>=0;
  if (classified && unknown>0 && value.unit==='skill-cast' && !value.incompleteUsesCountedAsMisses) attempts+=unknown;
  if (!classified && value.unit==='skill-cast' && Number.isInteger(value.combatCastCount) && value.combatCastCount>0) {attempts=value.combatCastCount;hits=Number.isInteger(hits)?hits:0;}
  if (Number.isInteger(attempts)&&Number.isInteger(hits)&&attempts>=hits&&hits>=0&&(attempts!==original.attemptCount||hits!==original.hitCount)) {
   value.sourceClassification=original;value.attemptCount=attempts;value.hitCount=hits;value.hitRate=attempts?hits/attempts:null;
   value.status='calculable-experimental';value.calculationConfidence='experimental';value.incompleteUsesCountedAsMisses=true;
   value.scoringPolicy='user-unknown-as-miss-v1';value.verifiedCompletionCredit=false;
   delete value.unresolvedHitRateBounds;
  }
 }
 Object.entries(value).forEach(([key,child])=>{if(key!=='sourceClassification')applyMissPolicy(child)});
}
