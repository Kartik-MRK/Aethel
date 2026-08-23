"""Tests for the hand-written API reference and the prose renderer behind it.

The reference at `/api` is prose in `hub/apidocs.py`, not a rendering of the
generated schema. That choice buys a page worth reading and costs the one thing
a generated page gives away free: it cannot go out of date on its own. So the
first class here is the whole justification for the choice -- it compares the
documented path set against the path set the application actually serves, in both
directions, and fails on either kind of drift.

Both directions matter and they fail differently. A served path that nobody
documented is a feature a client author cannot find. A documented path that is no
longer served is worse: it is the page confidently describing a call that returns
404, which is how documentation starts lying.

The second class covers `hub/prose.py`. Its security property is ordering --
escape first, substitute markers after -- and the test that matters is the one
that feeds it a tag.
"""

import pytest
from markupsafe import Markup

from hub import apidocs
from hub.prose import inline, prose


class TestTheReferenceMatchesTheApplication:
    def test_every_served_path_is_documented(self, hub_client):
        """A path with no entry on the page is a call nobody can discover."""
        undocumented = apidocs.served_paths(hub_client.app) - apidocs.documented_paths()

        assert not undocumented, f"served but not documented: {sorted(undocumented)}"

    def test_every_documented_path_is_served(self, hub_client):
        """The other direction, and the one that lets the page lie.

        A removed endpoint still described here reads exactly like a working one.
        """
        stale = apidocs.documented_paths() - apidocs.served_paths(hub_client.app)

        assert not stale, f"documented but not served: {sorted(stale)}"

    def test_the_application_serves_something_to_compare_against(self, hub_client):
        """Guards the two tests above against passing on an empty set.

        `served_paths` reads the generated schema, and a schema that came back
        without a `paths` key -- a FastAPI upgrade, a broken router include --
        would make both comparisons vacuously true. This is the canary.
        """
        assert len(apidocs.served_paths(hub_client.app)) > 10

    def test_no_endpoint_is_listed_twice(self):
        """Two entries for one path means two descriptions to keep in step."""
        pairs = [
            (endpoint.method, endpoint.path)
            for group in apidocs.GROUPS
            for endpoint in group.endpoints
        ]

        assert len(pairs) == len(set(pairs))

    @pytest.mark.parametrize(
        "endpoint",
        [endpoint for group in apidocs.GROUPS for endpoint in group.endpoints],
        ids=lambda endpoint: f"{endpoint.method} {endpoint.path}",
    )
    def test_every_entry_says_what_it_is_for_and_what_comes_back(self, endpoint):
        """An entry with no purpose or no return value is a heading, not a doc."""
        assert endpoint.purpose.strip()
        assert endpoint.returns.strip()

    def test_the_write_flag_matches_the_method(self):
        """`writes` gates the token marker, so it cannot be a GET.

        Not the reverse: `POST /log/verify` is a POST that changes nothing and
        needs no token, which is exactly why the page marks writes rather than
        colouring verbs.
        """
        for group in apidocs.GROUPS:
            for endpoint in group.endpoints:
                if endpoint.writes:
                    assert endpoint.method in {"POST", "PUT"}, endpoint.path

    def test_each_push_step_names_a_real_endpoint(self):
        """The sequence restates four calls documented in full further down.

        A restated path is a second copy, and a second copy drifts. Rather than
        thread references through the data, the duplication is allowed and pinned
        here -- a step whose path no longer matches any entry fails.
        """
        entries = {
            (endpoint.method, endpoint.path)
            for group in apidocs.GROUPS
            for endpoint in group.endpoints
        }

        for step in apidocs.PUSH_SEQUENCE:
            assert (step.method, step.path) in entries, step.title

    def test_every_push_step_is_a_write(self):
        """All four steps run against token-gated endpoints, which is what makes
        "configure a token and the write path closes" a true statement."""
        writes = {
            (endpoint.method, endpoint.path)
            for group in apidocs.GROUPS
            for endpoint in group.endpoints
            if endpoint.writes
        }

        for step in apidocs.PUSH_SEQUENCE:
            assert (step.method, step.path) in writes, step.title


