"""Tests for the Hub's response security headers.

These are tested rather than eyeballed because a header is invisible when it
works and equally invisible when it is missing. A stylesheet that fails to load
is obvious in a screenshot; a Content-Security-Policy that silently stopped
being sent is not, and nothing else in the suite would notice.

The properties worth pinning are the ones an ordinary refactor can break
without failing anything else:

* the policy reaches the dashboard at all,
* the nonce is fresh per response -- a nonce reused across responses is
  `'unsafe-inline'` wearing a disguise,
* the nonce in the HTML is the one the header authorized, since a mismatch
  blocks the theme script and the page paints the wrong theme,
* `nosniff` reaches a blob download, where attacker-supplied bytes are served,
* HSTS is sent when the request arrived over TLS and withheld when it did not,
* no page depends on an inline style, which the policy silently discards,
* the streaming download still streams, because the obvious way to write this
  middleware buffers the whole body.
"""

import hashlib
import re

import pytest

# Guards the imports below, which reach into `hub/`. The core CI job installs no
# [hub] extra on purpose, and an import that fails at module level is a
# collection error rather than a skip -- so the guard has to run before them.
pytest.importorskip("fastapi", reason="the Hub needs the [hub] extra")

from hub.security import BASE_HEADERS
from tests.conftest import build_hub_client

BLOB = b"aethel-security-test-payload"
BLOB_HASH = hashlib.sha256(BLOB).hexdigest()

#: Pages that render a template, as opposed to returning JSON. Each one must
#: carry the strict policy, because each one is a place an inline script could
#: be added later.
DASHBOARD_PATHS = ["/", "/ops", "/api"]


def policy_of(response) -> str:
    return response.headers.get("content-security-policy", "")


def directive(policy: str, name: str) -> str:
    """Pull one directive out of a policy string, without its name."""
    for part in policy.split(";"):
        part = part.strip()
        if part.split(" ")[0] == name:
            return part[len(name) :].strip()
    return ""


class TestBaseHeaders:
    @pytest.mark.parametrize("path", [*DASHBOARD_PATHS, "/api/v1/health"])
    def test_every_base_header_is_present(self, hub_client, path):
        response = hub_client.get(path)

        assert response.status_code == 200
        for header, value in BASE_HEADERS.items():
            assert response.headers.get(header) == value, header

    def test_headers_survive_an_error_response(self, hub_client):
        """A 404 is still a page an attacker can reach, so it is still covered.

        Middleware that only decorated successful responses would leave every
        error path unprotected, and error pages are a classic reflected-content
        sink.
        """
        response = hub_client.get("/r/no-such-repository")

        assert response.status_code == 404
        assert response.headers.get("X-Content-Type-Options") == "nosniff"
        assert policy_of(response).startswith("default-src 'none'")


class TestTransportSecurity:
    """HSTS is conditional, and both halves of the condition are the point.

    Sent unconditionally it would appear on plain-HTTP responses, where every
    browser ignores it -- a header that reads as protection in a scan while
    protecting nothing. Withheld unconditionally, a TLS deployment would accept
    an `http://` first request forever.
    """

    def test_a_plain_http_response_does_not_claim_hsts(self, hub_client):
        response = hub_client.get("/")

        assert "strict-transport-security" not in {k.lower() for k in response.headers}

    def test_a_tls_response_pins_https_for_a_year(self, hub_client):
        response = hub_client.get("https://testserver/")

        assert response.headers.get("Strict-Transport-Security") == "max-age=31536000"

    def test_it_does_not_claim_authority_over_sibling_hosts(self, hub_client):
        """No `includeSubDomains`: this process does not know what else is there."""
        header = hub_client.get("https://testserver/").headers["Strict-Transport-Security"]

        assert "includeSubDomains" not in header
        assert "preload" not in header

    def test_the_scheme_does_not_change_anything_else(self, hub_client):
        """Only HSTS is conditional. Every other header is unconditional."""
        over_tls = hub_client.get("https://testserver/")
        plain = hub_client.get("/")

        for header, value in BASE_HEADERS.items():
            assert over_tls.headers.get(header) == value, header
        assert policy_of(over_tls).startswith("default-src 'none'")
        assert directive(policy_of(over_tls), "style-src") == directive(
            policy_of(plain), "style-src"
        )


