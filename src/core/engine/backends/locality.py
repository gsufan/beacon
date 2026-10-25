"""Whether a model server is on this machine or on a private network."""

import ipaddress
from urllib.parse import urlparse


def is_local_host(host: str) -> bool:
    """True for servers on this machine or on a private network: loopback, private addresses, `.local` names and
    single-label names (such as a Docker Compose service name). Everything else is external."""
    name = (urlparse(host if "://" in host else "http://" + host).hostname or "").lower()
    if not name:
        return False
    if name in ("localhost", "host.docker.internal") or name.endswith((".localhost", ".local", ".internal", ".lan")) or "." not in name:
        return True
    try:
        ip = ipaddress.ip_address(name)
    except ValueError:
        return False
    return ip.is_loopback or ip.is_private or ip.is_link_local
