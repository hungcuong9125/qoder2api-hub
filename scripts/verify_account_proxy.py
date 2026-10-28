"""Opt-in live proxy verification. Sends one dfmodel request (may consume quota).

Run: python scripts/verify_account_proxy.py --account Skeleton
Only reads saved account files; probe results are written to a temporary pool.
Never prints tokens, proxy URLs, or proxy credentials.
"""
import argparse
import contextlib
import io
import json
import socket
import sys
import tempfile
from pathlib import Path
from urllib.parse import urlsplit
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import qoder_accounts as A
import qoder_account_proxy as X
import qoder_proxy as P


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--account', required=True, help='Exact account nickname or UID')
    args = parser.parse_args()
    original = A.AccountPool(P.ACCOUNTS_DIR)
    original.load()
    matches = [a for a in original.accounts if args.account.strip() in (a.uid, a.nickname.strip())]
    if len(matches) != 1:
        parser.error('Expected exactly one matching account')
    account = A.Account(matches[0].to_dict())
    if not account.proxy or account.proxy == 'direct':
        parser.error('Account must have a saved custom proxy')
    proxy = urlsplit(account.proxy)
    expected = (proxy.hostname, proxy.port or 80)
    real_connect, real_open = socket.create_connection, X.open_request
    connections, routes = [], []
    violations = []

    def connect(address, *a, **kw):
        connections.append(address)
        if address != expected:
            violations.append('Unexpected socket destination')
            raise RuntimeError('Direct connection blocked by verification')
        return real_connect(address, *a, **kw)

    def open_request(req, selected_proxy, *a, **kw):
        if selected_proxy != account.proxy:
            violations.append('Unexpected proxy selection')
            raise RuntimeError('Wrong account proxy')
        routes.append(urlsplit(req.full_url).hostname)
        return real_open(req, selected_proxy, *a, **kw)

    class Handler:
        _handle_account_proxy = P.Handler._handle_account_proxy

        def _json(self, status, data):
            return data

        def _error(self, status, *args):
            return {'ok': False, 'status': status}

    with tempfile.TemporaryDirectory(prefix='qd-live-proxy-') as directory:
        pool = A.AccountPool(directory)
        pool.accounts = [account]
        with patch.object(P, 'POOL', pool), \
                patch.object(socket, 'create_connection', side_effect=connect), \
                patch.object(X, 'open_request', side_effect=open_request), \
                contextlib.redirect_stderr(io.StringIO()):
            probe = P.Handler._handle_accounts(Handler(), '/accounts/proxy/test', {'uid': account.uid})
            result = P.Handler._handle_accounts(Handler(), '/accounts/test',
                                              {'uid': account.uid, 'model': 'dfmodel'})
        passed = bool(probe.get('ok') and result.get('ok') and connections and not violations)
        print(json.dumps({'passed': passed, 'proxyInfo': probe.get('proxyInfo'),
            'model': 'dfmodel', 'modelOK': result.get('ok', False),
            'elapsedMs': result.get('elapsed_ms'), 'reply': result.get('reply'),
            'upstreamHosts': sorted(set(routes)), 'socketConnections': len(connections),
            'allSocketsThroughSavedProxy': bool(connections) and not violations},
            ensure_ascii=False, indent=2))
        return 0 if passed else 1


if __name__ == '__main__':
    sys.exit(main())
