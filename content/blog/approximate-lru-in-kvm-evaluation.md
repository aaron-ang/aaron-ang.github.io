+++
title = "KVM Page Eviction, Part 2: Tracking Page Use"
date = "2026-10-09"
slug = "approximate-lru-in-kvm-evaluation"
tags = ["Engineering", "Research"]
+++

In [Part 1](/approximate-lru-in-kvm/), I described our UCSD class project replacing FIFO eviction with CLOCK in KVM’s legacy MMU. KVM stores address mappings in [_shadow pages_](https://docs.kernel.org/virt/kvm/x86/mmu.html#shadow-pages); evicting one removes the mappings without deleting the guest memory they point to. If the guest accesses that memory again, a missing mapping triggers a page fault, and KVM rebuilds it before execution resumes.

CLOCK gives recently used pages a second chance by checking a reference bit, `lru_ref`. _Marking_ a page means setting that bit to 1 to record recent use. The difficulty is knowing when to mark it: most guest accesses use mappings directly in hardware without notifying KVM.

After the class, I continued the work on an x86 machine emulated by QEMU on an Arm workstation. Marking shadow pages as KVM installed mappings in their entries helped considerably in a reuse test, but the improvement was much smaller with Redis. These experiments compare page faults and evictions; performance still needs to be measured on real hardware.[^setup] The [code](https://github.com/aaron-ang/kvm) is based on [Linux 6.14-rc7](https://github.com/torvalds/linux/tree/v6.14-rc7).

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

{{< table title="Change in page faults vs FIFO in lru_reuse_test" caption="Negative values mean fewer faults than FIFO. Best result per row is bold; rows where every policy ties FIFO have no bold entry." >}}
| Workload | Cache pages | FIFO faults | on creation | on install | install + leaf bits | install + parent bits |
|---|--:|--:|--:|--:|--:|--:|
| random | 32 | 212,816 | 0% | 0% | 0% | 0% |
| random | 64 | 175,134 | −8% | −61% | −62% | **−81%** |
| random | 128 | 119,119 | +3% | −65% | **−74%** | −62% |
| scan | 32 | 213,123 | 0% | 0% | 0% | 0% |
| scan | 64 | 179,136 | −10% | **−80%** | **−80%** | **−80%** |
| scan | 128 | 143,058 | −22% | **−81%** | **−81%** | **−81%** |
{{< /table >}}

At 64 and 128 cache pages, marking on install supplied most of the improvement, reducing faults by 61–81%. Retaining a hot shadow page preserves the translations for its 2 MiB region. Evicting that shadow page forces KVM to reinstall the mapping for each 4 KiB page the guest subsequently touches, making repeated visits to hot regions especially costly in this test.

In the 32-page cache, no form of marking changed faults or evictions: every policy took exactly as many faults as FIFO on every seed. Each eviction call restores 25 free slots, which at this size requires zapping about 20 shadow pages, most of the cache. By the next call, nearly every shadow page still in the cache has received new mappings through faults and been marked again. As a result, the sweep clears every bit before it can zap anything, and then zaps the oldest pages first, as FIFO does. The figure below shows one call at that size with only `lru_ref` in use:

![Eight pages of the 32-page cache, all with bit 1, drawn from head to tail. In one eviction call, the hand's first lap clears every bit and zaps nothing; its second lap zaps from the tail, oldest page first.](/images/approximate-lru-in-kvm/small-cache.svg "The two-lap bound described in [Part 1](/approximate-lru-in-kvm/#invariant-2-bound-each-sweep) lets one call clear every bit and still zap pages. With accessed bits enabled, the hand spends its second lap clearing those bits and zaps on its third.")

Reading accessed bits improved fault counts only in the random workload; in the scan workload, both modes matched marking on install alone on every seed. In every configuration, leaf mode averaged no more faults than marking on install, with its largest reduction, 26%, in the 128-page random test. Parent mode had a larger benefit in the 64-page random test, roughly halving faults, but incurred 9% more faults at 128 pages.[^paired-comparisons] The results favor different modes at different cache sizes. Even leaf mode’s more consistent fault counts do not yet tell us whether its gains justify the cost of reading accessed bits, which still needs to be measured on real hardware.

### Redis Results

The reuse test deliberately favors repeated visits to a hot set. To see whether CLOCK would help an application as well, I ran [Redis](https://redis.io/) in a nested guest, using [`redis-benchmark`](https://redis.io/docs/latest/operate/oss_and_stack/management/optimization/benchmarks/) for SET, GET, LPUSH, and LPOP. The workload needed a little over 128 shadow pages.

In this kernel version, KVM’s default cache limit is `max(64, guest memory pages / 50)`, roughly 2% of guest RAM. For the 1 GiB guest used here, that limit is about 5,200 shadow pages. The test limits of 80–128 pages are deliberately small by comparison.

Results varied enough that a single run could change the apparent ranking. I repeated the measurements and shuffled the policy order to spread changes in host load across the policies.[^redis-method] The table reports median shadow-page evictions and the percentage change in each median relative to FIFO:

{{< table title="Redis in a nested guest: shadow pages evicted, by policy" caption="Median shadow pages evicted, rounded to whole pages; percentages show the change relative to FIFO and use the unrounded medians." >}}
| Cache pages | FIFO | on install | install + leaf bits | install + parent bits |
|---|---|---|---|---|
| 80 | 1,312 | 1,004 (−24%) | 991 (−24%) | 984 (−25%) |
| 96 | 370 | 298 (−20%) | 296 (−20%) | 277 (−25%) |
| 128 | 63 | 44 (−31%) | 42 (−33%) | 42 (−33%) |
| KVM's default | 0 | 0 | 0 | 0 |
{{< /table >}}

In the two smallest caches, every version of CLOCK evicted 20–25% fewer pages than FIFO, with confidence intervals entirely below zero. However, the measurements could not establish an eviction advantage for either accessed-bit mode over marking on install alone, or for one accessed-bit mode over the other.[^redis-intervals]

At 128 pages, every version of CLOCK had about a third fewer median evictions than FIFO, though no run evicted more than 85 shadow pages. The confidence interval for marking on install included zero change.

Fewer evictions translated into a much smaller reduction in faults, 2–7% in the two smallest caches. With 128 pages, fault counts differed by less than 1% between policies. At KVM’s default size, Redis took about 34,000 faults despite evicting no shadow pages. This suggests that first touches account for most of its faults, leaving a smaller share for eviction policy to affect than in the reuse test.

These results exclude one run that did not finish; it was replaced with a run of the same policy.[^redis-failures]

## Accessed Bits and Host Reclaim

The accessed bits matter outside the shadow-page cache as well. Under memory pressure, the host can reclaim guest memory, using the leaf accessed bits to help judge which pages have been used recently. Since leaf mode clears those same bits, it could make a hot guest page appear idle. Two selftests examine that possible interaction, reusing the hot and cold regions from `lru_reuse_test`.

The first test uses the kernel's [idle-page tracking](https://docs.kernel.org/admin-guide/mm/idle_page_tracking.html), which queries KVM as host reclaim does but swaps nothing. In a 128-page cache under leaf mode, idle-page tracking reported about 8% of hot pages as idle after the guest had used them. Some hot pages were reported as idle on every seed under leaf mode, whereas FIFO, marking on install alone, and parent mode produced no such reports.

The second test asks whether clearing those bits causes more hot guest pages to be swapped out. It gives the emulated machine 8 GiB of memory, forces host reclaim to swap a fixed amount of guest memory to disk, and records which pages reclaim chooses. In the 128-page cache, reclaim swapped out 10% of hot pages under leaf mode, compared with 22% under FIFO, 13% under marking on install alone, and 12% under parent mode. The confidence interval supported leaf mode’s advantage over FIFO, but its smaller advantages over the other two policies remained uncertain.[^swap-intervals]

That comparison cannot isolate the effect of clearing accessed bits, because leaf mode also changes which shadow pages CLOCK evicts. To separate the two effects, a test variant of marking on install cleared leaf accessed bits without using them to guide eviction. Whenever the hand spared a shadow page because its reference bit was set, the variant cleared that page's leaf accessed bits too. Without host reclaim, its eviction counts matched those of marking on install alone exactly. With reclaim running, eviction counts differed by at most about 2% for an individual seed but matched on average. Reclaim can remove different entries between runs. When a guest access causes KVM to reinstall one of those entries, KVM marks the containing shadow page, which can change CLOCK’s later eviction choices.

Under this variant, reclaim swapped out 1.7 percentage points more hot pages than under marking on install alone. The confidence interval ranged from −2.0 to +5.3 points, leaving both a benefit and a disadvantage possible. The variant is not an exact reproduction of leaf mode's clearing, either. Leaf mode reads accessed bits only after the reference bit is clear, whereas the variant clears them while the reference bit is set. KVM's counter of scanned entries shows that the variant reads about half as many entries as leaf mode. The result therefore leaves open what would happen with leaf mode's timing and amount of clearing.

### Investigating the Cache-Size Effect

Cache size affected how many hot pages were swapped out. With a 2,048-page cache, no shadow pages were evicted, and only 2% of hot guest pages were swapped out. Depending on the policy, the proportion of hot pages swapped out was 8–20 percentage points higher with the 128-page cache. Every confidence interval for that difference excluded zero.

One possible explanation was that eviction erased the access history that host reclaim needed. A set accessed bit lets KVM report recent use of the guest page mapped by that shadow entry. Zapping a last-level shadow page removes both the entries for its 2 MiB region and their accessed bits, leaving KVM without that record even for hot pages. Since every eviction policy discards this history, the loss could explain why every policy had more hot pages swapped out in the smaller cache. The figure illustrates this explanation:

![One last-level shadow page maps the 4 KiB guest pages of one 2 MiB region, and the CPU has set the accessed bits of the hot pages' entries. The eviction policy zaps the page, and its accessed bits go with it. Host reclaim then finds no shadow entries, treats every page as unused, and may swap out pages, hot ones included.](/images/approximate-lru-in-kvm/eviction-drops-abits.svg "Only 16 of the shadow page's 512 entries are drawn, and which pages host reclaim swaps out is illustrative. The tests below found this effect too small to explain the difference between cache sizes.")

If losing access history explained the difference between cache sizes, preserving that history before zapping should help. To test this, a second variant flagged each guest page whose shadow entry had its accessed bit set before KVM removed the shadow page. Host reclaim could then count the flag as recent use. The variant was tested under both FIFO and marking on install alone. The measured proportion of hot pages swapped out rose by 1.9 percentage points under FIFO and 0.7 points under marking on install alone. Both confidence intervals allowed no change, but excluded a reduction of more than 3 points. A related experiment using the host's `mark_page_accessed()` increased the proportion swapped out under FIFO by about 5 points; these tests did not establish why.[^mark-page-accessed]

Looking at which hot pages were swapped out provided another check on the explanation. Under FIFO in the 128-page cache, about a quarter of hot pages had lost their shadow entries when reclaim ran. Reclaim swapped out 23% of those pages and 22% of the hot pages whose entries were still present. Since only a quarter of hot pages lacked entries, the difference between the two groups accounts for less than 1 percentage point of FIFO’s overall swap-out rate. Most hot pages selected by reclaim still had their shadow entries, and preserving access history had not reduced the difference between cache sizes. The cause remains unresolved.

## What the Measurements Show

Marking on install supplied most of the improvement in the reuse test without requiring KVM to inspect hardware accessed bits. In Redis, neither accessed-bit mode showed a clear eviction advantage over marking on install alone. Redis also exposed a limit to what eviction policy can change: retaining mappings avoids the faults needed to rebuild them, but cannot prevent faults caused by first accesses.

Accessed bits let CLOCK recognize uses that KVM’s fault handling cannot see, but their benefits depended on the workload and cache size. Leaf mode matched or reduced average faults relative to marking on install in every reuse-test configuration, but its main improvement came from the 128-page random workload. Whether that reduction saves enough work to offset scanning accessed bits under the MMU lock remains unmeasured. Clearing leaf bits also made about 8% of hot pages look idle to the host, though the swap test did not establish that this caused more hot pages to be swapped out. A more complete record of recent use therefore needs to be weighed against both its collection cost and its effects on host reclaim.

These results concern KVM’s legacy MMU, rather than the TDP MMU used for ordinary VMs on modern hosts. The small caches deliberately force evictions; Redis evicted nothing at KVM’s default cache size. Establishing a practical benefit will require a workload that exceeds that default and measurements of execution time on real hardware. The host-reclaim experiments leave a further question: why were more hot guest pages swapped out with the 128-page cache than with a cache that never evicted?

## Next Steps

- **Measure performance on real hardware.** Fault counts show how much rebuilding CLOCK avoids, but not whether the saved work outweighs the sweep and accessed-bit scans. I would start with Redis under true shadow paging, with EPT or NPT disabled, using repeated runs pinned to the same core.
- **Investigate why more hot guest pages are swapped out with the 128-page cache.** Preserving access history during eviction did not help, and under FIFO, reclaim selected hot pages with present shadow entries almost as often as those without. Tracing reclaim's decisions during the swap test would help identify which step selects these pages.
- **Test workloads that exceed the default cache.** I tried the PostgreSQL benchmark [pgbench](https://www.postgresql.org/docs/current/pgbench.html), but it either evicted nothing or hung the nested guest, even with a cache too large to require eviction. Running it on real hardware would show whether the hang depends on emulation.

[^setup]: All measurements use the legacy MMU under true shadow paging, with hardware nested paging disabled (`npt=0`, `tdp_mmu=0`). Selecting the legacy MMU alone does not disable hardware translation; [Part 1 explains the distinction and test setup](/approximate-lru-in-kvm/#which-kvm-path-the-patch-changes). QEMU’s emulated CPU provides fault and eviction counts, not timings representative of a physical x86 CPU. The measurements used a test build of the published code. Its extra module parameters select marking on creation only, disable the FIFO fallback, or enable the test variants described below. The reuse and swap tests ran under QEMU’s parallel emulation. Single-threaded reruns of a sample of the reuse runs, and of the swap test’s runs without reclaim, reproduced their counts exactly.

[^initial-marking]: `__kvm_mmu_get_shadow_page` marks a page when it is looked up or created while KVM builds a parent link. The initial implementation also marks successful `fast_page_fault` operations, which update permissions without taking `mmu_lock`. The `lru_ref_skips` counter records each page the hand spares because its reference bit is set. With marking on creation only, it recorded a median of about 1.1 skips per shadow page created in every reuse-test configuration, consistent with one skip for each creation mark and few afterward. [Part 1 covers the marking paths and their interaction with the sweep](/approximate-lru-in-kvm/#implementing-clock).

[^fallback]: The `lru_fifo_fallbacks` counter stayed at zero in every run of the reuse test and the swap test, and in every `lru_stress_test` run that reported it, under both accessed-bit modes. Further stress-test runs disabled the fallback through a test-build switch, and every sweep in them still freed a page.

[^reuse-method]: Each seed generates a sequence of 200,000 accesses, and rows average 20 seeds each. The exceptions are the random row at 128 pages and the two larger scan rows, which average 35. All seed counts were fixed before the runs. Every policy replays the same seeds under shadow paging on the emulated x86 CPU. The guest has 256 regions of 2 MiB each. A hot set of 16 regions receives 90% of accesses.

[^redis-method]: Each policy ran 16 times per cache size, or 8 times at KVM’s default size. Each benchmark test within a run sent 20,000 requests, and policy order was shuffled. The runs used QEMU’s parallel emulation, under which [Part 1 reports failures](/approximate-lru-in-kvm/#follow-up-on-an-emulated-x86-machine) in heavier correctness tests; the Redis counts were not repeated under single-threaded emulation. The kernel enabled KASAN and included a one-line fix for an upstream KVM bug.[^svm] The eviction counter is `mmu_recycled`; the table shows its median and the change in that median relative to FIFO.

[^redis-intervals]: Each interval is a 95% percentile bootstrap interval for the change in a median. Every pairwise eviction comparison among the three versions of CLOCK had an interval that included zero. At 80 pages, leaf mode incurred 1.6% fewer faults than marking on install, with an interval that excluded zero; no other pairwise fault comparison among the three excluded zero.

[^redis-failures]: One of 225 Redis runs did not finish: the nested guest froze during the benchmark under parent mode in the 96-page cache. A timer interrupt arrived inside the timer handler while interrupts were disabled, and the nested guest deadlocked on a lock that the first handler held. The L1 kernel running the modified KVM logged no KASAN, lockdep, or other kernel bug report. Without a kernel trace, the cause of this freeze remains unresolved. Similar timer-handler deadlocks occurred during Redis guest boots under single-threaded emulation, including on kernels without the eviction changes. [Part 1 describes those runs](/approximate-lru-in-kvm/#follow-up-on-an-emulated-x86-machine) and a one-line QEMU change that prevented those freezes in a separate test; that change was not tested under parallel emulation.

[^svm]: QEMU's emulated AMD CPU clears only the valid bit of the field that reports interrupted event delivery, and stale data stays behind. KVM's AMD code reads that field without checking the valid bit and can then decide that an ordinary write to a guest page table cannot be emulated, which stops the guest. The measured kernel adds a one-line KVM change that tests the valid bit. As of writing, neither bug is fixed upstream.

[^paired-comparisons]: Percentage changes are calculated within each seed and then averaged, rather than reconstructed from the table’s rounded percentages. Each interval is an unadjusted 95% t-interval over the seeds of its row. In the random workload at 128 pages (random/128), the interval for creation-only CLOCK ran from 2.5% to 3.0% more faults than FIFO. Relative to marking on install alone, leaf mode’s interval ran from 23% to 29% fewer faults in random/128. In random/64, leaf mode’s interval ran from 0.1% to 2.6% fewer. For parent mode, the interval ran from 48% to 53% fewer faults in random/64. In random/128, it ran from 3% to 15% more.

[^swap-intervals]: Differences are paired by seed, with 95% bootstrap confidence intervals about 7 percentage points wide. Relative to FIFO, leaf mode's advantage ranged from 9 to 16 points. Relative to marking on install alone, the interval ran from a 7-point advantage for leaf mode to a 0.1-point disadvantage. Relative to parent mode, it ran from a 6-point advantage to a 1.5-point disadvantage.

[^mark-page-accessed]: The host's `mark_page_accessed()` promotes a page toward the list that reclaim scans last. This variant used that function instead of the recent-use flag described above.
