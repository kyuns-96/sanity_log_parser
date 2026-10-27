# VWP-bridge 담당 사내 AI 작업 지시문

**현재 배포:** [토큰 없는 HTTPS 접수](../vwp/HTTPS_SUBMISSION.md)의 서버 schema 4/CLI schema 3과 기존 010 DB 원본 보관을 사용한다. 허용된 VWP 출발 IP에서 토큰 없이 받고, OS 계정은 미확인 표시로 보존한다. 회사 `authenticate_bearer/resolve_actor/authorize/approval_valid`는 새 모드에서 호출하지 않는다. 토큰 발급/검증 SDK를 구현하거나 기존 hook 보호를 해제하지 마라.

원본의 DB 저장·임시 복원·source 전송·OA 접수 대기 및 SSO OWNER의 범위 확정 API/UI는 Codex가 제공했다. 네 우선 작업은 회사 parser/명시적 registry와 실제 DB/TLS/출발 IP·임시 공간 검증이다. VWP는 bridge에만 접속하며 bridge↔OA는 기존 DB 경계다. 아래 인증된 mount/신원·승인 SDK 항목은 legacy 폴더/Bearer 배치에 한정한다.

너는 **VWP-bridge 회사 parser·원천 검증/전달 연동 담당 개발 AI**다. 기존 VWP 모델 경로를 유지할 때는 자기 회사 SDK 어댑터도 맡는다. 실제 수정 범위는 아래 회사 어댑터이며 공통 코드·DB/worker 전체의 수정 권한을 뜻하지 않는다.

[AI 판단 설계](../AI_DECISION_DESIGN.md)를 먼저 읽는다. 가장 중요한 기능은 AI 최초 분류·그룹 판정과 사람 확정 근거의 다음 판단 활용이다. 이번 모델 연동은 **OA OpenAI 호환 API를 우선 사용**한다. bridge는 이를 위한 전체 파싱 로그·원본 위치·점검 기준·실행 조건을 보존하고 DB로 전달한다. parser가 AI 그룹을 고정 사전/이름 매칭으로 미리 확정하지 않는다.

VWP-bridge 코드는 `vwp_bridge/`, 회사 구현은 `vwp_bridge/integration/`에 있다. Git 저장소 루트에서 실행한다. [자기 설정 예제](.env.example)의 키를 저장소 루트 비공개 `.env`에 설정하며 [서버별 실행](../DEPLOYMENT.md)을 따른다.

## 1. 먼저 읽을 문서

[AGENTS.md](../AGENTS.md) → [INTERNAL_AI_GUIDE.md](../INTERNAL_AI_GUIDE.md) 전체 → [통합 인계 명세](../INTERNAL_AI_HANDOFF.md) → 이 문서를 읽는다. 작업별로 [PARSER_INTEGRATION.md](PARSER_INTEGRATION.md), [CLUSTERING_INTEGRATION.md](CLUSTERING_INTEGRATION.md), [REVIEW_INTEGRATION.md](REVIEW_INTEGRATION.md), [DB 연결 인계](../db/CONNECTION_HANDOFF.md)를 읽는다.

[HTTPS 제출 안내](../vwp/HTTPS_SUBMISSION.md)의 API와 별도 `vwp_bridge.submission_worker`를 사용한다. 영구 archive/upload/receipt 폴더는 필요 없으며 원본은 DB에 보관한다. 회사 parser와 비공개 배포 설정을 연결하고 공통 저장/전송/확정 코드를 재구현하지 않는다.

## 2. 맡을 작업과 수정 소유권

담당 우선 작업은 **INT-03 parser와 INT-06 원천 신원/승인·VWP DB 연결 자료**다. INT-04·05의 기존 VWP 회사 어댑터는 해당 실행 경로를 유지할 때만 사용한다. OA 모델 API·한도는 OA 담당자가 확인하고, OA 모델 실행·사례 검색·최초 직접 근거 판정 공통 코드는 Codex가 제공한다.

