# VWP 원천 담당 범위

먼저 루트 [AGENTS.md](../AGENTS.md), [INTERNAL_AI_GUIDE.md](../INTERNAL_AI_GUIDE.md) 전체와 [자기 지시문](INTERNAL_AI_TASK.md)을 읽는다.

사내 VWP AI의 수정 위치는 `integration/corporate_plan.py`, `integration/corporate_submission.py`의 가이드에 열거된 메서드/명시적 주입과 `integration/company_plan/`뿐이다. `main.py`, `submission_cli.py`, 공통 코드·SQL·UI·기존 테스트는 읽기 전용이다. DB·모델 계정을 사용하지 않는다. 실제 원문/비밀은 사내에만 두고, 예상 밖 오류는 수정/재시도를 중단해 비식별 문의와 `WAITING_FOR_CODEX`로 인계한다. 이 파일은 루트 가이드의 권한을 넓히지 않는다.

Q1의 [분산 수집 설계](SOURCE_COLLECTION_DESIGN.md)와 [제출 실행 안내](SUBMISSION.md)를 따른다. 기존 `get_plan()`은 schema 1 plan 반환만 맡는다. 새 `prepare()`는 V2 plan과 원본 Path 대응을 읽어 반환하고 공통 CLI가 snapshot을 만든다. 예상 범위의 발견·실제 승인·수집 완료를 구분하며 두 계약/공통 봉인·DB/전송을 변경하지 않는다.
