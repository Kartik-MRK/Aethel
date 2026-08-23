/* Copy a digest to the clipboard.
 *
 * WHAT THIS IS FOR
 * A 64-character hash is not a value anybody retypes. It gets pasted into
 * `aethel checkout`, into a block explorer's search box, or into a message to
 * whoever published it -- and selecting one by hand means dragging across text
 * that has deliberately been styled in two weights, where the reader cannot tell
 * from the highlight whether they caught the last character.
 *
 * WHY THE BUTTONS ARE BUILT HERE RATHER THAN RENDERED BY THE TEMPLATE
 * Whether this page can copy anything is a question only the page can answer.
 * `navigator.clipboard` is unavailable outside a secure context, and the
 * fallback below is a deprecated call a browser is entitled to have removed. A
 * server has no way to know either, so a server-rendered button is a button that
 * may do nothing when pressed -- and a control that silently fails is worse than
 * no control, because the reader stops trusting the ones that work. So the
 * capability is probed first and the button only exists if something can carry
 * out the copy.
 *
 * THE NON-SECURE CONTEXT IS THE NORMAL CASE HERE, NOT THE EDGE CASE
 * Same problem `verify.js` has, for the same reason: this Hub is meant to be run
 * on a laptop and reached from another machine as `http://192.168.x.x:8000`.
 * Only `https`, `localhost` and `127.0.0.1` count as secure, so on the network
 * this was built for `navigator.clipboard` is `undefined` and a file written
 * against it alone would work everywhere except where it is used.
 *
 * `document.execCommand("copy")` is the fallback, and it is deprecated -- but it
 * is deprecated in favour of an API that is not reachable over plain HTTP, so
 * there is nothing to migrate to. It copies the current selection, which is why
 * the digest's own text is selected rather than a hidden element being planted:
 * an off-screen node needs styling to hide it, and `style-src` here does not
 * permit an inline style. Selecting the real text needs no styling at all, and
 * has the side effect that a reader watching the page sees exactly which
 * characters were taken.
 */
(function () {
  "use strict";

  /* How long the confirmation stays before the button says "copy" again. Long
   * enough to be read after the eye returns to the button, short enough that a
   * reader copying a second value is not looking at stale feedback from the
   * first. */
  var SETTLE_MS = 1600;

  /* ---------- capability ---------- */

  function asyncClipboard() {
    /* `writeText` is checked, not just `clipboard`: some embedded browsers
     * expose the object with only `read` on it. */
    return !!(navigator.clipboard && navigator.clipboard.writeText);
  }

  function legacyClipboard() {
    /* `queryCommandSupported` reports whether the command exists, not whether
     * this page may run it -- a copy from a handler outside a user gesture is
     * refused even when supported. That is the correct division here: the button
     * only ever runs from a click, so support is the question, and the return
     * value of the copy itself covers the refusal case. */
    return !!(document.queryCommandSupported && document.queryCommandSupported("copy"));
  }

  if (!asyncClipboard() && !legacyClipboard()) {
    return;
  }

  /* ---------- the copy itself ---------- */

  function selectText(node) {
    var range = document.createRange();
    range.selectNodeContents(node);

    var selection = window.getSelection();
    selection.removeAllRanges();
    selection.addRange(range);
    return selection;
  }

  function copyBySelecting(node) {
    /* The reader's own selection is saved and put back. Someone comparing two
     * hashes on this page may well have one half-selected when they reach for
     * the button, and losing it would be a small theft. */
    var selection = window.getSelection();
    var previous = selection.rangeCount ? selection.getRangeAt(0) : null;

    selectText(node);
    var copied = false;
    try {
      copied = document.execCommand("copy");
    } catch (e) {
      copied = false;
    }

    if (copied) {
      selection.removeAllRanges();
      if (previous) {
        selection.addRange(previous);
      }
    }
    /* On failure the text is left selected on purpose: the keyboard shortcut
     * still works, so a reader who cannot be helped automatically is at least
     * one keystroke from the value rather than back to dragging across it. */
    return copied;
  }

  function copy(node, value) {
    if (asyncClipboard()) {
      return navigator.clipboard.writeText(value).then(
        function () {
          return true;
        },
        function () {
          /* A rejected promise here is usually a permission the reader denied or
           * a window that lost focus mid-click, neither of which the fallback
           * cares about -- it runs off the gesture that is still in progress. */
          return copyBySelecting(node);
        }
      );
    }
    return Promise.resolve(copyBySelecting(node));
  }

  /* ---------- feedback ---------- */

  /* One live region for the whole page rather than one per button. A button
   * whose own label changes under a screen reader is announced inconsistently --
   * some read the new name, some say nothing, because the reader's focus has not
   * moved. A polite status region is announced by all of them, and one region
   * cannot contradict another. */
  function makeStatus() {
    var status = document.createElement("span");
    status.className = "sr-only";
    status.setAttribute("role", "status");
    document.body.appendChild(status);
    return status;
  }

  function makeButton(name) {
    var button = document.createElement("button");
    button.type = "button";
    button.className = "copy-btn";
    /* The visible word is "copy"; the accessible name says what of. Three
     * buttons all announcing "copy" on one page is three identical controls as
     * far as a screen reader is concerned. */
    button.setAttribute("aria-label", "Copy " + name);
    button.textContent = "copy";
    return button;
  }

  /* ---------- wiring ---------- */

  var marked = document.querySelectorAll("[data-copy]");
  if (!marked.length) {
    return;
  }

  var status = makeStatus();

  Array.prototype.forEach.call(marked, function (node) {
    var name = node.getAttribute("data-copy") || "value";
    var button = makeButton(name);
    var timer = null;

    /* Inserted after the digest, never inside it. Inside, the button's own text
     * would become part of the element's `textContent` -- so the second press
     * would copy the hash with "copied" welded onto the end of it, and the
     * selection fallback would highlight the button along with the value. */
    node.parentNode.insertBefore(button, node.nextSibling);

    button.addEventListener("click", function () {
      var value = node.textContent.trim();

      window.clearTimeout(timer);
      button.classList.remove("is-copied", "is-failed");

      copy(node, value).then(function (ok) {
        button.classList.add(ok ? "is-copied" : "is-failed");
        button.textContent = ok ? "copied" : "select";
        status.textContent = ok
          ? name + " copied to the clipboard"
          : name + " could not be copied; it is selected instead";

        timer = window.setTimeout(function () {
          button.classList.remove("is-copied", "is-failed");
          button.textContent = "copy";
          /* Cleared as well as reset. Left in place, the next reader to reach
           * this region with a screen reader's element navigation hears a stale
           * confirmation for a copy that happened minutes ago. */
          status.textContent = "";
        }, SETTLE_MS);
      });
    });
  });
})();
