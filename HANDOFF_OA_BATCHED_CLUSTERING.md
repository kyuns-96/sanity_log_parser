# OA 대용량 배치 분류 작업 인계

## 2026-10-28 로컬 후속 작업 — 저장 목표 확인·full 진행 중

- 사용자가 원격 서버를 삭제했음을 확인했다. 서버에만 있던 마지막 full 결과는
  복구하지 못했으며 통과로 간주하지 않는다. 아래 서버 경로와 수치는 이전
  체크포인트의 기록이다. 이후 작업은 `codex/oa-batch-followup-20261028`에서 이어간다.
- 기존 로컬 작업본의 `O_PATH` 디렉터리 접근 수정과 `tests.test_submission_files`를
  검토 후 같은 내용으로 가져왔다. 원래 작업본은 수정하지 않았다. 보안 검사는
  유지하며 전용 10건이 통과했다. 이 중 OS metadata 합성 검증은 실제 하드링크·root
  소유권 검증의 대체가 아니다. 원래 두 보안 테스트의 사내 실환경 검증은 대기다.
- 배치 계획을 `plan_format=2`의 압축 라운드 레코드에 모았다. 전체 descriptor와
  원본 hash·대상 참조, 호출 예약/결과/hash chain은 보존한다. 기존 개별 계획 형식을
  읽어 재개하며, 부모가 고정한 첫 계획 hash도 검사한다. 추가 SQL/GRANT는 없다.
- PostgreSQL 18.6의 독립 socket/SCRAM fixture에서 배치 작업·개인 토큰·보존 본문
  전용 60건이 실패/오류/skip 없이 통과했다. 순수 모델 8건도 통과했다. 많은 별도 그룹,
  기존 계획 재개, 재해시한 계획 교체 거부, 마지막 prompt 개정 검사가 포함된다.
- 로컬 Python 3.14의 native tokenizer 확장은 `libpython3.14.so`를 preload한
  시험 실행 환경에서 확인했다. 운영 코드·기존 테스트·권한 기대값은 바꾸지 않았다.
  초기 환경의 cryptography 누락/토크나이저 로딩 실패와 중단한 용량 실행은
  `artifacts/oa-batch-followup/`에 실패/중단 결과로 보존했다.
- 최신 quick은 **348건 실행: 341 통과·실패 5·오류 2·skip 0**이다. 실패 5건은
  PostgreSQL 18의 기존 포털 카탈로그 비교 문제이고, 오류 2건은 원래 하드링크/
  root 소유 설정 실환경 테스트다. 전체 성공으로 표시하지 않으며 이 기존 항목은
  그대로 남긴다. full은 진행 중이며 새 10만 건 배치 입력/partition 검사와
  10만 건 화면·근거 페이지 검사는 통과했다. 전체 성공 여부는 종료 결과로 판단한다.
- **새 저장 측정: PostgreSQL 18.6에서 19,357,696 bytes(19.36MB), 목표
  20,000,000 bytes 이내**다. 보존 본문 없는 POST 14,000,000 bytes·49,000건과
  PRE 224 bytes·4건, 네 테이블 TOAST/index·AI 감사·최종 분류 제안을 모두 포함한다.
  원본 byte·52개 패킷·VWP/OA 전체 기록과 실제 leaf 입력 hash, 최종 partition이
  일치했다. 모델 mock 442회(leaf 419회), worker 448단계, 자식 이력 1,337행이다.
  계획을 묶어도 descriptor·근거·배정·감사를 삭제하지 않았으며 repack하지 않았다.
  기존 PG12.1의 20.66MB는 다른 실행 환경의 과거 결과로 구분한다.
- 새 측정의 AI 전 합계는 12,730,368 bytes다. OA 총 7,217,152 bytes,
  VWP 총 5,849,088 bytes, VWP→OA 총 6,193,152 bytes, OA→VWP 총 98,304 bytes다.
  worker 1,772.355초는 다른 검사를 함께 실행한 Termux의 mock 처리 시간이며
  회사 모델 응답 시간의 예측이 아니다. 결과는
  `artifacts/oa-batch-followup/optimized-pg18/storage.json`, fixture 종료 상태는
  같은 디렉터리 `summary.json`에 있다. 이 fixture는 정상 종료했다.
