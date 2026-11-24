"""Revit-side identity provider. Kept independent from external MCP processes."""

from .registry import SessionRegistry
from .compatibility import TARGET_REVIT_VERSIONS, classify_revit_version
from .security import REQUIRED_ROUTES_HOST, identity_route_binding_is_safe
from .registry import SubscriptionTracker, same_document_instance

__all__ = ["SessionRegistry", "TARGET_REVIT_VERSIONS", "classify_revit_version",
           "REQUIRED_ROUTES_HOST", "identity_route_binding_is_safe", "SubscriptionTracker",
           "same_document_instance"]
