# 운영 SSO 권한·Welcome·관리 화면 계약

기준: 2026-11-16 사용자 결정. 로그인한 활성 SSO 사용자는 프로젝트 배정 없이 모든 등록 프로젝트·블록의 업무 자료를 조회한다. 수정 권한은 기존 프로젝트별 **MEMBER 한 종류**로 유지한다. 운영 진입점은 `/`와 `/api/*`다. `/?page=welcome`은 수정 권한 신청용이며 조회의 선행 조건이 아니다. `/?page=admin`에서 관리자가 프로젝트·블록을 등록하고 요청을 승인·거절하거나 수정 권한을 회수한다. 별도 합성 앱 `/workspace`와 `/workspace/api/*`는 운영에 마운트하지 않는다.

이 문서는 Codex가 관리하는 공통 구현 계약이다. 사내 AI의 회사 어댑터 허용 범위는 [INTERNAL_AI_GUIDE.md](../INTERNAL_AI_GUIDE.md)를 따른다. 실제 SSO·사내 report·운영 DB 배포 검증을 합성 테스트로 대신하지 않는다.

## 1. 계정과 관리자

- 인증은 기존 `VerifiedIdentity → LocalUser → Actor` 계약을 유지한다. 현재 로그인한 활성 SSO 계정의 `LocalUser.user_id`와 엄격한 009 `SSO_IDENTITY` 행을 확인한다. 표시 이름·이메일·브라우저 입력·신뢰되지 않은 헤더로 권한을 정하지 않는다.
- OA 서버의 비공개 환경변수 `SANITY_ADMIN_USER_ID`에 관리자 한 명의 **정확한 내부 user_id**를 설정한다. 영문·숫자 시작, 영문·숫자·`_.-`로 된 1~100자이며 대소문자를 구분한다. 실제 값은 Git·프런트엔드 `VITE_*`·외부 문의에 넣지 않는다.
- 누락 또는 빈 값이면 운영 권한 관리는 준비되지 않은 상태다. 기존 `AUTHORIZATION_NOT_IMPLEMENTED` 차단을 유지한다. 잘못된 값은 `AUTHORIZATION_CONFIG`로 거부하며 다른 사용자로 대체하지 않는다.
- 환경변수는 인증을 대신하지 않는다. 관리자도 유효한 SSO 세션과 활성 영속 계정이 필요하다. env를 설정해도 계정·배정·검사 데이터가 자동 생성되지 않는다.
- 관리자는 프로젝트·블록 등록과 요청 승인·거절·회수를 할 수 있다. report는 다른 활성 SSO 사용자와 같이 전체 조회하며, 판정·분류 변경에는 본인의 프로젝트 멤버 배정이 필요하다. 본인도 Welcome에서 수정 권한을 요청하며 자기 요청을 승인할 수 있다.
- 회사 SSO 로그인·콜백·세션 폐기와 매핑 저장부의 현장 구현은 [SSO_INTEGRATION.md](SSO_INTEGRATION.md), 계정 행은 [SSO_IDENTITY.md](SSO_IDENTITY.md)를 따른다. 이 기능은 새로운 로그인 방식이나 자동 계정 등록 경로를 추가하지 않는다.

## 2. 사용자 흐름과 역할

