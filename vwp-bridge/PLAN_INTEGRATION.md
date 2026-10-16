# 사내 AI 인수인계 — 예상 sanity 목록과 plan 입력

먼저 [AGENTS.md](../AGENTS.md), [INTERNAL_AI_GUIDE.md](../INTERNAL_AI_GUIDE.md), [통합 작업 지도](../INTERNAL_AI_HANDOFF.md)를 읽는다. 이 계약은 **이미 실행된 sanity의 원천 메타데이터를 기존 수집 입력으로 대응**하는 작업이다. 새로운 sanity 실행기·파일 변환 전송·DB import API를 만드는 계약이 아니다.

## 1. 제공한 경계

| 위치 | 내용 | 내부 AI 변경 |
|---|---|---|
| [sanity_integration_contracts.py](../sanity_integration_contracts.py) | `SanityPlanProvider`, `CollectionPlan`, `RunDescription`, `ExpectedInventory`, `PlannedUnit` | 읽기 전용 |
| [corporate_plan.py](../site_integration/corporate_plan.py) | `CorporatePlanProvider.get_plan` 기본 미구현 | 메서드 본문·필요 import·승인된 원천 조회 client 주입용 생성자 |
| `site_integration/company_plan/` | 회사 실행/기준 목록 SDK wrapper와 전용 모의 테스트를 추가할 위치 | 새 회사 구현/테스트만 허용 |
| [vwp-dev/main.py](../vwp-dev/main.py) | 기존 `validate_plan`, snapshot 수집 CLI | 읽기 전용 |
| [sanity_integration_examples.py](../sanity_integration_examples.py) | 작은 미승인 합성 plan과 AI 입력/출력 예제 | 읽기 전용. 회사 registry/운영 seed가 아님 |

기본 stub은 `ReviewError(code="PLAN_NOT_CONFIGURED")`로 실패한다. 회사 모듈 자동 import/검색·collector 주입·실행 CLI를 추가하지 않았다. 기존 CLI의 `collect --plan` 계약을 유지한다. 사내 launcher의 명시적인 provider 호출·반환 파일 작성은 허용된 회사 입력 wrapper에만 구현한다.

## 2. 추상 인터페이스

```python
class SanityPlanProvider(ABC):
    @abstractmethod
    def get_plan(self, run_reference: str) -> CollectionPlan:
        raise ReviewError("PLAN_NOT_CONFIGURED", "Company expected-inventory provider is not connected")
```

- `run_reference`: 회사에서 승인된 실행 정보의 조회 키다. 회사별 형식은 실제 계약으로 확인한다. 명령어·SQL·자유 경로를 실행하는 입력으로 취급하지 않는다.
- 반환: 기존 schema 1의 `{schema_version, run, inventory}` dict. `get_plan`은 메모리의 plan을 반환한다. report를 읽어 예상 목록을 역추정하거나 파일·DB를 쓰지 않는다.
- 동기 호출이며 객체를 재사용할 수 있다. 실행별 상태는 지역 변수에 두고 승인된 원천 client의 timeout·동시성 제약을 따른다. SDK를 넣기 위해 공통 설정 키/의존성/메서드 서명을 변경하지 않는다.
- 원천 조회 장애·확인 불가를 빈 목록/`COMPLETED`/승인된 목록으로 대체하지 않는다. 연결 미구현은 위 고정 코드, 확인된 원천 장애는 비밀이 없는 고정 `PLAN_SOURCE_UNAVAILABLE` 코드로 보고한다. 예상하지 못한 오류는 가이드에 따라 문의한다.

호출한 회사 wrapper는 반환 직후 기존 `vwp-dev/main.py:validate_plan(plan)`을 명시적으로 사용한다. 승인된 현장 방식으로 검증된 JSON을 작성한 뒤 기존 collector를 실행한다. 새 공통 수집 명령/동적 import를 만들 필요는 없다.

```bash
.venv/bin/python vwp-dev/main.py collect --plan /path/to/private/plan.json --reports /path/to/private/reports --out /path/to/private/new-snapshot
```

실제 수집/분석의 운영 실행 권한은 사용자 관리 절차를 따른다. 이 명령은 설명용 기존 진입점이며 사내 연결을 자동 시도하지 않는다.

## 3. 정확한 기존 plan 필드

| 필드 | 반환 형식/근거 | 누락·실패 처리 |
|---|---|---|
| `schema_version` | 정수 `1` | 새 workspace 필드 때문에 임의 증분하지 않음 |
| `run.run_id` | 안정 실행 ID, 기존 ID 문법 충족 | 파일명·현재 시각만으로 실제 실행을 재식별하지 않음 |
| `run.project`, `design`, `stage`, `tool`, `tool_version` | 각 비어 있지 않은 문자열, 확인된 원천 대응 | 표시 이름/임의 버전 기본값을 실제 식별 계약으로 대체하지 않음 |
| `inventory.confirmed` | 권위 있는 예상 목록의 확인/승인 여부 `bool` | 확인되지 않았으면 false. 조회/수집 성공을 승인으로 바꾸지 않음 |
| `inventory.source`, `version` | 예상 목록 원천과 개정의 확인 가능한 문자열 | 생성된 파일 목록을 원천으로 쓰지 않음 |
| `inventory.units` | 예상 전체 단위 배열 | report 누락/도구 실패/미실행도 삭제하지 않음 |
| `unit.review_unit_id` | 한 실행 내 유일한 검토 단위 ID | 실제 item+실행 조건 등 확인된 대응 사용 |
| `unit.item_id` | 확인된 sanity item ID | GCA/VC LP/SpyGlass 도구 이름만으로 여러 item을 합치지 않음 |
| `unit.report` | `reports/`로 시작하는 정규 상대 경로. 단위 간 중복 금지 | 없을 수 있는 **예상** 경로도 유지. 절대 경로·`..`·역슬래시 금지 |
| `unit.parser`, `rule_version` | 명시적 registry 이름과 확인된 업무 규칙 버전 | 미지원 형식을 내장 형식으로 위장하지 않음 |
| `unit.condition`, `identity_fields` | dict 및 중복 없는 필드 문자열 배열 | 승인된 실행 조건/동일성 기준만 사용 |
| `unit.execution_status` | `COMPLETED`, `FAILED`, `UNKNOWN`, `NOT_RUN` | 파일 존재만으로 성공 추정 금지 |
| `unit.applicable` | 적용 여부 `bool` | 실행 실패를 적용 제외로 바꾸지 않음 |
| `unit.exclusion` | 적용 제외 사유·승인자·참조 또는 null | 승인 근거 누락은 보완 대상이며 가짜 승인 생성 금지 |
| `unit.evaluation` | 수치 parser일 때 승인된 `operator=le/ge`, `limit`, `unit` | 정확한 십진 문자열/정수 기준. 분수 float·단위 추정 금지 |

