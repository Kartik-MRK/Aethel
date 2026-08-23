"""Tests for the motion system.

Motion is the one part of this design system whose failures are all silent. A
colour that stops resolving renders black and a missing font falls back to
Cantarell, both of which a screenshot shows. A `var(--dur-tap)` that stops
resolving produces `transition-duration: 0s` -- an instant state change, which is
indistinguishable from a deliberate one and is what the site did before this
system existed. Nothing else in the suite, and no screenshot, would notice.

Two properties carry real weight.

**Every duration comes from a token.** The point of naming three durations is
that a fourth cannot be introduced by typing a number, so the check is that no
rule anywhere states one literally. Before this system there were three unrelated
literals in three places.

**Reduced motion zeroes transitions and *removes* animations.** These are
different mechanisms and the difference is the whole design. Zeroing a transition
is correct: the end state is the resting style, so the reader gets the result
with no travel. Zeroing an *animation* is a bug -- with `backwards` fill and a
delay, a zero-duration animation holds its first keyframe for the length of the
delay, so `opacity: 0` would be applied for 420ms and then snap. That is a flash
of missing content produced in the name of respecting a preference against
motion, and it is the reason the entrances live behind
`@media (prefers-reduced-motion: no-preference)` rather than behind the tokens.

Both facts are read out of the stylesheet rather than out of a browser, because
the suite has no browser. The rendered side -- that the tokens resolve, that
`animation-name` really is `none` under `reduce` -- was measured with Chrome over
CDP while the system was written.
"""

import re
from pathlib import Path

import pytest

CSS_PATH = Path(__file__).resolve().parents[1] / "hub" / "static" / "hub.css"

#: The named durations, and the ceiling each one is allowed to reach. Motion that
#: a reader has to wait for is not polish -- 400ms is roughly where a transition
#: stops reading as responsiveness and starts reading as latency, and an entrance
#: that outlasts a glance has become an interstitial.
BUDGET_MS = {
    "--dur-tap": 160,
    "--dur-settle": 250,
    "--dur-reveal": 400,
    "--stagger": 120,
}


@pytest.fixture(scope="module")
def css() -> str:
    """The stylesheet's rules, with comments removed.

    Read from disk rather than over the client, which is the opposite of what
    `test_hub_fonts.py` does and for a reason: those tests are about URLs
    resolving, so the served bytes are the thing that has to be true. Nothing
    here involves a URL -- a custom property either exists in the cascade or it
    does not -- and the file being served at all is already asserted next door.

    Comments come out because these tests scan for property values, and a
    comment that *discusses* a property is not a declaration of it. This file
    explains its own motion rules at length -- the reduced-motion block's comment
    contains the literal text `transition-duration: 0s` -- and a scanner that
    read prose would report the explanation as a violation of the thing it
    explains. Replaced with a space rather than deleted, so that stripping one
    cannot fuse the tokens on either side of it into a third one.
    """
    return re.sub(r"/\*.*?\*/", " ", CSS_PATH.read_text(encoding="utf-8"), flags=re.DOTALL)


def declarations(css: str, prop: str) -> list[str]:
    """Every value assigned to `prop`, in source order."""
    return [
        match.group(1).strip()
        for match in re.finditer(rf"(?<![\w-]){re.escape(prop)}\s*:\s*([^;}}]+)", css)
    ]


def root_block(css: str) -> str:
    """The bare `:root` block -- the one that must define every token."""
    match = re.search(r"(?<!\S):root\s*\{(.*?)\}", css, re.DOTALL)
    assert match, "no bare :root block"
    return match.group(1)


def reduce_block(css: str) -> str:
    match = re.search(
        r"@media\s*\(prefers-reduced-motion:\s*reduce\s*\)\s*\{(.*?\n\})", css, re.DOTALL
    )
    assert match, "no prefers-reduced-motion: reduce block"
    return match.group(1)


class TestTheTokensExist:
    @pytest.mark.parametrize("token", sorted(BUDGET_MS))
    def test_each_duration_is_defined_on_the_bare_root(self, css, token):
        """On bare `:root`, not inside a media query.

        Same rule the palette follows: a value whose only definition sits in a
        conditional block is undefined for every reader outside that condition.
        """
        assert re.search(rf"{token}\s*:", root_block(css)), token

    def test_the_curve_is_defined_once(self, css):
        """One easing, and it decelerates. A second curve on a 120ms colour
        change would be a token nobody could tell was in use."""
        values = declarations(root_block(css), "--ease-out")

        assert len(values) == 1
        assert values[0].startswith("cubic-bezier(")

    @pytest.mark.parametrize(("token", "ceiling"), sorted(BUDGET_MS.items()))
    def test_each_duration_stays_inside_its_budget(self, css, token, ceiling):
        value = declarations(root_block(css), token)[0]
        match = re.fullmatch(r"(\d+(?:\.\d+)?)(ms|s)", value)
        assert match, f"{token} is not a plain duration: {value}"

        millis = float(match.group(1)) * (1000 if match.group(2) == "s" else 1)
        assert millis <= ceiling, f"{token} is {millis}ms, over its {ceiling}ms budget"


