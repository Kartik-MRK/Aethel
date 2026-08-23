"""Tests for the error pages, the favicon route, and the copy affordance.

Three small features with one thing in common: each of them fails in a way that
looks like success. That is the whole reason this module exists.

**The error split.** A styled 404 that returns 200 tells every crawler, cache and
uptime monitor that a missing repository is a perfectly good page, and it looks
completely correct in a browser -- the heading says "Nothing at this address" and
the status line is not on screen. The other half is worse: `aethel push` reads
`detail` out of a JSON body, so an HTML page returned to the CLI turns a precise
server message into a parse error. Neither failure is visible from the page.

**The 500 handler's headers.** Starlette installs `ServerErrorMiddleware`
outermost, above every middleware the application added, so a 500 response never
passes through `SecurityHeadersMiddleware` and would otherwise be the single page
on this site with no `Content-Security-Policy` on it. The handler asks
`hub.security.security_headers()` for the set by hand. Nothing about the rendered
page changes if that call is deleted, so the test is the only thing holding it.

**The detail filter.** Starlette fills an unraised `detail` from the status
phrase, which is how a 404 for an unmatched path arrives carrying the literal
string "Not Found" -- printed under a "Detail" heading below a page already
titled "Nothing at this address". That regressed once already.

**The copy affordance.** `hub/static/copy.js` copies the *text of the digest
element*, so the contract is that the element's `textContent` is the whole
64-character hash. The macro splits it across two child elements for styling. If
a future edit truncated the tail, the page would still look right, the button
would still say "copied", and the reader would paste a hash that resolves to
nothing.
"""

import http
import re

import pytest

from hub.errors import EXPLANATIONS, GENERIC, _is_api, _specific
from hub.security import BASE_HEADERS
from tests.conftest import build_hub_client

#: Something no explanation, template or header should ever echo back.
SECRET = "leaked-connection-string-9c1f"


@pytest.fixture
def failing_client(hub_config):
    """A Hub with two routes that raise, and a client that lets them.

    `raise_server_exceptions=False` is what makes the 500 handler's response
    observable at all: the default re-raises into the test, which is convenient
    for debugging a route and useless for testing the error page.

    Both routes are added after `create_app`, which is deliberate -- the
    middleware stack and the handlers are the ones the real app builds, not a
    stand-in assembled for the test.
    """
    from fastapi.testclient import TestClient

    from hub.app import create_app

    app = create_app(hub_config)

    @app.get("/boom", include_in_schema=False)
    async def boom():
        raise RuntimeError(SECRET)

    @app.get("/api/v1/boom", include_in_schema=False)
    async def api_boom():
        raise RuntimeError(SECRET)

    @app.get("/forbidden", include_in_schema=False)
    async def forbidden():
        from fastapi import HTTPException

        raise HTTPException(status_code=403)

    with TestClient(app, raise_server_exceptions=False) as client:
        yield client


def policy_of(response) -> str:
    return response.headers.get("content-security-policy", "")


def nonces_in(html: str) -> set[str]:
    return set(re.findall(r'<script nonce="([^"]+)"', html))


def text_of_marked_digest(html: str, name: str) -> str:
    """The rendered text inside the digest tagged `data-copy="<name>"`.

    Spans nest here -- the macro puts a `.digest-tail` inside the digest -- so
    the closing tag is found by counting rather than by matching the next
    `</span>`. A regex that stopped at the first one would silently measure the
    head only, which is exactly the truncation this is meant to catch.

    Returning the *text* rather than the markup is the point: `copy.js` copies
    the element's `textContent`, so that string is the contract, whatever
    elements it happens to be split across for styling.
    """
    anchor = html.index(f'data-copy="{name}"')
    start = html.index(">", anchor) + 1

    depth, cursor = 1, start
    while depth:
        step = re.compile(r"<(/?)span\b").search(html, cursor)
        assert step, "the marked digest is never closed"
        depth += -1 if step.group(1) else 1
        cursor = step.end()

    inner = html[start : html.rindex("<", start, cursor)]
    return re.sub(r"<[^>]+>", "", inner).strip()


