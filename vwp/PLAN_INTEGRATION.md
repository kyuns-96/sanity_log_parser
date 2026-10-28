# 사내 AI 인수인계 — 예상 sanity 목록과 plan 입력

먼저 [AGENTS.md](../AGENTS.md), [INTERNAL_AI_GUIDE.md](../INTERNAL_AI_GUIDE.md), [통합 작업 지도](../INTERNAL_AI_HANDOFF.md)를 읽는다. 이 계약은 **이미 실행된 sanity의 원천 메타데이터를 기존 수집 입력으로 대응**하는 작업이다. 새로운 sanity 실행기·파일 변환 전송·DB import API를 만드는 계약이 아니다.

개발 소유자는 [VWP 원천 담당 AI](INTERNAL_AI_TASK.md)다. [서버별 소스 구조](../DEPLOYMENT.md)에 plan provider/collector를 배치하며 이 단계에는 DB 계정이 없다. parser 코드는 별도 VWP-bridge 담당자가 작성한다.

2026-10-16 Q1 검토는 [분산 수집·PRE/POST 인계](SOURCE_COLLECTION_DESIGN.md)를 따른다. **아래 `get_plan()`은 기존 schema 1 계약이다.** 별도 V2의 `prepare()`·원천/신원/승인 SDK와 공통 제출 CLI/source 작업자는 [SUBMISSION.md](SUBMISSION.md)에 구현·정의했다. 두 계약을 혼합하지 않는다.

별도 `prepare()` 제출의 비-unit full.rpt/SDC 보존은 [plan 3 계약](PRESERVED_SOURCES.md)을 따른다. 이 확장은 아래 schema 1 `get_plan()`에 필드를 추가하는 지시가 아니다. 호환 코드 배포 후 허용된 회사 입력 대응과 전용 테스트만 작성한다.

## 1. 제공한 경계

| 위치 | 내용 | 내부 AI 변경 |
|---|---|---|
| [sanity_integration_contracts.py](../common/sanity_integration_contracts.py) | `SanityPlanProvider`, `CollectionPlan`, `RunDescription`, `ExpectedInventory`, `PlannedUnit` | 읽기 전용 |
| [corporate_plan.py](integration/corporate_plan.py) | `CorporatePlanProvider.get_plan` 기본 미구현 | 메서드 본문·필요 import·승인된 원천 조회 client 주입용 생성자 |
| `vwp/integration/company_plan/` | 회사 실행/기준 목록 SDK wrapper와 전용 모의 테스트를 추가할 위치 | 새 회사 구현/테스트만 허용 |
| [vwp/main.py](main.py) | 기존 `validate_plan`, snapshot 수집 CLI | 읽기 전용 |
| [sanity_integration_examples.py](../common/sanity_integration_examples.py) | 작은 미승인 합성 plan과 AI 입력/출력 예제 | 읽기 전용. 회사 registry/운영 seed가 아님 |

기본 stub은 `ReviewError(code="PLAN_NOT_CONFIGURED")`로 실패한다. 회사 모듈 자동 import/검색·collector 주입·실행 CLI를 추가하지 않았다. 기존 CLI의 `collect --plan` 계약을 유지한다. 사내 launcher의 명시적인 provider 호출·반환 파일 작성은 허용된 회사 입력 wrapper에만 구현한다.

사용자용 공용 진입점은 제공한 `bin/sanity`의 `submit/status/retry`다. 실제 회사 SDK/인프라 준비와 설치/실행은 [SUBMISSION.md](SUBMISSION.md)를 따른다. CLI·VWP 제출 폴더·영속 원본/DB·OA 반영/receipt는 공통 구현이며 회사 입력 어댑터에서 재구현하지 않는다.

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

호출한 회사 wrapper는 schema 1 반환 직후 기존 `vwp/main.py:validate_plan(plan)`을 사용한다. 승인된 현장 방식으로 JSON을 작성한 뒤 기존 collector를 실행한다. 어댑터 작업에서 공통 수집/제출 명령이나 동적 import를 만들지 않는다. 새 V2 CLI는 별도 회사 `corporate_submission.py` factory를 명시적으로 사용한다.

```bash
.venv/bin/python vwp/main.py collect --plan /path/to/private/plan.json --reports /path/to/private/reports --out /path/to/private/new-snapshot
```

