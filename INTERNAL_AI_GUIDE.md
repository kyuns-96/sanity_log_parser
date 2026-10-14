# 내부 AI 작업 범위와 디버깅 문의 규칙

## 1. 역할과 기본 원칙

사용자 결정: **내부 AI는 사내 환경 연결과 확인된 item별 report 추출 어댑터를 제한적으로 구현하고, 디버깅은 이 프로젝트의 설계·검토 담당 Codex에게 문의한다.**
여기서 Codex는 사용자와 이 프로젝트를 설계한 현재 대화의 담당자다. 내부 AI가 스스로 검토 담당 역할을 선언해 수정 범위를 넓히지 않는다.
운영 중 report를 분석하는 VWP AI와, 사내 코드를 작성하는 내부 AI는 서로 다른 역할이다.
실제 report는 보안상 사내 AI/담당자가 사내에서만 열람·검증한다. Codex에는 원문을 보내지 않고 합성 재현 입력·기대값·비식별 코드만 전달한다.

- 아래 허용 목록에 없는 파일은 기본적으로 읽기 전용이다. 미구현 기능이 있다는 사실은 수정 허가가 아니다.
- 사내에서만 확인 가능한 사실·SDK 호출·실제 형식 대응은 내부 AI가 준비한다. 공통 알고리즘·저장 계약·권한 정책은 Codex가 설계·구현·검토한다.
- 처음 구현한 어댑터를 포함해 **예상하지 못한 오류나 테스트 실패가 발생하면 수정 시도를 멈춘다.** 자료 수집 후 6절 형식으로 문의한다.
- 기존 변경은 다른 작업자의 작업일 수 있다. 덮어쓰기·원복·전체 파일 재작성·일괄 포맷을 하지 않는다.
- 이 문서는 에이전트 작업 지침이다. 파일시스템 권한이나 Git 병합을 기술적으로 강제하는 장치는 아니다. 병합·배포는 사용자/Codex 검토를 거친다.

## 2. 내부 AI가 맡는 사내 연동 작업

아래의 **필수**는 해당 기능을 사내에서 사용하기 위해 현장 구현/확인이 필요하다는 뜻이다. 필요한 정보가 없으면 추정해서 완성하지 않고 대기한다.

| 작업 | 담당 환경·필수 여부 | 내부 AI의 산출물 | 완료 조건 / 중단 경계 |
|---|---|---|---|
| SSO 인증 검증 어댑터 | OA, SSO 운영에 필수 | `CorporateIdentityProvider.authenticate`에서 승인된 SDK로 검증 후 `VerifiedIdentity` 반환 | 성공·위조·만료·잘못된 발급자/대상·서비스 장애를 검증. JWT decode/헤더 읽기만으로 성공시키지 않음 |
| 영속 사용자 연결 조회 | OA, SSO 운영에 필수 | `CorporateUserDirectory.resolve`에서 승인된 저장소의 `(issuer, subject) → user_id`와 활성 상태 조회 | 동명이인/이름 변경/미연결/비활성/장애 구분. 승인된 영속 매핑 저장소가 없으면 저장 설계를 Codex에게 문의 |
| 회사 SDK 클라이언트·인증 경로 | OA, 실제 SSO 방식에 따라 필요 | 클라이언트 초기화, 필요 시 `/auth` 로그인·콜백·로그아웃 router, 어댑터 주입 | 실제 회사 문서에 근거. 쿠키/콜백 검증 필요 조건 포함. 기존 업무 API·UI·보안 middleware 변경은 Codex 담당 |
| 실제 sanity 예상 목록 연결 | VWP, 실제 report 처리에 필수 | 실행 설정/기준 목록에서 기존 `plan.json` 계약을 만드는 별도 입력 어댑터 | 생성된 파일 목록으로 예상 목록을 역추정하지 않음. 누락·실패·적용 제외와 출처/버전을 보존. 목록 확정은 담당자 확인 필요 |
| item별 report 추출 어댑터·정답 사례 | VWP, 실제 parser 지원에 필수 | `ReportParser.parse` 구현, 명시적 registry 등록, 전용 테스트와 사내 비공개 기대값 검증 | [PARSER_INTEGRATION.md](vwp-bridge/PARSER_INTEGRATION.md)의 고정 계약 안에서만 구현. 임계값·단위·동일성 규칙 추정 금지. 공통 엔진/판정/등록 인터페이스는 Codex 담당. 실제 원문은 외부에 전달하지 않음 |
| 프로젝트·블록·버전 필드 대응 | VWP/OA, 운영 버전 화면에 필요 | 원천 필드와 project/block/NET_VER/REVISION/PRE·POST/ECO 대응표, 실제 식별/연결 근거 | 현재 전송 계약에 없는 필드는 설계 자료로 제출. payload/DB에 임의 추가하거나 기존 ID를 재해석하지 않음 |
| DB·VWP AI 접속 설정 확인 | 각 실행 환경, 설정 작업 | 승인된 env 값 설정, 버전/지원 API/제한 확인, 정해진 확인 절차 결과 | 접속 드라이버/AI 어댑터는 이미 구현되어 있음. 오류 시 재작성하지 않음. 실제 사내 DB 연결 확인은 사용자가 수행 |