class TestTheStatusCodeSurvives:
    """The template is cosmetic. The code is the contract."""

    def test_an_unmatched_path_is_still_a_404(self, hub_client):
        response = hub_client.get("/nowhere")

        assert response.status_code == 404
        assert response.headers["content-type"].startswith("text/html")

    def test_a_missing_repository_is_still_a_404(self, hub_client):
        response = hub_client.get("/r/no-such-repository")

        assert response.status_code == 404

    def test_a_wrong_verb_is_still_a_405(self, hub_client):
        """The dashboard is read-only, and says so on the 405 page."""
        response = hub_client.post("/")

        assert response.status_code == 405
        assert "read-only" in response.text

    def test_an_unhandled_exception_is_still_a_500(self, failing_client):
        response = failing_client.get("/boom")

        assert response.status_code == 500

    def test_a_status_with_no_explanation_still_renders(self, failing_client):
        """Every code reaches the template, mapped or not.

        403 has no entry in `EXPLANATIONS`, so it falls through to `GENERIC`.
        A page that only rendered for the three known codes would raise inside
        the error handler for every other one.
        """
        response = failing_client.get("/forbidden")

        assert response.status_code == 403
        assert GENERIC[0] in response.text


class TestTheSplitIsByPathNotByAccept:
    """`aethel push` and `curl` send no `Accept`, so negotiation would guess
    wrong exactly when a machine is reading the answer."""

    def test_a_dashboard_404_is_a_page(self, hub_client):
        response = hub_client.get("/nowhere")

        assert response.headers["content-type"].startswith("text/html")
        assert "<html" in response.text

    def test_an_api_404_is_json(self, hub_client):
        response = hub_client.get("/api/v1/repos/no-such-repository")

        assert response.status_code == 404
        assert response.headers["content-type"].startswith("application/json")
        assert isinstance(response.json()["detail"], str)

    def test_an_api_404_says_something_the_cli_can_print(self, hub_client):
        """The CLI's error reporting is only as good as this string."""
        detail = hub_client.get("/api/v1/repos/no-such-repository").json()["detail"]

        assert "no-such-repository" in detail

    def test_an_api_500_is_json_and_says_nothing_else(self, failing_client):
        response = failing_client.get("/api/v1/boom")

        assert response.status_code == 500
        assert response.json() == {"detail": "Internal server error."}

    def test_a_browser_sending_json_accept_still_gets_a_page(self, hub_client):
        """Pinning the mechanism, not just the outcome.

        A dashboard path serves HTML whatever the header says. If this ever
        starts honouring `Accept`, the CLI's branch becomes a coin flip.
        """
        response = hub_client.get("/nowhere", headers={"accept": "application/json"})

        assert response.headers["content-type"].startswith("text/html")

    @pytest.mark.parametrize(
        ("path", "is_api"),
        [
            ("/api/v1/anything", True),
            ("/api/", True),
            ("/api", False),
            ("/apidocs", False),
            ("/", False),
        ],
    )
    def test_the_prefix_boundary(self, path, is_api):
        """`/api` is the hand-written reference page; `/api/` is the machine
        surface. One character apart, opposite answers, so it is worth pinning."""

        class Stub:
            url = type("U", (), {"path": path})()

        assert _is_api(Stub()) is is_api


class TestAnErrorPageLeaksNothing:
    def test_the_exception_message_is_not_on_the_page(self, failing_client):
        body = failing_client.get("/boom").text

        assert SECRET not in body
        assert "RuntimeError" not in body
        assert "Traceback" not in body

    def test_the_exception_message_is_not_in_the_json(self, failing_client):
        body = failing_client.get("/api/v1/boom").text

        assert SECRET not in body
        assert "RuntimeError" not in body

    def test_no_file_path_from_this_machine_appears(self, failing_client):
        """A traceback leaks the deployment layout, not just the error."""
        body = failing_client.get("/boom").text

        assert "hub/errors.py" not in body
        assert "site-packages" not in body


