# Design Atlas 인계

기준일: 2026-11-13. Canonical 저장소는 **`/home/coder/workspace/design_atlas`** 다.

## 제품과 접속

- 사용자 확정 제품명은 **Design Atlas**다. 공통 프로젝트·인증·사이드바를 사용하며 수치 주 화면은 **프로젝트 비교** 하나다. 업로드 이력과 경로 대응은 화면 안에 두고 AI·작업·알림·target·변경 설명의 독립 주 탐색은 제거한다. 별도 Area 프로젝트 재등록은 없으며 파란색 light/dark 테마를 유지한다.
- 사용자 접속 주소는 **http://127.0.0.1:8080/login?to=%2Fproxy%2F8000%2F** 다. code-server 로그인 후 공통 홈으로 돌아온다. localhost나 다른 SSH 로컬 포트로 안내하지 않는다. code-server 인증은 유지한다.
- 앱 토큰은 Git 제외 **`.env.integrated.local`의 `SANITY_DASHBOARD_TOKEN`** 이다. 파일은 coder 소유·0600이며 토큰·DSN 값을 문서/응답/로그에 복사하지 않는다. 로컬 토큰은 브라우저 탭 메모리에만 보관하므로 문서 새로고침 후 다시 입력한다.
- 현재는 명시적인 `local_token`과 실제 로컬 DB다. 회사 SSO 실패 fallback이나 합성 데이터 대체가 아니다. 회사 SSO 전용 로그 작업 화면과 로컬에서 조회 가능한 실제 수신 로그 대장을 구분한다.

## 하나의 업무 흐름

1. 홈에서 공통 프로젝트를 등록/선택한다. 운영 등록은 기존 관리자 권한을 따른다.
2. 프로젝트만 선택해 **프로젝트 비교**에서 저장된 기준을 조회/설정한다. A→B는 단방향이고 한 프로젝트에 기준은 하나다. CSV나 버전이 없어도 현재 프로젝트와 설정 안내를 표시한다.
3. 업로드할 블록을 등록하고 버전을 선언하거나 실제 수신 버전을 선택한다. 선언은 실제 `source_namespace`, NET_VER, REVISION, PRE/POST와 서버의 원천 identity 규칙을 사용한다. 기준 설정 때문에 임의 버전이나 보고서를 만들지 않는다.
4. 실제 CSV 업로드 전에 블록·버전의 **수치 분석 준비**로 공통 범위를 정확한 Area UUID에 연결한다. 업로드 이력 안의 metadata JSON을 PC CLI에 전달하며 corner는 파일 하나에 하나다.
5. 비교할 양쪽 Block·NET_VER·REVISION·corner·정확한 upload 실행을 선택한다. 같은 화면에서 다른 경로를 명시적으로 대응하고 hierarchy별 Area/Power·Δ·%를 읽는다. 기준·매핑·데이터가 없으면 unavailable이며 이름·최신 시각으로 추측하거나 0으로 채우지 않는다.

등록/선언은 보고서·수신 ECO·AI 결과를 만들어내지 않는다. POST의 선언 ECO는 실제 수신 ECO와 별도다. 원천 PRE→POST의 `source_pre_id`와 수치 계보 `previous_version_id`를 혼용하지 않는다. 기존 수신 버전의 계보가 없으면 준비 시 이전 버전 없음 또는 실제 이전 버전을 명시한다. 이름·정렬·최신 시각으로 추측하지 않는다.

## 저장·권한 계약

