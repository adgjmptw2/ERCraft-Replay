"""Compile the adopted 90-character scope into exact gameDb stage candidates.

Registration is not verification. Detailed conditions remain explicit scope
requirements even when a base-stage metric can use a shared calculator.
"""
import hashlib
import json
from pathlib import Path
try:
    from .projectile_hit_catalog import build_projectile_skill_catalog
    from .requested_skill_scope import FIRST_BATCH,SCOPE_PATH
    from .requested_skill_hit_rates import GAME_DB_SHA256,CLIENT_VERSION
except ImportError:
    from projectile_hit_catalog import build_projectile_skill_catalog
    from requested_skill_scope import FIRST_BATCH,SCOPE_PATH
    from requested_skill_hit_rates import GAME_DB_SHA256,CLIENT_VERSION

SLOTS={
6:'Q',7:'QER',8:'QE',9:'QWR',10:'QER',11:'ER',12:'QWER',13:'QER',14:'QWER',15:'QWER',16:'QWE',17:'QWER',18:'QWER',19:'QWER',
20:'QWER',21:'QWER',22:'QWER',23:'QWER',24:'QWER',25:'QWR',26:'QWE',27:'QWER',28:'QWER',29:'QER',30:'QWER',31:'WER',32:'WR',
33:'QWER',34:'QWER',35:'QWR',36:'QWR',37:'QWR',38:'QWER',39:'QWER',40:'QWER',41:'QWER',42:'QER',43:'QWER',44:'QER',45:'QWER',
46:'QWER',47:'QWER',48:'QER',49:'QWER',50:'QWER',51:'QWER',52:'QWE',53:'WER',54:'QWER',55:'WER',56:'QWER',57:'QWER',58:'QWR',
59:'QER',60:'QWER',61:'QWE',62:'QER',63:'QWER',64:'QWER',65:'QWER',66:'QWER',67:'QWER',68:'QWER',69:'QWER',70:'QR',71:'QR',
72:'QER',73:'Q',74:'WER',75:'QWER',76:'QWER',77:'QWER',78:'QWER',79:'QWR',80:'QWER',81:'QWR',82:'QWER',83:'QWER',84:'QWER',
85:'QER',86:'QWR',87:'QE',88:'QWER',89:'QWER',90:'QWR'}
SLOT_BY_FAMILY={'Active1':'Q','Active2':'W','Active3':'E','Active4':'R'}
# These are actual owned follow-ups of one player use, not extra user uses.
OWNED_FOLLOWUP_PARENTS={1015900:1015400,1035600:1035500,1043520:1043500,1072800:1072500,1079600:1079300,1081800:1081500}
# Explicit non-damaging preparation or movement excluded by the adopted scope.
EXCLUDED_GROUPS={
    1019500:"사용자 2026-09-10: 엠마 R 전체 제외",
    **{g:"사용자 2026-09-10: 병목 목록 7번 에키온 R 임시 제외; 원본 보존" for g in (1044500,1044520,1044530,1044540)},
    1022210:'사용자 2026-09-10: 루크 Q2 적중률 제외',
    1090300:'사용자 2026-09-10: 루치아 W 소환체 적중률 제외',
    1028300:'사용자 2026-09-10: 수아 W 적중률 제외; R-W 범위는 유지',
    1055310:'사용자 2026-09-09: 에스텔 전방 물 분사 W2 긴급진화 적중률 제외',
    1055320:'사용자 2026-09-09: 제외한 에스텔 물 분사의 종료·방패 해제 단계도 적중률 대상에서 제외',
    1055500:'사용자 2026-09-09: 에스텔 궁극기 R 헬기호출 전체 적중률 제외',
    1018300:'사용자 2026-09-09: 쇼이치 W 제외',
    1031400:'사용자 2026-09-09: 리오 E 단궁/화궁 모두 제외',
    1031410:'사용자 2026-09-09: 리오 E 단궁/화궁 모두 제외',
    1054400:'사용자 2026-09-09: 칼라 E 제외',
    1074500:'사용자 2026-09-09: 다르코 R 제외',
    1044510:'사용자 2026-09-09: 에키온 R은 진화 후만 집계; 진화 전 바이퍼 R 및 해당 표본 수집 제외',
    1009300:'사용자 2026-09-09: 아이솔 W 적중률 제외',
    1047400:'라우라 E1 벽 연결·이동 성공률 제외; E2 착지 적중률만 유지',
    1015500:'시셀라 R은 최신 사용자 지시에 따라 제외',
    1057200:'마르티나 강화 전 스킬은 적중률 표시에서 제외',
    1057300:'마르티나 강화 전 스킬은 적중률 표시에서 제외',
    1057320:'마르티나 강화 전 카메라 공격은 적중률 표시에서 제외',
    1057400:'마르티나 강화 전 스킬은 적중률 표시에서 제외',
    1057410:'마르티나 강화 전 스킬은 적중률 표시에서 제외',
    1057420:'마르티나 강화 전 복귀는 적중률 표시에서 제외',
    1057500:'마르티나 강화 전 촬영은 적중률 표시에서 제외',
    1058500:'헤이즈 R 전환 휘두르기는 제외; R 상태의 Q 로켓 발사를 R 적중률로 표시',
    1058510:'헤이즈 R 폼 종료는 적중률 분모에서 제외',
    1058520:'헤이즈 R 상태 기본 공격은 요청한 Q 로켓 적중률에서 제외',
    1062500:'테오도르 R은 최신 사용자 지시에 따라 제외',
    1064300:'바냐 W 전체 단계는 최신 사용자 지시에 따라 제외',
    1064310:'바냐 W 전체 단계는 최신 사용자 지시에 따라 제외',
    1074300:'다르코 W는 최신 사용자 지시에 따라 제외',
    1077400:'유민 E 자체는 제외; E로 강화된 Q/W는 각각 유지',
    1084600:'블레어 D는 최신 사용자 지시에 따라 제외',
    1088200:'비형 Q 전체 단계는 최신 사용자 지시에 따라 제외',
    1088210:'비형 Q 전체 단계는 최신 사용자 지시에 따라 제외',
    1088220:'비형 Q 강화 평타 단계는 최신 사용자 지시에 따라 제외',
    1088230:'비형 Q 강화 평타 단계는 최신 사용자 지시에 따라 제외',
    1041400:'요한 E는 최신 사용자 지시에 따라 제외',
    1041500:'요한 R은 최신 사용자 지시에 따라 제외',
    1030500:'일레븐 R은 최신 사용자 지시에 따라 제외',
    1037510:'다니엘 R 진입은 최신 사용자 지시에 따라 제외',
    1037520:'다니엘 R 탈출은 최신 사용자 지시에 따라 제외',
    1001400:'재키 E는 최신 사용자 지시에 따라 적중률 대상에서 제외',
    1001510:'재키 R2는 최신 사용자 지시에 따라 적중률 대상에서 제외',
    1018100:'쇼이치 회수 단검 패시브는 조준 적중률 목록에서 제외',
    1019400:'엠마 E 확정 지정 적용은 최신 사용자 지시에 따라 제외',
    1021210:'로지 Q Move는 이동 단계이며 적중률 대상에서 제외',
    1008200:'하트 Q 준비는 실제 발사 단계와 분리',
    1006300:'나딘 W는 최신 사용자 지시에 따라 적중률 대상에서 제외',
    1006310:'나딘 W는 최신 사용자 지시에 따라 적중률 대상에서 제외',
    1014300:'키아라 W는 요청한 W2 보호막 폭발만 측정',
    1014500:'키아라 R은 사용자 확정대로 심판 처치 수만 측정',
    1038010:'제니 E는 요청 범위 밖',1038400:'제니 E는 요청 범위 밖',1038800:'제니 E는 요청 범위 밖',
    1047210:'라우라 Q는 요청한 Q1만 측정',
    1055400:'에스텔 E는 요청한 방패 돌진 E2에 대응하는 공격 단계만 후보',
    1056300:'피올로 W는 요청한 W2만 측정',
    1059400:'아이작 E는 요청한 E2만 측정',
    1061400:'이렘 인간 E는 요청 범위 밖',
    1066500:'아르다 R 준비는 강화 Q/W/E의 입력이며 별도 적중 분모 아님',
    1078500:'히스이 R은 요청한 R2만 측정',
    1084500:'블레어 R은 요청한 R2만 측정',
}


