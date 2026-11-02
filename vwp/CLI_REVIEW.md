# VWP 터미널에서 사람의 로그 분류·그룹 판정

웹의 report 검토 화면에서 **VWP CLI 검토**를 열어 연결키를 발급한 뒤, VWP에서 `sanity review`를 사용합니다. 검색·페이지 조회, 개별 로그의 전체 저장 본문 조회, 그룹 이동·분리·병합·이름 수정, 분류 근거 작성·확정, 그룹별 PASS/FAIL/보류를 지원합니다. 저장 결과는 웹과 같은 불변 분류/판정 이력에 반영됩니다.

AI의 최초 분류는 기존 OA AI 작업 경로를 사용합니다. CLI는 사람이 그 결과를 검토·수정하는 경로이며, 새 AI 호출이나 회사 parser를 구현하지 않습니다. 실제 회사 입력·SSO·권한·모델 품질의 검증은 별도입니다.

## 처음 연결하기

아래 배포 준비가 끝난 뒤 로그인한 현재 블록 OWNER가 report의 **VWP CLI 검토 → CLI 연결키 발급**을 누릅니다. 표시된 명령을 VWP 터미널에서 실행하고, 별도로 표시된 연결키를 비표시 입력란에 붙여 넣습니다. 연결키를 명령 인자나 환경 변수에 넣지 않습니다.

```sh
sanity review connect SESSION_UUID --expires-at EXPIRY_UNIX_SECONDS
sanity review shell
```

기존 HTTPS 제출 설정 `/etc/sanity/vwp-submit.json`을 사용합니다. 다른 설정은 `sanity --config /path/to/vwp-submit.json review …`로 지정합니다. CLI 연결키는 제출 토큰·SSO 쿠키·AI 모델 토큰과 별개이며, 제출용 토큰을 요구하거나 사용하지 않습니다.

연결은 발급한 사람·report·원본 hash에 한정하고 최대 8시간 유효합니다. 명령을 처리할 때 활성 SSO 계정과 현재 OWNER 권한을 확인합니다. 다른 report를 대상으로 바꾸거나 제출 OS 계정을 검토자로 지정할 수 없습니다. 같은 사람의 report별 활성 연결은 최대 8개입니다. 웹의 연결 목록에서 회수하거나 터미널의 `close` 명령으로 종료할 수 있습니다. TOP과 미확인 제출자는 연결을 발급할 수 없습니다.

로컬에는 서명 자격과 현재 개정·미완료 요청을 기본 `$XDG_STATE_HOME/sanity/review/` 또는 `~/.local/state/sanity/review/`에 저장합니다. 디렉터리는 본인 소유 0700, 파일은 0600이며 링크·다중 hardlink·다른 사용자 권한이 있는 상태 파일을 거부합니다. 동시에 두 프로세스가 같은 상태를 수정할 수 없습니다. 여러 report를 병행할 때는 `sanity review --state /본인/전용/디렉터리 …`로 각각 분리합니다.

## 많은 로그를 읽기

아래 명령은 shell 안에서 그대로 쓰거나 앞에 `sanity review`를 붙여 실행합니다. `help`, `groups --help` 등으로 인자를 확인할 수 있습니다.

```text
refresh
groups --status UNREVIEWED --limit 25
groups --search clock --offset 25
groups --rationale-status UNRESOLVED
records --group GROUP_ID --limit 25
records --group GROUP_ID --search synchronizer --offset 25
record RECORD_ID
record RECORD_ID --offset 16384
group GROUP_ID
history --offset 0 --limit 25
```

그룹/로그 목록은 전체 검색 결과 건수·현재 범위·다음 offset을 표시합니다. 검색 결과가 현재 페이지에 없다고 누락 처리하지 않습니다. 수정 선택에는 화면 순번 대신 안정 ID를 사용합니다. 로그 목록의 `raw_display_truncated=true`는 4096자 미리보기이며 전체 본문을 읽었다는 뜻이 아닙니다. `record`는 저장한 로그 본문을 문자 offset별로 읽고 전체 길이와 다음 위치를 표시합니다. 한 번에 최대 32768자를 반환하며 마지막 조각까지 읽으면 전체 본문을 확인할 수 있습니다. 위치·필드·redacted 표식·원본 hash 등 저장된 출처를 함께 표시합니다.

