# PostgreSQL 4개 테이블 신규 생성·설정 안내

현재 선택한 구성은 **운영 테이블 4개 모두 신규 생성**이다. 기존 테스트 테이블을 변경하거나 재사용하지 않는다.
모든 테이블은 같은 PostgreSQL DB에 있어야 한다. PostgreSQL 16 이상, UTF-8을 전제로 한다.
별도 스키마·시퀀스·확장 모듈·SQLite·DB 밖의 브리지 간 통신은 요구하지 않는다.
OA 브라우저의 로컬 API 접속은 브리지 간 통신과 별개다.
이 문서는 DDL/권한 계약이다. Python 실행 코드도 이 4개 테이블을 사용한다.
설치·DSN·실행·통합 테스트는 [루트 README](../README.md)를 따른다.
접속 안내 [공개용 설정 문서](../dbaccess.example.md)의 DBaaS HTTP API는 **임시 읽기 전용**이다.
4개 테이블 설계는 유지하며, 쓰기·트랜잭션을 포함한 정식 전체 접근 방식은 사용자가 별도로 제공한다.
현재 HTTP 인증·조회 클라이언트와 직접 PostgreSQL 저장 어댑터는 별개다.
아래 생성·설정 SQL을 임시 HTTP `/query`로 실행하지 않는다. 정식 방식 제공 후 승인된 DDL/권한 적용 경로를 확인한다.
현재 신청·변경 파일은 이 `db/` 폴더만 사용한다. 이전 신청안과 전용 테스트는 제거했다.

## 1. 신청 대상과 적용 순서

### 파일별 용도

| 파일 | 내용 | 사용할 곳 |
|---|---|---|
| `form/*.columns.sql` | 테이블별 컬럼·자료형·기본값·PK/UNIQUE/CHECK 정의. 바깥 `CREATE TABLE`은 없음 | GUI의 **컬럼 정의(SQL)** 입력란 |
| [002_indexes.sql](002_indexes.sql) | 추가 인덱스·부분 UNIQUE·테이블 간 FK·저장 설정 | 테이블 생성 후 SQL 실행 |
| [003_grants.sql](003_grants.sql) | VWP/OA 역할별 테이블·컬럼 권한 | 역할 준비 후 SQL 실행 |

**GUI에서 `form`으로 4개 생성 → `002` 실행 → 역할 준비 → `003` 실행**이 전체 설치 순서다.
직접 생성용·예전 설치 보정용 SQL은 제거했다. 남은 실행용 SQL은 위 두 개뿐이며 번호는 그대로 유지한다.
`tests/`는 개발자용 로컬 검증 도구이며 DB 신청이나 운영 설치 시 실행할 파일이 아니다.

### GUI 신청 절차

| 테이블 이름 | 컬럼 정의(SQL)에 붙여 넣을 파일 |
|---|---|
| `sanity_vwp_to_oa` | [컬럼 정의](form/sanity_vwp_to_oa.columns.sql) |
| `sanity_vwp_data` | [컬럼 정의](form/sanity_vwp_data.columns.sql) |
| `sanity_oa_data` | [컬럼 정의](form/sanity_oa_data.columns.sql) |
| `sanity_oa_to_vwp` | [컬럼 정의](form/sanity_oa_to_vwp.columns.sql) |

1. 실제 DB/스키마, 테이블 owner, VWP/OA의 서로 다른 로그인 계정을 확인한다.
2. 위 테이블 4개를 각각 새로 신청한다. 각 `.columns.sql`의 **전체 내용만** 컬럼 입력란에 붙여 넣는다.
   `CREATE TABLE`, 바깥 괄호, 코드펜스는 추가하지 않는다. 기존 테스트 테이블은 변경·삭제하지 않는다.
3. 네 테이블 생성 후 owner/DBA가 `002_indexes.sql` 전체를 한 번 실행한다.
4. DBA가 `sanity_vwp_app`, `sanity_oa_app` 역할을 준비하고 `003_grants.sql`을 실행한다.
   예: `CREATE ROLE sanity_vwp_app NOLOGIN;` / `CREATE ROLE sanity_oa_app NOLOGIN;`.
   기존 역할이 있다면 새로 만들지 말고 구성을 검토한다. 실제 로그인에 해당 역할 하나만 연결한다.
