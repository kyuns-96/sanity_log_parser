# VWP 근거·OUTBOX의 무손실 압축 저장

2026-10-27 제공한 선택형 011이다. 기존 네 테이블을 유지하고 VWP private에 `payload_bytes BYTEA` 한 컬럼과 `sanity_vwp_payload_storage_v1` CHECK를 추가한다. 원본 report, 근거 원문, 전체 파싱 로그, AI/사람의 판단 이력은 보존한다. 원본 삭제·보관 기한 변경·별도 DB/스토어 도입은 포함하지 않는다.

## 저장과 호환성

| 대상 | 저장 방식 | 읽기·검증 |
|---|---|---|
| 원본 `RAW_CHUNK` | 기존 SHA256 기반 원본 byte 조각. `raw_bytes`는 EXTENDED로 압축 허용 | 기존 전체 파일/조각 크기·SHA256 검증과 byte 동일 복원 유지 |
| V2 `EVIDENCE` | 충분히 줄어드는 경우 전체 payload와 UTF-8 근거 원문을 함께 gzip 저장. JSONB에는 조회 키·codec·압축 hash·확장 크기·DB 내부 참조 보존 | payload·근거 byte를 복원하고 기존 `raw_sha256`와 대조. ID·위치·전체 fields·판정·이유 유지 |
| `SOURCE_OUTBOX` | 정확한 전체 패킷을 gzip 저장. JSONB에는 제출·순서·message ID·collection ID·codec/hash/크기 보존 | 기존 패킷·message ID·내용 충돌 검사와 재전송 그대로 |
| 기존 평문·작은/압축 이득 없는 payload·legacy 근거 | 기존 저장 형식 유지 | 같은 공통 reader가 평문과 압축형을 함께 읽음 |

압축된 V2 근거는 원문도 `payload_bytes` 안에 보존하므로 물리 `raw_bytes`는 NULL일 수 있다. `local_evidence_ref=db-gzip-v1:<record_id>`는 **같은 행의 검증된 압축 본문 참조**이며 외부 폴더 참조가 아니다. 기존 원문/해시 CHECK는 유지하며 새 CHECK도 참조·codec·압축 hash·크기 경계를 검사한다. 앱은 복원한 원문의 기존 SHA256를 추가 검증한다. 이 구현 없이 기존 행을 수동 NULL 처리하면 안 된다.

JSONB `payload`만 직접 조회하면 압축형의 본문 전체를 얻을 수 없다. 공통 `PrivatePayloadStorage.restore`/`restore_row`, `public_records`, `SubmissionStore`가 논리 payload와 근거 원문을 복원한다. 회사 parser/SDK 인터페이스와 OA 수신·화면·모델 입력·전송 schema는 바꾸지 않는다. 파서가 DB를 직접 읽는 방식은 기존에도 허용되지 않는다.

압축 hash·크기·codec·조회 키·근거 원문 hash 불일치는 `STORED_PAYLOAD_CORRUPT`, 불완전한 011은 `PAYLOAD_STORAGE_SCHEMA_REQUIRED`로 중단한다. 잘못된 압축형을 평문이나 잘린 원문으로 대체하지 않는다. 확장 크기는 최대 64MiB로 제한한다.

## 신규 기록 도입 순서

1. 먼저 호환되는 VWP 공통 코드를 배포한다. 011 이전에는 기존 평문 쓰기·읽기를 유지한다. OA 전달 형식은 동일하다.
2. DBA가 010 적용과 백업·작업 창을 확인하고 [011_payload_storage.sql](011_payload_storage.sql)을 한 트랜잭션으로 검토·적용한다. 기존 데이터는 덮어쓰지 않는다. 다른 스키마는 원본을 보존한 비공개 사본에서 `public.` 참조를 모두 대응하며 복잡한 식별자 인용은 DBA가 처리한다.
3. 장기 실행 worker를 정상 재시작하면 검증된 011을 인식해 새 V2 근거/OUTBOX에 압축 저장을 사용한다. 작은/비압축성 본문은 그대로 저장한다. 새 환경변수나 broad GRANT는 없다. 기존 표 단위 SELECT/INSERT 및 제한 UPDATE 계약을 유지하며 `payload_bytes`를 앱이 UPDATE하지 않는다.
4. 각 bridge의 기존 DB 권한 check와 제출·OA OWNER 확정·전체 로그 검토를 확인한다. 011이나 압축 저장 성공이 AI 판단 품질 검증 완료를 뜻하지 않는다.

