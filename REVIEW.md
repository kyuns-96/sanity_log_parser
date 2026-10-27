# 프로토타입 구현 검토 기록

## 2026-10-27 VWP 제출 토큰 제거·OA OWNER 범위 확정

- 사용자 결정에 맞춰 새 기본 예제를 CLI schema 3·bridge schema 4의 `submission_auth=internal_network`로 바꿨다. CLI는 제출 토큰 환경변수를 읽거나 Authorization을 전송하지 않으며 VWP/bridge의 회사 신원·권한·승인 hook을 호출하지 않는다. 실제 소켓 출발 IP/CIDR·직접 TLS·Host를 검사하고 forwarded 헤더는 신뢰하지 않는다. 기존 Bearer/폴더 설정의 보호와 테스트는 호환 경로로 유지하며 자동 fallback하지 않는다.
- 제출 OS 계정은 `UnverifiedSubmitter`와 예약 namespace의 미확인 표시로 기록한다. 회사 계정·OWNER로 매핑하지 않으며 기존 제출 목록도 `submitted_by=null`·`submitter_verified=false`로 구분한다. 사용자 입력의 confirmed/approval은 미확정으로 정규화한다. 원본 DB/전체 hash·임시 파싱·재시작 복구는 유지하고, 미확인 자료가 검증된 SOURCE_RUN 실행 키를 선점하지 않게 했다.
- OA는 기존 WS_SUBMISSION/WS_SOURCE_PART에 전체 자료를 받고 `AWAITING_CONFIRMATION`으로 둔다. 현재 SSO OWNER의 명시적인 범위 확인 전에는 WS_REPORT/WS_VERSION을 공개하지 않는다. 새 workbench의 **VWP 제출 확인**에서 전체 범위·누락·제외·실행/도구 조건을 페이지로 확인하고 확정/반려한다. TOP은 조회만 가능하며 현재 계정/권한·변경 요청 보호를 재검사한다. 확정자·scope/manifest/content hash·개정·시각·사유·요청 ID를 보존한다. 같은 요청 재시도·부분 공개 rollback·늦은 대기 receipt를 처리하고 기존 PoR·사람 판정/분류는 변경하지 않는다.
- PostgreSQL 16.15의 토큰 없는 접수/기존 제출·HTTPS·원본·workbench/SSO 전용 **104/104**, PostgreSQL 12.1의 새 접수 전용 **14/14**, 최종 PostgreSQL 16.15 **quick 237/237**이 실패·오류·skip 0으로 통과했다. 마지막 제출자 표시 보정은 최종 quick에서 재검증했다. 저장 probe는 **100 records/10 groups**이며 전수/10만 건·실제 회사 데이터/모델 시험은 실행하지 않았다. SQL·포털 계획은 변경하지 않았다.
- 실제 독립 DB/SSO fixture와 빌드된 화면으로 브라우저 **7개 시나리오**를 확인했다. 첫 보고서가 없는 상태의 접수 확인, 체크/사유 필수, 서버 확정 응답 유실 후 동일 요청 재시도, TOP 조회 전용, 390px 모바일 반려/가로 넘침 없음, 53개 범위 페이지/누락 표시, 다른 창의 선행 결정을 덮어쓰지 않는 동작과 uncaught error 없음이다. 각 증거의 시나리오 이름·스크린샷을 보존했다.
- production bridge 이미지의 미구현 회사 authority를 그대로 둔 상태에서 **토큰 없는 실제 TLS 업로드 → UID 10001/read-only root에서 DB 파싱/임시 정리 → OA OWNER 확정 → 컨테이너 재생성 후 OA_STORED/READY 조회**의 4개 시나리오를 확인했다. 원본용 영속 volume과 개인 토큰 없이 성공했다. bridge/OA production 이미지 및 TypeScript/프런트엔드 빌드가 통과했다. OA 이미지의 첫 빌드는 검증용 node_modules symlink가 build context에 들어가 실패했고, 그 로컬 symlink만 제거해 재빌드했다. Dockerfile/검사 조건을 완화하지 않았다.
- 초기 11개 전용 시험과 이후 추가한 보호/rollback 검증 결과를 보존했다. 모든 DB fixture와 검증 컨테이너는 종료했다. 증거는 `/tmp/sanity-tokenless-20261027/`, 최종 quick은 `/tmp/sanity-mock-pg-7qyr3z5e/summary.json`이다. 기존 UI 작업은 별도 worktree에서 보존했고 사내 DB/서비스에는 접속하거나 배포하지 않았다.
- [현재 HTTPS 안내](vwp/HTTPS_SUBMISSION.md), [정책](vwp/HTTPS_DECISIONS.md), VWP/bridge/OA 인계와 공통 가이드를 실제 구현에 맞췄다. **OA receiver/API/UI를 먼저 갱신한 뒤** 새 CLI/bridge 설정을 적용한다. 기존 010 이후 추가 SQL/계정/GRANT는 없으며 실제 회사 provider/parser·SSO/OWNER·DB/TLS·출발 IP·모델 판단 품질은 사내 연결/검증 대상이다.

## 2026-10-26 원본 report DB 보관·임시 파싱 구현

- 사용자 결정으로 HTTPS 원본의 영구 보관을 기존 VWP private DB로 옮겼다. `RAW_SUBMISSION/RAW_FILE/RAW_CHUNK`에 제출 명세·파일 순서·최대 1MiB 원본 byte 조각을 저장한다. 전체 파일 크기/SHA256와 모든 파일을 확인해야 저장 완료이며 미완성 입력은 분석하지 않는다. 같은 UUID 충돌·내용 조각 중복 제거·전송 중단 후 재전송을 검증했다. 이전 항목의 bridge 영속 archive 설명은 schema 1/2 호환 경로의 기록이다.
- DB 상태에서 receipt를 만들고 worker가 파싱 중에만 파일을 복원한다. 정상 종료·파서 예외의 scratch 정리, 중단 후 재처리, 컨테이너 재생성 후 원본/`OA_STORED` 조회, 공간 부족 시 원본 보존을 확인했다. 기존 archive의 UUID·원문·원본 파일을 보존하는 명시적 import CLI도 제공한다. 강제 종료 때 남는 scratch의 정리는 OS/컨테이너 수명 주기로 관리한다.
- `010_raw_sources.sql`은 009 이후의 DBA 전용 증분이며 새 테이블/계정/GRANT/DELETE나 인증 메시지는 추가하지 않는다. 기존 form/원본 SQL·55단계 `plan.json`은 변경하지 않았다. 선택형 `raw-plan.json`에 단계 56~59를 추가했고 59단계 전체와 원본 SQL의 catalog 일치·GUI 제약 이름·테이블 OID·ACL 보존 및 migration 재적용/드리프트 rollback을 검증했다.
- PostgreSQL **16.15**에서 제출/HTTPS/원본/DB/포털/SSO 전용 **107/107**, 이후 추가한 포털 59단계 전체 재현 **1/1**을 통과했다. PostgreSQL **12.1**에서도 원본/010/포털 전용 **20/20**을 통과했다. 모두 실패·오류·skip 0이며 서로 겹치는 시험을 합산해 고유 시나리오 수로 표시하지 않는다. 원본 시험에는 **65MiB+6byte 파일의 66개 조각**과 전체 hash 대조가 포함된다.
- 최종 비동기 API의 DB 파일 조회를 thread pool로 옮긴 뒤 PostgreSQL 16.15 **quick 230/230**, 실패·오류·skip 0을 확인했다. 합성 저장 probe는 **100 records/10 groups**다. 전수 프로필·무관한 10만 건·실제 모델/회사 자료 검사는 실행하지 않았다. 모든 fixture는 socket/SCRAM 전용이며 종료했고 PostgreSQL 12.1 컨테이너도 제거했다.
- DB 보관 Docker 검증 **7개 시나리오**에서 UID 10001·읽기 전용 root·원본 volume 없음·DB 업로드/파싱/OA 수신·컨테이너 재생성·회사 인증 미설정 차단을 확인했다. worker tmpfs는 중복 mount 없이 512MiB 하나다. 마지막 API 수정 후 production 이미지 `sanity-vwp-bridge:db-originals-final-20261026`을 다시 빌드하고 네트워크 없는 컨테이너에서 진입점 import와 API 소스 hash 일치를 확인했다. 임시 합성 검증기는 운영 factory에 포함하지 않는다.
- 첫 원본 전용 시험의 SQL 포맷 중괄호 오류 1건은 신규 시험을 매개변수 SQL로 고쳤다. 첫 quick의 기존 migration 파일 허용 목록 실패 1건은 승인된 010만 정확한 집합에 추가했다. 이전 해시·입력·기대값·권한 검사는 유지하며 첫 실패 로그를 보존했다.
- 사용자가 재검토한 수동 개인 토큰 발급은 필수 요구로 추진하지 않는다. 이번 저장 변경은 익명 개발 접수·OA 확정 연결이나 토큰 발급 기능의 완료를 뜻하지 않으며, 미설정 인증·기존 SSO/RBAC·OWNER 범위 보호는 유지한다. 실제 회사 인증/범위·parser·모델 판단 품질과 운영 적용은 별도 연결/검증 대상이다.
- 적용·임시 공간·이관 안내는 [DB 원본 보관](vwp/DB_ORIGINALS.md), 현재 전송 안내는 [HTTPS 제출](vwp/HTTPS_SUBMISSION.md)을 따른다. 증거는 `/tmp/sanity-db-original-final-20261026-zidojuh2/`, 최종 quick은 `/tmp/sanity-mock-pg-tjoqgit4/summary.json`에 보존한다. 사내 운영 DB/DDL·원문·서비스는 변경하지 않았다.

## 2026-10-26 HTTPS 제출 인증·범위 정책 확정

- 사용자 선택은 OA SSO 본인 제출 토큰 발급/폐기, 현재 블록 OWNER의 직접 예상 범위 선언/확정, VWP → bridge HTTPS만 허용이다. [결정 기록](vwp/HTTPS_DECISIONS.md)에 반영했다. 초기 운영 기본값은 제출 토큰 8시간/동시 5개·원본 자동 삭제 없음이며 회사 강제 규정이 확인되면 별도 검토한다.
- VWP → OA 통로를 추가하지 않고 기존 DB 경계로 인증/권한·범위 확인을 요청한다. 기존 DB CHECK가 새 메시지를 허용하지 않으므로 별도 후속 migration·포털 단계가 필요하다. 기존 메시지 재해석·private 테이블 직접 읽기·공유 로그인 전환으로 우회하지 않는다. 아직 해당 SQL/메시지/발급 UI·범위 확정은 미구현이다.
- 이번 정책 반영은 문서/인계 지침 변경이며 실행 코드를 변경하지 않았다. 기존 전송 검증 222건을 이 후속 인증 기능의 검증으로 보고하지 않는다. 미구현 보호를 유지하며 신규 공통 코드는 Codex가 담당한다.

## 2026-10-26 공유 폴더 없는 VWP → bridge HTTPS 제출

- 사용자 확인으로 서로 다른 VWP/bridge 서버 사이의 공유 폴더 전제를 제거하고 명시적 schema 2 HTTPS 설정·제출 API·표준 라이브러리 CLI 전송을 추가했다. 원본은 bridge 전용 service 소유 archive에 보관하며 기존 파서·원천 ID·V2 분할/DB/OA 수신·승인/RBAC 계약을 유지한다. 새 테이블/DDL은 없다. schema 1 폴더 제출은 호환 경로로 남고 자동 전환하지 않는다.
- 사내 제출 인증 수단은 미정이다. `authenticate_bearer`·`resolve_actor` 연결 지점을 제공하되 `SUBMISSION_AUTH_NOT_CONFIGURED`를 유지한다. 테스트의 모의 검증기는 명시적으로 주입하며 운영 factory가 선택하지 않는다. OA 모델 JWT를 제출 인증으로 재사용하지 않는다.
- 전용 제출/DB/서버 분리/연동 계약 **86건**이 실패/오류/skip 0으로 통과했다. 이후 HTTP 오류 코드 보존 검사를 추가해 HTTPS 전용 **21건**을 최종 재검증했다. 최종 `quick`은 **222/222, 실패/오류/skip 0**이다. 별도 PostgreSQL **16.15**, UNIX socket/SCRAM fixture만 사용했고 종료했다. 저장 probe는 **100 records/10 groups**이며 전수·10만 건·운영 성능/AI 판단 검증을 실행한 것이 아니다.
- 실제 TLS 업로드 → 원본 hash/byte 대조 → source worker → 제한 계정 DB 큐 → OA 저장 → HTTPS의 검증된 `OA_STORED` 응답을 확인했다. 전송 중단·commit 응답 유실·파일/명세 충돌·타인 조회·권한/자격증명 폐기·부분 분석 후 재시작·불완전/잘린/압축 입력·인증 미설정 차단을 시험했다. 첫 quick에서 분석 시작 상태 파일이 이전 접수 상태에 머문 실패 1건을 발견해 실제 `PARSING` 전환 시 receipt를 작성하도록 수정했다. 기존 검사/기대값은 완화하지 않았다.
- production Dockerfile로 `sanity-vwp-bridge:https-submit-20261026`을 빌드했다. Compose 설정, UID 10001/읽기 전용 root에서 업로드·hash 검증·commit/회사 인증 차단, 컨테이너 재생성 후 원본·상태 보존, 실제 production TLS 진입점의 미설정 인증 503, 익명 401, DB 미설정 worker 차단의 **6개 시나리오**를 통과했다. 동일 부모의 단일 bind mount로 staging→archive atomic rename을 검증했고 컨테이너/네트워크를 정리했다. 초기 외부 검증 스크립트의 import 경로 오류는 스크립트에서 수정했으며 실패 로그를 보존했다.
- 설정·실행·회사 연결 지점은 [HTTPS 제출 안내](vwp/HTTPS_SUBMISSION.md)에 정리했다. 실제 사내 인증 발급/검증·인증서·네트워크·report 경로/완료 의미·범위 승인·parser 정확성·모델 판단 품질은 사내 연결/검증 대상으로 남는다. 결과와 초기 실패/수정 로그는 `/tmp/sanity-https-final-20261026-z1h2jmam/`, 최종 quick 결과는 `/tmp/sanity-mock-pg-0a4motgi/summary.json`에 보존한다.

## 2026-10-19 PR #8 Docker 구성과 제출 기능 통합 검증

