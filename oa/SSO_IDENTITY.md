# SSO 계정 저장 계약 — 009

2026-10-22 전달된 사내 확인을 기준으로 한다. LoginId는 이름 변경에도 바뀌지 않고 퇴사 후 다른 사람에게 재사용되지 않는다. 한 배포는 하나의 고정 issuer만 사용하며, 해당 IdP에서 검증된 전사 계정을 등록 대상으로 한다. 프로젝트·블록·TOP 권한 부여는 별도다.

기존 앱 계정과 업무 데이터는 보존하며 계정 이전·whitelist 승계는 별도 검토한다. 이미 009가 적용된 DB에서 아래의 초기 0행 설치 절차를 다시 실행하지 않는다. 회사 계정과 이 앱의 등록 계정은 구분한다. 실제 배포 상태·계정 및 데이터 현황은 공개 문서에 기록하지 않는다.

이 문서는 공통 저장 계약이다. [009 SQL](../db/009_sso_identity.sql)은 저장 가능한 구조를 제공한다. **이 공통 저장소는 회사 SSO 어댑터·DB 사용자 저장/조회·ACS 등록 호출의 운영 구현을 아직 제공하지 않는다.** 사내 구현 보고와 실제 배포·검증 결과는 별도로 검토한다. 운영 Welcome·관리·TOP/OWNER 계약은 [ACCESS_MANAGEMENT.md](ACCESS_MANAGEMENT.md)를 따른다. 권한 엔진 미설정 시 `AUTHORIZATION_NOT_IMPLEMENTED`를 유지하며, 계정 등록 자체로 업무 권한을 부여하지 않는다.

## 1. 식별과 저장 위치

- 위치: 기존 OA 전용 `sanity_oa_data`, `record_type='SSO_IDENTITY'`. 테이블·역할·권한 추가 없음. VWP에는 계정 정보를 전송하지 않는다.
- 외부 계정은 계속 `(issuer, subject)`로 식별한다. 매 요청에서 검증된 issuer, 회사 설정의 고정 issuer, 저장된 `payload.issuer`가 모두 같아야 한다. 설정 issuer와 검증 응답이 다르면 `UNAUTHORIZED`, 저장된 행의 issuer/subject/ID가 다르거나 손상되었으면 `AUTH_DIRECTORY_UNAVAILABLE`로 거부하고 자동 수정/재매핑하지 않는다. issuer 교체는 별도 관리 절차다.
- 이 배포에서 `subject`는 검증된 LoginId 원문이다. `workspace_key = subject`, `LocalUser.user_id = subject`. 영문·숫자 시작, 영문·숫자·`_.-`만 포함하는 1~100자여야 한다. 소문자화·공백 제거·이름/이메일 대체를 하지 않는다. 형식이 다르면 거부한다.
- `record_id = common.sanity_db.stable_uuid("sso-identity-v1", "SSO_IDENTITY", subject)`. 기존 구현의 UUIDv5 namespace와 canonical JSON을 그대로 사용한다. `report_id`가 아니다. SQL은 UUID 자료형을 검사하며, 결정적 ID 생성과 재조회 시 일치 검증은 향후 저장부 책임이다.
- 고정 issuer가 다른 서비스/여러 IdP를 이 저장소에 동시에 연결하는 정책은 지원하지 않는다. 필요하면 subject-only 키를 재사용하지 말고 공통 계약을 다시 설계한다.

## 2. 행과 JSON

`status='RECORDED'`. `project`, `run_id`, `bundle_id`, `review_unit_id`, `review_revision`, `request_id`, `entity_key`, `classification_revision`, `payload_bytes`, `payload_sha256`는 NULL이다. 참조 컬럼은 005의 3개, 006의 3개, 007의 4개, **총 10개 모두 NULL**이다. 기존 종류의 kind/scope 조건은 보존한다.

`payload`에는 다음 네 키만 저장하며 OA 앱 계정으로 수정하지 않는다.

```json
{
  "issuer": "https://synthetic-idp.example.test/entity",
  "subject": "synthetic.user01",
  "display_name": "합성 사용자",
  "first_seen_at": "2026-10-22T00:00:00.000000Z"
}
```

issuer는 1~512자, display_name은 1~100자의 유효한 문자열이다. 기존 `VerifiedIdentity`/`LocalUser`의 공백·제어문자 검사를 유지한다. 표시 이름은 검증된 claim을 사용하고 없으면 subject를 사용한다. claim이 있지만 잘못된 형식이면 임의 절단/정규화하지 않고 오류로 처리한다. 기존 행의 표시 이름을 로그인마다 덮어쓰지 않는다. 이름 변경 동기화는 별도 후속 작업이다.

`state`에는 다음 두 키만 저장한다.

```json
{"enabled": true, "last_seen_at": "2026-10-22T00:00:00.000000Z"}
```

`enabled`는 JSON boolean이어야 한다. 누락/null/문자열/숫자는 활성으로 취급하지 않는다. 신규 계정만 승인된 등록 경로에서 true로 생성한다. 기존 false는 로그인으로 true가 되지 않는다.

시각은 UTC `YYYY-MM-DDTHH:MM:SS.ffffffZ` 형식이며 실제 유효한 날짜여야 한다. 예: `datetime.now(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")`. `last_seen_at >= first_seen_at`을 검사한다. DB의 JSON 문자열 검사는 SQL NULL로 우회할 수 없게 한다. issuer 신뢰성·인증 성공·사용자 권한은 SQL이 판단하지 않는다.

