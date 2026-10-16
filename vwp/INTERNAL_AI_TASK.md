# VWP 담당 사내 AI 작업 지시문

너는 **VWP 원천 입력 담당 개발 AI**다. 이 문서의 VWP는 `vwp/`의 수집 단계를 뜻한다. 운영 중 report를 분석하는 모델이나 VWP-bridge 담당 개발 AI와 구분한다.

VWP 수집 코드는 `vwp/`, 회사 구현은 `vwp/integration/`에 있다. Git 저장소 루트에서 실행하며, 공통 계약은 `common/`을 사용한다. [서버별 실행](../DEPLOYMENT.md)을 따른다. 이 단계에는 앱 DB·모델 설정이 없다.

## 1. 먼저 읽을 문서

[AGENTS.md](../AGENTS.md) → [INTERNAL_AI_GUIDE.md](../INTERNAL_AI_GUIDE.md) 전체 → [통합 인계 명세](../INTERNAL_AI_HANDOFF.md) → 이 문서 → [PLAN_INTEGRATION.md](PLAN_INTEGRATION.md)를 읽는다. 입력 형식은 [PROTOCOL.md](../PROTOCOL.md), 원천 식별/버전은 [요구사항](../requirement.md)의 4·15·18절을 따른다.

## 2. 맡을 작업과 수정 소유권

담당 작업은 **INT-02와 INT-06의 실행·예상 목록·report/버전 원천 정보**다.

| 수정 위치 | 허용하는 작업 |
|---|---|
| `vwp/integration/corporate_plan.py` | `CorporatePlanProvider.get_plan` 본문, 필요한 import, 승인된 원천 client 주입용 생성자 |
| `vwp/integration/company_plan/` | 회사 원천 조회 wrapper, 기존 plan 입력을 작성하는 별도 launcher, 전용 합성/모의 테스트와 원천 대응표 |
| 비공개 `artifacts/internal-handoff/vwp/` 또는 승인된 사내 폴더 | 실행/형식 계약, 정답 비교, 비식별 인계 기록 |

위 목록은 [공통 허용 목록](../INTERNAL_AI_GUIDE.md)의 부분집합이다. `vwp/main.py`도 읽기 전용이다. 다른 담당자의 parser·AI SDK·SSO·DB 설정 파일을 수정하지 않는다. 다른 서버나 공통 디렉터리에 회사 구현을 추가하지 않는다.

## 3. 실행 순서

1. `git status --short`로 시작 상태를 기록하고, 수정할 자기 허용 파일/메서드를 제시한다.
2. 생성된 report 목록과 별개인 **권위 있는 예상 sanity 목록**, 버전과 승인 여부, 실제 완료된 실행 정보를 확인한다. 필요한 정보가 없으면 `FACTS_REQUIRED`로 기록한다.
3. `get_plan(run_reference) → CollectionPlan`을 구현한다. 기존 schema 1을 유지하고 누락 report, `FAILED/UNKNOWN/NOT_RUN`, 적용 제외와 그 승인 근거를 보존한다. 조회 성공으로 `confirmed=true`를 만들지 않는다. provider 자체는 report 수집·파일/DB 쓰기·sanity 실행을 하지 않는다.
4. 필요하면 회사 launcher에서 반환값을 기존 `vwp/main.py:validate_plan`으로 검사한 뒤 기존 collector에 전달한다. 원본이 작성 완료된 상태에서만 snapshot을 수집하며 원본은 변경하지 않는다. 실행 위치·출력 경로는 현장 담당자가 정한다.
5. 프로젝트/블록의 안정 ID, 블록별 NET_VER·REVISION·PRE/POST·ECO, 실행 ID·item·report 경로의 원천 대응표를 작성한다. 아직 전송 계약에 없는 workspace 필드는 **설계 자료로만 제출**한다.
6. VWP-bridge 담당자에게 item별 실제 형식/도구 버전, 종료·건수·명시적 0건 의미, 비교 방향·단위·임계값·identity 출처와 비공개 정답 사례를 현장 승인 절차로 인계한다. 실제 parser 코드는 VWP-bridge 담당자가 작성한다.