- 이전 `question_from_internal/oa.md` 원문은 복구하지 못했다. 사용자의 후속 지시로
  비공개 폴더와 답변 파일을 새로 만들고, 원문 복원본이 아닌 이번 Codex 답변임을
  명시했다. 기존 `/question_from_internal/` Git 제외 규칙을 확인했다. 원래 질문이나
  이전 답변을 재구성하지 않으며 이 파일을 공개 PR에 포함하지 않는다.

---

작성: 2026-10-28, 현재 대화의 설계 담당 Codex.
**상태: 사용자 요청에 따른 작업 중 체크포인트. 최종 검증·검토·main 병합·사내 배포 전이다.**

- 작업 브랜치: `feature/oa-batched-clustering`
- 기준 main: `113ad9d7bed82d7dc4c3e489535cc6a9e639a2cf` — PR #28 읽기 캐시 병합본
- 작업 디렉터리: `/root/work/sanity_log_parser-preserved`
- 원래 `/root/work/sanity_log_parser`는 다른 UI 브랜치이며 기존 수정된 `AGENTS.md`가 있다. 그 변경을 포함하거나 되돌리지 않았다.
- 실제 회사 DB·원문·모델은 사용하지 않았다. 검증은 독립 PostgreSQL과 합성 HTTP 모델이다.
- 이번 체크포인트 push는 운영 적용 완료를 뜻하지 않는다. 아래 미완료 항목을 먼저 확인한다.

## 1. 승인된 방향과 남은 범위

전체 로그를 예산에 맞춰 나누어 AI가 분류하고, 중간 그룹의 의미를 AI가 비교한 뒤 원본 ID 전체의
정확한 partition을 검증해 제안 하나를 공개한다. 규칙으로 최종 그룹을 확정하고 대표 로그만
모델에 보내는 방식으로 바꾸지 않는다. 사람의 확정·정정과 다음 판단의 확정 사례 활용을 유지한다.

모델/게이트웨이 한도와 `max_tokens` 수용에 관한 현장 회신은 비공개 문의/대화 기록을 따른다.
큰 출력 값을 API가 거절하지 않았다는 사실만으로 실제 출력 최대치를 확정하지 않는다.
정확한 tokenizer는 아직 확인되지 않아 명시적인 UTF-8 byte 추정 모드와 로컬 tokenizer JSON 모드를 제공했다.
사내 실제 설정·자료는 공개 저장소에 추가하지 않는다.

**이번 구현의 경계:** 대형 확정 그룹의 분할 판정, 큰 plan 4 보존 본문 자체의 분할,
실제 모델 의미 판단 품질은 남는다. 필요한 기록 하나/전체 보존 본문이 예산을 넘으면 실패한다.
분류 배치 완료를 전체 대용량 AI 판단 기능 완료로 보고하지 않는다.

## 2. 현재 반영한 코드

