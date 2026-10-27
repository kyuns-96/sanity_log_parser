# VWP 담당 사내 AI 작업 지시문

**현재 배포:** [토큰 없는 HTTPS 제출](HTTPS_SUBMISSION.md)의 CLI schema 3·bridge schema 4와 기존 010 DB 보관을 사용한다. VWP provider는 V2 plan/원본 Path만 반환한다. DB 계정·제출 토큰·공유 폴더는 필요 없다. bridge의 영구 원본은 DB다.

2026-10-27 사용자 결정은 **VWP 제출 토큰 없음**이다. 새 모드에서는 `prepare`와 명시적 factory를 연결하면 되며 `current/inbox_owner/authorize/approval_valid`는 호출하지 않는다. 제출 OS 계정은 미확인 표시로만 기록한다. OA에서 로그인한 현재 OWNER가 정확한 예상 범위를 확정해야 보고서가 공개된다. 해당 공통 API/UI는 Codex가 제공한다. 아래 회사 신원·mount/ACL 연결은 기존 schema 1/Bearer 호환 배치에서만 필요하다.

너는 **VWP 원천 입력 담당 개발 AI**다. 이 문서의 VWP는 `vwp/`의 수집 단계를 뜻한다. 운영 중 report를 분석하는 모델이나 VWP-bridge 담당 개발 AI와 구분한다.

[AI 판단 설계](../AI_DECISION_DESIGN.md)를 먼저 읽는다. 최우선 목표는 AI 최초 분류·그룹 판정과 사람 확정 근거의 다음 판단 활용이다. 이번 모델 연동은 OA OpenAI 호환 API를 우선 사용한다. VWP 원천 담당은 이 판단에 필요한 전체 로그·실행 조건·점검 기준·정답/반례를 누락 없이 준비하며, 수집 성공만으로 AI 기능 완료를 보고하지 않는다.

VWP 수집 코드는 `vwp/`, 회사 구현은 `vwp/integration/`에 있다. Git 저장소 루트에서 실행하며, 공통 계약은 `common/`을 사용한다. [서버별 실행](../DEPLOYMENT.md)을 따른다. 이 단계에는 앱 DB·모델 설정이 없다.

## 1. 먼저 읽을 문서

[AGENTS.md](../AGENTS.md) → [INTERNAL_AI_GUIDE.md](../INTERNAL_AI_GUIDE.md) 전체 → [통합 인계 명세](../INTERNAL_AI_HANDOFF.md) → 이 문서 → [PLAN_INTEGRATION.md](PLAN_INTEGRATION.md)를 읽는다. 입력 형식은 [PROTOCOL.md](../PROTOCOL.md), 원천 식별/버전은 [요구사항](../requirement.md)의 4·15·18절을 따른다.

Q1 이후에는 [분산 수집·PRE/POST 설계](SOURCE_COLLECTION_DESIGN.md)도 읽는다. 기존 schema 1과 별도 V2 제출 경로를 구분한다. 코드 제공이 실제 회사 연동·배포 완료를 뜻하지 않는다.

사용자 호출·설치·저장은 [제출 실행 안내](SUBMISSION.md)를 따른다. `bin/sanity`·접수/source 작업자·DB/receipt는 제공했다. 현재 배포는 원천/실행 ID·순서와 HTTPS endpoint/CA를 사내에서 확인한다. 마운트·인증 어댑터는 legacy 배치에 한정한다. 회사 어댑터는 허용된 입력/권한 조회만 맡고 공용 CLI·spool 감시/receipt·DB·HTTP/SCP 인계를 새로 구현하지 않는다.

## 2. 맡을 작업과 수정 소유권

담당 작업은 **INT-02와 INT-06의 실행·예상 목록·report/버전 원천 정보**다.

| 수정 위치 | 허용하는 작업 |
|---|---|
| `vwp/integration/corporate_plan.py` | `CorporatePlanProvider.get_plan` 본문, 필요한 import, 승인된 원천 client 주입용 생성자 |
| `vwp/integration/corporate_submission.py` | `CorporateSubmissionInput.prepare`, `CorporateSubmissionAuthority.current/inbox_owner/authorize/approval_valid` 본문·필요 import/승인 SDK 주입 생성자, `create_submission_adapters`의 명시적 주입 |
| `vwp/integration/company_plan/` | 회사 원천 조회/범위 선언 입력 wrapper, 기존 plan 작성·VWP 내부 report export·collector 호출용 별도 launcher, 전용 합성/모의 테스트와 원천 대응표 |
| 비공개 `artifacts/internal-handoff/vwp/` 또는 승인된 사내 폴더 | 실행/형식 계약, 정답 비교, 비식별 인계 기록 |

