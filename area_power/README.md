# Design Atlas · 프로젝트 비교

프로젝트 A가 지정한 기준 프로젝트 B와 Block·hierarchy instance별 Area/Power를 비교하는 수치 엔진이다. 주 화면은 **프로젝트 비교** 하나이며 업로드 이력과 경로 대응도 같은 화면에서 다룬다. 로그 분류·PASS/FAIL과 수치 엔진은 분리하되 공통 프로젝트/블록/버전·인증·탐색은 하나다.

## 코드와 데이터 경계

| 위치 | 역할 |
| --- | --- |
| `area_power/vwp_server/` | 중앙 VWP 서버: CSV 접수·수치 비교·통합 AI 처리 |
| `area_power/oa/` | 공통 OA 수치 API·권한 적용·메일 작업자 |
| `area_power/common/` | 공유 설정·정밀 JSON/모델·DB·작업 조정 |
| `oa/dashboard/src/areaPower/` | 같은 Dashboard 안의 Area/Power 화면 |
| `db/area_power/` | 신청한 두 테이블의 컬럼·후속 제약·인덱스 SQL |
| `docs/area_power/` | 원본 CSV 컬럼·수치·매핑·DB 계약 |

OA ↔ 중앙 VWP의 업무 전달은 DB 행으로만 수행한다. 브라우저가 VWP·AI에 직접 접속하거나 OA가 VWP API를 중계하지 않는다. 사용자 PC → VWP CSV 업로드는 별도 입력 경로다.

Area 테이블은 **`di_area_power_csv`, `di_area_power_tracking_record` 두 개**다. Sanity의 기존 네 테이블을 변경하거나 여기에 CSV를 끼워 넣지 않는다. 두 테이블 신청은 완료됐지만 실제 사내 생성·제약·권한 적용을 가정하지 않는다. 실제 schema·DSN은 사내에서만 설정한다. SQL의 `<schema>`를 사내에서 일괄 치환하고, 앱은 DDL을 실행하지 않는다.