| 수정 위치 | 허용하는 작업 |
|---|---|
| `vwp_bridge/integration/corporate_parsers.py` | `CorporateReportParser.parse` 본문·필요 import·`create_parser_registry`의 명시적 등록 |
| `vwp_bridge/integration/company_parsers/` | 확인된 item/버전별 `ReportParser` 구현과 전용 테스트 |
| `vwp_bridge/integration/corporate_submission.py` | `CorporateSubmissionAuthority.current/inbox_owner/authorize/approval_valid`의 기존 회사 경로 본문·필요 import·승인된 SDK 주입 생성자. 토큰 없는 접수/범위 확정은 제공된 Codex 공통 코드, HTTPS 두 hook은 legacy 전용 |
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
4. **기존 VWP 모델 경로를 사용할 경우에만** 승인된 SDK의 입출력·timeout·한도에 맞춰 1단계 `propose(context, cases, identity_fields)`를 구현한다. 응답은 `groups`, `reason`, `reference_ids`, `group_rationales` 네 키다. 사례 0건의 초기 그룹과 사람 정정의 참고, 전체 ID 단일 배정과 `require_rationales=True` 검증을 지킨다. OA 기본 실행의 선행 조건으로 VWP SDK 완성을 요구하지 않는다.
5. 같은 VWP 경로의 현재 2단계 `propose(context, cases)`는 선택 그룹 전체의 `conclusion`, `reason`, `reference_ids`를 반환한다. 현재 PASS/FAIL은 사람 사례 참조가 필요하지만 **목표는 사례 없는 최초 직접 근거 판정도 포함**한다. 이 차이는 COMMON-04 공통 확장으로 해결하며 가짜 참조·검사 삭제로 우회하지 않는다.
6. SDK의 실제 입력/출력 한도, 토큰·동시성·timeout/결과 불명·요청 조회/취소 기능을 확인해 COMMON-03~05 설계 자료로 제출한다. 평균 10,000건·100,000건 이상을 wrapper 안에서 임의 절단/요약하고 전체 처리로 표시하거나 자체 worker/DB/운영 재시도 루프를 만들지 않는다.
7. 현재 토큰 없는 배포는 정확한 HTTPS Host·TLS·허용 출발 IP와 010 DB 설정·파싱 임시 용량을 확인한다. source 접수용 회사 authority는 legacy 경로를 유지할 때만 인증된 mount 계정/UID와 inbox 소유자의 안정 신원·블록 담당·승인을 연결한다. 디렉터리명이나 사용자 작성 `actor`로 인증을 대체하지 않는다. 실제 storage ID·UID·ACL/상속·rename/flush·영속 volume 조건을 현장 확인해 배포 담당자에게 인계한다.

현재 두 SDK 파일은 VWP 실행 위치의 어댑터다. OA 모델용 코드는 Codex가 OA 지점에 제공할 예정이며 VWP 어댑터를 복사하거나 망 간 OA API 호출로 바꾸지 않는다. OA 사용은 새 목표이며 기존 파일의 위치·보호 검사를 우회하는 지시가 아니다. SDK 작성 완료와 새 AI 판단/사례 검색·운영 연결 완료를 구분하고 `CORPORATE` 차단은 공통 구현 전까지 유지한다.

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

> 너는 VWP-bridge 회사 parser·원천 검증/전달 담당이다. AI_DECISION_DESIGN.md, AGENTS.md, INTERNAL_AI_GUIDE.md 전체와 vwp_bridge/INTERNAL_AI_TASK.md, vwp/SUBMISSION.md를 읽어라. AI 최초 분류·그룹 판정과 확정 근거의 다음 판단 활용이 최우선이며 이번 모델은 OA API 연동을 우선한다. INT-03의 허용 parser/명시적 등록과 company_parsers/ 테스트를 구현하라. 새 토큰 없는 경로에는 제출 토큰/신원 SDK가 필요 없다. corporate_submission의 신원·권한·승인 메서드는 해당 legacy 경로를 유지할 때만 자기 허용 범위에서 연결하라. 전체 로그·원본 위치·실행 조건·점검 기준·정답/반례를 보존해 OA 판단 입력으로 인계하라. 기존 VWP SDK는 그 경로를 유지할 때만 자기 허용 범위에서 구현하고 OA 실행의 선행 조건으로 삼지 말라. DB 점검은 4절을 따르고 공통 bridge·DB SQL·전송·worker·UI·기존 테스트·보호 장치를 수정하지 말라. 예상 밖 오류는 WAITING_FOR_CODEX로 문의하며 파싱/접속·모의 SDK 검증과 실제 AI 판단 순환 완료를 구분하라.
