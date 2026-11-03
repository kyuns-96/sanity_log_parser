# 테스트 실행

저장소의 검사는 백엔드 67개, 브라우저 19개, 컨테이너 11개, 독립 DB 검사 2개로 총 99개다. 같은 테스트 ID를 다른 역할·프로필에서 다시 선택해도 별도 검사로 세지 않는다. 백엔드 `full`만 실행하면 67개이며 브라우저·컨테이너까지 실행한 결과로 표시하지 않는다.

## 담당자별 실행

사내 개발 AI는 전체 개발 의존성을 설치한 환경에서 자기 역할 명령 하나로 시작한다. 기존 회사 수정 허용 범위와 오류 시 `WAITING_FOR_CODEX` 규칙은 유지한다.

| 담당 | 명령 | 공개 검사 | 자기 회사 테스트 위치 |
|---|---|---|---|
| VWP | `.venv/bin/python run_tests.py --role vwp` | 제출 snapshot·실제 파일 보안·합성 OS metadata | `vwp/integration/company_plan/` |
| VWP-bridge | `.venv/bin/python run_tests.py --role vwp-bridge` | parser 원본·불완전 보고서·HTTPS·내부 제출 경계·모델 HTTP 계약 | `vwp_bridge/integration/company_{parsers,submission,clustering,review}/` |
| OA | `.venv/bin/python run_tests.py --role oa` | SSO 재검증·오류 비식별화·위조 인증 거부·모델 응답 계약 | `oa/integration/company_sso/` |

기본 역할 검사는 DB나 실제 모델을 호출하지 않는다. 실제 파일시스템·루프백 HTTPS는 사용한다. `--list`로 실제 선택을 확인한다. DB 연결 확인이 필요할 때만 독립 개발 머신에서 다음 중 자기 역할을 실행한다.

```bash
.venv/bin/python db/tests/run_mock.py --role oa --pg-bin /path/to/postgresql12/bin
.venv/bin/python db/tests/run_mock.py --role vwp-bridge --pg-bin /path/to/postgresql12/bin
```

OA DB 검사는 실제 권한·AI/사람 확정·근거 등록·암호화된 개인 인증값·보고서 삭제를 확인한다. VWP-bridge DB 검사는 parser 원자 반영·실제 병렬/rolling 작업·입력 고정·cache 전송·전역 슬롯·삭제 후 늦은 응답 차단을 확인한다. VWP에는 DB 역할 검사가 없다.

`--role`은 자기 역할의 company 디렉터리만 찾으며 namespace package도 지원한다. 회사 테스트가 없으면 `company_tests_selected=0`이다. 회사 테스트 파일의 import 실패·빈 전체 선택·skip을 성공으로 생략하지 않는다. 회사 SDK는 모의 client와 합성 자료로 확인하며 공개 검사 성공을 회사 연동 완료로 표시하지 않는다.

독립 mock은 회사 `.env`·실제 report·회사 구현/회사 테스트·비공개 fixture를 복사하거나 실행하지 않는다. 회사 어댑터 검사는 원본 checkout의 역할 명령으로 별도 확인한다. 역할과 `--profile regression/full` 또는 `--include`를 섞을 수 없다.

## Codex의 백엔드 통합 검사

운영 기준은 PostgreSQL 12다. PG12.1 이상 12.x 서버 바이너리를 `--pg-bin`으로 명시한다. mock은 SCRAM 인증·전용 UNIX socket·작업자별 독립 DB를 사용하고 종료 시 DB를 정지한다.

| 프로필 | 검사 수 | 범위 |
|---|---:|---|
| `quick` | 60 | 인증·보안·parser·DB 권한/트랜잭션·AI 작업·사람 확정·실패 전파 |
| `regression` | 65 | quick + 최신 저장 형식의 TLS 제출·OWNER 확인·페이지 복구·보존 본문 |
| `full` | 67 | regression + 포털 전체 66단계 재현·10만 건 전체 모델 입력/최종 partition |

```bash
.venv/bin/python db/tests/run_mock.py --pg-bin /path/to/postgresql12/bin
.venv/bin/python db/tests/run_mock.py --profile full --pg-bin /path/to/postgresql12/bin

# 변경 부분과 quick을 합쳐 같은 ID는 한 번만 실행
.venv/bin/python db/tests/run_mock.py --include tests.test_ai_rolling_batches --pg-bin /path/to/postgresql12/bin
```

