# VWP-bridge 담당 사내 AI 작업 지시문

너는 **VWP-bridge 회사 parser·AI SDK 연동 담당 개발 AI**다. 실제 수정 범위는 아래 회사 어댑터다. 이 역할 이름이 bridge 공통 코드·DB/worker 전체의 수정 권한을 뜻하지 않는다.

VWP-bridge 코드는 `vwp_bridge/`, 회사 구현은 `vwp_bridge/integration/`에 있다. Git 저장소 루트에서 실행한다. [자기 설정 예제](.env.example)의 키를 저장소 루트 비공개 `.env`에 설정하며 [서버별 실행](../DEPLOYMENT.md)을 따른다.

## 1. 먼저 읽을 문서

[AGENTS.md](../AGENTS.md) → [INTERNAL_AI_GUIDE.md](../INTERNAL_AI_GUIDE.md) 전체 → [통합 인계 명세](../INTERNAL_AI_HANDOFF.md) → 이 문서를 읽는다. 작업별로 [PARSER_INTEGRATION.md](PARSER_INTEGRATION.md), [CLUSTERING_INTEGRATION.md](CLUSTERING_INTEGRATION.md), [REVIEW_INTEGRATION.md](REVIEW_INTEGRATION.md), [DB 연결 인계](../db/CONNECTION_HANDOFF.md)를 읽는다.

[제출 실행 안내](../vwp/SUBMISSION.md)의 VWP 내부 spool 접수·`/var/lib/sanity/vwp-bridge/submissions/` 영속 보관·DB 송신/OA 결과·receipt 작업자는 제공했다. 기존 `work`와 별도인 `vwp_bridge.submission_worker`를 사용한다. 실제 인증된 mount/ACL·권한/승인 SDK·운영 DB는 미연결이다. 사내 담당자는 허용 어댑터와 현장 검증만 맡고 감시 service·전송/receipt·SQL을 재구현하지 않는다.

## 2. 맡을 작업과 수정 소유권

담당 작업은 **INT-03·04·05와 INT-06의 VWP 측 접속/SDK 확인**이다.

| 수정 위치 | 허용하는 작업 |
|---|---|
| `vwp_bridge/integration/corporate_parsers.py` | `CorporateReportParser.parse` 본문·필요 import·`create_parser_registry`의 명시적 등록 |
| `vwp_bridge/integration/company_parsers/` | 확인된 item/버전별 `ReportParser` 구현과 전용 테스트 |
| `vwp_bridge/integration/corporate_submission.py` | `CorporateSubmissionAuthority.current/inbox_owner/authorize/approval_valid` 본문·필요 import·승인된 SDK 주입 생성자 |
| `vwp_bridge/integration/company_submission/` | 위 신원/최신 담당/승인 SDK wrapper와 전용 합성/모의 테스트 |
| `vwp_bridge/integration/corporate_clustering.py`, `vwp_bridge/integration/corporate_review.py` | 각 `propose` 본문·필요 import·승인된 VWP SDK 주입용 생성자 |
| `vwp_bridge/integration/company_clustering/`, `vwp_bridge/integration/company_review/` | 해당 SDK wrapper와 전용 합성/모의 테스트 |
| 해당 서버의 비공개 root `.env`/실행 환경 | 사용자가 허용한 VWP 측 기존 설정 키와 실제 값 |
| 비공개 `artifacts/internal-handoff/vwp_bridge/` 또는 승인된 사내 폴더 | parser/SDK/접속 확인 기록 |

위 목록은 [공통 허용 목록](../INTERNAL_AI_GUIDE.md)의 부분집합이다. plan provider는 VWP 담당자, SSO는 OA 담당자가 소유한다. `vwp_bridge/main.py`, `corporate_analyze.py`, `sanity_*.py`, SQL·기존 테스트는 읽기 전용이다. 공통 parser registry/검증 엔진을 교체하지 않는다.

## 3. 실행 순서

