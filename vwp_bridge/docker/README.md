# 중앙 VWP 수집 서비스와 VWP-bridge Docker 실행

2026-10-23 사용자 결정에 따라 VWP의 접수·원본 보관·item별 파싱·검증·DB 전송은 **중앙 수집 서비스**가 담당하고, AI 분류·판정은 OA에서 실행한다. 사용자 환경에는 DB·모델 계정 없이 제출 CLI와 원천/신원 어댑터를 배치한다. 중앙 서비스는 기존 VWP 서버에 운영할 수 있으며 별도 장비가 필수는 아니다. Python 모듈 이름 `vwp_bridge`와 기존 데이터/권한 계약은 유지한다.

[OA Dockerfile](../../oa/docker/Dockerfile-prod)을 기준으로 저장소 루트를 build context로 사용하고, Python 의존성 설치와 실행 이미지를 분리한다. 같은 회사 베이스 이미지와 사내 PyPI 미러를 사용할 수 있으며 `/opt/venv`를 실행 단계로 복사한다. OA와의 통신은 PostgreSQL을 사용하며 수신 포트는 없다. OA AI 운영 경로는 아래 **4절의 `vwp-submission-worker`**를 사용한다. 기존 VWP AI worker는 별도 선택 경로다.

## 1. 이미지와 설정 준비

Docker Engine의 BuildKit과 Docker Compose **2.30 이상**을 사용한다.

```bash
# 저장소 루트
cp vwp_bridge/docker/.env.example vwp_bridge/docker/.env
```

