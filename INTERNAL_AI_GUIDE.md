# 내부 AI 작업 범위와 디버깅 문의 규칙

## 1. 역할과 기본 원칙

사용자 결정: **내부 AI는 사내 환경 연결과 확인된 item별 report 추출 어댑터를 제한적으로 구현하고, 디버깅은 이 프로젝트의 설계·검토 담당 Codex에게 문의한다.**
여기서 Codex는 사용자와 이 프로젝트를 설계한 현재 대화의 담당자다. 내부 AI가 스스로 검토 담당 역할을 선언해 수정 범위를 넓히지 않는다.
운영 중 report를 분석하는 VWP AI와, 사내 코드를 작성하는 내부 AI는 서로 다른 역할이다.
실제 report는 보안상 사내 AI/담당자가 사내에서만 열람·검증한다. Codex에는 원문을 보내지 않고 합성 재현 입력·기대값·비식별 코드만 전달한다.

2026-10-16 사용자 요청의 통합 구현 지도는 [INTERNAL_AI_HANDOFF.md](INTERNAL_AI_HANDOFF.md)다. INT-01~06별 선행 정보·허용 메서드·수용 기준과 Codex의 COMMON 후속 작업을 구분한다. 이 가이드와 함께 읽으며, SDK 어댑터 검증을 운영 연결 완료로 보고하지 않는다. 현재 검토 UI와 업무 용어는 유지한다.

