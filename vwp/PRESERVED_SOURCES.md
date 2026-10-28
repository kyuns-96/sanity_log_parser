# 비-unit 보존 근거 — plan 3

본문 제공·다중 대상 연결은 후속 구현된 [plan 4 계약](PRESERVED_TEXT.md)을 따른다. 아래는 기존 plan 3의 메타데이터 전용 계약이며 기존 제출의 동작은 유지한다.

2026-10-28 공통 구현. full.rpt 같은 원천 보고서와 SDC 같은 분석 입력을 원본 그대로 보관하면서, 예상 검사 목록과 구별하는 계약이다. [AI 판단 설계](../AI_DECISION_DESIGN.md), [제출 안내](SUBMISSION.md), [내부 AI 지침](../INTERNAL_AI_GUIDE.md)을 함께 따른다. 이 기능은 파싱·검사 PASS·AI 판단 완료를 뜻하지 않는다.

## 선언과 버전

기존 plan 2의 정확한 필드·해시·분석/확정 이력은 유지한다. 보존 파일이 필요한 제출만 `plan.schema_version=3`을 사용하고 `inventory.preserved_files`를 추가한다. 1~256개이며 빈 목록은 허용하지 않는다. 기존 collection·units·approval 필드는 동일하다. 아래는 전체 plan이 아닌 inventory 내부의 합성 예제다. 두 binding 모두 실제 같은 plan의 unit ID를 참조해야 한다.

```json
{
  "preserved_files": [
    {
      "path": "reports/companion/full.rpt",
      "role": "canonical_companion",
      "binding": {"kind": "execution", "anchor_unit_id": "u-gca"}
    },
    {
      "path": "reports/companion/input.sdc",
      "role": "constraint_input",
      "binding": {"kind": "unit", "review_unit_id": "u-gca"}
    }
  ]
}
```

| 필드 | 정확한 의미 |
|---|---|
| `path` | 기존 `reports/...` 안전한 상대 경로. 중복·경로 순회·unit.report와의 충돌 금지 |
| `canonical_companion` | CSV 등의 원천 보고서·카탈로그. 보존만으로 변환 동일성·제로 위반·완료 근거를 검증하지 않음 |
| `constraint_input` | 해당 검사에 적용된 입력 제약. 파일 존재를 승인된 점검 기준으로 승격하지 않음 |
| `unit` | 같은 plan의 정확한 `review_unit_id`에 귀속 |
| `execution` | `anchor_unit_id`에서 산출한 `source_execution(collection, unit)`에 귀속 |

execution ID는 source unit와 execution_ref의 조합이며 source unit에는 item·mode·원천 버전이 포함된다. 제출 전체나 도구 전체 run이라는 의미가 아니다. 한 파일을 여러 item/mode에 공유하는 다중 binding과 범용 `context` 역할은 이번 계약에서 지원하지 않는다. 필요한 경우 적용 대상 목록을 비식별 계약으로 문의한다. 기존 필수 unit을 제거하거나 가짜 unit을 추가해 보존을 대신하지 않는다.

| 경계 | 버전 |
|---|---|
| plan / `collection.json` snapshot manifest | 보존 선언 시 **3**, 기존은 **2** |
| 제출 HTTP/DB envelope, `source-collection-v2`, SOURCE_* 종류 | 기존 **2** 유지 |
| plan 3 source manifest producer | `postgresql-source-2.3` |
| 보존 근거가 연결된 unit analysis producer | `postgresql-source-2.3` |
| 보존 근거가 없는 기존 unit analysis | 기존 producer·분석 식별 유지 |

구버전 reader는 새 plan/manifest를 처리하지 못한다. **OA 수신·API·worker/UI와 bridge 공통 호환 코드를 배포한 뒤 VWP CLI/provider에서 plan 3을 활성화한다.** 필드를 제거해 구버전을 통과시키지 않는다. 기존 DB 네 테이블과 RAW 저장을 사용하며 이 기능 자체의 SQL/migration/GRANT 추가는 없다. 011~013 저장 최적화 적용 여부와 배포 순서는 해당 저장 문서를 별도로 따른다. plan 3 데이터가 존재하면 구버전 reader 복귀를 호환 rollback으로 간주하지 않는다.