`vwp_bridge/docker/.env`의 `VWP_BRIDGE_BASE_IMAGE`에 승인된 회사 이미지 주소를 설정한다. **OA에 사용하는 회사 Node 20.18 / Python 이미지도 그대로 지정할 수 있다.** Python 3.10 이상과 `venv`/`pip`가 필요하다. 기본 `python:3.10-slim-bookworm`은 외부 개발 예시다. 두 빌드 단계는 같은 베이스를 사용하며 VWP-bridge 전용 [requirements.txt](../requirements.txt)만 `/opt/venv`에 설치한다. [Docker 다단계 빌드](https://docs.docker.com/build/building/multi-stage/).

OA의 `OA_PIP_INDEX`에 대응하는 빌드 설정은 `VWP_BRIDGE_PIP_INDEX`다. 승인된 사내 Nexus/PyPI의 `/simple` URL을 지정한다. 기본 `https://pypi.org/simple`은 외부 개발용이며, 빌드 ARG에는 사용자명·비밀번호·토큰이 없는 URL만 사용한다. 인증이나 사내 CA 등 비공개 pip 설정이 필요하면 아래 BuildKit `pip_config` secret을 사용한다. secret을 주면 이 설정이 미러 ARG보다 우선한다. CA는 회사 베이스의 인증서 저장소나 승인된 pip 설정으로 준비하며 TLS 검증을 자동 해제하지 않는다.

이 문서의 Compose 명령은 이 저장소의 `vwp_bridge/docker/` 배치를 기준으로 한다. 회사 CI에서 OA처럼 이 폴더를 루트 `docker/`로 옮길 때도 `Dockerfile-prod.dockerignore`를 함께 배치하며 build context는 `common/`·`vwp_bridge/`가 함께 있는 회사 저장소 루트다. 직접 빌드는 `-f docker/Dockerfile-prod`를 지정한다. Compose도 사용할 때는 `docker/.env`에 다음 두 값을 추가하고 `--env-file docker/.env -f docker/compose.yaml`로 실행한다. 이미지 내부 패키지 경로와 실행 명령은 동일하다.

```dotenv
VWP_BRIDGE_BUILD_CONTEXT=..
VWP_BRIDGE_DOCKERFILE=docker/Dockerfile-prod
```

회사 SDK는 승인된 의존성 검토 후 `/opt/venv`에 설치하고 `vwp_bridge/integration/` 어댑터에 연결한다. 베이스의 전역 Python에 설치된 SDK와 Node CLI는 이 이미지의 Python 의존성을 자동 구성하지 않는다. 기존 `SANITY_AI_MODE=cli`를 사용할 때도 승인된 CLI 실행 파일과 필요한 런타임을 현장에서 별도로 준비한다. `/tmp`는 `noexec`이므로 실행 파일 설치 위치로 사용하지 않는다.

`VWP_BRIDGE_ENV_FILE`은 **VWP-bridge 전용 비공개 실행 설정 파일의 절대 경로**다. [vwp_bridge/.env.example](../.env.example)의 기존 DB 키를 사용해 현장에서 작성한다. 중앙 수집 서비스에는 VWP 모델 설정이 필요 없으며 `SANITY_AI_MODE=disabled`를 서비스 환경에서 고정한다. OA 모델 설정은 OA에만 둔다. 기본은 VWP 제한 로그인(`sanity_vwp_app` 소속)이며 명시적 단일 계정 모드는 [DB 연결 인계](../../db/CONNECTION_HANDOFF.md)를 따른다. 파일은 실행 담당 계정만 읽을 수 있게 관리한다. Compose가 이 파일을 읽어 프로세스 환경으로 전달하므로 컨테이너에 저장소 루트 `.env`를 복사하거나 마운트할 필요가 없다.

실행 설정은 `KEY=value` 형식이며 **값을 따옴표로 감싸지 않는다**. `env_file.format: raw`로 비밀번호의 `$`, `#`와 따옴표를 값 그대로 전달한다. 기존 python-dotenv용 파일에 따옴표가 있으면 외부에 출력하지 않고 현장에서 형식을 맞춘다. Compose 자체 `--env-file`로 읽는 `vwp_bridge/docker/.env`에는 이미지·빌드 미러·파일 경로만 둔다. [Compose env_file](https://docs.docker.com/reference/compose-file/services/#env_file).

## 2. 빌드와 사용법 확인

```bash
# 저장소 루트. --quiet는 실행 설정의 비밀값을 출력하지 않는다.
docker compose --env-file vwp_bridge/docker/.env \
  -f vwp_bridge/docker/compose.yaml config --quiet
docker compose --env-file vwp_bridge/docker/.env \
  -f vwp_bridge/docker/compose.yaml build vwp-bridge
docker compose --env-file vwp_bridge/docker/.env \
  -f vwp_bridge/docker/compose.yaml run --rm --no-deps vwp-bridge
```

마지막 명령은 기본 `python -m vwp_bridge.main --help`를 실행하고 종료한다. 이미지 빌드와 기본 실행은 DB·모델을 호출하지 않는다. `vwp-bridge`는 일회성 CLI 서비스이고 상시 worker는 아래에서 명시적으로 시작한다. OA용 Nginx·프런트엔드·포트 매핑과 로컬 DB를 추가하지 않는다.

컨테이너는 UID/GID **10001:10001**, 읽기 전용 root filesystem과 쓰기 가능한 `/tmp`로 실행한다. `init: true`로 종료 신호와 자식 프로세스를 관리하며 베이스의 기존 entrypoint/HTTP healthcheck는 사용하지 않는다. HTTP healthcheck나 DB polling을 상태 확인용으로 실행하지 않는다. `ps`의 실행 상태는 DB·모델 연결이나 업무 처리가 성공했다는 뜻이 아니다.

## 3. 사내 CA와 읽기 전용 DB 점검

사내 DB/모델 CA가 필요하면 `VWP_BRIDGE_CA_DIR`에 비공개 CA 디렉터리의 절대 경로를 설정한다. 실행 설정의 `SANITY_DB_SSLROOTCERT`, `SANITY_AI_CA_PATH`는 각각 `/run/sanity-ca/<CA 파일명>`으로 지정한다. TLS 검증은 기존 계약을 따른다. 디렉터리와 파일은 UID 10001이 읽을 수 있어야 한다.

CA를 사용할 때는 아래처럼 **이후의 모든 Compose 명령에도** `compose.ca.yaml`을 함께 지정한다. Compose는 해당 디렉터리를 읽기 전용으로 마운트하며 없는 호스트 경로를 자동 생성하지 않는다.

```bash
# 사용자가 사내에서 수행하는 읽기 전용 DB 연결·스키마·권한 점검
docker compose --env-file vwp_bridge/docker/.env \
  -f vwp_bridge/docker/compose.yaml -f vwp_bridge/docker/compose.ca.yaml \
  run --rm --no-deps vwp-bridge python -m common.sanity_db check --side vwp
```

CA가 필요 없으면 위 명령에서 두 번째 `-f`를 생략한다. `SANITY_VWP_DSN`이 비어 있지 않으면 필드별 DB/TLS 설정보다 우선한다. [DB 연결 인계](../../db/CONNECTION_HANDOFF.md)의 동일 DB·별도 제한 로그인 계약을 따른다. 이미지 시작 과정에서 DDL/migration이나 queue 수신을 자동 실행하지 않는다.

## 4. 중앙 수집 서비스 실행 — OA AI 운영 경로

기존 `vwp_bridge.submission_worker`가 사용자 제출을 접수하고 원본 보관·파싱·전체 로그 전송·OA 접수 응답/receipt를 처리한다. 회사 parser registry와 신원/권한/승인 어댑터를 그대로 사용하며, 이 배포 설정이 미구현 어댑터를 활성화하거나 합성 입력으로 대체하지 않는다.

Compose 설정 파일에 다음 세 호스트 경로를 지정하고 `compose.submission.yaml`을 추가한다. 모든 호스트 파일/디렉터리는 현장 담당자가 먼저 준비한다. 없는 bind 경로를 Docker가 자동 생성하지 않는다.

| Compose 설정 | 컨테이너 경로 | 현장 준비 |
|---|---|---|
| `VWP_SUBMISSION_CONFIG_FILE` | `/etc/sanity/vwp-bridge-submit.json` — 읽기 전용 | [제출 설정 예제](../submission.example.json)에 실제 프로필·storage/node ID를 대응. root 소유, group/other 쓰기 금지, UID 10001 읽기 허용 |
| `VWP_SUBMISSION_SPOOL_DIR` | `/srv/sanity/vwp-submit` | 사용자 CLI와 동일한 인증된 파일시스템. service 소유 `storage.json`, 사용자별 inbox/receipt와 검증된 UID·ACL/상속 |
| `VWP_SUBMISSION_ARCHIVE_DIR` | `/var/lib/sanity/vwp-bridge/submissions` | service UID/GID 10001:10001 소유 0700 영속 디렉터리. 재시작 후 같은 원본과 node ID 유지, 백업 준비 |

JSON의 `service_uid`는 **10001**, `spool_root`·`archive_root`는 위 **컨테이너 경로**로 지정한다. 호스트 경로가 다르면 Compose 설정의 source만 대응한다. 예제의 `service_uid=null`과 미확정 storage/profile 값은 그대로 운영하지 않는다. 실제 마운트 신원·사용자 UID·승인 SDK·전체 준비 조건은 [제출 실행 안내 2·3절](../../vwp/SUBMISSION.md#2-설치설정인프라)을 따른다. 이 서비스는 계정·ACL·DB DDL을 생성하지 않는다.

회사 어댑터와 파일시스템 준비, 읽기 전용 DB 점검, 현장 운영 승인을 마친 뒤 실행한다.

```bash
docker compose --env-file vwp_bridge/docker/.env \
  -f vwp_bridge/docker/compose.yaml -f vwp_bridge/docker/compose.submission.yaml \
  --profile submission config --quiet
docker compose --env-file vwp_bridge/docker/.env \
  -f vwp_bridge/docker/compose.yaml -f vwp_bridge/docker/compose.submission.yaml \
  --profile submission up -d --no-deps vwp-submission-worker
docker compose --env-file vwp_bridge/docker/.env \
  -f vwp_bridge/docker/compose.yaml -f vwp_bridge/docker/compose.submission.yaml \
  --profile submission ps
```

사내 DB CA가 필요하면 세 명령 모두 `-f vwp_bridge/docker/compose.ca.yaml`을 추가하고 `SANITY_DB_SSLROOTCERT`에 컨테이너 CA 경로를 설정한다. CA override는 중앙 서비스에도 읽기 전용으로 적용된다. 중지는 같은 파일 조합으로 `stop vwp-submission-worker`를 실행한다.

이 서비스는 VWP AI를 호출하지 않으며 legacy `worker` profile을 함께 시작할 필요가 없다. 중앙 서비스가 오류로 종료되면 자동 재시작하지 않고 기존 중단·문의 절차를 따른다. 원인을 확인한 뒤 동일한 spool/archive/node 설정으로 재개하면 기존 제출/봉인/전송 체크포인트를 사용한다. 프로세스 실행 상태와 `LOCAL_QUEUED`는 OA 반영 완료를 뜻하지 않는다.

OA에서는 별도 `oa.submission_receiver`와 `oa.ai_worker`를 관리 프로세스로 실행한다. OA에 spool/archive를 마운트하지 않으며, `OA_STORED`와 검토 가능 여부는 DB 응답으로 확인한다. [OA AI 실행 계약](../../oa/AI_RUNTIME.md)의 실제 모델 판단·사람 확정·다음 report 사례 활용 검증은 별도다.

## 5. 기존 VWP worker 실행 — legacy 선택 경로

DB 점검과 현장 운영 승인을 마친 뒤 아래 명령으로 **기존 legacy 분류·추가 근거 worker**를 실행한다. 이 서비스는 DB에 쓰고 설정에 따라 VWP 모델을 호출한다. 회사 모델 설정은 [루트 안내](../../README.md#6-vwp-ai-분류-루프)를 따른다.

```bash
docker compose --env-file vwp_bridge/docker/.env \
  -f vwp_bridge/docker/compose.yaml --profile worker up -d vwp-worker
docker compose --env-file vwp_bridge/docker/.env \
  -f vwp_bridge/docker/compose.yaml --profile worker ps
docker compose --env-file vwp_bridge/docker/.env \
  -f vwp_bridge/docker/compose.yaml --profile worker stop vwp-worker
```

CA가 필요하면 세 명령 모두 `-f vwp_bridge/docker/compose.ca.yaml`을 추가한다. `worker` profile은 기본 실행에서 제외되며, 서비스 이름을 직접 지정해도 실행할 수 있다. [Compose profiles](https://docs.docker.com/compose/how-tos/profiles/).

worker는 `python -m vwp_bridge.main work --interval 2`를 그대로 실행한다. 새 workspace `CORPORATE` job 연결이나 제출 spool 감시 service를 제공하는 설정은 아니다. Docker 설정은 회사 parser/SDK를 구현하거나 `CORPORATE` 보호 상태를 해제하지 않는다. 운영 중 오류·결과 불명확 상태의 처리는 기존 계약과 [작업 가이드](../../INTERNAL_AI_GUIDE.md)를 따른다.

## 6. 완료된 snapshot 분석

VWP 영역의 완료된 snapshot 상위 디렉터리를 `VWP_BRIDGE_SNAPSHOT_DIR`, 로컬 receipt 출력 디렉터리를 `VWP_BRIDGE_RECEIPT_DIR`에 절대 경로로 설정한다. **두 호스트 디렉터리를 먼저 준비**하고 UID 10001에 입력 읽기/탐색과 출력 쓰기 권한을 부여한다. 입력은 읽기 전용 `/data/snapshots`, 출력은 `/data/receipts`에 마운트한다. `run.json`과 함께 수집된 `reports/`를 같은 snapshot 안에 보존한다.

```bash
# 회사 parser registry를 명시적으로 주입한다. 이 명령은 DB에 분석을 저장한다.
docker compose --env-file vwp_bridge/docker/.env \
  -f vwp_bridge/docker/compose.yaml -f vwp_bridge/docker/compose.analysis.yaml \
  run --rm --no-deps vwp-bridge python -m vwp_bridge.corporate_analyze analyze \
  --input /data/snapshots/RUN_ID/run.json --out /data/receipts/RUN_ID
```

CA도 필요하면 `compose.ca.yaml`을 추가하며 두 override의 마운트는 함께 적용된다. 기본 제공 parser로 분석할 때는 `vwp_bridge.corporate_analyze`를 `vwp_bridge.main`으로 바꾼다. 회사 registry는 기본 비어 있으며 실제 형식 지원을 별도로 확인해야 한다. 종료 코드 0은 명령 성공이고 sanity PASS나 사람의 검토 완료를 뜻하지 않는다.

`--out`은 로컬 `receipt.json` 출력이며 OA 전송 경로가 아니다. 원본 snapshot의 영속 보관·백업은 현장 정책으로 관리하며 OA에는 위 디렉터리를 마운트하지 않는다. 기존 worker의 추가 근거 응답은 DB에 봉인된 근거를 사용하므로 이 분석용 마운트가 필요하지 않다.

## 7. Compose 없이 빌드·실행

다음 변수는 현장의 승인된 이미지/실행 설정 경로로 지정한다. registry 인증은 승인된 Docker credential 방식으로 제공한다.

```bash
docker build -f vwp_bridge/docker/Dockerfile-prod \
  --build-arg VWP_BRIDGE_BASE_IMAGE="$VWP_BRIDGE_BASE_IMAGE" \
  --build-arg VWP_BRIDGE_PIP_INDEX="$VWP_BRIDGE_PIP_INDEX" \
  -t sanity-vwp-bridge:local .

# DB/모델을 호출하지 않는 사용법 확인
docker run --rm --init --read-only --tmpfs /tmp:rw,noexec,nosuid,size=64m \
  --cap-drop ALL --security-opt no-new-privileges:true \
  sanity-vwp-bridge:local

# 현장에서 승인한 기존 worker 실행
docker run -d --name sanity-vwp-worker --init --restart unless-stopped \
  --read-only --tmpfs /tmp:rw,noexec,nosuid,size=64m \
  --cap-drop ALL --security-opt no-new-privileges:true \
  --env-file "$VWP_BRIDGE_ENV_FILE" sanity-vwp-bridge:local \
  python -m vwp_bridge.main work --interval 2
```

사내 CA가 필요하면 `docker run`에 `--mount type=bind,src="$VWP_BRIDGE_CA_DIR",dst=/run/sanity-ca,readonly`를 추가하고 실행 설정에 컨테이너 CA 경로를 사용한다. 승인된 pip 저장소 인증은 `docker build --secret id=pip_config,src=/private/pip.conf ...`로 주입한다. Dockerfile에 secret mount가 준비되어 있으며 인증값을 `ARG`/`ENV`나 소스에 넣지 않는다. [Docker build secrets](https://docs.docker.com/build/building/secrets/).

`Dockerfile-prod.dockerignore`는 OA/VWP 원천·DB/devtools를 context에서 제외하고, 허용한 소스 디렉터리 안의 `.env`·인증서·패키지 인증 설정·report/fixture·테스트·로컬 산출물도 제외한다. 실제 사내 SDK 자료·접속값·report는 외부 대화나 공개 Git에 넣지 않는다.

## 8. 개발 검증과 현장 확인

중앙 수집 설정은 4절의 `submission` profile과 `compose.submission.yaml` 조합으로 `config --quiet`를 실행한다. 기존 CLI/legacy 설정은 아래 명령으로 검사할 수 있다. 설정 검사에는 Docker 엔진이 필요하지 않다. 테스트에는 실제 실행 설정 대신 합성 `VWP_BRIDGE_ENV_FILE`을 사용하고, 비밀값을 표시하는 `config` 출력은 현장 설정으로 실행하지 않는다.

```bash
docker compose --env-file vwp_bridge/docker/.env \
  -f vwp_bridge/docker/compose.yaml --profile worker config --quiet
docker compose --env-file vwp_bridge/docker/.env \
  -f vwp_bridge/docker/compose.yaml -f vwp_bridge/docker/compose.ca.yaml \
  -f vwp_bridge/docker/compose.analysis.yaml --profile worker config --quiet
```

외부 개발 검증과 승인된 회사 베이스의 실제 이미지 빌드·UID/volume 권한·DB/TLS·모델 연결 검증은 구분한다. 운영 접속 확인과 worker 시작은 현장 담당자가 위 절차에 따라 수행한다.

2026-10-23 중앙 수집 서비스의 외부 개발 검증:

- Compose 5.6.0에서 기본/legacy 조합 8개, 중앙 수집과 CA/분석 override 조합 4개, 필수 경로 설정 누락 거부 3개로 총 15개 시나리오를 확인했다. 중앙 profile에서 legacy worker가 제외되고 VWP AI 비활성화·기존 UID/권한·자동 경로 생성 금지 설정이 유지된다.
- 공개 Python 3.10 베이스로 실제 이미지를 빌드했다. 네트워크를 차단한 실제 Compose 컨테이너에서 제출 진입점, UID/GID·특수문자 환경값·읽기 전용 root/JSON·영속 spool/archive 쓰기와 컨테이너 재생성 후 보존, 읽기 전용 CA, 없는 bind 경로 3종 거부, DB 미설정 차단의 8개 시나리오가 통과했다. 시험 컨테이너와 네트워크는 정리했다. 이미지는 `sanity-vwp-bridge:central-intake-20261023`로 남겼다.
- 별도 socket 전용 임시 PostgreSQL에서 `run_tests.py --profile quick --require-db` 172개와 제출 계약·DB·공개 참고 파서 전용 시험 42개가 통과했다. 실패·오류·skip은 각각 0개다. `full`과 100,000건 검사는 실행하지 않았다.
- 실제 사내 DB·모델·report는 사용하지 않았다. GCA를 포함한 공개 참고 파서 시험은 합성 자료의 계약 검증이며 사내 형식 지원 완료가 아니다. CA 시험도 합성 파일의 마운트/권한 검사이며 회사 인증서·TLS 연결 검증과 구분한다.

기존 CLI/legacy Docker 구성의 외부 개발 검증 기록:

- Dockerfile 문법 검사와 Compose 2.30.3의 기본/CA/분석/worker 조합 8가지 통과. 합성 비밀번호의 `$`·`#`·따옴표 보존과 누락 설정의 거부를 확인했다.
- VWP 전용 의존성만 설치한 Python 3.12.3 환경에서 UID/GID 10001로 세 CLI의 `--help`, 읽기 전용 소스와 모의 worker 실행, DB 미설정 시 고정 오류를 확인했다. DB·모델은 호출하지 않았다.
- Docker SDK의 context 필터로 필요한 소스 보존과 합성 비공개/report/산출물 경로 25개의 제외를 확인했다. 전달되는 Python 소스의 Python 3.10 문법 검사도 통과했다.
- 기존 DB 설정 10개·parser 27개·연동 계약 14개, 총 51개 테스트 통과.

- Docker Engine 29.9.1, Buildx 0.38.0, Compose 5.6.0을 설치해 실제 Dockerfile/Compose 이미지 빌드에 성공했다. 공개 `python:3.10-slim-bookworm` 베이스의 Python 3.10.22와 고정한 VWP 전용 의존성을 확인했다.
- 실제 Docker/Compose 실행 시나리오 10개가 통과했다. 네트워크 없이 기본 CLI 실행, 설치된 Compose와 최소 지원 Compose 2.30.3의 실행, UID/GID 10001·특수문자 보존·읽기 전용 root·capability 제거·`noexec` tmpfs·역할별 의존성, 회사/공개 parser 진입점, CA/입력의 읽기 전용 마운트와 receipt 출력 보존, DB 미설정 시 고정 오류, 없는 bind 경로의 생성 거부, 기본 worker 제외, 명시적 worker 시작·SIGTERM 종료를 확인했다.

빌드한 이미지는 `sanity-vwp-bridge:local`로 남겼다. 검증용 컨테이너와 Compose 네트워크는 정리했다. 실제 DB·모델에 접속하지 않았으며, CA 확인은 합성 파일의 마운트/권한 점검이다. 승인된 회사 베이스 이미지·실제 DB/TLS·모델 연결은 현장에서 확인해야 한다.
