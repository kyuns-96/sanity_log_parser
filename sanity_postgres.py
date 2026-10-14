"""Small psycopg2 connection adapter used by storage and isolated DB tests."""
from contextlib import contextmanager
import uuid

import psycopg2
from psycopg2 import extensions, extras, sql


class Connection(extensions.connection):
    def execute(self, query, params=None):
        cursor = self.cursor()
        try:
            cursor.execute(query, params)
        except BaseException:
            cursor.close()
            raise
        return cursor

    @contextmanager
    def transaction(self):
        # Keep named cursors usable and never commit a caller's outer transaction.
        outer = (not getattr(self, "_transaction_depth", 0)
                 and self.get_transaction_status() == extensions.TRANSACTION_STATUS_IDLE)
        previous_autocommit = self.autocommit
        savepoint = sql.Identifier("sanity_" + uuid.uuid4().hex)
        if outer:
            self.autocommit = False
        else:
            self.execute(sql.SQL("SAVEPOINT {}").format(savepoint)).close()
        self._transaction_depth = getattr(self, "_transaction_depth", 0) + 1
        try:
            yield self
            if outer:
                self.commit()
            else:
                self.execute(sql.SQL("RELEASE SAVEPOINT {}").format(savepoint)).close()
        except BaseException:
            if not self.closed:
                if outer:
                    self.rollback()
                else:
                    self.execute(sql.SQL("ROLLBACK TO SAVEPOINT {}").format(savepoint)).close()
                    self.execute(sql.SQL("RELEASE SAVEPOINT {}").format(savepoint)).close()
            raise
        finally:
            self._transaction_depth -= 1
            if outer and not self.closed:
                self.autocommit = previous_autocommit


def connect(dsn="", *, autocommit=True, **kwargs):
    conn = psycopg2.connect(dsn, connection_factory=Connection, **kwargs)
    try:
        conn.autocommit = autocommit
        extras.register_uuid(conn_or_curs=conn)
        # psycopg2 returns memoryview for bytea by default. Storage hashes, gzip
        # validation and idempotence comparisons use immutable bytes throughout.
        def bytea(value, cursor):
            return None if value is None else bytes(psycopg2.BINARY(value, cursor))
        extensions.register_type(extensions.new_type(psycopg2.BINARY.values, "SANITY_BYTEA", bytea), conn)
        return conn
    except BaseException:
        conn.close()
        raise