- 기존 Sanity 네 테이블과 신청한 **`di_area_power_csv`, `di_area_power_tracking_record` 두 테이블**을 유지한다. 새 테이블·앱 자동 DDL은 없다. Area 후속 SQL은 `db/area_power/`의 `<schema>`를 사내에서 치환하고 실제 생성/권한을 승인 후 적용한다. 사내 schema 실명·비밀은 요구하지 않는다.
- 네이티브 `WS_PROJECT`, `WS_BLOCK`, `WS_VERSION`이 공통 카탈로그다. 선언/계보는 `atlas.version.*`, 요청 재생 기록은 `atlas.command.*`, 정확한 버전 연결은 `design-atlas-v1.binding.*` 전역 `WS_CONFIG` 키에 저장한다. `WS_CONFIG`에 프로젝트/블록 FK를 붙이지 않는다. 네이티브 원천 버전 payload는 기존 수신 계약을 유지한다.
- 기존 Area UUID·원본 gzip·이력은 재작성하지 않는다. 과거 Area 버전은 프로젝트 공통일 수 있으므로 여러 네이티브 블록 버전을 같은 UUID에 연결할 수 있다. 로컬 운영자는 홈의 실제 기존 데이터 연결에서 UUID를 명시한다. 이름 자동 병합·사용자 자동 등록·권한 자동 부여는 없다.
- 운영은 기존 SSO·관리자/프로젝트 권한·mutation intent·현재 권한 잠금을 사용한다. 권한 없는 현재/기준 프로젝트·중첩 참조·고정 입력·다운로드·총계는 계속 차단한다. 네이티브 관리자 변경의 트랜잭션은 응답 전에 끝낸다.
- 프로젝트 기준은 기존 Area project의 `payload.baseline_project_id`와 `row_version` CAS로 저장한다. 추가 테이블·DDL·migration은 없다. 권한을 잃은 기준의 ID·이름은 가리되 현재 프로젝트와 기준 해제/교체는 유지한다. 실제 비교·원본·고정 결과는 양쪽 현재 권한을 계속 요구하며 기준 변경으로 이전 결과를 재해석하지 않는다.
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

## 로그 검토 높이·메뉴 복귀 수정 — 2026-11-11

- 사용자 보고: 1920×1080·5120×2160에서 가로만 확장되고 세로가 짧으며, 다른 메뉴에서 로그 검토로 돌아오면 멈춤.
- 높이 원인: 통합 Workspace가 상세 main의 viewport 높이를 inline `auto`/최소 높이 0으로 덮어쓴 상태에서 부모 높이 전달도 없어 작업 열이 두 해상도 모두 최소 350px, 로그 목록이 100px로 축소됐다.
- 수정: `AtlasShell`의 활성 `data-page`와 데스크톱 flex 높이 전달을 사용해 공통 chrome을 제외한 실제 남은 높이를 채운다. 고정 헤더 높이 계산이나 ResizeObserver는 추가하지 않았다. 임베디드 main의 inline 높이 덮어쓰기를 제거했고 작은 폭의 기존 레이아웃은 유지한다.
- 멈춤 원인: React Activity 복귀 시 경로→선택 상태 적용과 이전 render의 선택 상태→URL 출력이 충돌했다. 운영 build에서 React #185, 개발 build에서 `WorkspacePage`의 `resetSelection`/경로 layout effect가 포함된 maximum update depth stack을 확인했다. 최초 URL rewrite 횟수만으로는 원인을 특정할 수 없었으며 개발 stack으로 좁혔다.
- 수정: `appliedRouteSearch`를 선택 상태와 함께 반영하고, 현재 경로 적용이 끝나기 전에는 URL 출력을 건너뛴다. 보관 기능을 끄거나 오류를 숨기거나 강제 새로고침으로 우회하지 않았다. SSO·서버·DB·worker는 변경하지 않았다.
- 검증: TypeScript 검사·production build 성공. 실제 Chromium의 같은 통합 데모 상세에서 1920×1080 작업 열 350→637px/로그 목록 100→351px, 5120×2160 작업 열 350→1717px/로그 목록 100→1431px를 확인했다. 두 크기 모두 document 크기가 viewport와 같아 전체 가로/세로 넘침이 없었다.
- 두 크기 각각 수치·AI 분석, 홈·프로젝트, 업로드·실행에서 로그 검토로 왕복하고 같은 보고서를 다시 열었다. 총 6개 메뉴 복귀 흐름이 응답했고 JS 오류는 0건이었다. 확인은 합성 자료를 사용하는 실제 UI 구성요소로 수행했으며 실제 사내 SSO/실데이터 검증은 아니다.
- 새 테스트/fixture/mock/harness/검증 스크립트 파일은 추가하지 않았다. 현재 실행용 정적 build에 반영했다. 발행 전 검증 기록이며 최종 merge·push SHA와 담당 조치는 `artifacts/internal-handoff/LATEST.md` 및 업데이트 003 인계문을 따른다. 이전 main `f87c1450016d1ec2b5e414f7a7bb5dc5d79c05e1`에는 이 수정이 없다.

