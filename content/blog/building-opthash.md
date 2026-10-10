+++
title = "opthash: Bridging Theory with Hardware"
date = "2026-09-06"
slug = "building-opthash"
tags = ["Design", "Research", "Engineering"]
+++

Earlier this year, a [Quanta video on 2025’s biggest breakthroughs in computer science](https://www.quantamagazine.org/videos/2025s-biggest-breakthroughs-in-computer-science/) showcased a result about hash tables that motivated me to implement it. The paper, [_Optimal Bounds for Open Addressing Without Reordering_](https://arxiv.org/abs/2501.02305), presents Elastic Hashing and Funnel Hashing: two ways to keep insertion and successful lookup cheap even when a table is nearly full, without moving entries after placing them. It also resolves a long-standing conjecture that had stood for about 40 years.

In [open addressing](https://en.wikipedia.org/wiki/Open_addressing), keys and values live in the table’s array rather than in separately allocated chains. When a key hashes to an occupied slot, the table follows a _probe sequence_ to find another. As the table fills, finding an empty slot generally takes more probes. The paper’s bounds made me curious how Elastic and Funnel would perform against [`std::HashMap`](https://doc.rust-lang.org/std/collections/struct.HashMap.html) and [`hashbrown`](https://github.com/rust-lang/hashbrown) on real hardware, where cache behavior, branch prediction, and the instructions needed for each probe affect the timings.

I could not find an official implementation, so that became [opthash](https://github.com/aaron-ang/opthash-rs), an [exploratory exercise](https://news.ycombinator.com/item?id=48365720) in Rust. Implementing the constructions gave me a way to understand them beyond the proofs, while Rust offered control over memory layout and SIMD and a reason to learn more of the language.

For a while, I borrowed optimization ideas from `hashbrown` without checking how they affected the underlying algorithms. After several rounds of changes, an audit against the paper found that some of those optimizations had changed which slots a key visited. Parts of the implementation needed a rewrite before the benchmarks could answer the original question. The audit clarified which optimizations fit the project’s goal. We could make each probe cheaper, but had to preserve which slots it visited and in what order to keep benchmarking the paper’s algorithm.

## Two Ways to Fill a Nearly Full Table

### Elastic Hashing

A _greedy_ table takes the first empty slot its probe sequence reaches. Andrew Yao proved that the average insertion cost for such a table has to increase as it fills.[^greedy] Elastic avoids that restriction by limiting how long an insertion searches one array before trying another, even when the first array still has empty slots.

The table is split into arrays, each half the size of the previous one, and an insertion receives a budget for probing an array. If those probes find no room, it continues in the next array rather than spending longer in the crowded one. This non-greedy placement is what allows Elastic to keep its average insertion cost constant. The comparison with uniform probing illustrates the difference:

![Both walks hit the same crowded array, and the third slot they probe is empty. Uniform probing takes it. Elastic has used up its budget of two probes here, so it never tries that slot and moves on to the next, emptier array.](/images/building-opthash/probe-walk.svg "Skipping one free slot costs Elastic little, because the next array has far more room.")

The largest array fills to about 75 percent first. Subsequent batches work on a neighboring pair: the larger $A_i$ and the smaller $A_{i+1}$. A batch brings $A_i$ close to full and $A_{i+1}$ to 75 percent, ready for the next pair:

![Elastic fills its arrays in batches. Each array fills to 75 percent while it is the smaller of its pair, then to nearly full while it is the larger. After that, the next batch moves one array down.](/images/building-opthash/elastic-fill.svg "Blue is the filled part of each array.")

Within each batch, the free space in $A_i$ and $A_{i+1}$ determines how an insertion proceeds. Those two occupancy checks select one of three cases; Case 1 is the one that limits the probes in $A_i$ before falling back to $A_{i+1}$:

![Three keys, one per case. Each checks whether Aᵢ is nearly full (Case 2: go to Aᵢ₊₁) and whether Aᵢ₊₁ is already 3/4 full (Case 3: stay in Aᵢ); otherwise it probes Aᵢ within a budget (Case 1).](/images/building-opthash/elastic-insert.svg "The budget grows as Aᵢ fills: keys late in a batch probe Aᵢ longer before falling back.")

For a Case 1 key, suppose the probe sequence starts $A_i[17]$, $A_i[203]$, $A_{i+1}[88]$. These are hash-derived positions scattered across the arrays, not neighboring slots. After its budget of two probes, Elastic leaves $A_i$ despite its remaining free slots and inserts at $A_{i+1}[88]$, the first free slot it finds there. Slot numbers here and below are positions within an array, not memory offsets.

A lookup cannot simply retrace those three insertion probes, because it does not know which batch placed the key. Instead, it uses a fixed global sequence that interleaves probes across the arrays and may visit every array before finding the key.

### Funnel Hashing

Funnel takes a different approach: it remains greedy. In 1985, Yao conjectured that uniform probing was close to optimal for worst-case insertion among greedy tables. Farach-Colton and his coauthors disproved that conjecture with Funnel, whose worst-case bound is substantially lower.[^greedy]

Funnel also has shrinking levels. All main levels use buckets of one fixed width, with fewer buckets in the later, smaller levels. Bucket width, level count, and shrink rate depend on the table's headroom. The paper allows several parameter sets, and opthash chooses one. An insert tries successive levels until it finds room, with a small special area for keys the last level cannot hold:

![Funnel insertion: the first key falls through full buckets until Level 3 has room. The second key finds every bucket full, fails in special area B, and lands in special area C, in the emptier of two buckets.](/images/building-opthash/funnel-insert.svg "Special area C uses the [power of two choices](https://www.eecs.harvard.edu/~michaelm/postscripts/handbook2001.pdf): checking two buckets instead of one keeps them far more evenly filled.")

The first implementation translated these placement rules as literally as possible. That made it straightforward to compare with the paper, but the timings showed how much work remained between a bound on probes and a fast table.

## Making Each Probe Cheaper

Version 1 represented each region with a `Vec<Option<Entry>>`. The code calls both Elastic’s arrays and Funnel’s levels _levels_, and each level owned a separate allocation. To check a slot, the table followed the level’s pointer and read the `Option` tag stored with the entry. Even when that tag was enough to reject a slot, the lookup had already accessed the memory containing the entry.

The paper counts each probe as one step, but the processor has to execute the instructions and fetch the memory behind that step. A probe may fetch a 64-byte _cache line_, load a key that does not match, or take a mispredicted branch. Before adding deletion and growth, I worked on those costs with fixed-size, insert-only tables, so library operations would not obscure the effect of a layout change.

[SwissTable](https://abseil.io/about/design/swisstables), the design behind Abseil’s hash tables and `hashbrown`, offered a way to reject most lookup candidates before reading their keys. It stores a small hash fingerprint in a control byte associated with each slot and compares a group of those bytes with vector instructions. Only slots with matching fingerprints need a full key comparison. This made the metadata layout a natural place to start.

Version 2 moved the control bytes out of the entries while retaining a separate allocation for each level. A lookup could then inspect the metadata without first accessing the associated key and value. Version 3 brought all the levels into one arena ([opthash#68](https://github.com/aaron-ang/opthash-rs/pull/68)), with pointers and counts retaining their logical boundaries and the control bytes placed together in the allocation.

For the earlier probe at $A_i[203]$, these changes affect where the lookup finds the information it needs. Version 1 reads the tag alongside the entry; Version 2 checks a separate control byte before deciding whether to read the key; and Version 3 places that byte with the other levels’ metadata in the shared arena:

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

With separate control bytes available, opthash added SIMD scans to check them in groups and switched to the [`foldhash`](https://github.com/orlp/foldhash) hasher. Alongside the arena layout, these changes reduced metadata-checking and allocation costs. I had not yet checked all of these changes against the paper’s placement rules.

One such change rounded level sizes to powers of two, allowing a bit mask (`hash & (size - 1)`) to replace the division in `hash % size`. The arithmetic became cheaper, but changing a level’s size also changed where the same hash placed a key. The later audit would require removing that optimization: preserving the array boundaries was part of preserving the construction.

## Checking Whether a Speedup Holds

As the layout changed, some optimizations helped in one comparison and hurt in the next. Moving a field or changing the alignment of a hot loop could alter the generated code or the cache lines it touched. A lower timing therefore needed more investigation before I could attribute it to the intended optimization.

The clearest example was a mini-hash experiment that stored four extra hash bytes per slot to reject more candidates before comparing keys. Three Funnel workloads in the Python bindings initially improved by 33 to 39 percent. Before accepting the extra memory cost, I checked the comparison and found that the runs had used different classes of CPU core, one clocked a gigahertz higher than the other.

With both runs pinned to the same core, a rerun erased most of the win. In the table, the union workload combines the map with a Python `dict` of keys not in the map, using `|` (`m | other`), and runion puts the dict on the left (`other | m`):

| Funnel workload (Python) |  First run | Pinned to one core |
| ------------------------ | ---------: | -----------------: |
| insert                   | 33% faster |         21% faster |
| union                    | 37% faster |         12% slower |
| runion                   | 39% faster |               flat |
| lookup hit               |          – |          4% slower |
| median across the suite  |          – |               flat |

The full workload table, including Elastic, is in [opthash#12](https://github.com/aaron-ang/opthash-rs/pull/12). Once both versions ran on the same core, the suite’s median was flat, which did not justify four more bytes per slot. The experiment never merged. It also changed how I evaluated subsequent optimizations; the local harness now:

- pins each run to one core and fixes memory placement
- saves named baselines to compare against
- reruns the unchanged `std` and `hashbrown` maps as controls for the noise floor
- checks assembly and hardware counters to tell fewer instructions apart from better cache or branch behavior

[CodSpeed](https://codspeed.io) runs the same suite in CI on every pull request and reports changes against main ([opthash#36](https://github.com/aaron-ang/opthash-rs/pull/36)). It covers both the Rust Criterion benchmarks and the Python pytest benchmarks. CodSpeed estimates run times on a simulated CPU, which makes results independent of the runner. The simulation is blind to clock speed and core type, though. It runs alongside the pinned local runs and never replaces them.

Software prefetching, field reordering, and a 64-lane AVX-512 scan also failed to show a gain. Later, checking the fixture exposed a separate problem: inserting 20,000 entries left Elastic and `hashbrown` 70 percent full but filled Funnel completely. The timings were therefore comparing different loads. The fixture now uses 28,672 entries, the most all the maps can hold, and fills each map to capacity before measurement ([opthash#139](https://github.com/aaron-ang/opthash-rs/pull/139)).

These checks address the conditions under which the maps are measured. A repeatable speedup can still belong to a different algorithm, however. To answer the project’s original question, I also needed to compare the implementation’s placement decisions with the paper’s.

## Auditing Placement Against the Paper

When I eventually audited the implementation, several optimizations had changed the probe slots or their order. Funnel had two additional bugs ([opthash#47](https://github.com/aaron-ang/opthash-rs/pull/47), [opthash#98](https://github.com/aaron-ang/opthash-rs/pull/98)): some inserts bypassed the main levels and went straight to the special area, and special area C did not always choose the emptier of its two buckets. Both departed from the paper’s placement rules.

The existing placement tests ran after the first resize. Because resizing restored the paper’s layout, those tests missed the earlier bypass. Fixing that bug was not enough to establish that the rest of the optimized code followed the construction. A simple reference implementation would give us a way to compare placements as they happened, before a resize could hide a difference.

The rewrite established one fixed choice of parameters for each construction ([opthash#115](https://github.com/aaron-ang/opthash-rs/pull/115)). An optimized implementation had to preserve that choice’s slots and probe order, while remaining free to change how it checked them. This ruled out changes to level sizes and probing arithmetic, but retained the layout and metadata techniques that could operate within those rules:

| Removed: changed which slots get probed | Kept: changes only how slots are checked |
| --------------------------------------- | ---------------------------------------- |
| Power-of-two level sizes[^sizing]       | Single arena                             |
| Triangular Elastic probing              | Control bytes with fingerprints          |
| Altered probe budgets                   | SIMD control-byte scans                  |
| Reserve policy tuned for speed          |                                          |

### A Reference to Compare Against

The references apply the paper’s rules directly, without SIMD or cached state, so we can check their decisions against the construction without first untangling the optimized code. The Elastic insertion rule below is lightly trimmed. Its three branches correspond to the cases in the earlier insertion diagram:

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

Three details explain why the reference can enforce the placement rules and why some optimizations had to be removed:

1. **Placement is deterministic.** Each branch reads two occupancy counts and the key, and nothing else. The same inserts always produce the same placements. That repeatability lets the reference act as a test.
2. **Probes map to slots through exact sizes.** `vacancy(level, key, j)` checks the $j$-th slot in the key's probe sequence for one level. `uniform_vacancy` tries $j = 0, 1, 2, \ldots$ and stops at the first free one. Each $j$ maps to a slot using the level's true size. Round that size to a power of two and keys go to different slots.
3. **Thresholds use integer rounding.** opthash keeps $\delta$ a power of two, which turns $\delta |A_i| / 2$ into a bit shift (`floor_div_pow2`). An optimized table must round the same way. Otherwise, a key near a threshold ends up in a different case.

We can now insert the same keys into the reference and optimized table, then compare each key's slot and probe position:

```mermaid {caption="The equivalence check. If a single key lands somewhere else, the optimization changed the algorithm."}
flowchart LR
  in["same keys"] --> ref["Scalar reference"]
  in --> opt["Optimized table<br/>SIMD, cached state"]
  ref --> eq{"same slot and<br/>probe position?"}
  opt --> eq
  eq -- yes --> ok["same algorithm, maybe faster"]
  eq -- no --> bad["test fails: algorithm changed"]
```

For Funnel, the tests record the exact probe order for the chosen bucket width, level count, and shrink rate. Although the paper permits other parameter choices, the tests treat those as different instantiations to evaluate, rather than implementation-only speedups.

## The Cost of Preserving Placement

The rewrite made both maps much slower. The table shows nanoseconds per operation on a 20,000-entry map of `u64` keys, before (`v0.10.3`) and after the rewrite. Two interleaved runs on one pinned core agreed within 5 percent:

| Map         |            Insert |             Hit |            Miss |
| ----------- | ----------------: | --------------: | --------------: |
| Elastic     | 9.4 → 110 (11.7x) | 3.7 → 32 (8.6x) | 3.7 → 264 (70x) |
| Funnel      |   8.0 → 17 (2.1x) | 5.0 → 15 (3.0x) | 8.0 → 55 (6.9x) |
| `hashbrown` |               3.6 |             2.1 |             1.9 |

These numbers compare two versions of opthash and show how much slower exactness made this implementation. They do not test the paper's bounds, since those bounds count probes and this table counts nanoseconds. Because this older fixture leaves Funnel full and Elastic 70 percent full, the rows should not be compared with each other. The `hashbrown` control stayed flat on inserts and hits, but its misses took 0.8x as long in the newer build, which points to the two builds compiling the same baseline code differently.

I kept the slower version because the project was meant to investigate the paper’s constructions on hardware. The faster version showed what the shortcuts had bought, but could not benchmark the chosen construction. The references would check subsequent tuning against the placement rules.

### Explaining the Lookup Regression

To investigate the regression, I measured successful Elastic lookups with `perf` and the CPU’s [hardware counters](https://www.brendangregg.com/perf.html). Each version ran for five seconds on the same pinned core, with the old version first. Since the duration was fixed, the slower version completed fewer lookups. The raw counter totals below therefore need to be converted into counts per lookup before they can explain the difference:

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

Using the successful-lookup timings from the regression table, 3.7 ns and 32 ns, we can estimate how many lookups each version completed in five seconds:

$$
\begin{aligned}
\frac{5\ \text{s}}{3.7\ \text{ns}} &\approx 1.35 \text{ billion (v0.10.3)} \\
\frac{5\ \text{s}}{32\ \text{ns}} &\approx 156 \text{ million (exact)}
\end{aligned}
$$

Dividing the counters by those estimated lookup counts gives the comparison below. The benchmark loop contributes some work of its own, so these are approximate costs per lookup rather than isolated measurements of the table’s instructions:

| Per lookup             | v0.10.3 | Exact | Ratio          |
| ---------------------- | ------: | ----: | -------------- |
| Cycles                 |     ~15 |  ~125 | 8.4x           |
| Instructions           |     ~71 |  ~590 | 8.4x           |
| Instructions per cycle |    4.76 |  4.74 | about the same |
| Cache misses           |    ~1.0 |  ~1.1 | about the same |
| Branch misses          |   ~0.03 | ~0.35 | 13x            |

For this hit fixture, the instruction count grew by about the same factor as the cycle count: roughly 8 times, with almost unchanged instructions per cycle. Cache misses stayed near one per lookup. That points to the extra instruction work as the main cost of the rewrite in this comparison, rather than additional waits for memory.

Preserving the probe sequence requires additional work at each candidate. The exact version derives a fresh random number from the key’s hash, maps it uniformly into the array with [exact range reduction](https://arxiv.org/abs/1805.10941), and follows the interleaved order across arrays. The old version used power-of-two sizes to select positions with a bit mask and checked groups of slots at once. Removing those shortcuts restored the placement rules while adding instructions to each lookup.

Branch misses rose by a larger factor, 13 times, but their absolute cost remained relatively small. At about 0.35 misses per lookup and an estimated 10 to 20 cycles per miss, they account for roughly 4 to 7 of the additional 110 or so cycles. That leaves the extra instruction work as the more substantial difference in this lookup comparison.

Insertion showed a different cost profile, with about ten times as many cache misses per operation. It visits more slots before finding room and spreads those visits across two arrays, whereas the old implementation checked a contiguous group. Optimizing the lookup and insertion paths would therefore require attention to different costs.

## Optimizing Within the Placement Rules

The rewrite left room to recover performance without changing the algorithms again. Streamlining the exact hot paths made Elastic insertion more than 4× faster in CodSpeed’s simulation while preserving the visited slots ([opthash#121](https://github.com/aaron-ang/opthash-rs/pull/121)). The controls were noisy in that run, so only the insertion improvement counts.

Later work reduced unnecessary probing with a small [Bloom filter](https://en.wikipedia.org/wiki/Bloom_filter) at the end of the arena ([opthash#132](https://github.com/aaron-ang/opthash-rs/pull/132)). Most failed lookups can return after the filter reports that the key is absent. A “maybe” result still requires the normal lookup; the filter can produce false positives, but not false negatives. This avoids work before the probe sequence begins without changing the sequence used to search for a possible match.

The probing code itself also became cheaper ([opthash#135](https://github.com/aaron-ang/opthash-rs/pull/135)). Both maps have recovered much of the speed lost in the rewrite, although neither matches `hashbrown` yet. The reference checks let us pursue improvements while verifying that placements stay the same.

## Extending the Tables Into a Library

Getting the construction to run faster was only part of making opthash usable as a library. A map also needs deletion, clearing, and growth, which the paper does not specify. It needs a response when a key exhausts its allowed slots even though space remains elsewhere. After the audit, I wanted those extensions to have explicit boundaries, so a benchmark could distinguish the paper’s placement algorithm from the surrounding library behavior.

The table’s lifecycle is divided into _epochs_. Normal insertion and lookup follow the chosen construction within an epoch,[^hashing] while growth, cleanup after deletion, and other library operations run between epochs. Placement recovery is the exception that can require lookups outside the paper’s probe path.

```mermaid {caption="Every epoch follows the same paper rules for inserts and lookups. Each dashed arrow is library code that runs out of band and starts a fresh epoch: growth when an insert finds the table full, placement recovery when a key has no allowed slot left, and cleanup, clear, or resize after deletes or on request. Placement recovery is the only step that can put a key in a slot the paper would not choose."}
flowchart TB
  e1["Epoch N: paper rules"] -. "table full: grow" .-> e2["Epoch N+1: paper rules"]
  e2 -. "no allowed slot: placement recovery" .-> e3["Epoch N+2: paper rules"]
  e3 -. "cleanup, clear, or resize" .-> e4["Epoch N+3: paper rules"]
```

### Recovering When a Key Has No Allowed Slot

A key has a finite list of allowed slots, all of which may be occupied under heavy churn even while the table has room elsewhere. opthash first rebuilds at the same size, reinserting the live keys into a layout without tombstones, then retries the paper’s placement rule.

If rebuilding still leaves no allowed slot, the library places the key in the first free slot it finds, even outside that list. A flag then tells later lookups to fall back to a whole-table scan. Recovery therefore preserves the entry while departing from the paper’s placement and lookup rules.

### Tracking Batches Under Inserts and Deletes

A batch-tracking bug showed how library state could cause recovery even when the placement code itself had not changed. Elastic selected its active batch from the total number of inserts. Under a steady mix of inserts and deletes, that count kept increasing while the number of live keys stayed flat. The selected batch consequently advanced into smaller arrays, which soon ran out of room for new keys. The library was asking the construction to insert into the wrong active batch.

The fix was to count live entries instead of total inserts ([opthash#135](https://github.com/aaron-ang/opthash-rs/pull/135)). Across 100,000 mixed inserts and deletes, the fix cut recovery rebuilds from 11 to zero.

### Measuring the Algorithm and the Library Separately

The benchmark breakdown follows the same boundary. Inserts and successful lookups for both maps, plus failed lookups for Funnel, are operations the paper analyzes. Elastic's failed lookups, deletion, growth, and the other extensions measure opthash's library code instead. Reporting them separately keeps a fast insert from hiding a slow delete, or the reverse. Each run also includes `std::HashMap` and `hashbrown`, giving us mature general-purpose tables to compare on the same machine and workload.[^python]

## What I Would Do Differently

The measurement checks and the placement checks answered different questions. Pinning a core and rerunning an unchanged baseline helped establish whether a timing difference held under the same conditions. The reference comparison established whether that difference still described the chosen algorithm. A useful benchmark of a paper implementation needed both.

For another paper implementation, I would start with a simple reference and identify which choices the paper fixes before borrowing optimizations from an existing library. A changed placement would then fail a test when introduced, instead of surfacing in a later audit. The reference does not need to be fast; it needs to be clear enough that comparing it with the paper is easier than reviewing the optimized code.

## What's Still Open

The remaining work includes handling churn efficiently when the table is nearly full. Elastic can repeatedly enter placement recovery, with each recovery rebuilding the table at the same size and reinserting every live key before placing the new one. Because each rebuild takes time proportional to the live key count, repeated recovery can dominate the workload even when the normal insertion path has become faster.

opthash is licensed under Apache 2.0 and available from [PyPI](https://pypi.org/project/opthash/) (`pip install opthash`) or [crates.io](https://crates.io/crates/opthash). The Rust crate also builds without `std`.

Building opthash gave me a way to connect the paper’s probe bounds with the instructions, memory accesses, and library operations needed to use them. Whether those bounds can ultimately make it as fast as `hashbrown` in real workloads is still open. The project is [open source](https://github.com/aaron-ang/opthash-rs), and contributions are welcome, whether in optimizing the implementation consistent with the paper or improving the library around it.

[^greedy]: Here $\delta$ is the fraction of the table left free. [Yao](https://doi.org/10.1145/3828.3836) proved that among greedy tables, uniform probing's average insertion cost of $\Theta(\log(1/\delta))$ probes is optimal, and conjectured that the worst-case insertion must cost about $1/\delta$. Funnel's worst case is $O(\log^2(1/\delta))$, and the paper proves that no greedy table can do better. Elastic's average cost is $O(1)$ and its worst case is $O(\log(1/\delta))$.

[^sizing]: Elastic still rounds its _total_ size up to a power of two, because the paper fixes how the space splits into arrays and leaves the overall size open ([opthash#137](https://github.com/aaron-ang/opthash-rs/pull/137)).

[^hashing]: The paper assumes truly random hash functions, while opthash uses fixed mixers built from [wyhash](https://github.com/wangyi-fudan/wyhash) and [SplitMix64](https://prng.di.unimi.it/splitmix64.c) constants. Exactness holds for those hash functions and does not prove that they inherit the paper's bounds.

[^python]: Python binding numbers also include Python hashing, interpreter overhead, and the [PyO3](https://pyo3.rs/) crossing. They measure a binding workload, not the table.
