# 서버별 소스 구조와 실행

각 서버는 이 Git 저장소를 사용한다. 소스는 서버별 디렉터리로 나누고, 공통 계약은 `common/` 한 곳에서 관리한다. 모든 Python 명령은 **저장소 루트**에서 실행한다. 디렉터리를 다른 서버 폴더 안에 복사하거나 공통 파일을 중복 관리하지 않는다.

## 1. 서버와 코드 위치

| 서버 / 개발 AI | 실행 코드 | 사내 구현 위치 | DB / 설정 예제 |
|---|---|---|---|
| VWP 원천 수집 | `vwp/` | `vwp/integration/` — plan·원천 SDK | 앱 DB·모델 계정 없음 |
| VWP-bridge | `vwp_bridge/` | `vwp_bridge/integration/` — item parser·분류/판정 SDK | VWP 제한 로그인 / [예제](vwp_bridge/.env.example) |
| OA | `oa/`, `oa/dashboard/` | `oa/integration/` — SSO·사용자 조회 SDK | OA 제한 로그인 / [예제](oa/.env.example) |
| 공통 | `common/` | 사내 AI는 읽기 전용 | 설정·DB·전송 계약·추상 타입·순수 검증 |
| 개발 시험 | `devtools/`, `tools/`, `tests/` | 설계 담당 Codex 관리 | 합성 report·seed·외부 모델 mock·QA |
| DB 관리 | `db/` | 사용자/DBA 관리 | 기존 SQL·form·권한·접속 인계 |

`vwp_bridge`는 Python 모듈로 실행할 수 있도록 밑줄을 사용한다. 이전 `vwp-dev/`, `vwp-bridge/`, `oa-bridge/`, 루트 `sanity_*.py`, `site_integration/`의 소스는 위 위치로 이동했다. import도 `common.*`, `vwp.*`, `vwp_bridge.*`, `oa.*`, `devtools.*`로 변경했다.

각 서버에 저장소 전체가 있어도 **자기 진입점만 실행**한다. VWP-bridge는 OA 앱을 import하지 않고, OA의 일반 실행은 VWP 모델·외부 mock을 import하지 않는다. 두 서버 간 통신은 기존 PostgreSQL 네 테이블만 사용한다. 수집 snapshot은 VWP 영역 안에서 원천 수집→VWP-bridge로 전달하는 입력이다.

## 2. 의존성과 비공개 설정

각 서버의 저장소 루트에서 자기 역할의 의존성만 설치한다. 전체 개발·회귀 테스트에는 루트 `requirements.txt`를 사용한다.

```bash
python3 -m venv .venv
# VWP 원천 수집: 표준 라이브러리만 사용
.venv/bin/python -m pip install -r vwp/requirements.txt
# VWP-bridge 서버
.venv/bin/python -m pip install -r vwp_bridge/requirements.txt
# OA 서버
.venv/bin/python -m pip install -r oa/requirements.txt
```

위 세 설치 명령 중 **자기 서버 명령만** 사용한다. 회사 SDK 추가는 기존 가이드에 따라 별도 검토한다.

VWP-bridge는 `vwp_bridge/.env.example`, OA는 `oa/.env.example`의 키를 자기 서버의 **저장소 루트 `.env`**에 설정한다. 예제 파일의 위치와 읽는 위치를 구분한다. `common/sanity_config.py`는 저장소 루트 `.env`와 프로세스 환경을 읽으며 `common/.env`, `vwp_bridge/.env`, `oa/.env`는 읽지 않는다. 기존 `.env`를 덮어쓰지 않고 필요한 키만 현장에서 확인한다. 실제 값·인증서·사내 report는 Git에 넣지 않는다.

VWP-bridge/OA는 같은 논리 DB·스키마를 서로 다른 제한 로그인으로 사용한다. 상세 키·TLS·DSN 우선순위·권한·비공개 인계 양식은 [DB 연결 인계](db/CONNECTION_HANDOFF.md)를 따른다.

```bash
# VWP-bridge 서버: 읽기 전용 점검
.venv/bin/python -m common.sanity_db check --side vwp
# OA 서버: 읽기 전용 점검
.venv/bin/python -m common.sanity_db check --side oa
```

