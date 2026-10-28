# OA 전체 로그 배치 분류

**작업 중 체크포인트:** 최종 검증·main 병합·사내 배포 전이다. 진행 상태와 남은 검사는
[인계 문서](../HANDOFF_OA_BATCHED_CLUSTERING.md)를 따른다.

선택형 배치 분류는 현재 report의 **모든 기록을 AI 입력에 제공**한 뒤, 중간 그룹을
AI가 비교하고 서버가 전체 원본 ID의 정확한 partition을 검증해 제안 하나로 공개한다.
규칙으로 그룹을 확정하고 일부 대표 로그만 모델에 보내는 방식이 아니다.
사람의 수정·확정과 다음 report의 공유 확정 사례 활용은 기존 경로를 유지한다.

## 지원 범위

- `SANITY_OA_AI_BATCH_ENABLED=true`인 새 **분류 job**에 적용한다. 기본은 false다.
  기존 단일 호출 job/결과는 그대로 읽으며 과거 이력을 배치 처리한 것으로 바꾸지 않는다.
- 원본 ID는 배치 요청 안에서 짧은 별칭으로 대응하고 결과에서 다시 복원한다.
  각 기록의 raw·fields·위치는 그대로 제공한다. leaf마다 배정·그룹별 근거·인용을 검증한다.
- 중간 그룹의 label/근거로 비교 후보를 정렬한다. 다음 라운드는 근거 우선 정렬과 위치 이동으로
  이전 묶음 경계도 비교한다. 정렬은 후보 선정이며 필드/code/이름 일치로 자동 병합하지 않는다.
  모델이 해당 후보 그룹 전체를 유지/병합하고 서버가 구성원 합집합을 복원한다.
- 한 그룹이 남거나 두 번 연속 그룹 수가 줄지 않으면 최종 검증한다. 라운드/호출 수 한도까지
  안정되지 않으면 실패한다. 모든 가능한 그룹 쌍을 비교하는 알고리즘은 아니며, 실제 의미 분류
  품질과 잘못된 분리/병합은 사내 정답으로 평가해야 한다. 모호하면 별도로 유지하도록 지시한다.
- 통합 모델은 앞선 전체 로그 검토의 그룹 근거·mode 조건·예외/미확인 설명·참조를 비교한다.
  원본 전체를 통합 호출마다 다시 읽었다고 표시하지 않는다. 중간 그룹 내부 재분류는 자동으로
  수행하지 않으며, 필요하면 사람이 최종 제안을 분리/수정한다.
- plan 4 본문은 제공된 전체 본문을 각 호출에 유지하고 별도 인용을 검증한다. 필요한 본문이나
  기록 하나가 예산을 넘으면 호출 전에 실패한다. **대형 보존 본문 자체의 분할은 미지원**이다.
- **그룹 판정은 기존 단일 호출 경로**다. 분류 성공이 대형 그룹 판정 성공을 뜻하지 않는다.
  큰 확정 그룹의 분할 판정·기준별 통합은 후속이며 한도 차단을 유지한다.

## 입력·출력 예산

아래 키는 웹과 worker의 동일한 비공개 환경에 설정한다. 배치를 활성화하기 전에 실제 모델의
context/output·chat framing/reasoning·게이트웨이 body 제한과 출력 파라미터 지원을 확인한다.

