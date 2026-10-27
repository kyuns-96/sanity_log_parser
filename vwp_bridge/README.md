# VWP bridge — PostgreSQL

[AI 판단 설계](../AI_DECISION_DESIGN.md)의 **AI 초기 분류·그룹 판정과 확정 근거의 다음 판단 활용**이 최우선 제품 목표입니다. 이번 모델 연동은 OA API를 우선 사용합니다. bridge는 판단 입력인 전체 로그·실행 조건·원본 위치·점검 근거를 검증해 DB로 전달합니다. 아래 기존 VWP 모델/worker가 OA 판단 연결의 선행 조건은 아닙니다.

VWP-bridge의 파서·근거·모델·worker는 이 디렉터리, 회사 구현은 `integration/`에 있습니다. [담당 AI 지시문](INTERNAL_AI_TASK.md), [서버별 소스·실행 안내](../DEPLOYMENT.md), [DB 연결 인계](../db/CONNECTION_HANDOFF.md)를 따릅니다. 저장소 루트에서 `python -m vwp_bridge.main`으로 실행합니다.

Docker 이미지 빌드·비공개 설정·CA/분석 입력 마운트와 worker 실행은 [Docker 안내](docker/README.md)를 따릅니다.

완료된 VWP snapshot을 검증하고 개별 판정·군집·전체 근거를 `sanity_vwp_data`에 저장합니다.
OA에 전달할 압축 번들은 `sanity_vwp_to_oa`에 등록합니다.
SQLite·OA 파일 복사·직접 통신은 사용하지 않습니다.

현재 `sanity submit`은 공유 폴더 없이 [HTTPS API](../vwp/HTTPS_SUBMISSION.md)로 접수해 [VWP private DB에 원본을 분할 저장](../vwp/DB_ORIGINALS.md)합니다. 서버 schema 3 설정에는 영구 archive/upload/receipt 경로가 없고 파서는 실행 중에만 임시 복원합니다. `vwp_bridge.submission_api`가 업로드·상태 조회를, 별도 `vwp_bridge.submission_worker`가 파싱·V2 분할 전달·OA 응답/receipt를 처리합니다. VWP는 OA에 접속하지 않습니다. 제출 인증과 OWNER 범위 확정의 기존 보호는 이번 저장 변경으로 해제하지 않습니다. 기존 schema 1 인증·ACL 폴더 제출은 호환 경로로 유지합니다. 실제 회사 어댑터·운영 DB 검증과 기존 `work`는 이 두 프로세스와 구분합니다.

설치·DB 구성·공통 모듈 배포는 [루트 안내](../README.md)를 따릅니다.
VWP 서버의 비공개 `.env`에 `SANITY_DB_HOST/PORT/NAME/USER_NAME/PASSWORD`와 `SANITY_DB_SCHEMA`,
TLS 설정을 입력합니다. 사용자/비밀번호는 VWP의 제한된 계정이며 OA 계정은 필요하지 않습니다.
기존 `SANITY_VWP_DSN`이 비어 있지 않으면 연결 필드보다 우선합니다.

사용자가 psycopg2 접속을 확인했으며 기본 `SANITY_DB_ACCESS=postgresql`로 동작합니다.
기존 `.env`의 `dbaas_http`는 직접 변경해야 합니다. 연결/권한 확인은
`.venv/bin/python -m common.sanity_db check --side vwp`로 합니다.
[공개용 접속 안내](../dbaccess.example.md)의 DBaaS HTTP는 임시 읽기 전용으로만 남습니다.
명시적 HTTP 모드에서 분석 저장은 `DBAAS_SQL_CONTRACT_REQUIRED`로 중단하며 직접 접속으로 자동 우회하지 않습니다.

```bash
.venv/bin/python vwp_bridge/main.py analyze --input /tmp/sanity-source/run.json --out /tmp/sanity-receipt
.venv/bin/python vwp_bridge/main.py respond --request-id REQUEST_UUID
.venv/bin/python vwp_bridge/main.py respond --request-id REQUEST_UUID --reject
.venv/bin/python vwp_bridge/main.py work --interval 2
```

`analyze --out`은 선택 사항이며 `receipt.json`만 생성합니다.
분석 전후 파일 크기·해시·상대 경로와 실제 파일 목록을 검사합니다.
누락·미지원·형식 오류도 예상 단위에 남기며 불완전 결과를 DB에 등록합니다.
종료 코드 0은 명령 성공이지 sanity PASS나 검토 완료가 아닙니다.

## 사내 item별 report parser

실제 report는 사내에서만 열람·검증합니다. [PARSER_INTEGRATION.md](PARSER_INTEGRATION.md)에
`ReportParser` 추상 인터페이스, 반환 모델, 구현·등록 순서, 합성 예제와 검증/오류 인계 절차를 제공합니다.
사내 AI는 `vwp_bridge/integration/corporate_parsers.py`의 추출 메서드/등록과
`vwp_bridge/integration/company_parsers/`의 전용 구현·테스트만 작성합니다.
공통 코드가 scope·원문 span·EOF·미해석 구간·전체 건수와 기존 판정을 검증하며
구현 버전을 불변 분석 계약과 `producer.parsers`에 기록합니다.

