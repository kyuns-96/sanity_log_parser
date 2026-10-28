# PostgreSQL 4개 테이블 신청 원본·변경 안내

**원본 DB 보관:** 009 이후 [010_raw_sources.sql](010_raw_sources.sql)이 기존 VWP private 테이블에 엄격한 원본 명세/파일/1MiB byte 조각 규약을 추가한다. 새 테이블/권한은 없다. [실행·이관 안내](../vwp/DB_ORIGINALS.md)를 따른다. 운영 적용은 DBA가 관리하며 앱이 DDL을 실행하지 않는다.

**010 적용 = 기존 `sanity_vwp_data`의 v4 CHECK 6개와 인덱스 3개로 확인하며 새 테이블을 신청하지 않는다.** 모드 선택, `public` 이외 스키마용 SQL 사본 생성, 오류 코드별 읽기 전용 진단과 실제 010 확인 SQL은 [DB 설치 점검](INSTALLATION_CHECKS.md)에 있다. 공통 접속 `check` 성공만으로 010 적용을 판정하지 않는다.

현재 사용자 제출은 [토큰 없는 HTTPS 접수](../vwp/HTTPS_SUBMISSION.md)와 [DB 인계 7절](CONNECTION_HANDOFF.md#7-사용자-제출의-저장-경로)을 따른다. VWP CLI→bridge HTTPS API→VWP private DB 원본→파싱/source 큐→OA staging/OWNER 범위 확정→역방향 응답이며 네 테이블을 유지한다. [008](008_submissions.sql) 이후 009/010의 해당 계약이 필요하고, 기존 [폴더 제출](../vwp/SUBMISSION.md)은 호환 경로다. 실제 회사 인프라·운영 적용 상태는 현장에서 확인하며 증분 SQL은 사용자/DBA 검토 절차로 적용한다.

**기존 `db/form/`으로 테이블 신청을 완료한 상태**다. 신청 원본 4개와 `002`·`003`은 그대로 보존한다.
이후 기능 추가는 별도 증분 변경 SQL로 적용한다. 테이블을 재신청·재생성하거나 기존 데이터를 삭제하지 않는다.
모든 테이블은 같은 PostgreSQL DB에 있어야 한다. PostgreSQL 12.1 이상, UTF-8을 전제로 한다. 실검증 버전은 12.1과 16.15다.
별도 스키마·시퀀스·확장 모듈·SQLite·DB 밖의 브리지 간 통신은 요구하지 않는다.
OA 브라우저의 로컬 API 접속은 브리지 간 통신과 별개다.
이 문서는 DDL/권한 계약이다. Python 실행 코드도 이 4개 테이블을 사용한다.
설치·환경변수 접속 설정·실행·통합 테스트는 [루트 README](../README.md)를 따른다.
세 개발 AI의 DB 역할·기존 설정 키·서버별 읽기 전용 확인·비공개 접속 기록 양식은 [연결 인계](CONNECTION_HANDOFF.md)를 따른다. 각 서버는 [소스 구조·실행 안내](../DEPLOYMENT.md)에 따라 자기 모듈만 실행한다. 이 폴더의 SQL/fixture는 앱 시작과 함께 실행하지 않는다.

기본은 별도 제한 로그인·`003`의 역할/컬럼 권한 계약이다. 별도 계정을 확보할 수 없는 환경에는 **명시적 `SANITY_DB_ALLOW_SINGLE_ACCOUNT=true`**의 [단일 계정 계약](CONNECTION_HANDOFF.md#31-단일-계정-모드)을 제공한다. 이 모드는 네 테이블 전체 컬럼의 유효 SELECT/INSERT/UPDATE와 선택적 DELETE를 허용하며 DB의 private/큐 컬럼 격리를 제공하지 않는다. 관리자·소유·앱/public 스키마 CREATE·TRUNCATE/REFERENCES/TRIGGER 금지와 테이블/필수 UNIQUE 검사는 유지한다. 이 경우 `003` 역할 생성을 요구하지 않고 원본 SQL/포털 계획을 수정하거나 완료 DDL을 재실행하지 않는다. 아래 역할/권한 설치 설명은 기본 모드 기준이며 구조 증분 `002`, `004`~`010`의 기능별 필요 조건은 두 모드에 동일하다.
접속 안내 [공개용 설정 문서](../dbaccess.example.md)의 DBaaS HTTP API는 **임시 읽기 전용**이다.
사용자가 psycopg2 직접 PostgreSQL 접속 가능함을 확인했으며 이 방식을 기본으로 사용한다.
4개 테이블 설계·신청 원본·기존 SQL은 변경하지 않는다. HTTP 인증·조회 클라이언트와 PostgreSQL 저장 어댑터는 별개다.
아래 생성·설정 SQL을 임시 HTTP `/query`로 실행하지 않는다. 실제 DBA가 승인한 DDL/권한 적용 경로를 확인한다.
현재 신청·변경 파일은 이 `db/` 폴더만 사용한다. 이전 신청안과 전용 테스트는 제거했다.

## 1. 신청 대상과 적용 순서

### 파일별 용도

| 파일 | 내용 | 사용할 곳 |
|---|---|---|
| `form/*.columns.sql` | 테이블별 컬럼·자료형·기본값·PK/UNIQUE/CHECK 정의. 바깥 `CREATE TABLE`은 없음 | GUI의 **컬럼 정의(SQL)** 입력란 |
| [002_indexes.sql](002_indexes.sql) | 추가 인덱스·부분 UNIQUE·테이블 간 FK·저장 설정 | 테이블 생성 후 SQL 실행 |
| [003_grants.sql](003_grants.sql) | VWP/OA 역할별 테이블·컬럼 권한 | 역할 준비 후 SQL 실행 |
| [004_clustering.sql](004_clustering.sql) | 분류 작업·개정용 컬럼 2개, CHECK 확장, 인덱스 추가 | 기존 테이블의 `002`·`003` 준비 후 owner/DBA가 한 번 실행 |
| [005_workspace.sql](005_workspace.sql) | OA 관리 레코드·PoR·세션, scope/FK/인덱스 확장 | `004` 적용 후 workspace를 도입할 때 owner/DBA가 한 번 실행 |
| [006_workspace_reviews.sql](006_workspace_reviews.sql) | OA 사용자 판정 head·불변 사례/제안, 원본/버전/사례 참조 FK | `005` 적용 후 사용자 판정 기능 도입 시 owner/DBA가 한 번 실행 |
| [007_workspace_pipeline.sql](007_workspace_pipeline.sql) | OA 분류 개정·그룹 판정/제안, 확정 분류 event 참조 FK | `006` 적용 후 두 단계 그룹 검토를 도입할 때 owner/DBA가 한 번 실행 |
| [008_submissions.sql](008_submissions.sql) | V2 SUBMISSION/SOURCE_RUN/OUTBOX·OA staging, SOURCE 큐 종류·중복/실행 순서·범위 제약 | `007` 적용 후 새 제출 경로 도입 시 owner/DBA가 검토해 한 번 실행. 앱 자동 적용 없음 |
| [009_sso_identity.sql](009_sso_identity.sql) | OA `SSO_IDENTITY` kind/scope·엄격한 계정 JSON·기존 UNIQUE 재사용 | `008` 이후 검토·적용. 포털은 생성된 55번 단일 ALTER 사용. 실제 SSO/JIT 앱 구현·업무 권한 연결은 별도 |
| [010_raw_sources.sql](010_raw_sources.sql) | 기존 VWP private의 `RAW_SUBMISSION/RAW_FILE/RAW_CHUNK`, v4 CHECK 6개·인덱스 3개 | `009` 이후 원본 DB 보관 도입 시 DBA 검토·적용. 새 테이블/역할/GRANT 없음. 빈 초기 포털은 raw 계획의 56~59 단계 |
| [011_payload_storage.sql](011_payload_storage.sql) | VWP private `payload_bytes`, 무손실 근거·OUTBOX 압축과 raw 압축 허용 | 010 이후 선택형. 호환 코드 선배포·DBA 적용. 빈 포털은 `--payload-storage`의 60번. [기존 데이터 변환·재작성·평문 복구](PAYLOAD_STORAGE.md) |
| [012_source_evidence_pages.sql](012_source_evidence_pages.sql) | 새 V2 근거를 최대 256건씩 `page_bytes`에 무손실 보관 | 011 이후 선택형. [근거 묶음·호환 reader·기존 데이터 경계](EVIDENCE_PAGES.md). 빈 포털은 `--evidence-pages`의 61/62번 |
| [013_shared_source_packets.sql](013_shared_source_packets.sql) | SOURCE_PART 본문 한 개와 VWP/OA private FK 참조·발행 gate | 012 이후 선택형. [네 테이블 합 20MB 목표](TOTAL_STORAGE.md). 양쪽 호환 코드 선배포. 빈 포털은 `--shared-source-packets`의 63~66번 |

기본 모드의 전체 준비 순서는 **신청한 `form`으로 생성 → `002` → 역할 준비 → `003` → `004`**다. 단일 모드는 역할 준비/`003` 대신 위 단일 계정 계약을 확인한다.
이미 완료한 단계는 반복하지 않는다. 생성·`002`·`003`까지 완료했다면 **`004`만 실행**한다.
workspace에는 이어서 `005 → 006 → 007`을 적용한다. 각 증분은 직전의 검증된 kind/scope 제약을 확인하고 기존 행·테이블·권한을 보존한다.
현재 HTTPS 원본 DB 보관까지는 이어서 `008 → 009 → 010`이 필요하다. 이미 009까지 준비됐다면 010만 검토하며 앞 단계를 반복하지 않는다. 적용 증거는 [010 확인 기준](INSTALLATION_CHECKS.md#4-010-적용의-확인-기준)을 따른다.
용량 최적화는 선택형 011을 추가한다. 기존 데이터 변환과 [owner 재작성](maintenance/repack_payload_storage.sql)은 초기 설치와 분리된 유지보수이며 앱이 자동 실행하지 않는다. 원본 SQL 002~010과 기존 55/59단계 계획은 보존한다.

새 V2 분석에는 선택형 012의 근거 묶음 저장도 제공한다. 원문·개별 로그 ID·전체 fields·전송 내용은 유지하고 물리 행 수를 줄인다. 012는 기존 EVIDENCE 행을 자동 변환하지 않으며, 적용 이후에는 012 호환 reader를 유지한다. SQL 011과 60단계 계획도 보존한다.

전체 용량 목표는 VWP/OA private·양방향 큐 네 테이블의 물리 합이다. 선택형 013은 기존 공유 큐의 전체 source packet을 두 private 참조가 재사용한다. ACK 이후에도 본문을 유지하고 FK로 참조된 큐 행 삭제를 막는다. 012와 62단계 계획도 보존하며, 기존 데이터 자동 변환/삭제·이전 reader로의 복귀는 제공하지 않는다.
OA에 `WS_REVIEW`, `WS_REVIEW_EVENT`, `WS_REVIEW_PROPOSAL`을 추가하며, 사례/제안은 state/version/갱신 시각을 고정한다.
`007`은 `WS_CLUSTER`, `WS_CLUSTER_EVENT`, `WS_GROUP_REVIEW`, `WS_GROUP_REVIEW_EVENT`, `WS_GROUP_REVIEW_PROPOSAL`을 추가한다.
새 행은 원본 보고서/버전과 분류 event를 FK로 참조한다. 분류/판정 event·제안은 추가 전용이며 head만 갱신한다.
기존 payload UPDATE 권한은 없으며 신규 head의 state/version만 갱신한다. 새 테이블/역할/광범위 GRANT는 없다.
owner/DBA가 점검 시간에 파일 전체를 한 트랜잭션으로 실행한다. 잠금 시간 초과나 사전 제약 불일치 시 rollback한다.
앱은 자동 migration을 실행하지 않으며 성공한 SQL의 재실행은 실패한다. 합성 로컬 fixture 전용 upgrade는 [WORKSPACE](../oa/WORKSPACE.md)를 따른다.
단일 문장 DDL·FK를 지원하는 회사 포털에는 [별도 초기 설치 실행 안내](portal/README.md)를 사용한다. 네 테이블 0행·쓰기 중지 조건에서 원본 `002/004/005/006/007/008/009`와 동등한 실행본을 오프라인 생성·검증한다. 기존 54단계는 보존하며 008 완료 snapshot에서는 009 한 문장만 생성한다. `003` 역할·권한은 별도 DBA 경로다. 기존 SQL을 분해 편집하거나 트랜잭션/DO만 삭제해 실행하지 않는다. 포털 경로는 문장별 커밋이며 전체 파일 원자성은 제공하지 않는다. 실제 포털 적용은 사용자/승인 운영 담당자 책임이다. 009는 [계정 저장 계약](../oa/SSO_IDENTITY.md)이며 JIT 앱 구현을 포함하지 않는다.
`tests/`는 개발자용 로컬 검증 도구이며 DB 신청이나 운영 설치 시 실행할 파일이 아니다.
사내 DB 없이 새 합성 DB를 만드는 명령은 `.venv/bin/python db/tests/run_mock.py`다.
기본은 `quick` 핵심 회귀와 100건/10그룹 검사다. 전수 회귀·포털 전체 단계·10만 건 검증은 `--profile full`로 별도 실행한다. 두 프로필 모두 실제 DB의 원본 DDL/SQL smoke와 제한 계정 권한 검사를 수행한다. 프로필 선택은 테스트 데이터 범위이며 앱의 처리 한도나 무결성 검사를 변경하지 않는다.
PostgreSQL 12.1+ 서버 바이너리가 필요하며, 별도 로컬 소켓 DB 생성·권한/회귀 테스트·종료를 수행한다.
기존 DB와 `.env`는 사용하지 않는다. 자세한 전제와 결과 파일은 [검증 안내](../README.md#7-합성-demo와-검증)를 따른다.

### 최초 신청 원본과 생성 이후 절차

| 테이블 이름 | 컬럼 정의(SQL)에 붙여 넣을 파일 |
|---|---|
| `sanity_vwp_to_oa` | [컬럼 정의](form/sanity_vwp_to_oa.columns.sql) |
| `sanity_vwp_data` | [컬럼 정의](form/sanity_vwp_data.columns.sql) |
| `sanity_oa_data` | [컬럼 정의](form/sanity_oa_data.columns.sql) |
| `sanity_oa_to_vwp` | [컬럼 정의](form/sanity_oa_to_vwp.columns.sql) |

1. 실제 DB/스키마, 테이블 owner와 권한 모드를 확인한다. 기본은 VWP/OA의 서로 다른 로그인, 단일 모드는 공유 로그인이다.
2. 위 표는 이미 신청한 원본의 위치다. 현재 사용자는 재신청하지 않는다.
   별도 새 환경을 만들 때만 각 `.columns.sql` 전체를 GUI에 넣는다. 바깥 `CREATE TABLE`은 추가하지 않는다.
3. 네 테이블 생성 후 owner/DBA가 `002_indexes.sql` 전체를 한 번 실행한다.
4. DBA가 `sanity_vwp_app`, `sanity_oa_app` 역할을 준비하고 `003_grants.sql`을 실행한다.
   예: `CREATE ROLE sanity_vwp_app NOLOGIN;` / `CREATE ROLE sanity_oa_app NOLOGIN;`.
   기존 역할이 있다면 새로 만들지 말고 구성을 검토한다. 실제 로그인에 해당 역할 하나만 연결한다.
5. 아래 변경 안내에 따라 owner/DBA가 `004_clustering.sql`을 한 번 적용한다.
6. 먼저 양쪽 환경의 실제 앱 계정으로 [읽기 전용 설치 점검](INSTALLATION_CHECKS.md)을 수행한다. PING·합성 분석/수신은 별도 승인된 송수신 시험에서 확인한다.

SQL의 대상 스키마는 `public`이다. 다른 스키마에 신청했다면 실행할 `002`~`010`의 모든 `public.` 참조와
`003`의 `GRANT USAGE ON SCHEMA public`, 앱 설정을 **같은 실제 스키마로** 맞춘다. [공식 사본 생성 절차](INSTALLATION_CHECKS.md#3-public-이외-스키마에-원본-sql-대응)로 원본 hash를 보존하고, 데이터가 없는 포털 경로는 기존 `--schema` 도구를 사용한다.
기존 DB가 이미 준비되어 있다면 재생성하거나 `002`를 다시 실행하지 않는다.
앱은 필수 UNIQUE 인덱스를 이름이 아닌 대상 테이블·키·조건으로 확인한다.

### 이미 생성한 테이블에 `004` 적용

- 대상은 위 4개 테이블뿐이다. OA 전용 테이블에 `entity_key VARCHAR(256)`과
  `classification_revision BIGINT`를 추가하고, 새로운 작업·결과·개정을 담을 CHECK와 인덱스를 확장한다.
  기존 행에서 새 컬럼은 NULL이며, 기존 `review_revision`·보고서·근거·메시지 내용은 변경하지 않는다.
- 먼저 백업/복구 절차를 확인하고 양쪽 bridge와 대시보드의 DB 작업을 중지한다.
  스크립트가 네 테이블에 `ACCESS EXCLUSIVE` 잠금을 잡고 기존 행의 CHECK와 인덱스를 검증하므로
  데이터량에 따라 조회·쓰기 중단 시간이 발생한다. 잠금을 5초 안에 얻지 못하면 중단한다.
- 승인된 SQL 도구에서 파일 전체를 **한 트랜잭션으로**, owner/DBA 권한으로 실행한다.
  GUI의 컬럼 정의 칸에 넣는 파일이 아니다. 임시 읽기 전용 DBaaS `/query`로 실행하지 않는다.
- GUI가 생성한 제약조건 이름은 가정하지 않는다. 최초 form의 CHECK 표현식과 정확히 일치하는
  기존 제약 하나만 교체한다. 다른 정의·중복 정의·검증되지 않은 제약이면 자동으로 삭제하지 않고 실패한다.
- 오류가 나면 `ROLLBACK`하고 원인을 확인한다. 파일 전체가 성공하기 전에는 변경분이 커밋되지 않는다.
  성공한 파일을 재실행하면 추가 컬럼이 이미 있어 실패한다. `002`도 재실행하지 않는다.
- 기존 테이블 단위 SELECT/INSERT 권한이 새 컬럼에도 적용되고 UPDATE 허용 컬럼은 같으므로
  `003` 재실행이나 권한 확대는 필요 없다. 변경된 앱은 추가 UNIQUE 계약을 확인한다.

`004`는 분류 기능용 DB 기반이다. SQL 적용만으로 AI 작업자·OA 분류 화면·실제 AI 연동이 완성되지는 않는다.

### `005` 워크스페이스 관리 레코드

`005_workspace.sql`은 `004` 다음에 적용하는 별도 증분 변경이다. 기존 네 테이블과 form/002/003/004는 유지한다.
OA에 명시적인 `WS_*` 관리 종류·고유 키·프로젝트/블록/버전 참조를 추가한다. 관리 행만 run/bundle이 NULL이며
기존 분석/분류 행의 필수 scope는 새 CHECK로 보존한다. 가짜 실행·번들 ID를 넣지 않는다.
기존 payload·근거·보고서를 변경하지 않고 UPDATE 허용 컬럼/앱 역할도 늘리지 않는다.
명시적인 004 kind 계약이 다르면 실패하고 전체 rollback한다. 이미 성공한 파일은 재실행하지 않는다.
owner/DBA가 작업 중지·백업/복구·잠금 시간을 검토한 뒤 파일 전체를 적용한다. 앱이 DDL을 실행하지 않는다.
이번 실제 실행은 독립 테스트 DB뿐이며 운영 설치를 대신하지 않는다. 기능·실행 방법은 [WORKSPACE.md](../oa/WORKSPACE.md)에 있다.

## 2. 데이터 계약

아래는 기존 실행 중인 기본 분석 계약이다. `004` 적용 후 추가되는 종류는 다음과 같다.

| 테이블 | 추가 허용 종류 |
|---|---|
| VWP 전용 | `CLUSTER_JOB`, `CLUSTER_PART` |
| OA 전용 | `CLUSTER_STATE`, `CLUSTER_JOB`, `CLUSTER_PART`, `CLUSTER_MEMBER`, `CLUSTER_EVENT`, `CLUSTER_RULE` |
| OA → VWP | `CLUSTER_REQUEST` |
| VWP → OA | `CLUSTER_PART`, `CLUSTER_RESULT` |

분류 개정은 기존 sanity 사람 판정 개정과 다른 컬럼이다. 앱의 payload 검증·작업자·수신/확정 흐름은 [PROTOCOL](../PROTOCOL.md#vwp-ai와-분류-계약)을 따른다.

| 테이블 | 구분 | 행 하나의 의미 |
|---|---|---|
| VWP 전용 | `RUN`, `UNIT`, `FILE`, `GROUP`, `EVIDENCE` | 실행, 예상 단위, 파일, 문제 군집, PASS 포함 개별 근거 |
| OA 전용 | `RECEIVED_BUNDLE`, `REVIEW_UNIT`, `REVIEW_EVENT`, `REPORT` | 검증된 수신본, 전체 검토 단위, 검토 개정, 개정별 불변 보고서 |
| VWP → OA | `PING`, `ANALYSIS_BUNDLE`, `EVIDENCE_RESPONSE` | 연결 확인, 범위/무결성 검증한 gzip 번들, 근거 요청에 대한 최종 응답 |
| OA → VWP | `PING`, `EVIDENCE_REQUEST` | 연결 확인, 허용된 논리 ID/범위에 대한 추가 근거 요청 |

- `record_id`, `request_id`, lease token UUID는 앱이 생성한다. 같은 작업을 재시도할 때는 기존 ID를 재사용한다.
  lease token만 매번 새로 발급한다. `message_id`는 길이 128 이하의 영속 식별자다.
- VWP `analysis_id`는 해당 RUN의 `record_id`다. UNIT/FILE의 parent는 RUN, GROUP/EVIDENCE의 parent는 UNIT이다.
  `entity_key`는 실행·종류 내에서 유일하다. 파일/단위/그룹 키는 기존 논리 ID, 근거 키는 원본 내 고유 record ID를 쓴다.
  같은 분석의 parent 존재는 FK로 확인하지만 parent 종류·unit 일치·파일/그룹 연결은 앱 검증이다.
- VWP `payload`에는 불변 입력/규칙/식별 필드/위치/개별 판정을 넣고, `state`에는 가변 checkpoint·집계를 넣는다.
  RUN/UNIT/GROUP/CLUSTER_JOB의 state를 바꾼다. FILE/EVIDENCE/CLUSTER_PART의 state/version/갱신 시각은 고정한다.
  기본/legacy는 원문을 raw_bytes에 저장한다. 선택형 [011](PAYLOAD_STORAGE.md)은 V2 근거 원문도 검증된 압축 본문에 보존하고 읽을 때 동일 byte로 복원한다. DB 원문 보관이 불허되면 배포를 중단하고
  승인된 VWP 파일 위치/해시 참조 어댑터를 추가해야 한다. DDL의 local_evidence_ref만으로 지원되는 것은 아니다.
- OA 수신본의 state에 `current_review_revision`을 저장한다. 초기 개정 0의 REVIEW_EVENT와 함께 만든다.
  REVIEW_EVENT는 한 개정의 결정 변경 목록·검토자·사유를 payload에 담는다. 개별 단위는 REVIEW_UNIT 행으로 유지한다.
  REVIEW_EVENT/REVIEW_UNIT/REPORT는 추가 전용이며, 보고서는 `payload_bytes`에 원본 UTF-8 바이트와 SHA-256을 보존한다.
- Jira 게시와 시뮬레이터는 폐지했다. 신청 원본에는 Jira 작업이 없으며 이후 변경에서도 추가하지 않는다.
  검토·보고서는 OA 대시보드에서 확인한다.
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

아래 DB 권한 분리는 기본 `restricted` 모드 기준이다. 명시적 `single_account`는 [별도 계약](CONNECTION_HANDOFF.md#31-단일-계정-모드)의 전체 유효 권한과 격리 미제공을 검사한다. 정상 앱의 메시지 작성·수신·불변값·트랜잭션·lease 사용 규칙은 두 모드 모두 유지한다.

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

일반 접속 확인은 [읽기 전용 설치 점검](INSTALLATION_CHECKS.md)으로 수행한다. 아래 PING은 사용자/DBA가 승인한 **별도 송수신 시험**에서만 VWP 앱 계정으로 등록한다. 재실행 시 새로운 message_id를 사용한다.

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
실제 로그·개인정보·비밀은 접속 검사에 넣지 않는다. 기본 모드에서 앱에 DELETE 권한을 부여하지 않는다. 단일 모드는 기존 DELETE 보유를 허용하지만 삭제 기능을 추가하지 않는다.

로컬 회귀 검사는 운영 DB가 아닌 별도 빈 DB에서 실행한다. 두 앱 역할은 미리 준비한다.

```bash
bash db/tests/create_fixture.sh -h HOST -U OWNER -d sanity_test_example
python3 db/tests/check_concurrency.py -h HOST -U OWNER -d sanity_test_example
```

fixture는 `form`을 감싸 GUI 생성을 모사한 뒤 `002` 인덱스/FK → `003` 권한 적용을 검사한다.
smoke 테스트 행은 rollback하며, 테스트용 빈 테이블은 보존한다.
`tests/test_schema.py`는 신청용 컬럼 정의 4개·기존 설정 SQL의 원본 hash와 승인된 증분 파일 목록을 확인한다.
`tests/test_migration.py`는 기존 행 보존, GUI 제약 이름 차이, 스키마 불일치 rollback,
재실행 시 안전한 실패, 기존 무결성·앱 권한 유지를 실제 PostgreSQL에서 검사한다.
Python DB 회귀 검사도 동일한 `form`으로 생성한 테이블을 사용한다.
concurrency 검사는 실제 두 세션에서 잠긴 행을 건너뛰고 서로 다른 작업을 점유하는지 검사한 뒤,
이 검사에서 생성한 UUID 식별자의 합성 PING 두 개만 owner로 정리한다.
이 DDL 검사와 별도로 Python 어댑터의 실제 PostgreSQL 통합 전수 검사는 루트 `run_tests.py --profile full --require-db`로 실행한다. 기본 `run_tests.py --require-db`는 빠른 핵심 회귀다.
실제 사내 GUI SQL 필터, 정식 전체 접근·운영 연결/TLS/백업, 실데이터 성능은 로컬 검증에 포함되지 않는다.
