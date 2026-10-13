"""DBaaS HTTPS access from dbaccess.md. No assumed SQL/transaction wire contract."""
from __future__ import annotations

import argparse
from dataclasses import dataclass, field
import json
import logging
from pathlib import Path
import re
import ssl
import sys
from urllib.parse import urlsplit

from sanity_common import ReviewError, canonical_json, _object_pairs, _constant, _finite
from sanity_config import load_environment

try:
    import httpx
except ImportError:
    httpx = None

MAX_HTTP_BYTES = 8 * 1024 * 1024
TIMEOUT_SECONDS = 30


@dataclass(frozen=True)
class DbaasSettings:
    base_url: str = field(repr=False)
    token: str = field(repr=False)
    ca_path: str | None = field(default=None, repr=False)

    @classmethod
    def from_environment(cls, settings=None):
        values = load_environment() if settings is None else settings
        # The document spells the variable both ways. Prefer its .env example's DBAAS name.
        primary, alias = values.get("DBAAS_BASE_URL"), values.get("DBASS_BASE_URL")
        if primary and alias and primary.rstrip("/") != alias.rstrip("/"):
            raise ReviewError("DBAAS_CONFIG", "Conflicting DBAAS_BASE_URL and DBASS_BASE_URL")
        return cls(primary or alias or "", values.get("DBAAS_TOKEN") or "", values.get("DBAAS_CA_PATH") or None)

    def __post_init__(self):
        if not self.base_url or not self.token:
            raise ReviewError("DBAAS_NOT_CONFIGURED", "Set DBAAS_BASE_URL and DBAAS_TOKEN locally")
        try:
            url = urlsplit(self.base_url)
            valid = (url.scheme == "https" and url.hostname and url.port != 0
                     and not url.username and not url.password and not url.query and not url.fragment
                     and not any(ch.isspace() or ord(ch) < 32 for ch in self.base_url)
                     and all(part not in (".", "..") for part in url.path.split("/")))
        except ValueError:
            valid = False
        if not valid:
            raise ReviewError("DBAAS_CONFIG", "DBAAS_BASE_URL must be an HTTPS server URL without embedded credentials/query/fragment")
        # Bearer credentials cannot contain whitespace or header-control characters.
        if not self.token.isascii() or any(ch.isspace() or ord(ch) < 33 or ord(ch) == 127 for ch in self.token):
            raise ReviewError("DBAAS_CONFIG", "Invalid bearer token format")
        if self.ca_path is not None and not Path(self.ca_path).is_absolute():
            raise ReviewError("DBAAS_CA_ERROR", "DBAAS_CA_PATH must be an absolute certificate path")

    def tls_context(self):
        try:
            if self.ca_path is None:
                # System CA trust with hostname and certificate verification enabled.
                return ssl.create_default_context()
            path = Path(self.ca_path)
            if not path.is_file() or not 0 < path.stat().st_size <= 1024 * 1024:
                raise ValueError()
            certificate = path.read_bytes()
            context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
            # .cer may be PEM or binary DER; support both without disabling verification.
            context.load_verify_locations(cadata=certificate.decode("ascii")
                if b"-----BEGIN CERTIFICATE-----" in certificate else certificate)
            return context
        except (OSError, ValueError, UnicodeError):
            raise ReviewError("DBAAS_CA_ERROR", "Cannot load the configured trusted CA certificate") from None