class TestDashboardPolicy:
    @pytest.mark.parametrize("path", DASHBOARD_PATHS)
    def test_the_policy_starts_from_nothing(self, hub_client, path):
        policy = policy_of(hub_client.get(path))

        assert directive(policy, "default-src") == "'none'"
        assert directive(policy, "base-uri") == "'none'"
        assert directive(policy, "frame-ancestors") == "'none'"

    @pytest.mark.parametrize("path", DASHBOARD_PATHS)
    def test_inline_script_is_permitted_only_by_nonce(self, hub_client, path):
        """`'unsafe-inline'` anywhere in script-src would defeat the whole point.

        The dashboard needs two inline scripts and gets them by nonce. If a
        future change reaches for the easier fix, this fails.
        """
        script_src = directive(policy_of(hub_client.get(path)), "script-src")

        assert script_src.startswith("'nonce-")
        assert "unsafe-inline" not in script_src
        assert "unsafe-eval" not in script_src

    def test_the_nonce_is_new_on_every_response(self, hub_client):
        first = directive(policy_of(hub_client.get("/")), "script-src")
        second = directive(policy_of(hub_client.get("/")), "script-src")

        assert first != second

    def test_the_html_nonce_matches_the_header(self, hub_client):
        """The header authorizes a nonce; the template has to use that one.

        If these drift, the browser blocks the pre-paint theme script and the
        page flashes the wrong theme -- a failure that looks like a CSS bug and
        is not one.
        """
        response = hub_client.get("/")

        authorized = re.fullmatch(
            r"'nonce-([A-Za-z0-9_-]+)'", directive(policy_of(response), "script-src")
        )
        assert authorized, "script-src is not a bare nonce"

        used = re.findall(r'<script nonce="([^"]+)"', response.text)
        assert used, "no nonced script in the page"
        assert set(used) == {authorized.group(1)}

    def test_a_page_has_no_unnonced_inline_script(self, hub_client):
        """Every `<script>` on the page must carry the nonce.

        A script tag added without one is dead code that fails silently in the
        browser console, which is the least likely place anyone will look.
        """
        for tag in re.findall(r"<script\b[^>]*>", hub_client.get("/").text):
            assert "nonce=" in tag, tag


class TestNoPageDependsOnAnInlineStyle:
    """`style-src 'self'` blocks a `style="..."` attribute, not just a `<style>`.

    Attribute styles fall under `style-src-attr`, which is unset here and so
    inherits `style-src` -- and `style-src` has no `'unsafe-inline'`. So a
    `style="margin-top:4px"` added to a template does nothing in a browser while
    continuing to look correct in the source, and continuing to pass every other
    test in this suite. This is the one CSP directive whose violation is silent
    rather than loud, so it is the one worth a test.

    All five rendered pages are checked, because the risk is a future template
    edit and there is no reason to think it lands on the two that need no data.
    """

    @staticmethod
    def paths(pushed) -> list[str]:
        return ["/", "/ops", "/api", "/r/demo", f"/c/{pushed['second']}"]

    def test_no_element_carries_a_style_attribute(self, pushed):
        for path in self.paths(pushed):
            html = pushed["client"].get(path).text
            assert 'style="' not in html, path
            assert "style='" not in html, path

    def test_no_page_embeds_a_style_element(self, pushed):
        """The stylesheet is a file, which is what `style-src 'self'` permits."""
        for path in self.paths(pushed):
            html = pushed["client"].get(path).text
            assert "<style" not in html, path
            assert 'rel="stylesheet"' in html, path

    def test_the_policy_that_makes_that_necessary_is_still_in_force(self, pushed):
        """Guards the reason: if `'unsafe-inline'` ever appears, this stops being
        a real constraint and the tests above become superstition."""
        style_src = directive(policy_of(pushed["client"].get("/r/demo")), "style-src")

        assert style_src == "'self'"