세부 구조는 [PROTOCOL의 입력/출력](../PROTOCOL.md#vwp-dev-입력과-출력), parser 의미는 [PARSER_INTEGRATION](PARSER_INTEGRATION.md)을 따른다. 한 plan의 `run.tool/tool_version`는 한 쌍이다. 여러 도구 실행을 한 plan에 섞어 다른 버전을 숨기지 않는다. 현장 실행 묶음과 일치하지 않으면 합성 구조로 공통 확장을 문의한다.

`files`, `inventory.sha256`, `ready`, `unexpected_files`는 snapshot collector의 산출물이다. provider가 원문 검증을 수행한 것처럼 미리 채우지 않는다. `validate_plan`은 구조 검사이며 실제 예상 목록의 완전성·원천 진실성·업무 승인까지 증명하지 않는다. 확인되지 않은 목록도 구조 검사/수집은 가능하지만 이후 분석/검토 완료 조건을 우회할 수 없다.

## 4. workspace 원천 대응은 별도 자료

기존 run/bundle에는 workspace의 `project_id`, `block_id`, `version_id`, `NET_VER`, 전체 `REVISION`, `PRE/POST`, ECO, 사용자/담당 배정의 운영 수신 계약이 아직 없다. `run.project/design`을 곧바로 workspace ID로 재해석하거나 plan에 이 필드를 붙이지 않는다.

사내에서 다음 형태의 대응표를 작성한다. 외부 제출본에는 실제 값 대신 비식별 필드명·타입과 합성 예제를 사용한다.

| 대상 | 확인할 원천 계약 | 실패/변경 시 필요한 설명 |
|---|---|---|
| 사용자/담당 | 불변 계정 키, 내부 user ID, TOP/블록 담당의 권위 있는 목록 | 미연결/비활성/담당 해제/역할 중복 |
| project/block | 안정 ID·표시 이름·소속 관계, 다른 프로젝트/블록의 동명 처리 | ID 충돌/이름 변경/삭제·미수신 |
| 블록 version | 블록별 NET_VER·전체 REVISION·PRE/POST | 다른 블록의 같은 문자열, 새 개정/재수신 |
| ECO | 원천 값·없음의 표현·POST와의 관계 | chip_finish 등 이름을 실제 정책으로 추정하지 않음 |
| 실행/report/item | 실행 ID·결과 단위·파일·item·tool/parser/rule version 관계 | 미수신·여러 보고서·다른 버전·중복 import |
| PoR | 실제 기준 버전 지정 절차·권한과 PRE/POST 정책 | 외부 실행 성공을 PoR 자동 변경으로 취급하지 않음 |

Codex가 이 표로 공통 import·scope·감사 연결을 검토한다. 사내 AI는 DB SQL·workspace store·전송 payload/worker·UI를 수정하지 않는다.

## 5. 검증과 인계

사내 합성/모의 원천 테스트에는 다음을 포함한다.

- 확인된 목록, 미확인 목록, 정상/실패/미실행, report 누락과 예상 밖 파일.
- 다른 도구/실행 버전, 중복 unit/경로, 위험한 경로, 필수 원천 필드 누락, 원천 장애.
- 다른 실행을 연속/동시에 조회해도 상태가 섞이지 않음. plan 반환만으로 원천/report·DB·PoR을 변경하지 않음.
- 같은 원천 실행/개정의 동일한 필드 대응, 실제 실행 또는 기준 목록 개정 시 그 변경을 정확히 보존.
- 수치 기준·0건/완료·적용 제외의 의미는 parser/업무 담당자의 실제 확인 결과와 비교.

공통 합성 예제 검사:

```bash
.venv/bin/python -m unittest discover -s tests -p test_integration_contracts.py -v
```

회사 전용 테스트를 작성한 뒤 별도로 실행한다.

```bash
.venv/bin/python -m unittest discover -s site_integration/company_plan -p 'test_*.py' -v
```

기존 runner는 회사 provider나 비공개 테스트/fixture를 자동 수집하지 않는다. 실환경 검증·운영 적용과 모의 테스트를 나눠 기록한다. 예상 밖 실패 시 수정/재시도를 멈추고 가이드의 비식별 문의를 남긴다. 실제 실행 메타데이터·report·SDK/접속 정보는 공개 저장소와 외부 Codex 대화에 넣지 않는다.
