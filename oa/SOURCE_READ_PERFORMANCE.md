# OA 원천 로그 읽기 성능

`sanity_workspace_sources.hydrate`와 `records_page`는 전체 원천 로그의 검증 결과를
프로세스 내에서 재사용한다. 로그·필드·순서·ID·사람 확정 이력·AI 입력은 줄이지 않는다.
기존 페이지의 표시용 raw 길이 제한과 전체 AI 입력 한도도 그대로다.

## 읽기와 검증

- 한 번의 읽기에서 기존 013 스키마/FK 검사를 수행하고, 최대 64개 part씩 private 참조와
  공유 큐 행을 묶어서 조회한다. SQL 반환 순서와 관계없이 manifest 순서대로 복원한다.
- 매 요청의 SSO/현재 블록 권한 확인은 기존 업무 API에서 수행한다. 권한·HTTP 응답·DB 행을
  캐시하지 않으며, 권한 회수 후 예전 본문 캐시로 조회를 허용하지 않는다.
- 캐시 hit에서도 현재 private 참조·scope·manifest를 확인하고 실제 조회한 gzip byte의
  SHA256을 계산해 `payload_sha256`과 대조한다. DB 읽기나 무결성 검사를 생략하지 않는다.
- 키에는 DB/스키마/로그인 구분, message ID와 현재 큐의 불변 metadata, 실제 압축 hash,
  비밀 패턴·known secret 정책 fingerprint를 결합한다. 참조/내용/정책 변경으로 이전 검증을
  재사용할 수 없으면 처음부터 검증한다. 손상·누락에는 이전 캐시로 대체하지 않고 실패한다.
- 첫 읽기는 엄격한 JSON/gzip/크기, envelope/message ID, body/packet hash, 비밀 패턴,
  report·part 순서·건수·scope를 검증한다. 큰 records 배열을 canonical JSON으로 한 번
  직렬화한 byte를 body/packet 구성과 hash에 재사용한다.
- 이미 검증한 JSON의 비밀 패턴 검사는 별도 객체 트리를 만들지 않고 수행한다. 기존
  `SecretGuard.ensure_public`, receiver/producer 검증과 저장 형식은 변경하지 않는다.
  프로세스의 첫 읽기 검증은 유지하며 receiver의 검증만 믿고 생략하지 않는다.
- 캐시에는 검증된 불변 JSON byte를 보관하고 매번 독립 객체로 decode한다. 한 요청에서
  반환값을 수정해도 다음 요청의 기록이 바뀌지 않는다.

## 범위와 운영

캐시는 프로세스별 **128MiB canonical body byte·512개 entry·검증 후 300초** 한도다.
LRU로 퇴출하며 만료 검사는 읽을 때 수행한다. 이 byte 한도는 전체 프로세스 RSS 상한이
아니다. 동시에 처리하는 요청의 객체와 여러 worker의 개별 캐시 메모리는 별도로 고려한다.
재시작/worker 변경/만료/퇴출 후에는 다시 첫 읽기 검증을 거친다.

추가 DDL·테이블·GRANT·환경변수·외부 캐시 서버는 없다. 기존 013 공유 packet 참조에
캐시를 적용하고 이전 inline 자료도 계속 읽는다. DB 연결의 `get_dsn_parameters()`로
namespace를 확인할 수 없는 어댑터는 프로세스 캐시를 쓰지 않고 검증을 계속한다.
이는 새 DB 어댑터 지원을 추가하는 변경이 아니다.

OA 호환 코드를 적용하고 관련 OA 프로세스를 재시작한다. 원본/큐 재전송이나 데이터 변환은
필요 없다. 별도 `sanity_preserved_text`의 plan 4 보존 본문 reader와 모델 실행 자체의
성능 개선을 포함한다고 해석하지 않는다. 내부 AI의 공통 코드 수정 권한은 확대하지 않으며
[기존 작업 범위](../INTERNAL_AI_GUIDE.md)를 유지한다.
