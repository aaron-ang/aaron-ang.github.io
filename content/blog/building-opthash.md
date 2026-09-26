+++
title = "opthash: From Paper to Hardware and Back"
date = "2026-09-06"
slug = "building-opthash"
tags = ["Design", "Research", "Engineering"]
+++

## From Paper to Code

Earlier this year, a [Quanta video about recent breakthroughs in computer science](https://www.quantamagazine.org/videos/2025s-biggest-breakthroughs-in-computer-science/) covered a result that stuck with me: a new way to build [open-addressed](https://en.wikipedia.org/wiki/Open_addressing) hash tables. In open addressing, every key and value lives directly in one array. When a key initially hashes to an occupied slot, the table walks a sequence of slots (the _probe sequence_) until it finds an empty one. Tables slow down as they fill, because the fuller the array, the longer that walk gets.

The paper behind the result, [_Optimal Bounds for Open Addressing Without Reordering_](https://arxiv.org/abs/2501.02305), gives two constructions, Elastic Hashing and Funnel Hashing. Both keep insertions and successful lookups cheap even when the table is almost full, and neither ever moves an entry once it is placed. The surprising one is Elastic Hashing. Classic schemes are greedy: take the first empty slot you find; elastic deliberately skips past empty slots in its own probe sequence, allowing it to beat the lower bound that holds for greedy probing.[^budget]

![Both walks hit the same crowded array, and the third slot they probe is empty. Uniform probing takes it. Elastic has used up its budget of two probes here, so it never tries that slot and moves on to the next, emptier array.](/images/building-opthash/probe-walk.svg)

There was no official implementation to be found, so I decided to write one. Building the algorithms seemed a better way to understand them than rereading the proofs, and Rust offered control over memory layout and SIMD along with an excuse to learn more of the language.

That became [opthash](https://github.com/aaron-ang/opthash-rs). It started as an [exploratory exercise](https://news.ycombinator.com/item?id=48365720), not necessarily to replace `std::HashMap` or [`hashbrown`](https://github.com/rust-lang/hashbrown). The open question was how these constructions would hold up once caches, vector instructions, allocators, and the demands of a real library got involved. As it happened, the harder question was whether they would survive being optimized at all.

The two constructions attack the same collision problem with different shapes. Elastic splits the table into arrays that each halve in size, and fills the largest to about 75 percent before moving on. After that, insertions work in batches across two adjacent arrays, a larger $A_i$ and a smaller $A_{i+1}$. Each batch tops up $A_i$ until only a small fraction is free, and fills $A_{i+1}$ to 75 percent.

![Elastic fills its arrays in batches. Each batch works on one pair of neighboring arrays, then the next batch moves one array down.](/images/building-opthash/elastic-fill.svg)

Within a batch, three cases decide where each key goes:

```mermaid {caption="Elastic insertion during batch $i$. Case 1 is the normal path. Cases 2 and 3 kick in once an array hits its fill target."}
flowchart TD
  start["Insert during batch i"] --> full{"A<sub>i</sub> nearly full?"}
  full -- yes --> c2["Case 2: first free slot in A<sub>i+1</sub>"]
  full -- no --> sparse{"A<sub>i+1</sub> already ¾ full?"}
  sparse -- yes --> c3["Case 3: first free slot in A<sub>i</sub>"]
  sparse -- no --> c1["Case 1: try A<sub>i</sub> up to a budget<br/>(grows as A<sub>i</sub> fills, capped)"]
  c1 -- "budget used up" --> c1b["first free slot in A<sub>i+1</sub>"]
```

A lookup does not know any of this. It walks one global probe sequence that interleaves every array in a fixed order until it finds the key.

Funnel also uses shrinking levels, but its main levels are made of fixed-width buckets: later levels have fewer buckets, not narrower ones. The paper sets the bucket width, level count, and shrink ratio from how much headroom the table keeps.[^funnel-params] An insertion falls through the levels until it finds room:

```mermaid {caption="Funnel insertion: at each level the key hashes to one bucket and takes its first empty slot. Special area C uses the [power of two choices](https://www.eecs.harvard.edu/~michaelm/postscripts/handbook2001.pdf)."}
flowchart TB
  subgraph main["Main levels"]
    direction LR
    L1["Level 1<br/>one bucket,<br/>chosen by the key"] -- "full" --> L2["Level 2<br/>~3/4 as many<br/>buckets"]
    L2 -- "full" --> Ln["...<br/>last level"]
  end
  subgraph special["Special area"]
    direction LR
    B["B<br/>a few single-slot<br/>tries"] -- "all taken" --> C["C<br/>two buckets,<br/>alternating"]
  end
  main -- "last level full" --> special
```

The first version mapped the paper's structures straight onto Rust structs. Each region owned a `Vec<Option<Entry>>`. The code looked like the diagrams, and that was about all it had going for it. Every region was its own allocation, every probe chased a pointer, and every rejected slot had already pulled a full entry into cache.

The first redesign kept the regions separate but split control metadata out from the entries. A lookup could check a compact control byte before loading a key and value, which skipped most payload reads for empty or non-matching slots. Later, a single-arena refactor moved every region into one allocation while keeping the logical boundaries.[^arena]

```mermaid {caption="Yellow is control data. Gaps are separate allocations. In version 1, the control tag lives inside each slot, so checking it loads the whole entry. From version 2 on, each slot gets a control byte: 0 for empty, 0x80 for a deleted slot (a [tombstone](https://en.wikipedia.org/wiki/Lazy_deletion)), or a 7-bit hash fingerprint. Most probes never touch the entry. The membership filter came later."}
block-beta
  columns 10
  t1["1. One Vec per level: the Option tag sits inside every slot"]:10
  a0t["tag"] a0e["L0 entry"] a0t2["tag"] a0e2["entry ..."] space a1t["tag"] a1e["L1 ..."] space a2t["tag"] a2e["L2 ..."]
  t2["2. One table per level: control bytes split out"]:10
  b0["L0 slots"]:3 b0c["L0 ctrl"]:1 space b1["L1 slots"]:1 b1c["ctrl"]:1 space b2["L2 slots"]:1 b2c["ctrl"]:1
  t3["3. One arena: all control bytes first, then all slots"]:10
  c0["ctrl L0 L1 L2"]:2 c1["L0 slots"]:3 c2["L1"]:2 c3["L2"]:1 c4["membership filter"]:2
  classDef ctrl fill:#f4c95d,stroke:#8a6d1d,color:#000
  classDef title fill:none,stroke:none
  class a0t,a0t2,a1t,a2t,b0c,b1c,b2c,c0 ctrl
  class t1,t2,t3 title
```

Layout was not the whole problem. The paper studies fixed-size, insertion-only tables. A library has to replace duplicate keys, delete, clear, grow, and do something sensible when a finite candidate sequence runs out. None of that could be judged fairly, though, until the basic paper-shaped tables stopped fighting the hardware on every probe.

## Making It Fast

The bottleneck was never the theoretical probe count. It was what each probe made the processor do, and compact metadata let a probe reject a slot before touching its payload at all.

[SwissTable](https://abseil.io/about/design/swisstables) was the obvious model. It keeps a small hash fingerprint in each control byte and compares a whole vector of them at once. opthash picked up seven-bit fingerprints, SIMD control-byte scans, and the [`foldhash`](https://github.com/orlp/foldhash) hasher. Power-of-two sizes turned some divisions into masks. The single arena kept the hot control regions close together and cut allocation traffic.

All of this made the maps faster. It also made them harder to measure. A layout change can push fields or code across a cache-line boundary, so a faster run does not by itself tell you what got faster.

The lesson that stuck came from a mini-hash experiment that never merged.[^mini-hash] It stored 4 extra bytes per slot so a lookup could reject more candidates before comparing keys, and the first run showed three Funnel Python workloads improving by 33 to 39 percent. The two runs had landed on different classes of CPU core, one clocked a gigahertz faster than the other. Pinned to the same core, most of the win disappeared:

| Funnel workload (Python) |  First run | Pinned to one core |
| ------------------------ | ---------: | -----------------: |
| insert                   | 33% faster |         21% faster |
| union                    | 37% faster |         12% slower |
| runion                   | 39% faster |               flat |
| lookup hit               |          – |          4% slower |
| median across the suite  |          – |               flat |

A flat median wasn't worth 4 extra bytes per slot, so I dropped it. After that, I stopped trusting wall-clock numbers on their own. The local harness now:

- pins each run to one core and fixes memory placement
- saves named baselines to compare against
- reruns the unchanged `std` and `hashbrown` maps as controls for the noise floor
- checks assembly and hardware counters to tell fewer instructions apart from better cache or branch behavior

Smaller experiments kept confirming the need for this.[^small-experiments] Much later, I found the fixture itself was skewed. At 20,000 entries, Elastic and `hashbrown` were 70 percent full while Funnel was completely full. Now every map is filled to the most it can hold before anything is measured.[^fixture]

Better measurement caught noisy wins and local wins. It also turned up something worse than noise. Some optimizations that measured well, power-of-two geometry and changed probing arithmetic in particular, had changed which slots the algorithms visited. The maps were faster because they were no longer quite the algorithms from the paper.

## Returning to the Paper

Line the code up against the paper and the drift was obvious. Rounding the arrays to powers of two changed how hashes mapped to slots. Elastic's triangular walk visited groups out of the paper's order. Altered probe budgets and reserve behavior moved the point where insertion gave up or kept going. Funnel's bucket and overflow handling had its own mismatches. Each one looked like a one-off fix at first, but patching could never produce a trustworthy reference while some other optimization might still be quietly changing geometry or probe order elsewhere.

So the project got an exact-default rewrite around one fixed instantiation of the paper's constructions.[^exact] The rule was simple: did an optimization change which slots get probed?

| Removed: changed which slots get probed | Kept: changes only how slots are checked |
| --------------------------------------- | ---------------------------------------- |
| Power-of-two level sizes                | Single arena                             |
| Triangular Elastic probing              | Control bytes with fingerprints          |
| Altered probe budgets                   | SIMD control-byte scans                  |
| Performance-oriented reserve policy     |                                          |

One thing stayed: Elastic still rounds its total size up to a power of two. The paper doesn't say how big the table should be, so this doesn't break exactness. I tried exact sizing anyway. It saved about 45 percent of memory at a million entries, but lookups and deletes got much slower, so I dropped it.[^sizing]

Simple scalar reference implementations enforce the rule. Optimized code can change how it checks slots, but not which slots it visits or in what order.

The reference is deliberately dumb. Condensed, the Elastic insertion rule for a batch reads:

```rust
// Batch i works across levels `current` (A_i) and `next` (A_{i+1}).
let free_current = self.levels[current] - self.occupancy[current];
let free_next = self.levels[next] - self.occupancy[next];
let current_threshold = floor_div_pow2(self.levels[current], reserve + 1); // δ|A_i|/2

if free_current <= current_threshold {
    // Case 2: A_i has reached its fill target. First free slot in A_{i+1}.
    self.uniform_vacancy(next, key)
} else if free_next <= self.levels[next] / 4 {
    // Case 3: A_{i+1} is already 3/4 full. First free slot in A_i.
    self.uniform_vacancy(current, key)
} else {
    // Case 1: a bounded budget of probes in A_i, computed from how
    // full it is, then fall through to the first free slot in A_{i+1}.
    let budget = probe::elastic_dyadic_probe_budget(free_current, self.levels[current], reserve, C)
        .expect("valid budget inputs");
    (0..budget)
        .find_map(|j| self.vacancy(current, key, j))
        .unwrap_or_else(|| self.uniform_vacancy(next, key))
}
```

The reference decides where every probe lands. Tests insert the same keys into the reference and the optimized table, then check that each key ends up in the same slot.

```mermaid {caption="The equivalence check. If a single key lands somewhere else, the optimization changed the algorithm."}
flowchart LR
  in["same keys"] --> ref["Scalar reference"]
  in --> opt["Optimized table<br/>SIMD, cached state"]
  ref --> eq{"same slot and<br/>probe position?"}
  opt --> eq
  eq -- yes --> ok["same algorithm, maybe faster"]
  eq -- no --> bad["test fails: algorithm changed"]
```

Being exact was expensive. Here is the cost on a 20,000-entry map of `u64` keys, in nanoseconds per operation, before (`v0.10.3`) and after the rewrite:[^bench-setup]

| Map         |            Insert |             Hit |            Miss |
| ----------- | ----------------: | --------------: | --------------: |
| Elastic     | 9.4 → 110 (11.7x) | 3.7 → 32 (8.6x) | 3.7 → 264 (70x) |
| Funnel      |   8.0 → 17 (2.1x) | 5.0 → 15 (3.0x) | 8.0 → 55 (6.9x) |
| `hashbrown` |               3.6 |             2.1 |             1.9 |

Keep in mind this compares two versions of opthash. It says nothing about the paper's bounds.[^controls]

[Hardware counters](https://www.brendangregg.com/perf.html) say where the time went. Five seconds of Elastic hit lookups on the same pinned core, old version first:

```
$ perf stat -e cycles,instructions,cache-misses,branch-misses speedup --profile-time 5 '^get_hit/get_hit_elastic$'

v0.10.3
    20,136,315,080  cycles
    95,752,902,677  instructions        # 4.76 insn per cycle
     1,338,515,144  cache-misses
        37,048,235  branch-misses

exact rewrite
    19,511,160,964  cycles
    92,545,663,178  instructions        # 4.74 insn per cycle
       175,001,595  cache-misses
        55,432,818  branch-misses
```

The totals look alike because both ran for five seconds. Divide by the number of lookups each version completed in that window and the picture changes. Both versions take about one L1 miss per lookup and run at the same instructions per cycle. The exact version simply executes about 8x the instructions per lookup, roughly 590 against 70, and takes 13x the branch misses. On this fixture the exact hit path is not memory-bound. It is doing more work: walking the paper's interleaved probe order and doing [exact range reduction](https://arxiv.org/abs/1805.10941) on every candidate. Insert is different. There the exact version also takes about 10x the cache misses per operation. It visits far more slots before it settles, and those slots are spread across two arrays instead of one contiguous group.

The slower version stayed. A fast implementation of a different candidate order could not answer the question that started the project. The reset also supplied an oracle: if an optimization disagrees with the reference about a single slot, it is changing the algorithm, not speeding it up.

The first round of tuning showed there was still room. Streamlining the hot paths made Elastic insertion about 4x faster without changing which slots it visits.[^hot-paths] That gave the project a clearer question: how fast can the paper's design get on its own, with the library features kept separate?

## Beyond the Paper

A real library also has to delete, clear, grow, and cope when a key runs out of slots to try. None of that should change how the paper places keys. So opthash splits a table's life into epochs. Inside an epoch, inserts and lookups follow the paper exactly. Deletion, cleanup, clearing, and growth run in separate code, and each one starts a new epoch.

```mermaid {caption="Inside an epoch, inserts and lookups follow the paper's rules. Dashed boxes are library code that runs out of band: growth runs before an insert into a full table, cleanup runs after deletes leave too many tombstones, and clear or resize runs when you ask. Each one starts a fresh epoch. Only placement recovery puts a key where the paper wouldn't."}
flowchart TB
  subgraph epoch["Epoch N: paper rules"]
    op["insert or lookup"] --> walk["walk the paper's<br/>probe order"] --> done["key placed<br/>or found"]
  end
  api["delete, clear,<br/>or resize call"]
  op -. "table is full" .-> grow["grow the table"]
  walk -. "no allowed<br/>slot is free" .-> rec["placement recovery:<br/>rebuild, then place<br/>outside paper rules"]
  api -. "too many tombstones,<br/>or user asked" .-> ev["cleanup, clear,<br/>or resize"]
  grow --> next["Epoch N+1:<br/>paper rules again"]
  rec --> next
  ev --> next
  classDef oob stroke-dasharray: 5 4
  class grow,rec,ev oob
```

Recovery is the one exception. If every slot a key may use is taken, opthash widens the search rather than drop the key. So "paper-exact" covers normal operation, not recovery.[^hashing]

Keeping that split clean took work. Elastic used to pick its active batch from a running count of inserts. Under constant inserts and deletes, that count kept growing even though the table didn't, so new keys drifted into the smallest levels and set off recovery. Counting live entries instead fixed it.[^churn]

The same split applies to benchmarks. Inserts and successful lookups are closest to what the paper analyzes, and Funnel's analysis covers failed lookups too. Elastic misses, deletes, and growth measure the library around the paper. Mixing them into one score hides where the time goes. Every run also includes `std::HashMap` and `hashbrown`, with `hashbrown` as a reference point, not a target.[^python]

## Takeaways

- **Pin before you believe.** An unpinned speedup might just be a faster CPU core, and an uneven fixture can mislead too. Rerun an unchanged baseline like `hashbrown` every time, so you can see the noise.
- **Build the reference first.** A simple scalar version turns "is this still the algorithm?" into a test.
- **Faster but different isn't faster.** If an optimization changes which slots get visited, it's a different algorithm.
- **Measure the algorithm and the library separately.** Keep the paper's operations apart from deletes, growth, and recovery.

## What's Still Open

Later work kept the paper's slot order. A membership filter, a small [Bloom filter](https://en.wikipedia.org/wiki/Bloom_filter) at the end of the arena, lets most failed lookups return without probing,[^filter] and probing itself got cheaper.[^probing] Both maps have won back much of the lost speed, but neither matches `hashbrown`. Deletes on a nearly full table are still slow. A delete-heavy workload keeps pushing Elastic into placement recovery, which rebuilds the whole table at the same size before placing the key outside the paper's rules. Paying for that rebuild over and over is what makes these deletes slow.

The big question is still open: can the paper's probe bounds make opthash as fast as `hashbrown` in real use, without changing the algorithms again?

To try it, opthash is on [PyPI](https://pypi.org/project/opthash/) (`pip install opthash`) and [crates.io](https://crates.io/crates/opthash) (the Rust crate also builds without `std`).

[^budget]: With [uniform probing](https://doi.org/10.1145/3828.3836), the expected walk grows with $1/\varepsilon$, where $\varepsilon$ is the free fraction of the array. Elastic's budget is $c \cdot \min(\log^2(1/\varepsilon), \log(1/\delta))$, where $\delta$ is the free fraction of the whole table. That $\log(1/\delta)$ cap is how it beats the greedy bound.

[^funnel-params]: opthash pins one such choice and treats any change to it as a different algorithm for trace purposes.

[^arena]: See [opthash#68](https://github.com/aaron-ang/opthash-rs/pull/68). Each level became a pointer and a count into the shared arena.

[^mini-hash]: See [opthash#12](https://github.com/aaron-ang/opthash-rs/pull/12) for the full workload table, Elastic included.

[^small-experiments]: Software prefetching did nothing or hurt, reordering fields sometimes produced worse codegen, and a 64-lane AVX-512 path was dropped for the existing 16-lane x86 scan once the generated code showed no gain. The ARM machine used here scans 8 lanes with NEON. One change, swapping a batch-plan `Vec` for a boxed slice, lost 6.5 percent on its own and was reverted, then came back for free inside a later layout cleanup.

[^fixture]: See [opthash#139](https://github.com/aaron-ang/opthash-rs/pull/139). The fixture grew from 20,000 to 28,672 entries, the most every map can hold.

[^exact]: See [opthash#115](https://github.com/aaron-ang/opthash-rs/pull/115). The paper leaves Elastic's probe-budget constant free and treats its Funnel constants as sufficient rather than canonical, so "exact" here means exact to the instantiation opthash pins.

[^sizing]: See [opthash#137](https://github.com/aaron-ang/opthash-rs/pull/137), which also lists smaller layout ideas that were tried and dropped.

[^bench-setup]: The regression was first recorded in the [design notes](https://github.com/aaron-ang/opthash-rs/blob/e2aab0104d5afad0749a9e5fb949cb43f68aba6c/docs/superpowers/specs/2026-07-11-paper-faithful-performance-parity-design.md#context). A fresh run for this article reproduced it: same fixtures, 100,000 operations per sample, interleaved twice on one pinned Cortex-X925 core at 3.9 GHz. At 20,000 entries, Elastic and `hashbrown` are 70 percent full and Funnel is full, so compare each row before and after, not maps against each other.

[^controls]: Both pairs agreed within 5 percent on every cell. The `std` and `hashbrown` controls were flat on insert and hit. On miss, `std` was 1.5x slower and `hashbrown` 0.8x in the exact tree's binary, identically in both pairs, so that is the two bench binaries compiling the same control code differently, not run-to-run noise.

[^hot-paths]: See [opthash#121](https://github.com/aaron-ang/opthash-rs/pull/121), which measured Elastic insert 76.63 percent faster. Its controls were noisy, so only the insert gain counts.

[^filter]: See [opthash#132](https://github.com/aaron-ang/opthash-rs/pull/132). Each key sets two bits in a 64-bit word shared by ten slots. Bits are never cleared one key at a time, so the filter can wrongly say "maybe" but never wrongly say "no".

[^probing]: See [opthash#135](https://github.com/aaron-ang/opthash-rs/pull/135).

[^hashing]: The paper's analysis assumes truly random hash functions. opthash uses fixed mixers instead, built from [wyhash](https://github.com/wangyi-fudan/wyhash)'s default secrets and [SplitMix64](https://prng.di.unimi.it/splitmix64.c)'s constants. So "paper-exact" holds for opthash's own hashing, but it isn't a proof that fixed mixers inherit the paper's bounds.

[^churn]: See [opthash#135](https://github.com/aaron-ang/opthash-rs/pull/135). Over 100,000 mixed inserts and deletes, recovery rebuilds went from 11 to zero.

[^python]: The Python bindings need the same care. Python hashing, interpreter overhead, the [GIL](https://docs.python.org/3/glossary.html#term-global-interpreter-lock), and the [PyO3](https://pyo3.rs/) crossing can cost more than the Rust operation underneath, so those numbers measure a binding workload rather than the table.
