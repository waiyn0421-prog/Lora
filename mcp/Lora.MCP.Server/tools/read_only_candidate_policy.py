# -*- coding: utf-8 -*-
"""Route allowlist used only by the isolated read-only Revit sidecar candidate."""

API_NAME = "lora-identity-v1"

READ_ONLY_ROUTES = frozenset((
    ("/session_identity", "GET"),
    ("/level_catalog", "POST"),
    ("/coordinate_diagnostic", "GET"),
    ("/geometry_inventory", "POST"),
    ("/category_inventory", "POST"),
    ("/geometry_detail", "POST"),
))


class RouteRegistryConflict(RuntimeError):
    pass


def route_specs(route_registry):
    """Normalize pyRevit Route keys or simple test tuples to path/method pairs."""
    result = set()
    for route in (route_registry or {}).keys():
        if hasattr(route, "pattern") and hasattr(route, "method"):
            pattern, method = route.pattern, route.method
        elif isinstance(route, (tuple, list)) and len(route) == 2:
            pattern, method = route
        else:
            raise RouteRegistryConflict("Unrecognized route registration shape")
        if not isinstance(pattern, str) or not isinstance(method, str):
            raise RouteRegistryConflict("Route path and method must be strings")
        result.add((pattern, method.upper()))
    return frozenset(result)


def assert_registry_empty(route_registry):
    """Reject an existing API registration rather than coexist with unknown handlers."""
    existing = route_specs(route_registry)
    if existing:
        raise RouteRegistryConflict(
            "Read-only sidecar requires an empty API registry; existing routes: {}".format(
                sorted(existing)))


def assert_exact_read_only_registry(route_registry):
    """Fail closed unless the complete API route map is exactly the approved set."""
    actual = route_specs(route_registry)
    if actual != READ_ONLY_ROUTES:
        raise RouteRegistryConflict(
            "Read-only route registry differs from allowlist; missing={}, unexpected={}".format(
                sorted(READ_ONLY_ROUTES - actual), sorted(actual - READ_ONLY_ROUTES)))
