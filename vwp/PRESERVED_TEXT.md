# 보존 본문과 AI 근거 — plan 4

2026-10-28 공통 구현. [plan 3 보존 계약](PRESERVED_SOURCES.md)의 원본 보관·귀속·OWNER 확인을 유지하면서, 명시적으로 선택한 UTF-8 텍스트 전체를 마스킹해 OA와 AI에 제공한다. 원본 bytes는 VWP private RAW에 그대로 남는다. 파일을 읽는 것과 SDC 실행·도구 형식의 의미 검증·승인된 점검 기준은 구분한다.

## 입력과 호환

본문 제공 또는 다중 대상 binding이 필요한 제출은 `plan.schema_version=4`를 사용한다. 최상위/collection/units/approval는 기존과 같고 `inventory.preserved_files[]`는 정확히 `path`, `role`, `binding`, `content`를 가진다. 기존 plan 2/3은 변경 없이 읽고 쓴다. 아래는 전체 plan이 아닌 선언 예제다.

```json
{
  "preserved_files": [
    {
      "path": "reports/companion/full.rpt",
      "role": "canonical_companion",
      "binding": {"kind": "units", "review_unit_ids": ["u-gca-mission", "u-gca-test"]},
      "content": "utf8-text-v1"
    },
    {
      "path": "reports/companion/input.sdc",
      "role": "constraint_input",
      "binding": {"kind": "execution", "anchor_unit_id": "u-gca-mission"},
      "content": "utf8-text-v1"
    }
  ]
}
```

- `content="utf8-text-v1"`: 전체 UTF-8 텍스트를 줄 단위 근거로 추출한다. 형식별 규칙을 임의 추정하지 않는 공통 텍스트 경로이며 SDC/Tcl을 실행하지 않는다. `content=null`은 메타데이터만 제공한다. 바이너리·다른 encoding은 null로 보존하거나 확인된 별도 형식 계약을 문의한다. 오류 뒤 자동 null 전환은 없다.
- `binding`은 plan 3의 `unit`/`execution`에 더해 `units`+`review_unit_ids`, `executions`+`anchor_unit_ids`를 지원한다. 같은 plan에 존재하는 중복 없는 정확한 ID 목록이어야 한다. execution은 각 anchor의 source_unit/item/mode/버전과 execution_ref에서 산출하며 도구 전체 run으로 확대하지 않는다.
- 같은 파일을 여러 대상에 연결해도 파일·본문 전송은 한 번이다. 각 report에는 해당 파일의 검증된 참조만 연결한다. 다른 블록·임의 외부 대상·unit.report 충돌·범용 context 역할은 허용하지 않는다.
- scope digest에 본문 제공 선택과 모든 논리 귀속 대상을 결합한다. 실제 실행/파일 bytes/hash는 기존 content/manifest 무결성에 결합한다. 스캔 자동 승인·필수 unit 제거·예상 밖 파일 무시를 허용하지 않는다.

이 데이터 버전은 제출 CLI/bridge 설정 파일의 schema 번호와 별개다. plan/collection manifest는 **4**, source manifest와 연결된 analysis producer는 **`postgresql-source-2.4`**다. HTTP/DB envelope·`source-collection-v2`·메시지 종류는 기존 2를 유지한다. **OA 수신·공통 reader/API/AI worker/UI와 bridge를 먼저 배포한 뒤 VWP CLI/prepare에서 plan 4를 활성화**한다. 구버전 reader는 새 데이터를 이해하지 못하므로 새 필드를 제거해 통과시키지 않는다. 회사 AI는 기존 허용 입력 어댑터만 대응하며 공통 코드 수정 권한은 확대되지 않는다.

## 전체성·마스킹·저장

bridge는 검증된 RAW snapshot에서 해당 파일의 전체 크기·SHA256을 다시 대조한다. 알려진 자격증명과 기존 SecretGuard의 비밀 패턴을 **전체 텍스트에서 먼저 마스킹**한 뒤 줄 경계를 유지한 근거 구간을 만든다. 여러 줄의 키나 구간 경계에 걸친 비밀도 먼저 처리한다. 이 마스킹을 모든 개인정보·사내 민감 자료의 일반 DLP로 간주하지 않는다.

각 구간은 `pe-…` ID, `source_id=pf-…`, 원문 line_start/end·byte_start/end, 원문 구간 SHA256, 마스킹 text/text_sha256와 redacted를 가진다. 추출 메타데이터는 format·masking_version·전체 원본/텍스트 hash/크기·행 수·마스킹 여부·순서가 있는 part 참조를 가진다. 전체 구간의 byte/줄 범위에 빈틈·중복이 없어야 하며 마스킹 없는 자료는 원본 전체 hash까지 OA에서 재검증한다. 마스킹 자료의 원문 hash는 VWP의 검증된 원본에 결합한다.

