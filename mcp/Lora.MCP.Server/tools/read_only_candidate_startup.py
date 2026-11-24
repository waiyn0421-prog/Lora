# -*- coding: utf-8 -*-
"""Read-only-only pyRevit startup used by the isolated candidate bundle."""

import os
import sys

from pyrevit import HOST_APP, routes, script
from pyrevit.routes.server import get_active_server
from pyrevit.userconfig import user_config

logger = script.get_logger()
extension_dir = os.path.dirname(__file__)
lib_dir = os.path.join(extension_dir, "lib")
if lib_dir not in sys.path:
    sys.path.insert(0, lib_dir)

try:
    from lora_identity_provider.runtime import get_or_start
    from lora_identity_provider import coordinate_geometry
    from lora_identity_provider import category_inventory
    from lora_identity_provider import geometry_detail
    from lora_identity_provider import level_catalog
    from lora_identity_provider.security import identity_route_binding_is_safe, REQUIRED_ROUTES_HOST
    from lora_identity_provider.read_only_policy import (
        API_NAME, assert_registry_empty, assert_exact_read_only_registry)

    active_routes_server = get_active_server()
    if not identity_route_binding_is_safe(active_routes_server, user_config.routes_host):
        actual_host = getattr(active_routes_server, "host", None) if active_routes_server is not None else user_config.routes_host
        raise RuntimeError("Read-only sidecar requires Routes host {} (found {!r})".format(
            REQUIRED_ROUTES_HOST, actual_host))

    # The Routes server is shared. Never coexist with unknown or legacy handlers
    # under this API name; a legacy /execute_walls route must make startup fail.
    assert_registry_empty(routes.get_routes(API_NAME))
    api = routes.API(API_NAME)

    def _read_runtime(uiapp):
        assert_exact_read_only_registry(routes.get_routes(API_NAME))
        return get_or_start(uiapp)

    @api.route("/session_identity", methods=["GET"])
    def get_session_identity(uiapp, uidoc, doc, request):
        runtime = _read_runtime(uiapp)
        return runtime.identity_response(uiapp, doc)

    @api.route("/level_catalog", methods=["POST"])
    def get_level_catalog(uiapp, uidoc, doc, request):
        runtime = _read_runtime(uiapp)
        payload = request.data if hasattr(request, "data") else {}
        if not isinstance(payload, dict):
            return {"status": "error", "error": "invalid_request"}
        return level_catalog.level_catalog(runtime, uiapp, doc,
            payload.get("offset", 0), payload.get("limit", 100),
            payload.get("failure_offset", 0), payload.get("failure_limit", 50))

    @api.route("/coordinate_diagnostic", methods=["GET"])
    def get_coordinate_diagnostic(uiapp, uidoc, doc, request):
        runtime = _read_runtime(uiapp)
        return coordinate_geometry.coordinate_diagnostic(runtime, uiapp, doc)

    @api.route("/geometry_inventory", methods=["POST"])
    def get_geometry_inventory(uiapp, uidoc, doc, request):
        runtime = _read_runtime(uiapp)
        payload = request.data if hasattr(request, "data") else {}
        if not isinstance(payload, dict):
            return {"status": "error", "error": "invalid_request"}
        return coordinate_geometry.geometry_inventory(
            runtime, uiapp, doc, payload.get("offset", 0), payload.get("limit", 50),
            payload.get("failure_offset", 0), payload.get("failure_limit", 50))

    @api.route("/category_inventory", methods=["POST"])
    def get_category_inventory(uiapp, uidoc, doc, request):
        runtime = _read_runtime(uiapp)
        payload = request.data if hasattr(request, "data") else {}
        if not isinstance(payload, dict):
            return {"status": "error", "error": "invalid_request"}
        return category_inventory.category_inventory(runtime, uiapp, doc,
            payload.get("discipline"), payload.get("category_id"), payload.get("offset", 0),
            payload.get("limit", 50), payload.get("failure_offset", 0), payload.get("failure_limit", 50),
            payload.get("category_offset", 0), payload.get("category_limit", 50))

    @api.route("/geometry_detail", methods=["POST"])
    def get_geometry_detail(uiapp, uidoc, doc, request):
        runtime = _read_runtime(uiapp)
        payload = request.data if hasattr(request, "data") else {}
        if not isinstance(payload, dict):
            return {"status": "error", "error": "invalid_request"}
        return geometry_detail.geometry_detail(runtime, uiapp, doc, payload.get("element_ids"),
            payload.get("entity_offset", 0), payload.get("entity_limit", 100))

    assert_exact_read_only_registry(routes.get_routes(API_NAME))
    get_or_start(HOST_APP.uiapp)
    logger.info("Lora read-only sidecar registered exactly six allowlisted routes")
except Exception as exc:
    # Keep the established pyRevit startup contract but register no execution routes.
    logger.error("Lora read-only sidecar failed closed: {}: {}".format(type(exc).__name__, str(exc)))