class DbaasClient:
    def __init__(self, settings=None, *, transport=None):
        if httpx is None:
            raise ReviewError("DATABASE_DRIVER_MISSING", "Install requirements.txt for DBaaS HTTP access")
        config = settings or DbaasSettings.from_environment()
        context = config.tls_context()
        # HTTPX INFO includes full URLs; httpcore DEBUG includes hosts/TLS details.
        # This application must never emit these connection values, even with root DEBUG enabled.
        for name in ("httpx", "httpcore"):
            logging.getLogger(name).setLevel(logging.CRITICAL + 1)
        for name, logger in list(logging.Logger.manager.loggerDict.items()):
            if name.startswith(("httpx.", "httpcore.")) and isinstance(logger, logging.Logger):
                logger.setLevel(logging.CRITICAL + 1)
        base = config.base_url.rstrip("/")
        self._api_url = base if base.endswith("/api/v1") else base + "/api/v1"
        self._client = httpx.Client(verify=context, trust_env=False, timeout=TIMEOUT_SECONDS,
            follow_redirects=False, transport=transport,
            headers={"Authorization": "Bearer " + config.token, "Accept": "application/json",
                     "Accept-Encoding": "identity"})

    def _request(self, method, suffix, body=None):
        query = method == "POST"
        content = canonical_json(body) if query else None
        if content is not None and len(content) > MAX_HTTP_BYTES:
            raise ReviewError("DBAAS_REQUEST_TOO_LARGE", "DBaaS request exceeds the client byte limit")
        try:
            with self._client.stream(method, self._api_url + suffix, content=content,
                    headers={"Content-Type": "application/json"} if query else None) as response:
                status = response.status_code
                if status in (401, 403, 404):
                    code = {401: "DBAAS_UNAUTHORIZED", 403: "DBAAS_FORBIDDEN", 404: "DBAAS_NOT_FOUND"}[status]
                    raise ReviewError(code, "DBaaS authentication, permission or endpoint check failed")
                if 300 <= status < 400:
                    raise ReviewError("DBAAS_REDIRECT_REFUSED", "DBaaS redirects are not followed")
                if not 200 <= status < 300:
                    raise ReviewError("DBAAS_QUERY_UNCERTAIN" if query else "DBAAS_HTTP_ERROR",
                                      "DBaaS request failed; the server response body is not logged")
                media_type = response.headers.get("content-type", "").split(";", 1)[0].strip().lower()
                if media_type != "application/json" and not media_type.endswith("+json"):
                    raise ReviewError("DBAAS_INVALID_RESPONSE", "Expected a JSON API response")
                if response.headers.get("content-encoding", "identity").lower() not in ("", "identity"):
                    raise ReviewError("DBAAS_INVALID_RESPONSE", "Unexpected HTTP content encoding")
                chunks, size = [], 0
                for chunk in response.iter_bytes(chunk_size=65536):
                    size += len(chunk)
                    if size > MAX_HTTP_BYTES:
                        raise ReviewError("DBAAS_RESPONSE_TOO_LARGE", "DBaaS response exceeds the client byte limit")
                    chunks.append(chunk)
                try:
                    data = json.loads(b"".join(chunks).decode("utf-8"), object_pairs_hook=_object_pairs,
                                      parse_constant=_constant, parse_float=_finite)
                    if not isinstance(data, (dict, list)):
                        raise ValueError()
                    return data
                except (ValueError, UnicodeError, RecursionError, ReviewError):
                    raise ReviewError("DBAAS_INVALID_RESPONSE", "Cannot decode a valid JSON API response") from None
        except httpx.TimeoutException:
            raise ReviewError("DBAAS_QUERY_UNCERTAIN" if query else "DBAAS_TIMEOUT",
                              "DBaaS request timed out; no automatic retry was performed") from None
        except httpx.HTTPError:
            raise ReviewError("DBAAS_QUERY_UNCERTAIN" if query else "DBAAS_CONNECTION_ERROR",
                              "DBaaS connection failed; no automatic retry was performed") from None
        except ReviewError as exc:
            # Even a 2xx reply with unreadable data cannot prove a query's commit outcome.
            if query and exc.code in ("DBAAS_INVALID_RESPONSE", "DBAAS_RESPONSE_TOO_LARGE"):
                raise ReviewError("DBAAS_QUERY_UNCERTAIN", "Query response could not be verified; do not retry writes blindly") from None
            raise

    def me(self):
        # dbaccess.md says all endpoints use /api/v1; its /api/vi/me spelling is treated as a typo.
        return self._request("GET", "/me")

    def resources(self):
        return self._request("GET", "/resources")

    def query(self, resource_id, request_body):
        """Send an explicitly supplied API body, without inventing query/params/response fields.

        This is a low-level transport, not the bridge's transactional storage adapter.
        No retry and no implicit BEGIN/COMMIT across independent HTTP requests.
        """
        if not isinstance(resource_id, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}", resource_id):
            raise ReviewError("DBAAS_CONFIG", "Invalid resource identifier")
        if not isinstance(request_body, dict) or not request_body:
            raise ReviewError("DBAAS_CONFIG", "An explicit nonempty API request object is required")
        return self._request("POST", "/resources/" + resource_id + "/query", request_body)

    def close(self):
        self._client.close()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        self.close()


def main(argv=None):
    parser = argparse.ArgumentParser(description="DBaaS read-only access check; never prints connection values or API bodies")
    parser.add_argument("command", choices=("check",))
    parser.add_argument("--resources", action="store_true", help="also check resource-list access without printing its contents")
    args = parser.parse_args(argv)
    try:
        with DbaasClient() as client:
            client.me()
            if args.resources:
                client.resources()
        print("status=CONNECTED backend=DBAAS_HTTP checks=" + ("me,resources" if args.resources else "me"))
        return 0
    except ReviewError as exc:
        print("code=" + exc.code, file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
