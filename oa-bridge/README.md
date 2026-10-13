# OA bridge — 검토 대시보드

OA는 sanity_vwp_to_oa에서만 결과를 수신하고 검토·이력·불변 보고서는 sanity_oa_data에 저장합니다.
VWP private 테이블과 파일에는 접근하지 않습니다. Jira 조회·게시·시뮬레이터는 폐지했습니다.
React·shadcn UI가 검토 화면이고 실제 운영 AI는 NOT_CONFIGURED입니다.
공통 설치·배포와 DB 신청은 [루트 안내](../README.md)를 따릅니다.

## 접속 모드와 실행

기본 SANITY_DB_ACCESS=dbaas_http는 임시 읽기 전용이며 정식 전체 접근 방식은 사용자 제공 대기 중입니다.
세션에서 읽기 전용 안내를 받을 수 있지만 업무 DB 저장·조회 어댑터는 아직 준비되지 않았습니다.
수신·검토 저장은 DBAAS_SQL_CONTRACT_REQUIRED로 중단하며 SQLite나 직접 PostgreSQL로 우회하지 않습니다.
직접 저장 모드는 승인된 환경/로컬 테스트에서 postgresql을 명시하고 SANITY_OA_DSN과
SANITY_DB_SCHEMA를 설정해야 합니다. VWP 계정은 OA에 필요하지 않습니다.

루트 private .env에 SANITY_DASHBOARD_TOKEN(최소 32자)과 SANITY_DASHBOARD_REVIEWER를 설정합니다.
토큰은 브라우저 메모리에만 유지하고 localStorage·URL·로그·VITE_*·소스에 넣지 않습니다.
검토자 이름은 서버가 결정하며 UI에서 바꿀 수 없습니다.

```bash
# 저장소 루트에서 Python 의존성 설치 후
cd oa-bridge/dashboard
npm ci
npm run build
cd ../..
.venv/bin/python oa-bridge/dashboard_api.py
```

http://127.0.0.1:8000 에서 dist와 API가 같은 origin으로 제공됩니다.
단일 사용자 로컬 prototype이므로 외부 bind를 금지합니다. SSO/RBAC·TLS proxy·운영 인증은 미구현입니다.
FastAPI 엔드포인트를 별도 공개 서비스로 노출하는 구성은 지원 범위가 아닙니다.
프런트엔드 개발은 API를 실행한 상태에서 별도 터미널의 dashboard 폴더에서 npm run dev를 실행합니다.
개발 서버는 127.0.0.1:5173이며 /api를 8000으로 proxy합니다.

## 화면과 인증 API

모든 업무 데이터 조회·변경은 Bearer 인증이 필요합니다. health와 정적 UI는 업무 데이터가 아닙니다.
브라우저가 DB 자격증명을 받거나 VWP bridge에 직접 요청하지 않습니다.

| 동작 | API |
|---|---|
| 검토자·접속 모드·쓰기 가능 설정 | GET /api/session |
| 번들 목록 | GET /api/bundles |
| 다음 메시지 한 건 수동 수신 | POST /api/receive |
| 단위 목록·coverage·최근 50개 이력 | GET /api/bundles/{bundle_id} |
| 단위의 문제 그룹 | GET /api/bundles/{bundle_id}/units/{unit_id}/groups |
| 사람 판정 | POST /api/bundles/{bundle_id}/reviews |
| 불변 보고서 다운로드 | GET /api/bundles/{bundle_id}/report |
| 추가 근거 요청·목록 | POST .../{bundle_id}/evidence, GET .../{bundle_id}/requests |
| 응답 수신·미리보기 | POST /api/evidence/{request_id}/receive, GET /api/evidence/{request_id} |

번들·단위·그룹·요청은 페이지로 조회하며 한 페이지에 없는 항목도 전체 coverage에 포함합니다.
이력은 최근 50개와 전체 개정 수를 보여줍니다. 세션의 writable은 설정 모드 표시이며 실제 DB 연결·권한은
데이터 요청 시 검사합니다. 브라우저가 정상 응답했다고 DB 작업이 승인됐다고 해석하지 않습니다.

명시적으로 선택한 UI demo만 합성 메모리 데이터를 사용합니다. 새로고침하면 초기화되며 실제 DB에 저장하지 않습니다.
API 오류·인증 실패·DB 연결 실패가 발생해도 자동으로 demo로 전환하지 않습니다.
demo의 판정이나 화면 문구를 실제 AI 검토 결과로 표시하지 않습니다.

## 수신·사람 검토·보고서

