# VWP 원천 담당 범위

먼저 루트 [AGENTS.md](../AGENTS.md), [INTERNAL_AI_GUIDE.md](../INTERNAL_AI_GUIDE.md) 전체와 [자기 지시문](INTERNAL_AI_TASK.md)을 읽는다.

사내 VWP AI의 수정 위치는 `integration/corporate_plan.py`의 허용 메서드와 `integration/company_plan/`뿐이다. `main.py`, 공통 코드·SQL·UI·기존 테스트는 읽기 전용이다. DB·모델 계정을 사용하지 않는다. 실제 원문/비밀은 사내에만 두고, 예상 밖 오류는 수정/재시도를 중단해 비식별 문의와 `WAITING_FOR_CODEX`로 인계한다. 이 파일은 루트 가이드의 권한을 넓히지 않는다.
