"""Synthetic connection settings; never contact the internal DB or read real .env."""
from contextlib import redirect_stderr, redirect_stdout
import io
import os
from pathlib import Path
import tempfile
import traceback
import unittest
from unittest.mock import MagicMock, patch

from common.sanity_common import ReviewError
from common.sanity_config import access_mode, database_privilege_mode, load_environment, postgres_parameters
import common.sanity_db as sanity_db
from common.sanity_privacy import MASK, SecretGuard


class ConnectionConfigTests(unittest.TestCase):
    def setUp(self):
        self.settings = {"SANITY_DB_HOST": "db.synthetic.invalid", "SANITY_DB_NAME": "fixture",
                         "SANITY_DB_USER_NAME": "fixture_user", "SANITY_DB_PASSWORD": "a 'quote' \\ ${x} @/:="}

    def test_default_is_postgresql_but_http_requires_explicit_selection(self):
        self.assertEqual("postgresql", access_mode({}))
        self.assertEqual("dbaas_http", access_mode({"SANITY_DB_ACCESS": "dbaas_http"}))
        with self.assertRaises(ReviewError):
            access_mode({"SANITY_DB_ACCESS": "sqlite"})

    def test_single_account_requires_exact_explicit_true(self):
        self.assertEqual("restricted", database_privilege_mode({}))
        for value, expected in (("false", "restricted"), ("true", "single_account")):
            with self.subTest(value=value):
                self.assertEqual(expected, database_privilege_mode({"SANITY_DB_ALLOW_SINGLE_ACCOUNT": value}))
        for value in (None, "", "yes", "1", "TRUE", " true ", True, False):
            with self.subTest(value=value), self.assertRaises(ReviewError) as error:
                database_privilege_mode({"SANITY_DB_ALLOW_SINGLE_ACCOUNT": value})
            self.assertEqual("DATABASE_CONFIG", error.exception.code)

    def test_fields_are_separate_kwargs_and_password_is_not_rewritten(self):
        params = postgres_parameters(self.settings, "vwp")
        self.assertEqual({"host": "db.synthetic.invalid", "dbname": "fixture", "user": "fixture_user",
                          "password": self.settings["SANITY_DB_PASSWORD"], "port": 5432,
                          "sslmode": "verify-full"}, params)
        self.assertEqual(params, postgres_parameters(self.settings, "oa"))

    def test_local_env_is_allowlisted_without_interpolation_and_exported_values_win(self):
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, {}, clear=True):
            path = Path(tmp) / ".env"
            from common.sanity_common import atomic_text
            atomic_text(path, 'SANITY_DB_HOST=file.invalid\nSANITY_DB_PASSWORD="literal ${TOKEN}"\n'
                        'SANITY_DB_PORT=6543\nSANITY_DB_SSLROOTCERT=/synthetic/root.pem\n'
                        'SANITY_DB_ALLOW_SINGLE_ACCOUNT=true\nUNRELATED=no\n')
            with patch.dict(os.environ, {"SANITY_DB_HOST": "export.invalid"}):
                values = load_environment(path)
            self.assertEqual("export.invalid", values["SANITY_DB_HOST"])
            self.assertEqual("literal ${TOKEN}", values["SANITY_DB_PASSWORD"])
            self.assertEqual("6543", values["SANITY_DB_PORT"])
            self.assertEqual("single_account", database_privilege_mode(values))
            with patch.dict(os.environ, {"SANITY_DB_ALLOW_SINGLE_ACCOUNT": "false"}):
                self.assertEqual("restricted", database_privilege_mode(load_environment(path)))
            self.assertNotIn("UNRELATED", values)
            self.assertNotIn("SANITY_DB_PASSWORD", os.environ)

    def test_each_required_field_is_required_without_libpq_implicit_defaults(self):
        for key in self.settings:
            with self.subTest(key=key), self.assertRaises(ReviewError) as err:
                postgres_parameters({**self.settings, key: ""}, "vwp")
            self.assertEqual("DATABASE_NOT_CONFIGURED", err.exception.code)

    def test_invalid_port_tls_and_nul_are_rejected_without_values_in_errors(self):
        invalid = [("SANITY_DB_PORT", value) for value in ("", "0", "65536", "5,6", "not-a-port", "１２", "9" * 5000)]
        invalid += [("SANITY_DB_SSLMODE", "anything"), ("SANITY_DB_SSLROOTCERT", "relative.pem"),
                    ("SANITY_DB_PASSWORD", "secret\x00value")]
        for key, value in invalid:
            with self.subTest(key=key), self.assertRaises(ReviewError) as err:
                postgres_parameters({**self.settings, key: value}, "vwp")
            self.assertEqual("DATABASE_CONFIG", err.exception.code)

    def test_explicit_side_dsn_is_authoritative_and_other_side_is_not_reused(self):
        settings = {**self.settings, "SANITY_VWP_DSN": "service=fixture-vwp"}
        self.assertEqual({"dsn": "service=fixture-vwp"}, postgres_parameters(settings, "vwp"))
        self.assertEqual("fixture_user", postgres_parameters(settings, "oa")["user"])
        self.assertEqual("fixture_user", postgres_parameters({**settings, "SANITY_VWP_DSN": ""}, "vwp")["user"])

    def test_database_passes_kwargs_to_psycopg2_adapter(self):
        connection = MagicMock()
        with patch.object(sanity_db, "load_environment", return_value=self.settings), \
                patch.object(sanity_db, "connect", return_value=connection) as connect, \
                patch.object(sanity_db.Database, "_check_contract") as contract:
            with sanity_db.Database("oa"):
                pass
        kwargs = connect.call_args.kwargs
        self.assertEqual(self.settings["SANITY_DB_PASSWORD"], kwargs["password"])
        self.assertEqual("sanity-oa-bridge", kwargs["application_name"])
        self.assertEqual(10, kwargs["connect_timeout"])
        contract.assert_called_once()
        connection.close.assert_called_once()

    def test_failure_is_not_retried_and_driver_error_is_not_exposed(self):
        import psycopg2
        with patch.object(sanity_db, "load_environment", return_value=self.settings), \
                patch.object(sanity_db, "connect", side_effect=psycopg2.OperationalError("private-server-detail")) as connect:
            try:
                sanity_db.Database("vwp")
            except ReviewError as exc:
                self.assertEqual("DATABASE_UNAVAILABLE", exc.code)
                self.assertNotIn("private-server-detail", traceback.format_exc())
            else:
                self.fail("Expected connection failure")
        connect.assert_called_once()

    def test_invalid_single_account_setting_is_rejected_before_connecting(self):
        with patch.object(sanity_db, "load_environment", return_value={
                **self.settings, "SANITY_DB_ALLOW_SINGLE_ACCOUNT": "yes"}), \
                patch.object(sanity_db, "connect") as connect, self.assertRaises(ReviewError) as error:
            sanity_db.Database("oa")
        self.assertEqual("DATABASE_CONFIG", error.exception.code)
        connect.assert_not_called()

    def test_contract_failure_closes_connection_without_retrying_or_changing_mode(self):
        for value, expected in (("false", "restricted"), ("true", "single_account")):
            with self.subTest(value=value):
                connection = MagicMock()
                checked_modes = []
                def reject(db):
                    checked_modes.append(db.privilege_mode)
                    raise ReviewError("DATABASE_PRIVILEGES", "Synthetic contract failure")
                with patch.object(sanity_db, "load_environment", return_value={
                        **self.settings, "SANITY_DB_ALLOW_SINGLE_ACCOUNT": value}), \
                        patch.object(sanity_db, "connect", return_value=connection) as connect, \
                        patch.object(sanity_db.Database, "_check_contract", reject), self.assertRaises(ReviewError) as error:
                    sanity_db.Database("oa")
                self.assertEqual("DATABASE_PRIVILEGES", error.exception.code)
                self.assertEqual([expected], checked_modes)
                connect.assert_called_once()
                connection.close.assert_called_once()

    def test_configured_password_is_redacted_even_without_a_password_label(self):
        self.assertEqual("prefix " + MASK, SecretGuard(self.settings).text("prefix " + self.settings["SANITY_DB_PASSWORD"]))

    def test_check_command_only_opens_contract_and_prints_no_configuration(self):
        output, errors = io.StringIO(), io.StringIO()
        with patch.object(sanity_db, "Database") as db, redirect_stdout(output), redirect_stderr(errors):
            self.assertEqual(0, sanity_db.main(["check", "--side", "oa"]))
        db.assert_called_once_with("oa")
        self.assertIn("driver=psycopg2 side=oa", output.getvalue())
        self.assertEqual("", errors.getvalue())
        with patch.object(sanity_db, "Database", side_effect=ReviewError("DATABASE_CONFIG", "secret")), \
                redirect_stdout(output), redirect_stderr(errors):
            self.assertEqual(2, sanity_db.main(["check", "--side", "vwp"]))
        self.assertEqual("code=DATABASE_CONFIG\n", errors.getvalue())

    def test_check_command_identifies_the_checked_mode_and_database_isolation(self):
        for mode, isolation in (("restricted", "enforced"), ("single_account", "not_enforced")):
            with self.subTest(mode=mode):
                output = io.StringIO()
                with patch.object(sanity_db, "Database") as db, redirect_stdout(output):
                    db.return_value.__enter__.return_value.privilege_mode = mode
                    self.assertEqual(0, sanity_db.main(["check", "--side", "vwp"]))
                self.assertEqual("status=CONNECTED backend=POSTGRESQL driver=psycopg2 side=vwp "
                                 f"checks=schema,permissions privilege_mode={mode} db_isolation={isolation}\n",
                                 output.getvalue())
