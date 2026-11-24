"""pyRevit event and Routes adapter for SessionRegistry."""

from Autodesk.Revit.UI import Events as UIEvents
from Autodesk.Revit.DB import Events as DBEvents
from System import Object
from System.Diagnostics import Process
from pyrevit import framework
from pyrevit.coreutils import envvars

from .compatibility import classify_revit_version
from .registry import SessionRegistry, SubscriptionTracker, same_document_instance


_runtime_instance = None
_RUNTIME_ENVVAR = "LORA_IDENTITY_PROVIDER_RUNTIME"


def _same_document(left, right):
    return same_document_instance(left, right)


def _reference_equal(left, right):
    try:
        return bool(Object.ReferenceEquals(left, right))
    except Exception:
        return left is right


class Runtime(object):
    def __init__(self, uiapp):
        self.uiapp = uiapp
        self.registry = SessionRegistry(Process.GetCurrentProcess().Id, same_document=_same_document)
        self._handlers = []
        self._subscriptions = SubscriptionTracker()
        self._event_counts = {name: 0 for name in (
            "DocumentOpened", "DocumentClosing", "DocumentClosed", "ViewActivated", "ApplicationClosing")}
        self._shutdown = False
        self._execution_receipts = {}

    def _subscribe(self, source, event_name, callback):
        event_types = {
            "DocumentOpened": DBEvents.DocumentOpenedEventArgs,
            "DocumentClosing": DBEvents.DocumentClosingEventArgs,
            "DocumentClosed": DBEvents.DocumentClosedEventArgs,
            "ViewActivated": UIEvents.ViewActivatedEventArgs,
            "ApplicationClosing": UIEvents.ApplicationClosingEventArgs,
        }
        if not self._subscriptions.claim(event_name):
            return False
        try:
            handler = framework.EventHandler[event_types[event_name]](callback)
            if event_name == "DocumentOpened":
                source.DocumentOpened += handler
            elif event_name == "DocumentClosing":
                source.DocumentClosing += handler
            elif event_name == "DocumentClosed":
                source.DocumentClosed += handler
            elif event_name == "ViewActivated":
                source.ViewActivated += handler
            elif event_name == "ApplicationClosing":
                source.ApplicationClosing += handler
        except Exception:
            self._subscriptions.release(event_name)
            raise
        self._handlers.append((source, event_name, handler))
        return True

    def start(self):
        app = self.uiapp.Application
        compatibility = classify_revit_version(app.VersionNumber)
        if not compatibility["targeted"]:
            raise RuntimeError("Unsupported Revit version for Lora identity provider")
        if self._shutdown:
            raise RuntimeError("A stopped identity provider cannot be restarted")
        try:
            self._subscribe(app, "DocumentOpened", self._on_document_opened)
            self._subscribe(app, "DocumentClosing", self._on_document_closing)
            self._subscribe(app, "DocumentClosed", self._on_document_closed)
            self._subscribe(self.uiapp, "ViewActivated", self._on_view_activated)
            self._subscribe(self.uiapp, "ApplicationClosing", self._on_application_closing)
            self.registry.sync_open_documents(list(app.Documents))
            try:
                active = self.uiapp.ActiveUIDocument
                self.registry.view_activated(active.Document if active else None, list(app.Documents))
            except Exception:
                pass
        except Exception:
            self.stop()
            raise
        return self

    def stop(self):
        if self._shutdown:
            return
        self._shutdown = True
        for source, event_name, handler in reversed(self._handlers):
            try:
                if event_name == "DocumentOpened":
                    source.DocumentOpened -= handler
                elif event_name == "DocumentClosing":
                    source.DocumentClosing -= handler
                elif event_name == "DocumentClosed":
                    source.DocumentClosed -= handler
                elif event_name == "ViewActivated":
                    source.ViewActivated -= handler
                elif event_name == "ApplicationClosing":
                    source.ApplicationClosing -= handler
            except Exception:
                pass
        self._handlers = []
        self._subscriptions.clear()
        self.registry.shutdown()
        self._execution_receipts = {}
        try:
            envvars.set_pyrevit_env_var(_RUNTIME_ENVVAR, None)
        except Exception:
            pass

    def _documents(self):
        try:
            return list(self.uiapp.Application.Documents)
        except Exception:
            return []

    def _on_document_opened(self, sender, args):
        self._event_counts["DocumentOpened"] += 1
        self.registry.document_opened(self._documents())

    def _on_document_closing(self, sender, args):
        self._event_counts["DocumentClosing"] += 1
        self.registry.document_closing()

    def _on_document_closed(self, sender, args):
        self._event_counts["DocumentClosed"] += 1
        self.registry.document_closed(self._documents())

    def _on_view_activated(self, sender, args):
        self._event_counts["ViewActivated"] += 1
        try:
            view = args.CurrentActiveView
            document = view.Document if view else None
        except Exception:
            document = None
        self.registry.view_activated(document, self._documents())

    def _on_application_closing(self, sender, args):
        self._event_counts["ApplicationClosing"] += 1
        self.stop()

    def identity_response(self, uiapp, active_document):
        if self._shutdown or self.registry.is_shutdown:
            return {"status": "error", "error": "revit_shutdown", "identity": None,
                    "diagnostics": self._identity_diagnostics(active_document, None, [], 0)}
        view_count_before = self._event_counts["ViewActivated"]
        try:
            active_uidoc = uiapp.ActiveUIDocument
            current_active_document = active_uidoc.Document if active_uidoc else None
        except Exception:
            current_active_document = None
        documents = self._documents()
        diagnostics = self._identity_diagnostics(active_document, current_active_document,
                                                 documents, view_count_before)
        if current_active_document is None:
            diagnostics["failure_reason"] = "no_active_uidocument_at_verification"
            return {"status": "error", "error": "document_unavailable", "identity": None,
                    "diagnostics": diagnostics}
        if active_document is None:
            diagnostics["failure_reason"] = "route_context_document_is_null"
            return {"status": "error", "error": "document_unavailable", "identity": None,
                    "diagnostics": diagnostics}
        if not diagnostics["same_revit_document"]:
            diagnostics["failure_reason"] = "route_context_and_active_uidocument_are_not_equal"
            return {"status": "error", "error": "active_document_changed", "identity": None,
                    "diagnostics": diagnostics}
        result = self.registry.snapshot(active_document, documents, uiapp.Application.VersionNumber)
        if result.get("status") != "success":
            diagnostics["failure_reason"] = result.get("error")
            return dict(result, diagnostics=diagnostics)
        diagnostics["registry_contains_active_document"] = self.registry.contains_document(current_active_document)
        diagnostics["event_counts_after_identity_read"] = dict(self._event_counts)
        diagnostics["view_activated_events_during_identity_read"] = (
            self._event_counts["ViewActivated"] - view_count_before)
        result["diagnostics"] = diagnostics
        return result

    def get_execution_receipt(self, request_id):
        return self._execution_receipts.get(request_id)

    def store_execution_receipt(self, request_id, request_hash, state, response):
        self._execution_receipts[request_id] = {"request_hash": request_hash,
                                                "state": state, "response": response}

    def _identity_diagnostics(self, captured_document, current_document, documents, view_count_before):
        captured_title = _safe_document_value(captured_document, "Title")
        current_title = _safe_document_value(current_document, "Title")
        captured_path = _safe_document_value(captured_document, "PathName")
        current_path = _safe_document_value(current_document, "PathName")
        open_match = bool(captured_document is not None and
                          any(_same_document(captured_document, document) for document in documents))
        return {
            "captured_document_present": captured_document is not None,
            "current_active_document_present": current_document is not None,
            "captured_document_reference_equal": _reference_equal(captured_document, current_document),
            "same_revit_document": _same_document(captured_document, current_document),
            "captured_document_is_open": open_match,
            "captured_and_current_title_match": (captured_title == current_title if captured_title is not None and current_title is not None else None),
            "captured_and_current_path_match": (captured_path == current_path if captured_path is not None and current_path is not None else None),
            "captured_document_valid": _safe_document_valid(captured_document),
            "current_document_valid": _safe_document_valid(current_document),
            "event_handler_count": len(self._handlers),
            "event_subscriptions": self._subscriptions.snapshot(),
            "event_counts_at_request_start": dict(self._event_counts),
            "view_activated_events_during_identity_read": 0,
        }


def get_or_start(uiapp):
    """Reuse a single monitor per loaded Revit Python process/module runtime."""
    global _runtime_instance
    current = envvars.get_pyrevit_env_var(_RUNTIME_ENVVAR)
    if current is None:
        current = _runtime_instance
    same_process = False
    if current is not None and not current._shutdown:
        try:
            same_process = current.registry.process_id == Process.GetCurrentProcess().Id
        except Exception:
            same_process = False
    if current is not None and same_process:
        return current
    if current is not None:
        try:
            current.stop()
        except Exception:
            pass
    runtime = Runtime(uiapp).start()
    _runtime_instance = runtime
    envvars.set_pyrevit_env_var(_RUNTIME_ENVVAR, runtime)
    return runtime


def _safe_document_value(document, name):
    if document is None:
        return None
    try:
        value = getattr(document, name)
        return str(value) if value not in (None, "") else None
    except Exception:
        return None


def _safe_document_valid(document):
    if document is None:
        return None
    try:
        return bool(document.IsValidObject)
    except Exception:
        return None
