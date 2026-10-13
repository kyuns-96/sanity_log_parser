# 프로토타입 데이터 계약 — PostgreSQL

현재 실행 계약은 같은 PostgreSQL DB의 4개 테이블이다. SQLite와 브리지 간 직접 통신/파일 복사는 사용하지 않는다.
Python 3.10 이상, 직접 모드는 psycopg, DBaaS 접속은 HTTPX/python-dotenv를 사용한다. DB 신청은 [db/README.md](db/README.md),
환경 변수·배포 파일·실행 명령은 [README.md](README.md)를 따른다. Jira 조회·게시·시뮬레이터는 폐지했다.
OA는 React·shadcn 대시보드와 인증된 로컬 FastAPI를 사용한다. 운영 AI는 NOT_CONFIGURED이며 운영 SSO/RBAC는 미구현이다.

접속 방식 정정: [공개용 접속 안내](dbaccess.example.md)의 DBaaS HTTPS API는 **임시 읽기 전용**이다.
실제 사내 접속 안내 `dbaccess.md`는 Git 업로드에서 제외한다.
쓰기·트랜잭션을 포함한 정식 전체 접근 방식은 사용자가 별도로 제공하며, 최종 연결 방식은 아직 확정하지 않는다.
`sanity_config.py`는 저장소 루트 `.env`/환경변수에서 설정을 읽고 `sanity_dbaas.py`는
Bearer·사내 CA 검증·trust_env=False·30초 timeout·redirect 금지·응답 크기 제한을 적용한다.
URL/토큰/CA 경로/서버 오류 본문은 출력하지 않는다. `/api/vi/me`는 `/api/v1/me`의 오타로 해석했다.
현재 HTTP API는 운영 저장·메시지 등록·DDL 경로가 아니며, 이 API의 쓰기 지원을 전제로 확장하지 않는다.
따라서 이 문서의 PostgreSQL 원자적 저장/잠금 동작을 HTTP에서 구현했다고 주장하지 않는다.
`SANITY_DB_ACCESS=dbaas_http`(기본)의 전체 브리지는 DBAAS_SQL_CONTRACT_REQUIRED로 중단하며 DSN fallback은 없다.
아래 저장 계약의 실행 구현은 `SANITY_DB_ACCESS=postgresql`을 명시한 직접 접속 모드다.
공통 DBaaS API로 접근하는 HTTPS와 금지된 브리지 상호 간 직접 통신은 구분한다.

## 모듈과 데이터 소유권

- `vwp-dev/`: VWP 내부 입력 snapshot 생성. DB 계정 불필요.
- `vwp-bridge/`, `sanity_evidence.py`: 전체 판정, VWP private 근거, 봉인·번들 등록.
- `oa-bridge/`, `sanity_oa_store.py`: 수신본, 검토 개정, 불변 보고서, 대시보드/API.
- `oa-bridge/dashboard_api.py`, `oa-bridge/dashboard/`: OA 로컬 인증·조회·수동 수신·검토·근거 요청 UI.
- `sanity_db.py`: 제한된 계정·유효 권한 검사, 짧은 트랜잭션, 방향별 큐.
- `sanity_exchange.py`: OA 근거 요청 → VWP 명시적 승인/거부 → OA 응답 수신.
- `sanity_common.py`: 입력/번들/근거 동일성·coverage 검증, 제한된 gzip·JSON, 결정적 보고서 렌더링.

원본 bytes와 JSONB를 혼동하지 않는다. 전송 해시는 `payload_gzip`, 보고서 해시는 `payload_bytes`,
개별 근거 해시는 `raw_bytes`에 대해 계산한다. JSONB 키 순서는 보존되지 않으므로 렌더링 시 정렬한다.
DB에서 표현할 수 없는 NUL 등 정규화 필드는 조용히 삭제하지 않고 DB 오류로 중단한다.
원본 snapshot과 디코딩 실패 표현의 실제 원본 바이트는 VWP 보존 정책에 따라 유지해야 한다.

공통 `write_bundle/read_bundle`은 오프라인 형식 회귀 테스트용이다.
앱 실행 경로는 `pack_json/unpack_json/unpack_bundle`과 DB 큐만 사용한다.
명령 오류는 `code=...`와 종료 코드 2다. 불완전 분석/FAIL 결과도 유효 산출물이므로 종료 코드 0일 수 있다.

## vwp-dev 입력과 출력

CLI: `python3 vwp-dev/main.py demo --out DIR [--scenario complete|missing|malformed|unsupported]`
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

An applicable empty report or zero-record results must be incomplete in this prototype.
Non-applicable units require exclusion `{reason,approved_by,reference}`; no report is needed.
Never turn failed/missing execution into non-applicable based on observed report absence.

## vwp-bridge payload

CLI: `python3 vwp-bridge/main.py analyze --input DIR/run.json [--out RECEIPT_DIR]`
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
자동 heartbeat/지속 polling daemon은 없으며 CLI 호출은 한 건 처리한다.

OA private `sanity_oa_data`의 record_type은 RECEIVED_BUNDLE, REVIEW_UNIT, REVIEW_EVENT, REPORT뿐이다.
원본 수신 payload와 context={interface:"dashboard-v1",ai:{status:"NOT_CONFIGURED"}},
전체 REVIEW_UNIT, 초기 REVIEW_EVENT(개정 0), REPORT를 저장한다. 공유 RECEIVED까지 같은 트랜잭션이다.
재수신은 기존 검토/맥락을 유지하며 새 번들은 승인 없이 시작한다. Jira 작업·전용 컬럼·인덱스는 없다.

