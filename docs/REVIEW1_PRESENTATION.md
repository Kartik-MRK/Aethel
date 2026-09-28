# Review 1: who presents what, and the demo video script

Two things live here. Section 1 splits the 28 deck slides four ways for the live review. Section 2 is
the shooting script for the demo video, segment by segment, with the exact commands.

The rule behind the split: **you present the slides you can answer a follow-up question on.** A
panel interrupts, and the worst outcome in the room is a presenter who has to look at someone else
mid-answer. So the slides follow the code, not the alphabet. Everyone gets seven.

---

## 1. The slide split

| # | Slide | Presenter | Why this person |
|---|---|---|---|
| 1 | Approval from the project guide | **Sathwik** | Opens |
| 2 | Title and team | **Sathwik** | Opens |
| 3 | Outline | **Adyaa** | Framing |
| 4 | Abstract and scope | **Adyaa** | Scope claim rests on what metadata is kept, which is her contribution |
| 5 | Architecture: module map | **Karthik** | Built the Phase-2 prototype, knows the whole pipeline |
| 6 | Architecture: complete pipeline | **Karthik** | Same |
| 7 | Summary of Phase-2 work | **Karthik** | It is his Phase-2 work being summarised |
| 8 | L0 `aethel/core` | **Karthik** | Owns `objects`, `refs`, `hashing`, `atomic`, `repo`, `errors` |
| 9 | L1 `aethel/commands` | **Karthik** | Owns `branch`, `checkout`, `log`, `fsck`, `_common` |
| 10 | L3 `aethel/remote` + L4 `hub/` | **Sathwik** | Owns the push protocol and the server contract |
| 11 | L4 transparency log and verifier | **Sathwik** | Owns `aggregator.py`, `hub/log.py`, `verify.js` |
| 12 | Engineering discipline: coverage and CI | **Shravan** | Ran the suite end to end; owns the failure surface |
| 13 | Contribution of each team member | **Shravan** | Neutral slide, and it precedes his testing slides |
| 14 | SDK / API / model / tools used | **Shravan** | Owns the ML stack usage |
| 15 | Testing completed for the modules | **Shravan** | Same |
| 16 | Demo 1: the version control lifecycle | **Shravan** | Owns `train.py` and `commit.py` |
| 17 | Demo 2: integrity, and what failure looks like | **Shravan** | The exit code and error surface are his; internals defer to Karthik |
| 18 | Demo 3: push, and the log growing | **Sathwik** | Owns `push.py`, `remote/`, `hub/storage.py` |
| 19 | Demo 4: the auditor dashboard | **Adyaa** | `/r/` page is hers end to end: `views.py`, `repo.html`, `hub.css` |
| 20 | Demo 5: one commit, and its proof | **Adyaa**, then Sathwik | Record schema is `commits.py`, hers. **Hand the proof table to Sathwik.** |
| 21 | Demo 6: the operational health board | **Adyaa** | `views.py` builds the rows; flag the two amber chain rows as Sathwik's remaining work |
| 22 | Demo 7: verification, PASS then FAIL | **Sathwik** | Owns the proof endpoints and the in-browser verifier |
| 23 | Performance metrics vs the baseline | **Sathwik** | Four of the eight rows are his measurements |
| 24 | Remaining modules and proposed idea | **Karthik** | Divergence detection and merge are his next piece |
| 25 | Gantt chart | **Karthik** | Follows directly from 24 |
| 26 | GitHub repository | **Adyaa** | Framing |
| 27 | References | **Shravan** | Framing |
| 28 | Thank you | **Adyaa** | Closes |

**Totals: seven slides each.** Sathwik 1, 2, 10, 11, 18, 22, 23. Karthik 5, 6, 7, 8, 9, 24, 25.
Adyaa 3, 4, 19, 20, 21, 26, 28. Shravan 12, 13, 14, 15, 16, 17, 27.

### Speaking order, so nobody misses a cue

