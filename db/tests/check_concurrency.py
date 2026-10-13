"""전용 sanity_test_* DB에서 서로 다른 두 psql 세션의 SKIP LOCKED 점유 검사."""
import os
import select
import subprocess
import sys
import uuid


PSQL = [os.environ.get("SANITY_PSQL", "psql"), "-X", "-q", "-A", "-t", "-v", "ON_ERROR_STOP=1", *sys.argv[1:]]


def run(sql):
    result = subprocess.run(PSQL, input=sql, text=True, capture_output=True, timeout=15)
    if result.returncode:
        raise AssertionError(result.stderr)
    return result.stdout.strip()


def main():
    if run("SELECT current_database() LIKE 'sanity_test_%';") != "t":
        raise SystemExit("Use a dedicated sanity_test_* database")
    tag = uuid.uuid4().hex
    first, second = f"concurrency-{tag}-1", f"concurrency-{tag}-2"
    ids = f"('{first}', '{second}')"
    worker = None
    try:
        run(f"""SET ROLE sanity_vwp_app;
            INSERT INTO public.sanity_vwp_to_oa(message_id,project,run_id)
            VALUES ('{first}','synthetic_concurrency','test'),('{second}','synthetic_concurrency','test');""")
        worker = subprocess.Popen(PSQL, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        worker.stdin.write(f"""BEGIN;
            SET LOCAL ROLE sanity_oa_app;
            SELECT message_id FROM public.sanity_vwp_to_oa
            WHERE message_id IN {ids} AND status='READY' ORDER BY message_id FOR UPDATE SKIP LOCKED LIMIT 1;
            """)
        worker.stdin.flush()
        if not select.select([worker.stdout], [], [], 10)[0]:
            raise AssertionError("First worker did not acquire a row in time")
        claimed_a = worker.stdout.readline().strip()
        if claimed_a != first:
            raise AssertionError(f"Unexpected first claim: {claimed_a!r}")
        claimed_b = run(f"""BEGIN; SET LOCAL ROLE sanity_oa_app;
            WITH picked AS (
                SELECT message_id FROM public.sanity_vwp_to_oa
                WHERE message_id IN {ids} AND status='READY' ORDER BY message_id FOR UPDATE SKIP LOCKED LIMIT 1
            ) UPDATE public.sanity_vwp_to_oa m SET status='PROCESSING', attempt_count=1,
                lease_token='{uuid.uuid4()}', lease_owner='worker-b', lease_expires_at=clock_timestamp()+interval '1 minute'
              FROM picked WHERE m.message_id=picked.message_id RETURNING m.message_id;
            COMMIT;""")
        if claimed_b != second:
            raise AssertionError(f"Second worker failed to skip locked row: {claimed_b!r}")
        out, err = worker.communicate(f"""UPDATE public.sanity_vwp_to_oa SET status='PROCESSING', attempt_count=1,
            lease_token='{uuid.uuid4()}', lease_owner='worker-a', lease_expires_at=clock_timestamp()+interval '1 minute'
            WHERE message_id='{first}' AND status='READY' RETURNING message_id;
            COMMIT;""", timeout=15)
        if worker.returncode or out.strip() != first:
            raise AssertionError(f"First worker failed: {out}\n{err}")
        if run(f"SELECT count(*) FROM public.sanity_vwp_to_oa WHERE message_id IN {ids} AND status='PROCESSING';") != "2":
            raise AssertionError("Expected two distinct processing messages")
        print("PASS: two real sessions claim different rows while the first row remains locked")
    finally:
        if worker is not None and worker.poll() is None:
            worker.terminate()
            worker.communicate(timeout=10)
        # 이 검사에서 생성한 UUID 식별자의 합성 PING 두 개만 owner로 정리한다.
        run(f"DELETE FROM public.sanity_vwp_to_oa WHERE message_id IN {ids} AND project='synthetic_concurrency';")


if __name__ == "__main__":
    main()