```bash
.venv/bin/python vwp_bridge/corporate_analyze.py analyze --input /path/to/private/run.json --out /path/to/private/receipt
```

기본 회사 registry는 비어 있으며 stub은 `PARSER_NOT_CONFIGURED`로 실패합니다.
일반 `main.py`는 회사 코드를 자동 import하지 않습니다. 현장 형식 지원/의미적 정확성은
사내 어댑터 구현과 비공개 사례 검증 후 확인하며 Codex에게 원문을 전달하지 않습니다.

## 판정과 동일성

- `numeric_csv_v1`: 고정 헤더 `object,check_point,mode,code,severity,observed,unit`,
  마지막 `# END rows=N`. 물리 줄마다 한 CSV 행을 판정합니다.
  단위·모드·유한 수치·정확한 Decimal 임계값과 건수를 검사합니다.
- `summary_json_v1`: `format_version=1`, `complete=true`, `total`, `results` 검증.
  16MiB 상한이며 빈 결과·필수 필드 누락은 미완료입니다. JSON Pointer와 문서 줄 범위를 기록합니다.
- 임계값은 정수 또는 정확한 십진 문자열입니다. 소수 JSON 숫자·NaN·무한대는 규칙 오류입니다.
- FAIL/UNKNOWN을 실행·unit·code·severity·rule_version·선언된 `identity_fields`로 집계합니다.
  필드 누락이나 잘못된 타입은 각각 `UNDECIDED`로 격리합니다. Jira를 참조하지 않습니다.
- PASS도 포함한 모든 기록에 판정·위치·원문 SHA-256을 저장합니다. 대표 예시는 그룹당 최초 3개,
  원문은 2,048문자까지입니다. 샘플 생략이 전체 판정 생략을 뜻하지 않습니다.

## 영속성과 복구

분석 ID는 입력·목록·파일·생산자 버전에서 결정합니다. 같은 분석의 동시 실행은 advisory lock으로 차단합니다.
최대 256행 또는 약 4MiB마다 배치를 flush하고 RUN에 checkpoint를 남깁니다.
개별 큰 행은 이 배치 목표보다 클 수 있습니다. 최종 그룹 목록은 여전히 메모리에 있으므로
대규모 고유 군집/초대형 행의 엄격한 메모리 제한을 보장하지 않습니다.

재시작은 전체 입력을 다시 계산하고 기존 불변 행과 대조합니다. 누락 없는 전체 근거와 집계 확인 후,
RUN을 SEALED로 바꾸고 번들을 등록하는 작업을 같은 트랜잭션으로 확정합니다.
봉인된 분석은 변경/자동 수리하지 않습니다. 손상이 있으면 `EVIDENCE_CONFLICT`로 중단하며
출력 폴더를 바꾸는 것으로 동일 DB 분석의 충돌을 우회하지 않습니다.

## 추가 근거 반출

OA가 생성한 특정 요청 UUID에 `respond`를 실행하거나 `work`로 다음 요청을 처리합니다.
일반 sanity report는 요청별 수동 승인을 요구하지 않습니다. `--reject`는 명시적 거부이고 `--approve-export`는 호환용 선택 옵션입니다.
유효 기간, 번들·unit·원본 파일·근거 해시를 검사하고 요청한 모든 기록이 있어야 FULFILLED입니다.
최종 응답 등록과 요청 수신 확인은 원자적으로 처리합니다. 거부/만료 응답은 덮어쓰지 않습니다.

등록된 비밀값과 자격증명 패턴을 원문 응답에서 마스킹하고 원본/전송 해시·마스킹 여부를 함께 저장합니다.
분석 번들과 분류 입력/결과에서 발견한 자격증명은 전달을 중단합니다. 범용 DLP를 구현한 것은 아닙니다.
원본 snapshot은 현장 보존 정책에 따라 별도로 유지합니다.

## VWP AI 작업자

`work`는 DB의 분류 요청과 원문 요청을 polling하며 `--once`로 한 번만 실행할 수 있습니다.
비공개 `.env`의 OpenAI 호환 URL/token/model과 명시적인 API 방식으로 연결합니다. 설정은 [루트 안내](../README.md#6-vwp-ai-분류-루프)를 따릅니다.
AI는 최대 64개 표본으로 선언형 규칙만 제안하고 프로그램이 PASS 포함 전체 기록을 처리합니다.
분류 결과는 최대 128건/약 512KiB씩 저장·전송하고 체크포인트에서 재개합니다. AI 호출 중 lease는 갱신하되 긴 DB 트랜잭션은 열지 않습니다.
불확실한 API 결과를 자동 재호출하거나 CLI/합성 결과로 대체하지 않습니다. 확정은 OA의 사람이 합니다.
실제 사내 API·report 형식·정식 DB 접속 검증은 아직 대기 중입니다.

검증: 루트에서 DB 테스트 설정 후 `.venv/bin/python run_tests.py --require-db`.