| 키 (`SANITY_OA_AI_BATCH_` 접두사) | 기본 / 의미 |
|---|---|
| `ENABLED` | false; 새 분류 job에서만 선택 |
| `CONTEXT_TOKENS` | 0; 활성화 시 확인된 모델 context를 명시 |
| `INPUT_TOKENS` | 16000; 요청의 system/user 텍스트 예산. 아래 counter의 단위 |
| `OUTPUT_TOKENS` | 4096; 모델에 실제 전송하는 출력 예약 |
| `OUTPUT_PARAMETER` | 빈 값; Chat Completions는 `max_tokens`/`max_completion_tokens`, Responses는 `max_output_tokens` 중 지원된 값 |
| `OVERHEAD_TOKENS` | 1024; 모델의 메시지 포맷·추가 reasoning 등에 필요한 현장 여유 |
| `TOKENIZER_MODE` | `utf8_bytes` 또는 `tokenizer_json`; 기본 utf8_bytes |
| `TOKENIZER_FILE`, `TOKENIZER_SHA256` | tokenizer_json일 때만 필요한 절대 파일 경로와 정확한 SHA256 |
| `MAX_RECORDS` | 200; 건수 보조 상한, 고정 배치 크기가 아님 |
| `MERGE_GROUPS` | 20; 통합 호출의 그룹 수 보조 상한 |
| `MAX_CALLS`, `MAX_ROUNDS` | 20000 / 8; 초과 시 미완료 실패 |
| `DEADLINE_SECONDS` | 86400; 부모 job의 전체 기한 |

입력 예산은 `min(INPUT_TOKENS, CONTEXT_TOKENS - OUTPUT_TOKENS - ceil(context/10) - OVERHEAD_TOKENS)`다.
전체 system/user 텍스트·선택 사례·보존 본문을 함께 센다. 기존 `SANITY_OA_AI_MAX_INPUT_BYTES`는
실제 HTTP JSON body의 byte 제한으로 별도 유지한다. 게이트웨이가 decimal 1MB라면 1000000처럼
실제 제한에 맞춰 설정한다. 기존 `MAX_OUTPUT_BYTES`도 유지한다.

`utf8_bytes`는 UTF-8 **1 byte를 1 budget unit**으로 세는 보수적 추정이다. bytes/4로 token 수를
확정하지 않으며 실제 모델 token 수와 동일하다고 보고하지 않는다. tokenizer를 확인하기 전의
명시적 선택이며 해당 모델에서의 안전성과 overhead는 현장에서 확인해야 한다. 모델이 한도 초과나
불완전 응답을 반환하면 전체 실패로 처리하며 실패한 호출을 작은 배치로 자동 재호출하지 않는다.