## 3. 향후 앱 구현의 고정 순서

1. 회사 어댑터가 기존 SAML 서명·issuer/audience/recipient·시간창·nonce 검증을 완료한다. 실패 콜백은 DB 등록/최근 로그인 갱신을 하지 않는다.
2. 고정 issuer와 LoginId 형식을 확인한다. 검증된 전사 계정만 신규 등록 대상으로 삼는다. SSO 세션 확인·source 제출자 조회는 자동 등록 진입점이 아니다.
3. 성공한 로그인 처리에서 기존 부분 UNIQUE `(record_type, workspace_key) WHERE workspace_key IS NOT NULL`을 사용해 INSERT한다. 충돌 처리는 해당 키에 한정한 `ON CONFLICT (record_type, workspace_key) WHERE workspace_key IS NOT NULL DO NOTHING` 후 **별도 문장으로 재조회**한다. 무관한 PK 충돌은 숨기지 않는다. 재조회 결과가 없거나 record_id/issuer/subject/JSON 계약이 다르면 거부한다.
4. 기존 계정의 enabled를 확인한다. false는 `ACCOUNT_DISABLED`, 잘못된 행/저장소 장애는 `AUTH_DIRECTORY_UNAVAILABLE`로 처리한다. 재등록·자동 활성화·issuer 덮어쓰기를 하지 않는다.
5. `last_seen_at`은 세션 발급 시 갱신한다. 같은 행을 잠그고 최신 state를 읽어 enabled를 재확인하며, 시각은 기존 값보다 뒤로 가지 않게 한다. `state/version/updated_at`만 갱신하고 enabled와 first_seen_at을 보존한다. 일반 API 요청의 `resolve()`는 읽기만 한다.
6. 로그인 상태의 매 요청은 영속 계정을 다시 조회해 issuer·subject·enabled를 확인한다. source 제출 신원도 같은 매핑을 읽으며 신규 계정을 만들지 않는다. `None`은 `ACCOUNT_NOT_LINKED`, false는 기존 인증 서비스가 `ACCOUNT_DISABLED`로 처리한다.

DB commit과 메모리 세션 발급의 실패 경계·정확한 호출 메서드는 앱 구현 단계에서 Codex가 정한다. 최초 배포 검증은 신규 등록·동시 중복 등록·DB/세션 발급 실패 처리를 대상으로 한다. 등록 후 계정의 enabled를 false로 바꾼 합성 사례에서는 신규 로그인과 기존 세션 요청을 모두 거부하고 로그인으로 재활성화하지 않는지 확인한다. 이는 기존 계정 이전 검사가 아니다. 향후 저장부는 009 구조/제한 계정을 읽기 전용으로 확인하고 준비되지 않은 경우 실패해야 하며 자동 DDL이나 whitelist fallback을 사용하지 않는다. 009 적용 성공만으로 위 앱 구현이 완료된 것은 아니다. 내부 AI가 독자 저장부를 만들거나 공통 인증/API 보호를 해제하지 않는다.

## 4. 설치와 사내 인계

원본 009는 owner/DBA용 트랜잭션 SQL이다. 단일 문장 회사 포털에는 [포털 안내](../db/portal/README.md#008까지-적용한-db에서-009만-준비)를 사용한다. 기존 네 테이블 0행·쓰기 중지 조건을 유지하며, 새 snapshot과 새 bundle을 만든다. 008 완료 상태는 checkpoint 54, 추가 009는 55번 단일 ALTER다. 이전 54문장을 다시 실행하지 않는다.

포털 실행본 준비 환경에는 `db/` 폴더를 기존 구조대로 반영한다. 도구가 form·002~009·003 권한 SQL의 hash를 확인하므로 runbook/plan 두 파일만 복사하지 않는다. `build_reference.py`는 Codex 개발 검증용이며 사내 운영 DB에 실행하지 않는다.

사내 OA 담당은 제공된 공통 파일을 반영하고 읽기 전용 검증 자료와 어댑터 호출 계약을 준비한다. 실제 DDL은 사용자/승인 운영 담당자가 적용한다. 실측 스키마·원문 인증 응답·토큰·인증서·사내 SDK 코드는 외부로 전달하지 않는다. 예상 밖 오류 시 기존 문의 절차를 따른다.

## 5. 업무 권한과의 분리 — 2026-10-22

`SSO_IDENTITY`에는 역할·프로젝트·블록 필드를 추가하지 않는다. 기존 네 키의 payload와 두 키의 state, 결정적 record_id, 매 요청 enabled 검사를 유지한다. 운영 관리자 `SANITY_ADMIN_USER_ID`는 검증된 활성 user_id와 정확히 일치해야 하며 계정 생성·자동 활성화·자동 report 접근을 수행하지 않는다.

프로젝트·블록은 기존 `WS_PROJECT`/`WS_BLOCK`, 요청·승인·회수는 별도 namespaced `WS_CONFIG` 불변 이벤트로 관리한다. synthetic `WS_USER`나 표시 이름으로 계정을 합치지 않는다. TOP/OWNER는 사용자의 요청을 관리자가 승인해야 하며 관리자 본인의 자기 승인도 허용한다. 상세 [운영 권한 계약](ACCESS_MANAGEMENT.md) 반영에는 추가 DDL·009 재적용이 필요하지 않다.