## 수집·보관·범위 확정

회사 `CorporateSubmissionInput.prepare()`는 위 선언과 `SubmissionInput.reports[path] = 원본 Path` 대응을 반환한다. 파일을 변환하거나 잘라 보내지 않는다. 공통 snapshot이 전체 크기·SHA256을 산출하고 기존 HTTPS 업로드·RAW_FILE/RAW_CHUNK 보관·복원을 사용한다. 바이너리·개행·인코딩을 포함해 byte 동일 복원을 보장한다. 보존 파일도 기존 전체 업로드 파일 수/크기 제한에 포함된다.

- 선언한 파일이 없으면 `PRESERVED_FILE_MISSING`으로 제출을 거부한다. 보존 메타데이터나 파일 해시가 다르면 검증에 실패한다.
- 확인된 선언 파일만 예상 밖 목록에서 제외한다. 선언하지 않은 파일의 `UNEXPECTED_REPORTS`, 원래 unit의 누락·미완료·부분 파싱·기준 오류 차단은 유지한다.
- scope digest에는 경로·role·binding kind·논리 unit 범위를 결합한다. 실제 실행과 파일 byte/크기/hash는 content/manifest 무결성에 결합한다. 스캔 결과를 자동 승인하지 않는다.
- 토큰 없는 제출의 접수 대기와 OA SSO 현재 블록 OWNER 확정을 유지한다. OWNER 화면은 파일 목록·역할·귀속·크기/hash를 보여 주며 기존 content/scope/manifest digest·revision에 결합해 확정한다. TOP이나 관리자라는 이유로 블록 담당자의 확정을 대신하지 않는다.
- 다른 보존 파일이 연결되면 같은 CSV·원천 execution이어도 새 analysis/report 개정이다. 기존 report·사람 확정·사례를 덮어쓰지 않는다. 새 AI 작업의 source digest에도 새 보존 근거가 반영된다.

공통 검증 후의 `preserved_files[]` 메타데이터는 `id=pf-…`, `path`, `role`, `binding`, `sha256`, `size_bytes`, `unit_key`, `execution_id`, `storage_status="PRESERVED"`, `content_available_to_ai=false`다. ID는 해시·크기·귀속까지 포함한 전체 메타데이터에서 산출한다. source manifest에는 전체 목록, 각 report의 `preserved_sources`에는 해당 report에 귀속된 목록만 전달한다. 사용자 임의 hash로 보존 완료를 선언하지 않는다.

OA의 접수 상세·보고서·검토 화면은 이 메타데이터를 기존 블록 권한 아래 조회한다. 실제 절대 경로·임의 다운로드 URL·원본 본문은 제공하지 않는다. 원본은 VWP private RAW에서 복원하며 OA/공유 큐에는 미마스킹 파일 본문을 복제하지 않는다. SOURCE_PART는 기존 마스킹된 단위별 로그다.

## 이번 AI 연결과 후속 본문 활용

**구현:** 해당 report의 보존 메타데이터를 실제 OA 분류·그룹 판정 요청에 포함한다. 입력 SHA256과 불변 report/source digest에 결합하고 prompt 버전에 `.preserved-metadata-v1`을 기록한다. 모델에는 본문 미제공 사실을 명시한다. 파일 이름으로 SDC 조건이나 Rule Information을 추정하거나 읽었다고 주장하지 않으며, 부족한 근거를 설명하고 필요한 판정은 DEFER로 제안하도록 지시한다. `pf-…`는 로그 evidence ID·확정 case ID·승인 criterion ID가 아니며 그 인용 배열에 넣으면 기존 출력 검증이 거부한다. 기존 전체 로그·확정 사례·기준·사람 확정 흐름은 유지한다.

