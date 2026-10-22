# 사내 AI 인수인계 — SSO 인증 어댑터

SSO 공급자 SDK와 사내 계정 DB만 연결하면 `/api/session`에서 검증된 내부 사용자를 확인할 수 있도록 인증 경계를 구현했다. 실제 SSO 방식·URL·claim은 추정하지 않았다. **사내 AI가 먼저 수정할 파일은 [corporate_sso.py](integration/corporate_sso.py)의 두 메서드다.**

수정 권한은 [INTERNAL_AI_GUIDE.md](../INTERNAL_AI_GUIDE.md)와 [AGENTS.md](../AGENTS.md)를 먼저 따른다.
이 문서의 운영 권한·DB·UI 후속 계획은 **Codex 담당**이며 사내 AI에게 공통 파일 수정 권한을 주는 지시가 아니다.

개발 소유자는 [OA 담당 AI](INTERNAL_AI_TASK.md)다. [소스 구조](../DEPLOYMENT.md)의 `oa/`와 [OA 설정 예제](.env.example)와 [DB 연결 인계](../db/CONNECTION_HANDOFF.md)를 사용하며 VWP 자격증명/모델은 OA에서 사용하지 않는다.
사내 AI는 회사 어댑터와 명시적으로 허용된 주입 부분만 구현한다. 자기 코드에서도 예상 밖 오류가 나면 수정/재시도를 멈추고 Codex에게 문의한다.

운영 화면은 `/`, 업무 API는 기존 `/api/*`, SSO 세션은 `GET /api/session`을 사용한다. 2026-10-22 [운영 권한·Welcome·관리 계약](ACCESS_MANAGEMENT.md)을 추가했다. `SANITY_ADMIN_USER_ID`와 활성 계정·저장 계약이 준비된 경우에만 대상별 SSO 권한을 적용하며 미설정 상태의 `503 AUTHORIZATION_NOT_IMPLEMENTED`는 유지한다. 별도 합성 `/workspace`와 `/workspace/api/*`는 운영에 마운트하지 않는다. 합성 workspace의 PoR·버전 API를 운영 SSO에 연결한 것은 아니다. 기존 로컬 토큰 모드는 종전처럼 동작한다. 통합 인계는 [INTERNAL_AI_HANDOFF.md](../INTERNAL_AI_HANDOFF.md)를 따른다.

2026-10-20 Codex가 서버 인증 방식 조회와 쿠키 SSO용 로그인·세션 확인·POST 로그아웃 UI를 연결했다. SSO 인증이 성공해도 업무 권한이 준비되지 않았으면 연결 확인 화면을 보여주며 업무 API를 호출하지 않는다. 회사 어댑터·실제 IdP 검증과 workspace 운영 권한 연결은 별도다.

## 1. 파일과 구현 책임

| 파일 | 현재 제공한 내용 | 사내에서 구현할 부분 |
|---|---|---|
| [sanity_auth.py](sanity_auth.py) | 추상 인터페이스, 입력·출력 모델, 모드 선택, 계정 연결·활성 상태 검사, 오류 표준화 | 내부 AI 수정 금지. 운영 범위 검사 확장은 Codex 담당 |
| [corporate_sso.py](integration/corporate_sso.py) | `CorporateIdentityProvider`, `CorporateUserDirectory`의 명시적인 미구현 응답 | SSO 검증과 영속 사용자 ID 조회 |
| [sso_app.py](sso_app.py) | 두 어댑터를 API에 주입하는 사내 실행 진입점 | 회사 모듈 import와 어댑터/router 주입만 허용. 인증 모드·bind·proxy 설정 변경 금지 |
| [dashboard_api.py](dashboard_api.py) | `create_app` 주입 지점, `authenticated`, `authorized_actor`, `/api/auth-config`, 정규화된 `/api/session` | 내부 AI 수정 금지. 운영 scope 필터·안정 감사 ID·배포 경계 제공/변경은 Codex 담당 |
| [App.tsx](dashboard/src/App.tsx), [DashboardLogin.tsx](dashboard/src/DashboardLogin.tsx), [useDashboardAuth.ts](dashboard/src/useDashboardAuth.ts) | 서버 설정에 따른 토큰/쿠키 SSO 로그인, 세션·로그아웃, Welcome·관리·범위별 업무 진입 | 내부 AI 수정 금지. 회사 router와 세션 SDK만 허용된 위치에서 연결 |
| [test_auth.py](../tests/test_auth.py), [test_sso.py](test_sso.py) | 사내망 없이 실행하는 인증·계정 연결·API 계약 테스트 | 기존 테스트 변경 금지. 회사 어댑터 전용 테스트는 `oa/integration/company_sso/`에 새로 추가 |

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

