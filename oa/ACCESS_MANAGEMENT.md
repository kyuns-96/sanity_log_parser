# 운영 SSO 권한·Welcome·관리 화면 계약

기준: 2026-10-22. 운영 진입점은 `/`와 `/api/*`다. `/?page=welcome`에서 프로젝트·블록 권한을 요청하고, `/?page=admin`에서 관리자가 프로젝트·블록을 등록하고 요청을 승인·거절하거나 권한을 회수한다. 별도 합성 앱 `/workspace`와 `/workspace/api/*`는 운영에 마운트하지 않는다.

이 문서는 Codex가 관리하는 공통 구현 계약이다. 사내 AI의 회사 어댑터 허용 범위는 [INTERNAL_AI_GUIDE.md](../INTERNAL_AI_GUIDE.md)를 따른다. 실제 SSO·사내 report·운영 DB 배포 검증을 합성 테스트로 대신하지 않는다.

## 1. 계정과 관리자

- 인증은 기존 `VerifiedIdentity → LocalUser → Actor` 계약을 유지한다. 현재 로그인한 활성 SSO 계정의 `LocalUser.user_id`와 엄격한 009 `SSO_IDENTITY` 행을 확인한다. 표시 이름·이메일·브라우저 입력·신뢰되지 않은 헤더로 권한을 정하지 않는다.
- OA 서버의 비공개 환경변수 `SANITY_ADMIN_USER_ID`에 관리자 한 명의 **정확한 내부 user_id**를 설정한다. 영문·숫자 시작, 영문·숫자·`_.-`로 된 1~100자이며 대소문자를 구분한다. 실제 값은 Git·프런트엔드 `VITE_*`·외부 문의에 넣지 않는다.
- 누락 또는 빈 값이면 운영 권한 관리는 준비되지 않은 상태다. 기존 `AUTHORIZATION_NOT_IMPLEMENTED` 차단을 유지한다. 잘못된 값은 `AUTHORIZATION_CONFIG`로 거부하며 다른 사용자로 대체하지 않는다.
- 환경변수는 인증을 대신하지 않는다. 관리자도 유효한 SSO 세션과 활성 영속 계정이 필요하다. env를 설정해도 계정·배정·검사 데이터가 자동 생성되지 않는다.
- 관리자는 프로젝트·블록 등록과 요청 승인·거절·회수를 할 수 있다. **관리자라는 이유만으로 report 조회·판정·분류 권한이 생기지 않는다.** 본인도 Welcome에서 TOP 또는 OWNER를 요청하며 자기 요청을 승인할 수 있다.
- 회사 SSO 로그인·콜백·세션 폐기와 매핑 저장부의 현장 구현은 [SSO_INTEGRATION.md](SSO_INTEGRATION.md), 계정 행은 [SSO_IDENTITY.md](SSO_IDENTITY.md)를 따른다. 이 기능은 새로운 로그인 방식이나 자동 계정 등록 경로를 추가하지 않는다.

## 2. 사용자 흐름과 역할

1. SSO 로그인 후 내부 계정·활성을 확인한다. 권한 엔진이 준비됐으나 배정이 없으면 Welcome을 연다. 이는 로그인 실패가 아니다.
2. 관리자가 관리 화면에서 고정 프로젝트 ID·표시 이름·설명을 등록하고, 그 아래에 고정 블록 ID·표시 이름을 등록한다. ID는 생성 후 이름과 별개로 유지하며 다른 프로젝트에도 같은 표시 이름을 쓸 수 있다. 블록 ID는 이 레지스트리에서 고유하다.
3. 사용자는 등록 목록에서 TOP의 프로젝트 또는 OWNER의 프로젝트·블록을 골라 사유를 적어 요청한다. 다른 사람의 user_id를 대신 제출하지 않는다.
4. 관리자는 신청자·역할·범위·사유를 확인해 승인 또는 거절하고 사유를 남긴다. 아직 승인되지 않은 요청은 업무 권한이 아니다.
5. 승인 후 현재 배정으로 업무 목록을 조회한다. OWNER는 담당 블록을 검토·수정하고 TOP은 담당 프로젝트를 조회한다.
6. 관리자가 배정을 회수하면 다음 요청부터 최신 권한을 검사한다. 과거 report·사람 판정·작성자·승인/회수 이력은 삭제하거나 덮어쓰지 않는다. 다시 필요하면 새 요청·승인을 진행한다.

