"""Bounded, localhost-only HTTP client for the existing RevitMCP Routes API."""

import json
import logging
import os
import subprocess
import threading
from datetime import datetime, timezone
from urllib.parse import urlsplit

import requests


ROUTE_PREFIX = "/revit-mcp-v1"
DISCOVERY_PORTS = (48885, 48884, 48886)
READ_ROUTES = {
    ("GET", "/diagnostics/revit_state"),
    ("GET", "/schema/context"),
    ("GET", "/project_info"),
    ("GET", "/sheets/list_views"),
    ("POST", "/model/statistics"),
    ("POST", "/families/types"),
    ("POST", "/get_elements_by_category"),
    ("POST", "/elements/filter"),
    ("POST", "/elements/get_properties"),
}
IDENTITY_READ_ROUTE = ("GET", "/session_identity")
IDENTITY_SIDECAR_READ_ROUTES = {
    ("GET", "/coordinate_diagnostic"),
    ("POST", "/level_catalog"),
    ("POST", "/geometry_inventory"),
    ("POST", "/category_inventory"),
    ("POST", "/geometry_detail"),
}


class RevitMCPError(Exception):
    def __init__(self, code, message, details=None):
        super().__init__(message)
        self.code = code
        self.details = details

    def as_dict(self):
        result = {"code": self.code, "message": str(self)}
        if self.details is not None:
            result["details"] = self.details
        return result


def _positive_int(name, value, minimum, maximum):
    if isinstance(value, bool) or not isinstance(value, int) or not minimum <= value <= maximum:
        raise RevitMCPError("invalid_input", "{} must be an integer from {} to {}".format(name, minimum, maximum))
    return value


