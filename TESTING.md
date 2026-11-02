# 담당자별 테스트 실행

사내 개발 AI는 저장소 루트에서 **자기 역할 명령 하나**로 시작합니다. 공통 통합 quick·regression·full을 매번 반복하지 않습니다. 전체 개발 의존성을 설치한 `.venv`를 사용합니다.

| 담당 | 기본 명령 | 공개 검사 범위 | 자기 회사 테스트 위치 |
|---|---|---|---|
| VWP 원천 | `.venv/bin/python run_tests.py --role vwp` | 수집·plan/제출 snapshot·파일 보안·보존 파일·AI 필드 선언 | `vwp/integration/company_plan/` |
| VWP-bridge / 중앙 AI worker | `.venv/bin/python run_tests.py --role vwp-bridge` | parser·수집 전용·제출 HTTPS/실행 ID·VWP 모델 전송·no-cache·code 경계·전역 슬롯·배치/입력 필드·rolling ETA·안전 재시도 | `vwp_bridge/integration/company_{parsers,submission,clustering,review}/` |
| OA | `.venv/bin/python run_tests.py --role oa` | 인증/SSO·권한 경계·연결/개인 키 API·기본 모델·reasoning/cache·전역 슬롯/보고서 삭제 API·code 경계·작업 지표 | `oa/integration/company_sso/` |

역할은 소스 폴더명이 아니라 담당 업무로 나눕니다. 중앙 AI worker가 사용하는 `oa/` 공통 코드도 VWP-bridge 역할 검사에 포함됩니다. 공유 계약의 작은 검사는 여러 역할에 포함될 수 있지만 다른 역할의 DB 흐름·포털·10만 건 검사는 기본 역할 명령에 들어가지 않습니다. 기존 테스트 본문·입력·기대값·skip 조건은 바꾸지 않습니다.

## 기본 검사와 회사 테스트

- 공개 역할 검사는 DB나 실제 모델을 호출하지 않습니다. 기존 파일 보안 검사는 실제 파일시스템 조건이 필요한 항목도 포함합니다.
- `--role`은 표의 **자기 역할 company 디렉터리만** 찾아 `test_*.py`를 함께 실행합니다. namespace package도 지원하며, 다른 디렉터리의 같은 파일명도 분리합니다. 회사 테스트는 승인 SDK/원천의 모의 client와 합성 자료로 작성합니다. 이 기능이 회사 SDK·운영 DB·실제 모델 호출을 자동 승인하는 것은 아닙니다.
- 회사 테스트가 없으면 `company_tests_selected=0`으로 표시합니다. 공개 검사 통과를 회사 어댑터 검증 완료로 보고하지 않습니다. 회사 디렉터리에 테스트 파일이 있지만 전체 검사가 0건이거나 import에 실패하면 성공으로 생략하지 않습니다. 보조 모듈은 검사 수 0건이어도 같은 디렉터리의 실제 검사와 함께 사용할 수 있습니다. 역할 실행은 skip도 실패로 처리합니다.
- 목록 확인은 같은 명령에 `--list`를 붙입니다. 실행 결과 JSON에는 `role`, `database_tests`, `full_suite=false`, 실제 테스트 ID·건수·시간·회사 검사 수를 기록합니다. `--report-json 새파일.json`은 기존 파일을 덮어쓰지 않습니다.
- 수정한 회사 모듈만 더 좁게 확인할 때는 기존처럼 해당 회사 디렉터리에 `python -m unittest discover -s 경로 -p 'test_*.py' -v`를 실행할 수 있습니다. 자기 테스트가 이미 통과하고 관련 변경이 없으면 다른 역할 검사를 이어서 실행하지 않습니다.

## 필요한 경우의 역할별 DB 검사

DB 저장/worker 연결을 확인해야 할 때만 별도 개발 머신의 PostgreSQL 서버 바이너리로 실행합니다.

```bash
.venv/bin/python db/tests/run_mock.py --role oa
.venv/bin/python db/tests/run_mock.py --role vwp-bridge
```

각 명령은 해당 역할의 공개 기본 검사와 소수의 DB 대표 검사를 실행합니다. OA는 연결/인증 개정·암호화·모델 기본값·OWNER 확정·작업 실행 기록 삭제, VWP-bridge는 parser 봉인/원자 반영·worker·no-cache 실제 전송 이력·기존 병렬 묶음과 rolling 빈자리 보충·완료 조각 재개·삭제 후 늦은 응답 차단을 확인합니다. VWP 원천에는 DB가 없으므로 `run_mock.py --role vwp`는 지원하지 않습니다.

mock은 새 소켓 전용 PostgreSQL·작업자별 독립 DB를 만들고 종료합니다. 회사 `.env`·접속 정보·실제 report를 사용하지 않으며, 기존 snapshot 제외 정책에 따라 **회사 구현/회사 테스트/비공개 fixture도 복사하거나 실행하지 않습니다.** 회사 어댑터 검사는 원본 checkout의 기본 역할 명령 결과로 별도 보고합니다. 기본 최대 4개 작업자와 폐기용 ephemeral 정책은 유지하며 `--jobs 1 --durability durable`로 직렬·디스크 동기화 검사를 선택할 수 있습니다. 운영 DB 설정은 바꾸지 않습니다.

