"""Tests for pay/server.py against a local fake Stripe. Never reaches the real network."""
import hashlib
import hmac
import importlib.util
import json
import threading
import time
import unittest
import urllib.error
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

SPEC = importlib.util.spec_from_file_location("pay_server", Path(__file__).resolve().parents[1] / "pay" / "server.py")
pay = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(pay)


class FakeStripe(BaseHTTPRequestHandler):
    calls = []

    def log_message(self, *args):
        pass

    def do_POST(self):
        body = self.rfile.read(int(self.headers["Content-Length"])).decode()
        FakeStripe.calls.append({"path": self.path, "auth": self.headers["Authorization"],
                                 "form": urllib.parse.parse_qs(body)})
        data = json.dumps({"id": "cs_test_1", "url": "https://checkout.example.test/cs_test_1"}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args):
        return None


def start(server):
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return f"http://127.0.0.1:{server.server_address[1]}"


class PayServerTests(unittest.TestCase):
    def setUp(self):
        FakeStripe.calls = []
        self.stripe = ThreadingHTTPServer(("127.0.0.1", 0), FakeStripe)
        self.stripe_url = start(self.stripe)
        self.servers = [self.stripe]

    def tearDown(self):
        for s in self.servers:
            s.shutdown()
            s.server_close()

    def app(self, **env):
        server = pay.serve({"STRIPE_API_BASE": self.stripe_url, **env}, port=0)
        self.servers.append(server)
        return start(server)

    def post(self, url, data=b"", headers=None):
        opener = urllib.request.build_opener(NoRedirect)
        try:
            return opener.open(urllib.request.Request(url, data=data, method="POST", headers=headers or {}), timeout=5)
        except urllib.error.HTTPError as e:
            return e

    def test_pricing_page_shows_free_and_paid_plans(self):
        base = self.app(PAY_PRICE_LABEL="$9 / month")
        page = urllib.request.urlopen(base + "/", timeout=5).read().decode()
        self.assertIn("Free", page)
        self.assertIn("$9 / month", page)
        self.assertIn('action="/checkout"', page)
        self.assertIn("Payments configured: no", page)

    def test_checkout_without_keys_is_503_and_calls_nothing(self):
        r = self.post(self.app() + "/checkout")
        self.assertEqual(r.status if hasattr(r, "status") else r.code, 503)
        self.assertEqual(FakeStripe.calls, [])

    def test_checkout_redirects_to_stripe_session(self):
        base = self.app(STRIPE_SECRET_KEY="sk_test_x", STRIPE_PRICE_ID="price_x")
        r = self.post(base + "/checkout")
        self.assertEqual(r.code, 303)
        self.assertEqual(r.headers["Location"], "https://checkout.example.test/cs_test_1")
        call = FakeStripe.calls[0]
        self.assertEqual(call["path"], "/v1/checkout/sessions")
        self.assertEqual(call["auth"], "Bearer sk_test_x")
        self.assertEqual(call["form"]["mode"], ["subscription"])
        self.assertEqual(call["form"]["line_items[0][price]"], ["price_x"])

    def test_webhook_accepts_valid_signature_only(self):
        base = self.app(STRIPE_WEBHOOK_SECRET="whsec_x")
        body = json.dumps({"id": "evt_1", "type": "checkout.session.completed"}).encode()
        ts = int(time.time())
        sig = hmac.new(b"whsec_x", f"{ts}.".encode() + body, hashlib.sha256).hexdigest()
        ok = self.post(base + "/webhook", body, {"Stripe-Signature": f"t={ts},v1={sig}"})
        self.assertEqual(ok.status, 200)
        bad = self.post(base + "/webhook", body, {"Stripe-Signature": f"t={ts},v1={'0' * 64}"})
        self.assertEqual(bad.code, 400)

    def test_old_signature_is_rejected(self):
        body = b"{}"
        ts = int(time.time()) - 3600
        sig = hmac.new(b"s", f"{ts}.".encode() + body, hashlib.sha256).hexdigest()
        self.assertFalse(pay.verify_signature(body, f"t={ts},v1={sig}", "s"))


if __name__ == "__main__":
    unittest.main()
