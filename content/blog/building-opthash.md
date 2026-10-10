+++
title = "opthash: Bridging Theory with Hardware"
date = "2026-09-06"
slug = "building-opthash"
tags = ["Design", "Research", "Engineering"]
+++

## From Paper to Code

Earlier this year, I watched [Quanta's video on 2025's biggest breakthroughs in computer science](https://www.quantamagazine.org/videos/2025s-biggest-breakthroughs-in-computer-science/). The one that stuck with me was a paper that disproved a 40-year-old conjecture about open-addressed hash tables. This post follows my Rust implementation of that paper: the low-level tricks that made it fast, and why I removed several of them to keep the code faithful to the paper.

In [open addressing](https://en.wikipedia.org/wiki/Open_addressing), every key and value lives directly in one array. When a key hashes to a slot that is already taken, the table walks a fixed sequence of other slots, called the _probe sequence_, until it finds an empty one. As the table fills, a key steps past more occupied slots before it finds room, and the walk gets longer and slower.

The paper, [_Optimal Bounds for Open Addressing Without Reordering_](https://arxiv.org/abs/2501.02305), gives two constructions: Elastic Hashing and Funnel Hashing. Both keep insertions and successful lookups cheap even when the table is almost full, and neither shifts an entry once placed.

The main difference between them is whether they are _greedy_. Funnel (like most open-addressed tables) is greedy, meaning each key takes the first empty slot its probe sequence reaches. In 1985, Andrew Yao conjectured that uniform probing, the textbook greedy scheme, has close to the lowest worst-case insertion cost any greedy table can reach. Farach-Colton et al. disproved the conjecture by showing that funnel's worst case is much lower.[^greedy]

Yao also proved a separate result that does hold: in any greedy table, the average cost of an insertion must grow as the table fills. Elastic is _not_ greedy, which places it outside that proof, and its average insertion cost stays constant as the table fills. It gives each array a probe budget. Once the budget runs out, it stops looking in that array and moves on to the next one, even if the array still has empty slots.[^budget]

![Both walks hit the same crowded array, and the third slot they probe is empty. Uniform probing takes it. Elastic has used up its budget of two probes here, so it never tries that slot and moves on to the next, emptier array.](/images/building-opthash/probe-walk.svg "Skipping one free slot costs Elastic little, because the next array has far more room.")

Since I couldn't find an official implementation, I decided to write one. Building the algorithms seemed like a great way to understand them. I chose Rust because it offered control over memory layout and SIMD, and I wanted an opportunity to learn more of the language.

That became [opthash](https://github.com/aaron-ang/opthash-rs). It started as an [exploratory exercise](https://news.ycombinator.com/item?id=48365720). I was curious how these constructions would stack up against the tables almost all Rust code today relies on, such as [`std::HashMap`](https://doc.rust-lang.org/std/collections/struct.HashMap.html) and [`hashbrown`](https://github.com/rust-lang/hashbrown). To compete with them, opthash would have to make good use of caches, vector instructions, and allocators. It would also have to support what a general-purpose map does beyond the paper, such as deleting keys and growing.

Before getting to the code, let's look at how each construction places keys. The paper specifies which slots a key may use and the order in which it tries them, and the two constructions make those choices differently. The paper does not say how a slot should be checked; that is left to the code. For example, if a key's probe sequence is slots 17, 203, and 88, every faithful implementation must try those three slots in that order, but each can choose how to decide whether a slot is empty or holds the key.

### Elastic Hashing

Elastic splits the table into a series of arrays, each half the size of the one before, and starts by filling the largest to about 75 percent. Afterwards, insertions proceed in batches, each working on a pair of neighboring arrays: a larger $A_i$ and a smaller $A_{i+1}$. A batch tops up $A_i$ until only a small fraction of its slots are free and fills $A_{i+1}$ to 75 percent, setting up the next pair.

![Elastic fills its arrays in batches. Each array fills to 75 percent while it is the smaller of its pair, then to nearly full while it is the larger. After that, the next batch moves one array down.](/images/building-opthash/elastic-fill.svg "Blue is the filled part of each array.")

Within a batch, two checks pick one of three cases for each key:

![Three keys, one per case. Each checks whether Aᵢ is nearly full (Case 2: go to Aᵢ₊₁) and whether Aᵢ₊₁ is already 3/4 full (Case 3: stay in Aᵢ); otherwise it probes Aᵢ within a budget (Case 1).](/images/building-opthash/elastic-insert.svg "The budget grows as Aᵢ fills: keys late in a batch probe Aᵢ longer before falling back.")

Lookups don't use batches or cases, because a lookup can't tell which batch placed a key and may have to check every array. Instead, it walks one global probe sequence that visits the arrays in a fixed, interleaved order until it finds the key.

### Funnel Hashing

Funnel also splits the table into shrinking levels, but each main level is divided into buckets of the same fixed width. A later level is smaller only because it has fewer buckets. The paper derives the bucket width, level count, and shrink rate from the table's headroom.[^funnel-params] An insertion falls through the levels until it finds room, and a small special area at the end catches what the last level can't hold:

![Funnel insertion: the first key falls through full buckets until Level 3 has room. The second key finds every bucket full, fails in special area B, and lands in special area C, in the emptier of two buckets.](/images/building-opthash/funnel-insert.svg "Special area C uses the [power of two choices](https://www.eecs.harvard.edu/~michaelm/postscripts/handbook2001.pdf): checking two buckets instead of one keeps them far more evenly filled.")

My first version translated both constructions as literally as I could. It was easy to check against the paper, but it was slow.

## Low-level Optimizations

Version 1 mapped the paper's structures straight onto Rust structs. The code uses one name, _levels_, for both Elastic's arrays and Funnel's levels. In Version 1, each level owned a `Vec<Option<Entry>>`. The code mirrored the paper's diagrams. However, every level was its own allocation, every probe chased a pointer to reach it, and every slot the probe rejected had already pulled a full entry into cache just to read its tag.

Version 1's problems set the pattern for the rest of the project. What mattered was not how many probes an operation made, which is what the paper counts, but what each probe cost the processor. Processors load memory in 64-byte blocks called _cache lines_. That cost depended on which cache lines the probe pulled in, which branches it mispredicted, and whether it had to load a whole key just to find that the slot held a different key.

The paper studies fixed-size, insertion-only tables, but a library needs more features than that. Those features couldn't be measured fairly while the basic tables were still slow for hardware reasons like cache misses and pointer chasing. So I made the tables fast first and added features after.

[SwissTable](https://abseil.io/about/design/swisstables), the design behind Abseil's hash tables and `hashbrown`, was a natural model for making each probe cheaper. It stores a small hash fingerprint in each control byte and compares a whole group of them in one vector instruction, letting a single probe rule out many slots.

Version 2 took the first step toward that design. It kept the levels separate but moved control metadata out of the entries and into its own array. A lookup could then check a one-byte control value before loading any key or value, and most empty or non-matching slots never touched the payload. Version 3 put every level into one allocation, keeping the logical boundaries between levels while packing the control bytes contiguously in memory.[^arena]

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

With control bytes in place, opthash adopted the rest of SwissTable's probe design, seven-bit fingerprints and SIMD control-byte scans, and switched to the [`foldhash`](https://github.com/orlp/foldhash) hasher. Rounding sizes to powers of two let the code map a hash to a slot with a bit mask (`hash & (size - 1)`) instead of a much slower division (`hash % size`). The single arena kept all control bytes close together and cut allocation traffic.

## Performance Sensitivity

All of this made the maps faster but harder to measure. A layout change can move unrelated data or code across a cache-line boundary. A struct field or a hot loop that used to fit in one cache line may now span two, and the processor has to fetch both. Even when a run gets faster, it doesn't reveal which change helped, and re-running the same comparison can give a different answer.

A mini-hash experiment, which never made it into the code, showed how inconsistent those results could be.[^mini-hash] It stored 4 extra bytes of hash per slot so a lookup could reject more slots before comparing keys. The first run showed three Funnel workloads in the Python bindings improving by 33 to 39 percent. It looked like a clear win until I noticed the two runs had landed on different classes of CPU core, one clocked a gigahertz faster than the other. Rerun with both pinned to the same core, most of the win disappeared:

| Funnel workload (Python) |  First run | Pinned to one core |
| ------------------------ | ---------: | -----------------: |
| insert                   | 33% faster |         21% faster |
| union                    | 37% faster |         12% slower |
| runion                   | 39% faster |               flat |
| lookup hit               |          – |          4% slower |
| median across the suite  |          – |               flat |

I dropped the idea, since a flat median wasn't worth 4 extra bytes per slot. More importantly, I stopped trusting any result I couldn't reproduce. The benchmark harness now:

- pins each run to one core and fixes memory placement
- saves named baselines to compare against
- reruns the unchanged `std` and `hashbrown` maps as controls for the noise floor
- checks assembly and hardware counters to tell fewer instructions apart from better cache or branch behavior

In CI, [CodSpeed](https://codspeed.io) runs the same suite on every pull request and reports changes against main.[^codspeed] Since it estimates run time from a simulated CPU, results don't depend on the runner. Yet it can't see clock speed or core type, so it complements the pinned local runs rather than replacing them.

Smaller experiments went through the same checks.[^small-experiments] Later on, I found the benchmark fixture itself was skewed: at 20,000 entries, Elastic and `hashbrown` were 70 percent full while Funnel was completely full. Now, every map is filled to capacity before anything is measured.[^fixture]

Better measurement caught wins that were noise and wins that only held on one machine. The same measurements also exposed a bigger problem. Some optimizations that measured well had changed which slots the algorithms visited, notably power-of-two level sizes and changed probing arithmetic. The maps were faster because they were no longer quite the paper's algorithms.

## Recalibrating with the Paper

Lining the code up against the paper showed that the drift had more than one cause. Several optimizations had each changed which slots a key tried or the order it tried them in. Funnel also had bugs of its own: some inserts skipped the main levels and went straight to the special area, and special area C did not always pick the emptier of its two buckets.[^funnel-fixes] Fixing these one at a time wouldn't have been enough, because I couldn't be sure I had found them all. For that, I needed a version of each map that followed the paper exactly, to compare the optimized code against.

So I rewrote both maps to follow one fixed instantiation of the paper's constructions.[^exact] After that, each optimization had to pass one test. As in the earlier example of slots 17, 203, and 88, it could change how a slot is checked, but not which slots a key visits. Optimizations that failed the test were removed:

| Removed: changed which slots get probed | Kept: changes only how slots are checked |
| --------------------------------------- | ---------------------------------------- |
| Power-of-two level sizes[^sizing]       | Single arena                             |
| Triangular Elastic probing              | Control bytes with fingerprints          |
| Altered probe budgets                   | SIMD control-byte scans                  |
| Reserve policy tuned for speed          |                                          |

To enforce that test, I wrote reference implementations without SIMD or cached state, each one applying the paper's rules in the paper's order. It is slow but easy to check line by line against the paper. Here is the reference's Elastic insertion rule for one batch, lightly trimmed, with the same three cases as the diagram above:

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

The snippet is short, but three details in it explain why we pruned those optimizations specifically:

1. **Placement is deterministic.** Each branch depends only on two occupancy counts and the key itself. The same inserts always produce the same placements, allowing the reference to serve as a test.
2. **Probes map to slots through exact sizes.** `vacancy(level, key, j)` checks the $j$-th slot in the key's probe sequence for a level, and `uniform_vacancy` tries $j = 0, 1, 2, \ldots$ until one is free. The slot for each $j$ is computed from the level's true size, so rounding sizes to a power of two sends keys to different slots.
3. **Thresholds use integer rounding.** Since opthash keeps $\delta$ a power of two, $\delta |A_i| / 2$ is a bit shift (`floor_div_pow2`). An optimized table has to round the same way, otherwise a key near a threshold takes a different case.

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

Here is how much slower the rewrite made both maps on a 20,000-entry map of `u64` keys, in nanoseconds per operation, before (`v0.10.3`) and after:[^bench-setup]

| Map         |            Insert |             Hit |            Miss |
| ----------- | ----------------: | --------------: | --------------: |
| Elastic     | 9.4 → 110 (11.7x) | 3.7 → 32 (8.6x) | 3.7 → 264 (70x) |
| Funnel      |   8.0 → 17 (2.1x) | 5.0 → 15 (3.0x) | 8.0 → 55 (6.9x) |
| `hashbrown` |               3.6 |             2.1 |             1.9 |

These numbers compare two versions of opthash and show how much slower exactness made this implementation. They don't test the paper's bounds, which count probes rather than nanoseconds.[^controls]

I kept the slower, exact version anyway. A fast implementation of a different probe sequence couldn't answer the question that started the project: how the paper's algorithms behave on real hardware. From then on, the reference decided which optimizations were allowed.

Keeping it meant understanding where the time went. To find out, I read the CPU's [hardware counters](https://www.brendangregg.com/perf.html) with `perf`. Each version ran successful Elastic lookups for five seconds on the same pinned core, old version first:

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

The totals look alike because both runs lasted five seconds. But the exact version is slower and completed far fewer lookups in that time. To compare the two fairly, each counter has to be divided by the number of lookups. The regression table above lists Elastic's successful-lookup (Hit) time as 3.7 ns before the rewrite and 32 ns after. Dividing the five seconds by each time gives the number of lookups each version completed:

$$
\begin{aligned}
\frac{5\ \text{s}}{3.7\ \text{ns}} &\approx 1.35 \text{ billion (v0.10.3)} \\
\frac{5\ \text{s}}{32\ \text{ns}} &\approx 156 \text{ million (exact)}
\end{aligned}
$$

Dividing each counter by those counts gives the cost of a single lookup. These are estimates, since the benchmark loop adds a little work of its own, but the differences are far larger than that overhead:

| Per lookup             | v0.10.3 | Exact | Ratio          |
| ---------------------- | ------: | ----: | -------------- |
| Cycles                 |     ~15 |  ~125 | 8.4x           |
| Instructions           |     ~71 |  ~590 | 8.4x           |
| Instructions per cycle |    4.76 |  4.74 | about the same |
| Cache misses           |    ~1.0 |  ~1.1 | about the same |
| Branch misses          |   ~0.03 | ~0.35 | 13x            |

Cycles and instructions grew by the same factor, and instructions per cycle barely moved. The processor runs the exact version's code just as efficiently, but each lookup executes about 8 times as many instructions. Cache misses stayed near one per lookup, meaning the exact hit path on this fixture is not waiting on memory.

The extra instructions are the cost of following the paper. For every slot it visits, the exact version derives a fresh random number from the key's hash and maps it to a position in the array with [exact range reduction](https://arxiv.org/abs/1805.10941), which keeps every position equally likely. It also moves between arrays in the paper's interleaved order. The old version used power-of-two sizes, so a single bit mask picked each position, and it probed whole groups of slots at once.

Branch misses grew 13 times, to about 0.35 per lookup. At roughly 10 to 20 cycles each, they add only about 4 to 7 cycles, a small part of the 110 or so extra cycles.

Insert behaves differently, because there the exact version also takes about 10 times as many cache misses per operation. It visits far more slots before it settles, and those slots are spread across two arrays instead of one contiguous group.

The first round of tuning showed the exact version had room to improve. Streamlining the hot paths made Elastic insertion more than 4x faster without changing which slots it visits.[^hot-paths] That narrowed the project's question to how fast the paper's constructions can get on their own, with the library features kept out of the way.

## Keeping Extensions Separate

A real library must also delete, clear, grow, and handle a key running out of slots to try. To keep that code from changing how the paper places keys, opthash splits a table's lifecycle into epochs. Inside an epoch, inserts and lookups follow the paper exactly.[^hashing] Everything else, from growth to cleanup after deletes, runs as separate library code between epochs, and each run starts a new epoch under the paper's rules.

```mermaid {caption="Every epoch follows the same paper rules for inserts and lookups. Each dashed arrow is library code that runs out of band and starts a fresh epoch: growth when an insert finds the table full, placement recovery when a key has no allowed slot left, and cleanup, clear, or resize after deletes or on request. Placement recovery is the only step that can put a key in a slot the paper would not choose."}
flowchart TB
  e1["Epoch N: paper rules"] -. "table full: grow" .-> e2["Epoch N+1: paper rules"]
  e2 -. "no allowed slot: placement recovery" .-> e3["Epoch N+2: paper rules"]
  e3 -. "cleanup, clear, or resize" .-> e4["Epoch N+3: paper rules"]
```

Placement recovery is the one exception. The paper gives each key a finite list of slots it may use, and under heavy churn a real table can end up with every one of them taken. When that happens, opthash does not drop the key. It first rebuilds the table at the same size, reinserting every live key into a fresh layout that also has no tombstones, and then tries the paper's placement again.

If the key still has no allowed slot, opthash puts it in the first free slot it finds, even though the paper would not allow that slot. That placement sets a flag so later lookups know to fall back to scanning the whole table.

Library code could also change where the paper's rules put keys, without breaking any rule directly. One example was a bug in how Elastic tracked its batches. Elastic used to pick its active batch from a running count of inserts. Under a steady mix of inserts and deletes, that count kept climbing even though the number of live keys stayed flat. The batch index crept forward and new keys drifted into the smallest arrays, where they quickly ran out of room and set off recovery. Counting live entries instead of total inserts fixed it.[^churn]

Benchmarks follow the same split between the paper and the library. The paper analyzes only some operations: inserts and successful lookups for both maps, and failed lookups for Funnel. Everything else, including failed lookups for Elastic, deletes, and growth, runs opthash's own library code. In one combined score, a slow delete could hide a fast insert, or vice versa. opthash therefore reports the two groups separately. Each run also includes `std::HashMap` and `hashbrown` as reference points, showing how fast mature, general-purpose tables are on the same machine and workload.[^python]

## Looking Back

Several lessons came from numbers that turned out to be inaccurate. The mini-hash experiment looked like a clear win until both runs were pinned to the same core. The skewed fixture compared maps at different loads without any result looking off. Pinning the core, fixing memory placement, and rerunning an unchanged baseline like `hashbrown` add only a few minutes to each benchmark run. Together, they are the most reliable way I know to tell a real speedup from a lucky one.

Pinning made the measurements repeatable, but it couldn't verify whether the code being measured was still the paper's algorithm. When the goal is to study an algorithm, a speedup counts only if the algorithm stays the same. Power-of-two level sizes and triangular probing are good ideas for hash tables in general. They were not suitable here because they changed which slots a key visits, and therefore which algorithm was being measured. When implementing another paper, I would first separate what the paper specifies from what it leaves to the implementation. Any change to the specified part would count as a different algorithm.

That rule is easiest to enforce with a reference in place from the beginning. I wrote mine only after the optimized code had drifted, and untangling the drift took a rewrite. Had I built it first, it would have checked each optimization as soon as it was written. Any drift from the paper would have appeared as a failing test, rather than being found later by reviewing the code against the paper. The reference does not need to be fast. Its purpose is to be easy to check against the paper, and simple code serves that purpose better than optimized code.

Separating the paper's rules from the rest of the library helped too, because deletes, growth, and recovery can dominate a benchmark while saying nothing about the paper's bounds. Keeping them in separate epochs and benchmark groups showed whether the paper's algorithm or the library code was slow. That told me where to start optimizing.

## What's Still Open

Later work kept the paper's probe sequences and made each probe cheaper. A membership filter, a small [Bloom filter](https://en.wikipedia.org/wiki/Bloom_filter) at the end of the arena, lets most failed lookups return without probing,[^filter] and probing itself got cheaper.[^probing] Both maps have recovered much of the speed the rewrite cost, but neither matches `hashbrown` yet.

Deletes on a nearly full table are still slow. A delete-heavy workload keeps pushing Elastic into placement recovery. Each recovery rebuilds the whole table at the same size, reinserting every live key, before it can place the new one. Under steady churn these rebuilds keep recurring, and each one takes time proportional to the number of live keys.

opthash is licensed under Apache 2.0 and available on [PyPI](https://pypi.org/project/opthash/) (`pip install opthash`) and [crates.io](https://crates.io/crates/opthash) (the Rust crate also builds without `std`).

The question that started the project, whether the paper's probe bounds can make opthash as fast as `hashbrown` in real use without changing the algorithms again, is still open. If you would like to help answer it, the project is [open source](https://github.com/aaron-ang/opthash-rs) and welcomes contributions.

[^greedy]: Here $\delta$ is the fraction of the table left free. [Yao](https://doi.org/10.1145/3828.3836) proved that among greedy tables, uniform probing's average insertion cost of $\Theta(\log(1/\delta))$ probes is optimal, and conjectured that the worst-case insertion must cost about $1/\delta$. Funnel's worst case is $O(\log^2(1/\delta))$, and the paper proves that no greedy table can do better. Elastic's average cost is $O(1)$ and its worst case is $O(\log(1/\delta))$.

[^budget]: With [uniform probing](https://doi.org/10.1145/3828.3836), the expected walk grows with $1/\varepsilon$, where $\varepsilon$ is the free fraction of the array. Elastic's budget is $c \cdot \min(\log^2(1/\varepsilon), \log(1/\delta))$, where $\delta$ is the free fraction of the whole table. That $\log(1/\delta)$ cap is what keeps Elastic's worst case at $O(\log(1/\delta))$, below the $\Omega(\log^2(1/\delta))$ lower bound for greedy tables.

[^funnel-params]: The paper's formulas allow more than one valid set of values. opthash fixes one set. Its tests record the exact order in which each key visits slots, and a different bucket width, level count, or shrink rate would change that order and fail the tests, which treat it as a different algorithm.

[^arena]: See [opthash#68](https://github.com/aaron-ang/opthash-rs/pull/68). Each level became a pointer and a count into the shared arena.

[^mini-hash]: See [opthash#12](https://github.com/aaron-ang/opthash-rs/pull/12) for the full workload table, Elastic included.

[^small-experiments]: Software prefetching did nothing or hurt, reordering fields sometimes produced worse machine code, and a 64-lane AVX-512 path was dropped for the existing 16-lane x86 scan once the generated code showed no gain.

[^codspeed]: See [opthash#36](https://github.com/aaron-ang/opthash-rs/pull/36). Both the Rust Criterion benchmarks and the Python pytest benchmarks run this way. CodSpeed's simulation later replaced a separate iai-callgrind instruction-count benchmark.

[^fixture]: See [opthash#139](https://github.com/aaron-ang/opthash-rs/pull/139). The fixture grew from 20,000 to 28,672 entries, the most every map can hold.

[^funnel-fixes]: See [opthash#47](https://github.com/aaron-ang/opthash-rs/pull/47) and [opthash#98](https://github.com/aaron-ang/opthash-rs/pull/98). The first bug stayed hidden because the table returned to the paper's placements after its first resize, and existing tests passed.

[^exact]: See [opthash#115](https://github.com/aaron-ang/opthash-rs/pull/115). The paper leaves Elastic's probe-budget constant free and treats its Funnel constants as sufficient rather than canonical. So "exact" here means exact to the instantiation opthash pins.

[^sizing]: Elastic still rounds its _total_ size up to a power of two. This doesn't break exactness: the paper fixes how the space is split into arrays, not the overall table size. I tried exact total sizing anyway. It saved about 45 percent of memory at a million entries, but lookups and deletes got much slower, so I kept the power-of-two total. See [opthash#137](https://github.com/aaron-ang/opthash-rs/pull/137), which also lists smaller layout ideas that were tried and dropped.

[^bench-setup]: The regression was first recorded in the [design notes](https://github.com/aaron-ang/opthash-rs/blob/e2aab0104d5afad0749a9e5fb949cb43f68aba6c/docs/superpowers/specs/2026-07-11-paper-faithful-performance-parity-design.md#context). A fresh run for this article reproduced it: same fixtures, 100,000 operations per sample, interleaved twice on one pinned Cortex-X925 core at 3.9 GHz. At 20,000 entries, Elastic and `hashbrown` are 70 percent full and Funnel is full. Compare each row before and after, not maps against each other.

[^controls]: Each before-and-after comparison was run twice, and the two runs agreed within 5 percent on every operation. The `std` and `hashbrown` baselines were flat on insert and successful lookups. On failed lookups, they ran at different speeds in the two builds, `std` taking 1.5x as long after the rewrite and `hashbrown` 0.8x as long, by the same amount in both runs. That points to the two benchmark builds compiling the same baseline code differently, not to run-to-run noise.

[^hot-paths]: See [opthash#121](https://github.com/aaron-ang/opthash-rs/pull/121). In CodSpeed's simulation, Elastic insert went from 111 ms to 24 ms. Only the insert gain counts, because the controls were noisy in that run.

[^filter]: See [opthash#132](https://github.com/aaron-ang/opthash-rs/pull/132). Each key sets two bits in a 64-bit word shared by ten slots. Because bits are never cleared one key at a time, the filter can wrongly say "maybe" but never wrongly say "no".

[^probing]: See [opthash#135](https://github.com/aaron-ang/opthash-rs/pull/135).

[^hashing]: The paper's analysis assumes truly random hash functions. opthash uses fixed mixers instead, built from [wyhash](https://github.com/wangyi-fudan/wyhash)'s default secrets and [SplitMix64](https://prng.di.unimi.it/splitmix64.c)'s constants. So "follow the paper exactly" holds for opthash's own hash functions, but it isn't a proof that fixed mixers inherit the paper's bounds.

[^churn]: See [opthash#135](https://github.com/aaron-ang/opthash-rs/pull/135). Over 100,000 mixed inserts and deletes, recovery rebuilds went from 11 to zero.

[^python]: The Python bindings need the same care. Python hashing, interpreter overhead, the [GIL](https://docs.python.org/3/glossary.html#term-global-interpreter-lock), and the [PyO3](https://pyo3.rs/) crossing can cost more than the Rust operation underneath. Those numbers measure a binding workload rather than the table.
