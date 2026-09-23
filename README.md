# ERCraft Replay

[GitHub 저장소](https://github.com/adgjmptw2/ERCraft-Replay)

이터널 리턴의 .er 리플레이를 해석하고 이동, 전투, 스킬 사용 결과를 분석하여 웹 뷰어로 표현하는 프로젝트입니다.

## 프로젝트 상태 및 한계

현재 저장소는 12.4 버전에 대응하는 분석 소스와 워커, 뷰어의 공개 소스 스냅샷입니다.
운영 서비스 전체를 복제한 즉시 실행형 배포 패키지가 아니며, 단순히 저장소를 복제(clone)하는 것만으로 기동할 수 없습니다.

**제외된 자료**
본 저장소에는 아래 자료가 포함되지 않습니다.

- gameDb ZIP 및 게임 바이너리
- 원본 .er 리플레이 파일
- 인증 세션 및 인증서
- 운영 환경(DB, 큐, 로그 등) 설정
- 경기별 분석 JSON 데이터 및 개인 식별 자료
- 비공개 경기 근거 및 연구 결과 전체

뷰어 코드에 경기별 JSON 데이터가 없으므로 뷰어 서버만 실행한다고 해서 경기가 자동으로 재생되지는 않습니다.

## 구성 요소

**core**

- decoder: Brotli 및 MemoryPack 디코딩, 위치/상태/이벤트 추출, 스킬 시도 및 적중 집계, 공개 결과 변환과 검증.
- schema: 버전별 스키마, gameDb 해시 계약, 계산 실행 계획.
- data: 스킬 계산 규칙, 캐릭터/스킬/아이템/지도 정적 자료 및 출처 메타데이터.
- acquire: 인증 세션을 사용하는 일회성 수집/분석 코드(실제 세션 및 계정 설정은 없음).
- deliverables: 공개 가능한 일부 계산 근거.

**worker**
Windows 서비스 연동 소스로 FIFO 큐, 요청 제한, 결과 보존, 실패 상태 처리, 버전별 입력 검사를 수행합니다.
기존 서비스 디렉터리 배치와 강하게 연결되어 있어 기동 명령을 단독으로 제시할 수 없습니다.

**viewer**
정적 HTML, CSS, JavaScript 기반의 UI입니다.
이동 지도, 스킬 분석 화면, 재생, 플레이어 선택 및 테마 기능을 제공합니다.
스킬 적중률(합산 적중 / 합산 시도)의 산식과 귀속은 코어가 전담하며, UI가 임의로 계산하지 않습니다.

**docs**

- [ARCHITECTURE.md](docs/ARCHITECTURE.md): 구성과 데이터 흐름
- [DEVELOPMENT.md](docs/DEVELOPMENT.md): 개발 환경, 검증 및 실행 전제
- [DATA_POLICY.md](docs/DATA_POLICY.md): 포함/제외 자료와 비밀정보 취급 원칙
- [SOURCE.md](docs/SOURCE.md): 소스 출처와 스냅샷 범위

## 개발 환경 준비

Python 3.11 환경을 기준으로 합니다. Windows PowerShell을 사용하여 아래 명령으로 준비합니다.

    python -m venv .venv
    .venv\Scripts\python -m pip install -r requirements.txt

## 검증 명령

확실한 검증을 위해 다음 명령을 실행할 수 있습니다.

    .venv\Scripts\python tools/verify_publication.py
    cd core
    ..\.venv\Scripts\python -m unittest decoder.test_delta_payloads decoder.test_schema_member_cache decoder.test_skill_wire_order decoder.test_runtime_metric_privacy
    cd ..

위 4개의 테스트 모듈은 합성 입력을 활용하여 파서, 필드 캐시 격리, 이벤트 순서, 공개 결과의 식별자 제거 경계를 검사합니다.
전체 경기 분석이나 E2E 정확도를 완벽하게 보장하는 것은 아닙니다.

## 데이터 처리 원칙

- 일치성: 실제 분석에는 리플레이와 정확히 일치하는 gameDb, 스키마, 계산 근거, 실행 계획이 필요합니다. 버전이나 해시가 다를 경우 다른 버전의 데이터로 대체하지 않습니다.
- 미확인 상태 유지: unknown, null, unavailable, unresolved 등 미확인 상태를 0이나 성공으로 임의 변경하지 않습니다.
- 추정 분리: 관측된 사실, 계산 결과, 개발 추정을 명확히 구분하며, 검증되지 않은 결과를 확정된 사실로 다루지 않습니다.
- 파일 정리: 수집한 원본 .er 파일은 분석 중 임시 자료로만 사용하며 분석 성공 및 실패와 관계없이 정리합니다. 단, 기존 일부 저수준 연구 코드에서 입력을 보존할 수 있으므로 코드 전체 단위의 자동 삭제가 완벽히 보장되지는 않습니다.

## 저작권 및 라이선스

본 저장소의 제3자 자료에 관한 내용은 [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)를 참조하십시오.
게임과 관련된 이미지 및 명칭의 모든 권리는 각 권리자에게 있으며, 본 프로젝트가 이에 대해 새로운 라이선스를 임의로 부여하지 않습니다.