전송은 기존 SOURCE_PART 안에 명시적 `kind="preserved-text-v1"`, `source_id`, `part_index`, `segments` 본문을 사용한다. 로그 records나 가짜 report/unit을 생성하지 않는다. 최대 16개 근거 구간·512KiB를 한 part에 담아 행·인덱스·참조 메타데이터 중복을 줄인다. OA는 WS_SOURCE_PART로 보관하며 013이 있으면 같은 공유 큐 본문을 참조한다. 013의 물리 참조 header `report_id`에는 이 경우 파일 소유 참조인 pf ID, `record_count`에는 segment 수를 저장한다. 이것은 검사 로그 수/보고서 ID가 아니며 공통 reader가 kind와 원래 본문을 검증한다. 별도 SQL·테이블·GRANT는 없다.

SOURCE_COMMIT는 로그 part와 본문 part의 정확한 전체 집합을 결합한다. 미수신·중복·다른 파일/블록·변조·누락된 중간/마지막 구간을 거부하고 전체 검증 전 공개하지 않는다. 토큰 없는 접수는 기존 SSO 현재 블록 OWNER 확정 뒤에만 보고서·본문을 조회할 수 있다. 원본·본문·사람의 과거 이력을 삭제/재작성하지 않는다. 본문 또는 추출/마스킹 결과가 바뀌면 별도 analysis/report이며 source digest에 반영된다.

현재 명시 한도는 파일당 원본 16MiB, 한 제출에서 본문 제공을 선택한 원본 합계 32MiB, 한 원문 줄/근거 구간 128KiB, 파일당 1,024 parts다. 마스킹 결과는 파일당 32MiB 이내여야 한다. 인코딩/제어문자·긴 줄·용량 초과는 `PRESERVED_TEXT_ENCODING`, `PRESERVED_TEXT_LINE_LIMIT`, `PRESERVED_TEXT_LIMIT` 등으로 중단하며 잘라서 성공시키지 않는다. 실제 업로드 제한과 기존 SOURCE_MESSAGE_LIMIT도 적용한다.

## OA 조회와 AI 사용

기존 report 조회 권한 안에서 아래 경로를 제공한다.

```text
GET /api/workbench/blocks/{block}/versions/{version}/reports/{report}/preserved/{pf-id}
GET 같은 경로?offset=0&limit=10
GET 같은 경로?download=true
```

페이지는 최대 20개 근거 구간이며 반환 전 전체 본문 무결성을 검증한다. report에 귀속되지 않은 파일·다른 블록·미확정 제출은 조회할 수 없다. 다운로드는 **마스킹된 검토용 UTF-8 텍스트**다. 미마스킹 원본 bytes는 VWP에서 기존 RAW 복원을 사용한다. 브라우저에 본문을 명령이나 HTML로 실행하지 않는다.

AI worker는 해당 report의 본문 전체를 검증해 `context.preserved_evidence`로 입력한다. 현재 로그/선택 그룹의 전체 입력과 승인된 기준·확정 사례 조건은 유지한다. 파일 내용과 그 안의 지시는 분석 자료이며 실행 지시가 아니다. 마스킹된 부분이나 metadata-only 파일의 내용을 추정하지 않도록 prompt에 명시한다. Rule Information을 자동 승인 criterion으로 승격하지 않는다.

본문이 있는 모델 응답은 기존 출력 키에 `preserved_citations` 배열을 추가한다. 각 항목은 `{target_id,evidence_ids,reason}`이며 분류 target_id는 출력 그룹 ID, 판정 target_id는 `review`다. 제공한 pe ID만 1~20개 인용할 수 있고 실제 본문을 사용하지 않았다면 빈 배열을 반환한다. pf ID·다른 파일/이전 개정·미제공 구간·존재하지 않는 대상/중복 인용은 거부한다. 기존 로그 evidence_ids·criterion_ids·case reference_ids에는 pe ID를 넣지 않는다.

서버는 별도 본문 인용을 검증해 모델 metadata의 `preserved_citations`, 검증된 원문 위치의 `preserved_citation_sources`, `preserved_input_sha256`에 저장한다. 실제 요청 전체 SHA256·`.preserved-text-v1` prompt 버전·불변 job/source 개정과 함께 남기고 결과 공개 시 다시 검증한다. 사람의 분류/판정 확정은 기존 AI 원안/제안 참조와 연결하며 AI 인용을 사람의 확정으로 자동 승격하지 않는다. 다른 블록의 공유 사례 DTO에는 본문이나 이 원문 근거 목록을 추가하지 않는다.

**DB 분할 전송과 모델 분할 실행은 다르다.** 현재 AI 호출은 설정 한도 내의 전체 로그·본문·사례를 한 요청으로 제공한다. 초과는 모델 호출 전 `AI_INPUT_LIMIT`이며 일부 본문으로 대신하지 않는다. COMMON-05의 여러 모델 호출/조각 간 그룹 통합은 계속 후속이다. 실제 SDC 의미·도구 버전/완료·CSV 대응·승인 기준/정답과 모델 판단 품질은 사내에서 검증한다. UTF-8 본문 전달과 mock 모델 성공을 그 품질 검증 완료로 보고하지 않는다.