빈 초기 포털은 `prepare --payload-storage`로 기존 59단계 뒤 **60번 단일 ALTER**를 선택한다. `plan.json`의 55단계와 `raw-plan.json`의 59단계는 보존한다. 기존 010 완료·0행 snapshot에서는 60번만 준비한다. 포털의 0행/쓰기 중지 조건은 유지하며 데이터가 있는 DB는 DBA의 원본 011 경로다. [포털 안내](portal/README.md)

## 기존 데이터 변환과 물리 공간 회수

이 절은 사용자가 관리하는 **별도 DBA 유지보수**다. 제한된 앱 로그인이나 사내 연동 AI에 owner 권한을 주지 않는다. 공통 코드 배포와 011 적용만으로 기존 행이 자동 압축되거나 디스크가 줄어들지 않는다.

`vwp_bridge.payload_compaction`은 DSN 값을 인자로 받지 않고 승인된 비공개 환경변수 **이름**을 받는다. 아래 `sanity_app`과 환경변수 이름은 예시다. 실제 접속값·자료·hash 결과는 사내에 보관한다. 변환 전후 비교 중에는 VWP 쓰기를 중지한다. 각 출력은 새로운 비공개 기록에 보관한다.

```bash
# 읽기 전용: 011 전후/변환·재작성 전후의 논리적 동일성 기준
.venv/bin/python -m vwp_bridge.payload_compaction \
  --schema sanity_app --dsn-env SANITY_MAINTENANCE_DSN --fingerprint

# 기본값은 읽기 전용 dry run. --max-rows는 한정된 미리보기이며 전체 검증이 아니다.
.venv/bin/python -m vwp_bridge.payload_compaction \
  --schema sanity_app --dsn-env SANITY_MAINTENANCE_DSN --max-rows 1000

# 승인된 owner가 실행하는 명시적 데이터 변환
.venv/bin/python -m vwp_bridge.payload_compaction \
  --schema sanity_app --dsn-env SANITY_MAINTENANCE_DSN --apply --batch-size 32
```

- 실제 변환은 테이블 소유자 역할/승인 DBA만 가능하다. 제한 앱 계정은 읽기 전용 확인만 가능하고 `--apply`는 `COMPACTION_OWNER_REQUIRED`로 거부된다.
- 후보는 봉인된 V2 source 근거와 불변 OUTBOX다. legacy 분류 근거·원본 조각·판정·개정·진행 상태는 변환하지 않는다. 원문도 압축 본문에 보존한다.
- 한 번에 한 본문을 복원하며 batch마다 ACCESS EXCLUSIVE 잠금과 별도 트랜잭션을 사용한다. 모든 논리 컬럼·ID·원문/해시·state·version·시각의 동일성을 확인한다. 실패한 현재 batch는 rollback하고 이전에 커밋된 batch는 보존한다. 재실행은 이미 변환한 행을 건너뛴다.
- `rows_would_change`는 후보 수, `rows_changed`는 실제 변환 수다. `column_bytes_*`는 JSON 표현·압축 byte·별도 raw byte의 합이며 테이블 물리 크기가 아니다. 부분 제한은 `scan_scope=limited`로 표시한다.
- UPDATE는 MVCC의 이전 행·인덱스 사본을 남기므로 변환 직후 디스크 사용량이 **증가할 수 있다**. 도구는 DDL·VACUUM·DELETE·TRUNCATE를 실행하지 않으며 `physical_space_reclaimed=false`를 반환한다.

