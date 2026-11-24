"""Fail-closed route registration policy for the shared pyRevit Routes listener."""


REQUIRED_ROUTES_HOST = "127.0.0.1"


def identity_route_binding_is_safe(active_routes_server, configured_host):
    """Use the live server binding when available; otherwise validate its configured bind host."""
    if active_routes_server is not None:
        host = getattr(active_routes_server, "host", None)
    else:
        host = configured_host
    return isinstance(host, str) and host.strip() == REQUIRED_ROUTES_HOST
