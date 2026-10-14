# 사내 AI 인수인계 — SSO 인증 어댑터

SSO 공급자 SDK와 사내 계정 DB만 연결하면 `/api/session`에서 검증된 내부 사용자를 확인할 수 있도록 인증 경계를 구현했다. 실제 SSO 방식·URL·claim은 추정하지 않았다. **사내 AI가 먼저 수정할 파일은 [corporate_sso.py](corporate_sso.py)의 두 메서드다.**

수정 권한은 [INTERNAL_AI_GUIDE.md](../INTERNAL_AI_GUIDE.md)와 [AGENTS.md](../AGENTS.md)를 먼저 따른다.
이 문서의 운영 권한·DB·UI 후속 계획은 **Codex 담당**이며 사내 AI에게 공통 파일 수정 권한을 주는 지시가 아니다.
사내 AI는 회사 어댑터와 명시적으로 허용된 주입 부분만 구현한다. 자기 코드에서도 예상 밖 오류가 나면 수정/재시도를 멈추고 Codex에게 문의한다.

현재 업무 API는 단일 사용자용이다. SSO 연동 완료와 프로젝트·블록 권한 연동 완료를 분리한다. 아래의 업무 권한 작업까지 끝내기 전 SSO 사용자의 업무 API는 `503 AUTHORIZATION_NOT_IMPLEMENTED`를 반환한다. 기존 로컬 토큰 모드는 종전처럼 동작한다.

## 1. 파일과 구현 책임

| 파일 | 현재 제공한 내용 | 사내에서 구현할 부분 |
|---|---|---|
| [sanity_auth.py](../sanity_auth.py) | 추상 인터페이스, 입력·출력 모델, 모드 선택, 계정 연결·활성 상태 검사, 오류 표준화 | 내부 AI 수정 금지. 운영 범위 검사 확장은 Codex 담당 |
| [corporate_sso.py](corporate_sso.py) | `CorporateIdentityProvider`, `CorporateUserDirectory`의 명시적인 미구현 응답 | SSO 검증과 영속 사용자 ID 조회 |
| [sso_app.py](sso_app.py) | 두 어댑터를 API에 주입하는 사내 실행 진입점 | 회사 모듈 import와 어댑터/router 주입만 허용. 인증 모드·bind·proxy 설정 변경 금지 |
| [dashboard_api.py](dashboard_api.py) | `create_app` 주입 지점, `authenticated`, `authorized_actor`, 정규화된 `/api/session` | 내부 AI 수정 금지. 운영 권한 필터·감사 ID·배포 경계는 Codex 담당 |
| [App.tsx](dashboard/src/App.tsx) | 기존 메모리 Bearer 토큰 로그인·API 호출 | 내부 AI 수정 금지. 실제 SSO 화면 연결은 Codex 담당 |
| [test_auth.py](../tests/test_auth.py), [test_sso.py](test_sso.py) | 사내망 없이 실행하는 인증·계정 연결·API 계약 테스트 | 기존 테스트 변경 금지. 회사 어댑터 전용 테스트는 `oa-bridge/company_sso/`에 새로 추가 |

## 2. 고정 인터페이스

```python
class SSOIdentityProvider(ABC):
    def authenticate(self, request: AuthRequest) -> VerifiedIdentity: ...

class UserDirectory(ABC):
    def resolve(self, identity: VerifiedIdentity) -> LocalUser | None: ...
```

호출 흐름은 다음과 같다.

```text
HTTP 요청
  → AuthRequest: 검증되지 않은 헤더·쿠키
  → SSOIdentityProvider.authenticate: 사내 인증 검증
  → VerifiedIdentity(issuer, subject)
  → UserDirectory.resolve: 영속 계정 연결 조회
  → LocalUser(user_id, display_name, active)
  → Actor(user_id, reviewer, auth_method="sso")
  → 세션 응답 또는 업무 권한 검사
```