1. SSO 로그인 후 내부 계정·활성과 권한 엔진 준비를 확인하고 공통 홈을 연다. 프로젝트 배정이 없어도 전체 등록 프로젝트를 선택해 조회한다.
2. 관리자는 관리 화면에서 프로젝트 이름과 설명을 등록하고, 그 아래에서 블록 이름을 등록한다. 클라이언트는 ID를 입력하지 않는다. 서버가 이름과 프로젝트 범위로 고정 ID를 결정해 응답하며 같은 이름의 재등록은 기존 항목을 사용한다. 이름은 바깥 공백만 제거한 뒤 정확히 비교하며 대소문자나 내부 공백을 합치지 않는다. 같은 이름의 블록은 서로 다른 프로젝트에서 별도 항목이다.
3. 수정이 필요한 사용자는 Welcome에서 프로젝트를 골라 사유를 적어 멤버 권한을 요청한다. 역할·블록 선택은 없다. 다른 사람의 user_id를 대신 제출하지 않는다.
4. 관리자는 신청자·역할·범위·사유를 확인해 승인 또는 거절하고 사유를 남긴다. 아직 승인되지 않은 요청으로 수정할 수는 없다.
5. 승인된 프로젝트 멤버는 해당 프로젝트의 모든 등록 블록을 검토·수정한다. 이후 같은 프로젝트에 추가된 블록에도 적용된다. 조회 목록은 승인 여부와 관계없이 전체 등록 프로젝트를 포함한다.
6. 관리자가 프로젝트 배정을 회수하면 해당 사용자의 같은 프로젝트 수정 권한 전체를 한 이벤트로 해제하고 다음 쓰기 요청부터 최신 배정을 검사한다. 조회는 유지한다. 과거 report·사람 판정·작성자·승인/회수 이력은 삭제하거나 덮어쓰지 않는다. 다시 수정이 필요하면 새 요청·승인을 진행한다.

| 주체 | 프로젝트·블록 목록 | report 조회 | 판정·분류·추가 근거 변경 | 관리 |
|---|---|---|---|---|
| 활성 SSO 미배정 사용자 | 전체 등록 목록 | 전체 등록 프로젝트·블록 | 없음 | 본인 요청·결과 확인 |
| MEMBER | 전체 등록 목록 | 전체 등록 프로젝트·블록 | 배정 프로젝트의 모든 등록 블록 | 본인 요청·결과 확인 |
| env 관리자 | 전체 등록 목록 | 전체 등록 프로젝트·블록 | 별도 프로젝트 멤버일 때만 | 등록·승인·거절·회수 |

한 사람은 여러 프로젝트의 멤버가 될 수 있다. 프로젝트 생성은 수정 권한 배정과 별도이며 관리자를 자동 등록하거나 최소 인원을 강제하지 않는다. 블록이 아직 없는 등록 프로젝트도 목록에 표시한다. 미배정 프로젝트의 수정, 미인증·비활성 계정의 업무 접근은 차단한다.

관리 화면의 **부여된 권한**은 사용자별 목록과 선택한 사용자의 담당 범위로 나눠 표시한다. 이름·계정 ID로 사용자를 찾고, 선택한 사용자의 권한을 프로젝트 이름 또는 ID로 좁혀 볼 수 있다. 사용자와 권한은 각각 한 페이지에 10개씩 표시한다. 이름이 같아도 계정 ID로 구분하며, 활성 계정 표시 이름이 없는 기존 배정도 ID로 조회·회수할 수 있다.

권한 회수는 선택한 사용자의 프로젝트 멤버 배정에 적용한다. 같은 프로젝트의 기존 TOP·OWNER 배정도 모두 해제한다. 확인창에서 이름·계정 ID·범위·프로젝트 전체 적용 범위를 확인하고 처리 사유를 입력한다. 마지막 권한을 회수해도 선택한 사용자 화면을 유지하며 다른 사람으로 자동 전환하지 않는다. 검색·필터는 조회 조건이고 권한을 변경하지 않는다. 새 권한은 기존의 사용자 요청·관리자 승인 흐름으로 부여한다.

**PoR 경계:** 블록 담당자가 PoR을 지정한다는 업무 규칙은 유지한다. 현재 운영 legacy `/api/*`에는 PoR·버전 API가 없다. 합성 workspace의 PoR 저장이나 별도 V2 source import를 운영 SSO에 연결했다고 표시하지 않는다. 이번 Welcome·관리 기능으로 PoR 지정이 제공되는 것은 아니다.

## 3. bundle의 고정 프로젝트·블록 연결

기존 `payload.run.project`와 `RECEIVED_BUNDLE.project`는 자유 문자열이며, `WS_PROJECT`의 고정 프로젝트 ID(`workspace_key`/`payload.id`)와 자동 연결되지 않는다. 이름·파일명·REVISION·design·stage에서 소속을 추정하는 매핑은 만들지 않는다.

