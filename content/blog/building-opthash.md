+++
title = "opthash: From Paper to Hardware and Back"
date = "2026-09-06"
slug = "building-opthash"
tags = ["Design", "Research", "Engineering"]
+++

## From Paper to Code

Earlier this year, a [Quanta video about recent breakthroughs in computer science](https://www.quantamagazine.org/videos/2025s-biggest-breakthroughs-in-computer-science/) covered a result that stuck with me: a new way to build [open-addressed](https://en.wikipedia.org/wiki/Open_addressing) hash tables. In open addressing, every key and value lives directly in one array. When a key hashes to a slot that is already taken, the table walks a fixed sequence of other slots, called the _probe sequence_, until it finds an empty one. That walk is why tables slow down as they fill: the fuller the array, the more occupied slots a key steps past before it finds room.

The paper behind the result, [_Optimal Bounds for Open Addressing Without Reordering_](https://arxiv.org/abs/2501.02305), gives two constructions, Elastic Hashing and Funnel Hashing. Both keep insertions and successful lookups cheap even when the table is almost full, and neither ever moves an entry once placed. Elastic Hashing is the more surprising of the two. Classic schemes are greedy: they take the first empty slot their probe sequence reaches, and the paper proves a lower bound that every greedy scheme is stuck with. Elastic gets under that bound by giving each array a probe budget: once the budget runs out, it stops looking in that array and moves on to the next one, even when an empty slot was only one probe away.[^budget]

![Both walks hit the same crowded array, and the third slot they probe is empty. Uniform probing takes it. Elastic has used up its budget of two probes here, so it never tries that slot and moves on to the next, emptier array.](/images/building-opthash/probe-walk.svg)

No official implementation existed, so I wrote one. Building the algorithms seemed a better way to understand them than rereading the proofs, and Rust offered control over memory layout and SIMD, plus an excuse to learn more of the language.

That became [opthash](https://github.com/aaron-ang/opthash-rs). It started as an [exploratory exercise](https://news.ycombinator.com/item?id=48365720). I was curious how these constructions would stack up against the tables most Rust code already relies on, like `std::HashMap` and [`hashbrown`](https://github.com/rust-lang/hashbrown), once caches, vector instructions, allocators, and the demands of a real library got involved. As I optimized them, a harder question came up: would the faster versions still be the algorithms from the paper?

Both constructions shorten the probe walk in a crowded table, but they organize the table differently. Elastic splits the table into a series of arrays, each half the size of the one before, and starts by filling the largest to about 75 percent. After that, insertions proceed in batches, each working on a pair of neighboring arrays, a larger $A_i$ and a smaller $A_{i+1}$. A batch tops up $A_i$ until only a small fraction of its slots are free and fills $A_{i+1}$ to 75 percent, setting up the next pair.

![Elastic fills its arrays in batches. Each array fills to 75 percent while it is the smaller of its pair, then to nearly full while it is the larger. After that, the next batch moves one array down.](/images/building-opthash/elastic-fill.svg)

Within a batch, two checks pick one of three cases for each key:

![Three keys, one per case. Two checks pick the case: if Aᵢ is nearly full, go to Aᵢ₊₁ (Case 2). If Aᵢ₊₁ is already 3/4 full, stay in Aᵢ (Case 3). Otherwise probe Aᵢ up to a budget that grows as Aᵢ fills, then fall back to Aᵢ₊₁ (Case 1).](/images/building-opthash/elastic-insert.svg)

Lookups don't use batches or cases. A lookup can't tell which batch placed a key, so it may have to check every array. It walks one global probe sequence that visits the arrays in a fixed, interleaved order until it finds the key.

Funnel also splits the table into levels that shrink, but each main level is divided into buckets of one fixed width. A later level is smaller because it has fewer buckets, while each bucket keeps the same width. The paper derives the bucket width, level count, and shrink rate from the table's headroom.[^funnel-params] An insertion falls through the levels until it finds room, and a small special area at the bottom catches what the last level can't hold:

![The first key hashes to one bucket per level and falls through full buckets until Level 3 has room. The second key finds every bucket full, gives up on special area B after a few single-slot tries, and lands in special area C, which uses the [power of two choices](https://www.eecs.harvard.edu/~michaelm/postscripts/handbook2001.pdf).](/images/building-opthash/funnel-insert.svg)

Version 1 mapped the paper's structures straight onto Rust structs, with each region owning a `Vec<Option<Entry>>`. The code mirrored the paper's diagrams, which made it easy to check, but it was slow: every region was its own allocation, every probe chased a pointer to reach it, and every slot the probe rejected had already pulled a full entry into cache just to read its tag.

Version 2 kept the regions separate but moved control metadata out of the entries and into its own array. A lookup could now check a one-byte control value before loading any key or value, so most empty or non-matching slots never touched the payload. Version 3 put every region into one allocation, keeping the logical boundaries between levels while placing all their control bytes side by side in memory.[^arena]

```mermaid {caption="Yellow is control data. Gaps are separate allocations. In version 1, the control tag lives inside each slot, so checking it loads the whole entry. From version 2 on, each slot gets a control byte: 0 for empty, 0x80 for a deleted slot (a [tombstone](https://en.wikipedia.org/wiki/Lazy_deletion)), or a 7-bit hash fingerprint. Most probes never touch the entry. The membership filter came later."}
block-beta
  columns 10
  t1["Version 1: one Vec per level: the Option tag sits inside every slot"]:10
  a0t["tag"] a0e["L0 entry"] a0t2["tag"] a0e2["entry ..."] space a1t["tag"] a1e["L1 ..."] space a2t["tag"] a2e["L2 ..."]
  t2["Version 2: one table per level: control bytes split out"]:10
  b0["L0 slots"]:3 b0c["L0 ctrl"]:1 space b1["L1 slots"]:1 b1c["ctrl"]:1 space b2["L2 slots"]:1 b2c["ctrl"]:1
  t3["Version 3: one arena: all control bytes first, then all slots"]:10
  c0["ctrl L0 L1 L2"]:2 c1["L0 slots"]:3 c2["L1"]:2 c3["L2"]:1 c4["membership filter"]:2
  classDef ctrl fill:#f4c95d,stroke:#8a6d1d,color:#000
  classDef title fill:none,stroke:none
  class a0t,a0t2,a1t,a2t,b0c,b1c,b2c,c0 ctrl
  class t1,t2,t3 title
```

Layout was only one part of turning the paper into a library. The paper studies fixed-size, insertion-only tables, but a library must also replace duplicate keys, delete, clear, grow, and handle a key that has tried every slot the construction allows. None of that could be measured fairly while the basic tables were still slow for hardware reasons like cache misses and pointer chasing, so I worked on performance first.

## Making It Fast

The bottleneck was never the number of probes the paper counts. It was what each probe made the processor do: which cache lines it pulled in, which branches it mispredicted, and whether it had to load a whole key just to learn that the slot was wrong.

[SwissTable](https://abseil.io/about/design/swisstables), the design behind Abseil's hash tables and `hashbrown`, was the obvious model for making each probe cheaper. It stores a small hash fingerprint in each control byte and compares a whole group of them in one vector instruction, so one probe can rule out many slots. opthash adopted SwissTable's seven-bit fingerprints and SIMD control-byte scans, and switched to the [`foldhash`](https://github.com/orlp/foldhash) hasher. Rounding sizes to powers of two turned some divisions into bit masks, and the single arena kept the hot control regions together and cut allocation traffic.

All of this made the maps faster and harder to measure. A layout change can shift a field or loop across a cache-line boundary, so a faster run alone doesn't tell you which change helped, or whether the gain will hold on another machine.

The lesson that stuck came from a mini-hash experiment that never merged.[^mini-hash] It stored 4 extra bytes of hash per slot so a lookup could reject more candidates before comparing keys. The first run showed three Funnel workloads in the Python bindings improving by 33 to 39 percent. It looked like a clear win until I noticed the two runs had landed on different classes of CPU core, one clocked a gigahertz faster than the other. Pinned to the same core, most of the win disappeared:

| Funnel workload (Python) |  First run | Pinned to one core |
| ------------------------ | ---------: | -----------------: |
| insert                   | 33% faster |         21% faster |
| union                    | 37% faster |         12% slower |
| runion                   | 39% faster |               flat |
| lookup hit               |          – |          4% slower |
| median across the suite  |          – |               flat |

A flat median wasn't worth 4 extra bytes per slot, so I dropped the idea. More importantly, I stopped trusting wall-clock numbers on their own. The local harness now:

- pins each run to one core and fixes memory placement
- saves named baselines to compare against
- reruns the unchanged `std` and `hashbrown` maps as controls for the noise floor
- checks assembly and hardware counters to tell fewer instructions apart from better cache or branch behavior

Smaller experiments kept confirming the need.[^small-experiments] Much later, I found the benchmark fixture itself was skewed: at 20,000 entries, Elastic and `hashbrown` were 70 percent full while Funnel was completely full. Now every map is filled to capacity before anything is measured.[^fixture]

Better measurement caught wins that were noise and wins that only held on one machine. It also exposed a bigger problem. Some optimizations that measured well, notably power-of-two geometry and changed probing arithmetic, had quietly changed which slots the algorithms visited. The maps were faster because they were no longer quite the paper's algorithms.

## Returning to the Paper

Lining the code up against the paper made the drift obvious. It wasn't one bug: several optimizations had each nudged the probe sequence, and Funnel's bucket and overflow handling had mismatches of its own. Patching them one at a time could never yield a trustworthy reference, because another optimization might still be changing geometry or probe order somewhere I hadn't looked.

So the project got an exact-default rewrite around one fixed instantiation of the paper's constructions.[^exact] Every optimization faced one test: does it change which slots get probed, or only how each slot is checked?

| Removed: changed which slots get probed | Kept: changes only how slots are checked |
| --------------------------------------- | ---------------------------------------- |
| Power-of-two level sizes                | Single arena                             |
| Triangular Elastic probing              | Control bytes with fingerprints          |
| Altered probe budgets                   | SIMD control-byte scans                  |
| Performance-oriented reserve policy     |                                          |

Elastic still rounds its _total_ size up to a power of two. The paper fixes how the space is split into arrays, not the overall table size, so this doesn't break exactness. I tried exact total sizing anyway. It saved about 45 percent of memory at a million entries, but lookups and deletes got much slower, so I kept the power-of-two total.[^sizing]

Scalar reference implementations enforce the rule. They are written to be read, not to be fast: no SIMD, no cached state, just the paper's rules in the paper's order. Optimized tables may change how they check slots, but not which slots they visit or in what order. Here is the reference's Elastic insertion rule for one batch, lightly trimmed, with the same three cases as the diagram above:

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

Because the reference decides where every probe lands, it doubles as a test oracle. Tests insert the same keys into both tables and check that every key lands in the same slot at the same probe position.

```mermaid {caption="The equivalence check. If a single key lands somewhere else, the optimization changed the algorithm."}
flowchart LR
  in["same keys"] --> ref["Scalar reference"]
  in --> opt["Optimized table<br/>SIMD, cached state"]
  ref --> eq{"same slot and<br/>probe position?"}
  opt --> eq
  eq -- yes --> ok["same algorithm, maybe faster"]
  eq -- no --> bad["test fails: algorithm changed"]
```

The rewrite made both maps much slower. Here is the cost on a 20,000-entry map of `u64` keys, in nanoseconds per operation, before (`v0.10.3`) and after the rewrite:[^bench-setup]

| Map         |            Insert |             Hit |            Miss |
| ----------- | ----------------: | --------------: | --------------: |
| Elastic     | 9.4 → 110 (11.7x) | 3.7 → 32 (8.6x) | 3.7 → 264 (70x) |
| Funnel      |   8.0 → 17 (2.1x) | 5.0 → 15 (3.0x) | 8.0 → 55 (6.9x) |
| `hashbrown` |               3.6 |             2.1 |             1.9 |

These numbers compare two versions of opthash, so they show how much slower exactness made this implementation. They don't test the paper's bounds, which count probes rather than nanoseconds.[^controls]

To see why the exact version is slower, I read the CPU's [hardware counters](https://www.brendangregg.com/perf.html) with `perf`. Each version ran successful Elastic lookups for five seconds on the same pinned core, old version first:

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

The totals look alike because both ran for five seconds. Divide by the number of lookups each version completed in that window and the picture changes. Both versions take about one L1 miss per lookup and run at the same instructions per cycle. The exact version executes about 8x the instructions per lookup, roughly 590 against 70, and takes 13x the branch misses. On this fixture the exact hit path is not memory-bound. It is doing more work: walking the paper's interleaved probe order and doing [exact range reduction](https://arxiv.org/abs/1805.10941) on every candidate. Insert is different. There the exact version also takes about 10x the cache misses per operation. It visits far more slots before it settles, and those slots are spread across two arrays instead of one contiguous group.

I kept the slower, exact version anyway. A fast implementation of a different candidate order couldn't answer the question that started the project: how do the paper's algorithms behave on real hardware? From then on, the reference decided which optimizations were allowed: if an optimization disagrees with the reference about a single slot, it is changing the algorithm, not speeding it up.

The first round of tuning showed the exact version could still get much faster. Streamlining the hot paths made Elastic insertion about 4x faster without changing a single slot it visits.[^hot-paths] That narrowed the project's question to this: how fast can the paper's design get on its own, with the library features kept out of the way?

## Beyond the Paper

A real library must also delete, clear, grow, and handle a key running out of slots to try, all without changing how the paper places keys. So opthash splits a table's lifecycle into epochs. Inside an epoch, inserts and lookups follow the paper exactly. Everything else, from growth to cleanup after deletes, runs as separate library code between epochs, and each run starts a new epoch under the paper's rules.

```mermaid {caption="Every epoch follows the same paper rules for inserts and lookups. Each dashed arrow is library code that runs out of band and starts a fresh epoch: growth when an insert finds the table full, placement recovery when a key has no allowed slot left, and cleanup, clear, or resize after deletes or on request. Only placement recovery puts a key where the paper wouldn't."}
flowchart TB
  e1["Epoch N: paper rules"] -. "table full: grow" .-> e2["Epoch N+1: paper rules"]
  e2 -. "no allowed slot: placement recovery" .-> e3["Epoch N+2: paper rules"]
  e3 -. "cleanup, clear, or resize" .-> e4["Epoch N+3: paper rules"]
```

Placement recovery is the one exception. The paper gives each key a finite list of slots it may use, and under heavy churn a real table can end up with every one of them taken. Rather than drop the key, opthash rebuilds the table at the same size and then places the key outside the paper's rules. So "paper-exact" describes normal operation, not recovery.[^hashing]

Keeping library code from changing the paper's placements took some fixes. Elastic used to pick its active batch from a running count of inserts. Under a steady mix of inserts and deletes, that count kept climbing even though the number of live keys stayed flat, so the batch index crept forward and new keys drifted into the smallest levels, where they quickly ran out of room and set off recovery. Counting live entries instead of total inserts fixed it.[^churn]

The same split applies to benchmarks. Inserts and successful lookups are closest to what the paper analyzes, and Funnel's analysis also covers failed lookups. But Elastic misses, deletes, and growth measure the library around the paper. One combined score would hide where the time goes, so opthash reports them separately. Every run also includes `std::HashMap` and `hashbrown`, with `hashbrown` as a reference point, not a target.[^python]

## Takeaways

### Pin Before You Believe

The mini-hash experiment looked like a 33 to 39 percent win until both runs were pinned to the same core. The skewed fixture compared maps at different loads, and no number looked wrong. Pinning the core, fixing memory placement, and rerunning an unchanged baseline like `hashbrown` costs a few minutes per run. It is the most reliable way I know to tell a real speedup from a lucky one.

### Build the Reference First

I wrote the scalar reference only after the optimized code had drifted, and untangling that drift took a rewrite. Built first, it would have checked every optimization the day it was written, turning "is this still the algorithm?" into a failing test instead of an audit. A slow, obvious reference beats a clever one here, because its only job is to be believed.

### Faster but Different Isn't Faster

When the goal is to study an algorithm, a speedup counts only if the algorithm stays the same. Power-of-two geometry and triangular probing are good ideas for hash tables in general. They were wrong here because they changed which slots get visited, and so which algorithm was measured. The same check applies to implementing any paper: decide up front what the paper fixes and what it leaves open, and treat anything that moves the first as a different design.

### Measure the Algorithm and the Library Separately

The paper analyzes inserts and lookups in a fixed-size table. Deletes, growth, and recovery belong to the library, and they can dominate a benchmark while saying nothing about the paper's bounds. Separate epochs and separate benchmark groups showed which part was slow, the first step toward fixing either.

## What's Still Open

Later work kept the paper's slot order and made each probe cheaper. A membership filter, a small [Bloom filter](https://en.wikipedia.org/wiki/Bloom_filter) at the end of the arena, lets most failed lookups return without probing,[^filter] and probing itself got cheaper.[^probing] Both maps have won back much of the speed the rewrite cost, but neither matches `hashbrown` yet.

Deletes on a nearly full table are still slow. A delete-heavy workload keeps pushing Elastic into placement recovery, and each recovery rebuilds the whole table at the same size before placing the key outside the paper's rules. Paying for that rebuild again and again is what makes these deletes slow.

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
