# Sanity Reviewer — OA 대시보드 prototype

VWP bridge와 OA bridge는 **같은 PostgreSQL DB의 4개 테이블로만 통신**합니다.
검토 화면은 React·shadcn 기반 [OA 대시보드](oa-bridge/README.md)입니다.
Jira 조회·게시와 시뮬레이터는 폐지했습니다. 실제 운영 AI는 NOT_CONFIGURED입니다.

현재 DBaaS HTTP는 **임시 읽기 전용**이며 정식 전체 접근 방식은 사용자 제공 대기 중입니다.
기본 dbaas_http 모드에는 전체 저장 어댑터가 없고 DBAAS_SQL_CONTRACT_REQUIRED로 중단합니다.
아래 DB 저장 흐름은 명시적으로 승인된 직접 PostgreSQL 환경/로컬 통합 테스트에서만 실행합니다.
SQLite, 브리지 간 직접 HTTP/RPC·파일 전달, 로컬 DB fallback은 없습니다.
대시보드의 OA 로컬 API 접속과 공통 DBaaS API는 브리지 간 직접 통신과 구분합니다.

## 1. 설치와 DB 준비

Python 3.10 이상, PostgreSQL 16 이상·UTF-8을 전제로 합니다. 프런트엔드 의존성은
oa-bridge/dashboard/package-lock.json에 고정되어 있습니다. 현재 Vite의 Node.js 조건은
20.19 이상인 20.x 또는 22.12 이상입니다.

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
```

테이블은 아직 신규 신청 전이며 [DB 안내](db/README.md)의 순서만 사용합니다.

| 테이블 | 역할 |
|---|---|
| sanity_vwp_data | PASS 포함 전체 근거와 봉인된 분석 |
| sanity_oa_data | 수신본·전체 검토 단위·검토 개정·불변 보고서 |
| sanity_vwp_to_oa | 분석 번들·추가 근거 응답 |
| sanity_oa_to_vwp | 추가 근거 요청 |

**db/form/의 4개 컬럼 정의로 GUI 생성 → db/002_indexes.sql → 역할 준비 → db/003_grants.sql**.
GUI에는 컬럼 정의 전체만 넣고 CREATE TABLE이나 바깥 괄호를 붙이지 않습니다.
001/004 설치 파일은 없습니다. 기존 테스트 테이블을 변경·삭제하거나 재사용하지 않습니다.
앱은 DDL이나 자동 테이블 생성을 하지 않습니다.

각 환경에 해당 폴더와 루트 sanity_common.py, sanity_db.py, sanity_evidence.py,
sanity_oa_store.py, sanity_exchange.py, sanity_config.py, sanity_dbaas.py, requirements.txt를 배포합니다.
VWP/OA는 서로 다른 제한된 로그인과 각각 sanity_vwp_app/sanity_oa_app 역할을 사용합니다.
owner·superuser·상대 역할 상속·과도한 유효 권한은 거부됩니다.

## 2. 비공개 설정과 읽기 전용 DBaaS

[.env.example](.env.example)의 변수 이름을 저장소 루트 private .env에 옮기고 실제 값은 현장에서 입력합니다.
.env 및 변형 파일은 버전 관리에서 제외하며 해당 실행 계정만 읽도록 권한을 제한합니다.
사내 접속 원문 `dbaccess.md`도 Git에서 제외합니다. 공개 저장소에는
[접속 설정 예시](dbaccess.example.md)만 포함합니다.
브라우저나 VITE_* 변수에 DB 접속값·토큰을 넣지 않습니다.

- SANITY_DB_ACCESS=dbaas_http: 기본값. 직접 PostgreSQL로 자동 우회하지 않습니다.
- DBAAS_BASE_URL, DBAAS_TOKEN, DBAAS_CA_PATH: DBaaS 주소·Bearer 토큰·사내 CA 절대 경로.
- DBASS_BASE_URL은 별칭이며 원래 이름과 값이 다르면 중단합니다.
- CA는 PEM/DER를 지원하며 생략은 OS 신뢰 저장소를 사용할 때만 허용합니다.

TLS 검증, trust_env=False, redirect 금지, HTTPX I/O별 30초 timeout을 사용합니다.
URL·토큰·CA 경로·응답 detail은 진단에 출력하지 않습니다. 실제 접속 확인은 현장에서 해야 합니다.

```bash
.venv/bin/python sanity_dbaas.py check --resources
```

이 명령은 /api/v1/me와 /api/v1/resources의 GET만 호출합니다. 문서의 /api/vi/me는
/api/v1/me의 오타로 해석했으므로 실제 경로 확인이 필요합니다.
query POST 전송 함수는 DB 쓰기·트랜잭션 지원의 근거가 아닙니다.
정식 전체 접근 방식이 제공되면 권한·잠금·원자성·rollback·자료형·DDL 경로를 확인합니다.

## 3. 로컬 대시보드 실행

private .env에 SANITY_DASHBOARD_TOKEN(최소 32자 무작위 값)과
SANITY_DASHBOARD_REVIEWER(서버가 고정할 검토자 이름)를 설정합니다.
로그인 토큰은 브라우저 메모리에만 유지하며 localStorage·URL·로그에 저장하지 않습니다.

```bash
cd oa-bridge/dashboard
npm ci
npm run build
cd ../..
.venv/bin/python oa-bridge/dashboard_api.py
```

브라우저에서 http://127.0.0.1:8000 에 접속합니다. 빌드된 dist와 API가 같은 origin으로 제공됩니다.
단일 사용자 로컬 prototype이므로 **외부 bind를 하지 않습니다**.
SSO/RBAC·TLS proxy·운영 인증은 구현되지 않았습니다. 서버 검토자 이름은 UI에서 바꿀 수 없습니다.

프런트엔드 개발 시 Python API를 실행한 상태에서 별도 터미널로 다음을 실행합니다.

```bash
cd oa-bridge/dashboard
npm run dev
```

개발 주소는 http://127.0.0.1:5173 이고 /api는 8000으로 proxy합니다.
기본 DBaaS 모드에서는 세션의 읽기 전용 안내를 확인할 수 있지만 업무 데이터 저장·조회 어댑터는 아직 준비되지 않았습니다.
UI demo는 사용자가 명시적으로 선택하는 합성 메모리 상태이며 새로고침하면 초기화됩니다.
접속 오류를 demo로 자동 대체하지 않으며 demo 검토는 DB에 저장되지 않습니다.

## 4. 승인된 직접 PostgreSQL 검증 모드

VWP와 OA의 비공개 설정에서 SANITY_DB_ACCESS=postgresql을 명시하고 같은 DB·스키마를
서로 다른 제한된 로그인으로 지정합니다. DBaaS 값을 DSN으로 바꾸어 사용하지 않습니다.

```bash
# VWP 환경에만 설정
export SANITY_DB_ACCESS='postgresql'
export SANITY_VWP_DSN='service=sanity_vwp'
export SANITY_DB_SCHEMA='public'
# OA 환경에는 별도로 SANITY_OA_DSN='service=sanity_oa' 설정
```

비밀번호는 CLI 인자·소스·결과물에 넣지 않고 승인된 libpq/TLS 설정을 사용합니다.
SANITY_TEST_ADMIN_DSN은 테스트에만 사용하며 실제 앱 환경에 설정하지 않습니다.

```bash
.venv/bin/python vwp-dev/main.py collect --plan plan.json --reports /path/to/reports --out /tmp/sanity-source
.venv/bin/python vwp-bridge/main.py analyze --input /tmp/sanity-source/run.json --out /tmp/sanity-receipt
.venv/bin/python oa-bridge/main.py receive --message-id BUNDLE_ID --brief
.venv/bin/python oa-bridge/main.py status --bundle-id BUNDLE_ID --brief
.venv/bin/python oa-bridge/main.py report --bundle-id BUNDLE_ID --out review.md
```

BUNDLE_ID는 분석 결과의 실제 ID입니다. VWP의 --out은 로컬 접수증일 뿐 전송 경로가 아닙니다.
OA 대시보드에서도 다음 메시지 한 건을 수동 수신할 수 있습니다. NO_MESSAGE는 대기 작업이 없거나
다른 작업자가 처리 중이라는 뜻입니다. 수신은 사람 검토를 승인하지 않습니다.

## 5. 사람 검토와 추가 근거

대시보드는 모든 예상 단위를 보여주고 페이지별로 단위·그룹·요청을 조회합니다.
최근 50개 검토 개정과 전체 개정 수를 표시합니다. 모든 판정은 PASS/FAIL·사유·요청 UUID와
expected_revision을 보내며 개정이 바뀌면 최신 상태 확인이 필요합니다.
blocker 면제와 알려진 FAIL→PASS는 금지합니다. review=COMPLETE와 check=PASS는 별도 상태입니다.
새 번들은 기존 승인을 상속하지 않으며 A→B→A도 서로 다른 개정으로 남습니다.
보고서 다운로드는 저장된 현재 개정의 불변 바이트를 검증해서 제공합니다.

운영 CLI의 검토자 인자는 로컬 수동 작업용이며 대시보드의 서버 인증을 대체하지 않습니다.
같은 요청 재시도에는 같은 UUID, 새 판단에는 새 UUID를 사용합니다.

```bash
.venv/bin/python oa-bridge/main.py review --bundle-id BUNDLE_ID --unit-id u-numeric --request-id REQUEST_UUID --reviewer NAME --conclusion FAIL --reason '전체 판정과 근거 확인' --brief
.venv/bin/python oa-bridge/main.py request-evidence --bundle-id BUNDLE_ID --unit-id u-numeric --record-id RECORD_ID --request-id REQUEST_UUID --reason '원문 추가 확인'
.venv/bin/python vwp-bridge/main.py respond --request-id REQUEST_UUID --approve-export
.venv/bin/python oa-bridge/main.py receive-evidence --request-id REQUEST_UUID --out evidence.json
```

근거 요청은 단위별 record ID 1~100개, TTL 기본 3,600초·최대 24시간입니다.
DB 요청만 전달하며 VWP가 명시적으로 반출 승인하거나 --reject로 거부해야 합니다.
FULFILLED/REJECTED/EXPIRED는 최종 결과이고 일부만 성공으로 내보내지 않습니다.
근거 수신이 기존 판정을 자동 변경하지 않습니다.

폐지된 publish 명령은 --simulate와 --dry-run을 포함해 항상 JIRA_DISABLED로 실패합니다.
--jira-context는 지원하지 않습니다. Jira 작업이나 로컬 게시 시뮬레이터를 만들지 않습니다.

## 6. 합성 demo와 검증

CLI demo는 준비된 별도 테스트 DB와 양쪽 제한된 앱 DSN이 필요하며 UI 메모리 demo와 다릅니다.

```bash
.venv/bin/python demo.py --out demo-output-postgresql
.venv/bin/python demo.py --scenario missing --out demo-output-postgresql-missing
.venv/bin/python run_tests.py
# 별도 sanity_test_* DB의 admin/VWP/OA DSN과 postgresql 모드를 설정한 뒤
.venv/bin/python run_tests.py --require-db
```

CLI demo는 complete/missing/malformed/unsupported를 지원합니다.
새 complete 실행은 expected=3, approved_na=1, unresolved=2, review=INCOMPLETE, check=FAIL입니다.
동일 DB의 동일 번들은 기존 검토 이력을 유지합니다. AI는 NOT_CONFIGURED입니다.
테스트는 자신이 만든 임시 스키마만 정리하며 운영 DB를 초기화하지 않습니다.
DB 테스트 skip은 통합 검증 성공이 아니고 --require-db는 조건 누락 시 실패합니다.
UI 검증 명령과 실제 실행 결과는 [OA 안내](oa-bridge/README.md)와 [REVIEW.md](REVIEW.md)를 확인합니다.

## 복구와 제한

- 분석은 같은 snapshot으로 전체 재계산·대조합니다. 봉인된 근거 손상은 EVIDENCE_CONFLICT로 중단하며 자동 수리하지 않습니다.
- 수신 lease 기본 300초, CLI 설정 범위 1~3600초입니다. 만료·탈취된 token은 private 기록까지 rollback합니다. 자동 heartbeat는 없습니다.
- DB 오류를 SQLite·로컬 파일·직접 bridge 통신으로 우회하지 않습니다.
- 코드만 같아도 같은 문제는 아닙니다. 객체·조건·규칙까지 비교하고 정보 부족은 독립 UNDECIDED로 보존합니다.
- 최초 예시 3개·원문 2,048문자만 전송하지만 PASS 포함 전체 기록은 VWP DB에 보존합니다. 대표 예시 검토로 전체 완료를 대신하지 않습니다.
- summary JSON 16MiB, 전송 압축 전후 각각 64MiB, 원문 한 건 64MiB 제한입니다. 분할 전송·엄격한 전체 메모리 상한은 미구현입니다.
- 실제 report 계약·기준 목록 자동 수집·운영 AI·정식 DB 접근·다중 사용자 인증은 후속 범위입니다.
- 원문 DB 보관과 요약의 반출 허용 범위를 현장에서 확인해야 합니다. 과거 SQLite/16테이블 자동 이전은 없습니다.

[데이터 계약](PROTOCOL.md) · [요구사항](requirement.md) · [DB 신청](db/README.md) · [검증 기록](REVIEW.md)
