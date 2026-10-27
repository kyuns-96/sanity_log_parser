# 빈 초기 DB를 위한 포털 DDL 실행 안내

**선택형 압축 저장 011:** `prepare --payload-storage`는 `payload-plan.json`으로 기존 59단계 뒤 60번 단일 ALTER를 추가한다. 010 완료·0행·쓰기 중지 snapshot에서는 60번만 준비한다. 55/59단계 원본 계획은 유지한다. 데이터가 있는 DB의 011·변환·재작성은 [별도 DBA 유지보수](../PAYLOAD_STORAGE.md)이며 이 포털 도구가 실행하지 않는다.

**원본 DB 보관 010:** 기존 `plan.json`의 55단계는 유지한다. `prepare --raw-sources`가 별도 `raw-plan.json`의 전체 59단계를 선택하며, 009 완료 checkpoint에서는 56~59의 4개 단일 문장만 준비한다. 기존 0행/쓰기 중지/catalog 전후 검증 조건은 그대로다. 데이터가 있는 DB의 원본 010 적용은 DBA 검토 경로다. [원본 DB 보관](../../vwp/DB_ORIGINALS.md)을 참고한다.

사용자가 확인한 회사 포털은 한 요청에 한 DDL 문장만 받으며 FK 설정도 지원한다. owner psql 대신 이 포털을 사용하는 **별도 실행본**을 제공한다. 원본 form과 `002`~`008` SQL은 변경하지 않는다. 2026-10-22 확인된 [SSO 계정 저장 계약](../../oa/SSO_IDENTITY.md)의 `009`를 마지막 한 단계로 추가했다. `003` 역할·권한은 별도 DBA 경로다. 009는 DB 저장 구조이며 JIT 앱 구현·업무 권한 연결은 포함하지 않는다.

