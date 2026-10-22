# Sanity Reviewer — AI 분류·판정과 사람의 근거 축적

**AI가 report의 로그를 분류하고 그룹별 판정·근거를 제안하며, 사람이 확정한 구성·판정·근거를 다음 AI 판단에 활용하는 시스템입니다.** 가장 중요한 완료 기준은 AI 판단 품질과 사람의 정정이 다음 판단에 반영되는지입니다. 수집·파서·DB·인증·대시보드는 이 순환을 지원합니다.

현재 목표·실행 구조·사례 검색·품질 수용 기준은 [AI 판단 설계](AI_DECISION_DESIGN.md)를 먼저 읽습니다. 흐름은 **전체 로그 + 관련 확정 사례 → AI 초기 분류 → 사람 확정 → AI 그룹 판정 → 사람 확정 → DB 사례 축적 → 다음 판단**입니다. 첫 report도 기존 사례 없이 AI가 제안할 수 있어야 합니다.

이번 연동은 **사내 OA의 OpenAI 호환 AI API를 우선 사용**합니다. 기존 OA 프로그램이 DB에서 로그·사례를 조회해 모델을 호출하고 결과를 검증·저장하는 구조이며, AI용 HTTP 서버나 새 서버 장비를 필수로 추가하지 않습니다. VWP-bridge와 OA는 **같은 PostgreSQL DB의 4개 테이블로만 통신**합니다. OA의 자기 모델 API 호출은 이 브리지 간 경계와 구분합니다.

**현재 제공 범위:** OA OpenAI 호환 호출·영속 작업·현재 사람 확정 사례 검색·직접 근거 판정 V2와 운영 SSO/V2 검토 연결을 구현했습니다. 운영 진입점은 `/?page=workbench`, API는 `/api/workbench/*`입니다. 첫 순환은 **전체 report 또는 선택 그룹이 설정한 모델 입출력 한도에 들어가는 경우**를 처리합니다. 실제 사내 API·자료의 정확도/성능은 미검증이며, 10,000건·100,000건 이상의 실제 모델 분할·그룹 통합은 COMMON-05 후속입니다. 설정·실행·한계는 [OA AI 실행 안내](oa/AI_RUNTIME.md)를 따릅니다.

서버별 코드는 아래 위치에 있습니다. 각 사내 AI는 자기 `integration/`의 허용 파일만 구현합니다. 실행·의존성·설정 위치는 [소스 구조와 실행 안내](DEPLOYMENT.md)를 따릅니다.

| 담당 | 실제 코드 | 회사 연동 구현 |
|---|---|---|
| VWP | [vwp/](vwp/README.md) — 원천 수집 | `vwp/integration/` |
| VWP-bridge | [vwp_bridge/](vwp_bridge/README.md) — parser·원본 검증·DB 전달·기존 VWP 실행 | `vwp_bridge/integration/` |
| OA | [oa/](oa/README.md) — 모델 호출·작업·사례 검색·검토·SSO·dashboard | `oa/integration/`의 허용 SSO/source 지점. 모델은 제공된 공통 설정 사용 |
| 공통 | [common/](common/README.md) — 계약·DB·설정·검증 | 사내 AI 읽기 전용 |
| 개발 시험 | [devtools/](devtools/README.md), `tools/`, `tests/` | 합성 자료·mock·회귀 |

사내 Q1 검토의 [분산 수집·PRE/POST 인계 설계](vwp/SOURCE_COLLECTION_DESIGN.md)에 사용자별 REVISION 제출, 여러 원천 루트, 예상 범위 승인, 버전/실행/원문 중복 기준을 정리했습니다. 기존 schema 1 collector와 별도 V2 제출 경로를 제공합니다.

[사용자 제출 실행 안내](vwp/SUBMISSION.md)의 `bin/sanity submit/status/retry`, VWP 내부 제출/영속 원본 보관, DB 분할 송수신·OA 페이지/접수 응답을 구현했습니다. **회사 입력·신원/승인 SDK와 실제 마운트/운영 DB는 미연결**입니다. schema 1의 `collect`는 유지하며 사용자 DB 계정이나 OA report 파일 복사로 대체하지 않습니다. 신규 DB 계약은 `db/008_submissions.sql`, 설계 근거는 [제출 명세](vwp/SUBMISSION_DESIGN.md)입니다.

