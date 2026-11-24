import ast
import hashlib
import runpy
import sys
import tempfile
import unittest
from collections import namedtuple
from pathlib import Path
from types import ModuleType, SimpleNamespace
from unittest.mock import patch


SERVER_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SERVER_ROOT))

from tools import build_read_only_candidate as builder
from tools import read_only_candidate_policy as policy


class ReadOnlyCandidateTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.output = Path(self.temp.name) / "candidate"
        self.source_startup = builder.SOURCE_EXTENSION / "startup.py"
        self.source_hash_before = self._hash(self.source_startup)
        self.wall_source_hash_before = self._hash(
            builder.SOURCE_EXTENSION / "lib" / "lora_identity_provider" / "wall_execution.py")
        builder.build_candidate(self.output)

    def tearDown(self):
        self.temp.cleanup()

    @staticmethod
    def _hash(path):
        return hashlib.sha256(Path(path).read_bytes()).hexdigest()

    def test_empty_route_registry_is_accepted_and_existing_writer_rejected(self):
        policy.assert_registry_empty({})
        with self.assertRaises(policy.RouteRegistryConflict):
            policy.assert_registry_empty({("/execute_walls", "POST"): object()})
        route = namedtuple("Route", "pattern method")("/execute_walls", "POST")
        with self.assertRaises(policy.RouteRegistryConflict):
            policy.assert_registry_empty({route: object()})

    def test_exact_route_allowlist_accepts_only_approved_routes(self):
        exact = {route: object() for route in policy.READ_ONLY_ROUTES}
        policy.assert_exact_read_only_registry(exact)

    def test_route_map_rejects_forbidden_or_unexpected_routes(self):
        exact = {route: object() for route in policy.READ_ONLY_ROUTES}
        extra = dict(exact)
        extra[("/execute_walls", "POST")] = object()
        with self.assertRaises(policy.RouteRegistryConflict):
            policy.assert_exact_read_only_registry(extra)
        missing = dict(exact)
        missing.pop(("/geometry_detail", "POST"))
        with self.assertRaises(policy.RouteRegistryConflict):
            policy.assert_exact_read_only_registry(missing)

    def test_startup_route_registration_matches_manifest_exactly(self):
        manifest = builder.verify_candidate(self.output)
        tree = ast.parse((self.output / "LoraReadOnly.extension" / "startup.py").read_text(encoding="utf-8"))
        found = set()
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            for decorator in node.decorator_list:
                if (isinstance(decorator, ast.Call) and isinstance(decorator.func, ast.Attribute) and
                        decorator.func.attr == "route"):
                    path = ast.literal_eval(decorator.args[0])
                    keywords = {item.arg: ast.literal_eval(item.value) for item in decorator.keywords}
                    for method in keywords["methods"]:
                        found.add((path, method, node.name))
        expected = {(route["path"], route["method"], route["handler"])
                    for route in manifest["included_routes"]}
        self.assertEqual(found, expected)
        self.assertEqual(len(found), 6)

    def test_every_read_route_rechecks_the_exact_registry(self):
        startup = (self.output / "LoraReadOnly.extension" / "startup.py").read_text(encoding="utf-8")
        tree = ast.parse(startup)
        guarded = set()
        for node in ast.walk(tree):
            if not isinstance(node, ast.FunctionDef):
                continue
            if any(isinstance(item, ast.Call) and isinstance(item.func, ast.Name) and
                   item.func.id == "_read_runtime" for item in ast.walk(node)):
                guarded.add(node.name)
        self.assertEqual(guarded, {route["handler"] for route in builder.ROUTES})

    def _run_startup_with_fake_pyrevit(self, existing=None):
        registry = dict(existing or {})
        errors = []

        class FakeAPI(object):
            def __init__(self, name):
                self.name = name

            def route(self, pattern, methods=None):
                def register(handler):
                    for method in methods or ["GET"]:
                        registry[(pattern, method.upper())] = handler
                    return handler
                return register

        routes_module = ModuleType("pyrevit.routes")
        routes_module.__path__ = []
        routes_module.API = FakeAPI
        routes_module.get_routes = lambda name: registry
        routes_server = ModuleType("pyrevit.routes.server")
        routes_server.get_active_server = lambda: SimpleNamespace(host="127.0.0.1")
        userconfig_module = ModuleType("pyrevit.userconfig")
        userconfig_module.user_config = SimpleNamespace(routes_host="127.0.0.1")
        logger = SimpleNamespace(info=lambda *args: None, error=lambda *args: errors.append(args))
        pyrevit_module = ModuleType("pyrevit")
        pyrevit_module.__path__ = []
        pyrevit_module.routes = routes_module
        pyrevit_module.script = SimpleNamespace(get_logger=lambda: logger)
        pyrevit_module.HOST_APP = SimpleNamespace(uiapp=object())

        provider = ModuleType("lora_identity_provider")
        provider.__path__ = []
        runtime_module = ModuleType("lora_identity_provider.runtime")
        runtime_module.get_or_start = lambda uiapp: SimpleNamespace(
            identity_response=lambda app, doc: {"status": "success"})
        security_module = ModuleType("lora_identity_provider.security")
        security_module.REQUIRED_ROUTES_HOST = "127.0.0.1"
        security_module.identity_route_binding_is_safe = lambda server, host: host == "127.0.0.1"
        coordinate_module = ModuleType("lora_identity_provider.coordinate_geometry")
        coordinate_module.coordinate_diagnostic = lambda *args: {"status": "success"}
        coordinate_module.geometry_inventory = lambda *args: {"status": "success"}
        category_module = ModuleType("lora_identity_provider.category_inventory")
        category_module.category_inventory = lambda *args: {"status": "success"}
        geometry_module = ModuleType("lora_identity_provider.geometry_detail")
        geometry_module.geometry_detail = lambda *args: {"status": "success"}
        level_module = ModuleType("lora_identity_provider.level_catalog")
        level_module.level_catalog = lambda *args: {"status": "success"}
        policy_module = ModuleType("lora_identity_provider.read_only_policy")
        policy_module.API_NAME = policy.API_NAME
        policy_module.assert_registry_empty = policy.assert_registry_empty
        policy_module.assert_exact_read_only_registry = policy.assert_exact_read_only_registry
        provider_modules = {
            "lora_identity_provider": provider,
            "lora_identity_provider.runtime": runtime_module,
            "lora_identity_provider.security": security_module,
            "lora_identity_provider.coordinate_geometry": coordinate_module,
            "lora_identity_provider.category_inventory": category_module,
            "lora_identity_provider.geometry_detail": geometry_module,
            "lora_identity_provider.level_catalog": level_module,
            "lora_identity_provider.read_only_policy": policy_module,
        }
        fake_modules = {
            "pyrevit": pyrevit_module,
            "pyrevit.routes": routes_module,
            "pyrevit.routes.server": routes_server,
            "pyrevit.userconfig": userconfig_module,
        }
        fake_modules.update(provider_modules)
        startup = self.output / "LoraReadOnly.extension" / "startup.py"
        with patch.dict(sys.modules, fake_modules):
            namespace = runpy.run_path(str(startup))
        return registry, errors, namespace

    def test_startup_registers_only_read_routes_and_fails_with_existing_writer(self):
        registry, errors, _ = self._run_startup_with_fake_pyrevit()
        self.assertEqual(policy.route_specs(registry), policy.READ_ONLY_ROUTES)
        self.assertEqual(errors, [])

        writer = ("/execute_walls", "POST")
        existing = {writer: object()}
        registry, errors, _ = self._run_startup_with_fake_pyrevit(existing)
        self.assertEqual(registry, existing)
        self.assertEqual(len(errors), 1)
        self.assertIn("failed closed", errors[0][0])

    def test_write_routes_and_transaction_provider_are_unavailable(self):
        extension = self.output / "LoraReadOnly.extension"
        startup = (extension / "startup.py").read_text(encoding="utf-8")
        provider_dir = extension / "lib" / "lora_identity_provider"
        self.assertFalse((provider_dir / "wall_execution.py").exists())
        self.assertNotIn("wall_execution", startup)
        forbidden_routes = {"/validate_walls", "/execute_walls", "/verify_walls", "/execution_status"}
        tree = ast.parse(startup)
        registered_paths = set()
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            for decorator in node.decorator_list:
                if (isinstance(decorator, ast.Call) and isinstance(decorator.func, ast.Attribute) and
                        decorator.func.attr == "route"):
                    registered_paths.add(ast.literal_eval(decorator.args[0]))
        self.assertFalse(registered_paths & forbidden_routes)
        for path in provider_dir.glob("*.py"):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    self.assertFalse(any(alias.name.endswith("wall_execution") for alias in node.names))
                if isinstance(node, ast.ImportFrom):
                    self.assertNotEqual(node.module, "wall_execution")
                if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
                    self.assertNotIn(node.func.id, {"Transaction", "SubTransaction", "TransactionGroup"})
                if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                    self.assertNotIn(node.func.attr, {"Delete", "Commit", "RollBack"})
                    if node.func.attr == "Create" and isinstance(node.func.value, ast.Name):
                        self.assertNotEqual(node.func.value.id, "Wall")
                    self.assertNotEqual(node.func.attr, "Set")

    def test_manifest_hashes_and_unexpected_files_are_checked(self):
        manifest = builder.verify_candidate(self.output)
        self.assertTrue(manifest["read_only"])
        with self.assertRaises(FileExistsError):
            builder.build_candidate(self.output)
        extra = self.output / "unexpected.py"
        extra.write_text("pass\n", encoding="utf-8")
        with self.assertRaises(ValueError):
            builder.verify_candidate(self.output)

    def test_manifest_rejects_modified_artifact_content(self):
        target = self.output / "LoraReadOnly.extension" / "startup.py"
        target.write_text(target.read_text(encoding="utf-8") + "# altered\n", encoding="utf-8")
        with self.assertRaises(ValueError):
            builder.verify_candidate(self.output)

    def test_manifest_declares_inclusions_and_all_excluded_execution_routes(self):
        manifest = builder.verify_candidate(self.output)
        self.assertEqual({(r["path"], r["method"]) for r in manifest["included_routes"]},
                         set(policy.READ_ONLY_ROUTES))
        excluded = {(r["path"], r["method"]) for r in manifest["excluded_routes"]}
        self.assertTrue({("/validate_walls", "POST"), ("/execute_walls", "POST"),
                         ("/verify_walls", "POST"), ("/execution_status", "GET")} <= excluded)

    def test_candidate_build_does_not_change_development_or_installed_functionality_sources(self):
        self.assertEqual(self._hash(self.source_startup), self.source_hash_before)
        self.assertEqual(self._hash(builder.SOURCE_EXTENSION / "lib" / "lora_identity_provider" /
                                    "wall_execution.py"), self.wall_source_hash_before)
        self.assertTrue((builder.SOURCE_EXTENSION / "startup.py").exists())
        self.assertTrue((builder.SOURCE_EXTENSION / "lib" / "lora_identity_provider" /
                         "wall_execution.py").exists())


if __name__ == "__main__":
    unittest.main()