## 검토 사이드바 유지·데스크톱 검증 원칙 — 2026-11-11

- 사용자 결정: 검토 상세에서도 사이드바를 유지한다. 사내 데스크톱 전용 시스템이므로 모바일 동작은 절대 확인하지 않으며 모바일 지원/개선을 추가 작업으로 추론하지 않는다. 루트 `AGENTS.md`와 대시보드 README에 검증 제한을 반영하고 장기 기억에도 저장했다.
- `Workspace.tsx`의 검토 중 사이드바 렌더링 제외, 접기 상태 제외, 폭 조절 비활성 조건을 제거했다. `studio-system.css`·`sidebar-layout.css`의 검토 중 폭 0/왼쪽 여백 0 강제와 접기 스타일 예외를 제거했다. 기존 모바일 제어 기능은 별도로 확장하지 않았다.
- 통합 화면은 `AtlasShell` 공통 메뉴만 사용하며 내부 메뉴를 중복 생성하지 않는다. 독립 Workspace는 기존 접기·폭 설정을 검토 중에도 사용하고 본문은 실제 사이드바 폭을 제외한 공간을 차지한다.
- 검증: TypeScript 검사·production build 성공. 실제 Chromium 데스크톱 1920×1080·5120×2160에서 통합 production 화면의 공통 메뉴 244px 유지, 내부 메뉴 0개, 수치·AI 분석 왕복 후 같은 보고서 재개방, JS 오류 0건을 확인했다. 앞서 수정한 작업 열 높이 637px·1717px도 유지됐다.
- 독립 Workspace는 Vite의 명시적 합성 자료 경로에서 두 크기 모두 메뉴 220px와 본문 시작점 일치를 확인했다. 1920×1080에서는 검토 중 접기·펼치기, 키보드 폭 조절 후 원래 폭 복원, 프로젝트 현황 왕복을 확인했고 JS 오류가 없었다. 실제 사내 SSO·실데이터 검증은 아니다.
- 모바일 viewport·기기 에뮬레이션·모바일 동작 검증은 수행하지 않았다. 새 테스트/fixture/mock/harness/검증 스크립트 파일은 추가하지 않았다. 현재 실행용 build에 반영했다. 발행 상태는 위 최신 인계의 확정 SHA로 확인하며 실제 사내 배포 완료와 구분한다.

## 공통 범위 접기·NET_VER/REVISION 분리 — 2026-11-11

