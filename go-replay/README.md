# Go .er decoder

Python을 호출하지 않고 `.er` 원본을 직접 읽는 Go 라이브러리와 CLI입니다.
기존 Python 분석기 소스는 변경하지 않았습니다.

## 이식 범위

- 파일 서명·버전·0x410 헤더, 16바이트 레코드 프레이밍, 파일 SHA-256
- gzip 타입 정의, Brotli 압축 해제, delta envelope의 네 패킷 분류
- 패킷 이름/상속 정보, 버전별 스키마, 전체 command와 ReplaySnapshot 해석
- 기본 숫자형, 문자열, 벡터, enum, 배열/List/HashSet/Dictionary, native nullable
- 12.3 필드 순서·stateGroup 예외, 12.4 union과 unmanaged dictionary 배치
- strict boundary 검사, 원본 바이트·실패 상태 보존, 순서를 유지하는 NDJSON 출력
- 실제 시작/종료 시각, 전체 시간, 압축 해제·디코딩·출력 단계 시간

내부 객체는 공유 필드 레이아웃과 값 슬라이스(`Object`)로 저장합니다. 모든 타입을
개별 생성 Go struct로 바꾼 구현은 아닙니다. JSON 출력 시에만 이름 기반 map으로
변환합니다. 좌표/색상 벡터는 고정 길이 타입 배열로 저장합니다. 레코드 단위로 처리하므로
경기 전체의 해석 객체를 메모리에 유지하지 않습니다. 파일 접근은 128 KiB 버퍼로 처리하고,
Brotli 내부 상태는 재사용하되 반환 데이터는 각 레코드가 독립적으로 소유합니다.

추가 최적화 후 실제 전체 파일 처리 시간은 **1.03초**(수정 전 재측정 2.92초)입니다.
측정 조건과 원시 시각은 [OPTIMIZATION.md](OPTIMIZATION.md)를 참조하십시오.

## 실행 (PowerShell)

Go 1.27.1 이상을 설치하고 `go` 명령을 PATH에 등록한 뒤, 저장소 루트에서 실행합니다.

```powershell
Set-Location .\go-replay
$go = 'go'
New-Item -ItemType Directory -Force bin, output | Out-Null
& $go build -o bin/erdecode.exe ./cmd/erdecode

# 요약만 출력: 파일 읽기·압축 해제·전체 디코딩을 실제로 수행합니다.
.\bin\erdecode.exe -input C:\path\match.er -schema-dir ..\core\schema

# 전체 해석 결과를 저장합니다. 기존 출력 파일은 덮어쓰지 않습니다.
.\bin\erdecode.exe -input C:\path\match.er -schema-dir ..\core\schema -out output/match.ndjson
```

출력은 header 한 줄, 레코드별 한 줄, summary 한 줄입니다. 바이너리는
`{"__bytesBase64__":"..."}`로, NaN/Infinity는 `{"__float__":"..."}`로 표현합니다.
64비트 정수는 손실 없이 JSON 정수로 출력합니다. JavaScript에서 읽을 때 별도 정밀도
처리가 필요합니다. 출력은 비공개 원본 해석 자료이며 웹 뷰어 공개용 JSON이 아닙니다.

- 종료 코드 `0`: 모든 대상 패킷/스냅샷 해석 성공
- 종료 코드 `2`: unknown 또는 해석 실패 존재. 원본과 상태를 보존한 출력/요약 제공
- 종료 코드 `1`: 프레이밍·스키마·파일·출력 등 치명적 오류. 최종 결과 게시 안 함
- `-allow-gaps`: 조사 시 코드 2만 0으로 변경. `complete=false` 표시는 유지
- `-raw-records`: 이미 보존하는 패킷 바이트 외에 모든 압축 레코드 바이트도 출력
- `-max-payload`: 레코드 및 압축 해제 크기 상한(기본 256 MiB)

파일 경로는 CLI 작업 디렉터리 기준입니다. 스키마 선택은 헤더 버전으로 결정하며,
12.4 스키마는 기존 Python과 같은 SHA-256 계약을 검사합니다. 미등록 버전은 거부합니다.
디코더는 Python 런타임·인증 세션·gameDb 없이 동작합니다.

## 검증

```powershell
& $go test ./...
& $go vet ./...

# Python 원본을 oracle로 사용하는 추가 대조 검사 (Brotli Python 패키지 필요)
python tools/schema_fixtures.py output/schema-fixtures.json
$env:ER_GO_FIXTURES = (Resolve-Path output/schema-fixtures.json).Path
& $go test ./... -v
Remove-Item Env:ER_GO_FIXTURES

# 실제 파일의 모든 필드/바이트/상태/순서/개수를 대조합니다.
python tools/compare_python.py C:\path\match.er --compare output/match.ndjson
```

확인한 실제 12.1.0 파일에서 68,430개 레코드, 439,764개 항목(ReplaySnapshot 67개 포함)이
오류 없이 해석됐고 Python과 전체 필드·원본 패킷·순서가 일치했습니다.
12.1~12.4 합성 대조 10,937건, malformed 입력 단위 테스트, Go fuzz 69,185회가 통과했습니다.
스키마 fixture 생성에서 기존 Python도 지원하지 않는 `KeyValuePair<int,int>` 관련 클래스
12개(version/class 조합)와 비구체 base 1개는 제외 목록에 명시됩니다.

## 경계

기존 `delta_payloads.py`, `inspect_deltas.py`, `full_replay_corpus.py`의 저수준 해석 경로를
이식한 것입니다. 전투·스킬 지표 계산, 공개 결과 변환, worker 서비스 연결 및 SQLite
캐시 호환 어댑터는 이 모듈에 포함하지 않습니다. 기존 서비스는 아직 Python을 사용합니다.
NDJSON은 기존 SQLite 캐시의 대체 파일 형식이 아닙니다.

스키마에 byte[]로 선언된 내부 스냅샷은 바이트 그대로 보존합니다. 타입을 추측하지 않으며,
호출자가 확정된 타입을 알고 있으면 `Decoder.DecodeValue`로 추가 해석할 수 있습니다.
`complete=true`는 대상 wire 디코딩 성공이고, 전투 의미 검증 완료가 아닙니다.
실제 경기 검증은 현재 확보한 12.1.0 한 경기이며, 12.2~12.4는 합성 테스트 범위입니다.

## 의존성

Go 모듈은 `github.com/andybalholm/brotli v1.2.4`를 사용합니다.
[공식 소스](https://github.com/andybalholm/brotli),
[MIT 라이선스](https://github.com/andybalholm/brotli/blob/master/LICENSE).
`go.mod`와 `go.sum`에 버전과 체크섬을 고정했습니다.