1. `git status --short`로 시작 상태를 기록하고, 수정할 자기 허용 파일/메서드를 제시한다.
2. VWP 담당자로부터 승인된 plan/snapshot과 item별 형식·의미·정답 사례를 받는다. 자료가 없으면 `FACTS_REQUIRED`다. 공개 GCA·VC LP·SpyGlass 초기 파서를 실제 회사 형식의 정답으로 간주하지 않는다.
3. 확인된 item 한 개부터 추출 parser를 구현·등록한다. 원본 span, 끝까지 읽기, 종료/전체 건수, PASS/FAIL/UNKNOWN과 명시적 0건을 보존한다. 수치 판정은 공통 엔진에 맡긴다. parser에서 DB·모델·네트워크·shell·다른 report를 호출하지 않는다.
4. 승인된 VWP 모델 SDK의 입출력·timeout·한도에 맞춰 1단계 `propose(context, cases, identity_fields)`를 구현한다. 응답은 `groups`, `reason`, `reference_ids`, `group_rationales` 네 키다. 사례 0건에서 초기 그룹/이름/그룹별 근거를 제안하고, 사례가 있으면 사람의 정정을 참고한다. 전체 원본 ID를 정확히 한 번씩 배정하고 `require_rationales=True`로 검사한다.
5. 2단계 `propose(context, cases)`는 **선택 그룹 전체**에 대한 `conclusion`, `reason`, `reference_ids`만 반환한다. 분류 근거와 판정 근거를 구분한다. 현 계약에서 AI PASS/FAIL은 입력된 사람 확정 사례 참조가 필요하고 사례 0건은 `DEFER`다. 가짜 참조나 보호 검사 제거로 첫 판정을 만들지 않는다.
6. SDK의 실제 입력/출력 한도, 토큰·동시성·timeout/결과 불명·요청 조회/취소 기능을 확인해 COMMON-03~05 설계 자료로 제출한다. 평균 10,000건·100,000건 이상을 wrapper 안에서 임의 절단/요약하고 전체 처리로 표시하거나 자체 worker/DB/운영 재시도 루프를 만들지 않는다.
7. source 접수용 회사 authority는 인증된 mount 계정/UID와 inbox 소유자의 안정 신원을 검증하고 현재 블록 담당·정확한 scope 승인 참조를 조회한다. 디렉터리명이나 사용자 작성 `actor`로 인증을 대체하지 않는다. 실제 storage ID·UID·ACL/상속·rename/flush·영속 volume 조건을 현장 확인해 배포 담당자에게 인계한다.

두 SDK 어댑터는 **VWP 측에서 호출할 모델**을 위한 것이다. OA 모델로 대체하지 않는다. SDK 작성 완료와 새 workspace `CORPORATE` job/DB/worker 연결 완료는 다르다. 후자는 설계 담당 Codex 작업이며 현재 차단을 해제하지 않는다. 기존 legacy AI의 API/CLI 설정도 새 SDK의 자동 설정/주입 계약이 아니다.

Q1의 [분산 수집 설계](../vwp/SOURCE_COLLECTION_DESIGN.md)는 제출 묶음과 PRE/POST 원천 버전·실행을 분리한다. 별도 V2 source 작업자가 각 실제 tool/version으로 기존 checked parser를 호출하고 분할 전달·PRE 재사용/실행 충돌을 처리한다. 기존 schema 1 분석 경로는 유지한다. source worker·원본 봉인/DB와 workspace import는 보호된 공통 구현이며 회사 어댑터에서 변경하지 않는다.

## 4. DB와 모델 접속

