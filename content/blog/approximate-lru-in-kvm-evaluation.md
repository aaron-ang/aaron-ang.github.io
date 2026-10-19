+++
title = "KVM Page Eviction, Part 2: Tracking Page Use"
date = "2026-10-09"
slug = "approximate-lru-in-kvm-evaluation"
tags = ["Engineering", "Research"]
+++

In [Part 1](/approximate-lru-in-kvm/), I described our UCSD class project replacing FIFO eviction with CLOCK in KVM’s legacy MMU. KVM stores address mappings in [_shadow pages_](https://docs.kernel.org/virt/kvm/x86/mmu.html#shadow-pages); evicting one removes the mappings without deleting the guest memory they point to. If the guest accesses that memory again, a missing mapping triggers a page fault, and KVM rebuilds it before execution resumes.

CLOCK gives recently used pages a second chance by checking a reference bit, `lru_ref`. _Marking_ a page means setting that bit to 1 to record recent use. The difficulty is knowing when to mark it: most guest accesses use mappings directly in hardware without notifying KVM.

After the class, I continued the work on an x86 machine emulated by QEMU on an Arm workstation. Marking pages as KVM filled their entries helped considerably in a reuse test, but the improvement was much smaller with Redis. These experiments compare page faults and evictions; performance still needs to be measured on real hardware.[^setup] The [code](https://github.com/aaron-ang/kvm) is based on [Linux 6.14-rc7](https://github.com/torvalds/linux/tree/v6.14-rc7).

## Tracking Recent Use

### Testing the Initial CLOCK Implementation

To test whether CLOCK could retain frequently used pages, I wrote `lru_reuse_test`. The guest accesses 256 memory regions, directing 90% of its accesses to a hot set of 16 regions. Each region is covered by one last-level shadow page, letting us check whether the eviction policy retains the mappings used most often.

The remaining accesses go to cold regions. The _random_ workload chooses these regions pseudorandomly; the _scan_ workload visits them in order. Both choose pages within each region pseudorandomly. A fixed seed makes either workload repeatable, and every policy receives the same access sequence.

Even with 90% of accesses returning to the hot regions, the original CLOCK was barely better than FIFO. At 32 pages, the two policies produced identical counters on every seed; in the larger caches, CLOCK reduced faults by at most 22%.

Marking on creation gave CLOCK little information about continued use. Once KVM had linked a shadow page into the page tables, hardware could keep using it without returning to KVM. Rebuilding that link could mark the page again, but this happened infrequently for both hot and cold pages.[^initial-marking] After clearing the initial reference bits, the hand therefore tended to evict in FIFO order.

### Marking Entries as They Are Filled

A newly created last-level shadow page still has most of its 512 entries to fill, each mapping a 4 KiB page within the same 2 MiB region. The guest's first access to each unmapped page faults into `mmu_set_spte()`. This gave us a place to mark the containing shadow page again while handling work KVM already had to do, with one added call:

```c
/* in mmu_set_spte(), when a fault fills an entry of sp */
if (!prefetch)
	mark_kvm_page_accessed(sp);
```

While the guest first accesses the 4 KiB pages of a hot region, each missing mapping causes a fault that fills a shadow entry and re-marks the containing shadow page. If one of these faults occurs after the clock hand has cleared `lru_ref`, it sets the bit again, giving the page a second chance when the hand returns. With fewer accesses, a cold region is less likely to have its shadow page re-marked before the hand returns.

This marking stops once all 512 entries are filled, because subsequent accesses use the existing translations in hardware without reaching KVM. A region can therefore remain hot without its shadow page being re-marked. If the shadow page is zapped, later accesses have to rebuild its entries and mark it again as they do so. The figure below compares one hot page and one cold page with marking on creation only and with marking on fill:

![Hot page H and cold page C each map one 2 MiB region. In panel (a), both bits are 0 when the hand returns, and it zaps the older H. In panel (b), a fill re-marks H first, so C is zapped.](/images/approximate-lru-in-kvm/ref-signals.svg "Only two of the cache's shadow pages are shown, and the clock hand's trips around the rest of the ring are skipped.")

### Reading Hardware Accessed Bits

Hardware _accessed_ bits provide information about uses that do not fault into KVM. The CPU sets these bits as it uses shadow entries, and the `lru_age` module parameter lets the eviction sweep consult them. When it finds an accessed bit set, the sweep clears it and gives the page a second chance, as it would for `lru_ref`. In the next figure, a hit on $H$ leaves that hardware record even though KVM receives no fault and both pages have `lru_ref` cleared.

![H and C both have lru_ref 0, and each entry now carries an accessed bit. A hit on H sets one without a fault. The clock hand finds it set, clears it and keeps H; C has none set and is zapped.](/images/approximate-lru-in-kvm/ref-abits.svg "Compare panel (a) of the previous figure: the same two pages with `lru_ref` at 0, where the hand zapped $H$ instead.")

`lru_age` has two modes, which differ in which entries the sweep reads:

- **Leaf:** all 512 of the page's own entries.
- **Parent:** the one entry a level up that points to the page.

The CPU can set hardware accessed bits again while the sweep is running. These concurrent updates can keep pages looking recently used, making [Part 1’s FIFO fallback](/approximate-lru-in-kvm/#invariant-3-free-an-eligible-page-when-one-exists) necessary when CLOCK frees nothing.[^fallback]

There is one more difference between the modes. Leaf mode reads the same bits as _host reclaim_, the host kernel's search for guest memory it can take back. Parent mode reads none of them:

![One parent entry points to shadow page sp, which holds 512 leaf entries. Leaf mode (lru_age=1) reads and clears all 512 leaf accessed bits, the same bits host reclaim ages. Parent mode (lru_age=2) reads only the parent entry.](/images/approximate-lru-in-kvm/leaf-vs-parent.svg "`kvm_age_gfn` and `kvm_test_age_gfn` are the KVM functions that host reclaim calls to read and age the leaf bits.")

## Measuring Faults and Evictions

### Reuse-Test Results

The table compares page faults at three cache limits, averaging repeated runs with the same access sequences under every policy.[^reuse-method] Its columns show the original marking, marking on fill, and marking on fill with leaf or parent accessed bits. A reported 0% is an exact tie:

{{< table title="Change in page faults vs FIFO in lru_reuse_test" caption="Negative values mean fewer faults than FIFO. Best result per row is bold." >}}
| Workload | Cache pages | FIFO faults | on creation | on fill | fill + leaf bits | fill + parent bits |
|---|--:|--:|--:|--:|--:|--:|
| random | 32 | 212,865 | 0% | 0% | −4% | **−5%** |
| random | 64 | 175,209 | −10% | −61% | −56% | **−82%** |
| random | 128 | 118,952 | +3% | −65% | **−74%** | −61% |
| scan | 32 | 213,146 | 0% | 0% | −4% | **−5%** |
| scan | 64 | 179,096 | −11% | −81% | **−83%** | −75% |
| scan | 128 | 143,016 | −22% | −81% | −79% | **−83%** |
{{< /table >}}

At 64 and 128 cache pages, marking on fill supplied most of the improvement, reducing faults by 61–81%. Retaining a hot shadow page preserves the translations for its 2 MiB region. Evicting that shadow page forces KVM to refill the mapping for each 4 KiB page the guest subsequently touches, making repeated visits to hot regions especially costly in this test.

In the 32-page cache, marking on fill had no effect. Each eviction call zaps about 20 of the 32 pages, enough to restore 25 free slots. By the next call, nearly every page still in the cache has faulted in new entries and been marked again. Only the accessed bits reduced faults, by 4–5%. The figure below shows how one call at that size spends its first lap clearing bits and its second zapping in FIFO order:

![Eight pages of the 32-page cache, all with bit 1, drawn from head to tail. In one eviction call, the hand's first lap clears every bit and zaps nothing; its second lap zaps from the tail, oldest page first.](/images/approximate-lru-in-kvm/small-cache.svg "The two-lap bound described in [Part 1](/approximate-lru-in-kvm/#invariant-2-bound-each-sweep) lets one call clear every bit and still zap pages.")

Reading accessed bits did not consistently improve on marking on fill. Both modes had rows where they were best, but they also took more faults than plain CLOCK in others: up to 28% more in parent mode and 16% more in leaf mode. These fault counts give me no clear reason to make either mode the default, even before measuring the cost of reading the bits on real hardware.

### Redis Results

The reuse test deliberately favors repeated visits to a hot set. To see whether CLOCK would help an application as well, I ran [Redis](https://redis.io/) in a nested guest, using [`redis-benchmark`](https://redis.io/docs/latest/operate/oss_and_stack/management/optimization/benchmarks/) for SET, GET, LPUSH, and LPOP. The workload needed a little over 128 shadow pages.

Results varied enough that a single run could change the apparent ranking. I repeated the measurements and shuffled the policy order to spread changes in host load across the policies.[^redis-method] The table reports median shadow-page evictions and their median percentage change relative to FIFO:

{{< table title="Redis in a nested guest: shadow pages evicted, by policy" caption="Median shadow pages evicted; percentages show the change relative to FIFO." >}}
| Cache pages | FIFO | on fill | fill + leaf bits | fill + parent bits |
|---|---|---|---|---|
| 80 | 1,251 | 1,176 (−6%) | 982 (−22%) | 908 (−27%) |
| 96 | 360 | 282 (−22%) | 254 (−29%) | 254 (−29%) |
| 128 | 63 | 42 | 47 | 42 |
| KVM's default | 0 | 0 | 0 | 0 |
{{< /table >}}

At 96 pages, every version of CLOCK evicted fewer pages than FIFO. At 80 pages, only the accessed-bit modes showed a clear reduction, evicting 22–27% fewer pages. The measurements did not distinguish leaf mode from parent mode.[^redis-intervals]

At 128 pages, each run evicted only a few dozen shadow pages, too few to rank the policies.

Fewer evictions translated into a much smaller reduction in faults: 2–6% at 80 and 96 cache pages. At KVM’s default size, Redis still took about 35,000 faults despite evicting no shadow pages. This suggests that first touches account for most of its faults, leaving a smaller share for eviction policy to affect than in the reuse test.

These results exclude nine runs that did not finish, which were replaced with runs of the same policy. The failures and the checks for an eviction-code regression are recorded in the notes.[^redis-failures]

## Accessed Bits and Host Reclaim

The accessed bits matter outside the shadow-page cache as well. Under memory pressure, the host can reclaim guest memory, using the leaf accessed bits to help judge which pages have been used recently. Since leaf mode clears those same bits, it could make a hot guest page appear idle. I used two selftests, reusing the hot and cold regions from `lru_reuse_test`, to examine that possible interaction.

The first test uses the kernel's [idle-page tracking](https://docs.kernel.org/admin-guide/mm/idle_page_tracking.html), which queries KVM as host reclaim does but swaps nothing. In a 128-page cache, leaf mode reported about 6% of hot pages as idle after the guest had used them. FIFO, plain CLOCK, and parent mode reported none.

The second test forces host reclaim to swap a fixed amount of guest memory to disk and records which pages it chooses. Since leaf mode clears the bits reclaim uses, I expected it to lose more hot pages in the 128-page cache. Instead, leaf mode lost 10% and FIFO lost 18%. The policy differences were within the variation across five seeds, leaving this test unable to establish the expected disadvantage for leaf mode.

The clearer difference was between cache sizes. A 2,048-page cache never evicted and rarely lost hot pages to swap, whereas every policy in the 128-page cache lost about 12 percentage points more hot pages. Although the cache-size difference was larger, it came from a comparison I had not planned in advance and offers weaker evidence than the planned policy comparison.

My best explanation is that eviction itself discards the access history. A set accessed bit lets KVM report recent use of the guest page mapped by that shadow entry. Zapping a last-level shadow page removes both the entries for its 2 MiB region and the accessed bits that record recent use, depriving host reclaim of that history even for hot pages. This would affect all the eviction policies and could explain why cache size mattered more than leaf versus parent mode. The figure illustrates this proposed mechanism:

![One last-level shadow page maps the 4 KiB guest pages of one 2 MiB region, and the CPU has set the accessed bits of the hot pages' entries. The eviction policy zaps the page, and its accessed bits go with it. Host reclaim then finds no shadow entries, treats every page as unused, and swaps out pages, hot ones included.](/images/approximate-lru-in-kvm/eviction-drops-abits.svg "Only 16 of the shadow page's 512 entries are drawn, and which pages host reclaim swaps out is illustrative.")

## What the Measurements Show

Marking on fill supplied most of the improvement in the reuse test without requiring KVM to inspect hardware accessed bits. Redis showed why that result does not imply a comparable benefit for every workload: a policy can avoid rebuilding mappings yet have little effect on faults caused by first accesses.

Accessed bits let CLOCK recognize uses that KVM’s fault handling cannot see, but neither mode consistently improved on marking on fill. Reading them adds work while holding the MMU lock, and clearing leaf bits can remove information used by host reclaim. A better record of recent use therefore does not by itself establish a better eviction policy; the cost of collecting it and its effects elsewhere still matter.

These results concern KVM’s legacy MMU, rather than the TDP MMU used for ordinary VMs on modern hosts. The small caches deliberately force evictions; Redis evicted nothing at KVM’s default cache size. Establishing a practical benefit will require a workload that exceeds that default and measurements of execution time on real hardware.

## Next Steps

- **Measure performance on real hardware.** Fault counts show how much rebuilding CLOCK avoids, but not whether the saved work outweighs the sweep and accessed-bit scans. I would start with Redis under true shadow paging, with EPT or NPT disabled, using repeated runs pinned to the same core.
- **Preserve access history during eviction.** Before removing a shadow page, KVM could record its accessed bits in the host’s tracking data. An emulated test could check whether this helps retain hot guest pages; real hardware would be needed to measure the cost of scanning up to 512 entries while holding the MMU lock.
- **Test workloads that exceed the default cache.** I tried the PostgreSQL benchmark [pgbench](https://www.postgresql.org/docs/current/pgbench.html), but it either evicted nothing or hung the nested guest, even with a cache too large to require eviction. Running it on real hardware would let us investigate without relying on QEMU’s x86 emulation.

[^setup]: All measurements use the legacy MMU under true shadow paging, with hardware nested paging disabled (`npt=0`, `tdp_mmu=0`). Selecting the legacy MMU alone does not disable hardware translation; [Part 1 explains the distinction and test setup](/approximate-lru-in-kvm/#which-kvm-path-the-patch-changes). QEMU’s emulated CPU provides fault and eviction counts, not timings representative of a physical x86 CPU.

[^initial-marking]: `__kvm_mmu_get_shadow_page` marks a page when it is looked up or created while KVM builds a parent link. The initial implementation also marks successful `fast_page_fault` operations, which update permissions without taking `mmu_lock`. The `lru_ref_skips` counter records pages spared because their reference bits were set; a zero count would mean that no mark had affected eviction. [Part 1 covers the marking paths and their interaction with the sweep](/approximate-lru-in-kvm/#implementing-clock).

[^fallback]: With either accessed-bit mode, a two-minute `lru_stress_test` run fell back to FIFO a few hundred times; plain CLOCK never did. The fallback guarantees progress when concurrent updates prevent the bounded sweep from freeing an eligible page.

[^reuse-method]: Each row averages 10 to 25 seeds of 200,000 accesses. Every policy replays the same seeds under shadow paging on the emulated x86 CPU. Each of the guest’s 256 regions spans 2 MiB, with 16 hot regions receiving 90% of accesses. Seeds were added in batches fixed in advance until the nonzero comparisons’ 95% confidence intervals excluded zero. Checking the intervals after each batch makes the reported uncertainty optimistic; the intervals should be interpreted with that limitation.

[^redis-method]: For each cache size, each policy ran 16 times, or 8 at KVM’s default size, with 20,000 requests per test. Policy order was shuffled. The eviction counter is `mmu_recycled`; the table shows its median and the median percentage change relative to FIFO.

[^redis-intervals]: At 80 and 96 cache pages, the 95% confidence intervals for every percentage change excluded zero except the −6% for marking on fill at 80 pages. Leaf and parent mode did not separate from each other. At 128 pages, evictions were too few to rank the policies.

[^redis-failures]:
    Nine of 233 Redis runs did not finish and were replaced with extra runs of the same policy. Eight failed under heavy host load, across all four policies. Repeated test boots point to QEMU’s multi-threaded emulation, which failed as often under an unmodified upstream kernel as under the modified one. The ninth run hit a pair of upstream bugs outside the eviction code.[^svm]

    To check whether the new eviction code caused these failures, the emulated machine was booted 20 times under each of five kernels, upstream included. Every boot passed KVM’s MMU stress test, though 20 passing boots per kernel still leave room for a failure rate of up to 17%.

[^svm]: QEMU's emulated AMD CPU clears only the valid bit of the field that reports interrupted event delivery, and stale data stays behind. KVM's AMD code reads that field without checking the valid bit and decides that an ordinary write to a guest page table cannot be emulated. As of writing, neither bug is fixed upstream. In later database-benchmark runs on the same machine, a one-line KVM change that tests the valid bit made these errors go away.
