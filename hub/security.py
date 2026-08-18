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

The policy is deliberately `default-src 'none'`: this dashboard loads one
stylesheet from its own origin and nothing else. Every chart is inline SVG and
every font is a system face, so there is no CDN, no web font, and no XHR to
allow. Starting from nothing and adding back only what is used means a future
template that reaches for an external script fails loudly in development
instead of quietly widening the trust boundary.
"""

import secrets

from starlette.types import ASGIApp

#: Paths served by FastAPI's bundled API documentation. Swagger UI and ReDoc
#: load their bundles from a public CDN and set inline styles, so the strict
#: policy below would leave them blank. They are a developer aid rather than
#: part of the published trust surface, so they get their own narrower
#: exemption instead of relaxing the policy for the whole site.
_DOCS_PATHS = frozenset({"/docs", "/redoc", "/docs/oauth2-redirect", "/openapi.json"})

_DOCS_CDN = "https://cdn.jsdelivr.net"

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
    """The strict policy: one stylesheet, two nonced scripts, nothing else."""
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


def _docs_policy() -> str:
    """Relaxed only as far as Swagger UI and ReDoc actually require."""
    return "; ".join(
        (
            "default-src 'none'",
            f"script-src 'self' {_DOCS_CDN}",
            f"style-src 'self' 'unsafe-inline' {_DOCS_CDN}",
            f"img-src 'self' data: {_DOCS_CDN} https://fastapi.tiangolo.com",
            f"font-src 'self' {_DOCS_CDN}",
            "connect-src 'self'",
            "base-uri 'none'",
            "frame-ancestors 'none'",
        )
    )


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

        is_docs = scope.get("path", "") in _DOCS_PATHS
        policy = _docs_policy() if is_docs else _dashboard_policy(nonce)
        over_tls = scope.get("scheme") == "https"

        async def send_with_headers(message) -> None:
            if message["type"] == "http.response.start":
                headers = message.setdefault("headers", [])
                extra = dict(BASE_HEADERS)
                extra["Content-Security-Policy"] = policy
                if over_tls:
                    extra[HSTS_HEADER[0]] = HSTS_HEADER[1]
                for key, value in extra.items():
                    headers.append((key.lower().encode("latin-1"), value.encode("latin-1")))
            await send(message)

        await self.app(scope, receive, send_with_headers)


__all__ = [
    "BASE_HEADERS",
    "HSTS_HEADER",
    "SecurityHeadersMiddleware",
]
