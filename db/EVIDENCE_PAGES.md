# V2 근거 묶음 저장 012

2026-10-28 사용자가 확정한 20MB 목표는 **VWP·OA private와 양방향 큐의 네 테이블 전체**다. 이 문서는 그중 VWP 근거를 줄이는 012만 설명한다. 012 단독의 VWP 수치를 전체 목표 달성으로 보고하지 않으며, [013을 포함한 전체 저장·측정 계약](TOTAL_STORAGE.md)을 따른다. 선택형 [012_source_evidence_pages.sql](012_source_evidence_pages.sql)은 새 V2 분석의 근거를 최대 256건씩 묶어 저장한다. MB는 1,000,000 bytes 기준이다.

## 보존하는 내용과 물리 저장의 변화

- 원본 report는 기존 `RAW_CHUNK`와 `RAW_FILE`에 남는다. 원본을 삭제하거나 외부 보관 폴더로 옮기지 않는다.
- 새 `SOURCE_EVIDENCE_PAGE` 행의 `page_bytes BYTEA`에 각 로그의 전체 payload와 정규화된 UTF-8 원문을 gzip으로 함께 저장한다. JSONB에는 압축 SHA256·확장 크기·첫/마지막 순번·정확한 건수·상태별 건수만 둔다.
- 묶음은 **최대 256건·압축 전후 각각 4MiB**로 제한한다. 메모리의 부분 묶음은 완료 경계에서만 기록한다. checkpoint에는 DB에 기록한 건수만 남기며, 중단 시 기존 DB 원본을 재파싱하고 이미 기록한 묶음의 모든 논리 값과 대조한다.
- 개별 `r-…` ID·순번·원문 위치·전체 fields·도구 판정·이유는 유지한다. 근거 원문 SHA256는 복원한 정확한 byte에서 동일하게 계산하며, 묶음 자체의 저장 hash·길이·형식과 전체 원본 파일 hash도 검증한다. 집계/대표 로그만 보존하는 방식이 아니다.
- `private_records`/`public_records`가 한 번에 한 묶음씩 검증·복원한다. 첫/중간/마지막 묶음 누락, 순번 중복, 잘못된 ID·unit·부모·hash·확장 크기는 차단한다. 봉인된 RUN의 전체 건수와도 대조한다.
- OUTBOX는 011 방식으로 원래 패킷 전체를 보존한다. OA 메시지·message ID·마스킹·중복/충돌·재전송·SSO OWNER 확정과 AI/사람의 판정 이력은 변경하지 않는다. 같은 논리 내용의 gzip 표현이 Python/zlib 버전에 따라 달라져도 재처리에서 기존 행을 덮어쓰지 않는다.

물리 행 수와 논리 로그 수는 다르다. 새 분석의 `count(*) WHERE record_type='EVIDENCE'`로 전체 로그를 세면 안 된다. 기존 분석은 행별 근거를 계속 사용하고, 새 묶음 분석은 공통 reader 또는 검증된 RUN의 `state.counts.EVIDENCE`로 전체 건수를 확인한다. 한 분석 안에 두 형식을 섞지 않는다. 회사 parser/SDK는 기존 인터페이스를 사용하며 DB를 직접 읽지 않는다.

## 스키마와 도입 순서

012는 기존 네 테이블 중 VWP private 한 곳에 `page_bytes`·묶음 CHECK·부분 UNIQUE 인덱스를 추가한다. kind/status는 이전 조건을 보존한 v5로 확장한다. 기존 PK·UNIQUE·FK·원본/근거의 CHECK와 011 계약은 유지한다. 테이블·역할·GRANT나 앱의 payload UPDATE/DELETE 권한은 추가하지 않는다. SQL 002~011과 기존 55/59/60단계 포털 계획은 변경하지 않는다.

1. **먼저 012를 읽는 VWP 공통 코드**를 배포한다. 012가 없는 DB에서는 기존 행별 저장을 유지한다. 코드를 먼저 반영해도 기존 데이터를 변환하지 않는다.
2. DBA가 백업·쓰기 중지·작업 창을 확인하고, 010 → 011 → 012 순서로 필요한 미적용 SQL만 검토·적용한다. 012는 한 트랜잭션과 ACCESS EXCLUSIVE 잠금·5초 lock timeout을 사용한다. 앱은 DDL을 실행하지 않는다. 스키마명이 다르면 기존 방식대로 원본을 보존한 비공개 사본의 식별자를 정확히 대응한다.
3. worker를 정상 재시작하고 기존 DB 권한 check·원본 복원·새 제출·전체 로그·중단 후 재개·OA OWNER 확정을 확인한다. 새로운 V2 분석부터 묶음 저장을 사용한다. 이미 행별 근거가 있는 분석은 재처리에서도 그 형식을 유지한다.
4. 실제 회사 입력으로 원본 hash·전체 건수/ID·전송 내용을 확인하고 `pg_relation_size`, `pg_table_size`, `pg_indexes_size`, `pg_total_relation_size`를 구분해 기록한다. 물리 용량 감소를 AI 판단 품질 완료로 보고하지 않는다.