```
Sathwik   1  2
Adyaa     3  4
Karthik   5  6  7  8  9
Sathwik  10 11
Shravan  12 13 14 15 16 17
Sathwik  18
Adyaa    19 20 21
Sathwik  22 23
Karthik  24 25
Adyaa    26
Shravan  27
Adyaa    28
```

Eleven handovers. Two of them are worth rehearsing because they land mid-topic:

- **Slide 20, inside the slide.** Adyaa presents the commit record at the top, then says the
  handover line out loud: *"the proof table below it is the log's, Sathwik will take that on the next
  screen."* She does not attempt the fold table or the domain-separation point. Sathwik picks both up
  on slide 22.
- **Slide 17 to 18.** Shravan finishes six slides in a row, so the switch back to Sathwik is easy to
  fumble. Shravan's last line is the exit code; Sathwik's first line is the push.

### Time budget

A twenty minute slot over 28 slides is about forty seconds a slide, so roughly five minutes each.
Two slides need more than their share and two need less:

- **Long:** slide 22 (PASS then FAIL, the whole thesis) and slide 6 (the pipeline, the only slide
  where the four layers connect). Give each ninety seconds.
- **Short:** slides 3, 26, 27, 28. Fifteen seconds each, they are signposts.

Take the time back from those four. Do not take it from slide 23, because the accuracy row is the
one a panel will stop on.

### The question each of you must be able to answer without help

Rehearse these out loud. They are the follow-ups the slides invite.

**Sathwik**
- Why is a Merkle proof `log₂(n)` and not `n`, and what is the actual byte count at a million leaves?
- Why does the leaf get hashed under `0x00` and an interior node under `0x01`? What breaks without it?
- Your own dashboard says "Not yet anchored". So what does the log prove right now, and what does it
  not?
- The Hub verified its own proof. Why is that worth nothing on its own, and what fixes it?

**Karthik**
- Phase 2 could overwrite one commit's metadata with another's. What exactly was the bug, and what in
  the new object model makes it impossible rather than just unlikely?
- Why is the SQLite index a cache and not a source of truth? What breaks if it is deleted?
- Checkout restores bytes, not a pointer. Show where that happens.
- What is cosine similarity between two adapters actually measuring, and why a threshold rather than a
  hard block?

**Adyaa**
- Which commit metadata did you drop, and what convinced you it was safe to drop?
- The accuracy axis spans 85 to 95 percent, not 0 to 100. Defend that.
- The branch graph is drawn per request rather than cached. What does that cost, and what does it buy?
- What does a "Logged" chip on a commit actually assert?

**Shravan**
- The coverage table says `aethel/commands` is at 25 percent. Why is that not a problem, and where is
  that code actually exercised?
- Three tests skip on a base install. Which three, and why is skipping the right behaviour?
- The evaluator scores the file the adapter trained on. Say plainly what that means for the number.
- Why does one CI job install no ML stack at all?

---

## 2. The demo video script

Target length is about nine and a half minutes across thirteen segments. Every command below was run
before this script was written, and every output shown is what the tool actually printed. Nothing
here is a mock-up and nothing needs a GPU, a dataset download, or the `[ml]` extra.

Speaking time comes out at roughly Sathwik 3:00, Karthik 2:25, Adyaa 2:10, Shravan 2:10.

### Before you press record

Two terminals and a browser. Terminal A serves the Hub and stays untouched on camera. Terminal B is
the one you record.

```bash
cd ~/zzz/Capstone/Aethel
python3 scripts/seed_demo.py --force
```

That builds `.demo/hub-data` with eight commits across three branches, and `.demo/work` as a matching
client repo. The synthetic weights are seeded from the step name, so a rebuild produces identical
hashes and any URL you rehearsed keeps working. Then, in terminal A:

```bash
export AETHEL_HUB_DATA="$PWD/.demo/hub-data"
export AETHEL_HUB_TOKEN=review-demo-token
python3 -m hub
```

Confirm it came up before recording anything:

