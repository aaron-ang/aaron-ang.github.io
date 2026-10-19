+++
title = "KVM Page Eviction, Part 1: Implementing CLOCK"
date = "2025-03-22"
slug = "approximate-lru-in-kvm"
tags = ["Engineering", "Research"]
+++

When you rent a virtual machine (VM) from a cloud provider, the operating system inside it behaves as if it owns the whole computer. In reality, a [_hypervisor_](https://en.wikipedia.org/wiki/Hypervisor) underneath splits the hardware across many VMs. On Linux, that hypervisor is usually [KVM](https://linux-kvm.org) (Kernel-based Virtual Machine), which is part of the kernel itself.

One of the hypervisor’s jobs is translating the VM’s memory addresses into addresses on the host. On the path we studied, KVM caches the page tables used for those translations. When the cache runs short of space, it must choose which pages to discard. Keeping pages the guest still uses can avoid the work of rebuilding them later.

For a class project at UCSD, Alex Asch, Eric Huang, Justin Kaufman and I changed that eviction policy. KVM discarded the oldest cached pages first, using [first-in-first-out](https://en.wikipedia.org/wiki/Page_replacement_algorithm#First-in,_first-out) (FIFO). We replaced it with [CLOCK](https://en.wikipedia.org/wiki/Page_replacement_algorithm#Clock), a low-cost approximation of [least recently used](https://en.wikipedia.org/wiki/Page_replacement_algorithm#Least_recently_used) (LRU), to favor retaining pages used recently. The code is on [GitHub](https://github.com/aaron-ang/kvm), based on [Linux 6.14-rc7](https://github.com/torvalds/linux/tree/v6.14-rc7).

This first part explains the memory-management background, our CLOCK implementation, and the kernel changes and tests needed to keep eviction safe. In [Part 2](/approximate-lru-in-kvm-evaluation/), I continue the work with better access tracking and experiments evaluating the policy.

## KVM’s Page-Table Cache

### Shadow Paging

Programs use _virtual_ addresses, which the CPU translates through [_page tables_](https://en.wikipedia.org/wiki/Page_table) maintained by the operating system. For the 4 KiB pages considered here, the tables identify the physical page backing each virtual page. On the x86 setup in this project, those tables form a tree with 512 entries per table. The CPU walks from the root through intermediate entries to a _last-level_ entry containing the physical page number.

In a VM there are two of these lookups. The guest OS keeps its own page tables, mapping program addresses to what it thinks are physical addresses. The hypervisor maps these guest physical addresses to the host memory backing the VM. KVM keeps this second mapping in _memslots_; each memslot describes a span of guest physical addresses and the host virtual memory backing it.

Modern CPUs support both translations in hardware through Intel’s [EPT](https://en.wikipedia.org/wiki/Second_Level_Address_Translation#Extended_Page_Tables) (Extended Page Tables) or AMD’s NPT (Nested Page Tables). Without that support, KVM uses _shadow paging_: it builds tables that combine the guest and host mappings, translating guest virtual addresses directly to host physical addresses. The CPU walks these _shadow page tables_, while KVM tracks changes to the guest’s page tables and updates the corresponding shadow mappings.

A [_shadow page_](https://docs.kernel.org/virt/kvm/x86/mmu.html#shadow-pages) is one memory page holding entries from these shadow page tables. KVM fills an entry on the guest’s first access to the page it maps. Once filled, that entry supplies the result of both lookups in one translation:

![Three tables: the guest page table, KVM's memslots, and the shadow page table the CPU walks. The filled entry 0x5000 takes one lookup. Touching unfilled 0x4000 faults, and KVM resolves it through both tables to 0x79000 and stores it.](/images/approximate-lru-in-kvm/two-level-translation.svg "Each page table tree is drawn as a flat list of mappings.")

### Which KVM Path the Patch Changes

Our patch changes KVM’s older memory management unit (MMU) implementation, called the [legacy MMU](https://docs.kernel.org/virt/kvm/x86/mmu.html), which manages shadow paging. KVM also has a newer [two-dimensional paging (TDP) MMU](https://lwn.net/Articles/832835/) for hardware-assisted translation, and our eviction code does not run on that path.

Selecting the legacy MMU is a separate choice from disabling hardware translation. Because the legacy MMU can also manage EPT or NPT tables, `tdp_mmu=0` does not by itself force shadow paging. The legacy path is also used on CPUs without EPT or NPT and for some nested VMs, where one VM runs another. The follow-up tests below explicitly disable hardware nested paging to exercise true shadow paging.

### Cache Limits and Zapping

For each shadow page, KVM allocates a small `struct kvm_mmu_page` and puts it on a linked list called `active_mmu_pages`, with new pages going at the head.

Before handling a fault, KVM checks how much space remains in the shadow-page cache. If fewer than 5 slots are free, it evicts pages until 25 slots are available. Choosing and removing those pages happens while `mmu_lock`, a [reader-writer lock](https://en.wikipedia.org/wiki/Readers%E2%80%93writer_lock), is held for writing. Time spent choosing victims therefore also delays work that needs conflicting access to the page tables.

KVM calls removing a shadow page _zapping_ it; the _eviction policy_ chooses which pages to zap when the cache fills. Evicting a shadow page removes its address mappings without deleting the guest memory they point to. When the guest next accesses that memory, a [page fault](https://en.wikipedia.org/wiki/Page_fault) exits to KVM, which has to rebuild the mapping.

### Why FIFO Can Discard a Hot Page

In _upstream_ KVM (the official version, without the changes described here), eviction happens in `kvm_mmu_zap_oldest_mmu_pages()`. The function starts at the tail of the list, where the oldest pages sit, and zaps one page after another until it has freed enough slots. The only pages it skips are _roots_, meaning pages at the top of a page table tree that a virtual CPU is currently using.

Starting from the tail makes this a _FIFO_ policy, based on when a page was created rather than when it was last used. Suppose KVM built one page long ago and another a minute ago. FIFO would still discard the older one first, even if the guest kept accessing it while the newer page had gone untouched since its creation.

In the example below, FIFO removes a page the guest still uses, causing another fault to rebuild its mappings:

![The active_mmu_pages list runs from the newest page at the head to the oldest at the tail. FIFO zaps the two oldest pages, cold G and in-use P. The guest's next access to P faults, and KVM rebuilds P at the head.](/images/approximate-lru-in-kvm/fifo-eviction.svg "Using a page never moves it in the list: $P$ stays near the tail however often the guest touches it.")

## Adapting CLOCK to KVM

### How CLOCK Approximates LRU

A _least recently used_ policy would instead discard the page that had gone unused longest. Maintaining that exact order would require recording each access or moving the accessed page within a list. Several virtual CPUs may use the tables concurrently, and most accesses do not reach KVM at all. We needed an approximation that could use the observations available to KVM without maintaining a new list order on every access.

In practice, operating systems approximate LRU with the _CLOCK_ algorithm (sometimes called _second chance_). Instead of an exact order, CLOCK keeps one bit per page and follows three rules:

1. Whenever a page is used, its _referenced_ bit is set to 1.
2. To evict, a pointer called the _clock hand_ sweeps around the pages as if they were arranged in a circle. A page whose bit is 1 has its bit cleared and is skipped (this is its second chance). The first page the hand finds with a 0 is evicted.
3. The hand stays where it stopped, and the next sweep continues from there.

This post calls one full trip of the hand around the circle a _lap_. A page in regular use has its bit set back to 1 before the hand comes around again, and survives. A page that nobody has touched since the hand's last visit still reads 0, and the hand evicts it.

The following example shows the hand zapping two pages:

![Pages A to H form a ring, and the clock hand sweeps from the tail. A, B and D have bit 1, so they are cleared and skipped. C and E have bit 0 and are zapped; the hand stops at F.](/images/approximate-lru-in-kvm/clock-sweep.svg "Eight pages shown, and none is used while the hand sweeps, so no bit is set back to 1 mid-sweep.")

The next figure replays one sequence of accesses under both policies. FIFO evicts pages the guest is about to use again, while CLOCK retains them. This is textbook CLOCK, where every use sets the reference bit. In our initial implementation, however, only accesses that fault into KVM can set the bit, limiting how closely it can reproduce that behavior:

![FIFO and CLOCK start with A B C D in four slots and replay A B E A B. On E, FIFO zaps A, and A and B refault: 3 faults. CLOCK spares A and B, zaps C, and ends with 1 fault.](/images/approximate-lru-in-kvm/fifo-vs-clock.svg "Each use sets the page's bit.")

### Implementing CLOCK

The implementation adds a bool (`lru_ref`) to `struct kvm_mmu_page` to serve as each page's referenced bit, and a pointer (`clock_hand`) to each VM. `mark_kvm_page_accessed()` is the helper that sets `lru_ref`; fault paths call it when they use a shadow page.

In the first version there were two callers. One, in `__kvm_mmu_get_shadow_page`, runs when KVM looks up or creates a shadow page while handling a fault. The other, in `fast_page_fault`, runs when the _fast path_ for page faults succeeds. That path is for faults where the shadow entry exists but has the wrong permission, e.g. a write to a page KVM made read-only deliberately, to detect changes. It patches the entry with a single atomic update and never takes `mmu_lock` (hence "lock-free").

The eviction loop below sweeps the clock hand instead of walking from the tail as FIFO does:

```c
unsigned long budget = 2 * kvm->arch.n_used_mmu_pages;

if (!kvm->arch.clock_hand)
	kvm->arch.clock_hand = list_last_entry(&kvm->arch.active_mmu_pages,
					       struct kvm_mmu_page, link);

while (budget-- && (sp = kvm->arch.clock_hand)) {
	kvm->arch.clock_hand = kvm_mmu_clock_next(kvm, sp);

	if (sp->root_count)
		continue;              /* never zap an active root */

	if (sp->lru_ref) {
		sp->lru_ref = false;   /* second chance */
		continue;
	}

	__kvm_mmu_prepare_zap_page(kvm, sp, invalid_list, &nr_zapped);
	total_zapped += nr_zapped;
	if (total_zapped >= nr_to_zap)
		break;
}
```

`kvm_mmu_clock_next` returns the page before `sp`, wrapping from the head back to the tail; that wrap is what makes the list a circle. `budget` limits the sweep to two laps (the next section explains why).

CLOCK also fits an existing constraint: other KVM code relies on `active_mmu_pages` remaining in creation order. Setting `lru_ref` leaves that order intact, while `list_move()` on each observed use would change it. The fault path can therefore mark a page with a single store, and the fast fault path needs no additional lock for list manipulation.

Retaining `clock_hand` between eviction calls lets each sweep resume where the previous one stopped. A page has a whole lap in which to be used and marked again before the hand returns. The `kvm.lru_mmu` module parameter lets us switch between FIFO and CLOCK at runtime, making it possible to benchmark both policies on the same kernel without rebuilding it.

## Keeping CLOCK Safe in the Kernel

The saved hand added state that had to remain valid whenever `arch/x86/kvm/mmu/mmu.c` changed `active_mmu_pages`. Reasoning about those interactions took longer than writing the CLOCK loop. We organized the implementation around three _invariants_: the hand must stay valid, a sweep must finish, and eviction must free a page when an eligible one exists.

### Invariant 1: Keep the Hand on a Live Page

The hand must point to a live page or be null. That requirement extends beyond the CLOCK loop. Shadow pages can be removed through several paths. Memslot changes trigger `kvm_arch_flush_shadow_memslot()`, while `kvm_mmu_zap_all()` removes every shadow page. Zapping a parent page can also remove its children. Any of these paths could free the page saved in the hand, leaving the next eviction to dereference freed memory (use-after-free) that can crash the kernel. Every zap passes through `__kvm_mmu_prepare_zap_page`, so we added a call there to `kvm_mmu_clock_hand_unlink`. This helper advances the hand if it points to the page being removed:

```c
/* in __kvm_mmu_prepare_zap_page(); invalid pages are already off the list */
if (!sp->role.invalid)
	kvm_mmu_clock_hand_unlink(kvm, sp);

/* in kvm_mmu_clock_hand_unlink() */
if (kvm->arch.clock_hand == sp) {
	next = kvm_mmu_clock_next(kvm, sp);
	kvm->arch.clock_hand = next == sp ? NULL : next;  /* NULL: sp was the only page */
}
```

Zapping a page can also remove its children, including the page the hand was about to visit. Upstream's FIFO loop handles these removals by returning to the tail and starting over (`goto restart`). For CLOCK, we advance the hand _before_ zapping the current page. If that zap removes the page the hand moved to, `kvm_mmu_clock_hand_unlink` advances it again. The loop can then continue without restarting, provided each iteration rereads `kvm->arch.clock_hand`; a local copy could still point at a child that was freed during the zap.

### Invariant 2: Bound Each Sweep

FIFO can stop at the head of the list, whereas CLOCK’s hand wraps around and could continue indefinitely. If all remaining pages were roots, none could be zapped, and an unbounded sweep would keep `mmu_lock` held for writing while other virtual CPUs waited for it. We use `budget` to stop after two laps even if the sweep hasn't freed enough pages. Assuming no bits are set again behind the hand, the first lap clears referenced bits and the second can zap the non-root pages.

### Invariant 3: Free an Eligible Page When One Exists

The two-lap argument assumes that nothing re-marks pages behind the hand, an assumption the lock-free fast fault path can violate by setting those bits at any time. In the worst case, every page is re-marked before the hand returns, the sweep frees nothing, and KVM fails the guest's page fault with `ENOSPC` (out of space), which stops the VM. When the sweep zaps nothing, the code now falls back to FIFO order, and a new counter, `lru_fifo_fallbacks`, records each fallback:

```c
total_zapped = kvm_mmu_clock_zap_pages(kvm, nr_to_zap,
				       &invalid_list, &aged);
if (total_zapped)
	goto commit;

/* Every non-root page was referenced again during the sweep. */
kvm->stat.lru_fifo_fallbacks++;
/* fall through to the FIFO loop */
```

### Marking While the Sweep Runs

Changes to the list and hand rely on the existing protection of `mmu_lock` held for writing, without requiring another lock. Reference marking remains lock-free and can race with the sweep clearing `lru_ref`. If the clear occurs last, a recent access can lose its second chance; if the set occurs last, the reference bit remains available for the next check. This uncertainty affects the choice of victim without invalidating the list. If repeated marking prevents CLOCK from freeing any page, the FIFO fallback from invariant 3 provides progress.

## Test Environments and Correctness Checks

### The Class Project’s Nested Setup

During the class project, getting access to a machine that could boot a custom kernel with hardware virtualization enabled was difficult. Our instructor arranged a bare-metal AWS instance, [**c5n.metal**](https://aws.amazon.com/ec2/instance-types/c5/), for our initial setup.

That setup was [nested](https://docs.kernel.org/virt/kvm/x86/running-nested-guests.html), with the modified KVM running inside a [QEMU](https://www.qemu.org/) VM:

![Three layers, bottom up. L0 is the AWS c5n.metal host, L1 is a QEMU guest running our modified KVM, and L2 is a nested guest running redis-server under redis-benchmark. L2's page faults are handled by L1's KVM MMU.](/images/approximate-lru-in-kvm/nested-setup.svg "L0, L1 and L2 are the usual names for the physical host, the guest hypervisor, and the nested guest.")

### Follow-Up on an Emulated x86 Machine

For the follow-up, I used an x86 machine emulated by QEMU on an Arm workstation.

QEMU’s [Tiny Code Generator](https://www.qemu.org/docs/master/devel/tcg.html) (TCG) translates x86 instructions into Arm instructions as the guest runs. It emulates an AMD x86 CPU and [Secure Virtual Machine](<https://en.wikipedia.org/wiki/X86_virtualization#AMD_virtualization_(AMD-V)>) (SVM), AMD’s hardware virtualization support. Inside that machine, the modified KVM runs nested guests:

![L0 is an Arm-based NVIDIA GB10 workstation, where QEMU emulates an x86 AMD EPYC CPU in software. L1 runs our modified KVM with nested paging off. L2 holds the selftest VMs and a Redis guest, whose faults fill L1's shadow MMU cache.](/images/approximate-lru-in-kvm/emulated-setup.svg "Layers L0 to L2 are numbered as in the c5n.metal setup above; compare the two stacks row by row.")

This setup used true shadow paging, with AMD’s nested page tables disabled (`kvm_amd.npt=0`). Its own [test programs](https://docs.kernel.org/dev-tools/kselftest.html) passed under CLOCK, under FIFO, and while switching policies with VMs running. We enabled [KASAN](https://docs.kernel.org/dev-tools/kasan.html) (Kernel Address Sanitizer) for memory errors, [lockdep](https://docs.kernel.org/locking/lockdep-design.html) for locking mistakes, and the [lockup detectors](https://docs.kernel.org/admin-guide/lockup-watchdogs.html) for hangs.

### Stressing Eviction During Memory Changes

I also added `lru_stress_test` to check eviction while the VM’s memory configuration was changing. Four virtual CPUs read and write guest pages as host threads change the memory layout, toggle dirty-page logging, and resize the cache between 64 and 1,024 pages. Each guest page holds a value derived from its address, which the guest checks on every read. If a stale translation directs a read to the wrong page, that value no longer matches and the test fails.

This test caught the `ENOSPC` failure from invariant 3. Before the FIFO fallback existed, one version of CLOCK stopped the VM 71 seconds into a run. With the fallback in place, every run passed under every policy.

The follow-up also records whether the reference marks affect eviction directly. The `lru_ref_skips` counter records each page spared because its referenced bit was set. If it stays at zero, none of the marks has caused the sweep to spare a page; we need to understand that before attributing a benchmark difference to CLOCK's reference tracking.

## Looking Back

This was my first time changing a real operating system kernel. The CLOCK loop itself was small; much of the class project went into finding hardware that could boot our modified kernel and building the nested setup. Continuing the work afterward gave me a way to investigate the policy more closely, especially whether our reference marks were giving frequently used pages second chances.

The kernel integration also changed how I approached the implementation. Reasoning about the eviction loop alone was not enough, because other paths could remove the page saved in the hand. Writing down the invariants and identifying the shared removal path made it possible to cover those cases without adding a separate lock.

We also tried extending CLOCK to the TDP MMU but did not complete that work. In the kernel version we used, it does not use the legacy MMU’s bounded cache and FIFO recycling loop, making this more than a replacement-policy change. Whether adding a similar memory limit and eviction policy would benefit the TDP MMU is a separate question we did not evaluate.

## Continuing in Part 2

The implementation gave us a way to give shadow pages second chances without changing the list’s creation order, while bounding each sweep and guaranteeing progress when an eligible page exists. Its usefulness still depended on whether KVM could observe enough accesses to distinguish hot pages from cold ones.

In [Part 2: Tracking Page Use](/approximate-lru-in-kvm-evaluation/), I investigate that problem on the emulated x86 machine. Marking pages while their entries were filled reduced faults by 61–81% compared with FIFO in the reuse test’s 64- and 128-page caches. Hardware accessed bits and the Redis workload gave more mixed results. Those measurements establish changes in counts, with timings on real hardware still left to do.
