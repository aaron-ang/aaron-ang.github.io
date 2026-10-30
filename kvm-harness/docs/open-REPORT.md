# KVM post "What's Still Open": follow-up results (2026-10-09)

All runs: QEMU TCG with emulated SVM on an aarch64 host, nested L1 running the modified 6.14-rc7 KVM.
Protocol and every amendment: PREREG.md.

## Kernel fixes found (applied to ~/projects/kvm, uncommitted)
1. **SVM EXITINTINFO valid bit** (`svm.c:4846`): the check tested the type bits, not `SVM_EXITINTINFO_VALID`.
   QEMU leaves stale type bits, so KVM refused to emulate (KVM_INTERNAL_ERROR suberror 3).
   Upstream fails the same way. Before: 5/15 pgbench runs died of it. After: 0/9 (p=0.026).
2. **CLOCK liveness**: `lru_ref` (set by lockless `fast_page_fault`) and the hardware Accessed bits are re-set by running vCPUs
   while the sweep holds `mmu_lock`. Under aging, every non-root page can look referenced on both passes.
   The sweep then zaps 0 pages, and `make_mmu_pages_available` returns -ENOSPC, which kills the VM.
   Repro: parent aging, pool 64, 4 vCPUs, dies at 71 s. Fix: fall back to FIFO when the sweep zaps nothing,
   counted in the new stat `lru_fifo_fallbacks`. Fallbacks per 120 s run: leaf ~230, parent ~280, toggle ~160, fifo/clock 0.

## Test 4: stress/correctness (KASAN, lockdep, DEBUG_LIST)
- 4 vCPUs with address-tagged pages, plus memslot churn, dirty logging, madvise, page_idle and pool resizing.
- Result: 15/15 PASS on the fixed kernel (one TCG fault, IRQ taken with IF=0, replaced). No KASAN or lockdep splat.

## Test 3: host reclaim with real swap (Q6)
- Setup: 64 MiB guest set, 1 MiB hot (16 regions, 90% of accesses). Memory is locked until ~108 MiB of it is swapped out.
- Pre-registered prediction (leaf loses more hot pages at pool 128): **not supported**.
  Means: fifo 0.176, clock 0.117, leaf 0.100, parent 0.110. All CIs span 0, n=5.
- Exploratory: small pool (128) vs big pool (2048), paired: **+11.8 points of hot pages swapped out** [+7.4, +16.2], 16/20 positive.
  Losses come in whole hot regions (~3 of 16). The big pool almost never loses a hot page.
  Reading: an evicted shadow page takes its SPTEs and their Accessed bits with it, so host reclaim no longer sees those guest pages as hot.
  The cost comes from eviction itself; the choice of policy did not move it detectably.

## Q4 (page_idle, from before): leaf aging misreports hot pages as idle
- 6.5% of hot pages at pool 128 (CI +3.3..+9.7) and 5.1% at 256. Other policies: 0.
- Under real reclaim (Test 3) this did not turn into more hot swap-outs.

## Test 2 / Q5: pgbench, realistic sizes: not runnable here
- No configuration both evicts shadow pages and completes under TCG.
- C=16 at 1G evicts nothing. C=100 stalls (L2 timekeeping NMI panic) even with the big pool, where no eviction happens.
- Needs real x86 hardware.

## Emulator / upstream noise (not our code)
- L1 "sleeping function called from invalid context" in `handle_fastpath_hlt` (QEMU gives next_rip=0): ~330 per run, upstream and ours alike.
- L2 double faults (kernel %gs with GS base 0).
- IRQs delivered with IF=0 (spinlock recursion, timekeeper deadlock).