```bash
curl -s http://127.0.0.1:8000/api/v1/version
```

It should report `"auth_required":true` and a `data_dir` ending in `.demo/hub-data`. If `data_dir` is
anywhere else, the pages will look empty and you will waste a take.

Then set up the second repo, the one you push from. The seeded client repo cannot demonstrate a push
usefully, because the seed wrote its objects straight into the Hub store, so every push from it
reports nothing to send. A fresh repo is what shows bytes moving.

```bash
export AETHEL_HUB_TOKEN=review-demo-token
rm -rf /tmp/clidemo && mkdir /tmp/clidemo && cd /tmp/clidemo
```

Recording settings that matter: terminal at about 100 columns so no line wraps mid-command, font
large enough to read on a laptop, and the browser at 1440 wide with the bookmarks bar hidden.

**One honest note to keep in mind while narrating.** The adapter files in this recording are
synthetic bytes of the right shape and size, not the output of a training run, and the accuracy
figures on the dashboard are seeded fixtures. Segment 11 says that out loud rather than leaving it to
be discovered. Do not narrate any of it as a trained result.

---

### Segment 1. Title card. Sathwik. 0:15

Slide 2 of the deck on screen, held still.

> "This is Aethel, version control and provenance for LoRA adapters. A version here is a three
> megabyte patch plus a pinned reference to a base model we never store, and the published history is
> an append only log you can check without trusting us. Nine commands, a Hub, and a transparency log.
> Here it is running."

---

### Segment 2. The pinned base. Shravan. 0:45

Terminal B, in `/tmp/clidemo`.

```bash
aethel init --model distilbert-base-uncased --author johndoe
```

```
Initialized Aethel repository in /tmp/clidemo/.aethel
Base model: distilbert-base-uncased
Revision:   12040accade4e8a0f71eabdb258fecc2e7e948be
Base ref:   c0c3831886b3
Author:     johndoe

Base weights are not stored, only the pinned reference.
```

> "Init resolves the base model's revision hash live from the Hugging Face API. It is not typed into
> a config file, and it is the same hash we reported in Phase 2, which is the entire point of pinning
> one. Two hundred and sixty five megabytes of base weights are not copied anywhere. What gets stored
> is that reference, and every commit points at it."

Then stage an adapter and commit. On camera, do not run the staging script; have the two files
already in `.aethel/workspace/` and just list them.

```bash
ls -l .aethel/workspace/
aethel commit -m "baseline sst2 adapter, r=8"
```

```
131 adapter_config.json
64000 adapter_model.safetensors

Committed 24e36e245531
Branch: main
```

> "Sixty four kilobytes of rank eight weights, committed. The commit is keyed by the hash of the
> commit record itself, not by the hash of the weights, and that distinction is what fixes the bug
> Karthik will describe next."

Your commit hashes will differ from the ones in this script. That is expected and fine.

---

### Segment 3. Branch, and going back. Karthik. 1:05

```bash
aethel branch wide-rank
aethel checkout wide-rank
# a wider adapter is staged
aethel commit -m "widen to r=16 on the same targets"
aethel branch
aethel log
```

```
Created branch wide-rank at 24e36e245531
Switched to branch wide-rank
Committed 02cdc415e5c4

  main      24e36e245531  baseline sst2 adapter, r=8
* wide-rank 02cdc415e5c4  widen to r=16 on the same targets
```

> "Two branches off one baseline, the way Git does it. Note the accuracy column in the log reads as a
> dash on both commits. These carry no evaluation metrics, and the CLI reports that absence instead
> of printing a zero. That distinction matters later."

Now the part worth slowing down for.

```bash
ls -l .aethel/workspace/      # 128000 bytes, rank 16
aethel checkout main
ls -l .aethel/workspace/      # 64000 bytes, rank 8
```

