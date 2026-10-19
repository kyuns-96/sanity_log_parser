# OA 담당 범위

먼저 루트 [AGENTS.md](../AGENTS.md), [INTERNAL_AI_GUIDE.md](../INTERNAL_AI_GUIDE.md) 전체와 [자기 지시문](INTERNAL_AI_TASK.md)을 읽는다.

사내 OA AI는 `integration/corporate_sso.py`의 두 허용 메서드, `integration/corporate_submission.py`의 `CorporateSourceIdentity.resolve`와 허용 import/생성자, `integration/company_sso/`, `sso_app.py`의 허용된 주입만 수정한다. source 계정 연결은 SSO와 같은 영속 매핑을 사용한다. 공통 인증·source 수신/업무 API/store·UI·SQL·기존 테스트는 읽기 전용이다. VWP 모델/DB 자격증명을 사용하지 않는다. `AUTHORIZATION_NOT_IMPLEMENTED`를 유지하고, 예상 밖 오류는 수정/재시도를 중단해 비식별 문의와 `WAITING_FOR_CODEX`로 인계한다. 이 파일은 루트 가이드의 권한을 넓히지 않는다.
