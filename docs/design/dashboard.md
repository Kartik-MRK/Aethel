# The dashboard

Why the Hub's pages look and move the way they do. This is the design record for
`hub/templates/`, `hub/static/hub.css`, and the two scripts that generate their
assets — the reasoning that does not fit in a stylesheet comment, and the rules
that a later change has to either keep or knowingly break.

The pages are server-rendered Jinja with one stylesheet and a small amount of
JavaScript: two files (`copy.js`, `verify.js`) and two inline blocks in the base
layout, one applying the stored theme before first paint and one wiring the
toggle. There is no build step, no framework, and no client-side routing.
Everything below follows from a single constraint: the primary content of this
system is 64-character hexadecimal digests, read by someone comparing one against
another.

---

## 1. The subject is a hash, so the type is chosen for hashes

A digest is not prose and it is not code. It is an identifier a person has to
compare, character by character, against another one — usually a few lines away,
sometimes in another window.

**The monospace face is a display face here, not a fallback for code.** It sets
identifiers, figures, section labels and the Merkle ladder; the sans is reserved
for prose. That is backwards from most sites, and it is right here because most
of what a reader is looking at is an identifier.

**A digest's head takes full ink and its tail recedes.** People do not read 64
characters. They read the first eight or twelve and treat the rest as
confirmation, which is why `git log` prints short hashes at all. The `.digest`
rule makes the type say what the reader is already doing.

The consequence is that the *whole* value stays on the page. Truncating with an
ellipsis would look tidier and would break the one operation the page exists to
support: comparing a value against one somewhere else. So the full digest is
always present, and a copy button sits next to it because the realistic next
action is pasting it into a terminal.

`copy.js` copies the element's `textContent`. That makes the rendered text the
contract — not the markup, which splits the value across two elements for
styling. A test asserts the copied string, by walking the nested spans rather
than by regex, because a regex that stopped at the first `</span>` would
silently measure the head alone and pass.

**Both faces are self-hosted**, subset to 36 KiB for the pair. Three independent
reasons, any one of which would be enough: the demo network may not reach a CDN;
the Content-Security-Policy names no external origin, so a linked font would be
blocked by our own header; and a system-font stack resolves to Cantarell on the
development machine and Segoe UI on the presentation machine — different metrics,
so the layout tuned on one is not the layout shown on the other.

The pair needs no optical-size correction, and that was measured rather than
assumed: x-height 0.5170em against 0.5160em, a 0.2% difference. A digest set
inline in a sentence therefore aligns with the prose around it at the same
`font-size`. That is a property of these two faces *together*, and a reason not
to swap one out casually.

---

## 2. The palette's numbers are the point

The three categorical slots are not chosen by eye. They come from a validated
reference instance and carry measured figures:

| | worst-pair CVD ΔE | normal-vision ΔE |
|---|---|---|
| light | 9.2 (deutan) | 24.0 |
| dark | 9.4 (deutan) | 20.9 |

**Changing a hex invalidates the measurement, which is the strongest thing about
the palette.** So everything layered on top is derived with `color-mix` from
those values rather than introduced as fresh hex. There is exactly one raw
addition per theme — the recessed well — and it is a neutral that carries no
data.

Two consequences are worth stating because they look like mistakes:

**Slots 2 and 3 are declared and mostly unused.** The ΔE figures are a property
of the three values *together*, so keeping the set intact is what makes adding a
second series a lookup instead of a fresh guess.

**Slot 1 is also the accent** — links, the fold chip, focus rings, marks. That
makes it ink as well as a graphic, and ink answers to 4.5:1 rather than the 3:1 a
stroke gets. The tension is real: a brighter blue clears the graphic bar and
fails the text one, worst on the fold chip where the ink sits on a 10% tint of
itself, so darkening the ink darkens its own ground. The chosen value clears
every use it has — 6.7:1 on a card, 5.2:1 on that chip.

