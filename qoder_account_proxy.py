"""Per-account HTTP forward proxies, without process-global urllib state."""
import base64
import re
import urllib.error
import urllib.parse
import urllib.request


def normalize_proxy(value):
    """Empty inherits the environment; 'direct' disables proxies for this account."""
    if not isinstance(value, str):
        raise ValueError("proxy must be a string")
    value = value.strip()
    if value in ("", "direct"):
        return value
    message = "Use http://[user:password@]host:port, direct, or an empty string"
    try:
        if len(value) > 2048 or re.search(r"[\s\x00-\x1f\x7f]", value):
            raise ValueError(message)
        parsed = urllib.parse.urlsplit(value)
        if (parsed.scheme != "http" or not parsed.hostname or not parsed.port
                or parsed.path not in ("", "/") or parsed.query or parsed.fragment
                or re.search(r"%(?![0-9a-fA-F]{2})", value)):
            raise ValueError(message)
        host = parsed.hostname
        if any(c in host for c in "@%\\"):
            raise ValueError(message)
        for part in (parsed.username, parsed.password):
            if part is not None and re.search(r"[\x00-\x1f\x7f]", urllib.parse.unquote(part)):
                raise ValueError(message)
        if parsed.username is not None and (not parsed.username or parsed.password is None):
            raise ValueError(message)
        if parsed.username and ":" in urllib.parse.unquote(parsed.username):
            raise ValueError(message)
    except (ValueError, UnicodeError):
        # Never include an invalid URL: it can contain proxy credentials.
        raise ValueError(message) from None
    return urllib.parse.urlunsplit(("http", parsed.netloc, "", "", ""))


def public_proxy(value):
    """Account summaries deliberately omit both proxy username and password."""
    if value == "direct":
        return {"proxyMode": "direct", "proxyMasked": "", "hasProxy": False}
    if not value:
        return {"proxyMode": "inherit", "proxyMasked": "", "hasProxy": False}
    try:
        parsed = urllib.parse.urlsplit(normalize_proxy(value))
        host = "[%s]" % parsed.hostname if ":" in parsed.hostname else parsed.hostname
        masked = "http://%s%s:%d" % (
            "***:***@" if parsed.username is not None else "", host, parsed.port)
    except ValueError:
        masked = "Invalid proxy configuration"
    return {"proxyMode": "custom", "proxyMasked": masked, "hasProxy": True}


class _PinnedProxyHandler(urllib.request.ProxyHandler):
    """Explicit account routes must not be bypassed by NO_PROXY or OS settings."""
    def proxy_open(self, req, proxy, type):
        parsed = urllib.parse.urlsplit(proxy)
        host = "[%s]" % parsed.hostname if ":" in parsed.hostname else parsed.hostname
        if parsed.username is not None:
            credentials = "%s:%s" % (urllib.parse.unquote(parsed.username),
                                      urllib.parse.unquote(parsed.password))
            token = base64.b64encode(credentials.encode("utf-8")).decode("ascii")
            # A redirect gets its own proxy header; never copy this as origin auth.
            req.add_unredirected_header("Proxy-Authorization", "Basic " + token)
        req.set_proxy("%s:%d" % (host, parsed.port), "http")
        # HTTP is forwarded as an absolute URL; HTTPSHandler uses CONNECT with
        # normal certificate verification for the destination, not TLS bypass.
        return None


def opener(proxy, inherited_proxies):
    proxy = normalize_proxy(proxy)
    if proxy == "direct":
        handler = urllib.request.ProxyHandler({})
    elif proxy:
        handler = _PinnedProxyHandler({"http": proxy, "https": proxy})
    else:
        handler = urllib.request.ProxyHandler(inherited_proxies)
    return urllib.request.build_opener(handler)


def open_request(req, proxy, inherited_proxies, timeout=30):
    """No direct retry on proxy failure and no credential-bearing diagnostics."""
    try:
        return opener(proxy, inherited_proxies).open(req, timeout=timeout)
    except urllib.error.HTTPError:
        raise
    except (OSError, ValueError) as exc:
        if proxy and proxy != "direct":
            raise urllib.error.URLError("Account proxy connection failed (%s)" %
                                        type(exc).__name__) from None
        raise