2026-11-12 실제 이름 전환은 [DB backend 전환 안내](../docs/area_power/database.md#실제-테이블-이름으로의-backend-전환--2026-11-12)를 따른다. 기존 사용자 DB·서비스를 자동 rename/migrate/재시작하지 않는다. OA·중앙 VWP는 승인된 두 테이블/권한/readiness 확인 후 함께 전환하고, 동등한 기존 제약/인덱스 이름은 유지한다. PC config/API·UI는 그대로이므로 재-export·재-upload·의존성 변경·frontend build가 필요하지 않다.

## 인증·프로젝트 연결

- `/`의 공통 홈에서 프로젝트·블록·버전을 선택한다. 수치 화면은 `/?page=area-power`, 엔진 API는 `/api/area-power/*`다. 별도 Area 로그인·브라우저용 `OA_API_TOKEN`·신뢰하는 `X-Actor`는 없다.
- 로그인한 활성 SSO 사용자는 프로젝트 배정 없이 모든 등록 프로젝트의 수치 자료를 조회한다. 관리자 기능·프로젝트별 수정 권한·mutation 보호와 회사 SSO 미설정·`AUTHORIZATION_NOT_IMPLEMENTED` 차단은 유지한다.
- 공통 등록·선언·준비는 `/api/atlas/*`만 사용한다. 기존 Area 전용 등록 API는 제거했다. 수치 범위 준비가 네이티브 프로젝트·블록·버전을 실제 내부 UUID에 정확히 연결하므로 이중 등록하지 않는다.
- 기존 프로젝트 공통 Area 버전 UUID는 그대로 보존하며 여러 네이티브 블록 버전에 연결할 수 있다. 버전별 binding·선언·수치 계보는 기존 `WS_CONFIG`의 전역 namespace에 저장한다. 별도 테이블·scope FK·자동 DDL을 만들지 않는다.
- `/api/area-power/access`는 전체 등록 프로젝트·block을 제공한다. 목록·상세·다운로드·현재/기준 비교·고정 입력·총계는 프로젝트 배정 없이 조회한다. 새 분석 요청과 기준·매핑·사람 상태 변경은 기존 프로젝트 수정 권한을 검사한다.
- 기존 데이터의 binding을 임의 추정하지 않는다. 미연결 데이터는 운영 SSO에서 차단한다.
- 호스트가 **명시적으로 `local_token`**을 선택한 개발 환경만 미연결 로컬 catalog를 허용한다. `access.local_mode=true`로 표시하며 회사 SSO 실패 시 이 모드로 전환하지 않는다.

## 설치·설정

Area 모듈은 Python 3.11 이상을 사용한다. 기존 Sanity 실행 구조는 [배포 안내](../DEPLOYMENT.md)를 따른다. 같은 저장소 루트에서 역할별 requirements를 설치한다. 기존 venv를 덮어쓰지 않아야 할 때는 별도 venv를 사용한다.

```bash
python3 -m venv .venv-integrated
.venv-integrated/bin/python -m pip install -r requirements.txt
npm --prefix oa/dashboard ci
npm --prefix oa/dashboard run build
```

[환경변수 예제](.env.example)의 필요한 Area 키를 해당 서버 실행 환경에 export한다. `AREA_POWER_*`는 Area 기능·DB·메일·사용자 PC 업로드 설정이며 별도 AI endpoint, model, token 설정은 없다. 로그 AI와 동일하게 OA 인증 actor의 `CLUSTER` 설명 추출·`REVIEW` 판정 task selection/default를 사용한다. 사용할 managed connection이 없고 해당 task default도 설정되지 않은 경우에만 기존 no-connection Log AI 기본 설정을 사용한다. 선택된 managed connection/default 또는 credential이 유효하지 않으면 그 오류를 유지하고 legacy 설정으로 전환하지 않는다. 사용자별 인증 방식이면 managed connection 또는 기존 Log AI의 사용자 토큰 저장소를 사용하고, 기존 server 인증 방식이면 Log AI 서버 설정을 사용한다. 개인 token은 Area DB에 저장하지 않는다. 기존 VWP 로그 작업자가 사용하는 DB 설정을 그대로 쓰며 VWP 관리 연결을 선택할 때만 [VWP 프로필](../vwp_bridge/AI_WORKER.md)이 VWP에 있어야 한다. 프로필·endpoint·비밀을 OA/Area 설정으로 복사하지 않는다. OA↔VWP 작업 전달은 기존 DB 경계를 사용하며 새 HTTP 중계나 비밀 전달 경로를 추가하지 않는다. 연결·legacy AI 설정은 [OA 로그 AI 실행 안내](../oa/AI_RUNTIME.md)를 따른다. PC에는 DB·AI 계정을 주지 않고 `VITE_*`에 비밀을 넣지 않는다.

| 실행 위치 | 주요 설정 |
| --- | --- |
| OA 호스트 | 기존 `SANITY_AUTH_MODE`/SSO 설정, `AREA_POWER_ENABLED=true`, 공유 DB 설정 |
| 중앙 VWP + OA | `AREA_POWER_DB_DSN`, `AREA_POWER_DB_SCHEMA` — 각 서버에서 같은 DB/schema를 가리키도록 설정 (기존 역할별 계정 사용 가능) |
| 중앙 VWP | Area AI용 별도 endpoint/model/token 키 없음. OA 로그 AI와 같은 `CLUSTER`/`REVIEW` 선택·task-default 및 no-connection legacy 기본 경로를 사용하며, 선택된 경로의 기존 model/auth/limits를 그대로 적용 |
| OA 메일 | `AREA_POWER_MAIL_RECIPIENTS`, 명시적으로 주입한 사내 `MailSender` 어댑터 |
| 사용자 PC | `AREA_POWER_SERVER_URL` 또는 `--url` — DB·AI 계정과 업로드 신원 설정 불필요 |

선택 기능을 끄면 Area API는 인증 후 `503 AREA_POWER_UNAVAILABLE`를 반환한다. 필수 설정이 없거나 DB 준비가 안 됐다고 demo·다른 DB·가짜 AI·가짜 발송으로 대체하지 않는다.

## 서버 실행

회사 OA는 기존 SSO composition root를 그대로 사용한다. 아래 명령은 **각자의 서버**에서 실행하며 회사 SDK·TLS·네트워크·권한을 연결해야 한다.

```bash
# OA: Sanity 화면과 Area API를 한 앱으로 제공
.venv-integrated/bin/python -m oa.sso_app

# 중앙 VWP: 업로드·수치·AI를 한 프로세스에서 처리한다. 로컬 OA가 8000을 쓰면 8010을 지정한다.
.venv-integrated/bin/python -m area_power.vwp_server --port 8010

# OA: 회사 메일 어댑터 연결 후 별도 프로세스로 요청 소비
.venv-integrated/bin/python -m oa.mail_app
```

중앙 VWP 서버 프로세스가 업로드와 수치/AI 작업을 함께 처리하며 별도 Area Power worker 명령은 없다. 업로드는 `/api/area-power/uploads`, 상태는 `/api/area-power/uploads/{id}`다. 두 경로에는 추가 인증을 두지 않고 사내 도달성을 신뢰한다. `uploaded_by`와 업로드가 만든 snapshot 작성자는 기술적 수신 서비스 `vwp_server`이며 인증된 사용자를 뜻하지 않는다. 요청의 임의 신원 헤더는 신뢰하지 않는다. **OA 조회·다운로드는 활성 로그인과 유효한 scope/binding을 검사하며, 쓰기는 프로젝트 배정을 요구한다.** 작업 완료 여부는 서버 프로세스 상태가 아니라 DB에 저장된 상태로 판단한다. 배포 전에 기존 별도 VWP API/worker unit을 중지하고, 새 server와 standalone worker를 함께 실행하지 않는다.

VWP 서버는 schema와 첫 작업 조회/claim이 성공하면 ready가 된다(대기 작업이 없어도 준비 완료이며 AI 응답을 기다리지 않는다). 일반 AI 작업 실패는 해당 job을 `failed`로 저장하고 consumer는 계속 실행한다. 치명적인 claim/finish 실패만 consumer를 비정상으로 만들어 `/health`와 업로드가 503을 반환한다. 정상 SIGTERM은 claim한 작업을 마친 뒤 새 작업 없이 종료한다. 강제 종료나 fatal finish 실패는 불확실한 `running` 작업을 남길 수 있으며 자동 reclaim은 없다.

## CSV 업로드

먼저 공통 홈에서 프로젝트·블록·네이티브 버전을 등록/선택하고 **수치 범위 준비**를 실행한다. 업로드 화면의 metadata JSON은 선택한 공통 범위의 실제 Area UUID를 사용한다. 그 UUID를 CLI에 전달하며 공통 문자열 ID로 대체하지 않는다. Corner는 CSV에서 읽고 중복 입력하지 않는다. 네이티브 수집의 `source_pre_id`와 수치 비교의 `previous_version_id`는 별개다.

```bash
.venv-integrated/bin/python -m area_power.vwp ./block.csv.gz --metadata ./metadata.json
# 또는 설치한 사용자 PC 의존성·Python 환경으로 bin/area-power 실행
```

PC마다 비공개 JSON 하나를 준비하면 같은 실행기를 설정 파일로 실행할 수 있다. Python 3.11+ 환경에 `vwp/requirements.txt`의 PC 의존성만 설치한다(DB·모델·pydantic 불필요).

```bash
python -m pip install -r vwp/requirements.txt
python -m area_power.vwp --config /approved/pc/upload.json
# 또는 해당 Python 환경으로 /release/bin/area-power --config /approved/pc/upload.json
```

제품 설정 예제는 [`vwp/upload.example.json`](vwp/upload.example.json)이다:

```json
{
  "csv": "reports/block.csv.gz",
  "metadata": "metadata.json"
}
```

필수 키는 `csv`, `metadata` 경로, 선택 키는 `report_export`의 **완전한 실제 argv 문자열 배열**뿐이다. 알 수 없는 키·빈 경로·잘못된 argv는 실행 전에 거부한다. 모든 상대 입력 경로와 exporter의 작업 디렉터리는 **설정 파일 디렉터리** 기준이다. 상대 실행 파일은 `./...`로 명시하고, argv의 상대 경로도 그 디렉터리에서 실행된다. Exporter 없는 설정은 기존 CSV/gzip을 그대로 전송한다. `--config`는 위치 인자 CSV·`--metadata`·`--report-export`와 함께 쓸 수 없으며 `--url`·`AREA_POWER_SERVER_URL`과 `--timeout`을 사용할 수 있다. 기존 명시적 CLI 모드의 경로·exporter cwd는 호출 디렉터리 기준을 유지한다.

metadata에는 준비된 실제 Area `project_id`, `version_id`, `block_id` UUID와 `execution_name`, `design_stage`, `hierarchy_separator`가 필요하다. 승인된 공통 범위를 준비한 뒤 실제 dashboard에서 내보낸 JSON을 사용한다. 데모 UUID·이름으로 ID 추정·별도 최상위 NET_VER/REVISION/layout/ECO/corner 필드를 만들지 않는다. CSV는 81컬럼·필수 power 20필드·한 파일 한 corner다.

`AREA_POWER_SERVER_URL`은 중앙 VWP의 기본 URL이다. CLI는 `/api/area-power/uploads`를 붙인다. 사내 report_export가 필요하면 마지막 옵션에 실제 executable·argv를 전달한다.

```bash
.venv-integrated/bin/python -m area_power.vwp ./block.csv --metadata ./metadata.json --report-export report_export <사내에서 확인한 실제 인자>
```

실제 바이너리·라이선스·출력 옵션은 제공받지 않았으므로 임의 인자를 만들지 않는다. Exporter는 shell 없이 실행하고 출력 파일이 있을 때만 업로드한다. 사용자가 이미 만든 CSV는 exporter 없이 전달할 수 있다.

### 사내 AI가 마무리할 범위

공통 전송은 기존 CLI와 실제 업로드 API가 담당한다. 사내 AI의 Area 작업은 **비공개 데이터/설정 준비**다: PC별 경로·실제 metadata 실행 문맥, 필요한 경우 라이선스가 있는 `report_export`의 확인된 전체 argv(또는 승인된 기존 회사 wrapper), PC에서 도달 가능한 VWP upload endpoint를 준비한다. AI 연결 endpoint/model/token은 기존 로그 AI 연결을 사용하며 별도 Area 설정을 추가하지 않는다. 업로드용 인증 SDK·토큰·수동 사용자명은 추가하지 않는다. SDK/인자·라이선스·실제 보고서는 여기서 확인하지 못했으며 구현 완료로 표시하지 않는다. 이 안내가 회사 AI에 공통 CLI/API/DB/SSO나 새 회사 코드의 수정 권한을 추가하지 않는다.

현재 로컬 서버 `127.0.0.1:8010`은 다른 PC의 endpoint가 아니다. 회사 운영 담당자가 실제 사내 도달 경로를 제공해야 하며 기존 서비스의 bind·네트워크·TLS 설정을 이 코드 변경으로 자동 수정하지 않는다. PC에는 DB/AI 계정을 주지 않는다. 2026-11-13 사용자 결정으로 업로드의 추가 인증·수동 actor를 제거했다. 기존 launcher의 `--token`, `--actor`, `AREA_POWER_UPLOAD_TOKEN`, `AREA_POWER_ACTOR`를 제거한다. 인증된 업로드 사용자 추적은 제공하지 않으며 대신 기술적 수신 서비스 출처만 남긴다.

각 POST는 새 upload 행이다. gzip append·기존 행 overwrite·자동 선택·중복 제거·자동 재시도는 없다. 네트워크 오류로 결과를 못 받았어도 서버에는 commit됐을 수 있으므로 무작정 재전송하지 말고 운영 담당자와 접수 여부를 확인한다.

## 조회·비교·판정

- 프로젝트는 한 번 선택하고 그 프로젝트에 저장한 기준을 사용한다. A→B는 단방향이며 B의 기준이나 다른 프로젝트의 선택을 바꾸지 않는다. 한 프로젝트에 기준은 하나이고 자기 자신은 기준으로 지정할 수 없다.
- 기준은 기존 project 레코드의 `payload.baseline_project_id`에 저장한다. 새 테이블·DDL·migration은 없다. 프로젝트만 준비해도 설정할 수 있으며 CSV·블록·버전 준비를 선행 조건으로 요구하지 않는다.
- 양쪽 Block·NET_VER·REVISION·corner와 정확한 upload 실행을 명시적으로 선택한다. 현재 Block은 상단 공통 범위에서 선택하고 비교 화면에서는 읽기 전용이며, 현재 NET_VER·REVISION은 비교 화면과 상단 공통 범위에서 양방향으로 동기화된다. 현재 NET_VER 후보는 해당 Block에 한정되고, NET_VER 변경은 REVISION을 비우며 해당 NET_VER의 유효한 REVISION만 선택지로 제공한다. 현재 쪽 NET_VER/REVISION 변경 시 현재 corner·upload·mapping 선택은 초기화하되 기준 프로젝트와 기준 쪽 선택은 유지한다. 기준 쪽 선택은 현재 쪽과 독립적이다. Corner 하나가 CSV 한 파일이다. 이름·최신 시각·동일 경로로 기준 실행이나 프로젝트 간 대응을 자동 선택하지 않는다.
- 서로 다른 Block·경로는 같은 화면의 명시적 대응으로 연결한다. 매핑 없는 행은 한쪽의 관측값만 보여 주며 상대 값·Δ·%는 unavailable이다. 부모 수치와 자식을 다시 합산하지 않는다.
- AI·작업·알림·target·변경 설명의 내부 엔진은 보존하지만 독립 주 탐색이나 비교의 선행 입력이 아니다.

### 프로젝트 기준 API

| 요청 | 계약 |
| --- | --- |
| `GET /api/area-power/projects/{Area UUID}/baseline` | `{project_id,row_version,baseline_project_id,baseline_project,status}`. status는 `unset`, `available`, `unavailable` |
| `POST` 같은 경로 | `{baseline_project_id: UUID 또는 null, expected_row_version: 양의 정수}`. null은 해제, stale은 `409 CONFLICT`, 자기 지정은 `422 INVALID_BASELINE` |
| `POST /api/area-power/comparisons` | 기존 명시적 upload·mapping 계약. `comparison_kind=previous_project`의 새 요청은 저장된 기준을 검사하며 다른 프로젝트를 임의로 넘길 수 없음 |
| `GET /api/area-power/uploads/{id}/hierarchy` | 검증된 정확한 upload의 `{upload_id,snapshot_id,hierarchy_separator,items:[{path,parent_path,depth,area,power_mw}]}`. 경로 선택·트리용이며 새 저장 구조 없음 |

기준 선택·해제·교체에는 현재 프로젝트 수정 권한이 필요하며 대상은 유효한 공통 프로젝트 binding을 가져야 한다. 로그인한 활성 사용자는 양쪽 기준·원본·고정 결과를 배정 없이 조회한다. 배정 회수는 현재 프로젝트 수정 권한만 해제하며 등록된 기준의 조회를 숨기지 않는다. 미등록·미연결 입력은 계속 차단한다. 프로젝트의 기준을 연쇄 탐색하거나 순환 관계를 해석하지 않는다. 공통 `/api/atlas/area-scope`는 `{project_id: native ID}`만으로 정확한 Area mirror를 준비하며 별도 project를 만들거나 native ID를 UUID로 재해석하지 않는다.

새 교차 프로젝트 매핑도 저장된 기준을 검사한다. 비교는 선택한 upload·snapshot·mapping revision과 당시 기준의 프로젝트 ID/row_version을 고정한다. 기준이나 매핑의 후속 변경은 완료 결과를 재해석/수정하지 않는다. 기존 이전 버전·AI 분석 계약은 내부 소비자를 위해 유지한다.

- [81컬럼 통합 CSV](../docs/area_power/csv_structure), 한 파일 한 corner, 필수 20개 power 필드를 사용한다. Power 없는 cell usage 단독 업로드는 제외한다.
- 수정본·재실행·추가 corner는 각각 새 upload 행이다. 기존 gzip에 append하거나 과거 원본을 덮어쓰지 않는다. Snapshot 재사용은 명시적으로 선택하고 전체 구조·산출 조건을 검증한다.
- 현재 파일은 명시적인 selection이며 업로드 시각으로 자동 교체하지 않는다. 과거 비교는 당시 upload·mapping·설명 revision을 고정한다.
- 부모 수치에는 자식이 포함된다. 루트와 하위를 중복 합산하지 않으며 split/merge는 겹치지 않는 그룹만 합친다. 비율은 합산 분자·분모에서 재계산하고 분할 자식의 과거 값은 배분하지 않는다.
- 일반 Vth·LLP·drive 분류를 구분하고 분류 누락을 표시한다. 비율 변화는 %p, 기준 0의 상대 변화율과 0분모 비율은 계산 불가로 둔다. 결측을 0으로 바꾸지 않는다.
- Power 단위는 mW이며 logic에 CTS가 포함된다. 실제 PVT·mode·activity·도구 등 조건이 같아야 comparable이다. 미확인·다른 조건은 contextual, 기준 누락은 unavailable이다.
- 임계값을 임의로 정하지 않는다. 요청 정책이 없으면 `policy_unconfigured`를 표시한다. 산출 문맥이 미확인인 지표도 정상으로 보증하지 않는다.
- 수치 계산은 코드가 담당한다. Area AI는 OA 인증 actor의 기존 Log AI `CLUSTER`/`REVIEW` 선택과 기본값을 그대로 사용한다. 유효한 managed 연결이 없고 작업별 기본값도 설정되지 않은 경우에만 기존 no-connection Log AI 설정(`SANITY_OA_AI_*`)을 사용한다. 선택된 managed 연결/default가 유효하지 않거나 인증에 실패하면 별도 AI 경로로 전환하지 않고 숫자 결과와 비교 상태는 보존한다. 기존 provider가 요구하는 actor 개인 token 또는 기존 server credential을 사용하며, 수치·매핑·비교 상태는 바꾸지 못한다.
- 익명 VWP 업로드는 AI 사용자의 신원이 아니며 AI 인증으로 승격되지 않는다. OA 비교 actor가 없으면 AI 작업은 인증 오류로 남고 숫자 비교는 계속 처리한다.
- 같은 숫자 비교 요청을 다시 제출하면 수치 비교와 고정 입력은 재사용한다. 요청 actor, `CLUSTER`/`REVIEW` 기본 연결, 연결 설정 또는 credential revision/scope가 달라졌으면 별도 AI 실행 문맥으로 새 run을 만들며 기존 수치·고정 문맥·이력은 바꾸지 않는다. 실패한 AI job의 명시적 재실행은 원래 actor와 저장된 pins를 사용한다. credential 변경 후에는 새 비교 AI run 또는 rejudge를 사용하고 재실행 중 다른 사용자나 credential로 바꾸지 않는다.
- 알림 event마다 mail job을 DB에 남긴다. OA는 [사내 메일 Protocol](../oa/MAIL_INTEGRATION.md)의 `MailSender`만 호출하며 회사 API/SDK는 사내 AI가 허용된 어댑터 지점에서 구현한다. 미연결은 DB 접속·job 점유 전에 실패한다. 요청 수신자가 없으면 `AREA_POWER_MAIL_RECIPIENTS`를 사용하며 수신자도 없으면 실패한다. 서비스 접수는 전달·열람 보장이 아니며 일부 거부·결과 불명·접수 후 DB 기록 실패를 보존한다. 자동 재발송·정확히 한 번 전달은 보장하지 않는다. SMTP 구현·`AREA_POWER_SMTP_*`는 제거했다.

상세 계약: [제품 설계](../docs/area_power/design.md) · [DB와 적용 순서](../docs/area_power/database.md).

## code-server 접속

현재 사용하는 code-server의 scheme·호스트·SSH 로컬 포트를 유지한다. 로그인 복귀 경로를 지정하려면 `/login?to=` 뒤에 Dashboard 경로를 URL 인코딩해서 넣는다. 호스트 OA 기본 포트가 8000일 때:

```text
http://127.0.0.1:8080/login?to=%2Fproxy%2F8000%2F
```

인증되어 있으면 `/proxy/8000/?page=area-power`로 직접 열 수 있다. `localhost`와 `127.0.0.1`을 바꾸면 code-server 인증 cookie가 공유되지 않는다. code-server 비밀번호와 업무 앱의 인증은 별개다. 새 터널·인증 해제는 필요 없다.

## 현재 로컬 실행

이 작업 호스트의 `.env.integrated.local`은 Git 제외·0600인 로컬 실행 설정이다. 기존 Area DB `iat_dashboard_local`을 보존하고 별도의 실제 Sanity DB를 사용한다. 사내 DB·SSO·실데이터가 아니며 demo 데이터를 운영 응답으로 대체하지 않는다. 재실행 시 저장소 루트에서 환경을 export한 뒤 OA를 실행한다.

```bash
set -a && source .env.integrated.local && set +a
.venv-integrated/bin/python -m uvicorn oa.dashboard_api:app --host 127.0.0.1 --port 8000 --no-proxy-headers --no-access-log
```

호스트 재부팅 자동 시작은 별도 운영 설정이다. 원래 `.venv`는 이번 작업에서 실행 불가능한 링크 상태였으므로 수정하지 않고 `.venv-integrated`를 만들었다. 기존 사용자 데이터·설정·SSO 보호는 삭제하거나 초기화하지 않는다.

## 로컬 실행 검증

이전 독립 구현에서는 전체 hierarchy 237개 지표, target 79개, 루트 이름 변경·split·삭제 395개 지표의 수치·coverage·재판정 상태 보존과 gzip 왕복·SMTP 수락을 확인했다. 당시 SMTP 구현은 현재 제거됐으며 이 기록은 사내 메일 어댑터의 검증이 아니다. 통합 후 확인한 범위는 작업 인계와 최종 실행 결과에 별도로 기록한다.

운영 전에는 실제 CSV의 인코딩·정밀도·area 포함 관계, 신청한 두 테이블의 설치·권한, SSO 프로젝트 binding, 사내 AI 프로토콜·허용 데이터, 사내 메일 어댑터와 대규모 조회 비용을 확인해야 한다. Sanity의 기존 PostgreSQL 18 포털 catalog 이슈를 이 모듈 통합으로 해결했다고 표시하지 않는다.

### 이전 엔진 통합 시점의 확인 기록

2026-11-10, 기존 사용자 Area DB와 분리한 폐기용 DB에서 실제 호스트 OA·VWP 업로드 API·수치 worker를 실행했다. 새 namespace로 CSV를 업로드하고 이전 프로젝트의 명시적 매핑 비교에서 79개 지표 전체 처리와 logic area 1 → 2, Δ 1, +100%를 확인했다. AI 미설정은 needs_review이며 숫자 결과는 completed로 남았다.

기존 SSO 회귀 1개가 통과했다. 기존 합성 SSO 어댑터와 실제 DB의 권한 저장소로 현재·기준 프로젝트를 모두 허용한 비교가 200임을 확인했고, 기준 프로젝트 권한을 회수하면 비교·원본 다운로드는 404, 지표 목록·비교/알림/job 총계는 권한 범위에서 제외됐다. Mutation intent 없는 SSO 쓰기는 403이었다. Tool의 `version_id`·constraint의 `project_id` 같은 불투명 관측값은 업무 참조로 오해하지 않고, 실제 기준 프로젝트 참조는 계속 검사했다. 회사 SSO 구현·연결을 검증한 것은 아니다.

Chromium에서 실제 code-server proxy 경유 공통 로그인, 재로그인 없는 Sanity↔Area 전환, 파란색 light/dark 화면, 390px drawer·수평 넘침 없음, 비교 수치·coverage·검색·needs_review 표시와 UI 원본 gzip 다운로드를 확인했다. 기존 명시적 Sanity 합성 demo는 유지됐고 자원 경로가 proxy 아래에서 200이었다. Frontend build와 canonical CLI 두 진입점의 help 실행이 성공했다.

위 내용은 이전 엔진 통합 시점의 검증 기록이다. 현재 Design Atlas 공통 카탈로그·화면·실행 서비스·검증 상태는 [제품 인계](../HANDOFF.md)를 따른다. 사용자 DB·기존 수정 파일은 보존하며 새 테스트·fixture·mock·검증 스크립트 파일은 추가하지 않는다. 회사 DB·실데이터·AI 품질·사내 메일 서비스·Docker 이미지·대규모 성능은 미검증이다.