The quiet ink tier shows the same discipline in reverse. The obvious grey for
"de-emphasised" measures 3.4:1 on the page, under the 4.5:1 that 12px text
needs — and the text wearing it is a figure's label, a table header, and the tail
of a hash, none of which is decorative enough to be exempt. So the floor set the
value, not the taste: 5.1:1 on the page, 4.7:1 on the well. The honest cost is
that three ink tiers on a near-white ground were only three tiers because the
bottom one sat outside the legible range; pulling it inside narrows the gap to
the middle tier, and the remaining separation is carried by size as much as by
ink. That is why the muted tier is nearly always 12px.

**Colour never carries a value alone.** Anywhere. Every series is directly
labelled at its own line end and every value also exists in the table below the
chart. This started as an accessibility rule and turned out to be the reason
slot 3 costs nothing to introduce later despite measuring 2.74:1 in light mode:
the relief it would need is already how these pages work.

**No colour has its only definition inside a conditional block.** Dark values are
declared under both `prefers-color-scheme` and `[data-theme]`, so an explicit
toggle wins in both directions and nothing is undefined for a reader outside the
condition.

---

## 3. Motion marks a change of state, and never marks arrival

This is the rule, and it is a rule rather than a preference.

**There is no fade-up on page sections as they load.** That effect is the house
style of a very large number of sites and it is decoration wearing the costume of
craft: it delays content the reader asked for in order to animate the fact that
it arrived, which the reader can already see. On these pages it also has a
measurable cost. They are long columns of 64-character hashes, and a reader
comparing two of them does not want the second one still moving.

**Exactly one thing animates on arrival**, and it animates because the movement
*is* the explanation rather than an ornament on it: the Merkle ladder assembles
from the leaf upward, in fold order, because each step consumes the previous
step's output. The sequence is the data dependency. Take the animation away and
the drawing still shows a fold; it just no longer shows the direction. Measured
delays, leaf to root: 0, 70, 140, 210, 420ms.

Everything else that moves is a transition on a state the reader is currently
changing — a hover, a focus, a control mid-press, a rung resolving as the check
reaches it.

### Three durations and one curve

```
--dur-tap     120ms   a control acknowledging a pointer
--dur-settle  190ms   a value resolving into place
--dur-reveal  380ms   the one entrance
--stagger      70ms   the interval between ladder steps
--ease-out    cubic-bezier(0.2, 0, 0.2, 1)
```

A duration should be a choice from a set, the same way a font size is. Before
these existed the file held 380ms, 160ms and 120ms in three places with no
relationship between them. A test asserts that no rule in the stylesheet states a
duration literally, so a fourth value cannot be introduced by typing a number.

One curve, and it decelerates, because everything here either arrives or changes
state and nothing accelerates away. A symmetric ease on a 120ms colour change is
indistinguishable from linear, so a second curve would be a token nobody could
tell was in use.

The one component that is not on `--dur-tap` earns the exception. The chart's
hover readout is a *value being displayed*, not a control acknowledging a
pointer, and a value that snaps in at 120ms as the pointer crosses eight commits
in a row reads as flicker rather than as a reading. At `--dur-settle` it resolves
as the pointer lands.

**`transition: all` is banned.** It transitions layout, so a hover that changed
padding would animate the page's geometry. The shared declaration names four
properties — colour, border colour, background colour, opacity — which are the
only things a hover changes anywhere on this site.

**The theme switch is instant.** A colour theme is a mode, not an event.
Transitioning every surface on toggle animates the whole page at once, which
reads as the site struggling, and it lands on the one control a reader presses to
*stop* looking at something.

### Reduced motion: transitions are zeroed, animations are removed

These are different mechanisms and the difference is load-bearing.

Zeroing a *transition* is correct. The end state is the resting style, so the
reader gets the result with no travel, and nothing on this site listens for
`transitionend`.

