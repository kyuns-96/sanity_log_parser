"""Exact OA service hosts for deployment behind the company's ingress."""
from __future__ import annotations

import ipaddress
import re


DEFAULT_HOSTS = "127.0.0.1,localhost,[::1]"
HOST_LABEL = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?\Z")


def allowed_hosts(value: str | None = None) -> list[str]:
    """Reject patterns, URLs, ports and config fragments; retain local health."""
    if value is None:
        value = DEFAULT_HOSTS
    if not isinstance(value, str) or not value or len(value) > 4096:
        raise ValueError("CONTAINER_HOST_CONFIG")
    names = []
    for raw in value.split(","):
        host = raw.strip()
        if not host or len(host) > 253:
            raise ValueError("CONTAINER_HOST_CONFIG")
        try:
            address = ipaddress.ip_address(host)
        except ValueError:
            if host.startswith("[") and host.endswith("]"):
                try:
                    address = ipaddress.IPv6Address(host[1:-1])
                except ValueError:
                    raise ValueError("CONTAINER_HOST_CONFIG") from None
            elif all(HOST_LABEL.fullmatch(label) for label in host.split(".")):
                names.append(host.lower())
                continue
            else:
                raise ValueError("CONTAINER_HOST_CONFIG") from None
        if address.version == 6 and address.scope_id is not None:
            raise ValueError("CONTAINER_HOST_CONFIG")
        names.append(f"[{address}]" if address.version == 6 else str(address))
    return list(dict.fromkeys([*names, "127.0.0.1"]))