| 주체 | 프로젝트·블록 목록 | report 조회 | 판정·분류·추가 근거 변경 | 관리 |
|---|---|---|---|---|
| 활성 SSO 미배정 사용자 | 신청용 ID·이름·소속 목록 | 없음 | 없음 | 본인 요청·결과 확인 |
| TOP | 신청용 등록 목록 | 해당 프로젝트의 등록 블록 | 없음 | 본인 요청·결과 확인 |
| OWNER | 신청용 등록 목록 | 해당 블록 | 해당 블록 | 본인 요청·결과 확인 |
| env 관리자 | 신청용 등록 목록 | 별도 TOP/OWNER가 있을 때만 | 별도 OWNER가 있을 때만 | 등록·승인·거절·회수 |

한 사람은 여러 프로젝트·블록에 배정되거나 TOP과 OWNER를 함께 가질 수 있다. TOP만으로 OWNER 권한을 부여하지 않는다. 프로젝트 생성은 TOP 배정과 별도이며 최소 TOP 1명을 강제로 만들지 않는다. 초기 프로젝트·블록에 업무 결과가 없을 수 있다.

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

기존 `ANALYSIS_BUNDLE` envelope에는 인증된 제출자 정보가 없다. 이 경로는 **신뢰된 VWP 서비스 수신**이며, scope 검증이 업로더의 OWNER 권한을 입증하는 것은 아니다. 사용자 제출 권한은 기존 V2의 검증된 source 신원·승인 계약을 따르며 V2 schema나 SDK 계약을 이번 기능으로 바꾸지 않는다. 전역 큐를 처리하는 `/api/receive`는 SSO에서 관리자 포함 403이다. 수신은 신뢰된 서비스 작업자/운영 CLI가 담당한다.

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
| `POST /api/access/requests` | `{request_id,role:"TOP"\|"OWNER",project_id,block_id,reason}`. TOP의 block_id는 null, OWNER는 등록 블록 ID |
| `GET /api/admin/access` | 관리자 전용 현재 `revision`, 전체 요청·활성 배정·활성 계정의 ID/표시 이름 |
| `POST /api/admin/projects` | `{request_id,project_id,name,description}` |
| `POST /api/admin/projects/{project_id}/blocks` | `{request_id,block_id,name}` |
| `POST /api/admin/requests/{request_id}/decision` | `{command_id,decision:"APPROVE"\|"REJECT",reason,expected_revision}` |
| `POST /api/admin/grants/{grant_id}/revoke` | `{command_id,reason,expected_revision}` |

request status는 `PENDING`, `APPROVED`, `REJECTED`다. grant는 승인된 활성 배정이며 회수 후 현재 grant 목록에서 제외하고 과거 승인·회수 이벤트는 보존한다. ID는 1~100자 고정 형식, 이름은 1~100자, 설명은 최대 1,000자, 사유는 1~2,000자다. 앞뒤 공백·제어문자·미정의 필드를 받아 임의 정규화하지 않는다.

`GET /api/session`은 기존 필드를 유지하고 `is_admin`, `can_read_business`, `can_receive`를 추가한다. `authorization_ready`는 권한 엔진 준비 여부이며 배정 보유 여부가 아니다. 미배정 SSO 사용자도 준비된 엔진에서는 `authorization_ready=true`, `can_read_business=false`, `writable=false`다. SSO의 `can_receive`는 false다. `writable=true`가 모든 bundle의 수정 권한을 뜻하지 않으며 상세 `permissions`와 실제 서버 검사를 따른다.