OA의 낮은 성능 AI에 운영 clustering을 맡기지 않는다. 운영 분석은 VWP AI가 수행하고, OA는 사람의 검토·제어 화면이다.
2026-10-14 사용자 요청에 따라 item별 파서의 사내 구현을 허용한다. 범위는 아래 회사 파일의 추출 메서드·명시적 등록·전용 테스트이며, 추상 인터페이스와 검증 기준은 Codex가 작성한 [PARSER_INTEGRATION.md](vwp-bridge/PARSER_INTEGRATION.md)를 따른다. 이 계약에 없는 판정 방식·형식/encoding·한도·전송 필드가 필요하면 먼저 문의한다.

## 3. 수정 가능한 파일 — 명시적 허용 목록

| 위치 | 허용하는 변경 | 허용하지 않는 변경 |
|---|---|---|
| `oa-bridge/corporate_sso.py` | 두 기존 메서드 본문, 필요한 import, SDK/조회 클라이언트 주입용 생성자 | 메서드 서명·반환 모델·오류 코드 변경, 인증 생략, 사용자 자동 등록/권한 자동 부여 |
| `oa-bridge/sso_app.py` | 승인된 회사 모듈 import와 `identity_provider`, `user_directory`, `auth_router` 주입 | `auth_mode="sso"` 제거, 외부 bind, proxy/Host/로그 설정 완화, 기존 API 교체 |
| `oa-bridge/company_sso/` — 신규 작업용 위치 | 위 어댑터를 지원하는 SDK wrapper·인증 router·모의 SDK 테스트만 추가 | 업무 데이터 조회/수정 API, 프로젝트 권한 엔진, DB migration, 공통 모듈 monkey patch |
| `site_integration/corporate_parsers.py` | `CorporateReportParser.parse` 본문, 필요한 import, `create_parser_registry`의 명시적 회사 파서 등록 | 메서드/함수 서명·반환 계약 변경, 자동 import/발견, 내장 파서 교체, 공통 판정 우회 |
| `site_integration/company_parsers/` — 신규 작업용 위치 | 확인된 형식의 `ReportParser` 하위 클래스·전용 테스트 추가 | DB/AI/네트워크/shell 호출, 다른 report 읽기, 공통 모듈 monkey patch, 원문/객체명/비밀값 하드코딩 |
| `site_integration/` — 신규 작업용 위치 | 기존 `plan.json` 생성용 별도 VWP 입력 어댑터, 합성 fixture, 추출 기대값, 대응표, 해당 어댑터 테스트 | 공통 parser/판정/군집 엔진 대체, DB 직접 쓰기, 전송 계약 변경, 새 sanity 실행/설계 파일 변경 |
| 각 서버의 비공개 `.env`/실행 환경 | 사용자가 허용한 실제 값과 기존 지원 키 설정 | 파일 내용을 대화/로그/Git에 출력, VWP 자격증명을 OA로 복사, 비밀을 `VITE_*`에 넣기 |
| 로컬 `artifacts/` 또는 승인된 비공개 작업 폴더 | 진단 메모, 비식별 fixture, 실행 결과 | 원문 로그·비밀값·사내 SDK/접속 문서의 무검토 외부 반출 |

