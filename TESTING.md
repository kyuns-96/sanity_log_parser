# 담당자별 테스트 실행

사내 개발 AI는 저장소 루트에서 **자기 역할 명령 하나**로 시작합니다. 공통 통합 quick·regression·full을 매번 반복하지 않습니다. 전체 개발 의존성을 설치한 `.venv`를 사용합니다.

| 담당 | 기본 명령 | 공개 검사 범위 | 자기 회사 테스트 위치 |
|---|---|---|---|
| VWP 원천 | `.venv/bin/python run_tests.py --role vwp` | 수집·plan/제출 snapshot·파일 보안·보존 파일·AI 필드 선언 | `vwp/integration/company_plan/` |
| VWP-bridge / 중앙 AI worker | `.venv/bin/python run_tests.py --role vwp-bridge` | parser·수집 전용·제출 HTTPS·VWP 모델 전송·no-cache·배치/입력 필드·rolling ETA/그룹 집계·안전 재시도 | `vwp_bridge/integration/company_{parsers,submission,clustering,review}/` |
| OA | `.venv/bin/python run_tests.py --role oa` | 인증/SSO·권한 경계·관리 연결/개인 키 API·기본 모델·reasoning/cache 설정·작업 삭제 API/지표 | `oa/integration/company_sso/` |

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

각 명령은 해당 역할의 공개 기본 검사와 소수의 DB 대표 검사를 실행합니다. OA는 연결/인증 개정·암호화·모델 기본값·OWNER 확정·작업 실행 기록 삭제, VWP-bridge는 parser 봉인/원자 반영·worker·no-cache 실제 전송 이력·기존 병렬 묶음과 rolling 빈자리 보충·완료 조각 재개·삭제 후 늦은 응답 차단을 확인합니다. 통합 demo와 별도 scale probe는 선택하지 않습니다. VWP 원천에는 DB가 없으므로 `run_mock.py --role vwp`는 지원하지 않습니다.

mock은 새 소켓 전용 PostgreSQL·작업자별 독립 DB를 만들고 종료합니다. 회사 `.env`·접속 정보·실제 report를 사용하지 않으며, 기존 snapshot 제외 정책에 따라 **회사 구현/회사 테스트/비공개 fixture도 복사하거나 실행하지 않습니다.** 회사 어댑터 검사는 원본 checkout의 기본 역할 명령 결과로 별도 보고합니다. 기본 최대 4개 작업자와 폐기용 ephemeral 정책은 유지하며 `--jobs 1 --durability durable`로 직렬·디스크 동기화 검사를 선택할 수 있습니다. 운영 DB 설정은 바꾸지 않습니다.

역할 검사는 quick 범위입니다. `--role`과 `--profile regression/full` 또는 mock의 `--rows/--groups`를 함께 지정하면 잘못된 조합으로 거부합니다. 더 넓은 통합 검사가 필요하면 아래 명령을 사용합니다.

## 설계 Codex의 통합 검사

```bash
# 공통 변경의 기본 통합 회귀 — 3분 이내 목표, DB 준비/종료 포함
.venv/bin/python db/tests/run_mock.py --profile quick

# 이전의 넓은 quick 범위 / 전수·포털·10만 건 검증
.venv/bin/python db/tests/run_mock.py --profile regression
.venv/bin/python db/tests/run_mock.py --profile full
```

역할 없는 명령의 기존 quick·regression·full 선택 범위는 유지합니다. 역할 실행기 전용 검사와 이번 카드의 작은 지표 검사·삭제 DB 대표 2건을 quick에 추가했습니다. 알려진 PG18 실패·파일 보안 검사·대용량/포털 시나리오를 기존 통합 범위에서 제거하지 않습니다. 통합 DB·공통 변경 검토는 설계 Codex가 맡고, 사내 담당자는 자기 변경·역할 검사 결과와 필요한 후속 검증을 인계합니다.

## 남은 실환경 확인과 보고

Termux의 실제 하드링크·root 소유 설정 파일 검사는 아래 기존 항목을 그대로 유지합니다. 첫 항목은 VWP, 둘째는 VWP-bridge 기본 검사에도 포함됩니다.

- `tests.test_submission_contracts.SubmissionContractTests.test_hardlinks_symlinks_and_changed_snapshot_are_rejected`
- `tests.test_submission_https.HttpsSubmissionTests.test_transport_config_is_explicit_https_and_receipts_are_bound_to_the_owner`

이 두 항목의 실제 확인은 **사내 독립 Linux 테스트 환경에서 대기 중**입니다. `st_nlink == 1`·root 소유·그룹/다른 사용자 쓰기 금지 검사를 완화하거나 기존 테스트를 skip/삭제하지 않습니다. `tests.test_submission_files`의 로컬 합성 metadata 검증 통과는 실제 하드링크·소유권 확인 완료가 아닙니다. PostgreSQL 18 카탈로그 호환 실패는 별도 미해결입니다.

