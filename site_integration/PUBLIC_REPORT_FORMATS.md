# GCA · VC LP · SpyGlass 초기 파서와 합성 대시보드 인계

이 문서는 공개 열람 가능한 **공급자 문서**에서 확인한 구조와, 이번에 작성한 초기 구현의 범위를 구분한다.
실제 사내 report, 회사 계정, 회사 DB, EDA 실행 결과는 사용하지 않았다. 공급자 report 원문을 복제하지 않았으며
샘플의 메시지·경로·객체·수치는 직접 작성했다. 공개 문서의 버전은 사내 설치 버전의 호환성 보증이 아니다.

## 1. 확인한 자료와 형식

| 도구 | 공급자 자료의 공개 열람본 | 확인한 구조 | 초기 구현 |
|---|---|---|---|
| PrimeTime GCA | [PrimeTime GCA User Guide L-2016.06, Chapter 2 / Example 2-8](https://www.scribd.com/document/807828202/PrimeTime-GCA-User-Guide-2016) | `report_constraint_analysis` full text의 design/scenario/severity/rule 계층, 위반·waived 건수와 번호가 있는 상세 메시지 | `GcaPublicTextParser`, `gca_public_text_v1` |
| VC LP | [VC LP User Guide, Message Reporting and Iteration](https://www.scribd.com/document/624722971/VC-LP-UG) | `report_violations -app LP`, Management/Tree Summary, severity/stage/tag/count와 verbose 상세의 Tag·Description·source/sink/domain 필드 | `VclpPublicTextParser`, `vclp_public_text_v1` |
| SpyGlass | [SpyGlass Explorer User Guide W-2024.09, Reports](https://studylib.net/doc/28896253/spyglass-explorer-userguide), [SpyGlass Lint Turbo Structural User Guide](https://iccircle.com/static/upload/img20240912122116.pdf) | 여러 report 표현 중 `moresimple`의 rule/severity/file/line/message 상세. Turbo 계열은 ParentID 등 추가 열을 가짐 | `SpyglassMoresimpleParser`, `spyglass_moresimple_v1` |

[Synopsys GCA 백서](https://www.synopsys.com/content/dam/synopsys/implementation%26signoff/white-papers/GCA_wp.pdf)는
GCA 검사 배경을 확인하는 보조 자료다. 실제 구문 근거는 위 사용자 가이드다.
온라인 예제 파일을 받아 실제 EDA report로 검증한 결과가 아니다.

## 2. 지원하는 좁은 부분과 남은 차이

코드는 [public_parsers.py](public_parsers.py)에 있으며 기존 [추상 파서 계약](../vwp-bridge/PARSER_INTEGRATION.md)을 사용한다.
원문 reader/span은 공통 엔진이 제공하고, 파서는 필드와 원문 범위를 반환한다. PASS/FAIL은 공통 Decimal evaluator가 계산한다.
샘플 기준 `observed <= 0 violations`는 **이 예시만의 명시적인 테스트 정책**이다. 실제 item의 임계값이나 waiver 정책을 뜻하지 않는다.

- **GCA:** 단일 design/scenario/rule의 수치 요약과 `n of total waived message` 상세를 읽는다.
  rule·severity·scenario·design·객체 경로·waived를 보존하고, 상세 번호·건수와 plan의 rule/mode/design을 대조한다.
  waiver가 있으면 `WAIVER_REVIEW_REQUIRED`로 완료를 막는다. 다중 design/rule, 전체 native 들여쓰기·줄바꿈,
  default scenario 설명 차이, CSV, constraints/statistics/추가 섹션은 현장 대응 대상이다.
- **VC LP:** 단일 Tag의 Tree Summary와 `Tag :` 상세 블록에서 source/sink/domain/strategy를 읽고 상세 건수를 요약과 대조한다.
  public guide의 verbose 상세는 기본적으로 Tag당 일부 결과만 출력될 수 있으므로 summary-only 또는 요약보다 적은 상세는
  `SUMMARY_DETAIL_MISMATCH`로 완료를 막는다. 실제 도구에서 출력 제한 옵션·전체 추출 방식·종료 근거를 확인해야 한다.
  Management Summary 전체, 여러 Tag, 다른 stage/중첩 계층, 추가 debug 필드·waiver 출력은 아직 지원하지 않는다.
- **SpyGlass:** non-turbo `moresimple`의 `ID Rule Alias Severity File Line Wt Message`와 `[ID]` 상세 행을 읽는다.
  source file/line·weight·alias·message ID를 보존한다. `ParentID`가 있는 Turbo, VCStatic·summary·XML·HTML 등
  다른 출력 형식은 지원하지 않으며 UNKNOWN 원문으로 남긴다. native 줄바꿈·공백/인용 경로·중복 ID의 의미도 현장 확인 대상이다.

예시의 `+ ...` 후속 줄은 합성 fixture의 continuation 문법이다. 모든 공급자의 실제 wrap 문법을 구현한 것이 아니다.
인식하지 못한 내용은 metadata로 버리지 않고 UNKNOWN evidence로 남긴다. 이미 확인한 위반이 있으면
unit 판정은 FAIL이면서 파싱 상태는 PARTIAL일 수 있다. PARTIAL의 의미를 UNKNOWN 판정과 혼동하지 않는다.

## 3. 합성 표식과 운영 형식의 경계

아래 표식은 **우리가 만든 fixture 전용 문법**이며 공급자 native 출력이 아니다.

```text
# SANITY_SAMPLE family=GCA profile=public-reference-v1
# SANITY_ZERO rule=CLK_0003 violations=0
# SANITY_END records=1
```

샘플 registry는 `tool_version=public-reference-v1`, `rule_version=sample-zero-violations-v1`와 명시한 item만 허용한다.
`condition.sample_profile=public-reference-v1`의 합성 입력에서만 표식을 받아들인다. 시작·종료·건수와 native 부분의
수치 일치 여부를 확인하고, 명시적인 0건 표식은 원문 위치가 있는 PASS 근거로 저장한다. 종료 뒤 추가 내용도 EOF까지 검사한다.
빈 report나 FAIL 문구가 없는 파일만으로 PASS를 만들지 않는다.

표식 없는 native 부분도 지원하는 행은 추출하지만, 실제 버전의 완료 계약이 확인되지 않았으므로
`UNVERIFIED_REPORT_COMPLETION`을 남기고 PARTIAL을 유지한다. 회사 report에 표식을 붙여 COMPLETE로 만드는 방식은 운영 구현이 아니다.
일반 `vwp-bridge/main.py`와 회사 registry는 이 샘플을 자동 로드하지 않는다.
예시를 명시적으로 처리할 때만 [public_analyze.py](../vwp-bridge/public_analyze.py)가 초기 registry를 주입한다.

## 4. 1,000개 데이터 생성과 대시보드

구성은 가상 이용자 **10명**, 프로젝트 **Atlas/Boreal/Cygnus 3개**, 프로젝트마다 **CPU/GPU/DDR 3개 블록**이다.
TOP 3명은 각자의 프로젝트를, 나머지 7명은 배정 블록을 조회하는 예시로 구성했다. 일부 담당자는 다른 프로젝트의 블록도 가진다.
전체 예상 sanity item은 1,000개이며 실제 합성 `.rpt` 950개와 누락 50개를 포함한다. 각 블록에 세 도구가 모두 존재한다.

| 항목 | 실제 샘플 처리 결과 |
|---|---|
| 도구별 예상 item | GCA 334 / VC LP 333 / SpyGlass 333 |
| 수치 판정 | PASS 600 / FAIL 350 / UNKNOWN 50 |
| 파싱 상태 | COMPLETE 850 / PARTIAL 100 / MISSING 50 |
| 수집·분석·전송 묶음 | 프로젝트·블록·도구별 27개 |
| DB에서 읽어 내보낸 evidence | 1,000건 |

실제 PostgreSQL 16+ 서버 바이너리와 Python 의존성이 필요하다. 저장소 루트에서:

```bash
.venv/bin/python tools/build_public_preview.py --pg-bin /path/to/postgresql/bin --count 1000
cd oa-bridge/dashboard
npm ci
npm run build
npm run dev
```

주소: `http://127.0.0.1:5173/?demo=1&dataset=reports`.
배포된 static `dist/`에서도 같은 query를 사용한다. 신규 개발 포트가 필요하면 `npm run dev -- --port 5188`처럼 지정한다.

생성기는 매번 새 로컬 소켓/SCRAM PostgreSQL 인스턴스와 제한된 VWP/OA 계정을 사용한다.
`collect → 초기 파서 → VWP evidence/DB queue → OA 수신/저장 → DB 재조회`를 실행하고 원래 payload와 저장 payload가 같은지 확인한다.
그 결과를 `public/sample-data/reports-library.json`에 내보낸 후 mock DB를 종료한다.
사내 `.env`, 회사 어댑터/실제 report, 운영 DB와 실제 AI는 사용하지 않는다. 생성물은 Git에서 제외되며 다시 만들 수 있다.
`--count`는 27~10,000개를 허용하지만 이 문서의 집계와 브라우저 테스트는 기본 1,000개 기준이다.

화면은 **DB 처리 결과의 읽기 전용 스냅샷**이다. 사용자/프로젝트/블록 전환, 도구·결과 필터, 전체 범위 검색·25개씩 페이지 이동,
원문·물리 줄 위치·추출 필드·미완료 사유 확인, 합성 `.rpt` 다운로드를 제공한다.
브라우저의 담당 범위 전환은 실제 SSO/RBAC가 아니며 운영 데이터 보호를 대신하지 않는다. 실시간 DB, 운영 검토/PoR 저장을 구현한 화면도 아니다.

## 5. 사내 AI가 이어갈 작업

[INTERNAL_AI_GUIDE.md](../INTERNAL_AI_GUIDE.md)의 파일/메서드 허용 목록과 오류 문의 절차를 따른다.
이 공개 참고 파서는 고정 scaffold로 읽고, 회사 구현은 `company_parsers/`와 `corporate_parsers.py`의 허용 지점에 작성한다.

1. 실제 item·report 생성 명령·도구 버전·rule version·완료/0건/waiver/필수 섹션과 상세 출력 제한을 사내 담당자와 확인한다.
2. 공개 구현에서 참고할 추출 패턴을 회사 전용 `ReportParser`로 옮기고, item별 판정 기준은 승인된 plan으로 전달한다.
   실제 여러 형식에 동일 sample parser ID를 재사용하거나 샘플 표식으로 완료 검사를 대신하지 않는다.
3. native 종료 근거, 예상 범위/전체 건수, 다중 시나리오·Tag, 줄바꿈과 encoding을 검증한 뒤 명시적으로 실제 버전만 등록한다.
4. 정상·위반·명시적 0건·빈 파일·잘림·detail limit·미지원 섹션·다른 block/rule/version의 정답 사례를 사내에서 검증한다.
5. 회사 계정/실제 원문은 외부로 보내지 않는다. 공통 계약 변경이나 예상 밖 오류는 합성 재현 입력으로 Codex에게 문의한다.

공개 구현 테스트:

```bash
.venv/bin/python -m unittest discover -s tests -p test_public_parsers.py -v
.venv/bin/python db/tests/run_mock.py --pg-bin /path/to/postgresql/bin
cd oa-bridge/dashboard
SANITY_DASHBOARD_TEST_PORT=5188 npm run test:e2e
```

브라우저 테스트 전에는 위의 1,000개 스냅샷을 생성한다. 별도 테스트 포트는 다른 작업 디렉터리의 개발 서버를 재사용하지 않게 한다.
공통 mock은 공개 참고 코드만 복사하며 회사 구현·비공개 fixture는 여전히 수집하지 않는다.
