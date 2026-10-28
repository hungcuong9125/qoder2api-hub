"""Offline per-account proxy routing, persistence, and management API tests."""
import ast
import base64
from concurrent.futures import ThreadPoolExecutor
import http.server
import io
from types import SimpleNamespace
import json
import os
from pathlib import Path
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch
import urllib.error
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
_TEMP = tempfile.TemporaryDirectory(prefix="qd-account-proxy-")
os.environ["ACCOUNTS_DIR"] = _TEMP.name
import qoder_accounts as A
import qoder_account_proxy as X
import qoder_proxy as P


class ForwardProxy(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        self.server.seen.append((self.path, self.headers.get("Proxy-Authorization")))
        data = json.dumps({"proxy": self.server.tag}).encode()
        self.send_response(200)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_CONNECT(self):
        self.server.seen.append((self.path, self.headers.get("Proxy-Authorization")))
        self.send_error(502, "Offline CONNECT fixture")

    def log_message(self, *args):
        pass


class ProxyTests(unittest.TestCase):
    def setUp(self):
        self.env = patch.dict(os.environ, {}, clear=True)
        self.env.start()
        self.addCleanup(self.env.stop)
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)

    def server(self, handler=ForwardProxy, tag="proxy"):
        server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
        server.tag, server.seen = tag, []
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        return server, "http://127.0.0.1:%d" % server.server_address[1]

    def test_validation_and_masking(self):
        for value in (None, 1, [], "socks5://127.0.0.1:1080", "https://host:443",
                      "http://host", "http://host:0", "http://host:65536",
                      "http://host:8/path", "http://host:8?q=x", "http://host:8#x",
                      "http://user:secret%0a@host:8", "http://user@host:8",
                      "http://bad%xx:secret@host:8", "http://user:secret@bad\\host:8"):
            with self.subTest(value=value), self.assertRaises(ValueError) as exc:
                X.normalize_proxy(value)
            self.assertNotIn("secret", str(exc.exception))
        self.assertEqual(X.normalize_proxy(" http://[::1]:8080/ "), "http://[::1]:8080")
        self.assertEqual(X.public_proxy("")["proxyMode"], "inherit")
        self.assertEqual(X.public_proxy("direct")["proxyMode"], "direct")
        view = X.public_proxy("http://alice:secret@host:8080")
        self.assertEqual(view["proxyMasked"], "http://***:***@host:8080")
        self.assertNotIn("secret", json.dumps(view))
        self.assertNotIn("alice", json.dumps(view))

    def test_account_persistence_and_reimport(self):
        pool = A.AccountPool(self.directory.name)
        account = A.Account({"uid": "a", "accessToken": "dt-fixture"})
        pool.add(account)
        url = "http://alice:secret@127.0.0.1:8181"
        view = pool.set_proxy("a", url)
        self.assertNotIn("secret", json.dumps(view))
        other = A.AccountPool(self.directory.name)
        other.load()
        self.assertEqual(other.get("a").proxy, url)
        self.assertEqual(other.get("a").to_dict()["proxy"], url)
        pool.add(A.Account({"uid": "a", "accessToken": "dt-new"}))
        self.assertEqual(pool.get("a").proxy, url)
        kwargs = A.normalise_import_row(pool.get("a").to_dict())
        self.assertEqual(kwargs["proxy"], url)
        pool.add(A.Account(A.normalise_import_row({"uid": "a", "accessToken": "dt-2"})))
        self.assertEqual(pool.get("a").proxy, url)
        pool.add(A.Account(A.normalise_import_row({"uid": "a", "accessToken": "dt-3", "proxy": ""})))
        self.assertEqual(pool.get("a").proxy, "")
        with patch.object(pool.get("a"), "save", side_effect=OSError("disk full")):
            with self.assertRaises(OSError):
                pool.set_proxy("a", url)
        self.assertEqual(pool.get("a").proxy, "")
        self.assertIsNone(pool.set_proxy("missing", "direct"))

    def test_pinned_proxy_ignores_bypass_and_global_opener(self):
        server, url = self.server()
        os.environ.update(HTTP_PROXY="http://127.0.0.1:1", NO_PROXY="*", no_proxy="*")
        req = urllib.request.Request("http://origin.invalid/account")
        with patch.object(urllib.request, "urlopen", side_effect=AssertionError("global opener used")):
            with X.open_request(req, url, {}, timeout=2) as response:
                self.assertEqual(json.loads(response.read()), {"proxy": "proxy"})
        self.assertEqual(server.seen, [("http://origin.invalid/account", None)])

    def test_parallel_accounts_are_isolated(self):
        first, one = self.server(tag="one")
        second, two = self.server(tag="two")
        def fetch(url):
            with X.open_request(urllib.request.Request("http://origin.invalid/"), url, {}, 2) as response:
                return json.loads(response.read())["proxy"]
        with ThreadPoolExecutor(max_workers=4) as executor:
            results = list(executor.map(fetch, [one, two] * 6))
        self.assertEqual(results, ["one", "two"] * 6)
        self.assertEqual(len(first.seen), 6)
        self.assertEqual(len(second.seen), 6)

    def test_connect_auth_and_no_direct_fallback(self):
        server, url = self.server()
        proxy = url.replace("http://", "http://alice:p%40ss@")
        os.environ["NO_PROXY"] = "*"
        with self.assertRaises(urllib.error.URLError) as exc:
            X.open_request(urllib.request.Request("https://origin.invalid/"), proxy, {}, 2)
        self.assertNotIn("alice", str(exc.exception))
        self.assertNotIn("p%40ss", str(exc.exception))
        auth = "Basic " + base64.b64encode(b"alice:p@ss").decode()
        self.assertEqual(server.seen, [("origin.invalid:443", auth)])

    def test_inherited_and_direct_modes(self):
        proxy, url = self.server(tag="inherited")
        origin, origin_url = self.server(tag="origin")
        with patch.object(urllib.request, "proxy_bypass", return_value=False):
            with X.open_request(urllib.request.Request(origin_url), "", {"http": url}, 2) as response:
                self.assertEqual(json.loads(response.read())["proxy"], "inherited")
            with X.open_request(urllib.request.Request(origin_url), "direct", {"http": url}, 2) as response:
                self.assertEqual(json.loads(response.read())["proxy"], "origin")
        self.assertEqual(len(proxy.seen), 1)
        self.assertEqual(len(origin.seen), 1)

    def test_json_requests_use_account_proxy(self):
        server, url = self.server()
        account = A.Account({"uid": "json", "proxy": url})
        with patch.object(A, "validate_public_http_url", side_effect=lambda u: u):
            result = account._http_json("http://origin.invalid/credits", retries=1, timeout=2)
        self.assertEqual(result, {"proxy": "proxy"})
        self.assertEqual(server.seen[0][0], "http://origin.invalid/credits")

    def test_all_account_json_calls_use_wrapper(self):
        tree = ast.parse((ROOT / "qoder_accounts.py").read_text())
        cls = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == "Account")
        calls = [node for node in ast.walk(cls) if isinstance(node, ast.Call)]
        bare = [node for node in calls if isinstance(node.func, ast.Name) and node.func.id == "http_json"]
        self.assertEqual(len(bare), 1)  # Only the wrapper itself can call the shared transport.
        wrapped = [node for node in calls if isinstance(node.func, ast.Attribute) and node.func.attr == "_http_json"]
        self.assertEqual(len(wrapped), 11)

    def test_chat_uses_the_selected_accounts_route(self):
        pool = A.AccountPool(self.directory.name)
        accounts = [A.Account({"uid": "chat-a", "realm": "cn", "accessToken": "token-a",
                               "proxy": "http://127.0.0.1:8181"}),
                    A.Account({"uid": "chat-b", "realm": "cn", "accessToken": "token-b",
                               "proxy": "direct"})]
        pool.accounts = accounts
        sessions = SimpleNamespace(get=lambda acc: SimpleNamespace(
            headers=lambda *args, **kwargs: {"X-Test-Token": acc.access_token}))
        payload = {"model": "auto", "messages": [{"role": "user", "content": "offline test"}]}
        with patch.object(P, "POOL", pool), patch.object(P, "SESSIONS", sessions), \
                patch.object(X, "open_request", side_effect=lambda *a, **k: io.BytesIO(b"data: [DONE]\\n\\n")) as transport, \
                patch.object(urllib.request, "urlopen", side_effect=AssertionError("global transport")) as global_open:
            for account in accounts:
                response, used, _ = P.open_upstream(payload, target_realm="cn", only_account=account)
                response.close()
                self.assertIs(used, account)
                args = transport.call_args
                self.assertEqual(args.args[1], account.proxy)
                self.assertEqual(args.args[0].get_header("X-test-token"), account.access_token)
                self.assertEqual(args.kwargs["timeout"], 600)
            global_open.assert_not_called()

    def test_chat_proxy_failure_never_uses_global_transport(self):
        pool = A.AccountPool(self.directory.name)
        account = A.Account({"uid": "failed", "realm": "cn", "accessToken": "fixture",
                             "proxy": "http://127.0.0.1:8181"})
        pool.accounts = [account]
        sessions = SimpleNamespace(get=lambda acc: SimpleNamespace(headers=lambda *a, **k: {}))
        with patch.object(P, "POOL", pool), patch.object(P, "SESSIONS", sessions), \
                patch.object(X, "open_request", side_effect=urllib.error.URLError("offline proxy")) as transport, \
                patch.object(urllib.request, "urlopen", side_effect=AssertionError("direct fallback")) as global_open, \
                patch.object(P.time, "sleep"), patch.object(P, "_short_error_cooldown_wait", return_value=0):
            with self.assertRaises(Exception):
                P.open_upstream({"model": "auto", "messages": [{"role": "user", "content": "test"}]},
                                target_realm="cn", only_account=account)
            self.assertTrue(transport.called)
            global_open.assert_not_called()

    def test_model_discovery_uses_account_proxy(self):
        account = A.Account({"uid": "models", "realm": "cn", "accessToken": "fixture",
                             "proxy": "http://127.0.0.1:8181"})
        pool = SimpleNamespace(pick=lambda **kwargs: account)
        sessions = SimpleNamespace(get=lambda acc: SimpleNamespace(headers=lambda *a, **k: {}))
        body = json.dumps({"chat": [{"key": "fixture-model"}]}).encode()
        with patch.object(P, "POOL", pool), patch.object(P, "SESSIONS", sessions), \
                patch.object(X, "open_request", return_value=io.BytesIO(body)) as transport, \
                patch.object(urllib.request, "urlopen", side_effect=AssertionError("global transport")):
            models = P.read_dynamic_models(realm="cn")
        self.assertEqual(models[0][0], "fixture-model")
        self.assertEqual(transport.call_args.args[1], account.proxy)
        self.assertEqual(transport.call_args.kwargs["timeout"], 15)

    def test_authenticated_api_and_probe(self):
        pool = A.AccountPool(self.directory.name)
        account = A.Account({"uid": "api", "realm": "cn", "accessToken": "dt-fixture"})
        pool.add(account)
        with patch.object(P, "POOL", pool), patch.object(P, "ACCOUNTS_DIR", self.directory.name):
            server, base = self.server(handler=P.Handler)
            token = P.PANEL.create()
            client = urllib.request.build_opener(urllib.request.ProxyHandler({}))
            def post(path, payload, headers=None):
                req = urllib.request.Request(base + path, data=json.dumps(payload).encode(),
                    headers=dict({"Content-Type": "application/json"}, **(headers or {})))
                try:
                    with client.open(req, timeout=5) as response:
                        return response.status, json.loads(response.read())
                except urllib.error.HTTPError as exc:
                    with exc:
                        return exc.code, json.loads(exc.read())
            headers = {"X-Panel-Token": token}
            for path in ("/accounts/proxy", "/accounts/proxy/test"):
                self.assertEqual(post(path, {"uid": "api", "proxy": "direct"})[0], 401)
                self.assertEqual(post(path, {"uid": "api"}, {"Authorization": "Bearer not-panel"})[0], 401)
                self.assertEqual(post(path, [], headers)[0], 400)
                self.assertEqual(post(path, {"uid": "missing"}, headers)[0], 404)
                self.assertEqual(post(path, {"uid": "api", "proxy": None}, headers)[0], 400)
            self.assertEqual(post("/accounts/proxy", {"uid": "api"}, headers)[0], 400)
            url = "http://alice:secret@127.0.0.1:8181"
            status, body = post("/accounts/proxy", {"uid": "api", "proxy": url}, headers)
            self.assertEqual(status, 200)
            self.assertEqual(account.proxy, url)
            self.assertNotIn("secret", json.dumps(body))
            error = urllib.error.HTTPError("https://openapi.qoder.com.cn/", 401, "fixture", {}, None)
            with patch.object(X, "open_request", side_effect=error) as transport:
                status, body = post("/accounts/proxy/test", {"uid": "api"}, headers)
                self.assertEqual(status, 200)
                self.assertTrue(body["ok"])
                self.assertEqual(body["status"], 401)
                args = transport.call_args
                self.assertEqual(args.args[1], url)
                self.assertEqual(args.args[0].get_method(), "HEAD")
                self.assertIsNone(args.args[0].get_header("Authorization"))
                self.assertEqual(args.kwargs["timeout"], 8)
            with patch.object(X, "open_request", side_effect=OSError("alice:secret")):
                _, body = post("/accounts/proxy/test", {"uid": "api", "proxy": "direct"}, headers)
                self.assertFalse(body["ok"])
                self.assertNotIn("secret", json.dumps(body))
            self.assertEqual(account.proxy, url)  # Testing an unsaved draft does not mutate it.
            self.assertEqual(post("/accounts/proxy", {"uid": "api", "proxy": ""}, headers)[0], 200)
            self.assertEqual(account.proxy, "")


if __name__ == "__main__":
    result = unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.loadTestsFromTestCase(ProxyTests))
    failed = len(result.failures) + len(result.errors)
    print("SUMMARY: TOTAL %d checks, %d passed, %d failed" %
          (result.testsRun, result.testsRun - failed, failed))
    _TEMP.cleanup()
    sys.exit(0 if result.wasSuccessful() else 1)