## 3. 서버별 실행 진입점

### VWP 원천 수집

[담당 AI 지시문](vwp/INTERNAL_AI_TASK.md)과 [plan 계약](vwp/PLAN_INTEGRATION.md)을 읽는다. 회사 plan provider는 `vwp/integration/corporate_plan.py`에 구현하고, 필요한 wrapper·전용 테스트는 `vwp/integration/company_plan/`에 둔다. 자동 plugin 탐색은 없다.

```bash
.venv/bin/python -m vwp.main collect --plan PLAN_JSON --reports REPORT_ROOT --out SNAPSHOT_DIR
```

이미 완료된 report를 수집한다. sanity 실행·report 수정·DB 연결은 이 단계의 역할이 아니다.

사용자별 REVISION 제출에 PRE/POST의 여러 물리 루트가 포함될 때는 [분산 수집 설계](vwp/SOURCE_COLLECTION_DESIGN.md)를 따른다. 회사 launcher의 원본 불변 export는 VWP 내부 입력 준비이며, OA로 파일을 전달하지 않는다. 예상 범위 선언·원천 버전/실행의 V2 운영 연결은 현재 schema 1과 구분한다.

### VWP-bridge

[담당 AI 지시문](vwp_bridge/INTERNAL_AI_TASK.md)을 읽는다. 회사 parser 등록은 `vwp_bridge/integration/corporate_parsers.py`, 분류/판정 모델은 같은 폴더의 `corporate_clustering.py`, `corporate_review.py`에 구현한다.

```bash
.venv/bin/python -m vwp_bridge.corporate_analyze analyze --input SNAPSHOT_DIR/run.json
.venv/bin/python -m vwp_bridge.main work --interval 2
```

첫 명령은 회사 registry를 명시적으로 주입한다. 빈 registry와 미구현 회사 SDK를 실제 지원으로 취급하지 않는다. 현재 worker는 기존 legacy 분류/추가 근거 계약용이며, 새 workspace `CORPORATE` 운영 job 연결은 COMMON 후속 작업이다.

### OA

사내 Nginx 뒤에서 실행하는 간단한 CI/CD 구성은 [루트 Docker 안내](docker/README.md)를 따른다. 사내 OA Git에는 `docker/`, `oa/`, `common/`을 같은 루트에 두고 **`docker/Dockerfile-prod`**를 빌드한다. 컨테이너는 8080의 OA 앱 한 개만 실행한다. 아래 loopback 명령은 컨테이너 밖의 직접 실행용이다.

[담당 AI 지시문](oa/INTERNAL_AI_TASK.md)을 읽는다. 회사 인증/사용자 조회는 `oa/integration/corporate_sso.py`와 `company_sso/`에 구현하고, 기존 `oa/sso_app.py` 주입 지점을 사용한다.

```bash
# OA 프런트엔드 빌드: 승인된 Node 환경에서 수행
npm --prefix oa/dashboard ci
npm --prefix oa/dashboard run build
# 회사 SSO 진입점, loopback 8000
.venv/bin/python -m oa.sso_app
# 기존 분류 결과 수신 worker, 별도 프로세스
.venv/bin/python -m oa.main work --interval 2
```

UI 소스와 빌드 출력은 `oa/dashboard/`, `oa/dashboard/dist/`다. 빌드 출력은 Git 제외 대상이므로 Git 소스 업데이트 후 승인된 빌드 환경에서 생성해야 한다. 회사 SDK가 미구현이면 인증이 실패하고, SSO 인증이 되어도 업무 권한이 연결되기 전에는 `AUTHORIZATION_NOT_IMPLEMENTED`가 유지된다. `/workspace` 합성 시험은 [별도 안내](oa/WORKSPACE.md)를 따른다.

## 4. 세 사내 AI에게 전달할 문서

각 AI에게 루트 [AGENTS.md](AGENTS.md), [작업 가이드](INTERNAL_AI_GUIDE.md), [통합 인계](INTERNAL_AI_HANDOFF.md)와 **자기 역할의 `AGENTS.md`·`INTERNAL_AI_TASK.md`**를 전달한다. 세 역할의 회사 어댑터가 물리적으로 분리되어도 수정 허용 범위는 기존 가이드 그대로다. 공통 코드·SQL·UI·기존 테스트 변경과 디버깅은 설계 담당 Codex가 맡는다.