보고에는 역할·DB 포함 여부·공개/회사 검사 수·통과/실패/skip·실제 소요 시간을 적습니다. 기본 역할 검사 성공을 통합 전수·실제 회사 모델 품질·운영 배포 완료로 표시하지 않습니다. 기존 [수정 허용 범위와 WAITING_FOR_CODEX](INTERNAL_AI_GUIDE.md)는 유지합니다.

## 역할 분리와 PR #37의 로컬 확인 — 2026-11-02

Termux/PostgreSQL 18.6에서 공개 합성 검사만 실행한 결과입니다. 회사 테스트는 0건이며 실제 회사 어댑터/모델 검증은 미실시입니다. 기본 역할 시간은 프로세스 전체 경과, mock 시간은 DB 준비·종료를 포함한 실행기 기록입니다. OA·VWP 기본과 OA mock 수치는 앞선 역할 분리 확인 기록을 유지하고, VWP-bridge·통합 quick 수치는 PR #37 통합본에서 갱신했습니다.

| 선택 범위 | 실행 | 결과 | 시간 |
|---|---:|---|---:|
| OA 기본 | 71 | 71 통과 | 8.3초 |
| VWP-bridge 기본 | 122 | 121 통과·기존 root 소유 설정 오류 1 | 6.7초 |
| VWP 기본 | 41 | 40 통과·기존 하드링크 오류 1 | 1.7초 |
| OA mock DB | 77 | 77 통과 | 12.419초 |
| VWP-bridge mock DB | 135 | 134 통과·기존 root 소유 설정 오류 1 | 10.839초 |
| 통합 quick | 409 | 402 통과·기존 PG18 실패 5·Termux 오류 2 | 30.918초 |

실행기 전용 33건을 포함한 PR 통합 전용 57건이 통과했습니다. rolling 보충/ETA, 기존 병렬·UNKNOWN 복구, no-cache 실제 전송 hash와 보존 본문 경로를 함께 확인했습니다. TypeScript/Vite 빌드와 병렬·rolling 화면 7건도 통과했습니다. 모든 실행의 skip은 0건이고 mock DB는 모두 종료됐으며 병렬 실행 ID의 누락/중복은 없습니다. 회사 테스트 발견의 역할 격리·동일 파일명/namespace·import 실패·검사 없는 디렉터리·보조 모듈·skip 실패 처리와 DB 역할 전파/범위 혼합 거부도 합성으로 확인했습니다. 전체 성공 상태는 아니며 위 파일 보안 2건의 사내 독립 Linux 실환경 검증과 PG18 문제는 그대로 남습니다. regression/full·10만 건 검사는 이번 병합 검토에서 재실행하지 않았습니다.

## AI 작업 카드·실행 기록 삭제의 로컬 확인 — 2026-11-02

이후 카드 변경에서는 작은 지표 검사 5건·삭제 API 1건·삭제 DB 대표 2건을 기존 quick에 추가했습니다. 기존 검사와 실패 조건은 유지했습니다. PostgreSQL 18.6의 독립 합성 DB를 사용했습니다.

| 선택 범위 | 실행 | 결과 | 시간 |
|---|---:|---|---:|
| 삭제·지표·API 최종 전용 | 41 | 41 통과 | 91.100초 |
| VWP-bridge 기본 | 127 | 126 통과·기존 root 소유 설정 오류 1 | 17.282초 |
| VWP-bridge mock DB | 141 | 140 통과·같은 기존 오류 1 | 42.958초 |
| 통합 quick | 417 | 410 통과·기존 PG18 실패 5·Termux 오류 2 | 111.980초 |

최종 전용 검사는 대기·실행·완료·UNKNOWN 작업의 물리 행 삭제, 원본/확정 결과 보존, 직렬·기존 병렬·rolling의 늦은 응답/관찰 저장 차단, 요청 ID 재사용 거부, 권한/트랜잭션 롤백과 관찰/삭제 잠금 순서를 확인했습니다. 앞선 rolling·no-cache 회귀를 포함한 전용 61건도 통과했으며 최종 41건과 중복됩니다. 전용·mock 시간에는 DB 준비/종료를 포함했습니다.

TypeScript/Vite 프로덕션 빌드와 카드/병렬/rolling 화면 17종을 확인했습니다. 화면 검사는 첫 완료 실행에서 15건 통과 후, 확인창이 배경을 접근성 트리에서 숨기는 조건을 반영해 새 검사 2건의 DOM 선택자만 수정하고 두 건을 재실행해 통과했습니다. 기대값과 제품 동작은 유지했습니다. 배치 평균·병합/그룹 수·삭제 확인/오류/오래된 조회 결과·애니메이션/reduced-motion을 확인했고, 중단되어 결과 파일이 없는 앞선 브라우저 실행은 통과 건수에 포함하지 않았습니다.

회사 검사는 0건이며 skip·작업자 오류·실행 ID 누락/중복은 없습니다. 모든 mock DB는 종료했습니다. 통합 quick의 7건은 기존 실패 집합과 동일합니다. 실제 회사 모델/사내 Linux 파일 보안 검증은 완료하지 않았으며, PG18 카탈로그 문제는 별도 미해결입니다. regression/full·10만 건 검사는 이번 카드 변경에서 재실행하지 않았습니다.