기존 공간을 즉시 줄이려면 DBA가 백업 복구 가능성, 쓰기 중지, 잠금 시간, 기존/새 테이블·인덱스·WAL을 수용할 여유 공간을 확인한 뒤 [별도 재작성 SQL](maintenance/repack_payload_storage.sql)을 검토한다. SQL은 전체 테이블을 다시 쓰고 인덱스를 재구성하며 커밋까지 ACCESS EXCLUSIVE를 유지한다. 원래 논리 값·hash·ID·시각은 바꾸지 않으며 컬럼 저장 속성도 유지한다. 실제 실행 시간과 추가 공간은 자료량·인프라에 따라 다르다.

재작성 전후 `--fingerprint`의 행 수·논리 SHA256가 같아야 한다. 이 값은 UTC의 일관된 읽기 스냅샷으로 **모든 논리 컬럼·근거/원본 byte**를 검증하며 압축 표현 자체는 제외한다. 평상시 쓰기가 계속되면 정상 상태 변경도 hash에 반영되므로 쓰기 중지 중 비교한다. 이후 DBA가 통계를 갱신하고 실제 `pg_total_relation_size`와 main/TOAST/index, 제출 재시도·원본 복원·OA 전체 로그를 확인한다.

## 평문 복구와 이전 코드로의 복귀

이전 reader를 배포하기 전에 쓰기를 중지하고, 현재 reader와 승인된 owner에서 다음 **명시적 복구**를 수행한다.

```bash
.venv/bin/python -m vwp_bridge.payload_compaction \
  --schema sanity_app --dsn-env SANITY_MAINTENANCE_DSN --restore-plain

.venv/bin/python -m vwp_bridge.payload_compaction \
  --schema sanity_app --dsn-env SANITY_MAINTENANCE_DSN --restore-plain --apply
```

복구는 payload와 근거 `raw_bytes`를 원래 값으로 되돌리고 압축 컬럼만 NULL로 만든다. 동일성 fingerprint·전체 근거 조회·남은 압축 행 0건을 확인한 뒤 이전 reader를 사용한다. 기존 데이터나 011 컬럼/제약을 삭제할 필요는 없다. 압축 본문이 손상됐다면 원문을 추정하거나 누락한 채 복구 완료로 표시하지 않는다.

## 합성 검증과 용량 비교

다음 도구는 **새 소켓 전용 합성 DB만 생성**하며 기존 DB/DSN/report를 입력받지 않는다. 전후 전체 논리 행·원문 byte·마스킹된 source records·OUTBOX 패킷의 hash와 행 수가 같아야 결과를 기록한다. 종료 시 생성한 서버를 중지한다.

```bash
.venv/bin/python db/tests/benchmark_payload_storage.py \
  --pg-bin /path/to/postgresql/bin --rows 10000 --out /private/new-storage-benchmark.json
```

PostgreSQL 12.1의 합성 10,000건 비교에서 원문은 3,170,000 bytes, 전후 저장 행은 10,017건으로 동일했다. 변환·명시적 재작성 후 해당 VWP 테이블의 물리 총량은 **26,492,928 → 19,374,080 bytes (26.87% 감소)**였다. 변환 직후에는 38,887,424 bytes로 늘었다. 회사 원문의 압축률·최종 사용량을 보장하는 결과가 아니며 실제 회사 DB 사용량에 이 비율을 그대로 적용하지 않는다. OA/전송 큐의 별도 사용량·WAL·백업량도 전체 용량 계획에 포함한다.

PostgreSQL의 압축과 기존 행 재저장 의미는 [PG12 TOAST](https://www.postgresql.org/docs/12/storage-toast.html), [ALTER TABLE](https://www.postgresql.org/docs/12/sql-altertable.html)을, 물리/논리 집계 구분은 [용량 측정 함수](https://www.postgresql.org/docs/12/functions-admin.html#FUNCTIONS-ADMIN-DBSIZE)를 따른다. 사내 원문·실제 모델의 판단 품질은 사내 검증 대상이다.