이 조회 인터페이스는 사용자 DB 생성이나 자동 가입을 수행하지 않는다. 2026-10-22 전달된 사내 정책은 단일 issuer·불변/비재사용 LoginId·검증된 전사 계정 등록이며 [009 계정 저장 계약](SSO_IDENTITY.md)에 반영했다. 009는 DB 구조만 제공하고 등록/조회 저장부와 ACS 연결은 아직 미구현이다. 내부 AI가 독자 저장부·자동 가입/권한 부여를 구현하지 않는다. 향후 등록 경로도 `(issuer, subject)` 확인과 DB 중복 방지를 유지하고 프로젝트/TOP/블록 권한을 자동 부여하지 않는다.

`db/005_workspace.sql`로 기존 네 테이블 안에 `WS_USER` 등 관리 레코드는 추가되어 있다. 이것은 `(issuer, subject)`의 실제 영속 매핑·회사 사용자 등록이나 SSO 운영 연결 완료를 뜻하지 않는다. 이름을 임시 ID로 저장하거나 가짜 run/bundle에 계정을 넣지 않는다. 승인된 매핑 저장소가 없으면 기존 workspace와의 연결 요구를 Codex에게 인계한다. 내부 AI는 SQL을 새로 만들거나 DB 제약을 완화하지 않는다. 프로세스 재시작 후에도 같은 계정은 같은 내부 ID에 연결되어야 한다.

009의 `SSO_IDENTITY`와 기존 workspace의 `WS_USER`는 서로 다른 레코드다. 자동 생성된 인증 계정은 담당 배정이나 workspace 업무 접근을 뜻하지 않는다. 조회는 매 요청에서 enabled와 issuer를 다시 확인하고, 최근 로그인 시각은 향후 성공한 세션 발급 경로에서만 갱신한다. 실제 회사 인증 값 없이 준비한 SQL과 회사 IdP 실검증 결과는 구분한다.

## 4. 실행과 로그인 경로 연결

기존 실행은 `SANITY_AUTH_MODE=local_token` 또는 설정 생략 시 기존 토큰·고정 검토자 방식을 사용한다. 일반 진입점에서 `SANITY_AUTH_MODE=sso`만 설정하면 미연결 오류가 반환된다. 실제 어댑터는 아래 진입점에 주입한다.

```bash
# 저장소 루트, 기존 requirements.txt 설치 환경
.venv/bin/python oa/sso_app.py
```

`sso_app.py`는 `auth_mode="sso"`를 명시하므로 `.env`에 이전 로컬 토큰이 남아 있어도 이를 인증에 사용하지 않는다. 어댑터 미구현·실패 시 기존 토큰·데모 사용자로 우회하지 않는다. 예제 그대로 실행하면 health/UI는 열리지만 세션은 `503 AUTH_SSO_NOT_CONFIGURED`다.

로그인·콜백·로그아웃이 앱에 필요하면 사내 AI가 허용된 `oa/integration/company_sso/`에 `APIRouter`를 구현하고 주입한다. 경로는 자동으로 `/auth` 아래에 배치되며 UI의 정적 파일 mount보다 먼저 등록된다. 기존 UI/API는 수정하지 않는다.

