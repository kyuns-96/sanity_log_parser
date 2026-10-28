# OA 담당 사내 AI 작업 지시문

**2026-10-27 제출 경로:** VWP는 토큰 없이 내부 HTTPS로 접수하고, OA가 전체 source 자료를 `AWAITING_CONFIRMATION`으로 보관한다. 제공된 workbench의 **VWP 제출 확인**에서 현재 SSO OWNER가 범위를 확정해야 보고서가 공개된다. 기존 SSO·현재 블록 권한 연결을 검증하며 VWP 제출 토큰 발급기나 새 인증 SDK를 만들지 않는다. `CorporateSourceIdentity.resolve`는 기존 trusted/legacy source 경로에만 사용한다. 세부 배포 순서는 [HTTPS 안내](../vwp/HTTPS_SUBMISSION.md)를 따른다.

너는 **OA 회사 연동 담당 개발 AI**다. 이번 핵심 작업은 사내 OA의 OpenAI 호환 AI API와 판단 정답/반례를 확인해 **AI 최초 분류·그룹 판정 → 사람 정정·확정 → 다음 판단의 사례 활용**을 제공된 공통 경로에 연결하고 사내에서 검증하는 것이다. 기존 SSO·source 사용자 연결의 코드 소유권도 유지한다. 운영 추론 모델과 사내 코드를 작성하는 개발 AI는 다른 역할이다.

OA API·화면은 `oa/`, 회사 구현은 `oa/integration/`에 있다. Git 저장소 루트에서 실행한다. [자기 설정 예제](.env.example)의 키를 저장소 루트 비공개 `.env`에 설정한다. UI 빌드와 진입점은 [서버별 실행](../DEPLOYMENT.md)을 따른다.

원천 로그의 복원 캐시·묶음 조회와 합성 측정/현장 확인 범위는 [읽기 성능 안내](SOURCE_READ_PERFORMANCE.md)를 따른다. 추가 DDL은 없으며 공통 캐시·비밀 검사·권한 코드를 현장 AI가 직접 수정하는 권한은 부여하지 않는다.

## 1. 먼저 읽을 문서

[AGENTS.md](../AGENTS.md) → [INTERNAL_AI_GUIDE.md](../INTERNAL_AI_GUIDE.md) 전체 → [통합 인계 명세](../INTERNAL_AI_HANDOFF.md) → 이 문서 → [AI_RUNTIME.md](AI_RUNTIME.md) → [SSO_INTEGRATION.md](SSO_INTEGRATION.md) → [DB 연결 인계](../db/CONNECTION_HANDOFF.md)를 읽는다. 업무 범위/담당/PoR 기준은 [요구사항](../requirement.md)의 15·17·18절을 따른다.

가장 먼저 [AI 판단 설계](../AI_DECISION_DESIGN.md)를 읽는다. 이전의 OA 모델 사용 금지·VWP 전용 방침은 대체됐다. 이번 목표는 OA 프로그램이 DB에서 관련 확정 사례와 전체 로그를 읽어 **자기 사내 모델 API**를 호출하고 결과를 검증·저장하는 것이다. 새 AI용 HTTP 서버는 필수가 아니다. 완료 기준은 API 접속 성공이 아니라 판단 품질과 사람 정정의 다음 호출 반영이다.

## 2. 맡을 작업과 수정 소유권

담당 작업은 **INT-04·05의 OA 모델 설정·승인 기준·사내 판단 검증**, **INT-01·06의 사용자/source 동일 계정·OA 접속**이다. [OA 실행 계약](AI_RUNTIME.md)의 공통 모델 연결·검색·직접 근거 판정·worker/운영 화면을 사용한다. 새 모델 어댑터는 필요 없으며 공통 파일은 읽기 전용이다.