운영 SSO에 공개할 legacy 결과는 **수집 전에** 아래 `run.scope`를 포함한다. 합성 예제의 값은 실제 사내 식별자가 아니다.

```json
{
  "schema_version": 1,
  "run": {
    "run_id": "run-example-1",
    "project": "표시용 프로젝트 문자열",
    "scope": {
      "schema_version": 1,
      "project_id": "project-example",
      "block_id": "block-example"
    }
  }
}
```

위 코드는 scope 위치만 설명하는 발췌다. 실제 plan의 design·stage·tool·tool_version·inventory 등 기존 필수 필드는 생략하지 않는다.

- `run.scope`의 키는 `schema_version`, `project_id`, `block_id` 세 개로 고정한다. schema_version은 정수 1이다. 누락된 선택 필드와 `null`은 다르며, `scope:null`이나 추가 키·잘못된 ID는 거부한다.
- 한 bundle은 등록된 프로젝트·블록 한 쌍에 속한다. 블록의 실제 부모 프로젝트와 일치해야 한다. 여러 블록의 결과를 한 bundle로 묶지 않는다.
- 회사 원천 어댑터는 실제 report의 소속을 확인해 고정 ID를 제공한다. VWP 수집→분석→봉인 bundle까지 동일 scope를 보존하며 analysis/bundle 해시에 포함한다. 수신 뒤 브라우저나 OA가 scope를 덧붙이거나 변경하지 않는다.
- OA 수신은 scope가 있을 때 등록 프로젝트·블록 관계를 검증한다. 미등록·불일치는 거부한다. 기존 scope 없는 결과는 로컬 토큰 prototype 호환을 위해 받을 수 있지만, SSO 목록·건수에 포함하지 않고 상세도 404로 차단한다.
- 기존 SSO 계정과 업무 데이터는 보존한다. 이름 기반 자동 이관·기존 bundle 재봉인은 수행하지 않는다. scope 없는 기존 업무 데이터를 옮길 필요가 생기면 별도 Codex 검토가 필요하다. 실제 배포 상태·계정 및 데이터 현황은 공개 문서에 기록하지 않는다.
- 분류 scope에도 canonical project/block을 포함한다. 표시 문자열이 같아도 다른 블록의 규칙·그룹·근거·과거 사례와 섞지 않으며, scope 없는 과거 사례를 scoped 결과에 자동 적용하지 않는다.

기존 `ANALYSIS_BUNDLE` envelope에는 인증된 제출자 정보가 없다. 이 경로는 **신뢰된 VWP 서비스 수신**이며, scope 검증이 업로더의 OWNER 권한을 입증하는 것은 아니다. 사용자 제출 권한은 프로젝트 멤버 검사와 기존 V2의 검증된 source 신원·승인 계약을 따르며 V2 schema나 SDK 계약을 이번 기능으로 바꾸지 않는다. 전역 큐를 처리하는 `/api/receive`는 SSO에서 관리자 포함 403이다. 수신은 신뢰된 서비스 작업자/운영 CLI가 담당한다.

## 4. 저장 계약 — 추가 DDL 없음

기존 네 테이블과 005~009 제약을 사용한다. 새 테이블·역할·GRANT·migration을 추가하지 않는다. 009가 적용되고 SSO 행이 존재하는 DB에 초기 0행 설치 절차나 원본 DDL을 다시 실행하지 않는다.

| 저장 종류 | 용도·권한의 근거 |
|---|---|
| `SSO_IDENTITY` | 기존 009 계정·활성 상태. payload/state의 고정 키를 유지하고 역할을 추가하지 않음 |
| `WS_PROJECT`, `WS_BLOCK` | `registry_namespace="oa-rbac-v1"`의 불변 프로젝트·블록 레지스트리. `tops`/`owners`는 빈 배열이며 운영 권한의 근거로 읽지 않음 |
| `WS_CONFIG` | `namespace="oa-rbac-v1"`, `schema_version=1`인 불변 관리 이벤트. 프로젝트/블록 생성·권한 요청·판정·회수를 순서대로 보존 |
| `RECEIVED_BUNDLE.payload.bundle.run.scope` | 봉인된 결과 소속. legacy 행의 workspace FK 컬럼을 임의로 채우지 않음 |