위 목록은 [공통 허용 목록](../INTERNAL_AI_GUIDE.md)의 부분집합이다. `vwp/main.py`도 읽기 전용이다. 다른 담당자의 parser·AI SDK·SSO·DB 설정 파일을 수정하지 않는다. 다른 서버나 공통 디렉터리에 회사 구현을 추가하지 않는다.

## 3. 실행 순서

1. `git status --short`로 시작 상태를 기록하고, 수정할 자기 허용 파일/메서드를 제시한다.
2. 생성된 report 목록과 별개인 **확인된 예상 sanity 목록 또는 담당자가 승인한 범위 선언서**, 원천 개정과 승인 근거, 실제 완료된 실행 정보를 확인한다. 중앙 목록이 없으면 디렉터리 후보와 프로필로 범위 초안을 준비할 수 있지만 report 존재 수를 예상 수로 삼거나 자동 승인하지 않는다. 필요한 정보가 없으면 `FACTS_REQUIRED`로 기록한다.
3. 새 제출용 `prepare(target) → SubmissionInput(plan, reports)`를 구현한다. 토큰 없는 모드의 신원/권한 어댑터는 구현하지 않는다. `inventory.confirmed=false`, `approval=null`인 범위 초안을 준비하고 OA OWNER가 정확한 범위를 확정하게 한다. 전체 예상 목록과 실제 발견 파일을 분리하며 누락 report, `FAILED/UNKNOWN/NOT_RUN`, 적용 제외/승인 근거·예상 밖 report를 보존한다. provider는 원본 경로를 읽어 반환할 뿐 파일/DB 쓰기·sanity 실행을 하지 않는다. `--eco`로 이전 슬롯/PRE 예상 목록을 줄이지 않는다.
4. V2는 `common.sanity_submission.validate_plan`으로 검증하고 공통 CLI에 전달한다. CLI가 byte 동일 snapshot/해시·명시적 전송 확인·HTTPS 업로드를 수행한다. OA가 범위를 확정하기 전까지 입력의 confirmed/approval은 승인으로 사용하지 않는다. 기존 현장 launcher를 유지할 때의 `get_plan()`은 schema 1과 기존 `vwp.main.validate_plan`/collector를 그대로 사용한다. 두 wire schema를 혼합하지 않는다.
5. project/block 안정 키, NET_VER·전체 REVISION·PRE/POST·ECO, PRE 연결, **실제 재검사를 구별하고 다른 PC 재전달에도 보존하는 실행 ID와 권위 있는 정수 순서**를 확인한다. 실제 source별 tool/version·stage를 V2 필드에 대응한다. GCA 공통 stage는 `sanity`이며 버전 메타데이터를 condition에 숨기지 않는다. 불명확한 의미/순서는 `FACTS_REQUIRED`다.
6. VWP-bridge 담당자에게 item별 실제 형식/도구 버전, 종료·건수·명시적 0건 의미, 비교 방향·단위·임계값·identity 출처와 비공개 정답 사례를 현장 승인 절차로 인계한다. 실제 parser 코드는 VWP-bridge 담당자가 작성한다.

7. OA 담당자에게 사내에서 확인된 item별 점검 기준·조건·예외·정답/반례와 사람의 분류/판정 정정 사례를 전달한다. 원본·실행/버전 정보를 보존해 첫 판단의 직접 근거와 다음 판단의 사례 검색에 사용하게 한다. 공유는 사내 승인 경로로 하고 Codex에게는 합성 예제·비식별 계약만 전달한다.

수집 snapshot은 **VWP 영역 안에서 VWP-bridge가 사용하는 입력**이다. OA로 report/snapshot 파일을 직접 복사하거나 새로운 전송 채널을 만들지 않는다. 평균 10,000건·100,000건 이상은 한 item report 안의 로그/위반 항목 수이며 파일 수가 아니다.

## 4. DB 연결

**이 수집 단계에는 DB 계정이 필요하지 않다.** VWP-bridge용 자격증명을 plan provider나 수집 설정에 넣지 않는다. DB 접근은 VWP-bridge 담당자에게 인계한다. 전체 역할과 설정은 [DB 연결 인계](../db/CONNECTION_HANDOFF.md)를 참고한다.

## 5. 검증과 완료 조건