| 모델 | 필드 | 계약 |
|---|---|---|
| `AuthRequest` | `method`, `path`, `headers`, `cookies`, `client_host` | 헤더 이름은 소문자. 읽기 전용 매핑이며 헤더·쿠키는 repr에서 제외. 요청 본문·query는 전달하지 않음 |
| `VerifiedIdentity` | `issuer`, `subject` | 인증 검증이 끝난 외부 계정 키. 각 1~512자, 앞뒤 공백·제어문자 오류 거부. 값을 임의 정규화하지 않음 |
| `LocalUser` | `user_id`, `display_name`, `active` | 내부 영속 ID는 영문·숫자 시작, 영문·숫자·`_.-` 1~100자. 표시 이름 1~100자, 활성 여부는 bool |
| `Actor` | `user_id`, `reviewer`, `auth_method` | 앱이 사용하는 신원. `reviewer`는 내부 계정의 표시 이름. 외부 토큰·전체 claims·SSO 역할 없음 |

두 메서드는 **동기 함수**다. FastAPI의 동기 dependency에서 작업 스레드로 호출하므로 기존 동기 SDK/DB 클라이언트를 사용할 수 있다. 네트워크 호출에는 사내 기준의 timeout을 설정한다. `async def`나 dependency 변경이 필요하면 내부 AI가 수정하지 않고 Codex에게 문의하며 coroutine을 그대로 반환하지 않는다. 어댑터 객체는 앱 안에서 공유되므로 사용자별 상태를 인스턴스 변수에 저장하지 않고 동시 요청을 안전하게 처리한다.

`issuer + subject`를 유일한 외부 계정 키로 사용한다. 다른 issuer의 같은 subject, 같은 이름의 다른 사용자, 이름 변경을 구분한다. 이메일·표시 이름·REVISION 안의 작성자 이름으로 계정을 합치지 않는다. `user_id`는 프로젝트·블록 담당 배정과 감사 이력에서 쓰는 내부 ID다. DB 로그인 계정과 다르다.

## 3. 사내 AI가 채울 두 메서드

### 3.1. CorporateIdentityProvider.authenticate

1. 실제 SSO가 OIDC/OAuth access token, SAML 세션, 사내 gateway 세션 중 무엇을 제공하는지 확인한다.
2. 승인된 SDK·인증 middleware·세션 저장소로 요청을 검증한다. 토큰 방식이면 서명·발급자·대상 서비스·만료 등을, 세션 방식이면 유효한 서버 세션·만료·폐기를 확인한다.
3. 검증한 결과에서 안정적인 issuer와 subject를 추출해 `VerifiedIdentity`를 반환한다.
4. 누락·위조·만료는 `AuthError("UNAUTHORIZED")`, 인증 서비스 장애는 `AuthError("AUTH_PROVIDER_UNAVAILABLE")`로 변환한다.

현재 미구현 메서드는 `AUTH_SSO_NOT_CONFIGURED`를 반환한다. JWT payload decode나 `X-User-ID`/`X-Remote-User`를 읽는 것만으로 성공 처리하지 않는다. 인증 proxy를 사용한다면 proxy의 사용자 검증, 외부 입력 헤더 제거, backend 직접 우회 차단까지 포함해 신뢰 경계를 구현한다. 비밀값과 SDK 예외 원문을 응답·로그에 넣지 않는다.

### 3.2. CorporateUserDirectory.resolve

1. 영속 저장소에서 `(identity.issuer, identity.subject)`의 계정 연결을 조회한다.
2. 연결된 내부 계정을 `LocalUser(user_id=..., display_name=..., active=...)`로 반환한다.
3. 연결이 없으면 `None`, 비활성 계정이면 `active=False`, 저장소 장애면 `AuthError("AUTH_DIRECTORY_UNAVAILABLE")`를 사용한다.