이 본문은 OA에 전달된 추출 로그입니다. 마스킹된 로그를 VWP의 원본 파일과 동일하다고 표시하지 않습니다. 전체 원본 파일은 기존 VWP 보관 경로를 유지합니다. 터미널 제어 문자는 화면 제어 명령으로 실행되지 않도록 이스케이프합니다. 스크립트용 JSON 출력은 `sanity review --json records …`로 받습니다. 기존 API와 같은 페이지당 최대 100건을 사용하며, 큰 결과를 잘라 전체 성공으로 보고하지 않습니다.

## 분류 수정과 확정

모든 수정은 마지막 조회 개정에 묶습니다. 웹이나 다른 CLI에서 바뀌면 `REVISION_CONFLICT`로 거부하며 자동으로 최신 개정을 끼워 넣어 재시도하지 않습니다. `refresh`로 전체 상태를 다시 확인한 뒤 다시 판단합니다.

```text
rename GROUP_ID --label "구체적인 그룹 이름" --reason "그룹 이름을 정정한 근거"
split --record RECORD_ID --label "별도 원인" --reason "다른 조건임을 원문에서 확인"
move --record RECORD_ID --to DEST_GROUP_ID --reason "이 그룹과 함께 검토할 근거"
merge --group GROUP_A --group GROUP_B --to GROUP_A --reason "두 그룹을 함께 검토할 근거"
```

여러 로그는 `--records-file ids.txt`에 **선택한 record ID를 한 줄에 하나씩** 기록해 이동·분리합니다. 빈 ID·중복·없는 ID·서로 다른 규칙 코드를 잘못 묶는 변경은 거부합니다. 검색어를 저장 시점에 다시 실행하여 선택 범위를 확대하지 않습니다. 한 서명 요청의 한도는 256KiB이며 초과하면 실패합니다. report 전체 로그 수의 제한이나 일부 기록만 반영하는 기능이 아닙니다.

구성이 바뀐 그룹의 분류 근거는 다시 확인해야 합니다. `group GROUP_ID`로 기존 근거를 읽고, AI/기존 근거를 그대로 확인할 때는 `accept`를, 직접 작성할 때는 UTF-8 파일을 사용합니다. `--evidence`는 자기 그룹의 대표 원본 ID 1~20개입니다. 대표 근거 선택이 그룹 전체의 구성·coverage 검사를 대체하지 않습니다.

```text
rationale GROUP_ID --text-file classification.txt --evidence RECORD_ID --summary "짧은 분류 이유" --reason "작성·수정 사유"
accept GROUP_ID --reason "전체 구성과 해당 분류 근거를 확인"
confirm --reason "모든 그룹의 구성과 분류 근거 검토 완료"
```

분류 근거는 왜 함께 묶었는지를 설명합니다. 누락·오래된 근거가 있거나 AI 작업이 진행 중이면 기존 웹 계약대로 확정을 차단합니다. `confirm`은 현재 분류 전체와 유효한 초안 근거를 사람이 명시적으로 확정하는 동작입니다.

## 그룹별 판정과 AI 참고 등록

먼저 `group GROUP_ID`로 현재 판정·개정과 AI 제안을 확인합니다. 로컬에는 직전에 읽은 그룹의 저장 조건만 유지하므로 다른 그룹을 읽었다면 대상 그룹을 다시 조회합니다. 그룹 수가 늘어도 로컬 자격 파일에 전체 목록을 쌓지 않습니다. 분류 전체가 확정된 뒤 판정 근거를 적어 저장합니다. 판정 근거는 왜 PASS/FAIL/보류인지를 설명하며 분류 설명과 구분합니다.

```text
group GROUP_ID
judge GROUP_ID FAIL --reason-file judgment.txt
group OTHER_GROUP_ID
judge OTHER_GROUP_ID DEFER --reason "추가 원본과 실행 조건 확인 필요"
```

AI 제안과 사람의 정정을 직접 연결하려면 표시된 제안 UUID를 `--proposal-id UUID`로 지정합니다. 오래된 제안은 거부합니다. 선택형 `--exception-file exception.json`은 기존 `{ "rule": "대상 규칙", "reason": "예외 사유", "conditions": "예외가 성립하는 조건" }` 형식이며 PASS/FAIL에서만 사용할 수 있습니다. 예외를 자동 규칙이나 승인 기준으로 승격하지 않습니다.

사람 확정과 AI 참고 등록은 별개입니다. CLI에서도 자동 등록하지 않으며, 해당 그룹을 다시 읽은 후 아래처럼 명시적으로 선택합니다. 판정이 없는 그룹의 REVIEW 등록이나 보류의 등록은 기존 계약대로 차단합니다.

```text
group GROUP_ID
evidence GROUP_ID CLUSTER on
group GROUP_ID
evidence GROUP_ID REVIEW on
group GROUP_ID
evidence GROUP_ID REVIEW off
```

