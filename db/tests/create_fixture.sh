#!/usr/bin/env bash
# 전용 빈 테스트 DB에만 사용. 4개 테이블을 실제 생성하며 삭제하지 않는다.
# 사용: SANITY_PSQL=/path/to/psql bash db/tests/create_fixture.sh -h HOST -U OWNER -d EMPTY_TEST_DB
# 두 앱 역할은 DBA가 사전 생성한다. 기존 테이블이 있으면 CREATE에서 중단한다.
set -euo pipefail
sanity_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
"${SANITY_PSQL:-psql}" -X -v ON_ERROR_STOP=1 "$@" -c "DO \$\$ BEGIN
    IF current_database() NOT LIKE 'sanity_test_%' OR EXISTS (
        SELECT 1 FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
        WHERE n.nspname = 'public' AND c.relkind IN ('r', 'p', 'v', 'm', 'f')) THEN
        RAISE EXCEPTION 'Use an empty, dedicated sanity_test_* database';
    END IF;
END \$\$;"
# GUI가 컬럼 정의를 감싸는 동작을 테스트 DB에서만 모사한다.
{
    printf 'BEGIN;\n'
    for sanity_table in sanity_vwp_data sanity_oa_data sanity_vwp_to_oa sanity_oa_to_vwp; do
        printf 'CREATE TABLE public.%s (\n' "$sanity_table"
        cat "$sanity_dir/form/$sanity_table.columns.sql"
        printf '\n);\n'
    done
    printf 'COMMIT;\n'
} | "${SANITY_PSQL:-psql}" -X -v ON_ERROR_STOP=1 "$@"
"${SANITY_PSQL:-psql}" -X -v ON_ERROR_STOP=1 "$@" -f "$sanity_dir/002_indexes.sql"
"${SANITY_PSQL:-psql}" -X -v ON_ERROR_STOP=1 "$@" -f "$sanity_dir/003_grants.sql"
"${SANITY_PSQL:-psql}" -X -v ON_ERROR_STOP=1 "$@" -f "$sanity_dir/004_clustering.sql"
"${SANITY_PSQL:-psql}" -X -v ON_ERROR_STOP=1 "$@" -f "$sanity_dir/tests/smoke.sql"