class TestThe500StillCarriesTheSecurityHeaders:
    """The property that costs a deliberate call, because the middleware cannot
    reach this response. Deleting that call changes nothing you can see."""

    def test_every_base_header_is_present(self, failing_client):
        response = failing_client.get("/boom")

        for header, value in BASE_HEADERS.items():
            assert response.headers.get(header) == value, header

    def test_the_policy_is_the_same_strict_one(self, failing_client):
        policy = policy_of(failing_client.get("/boom"))

        assert policy.startswith("default-src 'none'")
        assert "unsafe-inline" not in policy

    def test_the_html_nonce_is_the_one_the_header_authorized(self, failing_client):
        """Otherwise the theme script is blocked and the 500 page paints in the
        wrong theme -- on the one page nobody will stop to investigate."""
        response = failing_client.get("/boom")

        authorized = re.search(r"'nonce-([A-Za-z0-9_-]+)'", policy_of(response))
        assert authorized, "script-src carries no nonce"

        used = nonces_in(response.text)
        assert used == {authorized.group(1)}

    def test_the_api_500_is_covered_too(self, failing_client):
        response = failing_client.get("/api/v1/boom")

        assert response.headers.get("X-Content-Type-Options") == "nosniff"
        assert policy_of(response).startswith("default-src 'none'")

    def test_hsts_is_still_conditional_on_tls(self, failing_client):
        over_tls = failing_client.get("https://testserver/boom")
        plain = failing_client.get("/boom")

        assert over_tls.headers.get("Strict-Transport-Security") == "max-age=31536000"
        assert "strict-transport-security" not in {k.lower() for k in plain.headers}

    def test_an_http_error_gets_them_from_the_middleware(self, hub_client):
        """The other half of the reason the 500 handler is special-cased: an
        `HTTPException` runs inside the stack and needs no help at all."""
        response = hub_client.get("/nowhere")

        for header, value in BASE_HEADERS.items():
            assert response.headers.get(header) == value, header


class TestTheDetailIsOnlyEverSomethingWeWrote:
    """`_specific` filters Starlette's default, which is the status phrase."""

    def test_the_status_phrase_is_suppressed(self):
        assert _specific(404, "Not Found") == ""
        assert _specific(405, "Method Not Allowed") == ""

    def test_the_comparison_ignores_case_and_padding(self):
        assert _specific(404, "  not found  ") == ""

    def test_an_application_message_survives(self):
        message = "No repository 'nope'."

        assert _specific(404, message) == message

    def test_an_empty_detail_stays_empty(self):
        assert _specific(404, "") == ""

    def test_an_unknown_status_keeps_its_detail(self):
        """599 is not in `http.HTTPStatus`, so there is no phrase to compare."""
        assert _specific(599, "Upstream gave up") == "Upstream gave up"

    @pytest.mark.parametrize("status", sorted(EXPLANATIONS))
    def test_no_explained_status_can_echo_its_own_phrase(self, status):
        """Derived from `http.HTTPStatus` rather than a copied list of phrases,
        so a hand-written table cannot drift away from Starlette's source."""
        assert _specific(status, http.HTTPStatus(status).phrase) == ""

    def test_an_unmatched_path_renders_no_detail_strip(self, hub_client):
        """The regression this filter exists for: "Not Found" printed under a
        "Detail" heading, below a page already titled "Nothing at this
        address" -- the same sentence three times."""
        body = hub_client.get("/nowhere").text

        assert "Nothing at this address" in body
        assert ">Detail<" not in body

    def test_a_specific_failure_does_render_one(self, hub_client):
        body = hub_client.get("/r/no-such-repository").text

        assert ">Detail<" in body
        assert "no-such-repository" in body

    def test_a_500_never_shows_a_detail(self, failing_client):
        """Starlette's phrase for 500 is "Internal Server Error", which the
        heading already says in plainer words."""
        body = failing_client.get("/boom").text

        assert ">Detail<" not in body
        assert "Internal Server Error" not in body