class TestNoRuleStatesADurationLiterally:
    """The property that makes the tokens a system rather than four more values.

    Checked against the whole file rather than a list of known components, so a
    rule added later has to reach for a token too.
    """

    @pytest.mark.parametrize("prop", ["transition", "transition-duration"])
    def test_no_transition_names_a_raw_duration(self, css, prop):
        for value in declarations(css, prop):
            if value.strip() in {"none", "0s", "initial"}:
                continue
            assert not re.search(r"(?<![\w-])\d+(\.\d+)?m?s", value), value

    @pytest.mark.parametrize("prop", ["animation", "animation-duration", "animation-delay"])
    def test_no_animation_names_a_raw_duration(self, css, prop):
        """`animation-delay: 0ms` is the one literal allowed: it is the sequence's
        origin, not an interval, and `calc(0 * var(--stagger))` says less."""
        for value in declarations(css, prop):
            if value.strip() in {"0ms", "0s", "none"}:
                continue
            assert not re.search(r"(?<![\w-])\d+(\.\d+)?m?s", value), value

    def test_no_transition_uses_all(self, css):
        """`all` transitions layout, so a hover that changed padding would
        animate the page's geometry."""
        for value in declarations(css, "transition"):
            assert not re.match(r"all\b", value.strip()), value

    def test_the_stagger_is_applied_as_a_multiple(self, css):
        """Delays are multiples of one token, so the interval is one number to
        change and the ordering is visible in the arithmetic."""
        delays = declarations(css, "animation-delay")

        assert delays, "nothing is staggered"
        assert sum("var(--stagger)" in d for d in delays) >= 5


class TestReducedMotion:
    def test_every_duration_token_is_zeroed(self, css):
        """One block covers every transition on the site, so a component added
        later inherits the preference without having to remember it."""
        block = reduce_block(css)

        for token in BUDGET_MS:
            assert re.search(rf"{token}\s*:\s*0s", block), token

    def test_the_curve_is_left_alone(self, css):
        """An easing on a zero-length transition is not applied to anything.
        Overriding it would suggest it mattered."""
        assert "--ease-out" not in reduce_block(css)

    def test_the_entrance_is_gated_by_no_preference_instead(self, css):
        """The distinction this whole design turns on. `backwards` fill plus a
        delay means a zero-duration animation still applies `opacity: 0` for the
        length of the delay -- a flash of missing content, produced in the name
        of respecting a preference against motion. Under `no-preference` the rule
        does not exist at all.
        """
        gate = re.search(
            r"@media\s*\(prefers-reduced-motion:\s*no-preference\s*\)\s*\{(.*?\n\})",
            css,
            re.DOTALL,
        )
        assert gate, "the entrance is not gated by no-preference"

        body = gate.group(1)
        assert "animation:" in body
        assert "animation-delay" in body

    def test_no_animation_is_declared_outside_that_gate(self, css):
        """If an entrance were declared unconditionally, zeroing the tokens
        would produce exactly the flash described above."""
        gate = re.search(
            r"@media\s*\(prefers-reduced-motion:\s*no-preference\s*\)\s*\{.*?\n\}",
            css,
            re.DOTALL,
        )
        outside = css[: gate.start()] + css[gate.end() :]

        for value in declarations(outside, "animation"):
            assert value.strip() == "none", value

    def test_the_reveal_still_has_somewhere_to_land(self, css):
        """The `to` frame has to be the resting style, which is what makes
        `backwards` safe: nothing jumps when the fill lets go."""
        frames = re.search(r"@keyframes\s+rung-in\s*\{(.*?)\n\}", css, re.DOTALL)
        assert frames, "the entrance keyframes are gone"

        assert re.search(r"to\s*\{[^}]*opacity:\s*1", frames.group(1))
        assert re.search(r"to\s*\{[^}]*transform:\s*none", frames.group(1))


class TestWhatIsAllowedToMove:
    """Motion marks a change of state and never marks arrival.

    The rule is worth a test because the alternative is the single most
    recognisable tic of a generated site -- a fade-up on every section as the
    page loads -- and it is the kind of thing that gets added back one component
    at a time.
    """

    #: The one exception, and the reason it is one: the fold assembles in the
    #: order it is computed, so the sequence is the data dependency rather than
    #: an ornament on it.
    ANIMATED = {".rung", ".ladder-node"}

    def test_only_the_ladder_animates_on_arrival(self, css):
        gate = re.search(
            r"@media\s*\(prefers-reduced-motion:\s*no-preference\s*\)\s*\{(.*?\n\})",
            css,
            re.DOTALL,
        )
        selectors = re.findall(r"([.\w][^{}]*?)\s*\{[^}]*animation:", gate.group(1))
        named = {part.strip() for line in selectors for part in line.split(",")}

        assert named == self.ANIMATED, named

    def test_no_structural_container_transitions(self, css):
        """A transition on the page's own bands is how an arrival effect gets
        reintroduced without anyone calling it one."""
        for match in re.finditer(r"([^{}]+)\{([^}]*transition[^}]*)\}", css):
            selectors = {part.strip() for part in match.group(1).split(",")}
            for banned in {".shell", ".lede", ".strip", "body", "main", ".masthead"}:
                assert banned not in selectors, f"{banned} transitions"

    def test_the_theme_switch_is_instant(self, css):
        """A colour theme is a mode, not an event. Transitioning every surface
        on toggle animates the whole page at once, which reads as the site
        struggling rather than as a considered change -- and it lands on the one
        control a reader presses to *stop* looking at something."""
        for match in re.finditer(r"([^{}]+)\{([^}]*transition[^}]*)\}", css):
            selectors = {part.strip() for part in match.group(1).split(",")}
            assert "html" not in selectors
            assert ":root" not in selectors