> "Checkout put the earlier commit's actual bytes back on disk. A hundred and twenty eight kilobytes
> of rank sixteen weights replaced by the sixty four kilobyte rank eight version. It restores
> content, not a pointer, and it does it from the object store alone. In Phase 2 this path needed a
> SQLite database to resolve a commit at all, which meant the repository was not portable. Now the
> index is a cache you can delete."

---

### Segment 4. Integrity, and what failure looks like. Karthik. 0:55

```bash
aethel fsck
```

```
Checked 9 objects (2 commits, 2 trees, 4 blobs, 1 bases)
Repository integrity OK.
```

Flip one bit. Run the one-liner on camera, it is more convincing than describing it.

```bash
python3 -c "
import pathlib
p = max((b for b in pathlib.Path('.aethel/objects/blobs').rglob('*') if b.is_file()),
        key=lambda b: b.stat().st_size)
d = bytearray(p.read_bytes()); d[100] ^= 0x01; p.write_bytes(bytes(d))"
aethel fsck
echo $?
```

```
Checked 9 objects (2 commits, 2 trees, 4 blobs, 1 bases)

1 CORRUPT object(s):
  blob 91e508e0cc097027a7b25f6821e73334a048ff85073ee5167b621076a552d1c4
    /tmp/clidemo/.aethel/objects/blobs/91/e508e0cc09...a552d1c4

Repository integrity check FAILED.
1
```

> "One bit, out of a hundred and twenty five kilobytes, and it is named by hash and by path. Exit
> code one, so a script or a CI job can act on it rather than parsing text for it. Phase 2 had no
> integrity check of any kind, and its own status report listed silent metadata overwriting as a
> known limitation. Detection is the difference between a store you can audit and one you can only
> hope about."

Restore the bit and show it clean again, so the recording does not leave a corrupt repository behind.

---

### Segment 5. The suite. Shravan. 0:30

Terminal B, back in the repository root.

```bash
python3 -m pytest
```

> "Seven hundred and nineteen cases, and they finish in about eight seconds with no GPU and no
> network. Seven hundred and sixteen pass and three skip, and the three that skip are the ones that
> need torch, which is deliberate, because one of the two CI jobs installs no machine learning stack
> at all. Every one of these is written against a failure that would otherwise be silent, and its
> docstring names that failure. Phase 2 had zero tests, and that is exactly how it shipped a
> corruption bug."

---

### Segment 6. The failure surface. Shravan. 0:30

Browser.

```
http://127.0.0.1:8000/r/does-not-exist
```

> "A repository that does not exist gives a clean page that says so. No stack trace, no file path, no
> connection string. There is a test that plants a fake secret in the configuration and asserts it
> never reaches an error page, because an error page is the easiest place in a web application to
> leak one."

Then show the headers, which is a five second beat with a good payoff.

```bash
curl -sI http://127.0.0.1:8000/ | grep -i content-security
curl -sI http://127.0.0.1:8000/ | grep -i content-security
```

> "Every page carries a strict content security policy with a script nonce, and the nonce is
> different on each response. Run it twice and you get two values. A reused nonce would defeat the
> point of having one, so there is a test for that too."

---

### Segment 7. Push, and the log growing. Sathwik. 1:10

Terminal B, back in `/tmp/clidemo`, on `main`.

```bash
aethel push -r http://127.0.0.1:8000 --repo review-demo
```

```
Pushing main → review-demo at http://127.0.0.1:8000
5 objects reachable from 24e36e245531 (1 commits, 1 trees, 2 blobs, 1 bases)
Hub 0.1.0 build 168708d

Hub needs 5 of 5 objects.
  sent blob    21ee968a6cbe  62.5 KB
  sent blob    e65b634a2a56  131 B
  sent tree    1cf6f8e285ef  195 B
  sent base    c0c3831886b3  130 B
  sent commit  24e36e245531  385 B

Pushed 5 object(s) · 63.3 KB · 0 already present
Transparency log: 9 leaves (1 new)
```

> "The client works out what the branch reaches, asks the Hub which of those it is missing, and
> uploads only that. First push, the Hub has none of it, so all five objects go."