`tokenizer_json`은 운영 모델에 맞는 로컬 파일을 [Tokenizers](https://huggingface.co/docs/tokenizers/api/tokenizer)로
로드한다. 실제 파일 hash를 재확인하고 padding/truncation을 해제해 전체 텍스트를 센다.
라이브러리는 [tokenizers 0.23.3](https://pypi.org/project/tokenizers/0.23.3/)으로 고정한다.
프로그램은 tokenizer/모델 파일을 다운로드하지 않는다. 잘못된 파일을 byte 계수로 대체하지 않는다.
문자열 계수에 special token을 포함하되 게이트웨이의 실제 chat template 전체를 자동 추정하지는 않는다.

출력 배정·근거가 커지는 경우를 고려해 leaf 건수는 `OUTPUT_TOKENS // 192` 이하(최소 1)로도 제한한다.
이는 계획용 추정이며 정확한 최악 출력 크기 보장은 아니다. 응답 잘림·출력 초과·누락은 실패한다.
순차 호출 한 개부터 시작한다. 같은 report의 동시 worker가 동일 예약을 다시 호출하지 않는다.

## 작업 이력과 중단

부모 job은 기존 `oa-ai-v1`, 자식 자료는 `oa-ai-batch-v1` namespace의 `WS_CONFIG`를 사용한다.
별도 테이블·DDL·GRANT·큐 재전송은 없다. plan·자식 결과는 압축 JSON으로 보관하고 작은 호출
이벤트를 hash chain으로 연결한다. 원문이나 인증 header를 감사 행에 복제하지 않는다.

새 라운드 계획은 `plan_format=2`로 전체 descriptor 목록을 한 압축 레코드에
보존한다. 기록 범위·원본 hash·통합 대상 참조는 생략하지 않으며, 각 호출 예약에는
해당 descriptor와 실제 HTTP body hash를 계속 남긴다. 첫 라운드의 hash는 부모가
고정한 계획 참조와도 대조한다. 기존 개별 `plan.*` 레코드 형식은 그대로 읽어
진행 중 작업을 재개한다. 기존 행을 변환하거나 삭제하지 않는다. 새 계획을 생성하기
전에 웹/worker 모두 이 reader를 배포해야 하며 이전 reader로 되돌릴 수 없다.

1. 전체 입력·사례/기준·설정 개정을 고정하고 모든 leaf를 사전 계획한다. 한 기록 과대도 첫 호출 전에 거부한다.
2. 네트워크 호출 전에 배치 범위, 실제 전송 body SHA256, prompt, 예약 ID·deadline을 commit한다.
3. DB 연결/권한 lock을 놓은 뒤 모델을 호출한다. 응답 SHA·검증 결과·실제 모델·사용량을 자식 결과에 저장한다.
4. 저장된 자식은 재시작 후 재호출하지 않는다. 다음 미호출 자식을 진행한다. 완료 여부를 모르는 예약은
   기한 후 UNKNOWN으로 끝내며 자동 재시도하지 않는다. 설정/권한/개정 변경은 STALE 등으로 중단한다.
5. 모든 최종 원본 ID의 누락/중복/미제공 배정, 그룹별 이유·참조, 현재 권한/개정을 확인한 뒤 제안을 원자 공개한다.
   부분 결과는 완성된 분류나 사람 확정 사례로 공개하지 않는다. RESULT_READY는 모델 재호출 없이 반영을 복구한다.

각 자식의 `input_sha256`은 실제 HTTP body hash다. 최종 결과는 여러 요청의 이력을 묶는
`input_hash_kind=BATCH_MANIFEST`와 `batch_receipt`를 사용한다. 이를 하나의 HTTP 입력 hash로
설명하지 않는다. 최종 배정은 자식 결과 참조에서 복구하며 별도의 전체 배정 복사본을 부모마다 저장하지 않는다.
개인 토큰은 job 시작 개정에 결합하고 호출/반영 때 재확인한다. 실제 응답 모델이 도중에 바뀌어도 중단한다.

기존 worker 명령을 사용한다. `--once`는 배치에서는 다음 한 단계(모델 호출 최대 1회)를 수행한다.
장기 운영은 기존 반복 실행을 사용한다. 화면은 기록 처리량과 그룹 통합 단계를 나눠 표시하고,
전체 완료 전 분류 확정·판정으로 진행하지 않는다. 모델 호출 성공과 사람 확정은 계속 구분한다.

## 검증과 현장 적용

합성 시험은 전체 기록 HTTP 전달/최종 partition, 같은 이름의 다른 조건과 배치 간 통합,
token/body 한도·한 기록 과대, 보존 본문·별도 인용, DB 자식 이력, 중복 worker·실패·UNKNOWN·재시작,
권한/개인 토큰/사람 개정 변경, 최종 공개 원자성과 화면 진행 표시를 확인한다.
전체 검증에는 10만 건 합성 입력을 포함한다. 실제 모델 품질 검증은 별도다.

감사 이력/최종 사람 이력이 추가되므로 기존 수집만의 용량 수치를 그대로 적용하지 않는다.
`db/tests/benchmark_total_storage.py --measure-ai`는 같은 합성 제출에 mock 모델을 연결하고
전체 네 테이블의 TOAST/index 포함 크기, 실제 전달 기록 hash·최종 배정과 호출 수를 기록한다.
`ledger_storage_by_kind`는 자식 이력 종류별 행 수와 `pg_column_size(payload)` 합계다.
이 값은 원인 분석용이며 네 테이블의 TOAST/index 포함 물리 합계를 대신하지 않는다.
현재는 보존 본문 없는 입력용 측정이다. 임의 초기화·삭제·repack으로 용량 목표를 맞추지 않는다.

현장 담당은 지원된 설정/토크나이저 자료·모델 한도·독립 정답 평가를 준비한다. 공통 모델/worker/
partition/SQL/기존 테스트의 자체 수정 권한은 확대하지 않는다. [기존 가이드](../INTERNAL_AI_GUIDE.md)를 따른다.
