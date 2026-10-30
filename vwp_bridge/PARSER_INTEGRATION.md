# 사내 AI 인수인계 — item별 report parser

실제 report는 보안상 **사내 AI와 현장 담당자가 사내에서만 열람·검증**한다. 현재 대화의 Codex는 원문을 받지 않고 공통 인터페이스·검증 엔진·합성 회귀 사례를 관리한다. 이 개발 경계는 기존 사내 OA report 조회 계약과 별개다.

먼저 [AGENTS.md](../AGENTS.md)와 [INTERNAL_AI_GUIDE.md](../INTERNAL_AI_GUIDE.md)를 읽는다. 이 문서는 사용자가 요청한 파서 개발 범위를 구체화한다. 내부 AI는 아래 회사 구현 지점만 수정하고 공통 코드·DB·판정·coverage·기존 테스트는 수정하지 않는다. 예상 밖 오류는 자기 어댑터에서도 수정/재시도를 멈추고 `WAITING_FOR_CODEX`로 문의한다.

작업별 선행 정보/공통 연결 의존성은 [INTERNAL_AI_HANDOFF.md](../INTERNAL_AI_HANDOFF.md)의 INT-02/03을 따른다. 파서에 전달할 예상 목록은 [PLAN_INTEGRATION.md](../vwp/PLAN_INTEGRATION.md)의 `SanityPlanProvider`로 기존 plan 계약에 대응한다. 이 provider와 파서가 workspace 버전 수신이나 회사 AI worker를 자동 연결하는 것은 아니다.

파서의 공통 인터페이스·정규화 규약·검증·판정·DB 저장·전송과 전체 흐름의 설계·구현·디버깅은 **현재 대화의 설계 담당 Codex**가 책임진다. [VWP-bridge 담당 AI](INTERNAL_AI_TASK.md)는 확인된 사내 형식을 이 고정 규약에 대응하는 추출 어댑터와 명시적 등록·전용 테스트만 구현한다. VWP 원천 담당자는 실제 형식·의미·정답 자료를 사내에서 준비한다. 실행 코드와 회사 구현은 [소스 구조](../DEPLOYMENT.md)의 `vwp_bridge/`와 `vwp_bridge/integration/`에 둔다.