수신은 FOR UPDATE SKIP LOCKED로 한 행을 짧게 점유하고 gzip 크기·SHA-256·내용 ID·스키마·
전체 예상 단위·집계·근거 파일 연결을 검사합니다. 수신본·전체 단위·개정 0·보고서와 공유 RECEIVED를
같은 트랜잭션으로 저장합니다. token 변경·만료 시 완료 트랜잭션 전체가 rollback됩니다.
수동 수신은 한 건만 처리하고 기본 lease는 300초입니다. 같은 번들은 기존 이력을 유지하며 새 번들은 승인을 상속하지 않습니다.

사람 판정 본문에는 unit_id, conclusion(PASS/FAIL), reason, request_id(UUID), expected_revision이 필요합니다.
검토자는 서버 설정에서 가져옵니다. 번들 잠금 아래 기대 개정을 검사하여 다른 변경이 먼저 저장되었으면
REVISION_CONFLICT를 반환합니다. 최신 상태를 읽고 새 판단을 제출해야 합니다.
동일 요청 재시도는 같은 UUID·내용을 사용하고 새 판단에는 새 UUID를 사용합니다.
동일 UUID의 다른 내용은 거부하며 A→B→A도 별도 개정으로 보존합니다.

blocker를 승인으로 무시하거나 알려진 FAIL을 PASS로 바꿀 수 없습니다.
모든 검토가 끝나도 sanity 결과는 FAIL일 수 있습니다. 대표 예시가 전체 개별 판정을 대신하지 않습니다.
REVIEW_EVENT·현재 개정·REPORT를 원자적으로 저장하고 과거 보고서와 이벤트는 덮어쓰지 않습니다.
조회·보고서 다운로드는 읽기 전용 트랜잭션에서 저장된 보고서와 재렌더링 바이트·해시를 확인합니다.

CLI는 승인된 직접 DB 검증 환경의 보조 도구입니다. CLI 검토자 이름 인자는 대시보드 인증을 대신하지 않습니다.

```bash
.venv/bin/python oa-bridge/main.py receive --message-id BUNDLE_ID --brief
.venv/bin/python oa-bridge/main.py status --bundle-id BUNDLE_ID --brief
.venv/bin/python oa-bridge/main.py report --bundle-id BUNDLE_ID --out review.md
.venv/bin/python oa-bridge/main.py review --bundle-id BUNDLE_ID --unit-id u-numeric --request-id REQUEST_UUID --reviewer NAME --conclusion FAIL --reason '전체 근거 확인' --brief
```

ID 없는 receive는 다음 작업 한 건을 처리합니다. NO_MESSAGE는 대기 작업이 없거나 다른 작업자가 처리 중이라는 뜻입니다.
--state, --manifest, --jira-context는 지원하지 않습니다.
폐지된 publish는 --simulate·--dry-run을 포함해 항상 JIRA_DISABLED로 즉시 실패하며 게시 작업을 만들지 않습니다.

## 추가 근거 요청

```bash
.venv/bin/python oa-bridge/main.py request-evidence --bundle-id BUNDLE_ID --unit-id u-numeric --record-id RECORD_ID --request-id REQUEST_UUID --reason '전체 원문 확인'
.venv/bin/python oa-bridge/main.py receive-evidence --request-id REQUEST_UUID --out evidence.json
```

요청은 sanity_oa_to_vwp, 응답은 sanity_vwp_to_oa만 사용합니다.
record ID는 단위별 1~100개이며 TTL 기본 3,600초·최대 86,400초입니다.
임의 경로·명령·SQL을 요청하지 않습니다. VWP가 명시적으로 반출 승인하거나 거부해야 합니다.
FULFILLED/REJECTED/EXPIRED가 최종 결과이고 일부만 성공으로 내보내지 않습니다.
응답은 요청한 ID 집합·단위/파일 연결·원문 해시를 확인한 뒤 수신합니다.
미리보기는 기록별 원문 일부와 잘림 여부를 표시하며 근거 수신은 기존 판정을 자동 변경하지 않습니다.

## 검증

브라우저 테스트에는 Playwright용 Chromium이 설치되어 있어야 합니다.
의존성·브라우저 설치는 승인된 개발 환경에서 준비하며 사내 DB 접근을 대신하지 않습니다.

```bash
# 저장소 루트, 별도 테스트 DB와 제한된 계정 설정 후
.venv/bin/python run_tests.py --require-db
# dashboard 폴더에서 프런트엔드 빌드 및 브라우저 테스트
npm run build
npm run test:e2e
```

실제 실행 환경·성공 범위·누락 사항은 [REVIEW.md](../REVIEW.md)에 기록합니다.
실제 사내 DB·report·운영 AI·다중 사용자 인증 검증을 로컬 테스트로 대신하지 않습니다.