빈 초기 포털은 `prepare --evidence-pages`와 `pages-plan.json`으로 **61번 ALTER·62번 UNIQUE 인덱스**를 선택한다. 011 완료·0행·쓰기 중지 snapshot이면 두 문장만 준비한다. 기존 데이터가 있는 DB에는 0행 조건을 풀지 않고 DBA의 원본 012를 사용한다. [포털 절차](portal/README.md)

불완전한 컬럼/저장 속성·CHECK·인덱스는 `EVIDENCE_PAGE_SCHEMA_REQUIRED`, 손상된 묶음은 `EVIDENCE_PAGE_CORRUPT`로 중단한다. 새 환경변수나 실패 후 다른 저장소로의 자동 전환은 없다.

## 기존 데이터·복귀와 유지보수 경계

**012는 기존 수만 개 EVIDENCE 행을 자동 합치거나 삭제하지 않는다.** 기존 데이터의 011 변환·별도 재작성은 [011 유지보수 절차](PAYLOAD_STORAGE.md)를 따른다. 기존 데이터에도 새 묶음 수준의 절감을 적용하려면 참조·시각·원문·개정·동시성을 보존하는 별도 owner 변환 설계가 필요하며 이번 제공 범위에 포함하지 않는다. 사내 연동 AI가 행 삭제·직접 NULL 처리·인덱스 제거로 대신하지 않는다.

012를 적용한 DB는 **012 호환 reader를 계속 유지**한다. 011의 `--restore-plain`만으로 이전 reader 호환성이 복구되지 않으므로 도구는 `PAGE_STORAGE_RESTORE_REQUIRED`로 거부한다. 묶음을 개별 행으로 되돌리거나 012 DDL을 제거하는 자동 도구는 제공하지 않는다. 다른 기능을 되돌릴 때도 저장 reader는 유지한다. 012가 없는 011 DB의 기존 평문 복구 기능은 그대로다.

읽기 전용 `payload_compaction --fingerprint`는 묶음의 실제 본문도 검증해 논리 hash에 포함한다. 기존 011 재작성 SQL을 별도로 사용하는 경우에도 묶음 byte와 EXTERNAL 속성이 보존되는지 검사한다. 신규 묶음 저장의 목표 충족에는 전체 테이블 재작성이 필요하지 않다. 기존 MVCC 공간 회수의 잠금·여유 공간·백업 조건은 별도 DBA 책임이다.

## 합성 용량 확인과 재현

다음 명령은 새 소켓 전용 합성 DB를 만들며 사내 report·기존 DB·DSN을 인자로 받지 않는다. 종료 시 생성한 서버를 중지한다. 전체 원본 byte/hash, 5만 건 모든 논리 로그, OUTBOX 전체 패킷을 생성 입력과 대조하고 재작성 전후에도 검증한다.

```bash
.venv/bin/python db/tests/benchmark_payload_storage.py \
  --pg-bin /path/to/postgresql/bin --evidence-pages \
  --rows 50000 --source-bytes 14000000 --out /private/new-pages-result.json
```

PG12.1의 합성 14,000,000-byte report·50,000건은 **새 기록 직후 15,327,232 bytes(15.33MB)**였다. 본문 172,032 bytes·테이블/TOAST 합 15,007,744 bytes·인덱스 319,488 bytes를 포함한 `pg_total_relation_size`이며 물리 행은 264건이다. 전체 근거는 여전히 50,000건이다. 별도 재작성 후에는 15,261,696 bytes였지만 **20MB 목표는 재작성 전에 이미 충족**했다.

같은 합성 입력과 전체 전송 hash가 동일한 별도 DB 비교는 다음과 같다. 011에 가장 유리한 재작성 이후와 비교해도 새 기록의 물리 총량은 86.41% 줄었다. 레이아웃별 행 수·시각이 다르므로 서로 다른 DB의 전체 행 fingerprint를 동일하다고 주장하지 않고, 입력·모든 논리 로그와 OUTBOX의 동일성을 비교했다.

| 저장 표현 | VWP 물리 총량 | 물리 행 수 |
|---|---:|---:|
| 평문 | 132,399,104 bytes | 50,068 |
| 011 행별 gzip·재작성 후 | 112,779,264 bytes | 50,068 |
| 012 묶음·새 기록 직후 | 15,327,232 bytes | 264 |

이 수치는 합성 입력의 결과다. 회사 parser가 생성하는 fields 양·고유 문자열·원문의 압축성·재시도/분석 개정·기존 bloat에 따라 달라지므로 모든 14MB 파일에 대한 20MB 상한 보장은 아니다. 목표 초과 시 원문·근거를 잘라 맞추지 않는다. 실제 사내 입력의 기능·용량·읽기 성능은 사내에서 확인하고, 외부 문의에는 비식별 집계만 전달한다.