인터페이스가 사용자 DB 생성이나 자동 가입을 수행하지 않는다. 초기 관리자·사전 등록·첫 로그인 등록 정책은 사내 기준을 확인해 Codex에게 전달한다. 내부 AI가 정책을 선택하거나 자동 등록/권한 부여를 구현하지 않는다. 승인된 자동 등록을 추후 구현하더라도 `(issuer, subject)` 중복을 DB에서 방지하고 프로젝트/TOP/블록 권한을 자동 부여하지 않는다. 프로젝트·블록 배정은 업무 DB의 별도 권한 정보다.

기존 4개 테이블에는 관리 레코드 확장 DDL이 아직 없다. 이름을 임시 ID로 저장하거나 가짜 run/bundle에 계정을 넣지 않는다. 승인된 영속 매핑 저장소가 없다면 중단하고 [요구사항 15.8절](../requirement.md#dashboard-domain-spec)의 증분 저장 구현을 Codex에게 요청한다. 내부 AI는 SQL을 새로 만들거나 DB 제약을 완화하지 않는다. 프로세스 재시작 후에도 같은 계정은 같은 내부 ID에 연결되어야 한다.

## 4. 실행과 로그인 경로 연결

기존 실행은 `SANITY_AUTH_MODE=local_token` 또는 설정 생략 시 기존 토큰·고정 검토자 방식을 사용한다. 일반 진입점에서 `SANITY_AUTH_MODE=sso`만 설정하면 미연결 오류가 반환된다. 실제 어댑터는 아래 진입점에 주입한다.

```bash
# 저장소 루트, 기존 requirements.txt 설치 환경
.venv/bin/python oa-bridge/sso_app.py
```

`sso_app.py`는 `auth_mode="sso"`를 명시하므로 `.env`에 이전 로컬 토큰이 남아 있어도 이를 인증에 사용하지 않는다. 어댑터 미구현·실패 시 기존 토큰·데모 사용자로 우회하지 않는다. 예제 그대로 실행하면 health/UI는 열리지만 세션은 `503 AUTH_SSO_NOT_CONFIGURED`다.

로그인·콜백·로그아웃이 앱에 필요하면 사내 AI가 허용된 `oa-bridge/company_sso/`에 `APIRouter`를 구현하고 주입한다. 경로는 자동으로 `/auth` 아래에 배치되며 UI의 정적 파일 mount보다 먼저 등록된다. 기존 UI/API는 수정하지 않는다.

```python
# 사내 구현 후 sso_app.py에서 연결하는 형태
from company_sso.login_routes import router

app = create_app(
    auth_mode="sso",
    identity_provider=CorporateIdentityProvider(),
    user_directory=CorporateUserDirectory(),
    auth_router=router,  # router의 /login → 실제 /auth/login
)
```

`company_sso.login_routes`는 사내에서 추가할 모듈 이름 예시이며 현재 파일이 아니다. 로그인 시작·콜백 검증·세션 생성/폐기와 해당 SSO에 필요한 state/nonce/PKCE 또는 SAML 검증은 여기서 구현한다. callback을 `SSOIdentityProvider.authenticate` 안에서 매번 처리하지 않는다. 쿠키 인증은 안전한 쿠키 설정과 상태 변경 요청의 CSRF 검증을 포함해야 한다. 공통 API/UI 변경이 필요한 부분은 요구 계약을 Codex에게 전달하며, 연결되기 전 업무 API 차단을 유지한다.

현재 API는 loopback 및 고정 Host 허용 목록을 유지한다. 사내 AI는 회사 배포용 proxy·Host·TLS 계약을 확인하고 실제 경계 변경은 사용자/Codex에게 요청한다. 무조건적인 forwarded header 신뢰나 모든 Host 허용으로 대체하지 않는다.

## 5. 세션 응답과 오류 계약

검증된 SSO 계정이 연결되면 DB의 업무 데이터에 접근하지 않고 세션을 확인할 수 있다.

```json
{
  "user_id": "usr-203",
  "reviewer": "김철수",
  "auth_method": "sso",
  "access_mode": "postgresql",
  "writable": false,
  "authorization_ready": false,
  "notice": "SSO 사용자 연결 확인 · 프로젝트·블록 권한 연동은 아직 준비되지 않았습니다."
}
```

`GET /api/session` 응답에서 issuer·subject·원본 claims·토큰·쿠키를 노출하지 않는다. `writable`은 현재 운영 권한 미구현 상태에서는 false다. 로컬 모드의 `user_id="local-reviewer"`는 단일 사용자 prototype 식별자이며 회사 사용자 계정으로 이전·연결하지 않는다.

| HTTP | code | 의미 |
|---|---|---|
| 401 | `UNAUTHORIZED` | 인증 누락·위조·만료 |
| 403 | `ACCOUNT_NOT_LINKED` | 인증은 됐으나 내부 계정 연결 없음 |
| 403 | `ACCOUNT_DISABLED` | 연결된 내부 계정이 비활성 |
| 503 | `AUTH_SSO_NOT_CONFIGURED` | SSO 어댑터 미연결·미구현 |
| 503 | `AUTH_DIRECTORY_NOT_CONFIGURED` | 계정 연결 어댑터 미연결·미구현 |
| 503 | `AUTH_PROVIDER_UNAVAILABLE`, `AUTH_DIRECTORY_UNAVAILABLE` | 공급자 또는 계정 저장소 장애 |
| 503 | `AUTH_CONFIG`, `AUTH_INVALID_RESULT` | 잘못된 모드·주입 설정 또는 계약에 맞지 않는 어댑터 결과 |
| 503 | `AUTHORIZATION_NOT_IMPLEMENTED` | SSO 신원은 확인됐으나 업무 데이터 범위 검사가 아직 없음 |

인증 공급자·계정 저장소를 매 요청에서 다시 확인하므로 이전 요청 성공을 그대로 캐시하지 않는다. 공급자의 자체 세션 검증·폐기 정책은 사내 구현 책임이다. 알 수 없는 SDK/DB 예외는 고정 장애 코드로 변환하며 상세 내용은 응답에 포함하지 않는다.

## 6. SSO 사용자에게 업무 화면을 연결하는 순서 — Codex 후속 작업

1번의 회사 어댑터 검증은 내부 AI 범위다. **2~7번의 저장·권한·감사·기존 UI/API 변경은 Codex가 수행한다.** 내부 AI는 사내 사실과 요구 계약만 제공하고 관련 보호 파일은 수정하지 않는다.

1. 위 두 어댑터를 구현하고 `/api/session`의 사용자 ID·활성/비활성·미연결·만료를 확인한다.
2. 내부 사용자 ID에 프로젝트·블록 담당 배정을 저장하고 로그인 사용자의 조회 범위를 계산한다. TOP은 담당 프로젝트 전체 조회, PoR 지정은 해당 블록 담당자라는 [15절 업무 규칙](../requirement.md#dashboard-domain-spec)을 따른다.
3. API의 `authorized_actor`를 대상별 권한 검사로 확장한다. **`require_business_access`의 SSO 차단만 삭제하지 않는다.** `/api/bundles`의 SQL 목록·건수·페이지부터 허용 범위를 적용해야 한다. 현재는 전체 번들을 조회한다.
4. bundle/unit/report/download, 근거 request/response/record, AI 작업·구성원·수신·변경 API까지 대상 ID의 실제 프로젝트·블록을 조회해 검사한다. `/api/receive`는 전역 큐 처리이므로 별도의 운영 권한·작업자 책임을 결정한다. URL의 ID나 사용자 입력 범위를 그대로 신뢰하지 않는다.
5. 감사·검토·PoR 저장에 `actor.user_id`를 추가한다. 현재 review/clustering 저장 함수의 `reviewer` 문자열은 이름이다. 이름만 저장하면 동명이인·이름 변경 이력을 구분할 수 없다. 기존 불변 이력은 덮어쓰지 않고 새 계약으로 확장한다.
6. 그다음 `/api/session`의 `authorization_ready`와 `writable`을 실제 권한에 맞춰 계산한다. 미배정 사용자는 정상 인증된 상태로 업무 데이터가 없는 안내를 보여준다. 계정 미연결과 업무 미배정을 구분한다.
7. `App.tsx`의 로그인 form, `api` 호출, 최초 세션 확인 effect, `logout`을 사내 방식에 맞게 연결한다. 현재는 토큰 입력 전 API를 호출하지 않고 모든 호출에 Bearer를 넣으므로 쿠키 SSO를 자동 지원하지 않는다. 쿠키 방식이면 로그인 후 세션 조회·서버 로그아웃·CSRF를 연결하고, 토큰 방식이면 사내 SDK의 토큰 수명 관리를 연결한다. 데모 사용자 선택은 인증 수단으로 사용하지 않는다.

## 7. 검증과 완료 기준

사내망 없이 아래 테스트를 실행할 수 있다. 새 테스트의 인증 성공은 명시적인 테스트 더블만 사용한다.

```bash
.venv/bin/python -m unittest discover -s tests -p test_auth.py -v
.venv/bin/python -m unittest discover -s oa-bridge -p test_sso.py -v
.venv/bin/python -m unittest discover -s oa-bridge -p test_dashboard.py -v
```

마지막 명령의 PostgreSQL 통합 테스트는 별도 테스트 DB 설정이 없으면 skip된다. skip을 DB 검증 성공으로 보고하지 않는다. 실제 어댑터 구현 후에는 회사 SDK/mock transport로 아래를 검증한다.
어댑터 전용 테스트는 신규 허용 위치에 추가하며 기존 테스트는 수정하지 않는다. 아래 중 UI·업무 권한 검증은 Codex의 후속 구현 수용 기준이다. 예상하지 못한 실패는 자체 수정하지 않고 문의한다.

- 검증 성공, 자격증명 없음·위조·만료·폐기, 잘못된 issuer/audience, 인증 서비스 장애.
- 같은 subject의 다른 issuer, 동명이인, 이름 변경, 미연결·비활성 계정, 계정 저장소 장애.
- 두 사용자의 동시 요청이 서로의 계정으로 바뀌지 않음, 세션 종료·계정 비활성화 후 접근 차단.
- 프런트엔드의 로그인→세션→로그아웃과 실제 적용 방식의 CSRF·콜백 검증.
- TOP·블록 담당자·미배정 사용자의 목록/건수/상세/다운로드/AI 요청 권한, 타 프로젝트·블록 ID 접근 거부.
- 기존 로컬 토큰 회귀와 원래 검사·검토 이력 보존. SSO 장애 시 로컬 토큰이나 데모로 우회하지 않음.

## 8. 사내 AI에게 전달할 작업 요청문

> 먼저 루트 `AGENTS.md`, `INTERNAL_AI_GUIDE.md`와 이 SSO 문서를 읽어라. 너의 수정 범위는 `corporate_sso.py`의 회사 검증·영속 계정 조회 어댑터, `sso_app.py`의 허용된 주입 부분, 새 `company_sso/` 지원 모듈과 테스트다. 회사 문서로 확인된 SDK와 승인된 계정 연결 저장소만 사용하라. 저장소가 없거나 새 의존성/핵심 코드 변경이 필요하면 Codex에게 문의하라. `sanity_auth.py`, `dashboard_api.py`, DB SQL, 기존 테스트와 UI는 수정하지 말라. 특히 `AUTHORIZATION_NOT_IMPLEMENTED`를 해제하지 말라. 어댑터 테스트가 예상과 다르게 실패하면 수정/재시도를 멈추고 비식별 `[Codex 디버깅 요청]`을 작성하라. 직접 연결 수단이 없으면 사용자가 현재 Codex 대화로 전달하도록 요청하고 답변 전에는 `WAITING_FOR_CODEX`로 대기하라. 인증 성공과 업무 권한/운영 배포 완료를 구분하라.
