# Area/Power AI 이상치 분석 Implementation Plan

> **For agentic workers:** 이 세션에서 직접 순서대로 구현한다(사용자 지시: "원하는 대로 구현하고 그다음 수정"). 체크박스로 진행을 표시한다.

**Goal:** 비교 화면의 "AI 분석" 버튼으로 instance 단위 이상치를 선별·판정하고, 검토 후 요약 메일 한 통을 보낸다.

**Architecture:** 후보 선별·진단 자료·응답 검증은 공유 모듈(`area_power/common/triage.py`)의 순수 코드다. OA는 미리보기·실행 요청·결과 조회·메일 요청만 하고, 중앙 VWP가 `judge` job으로 AI를 호출한다. 요약 메일은 OA mail consumer가 보낸다.

**Tech Stack:** Python 3.11+/FastAPI/psycopg/pydantic, React/TypeScript/zod. 새 의존성 없음.

**Spec:** `docs/superpowers/specs/2026-11-17-area-power-ai-triage-design.md`

## Global Constraints

- 새 테이블·DDL·job kind 없음. AI 작업은 `judge`, 메일은 `mail` job.
- 새 테스트·fixture·mock·검증 스크립트 **파일**을 만들지 않는다. 검증은 실제 코드를 메모리에서 실행(heredoc)하고 quick·build·데모 브라우저로 한다.
- 기존 엔진(`area_power/vwp_server/ai.py`), `/rejudge`, `comparability`/`evaluate_metric`의 동작을 바꾸지 않는다.
- AI 실패·미설정은 `needs_review`. 일부만 분석하고 완료로 표시하지 않는다.
- 데스크톱 1920×1080·5120×2160만 확인. 데모는 AI 호출을 흉내 내지 않는다.
- 상수: `ENGINE='instance-triage-v1'`, `BATCH_SIZE=20`, `MAX_ITEMS=400`, `ROLLUP_SHARE=0.8`, 기본 기준 Area +10% / Power +10% / Area 최소 50.

## Review Focus

1. 후보가 0개인 비교 → AI 호출 없이 완료, "후보 없음" 표시 (Task 3).
2. 기준 값이 0이라 Δ%가 없는 instance → 현재 값이 Area 최소 이상이면 후보, Δ%는 비움 (Task 1).
3. AI가 같은 id를 두 번 또는 모르는 id를 돌려줌 → 그 묶음 전체 `확인 필요(AI 응답 오류)`, 실행은 실패 표시 (Task 1).
4. 실행 중 요청자 권한 회수·AI 연결 변경 → 중단하고 남은 항목은 `확인 필요`, 실행은 실패 표시 (Task 3).
5. 같은 NET_VER의 REVISION 비교 → NET_VER 메모 0건, `same_rtl=true` (Task 2).

---

### Task 1: 공유 분석 코어 `area_power/common/triage.py`

**Files:** Create `area_power/common/triage.py`; Modify `area_power/common/analysis.py`(`ObservationView.parents` 추가), `area_power/common/ai.py`(`Runtime.validate(tasks=('CLUSTER','REVIEW'))`).

**Produces:**
- `Thresholds(area_increase_pct=10, power_increase_pct=10, min_area=50)` (pydantic, 유한·≥0)
- `side_context(view, current_process, baseline_process) -> dict` — `{'same_project','same_corner','area':{'analyzed','unconfirmed','reason'},'power':{...},'library':'same'|'unknown'|'different'}`
- `select_items(view, thresholds, watched_paths, context) -> Selection(items, missing_watched, counts, over_limit)`; `Item(position, group_id, reasons, watched, rolled_up)`
- `build_card(view, item, context) -> dict` (키: `id,current_path,baseline_path,reasons,watched,flags,area,power_mw,vth,drive,logic_cells,rolled_up_ancestors,top_children,ancestor_ids`)
- `batch_request(cards, notes, context, same_rtl) -> (context_dict, instructions)`; `BatchVerdict`/`ItemVerdict` 스키마 (`id, verdict, cause, explanation, note_ids, quotes`)
- `validate_batch(cards, notes, response, failure=None) -> list[dict]` (키: `group_id,card,verdict,cause,explanation,note_ids,quotes,failed,failure,adjusted`)
- 판정값 `expected|unexpected|needs_review|minor`, 원인 `note|vth_shift|drive_shift|new_instance|unknown`

