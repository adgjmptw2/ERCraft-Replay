# 개발과 검증

## 환경

Python 3.11을 기준으로 확인했다. 아래 명령은 저장소 루트의 Windows PowerShell에서 실행한다.

```powershell
python -m venv .venv
.venv\Scripts\python -m pip install -r requirements.txt
.venv\Scripts\python tools/verify_publication.py
```

오프라인 바이너리 연구 도구를 사용할 때만 `requirements-research.txt`의 추가 패키지를 설치한다. 게임 바이너리와 gameDb는 포함하지 않는다.

## 합성 입력 검증

다음 테스트는 운영 서비스, 인증 세션, 경기 다운로드를 사용하지 않는다.

```powershell
cd core
..\.venv\Scripts\python -m unittest decoder.test_delta_payloads decoder.test_schema_member_cache decoder.test_skill_wire_order decoder.test_runtime_metric_privacy
cd ..\worker
..\.venv\Scripts\python -m unittest test_failure_code test_payload_storage test_job_scratch
cd ..
```

파서, 캐시 격리, 이벤트 순서, 공개 지표의 식별자 제거, 실패 분류, 압축 보존, 임시 작업 정리를 확인한다. 전체 경기의 정확도나 서비스 배포 성공을 증명하는 테스트는 아니다.

`worker/test_queue.py`는 기존 서비스용 통합 테스트다. 기본 포트의 서버에 요청을 보낼 수 있으므로 위 테스트 목록에 포함하지 않았다. 전체 테스트 디렉터리를 무조건 실행하지 말고, 격리된 서버와 설정을 먼저 준비한다.

## 실제 분석의 전제

이 저장소는 소스 공개본이며 완성된 독립 실행 패키지가 아니다. 다음 입력과 통합 작업이 별도로 필요하다.

- 권한 있는 취득 경로와 비공개 세션/계정 설정
- 리플레이의 정확한 버전 및 해시와 일치하는 gameDb ZIP
- 해당 버전의 스키마, 실행 계획과 모든 계산 근거
- 필요한 생성 자료와 버전별 release manifest
- 서비스 디렉터리 배치, Python 실행 경로와 워커 설정

`worker/`는 원래 서비스의 `scripts/replay-beta/`에서 분리했다. 파일 안의 `parents[2]`, 형제 코어 경로, 서비스 스크립트 경로는 기존 배치를 가리킨다. 공개 저장소의 경로로 자동 치환하지 않았다. 별도 통합 없이 `worker/server.py`를 실행하면 잘못된 작업 위치를 참조할 수 있다.


원본 `.er`의 임시 사용·정리는 수집 및 워커 경계에서 관리한다. 일부 저수준 함수는 분석 입력이나 캐시를 보존하므로 모든 함수가 자동으로 입력을 삭제한다고 가정하지 않는다.

## 뷰어

`viewer/`는 빌드 단계가 없는 HTML/CSS/JavaScript다. 화면은 `public/ui-assets/`의 경기별 JSON을 읽는다. 기본 분석 파일은 `combat-analysis-personal-pvp-v1.json`이며 스킬 레벨, 이동 이벤트와 개인 지표 등의 sidecar도 사용한다.

경기 데이터는 저장소에서 제외했다. 정적 서버만 실행한 빈 상태에서 경기 재생이나 분석 성공을 기대하면 안 된다. 공개 변환 및 검증을 마친 동일 경기의 출력 파일을 연결해야 한다.
