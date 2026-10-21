# 영속 워크스페이스 — 프로젝트·PoR·사용자 판정

`/workspace`는 브라우저 메모리 데모와 별도로, 실제 FastAPI·PostgreSQL에 연결한 작업 화면이다.
10개 테스트 계정, 3개 프로젝트, 9개 블록, 공개 parser로 검증한 1,000개 예시 검사 항목을 함께 사용한다.
PoR, 사용자 판정·사례·기준과 제안 이력은 서버에 저장되며 새로고침·로그아웃·다른 계정 로그인·서버 재시작 후에도 유지된다.
실제 회사 계정/SSO·사내 report·운영 DB·사내 AI는 연결하지 않았다. 별도 합성 mock 시험에는 Codex 모델을 실제 호출하며 아래 범위를 따른다.

report 입력은 [제출 실행 안내](../vwp/SUBMISSION.md)를 따른다. 구현된 `sanity submit` → VWP 내부 제출 폴더 → bridge 영속 원본/DB → OA 분할 수신·원자 반영 경로를 사용한다. `sanity status`가 OA 저장과 검토 가능/차단 사유를 반환한다. 실제 회사 SDK·계정/담당·mount/운영 DB는 미연결이다. 기존 합성 workspace를 운영 수신 증거로 사용하지 않으며 원본 파일 업로드 API는 제공하지 않는다.

사내 구현의 진입 문서는 [INTERNAL_AI_HANDOFF.md](../INTERNAL_AI_HANDOFF.md)다. SSO·예상 목록/원천 대응·parser·1단계/2단계 SDK의 허용 구현 지점과 공통 운영 연결의 남은 작업을 구분한다. 새 타입/합성 packet은 회사 어댑터의 모의 검증용이며 이 테스트 서버나 UI를 운영 모드로 전환하지 않는다.

## 사용 흐름