AI와 사람이 함께 만드는 분류 루프, 대량 report 처리, 그룹별 evidence 추가의 **확장 설계**는
[요구사항 14절](requirement.md#ai-clustering)에 정리했습니다. 알려진 두 report 형식에 대해 AI 규칙 제안·전체 시험·사람 확정 루프를 구현했습니다.
실제 사내 AI/DB 연결과 새 report 형식·의미 기반 후보 검색은 아직 검증/확장 대상입니다.

프로젝트 등록·SSO 사용자 연결·담당 권한·블록별 NET_VER/REVISION·ECO·PoR의 최신 기준은
[요구사항 15절](requirement.md#dashboard-domain-spec)에 정리했습니다. PoR은 각 블록 담당자가 지정합니다.
기존 데모와 별도로 **`/workspace`에서 로그인·프로젝트·블록·버전·검사·PoR을 서버에 연결**했습니다.
10개 테스트 계정의 범위를 서버에서 검사하고 PoR·변경 이력을 PostgreSQL에 저장합니다. 새로고침/다른 계정 조회/서버 재시작에도 유지됩니다.
검사별 사용자 판정·사유·재사용 조건과 제안을 따로 저장합니다. 원본 FAIL에 사용자 PASS를 기록할 수 있고,
확정한 조건을 다음 보고서의 제안에 재사용합니다. 이 독립 합성 환경의 일반 제안은 조건 적용 엔진이며 운영 OA AI 경로와 구분합니다.
별도 [Codex 합성 mock 시험](oa/WORKSPACE.md#codex-모델을-사용하는-합성-mock-시험)은 `gpt-6-luna`를 실제 호출해 AI 초기 분류 → 사람 수정·DB 확정 → 다음 AI 입력의 사례 활용을 검증합니다. 명시적으로 활성화한 합성 workspace의 **AI로 그룹 만들기** 버튼에서도 호출할 수 있으며 일반 회귀 테스트는 외부 모델을 호출하지 않습니다.
sanity item의 **검토 시작**에서는 단일 report의 평균 10,000건·100,000건 이상 로그를 1단계 분류·확정 후 2단계 그룹별 판정으로 검토합니다.
그룹/원문은 페이지로 조회하며 분류 작업은 저장한 위치에서 이어갈 수 있습니다. 분류 확정 전에 판정을 시작할 수 없습니다.
확정 분류 사례와 그룹 판정 근거를 각각 재사용하고, 재분류된 그룹의 이전 판정은 재검토 대상으로 남깁니다.
[워크스페이스 실행 안내](oa/WORKSPACE.md)를 따릅니다. 운영 화면은 같은 검토 흐름에 SSO의 현재 프로젝트/블록 권한을 적용합니다. 합성 환경의 대량 참고 엔진 검증은 OA 모델의 대량 처리 검증을 대신하지 않으며 실제 회사 SDK·원천·DB 배포는 사내 검증 대상입니다.

SSO의 인증 검증·내부 사용자 연결은 교체 가능한 추상 인터페이스로 분리했습니다.
사내 AI는 먼저 [내부 AI 작업 범위](INTERNAL_AI_GUIDE.md)와 [AGENTS.md](AGENTS.md)를 읽습니다.
[사내 AI 통합 인계 명세](INTERNAL_AI_HANDOFF.md)에 작업별 선행 정보·허용 파일/메서드·입출력·수용 기준·실행 명령·Codex 후속 연결과 복사 가능한 시작 지시문을 정리했습니다.
사내 개발 AI 세 명에게는 [VWP 원천 입력 지시문](vwp/INTERNAL_AI_TASK.md), [VWP-bridge parser·원천 전달 지시문](vwp_bridge/INTERNAL_AI_TASK.md), [OA 사용자 연결·모델 연동 준비 지시문](oa/INTERNAL_AI_TASK.md)을 각각 전달합니다. 각 문서 마지막에 복사할 시작 명령이 있습니다.
DB 설정 키·모드별 권한·서버별 확인 명령·비공개 접속 인계 양식은 [DB 연결 인계](db/CONNECTION_HANDOFF.md)에 있습니다. VWP 원천 수집에는 DB 계정이 없고, 두 bridge만 기본적으로 별도 제한 로그인을 사용합니다. 별도 계정이 없는 환경에는 명시적 `SANITY_DB_ALLOW_SINGLE_ACCOUNT=true`의 [단일 계정 모드](db/CONNECTION_HANDOFF.md#31-단일-계정-모드)를 제공합니다. 단일 모드는 DB의 private/큐 컬럼 격리를 제공하지 않습니다.
코드는 서버별로 **`vwp/`·`vwp_bridge/`·`oa/`**, 공통 계약은 **`common/`**, 합성 시험은 **`devtools/`**에 분리했습니다. 각 서버는 Git 저장소 루트에서 자기 모듈만 실행합니다. [서버별 코드·실행 안내](DEPLOYMENT.md)에 디렉터리 책임·의존성·실행 명령·설정 위치를 정리했습니다.

GCA·VC LP·SpyGlass의 공개 문서를 참고한 **초기 추출 파서와 1,000개 합성 데이터 대시보드**도 제공합니다.
가상 이용자 10명·프로젝트 3개·블록 9개를 구성하고, 독립 mock PostgreSQL에서 실제 수집/파싱/수신/저장을 검증한
읽기 전용 스냅샷을 `/?demo=1&dataset=reports`에서 확인합니다. 생성·실행 방법과 사내 AI의 후속 구현 범위는
[공개 report 형식 인계](vwp_bridge/integration/PUBLIC_REPORT_FORMATS.md)에 정리했습니다. native 현행 버전의 완료/정확성 검증과 구분합니다.

사용자가 **psycopg2로 PostgreSQL 직접 접속 가능**함을 확인했습니다. 기본 접근 모드는 postgresql입니다.
실제 호스트·DB 이름·계정·비밀번호는 각 서버의 비공개 `.env`/환경변수에서 설정합니다.
이전 DBaaS HTTP는 임시 읽기 전용으로만 유지하며, 명시적으로 선택하면 저장은 차단합니다.
SQLite, 브리지 간 직접 HTTP/RPC·파일 전달, 로컬 DB fallback은 없습니다.
대시보드의 OA 로컬 API 접속과 공통 DBaaS API는 브리지 간 직접 통신과 구분합니다.

## 1. 설치와 DB 준비

Python 3.10 이상, PostgreSQL 12.1 이상·UTF-8을 전제로 합니다. DB 실검증 버전은 12.1과 16.15이며,
포털 적용·검증 방법은 [DB 안내](db/README.md)를 따릅니다. 프런트엔드 의존성은
oa/dashboard/package-lock.json에 고정되어 있습니다. 현재 Vite의 Node.js 조건은
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

각 서버는 [서버별 코드·실행 안내](DEPLOYMENT.md)에 따라 자기 역할의 `requirements.txt`를 설치하고 자기 진입점만 실행합니다. `common/`은 공유 계약이며 서버 앱을 import하지 않습니다. 기본 모드에서 VWP-bridge/OA는 별도 제한 로그인과 각각 `sanity_vwp_app`/`sanity_oa_app` 역할을 사용합니다. 단일 모드는 위 별도 계약을 따릅니다. 모델 자격증명은 해당 모델을 호출하는 서버의 비공개 설정에만 두며, OA 모델 설정 키와 별도 작업자 실행은 [OA AI 실행 안내](oa/AI_RUNTIME.md)를 따릅니다. 두 모드 모두 owner·superuser 등 금지 권한을 검사합니다.

## 2. 비공개 PostgreSQL 접속 설정

VWP-bridge는 [자기 설정 예제](vwp_bridge/.env.example), OA는 [자기 설정 예제](oa/.env.example)의 키를 각 서버의 **저장소 루트 private `.env`**에 설정합니다. `vwp_bridge/.env`, `oa/.env`, `common/.env`는 자동으로 읽지 않습니다. `.env`·실제 접속 문서 `dbaccess.md`는 Git 제외 대상이며 실행 계정만 읽도록 관리합니다. 공개 저장소에는 [접속 인계 예시](dbaccess.example.md)만 남깁니다. 브라우저나 `VITE_*`에는 비밀값을 넣지 않습니다.

- `SANITY_DB_ACCESS=postgresql`: 기본값. 드라이버는 `psycopg2`입니다.
- `SANITY_DB_ALLOW_SINGLE_ACCOUNT=false`: 기본 제한 모드. 정확한 `true`만 공유 로그인 모드이며 빈 값·`yes`·`1` 등은 오류입니다.
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
.venv/bin/python -m common.sanity_db check --side vwp
# OA 서버
.venv/bin/python -m common.sanity_db check --side oa
```

이 명령은 데이터나 DDL을 쓰지 않습니다. 접속 가능해도 기존 권한/스키마 계약과 다르면 실패합니다.
사내 DB의 실제 역할·잠금·트랜잭션·TLS·DDL 적용 경로는 현장에서 검증해야 합니다.
방화벽은 VWP/OA 실행 서버 → DB 실제 호스트·포트 연결이 필요합니다. DB가 브리지로 새 연결을 열지는 않습니다.

기존 `python -m common.sanity_dbaas check --resources`는 임시 HTTP 읽기 확인용으로 남습니다.
별도 `DBAAS_BASE_URL`, `DBAAS_TOKEN`, `DBAAS_CA_PATH`를 사용하며 PostgreSQL 연결에는 필요하지 않습니다.
`dbaas_http` 모드의 저장은 `DBAAS_SQL_CONTRACT_REQUIRED`로 중단하고 직접 접속으로 우회하지 않습니다.

## 3. 로컬 대시보드 실행

private .env에 SANITY_DASHBOARD_TOKEN(최소 32자 무작위 값)과
SANITY_DASHBOARD_REVIEWER(서버가 고정할 검토자 이름)를 설정합니다.
로그인 토큰은 브라우저 메모리에만 유지하며 localStorage·URL·로그에 저장하지 않습니다.

```bash
cd oa/dashboard
npm ci
npm run build
cd ../..
.venv/bin/python oa/dashboard_api.py
```

브라우저에서 http://127.0.0.1:8000 에 접속합니다. 빌드된 dist와 API가 같은 origin으로 제공됩니다.
단일 사용자 로컬 prototype이므로 **외부 bind를 하지 않습니다**.
이 명령은 local_token 방식의 로컬 검증용입니다. 운영 SSO·현재 권한 검사는 별도 `oa.sso_app` 진입점을 사용합니다. 서버 검토자 이름은 UI에서 바꿀 수 없습니다.

프런트엔드 개발 시 Python API를 실행한 상태에서 별도 터미널로 다음을 실행합니다.

```bash
cd oa/dashboard
npm run dev
```

개발 주소는 http://127.0.0.1:5173 이고 /api는 8000으로 proxy합니다.
임시 DBaaS 모드를 명시적으로 선택한 경우에만 세션에 읽기 전용 안내가 표시되며 업무 DB 작업은 차단합니다.
UI demo는 사용자가 명시적으로 선택하는 합성 메모리 상태이며 새로고침하면 초기화됩니다.
`/?demo=1`과 `/workspace?demo=1`은 최신 워크스페이스 UI에서 프로젝트·버전·PoR·로그 분류·그룹 판정을 체험합니다.
이전 번들 검토 대장은 `/?demo=1&flow=legacy`에서 확인합니다. [데모 사용법](oa/dashboard/README.md)을 참고하세요.
접속 오류를 demo로 자동 대체하지 않으며 demo 검토는 DB에 저장되지 않습니다.

## 4. PostgreSQL 분석·수신 실행

2절에 따라 VWP/OA 각각의 비공개 `.env`를 설정합니다. 두 환경은 같은 DB·스키마를
기본적으로 서로 다른 제한 로그인으로 사용하며 명시적 단일 계정 모드는 위 별도 계약을 따릅니다. HTTP URL·Bearer 토큰은 PostgreSQL 접속 정보가 아닙니다.

비밀번호는 CLI 인자·소스·결과물에 넣지 않고 승인된 libpq/TLS 설정을 사용합니다.
SANITY_TEST_ADMIN_DSN은 테스트에만 사용하며 실제 앱 환경에 설정하지 않습니다.

```bash
.venv/bin/python vwp/main.py collect --plan plan.json --reports /path/to/reports --out /tmp/sanity-source
.venv/bin/python vwp_bridge/main.py analyze --input /tmp/sanity-source/run.json --out /tmp/sanity-receipt
.venv/bin/python oa/main.py receive --message-id BUNDLE_ID --brief
.venv/bin/python oa/main.py status --bundle-id BUNDLE_ID --brief
.venv/bin/python oa/main.py report --bundle-id BUNDLE_ID --out review.md
```

BUNDLE_ID는 분석 결과의 실제 ID입니다. VWP의 --out은 로컬 접수증일 뿐 전송 경로가 아닙니다.
OA 대시보드에서도 다음 메시지 한 건을 수동 수신할 수 있습니다. NO_MESSAGE는 대기 작업이 없거나
다른 작업자가 처리 중이라는 뜻입니다. 수신은 사람 검토를 승인하지 않습니다.

## 5. 사람 검토와 추가 근거

대시보드는 모든 예상 단위를 보여주고 페이지별로 단위·그룹·요청을 조회합니다.
최근 50개 검토 개정과 전체 개정 수를 표시합니다. 모든 판정은 PASS/FAIL·사유·요청 UUID와
expected_revision을 보내며 개정이 바뀌면 최신 상태 확인이 필요합니다.
blocker 면제와 알려진 FAIL→PASS는 금지합니다. review=COMPLETE와 check=PASS는 별도 상태입니다.
이 절은 legacy bundle/CLI 검토 계약입니다. 운영 `/api/workbench`의 그룹 판정과 독립 `/workspace`의 사용자 판정은 원본 FAIL에 PASS를 저장할 수 있으며 원본 결과를 보존합니다.
새 번들은 기존 승인을 상속하지 않으며 A→B→A도 서로 다른 개정으로 남습니다.
보고서 다운로드는 저장된 현재 개정의 불변 바이트를 검증해서 제공합니다.

운영 CLI의 검토자 인자는 로컬 수동 작업용이며 대시보드의 서버 인증을 대체하지 않습니다.
같은 요청 재시도에는 같은 UUID, 새 판단에는 새 UUID를 사용합니다.

```bash
.venv/bin/python oa/main.py review --bundle-id BUNDLE_ID --unit-id u-numeric --request-id REQUEST_UUID --reviewer NAME --conclusion FAIL --reason '전체 판정과 근거 확인' --brief
.venv/bin/python oa/main.py request-evidence --bundle-id BUNDLE_ID --unit-id u-numeric --record-id RECORD_ID --request-id REQUEST_UUID --reason '원문 추가 확인'
.venv/bin/python vwp_bridge/main.py respond --request-id REQUEST_UUID
.venv/bin/python oa/main.py receive-evidence --request-id REQUEST_UUID --out evidence.json
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

이 절은 **현재 legacy VWP 실행의 설정·명령**입니다. 이번 우선 연동인 OA 모델 호출·사례 검색·직접 근거 판정은 [OA AI 실행 안내](oa/AI_RUNTIME.md)를 따릅니다. 아래 legacy 키·worker와 별도로 실행합니다.

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
.venv/bin/python vwp_bridge/main.py work --interval 2
# OA에서: 분류 결과 조각 수신 (화면의 수동 수신으로 대체 가능)
.venv/bin/python oa/main.py work --interval 2
```

`--once`는 한 번만 처리합니다. OA 작업자는 최초 분석 번들/원문 응답을 자동 수신하지 않으므로 기존 화면의 수신 버튼을 사용합니다.

1. 분석 번들을 수신한 뒤 **AI 분류** 탭에서 item과 자연어 요청을 선택합니다.
2. VWP는 최대 64건의 표본으로 선언형 identity 규칙을 제안하고 **전체 기록**에 적용합니다. 규칙만 시험하는 모드도 있습니다.
3. 결과 수신 후 미분류·그룹 소속 변경·모든 FAIL 연관 후보·표본/출처를 확인합니다. 원문은 ‘추가 근거’에서 조회합니다.
4. 사유를 기록하고 **제안 적용 → 후보별 판단 → 분류 확정 → 규칙 재사용 저장**을 각각 수행합니다.
5. 피드백으로 다시 요청하면 새 작업/개정을 만들고 과거 결과는 보존합니다. sanity PASS/FAIL 검토는 기존 대장에서 별도로 합니다.

같은 scope의 확정 규칙은 다음 run에 재사용합니다. 후보 비교는 현재 정확한 필드 대조이며 확률·공통 원인을 추론한 결과가 아닙니다.
기존 FAIL 기록을 포함하는 확정 그룹을 후보 대상으로 삼고, 한 scope의 비교 그룹이 100개를 넘으면 `CATALOG_LIMIT`로 명시적으로 중단합니다.
이 legacy 경로의 AI 표본 수와 전체 적용 건수는 별도입니다. 새 파서 자동 생성·의미 기반 검색·대규모 성능 보증은 후속 범위입니다. 임의 구성원 이동/병합·분리 UI는 별도 workbench에서 제공합니다.

## 7. 합성 demo와 검증

사내 DB 없이 **새 로컬 mock DB를 생성하고 빠른 기본 검사**를 하려면 다음을 실행합니다.
Linux/Unix와 PostgreSQL 12.1 이상 서버 바이너리(`initdb`, `pg_ctl`, `postgres`), Python 의존성이 필요합니다.
바이너리 경로가 PATH에 없으면 `--pg-bin /path/to/postgresql/bin`을 추가합니다. 자동 다운로드/설치는 하지 않습니다.

```bash
.venv/bin/python db/tests/run_mock.py
# 전체 회귀·포털 전체 단계·10만 건 검사는 필요할 때 별도로 실행
.venv/bin/python db/tests/run_mock.py --profile full
```

이 mock은 SQLite/가짜 SQL 응답이 아니라 **실제 PostgreSQL 엔진 + 합성 report + 모의 AI 응답**입니다.
매번 별도 `/tmp/sanity-mock-pg-*`에 새 DB를 만들고, TCP 수신 없이 전용 로컬 소켓·SCRAM 인증을 사용합니다.
VWP/OA 로그인은 서로 다르며 임시 비밀번호를 자동 생성합니다. root 실행 시 PostgreSQL만 `nobody`로 실행합니다.
코드/SQL만 임시 폴더에 복사하고 접속 관련 환경변수를 제거하므로 원래 `.env`·사내 접속 문서·실제 report는 사용하지 않습니다.
기존 form → `002`~`009` → SQL smoke → 양쪽 접속/권한 검사는 모든 프로필에서 수행합니다.
기본 `quick`은 인증·파서·권한·핵심 분류/판정 회귀, complete CLI demo 하나,
100건/10그룹 파싱·저장 검사를 실행합니다. `full`은 기존 전체 테스트와 포털 전체 단계 반복 검증,
10,000/100,000건 보고서·그룹 사례, 4종 CLI demo, 10,000건/100그룹 규모 검사를 포함합니다.
선택한 프로필과 실제 실행 건수를 stdout·`tests.json`·`summary.json`에 기록합니다.
`quick` 성공은 선택한 기본 검사의 성공이며 전체 검증 성공을 뜻하지 않습니다.
`--rows`, `--groups`는 마지막 파싱·저장 검사 규모만 조정하며 기존 대용량 테스트의 입력·기대값은 바꾸지 않습니다.
모의 AI는 분류 회귀 테스트에서만 사용합니다. 파싱·저장 규모 검사는 AI 품질 검증이 아닙니다.
정상 완료와 일반 오류 시 DB 프로세스를 종료하고 결과 경로를 출력합니다. `summary.json`, `tests.json`,
`tests.log`, `scale.json`, demo별 보고서와 정지된 DB는 조사할 수 있도록 남기며 자동 삭제하지 않습니다.
강제 종료/전원 차단 시에는 자동 정지를 보장하지 않습니다. 이 도구는 운영 DB 접속 실패 시의 자동 대체 경로가 아닙니다.

CLI demo는 준비된 별도 테스트 DB와 양쪽 제한된 앱 DSN이 필요하며 UI 메모리 demo와 다릅니다.

```bash
.venv/bin/python devtools/demo.py --out demo-output-postgresql
.venv/bin/python devtools/demo.py --scenario missing --out demo-output-postgresql-missing
.venv/bin/python run_tests.py
# 별도 sanity_test_* DB의 admin/VWP/OA DSN과 postgresql 모드를 설정한 뒤
.venv/bin/python run_tests.py --require-db
# 전체 검사 또는 실행 대상 확인(목록만 출력)
.venv/bin/python run_tests.py --profile full --require-db
.venv/bin/python run_tests.py --profile full --list
```

CLI demo는 complete/missing/malformed/unsupported를 지원합니다.
새 complete 실행은 expected=3, approved_na=1, unresolved=2, review=INCOMPLETE, check=FAIL입니다.
동일 DB의 동일 번들은 기존 검토 이력을 유지합니다. AI는 NOT_CONFIGURED입니다.
테스트는 자신이 만든 임시 스키마만 정리하며 운영 DB를 초기화하지 않습니다.
DB 테스트 skip은 통합 검증 성공이 아니고 --require-db는 조건 누락 시 실패합니다.
UI 검증 명령과 실제 실행 결과는 [OA 안내](oa/README.md)와 [REVIEW.md](REVIEW.md)를 확인합니다.

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