| 파일 | 구현 내용 |
|---|---|
| `oa/sanity_ai_budget.py` | 선택형 배치 설정, context/출력/overhead 예산, UTF-8 byte 추정, hash로 고정한 로컬 tokenizer. truncation/padding 해제, 네트워크 다운로드 없음 |
| `oa/sanity_ai_batch_model.py` | 전체 기록 leaf 계획, 짧은 ID와 원본 ID의 일대일 대응, 배치별 partition/근거/인용 검증, 제한된 그룹 통합 호출, 원본 전체 최종 검증 |
| `oa/sanity_ai_batch_jobs.py` | 기존 WS_CONFIG의 별도 namespace에 압축 plan/결과와 작은 hash chain 이벤트 저장. HTTP 전 실제 입력 SHA 예약, 자식별 deadline, 재호출 없는 결과 복구, 최종 결과 참조 저장 |
| `oa/sanity_ai_jobs.py`, `oa/ai_worker.py` | 기존 부모 API/단일 호출 이력 보존, 선택형 배치 경로, 현재 권한·source/사람/사례/설정/개인 토큰 개정 확인, 한 번에 최대 한 호출 |
| `oa/sanity_ai_model.py` | 요청 생성과 HTTP 실행 분리, 지원된 출력 파라미터 전달, 호출 전 token/body 검사, 배치 prompt 버전 설정 지문 결합 |
| `oa/sanity_workspace_review.py` | 여러 호출의 최종 `BATCH_MANIFEST` receipt를 단일 HTTP hash와 구분해 검사 |
| `common/sanity_config.py`, `oa/.env.example`, requirements 2개 | 배치 설정 키, `tokenizers==0.23.3` 의존성 |
| `oa/dashboard/src/workspaceApi.ts` | 기록 처리량과 중간 그룹 통합 단계의 진행 메시지 |
| `tests/test_ai_batches.py` | 예산/전체 범위/서로 다른 조건/본문 인용/자식 이력·실패·재시작·권한·토큰·개정과 10만 건 합성 HTTP 검사 |
| `db/tests/benchmark_ai_batches.py`, `benchmark_total_storage.py` | `--measure-ai`: 실제 합성 worker와 전체 원본 전달 hash·최종 배정·감사 포함 네 테이블 용량 측정 |

부모는 기존 `oa-ai-v1`, 자식은 `oa-ai-batch-v1` namespace를 쓴다. 추가 DDL/테이블/GRANT는 없다.
최종 분류 결과는 자식 결과 참조에서 복구하며 감사 이력에 전체 배정 목록을 다시 복제하지 않는다.
전체 공개 전까지 기존 정상 분류/사람 확정을 유지한다. 일부 실패는 전체 성공 공개를 막고,
호출 결과가 불명확하면 UNKNOWN으로 남겨 자동 재시도하지 않는다.

통합은 label/근거에 따른 정렬과 다음 라운드의 순서 이동으로 비교 후보를 만든다. 모델이 유지/병합을
결정하며 code/이름 일치가 자동 병합 규칙은 아니다. 모든 그룹 쌍을 비교하지 않으며 의미 검색 품질은
아직 검증되지 않았다. 중간 그룹 내부 재분류는 자동 실행하지 않는다. 상세 계약은
[oa/BATCHED_CLUSTERING.md](oa/BATCHED_CLUSTERING.md)를 읽는다.

## 3. 검증 결과와 정확한 한계

서로 다른 실행/소스 시점의 결과다. 아래 수치를 합치거나 최신 코드의 전수 통과로 표시하지 않는다.

| 실행 | 결과 | 비고 / 로컬 근거 |
|---|---|---|
| 순수 모델/게이트웨이 초기 검사 | 51 통과 | 초기 배치 pure 5 + 기존 모델/gateway |
| PG12.1 초기 전용 | 56 통과 | `/tmp/sanity-mock-pg-pfiw6gm4` |
| PG12.1 확장 전용 | 61 통과 | 10만 건 합성 HTTP/전체 partition 포함. 이후 최종 결과 저장을 참조형으로 변경. `/tmp/sanity-mock-pg-97hqf_ds` |
| PG12.1 이전 quick | 340 선택·실행·통과, 실패/오류/skip 0 | 190.008초. `/tmp/sanity-mock-pg-gkugakou/result.json` |
| PG16.15 최신 완료 전용 | 63 통과, 실패/오류/skip 0 | 104.697초. `/tmp/sanity-mock-pg-u6p032y3/tests.log`, `result.json` |
| 마지막 추가 pure 검사 | 7 통과 | 같은 label의 다른 조건, 전체 보존 본문/인용 포함 |
| 브라우저 | 26/26 통과 | `e2e/oa-ai-workbench.spec.ts`. 진행 표시 포함 |
| TypeScript / production build | 통과 | `npm run build` |
| PG12.1 추가 quick | 343 선택·실행·통과, 실패/오류/skip 0 | 299.361초. `/tmp/sanity-mock-pg-btjg33zx/result.json`; 마지막 prompt 버전 지문 변경 전 snapshot |
| PG12.1 full | 체크포인트 작성 시 실행 중 | 아래 위치에서 종료 결과 확인 필요 |
| 14MB 전체 저장량 + 배치 AI 감사 | **20,660,224 bytes, 20MB 목표 미달성** | 모든 입력/최종 배정 검증은 성공. 아래 상세 수치와 결과 파일 참고 |