- 사용자 요청: 상단 공통 범위를 접을 수 있게 하고 NET_VER·REVISION을 개별 선택하며 접었을 때는 개요만 표시한다.
- `AtlasShell.tsx`에 접기/펼치기 버튼과 프로젝트·블록·NET_VER·REVISION 개요를 추가했다. 기본은 펼침이며 접힘은 같은 탭의 메뉴 왕복 동안 유지한다. 선택 필드는 숨김 처리하고 선택값·URL·현재 보고서는 그대로 둔다. `atlas.css`는 기존 공통 색상과 같은 행 배치를 사용하며 접힌 높이만 줄인다.
- NET_VER는 현재 블록에서 중복을 제거하고 REVISION은 해당 NET_VER로 필터링한다. PRE/POST 표시와 기존 version UUID 선택은 유지한다. NET_VER 변경 시 REVISION·보고서·ECO 범위를 해제하며 REVISION만 해제하면 NET_VER는 유지한다. 프로젝트/블록 변경은 하위 선택을 초기화한다. REVISION 선택 전 NET_VER와 접힘은 임시 UI 상태이며 새로고침 보존 대상이 아니다.
- 공통 범위 context·API·DB·SSO·업무 버전 식별 계약은 변경하지 않았다. 기존 `selectScope`의 URL/보고서 범위 정리와 권한 확인 흐름을 그대로 사용한다.
- 검증: TypeScript 검사·production build 성공. 새 정적 build를 실제 Chromium 통합 데모의 데스크톱 1920×1080·5120×2160에서 확인했다. 두 크기 모두 범위 높이 83→53px, 작업 열은 각각 637→667px·1717→1747px이며 전체 document 넘침이 없었다.
- 두 크기에서 접힌 개요 값·숨긴 입력·선택/URL 유지·수치 AI 분석 메뉴 왕복 후 접힘 유지·다시 펼친 선택값 복원을 확인했다. 1920×1080에서는 NET_VER 변경 후 REVISION 후보 제한과 이전 보고서 범위 해제, POST/PRE UUID 선택, 뒤로/앞으로 복원, REVISION 해제 시 NET_VER 유지, 블록 변경 초기화를 확인했다. JS 오류는 없었다.
- 실제 사내 SSO/실데이터 검증은 아니며 모바일 검증은 수행하지 않았다. 새 테스트/fixture/mock/harness/검증 스크립트 파일은 추가하지 않았다. 현재 실행용 build에 반영했다. 발행 SHA와 담당 조치는 `artifacts/internal-handoff/LATEST.md` 및 인계 004를 따른다. 기존 main `6ab6feb8273aec3bbe0d43c6ccae0acbf70efe82` 및 인계 003에는 이 추가 기능이 없으며 수정 브랜치 발행과 main 병합을 구분한다.

## 사이드바 업무 대분류 — 2026-11-12

- 사용자 요청에 따라 `AtlasShell.tsx`의 메뉴를 로그 검토 / Area/Power 대분류로 묶었다. 로그 검토에는 기존 로그 검토·수신 로그 대장을, Area/Power에는 기존 구성·전력 업무 메뉴를 배치한다. 홈은 상단, 계정·관리는 하단의 공통 영역으로 분리한다.
- `atlas.css`에 그룹 제목·구분선·간격을 추가했다. 그룹 자체의 접기 기능이나 새 메뉴·라우트·권한은 추가하지 않았다. 관리자 메뉴는 로그인 확인 이후의 기존 관리자 표시값을 그대로 사용한다.
- TypeScript 검사·production build 성공. 실제 Chromium 통합 데모의 1920×1080·5120×2160에서 대분류 제목과 메뉴 소속, 선택 메뉴 하나만 강조, 프로필과 메뉴의 비겹침, 가로 넘침 없음을 확인했다. 두 크기 모두 Area/Power 수치·AI 분석 왕복 후 동일 로그 보고서를 다시 열었으며 공통 범위의 접힘이 유지되고 JS 오류는 없었다.
- 확인은 합성 자료를 사용하는 실제 UI 구성요소에서 수행했다. 실제 사내 SSO·실데이터 검증은 아니며 모바일 검증은 수행하지 않았다. SSO·DB·업무 API와 앞선 공통 범위 선택 기능은 변경하지 않았다. 새 테스트/fixture/mock/harness/검증 스크립트 파일은 추가하지 않았다.
- 현재 실행용 정적 build에 반영했다. 발행 상태는 위 최신 인계의 확정 SHA로 확인한다. 기존 main `6ab6feb8273aec3bbe0d43c6ccae0acbf70efe82` 및 인계 003에는 이 대분류와 앞선 공통 범위 추가 기능이 없다. 수정 브랜치 push가 main 병합·사내 배포 완료를 뜻하지 않는다.