class TestBlobDownload:
    """The one endpoint that returns bytes an untrusted client supplied."""

    @pytest.fixture
    def stored(self, hub_client):
        assert hub_client.put(f"/api/v1/blobs/{BLOB_HASH}", content=BLOB).status_code == 200
        return hub_client

    def test_a_download_is_marked_nosniff(self, stored):
        """Without this a browser may sniff an uploaded adapter as HTML.

        Adapter bytes are whatever a pusher sent. `nosniff` plus a binary
        content type is what keeps a download a download.
        """
        response = stored.get(f"/api/v1/blobs/{BLOB_HASH}")

        assert response.status_code == 200
        assert response.headers.get("X-Content-Type-Options") == "nosniff"
        assert "text/html" not in response.headers.get("content-type", "")

    def test_the_bytes_come_back_intact(self, stored):
        """Guards the streaming path against a header middleware that buffers.

        The middleware is written against raw ASGI for exactly this reason. A
        BaseHTTPMiddleware subclass would collect the body first, holding a whole
        adapter in memory per request -- and would still pass every assertion
        above, which is why the payload itself is checked here.
        """
        response = stored.get(f"/api/v1/blobs/{BLOB_HASH}")

        assert response.content == BLOB
        assert hashlib.sha256(response.content).hexdigest() == BLOB_HASH


class TestOnePolicyEverywhere:
    """There is no exempt path, and this is the test that keeps it that way.

    There used to be a second, wider policy for FastAPI's bundled Swagger UI and
    ReDoc, which fetch their bundles from a public CDN. Both pages are off and the
    reference at `/api` is an ordinary template, so the exemption is gone -- and
    the reason to test its absence is that an exemption is easy to reintroduce for
    one page and hard to notice once a second page is routed through it.
    """

    #: Every path a browser can reach, template and JSON alike.
    EVERY_PATH = [*DASHBOARD_PATHS, "/openapi.json", "/api/v1/health", "/api/v1/repos"]

    @pytest.mark.parametrize("path", EVERY_PATH)
    def test_no_policy_names_an_external_origin(self, hub_client, path):
        """The whole point of `default-src 'none'` is that the list stays empty.

        Checked as "no scheme and no dot" rather than against a list of known
        CDNs, so a host nobody thought to name still fails.
        """
        policy = policy_of(hub_client.get(path))
        sources = policy.replace(";", " ").split()

        assert policy, path
        for source in sources:
            assert "//" not in source, f"{path} allows {source}"
            assert "." not in source, f"{path} allows {source}"

    @pytest.mark.parametrize("path", EVERY_PATH)
    def test_every_path_gets_the_same_directives(self, hub_client, path):
        policy = policy_of(hub_client.get(path))

        assert directive(policy, "default-src") == "'none'"
        assert directive(policy, "style-src") == "'self'"
        assert directive(policy, "connect-src") == "'self'"
        assert directive(policy, "frame-ancestors") == "'none'"

    def test_the_generated_schema_is_still_served(self, hub_client):
        """Turning off the two docs pages must not take `/openapi.json` with them.

        It is same-origin JSON with nothing to fetch, so it costs the policy
        nothing, and a client generator still has something to read.
        """
        response = hub_client.get("/openapi.json")

        assert response.status_code == 200
        assert response.json()["info"]["title"] == "Aethel Hub"

    @pytest.mark.parametrize("path", ["/docs", "/redoc"])
    def test_the_cdn_backed_pages_are_gone(self, hub_client, path):
        assert hub_client.get(path).status_code == 404


class TestConfigurationIsNotRequired:
    def test_headers_do_not_depend_on_optional_config(self, hub_config):
        """A Hub with no chain and no token configured is still hardened.

        Security headers that arrived with a feature flag would be absent in
        exactly the default configuration a demo runs in.
        """
        with build_hub_client(hub_config) as client:
            response = client.get("/")

        assert response.headers.get("X-Frame-Options") == "DENY"
        assert policy_of(response).startswith("default-src 'none'")