신규 브라우저 시험에서 초기에는 “분류 결과 없음” 상태의 숨겨진 확정 버튼을 disabled로 기대해
1건 실패했다. 실제 DOM을 확인해 **버튼 없음 + 그룹 판정 탭 비활성**을 검사하도록 새 테스트를
수정했고 26개 전체가 통과했다. 기존 테스트/권한/기대값을 완화하지 않았다.

**마지막 미검증 변경:** 실행 중 배치 prompt 개정이 바뀌면 중단하도록 `BATCH_PROMPT_VERSION`을
`OAAdvisor.describe().prompt_versions`에 결합했고
`test_batch_prompt_version_change_stops_between_children`를 추가했다.
위 PG16 63개, quick 343개, 실행 중 full과 용량 측정의 소스 snapshot은 이 마지막 변경 전이다.
현재 코드의 전용/quick 재검증과 snapshot 이후 diff 확인이 남는다.

합성 용량 측정은 POST 14,000,000 bytes·49,000건과 PRE 224 bytes·4건이며 보존 본문은 포함하지 않았다.
AI 전 네 테이블 합계 12,820,480 bytes에서 감사/분류 제안 포함 **20,660,224 bytes(20.66MB)**가 됐다.
목표 20,000,000 bytes를 **660,224 bytes** 초과한다. OA 테이블 TOAST/index 포함 크기는 8,445,952 bytes다.
원본 byte·52개 패킷·VWP/OA 전체 기록 hash와 **실제 leaf HTTP 입력 hash**를 대조했고 최종 partition도 일치했다.
모델 호출은 mock 442회(leaf 419회), worker 448단계, 감사 행 1,780개, 최대 body 135,843 bytes다.
worker 측정 387.379초는 동시 시험 중인 로컬 mock 실행이며 사내 모델 응답 시간을 예측하는 수치가 아니다.
fixture DB는 종료했다. **완료된 용량/분류 검사와 미달성 저장 목표를 구분한다.**

## 4. 실행 중인 검사와 환경

2026-10-28 21:52 KST에 아래 프로세스의 실행을 확인했다. 이후 quick과 용량 측정은 완료/DB 종료를 확인했다.
full은 여전히 확인 대상이다. PID는 재사용될 수 있으므로 반드시
명령/시작 시각을 다시 확인한다. 이미 실행 중인 같은 검사를 중복 시작하지 않는다.
완료된 fixture는 runner의 finally에서 DB를 종료한다. 살아 있는 runner를 무작정 죽여 fixture를 남기지 않는다.

| 작업 | wrapper / child PID | 로그·결과 |
|---|---|---|
| PG12 full | 306548 / 306602 | `/tmp/sanity-mock-pg-q31b0ys2/tests.log`, `tests.json`, `result.json` |
| PG12 quick — 완료 | 이전 308944 / 309029 | `/tmp/sanity-mock-pg-btjg33zx/tests.log`, `tests.json`, `result.json` |
| 합성 저장량+AI — 완료 | 이전 306582 / 306627 | `/tmp/sanity-total-storage-yd7fcvau/benchmark.log`; 최종 `/tmp/sanity-oa-batches-20261028/storage-ai.json` |

실행 메모/전용 harness: `/tmp/sanity-oa-batches-20261028/WORK.md`, `validate.py`.
`WORK.md`에는 시간순 이전 상태도 있어 최신 문단과 실제 결과 파일을 우선한다. `/tmp` 자료는 이 머신에만 있다.

