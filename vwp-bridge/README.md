# VWP bridge — PostgreSQL

완료된 VWP snapshot을 검증하고 개별 판정·군집·전체 근거를 `sanity_vwp_data`에 저장합니다.
OA에 전달할 압축 번들은 `sanity_vwp_to_oa`에 등록합니다.
SQLite·OA 파일 복사·직접 통신은 사용하지 않습니다.

설치·DB 구성·공통 모듈 배포는 [루트 안내](../README.md)를 따릅니다.
VWP 계정의 `SANITY_VWP_DSN`과 공통 `SANITY_DB_SCHEMA`가 필요하며 OA 계정은 필요하지 않습니다.

위 설명과 아래 분석 명령은 `SANITY_DB_ACCESS=postgresql`을 명시한 직접 접속 검증 모드입니다.
[공개용 접속 안내](../dbaccess.example.md)의 DBaaS HTTP는 **임시 읽기 전용**이며 정식 전체 접근 방식은 별도 제공 예정입니다.
`sanity_dbaas.py check --resources`는 Bearer/CA 기반 읽기 전용 접속을 확인합니다.
코드·설정 기본값은 그대로이며 HTTP 모드의 분석 저장은 기존처럼 `DBAAS_SQL_CONTRACT_REQUIRED`로 중단합니다.
이 API를 쓰기용으로 확장하거나 직접 접속으로 자동 우회하지 않습니다.

```bash
.venv/bin/python vwp-bridge/main.py analyze --input /tmp/sanity-source/run.json --out /tmp/sanity-receipt
.venv/bin/python vwp-bridge/main.py respond --request-id REQUEST_UUID --approve-export
.venv/bin/python vwp-bridge/main.py respond --request-id REQUEST_UUID --reject
```

`analyze --out`은 선택 사항이며 `receipt.json`만 생성합니다.
분석 전후 파일 크기·해시·상대 경로와 실제 파일 목록을 검사합니다.
누락·미지원·형식 오류도 예상 단위에 남기며 불완전 결과를 DB에 등록합니다.
종료 코드 0은 명령 성공이지 sanity PASS나 검토 완료가 아닙니다.

## 판정과 동일성

- `numeric_csv_v1`: 고정 헤더 `object,check_point,mode,code,severity,observed,unit`,
  마지막 `# END rows=N`. 물리 줄마다 한 CSV 행을 판정합니다.
  단위·모드·유한 수치·정확한 Decimal 임계값과 건수를 검사합니다.
- `summary_json_v1`: `format_version=1`, `complete=true`, `total`, `results` 검증.
  16MiB 상한이며 빈 결과·필수 필드 누락은 미완료입니다. JSON Pointer와 문서 줄 범위를 기록합니다.
- 임계값은 정수 또는 정확한 십진 문자열입니다. 소수 JSON 숫자·NaN·무한대는 규칙 오류입니다.
- FAIL/UNKNOWN을 실행·unit·code·severity·rule_version·선언된 `identity_fields`로 집계합니다.
  필드 누락이나 잘못된 타입은 각각 `UNDECIDED`로 격리합니다. Jira를 참조하지 않습니다.
- PASS도 포함한 모든 기록에 판정·위치·원문 SHA-256을 저장합니다. 대표 예시는 그룹당 최초 3개,
  원문은 2,048문자까지입니다. 샘플 생략이 전체 판정 생략을 뜻하지 않습니다.

## 영속성과 복구

분석 ID는 입력·목록·파일·생산자 버전에서 결정합니다. 같은 분석의 동시 실행은 advisory lock으로 차단합니다.
최대 256행 또는 약 4MiB마다 배치를 flush하고 RUN에 checkpoint를 남깁니다.
개별 큰 행은 이 배치 목표보다 클 수 있습니다. 최종 그룹 목록은 여전히 메모리에 있으므로
대규모 고유 군집/초대형 행의 엄격한 메모리 제한을 보장하지 않습니다.

재시작은 전체 입력을 다시 계산하고 기존 불변 행과 대조합니다. 누락 없는 전체 근거와 집계 확인 후,
RUN을 SEALED로 바꾸고 번들을 등록하는 작업을 같은 트랜잭션으로 확정합니다.
봉인된 분석은 변경/자동 수리하지 않습니다. 손상이 있으면 `EVIDENCE_CONFLICT`로 중단하며
출력 폴더를 바꾸는 것으로 동일 DB 분석의 충돌을 우회하지 않습니다.

## 추가 근거 반출

OA가 생성한 특정 요청 UUID에 대해서만 `respond`를 실행합니다.
`--approve-export` 또는 `--reject` 중 하나를 반드시 지정합니다.
유효 기간, 번들·unit·원본 파일·근거 해시를 검사하고 요청한 모든 기록이 있어야 FULFILLED입니다.
최종 응답 등록과 요청 수신 확인은 원자적으로 처리합니다. 거부/만료 응답은 덮어쓰지 않습니다.

실제 사내 파서·반출 승인 시스템·내부 AI 연동은 미구현입니다.
분석 요약에는 대표 원문이 포함되므로 해당 내용과 private DB 원문 보관 정책도 먼저 확인해야 합니다.
원본 snapshot은 현장 보존 정책에 따라 별도로 유지합니다.

검증: 루트에서 DB 테스트 설정 후 `.venv/bin/python run_tests.py --require-db`.