```python
# 사내 구현 후 sso_app.py에서 연결하는 형태
from company_sso.login_routes import router

app = create_app(
    auth_mode="sso",
    identity_provider=CorporateIdentityProvider(),
    user_directory=CorporateUserDirectory(),
    allowed_hosts=os.environ.get("OA_ALLOWED_HOSTS"),  # 기존 os import·배포 설정 유지
    auth_router=router,  # router의 /login → 실제 /auth/login
)
```

`company_sso.login_routes`는 사내에서 추가할 모듈 이름 예시이며 현재 파일이 아니다. 로그인 시작·콜백 검증·세션 생성/폐기와 해당 SSO에 필요한 state/nonce/PKCE 또는 SAML 검증은 여기서 구현한다. callback을 `SSOIdentityProvider.authenticate` 안에서 매번 처리하지 않는다. 쿠키 인증은 안전한 쿠키 설정과 상태 변경 요청의 CSRF 검증을 포함해야 한다. 공통 API/UI 변경이 필요한 부분은 요구 계약을 Codex에게 전달하며, 연결되기 전 업무 API 차단을 유지한다.

직접 실행하는 `python -m oa.sso_app`은 loopback 8000을 유지한다. 사용자가 요청한 사내 Nginx 뒤의 컨테이너 실행은 [루트 Docker 구성](../docker/README.md)이 담당하며, uvicorn 한 개를 컨테이너의 8080에 실행한다. `sso_app.py`는 `OA_ALLOWED_HOSTS`의 정확한 도메인/IP를 적용하고 미지정 시 기존 localhost 목록만 허용한다. 회사 어댑터 주입 시 이 배포 설정을 보존한다. 사내 AI는 실제 proxy·TLS·SSO callback 계약을 확인하되 공통 bind·Host 검증을 변경하지 않는다. 무조건적인 forwarded header 신뢰나 모든 Host 허용으로 대체하지 않는다.

