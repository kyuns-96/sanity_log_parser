# Design Atlas 인계

기준일: 2026-11-11. Canonical 저장소는 **`/home/coder/workspace/design_atlas`** 다.

## 제품과 접속

- 사용자 확정 제품명은 **Design Atlas**다. 공통 홈·프로젝트/블록/버전·인증·사이드바로 통합했으며 별도 Area 프로젝트 등록 UI/API는 제거했다. 파란색 light/dark 테마와 모바일 drawer를 사용한다.
- 사용자 접속 주소는 **http://127.0.0.1:8080/login?to=%2Fproxy%2F8000%2F** 다. code-server 로그인 후 공통 홈으로 돌아온다. localhost나 다른 SSH 로컬 포트로 안내하지 않는다. code-server 인증은 유지한다.
- 앱 토큰은 Git 제외 **`.env.integrated.local`의 `SANITY_DASHBOARD_TOKEN`** 이다. 파일은 coder 소유·0600이며 토큰·DSN 값을 문서/응답/로그에 복사하지 않는다. 로컬 토큰은 브라우저 탭 메모리에만 보관하므로 문서 새로고침 후 다시 입력한다.
- 현재는 명시적인 `local_token`과 실제 로컬 DB다. 회사 SSO 실패 fallback이나 합성 데이터 대체가 아니다. 회사 SSO 전용 로그 작업 화면과 로컬에서 조회 가능한 실제 수신 로그 대장을 구분한다.

## 하나의 업무 흐름

1. 홈에서 공통 프로젝트·블록을 등록한다. 운영 SSO에서는 기존 관리자 권한이 필요하다.
2. 버전을 선언하거나 실제 수신 버전을 선택한다. 선언 시 실제 `source_namespace`, NET_VER, REVISION, PRE/POST를 사용하며 서버의 원천 identity 함수를 재사용한다. 임의 이름으로 버전 ID를 생성하지 않는다.
3. **수치 분석 준비**를 실행한다. 공통 범위를 내부 Area UUID에 정확히 연결하며 같은 요청의 동시 실행으로 중복 객체를 만들지 않는다.
4. 공통 범위를 유지한 채 로그 검토·수신 대장·구성/전력·업로드·분석·추적·변경·알림을 탐색한다. URL 범위는 뒤로 가기와 새로고침에도 남는다. 수신 대장은 선택한 네이티브 버전으로 실제 DB를 조회한다.
5. 업로드 화면의 metadata JSON을 사용자 PC CLI에 전달한다. JSON의 project/version/block은 공통 문자열 ID가 아니라 준비된 실제 Area UUID다. Corner는 CSV에서 읽으며 파일 하나에 단일 corner다.

등록/선언은 보고서·수신 ECO·AI 결과를 만들어내지 않는다. POST의 선언 ECO는 실제 수신 ECO와 별도다. 원천 PRE→POST의 `source_pre_id`와 수치 계보 `previous_version_id`를 혼용하지 않는다. 기존 수신 버전의 계보가 없으면 준비 시 이전 버전 없음 또는 실제 이전 버전을 명시한다. 이름·정렬·최신 시각으로 추측하지 않는다.

## 저장·권한 계약

- 기존 Sanity 네 테이블과 신청한 **`di_area_power_csv`, `tracking_record` 두 테이블**을 유지한다. 새 테이블·앱 자동 DDL은 없다. Area 후속 SQL은 `db/area_power/`의 `<schema>`를 사내에서 치환하고 실제 생성/권한을 승인 후 적용한다. 사내 schema 실명·비밀은 요구하지 않는다.
- 네이티브 `WS_PROJECT`, `WS_BLOCK`, `WS_VERSION`이 공통 카탈로그다. 선언/계보는 `atlas.version.*`, 요청 재생 기록은 `atlas.command.*`, 정확한 버전 연결은 `design-atlas-v1.binding.*` 전역 `WS_CONFIG` 키에 저장한다. `WS_CONFIG`에 프로젝트/블록 FK를 붙이지 않는다. 네이티브 원천 버전 payload는 기존 수신 계약을 유지한다.
- 기존 Area UUID·원본 gzip·이력은 재작성하지 않는다. 과거 Area 버전은 프로젝트 공통일 수 있으므로 여러 네이티브 블록 버전을 같은 UUID에 연결할 수 있다. 로컬 운영자는 홈의 실제 기존 데이터 연결에서 UUID를 명시한다. 이름 자동 병합·사용자 자동 등록·권한 자동 부여는 없다.
- 운영은 기존 SSO·관리자/프로젝트 권한·mutation intent·현재 권한 잠금을 사용한다. 권한 없는 현재/기준 프로젝트·중첩 참조·고정 입력·다운로드·총계는 계속 차단한다. 네이티브 관리자 변경의 트랜잭션은 응답 전에 끝낸다.
- Area commit 후 호스트 연결 기록이 실패하면 `409 PARTIAL`과 실제 Area ID를 반환한다. 분산 rollback이 성공했다고 표시하지 않는다. 명시적으로 다시 준비하면 같은 UUID의 연결 기록을 복구한다. 자동 재시도는 없다.
- OA↔VWP 업무 전달은 DB만 사용한다. CLI HTTPS 업로드·모델 API·사내 메일 어댑터는 별도의 외부 경계다. 로그와 수치 분석 엔진은 독립이며 `SANITY_*`, `AREA_POWER_*`, `bin/sanity`, `bin/area-power`의 수집 계약은 제품명 때문에 바꾸지 않는다.