Now push the other branch, whose history overlaps. This is the segment's actual point.

```bash
aethel checkout wide-rank
aethel push -r http://127.0.0.1:8000 --repo review-demo
```

```
9 objects reachable from 02cdc415e5c4 (2 commits, 2 trees, 4 blobs, 1 bases)
Hub needs 4 of 9 objects.
  ...
Pushed 4 object(s) · 125.8 KB · 5 already present
Transparency log: 10 leaves (1 new)
Log root: 3a922babc687bc796283e7852963db5ef8aa573804d98b5d0310b6f46a601558
```

> "Nine objects reachable, the Hub needs four. A hundred and twenty five kilobytes moved instead of
> the hundred and eighty nine the branch references, so a third was skipped on a two commit
> repository, and the saving grows with shared history. And each accepted push appends one leaf to
> the transparency log and prints the new root. That thirty two byte value is exactly what the
> remaining chain work batches into a Sepolia transaction. The anchoring step is the part that is not
> wired yet, not the log it anchors."

---

### Segment 8. The auditor dashboard. Adyaa. 0:55

Browser.

```
http://127.0.0.1:8000/r/sentiment-lora
```

> "This is the seeded repository, eight commits across three branches. Standing figures at the top,
> accuracy across the history, then the commit table and the branch graph below."

Scroll to the graph.

> "Four things here a panel should push on. The graph is drawn from the commit objects on every
> request, not from a cached table, so it cannot disagree with the store. The accuracy axis spans
> eighty five to ninety five percent and the page says so in words, because a full zero to a hundred
> axis would flatten every one of these differences to nothing. Each Logged chip means that commit is
> a leaf in the transparency log and a proof can be fetched for it. And the root at the foot spans
> every repository on this Hub, recomputed from the log file on each request rather than stored."

> "On the metadata: originally every field the training run produced was written into the commit. My
> part was working out which fields provenance and verification actually need, and dropping the rest,
> because the record is what eventually goes on chain and every field costs."

---

### Segment 9. One commit, and its record. Adyaa. 0:40

Click through to the top commit, or go straight to it.

```
http://127.0.0.1:8000/c/82ba7c844e8d...
```

> "The commit record: the message, the author, the parent, the pinned base reference, the file
> manifest with a hash per file, and the metrics. Everything needed to identify what this version is
> and to fetch it again, and nothing that is only interesting while the training job is running."

Scroll to the proof panel and hand over.

> "Below it is the inclusion proof for this commit, folded rung by rung. That belongs to the log, so
> Sathwik will take it in a moment."

---

### Segment 10. The health board. Adyaa. 0:35

```
http://127.0.0.1:8000/ops
```

> "Four subsystems fail independently, and without this page a failure gets debugged live in front of
> a panel. Every row is computed when the page is requested. The integrity row re-hashes all forty
> four objects, and the transparency row rebuilds the Merkle root from the log file. That is why
> health is the slowest route on the Hub at nine milliseconds, and the only one doing real work
> rather than reading a cache. Each check degrades its own row instead of failing the page, so the
> board survives the failures it reports."

> "Two rows are amber, and both are honest. The chain layer is remaining work, so the log has never
> been anchored and no RPC is configured. Nothing here is hidden to make the demo look finished."

---

### Segment 11. Proof, PASS then FAIL. Sathwik. 1:20

This is the segment the whole video exists for. Slow down and let each output sit on screen.

```bash
curl -s http://127.0.0.1:8000/api/v1/log/proof/$FULL | python3 -m json.tool
```

```json
{
    "commit_hash": "24e36e2455314de3170e039d0e3f8b24feca29437811e89261c80dd1e36747f1",
    "leaf_index": 8,
    "log_size": 10,
    "root": "3a922babc687bc796283e7852963db5ef8aa573804d98b5d0310b6f46a601558",
    "proof": [
        { "sibling": "6a39e6ae8e0a667e...e6e55cd4", "side": "right" },
        { "sibling": "e81652267f323af1...4836e7bb", "side": "left"  }
    ]
}
```

