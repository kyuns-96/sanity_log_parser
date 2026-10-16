# DB 접근 설정 — 공개용 안내

실제 사내 접속 안내 `dbaccess.md`는 Git 업로드 대상에서 제외한다.
이 문서에는 실제 서비스 주소·사용자·리소스 ID·인증정보·CA 파일 경로를 넣지 않는다.

VWP·VWP-bridge·OA별 담당과 접속 설정/권한/확인 명령·비공개 인계 양식은 [DB 연결 인계](db/CONNECTION_HANDOFF.md)에 있다. [서버별 소스·실행 안내](DEPLOYMENT.md)에 따라 VWP-bridge/OA의 자기 설정 예제를 사용한다. VWP 원천 수집에는 앱 DB 계정이 없다.

사용자가 **psycopg2 직접 PostgreSQL 접속** 가능함을 확인했다. 기본 모드는 `postgresql`이다.
호스트·포트·DB 이름·각 환경의 사용자/비밀번호·스키마·TLS 설정은 비공개 `.env`에 둔다.
변수 이름은 `SANITY_DB_HOST`, `SANITY_DB_PORT`, `SANITY_DB_NAME`, `SANITY_DB_USER_NAME`,
`SANITY_DB_PASSWORD`, `SANITY_DB_SCHEMA`, `SANITY_DB_SSLMODE`, `SANITY_DB_SSLROOTCERT`다.
기존 side DSN이 있으면 우선하므로 필드 방식으로 전환할 때는 DSN을 비우거나 제거한다.
이전 DBaaS HTTP 방식은 **임시 읽기 전용**으로만 남으며 HTTP 인증 성공을 쓰기·트랜잭션 지원으로 해석하지 않는다.

설정할 변수 이름은 [VWP-bridge 예제](vwp_bridge/.env.example) 또는 [OA 예제](oa/.env.example) 중 자기 역할을 참고한다. 실제 값은 프로젝트 루트의
비공개 `.env` 또는 해당 실행 환경에만 설정한다. DBaaS 인증 토큰과 대시보드 로그인 토큰은 별개다.
실제 인증정보, 인증서, 사내 원문, 테스트 출력물을 Git에 커밋하지 않는다.

- `SANITY_DB_ACCESS=postgresql`이 기본이며 각 환경의 DB 설정을 반드시 입력한다.
- 예전 `.env`에 `SANITY_DB_ACCESS=dbaas_http`가 남아 있으면 직접 수정한다. 이 모드에서는 전체 저장 명령을 차단한다.
- VWP와 OA는 별도 제한된 계정으로 같은 DB에 접근하고, 브리지 간 통신은 공유 DB만 사용한다.
- 실제 DB에서 트랜잭션·잠금·권한·자료형·DDL 적용 경로를 함께 검증한다.

실행 절차는 [README](README.md), 신규 테이블 신청은 [DB 안내](db/README.md)를 따른다.