## 현재 실행

- `DesignAtlas`: 새 루트, coder 사용자, loopback 8000, persistent. 원래 `SanityIntegrated`는 종료했다.
- 기존 Vite 개발 프로세스는 같은 `/absproxy/5173/` base로 새 루트에서 `DesignAtlasDevServer`로 복원했다. 사용자 접속은 위 code-server 8080 → proxy 8000 주소를 안내한다.
- `.venv-integrated`는 새 경로에서 다시 만들고 root requirements를 설치했다. 기존 사용자 `.venv`의 불완전한 링크는 덮어쓰지 않았다.
- 재부팅 자동 시작은 등록하지 않았다. VWP 업로드·수치/AI worker·OA 메일은 별도 프로세스이며 현재 상시 실행하지 않는다. 역할별 실행은 [수치 엔진 README](area_power/README.md)와 [배포 안내](DEPLOYMENT.md)를 따른다.
- 사용자 DB **`iat_dashboard_local`, `sanity_dashboard_local`은 보존 대상**이다. 검증 DB로 오인해 삭제하지 않는다. 실제 로컬 PostgreSQL 18.6에서 사용하며 앱 계정은 USAGE/DML만 가진다.
- 저장소 디렉터리를 이동하고 Git의 연결 worktree 8개를 native `git worktree repair`로 복구했다. 각 worktree의 경로·브랜치·내용은 이동하지 않았고 커밋/리셋하지 않았다. 옛 canonical 디렉터리의 runtime alias는 없다.

## 2026-11-10 제품 통합 검증 기록

- TypeScript 검사와 production frontend build 통과. 기존 대형 chunk·classic color-mode script 경고는 남아 있으며 억제하지 않았다.
- 별도 실제 PostgreSQL DB와 비소유 DML 계정에서 공통 생성/명령 재생·identity·동시 준비·선행 버전 검증·원천 PRE와 수치 계보 분리·선택 버전 조회 경계를 실행했다.
- 기존 프로젝트 공통 Area 버전 하나를 두 네이티브 블록 버전에 연결하고 UUID·payload·row version·블록 소유가 바뀌지 않음을 확인했다.
- 실제 DB 연결을 끊어 수치 commit 후 호스트 기록 실패를 만들고 PARTIAL의 정확한 UUID와 명시적 재준비의 중복 없는 복구를 확인했다.
- 기존 테스트 전용 SSO 어댑터와 실제 권한 DB로 관리자/멤버 경계·미승인 차단·권한 회수 후 404를 확인했다. 기존 SSO 위조 헤더/로컬 토큰 차단 회귀 1개도 통과했다. 회사 SSO 자체의 검증은 아니다.
- Chromium에서 프로젝트→블록→네이티브 버전 선언→수치 준비, 실제 UUID의 VWP metadata 생성, 선택 버전의 수신 로그 API 호출, SSO 모드 로그 작업 화면과 수치 왕복·뒤로/새로고침을 확인했다.
- 실제 code-server 로그인 복귀가 `/proxy/8000/`임을 확인했다. 파란색 light/dark, 390px 화면의 넘침 없음·drawer·JS 오류 없음, 명시적 합성 데모의 유지와 호스트 카탈로그 미조회도 확인했다.
- 이동 후 두 Area CLI 진입점의 help가 성공했다. 검증 DB/계정·임시 서버·브라우저 쿠키/탭을 정리했다. 사용자 DB에 합성 프로젝트를 넣지 않았고 새 테스트·fixture·mock·harness·검증 스크립트 파일을 만들지 않았다.

