# VWP-bridge 담당 범위

먼저 루트 [AGENTS.md](../AGENTS.md), [INTERNAL_AI_GUIDE.md](../INTERNAL_AI_GUIDE.md) 전체와 [자기 지시문](INTERNAL_AI_TASK.md)을 읽는다.

사내 VWP-bridge AI는 `integration/corporate_parsers.py`의 추출/명시적 등록, `corporate_clustering.py`·`corporate_review.py`의 허용 메서드, 각각의 `integration/company_*/`만 수정한다. 공개 parser 예제, bridge 본체·DB·worker·공통 계약·UI·기존 테스트는 읽기 전용이다. 실제 접속값은 자기 서버의 저장소 루트 `.env`에서 관리한다. `CORPORATE` 보호 상태를 유지하고, 예상 밖 오류는 수정/재시도를 중단해 비식별 문의와 `WAITING_FOR_CODEX`로 인계한다. 이 파일은 루트 가이드의 권한을 넓히지 않는다.