소스 정리는 회사 SDK·운영 DB·SSO/RBAC·신규 workspace job의 연동 완료를 뜻하지 않는다. 실제 원문과 접속 사실은 사내에서 검증하며, 예상 밖 오류는 비식별 문의와 `WAITING_FOR_CODEX` 상태로 인계한다.

## 5. 사용자 제출 명령과 데이터 배치

공통 `bin/sanity`·제출 작업자·V2 DB 수신을 제공했다. **실제 회사 SDK/신원·프로필·제출 마운트·운영 DB 적용은 사내 준비가 필요**하다. 3절의 legacy collector/analyze/worker가 새 흐름을 대신하지 않는다. 설정 예제·UID/ACL/영속 volume·실행은 [사용자 제출 안내](vwp/SUBMISSION.md)를 따른다.

| 배포 환경 | 설치/데이터 위치 | 필요한 준비 |
|---|---|---|
| 사용자 VWP 작업 환경 | `/opt/sanity/releases/<release-id>/`, `/opt/sanity/current/bin/sanity` | 검토된 release·회사 입력 어댑터 명시적 주입, PATH에 bin 등록. 사용자는 Git 루트 밖에서 호출 |
| 사용자 VWP 설정 | `/etc/sanity/vwp-submit.json` | 제공된 엄격한 JSON 설정. 프로필·spool/storage ID·service UID. DB 비밀 없음. 실제 SDK는 허용 회사 파일에 연결 |
| VWP 내부 제출 저장소 | `/srv/sanity/vwp-submit/inbox/<principal-key>/`와 `receipts/<principal-key>/` | 사용자/bridge에 동일 파일시스템 제공, 인증 신원·개별 ACL·완료 rename/내구성·용량 검증. OA에는 제공하지 않음 |
| VWP-bridge 설정/원본 | `/etc/sanity/vwp-bridge-submit.json`, `/var/lib/sanity/vwp-bridge/submissions/<submission-id>/` | 제공된 source worker의 설정·영속 volume/백업. bridge가 검증한 사본 보관. 실제 mount/ACL은 현장 준비 |
| VWP-bridge DB | 기존 VWP 제한 로그인·`sanity_vwp_data`, 송신 `sanity_vwp_to_oa` | 제공된 `008_submissions.sql`을 007 이후 DBA 검토·적용. 기존 4개 테이블/최소 권한 유지 |
| OA DB/화면 | 기존 OA 제한 로그인·`sanity_oa_data`, 응답 `sanity_oa_to_vwp` | 새 수신 staging/전체 로그 페이지·권한 재검사·최종 반영/응답. report 파일/제출 저장소는 마운트하지 않음 |

사내 연결·배포 후 사용자 호출 예시:

```bash
sanity submit --project PROJECT_A --block CPU --net-ver NET_01 --revision preA_postB-BE --layout post --eco eco02
sanity status 11111111-1111-4111-8111-111111111111
sanity retry 11111111-1111-4111-8111-111111111111
```

인계는 `.partial` 작성→같은 부모의 `<submission-id>.ready/` 공개→bridge 검증·영속 보관/DB 접수 순이다. `submit` 성공은 `LOCAL_QUEUED`이며, OA 최종 저장 응답을 확인한 `OA_STORED`와 다르다. 승인/누락/파싱 상태가 유효하지 않으면 OA 저장 후에도 검토가 차단된다. 제출 저장소를 제공할 수 없으면 설정 오류로 중단하며 HTTP/SCP·사용자 DB 로그인·OA 공유 파일로 우회하지 않는다.

회사 실제 마운트·주소·계정은 비공개 배포 기록으로 대응한다. 문서의 기본 경로가 존재한다고 추정하거나 이 변경만으로 디렉터리 생성·PATH/service 배포·운영 SQL을 실행하지 않는다. 008 적용 후 VWP-bridge는 `python -m vwp_bridge.submission_worker`, OA는 `python -m oa.submission_receiver`를 별도로 실행한다. 기존 분류 worker와 구분하며 사용자/배포 담당자·DBA가 승인된 인프라와 실행을 관리한다.
