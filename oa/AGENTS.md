# OA 담당 범위

먼저 루트 [AGENTS.md](../AGENTS.md), [INTERNAL_AI_GUIDE.md](../INTERNAL_AI_GUIDE.md) 전체와 [자기 지시문](INTERNAL_AI_TASK.md)을 읽는다.

[AI 판단 설계](../AI_DECISION_DESIGN.md)의 최초 분류·그룹 판정과 확정 사례 검색·피드백이 최우선 제품 목표다. 사내 OA OpenAI 호환 API 연동을 준비하며, 이전 OA 모델 금지 방침은 대체됐다. OA 모델 호출·사례 검색·직접 근거 판정·작업자와 운영 workbench 공통 코드는 [AI 실행 계약](AI_RUNTIME.md)을 따른다. 내부 AI는 지원된 비공개 환경변수·승인된 점검 기준 catalog·API/품질 평가 자료만 준비하며 새 모델 어댑터나 공통 실행 코드를 자체 작성하지 않는다. 선택형 전체 로그·확정 그룹 판정·보존 본문 분할은 공통 구현을 사용한다. 사내 실제 API 호환·판단 품질 검증은 별도로 구분한다.

사내 OA AI는 `integration/corporate_sso.py`의 두 허용 메서드, `integration/corporate_submission.py`의 `CorporateSourceIdentity.resolve`와 허용 import/생성자, `integration/company_sso/`, `sso_app.py`의 허용된 주입만 수정한다. source 계정 연결은 SSO와 같은 영속 매핑을 사용한다. 공통 인증·source 수신/업무 API/store·UI·SQL·기존 테스트는 읽기 전용이다. VWP 모델/DB 자격증명을 사용하지 않는다. `AUTHORIZATION_NOT_IMPLEMENTED`를 유지하고, 예상 밖 오류는 수정/재시도를 중단해 비식별 문의와 `WAITING_FOR_CODEX`로 인계한다. 이 파일은 루트 가이드의 권한을 넓히지 않는다.

의존성의 유일한 추가 허용 범위는 [가이드 3.1절](../INTERNAL_AI_GUIDE.md#31-oa-sso-고정-의존성-추가-승인--2026-10-20)의 `oa/requirements.txt` 세 pin이다. OA 담당은 `signxml==5.2.0`, `lxml==6.1.3`, `cryptography==50.0.2`가 없을 때만 추가할 수 있다. 다른 pin과 충돌하면 수정하지 않고 문의하며, 나머지 requirements·lockfile·공통 설정 코드는 보호를 유지한다. `SANITY_OA_AI_*`의 지원 키와 승인된 비공개 기준 파일만 현장 값으로 설정할 수 있으며 `.env.example`·prompt·검색/권한·job 코드는 수정하지 않는다.
