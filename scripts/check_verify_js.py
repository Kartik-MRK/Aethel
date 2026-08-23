"""Cross-check hub/static/verify.js against Python's hashlib.

Not part of the suite -- pytest cannot run JavaScript, and adding node as a test
dependency to check one file is a bad trade. This is the throwaway harness that
was used to establish the implementation is right, kept next to the fonts script
so the claim is reproducible rather than remembered.

Run it with: python3 scripts/check_verify_js.py
"""

from __future__ import annotations

import json
import random
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from aethel.core.aggregator import MerkleTree, hash_leaf, hash_node  # noqa: E402

VERIFY = Path(__file__).resolve().parents[1] / "hub" / "static" / "verify.js"
MARKER = "/* ---------- wiring ---------- */"

# The harness slices the file at the wiring comment and keeps the arithmetic
# above it, which is the whole point of that section boundary: the hash and the
# fold do not touch the DOM, so they can be exercised outside a browser.
HARNESS = """
%(functions)s

const cases = JSON.parse(require("fs").readFileSync(%(input)s, "utf8"));
const out = {
  selfTest: selfTestPasses(),
  sha256: cases.strings.map((text) => sha256(new TextEncoder().encode(text))),
  leaves: cases.leaves.map((value) => hashLeaf(value)),
  nodes: cases.nodes.map((pair) => hashNode(pair[0], pair[1])),
  folds: cases.folds.map((one) => fold(one.leaf, one.proof).root),
};
process.stdout.write(JSON.stringify(out));
"""


def javascript_side(cases: dict, workdir: Path) -> dict:
    source = VERIFY.read_text(encoding="utf-8")
    body = source.split(MARKER)[0]
    # Drop the IIFE opener so the declarations land at module scope.
    body = body[body.index('"use strict";') + len('"use strict";') :]

    payload = workdir / "cases.json"
    payload.write_text(json.dumps(cases), encoding="utf-8")

    script = workdir / "harness.js"
    script.write_text(
        HARNESS % {"functions": body, "input": json.dumps(str(payload))},
        encoding="utf-8",
    )

    finished = subprocess.run(
        ["node", str(script)], capture_output=True, text=True, check=True
    )
    return json.loads(finished.stdout)


def build_cases(rng: random.Random) -> dict:
    strings = ["", "abc", "a" * 55, "a" * 56, "a" * 63, "a" * 64, "a" * 65, "a" * 200]
    # Multi-byte text on purpose: the JS encodes with TextEncoder and Python with
    # .encode("utf-8"), and a mismatch there would only show up here.
    strings += ["über", "日本語のテスト", "é" * 40]
    strings += ["".join(rng.choice("0123456789abcdef") for _ in range(n)) for n in range(1, 130)]

    leaves = [f"{rng.getrandbits(256):064x}" for _ in range(40)]
    nodes = [(f"{rng.getrandbits(256):064x}", f"{rng.getrandbits(256):064x}") for _ in range(40)]

    # Real proofs from real trees, including the odd-leaf-count shapes where a
    # node is promoted rather than duplicated.
    folds = []
    for size in (1, 2, 3, 5, 7, 8, 9, 17, 33):
        tree = MerkleTree([f"{rng.getrandbits(256):064x}" for _ in range(size)])
        for index in range(size):
            leaf = tree.raw_leaves[index]
            folds.append(
                {
                    "leaf": leaf,
                    "proof": [
                        {"sibling": sibling, "side": side}
                        for sibling, side in tree.get_proof(leaf)
                    ],
                    "root": tree.get_root(),
                }
            )

    return {"strings": strings, "leaves": leaves, "nodes": nodes, "folds": folds}


def main() -> int:
    import hashlib
    import tempfile

    rng = random.Random(20260817)
    cases = build_cases(rng)

    with tempfile.TemporaryDirectory() as workdir:
        actual = javascript_side(cases, Path(workdir))

    failures = 0

    if not actual["selfTest"]:
        print("FAIL  the file's own selfTestPasses() returned false")
        failures += 1

    for text, got in zip(cases["strings"], actual["sha256"], strict=True):
        want = hashlib.sha256(text.encode("utf-8")).hexdigest()
        if got != want:
            print(f"FAIL  sha256({text[:20]!r}, {len(text)} chars): {got} != {want}")
            failures += 1

    for value, got in zip(cases["leaves"], actual["leaves"], strict=True):
        if got != hash_leaf(value):
            print(f"FAIL  hash_leaf({value[:12]}…): {got} != {hash_leaf(value)}")
            failures += 1

    for (left, right), got in zip(cases["nodes"], actual["nodes"], strict=True):
        if got != hash_node(left, right):
            print(f"FAIL  hash_node({left[:12]}…, {right[:12]}…): {got}")
            failures += 1

    for one, got in zip(cases["folds"], actual["folds"], strict=True):
        if got != one["root"]:
            print(f"FAIL  fold of leaf {one['leaf'][:12]}…: {got} != {one['root']}")
            failures += 1

    checked = (
        1
        + len(cases["strings"])
        + len(cases["leaves"])
        + len(cases["nodes"])
        + len(cases["folds"])
    )
    print(f"{checked - failures}/{checked} agree with Python")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
