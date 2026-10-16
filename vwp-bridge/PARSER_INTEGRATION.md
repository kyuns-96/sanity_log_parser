# 사내 AI 인수인계 — item별 report parser

실제 report는 보안상 **사내 AI와 현장 담당자가 사내에서만 열람·검증**한다. 현재 대화의 Codex는 원문을 받지 않고 공통 인터페이스·검증 엔진·합성 회귀 사례를 관리한다. 이 개발 경계는 기존 사내 OA report 조회 계약과 별개다.

먼저 [AGENTS.md](../AGENTS.md)와 [INTERNAL_AI_GUIDE.md](../INTERNAL_AI_GUIDE.md)를 읽는다. 이 문서는 사용자가 요청한 파서 개발 범위를 구체화한다. 내부 AI는 아래 회사 구현 지점만 수정하고 공통 코드·DB·판정·coverage·기존 테스트는 수정하지 않는다. 예상 밖 오류는 자기 어댑터에서도 수정/재시도를 멈추고 `WAITING_FOR_CODEX`로 문의한다.

작업별 선행 정보/공통 연결 의존성은 [INTERNAL_AI_HANDOFF.md](../INTERNAL_AI_HANDOFF.md)의 INT-02/03을 따른다. 파서에 전달할 예상 목록은 [PLAN_INTEGRATION.md](PLAN_INTEGRATION.md)의 `SanityPlanProvider`로 기존 plan 계약에 대응한다. 이 provider와 파서가 workspace 버전 수신이나 회사 AI worker를 자동 연결하는 것은 아니다.

## 1. 제공 파일과 수정 책임

| 파일/위치 | 제공 내용 | 사내 AI의 작업 |
|---|---|---|
| [sanity_parsers.py](../sanity_parsers.py) | 추상 클래스·반환 모델·원문 reader·registry·공통 결과 검사 | 읽기 전용 |
| [corporate_parsers.py](../site_integration/corporate_parsers.py) | `CorporateReportParser.parse` 미구현 메서드, 빈 `create_parser_registry` | 메서드 본문과 필요한 import, 명시적 파서 등록 |
| `site_integration/company_parsers/` | 사내에서 추가할 위치. 자동 발견/로드하지 않음 | `ReportParser` 구현 모듈과 해당 어댑터 전용 테스트 추가 |
| [corporate_analyze.py](corporate_analyze.py) | 회사 registry를 기존 VWP 분석 CLI에 주입 | 그대로 실행. 파일은 읽기 전용 |
| [parser_example.py](../site_integration/parser_example.py) | 합성 텍스트 형식의 학습용 구현 | 읽기 전용. 실제 item에 등록하지 않음 |
| [test_parsers.py](../tests/test_parsers.py), [test_corporate_parsers.py](test_corporate_parsers.py) | 순수 계약 테스트와 PostgreSQL 합성 통합 테스트 | 읽기 전용 |

실제 report·fixtures·현장 계약서는 승인된 비공개 작업 폴더 또는 Git 제외 `artifacts/`에 둔다. 회사 어댑터 코드에 원문·객체명·접속값을 하드코딩하거나 실제 자료를 공개 저장소에 commit하지 않는다. 회사 형식 코드 자체의 공개 가능 여부도 현장에서 확인한다. Codex 디버깅에는 형식을 보존한 **합성 재현 입력과 기대값**만 전달한다.

## 2. 고정 추상 인터페이스

```python
class ReportParser(ABC):
    def parse(self, source: ReportSource, context: ParseContext) -> Iterable[ParseEvent]: ...
```

`parse`는 동기 함수이며 이벤트를 순서대로 `yield`하는 generator로 작성한다. `context.run`과 `context.unit`은 중첩 구조까지 읽기 전용이다. 실행 정보·조건·판정 설정·동일성 규칙을 읽되 변경하지 않는다. 어댑터 인스턴스가 여러 unit에 재사용되므로 입력별 buffer·count·상태는 메서드의 지역 변수로 둔다.

공통 경로는 다음과 같다.

```text
확정된 plan → 원본 snapshot/해시 검사 → 등록 범위 대조
 → ReportParser.parse(ReportSource, ParseContext)
 → 원문 위치·전체 읽기·미해석 구간·완료 표시·건수 검사
 → 기존 evaluate(Decimal 또는 report의 명시적 PASS/FAIL)
 → PASS 포함 전체 evidence 저장 → 집계·봉인 → 기존 DB 전송
```

파서는 DB·네트워크·운영 AI·shell·다른 report를 호출하지 않는다. 원문 변환 파일을 만들거나 DB에 직접 쓰지 않는다. 이 인터페이스는 Python sandbox가 아니며 수정 경계는 지침·검토로 관리한다.

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

registry의 `evaluation`은 파서별로 고정한다. 개별 이벤트가 판정 방식을 바꿀 수 없다.

| 등록 방식 | 추출 필드 | 공통 판정 |
|---|---|---|
| `numeric` | `code`, `severity`, `observed`(정확한 십진 문자열), `unit`, 객체/검사 지점/모드 | 기존 plan의 `evaluation.operator=le/ge`, `limit`, `unit`으로 Decimal 비교. 어댑터가 반환한 `status`는 수치 판정에 사용하지 않음 |
| `reported` | `code`, `severity`, `status=PASS/FAIL`, `object`, `check_point`, `mode` | report에 명시된 판정과 필수 필드·모드 검증. plan의 수치 evaluation을 이 방식으로 우회할 수 없음 |