**후속 설계이며 미구현:** 실제 본문을 활용하려면 다음 단계를 함께 구현·검증한다.

1. bridge에서 선언된 원본 전체 hash와 정확한 binding을 검증한 뒤, 회사 형식에 맞는 추출기로 Rule Information과 SDC 사실을 얻는다. SDC/Tcl은 실행하지 않는다. 원본은 별도 불변 RAW로 남긴다.
2. 각 추출 결과에 원본 hash·원문 위치·추출기/형식/마스킹 개정·범위와 완전성 상태를 기록한다. 허용된 회사 parser의 단일 입력 추출 역할과 공통 호출/저장/전송 역할을 분리한다. 기존 collection-only parser는 선언만으로 자동 호출되지 않는다.
3. 개인정보·비밀 마스킹과 문서 내 지시 무시를 적용하고 해당 unit/실행의 검증된 발췌만 OA로 전달한다. 중간/마지막 근거를 임의 절단하거나 잘린 내용을 완전한 근거로 표시하지 않는다. 한도 초과는 명시적으로 실패/보류하고 별도 분할 계약을 사용한다.
4. AI 입력 snapshot에 사용한 근거 ID·hash·추출 개정을 고정하고 실제 파일 근거 인용을 검증한다. 역할·원문 도구 규칙을 승인된 업무 기준과 구별하며 사람이 확인한 적용 기준·예외·정정 이유를 DB 사례로 축적한다.
5. CSV↔full.rpt 원천 대응, 실제 적용 SDC/모드, 0건/실행 완료 의미, 다중 대상 공유, 크기 분포와 독립 정답·반례를 사내 합성 계약으로 확인한다. 실제 OA 모델에서 사례 유무·정정 전후의 판단 개선을 별도로 측정한다.

이번 메타데이터 연결이나 합성 mock 모델 테스트만으로 실제 SDC 해석·Rule Information 판단 품질 완료를 보고하지 않는다. OA 본문 열람/다운로드도 후속 기능이다.

## 용량·검증·사내 역할

용량 목표는 **원본 전체 14MB를 저장했을 때 네 테이블 합계 20MB 이내**다. 보존 파일도 원본 전체에 포함한다. 이전 CSV 단독 수치로 새 구성을 검증했다고 보고하지 않는다. 재현은 `db/tests/benchmark_total_storage.py --preserved-sources`로 수행한다. 이 합성 구성은 원본 예산의 30%를 full.rpt/SDC에 배정하고 나머지를 POST CSV와 PRE에 사용한다. 전체 원본 hash·로그/패킷 동일성과 TOAST·index 포함 물리 합계를 함께 검사한다. 실제 입력 압축률·누적 이력·기존 미회수 공간은 별도 측정 대상이다.

- VWP 담당: 배포된 plan 3 계약 아래 기존 허용 `prepare()`/회사 입력 wrapper에서 명시한 파일·역할·정확한 단위 귀속을 대응하고 전용 합성 테스트를 작성한다. 파일 발견만으로 보존 대상/예상 범위를 승인하지 않는다.
- bridge 담당: 공통 호환본을 배포하고 실제 원본 복원·완전성·상태를 현장에서 확인한다. 보존 본문 추출을 임의 연결하거나 UNEXPECTED/누락 차단을 수정하지 않는다.
- OA 담당: 수신/API/worker/UI 호환본을 함께 배포하고 SSO OWNER 확인·권한·메타데이터·새 개정·AI 실제 요청과 이력 연결을 확인한다. 파일 본문 활용 완료로 보고하지 않는다.

사내 AI의 공통 코드·SQL·UI·기존 테스트 수정 권한은 늘어나지 않는다. 예상 밖 오류는 중단 후 `WAITING_FOR_CODEX`로 비식별 문의한다. 실제 파일·URL·계정·토큰·DSN은 사내에 보관한다.
