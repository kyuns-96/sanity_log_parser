# 합성 자료 전용 개발 도구

운영 코드와 구분한 시험용 Python 패키지다. 실제 회사 report·사례·자격증명을 연결하지 않는다.

| 파일 | 용도 |
|---|---|
| `demo.py` | 가상 수집→VWP 분석→OA 수신의 통합 시험 |
| `sanity_sample_reports.py`, `sanity_large_samples.py` | 공개 참고 형식 합성 report·대량 검증 입력 |
| `sanity_codex_mock.py`, `sanity_workspace_codex.py` | 명시적으로 활성화한 합성 환경의 외부 모델 시험 |
| `workspace_app.py` | 독립 합성 DB 설정만 사용하는 `/workspace` 진입점 |
| `worker_dispatch.py` | 회귀 테스트에서 두 서버 worker를 호출하는 편의 함수 |

저장소 루트에서 `python -m devtools.demo --out NEW_DIRECTORY`로 실행한다. 이 통합 시험은 **별도로 준비한 합성 DB 설정**이 필요하므로 운영 환경에서 실행하지 않는다. 외부 모델 시험과 persistent workspace는 [WORKSPACE.md](../oa/WORKSPACE.md)를 따른다. `tools/`의 seed·실행 도우미, `tests/`의 회귀 테스트도 개발용이다. 일반 VWP-bridge/OA worker는 이 패키지를 사용하지 않는다.