신규 작업용 위치는 자동 발견되는 plugin 경로가 아니다. 회사 파서는 `create_parser_registry`에서 명시적으로 등록하고 기존 `vwp-bridge/corporate_analyze.py`로 주입한다. 이 연결과 공통 검증은 이미 제공되어 있으므로 내부 AI가 공통 파일을 수정하지 않는다.
`sanity_parsers.py`, `vwp-bridge/corporate_analyze.py`, `site_integration/parser_example.py`, `site_integration/__init__.py`는 읽기 전용이다. 같은 `site_integration/` 아래라는 이유로 이 고정 scaffold를 수정하지 않는다. 회사 파서 구현 완료와 사용자/Codex 검토·운영 적용 완료는 구분한다.
새 SDK 의존성·환경변수 키가 필요하면 이름/용도/버전을 먼저 문의한다. `requirements.txt`, lockfile, `sanity_config.py`를 임의 수정하지 않는다.
기존 사내 SDK로 충분한 경우에도 확인된 문서에 근거해 구현하며 인증·CSRF·TLS 조건을 추정하지 않는다.
자격증명·실제 사내 사례는 이 공개 저장소에 넣지 않는다. 문서/fixture는 공개 가능 여부를 별도로 확인한다.

## 4. 내부 AI가 절대 수정하지 않는 부분

읽고 설명하거나 검토 요청을 만드는 것은 가능하지만, 수정·우회·테스트 완화는 금지한다. 허용 목록의 부분 변경 외에는 모두 보호 대상이다.

| 보호 대상 | 대표 파일·위치 | 보호하는 계약 |
|---|---|---|
| 원본·판정·coverage·ID·무결성 | `sanity_common.py`, `sanity_evidence.py`, `sanity_parsers.py`, `vwp-dev/main.py`, `vwp-bridge/main.py`, `vwp-bridge/corporate_analyze.py` | 누락/UNKNOWN/FAIL 처리, Decimal 판정, parser 입력/출력 검사·등록 계약, scope·해시·전체 건수, 원문 위치·봉인 |
| DB 연결·스키마·권한·큐 | `sanity_db.py`, `sanity_postgres.py`, `db/` 전체 | 4개 고정 테이블, 제한된 로그인, SQL 제약/인덱스, 트랜잭션·잠금·lease·중복 방지 |
| 전송·추가 근거·비밀정보 | `sanity_exchange.py`, `sanity_privacy.py` | DB만 사용하는 통신, 근거 범위·TTL·해시·마스킹, 불변 응답 |
| AI 호출·분류·규칙·작업자 | `sanity_ai.py`, `sanity_clustering.py`, `sanity_cluster_worker.py`, `sanity_cluster_store.py`, `sanity_workers.py` | strict JSON·근거 검증, scope, 전체 적용, 중단 복구, 불확실 호출 재시도 금지, 사람 확정 |
| 검토·보고서·인증·업무 API | `sanity_oa_store.py`, `sanity_auth.py`, `oa-bridge/main.py`, `oa-bridge/dashboard_api.py` | 검토 개정·불변 이력·서버 검토자, 신원/권한 검사, API 계약 |
| 대시보드 전체 | `oa-bridge/dashboard/` | 로그인·권한·사용자 범위, 데모와 실데이터 분리, 분류/검토/PoR의 의미 |
| 설정·의존성·기존 테스트 | `sanity_config.py`, `sanity_dbaas.py`, `.env.example`, `requirements.txt`, 모든 기존 test/runner와 lockfile | 설정/실패 처리, 재현성, 기존 테스트 기대값과 skip 조건 |
| 작업 지침·명세·설치 문서 | `AGENTS.md`, 이 문서, `requirement.md`, `PROTOCOL.md`, 기존 README/인수인계/검토 문서, `.gitignore` | 작업 범위·업무 기준·완료 조건. 문서 변경으로 권한을 스스로 확대하지 않음 |

특히 다음 변경은 하지 않는다.

1. `sanity_vwp_data`, `sanity_oa_data`, `sanity_vwp_to_oa`, `sanity_oa_to_vwp` 이름 변경 또는 추가 테이블 임의 생성.
2. DB 오류 해결을 위한 권한 검사 삭제, 관리자 계정 사용, 광범위 GRANT, CHECK/FK/UNIQUE 삭제, `DELETE`/`TRUNCATE`, 임의 migration 실행.
3. `AUTHORIZATION_NOT_IMPLEMENTED` 제거, `authorization_ready`/`writable` 강제 true, SSO 실패 시 로컬 토큰/데모 사용자 fallback.
4. 미지원 report를 PASS로 처리, 누락 item 삭제, 로그 앞부분만 처리하고 전체 완료 표시, parser가 원본 위치/해시를 잃도록 임의 변환.
5. code/severity만으로 같은 문제 확정, 다른 scope 자동 병합, AI 제안을 사람 확정으로 기록, 원래 PASS/FAIL·봉인 자료 덮어쓰기.
6. 브리지 간 HTTP/RPC/공유 파일 전송, SQLite나 별도 임시 DB를 운영 fallback으로 추가, Jira 연동 부활.
7. TLS 검증 끄기, 모든 Host/CORS 허용, 인증 header 무조건 신뢰, shell 실행 추가, 예외 무시, 테스트 삭제/skip/기대값 완화.
8. UI 데모의 프로젝트·PoR·사용자 데이터를 실제 DB/SSO 결과로 취급하거나 운영 권한 필터 대신 브라우저 필터만 적용.

