# VWP 원천 수집

공유 폴더가 없는 현재 배치는 [VWP → bridge HTTPS 제출](HTTPS_SUBMISSION.md)을 사용합니다. 원본 snapshot과 재전송 UUID는 유지하며 [원본을 VWP private DB에 영구 보관](DB_ORIGINALS.md)합니다. bridge의 영구 파일 볼륨은 필요 없으며 파싱 때만 임시 복원합니다. VWP는 제출 토큰 없이 bridge에만 접속하며, OA에서 로그인한 OWNER의 범위 확정 후 보고서를 공개합니다. CLI schema 3·bridge schema 4를 사용합니다. 아래 mount/ACL 설명은 기존 schema 1 호환 배치입니다.

VWP 원천 수집 코드는 이 디렉터리, 회사 입력 어댑터는 `integration/`에 있습니다. [VWP 지시문](INTERNAL_AI_TASK.md)과 [서버별 소스·실행 안내](../DEPLOYMENT.md)를 따릅니다. 저장소 루트에서 `python -m vwp.main`으로 실행합니다.

분산 운영의 PRE/POST·예상 범위 승인·버전/실행·중복 기준은 [Q1 반영 설계](SOURCE_COLLECTION_DESIGN.md)에 정리했습니다. 기존 schema 1과 별도 V2 제출/분할 전달·workspace import를 제공하며 실제 회사 SDK/인프라는 미연결입니다.

사용자용 `bin/sanity`의 `submit/status/retry`, bridge 접수·OA 수신 작업자와 증분 SQL을 구현했습니다. PATH 설치·엄격한 설정·인증된 mount/ACL·세 회사 어댑터와 실행 순서는 [제출 실행 안내](SUBMISSION.md)를 따릅니다. 아래 schema 1 `collect`는 snapshot 준비용 개발자 명령이며 새 작업자를 대신하지 않습니다.

Python 3.10 이상과 표준 라이브러리만 사용하며 공통 계약은 `common/`에 있습니다. 실제 sanity 실행기와 내부 AI 연결은 아직 없으며,
이미 종료된 실행의 설정과 report를 수집합니다.

이 단계에는 DB 계정이 필요하지 않습니다. 결과 snapshot은 VWP 내부의 `vwp_bridge/` 입력입니다.
VWP↔OA 통신은 PostgreSQL 4개 테이블만 사용하며, 이 폴더의 report나 snapshot을 OA로 복사하지 않습니다.
브리지는 별도 `requirements.txt`의 PostgreSQL 드라이버가 필요합니다. [전체 실행 안내](../README.md)를 참조합니다.

저장소 루트에서 합성 예제를 생성합니다.

```bash
python3 vwp/main.py demo --out /tmp/sanity-demo
python3 vwp/main.py demo --out /tmp/sanity-missing --scenario missing
```

`--scenario`는 `complete`, `missing`, `malformed`, `unsupported`를 지원합니다.
`complete`는 report가 완전하다는 뜻이며 검사 통과를 뜻하지 않습니다. 수치 CSV에는
동일 객체 FAIL 두 건, 다른 객체 FAIL 한 건, PASS 한 건이 있습니다. JSON report에는
PASS와 FAIL이 각각 한 건 있으며 승인된 적용 제외 단위도 예상 목록에 포함합니다.
`missing`은 JSON report가 없고, `malformed`는 CSV 종료 표시가 없으며,
`unsupported`는 JSON report의 파서 식별자가 미지원 값입니다.

현장 입력을 수집하려면 다음 명령을 사용합니다.

```bash
python3 vwp/main.py collect --plan plan.json --reports /path/to/reports --out /tmp/sanity-run
```

설정 계약은 루트 `PROTOCOL.md`를 따릅니다. `--reports`는 report 자체의 루트입니다.
설정의 `reports/sub/check.csv`는 `/path/to/reports/sub/check.csv`를 가리킵니다.
demo 결과의 `run.json`도 `collect --plan` 입력으로 사용할 수 있으며, 수집 정보와
해시는 새 원본을 기준으로 다시 생성합니다.

출력은 `run.json`과 `reports/`입니다. 원본 파일의 크기와 SHA-256을 실제 사본과
대조하고 전체 snapshot을 완성한 후 출력 디렉터리를 공개합니다. 입력은 변경하지
않습니다. 출력은 없거나 비어 있어야 하며 원본 report 폴더 내부일 수 없습니다.
심볼릭 링크와 특수 파일은 거부합니다. 실행 중인 report에 대한 일관된 snapshot을
보장하는 기능은 없으므로 입력 작성이 끝난 뒤 실행해야 합니다.

누락된 report는 예상 목록에서 삭제하지 않습니다. 예상 밖 파일도 보존하고
`unexpected_files`에 기록해 다음 단계에서 전체 완료를 차단하도록 합니다.
미확정 목록, 실행 실패와 제외 승인 누락도 다음 단계의 검토 대상으로 유지합니다.
수집 성공은 입력 전달 준비 완료일 뿐, 파싱·검토 완료나 sanity PASS가 아닙니다.

```bash
python3 -m unittest discover -s vwp -p 'test_*.py' -v
```