class TestThePageRenders:
    def test_it_is_reachable_and_needs_no_repository(self, hub_client):
        """A protocol description does not depend on what has been published.

        `hub_client` is an empty Hub, so this passing is the claim: the page reads
        no state and cannot break when the store is empty.
        """
        response = hub_client.get("/api")

        assert response.status_code == 200
        assert "text/html" in response.headers["content-type"]

    def test_every_documented_path_appears_in_the_html(self, hub_client):
        """The template loops over the groups; this proves none is dropped.

        Paths are checked without their braces because the route line renders a
        parameter as its bare name inside a span -- that transformation is the
        page's one typographic device, so the test has to know about it.
        """
        html = hub_client.get("/api").text

        for path in apidocs.documented_paths():
            plain = path.replace("{", "").replace("}", "")
            assert plain.rsplit("/", 1)[-1] in html, path

    def test_the_push_sequence_is_numbered_in_order(self, hub_client):
        """The one numbered list on the site, and the numbers are load bearing.

        Step 4 fails with 409 if step 3 has not finished, so a sequence rendered
        out of order would be wrong rather than merely untidy.
        """
        html = hub_client.get("/api").text
        positions = [html.index(step.title) for step in apidocs.PUSH_SEQUENCE]

        assert positions == sorted(positions)
        assert len(apidocs.PUSH_SEQUENCE) == 4

    def test_the_token_marker_appears_only_where_a_call_writes(self, hub_client):
        writes = sum(
            1 for group in apidocs.GROUPS for endpoint in group.endpoints if endpoint.writes
        )

        assert hub_client.get("/api").text.count('class="route-auth"') == writes

    def test_the_schema_link_still_resolves(self, hub_client):
        """The page offers `/openapi.json` for tooling; a dead link there is a
        promise the Hub does not keep."""
        assert "/openapi.json" in hub_client.get("/api").text
        assert hub_client.get("/openapi.json").status_code == 200


class TestProse:
    """`hub/prose.py` -- the Markdown subset the reference is written in."""

    def test_a_tag_in_the_source_is_escaped_not_emitted(self):
        """The security property, stated as the test that would catch its loss.

        Escaping runs before the markers are substituted. Reverse those two steps
        and a backtick span becomes a way to smuggle a tag through.
        """
        rendered = inline("beware <script>alert(1)</script>")

        assert "<script>" not in rendered
        assert "&lt;script&gt;" in rendered

    def test_a_tag_inside_a_code_span_is_escaped_too(self):
        """The case the ordering exists for.

        A naive implementation substitutes `<code>` first and then escapes, which
        turns its own output into text; one that escapes only outside the spans
        passes the tag straight through.
        """
        rendered = inline("send `<img onerror=x>` as the body")

        assert "<code>" in rendered
        assert "<img" not in rendered
        assert "&lt;img" in rendered

    def test_markers_render(self):
        rendered = inline("a `hash`, a **rule**, an *aside*")

        assert "<code>hash</code>" in rendered
        assert "<strong>rule</strong>" in rendered
        assert "<em>aside</em>" in rendered

    def test_double_asterisks_are_not_read_as_two_emphases(self):
        """The emphasis pattern is guarded on both sides for this reason."""
        assert "<em>" not in inline("**strong only**")

    def test_a_blank_line_starts_a_paragraph(self):
        rendered = prose("first thing.\n\nsecond thing.")

        assert rendered.count("<p>") == 2
        assert "<p>first thing.</p>" in rendered

    def test_a_single_newline_does_not(self):
        """Source in a Python string wraps at 88 columns; those wraps are not
        paragraph breaks and must not render as any."""
        rendered = prose("one sentence\nwrapped in the source")

        assert rendered.count("<p>") == 1
        assert "wrapped in the source" in rendered

    def test_empty_text_renders_nothing(self):
        assert prose("") == Markup("")

    def test_the_result_is_markup_so_jinja_does_not_escape_it_again(self):
        """Returning `str` would have the template escape the tags this produced,
        printing the markup instead of applying it."""
        assert isinstance(inline("plain"), Markup)
        assert isinstance(prose("plain"), Markup)