프록시에서 공개 Host·포트와 Origin을 보존해야 업무 API의 Origin 검사가 일치한다. [Nginx 포함 구성](docker/README.md#2-빌드와-실행)은 `proxy_set_header Host $http_host;`를 사용하며 OA와 같은 정확한 호스트 허용 목록을 적용한다. Origin 삭제·검증 비활성화로 해결하지 않으며 `/auth/logout`의 기존 CSRF 정책을 유지한다. forwarded 헤더 신뢰나 TLS scheme 추론을 새로 허용하는 변경은 아니다.

## 5. 세션 응답과 오류 계약

로그인 전 `GET /api/auth-config`로 서버가 선택한 인증 방식을 조회한다. 세션의 `auth_method`나 빌드 플래그로 미인증 사용자의 로그인 방식을 추측하지 않는다.

```json
{
  "mode": "sso",
  "login_url": "/auth/login",
  "logout_url": "/auth/logout",
  "logout_method": "POST"
}
```

이 응답은 사용자·쿠키·토큰·내부 주소 없이 표시용 설정만 반환하고 인증 SDK나 업무 DB를 호출하지 않는다. 실제 인증과 같은 `effective_mode`를 사용하며, 잘못된 모드는 `503 AUTH_CONFIG`다. 로컬 토큰 모드의 세 경로 필드는 모두 null이다. SSO 모드는 주입한 router에 `GET /login`, `POST /logout`이 있을 때 해당 고정 경로를 제공하고 없으면 null로 표시한다. GET 로그아웃만 있는 기존 router는 POST 계약으로 광고하지 않는다. 설정·세션 응답에는 `Cache-Control: no-store`를 적용한다.

UI 동작은 다음 계약을 따른다.

- SSO: 토큰 입력 없이 same-origin 쿠키로 `/api/session`을 조회한다. Authorization 헤더를 만들지 않는다. 미인증 401이면 SSO 버튼을, 계정 미연결/비활성 403이면 원인 안내와 앱 로그아웃을, 설정/서비스 503이면 명시적인 오류를 보여준다. 오류를 로컬 토큰·데모로 대체하지 않는다.
- 로그인: `GET /auth/login`으로 페이지를 이동한다. 회사 router가 IdP 이동·콜백 검증·쿠키 발급을 담당한다. 콜백 후 앱으로 돌아오면 `/api/session`을 다시 조회하며, 콜백 성공만으로 내부 계정 연결을 가정하지 않는다.
- 로그아웃: same-origin 쿠키로 `POST /auth/logout`을 보내고 `200 {"status":"LOGGED_OUT"}`를 확인한 뒤 탭의 세션과 업무 표시를 지운다. 실패하면 종료됐다고 표시하지 않으며 자동 재시도하지 않는다. Origin/Referer 검증과 서버 세션 폐기·쿠키 삭제는 회사 router의 책임이다. IdP 전체 로그아웃은 회사 정책에 따라 별도 연결한다.
- SSO의 `authorization_ready`가 true가 아니면 인증 확인·권한 연결 대기 화면을 보여주고 업무 요청을 시작하지 않는다. 준비된 엔진에서 배정이 없는 사용자는 `/?page=welcome`으로 안내하고, env 관리자는 `/?page=admin`에서 등록·승인을 진행한다. 정상 SSO 세션과 범위 거부 403/404를 구분하며 타인의 데이터나 이전 선택 자료를 남기지 않는다.
- 로컬 토큰: 기존 메모리 Bearer 토큰 로그인을 유지한다. 공통 인증 UI는 `/workspace`의 합성 계정 로그인과 별도이며, 명시적인 데모 진입은 예시 데이터만 사용한다.

검증된 SSO 계정이 연결되면 report 원문에 접근하지 않고 세션을 확인할 수 있다. 아래는 관리자 설정이 없는 보호 상태 예다. 권한 엔진이 준비된 경우의 추가 필드와 계산 기준은 [운영 권한 계약 5절](ACCESS_MANAGEMENT.md#5-api세션요청-보호)을 따른다.

```json
{
  "user_id": "usr-203",
  "reviewer": "김철수",
  "auth_method": "sso",
  "access_mode": "postgresql",
  "writable": false,
  "authorization_ready": false,
  "is_admin": false,
  "can_read_business": false,
  "can_receive": false,
  "notice": "SSO 사용자 연결 확인 · 프로젝트·블록 권한 연동은 아직 준비되지 않았습니다."
}
```

`GET /api/session` 응답에서 issuer·subject·원본 claims·토큰·쿠키를 노출하지 않는다. `writable`은 미설정 상태에서는 false이고, 준비된 엔진에서는 OWNER 배정을 반영한다. 특정 bundle의 수정 여부는 상세 `permissions`와 서버의 대상별 검사로 판단한다. 로컬 모드의 `user_id="local-reviewer"`는 단일 사용자 prototype 식별자이며 회사 사용자 계정으로 이전·연결하지 않는다.

| HTTP | code | 의미 |
|---|---|---|
| 401 | `UNAUTHORIZED` | 인증 누락·위조·만료 |
| 403 | `ACCOUNT_NOT_LINKED` | 인증은 됐으나 내부 계정 연결 없음 |
| 403 | `ACCOUNT_DISABLED` | 연결된 내부 계정이 비활성 |
| 503 | `AUTH_SSO_NOT_CONFIGURED` | SSO 어댑터 미연결·미구현 |
| 503 | `AUTH_DIRECTORY_NOT_CONFIGURED` | 계정 연결 어댑터 미연결·미구현 |
| 503 | `AUTH_PROVIDER_UNAVAILABLE`, `AUTH_DIRECTORY_UNAVAILABLE` | 공급자 또는 계정 저장소 장애 |
| 503 | `AUTH_CONFIG`, `AUTH_INVALID_RESULT` | 잘못된 모드·주입 설정 또는 계약에 맞지 않는 어댑터 결과 |
| 403 | `FORBIDDEN` | 관리 권한 없음·조회 전용 범위의 수정·요청 출처 보호 실패 |
| 404 | `RESOURCE_NOT_FOUND` | 업무 대상 없음 또는 허용 범위 밖·scope 없음 |
| 409 | `REVISION_CONFLICT`, `REQUEST_CONFLICT` | 관리 개정 충돌 또는 UUID/내용·요청 상태 충돌 |
| 503 | `AUTHORIZATION_NOT_IMPLEMENTED` | SSO 신원은 확인됐으나 운영 관리자/권한 엔진이 미설정 |
| 503 | `AUTHORIZATION_CONFIG`, `AUTHORIZATION_SCHEMA`, `AUTHORIZATION_STATE_CORRUPT` | 관리자 설정·필수 저장 구조·관리 이력 계약 오류 |

인증 공급자·계정 저장소를 매 요청에서 다시 확인하므로 이전 요청 성공을 그대로 캐시하지 않는다. 공급자의 자체 세션 검증·폐기 정책은 사내 구현 책임이다. 알 수 없는 SDK/DB 예외는 고정 장애 코드로 변환하며 상세 내용은 응답에 포함하지 않는다.

## 6. 운영 권한 연결과 사내 반영

Codex 공통 구현과 사내 SDK 구현을 분리한다. 상세 API·저장·검증·반영 순서는 [ACCESS_MANAGEMENT.md](ACCESS_MANAGEMENT.md)를 따른다. 내부 AI는 이 목록을 근거로 공통 코드를 직접 수정하지 않는다.

1. 사내 두 인증 어댑터와 기존 회사 router를 유지하고 `/api/session`의 검증된 user_id·활성/미연결·만료를 확인한다. 등록은 별도 승인된 로그인 경로에서만 수행하며 조회가 계정을 자동 생성하지 않는다.
2. OA 비공개 `SANITY_ADMIN_USER_ID`에 관리자 한 명의 정확한 user_id를 설정한다. env는 SSO를 대신하지 않고 report 권한도 자동 부여하지 않는다. 빈 값이면 기존 보호 차단을 유지한다.
3. 관리자가 프로젝트·블록을 등록하고, 사용자가 Welcome에서 TOP/OWNER를 요청한다. 관리자는 사유와 기대 개정으로 승인·거절·회수한다. 자기 요청 승인도 허용한다. synthetic `WS_USER`·`tops/owners`는 운영 권한의 근거가 아니다.
4. 기존 자유 `run.project`에 이름 매핑을 추가하지 않는다. VWP 수집 전의 명시적 `run.scope`를 봉인하고 수신 시 레지스트리 소속을 검증한다. scope 없는 결과는 SSO에서 숨긴다. legacy 신뢰된 서비스 수신과 사용자 신원을 검증하는 V2 제출은 서로 다른 계약이다.
5. 목록·건수·페이지 SQL과 모든 대상별 API에 최신 TOP/OWNER 범위를 검사한다. TOP은 프로젝트 조회, OWNER는 해당 블록 조회·검토·분류·근거 변경이다. SSO의 전역 `/api/receive`는 관리자 포함 403이며 수신은 신뢰된 서비스 작업자가 담당한다.
6. 기존 005~009 저장 구조에서 불변 레지스트리와 `oa-rbac-v1` 관리 이벤트를 사용한다. 추가 DDL·초기 설치 재실행 없이 기존 SSO 행을 보존한다. 업무 요청/권한 회수의 공유·배타 잠금, `READ COMMITTED`, 안정 검토자 user_id를 적용한다.
7. 업무 쓰기는 JSON과 `X-Sanity-Request: 1` 및 요청 출처 검사를 사용한다. 회사 callback/logout의 CSRF·세션 폐기·실제 IdP 리다이렉트 검증은 계속 사내 책임이다. `/workspace`를 운영에 마운트하거나 데모 사용자를 fallback으로 사용하지 않는다. PoR/V2의 운영 SSO 연결은 별도다.

## 7. 검증과 완료 기준

사내망 없이 아래 테스트를 실행할 수 있다. 새 테스트의 인증 성공은 명시적인 테스트 더블만 사용한다.

```bash
.venv/bin/python -m unittest discover -s tests -p test_auth.py -v
.venv/bin/python -m unittest discover -s oa -p test_sso.py -v
.venv/bin/python -m unittest discover -s oa -p test_auth_config.py -v
.venv/bin/python -m unittest discover -s oa -p test_dashboard.py -v
```

마지막 명령의 PostgreSQL 통합 테스트는 별도 테스트 DB 설정이 없으면 skip된다. skip을 DB 검증 성공으로 보고하지 않는다. 실제 어댑터 구현 후에는 회사 SDK/mock transport로 아래를 검증한다.
어댑터 전용 테스트는 신규 허용 위치에 추가하며 기존 테스트는 수정하지 않는다. 아래 중 UI·업무 권한 검증은 Codex 공통 구현의 수용 기준이며 실제 사내 배포 검증과 구분한다. 예상하지 못한 실패는 자체 수정하지 않고 문의한다.

- 검증 성공, 자격증명 없음·위조·만료·폐기, 잘못된 issuer/audience, 인증 서비스 장애.
- 같은 subject의 다른 issuer, 동명이인, 이름 변경, 미연결·비활성 계정, 계정 저장소 장애.
- 두 사용자의 동시 요청이 서로의 계정으로 바뀌지 않음, 세션 종료·계정 비활성화 후 접근 차단.
- 프런트엔드의 로그인→세션→로그아웃과 실제 적용 방식의 CSRF·콜백 검증.
- TOP·블록 담당자·미배정 사용자의 목록/건수/상세/다운로드/AI 요청 권한, 타 프로젝트·블록 ID 접근 거부.
- 기존 로컬 토큰 회귀와 원래 검사·검토 이력 보존. SSO 장애 시 로컬 토큰이나 데모로 우회하지 않음.

## 8. 사내 AI에게 전달할 작업 요청문

> 먼저 루트 `AGENTS.md`, `INTERNAL_AI_GUIDE.md`와 이 SSO 문서를 읽어라. 너의 수정 범위는 `corporate_sso.py`의 회사 검증·영속 계정 조회 어댑터, `sso_app.py`의 허용된 주입 부분, 새 `company_sso/` 지원 모듈과 테스트다. 회사 문서로 확인된 SDK와 승인된 계정 연결 저장소만 사용하라. 저장소가 없거나 새 의존성/핵심 코드 변경이 필요하면 Codex에게 문의하라. `oa/sanity_auth.py`, `dashboard_api.py`, DB SQL, 기존 테스트와 UI는 수정하지 말라. 특히 `AUTHORIZATION_NOT_IMPLEMENTED`를 해제하지 말라. 어댑터 테스트가 예상과 다르게 실패하면 수정/재시도를 멈추고 비식별 `[Codex 디버깅 요청]`을 작성하라. 직접 연결 수단이 없으면 사용자가 현재 Codex 대화로 전달하도록 요청하고 답변 전에는 `WAITING_FOR_CODEX`로 대기하라. 인증 성공과 업무 권한/운영 배포 완료를 구분하라.

## 9. 인증 UI 공통 수정본의 사내 반영

회사 어댑터와 `sso_app.py`의 기존 SDK/router 주입을 유지하면서 사용자가 Codex의 공통 수정본을 반영한다. 이 목록은 2026-10-20 인증 UI 변경의 기록이며, 최신 Welcome·관리 기능의 파일/API·반영 순서는 [운영 권한 계약](ACCESS_MANAGEMENT.md)을 함께 따른다. 내부 AI의 공통 설계 변경 권한을 추가하지 않는다.

- 서버: `oa/sanity_auth.py`, `oa/dashboard_api.py`.
- 화면: `oa/dashboard/src/App.tsx`, `DashboardLogin.tsx`, `useDashboardAuth.ts`, `types.ts`, `demo.ts`. Node 20.18에서 프런트엔드를 다시 빌드한다.
- 검증: `oa/test_auth_config.py`, `oa/test_sso.py`, `oa/dashboard/e2e/sso-login.spec.ts`, `dashboard.spec.ts`. 권한 미설정 시 기존 SSO 업무 API 차단 검증을 유지하고, 설정 후 범위 제한은 새 전용 검증으로 확인한다.

2026-10-20 인증 UI 변경만에는 새 환경변수나 DB SQL이 필요하지 않았다. 최신 관리 기능은 OA 전용 `SANITY_ADMIN_USER_ID`를 추가하며 DB SQL은 추가하지 않는다. 프록시는 기존 `/api/*`, `/auth/*`를 같은 OA 서비스로 전달한다. 설정 조회 → SSO 로그인 이동 → 콜백 후 세션 조회 → POST 로그아웃을 실제 HTTPS 진입 주소에서 사내 검증한다. 실제 SAML 검증·회사 저장소·SP 등록은 합성 검증의 범위에 포함되지 않는다. 인계에는 고정 오류 코드와 합성/실환경 검증 결과만 기록하며 실제 사내 코드·diff·세션 쿠키·SAML 원문을 외부에 전달하지 않는다.

## 10. 승인된 SSO 의존성

2026-10-20 사용자 요청과 Codex 검토로 OA의 SAML/XML 서명 검증에 다음 고정 버전을 추가했다. [OA 런타임 requirements](requirements.txt)와 [전체 개발용 requirements](../requirements.txt)에 같은 pin을 사용한다. VWP·VWP-bridge에는 추가하지 않는다.

| 패키지 | 고정 버전 | 용도 / 공식 배포 정보 |
|---|---|---|
| `signxml` | `5.2.0` | XML 서명 검증. [PyPI 메타데이터](https://pypi.org/pypi/signxml/5.2.0/json) |
| `lxml` | `6.1.3` | XML 처리. [PyPI](https://pypi.org/project/lxml/6.1.3/) |
| `cryptography` | `50.0.2` | 암호·인증서 처리. [PyPI](https://pypi.org/project/cryptography/50.0.2/) |

이 조합은 `signxml`의 `cryptography>=45`, `lxml>=5.2.1,<7` 조건과 프로젝트의 Python 3.10+ 조건을 만족한다. 실제 사내 베이스 이미지·패키지 미러에서 설치와 import를 확인한다. 패키지는 앱이 사용하는 Python에 설치해야 하며, `/opt/venv`를 사용하는 OA Docker 구성은 해당 venv의 Python으로 확인한다. 두 기존 OA Dockerfile은 이미 `oa/requirements.txt`를 설치하므로 이 의존성 추가를 위해 Dockerfile을 수정할 필요는 없다.

저장소 루트의 활성 OA 환경에서 승인된 사내 pip 저장소·CA 설정을 사용한다. 아래 `.venv/bin/python`은 개발 환경 예시이며, 컨테이너에서는 실제 앱 Python 경로를 사용한다.

```bash
.venv/bin/python -m pip install -r oa/requirements.txt
.venv/bin/python -m pip check
.venv/bin/python -c "from importlib.metadata import version; import signxml, lxml.etree, cryptography; print({name: version(name) for name in ('signxml', 'lxml', 'cryptography')})"
.venv/bin/python -m unittest tests.test_auth oa.test_sso oa.test_auth_config -v
```

회사 어댑터 전용 시험은 작성된 `oa/integration/company_sso/`에서 별도로 실행한다. 설치·import와 합성 서명 검증은 실제 SAML 검증·회사 IdP/SP 등록·세션/CSRF·업무 권한 연결을 대신하지 않는다. 사내에서는 기존 정상/위조/만료/재전송/폐기 검증을 유지한다.

**보호 범위의 예외는 [가이드 3.1절](../INTERNAL_AI_GUIDE.md#31-oa-sso-고정-의존성-추가-승인--2026-10-20)뿐이다.** OA 담당 내부 AI는 사내 `oa/requirements.txt`에 없는 위 세 pin만 추가할 수 있다. 이미 같은 pin이 있으면 그대로 두며, 다른 pin이 있거나 설치·기존 시험이 예상 밖으로 실패하면 버전 조정·공통 코드/테스트 수정 없이 `WAITING_FOR_CODEX`로 문의한다. 인증 보호 해제·TLS 검증 생략·SSO 실패 fallback은 허용하지 않는다.