5. 양쪽 환경의 실제 앱 계정으로 PING 전달을 확인하고 승인된 접속 방식으로 합성 분석/수신을 확인한다.

SQL의 대상 스키마는 `public`이다. 다른 스키마에 신청했다면 `002`/`003`의 모든 `public.` 참조와
`GRANT USAGE ON SCHEMA public`, 앱 설정을 **같은 실제 스키마로** 맞춘다.
기존 DB가 이미 준비되어 있다면 재생성하거나 `002`를 다시 실행하지 않는다.
앱은 필수 UNIQUE 인덱스를 이름이 아닌 대상 테이블·키·조건으로 확인한다.

## 2. 데이터 계약

| 테이블 | 구분 | 행 하나의 의미 |
|---|---|---|
| VWP 전용 | `RUN`, `UNIT`, `FILE`, `GROUP`, `EVIDENCE` | 실행, 예상 단위, 파일, 문제 군집, PASS 포함 개별 근거 |
| OA 전용 | `RECEIVED_BUNDLE`, `REVIEW_UNIT`, `REVIEW_EVENT`, `REPORT` | 검증된 수신본, 전체 검토 단위, 검토 개정, 개정별 불변 보고서 |
| VWP → OA | `PING`, `ANALYSIS_BUNDLE`, `EVIDENCE_RESPONSE` | 연결 확인, 반출 승인된 gzip 번들, 근거 요청에 대한 최종 응답 |
| OA → VWP | `PING`, `EVIDENCE_REQUEST` | 연결 확인, 허용된 논리 ID/범위에 대한 추가 근거 요청 |

- `record_id`, `request_id`, lease token UUID는 앱이 생성한다. 같은 작업을 재시도할 때는 기존 ID를 재사용한다.
  lease token만 매번 새로 발급한다. `message_id`는 길이 128 이하의 영속 식별자다.
- VWP `analysis_id`는 해당 RUN의 `record_id`다. UNIT/FILE의 parent는 RUN, GROUP/EVIDENCE의 parent는 UNIT이다.
  `entity_key`는 실행·종류 내에서 유일하다. 파일/단위/그룹 키는 기존 논리 ID, 근거 키는 원본 내 고유 record ID를 쓴다.
  같은 분석의 parent 존재는 FK로 확인하지만 parent 종류·unit 일치·파일/그룹 연결은 앱 검증이다.
- VWP `payload`에는 불변 입력/규칙/식별 필드/위치/개별 판정을 넣고, `state`에는 가변 checkpoint·집계를 넣는다.
  RUN/UNIT/GROUP만 state를 바꾼다. FILE/EVIDENCE의 state/version/갱신 시각은 고정한다.
  현재 구현은 모든 원문을 raw_bytes에 저장한다. DB 원문 보관이 불허되면 배포를 중단하고
  승인된 VWP 파일 위치/해시 참조 어댑터를 추가해야 한다. DDL의 local_evidence_ref만으로 지원되는 것은 아니다.
- OA 수신본의 state에 `current_review_revision`을 저장한다. 초기 개정 0의 REVIEW_EVENT와 함께 만든다.
  REVIEW_EVENT는 한 개정의 결정 변경 목록·검토자·사유를 payload에 담는다. 개별 단위는 REVIEW_UNIT 행으로 유지한다.
  REVIEW_EVENT/REVIEW_UNIT/REPORT는 추가 전용이며, 보고서는 `payload_bytes`에 원본 UTF-8 바이트와 SHA-256을 보존한다.
- Jira 게시와 시뮬레이터는 폐지했다. 아직 신규 신청 전이므로 신청 컬럼·인덱스에서 Jira 작업을 제거했다.
  OA 테이블은 위 4개 record_type만 허용하며 검토·보고서는 OA 대시보드에서 확인한다.
- 공유 payload JSON은 공개 가능한 전송 명세/진단만 포함한다. gzip 바이트는 `payload_gzip`에 보존한다.
  ANALYSIS_BUNDLE의 payload에는 기존 schema_version/producer_version/inventory_sha256 등 명세를 넣는다.
  압축 전후 한도는 각각 64 MiB다. JSONB 재직렬화로 원본 해시를 대신하지 않는다.