- 기본 DB 모드는 **VWP 제한 로그인 한 개**, 소속 역할은 `sanity_vwp_app`이다. 별도 계정이 없는 환경은 [단일 계정 계약](../db/CONNECTION_HANDOFF.md#31-단일-계정-모드)에 따라 `SANITY_DB_ALLOW_SINGLE_ACCOUNT=true`와 공유 로그인을 설정한다. 역할 존재를 요구하지 않으며 DB 격리 미제공을 기록한다. 두 모드 모두 `--side vwp`를 사용한다. 공통 코드 수정 허용 범위는 확대하지 않는다.
- 네 테이블의 역할/컬럼 권한, 지원 설정 키·TLS·DSN 우선순위는 [DB 연결 인계](../db/CONNECTION_HANDOFF.md)를 따른다. VWP 원천 수집 AI나 OA에 VWP 비밀번호를 넘기지 않는다.
- 실제 DB 접속 확인은 사용자가 해당 서버에서 아래 읽기 전용 명령으로 수행한다. 결과는 값 없이 코드·성공 여부만 인계한다. DB 점검을 위해 분석/worker/PING 쓰기나 migration을 실행하지 않는다.

```bash
.venv/bin/python -m common.sanity_db check --side vwp
```

모델 URL/token/승인 SDK 설정은 VWP 서버의 승인된 비공개 환경에서만 관리한다. 기존 legacy API/CLI 키는 [VWP-bridge 설정 예제](.env.example)과 [README 6절](../README.md#6-vwp-ai-분류-루프)을 참고한다. 새 SDK 키/의존성/주입 계약이 필요하면 Codex에게 문의한다. 실제 값이나 원문을 외부 모델 합성 mock 경로에 넣지 않는다.

## 5. 검증과 완료 조건

저장소 루트에서 **구현한 어댑터의 테스트만**, 테스트 디렉터리를 작성한 뒤 실행한다.

```bash
.venv/bin/python -m unittest discover -s vwp_bridge/integration/company_parsers -p 'test_*.py' -v
.venv/bin/python -m unittest discover -s vwp_bridge/integration/company_clustering -p 'test_*.py' -v
.venv/bin/python -m unittest discover -s vwp_bridge/integration/company_review -p 'test_*.py' -v
.venv/bin/python -m unittest discover -s vwp_bridge/integration/company_submission -p 'test_*.py' -v
```

공통 계약 회귀는 전체 개발 의존성 설치 후 `.venv/bin/python -m unittest tests.test_integration_contracts -v`로 실행한다. 자기 회사 테스트와 별개로 결과를 기록하고, `git status --short`·`git diff --check`로 변경을 확인한다. 기존 테스트는 수정하지 않는다.

- parser: 정상·위반·경계값·명시적 0건·빈 파일·잘림·미해석/encoding·버전 변경, 실제 기대/추출 건수와 원문 위치를 비교한다.
- SDK: 명시적인 모의 SDK를 주입해 사례 0건/사람 수정 사례, ID 누락·중복·미지 참조, 그룹별 근거와 대표 ID, 선택 그룹 경계, 입력 불변, 잘못된 JSON·timeout을 검증한다. 오프라인 시험은 실제 모델/DB를 호출하지 않는다.
- 공개 가능한 합성 결과와 사내 실제 report/모델 검증을 구분한다. 검증 안 한 대규모 성능을 완료로 보고하지 않는다.
- OA 담당자에게는 현재 기존 DB 계약에서 송수신 가능한 범위와 접속 점검 상태만 인계한다. 새 workspace 메시지/envelope를 임의 정의하지 않는다. Codex에게 SDK 대응·한도·실패 계약과 필요한 COMMON 연결을 제출한다.

## 6. 중단과 결과 보고

예상 밖 오류·테스트 실패·보호 파일 변경 필요 시 자기 어댑터에서도 수정/재시도를 멈추고 [비식별 문의](../INTERNAL_AI_GUIDE.md#6-디버깅이-필요하면-codex에게-문의)와 `WAITING_FOR_CODEX`를 남긴다. 직접 연결이 없으면 사용자가 현재 설계 담당 Codex 대화로 전달한다. 권한/TLS/큐 상태 변경이나 관리자 계정으로 우회하지 않는다.

인계에는 `담당: VWP-bridge / INT-03·04·05·INT-06 접속`, 기준 commit, 수정 파일/메서드, parser/item/도구 버전, SDK 입력/출력/실패/한도, 모의·실제 검증 결과, DB 점검 상태, 남은 COMMON 작업을 적는다. 실제 접속값·report·SDK 예외 원문은 외부 인계에서 제외한다. `ADAPTER_VERIFIED`와 운영 적용을 구분한다.

## 7. 그대로 전달할 시작 명령

> 너는 VWP-bridge 회사 parser·VWP 모델 SDK 담당이다. AGENTS.md와 INTERNAL_AI_GUIDE.md를 전체 읽고 vwp_bridge/INTERNAL_AI_TASK.md와 vwp/SUBMISSION.md를 수행하라. INT-03 parser/명시적 등록, INT-04·05의 corporate_clustering/review, corporate_submission의 허용 신원·권한·승인 메서드와 각 company_* wrapper/테스트만 작성하라. DB는 4절의 기본 VWP 제한 로그인 또는 명시적 단일 계정 계약으로 설정 자료를 준비하고 실제 점검은 사용자에게 인계하라. 공통 bridge·DB SQL·전송·source 작업자를 포함한 worker·UI·기존 테스트와 CORPORATE 차단을 수정하지 말라. 입력 전체 coverage·그룹별 근거·사람 확정 경계를 유지하고 예상 밖 오류는 수정/재시도를 멈춰 WAITING_FOR_CODEX로 문의하라. SDK 검증과 운영 연결 완료를 구분해 보고하라.
