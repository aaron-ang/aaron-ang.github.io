+++
title = "Building opthash: a hash-table paper, on real hardware"
date = "2023-09-06"
slug = "building-opthash"
tags = ["Design", "Research", "Engineering"]
+++

## From Paper to Code

Earlier this year, a [Quanta video about recent breakthroughs in computer science](https://www.quantamagazine.org/videos/2025s-biggest-breakthroughs-in-computer-science/) covered a result that stuc with me: a new way to build open-addressed hash tables. In open addressing, every key and value lives directly in one array. When two keys want the same slot, the table walks a sequence of other slots (the _probe sequence_) until it finds an empty one. Tables slow down as they fill, because the fuller the array, the longer that walk gets.

The paper behind the result, [_Optimal Bounds for Open Addressing Without Reordering_](https://arxiv.org/abs/2501.02305), gives two constructions, Elastic Hashing and Funnel Hashing. Both keep insertions and successful lookups cheap even when the table is almost full, and neither ever moves an entry once it is placed. The surprising one is Elastic Hashing. Classic schemes are greedy: take the first empty slot you find; elastic deliberately skips past empty slots in its own probe sequence, allowing it to beat the lower bound that holds for greedy probing.

There was no official implementation to be found, so I decided to write one. Building the algorithms seemed a better way to understand them than rereading the proofs, and Rust offered control over memory layout and SIMD along with an excuse to learn more of the language.

That became [opthash](https://github.com/aaron-ang/opthash-rs). It started as a [learning exercise](https://news.ycombinator.com/item?id=48365720), not as a replacement to `std::HashMap` or `hashbrown`. The open question was how these constructions would hold up once caches, vector instructions, allocators, and the demands of a real library got involved. As it happened, the harder question was whether they would survive being optimized at all.

The two constructions attack the same collision problem with different shapes. Elastic splits the table into geometrically shrinking arrays and fills the largest to about 75 percent before moving on. After that, insertions work in batches across two adjacent arrays: a bounded number of tries in the larger one, the budget depending on how full it is, then the first free slot in the smaller one. When either array is too full or too empty, the batch skips it. A lookup does not know any of this. It walks one global probe sequence that interleaves every array in a fixed order until it finds the key.

Funnel also uses shrinking levels, but its main levels are made of fixed-width buckets: later levels have fewer buckets, not narrower ones. An insertion tries one bucket per level, chosen by the key, before falling through to a small special area with single-slot choices and then two-choice buckets. The paper sets the bucket width, level count, and shrink ratio as functions of how much headroom the table keeps. opthash pins one such choice and treats any change to it as a different algorithm for trace purposes.

The first version mapped the paper's structures straight onto Rust structs. Each region owned a `Vec<Option<Entry>>`. The code looked like the diagrams, and that was about all it had going for it. Every region was its own allocation, every probe chased a pointer, and every rejected slot had already pulled a full entry into cache.

The first redesign kept the regions separate but split control metadata out from the entries. A lookup could check a compact control byte before loading a key and value, which skipped most payload reads for empty or non-matching slots. Later, [a single-arena refactor](https://github.com/aaron-ang/opthash-rs/pull/68) moved every region into one allocation while keeping the logical boundaries.

Layout was not the whole problem. The paper studies fixed-size, insertion-only tables. A library has to replace duplicate keys, delete, clear, grow, and do something sensible when a finite candidate sequence runs out. None of that could be judged fairly, though, until the basic paper-shaped tables stopped fighting the hardware on every probe.

## Making It Fast

The bottleneck was never the theoretical probe count. It was what each probe made the processor do, and compact metadata let a probe reject a slot before touching its payload at all.

[SwissTable](https://abseil.io/about/design/swisstables) was the obvious model. It keeps a small hash fingerprint in each control byte and compares a whole vector of them at once. opthash picked up seven-bit fingerprints, SIMD control-byte scans, and the `foldhash` hasher. Power-of-two sizes turned some divisions into masks. The single arena kept the hot control regions close together and cut allocation traffic.

All of this made the maps faster. It also made them harder to measure. A layout change can push fields or code across a cache-line boundary, so a faster run does not by itself tell you what got faster.

The lesson that stuck came from an unmerged [mini-hash sidecar experiment](https://github.com/aaron-ang/opthash-rs/pull/12). It added metadata meant to reject candidates earlier, and the first run showed three Funnel Python workloads improving by 33 to 39 percent. The two runs had landed on different classes of CPU core, one clocked a gigahertz faster than the other. Pinned to the same core, Funnel insertion still gained 21 percent, but union got 9 to 12 percent slower, lookups moved a few percent the wrong way, and the median across the suite was flat. Add 4 bytes per slot of memory for that, and the experiment closed as net-neutral.

After that, wall-clock numbers on their own stopped counting as evidence. The local harness now pins each run to one core, fixes memory placement, and saves named baselines. The unchanged `std` and `hashbrown` results act as controls for the run's noise floor. Assembly and hardware counters show whether a win came from fewer instructions or from better cache or branch behavior. Smaller experiments kept confirming the need for this: software prefetching did nothing or hurt, reordering fields sometimes produced worse codegen, and a 64-lane AVX-512 path gave way to the plain 16-lane scan. One change, swapping a batch-plan `Vec` for a boxed slice, lost 6.5 percent on its own and was reverted, then came back for free inside a later layout cleanup.

Better measurement caught noisy wins and local wins. It also turned up something worse than noise. Some optimizations that measured well, power-of-two geometry and changed probing arithmetic in particular, had changed which slots the algorithms visited. The maps were faster because they were no longer quite the algorithms from the paper.

## Returning to the Paper

Line the code up against the paper and the drift was obvious. Rounding the arrays to powers of two changed how hashes mapped to slots. Elastic's triangular walk visited groups out of the paper's order. Altered probe budgets and reserve behavior moved the point where insertion gave up or kept going. Funnel's bucket and overflow handling had its own mismatches. Each one looked like a one-off fix at first, but patching could never produce a trustworthy reference while some other optimization might still be quietly changing geometry or probe order elsewhere.

So the project got an [exact-default rewrite](https://github.com/aaron-ang/opthash-rs/pull/115) around one fixed instantiation of the paper's constructions. The paper leaves Elastic's probe-budget constant free and treats its Funnel constants as sufficient rather than canonical, so "exact" here means exact to the instantiation opthash pins. Out went power-of-two geometry, triangular Elastic probing, the altered probe budgets, and the performance-oriented reserve policy. The single arena, control bytes, and SIMD scans stayed, since none of them change a logical choice. Plain scalar reference implementations pin down the expected candidate order, so optimized code is free to change how candidates get checked but not which ones appear or when.

The reference is deliberately dumb. Condensed, the Elastic insertion rule for a batch reads:

```rust
// Batch i works across levels `current` (A_i) and `next` (A_{i+1}).
let free_current = self.levels[current] - self.occupancy[current];
let free_next = self.levels[next] - self.occupancy[next];

if free_current <= current_threshold {
    // Case 2: A_i is too full. Skip it, first free slot in A_{i+1}.
    self.uniform_vacancy(next, key)
} else if free_next <= self.levels[next] / 4 {
    // Case 3: A_{i+1} is still too empty. First free slot in A_i.
    self.uniform_vacancy(current, key)
} else {
    // Case 1: a bounded budget of probes in A_i, computed from how
    // full it is, then fall through to the first free slot in A_{i+1}.
    let budget = probe::elastic_dyadic_probe_budget(free_current, self.levels[current], reserve, C);
    (0..budget)
        .find_map(|j| self.vacancy(current, key, j))
        .unwrap_or_else(|| self.uniform_vacancy(next, key))
}
```

Every probe goes through one routing function that maps a key, a level, and a probe index to a slot. The optimized table has to land on the same slot for the same three inputs, and the tests check that it does.

It cost a lot. The regression was first recorded in the [design notes](https://github.com/aaron-ang/opthash-rs/blob/e2aab0104d5afad0749a9e5fb949cb43f68aba6c/docs/superpowers/specs/2026-07-11-paper-faithful-performance-parity-design.md#context), and a fresh run for this article reproduced it: `v0.10.3` against the exact rewrite, same fixtures, interleaved twice on one pinned Cortex-X925 core at 3.9 GHz. The fixture is a 20,000-entry map of `u64` keys with 100,000 operations per sample. Numbers are nanoseconds per operation, old version to exact version, with the slowdown factor:

| Map         |            Insert |             Hit |            Miss |
| ----------- | ----------------: | --------------: | --------------: |
| Elastic     | 9.4 → 110 (11.7x) | 3.7 → 32 (8.6x) | 3.7 → 264 (70x) |
| Funnel      |   8.0 → 17 (2.1x) | 5.0 → 15 (3.0x) | 8.0 → 55 (6.9x) |
| `hashbrown` |               3.6 |             2.1 |             1.9 |

Both pairs agreed within 5 percent on every cell. The `std` and `hashbrown` controls were flat on insert and hit. On miss, `std` was 1.5x slower and `hashbrown` 0.8x in the exact tree's binary, identically in both pairs, so that is the two bench binaries compiling the same control code differently, not run-to-run noise. Either way, this is the distance between two implementations of the same project, not a statement about the paper's bounds.

Hardware counters say where the time went. Five seconds of Elastic hit lookups on the same pinned core, old version first:

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

The totals look alike because both ran for five seconds. Divide by the number of lookups each version completed in that window and the picture changes. Both versions take about one L1 miss per lookup and run at the same instructions per cycle. The exact version simply executes about 8x the instructions per lookup, roughly 590 against 70, and takes 13x the branch misses. On this fixture the exact hit path is not memory-bound. It is doing more work: walking the paper's interleaved probe order and doing exact range reduction on every candidate. Insert is different. There the exact version also takes about 10x the cache misses per operation. It visits far more slots before it settles, and those slots are spread across two arrays instead of one contiguous group.

The slower version stayed. A fast implementation of a different candidate order could not answer the question that started the project. The reset also supplied an oracle: if an optimization disagrees with the reference about a single slot, it is changing the algorithm, not speeding it up.

The first recovery showed how much room that still leaves. A [trace-preserving rewrite of Elastic insertion](https://github.com/aaron-ang/opthash-rs/pull/121) cut that operation's time by about 77 percent, roughly a 4x speedup over the reset version, with exact geometry and candidate order intact. That turned the vague question into a concrete one: how far can the fixed construction go on its own terms, with the dynamic-library machinery kept outside its boundary?

## What opthash Is Testing Now

opthash now treats each fixed-size table between growth events as an epoch. Within an epoch, placement and lookup follow the paper's geometry and probe order. Deletion, cleanup, clearing, and growth live in separate lifecycle code, so those library requirements cannot quietly redefine the normal trace.

There is one deliberate exception. If every finite candidate is occupied, a recovery path widens placement and lookup beyond the paper's model rather than drop an entry. The implementation also uses concrete deterministic mixers where the analysis assumes random hashing. So "paper-exact" is a claim about normal traces under the implementation's hashing. It does not cover the recovery path, and it is not a proof that deterministic hashing inherits the random-hashing bounds.

The benchmark suite compares Elastic and Funnel against `std::HashMap` and `hashbrown`. `hashbrown` is there as a well-optimized reference, not as a target opthash already hits. The Python bindings need the same care. Python hashing, interpreter overhead, the GIL, and the PyO3 crossing can cost more than the Rust operation underneath, so those numbers measure a binding workload rather than the table.

The workloads also have to keep two questions apart. Fixed-size insertion and successful lookup sit closest to the theoretical model, and Funnel's model covers unsuccessful lookups too. Elastic misses, deletion, cleanup, and growth measure what a dynamic library costs. Fold both into one score and you lose track of whether the time went to the construction or to the lifecycle around it.

Exact traces leave a lot of room. Metadata can move for locality, candidate checks can be batched with SIMD, routing state can be cached, arithmetic and scheduling can improve. The rule is that every change preserves candidate order and helps the whole suite, not one microbenchmark. Scattered memory access may still cost more than fewer probes save, and one construction may suit SIMD widths better than the other. Those are results to measure.

opthash now has a reference that reproduces one instantiation of the paper's candidate order, and a clear line around the behavior the paper does not cover. Whether the paper's probe bounds turn into useful wall-clock behavior on real hardware, and how much of the lost performance comes back without changing the algorithms again, is what the rest of the project is for.
