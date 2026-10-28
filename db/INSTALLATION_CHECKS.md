# DB 모드 선택·스키마 대응·읽기 전용 설치 점검

[연결 인계](CONNECTION_HANDOFF.md)의 실제 권한 계약과 [설치 순서](README.md)를 구체화한다. 대상은 같은 PostgreSQL DB의 기존 네 테이블이다. 아래 생성 명령은 로컬 SQL 사본만 만들며 DB에 접속하거나 실행하지 않는다. 운영 DDL·권한 변경은 사용자/DBA의 기존 적용 절차를 따른다.

## 1. restricted / single_account 선택

**공용 서버라는 이유만으로 모드를 결정하지 않는다.** 같은 PostgreSQL 서버라도 DB가 다르면 `public` 스키마도 별개다. 같은 DB를 공유할 때는 앱 스키마, 발급 로그인, 상속·PUBLIC을 포함한 유효 권한을 확인한다.

| 실제 조건 | 판단 |
|---|---|
| 서로 다른 VWP/OA 제한 로그인과 자기 앱 역할을 준비할 수 있음 | 기본 `restricted`. 상대 private 접근과 과도한 컬럼 권한을 금지한다. |
| 공유 로그인만 있고 네 테이블 전체 컬럼의 SELECT/INSERT/UPDATE가 준비됨 | `single_account`를 명시적으로 선택할 수 있다. 아래 관리자/소유/CREATE 금지 조건과 DB 격리 미제공을 함께 확인한다. |
| 같은 서버를 쓰지만 별도 DB이며 그 DB의 권한을 계약에 맞출 수 있음 | 서버 공유 여부는 장애 조건이 아니다. 실제 로그인/권한에 따라 선택한다. |
| 같은 DB의 `public` CREATE를 타 앱 때문에 유지해야 하지만, 별도 앱 스키마에서는 앱 로그인의 CREATE를 금지할 수 있음 | 두 제한 로그인을 준비하면 `restricted` 구성 가능. `single_account`는 앱 스키마 외에 `public` CREATE도 금지하므로 이 조건에서는 불가하다. |
| 앱 테이블도 `public`에 있고 앱 로그인의 유효 CREATE를 제거할 수 없음 | 두 모드 모두 부적합하다. DBA가 별도 스키마/DB 등 배치를 검토한다. |
| 로그인이나 상속 역할이 관리자/테이블 소유자이거나 TRUNCATE/REFERENCES/TRIGGER를 가짐 | 두 모드 모두 부적합하다. 모드 변경으로 검사를 우회하지 않는다. |

`PUBLIC`은 모든 로그인에 적용되는 권한 대상이고 `public`은 현재 DB의 스키마 이름이다. `PUBLIC`에서 CREATE를 회수하면 **같은 DB·같은 스키마를 사용하는 다른 앱**에 영향이 있을 수 있다. 개인 로그인에서 직접 CREATE만 회수해도 PUBLIC/상속으로 남아 있으면 유효 권한은 사라지지 않는다. DBA가 영향과 필요한 재부여를 검토하며 이 문서는 일괄 REVOKE/GRANT를 지시하지 않는다.