## 유지할 경계와 미검증

Hierarchy 누적 집계의 부모/자식 중복 합산 금지, logic_cells 분모·LLP 분리·CTS/logic power 포함 관계·mW·단일 corner·재실행별 새 upload·명시 snapshot/selection·과거 고정 입력을 유지한다. 결측/0분모와 split 자식의 과거 수치를 임의 생성하지 않는다. 수치는 코드, 변경 설명 판단은 AI다. AI 미설정/실패는 needs_review로 남기고 사람의 읽음·확인·해소를 초기화하지 않는다.

회사 DBaaS·SSO/실제 SDK·실데이터·AI 품질·사내 메일 서비스·Docker 이미지·대규모 성능은 미검증이다. 기존 PG18 포털 catalog 이슈를 해결한 변경은 아니다. 사내 연결·테이블 생성·권한 적용 완료를 로컬 검증으로 대신하지 않는다.

이전 사용자 수정분을 리셋하지 않는다. 원본 `inst_area_tracking/docs/csv_structure`와 기존 사용자 파일·`.swp`는 보존한다. 사용자 요청 없는 새 테스트/fixture/mock/harness/검증 스크립트와 임시 파일 우회는 금지한다. 기존 `set noswapfile` 설정은 유지한다.

## 2026-11-11 통합 제품 데모

- 사용자 요청으로 일반 `?demo=1`도 `DashboardNavigation`·`AtlasShell`·`AtlasHome`·공통 범위·기존 로그 작업 화면·Area/Power 화면을 함께 사용한다. 주소는 **http://127.0.0.1:8080/login?to=%2Fproxy%2F8000%2F%3Fdemo%3D1** 다. 기존 열린 데모는 새로고침해야 새 bundle을 사용한다.
- `AtlasDemo.tsx`는 명시적 데모에서만 lazy load되는 메모리 조합점이다. 실제 인증 config를 만들거나 SSO 신원을 위조하지 않는다. 기존 `workspaceDemo.ts`와 `workspaceDemoCatalog.ts`가 유일한 합성 카탈로그·계정·로그 상태다. `areaPower/demo.ts`는 그 카탈로그의 정확한 UUID와 배정 범위를 사용한다. 운영 실패/미설정에서 자동 선택하지 않는다.
- 초기 합성 프로젝트의 여러 버전·두 corner·재실행·고정 비교·target/mapping/change·알림·작업을 제공한다. 명시적 선택, 정확한 유리수 계산, 누적 hierarchy의 비중복 합산, revision/CAS, 재검토와 사람 상태 보존, 수치 작업 재실행 및 실제 gzip 다운로드가 메모리에서 동작한다. 원본은 기존 81컬럼 CSV 소비자로 검증했으며 산출 조건도 완전하다.
- 기존 seed ID와 로그/그룹/검토/PoR 기능은 유지한다. 새 버전 선언은 운영과 같은 identity 규칙을 사용하지만 보고서·수신 ECO·측정 파일을 제조하지 않는다. 선언 ECO와 수신 ECO는 별개이며, 수신 ECO가 없는 POST 화면은 무한 loading 대신 미수신 상태를 표시한다.
- 합성 계정 전환은 프로젝트 멤버 범위를 공통 홈·로그·수치에 같이 적용하고 보관 화면을 다시 만든다. 계정·편집·작업 상태는 새로고침 시 초기화한다. 사라진 임시 범위와 snapshot UUID는 초기 진입에서 정리한다. 데이터·권한·인증값을 브라우저 저장소나 실제 DB에 저장하지 않는다. 기존 테마 설정은 기존 UI 정책을 유지한다.
- 원본 CSV/metadata도 합성 자료이며 JSON의 UUID는 실제 VWP에 사용할 수 없다고 표시한다. AI·사내 메일 성공을 흉내 내지 않고 `model_called=false`, `needs_review`, 미발송 상태를 유지한다. 실제 서비스 종료 링크는 문서 이동이며 SSO 로그아웃을 호출하지 않는다. 회사 SSO 로그인·콜백·쿠키·어댑터와 서버/DB 코드는 이 데모 통합 작업에서 변경하지 않았다.
- TypeScript 검사와 production build가 통과했다. Chromium의 실제 code-server proxy에서 모든 공통 메뉴, corner 필터·재실행 선택·원본 gzip, 고정 비교와 합성 재검토, target 새 revision/보존 이력, mapping/change, 수치 재실행, 기존 PASS 로그의 분류·판정 화면을 확인했다. 새 프로젝트/블록/POST 선언과 입력 미생성, 합성 계정 격리, 뒤로 가기·재초기화, light/dark·390px drawer·넘침 없음도 확인했다.
- 실제 모델 코드의 메모리 요청으로 비중복 target 계산·정확한 delta, 다른 corner의 contextual 처리, 재검토 후 읽음/확인/해소 보존, 새 선언의 미수신 상태와 미배정 프로젝트 원본 차단을 확인했다. 브라우저 데모의 실제 업무/인증 API 요청과 JS 오류는 모두 없었다. 별도 비데모 진입은 여전히 정상 인증 화면이며 합성 toolbar가 없다.
- 새 자동 테스트·fixture·mock·harness·검증 스크립트 파일은 추가하지 않았다. 추가 모듈과 합성 자료는 요청받은 제품 데모 기능이며, 확인 코드는 메모리에서 실행했다. 검증 탭·인증 쿠키·다운로드는 정리했고 기존 사용자 DB와 실행 서비스는 그대로 유지했다.