severity는 기존 `ERROR`/`WARNING`/`INFO`/`FATAL` 중 확인된 대응을 사용하며 severity만으로 PASS/FAIL을 추정하지 않는다. 수치 임계값·단위·같은 문제의 기준을 내부 AI가 새로 정하지 않는다. 기존 설정·스크립트·담당자가 확인한 업무 규칙을 plan으로 전달한다. 복합식이나 새로운 판정 정책이 필요하면 공통 evaluator를 수정하지 않고 문의한다.

**위반 0건:** 실제 report에 완료된 검사 범위와 `0 violations` 같은 정상 근거가 명시되어 있으면 그 문구의 span을 사용하는 결과 기록을 만든다. 수치 형식은 위반 수 `observed="0"`를 승인된 기준과 비교하고, 명시적 정상 판정 형식은 확인된 규칙으로 PASS를 대응한다. 합성 예제의 `CLEAN ...`은 후자의 예다. 빈 파일, header/footer만 있는 파일, 단순히 FAIL 문구가 없는 파일을 PASS로 만들지 않는다. 현재 계약은 결과 기록이 0개면 미완료다.

## 5. 구현·등록 순서

1. 실제 item 한 개와 지원할 도구/형식 버전을 정한다. 사내에서 필드·섹션·종료 표시·건수·0건 의미·판정 기준·identity 대응을 확인해 비공개 계약서를 만든다. 정보가 없으면 추정해서 구현하지 않는다.
2. 정상·위반·경계값·0건·빈 파일·중간 잘림·누락 필드·새 섹션·잘못된 버전의 현장 기대값을 확정한다. 공통 코드를 재현할 자료는 별도 합성 입력으로 작성한다.
3. `CorporateReportParser.parse`를 구현하거나 `site_integration/company_parsers/`에 `ReportParser` 하위 클래스를 추가한다. [합성 예제](../site_integration/parser_example.py)의 읽기/완료 패턴을 참고하되 형식은 실제 계약에서 구현한다.
4. `create_parser_registry`에서 실제 클래스를 명시적으로 import하고 등록한다. 예시 이름은 사내 사실이 아니므로 확인한 값으로 교체한다.

```python
# site_integration/corporate_parsers.py의 create_parser_registry 본문 예시.
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
[public_parsers.py](../site_integration/public_parsers.py)와 [형식·샘플 인계](../site_integration/PUBLIC_REPORT_FORMATS.md)에 있습니다.
이는 sample profile에서만 완성되는 고정 예제이며 실제 회사 registry에 자동 등록되지 않습니다.
회사 구현은 확인한 native 버전/종료 계약과 전용 parser ID로 허용 지점에 작성합니다.

공통 합성 테스트는 DB/실제 report/AI 접속 없이 실행한다.

```bash
.venv/bin/python -m unittest discover -s tests -p test_parsers.py -v
```

사내 어댑터 전용 테스트 디렉터리를 작성한 뒤 별도로 수집한다. 기존 runner는 회사 디렉터리를 자동 수집하지 않는다.

```bash
.venv/bin/python -m unittest discover -s site_integration/company_parsers -p 'test_*.py' -v
```

기존 지원 형식은 종전 `vwp-bridge/main.py analyze`를 사용한다. 회사 파서는 같은 명령에 아래 진입점을 사용한다. 먼저 `vwp-dev/main.py collect`로 완료된 실행의 원문 snapshot을 만든다. 운영 DB 변경/분석 실행은 기존 사용자 검토·적용 절차를 따른다.

```bash
.venv/bin/python vwp-bridge/corporate_analyze.py analyze --input /path/to/private/run.json --out /path/to/private/receipt
```

일반 `main.py`는 회사 코드를 자동 import하지 않는다. 기존 내장 형식은 회사 진입점에서도 종전대로 동작한다. 의존성·새 env 키·새 실행 방식이 필요하면 Codex에게 문의한다.

검증 표에는 최소 다음을 기록한다.

| 사례 | 확인할 결과 |
|---|---|
| 정상/위반/경계값 | 기대 필드·원문 위치·개별 PASS/FAIL·전체 건수 |
| 명시적 위반 0건 | 정상 근거를 가진 결과 기록과 COMPLETE, 사람 검토는 별도 |
| 빈 파일/종료 누락/잘림/건수 불일치 | PARTIAL/UNKNOWN·blocker, 검토 완료 차단 |
| 미해석 섹션/encoding 실패/후속 내용 | 모든 해당 원문 UNKNOWN 보존 |
| 다른 item·도구/규칙 버전 | `PARSER_SCOPE_MISMATCH`, DB 쓰기 전 중단 |
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

> 먼저 AGENTS.md, INTERNAL_AI_GUIDE.md, vwp-bridge/PARSER_INTEGRATION.md를 읽어라. 실제 report는 사내에서만 확인한다. site_integration/corporate_parsers.py의 parse/등록과 company_parsers/의 전용 구현·테스트만 작성하라. 공통 인터페이스·판정·DB·coverage·기존 테스트는 수정하지 말라. item 한 개의 확인된 형식부터 구현하고 원문 span·EOF·종료/건수·정상 0건·잘림 사례를 검증하라. 공통 코드 문의에는 합성 재현 입력만 사용하라. 예상 밖 오류는 수정/재시도를 멈추고 비식별 Codex 문의를 작성해 WAITING_FOR_CODEX로 대기하라.