## Area/Power 실제 테이블 이름 전환 — 2026-11-12

- 물리 테이블은 정확히 `di_area_power_tracking_record`, `di_area_power_csv`다. 공통 DB/readiness·OA 서비스/공통 catalog·VWP 분석/AI 조회와 수동 SQL 템플릿을 함께 전환했다. 컬럼 참고본은 `db/area_power/02_di_area_power_tracking_record.columns.sql`이며 이전 파일은 제거했다. 업무 컬럼·record_type·UUID/API·PC config/전송 계약은 그대로다.
- 독립 폐기 PostgreSQL 18.6·비소유 DML 로그인에서 새 두 테이블만 설치하고 readiness, canonical config CLI→실제 FastAPI 업로드 2건, gzip/SHA/범위/actor/ready snapshot·상태 GET·자동 선택 0개를 확인했다. 실제 OA catalog·선택 CAS·비교/AI 실행 dedupe·job 점유/종료·명시 비교/ingest 재실행과 VWP dedupe·version 조회/AI 미설정 실패 저장도 확인했다. 실제 AI·메일·SSO 호출은 하지 않았다.
- 승인된 실제 테이블/권한/필수 정의 준비 후 OA·중앙 VWP backend를 함께 전환한다. 앱 자동 DDL·사용자 DB rename/migration·옛 테이블 fallback은 없다. 동등한 기존 제약/인덱스 이름은 유지하며 승인된 누락 정의만 운영자가 적용한다. [DB 전환 안내](docs/area_power/database.md)를 따른다.
- 폐기 DB/로그인/자체 서버/임시 CSV·metadata·config는 제거했다. 사용자 DB·기존 OA 서비스·설정은 보존하고 재시작하지 않았다. 새 테스트/fixture/mock/harness/검증 스크립트 파일·모바일 검사·UI build·광범위 suite는 추가/실행하지 않았다. 회사 DB·SSO·실제 배포는 미검증이다. feature 발행과 main 병합은 별개이며 실제 SHA/담당 조치는 비공개 인계 006을 따른다.

## 프로젝트 비교 단순화 — 2026-11-13

- 기준 API는 `GET`/`POST /api/area-power/projects/{Area UUID}/baseline`이다. POST는 nullable `baseline_project_id`와 필수 `expected_row_version`을 받는다. 기존 인증 클라이언트의 POST mutation 계약과 SSO·프로젝트 권한을 변경하지 않았다.
- 모든 새 `previous_project` 비교와 교차 프로젝트 매핑은 저장한 기준을 검사한다. 당시 기준 ID/row_version·양쪽 upload/snapshot·mapping revision을 고정한다. 매핑 없는 루트/동일 이름 경로도 자동 연결하지 않는다. 정확한 upload의 hierarchy 조회 API로 같은 화면의 경로 선택을 지원한다.
- 폐기 PostgreSQL 18.6·비소유 제한 DML 로그인·기존 두 테이블에서 실제 FastAPI ASGI 경로의 인증·POST 저장/오류·CAS 동시 경쟁 200/409와 기존 multipart plain/gzip·SHA·원본 byte 보존·재업로드 별도 행·자동 선택 없음을 확인했다. DB 큐의 실제 `consume_once`로 비교 3개를 처리해 hierarchy instance area 2→4, Δ2/+100%, power 8→16 mW, Δ8/+100%와 대응/기준 누락의 null/unavailable을 확인했다. AI 미설정에 따른 AIError는 예상된 실패이며 수치 completed는 보존됐다. 실제 AI·메일은 호출하지 않았다.
- 권한 경계는 실제 `AreaAccess`/`AuthorizedDatabase`/서비스에 명시적인 메모리 grant context를 넣어 확인했다. 기준 권한 회수 후 기준 설정과 프로젝트 목록 모두 식별자를 가리고 현재 프로젝트는 유지했으며, 과거 결과·기준 원본·비교는 차단하고 해제/교체는 허용했다. 회사 SSO·실제 권한 저장소/HTTP 경로 전체의 회수 시나리오를 검증했다고 주장하지 않는다.
- 기준 파일 없는 비교의 dedupe도 지정 기준 UUID를 포함한다(row_version은 제외). 별도 폐기 PG18.6에서 현재 CSV 하나로 A→B와 A→C의 서로 다른 비교/고정 기준, C 반복·C 재저장 시 재사용, B 복귀 시 원래 B 재사용, B 권한 회수 후 C 생성/재사용과 기존 B 결과 불변을 좁게 확인했다. 이 추가 확인은 기존 backend 증거를 반복 실행하지 않았으며 자체 DB·제한 로그인은 정리했다.
- UI는 총 build 3회와 1920×1080·5120×2160 전체 viewport로 확인했다. 단일 표·sidebar·계층 펼침, 지정 기준의 방향성·고정 이력, 같은 corner 수치와 다른 corner의 Power contextual, 선택을 보존하는 갱신이 동작했다. %는 BigInt로 두 자리 표시하되 원래 전체 값은 title/접근성 설명에 보존했다. 십진수 corner 동일성·반올림 경계와 IEEE 범위를 넘는 정밀도도 실제 소스의 인라인 계산으로 확인했다. 브라우저 오류는 없었으며 모바일은 실행하지 않았다.
- 자체 폐기 DB·로그인은 정리했고 사용자 DB·설정·기존 서비스는 보존했다. 새 테스트/fixture/mock/harness/검증 스크립트 파일·모바일 검사는 추가/실행하지 않았다. 실제 발행 SHA·feature/main 병합 상태·최신 역할별 조치는 비공개 `artifacts/internal-handoff/LATEST.md`의 현재 기록을 따른다. 검증 기록 자체가 회사 배포 완료를 뜻하지 않는다.

