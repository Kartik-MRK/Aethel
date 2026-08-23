"""Tests for the in-browser proof verifier.

`hub/static/verify.js` recomputes an inclusion proof on the reader's machine.
Python cannot execute it, so these tests do not try -- they pin the three things
that would break it silently and that a browser check would not catch until the
demo.

**The vectors.** The script refuses to render a verdict unless its own SHA-256
reproduces a set of published test vectors. That safety net is only as good as
the vectors themselves: a wrong constant in that list would make a correct
implementation refuse to run, or -- worse, if two were wrong together -- let a
broken one through. So the constants are read out of the file and checked against
`hashlib`. They are the only numbers in this project that exist in two languages
at once.

**The contract.** The verifier compares what it computed against values the
template printed into `data-` attributes. Rename an attribute or drop one and the
check quietly stops comparing anything, which looks exactly like it passing.

**The policy.** The script is external, so it runs only because a nonce admits
it. The CSP names no origin at all -- not even `'self'` -- and that has to stay
true, because a policy that allowed an origin would allow every script on it.

The arithmetic itself is checked elsewhere: `scripts/check_verify_js.py` runs the
file under node against 300-odd cases generated from `aethel.core.aggregator`,
including the odd-leaf-count trees where a node is promoted rather than
duplicated. That needs node, so it is a script rather than a test.
"""

import hashlib
import re
from pathlib import Path

import pytest

VERIFY_JS = Path(__file__).resolve().parents[1] / "hub" / "static" / "verify.js"


@pytest.fixture(scope="module")
def source():
    return VERIFY_JS.read_text(encoding="utf-8")


class TestTheSelfTestVectorsAreRight:
    """Every hash literal in the file, recomputed with `hashlib`.

    This is the test that makes the file's own self-test worth having. Without
    it, the vectors are just numbers somebody typed.
    """

    def test_the_plain_sha256_vectors_match_hashlib(self, source):
        """The VECTORS table, read as (input, digest) pairs.

        Inputs are written in the file as `""`, `"abc"` and `rep("a", n)`, so
        both spellings are parsed -- the repeated ones are there for the padding
        boundary and are the ones most likely to be mistyped.
        """
        table = source[source.index("var VECTORS = [") : source.index("function rep(")]

        literals = re.findall(r'\["([^"]*)", "([0-9a-f]{64})"\]', table)
        repeats = re.findall(r'\[rep\("(.)", (\d+)\), "([0-9a-f]{64})"\]', table)

        assert len(literals) + len(repeats) == 5, "the VECTORS table changed shape"

        for text, digest in literals:
            assert hashlib.sha256(text.encode()).hexdigest() == digest, text

        for character, count, digest in repeats:
            message = character * int(count)
            assert hashlib.sha256(message.encode()).hexdigest() == digest, count

    def test_the_padding_boundary_is_actually_straddled(self, source):
        """The 55- and 56-byte cases are the reason this table is not just "abc".

        A message of 55 bytes plus a 0x80 byte plus an 8-byte length is exactly
        one 64-byte block; 56 bytes needs two. Nearly every hand-written SHA-256
        that is wrong is wrong there, so if those two lengths ever disappear from
        the table the self-test has stopped testing the interesting case.
        """
        lengths = {int(count) for _, count in re.findall(r'rep\("(.)", (\d+)\)', source)}

        assert {55, 56} <= lengths

    def test_the_leaf_vector_matches_the_python_leaf_hash(self, source):
        """The domain-separated leaf, at the size a real one is.

        Checked against `aethel.core.aggregator` rather than against a
        reimplementation here, because agreeing with the aggregator is the
        property that matters -- if the aggregator's tagging ever changed, this
        test should fail rather than agree with a stale copy of the old rule.
        """
        from aethel.core.aggregator import hash_leaf

        match = re.search(r'hashLeaf\(rep\("(.)", (\d+)\)\) !==\s*"([0-9a-f]{64})"', source)
        assert match, "the leaf vector is no longer in the self-test"

        character, count, digest = match.groups()

        assert hash_leaf(character * int(count)) == digest

    def test_the_node_vector_matches_the_python_node_hash(self, source):
        from aethel.core.aggregator import hash_node

        match = re.search(
            r'hashNode\(rep\("(.)", (\d+)\), rep\("(.)", (\d+)\)\) !==\s*"([0-9a-f]{64})"',
            source,
        )
        assert match, "the node vector is no longer in the self-test"

        left_char, left_n, right_char, right_n, digest = match.groups()

        assert hash_node(left_char * int(left_n), right_char * int(right_n)) == digest

    def test_the_prefixes_agree_with_the_aggregator(self, source):
        """Domain separation, spelled out in both languages.

        `hashLeaf` tags with 0x00 and `hashNode` with 0x01. Swap them and every
        proof still folds self-consistently in the browser while disagreeing with
        every root the Hub ever published -- a failure that reads as tampering.
        """
        from aethel.core.aggregator import LEAF_PREFIX, NODE_PREFIX

        assert f"sha256(tagged(0x0{LEAF_PREFIX[0]}, value))" in source
        assert f"sha256(tagged(0x0{NODE_PREFIX[0]}, left + right))" in source


