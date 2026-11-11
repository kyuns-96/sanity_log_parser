# 사내 메일 Protocol 연동

2026-11-11 사용자 결정: 메일은 사내 메일 서비스로 구현한다. 공통 코드는 Protocol·DB 작업 소비·발송 이력만 제공한다. SMTP 구현·설정·자동 fallback은 제거했으며 회사 API/SDK·인증·발신 계정·네트워크 방식은 사내에서 확인해 구현한다. SSO와 업무 권한 코드는 변경하지 않는다.

## 명시적 연결

`python -m oa.mail_app` → `oa/integration/corporate_mail.py:create_mail_sender()` → 공통 메일 worker에 `MailSender` 주입 → `send(message)` 한 번 호출.

- 계약: `area_power/common/mail.py`의 `MailSender`, `MailMessage`, `MailReceipt`, `MailNotConfigured`, `MailRejected`.
- 회사 구현: `oa/integration/company_mail/`에 승인 SDK wrapper를 작성하고 `create_mail_sender()`의 본문·필요 import에서 명시적으로 생성해 반환한다. 자동 plugin 탐색·환경변수 import 경로·공통 worker monkey patch는 없다.
- 기본 factory는 `MailNotConfigured`를 발생시킨다. 연결 전 worker는 DB 접속·job 점유 전에 실패한다. 모의 성공·개발용 전송·다른 프로토콜로 대체하지 않는다. `--help`는 회사 연결 없이 확인할 수 있다.
- 공통 worker, Protocol/모델, SQL, CLI composition root `oa/mail_app.py`, SSO·UI·권한·기존 테스트는 사내 AI에게 읽기 전용이다.
- 새 SDK 의존성·환경변수 키가 필요하면 사용 목적·버전·실패 계약을 비식별화해 Codex에게 문의한다. 비밀값·실제 주소·원문 SDK 예외는 공개하지 않는다.

## 요청

`MailSender.send(message: MailMessage) -> MailReceipt`를 구현한다. 구조적 Protocol이므로 상속은 필수가 아니다.

| 필드 | 의미 |
| --- | --- |
| `job_id: str` | DB 메일 job UUID |
| `attempt_id: str` | 외부 호출 전에 DB에 기록한 발송 시도 UUID. 회사 요청 추적에 사용하며 서비스의 멱등성 보장은 아님 |
| `recipients: tuple[str, ...]` | 중복 제거·검증된 이메일 주소. 사용자 요청 수신자를 우선하며 없으면 OA 기본 수신자 사용 |
| `subject: str` | 개행을 정리한 Design Atlas 알림 제목 |
| `text_body: str` | 알림·비교·지표·고정 입력·실행 조건의 텍스트 근거 |

발신 계정·인증값·SDK client·프로토콜별 헤더/인코딩은 회사 어댑터가 관리한다. 요청을 임의 절단하거나 수신자를 바꾼 뒤 전체 처리로 보고하지 않는다. SDK의 자동 재전송도 결과 불명 요청을 중복 발송하지 않도록 비활성화한다.

## 응답과 실패

`MailReceipt`는 **발송 요청 접수**만 뜻하며 실제 수신·열람을 보장하지 않는다.

- `accepted_recipients: tuple[str, ...]`: 회사 서비스가 접수를 확인한 수신자.
- `rejected_recipients: tuple[str, ...]`: 접수를 거부한 수신자.
- `provider_message_id: str | None = None`: 확인한 비밀 아닌 추적 ID. 없으면 `None`.

수락/거부 목록은 중복·겹침 없이 요청의 모든 수신자를 정확히 포함해야 한다. 전체 요청 접수만 제공하는 API는 확인된 접수를 모든 요청 수신자의 accepted로 대응한다. 단순 호출 시작·빈 응답·결과 불명으로 receipt를 만들지 않는다. 일부 거부는 `mail_partial_acceptance`, 전체 거부는 `mail_recipients_rejected`로 job이 failed가 되며 상세 receipt를 보존한다. 불완전한 receipt는 결과 불명이지 성공이 아니다.

| 상황 | 어댑터 동작 |
| --- | --- |
| 아직 미연결·호출 전 설정 누락 | `MailNotConfigured` 발생. 발송을 시도한 뒤에는 이 오류를 사용하지 않음 |
| 전체 요청이 명시적으로 거부됨 | `MailRejected` 발생 또는 전체 거부 receipt 반환 |
| timeout·연결 종료·응답 해석 불가 등 접수 여부 불명 | 예외 발생. 공통 worker가 `mail_delivery_ambiguous`로 기록하며 자동 재시도하지 않음 |

SDK 원문 응답·오류 메시지·토큰·DSN을 receipt나 로그에 넣지 않는다. 공통 worker는 어댑터 예외 원문을 저장하지 않는다.

## 설정·실행

OA의 실행 환경에 `AREA_POWER_DB_DSN`, `AREA_POWER_DB_SCHEMA`와 선택적인 `AREA_POWER_MAIL_RECIPIENTS`를 export한다. 기본 수신자는 쉼표로 구분한다. 사내 연결 비밀은 승인된 비공개 설정에서 어댑터에 주입한다. `AREA_POWER_SMTP_*`는 더 이상 사용하지 않으며 대체 읽기/호환 alias도 없다.

```bash
# 연결 전에도 도움말 확인 가능
python -m oa.mail_app --help
# 회사 어댑터 연결 후, 승인된 환경에서 실행
python -m oa.mail_app --once
python -m oa.mail_app
```

OA↔VWP 업무 전달은 기존 DB 행만 사용한다. 외부 메일 호출 중 DB 트랜잭션·행 잠금을 유지하지 않는다. 메일 접수와 DB commit은 원자적이지 않다. 접수 확인 후 완료 기록 실패는 `mail_accepted_db_ambiguous`로 남기고 재발송 전에 운영자가 확인한다. 프로세스 종료/DB 장애로 running에 남은 시도도 자동 회수·재시도하지 않는다. 기존 SMTP 시도 이력은 과거 JSON으로 보존하며 재작성하지 않는다. 새 이력은 `provider_accepted`·접수/거부 목록·추적 ID를 사용하며 추가 DDL은 없다.

## OA 사내 AI에게 전달

`AGENTS.md`, `INTERNAL_AI_GUIDE.md`, `oa/INTERNAL_AI_TASK.md`, 이 계약을 읽고 실제 사내 메일 API/SDK를 확인해 `company_mail/`과 `create_mail_sender()`만 연결한다. 기존 SSO·DB·worker·Protocol은 수정하지 않는다. 이번 연동을 위해 새 테스트/fixture/mock/검증 스크립트 파일을 추가하지 않는다. 기존 역할 검증과 승인된 실환경 확인을 구분하며 예상 밖 오류는 수정·재시도를 중단하고 `WAITING_FOR_CODEX`로 문의한다. 보고에는 기준 commit, 허용 파일 변경, 확인한 API/SDK·한도·접수/거부/불명 계약, 실제 검증 여부와 미확정 사항만 포함한다. 배포·실제 발송은 별도 사용자 승인 범위에서만 수행한다.
