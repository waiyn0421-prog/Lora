"""Testable session registry; the host adapter supplies Revit document references."""

import uuid


class SessionRegistry(object):
    """Assign one unpredictable token per open-document object lifecycle."""

    def __init__(self, process_id, process_session_id=None, application_session_id=None,
                 token_factory=None, same_document=None):
        self.process_id = int(process_id)
        self.process_session_id = process_session_id or str(uuid.uuid4())
        self.application_session_id = application_session_id or str(uuid.uuid4())
        self._new_token = token_factory or (lambda: str(uuid.uuid4()))
        self._same_document = same_document or same_document_instance
        self._documents = []
        self._active_session_id = None
        self._active_switch_counter = 0
        self._lifecycle_counter = 0
        self._shutdown = False

    def _find(self, document):
        for entry in self._documents:
            if self._same_document(entry["document"], document):
                return entry
        return None

    def contains_document(self, document):
        return self._find(document) is not None

    def _add(self, document):
        entry = {"document": document, "document_session_id": self._new_token(),
                 "opened_sequence": self._lifecycle_counter}
        self._lifecycle_counter += 1
        self._documents.append(entry)
        return entry

    def sync_open_documents(self, documents):
        """Reconcile a small Revit open-document collection after open/close events."""
        if self._shutdown:
            return
        current = list(documents or [])
        self._documents = [entry for entry in self._documents
                           if any(self._same_document(entry["document"], document) for document in current)]
        for document in current:
            if self._find(document) is None:
                self._add(document)

    def document_opened(self, documents):
        self.sync_open_documents(documents)

    def document_closing(self):
        """Invalidate in-flight snapshots even if Revit later cancels the close."""
        if not self._shutdown:
            self._lifecycle_counter += 1

    def document_closed(self, documents):
        if not self._shutdown:
            self.sync_open_documents(documents)
            self._lifecycle_counter += 1

    def _active_entry(self, document):
        if document is None:
            return None
        entry = self._find(document)
        if entry is None:
            entry = self._add(document)
        if self._active_session_id != entry["document_session_id"]:
            if self._active_session_id is not None:
                self._active_switch_counter += 1
            self._active_session_id = entry["document_session_id"]
        return entry

    def view_activated(self, document, documents):
        if self._shutdown:
            return
        self.sync_open_documents(documents)
        self._active_entry(document)

    def snapshot(self, active_document, documents, revit_version=None):
        if self._shutdown:
            return {"status": "error", "error": "revit_shutdown", "identity": None}
        self.sync_open_documents(documents)
        if active_document is None:
            return {"status": "error", "error": "document_unavailable", "identity": None}
        if not any(self._same_document(active_document, document) for document in (documents or [])):
            return {"status": "error", "error": "document_not_open", "identity": None}
        entry = self._active_entry(active_document)
        document = entry["document"]
        is_family = bool(getattr(document, "IsFamilyDocument", False))
        identity = {
            "process_id": self.process_id,
            "process_session_id": self.process_session_id,
            "application_session_id": self.application_session_id,
            "document_session_id": entry["document_session_id"],
            "document_lifecycle_sequence": entry["opened_sequence"],
            "document_lifecycle_generation": self._lifecycle_counter,
            "active_document_switch_counter": self._active_switch_counter,
            "active_document": True,
            "document_title": _safe_attr(document, "Title"),
            "document_path": _safe_attr(document, "PathName"),
            "document_type": "family" if is_family else "project",
            "revit_version": revit_version,
            "identity_provider_version": 1,
            "identity_source": "revit_side_open_document_session",
            "document_status": "open",
        }
        return {"status": "success", "identity": identity,
                "open_document_session_ids": [item["document_session_id"] for item in self._documents]}

    def shutdown(self):
        self._shutdown = True
        self._active_session_id = None
        self._documents = []
        self._lifecycle_counter += 1

    @property
    def is_shutdown(self):
        return self._shutdown


def _safe_attr(value, name):
    try:
        result = getattr(value, name)
        return str(result) if result not in (None, "") else None
    except Exception:
        return None


def same_document_instance(left, right):
    """Compare Revit Document wrappers by Revit's lifecycle-aware equality."""
    if left is None or right is None:
        return left is right
    if left is right:
        return True
    try:
        # Autodesk.Revit.DB.Document.Equals explicitly equates wrappers for
        # the same currently-open document, without equating a reopened file.
        return bool(left.Equals(right))
    except Exception:
        # Fail closed: title/path equality is not document identity.
        return False


class SubscriptionTracker(object):
    """Keep one subscription per Revit event name and expose safe counts."""

    def __init__(self):
        self._events = set()

    def claim(self, event_name):
        if event_name in self._events:
            return False
        self._events.add(event_name)
        return True

    def release(self, event_name):
        self._events.discard(event_name)

    def clear(self):
        self._events.clear()

    def count(self, event_name):
        return 1 if event_name in self._events else 0

    def snapshot(self):
        return {event_name: 1 for event_name in sorted(self._events)}