class TestTheScriptIsServedAndAdmitted:
    def test_the_file_is_reachable(self, hub_client):
        response = hub_client.get("/static/verify.js")

        assert response.status_code == 200
        assert "javascript" in response.headers["content-type"]

    def test_the_commit_page_loads_it_with_the_response_nonce(self, pushed):
        """An external script under `script-src 'nonce-...'` runs only if its tag
        carries that response's nonce. A tag without one is inert, and inert is
        indistinguishable from working until someone presses the button."""
        response = pushed["client"].get(f"/c/{pushed['second']}")
        nonce = re.search(r"'nonce-([^']+)'", response.headers["content-security-policy"])

        assert nonce
        assert f'nonce="{nonce.group(1)}" src="/static/verify.js"' in response.text

    def test_the_policy_still_names_no_origin(self, pushed):
        """Not `'self'`, not a host, not a scheme. The nonce is the whole
        allowance, so nothing else on this origin gains anything by being here."""
        policy = pushed["client"].get(f"/c/{pushed['second']}").headers[
            "content-security-policy"
        ]
        directive = next(part for part in policy.split("; ") if part.startswith("script-src"))

        assert re.fullmatch(r"script-src 'nonce-[\w-]+'", directive), directive

    def test_a_page_with_no_proof_does_not_load_it(self, hub_client, pushed):
        """The repository index has no ladder, so the script has nothing to do.

        Worth pinning rather than leaving to the script's own early return: a
        file fetched on every page is a file every page waits for.
        """
        assert "verify.js" not in hub_client.get("/").text


class TestTheContractWithTheTemplate:
    """The `data-` attributes the script reads. Each one is a silent failure.

    A verifier that finds nothing to compare against reports success, because
    "no disagreements" and "nothing checked" are the same state unless the code
    is written to tell them apart. Rather than complicate the script with
    assertions about its own page, the attributes are pinned here.
    """

    @pytest.fixture
    def page(self, pushed):
        return pushed["client"].get(f"/c/{pushed['second']}").text

    def test_the_proof_url_is_on_the_page_and_resolves(self, pushed, page):
        match = re.search(r'data-proof-url="([^"]+)"', page)
        assert match, "the script has nothing to fetch"

        response = pushed["client"].get(match.group(1))

        assert response.status_code == 200
        assert response.json()["commit_hash"] == pushed["second"]

    def test_the_commit_attribute_matches_the_page(self, pushed, page):
        """The script refuses a proof issued for some other commit, which it can
        only do if the page says which commit it is about."""
        assert f'data-commit="{pushed["second"]}"' in page

    def test_every_rung_carries_the_value_the_server_computed(self, pushed, page):
        """One `data-rung-result` per rung, holding that rung's own output.

        Compared against a fold done here rather than against the JSON, so this
        fails if the template ever prints the attribute from a different source
        than the digest beside it.
        """
        from aethel.core.aggregator import hash_leaf, hash_node

        proof = pushed["client"].get(f"/api/v1/log/proof/{pushed['second']}").json()

        running = hash_leaf(proof["commit_hash"])
        expected = []
        for step in proof["proof"]:
            if step["side"] == "left":
                running = hash_node(step["sibling"], running)
            else:
                running = hash_node(running, step["sibling"])
            expected.append(running)

        # The ladder is drawn root-first, so the attributes appear in the reverse
        # of the fold order. The script relies on exactly this, so the test has
        # to know about it too.
        found = re.findall(r'data-rung-result="([0-9a-f]{64})"', page)

        assert found == expected[::-1]
        assert found, "a logged commit produced no rungs to check"

    def test_the_root_node_carries_the_published_root(self, pushed, page):
        proof = pushed["client"].get(f"/api/v1/log/proof/{pushed['second']}").json()

        assert f'data-shown-root="{proof["root"]}"' in page

    def test_the_panel_is_hidden_by_the_attribute_not_by_css(self, page):
        """`style-src` has no `'unsafe-inline'`, so a `style` attribute would be
        discarded and the panel would render visible with a dead button. The
        `hidden` attribute is the only reveal that survives the policy."""
        panel = re.search(r'<div class="proof-check"[^>]*>', page)

        assert panel
        assert " hidden" in panel.group(0)
        assert "style=" not in panel.group(0)

    def test_the_button_and_the_state_line_are_both_present(self, page):
        """`data-check-run` and `data-check-state`. Without the second the script
        has nowhere to report, and returns before touching anything."""
        assert "data-check-run" in page
        assert "data-check-state" in page

    def test_the_state_line_announces_its_own_changes(self, page):
        """The result arrives by replacing text in place, which a screen reader
        is not told about unless the region says so."""
        assert re.search(r'data-check-state[^>]*aria-live="polite"', page)


class TestTheHubsOwnVerdictIsNotPresentedAsIndependent:
    """A page with two verdicts on it has to say which is which.

    The Hub's check and the browser's check use the same two hash functions and
    normally agree, so the honest distinction is not the result -- it is who ran
    it. If the server-rendered verdict ever stops saying so, the browser check
    becomes decoration.
    """

    def test_the_server_verdict_says_who_computed_it(self, pushed):
        page = pushed["client"].get(f"/c/{pushed['second']}").text

        assert "by this Hub" in page

    def test_the_page_does_not_claim_the_root_itself_is_proven(self, pushed):
        """The limit of an inclusion proof, kept in the copy.

        Folding to the published root proves the path is real. It says nothing
        about whether that root is the right one -- that is the chain anchor's
        job, and the page has to point at it rather than let a tick imply it.
        """
        page = pushed["client"].get(f"/c/{pushed['second']}").text

        assert "Not yet anchored" in page or "On chain" in page
