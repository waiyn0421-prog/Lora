import importlib
import sys
import types
import unittest
from unittest.mock import patch

PROVIDER_LIB = str(__import__("pathlib").Path(__file__).resolve().parents[1] /
                   "revit_identity" / "LoraIdentity.extension" / "lib")
if PROVIDER_LIB not in sys.path:
    sys.path.insert(0, PROVIDER_LIB)


class FakeEvent:
    def __init__(self):
        self.handlers = []

    def __iadd__(self, handler):
        self.handlers.append(handler)
        return self

    def __isub__(self, handler):
        self.handlers.remove(handler)
        return self

    def fire(self, sender=None, args=None):
        for handler in list(self.handlers):
            handler(sender, args)


class FakeDocument:
    Title = "Sample"
    PathName = "fixture://Sample.rvt"
    IsFamilyDocument = False
    IsValidObject = True

    def Equals(self, other):
        return self is other


class FakeApplication:
    VersionNumber = "2024"

    def __init__(self, documents):
        self.DocumentOpened = FakeEvent()
        self.DocumentClosing = FakeEvent()
        self.DocumentClosed = FakeEvent()
        self.ApplicationClosing = FakeEvent()
        self._documents = documents
        self.documents_error = None

    @property
    def Documents(self):
        if self.documents_error:
            raise RuntimeError("document list failed")
        return self._documents


class FakeUIApp:
    def __init__(self, application, document):
        self.Application = application
        self.ViewActivated = FakeEvent()
        self.ApplicationClosing = FakeEvent()
        self.ActiveUIDocument = types.SimpleNamespace(Document=document)


class FakeHandlerFactory:
    def __getitem__(self, event_args_type):
        return lambda callback: callback


def runtime_module_stubs():
    db_events = types.SimpleNamespace(
        DocumentOpenedEventArgs=type("DocumentOpenedEventArgs", (), {}),
        DocumentClosingEventArgs=type("DocumentClosingEventArgs", (), {}),
        DocumentClosedEventArgs=type("DocumentClosedEventArgs", (), {}))
    ui_events = types.SimpleNamespace(
        ViewActivatedEventArgs=type("ViewActivatedEventArgs", (), {}),
        ApplicationClosingEventArgs=type("ApplicationClosingEventArgs", (), {}))
    db = types.ModuleType("Autodesk.Revit.DB")
    db.Events = db_events
    ui = types.ModuleType("Autodesk.Revit.UI")
    ui.Events = ui_events
    revit = types.ModuleType("Autodesk.Revit")
    revit.DB = db
    revit.UI = ui
    autodesk = types.ModuleType("Autodesk")
    autodesk.Revit = revit

    system = types.ModuleType("System")
    system_object = type("Object", (), {})
    system_object.ReferenceEquals = staticmethod(lambda left, right: left is right)
    system.Object = system_object
    system_diagnostics = types.ModuleType("System.Diagnostics")
    system_diagnostics.Process = types.SimpleNamespace(
        GetCurrentProcess=staticmethod(lambda: types.SimpleNamespace(Id=99)))

    env = {}
    envvars = types.ModuleType("pyrevit.coreutils.envvars")
    envvars.get_pyrevit_env_var = lambda name: env.get(name)
    envvars.set_pyrevit_env_var = lambda name, value: env.__setitem__(name, value)
    framework = types.SimpleNamespace(EventHandler=FakeHandlerFactory())
    pyrevit = types.ModuleType("pyrevit")
    pyrevit.framework = framework
    coreutils = types.ModuleType("pyrevit.coreutils")
    coreutils.envvars = envvars
    return {"Autodesk": autodesk, "Autodesk.Revit": revit,
            "Autodesk.Revit.DB": db, "Autodesk.Revit.UI": ui,
            "System": system, "System.Diagnostics": system_diagnostics,
            "pyrevit": pyrevit, "pyrevit.coreutils": coreutils,
            "pyrevit.coreutils.envvars": envvars}


class RuntimeLifecycleTests(unittest.TestCase):
    def load_runtime(self):
        with patch.dict(sys.modules, runtime_module_stubs()):
            sys.modules.pop("lora_identity_provider.runtime", None)
            return importlib.import_module("lora_identity_provider.runtime")

    def test_start_is_idempotent_and_application_shutdown_unsubscribes_every_handler(self):
        runtime_module = self.load_runtime()
        document = FakeDocument()
        application = FakeApplication([document])
        uiapp = FakeUIApp(application, document)
        runtime = runtime_module.Runtime(uiapp)
        runtime.start()
        runtime.start()
        self.assertEqual(len(runtime._handlers), 5)
        self.assertEqual(sum(len(getattr(source, event_name).handlers)
                             for source, event_name, _ in runtime._handlers), 5)

        uiapp.ApplicationClosing.fire()

        self.assertTrue(runtime._shutdown)
        self.assertTrue(runtime.registry.is_shutdown)
        self.assertEqual(runtime._handlers, [])
        self.assertEqual(runtime._subscriptions.snapshot(), {})
        self.assertTrue(all(not getattr(source, event_name).handlers for source, event_name, _ in (
            (application, "DocumentOpened", None), (application, "DocumentClosing", None),
            (application, "DocumentClosed", None), (uiapp, "ViewActivated", None),
            (uiapp, "ApplicationClosing", None))))
        self.assertEqual(runtime._event_counts["ApplicationClosing"], 1)

    def test_partial_start_failure_removes_already_subscribed_handlers(self):
        runtime_module = self.load_runtime()
        document = FakeDocument()
        application = FakeApplication([document])
        uiapp = FakeUIApp(application, document)
        application.documents_error = True
        runtime = runtime_module.Runtime(uiapp)

        with self.assertRaises(RuntimeError):
            runtime.start()

        self.assertTrue(runtime._shutdown)
        self.assertEqual(runtime._handlers, [])
        self.assertTrue(all(not getattr(source, event_name).handlers for source, event_name, _ in (
            (application, "DocumentOpened", None), (application, "DocumentClosing", None),
            (application, "DocumentClosed", None), (uiapp, "ViewActivated", None),
            (uiapp, "ApplicationClosing", None))))


if __name__ == "__main__":
    unittest.main()