`restricted`는 두 역할의 존재와 자기 역할만의 소속이 필요하다. `single_account`는 그 두 역할의 존재·소속을 요구하지 않지만, 앱 스키마와 `public` 모두에서 유효 CREATE가 없어야 한다. DELETE는 단일 모드에서 보유를 허용할 뿐 필수가 아니다. 나머지 테이블/컬럼 권한은 [인계 3절](CONNECTION_HANDOFF.md#3-네-테이블과-유효-권한)과 대조한다. `003_grants.sql`은 제한 계정용이며 단일 모드에 실행할 파일이 아니다.

## 2. 오류를 좁히는 순서

각 서버의 승인된 **기존 읽기 전용 연결**에서 아래 순서로 확인한다. 조회 결과의 DB/로그인/스키마 이름은 사내에만 보관한다. 오류가 이미 발생했다면 기존 중단·비식별 문의 절차를 유지하며, 내부 AI가 SQL 적용·권한 변경·같은 실패 요청의 반복 실행으로 복구하지 않는다.

1. 프로세스 환경과 root `.env`의 설정 키·선택 모드를 확인한다. side DSN이 있으면 필드별 설정보다 우선한다. 비밀번호/DSN을 출력하지 않는다.
2. 연결과 DB/로그인·버전·인코딩을 확인한다. 연결 자체가 실패하면 플랫폼 담당자가 DNS/포트/로그인/TLS CA·호스트명을 점검한다. TLS 검증 수준을 낮추지 않는다.

   ```sql
   SELECT current_database(), current_user,
          current_setting('server_version_num') AS server_version_num,
          current_setting('server_encoding') AS server_encoding;
   ```

3. 앱 스키마의 존재와 USAGE/CREATE, 로그인 자체의 관리자 속성을 확인한다. 아래 `sanity_app`은 실제 스키마의 사내 예시로 바꾼다. 스키마가 없을 때도 이 조회는 결과로 구분한다.

   ```sql
   SELECT n.oid IS NOT NULL AS schema_exists,
          has_schema_privilege(current_user, n.oid, 'USAGE') AS can_use,
          has_schema_privilege(current_user, n.oid, 'CREATE') AS can_create
   FROM (VALUES ('sanity_app')) AS expected(name)
   LEFT JOIN pg_namespace n ON n.nspname = expected.name;

   SELECT rolsuper, rolcreaterole, rolbypassrls
   FROM pg_roles WHERE rolname = current_user;
   ```

   USAGE는 필요하고 앱 스키마 CREATE는 금지다. 세 관리자 속성은 false여야 한다. 관리자/테이블 소유자 역할의 소속 검사도 앱의 최종 check가 수행한다. 단일 모드에서는 위 스키마 조회의 이름을 `public`으로 바꿔 CREATE가 없는지도 확인한다.

4. `restricted`일 때만 두 역할의 **존재부터** 확인하고 소속을 읽는다. 없는 역할 이름을 바로 `pg_has_role`에 넘기지 않도록 OID와 CASE를 사용한다. VWP는 own=VWP/other=OA, OA는 반대다. 단일 모드에서는 역할이 없어도 이 단계의 설치 누락으로 판단하지 않는다.

   ```sql
   SELECT expected.name, r.oid IS NOT NULL AS role_exists,
          CASE WHEN r.oid IS NOT NULL
               THEN pg_has_role(current_user, r.oid, 'MEMBER') END AS is_member
   FROM (VALUES ('sanity_vwp_app'), ('sanity_oa_app')) AS expected(name)
   LEFT JOIN pg_roles r ON r.rolname = expected.name;
   ```

5. 같은 스키마의 네 테이블과 실제 소유자를 확인한다. 다음 결과는 4행이고 모두 `table_exists=true`여야 한다. owner/owner 역할 소속은 앱 로그인으로 사용하지 않는다.

   ```sql
   SELECT expected.name, c.oid IS NOT NULL AS table_exists,
          pg_get_userbyid(c.relowner) AS table_owner
   FROM (VALUES ('sanity_vwp_data'), ('sanity_oa_data'),
                ('sanity_vwp_to_oa'), ('sanity_oa_to_vwp')) AS expected(name)
   LEFT JOIN pg_namespace n ON n.nspname = 'sanity_app'
   LEFT JOIN pg_class c ON c.relnamespace = n.oid
                       AND c.relname = expected.name AND c.relkind = 'r';
   ```

6. DBA가 선택한 모드의 테이블·컬럼별 **유효** GRANT를 인계 3절과 대조한다. 테이블 SELECT 하나나 로그인에 직접 준 GRANT만 확인해서는 부족하다. 다른 역할/PUBLIC에서 온 권한도 포함한다. 기본 UNIQUE는 이름 외에 키·순서·부분 조건·유효 상태까지 원본 `002/004`와 대조하고, 기능별 CHECK/FK/인덱스와 `010`은 적용 기록 및 4절을 확인한다.
7. 준비가 확인되면 각 서버에서 인계 문서의 `python -m common.sanity_db check --side vwp` 또는 `--side oa`를 실행한다. 읽기 전용 check는 앱 초기화의 공통 권한/기본 UNIQUE 계약 검사다. **성공만으로 010이나 모든 기능별 migration의 적용이 확인되는 것은 아니다.** 원본 DB 보관은 4절의 별도 검사까지 필요하다.

위는 진단을 위한 순서다. 실제 `Database` 초기화 순서는 접속/세션 설정 → 관리자·앱 스키마 CREATE → 모드별 역할/추가 CREATE → 네 테이블·소유·ACL → 기본 UNIQUE 검사다. 사람이 읽기 쉽게 정한 진단 순서와 첫 실패 위치를 혼동하지 않는다.

| 고정 오류 코드 | 확인할 범위 |
|---|---|
| `DATABASE_NOT_CONFIGURED` | 필수 필드 미설정. side DSN 사용 여부와 필드 설정 키 존재를 확인한다. |
| `DATABASE_CONFIG` | 잘못된 side·모드 값·포트·스키마·CA 경로 등 설정 형식. |
| `DATABASE_DRIVER_MISSING` | 해당 실행 환경의 psycopg2 설치 여부. |
| `DBAAS_SQL_CONTRACT_REQUIRED` | HTTP DBaaS는 현재 PostgreSQL 저장 연결을 대신하지 못한다. |
| `DATABASE_UNAVAILABLE` | 접속 실패 **또는 초기화/계약 검사 중 psycopg2 오류**. 인증/망/TLS뿐 아니라 없는 스키마·없는 앱 역할·catalog 조회 오류도 후보다. 이 코드만으로 네트워크 장애라고 확정하지 않는다. |
| `DATABASE_PRIVILEGES` | 관리/소유/CREATE·역할 소속·과다/부족한 유효 테이블·컬럼 권한 등 계약 불일치. |
| `DATABASE_SCHEMA` | 네 테이블 또는 공통 필수 UNIQUE 정의 누락/불일치. |
| `RAW_SOURCE_SCHEMA_REQUIRED` | 원본 보관의 v4 CHECK 또는 raw 부분 UNIQUE 계약 미준비. 아래 010 검사를 따른다. |
| `DATABASE_ERROR` | 연결 후 실제 DB 연산의 PostgreSQL 오류. 제약·권한·잠금/트랜잭션 등 발생 단계와 승인된 진단 결과로 좁힌다. |

특히 `restricted`에서 역할이 없으면 `pg_has_role(current_user, '없는 역할', 'MEMBER')` 자체가 DB 오류가 되고 현재 초기화 코드는 이를 `DATABASE_UNAVAILABLE`로 반환한다. 이것은 **현재 오류 노출 방식의 설명**이며 이번 문서 보완에서 코드/예외 처리/보호 검사를 변경하지 않는다. 외부 문의에는 driver 예외 원문 대신 코드·실패 단계·역할 존재 여부만 전달한다.

## 3. public 이외 스키마에 원본 SQL 대응

앱은 `SANITY_DB_SCHEMA`로 지정한 동일 스키마의 네 테이블을 사용한다. 원본 `002`~`010`의 테이블 참조는 `public`이므로 **실행 사본의 모든 `public.`**과 `003`의 **`GRANT USAGE ON SCHEMA public`**을 함께 대응한다. `010`의 DO 안 `::regclass` 문자열과 동적 ALTER도 포함한다. 테이블·제약·인덱스·앱 역할 이름과 JSON 값은 바꾸지 않는다. `search_path`나 `.env`만 변경해 원본 DDL의 대상을 바꿀 수는 없다.

다음은 단순한 소문자 ASCII 스키마 이름(`[a-z_][a-z0-9_]{0,62}`)용 **오프라인 sed 사본 생성**이다. `/private`는 담당자가 준비한 비공개 디렉터리로 바꾼다. Bash에서 저장소 루트를 기준으로 실행한다. 원본 파일과 포털 계획의 hash는 보존한다.

이 sed 절차는 사용자/DBA의 배포 사본 준비용이다. 사내 AI에게 원본 SQL·실행본의 임의 편집이나 공통 설정 계약 변경 권한을 추가하지 않는다. 사내 AI의 포털 `inspect/prepare`와 결과 인계는 기존 가이드에 허용된 범위를 따른다.

```bash
set -eu
umask 077
bridge_schema='sanity_app'
python3 - "$bridge_schema" <<'PY'
import re
import sys
if not re.fullmatch(r'[a-z_][a-z0-9_]{0,62}', sys.argv[1], flags=re.ASCII) or sys.argv[1] == 'public':
    raise SystemExit('Use a simple non-public schema name; otherwise request a quoted DBA rendering')
PY
bridge_sql_dir="$(mktemp -d /private/sanity-db-sql.XXXXXX)"
for bridge_sql_source in db/00[2-9]_*.sql db/010_raw_sources.sql; do
  sed -e "s/public[.]/${bridge_schema}./g" \
      -e "s/GRANT USAGE ON SCHEMA public /GRANT USAGE ON SCHEMA ${bridge_schema} /g" \
      "$bridge_sql_source" > "$bridge_sql_dir/$(basename "$bridge_sql_source")"
done
if rg -n 'public[.]|GRANT USAGE ON SCHEMA public\b' "$bridge_sql_dir"; then
  printf '%s\n' 'Unexpected public reference remains; stop and inspect the generated copies.' >&2
  exit 1
fi
```

이 생성물 전체를 처음부터 실행하라는 뜻이 아니다. **적용되지 않은 단계만** DBA가 원본과 diff/트랜잭션/잠금 조건을 검토해 적용한다. 단일 모드의 `003`은 제외하고 성공한 SQL은 반복하지 않는다. 대문자·공백·따옴표 등이 있는 스키마를 이 sed 예제에 넣거나 이름을 임의로 단순화하지 않는다. DBA의 올바른 identifier/literal 인용이 필요하다.

네 테이블이 비어 있는 포털 경로는 sed 사본 대신 기존 [포털 도구](portal/README.md)의 `inspect --schema 실제이름`과 `prepare --raw-sources`를 사용한다. 009 완료 checkpoint에서는 56~59만 생성한다. 포털 경로의 0행·쓰기 중지·전후 catalog 검증 조건은 유지한다. 데이터가 있는 DB의 원본 `010` 전체 트랜잭션 경로와 포털 문장을 섞어 재시도하지 않는다.

`SANITY_DB_SCHEMA`는 양쪽 서버에서 실제 DDL 대상과 같게 지정한다. 단일 모드가 검사하는 **현재 DB의 `public` CREATE 금지**는 별도 조건이므로 공통 Python 코드의 `public` 문자열을 치환하지 않는다.

## 4. 010 적용의 확인 기준

아래는 **010 단독 상태**의 확인 기준이다. 012를 적용한 정상 DB는 kind/status가 `sanity_vwp_kind_v5`·`sanity_vwp_status_v5`로 확장되고 나머지 원본 보호 v4는 유지된다. 이 경우 v4 kind/status 두 이름이 없다고 010을 재실행하지 않는다. 현재 `RawSourceStore`는 완전한 010 또는 012 이후의 호환 계약을 검사한다. 011~013의 저장/참조·양쪽 reader 도입과 전체 용량 확인은 [전체 저장 안내](TOTAL_STORAGE.md)를 함께 따른다.

**010 적용 = 새 테이블 생성이 아니라 기존 `sanity_vwp_data`의 v4 CHECK 6개와 인덱스 3개 추가/교체로 확인한다.** 기존 네 테이블·역할·GRANT를 유지한다. 앞 단계는 009이며 성공한 010은 재실행하지 않는다.

승인된 읽기 전용 연결에서 아래 실제 정의를 사내에 보관하고 [원본 010](010_raw_sources.sql)과 대조한다. `sanity_app`을 실제 스키마로 바꾼다. CHECK는 아래 이름 6개 모두 `convalidated=true`, `connoinherit=false`여야 한다.

```sql
SELECT c.conname, c.convalidated, c.connoinherit,
       pg_get_constraintdef(c.oid, true) AS definition
FROM pg_constraint c
JOIN pg_class t ON t.oid = c.conrelid
JOIN pg_namespace n ON n.oid = t.relnamespace
WHERE n.nspname = 'sanity_app' AND t.relname = 'sanity_vwp_data'
  AND c.contype = 'c' AND c.conname IN (
    'sanity_vwp_kind_v4', 'sanity_vwp_status_v4',
    'sanity_vwp_submission_parent_v4', 'sanity_vwp_raw_scope_v4',
    'sanity_vwp_raw_immutable_v4', 'sanity_vwp_raw_payload_v4')
ORDER BY c.conname;

SELECT x.relname, i.indisunique, i.indisvalid, i.indisready,
       i.indislive, i.indimmediate, pg_get_indexdef(x.oid) AS definition
FROM pg_index i
JOIN pg_class t ON t.oid = i.indrelid
JOIN pg_namespace n ON n.oid = t.relnamespace
JOIN pg_class x ON x.oid = i.indexrelid
WHERE n.nspname = 'sanity_app' AND t.relname = 'sanity_vwp_data'
  AND x.relname IN ('sanity_vwp_raw_key_uq',
                   'sanity_vwp_raw_file_idx', 'sanity_vwp_raw_pending_idx')
ORDER BY x.relname;
```

표준 인덱스는 `raw_key_uq` 한 개가 UNIQUE이고 나머지 두 개는 일반 인덱스다. 키·표현식·부분 조건과 valid/ready/live/immediate를 함께 확인한다. DBA가 인덱스 이름을 다르게 등록했다면 이름만으로 누락을 확정하지 않고 실제 정의를 대조한다. 위 조회의 6/3행 존재만으로 다른 정의를 승인하지 않는다.

추가로 VWP 앱의 **읽기 전용 원본 저장 계약 검사**를 실행할 수 있다. 원문·행 목록을 읽거나 DB에 쓰지 않는다.

```bash
.venv/bin/python - <<'PY'
import sys
from common.sanity_common import ReviewError
from common.sanity_db import Database
from common.sanity_raw_sources import require_raw_schema
try:
    with Database('vwp') as db:
        require_raw_schema(db)
except ReviewError as exc:
    print('code=' + exc.code, file=sys.stderr)
    raise SystemExit(2)
print('status=RAW_SOURCE_SCHEMA_VERIFIED side=vwp')
PY
```

이 함수는 검증된 v4 CHECK 6개의 존재와 raw 부분 UNIQUE의 실제 키/조건/유효 상태를 검사한다. 일반 인덱스 두 개는 위 DBA catalog 조회로 별도 확인한다. 원본 무결성·복구·성능·실제 송수신·OA OWNER 범위 확정·AI 판단 품질은 이 설치 검사의 성공과 별도다.
