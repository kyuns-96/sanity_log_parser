# OA Docker 실행

저장소 루트를 build context로 사용한다. `Dockerfile-prod`는 프런트엔드를 **Node 20.18** 단계에서 빌드하고, OA Python 의존성과 Nginx를 실행 이미지에 준비한다. 실행 시 Nginx가 **8080**, 회사 SSO 앱이 컨테이너 내부 **127.0.0.1:8000**을 수신한다. 외부에 매핑하는 포트는 8080 하나다.

## 1. 이미지와 설정 준비

Docker Engine의 BuildKit과 Docker Compose **2.30 이상**을 사용한다.

```bash
# 저장소 루트
cp oa/docker/.env.example oa/docker/.env
```

`oa/docker/.env`의 `OA_BASE_IMAGE`와 `OA_NODE_IMAGE`에 승인된 회사 이미지 주소를 설정한다. **두 키에 같은 Node 20.18 / Python 3.10 회사 이미지 주소를 넣을 수 있다.** 기본 공개 이미지 값은 외부 개발 예시이며, 빌드 이미지도 `node:20.18.0-bookworm-slim`으로 고정한다. 빌드 단계는 설치 도구와 `node_modules`를 실행 결과에서 분리하기 위한 구조다. [Docker 다단계 빌드](https://docs.docker.com/build/building/multi-stage/).

프런트엔드는 Node 20.18을 지원하는 **Vite 6.4.4 / `@vitejs/plugin-react` 4.7.0** 조합과 Node 20 계열 타입 정의를 사용한다. React·Tailwind와 화면 소스는 기존 구성을 사용한다. `package-lock.json`을 함께 관리하고 Docker의 `npm ci --engine-strict`로 호환되지 않는 의존성 설치를 거부한다. [Vite 6 요구사항](https://v6.vite.dev/guide/), [React 플러그인 계약](https://github.com/vitejs/vite-plugin-react/blob/plugin-react%404.7.0/packages/plugin-react/package.json).

실행 베이스에는 Python 3.10 이상, `venv`/`pip`, Nginx가 필요하다. Nginx가 없으면 Dockerfile은 Debian/Ubuntu 계열의 `apt-get`으로 설치한다. 사내 베이스의 승인된 apt/pip/npm 저장소와 CA를 사용하며, 다른 배포판이면 승인된 Nginx 포함 베이스를 지정한다. 회사 SSO SDK는 승인된 의존성 검토 후 `/opt/venv`에 설치하고 `oa/integration/` 어댑터에 연결한다. 베이스의 전역 Python에만 설치된 SDK는 이 격리된 venv에서 자동으로 보이지 않는다.

`OA_ENV_FILE`은 **OA 전용 비공개 실행 설정 파일의 절대 경로**다. [oa/.env.example](../.env.example)의 기존 키를 사용해 현장에서 작성한다. VWP 로그인이나 모델 토큰을 넣지 않는다. 파일은 Compose가 읽어서 프로세스 환경으로 전달하며 이미지로 복사하지 않는다. 파일 권한은 현장 실행 계정만 읽을 수 있게 관리한다.

`format: raw`는 비밀번호의 `$` 등을 보간하지 않는다. 이 실행 설정 파일은 Docker `--env-file`과 같은 `KEY=value` 형식이며, 값을 감싸는 따옴표도 값에 포함되므로 **따옴표 없이** 작성한다. 이 파일을 Compose 자체의 `--env-file`과 혼용하지 않는다. [Compose env_file](https://docs.docker.com/reference/compose-file/services/#env_file).

`OA_ALLOWED_HOSTS`에는 브라우저 또는 앞단 ingress가 보내는 실제 Host의 **정확한 도메인/IP를 쉼표로 구분**해 현장에서 설정한다. 포트·URL·와일드카드는 넣지 않는다. `127.0.0.1`은 컨테이너 상태 확인용으로 항상 허용한다. 나머지 미등록 Host는 Nginx가 400으로 거부한다. 실제 사내 주소를 공개 설정 예제에 기입하지 않는다.

## 2. 빌드와 실행

```bash
# 저장소 루트. config --quiet는 비밀값을 출력하지 않는다.
docker compose --env-file oa/docker/.env -f oa/docker/compose.yaml config --quiet
docker compose --env-file oa/docker/.env -f oa/docker/compose.yaml build oa
docker compose --env-file oa/docker/.env -f oa/docker/compose.yaml up -d oa
docker compose --env-file oa/docker/.env -f oa/docker/compose.yaml ps
curl --fail http://127.0.0.1:8080/api/health
```

화면은 `http://127.0.0.1:8080/` 또는 허용한 사내 진입 주소로 접속한다. Compose는 `8080:8080`을 매핑한다. 플랫폼이 컨테이너 포트만 지정받는 경우에도 **8080**을 사용한다. 8000은 공개하지 않는다. 실행 계정은 UID/GID `10001:10001`이며, 쓰기 위치는 `/tmp`다.

Nginx는 기존 OA 앱으로 `/`, `/api/*`, `/auth/*`를 전달한다. 허용 Host 검사 후 내부 Host를 loopback으로 바꾸며 전달된 신원/proxy 헤더로 사용자를 인증하지 않는다. OA의 `proxy_headers=False`는 그대로 적용된다. 실제 SSO의 외부 callback URL·Secure cookie·CSRF·TLS ingress 계약은 회사 어댑터 연동 시 별도로 확인해야 한다. 이 HTTP 8080 예제가 TLS 종료나 SSO 운영 연결을 구현한 것은 아니다.

`start.py`는 두 프로세스를 함께 관리한다. 하나가 종료되면 다른 프로세스도 정리하고 컨테이너를 실패로 종료한다. Docker 종료 신호는 두 프로세스 그룹에 전달되며 제한 시간 후 강제 정리한다. 요청 URL/토큰이 로그로 남지 않도록 Nginx/uvicorn access log를 끈다.

상태 확인은 Nginx→OA의 `/api/health` 경로만 검사한다. **DB·SSO·업무 권한 검증 성공을 의미하지 않는다.** 회사 어댑터 미구현 시 `/api/session`의 `AUTH_SSO_NOT_CONFIGURED`, SSO 업무 범위 미연결 시 `AUTHORIZATION_NOT_IMPLEMENTED`가 유지된다. 데모·로컬 토큰으로 자동 전환하지 않는다.

## 3. 사내 CA와 읽기 전용 DB 점검

사내 CA가 필요하면 비공개 디렉터리를 준비해 `OA_CA_DIR`에 절대 경로를 설정하고, OA 실행 설정의 `SANITY_DB_SSLROOTCERT`를 `/run/sanity-ca/<CA 파일명>`으로 지정한다. 디렉터리와 CA 파일은 컨테이너 UID 10001이 읽을 수 있어야 한다. 아래처럼 CA override 파일을 **이후의 모든 Compose 명령에 함께** 지정한다.

```bash
docker compose --env-file oa/docker/.env \
  -f oa/docker/compose.yaml -f oa/docker/compose.ca.yaml up -d oa

# 사용자가 사내에서 수행하는 읽기 전용 연결·스키마·권한 확인
docker compose --env-file oa/docker/.env \
  -f oa/docker/compose.yaml -f oa/docker/compose.ca.yaml \
  exec oa python -m common.sanity_db check --side oa
```

`SANITY_OA_DSN`이 설정되어 있으면 필드별 DB/TLS 설정보다 우선한다. 상세 계약은 [DB 연결 인계](../../db/CONNECTION_HANDOFF.md)를 따른다. 이미지 빌드/시작 과정에서 DB 접속·DDL·migration·queue 수신을 자동 실행하지 않는다.

## 4. 선택형 기존 분류 결과 수신 worker

`oa-receiver`는 기본 실행에서 제외된다. DB 점검과 사용자 운영 승인 후 기존 VWP 분류 결과를 수신할 때 명시적으로 시작한다. 번들 전체 수신이나 새 workspace `CORPORATE` 작업 연결을 대신하지 않는다.

```bash
docker compose --env-file oa/docker/.env -f oa/docker/compose.yaml \
  --profile receiver up -d oa oa-receiver
```

이 서비스는 DB에 쓰므로 health 확인을 위한 테스트로 실행하지 않는다. CA가 필요하면 위와 같이 CA override도 함께 지정한다.

## 5. Compose 없이 빌드·실행

아래 `OA_BASE_IMAGE`, `OA_NODE_IMAGE`, `OA_ENV_FILE`, `OA_ALLOWED_HOSTS`는 현장의 승인된 값을 환경에 설정한다. 베이스 이미지와 build 이미지의 registry 자격증명은 승인된 Docker credential 방식으로 제공한다.

```bash
docker build -f oa/docker/Dockerfile-prod \
  --build-arg OA_BASE_IMAGE="$OA_BASE_IMAGE" \
  --build-arg OA_NODE_IMAGE="$OA_NODE_IMAGE" \
  -t sanity-oa:local .

docker run -d --name sanity-oa --init \
  --read-only --tmpfs /tmp:rw,noexec,nosuid,size=64m \
  --cap-drop ALL --security-opt no-new-privileges:true \
  --env-file "$OA_ENV_FILE" -e OA_ALLOWED_HOSTS="$OA_ALLOWED_HOSTS" \
  -p 8080:8080 sanity-oa:local
```

인증이 필요한 패키지 저장소는 `docker build`의 `--secret id=npm_config,src=/private/npmrc` 또는 `--secret id=pip_config,src=/private/pip.conf`로 주입한다. Dockerfile에 해당 secret mount가 준비되어 있다. 비밀을 `ARG`·`ENV`·소스에 넣지 않는다. [Docker build secrets](https://docs.docker.com/build/building/secrets/).

`Dockerfile-prod.dockerignore`는 `.env`, 인증서, 편집기 복구 파일, 테스트·로컬 빌드 출력, VWP/devtools/DB 자료를 빌드 context에서 제외한다. 실제 사내 접속값·SDK 자료·report를 외부 대화나 공개 Git으로 내보내지 않는다.

## 6. 개발 환경 검증

아래 시험은 회사 SDK·DB를 호출하지 않는다. Host 설정의 구성 주입/와일드카드 거부와 실제 자식 프로세스의 실패·종료·강제 정리를 검증한다.

```bash
.venv/bin/python -m unittest discover -s oa/docker -p 'test_*.py' -v
.venv/bin/python -m unittest discover -s tests -p test_auth.py -v
.venv/bin/python -m unittest discover -s oa -p test_sso.py -v
npm --prefix oa/dashboard run build
```

외부 개발 환경에서의 검증과 사내 베이스 이미지 빌드·SSO·실제 DB 점검 결과를 구분한다. 실제 이미지 빌드와 사내 접속 확인은 위 실행 절차로 현장에서 수행한다.