- [x] 구현: 후보 조건(지표별), 조상 묶기(자식 후보 증가분 합 ≥ 80%), 신규 묶기(최상위만), 주요 instance(양방향·항상), 5.3a 제외 규칙, library 규칙(공정·corner)
- [x] 메모리 실행 검증: 조상 묶기·신규 묶기·기준 0·주요 instance 누락·상한 초과, 검증 가드(집합 불일치, 인용 없음→이상, 무효 인용→확인 필요, minor 제한)

### Task 2: OA 요청 측 `area_power/oa/triage.py`

**Files:** Create `area_power/oa/triage.py`; Modify `area_power/oa/service.py`(`ChangePayload.note_kind/net_ver`, `TargetPayload.watch`, `ProcessInput`, `project_process`, `baseline_setting`에 `process`·`baseline_process`), `area_power/oa/app.py`(라우트).

**Routes:** `POST /projects/{id}/process`, `POST /comparisons/{id}/triage/preview`, `POST /comparisons/{id}/triage`, `GET /comparisons/{id}/triage`, `GET /triage/{run_id}?offset&limit`, `POST /triage/{run_id}/mail`.

**Produces:** `triage_context(service, conn, comparison)`(적용 메모·경고·same_rtl·주요 경로·공정·표시 이름), `preview`, `request_run`(→ `ai_run` + `judge` job, 상한 초과 409), `runs`, `results`, `request_mail`(→ `triage_mail` 레코드 + `mail` job).

- [x] 구현: NET_VER 조회(Area 버전 `sanity_version_id`/binding → 호스트 `WS_VERSION.net_ver`), 계보 구간 메모 적용, 미리보기는 `GET /comparisons/{id}`와 같은 3단계 권한 패턴
- [x] 메모리 실행 검증: 버전 비교 구간·같은 NET_VER(same_rtl)·프로젝트 비교 누적·연결 없는 버전 경고

### Task 3: 중앙 VWP 실행 `area_power/vwp_server/triage.py`

**Files:** Create `area_power/vwp_server/triage.py`; Modify `area_power/vwp_server/worker.py`(judge 분기), `area_power/vwp_server/ai.py`(`new_attempt`가 분석 키 복사).

**Produces:** `run_triage(db, settings, run_id) -> dict`(최종 `ai_run`). `judgement` 레코드(부모=실행, dedupe=실행+group), 실행 payload `coverage{expected_items,processed_items,failed_items,batches,processed_batches}`, `missing_watched`.

- [x] 구현: REVIEW 연결만 검증, 묶음 호출, 입력 한도 시 반으로 분할, 권한·연결 오류 시 중단하고 남은 항목 `needs_review`
- [x] 메모리 실행 검증: 응답을 직접 넣어 정상·오류 묶음·권한 오류·후보 0개

### Task 4: 요약 메일 `area_power/oa/mail.py`

- [x] `mail_context`에 `triage_mail` 부모 분기, `digest_message(...)`, `consume_once` 분기. 본문: 비교 대상·기준 숫자·적용 메모 수·판정별 목록·제외·실패 사실
- [x] 메모리 실행 검증: 본문 구성과 제외 항목

### Task 5: 화면 — 버전 비교·공정·메모·주요 instance

**Files:** Modify `ComparisonViews.tsx`, `App.tsx`(change·target 조회), `api.ts`; Create `NotesPanel.tsx`.

- [x] 비교 대상 전환(`기준 프로젝트` / `같은 프로젝트의 이전 버전`), `previous_version` 요청, 공정 입력
- [x] NET_VER 메모·프로젝트 변경 메모·주요 instance 편집(`POST /config`)

### Task 6: 화면 — AI 분석 패널과 트리 표시

**Files:** Create `TriagePanel.tsx`; Modify `HierarchyBrowser.tsx`(`marks`, `locate`), `ComparisonViews.tsx`, `styles.css`.

- [x] 기준 입력·미리보기·실행·진행·결과 목록·지난 실행·메일 보내기
- [x] 항목 클릭 → 트리에서 조상을 펼치고 선택, 행에 판정 표시

### Task 7: 데모 모드

- [x] `demo.ts`에 새 경로 처리. 같은 후보 규칙을 흉내 내되 판정은 전부 `needs_review`(AI 미호출), 메일은 미발송 표시

### Task 8: 검증·문서·발행

- [x] 백엔드 quick, tsc/build, 데모 두 해상도 확인
- [x] `area_power/README.md`, `oa/dashboard/README.md`, `docs/area_power/design.md`, `HANDOFF.md` 반영
- [x] commit·push·main, 인계 034(세 역할 + LATEST)