## 사내 메일 Protocol 전환 — 2026-11-11

- 사용자 결정으로 SMTP 구현·`AREA_POWER_SMTP_*` 설정을 제거했다. 공통 `area_power/common/mail.py`는 `MailSender.send(MailMessage) -> MailReceipt`와 미연결/명시 거부 예외만 제공하며 실제 프로토콜·SDK·인증·발신 계정은 가정하지 않는다.
- 회사 구현 지점은 `oa/integration/corporate_mail.py:create_mail_sender()`와 `oa/integration/company_mail/`다. `python -m oa.mail_app`이 명시적으로 어댑터를 주입한다. 기본 factory는 미연결 오류로 DB 접속·job 점유 전에 실패하며 성공/다른 전송으로 대체하지 않는다.
- 공통 기본 수신자는 `AREA_POWER_MAIL_RECIPIENTS`다. 요청 수신자가 있으면 우선하며 모든 수신자의 접수/거부를 정확히 대응해야 한다. 회사 서비스 접수는 실제 수신/열람 보장이 아니다.
- 새 이력은 `provider_accepted`, accepted/rejected recipients, 비밀 아닌 provider message ID를 사용한다. 과거 SMTP JSON 이력과 두 테이블은 그대로 보존하며 추가 DDL은 없다. 외부 호출 전 시도를 기록하고 결과 불명·일부 거부·접수 후 DB 기록 실패를 보존한다. 자동 재발송/점유 회수는 없다.
- 상세 계약과 OA 사내 AI 허용 범위는 [메일 연동](oa/MAIL_INTEGRATION.md), AGENTS·내부 AI 가이드·OA 역할 지시문에 반영했다. 회사 AI는 자기 어댑터만 구현하며 공통 worker·Protocol·composition root·SSO·SQL·UI는 변경하지 않는다.
- 검증: 기존 두 테이블 SQL을 설치한 별도 폐기 PostgreSQL 18.6 DB와 제한 DML 로그인으로 전체/일부 접수·전체 거부·명시 거부 예외·timeout·불완전 receipt·기본/누락/중복 수신자·접수 후 DB 완료 실패·미연결 CLI의 pending job 미점유를 확인했다. 발송 경계에서 별도 연결의 `FOR UPDATE NOWAIT`가 성공해 행 잠금이 해제됐음을 확인했고, 사람 읽음/확인/해소 상태와 자동 재호출 없음도 확인했다. 메일 제공자는 메모리 경계 대역이며 실제 외부 메일 호출은 0건이다. 폐기 DB·계정은 제거했고 사용자 DB는 변경하지 않았다.
- TypeScript 검사·production build와 실제 Chromium 데모의 수신자 입력 안내를 확인했다. SMTP 안내가 없고 사내 메일 미발송 안내를 표시하며 runtime JS 오류는 없었다. 회사 서비스 구현·실제 발송·배포는 미검증이다. 새 테스트/fixture/mock/harness/검증 스크립트 파일은 추가하지 않았다.