관리 이벤트의 `workspace_key`는 `rbac1.event.<command UUID>`다. `record_id`는 기존 `stable_uuid("workspace-v1", "WS_CONFIG", workspace_key)`로 만들며 `state={}`, `version=0`, `updated_at=created_at`을 유지한다. 이벤트에는 연속 `revision`, `kind`, `command_id`, `actor_id`, `created_at`, `data`를 저장한다. 과거 이벤트 UPDATE/DELETE로 배정을 고치지 않는다.

현재 배정은 이벤트를 검증해 재구성한다. 프로젝트·블록 등록 행과 관리 이벤트의 ID·소속·불변값이 다르거나, 이력 개정이 끊기거나, 알 수 없는 형식이 있으면 실패한다. 합성 `WS_USER`·기존 workspace의 `tops/owners`를 회사 계정·권한으로 재사용하지 않는다. 기존 workspace UNIQUE의 정의와 valid/ready 상태도 읽기 전용으로 확인하며 자동 DDL은 수행하지 않는다.

생성·요청에는 클라이언트 UUID `request_id`, 승인·회수에는 UUID `command_id`를 사용한다. 같은 UUID·같은 사용자·같은 내용의 재전달은 중복 이력을 만들지 않는다. UUID를 다른 내용에 재사용하면 `REQUEST_CONFLICT`다. 승인·거절·회수는 현재 `revision`과 `expected_revision`이 같아야 하며 충돌은 재조회 후 사용자가 다시 결정한다.

## 5. API·세션·요청 보호

모든 관리 API는 기존 SSO dependency와 활성 계정 검사를 사용한다. 로컬 토큰은 신규 관리 기능의 관리자 인증 수단이 아니다.

| 메서드·경로 | 입력·결과 |
|---|---|
| `GET /api/access/catalog` | 신청용 `{projects:[{id,name,description}], blocks:[{id,project_id,name}]}`. 타인의 배정·report 없음 |
| `GET /api/access/me` | 본인 `{user_id,is_admin,revision,grants,requests}` |
| `POST /api/access/requests` | `{request_id,role:"MEMBER",project_id,block_id:null,reason}`. 기존 TOP·OWNER 요청은 호환 수락하되 프로젝트 멤버로 해석 |
| `GET /api/admin/access` | 관리자 전용 현재 `revision`, 전체 요청·활성 배정·활성 계정의 ID/표시 이름 |
| `POST /api/admin/projects` | `{request_id,name,description?}` → `{status,project_id,revision}`. 반환된 `project_id`를 이후 범위 선택에 사용 |
| `POST /api/admin/projects/{project_id}/blocks` | `{request_id,name}` → `{status,project_id,block_id,revision}`. 경로의 canonical `project_id`와 반환된 `block_id`를 사용 |
| `POST /api/admin/requests/{request_id}/decision` | `{command_id,decision:"APPROVE"\|"REJECT",reason,expected_revision}` |
| `POST /api/admin/grants/{grant_id}/revoke` | `{command_id,reason,expected_revision}` |

프로젝트·블록 생성은 서버에서 canonical ID를 정한다. 프로젝트 ID는 `project-`와 `["atlas-project-v1",trimmed name]`의 compact UTF-8 JSON 바이트에 대한 SHA-256 digest로, 블록 ID는 `block-`와 `["atlas-block-v1",canonical project ID,trimmed name]`의 compact UTF-8 JSON 바이트에 대한 SHA-256 digest로 구성된다. 입력은 기존 텍스트 규칙대로 바깥 공백만 제거하고, 대소문자·내부 공백·Unicode를 정규화하지 않는다. 클라이언트와 수집기는 규칙을 재구현하거나 이름에서 ID를 계산하지 않고 응답·선택된 ID를 그대로 보존한다. 기존 등록 이름과 ID는 우선 보존되며, 모호한 기존 이름·ID 충돌은 거부한다. 빈 설명을 포함한 기존 재등록은 메타데이터를 바꾸지 않으며, 비어 있지 않은 다른 설명은 충돌로 거부한다.