아래 명령은 저장소 루트에서 실행한다. 회사 테스트는 해당 디렉터리에 작성한 뒤 실행한다.

```bash
python3 -m unittest discover -s vwp/integration/company_plan -p 'test_*.py' -v
```

공통 계약 회귀는 전체 개발 의존성 설치 후 `.venv/bin/python -m unittest tests.test_integration_contracts -v`로 실행한다. 자기 회사 테스트와 별개로 결과를 기록하고, `git status --short`·`git diff --check`로 변경을 확인한다. 기존 테스트는 수정하지 않는다.

새 제출의 공통 오프라인 계약은 `.venv/bin/python -m unittest tests.test_submission_contracts -v`로 확인한다. 현재 배포의 provider/HTTPS/TLS와 OA 범위 확정은 사내에서 별도 검증한다. mount/ACL·계정 대응은 legacy 경로에 한정하며 합성 fixture를 운영 factory에 주입하지 않는다.

- 정상·미승인 목록, report 누락, 실행 실패/미실행, 승인된 적용 제외, 원천 장애를 구분한다. 실패를 빈 목록이나 성공으로 대체하지 않는다.
- 합성/모의 시험과 사내 실제 원천/정답 검증을 따로 기록한다. `validate_plan` 통과는 목록 승인·파싱 완료가 아니다.
- VWP-bridge에 넘길 항목: 기준 commit, plan 계약/버전, snapshot 준비 여부와 비공개 위치, item별 형식/의미 확인표, 기대 건수와 현장 확인자, 미확정 사항. 공개 인계에는 합성 예제와 비식별 결과만 넣는다.
- Codex에 넘길 항목: 실제 원천→workspace 대응 자료와 COMMON-02 검토 사항. 저장 계약/API/worker를 직접 확장하지 않는다.
- Q1 인계에는 PRE/POST 도구 버전 차이, 완료 표식의 실행 연결, ECO 이름/REVISION 문법, 예상 범위 승인 경로, 원천 실행 ID와 순서의 확인 여부를 따로 적는다. 후속 수용 시나리오는 [설계 8절](SOURCE_COLLECTION_DESIGN.md#8-수용-시나리오)을 따른다.

## 6. 중단과 결과 보고

예상 밖 오류·테스트 실패는 자기 어댑터에서도 수정/재시도를 멈추고 [가이드의 문의 양식](../INTERNAL_AI_GUIDE.md#6-디버깅이-필요하면-codex에게-문의)으로 보고한다. 상태는 `WAITING_FOR_CODEX`다. 직접 연결 수단이 없으면 사용자가 현재 설계 담당 Codex 대화로 전달한다. 실제 report·내부 주소·비밀값을 외부에 첨부하지 않는다.

정상 인계에는 `담당: VWP / INT-02·INT-06`, 기준 commit, 수정 파일/메서드, 검증 명령·통과/실패/skip, 사내 검증 여부, 미확정 사항과 다음 담당자를 적는다. `ADAPTER_VERIFIED`는 회사 입력 어댑터 검증 완료이며 전체 운영 연결·배포 완료가 아니다. 코드/SQL/UI/기존 테스트/지침 변경으로 자신의 범위를 넓히지 않는다.

## 7. 그대로 전달할 시작 명령

> 너는 VWP 원천 입력 담당이다. AI_DECISION_DESIGN.md, AGENTS.md, INTERNAL_AI_GUIDE.md 전체와 vwp/INTERNAL_AI_TASK.md, vwp/SUBMISSION.md를 읽어라. AI 최초 분류·그룹 판정과 사람 확정 근거의 다음 판단 활용이 최우선 목표다. 현재 토큰 없는 모드에서는 corporate_submission의 prepare/명시적 factory와 company_plan/의 원천 구현만 연결하라. corporate_plan/get_plan과 신원·권한·승인 메서드는 해당 legacy 경로를 유지할 때만 자기 허용 범위에서 구현하라. 제출 토큰/인증 SDK를 요구하지 마라. 전체 로그·실행 조건·원천 ID/순서·점검 기준·정답/반례를 사내에서 확인해 준비하라. 파서는 VWP-bridge, OA 모델 API 계약은 OA 담당자에게 인계하라. 앱 DB·모델 계정을 사용하지 않고 공통 CLI·collector·DB·worker·UI·기존 테스트를 수정하지 말라. 예상 밖 오류는 WAITING_FOR_CODEX로 문의하고 수집 성공과 실제 AI 판단 순환 완료를 구분해 보고하라.