- 현재 EVIDENCE_REQUEST payload에는 unit ID, 최대 100개 record ID, 유효 기간·사유를 넣는다.
  group/range 선택은 후속 확장이다. 임의 경로·명령은 금지한다.
  EVIDENCE_RESPONSE의 correlation_id는 요청 message_id이며 project/run/bundle 일치는 FK로 확인한다.
  응답 result는 FULFILLED/REJECTED/EXPIRED 중 하나이며 FULFILLED에만 gzip이 있다.
- `updated_at`은 자동 갱신되지 않는다. 가변 상태 변경 시 앱이 명시적으로 갱신한다.
  JSONB에 정확한 소수는 기존 계약처럼 문자열로 보존한다.

## 3. 권한과 운영 규칙

- 두 앱은 같은 DB를 쓰지만 같은 owner 계정을 공유하지 않는다. VWP 전용과 OA 전용 테이블의 접근을 교차 허용하지 않는다.
  다른 역할/PUBLIC/owner 상속을 통한 권한 우회가 없어야 한다. 앱에 schema CREATE, DDL, DELETE/TRUNCATE를 허용하지 않는다.
- 송신자는 공유 메시지의 ID·내용 컬럼만 INSERT한다. 상태/점유/완료 시각은 INSERT할 수 없다.
  수신자는 공유 메시지의 처리 상태 컬럼만 UPDATE한다. 원문 변경, 방향 위조, 상대 방향 메시지 INSERT는 금지한다.
- `003_grants.sql`은 네 테이블에 남은 예상 밖 직접 권한을 발견하면 중단한다. 이전 테스트 로그인에 부여한
  광범위 권한을 DBA가 검토·회수하고 역할 방식으로 연결해야 한다. 앱 로그인이 owner이면 별도 owner로 분리해야 한다.
  관리자가 읽을 수 있는 공유 DB라는 사실과 데이터 반출/보관 승인 여부는 별도다.
- polling은 payload를 제외한 ID/상태만 조회한다. READY/RETRY 및 next_attempt_at으로 대상을 찾고
  `FOR UPDATE SKIP LOCKED`로 짧게 잠근 뒤 새 lease token/만료 시각을 기록한다. 처리 중에는 트랜잭션을 잡고 있지 않는다.
  완료 UPDATE는 `status='PROCESSING' AND lease_token=:token AND lease_expires_at>clock_timestamp()` 조건을 사용한다.
  영향 행이 0개면 수신본/검토 단위 등을 포함한 완료 트랜잭션 전체를 rollback한다.
- 분석 봉인과 ANALYSIS_BUNDLE 등록은 같은 트랜잭션으로 확정한다. 수신본/REVIEW_UNIT/개정 0과 RECEIVED도 같은 트랜잭션이다.
  근거 최종 응답과 역방향 요청의 RECEIVED 갱신도 함께 확정한다. 일시 장애는 최종 거부 응답으로 저장하지 않는다.
- 사람 검토는 RECEIVED_BUNDLE 행을 잠그고 예상 목록·blocker·해당 단위·현 개정을 검증한 뒤 새 REVIEW_EVENT와
  current_review_revision/version을 같은 트랜잭션에 기록한다. A→B→A도 서로 다른 세 개정이다.
  같은 request_id의 재시도는 내용까지 비교한다. `ON CONFLICT DO NOTHING`만으로 동일 작업이라고 판단하지 않는다.
  대시보드 API는 expected_revision을 요구하며 최신 개정과 다르면 충돌을 반환한다.
  REVIEW_EVENT·현재 개정·REPORT 저장은 한 트랜잭션이다. 과거 보고서·이벤트는 덮어쓰지 않는다.
- OA 앱의 UPDATE 권한은 state/status/version/updated_at에 한정한다. 게시 작업용 추가 컬럼 권한은 없다.
- 공유 수신 완료, 전체 분석 완료, 사람 검토 완료, sanity PASS는 각각 별도 판단이다.

## 4. DB와 애플리케이션의 검증 경계