기존 TOP·OWNER의 불변 요청·승인·회수 이력은 재작성하지 않는다. 현재 grant 응답은 사용자·프로젝트당 `role="MEMBER"`, `block_id=null` 한 건으로 통합하며 실제 현재 grant UUID 중 사전순 첫 값을 회수 대상 ID로 제공한다. 새로운 `REVOKE_PROJECT` 이벤트는 이 ID의 사용자·프로젝트에 속한 모든 현재 배정을 한 번에 해제한다. 과거 `REVOKE` 이벤트는 원래 한 건 회수 의미와 같은 UUID 재확인 계약을 유지한다. 새 신청·승인은 사용자·프로젝트 단위 중복을 거부한다.

request status는 `PENDING`, `APPROVED`, `REJECTED`다. grant는 승인된 활성 배정이며 회수 후 현재 grant 목록에서 제외하고 과거 승인·회수 이벤트는 보존한다. 등록 이름은 1~100자, 설명은 최대 1,000자, 사유는 1~2,000자다. 등록 이름의 앞뒤 공백은 제거되며 제어문자·미정의 필드는 거부된다. 생성 요청은 UUID `request_id`를 계속 사용하며 같은 UUID의 재전달은 기존 idempotency 계약을 따른다. 기존 이름을 새 `request_id`로 다시 등록한 경우에는 별도의 재사용 receipt를 기록한다. 해당 이벤트는 중앙 VWP 작업자와 같은 릴리스로 배포해야 한다.

`GET /api/session`은 기존 필드를 유지하고 `is_admin`, `can_read_business`, `can_receive`를 제공한다. `authorization_ready`는 권한 엔진 준비 여부이며 배정 보유 여부가 아니다. 미배정 활성 SSO 사용자도 준비된 엔진에서는 `authorization_ready=true`, `can_read_business=true`, `writable=false`다. SSO의 `can_receive`는 false다. `writable=true`가 미배정 프로젝트 bundle의 수정 권한을 뜻하지 않으며 상세 `permissions`와 실제 서버 검사를 따른다.

SSO 상태 변경 요청은 JSON과 `X-Sanity-Request: 1` 헤더가 필요하다. 명시적인 cross-site `Origin`/`Sec-Fetch-Site`는 거부하고 CORS를 개방하지 않는다. Origin은 검증된 Host와 비교하며 임의 forwarded header를 신뢰하지 않는다. 회사 `/auth/logout`과 콜백의 CSRF/세션 검증은 계속 회사 router의 책임이다.