## 사람 검토·불변 보고서

실제 CLI 목록은 [OA 안내](oa-bridge/README.md)를 따른다. `--state`, `--manifest`는 없다.
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

VWP `respond --request-id ... --approve-export|--reject`는 특정 요청만 점유한다.
유효 기간·봉인된 분석·단위·파일 연결·전체 선택 기록·원문 해시를 검사한다.
반출 승인을 명시하지 않으면 원문을 내보내지 않는다.
EVIDENCE_RESPONSE의 message_id는 `response-<UUID>`, correlation_id는 요청 message_id다.
project/run/bundle 일치는 DB FK로도 확인한다. 최종 결과는 FULFILLED/REJECTED/EXPIRED다.
FULFILLED에만 gzip을 저장하며 내부에는 schema_version, request_id(요청 message_id),
bundle_id, review_unit_id, records가 있다. 각 record에는 전체 raw와 raw_sha256이 포함된다.
최종 응답 등록과 요청 RECEIVED는 같은 트랜잭션이며 부분 성공은 없다.

OA 수신은 요청한 ID 집합과 정확히 일치하는지, source/unit 연결과 원문 해시를 검사한다.
검증된 bytes는 공유 응답 행에 남는다. `--out`은 선택적인 OA 내부 파일일 뿐 통신 경로가 아니다.
근거 수신으로 원래 분석 payload나 사람 검토 판정을 자동 변경하지 않는다.

## 대시보드 API와 인증

서버는 python oa-bridge/dashboard_api.py로 실행하며 127.0.0.1:8000에만 bind한다.
npm ci / npm run build 결과 dist를 같은 origin으로 제공한다. 개발 서버는
127.0.0.1:5173에서 /api를 8000으로 proxy한다. 외부 bind는 금지한다.
이 HTTP는 OA 브라우저→OA API이며 bridge 간 통신은 여전히 공유 DB만 사용한다.

private .env의 SANITY_DASHBOARD_TOKEN은 최소 32자이고 SANITY_DASHBOARD_REVIEWER는
서버가 고정하는 검토자다. 모든 업무 데이터 조회/변경은 Bearer 인증이 필요하다.
브라우저는 토큰을 메모리에만 보관한다. localStorage·URL·로그·VITE_*나 빌드 파일에 넣지 않는다.
health와 정적 UI는 공개 가능하지만 업무 정보를 제공하지 않는다.
현재 단일 사용자 로컬 prototype이며 SSO/RBAC·TLS proxy·운영 인증은 미구현이다.

- GET /api/session: 서버 검토자, access_mode, writable 설정. 기본 HTTP 모드는 읽기 전용 안내.
  이 응답만으로 DB 연결·권한이나 업무 조회 어댑터가 준비됐다고 보지 않는다.
- GET /api/bundles: offset/limit 페이지, 전체 수.
- POST /api/receive: 빈 JSON 객체로 다음 메시지 한 건 수동 처리.
- GET /api/bundles/{bundle_id}: unit_offset/unit_limit, 전체 coverage, 단위 페이지,
  최근 REVIEW_EVENT 50개와 전체 history_total.
- GET /api/bundles/{bundle_id}/units/{unit_id}/groups: 그룹 페이지와 전체 수.
- POST /api/bundles/{bundle_id}/reviews:
  {unit_id,conclusion:PASS|FAIL,reason,request_id:UUID,expected_revision:integer}.
  reviewer는 요청 본문을 받지 않고 서버 설정을 사용한다.
- GET /api/bundles/{bundle_id}/report: 현재 개정의 저장된 불변 Markdown 보고서 다운로드.
- POST /api/bundles/{bundle_id}/evidence:
  {unit_id,record_ids,reason,request_id:UUID,ttl_seconds}; VWP 반출 승인이 별도로 필요하다.
- GET /api/bundles/{bundle_id}/requests: 요청 상태 페이지.
- POST /api/evidence/{request_id}/receive: 응답 명시적 수신.
- GET /api/evidence/{request_id}: 검증·수신된 응답의 제한된 원문 미리보기와 잘림 표시.

목록 페이지 기본값은 엔드포인트별로 다르며 limit은 최대 100이다. coverage 분모는 화면 페이지와 독립적이다.
POST는 JSON 객체를 사용하고 추가 필드·잘못된 ID/UUID·범위 밖 수치는 거부한다.
오류는 짧은 code로 반환하며 비밀·원문 예외를 응답에 노출하지 않는다.
화면의 합성 demo는 사용자가 명시적으로 진입하는 메모리 상태이며 새로고침 시 초기화한다.
인증/DB/API 오류에서 demo로 자동 fallback하지 않고 demo 결과를 실제 DB 저장으로 표현하지 않는다.

## 운영 AI 경계

AI는 NOT_CONFIGURED다. 가짜 AI 의견이나 모델 호출 성공을 생성하지 않는다.
개발 협업용 내부 AI와 운영 API는 별개이며 운영 모델 연동은 후속 범위다.
Jira 맥락 조회·후보 fixture·게시 API는 현재 프로토콜에서 제거했다.

## Coverage and finality

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