기본은 `quick`이며 독립 DB 준비·종료를 포함해 3분 이내를 목표로 한다. 최대 4개 작업자를 사용하고 `--jobs 1 --durability durable`로 직렬·디스크 동기화를 선택할 수 있다. 포털 변경은 `--include tests.test_shared_source_migration`으로 전체 단계·이전 단계의 최종 카탈로그·테이블 OID 보존을 확인한다. 무관한 10만 건 검사를 매번 반복하지 않는다.

`--include`는 공개 `tests.test_*`·`oa.test_*`·`vwp.test_*`·`vwp_bridge.test_*`의 실제 모듈/클래스/메서드만 받는다. 없거나 비어 있으면 실패하며 모든 작업자의 선택이 같고 실행이 빠짐없이 끝났는지 대조한다. `--list`는 실행하지 않고 목록만 보여준다. `--report-json 새파일.json`은 기존 파일을 덮어쓰지 않는다.

## 브라우저와 컨테이너

```bash
cd oa/dashboard
npm run build
npm run test:e2e       # 격리된 API mock 12개
npm run test:workspace # 실제 합성 API/PG12 workspace 8002에서 5개
npm run test:drag      # 실제 합성 API/PG12 workspace 8003에서 2개
```

실제 DB 검사는 별도 workspace를 준비한 뒤 실행한다. [workspace 준비](oa/WORKSPACE.md)를 따르며 `tools/seed_pipeline_qa.py`, `tools/seed_large_workspace.py --block project-3-gpu --family GCA`의 1만 건, `tools/seed_drag_qa.py`의 두 보고서를 사용한다. workspace의 같은 serial 그룹 안에서는 사람 확정→다음 보고서 재사용→재분류에 따른 무효화를 순서대로 검증한다. 실제 모델은 호출하지 않는다.

컨테이너 진입점은 실제 nginx의 Host/Origin/전달 헤더 경계와 자식 프로세스 오류·정상 종료·강제 종료를 11개로 확인한다.

```bash
SANITY_TEST_NGINX_BINARY=/path/to/nginx .venv/bin/python -m unittest oa.docker.test_start oa.docker.test_proxy_origin -v
```

nginx가 없어서 skip되면 컨테이너 전체 성공이 아니다. 이 검사는 컨테이너 이미지 배포 완료를 뜻하지 않는다.

## 독립 DB 검사

`db/tests/smoke.sql`은 제약·권한·queue 상태 전이의 SQL 검사 하나다. `db/tests/check_concurrency.py`는 실제 두 psql 세션이 잠긴 행을 건너뛰고 서로 다른 메시지를 점유하는 검사 하나다. 두 항목 모두 mock의 DB 준비에서 실행하며 100개 상한에 포함한다. 단독 실행은 DBA가 앱 역할을 준비한 전용 빈 `sanity_test_*` DB에서만 실행한다.

```bash
SANITY_PSQL=/path/to/postgresql12/bin/psql bash db/tests/create_fixture.sh -h HOST -U OWNER -d sanity_test_NAME
SANITY_PSQL=/path/to/postgresql12/bin/psql .venv/bin/python db/tests/check_concurrency.py -h HOST -U OWNER -d sanity_test_NAME
```

## 남은 실환경 확인과 보고

다음 두 원래 보안 검사는 삭제·skip하거나 운영 검사를 완화하지 않는다.

- `tests.test_submission_contracts.SubmissionContractTests.test_hardlinks_symlinks_and_changed_snapshot_are_rejected`
- `tests.test_submission_https.HttpsSubmissionTests.test_transport_config_is_explicit_https_and_receipts_are_bound_to_the_owner`

`st_nlink == 1`, 설정의 root 소유 및 그룹/다른 사용자 쓰기 금지는 유지한다. `tests.test_submission_files`는 OS metadata를 합성해 정상 수락과 위험 조건 거부를 확인하며 실제 하드링크/소유권 동작의 검증 결과로 표시하지 않는다. 위 두 항목의 **사내 독립 Linux 실환경 확인은 대기 중**이다. PostgreSQL 18 카탈로그 호환 실패도 별도 미해결이며 PG12 결과로 해결됐다고 표시하지 않는다.

결과에는 실행 프로필·역할·실제 검사 수·실패/오류/skip·회사 검사 유무를 구분한다. 세부적인 화면 크기·테마 조합과 각 계층의 반복 검사를 삭제했으므로 예전 전수 검증과 같은 세부 범위라고 주장하지 않는다. 실제 회사 모델 품질·운영 배포 완료는 별도 확인한다.
