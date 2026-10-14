# Sanity Reviewer — OA 대시보드 prototype

VWP bridge와 OA bridge는 **같은 PostgreSQL DB의 4개 테이블로만 통신**합니다.
검토 화면은 React·shadcn 기반 [OA 대시보드](oa-bridge/README.md)입니다.
Jira 조회·게시와 시뮬레이터는 폐지했습니다. VWP AI 어댑터는 구현했으며 기본 설정은 disabled입니다.

VWP AI와 사람이 함께 만드는 분류 루프, 대량 report 처리, 그룹별 evidence 추가의 **확장 설계**는
[요구사항 14절](requirement.md#ai-clustering)에 정리했습니다. 알려진 두 report 형식에 대해 AI 규칙 제안·전체 시험·사람 확정 루프를 구현했습니다.
실제 사내 AI/DB 연결과 새 report 형식·의미 기반 후보 검색은 아직 검증/확장 대상입니다.

프로젝트 등록·SSO 사용자 연결·담당 권한·블록별 NET_VER/REVISION·ECO·PoR의 최신 기준은
[요구사항 15절](requirement.md#dashboard-domain-spec)에 정리했습니다. PoR은 각 블록 담당자가 지정합니다.
해당 화면은 현재 브라우저 데모이며, 운영 SSO/RBAC와 관리 정보·PoR의 DB/API 연동은 후속 구현 대상입니다.

SSO의 인증 검증·내부 사용자 연결은 교체 가능한 추상 인터페이스로 분리했습니다.
사내 AI는 먼저 [내부 AI 작업 범위](INTERNAL_AI_GUIDE.md)와 [AGENTS.md](AGENTS.md)를 읽습니다.
[SSO 연동 인수인계](oa-bridge/SSO_INTEGRATION.md)의 회사 어댑터, 허용된 입력 매핑과
[report parser 인수인계](vwp-bridge/PARSER_INTEGRATION.md)의 item별 추출/명시적 등록만 구현하며,
DB 계약·분류·권한 검사·핵심 UI는 수정하지 않습니다. 예상 밖 오류는 수정/재시도를 멈추고 비식별 진단 자료와 함께 Codex에게 문의합니다.
실제 report는 사내 AI/담당자가 사내에서만 검증합니다. Codex에는 합성 재현 입력만 전달합니다.
파서 추상 인터페이스·완료/원문/건수 검사를 제공했으며 회사 registry는 초기값이 비어 있습니다.
사내 구현은 `site_integration/corporate_parsers.py`에서 등록하고 `vwp-bridge/corporate_analyze.py analyze`로 실행합니다.

사용자가 **psycopg2로 PostgreSQL 직접 접속 가능**함을 확인했습니다. 기본 접근 모드는 postgresql입니다.
실제 호스트·DB 이름·계정·비밀번호는 각 서버의 비공개 `.env`/환경변수에서 설정합니다.
이전 DBaaS HTTP는 임시 읽기 전용으로만 유지하며, 명시적으로 선택하면 저장은 차단합니다.
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

테이블은 기존 form으로 신청한 상태입니다. 신청 원본은 유지하고 이후 변경은 별도 SQL로 적용합니다.
자세한 순서는 [DB 안내](db/README.md)를 따릅니다.

| 테이블 | 역할 |
|---|---|
| sanity_vwp_data | PASS 포함 전체 근거와 봉인된 분석 |
| sanity_oa_data | 수신본·전체 검토 단위·검토 개정·불변 보고서 |
| sanity_vwp_to_oa | 분석 번들·추가 근거 응답·분류 결과 조각 |
| sanity_oa_to_vwp | 추가 근거·AI 분류 요청 |

**신청한 form으로 생성 → db/002_indexes.sql → 역할 준비 → db/003_grants.sql → db/004_clustering.sql**.
GUI에는 컬럼 정의 전체만 넣고 CREATE TABLE이나 바깥 괄호를 붙이지 않습니다.
이미 `002`·`003`까지 완료했다면 [004_clustering.sql](db/004_clustering.sql)만 적용합니다.
완료한 단계를 반복하거나 테이블을 재신청·재생성하지 않습니다. `004`는 기존 행을 보존하는 증분 변경이며,
owner/DBA가 작업을 중지한 점검 시간에 파일 전체를 한 번 실행합니다.
앱은 DDL이나 자동 테이블 생성을 하지 않습니다.

각 환경에 해당 폴더와 루트 `sanity_*.py`, `requirements.txt`를 함께 배포합니다.
AI 접속 설정은 VWP에만 두며 OA에서는 어댑터를 실행하지 않습니다.
VWP/OA는 서로 다른 제한된 로그인과 각각 sanity_vwp_app/sanity_oa_app 역할을 사용합니다.
owner·superuser·상대 역할 상속·과도한 유효 권한은 거부됩니다.

## 2. 비공개 PostgreSQL 접속 설정

[.env.example](.env.example)의 변수 이름을 저장소 루트 private .env에 옮기고 실제 값은 현장에서 입력합니다.
.env 및 변형 파일은 버전 관리에서 제외하며 해당 실행 계정만 읽도록 권한을 제한합니다.
사내 접속 원문 `dbaccess.md`도 Git에서 제외합니다. 공개 저장소에는
[접속 설정 예시](dbaccess.example.md)만 포함합니다.
브라우저나 VITE_* 변수에 DB 접속값·토큰을 넣지 않습니다.

- `SANITY_DB_ACCESS=postgresql`: 기본값. 드라이버는 `psycopg2`입니다.
- `SANITY_DB_HOST`, `SANITY_DB_NAME`: 같은 DB 서버와 DB 이름. DB 이름과 스키마는 다릅니다.
- `SANITY_DB_PORT`: 실제 발급된 포트, 생략 시 5432.
- `SANITY_DB_USER_NAME`, `SANITY_DB_PASSWORD`: **해당 환경의 제한된 계정**. VWP/OA 각 서버의 `.env`에 서로 다른 값을 둡니다.
- `SANITY_DB_SCHEMA`: 기본 public, 실제 테이블 스키마로 맞춥니다.
- `SANITY_DB_SSLMODE`: 기본 verify-full. DBA의 TLS 정책과 서버 인증서 호스트 이름을 확인합니다.
- `SANITY_DB_SSLROOTCERT`: 필요하면 신뢰할 CA 파일의 절대 경로. TLS 실패 시 자동으로 검증 수준을 낮추지 않습니다.

환경변수가 `.env`보다 우선하며 변수 보간은 하지 않습니다. 비밀번호는 드라이버의 별도 인자로 전달합니다.
값에 `#`·공백 등이 있으면 `.env` 문법에 맞춰 인용합니다. 실제 값을 명령행에 넣지 않습니다.
기존의 비어 있지 않은 `SANITY_VWP_DSN`/`SANITY_OA_DSN`이 있으면 **해당 side의 DSN이 위 연결 필드보다 우선**합니다.
필드 방식으로 바꾸려면 기존 DSN을 제거하거나 비웁니다. 실패 시 다른 계정/방식으로 자동 재접속하지 않습니다.
기존 `.env`에 `SANITY_DB_ACCESS=dbaas_http`가 있으면 직접 `postgresql`로 변경해야 합니다.

테이블·인덱스·역할 준비 후 각 서버에서 읽기 전용으로 연결과 권한을 확인합니다.

```bash
# VWP 서버
.venv/bin/python sanity_db.py check --side vwp
# OA 서버
.venv/bin/python sanity_db.py check --side oa
```

이 명령은 데이터나 DDL을 쓰지 않습니다. 접속 가능해도 기존 권한/스키마 계약과 다르면 실패합니다.
사내 DB의 실제 역할·잠금·트랜잭션·TLS·DDL 적용 경로는 현장에서 검증해야 합니다.
방화벽은 VWP/OA 실행 서버 → DB 실제 호스트·포트 연결이 필요합니다. DB가 브리지로 새 연결을 열지는 않습니다.

기존 `sanity_dbaas.py check --resources`는 임시 HTTP 읽기 확인용으로 남습니다.
별도 `DBAAS_BASE_URL`, `DBAAS_TOKEN`, `DBAAS_CA_PATH`를 사용하며 PostgreSQL 연결에는 필요하지 않습니다.
`dbaas_http` 모드의 저장은 `DBAAS_SQL_CONTRACT_REQUIRED`로 중단하고 직접 접속으로 우회하지 않습니다.

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
임시 DBaaS 모드를 명시적으로 선택한 경우에만 세션에 읽기 전용 안내가 표시되며 업무 DB 작업은 차단합니다.
UI demo는 사용자가 명시적으로 선택하는 합성 메모리 상태이며 새로고침하면 초기화됩니다.
접속 오류를 demo로 자동 대체하지 않으며 demo 검토는 DB에 저장되지 않습니다.

## 4. PostgreSQL 분석·수신 실행

2절에 따라 VWP/OA 각각의 비공개 `.env`를 설정합니다. 두 환경은 같은 DB·스키마를
서로 다른 제한된 로그인으로 사용합니다. HTTP URL·Bearer 토큰은 PostgreSQL 접속 정보가 아닙니다.

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
.venv/bin/python vwp-bridge/main.py respond --request-id REQUEST_UUID
.venv/bin/python oa-bridge/main.py receive-evidence --request-id REQUEST_UUID --out evidence.json
```

근거 요청은 단위별 record ID 1~100개, TTL 기본 3,600초·최대 24시간입니다.
DB 요청만 전달하며 VWP 작업자가 범위·유효 기간·봉인·원문 해시를 검증하고 일반 report를 응답합니다.
요청별 수동 반출 승인은 필요 없고 `--reject`로 명시적 거부는 가능합니다. `--approve-export`는 호환용 선택 옵션입니다.
자격증명은 원문 응답에서 마스킹하며 원본/전송본 해시와 마스킹 여부를 보존합니다.
목록은 4,000자 미리보기, 상세는 8,000자씩 페이지 조회합니다. 원문은 VWP에 그대로 남습니다.
FULFILLED/REJECTED/EXPIRED는 최종 결과이고 일부만 성공으로 내보내지 않습니다.
근거 수신이 기존 판정을 자동 변경하지 않습니다.

폐지된 publish 명령은 --simulate와 --dry-run을 포함해 항상 JIRA_DISABLED로 실패합니다.
--jira-context는 지원하지 않습니다. Jira 작업이나 로컬 게시 시뮬레이터를 만들지 않습니다.

## 6. VWP AI 분류 루프

VWP의 비공개 `.env`에 다음 변수를 설정합니다. 실제 값은 저장소에 올리지 않습니다.

```dotenv
SANITY_AI_MODE=api
SANITY_AI_URL=https://your-internal-host/v1/chat/completions
SANITY_AI_TOKEN=YOUR_PRIVATE_TOKEN
SANITY_AI_MODEL=YOUR_INTERNAL_MODEL
SANITY_AI_API=chat_completions
SANITY_AI_JSON_MODE=off
SANITY_AI_TIMEOUT=120
```

URL은 전체 HTTPS 엔드포인트입니다. Responses 서버는 URL과 `SANITY_AI_API=responses`를 함께 설정합니다.
JSON object 모드 지원을 확인한 경우에만 `SANITY_AI_JSON_MODE=on`을 사용합니다.
사내 PEM CA가 필요하면 `SANITY_AI_CA_PATH`에 절대 경로를 지정합니다. TLS 검증을 끄지 않습니다.
CLI는 `SANITY_AI_MODE=cli`와 절대 실행 파일 경로를 포함한 `SANITY_AI_ARGV` JSON 배열로 명시적으로 선택합니다.
API 실패 시 CLI·다른 모델·합성 응답으로 우회하지 않습니다.

각 환경에서 별도 프로세스로 작업자를 실행합니다. 브라우저를 열어도 작업자가 자동 기동되지는 않습니다.

```bash
# VWP에서: 분류 요청 + 일반 원문 요청 처리
.venv/bin/python vwp-bridge/main.py work --interval 2
# OA에서: 분류 결과 조각 수신 (화면의 수동 수신으로 대체 가능)
.venv/bin/python oa-bridge/main.py work --interval 2
```

`--once`는 한 번만 처리합니다. OA 작업자는 최초 분석 번들/원문 응답을 자동 수신하지 않으므로 기존 화면의 수신 버튼을 사용합니다.

1. 분석 번들을 수신한 뒤 **AI 분류** 탭에서 item과 자연어 요청을 선택합니다.
2. VWP는 최대 64건의 표본으로 선언형 identity 규칙을 제안하고 **전체 기록**에 적용합니다. 규칙만 시험하는 모드도 있습니다.
3. 결과 수신 후 미분류·그룹 소속 변경·모든 FAIL 연관 후보·표본/출처를 확인합니다. 원문은 ‘추가 근거’에서 조회합니다.
4. 사유를 기록하고 **제안 적용 → 후보별 판단 → 분류 확정 → 규칙 재사용 저장**을 각각 수행합니다.
5. 피드백으로 다시 요청하면 새 작업/개정을 만들고 과거 결과는 보존합니다. sanity PASS/FAIL 검토는 기존 대장에서 별도로 합니다.

같은 scope의 확정 규칙은 다음 run에 재사용합니다. 후보 비교는 현재 정확한 필드 대조이며 확률·공통 원인을 추론한 결과가 아닙니다.
기존 FAIL 기록을 포함하는 확정 그룹을 후보 대상으로 삼고, 한 scope의 비교 그룹이 100개를 넘으면 `CATALOG_LIMIT`로 명시적으로 중단합니다.
AI 표본 수와 전체 적용 건수는 별도입니다. 새 파서 자동 생성, 임의 구성원 이동/merge·split UI, 의미 기반 검색, 대규모 성능 보증은 후속 범위입니다.

## 7. 합성 demo와 검증

사내 DB 없이 **새 로컬 mock DB를 생성하고 전체 테스트**하려면 다음을 실행합니다.
Linux/Unix와 PostgreSQL 16 이상 서버 바이너리(`initdb`, `pg_ctl`, `postgres`), Python 의존성이 필요합니다.
바이너리 경로가 PATH에 없으면 `--pg-bin /path/to/postgresql/bin`을 추가합니다. 자동 다운로드/설치는 하지 않습니다.

```bash
.venv/bin/python db/tests/run_mock.py
```

이 mock은 SQLite/가짜 SQL 응답이 아니라 **실제 PostgreSQL 엔진 + 합성 report + 모의 AI 응답**입니다.
매번 별도 `/tmp/sanity-mock-pg-*`에 새 DB를 만들고, TCP 수신 없이 전용 로컬 소켓·SCRAM 인증을 사용합니다.
VWP/OA 로그인은 서로 다르며 임시 비밀번호를 자동 생성합니다. root 실행 시 PostgreSQL만 `nobody`로 실행합니다.
코드/SQL만 임시 폴더에 복사하고 접속 관련 환경변수를 제거하므로 원래 `.env`·사내 접속 문서·실제 report는 사용하지 않습니다.
기존 form → `002`·`003`·`004` → SQL smoke → 양쪽 접속/권한 검사 → 전체 Python 테스트(skip 불허) →
4종 CLI demo → 10,000건/100그룹 파싱·저장 규모 검사를 실행합니다. 규모는 `--rows`, `--groups`로 조정합니다.
모의 AI는 분류 회귀 테스트에서만 사용합니다. 10,000건 규모 검사는 AI 품질이나 전체 분류 처리량 검증이 아닙니다.
정상 완료와 일반 오류 시 DB 프로세스를 종료하고 결과 경로를 출력합니다. `summary.json`, `tests.json`,
`tests.log`, `scale.json`, demo별 보고서와 정지된 DB는 조사할 수 있도록 남기며 자동 삭제하지 않습니다.
강제 종료/전원 차단 시에는 자동 정지를 보장하지 않습니다. 이 도구는 운영 DB 접속 실패 시의 자동 대체 경로가 아닙니다.

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
- 수신 lease 기본 300초, CLI 설정 범위 1~3600초입니다. 분류 작업은 페이지 처리/AI 대기 중 heartbeat를 갱신하고 만료·탈취된 token의 완료를 거부합니다.
- DB 오류를 SQLite·로컬 파일·직접 bridge 통신으로 우회하지 않습니다.
- 코드만 같아도 같은 문제는 아닙니다. 객체·조건·규칙까지 비교하고 정보 부족은 독립 UNDECIDED로 보존합니다.
- 최초 예시 3개·원문 2,048문자만 전송하지만 PASS 포함 전체 기록은 VWP DB에 보존합니다. 대표 예시 검토로 전체 완료를 대신하지 않습니다.
- summary JSON 16MiB, 전송 압축 전후 각각 64MiB, 원문 한 건 64MiB 제한입니다. 분류는 128건/약 512KiB씩 분할하지만 최초 분석 번들의 전체 그룹 목록과 원문 응답 전체에는 기존 상한이 남습니다.
- 실제 report 계약·기준 목록 자동 수집·사내 AI 호환성·배포 DB 권한/연결 검증·다중 사용자 인증은 확인 대기입니다.
- sanity 원문·객체 경로·측정값·관련 설정의 OA 조회는 허용됩니다. 자격증명 보호는 등록된 비밀값과 명시적 패턴 검사이며 모든 비밀을 탐지하는 DLP는 아닙니다.
- 봉인 번들/분류 입력·결과에서 자격증명을 발견하면 해당 전달을 중단합니다. 원문 응답의 마스킹과 구분합니다. 과거 SQLite/16테이블 자동 이전은 없습니다.

[데이터 계약](PROTOCOL.md) · [요구사항](requirement.md) · [DB 신청](db/README.md) · [검증 기록](REVIEW.md)
