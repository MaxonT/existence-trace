#!/usr/bin/env python3
"""What: a local pricing page with a Subscribe button wired to Stripe Checkout.
Why: the first paid tier of existence-trace (the 30-day test of one number: the first paying user).
How: python3 pay/server.py  (standard library only). Open http://127.0.0.1:8787.

Keys come only from the environment (copy .env.example, never commit real keys):
  STRIPE_SECRET_KEY      sk_test_... (sk_live_... only when you switch to real mode)
  STRIPE_PRICE_ID        price_... of the paid plan (a recurring price)
  STRIPE_WEBHOOK_SECRET  whsec_... used to verify POST /webhook
Optional: PAY_PORT (8787), PAY_BASE_URL (http://127.0.0.1:<port>),
  PAY_PRICE_LABEL (the price shown on the page), STRIPE_API_BASE (tests only).
Without STRIPE_SECRET_KEY the page still renders and /checkout answers 503,
so nothing is ever charged by accident. Logs are one JSON line per request.
"""
import hashlib
import hmac
import html
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

HERE = Path(__file__).resolve().parent
DEFAULT_PRICE_LABEL = "$5 / month (placeholder)"
STRIPE_TIMEOUT = 10  # seconds per Stripe call
STRIPE_RETRIES = 2  # extra tries on network errors or 5xx, with backoff
SIGNATURE_TOLERANCE = 300  # seconds, Stripe's default


def log(**fields):
    print(json.dumps({"ts": time.strftime("%Y-%m-%dT%H:%M:%S%z"), **fields}), flush=True)


def settings(env=None):
    env = os.environ if env is None else env
    port = int(env.get("PAY_PORT", "8787"))
    return {
        "port": port,
        "base_url": env.get("PAY_BASE_URL", f"http://127.0.0.1:{port}").rstrip("/"),
        "secret_key": env.get("STRIPE_SECRET_KEY", ""),
        "price_id": env.get("STRIPE_PRICE_ID", ""),
        "webhook_secret": env.get("STRIPE_WEBHOOK_SECRET", ""),
        "price_label": env.get("PAY_PRICE_LABEL", DEFAULT_PRICE_LABEL),
        "api_base": env.get("STRIPE_API_BASE", "https://api.stripe.com").rstrip("/"),
    }


def create_checkout_session(cfg):
    """POST /v1/checkout/sessions and return the hosted Checkout URL."""
    form = urllib.parse.urlencode({
        "mode": "subscription",
        "line_items[0][price]": cfg["price_id"],
        "line_items[0][quantity]": "1",
        "success_url": cfg["base_url"] + "/success?session_id={CHECKOUT_SESSION_ID}",
        "cancel_url": cfg["base_url"] + "/",
    }).encode()
    last = None
    for attempt in range(STRIPE_RETRIES + 1):
        req = urllib.request.Request(cfg["api_base"] + "/v1/checkout/sessions", data=form, method="POST",
                                     headers={"Authorization": "Bearer " + cfg["secret_key"],
                                              "Content-Type": "application/x-www-form-urlencoded"})
        try:
            with urllib.request.urlopen(req, timeout=STRIPE_TIMEOUT) as r:
                return json.loads(r.read())["url"]
        except urllib.error.HTTPError as e:
            if e.code < 500:
                raise  # a 4xx (bad key, bad price) will not fix itself
            last = e
        except (urllib.error.URLError, TimeoutError) as e:
            last = e
        time.sleep(0.5 * 2 ** attempt)
    raise last


def verify_signature(payload, header, secret, now=None):
    """Stripe-Signature: t=<ts>,v1=<hex hmac-sha256 of '<ts>.<payload>'>."""
    parts = dict(p.split("=", 1) for p in header.split(",") if "=" in p)
    try:
        ts = int(parts["t"])
    except (KeyError, ValueError):
        return False
    if abs((now or time.time()) - ts) > SIGNATURE_TOLERANCE:
        return False
    expected = hmac.new(secret.encode(), f"{ts}.".encode() + payload, hashlib.sha256).hexdigest()
    return any(hmac.compare_digest(expected, v) for k, v in
               (p.split("=", 1) for p in header.split(",") if "=" in p) if k == "v1")


def make_handler(cfg):
    page = (HERE / "pricing.html").read_text(encoding="utf-8")

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass  # replaced by one JSON line per request

        def send(self, code, body, ctype="text/html; charset=utf-8", headers=None):
            data = body.encode() if isinstance(body, str) else body
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(data)))
            for k, v in (headers or {}).items():
                self.send_header(k, v)
            self.end_headers()
            self.wfile.write(data)
            log(method=self.command, path=self.path, status=code)

        def do_GET(self):
            path = self.path.split("?", 1)[0]
            if path == "/":
                configured = "yes" if cfg["secret_key"] and cfg["price_id"] else "no"
                self.send(200, page.replace("{{PRICE_LABEL}}", html.escape(cfg["price_label"]))
                          .replace("{{CONFIGURED}}", configured))
            elif path == "/success":
                self.send(200, "<!doctype html><meta charset=utf-8><title>Thank you</title>"
                               "<p>Payment received. Thank you for supporting existence-trace.</p>")
            elif path == "/health":
                self.send(200, json.dumps({"ok": True, "payments_configured": bool(cfg["secret_key"])}),
                          "application/json")
            else:
                self.send(404, "not found", "text/plain")

        def do_POST(self):
            length = int(self.headers.get("Content-Length") or 0)
            body = self.rfile.read(length) if length else b""
            if self.path == "/checkout":
                if not (cfg["secret_key"] and cfg["price_id"]):
                    return self.send(503, json.dumps({"error": "payments not configured: set STRIPE_SECRET_KEY "
                                                      "and STRIPE_PRICE_ID"}), "application/json")
                try:
                    url = create_checkout_session(cfg)
                except Exception as e:
                    log(event="checkout_failed", error=type(e).__name__)
                    return self.send(502, json.dumps({"error": "Stripe checkout failed"}), "application/json")
                return self.send(303, "", headers={"Location": url})
            if self.path == "/webhook":
                if not cfg["webhook_secret"] or not verify_signature(
                        body, self.headers.get("Stripe-Signature", ""), cfg["webhook_secret"]):
                    return self.send(400, json.dumps({"error": "bad signature"}), "application/json")
                event = json.loads(body or b"{}")
                log(event="stripe_webhook", type=event.get("type"), id=event.get("id"))
                return self.send(200, json.dumps({"received": True}), "application/json")
            self.send(404, "not found", "text/plain")

    return Handler


def serve(env=None, port=None):
    cfg = settings(env)
    return ThreadingHTTPServer(("127.0.0.1", cfg["port"] if port is None else port), make_handler(cfg))


def main():
    server = serve()
    cfg = settings()
    log(event="start", url=f"http://127.0.0.1:{server.server_address[1]}",
        payments_configured=bool(cfg["secret_key"] and cfg["price_id"]),
        mode="live" if cfg["secret_key"].startswith("sk_live_") else "test")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