실제 수집/분석의 운영 실행 권한은 사용자 관리 절차를 따른다. 이 명령은 설명용 기존 진입점이며 사내 연결을 자동 시도하지 않는다.

## 3. 정확한 기존 plan 필드

| 필드 | 반환 형식/근거 | 누락·실패 처리 |
|---|---|---|
| `schema_version` | 정수 `1` | 새 workspace 필드 때문에 임의 증분하지 않음 |
| `run.run_id` | 안정 실행 ID, 기존 ID 문법 충족 | 파일명·현재 시각만으로 실제 실행을 재식별하지 않음 |
| `run.project`, `design`, `stage`, `tool`, `tool_version` | 각 비어 있지 않은 문자열, 확인된 원천 대응 | 자유 project/design 문자열을 등록 프로젝트·블록 ID로 재해석하지 않음 |
| `run.scope` | 선택형 `{schema_version:1,project_id,block_id}`, 등록 고정 ID·실제 부모 관계 | 운영 SSO에 공개할 legacy 결과에 수집 전 제공. null/추가 키·이름 기반 추정·봉인 후 추가 금지 |
| `inventory.confirmed` | 확인된 원천 목록 또는 승인된 예상 범위 선언서의 승인 사실을 대응한 `bool` | 스캔/조회/수집 성공으로 승인하지 않음. V2는 별도 digest/참조·회사 authority 검증 필요 |
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

