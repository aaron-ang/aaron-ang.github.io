+++
title = "KVM Page Eviction, Part 2: Tracking Page Use"
date = "2026-10-09"
slug = "approximate-lru-in-kvm-evaluation"
tags = ["Engineering", "Research"]
+++

In [Part 1](/approximate-lru-in-kvm/), I described our UCSD class project replacing FIFO eviction with CLOCK in KVM’s legacy MMU. KVM stores address mappings in [_shadow pages_](https://docs.kernel.org/virt/kvm/x86/mmu.html#shadow-pages); evicting one removes the mappings without deleting the guest memory they point to. If the guest accesses that memory again, a missing mapping triggers a page fault, and KVM rebuilds it before execution resumes.

CLOCK gives recently used pages a second chance by checking a reference bit, `lru_ref`. _Marking_ a page means setting that bit to 1 to record recent use. The difficulty is knowing when to mark it: most guest accesses use mappings directly in hardware without notifying KVM.

After the class, I continued the work on a bare-metal x86 host.[^setup] In a reuse test, marking pages when KVM installed their mappings cut page faults by 61–81%. With Redis, marking on install alone and parent mode raised request rates by 58–134% between them in the two smallest caches. With pgbench, every version of CLOCK raised transactions per second by 17–33% using 200 clients and KVM’s default cache size. The [code](https://github.com/aaron-ang/kvm) is based on [Linux 6.14-rc7](https://github.com/torvalds/linux/tree/v6.14-rc7).

## Tracking Recent Use

### Testing the Initial CLOCK Implementation

To test whether CLOCK could retain frequently used pages, I wrote `lru_reuse_test`. The guest accesses 256 memory regions, directing 90% of its accesses to a hot set of 16 regions. Each region is covered by one last-level shadow page, letting us check whether the eviction policy retains the mappings used most often.

The remaining accesses go to cold regions. The _random_ workload chooses these regions pseudorandomly; the _scan_ workload visits them in order. Both choose pages within each region pseudorandomly. A fixed seed makes either workload repeatable, and every policy receives the same access sequence.

Even with 90% of accesses returning to the hot regions, the original CLOCK was barely better than FIFO. At 32 pages, the two policies produced identical fault and eviction counts on every seed. In the larger caches, the original CLOCK reduced faults by at most 22%, but produced about 3% more faults than FIFO in the random workload at 128 pages.[^paired-comparisons]

Marking on creation gave CLOCK little information about continued use. Once KVM had linked a shadow page into the page tables, hardware could keep using it without returning to KVM. Rebuilding that link could mark the page again, but this happened infrequently for both hot and cold pages.[^initial-marking] After clearing the initial reference bits, the hand therefore tended to evict in FIFO order.

### Marking on Entry Installation

A newly created last-level shadow page initially has mostly non-present entries. Each entry can map a 4 KiB guest page within the same 2 MiB region. When the guest accesses a page whose shadow entry is not present, KVM installs the missing mapping through `mmu_set_spte()`. Adding one call here records recent use by setting the containing shadow page’s `lru_ref` bit. I call this _marking on install_. The `sp` argument below points to the shadow page’s metadata (`struct kvm_mmu_page`):

```c
/* in mmu_set_spte(), when a fault installs or updates an entry of sp */
if (!prefetch)
    mark_kvm_page_accessed(sp);
```

The same call also marks the page when a fault updates an entry that is already present. One example is a first write, which KVM must record by setting the dirty bit in the guest’s own page table.

While the guest first accesses the 4 KiB pages of a hot region, each missing mapping causes a fault, during which KVM installs the mapping and re-marks the containing shadow page. If one of these faults occurs after the clock hand has cleared `lru_ref`, it sets the bit again, giving the page a second chance when the hand returns. With fewer accesses, a cold region is less likely to have its shadow page re-marked before the hand returns.

Once all 512 entries are present and permit the guest’s reads and writes, those accesses proceed in hardware without reaching KVM, so they no longer re-mark the shadow page. A region can therefore remain hot without its shadow page being re-marked. If the shadow page is zapped, later accesses have to rebuild its entries and mark it again as they do so. The figure below compares one hot page and one cold page with marking on creation only and with marking on install:

![Hot shadow page H and cold shadow page C each map one 2 MiB region. In panel (a), both bits are 0 when the hand returns, and it zaps the older H. In panel (b), installing a mapping re-marks H first, so C is zapped.](/images/approximate-lru-in-kvm/ref-signals.svg "Only two of the cache's shadow pages are shown, and the clock hand's trips around the rest of the ring are skipped.")

### Reading Hardware Accessed Bits

Hardware _accessed_ bits provide information about uses that do not fault into KVM. The CPU sets these bits as it uses shadow entries, and the `lru_age` module parameter lets the eviction sweep consult them. When it finds an accessed bit set, the sweep clears it and gives the page a second chance, as it would for `lru_ref`. In the next figure, a hit on $H$ leaves that hardware record even though KVM receives no fault and both pages have `lru_ref` cleared.

![H and C both have lru_ref 0, and each entry now carries an accessed bit. A hit on H sets one without a fault. The clock hand finds it set, clears it and keeps H; C has none set and is zapped.](/images/approximate-lru-in-kvm/ref-abits.svg "Compare panel (a) of the previous figure: the same two pages with `lru_ref` at 0, where the hand zapped $H$ instead.")

`lru_age` has two modes, which differ in which entries the sweep reads:

- **Leaf:** all 512 of the page's own entries.
- **Parent:** the one entry a level up that points to the page.

There is one more difference between the modes. Leaf mode reads the same bits as _host reclaim_, the host kernel's search for guest memory it can take back. Parent mode reads none of them:

![One parent entry points to shadow page sp, which holds 512 leaf entries. Leaf mode (lru_age=1) reads and clears all 512 leaf accessed bits, the same bits host reclaim ages. Parent mode (lru_age=2) reads only the parent entry.](/images/approximate-lru-in-kvm/leaf-vs-parent.svg "`kvm_age_gfn` and `kvm_test_age_gfn` are the KVM functions that host reclaim calls to read and age the leaf bits.")

Consulting accessed bits lets a page survive one more visit from the clock hand. The sweep checks `lru_ref` first: when both the reference bit and accessed bits are set, the first visit clears `lru_ref`, and the second clears the accessed bits. If every page starts with both reference and accessed bits set, the sweep reaches [Part 1’s two-lap limit](/approximate-lru-in-kvm/#invariant-2-bound-each-sweep) without freeing a page. Enabling `lru_age` therefore extends the budget to a third lap, allowing the hand to return to pages whose bits it has cleared:

```c
uint age = shadow_accessed_mask ? READ_ONCE(lru_age) : LRU_AGE_OFF;
unsigned long budget = (age ? 3 : 2) * kvm->arch.n_used_mmu_pages;
```

Running vCPUs can still set accessed bits again after the hand clears them. Whenever the bounded sweep frees nothing, [Part 1’s FIFO fallback](/approximate-lru-in-kvm/#invariant-3-free-an-eligible-page-when-one-exists) zaps the oldest pages instead.[^fallback]

## Measuring Faults and Evictions

### Reuse-Test Results

The table compares page faults at three cache limits, averaging repeated runs with the same access sequences under every policy.[^reuse-method] Its columns show the original marking, marking on install, and marking on install with leaf or parent accessed bits. A reported 0% is an exact tie:

{{< table title="Change in page faults vs FIFO in lru_reuse_test" caption="Negative values mean fewer faults than FIFO. Best result per row is bold; rows where no policy beats FIFO have no bold entry." >}}

| Workload | Cache pages | FIFO faults | on creation | on install | install + leaf bits | install + parent bits |
| --- | --: | --: | --: | --: | --: | --: |
| random | 32 | 208,396 | 0% | 0% | +0.2% | 0% |
| random | 64 | 174,454 | −8% | −61% | −62% | **−81%** |
| random | 128 | 118,977 | +3% | −65% | **−74%** | −61% |
| scan | 32 | 208,644 | 0% | 0% | +0.2% | 0% |
| scan | 64 | 178,357 | −10% | **−81%** | **−81%** | **−81%** |
| scan | 128 | 142,826 | −22% | **−81%** | **−81%** | **−81%** |
{{< /table >}}

At 64 and 128 cache pages, marking on install supplied most of the improvement, reducing faults by 61–81%. Retaining a hot shadow page preserves the translations for its 2 MiB region. Evicting that shadow page forces KVM to reinstall the mapping for each 4 KiB page the guest subsequently touches, making repeated visits to hot regions especially costly in this test.

In the 32-page cache, every policy evicted as many shadow pages as FIFO on every seed. Fault counts also matched FIFO, except under leaf mode, which took 0.2% more, or about 380 extra faults per run.

Each eviction call runs until 25 slots are free. Because a call starts with fewer than 5 free slots, it zaps about 20 pages, most of the 32-page cache. Before the next call, faults install new mappings in nearly every page left in the cache and mark it again. The sweep's first lap therefore clears every bit and zaps nothing; its second lap zaps the oldest pages first, as FIFO does. The figure shows one such call with only `lru_ref` in use:

![Eight pages of the 32-page cache, all with bit 1, drawn from head to tail. In one eviction call, the hand's first lap clears every bit and zaps nothing; its second lap zaps from the tail, oldest page first.](/images/approximate-lru-in-kvm/small-cache.svg "The two-lap bound described in [Part 1](/approximate-lru-in-kvm/#invariant-2-bound-each-sweep) lets one call clear every bit and still zap pages. With accessed bits enabled, the hand spends its second lap clearing those bits and zaps on its third.")

Reading accessed bits improved fault counts only in the random workload; in the scan workload at 64 and 128 pages, both modes matched marking on install alone on every seed. Apart from the 32-page cache, leaf mode averaged no more faults than marking on install in every configuration, with its largest reduction, 25%, in the 128-page random test. Parent mode had a larger benefit in the 64-page random test, roughly halving faults, but incurred 12% more faults at 128 pages.[^paired-comparisons] The fault counts favor different modes at different cache sizes. They leave out the cost of reading accessed bits, which the Redis measurements below show.

### Redis Results

The reuse test deliberately favors repeated visits to a hot set. To see whether CLOCK would help an application as well, I ran [Redis](https://redis.io/) in a guest, using [`redis-benchmark`](https://redis.io/docs/latest/operate/oss_and_stack/management/optimization/benchmarks/) for SET, GET, LPUSH, and LPOP. The workload needed a little over 128 shadow pages.

In this kernel version, KVM’s default cache limit is 2% of the guest memory pages it tracks, with a minimum of 64. KVM tracks guest memory twice, once for normal operation and once for [System Management Mode](https://en.wikipedia.org/wiki/System_Management_Mode), so for the 1 GiB guest used here the limit is about 10,500 shadow pages. The test limits of 80–128 pages are deliberately small by comparison.

Results varied enough that a single run could change the apparent ranking. I repeated each measurement 16 times and shuffled the policy order to spread changes in host load across the policies.[^redis-method]

{{< table title="Redis: evictions and throughput by policy" caption="Medians of 16 runs per policy and cache size. Throughput is the mean requests per second across the four benchmark tests. Percentages show the change in each median relative to FIFO." >}}

| Cache pages | Measure | FIFO | on install | install + leaf bits | install + parent bits |
| --- | --- | --- | --- | --- | --- |
| 80 | evictions | 982,858 | −47% | +1% | −53% |
| 80 | requests/s | 5,006 | +107% | −27% | +134% |
| 96 | evictions | 139,095 | −39% | −28% | −64% |
| 96 | requests/s | 27,930 | +58% | +1% | +121% |
| 128 | evictions | 1,929 | −19% | −35% | −36% |
| 128 | requests/s | 179,399 | +0.6% | +0.6% | +0.9% |
| KVM’s default | evictions | 0 | 0 | 0 | 0 |
| KVM’s default | requests/s | 182,967 | +0.2% | +0.1% | +0.1% |
{{< /table >}}

At 80 and 96 pages, the cache was far too small for the workload, and FIFO’s request rate fell to 3–15% of its rate at KVM’s default size. Marking on install and parent mode cut evictions by 39–64% and faults by 41–60%, and raised throughput by 58–134%, with every confidence interval excluding zero. Parent mode also outperformed marking on install alone in both caches, in evictions as well as throughput.[^redis-intervals]

Leaf mode behaved differently. At 80 pages, it evicted as many shadow pages as FIFO and handled 27% fewer requests per second, although it took 25% fewer faults. In that cache, it scanned a median of 1.4 billion leaf entries per run, compared with about 1.1 million parent entries for parent mode. A CPU profile of the two host cores running the guest showed the cost of those scans: under leaf mode, 34–37% of samples fell in the two functions that read and clear accessed bits, against 0.3% under parent mode.[^profile] At 96 pages, where the sweep ran less often, leaf mode matched FIFO’s throughput while evicting 28% fewer pages.

At 128 pages, every version of CLOCK reduced evictions by 19–36% and faults by 4–7%, but throughput changed by less than 1%. At KVM’s default size, Redis evicted nothing and took about 100,000 faults under every policy, and the policies performed alike. Those faults occurred without shadow-page eviction, leaving no eviction-related faults for CLOCK to prevent. A second set of runs with 1,000,000 requests per test showed the same pattern.[^metal-long]

### pgbench Results

Each PostgreSQL client is a separate process with its own address space and therefore its own shadow pages. With 16 clients, the 2 GiB guest stayed within KVM’s default cache size; with 200, it exceeded it. Every run finished.[^pgbench-method]

{{< table title="pgbench at KVM’s default cache size" caption="Medians of 8 runs per policy. Counts cover only the benchmark. Percentages show the change in each median relative to FIFO." >}}

| Clients | Measure | FIFO | on install | install + leaf bits | install + parent bits |
| --- | --- | --- | --- | --- | --- |
| 16 | evictions | 0 | 0 | 0 | 0 |
| 16 | transactions/s | 7,070 | +0.1% | −0.1% | +0.2% |
| 200 | evictions | 521,552 | −18% | −44% | −45% |
| 200 | transactions/s | 3,452 | +17% | +18% | +33% |
{{< /table >}}

With 200 clients, every version of CLOCK evicted fewer shadow pages and processed more transactions per second than FIFO. No run of any CLOCK version was as slow as the fastest FIFO run: FIFO ranged from 3,418 to 3,519 transactions per second, and parent mode from 4,500 to 4,624. Leaf and parent mode evicted about the same number of pages, but parent mode was 12% faster, again consistent with the cost of leaf mode’s scans. A single run with a cache too large to evict reached 4,807 transactions per second, so parent mode recovered most of the throughput that FIFO lost.

## Accessed Bits and Host Reclaim

The accessed bits matter outside the shadow-page cache as well. Under memory pressure, the host can reclaim guest memory, using the leaf accessed bits to help judge which pages have been used recently. Since leaf mode clears those same bits, it could make a hot guest page appear idle. Two selftests examine that possible interaction, reusing the hot and cold regions from `lru_reuse_test`.[^reclaim-lists]

The first test uses the kernel's [idle-page tracking](https://docs.kernel.org/admin-guide/mm/idle_page_tracking.html), which queries KVM as host reclaim does but swaps nothing. In a 128-page cache under leaf mode, idle-page tracking reported about 8% of hot pages as idle after the guest had used them, with some reported on every seed. Under FIFO and marking on install alone, it reported none; under parent mode, it reported two pages, on one of ten seeds.

The second test asks whether clearing those bits causes more hot guest pages to be swapped out. It boots the host with 8 GiB of memory, forces host reclaim to swap a fixed amount of guest memory to a dedicated disk, and records which pages reclaim chooses. In the 128-page cache, reclaim swapped out 1.4% of hot pages under leaf mode, on average, and 0.0–0.2% under FIFO, marking on install alone, and parent mode. Leaf mode's excess over each of the other three policies had an interval above zero. Nearly all the hot pages it lost still had shadow entries, so reclaim found their accessed bits cleared rather than missing.[^swap-intervals]

That comparison cannot isolate the effect of clearing accessed bits, because leaf mode also changes which shadow pages CLOCK evicts. To separate the two effects, a test variant of marking on install cleared leaf accessed bits without using them to guide eviction. Whenever the hand spared a shadow page because its reference bit was set, the variant cleared that page's leaf accessed bits too. Without host reclaim, its eviction counts matched those of marking on install alone exactly. Under host reclaim, it swapped out 0.1% of hot pages, no more than marking on install alone. Clearing accessed bits on pages the hand spares therefore did not reproduce leaf mode's effect; the bits leaf mode clears, on pages whose reference bit has already lapsed, appear to be the ones reclaim relies on. KVM's counter of scanned entries shows that the variant reads about half as many entries as leaf mode, and it clears them only while a page's reference bit is set, whereas leaf mode reads them only after the bit is clear.

### Eviction and Access History

Eviction also removes accessed bits. A set accessed bit lets KVM report recent use of the guest page mapped by that shadow entry. Zapping a last-level shadow page removes both the entries for its 2 MiB region and their accessed bits, leaving KVM without that record even for hot pages:

![One last-level shadow page maps the 4 KiB guest pages of one 2 MiB region, and the CPU has set the accessed bits of the hot pages' entries. The eviction policy zaps the page, and its accessed bits go with it. Host reclaim then finds no shadow entries, treats every page as unused, and may swap out pages, hot ones included.](/images/approximate-lru-in-kvm/eviction-drops-abits.svg "Only 16 of the shadow page's 512 entries are drawn, and which pages host reclaim swaps out is illustrative. The swap test found no sign of this effect.")

The swap test found no sign of it. Under FIFO in the 128-page cache, 27% of hot pages had no shadow entry at the end of a test interval, yet reclaim swapped out hot pages in only one of 64 runs, 8 pages in that run, about the same as with a 2,048-page cache that never evicted. One likely reason is that KVM marks the host page accessed, through `mark_page_accessed()`, each time it handles a fault on that page. A hot page whose entry is zapped is soon accessed again, and the fault that rebuilds the entry records the use that the zap erased. Two more variants preserved the accessed bits at zap time instead, by either marking the page accessed or setting its young flag. Under FIFO and marking on install alone, neither variant changed the share of hot pages swapped out by more than 0.2 points.

## What the Measurements Show

Marking on install supplied most of the improvement in the reuse test without requiring KVM to inspect hardware accessed bits. In Redis and pgbench, it reduced evictions and raised throughput wherever the cache was too small for the workload. Retaining mappings avoids the faults needed to rebuild them. At KVM’s default cache size, Redis had no evictions and therefore no eviction-related faults for CLOCK to prevent.

Accessed bits let CLOCK recognize uses that KVM’s fault handling cannot see, but the two modes differ sharply in cost. Parent mode reads one entry per page and was the fastest policy, or tied for fastest, in every Redis and pgbench configuration that evicted. Leaf mode reads 512. In the smallest Redis cache, those scans accounted for 34–37% of samples on the two cores running the guest, and leaf mode fell below FIFO’s throughput. Leaf mode also clears the bits that host reclaim reads: it made about 8% of hot pages look idle to the host and was the only policy that measurably raised the share of hot pages host reclaim swapped out. Of the configurations measured here, parent mode gave the largest benefit without either cost.

These results concern KVM’s legacy MMU, rather than the TDP MMU used for ordinary VMs on modern hosts, and they come from one Intel host. The small caches deliberately force evictions; Redis evicted nothing at KVM’s default cache size. pgbench with 200 clients did exceed that default, and every version of CLOCK raised its throughput.

[^setup]: The host was an AWS c5n.metal instance with two 18-core Intel Xeon Platinum 8124M CPUs. All measurements use the legacy MMU under true shadow paging, with Intel’s extended page tables disabled (`kvm_intel.ept=0`, `kvm.tdp_mmu=0`). Selecting the legacy MMU alone does not disable hardware translation; [Part 1 explains the distinction](/approximate-lru-in-kvm/#which-kvm-path-the-patch-changes) and [reports correctness checks on the same host](/approximate-lru-in-kvm/#correctness-checks-on-real-hardware). The selftests used a test build of the published code without debugging options. Its extra module parameters select marking on creation only, disable the FIFO fallback, or enable the test variants described below. The Redis and pgbench runs used the published code, which behaves like the test build for the four policies they compare. Each selftest and guest ran with one vCPU, pinned to two physical cores on one NUMA node.

[^initial-marking]: `__kvm_mmu_get_shadow_page` marks a page when it is looked up or created while KVM builds a parent link. The initial implementation also marks successful `fast_page_fault` operations, which update permissions without taking `mmu_lock`. The `lru_ref_skips` counter records each page the hand spares because its reference bit is set. With marking on creation only, it recorded a median of about 1.1 skips per shadow page created in every reuse-test configuration, consistent with one skip for each creation mark and few afterward. [Part 1 covers the marking paths and their interaction with the sweep](/approximate-lru-in-kvm/#implementing-clock).

[^fallback]: The `lru_fifo_fallbacks` counter stayed at zero in every run of the reuse and swap tests, and in every `lru_stress_test` run [reported in Part 1](/approximate-lru-in-kvm/#correctness-checks-on-real-hardware), under both accessed-bit modes.

[^reuse-method]: Each seed generates a sequence of 200,000 accesses, and rows average 20 seeds each. The exceptions are the random row at 128 pages and the two larger scan rows, which average 35. All seed counts were fixed before the runs. Every policy replays the same seeds, each in a fresh VM. The guest has 256 regions of 2 MiB each. A hot set of 16 regions receives 90% of accesses.

[^paired-comparisons]: Percentage changes are calculated within each seed and then averaged, rather than reconstructed from the table’s rounded percentages. Each interval is an unadjusted 95% t-interval over the seeds of its row. In the random workload at 128 pages (random/128), the interval for creation-only CLOCK ran from 2.5% to 3.0% more faults than FIFO. Relative to marking on install alone, leaf mode’s interval ran from 23% to 28% fewer faults in random/128. In random/64, leaf mode’s interval ran from 0.1% to 2.6% fewer. For parent mode, the interval ran from 48% to 54% fewer faults in random/64. In random/128, it ran from 6% to 18% more.

[^redis-method]: Each policy ran 16 times per cache size, and each benchmark test within a run sent 20,000 requests. The guest ran Ubuntu’s 6.8 kernel with 1 GiB of memory. Each run booted a fresh guest, and cache-size order and policy order were shuffled within each round after one discarded warm-up run. Counts cover the whole run, including the guest’s boot. The eviction counter is `mmu_recycled`. Every run finished.

[^redis-intervals]: Each interval is a 95% percentile bootstrap interval for the change in a median, not adjusted for multiple comparisons. Relative to marking on install alone, parent mode’s throughput was 13% higher at 80 pages (interval 1% to 23%) and 40% higher at 96 pages (34% to 44%). It evicted 13% fewer pages at 80 pages (3% to 22%) and 40% fewer at 96 pages (35% to 42%).

[^profile]: `perf` sampled both cores for the whole of three runs per mode. The two functions are `kvm_mmu_sp_test_and_clear_young`, which walks a shadow page’s entries, and `spte_test_and_clear_young`, which tests and clears one entry. Under both modes, zapping shadow pages took another 17–24% of samples.

[^metal-long]: These runs used eight runs per policy and cache size. Relative to FIFO, marking on install raised throughput by 60% at 80 pages and 48% at 96 pages, and parent mode by 85% and 94%. Leaf mode changed it by −29% and −4%. At 128 pages and at KVM’s default size, throughput differed by less than 1%.

[^pgbench-method]: The guest had 2 GiB of memory and ran PostgreSQL 16 with 512 MB of shared buffers, a scale-40 database prewarmed into those buffers, and the read-only `pgbench -S` workload with one pgbench thread. Each run used a fresh copy of the database. Runs processed 12,000 transactions per client with 16 clients and 1,000 with 200, lasting about 30 and 60 seconds. In a pilot run with 100 clients and a cache too large to require eviction, the highest sample showed about 22,000 shadow pages in use, only slightly above the default limit, so the main runs used 200 clients. Policy order was shuffled within each of 8 rounds.

[^reclaim-lists]: Both tests ran with the host’s [multi-generational LRU](https://docs.kernel.org/admin-guide/mm/multigen_lru.html) disabled, so reclaim used the classic active and inactive lists. With it enabled, idle-page tracking also reported about 6% of hot pages as idle under FIFO. That LRU’s version of `folio_mark_accessed()` does not clear a page’s idle flag, so a use recorded through a fault does not count once the page’s shadow entry has been zapped.

[^swap-intervals]: Each policy ran the same 64 seeds. After a warm-up, each run turned swap on and locked host memory until only 40 MiB remained available, then kept locking more during six test intervals until 24 MiB in total had been swapped out, while the guest kept accessing its hot regions. At the end of each interval, the test read the host's page map to see which hot and cold guest pages were swapped out. Percentages are means over seeds. Each interval is a 95% percentile bootstrap interval over seeds, paired by seed for comparisons between policies and not adjusted for multiple comparisons. Swap went to a dedicated 2 GiB EBS volume, the host used the classic LRU lists, and proactive memory compaction was off. A 2,048-page cache, large enough that nothing was evicted, served as a no-eviction baseline. The host's out-of-memory killer ended six runs early; each was rerun with the same seed. Before the main runs, one run per policy without reclaim confirmed that no hot pages were swapped out.
