# 공통 계약과 기반 코드

VWP·VWP-bridge·OA가 함께 사용하는 계약을 한 곳에서 관리한다. `common`은 각 서버 앱이나 `devtools`를 import하지 않는다.

| 파일 | 책임 |
|---|---|
| `sanity_common.py`, `sanity_parsers.py` | 원문·ID·봉인·coverage·parser 추상/검증 |
| `sanity_config.py`, `sanity_db.py`, `sanity_postgres.py`, `sanity_dbaas.py` | 저장소 루트 `.env` 읽기·제한 DB 연결·기존 HTTP 확인 |
| `sanity_clustering.py`, `sanity_clustering_advice.py`, `sanity_workspace_rationale.py`, `sanity_review_advice.py` | 순수 분류/판정·그룹 근거 계약과 검증 |
| `sanity_integration_contracts.py`, `sanity_integration_examples.py` | 사내 구현용 고정 타입·추상 인터페이스·작은 합성 예제 |
| `sanity_privacy.py`, `sanity_exchange.py`, `sanity_workers.py` | 비밀값 보호·추가 근거 selector·공통 polling |

DB에 실제 분석/수신/검토를 저장하거나 메시지를 처리하는 코드는 각각 `vwp_bridge/`, `oa/`에 있다. 회사 구현은 각 서버의 `integration/`에 작성한다. 사내 AI는 이 디렉터리를 수정하지 않는다. [작업 가이드](../INTERNAL_AI_GUIDE.md)와 [서버별 실행](../DEPLOYMENT.md)을 따른다.