- [PR #8](https://github.com/kyuns-96/sanity_log_parser/pull/8)의 고정 head `7c112387b2e1fcd8be076e6594c1f1b0dd616460`을 검토했다. VWP-bridge의 Docker/Compose·설정 예제·문서 8개 파일이며 기존 업무 코드/SQL/권한을 변경하지 않는다. 별도 worktree에 기존 제출 기능과 문서 53개를 함께 반영해 검증했다.
- DB 설정·parser·연동 계약·서버 분리·mock runner·SQL 불변성 **65개**, 별도 PostgreSQL의 제출 계약/통합 **30개**, 총 **95개 통과, 실패/오류/skip 0**이다. DB 시험은 새 socket/SCRAM fixture만 사용했고 종료했다.
- 결합한 소스로 Docker 이미지를 실제 빌드했다. Python 3.10.22와 VWP 전용 고정 의존성에서 기존 CLI·회사 parser CLI·새 `submission_worker`의 세 진입점이 로딩됐다. 네트워크를 차단한 합성 컨테이너에서 UID/GID 10001·읽기 전용 root/CA/snapshot·capability 제거·no-new-privileges·특수문자 환경변수 보존·receipt 출력 마운트를 확인했다. Compose의 CA/분석/worker 조합 8개를 검사했고 테스트 컨테이너는 정리했다.
- Compose 기본 worker는 기존 legacy 처리용이다. 새 제출 spool/영속 보관 worker의 배포는 [제출 안내](vwp/SUBMISSION.md)의 설정·인증된 mount/ACL을 따른다. Docker 기본 실행은 사용법만 표시하고 SSO/CORPORATE 보호를 유지한다. 실제 회사 베이스·SDK·DB/TLS·모델 및 운영 배포를 검증한 결과가 아니다.
- 결과는 로컬 `artifacts/pr8-compose-validation.json`, `artifacts/pr8-submission-validation.json`, `artifacts/pr8-integration-validation.json`에 남긴다. OA Docker PR #7의 작업 파일과 내부 Q1 원문·비공개 설정·로컬 산출물은 이번 통합 커밋에 포함하지 않는다.

## 2026-10-16 사용자 제출 CLI·V2 source 전달·OA 검토 연결 구현

- 사용자 요청으로 `bin/sanity submit/status/retry`를 구현했다. 어느 작업 폴더에서든 release 실행기를 고정하며 검증된 계정·프로필/개정·연결 PRE·전체 예상/발견/누락/실행 미완료·승인 참조를 보여주고 명시적으로 제출 확인을 받는다. 사용자에게 DB 계정은 없고, 원본 bytes를 바꾸지 않는 로컬 snapshot과 인증된 VWP 내부 `.partial`→`.ready` 인계를 사용한다.
- `vwp_bridge.submission_worker`가 private 영속 원본·SUBMISSION/SOURCE_RUN/불변 outbox를 관리한다. source별 실제 tool/version으로 기존 checked parser를 호출해 전체 EVIDENCE를 보존하며 파싱을 AI 초기 그룹으로 확정하지 않는다. UUID/내용·실행/raw 충돌·권위 있는 실행 순서·PRE 재전달과 새 ECO/재검사를 구분한다. 원본 보관 직후/DB 접수 직후/부분 발행/DB 응답 직후의 중단과 공유 `.ready` 삭제 뒤에도 archive/DB에서 재개·receipt 재작성이 가능하다. 예상 밖 회사 parser 오류는 고정 코드로 거부하고 자동 재호출하지 않는다.
- SOURCE_MANIFEST/PART/COMMIT는 envelope 포함 압축 전/후 각각 4MiB·data part당 1,000건 이하로 전달한다. `oa.submission_receiver`가 현재 신원/담당·원천 ID·전체 조각/순서/건수/hash를 검증한 뒤 버전/report 공개와 역방향 SOURCE_RECEIPT를 같은 트랜잭션으로 확정한다. 원본 파일은 VWP에 남고 OA는 불변 header/전체 parts와 로그 페이지를 사용한다. 기존 두 단계 분류·그룹별 근거/판정·다음 사례 연결을 검증하며 PoR·기존 사람 판정을 수신으로 변경하지 않는다.
- `008_submissions.sql`은 기존 네 테이블·제한 로그인·legacy run/부모/scope 제약을 유지한 증분이다. 007까지 적용한 독립 PostgreSQL에서 적용·기존 데이터 보존·재적용 rollback을 검사했다. 기존 form/002/003은 변경하지 않았으며 운영 DDL/DB·서비스 재시작·기존 workspace 초기화는 수행하지 않았다.
- 기존 공통/DB/API/대규모 검토를 포함한 전체 회귀 **385개 통과, 실패/오류/skip 0**, legacy 정상/누락/잘림/미지원 시나리오와 10,000건 규모 probe도 통과했다. 이후 접수 복구·최종 미리보기/마운트 신원·parser 자동 재시도 금지 변경은 별도 최종 제출 회귀 **30개(계약 14·DB 통합 16), 실패/오류/skip 0**으로 재검증했다. 결과/로그 경로는 `artifacts/submission-regression-verification.json`, `artifacts/submission-targeted-verification.json`에 기록한다.
- 합성 POST report 10,000건(+PRE 4건)은 11 parts/총 5,262,556 bytes, POST 100,000건(+PRE 4건)은 101 parts/총 **102,742,988 bytes**, 가장 큰 메시지 **1,027,376 bytes**로 OA 저장/전체 근거·마지막 로그·PoR 보존을 통과했다. 각각 `artifacts/submission-demo-10000.json`, `artifacts/submission-demo-100000.json`이다. 100,000건 시험은 약 205초의 공유 개발 환경 관찰이며 운영 SLA가 아니다. 새 source 10만 건의 모델 추론·전체 그룹 판정/운영 메모리 상한을 검증했다고 주장하지 않는다. 모든 DB는 별도 socket/SCRAM 인스턴스였고 종료했다.
- 합성 도구의 설정 파일 경로도 credential-free snapshot으로 고정했다. dotenv 파일 읽기 자체를 거부하는 guard 아래 최종 10,000건 통과를 확인했다. TypeScript/production 빌드를 통과했으며 작은 source 상세 분기에서 전체 로그 페이지/검토 열기와 VWP 원본 보관을 안내한다. 기존 테마/검토 작업 영역은 유지했다. 새 branch의 실제 브라우저 시험은 수행하지 않았다.
- 실행·설정·회사 SDK 메서드/소유권은 [SUBMISSION.md](vwp/SUBMISSION.md)에 정리하고 명세·가이드·세 담당 지시문·DB/배포 인계를 갱신했다. VWP 입력/신원·승인, bridge 마운트/현재 담당·승인, OA의 SSO와 같은 영속 계정 매핑만 회사 파일에서 구현한다. 실제 회사 SDK·인프라/운영 DB·SSO 업무 연결·CORPORATE 모델 job은 미완료이며 기존 보호 상태를 유지한다. 실제 사내 report·회사 DB/모델은 사용하지 않았다.

## 2026-10-16 사용자 제출 명령·서버/DB 저장 경로 설계

- 사용자 요청으로 [제출 명령·저장 설계](vwp/SUBMISSION_DESIGN.md)와 요구사항 20절을 추가했다. `sanity submit/status/retry`의 VWP 터미널 호출·PATH/release 설치, 전체 예상 범위 미리보기/확인과 접수 번호·상태 의미를 정했다. 현재 이 명령과 접수 service는 미구현이라고 명시했다.
- VWP 사용자/bridge 사이의 인증·ACL 제출 파일시스템, `.partial`→`.ready` 인계, bridge 전용 영속 원본 보관소, 네 DB 테이블의 저장/반환 방향과 사용자별 receipt 경로를 정했다. 실제 회사 마운트·신원·계정이 확보됐다고 가정하지 않으며 사용자 DB 계정이나 OA 파일 복사로 우회하지 않는다.
- 파일 보관/DB commit 사이 중단 복구, 같은 UUID/내용 검증, OA의 4MiB/1,000건 이하 전체 로그 분할 staging/최종 반영/응답, 전달 완료와 검토 가능·검토 완료·PASS의 구분을 명시했다. 기존 PRE/ECO/실행·검토/PoR 정책을 유지하고 기본 업무 원본 자동 삭제는 두지 않았다.
- COMMON-02E에 설치/CLI·VWP 내부 접수/보관/receipt를 추가하고 protocol·배포·DB 인계·세 역할 지시문·README/workspace를 연결했다. 내부 AI의 회사 입력/parser/SDK/SSO 허용 범위를 넓히지 않았다.
- 문서 링크/anchor·코드 fence·공백·필수 계약과 Markdown만 변경한 범위를 검사했다. 결과는 `artifacts/submission-design-validation.json`이다. 이 변경에는 실행 코드·SQL·설정 예제·기존 테스트·UI 수정, 운영 DB/실제 report/모델 접근이나 배포가 없다. 후속 수용 기준은 시험 명세이며 실행 통과 결과가 아니다.

## 2026-10-16 사내 VWP Q1 기반 수집·버전 인계 설계 업데이트

- 사용자 요청으로 [분산 수집·PRE/POST 설계](vwp/SOURCE_COLLECTION_DESIGN.md)를 작성하고 요구사항 19절·protocol의 계획 V2·plan 인계·공통 가이드/작업 지도·세 역할 지시문을 맞췄다. 원천 정보는 비식별 Q1만 사용했다.
- 제출 collection과 report별 원천 버전/실행을 분리했다. VWP 내부 byte 동일 export·원본/사본 검증, 누락/예상 밖 파일 보존, GCA 수집 stage/design, 별도 source 메타데이터, 중앙 목록이 없는 경우의 승인된 예상 범위 선언을 정의했다. 스캔 자동 승인·REVISION 사용자명에 의한 권한 부여·임계값/제외 승인 추정은 금지했다.
- 버전/단위/실행/원문/분석/전송 요청의 식별 계층과 여러 POST가 공유하는 같은 PRE 참조, 다른 PC 재전달, 동일 실행의 원문 충돌, 같은 바이트 재검사, 대용량 collection 분할/부분 수신의 기준을 명시했다. 원천 실행 ID·순서 등 현장 미확정 사실과 COMMON-02A~D 공통 구현을 분리했다.
- 사내 AI에게 전달할 답변은 `question_from_internal/vwp-dev/A1.md`에 작성했다. 질문 Q1은 수정하지 않았다. 현재 schema 1·CollectionPlan·SQL·API/UI·테스트 코드와 SSO/CORPORATE/미승인 목록의 보호 동작은 변경하지 않았다. V2와 수용 시나리오는 목표 설계이며 구현/실환경 검증 완료로 보고하지 않는다.
- 문서의 로컬 파일/anchor·코드 fence·공백 및 diff를 검사했다. 결과는 `artifacts/q1-design-review-validation.json`이다. 문서 변경이므로 전체 DB/모델 회귀를 재실행하지 않았고 실제 사내 자료·DB·모델은 사용하지 않았다.

## 2026-10-16 불필요한 로컬 폴더 정리와 Git 인계

- 사용자 요청으로 빈 `examples/`, 이전 `demo-output*/`, 폐기한 서버 전달용 ZIP 생성물, 중복 프런트엔드 빌드, 임시 소스 백업 등 18개 디렉터리와 캐시 11곳을 정리했다. 현재 workspace·별도 QA 설정의 정적 파일, 비공개 설정·DB·검증 기록은 보존했다.
- `.gitignore`의 합성 snapshot 제외 경로를 `oa/dashboard/public/sample-data/`로 갱신했다. 경로 이동 중 누락된 내부 AI의 기존 검증·승인 순서와 SSO 보호 상태 설명도 복원했다. 회사 파일/메서드 허용 범위는 그대로다.
- 이전 전체 회귀 360개가 통과한 Python/SQL 106개 파일과 현재 파일이 동일함을 확인했다. 정리 후 서버 import/설정 경계·합성 snapshot 격리 점검 10개가 추가로 통과했다. 로컬·공개 workspace는 HTTP 200이며 실제 비공개 설정과 생성 데이터는 커밋에서 제외한다.

## 2026-10-16 Git 저장소의 실제 코드를 서버별로 분리

- 사용자 정정에 따라 실제 소스를 `vwp/`(수집), `vwp_bridge/`(parser·근거·모델·worker), `oa/`(API·인증·검토 저장·dashboard)로 이동했다. 공통 계약·DB·설정·순수 검증은 `common/`, 합성 report·외부 모델 mock·시험 진입점은 `devtools/`에 모았다. 이전 소스 디렉터리와 루트의 평면 모듈은 남기지 않았다. import·CLI·테스트 수집·합성 실행 도우미도 새 namespace를 사용한다.
- 회사 어댑터는 각 서버의 `integration/`에 분리하고 scoped `AGENTS.md`, 역할별 `INTERNAL_AI_TASK.md`, 통합 가이드·요구사항·DB 인계를 갱신했다. [서버별 소스 안내](DEPLOYMENT.md)에 실행 명령·의존성·구현 위치를 정리했다. 비공개 설정은 각 checkout의 저장소 루트 `.env`를 읽는 기존 계약을 유지한다. 기존 수정 허용 메서드·SSO/CORPORATE 보호·사람 확정·네 테이블 SQL은 유지했다.
- 서버별 worker와 추가 근거 요청/응답 처리를 분리했다. 옮긴 전송 함수 7개의 본문은 import 외에 동일하다. 새 회귀 5개는 VWP의 표준 라이브러리 실행, 공통/서버별 import 경계, VWP-bridge의 OA 미의존, 일반 OA 실행의 모델/mock 미로딩, 설정 루트를 검증한다. 회사 구현·비공개 fixture·설정은 mock source 수집에서 제외한다.
- 새 독립 PostgreSQL fixture에서 **회귀 360개 통과, 실패/오류/skip 0**을 확인했다(682.338초). 10,000/100,000건 parser·분류/그룹 근거·100,000개 고유 그룹·작업 재개·마지막 그룹 판정을 포함한다. 별도 complete/missing/malformed/unsupported 실행과 10,000건 DB 송수신 scale probe도 통과했다. 테스트 DB는 종료했으며 실제 사내 DB·report·외부 모델은 사용하지 않았다. 검증한 snapshot의 Python/SQL과 현재 소스가 동일하다.
- 실행 진입점 15개와 Python 3.10 문법 검사, TypeScript/production build를 통과했다. 이동한 프런트엔드 추적 파일 70개의 내용은 이전 HEAD와 동일하며 JS/CSS 자산 hash도 기존과 같다. 소스 구조 변경과 기존 UI 변경을 구분한다.
- 기존 공개 workspace는 새 소스 경로와 `oa/dashboard/dist`로 전환했다. DB 프로세스를 재시작하지 않았고 기존 1,130개 행·네 테이블·전체 내용 hash가 전후 동일하다. 공개 HTTPS workspace/JS/CSS는 200, 미인증 업무 API는 401이다. seed·DDL·판정 저장·외부 모델 호출은 하지 않았다.
- 상세 증거: `artifacts/server-code-layout-verification.json`, `artifacts/server-code-layout-db-summary.json`, `artifacts/layout-cli-smoke.json`, `artifacts/layout-workspace-preservation.json`, `artifacts/layout-public-smoke.json`. 실제 사내 SSO·SDK·원천 import·신규 workspace 운영 job 연결은 기존 COMMON 후속 작업이다.

## 2026-10-16 세 사내 AI·DB 인계와 배포 묶음 제안의 정정

- VWP·VWP-bridge·OA의 역할별 지시문과 DB 연결 인계를 작성했다. 수정 허용 메서드·공통 코드 보호·비식별 문의·`WAITING_FOR_CODEX` 규칙을 유지했다.
- 처음에는 전달용 ZIP 생성으로 해석했으나, 사용자는 **서버별 Git을 사용하며 실제 코드를 정리하라는 뜻**이라고 정정했다. 묶음 생성 도구·manifest·별도 배포 템플릿은 제거하고 실제 소스를 서버별 Python 패키지로 옮기는 작업으로 대체했다. 이전 묶음의 검증 결과는 현재 소스 구조의 검증으로 사용하지 않는다. 최신 디렉터리/진입점/설정은 [소스 안내](DEPLOYMENT.md)를 따른다.

## 2026-10-16 사내 AI 구현 인계와 추상 계약 정리

- 사용자 요청으로 [INTERNAL_AI_HANDOFF.md](INTERNAL_AI_HANDOFF.md)를 통합 진입 문서로 추가했다. SSO·예상 sanity 목록·item parser·1단계 분류·2단계 판정·원천 버전/담당 매핑을 INT-01~06으로 나누고 선행 정보·허용 메서드·입출력·수용 기준·실행 명령·인계 상태·복사 가능한 시작 지시문을 정리했다. 내부 AI의 구현 범위와 Codex의 COMMON 운영 연결을 구분했다.
- `common/sanity_integration_contracts.py`에 기존 dict 기반의 plan/context/case/분류 V2/판정 타입과 `SanityPlanProvider.get_plan` 추상화를 추가했다. `vwp/integration/corporate_plan.py`는 명시적으로 `PLAN_NOT_CONFIGURED`를 반환하며 자동 collector/DB/UI 연결이 없다. 회사 입력 wrapper의 허용 위치를 `INTERNAL_AI_GUIDE.md`에서 구체화하고 [PLAN_INTEGRATION.md](vwp/PLAN_INTEGRATION.md)에 기존 schema 1 대응·예상 누락 보존·미승인 목록과 실제 원천 버전 연결 경계를 작성했다.
- 기존 `ClusteringAdvisor`/`ReviewAdvisor`와 회사 두 SDK stub에 타입/계약 설명을 추가했다. 인자 순서·출력 키·기존 런타임 validator·V1 호환·DB schema는 변경하지 않았다. 새 회사 분류는 `require_rationales=True` 검증을 명시한다. `common/sanity_integration_examples.py`에 최초/사람 정정 사례 분류·선택 그룹 판정·미승인 plan의 작은 순수 합성 packet을 추가했다. 실회사 SDK나 DB를 import/호출하지 않는다.
- 현 review가 사람 사례 참조 없는 AI PASS/FAIL을 거부한다는 실제 제한, 회사 SDK만으로 새 workspace 운영 VWP job이 연결되지 않는 점, legacy `/api` SSO와 합성 `/workspace/api`의 차이를 명시했다. SSO 문서의 ‘관리 DDL 없음’을 현재 005 관리 레코드와 미구현 신원 매핑의 구분으로 정정했다. 직접 원본을 이용한 최초 AI 판정·사례 기본 활용 정책/선택형 기준 관계·대규모 모델 orchestration은 Codex 공통 후속 작업으로 남겼다.
- 새 계약 시험 14개는 실제 기존 validator로 전체 ID 배정·V2 근거 누락·다른 그룹 대표 ID·미지 사례 group·참조 합집합·AI의 사용자 승인 필드 삽입·최초 판정 DEFER·도구 FAIL과 독립 제안·선택 그룹/원본 hash·미승인/실패 목록을 검증한다. 공통 SSO 진입점 시험은 회사 구현이 영원히 미구현일 것을 전제하지 않도록 명시적 회사 모듈 테스트 더블을 주입했다. mock source 수집의 회사 plan/분류/판정 구현 제외도 확장 검증했다.
- 비공개 설정을 포함하지 않는 임시 코드 snapshot에서 새 계약 14개·인증 12개·파서 27개·판정 참고 3개·분류 참고 3개·근거 V2 7개·mock 수집 5개·SSO API 8개, 총 **79개 통과, skip 0**을 확인했다. 검증 내역과 로그는 `artifacts/internal-handoff-verification.json` 및 `artifacts/internal-handoff-checks/`에 있다. 실제 DB/모델 연결·운영 적용은 0회다. 추가/연관 Python 8개 파일은 Python 3.10 문법으로도 파싱했다(3.10 실런타임 시험과 구분).
- 기존 UI 소스 5개는 마지막 승인 화면의 hash와 같음을 확인했다. 프런트엔드 배포/운영 서버 재시작·DB/SSO/CORPORATE 보호 변경은 없다. 실제 사내 report/SDK 정확성·실제 계정/DB 연결·대규모 회사 모델 성능을 검증한 것으로 보고하지 않는다.

## 2026-10-16 검토 화면의 시각적 구조 재정리 — 사용자 정정 반영

- 사용자는 문장 수준을 낮추는 대신 처음 방문해도 UI를 한눈에 파악하도록 요청했다. 이전의 대화형 문구와 큰 안내 제목을 수정하고 **로그 분류 / 그룹별 판정 / 분류 근거 / 사용자 판정 / PASS·FAIL·보류** 등 업무 용어를 복원했다. 요구사항 18.11절과 사용 문서를 정정했다.
- 상단을 보고서 맥락·단계·진행률·주요 동작으로 압축하고 데스크톱에 **그룹 목록 / 선택 그룹의 로그 / 분류 근거 또는 판정**을 정렬했다. 그룹명·로그 수·도구 판정은 공통 헤더로 모으고 목록의 긴 근거 반복을 제거했다. 로그 미리보기는 원본 메시지를 우선 표시하며 원본·행 번호·접힌 전문을 유지한다. 2단계도 원문 로그를 계속 표시한다.
- 분류 확정 동작은 상단의 **분류 확정 · 판정 시작**으로 통일했다. 전체 그룹 수와 유효 초안 일괄 채택 범위를 함께 명시하며 근거 누락·재검토·미저장 보호를 유지한다. 상세 집계·대표 원문·AI 원안·사례·설정·이력은 필요할 때 펼친다. 초록색 테마, 드래그·키보드·터치, 페이지 선택, 사용자 판정과 AI 출처 계약을 보존했다.
- 새 배치의 세 영역이 데스크톱 첫 화면에서 나란히 보이고 첫 원본 로그가 900px 화면 안에 표시됨을 브라우저로 검증했다. 두 단계 실제 저장·보류·정정·완료·모바일 미저장 보호·TOP 조회 **4개**, 근거/원안·대표 로그 **5개**, 이동/재시도/충돌 **8개**, 저장된 실제 AI 결과 조회 **3개**, 기존 workspace/PoR/권한/모바일/판정/분류 **13개**, 총 **33개 통과**했다. 첫 workspace 프로세스는 10개 통과 후 종료 코드 143으로 중단되어 전체 13개를 재실행해 완료했다. 결과는 `artifacts/clear-review-browser-summary.json`과 `artifacts/clear-review-tests/`에 보존한다.
- TypeScript/production 빌드·관련 문서의 로컬 링크 55개·`git diff --check`를 확인했다. 이번 작업의 외부 모델 호출은 0회이며 API/SQL/분류·판정 저장 모듈은 수정하지 않았다. 10,000/100,000건 규모 시험을 이번 UI 정정에서 다시 실행한 것은 아니다.
- 공개 테스트 페이지는 프런트엔드 자산/index만 교체했다. 기존 OA row **1,126개**, 네 테이블 OID·스키마/내용 snapshot이 배포 전후 동일함을 확인했고 API/DB를 재시작하지 않았다(`artifacts/clear-review-deploy.json`). 공개 HTTPS의 1440/1024/390px에서 가로 넘침·브라우저 오류 없이 조회했고, 분류 개정 9·두 그룹의 검토 완료 근거·원본 hash가 유지됨을 확인했다. 1440×900px에서 세 작업 영역과 첫 로그가 화면 안에 표시되며 판정 단계에서도 로그와 결과 선택이 함께 보인다(`artifacts/clear-review-public-check.json`). 조회 중 검토 저장·외부 모델 호출은 0회다. 별도 worktree의 5173 서버도 HTTP 200을 유지한다. 아래 ‘첫 방문 안내’ 기록은 정정 전 구현의 이력이다.

## 2026-10-16 첫 방문 안내와 검토 화면 정리

- 기존 두 단계를 **그룹 확인 → PASS·FAIL 판정**으로 설명한다. 현재 할 일과 다음 행동을 큰 제목으로 안내하고, 이미 분류를 확정한 보고서에는 상단에 **판정 이어하기**를 제공한다. 기본 진행 정보는 전체 로그/그룹 수와 현재 단계의 확인 수다. 상세 상태 집계는 펼쳐서 조회한다.
- 선택 그룹의 **왜 같은 그룹인가요?**에서 현재 설명과 확인 상태를 먼저 보여 준다. 긴 설명은 실제 생략량과 함께 펼치며, 대표 원문·AI 원안·과거 사례·이력은 접었다. 병합 체크박스는 **합치기**를 누른 뒤 표시한다. 전체 메모·재사용 설정·AI 재실행·모델/버전 정보는 **설정과 이전 기록**에 보관한다.
- 판정 입력은 **통과 / 수정 필요 / 나중에 결정**과 이유·저장 버튼에 집중한다. 사내 AI 미연결과 현재 참고 엔진의 출처를 그대로 설명하며 분류 근거를 판정 사유로 복사하지 않는다. 보류는 완료로 세지 않고 모든 그룹을 마치면 목록으로 돌아가는 버튼을 제공한다.
- 보고서 목록의 중복된 선택/시작 양식을 없애고 행의 **검토 시작**으로 통일했다. 규칙·보고서 전체 판정·분석 상태는 **상세 열 보기**에서 조회한다. 초록색 토큰과 기존 사용자/버전/PoR·권한 범위를 유지하고, 화면 문구와 접근성 이름을 일치시켰다.
- 첫 검증에서 하단 고정 영역이 로그 손잡이를 가리는 문제를 발견해 고정을 제거했다. 다음 그룹 조회 중 이전 입력 양식이 잠깐 남아 빠른 입력이 다른 초기값과 섞일 수 있는 문제도 확인해, 로딩 표시와 새 그룹 맥락 수신 후 입력으로 수정했다. 실제 브라우저의 보류 저장/정정/완료 시나리오로 검증했다. 마지막 미확인 그룹에서 다시 다음 버튼을 눌러도 현재 로그 맥락과 수정 기능을 유지하며, 조회 중에는 기존 그룹의 편집을 막는다. 마우스 시험은 드롭 대상을 먼저 화면 안에 배치하고, 터치 시험은 가장자리 자동 스크롤을 계속 검증한다.
- 첫 방문·기본/상세 표시·두 단계 완료·PASS/FAIL/보류/정정·모바일 미저장 보호·TOP 조회의 신규 브라우저 **4개 통과**, 기존 그룹별 근거 **5개**, 마우스/터치/키보드·51건 이동·응답 유실/개정 충돌/취소 **8개**, 저장된 실제 AI 결과·출처·이력 조회 **3개**를 통과했다. 로그인/로그아웃·PoR 저장·버전/ECO·범위 권한·보고서/모바일 팝업·개별 판정·소규모 두 단계/재사용/무효화를 포함한 기존 workspace **13개**도 통과해 총 **33개 브라우저 시나리오**를 확인했다. 시험의 모든 AI 관련 조회는 저장 결과 또는 참고 엔진이며 이번 UI 작업에서 외부 모델을 추가 호출하지 않았다.
- QA 초기화가 로그인 완료보다 먼저 실행되던 새 테스트의 대기를 수정했고, 첫 실패와 고정 영역/좌표 문제의 기록은 `artifacts/easy-review-{guidance,drag}-*-run.json`에 남겼다. 확장한 workspace 첫 시험에서 모바일 팝업의 가로 넘침 2건을 발견했다. 화면 밖 표의 절대 위치 `sr-only` 열 제목이 원인이어서 일반 열 제목으로 바꾸고 13개 전체를 재검증했다. 첫 결과는 `artifacts/easy-review-workspace-first-run/`에 보존한다. 최종 결과는 dashboard `artifacts/{guidance,rationale,drag,codex,workspace}-browser-tests.json`과 화면 캡처에 있다. 실제 초등학생 사용성 시험을 수행한 것은 아니다.
- TypeScript/production 빌드·문서 경로·`git diff --check`를 확인했다. 기존 500kB bundle 안내는 남아 있다. API·SQL·모델/저장 모듈은 변경하지 않았고 10,000/100,000건의 분할/페이지 계약을 유지한다. 이 UI 작업에서는 대규모 재분류/모델 추론을 다시 수행하지 않았다.
- 공유 테스트 페이지는 자산과 index만 교체한다. API/DB를 재시작하거나 기존 검토/PoR을 초기화하지 않는다. 배포 전후 전체 행/테이블 OID·스키마 해시 비교는 `artifacts/easy-review-deploy.json`, 공개 HTTPS 1440/1024/390px의 화면/무변경 조회 검증은 `artifacts/easy-review-public-check.json`에 기록한다. 회사 연동·SSO 보호·내부 AI의 허용 범위는 그대로다.

## 2026-10-15 그룹별 분류 근거 V2 구현과 실제 모델 검증

- `common/sanity_workspace_rationale.py`와 불변 분류 event에 그룹별 본문·대표 원본 ID·참고 사례 group·최초 원안·사람 정정/채택·작성자/시각과 구성 바인딩을 추가했다. 같은 source/group/이름/전체 소속인 근거는 출처를 기록해 이어받고, 이동 양쪽·이름 변경·병합은 재검토, 새 그룹은 미작성으로 표시한다. 근거만 바꾸어도 새 분류 개정이며 새 그룹 판정에는 전체 분류 재확정이 필요하다.
- 그룹별 `SAVE`/`ACCEPT`와 개정 고정 상세/이력 API, 네 상태 집계·필터, 대표 로그 선택·수정, AI 원안/사례/이력의 펼침 조회를 구현했다. 전역 메모는 선택 입력이다. `CONFIRM`은 현재 전체 개정의 유효한 초안을 명시적으로 일괄 채택하고 확정하며 미작성/재검토/무효 참조는 차단한다. 2단계의 판정 근거는 별도로 보관한다.
- AI JSON V2는 기존 세 키와 `group_rationales`를 반환한다. 회사 SDK 인계 문서를 갱신했으며 공통 코드/DB/UI를 회사 AI가 임의 변경할 권한은 추가하지 않았다. SSO 보호와 `CORPORATE` 503은 유지한다.
- 1,000개 이상 근거는 같은 event 안의 독립 압축 bucket/index로 저장하고 공통 store가 원래 객체로 복원한다. 압축 전 128MiB를 검사하며 전체 내용을 생략하지 않는다. 원안 중복 저장과 변경 없는 본문의 복사를 줄이고, 최종 task 저장은 이미 검증한 입력을 다시 읽지 않는다. 새 물리 테이블·DDL은 없다.
- 공유 4GiB 환경의 초기 10만 고유 그룹/브라우저 시험에서 메모리 부족을 발견했다. 압축 저장과 workspace의 짧은 DB 처리 구간 직렬화로 동시 원본 디코딩을 제한했고, 오래된/자기 의존 사례는 records를 읽기 전에 제외한다. 실제 추론은 DB 처리 구간 밖이다. 브라우저 부하로 종료된 별도 worktree의 기존 5173 Vite는 동일 실행 인자로 복구했으며 그 worktree의 파일은 수정하지 않았다.
- **DB·API/모델 단위 50개 통과**, 10,000건/10,000개 고유 그룹과 사례 재사용·재개·페이지·실패/취소의 **규모 회귀 8개 통과**. 100,000건/100그룹의 근거/일괄 채택 시험도 통과했다. 별도 100,000건/100,000개 고유 그룹에서는 전체 분류 121.712초, workflow 206.009초, peak RSS 889,912KiB, metadata 7,016 bytes, 그룹 25개·로그 50건 및 마지막 그룹 PASS를 검증했다. 이 대규모 시험은 모델 호출 0회다.
- 새 근거 UI **5개**, 기존 마우스·터치·키보드/다중 선택·UUID·충돌/취소의 drag & drop **8개**, 기존 두 단계 분류·판정/다음 사례/모바일 회귀 **3개**를 실제 별도 PostgreSQL에서 통과했다. GCA/VC LP/SpyGlass의 10,000건 브라우저 흐름과 모바일도 통과했다. DB 처리 구간 직렬화 후 **100,000건 브라우저 1개도 3.9분에 통과**했다(전체 분류·마지막 원문·사람 PASS·새로고침). 검사 대장으로 돌아갈 때 최신 요약을 다시 조회하도록 하고, 재로드 후 검색 조건을 명시적으로 선택해 대상 report의 결과를 확인하도록 시험을 맞췄다.
- 합성 report 12건에 `gpt-6-luna`를 총 **2회** 호출했다. 첫 V2 응답은 구조 검증을 통과했지만 사람이 통합한 두 패턴을 같은 이름의 두 ID로 나누었다. 원래 결과를 이력에 남기고, 실제 소속과 통합 의도의 일관성을 명시한 **prompt V3**로 별도의 한 요청을 평가했다. 최종 결과는 **제약 설정 누락 8건 / 데이터 폭 불일치 4건**과 서로 다른 그룹별 근거·자기 원본 참조이며 모두 사람 미확인 초안이다. 입력 사례 1건의 사람 최종 근거를 사용했다. 모델 결과를 코드로 합치거나 실패/불확실 호출을 자동 재호출하지 않았다. V1/V2의 고정 receipt 검증도 유지하고 실제 저장 V2 receipt의 입력 hash를 다시 검증했다.
- V3 호출 전 존재한 불변 OA row **1,094개**의 hash와 원본 report/PoR이 호출 후 같음을 확인했다. 공개 서버 반영은 기존 row/테이블 OID를 그대로 보존했으며 합성 사용자/모델 조작은 새 event와 해당 head에만 기록했다. 구형 근거 미작성 event/모델 원문/판정은 변경하지 않는다.
- 공개 HTTPS에서 실제 모델 V3의 8/4건 두 그룹·각 근거·미확인 상태·빈 전체 메모로 확정 가능한 상태, 데스크톱/모바일·가로 넘침 없음·검사 대장 복귀를 확인했다. 이전 참고 엔진 작업 기록은 현재 AI 실행과 구분해 표시한다. 화면 조회 중 추가 모델 호출은 0회다.
- 실제 회사 자료·SSO/SDK·VWP 운영 job/전송·10만 건 모델 호출/2단계 LLM 판정은 검증하지 않았다. 대규모 수치는 한 공유 개발 환경의 합성 시험이며 운영 SLA가 아니다. 출력 schema는 [공식 Codex CLI 안내](https://developers.openai.com/codex/cli/reference/)의 `--output-schema`를 사용한다.


## 2026-10-15 그룹별 분류 근거 명세 갱신 — 구현 전

- 사용자 확인에 따라 [요구사항 18.10절](requirement.md#group-clustering-rationale)을 추가하고 17.4/18.3~18.5/18.7/18.9를 맞췄다.
  각 그룹의 AI 분류 설명·원본 record 참조·참고 사례, 사람의 채택/정정 근거·작성자/시각·이력을 현재 구성/개정에 연결한다.
  전체 분류 메모·자동 조작 문장·2단계 PASS/FAIL 이유가 그룹별 분류 근거를 대신하지 않도록 구분했다.
- 근거 미기록/미검토/확인/재검토 상태, 이동 양쪽 그룹·분리/병합/이름 변경·근거만 정정할 때의 처리,
  전체 분류 확정/2단계 저장의 서버 차단, 초안 저장과 명시적 채택 범위를 명시했다.
  최종 목표의 전체 메모는 선택 입력이며 현재 구현의 공통 사유 필수 동작과 구분한다.
- 사람 확정 사례를 다음 AI에 제공할 때 그룹별 최종 근거/원본 참조/정정 이력을 함께 전달하도록 정의했다.
  기존 근거 미기록 자료의 이력 보존·누락 표시·새 확정 전 보완, 대규모 페이지/참조 한도와 수용 시나리오도 추가했다.
- PROTOCOL·WORKSPACE·사내 clustering/review 인계에 동일한 목표와 현재 미구현 상태를 기록했다.
  기존 세 필드 adapter 출력은 여전히 현재 계약이며 문서만 보고 내부 AI가 키/서명/공통 코드·SQL·UI를 바꾸지 않도록 구분했다.
- 이번 작업은 문서 6개만 수정한다. 코드/SQL/기존 데이터/실행 중 서비스는 변경하지 않고 모델을 호출하지 않았다.
  문서 내용·로컬 링크/anchor 100개·`git diff --check`를 확인했다. 문서만 변경해 런타임/브라우저 테스트는 재실행하지 않았다.

## 2026-10-15 로그 drag & drop과 분류 검토 화면 정리

- 1단계의 로그 손잡이를 그룹에 끌어 기존 `MOVE` API로 소속을 저장한다. 선택 로그는 페이지 간 선택 전체,
  선택하지 않은 로그는 한 건만 이동하며 새 그룹에 놓으면 이름을 입력한다. 기존 선택 이동 버튼·그룹 병합·이름 변경도 유지했다.
  별도 라이브러리 없이 Pointer Events로 마우스/터치·가장자리 자동 스크롤, 키보드 Space/Enter·방향키·Escape를 제공한다.
- 화면은 보고서 맥락과 두 단계, 왼쪽 그룹 목록·오른쪽 로그·하단 전체 분류 확정을 중심으로 정리했다.
  블록/버전·PoR·항목 목록을 중복 표시하지 않고 검사 대장으로 돌아가면 다시 제공한다.
  설정·출처·원본 검색·이력·참고 엔진 재사용 옵션은 접고 초록색 토큰을 유지했다. 로그에 짧은 내용 미리보기를 추가했다.
- 드롭은 기대 개정·UUID·전체 ID 단일 배정을 그대로 검사한다. 같은 그룹/화면 밖/취소는 저장하지 않고,
  개정 충돌은 최신 구성을 조회한다. 새 그룹과 검색 중 이름 변경은 대상 이름을 필터에 표시하고 검색 지우기로 전체 목록에 돌아간다.
  개별 조작 사유를 비우면 수행 내역을 기록하며 전체 분류 확정에는 별도의 사람 검토 사유가 필요하다.
  원본/PoR·과거 event를 수정하지 않고, 이동 후 다시 분류를 확정해야 그룹 판정을 진행한다.
- 독립 합성 8003 DB의 report 8개·각 120건에서 **브라우저 8개 통과**(최종 35.8초).
  단일 이동·페이지 간 51건 이동·이름을 정해 분리/취소·키보드·390px 실제 터치 자동 스크롤·동시 개정 거부·
  저장 응답 유실 후 같은 UUID 재시도·TOP 읽기 전용을 확인했다. 매 이동의 120건 전체 누락/중복 없음과 원본/PoR 불변을 대조했다.
  `tools/seed_drag_qa.py`는 제한된 로컬 8003 합성 DB만 허용하고 기존 report 교체를 거부한다.
  결과는 dashboard의 `artifacts/drag-browser-tests.json`, `artifacts/workbench-drag-{desktop,mobile}.png`다.
- 저장된 실제 AI 결과의 조회/출처·수정 이력·TOP 화면 **3개 통과**(최종 7.8초).
  8002의 기존 10,000건 업무 흐름 **3개 통과**(3.0분): GCA의 51건 분리·병합·분류 확정·규칙 FAIL에 사람 PASS·TOP 재조회,
  VC LP/SpyGlass 전체 초안과 마지막 원문 검색, 모바일 미저장 이름/필드 변경의 단계 차단을 확인했다.
  대용량 저장은 전체 배정 검사와 재조회가 5초를 넘길 수 있어 통합 검증은 저장 창 닫힘을 기다리고 기대 대기를 30초로 설정했다.
  서버 timeout·검사·권한은 바꾸지 않았다. 이 시험은 참고 엔진이며 외부 AI를 호출하지 않았다.
- 별도 100,000건 보고서의 실제 DB/화면을 읽어 그룹 25개·로그 50건과 각각 다음 페이지, 원본 해시·PoR·분류 개정 불변을 확인했다.
  조회 검증이며 이번 작업에서 10만 건 모델 호출/재분류는 수행하지 않았다.
  결과 dashboard의 `artifacts/workbench-drag-100000-result.json`·`artifacts/workbench-drag-100000.png`.
- TypeScript/production 빌드·QA 도구 Python 구문·`git diff --check`를 통과했다. Vite의 기존 500kB chunk 안내는 남아 있다.
  공유 8001은 API 재시작 없이 자산과 index만 교체했고 **기존 1,108행**의 payload/state/version/시각 해시와 네 테이블/OID가 동일했다.
  HTTPS의 데스크톱/390px에서 두 그룹·로그 손잡이·모델 버튼·접힌 상세·초록색과 브라우저 오류 없음을 확인했다.
  공유 업무 데이터 변경/추가 모델 호출/DDL은 없고 QA 8002/8003은 종료했다.
  배포 `artifacts/log-drag-deploy.json`, 공유 조회 dashboard의 `artifacts/workbench-drag-public-result.json`.

## 2026-10-15 화면에서 실제 AI 분류 요청

- 사용자 요청에 따라 1단계의 **AI 분류 초안 생성** 버튼을 `gpt-6-luna` 실제 호출에 연결했다. 기존 참고 엔진 버튼과 구분하며
  명시적으로 활성화한 독립 합성 workspace의 `codex-mock-source/control/feedback`만 지원한다. TOP은 조회 전용이고 회사 SDK/SSO 차단은 유지한다.
- 전용 `POST /clustering/ai`는 기존 로그인·범위·Origin/CSRF와 기대 개정/UUID를 검사한다. 호출 전에 기존 `WS_CLUSTER.state`에 요청을 예약하고
  DB 쓰기 트랜잭션 밖에서 모델을 호출한다. 받은 결과도 먼저 저장하고 현재 개정/사례·전체 ID를 검증한 뒤 미확정 초안을 원자 적용한다.
  생성 중 수동 분류/그룹 판정·중복 AI 작업을 차단한다. 같은 UUID는 상태/결과를 반환하며 다시 호출하지 않는다.
  서버 중단의 불명확한 호출은 UNKNOWN이며 자동 재실행하지 않는다. 저장 실패 재시도는 보관한 receipt를 사용한다.
  공개 mock 예산은 전체 동시 1개·report별 UUID 20개이고 이전 UUID를 삭제하지 않는다. 회사 report/대용량 모델 연결과 구분한다.
- 모델 호출 없는 관련 순수/PG **21개**, 기존 두 단계 저장/참조 회귀 **12개**를 통과했다. 실패/오류/skip 0개다.
  실제 HTTP 모드 차단 유지, 새 opt-in 경로, 권한/CSRF/위조 입력, 중복/동시 요청, 사례 변경의 늦은 결과, 사람 확정 사례의 다음 입력,
  timeout/재시작의 재호출 없음, 저장 재시도의 추가 호출 없음과 예산/오류 비밀정보 제외를 확인했다.
  결과 `artifacts/codex-button-focused-tests.json`, `artifacts/codex-button-pipeline-regression.json`.
- 브라우저의 버튼 클릭으로 합성 feedback 12건을 **실제 1회 호출**했다. 호출 중 페이지를 새로고침한 뒤 요청/결과를 복원했고,
  다시 보낸 같은 UUID는 기존 event를 반환했다. 남은 호출 예산도 정확히 1회만 줄었다. 원본 해시·PoR은 동일했다.
  확정 사례 1건을 참고해 두 그룹(8/4건)을 만들고 사람이 확정한 한국어 이름을 유지했다. 모델 9.018초, 입력 13,749 / 출력 300 tokens,
  실제 호출 브라우저 시험 12.7초(전체 13.5초)였다. 그룹 구성은 여전히 사람이 확정해야 한다.
  `artifacts/codex-button-live-result.json`과 `artifacts/codex-button-live-browser.json`, dashboard의 `artifacts/codex-button-{desktop,mobile}.png`에 기록했다.
- 저장 결과/이력·모델/사례 출처·TOP·390px 표시의 브라우저 **3개**도 최종 결과에서 통과했다(7.7초). 실제 호출 시험은 별도 환경 flag/실행 설정으로 분리해 일반 회귀에서 실행하지 않는다.
  frontend TypeScript/production 빌드·Python 구문·문서 링크·`git diff --check`를 확인했다.
- 공유 8001에 최종 자산과 서버 opt-in을 반영했다. 기존 **1,106행**의 payload/state/version/시각 해시와 네 테이블/OID가 같았다.
  배포/화면 조회는 추가 모델 호출을 하지 않았다. 회사 DB나 DDL을 사용하지 않았다. 배포 결과 `artifacts/codex-button-deploy.json`.

## 2026-10-15 저비용 Codex 모델의 실제 합성 feedback 시험

- 사용자 요청으로 Codex CLI의 기존 ChatGPT 로그인과 `gpt-6-luna`를 연결했다. API 키·회사 `.env`·실제 report는 사용하지 않았다.
  연결 확인 1회와 분류 비교 3회를 실제 호출했다. CLI 로그인 사용량이며 API 단가로 청구액을 계산하지 않는다.
- `devtools/sanity_codex_mock.py`는 정확히 생성된 합성 fixture만 받는 opt-in advisor다. 모델은 빈 폴더에서 도구 없이 JSON을 반환한다.
  회사/DB/API 환경변수를 전달하지 않으며 일반 회귀 테스트와 HTTP API는 외부 호출을 켤 수 없다. 회사 `CORPORATE`와 SSO 차단은 유지한다.
- 실제 source 8건에서 초기 2개 그룹을 생성했다. 수정 사례가 없는 control 12건은 3개 그룹이었다.
  테스트 코드가 source를 **제약 설정 누락 통합 검토** 1개로 합치고 한국어 이름·검토 이유를 작성해 확정했다.
  `reusable=false`·기준 입력 없음에도 해당 DB 확정 사례를 다음 실제 호출에 제공했다.
  경로·문구가 달라진 feedback 12건에서는 관련 8건을 같은 한국어 이름으로 묶고 새 폭 불일치 4건은 별도 그룹으로 제안했다.
  확정 사례 1건을 참조했고 모든 record ID·원본 해시·PoR을 보존했다. AI 결과는 미확정 초안이며 사람 수정은 자동 모사한 테스트다.
- 모델 출력·입력 해시·prompt 버전·사용량·소요 시간을 불변 event에 저장하고 사람 수정/확정 이후에도 최초 출처를 보존한다.
  실제 분류 3회의 입력 38,855 / 출력 732 tokens, fixture 준비·저장·종료 포함 24.418초였다. 연결 확인 호출은 이 수치에서 제외한다.
  source/control 최초 이름·설명은 한국어 지시에도 중국어로 반환됐다. 실제 출력을 그대로 남겼으며 한국어 지시 준수를 보장하지 않는다.
  feedback에서는 사람이 확정한 한국어 이름을 유지했다. 성공 결과는 `artifacts/codex-mock-feedback-live/result.json`·`trace.json`·`export.json`이다.
- 실제 출력 이관은 기존 서비스의 같은 범위/개정/UUID/전체 ID 검사를 재사용하며 외부 모델을 재호출하지 않는다.
  독립 QA DB에 이관·재실행 시 기존 행/현재 구성 불변을 확인했다. 공유 workspace에도 3개 report와 이력을 추가했고 기존 **1,092행**의 payload/state/version/시각 해시가 동일했다.
  API·DB 재시작 후 HTTPS에서 모델명·8/4건의 두 그룹·확정 사례 참조 1건과 미확정 상태를 확인했다. 새 SQL/물리 테이블/권한 확대는 없다.
- 신규 순수/PG 검증 **13개 통과, 실패/오류/skip 0개**(`artifacts/codex-focused-tests.json`). 외부 호출 opt-in, 합성 입력 강제,
  누락/중복/외부 ID·도구 실행·실패/timeout 거부, prompt/source/case 고정, UUID 재시도·stale 사례·권한/HTTP 경계를 검사했다.
  첫 전체 회귀는 318개 중 2개 실패/skip 0개였다. 새 테스트의 HTTP 기대값 422를 기존 계약 400에 맞춰 정정하고 13개를 재검증했다.
  나머지 100,000개 고유 그룹 시험은 브라우저 동시 실행 중 DB statement timeout이었다. 병행 부하를 제거해 동일한 기존 시험을 **165.524초에 통과**했다.
  최초 실패 결과는 `artifacts/codex-regression-first-run.json`, 단독 재검증은 `artifacts/codex-scale-recheck.json`에 보존했다. 시간/권한/판정 검사를 완화하지 않았다.
- 실제 저장 결과를 쓰는 브라우저 **3개 통과**(8.1초, skip/flaky 0개). 모델/사례 출처·초기 2그룹→수정 1그룹 이력·feedback 2그룹·새로고침·TOP 조회·390px 가로 넘침 없음을 확인했다.
  `artifacts/codex-browser-tests.json`과 `oa/dashboard/artifacts/codex-feedback-{desktop,mobile}.png`에 남겼다.
  병행 부하 때 기존 브라우저 37개 중 1개의 renderer가 종료됐다. 다른 36개는 통과했고 해당 프로젝트 생성 시나리오도 단독 **4.3초에 통과**했다. 새 mock 브라우저 시험의 첫 screenshot timeout도 순차 실행에서 해소됐다.
  frontend TypeScript/production 빌드·Python 구문·문서 상대 링크·`git diff --check`를 확인했다. QA 8003과 임시 DB는 종료하고 공유 8001은 재시작 후 유지했다.
- 이번 모델 실험은 **8/12/12건·1단계 분류**다. 10,000/100,000건 모델 처리·2단계 모델 판정·회사 SDK/운영 job·전송은 검증하지 않았다.
  일반 새 초안/판정 제안 버튼은 기존 참고 엔진이다. 확정 사례를 다음 prompt에 제공하는 동작이며 모델 fine-tuning은 하지 않는다.
  실행·저장 계약은 [WORKSPACE.md](oa/WORKSPACE.md#codex-모델을-사용하는-합성-mock-시험)를 따른다.

## 2026-10-15 AI initial groups·사람 확정 사례 축적 의도 정정

- 사용자 결정은 **AI가 매 report의 초기 그룹을 생성 → 사람이 소속/이름/근거를 수정·확정 → DB 저장 → 다음 AI가 확정 사례를 참고해 초기 그룹 생성**이다.
  기존 그룹 유형과의 일대일 매칭·고정 그룹 사전이나 별도 공통 분류 유형 ID를 필수 구조로 제안했던 대화 설명을 정정했다.
- 요구사항 18.3/18.5/18.7과 PROTOCOL·WORKSPACE·사내 분류 인계에 최초 사례 0건, 새 그룹 생성/분리/병합,
  AI 이름 제안과 사람의 정정 피드백을 다음 입력으로 활용하는 흐름을 명시했다. 확정 사례의 축적은 기본 동작이며 별도 재사용 체크/필수 기준 입력을 요구하지 않는 것이 목표다.
- 현재 필드 일치 참고 엔진·선택형 재사용 UI·임시 그룹 이름·실제 모델 미연결은 목표의 완료 상태로 취급하지 않는다.
  공통 후보 조회/저장/API/UI·VWP 연결 전환은 Codex가 담당하고, 내부 AI의 회사 SDK 어댑터 허용 범위는 유지한다.
  과거 재사용/버전 호환 승인 기록과 고정 어댑터 서명은 이번 문서로 변경하거나 소급 재해석하지 않는다.
- 이번 작업은 문서 정정이다. 코드·SQL·DB·실행 중 서비스는 변경하지 않았다. 문서 diff/로컬 링크를 확인하며 런타임 테스트는 재실행하지 않는다.

## 2026-10-15 평균 10,000건·100,000건 대용량 순차 검토 구현

- 사용자 정정대로 **10,000건은 평균이며 100,000건 이상도 가능**한 입력으로 요구사항 18절과 인계 문서를 갱신했다.
  10,000건/100,000건을 고정 크기나 업무상 상한으로 쓰지 않는다. 기술적 자원 예산과 실제 검증 범위를 구분했다.
- Sanity item/명시적 report 선택에서 **1. 전체 로그 분류 → 사람 확정 → 2. 확정 그룹별 판정**으로 진행하는 전용 작업 화면을 구현했다.
  그룹 25개·원문 50건, 검색/과거 개정 고정 페이지, 페이지 간 선택을 보존하는 서버 분리·병합·이동·이름 변경,
  현재 분류 근거/참조 출처, 전체·확정·남은 그룹 수와 다음 미검토 그룹을 연결했다. 미저장 이름/필드 변경과 미확정/오래된 분류는 2단계로 진행하지 못한다.
- 기존 `WS_CLUSTER.state.task`에 요청 UUID·고정 원본/사례 해시·범위별 해시·진행·오류·재개 위치를 저장한다.
  평균 10,000건은 500건씩, 100,000건은 5,000건씩 처리한다. 전체 ID의 누락/중복 검증을 통과한 완성 결과만 불변 event와 head로 원자 적용한다.
  실패/참조 변경을 일부 성공으로 확정하거나 자동 재시도하지 않는다. 과거 판정·제거된 그룹 이력을 보존하며 그룹 구성 개정이 바뀌면 재검토한다.
  원본 규칙 FAIL에도 사람이 사유를 적어 그룹 PASS를 확정할 수 있고, 원본·별도 보고서 판정·PoR은 유지된다.
- 모델 호출 없는 기존 참고 엔진을 사용했다. 실제 회사 parser/SDK 검증과 VWP 모델/운영 전송 연결은 미완료이며
  `CORPORATE`의 503, SSO 보호 장치와 내부 AI의 회사 어댑터 허용 범위를 유지한다. 대용량 확장에는 새 DDL/물리 테이블·권한 확대가 없다.
- 독립 PostgreSQL **305개 통과, 실패/오류/skip 0개**(테스트 478.003초, 전체 runner 494.734초),
  기존 CLI 4개 시나리오와 별도 10,000행/100그룹 저장 probe(7.288초)도 통과했다. fixture DB 종료 확인.
  결과 `artifacts/workspace-large-backend-tests.json`, 원본 `/tmp/sanity-mock-pg-vjuftteq/summary.json`.
- GCA·VC LP·SpyGlass 각각 **단일 합성 report의 10,000건과 100,000건**을 실제 공개 참고 parser로 추출하고
  모든 ID·건수와 첫/중간/마지막 원문 위치를 확인했다. 여러 개의 한 건짜리 report로 대체하지 않았다.
  100,000건/100,000개 고유 그룹의 전체 분류 118.851초, 분류·확정·마지막 그룹 사람 PASS까지 163.692초,
  원본 JSON 60,845,516 bytes, metadata 직렬화 6,831 bytes, 그룹/원문 페이지 25/50개를 확인했다.
  `runner_peak_rss_kib=699168`은 이 테스트까지의 회귀 프로세스 누적 최대 RSS이며 순수 분류만의 최대 메모리는 아니다.
  결과 `artifacts/workspace-100000-unique.json`.
- 최종 빌드에서 workspace 브라우저 **16개**(2.8분)와 별도 **100,000건 시나리오 1개**(2.9분), 기존 브라우저 **37개**(1.7분)를 통과했다.
  10,000건/100그룹은 분류 11.150초, metadata 응답 6,357 bytes였다. 페이지 간 51건 선택·분리/병합·사례 재사용·TOP 조회,
  VC LP/SpyGlass의 마지막 원문 검색, 사람 FAIL→PASS 독립 판정과 390px 화면을 확인했다.
  100,000건/100그룹은 5,000건 저장 후 API·DB를 실제 재시작하고 같은 task/cursor를 복원했다. 과거 범위 재요청도 중복 처리하지 않았다.
  브라우저에서 남은 95,000건을 89.520초에 처리하고 20개 전체 범위·분류 확정·사람 판정·새로고침·모바일·원본/PoR 보존을 확인했다.
  metadata 6,704 bytes/최초 조회 4.326초, 초기 그룹/원문 25/50개, 페이지 오류/가로 넘침 없음.
  결과 `artifacts/workspace-large-persistence.json`, `artifacts/workspace-100000-browser-tests.json`,
  `artifacts/workspace-large-frontend-tests.json`, `oa/dashboard/artifacts/workspace-100000-browser-metrics.json`과 데스크톱/모바일 PNG.
- 4GB 개발 환경에서 다른 검증과 함께 실행한 참고 수치다. 서버는 요청마다 해당 report/구성을 읽어 전체 검증하므로 원본 크기에 비례하는 비용이 남는다.
  report JSON 128MiB, 로그/그룹 각각 1,000,000개와 사례 조회 예산을 명시적으로 검사하며, 한도 초과는 부분 처리 없이 중단한다.
  더 큰 입력·긴 행·동시 사용자 부하, 실제 사내 형식/모델 품질과 운영 지연 목표는 별도 사내 검증 대상이다.
- 공유 `/workspace`에 최종 빌드/API와 도구별 10,000건 report 3개·GCA 100,000건 report 1개를 추가했다.
  API를 잠시 중단한 상태에서 기존 **1,081개 행의 payload/state/개정/시각 해시와 네 테이블 OID가 동일**함을 확인했다.
  기존 1,000개를 포함한 총 report 1,004개·추출 record 131,000건이다. 새 대형 report에는 검증용 분류/사람 판정을 작성하지 않았다.
  실제 HTTPS 주소에서 100,000건 진입·50건 페이지·마지막 원문 검색·2단계 잠금·390px/초록색 테마·Secure/HttpOnly 쿠키를 확인했다.
  최초 metadata 1,252 bytes, 페이지 오류/가로 넘침 0개. 결과 `artifacts/workspace-large-deployment.json`,
  `oa/dashboard/artifacts/workspace-large-live-smoke.json`과 `workspace-large-live-desktop/mobile.png`.
  별도 QA API/DB는 종료했고 설정의 static 경로는 영속 dist로 복원했다. 공유 서비스와 다른 작업자의 개발 서버는 유지했다.

## 2026-10-15 단일 report 10,000건의 순차 검토 — 명세 우선 갱신

- 사용자가 규모를 **한 sanity item의 report 안의 로그·위반 항목 약 10,000건**으로 확인하고 구현 전에 명세 갱신을 요청했다.
- [요구사항 18절](requirement.md#large-report-review-flow)에 Sanity item 진입 → 전체 로그 분류·사람 확정 → 확정 그룹별 판정·사람 확정의 주 동선과 단계 전환 조건을 추가했다.
  전체 ID coverage, 분리/병합·초안 저장, 그룹별 진행률·보류·재분류, 두 종류의 DB 사례 재사용, 대용량 페이지/분할 처리와 수용 시나리오를 명시했다.
- 현행 report당 1,000건/100그룹 한도·모달 UI·대부분 한 건짜리 샘플을 10,000건 지원 완료로 취급하지 않는다.
  기존 10,000행 DB 측정과 소규모 기능 검증도 이 종단 시나리오의 검증 결과와 구분했다.
- PROTOCOL과 WORKSPACE 안내에 최신 명세/현재 한도를 연결했다. 이번 작업은 문서 변경이며 코드·SQL·샘플·실행 중 API/DB는 변경하지 않았다.
  문서 diff·로컬 링크를 확인했으며 런타임 변경이 없어 앱/DB 테스트는 재실행하지 않았다. 후속 구현은 아직 진행하지 않았다.

## 2026-10-15 두 단계 clustering·그룹 판정 연결

- `/workspace`의 **근거·검토 → Clustering·그룹 판정**을 추가했다. 1단계에서 원본 로그 전체의
  초안을 받고 사람이 분리·병합·이름·사유·재사용 기준을 저장/확정한다. 2단계는 확정된 그룹마다
  제안과 참고 근거를 조회하고 사람이 PASS/FAIL/보류·사유·재사용 조건을 별도로 저장한다.
- 원본 record 전체를 중복/누락 없이 배정하며 PASS 로그도 포함한다. `007_workspace_pipeline.sql`은
  기존 OA 테이블에 분류 head/불변 event와 그룹 판정 head/불변 event/proposal을 추가한다.
  원본/버전/분류 event를 실제 FK로 연결하며 기존 네 테이블·앱 권한과 원본 규칙 판정·PoR을 보존한다.
- 그룹 판정은 분류 event ID/개정·전체 구성 해시·그룹 snapshot에 연결한다. 분류를 바꾸면 같은 그룹 ID의
  이전 판정도 재검토가 필요하며 삭제된 그룹과 제안 수정 이력도 보존한다. 다른 보고서가 참조한 분류의
  정정/철회와 자기 자신에 의존하는 사례를 검사한다. 기존 보고서 전체 판정과 목록 열은 별도로 유지한다.
- 다음 초안은 명시적으로 재사용을 승인한 분류 사례를 참고한다. 현재 유효한 확정 그룹의 사람 판정만
  다음 그룹 판정 기준으로 사용하며 출처/개정을 표시한다. 담당자/TOP 범위, CSRF/Origin, UUID 재시도,
  동시 개정 충돌과 오래된 분류에 대한 저장 거부를 검증했다.
- 현재 초안/판정 제안은 `CONFIRMED_PARTITION_V1`/`CONFIRMED_CRITERIA_V1` 참고 엔진이며 실제 모델이 아니다.
  모호한 사람의 분리나 충돌을 임의 순서로 재현하지 않는다. `CORPORATE`는 `503 AI_NOT_CONFIGURED`를 유지한다.
  사내 AI용 추상 계약·검증·제한은 [분류 인계](vwp_bridge/CLUSTERING_INTEGRATION.md)와
  [판정 인계](vwp_bridge/REVIEW_INTEGRATION.md)에 기록했다. 실제 SDK와 운영 VWP job/DB 전달 연결은 미연결이다.
- 독립 PostgreSQL **293개 통과, 실패/오류/skip 0개**(169.566초), CLI 4개 시나리오와
  10,000건/100그룹 저장(6.874초) 통과. 결과 `artifacts/workspace-pipeline-backend-tests.json`; 테스트 DB 종료 확인.
  기존 브라우저 회귀 **37개**, 실제 별도 API/DB 통합 **13개**(최종 빌드 50.2초)도 통과했다.
- 별도 QA에만 합성 다중 로그 보고서 2개를 추가해 분리→병합→확정→그룹 PASS, 다음 보고서의 분류/판정
  재사용→사람의 FAIL 정정, 재분류에 따른 판정/참조 무효화를 확인했다. 이 QA 자료는 공유 DB에 넣지 않았다.
  API/DB 재시작 후 두 보고서의 분류·그룹 판정 이력 5개와 PoR의 동일성을 확인했다.
  결과 `artifacts/workspace-pipeline-persistence.json`; QA API/DB는 검증 후 종료했다.
- 모바일 모달의 높이 변경 애니메이션과 줄바꿈된 탭의 축소 때문에 생기던 화면 초과/겹침을 수정하고
  실제 브라우저의 모달 경계·탭/본문 경계 검사로 확인했다. 초록색 토큰은 유지한다.
  직접 확인한 캡처는 `oa/dashboard/artifacts/workspace-pipeline-stale-mobile.png`,
  `workspace-pipeline-clustering-desktop.png`, `workspace-pipeline-public-desktop.png`, `workspace-pipeline-public-mobile.png`다.
- 공유 DB는 기존 **1,079개 OA 행 전체 해시·건수와 네 테이블 OID를 보존**하며 `007`만 적용했다.
  검증한 API/정적 빌드를 공유 HTTPS에 반영했다. 실주소에서 두 단계·담당자 초안 버튼·초록색·모바일을 확인하고
  원본/보고서 판정/분류/PoR이 동일함과 page error 0개를 확인했다. 공유 환경에는 업무 판정/분류를 시험 저장하지 않았다.
  결과 `artifacts/workspace-pipeline-publish.json`, `artifacts/workspace-pipeline-public-smoke.json`에 기록했다.
- TypeScript 포함 빌드·Python 구문 검사·`git diff --check`와 관련 문서의 로컬 링크 112개를 확인했다.
  합성 검증을 실제 회사 report/SSO/AI의 운영 검증으로 표시하지 않는다.

## 2026-10-15 최초 초록색 테마 복원

- 사용자 요청으로 최초 Git 팔레트의 색상 토큰 22개를 복원했다. 강조색 `#294f46`,
  배경 `#f5f4f0`, 사이드바 `#eeeee7`과 모달/모바일 배경의 중성 그림자 색을 함께 적용했다.
- TypeScript 포함 빌드·`git diff --check`와 데스크톱/모바일 브라우저 검사 4개를 통과했다.
  공유 HTTPS에서 실제 색상·사용자 판정 입력·모바일 메뉴·overflow/page error 없음과 PoR 불변을 확인했다.
- 공유 정적 자산을 갱신했다. 결과 `artifacts/workspace-green-smoke.json`, 캡처
  `oa/dashboard/artifacts/workspace-green-overview.png`, `workspace-green-review.png`,
  `workspace-green-mobile.png`를 직접 확인했다. 별도 QA 서버/DB는 종료했다.

## 2026-10-15 사용자 판정·확정 조건·제안의 영속 연결

- `/workspace` 검사별 **근거·검토 → 사용자 판정·AI review**를 구현했다.
  원본 규칙 FAIL에도 독립 사용자 PASS와 사유를 저장하며 규칙 결과·원문·PoR은 보존한다.
  목록의 규칙/사용자 판정을 분리하고 담당자의 저장/제안과 TOP의 조회 권한을 서버에서 검사한다.
- `006_workspace_reviews.sql`은 기존 네 테이블/권한을 유지하면서 OA에 현재 판정 head,
  불변 판정 사례/제안을 추가한다. UUID 재시도·개정 충돌·CSRF/Origin·다른 블록/버전 차단을 검증했다.
  원본 해시·버전 snapshot·조건·이유·판정자·이전 개정과 제안/최종 판정의 일치·수정을 보존한다.
- 일회성 사례와 명시적 재사용 조건을 구분한다. 현재 같은 scope·호환 버전의 다른 보고서 최신 사례만 조회하며,
  조건의 정확한 일치에만 제안한다. 원문 미수신/파싱 미완료·기준 부재·판정 충돌은 보류다.
  조건 정정/철회뿐 아니라 새 반례 추가도 전체 사례 목록 해시로 감지하여 기존 제안 확정을 거부한다.
- `ReviewAdvisor` 추상 계약과 회사 어댑터·[인수인계](vwp_bridge/REVIEW_INTEGRATION.md)를 제공했다.
  현재 엔진은 `CONFIRMED_CRITERIA_V1`, `model_called=false`이며 사내 AI 미연결을 표시한다.
  실제 SDK·VWP job/DB 전달 연결, 자유 사유로부터 일반 기준 추출, 별도 검증 집합의 모델 정확도 평가는 미구현이다.
  구형 승인 API/PR #6 원본 snapshot의 `FAIL_CANNOT_PASS`를 이 새 사용자 판정 API와 혼동하지 않는다.
- 독립 PostgreSQL 전체 **277개 통과, 실패/오류/skip 0개**(130.732초), CLI 4개 시나리오와
  10,000건/100그룹 저장(7.972초) 통과. 결과 `/tmp/sanity-mock-pg-my6mjywq/summary.json`; DB 종료 확인.
  테스트 중 신규 원본 변경 검사의 기대 HTTP status를 기존 `STATE_CORRUPT=503` 계약에 맞췄다.
- 기존 Chromium 회귀 **37개 통과**(1.8분), 실제 별도 API/DB 통합 **10개 통과**(35.7초).
  FAIL→사용자 PASS, 새로고침·다른 TOP 조회, 다음 보고서의 기준/출처 참조, 제안 수정 이력과 모바일 보류 저장을 확인했다.
  저장된 사유를 다시 읽을 때 label에 textarea 내용이 포함되던 문제는 label/입력 분리로 수정했다.
  API와 DB를 실제 재시작한 뒤 3개 판정의 개정·전체 이력·제안과 PoR이 동일함을 확인했다.
- 별도 QA DB는 기존 1,110행, 공유 DB는 기존 1,074행을 보존하며 `006`을 적용했다.
  합성 fixture 전용 offline upgrade는 네 테이블 OID와 기존 OA 행 전체 해시/건수가 동일한지 비교했고,
  이미 적용된 QA DB의 재실행은 `MIGRATION_CURRENT`로 종료했다. 앱 권한·인증 설정·회사 환경은 변경하지 않았다.
- 검증한 빌드와 API를 공유 HTTPS `/workspace`에 반영했다. 실주소 브라우저 읽기 검증에서
  FAIL의 사용자 PASS 입력 가능·사내 AI 미연결·PoR 불변·모바일 overflow 없음·page error 0개와
  Secure/HttpOnly/SameSite=Strict cookie를 확인했다. 공유 DB에 사용자 판정/기준을 시험 저장하지 않았다.
  별도 QA의 판정 데이터와 공유 환경은 분리했으며 QA API/DB는 종료했다.
- TypeScript 포함 프런트엔드 빌드, Python 구문 검사, `git diff --check`, 관련 문서 로컬 링크 84개 통과.
  직접 확인한 캡처는 `oa/dashboard/artifacts/workspace-human-review-desktop.png`,
  `workspace-review-proposal.png`, `workspace-human-review-mobile.png`, `workspace-review-public-mobile.png`다.
  합성 통합 결과를 실제 회사 report/SSO/AI 검증으로 표시하지 않는다.

## 2026-10-15 사람 판정 기준을 축적하는 AI review 목표 확인

- 사용자 결정: 사람이 확정한 판정·근거·적용 조건을 계속 축적해 AI가 같은 기준으로 판정을 제안하도록 한다.
  원본 규칙 판정·AI 제안·사용자 판정을 분리하며 원본 FAIL을 보존한 사용자 PASS를 허용하는 후속 정책을 기록했다.
- [요구사항 17절](requirement.md#human-judgment-learning)에 사례/기준 저장, 조건·예외·반례, 일회성 승인과 재사용 기준의 구분,
  범위/버전 호환, 기준 정정 이력, AI 입력의 참조 출처, 사람과의 일치/정정/보류 평가를 정리했다.
  기존 문제 그룹 분류 기준과 업무상 PASS/FAIL 판정 기준을 구분한다.
- 우선 방식은 확정 사례·기준을 다음 AI 입력에 참조하는 것이며 모델 가중치 자동 학습으로 표시하지 않는다.
  실제 report와 AI 실행은 사내 VWP, 검토·확정은 OA라는 역할과 DB 통신 경계를 유지한다.
- 이번 변경은 요구사항/프로토콜/워크스페이스 안내의 합의 기록이다. 런타임과 PR #6 원본 테스트 화면,
  `FAIL_CANNOT_PASS`, 기존 테스트·DB·회사 어댑터는 변경하지 않았다. 실제 기능 연결 완료를 주장하지 않는다.

## 2026-10-15 sanity item별 검사 대장·브라운 테마

- `/workspace` 검사 대장에 `item_id`별 선택과 전체/PASS/FAIL/보류/미수신 건수를 추가했다.
  현재 예시는 GCA 제약조건·VC LP 저전력·SpyGlass Lint 검사이며 실제 ID로 구분한다.
  같은 도구의 다른 item은 분리하고 ID가 없는 과거 자료는 항목 미분류로 표시한다.
- reports API에 선택적 `item_id`와 `groups` 집계를 추가했다. 프로젝트/블록/버전/ECO 권한을 확인한 뒤,
  페이지·검색·결과 필터 적용 전에 항목별 건수를 DB에서 계산한다. 기존 API의 item 생략 동작은 유지한다.
  UI는 한 항목의 보고서만 표시하고 검색/필터/페이지를 그 안에 적용한다. 항목 전환은 검색·필터·페이지를 초기화하고
  선택 항목은 URL에 보관해 새로고침 후 복원한다. 검색 중 입력 포커스와 항목 선택 버튼을 유지한다.
- 테마의 붉은기를 줄여 커피 브라운 `#79583a`, 베이지 선택 배경, 따뜻한 중성색으로 변경했다.
  공통 토큰과 modal/backdrop 색을 함께 바꾸고 PASS/FAIL 상태 의미 색은 유지했다.
- 동일 GCA 도구의 별도 item 40건과 미분류 자료를 넣어 페이지 밖 집계·정확한 item 필터·미수신·타 블록 접근 거부를 검증했다.
  독립 PostgreSQL 전체 **256개 통과, 실패/오류/skip 0개**(89.136초), CLI 4개 시나리오 및
  10,000건/100그룹 저장(6.869초) 통과. 결과 `/tmp/sanity-mock-pg-o14v2x3b/summary.json`; DB 종료 확인.
- 기존 Chromium 회귀 **37개 통과**(1.8분), 실제 별도 API/DB 브라우저 통합 **7개 통과**(30.1초).
  항목별 38/37/37건, 항목 안의 25건 페이지, 타 항목 보고서 검색 제외, 연속 타이핑 포커스,
  키보드 선택·새로고침 복원·모바일·PoR 불변과 기존 지정/권한/로그아웃을 확인했다.
  기존 전체 검색 사례는 현재 선택 item에 속하고 첫 페이지 밖에 있는 `sample-1000`으로 검증한다.
- 프런트엔드 빌드·TypeScript/Python 구문 검사·`git diff --check` 통과.
  캡처 `oa/dashboard/artifacts/workspace-items-desktop.png`, `workspace-mobile.png`를 직접 확인했다.
  검증한 빌드를 공유 HTTPS 주소에 반영해 item 3개·VC LP 37건·선택 복원·모바일 overflow 없음·PoR 불변·page error 0개를 확인했다.
  공유 서버/DB는 유지하고 검증용 8002 API/DB는 종료했다. 문서 로컬 링크 24개도 통과했다.
  원본 report·판정·사용자 배정·PoR·DB 스키마는 수정하지 않았다. 사용 범위는 [WORKSPACE.md](oa/WORKSPACE.md)를 따른다.

## 2026-10-15 영속 워크스페이스·PoR 저장·버건디 색상

- `/workspace`를 실제 FastAPI·독립 PostgreSQL에 연결했다. 테스트 계정 10개·프로젝트 3개·블록 9개와
  기존 공개 parser로 DB 수신까지 검증한 1,000개 예시 항목을 하나의 로그인/조회 흐름에서 사용한다.
  계정과 자료는 합성이며 회사 SSO·운영 DB·실제 report·실제 AI를 호출하지 않는다.
- 서버에서 활성 사용자·프로젝트 TOP/블록 담당 배정·조회/지정 범위를 확인한다. PoR 변경은 담당자만 허용하며
  기대 개정·요청 UUID 잠금·PoR 행 잠금·불변 이력 저장을 한 트랜잭션에서 처리한다.
  동일 요청 재시도, 다른 내용의 UUID 재사용, 동시 개정 충돌, 다른 프로젝트/블록 접근을 검증했다.
  버전/ECO 조회는 PoR을 변경하지 않고 PoR 지정도 원문·판정·다른 블록을 수정하지 않는다.
- 새 `db/005_workspace.sql`만으로 OA 관리 레코드의 종류·키·FK·필수 scope를 확장했다.
  기존 form/002/003/004, 네 테이블/OID, 기존 행, 제한된 앱 UPDATE 권한을 보존한다.
  이전 종류 제약이 예상과 다르면 전체 migration을 rollback한다. 실제 사내 DB에는 적용하지 않았다.
- 세션은 DB에 저장하고 HttpOnly/SameSite=Strict cookie, HTTPS Secure, 쓰기 Origin/CSRF,
  입력 크기·Host 검사와 로그인 제한을 적용했다. 기존 SSO `AUTHORIZATION_NOT_IMPLEMENTED`는 유지한다.
  테스트용 실행기는 회사 `.env`와 DB 환경변수를 상속하지 않는 공개 소스 snapshot으로 실행한다.
- 현재 확인된 report는 NET_001 POST/chip_finish에만 연결했다. 미수신 PRE/이전 버전/ECO를 다른 결과로 채우지 않는다.
  현황도 PoR 버전의 검사만 집계하고, 미지정일 때만 최신 수신 버전임을 표시한다.
- 공통 색상 토큰을 버건디 `#913c4b`와 따뜻한 붉은 중성색으로 변경했다.
  로그인·사이드바·선택·버튼·포커스에 적용하고 PASS/FAIL 의미 색은 구분했다.
  데스크톱과 390px 모바일을 실제 Chromium 캡처로 확인했다.
- 최종 독립 PostgreSQL 회귀: **255개 통과, 실패/오류/skip 0개**(87.453초).
  기존 CLI 4개 시나리오 및 10,000건/100그룹 저장(6.924초)도 통과했고 DB 종료를 확인했다.
  결과: `/tmp/sanity-mock-pg-y3u1s_ns/summary.json`, `tests.json`, `tests.log`, `scale.json`.
- 기존 브라우저 회귀 **37개 통과**. 별도 API/DB의 실제 워크스페이스 브라우저 통합 **6개 통과**(10.4초).
  로그인 범위·전체 검색/원문·PoR 저장/새로고침/별도 TOP 계정 조회·권한 거부·미수신·모바일·로그아웃을 검증했다.
  검증용 8002 DB를 분리해 사용자의 8001 PoR을 변경하지 않았다.
  캡처: `oa/dashboard/artifacts/workspace-login.png`, `workspace-overview.png`,
  `workspace-history.png`, `workspace-mobile.png`(로컬 생성물, Git 제외).
- 별도 검증 서버에서 **API와 PostgreSQL을 모두 재시작한 뒤에도 PoR/개정/지정 이력이 동일**함을 확인했다.
  공유 HTTPS 경로는 loopback 8001로 연결하고 `/` → `/workspace`, Secure cookie, 실제 조회,
  버건디 토큰, 모바일 overflow 없음, 로그아웃을 확인했다. 다른 worktree의 5173 서버는 보존했다.
- `npm run build` 통과. 실행·테스트 계정·지원 범위·운영 미연결 항목은 [WORKSPACE.md](oa/WORKSPACE.md)에 기록했다.
  Python 구문 검사·문서 로컬 링크 91개·`git diff --check`도 통과했다. 검증용 8002 API/DB는 종료하고 확인용 8001은 유지했다.
  서버 config/DB/로그/생성 자료는 Git에 포함하지 않는다.

## 2026-10-14 공개 report 초기 파서·1,000개 데이터·대시보드

- 사용자 요청의 가상 이용자 10명·프로젝트 3개·각 프로젝트 3개 블록을 구성했다.
  TOP 3명과 블록 담당자 7명, 프로젝트별 CPU/GPU/DDR을 사용하며 일부 담당자는 두 프로젝트에 배정했다.
- 공급자 공개 문서의 GCA full text·VC LP Tree Summary/Tag 상세·SpyGlass non-turbo moresimple 구조를 조사했다.
  `vwp_bridge/integration/public_parsers.py`에 추상 계약을 사용하는 초기 추출기를 추가하고
  `PUBLIC_REPORT_FORMATS.md`에 출처·지원 subset·미지원 형식·회사 구현 인계를 명시했다.
  실제 사내 report, 실제 EDA 실행 결과, vendor 원문의 복제는 없다.
- 초기 registry는 명시적인 합성 tool/rule version만 허용한다. native 부분은 추출하되 실제 완료 계약 미확인 시 PARTIAL을 유지한다.
  `SANITY_SAMPLE/ZERO/END`는 공급자 문법이 아닌 fixture 전용 표식이다. 수치 판정은 기존 공통 evaluator가 수행한다.
  rule/design/mode·요약/상세 건수·waiver·종료 후 내용·미해석 섹션을 검사하며 일반/회사 실행 경로에는 자동 등록하지 않았다.
- `tools/build_public_preview.py`로 독립 PostgreSQL의 수집 → 파싱 → VWP DB evidence/queue → OA 수신/저장 → DB 재조회 경로를 실행했다.
  원래 payload와 OA 저장 payload의 일치를 확인하고 읽기 전용 JSON을 내보낸 뒤 mock DB를 종료했다.
  마지막 데이터 결과: `/tmp/sanity-mock-pg-ou_3ks1y/dataset-summary.json`.
  예상 item 1,000개(합성 report 파일 950개·누락 50개), 묶음 27개, DB evidence 1,000건.
  GCA 334 / VC LP 333 / SpyGlass 333, PASS 600 / FAIL 350 / UNKNOWN 50,
  COMPLETE 850 / PARTIAL 100 / MISSING 50. PARTIAL 100건에도 확인된 위반은 FAIL로 유지된다.
- `/?demo=1&dataset=reports`에서 스냅샷을 표시한다. 가상 계정/프로젝트/블록 범위 전환,
  전체 선택 범위 검색·도구/결과 필터·25건 페이지·원문/줄 위치/추출 필드/차단 사유·합성 report 다운로드를 제공한다.
  실제 SSO/RBAC·실시간 DB·사람의 검토/PoR 저장을 구현한 화면으로 표시하지 않는다.
  기존 Tailscale static dist 경로에서 HTTP 200과 Chromium 렌더링(25개 행·계정 10개·page error 0개)을 확인했다.
- 공개 파서 회귀 12개를 추가했다. 실제 독립 mock PostgreSQL 전체: **236개 통과, 실패/오류/skip 0개**(66.655초).
  기존 CLI 4개 시나리오와 10,000건/100그룹 파싱·저장(6.371초)도 통과했다.
  결과: `/tmp/sanity-mock-pg-9cc_wkrp/summary.json`, `tests.json`, `tests.log`, `scale.json`; DB 종료 확인.
- 기존 브라우저 회귀와 신규 preview 7개를 포함해 **Chromium 35개 통과**(1.1분).
  다운로드 바이트, 누락 report의 다운로드 차단, PARTIAL 원문, 계정별 범위, 전체 검색, 390px 모바일,
  명시적 demo 조건·데이터 조회 실패 시 다른 demo로 대체하지 않는 동작을 확인했다.
  기본 5173에 다른 worktree 서버가 있어 테스트 전용 5188을 사용했다. 기존 서버는 변경하지 않았다.
  캡처: `oa/dashboard/artifacts/reports-library-desktop.png`, `reports-library-mobile.png`,
  `reports-library-mobile-dialog.png`, `reports-library-hosted.png`(로컬 생성물, Git 제외).
- `npm run build`, Python 구문 검사, 변경 문서의 로컬 링크 검사, `git diff --check` 통과.
  샘플 JSON/DB/report는 Git에서 제외한다. 회사 registry/비공개 fixture·실제 `.env`/DB/AI·SQL·운영 인증 계약을 변경하지 않았다.

## 2026-10-14 사내 item별 report parser 추상 인터페이스·인수인계

- 사용자 결정: 실제 report는 사내 AI/담당자가 사내에서만 열람·검증한다. 현재 Codex는
  공통 추상 계약·검증·합성 재현을 담당하고 실제 원문을 전달받지 않는다.
- `common/sanity_parsers.py`에 `ReportParser`, 읽기 전용 context, source span/reader,
  추출·metadata·미해석 구간·완료 이벤트와 명시적 registry를 구현했다.
  공통 코드가 원문 위치/Pointer·EOF·미해석 구간·선언 건수와 기존 Decimal/report 판정을 검사한다.
  정상 0건은 명시적 근거를 가진 결과 기록으로 남기며 빈 report/0개 기록은 미완료를 유지한다.
- 회사 구현 지점은 `vwp_bridge/integration/corporate_parsers.py`의 parse/등록과
  `vwp_bridge/integration/company_parsers/`의 전용 어댑터/테스트다.
  `vwp_bridge/corporate_analyze.py`로 주입하며 일반 main은 회사 코드를 자동 로드하지 않는다.
  기본 registry는 비어 있고 미구현 메서드의 `PARSER_NOT_CONFIGURED`를 별도 smoke로 확인했다.
- 등록은 item·tool·tool/rule version과 고정 판정 방식으로 제한한다. 내장 파서 교체·wildcard를 거부한다.
  사용한 구현 버전/범위를 불변 RUN 계약과 `producer.parsers`에 포함하여
  미지원 상태의 과거 분석, 최초 등록, 구현 버전 변경을 각각 별도 분석/번들로 보존한다.
  예상 밖 어댑터 예외는 고정 코드로 중단하며 원문 예외 출력·자동 재시도·성공 봉인/전송이 없다.
- `PARSER_INTEGRATION.md`에 필드/반환 계약, 합성 예제, 구현·등록 순서,
  정상/위반/경계/0건/잘림/형식 변경 검증과 합성 Codex 문의 절차를 제공했다.
  AGENTS·내부 AI 가이드·요구사항·프로토콜·README의 사내 수정 허용 범위를 함께 맞췄다.
- 공통 테스트는 수정될 회사 stub/registry에 의존하지 않는다. mock snapshot은 회사
  구현/등록 파일·company_parsers·비공개 fixture를 수집하지 않음을 별도 회귀로 확인했다.
- 최종 독립 mock PostgreSQL: **224개 통과, 실패/오류/skip 0개**(52.831초).
  결과: `/tmp/sanity-mock-pg-8kuba2z3/summary.json`, `tests.json`, `tests.log`.
  기존 CLI 4개 시나리오와 10,000건/100그룹 파싱·저장(6.792초)도 통과했고 DB는 종료했다.
  신규 검증은 순수 파서 계약 27개·PostgreSQL 통합 6개·회사 자료 미수집 1개다.
  원문/판정 저장·OA 수신·결정적 재실행·구현 버전 변경·오류 시 전송 차단을 포함한다.
- 구문 검사, 문서 링크 검사, `git diff --check` 통과. 기존 사내 설정·SQL·UI·SSO 계약은 유지했다.
  실제 report/사내 DB/실제 AI를 사용하지 않았으며 현장 어댑터의 의미적 정확성은 사내 검증 대상이다.

## 2026-10-14 전체 검토에서 재현한 원문 작업자·분류 참조 복구 수정

- 기본 VWP 작업자의 `respond_evidence()` 호출이 큐에서 가져온 요청 ID를 응답에 반영하지 않아
  PostgreSQL CHECK 오류와 `PROCESSING` 잔류를 일으키는 문제를 수정했다. 응답 본문과 correlation_id 모두 점유한 요청 ID를 사용한다.
  자동 다중 요청 처리·명시 ID 재시도·거부/만료/없는 근거·만료 lease 재점유 후 정상 응답을 회귀로 확인했다.
- A 확정 → A를 참조한 B 확정 → A 재적용 시 자기 자신에 의존하는 제안이 만들어지는 문제를 수정했다.
  새 후보 목록은 현재 유효하고 요청 대상에 직·간접 의존하지 않는 소스만 선택하며, 최신 소스가 부적격이면
  같은 지속 그룹의 다른 번들에서 이전 유효 소스를 찾는다. 제외 소스·개정·사유는 불변 요청과 OA 화면에 남긴다.
  페이지 경계를 넘는 이전 소스 검색과 상한 초과 시 전체 요청 거부도 확인했다.
- 이전 코드가 만든 순환 작업의 적용과 오래된 작업의 확정/규칙 저장은 계속 막는다.
  활성 READY 작업의 보류는 오래된 참조에서도 허용하되 사유·기대 개정·UUID 검증은 유지한다.
  이미 막힌 작업을 보류 → 새 요청 → 적용/확정으로 복구하고 후속 보고서도 처리하는 시나리오를 검증했다.
  기존 sanity 판정·봉인 번들·보고서가 변경되지 않는 것을 함께 확인했다.
- 독립 mock PostgreSQL에서 **190개 통과, 실패/오류/skip 0개**(51.600초).
  결과: `/tmp/sanity-mock-pg-piqk9mrw/summary.json`, `tests.json`, `tests.log`.
  CLI 4개 시나리오와 10,000건/100그룹 파싱·저장 검사도 통과했고 mock DB는 종료했다.
- 별도 포트/결과 폴더에서 브라우저 회귀 **28개 통과**(53.0초).
  추가 UI 검증은 오래된 작업의 승인 버튼 차단·보류 허용·제외 사유 표시·모바일 폭을 포함한다.
  결과: `/tmp/sanity-fix-browser-5UrtwV/results.json`. TypeScript/Vite build, `pip check`, `git diff --check` 통과.
- 신청 form·설치 SQL·4개 테이블·권한·SSO 보호는 변경하지 않았다. 이번 수정용 추가 migration은 없다.
  사내 DB/실제 AI 호출·실제 `.env` 변경·Git commit/push는 하지 않았다.

## 2026-10-14 내부 AI 작업 경계·디버깅 인계

- `AGENTS.md`와 `INTERNAL_AI_GUIDE.md`에 사내 AI의 파일/메서드 허용 목록, 보호 영역, 완료 기준을 기록했다.
  회사 SSO/계정 조회·허용된 주입·기존 plan 입력 연결만 제한적으로 맡기며, 공통 parser·분류·DB·권한·UI 변경은 Codex가 담당한다.
- 자기 어댑터의 예상 밖 오류도 수정/재시도를 멈추고 `WAITING_FOR_CODEX`로 문의하도록 명시했다.
  비식별 진단 템플릿을 제공하고 자동 연락 경로가 없을 때 사용자가 현재 대화로 중계하도록 했다.
- 기존 SSO 인수인계의 RBAC·DB·기존 UI 변경 지시를 Codex 후속 작업으로 정정하고 README·요구사항과 일치시켰다.
  이번 작업은 문서/에이전트 지침만 변경했다. 링크 검사와 `git diff --check`를 통과했으며 실행 코드·DB·AI 테스트는 다시 실행하지 않았다.
  지침 작성은 파일시스템/병합 권한 강제 설정을 의미하지 않는다.

## 2026-10-14 로컬 mock PostgreSQL 생성·통합 검증

- 실제 사내 DB 연결은 사용자가 사내 배포 후 확인하기로 했으며, 개발 측 검증은 새 로컬 DB에서 수행했다.
  `db/tests/run_mock.py`는 PostgreSQL 16+ 바이너리로 매번 독립 인스턴스를 만들고 테스트 뒤 종료한다.
  이번에는 PostgreSQL 16.15/psycopg2 2.9.13, 로컬 소켓, SCRAM 인증과 서로 다른 제한된 VWP/OA 로그인을 사용했다.
  잘못된 비밀번호의 거부, 양쪽 스키마/권한 확인 및 SQL smoke를 통과했다. TCP 수신은 열지 않았다.
- 코드/SQL만 별도 작업 폴더에 복사하며 기존 `.env`·사내 접속 문서·실제 report를 복사/조회하지 않는다.
  부모 프로세스의 DB/AI 설정도 제거한다. 테스트 AI는 FakeAI/HTTPX 모의 응답이며 실제 AI를 호출하지 않았다.
  환경 격리·비공개 파일 제외·symlink 거부·시작 실패 시 종료 처리의 안전성 테스트도 추가했다.
- 첫 새 DB 실행에서 163개가 통과했고, 시작 실패 시 정리 처리를 검증하는 테스트를 추가한 뒤 **또 다른 새 DB에서 164개 통과**,
  실패/오류/skip 각 0개, 회귀 테스트 43.651초를 확인했다.
  검증에는 원자 저장/rollback·권한 격리·중복 전달·동시 큐 점유·lease 만료/복구·분류와 사람 검토 분리가 포함된다.
- complete/missing/malformed/unsupported CLI demo를 각각 실행해 보고서를 만들었다.
  정상 입력도 알려진 FAIL과 사람 검토 대기를 포함하므로 `review=INCOMPLETE`가 예상 결과다.
  테스트 성공을 합성 sanity 결과의 PASS 또는 사람 검토 완료로 표시하지 않는다.
- 최종 규모 검사: 합성 numeric 10,000건/100그룹, 전체 evidence 10,000건 저장과 그룹 수 대조 성공,
  파싱/저장 6.847초, 클라이언트 peak RSS 35,588 KiB, 압축 번들 20,186 bytes.
  이는 한 로컬 환경의 파싱/저장 측정이며 실제 AI 품질·수만 report 전체 분류 처리량 보증이 아니다.
- 결과는 실행 시 출력하는 `/tmp/sanity-mock-pg-*/summary.json`, `tests.json`, `tests.log`, `scale.json`과
  demo별 보고서에 보존한다. 운영 SQL 원본·실제 `.env`·사내 DB는 변경하지 않았다.
  이번 검증 범위는 DB/Python API이며 브라우저 E2E는 다시 실행하지 않았다.

## 2026-10-14 psycopg2 직접 접속 · 환경변수 설정

- 사용자 확인에 따라 기본 DB 모드를 `postgresql`로 전환하고 드라이버를 `psycopg2-binary 2.9.13`으로 변경했다.
  `SANITY_DB_HOST/PORT/NAME/USER_NAME/PASSWORD`, 스키마·TLS 설정을 비공개 env에서 읽는다.
  해당 side의 기존 DSN 우선순위는 유지하고 HTTP/다른 계정/낮은 TLS 수준으로 자동 재시도하지 않는다.
- `common/sanity_postgres.py`에 커서 실행, 외부 트랜잭션 보존 및 중첩 savepoint,
  UUID/JSONB/BYTEA 변환을 연결했다. named cursor와 짧은 트랜잭션·세션 advisory lock을 유지한다.
  트랜잭션/커서 동작과 타입 등록은 [Psycopg 공식 연결 문서](https://www.psycopg.org/docs/connection.html)와
  [공식 타입 확장 문서](https://www.psycopg.org/docs/extras.html)를 참고했다.
- `common/sanity_db.py check --side vwp|oa`는 연결·설치된 스키마·유효 권한을 읽기 전용으로 확인한다.
  DSN/비밀번호/드라이버의 서버 상세 오류를 출력하지 않는다. UI에 DB 자격증명을 전달하지 않는다.
- PostgreSQL 16.15의 전용 로컬 소켓 DB와 서로 다른 제한된 앱 로그인으로
  `run_tests.py --require-db`: **160개 통과, skip 0개**.
  추가 13개 테스트는 필드 기반 양쪽 로그인, 환경변수 우선순위/무보간, 특수문자 비밀번호 인자 전달,
  오류 비노출/재시도 금지, 비밀번호 마스킹, UUID·한글 JSONB·binary roundtrip,
  중첩/외부 rollback, read-only 격리와 서버 측 커서를 포함한다.
- `pip check`, 구문 검사, `git diff --check` 통과. 기존 form·`002`·`003` 변경 없음.
  실제 사내 DB/TLS/계정 권한은 검증하지 않았고 실제 `.env`나 사내 접속값은 수정·출력하지 않았다.
  이번 변경은 Python DB 계층과 설정/문서/테스트 범위이며 프런트엔드 빌드·브라우저 테스트는 다시 실행하지 않았다.

## 2026-10-13 VWP AI 분류 루프 · OA 조작 · 원문 조회

- `vwp_bridge/sanity_ai.py`에 OpenAI 호환 Chat Completions/Responses와 선택 CLI 어댑터를 연결했다.
  공식 요청/응답 형식을 확인했지만 실제 사내 endpoint/model을 호출하지 않았다. 기본 disabled,
  명시적 설정·strict JSON·근거 ID·크기/TLS/timeout 검사, HTTP 로그 억제, 자동 재시도/CLI 우회 금지를 적용했다.
- VWP 작업자는 전체 기록 스캔/최대 64건 표본 → AI 선언형 규칙 제안 → 전체 기록 적용 → 분할 전송을 수행한다.
  AI 호출 동안 트랜잭션을 열지 않고 lease를 갱신한다. 조각/체크포인트를 원자 저장하며 불확실한 AI 호출은 재호출하지 않는다.
- OA에 작업/조각/구성원/분류 개정/규칙 snapshot을 연결했다. 누락·중복·해시·건수·개별 판정/소속 재계산을 검증한다.
  초기 규칙 없음, 다음 run의 규칙 재사용, 자연어 피드백, 적용/후보 판단/확정/재사용 저장을 별도 동작으로 구현했다.
  늦은 결과·기대 개정 충돌·오래된 직접/간접 그룹 참조는 적용/확정을 막는다. 기존 sanity 보고서/검토 개정은 수정하지 않는다.
- React/shadcn AI 분류 탭은 규칙 JSON 편집, 작업·구성원 페이지, 이전 소속 변화, 모든 FAIL 연관 후보·원문 요청을 제공한다.
  후보 연관 확인은 실제 소속 변경이나 개별 FAIL 판정이 아니다. PASS 기록에도 후보를 표시하며 미해결 후보는 분류 확정을 막는다.
- 일반 원문 응답의 요청별 수동 승인을 제거했다. 범위·TTL·봉인·원문 해시 검증은 유지하며 자격증명 마스킹/원본·전송본 해시를 기록한다.
  OA는 4,000자 미리보기와 기본 8,000자 전체 상세 페이지를 제공한다. 등록 비밀값/명시적 패턴 검사이지 범용 DLP는 아니다.
- 별도 로컬 PostgreSQL 16.15, 제한된 두 앱 계정으로 `run_tests.py --require-db`: **147개 통과, skip 0개, 73.729초**.
  새 테스트에는 270건/3조각 중단 후 재개, AI 불확실 결과 재호출 차단, heartbeat 만료/재점유,
  최종 메시지 선도착, 조각 위조 rollback, 임의 규칙 근거 거부, 실행 간 동일 그룹/후보/참조 변경,
  원문 자격증명 마스킹·private 바이트 보존, 인증/고정 검토자/상세 원문 범위 검사를 포함한다.
  API 형식은 HTTPX MockTransport, 모델 제안은 명시적 FakeAI를 사용했다. 실제 AI 정확도 검증이 아니다.
- TypeScript/Vite build와 `pip check`, `compileall`, `git diff --check` 통과.
  브라우저 기본/분류 회귀 **10개 통과, 25.3초**. 제안/적용/확정/규칙 저장 분리와 모바일 화면도 확인했다.
  동시에 진행된 별도 GCA 데모 변경은 보존했다. 공유 Playwright 결과 폴더 충돌이 있어
  `/tmp/sanity-cluster-browser.kh65to/`의 별도 결과 폴더와 5187 포트로 `e2e/dashboard.spec.ts`를 재검증했다.
  GCA 전용 테스트 성공을 이 기록으로 주장하지 않는다.
- 기존 신청 form/002/003은 바이트 단위로 변경하지 않았다. 새 분류 스키마에는 `004_clustering.sql` 적용이 필요하다.
  사내 DB에 접속/DDL 실행하거나 실제 `.env`의 AI 값을 설정하지 않았으며 Git commit/push도 하지 않았다.
- 남은 범위: 실제 사내 API/DB/report 검증, 새 파서 생성, 의미 기반 후보 검색·독립 FAIL 태그 정책,
  임의 구성원 이동/그룹 lineage, 수만 report의 성능 검증, 운영 SSO/RBAC. 현재 후보 비교는 정확한 필드 대조이고 scope당 최대 100그룹이다.
  최초 분석 번들의 전체 그룹 목록은 여전히 기존 메모리/64MiB 전송 제한을 가진다.

## 2026-10-13 신청 원본 보존 · 분류 DB 증분 변경

- 사용자가 최초 form으로 신청을 완료했다고 확인했다. `db/form/` 4개와 기존
  `002_indexes.sql`·`003_grants.sql`은 Git 기준 원본과 바이트 단위로 동일하게 유지했다.
  향후 실수로 신청 원본을 변경하면 실패하도록 SHA-256 고정 검사를 추가했다.
- `db/004_clustering.sql`을 별도 작성했다. 테이블 4개를 유지하고 OA 컬럼 2개와
  작업/분류 개정의 CHECK·인덱스를 추가한다. 기존 보고서·근거·메시지·판정 개정은 수정하지 않는다.
  기존 CHECK 식을 PostgreSQL 파서로 비교해 GUI 제약 이름 차이를 허용하되 정의 불일치는 거부한다.
  전체 DDL은 한 트랜잭션이며 5초 잠금 대기 제한을 둔다. 실제 적용은 작업 중지·백업 확인 후 owner/DBA가 수행한다.
- 별도 로컬 PostgreSQL 16.15 DB에서 기존 행이 있는 최초 스키마에 migration을 적용했다.
  기존 데이터·테이블 OID 보존, 제약 이름 변경, 정의 불일치 시 전체 rollback,
  재실행 실패 시 보존, 기존 원문 해시/메시지 제약과 제한된 앱 권한을 확인했다.
- `run_tests.py --require-db`: **124개 통과, skip 0개, 30.783초**.
  이후 추가한 원본 보존 검사를 포함한 `test_schema.py`: **4개 통과**.
  `git diff --check` 및 기존 form/002/003의 변경 없음 검사도 통과했다.
- 이 기록은 DB 기반과 기존 기능 회귀 검증이다. 작성 중인 `common/sanity_clustering.py`·`vwp_bridge/sanity_ai.py`는
  아직 작업자/API/화면에 연결되지 않았으며, 전체 AI 분류 루프나 실제 사내 AI 호출 완료를 의미하지 않는다.
  사내 DB에는 실행하지 않았고 Git commit/push도 하지 않았다.

## 2026-10-12 Jira 폐지 · OA 대시보드 전환

- 아직 테이블 신청 전이라는 사용자 확인에 따라 신규 신청 SQL 자체를 정리했다.
  4개 테이블은 유지하며 OA의 Jira 작업 종류, 전용 컬럼 9개, 인덱스 5개와 갱신 권한을 제거했다.
  실제 생성 결과는 VWP private 14 / OA private 16 / VWP→OA 21 / OA→VWP 18 컬럼,
  총 69컬럼·인덱스 23개다. 신청은 `db/form/` 4개, 후속 설정은 `002`와 `003`만 사용한다.
- Jira 조회·게시·DB 게시 시뮬레이터를 폐지했다. 예전 publish 호출은 모든 옵션에서
  `JIRA_DISABLED`로 실패하며 새 보고서에는 Jira 후보가 없다. 과거 보고서 바이트 검증용 렌더러만 유지한다.
  사용하지 않는 `examples/jira_context.json`을 삭제했다. 삭제본·문서·이전 OA 코드는
  `/tmp/sanity-dashboard-change.JaBp6a/before-dashboard.tar.gz`에 보존했다(`/tmp` 정리 전 복구 가능).
- React·실제 shadcn/ui 구성 요소와 로컬 FastAPI를 `oa/`에 추가했다.
  토큰은 브라우저 메모리에만 보관하며 서버 고정 검토자를 사용한다. 모든 업무 API는 인증이 필요하고
  호스트·본문 크기·입력 형식 검증과 비밀정보 없는 오류 응답을 적용한다. UI에는 DB 자격증명이 없다.
- 기존의 전체 inventory·coverage·블로커·알려진 FAIL 보호와 원자적 개정/보고서 저장을 사용한다.
  기대 개정이 달라진 검토는 거부하며 같은 UUID의 동일 요청만 재시도한다.
  현재 보고서 손상이 새 검토 개정으로 가려지지 않도록 변경 전 바이트 검증도 추가했다.
  추가 근거는 DB 큐로만 요청하고 VWP가 승인한 응답을 검증한 뒤 읽는다. 미리보기는 원문별 4,000자로 제한하고 잘림을 표시한다.
- 로컬 PostgreSQL 16.15에서 **119개 테스트 모두 통과, skip 0개**(36.072초).
  이 중 새 API 테스트 13개는 인증·HTTP 기본 모드 차단·입력 한도·서버 검토자·개정 충돌·동시 제출·
  보고서 손상·근거 반출 승인 경계·FULFILLED 미리보기 제한/재검증을 포함한다.
  새 form으로 실제 테이블 생성, SQL 권한 smoke, 서로 다른 두 세션의 SKIP LOCKED 점유도 통과했다.
- 주 에이전트가 실제 PostgreSQL + 로컬 API + Chromium으로 로그인, 사람 판정 2건 저장,
  review COMPLETE / check FAIL 구분, 보고서 다운로드, VWP 승인 후 근거 수신/조회,
  새로고침 뒤 DB 상태 유지·토큰 재로그인, 데스크톱/모바일을 확인했다. 브라우저 실행 오류는 없었다.
  합성 검증 자료는 `/tmp/sanity-dashboard-browser.cYFGNZ/`이며 사내 DB에는 접속하지 않았다.
- 프런트엔드 TypeScript/Vite 빌드와 **Chromium 브라우저 테스트 8개 모두 통과**(주 에이전트 재실행 20.2초).
  명시적 데모, 토큰 비저장, 전체 대장·판정 기준, 검토/개정/이력, 추가 근거와 다운로드,
  API 실패·읽기 전용·UUID 재시도·번들/페이지 전환의 이전 자료 차단, 390px 모바일을 검증했다.
  데스크톱·모바일 합성 화면을 직접 확인했고 본문 대비를 보완했다.
  화면 캡처는 로컬 `oa/dashboard/artifacts/`에 있으며 Git에는 포함하지 않는다.
  Python 의존성 검사와 npm 운영 의존성 audit도 통과했다.
- 기본 DBaaS 방식은 여전히 임시 읽기 전용이고 정식 전체 접근 방식은 사용자 제공 대기 중이다.
  데모는 명시적 합성 메모리 화면이며 API 오류 시 전환하지 않는다. 실제 AI는 NOT_CONFIGURED다.
  Sites의 로컬 미리보기 경로만 사용했으며 외부 사이트 등록·배포는 하지 않았다.
  현재 인증은 loopback 단일 사용자 prototype으로 운영 SSO/RBAC·TLS·대규모 성능 검증을 대신하지 않는다.
  검증용 API·Vite·PostgreSQL 서버는 작업 완료 후 종료했다.

## 2026-10-12 GUI 신청 경로로 통일

- GUI 신청에 필요 없는 직접 생성 SQL과 이전 설치 보정 SQL을 삭제하고 실행 안내에서 제외했다.
  `db/form/`의 4개 컬럼 정의 → `002_indexes.sql` → 역할 준비 → `003_grants.sql`만 사용한다.
  `db/tests/`는 개발 검증용임을 명시했다. 삭제본은 `/tmp/sanity-db-gui-only.9q1mqH/before-cleanup.tar.gz`에 백업했다.
- 제거한 SQL 전용 테스트 3개를 없애고, 신청 파일 4개와 후속 SQL 2개의 구성을 검사하도록 수정했다.
  fixture는 GUI처럼 컬럼 정의를 감싸 로컬 테스트 테이블을 만든다. 사내 테이블·데이터는 변경하지 않았다.
- 로컬 PostgreSQL에서 **108개 모두 통과, skip 0개**(33.033초). GUI 생성 모사·권한 smoke·두 세션 동시성 검사도 통과했다.

## 2026-10-12 운영 테이블 4개 전면 신규 생성 — 이전 검증 기록

- 당시 기존 테스트 테이블 재사용을 중단하고 직접 생성용 SQL도 제공했다. 이후 GUI 신청으로 통일하며 해당 SQL은 제거했다.
  이전 변경 SQL·전용 변경 검사·8컬럼 샘플은 제거했다. 원본은
  `/tmp/sanity-db-create.rNwaPo/before-create-change.tar.gz`에 보존했으며 `/tmp` 정리 전까지 복구할 수 있다.
- 당시 두 생성 경로와 예전 설치 보정을 안내했으나 현재는 GUI 생성 후 인덱스/FK·권한 설정만 따른다.
- GUI 컬럼 정의 파일과 편집기 복구 파일은 변경하지 않았다. 당시 직접 생성 SQL은 기존 테이블을 갱신/삭제하지 않고,
  이름 충돌 시 오류로 중단한다. 생성 4개는 하나의 트랜잭션이며 부분 생성은 rollback한다.
- 로컬 PostgreSQL 16.15에서 `run_tests.py --require-db` **111개 모두 통과, skip 0개**(35.523초).
  GUI/SQL 정의 일치, 트랜잭션 구조, 기존 테이블/행 보존, 마지막 테이블 충돌 시 앞선 생성 rollback을 추가 검증했다.
- 당시 직접 생성·인덱스·권한 SQL 실행 및 권한 smoke, 두 세션 동시성 검사도 통과했다.
  Python/shell 구문 검사 및 문서 로컬 링크 54개 확인도 통과했다. 사내 DB에는 접속하지 않았다.

## 2026-10-12 DB 파일 정리 및 경로 통합

- 현재 4개 테이블의 SQL·신청용 컬럼·테스트를 `db/` 바로 아래로 통합했다.
  이전 신청안의 SQL/컬럼 조각과 전용 테스트 8개를 삭제하고, 관련 문서의 이전 실행 안내도 제거했다.
  당시 기존 8컬럼 샘플을 변경 검증용으로 보존했으며, 이후 전면 신규 생성 전환과 함께 제거했다.
- 코드·문서·테스트의 경로와 SQL 객체 명칭을 갱신했다. 현재 4개 컬럼 정의와 권한 SQL은 이동 전 내용과 동일하다.
  기존 DB 재생성을 요구하지 않도록 앱은 필수 UNIQUE 인덱스의 이름 대신 대상 테이블·키·조건을 확인한다.
- 별도 로컬 PostgreSQL 16.15에서 `run_tests.py --require-db` **107개 모두 통과, skip 0개**(33.663초).
  기존 이름 허용, 필수 인덱스 누락 거부, 같은 이름의 잘못된 키/조건/테이블/비고유 인덱스 거부 검사를 추가했다.
- 새 `db/tests/` 경로의 생성·권한 smoke, 기존 테이블 변경 보호, 두 세션 동시성 검사를 모두 통과했다.
  문서 내 로컬 링크 54개(대상 21개), Python 구문, shell 구문, 의존성 검사도 통과했다.
- 사내 DB·운영 데이터와 임시 읽기 전용 HTTP 접근 설정은 변경하지 않았다.
  삭제 전 파일은 `/tmp/sanity-db-cleanup.Ir5C3T/before-cleanup.tar.gz`에 백업했고,
  발견된 편집기 복구 파일도 같은 백업 폴더에 별도로 보존했다. `/tmp` 정리 전까지 복구할 수 있다.

## 2026-10-12 접근 방식 정정 — 임시 읽기 전용

- 사용자가 `dbaccess.md`의 HTTP API는 임시 읽기 전용이며 정식 전체 접근 방식은 별도로 제공한다고 확인했다.
- 원본 접속 안내에 상태를 추가하고 요구사항·프로토콜·실행/DB 신청 안내의 전체 HTTP 저장 전환 가정을 정정했다.
  기존 인증 예시와 사용자 swap 파일은 그대로 보존했다.
- 현재 API를 쓰기·트랜잭션용으로 확장하지 않는다. 정식 방식이 제공되면 그 방식에 맞춰 저장 경로를 갱신한다.
- 이번 변경은 문서뿐이며 실행 코드·설정 기본값·4테이블 설계는 변경하지 않았다.
  테스트는 다시 실행하지 않았고 아래 104개 통과 기록은 이전 구현 검증 결과다.

## 2026-10-12 dbaccess.md 기반 HTTP 접근 보완

- 현장 경로가 DBaaS HTTP API임을 확인해 `common/sanity_dbaas.py`, `common/sanity_config.py`, `.env.example`을 추가했다.
  실제 URL/토큰/CA 경로는 만들거나 출력하지 않았으며 사용자가 작성한 `dbaccess.md`와 swap 파일은 수정하지 않았다.
- Bearer 인증, CA/호스트 검증(PEM/DER), proxy 환경 무시, timeout 30초, redirect 거부,
  HTTP 상세 로그/오류 본문 비노출, 요청·응답 크기 제한, POST 결과 불명 시 무재시도를 구현했다.
- 문서의 DBASS_BASE_URL 오기는 DBAAS_BASE_URL의 별칭으로 지원한다. `/api/vi/me`는
  `/api/v1/me`로 해석했으며 실제 경로 확인이 필요하다고 명시했다.
- 기본 접근 모드는 dbaas_http다. 전체 저장 API의 SQL·트랜잭션·세션·권한 계약이 없으므로
  PostgreSQL 세션을 독립 HTTP 요청으로 대체하지 않았다. 해당 모드는 DBAAS_SQL_CONTRACT_REQUIRED로 중단한다.
  기존 실제 PostgreSQL 회귀 검증은 명시적인 postgresql 모드로 유지한다.
- `tests/test_dbaas.py`의 모의 HTTP 테스트 20개를 통과했다. 실제 서비스에 요청하거나 실제 자격증명을 쓰지 않았다.
  상세 범위는 헤더/경로/timeout, 인증 오류, CA 오류, PEM/DER, redirect, proxy 무시,
  dotenv 우선순위, 비밀 비노출, JSON·크기 제한, 재시도 금지, 직접 DSN fallback 차단이다.
- 변경 후 로컬 PostgreSQL을 포함한 `run_tests.py --require-db`는 **104개 모두 통과, skip 0개**(31.993초)했다.
  이 중 HTTP 20개는 mock이며 나머지는 직접 PostgreSQL/순수 회귀 검증이다.
  의존성 및 구문 검사도 통과했다. 설정 없이 접속 확인 명령은 DBAAS_NOT_CONFIGURED로 안전하게 종료했다.
- 당시 전체 저장 파이프라인의 HTTP 전환은 미완료였다. 이후 사용자 확인으로 HTTP는 임시 읽기 전용으로 정정했다.
  정식 전체 접근 방식의 반영 대기 상태는 requirement 20.3절에 기록했다.
  아래 84개/성능 검증은 직접 PostgreSQL 구현의 기록이며 실제 DBaaS 지원 증거가 아니다.

## 2026-10-12 PostgreSQL 4개 테이블 런타임 전환

이 절은 직접 PostgreSQL 실행 코드의 검증 기록이다. HTTP 전환 상태는 맨 위 절을 기준으로 한다.
아래 SQLite/16테이블 기록은 당시 이력이며 현재 실행 방식이 아니다.

### 반영 범위

- 두 브리지의 저장과 전달을 psycopg/PostgreSQL로 전환했다. SQLite import/로컬 상태 DB/peer inbox 전달은 실행 경로에서 제거했다.
- VWP는 PASS 포함 전체 근거를 배치 저장하고 같은 분석 재실행 시 전체 불변 내용을 대조한다.
  봉인/번들 등록은 원자적이며 봉인된 근거를 수정하거나 자동 수리하지 않는다.
- OA 수신본·전체 단위·개정 0·보고서·수신 확인을 같은 트랜잭션으로 확정한다.
  명시적 review UUID로 요청 재시도와 새 판단을 구분하고 개정별 불변 보고서를 저장한다.
- 추가 근거 요청·승인/거부/만료 응답도 DB 큐만 사용한다. 명시적 VWP 승인 없이는 전체 원문을 내보내지 않는다.
- 앱은 상대 private 테이블·불변 컬럼에 접근하지 않는다. 실제 유효 컬럼 권한, schema CREATE,
  owner/관리자/상대 역할 상속을 검사하고 불일치하면 실행을 중단한다.
- 실제 Jira/AI는 미구현이다. 게시에는 `--simulate`가 필요하며 DB_SIMULATOR/LOCAL ID로 명확히 표시한다.
- requirements, demo, 환경별 README, PROTOCOL, requirement 19절, 신청 안내를 같은 구조로 갱신했다.
  기존 사용자 결과 폴더·SQLite 파일은 열거나 삭제하지 않았다.

### 확인·수정한 문제

- JSONB 왕복 후 객체 키 순서가 달라 Jira 조건 차이 보고서가 불일치할 수 있었다.
  구조화된 조건 차이를 정렬 직렬화하도록 수정하고 저장 bytes와 재렌더링 비교를 검증했다.
- 빈 오류 행의 0바이트 근거를 이전 CHECK가 거부했다.
  현재 컬럼 정의에 0바이트 허용을 반영했다. 당시 기존 데이터 보존 및 보정 SQL 반복 적용도 검증했으며,
  신규 GUI 신청으로 통일한 뒤 별도 보정 SQL은 제거했다.
- 테이블 SELECT 권한 검사만으로는 직접 부여된 단일 컬럼 권한을 놓친다.
  유효 SELECT/INSERT/UPDATE 컬럼 집합과 DELETE/TRUNCATE/REFERENCES/TRIGGER 권한을 검사하도록 보완했다.
- 잘못된 타입의 추가 근거 응답을 집합 연산 전에 검증해 TypeError로 처리 작업이 방치되지 않게 했다.
  전체 근거 조회 전 예상 바이트 크기를 확인하고, 최종 gzip 전후 크기도 제한한다.

### 실행 검증

환경: Python 3.12, psycopg 3.3.6, PostgreSQL 16.15.
별도 `sanity_test_*` 로컬 DB와 VWP/OA의 서로 다른 제한된 LOGIN을 사용했다.
서버는 Unix socket 전용이며 사내/운영 DB에는 접속하지 않았다.

- `run_tests.py --require-db`: **84개 테스트 모두 통과, skip 0개**(31.948초).
  DB 미설정 실행에서는 순수 테스트 22개만 실행하고 PostgreSQL 의존 테스트는 명시적으로 skip한다.
  `--require-db`는 설정 누락 시 종료 코드 2로 실패함을 확인했다. 의존성 검사와 Python 구문 검사도 통과했다.
- 이전 파서/Decimal/동일성/목록 누락/검토 완료 기준과 Jira 후보 상한을 유지했다.
  SQLite VACUUM/파일 손상/SQLite 레거시 스키마 이전 전용 테스트는 현재 구조에 맞는
  PostgreSQL 재실행/손상 보존/트랜잭션 rollback 검증으로 대체했다.
- 실제 계정으로 private 영역 격리, 발신자의 ACK 변경 금지, 수신자의 payload 수정 금지,
  관리자/과도한 컬럼/schema CREATE 권한 거부를 확인했다.
- 동시 두 작업자의 서로 다른 메시지 점유, 만료 토큰 갱신 거부·재점유,
  수신 ACK 실패 시 OA 기록 전체 rollback 후 복구를 확인했다.
- 동일 검토 UUID 동시 제출은 개정 하나만 생성했다. UUID 내용 충돌 거부, A→B→A 3개 개정,
  dry-run 무변경, 보고서/게시 실패 rollback, UNCERTAIN 무조건 재시도 거부를 확인했다.
- 근거 요청 재시도, 명시적 승인/거부, 만료, 일부 근거 누락 시 전체 거부,
  위조 응답 거부, 응답 재수신 및 OA 로컬 파일 저장을 확인했다.
- complete/missing/malformed/unsupported demo가 DB로 이어지고 예상 3개 단위를 유지했다.
  자동 사람 승인은 하지 않으며 complete 입력에서도 review=INCOMPLETE/check=FAIL이다.
- 최신 컬럼 정의로 기존 테스트 테이블 재사용 fixture, DDL smoke, 안전한 ALTER 거부 조건,
  신규/ALTER 21컬럼 일치, 실제 두 psql 세션 SKIP LOCKED 검사를 다시 통과했다.

### 합성 성능 측정

`tests/benchmark_postgresql.py`를 같은 제한 계정과 별도 임시 스키마에서 실행했다.

| 합성 CSV 기록 | 군집 | 분석·DB 등록 시간 | 클라이언트 프로세스 peak RSS | 압축 번들 |
|---|---:|---:|---:|---:|
| 10,000 | 100 | 4.156초 | 208,912 KiB | 20,186 bytes |
| 100,000 | 100 | 85.324초 | 207,128 KiB | 20,156 bytes |

두 경우 모두 EVIDENCE 건수와 군집 수를 실제 DB에서 대조했다. 모든 측정 기록은 합성 FAIL이다.
RSS는 fixture 준비를 포함한 클라이언트 전체 프로세스의 고수위이며 DB 서버 메모리를 포함하지 않는다.
단일 로컬 환경의 한 번 측정이고, 10만 건에서 처리 시간이 선형으로 증가하지 않았다.
사내 네트워크·WAL/인덱스·고유 군집 수·최대 행 크기별 성능 튜닝과 운영 규모 검증은 남아 있다.
최종 그룹 목록과 초대형 입력 행의 엄격한 메모리 상한을 보장한다는 의미가 아니다.

실제 DB 신청/GUI 변경/계정/TLS/백업·보존 정책, 실데이터, 검토자 인증, 내부 AI와 Jira는 미검증이다.
앱은 기존 SQLite/16테이블 상태를 자동 이전하지 않는다. 반출 승인 시스템 연동과
DB 원문 보관 금지 환경용 local_evidence_ref 어댑터도 후속 과제다.


## 2026-10-12 이전 4개 테이블·기존 테스트 테이블 재사용 검증

- 당시 사용자가 운영 테이블 4개와 기존 테스트 테이블 1개 재사용을 선택했다. 이후 전면 신규 생성으로 변경했다.
  현재 적용 파일은 [db/README.md](db/README.md)의 신규 생성/인덱스/권한 SQL이다.
  이전 신청안의 SQL과 전용 테스트는 저장소 정리 과정에서 제거했다.
- PostgreSQL 16.15의 전용 로컬 테스트 DB에 기존 8컬럼 테스트 테이블을 생성하고 이름 변경을 모사한 뒤,
  실제 `ALTER TABLE`로 `sanity_vwp_to_oa` 21컬럼으로 확장했다. 나머지 3개 신청용 조각도 실제 생성했다.
  결과는 테이블 4개, 컬럼 78개(14/25/21/18), FK 2개, 인덱스 28개(명시적 21개 + PK/UNIQUE 자동 7개)다.
- [smoke.sql](db/tests/smoke.sql): 실제 앱 역할로 양방향 등록·조회·상태 갱신,
  비공개 영역 격리, 송신자의 상태 주입 금지, 수신자의 원문 수정 금지, 근거/검토 이력 불변,
  중복 bundle/개정 거부, 바이트 해시·크기, 같은 분석 parent 및 요청/응답 scope,
  JSON NULL 우회 거부, 점유 만료·이전 token 거부, A→B→A 이력 및 Jira UNCERTAIN 저장을 확인했다.
- 당시 변경 SQL 검사(신규 생성 전환 후 제거): 이름 미변경, 기존 합성 행이 있는 경우,
  예상 밖 컬럼, 변경 SQL 재실행을 거부했다. 중단 시 합성 행과 원래 8컬럼이 유지됐고,
  성공 시 테이블 OID가 유지됐다. 신규 생성용 조각과 ALTER 결과의 컬럼 타입/NULL/기본값이 일치했다.
- [check_concurrency.py](db/tests/check_concurrency.py): 실제 두 psql 세션에서 첫 세션이 행 잠금을
  유지하는 동안 다른 세션이 SKIP LOCKED로 다른 행을 점유함을 확인했다.
- 위 검사는 모두 최종 통과했다. smoke 행은 rollback했고, guard/동시성 검사가 직접 생성한
  식별 가능한 합성 행만 테스트 DB에서 정리했다. 기존 사용자/운영 DB 데이터는 접근하거나 변경하지 않았다.
- 테이블 축소에 따라 일부 parent 종류·단위 연결·검토 개정/보고서 참조는 DB FK가 아닌 앱 검증 책임이다.
  이 차이를 requirement.md 19절 및 신청 안내에 명시했다. 이 SQL만으로 전체 검토 완료를 보증하지 않는다.
- 이 DDL 작성 시점에는 Python 어댑터가 미구현이었다. 현재 런타임 검증은 맨 위 절을 참조한다.

## 이전 DB 신청안 검증 기록

16개 테이블 및 단일 번들 신청안은 당시 로컬 PostgreSQL에서 검증했으나 현재 구성은 아니다.
사용자 요청으로 해당 SQL·신청용 조각·전용 테스트를 제거했으며, 현재 DB 파일은 [db/README.md](db/README.md)만 따른다.
아래 SQLite 프로토타입 검증도 이전 이력이며 현재 PostgreSQL 실행 검증과 구분한다.

## 이전 프로토타입 구현

세 sub-agent가 각각 `vwp/`, `vwp_bridge/`, `oa/`를 구현했다.
부모 에이전트가 공통 데이터 계약·검토 완료 검사·통합 실행을 구현하고, 각 환경 코드를 읽어
입력 계약, 판정, 동일성, 누락 방지, 재수신·재게시 및 사람 검토 경계를 검토했다.

## 검토 중 수정한 사항

- 수치의 부동소수점 반올림으로 경계 위반이 PASS가 될 수 있어 `Decimal`로 비교한다.
  임계값도 정수 또는 정확한 십진 문자열만 허용한다.
- snapshot 명세에 없는 실제 파일·symlink를 검출하고, 수집 후 입력 변경을 확인한다.
- OA는 전송된 blocker 목록과 별도로 원본 report 누락·예상 밖 파일을 확인하며,
  사람 검토 명령도 이 공통 검사 결과를 사용한다.
- summary report의 필수 객체·검사 지점 누락을 정상 판정하지 않는다.
- 보고서에 전체 판정 건수, 적용 규칙·임계값, 논리 파일명·해시·JSON Pointer를 표시한다.
- 그룹별 FAIL/UNKNOWN 합계가 단위의 개별 판정 건수와 일치하는지 검증한다.
  미분류 기록을 빈 식별자끼리 병합하지 않는다.
- 새로운 결과 번들에는 이전 사람 승인을 상속하지 않으며, FAIL의 검토 완료와
  sanity PASS를 구분한다.

## 2026-09-23 전체 점검 후 수정

전체 점검에서 확인한 7개 문제를 수정하고 각각 회귀 사례를 추가했다.
VWP·OA 구현은 해당 작업자에게 분리했고, 부모 에이전트가 공통 수신 검증·통합 테스트·문서를
수정했다. 별도의 읽기 전용 검토자들이 VWP·OA·공통 경계를 다시 확인했다.

| 문제 | 수정 및 검증 |
|---|---|
| 동일 본문을 과거 게시 전체에서 중복 제거해 `FAIL → PASS → FAIL`의 마지막 판정을 누락 | 영속 검토 이력 ID를 게시 개정으로 사용. 같은 개정 재시도만 중복 제거. 본문이 같은 첫·마지막 FAIL의 별도 게시 확인 |
| 기존 근거 DB가 손상되거나 기록이 삭제되어도 재분석 성공 | SQLite 무결성·스키마·전체 records/groups/metadata 대조. 변경·손상·symlink는 `EVIDENCE_CONFLICT`로 거부하고 원본 보존. VACUUM 후 정상 재사용 확인 |
| 숫자·배열·객체인 객체명이 같은 문제로 병합 | 공통 타입 규칙을 생산·수신 양쪽에 적용. 잘못된 식별값은 기록별 `UNDECIDED` 격리 |
| Jira 후보가 그룹 수×이슈 수만큼 증가 | 그룹당 5개·전체 1,000개 제한과 결정적 우선순위. 전체·그룹별 일치/반환/생략 수 및 후보가 전부 생략된 그룹 표시 |
| 5,000자리 CSV 종료 건수가 예외를 발생시켜 전체 출력 누락 | 정수 변환 없이 표준 십진 문자열 비교. `TOTAL_MISMATCH`를 가진 부분 결과 및 근거 유지 |
| 다른 item의 report를 근거로 연결해도 수신 성공 | 예시의 file_id가 가리키는 source.path와 해당 단위의 report 일치 강제 |
| 선언된 식별 필드 일부가 빠져도 동일성 확정 가능 | `SAME_FINDING`에 비어 있지 않은 중복 없는 선언, 정확히 같은 필드 집합, 유효한 타입 요구 |

기존 OA 게시 테이블은 다음 쓰기 트랜잭션에서 자동 이전한다. 게시 ID·본문·시각과 검토
이력을 보존하고, 가장 최근 게시만 현재 개정에 연결할 수 있다. 조회·dry-run의 DB 바이트
불변 및 이전 중 게시 실패 시 원래 스키마까지 rollback되는 것을 검증했다.
호환되지 않는 과거 번들은 자동 보정하지 않고 그대로 거부하지만, 그 번들이 다른 정상
번들의 이전과 게시를 막지는 않도록 했다. 사용자의 기존 실행 결과 DB는 이 작업에서
직접 이전하거나 삭제하지 않았다.

최종 재검토에서 중첩된 Jira 비교 조건이 후보마다 반복 저장될 수 있는 경로도 발견했다.
원본 비교와 표시값을 분리해 중첩 값은 제한된 요약으로 남기고 생략 여부를 표시한다.
후보 생략·값 요약은 원인 확정이나 사람의 검토 완료로 취급하지 않는다.

## 실행 검증

환경: Linux, Python 3.12.3. 최종 코드에서 `python3 run_tests.py`로 **65개 테스트 통과**.
구성은 공통 12개·통합 3개, VWP dev 8개, VWP bridge 21개, OA bridge 21개다.
`python3 -m compileall -q common/sanity_common.py devtools/demo.py run_tests.py tests vwp-dev vwp-bridge oa-bridge`도 통과했다.

확인한 주요 시나리오:

- CSV와 JSON report의 전체 흐름, 같은 코드의 대상별 분리와 같은 대상 반복 집계.
- 원문 근거 DB에 PASS를 포함한 모든 개별 기록 보존.
- 정상 입력, report 누락, 종료 표시 누락, 미지원 형식에서 검토 분모 유지.
- 해시 손상, JSON 중복 키, 경로 탈출, 압축 해제 크기 초과, 예상 목록 불일치 거부.
- 동일 수신·게시 재시도의 중복 방지, dry-run 상태 불변, 로컬 게시 중단 후 rollback.
- 사람 확인 전 미완료, 충분한 FAIL 검토 후 `COMPLETE`와 `FAIL` 동시 유지.
- 관련 Jira는 후보로 표시하고 AI는 `NOT_CONFIGURED`로 유지.

통합 테스트는 각 시나리오의 임시 디렉터리에서 실제 세 단계 CLI를 실행하고 보고서·근거 DB를
검사한다. 체크섬과 내용 ID까지 유효하게 만든 잘못된 근거 연결·불완전 식별자 번들도
OA CLI가 `INVALID_SCHEMA`로 거부하며 상태 디렉터리를 만들지 않는지 확인한다.
기존 로컬 예시 `demo-output-reviewed/report.md`는 덮어쓰지 않았다. 이 생성물은 Git에 포함하지 않는다.

## 합성 대용량 측정

이번 수정의 Jira 크기 회귀 테스트는 검증 가능한 500개 그룹과 200개 이슈를 실제 OA 수신·
저장·재조회·보고서 경로에 넣었다. 조건에 20,000자 중첩 객체와 약 2MB 배열을 포함해도
100,000개 일치 쌍 중 1,000개만 후보로 보관하고 99,000개 생략을 표시했으며, 직렬화한
Jira·AI 맥락은 1,000,000바이트 미만이었다. 이는 해당 합성 입력의 검증 결과이며 모든 입력의
메모리·소요 시간 보장은 아니다.

아래 수치는 최초 프로토타입 검증 당시의 측정이다. 이번 수정 후 성능 재측정값은 아니다.
수치 CSV에서 100개 객체가 반복되는 입력을 사용했다. 25% PASS, 75% FAIL이며,
FAIL 대상 75개가 각각 문제 그룹이 된다. VWP bridge 분석부터 전송 번들 생성까지 측정했다.

| 데이터 행 | 전체 판정 | 문제 그룹 | 소요 시간 | 자식 프로세스 최대 RSS |
|---|---|---|---|---|
| 10,000 | 10,000 | 75 | 1.069초 | 24,124 KiB |
| 100,000 | 100,000 | 75 | 11.034초 | 24,820 KiB |

RSS는 Linux `getrusage(RUSAGE_CHILDREN)`의 실행 누적 최대값이다. 단일 실행 측정이며
테스트와 병행한 개발 환경 수치이므로 운영 성능 보장이 아니다. 고유 그룹 10만 개,
초대형 단일 행, 실제 사내 로그의 성능·정확성은 검증하지 않았다.

## 남은 범위

실제 sanity 목록·report 계약·판정 규칙, 내부 AI 및 Jira API, 운영 인증, 망 간 전송,
개정 순서 관리와 대규모 결과 분할은 미구현이다. 현재의 Jira 게시 트랜잭션은 로컬
시뮬레이터 검증이며 실제 Jira의 응답 유실 문제를 해결했다고 간주하지 않는다.
구체적인 지원 범위와 한도는 [README.md](README.md)에 기록했다.


## 2026-10-14 — 사내 SSO 추상 인터페이스와 인수인계

- `oa/sanity_auth.py`의 `SSOIdentityProvider`·`UserDirectory` 추상 계약, 검증된 외부 계정 → 내부 ID 연결,
  활성/미연결 처리, `local_token`/`sso` 모드 분리와 고정 오류 응답을 추가했다.
- `corporate_sso.py`는 사내에서 채울 두 메서드를 명시적으로 미구현 상태로 제공하며,
  `sso_app.py`가 `create_app`에 주입한다. `/auth` router는 UI 정적 mount보다 먼저 등록한다.
- `/api/session`에서 정규화된 신원을 확인한다. 업무 범위 필터가 없는 현재 API는
  SSO 신원만으로 접근하지 못하도록 `AUTHORIZATION_NOT_IMPLEMENTED`를 반환한다.
  기존 토큰 로그인과 서버 고정 검토자 동작은 유지한다.
- `tests/test_auth.py` 12개, `oa/test_sso.py` 8개, 기존 dashboard 경계 6개,
  환경/DB 설정 회귀 30개로 **56개 통과, 실패 0개**를 확인했다.
  함께 발견된 dashboard PostgreSQL 통합 테스트 7개는 테스트 DB 환경 미설정으로 skip했다.
- `oa/dashboard`의 `tsc --noEmit` 통과. 사용자 화면 동작은 변경하지 않았으며 브라우저 테스트는 재실행하지 않았다.
- 검증 범위: 위조 사용자 헤더·로컬 토큰의 SSO 우회 거부, issuer별 계정 분리, 미연결·비활성·만료 재확인,
  어댑터 장애·잘못된 반환의 고정 오류 처리, 세션 비밀정보 비노출, 모든 업무 API의 저장소 접근 전 차단,
  인증 router 연결, 기존 토큰 인증 및 설정 회귀.
- 실제 사내 SSO/계정 DB, 로그인 callback·서버 세션·프런트엔드 SSO, 프로젝트/블록 RBAC는 검증하지 않았다.
  사내 구현 순서·계약·작업 요청문은 [SSO_INTEGRATION.md](oa/SSO_INTEGRATION.md)에 제공한다.

## 2026-10-22 — 명시적 단일 DB 계정 계약

- `SANITY_DB_ALLOW_SINGLE_ACCOUNT`는 기본 `false`, 정확한 `true`에서만 공유 로그인 계약을 선택한다. 잘못된 값은 접속 전에 거부하며 검사 실패 후 모드/계정을 자동 전환하지 않는다.
- 단일 모드에서도 네 테이블 전체 컬럼의 유효 SELECT/INSERT/UPDATE, 관리자·소유자 역할 차단, 앱/public 스키마 CREATE 금지, TRUNCATE/REFERENCES/TRIGGER 금지와 필수 UNIQUE를 검사한다. DELETE는 선택적 보유를 허용한다.
- DB의 private 테이블·큐 컬럼 격리 미제공을 명세/인계에 기록하고 실제 check 출력에 `privilege_mode=single_account db_isolation=not_enforced`를 추가했다. 기존 큐의 불변값·송신 필드·트랜잭션·lease와 SSO 업무 보호는 유지한다. 원본 SQL/포털 계획은 변경하지 않았다.
- PostgreSQL 12.1의 독립 소켓 DB에서 **quick 149/149 통과**(21.545초), 이후 추가한 기본 모드의 DELETE/TRUNCATE/REFERENCES/TRIGGER 거부 회귀 **1/1 통과**를 확인했다.
- PostgreSQL 16.15의 최종 소스에서 **quick 150/150 통과**(23.362초), 실패·오류·skip 0개다. 두 quick 실행의 합성 처리 검사는 각각 100건/10그룹이며 전수/10만 건 검사는 실행하지 않았다.
- 별도 PostgreSQL 12.1 fixture에서 제한 역할 두 개를 실제 제거하고, 공유 로그인으로 VWP/OA의 읽기 전용 check가 모두 성공함을 확인했다. 두 출력은 DB 격리 미제공을 표시하고 네 테이블의 행 수는 바뀌지 않았다. 테스트 인스턴스는 모두 종료했다.
- 설정·권한·기존 모드·메시지 충돌/lease/rollback의 합성 검증이며 실제 사내 DB·계정·TLS·SSO/JIT 운영 검증을 대신하지 않는다. 상세 현장 절차는 [DB 연결 인계](db/CONNECTION_HANDOFF.md#31-단일-계정-모드)를 따른다.

## 2026-10-27 — 선택형 011 무손실 VWP 저장 최적화

- 기존 네 테이블과 원본 002~010을 유지하고 VWP private에 압축 본문 컬럼/CHECK를 추가했다. V2 근거의 전체 payload·근거 원문과 OUTBOX의 정확한 패킷을 보존한다. 평문/압축형 혼합 조회, 원문/압축 hash·조회 키·확장 한도 검증과 기존 전송/SSO OWNER 경계를 유지한다. 작은/비압축성 본문과 legacy 근거는 기존 형식이다.
- owner 유지보수 도구는 기본 읽기 전용이며 명시적 batch 변환·동일성 fingerprint·평문 복구를 제공한다. 실제 공간 회수는 별도 SQL의 전체 재작성이다. 앱의 owner 사용·DELETE/TRUNCATE·원본/판정/개정 삭제 기능은 추가하지 않았다.
- PostgreSQL 12.1의 최종 소스에서 **quick 253/253 통과**, PostgreSQL 16.15에서 **전용 24/24 통과**, 실패·오류·skip 0건이다. 이전 quick 237개는 모두 유지하고 새 경계를 추가했다. 전수·10만 건 검사는 실행하지 않았다.
- 포털의 기존 55/59단계 계획을 보존하고 60번 선택형 ALTER를 추가했다. PG12.1/PG16.15에서 새 전체 60단계 실행·전후 catalog 일치·테이블 OID 보존을 확인했다.
- 실제 TLS 제출→DB 원본→혼합 저장→OA 접수/현재 OWNER 확정·receipt, 파싱 중단 후 재개, 기존 row 불변/중복 충돌, DBA dry run·중단 batch rollback·평문 복구, 원문 NULL/빈 byte·Unicode·압축/원문 hash 손상을 검증했다. 모델의 실제 의미 판단 품질 검증은 아니다.
- 최종 PG12.1 합성 벤치마크는 **10,000근거/10,017저장행**, 원문 3,170,000 bytes다. 전체 논리 row·원문 byte·전송 내용 hash를 유지하면서 VWP 테이블 총량 **26,492,928 → 19,374,080 bytes(26.87% 감소)**를 확인했다. 변환 직후 MVCC 사용량은 38,887,424 bytes였으므로 백필만으로 공간이 줄었다고 보고하지 않는다. 회사 데이터의 절감률 보장이 아니다.
- fixture는 모두 별도 소켓 DB이며 종료했다. 실제 사내 DB·원문·DDL/변환·배포·모델 호출은 수행하지 않았다. 적용 순서와 잠금/여유 공간·복구 경계는 [011 안내](db/PAYLOAD_STORAGE.md)에 기록했다.
