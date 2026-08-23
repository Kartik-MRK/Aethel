"""Response security headers, including a nonce-based Content-Security-Policy.

Why a nonce rather than `'unsafe-inline'`: the dashboard needs exactly two
inline scripts, both of which must run before first paint or the page flashes
the wrong theme. Moving them to a file would trade a real visual defect for a
policy that still permits every *other* inline script on the page. A nonce
permits those two and nothing else, which is the actual security property we
want.

The nonce is minted per response and published on `request.state.csp_nonce` for
the templates. It is never reused across responses -- a fixed nonce is
equivalent to `'unsafe-inline'` for an attacker who can read one page.

The policy is deliberately `default-src 'none'`: every asset this dashboard
loads comes from its own origin. Charts are inline SVG, the two web fonts are
served from `/static/fonts`, and no page makes a cross-origin request, so there
is nothing external to allow. Starting from nothing and adding back only what is
used means a future template that reaches for an external script fails loudly in
development instead of quietly widening the trust boundary.

There is exactly one policy, applied to every path. There used to be a second,
relaxed one for FastAPI's bundled Swagger UI and ReDoc, which load their bundles
from a public CDN. Both pages are off now (see `hub/app.py`) and the reference at
`/api` is a normal template, so the exemption is gone with them -- which is the
better outcome: an exemption that exists is an exemption a future page can be
routed through.
"""

import secrets

from starlette.types import ASGIApp

#: Headers applied to every response. `nosniff` matters here specifically
#: because the Hub serves attacker-supplied bytes from /api/v1/blobs -- without
#: it a browser may sniff an uploaded adapter as HTML and execute it.
BASE_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",
    "Cross-Origin-Opener-Policy": "same-origin",
    "Cross-Origin-Resource-Policy": "same-origin",
    "Permissions-Policy": "geolocation=(), camera=(), microphone=(), payment=()",
}

#: Sent only on responses that actually arrived over TLS. Two reasons it is
#: conditional rather than unconditional. A browser ignores this header on a
#: plain-HTTP response, so sending it there is noise that reads as protection
#: without being any. And the Hub is deliberately host-agnostic -- it runs on a
#: LAN by IP, behind a tunnel, or on localhost -- so pinning HTTPS from a
#: hostname that has served both schemes would break the plain-HTTP fallback
#: that exists precisely for when the tunnel fails.
#:
#: `includeSubDomains` is omitted for the same reason: this process does not
#: know what else lives under its parent domain, and claiming authority over
#: sibling hosts is not ours to claim.
HSTS_HEADER = ("Strict-Transport-Security", "max-age=31536000")


def _dashboard_policy(nonce: str) -> str:
    """The policy: one stylesheet, two nonced scripts, nothing else."""
    return "; ".join(
        (
            "default-src 'none'",
            f"script-src 'nonce-{nonce}'",
            "style-src 'self'",
            "img-src 'self' data:",
            "font-src 'self'",
            "connect-src 'self'",
            "base-uri 'none'",
            "form-action 'none'",
            "frame-ancestors 'none'",
        )
    )


def security_headers(nonce: str, *, over_tls: bool) -> dict[str, str]:
    """The full header set for one response.

    Split out of the middleware because one response never passes through it.
    Starlette builds its stack with `ServerErrorMiddleware` outermost, so the
    handler for an unhandled exception runs *outside* every middleware the
    application added -- including this one. Its response is written straight to
    the transport, and a 500 page would otherwise be the single page on this site
    with no `Content-Security-Policy` on it.

    The fix is not to move the 500 handler inward. It belongs where Starlette
    puts it, because that is also what re-raises after responding, which is what
    makes uvicorn log the traceback -- and an error page that renders while the
    error goes unlogged is worse than no error page. So the handler asks for the
    same headers here instead, and this function is the one definition of what
    they are.
    """
    headers = dict(BASE_HEADERS)
    headers["Content-Security-Policy"] = _dashboard_policy(nonce)
    if over_tls:
        headers[HSTS_HEADER[0]] = HSTS_HEADER[1]
    return headers


class SecurityHeadersMiddleware:
    """Attach security headers to every response, and a nonce to every request.

    Written against the ASGI interface rather than as a BaseHTTPMiddleware
    subclass so it cannot buffer a streaming response: /api/v1/blobs returns
    adapter files, and wrapping those in a middleware that collects the body
    would hold a whole adapter in memory per request.
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope, receive, send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        nonce = secrets.token_urlsafe(18)

        # Published for the templates. Starlette builds request.state from
        # scope["state"], so setting it here is what makes it visible to a
        # Jinja context built downstream.
        scope.setdefault("state", {})
        scope["state"]["csp_nonce"] = nonce

        extra = security_headers(nonce, over_tls=scope.get("scheme") == "https")

        async def send_with_headers(message) -> None:
            if message["type"] == "http.response.start":
                headers = message.setdefault("headers", [])
                for key, value in extra.items():
                    headers.append((key.lower().encode("latin-1"), value.encode("latin-1")))
            await send(message)

        await self.app(scope, receive, send_with_headers)


__all__ = [
    "BASE_HEADERS",
    "HSTS_HEADER",
    "SecurityHeadersMiddleware",
    "security_headers",
]
