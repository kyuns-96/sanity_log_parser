# 프로토타입 데이터 계약 — PostgreSQL

제품의 최우선 목표와 모델 실행 방향은 [AI 판단 설계](AI_DECISION_DESIGN.md)를 따른다. **AI 최초 분류·그룹 판정 → 사람 수정·확정 근거 축적 → 관련 사례 검색·다음 AI 판단 활용**이 필수 흐름이며 이번 연동은 사내 OA OpenAI 호환 API를 우선한다. 이 파일은 현재 직렬화 계약을 설명한다. OA 공통 모델 호출·불변 job·현재 확정 사례 검색·직접 근거 판정 V2와 운영 SSO workbench를 제공한다. 실제 사내 API·판단 품질과 대량 모델 분할/통합은 별도 검증/구현 대상이다.

현재 실행 계약은 같은 PostgreSQL DB의 4개 테이블이다. SQLite와 브리지 간 직접 통신/파일 복사는 사용하지 않는다.
Python 3.10 이상, 직접 모드는 psycopg2, DBaaS 접속은 HTTPX/python-dotenv를 사용한다. DB 신청은 [db/README.md](db/README.md),
환경 변수·배포 파일·실행 명령은 [README.md](README.md)를 따른다. Jira 조회·게시·시뮬레이터는 폐지했다.
OA는 React·shadcn 대시보드와 인증된 로컬 FastAPI를 사용한다. VWP AI 어댑터의 기본 설정은 disabled다. SSO 인증·사용자 연결 추상 인터페이스와 [운영 Welcome·관리·legacy 범위 권한](oa/ACCESS_MANAGEMENT.md)을 제공한다. V2 source 수신과 버전/PoR·검토의 운영 SSO 권한 연결도 제공한다. 실제 회사 어댑터·배포 검증은 별도다.