`003` 역할 준비는 기본 제한 계정 모드의 조건이다. 명시적 `SANITY_DB_ALLOW_SINGLE_ACCOUNT=true`는 [별도 공유 로그인 계약](../CONNECTION_HANDOFF.md#31-단일-계정-모드)을 사용하며 역할 생성을 요구하지 않는다. DB의 private/큐 컬럼 격리가 없다는 제약을 기록한다. 포털 구조 계획·원본 SQL·기존 완료 단계와 기능별 `002`, `004`~`009` 조건은 그대로이며 이 모드 선택 때문에 DDL을 반복하지 않는다.

대상은 **기존 네 테이블이 모두 비어 있고, 양쪽 bridge·대시보드·수신/제출 작업자의 DB 작업이 중지된 초기 설치**다. 데이터가 생긴 DB의 일반 업그레이드 도구가 아니다. 실제 스키마 이름·catalog·생성된 실행본은 사내 비공개 폴더에 보관한다. 공개 Git이나 외부 Codex 대화에 올리지 않는다.

## 1. 제공 파일과 책임

| 파일 | 역할 |
|---|---|
| `runbook.py` | Python 표준 라이브러리만 사용하는 오프라인 도구. 조회 SQL 생성·catalog 비교·실행본 생성·결과 검증만 수행. DB 연결·DDL 실행·자동 재시도 없음 |
| `plan.json` | 원본 form·`002/004/005/006/007/008/009`의 구조 변경 계획. 기존 54단계/55개 구조를 보존하고 009의 단일 ALTER를 추가한 55단계/56개 구조. 원본 SHA-256과 기존 제약을 보존 |
| `build_reference.py` | Codex/개발자 전용 기준 계획 생성기. 새 로컬 소켓 전용 합성 DB에서만 실행. 사내 운영 연결 인자를 받지 않음 |

사내 AI는 승인된 읽기 전용 접속으로 확인한 catalog를 비공개 파일로 준비하고 `runbook.py`를 실행할 수 있다. 원본 SQL·도구·계획 JSON·기존 테스트를 수정하지 않는다. 실제 DDL 포털 적용과 DBA 권한 변경은 사용자/승인된 운영 담당자가 수행한다. 실패하거나 예상한 기준 상태와 다르면 `WAITING_FOR_CODEX`로 문의한다.

## 2. 실행 전 확인

- PostgreSQL 12.1 이상과 UTF-8, 같은 DB/실제 스키마에 있는 기존 네 테이블을 사용한다. 실검증 버전은 12.1과 16.15다. 최초 form의 CHECK·UNIQUE도 확인한다. 예를 들어 VWP의 `(analysis_id, record_id)` UNIQUE가 없으면 FK의 선행 조건도 없으므로 도구는 중단한다.
- 포털이 **한 ALTER TABLE의 복수 절**을 허용해야 한다. CHECK의 삭제와 추가는 같은 ALTER 문장에 포함된다. 별도 요청으로 쪼개거나 CHECK/FK를 생략하지 않는다.
- 원본의 전체 파일 트랜잭션과 명시적 LOCK은 포털 실행본에 없다. 각 DDL 문장의 원자성만 보장된다. 작업 전체가 끝날 때까지 앱/작업자 쓰기를 재개하지 않는다.
- 원본의 `SET LOCAL lock_timeout='5s'`도 문장 안에 넣지 않는다. 포털 DDL 세션의 잠금/문장 timeout과 취소 방법을 플랫폼 담당자에게 확인한다. 별도 읽기 연결에서 조회한 timeout을 포털 세션 값으로 간주하지 않는다.
- 단순 접속 성공·네 테이블 읽기/쓰기만으로 앱 권한 검증을 대신하지 않는다. 기본 모드는 `003_grants.sql`의 별도 로그인·자기 역할·테이블/컬럼 권한을, 명시적 단일 모드는 [DB 연결 인계](../CONNECTION_HANDOFF.md)의 별도 계약을 검사한다.

### PG12 조회 오류가 있었던 경우

이전 도구의 `column i.indnullsnotdistinct does not exist`는 PG15부터 생긴 catalog 컬럼을 PG12에서 직접 조회한 오류다. 수정본은 catalog 행을 JSON으로 읽어 없는 필드에 PG12의 NULL-distinct 의미(`false`)를 사용하고, PG15 이상에서는 실제 값을 보존한다. snapshot의 버전 값은 바꾸지 않는다. PG12.1 미만·잘못된 버전 값·UTF-8이 아닌 DB는 계속 거부한다.

수정본으로 **조회 SQL부터 새로 생성**한다. 이전 SQL·snapshot·bundle을 덮어쓰거나 그 안의 버전/hash를 편집하지 않는다.

```bash
python3 db/portal/runbook.py inspect --schema SCHEMA_NAME --out /private/inspect-pg12.sql
```

새 SQL을 기존 읽기 전용 연결에서 실행하고 JSON 객체를 `/private/before-pg12.json`으로 저장한 뒤 준비한다.

```bash
python3 db/portal/runbook.py prepare --snapshot /private/before-pg12.json --out /private/portal-bundle-pg12
```

이후에는 새 bundle과 새 snapshot으로 아래 전후 검증을 수행한다. 이전 버전에서 만든 PG16 bundle도 조회 SQL이 바뀌었으므로 현재 snapshot으로 새 폴더에 다시 준비한다. DDL을 이미 실행했다면 완료 단계를 반복하지 않으며, 확인된 중간 상태에서의 재개는 5절을 따른다. 실제 포털 적용·DBA 권한 작업의 책임과 실패 시 중단 규칙은 같다.

## 3. 읽기 전용 사전 조회

아래 명령의 `SCHEMA_NAME`과 `/private/...`는 사내에서 실제 값/비공개 경로로 바꾼다. 도구는 `.env`나 DSN을 읽지 않는다. 저장소 루트에서 실행한다.

```bash
python3 db/portal/runbook.py inspect --schema SCHEMA_NAME --out /private/inspect.sql
```

생성된 `inspect.sql` 전체는 **SELECT 한 문장**이다. 기존 승인된 읽기 전용 연결에서 실행하고 `snapshot` 컬럼의 JSON 객체 값만 `/private/before.json`에 저장한다. 테이블·컬럼·CHECK/UNIQUE/FK·인덱스의 실제 정의/검증 상태와 네 테이블의 실제 행 수를 읽는다. GUI의 표 제목이나 JSON 배열로 감싸지 않는다. 실제 접속값이나 catalog를 외부로 전달하지 않는다.

```bash
python3 db/portal/runbook.py prepare --snapshot /private/before.json --out /private/portal-bundle
```

초기 form 상태 또는 도구가 알고 있는 정확한 중간 상태일 때만 실행본을 생성한다. 원본 SQL의 hash가 달라졌거나 데이터가 있거나 컬럼/제약/인덱스 정의가 다르면 중단한다. GUI가 부여한 제약 이름은 정의를 정확히 대조해 사용하며, 같은 정의가 중복되면 추정하지 않는다. 이미 적용한 단계를 `IF NOT EXISTS`로 무시하지 않는다. 출력 폴더는 새 경로여야 한다.

## 4. 한 문장씩 적용·검증

`manifest.json`의 `steps` 순서로 진행한다. 처음부터 시작하면 DDL은 **55문장**이며 `002 → 004 → 005 → 006 → 007 → 008 → 009`를 포함한다. 008까지 완료한 경우 아래 절차로 **55번 한 문장만** 생성한다. 동일 테이블의 CHECK·scope·컬럼 변경은 한 ALTER 문장으로 묶고, 인덱스 생성은 각각 별도 문장이다. FK의 복합 키·참조 대상·DEFERRABLE/INITIALLY DEFERRED 등 원본 옵션을 보존한다.

1. bundle의 `inspect.sql`을 읽기 전용 연결에서 실행해 현재 snapshot을 새 비공개 JSON으로 저장한다.
2. 다음 명령으로 해당 문장의 **적용 전 상태**를 검증한다. `--step`은 manifest의 실제 번호다.

   ```bash
   python3 db/portal/runbook.py verify --bundle /private/portal-bundle --snapshot /private/current.json --step 1 --when before
   ```

3. `status=SCHEMA_VERIFIED`일 때만 해당 `steps[].file`의 SQL 한 문장 전체를 포털에 제출한다. SQL을 편집하거나 하나의 ALTER를 나누지 않는다. `DROP TABLE`·데이터 삭제·권한 부여·추가 테이블 생성은 들어 있지 않다.
4. 다시 SELECT로 새 snapshot을 저장하고 같은 번호에 `--when after`로 검증한다. 성공한 뒤 다음 번호로 이동한다. 읽기 전용 확인 실패도 정상 진행으로 간주하지 않는다.

SQL 파일 hash와 전후 구조를 검사한다. 포털 접수 성공·HTTP 200·화면 표시만으로 DB 반영을 판단하지 않는다. 단계별 문장/전후 확인 결과와 실행 시각은 사내 기록으로 남긴다.

## 5. 오류·응답 유실·재개

한 ALTER의 추가 제약이 실패하면 같은 문장 안의 CHECK 삭제·컬럼 추가도 함께 취소된다. 그러나 이전에 성공한 다른 문장까지 되돌아가지는 않는다. **실패/취소/timeout 후 다음 DDL이나 동일 DDL을 바로 재실행하지 않는다.**

쓰기 중지 상태를 유지하고 읽기 전용 SELECT로 현재 catalog를 다시 확인한다. 기존 before/after 검증 중 어느 상태인지 대조한다. 사내 AI는 예상 밖 실패를 비식별 문의로 인계하고 답변 전까지 대기한다. Codex 검토와 사용자 승인 후, 확인된 상태로 새 비공개 bundle을 생성하면 이미 완료된 문장을 제외한 나머지만 나온다.

```bash
python3 db/portal/runbook.py prepare --snapshot /private/rechecked.json --out /private/resume-bundle
```

알려진 전후 체크포인트와 일치하지 않으면 자동 복구하지 않는다. 제약을 삭제하거나 버전을 맞추는 임의 수정을 하지 않는다. 테이블 재생성·데이터 삭제·일괄 rollback SQL은 제공하지 않는다. 원본 SQL의 트랜잭션 방식과 포털 실행을 같은 DB에서 섞어 재시도하지 않는다.

### 008까지 적용한 DB에서 009만 준비

기존 008 완료 bundle과 기록은 보존한다. 기존 bundle은 이전 계획 hash에 묶여 있으므로 새 도구로 이전 bundle을 편집하거나 재검증하지 않는다. 아래 새 조회 SQL을 기존 승인된 읽기 전용 연결에서 실행해 JSON 객체를 `/private/before-009.json`으로 저장한다. 네 테이블 0행·쓰기 중지는 그대로 필요하다.

```bash
python3 db/portal/runbook.py inspect --schema SCHEMA_NAME --out /private/inspect-009.sql
# 생성 SELECT를 읽기 전용 연결에서 실행하고 새 JSON 파일로 저장한 다음
python3 db/portal/runbook.py prepare --snapshot /private/before-009.json --out /private/portal-bundle-009
```

정확한 008 완료 상태이면 `checkpoint=54 remaining=1`이다. manifest의 `total_steps=55`, 유일한 단계는 `number=55`, `phase=009`여야 한다. 다른 결과면 실행하지 않고 문의한다. 009는 기존 v6 kind/scope를 v7로 교체하고 SSO JSON 제약을 추가하는 단일 ALTER다. 기존 54문장·원본 009 파일의 DO/BEGIN/COMMIT을 포털에 다시 제출하지 않는다.

```bash
python3 db/portal/runbook.py verify --bundle /private/portal-bundle-009 --snapshot /private/before-009.json --step 55 --when before
# 승인 운영 담당자가 manifest의 유일한 SQL 파일 전체를 포털로 적용
# 새 조회 결과를 /private/after-009.json으로 저장한 다음
python3 db/portal/runbook.py verify --bundle /private/portal-bundle-009 --snapshot /private/after-009.json --step 55 --when after
python3 db/portal/runbook.py verify --bundle /private/portal-bundle-009 --snapshot /private/after-009.json
```

처음 성공 이후 다시 `prepare`하면 `checkpoint=55 remaining=0`이다. DDL을 재실행하지 않는다. 오류/응답 유실 시 5절의 중단·읽기 전용 진단 절차를 따른다.

## 6. 완료와 후속 작업

마지막 snapshot으로 전체 구조를 확인한다.

```bash
python3 db/portal/runbook.py verify --bundle /private/portal-bundle --snapshot /private/final.json
```

새 계획의 최종 성공은 **009까지의 구조 검증**이다. `operational_ready=false`는 의도된 결과이며, 권한·SSO·모델·실제 원천이 연결됐다는 뜻이 아니다. 기존 008 완료 기록도 해당 범위의 성공으로 보존한다.

- DBA가 원본 `003_grants.sql`의 실제 스키마·기존 권한·두 역할/로그인을 검토해 별도 적용한다. 앱 계정에 DDL이나 전체 테이블 UPDATE를 부여하지 않는다.
- 사용자/DBA가 실제 각 앱 로그인으로 기존 읽기 전용 `common.sanity_db check --side vwp` / `--side oa`를 수행한다. 스키마·유효 권한 검사를 모두 통과해야 운영 연결 검토를 진행한다. 검사 삭제나 광범위 GRANT로 우회하지 않는다.
- 009 저장 계약은 [SSO_IDENTITY.md](../../oa/SSO_IDENTITY.md)를 따른다. 실제 JIT 저장/조회·회사 ACS 호출·SSO→workspace/RBAC는 후속 공통 구현이다. 내부 AI가 자체 저장부·자동 가입·권한 부여를 구현하지 않으며 `AUTHORIZATION_NOT_IMPLEMENTED`를 유지한다.

## 7. 합성 검증과 기준 계획 갱신

개발 환경에서는 소켓 전용 mock runner의 `full` 프로필이 원본 SQL과 포털 실행본의 구조 동등성, GUI CHECK 이름, 중간 재개, 원자적 실패, FK 옵션, 최소 권한과 원본 smoke 검증을 수행한다. 기본 `quick`은 핵심 회귀·catalog 조회 호환성과 008→009 한 단계 재개/제한 계정 검사를 포함하며 포털 55단계를 전수 반복하지 않는다. 실제 회사 포털의 문법 검사·timeout·계정/DB 검증을 대신하지 않는다.

기준 계획의 합성 검증은 PG12.1과 PG16.15에서 각각 원본 SQL의 모든 중간 구조를 `plan.json`과 비교하고, 포털의 55개 DDL도 별도 스키마에서 한 문장씩 실행해 전후 구조를 확인한다. 기존 008까지의 54문장과 구조 fingerprint는 변경하지 않았다. `plan.json.initial.server_version_num=160015`는 최초 생성 환경의 기록이다. PG12에서 검증을 생략하거나 PG16으로 위장하는 조건이 아니다. CHECK/FK/UNIQUE·UNIQUE NULL 처리·인덱스 순서·전체 컬럼 비교는 동일하게 유지한다.

PG15의 NULLS NOT DISTINCT 플래그는 [PG12 catalog](https://www.postgresql.org/docs/12/catalog-pg-index.html)와 [PG15 catalog](https://www.postgresql.org/docs/15/catalog-pg-index.html)의 차이를 반영한다. 새 버전에서 이 플래그가 true인 인덱스/제약을 false로 덮어 정규화하지 않으며, 예상과 다른 정의는 계속 거부한다.

전수 `full` 검증에는 10만 건 보고서/10만 그룹 사례가 포함되므로 여러 PostgreSQL 버전은 **순차 실행**한다. 메모리 부족으로 DB 프로세스가 종료되면 검증 실패로 중단한다. `quick`/`full`은 사용자가 정한 실행 범위이며 기존 대용량 입력·기대값은 유지한다. `quick` 성공을 전수 통과로 표시하거나 실패한 검사를 생략해 통과시키지 않는다.

```bash
.venv/bin/python db/tests/run_mock.py --pg-bin /path/to/postgresql/bin
# 포털 기준 변경·전체 회귀·대용량 검증이 필요할 때
.venv/bin/python db/tests/run_mock.py --profile full --pg-bin /path/to/postgresql/bin
```

원본 증분을 바꿔야 할 때의 `build_reference.py`/`plan.json` 갱신은 Codex 담당이다. 사내 AI가 계획 hash나 비교 조건을 바꿔 불일치를 통과시키지 않는다.
