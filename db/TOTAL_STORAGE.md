# 원본 약 14MB의 전체 DB 저장량 20MB 목표 — 013

2026-10-28 사용자가 확정한 기준은 **VWP private·OA private·양방향 전송 큐의 네 테이블 전체**다. 각 `pg_total_relation_size`를 합산해 본문·TOAST·인덱스를 포함한다. MB는 1,000,000 bytes다. 원본 한 건의 정상 제출을 비교하며, 누적된 다른 report/판정 이력·WAL·백업·작업 중 임시 공간은 별도 용량 계획 대상이다. 기존 데이터나 근거를 버려 수치를 맞추지 않는다.

011은 행별 압축, 012는 VWP 근거 묶음을 제공한다. 012만으로 전체 목표가 완료되지는 않는다. 선택형 [013_shared_source_packets.sql](013_shared_source_packets.sql)은 **마스킹된 SOURCE_PART 전체 패킷을 기존 공유 큐에 한 번 보존**하고 VWP OUTBOX와 OA source part가 그 본문을 검증해 사용하도록 한다. 새 테이블·스토어·역할·GRANT·HTTP 경로는 추가하지 않는다.

## 한 본문과 두 private 참조

| 위치 | 013 이후 신규 자료 |
|---|---|
| VWP `RAW_CHUNK/RAW_FILE` | 기존 전체 원본 byte·명세·hash 유지 |
| VWP `SOURCE_EVIDENCE_PAGE` | 012의 전체 private 근거·정규화된 원문 유지. 공유 마스킹본으로 대체하지 않음 |
| `sanity_vwp_to_oa`의 `SOURCE_PART` | 기존 전체 source packet을 gzip 보관. 처리 완료 후에도 본문 유지 |
| VWP `SOURCE_OUTBOX` | 제출/순서/kind/message ID/collection ID·전체 패킷 SHA256와 `source_packet_ref` |
| OA `WS_SOURCE_PART` | report/part 순서·패킷/본문 SHA256·건수와 `source_packet_ref` |
| 나머지 source 명세·commit·receipt·사람/AI 이력 | 기존 계약 유지 |

두 private 참조 컬럼은 같은 공유 큐의 `message_id`로 FK를 맺는다. **참조된 큐 행의 DELETE와 ID 변경은 NO ACTION FK가 차단**한다. 앱의 payload/참조 UPDATE·DELETE 권한은 늘리지 않는다. 큐 ACK를 받았다고 본문을 NULL 처리하거나 큐를 비우면 안 된다. 현재 제공 범위에 GC·보관 기한 변경·참조 해제 도구는 없다.

공통 reader는 기존 공유 큐 SELECT 권한으로 본문을 읽고 gzip/크기/hash·정확한 message ID·제출·project/block·report/part·전체 건수를 확인한다. OA는 VWP private를 읽지 않는다. 원래 패킷·개별 로그 ID·원문 위치·fields·도구 판정·이유·마스킹된 raw를 복원하며, 과거 사람 확정·정정 근거와 현재 AI의 전체 입력을 유지한다. 회사 parser/SDK와 논리 source packet JSON은 바꾸지 않는다.

FK·컬럼·CHECK·commit 조회 인덱스가 불완전하면 `SHARED_SOURCE_SCHEMA_REQUIRED`, 참조·본문·scope가 다르면 `SHARED_SOURCE_CORRUPT`로 중단한다. 다른 블록의 유효한 패킷으로 참조를 바꿔도 현재 private 소속 검사를 통과하지 않는다. 누락된 본문을 대표/일부 로그나 빈 목록으로 대신하지 않는다.

## 저장과 발행의 구분

파싱 도중 만든 SOURCE_PART는 먼저 **마스킹·공개 가능성·형식 검사를 통과한 전체 본문**을 큐에 기록하고 private OUTBOX 참조와 함께 커밋한다. 이 행의 header에는 `__source_commit_gate__=true`가 있다. 큐 상태가 READY라도 아직 발행 가능한 메시지라는 뜻은 아니다.

공통 `Queue.claim`은 같은 제출·project·내용 hash·collection의 최종 SOURCE_COMMIT가 생기기 전에는 이 part를 선택하지 않는다. 명시적 message ID 요청도 같다. worker는 기존처럼 원본 snapshot 재검증·모든 미발행 패킷의 preflight를 수행한 뒤 최종 COMMIT를 마지막에 등록한다. 실패/중단 시 준비한 본문과 참조는 남고, 재시도는 내용을 검증해 재사용한다. 다른 정상 제출의 진행을 막기 위해 미완료 part를 임의 수신 완료 처리하지 않는다.

OA는 종전과 같이 전체 명세·조각·commit·건수/ID/hash·현재 권한을 대조하고 원자적으로 공개한다. 토큰 없는 제출의 OWNER 범위 확정과 SSO/프로젝트·블록 권한은 유지한다. 저장/접수·용량 목표 달성은 AI 판단 품질 검증 완료가 아니다.

## 적용 순서와 기존 데이터