> "Two sibling hashes, sixty four bytes, and that is enough to prove this commit is in a ten leaf
> log. The verifier's work is log base two of the log size, so twenty siblings and six hundred and
> forty bytes would cover a million commits. Auditing one commit does not get more expensive as the
> history grows."

```bash
curl -s -X POST http://127.0.0.1:8000/api/v1/log/verify \
     -H 'content-type: application/json' --data @proof.json
```

```json
{ "valid": true, "commit_hash": "24e36e...47f1", "root": "3a922b...1558" }
```

> "Fold the leaf with each sibling in the order and on the side the proof says, and you land on the
> root the log published. One detail that is easy to miss: a leaf is hashed under a leading zero byte
> and an interior node under a leading one. Without that tag, a leaf value could be passed off as a
> subtree. That is the second preimage weakness a naive Merkle tree admits, and the fix RFC 6962
> specifies."

Now change one character. Say what you are changing before you change it.

```bash
# first hex digit of the commit hash, 2 becomes 3
curl -s -X POST http://127.0.0.1:8000/api/v1/log/verify \
     -H 'content-type: application/json' --data @tampered.json
```

```json
{ "valid": false, "commit_hash": "34e36e...47f1", "root": "3a922b...1558" }
```

> "One character of difference, and the root recomputed from the proof no longer matches the root the
> log published. PASS and FAIL come out of the same code path with a one character change between
> them, which is what makes this checkable by someone who does not trust us and has not read our
> code. The same fold also runs in the browser from the proof alone, on the commit page."

> "And the honest limit, because it is the first thing a good panel asks. The page says Not yet
> anchored, and it means it. Right now the log is append only by policy. A proof issued before an
> edit stops verifying, which is real, but the operator could still rebuild the whole log and issue
> fresh proofs that check out. Anchoring the root to Sepolia is what removes the operator from the
> trusted set, and that is the next piece of work, not a claim we are making today."

---

### Segment 12. What the accuracy number means. Shravan. 0:25

Back to the dashboard chart, or the metrics slide.

> "The evaluator is merged and it runs inside every commit. It loads the pinned base, attaches the
> committed adapter, computes loss, accuracy and adapter size, scores the parent commit the same way,
> and writes the comparison into the record. Two things are outstanding before we quote a number. It
> has to run once on a machine with torch, and it currently scores the dataset recorded at training
> time, which is the file the adapter trained on, so a held out split is what makes the figure mean
> anything. The accuracy values on these pages are seeded fixtures and the deck labels them as such.
> We built the path a metric travels before the number, on purpose."

---

### Segment 13. What is next, and close. Karthik, then Sathwik. 0:40

> "Karthik: next is automated branching. Take the cosine similarity between embeddings of consecutive
> patches, and when it drops past a threshold, warn the developer that this version has diverged and
> offer to fork instead of continuing on the branch. It is a prompt, not a block, and it happens
> before anything is written. Merge semantics come after that."

> "Sathwik: and the chain. The contract, the Sepolia deploy, batching the log root into a
> transaction, and the fourth row on the health board turning green. Everything that root will anchor
> is already built and tested. Thank you for watching."

---

### Take checklist

Record it in segments and cut, rather than in one pass. If a command errors on camera you lose one
segment instead of ten minutes.

- [ ] Hub `version` reports the `.demo/hub-data` path before the first take
- [ ] `/tmp/clidemo` is deleted and rebuilt fresh, so the push shows objects moving
- [ ] The corrupted bit is restored before recording anything after segment 4
- [ ] `proof.json` and `tampered.json` are saved and differ by exactly one character
- [ ] No terminal on camera shows a token, a key, or a path under a personal home directory
- [ ] Browser zoom set so the smallest text on `/ops` is readable at 720p
- [ ] Nothing in the narration calls a seeded accuracy figure a trained result