사내 개발 AI는 **VWP·VWP-bridge·OA 세 담당**으로 나눈다. 작업 시작 전에 아래 역할을 확인하고 자기 [역할별 지시문](INTERNAL_AI_HANDOFF.md#21-세-개발-ai의-작업-소유권)을 따른다. 운영 분석 모델을 VWP AI라고 부르는 것과 개발 AI의 소유권은 별개다. VWP 원천 수집에는 DB 계정이 없고, 두 bridge만 같은 DB에 연결한다. 기본은 별도 제한 로그인이며 명시적 단일 계정 모드는 3.4절을 따른다. 설정과 역할별 점검은 [DB 연결 인계](db/CONNECTION_HANDOFF.md)를 따른다.

코드는 [서버별 소스·실행 안내](DEPLOYMENT.md)의 `vwp/`, `vwp_bridge/`, `oa/`, `common/`, `devtools/`로 분리한다. 각 서버는 Git checkout의 저장소 루트에서 자기 역할 모듈을 실행한다. 공통 소스는 한 곳에서 관리하며 회사 SDK 때문에 복사하거나 의존성을 임의 추가하지 않는다. 디렉터리 분리가 내부 AI의 수정 허용 범위를 넓히지 않는다.

2026-10-16 사용자 요청으로 Q1의 [분산 수집·PRE/POST 설계](vwp/SOURCE_COLLECTION_DESIGN.md)를 반영하고 별도 V2 공통 제출 경로를 구현했다. `get_plan()`의 파일 쓰기 금지·기존 schema 1·미승인 목록 차단은 유지한다. V2 원천/버전 검증·논리 중복·분할 수신과 workspace 연결은 제공했으며, 실제 회사 원천·신원/승인 SDK는 아래 회사 지점에서 연결한다.

같은 날 [제출 명령·저장 경로](vwp/SUBMISSION_DESIGN.md)의 `sanity submit/status/retry`, VWP 내부 spool/bridge 영속 보관·DB 접수, OA 원자 반영·응답/receipt를 구현했다. 실행·설정·SDK 계약은 [SUBMISSION.md](vwp/SUBMISSION.md)를 따른다. **사내 파일시스템/ACL·실제 SDK·운영 DB는 미연결**이다. 사내 AI는 아래 회사 메서드와 현장 자료만 맡으며 CLI·작업자·전송·설정 계약·SQL/API를 재구현하지 않는다. VWP↔OA DB-only와 사용자 DB 계정 없음은 유지한다.

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
| 실제 sanity 예상 목록 연결 | VWP 원천 담당, 실제 report 처리에 필수 | 실행 설정/기준 목록 또는 승인된 범위 선언 입력을 기존 `plan.json`으로 반환 | [PLAN_INTEGRATION.md](vwp/PLAN_INTEGRATION.md)와 Q1 설계를 따름. 스캔은 후보 발견이며 자동 승인 아님. 누락·실패·적용 제외·출처/개정과 실제 승인 근거 보존 |
| item별 report 추출 어댑터·정답 사례 | VWP-bridge 담당, 실제 parser 지원에 필수 | VWP 담당자에게 형식/의미/정답을 받아 `ReportParser.parse` 구현, 명시적 registry 등록, 전용 테스트와 사내 비공개 기대값 검증 | [PARSER_INTEGRATION.md](vwp_bridge/PARSER_INTEGRATION.md)의 고정 계약 안에서만 구현. 임계값·단위·동일성 규칙 추정 금지. 공통 엔진/판정/등록 인터페이스는 Codex 담당. 실제 원문은 외부에 전달하지 않음 |
| 프로젝트·블록·버전/담당 필드 대응 | VWP 원천/OA 담당, 운영 버전 화면에 필요 | VWP는 실행·버전·report, OA는 사용자·담당 배정의 원천 대응표와 안정 ID 근거 | 현재 전송 계약에 없는 필드는 설계 자료로 제출. payload/DB에 임의 추가하거나 기존 ID를 재해석하지 않음 |
| DB·VWP AI 접속 설정 확인 | VWP-bridge/OA 담당, 설정 작업 | 자기 서버의 승인된 env 값, 버전/지원 API/제한과 정해진 확인 절차 결과 | DB 드라이버와 legacy AI 접속 어댑터는 제공됨. 새 SDK 주입/운영 job 연결과 구분. 오류 시 재작성하지 않음. 실제 사내 DB 연결 확인은 사용자가 수행 |

OA의 낮은 성능 AI에 운영 clustering을 맡기지 않는다. 운영 분석은 VWP AI가 수행하고, OA는 사람의 검토·제어 화면이다.
2026-10-14 사용자 요청에 따라 item별 파서의 사내 구현을 허용한다. 범위는 아래 회사 파일의 추출 메서드·명시적 등록·전용 테스트이며, 추상 인터페이스와 검증 기준은 Codex가 작성한 [PARSER_INTEGRATION.md](vwp_bridge/PARSER_INTEGRATION.md)를 따른다. 이 계약에 없는 판정 방식·형식/encoding·한도·전송 필드가 필요하면 먼저 문의한다.

## 3. 수정 가능한 파일 — 명시적 허용 목록

아래 파일 허용 목록은 세 역할의 **합집합**이다. 각 AI는 다음 소유권과 허용 메서드를 동시에 만족하는 부분만 수정한다. 담당 역할이 명시되지 않았으면 먼저 사용자에게 배정을 확인하며 다른 역할의 미완료 작업을 대신 가져가지 않는다.

| 담당 | 자기 수정 위치/책임 | 개별 지시문 |
|---|---|---|
| VWP | `vwp/integration/corporate_plan.py`, `corporate_submission.py`, `company_plan/`; 실행·예상 목록·버전/report·제출자/승인 SDK. DB 계정 없음 | [VWP 작업 지시문](vwp/INTERNAL_AI_TASK.md) |
| VWP-bridge | `vwp_bridge/integration/`의 회사 parser·clustering·review·submission과 각 허용 `company_*/`; 인증된 마운트 신원·자기 VWP 접속 설정 | [VWP-bridge 작업 지시문](vwp_bridge/INTERNAL_AI_TASK.md) |
| OA | `oa/integration/corporate_sso.py`, `corporate_submission.py`, `oa/sso_app.py`, `company_sso/`; 사용자/담당 조회·동일 계정 매핑·자기 OA 접속 설정 | [OA 작업 지시문](oa/INTERNAL_AI_TASK.md) |

회사 구현은 **자기 서버의 `integration/`** 아래다. VWP는 `vwp/integration/`, VWP-bridge는 `vwp_bridge/integration/`, OA는 `oa/integration/`만 사용한다. 원천 대응표·신규 보조 자료도 자기 `company_*/`에 추가한다. 비공개 진단은 `artifacts/internal-handoff/vwp/`, `vwp_bridge/`, `oa/` 또는 승인된 사내 폴더에 남긴다. 다른 담당 파일과 공통 코드·SQL·UI를 수정하지 않는다.

| 위치 | 허용하는 변경 | 허용하지 않는 변경 |
|---|---|---|
| `oa/integration/corporate_sso.py` | 두 기존 메서드 본문, 필요한 import, SDK/조회 클라이언트 주입용 생성자 | 메서드 서명·반환 모델·오류 코드 변경, 인증 생략, 사용자 자동 등록/권한 자동 부여 |
| `oa/integration/corporate_submission.py` | `CorporateSourceIdentity.resolve` 본문, 필요한 import·승인된 조회 client 주입 생성자. OA SSO와 같은 영속 `(issuer, subject) → user_id` 매핑 사용 | 임의 actor 문자열 신뢰, 사용자 자동 등록, importer/SSO 보호 코드 수정·해제 |
| `oa/sso_app.py` | 승인된 회사 모듈 import와 `identity_provider`, `user_directory`, `auth_router` 주입 | `auth_mode="sso"` 제거, 외부 bind, proxy/Host/로그 설정 완화, 기존 API 교체 |
| `oa/integration/company_sso/` — 신규 작업용 위치 | SSO/source 동일 계정 조회를 지원하는 SDK wrapper·인증 router·모의 SDK 테스트만 추가 | 업무 데이터 조회/수정 API, 프로젝트 권한 엔진, DB migration, 공통 모듈 monkey patch |
| `oa/requirements.txt` — 3.1절의 승인된 SSO 의존성만 | OA 담당이 `signxml==5.2.0`, `lxml==6.1.3`, `cryptography==50.0.2`를 없을 때 추가 | 다른 의존성 추가·삭제·버전 변경, 루트/VWP/VWP-bridge 의존성·lockfile·Docker 수정 |
| `vwp/integration/corporate_plan.py` | `CorporatePlanProvider.get_plan` 본문, 필요한 import, 승인된 실행/예상 목록 조회 client 주입용 생성자 | 추상 서명/plan schema 변경, 목록 승인 자동 부여, report/설계 변경·sanity 실행·DB 쓰기·공통 CLI 수정 |
| `vwp/integration/corporate_submission.py` | `CorporateSubmissionInput.prepare`, `CorporateSubmissionAuthority.current/inbox_owner/authorize/approval_valid` 본문, 필요한 import·승인 SDK 주입 생성자, `create_submission_adapters`의 명시적 주입 | 서명/모델·V2 schema 변경, 파일/DB 쓰기·실행, 자동 승인, 사용자명만으로 인증, CLI/상태/권한 우회 |
| `vwp/integration/company_plan/` — 신규 작업용 위치 | schema 1/V2 입력·원천/제출 신원/담당·승인 SDK wrapper, 기존 plan 작성·VWP 내부 byte 동일 export/collector 호출용 별도 launcher·전용 합성/모의 테스트 | 파일 목록으로 예상 unit 누락 은폐·자동 승인, 새 전송/DB 계약·worker 연결, 원본 report/설계 파일 변경 |
| `vwp_bridge/integration/corporate_parsers.py` | `CorporateReportParser.parse` 본문, 필요한 import, `create_parser_registry`의 명시적 회사 파서 등록 | 메서드/함수 서명·반환 계약 변경, 자동 import/발견, 내장 파서 교체, 공통 판정 우회 |
| `vwp_bridge/integration/corporate_submission.py` | `CorporateSubmissionAuthority.current/inbox_owner/authorize/approval_valid` 본문, 필요한 import·승인 SDK 주입 생성자 | 디렉터리명/파일 actor만으로 인증, 자동 승인, 공통 archive/spool/DB·worker 변경 |
| `vwp_bridge/integration/company_submission/` — 신규 작업용 위치 | 위 bridge 신원·배정·승인 어댑터의 승인 SDK wrapper·합성/모의 SDK 전용 테스트 | 독자 접수/전송 worker, DB 직접 쓰기, 권한/실패 우회, 실제 사내 자료 하드코딩 |
| `vwp_bridge/integration/company_parsers/` — 신규 작업용 위치 | 확인된 형식의 `ReportParser` 하위 클래스·전용 테스트 추가 | DB/AI/네트워크/shell 호출, 다른 report 읽기, 공통 모듈 monkey patch, 원문/객체명/비밀값 하드코딩 |
| `vwp_bridge/integration/corporate_review.py` | `CorporateReviewAdvisor.propose` 본문, 필요한 import, 승인된 VWP AI SDK 주입용 생성자 | 메서드/출력 계약 변경, OA 모델 호출, DB/전송/worker 연결 변경, 판정/재사용 기준 자동 확정 |
| `vwp_bridge/integration/company_review/` — 신규 작업용 위치 | 위 어댑터를 지원하는 SDK wrapper·합성/모의 SDK 전용 테스트 | 공통 AI/저장 모듈 대체, DB·파일·다른 report 조회, shell 실행, 운영 재시도/상태 우회 |
| `vwp_bridge/integration/corporate_clustering.py` | `CorporateClusteringAdvisor.propose` 본문, 필요한 import, 승인된 VWP SDK 주입용 생성자 | 분류/판정/출력 계약 변경, OA 모델 호출, 공통 DB/worker/API 연결, 그룹 자동 확정 |
| `vwp_bridge/integration/company_clustering/` — 신규 작업용 위치 | 위 어댑터의 SDK wrapper·합성/모의 SDK 전용 테스트 | DB·파일·다른 report 조회, shell 실행, 공통 모듈 변경/monkey patch, 실패의 성공 대체 |
| 자기 역할의 위 `company_*/` — 신규 보조 자료 위치 | 기존 `plan.json` 생성용 별도 VWP 입력 어댑터, 합성 fixture, 추출 기대값, 대응표, 해당 어댑터 테스트 | 소유자 불명확한 root 공용 파일 추가, 다른 담당 파일 변경, 공통 parser/판정/군집 엔진 대체, DB 직접 쓰기, 전송 계약 변경, 새 sanity 실행/설계 파일 변경 |
| 각 서버의 비공개 `.env`/실행 환경 | 사용자가 허용한 실제 값과 기존 지원 키 설정 | 파일 내용을 대화/로그/Git에 출력, VWP 자격증명을 OA로 복사, 비밀을 `VITE_*`에 넣기 |
| 로컬 `artifacts/` 또는 승인된 비공개 작업 폴더 | 진단 메모, 비식별 fixture, 실행 결과 | 원문 로그·비밀값·사내 SDK/접속 문서의 무검토 외부 반출 |

신규 작업용 위치는 자동 발견되는 plugin 경로가 아니다. 회사 파서는 `create_parser_registry`에서 명시적으로 등록하고 기존 `vwp_bridge/corporate_analyze.py`로 주입한다. 이 연결과 공통 검증은 이미 제공되어 있으므로 내부 AI가 공통 파일을 수정하지 않는다.
plan provider도 자동 수집/주입되지 않는다. 허용된 회사 wrapper에서 명시적으로 호출하고 기존 `vwp/main.py:validate_plan`으로 검사한 plan만 기존 collector에 전달한다. 구조 검사 통과는 예상 목록 승인이나 실제 report 완료를 뜻하지 않는다. plan 계약·실제 원천 버전 필드 대응은 [PLAN_INTEGRATION.md](vwp/PLAN_INTEGRATION.md)를 따른다.
PRE/POST의 여러 원천 루트 export는 같은 VWP 영역의 입력 준비만 허용한다. 복사 전후 원본/사본 hash와 완료·경로를 검사하고 누락 unit·예상 밖 report를 보존한다. `get_plan()` 안에서 복사하지 않으며 OA로 파일을 전달하지 않는다. V2의 `prepare()`도 읽기만 하며 원본 Path 대응을 반환하고 공통 CLI가 byte 동일 snapshot을 만든다. 중앙 목록이 없을 때의 승인된 범위 선언서와 스캔 후보를 구분하고, 승인 권한·원천 실행 ID/순서·평가 기준이 확인되지 않으면 `FACTS_REQUIRED`로 인계한다. 제공된 V2 필드·메시지·DB/validator는 내부 AI가 변경하지 않는다.
`common/sanity_parsers.py`, `vwp_bridge/corporate_analyze.py`, `vwp_bridge/integration/parser_example.py`, `vwp_bridge/integration/__init__.py`는 읽기 전용이다. 같은 `integration/` 아래라는 이유로 이 고정 scaffold를 수정하지 않는다. 회사 파서 구현 완료와 사용자/Codex 검토·운영 적용 완료는 구분한다.
공개 형식 참고용 `vwp_bridge/integration/public_parsers.py`와 [PUBLIC_REPORT_FORMATS.md](vwp_bridge/integration/PUBLIC_REPORT_FORMATS.md)도 고정 예제로 읽는다.
사용자 판정 참고 AI 어댑터는 [REVIEW_INTEGRATION.md](vwp_bridge/REVIEW_INTEGRATION.md)를 따른다. 현재는 SDK/모델 미연결 scaffold이며 공통 workspace 제안 엔진은 정확한 조건 적용이다. SDK 구현과 VWP DB 전달/운영 연결은 구분한다. 내부 AI는 후자의 공통 코드를 수정하지 않는다.
1단계 분류 SDK는 [CLUSTERING_INTEGRATION.md](vwp_bridge/CLUSTERING_INTEGRATION.md), 2단계 그룹 판정 SDK는 위 review 계약을 따른다. 두 어댑터는 사내 VWP용이며 workspace의 `CORPORATE` 경로는 아직 미연결이다. 기존 legacy AI 어댑터의 접속 설정 완료와 새 workspace 운영 연결 완료를 혼동하지 않는다.
샘플 registry/완료 표식은 운영 계약이 아니다. 참고 패턴은 허용된 회사 구현 위치에서 실제 버전·종료/건수 계약에 맞춰 구현한다.
새 SDK 의존성·환경변수 키가 필요하면 이름/용도/버전을 먼저 문의한다. 아래 3.1절의 고정 승인 범위 외에는 `requirements.txt`, lockfile, `common/sanity_config.py`를 임의 수정하지 않는다.
기존 사내 SDK로 충분한 경우에도 확인된 문서에 근거해 구현하며 인증·CSRF·TLS 조건을 추정하지 않는다.
자격증명·실제 사내 사례는 이 공개 저장소에 넣지 않는다. 문서/fixture는 공개 가능 여부를 별도로 확인한다.

### 3.1. OA SSO 고정 의존성 추가 승인 — 2026-10-20

사용자 요청과 설계 담당 Codex 검토로 **OA의 SAML/XML 서명 검증용 세 패키지 추가만** 승인했다. OA 담당 내부 AI는 사내 checkout의 `oa/requirements.txt`에 `signxml==5.2.0`, `lxml==6.1.3`, `cryptography==50.0.2`가 없으면 정확히 추가할 수 있다. 이미 같은 pin이 있으면 중복 추가하지 않는다. 기존에 다른 pin이 있거나 설치 충돌이 나면 버전을 바꾸지 않고 `WAITING_FOR_CODEX`로 문의한다.

이것은 파일 전체의 보호 해제가 아니다. 나머지 의존성·환경변수·설정·Docker·공통 코드·기존 테스트 보호와 `AUTHORIZATION_NOT_IMPLEMENTED`는 유지한다. 개발용 루트 `requirements.txt`의 동일 pin 반영은 Codex가 제공하며, VWP와 VWP-bridge의 의존성은 변경하지 않는다. 설치·검증·사내 반영은 [SSO 인계 10절](oa/SSO_INTEGRATION.md#10-승인된-sso-의존성)을 따른다. 패키지 추가 승인은 실제 IdP/SP 검증이나 업무 권한 연결 완료를 뜻하지 않는다.

### 3.2. 빈 초기 DB의 포털 실행본 준비 — 2026-10-21

사용자가 단일 문장 DDL과 FK의 포털 설정 가능 여부를 확인했다. [포털 초기 설치 안내](db/portal/README.md)에 따라 네 테이블 0행·쓰기 중지 상태에서 승인된 읽기 전용 catalog를 비공개 파일로 정리하고 `db/portal/runbook.py`로 실행본 준비·전후 검증을 할 수 있다. 도구는 DB 접속/DDL 실행을 하지 않으며 실제 포털 적용은 사용자/승인 운영 담당자가 수행한다. 실제 스키마·catalog·생성 SQL은 사내에만 남긴다.

PG12.1 호환 수정본은 PG15 전용 catalog 컬럼을 직접 참조하지 않는다. 이 오류로 대기한 경우 수정본으로 새 조회 SQL을 생성해 읽기 전용 확인·실행본 준비를 재개할 수 있다. [PG12 재개 절차](db/portal/README.md#pg12-조회-오류가-있었던-경우)에 따라 새 비공개 경로를 사용하며, snapshot의 버전·계획 hash를 바꾸거나 검증 조건을 제거하지 않는다. 기준 계획은 PG12.1/PG16.15의 54단계 구조가 정확히 같음을 검증해 공통 사용한다. 다른 실패에는 기존 중단·문의 규칙을 유지한다.

`db/form/`, 원본 `002`~`008`, `db/portal/`의 소스·계획 JSON과 기존 테스트는 내부 AI에게 읽기 전용이다. 기본 모드의 `003` 역할·권한은 별도 DBA 경로이며 임의의 전체 읽기/쓰기나 DDL 권한으로 대체하지 않는다. 명시적 단일 계정 모드는 별도 계약인 3.4절을 따른다. `005`는 필수다. 이 초기 3.2절 범위는 009/JIT를 포함하지 않으며 후속 009 저장 구조는 3.3절을 따른다. 사용자 자동 등록 앱 구현·SSO 업무 권한 확장 승인은 포함하지 않는다. 예상 밖 실패·응답 유실 후 DDL 재시도/다음 실행은 중단하고 읽기 전용 현황과 비식별 오류만 `WAITING_FOR_CODEX`로 문의한다.

### 3.3. 009 SSO 계정 저장 구조 — 2026-10-22

후속 사내 확인으로 단일 issuer·LoginId 불변/비재사용·검증된 전사 계정 등록 정책을 [SSO 계정 저장 계약](oa/SSO_IDENTITY.md)에 반영했다. Codex가 원본 `db/009_sso_identity.sql`과 포털 계획의 55번 단일 ALTER를 제공한다. 3.2절의 기존 54단계는 그대로 보존한다. 008 완료·0행·쓰기 중지 DB는 [009 준비 절차](db/portal/README.md#008까지-적용한-db에서-009만-준비)로 새 snapshot/bundle을 준비하고 사용자/승인 운영 담당자가 적용한다. 기존 bundle/hash를 편집하거나 완료 DDL을 반복하지 않는다.

사용자 확인으로 현재는 최초 배포 전이며 앱에 등록된 기존 운영 계정이 없다. 기존 계정 이전·whitelist 승계는 이번 범위의 작업이나 선행 조건이 아니다. 신규 등록·동시 중복·DB/세션 실패 경계와 등록 후 비활성화 차단을 검증한다.

이것은 SQL/설치 절차 제공이며 내부 AI의 파일·메서드 허용 범위를 넓히지 않는다. 원본 009·포털 도구·계획·기존 테스트는 읽기 전용이다. JIT 등록/조회 공통 저장부·ACS 호출/세션 발급 실패 경계는 별도 Codex 후속 구현이며 내부 AI가 독자 작성하지 않는다. `resolve()`와 source 신원 조회는 등록/갱신을 수행하지 않는다. `AUTHORIZATION_NOT_IMPLEMENTED`, 프로젝트/블록 권한의 별도 부여와 오류 시 중단 규칙을 유지한다.

### 3.4. 명시적 단일 DB 계정 모드 — 2026-10-22

사용자 요청과 사내 비식별 권한 실측에 따라 Codex가 `SANITY_DB_ALLOW_SINGLE_ACCOUNT`와 별도 ACL 계약을 제공한다. 기본은 `false`이며 정확한 `true`만 단일 계정 모드를 선택한다. 두 bridge는 같은 공유 DB 로그인을 현장 설정할 수 있고 `sanity_vwp_app`/`sanity_oa_app` 역할은 요구하지 않는다. 원천 VWP의 DB 계정 없음·모델/SSO 비밀 분리는 유지한다. 실패 후 자동 전환하거나 내부 AI가 검사 목록을 삭제하지 않는다.

단일 모드도 네 테이블의 모든 컬럼에 유효 SELECT/INSERT/UPDATE를 요구한다. DELETE는 보유할 수 있지만 앱 삭제 기능은 추가하지 않는다. TRUNCATE/REFERENCES/TRIGGER·관리자 속성/상위 관리자 역할·테이블 소유/소유자 역할·앱 및 public 스키마 CREATE는 계속 금지하며 기존 테이블/필수 UNIQUE 검사를 유지한다. **private 테이블 접근 격리와 큐의 송수신 컬럼 분리는 DB가 강제하지 못한다.** 정상 앱 경로의 불변값·입력 필드·트랜잭션·lease·업무 권한 검사는 그대로다.

설정·모드별 권한 검사·기존 테스트·명세는 Codex 담당이며 회사 AI의 파일/메서드 허용 범위를 확대하지 않는다. 내부 AI는 수정본을 전달받은 뒤 자기 서버의 비공개 설정과 [DB 연결 인계 3.1절](db/CONNECTION_HANDOFF.md#31-단일-계정-모드)에 따른 읽기 전용 점검 결과만 준비한다. 단일 모드의 성공은 `privilege_mode=single_account db_isolation=not_enforced`이며 제한 계정 격리 검증 완료로 보고하지 않는다. 원본 SQL/포털 계획 수정·재실행이나 권한 자동 변경은 필요하지 않다. `AUTHORIZATION_NOT_IMPLEMENTED`와 오류 시 중단·문의 규칙은 유지한다.

## 4. 내부 AI가 절대 수정하지 않는 부분

읽고 설명하거나 검토 요청을 만드는 것은 가능하지만, 수정·우회·테스트 완화는 금지한다. 허용 목록의 부분 변경 외에는 모두 보호 대상이다.

| 보호 대상 | 대표 파일·위치 | 보호하는 계약 |
|---|---|---|
| 원본·판정·coverage·ID·무결성 | `common/sanity_common.py`, `vwp_bridge/sanity_evidence.py`, `common/sanity_parsers.py`, `vwp/main.py`, `vwp_bridge/main.py`, `vwp_bridge/corporate_analyze.py` | 누락/UNKNOWN/FAIL 처리, Decimal 판정, parser 입력/출력 검사·등록 계약, scope·해시·전체 건수, 원문 위치·봉인 |
| DB 연결·스키마·권한·큐 | `common/sanity_db.py`, `common/sanity_postgres.py`, `db/` 전체 | 4개 고정 테이블, 기본 제한 로그인/명시적 단일 계정 계약, SQL 제약/인덱스, 트랜잭션·잠금·lease·중복 방지 |
| 전송·추가 근거·비밀정보 | `common/sanity_exchange.py`, `vwp_bridge/sanity_exchange.py`, `oa/sanity_exchange.py`, `common/sanity_privacy.py` | DB만 사용하는 통신, 근거 범위·TTL·해시·마스킹, 불변 응답 |
| V2 제출·원본 보관·source 수신 | `bin/sanity`, `common/sanity_submission*.py`, `vwp/submission_cli.py`, `vwp_bridge/submission_*.py`, `oa/submission_receiver.py`, `oa/sanity_workspace_sources.py`, 제출 JSON 예제 | 엄격한 설정·V2 schema·신원/범위·원본 hash·원자 인계·분할 staging/공개·receipt·중복/충돌·실행 순서 |
| AI 호출·분류·규칙·작업자 | `vwp_bridge/sanity_ai.py`, `common/sanity_clustering.py`, `vwp_bridge/sanity_cluster_worker.py`, `oa/sanity_cluster_store.py`, `common/sanity_workers.py`, `vwp_bridge/worker.py`, `oa/worker.py` | strict JSON·근거 검증, scope, 전체 적용, 중단 복구, 불확실 호출 재시도 금지, 사람 확정 |
| 검토·보고서·인증·업무 API | `oa/sanity_oa_store.py`, `oa/sanity_auth.py`, `oa/main.py`, `oa/dashboard_api.py` | 검토 개정·불변 이력·서버 검토자, 신원/권한 검사, API 계약 |
| 대시보드 전체 | `oa/dashboard/` | 로그인·권한·사용자 범위, 데모와 실데이터 분리, 분류/검토/PoR의 의미 |
| 설정·의존성·기존 테스트 | `common/sanity_config.py`, `common/sanity_dbaas.py`, 각 서버의 `.env.example`·`requirements.txt`, 루트 `requirements.txt`, 모든 기존 test/runner와 lockfile | 설정/실패 처리, 재현성, 기존 테스트 기대값과 skip 조건 |
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

프로젝트·블록 RBAC, 영속 관리 레코드/PoR 저장, 새 버전 전송 계약, 공통 parser/판정/등록 엔진 확장, UI SSO 연결은 **내부 AI의 수정 허용 영역이 아니다**. 구현·변경·디버깅은 Codex가 담당한다. 회사 파서 등록은 위 허용 파일의 고정 인터페이스 사용만 허용한다.
2026-10-15 Codex가 별도 테스트 workspace의 서버 범위 검사와 PoR 영속 저장을 추가했다.
`oa/sanity_workspace.py`, `oa/workspace_api.py`, `devtools/workspace_app.py`, `db/005_workspace.sql`, `tools/run_workspace.py`도 보호 공통 코드다.
사내 공통 Nginx 뒤의 간단한 컨테이너 실행은 `docker/Dockerfile-prod`와 `oa/sanity_hosts.py`의 정확한 Host 검증을 사용한다. 이 Docker 구성·Host 검증·`dashboard_api.create_app`의 배포 인자도 Codex 관리 공통 코드다. 내부 AI는 승인된 실제 `OA_ALLOWED_HOSTS` 값과 회사 어댑터만 설정하며 bind/헤더 신뢰/인증 보호를 변경하지 않는다.
사용자 판정의 `oa/sanity_workspace_review.py`, `common/sanity_review_advice.py`, `db/006_workspace_reviews.sql`과 화면/기존 테스트도 보호 대상이다. 회사 review SDK는 위 어댑터 본문에서만 구현한다.
두 단계의 `oa/sanity_workspace_clustering.py`, `oa/sanity_workspace_group_review.py`, `db/007_workspace_pipeline.sql`과 UI/API·기존 테스트·QA helper도 보호 대상이다. 내부 AI가 분류/판정 개정이나 오래된 참조 검사를 완화하지 않는다.
`devtools/sanity_codex_mock.py`, `tools/run_codex_mock.py`, `tools/seed_codex_mock.py`와 관련 테스트는 사용자 요청으로 Codex가 추가한 **외부 모델·합성 자료 전용 시험**이며 보호 대상이다. 내부 AI는 이를 회사 report/사례/SDK에 연결하거나 합성 입력 검사를 완화하지 않는다. 회사 VWP용 어댑터의 허용 범위와 `CORPORATE` 미연결 상태는 유지한다.
`devtools/sanity_workspace_codex.py`와 전용 `/clustering/ai` API/버튼도 같은 보호 대상이다. 명시적으로 켠 독립 합성 환경만 사용하며 회사 어댑터의 OA 모델 호출 금지를 해제하지 않는다. 내부 AI가 `codex_mock_enabled`나 합성 입력 검사를 회사 환경에서 켜거나 우회하지 않는다.
대용량 순차 검토의 `oa/sanity_workspace_pipeline.py`, `devtools/sanity_large_samples.py`, `WorkspaceWorkbench.tsx`와 작업 재개·페이지·서버 편집 API/테스트도 같은 보호 대상이다. 내부 AI가 task 상태/전체 coverage·한도·권한을 바꾸거나 공통 worker 연결을 자체 추가하지 않는다.
`common/sanity_integration_contracts.py`, `common/sanity_integration_examples.py`, `tests/test_integration_contracts.py`와 [INTERNAL_AI_HANDOFF.md](INTERNAL_AI_HANDOFF.md)도 Codex가 관리하는 고정 추상 계약/합성 예제/명세다. 내부 AI는 SDK 필드에 맞춰 이 타입/예제를 바꾸지 않고 허용된 회사 본문에서 대응한다. 현 review 계약의 PASS/FAIL 최소 사람 사례 참조 조건, workspace의 `CORPORATE` 차단과 SSO 업무 차단도 그대로다. 원본만으로 최초 AI 판정을 제안하는 공통 계약 확장은 Codex 후속 작업이다.
[WORKSPACE.md](oa/WORKSPACE.md)의 공용 테스트 계정/환경 표식을 회사 인증 fallback으로 쓰거나 실제 SSO 보호 장치를 해제하지 않는다.
DBA의 승인된 설치 SQL 실행과 실제 운영 배포는 사용자가 관리하며, 이 문서가 그 실행 권한을 내부 AI에게 자동 부여하지 않는다.

## 5. 작업·검증·인계 순서

1. 이 문서 → `INTERNAL_AI_HANDOFF.md` → 자기 역할의 `INTERNAL_AI_TASK.md` → `requirement.md`의 관련 절 → `PROTOCOL.md` → 해당 연동 문서를 읽는다. SSO는 `oa/SSO_INTEGRATION.md`, 예상 목록은 `vwp/PLAN_INTEGRATION.md`, report 파서는 `vwp_bridge/PARSER_INTEGRATION.md`도 읽는다. 두 bridge의 접속 정보는 `db/CONNECTION_HANDOFF.md`를 따른다.
2. 작업 시작 시 `git status --short`로 기존 변경을 기록하고, 새로 수정할 자기 허용 파일·메서드와 근거 문서를 짧게 제시한다. 범위가 불명확하면 먼저 문의한다.
3. 사내 사실을 확인하고 한 번에 어댑터 하나만 구현한다. 예시를 실제 계약으로 추정하거나 관련 없는 리팩터링을 하지 않는다.
4. 새 SDK/입력/report 어댑터 테스트를 추가한다. 기존 테스트를 수정하지 않는다. 파서는 정상·위반·경계값·명시적 0건·빈 파일·잘림·미해석 구간·도구 버전 변경을 검증한다. 합성/모의 검증과 사내 실환경 검증 결과를 구분한다.
5. 오류가 발생하면 6절로 전환한다. 정상인 경우 수정 파일·핵심 diff·실행 명령·통과/실패/skip·남은 항목을 인계한다.
6. Codex 검토와 사용자 승인 전에는 운영 DB 변경·인증 범위 공개·배포·push/강제 push를 하지 않는다. `.env`와 비공개 자료는 diff 첨부에서 제외한다.

기존 계약 확인 명령(저장소 루트의 전체 개발 테스트 환경에서 실행):

```bash
.venv/bin/python -m unittest discover -s tests -p test_auth.py -v
.venv/bin/python -m unittest discover -s tests -p test_integration_contracts.py -v
.venv/bin/python -m unittest discover -s oa -p test_sso.py -v
.venv/bin/python -m unittest discover -s tests -p test_parsers.py -v
.venv/bin/python db/tests/run_mock.py --pg-bin /path/to/postgresql/bin
git diff --check
```

마지막 DB 명령은 개발 머신에 PostgreSQL 12.1+ 서버 바이너리가 준비된 경우의 별도 mock 검증이다. 사내 실제 DB에 실행하는 명령이 아니다.
2026-10-21 사용자 요청으로 `run_tests.py`와 `db/tests/run_mock.py`의 기본은 `quick` 핵심 회귀다. 전수 회귀·포털 전체 단계·10만 건 검사는 `--profile full`로 별도 실행한다. 선택 프로필·실행 건수를 결과에 남기며 `quick`을 전수 통과로 보고하지 않는다. 이 실행 범위 분리는 내부 AI에게 기존 테스트·입력·기대값·앱 한도를 수정하거나 실패를 생략할 권한을 주지 않는다.
회사 테스트는 자기 서버의 `integration/company_*/`에 작성하고 해당 디렉터리를 별도로 테스트한다. 예: `.venv/bin/python -m unittest discover -s vwp_bridge/integration/company_parsers -p 'test_*.py' -v`. 공통 runner/mock은 회사 구현·비공개 fixture를 자동 수집하지 않는다.
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
작업 ID / 담당 개발 AI: VWP / VWP-bridge / OA 중 하나
실행 환경: VWP 원천 / VWP bridge / OA / mock, DB side는 해당 시 vwp 또는 oa
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

역할별로 [VWP](vwp/INTERNAL_AI_TASK.md), [VWP-bridge](vwp_bridge/INTERNAL_AI_TASK.md), [OA](oa/INTERNAL_AI_TASK.md)의 마지막 시작 명령을 각각 전달한다. 모든 AI에게 전체 INT 작업을 수행하라고 지시하지 않는다. 다음 공통 지시는 맡은 역할에 함께 적용한다.

> 먼저 저장소 루트 `AGENTS.md`, `INTERNAL_AI_GUIDE.md`, `INTERNAL_AI_HANDOFF.md`와 자기 역할의 `INTERNAL_AI_TASK.md`를 읽어라. 너의 역할은 배정받은 사내 연동 구현자다. 자기 역할이 소유한 회사 어댑터·입력 매핑·전용 테스트만 작성하고 다른 담당자의 파일과 공통 코드·DB SQL·판정/coverage·권한 검사·UI·기존 테스트는 수정하지 말라. 실제 원문은 사내에서만 검증하며 외부 Codex 문의에는 합성 입력만 사용하라. 실제 SDK와 사내 자료로 확인한 사실만 사용하라. 예상하지 못한 오류가 발생하거나 보호 파일 변경이 필요하면 수정/재시도를 멈추고 `[Codex 디버깅 요청]`을 비식별화해 작성하라. 직접 문의 도구가 없으면 사용자에게 이 대화로 전달해 달라고 요청하고 `WAITING_FOR_CODEX`로 대기하라. SSO 업무 API·CORPORATE의 의도된 차단이나 미완료 sanity 상태를 성공으로 바꾸지 말라. 어댑터 검증과 COMMON 운영 연결·실제 적용 승인을 구분하라.