역할 검사는 quick 범위입니다. `--role`과 `--profile regression/full`를 함께 지정하면 잘못된 조합으로 거부합니다. 더 넓은 통합 검사가 필요하면 아래 명령을 사용합니다.

## 설계 Codex의 통합 검사

```bash
# 공통 변경의 기본 통합 회귀 — 3분 이내 목표, DB 준비/종료 포함
.venv/bin/python db/tests/run_mock.py --profile quick

# 변경한 기능의 전용 검사 + quick을 한 번에 실행 — 중복 ID는 한 번만
.venv/bin/python db/tests/run_mock.py --include tests.test_ai_parallel_width

# 이전의 넓은 quick 범위 / 전수·포털·10만 건 검증
.venv/bin/python db/tests/run_mock.py --profile regression
.venv/bin/python db/tests/run_mock.py --profile full
```

Codex는 변경 부분의 모듈·클래스·메서드를 `--include` 뒤에 공백으로 나열할 수 있습니다. 기본 프로필에 더한 전체 목록을 먼저 정하고, 겹치는 테스트 ID를 한 번만 실행한 뒤 최대 4개 독립 작업자 DB의 결과를 합칩니다. 전용 검사와 quick을 같은 소스로 합쳐 완료했다면 별도 quick을 반복하지 않습니다. 예를 들어 `--include tests.test_ai_evidence_choices tests.test_ai_parallel_width`는 두 기능의 전용 검사와 quick을 한 번에 확인합니다. 목록만 보려면 `run_tests.py --include 모듈명 --list`를 사용합니다.

추가 선택은 공개 `tests.test_*`·`oa.test_*`·`vwp.test_*`·`vwp_bridge.test_*`의 실제 모듈/클래스/메서드만 받으며 없거나 비어 있으면 실패합니다. 콘솔과 JSON의 `include`에 선택을 기록하고 모든 병렬 작업자가 같은 선택을 사용했는지 대조합니다. 기본 프로필·실행 건수·실패/skip·완전성 보고는 유지합니다. `--role`과의 혼합은 거부하며 사내 담당자는 기존 자기 역할 명령을 계속 사용합니다.

매번 32개 호출을 띄우는 통합 검사와 실제 source packet부터 분류·판정까지 끝내는 보존 본문 전체 흐름은 regression/full에 보존합니다. 병렬 예약/복구나 보존 본문 입력 계약을 바꿀 때 해당 검사를 `--include`로 추가합니다. 기본 quick에는 32개 상태의 순수 검사·실제 2개 병렬/8개 rolling 처리·근거/입력 계약의 대표 검사를 유지합니다. 회사 담당자의 기본 bridge DB 검사도 무거운 32개 전체 실행을 반복하지 않습니다.

역할 없는 명령은 quick의 핵심 검사와 regression/full의 확장 검사를 구분합니다. 카드의 지표·삭제·제출 실행 ID/code 경계/전역 슬롯/보고서 삭제의 작은 계약과 DB 대표 경로는 quick에 유지합니다. 신규 전체 모듈은 명시적 regression/full 또는 `--include`에서 확인합니다. 알려진 PG18 실패·파일 보안 검사와 regression/full의 기존 대용량/포털 시나리오는 유지합니다. 통합 DB·공통 변경 검토는 설계 Codex가 맡고, 사내 담당자는 자기 변경·역할 검사 결과와 필요한 후속 검증을 인계합니다.

## 남은 실환경 확인과 보고

Termux의 실제 하드링크·root 소유 설정 파일 검사는 아래 기존 항목을 그대로 유지합니다. 첫 항목은 VWP, 둘째는 VWP-bridge 기본 검사에도 포함됩니다.

- `tests.test_submission_contracts.SubmissionContractTests.test_hardlinks_symlinks_and_changed_snapshot_are_rejected`
- `tests.test_submission_https.HttpsSubmissionTests.test_transport_config_is_explicit_https_and_receipts_are_bound_to_the_owner`

이 두 항목의 실제 확인은 **사내 독립 Linux 테스트 환경에서 대기 중**입니다. `st_nlink == 1`·root 소유·그룹/다른 사용자 쓰기 금지 검사를 완화하거나 기존 테스트를 skip/삭제하지 않습니다. `tests.test_submission_files`의 로컬 합성 metadata 검증 통과는 실제 하드링크·소유권 확인 완료가 아닙니다. PostgreSQL 18 카탈로그 호환 실패는 별도 미해결입니다.

보고에는 역할·DB 포함 여부·공개/회사 검사 수·통과/실패/skip·실제 소요 시간을 적습니다. 기본 역할 검사 성공을 통합 전수·실제 회사 모델 품질·운영 배포 완료로 표시하지 않습니다. 기존 [수정 허용 범위와 WAITING_FOR_CODEX](INTERNAL_AI_GUIDE.md)는 유지합니다.