class TestThePageProseIsWrittenForReading:
    def test_no_explanation_contains_an_ascii_double_hyphen(self):
        """These strings go to the template unfiltered, so `--` renders as two
        hyphens on the live page. Em dashes belong in prose; `--` belongs in
        comments and docstrings, which are read as source.

        Caught by reading a screenshot, not by any assertion, which is why it
        has one now.
        """
        for status, (heading, explanation) in EXPLANATIONS.items():
            assert "--" not in heading, status
            assert "--" not in explanation, status

    def test_no_explanation_is_just_the_rfc_phrase(self):
        """"Not Found" tells a reader nothing they did not know from arriving."""
        for status, (heading, _) in EXPLANATIONS.items():
            assert heading.casefold() != http.HTTPStatus(status).phrase.casefold()

    def test_the_way_out_is_a_link_per_destination(self, hub_client):
        """Each row's link text is the destination's name and nothing else.

        A sentence-long anchor is a harder target, and it runs the hover
        underline under text that is not a destination -- spending the site's
        own clickability signal on prose.
        """
        body = hub_client.get("/nowhere").text
        anchors = dict(re.findall(r'<a href="(/[a-z]*)">([^<]+)</a>', body))

        assert anchors.get("/") == "Repositories"
        assert anchors.get("/ops") == "Operations"
        assert anchors.get("/api") == "API reference"

    def test_an_error_page_extends_the_ordinary_layout(self, hub_client):
        """So the masthead nav is the actual way out, rather than a bespoke
        "go home" button that duplicates a link already on screen."""
        body = hub_client.get("/nowhere").text

        assert 'class="masthead"' in body
        assert 'href="/static/hub.css"' in body


class TestAnErrorPageObeysThePolicyItCarries:
    """The page most likely to be written as a self-contained HTML string, and
    the one where an inline style would be discovered by whoever hits it."""

    @pytest.mark.parametrize("path", ["/nowhere", "/r/no-such-repository"])
    def test_no_inline_style(self, hub_client, path):
        body = hub_client.get(path).text

        assert 'style="' not in body
        assert "<style" not in body

    def test_every_script_on_a_500_page_carries_the_nonce(self, failing_client):
        for tag in re.findall(r"<script\b[^>]*>", failing_client.get("/boom").text):
            assert "nonce=" in tag, tag


class TestTheFavicon:
    """Browsers request `/favicon.ico` whatever the page declares, so without a
    route the log fills with 404s that are ours rather than a visitor's."""

    def test_the_conventional_path_answers(self, hub_client):
        response = hub_client.get("/favicon.ico")

        assert response.status_code == 200

    def test_it_is_served_as_svg_despite_the_extension(self, hub_client):
        """The content type decides how it is parsed; `.ico` is only a habit."""
        response = hub_client.get("/favicon.ico")

        assert response.headers["content-type"].startswith("image/svg+xml")
        assert response.content.lstrip().startswith(b"<svg")

    def test_the_static_file_is_reachable_under_its_real_name(self, hub_client):
        response = hub_client.get("/static/favicon.svg")

        assert response.status_code == 200
        assert response.content == hub_client.get("/favicon.ico").content

    def test_it_carries_its_own_theme_swap(self, hub_client):
        """A tab strip follows the OS theme, not this page's, so the in-page
        toggle cannot reach the icon and the file has to handle it itself."""
        body = hub_client.get("/static/favicon.svg").text

        assert "prefers-color-scheme: dark" in body

    def test_every_page_declares_it(self, hub_client):
        body = hub_client.get("/").text

        assert '<link rel="icon" href="/static/favicon.svg" type="image/svg+xml">' in body