1. `/workspace`에서 사용자 ID와 비밀번호로 로그인한다.
2. 배정된 프로젝트에서 블록을 연다. TOP은 프로젝트 전체, 블록 담당자는 자신의 블록을 조회한다.
3. PRE/POST → NET_VER·전체 REVISION → POST ECO → sanity item을 선택하고 해당 항목의 검사 대장/원문을 확인한다.
4. 블록 담당자에게만 표시되는 **이 버전을 PoR로 지정**을 누르고 사유를 입력한다.
5. 지정자·시각·사유·개정이 기록된다. TOP 계정에서 같은 블록을 조회하면 저장된 PoR이 보인다.
6. sanity item을 고르고 report 행의 **검토 시작**으로 순차 작업 화면을 연다. 대상 report ID·전체 로그 수·버전을 확인한다. 목록의 규칙·보고서 전체 판정·파싱 열은 **상세 열 보기**에서 조회한다.
7. 1단계 **로그 분류**에서 그룹 구성과 분류 근거를 검토한다. 그룹이 없으면 연결된 합성 mock의 **AI로 그룹 만들기**가 실제 모델을 호출하고 일반 자료의 **그룹 초안 만들기**는 참고 엔진이다. 그룹 목록·선택 그룹의 로그·분류 근거를 나란히 조회한다. 로그를 끌어 옮기거나 이름/근거를 수정하고, **병합**에서 합칠 그룹을 선택한다. **분류 확정 · 판정 시작**은 전체 그룹의 유효한 근거 초안을 일괄 채택하고 분류를 확정한다.
8. 2단계 **그룹별 판정**에서 원문 로그 옆의 **사용자 판정**에 **PASS / FAIL / 보류**와 **판정 근거**를 입력하고 **판정 저장**을 누른다. **다음 미검토 그룹**으로 이어가며 보류는 완료로 세지 않는다. AI review의 제안 출처와 미연결 상태를 표시한다. 재사용 조건·참고 사례·판정 이력·분류 근거는 펼쳐서 조회한다.
9. 다음 보고서에서 확정된 분류 사례와 그룹 판정 근거를 각각 참고한다. 기존 **사용자 판정·AI review** 탭은 보고서 전체의 별도 판정·과거 이력이다. 전문 용어를 유지하면서 상단 맥락을 압축하고 작업 영역을 정렬한다. 세부 집계는 **상세 현황**, 설정과 출처는 **설정과 이전 기록**에서 조회한다. 기준은 [요구사항 18.11절](../requirement.md#1811-처음-방문한-사람을-위한-검토-화면)이다.

기본 테스트 계정은 `user04`(정민수, Atlas CPU/Cygnus GPU 담당)다.
`user01`은 Atlas TOP이며 지정 권한 없이 세 블록을 조회한다.
**공용 테스트 비밀번호 `sanity-workspace`는 공개 fixture 값이며 회사 비밀값이 아니다.**
이 계정 제공 방식은 합성 자료만 있는 독립 테스트 환경용이다. 회사 인증 설정이 없을 때 자동 선택하는 fallback이 아니다.

| ID | 이름 | 조회·지정 범위 |
|---|---|---|
| user01 | 이서연 | Atlas TOP, 조회 전용 |
| user02 | 박지훈 | Boreal TOP, 조회 전용 |
| user03 | 김도윤 | Cygnus TOP, 조회 전용 |
| user04 | 정민수 | Atlas CPU / Cygnus GPU, PoR 지정 |
| user05 | 최유진 | Atlas GPU / Cygnus DDR, PoR 지정 |
| user06 | 한지민 | Atlas DDR, PoR 지정 |
| user07 | 오서준 | Boreal CPU, PoR 지정 |
| user08 | 윤지호 | Boreal GPU, PoR 지정 |
| user09 | 임수빈 | Boreal DDR, PoR 지정 |
| user10 | 강현우 | Cygnus CPU, PoR 지정 |

초기 PoR은 미지정이다. 검사 파일 수신만으로 PoR을 자동 지정하지 않는다.
PoR은 블록의 NET_VER·REVISION·PRE/POST 버전을 참조하며 ECO나 검사 실행을 포함하지 않는다.
버전·ECO 조회, PoR 지정, 검사 PASS, 사람 검토 완료는 별개의 상태다.

프로젝트 현황의 검사 건수는 PoR이 있으면 해당 버전, 미지정이면 최신 수신 버전의 결과다.
각 행에 기준 버전을 표시하며 PoR 버전이 미수신이면 다른 버전의 검사 건수를 대신 표시하지 않는다.

검사 대장은 `item_id`별로 분리한다. 현재 예시는 GCA 제약조건·VC LP 저전력·SpyGlass Lint 검사다.
항목별 전체/PASS/FAIL/보류/미수신 건수는 버전·ECO 전체에서 집계하며 페이지·검색·결과 필터에 따라 바뀌지 않는다.
검색·도구/결과 필터·25건 페이지는 선택한 항목 안에서만 적용한다. 항목을 바꾸면 검색/필터/페이지를 초기화하며,
선택 항목은 URL의 `item`에 기록해 새로고침 후 복원한다. 원본 report와 PoR은 변경하지 않는다.
알 수 없는 item ID는 그대로 표시하며, ID가 없는 과거 자료는 `항목 미분류`로 분리한다. 도구명으로 item을 추정하지 않는다.
공통 테마는 최초 팔레트의 밝은 중성 배경과 짙은 초록색 포인트 `#294f46`를 사용한다.

## 구현 범위와 저장 계약

- [sanity_workspace.py](sanity_workspace.py): 사용자·배정·버전·조회 범위와 PoR 변경 서비스.
- [workspace_api.py](workspace_api.py): `/workspace/api` 세션·범위 조회·변경 API.
- [workspace_app.py](../devtools/workspace_app.py): 명시적인 독립 테스트 실행 진입점. 회사 `.env`를 읽지 않는다.
- [Workspace.tsx](dashboard/src/Workspace.tsx): 하나의 로그인 상태로 프로젝트/버전/검사/이력을 연결한 화면.
- [005_workspace.sql](../db/005_workspace.sql): 기존 OA 테이블의 관리 레코드 확장. 네 테이블을 유지한다.
- [006_workspace_reviews.sql](../db/006_workspace_reviews.sql): 독립 사용자 판정·불변 사례/제안과 참조 FK.
- [sanity_workspace_review.py](sanity_workspace_review.py), [sanity_review_advice.py](../common/sanity_review_advice.py): 사례 저장·개정/참조 검사와 확정 조건 적용.
- [WorkspaceReview.tsx](dashboard/src/WorkspaceReview.tsx): 사용자 판정·조건 확정·제안/사례/이력 화면.
- [007_workspace_pipeline.sql](../db/007_workspace_pipeline.sql): 분류 개정과 그룹 판정을 원본/버전/확정 분류 event에 연결.
- [008_submissions.sql](../db/008_submissions.sql), [submission_receiver.py](submission_receiver.py), [sanity_workspace_sources.py](sanity_workspace_sources.py): V2 source staging/전체 검증·PRE 참조/재검사·최신/이력·전체 로그 페이지와 접수 응답. 원본 파일은 VWP에 보관한다.
- [sanity_workspace_clustering.py](sanity_workspace_clustering.py), [sanity_workspace_group_review.py](sanity_workspace_group_review.py): 두 단계의 저장·참조·재검토 검사.
- [WorkspacePipeline.tsx](dashboard/src/WorkspacePipeline.tsx): 분리·병합·분류 확정과 그룹 판정 화면.
- [WorkspaceWorkbench.tsx](dashboard/src/WorkspaceWorkbench.tsx), [sanity_workspace_pipeline.py](sanity_workspace_pipeline.py): 순차 작업 화면·페이지 조회·서버 편집·재개 가능한 분류 작업.
- [sanity_large_samples.py](../devtools/sanity_large_samples.py), [seed_large_workspace.py](../tools/seed_large_workspace.py): 도구별 단일 report의 로그 10,000건/100,000건을 실제 공개 참고 parser로 추출한 합성 자료. 기존 자료를 덮어쓰지 않는다.

신규 `WS_CONFIG/USER/SESSION/PROJECT/BLOCK/VERSION/POR/POR_EVENT/REPORT`는 OA 전용 관리 레코드다.
`workspace_key`, 프로젝트/블록 참조, 현재/대상 버전 FK를 사용하며 가짜 run/bundle ID를 만들지 않는다.
새 관리 행만 `run_id`·`bundle_id`가 NULL이고 기존 분석/분류 행의 필수 scope는 새 CHECK로 유지한다.
원래 `form`·`002`·`003`·`004`, 공유 queue와 기존 불변 report 계약은 변경하지 않는다.
테이블 소유/DDL/DELETE/TRUNCATE 권한은 앱에 추가하지 않았고 UPDATE는 기존 state/version/시각 컬럼만 사용한다.

PoR 변경은 최신 서버 배정을 다시 검사하고 요청 UUID advisory lock → 블록 PoR 행 잠금 → 기대 개정 확인 →
PoR state 변경 + 불변 이력 추가를 한 트랜잭션으로 수행한다. 동일 UUID/내용은 같은 결과를 반환한다.
다른 내용의 UUID 재사용과 동시 변경의 오래된 개정은 409로 거부한다. 다른 블록/버전은 지정할 수 없다.
PoR 변경은 검사 원문·판정·다른 블록의 PoR을 수정하지 않는다.

`GET /workspace/api/blocks/{block_id}/versions/{version_id}/reports`는 선택적 `item_id`를 정확히 일치시켜 필터링한다.
응답의 `groups`에는 현재 버전·ECO의 전체 item별 건수·판정 건수·파싱 건수·도구 목록을 포함한다.
기존 `items/total`은 선택한 item과 필터·페이지 기준이며 `counts`는 기존과 같이 버전·ECO 전체 판정 집계다.
`item_id`를 생략한 API 호출의 기존 전체 목록 동작은 유지한다. UI는 항상 한 item을 선택한 뒤 그 목록만 표시한다.

세션은 DB에 보관하며 브라우저에는 HttpOnly·SameSite=Strict cookie만 둔다. HTTPS 접속에는 Secure도 적용한다.
활성 상태와 만료/로그아웃 상태를 매 요청 확인하고, 쓰기에는 명시적 Origin과 세션 CSRF token을 검사한다.
인증 값은 localStorage/URL에 쓰지 않는다. 사용자 이름·권한·지정자는 요청 본문에서 받지 않는다.
기존 SSO의 `AUTHORIZATION_NOT_IMPLEMENTED`와 회사 어댑터는 유지된다.

## 예시 검사 자료의 출처와 한계

[공개 report 형식 인계](../vwp_bridge/integration/PUBLIC_REPORT_FORMATS.md)의 DB 검증 스냅샷을 digest/출처 표식과 함께 확인한 뒤
독립 OA DB에 import한다. 원문/추출 결과는 직접 파싱·저장·수신 검증한 예시이며 회사 자료는 없다.
1,000개 예상 item에는 보고서 미수신 50개가 포함된다. 확인된 결과는 각 블록의 NET_001 POST / chip_finish에만 연결한다.
그 밖의 PRE/이전 버전/eco01은 **미수신**이며 다른 버전의 결과를 복사해 채우지 않는다.
카탈로그와 PRE/POST 참조는 이 테스트용 명시적 seed다. 기존 VWP payload를 운영 버전 전송 계약으로 바꿔 해석하지 않는다.

현재 쓰기 기능은 PoR 지정, clustering 초안·그룹 구성/확정, 보고서/그룹별 사용자 판정·재사용 조건과 참조 제안이다.
프로젝트 신규 생성·담당자 변경·운영 사용자 디렉터리·실제 VWP 버전 수신은 이 구현에 포함하지 않는다.
사내 SSO/실제 버전 계약 연결은 보호된 공통 코드와 권한 경계를 검토한 별도 후속 작업이다.

## 두 단계의 그룹 검토

**사용자 정정에 따른 목표:** 매 report의 initial groups는 VWP AI가 로그 내용과 허용된 과거 사람 확정 사례를 참고해 만든다.
사람이 초안의 소속·이름·분류 근거를 수정·확정하면 DB에 사례가 축적되고, 다음 AI 호출의 참고 입력 후보가 된다.
기존 그룹과의 일대일 매칭·고정 그룹 사전·공통 분류 유형 등록을 필수 단계로 두지 않는다. 관련 과거 사례가 없어도 AI가 새 그룹을 제안한다.
사람 확정 사례의 축적은 기본 동작으로 설계하며 별도 재사용 체크/필수 기준 입력을 선행 조건으로 삼지 않는다.
현재 UI의 선택형 재사용과 아래의 정확한 필드 비교 엔진은 이 목표에 맞춰 전환할 구현 과제이며, 이 문서 수정만으로 동작이 바뀐 것은 아니다.

현재 [그룹별 분류 근거](../requirement.md#group-clustering-rationale)는 V2로 구현되어 있다.
그룹의 `id`·`label`·`record_ids`는 유지하고 event의 `rationales[group_id]`에 별도로 저장한다.
분류 `reason`은 선택적인 전체 메모이고 2단계의 그룹별 `reason`은 판정 사유다.

두 단계는 같은 원본 로그에서 이어지지만 **분류 확정**과 **그룹 PASS/FAIL 판정**은 서로 다른 event/개정이다.
원본 보고서·규칙 판정·PoR은 두 단계의 변경으로 수정되지 않는다. 담당자만 수정·확정하며 TOP은 동일한 이력을 조회한다.

1. `WS_CLUSTER` head와 불변 `WS_CLUSTER_EVENT`가 분류 초안·수정·확정·확정 해제를 저장한다.
   원본 record ID 전체가 정확히 한 그룹에 속하는지 검사한다. PASS 로그도 분류 분모에 포함한다.
   선택 로그를 기존 그룹으로 옮기면 병합하고 새 그룹으로 옮기면 분리한다. 빈 그룹은 저장하지 않는다.
2. 확정된 분류의 그룹 ID·분류 event ID/개정·전체 구성 해시를 참조해 `WS_GROUP_REVIEW`와 불변
   `WS_GROUP_REVIEW_EVENT/PROPOSAL`을 저장한다. 원본 FAIL에 사용자 PASS도 사유와 함께 기록할 수 있다.
3. 다음 분류 초안은 같은 scope·호환 버전의 다른 보고서에서 사람이 확정한 분류 사례를 조회한다.
   현재 참고 엔진은 공통 `assignment` 규칙과 정확한 필드별 사례의 그룹 관계를 적용한다.
   현재 참고 엔진에서 분류 사례의 적용은 별도 선택이며 코드·심각도와 확인된 모드를 항상 구분한다. 이는 위 사용자 정정 이후의 실제 AI 기본 동작과 구분한다.
4. 그룹 판정 제안은 현재 유효한 확정 분류에 연결된 사람의 판정 조건만 적용한다.
   분류가 정정/철회되면 해당 그룹의 이전 판정은 재검토 대상이며 다음 판정의 확정 근거에서 제외한다.

분류 개정이 바뀌면 동일 그룹 ID라도 이전 판정을 자동 승계하지 않는다. 구성에서 사라진 그룹의 판정도
**이전 구성에서 제거된 그룹의 판정 이력**에서 조회할 수 있다. 다른 보고서의 분류 사례를 참조한 초안/분류는
참조 개정 변경을 감지하고 재검토한다. 자기 자신을 참조하는 분류 경로는 사례 조회에서 제외한다.
목록의 **그룹 검토** 열은 현재 확정 분류의 그룹 수·유효 판정 수·재검토 수를 표시한다.
기존 **사용자 판정** 열은 보고서 전체의 별도 판정이며 그룹 판정으로 덮어쓰지 않는다.

참고 엔진은 모델이 아니다. 동일 필드의 로그를 사람이 분리해 기계적으로 구분할 수 없거나 서로 다른 사례가
충돌하면 임의 순서로 배정하지 않고 개별 그룹으로 남겨 사람이 확인하도록 한다. 자유 서술의 분류 기준을 실행하지 않는다.
실제 사내 AI의 `ClusteringAdvisor`와 `ReviewAdvisor`는 VWP용 추상 계약이며 `CORPORATE`는 현재 503 미연결이다.
새 workspace 계약은 기존 봉인된 bundle/legacy 분류 job을 가짜로 생성하거나 재해석하지 않는다.
기존 VWP 분류 엔진과는 공통 exact-field matcher/검증을 재사용하며, 실제 모델/운영 VWP 전송 연결은 별도 인수인계 범위다.

업무 규모는 report당 **평균 약 10,000건이며 100,000건 이상도 가능**하다. 10,000건이나 100,000건을 업무상 상한으로 쓰지 않는다.
현재 서버의 자원 보호 예산은 report JSON 128MiB, 로그/그룹 각각 1,000,000개, 분류 사례 1,000건·사례 로그 합계 1,000,000개·사례 원본 JSON 합계 128MiB다. 바이트 예산이 먼저 제한할 수 있다.
report/사례 바이트와 사례 전체 건수는 큰 payload를 가져오기 전에 DB에서 검사한다. 그룹 판정 사례는 최대 100,000건이며 화면에는 최근/참고 사례 50건을 구분해 표시한다.
모든 입력을 처리할 수 없는 경우 한도 오류로 중단하며 일부만 처리해 확정하지 않는다. 한 초안의 참조 사례도 최대 100건이다.
서버 편집 명령 요청은 최대 16MiB, 다른 분류 요청은 4MiB이며 기존 판정/PoR 요청은 16KiB 한도를 유지한다. 주 화면은 전체 구성 전송 대신 ID를 명시한 서버 편집 명령을 사용한다.
이 예산 안의 모든 크기에 대한 시간/메모리 보장을 뜻하지 않는다. 10,000건과 100,000건 합성 검증의 실제 바이트·그룹 분포·측정 결과는 [REVIEW.md](../REVIEW.md)에 기록하고 더 큰 운영 입력은 사내에서 측정한다.
OA 응답은 페이지로 제한하지만 서버는 요청마다 해당 report JSON과 현재 구성을 읽어 전체 검증한다. 서버 메모리·조회 비용은 원본 크기에 비례한다. 원본을 일정 메모리로 스트리밍 처리하는 구현이나 동시 10명의 대형 report 부하 보장은 아니다. 더 큰 원문·긴 로그·동시 요청의 운영 예산은 실제 서버에서 측정하고 공통 코드 변경이 필요하면 Codex가 검토한다.

사용자가 확정한 [대용량 순차 검토 명세](../requirement.md#large-report-review-flow)는 **한 sanity item의 report 내 평균 약 10,000건·100,000건 이상 로그 → 전체 분류·사람 확정 → 확정 그룹별 판정·사람 확정**이다.
이 순서는 아래 전용 작업 화면에 연결했다. 기존 소규모 모달/대부분 한 건짜리 샘플과 10,000행 DB 측정만으로 이 종단 검증을 대신하지 않는다.

API는 보고서 경로 아래 `GET /clustering`, `POST /clustering/proposals`, `POST /clustering`과
`GET/POST /groups/{group_id}/review`, `POST /groups/{group_id}/review-proposals`다.
분류 수정은 `action=EDIT/CONFIRM/WITHDRAW`, 기대 개정·UUID·사유를 사용한다.
그룹 판정은 기존 판정 필드와 **`expected_cluster_revision`**을 함께 보내며 판정 개정과 분류 개정을 모두 검사한다.
동일 요청/내용은 같은 결과를 반환하고 오래된 개정·제안은 거부한다. 분류/판정 쓰기는 같은 짧은 DB 잠금으로 직렬화한다.

## 그룹별 분류 근거 — V2 구현

기준은 [요구사항 18.10절](../requirement.md#group-clustering-rationale)이다. `common/sanity_workspace_rationale.py`의 공통 검증과 기존 불변 분류 event에 저장한다.

| 항목 | 구현된 동작 |
|---|---|
| AI 초기 그룹 | 그룹마다 공통 패턴·조건·다른 그룹과 구분한 이유, 예외/미확인 사항과 자신의 대표 원본 record 참조를 제안 |
| 사람 검토 | AI 설명 채택 또는 설명/참조 정정과 이유 저장. AI 원문·최종 근거·검토자/시각·수정 이력 분리 |
| 참조와 개정 | 원본 해시·분류 event/개정·group ID/이름·해당 그룹 전체 구성 해시에 근거를 연결. 입력 사례의 event/개정/group 참조도 보존 |
| 수정 후 처리 | 이동한 출발/도착 그룹, 분리/병합한 그룹의 근거 재검토. 구성과 근거 상태를 같은 개정에 저장. 의미가 그대로인 다른 그룹의 유효 근거는 출처를 기록해 이어받을 수 있음 |
| 확정 조건 | 근거 미기록/미검토/재검토가 남으면 전체 분류 확정과 2단계 새 판정 저장 차단. 전체 메모는 선택 입력이며 그룹별 근거 대체 불가 |
| 다음 AI 입력 | 사람이 확정한 구성·이름·그룹별 최종 근거·원본 참조·정정 이력을 관련 사례로 함께 제공 |
| 기존 자료 | 과거 event/모델 원문/판정/PoR은 그대로 보존. 누락 근거를 표시하고 새 기준 확정 전 보완. 전체 사유·자동 이동 문장을 그룹별 근거로 복제하지 않음 |

근거 본문은 1~2,000자, 대표 원본 참조는 그룹 구성원 중 1~20건이며 기본 최대 3건을 보여 준다.
대표 참조/페이지 열람이 전체 분석 완료를 의미하지 않는다. 전체 구성의 단일 배정은 기존처럼 서버에서 검사한다.
현재 선택 그룹의 로그 위에 분류 근거를 표시하고 목록에는 짧은 설명·상태를 제공한다. 예외·출처·과거 수정은 펼쳐서 조회한다.
전체 근거 미기록/미검토/확인/재검토 집계와 미해결 그룹 이동을 제공하며 2단계 PASS/FAIL 사유·진행 수치와 구분한다.
명시적인 개별/전체 대상 채택을 지원하되 현재 페이지 선택이나 자동 소속 변경을 사람의 전체 근거 확인으로 기록하지 않는다.
drag & drop 저장 때마다 긴 사유 창이나 추가 AI 호출을 강제하지 않고 전체 분류 확정 전에 근거를 보완한다.

평균 10,000건·100,000건 이상과 다수 그룹에서 기존 그룹 25개·로그 50건의 페이지 구조를 유지한다.
metadata에는 전체 상태 집계, 목록에는 요약, 선택 그룹에는 개정을 고정한 상세/이력을 조회하며 모든 근거 본문/참조를 초기 응답에 넣지 않는다.
근거만 정정해도 새 분류 개정·재확정을 요구하고 기존 판정의 개정 검사는 유지한다.
담당자 쓰기/TOP·과거 개정 읽기, 기대 개정·UUID·범위 검사와 기존 네 테이블/DB 전달/SSO 경계를 유지한다.
공통 저장/API/모델 JSON V2 계약은 [PROTOCOL](../PROTOCOL.md)과 [SDK 출력](../vwp_bridge/CLUSTERING_INTEGRATION.md)을 따른다. 내부 AI의 SDK 어댑터 허용 범위는 유지한다.

## Codex 모델을 사용하는 합성 mock 시험

사용자 요청에 따라 `gpt-6-luna`를 Codex CLI의 기존 로그인으로 호출하는 별도 실험을 추가했다.
공식 [모델 안내](https://developers.openai.com/codex/models)는 Luna를 반복·분류 작업용 저비용 모델로 안내한다.
이 환경에서는 ChatGPT 로그인 사용량으로 처리하며 API 단가로 실제 청구액을 산정하지 않는다. 별도 API 키/SDK는 필요하지 않다.

```bash
codex login status
.venv/bin/python tools/run_codex_mock.py --live --pg-bin /path/to/postgresql/bin
```

명령마다 새 SCRAM·UNIX socket 전용 PostgreSQL을 생성하고 반드시 종료한다. 일반 회귀 테스트에서는 외부 모델을 호출하지 않는다.
이 CLI는 `--live`가 있어야 실제 호출하며, 회사 `.env`나 운영 report를 읽는 경로가 없다. 정확히 생성된 3개 합성 report와 해당 report의 사람 확정 사례만 사용한다.
모델은 빈 작업 폴더에서 파일·shell·웹·MCP·하위 agent 도구 없이 JSON만 반환한다. 사용자 CLI 설정·프로젝트 지침/회사 환경변수는 전달하지 않는다.
Codex CLI 자체 로그인은 CLI가 관리한다. 자격증명을 읽거나 DB 설정과 함께 모델 입력으로 보내지 않는다.

| report | 비교 목적 | 이번 실제 호출 결과 |
|---|---|---|
| `codex-mock-source` · 8건 | 사례 0건에서 초기 분류 | 클록 누락 4건·입력 지연 누락 4건을 2개 그룹으로 제안 |
| `codex-mock-control` · 12건 | 수정 사례가 없는 비교 분류 | 클록 누락·입력 지연 누락·폭 불일치를 3개 그룹으로 제안 |
| `codex-mock-feedback` · 12건 | DB의 사람 확정 사례 1건을 제공 | 앞의 두 유형 8건을 **제약 설정 누락 통합 검토**로 묶고 새 폭 불일치 4건은 별도 그룹 |

테스트 코드가 source의 초기 두 그룹을 하나로 합치고 한국어 이름·정정 이유를 작성해 사람 확정 API 계약으로 저장한다.
이는 **사람 수정을 자동 모사한 테스트**이며 실제 담당자의 업무 승인으로 표시하지 않는다. `reusable=false`, 기준 입력 없이 확정한 사례가 다음 모델 입력에 포함된다.
control/feedback은 같은 내용과 경로를 사용하며 report/record ID만 다르다. source와는 경로·메시지가 달라 정확한 객체명 일치만으로 묶을 수 없다.
실제 출력은 그대로 보존한다. 이번 source/control의 최초 이름·설명은 한국어 요청에도 중국어로 반환됐으며, feedback은 사람이 정한 한국어 이름을 유지했다.
따라서 분류 피드백 동작과 모델의 언어 지시 준수는 구분하며 한국어 출력 품질을 보장하지 않는다.

원본 ID 전체 단일 소속·현재 scope·참조 사례·고정 원본/사례 해시를 검증한 뒤 기존 `WS_CLUSTER_EVENT`/head에 저장한다.
실제 호출한 event만 `model_called=true`이며 모델·prompt 버전·입력 해시·토큰·소요 시간·참고 사례 수를 기록한다.
사람 편집/확정 event는 `model_called=false`이고 최초 모델 metadata와 event 참조를 보존한다. AI는 분류나 PASS/FAIL을 자동 확정하지 않는다.
모델 호출은 DB 쓰기 잠금 전에 수행하고 저장 시 개정/사례를 재검사한다. 응답 불명·오류에 자동 재호출이나 참고 엔진 fallback을 하지 않는다.

`artifacts/<실험>/result.json`, `trace.json`, `export.json`에 결과를 남긴다. 성공한 결과는 추가 호출 없이 다음 명령으로 독립 workspace에 이관할 수 있다.
기존 행·PoR·원본을 변경하지 않으며 동일 UUID 재실행도 새 호출/중복 저장을 하지 않는다.

```bash
.venv/bin/python tools/seed_codex_mock.py --config artifacts/workspace-runtime.json --result-dir artifacts/<실험>
cd oa/dashboard
npx playwright test --config playwright.codex.config.ts
```

브라우저 검증은 이관된 별도 8003 합성 workspace가 필요하며 모델을 호출하지 않는다. 화면에 모델과 참고 사례를 표시하고,
실제 모델 초안의 확정 영역에서는 선택형 재사용 체크 대신 확정 시 사례로 저장됨을 안내한다.
일반 화면의 참고 초안/판정 제안은 기존 엔진이다. 기존 제안 API의 HTTP mode로 `MOCK_CODEX`를 입력할 수 없고 사내 `CORPORATE`는 계속 503이다.
이번 실험은 **8/12/12건·분류 3회**다. 연결 확인 1회를 별도로 실행했으며 10만 건 전체 모델 호출·2단계 모델 판정·사내 SDK 연결은 검증하지 않았다.
이전 대용량 시험은 참고 엔진의 처리/DB/화면 시험이다. DB 사례를 다음 prompt에 제공하는 동작이며 모델 가중치를 학습시키는 fine-tuning은 수행하지 않는다.

### 화면의 AI 분류 버튼

후속 요청으로 `/workspace`의 **1단계 로그 분류 → AI로 그룹 만들기**을 실제 Codex 호출에 연결했다.
공유 테스트 환경의 `codex-mock-source/control/feedback`에서 사용할 수 있다. 다른 report는 참고 엔진 버튼과 분류 설정의 사내 AI 미연결 설명을 제공한다.
블록 담당자에게만 생성 버튼이 보이고 TOP은 같은 초안/진행 상태를 조회한다. **참고 엔진으로 초안 생성**과 모델 버튼을 구분한다.
AI는 초안만 저장한다. 기존 확정을 새 초안으로 대체하면 과거 구성/판정은 이력으로 보존하며 다시 사람이 분류를 확정해야 2단계로 진행한다.

- 서버 시작의 비공개 합성 runtime JSON에 `codex_mock_enabled: true`를 명시해야 한다. 기본은 false다. DSN은 제한된 로컬 `sanity_test_mock`·UNIX socket인지 검사한다.
- `GET/POST .../reports/{report_id}/clustering/ai`는 기존 로그인·범위·Origin/CSRF 경계를 사용한다. POST 본문은 `expected_revision`, `request_id`뿐이며 모델/원문/담당자를 클라이언트가 지정하지 않는다.
- 호출 전에 `WS_CLUSTER.state.codex_jobs`에 UUID·고정 입력/사례 해시·요청자·진행을 기록한다. 전용 API는 동기 요청 안에서 모델을 호출하지만 DB 잠금/트랜잭션은 유지하지 않는다. 브라우저가 이탈하거나 새로고침해도 서버 처리는 이어지고 화면은 GET으로 상태를 조회한다. GET/자동 polling은 모델을 호출하지 않는다.
- 같은 UUID는 이미 실행 중/완료/실패한 요청을 반환하며 다시 호출하지 않는다. 상태는 `RUNNING/RESULT_READY/COMPLETED/FAILED/STALE/UNKNOWN/SAVE_FAILED`다. 받은 receipt를 먼저 보관하고 전체 ID·최신 개정/사례 검증 후 초안을 원자 저장한다. `SAVE_FAILED`의 **받은 AI 결과 저장**은 보관된 결과만 적용한다.
- 서버가 중단된 호출은 `UNKNOWN`, 보관된 결과의 저장 중단은 `SAVE_FAILED`로 복원한다. 자동 재호출/자동 적용은 하지 않는다. `UNKNOWN` 후 새 요청은 사용자가 별도로 실행하며 추가 사용량이 발생할 수 있음을 표시한다.
- 모델 생성 중에는 같은 report의 수동 재분류/확정·그룹 판정을 차단한다. 참조 사례가 호출 중 바뀌면 결과를 `STALE`로 남기고 기존 구성을 유지한다.
- 공개 mock의 자원 예산은 동시에 1개, report별 총 20개 UUID다. 실패 요청도 이력과 예산에 포함하며 과거 UUID를 삭제해 재사용하지 않는다. 이는 운영 규모나 보고서 건수 제한이 아니다.
- 사람이 이 테스트의 그룹 이름·검토 사유에 입력한 내용도 다음 모델 prompt에 포함된다. 실제 사내 자료/비밀값을 이 테스트에 작성하지 않는다. 회사 report와 사내 SDK 연결은 여전히 VWP 경계로 별도 구현한다.

브라우저의 실제 호출 검증은 별도 명시적 실행만 허용한다. 일반 회귀/화면 테스트에는 포함하지 않는다.

```bash
cd oa/dashboard
SANITY_CODEX_LIVE_TEST=1 npx playwright test --config playwright.codex-live.config.ts
```

이 시험은 합성 QA 8003에서 버튼 클릭 1회·호출 중 새로고침·결과 복원·동일 UUID 재요청의 추가 호출 없음·원본/PoR 불변·TOP/모바일 표시를 확인한다.
실제 호출은 12건이며 100,000건 전체 모델 처리나 2단계 모델 판정의 검증으로 쓰지 않는다.

## 대용량 report의 순차 검토

순차 작업 화면은 `review={report_id}`를 현재 프로젝트·블록·버전·ECO·item의 URL과 함께 보존한다.
새로고침하면 같은 대상에서 다시 조회한다. 대상 report는 선택/행 동작으로 명시하며 검색 결과의 첫 행을 자동 판정하지 않는다.
기존 모달과 보고서 전체 판정 이력은 유지한다. 로그가 1,000건을 넘는 report의 근거 동작도 전용 작업 화면으로 연결한다.

- **1단계:** 전체 건수·원문 행 수·분류 개정을 확인하고 초안을 생성한다. 그룹은 25개, 선택 그룹의 로그는 50건씩 조회한다.
  검색/페이지를 바꿔 선택한 로그 ID는 유지하며 그룹 전체 선택은 별도 동작이다. 서버가 전체 구성에서 분리/병합/이동을 처리한다.
  로그 손잡이를 왼쪽 그룹에 끌어 놓으면 즉시 이동한다. 선택한 로그를 끌면 페이지 간 선택 전체, 선택하지 않은 로그는 한 건만 이동한다.
  **새 그룹으로 분리**에 놓으면 이름 입력 창을 제공한다. 이름 변경과 그룹 병합도 필요할 때만 창을 연다.
  개별 변경 사유는 선택이며 비워 두면 실제 조작 내역을 기록한다. 전체 분류 확정에는 별도의 사람 검토 사유가 필요하다.
  이름 변경 창은 저장/취소해야 다른 그룹이나 2단계로 진행할 수 있다. 참고 엔진 재사용 옵션은 확정 영역에서 펼친다.
- **작업 재개:** 현재 참고 엔진의 범위 크기는 `max(500, ceil(record_count / 20))`이다. 평균 규모 10,000건은 500건씩, 100,000건은 5,000건씩 총 20개 범위로 처리한다. 이는 결정적 참고 엔진의 작업량 조절이며 실제 모델의 토큰 예산이 아니다. 부분 결과·다음 작업 번호·입력 해시·사례 목록 해시·진행/오류가 DB에 저장된다.
  화면을 떠나면 자동 진행은 멈추며, 다시 열어 **저장한 위치에서 이어서 처리**할 수 있다. 실패는 자동 재호출하지 않는다.
  명시적인 이어서 처리/취소를 제공하고 원본·기준·분류 개정이 바뀌면 STALE로 중단한다.
- **단계 전환:** 전체 source ID의 단일 배정 검증을 통과한 완성 초안만 분류 event가 된다. 사람이 현재 분류 전체를 확정해야 2단계를 연다.
  진행/실패/오래된 작업, 미완료 근거와 미저장 이름 변경은 전환을 막는다. 서버도 현재 확정 여부·기대 개정을 검사한다.
- **2단계:** 그룹별 원본 규칙 결과·제안·사람 판정·사유/조건을 분리한다. 그룹 전체의 facts로 조건을 확인하며 현재 보이는 50건만으로 판단하지 않는다.
  전체/확정/미검토·보류·재검토를 표시하고 현재 모든 그룹의 사람 PASS/FAIL 확정만 완료에 포함한다. FAIL 검토 완료는 검사 PASS가 아니다.
- **근거/이력:** 전체 원문은 명시적으로 다운로드하고 로그/과거 분류 구성은 페이지로 조회한다. 긴 개별 원문은 앞부분 표시임을 명시한다.
  제거된 그룹의 판정은 별도 읽기 전용 이력에서 확인한다. 현재 목록에는 전체 과거 구성원 배열을 보내지 않는다.

검토 중에는 블록/버전 선택·PoR 요약·항목 목록의 중복 표시를 줄이고 보고서 맥락·두 단계·그룹/로그 두 영역을 제공한다.
**검사 대장으로** 돌아가면 기존 버전/PoR 기능을 다시 사용할 수 있다. 설정·출처·원본 검색·이력은 접힌 상태에서 시작한다.
키보드는 로그 손잡이에서 Space/Enter → ↑/↓로 그룹 선택 → Enter 이동, Escape 취소다. 선택 로그 이동 버튼도 사용할 수 있다.
모바일은 손잡이에서 터치 드래그하며 화면 위/아래 가장자리와 그룹 목록 안쪽에서 자동 스크롤한다. 나머지 로그 영역은 일반 스크롤을 유지한다.
같은 그룹/대상 밖 드롭·취소는 쓰지 않는다. 읽기 전용/과거 구성/AI·참고 작업 진행 중에는 손잡이를 제공하지 않는다.
저장 응답이 유실된 동일 조작은 같은 UUID로 재시도하고, 개정 충돌은 최신 상태를 불러온다. 소속 변경은 기존 `MOVE` API이며 원본/PoR을 수정하지 않는다.

기존 `WS_CLUSTER`의 mutable `state`에 `task`와 과거 작업 요약 `task_history`를 보관한다. source payload와 기존 event는 수정하지 않는다.
task는 요청 UUID/command·고정 원본/기준 해시·기대 분류 개정·선택 필드·범위별 해시/진행·오류·미완성 그룹을 가진다.
`next_chunk`가 이미 지난 재시도는 현재 진행 상태를 반환하고 같은 범위를 중복 처리하지 않는다. 최대 1,000개 과거 작업 요약을 유지하며 초과하면 명시적 한도 오류다.
작업 진행 중에도 head의 분류 개정은 그대로다. 최종 전체 검증과 새 불변 event/head 변경은 한 트랜잭션에서 수행하며 완료된 task에는 구성원 배열 대신 범위/입력 근거만 남긴다.
새 물리 테이블/DDL/권한 확대는 필요하지 않으며 `005`~`007` 설치 계약을 유지한다.

다음 경로는 기존 report API 경로 아래에 추가한다. 모두 같은 세션·배정·Origin/CSRF 검사를 적용한다.

| API | 반환/쓰기 범위 |
|---|---|
| `GET /workbench` | 원문/records/전체 소속/과거 snapshot을 제외한 현재 metadata·진행·전체 요약 |
| `GET /clustering/groups` | `expected_revision`, offset/limit, search, status, rationale_status로 그룹 요약 조회. 기본 25개, 최대 100개 |
| `GET /clustering/records` | `expected_revision`, 선택 group_id, 검색/페이지의 원본 근거. 기본 50건, 최대 100건 |
| 위 두 GET의 `event_id` | 같은 report의 불변 과거 분류를 읽기 전용 페이지로 조회 |
| `GET /groups/{group_id}/rationale` | `expected_revision` 또는 과거 `event_id`, 현재 근거·대표 원본·참고 사례와 이력 페이지(offset/limit, 기본 10, 최대 25). 그룹 밖 대표 ID는 원문 연결하지 않음 |
| `POST /groups/{group_id}/rationale` | `SAVE`(본문/대표 ID/참고 그룹 + 수정 이유, `reviewed` 선택) 또는 `ACCEPT`(기존 근거 명시적 재채택). 새 분류 개정 생성, UUID 재전달 안전 |
| `GET /clustering/history` | 분류 event metadata 페이지. `removed=true`는 제거된 그룹의 판정 목록 |
| `POST /clustering/operations` | `expected_revision`, request UUID, reason, `MOVE/MERGE/RENAME`과 명시적 record/group ID. 같은 UUID/내용은 같은 event 반환 |
| `POST /clustering/tasks` | 고정 분류 개정/필드·UUID로 참고 엔진 작업 생성. `CORPORATE`는 미연결 503 |
| `POST /clustering/tasks/step` | task_id·expected_chunk로 다음 범위 처리. `RESUME/CANCEL`은 명시적 동작 |
| `GET /source` | 권한 범위 내 원본 report 다운로드 |

현재 개정의 페이지는 `expected_revision`이 달라지면 409를 반환한다. 다른 개정의 그룹/원문을 섞어 편집하지 않는다.
분류 저장과 그룹 review GET/POST/proposal에 `compact=true`를 쓰면 구성원 ID/command의 중복 전송을 줄인다.
그룹 snapshot의 label·record_count와 모든 판정/참조 정보는 유지하며 실제 구성원은 고정된 분류 개정의 페이지에서 조회한다.
기존 전체 snapshot 응답 API는 호환성을 위해 유지하되 새 화면의 초기 조회에는 사용하지 않는다.

이 분할 처리는 **현재 OA의 결정적 참고 엔진**이다. 사내 VWP 모델 호출·worker/DB 전송은 아직 연결되지 않았다.
현장의 문맥/토큰/호출 제한에 맞춘 SDK 작업 연결은 [사내 분류 인계](../vwp_bridge/CLUSTERING_INTEGRATION.md)와 Codex 검토를 거친다.

대용량 합성 자료는 기존 1,000개와 별도로 추가한다. 각 파일에 로그 10,000건 또는 100,000건을 포함하며 공개 참고 parser의 전체 건수·원문 위치를 검사한다. 합성 생성기의 100,000건 선택지는 검증 자료의 크기이며 업무 입력의 상한이 아니다.
실제 사내 report 형식/모델 정확도 검증으로 표시하지 않는다. 이미 같은 ID가 있으면 원문을 덮어쓰지 않고 동일성을 검사한다.

```bash
.venv/bin/python tools/seed_large_workspace.py
.venv/bin/python tools/seed_large_workspace.py --count 100000 --family GCA
# 별도 QA에는 기존 CPU 검증 자료와 섞지 않고 Cygnus GPU에 추가한다.
.venv/bin/python tools/seed_large_workspace.py --config /tmp/sanity-workspace-e2e/runtime.json --block project-3-gpu
```

2026-10-15 합성 검증: PostgreSQL 회귀 305개, 기존 브라우저 37개와 workspace 17개를 통과했다.
GCA·VC LP·SpyGlass 각각 단일 report에서 100,000개 record의 파싱/원문 위치를 확인했다.
GCA의 100,000건/100,000개 고유 그룹은 전체 분류 118.851초, 원본 JSON 60,845,516 bytes,
metadata 직렬화 6,831 bytes였다. 해당 시점의 회귀 runner 누적 최대 RSS는 699,168KiB이며 순수 분류만의 메모리 측정값은 아니다.
브라우저의 100,000건/100그룹은 5,000건에서 API·DB를 재시작한 뒤 남은 95,000건의 분류를 89.520초에 마쳤고,
metadata 응답 6,704 bytes, 그룹 25개·원문 50건 렌더와 마지막 원문 검색·사람 판정·모바일을 확인했다.
이 측정은 다른 검증도 실행한 4GB 개발 환경의 참고 엔진 결과이며 실제 회사 AI의 품질/처리량이나 운영 지연 목표가 아니다.
공유 자료는 기존 1,000개를 보존하고 도구별 10,000건 report 3개와 GCA 100,000건 report 1개를 추가한다. 전체 샘플 분포의 평균을 10,000건으로 맞춘 데이터셋은 아니다.

## 사용자 판정과 사례 재사용

[사람의 판정 기준 축적](../requirement.md#human-judgment-learning)의 첫 단계를 실제 workspace API/DB에 연결했다.
검사 대장은 **규칙 판정 / 사용자 판정**을 별도 열로 표시한다. 원본 FAIL에도 사용자 PASS를 사유와 함께 저장할 수 있다.
파싱 미완료·원문 미수신은 보류만 기록한다. 사용자 판정은 원본/근거/규칙 결과나 PoR을 변경하지 않는다.
담당자만 저장/제안을 요청하고 TOP은 같은 판정·이력을 읽는다. 구형 `?demo=1&flow=legacy`의 승인 API와 이 별도 판정 API는 다르다.

- `WS_REVIEW`는 현재 사례를 참조하는 개정 head다. `WS_REVIEW_EVENT`는 판정·사유·조건·버전 snapshot·원본 SHA-256·판정자·이전 개정을 보존한다.
- `WS_REVIEW_PROPOSAL`은 제안·참조 사례 개정·입력 해시·엔진·호출 여부를 보존한다. 제안과 사용자의 최종 판정/수정 여부를 연결한다.
- 정정은 새 event를 만든다. 정정 전 기준은 현재 조회/조건 적용에서 제외하며, 이미 생성된 제안은 stale로 표시하고 확정을 거부한다.
- 요청 UUID와 내용이 같으면 같은 결과를 반환한다. 개정 충돌은 409로 거부하며 원본 해시 불일치는 중단한다.
- 일회성 판정은 사례로만 남긴다. 재사용을 명시한 PASS/FAIL과 원본에서 확인한 조건만 조건 적용 엔진이 사용한다.
- 프로젝트·블록·item·도구·parser·규칙 버전·PRE/POST·ECO는 정확히 구분한다. 기본적으로 원본 NET_VER/REVISION 안에서만 사용한다. 다른 버전 재사용은 담당자의 명시적 선택이 필요하다.
- 원본 record 전체에서 동일하게 확인되는 필드만 조건 후보로 제공한다. 규칙과 확인된 모드는 필수이며 대상/검사 지점/심각도/코드/설계는 선택할 수 있다.
- 현재 엔진은 선택한 조건의 정확한 일치만 적용한다. 자유 서술의 예외·수치 범위·정규식·의미 유사도를 해석하지 않는다. 조건으로 표현할 수 없으면 일회성 사례로 기록한다.
- 참조할 기준이 없거나 PASS/FAIL 기준이 충돌하면 보류를 제안한다. 현재 보고서 자신의 판정을 참고 입력으로 사용하지 않는다.
- 같은 범위의 현재 사례 최대 1,000건을 처리한다. 초과하면 부분 조회로 성공시키지 않는다. 적용 기준 100건 초과도 범위 확인이 필요한 보류다. 화면은 최근 50건과 전체 건수를 구분한다.

현재 제안은 `CONFIRMED_CRITERIA_V1`, `model_called=false`인 **확정 조건 적용**이다. 화면에 사내 AI 미연결을 표시한다.
`CORPORATE` 요청은 `503 AI_NOT_CONFIGURED`이며 실제 모델 호출·가중치 학습·사유에서 일반 규칙 추출은 수행하지 않는다.
사내 AI용 추상 메서드와 출력 검사는 [AI review 인수인계](../vwp_bridge/REVIEW_INTEGRATION.md)에 있다.
사내 SDK 구현과 운영 VWP 작업/DB 전달 연결은 구분하며, 공통 전송 연결은 Codex의 후속 구현·검토 대상이다.

API 경로는 `/workspace/api/blocks/{block}/versions/{version}/reports/{report}` 아래의
`GET /review`, `POST /review`, `POST /review-proposals`다. 세션/배정/Origin/CSRF를 서버에서 검사한다.
판정 POST는 `conclusion`, `reason`, `criteria`, `conditions`, `reusable`, `compatible_versions`,
`expected_revision`, `request_id`, 선택 `proposal_id`를 받는다. 제안 POST는 `expected_revision`, `request_id`, `mode`를 받는다.
검토자와 권한·원본 해시는 서버가 결정한다.

## 독립 환경 생성과 실행

먼저 Python/Node 의존성과 PostgreSQL 12.1+ 서버 바이너리를 준비하고 공개 parser 스냅샷을 생성한다.

```bash
.venv/bin/python tools/build_public_preview.py --pg-bin /path/to/postgresql/bin --count 1000
cd oa/dashboard
npm ci
npm run build
cd ../..
.venv/bin/python tools/run_workspace.py setup --pg-bin /path/to/postgresql/bin \
  --origin http://127.0.0.1:8001
.venv/bin/python tools/run_workspace.py start
```

주소는 `http://127.0.0.1:8001/workspace`다. `setup`의 `--origin`은 정확한 주소를 명시하며 필요한 경우 반복한다.
TLS proxy를 사용할 때는 그 HTTPS origin도 명시한다. 서버는 loopback에만 bind하고 CORS를 허용하지 않는다.
`/`는 워크스페이스로 이동한다. 명시적인 `/?demo=1`과 `/workspace?demo=1`은 같은 최신 UI의 합성 메모리 데모를 연다.
데모는 서버 API·DB·AI를 호출하지 않으며 계정 전환으로 공유하는 변경도 새로고침하면 초기화된다.
이전 번들 검토 대장은 `/?demo=1&flow=legacy`에서 유지한다. 상세 사용법은 [대시보드 안내](dashboard/README.md)를 따른다.
이 별도 테스트 배포의 `/api/*`는 기존 업무 API를 활성화하지 않고 `AUTHORIZATION_NOT_IMPLEMENTED`를 반환한다.

`setup`은 매번 신규 로컬 소켓/SCRAM PostgreSQL과 제한된 OA 계정을 만든다. TCP DB 수신이나 회사 접속 설정 상속은 없다.
DB 파일은 기본 `/var/lib/sanity-review-workspace/` 아래, 비공개 실행 설정은 `artifacts/workspace-runtime.json`에 mode 0600으로 저장한다.
환경에 따라 `--runtime-dir`와 `--config`를 지정한다. 설정/DB/로그/생성물은 Git과 브라우저 정적 자산에 포함하지 않는다.

```bash
.venv/bin/python tools/run_workspace.py status
.venv/bin/python tools/run_workspace.py stop
.venv/bin/python tools/run_workspace.py start
.venv/bin/python tools/run_workspace.py restart
.venv/bin/python tools/run_workspace.py upgrade
```

`stop/start`는 기존 DB를 보존한다. `restart`는 공개 소스 snapshot만 갱신하고 같은 DB로 다시 시작한다.
기존 `005/006` 로컬 fixture는 `upgrade`로 필요한 `006 → 007`을 순서대로 적용한 뒤 `restart`한다.
이 명령은 이 helper의 합성 전용 로컬 DB만 허용한다. API/DB를 일시 중지하고 OS 소유자로 PostgreSQL single-user 모드에서
고정 SQL을 실행하며, 네 테이블 OID와 기존 OA 행 전체의 해시/건수를 전후 비교한다. 인증 설정·앱 권한을 변경하지 않는다.
관리자 비밀번호를 앱 설정에 보관하지 않는다. 이미 적용된 fixture에는 `MIGRATION_CURRENT`를 반환한다.
기존 config가 있으면 `setup`을 거부한다. 같은 seed 재적용은 초기화하지 않고, 다른 seed로 덮어쓰기도 거부한다.
서버와 DB를 명시적으로 종료하기 전까지 백그라운드로 유지한다. 운영 서비스 감독/백업 구성과는 별도다.
사내 DB에는 이 helper를 사용하지 않는다. 운영 SQL 적용은 owner/DBA의 검토·승인 절차를 따른다.

## 검증

```bash
.venv/bin/python db/tests/run_mock.py --pg-bin /path/to/postgresql/bin
cd oa/dashboard
SANITY_DASHBOARD_TEST_PORT=5188 npm run test:e2e
```

실제 워크스페이스 브라우저 테스트는 별도 DB/8002 서버를 사용해 사용자의 PoR을 바꾸지 않는다.
루트에서 별도의 config와 runtime 디렉터리로 준비한 뒤 실행한다.

```bash
.venv/bin/python tools/run_workspace.py setup --config /tmp/sanity-workspace-e2e/runtime.json \
  --runtime-dir /tmp/sanity-workspace-e2e/db --port 8002 --pg-bin /path/to/postgresql/bin \
  --origin http://127.0.0.1:8002
.venv/bin/python tools/run_workspace.py start --config /tmp/sanity-workspace-e2e/runtime.json
.venv/bin/python tools/seed_pipeline_qa.py --config /tmp/sanity-workspace-e2e/runtime.json
cd oa/dashboard
npm run test:workspace
cd ../..
.venv/bin/python tools/run_workspace.py stop --config /tmp/sanity-workspace-e2e/runtime.json
```

`test:e2e`는 기존 demo/API mock 회귀, `test:workspace`는 실제 별도 API/DB 통합이다.
`seed_pipeline_qa.py`는 지정된 `/tmp/sanity-workspace-e2e/db`·8002 합성 QA DB만 허용한다.
Cygnus GPU에 로그 4개씩 가진 분류 원본/후속 보고서 2개를 추가하며 기존 보고서를 덮어쓰지 않는다.
기본 1,000개 공개 형식 예시와 별도의 검증용 fixture다. 공유 워크스페이스에는 이 자료를 추가하지 않는다.
전체 실행 결과와 캡처는 [REVIEW.md](../REVIEW.md)에 기록한다.

로그 이동의 실제 DB/브라우저 테스트는 별도의 합성 **8003** 서버에서 실행한다. `tools/run_workspace.py setup/start`로
같은 포트·출처의 QA config를 준비한 뒤 아래를 실행한다. 기존 Codex mock QA config도 사용할 수 있으며 모델은 호출하지 않는다.

```bash
.venv/bin/python tools/seed_drag_qa.py --config /path/to/private-8003-qa-runtime.json
cd oa/dashboard
npx playwright test --config playwright.drag.config.ts
```

시나리오별 `qa-log-drag-*` report 8개·각 120건을 추가한다. 기존 report가 다르면 교체를 거부하며,
테스트는 이 QA report의 분류만 새 개정으로 시작한다. 단일/페이지 간 다중 이동·새 그룹/취소·키보드·터치 자동 스크롤·
개정 충돌·저장 응답 유실 후 UUID 재시도·TOP 조회 및 원본/PoR 불변을 검증한다.
공유 8001 서버는 이 seed 도구가 거부하며 기본 `test:e2e`는 이 통합 테스트를 실행하지 않는다.

### V2 근거 저장·확정 요청

`POST /groups/{group_id}/rationale`의 SAVE 예시:

```json
{"action":"SAVE","expected_revision":3,"request_id":"<new UUID>","reason":"변경된 구성의 차이/예외를 보완","reviewed":true,"content":{"reason":"같은 설정 누락을 함께 검토하며 다른 오류와 구분합니다. 동일 원인은 미확인입니다.","evidence_ids":["original-record-id"],"reference_groups":[]}}
```

`ACCEPT`는 content/reviewed 없이 action·기대 개정·UUID·검토 사유를 보낸다. 현재 그룹 소속과 사용 가능한 과거 사례를 다시 검증한다.
`POST /clustering`의 CONFIRM은 **명시적인 현재 전체 개정의 일괄 채택 + 전체 분류 확정**이다. 유효한 DRAFT를 CONFIRMED로 채택하며 MISSING/STALE은 409로 차단한다.
전역 reason은 생략 가능하다. `SAVE(reviewed=true)`는 그 그룹의 근거만 확인하며 전체 분류를 확정하지 않는다.
소속이나 이름 변경 및 근거만 정정한 뒤에는 전체 분류를 다시 확정해야 새 그룹 판정을 저장할 수 있다.
V1 archive는 기존 workspace에서 검증할 수 있다. 근거 없는 옛 확정을 새 workspace에 재생하는 작업은 `LEGACY_ARCHIVE_READ_ONLY`로 거부한다. 새 실험/가져오기는 V2 archive(모델 3회, 분류 event 6개)를 사용한다.

### 대용량 조회의 메모리 경계

독립 workspace API는 프로세스 내 짧은 DB 처리 구간을 한 번에 하나씩 실행한다. 여러 페이지가 동일한 10만 건 JSONB 원본을 동시에 디코딩해 메모리를 중복 점유하지 않도록 한다. 실제 모델 추론은 이 구간과 DB 트랜잭션 밖에서 실행되므로 분류 상태 조회가 모델 호출 종료까지 잠기지는 않는다. 참조 사례의 바이트 검사는 유지하며 오래된/자기 의존 사례는 원본 records를 읽기 전에 제외한다. 여러 API 프로세스를 운영할 때는 프로세스 수에 맞는 별도 메모리 예산이 필요하다.
