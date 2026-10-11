+++
title = "Extending KVM Page Eviction using Approximate LRU"
date = "2025-03-22"
slug = "approximate-lru-in-kvm"
tags = ["Engineering", "Research"]
+++

When you rent a virtual machine (VM) from a cloud provider, the operating system inside it behaves as if it owns the whole computer. In reality, a [_hypervisor_](https://en.wikipedia.org/wiki/Hypervisor) underneath splits the hardware across many VMs. On Linux, that hypervisor is usually [KVM](https://linux-kvm.org) (Kernel-based Virtual Machine), which is part of the kernel itself.

One of a hypervisor's jobs is translating its guests' memory addresses. When the CPU cannot perform that translation in hardware, KVM builds page tables of its own and keeps them in a cache. The cache has a size limit, and once it is full, KVM must choose which tables to discard.

For a class project at UCSD, Alex Asch, Eric Huang, Justin Kaufman and I changed how KVM makes that choice. Upstream KVM discards the oldest table first ([first-in-first-out](https://en.wikipedia.org/wiki/Page_replacement_algorithm#First-in,_first-out), or FIFO), and we replaced that rule with an approximation of ["least recently used"](https://en.wikipedia.org/wiki/Page_replacement_algorithm#Least_recently_used) (LRU). The code is on [GitHub](https://github.com/aaron-ang/kvm), based on [Linux 6.14-rc7](https://github.com/torvalds/linux/tree/v6.14-rc7).

The change only affects KVM's older translation path. Most cloud hosts now translate in hardware (Intel calls this EPT, and AMD calls it NPT), and the new code never runs on those hosts. KVM still uses the older path on CPUs without EPT or NPT, when an administrator loads KVM with `tdp_mmu=0`, and for some nested VMs (a VM inside a VM).

LRU needs to know which pages are in use, but KVM usually cannot tell. KVM learns of an access only when the hardware fails to translate the address on its own (a page fault). After KVM fills in a translation, the hardware keeps reusing it without notifying KVM. The first version marked a page as used only when KVM created it. As a result, its eviction order was almost identical to FIFO's.

After the class, I continued the work alone, using an x86 machine that QEMU emulates on an Arm workstation. In a new selftest, `lru_reuse_test`, the guest repeatedly returns to 16 hot 2 MiB regions. In that test, marking pages on more of the faults KVM already handles reduced page faults by 61–81% compared with FIFO. Adding the hardware's accessed bits helped in some configurations and hurt in others. An emulated CPU does not run at real speed, and these results count faults without measuring time. The measurements from the class project itself were too weak to show any effect.

The first half of this post explains how KVM translates VM memory with [virtual memory](https://en.wikipedia.org/wiki/Virtual_memory), and how the classic [CLOCK algorithm](https://en.wikipedia.org/wiki/Page_replacement_algorithm#Clock) approximates LRU cheaply. The second half covers the kernel changes, the measurements, and where those measurements went wrong.

## Shadow Paging

Programs never see physical memory directly. Every address a program uses is _virtual_, and the CPU turns it into a physical one by looking it up in [_page tables_](https://en.wikipedia.org/wiki/Page_table) that the OS maintains. Memory comes in 4 KiB _pages_; for each virtual page, the page tables say which physical page backs it. Strictly speaking, the page tables form a tree of tables with 512 entries each, and every entry points one level further down. The CPU walks it from the root to a _last-level_ entry holding the physical page number.

In a VM there are two of these lookups. The guest OS keeps its own page tables, mapping program addresses to what it thinks are physical addresses. Those are really offsets into the region of host memory the VM was given, and the hypervisor maps them again, to real hardware addresses. KVM keeps this second mapping in _memslots_; each memslot covers one range of guest memory and says where in host memory it lives.

Newer CPUs can walk both sets of tables in hardware. Intel's version is called [EPT](https://en.wikipedia.org/wiki/Second_Level_Address_Translation#Extended_Page_Tables) (Extended Page Tables), and inside KVM it is handled by the [_TDP MMU_](https://lwn.net/Articles/832835/) (two-dimensional paging [memory management unit](https://en.wikipedia.org/wiki/Memory_management_unit)). If the CPU lacks it, KVM falls back to [_shadow paging_](https://docs.kernel.org/virt/kvm/x86/mmu.html#shadow-pages), which is how it worked before EPT existed. With shadow paging, KVM builds page tables of its own, _shadow page tables_, that map guest virtual addresses straight to host physical addresses. The real CPU walks those instead of the guest's. Whenever the guest writes to its page tables, KVM traps the write and patches the shadow copy.

A shadow entry holds the end result of both lookups. KVM fills it in the first time the guest touches the page:

![Three tables: the guest page table, KVM's memslots, and the shadow page table the CPU walks. The filled entry 0x5000 takes one lookup. Touching unfilled 0x4000 faults, and KVM resolves it through both tables to 0x79000 and stores it.](/images/approximate-lru-in-kvm/two-level-translation.svg "Each page table tree is drawn as a flat list of mappings.")

The project changed the code that manages these tables, which KVM calls the [legacy MMU](https://docs.kernel.org/virt/kvm/x86/mmu.html). Confusingly, the legacy MMU can also manage EPT tables in place of the TDP MMU, and the class benchmarks ran in that mode.

## The Cache and Its Eviction Policy

One page of a shadow page table is a _shadow page_. For each one, KVM allocates a small `struct kvm_mmu_page` and puts it on a linked list called `active_mmu_pages`, with new pages going at the head.

KVM caps the number of shadow pages it keeps. Before it handles a fault, KVM checks that at least 5 slots are free. If they are not, it evicts shadow pages until 25 slots are free. For this whole step it holds `mmu_lock`, a [reader-writer lock](https://en.wikipedia.org/wiki/Readers%E2%80%93writer_lock), for writing, and no other virtual CPU can touch the page tables until it finishes.

In KVM's code, removing a shadow page is called _zapping_ it, and the _eviction policy_ is whatever decides which pages get zapped when the cache fills up. A zap loses no data, because the guest's own page tables still hold the correct mappings. The guest's next access to memory under that page instead finds no translation in hardware. The access raises a [page fault](https://en.wikipedia.org/wiki/Page_fault), the CPU exits to KVM, and KVM has to build the shadow page again from scratch. If the guest was actively using the page, all of that work was wasted.

### FIFO

In _upstream_ KVM (the official version, without the changes described here), eviction happens in `kvm_mmu_zap_oldest_mmu_pages()`. The function starts at the tail of the list, where the oldest pages sit, and zaps one page after another until it has freed enough slots. The only pages it skips are _roots_, meaning pages at the top of a page table tree that a virtual CPU is currently using.

Since it always starts from the tail, this policy is _FIFO_, and how recently a page was used plays no part in it. For example, if KVM built one page long ago and another a minute ago, FIFO zaps the older page first, even if the guest touches it constantly and has not touched the newer one since.

For many workloads this costs little, because their old pages tend to be cold anyway. FIFO becomes costly when the guest keeps returning to a page it created early on. In the figure below, FIFO zaps such a page, and the guest faults on it immediately:

![The active_mmu_pages list runs from the newest page at the head to the oldest at the tail. FIFO zaps the two oldest pages, cold G and in-use P. The guest's next access to P faults, and KVM rebuilds P at the head.](/images/approximate-lru-in-kvm/fifo-eviction.svg "Using a page never moves it in the list: $P$ stays near the tail however often the guest touches it.")

## Approximating LRU

The textbook fix is _LRU_, which evicts whichever page went unused for the longest time. Exact LRU has to order every page by last use: each access either moves the page to the front of a list or records a timestamp. Several virtual CPUs access the page tables constantly and concurrently, and that much bookkeeping would cost more than the evictions save.

In practice, operating systems approximate LRU with the _CLOCK_ algorithm (sometimes called _second chance_). Instead of an exact order, CLOCK keeps one bit per page and follows three rules:

- Whenever a page is used, its _referenced_ bit is set to 1.
- To evict, a pointer called the _clock hand_ sweeps around the pages as if they were arranged in a circle. A page whose bit is 1 has its bit cleared and is skipped (this is its second chance). The first page the hand finds with a 0 is evicted.
- The hand stays where it stopped, and the next sweep continues from there.

This post calls one full trip of the hand around the circle a _lap_. A page in regular use has its bit set back to 1 before the hand comes around again, and survives. A page that nobody has touched since the hand's last visit still reads 0, and the hand evicts it.

The following example shows the hand zapping two pages:

![Pages A to H form a ring, and the clock hand sweeps from the tail. A, B and D have bit 1, so they are cleared and skipped. C and E have bit 0 and are zapped; the hand stops at F.](/images/approximate-lru-in-kvm/clock-sweep.svg "Eight pages shown, and none is used while the hand sweeps, so no bit is set back to 1 mid-sweep.")

The next figure replays one sequence of accesses under both policies. FIFO ends up evicting pages the guest is about to use again; CLOCK keeps them. That figure is textbook CLOCK, though, where every use sets the bit. The modified kernel only sets the bit when a use faults into KVM, and most uses do not. The results section shows how much this matters:

![FIFO and CLOCK start with A B C D in four slots and replay A B E A B. On E, FIFO zaps A, and A and B refault: 3 faults. CLOCK spares A and B, zaps C, and ends with 1 fault.](/images/approximate-lru-in-kvm/fifo-vs-clock.svg "Each use sets the page's bit, and the counters count page faults.")

### Our implementation

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

Pages never move in `active_mmu_pages`. An LRU list would `list_move()` a page to the head on every use. Here a use is one store to `lru_ref`, with no list manipulation and no lock on the fast path. Because pages stay in place, the list also keeps its creation order, which other KVM code relies on. `clock_hand` also survives between eviction calls. Each call resumes where the last one stopped, which gives every page a whole lap to get used again before the hand comes back. FIFO remains available through the `kvm.lru_mmu` module parameter, which switches policies at runtime without a rebuild.

## Making CLOCK safe in the Kernel

The eviction loop above is the entire algorithm. What took longer was everything else in `arch/x86/kvm/mmu/mmu.c` that edits `active_mmu_pages` while the hand points into it. To keep track, we wrote down three _invariants_, rules that must hold at all times.

**Invariant 1: the hand points at a live page or at nothing.** The CLOCK loop is not the only code that zaps shadow pages. A memslot change zaps them through `kvm_arch_flush_shadow_memslot()`, `kvm_mmu_zap_all()` drops all of them at once, and zapping a parent also zaps its children. If one of those paths frees the page the hand points at, the next eviction starts from freed memory. In the kernel, this use-after-free causes random crashes. Fortunately, every zap passes through `__kvm_mmu_prepare_zap_page`. The fix adds a call there to a new helper, `kvm_mmu_clock_hand_unlink`, which moves the hand to the next page if it points at the one being zapped:

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

**Invariant 2: every sweep ends.** FIFO stops when it reaches the head of the list. A sweep around a circle has no natural end. If every page left were a root, a naive sweep would spin forever with `mmu_lock` held for writing, and every virtual CPU waiting on the lock would hang. `budget` stops the loop after two laps, whether it has zapped enough or not. Two laps suffice if nothing sets bits behind the hand: lap one clears every referenced bit, and lap two zaps any page that is not a root.

**Invariant 3: an eviction call zaps a page whenever one can be zapped.** That "nothing sets bits behind the hand" assumption is false, since the lock-free fast path can set them at any time. In the worst case, every page is re-marked before the hand returns, the sweep frees nothing, and KVM fails the guest's page fault with `ENOSPC` (out of space), which stops the VM. When the sweep zaps nothing, the code now falls back to FIFO order, and a new counter, `lru_fifo_fallbacks`, records each fallback:

```c
total_zapped = kvm_mmu_clock_zap_pages(kvm, nr_to_zap,
				       &invalid_list, &aged);
if (total_zapped)
	goto commit;

/* Every non-root page was referenced again during the sweep. */
kvm->stat.lru_fifo_fallbacks++;
/* fall through to the FIFO loop */
```

### Changes while the sweep runs

One more case remains: zapping a page can zap its children too, and the hand may have just moved onto one of them. Upstream's FIFO loop deals with this by going back to the tail and starting over (`goto restart`). The CLOCK loop does not restart. Its hand moves _before_ the current page is zapped. If the page it moved to is zapped as a child, `kvm_mmu_clock_hand_unlink` (invariant 1) moves the hand again. Because the hand can move during a zap, each iteration rereads `kvm->arch.clock_hand` instead of keeping a local copy.

The change adds no locks. Every function that edits the list or moves the hand already holds `mmu_lock` for writing, which serializes them. The lock-free fast path is the one exception: it writes `lru_ref` without the lock and can race a sweep that is clearing the same bit. Neither order breaks anything. When the clear lands last, the page loses its second chance and may be evicted early, costing one extra fault. When the set lands last, the page keeps its bit. If that happened to every page at once, the sweep would zap nothing and fall back to FIFO (invariant 3).

## How We Tested It

Modifying the kernel's hypervisor needs a machine where you can boot custom kernels with hardware virtualization enabled. Getting access to one was difficult. Only one team member had an x86 laptop that could do it, and the benchmarks ran on a bare-metal AWS instance, [**c5n.metal**](https://aws.amazon.com/ec2/instance-types/c5/), that the course instructor arranged.

The test setup was [nested](https://docs.kernel.org/virt/kvm/x86/running-nested-guests.html), with the modified KVM running inside a [QEMU](https://www.qemu.org/) VM:

![Three layers, bottom up. L0 is the AWS c5n.metal host, L1 is a QEMU guest running our modified KVM, and L2 is a nested guest running redis-server under redis-benchmark. L2's page faults are handled by L1's KVM MMU.](/images/approximate-lru-in-kvm/nested-setup.svg "L0, L1 and L2 are the usual names for the physical host, the guest hypervisor, and the nested guest.")

Every run used three kernel parameters:

- **`tdp_mmu=0`** turns the TDP MMU off, which puts KVM on the legacy MMU where the CLOCK code lives.
- **`min_alloc_pages=32`** is a parameter the project added. It pins the cache at 32 pages.
- **`lru_mmu`** picks the policy: 0 for FIFO, 1 for CLOCK.

By default KVM sizes the cache from the VM's memory, and at that size none of the test workloads evicted anything. Forcing it down to 32 pages, below KVM's own floor of 64, kept both policies evicting continuously. This is a stress setting, far from any realistic configuration.

These runs had one limitation: EPT was still enabled. With EPT on and only the TDP MMU off, the legacy MMU builds EPT tables directly and shadows none of the guest's page tables. It still tracks each EPT table page as a `struct kvm_mmu_page` on `active_mmu_pages`, and the eviction code is the same in both modes. The eviction policy was still exercised, but not under real shadow paging.

KVM's own [test programs](https://docs.kernel.org/dev-tools/kselftest.html) also ran in an emulated x86 machine to test correctness under true shadow paging. In that machine, AMD's nested page tables, NPT, were turned off (`kvm_amd.npt=0`), and every guest page table was really shadowed. The suite passed, with the kernel's debugging checks turned on. [KASAN](https://docs.kernel.org/dev-tools/kasan.html) (Kernel Address Sanitizer) catches memory errors, [lockdep](https://docs.kernel.org/locking/lockdep-design.html) catches locking mistakes, and the [lockup detectors](https://docs.kernel.org/admin-guide/lockup-watchdogs.html) catch hangs. It passed under CLOCK, under FIFO, and while the policy was switched repeatedly with VMs running.

The emulated machine needs no x86 hardware and runs on an Arm workstation, where QEMU's [Tiny Code Generator](https://www.qemu.org/docs/master/devel/tcg.html) (TCG) translates x86 instructions into Arm instructions as the guest runs. QEMU emulates an AMD x86 CPU in software, along with [Secure Virtual Machine](https://en.wikipedia.org/wiki/X86_virtualization#AMD_virtualization_(AMD-V)) (SVM), AMD's hardware support for virtualization:

![L0 is an Arm-based NVIDIA GB10 workstation, where QEMU emulates an x86 AMD EPYC CPU in software. L1 runs our modified KVM with nested paging off. L2 holds the selftest VMs and a Redis guest, whose faults fill L1's shadow MMU cache.](/images/approximate-lru-in-kvm/emulated-setup.svg "Layers L0 to L2 are numbered as in the c5n.metal setup above; compare the two stacks row by row.")

A later stress test, `lru_stress_test`, comes from the solo follow-up after the class. It runs four virtual CPUs that read and write guest pages. Meanwhile, host threads keep changing the VM's memory layout, turning dirty-page logging on and off, and resizing the cache anywhere from 64 to 1,024 pages. The guest writes a value derived from each page's address into that page and checks it on every read. A read through a stale translation lands on the wrong page and fails the test.

This test caught the `ENOSPC` failure from invariant 3. Before the FIFO fallback existed, one version of CLOCK stopped the VM 71 seconds into a run (that version also reads the hardware accessed bits, which the results section covers). With the fallback in place, every run passed under every policy.

## Results

In the emulated machine, KVM's own counters give exact numbers of page faults and evictions.

### The reuse test

None of the class benchmarks tested reuse, where a small set of pages is touched repeatedly. A selftest written after the class, `lru_reuse_test`, covers that case under true shadow paging (`npt=0`, `tdp_mmu=0`). Its guest touches 256 regions of 2 MiB each. Each last-level shadow page has 512 entries that map 4 KiB each, enough for exactly one 2 MiB region.

Sixteen of the regions are hot and get 90% of the accesses. The other 10% go to cold regions, in one of two patterns: either at random (the _random_ workload), or in order, like a background scan (the _scan_ workload). The whole access sequence comes from a seed, and every policy replays exactly the same trace.

The original CLOCK, which marks a page only when KVM creates it, was barely better than FIFO. At 32 pages its counters were identical to FIFO's on every seed, and bigger caches took at most 22% fewer faults.

The problem is where `lru_ref` is set. Once a parent entry points at a shadow page, the hardware walks through it on every access without notifying KVM. KVM re-marks the page, in `__kvm_mmu_get_shadow_page`, only if that parent link is torn down and rebuilt. Hot and cold pages get re-marked about equally rarely, and once a lap has cleared every bit, CLOCK zaps in FIFO order again.

### Two ways to spot hot pages

#### Marking on fill

A fresh last-level shadow page is mostly empty. It has 512 slots, one per 4 KiB page of its 2 MiB region, and the guest's first touch of each page faults to fill its slot. Every one of those faults already goes through `mmu_set_spte()`, and marking the page there is one call:

```c
/* in mmu_set_spte(), when a fault fills an entry of sp */
if (!prefetch)
	mark_kvm_page_accessed(sp);
```

When all 512 slots are full, the faults stop and the marking stops with them. Marking on fill helps most while hot pages are filling up, or filling again after a zap. Cold regions get few touches and keep their bits at 0. The figure below follows one hot page and one cold page, first with marking on creation only and then with marking on fill:

![Hot page H and cold page C each map one 2 MiB region. In panel (a), both bits are 0 when the hand returns, and it zaps the older H. In panel (b), a fill re-marks H first, so C is zapped.](/images/approximate-lru-in-kvm/ref-signals.svg "Only two of the cache's shadow pages are shown, and the clock hand's trips around the rest of the ring are skipped.")

#### Hardware accessed bits

Each time the CPU uses an entry in a shadow page table, it sets that entry's _accessed_ bit, even on hits that KVM never sees. A new module parameter, `lru_age`, makes the sweep look at these bits as well. A set bit is treated like `lru_ref`: the sweep clears it and skips the page. Below are the same hot and cold pages, both with `lru_ref` cleared. A hit on $H$ still never reaches KVM, but now it leaves an accessed bit behind for the sweep to find.

![H and C both have lru_ref 0, and each entry now carries an accessed bit. A hit on H sets one without a fault. The clock hand finds it set, clears it and keeps H; C has none set and is zapped.](/images/approximate-lru-in-kvm/ref-abits.svg "Compare panel (a) of the previous figure: the same two pages with `lru_ref` at 0, where the hand zapped $H$ instead.")

`lru_age` has two modes, which differ in which entries the sweep reads:

- **Leaf:** all 512 of the page's own entries.
- **Parent:** the one entry a level up that points to the page.

The CPU keeps setting these bits while a sweep is running, which is one more reason for the FIFO fallback from invariant 3. With either mode, a two-minute `lru_stress_test` run fell back to FIFO a few hundred times; plain CLOCK never did.

There is one more difference between the modes. Leaf mode reads the same bits as _host reclaim_, the host kernel's search for guest memory it can take back. Parent mode reads none of them:

![One parent entry points to shadow page sp, which holds 512 leaf entries. Leaf mode (lru_age=1) reads and clears all 512 leaf accessed bits, the same bits host reclaim ages. Parent mode (lru_age=2) reads only the parent entry.](/images/approximate-lru-in-kvm/leaf-vs-parent.svg "`kvm_age_gfn` and `kvm_test_age_gfn` are the KVM functions that host reclaim calls to read and age the leaf bits.")

### What the better signals changed

Here is how each version of CLOCK changed the fault count relative to FIFO, at three cache sizes. Seeds were added in batches fixed in advance until every comparison's confidence interval excluded zero. Because the intervals were checked after each batch, the true intervals are a little wider than shown. From left to right, the columns are the original marking (on creation only), marking on fill as well, and marking on fill plus leaf or parent accessed bits:

{{< table title="Change in page faults vs FIFO in lru_reuse_test" caption="Best result per row in bold. Each row is the mean over 10 to 25 seeds of 200,000 accesses, with every policy run on the same seeds. Every nonzero change lies outside the 95% confidence interval of zero, and 0% means an exact tie. Counted by KVM under shadow paging on an emulated x86 CPU." >}}
| Workload | Cache pages | FIFO faults | on creation | on fill | fill + leaf bits | fill + parent bits |
|---|--:|--:|--:|--:|--:|--:|
| random | 32 | 212,865 | 0% | 0% | −4% | **−5%** |
| random | 64 | 175,209 | −10% | −61% | −56% | **−82%** |
| random | 128 | 118,952 | +3% | −65% | **−74%** | −61% |
| scan | 32 | 213,146 | 0% | 0% | −4% | **−5%** |
| scan | 64 | 179,096 | −11% | −81% | **−83%** | −75% |
| scan | 128 | 143,016 | −22% | −81% | −79% | **−83%** |
{{< /table >}}

In the two larger caches, marking pages on fill cut page faults by 61–81%, which accounts for most of the improvement. CLOCK now keeps the hot pages, and evicting a hot page costs the most faults because the guest faults again on every 4 KiB page it touches in that 2 MiB region.

In the 32-page cache, marking on fill had no effect. Each eviction call zaps about 20 of the 32 pages, enough to restore 25 free slots. By the next call, nearly every page still in the cache has faulted in new entries and been marked again. Only the accessed bits reduced faults, by 4–5%. The figure below shows how one call at that size spends its first lap clearing bits and its second zapping in FIFO order:

![Eight pages of the 32-page cache, all with bit 1, drawn from head to tail. In one eviction call, the hand's first lap clears every bit and zaps nothing; its second lap zaps from the tail, oldest page first.](/images/approximate-lru-in-kvm/small-cache.svg "The two-lap bound we set earlier to keep CLOCK safe lets one call clear every bit and still zap pages.")

Adding accessed bits on top of marking on fill gave mixed results. Based on fault counts alone, I would not make either mode the default. Each mode was best in some rows and lost to plain CLOCK in others, parent mode by as much as 28% and leaf mode by as much as 16%.

### Redis under shadow paging

A second benchmark ran [Redis](https://redis.io/) in a nested guest under true shadow paging, with [`redis-benchmark`](https://redis.io/docs/latest/operate/oss_and_stack/management/optimization/benchmarks/) running SET, GET, LPUSH, and LPOP inside the same guest. The whole workload needed a little over 128 shadow pages.

Because Redis results vary from run to run, each policy ran 16 times per cache size, in shuffled order to spread the host's load evenly across policies.

The table below counts evicted shadow pages under FIFO and the CLOCK versions from the reuse table:

{{< table title="Redis in a nested guest: shadow pages evicted, by policy" caption="Median of mmu_recycled, KVM's count of evicted shadow pages, over 16 runs per policy (8 at KVM's default size), 20,000 requests per test. Percentages are the median change relative to FIFO. At 80 and 96 pages, every percentage is outside its 95% confidence interval of zero except plain CLOCK's −6% at 80 pages. At 128 pages each run evicted only a few dozen pages, too few to rank the policies." >}}
| Cache pages | FIFO | on fill | fill + leaf bits | fill + parent bits |
|---|---|---|---|---|
| 80 | 1,251 | 1,176 (−6%) | 982 (−22%) | 908 (−27%) |
| 96 | 360 | 282 (−22%) | 254 (−29%) | 254 (−29%) |
| 128 | 63 | 42 | 47 | 42 |
| KVM's default | 0 | 0 | 0 | 0 |
{{< /table >}}

In a 96-page cache, every version of CLOCK evicted fewer pages than FIFO. At 80 pages, marking on fill by itself did not separate from FIFO, while both accessed-bit modes still evicted 22–27% fewer pages. Leaf and parent mode never separated from each other.

Page faults changed little: 2–6% fewer under CLOCK at 80 and 96 pages. At KVM's default size, with zero evictions, a run still took about 35,000 faults. I suspect most of Redis's faults are first touches, and evictions add only a small share.

Nine of the 233 Redis runs did not finish and were replaced with extra runs of the same policy. Eight failed under heavy host load, across all four policies. Repeated test boots point to QEMU's multi-threaded emulation, which failed as often under an unmodified upstream kernel as under the modified one. The ninth run hit a pair of upstream bugs outside the eviction code.[^svm] To rule out the new eviction code as the cause, the emulated machine was booted 20 times under each of five kernels, upstream included. Every boot passed KVM's MMU stress test, though 20 passing boots per kernel still leave room for a failure rate of up to 17%.

### Leaf mode and host reclaim

When the host runs low on memory, host reclaim swaps out guest pages that the guest has not used lately. It judges recency from the leaf accessed bits that leaf mode clears, and hot guest pages could then appear idle and be swapped out. Two selftests check this, reusing the hot and cold regions from `lru_reuse_test`.

The first test uses the kernel's [idle-page tracking](https://docs.kernel.org/admin-guide/mm/idle_page_tracking.html), which queries KVM as host reclaim does but swaps nothing. In a 128-page cache, leaf mode reported about 6% of hot pages as idle after the guest had used them. FIFO, plain CLOCK, and parent mode reported none.

The second test forces host reclaim to swap a fixed amount of guest memory to disk and records which guest pages are gone. I predicted that leaf mode would lose more hot pages than the other policies in a 128-page cache, but the results did not bear this out. Leaf mode lost 10% of its hot pages and FIFO lost 18%, and across five seeds no gap between policies was bigger than the spread between seeds.

Cache size had a larger effect than the policy. Hot pages were rarely swapped out from a 2,048-page cache, which never evicted. In the 128-page cache, every policy lost about 12 percentage points more hot pages. That comparison was not planned in advance, which makes it weaker evidence than the planned comparison between policies.

My best explanation is that eviction itself causes the small cache's losses. KVM only reports a guest page as recently used while some shadow entry maps it with the accessed bit set. When the cache evicts a last-level shadow page, the bits for its entire 2 MiB region are lost, and host reclaim sees every hot page in that region as unused, whichever policy picked the victim. The figure below walks through this with one shadow page:

![One last-level shadow page maps the 4 KiB guest pages of one 2 MiB region, and the CPU has set the accessed bits of the hot pages' entries. The eviction policy zaps the page, and its accessed bits go with it. Host reclaim then finds no shadow entries, treats every page as unused, and swaps out pages, hot ones included.](/images/approximate-lru-in-kvm/eviction-drops-abits.svg "Only 16 of the shadow page's 512 entries are drawn, and which pages host reclaim swaps out is illustrative.")

### The class project's measurements, on c5n.metal

On a later rereading, the class numbers showed much less than we had believed. The Redis runs used a build with every call that sets `lru_ref` commented out, which made "CLOCK" FIFO with a rotating start point. It is unclear which build produced the other measurements, and every benchmark ran once, with EPT on.

Under [stress-ng](https://github.com/ColinIanKing/stress-ng), CLOCK caused 4.6% fewer page faults than FIFO. The drop is probably noise, since CLOCK with the original marking matched FIFO exactly at the same 32-page size in the reuse test. In single [`perf`](https://perfwiki.github.io/main/) profiles, page-fault handling took 51% of CPU samples under CLOCK and 61% under FIFO, a gap that one profile per policy cannot separate from noise. The class Redis runs showed GET 48% faster under "CLOCK", while PING, which never touches the eviction code, varied from 36% slower to 37% faster. With marking off, both builds ran nearly the same code, and those differences are noise.

## Tradeoffs and Limits

- **Cheap but coarse marking.** A page used once since the last sweep and a page used a thousand times both end up with `lru_ref` set to 1. In exchange, marking costs the fault path a single store.
- **KVM's own marks miss most uses.** `lru_ref` only changes when KVM's fault handling touches the page. Marking on fill (on top of on creation) was enough to separate hot from cold in the reuse test, except at 32 pages. Accessed bits also see the hits that never fault.
- **Hit visibility vs. sweep cost.** Accessed bits are read during the sweep, with `mmu_lock` held for writing. For each page checked, parent mode adds about one read and leaf mode adds 512.
- **Bits set mid-sweep vs. guaranteed progress.** While the sweep clears accessed bits, running virtual CPUs set them again. In the worst case every page looks recently used, and only the FIFO fallback guarantees that an eviction call frees a page.
- **A narrow use case.** The legacy MMU is only in play with the TDP MMU off, on hardware without EPT (or AMD's NPT), or when KVM shadows a guest hypervisor's EPT tables for a nested VM. On a modern host running normal VMs, this code is effectively idle.

We also tried to bring CLOCK eviction to the TDP MMU, but could not get it working in time. Unlike the legacy MMU, the TDP MMU has no cache limit and never evicts pages to make room. It would first need an eviction path of its own.

## Looking Back

The project was my first time changing a real operating system kernel. Writing CLOCK took a few lines of code. The remaining time went to finding a machine that would boot the modified kernel and building the nested setup on it. We also had to make sure the benchmarks really ran the new code, and in that we failed: the Redis build had every `mark_kvm_page_accessed()` call commented out, and nothing in the results hinted at it. A counter added afterwards, `lru_ref_skips`, counts pages the sweep spared because their referenced bit was set. It reads zero with marking off. One run with it would have caught the mistake.

The second lesson was about shared data: whatever kernel structure you change, other code touches it too. In this project that structure was `active_mmu_pages`. Reasoning about the eviction loop alone was easy. Memslot changes, `kvm_mmu_zap_all()`, and parents zapping their children were harder, since any of them could leave the saved hand on freed memory. In the end we wrote down two facts and built most of the design on them: `mmu_lock` guards the list, and every zap passes through `__kvm_mmu_prepare_zap_page`.

## What's Still Open

- **Measure true shadow paging on real hardware**, with EPT off and timings. The emulator gave exact fault and eviction counts but no timings, not even for the accessed-bit scan. I would start with the Redis benchmark again, this time with the fixed code and repeated, pinned runs.
- **Pass the accessed bit on at eviction.** A zapped shadow page takes its accessed bits with it, which is the explanation proposed earlier for the small cache losing hot pages in the host-reclaim test. Before zapping, KVM could copy each set bit into the host's own record for that page. That costs up to 512 extra reads per eviction, under the same lock as the sweep.
- **Realistic cache sizes and applications.** Caches of 32 to 128 pages are still stress settings. With KVM's default size, Redis evicted nothing. The next step is applications whose working sets are larger than the default cache. In the emulated machine, the PostgreSQL benchmark [pgbench](https://www.postgresql.org/docs/current/pgbench.html) either evicted nothing or hung the nested guest. It hung even with a cache too large to evict from, which leaves this measurement to real hardware as well.

[^svm]: QEMU's emulated AMD CPU clears only the valid bit of the field that reports interrupted event delivery, and stale data stays behind. KVM's AMD code reads that field without checking the valid bit and decides that an ordinary write to a guest page table cannot be emulated. As of writing, neither bug is fixed upstream. In later database-benchmark runs on the same machine, a one-line KVM change that tests the valid bit made these errors go away.