1. **VWP와 OA 양쪽에 013 호환 공통 코드를 먼저 배포**한다. 013 이전에는 평문/011/012 형식을 계속 읽고 쓴다. 이전 OA reader는 새 참조 형식과 발행 gate를 이해하지 못한다.
2. 승인 DBA가 백업·**VWP worker와 OA receiver 모두 중지**·잠금/여유 공간을 확인하고 미적용 migration만 010 → 011 → 012 → 013 순서로 검토·적용한다. 앱은 DDL을 실행하지 않는다. 013은 세 테이블의 ACCESS EXCLUSIVE 잠금·5초 lock timeout과 한 트랜잭션을 사용한다. 기존 body는 자동 변환하지 않는다.
3. 스키마 완료 뒤 양쪽 worker/receiver를 정상 재시작해 저장 기능의 탐지 상태를 갱신한다. 기존 DB 권한 check, 새 제출/중단 재개, 원본 복원, 전체 로그, OA OWNER 확정·사람의 검토를 확인한다. 새 테이블/역할/광범위 권한을 발급하지 않는다. 기존 표 단위 SELECT/INSERT는 새 private 참조 컬럼에도 적용되며 제한 UPDATE 범위는 유지한다.
4. 실제 사내 자료로 네 테이블의 물리 합과 모든 원본/근거·ID·재전송 결과를 검증한다. 부분 테이블만 작아진 수치를 전체 목표 달성으로 보고하지 않는다.

빈 초기 포털은 `prepare --shared-source-packets`와 `shared-plan.json`으로 010~013을 선택한다. 기존 55/59/60/62단계 계획은 보존하며 **013은 63~66번의 네 문장**이다. 012 완료·0행·쓰기 중지 snapshot이면 네 문장만 준비한다. 데이터가 있는 DB에는 포털의 0행 조건을 풀지 않고 DBA의 원본 SQL을 사용한다. [포털 안내](portal/README.md)

기존 EVIDENCE/OUTBOX/OA inline 본문은 그대로 읽으며 자동 합치거나 제거하지 않는다. 따라서 기존 대용량 DB가 migration만으로 20MB로 줄어들지는 않는다. 기존 데이터의 011 변환은 새 참조 OUTBOX를 건너뛰고, 읽기 전용 fingerprint는 실제 참조 패킷도 검증한다. 012 이후의 `--restore-plain` 차단도 유지한다. **이전 reader 복귀나 기존 inline→참조 변환 도구는 이번 범위에 없다.** 다른 기능을 되돌릴 때도 이 저장 reader를 유지한다.

## 전체 용량의 합성 재현

도구는 새 소켓 전용 합성 PostgreSQL만 만들며 사내 report·DSN·외부 endpoint를 받지 않는다. in-process HTTPS API → DB 원본 → 실제 `numeric_csv_v1` → VWP worker/전송 → OA 수신 → 최종 receipt를 실행한다. 실제 TLS 연결은 별도의 통합 테스트로 검증한다. 종료 시 생성한 DB를 중지한다.

```bash
.venv/bin/python db/tests/benchmark_total_storage.py \
  --pg-bin /path/to/postgresql/bin --layout shared \
  --rows 50000 --source-bytes 14000000 --out /private/new-total-storage.json
```

PG12.1에서 POST 원본 14,000,000 bytes·50,000건과 PRE 원본 224 bytes·4건을 제출했다. 네 테이블의 기존 합성 workspace 메타데이터까지 포함한 **새 기록 직후 총량은 13,033,472 bytes(13.03MB)**였다. 재작성·원본/근거 삭제를 사용하지 않았다. 같은 입력의 012 단독 비교는 29,523,968 bytes(29.52MB)로 목표를 넘었다.

| 테이블 | 012 단독 | 013 공유 참조 |
|---|---:|---:|
| VWP private | 12,099,584 | 6,152,192 |
| OA private | 11,190,272 | 630,784 |
| VWP → OA | 6,135,808 | 6,152,192 |
| OA → VWP | 98,304 | 98,304 |
| **전체 bytes** | **29,523,968** | **13,033,472** |

두 실행에서 모든 VWP/OA 논리 로그의 hash가 같았으며, 전체 원본 byte와 53개 OUTBOX/공유 패킷도 각각 대조했다. 전체 로그는 50,004건으로 유지됐다. 큐의 추가 인덱스·발행 header까지 합산한 결과다.

단위 혼동을 피하려고 POST 원본을 **14MiB(14,680,064 bytes)**로 늘린 별도 합성 실행도 확인했다. PRE를 포함한 원본은 14,680,288 bytes, 전체 네 테이블은 **13,246,464 bytes(13.25MB)**였고 같은 원본/전체 로그/53개 패킷 검사를 통과했다.

이 결과는 합성 입력의 목표 충족이며 모든 회사 파일의 절대 상한을 보장하지 않는다. 원문의 압축성·parser fields 양·고유 문자열·분석/재시도 이력·기존 bloat에 따라 달라진다. 참조 복원은 DB 조회와 gzip/hash 검증 CPU를 사용하므로 현장의 처리·읽기 성능도 검증한다. 과거 이력·다른 report·WAL·백업과 유지보수 중 임시 공간을 이 수치에 숨겨 포함하거나 제외하지 않는다.