프로젝트·블록 RBAC, 영속 관리 레코드/PoR 저장, 새 버전 전송 계약, 공통 parser/판정/등록 엔진 확장, UI SSO 연결은 **남은 작업이지만 내부 AI의 수정 허용 영역이 아니다**. Codex가 별도 구현한다. 회사 파서 등록은 위 허용 파일의 고정 인터페이스 사용만 허용한다.
DBA의 승인된 설치 SQL 실행과 실제 운영 배포는 사용자가 관리하며, 이 문서가 그 실행 권한을 내부 AI에게 자동 부여하지 않는다.

## 5. 작업·검증·인계 순서

1. 이 문서 → `requirement.md`의 관련 절 → `PROTOCOL.md` → 해당 연동 문서를 읽는다. SSO는 `oa-bridge/SSO_INTEGRATION.md`, report 파서는 `vwp-bridge/PARSER_INTEGRATION.md`도 읽는다.
2. 작업 시작 시 `git status --short`로 기존 변경을 기록한다. 내부 AI가 새로 수정할 파일·메서드와 근거 문서를 짧게 제시한다. 범위가 불명확하면 먼저 문의한다.
3. 사내 사실을 확인하고 한 번에 어댑터 하나만 구현한다. 예시를 실제 계약으로 추정하거나 관련 없는 리팩터링을 하지 않는다.
4. 새 SDK/입력/report 어댑터 테스트를 추가한다. 기존 테스트를 수정하지 않는다. 파서는 정상·위반·경계값·명시적 0건·빈 파일·잘림·미해석 구간·도구 버전 변경을 검증한다. 합성/모의 검증과 사내 실환경 검증 결과를 구분한다.
5. 오류가 발생하면 6절로 전환한다. 정상인 경우 수정 파일·핵심 diff·실행 명령·통과/실패/skip·남은 항목을 인계한다.
6. Codex 검토와 사용자 승인 전에는 운영 DB 변경·인증 범위 공개·배포·push/강제 push를 하지 않는다. `.env`와 비공개 자료는 diff 첨부에서 제외한다.

기존 계약 확인 명령(저장소 루트):

```bash
.venv/bin/python -m unittest discover -s tests -p test_auth.py -v
.venv/bin/python -m unittest discover -s oa-bridge -p test_sso.py -v
.venv/bin/python -m unittest discover -s tests -p test_parsers.py -v
.venv/bin/python db/tests/run_mock.py --pg-bin /path/to/postgresql/bin
git diff --check
```

마지막 DB 명령은 개발 머신에 PostgreSQL 16+ 서버 바이너리가 준비된 경우의 별도 mock 검증이다. 사내 실제 DB에 실행하는 명령이 아니다.
`oa-bridge/company_sso/` 또는 `site_integration/`에 새 테스트를 만들었다면 그 디렉터리도 별도로 테스트한다. 회사 파서 테스트는 작성 후 `.venv/bin/python -m unittest discover -s site_integration/company_parsers -p 'test_*.py' -v`로 수집한다. 기존 전체 runner/mock이 회사 구현·비공개 fixture를 자동 수집한다고 가정하지 않는다.
SSO 어댑터 성공은 업무 API 공개 조건이 아니다. 현재 SSO 업무 API의 `503 AUTHORIZATION_NOT_IMPLEMENTED`는 의도된 보호 상태다.
미구현 상태의 `AUTH_SSO_NOT_CONFIGURED`도 숨기지 않는다. 구현 후 예상하지 못한 같은 오류가 계속되면 문의한다.
test skip, health 응답, 화면 렌더링, DB 연결 성공만으로 전체 연동 완료를 선언하지 않는다.

## 6. 디버깅이 필요하면 Codex에게 문의

테스트 실패, 예상 밖 예외/응답, DB 연결/권한/스키마 불일치, API 호환 오류, 해시·coverage 불일치, 보호 파일 수정 필요 시 즉시 해당 작업을 멈춘다.
허용 파일 안에서 난 오류라도 혼자 수정하며 시행착오를 반복하지 않는다. 남은 무관한 작업으로 실패를 덮거나 완료 처리하지 않는다.

