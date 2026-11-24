"""Registers the sidecar session identity route on pyRevit's existing Routes server."""

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
    from lora_identity_provider import wall_execution
    from lora_identity_provider import coordinate_geometry
    from lora_identity_provider import category_inventory
    from lora_identity_provider import geometry_detail
    from lora_identity_provider import level_catalog
    from lora_identity_provider.security import identity_route_binding_is_safe, REQUIRED_ROUTES_HOST

    active_routes_server = get_active_server()
    if not identity_route_binding_is_safe(active_routes_server, user_config.routes_host):
        actual_host = getattr(active_routes_server, "host", None) if active_routes_server is not None else user_config.routes_host
        logger.error("Refusing to register the identity route: Routes host {!r} is not the required loopback host {}.".format(
            actual_host, REQUIRED_ROUTES_HOST))
        raise RuntimeError("Identity route requires Routes host {}".format(REQUIRED_ROUTES_HOST))

    get_or_start(HOST_APP.uiapp)
    api = routes.API("lora-identity-v1")

    @api.route("/session_identity", methods=["GET"])
    def get_session_identity(uiapp, uidoc, doc, request):
        runtime = get_or_start(uiapp)
        return runtime.identity_response(uiapp, doc)

    @api.route("/wall_types", methods=["GET"])
    def get_wall_types(uiapp, uidoc, doc, request):
        runtime = get_or_start(uiapp)
        return wall_execution.list_wall_types(runtime, uiapp, doc)

    @api.route("/level_catalog", methods=["POST"])
    def get_level_catalog(uiapp, uidoc, doc, request):
        runtime = get_or_start(uiapp)
        payload = request.data if hasattr(request, "data") else {}
        if not isinstance(payload, dict):
            return {"status": "error", "error": "invalid_request"}
        return level_catalog.level_catalog(runtime, uiapp, doc,
            payload.get("offset", 0), payload.get("limit", 100),
            payload.get("failure_offset", 0), payload.get("failure_limit", 50))

    @api.route("/coordinate_diagnostic", methods=["GET"])
    def get_coordinate_diagnostic(uiapp, uidoc, doc, request):
        runtime = get_or_start(uiapp)
        return coordinate_geometry.coordinate_diagnostic(runtime, uiapp, doc)

    @api.route("/geometry_inventory", methods=["POST"])
    def get_geometry_inventory(uiapp, uidoc, doc, request):
        runtime = get_or_start(uiapp)
        payload = request.data if hasattr(request, "data") else {}
        if not isinstance(payload, dict):
            return {"status": "error", "error": "invalid_request"}
        return coordinate_geometry.geometry_inventory(
            runtime, uiapp, doc, payload.get("offset", 0), payload.get("limit", 50),
            payload.get("failure_offset", 0), payload.get("failure_limit", 50))

    @api.route("/category_inventory", methods=["POST"])
    def get_category_inventory(uiapp, uidoc, doc, request):
        runtime = get_or_start(uiapp)
        payload = request.data if hasattr(request, "data") else {}
        if not isinstance(payload, dict):
            return {"status": "error", "error": "invalid_request"}
        return category_inventory.category_inventory(runtime, uiapp, doc,
            payload.get("discipline"), payload.get("category_id"), payload.get("offset", 0),
            payload.get("limit", 50), payload.get("failure_offset", 0), payload.get("failure_limit", 50),
            payload.get("category_offset", 0), payload.get("category_limit", 50))

    @api.route("/geometry_detail", methods=["POST"])
    def get_geometry_detail(uiapp, uidoc, doc, request):
        runtime = get_or_start(uiapp)
        payload = request.data if hasattr(request, "data") else {}
        if not isinstance(payload, dict):
            return {"status": "error", "error": "invalid_request"}
        return geometry_detail.geometry_detail(runtime, uiapp, doc, payload.get("element_ids"),
            payload.get("entity_offset", 0), payload.get("entity_limit", 100))

    @api.route("/validate_walls", methods=["POST"])
    def validate_lora_walls(uiapp, doc, request):
        runtime = get_or_start(uiapp)
        payload = request.data if hasattr(request, "data") else {}
        return wall_execution.validate_walls(runtime, uiapp, doc, payload)

    @api.route("/execute_walls", methods=["POST"])
    def execute_lora_walls(uiapp, doc, request):
        runtime = get_or_start(uiapp)
        payload = request.data if hasattr(request, "data") else {}
        return wall_execution.execute_walls(runtime, uiapp, doc, payload)

    @api.route("/verify_walls", methods=["POST"])
    def verify_lora_walls(uiapp, doc, request):
        runtime = get_or_start(uiapp)
        payload = request.data if hasattr(request, "data") else {}
        return wall_execution.verify_walls(runtime, uiapp, doc, payload)

    @api.route("/execution_status", methods=["GET"])
    def get_lora_execution_status(uiapp, request):
        runtime = get_or_start(uiapp)
        query = getattr(request, "query_params", {}) or {}
        request_id = query.get("request_id") if hasattr(query, "get") else None
        if not isinstance(request_id, str) or len(request_id) > 100:
            return {"status": "error", "error": "invalid_request_id"}
        receipt = runtime.get_execution_receipt(request_id)
        if receipt is None:
            return {"status": "error", "error": "execution_receipt_unavailable"}
        return {"status": "success", "state": receipt.get("state"),
                "response": receipt.get("response") if receipt.get("state") == "complete" else None}

    logger.info("Lora Revit-side session identity route registered: /lora-identity-v1/session_identity")
except Exception as exc:
    logger.error("Lora Revit-side session identity provider failed to start: {}".format(type(exc).__name__))