세부 구조는 [PROTOCOL의 입력/출력](../PROTOCOL.md#vwp-수집-입력과-출력), parser 의미는 [PARSER_INTEGRATION](../vwp_bridge/PARSER_INTEGRATION.md)을 따른다. 한 plan의 `run.tool/tool_version`는 한 쌍이다. 여러 도구 실행을 한 plan에 섞어 다른 버전을 숨기지 않는다. 현장 실행 묶음과 일치하지 않으면 합성 구조로 공통 확장을 문의한다.

**2026-10-28 수집 전용 입력:** registry의 `evaluation="none"`은 [parser 4.1절](../vwp_bridge/PARSER_INTEGRATION.md#collection-only-parser)의 공통 구현을 사용한다. registry의 방식 문자열과 plan의 `unit.evaluation` 수치 정책 객체는 서로 다른 필드다. `none` 대상의 수치 정책은 생략/null이며 `{}` 등 non-null 정책과의 조합은 거부한다. plan에 `unit.evaluation="none"`을 넣거나 schema를 임의 증분하지 않는다. 기존 승인된 수치 정책을 삭제해 `numeric` 평가를 회피하지 않는다.

VWP 담당자는 원문 PASS/FAIL 유무, 수치 평가 대상 여부, 판정 없는 로그의 필드·완료/건수·0건 의미를 bridge에 인계한다. 원문 판정은 `reported`로 보존할 수 있고 심각도·파일 존재·위반 문구 부재만으로 판정을 만들지 않는다. 수집 전용은 양쪽 공통 수정본을 반영한 뒤 기존 회사 parser 등록과 제출 경로를 사용한다. 예상 범위·실행 상태·접수 확정의 기존 검사는 유지한다.

`files`, `inventory.sha256`, `ready`, `unexpected_files`는 snapshot collector의 산출물이다. provider가 원문 검증을 수행한 것처럼 미리 채우지 않는다. `validate_plan`은 구조 검사이며 실제 예상 목록의 완전성·원천 진실성·업무 승인까지 증명하지 않는다. 확인되지 않은 목록도 구조 검사/수집은 가능하지만 이후 분석/검토 완료 조건을 우회할 수 없다.

## 4. workspace 원천 대응은 별도 자료

2026-10-22 legacy run/bundle의 고정 프로젝트·블록 소속에 선택형 `run.scope` 계약을 제공했다. 회사 `get_plan()` 본문/허용 wrapper에서 등록된 실제 소속을 확인해 기존 plan의 run 안에 반환한다. schema_version 1과 기존 메서드 서명을 유지하며 공통 collector·DB·권한 엔진을 수정하지 않는다. scope는 분석/bundle 해시에 봉인되고 OA는 등록 부모 관계를 검사한다. 자유 `run.project/design`과 ID의 이름 매핑은 하지 않는다. scope 없는 기존 결과는 prototype에서 유지하지만 SSO에서 숨긴다. 자세한 형식·신뢰된 수신 경계는 [ACCESS_MANAGEMENT.md](../oa/ACCESS_MANAGEMENT.md#3-bundle의-고정-프로젝트블록-연결)를 따른다.

`version_id`, `NET_VER`, 전체 `REVISION`, `PRE/POST`, ECO, 사용자/담당 배정은 이 선택형 scope에 넣지 않는다. 별도 V2 source 계약과 운영 SSO 연결은 여전히 구분한다. V2 SourceReceiver에 새 AccessStore를 연결한 것으로 표시하지 않는다.

별도 V2는 `collection`과 `inventory.units[].source`로 제출 대상과 각 report의 원천 버전/실행을 구분한다. 실제 대응표를 확인해 `CorporateSubmissionInput.prepare`에서 반환하며 `condition`으로 대신 운반하지 않는다. GCA 공통 stage는 `sanity`, design은 확인된 블록 식별자다. 단일 schema 1의 tool/version으로 표현할 수 없는 PRE/POST를 한 값으로 숨기지 않는다. 상세 경계는 [V2 필드 배치](SOURCE_COLLECTION_DESIGN.md#5-필드-배치와-계획-v2)를 따른다.

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
- 선택형 scope의 정확한 등록 ID/부모 관계, 다른 블록의 동명 프로젝트 문자열, scope 누락과 null/잘못된 ID/추가 키 구분, 동일 원천에서 고정 ID 보존. scope 변경은 봉인 hash와 분류 재사용 범위를 분리함.
- 다른 실행을 연속/동시에 조회해도 상태가 섞이지 않음. plan 반환만으로 원천/report·DB·PoR을 변경하지 않음.
- 같은 원천 실행/개정의 동일한 필드 대응, 실제 실행 또는 기준 목록 개정 시 그 변경을 정확히 보존.
- 수치 기준·0건/완료·적용 제외의 의미는 parser/업무 담당자의 실제 확인 결과와 비교.

공통 합성 예제 검사:

```bash
.venv/bin/python -m unittest discover -s tests -p test_integration_contracts.py -v
```

회사 전용 테스트를 작성한 뒤 별도로 실행한다.

```bash
.venv/bin/python -m unittest discover -s vwp/integration/company_plan -p 'test_*.py' -v
```

기존 runner는 회사 provider나 비공개 테스트/fixture를 자동 수집하지 않는다. 실환경 검증·운영 적용과 모의 테스트를 나눠 기록한다. 예상 밖 실패 시 수정/재시도를 멈추고 가이드의 비식별 문의를 남긴다. 실제 실행 메타데이터·report·SDK/접속 정보는 공개 저장소와 외부 Codex 대화에 넣지 않는다.

## 6. Q1 이후의 회사 launcher 작업

`company_plan/`에서는 source root의 원본을 수정하지 않고 단일 report 루트에 byte 동일 사본을 만드는 입력 준비를 구현할 수 있다. 복사 전후 원본/사본 hash를 검사하고, 누락 unit과 예상 밖 report를 보존하며, plan·완료 표식·승인 자료는 report 루트 밖에 둔다. 원문 복사는 `get_plan()` 내부에 넣지 않는다.

중앙 예상 목록이 없으면 프로필과 담당자의 선언으로 **예상 범위 초안**을 준비할 수 있다. 실제 report 존재 여부로 예상 단위를 정하지 않고, 전체 슬롯×mode×item과 PRE 관계를 명시한다. 확인된 실제 승인 원천이 없으면 `confirmed=false`를 유지한다. 기존 OA의 `REVIEW_BLOCKED`/`INVENTORY_UNCONFIRMED`를 우회하지 않는다. 실행 ID·승인 권한·parser 평가 기준 등이 미확정이면 `FACTS_REQUIRED`로 남긴다.

V2 공통 타입/validator·봉인·DB 전송·workspace import와 CLI·접수/보관/receipt는 제공했으며 사내 AI가 수정하지 않는다. V2는 새 제출 CLI/source 작업자를 사용한다. 기존 `collect`/`analyze`에 V2 fixture를 넣거나 임의 sidecar/추가 필드를 운영 전송으로 취급하지 않는다.
