# 공통 피해·적중 품질 모듈

2026-09-26 연구에서 선별한 공통 집계 코드와 합성 입력 테스트입니다. 기존 `core`, `worker`, `viewer`에 자동 적용되지 않습니다. 경기별 실험 스크립트와 원본 자료는 포함하지 않습니다.

| 모듈 | 역할 |
| --- | --- |
| `damage_accounting_ledger` | 사건 ID 중복·충돌 검사, 소유자 귀속, 준/받은 피해 분리, 미확정 값 보존 |
| `damage_encounter_partition` | 교전 안/밖 집계와 합계 보존 검사 |
| `replay_damage_quality_result` | 기록된 종료 통계와 추정 소계·잔차를 구분 |
| `damage_source_coverage_candidate` | 디코딩된 원본 사건과 두 가공 자료의 사건 ID 대조 |
| `skill_required_channel_guard` | 적중 판단에 필요한 기록의 누락이 알려졌을 때 수치 확정 차단 |

공개 코드는 공통 연구용 집계·검증 모듈이며 운영용 피해 계산기가 아닙니다. 호출자가 사건 목록, 검증된 소유 관계와 피해 포함 조건을 제공해야 합니다. 이 디렉터리의 코드만으로는 리플레이 다운로드나 디코딩을 수행할 수 없습니다.

`damage_source_coverage_candidate.require_damage_source_coverage(catalog, observations, recorded, metadata)`는 딕셔너리 입력을 검사합니다. 비공개 디코더에 의존하는 실행 래퍼는 공개본에서 제외했습니다. 누락 검사는 디코딩된 입력 이후의 자료 일치를 검사하며, 그 이전에 사라진 기록까지 찾아내지는 않습니다.

교전 구간은 시작 포함·끝 제외입니다. 겹치는 구간의 사건을 전체 소계에 중복 가산하지 않지만, 이 검사가 교전 경계 자체의 정확성을 증명하지는 않습니다. 미확정 피해량을 0으로 채우거나 종료 통계에 맞춰 차액을 분배하지 않습니다.

저장소 루트에서 다음 테스트를 실행할 수 있습니다. 외부 패키지나 실제 경기 자료는 필요하지 않습니다.

```sh
python -m unittest discover -s research/quality -p "test_*.py"
```

실제 경기 결과와 이후 작업 기준은 [진행 현황](../../docs/PROGRESS-20260926.md)을 참고하세요.