VWP AI의 API·CLI 호출 가능 여부는 사용자에게 확인했다. AI clustering·사람 피드백·지속 그룹·evidence 추가의 목표 계약은
[요구사항 14절](requirement.md#ai-clustering)에 정리되어 있으며, 이 파일은 현재 구현된 프로토콜을 설명한다.
새 AI 작업/메시지·분류 개정의 DB 기반은 `db/004_clustering.sql`로 추가한다. 기존 신청 form과 `002`·`003`은 유지한다.
기존 legacy 작업자·분류 개정·분할 전송 API와 화면을 연결했다. 이 경로는 VWP에서 모델을 호출한다. 새 OA 모델 실행은 OA의 로그·관련 확정 사례 조회 → 자기 모델 API → 근거/개정 검증·DB 저장으로 연결했다. 별도 AI용 HTTP 서버 없이 OA 작업자를 실행하며 전체 입력이 설정한 모델 한도 안에 들어가는 첫 순환을 지원한다.
실제 사내 API/DB 호환성과 report 사례는 사용자 제공 후 검증한다.

프로젝트·사용자 배정·블록별 버전·PoR의 목표 요구사항은 [15절 명세](requirement.md#dashboard-domain-spec)를 따른다.
OA 관리 API·DB 레코드와 VWP의 NET_VER/REVISION/ECO를 받는 별도 V2 source 전송·workspace import를 제공한다. 실제 회사 원천/신원·승인 SDK·운영 DB는 미연결이다. [분산 수집 V2 설계](vwp/SOURCE_COLLECTION_DESIGN.md), 실행 가능한 타입/validator와 설정은 [제출 실행 안내](vwp/SUBMISSION.md)를 따른다.

운영은 `/`·`/api/*`이고 workbench는 `/?page=workbench`·`/api/workbench/*`를 사용한다. `/workspace`·`/workspace/api/*`는 운영에 마운트하지 않는다. 아래에서 `/workspace/api`로 명시한 기존 계약은 독립 합성 환경이며 운영 인증 경로와 구분한다.

## 운영 OA AI 작업과 직접 근거 계약

[OA AI 실행 안내](oa/AI_RUNTIME.md)의 OpenAI 호환 호출은 전체 report의 분류와 현재 확정 그룹 전체의 판정을 처리한다. 모델 설정은 서버의 `SANITY_OA_AI_*`, 실행은 `oa.ai_worker`다. 웹 요청에서 모델을 직접 호출하거나 브라우저가 모델 접속값을 지정하지 않는다.

운영 API prefix는 `/api/workbench`, report 경로는 `/blocks/{block}/versions/{version}/reports/{report}`다. 기존 `authorized_actor`의 SSO/현재 권한·변경 요청 검사를 적용하고 합성 session/user 저장을 사용하지 않는다.

| API | 요청/응답 |
|---|---|
| `GET /session` | 현재 SSO `user`, 빈 `csrf`/`expires_at`, `environment=PRODUCTION_SSO` |
| `GET /overview`, `/blocks/{block}/versions`, `/blocks/{block}/versions/{version}/reports` | 승인된 범위의 프로젝트·버전·ECO·검사 목록 |
| `GET/POST /blocks/{block}/por` | 현재 PoR 조회 / `version_id`, `expected_revision`, UUID `request_id`, `reason`으로 명시적 지정 |
| `GET .../workbench`, `.../clustering/groups`, `.../clustering/records` | 현재 개정과 페이지/이력 범위를 고정한 검토 자료 |
| `GET/POST .../clustering/ai` | capability/job 조회 / `expected_revision`, UUID `request_id`로 등록(202) |
| `GET/POST .../groups/{group}/review/ai` | capability/job 조회 / 판정 `expected_revision`, `expected_cluster_revision`, UUID `request_id`로 등록(202) |
| `POST .../clustering`, `.../clustering/operations`, `.../groups/{group}/rationale` | 사람의 수정·근거 검토·분류 확정 |
| `GET/POST .../groups/{group}/review` | 현재 제안/판정/이력 조회와 사람 판정 저장 |

AI 요청은 actor·모델·기준·endpoint를 본문에서 받지 않는다. 사람 판정은 `GroupReviewChange`, 분류/근거 편집은 기존 strict body 계약을 사용한다. 운영에는 synthetic login, `CRITERIA` proposal/task, 보고서 전체 `/review`를 노출하지 않는다. V2 원본 `/source` 다운로드는 `SOURCE_FILE_ON_VWP`이며 추출 로그는 OA에서 조회한다.

OA job은 기존 `sanity_oa_data`의 `WS_CONFIG`에 namespace `oa-ai-v1`의 append-only 이벤트로 저장한다. 물리 row의 `state={}`, `version=0`, 생성/수정 시각 불변을 유지하고 논리 상태는 payload에 기록한다. `PENDING → RUNNING → RESULT_READY → COMPLETED`와 `FAILED/STALE/UNKNOWN/CANCELLED`를 구분한다. 모델 호출 동안 DB 트랜잭션/권한 잠금을 유지하지 않으며 기한이 지난 RUNNING/불명확한 호출을 자동 재호출하지 않는다. 저장된 RESULT_READY만 모델 재호출 없이 재검증·반영한다.

최종 분류는 기존 `WS_CLUSTER_EVENT`, 판정 제안은 `WS_GROUP_REVIEW_PROPOSAL`에 저장하며 원본/버전/분류 FK를 유지한다. command·원본/개정·선정 사례·점검 기준·모델/prompt를 고정하고 적용 직전 현재 권한과 유효성을 다시 검사한다. 이번 범위에 새 물리 테이블·DDL·GRANT는 없다. 누적 queue/index 성능은 후속 검토 대상이다.

사례 검색은 현재 head의 유효한 사람 확정 사례를 단어/한글 n-gram으로 선별한다. `reusable=false`도 후보이며 자동 규칙 적용 승인과 구분한다. 프로젝트/블록/item/family/parser/규칙 버전/layout은 제한하고 ECO 차이는 모델 입력에 보존한다. 다른 버전은 기존 명시적 `compatible_versions` 승인이 필요하다. 미확정/철회/정정 전·자기 정답·순환 의존을 제외한다. 선정 사례를 완전한 단위로 제공하고 검색/제외와 실제 제공/인용을 기록한다. 이 lexical 선정은 모델의 의미 판단이나 품질 검증을 대신하지 않는다.

분류 출력은 기존 `ClusteringProposalV2`와 전체 ID 단일 배정·그룹별 근거 검사를 사용한다. 그룹 판정 출력은 [validate_review_v2](common/sanity_ai_contracts.py)가 정확히 `schema_version=2`, `conclusion`, `reason`, `evidence_ids`, `reference_ids`, `criterion_ids`를 검증한다. PASS/FAIL은 현재 그룹 직접 근거와 제공된 승인 기준 또는 확정 사례를 인용해야 한다. 과거 사례 0건이어도 승인 기준이 있으면 최초 판정을 제안할 수 있다. 모델은 사람의 확정/actor를 생성하지 않는다. 기존 VWP `ReviewProposal`/`validate_advice`는 변경하지 않는다.

현재 전체 입력/응답 한도를 넘으면 `AI_INPUT_LIMIT`/`AI_OUTPUT_LIMIT`로 실패하고 누락·잘림·거절·허위 참조를 제안으로 저장하지 않는다. 평균 10,000건·100,000건 이상의 실제 모델 분할/조각 통합은 COMMON-05 후속이며 기존 참고 엔진의 분할 성공으로 완료 처리하지 않는다.

## OA 워크스페이스 관리 계약

`db/005_workspace.sql`은 OA에 `WS_CONFIG`, `WS_USER`, `WS_SESSION`, `WS_PROJECT`, `WS_BLOCK`, `WS_VERSION`,
`WS_POR`, `WS_POR_EVENT`, `WS_REPORT`를 추가한다. run/bundle 없는 관리 정보의 별도 종류이며 기존 분석 payload로 위장하지 않는다.
프로젝트/블록/대상 버전의 FK와 앱의 종류·소속 대조를 함께 검사한다. 네 테이블과 브리지 간 DB-only 통신은 유지한다.
PoR은 블록의 버전 참조이고 ECO/특정 검사 실행을 포함하지 않는다. 버전 조회와 PoR 변경은 구분한다.

`POST /workspace/api/blocks/{block_id}/por`는 version_id·expected_revision·request_id(UUID)·reason만 받는다.
지정 사용자·권한·시각은 서버가 결정한다. 최신 배정 검사와 UUID/PoR 행 잠금 후 state·개정·불변 이력을 원자적으로 저장한다.
같은 요청은 같은 결과, 다른 내용의 UUID 재사용/오래된 개정은 409다. 검사 판정·원문·다른 블록은 변경하지 않는다.
세션/Origin/CSRF와 서버의 프로젝트·블록 범위를 검사하고 조회 응답에도 같은 범위를 적용한다.

위 `/workspace/api` 계약의 실행 진입점은 **독립 합성 workspace**를 명시적으로 초기화한 `devtools/workspace_app.py`다.
검증된 공개 parser 스냅샷을 해당 테스트 버전에 연결하며 실제 회사 SSO/자료/운영 DB를 쓰지 않는다.
회사 인증 실패 시 이 계정을 선택하거나 기존 `AUTHORIZATION_NOT_IMPLEMENTED`를 해제하지 않는다.
별도 V2 importer의 운영 진입점은 SSO와 같은 계정 매핑 및 현재 프로젝트/블록 권한을 검사한다. 합성 importer 시험은 회사 SDK/인프라의 실제 배포 검증을 뜻하지 않는다.
필드·실행·인계는 [WORKSPACE.md](oa/WORKSPACE.md)를 따른다.

검사 조회는 같은 버전·ECO 안에서 `item_id`로 구분한다. reports API의 `groups`는 필터·페이지 적용 전의 항목별 집계이며,
선택적 `item_id`는 해당 항목의 상세 목록에 정확히 적용한다. 도구가 같아도 다른 item을 합치지 않는다.

## PostgreSQL 접속 계약

접속 방식 확정(2026-10-14): 사용자가 psycopg2 직접 PostgreSQL 접속 가능함을 확인했다.
기본 `SANITY_DB_ACCESS=postgresql`이며 `SANITY_DB_HOST/PORT/NAME/USER_NAME/PASSWORD`,
`SANITY_DB_SCHEMA`, `SANITY_DB_SSLMODE/SSLROOTCERT`를 각 서버의 비공개 설정에서 읽는다.
비어 있지 않은 해당 side DSN은 호환성을 위해 연결 필드보다 우선한다. 비밀번호는 문자열 조립 없이 드라이버 인자로 전달한다.
`common/sanity_postgres.py`는 psycopg2 커서·트랜잭션/savepoint와 UUID·JSONB·BYTEA 변환을 기존 저장 계약에 연결한다.
트랜잭션 밖 autocommit, 내부 commit/rollback, 서버 측 커서와 세션 advisory lock을 유지한다.
직접 접속 성공과 실제 배포 DB의 역할/DDL/트랜잭션 검증 완료는 구분한다.
[공개용 접속 안내](dbaccess.example.md)의 이전 DBaaS HTTPS API는 **임시 읽기 전용**으로 남는다.
실제 사내 접속 안내 `dbaccess.md`는 Git 업로드에서 제외한다.

권한 모드는 기본 `restricted`다. `SANITY_DB_ALLOW_SINGLE_ACCOUNT`는 미설정/`false`에서 기존 역할·side·컬럼 격리를 검사하고 정확한 `true`에서만 `single_account`를 선택한다. 다른 값은 `DATABASE_CONFIG`이며 실패 후 다른 모드로 전환하지 않는다. 단일 모드는 명명된 앱 역할을 조회하지 않고 네 테이블 전체 컬럼의 유효 SELECT/INSERT/UPDATE를 요구한다. DELETE는 보유를 허용하지만 필수는 아니며 TRUNCATE/REFERENCES/TRIGGER·관리자 속성/역할 소속·테이블 소유/소유자 역할·앱 및 public 스키마 CREATE는 금지한다. 공통 테이블/필수 UNIQUE 검사와 기존 큐/트랜잭션/lease/SSO·업무 권한 보호를 유지한다. **단일 모드에는 DB의 private 접근·송수신 컬럼 격리가 없다.** 새 SQL/GRANT나 DDL 재실행은 요구하지 않는다. 읽기 전용 check는 `privilege_mode=restricted db_isolation=enforced` 또는 `privilege_mode=single_account db_isolation=not_enforced`를 출력한다. 자세한 설정/인계는 [DB 연결 인계](db/CONNECTION_HANDOFF.md#31-단일-계정-모드)를 따른다.
`common/sanity_config.py`는 저장소 루트 `.env`/환경변수에서 설정을 읽고 `common/sanity_dbaas.py`는
Bearer·사내 CA 검증·trust_env=False·30초 timeout·redirect 금지·응답 크기 제한을 적용한다.
URL/토큰/CA 경로/서버 오류 본문은 출력하지 않는다. `/api/vi/me`는 `/api/v1/me`의 오타로 해석했다.
현재 HTTP API는 운영 저장·메시지 등록·DDL 경로가 아니며, 이 API의 쓰기 지원을 전제로 확장하지 않는다.
따라서 이 문서의 PostgreSQL 원자적 저장/잠금 동작을 HTTP에서 구현했다고 주장하지 않는다.
`SANITY_DB_ACCESS=dbaas_http`를 명시한 전체 브리지는 DBAAS_SQL_CONTRACT_REQUIRED로 중단하며 DSN fallback은 없다.
아래 저장 계약의 실행 구현은 기본 `SANITY_DB_ACCESS=postgresql` 직접 접속 모드다.
공통 DBaaS API로 접근하는 HTTPS와 금지된 브리지 상호 간 직접 통신은 구분한다.

## 모듈과 데이터 소유권

- `vwp/`: VWP 내부 입력 snapshot 생성. DB 계정 불필요.
- `vwp_bridge/`, `vwp_bridge/sanity_evidence.py`: 전체 판정, VWP private 근거, 봉인·번들 등록.
- `common/sanity_parsers.py`, `vwp_bridge/integration/corporate_parsers.py`: 회사 report 추출 추상 계약·결과 검사와 명시적 registry. 회사 진입점은 `vwp_bridge/corporate_analyze.py`.
- `oa/`, `oa/sanity_oa_store.py`: 수신본, 검토 개정, 불변 보고서, 대시보드/API.
- `oa/dashboard_api.py`, `oa/dashboard/`: OA 로컬 인증·조회·수동 수신·검토·근거 요청 UI.
- `common/sanity_db.py`: 기본 제한 계정/명시적 단일 계정의 유효 권한 검사, 짧은 트랜잭션, 방향별 큐.
- `common/sanity_exchange.py`, `common/sanity_privacy.py`: OA 원문 요청 → VWP 범위/무결성 확인·자격증명 마스킹 → OA 수신.
- `vwp_bridge/sanity_ai.py`, `vwp_bridge/sanity_cluster_worker.py`: VWP AI 호출·규칙 제안·전체 적용·체크포인트/분할 전송.
- `common/sanity_clustering.py`, `oa/sanity_cluster_store.py`: 선언형 규칙·지속 ID·OA 분류 개정·후보 판단·규칙 저장.
- `common/sanity_workers.py`: 명시적으로 실행하는 VWP/OA DB polling 프로세스.
- `common/sanity_common.py`: 입력/번들/근거 동일성·coverage 검증, 제한된 gzip·JSON, 결정적 보고서 렌더링.

원본 bytes와 JSONB를 혼동하지 않는다. 전송 해시는 `payload_gzip`, 보고서 해시는 `payload_bytes`,
개별 근거 해시는 `raw_bytes`에 대해 계산한다. JSONB 키 순서는 보존되지 않으므로 렌더링 시 정렬한다.
DB에서 표현할 수 없는 NUL 등 정규화 필드는 조용히 삭제하지 않고 DB 오류로 중단한다.
원본 snapshot과 디코딩 실패 표현의 실제 원본 바이트는 VWP 보존 정책에 따라 유지해야 한다.

공통 `write_bundle/read_bundle`은 오프라인 형식 회귀 테스트용이다.
앱 실행 경로는 `pack_json/unpack_json/unpack_bundle`과 DB 큐만 사용한다.
명령 오류는 `code=...`와 종료 코드 2다. 불완전 분석/FAIL 결과도 유효 산출물이므로 종료 코드 0일 수 있다.

## VWP 수집 입력과 출력

회사 구현 작업의 통합 진입 문서는 [INTERNAL_AI_HANDOFF.md](INTERNAL_AI_HANDOFF.md), plan 원천 어댑터 계약은 [PLAN_INTEGRATION.md](vwp/PLAN_INTEGRATION.md)다.
`SanityPlanProvider.get_plan(run_reference) → CollectionPlan`은 기존 아래 plan dict를 반환하는 동기 hook이며 자동 수집/실행/DB 주입이 아니다.
`common/sanity_integration_contracts.py`의 `CollectionPlan`, `AdvisorContext`, `ClusteringProposalV2`, `ReviewProposal`은 기존 Python dict 계약의 타입 표기다. wire/storage 필드·schema·오류 판정·legacy V1 호환 처리는 바꾸지 않는다.
새 회사 분류 출력은 `validate_clustering_advice(..., require_rationales=True)`로 검사하고, review의 참조 없는 PASS/FAIL 제한을 유지한다. SDK 출력에서 사용자 확정이나 운영 연결 완료를 기록하지 않는다.

CLI: `python3 vwp/main.py demo --out DIR [--scenario complete|missing|malformed|unsupported]`
creates `DIR/run.json` and report files. CLI also supports
`collect --plan PLAN --reports REPORT_DIR --out DIR` to snapshot existing reports.
The collector does not run arbitrary shell commands. The plan describes already-finished
execution statuses; real sanity runner integration is out of prototype scope.

A plan is `{schema_version:1, run:{...}, inventory:{confirmed:true, source:"...",
version:"...", units:[...]}}`. Output `run.json` additionally contains `ready:true`,
`inventory.sha256` (digest of inventory without sha256), `files`, and `unexpected_files`.
`files` lists all copied report files as `{file_id,path,size_bytes,sha256}`; paths are
relative to the run.json directory and start with `reports/`.
Unexpected reports are copied and included in files and `unexpected_files` (path strings).
Missing expected reports remain in units but have no file entry.
Collector must refuse a nonempty output directory; source inputs must not be modified.

Required run fields: `run_id, project, design, stage, tool, tool_version` (nonempty strings).
Units have unique `review_unit_id` and unique `report` paths in prototype v1.

2026-10-22 운영 SSO용 legacy 결과는 선택형 `run.scope`에 정확히
`{schema_version:1, project_id:"project-example", block_id:"block-example"}`를 포함한다.
형식은 [sanity_scope.py](common/sanity_scope.py), 실제 등록/소속 계약은 [ACCESS_MANAGEMENT.md](oa/ACCESS_MANAGEMENT.md)를 따른다.
한 bundle은 등록된 프로젝트·블록 한 쌍에 속하며 자유 문자열 `run.project/design`을 ID로 매핑하지 않는다.
회사 plan provider가 수집 전에 실제 report 소속을 확인해 반환하고 collector·분석·bundle 해시에 그대로 봉인한다.
추가 키·null·잘못된 ID·미등록/부모 불일치를 허용하지 않는다. scope 없는 prototype 입력의 기존 hash/로컬 경로는 유지하되 SSO에서는 목록·건수 제외/개별 조회 404다.
봉인 후 scope를 붙이거나 바꾸지 않는다. legacy envelope에는 검증된 제출자가 없어 이 수신은 신뢰된 VWP 서비스 경로다.
이 legacy scope 계약은 별도 V2의 source 신원·승인/collection 계약을 대체하지 않는다. V2 SourceReceiver 운영 진입점은 별도로 제공한 ProductionWorkspaceStore/AccessStore 연결을 사용한다.


```json
{
  "review_unit_id": "u-numeric",
  "item_id": "numeric_limits",
  "report": "reports/numeric.csv",
  "parser": "numeric_csv_v1",
  "rule_version": "1",
  "condition": {"mode": "M1"},
  "identity_fields": ["object", "check_point", "mode"],
  "evaluation": {"operator": "le", "limit": 10, "unit": "ns"},
  "execution_status": "COMPLETED",
  "applicable": true,
  "exclusion": null
}
```

Two supported report formats:

- `numeric_csv_v1`: exact header `object,check_point,mode,code,severity,observed,unit`;
  final line `# END rows=N` required. No silent extra columns/lines. Finite numeric values;
  unit must match configured unit, mode must match condition.mode when specified;
  configured operator is `le` or `ge`; configured threshold is authoritative.
  Threshold must be an integer or exact decimal string (e.g. `"0.10000000000000001"`).
  Fractional JSON floating-point thresholds are rejected to avoid rounding before comparison.
- `summary_json_v1`: `{ "format_version":1, "complete":true,
  "results":[{"object":"...","check_point":"...","mode":"M1",
  "code":"CHECK-201","severity":"ERROR","status":"PASS"|"FAIL"}],
  "total":N }`. Missing/malformed fields or mismatched total block completion.
  This is reported evaluation: an applicable unit must omit `evaluation` or set
  it to null. Any non-null value, including `{}`, is `PARSER_SCOPE_MISMATCH`;
  a report's PASS cannot override a configured numerical threshold. This check
  applies before analysis writes, and to manifests received from older senders.

An applicable empty report or zero-record results must be incomplete in this prototype.
Non-applicable units require exclusion `{reason,approved_by,reference}`; no report is needed.
Never turn failed/missing execution into non-applicable based on observed report absence.

### Company report adapters

The two built-ins retain their contracts. Company formats implement
`ReportParser.parse(ReportSource, ParseContext)` from `common/sanity_parsers.py` and are
explicitly registered in `vwp_bridge/integration/corporate_parsers.py`. The default
registry is empty; the company stub raises `PARSER_NOT_CONFIGURED`.
`corporate_analyze.py analyze` injects that registry into the existing analyzer;
normal `main.py` does not load company code. A plan cannot select Python modules.

Registrations require parser ID, implementation_version, fixed evaluation mode
(`numeric`, `reported` or `none`), item IDs, tool, tool versions and rule versions.
Duplicate/built-in names and wildcard scopes are rejected; a registered scope
mismatch stops before DB writes. Reported mode cannot bypass a plan's numerical
evaluation. Numerical measurements use the existing exact Decimal evaluator.

Adapters emit ordered `ParsedRecord(fields, span, json_pointer=None)`,
`ReportMetadata(span, kind)`, or `UnparsedRegion(span, reason)` events, then exactly
one `ParseFinished(declared_records, complete, blockers=())` after reading EOF.
Spans originate in the common reader; common code derives raw excerpts and JSON
Pointer content from the source. Omitted/unread bytes become UNKNOWN evidence.
Completion/count/encoding blockers prevent review completion. Unknown sections
must not be silently classified as metadata. Empty results remain incomplete;
zero violations requires an explicitly evidenced result record.

Used registrations are included in the VWP immutable RUN's `parser_contract` and
payload `producer.parsers` as `{parser_id, implementation_version, evaluation,
item_ids, tool, tool_versions, rule_versions}`. They participate in analysis and
bundle IDs; installing/updating an adapter produces a separate immutable analysis.
For current numeric/reported registrations, no SQL migration or review-status changes
are needed. Legacy built-in-only analyses
keep the original producer/ID contract. V2 source analysis uses the separate
normalization revision described below. Adapter changes must increment implementation_version.

Expected malformed input yields PARTIAL/UNKNOWN evidence. Unexpected adapter
exceptions/invalid return contracts stop without sealing/enqueueing a success,
using fixed `PARSER_*` codes without exception text or automatic retries.
The common UTF-8 reader limits physical lines to 1MiB, raw spans to 64MiB and JSON
Pointer documents to 16MiB. See [parser handoff](vwp_bridge/PARSER_INTEGRATION.md)
for field requirements, source coverage and company-only validation. Real reports
stay inside the company; external Codex debugging uses synthetic inputs only.

**Collection-only mode (2026-10-28):**
[`evaluation="none"`](vwp_bridge/PARSER_INTEGRATION.md#collection-only-parser)
is an explicitly scoped registry mode. The plan's `unit.evaluation` must be absent
or null, including rejection of `{}`; it is never the string `"none"`. Numerical
policies must not be removed to bypass evaluation. `none` performs structural
field/source/count checks without calling the evaluator. Normal records retain
`check_status="UNKNOWN"`, add `evaluation_status="NOT_PERFORMED"`, and have no
error reasons. Actual malformed/unparsed records retain UNKNOWN/error reasons
without this marker. Result and OA report headers add `evaluation_mode="none"`
and `not_evaluated_count`. That count is a subset of `unknown_count` and
`status_counts.UNKNOWN`; the existing three status-count keys remain unchanged.
A complete collection has positive records, no blockers, zero evaluated records,
and a NOT_PERFORMED marker on every record. Missing/partial sources retain their
real blockers and do not become ready.

Source analyses using `none` and collections containing them use producer
`postgresql-source-2.2`. Numeric/reported source analyses retain 2.1, preserving
their IDs. Registry/producer contracts bind analysis/collection IDs, and ordered
part hashes bind record markers. OA revalidates the declared scope, policy,
producer, counts and actual part markers, including at OWNER confirmation.
Both bridges must receive this release before using `none`; old receivers reject
the new record metadata rather than silently accepting it. Wire schema 2 and
the existing four tables require no migration or GRANT changes. Normal collection
can feed the authorized OA AI/human group workflow; it never grants review
completion or creates a confirmed knowledge case. Legacy coverage still blocks
unevaluated/UNKNOWN records. COMMON-02F owns this common implementation.

## V2 수집·원천 인계

사용자별 하나의 REVISION 제출은 PRE 대응 원본과 여러 POST 슬롯의 report를 담는 **collection**이다. source 버전/실행은 report별로 유지하며, 제출 대상 POST에 PRE report를 재귀속하지 않는다. 상세 필드 배치·ID·수용 기준은 [SOURCE_COLLECTION_DESIGN.md](vwp/SOURCE_COLLECTION_DESIGN.md)가 기준이다.

| 설계 경계 | 목표 규칙 |
|---|---|
| 로컬 준비 | VWP 내부의 여러 원천 루트를 byte 동일 export하고 기존 collector에 전달. source/계획/보조 자료 분리·누락과 예상 밖 report 보존 |
| 메타데이터 | `collection`은 제출 범위, `units[].source`는 NET_VER/전체 REVISION/layout/ECO/mode/실제 실행·tool/version. 현재 condition에 임의 추가하지 않음 |
| 예상 목록 | 승인된 원천 또는 정확한 집합·프로필·블록 권한에 결합한 범위 선언. 스캔은 후보 발견이며 자동 승인 아님 |
| 식별 | project/block 포함 버전 키 → 검사 단위 → 실행 → 원본 snapshot → 분석 계약. 수신 요청 UUID와 분리 |
| 재수신 | 같은 실행/원문은 하나의 occurrence로 연결. 같은 PRE를 여러 POST가 참조해도 중복 발생 생성 없음 |
| 충돌/재검사 | 같은 실행·다른 원문은 충돌 보류. 새 원천 실행 ID는 별도 실행, parser/규칙 변경은 별도 분석 개정 |

**기존 `schema_version=1`, `CollectionPlan`·parser 입력·bundle/queue 계약은 그대로다.** 별도 V2 타입/validator는 `common/sanity_submission.py`에 있다. `sanity submit`과 새 source 작업자가 V2 봉인/분할 DB 전달·scope 승인 검증·workspace import를 수행한다. 회사 원천/신원·승인 SDK는 기본 차단 상태이며 허용된 `corporate_submission.py`에서만 연결한다. sidecar나 우연히 통과하는 추가 키를 전송 계약으로 간주하거나 사내 AI가 schema/메시지/테이블을 변경하지 않는다.

`inventory.confirmed=false`는 현재 `INVENTORY_UNCONFIRMED`와 기존 OA 판정 저장/완료 차단을 유지한다. 선언 승인도 누락/실패·잘림·미지원 parser·평가 기준 불명 차단을 해제하지 않는다. UI·SSO/CORPORATE·PoR 보호 상태는 이 설계 문서로 바뀌지 않는다.

### 사용자 제출·파일/DB 인계

[SUBMISSION_DESIGN.md](vwp/SUBMISSION_DESIGN.md)의 `sanity submit/status/retry`와 저장 경로를 구현했다. 설치·실행·설정·회사 SDK 지점은 [SUBMISSION.md](vwp/SUBMISSION.md)를 따른다. 기존 `collect --plan`은 schema 1 snapshot 준비만 수행하므로 새 제출 폴더/DB 작업자를 대신하지 않는다.

| 목표 경계 | 결정 |
|---|---|
| 사용자 호출 | VWP 터미널의 PATH 명령. 사용자는 DB 계정·plan JSON을 작성하지 않음 |
| VWP 내부 인계 | `/srv/sanity/vwp-submit/inbox/<principal-key>/.<submission-id>.partial`을 완성 후 같은 부모의 `.ready`로 공개. 인증·ACL 파일시스템은 VWP 사용자/bridge만 사용 |
| 신뢰할 원본 | bridge가 `/var/lib/sanity/vwp-bridge/submissions/<submission-id>/`에 직접 복사·해시 검증한 영속 보관본과 VWP DB 접수 |
| DB 흐름 | `sanity_vwp_data` → `sanity_vwp_to_oa` → OA staging/검증 → `sanity_oa_data`. OA 최종 반영 응답은 `sanity_oa_to_vwp` |
| 상태 확인 | bridge가 자기 DB/검증한 OA 응답에서 사용자 receipt를 작성. 로컬 인계와 `OA_STORED`, 검토 가능/완료/PASS는 별도 |
| 대용량 | SOURCE_PART envelope 포함 압축 전/후 각각 4MiB·최대 1,000 records. 전체 자료를 분할/검증하며 초과 한 건은 명시적으로 거부 |
| 중단/중복 | 동일 제출 UUID·내용 digest로 재개. 같은 원천 occurrence는 참조, 같은 실행/다른 원문은 충돌. 파일 보관과 DB commit 사이 복구 필요 |

`db/008_submissions.sql`은 기존 네 테이블 안에 VWP private `SUBMISSION`·`SOURCE_RUN`·`SOURCE_OUTBOX`, OA private `WS_SUBMISSION`·`WS_SOURCE_PART`, 공유 `SOURCE_MANIFEST/PART/COMMIT/RECEIPT`를 추가한다. 007까지 적용된 DB에 DBA가 검토·적용하며 기존 run 필수/불변성/종류별 scope 제약을 유지한다. schema 2 envelope는 `protocol=source-collection-v2`, submission UUID/content hash, collection ID, 검증된 actor, collection scope와 body를 결합한다. OA는 모든 조각/manifest/commit의 전체 건수·순서·hash·현재 담당 권한을 대조한 뒤 report와 최종 receipt를 원자적으로 공개한다. source header는 불변 parts의 hash에 결합되며 원본 파일은 VWP에 남긴다. legacy 메시지로 위장하거나 수신 상태를 수동 변경하지 않는다. 내부 AI 수정 허용 범위는 유지한다.

### 정규화·원문 위치·전송 개정

- UTF-8 디코딩에 실패한 원문은 VWP archive의 실제 bytes와 private evidence의
  `hex:<bytes>` 표현으로 보존하고 UNKNOWN/미완료 상태를 유지한다. 이 표현은
  가역적이므로 공개 raw에는 전체 `[REDACTED]`를 쓰고 `redacted=true`로 전달한다.
  `hex:` 뒤의 짝수 길이 16진 bytes가 유효한 UTF-8이 아닌 경우를 예약 표현으로
  취급한다. 평문 마스킹만 적용하거나 hex 자체를 공개하지 않는다. 원문 위치·근거 ID·
  private 원문 해시는 유지하며 해시를 마스킹된 문자열에서 다시 만들지 않는다.
  legacy의 길이 제한 샘플에서 `raw_omitted_chars>0`인 hex raw도 불투명한 원문으로
  가린다. 잘린 prefix가 유효한 UTF-8로 보인다는 이유로 공개하지 않는다. legacy
  bundle의 기존 민감정보 검출 시 봉인·발행 중단 계약은 유지한다.
- V2 record의 선택 필드 `json_pointer`는 `/results/0` 같은 비어 있지 않은
  JSON Pointer다. `/`로 시작하고 `~` escape는 `~0`/`~1`만 허용한다. VWP가
  실제 원본에서 해석한 Pointer를 OA part·record 조회까지 그대로 보존한다.
  OA는 형식을 검사하며 원본 파일이 없는 OA에서 Pointer 해석을 추정하지 않는다.
  필드가 없는 기존 record도 수용하되 정확한 JSON 위치를 사후 생성하지 않는다.
- V2의 producer는 `postgresql-source-2.1`이며 source 분석 계약과 collection ID에
  포함한다. 같은 실행·원본의 새 정규화 결과는 별도 analysis/report로 저장하며
  기존 근거·봉인·사람의 확정 기록을 덮어쓰지 않는다. legacy producer는 그대로다.
- 수신기를 먼저 갱신한 뒤 송신기를 갱신한다. 구 수신기는 새 Pointer 필드를
  거부할 수 있다. 완성된 기존 outbox는 재작성하지 않고 현재 마스킹·판정 계약을
  재검사한 뒤 발행한다. 민감한 구 packet은 `SENSITIVE_CONTENT`로 차단한다.
  PARSING 중 이미 쓴 part의 collection 계약이 현재 버전과 다르면
  `SUBMISSION_CONTRACT_MISMATCH`로 거부하고 archive/outbox를 보존한다. 같은
  UUID의 다른 개정 part를 섞지 않고 새 제출 UUID로 다시 제출한다. 같은 개정의
  중단은 기존 UUID로 재개한다. 이미 OA에 저장한 과거 자료는 자동 수정하지 않는다.
- OA는 최종 report 공개 직전에 DB staging의 manifest와 모든 part를 현재 규약으로
  다시 검사한다. 수신기 갱신 전에 보관한 자료도 수치 판정 방식·민감정보 마스킹·
  record/Pointer 검증을 거친다. hash 일치는 현재 정규화 규약의 충족을 대신하지
  않는다. 실패하면 report·버전·PoR·AI 입력을 공개하지 않고 제출을 `REJECTED`,
  검토 준비 상태를 `BLOCKED`로 응답한다. 기존 불변 part를 수정해 통과시키지 않는다.
  Pointer가 없는 유효한 구버전 자료는 그대로 수용한다.

## vwp-bridge payload

CLI: `python3 vwp_bridge/main.py analyze --input DIR/run.json [--out RECEIPT_DIR]`
Must validate readiness, inventory hash, safe paths and actual file hashes/sizes before
parsing. Produce valid incomplete payload for missing/unsupported/malformed units.
Integrity mismatch is an operational error. Preserve all occurrence/evidence records in the VWP private PostgreSQL table.
Bundle id is deterministic from the payload excluding bundle_id (use `b-` + digest).
Repeat identical input produces identical bundle id and bytes. Changed data/rules yield new id.
Prototype uses content-addressed bundles, not revision ordering; OA explicitly selects bundle.

Required payload fields:

```json
{
  "schema_version": 1,
  "bundle_id": "b-example",
  "run": {"run_id":"demo-001","project":"demo","design":"chip","stage":"sanity","tool":"synthetic","tool_version":"1"},
  "inventory": {"confirmed":true,"source":"plan.json","version":"demo-v1","sha256":"...","expected_unit_ids":["u-numeric"]},
  "review_units": [{
    "review_unit_id":"u-numeric","item_id":"numeric_limits","report":"reports/numeric.csv",
    "execution_status":"COMPLETED","applicable":true,"exclusion":null,
    "parse_status":"COMPLETE","check_status":"FAIL","blockers":[],
    "record_count":2,"evaluated_count":2,"unknown_count":0,
    "status_counts":{"PASS":0,"FAIL":2,"UNKNOWN":0},
    "rule_version":"1","identity_fields":["object","check_point","mode"]
  }],
  "finding_groups": [{
    "finding_group_id":"g-example","review_unit_id":"u-numeric","item_id":"numeric_limits",
    "message_code":"CHECK-101","severity":"WARNING",
    "identity":{"object":"top/A","check_point":"P1","mode":"M1"},
    "identity_status":"SAME_FINDING","rule_version":"1","occurrence_count":2,
    "status_counts":{"PASS":0,"FAIL":2,"UNKNOWN":0},
    "examples":[{"record_id":"r-1","file_id":"f-1","line_start":2,"line_end":2,"raw":"...","observed":12,"expected":10,"unit":"ns"}],
    "examples_omitted":1
  }],
  "global_blockers":[],
  "source_files":[{"file_id":"f-1","path":"reports/numeric.csv","size_bytes":100,"sha256":"..."}],
  "producer":{"version":"postgresql-2"}
}
```

review_units are a bijection with expected_unit_ids, including missing and unsupported items.
Units also retain parser, condition, evaluation and identity_fields from the plan for report
provenance and dashboard display (not for re-running VWP evaluation in OA).
`check_status`: PASS/FAIL/UNKNOWN/NOT_APPLICABLE. `parse_status`: COMPLETE/PARTIAL/MISSING/
UNSUPPORTED/NOT_APPLICABLE. Blockers are short string codes; never put raw credentials in them.
Global blockers include unconfirmed inventory and unexpected reports.
Group only FAIL and UNKNOWN records, keep PASS per-record evidence locally.
Missing identity fields yield isolated UNDECIDED groups, never coalesce nulls; identity-only
missing fields need not block a valid numerical judgment. Missing code/severity, bad measurement,
or mismatched mode/unit do block evaluation. Default identity key includes code, severity,
review unit and configured identity fields. At most 3 representative examples per group.
SAME_FINDING requires a nonempty, unique identity_fields declaration on its unit and exactly
those identity keys. Known textual fields (object/check_point/mode and run/code/severity
identifiers) require nonblank strings. Custom identity fields accept nonblank strings or finite
numbers, never bool/null/containers. Invalid identity values produce isolated UNDECIDED groups.
Every example file_id must resolve to a source_file whose path equals its review unit's report;
an unrelated but listed report is not valid evidence. These checks also apply at OA reception.
Older SAME_FINDING bundles without a declaration must be regenerated, not implicitly trusted.

## PostgreSQL 저장·전달 계약

이 절의 행별 EVIDENCE·그룹 연결·ANALYSIS_BUNDLE은 legacy 저장/전달 계약이다. V2 source는 선택형 [012 근거 묶음](db/EVIDENCE_PAGES.md)을 사용하면 `SOURCE_EVIDENCE_PAGE`의 검증된 본문에서 모든 개별 로그를 복원한다. 물리 행 수와 논리 로그 수를 구분하며, 원본 RAW·record ID·원문/전체 fields·V2 SOURCE_PART/COMMIT/RECEIPT·OA/사람의 판정 계약은 유지한다. 012가 없거나 이미 행별 근거가 있는 분석은 기존 형식을 사용한다.

선택형 [013 공유 저장](db/TOTAL_STORAGE.md)은 같은 마스킹된 SOURCE_PART의 불변 본문을 기존 공유 큐 한 곳에 보존하고 VWP/OA private에서 FK로 참조한다. 논리 packet JSON·message ID·scope·기존 권한을 유지한다. `__source_commit_gate__=true`인 준비된 part는 같은 제출/내용/collection의 최종 COMMIT가 있어야 claim되며, 명시적 message ID도 우회하지 않는다. ACK 후에도 참조된 큐 행을 보존한다. 원본/private 원문은 공유 마스킹본으로 대체하지 않는다.

VWP private `sanity_vwp_data`의 RUN/UNIT/FILE/GROUP/EVIDENCE는 종류별 별도 행이다.
모든 PASS/FAIL/UNKNOWN 원문과 판정·위치·그룹 연결을 EVIDENCE에 보존한다.
입력 계약에서 결정한 UUID가 analysis_id이며 같은 분석의 동시 실행은 advisory lock으로 차단한다.
UNIT/FILE parent는 RUN, GROUP/EVIDENCE parent는 UNIT이다. 같은 분석 parent 존재는 DB FK,
parent 종류와 unit/file/group 연결은 앱이 검증한다.

최대 256행 또는 약 4MiB 배치로 저장한다. 중단 시 BUILDING 행을 남기고 재실행으로 복구한다.
checkpoint로 전체 검사를 건너뛰지 않고 모든 입력을 다시 계산해 기존 불변 행과 대조한다.
최종 전체 행 수·단위별 근거 수·그룹 집계를 대조한 뒤 SEALED와 공유 메시지 등록을 원자적으로 확정한다.
SEALED 재실행은 근거를 갱신하지 않는다. 변경·누락·추가 근거는 EVIDENCE_CONFLICT다.

VWP→OA 분석 메시지 필드는 다음과 같다.

- `message_id = bundle_id = "b-" + SHA256(canonical_json(payload without bundle_id))`.
- `message_type=ANALYSIS_BUNDLE`, `project/run_id`는 payload와 일치.
- `payload={schema_version:1, inventory_sha256, producer_version}`은 공개 명세.
- `payload_gzip`은 canonical JSON의 결정적 gzip(mtime=0) 원본 bytes.
- `payload_sha256`은 압축 bytes의 해시, `uncompressed_bytes`는 정확한 해제 크기.
- 압축 전후 각각 최대 64MiB. 스키마·중복 키·내용 ID·필드 간 의미도 검사한다.
- 같은 메시지를 다시 등록할 때 모든 불변 내용을 대조한다. 단순 충돌 무시만으로 성공 처리하지 않는다.

공유 큐는 READY/RETRY의 도래 작업 또는 PROCESSING의 만료 작업을 대상으로 한다.
`FOR UPDATE SKIP LOCKED`로 한 행을 점유하고 새 token/만료/attempt_count를 기록한 뒤 잠금을 놓는다.
완료 갱신은 PROCESSING + 동일 token + 미만료 조건으로만 수행한다.
0행 갱신이면 LEASE_LOST이며 수신 private 기록과 함께 전체 완료 트랜잭션을 rollback한다.
의미/무결성 오류는 REJECTED, 일시 DB 장애·프로세스 중단은 lease 만료 후 다음 호출에서 복구한다.
기존 `receive` CLI는 한 건 처리하며 heartbeat를 사용하지 않는다. 별도 `work` 프로세스는
분류 큐를 지속 polling하고 VWP 분류 작업은 heartbeat로 lease를 갱신한다.

이 legacy 수신/단위 검토 흐름은 OA private `sanity_oa_data`의 RECEIVED_BUNDLE, REVIEW_UNIT, REVIEW_EVENT, REPORT를 사용한다. 다른 절의 workspace/SSO/AI 작업 record_type과 구분한다.
원본 수신 payload와 context={interface:"dashboard-v1",ai:{status:"NOT_CONFIGURED"}},
전체 REVIEW_UNIT, 초기 REVIEW_EVENT(개정 0), REPORT를 저장한다. 공유 RECEIVED까지 같은 트랜잭션이다.
재수신은 기존 검토/맥락을 유지하며 새 번들은 승인 없이 시작한다. Jira 작업·전용 컬럼·인덱스는 없다.

## 사람 검토·불변 보고서

실제 CLI 목록은 [OA 안내](oa/README.md)를 따른다. `--state`, `--manifest`는 없다.
검토할 번들 ID는 명시적으로 선택하며 최신 번들 자동 선택/번들 순서 관리는 미구현이다.

검토마다 UUID request_id를 받는다. 동일 명령 재시도는 동일 UUID를 쓰고,
다른 내용으로 재사용하면 REQUEST_CONFLICT다. 새 판단은 새 UUID를 쓰며 새 개정을 생성한다.
같은 본문으로 되돌아가는 A→B→A도 서로 다른 REVIEW_EVENT다.
대시보드 검토 API는 request_id와 expected_revision을 필수로 받는다.
RECEIVED_BUNDLE 행 잠금 아래 단위·blocker·현 개정을 검사하고 기대 개정 불일치는 REVISION_CONFLICT로 거부한다.
REVIEW_EVENT·current_review_revision/version·REPORT를 원자적으로 기록한다.
수신본/전체 단위/연속 개정이 일치하지 않으면 STATE_CORRUPT로 중단한다.
현재 개정 보고서는 재렌더링 bytes 및 SHA-256과 비교한다.

조회·보고서 다운로드는 repeatable-read/read-only 트랜잭션이다.
사람 검토는 blocker를 무시하거나 알려진 FAIL을 PASS로 바꿀 수 없다.
검토 완료와 sanity 통과는 별도이며 수신·다운로드는 검토를 승인하지 않는다.

2026-10-15 합의한 후속 판정 정책은 [사람의 판정 기준 축적](requirement.md#human-judgment-learning)을 따른다.
원본 규칙 FAIL과 별도로 사용자의 PASS를 사유/근거와 함께 저장하고, 확정 사례·조건을 다음 AI review에 참조하는 것이 목표다.
위 FAIL 제한은 현재 legacy bundle 계약이다. 별도 운영 workbench의 그룹 판정과 독립 workspace의 사용자 판정은 원본 FAIL을 보존하면서 사람의 PASS/FAIL을 저장한다. legacy 승인 API를 이 새 판정 경로로 소급 변경하지 않는다.

Jira 게시·시뮬레이터는 폐지했다. 과거 CLI publish는 --simulate/--dry-run을 포함해
항상 JIRA_DISABLED로 즉시 실패하며 DB 작업을 만들지 않는다. --jira-context는 지원하지 않는다.
과거 Jira context 보고서의 렌더러 호환만 유지하며 새 수신 맥락에는 Jira 데이터를 만들지 않는다.
과거 SQLite/16테이블 데이터를 읽거나 자동 이전하지 않는다.

## 추가 근거 요청/응답

OA는 `sanity_oa_to_vwp`에 EVIDENCE_REQUEST를 넣는다.

- message_id는 `request-<UUID>`, project/run/bundle은 OA 수신본에서 얻는다.
- payload는 `{schema_version:1,review_unit_id,record_ids,reason,ttl_seconds}`.
- 서로 다른 record ID 1~100개, reason 1~1000문자, TTL 1~86400초(기본 3600).
- 임의 경로·명령·SQL은 허용하지 않는다. 현재 group/range 선택은 미구현이다.
- 같은 요청 UUID는 동일 내용과 최초 expires_at을 유지한다. 새 요청은 새 UUID다.

VWP `respond --request-id ... [--reject]`는 특정 요청만 점유한다. `work`는 다음 요청을 처리한다.
유효 기간·봉인된 분석·단위·파일 연결·전체 선택 기록·원문 해시를 검사한다.
일반 sanity 원문은 요청별 수동 승인 없이 응답한다. `--approve-export`는 호환용 선택 옵션이다.
EVIDENCE_RESPONSE의 message_id는 `response-<UUID>`, correlation_id는 요청 message_id다.
project/run/bundle 일치는 DB FK로도 확인한다. 최종 결과는 FULFILLED/REJECTED/EXPIRED다.
FULFILLED에만 gzip을 저장하며 내부에는 schema_version, request_id(요청 message_id),
bundle_id, review_unit_id, records가 있다. 각 record에는 마스킹 후 전체 raw와 raw_sha256,
VWP 원본의 source_raw_sha256, redacted, redaction_policy=credentials-v1이 포함된다.
등록된 비밀값·password/token/Authorization·DB URI 암호·private key 패턴을 마스킹하고 private 원문은 수정하지 않는다.
이는 범용 DLP가 아니다. 분석 번들/분류 입력/출력은 자격증명 감지 시 마스킹으로 identity를 바꾸지 않고 전달을 중단한다.
최종 응답 등록과 요청 RECEIVED는 같은 트랜잭션이며 부분 성공은 없다.

OA 수신은 요청한 ID 집합과 정확히 일치하는지, source/unit 연결과 원문 해시를 검사한다.
검증된 bytes는 공유 응답 행에 남는다. `--out`은 선택적인 OA 내부 파일일 뿐 통신 경로가 아니다.
근거 수신으로 원래 분석 payload나 사람 검토 판정을 자동 변경하지 않는다.

## 대시보드 API와 인증

SSO 확장 계약과 사내 구현 방법은 [SSO_INTEGRATION.md](oa/SSO_INTEGRATION.md)를 따른다.
기본 `SANITY_AUTH_MODE=local_token`은 아래의 기존 Bearer 방식이다. `sso` 모드에서는 검증된
`VerifiedIdentity(issuer, subject)`를 `UserDirectory`로 내부 `user_id`에 연결한다.
`create_app(auth_mode="sso", identity_provider=..., user_directory=..., auth_router=...)`로 주입하며,
인증 router는 `/auth` 아래에 등록한다. 공급자 실패·미구현 시 로컬 토큰으로 우회하지 않는다.

`GET /api/session`은 기존 필드와 `user_id`, `auth_method`, `authorization_ready`,
`is_admin`, `can_read_business`, `can_receive`를 반환한다. 운영 관리자/권한 엔진 미설정 시
`writable=false`, `authorization_ready=false`이며 업무 API는 `503 AUTHORIZATION_NOT_IMPLEMENTED`로 차단한다.
설정된 경우 authorization_ready는 엔진 준비를 뜻하며 미배정도 정상 인증 상태다.
OA 비공개 `SANITY_ADMIN_USER_ID`의 활성 SSO 사용자 한 명만 등록/승인/거절/회수를 관리하고 report 권한은 별도 승인한다.
TOP은 프로젝트 조회, OWNER는 블록 조회·검토·분류·근거 변경이며 목록 count/페이지 SQL과 대상별 API에서 최신 범위를 검사한다.
관리·신청 API, 불변 WS_CONFIG 이력, `READ COMMITTED` 공유/배타 잠금과 전체 입력 계약은 [ACCESS_MANAGEMENT.md](oa/ACCESS_MANAGEMENT.md)를 따른다.
SSO 쓰기는 JSON·`X-Sanity-Request: 1` 및 명시적 cross-site 차단을 요구한다. CORS·임의 forwarded header 신뢰를 추가하지 않는다.
인증 실패는 401, 계정 미연결·비활성/금지 행위는 403, 범위 밖·미봉인 결과는 404, 어댑터 미설정·장애는 503이며 고정 code만 반환한다.
회사 로그인 redirect·callback·세션 생성/폐기와 callback/logout CSRF는 사내 구현 범위다.


서버는 python oa/dashboard_api.py로 실행하며 127.0.0.1:8000에만 bind한다.
npm ci / npm run build 결과 dist를 같은 origin으로 제공한다. 개발 서버는
127.0.0.1:5173에서 /api를 8000으로 proxy한다. 외부 bind는 금지한다.
이 HTTP는 OA 브라우저→OA API이며 bridge 간 통신은 여전히 공유 DB만 사용한다.

private .env의 SANITY_DASHBOARD_TOKEN은 최소 32자이고 SANITY_DASHBOARD_REVIEWER는
서버가 고정하는 검토자다. 로컬 모드의 업무 조회/변경은 Bearer 인증, SSO 모드는 검증된 same-origin 세션과 대상별 권한이 필요하다.
브라우저는 토큰을 메모리에만 보관한다. localStorage·URL·로그·VITE_*나 빌드 파일에 넣지 않는다.
health와 정적 UI는 공개 가능하지만 업무 정보를 제공하지 않는다.
이 Bearer 경로는 단일 사용자 로컬 prototype이며 회사 SSO 실패 fallback이 아니다. 운영 SSO의 현장 검증은 별도다.

- GET /api/session: 서버 검토자, access_mode, writable 설정. 기본 HTTP 모드는 읽기 전용 안내.
  이 응답만으로 DB 연결·권한이나 업무 조회 어댑터가 준비됐다고 보지 않는다.
- GET /api/bundles: offset/limit 페이지, 전체 수.
- POST /api/receive: 로컬 토큰 prototype에서 빈 JSON 객체로 다음 메시지 한 건 수동 처리. SSO는 관리자 포함 403이며 운영 수신은 신뢰된 서비스 작업자/CLI가 담당.
- GET /api/bundles/{bundle_id}: unit_offset/unit_limit, 전체 coverage, 단위 페이지,
  최근 REVIEW_EVENT 50개와 전체 history_total.
- GET /api/bundles/{bundle_id}/units/{unit_id}/groups: 그룹 페이지와 전체 수.
- POST /api/bundles/{bundle_id}/reviews:
  {unit_id,conclusion:PASS|FAIL,reason,request_id:UUID,expected_revision:integer}.
  reviewer는 요청 본문을 받지 않고 서버 인증 결과를 사용한다. SSO의 새 검토는 안정 user_id를 기록하며 기존 이력은 덮어쓰지 않는다.
- GET /api/bundles/{bundle_id}/report: 현재 개정의 저장된 불변 Markdown 보고서 다운로드.
- POST /api/bundles/{bundle_id}/evidence:
  {unit_id,record_ids,reason,request_id:UUID,ttl_seconds}; VWP 작업자가 자동으로 범위/무결성을 검사한다.
- GET /api/bundles/{bundle_id}/requests: 요청 상태 페이지.
- POST /api/evidence/{request_id}/receive: 응답 명시적 수신.
- GET /api/evidence/{request_id}: 검증·수신된 응답의 제한된 원문 미리보기와 잘림 표시.
- GET /api/evidence/{request_id}/records/{record_id}: 수신된 요청에 포함된 원문만 페이지 조회.
  start>=0, length 기본 8000·최대 16000자. total_chars·원본/전송본 해시·redacted를 함께 반환한다.

목록 페이지 기본값은 엔드포인트별로 다르며 limit은 최대 100이다. coverage 분모는 화면 페이지와 독립적이다.
POST는 JSON 객체를 사용하고 추가 필드·잘못된 ID/UUID·범위 밖 수치는 거부한다.
오류는 짧은 code로 반환하며 비밀·원문 예외를 응답에 노출하지 않는다.
화면의 합성 demo는 사용자가 명시적으로 진입하는 메모리 상태이며 새로고침 시 초기화한다.
인증/DB/API 오류에서 demo로 자동 fallback하지 않고 demo 결과를 실제 DB 저장으로 표현하지 않는다.

## VWP AI와 분류 계약

아래 키·한도·메시지는 **현재 legacy VWP 실행 계약**이다. OA 모델 실행/설정에는 위 별도 계약과 [AI_RUNTIME.md](oa/AI_RUNTIME.md)를 사용한다. OA의 우선 연동·사례 검색·직접 근거 판정·품질 비교는 [AI 판단 설계](AI_DECISION_DESIGN.md)와 [COMMON-03~06](INTERNAL_AI_HANDOFF.md#5-codex-공통-연결의-상태와-남은-작업)을 따른다.

`SANITY_AI_MODE=api|cli`와 모델을 명시해야 한다. 미설정은 AI_NOT_CONFIGURED 실패이며 합성 응답으로 대체하지 않는다.
URL은 전체 HTTPS 주소, token은 Bearer, CA는 선택적인 절대 PEM 경로다. redirect·환경 proxy·자동 재시도·API→CLI 우회는 없다.
HTTPX I/O timeout은 1~600초(기본 120)이고 응답 chunk마다 경과 시간도 검사한다. 엄격한 전체 wall-clock deadline과 동일하지 않다.
입력 512KiB·출력 128KiB를 제한하고 원격 오류 본문/접속값은 노출하지 않는다.

OpenAI 공식 [Chat Completions](https://developers.openai.com/api/reference/resources/chat/subresources/completions/methods/create)와
[Responses 전환 문서](https://developers.openai.com/api/docs/guides/migrate-to-responses)의 wire 형식을 따른다.
내부 서버의 지원 범위는 아직 확인되지 않았다.

- `SANITY_AI_API=chat_completions`: model/stream=false/messages(system,user). 선택 JSON 모드는 response_format.type=json_object.
  choices 한 개, finish_reason=stop, refusal 없음, message.content의 strict JSON만 허용한다.
- `SANITY_AI_API=responses`: model/stream=false/input/store=false. 선택 JSON 모드는 text.format.type=json_object.
  status=completed와 message/output_text를 확인한다. 미완료·refusal·중복 키·비정상 수치·잘린 JSON을 거부한다.
- CLI: 절대 argv 배열, shell=false, stdin JSON과 stdout strict JSON. DB/dashboard 환경변수는 전달하지 않고
  PATH/LANG/LC_ALL/TMPDIR 및 선택 AI token만 전달한다. stderr는 버리고 timeout/초과 출력 시 자식 프로세스 그룹을 종료한다.

AI 논리 출력은 `{schema_version:1,rules:[...],explanation,needs_clarification:boolean}`이다.
규칙은 `{identity_fields:[...],where:{...},reason,evidence_ids:[...]}`이며 코드/스크립트는 허용하지 않는다.
필드는 object/check_point/mode/code/severity/observed/expected/unit만 허용하고 code/severity 외 식별 필드를 최소 하나 요구한다.
where는 정확한 scalar 일치이고 필수 identity 누락·상충하는 규칙은 UNDECIDED로 남긴다.
AI 근거 ID는 입력 표본에 속해야 하고 사용자가 준 규칙의 근거도 같은 scope의 봉인된 VWP 근거여야 한다.

scope는 project/design/stage/tool/tool_version/item_id/parser/rule_version/condition의 정확한 조합이다.
선택형 `run.scope`가 있으면 canonical 프로젝트·블록 객체도 분류 scope/hash에 포함한다.
표시 문자열이 같아도 블록 간 규칙·지속 그룹·과거 근거를 재사용하지 않고, scope 없는 과거 결과를 scoped 결과와 섞지 않는다.
이 보강은 legacy 공통 matcher의 범위 계약이며 workspace V2용 회사 AI job 연결 완료를 뜻하지 않는다.
rule ID는 scope+identity_fields+where, 지속 group ID는 rule ID+identity로 결정한다. 설명/근거 추가만으로 ID가 바뀌지 않는다.
규칙 의미/범위가 바뀌면 ID가 바뀌므로 의미 기반 그룹 연속성이나 임의 merge/split을 자동 추정하지 않는다.

### DB 작업과 누락 검증

OA가 `CLUSTER_REQUEST`, message_id=`cj-<요청 UUID>`, TTL 24시간을 등록한다. payload는
schema_version/job_id/bundle_id/unit_id/scope/base_revision/base_job/rules/known_groups/command를 포함한다.
새 요청은 `catalog_exclusions`도 보존한다. 각 항목은 group_id/group_revision/source_bundle/source_unit/source_job/reason이며,
비교 소스에서 제외한 과거 확정본을 설명한다. 이전 요청의 이 필드가 없으면 빈 목록으로 조회한다.
command는 request_id/expected_revision/instruction/rules(명시 수정 또는 null)/mode(AUTO|AI|RULES)/reviewer다.
UUID 재전송은 최초 내용/만료를 유지한다. active 분류 규칙→저장된 scope 규칙 순으로 재사용한다.

VWP는 lease와 job advisory lock을 사용하며 전체 기록을 스캔해 최대 64건/128KiB의 결정적 표본을 만든다.
미분류를 우선하고 개별 표본 2KiB 초과는 생략 수에 기록한다. 표본 크기·누락·방법은 provenance에 남는다.
규칙만으로 모두 배정되고 피드백이 없으면 AUTO는 AI를 호출하지 않는다. AI는 모든 원문을 읽은 것으로 표시하지 않는다.
AI 호출 직전 ai_calling을 영속화하고 DB 트랜잭션 밖에서 호출한다. 대기 중 20초마다 lease를 갱신한다.
중단으로 응답 여부가 불확실하면 AI_OUTCOME_UNKNOWN으로 종료하고 같은 작업에서 자동 호출하지 않는다.

전체 기록에 규칙을 적용한 `CLUSTER_PART`는 최대 128건/구성원 약 512KiB(+header 최대 64KiB)이며 gzip/hash를 사용한다.
private 조각·공유 메시지·마지막 record ID 체크포인트를 같은 트랜잭션으로 저장한다. 확정 header 이후 중단은 AI 재호출 없이 재개한다.
header는 scope_hash/rules/provenance/explanation/needs_clarification이다.
member는 원본 record/file/unit/index, 판정, 정규화 fields/location, 소속/규칙/identity/method/reason,
연관 후보 배열과 비교 방법/비교 그룹 수를 포함한다. raw는 별도 원문 요청으로 가져온다.
최종 `CLUSTER_RESULT`는 COMPLETE/FAILED, 조각 수와 COMPLETE일 때 순서별 누적 해시·header를 포함한다.

OA는 header 일관성, 원본 ID 공식/범위, 조각 간 중복, 순서/누적 해시, 전체 건수와 PASS/FAIL/UNKNOWN 건수,
`분류 대상=배정+미분류`를 검증하고 소속/후보를 동일한 순수 함수로 재계산한다.
최종 메시지가 먼저 와도 누락 조각이 있으면 RECEIVING이다. 오류 조각은 REJECTED로 기록하며 남은 기록을 임의로 완료하지 않는다.
READY는 전체 전송/계산 검증 완료이지 사람 확정이 아니다. FAILED/REJECTED/NEEDS_CLARIFICATION은 적용할 수 없다.

### OA 조작과 후보 경계

공통 경로는 `/api/bundles/{bundle_id}/units/{unit_id}/clustering`이다.

- GET: 작업 목록/phase/표본 provenance/개정/최근 50개 이벤트/저장 규칙/입력 차단 사유와 작업별 catalog_exclusions.
- POST: `{request_id,expected_revision,instruction,rules,mode}`. 서버 고정 reviewer를 사용한다.
- GET `/{jid}/members`: offset/limit, filter=all|unresolved|candidates, 선택 group_id. 이전 job 소속과 변경 여부도 반환한다.
- POST `/{jid}/receive`: 빈 JSON, 해당 job의 메시지만 최대 20개 수신. 또는 OA `work` 프로세스를 사용한다.
- POST `/actions`: `{request_id,expected_revision,action,job_id,reason,record_id?,group_id?,decision?}`.

APPLY는 기준 개정이 현재와 같을 때만 현재 분류를 바꾸고 확정을 해제한다.
CONFIRM은 미분류 없음·전체 입력 완료·미해결 후보 없음·참조 개정 유효가 필요하다.
SAVE_RULES는 현재 확정 후 별도로 실행하고 정확한 scope의 다음 run에 재사용한다.
DEFER는 현재 활성 READY 작업의 확정을 해제하며 참조가 오래되었거나 순환 참조가 있어도 허용한다.
기대 개정·요청 UUID·사유 검증은 그대로 적용하고, 다른 작업을 보류하거나 이를 승인 경로로 사용하지 못한다.
각 동작은 별도의 classification_revision·불변 CLUSTER_EVENT로 기록하며 sanity review_revision/REPORT는 바꾸지 않는다.

현재 FAIL 그룹 기준은 다른 번들에서 사람이 분류 확정했고 FAIL 구성원이 존재하는 그룹이다. 독립 FAIL 태그 UI는 후속이다.
한 scope 최대 100그룹을 정확한 필드로 대조하고 초과 시 CATALOG_LIMIT로 실패한다. top-1 확률 선택이나 의미 검색이 아니다.
새 요청은 각 지속 그룹의 발생을 최신순으로 확인하여 현재 확정·참조가 유효하고 요청 대상에 직·간접 의존하지 않는 소스를 선택한다.
최신 소스가 `STALE_REFERENCE` 또는 `SELF_DEPENDENCY`이면 제외 사유/원본 개정을 기록하고 같은 그룹의 다른 번들에서 더 이전의 유효한 발생을 찾는다.
유효한 소스가 없으면 그 그룹을 known_groups에 넣지 않는다. 자기 자신에 의존하는 후속 분류를 제외하여 A→B→A 재검토의 순환을 막는다.
과거의 비활성 개정을 다시 활성화하거나 제외된 소스의 확정을 자동 복구하지 않는다. 비교 건수는 known_groups 기준이고 제외된 확정본의 비교 완료를 뜻하지 않는다.
소스 조회는 32건씩 keyset 페이지로 수행한다. 한 요청에서 최대 1,000개 그룹 발생을 확인하며 초과하거나 의존 검증 상한을 넘으면
CATALOG_LIMIT로 전체 요청을 거부한다. 후보/제외 내역을 크기 제한에 맞추려고 조용히 잘라내지 않는다.
code/severity만 일치하는 것은 후보 근거로 부족하며 identity의 명확한 차이는 제외한다. 일부 필드가 없으면 NEEDS_EVIDENCE다.
각 후보에 source_bundle/unit/job, group_revision, identity·일치/차이/누락 필드·FAIL 근거 최대 3개를 둔다.
PASS 기록에도 후보를 표시하지만 소속은 NOT_TARGET로 남는다. CANDIDATE 결정은 CONFIRM_MATCH/DIFFERENT/DEFER이며
연관 확인은 소속 이동이나 개별 판정 변경이 아니다. DEFER/미판단은 확정을 막는다.
참조 그룹 개정이 바뀌면 needs_recheck를 표시하고 적용/확정/규칙 저장을 거부하여 새 요청을 요구한다.
참조 그룹이 의존한 이전 분류 참조도 대조하며 최대 1,000개 의존 snapshot을 넘으면 유효하다고 선언하지 않는다.
이전 코드에서 생성한 순환 참조 작업도 적용/확정/규칙 저장을 거부한다. 이미 적용된 작업은 보류하거나 최신 후보로 새 요청을 만들어 복구한다.

현재 루프는 기존 정규화 필드 계약을 사용한다. 회사 파서는 같은 계약으로 연결할 수 있으나 실제 형식은 사내 검증이 필요하다.
새 파서 자동 생성, 정교한 의미 비교/반례 검색, 임의 구성원 이동,
그룹 lineage, 수만 report의 성능·운영 인증 검증은 남아 있다. 기존 보고서의 ai=NOT_CONFIGURED는
봉인된 최초 분석 context이며 별도 분류 job의 모델 호출 provenance와 혼동하지 않는다. Jira는 사용하지 않는다.

## Coverage and finality

2026-10-15 사용자 정정에 따른 1단계 목표는 **report 로그와 과거 사람 확정 사례를 참고한 AI 초기 그룹 생성 → 사람 수정·확정 → DB 사례 축적 → 다음 AI 초기 분류의 참고 입력**이다.
AI는 각 report의 그룹 구성·이름·근거를 새로 제안한다. 과거 그룹과의 필수 일대일 매칭이나 고정 분류 유형 사전의 선택으로 대체하지 않는다.
확정 사례를 다음 AI 입력 후보로 축적하는 것이 기본 동작이며 별도 재사용 체크/필수 기준 작성이 선행 조건은 아니다.
독립 합성 일반 화면은 정확한 필드 비교·선택형 사례 재사용 엔진을 유지한다. 운영 workbench는 위 OA AI job·직접 근거 V2·현재 확정 사례 검색을 사용하며 실제 사내 모델 연결/품질은 별도 검증한다.
외부 Codex의 실제 호출은 [합성 mock 시험](oa/WORKSPACE.md#codex-모델을-사용하는-합성-mock-시험)에만 추가했다. 이는 회사 VWP/SDK 연결이 아니다.
명시적으로 켠 합성 workspace의 전용 `/clustering/ai`와 **AI로 그룹 만들기** 버튼은 UUID 예약·고정 입력 검사 후 실제 모델을 호출한다. 기존 제안 API의 mode는 확장하지 않는다. 호출 상태/receipt는 기존 `WS_CLUSTER.state`에 보관하며 새 물리 테이블이나 브리지 간 전송 경로를 만들지 않는다.
기존 `reusable`/버전 호환 승인·저장 데이터·어댑터 서명이나 내부 AI의 수정 허용 범위는 유지한다.

독립 workspace의 두 단계는 `WS_CLUSTER_EVENT`의 확정 분류를 `WS_GROUP_REVIEW_EVENT/PROPOSAL`이 참조한다.
원본 record ID 전체를 중복/누락 없이 배정하고, 분류 개정과 판정 개정을 분리한다. 그룹 ID·분류 event·구성 해시가 바뀌면
이전 그룹 판정은 이력으로만 보존하고 최신 확정 판정/재사용 기준에 포함하지 않는다. 제거된 그룹도 이력을 조회할 수 있다.
사람이 확인한 분류 사례와 판정 조건은 각각 다음 분류와 그룹 판정에 참고한다. 정확한 필드로 구분할 수 없는 분리·사례 충돌은
임의 자동 배정/확정으로 처리하지 않는다. 상세 API와 명시적 처리 한도는 [두 단계 계약](oa/WORKSPACE.md#두-단계의-그룹-검토)을 따른다.

후속 순차 검토의 기준은 [요구사항 18절](requirement.md#large-report-review-flow)이다. 입력 규모는 **한 sanity item의 report 안의 로그·위반 항목 평균 약 10,000건이며 100,000건 이상도 가능**하다. 10,000건은 상한이나 고정 크기가 아니며 report 파일 10,000개를 뜻하지 않는다.
전체 로그 분류·사람 확정 이후에만 확정 그룹별 판정을 진행하며, 분류 초안/보류·오래된 개정으로 새 그룹 판정을 확정할 수 없다.
분류 coverage는 전체 ID 집합을 대조하고 페이지 조회·분할 작업·대표 근거가 전체 처리/완료를 대신하지 않는다.
그룹 판정 완료는 현재 유효한 전체 분류의 그룹별 사람 PASS/FAIL 확정을 기준으로 하며 미검토·보류·재검토가 남으면 미완료다. 원본 규칙 결과와 PoR은 변경하지 않는다.
명세 작성 당시의 1,000 record/100 group 한도·모달 UI에 이어, 현재 코드에는 평균 10,000건과 100,000건 이상에 같은 구조를 사용하는 순차 작업 화면과 페이지/서버 편집 API를 추가했다.
`WS_CLUSTER.state.task`는 참고 엔진의 가변 범위(`max(500, ceil(record_count/20))`)·입력/기준 해시·진행·오류·다음 작업을 보관하며 head의 분류 개정과 분리한다.
완성된 전체 배정만 불변 event/head로 원자적으로 저장한다. 진행/실패/오래된 작업은 새 그룹 판정의 선행 확정으로 사용하지 않는다.
기대 분류 개정이 다른 페이지는 409로 거부하며 편집 UUID와 명시적 ID로 전체 구성에서 변경한다. 과거 구성과 제거된 그룹은 읽기 전용 조회다.
API·상한·task 재개 계약은 [대용량 순차 검토](oa/WORKSPACE.md#대용량-report의-순차-검토)를 따른다. 새 테이블·권한·DDL은 추가하지 않았다.
이 `WS_CLUSTER.state.task` 분할 실행은 모델 호출이 없는 참고 엔진이다. 별도 OA 모델의 한도 내 첫 순환·현재 확정 사례 검색과 구분하며 실제 모델 대량 분할/그룹 통합·품질 검증을 완료한 것으로 표시하지 않는다.

**그룹별 분류 근거 V2**는 [요구사항 18.10절](requirement.md#group-clustering-rationale)을 구현한다.
물리 테이블·DDL은 추가하지 않는다. `groups`는 기존 `id`·`label`·`record_ids`를 유지하고
불변 `WS_CLUSTER_EVENT`에 `rationale_schema=2`, `rationales`(group ID → 근거)를 추가한다.

- `content`: `reason`(1–2,000자), `evidence_ids`(해당 그룹의 원본 ID 1–20개), `reference_groups`(최대 20개, 각 `case_id`, `group_id`, 적용 이유 `reason` 1–500자). 전체 사례 참조는 기존 최대 100개다.
- `proposal`: 최초 제안의 `source`와 불변 `event_id`. 내용이 그대로이면 해당 event/content를 참조하여 중복 저장을 피하고, 사람 정정 직전에 원안을 `proposal.content`로 보존한다. 선택 그룹 상세 및 다음 AI 입력은 원안을 명시적으로 펼쳐 제공한다.
- `source`: `AI`, `REFERENCE_ENGINE`, `HUMAN`. `status`: `DRAFT`, `CONFIRMED`, `STALE`; row 없는 구형 그룹은 계산 상태 `MISSING`이다.
- `binding`: `source_sha256`, `cluster_event_id`, `cluster_revision`, `group_id`, `label`, `membership_sha256`(정렬한 해당 그룹 record IDs의 SHA-256).
- `reviewed_by`/`reviewed_at`, 사람 작성 시 `edited_by`/`edited_at`, `change_reason`, `previous_event_id`로 검토/정정과 승계 이력을 보관한다.

다수 그룹(현재 1,000개 이상)의 저장은 `rationale_encoding=bucket-zlib-v1`로 JSONB 생성 메모리를 제한한다.
`rationales`에는 group ID → bucket 인덱스, `rationale_chunks`에는 최대 256개의 독립 zlib/base64 원문 JSON 묶음,
`rationale_bytes`에는 압축 전 전체 바이트 수를 보관한다. 압축 전 근거는 128MiB까지이며 초과/손상은 명시적으로 거부한다.
이는 같은 불변 event의 내부 저장 형식이다. 공통 store가 원래 객체로 복원하므로 API와 SDK의 위 V2 논리 계약은 바뀌지 않는다.
선택 그룹 이력은 필요한 bucket만 읽고, 초기 metadata는 chunk 본문을 포함하지 않는다. 새 물리 테이블·DDL·자료 생략은 없다.
사례/구형 전체 이력 조회의 바이트 예산에도 압축 해제 크기를 포함하며, 대용량 이력은 페이지 API를 사용한다.

AI 출력 V2는 기존 세 키에 `group_rationales`를 추가한다. 모든 그룹에 정확히 하나의 설명이 있어야 하며,
이 배열의 사례 ID 합집합이 `reference_ids`와 같아야 한다. 각 과거 group이 입력 case에 존재하는지 검사한다.
합성 모델의 현재 prompt는 `sanity-group-rationales-feedback-v3`다. V1/V2 receipt도 각각 원래 고정 prompt/hash로 검증한다. V3는 적용 가능한 과거 통합 의도와 실제 group ID/소속의 일관성을 추가 점검하며 출력 JSON V2는 유지한다. 구형 결과에 근거/사람 확인을 보충 생성하지 않는다.

그룹 상세/이력은 [`GET/POST /groups/{group_id}/rationale`](oa/WORKSPACE.md#v2-근거-저장확정-요청), metadata에는 네 상태의 `rationale_counts`를 제공한다.
목록은 상태와 160자 이내 요약(`reason_truncated`), 상세는 대표 로그 기본 3개와 필요 시 나머지를 제공한다. 이 참조 수는 분석한 전체 로그 수와 다르다.

MOVE의 출발/도착 그룹, RENAME 및 병합으로 구성/이름이 바뀐 그룹의 근거는 STALE, 새 그룹은 MISSING이다.
변경 없는 그룹은 바인딩을 새 event로 연결하고 이전 event를 기록해 확인을 승계한다. 삭제 전 근거는 불변 과거 event에 남는다.
근거만 바꿔도 새 분류 개정을 만들고 과거 그룹 판정은 현재 개정의 판정으로 사용하지 않는다.

CONFIRM은 현재 기대 개정 전체의 **명시적인 DRAFT 일괄 채택 + 분류 확정**이며 MISSING/STALE/무효 참조는 차단한다.
전역 reason은 선택 메모다. 개별 SAVE(reviewed=true)/ACCEPT만으로 전체 분류를 확정하지 않는다.
근거 미기록의 구형 확정도 과거 이력으로 조회할 수 있지만 보완·새 분류 확정 전에는 새 그룹 판정/제안을 쓸 수 없다.
`WS_GROUP_REVIEW_EVENT/PROPOSAL.clustering_rationale`는 유효한 분류 근거 snapshot이며 판정 `reason`과 별도다.
다음 합성 AI 입력에는 최종 근거·대표 ID·최초 원안·수정 이유와 불변 이력 참조를 제공한다.
회사 SDK/전송, 사내 원문, SSO 보호·내부 AI의 수정 허용 범위는 이 변경으로 확장되지 않는다.

독립 `/workspace`의 사용자 판정은 기존 승인/coverage 계산과 별도다.
`WS_REVIEW_EVENT`의 PASS/FAIL/DEFER는 원본 규칙 판정을 바꾸지 않으며 원본 FAIL에 사용자 PASS도 기록한다.
검사 원문 미수신/파싱 미완료에는 DEFER만 허용한다. 기준/사유/해시/버전/검토자와 정정 이력은 불변 event로 남긴다.
현재 head가 참조하는 다른 보고서의 확정 사례만 같은 scope·호환 버전으로 재사용한다.
`WS_REVIEW_PROPOSAL`은 참조 event와 전체 사례 목록의 해시를 보관하며 기준 정정·추가 시 오래된 제안으로 확정하지 못한다.
AI 제안은 자동 사람 확정이 아니며 제안과 최종 판정의 차이도 기록한다.
현재 `CRITERIA` 모드는 확정 조건 적용, `model_called=false`다. `CORPORATE` 모드는 아직 `AI_NOT_CONFIGURED`로 차단한다.
운영 OA 모델 실행·현재 확정 사례 검색/피드백·직접 근거 판정 V2는 위 별도 계약을 사용한다. 이 독립 합성/기존 VWP `ReviewAdvisor`의 사람 사례 참조 필수 제한과 구형 승인 API는 유지한다. 실제 회사 API/자료의 판단 품질은 별도 검증한다.
상세 저장/API/역할 경계는 [WORKSPACE.md](oa/WORKSPACE.md#사용자-판정과-사례-재사용)를 따른다.

Expected = reviewed + approved_na + unresolved (mutually exclusive unit counts).
Completion requires confirmed nonempty inventory, matching units, no global blockers,
no applicable execution/parse/evaluation blockers or unknowns, and current-bundle human
decisions for all applicable units. Exclusions require reason, approved_by and reference.
Missing/unsupported records stay in denominator. Report unit-level reasons and all items,
including PASS. Overall check FAIL takes precedence, else UNKNOWN for blocked/incomplete
analysis, else PASS; human review completion is a separate field.

This is a prototype: actual report adapters, authoritative inventory discovery,
full-access DBaaS integration, model APIs, ordering multiple bundles and production authentication
remain unimplemented. Jira integration has been retired, not deferred. Documentation and outputs must say so plainly.


## plan 3의 보존 원천 근거 — 2026-10-28

[정식 계약과 배포 순서](vwp/PRESERVED_SOURCES.md)를 따른다. 보존 선언이 있는 plan/collection manifest는 schema 3, source producer는 `postgresql-source-2.3`이다. 제출 envelope·`source-collection-v2`·SOURCE_* 종류는 기존 2를 유지한다. plan 2 필드/해시/이력은 유지하며 구버전 reader에 새 필드를 버려 전달하지 않는다. 별도 DB DDL은 없다.

`inventory.preserved_files`의 path/role/정확한 unit 또는 execution anchor를 검증하고 실제 전체 file hash/크기와 결합해 `PRESERVED` 메타데이터를 만든다. 누락은 `PRESERVED_FILE_MISSING`, 메타데이터/귀속 불일치는 `PRESERVED_SOURCE_MISMATCH` 등으로 거부한다. 선언 없는 파일은 UNEXPECTED_REPORTS로 남는다. 원본은 VWP RAW에 전체 byte로 보관하며 OA에는 전체 목록과 report별 `preserved_sources` 참조를 보낸다. scope/content/manifest digest와 OWNER 현재 권한·revision 검사는 유지한다.

귀속된 보존 메타데이터를 분석 계약에 포함하므로 같은 CSV/execution에서도 근거가 바뀌면 새 analysis/report가 생성된다. 기존 식별 공식과 이전 확정 기록을 바꾸지 않는다. OA source digest·AI 입력 SHA256에 연결하며 실제 분류/판정 요청에 메타데이터와 본문 미제공 안내를 전달한다. 파일 본문·추출 결과·새 파일 근거 인용은 이번 버전에 없다.


## plan 4 보존 본문과 다중 귀속 — 2026-10-28

[본문 계약](vwp/PRESERVED_TEXT.md)은 plan/manifest 4, producer postgresql-source-2.4를 사용한다. 기존 SOURCE_PART 안의 명시 kind=preserved-text-v1과 source_id/part_index/segments를 검증하며 가짜 unit/로그를 만들지 않는다. 같은 파일을 여러 대상에 연결해도 본문 part는 한 번 전송·보관한다. 전체 줄/byte·hash·완전성·마스킹을 검증하고 SOURCE_COMMIT에 로그/본문 전체 part 집합을 결합한다. 기존 SQL/제한 권한/SSO OWNER·불변 이력은 유지한다.

OA는 보고서 권한 안에서 마스킹 본문을 조회/다운로드하고 실제 AI 요청에 전체 본문을 제공한다. 별도 preserved_citations를 실제 pe 근거/대상과 대조해 모델 metadata·원문 위치·입력 SHA256·prompt 개정에 저장하고 공개 시 재검증한다. 승인 criterion/사람 case 조건을 대체하지 않는다. 미마스킹 원본은 VWP RAW에 남고 DB 분할과 다중 모델 호출을 혼동하지 않는다.
