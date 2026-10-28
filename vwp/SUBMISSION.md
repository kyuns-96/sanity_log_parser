# report 제출 실행·연동 안내

**2026-10-27 현재 환경:** 공유 폴더 없이 [토큰 없는 HTTPS 제출](HTTPS_SUBMISSION.md)을 사용한다. CLI schema 3·bridge schema 4, 원본은 010 이후 VWP private DB 보관이며 파싱할 때만 임시 복원한다. 제출용 토큰이나 회사 인증 SDK를 준비하지 않는다. OA에 로그인한 현재 OWNER가 범위를 확정한 뒤 보고서를 공개한다. 아래 파일시스템·신원/승인 어댑터 설치는 기존 schema 1/Bearer 호환 배치 설명이다.

사용자 CLI, VWP 내부 snapshot 인계/영속 보관, V2 분할 송수신, OA source 페이지와 접수 응답을 구현했다. **실제 회사 신원·예상 범위/승인·report 경로 SDK와 사내 파일시스템/운영 DB는 연결하지 않았다.** 아래 일반 실행은 그 준비와 승인된 배포 후 사용한다. SSO 업무 API와 CORPORATE 모델의 기존 차단은 유지한다.

2026-10-23 사용자 결정으로 접수·원본 보관·파싱·검증·DB 전송은 **VWP 중앙 수집 서비스**가 맡는다. 사용자 CLI에는 DB·모델 계정이 없고 AI 분류·판정은 OA에서 실행한다. 중앙 서비스는 기존 `vwp_bridge.submission_worker`를 사용하며 별도 장비가 필수는 아니다. [Docker 운영 설정](../vwp_bridge/docker/README.md#4-중앙-수집-서비스-실행--oa-ai-운영-경로)은 `submission` profile과 private JSON·spool/archive 마운트를 제공한다.

설계는 [SUBMISSION_DESIGN.md](SUBMISSION_DESIGN.md), 원천 ID·PRE 재사용은 [SOURCE_COLLECTION_DESIGN.md](SOURCE_COLLECTION_DESIGN.md), 수정 소유권은 [사내 AI 가이드](../INTERNAL_AI_GUIDE.md)를 따른다.

## 1. 사용자의 호출

배포 담당자가 Git release의 `.venv`와 `bin/sanity`를 배치하고 `/opt/sanity/current/bin`을 사용자의 PATH에 등록한다. 진입점은 실제 release 경로를 한 번 고정하고 그 release의 `.venv/bin/python`이 있으면 사용한다. 원본 report를 읽을 수 있는 VWP 터미널의 어느 작업 폴더에서든 실행한다.

```bash
sanity submit --project PROJECT_A --block CPU --net-ver NET_01 --revision preA_postB-BE --layout post --eco eco02
sanity status 11111111-1111-4111-8111-111111111111
sanity status 11111111-1111-4111-8111-111111111111 --wait --timeout 60
sanity retry 11111111-1111-4111-8111-111111111111
```

- `submit --dry-run`은 실제 포함 범위·예상/발견/누락·프로필/승인 상태만 확인한다. 정상 제출은 미리보기 뒤 `y` 확인을 요구하며 비대화형 자동 승인은 제공하지 않는다.
- `--eco`는 이번 대상이다. 회사 provider가 반환한 전체 REVISION 예상 목록/PRE를 파일 존재 여부로 축소하지 않는다. PRE 단독 제출은 `--layout pre`로 실행하고 `--eco`를 생략한다.
- 종료 코드 0과 `LOCAL_QUEUED`는 VWP 내부 폴더 인계 완료다. `status`의 `OA_STORED`가 OA 반영 완료이며 `review_readiness=BLOCKED`이면 사유를 해결해야 한다. `status --wait` timeout은 코드 3과 마지막 상태를 반환하며 제출을 취소하지 않는다.
- `retry`는 기존 UUID/봉인 바이트만 재사용한다. 원본 파일이 바뀌어도 이미 만든 snapshot을 바꾸지 않는다. 새 report/실제 재검사는 새 `submit`을 사용한다. 결과가 불명확한 AI 호출을 재실행하는 기능은 아니다.
- bridge가 접수한 뒤의 재개는 private DB와 영속 원본을 사용한다. 공유 `.ready` 사본이 사라져도 parsing/부분 outbox를 재개하고, DB 확정 뒤 쓰지 못한 receipt를 다시 작성한다. DB 등록 직전 중단된 archive도 검증 후 접수한다. 신원별 inbox/receipt·인증된 mount와 private archive 자체는 유지해야 한다.
- 설정을 별도 검토 경로에 둘 때는 전역 옵션을 명령 앞에 둔다: `sanity --config /approved/path/vwp-submit.json submit ...`.

## 2. 설치·설정·인프라

| 역할 | 제공한 파일 / 배포 위치 |
|---|---|
| VWP 사용자 CLI | [bin/sanity](../bin/sanity), [submission_cli.py](submission_cli.py) |
| VWP 설정 | [submission.example.json](submission.example.json) → `/etc/sanity/vwp-submit.json` |
| VWP-bridge 설정 | [submission.example.json](../vwp_bridge/submission.example.json) → `/etc/sanity/vwp-bridge-submit.json` |
| VWP 내부 인계 | `/srv/sanity/vwp-submit/inbox/<principal-key>/` 및 `receipts/<principal-key>/` |
| VWP 영속 원본 | `/var/lib/sanity/vwp-bridge/submissions/<submission-id>/` |
| DB 증분 | [008_submissions.sql](../db/008_submissions.sql), 기존 007까지 적용한 뒤 DBA가 검토·실행 |

설정 파일은 **root 소유이며 group/other 쓰기가 없어야 한다.** 예제의 `service_uid=null`은 의도적으로 실행 불가다. 실제 비관리자 bridge OS 계정의 숫자 UID, 프로필, node ID, 실제 마운트와 storage ID를 사내에서 설정한다. DB 자격증명은 기존 bridge root `.env`에만 두며 사용자 CLI 설정에는 넣지 않는다. 새 JSON 설정은 기존 `.env` 키를 대체하지 않는다.

제출 파일시스템은 양쪽 VWP 환경에 **동일한 내용**으로 마운트해야 한다. 배포 담당자는 다음을 준비한다.

1. service 소유 `storage.json`: `{"schema_version":1,"storage_id":"실제 설정과 같은 ID"}`. 일반 사용자 쓰기 금지, 필요한 읽기 허용.
2. 검증된 신원별 inbox·receipt 디렉터리. `<principal-key>`는 `SHA256(canonical_json([issuer, subject]))`이며 담당자가 임의 username으로 만들지 않는다.
3. inbox의 소유 UID는 검증된 사용자 UID, other 권한은 없음. 그 사용자는 작성하고 bridge service는 읽기/접근할 수 있도록 사내 group/ACL을 설정한다. 클라이언트의 하위 디렉터리는 0750, 파일은 0640이므로 **bridge 읽기 권한이 상속되는 group/ACL**이 필요하다.
4. receipt는 service만 쓰고 본인만 읽게 한다. 일반 사용자들이 같은 primary group이라고 해서 그 group 전체에 타인의 receipt 읽기를 부여하지 않는다. 파일은 0640으로 작성하므로 실제 사용자별 ACL 동작을 확인한다.
5. bridge 원본 보관 루트는 service 소유 0700 영속 volume이며 service의 실제 UID가 설정과 일치해야 한다. 재시작/백업 후에도 같은 node가 접근하도록 배포한다.
6. 실제 파일시스템에서 atomic rename·flush·권한·접속 신원 검증이 되는지 확인한다. CLI의 marker/UID 검사만으로 인증된 마운트를 증명하는 것은 아니며 회사 authority가 그 연결을 검증해야 한다.

이 프로그램은 mount·OS 계정/ACL·관리자 권한·DB DDL을 자동 생성하지 않는다. 미구비 시 명시적으로 실패하고 사용자 DB 로그인·SCP/HTTP·OA 파일 마운트로 우회하지 않는다. 초기 구현은 인계 사본과 업무 원본을 자동 삭제하지 않는다.

## 3. 세 사내 AI가 연결할 메서드

| 담당 | 허용된 회사 구현 | 계약 |
|---|---|---|
| VWP | [corporate_submission.py](integration/corporate_submission.py)의 `CorporateSubmissionInput.prepare` | `target → SubmissionInput(plan, reports)` 반환. plan은 기존 V2 또는 보존 선언 시 V3, reports는 `reports/... → 사내 원본 Path` 대응. 예상 목록과 실제 파일 발견을 분리하고 예상 밖 파일도 보존. 원본/DB 쓰기 없음 |
| VWP | 같은 파일의 `CorporateSubmissionAuthority.current/inbox_owner/authorize/approval_valid`와 명시적 factory | 실제 caller/마운트 신원→`VerifiedSubmitter`; 최신 블록 담당 확인; 실제 승인 참조/approver/범위 digest 검증. CLI 확인을 승인으로 바꾸지 않음 |
| VWP-bridge | [corporate_submission.py](../vwp_bridge/integration/corporate_submission.py)의 `authenticate_bearer/resolve_actor/authorize/approval_valid` (HTTPS), 기존 `inbox_owner` (폴더 경로) | HTTPS 자격증명 검증·최신 계정 대응, 또는 인증된 마운트 신원. 최신 블록 담당/승인 확인. 입력 actor나 디렉터리명만 신뢰하지 않음 |
| OA | [corporate_submission.py](../oa/integration/corporate_submission.py)의 `CorporateSourceIdentity.resolve` | 신뢰한 VWP assertion의 issuer/subject를 OA SSO와 같은 영속 사용자 ID로 대응. importer가 현재 활성/블록 담당을 다시 검사 |

필요 import·승인된 SDK client 주입 생성자와 자기 역할의 전용 wrapper/모의 테스트만 허용한다. VWP는 `company_plan/`, bridge 신원 wrapper는 `company_submission/`, OA는 `company_sso/`에 둔다. parser는 기존 별도 회사 registry 계약을 따른다. 허용 범위 밖 공통 파일·SQL·worker·API/UI를 내부 AI가 수정하지 않는다.

현재 제공된 회사 메서드는 `*_NOT_CONFIGURED` 또는 `AUTHORIZATION_NOT_IMPLEMENTED`로 실패한다. 운영 코드가 합성 신원/fixture로 fallback하지 않는다. 실제 SDK·새 의존성·신원/실행 ID·회사 프로필 사실은 사내에서 검증하고 비밀/실제 report는 외부에 보내지 않는다.

## 4. 실행 가능한 V2 데이터 계약

[common/sanity_submission.py](../common/sanity_submission.py)의 타입과 `validate_plan`을 기준으로 한다. 기존 `CollectionPlan`/`get_plan()`과 `vwp.main collect`는 schema 1을 유지하며 V2를 받지 않는다.

- V2의 최상위 키는 `schema_version=2`, `collection`, `inventory`, `approval`다. `collection`에는 namespace, project/block, NET_VER, 제출 REVISION/layout/대상 ECO/참조 PRE, stage, 프로필 ID/개정이 있다.
- `inventory.units`는 기존 item/조건/parser/규칙/실행 상태와 `family`, `source`를 가진다. `source`는 NET_VER·실제 REVISION·PRE/POST·ECO/null·mode·실제 `execution_ref`·**권위 있는 정수 `execution_order`**·source_stage·tool/version이다. 실제 실행 순서를 전송 시각/mtime/ECO 문자열 정렬로 추정하지 않는다.
- 선언 승인 digest는 unit identity 전체와 프로필/범위에 결합하며 실제 파일/실행 상태/실행 ID/전달자는 제외한다. `approval={scope_sha256, reference, actor, approved_at}`은 실제 회사 참조 검증까지 통과해야 한다. input의 `confirmed=true`만으로 검토를 허용하지 않는다.
- 실행 단위로 기존 checked parser를 호출해 private RUN/UNIT/FILE/EVIDENCE에 전체 근거를 저장한다. source 파싱은 AI의 initial group을 미리 확정하지 않는다. parser implementation/규칙이 바뀌면 별도 분석 ID다.
- SOURCE_MANIFEST/PART/COMMIT와 SOURCE_RECEIPT는 schema 2와 `protocol=source-collection-v2`를 명시한다. 데이터 조각은 envelope 포함 압축 전/후 4MiB, 최대 1,000 records다. 전체 예상/상태별 건수·원천 ID·순서·해시를 대조하고 OA에서는 staging 완료 후 한 번에 공개한다.
- 대용량 검토의 전체 context는 기존 1,000,000 records와 별도 256MiB의 명시적 예산을 적용한다. metadata/로그 페이지는 전체를 materialize하지 않는다. 초과 자료를 자르지 않고 `SOURCE_CONTEXT_LIMIT`로 알린다. 이 수치는 평균이나 업무상 상한이 아니다.
- 같은 원천 실행의 다른 바이트/순서와 같은 UUID의 다른 내용은 충돌이다. 여러 POST의 같은 PRE·재전달은 같은 원본/보고서를 참조한다. 새 실행은 바이트가 같아도 별도다. 각 ECO의 기본 목록/집계는 검증된 실행 순서와 VWP 분석 생성 시각의 최신 결과를 사용한다. `include_history=true`로 과거 report를 조회할 수 있으며 근거/판정은 보존한다.
- 회사 parser의 예상하지 못한 예외/반환 계약 오류는 고정 `PARSER_*` 코드로 거부한다. 자동 재호출·부분 봉인/전달을 하지 않는다. 사내 AI는 가이드에 따라 문의하고, 수정/검토 후 새 제출에서 확인한다.

### 4.1. 선택형 보존 근거 plan 3

full.rpt/SDC 같은 비-unit 원본은 [PRESERVED_SOURCES.md](PRESERVED_SOURCES.md)의 `inventory.preserved_files`로 명시한다. 이때 plan/collection manifest는 3, source producer는 `postgresql-source-2.3`이며 HTTP/DB envelope는 기존 2다. 파일의 역할·정확한 unit/실행 귀속을 범위와 hash에 결합한다. CLI preview와 OA OWNER 화면은 보존 파일을 따로 보여 주며 선언 없는 파일·기존 누락 차단은 유지한다. OA/bridge 호환본 선배포 후 새 선언을 사용한다. AI에는 검증된 메타데이터가 전달되며 본문은 아직 제공되지 않는다.

## 5. 서버 작업자와 OA 조회

각 서버의 저장소 루트에서 **자기 작업자**를 실행한다. 실제 운영 시작/SQL 적용은 사용자/배포 담당자·DBA 절차로 관리한다.

```bash
# VWP-bridge: 제출 폴더 접수 + source outbox/접수 응답 처리
.venv/bin/python -m vwp_bridge.submission_worker --config /etc/sanity/vwp-bridge-submit.json --once
.venv/bin/python -m vwp_bridge.submission_worker --config /etc/sanity/vwp-bridge-submit.json --interval 2
```

```bash
# OA: source V2 큐 수신 + 검증/원자 반영/접수 결과 응답
.venv/bin/python -m oa.submission_receiver --once
.venv/bin/python -m oa.submission_receiver --interval 2
```

기존 `vwp_bridge.main work`, `oa.main work`는 legacy 분류/근거 작업용이며 이 두 새 작업자를 대신하지 않는다. OA importer는 사용자/프로젝트/블록을 자동 생성하지 않으므로 사내의 검증된 관리 데이터/배정을 먼저 준비한다. 기존 SSO 업무 차단을 해제하거나 합성 사용자를 회사 계정으로 쓰지 않는다.

Docker 중앙 서비스는 `vwp_bridge/docker/compose.yaml`과 `compose.submission.yaml`의 `vwp-submission-worker`를 사용한다. 기존 `vwp-worker`는 별도 legacy 선택 경로다. OA AI 운영에는 VWP 모델 설정이나 legacy worker가 필요하지 않다. 컨테이너의 실제 UID와 JSON의 `service_uid`·인증된 제출 저장소 ACL을 함께 맞추며, 오류 시 자동 재시작 대신 기존 중단·문의 절차를 따른다.

OA API는 기존 인증/CSRF/블록 범위를 적용한다.

- `GET /workspace/api/blocks/{block_id}/submissions`: 소속/접수 상태·차단 사유·이동 링크의 페이지 조회. issuer/subject·원문·사내 경로를 응답하지 않는다.
- 기존 versions/reports API: 수신한 PRE/POST·ECO와 최신 item/mode 결과. source version payload를 덮어쓰지 않고 ECO를 조회한다. PoR은 수신으로 변경되지 않는다.
- 기존 report 상세는 V2 header만 반환하고, workbench/records API가 전체 로그를 페이지로 보여준다. 검증된 전체 source는 기존 두 단계의 분류·사람 확정·그룹 판정·사례 재사용에 연결된다. 회사 모델 SDK의 CORPORATE job 연결은 별도다.
- byte 동일 원본 report 파일은 VWP에만 보관한다. V2의 원본 다운로드 요청은 `SOURCE_FILE_ON_VWP`이며 빈 파일이나 합성 원문을 반환하지 않는다. OA의 마스킹된 전체 추출 로그와 VWP 원본 파일을 구분한다. 기존 legacy EVIDENCE_REQUEST의 bundle 계약을 V2 원본 파일 요청으로 재해석하지 않는다.

정규화 개정은 [PROTOCOL.md](../PROTOCOL.md#정규화원문-위치전송-개정)를 따른다. OA 수신기를 먼저 갱신하고 VWP를 갱신한다. `SUBMISSION_CONTRACT_MISMATCH`로 거부된 구버전 PARSING 제출은 archive와 불변 outbox를 남기고 새 UUID로 다시 제출한다. 기존 UUID의 packet을 편집하지 않는다. UTF-8 실패 원문은 VWP에서 보관하며 OA에는 마스킹된 UNKNOWN 근거를 전달한다.

## 6. 합성 검증

회사 자료/DB endpoint를 받지 않는 별도 도구가 새 socket 전용 PostgreSQL과 합성 입력을 만들고 실제 제출·분할 송수신·OA 마지막 로그·receipt·PoR 보존을 확인한 뒤 DB를 종료한다.

```bash
.venv/bin/python tools/demo_submission.py --pg-bin /path/to/postgresql/16/bin --rows 10000
.venv/bin/python tools/demo_submission.py --pg-bin /path/to/postgresql/16/bin --rows 100000 --padding 250
```

큰 예시는 전체 전달이 64MiB를 넘더라도 한 메시지 예산 안에서 모든 로그를 전달하는 시험이다. 실제 크기는 실행 결과로 확인하며 운영 성능을 보장하지 않는다. 결과는 `artifacts/submission-demo-<rows>.json`에 남긴다. `devtools/sanity_submission_fixture.py`는 이 시험에서만 명시적으로 주입하며 회사 명령/설정이 자동 선택하지 않는다.

일반 단위 시험은 `.venv/bin/python -m unittest tests.test_submission_contracts -v`, 실제 PostgreSQL 회귀는 [기존 독립 mock runner](../db/tests/run_mock.py)를 사용한다. 구체적인 결과와 범위는 [REVIEW.md](../REVIEW.md)에 기록한다.