프록시는 공개 Host와 포트를 보존하고 Origin을 그대로 전달한다. Nginx 포함 구성은 `proxy_set_header Host $http_host;`를 사용하며 Nginx와 OA 양쪽에서 동일한 정확한 호스트 허용 목록을 적용한다. Host를 내부 주소로 바꾸거나 Origin을 삭제해 비교를 피하지 않는다. 전달 헤더 소거·백엔드 loopback·검증 활성 상태는 유지한다. 구체적인 배치는 [OA Docker 안내](docker/README.md#2-빌드와-실행)를 따른다.

SSO 조회는 모든 등록 프로젝트·블록의 유효 scope를 **SQL count·정렬·페이지 제한 전에** 적용한다. 상세·원문·다운로드·검토 이력과 Area 현재/기준 프로젝트 자료도 배정 없이 조회할 수 있다. 실제 소속·원본/binding 무결성과 비공개 인증값의 경계는 유지하며 미등록·scope 없음은 `404 RESOURCE_NOT_FOUND`다. 판정·분류·근거 요청·AI 실행 등 쓰기는 대상 프로젝트의 멤버 배정을 검사하고, 비관리자의 관리 시도는 `403 FORBIDDEN`이다. 조회 개방은 미인증 접근 허용이 아니다.

업무 요청은 공유 transaction advisory lock을 검사부터 사용 완료까지 유지하고, 레지스트리/요청/승인/회수 변경은 같은 전역 잠금을 배타적으로 잡는다. `READ COMMITTED`에서 잠금 후 최신 계정·배정을 읽어, 회수와 이미 진행 중인 쓰기 요청의 순서를 직렬화한다. 회수 완료 후 시작한 쓰기가 이전 배정으로 실행되지 않게 한다. 새 SSO 검토 작성자는 표시 이름만으로 식별하지 않고 안정 `user_id`를 기록한다.

## 6. 사내 반영 순서와 완료 기준

1. 사용자가 Codex 공통 수정본을 반영한다. OA 회사 SSO SDK/router 주입과 비공개 설정을 보존하고 `/workspace`를 운영에 마운트하지 않는다. frontend는 새 소스로 다시 빌드한다.
2. 기존 DB 구조·필수 UNIQUE·009 계정 저장 계약을 읽기 전용으로 확인한다. 기존 계정과 데이터를 유지하고 원본 DDL/포털 초기 설치를 재실행하지 않는다.
3. OA 환경의 `SANITY_ADMIN_USER_ID`에 현재 로그인할 활성 계정의 정확한 user_id를 비공개로 설정한다. 미설정 차단·비활성 관리자 차단을 확인한다.
4. SSO 관리자로 로그인해 프로젝트·블록을 등록한다. 미배정 사용자와 관리자도 전체 프로젝트·report를 조회하는지 확인한다. Welcome의 수정 권한 신청·승인·거절·회수와 회수 후 조회 유지·쓰기 차단을 확인한다.
5. VWP 회사 입력 어댑터에서 등록된 고정 `run.scope`와 실제 report 소속을 연결한다. scoped 결과의 수집·분석·봉인·신뢰된 수신을 검증한다. 자유 project 문자열 비교로 보완하지 않는다.
6. 기존 관련 검사로 미배정 사용자의 전체 조회, SQL 목록 건수/페이지, 미배정 쓰기·관리 차단, 미등록/scope 없는 자료 차단, 배정 회수와 동시 쓰기를 확인한다. 로컬 토큰 회귀와 기존 불변 이력도 유지한다.
7. 변경과 직접 관련된 기존 검사 결과를 기록한다. 실제 IdP·프록시·운영 DB·실제 report 정확성은 별도 사내 검증으로 남긴다. PoR/V2 운영 연결이나 전체 대용량 검증 완료로 표현하지 않는다.

문제가 나면 [비식별 문의 규칙](../INTERNAL_AI_GUIDE.md#6-디버깅이-필요하면-codex에게-문의)에 따라 중단한다. 내부 AI가 guard·DB 제약·API/UI·설정 계약을 임의 수정하지 않는다.

업무 overview의 `tops`·`owners` 배열은 빈 배열로 제공하고 전체 사용자 명단을 조회·표시하지 않는다. 권한 관리는 관리자 화면에서 사용자별로 확인하며, PoR 지정자·판정 작성자·변경 이력의 실제 행위자 이름은 보존한다. 프로젝트 ID·이름은 이 변경에서 수정하지 않는다.

배포는 두 단계로 진행한다. 먼저 중앙 VWP 작업자를 `PROJECT_REUSE`/`BLOCK_REUSE`를 읽는 새 receipt reader로 배포하되, 기존 `PROJECT`/`BLOCK` 이력도 계속 읽는 backward-compatible 상태를 확인한다. 그 reader 준비 후 OA API와 정적 frontend를 같은 릴리스로 배포해 새 receipt를 쓰도록 한다. reader보다 OA emitter를 먼저 배포하면 구버전 작업자가 알 수 없는 이벤트 kind로 이력을 읽지 못할 수 있다. 기존 MEMBER/REVOKE_PROJECT 이력과 모든 불변 이력은 유지하며 추가 DDL·GRANT·운영 이력 일괄 수정은 없다. 회사 어댑터 수정 허용 범위는 확대하지 않는다.