| 수정 위치 | 허용하는 작업 |
|---|---|
| `oa/integration/corporate_sso.py` | `CorporateIdentityProvider.authenticate`, `CorporateUserDirectory.resolve` 본문·필요 import·승인된 회사 client 주입용 생성자 |
| `oa/integration/corporate_submission.py` | `CorporateSourceIdentity.resolve` 본문·필요 import·승인된 계정 조회 client 주입 생성자. SSO와 같은 영속 매핑 사용 |
| `oa/sso_app.py` | 승인된 회사 모듈 import와 `identity_provider`, `user_directory`, 필요 `auth_router` 주입 |
| `oa/integration/company_sso/` | 위 어댑터용 SDK wrapper·인증 router·전용 모의 테스트, 사용자/담당 원천 대응표 |
| `oa/requirements.txt` | [가이드 3.1절](../INTERNAL_AI_GUIDE.md#31-oa-sso-고정-의존성-추가-승인--2026-10-20)에 승인된 `signxml==5.2.0`, `lxml==6.1.3`, `cryptography==50.0.2`만 없을 때 추가. 기존 다른 pin·설치 충돌은 변경하지 않고 문의 |
| 해당 서버의 비공개 root `.env`/실행 환경 | 사용자가 허용한 OA 설정 키와 실제 값. [AI 실행 계약](AI_RUNTIME.md)의 `SANITY_OA_AI_*` 포함 |
| `SANITY_OA_AI_CRITERIA_FILE`의 승인된 비공개 파일 | [고정 schema](AI_RUNTIME.md#3-최초-판정을-위한-확인된-점검-기준)로 담당자가 확인한 기준·개정·scope 작성. 실제 기준/정답은 사내 보관 |
| 비공개 `artifacts/internal-handoff/oa/` 또는 승인된 사내 폴더 | OA 모델 API·한도·점검 기준·관련/무관 사례·정답/반례와 SSO/접속 확인 기록 |

위 목록은 [공통 허용 목록](../INTERNAL_AI_GUIDE.md)의 부분집합이다. VWP의 plan/parser/모델 SDK 파일을 수정하지 않는다. `oa/sanity_auth.py`, workspace/업무 API·UI·DB SQL·기존 테스트·지침은 읽기 전용이다. OA 담당이라는 이유로 공통 API/store 전체를 변경할 수 없다.

2026-10-20 승인된 세 SSO 의존성은 공개 공통 수정본에 포함됐다. 사내에 누락된 pin만 위 범위로 추가할 수 있으며 파일 전체의 보호 해제는 아니다. 설치·`pip check`·합성/실환경 검증은 [SSO 인계 10절](SSO_INTEGRATION.md#10-승인된-sso-의존성)을 따른다. 루트 개발용 requirements와 다른 서버의 의존성은 수정하지 않는다.

## 3. 실행 순서

**현재 우선 작업 — AI 판단 설정과 검증:** 다음을 확인해 비공개 인계 위치에 정리한다. 실제 URL·키·report·사람 계정은 외부에 보내지 않고 기능의 지원 여부와 비식별 계약을 Codex에게 전달한다.

- API 종류·인증 방식·모델/버전, JSON/구조화 출력·도구 호출 지원. OpenAI 호환이라는 사실만으로 세부 기능을 추정하지 않는다.
- 입력/출력 토큰·바이트·요청 크기, 동시성·속도 제한, timeout·실패 구분·사용량, 요청 ID로 결과 조회/취소가 가능한지.
- item별 확인된 점검 기준, 사례 없는 최초 분류·판정 정답/반례, 사람 수정 후 다음 report에서 참고해야 하는 사례와 조건 차이. VWP 담당자와 사내 승인 절차로 준비한다.
- 실제 모델 평가에 쓸 독립 정답 집합과 비식별 규모 분포. 검증 대상 자신의 정답을 모델 참고 입력에 넣지 않는다.

제공된 [OA 실행 계약](AI_RUNTIME.md)에 맞춰 private 모델 설정과 승인된 점검 기준 파일을 준비한다. 기준은 현재 로그를 평가하는 확인된 업무 기준이며, DB에 쌓인 사람 확정 사례와 구분한다. 기준/사례가 모두 없으면 판정은 DEFER다. 웹과 worker에 같은 설정·release를 배치하고 사용자 관리 아래 report A → 사람 정정/확정 → report B 입력의 사례 반영을 검증한다. `company_sso/`에 모델 실행을 추가하거나 VWP 어댑터를 복사하지 않는다. 전체 입력 한도 초과는 명시적 실패이며 COMMON-05 모델 분할과 COMMON-06 사내 품질 실측을 완료로 표시하지 않는다.

**기존 SSO/source 연동이 필요한 경우** 아래 절차를 자기 허용 지점에서 수행한다. 이미 검증한 작업을 AI 연동 준비 때문에 반복하지 않는다.

1. `git status --short`로 시작 상태를 기록하고, 수정할 자기 허용 파일/메서드를 제시한다.
2. 실제 SSO 방식·승인 SDK/버전, 신원 검증/폐기 정책, `(issuer, subject) → user_id` 영속 매핑 저장소, 활성/비활성 기준을 확인한다. 승인된 저장소가 없으면 `FACTS_REQUIRED`로 자료를 정리하고 저장 설계를 Codex에게 문의한다. 임의 테이블·메모리 매핑·자동 사용자 생성을 추가하지 않는다.
3. SDK가 실제 검증한 신원으로 `VerifiedIdentity`를 반환하고 승인된 원천 조회로 `LocalUser | None`을 반환한다. 토큰 decode나 신뢰되지 않은 헤더만으로 인증 성공을 만들지 않는다. 동명이인/표시 이름 변경/다른 issuer/미연결/비활성을 구분한다.
4. 회사 방식에서 필요한 경우에만 `company_sso/`에 로그인·콜백·로그아웃 router를 작성하고 기존 `sso_app.py` 주입 지점을 사용한다. 실제 쿠키·CSRF·redirect·TLS 조건을 확인한다. API/보안 middleware·Host·proxy 정책을 자체 변경하지 않는다.
5. 관리자·TOP·블록 담당 배정의 권위 있는 출처, 안정 사용자/프로젝트/블록 ID, 부여·해제·비활성 반영 시점의 대응표를 준비한다. 이는 **공통 RBAC 구현을 위한 사실 자료**다. 역할을 자동 부여하거나 PoR·기존 검토를 직접 저장하지 않는다.
6. OA DB 설정과 현장 점검 결과를 준비하고 COMMON-01·02의 운영 SSO→workbench/원천 연결 검증 자료를 Codex에게 인계한다. 운영 workbench는 기존 `/api/session`·`/api/workbench/*`를 사용하며 합성 workspace의 `/workspace/api/session`은 별도다.
7. source importer의 `resolve(actor)`는 신뢰한 VWP assertion의 `(issuer, subject)`를 SSO와 같은 안정 `user_id`로 조회한다. 입력 user_id만 신뢰하거나 사용자/담당을 자동 생성하지 않는다. importer가 OA의 최신 활성/블록 담당을 다시 대조하므로 실제 관리 데이터 준비를 사용자/Codex에게 인계한다.

`AUTHORIZATION_NOT_IMPLEMENTED`, `authorization_ready`·`writable` 검사를 유지한다. 회사 SDK 구현 완료로 현 테스트 계정이 회사 사용자로 전환되거나 업무 권한 연결이 완료되지 않는다. 인증 실패를 로컬 토큰/데모 사용자로 대체하지 않는다.

Q1의 [분산 수집 설계](../vwp/SOURCE_COLLECTION_DESIGN.md)에 필요한 예상 범위 승인 주체/블록 권한의 원천도 확인한다. launcher의 PC 사용자명·REVISION 안의 이름·입력 `approved_by`는 인증 근거가 아니다. 선언 승인 API/저장과 workspace 논리 중복·PRE 참조 처리는 COMMON-01·02의 Codex 구현이며 SSO 어댑터에서 대신 구현하지 않는다.

[제출 실행 안내](../vwp/SUBMISSION.md)에 따라 사용자는 VWP 터미널에서 제출하고 OA는 DB를 통해서만 수신한다. OA에는 VWP 제출/원본 보관 폴더를 마운트하지 않는다. `oa.submission_receiver`가 전체 조각 검증 후 `sanity_oa_data` 반영과 `sanity_oa_to_vwp` 접수 응답을 원자적으로 확정한다. 공통 운영 권한 연결은 제공했으며 실제 계정 매핑/담당·원천 어댑터·운영 환경의 검증은 사내에서 수행한다. 업로드 API·수신/응답 worker·VWP DB 계정·공통 SQL을 추가하지 않는다.

## 4. DB 연결

- 기본 DB 모드는 **OA 제한 로그인 한 개**, 소속 역할은 `sanity_oa_app`이다. 별도 계정이 없는 환경은 [단일 계정 계약](../db/CONNECTION_HANDOFF.md#31-단일-계정-모드)에 따라 `SANITY_DB_ALLOW_SINGLE_ACCOUNT=true`와 공유 로그인을 설정한다. 역할 존재를 요구하지 않으며 DB 격리 미제공을 기록한다. 두 모드 모두 `--side oa`를 사용한다. 공통 코드 수정 허용 범위는 확대하지 않는다.
- VWP-bridge와 같은 PostgreSQL DB/스키마를 사용하며 기본은 다른 계정, 명시적 단일 모드는 공유 계정이다. 정상 OA 경로는 `sanity_vwp_data`와 VWP 모델 자격증명을 사용하지 않는다. 단일 모드의 상대 private 접근은 DB가 차단하지 못한다. 브라우저/`VITE_*`에 DB·SSO·모델 비밀값을 넣지 않는다.
- 설정 키·TLS·DSN 우선순위·네 테이블 권한은 [DB 연결 인계](../db/CONNECTION_HANDOFF.md)를 따른다. SSO 계정 원천 저장소와 이 프로젝트의 네 테이블 DB가 같다고 가정하지 않는다. 별도 계정 원천은 승인된 조회 client로만 연결한다.
- 실제 DB 접속 확인은 사용자가 해당 서버에서 아래 읽기 전용 명령으로 수행한다. 성공 여부·고정 오류 코드만 인계한다. 접속 확인용으로 수신/검토 저장/PING/migration을 실행하지 않는다.

```bash
.venv/bin/python -m common.sanity_db check --side oa
```

## 5. 검증과 완료 조건

회사 테스트는 작성한 후, 아래 명령을 저장소 루트에서 실행한다.

```bash
.venv/bin/python -m unittest discover -s oa/integration/company_sso -p 'test_*.py' -v
```

기존 인증 회귀는 전체 개발 의존성 설치 후 `.venv/bin/python -m unittest tests.test_auth oa.test_sso -v`로 실행한다. 회사 전용 테스트와 결과를 구분하며 기존 시험은 수정하지 않는다. `git status --short`·`git diff --check`로 변경을 확인한다.

- 정상·위조·만료·잘못된 issuer/audience·SDK 장애와 동명이인·이름 변경·미연결·비활성·동시 요청을 명시적 모의 client로 검증한다. 기존 시험을 수정하지 않는다.
- 모의 검증과 사내 실제 SSO/계정 조회 검증을 나눠 보고한다. 세션 성공을 업무 API 공개·사용자 범위 검증 완료로 표시하지 않는다.
- Codex에 넘길 항목: 비식별 신원/계정/배정 필드 대응, 검증·세션/폐기 계약, 승인 저장소 유무, SDK 의존성/실패 계약, 남은 COMMON-01·02. VWP-bridge에는 기존 DB 송수신 점검 상태만 공유한다.
- OA 모델 호출·검색·직접 근거 판정의 공통 실행은 제공됐으며, 사내 실제 API 호환·입력 한도·판단 품질을 검증한다. 기존 SSO 코드·외부 합성 mock을 우회 경로로 사용하지 않는다. VWP 원본 파일을 직접 가져오지 않으며 VWP↔OA 전달은 기존 DB 계약을 사용한다.
- 모델 연동 완료 보고에는 실제 사용 모델·입력 범위·제공/인용 사례·사람 정정의 다음 입력 반영·품질 비교 여부를 포함한다. API 응답/JSON 검증만으로 의미 판단 품질 완료를 보고하지 않는다.

## 6. 중단과 결과 보고

예상 밖 오류·테스트 실패·보호 파일 변경 필요 시 자기 SSO 어댑터에서도 수정/재시도를 멈춘다. [가이드의 비식별 문의](../INTERNAL_AI_GUIDE.md#6-디버깅이-필요하면-codex에게-문의)를 작성하고 `WAITING_FOR_CODEX`로 남긴다. 직접 문의 수단이 없으면 사용자가 현재 설계 담당 Codex 대화로 전달한다. 전체 claim·토큰·쿠키·개인정보·DSN·SDK 예외 원문을 외부에 첨부하지 않는다.

인계에는 `담당: OA / INT-04·05 모델 계약·품질 자료, INT-01·06 사용자·접속`, 기준 commit, 수정 파일/메서드, API/한도·점검 기준·정답/반례 준비 상태, 모의/실제 검증·DB 점검과 남은 COMMON 작업을 적는다. 자료 준비·`ADAPTER_VERIFIED`·실제 AI 판단 품질·운영 적용 완료를 구분한다.

## 7. 그대로 전달할 시작 명령

> 너는 OA 회사 연동 담당이다. AI_DECISION_DESIGN.md, AGENTS.md, INTERNAL_AI_GUIDE.md 전체와 oa/INTERNAL_AI_TASK.md를 읽어라. 최우선 목표는 AI 최초 분류·그룹 판정과 사람 확정 근거의 검색·피드백 순환이다. 우선 OA OpenAI 호환 API의 세부 요청/응답·인증·모델·JSON/도구 지원·입출력/동시성/timeout/조회·취소 한도와 item별 기준·정답/반례를 비공개 위치에 준비해 비식별 계약을 인계하라. oa/AI_RUNTIME.md와 oa/.env.example의 지원 설정 및 승인된 비공개 기준 catalog를 준비하고 제공된 공통 실행으로 첫 판단 순환을 검증하라. 모델 실행·검색·검증·worker/저장 공통 코드는 읽기 전용이다. 대량 모델 분할과 사내 실제 품질 검증의 미완료 상태를 구분하라. 기존 SSO/source 작업이 필요하면 위 허용 파일/메서드만 사용하고 완료한 작업을 반복하지 말라. VWP 전용 자격증명·원본 파일과 외부 합성 모델 경로를 사용하지 않는다. 공통 코드·SQL·UI·기존 테스트·보호 장치를 수정하지 말고 예상 밖 오류는 WAITING_FOR_CODEX로 문의하라. API 접속·사례 준비·실제 판단 품질·운영 적용 완료를 구분해 보고하라.
