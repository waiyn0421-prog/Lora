import sys
import unittest
from pathlib import Path

PROVIDER_LIB = (Path(__file__).resolve().parents[1] / "revit_identity" /
                "LoraIdentity.extension" / "lib")
if str(PROVIDER_LIB) not in sys.path:
    sys.path.insert(0, str(PROVIDER_LIB))

from lora_identity_provider import (SessionRegistry, SubscriptionTracker,
                                    TARGET_REVIT_VERSIONS, classify_revit_version)


class FakeDocument:
    def __init__(self, title, path="", family=False, underlying=None):
        self.Title = title
        self.PathName = path
        self.IsFamilyDocument = family
        self._underlying = underlying if underlying is not None else object()

    def Equals(self, other):
        return isinstance(other, FakeDocument) and self._underlying is other._underlying


class SessionRegistryTests(unittest.TestCase):
    def registry(self):
        tokens = iter("session-{}".format(n) for n in range(1, 100))
        return SessionRegistry(100, "process-session", "application-session", token_factory=lambda: next(tokens))

    def test_same_active_document_keeps_session_identity(self):
        registry = self.registry()
        doc = FakeDocument("Project", "fixture://Project.rvt")
        first = registry.snapshot(doc, [doc], "2024")["identity"]
        second = registry.snapshot(doc, [doc], "2024")["identity"]
        self.assertEqual(first["document_session_id"], second["document_session_id"])
        self.assertEqual(first["active_document_switch_counter"], 0)

    def test_same_document_different_wrappers_and_views_keep_same_session(self):
        registry = self.registry()
        underlying = object()
        route_doc = FakeDocument("Project", "fixture://Project.rvt", underlying=underlying)
        active_view_doc = FakeDocument("Project", "fixture://Project.rvt", underlying=underlying)
        before = registry.snapshot(route_doc, [route_doc])["identity"]
        registry.view_activated(active_view_doc, [active_view_doc])
        during_view_activation = registry.snapshot(route_doc, [active_view_doc])["identity"]
        after = registry.snapshot(active_view_doc, [active_view_doc])["identity"]
        self.assertEqual(before["document_session_id"], during_view_activation["document_session_id"])
        self.assertEqual(before["document_session_id"], after["document_session_id"])
        self.assertEqual(after["active_document_switch_counter"], 0)
        self.assertEqual(len(registry.snapshot(route_doc, [active_view_doc])["open_document_session_ids"]), 1)

    def test_multiple_open_documents_track_active_switches(self):
        registry = self.registry()
        project, family = FakeDocument("Project"), FakeDocument("Door", family=True)
        registry.document_opened([project, family])
        a = registry.snapshot(project, [project, family])["identity"]
        registry.view_activated(family, [project, family])
        b = registry.snapshot(family, [project, family])["identity"]
        registry.view_activated(project, [project, family])
        c = registry.snapshot(project, [project, family])["identity"]
        self.assertEqual(b["document_type"], "family")
        self.assertNotEqual(a["document_session_id"], b["document_session_id"])
        self.assertEqual(c["document_session_id"], a["document_session_id"])
        self.assertEqual(c["active_document_switch_counter"], 2)
        self.assertEqual(len(registry.snapshot(project, [project, family])["open_document_session_ids"]), 2)

    def test_second_document_open_and_real_switch_are_distinct(self):
        registry = self.registry()
        first_doc = FakeDocument("Same title", "fixture://same.rvt")
        registry.document_opened([first_doc])
        first = registry.snapshot(first_doc, [first_doc])["identity"]
        second_doc = FakeDocument("Same title", "fixture://same.rvt")
        registry.document_opened([first_doc, second_doc])
        second = registry.snapshot(second_doc, [first_doc, second_doc])["identity"]
        self.assertNotEqual(first["document_session_id"], second["document_session_id"])
        self.assertEqual(second["active_document_switch_counter"], 1)

    def test_reopening_same_path_gets_new_document_session(self):
        registry = self.registry()
        original = FakeDocument("Project", "fixture://Project.rvt")
        first = registry.snapshot(original, [original])["identity"]["document_session_id"]
        registry.document_closing()
        registry.document_closed([])
        reopened = FakeDocument("Project", "fixture://Project.rvt")
        second = registry.snapshot(reopened, [reopened])["identity"]["document_session_id"]
        self.assertNotEqual(first, second)

    def test_unsaved_and_family_documents_have_explicit_identity(self):
        registry = self.registry()
        unsaved = FakeDocument("Family1", family=True)
        identity = registry.snapshot(unsaved, [unsaved], "2024")["identity"]
        self.assertIsNone(identity["document_path"])
        self.assertEqual(identity["document_type"], "family")
        self.assertTrue(identity["document_session_id"])

    def test_missing_or_closed_active_document_is_rejected(self):
        registry = self.registry()
        doc = FakeDocument("Closed")
        self.assertEqual(registry.snapshot(None, [])["error"], "document_unavailable")
        self.assertEqual(registry.snapshot(doc, [])["error"], "document_not_open")

    def test_document_closure_invalidates_old_snapshot_generation(self):
        registry = self.registry()
        doc = FakeDocument("Project")
        before = registry.snapshot(doc, [doc])["identity"]
        registry.document_closing()
        after = registry.snapshot(doc, [doc])["identity"]
        self.assertEqual(before["document_session_id"], after["document_session_id"])
        self.assertGreater(after["document_lifecycle_generation"], before["document_lifecycle_generation"])

    def test_closing_inactive_document_preserves_active_document_session(self):
        registry = self.registry()
        active, inactive = FakeDocument("Active"), FakeDocument("Inactive")
        before = registry.snapshot(active, [active, inactive])["identity"]
        registry.document_closing()
        registry.document_closed([active])
        after = registry.snapshot(active, [active])["identity"]
        self.assertEqual(before["document_session_id"], after["document_session_id"])
        self.assertEqual(after["active_document_switch_counter"], 0)
        self.assertEqual(len(registry.snapshot(active, [active])["open_document_session_ids"]), 1)

    def test_closing_active_document_rejects_closed_doc_and_tracks_new_active_doc(self):
        registry = self.registry()
        closing, remaining = FakeDocument("Closing"), FakeDocument("Remaining")
        before = registry.snapshot(closing, [closing, remaining])["identity"]
        registry.document_closing()
        registry.document_closed([remaining])
        self.assertEqual(registry.snapshot(closing, [remaining])["error"], "document_not_open")
        after = registry.snapshot(remaining, [remaining])["identity"]
        self.assertNotEqual(before["document_session_id"], after["document_session_id"])
        self.assertEqual(after["active_document_switch_counter"], 1)

    def test_temporary_null_active_document_does_not_replace_open_session(self):
        registry = self.registry()
        doc = FakeDocument("Project")
        before = registry.snapshot(doc, [doc])["identity"]
        self.assertEqual(registry.snapshot(None, [doc])["error"], "document_unavailable")
        after = registry.snapshot(doc, [doc])["identity"]
        self.assertEqual(before["document_session_id"], after["document_session_id"])

    def test_duplicate_event_subscription_claims_are_blocked(self):
        tracker = SubscriptionTracker()
        self.assertTrue(tracker.claim("ViewActivated"))
        self.assertFalse(tracker.claim("ViewActivated"))
        self.assertEqual(tracker.count("ViewActivated"), 1)
        self.assertEqual(tracker.snapshot(), {"ViewActivated": 1})

    def test_identity_during_view_activation_for_same_document_is_stable(self):
        registry = self.registry()
        underlying = object()
        route_doc = FakeDocument("Project", "fixture://Project.rvt", underlying=underlying)
        view_event_doc = FakeDocument("Project", "fixture://Project.rvt", underlying=underlying)
        initial = registry.snapshot(route_doc, [route_doc])["identity"]
        registry.view_activated(view_event_doc, [view_event_doc])
        response = registry.snapshot(route_doc, [view_event_doc])
        self.assertEqual(response["status"], "success")
        self.assertEqual(response["identity"]["document_session_id"], initial["document_session_id"])

    def test_shutdown_rejects_later_identity_queries(self):
        registry = self.registry()
        doc = FakeDocument("Project")
        registry.snapshot(doc, [doc])
        registry.shutdown()
        self.assertEqual(registry.snapshot(doc, [doc])["error"], "revit_shutdown")

    def test_process_restart_creates_new_process_and_document_sessions(self):
        doc = FakeDocument("Project")
        first = SessionRegistry(100).snapshot(doc, [doc])["identity"]
        second = SessionRegistry(101).snapshot(doc, [doc])["identity"]
        self.assertNotEqual(first["process_session_id"], second["process_session_id"])
        self.assertNotEqual(first["application_session_id"], second["application_session_id"])
        self.assertNotEqual(first["document_session_id"], second["document_session_id"])

    def test_revit_version_compatibility_is_a_target_not_a_runtime_claim(self):
        self.assertEqual(TARGET_REVIT_VERSIONS, (2023, 2024, 2025, 2026))
        for version in TARGET_REVIT_VERSIONS:
            self.assertTrue(classify_revit_version(version)["targeted"])
        self.assertFalse(classify_revit_version(2022)["targeted"])
        self.assertEqual(classify_revit_version("unknown")["status"], "unknown")


if __name__ == "__main__":
    unittest.main()