SSO 상태 변경 요청은 JSON과 `X-Sanity-Request: 1` 헤더가 필요하다. 명시적인 cross-site `Origin`/`Sec-Fetch-Site`는 거부하고 CORS를 개방하지 않는다. Origin은 검증된 Host와 비교하며 임의 forwarded header를 신뢰하지 않는다. 회사 `/auth/logout`과 콜백의 CSRF/세션 검증은 계속 회사 router의 책임이다.

SSO 목록은 허용 scope를 **SQL count·정렬·페이지 제한 전에** 적용한다. 상세·원문·다운로드·검토 이력·근거 요청/응답·AI 작업/구성원/수신도 실제 소속 ID를 검사한다. 다른 범위·scope 없음은 `404 RESOURCE_NOT_FOUND`, 조회 가능한 TOP의 수정 시도·비관리자의 관리 시도는 `403 FORBIDDEN`이다. 범위 거부만으로 정상 SSO 세션을 지우지 않으며 이전 업무 자료를 화면에 남기지 않는다.

업무 요청은 공유 transaction advisory lock을 검사부터 사용 완료까지 유지하고, 레지스트리/요청/승인/회수 변경은 같은 전역 잠금을 배타적으로 잡는다. `READ COMMITTED`에서 잠금 후 최신 계정·배정을 읽어, 회수와 이미 진행 중인 요청의 순서를 직렬화한다. 회수 완료 후 시작한 요청이 이전 배정으로 실행되지 않게 한다. 새 SSO 검토 작성자는 표시 이름만으로 식별하지 않고 안정 `user_id`를 기록한다.

## 6. 사내 반영 순서와 완료 기준

1. 사용자가 Codex 공통 수정본을 반영한다. OA 회사 SSO SDK/router 주입과 비공개 설정을 보존하고 `/workspace`를 운영에 마운트하지 않는다. frontend는 새 소스로 다시 빌드한다.
2. 기존 DB 구조·필수 UNIQUE·009 계정 저장 계약을 읽기 전용으로 확인한다. 기존 계정과 데이터를 유지하고 원본 DDL/포털 초기 설치를 재실행하지 않는다.
3. OA 환경의 `SANITY_ADMIN_USER_ID`에 현재 로그인할 활성 계정의 정확한 user_id를 비공개로 설정한다. 미설정 차단·비활성 관리자 차단을 확인한다.
4. SSO 관리자로 로그인해 프로젝트·블록을 등록한다. 본인과 일반 사용자가 Welcome에서 신청하고 관리자가 승인·거절·회수를 검증한다. 관리자에게 자동 report 권한이 없는지도 확인한다.
5. VWP 회사 입력 어댑터에서 등록된 고정 `run.scope`와 실제 report 소속을 연결한다. scoped 결과의 수집·분석·봉인·신뢰된 수신을 검증한다. 자유 project 문자열 비교로 보완하지 않는다.
6. 작은 합성 결과로 미배정·TOP·OWNER·다른 블록 접근, SQL 목록 건수/페이지, report·근거·AI job ID 우회, 권한 회수와 동시 요청, 교차 블록 과거 사례 차단을 확인한다. 로컬 토큰 회귀와 기존 불변 이력도 유지한다.
7. 변경 전용 테스트와 기본 `quick` 프로필의 실제 실행·통과·실패·skip 수를 기록한다. 실제 IdP·프록시·운영 DB·실제 report 정확성은 별도 사내 검증으로 남긴다. PoR/V2 운영 연결이나 전체 대용량 검증 완료로 표현하지 않는다.

문제가 나면 [비식별 문의 규칙](../INTERNAL_AI_GUIDE.md#6-디버깅이-필요하면-codex에게-문의)에 따라 중단한다. 내부 AI가 guard·DB 제약·API/UI·설정 계약을 임의 수정하지 않는다.