- Python: `/root/work/sanity_log_parser/.venv/bin/python`
- PG12.1: `/tmp/sanity-storage-pg12-tools-20261027-y8y6yn1b/install/bin`
- PG16.15: `/tmp/sanity-oa-gateway-pg-tools-20261026-pps4utch/root/usr/lib/postgresql/16/bin`
- PG16 실행용 `LD_LIBRARY_PATH`: 위 도구 root의 `usr/lib/x86_64-linux-gnu`
- 프런트엔드는 이 작업 worktree의 `oa/dashboard/node_modules`를 사용했다. 별도 test port 5199를 사용해 다른 UI preview를 건드리지 않았다.
- 이번에 개발 venv에 tokenizers와 그 의존성을 설치했다. 재현 시 requirements를 설치한 환경을 사용한다.

다른 머신의 독립 검증은 저장소의 runner를 사용한다. 실제 회사 DSN을 테스트에 연결하지 않는다.

```bash
.venv/bin/python db/tests/run_mock.py --pg-bin /path/to/postgresql/bin --profile quick
# 분류/대용량 계약 변경의 전수 검사. 이미 돌아가는 full 결과를 먼저 확인한다.
.venv/bin/python db/tests/run_mock.py --pg-bin /path/to/postgresql/bin --profile full

.venv/bin/python db/tests/benchmark_total_storage.py \
  --pg-bin /path/to/postgresql/bin --rows 49000 --source-bytes 14000000 \
  --measure-ai --out /tmp/new-synthetic-batch-storage.json

cd oa/dashboard
npm run build
SANITY_DASHBOARD_TEST_PORT=5199 npx playwright test e2e/oa-ai-workbench.spec.ts
```

전용 DB 테스트는 독립 fixture 내부에서 `tests.test_ai_batches.BatchPureTests`,
`BatchDatabaseTests`, `tests.test_ai_jobs`, `tests.test_ai_credentials`, `tests.test_preserved_text`를 실행했다.
DB 없는 환경의 skip을 DB 검증 성공으로 표시하지 않는다. 10만 건 검사는 `BatchLargeTests`이며 quick에서 제외된다.

## 5. 이어서 할 작업

1. 브랜치를 받고 작업 상태를 확인한다. 기존 root/UI worktree와 다른 작업자의 변경은 보존한다.
2. 실행 중이던 full의 종료·실패/오류/skip·DB 종료 상태를 먼저 확인해 결과를 기록한다.
   full 결과는 마지막 prompt 버전 결합 전 snapshot임을 명시한다.
3. 마지막 prompt 버전 변경/새 회귀를 전용 검사와 quick으로 검증한다. 그 뒤 추가 실질 변경이
   없다면 이미 통과한 10만 건 검사를 무조건 반복하지 않는다. 새 분류/저장 계약 변경이 있으면 해당 full 범위를 다시 검토한다.
4. child plan/result hash chain·예산/단일 기록 과대·동일 label의 다른 원인·많은 고유 그룹·늦은 결과·
   권한/credential/개정 변경을 최종 코드 기준으로 검토한다. 모델 후보 정렬의 품질 한계도 숨기지 않는다.
5. **20.66MB → 20MB 이하의 남은 저장 최적화**를 검토한다. 자식 metadata/인덱스/최종 제안의 중복과
   실제 물리 용량을 분석하되 원문·배정·근거·감사를 삭제하지 않는다. 변경하면 같은 전체 제출/AI 검사를 재측정한다.
   수집만의 이전 20MB 결과나 다른 보존 본문 구성을 대신 쓰지 않는다.
6. 실제 결과로 `REVIEW.md`와 이 handoff의 상태를 갱신하고, 비공개 원본 `question_from_internal/oa.md`에도
   날짜·구현/검증/미완료·현장 설정 안내를 추가한다. 원문/이전 답변 보존과 Git 제외 정책을 유지한다.
7. 검토 가능한 PR로 인계한다. 현재 체크포인트를 main 병합/사내 배포 완료로 보고하지 않는다.

사내 AI의 수정 허용 범위는 늘어나지 않는다. 회사 담당은 지원 설정·한도/토크나이저 자료·
실제 독립 정답 평가를 준비하고, 공통 구현/디버깅은 설계 담당 Codex가 이어간다.
