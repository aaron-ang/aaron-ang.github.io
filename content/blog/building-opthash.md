+++
title = "opthash: Bridging Theory with Hardware"
date = "2026-09-06"
slug = "building-opthash"
tags = ["Design", "Research", "Engineering"]
+++

In [open addressing](https://en.wikipedia.org/wiki/Open_addressing), the keys and values of a hash table sit directly in one array. If a key hashes to a slot that another key already holds, the table tries other slots in a fixed order, called the _probe sequence_, and stops at the first empty one. The fuller the table gets, the more taken slots a key steps past before it finds room. Each walk along the probe sequence gets longer and slower.

The paper [_Optimal Bounds for Open Addressing Without Reordering_](https://arxiv.org/abs/2501.02305) answers that slowdown with two constructions, Elastic Hashing and Funnel Hashing. With either one, insertions and successful lookups stay cheap even in an almost-full table, and neither ever moves an entry after placing it. The paper also disproved a 40-year-old conjecture about open-addressed tables. The result was featured in [Quanta's video on 2025's biggest breakthroughs in computer science](https://www.quantamagazine.org/videos/2025s-biggest-breakthroughs-in-computer-science/).

No official implementation was available, and [opthash](https://github.com/aaron-ang/opthash-rs) is an attempt to understand the algorithms by building them. It was written in Rust, because Rust offered control over memory layout and SIMD and I wanted to learn more of the language. The project started as an [exploratory exercise](https://news.ycombinator.com/item?id=48365720) to see how these constructions compare with the tables that almost all Rust code relies on today, such as [`std::HashMap`](https://doc.rust-lang.org/std/collections/struct.HashMap.html) and [`hashbrown`](https://github.com/rust-lang/hashbrown). To compete with them, opthash had to make good use of caches, vector instructions, and allocators. It also needed the parts of a general-purpose map that the paper never covers, such as deleting keys and growing.

The main lesson of the project is about scope. When the goal is to study the paper's algorithm, a speedup only counts if the algorithm stays the same. The paper fixes two things per key: which slots the key may use, and the order in which it tries them. The paper leaves open how to check a slot, and that part belongs to the code. Every faithful insert tries the same slots in the same order, but each implementation may decide for itself how to tell whether a slot is empty or holds the key. This post calls the split the faithfulness rule: the code may change how a slot is checked, but never which slots a key visits. Several early optimizations in opthash broke the rule and were removed to keep the code faithful to the paper.

This post follows one example key through the project. It first shows how Elastic places the key and how Funnel differs, then how opthash made each probe cheaper. Next come the measurement problems that exposed the unfaithful optimizations, the rewrite that follows the paper exactly, and the cost of that rewrite. The last sections cover the library features that run outside the paper's rules, the lessons for implementing the next paper, and the problems that are still open.

## Elastic Hashing

Most open-addressed tables are _greedy_: each key goes in the first empty slot its probe sequence reaches. A result by Andrew Yao, which still stands, says that the average insertion cost of any greedy table must rise as the table fills.[^greedy] Elastic is _not_ greedy, which puts it outside that result.

Elastic cuts the table into a series of arrays, each half the size of the one before. Each array gets a probe budget. When Elastic spends the budget, it gives up on that array and moves to the next one, even if the array still has empty slots. Because Elastic is not greedy, its average insertion cost stays flat as the table fills. The figure compares one such skip with uniform probing, the textbook greedy scheme:

![Both walks hit the same crowded array, and the third slot they probe is empty. Uniform probing takes it. Elastic has used up its budget of two probes here, so it never tries that slot and moves on to the next, emptier array.](/images/building-opthash/probe-walk.svg "Skipping one free slot costs Elastic little, because the next array has far more room.")

Elastic fills its arrays in batches. The largest array fills first, to about 75 percent. After that, each batch works on two neighboring arrays, a larger $A_i$ and a smaller $A_{i+1}$. The batch tops up $A_i$ until only a small fraction of its slots are free, and it fills $A_{i+1}$ to 75 percent, which sets up the next pair:

![Elastic fills its arrays in batches. Each array fills to 75 percent while it is the smaller of its pair, then to nearly full while it is the larger. After that, the next batch moves one array down.](/images/building-opthash/elastic-fill.svg "Blue is the filled part of each array.")

Within a batch, each key gets two checks, one on how full $A_i$ is and one on how full $A_{i+1}$ is. Together they pick one of three cases, and the probe budget applies in Case 1:

![Three keys, one per case. Each checks whether Aᵢ is nearly full (Case 2: go to Aᵢ₊₁) and whether Aᵢ₊₁ is already 3/4 full (Case 3: stay in Aᵢ); otherwise it probes Aᵢ within a budget (Case 1).](/images/building-opthash/elastic-insert.svg "The budget grows as Aᵢ fills: keys late in a batch probe Aᵢ longer before falling back.")

Take an example key in Case 1 whose probe sequence starts $A_i[17]$, $A_i[203]$, $A_{i+1}[88]$. Each position comes from the key's hash, so consecutive probes land at scattered positions, not in neighboring slots. With a budget of two probes, Elastic tries $A_i[17]$ and $A_i[203]$ and gives up on $A_i$, even if $A_i$ still has free slots. It then moves on to $A_{i+1}[88]$, its first free slot in the next array. Here and throughout the post, slot numbers are positions within one array, not offsets in memory.

Lookups ignore batches and cases. A lookup has no way to know which batch placed a key, and it may need to check every array. It walks a single global probe sequence instead, visiting the arrays in a fixed, interleaved order until it reaches the key. A lookup for the example key therefore does not simply retrace $A_i[17]$, $A_i[203]$, and $A_{i+1}[88]$.

## Funnel Hashing

In 1985, Yao also conjectured that uniform probing has close to the lowest worst-case insertion cost any greedy table can reach. Funnel, unlike Elastic, is greedy. Farach-Colton et al. disproved the conjecture with it: Funnel's worst case is much lower.[^greedy]

Like Elastic, Funnel splits the table into shrinking levels, but it divides each main level into buckets of one fixed width. Later levels hold fewer buckets, and that is the only reason they are smaller. The paper derives the bucket width, the level count, and the shrink rate from how much headroom the table has. More than one set of values satisfies the paper's formulas, and opthash fixes one of them. An insert drops through the levels until one has room, and anything the last level cannot hold lands in a small special area at the end:

![Funnel insertion: the first key falls through full buckets until Level 3 has room. The second key finds every bucket full, fails in special area B, and lands in special area C, in the emptier of two buckets.](/images/building-opthash/funnel-insert.svg "Special area C uses the [power of two choices](https://www.eecs.harvard.edu/~michaelm/postscripts/handbook2001.pdf): checking two buckets instead of one keeps them far more evenly filled.")

The first version translated both constructions as literally as possible. It was easy to check against the paper, but it ran slowly.

## Low-level Optimizations

In Version 1, each of the paper's structures became one Rust struct. The code calls Elastic's arrays and Funnel's levels by a single name, _levels_. Each level owned a `Vec<Option<Entry>>`, and the code read like the paper's diagrams. However, every level was a separate allocation, and a probe had to chase a pointer to reach it. A probe that rejected a slot had also pulled the whole entry into cache, only to read its tag.

Version 1's problems set the pattern for the rest of the project. The paper counts how many probes an operation makes, but what mattered in practice was what each probe cost the processor. Processors load memory in 64-byte blocks called _cache lines_. A probe's cost came from which cache lines it pulled in and which branches it mispredicted. It also mattered whether the probe loaded a whole key only to find a different key in the slot.

The paper studies tables that have a fixed size and only take inserts, but a library needs more than that. The extra features could not be measured fairly while cache misses and pointer chasing still slowed the basic tables down. Speeding up the basic tables came first, and the features came afterwards.

For cheaper probes, the obvious model was [SwissTable](https://abseil.io/about/design/swisstables), the design behind Abseil's hash tables and `hashbrown`. Each slot gets a control byte holding a small hash fingerprint. One vector instruction compares a whole group of those bytes, and a single probe can rule out many slots.

Version 2 moved toward SwissTable. The levels stayed separate, but control metadata left the entries and got an array of its own. A lookup now read a one-byte control value before it loaded any key or value. For most empty or non-matching slots, the payload was never touched. Version 3 then packed every level into one allocation, and each level became a pointer and a count into the shared arena ([opthash#68](https://github.com/aaron-ang/opthash-rs/pull/68)). The levels kept their logical boundaries, and the control bytes sat next to each other in memory.

The diagram below shows the three layouts. Take the example key's check of $A_i[203]$. In Version 1, the check loads the whole entry in $A_i[203]$ to read its tag. In Version 2, the check reads the control byte for $A_i[203]$ first, and the entry stays untouched unless the fingerprint matches. In Version 3, the control byte for $A_i[203]$ sits in the shared control region at the start of the arena, next to the control bytes of every level.

```mermaid {caption="Yellow represents control data, gaps are separate allocations. In version 1, the control tag lives inside each slot, and checking it loads the whole entry. From version 2 on, each slot gets a control byte: 0 for empty, 0x80 for a deleted slot (a [tombstone](https://en.wikipedia.org/wiki/Lazy_deletion)), or a 7-bit hash fingerprint."}
block-beta
  columns 10
  t1["Version 1: one Vec per level: the Option tag sits inside every slot"]:10
  a0t["tag"] a0e["L0 entry"] a0t2["tag"] a0e2["entry ..."] space a1t["tag"] a1e["L1 ..."] space a2t["tag"] a2e["L2 ..."]
  t2["Version 2: one table per level: control bytes split out"]:10
  b0["L0 slots"]:3 b0c["L0 ctrl"]:1 space b1["L1 slots"]:1 b1c["ctrl"]:1 space b2["L2 slots"]:1 b2c["ctrl"]:1
  t3["Version 3: one arena: all control bytes first, then all slots"]:10
  c0["ctrl L0 L1 L2"]:3 c1["L0 slots"]:4 c2["L1"]:2 c3["L2"]:1
  classDef ctrl fill:#f4c95d,stroke:#8a6d1d,color:#000
  classDef title fill:none,stroke:none
  class a0t,a0t2,a1t,a2t,b0c,b1c,b2c,c0 ctrl
  class t1,t2,t3 title
```

Once control bytes existed, opthash took the rest of SwissTable's probe design: SIMD control-byte scans. It also switched to the [`foldhash`](https://github.com/orlp/foldhash) hasher. With a single arena, the control bytes stayed close together and allocation traffic dropped.

Level sizes also got rounded to powers of two. A hash could then map to a slot through a bit mask (`hash & (size - 1)`) instead of a much slower division (`hash % size`). This change broke the faithfulness rule from the introduction, although the break surfaced only later. With power-of-two level sizes, the example key visits a different set of slots instead of $A_i[17]$, $A_i[203]$, and $A_{i+1}[88]$.

## Performance Sensitivity

The maps got faster but also harder to measure. A layout change can shift unrelated data or code across a cache-line boundary. A struct field or hot loop that used to fit in one cache line might now straddle two, and the processor has to fetch both. A faster run does not say which change helped, and the same comparison run again can give a different answer.

A mini-hash experiment, which never made it into the code, showed how inconsistent those results could get. It stored 4 extra bytes of hash per slot, letting a lookup reject more slots before it compared keys. On the first run, three Funnel workloads in the Python bindings got 33 to 39 percent faster. A closer look showed that the two runs had landed on different classes of CPU core, and one core was clocked a gigahertz higher than the other.

With both runs pinned to the same core, a rerun erased most of the win. In the table, the union workload combines the map with a Python `dict` of keys not in the map, using `|` (`m | other`), and runion puts the dict on the left (`other | m`):

| Funnel workload (Python) |  First run | Pinned to one core |
| ------------------------ | ---------: | -----------------: |
| insert                   | 33% faster |         21% faster |
| union                    | 37% faster |         12% slower |
| runion                   | 39% faster |               flat |
| lookup hit               |          – |          4% slower |
| median across the suite  |          – |               flat |

The full workload table, Elastic included, is in [opthash#12](https://github.com/aaron-ang/opthash-rs/pull/12). A flat median was not worth 4 extra bytes per slot, and the idea was dropped. The bigger change was to stop trusting any result that could not be reproduced. The benchmark harness now:

- pins each run to one core and fixes memory placement
- saves named baselines to compare against
- reruns the unchanged `std` and `hashbrown` maps as controls for the noise floor
- checks assembly and hardware counters to tell fewer instructions apart from better cache or branch behavior

[CodSpeed](https://codspeed.io) runs the same suite in CI on every pull request and reports changes against main ([opthash#36](https://github.com/aaron-ang/opthash-rs/pull/36)). It covers both the Rust Criterion benchmarks and the Python pytest benchmarks. CodSpeed estimates run times on a simulated CPU, which makes results independent of the runner. The simulation is blind to clock speed and core type, though. It runs alongside the pinned local runs and never replaces them.

Smaller experiments got the same treatment: software prefetching, field reordering, and a 64-lane AVX-512 scan all showed no gain. A later check also caught a skew in the benchmark fixture itself. At 20,000 entries, Elastic and `hashbrown` sat at 70 percent full, and Funnel was completely full. Every map now fills to capacity before any measurement starts. The fixture grew to 28,672 entries, the most every map can hold ([opthash#139](https://github.com/aaron-ang/opthash-rs/pull/139)).

Better measurement caught wins that were just noise, plus wins that held on only one machine. The same numbers exposed a bigger problem. Some optimizations that benchmarked well had changed which slots the algorithms visited, notably power-of-two level sizes and changes to the probing arithmetic. The maps were no longer quite the paper's algorithms, and that was why they were faster.

## Recalibrating with the Paper

Lining the code up against the paper revealed more than one cause of drift. Several optimizations had each changed which slots a key tried, or the order of those tries. Funnel also had two bugs of its own ([opthash#47](https://github.com/aaron-ang/opthash-rs/pull/47), [opthash#98](https://github.com/aaron-ang/opthash-rs/pull/98)). Some inserts skipped the main levels and went straight to the special area. Special area C also did not always pick the emptier of its two buckets.

Existing tests passed with the bug that sent inserts straight to the special area. After its first resize, the table went back to the paper's placements, and that hid the bug. Because there was no way to be sure that every such problem had been found, fixing them one at a time would not be enough. Each map needed a version that followed the paper exactly, as a baseline for the optimized code.

A rewrite brought both maps in line with one fixed instantiation of the paper's constructions ([opthash#115](https://github.com/aaron-ang/opthash-rs/pull/115)). From then on, every optimization had to pass the faithfulness rule. An optimization could change how $A_i[17]$, $A_i[203]$, and $A_{i+1}[88]$ are checked for the example key, but it could never change which slots the key visits. Optimizations that broke the rule were removed:

| Removed: changed which slots get probed | Kept: changes only how slots are checked |
| --------------------------------------- | ---------------------------------------- |
| Power-of-two level sizes[^sizing]       | Single arena                             |
| Triangular Elastic probing              | Control bytes with fingerprints          |
| Altered probe budgets                   | SIMD control-byte scans                  |
| Reserve policy tuned for speed          |                                          |

To enforce the rule, opthash has reference implementations without SIMD or cached state, each one applying the paper's rules in the paper's order. Each reference is slow but easy to check against the paper line by line. Below is the Elastic reference's insertion rule for one batch, lightly trimmed. Its three cases match the three insert cases shown earlier:

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
    let budget = probe::elastic_dyadic_probe_budget(
        free_current,
        self.levels[current],
        reserve,
        C,
    )
    .expect("valid budget inputs");
    (0..budget)
        .find_map(|j| self.vacancy(current, key, j))
        .unwrap_or_else(|| self.uniform_vacancy(next, key))
}
```

The snippet is short, but three details in it explain why those optimizations specifically had to go:

1. **Placement is deterministic.** Each branch reads two occupancy counts and the key, and nothing else. The same inserts always produce the same placements. That repeatability lets the reference act as a test.
2. **Probes map to slots through exact sizes.** `vacancy(level, key, j)` checks the $j$-th slot in the key's probe sequence for one level. `uniform_vacancy` tries $j = 0, 1, 2, \ldots$ and stops at the first free one. Each $j$ maps to a slot using the level's true size. Round that size to a power of two and keys go to different slots.
3. **Thresholds use integer rounding.** opthash keeps $\delta$ a power of two, which turns $\delta |A_i| / 2$ into a bit shift (`floor_div_pow2`). An optimized table must round the same way. Otherwise, a key near a threshold ends up in a different case.

The reference decides where every probe lands, and that makes it a test oracle too. A test inserts the same keys into the reference and into the optimized table. It then checks that each key sits in the same slot, at the same probe position, in both tables:

```mermaid {caption="The equivalence check. If a single key lands somewhere else, the optimization changed the algorithm."}
flowchart LR
  in["same keys"] --> ref["Scalar reference"]
  in --> opt["Optimized table<br/>SIMD, cached state"]
  ref --> eq{"same slot and<br/>probe position?"}
  opt --> eq
  eq -- yes --> ok["same algorithm, maybe faster"]
  eq -- no --> bad["test fails: algorithm changed"]
```

Because opthash fixes one Funnel parameter set, the tests also record the exact order of slots each key visits. A different Funnel bucket width, level count, or shrink rate changes that order. The tests then fail, because they treat the change as a different algorithm.

## The Cost of Exactness

The rewrite made both maps much slower. The table shows nanoseconds per operation on a 20,000-entry map of `u64` keys, before (`v0.10.3`) and after the rewrite. Two interleaved runs on one pinned core agreed within 5 percent:

| Map         |            Insert |             Hit |            Miss |
| ----------- | ----------------: | --------------: | --------------: |
| Elastic     | 9.4 → 110 (11.7x) | 3.7 → 32 (8.6x) | 3.7 → 264 (70x) |
| Funnel      |   8.0 → 17 (2.1x) | 5.0 → 15 (3.0x) | 8.0 → 55 (6.9x) |
| `hashbrown` |               3.6 |             2.1 |             1.9 |

These numbers compare two versions of opthash and show how much slower exactness made this implementation. They do not test the paper's bounds, since those bounds count probes and this table counts nanoseconds. Because this older fixture leaves Funnel full and Elastic 70 percent full, the rows should not be compared with each other. The `hashbrown` control stayed flat on inserts and hits, but its misses took 0.8x as long in the newer build, which points to the two builds compiling the same baseline code differently.

The exact version stayed, slow as it was. The project started with one question: how do the paper's algorithms behave on real hardware? A fast table with a different probe sequence cannot answer it. After the rewrite, the reference had the final say on which optimizations stayed.

Keeping the exact version required understanding where its time went, which `perf` measured through the CPU's [hardware counters](https://www.brendangregg.com/perf.html). Each version ran successful Elastic lookups on the same pinned core for the same five seconds, old version first, and the totals look alike for that reason. A fair comparison needs the counters per lookup:

```text
$ perf stat -e cycles,instructions,cache-misses,branch-misses \
    speedup --profile-time 5 '^get_hit/get_hit_elastic$'

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

The slower exact version finished far fewer lookups. The regression table gives Elastic's successful lookup (Hit) as 3.7 ns before the rewrite and 32 ns after. Five seconds divided by each time gives the number of lookups each version completed:

$$
\begin{aligned}
\frac{5\ \text{s}}{3.7\ \text{ns}} &\approx 1.35 \text{ billion (v0.10.3)} \\
\frac{5\ \text{s}}{32\ \text{ns}} &\approx 156 \text{ million (exact)}
\end{aligned}
$$

Dividing each counter by those counts gives the cost of one lookup. These are estimates, since the benchmark loop adds a little work of its own, but the differences are far larger than that overhead. The Ratio column shows which costs grew:

| Per lookup             | v0.10.3 | Exact | Ratio          |
| ---------------------- | ------: | ----: | -------------- |
| Cycles                 |     ~15 |  ~125 | 8.4x           |
| Instructions           |     ~71 |  ~590 | 8.4x           |
| Instructions per cycle |    4.76 |  4.74 | about the same |
| Cache misses           |    ~1.0 |  ~1.1 | about the same |
| Branch misses          |   ~0.03 | ~0.35 | 13x            |

Cycles and instructions grew by the same factor, and instructions per cycle barely moved. The processor gets through the exact version's code at the same rate, but each lookup runs about 8 times as many instructions. Cache misses held near one per lookup, which shows that the exact hit path on this fixture is not waiting on memory.

The extra instructions are what following the paper costs. At every slot it visits, the exact version derives a fresh random number from the key's hash. [Exact range reduction](https://arxiv.org/abs/1805.10941) maps each number to a position in the array and keeps every position equally likely. The exact version also hops between arrays in the paper's interleaved order. The old version had power-of-two sizes, picked each position with one bit mask, and probed whole groups of slots at once.

Branch misses went up 13 times, to about 0.35 per lookup. Each miss costs roughly 10 to 20 cycles. Together the misses add about 4 to 7 cycles per lookup, a small slice of the 110 or so extra cycles.

Insert behaves differently, because the exact version also takes about 10 times as many cache misses per insert. An exact insert visits far more slots before it settles, and those slots sit in two arrays instead of one contiguous group.

The exact version still had room to improve, as the first round of tuning showed. Streamlining the hot paths made Elastic insertion more than 4x faster in CodSpeed's simulation while visiting the same slots ([opthash#121](https://github.com/aaron-ang/opthash-rs/pull/121)). Only the insert gain counts, since the controls were noisy in that run. The first round of tuning narrowed the project's question: how fast can the paper's constructions get on their own, with the library features kept out of the way?

## Keeping Extensions Separate

A real library has to delete, clear, and grow, and it has to cope when a key runs out of slots to try. opthash keeps that code from changing the paper's placement by splitting a table's lifecycle into epochs. Within an epoch, inserts and lookups follow the paper exactly.[^hashing] Growth, cleanup after deletes, and the rest run as separate library code between epochs. Each time that code runs, it opens a new epoch under the paper's rules.

```mermaid {caption="Every epoch follows the same paper rules for inserts and lookups. Each dashed arrow is library code that runs out of band and starts a fresh epoch: growth when an insert finds the table full, placement recovery when a key has no allowed slot left, and cleanup, clear, or resize after deletes or on request. Placement recovery is the only step that can put a key in a slot the paper would not choose."}
flowchart TB
  e1["Epoch N: paper rules"] -. "table full: grow" .-> e2["Epoch N+1: paper rules"]
  e2 -. "no allowed slot: placement recovery" .-> e3["Epoch N+2: paper rules"]
  e3 -. "cleanup, clear, or resize" .-> e4["Epoch N+3: paper rules"]
```

Placement recovery is the one exception. Under the paper, each key may use only a finite list of slots. With heavy churn, a real table can end up with every slot on that list taken. opthash does not drop the key in that case. It first rebuilds the table at the same size, reinserting every live key into a fresh layout with no tombstones. Then it tries the paper's placement again.

If the rebuild still leaves the key with no allowed slot, opthash puts it in the first free slot it finds, even one the paper would not allow. Doing so sets a flag, and later lookups check it and fall back to scanning the whole table.

Library code can shift where the paper's rules put keys without directly breaking any rule. Elastic's batch tracking had a bug like that. Elastic used to choose its active batch from a running count of inserts. With a steady mix of inserts and deletes, the number of live keys stayed flat, but the insert count kept climbing. The batch index crept forward, and new keys drifted into the smallest arrays, ran out of room quickly, and set off recovery.

The fix was to count live entries instead of total inserts ([opthash#135](https://github.com/aaron-ang/opthash-rs/pull/135)). Across 100,000 mixed inserts and deletes, the fix cut recovery rebuilds from 11 to zero.

The benchmarks split along the same line between the paper and the library. The paper only analyzes inserts and successful lookups for both maps, plus failed lookups for Funnel. Every other operation runs opthash's own library code, including Elastic's failed lookups, deletes, and growth. If both groups shared one score, a slow delete could hide a fast insert, and the reverse could happen too. opthash reports the two groups separately for that reason. Every run also includes `std::HashMap` and `hashbrown` as reference points. They show how fast mature, general-purpose tables are on the same machine and workload.[^python]

Keeping the paper's rules apart from the library code also paid off during optimization. Deletes, growth, and recovery can dominate a benchmark while saying nothing about the paper's bounds. Separate epochs and separate benchmark groups showed whether the slow part was the paper's algorithm or the library code, and that pointed to where optimization should start.

## Looking Back

Several lessons came from inaccurate numbers. The mini-hash experiment looked like a clear win right up until both runs were pinned to the same core. The skewed fixture compared maps at different loads, and no result looked off. A few habits add only minutes to each benchmark run: pinning the core, fixing memory placement, and rerunning an unchanged baseline like `hashbrown`. Together, these habits are the most reliable way I know to tell a real speedup from a lucky one.

Pinning made the numbers repeatable. It could not show whether the code under test was still the paper's algorithm. Power-of-two level sizes and triangular probing are good ideas for hash tables in general. Here they changed which slots a key visits, which changed the algorithm being measured, and that ruled them out. For the next paper, I would start by separating what the paper specifies from what it leaves to the implementation. Any change to the specified part would count as a different algorithm.

The faithfulness rule is easiest to enforce with a reference in place from day one. In opthash, the reference came only after the optimized code had drifted, and untangling the drift took a rewrite. A reference built first would have checked each optimization the moment it was written. Drift from the paper would have shown up as a failing test instead of in a later review of the code against the paper. Speed does not matter for the reference. It exists to be easy to check against the paper, and simple code does that job better than optimized code.

## What's Still Open

Later work kept the paper's probe sequences and made each probe cheaper. Most failed lookups now return without probing at all, thanks to a membership filter: a small [Bloom filter](https://en.wikipedia.org/wiki/Bloom_filter) at the end of the arena ([opthash#132](https://github.com/aaron-ang/opthash-rs/pull/132)). The filter can wrongly say "maybe" but never wrongly says "no". Probing itself also got cheaper ([opthash#135](https://github.com/aaron-ang/opthash-rs/pull/135)). Both maps have won back much of the speed the rewrite cost, but neither matches `hashbrown` yet.

Deletes are still slow when the table is nearly full. A delete-heavy workload pushes Elastic into placement recovery again and again. Every recovery rebuilds the whole table at the same size and reinserts every live key before the new key gets placed. Under steady churn the rebuilds keep coming, and each one takes time proportional to the number of live keys.

opthash is licensed under Apache 2.0 and available from [PyPI](https://pypi.org/project/opthash/) (`pip install opthash`) or [crates.io](https://crates.io/crates/opthash). The Rust crate also builds without `std`.

The question the project ended up with remains unanswered. Can the paper's probe bounds make opthash as fast as `hashbrown` in real use, without changing the algorithms again? If you want to help find out, the project is [open source](https://github.com/aaron-ang/opthash-rs) and contributions are welcome.

[^greedy]: Here $\delta$ is the fraction of the table left free. [Yao](https://doi.org/10.1145/3828.3836) proved that among greedy tables, uniform probing's average insertion cost of $\Theta(\log(1/\delta))$ probes is optimal, and conjectured that the worst-case insertion must cost about $1/\delta$. Funnel's worst case is $O(\log^2(1/\delta))$, and the paper proves that no greedy table can do better. Elastic's average cost is $O(1)$ and its worst case is $O(\log(1/\delta))$.

[^sizing]: Elastic still rounds its _total_ size up to a power of two, because the paper fixes how the space splits into arrays and leaves the overall size open ([opthash#137](https://github.com/aaron-ang/opthash-rs/pull/137)).

[^hashing]: The paper assumes truly random hash functions, while opthash uses fixed mixers built from [wyhash](https://github.com/wangyi-fudan/wyhash) and [SplitMix64](https://prng.di.unimi.it/splitmix64.c) constants. Exactness holds for those hash functions and does not prove that they inherit the paper's bounds.

[^python]: Python binding numbers also include Python hashing, interpreter overhead, and the [PyO3](https://pyo3.rs/) crossing. They measure a binding workload, not the table.