class TestTheCopyAffordance:
    """`copy.js` copies the digest element's own text, because there is no
    clipboard API over plain HTTP. That makes the element's `textContent` the
    contract, and the macro splits it across two children for styling."""

    #: Where a digest is the subject of its block, and what the button calls it.
    #: Written out rather than counted, so adding a fifth has to be deliberate:
    #: a button beside every hash in a table is a column of chrome, and the value
    #: a reader came for is the one the section is about.
    EXPECTED = {
        "/": {"transparency log root"},
        "/ops": {"log root"},
        "/r/demo": {"log root"},
        "/api": set(),
    }

    def test_only_the_intended_digests_ask_for_a_button(self, pushed):
        for path, names in self.EXPECTED.items():
            body = pushed["client"].get(path).text
            assert set(re.findall(r'data-copy="([^"]+)"', body)) == names, path

    def test_the_commit_page_offers_its_own_hash(self, pushed):
        body = pushed["client"].get(f"/c/{pushed['second']}").text

        assert set(re.findall(r'data-copy="([^"]+)"', body)) == {"commit hash"}

    def test_the_marked_element_holds_the_whole_hash(self, pushed):
        """The silent failure this guards: truncate the tail and the page still
        looks right, the button still says "copied", and the reader pastes a
        prefix -- which is not a name and will not resolve."""
        commit_hash = pushed["second"]
        body = pushed["client"].get(f"/c/{commit_hash}").text

        assert text_of_marked_digest(body, "commit hash") == commit_hash

    def test_the_log_root_is_whole_too(self, pushed):
        """Same contract, on the value the front page is built around.

        Checked against the API's own answer rather than a regex alone: a
        64-character hex string that is not the current root would satisfy the
        shape and still be the wrong thing to paste.
        """
        body = pushed["client"].get("/").text
        root = text_of_marked_digest(body, "transparency log root")

        assert re.fullmatch(r"[0-9a-f]{64}", root), root
        assert root == pushed["client"].get("/api/v1/log").json()["root"]

    def test_no_button_is_rendered_server_side(self, pushed):
        """Whether this browser can copy anything is a runtime question --
        `navigator.clipboard` is undefined outside a secure context, and the
        demo runs on a plain-HTTP LAN address. A server-rendered button could
        be a button that does nothing, and a control that silently fails
        teaches the reader to distrust the ones that work."""
        for path in [*self.EXPECTED, "/r/demo"]:
            assert "copy-btn" not in pushed["client"].get(path).text, path

    def test_the_script_is_loaded_with_a_nonce_on_every_page(self, pushed):
        """External, so it runs only because the policy admits it. The tag is in
        the base layout rather than per-template: it does nothing unless the
        page has a `data-copy` element, and deciding per page which ones those
        are is bookkeeping that goes stale the first time a digest moves.

        Both halves come out of the *same* response. The nonce is fresh per
        response, so checking a tag from one request against a policy from
        another would compare two unrelated values and fail for the right
        reason by accident.
        """
        for path in [*self.EXPECTED, "/r/demo", f"/c/{pushed['second']}"]:
            response = pushed["client"].get(path)
            tag = re.search(
                r'<script nonce="([^"]+)" src="/static/copy\.js"></script>', response.text
            )
            assert tag, path
            assert f"'nonce-{tag.group(1)}'" in policy_of(response), path

    def test_the_script_is_served(self, hub_client):
        response = hub_client.get("/static/copy.js")

        assert response.status_code == 200
        assert "data-copy" in response.text

    def test_it_gives_up_rather_than_render_a_dead_control(self, hub_client):
        """Both clipboard paths are feature-detected and the script returns
        before building anything if neither is there."""
        source = hub_client.get("/static/copy.js").text

        assert "navigator.clipboard" in source
        assert "queryCommandSupported" in source

    def test_the_page_gets_one_live_region_rather_than_one_per_button(self, hub_client):
        """A button whose own accessible name mutates is announced
        inconsistently, because focus has not moved. One polite status region
        is announced by every screen reader, and one region cannot contradict
        another."""
        source = hub_client.get("/static/copy.js").text

        assert source.count('"role"') == 1
        assert '"status"' in source
        assert source.count("createElement") == 2  # the region and the button


class TestConfigurationIsNotRequired:
    def test_the_error_pages_work_on_a_hub_with_nothing_configured(self, hub_config):
        """A demo runs in the default configuration, so that is the one the
        error pages have to render in."""
        with build_hub_client(hub_config) as client:
            response = client.get("/nowhere")

        assert response.status_code == 404
        assert "Nothing at this address" in response.text