허용되는 진단은 이미 나온 오류·변경 diff 확인, 설치 버전 확인, 문서/설정 **키의 존재 여부** 확인 등 읽기 전용 자료 수집이다.
같은 쓰기/AI 요청 반복, 무작정 worker 재시작, 토큰/권한/TLS 변경, queue 상태 수동 변경, DB 전체 dump는 진단 수단으로 사용하지 않는다.
순수 읽기 전용 재현도 횟수·범위를 제한한다. 운영 쓰기·외부 AI 호출을 재현해야 한다면 먼저 문의한다.

다음 내용을 비식별화해 작성하고 상태를 `WAITING_FOR_CODEX`로 남긴다.

```text
[Codex 디버깅 요청]
작업 ID / 환경: VWP 또는 OA / mock 또는 사내 검증
작업 목표와 실패 단계:
기준 commit / 작업 시작부터 있던 변경 / 이번에 수정한 파일:
Python·psycopg2·PostgreSQL·회사 SDK 버전(확인 가능한 항목만):
실행 명령: 값은 숨기고 env 키·인자 형태만 표시
기대 결과 / 실제 결과 / 오류 코드 / 발생 시각·횟수:
관련 diff: 이번 작업 부분만, 비밀값 제거
최소 재현 입력: 합성 예제와 기대값, 필요한 구조·자료형 유지
진단 자료: 비식별 stack의 함수·줄 번호, 통과/실패/skip 수
DB/AI 작업 상태: 미실행·읽기만·쓰기 시도·commit 확인·결과 불명 중 무엇인지
문의: Codex가 결정해야 할 사항 1~3개
현재 상태: WAITING_FOR_CODEX — 수정·재시도·배포 중단
```

비밀번호·토큰·쿠키·DSN·내부 주소·개인정보·전체 인증 claim·원문 SDK 예외는 보내지 않는다. 값의 타입/길이/존재 여부와 비밀 제거된 오류 코드면 충분한지 먼저 확인한다.
report parser 문의에는 parser ID·implementation_version·판정 방식·입력 크기/줄 수·기대/실제 건수·합성 원문 위치를 추가하고 실제 report는 첨부하지 않는다. 실제 원문/업무 의미의 검증은 사내 담당자가 맡으며 Codex의 합성 검토를 실데이터 정확성 검증으로 표시하지 않는다.
합성 입력으로 재현되지 않으면 그 사실을 알리고 허용된 자료 범위를 문의한다. 고객 report 조회 허용이 외부 대화에 모든 원문을 보낼 허가를 의미하지 않는다.
현재 이 저장소에는 내부 AI가 이 대화의 Codex에게 자동 문의하는 통신 경로가 없다. 연결 도구가 별도로 제공되지 않으면 **사용자가 위 요청문을 이 대화로 전달**한다. 답변을 받지 않았으면 승인된 것으로 간주하지 않는다.
Codex 답변이 오면 해당 작업의 허용 파일·지시 범위에서만 진행한다. 보호 파일의 변경은 내부 AI가 직접 하지 않고 Codex 수정본을 사용자에게 전달받는 절차로 처리한다.

## 7. 내부 AI에게 그대로 전달할 지시문

> 먼저 저장소 루트 `AGENTS.md`와 `INTERNAL_AI_GUIDE.md`를 읽어라. 너의 역할은 사내 연동 구현자다. 허용 목록의 회사 어댑터·입력 매핑·item별 report 추출/명시적 등록·전용 테스트만 작성하고 공통 코드·DB SQL·판정/coverage·권한 검사·UI·기존 테스트는 수정하지 말라. report 작업은 `vwp-bridge/PARSER_INTEGRATION.md`를 따르고 실제 원문은 사내에서만 검증하며 외부 Codex 문의에는 합성 입력만 사용하라. 실제 SDK와 사내 자료로 확인한 사실만 사용하라. 예상하지 못한 오류가 발생하거나 보호 파일 변경이 필요하면 수정/재시도를 멈추고 `[Codex 디버깅 요청]`을 비식별화해 작성하라. 직접 문의 도구가 없으면 사용자에게 이 대화로 전달해 달라고 요청하고 `WAITING_FOR_CODEX`로 대기하라. SSO 업무 API의 의도된 차단이나 미완료 sanity 상태를 성공으로 바꾸지 말라. 작업 완료와 운영 적용 승인을 구분하라.
