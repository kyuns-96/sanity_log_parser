# VWP-bridge 담당 범위

먼저 루트 [AGENTS.md](../AGENTS.md), [INTERNAL_AI_GUIDE.md](../INTERNAL_AI_GUIDE.md) 전체와 [자기 지시문](INTERNAL_AI_TASK.md)을 읽는다.

[AI 판단 설계](../AI_DECISION_DESIGN.md)의 최초 분류·그룹 판정·확정 근거 피드백이 최우선 목표다. 이번 모델 연동은 OA API를 우선 사용한다. bridge는 전체 파싱·원본 검증·DB 전달을 준비하고, 기존 VWP 모델 어댑터는 그 경로를 유지할 때만 사용한다. OA 호출·사례 검색·최초 판정 공통 코드는 Codex가 제공하며 이 문서가 허용 목록을 넓히지 않는다.

사내 VWP-bridge AI는 `integration/corporate_parsers.py`의 추출/명시적 등록, `corporate_clustering.py`·`corporate_review.py`·`corporate_submission.py`의 가이드에 열거된 메서드, 각 허용 `integration/company_*/`만 수정한다. 제출 신원 wrapper는 `company_submission/`에 둔다. 공개 parser 예제, bridge 본체·DB·source 작업자를 포함한 worker·공통 계약·UI·기존 테스트는 읽기 전용이다. 실제 접속값은 자기 서버의 저장소 루트 `.env`에서 관리한다. `CORPORATE` 보호 상태를 유지하고, 예상 밖 오류는 수정/재시도를 중단해 비식별 문의와 `WAITING_FOR_CODEX`로 인계한다. 이 파일은 루트 가이드의 권한을 넓히지 않는다.
