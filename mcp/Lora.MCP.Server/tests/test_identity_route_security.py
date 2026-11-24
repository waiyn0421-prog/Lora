import sys
import unittest
from pathlib import Path

PROVIDER_LIB = (Path(__file__).resolve().parents[1] / "revit_identity" /
                "LoraIdentity.extension" / "lib")
if str(PROVIDER_LIB) not in sys.path:
    sys.path.insert(0, str(PROVIDER_LIB))

from lora_identity_provider import identity_route_binding_is_safe


class FakeRoutesServer:
    def __init__(self, host):
        self.host = host


class IdentityRouteSecurityTests(unittest.TestCase):
    def test_configured_ipv4_loopback_is_allowed_before_server_start(self):
        self.assertTrue(identity_route_binding_is_safe(None, "127.0.0.1"))

    def test_actual_active_server_binding_overrides_safe_config(self):
        self.assertFalse(identity_route_binding_is_safe(FakeRoutesServer("0.0.0.0"), "127.0.0.1"))
        self.assertTrue(identity_route_binding_is_safe(FakeRoutesServer("127.0.0.1"), "0.0.0.0"))

    def test_wildcard_empty_hostname_and_other_interfaces_are_rejected(self):
        for host in (None, "", "0.0.0.0", "::", "::1", "localhost", "203.0.113.8"):
            self.assertFalse(identity_route_binding_is_safe(None, host), repr(host))

    def test_non_loopback_active_listener_never_registers_identity_route(self):
        for host in ("0.0.0.0", "203.0.113.8", "::"):
            self.assertFalse(identity_route_binding_is_safe(FakeRoutesServer(host), "127.0.0.1"), host)


if __name__ == "__main__":
    unittest.main()