## Area 업로드·상태 조회의 추가 인증 제거 — 2026-11-13

- 사용자 결정은 Area PC→중앙 VWP CSV 입력에만 적용한다. 업로드/상태 GET의 추가 인증과 CLI `--token`/`--actor`, `AREA_POWER_UPLOAD_TOKEN`/`AREA_POWER_ACTOR`를 제거했으며 대체 인증 SDK/Protocol·새 gate는 추가하지 않았다. 기존 launcher에서 제거한 옵션/환경변수를 빼고 URL·CSV/metadata만 제공한다.
- 사내 도달성을 신뢰하며 인증된 사용자별 업로드 추적은 제공하지 않는다. `uploaded_by`와 새 snapshot 작성자는 기술적 수신 서비스 `vwp_server`이고 임의 요청 헤더의 사용자명은 신뢰하지 않는다. 기존 OA 로그인·SSO·프로젝트 권한과 조회/다운로드/비교 보호는 유지한다. DB의 기존 두 테이블/NOT NULL 컬럼·이력은 바꾸지 않는다.
- 별도 폐기 PG18.6·비소유 제한 로그인에서 실제 CLI→loopback uvicorn→DB의 config/plain·명시적 gzip 업로드를 토큰/actor 설정 없이 확인했다. health/상태 GET 200, 기술 출처 고정·명시적 snapshot 재사용·원본 gzip/SHA 보존·자동 선택 없음·잘못된 CSV 422와 제거 옵션의 CLI 거부가 확인됐다. 기존 OA 미로그인 401와 미설정 SSO 차단은 그대로였다. 새 입력/fixture/검증 스크립트 파일 대신 메모리 FD를 사용했고 자체 서버·DB·로그인은 정리했다.
- 앞선 과거 업로드 token 401·수동 actor 검증은 당시 동작의 역사 기록이며 현재 설정 요구가 아니다. 이번 입력 경로 변경 때문에 UI build·기존 비교/DB 전수·모바일 검사는 반복하지 않았다. 실제 발행/역할 인계는 비공개 최신 LATEST 기록이 기준이고 사내 배포는 승인 운영자가 별도로 관리한다.