class RevitMCPClient:
    @staticmethod
    def _candidate_ports():
        raw = os.environ.get("LORA_REVITMCP_PORTS", "")
        if not raw.strip():
            return DISCOVERY_PORTS
        try:
            ports = tuple(dict.fromkeys(int(part.strip()) for part in raw.split(",") if part.strip()))
        except ValueError as exc:
            raise RevitMCPError("invalid_configuration", "LORA_REVITMCP_PORTS must be comma-separated port numbers") from exc
        if not ports or any(port < 1 or port > 65535 for port in ports):
            raise RevitMCPError("invalid_configuration", "LORA_REVITMCP_PORTS contains an invalid port")
        return ports

    @staticmethod
    def _url(route, base_url):
        return base_url + route

    def _request_json(self, method, route, payload=None, base_url=None):
        if (method, route) not in READ_ROUTES and (method, route) != IDENTITY_READ_ROUTE:
            raise RevitMCPError("route_not_allowed", "Route is not in Lora's read-only allowlist")
        endpoint = base_url or self.base_url
        if not endpoint:
            raise RevitMCPError("not_connected", "RevitMCP Routes endpoint has not been discovered")
        response = None
        try:
            response = self.session.request(
                method,
                self._url(route, endpoint),
                json=payload if method == "POST" else None,
                timeout=self.timeout,
                stream=True,
                allow_redirects=False,
            )
            raw = bytearray()
            for chunk in response.iter_content(chunk_size=65536):
                if chunk:
                    raw.extend(chunk)
                    if len(raw) > self.max_response_bytes:
                        raise RevitMCPError("response_too_large", "RevitMCP response exceeded the configured byte limit")
            if response.status_code < 200 or response.status_code >= 300:
                upstream_error_kind = None
                try:
                    error_body = json.loads(raw.decode("utf-8-sig"))
                    exception = error_body.get("exception") if isinstance(error_body, dict) else None
                    message = exception.get("message", "") if isinstance(exception, dict) else ""
                    if ('Route does not exits: "GET lora-identity-v1/session_identity"' in message and
                            route == "/session_identity"):
                        upstream_error_kind = "route_not_found"
                except (UnicodeDecodeError, ValueError):
                    pass
                raise RevitMCPError("upstream_http_error", "RevitMCP returned HTTP {}".format(response.status_code),
                                    {"http_status": response.status_code, "endpoint": endpoint, "route": route,
                                     "upstream_error_kind": upstream_error_kind})
            try:
                result = json.loads(raw.decode("utf-8-sig"))
            except (UnicodeDecodeError, ValueError) as exc:
                raise RevitMCPError("invalid_upstream_json", "RevitMCP returned an invalid JSON response") from exc
            if not isinstance(result, dict):
                raise RevitMCPError("invalid_upstream_response", "RevitMCP response must be a JSON object")
            if result.get("status") == "error" or result.get("error"):
                details = {"http_status": response.status_code, "endpoint": endpoint,
                           "route": route, "upstream_error": result.get("error") or result.get("message")}
                provider_diagnostics = result.get("diagnostics")
                if route == "/session_identity" and isinstance(provider_diagnostics, dict):
                    details["provider_diagnostics"] = provider_diagnostics
                raise RevitMCPError("upstream_error", "RevitMCP reported an error", details)
            return result
        except RevitMCPError:
            raise
        except requests.Timeout as exc:
            raise RevitMCPError("timeout", "Timed out communicating with RevitMCP") from exc
        except requests.RequestException as exc:
            raise RevitMCPError("connection_error", "Could not communicate with RevitMCP") from exc
        finally:
            if response is not None:
                close = getattr(response, "close", None)
                if close:
                    close()

    def __init__(self, session=None, connect_timeout=2.0, read_timeout=12.0, max_response_bytes=8 * 1024 * 1024):
        self.session = session or requests.Session()
        # Revit calls must stay on the explicit loopback endpoint even when the
        # host process has HTTP_PROXY/HTTPS_PROXY or netrc configuration.
        self.session.trust_env = False
        self.timeout = (connect_timeout, read_timeout)
        self.max_response_bytes = max_response_bytes
        self.base_url = None
        self.logger = logging.getLogger("lora_mcp")
        self._endpoint_lock = threading.RLock()
        if connect_timeout <= 0 or read_timeout <= 0:
            raise ValueError("Timeouts must be positive")
        if max_response_bytes < 1024:
            raise ValueError("max_response_bytes must be at least 1024")

    def _endpoint_for_port(self, port):
        return "http://127.0.0.1:{}{}".format(port, ROUTE_PREFIX)

    def probe_endpoint(self, endpoint):
        """Probe a single explicit loopback Routes endpoint without changing the pinned endpoint."""
        try:
            data = self._request_json("GET", "/diagnostics/revit_state", base_url=endpoint)
            doc = data.get("document_state") or {}
            process = data.get("process") or {}
            confirmed = data.get("status") == "success" and bool(data.get("route_version") or process)
            return {"endpoint": endpoint, "http_reachable": True, "routes_confirmed": confirmed,
                    "connection_status": "connected" if confirmed else "responding_unverified",
                    "document_status": "available" if _has_document(doc) else "unavailable",
                    "process_id": process.get("pid"), "route_version": data.get("route_version"),
                    "error": None, "diagnostics": data}
        except RevitMCPError as exc:
            details = exc.details if isinstance(exc.details, dict) else {}
            responding = exc.code == "upstream_error" or details.get("http_status") is not None
            document_unavailable = details.get("http_status") == 503
            return {"endpoint": endpoint, "http_reachable": responding,
                    "routes_confirmed": False,
                    "connection_status": "responding_unverified" if responding else exc.code,
                    "document_status": "unavailable" if document_unavailable else "unknown",
                    "process_id": None, "route_version": None, "error": exc.as_dict(), "diagnostics": None}

    def probe_candidates(self):
        """Probe every configured loopback port; never select the first response silently."""
        results = [self.probe_endpoint(self._endpoint_for_port(port)) for port in self._candidate_ports()]
        responders = [item for item in results if item["routes_confirmed"]]
        return {"probes": results, "confirmed_endpoints": [item["endpoint"] for item in responders],
                "responding_endpoints": [item["endpoint"] for item in results if item["http_reachable"]]}

    def discover(self):
        """Pin a unique confirmed endpoint. Ambiguous or failed discovery never falls through to another process."""
        with self._endpoint_lock:
            if self.base_url is not None:
                return self.base_url
            result = self.probe_candidates()
            endpoints = result["confirmed_endpoints"]
            if len(endpoints) == 1:
                self.base_url = endpoints[0]
                return self.base_url
            self.base_url = None
            if len(endpoints) > 1:
                raise RevitMCPError("endpoint_ambiguous", "Multiple RevitMCP Routes endpoints responded; refusing to choose one",
                                    result)
            if result["responding_endpoints"]:
                raise RevitMCPError("routes_unverified", "A localhost endpoint responded but did not identify as RevitMCP Routes",
                                    result)
            raise RevitMCPError("routes_unavailable", "No configured localhost RevitMCP Routes endpoint responded",
                                result)

    def diagnose_connection(self):
        """Probe all candidates and sample the pinned endpoint twice without switching endpoints."""
        with self._endpoint_lock:
            pinned = self.base_url
            discovery = self.probe_candidates()
            confirmed = discovery["confirmed_endpoints"]
            if pinned is None and len(confirmed) == 1:
                self.base_url = confirmed[0]
                pinned = self.base_url
            ambiguous = len(confirmed) > 1 or (pinned is not None and any(item != pinned for item in confirmed))
            endpoint_probe = next((item for item in discovery["probes"] if item["endpoint"] == pinned), None) if pinned else None
            result = {"connection_status": "ambiguous" if ambiguous else (
                    "connected" if endpoint_probe and endpoint_probe["routes_confirmed"] else "unavailable"),
                "pinned_endpoint": pinned, "endpoint_changed": False,
                "candidate_endpoints": discovery["probes"],
                "responding_endpoint_count": len(confirmed), "identity_stability": "not_checked",
                "identity_change": None, "active_document_identity": None,
                "local_revit_process": self.local_revit_processes()}
            if ambiguous:
                result["diagnostic_error"] = {"code": "endpoint_ambiguous",
                    "message": "More than one configured Routes endpoint responded; the existing pin was not changed."}
                return result
            if endpoint_probe is None or not endpoint_probe["routes_confirmed"]:
                result["diagnostic_error"] = (endpoint_probe.get("error") if endpoint_probe else
                    next((item.get("error") for item in discovery["probes"] if item.get("error")), None))
                result["local_revit_process"] = self.local_revit_processes()
                result["revit_process_status"] = result["local_revit_process"].get("status")
                error_code = (result["diagnostic_error"] or {}).get("code")
                error_details = (result["diagnostic_error"] or {}).get("details") or {}
                processes = result["local_revit_process"].get("processes") or []
                if error_code == "timeout":
                    result["connection_status"] = "timeout"
                elif error_details.get("http_status") == 503:
                    result["connection_status"] = "document_unavailable"
                elif result["local_revit_process"].get("status") != "available":
                    result["connection_status"] = "revit_status_unknown"
                else:
                    result["connection_status"] = "routes_listener_unavailable" if processes else "revit_unavailable"
                return result
            try:
                first = self._capture_identity_at(pinned)
                second = self._capture_identity_at(pinned)
                changed = _identity_key(first) != _identity_key(second)
                result["active_document_identity"] = second
                result["identity_stability"] = "changed_between_queries" if changed else "stable_between_queries"
                result["identity_change"] = {"before": first, "after": second} if changed else None
                result["connection_status"] = "document_changed" if changed else "connected"
                result["document_status"] = second["document_status"]
                result["local_revit_process"] = self.local_process_metadata(second.get("revit_process_id"))
            except RevitMCPError as exc:
                result["connection_status"] = "document_unavailable" if exc.code == "document_unavailable" else exc.code
                result["diagnostic_error"] = exc.as_dict()
                result["local_revit_process"] = self.local_revit_processes()
            if result["connection_status"] == "routes_unavailable":
                local = result["local_revit_process"]
                processes = local.get("processes") or []
                result["connection_status"] = ("routes_listener_unavailable" if processes else
                    "revit_unavailable" if local.get("status") == "available" else "revit_status_unknown")
            return result

    def _ensure_base_url(self):
        with self._endpoint_lock:
            if self.base_url is None:
                self.discover()

    def _capture_identity_at(self, endpoint):
        data = self._request_json("GET", "/diagnostics/revit_state", base_url=endpoint)
        doc = data.get("document_state") or {}
        process = data.get("process") or {}
        if not isinstance(doc, dict) or not isinstance(process, dict):
            raise RevitMCPError("document_identity_unavailable", "RevitMCP diagnostic identity fields are invalid")
        has_document = _has_document(doc)
        if not has_document:
            raise RevitMCPError("document_unavailable", "RevitMCP Routes is responding but has no active document",
                                {"endpoint": endpoint, "document_state": doc})
        title = doc.get("title")
        if not title:
            raise RevitMCPError("document_identity_unavailable", "Active document title is unavailable",
                                {"endpoint": endpoint, "document_state": doc})
        diagnostic_document_id = _first_nonempty(
            doc, ("document_id", "document_guid", "unique_id", "guid", "document_identifier"))
        document_id = None
        app = data.get("application") or {}
        revit_version = _first_nonempty(data, ("revit_version", "application_version"))
        if revit_version is None and isinstance(app, dict):
            revit_version = _first_nonempty(app, ("revit_version", "application_version", "version_number"))
        path = doc.get("path")
        process_id = process.get("pid")
        identity_provider_status = "not_checked"
        provider_identity = None
        try:
            provider_data = self._request_json("GET", "/session_identity",
                                               base_url=self._identity_endpoint_for(endpoint))
            provider_identity = provider_data.get("identity")
            if provider_data.get("status") != "success" or not isinstance(provider_identity, dict):
                provider_error = provider_data.get("error")
                code = ("active_document_changed" if provider_error == "active_document_changed" else
                        "document_unavailable" if provider_error in ("document_unavailable", "document_not_open") else
                        "identity_provider_invalid")
                raise RevitMCPError(code, "Revit-side identity provider returned no valid identity",
                                    {"provider_error": provider_error,
                                     "provider_diagnostics": provider_data.get("diagnostics")})
            required_provider_fields = ("process_session_id", "application_session_id", "document_session_id")
            missing_provider_fields = [name for name in required_provider_fields
                                       if not provider_identity.get(name)]
            if missing_provider_fields:
                raise RevitMCPError("identity_provider_missing_identity",
                                    "Revit-side identity provider omitted required session identity fields",
                                    {"missing_fields": missing_provider_fields})
            provider_pid = provider_identity.get("process_id")
            if provider_pid != process_id:
                raise RevitMCPError("identity_provider_process_mismatch",
                                    "Revit-side identity provider and Routes diagnostics identify different processes",
                                    {"routes_process_id": process_id, "identity_process_id": provider_pid})
            if provider_identity.get("active_document") is not True or provider_identity.get("document_status") != "open":
                raise RevitMCPError("identity_provider_document_unavailable",
                                    "Revit-side identity provider does not confirm an open active document")
            if provider_identity.get("document_title") != title:
                raise RevitMCPError("identity_provider_document_mismatch",
                                    "Revit-side identity provider and Routes diagnostics identify different documents")
            provider_path = provider_identity.get("document_path")
            if path and provider_path and provider_path != path:
                raise RevitMCPError("identity_provider_document_mismatch",
                                    "Revit-side identity provider and Routes diagnostics report different document paths")
            identity_provider_status = "ready"
            document_id = provider_identity["document_session_id"]
        except RevitMCPError as exc:
            details = exc.details if isinstance(exc.details, dict) else {}
            if details.get("http_status") == 404 or details.get("upstream_error_kind") == "route_not_found":
                identity_provider_status = "not_installed"
            else:
                raise RevitMCPError("identity_provider_unavailable",
                                    "Revit-side session identity could not be verified",
                                    {"provider_error": exc.as_dict()}) from exc
        certainty = "stable_document_session" if identity_provider_status == "ready" else (
            "saved_document_composite" if path and title and process_id else (
                "unsaved_title_process_only" if title and process_id else "incomplete"))
        result = {"document_title": str(title), "document_path": str(path) if path else None,
                "document_type": _first_nonempty(doc, ("document_type", "type")),
                "revit_version": revit_version, "revit_process_id": process_id,
                "revit_process_name": process.get("process_name"),
                "document_identifier": document_id, "diagnostic_document_identifier": diagnostic_document_id,
                "document_identifier_source": None,
                "connection_endpoint": endpoint, "route_version": data.get("route_version"),
                "document_status": "available",
                "observed_at": datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
                "identity_certainty": certainty,
                "identity_provider_status": identity_provider_status,
                "identity_certain_for_guard": identity_provider_status == "ready",
                "identity_limitations": [] if identity_provider_status == "ready" else [
                    "The Revit-side session identity provider is not installed; title/path/PID are not accepted as a stable session identity."]}
        if provider_identity is not None:
            result.update({
                "document_identifier_source": "revit_side_open_document_session",
                "process_session_id": provider_identity.get("process_session_id"),
                "application_session_id": provider_identity.get("application_session_id"),
                "document_session_id": provider_identity.get("document_session_id"),
                "document_lifecycle_sequence": provider_identity.get("document_lifecycle_sequence"),
                "document_lifecycle_generation": provider_identity.get("document_lifecycle_generation"),
                "active_document_switch_counter": provider_identity.get("active_document_switch_counter"),
                "identity_provider_version": provider_identity.get("identity_provider_version"),
                "identity_diagnostics": provider_data.get("diagnostics"),
                "identity_provider_status": identity_provider_status,
                "document_type": provider_identity.get("document_type") or result["document_type"],
                "revit_version": provider_identity.get("revit_version") or result["revit_version"],
                "document_path": provider_identity.get("document_path") or result["document_path"],
            })
        return result

    @staticmethod
    def _identity_endpoint_for(endpoint):
        parsed = urlsplit(endpoint)
        if parsed.scheme != "http" or parsed.hostname != "127.0.0.1" or not parsed.port:
            raise RevitMCPError("invalid_endpoint", "Identity provider endpoint must use HTTP loopback")
        return "http://127.0.0.1:{}/lora-identity-v1".format(parsed.port)

    def active_document_identity(self):
        self._ensure_base_url()
        return self._capture_identity_at(self.base_url)

    def get_wall_types(self):
        """Read the Lora sidecar's actual WallType catalog, pinned to the same loopback Routes port."""
        from urllib.parse import urlsplit
        before = self.active_document_identity()
        parsed = urlsplit(self.base_url)
        if parsed.scheme != "http" or parsed.hostname != "127.0.0.1" or not parsed.port:
            raise RevitMCPError("invalid_endpoint", "WallType discovery requires the pinned IPv4 loopback Routes port")
        url = "http://127.0.0.1:{}/lora-identity-v1/wall_types".format(parsed.port)
        response = None
        try:
            response = self.session.get(url, timeout=self.timeout, stream=True, allow_redirects=False)
            raw = bytearray()
            for chunk in response.iter_content(chunk_size=65536):
                if chunk:
                    raw.extend(chunk)
                    if len(raw) > self.max_response_bytes:
                        raise RevitMCPError("response_too_large", "WallType catalog exceeded configured response limit")
            if response.status_code < 200 or response.status_code >= 300:
                raise RevitMCPError("wall_type_route_unavailable", "Lora WallType discovery route returned HTTP {}".format(response.status_code),
                                    {"http_status": response.status_code})
            result = json.loads(raw.decode("utf-8-sig"))
        except RevitMCPError:
            raise
        except requests.Timeout as exc:
            raise RevitMCPError("timeout", "Timed out reading the Revit WallType catalog") from exc
        except requests.RequestException as exc:
            raise RevitMCPError("connection_error", "Could not reach the local WallType discovery route") from exc
        except (UnicodeDecodeError, ValueError) as exc:
            raise RevitMCPError("invalid_upstream_json", "WallType discovery returned invalid JSON") from exc
        finally:
            if response is not None:
                response.close()
        after = self.active_document_identity()
        if _identity_key(before) != _identity_key(after):
            raise RevitMCPError("document_changed", "Active document changed during WallType discovery")
        if not isinstance(result, dict) or result.get("status") != "success" or not isinstance(result.get("wall_types"), list):
            raise RevitMCPError("invalid_wall_type_response", "WallType discovery response is incomplete")
        route_identity = result.get("document_identity") or {}
        route_key = (route_identity.get("process_id"), route_identity.get("process_session_id"),
            route_identity.get("application_session_id"), route_identity.get("document_session_id"),
            route_identity.get("document_lifecycle_sequence"), route_identity.get("document_lifecycle_generation"),
            route_identity.get("active_document_switch_counter"))
        expected_key = (before.get("revit_process_id"), before.get("process_session_id"),
            before.get("application_session_id"), before.get("document_session_id"),
            before.get("document_lifecycle_sequence"), before.get("document_lifecycle_generation"),
            before.get("active_document_switch_counter"))
        if route_key != expected_key:
            raise RevitMCPError("document_changed", "Active document changed during WallType route execution")
        result["_lora_document_identity"] = before
        return result

    def _identity_sidecar_call(self, method, route, payload=None):
        """Read a dedicated Lora sidecar route on the already pinned loopback Routes port."""
        if (method, route) not in IDENTITY_SIDECAR_READ_ROUTES:
            raise RevitMCPError("route_not_allowed", "Route is not in Lora's identity-sidecar read-only allowlist")
        before = self.active_document_identity()
        endpoint_before = self.base_url
        parsed = urlsplit(self.base_url)
        if parsed.scheme != "http" or parsed.hostname != "127.0.0.1" or not parsed.port:
            raise RevitMCPError("invalid_endpoint", "Identity diagnostics require the pinned IPv4 loopback Routes port")
        url = "http://127.0.0.1:{}/lora-identity-v1{}".format(parsed.port, route)
        response = None
        try:
            response = self.session.request(method, url, json=payload if method == "POST" else None,
                timeout=self.timeout, stream=True, allow_redirects=False)
            raw = bytearray()
            for chunk in response.iter_content(chunk_size=65536):
                if chunk:
                    raw.extend(chunk)
                    if len(raw) > self.max_response_bytes:
                        raise RevitMCPError("response_too_large", "Identity diagnostic exceeded configured response limit")
            if response.status_code < 200 or response.status_code >= 300:
                raise RevitMCPError("identity_diagnostic_unavailable",
                    "Lora identity diagnostic returned HTTP {}".format(response.status_code),
                    {"http_status": response.status_code, "route": route})
            result = json.loads(raw.decode("utf-8-sig"))
        except RevitMCPError:
            raise
        except requests.Timeout as exc:
            raise RevitMCPError("timeout", "Timed out reading the Lora identity diagnostic") from exc
        except requests.RequestException as exc:
            raise RevitMCPError("connection_error", "Could not reach the local Lora identity diagnostic") from exc
        except (UnicodeDecodeError, ValueError) as exc:
            raise RevitMCPError("invalid_upstream_json", "Identity diagnostic returned invalid JSON") from exc
        finally:
            if response is not None:
                response.close()
        after = self.active_document_identity()
        if endpoint_before != self.base_url:
            raise RevitMCPError("endpoint_changed", "Pinned RevitMCP endpoint changed during the identity diagnostic",
                                {"before": endpoint_before, "after": self.base_url})
        if _identity_key(before) != _identity_key(after):
            raise RevitMCPError("document_changed", "Active Revit document changed during the diagnostic")
        if not isinstance(result, dict) or result.get("status") != "success":
            raise RevitMCPError("invalid_identity_diagnostic", "Identity diagnostic returned an incomplete response")
        route_identity = result.get("document_identity") or {}
        expected = (before.get("revit_process_id"), before.get("process_session_id"),
                    before.get("application_session_id"), before.get("document_session_id"))
        actual = (route_identity.get("process_id", route_identity.get("revit_process_id")),
                  route_identity.get("process_session_id"), route_identity.get("application_session_id"),
                  route_identity.get("document_session_id"))
        if expected != actual:
            raise RevitMCPError("document_changed", "Sidecar diagnostic identity does not match the active document")
        result["_lora_document_identity"] = before
        return result

    def get_coordinate_diagnostic(self):
        return self._identity_sidecar_call("GET", "/coordinate_diagnostic")

    def get_level_catalog(self, offset=0, limit=100, failure_offset=0, failure_limit=50):
        for name, value, minimum, maximum in (("offset", offset, 0, 10000000),
                ("failure_offset", failure_offset, 0, 10000000),
                ("limit", limit, 1, 100), ("failure_limit", failure_limit, 1, 100)):
            if isinstance(value, bool) or not isinstance(value, int) or not minimum <= value <= maximum:
                raise RevitMCPError("invalid_input", "{} is outside the allowed range".format(name))
        return self._identity_sidecar_call("POST", "/level_catalog", {
            "offset": offset, "limit": limit,
            "failure_offset": failure_offset, "failure_limit": failure_limit})

    def get_geometry_inventory(self, offset=0, limit=50, failure_offset=0, failure_limit=50):
        if isinstance(offset, bool) or not isinstance(offset, int) or offset < 0 or offset > 10000000:
            raise RevitMCPError("invalid_input", "offset must be an integer from 0 to 10000000")
        if isinstance(limit, bool) or not isinstance(limit, int) or limit < 1 or limit > 100:
            raise RevitMCPError("invalid_input", "limit must be an integer from 1 to 100")
        if isinstance(failure_offset, bool) or not isinstance(failure_offset, int) or failure_offset < 0 or failure_offset > 10000000:
            raise RevitMCPError("invalid_input", "failure_offset must be an integer from 0 to 10000000")
        if isinstance(failure_limit, bool) or not isinstance(failure_limit, int) or failure_limit < 1 or failure_limit > 100:
            raise RevitMCPError("invalid_input", "failure_limit must be an integer from 1 to 100")
        return self._identity_sidecar_call("POST", "/geometry_inventory", {
            "offset": offset, "limit": limit,
            "failure_offset": failure_offset, "failure_limit": failure_limit})

    def get_category_inventory(self, discipline=None, category_id=None, offset=0, limit=50,
                               failure_offset=0, failure_limit=50, category_offset=0, category_limit=50):
        if discipline is not None and discipline not in ("architecture", "structural", "mep", "other", "unknown"):
            raise RevitMCPError("invalid_input", "discipline must be architecture, structural, mep, other, or unknown")
        for name, value, minimum, maximum in (("offset", offset, 0, 10000000),
                ("failure_offset", failure_offset, 0, 10000000),
                ("limit", limit, 1, 100), ("failure_limit", failure_limit, 1, 100),
                ("category_offset", category_offset, 0, 10000000), ("category_limit", category_limit, 1, 100)):
            if isinstance(value, bool) or not isinstance(value, int) or not minimum <= value <= maximum:
                raise RevitMCPError("invalid_input", "{} is outside the allowed range".format(name))
        if category_id is not None and (isinstance(category_id, bool) or not str(category_id).lstrip("-").isdigit()):
            raise RevitMCPError("invalid_input", "category_id must be a numeric Revit category ID")
        return self._identity_sidecar_call("POST", "/category_inventory", {
            "discipline": discipline, "category_id": str(category_id) if category_id is not None else None,
            "offset": offset, "limit": limit,
            "failure_offset": failure_offset, "failure_limit": failure_limit,
            "category_offset": category_offset, "category_limit": category_limit})

    def get_geometry_detail(self, element_ids, entity_offset=0, entity_limit=100):
        if not isinstance(element_ids, (list, tuple)) or not 1 <= len(element_ids) <= 25:
            raise RevitMCPError("invalid_input", "element_ids must contain 1 to 25 Revit element IDs")
        for value in element_ids:
            if isinstance(value, bool) or not str(value).isdigit():
                raise RevitMCPError("invalid_input", "element_ids must contain numeric Revit IDs")
        if len(set(str(value) for value in element_ids)) != len(element_ids):
            raise RevitMCPError("invalid_input", "element_ids must be unique")
        if isinstance(entity_offset, bool) or not isinstance(entity_offset, int) or not 0 <= entity_offset <= 10000000:
            raise RevitMCPError("invalid_input", "entity_offset is outside the allowed range")
        if isinstance(entity_limit, bool) or not isinstance(entity_limit, int) or not 1 <= entity_limit <= 200:
            raise RevitMCPError("invalid_input", "entity_limit must be from 1 to 200")
        return self._identity_sidecar_call("POST", "/geometry_detail", {
            "element_ids": [str(value) for value in element_ids],
            "entity_offset": entity_offset, "entity_limit": entity_limit})

    def _identity(self):
        return self.active_document_identity()

    def call(self, method, route, payload=None):
        """Call one allowlisted route and reject results if the active document changes."""
        if (method, route) not in READ_ROUTES:
            raise RevitMCPError("route_not_allowed", "Route is not in Lora's read-only allowlist")
        before = self._identity()
        endpoint_before = self.base_url
        result = self._request_json(method, route, payload, base_url=endpoint_before)
        after = self._identity()
        if endpoint_before != self.base_url:
            raise RevitMCPError("endpoint_changed", "Pinned RevitMCP endpoint changed during the read operation",
                                {"before": endpoint_before, "after": self.base_url})
        if _identity_key(before) != _identity_key(after):
            raise RevitMCPError("document_changed", "Active Revit document or process changed during the read operation",
                                {"before": before, "after": after})
        result["_lora_document_identity"] = before
        return result

    def local_process_metadata(self, process_id):
        """Read process executable/version metadata via PowerShell; failure is reported, never guessed."""
        if os.name != "nt" or isinstance(process_id, bool) or not isinstance(process_id, int):
            return {"status": "unavailable", "executable_path": None, "file_version": None,
                    "product_version": None, "reason": "Windows process inspection unavailable or PID invalid"}
        script = ("$p=Get-Process -Id {0} -ErrorAction Stop; $path=$p.Path; "
                  "$v=(Get-Item -LiteralPath $path -ErrorAction Stop).VersionInfo; "
                  "[pscustomobject]@{{pid=$p.Id;path=$path;file_version=$v.FileVersion;product_version=$v.ProductVersion}} "
                  "| ConvertTo-Json -Compress").format(process_id)
        try:
            completed = subprocess.run(["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", script],
                                       capture_output=True, text=True, timeout=3, check=False,
                                       creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            if completed.returncode != 0 or not completed.stdout.strip():
                return {"status": "unavailable", "executable_path": None, "file_version": None,
                        "product_version": None, "reason": "PowerShell could not read the process metadata"}
            value = json.loads(completed.stdout)
            return {"status": "available", "executable_path": value.get("path"),
                    "file_version": value.get("file_version"), "product_version": value.get("product_version"),
                    "pid": value.get("pid")}
        except (OSError, subprocess.SubprocessError, ValueError):
            return {"status": "unavailable", "executable_path": None, "file_version": None,
                    "product_version": None, "reason": "Process executable metadata query failed"}

    def local_revit_processes(self):
        if os.name != "nt":
            return {"status": "unavailable", "processes": [], "reason": "Local Windows process inspection is unavailable"}
        try:
            script = "Get-Process -Name Revit -ErrorAction SilentlyContinue | Select-Object Id,ProcessName | ConvertTo-Json -Compress"
            completed = subprocess.run(["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", script],
                                       capture_output=True, text=True, timeout=3, check=False,
                                       creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            if completed.returncode != 0:
                return {"status": "unavailable", "processes": [], "reason": "PowerShell process query failed"}
            raw = json.loads(completed.stdout) if completed.stdout.strip() else []
            values = raw if isinstance(raw, list) else ([raw] if raw else [])
            return {"status": "available", "processes": values}
        except (OSError, subprocess.SubprocessError, ValueError):
            return {"status": "unavailable", "processes": [], "reason": "Local Revit process query failed"}


def _has_document(doc):
    return bool(doc.get("has_document", doc.get("has_doc", False))) if isinstance(doc, dict) else False


def _first_nonempty(mapping, keys):
    if not isinstance(mapping, dict):
        return None
    for key in keys:
        value = mapping.get(key)
        if value not in (None, ""):
            return value
    return None


def _identity_key(identity):
    fields = ("document_title", "document_path", "document_type", "revit_version",
              "revit_process_id", "document_identifier", "connection_endpoint",
              "process_session_id", "application_session_id", "document_session_id",
              "document_lifecycle_sequence", "document_lifecycle_generation",
              "active_document_switch_counter", "identity_provider_version", "identity_provider_status")
    return tuple(identity.get(key) for key in fields)


def paginate(records, offset, limit, total=None):
    offset = _positive_int("offset", offset, 0, 10_000_000)
    limit = _positive_int("limit", limit, 1, 200)
    items = records[offset:offset + limit]
    reported_total = len(records) if total is None else total
    upstream_complete = reported_total == len(records)
    end = offset + len(items)
    return {
        "items": items,
        "offset": offset,
        "limit": limit,
        "total": reported_total,
        "has_more": end < reported_total or not upstream_complete,
        "complete": end >= reported_total and upstream_complete,
        "upstream_complete": upstream_complete,
    }


def validate_category(category):
    if not isinstance(category, str) or not category.strip() or len(category) > 120:
        raise RevitMCPError("invalid_input", "category must be a non-empty string of at most 120 characters")
    return category.strip()


def normalize_parameters(element):
    """Normalize RevitMCP's merged properties/typed_properties without inventing provenance."""
    properties = element.get("properties") or {}
    typed = element.get("typed_properties") or {}
    keys = sorted(set(properties) | set(typed))
    output = []
    for name in keys:
        if name in ("available_parameter_names",):
            continue
        value = properties.get(name)
        meta = typed.get(name) or {}
        storage = meta.get("storage_type")
        has_value = meta.get("has_value")
        if name not in properties and name not in typed:
            value_status = "missing"
        elif storage is None and value in (None, "Not available"):
            value_status = "unavailable"
        elif (isinstance(value, str) and not value.strip()) or has_value is False:
            value_status = "empty"
        else:
            value_status = "populated"
        output.append({
            "name": name,
            "value": value,
            "storage_type": storage,
            "has_value": has_value,
            "is_numeric": meta.get("is_numeric"),
            "numeric_value": meta.get("numeric_value"),
            "value_status": value_status,
            "parameter_scope": "upstream_does_not_report_instance_or_type_source",
            "is_read_only": None,
        })
    return output