**2026-10-28 수집 전용 구현:** `evaluation="none"`의 공통 등록·검증·전송·OA 연결을 제공한다. [4.1절](#collection-only-parser)의 상태·호환 계약을 따르고 양쪽 bridge에 수정본을 반영한 뒤 기존 회사 등록 지점에서 사용한다. 실제 형식·의미·정확성은 사내에서 별도 검증한다.

[plan 4](../vwp/PRESERVED_TEXT.md)는 별도 공통 UTF-8 전체 텍스트 추출·마스킹·AI 인용을 제공한다. 사내 parser의 자동 호출이나 SDC 실행은 아니며 기존 허용 parser 지점은 유지한다.

2026-10-30 [AI 입력 필드 선택](../oa/AI_INPUT_FIELDS.md)은 plan unit의 `ai_fields`·`include_raw`·`include_details`를 모델 요청에 적용한다. 파서는 선택 설정과 관계없이 전체 fields/raw/span을 추출하고, source 전달·DB 보관도 전체를 유지한다. parser registry 계약이나 허용된 회사 구현 범위는 변경하지 않는다.

**비-unit 보존 근거(plan 3):** full.rpt/SDC의 명시 보존은 별도 [plan 3 계약](../vwp/PRESERVED_SOURCES.md)을 따른다. 보존 선언만으로 이 parser가 호출되거나 본문이 OA AI에 전달되지는 않는다. 이번 연결은 전체 byte RAW 보관과 귀속·hash 메타데이터까지이며, 본문 추출/마스킹/AI 인용 연결은 후속 공통 작업이다. 기존 허용 회사 parser/등록 범위는 그대로다.

## 1. 제공 파일과 수정 책임

| 파일/위치 | 제공 내용 | 사내 AI의 작업 |
|---|---|---|
| [sanity_parsers.py](../common/sanity_parsers.py) | 추상 클래스·반환 모델·원문 reader·registry·공통 결과 검사 | 읽기 전용 |
| [corporate_parsers.py](integration/corporate_parsers.py) | `CorporateReportParser.parse` 미구현 메서드, 빈 `create_parser_registry` | 메서드 본문과 필요한 import, 명시적 파서 등록 |
| `vwp_bridge/integration/company_parsers/` | 사내에서 추가할 위치. 자동 발견/로드하지 않음 | `ReportParser` 구현 모듈과 해당 어댑터 전용 테스트 추가 |
| [corporate_analyze.py](corporate_analyze.py) | 회사 registry를 기존 VWP 분석 CLI에 주입 | 그대로 실행. 파일은 읽기 전용 |
| [parser_example.py](integration/parser_example.py) | 합성 텍스트 형식의 학습용 구현 | 읽기 전용. 실제 item에 등록하지 않음 |
| [test_parsers.py](../tests/test_parsers.py), [test_corporate_parsers.py](test_corporate_parsers.py) | 순수 계약 테스트와 PostgreSQL 합성 통합 테스트 | 읽기 전용 |

실제 report·fixtures·현장 계약서는 승인된 비공개 작업 폴더 또는 Git 제외 `artifacts/`에 둔다. 회사 어댑터 코드에 원문·객체명·접속값을 하드코딩하거나 실제 자료를 공개 저장소에 commit하지 않는다. 회사 형식 코드 자체의 공개 가능 여부도 현장에서 확인한다. Codex 디버깅에는 형식을 보존한 **합성 재현 입력과 기대값**만 전달한다.

## 2. 고정 추상 인터페이스

```python
class ReportParser(ABC):
    def parse(self, source: ReportSource, context: ParseContext) -> Iterable[ParseEvent]: ...
```

`parse`는 동기 함수이며 이벤트를 순서대로 `yield`하는 generator로 작성한다. `context.run`과 `context.unit`은 중첩 구조까지 읽기 전용이다. 실행 정보·조건·판정 설정·동일성 규칙을 읽되 변경하지 않는다. 어댑터 인스턴스가 여러 unit에 재사용되므로 입력별 buffer·count·상태는 메서드의 지역 변수로 둔다.

현재 제공한 공통 경로는 다음과 같다.

```text
확정된 plan → 원본 snapshot/해시 검사 → 등록 범위 대조
 → ReportParser.parse(ReportSource, ParseContext)
 → 원문 위치·전체 읽기·미해석 구간·완료 표시·건수 검사
 → 등록 방식별 처리(numeric: Decimal / reported: 원문 판정 / none: 구조 검사·판정 미실행)
 → PASS 포함 전체 evidence 저장 → 집계·봉인 → 기존 DB 전송
```

파서는 DB·네트워크·운영 AI·shell·다른 report를 호출하지 않는다. 원문 변환 파일을 만들거나 DB에 직접 쓰지 않는다. 이 인터페이스는 Python sandbox가 아니며 수정 경계는 지침·검토로 관리한다.

### 2.1. 정규화와 저장의 공통 책임

실제 report의 사내 열람·검증과 처리 경로의 개발 책임을 구분한다. 원본이 사내에 있다는 이유로 내부 AI가 정규화 schema·판정·DB protocol을 새로 설계하지 않는다. Codex가 아래 기존 계약과 실행 코드를 제공하고, 회사 어댑터는 확인된 원문 문법을 `ParseEvent`로 대응한다. 이 절은 기존 계약의 책임과 연결을 명확히 하며 새 schema나 수정 권한을 추가하지 않는다.

| 경계 | Codex가 제공하는 고정 계약·실행 | 사내 확인/구현 |
|---|---|---|
| 원본 → 추출 이벤트 | `ReportParser`, `ReportSource`, `ParseContext`, `ParsedRecord`·metadata·미해석·완료 이벤트 | 실제 GCA 등 지원 버전의 문법·섹션·필드·건수·종료 근거를 허용된 `parse()`에 대응 |
| 추출 이벤트 → 정규화 근거 | `checked_records`의 원문 위치·EOF·누락·완료/건수 검사, 공통 필드·모드 검사와 방식별 Decimal/명시적 판정 또는 판정 미실행 기록 | 실제 추출 기대값과 정규화 결과가 일치하는지 사내 fixture로 검증 |
| 정규화 근거 → VWP DB | `analyze_unit`, `SourceEvidence`의 record ID·원본 참조·상태·전체 건수 저장과 봉인 | 회사 파서에서 DB를 직접 쓰지 않음 |
| VWP DB → OA DB | `build_transfer`의 마스킹·분할·manifest/commit, 제출 worker의 outbox 발행, OA `SourceReceiver`의 범위·해시·전체 건수 검증과 반영 | 기존 허용 신원 어댑터·실환경 설정/검증. 공통 packet·저장 코드는 읽기 전용 |

`ParsedRecord.fields`의 공통 이름은 `code`, `severity`, `object`, `check_point`, `mode`다. `severity`는 `ERROR/WARNING/INFO/FATAL`을 사용한다. `numeric`은 정확한 십진 문자열 `observed`와 `unit`, `reported`는 원본의 명시적 `status=PASS/FAIL`을 사용한다. 필수 여부·판정·0건 의미는 4절의 기존 방식별 계약을 따른다. 회사 필드명을 공통 키에 대응하는 근거와 단위/판정의 의미는 사내에서 확인하며 공통 엔진이 추측하지 않는다.

어댑터가 반환한 `fields`와 공통 코드가 생성하는 저장 레코드는 구분한다. ID·원문 추출·해시·위치·범위·전체 건수·parser/규칙 버전은 공통 경로가 결합/검증한다. V2 OA 전달 레코드는 `id`, `fields`, `check_status`, `raw`, `line_start`, `line_end`, `reasons`, `evidence_id`, `source_raw_sha256`, `redacted`와 제공된 경우의 byte 위치·`json_pointer`를 사용하며, 원본 파일 hash·실행/버전·parser 계약은 report header/manifest에 결합한다. 수집 전용의 추가 marker/집계는 4.1절을 따른다. Python 파서 이벤트와 DB 전송 packet을 회사 코드에서 별도로 변환·재정의하지 않는다.

GCA 원문 추출 후에도 이 공통 경로 전체를 합성 입력으로 연결해 검증하는 것은 Codex 책임이다. 공통 검증 성공과 실제 사내 형식·업무 의미의 정확성 검증은 각각 결과를 남긴다. AI 초기 그룹·판정은 이후 OA 단계이며, 파서의 원본 규칙 결과를 AI 또는 사람의 최종 판단으로 저장하지 않는다.

## 3. 원문과 반환 이벤트

`for line in source.lines():`로 UTF-8 report를 읽는다. `line.text`는 줄 끝 CR/LF를 제거한 문자열, `line.span`은 공통 reader가 만든 실제 byte/물리 줄 위치다. 디코딩 실패는 `line.encoding_valid=False`이며 해당 span을 `UnparsedRegion(..., "INVALID_ENCODING")`으로 남긴다.

| 반환 모델 | 필드/역할 | 계약 |
|---|---|---|
| `ParsedRecord` | `fields`, `span`, 선택 `json_pointer` | 추출한 필드만 반환. 원문·ID·최종 수치 판정은 공통 엔진이 생성 |
| `ReportMetadata` | `span`, `kind` | 확인된 형식의 `HEADER`/`FOOTER`/`COMMENT`/`BLANK`만. 예상 못한 섹션을 metadata로 버리지 않음 |
| `UnparsedRegion` | `span`, `reason` | 해당 원문을 UNKNOWN 기록으로 보존. 사유는 `[A-Z][A-Z0-9_]{0,63}` 코드 |
| `ParseFinished` | `declared_records`, `complete`, `blockers=()` | EOF 확인 후 마지막으로 정확히 한 번 반환. 뒤에 이벤트를 추가하지 않음 |

한 줄 기록은 `line.span`을 쓴다. 여러 줄 기록은 첫/마지막 `SourceLine`을 보존해 `source.span(first, last)`로 만든다. 줄 번호·byte 위치·원문을 직접 만들어 반환하지 않는다. span은 원문 순서로 겹치지 않게 반환한다. 같은 섹션에 여러 검사가 있는 경우에 한해 연속 `ParsedRecord`의 **동일 span** 반복을 허용한다.

JSON은 `source.lines()`를 끝까지 읽고 전체 문서 span과 `/results/0` 같은 JSON Pointer를 반환할 수 있다. 공통 코드가 원본 JSON에서 Pointer를 실제로 해석해 원문을 만든다. 중복 키·없는 Pointer·잘못된 배열 위치는 거부한다. 추출 필드와 그 Pointer의 의미적 대응은 사내 기대값 테스트로 검증한다.

`declared_records`는 이 파서가 내보내야 하는 `ParsedRecord` 수다. report의 전체 검사 건수·필수 섹션/검사 목록 등 확인된 형식 계약에서 산출한다. metadata와 `UnparsedRegion`은 이 선언 건수에 포함하지 않는다. report가 위반 건수만 제공한다면 검사 결과 기록 수와 위반 건수를 구분하고 대응을 전용 테스트로 검증한다.

`complete`는 도구/형식 버전·필수 섹션·종료 표시·내부 건수 검증을 통과한 경우만 true다. Python generator가 끝났다는 사실이나 파일이 존재한다는 사실을 완료 근거로 삼지 않는다. 종료 표시를 만났어도 `break`하지 말고 EOF까지 읽어 예상 밖 후속 내용도 검사한다.

공통 검증은 원문 전체 읽기 여부와 span으로 설명되지 않은 구간을 확인한다. 누락 구간은 원문 순서로 UNKNOWN evidence에 추가하고 `UNPARSED_CONTENT`를 남긴다. EOF 미확인·마지막 이벤트 누락·건수 불일치는 완료를 막는다. 단, 공통 검사가 metadata 의미나 실제 업무 추출의 정확성을 증명하지는 않는다. 이 부분은 현장 사례·반례와 담당자의 검증이 필요하다.

기본 한도: 물리 줄 UTF-8 bytes 1MiB, 한 원문 span 64MiB, Pointer를 사용하는 JSON 전체 16MiB. 초과하면 `PARSER_SOURCE_LIMIT`로 중단한다. 상한 변경·다른 encoding/바이너리 report·추가 공통 판정 방식이 필요하면 Codex에게 **합성 구조와 필요한 한도/규칙**을 문의한다.

## 4. 추출과 판정의 분리

registry의 `evaluation`은 파서별로 고정한다. 개별 이벤트가 판정 방식을 바꿀 수 없다. 아래 세 방식을 지원한다. 수집 전용의 상태·전송 계약은 4.1절을 함께 따른다.

| 등록 방식 | 추출 필드 | 공통 판정 |
|---|---|---|
| `numeric` | `code`, `severity`, `observed`(정확한 십진 문자열), `unit`, 객체/검사 지점/모드 | 기존 plan의 `evaluation.operator=le/ge`, `limit`, `unit`으로 Decimal 비교. 어댑터가 반환한 `status`는 수치 판정에 사용하지 않음 |
| `reported` | `code`, `severity`, `status=PASS/FAIL`, `object`, `check_point`, `mode` | report에 명시된 판정과 필수 필드·모드 검증. plan의 수치 evaluation을 이 방식으로 우회할 수 없음 |
| `none` | 확인된 `code`, `severity`, 실행 조건의 `mode`, 나머지 추출 필드 | 필드·모드 정합성만 검사하고 새 PASS/FAIL은 만들지 않음. 수치 evaluation과 조합 금지 |

severity는 기존 `ERROR`/`WARNING`/`INFO`/`FATAL` 중 확인된 대응을 사용하며 severity만으로 PASS/FAIL을 추정하지 않는다. 수치 임계값·단위·같은 문제의 기준을 내부 AI가 새로 정하지 않는다. 기존 설정·스크립트·담당자가 확인한 업무 규칙을 plan으로 전달한다. 복합식이나 새로운 판정 정책이 필요하면 공통 evaluator를 수정하지 않고 문의한다.

내장 `summary_json_v1`도 `reported` 계약을 따른다. 적용 대상 unit의 `evaluation`은 생략하거나 null이어야 하며 `{}`를 포함한 non-null 값은 `PARSER_SCOPE_MISMATCH`로 거부한다. 수치 기준을 가진 item은 `numeric` 추출과 공통 Decimal 평가를 사용한다. JSON의 `status=PASS`로 그 기준을 대체하지 않는다.

정규화 이후의 공개 근거에도 원문 위치 계약을 유지한다. `json_pointer`가 있으면 VWP private evidence에서 OA part·record 조회까지 보존하며 없는 위치를 만들어 넣지 않는다. UTF-8 실패의 `hex:<bytes>`는 VWP 안에서만 보존하고 공개 raw 전체를 `[REDACTED]`로 바꿔 `redacted=true`로 전달한다. UNKNOWN·완료 차단·근거 ID·원문 해시는 유지한다. 세부 예약 표현과 구 packet 호환성은 [PROTOCOL.md의 전송 개정](../PROTOCOL.md#정규화원문-위치전송-개정)을 따른다. 회사 어댑터가 이 마스킹이나 전송 개정을 직접 구현하지 않는다.

**위반 0건:** 실제 report에 완료된 검사 범위와 `0 violations` 같은 정상 근거가 명시되어 있으면 그 문구의 span을 사용하는 결과 기록을 만든다. 수치 형식은 위반 수 `observed="0"`를 승인된 기준과 비교하고, 명시적 정상 판정 형식은 확인된 규칙으로 PASS를 대응한다. 합성 예제의 `CLEAN ...`은 후자의 예다. 빈 파일, header/footer만 있는 파일, 단순히 FAIL 문구가 없는 파일을 PASS로 만들지 않는다. 현재 계약은 결과 기록이 0개면 미완료다.

<a id="collection-only-parser"></a>

### 4.1. 수집 전용 `none` — 2026-10-28 공통 구현

VWP-bridge에서 새 PASS/FAIL을 계산하지 않고 원문·추출 필드·실행 조건을 수집하는 요구를 지원 방향으로 반영한다. 이 범위는 **parser/bridge의 평가 단계**다. OA AI의 초기 분류·그룹 판정 제안, 사람의 확정과 다음 판단의 사례 활용이라는 제품 목표는 유지한다.

**현재 구현:** `ParserRegistry.register`와 `checked_records`가 `none`을 지원한다. `none`의 정상 추출은 공통 구조 검사를 통과한 뒤 기존 `evaluate`를 호출하지 않는다. 기존 `numeric`/`reported`의 평가와 legacy coverage 차단은 유지한다.

| 위치 | 수집 전용의 저장·표시 계약 |
|---|---|
| 분석 결과와 OA report header | `evaluation_mode="none"`, 정수 `not_evaluated_count` |
| 정상 추출 record | `check_status="UNKNOWN"`, `evaluation_status="NOT_PERFORMED"`, `reasons=[]` |
| 실제 필드/파싱 오류 record | 기존 `UNKNOWN`과 오류 `reasons`, 판정 미실행 marker 없음 |
| 기존 집계 | `evaluated_count=0`, `status_counts.UNKNOWN`/`unknown_count`는 전체 UNKNOWN 건수. `not_evaluated_count`는 그중 정상 추출된 미평가 건수이며 별도 합산하지 않음 |
| OA 화면 | 보고서·그룹·로그의 정상 수집 상태는 **판정 미실행**, 실제 오류와 파싱/준비 상태는 별도 표시 |

구조 검사는 `code`와 기존 네 값 중 `severity`를 요구한다. `object`/`check_point`/`mode`는 제공된 경우 문자열이어야 하고, 실행 조건에 mode가 있으면 추출 mode와 일치해야 한다. identity 누락에 임의 값을 넣지 않는다. 수집 전용도 EOF·span·전체 건수·완료/encoding·미해석 검사를 통과해야 파싱 완료다.

선택 기준과 공통 계약은 다음과 같다.

| 대상 | 적용 방향 |
|---|---|
| 승인된 수치 기준을 적용해야 하는 report | 기존 `numeric`과 Decimal 평가 유지 |
| 원문에 명시적 PASS/FAIL이 있는 report | 기존 `reported`로 도구 결과 보존 가능. 원문 판정을 읽는 것과 새로운 판정을 계산하는 것을 구분 |
| 원문에 판정이 없고 로그·사실만 추출하는 report | `none` 대상. 판정을 만들지 않고 추출 완료와 판정 미실행을 각각 기록 |

1. `none`은 명시적으로 등록한 item·tool/tool_version·rule_version에만 적용한다. 미지원 parser, 추출 오류, 수치 기준 불명, 평가 실패를 `none`으로 자동 전환하지 않는다. `unit.evaluation`은 생략 또는 null이어야 하며 `{}`를 포함한 non-null 수치 정책과 함께 사용하지 못하게 한다. plan의 수치 정책을 없애 기존 `numeric` 대상의 평가를 우회하지 않는다.
2. PASS/FAIL용 비교는 실행하지 않더라도 추출 필드·자료형·실행 mode의 정합성, 원문 span·EOF·전체 건수·완료 표시·encoding·미해석 구간·원본 hash 검증은 유지한다. 확인되지 않은 code/severity/status나 임계값을 임의로 채워 성공시키지 않는다. 필드 누락과 identity 불명도 숨기지 않는다.
3. **수집·파싱 완료와 판정 완료를 분리한다.** 정상 추출의 판정 미실행을 파싱 오류로 집계하거나 자동 PASS/NOT_APPLICABLE로 표시하지 않는다. 반대로 잘림·누락·미해석·건수 불일치 등 실제 오류는 기존 차단 사유로 남긴다. 원문에 명시된 결과·측정 사실은 보존하며 AI 제안·사람 판정으로 덮어쓰지 않는다.
4. 명시적 위반 0건도 원문 근거의 span을 가진 사실 기록으로 보존한다. 판정 미실행 상태에서는 이 사실만으로 PASS를 생성하지 않는다. 빈 파일·header/footer만 있는 파일·결과 기록 0개를 완료로 바꾸는 요구는 이번 범위에 포함하지 않는다.
5. 정상 수집된 전체 기록은 OA 분류·사람의 그룹 확정·그룹 판정의 입력으로 전달할 수 있어야 한다. 현재 OWNER의 접수 범위 확정과 기존 SSO/RBAC·전체 입력·근거/기준/사례 검증을 유지한다. 판정 미실행만을 이유로 이 후속 흐름을 영구 차단하지 않되, 수집 완료를 AI 판단·사람 검토 완료로 승계하지 않는다. 기준·사례·현재 근거가 부족하면 기존 계약대로 보류한다.
6. 기존 evaluated 경로와 봉인 이력은 보존한다. 평가 의미 변경은 새 parser/규칙 계약과 분석 개정으로 구분하며 과거 결과를 재해석하거나 덮어쓰지 않는다. 기존 legacy coverage의 UNKNOWN 검사도 일괄 삭제하지 않는다.

**호환·검증 경계:** `check_status` enum과 세 항목의 `status_counts`는 변경하지 않았다. V2 source의 `none` 분석은 producer `postgresql-source-2.2`로 봉인하고, 이를 포함한 collection도 2.2로 식별한다. 기존 `numeric`/`reported`의 source producer/분석 ID는 2.1을 유지한다. parser 계약·producer·순서 있는 part hash가 새 상태와 건수에 결합되며 이전 분석/확정 이력을 덮어쓰지 않는다. wire schema 2와 기존 네 테이블을 사용하고 추가 migration/GRANT는 없다.

OA는 명시적 `none` 등록의 item·tool/version·규칙 범위, 수치 정책 부재, producer, 결과 집계와 실제 part의 marker 건수를 재검증한다. marker를 다른 방식에 붙이거나 정상 marker/오류 사유를 바꿔 수신·OWNER 확정을 우회할 수 없다. 전체 수집/파싱이 정상이고 기존 접수 범위·권한 검사를 통과하면 OA AI와 사람 검토의 입력이 된다. legacy bundle의 미평가/UNKNOWN 검토 완료 차단은 그대로다. 구체 직렬화는 [PROTOCOL.md](../PROTOCOL.md)를 따른다.

**사내 담당자의 현재 범위:** VWP·OA 양쪽에 수정본을 전달받은 뒤 기존 `create_parser_registry`의 명시적 등록에서 `evaluation="none"`을 사용할 수 있다. 원문 판정의 유무, 추출 필드/누락 의미, 완료·건수·0건 계약과 합성 입력/기대값을 확인하고 실제 report는 사내에서 검증한다. 회사 파일/메서드의 허용 범위는 유지한다. 공통 코드를 직접 바꾸거나 오류 후 등록/분석/제출을 반복하지 않고 기존 중단 규칙대로 `WAITING_FOR_CODEX`로 문의한다.

공통 구현의 최소 수용 시나리오:

| 사례 | 확인할 결과 |
|---|---|
| 명시적으로 등록한 `none`, 판정 없는 정상 로그 | 전체 필드·원문 위치·건수 보존, 수집/파싱 완료와 판정 미실행 구분, 가짜 PASS/FAIL 없음 |
| `none`과 non-null `unit.evaluation`의 조합 | scope 불일치로 봉인·전송 차단. 평가 오류의 자동 fallback도 없음 |
| 잘림·빈 파일·종료/건수 불일치·미해석·encoding 오류 | 실제 미완료 사유와 UNKNOWN 근거 보존, 수집 전용을 이유로 성공 전환하지 않음 |
| 원문의 명시적 0건 | 근거 있는 사실 기록 보존, 판정 자동 생성 없음 |
| VWP→OA 전달과 OWNER 접수 확정 후 두 단계 검토 | 전체 입력·권한·개정 검사 유지, 별도 사람 확정 전에는 검토 완료/정답 사례 생성 없음 |
| 기존 `numeric`/`reported`와 과거 봉인 결과 | 기존 판정·건수·차단·불변 이력 계약 유지 |

전용 합성 시험은 `tests/test_collection_only.py`와 `tests/test_submission_tokenless.py`의 수집 전용 시나리오를 사용하며 기본 `quick`에 포함한다. 원문·필드·도구 버전의 사내 정확성, 실제 OA 모델 판단 품질과 운영 배포는 별도 확인 대상이다. 합성 모델 응답의 흐름 검증을 실제 AI 품질 완료로 보고하지 않는다.

## 5. 구현·등록 순서

1. 실제 item 한 개와 지원할 도구/형식 버전을 정한다. 사내에서 필드·섹션·종료 표시·건수·0건 의미·판정 기준·identity 대응을 확인해 비공개 계약서를 만든다. 정보가 없으면 추정해서 구현하지 않는다.
2. 정상·위반·경계값·0건·빈 파일·중간 잘림·누락 필드·새 섹션·잘못된 버전의 현장 기대값을 확정한다. 공통 코드를 재현할 자료는 별도 합성 입력으로 작성한다.
3. `CorporateReportParser.parse`를 구현하거나 `vwp_bridge/integration/company_parsers/`에 `ReportParser` 하위 클래스를 추가한다. [합성 예제](integration/parser_example.py)의 읽기/완료 패턴을 참고하되 형식은 실제 계약에서 구현한다.
4. `create_parser_registry`에서 실제 클래스를 명시적으로 import하고 등록한다. 예시 이름은 사내 사실이 아니므로 확인한 값으로 교체한다.

```python
# vwp_bridge/integration/corporate_parsers.py의 create_parser_registry 본문 예시.
# 아래 클래스/이름은 설명용이며 현재 제공된 구현이 아니다.
from .company_parsers.item_a import ItemAParser

registry = ParserRegistry()
registry.register(
    "company_item_a_text_v1", ItemAParser(),
    implementation_version="1", evaluation="numeric",
    item_ids=("item_a",), tool="confirmed_tool",
    tool_versions=("confirmed_version",), rule_versions=("confirmed_rule",),
)
return registry
```

5. 예상 목록 입력 어댑터에서 해당 item의 `parser`를 등록 이름과 일치시키고 실제 `rule_version`, 조건, evaluation, identity를 설정한다. plan에 Python 모듈 경로를 넣거나 미지원 형식을 기존 CSV/JSON 형식으로 위장하지 않는다.
6. 전용 합성 테스트와 사내 비공개 fixture 검증을 따로 실행한다. 결과를 인계한 뒤 사용자/Codex 검토 절차로 운영 적용한다. 실제 원문은 이 대화에 첨부하지 않는다.

등록은 item ID·tool·tool_version·rule_version의 명시적 허용 범위를 요구한다. wildcard·중복 이름·기존 built-in 교체는 거부한다. 회사 registry의 초기값은 비어 있고, 미구현 메서드는 `PARSER_NOT_CONFIGURED`로 실패한다. 미등록 이름은 기존 `UNSUPPORTED`로 남고 다른 형식으로 우회하지 않는다.

추출 코드·정규화·완료 검사 변경 시 **`implementation_version`을 올린다**. 새로운 원문 형식이나 추출 필드의 의미 변경은 parser ID의 버전도 바꾸고 업무 판정/동일성 기준 변경은 plan의 rule_version을 바꾼다. 분류 규칙 재사용 범위는 기존 parser ID·rule_version을 사용하므로 의미가 달라진 어댑터를 implementation_version만 올려 같은 형식으로 등록하지 않는다. 사용된 등록의 버전·범위는 VWP 불변 RUN 계약과 번들 `producer.parsers`에 들어가므로 기존 봉인 결과를 덮어쓰지 않고 새 분석/번들을 생성한다.

## 6. 실행과 검증

공개 문서에서 확인한 GCA·VC LP·SpyGlass의 초기 추출 참고 구현은
[public_parsers.py](integration/public_parsers.py)와 [형식·샘플 인계](integration/PUBLIC_REPORT_FORMATS.md)에 있습니다.
이는 sample profile에서만 완성되는 고정 예제이며 실제 회사 registry에 자동 등록되지 않습니다.
회사 구현은 확인한 native 버전/종료 계약과 전용 parser ID로 허용 지점에 작성합니다.

공통 합성 테스트는 DB/실제 report/AI 접속 없이 실행한다.

```bash
.venv/bin/python -m unittest discover -s tests -p test_parsers.py -v
```

사내 어댑터 전용 테스트 디렉터리를 작성한 뒤 별도로 수집한다. 기존 runner는 회사 디렉터리를 자동 수집하지 않는다.

```bash
.venv/bin/python -m unittest discover -s vwp_bridge/integration/company_parsers -p 'test_*.py' -v
```

기존 지원 형식은 종전 `vwp_bridge/main.py analyze`를 사용한다. 회사 파서는 같은 명령에 아래 진입점을 사용한다. 먼저 `vwp/main.py collect`로 완료된 실행의 원문 snapshot을 만든다. 운영 DB 변경/분석 실행은 기존 사용자 검토·적용 절차를 따른다.

```bash
.venv/bin/python vwp_bridge/corporate_analyze.py analyze --input /path/to/private/run.json --out /path/to/private/receipt
```

일반 `main.py`는 회사 코드를 자동 import하지 않는다. 기존 내장 형식은 회사 진입점에서도 종전대로 동작한다. 의존성·새 env 키·새 실행 방식이 필요하면 Codex에게 문의한다.

검증 표에는 최소 다음을 기록한다.

| 사례 | 확인할 결과 |
|---|---|
| 정상/위반/경계값 | 기대 필드·원문 위치·기존 방식의 PASS/FAIL 또는 `none`의 판정 미실행·전체 건수 |
| 명시적 위반 0건 | 정상 근거를 가진 결과 기록과 COMPLETE, 사람 검토는 별도 |
| 빈 파일/종료 누락/잘림/건수 불일치 | PARTIAL/UNKNOWN·blocker, 검토 완료 차단 |
| 미해석 섹션/encoding 실패/후속 내용 | 모든 해당 원문 UNKNOWN 보존 |
| 다른 item·도구/규칙 버전 | `PARSER_SCOPE_MISMATCH`, DB 쓰기 전 중단 |
| 수치 evaluation을 가진 `summary_json_v1` | `PARSER_SCOPE_MISMATCH`, 봉인·전송 없음 |
| 한 물리 줄의 여러 JSON 결과 | VWP/OA의 정확한 Pointer·원문 해시·전체 건수 보존 |
| UTF-8 실패의 공개 근거 | private hex/실제 bytes 보존, 공개 raw 마스킹·UNKNOWN·완료 차단 |
| V2 정규화 개정/중단 복구 | 기존 봉인·사람 확정 보존, 다른 개정 part 혼합 차단·같은 개정 재개 |
| 같은 입력·구현 버전 재실행 | 동일 분석 ID/번들, 원문·봉인 결과 불변 |
| 구현 버전 변경 | 새 분석 ID/번들, 이전 결과 보존 |
| 예상 밖 어댑터 예외/잘못된 반환 | 고정 오류 코드, 재시도/봉인/전송 성공 없음 |

공통 테스트는 회사 stub의 미구현 상태나 실제 등록 목록에 의존하지 않으며 합성 어댑터로 고정 계약을 검증한다. 공통 DB 검증은 개발 측의 독립 mock PostgreSQL과 합성 report로 수행한다. mock runner는 공개 package/예제만 복사하고 회사 구현/등록 파일 `corporate_parsers.py`, `company_parsers/`, 비공개 fixture, 실제 report, `.env`를 수집하지 않는다. 사내 어댑터의 의미적 정확성과 회사 코드까지 검증한 것으로 해석하지 않는다.

## 7. 오류와 Codex 문의

확인된 입력 형식 오류는 `UnparsedRegion`과 `ParseFinished(complete=False, blockers=...)`로 표현해 불완전 분석 근거를 보존한다. 예상 밖 코드 예외·잘못된 반환 계약은 다음 고정 코드로 중단하며 원문 예외/traceback을 CLI에 출력하지 않는다.

- `PARSER_NOT_CONFIGURED`: 회사 stub 미구현.
- `PARSER_REGISTRATION_ERROR`: 등록 이름·범위·타입 오류, 중복/내장 파서 교체.
- `PARSER_SCOPE_MISMATCH`: 승인된 item·tool·tool/rule version 또는 판정 방식 불일치.
- `PARSER_INVALID_RESULT`: 잘못된 이벤트·span·Pointer·완료 모델·reason 코드.
- `PARSER_SOURCE_LIMIT`: 현재 입력/원문 크기 한도 초과.
- `PARSER_FAILED`: 예상 밖 import/어댑터 예외. 상세 원문은 외부에 보내지 않음.

`INTERNAL_AI_GUIDE.md` 6절의 문의 양식에 parser ID·implementation_version·판정 방식·입력 크기/줄 수·기대/실제 건수·합성 위치/필드 예를 추가한다. 원문·내부 객체명·내부 주소·개인정보·자격증명은 제외한다. 합성 입력으로 재현되지 않으면 재현 불가라고 기록하고 사내 담당자가 원문 검증을 맡는다. Codex가 실제 report를 열람한 것처럼 인계하지 않는다.

내부 AI에게 전달할 지시문:

> 먼저 AGENTS.md, INTERNAL_AI_GUIDE.md, vwp_bridge/PARSER_INTEGRATION.md를 읽어라. 실제 report는 사내에서만 확인한다. vwp_bridge/integration/corporate_parsers.py의 parse/등록과 company_parsers/의 전용 구현·테스트만 작성하라. 공통 인터페이스·판정·DB·coverage·기존 테스트는 수정하지 말라. item 한 개의 확인된 형식부터 구현하고 원문 span·EOF·종료/건수·정상 0건·잘림 사례를 검증하라. 공통 코드 문의에는 합성 재현 입력만 사용하라. 예상 밖 오류는 수정/재시도를 멈추고 비식별 Codex 문의를 작성해 WAITING_FOR_CODEX로 대기하라.