## 통신 중단과 동시 수정

쓰기 전에 정확한 요청과 UUID를 로컬에 저장합니다. 시간 초과·연결 끊김은 판정 실패의 증거가 아니므로 `sanity review resume`으로 **같은 요청**의 결과를 확인합니다. 새 UUID로 자동 재실행하지 않습니다. 미완료 요청이 남은 동안 다른 명령을 보내지 않으며, 서버도 같은 UUID의 다른 내용을 거부합니다. DB 반영과 응답·큐 완료는 한 트랜잭션입니다.

명령 완료 응답은 처리 당시의 권한 검사와 개정에 대한 receipt입니다. `refresh`는 현재 상태를 다시 조회합니다. 권한/연결 회수·만료·원본 변경 후 새 명령은 거부합니다. 만료된 연결에 미완료 요청이 남았다면 웹의 불변 이력에서 요청 UUID를 확인하고 새 연결/별도 `--state`로 시작합니다. 미완료 기록을 없애서 실패로 취급하지 않습니다.

## 배포 준비

설계 Codex가 제공한 공통 기능이며, 내부 개발 AI의 회사 어댑터 수정 허용 범위를 넓히지 않습니다. 운영 DB 변경·서비스 배포는 기존 담당 절차로 수행합니다.

1. 양쪽 bridge/OA와 VWP에 같은 호환 소스를 배포합니다. 원천 VWP에는 계속 DB 계정이 없습니다.
2. DBA가 009 이후의 독립 확장 [014_cli_reviews.sql](../db/014_cli_reviews.sql)을 검토·적용합니다. 선택형 저장 확장 010~013이 없어도 014 자체는 적용할 수 있습니다. 기존 테이블·역할·GRANT는 추가하지 않습니다. 앱은 DDL을 실행하지 않으며 미설치 시 `CLI_REVIEW_SCHEMA_REQUIRED`로 차단합니다.
3. GUI 포털은 기존 저장 옵션에 `--cli-reviews`를 추가하여 준비합니다. 55/59/60/62/66단계 앞부분은 보존하고 두 큐의 ALTER 두 문장만 덧붙입니다. 실제 snapshot 검증·0행/쓰기 중지·승인 절차는 [포털 안내](../db/portal/README.md)를 유지합니다. **기존 PostgreSQL 18 카탈로그 비교 실패는 별도 미해결 문제**이며 이번 기능으로 해결되지 않습니다. 실패 시 검증을 건너뛰지 않습니다.
4. VWP 클라이언트는 기존 HTTPS schema 3 설정, bridge는 PostgreSQL 저장의 schema 4 설정과 실제 출발 IP/CIDR·TLS 검증을 유지합니다. CLI는 기존 bridge의 `/v1/reviews/*`만 호출합니다. VWP↔OA 사이에는 두 공유 DB 큐만 사용하고 직접 HTTP/파일 전달을 추가하지 않습니다.
5. OA DB 계정을 보유한 **OA bridge 작업자 환경**에서 기존 접수/AI 작업자와 함께 다음 프로세스를 실행합니다. 원천 VWP에 OA 계정·관리자 권한·SSO 쿠키를 배포하지 않습니다. 기존 `SANITY_ADMIN_USER_ID`와 SSO/RBAC 연결이 필요합니다.

```sh
python -m oa.review_worker
# 배포 점검 시 한 명령만 처리
python -m oa.review_worker --once
```

공유 큐에는 서명된 요청과 해당 report의 조회 결과/receipt가 남습니다. 반복 조회 응답과 검토 이력은 추가 DB 공간을 사용합니다. 기존 013 수집 용량 목표를 CLI 조회·이력이 누적된 전체 운영 용량의 보장으로 표시하지 않으며, 해당 규모·보관 비용은 사내에서 확인해야 합니다. 연결 원문키와 OA의 검증용 서명 자격은 큐에 넣지 않습니다. 제한 DB 계정의 private 격리와 명시적 단일 계정 모드의 기존 한계는 그대로입니다. 조회 결과·검토 근거도 사내 자료이므로 사내에서만 사용합니다.

공통 검증은 `db/tests/run_mock.py --include tests.test_cli_review tests.test_cli_review_migration`로 quick과 합쳐 실행합니다. 실제 사내 SSO·회사 입력과 VWP 터미널의 읽기/수정·회수·웹 동시 편집, 대량 report의 실사용성은 현장에서 별도로 확인합니다. Termux의 기존 실제 파일 보안 두 검사의 사내 독립 Linux 검증 대기는 유지합니다.