수집 snapshot은 **VWP 영역 안에서 VWP-bridge가 사용하는 입력**이다. OA로 report/snapshot 파일을 직접 복사하거나 새로운 전송 채널을 만들지 않는다. 평균 10,000건·100,000건 이상은 한 item report 안의 로그/위반 항목 수이며 파일 수가 아니다.

## 4. DB 연결

**이 수집 단계에는 DB 계정이 필요하지 않다.** VWP-bridge용 자격증명을 plan provider나 수집 설정에 넣지 않는다. DB 접근은 VWP-bridge 담당자에게 인계한다. 전체 역할과 설정은 [DB 연결 인계](../db/CONNECTION_HANDOFF.md)를 참고한다.

## 5. 검증과 완료 조건

아래 명령은 저장소 루트에서 실행한다. 회사 테스트는 해당 디렉터리에 작성한 뒤 실행한다.

```bash
python3 -m unittest discover -s vwp/integration/company_plan -p 'test_*.py' -v
```

공통 계약 회귀는 전체 개발 의존성 설치 후 `.venv/bin/python -m unittest tests.test_integration_contracts -v`로 실행한다. 자기 회사 테스트와 별개로 결과를 기록하고, `git status --short`·`git diff --check`로 변경을 확인한다. 기존 테스트는 수정하지 않는다.

- 정상·미승인 목록, report 누락, 실행 실패/미실행, 승인된 적용 제외, 원천 장애를 구분한다. 실패를 빈 목록이나 성공으로 대체하지 않는다.
- 합성/모의 시험과 사내 실제 원천/정답 검증을 따로 기록한다. `validate_plan` 통과는 목록 승인·파싱 완료가 아니다.
- VWP-bridge에 넘길 항목: 기준 commit, plan 계약/버전, snapshot 준비 여부와 비공개 위치, item별 형식/의미 확인표, 기대 건수와 현장 확인자, 미확정 사항. 공개 인계에는 합성 예제와 비식별 결과만 넣는다.
- Codex에 넘길 항목: 실제 원천→workspace 대응 자료와 COMMON-02 검토 사항. 저장 계약/API/worker를 직접 확장하지 않는다.

## 6. 중단과 결과 보고

예상 밖 오류·테스트 실패는 자기 어댑터에서도 수정/재시도를 멈추고 [가이드의 문의 양식](../INTERNAL_AI_GUIDE.md#6-디버깅이-필요하면-codex에게-문의)으로 보고한다. 상태는 `WAITING_FOR_CODEX`다. 직접 연결 수단이 없으면 사용자가 현재 설계 담당 Codex 대화로 전달한다. 실제 report·내부 주소·비밀값을 외부에 첨부하지 않는다.

정상 인계에는 `담당: VWP / INT-02·INT-06`, 기준 commit, 수정 파일/메서드, 검증 명령·통과/실패/skip, 사내 검증 여부, 미확정 사항과 다음 담당자를 적는다. `ADAPTER_VERIFIED`는 회사 입력 어댑터 검증 완료이며 전체 운영 연결·배포 완료가 아니다. 코드/SQL/UI/기존 테스트/지침 변경으로 자신의 범위를 넓히지 않는다.

## 7. 그대로 전달할 시작 명령

> 너는 VWP 원천 입력 담당이다. AGENTS.md와 INTERNAL_AI_GUIDE.md를 전체 읽고 vwp/INTERNAL_AI_TASK.md를 수행하라. INT-02의 corporate_plan.py/get_plan과 company_plan/만 구현하고 INT-06의 실행·버전·item/report 원천 대응과 비공개 정답 자료를 준비하라. DB 계정은 사용하지 않는다. 파서와 모델 SDK 코드는 VWP-bridge 담당자에게 맡기고 공통 collector·DB·worker·UI·기존 테스트를 수정하지 말라. 예상 밖 오류는 수정/재시도를 멈추고 비식별 문의와 WAITING_FOR_CODEX 상태를 남겨라. 완료 시 plan/사내 검증 결과와 VWP-bridge·Codex에 넘길 자료를 구분해 보고하라.