Zeroing an *animation* would be a bug. An animation with a zero duration and a
`backwards` fill still applies its first keyframe for the length of its delay — so
zeroing the tokens would hold the ladder at `opacity: 0` for 420ms and then snap
it in. That is a flash of missing content, produced in the name of respecting a
preference against motion.

So the entrance is gated by `@media (prefers-reduced-motion: no-preference)`
instead, and under `reduce` the rule does not exist at all. Verified in Chrome
with `--force-prefers-reduced-motion`: every rung and node reports
`animation-name: none` and `opacity: 1`, while the four duration tokens read
`0s`.

Both halves of that are asserted in `tests/test_hub_motion.py` — that the tokens
are zeroed under `reduce`, and that no `animation` is declared anywhere outside
the `no-preference` gate.

---

## 4. Everything is testable, because motion fails quietly

A colour that stops resolving renders black. A missing font falls back to
Cantarell. Both show up in a screenshot. A `var(--dur-tap)` that stops resolving
produces `transition-duration: 0s` — an instant state change, indistinguishable
from a deliberate one, and exactly what the site did before the tokens existed.
No screenshot catches that, and nothing else in the suite would notice.

So the front end is tested as code:

| File | Asserts |
|---|---|
| `test_hub_motion.py` | tokens exist on bare `:root`, no literal durations, no `transition: all`, the reduced-motion split, that only the ladder animates |
| `test_hub_fonts.py` | the served fonts resolve at the URLs the CSS names |
| `test_hub_errors.py` | error pages keep their status code and their security headers, leak nothing, and obey their own CSP |
| `test_hub_verify.py` | the browser verifier's structure and its self-check |
| `test_hub_apidocs.py` | the API reference lists routes that actually exist |

Where a behaviour fails invisibly, the test was mutation-checked: the code was
deleted and the suite confirmed to notice. The 500 handler's manual
`security_headers()` call, the digest tail, the error-detail filter, and nine
separate stylesheet mutations were each verified this way. A green suite is not
evidence on its own; a suite that goes red when you break the thing is.

---

## 5. Decisions that were considered and rejected

**A commit graph drawn as an SVG canvas.** The history table earns its place by
being copy-pasteable, sortable by reading order, and legible at 375px. The DAG
rail is drawn *alongside* the table rows instead, so the topology is visible
without the rows becoming a picture.

**A chart plotted against wall-clock time.** Commits are a DAG, not a sequence. A
time axis puts two unrelated experiments next to each other and draws a line
between them, implying a change someone made. Each line now follows one branch,
segment by segment from a commit to its own parent, so a slope always means
something a person did.

**A full-domain 0–100% accuracy axis.** Honest-looking and useless: every real
line flattens to nothing. The axis spans the data with its range stated in the
caption, which is the trade a reader can check.

**Swagger UI from a CDN.** It would have been one script tag. It also needs an
external origin the CSP does not name, ships a large bundle to render a page of
mostly static text, and looks like every other API page. `/api` is hand-written
from the same route table the tests read.

**An error page with an illustration and an oversized status numeral.** An error
page is read by someone already stuck, answering three questions: what happened,
was it my fault, what now. Anything else is in the way. The status code is set at
the size of an eyebrow because a reader who cares about the number is copying it
into a bug report and one who does not is looking straight past it at the
sentence underneath.

**A bespoke "go home" button on the error page.** The page extends the ordinary
layout, which puts the masthead and its nav back within reach. That is the actual
way out of a 404; a custom button would be a second, worse copy of a link that is
already there.

---

## 6. What a later change has to keep

1. Every duration comes from a token, and the tokens live on bare `:root`.
2. Entrance animations stay behind `no-preference`, never behind a zeroed token.
3. No new hex in the palette — derive with `color-mix`, or re-run the validator
   and update the measured figures in the stylesheet header.
4. Colour never carries a value alone; a directly-labelled line or a table row
   carries it too.
5. Nothing external is fetched at runtime. No CDN, no remote font, no analytics.
   The CSP names no external origin, and it is the header the site itself serves.
6. The full digest stays on the page.
