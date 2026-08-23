/* Recompute an inclusion proof in the reader's browser.
 *
 * WHAT THIS IS FOR
 * The commit page already renders a verdict, and that verdict is computed by the
 * Hub. Asking the Hub whether the Hub is honest settles nothing. This file moves
 * the arithmetic onto the reader's machine: it fetches the proof as JSON, folds
 * the leaf through the sibling path here, and compares its own result against
 * every value the server printed on the page.
 *
 * Be precise about what that buys, because it is easy to overclaim. Recomputing
 * here proves the proof is *internally sound* -- that this leaf and this path
 * really do fold to this root, so a Hub cannot hand out a plausible-looking path
 * for a commit it never logged. It does not prove the root is the right root.
 * That needs the root compared against the one anchored on chain, which is a
 * different check with a different trust root, and the page says so rather than
 * letting a green tick here imply it.
 *
 * WHY SHA-256 IS WRITTEN OUT BELOW
 * `crypto.subtle` is unavailable outside a secure context, and the demo
 * configuration is exactly that: two laptops on a LAN, reached as
 * `http://192.168.x.x:8000`. Only `https`, `localhost` and `127.0.0.1` count as
 * secure, so on the machine this was built for, `crypto.subtle` is `undefined`
 * and a verifier written against it would be silently missing in the one place
 * it is meant to be used. Sixty lines of hash is the cost of the feature working
 * where it has to work.
 *
 * A hand-written hash has one failure mode worth designing against: if it is
 * subtly wrong, every proof "fails" and the page reports tampering that never
 * happened -- the most damaging thing this code could do. So it checks itself
 * against published vectors before it is allowed to render any verdict, and
 * refuses to answer if they do not match.
 */