def build_registry(game_db):
    if hashlib.sha256(game_db.read_bytes()).hexdigest()!=GAME_DB_SHA256:raise ValueError('registry needs exact gameDb')
    scope=json.loads(SCOPE_PATH.read_text(encoding='utf-8'))
    catalog=build_projectile_skill_catalog(game_db)
    rows=[]
    for character in scope['characters']:
        code=character['characterCode'];stages=[];excluded=[];internal=[]
        if code<=5:
            stages=[{'skillGroup':g,'label':label,'mode':mode,'scopeTier':'reviewed-request-stage'} for g,label,mode in FIRST_BATCH[code]]
            excluded=[{'skillGroup':s['skillGroup'],'skillId':s['skillId'],'reason':EXCLUDED_GROUPS[s['skillGroup']]}
                      for s in catalog['characters'][str(code)]['skills'] if s['skillGroup'] in EXCLUDED_GROUPS]
        else:
            for stage in catalog['characters'][str(code)]['skills']:
                group=stage['skillGroup'];slot='R' if group==1058220 else 'T' if group==1018100 else 'D' if group==1084600 else SLOT_BY_FAMILY.get(stage['family'])
                if group in OWNED_FOLLOWUP_PARENTS:
                    internal.append({'skillGroup':group,'skillId':stage['skillId'],
                        'parentRequestedSkillGroup':OWNED_FOLLOWUP_PARENTS[group],
                        'reason':'본체 한 번 사용에 연결할 소환물 후속 시전; 독립 사용 횟수로 세지 않음'})
                    continue
                if group in EXCLUDED_GROUPS:
                    excluded.append({'skillGroup':group,'skillId':stage['skillId'],'reason':EXCLUDED_GROUPS[group]})
                    continue
                if slot is None or (slot not in SLOTS[code] and group not in {1084600,1018100}):continue
                if group==1026020:
                    for mode,label in [('summon-railgun-normal','Q 일반 센트리건 레일건'),
                                       ('summon-railgun-reinforced','RQ 강화 센트리건 레일건')]:
                        stages.append({'skillGroup':group,'slot':slot,'label':label,'skillId':stage['skillId'],
                            'mode':mode,'scopeTier':'base-stage-candidate','reportMultiTarget':True})
                    continue
                mode='enemy-kill' if code==14 and slot=='R' else 'any'
                if group==1009300:mode='shot-with-camera'
                if group==1018100:mode='autonomous-shot'
                if group in {1026200,1026210,1090300}:mode='summon-shot'
                if group==1047400:mode='movement-success'
                if group==1076510:mode='execute-success'
                if group==1072500:mode='target-count'
                if group in {1028300,1028520}:mode='blind'
                if group==1020300:mode='pull-hit'
                if group==1033300:mode='guard-success'
                if group==1082200:mode='first-hit'
                label='R 로켓(Q 입력) [HazeActive1_3]' if group==1058220 else slot+' ['+stage['skillId']+']'
                if group==1020300:label='W 끌기 적용'
                if group==1033300:label='W 가드 성공률'
                if group==1082200:label='Q 최초 투사체 적중'
                stages.append({'skillGroup':group,'slot':slot,'label':label,
                    'skillId':stage['skillId'],'mode':mode,'scopeTier':'base-stage-candidate',
                    'reportMultiTarget':not(code==31 and slot=='W' or group==1033300)})
                if group in {1086300,1086500}:
                    for condition,suffix in [('absorb','흡수 연결'),('absorb-pulse','흡수 반복 적용')]:
                        stages.append({'skillGroup':group,'slot':slot,'label':slot+' '+suffix,'skillId':stage['skillId'],
                            'mode':condition,'scopeTier':'requested-condition','reportMultiTarget':True})
                if group in {1009200,1021500}:
                    excluded.append({**stages.pop(), 'reason':
                        '사용자 2026-09-09: 최초 폭탄 부착만 집계; 지연 폭발 피해 적중률 제외'})
                    stages.append({'skillGroup':group,'slot':slot,'label':slot+' 부착 적용','skillId':stage['skillId'],
                        'mode':'attach','scopeTier':'requested-condition','reportMultiTarget':True})
                if group==1062200:
                    for condition,outcome_label in [('direct-hit','Q 일반 광선'),('screen-hit','Q 스크린 재발사')]:
                        stages.append({'skillGroup':group,'slot':slot,'label':outcome_label,
                            'skillId':stage['skillId'],'mode':condition,
                            'scopeTier':'requested-condition','reportMultiTarget':True})
                if group in {1020500,1067200,1077500,1078200}:
                    for condition,suffix in [('first-hit','첫 타격'),('end-hit','둘째 타격'),('both-hit','같은 적에게 두 타 모두 적중')]:
                        stages.append({'skillGroup':group,'slot':slot,'label':slot+' '+suffix,
                            'skillId':stage['skillId'],'mode':condition,
                            'scopeTier':'requested-condition','reportMultiTarget':True})
                if group==1083500:
                    for condition,outcome_label in [('first-hit','R 첫 범위 타격'),('end-hit','R 종료 폭발 타격')]:
                        stages.append({'skillGroup':group,'slot':slot,'label':outcome_label,
                            'skillId':stage['skillId'],'mode':condition,
                            'scopeTier':'requested-condition','reportMultiTarget':True})
                if group==1083400:
                    stages[-1]['label']='E W 연계 피해'
                    stages.append({'skillGroup':group,'slot':slot,'label':'E W 연계 실제 속박',
                        'skillId':stage['skillId'],'mode':'fetter',
                        'scopeTier':'requested-condition','reportMultiTarget':True})
                if group==1042500:
                    for condition,outcome_label in [('creation-hit','R 마법진 생성 타격'),('end-hit','R 마법진 종료 타격')]:
                        stages.append({'skillGroup':group,'slot':slot,'label':outcome_label,
                            'skillId':stage['skillId'],'mode':condition,
                            'scopeTier':'requested-condition','reportMultiTarget':True})
                if group in {1034300,1090500}:
                    outcomes=[('fetter','W 마지막 촬영 속박')] if group==1034300 else [
                        ('unmarked-hit','R 일반 대상 타격'),('marked-hit','R 수정 대상 타격'),
                        ('stun','R 수정 대상 실제 기절')]
                    for condition,outcome_label in outcomes:
                        stages.append({'skillGroup':group,'slot':slot,'label':outcome_label,
                            'skillId':stage['skillId'],'mode':condition,
                            'scopeTier':'requested-condition','reportMultiTarget':True})
                if group in {1014400,1025500,1051400,1054500,1019500,1041200,1046300,1049320,1062400,1075300,1076300,1083500,1087400}:
                    condition='stun' if group in {1054500,1083500} else 'fetter'
                    label=slot+(' 기절 적용' if condition=='stun' else ' 속박 적용')
                    if group in {1019500,1062400,1075300,1087400}:label+='(형태 미분리)'
                    if group==1041200:label+='(본체 발사 기준)'
                    if group==1049320:label+='(3연계)'
                    stages.append({'skillGroup':group,'slot':slot,'label':label,'skillId':stage['skillId'],
                        'mode':condition,'scopeTier':'requested-condition','reportMultiTarget':True})
                if code==41:
                    for cohort,suffix in [('ally','아군 효과 적중'),('either-team','적·아군 중 한 명 이상 적중')]:
                        stages.append({'skillGroup':group,'slot':slot,'label':slot+' '+suffix+' ['+stage['skillId']+']',
                            'skillId':stage['skillId'],'mode':cohort,'scopeTier':'requested-condition','reportMultiTarget':True})
                if group==1081500:
                    stages.append({'skillGroup':group,'slot':slot,'label':'R 가장자리 기절','skillId':stage['skillId'],
                        'mode':'world-edge-stun','scopeTier':'requested-condition','reportMultiTarget':True})
                if group==1015400:
                    for condition,label in [('enemy-pull','E 적 끌기 적용'),('self-pull','E 자기 끌기')]:
                        stages.append({'skillGroup':group,'slot':slot,'label':label,'skillId':stage['skillId'],
                            'mode':condition,'scopeTier':'requested-condition','reportMultiTarget':condition!='self-pull'})
                if group in {1023300,1026400}:
                    for condition,suffix in ([('central-stun','중앙 기절')] if group==1026400
                            else [('inner-hit','안쪽 적중'),('outer-hit','바깥쪽 적중')]):
                        stages.append({'skillGroup':group,'slot':slot,'label':slot+' '+suffix,'skillId':stage['skillId'],
                            'mode':condition,'scopeTier':'requested-condition','reportMultiTarget':True})
        # Scope changes filter individual conditions, not the entire skill.
        keep=[]
        for s in stages:
            group,mode=s['skillGroup'],s['mode']
            if ((group==1054500 and mode=='stun') or
                    (group==1046300 and mode=='fetter') or
                    (group==1083500 and mode!='end-hit') or
                    (group==1047500 and mode=='any')):
                excluded.append({**s,'reason':'사용자 2026-09-10: 타격 단계 집계 범위 수정'})
                if group==1047500:
                    keep.extend([{**s,'mode':m,'label':label,'scopeTier':'requested-condition'}
                                 for m,label in [('first-hit','R 첫 타격'),('end-hit','R 폭발 타격')]])
                continue
            if group==1077210:s={**s,'label':'강화 Q 최초 타격'}
            if group==1082400:s={**s,'label':'E 돌진 타격'}
            if group==1046300:s={**s,'label':'W 타격 적중'}
            keep.append(s)
        stages=keep
        rows.append({**character,'registeredStages':stages,'excludedStageCandidates':excluded,'internalStageCandidates':internal,
                     'detailedRequirements':character.get('currentScopeText',character['requestedScopeText']),
                     'detailedRequirementsComplete':False,
                     'unmappedDetailsMustRemainVisible':True})
    return {'format':'er-requested-skill-stage-registry.v1','clientVersion':CLIENT_VERSION,'gameDataSha256':GAME_DB_SHA256,
        'scopeSourceSha256':hashlib.sha256(SCOPE_PATH.read_bytes()).hexdigest(),'characterCount':90,
        'registeredStageMetricCount':sum(len(r['registeredStages']) for r in rows),'characters':rows,
        'registrationMeansImplementation':False,'implementationComplete':False}


if __name__=='__main__':
    root=Path(__file__).resolve().parent.parent
    result=build_registry(root/'acquire/gamedata-20260903071248.zip')
    path=root/'data/requested_skill_group_registry_20260905.json'
    path.write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(json.dumps({'characters':90,'registeredStageCandidates':result['registeredStageMetricCount'],'implementationComplete':False}))