DB는 타입/기본값, 고유 키, 원문 바이트 해시/크기, 같은 분석 parent, 요청/응답 scope,
상태별 lease/완료 시각, 불변 컬럼 권한을 검사한다. `002_indexes.sql`의 부분 UNIQUE도 필수 계약이다.
**컬럼 SQL만 신청한 상태는 운영 준비 완료가 아니다.** 인덱스/권한 SQL의 별도 실행 권한 또는 DBA 지원이 필요하다.

다음 의미 검증은 DB 제약만으로 보장하지 않으므로 앱 검증과 통합 테스트가 담당한다.
Jira 호출·게시·DB 시뮬레이터는 현재 실행 범위에 없으며 운영 AI도 연결하지 않았다.

- RUN/UNIT/FILE/GROUP/EVIDENCE의 정확한 parent 종류와 단위 연결, 파일 위치·해시·record 순서의 유일성.
- 예상 전체 item과 실제 입력·PASS/FAIL/UNKNOWN 건수의 대조, 누락/미지원/제외 근거, 미확정 군집의 단독 보존.
- JSON 구조/버전/중복 키, 실제 gzip 유효성 및 제한된 압축 해제, 원래 bundle 내용 ID.
- 봉인 이후 갱신 금지, 개정 번호 할당/현재 개정의 존재, 보고서와 올바른 검토 개정 연결.
- 허용 상태 전이, token/만료/version 조건을 포함한 UPDATE, expected_revision 충돌 처리.

검증을 통과하지 않은 데이터는 봉인/전송/검토 완료로 승격하지 않는다. 신청 수는 줄였지만 이 책임은 줄이지 않는다.

## 5. 연결 테스트와 회귀 검사

권한 설정 후 VWP 앱 계정에서 작은 합성 PING을 등록한다. 재실행 시 새로운 message_id를 사용한다.

```sql
INSERT INTO public.sanity_vwp_to_oa(message_id, project, run_id, payload)
VALUES ('ping-vwp-001', 'connection_test', 'run-001', '{"message":"연결 확인"}');
```

OA 앱 계정에서 조회한 후 해당 테스트 행만 수신 확인한다. 아래 직접 완료 갱신은 단일 PING 수동 검사에 한정한다.
운영 작업은 위의 lease 규약을 따른다.

```sql
SELECT message_id, direction, message_type, status, payload
FROM public.sanity_vwp_to_oa WHERE message_id = 'ping-vwp-001';
UPDATE public.sanity_vwp_to_oa
SET status = 'RECEIVED', completed_at = CURRENT_TIMESTAMP, updated_at = CURRENT_TIMESTAMP
WHERE message_id = 'ping-vwp-001' AND message_type = 'PING' AND status = 'READY';
```

역방향은 OA가 `sanity_oa_to_vwp`에 다른 PING ID를 INSERT하고 VWP가 같은 방법으로 확인한다.
실제 로그·개인정보·비밀은 접속 검사에 넣지 않는다. 앱에 DELETE 권한은 부여하지 않는다.

로컬 회귀 검사는 운영 DB가 아닌 별도 빈 DB에서 실행한다. 두 앱 역할은 미리 준비한다.

```bash
bash db/tests/create_fixture.sh -h HOST -U OWNER -d sanity_test_example
python3 db/tests/check_concurrency.py -h HOST -U OWNER -d sanity_test_example
```

fixture는 `form`을 감싸 GUI 생성을 모사한 뒤 `002` 인덱스/FK → `003` 권한 적용을 검사한다.
smoke 테스트 행은 rollback하며, 테스트용 빈 테이블은 보존한다.
`tests/test_schema.py`는 신청용 컬럼 정의 4개와 생성 후 설정 SQL 두 개의 구성을 확인한다.
Python DB 회귀 검사도 동일한 `form`으로 생성한 테이블을 사용한다.
concurrency 검사는 실제 두 세션에서 잠긴 행을 건너뛰고 서로 다른 작업을 점유하는지 검사한 뒤,
이 검사에서 생성한 UUID 식별자의 합성 PING 두 개만 owner로 정리한다.
이 DDL 검사와 별도로 Python 어댑터의 실제 PostgreSQL 통합 검사는 루트 `run_tests.py --require-db`로 실행한다.
실제 사내 GUI SQL 필터, 정식 전체 접근·운영 연결/TLS/백업, 실데이터 성능은 로컬 검증에 포함되지 않는다.