(function () {
  "use strict";

  /* ---------- SHA-256 ----------
   * FIPS 180-4. The first thirty-two bits of the fractional parts of the cube
   * roots of the first sixty-four primes.
   */
  var K = [
    0x428a2f98, 0x71374491, 0xb5c0fbcf, 0xe9b5dba5, 0x3956c25b, 0x59f111f1,
    0x923f82a4, 0xab1c5ed5, 0xd807aa98, 0x12835b01, 0x243185be, 0x550c7dc3,
    0x72be5d74, 0x80deb1fe, 0x9bdc06a7, 0xc19bf174, 0xe49b69c1, 0xefbe4786,
    0x0fc19dc6, 0x240ca1cc, 0x2de92c6f, 0x4a7484aa, 0x5cb0a9dc, 0x76f988da,
    0x983e5152, 0xa831c66d, 0xb00327c8, 0xbf597fc7, 0xc6e00bf3, 0xd5a79147,
    0x06ca6351, 0x14292967, 0x27b70a85, 0x2e1b2138, 0x4d2c6dfc, 0x53380d13,
    0x650a7354, 0x766a0abb, 0x81c2c92e, 0x92722c85, 0xa2bfe8a1, 0xa81a664b,
    0xc24b8b70, 0xc76c51a3, 0xd192e819, 0xd6990624, 0xf40e3585, 0x106aa070,
    0x19a4c116, 0x1e376c08, 0x2748774c, 0x34b0bcb5, 0x391c0cb3, 0x4ed8aa4a,
    0x5b9cca4f, 0x682e6ff3, 0x748f82ee, 0x78a5636f, 0x84c87814, 0x8cc70208,
    0x90befffa, 0xa4506ceb, 0xbef9a3f7, 0xc67178f2
  ];

  function rotr(word, bits) {
    return (word >>> bits) | (word << (32 - bits));
  }

  function hex32(word) {
    /* `>>> 0` first: every intermediate below is a signed 32-bit int, and
     * toString(16) on a negative number yields a minus sign, not two's
     * complement. */
    var text = (word >>> 0).toString(16);
    return "00000000".slice(text.length) + text;
  }

  function sha256(bytes) {
    /* Padding: the message, a single 1 bit, zeroes, and the length in bits as a
     * 64-bit big-endian integer -- rounded up to whole 64-byte blocks. The 9 is
     * that 0x80 byte plus the eight length bytes, which is why a 56-byte message
     * needs two blocks and a 55-byte one does not. */
    var padded = new Uint8Array(((bytes.length + 9 + 63) >> 6) << 6);
    padded.set(bytes);
    padded[bytes.length] = 0x80;

    var view = new DataView(padded.buffer);
    var bits = bytes.length * 8;
    /* Split across two writes because a bit length above 2^32 does not fit in
     * one setUint32, and because bitwise operators would truncate it silently. */
    view.setUint32(padded.length - 8, Math.floor(bits / 0x100000000), false);
    view.setUint32(padded.length - 4, bits >>> 0, false);

    /* Fractional parts of the square roots of the first eight primes. */
    var h0 = 0x6a09e667, h1 = 0xbb67ae85, h2 = 0x3c6ef372, h3 = 0xa54ff53a;
    var h4 = 0x510e527f, h5 = 0x9b05688c, h6 = 0x1f83d9ab, h7 = 0x5be0cd19;

    var w = new Int32Array(64);

    for (var offset = 0; offset < padded.length; offset += 64) {
      var index;
      for (index = 0; index < 16; index++) {
        w[index] = view.getInt32(offset + index * 4, false);
      }
      for (index = 16; index < 64; index++) {
        var s15 = w[index - 15];
        var s2 = w[index - 2];
        var sigma0 = rotr(s15, 7) ^ rotr(s15, 18) ^ (s15 >>> 3);
        var sigma1 = rotr(s2, 17) ^ rotr(s2, 19) ^ (s2 >>> 10);
        w[index] = (w[index - 16] + sigma0 + w[index - 7] + sigma1) | 0;
      }

      var a = h0, b = h1, c = h2, d = h3, e = h4, f = h5, g = h6, h = h7;

      for (index = 0; index < 64; index++) {
        var big1 = rotr(e, 6) ^ rotr(e, 11) ^ rotr(e, 25);
        var choose = (e & f) ^ (~e & g);
        var t1 = (h + big1 + choose + K[index] + w[index]) | 0;
        var big0 = rotr(a, 2) ^ rotr(a, 13) ^ rotr(a, 22);
        var majority = (a & b) ^ (a & c) ^ (b & c);
        var t2 = (big0 + majority) | 0;

        h = g; g = f; f = e; e = (d + t1) | 0;
        d = c; c = b; b = a; a = (t1 + t2) | 0;
      }

      h0 = (h0 + a) | 0; h1 = (h1 + b) | 0; h2 = (h2 + c) | 0; h3 = (h3 + d) | 0;
      h4 = (h4 + e) | 0; h5 = (h5 + f) | 0; h6 = (h6 + g) | 0; h7 = (h7 + h) | 0;
    }

    return hex32(h0) + hex32(h1) + hex32(h2) + hex32(h3) +
           hex32(h4) + hex32(h5) + hex32(h6) + hex32(h7);
  }

  /* ---------- the two domain-separated hashes ----------
   * These must agree byte for byte with aethel/core/aggregator.py. A leaf is
   * tagged 0x00 and an internal node 0x01, so no leaf value can be presented as
   * a subtree -- the second-preimage defence from RFC 6962. The node input is
   * the two children as 64-character hex strings concatenated, not as raw bytes,
   * which is what the Python does and therefore what this has to do.
   */
  var encoder = new TextEncoder();

  function tagged(tag, text) {
    var body = encoder.encode(text);
    var out = new Uint8Array(body.length + 1);
    out[0] = tag;
    out.set(body, 1);
    return out;
  }

  function hashLeaf(value) {
    return sha256(tagged(0x00, value));
  }

  function hashNode(left, right) {
    return sha256(tagged(0x01, left + right));
  }

  /* ---------- self-test ----------
   * Three published vectors plus the two shapes this file actually computes.
   * The 55- and 56-byte cases are here on purpose: they sit either side of the
   * point where the padding spills into a second block, which is where a
   * hand-written implementation goes wrong if it is going to.
   */
  var VECTORS = [
    ["", "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"],
    ["abc", "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"],
    [rep("a", 55), "9f4390f8d30c2dd92ec9f095b65e2b9ae9b0a925a5258e241c9f1e910f734318"],
    [rep("a", 56), "b35439a4ac6f0948b6d6f9e3c6af0f5f590ce20f1bde7090ef7970686ec6738a"],
    [rep("a", 64), "ffe054fe7ae0cb6dc65c3af9b61d5209f439851db43d0ba5997337df154668eb"]
  ];

  function rep(character, count) {
    var out = "";
    for (var i = 0; i < count; i++) { out += character; }
    return out;
  }

  function selfTestPasses() {
    for (var i = 0; i < VECTORS.length; i++) {
      if (sha256(encoder.encode(VECTORS[i][0])) !== VECTORS[i][1]) { return false; }
    }
    /* And the domain functions, at the exact sizes a real fold uses: a leaf is
     * 65 bytes in and a node 129, both of which cross the block boundary. */
    if (hashLeaf(rep("a", 64)) !==
        "88df0645999a1bc9dec19086e862403750a069436d7ecf7775256f78279b3fcb") {
      return false;
    }
    if (hashNode(rep("0", 64), rep("1", 64)) !==
        "8097e1b1da2a90ccae5a3eaa87e4b6619fe143910221651cbeb9a31a5298d351") {
      return false;
    }
    return true;
  }

  /* ---------- the fold ----------
   * The same climb the page draws: start at the leaf, fold in one sibling per
   * rung, and finish holding what should be the root.
   */
  function fold(commitHash, path) {
    var running = hashLeaf(commitHash);
    var steps = [];

    for (var i = 0; i < path.length; i++) {
      var sibling = path[i] && path[i].sibling;
      var side = path[i] && path[i].side;

      if (typeof sibling !== "string" || (side !== "left" && side !== "right")) {
        return { steps: steps, root: null, malformed: true };
      }

      running = side === "left" ? hashNode(sibling, running) : hashNode(running, sibling);
      steps.push({ sibling: sibling, side: side, result: running });
    }

    return { steps: steps, root: running, malformed: false };
  }

  /* ---------- wiring ---------- */

  var proof = document.querySelector("[data-proof-url]");
  if (!proof) { return; }

  var panel = proof.querySelector("[data-check]");
  var button = proof.querySelector("[data-check-run]");
  var state = proof.querySelector("[data-check-state]");
  var rungs = Array.prototype.slice.call(proof.querySelectorAll("[data-rung-result]"));
  var rootNode = proof.querySelector("[data-shown-root]");
  if (!panel || !button || !state) { return; }

  /* Revealed from script so a reader without JavaScript is never shown a control
   * that cannot do anything. The server-rendered ladder and the footnote already
   * tell them the fold is theirs to run. */
  panel.hidden = false;

  var still = window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  /* Rung-by-rung pacing, because the folds genuinely are sequential -- each one
   * consumes the previous one's output, and showing them settle in order is the
   * data dependency, not decoration. Reduced motion gets the same sequence with
   * the delay removed. */
  var PACE = still ? 0 : 110;

  function say(message, tone) {
    state.textContent = message;
    state.className = "check-state" + (tone ? " " + tone : "");
  }

  function markRung(rung, agrees) {
    rung.classList.remove("rung-pending");
    rung.classList.add(agrees ? "rung-agrees" : "rung-differs");
  }

  function wait(ms) {
    return new Promise(function (resolve) { window.setTimeout(resolve, ms); });
  }

  async function run() {
    button.disabled = true;

    if (!selfTestPasses()) {
      /* Refusing is the only honest option. A broken hash would fold every proof
       * to the wrong root and report the log as tampered. */
      say(
        "This browser's own check could not start: the SHA-256 in verify.js does " +
        "not reproduce its published test vectors, so any verdict it gave you " +
        "would be worthless. The Hub's result above is unaffected.",
        "bad"
      );
      return;
    }

    say("Fetching the proof as JSON…");

    var payload;
    try {
      var response = await fetch(proof.dataset.proofUrl, {
        headers: { Accept: "application/json" }
      });
      if (!response.ok) {
        say("The Hub returned " + response.status + " for this proof. Nothing to check.", "bad");
        button.disabled = false;
        return;
      }
      payload = await response.json();
    } catch (error) {
      say("Could not reach the Hub to fetch the proof. Nothing was checked.", "bad");
      button.disabled = false;
      return;
    }

    /* The proof has to be for the commit this page is about. A Hub that answers
     * with a valid proof for some *other* commit would otherwise produce a green
     * tick on the wrong page. */
    if (payload.commit_hash !== proof.dataset.commit) {
      say(
        "The Hub answered with a proof for a different commit (" +
        String(payload.commit_hash).slice(0, 12) + "…). Not checked.",
        "bad"
      );
      return;
    }

    var path = Array.isArray(payload.proof) ? payload.proof : [];
    rungs.forEach(function (rung) { rung.classList.add("rung-pending"); });
    say("Folding " + path.length + " sibling hash" + (path.length === 1 ? "" : "es") + "…");

    var computed = fold(payload.commit_hash, path);
    if (computed.malformed) {
      say("The proof contains a step that is not a sibling and a side. Not checked.", "bad");
      return;
    }

    /* The ladder is drawn bottom-up and `computed.steps` runs leaf-first, so the
     * displayed rungs are the reverse of the computed order. */
    var shown = rungs.slice().reverse();
    var agreements = 0;

    for (var i = 0; i < computed.steps.length; i++) {
      if (PACE) { await wait(PACE); }
      var rung = shown[i];
      if (!rung) { continue; }
      var agrees = rung.dataset.rungResult === computed.steps[i].result;
      if (agrees) { agreements++; }
      markRung(rung, agrees);
    }

    var rootAgrees = computed.root === payload.root;
    var pageAgrees = !rootNode || rootNode.dataset.shownRoot === computed.root;

    if (rootNode) {
      rootNode.classList.add(rootAgrees && pageAgrees ? "node-agrees" : "node-differs");
    }

    if (!rootAgrees) {
      say(
        "The fold does not reach the root the Hub published. This proof does not " +
        "prove what it claims — recomputed " + computed.root.slice(0, 12) + "…, " +
        "was given " + String(payload.root).slice(0, 12) + "….",
        "bad"
      );
      return;
    }

    if (!pageAgrees || agreements !== computed.steps.length) {
      say(
        "The arithmetic holds, but this page is printing values the fold does not " +
        "produce. Trust the JSON over the HTML, and treat the difference as a bug " +
        "in the Hub's rendering.",
        "warn"
      );
      return;
    }

    say(
      "Checked here. Folding leaf " + payload.leaf_index + " of " +
      payload.log_size + " through " + path.length + " sibling hash" +
      (path.length === 1 ? "" : "es") + " reaches the published root, and every " +
      "value on this page matches what your browser computed. What is still " +
      "unproven is the root itself — that is settled by comparing it against the " +
      "chain anchor, not here.",
      "ok"
    );
  }

  button.addEventListener("click", function () {
    run().catch(function () {
      say("The check stopped on an unexpected error and reported nothing.", "bad");
    });
  });
})();
