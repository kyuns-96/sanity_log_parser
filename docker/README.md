# OA 사내 CI/CD

사내 Nginx가 앞단에서 요청을 전달하는 환경용이다. 컨테이너는 **OA 앱 한 개만 8080으로 실행**하며 화면과 API를 함께 제공한다. Nginx 설치, Compose, 별도 시작 스크립트는 사용하지 않는다.

사내 OA Git에는 다음 구성을 유지한다. 빌드 context는 이 저장소의 루트다.

```text
docker/
  Dockerfile-prod
  Dockerfile-prod.dockerignore
oa/
common/
```

## 사내에서 지정할 값

- **베이스 이미지**: Python 3.10 이상, pip, Node 20.18과 npm이 들어 있는 승인된 이미지. `OA_BASE_IMAGE` 빌드 인자로 지정한다. CI가 빌드 인자를 지원하지 않으면 Dockerfile의 `FROM ${OA_BASE_IMAGE}` 한 줄을 실제 사내 이미지로 교체한다. 기본 `sanity-oa-base:local`은 현장에서 준비할 이미지 이름이며 제공된 이미지가 아니다.
- **실행 포트**: `8080`. 사내 Nginx의 upstream을 이 컨테이너의 8080으로 연결하고 `/`, `/api/`, `/auth/`를 같은 서비스에 전달한다.
- **`OA_ALLOWED_HOSTS` 실행 환경변수**: 실제 서비스 도메인/IP를 쉼표로 구분한다. URL, 포트, 와일드카드는 넣지 않는다. 미지정 시 localhost만 허용한다. `127.0.0.1`은 상태 확인용으로 항상 허용된다. Nginx가 Host를 보존한다면 서비스 도메인을, 변경한다면 변경 후 Host도 포함한다.
- **DB·SSO 설정**: [OA 설정 예제](../oa/.env.example)의 기존 키와 회사 SDK 설정을 CI의 비공개 실행 환경으로 주입한다. 비밀번호·토큰·인증서·패키지 저장소 인증 설정을 Dockerfile이나 Git에 넣지 않는다. 사내 npm/pip 저장소와 CA는 승인된 베이스 이미지/CI 구성을 사용한다. TLS 검증은 끄지 않는다.

Dockerfile은 전역 Python에 패키지를 설치하므로 회사 SDK도 해당 Python에서 import할 수 있어야 한다. 베이스 이미지의 Python은 pip 설치를 지원해야 한다.

## 빌드와 확인

아래 두 환경변수는 현장 값으로 지정한다. 회사 CI가 컨테이너를 직접 실행하면 `docker run` 대신 컨테이너 포트·실행 환경변수만 등록한다.

```bash
docker build -f docker/Dockerfile-prod \
  --build-arg OA_BASE_IMAGE="$OA_BASE_IMAGE" -t sanity-oa:local .

docker run -d --name sanity-oa \
  --env-file /srv/sanity-oa/oa.env -e OA_ALLOWED_HOSTS="$OA_ALLOWED_HOSTS" \
  -p 127.0.0.1:8080:8080 sanity-oa:local

curl --fail http://127.0.0.1:8080/api/health
docker logs sanity-oa
```

빌드 로그에서 `pip install`, `npm ci`, `npm run build`가 별도 단계로 표시된다. 컨테이너 내부 점검이 필요하면 `docker run --rm -it --entrypoint /bin/sh sanity-oa:local`로 확인한다. 실행 사용자는 UID/GID `10001:10001`이다. CI의 상태 확인 경로는 `/api/health`, Host는 `127.0.0.1` 또는 등록한 서비스 도메인을 사용한다. 이미지 안에서 별도 healthcheck를 실행하지 않는다.

TLS 종료는 사내 Nginx가 담당한다. 앱은 proxy 헤더로 신원을 인증하지 않고 access log를 남기지 않는다. 외부 SSO callback URL·쿠키 설정은 회사 어댑터에서 확인한다. SSO SDK와 업무 권한 미연동 차단은 유지하며 health 성공은 DB·SSO 연동 완료를 뜻하지 않는다. 빌드/시작 과정에서 DB 접속이나 migration을 실행하지 않는다.

`oa/docker/`는 Nginx를 함께 실행하던 기존 구성이다. 이 사내 CI/CD 구성의 빌드 경로는 **`docker/Dockerfile-prod`**다.
