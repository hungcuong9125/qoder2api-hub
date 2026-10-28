# Per-account outbound proxy

In the Accounts page, click **Proxy** on an account. Choose a mode, optionally
use **Test connection**, then **Save**. Changes apply to subsequent outbound
requests without a restart; an existing stream keeps its original connection.

## Modes

- **Inherit environment** (`proxy: ""`, also the default for existing accounts):
  preserve the existing application transport behavior and environment settings.
- **Direct** (`proxy: "direct"`): bypass environment proxies for this account.
- **Custom** (`proxy: "http://[user:password@]host:port"`): pin the account to an
  HTTP forward proxy. HTTPS destinations use CONNECT with normal TLS certificate
  verification. IPv6 proxy addresses need brackets. Percent-encode special
  characters in credentials, for example `p%40ss` for `p@ss`.

Only HTTP proxy servers are supported in this version; SOCKS and TLS-to-proxy
(`https://` proxy URLs) are rejected. An `http://` proxy can still carry HTTPS
traffic via CONNECT. Basic proxy credentials are sent to the proxy without TLS,
so use a trusted local/private network or an encrypted tunnel to the proxy.

Explicit custom routes ignore `NO_PROXY` and OS bypass settings, preventing
silent direct connections. A failing custom proxy does **not** fall back to a
direct connection. Normal account failover can still choose a different account,
using that account's own configured route.

## Coverage and storage

The account route covers chat (OpenAI/Responses/Anthropic through the shared
upstream path), signed model discovery, token refresh, quota/plan queries, and
campaign/check-in requests. Initial OAuth/PAT import has no saved account yet
and continues to use the existing environment route. Update checks, local agent
configuration, and unrelated network tools do not use an account proxy.

Configuration is saved in `accounts/<uid>.json` as `proxy`. Credential-only
reimports preserve the existing route. An explicitly imported `proxy` replaces
it; an empty string resets it to inherited mode. Account export includes the
real proxy URL alongside account tokens: treat exported files as secrets.
Ordinary account API responses expose only `proxyMode`, `proxyMasked`, and
`hasProxy`, never the proxy username or password. The edit form keeps saved
credentials when left blank in custom mode.

Both management routes require a panel session (`X-Panel-Token`); an inference
API key alone is insufficient:

- `POST /accounts/proxy`: `{"uid":"account-id","proxy":"http://127.0.0.1:7890"}`.
  Returns `{"ok":true,"account":{...}}` with masked proxy details.
- `POST /accounts/proxy/test`: same body, or omit `proxy` to test the saved route.
  Tests a draft without saving. It makes an unauthenticated HEAD request to the
  account realm's fixed Qoder OpenAPI host with an 8-second socket timeout.
  This is a reachability check, not verification of account tokens, exit IP,
  model access, or streaming. HTTP 4xx other than 407 can establish reachability;
  transport errors, proxy authentication failures and 5xx are reported as failed.

## Candidates from WorkBuddy (not included here)

1. Named proxy slots with stable IDs: share configurations and update many
   accounts without editing each URL. This version intentionally stores a URL
   per account; it does not implement `proxySlot` bindings.
2. Exit IP/country/ISP diagnostics and optional mihomo port discovery, with
   explicit opt-in for external lookup services and local port scanning.
3. Egress-level retry backoff, so multiple accounts sharing a broken proxy do
   not independently hammer it or incorrectly accumulate account penalties.
4. Keep-alive connection pooling keyed by proxy and destination, with careful
   SSE lifetime handling and tests for cross-account connection isolation.

Reference implementations: WorkBuddy's `wb_settings.py` (`proxy_slots`),
`wb_proxy.py` (`probe_proxy_intel`, `discover_proxy_slots`), `wb_egress.py`, and
`wb_upstream_pool.py`.
