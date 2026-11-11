# Design Atlas 수치 엔진 인계

기준일: 2026-11-10. Canonical 저장소: `/home/coder/workspace/design_atlas`. 제품 전체 최신 상태는 [루트 인계](../HANDOFF.md)를 따른다.

## 확정된 구성

- 제품은 Design Atlas다. 공통 홈·프로젝트/블록/버전 선택·인증·파란색 light/dark 사이드바를 사용한다. 로그와 수치 엔진은 분리하되 별도 Area 카탈로그 등록 UI·API는 제거했다.
- 기존 `inst_area_tracking`의 실행 코드·독립 UI·중복 SQL·배포 설정은 제거했다. 옛 Python import·entry point의 alias/shim은 없다. 원래 사용자 제공 `inst_area_tracking/docs/csv_structure`는 보존했다.
- Area의 `di_area_power_csv`, `tracking_record` 두 테이블과 Sanity 네 테이블은 별도 계약이다. 이미 신청한 두 테이블을 다시 신청하지 않는다. 사내 실제 생성·제약·권한 적용은 미확인이다.
- 실제 사내 스키마·DSN은 요구하거나 저장하지 않는다. SQL은 `db/area_power/`의 `<schema>.테이블` 템플릿이며 사내에서 치환한다. 앱 자동 DDL은 없다.
- OA↔VWP 업무 전달은 DB만 사용한다. CLI 업로드·모델 API·사내 메일은 각 외부 입력/제공자 경계다. 메일은 [Protocol 계약](../oa/MAIL_INTEGRATION.md)과 명시적 회사 어댑터 주입만 제공하며 SMTP 구현은 제거했다.

## 실행·인증

- 사용자 접속용 persistent 서비스는 새 루트의 `DesignAtlas`, `127.0.0.1:8000`이다. 이전 `SanityIntegrated`와 독립 `DashboardProxy:8001`은 종료했다.
- 사용자 주소는 **http://127.0.0.1:8080/login?to=%2Fproxy%2F8000%2F** 이다. code-server 인증을 유지하며 로그인 후 공통 홈으로 돌아온다. 임의로 localhost·다른 포트로 안내하지 않는다.
- 비공개 Git 제외 `.env.integrated.local`(0600), `.venv-integrated`를 사용한다. 기존 Sanity `.venv`는 실행 불가능한 링크 상태여서 덮어쓰지 않았다. 토큰·DSN은 문서·응답·로그에 복사하지 않는다.
- 현재 호스트는 명시적 `SANITY_AUTH_MODE=local_token`, 고정 서버 reviewer, 실제 로컬 DB다. 같은 인증으로 업무를 탐색한다. 사내 SSO 실패의 fallback이 아니며 회사 SDK/계정 연결은 별도로 필요하다.
- 기존 Area DB `iat_dashboard_local`과 사용자 입력은 보존했다. 별도 Sanity 로컬 DB `sanity_dashboard_local`에는 원본 네 테이블·002/004~013을 적용했다. 앱 계정은 USAGE/DML이며 owner/DBA가 아니다. 이 두 사용자 접속용 DB를 검증용으로 오인해 삭제하지 않는다.
- 현재 persistent 실행은 OA 한 개다. VWP 업로드·수치/AI worker·OA 메일은 실행 안내에 따라 별도로 구동한다. 재부팅 자동 시작 등록은 하지 않았다.

## 운영 권한과 호환

- API는 `/api/area-power/*`, 설정은 export된 `AREA_POWER_*`다. 기존 unprefixed Area 환경변수·브라우저 `OA_API_TOKEN`·신뢰하는 X-Actor는 제거했다.
- 운영 SSO는 현재 사용자·잠긴 프로젝트 권한 snapshot을 사용한다. 공통 API가 Area 프로젝트/블록의 정확한 binding과 기존 `WS_CONFIG`의 버전 binding을 준비한다. 과거 프로젝트 공통 버전 UUID는 재작성하지 않고 여러 네이티브 버전에 연결할 수 있다. 이름 기반 병합·사용자 자동 등록·권한 자동 부여는 없다.
- 모든 목록·상세·원본·mutation·counts에서 기준/현재/고정/추가 참조를 검사한다. 기준 프로젝트 권한이 없으면 현재 프로젝트 권한만으로 결과를 볼 수 없다. 기존 미연결 Area catalog는 SSO에서 차단한다.
- 명시적 local_token만 `local_mode:true`인 미연결 실제 로컬 catalog를 허용한다. 임의 tool/constraint 관측값의 `version_id`·`project_id`는 실제 업무 FK로 오해하지 않는다.
- 새로운 dependency는 역할 requirements에 반영했고, production Docker context/COPY에 Area OA/common을 추가했다. Docker build·사내 베이스 이미지·Nginx 배포는 미실행이다. Sanity의 기존 PG18 포털 catalog 문제를 수정한 작업은 아니다.

## 이전 엔진 통합 검증 기록

- 새 namespace의 실제 VWP 업로드 → DB → 수치 worker → 호스트 결과 API를 폐기 DB에서 실행했다. 79개 지표 전체 처리, logic area 1→2와 +100%, AI 미설정 needs_review·숫자 completed를 확인했다.
- 기존 SSO 회귀 1개가 통과했다. 기존 합성 SSO 어댑터와 실제 권한 DB로 현재·기준 양쪽 허용 200, 기준 권한 회수 후 비교/다운로드 404·지표 0행·공개 counts 제외, mutation intent 없는 쓰기 403을 확인했다. 회사 SSO 자체의 검증은 아니다.
- 실제 code-server proxy에서 공통 로그인·업무 왕복·blue light/dark·390px 모바일·비교/검색·gzip 원본 다운로드를 확인했다. 기존 명시적 Sanity demo는 유지했고 runtime JS 오류는 없었다.
- Frontend build, `python -m area_power.vwp --help`, venv interpreter로 `bin/area-power --help`가 성공했다. 추가 자동 테스트·fixture·mock·harness·검증 스크립트 파일은 만들지 않았다.
- 위 검증의 폐기 DB/계정·서버·브라우저·다운로드는 정리했다. 최신 Design Atlas 검증·서비스 상태는 루트 인계를 따른다. 회사 DB·실데이터·AI 품질·사내 메일 서비스·규모/성능은 미검증이다. Area 권한 목록은 완전 참조 검증을 위해 후보를 스캔하므로 큰 데이터의 비용은 실측이 필요하다.

## 보존할 사용자 작업과 규칙

- 시작 전 수정되어 있던 `oa/dashboard/README.md`, `src/WorkspaceDemoBanner.tsx`, `src/WorkspaceReview.tsx`, `src/workspaceDemo.ts`를 덮어쓰거나 되돌리지 않았다.
- 사용자 요청 없는 새 테스트·case·fixture·mock·harness·검증 스크립트 추가 금지. 임시 파일·기존 검사 파일 추가로 우회하지 않는다.
- 기존 사용자 파일·`.swp`를 임의 삭제하지 않는다. 기존 `set noswapfile` 설정은 유지한다.
- 실데이터·사내 비밀은 제공받지 않는다. Hierarchy 누적 집계·부모/자식 중복 합산 금지·LLP 분리·분모·power 포함 관계·파일별 단일 corner는 다시 질문하지 않는다.

실행·수치·보존 계약은 [README](README.md), [제품 설계](../docs/area_power/design.md), [DB 계약](../docs/area_power/database.md)을 따른다.
