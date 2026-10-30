# open3 report: rerun of the accessed-bit results on the three-lap sweep

Draft, 2026-10-22. Covers every finished open3 test. Test D (Redis) is pending. The design and every rule change are in
open3/PREREG.md; this report cites the file behind each number. Unless stated otherwise, "single-thread" means
QEMU 8.2.2 `-accel tcg,thread=single`. "MTTCG" means multi-threaded TCG (`thread=multi`, or plain `-accel tcg`, which
PREREG's Amendment 6 revision records as MTTCG for an x86 guest on this host).

Kernels: bzImage-camp-3l (KASAN + lockdep) and bzImage-camp-3l-nokasan, both with the three-lap budget
`(lru_age ? 3 : 2) * n_used_mmu_pages` (PREREG "Code change"). Control kernel: bzImage-upstream-svmfix (6.14-rc7 + the
SVM vectoring fix; no LRU code).

## Summary

| Test | Mode | Boots | Outcome | Source |
|---|---|---|---|---|
| U3 reuse table | MTTCG, 1 vCPU (deviation) | 165 (825 tests) | 825/825 PASS; determinism 0 differ; fallbacks 0 | u3.analysis.txt, manifest_v.csv |
| U3 spot check | single-thread | 6 (30 tests) | 30/30 PASS, pf_taken equal to U3 in 30/30 | manifest_u3spot.csv |
| R4 swap test | MTTCG, 4 vCPUs (deviation) | 325 + 2 reruns | 320 + 5 controls fails=0 after 2 OOM reruns | analysis_t7_final.txt, logs/t7-* |
| R4 spot check | single-thread | 5 | 5/5 controls equal t7ctl exactly | logs/r4spot-*, logs/t7ctl-* |
| I idle tracking | single-thread | 40 (80 runs) | 40/40 fails=0; no pfn/vmstat exclusions | i3.analysis.txt, i.run |
| S1 stress | MTTCG | 15 | 14 PASS, 1 FAIL (s1-toggle-s3, class M+L) | classify_a8 s1-* |
| S2 stress, fallback off | MTTCG | 9 | 9/9 PASS, no ENOSPC, fallbacks 0 | classify_a8 s2-*, logs |
| S3 suites (phase 1) | MTTCG | 2 | 2/2 class L (hard lockup in demand_paging_test) | s.run, classify_a8 |
| X5 | MTTCG | 9 started, stopped | 4 PASS, 1 FAIL (class M), 4 killed unread | x5a/, PREREG Amendment 6 |
| X6 | single-thread | 9, stopped | harness artifact; counted neither pass nor fail | x6a/, PREREG Amendment 7 |
| X6b stress | single-thread | 36 | 35/36 PASS; 1 FAIL before any VM existed | classify_a8 x6b-* |
| x6b-toggle-s10r | single-thread | 1 | PASS | classify_a8, x6log/x6b-toggle-s10r.run |
| S3s suites | single-thread | 4 | 4/4 PASS (12/12 tests each) | classify_a8 s3s-* |
| S3m controls (8.2.2) | MTTCG | 8 | 8/8 failed | classify_a8 s3m-*, PREREG RESULTS |
| S3q (QEMU 11.1.2) | MTTCG | 4 | 4/4 failed | classify_a8 q11-*, PREREG RESULTS |
| Redis smoke + Diagnostic A | single-thread | 8 | 8/8 hung in L2 boot; 7 IF-clear freezes, 2 of them upstream | dsmoke_diag/, diag/ |
| D Redis | MTTCG (Amendment 11 Part B) | 225 runs (224 + 1 rule-(b) replacement) | 224 END; 1 STALL-I (d1-p96-parent-r4); no halts; Fisher p = 1 | manifest_d1.csv, d1an/analysis.txt |

No selftest boot under single-thread TCG failed after a VM existed. Those boots ran selftest guests, which do not cover
a full Linux L2 taking timer interrupts: every single-thread Redis run hung during L2 boot (Diagnostic A below). Every
set of MTTCG selftest-suite boots in this campaign had failures, including the boots of the unpatched upstream kernel.

## Test U3: reuse table

Source: open3/u3.analysis.txt (analyze_u3.py), open3/manifest_v.csv.

- 165 boots, 825 tests (5 policies per boot), all PASS.
- Determinism check: fifo, clock_create and clock_fill pf_taken equal Test U for every seed in every cell (18 rows, 0 seeds
  differ).
- U3a: lru_fifo_fallbacks = 0 in every run of every policy and cell (mean 0.00, max 0).
- U3b (% change in faults vs FIFO, mean of per-seed ratios, 95% t-CI):

| wl | cache | n | FIFO faults | clock_create | clock_fill | leaf | parent |
|---|---|---|---|---|---|---|---|
| hot | 32 | 20 | 212,816 | +0.0 | +0.0 | +0.0 | +0.0 |
| hot | 64 | 20 | 175,134 | -8.5 [-10.4, -6.5] | -61.2 [-61.7, -60.7] | -61.7 [-62.2, -61.3] | -80.8 [-81.7, -79.9] |
| hot | 128 | 35 | 119,119 | +2.7 [+2.5, +3.0] (worse, small) | -64.9 [-65.2, -64.6] | -74.0 [-74.9, -73.2] | -61.8 [-63.7, -59.9] |
| scan | 32 | 20 | 213,123 | +0.0 | +0.0 | +0.0 | +0.0 |
| scan | 64 | 35 | 179,136 | -10.4 [-11.8, -9.1] | -80.5 [-81.3, -79.6] | -80.5 [-81.3, -79.6] | -80.5 [-81.3, -79.6] |
| scan | 128 | 35 | 143,058 | -22.4 [-22.5, -22.4] | -80.7 [-81.5, -79.9] | -80.7 [-81.5, -79.9] | -80.7 [-81.5, -79.9] |

- U3c (accessed-bit modes vs install marking; full table in u3.analysis.txt):
  - hot/64: leaf vs clock_fill -1.3% [-2.6, -0.1] (better, small; 75% of seeds); parent vs clock_fill -50.5%
    [-53.1, -47.8] (better, 100%).
  - hot/128: leaf vs clock_fill -26.0% [-28.6, -23.3] (better, 100%); parent vs clock_fill +9.0% [+3.3, +14.6]
    (worse, 29% of seeds better); parent vs leaf +48.2% [+39.9, +56.5] (worse).
  - Every scan cell and the 32-page cells: no difference.
- REPORT-only comparison with the two-lap kernel (same seed, % of two-lap pf_taken): at 32 pages leaf and parent are
  +3.6% to +3.8% higher in every seed (min +3.53, max +3.97), so the three-lap kernel faults slightly more there. The
  other cells range widely in both directions (u3.analysis.txt, last section). The posts do not use this comparison.

## Test R4: swap test

Source: open3/analysis_t7_final.txt (analyze_t7.py), logs/t7-*, open3/t7.run, open3/t7rerun.run.

- 325 boots (5 arms x 64 seeds + 5 -M 0 controls). t7-fifo128-s117 and t7-fifo128-s151 failed (L1 OOM killer killed
  lru_reclaim_test, fails=1; original logs open3/t7-fifo128-s{117,151}.oom.log). Both were rerun once with the same seed
  under the pre-registered exclusion rule, and both reruns passed. Final set: 320 runs + 5 controls, all
  "ALL DONE fails=0"; n = 64 per arm, 0 excluded.
- Controls: all five -M 0 controls have out_hot = out_cold = pswpout = 0. t7ctl equals open2's t6ctl exactly for fifo128,
  clock128 and fifo2048, so the R' variant results keep current-code status (PREREG 01:26).

| arm | n | H % | H_present % | H_absent % | zero-loss runs | hot_zaps | age_sptes | fallbacks |
|---|---|---|---|---|---|---|---|---|
| fifo128 | 64 | 21.9 | 21.8 | 22.7 | 9 | 452 | 0 | 0 |
| clock128 | 64 | 13.0 | 13.4 | 7.1 | 14 | 162 | 0 | 0 |
| leaf128 | 64 | 9.6 | 9.6 | 19.8 | 17 | 12 | 3,382,272 | 0 |
| parent128 | 64 | 12.0 | 12.2 | 7.1 | 16 | 101 | 5,609 | 0 |
| fifo2048 | 64 | 1.9 | 1.9 | n/a | 57 | 0 | 0 | 0 |

- R4-net (leaf128 minus each policy, paired by seed, 95% bootstrap CI, predicted > 0):
  - leaf128 - fifo128: -12.4 points [-15.9, -8.7], Holm-adjusted Wilcoxon p = 1.29e-06. The CI excludes 0 in the
    opposite direction: leaf loses fewer hot pages than FIFO.
  - leaf128 - clock128: -3.4 [-7.0, +0.1], Holm p = 0.117.
  - leaf128 - parent128: -2.4 [-6.2, +1.5], Holm p = 0.392.
  - The three verdicts read "no effect >= 5 pts", with the first one excluding 0 in the opposite direction.
- Descriptive: parent128 - fifo128 -10.0 [-14.1, -5.7]; clock128 - fifo128 -8.9 [-13.1, -4.8]; parent128 - clock128
  -1.1 [-5.2, +3.2].
- R4-size (pool 128 minus fifo2048, predicted > 0): fifo +20.0 [+16.6, +23.4], clock +11.1 [+7.9, +14.1], leaf +7.7
  [+4.8, +10.5], parent +10.1 [+7.2, +13.0]. All four are supported.
- Absent share of hot checks: fifo128 27.1%, clock128 8.8%, leaf128 0.5%, parent128 6.2%.

## Test I: idle-page tracking

Source: open3/i3.analysis.txt (analyze_i3.py), open3/i.run (40 x `-accel tcg,thread=single`), logs/i3-*.

- 40 boots, 80 runs (aging on and off per boot). All 40 logs report "ALL DONE fails=0". No run was dropped by the
  pfn_changes / vmstat check.
- Hot false_idle % with aging on: fifo 0.00, clock 0.00, leaf 7.78, parent 0.00. Leaf minus each other policy: +7.78
  [+4.87, +10.69], n = 10, leaf higher in 10 of 10 seeds.
- pf_taken change with aging (aging vs none, same boot): leaf +1.88% [+0.95, +2.81] (10/10 seeds higher); fifo, clock and
  parent +0.00.
- Parser note: one aging=1 RESULT line in i3-R1024p128-fifo-s3 was split by a clocksource-watchdog console message.
  The parser rejoins it; no data changed (PREREG 01:27).

## Test S: stress, fallback and selftest suites (MTTCG, as pre-registered)

Source: classify_a8.py on s1-*, s2-*, s3-suite-*; logs/s1-*, s2-*; open3/s.run (26 x `-accel tcg,thread=multi`).

- S1 (lru_stress_test -v 4 -d 120, fallback on, seeds 1-3): fifo 3/3, clock 3/3, leaf 3/3, parent 3/3 PASS; toggle 2/3.
  lru_fifo_fallbacks = 0 in every run that reported.
  - s1-toggle-s3: class M + L. "BUG: KASAN: null-ptr-deref in paging64_page_fault+0x60f/0xd80", "Write of size 4 at addr
    000000000000004c", with the Oops RIP at paging64_page_fault+0x617. This is clear_sp_write_flooding_count(it.sptep) in
    FNAME(fetch): sptep_to_sp() returned NULL. At the fault R15 = it.level = 1 and RBX = sptep = ffff88800003ae00 (pfn
    0x3a, in the first 1 MiB that x86 reserves at boot). The level-2 SPTE held a garbage pfn, not a pointer to a freed
    shadow page. KVM_PROVE_MMU, DEBUG_LIST, KASAN and lockdep reported nothing earlier (PREREG Amendment 5).
- S2 (fallback off: lru_fifo_fallback written 0 in all 9 boots): clock 3/3, leaf 3/3, parent 3/3 PASS. No ENOSPC.
  lru_fifo_fallbacks (empty sweeps, counted with the fallback off) = 0 in every run.
- S3 phase-1 suites (lru_mmu 1, pool 64, lru_age 1 and 2): both boots passed memslot_modification_stress_test and
  mmu_stress_test, then hit a hard lockup on CPU 0 during demand_paging_test (about 398 s and 400 s), followed by a
  soft lockup on CPU 1 and a softlockup panic. Under the 02:04 S3q reading they are excluded from the S3q comparison
  (phase-1 load).
- Prediction check (Amendment 1): no empty sweep in any S1/S2 arm and no ENOSPC in S2. Under the pre-declared
  consequence, Part 1 drops the "caught ENOSPC ... 71 seconds" story.

## Tests X5, X6, X6b: does the S1 crash depend on the LRU code?

### X5 (MTTCG, stopped)

Source: open3/x5a/ (x5.run: 9 x `-accel tcg,thread=multi`), PREREG Amendment 6.

X5 was stopped at 01:39:58, when the user asked to address the bugs, before its stopping rule was reached. PASS:
fifotog-s13, fifotog-s9, toggle-s12, toggle-s4. FAIL: fifotog-s5 (FIFO throughout, togglers writing no-op values):
"BUG: KASAN: null-ptr-deref in __enqueue_entity+0xf5/0x350", a concurrent "WARNING: CPU: 3 PID: 73 at
kernel/sched/sched.h:1512 update_curr_se", and a lockdep circular-dependency report. Killed mid-run, unread:
fifotog-s11, fifotog-s4, toggle-s13, toggle-s14. X5 is not analysed further.

### X6 (single-thread, stopped)

Source: open3/x6a/ (9 x `-accel tcg,thread=single`), PREREG Amendment 7.

The first 5 finished boots (fifotog-s9, fixage-s7, fixage-s12, fixage-s14, toggle-s11) failed lru_stress_test's progress
check, "made no progress in 30 s". The other 4 (fifotog-s14, fixage-s8, toggle-s10, toggle-s6) were killed by PID before
they finished. The only kernel warning in the 9 logs is the boot-time SRSO message. Diagnosis: harness artifact. The
guest reports progress once every 2,000 iterations, and under single-thread TCG a vCPU completes about 2,000 iterations
per 30 s. Fix: lru_stress_test -w (progress window; default 30, so S1 is unchanged). The 9 X6 boots count as neither
pass nor fail. X6b repeats their seeds. X6b had no "made no progress in 240 s" failure, so the 5 aborts stay excused
(Amendment 7 condition).

### X6b (single-thread, primary stability test)

Source: classify_a8.py on x6b-*, logs/x6b-*, open3/jobs_x6b.txt. Plan: lru_stress_test -v 4 -d 720 -w 240 -s N,
bzImage-camp-3l (KASAN), -smp 4, kvmmmu tracing, seeds 4-15.

| arm | boots | PASS | FAIL | per-vCPU iterations, passing boots | lru_fifo_fallbacks |
|---|---|---|---|---|---|
| toggle | 12 | 11 | 1 (s10) | 30,000-52,000 | 0 |
| fifotog | 12 | 12 | 0 | 38,000-64,000 | 0 |
| fixage | 12 | 12 | 0 | 40,000-64,000 | 0 |

Iterations next to S1 (MTTCG, -d 120; per-vCPU ranges from the same RESULT lines): fifo 58,000-78,000, clock
78,000-92,000, leaf 58,000-74,000, parent 60,000-84,000, toggle (s1, s2) 50,000-62,000. Amendment 7 aimed X6b's 720 s at
about 51,000 iterations per vCPU, to match S1 toggle s1 (50,000-54,000). X6b reached 30,000-64,000 per vCPU, so several
X6b boots did less work per vCPU than S1, and none did much more.

x6b-toggle-s10r, alone: 100,000 / 100,000 / 100,000 / 98,000 iterations per vCPU. It ran beside fewer boots than the
X6b batch (less host contention), so it is not comparable with the X6b ranges above.

### x6b-toggle-s10: upstream IOAPIC race

Class M (+ no ALL DONE): "BUG: KASAN: null-ptr-deref in mask_ioapic_irq+0x40/0x130" at 12.29 s of L1 boot, on CPU 0
(swapper), during setup_IO_APIC. This was before SMP bring-up, before init ran and before any VM existed;
lru_stress_test never started.

Root cause (PREREG RESULTS, checked against /tmp/kvm-build-knob/vmlinux):
- mp_irqdomain_alloc() stores irq_data->chip = &ioapic_chip at +0x3ca and irq_data->chip_data at +0x3db, with
  interrupts enabled. Legacy ISA IRQ descriptors already use handle_level_irq.
- The interrupted frame shows RIP __asan_store8+0x0, return address mp_irqdomain_alloc+0x3db, R13 = ffff88810012a028.
  The Oops shows mask_ioapic_irq with RBX = the same address and chip_data = NULL.
- A legacy interrupt arrived between the two stores. handle_level_irq called the new chip's irq_mask
  (mask_ioapic_irq), which dereferenced the NULL chip_data.
- This is a race in upstream arch/x86/kernel/apic/io_apic.c. The patch changes no file under arch/x86/kernel or
  kernel/irq. KASAN's __asan_store8 call between the two stores widens the window, and TCG delivers interrupts at block
  boundaries. One web search found no upstream fix.
- It is not a KVM or LRU failure. It stays a FAIL in every tally.

### x6b-toggle-s10r

Same plan, kernel and tracing as x6b-toggle-s10, single-thread (`-accel tcg,thread=single` on the qemu command line in
open3/x6log/x6b-toggle-s10r.run). Result: 1/1 test PASS, "ALL DONE fails=0", no signature, no warning beyond SRSO.
It counted fully under Amendment 9 and passed (PREREG 04:49:48).

## S3 suites under single-thread TCG (S3s)

Source: classify_a8.py on s3s-*, open3/x6log/s3s-*.run.

s3s-age1, s3s-age2, s3s-lru0 and s3s-up (upstream kernel) each passed 12/12 tests ("ALL DONE fails=0", read-backs
tdp_mmu = N, npt = N). demand_paging_test -u MISSING passed 3 times in each boot. No class M, I or L.

## MTTCG selftest-suite controls (S3m, QEMU 8.2.2) and QEMU A/B (S3q, QEMU 11.1.2)

Source: classify_a8.py on s3m-* and q11-*, PREREG RESULTS, open3/earlykill.log. All boots -smp 4, MTTCG.

| boot | kernel / knobs | QEMU | class | signature (classify_a8 / PREREG) |
|---|---|---|---|---|
| s3m-age1-r1 | camp-3l, lru_age 1 | 8.2.2 | M + L | KASAN user-memory-access in vcpu_enter_guest+0x11ea, mmu_stress_test |
| s3m-age1-r2 | camp-3l, lru_age 1 | 8.2.2 | L | silent in mmu_stress_test until TIMEOUT |
| s3m-up-r1 | upstream | 8.2.2 | I + L | spinlock recursion, then hard LOCKUP, demand_paging_test; killed early |
| s3m-up-r2 | upstream | 8.2.2 | L | hard LOCKUP, demand_paging_test |
| s3m-lru0-r1 | camp-3l, lru_mmu 0 | 8.2.2 | I | spinlock recursion, mmu_stress_test; killed early |
| s3m-lru0-r2 | camp-3l, lru_mmu 0 | 8.2.2 | L | hard LOCKUP, demand_paging_test |
| s3m-tdp-r1 | camp-3l, TDP MMU (Y/Y) | 8.2.2 | failed (fails=6) | no kernel signature; see below |
| s3m-tdp-r2 | camp-3l, TDP MMU (Y/Y) | 8.2.2 | L | QEMU exited mid-mmu_stress_test, no console output |
| q11-age1-r1 | camp-3l, lru_age 1 | 11.1.2 | I | spinlock recursion, mmu_stress_test; killed by hand |
| q11-age1-r2 | camp-3l, lru_age 1 | 11.1.2 | I | spinlock recursion, mmu_stress_test; killed early |
| q11-up-r1 | upstream | 11.1.2 | L (S3-L) | hard LOCKUP, demand_paging_test |
| q11-up-r2 | upstream | 11.1.2 | L | hard LOCKUP, mmu_stress_test |

12 of 12 MTTCG boots failed. classify_a8.py prints "cls=PASS" for s3m-tdp-r1 because it has no kernel signature and
reached ALL DONE. Its "ALL DONE fails=6" makes it a failed boot.

TDP controls:
- s3m-tdp-r1 (read-backs Y/Y): mmu_stress_test failed 3/3 (assertion `get_ucall(vcpu, &uc) == UCALL_SYNC`, mmu_stress_test.c:106)
  and demand_paging_test failed 3/3 ("Invalid guest sync status: exit_reason=SHUTDOWN", an L2 shutdown). The other 6
  tests passed.
- s3m-tdp-r2 (Y/Y): the log ends after memslot_modification_stress_test PASS, during mmu_stress_test, with no kernel
  message. PREREG records that QEMU exited 0 at 3.5 min, which under -no-reboot means the L1 reset (the run file shows
  no exit code). It counts as class L under the "no ALL DONE" definition.

S3q reading (agreed wording, PREREG Amendment 9 entry; counts beside the full MTTCG tally, never alone):

> the demand-paging lockup (S3-L) occurred under multi-threaded emulation on the patched kernel with the LRU policy off
> and on the unpatched kernel, on both QEMU versions (3 of 8 boots on 8.2.2, 1 of 4 on 11.1.2); boots with the LRU
> policy on failed earlier, in another test; it occurred in 0 of 4 single-threaded boots, which at the multi-threaded
> rate (4 of 12) happens by chance about 20% of the time.

- The S3-L boots are s3m-up-r1, s3m-up-r2, s3m-lru0-r2 (8.2.2) and q11-up-r1 (11.1.2). Any failure: 8 of 8 on 8.2.2,
  4 of 4 on 11.1.2.
- Amendment 8 reading: the same lockup occurred on the unpatched upstream kernel under QEMU 11.1.2 MTTCG, so it does not
  depend on the patch. Whether QEMU or upstream KVM/userfaultfd causes it is unresolved; QEMU is not named.
- q11-age1-r1 is class I on 11.1.2: the interrupt anomaly persists in the newer QEMU.
- Amendment 6 caveat: single-thread TCG serialises vCPUs and can hide a real guest-kernel race. Only the IF-clear IRQ
  frames are specific to the emulator.

## Crash-site tally: MTTCG class-M crashes

All open3 MTTCG boots: S1 15, S2 9, S3 phase 1 2, X5 9, S3m 8, S3q 4. Class-M crashes, by site:

| site | boot | arm / knobs | lru_age | kernel |
|---|---|---|---|---|
| MMU walk (garbage SPTE in FNAME(fetch)) | s1-toggle-s3 | toggle (lru_mmu 0/1, lru_age 1/2) | on | camp-3l |
| vcpu_enter_guest stack | s3m-age1-r1 | lru_mmu 1, lru_age 1 | on | camp-3l |
| scheduler (__enqueue_entity) | x5-fifotog-s5 | FIFO, no-op togglers | 0 | camp-3l |
| any | upstream, lru0 and tdp controls (s3m-up, s3m-lru0, s3m-tdp, q11-up) | | | 0 class-M crashes |

The KVM-code class-M crashes (MMU walk, vcpu_enter_guest) appeared only in boots with lru_age on: 2 of 2. Under the
Amendment 7 reading, the posts may not describe the MTTCG failures as unrelated to the LRU code until the 0x3a signature
is root-caused. The scheduler crash ran with lru_age 0.

s3m-age1-r1 crash-site notes (PREREG 02:23, Amendment 8, 02:35 addition):
- The KASAN read is at vcpu_enter_guest+0x11ea: `mov -0xe8(%rbp),%rdi`, right after the `sti` that follows
  handle_exit_irqoff. It reloads &vcpu->stat.exits, which was stored to -0xe8(%rbp) at +0xfe6 before the inner run
  loop. The slot held 0x3ad00 instead of the kernel pointer ("Read of size 8 at addr 000000000003ad00").
- Between the store and the reload, only kvm_x86_vcpu_run (svm_vcpu_run, VMRUN) and handle_exit_irqoff run, with
  IRQs disabled. Neither reaches the shadow-MMU eviction code. An out-of-bounds stack write by instrumented C would
  have been reported by KASAN_STACK first.
- The uninstrumented load completed, so RBP was valid and the frame slot itself held 0x3ad00.
- On CPU 2 (PID 82), two words of the same frame were corrupt, 0xb0 bytes apart: the spill slot at rbp-0xe8 and the
  stack canary at rbp-0x38, on either side of the KASAN-guarded locals. The stack-protector check of that frame failed.
- CPU 3 (PID 84) oopsed at the same moment at ioctl_has_perm+0x17b, faulting on the same address 0x3ad00. It reached
  the address through file->f_security (site class: slab; the file pointer is unverified), not through a stack reload.
- No KASAN report preceded CPU 2's ("Not tainted"), and the command line has no kasan_multi_shot, so CPU 3's later
  checks prove nothing.
- 0x3ad00 is not a ucall slot address for 4 vCPUs and is not page aligned. The L2-address check is otherwise
  inconclusive.
- Reading: consistent with SVM entry/exit state handling, not established.

## Spot checks for the MTTCG deviations

- U3 (source: open3/jobs_u3spot.json, accel `tcg,thread=single`; manifest_u3spot.csv): 6 boots, the first v1 job of each
  cell, 30 tests, all PASS. pf_taken equals manifest_v.csv in 30 of 30 (all five policies in every cell).
- R4 (source: logs/r4spot-*, header ACCEL=tcg,thread=single; logs/t7ctl-*): the 5 -M 0 controls equal t7ctl exactly in
  mmu_recycled / hot_zaps / pf_taken: fifo128 3318/442/10396, clock128 3024/195/6404, leaf128 2688/0/2925, parent128
  2961/144/5592, fifo2048 0/0/2.
- Under PREREG's rule (a mismatch reruns the whole test single-threaded), U3 and R4 are kept. The deviations stay logged.

## Protocol deviations and disclosures

1. U3 did not run single-threaded as planned. run_phase1.sh ran it through vmrun.py with --smp 1, and camp/jobs/v1.json
   sets job accel "tcg" (MTTCG with one vCPU, per the Amendment 6 revision). The U3 logs carry no ACCEL header.
2. R4 did not run single-threaded as planned. open3/t7.run shows 325 x `-accel tcg` with -smp 4, and t7rerun.run 2 x
   `-accel tcg`. The t7 logs carry no ACCEL header.
3. open2's R' also ran with plain `-accel tcg` (open2/t6.run: 585 x `-accel tcg`), the same deviation. R' supplies only
   within-R' paired differences (Amendment 1 source rule).
4. I ran single-threaded as pre-registered (open3/i.run: 40 x `-accel tcg,thread=single`). S1, S2 and the phase-1 S3
   suites ran MTTCG as pre-registered (open3/s.run: 26 x `-accel tcg,thread=multi`). Amendment 6 first said U3, R4 and
   I ran under MTTCG; its revision corrected this from run_phase1.sh. REPORT and posts must not describe U3 or R4 as
   single-thread runs.
5. Amendment 9: the D gate was changed after the X6b data was seen. Under the strict Amendment 6 gate ("X6b toggle and
   fixage have 0 failures"), x6b-toggle-s10 would have blocked D. The new exception covers a crash before
   "Unpacking initramfs" (before "Run /sbin/init as init process" for D's L1). It is symmetric across arms and changes
   only the D gate. x6b-toggle-s10 stays a FAIL in every tally. The s10r rerun counted fully and passed.
6. The Amendment 8 watcher (watch_a8.sh) missed x6b-toggle-s10. The boot crashed and exited between two 60 s polls,
   and the watcher scanned only running boots.
7. Amendment 5 was written after the S data was seen. It also discloses that an older two-lap toggle stress boot
   (t4-toggle-s2, kernel camp-fb, 2026-10-09) failed with a spinlock recursion on the rq lock and a timer IRQ taken with
   EFLAGS.IF clear, and was rerun as s2r without being reported as a failure.
8. X5 was stopped before its stopping rule. X6 was stopped and replaced by X6b after a harness fix (lru_stress_test -w,
   patched in ~/projects/kvm, uncommitted).
9. Timestamp correction (PREREG 23:31): the early amendment labels were typed by hand and several were wrong; corrected
   labels are in PREREG. Every later entry takes its time from `date`.
10. Amendment 10 (before any D data): run_d2.sh was stopped and replaced by run_d3.sh with rule (b) rounds, a done-file
    watcher exit and halt categories; redis_camp.py gained STALL_S passthrough, a NO_LOG labelling fix (published
    r1-p96-clock-r1 was such a row) and a kill on reboot failure. The single-thread D settings come from a 2-run smoke.

11. Amendment 11 (before any D data): the single-thread D restart was dropped because the single-thread Redis smoke
    froze; Diagnostic A ran first, and D runs under MTTCG only, with RUN/SERIAL/STALL-I findings recorded instead of
    halting (stop rules: patch site, volume cap). Diagnostic A's trace reading deviates from the literal rule (see that
    section). Before D_GO, stall_class.py was widened to parse real-mode EIP=/EFL= captures; on the 15 STALL logs only
    diag c3l-r2 changed (O to I).

## Early kills (Amendment 8)

Source: open3/earlykill.log, PREREG 02:28:11.

| boot | qemu pid | signature | log mtime | kill |
|---|---|---|---|---|
| q11-age1-r1 | 975025 | BUG: spinlock recursion on CPU#0, mmu_stress_test/80 (at 377 s) | 2026-10-22 02:11:09 | 02:28:11, by hand |
| s3m-up-r1 | 987643 | BUG: spinlock recursion on CPU#0, demand_paging_t/91 | 2026-10-22 02:33:20 | 02:44:05 |
| s3m-lru0-r1 | 1035559 | spinlock recursion (see note) | 2026-10-22 02:33:59 | 02:44:15 |
| q11-age1-r2 | 1044088 | spinlock recursion (see note) | 2026-10-22 02:49:31 | 03:00:27 |

Note: for s3m-lru0-r1 and q11-age1-r2, the signature field in earlykill.log holds selftest progress text ("4 vCPUs
haven't rendezvoused..."). The selftest's progress output has no newlines, so the kernel message shared a console line
with it, and the watcher logged 110 characters from the start of that line. classify_a8.py finds "BUG: spinlock
recursion on CPU#0, mmu_stress_test/81" (s3m-lru0-r1) and ".../83" (q11-age1-r2), and PREREG RESULTS records each kill
signature as verified spinlock recursion. Classes come only from each log as it stood before the kill.

## Diagnostic A: single-thread Redis freezes (Amendment 11 Part A)

Source: PREREG Amendment 11 and its 12:08:42 reading; diag/logs/diag-*.log, diag/x/*.trace (l2y traces copied off the
powered-off overlays), diag/sym.py (System.map-6.8.0-142-generic from the L1 /boot partition, KASLR slide from the IDT
#PF gate). QEMU 8.2.2, `-accel tcg,thread=single`, fifo, pool 0, one run per fresh L1. Not D data.

| arm | kernel | runs | result | freezes | vector |
|---|---|---|---|---|---|
| up | bzImage-upstream | 2 | 2 STALL | 2 | 0x30, 0x30 |
| upsvm | bzImage-upstream-svmfix | 2 | 2 STALL | 2 | 0x30, 0xec |
| c3l | bzImage-camp-3l (D's kernel) | 2 | 2 STALL | 1 | 0xec (r2: real-mode hang, not a freeze) |

The 2-run smoke before it (camp-3l, fifo, no traces) froze in both runs with the same stack shape.

- Freeze: every HMP capture has RFLAGS.IF clear, and the L2 stack shows a nested interrupt of one vector taken with IF
  clear inside that vector's own handler, then a spin on a lock or seqcount the outer handler holds (irq_desc->lock in
  handle_edge_irq for 0x30; timekeeper_lock or the tk_core.seq read loop for 0xec).
- Trace (all 6 valid, overrun 0): in each freeze the last kvm_inj_virq is a fresh injection of that vector into an
  IF-set context (VINTR or INTR exit just before it; in up-r1 and up-r2 the outer iret frame matches that exit's RIP,
  with RFLAGS 0x282 and 0x246). The next exit, with none between, reports exit_int_info 0x330 or 0x3ec: same vector,
  VALID clear, type 3. An EVENTINJ delivery leaves type 0, and L1 cannot inject an external vector as type 3
  (helper_vmrun rejects it), so the second delivery did not come from L1. One L1 injection gave two deliveries in one
  VMRUN. All 30 type-3 exits on IRQ vectors in the 6 traces follow a same-vector injection with no exit between; the
  earlier ones in each freeze did not deadlock. One of the 30 is c3l-r2's real-mode 0x308, right before its derailment.
- Reading: reading 3 (the interrupt reached L2 without an L1 injection), with a declared deviation. The literal rule
  ("last kvm_inj_virq not reinjected" = reading 2) picks the first, legitimate delivery, and reading 2's content (L1
  injected while IF was clear or the vector blocked) is contradicted by the exit RIP and the outer frame. Reading 3,
  the literal reading 2 on up freezes, and the count table (up 2/2) all give the same decision: D proceeds on camp-3l.
- Mechanism, consistent with the traces, not tested with a patched build: in QEMU 11.1.2 source, helper_vmrun's
  SVM_EVTINJ_TYPE_INTR branch sets cs->exception_index to the vector and delivers it directly without resetting the
  index; a later exit_request with no interrupt processed makes cpu_handle_exception deliver the stale vector again,
  with no IF or LAPIC check. The 8.2.2 traces show the same signature (no 8.2.2 source on the host).
- Scope: the replay inference rests on the three 0x30 freezes. For the 0xec freezes, vector equality alone does not rule
  out L1's own LAPIC timer (also 0xec) reaching L2. The mechanism concerns events injected at VMRUN into L2; it is not
  evidence about the L1-level IF-clear anomalies under MTTCG (the stab1l timer IRQ, the S3 spinlock recursions), which
  stay unexplained.

## Test D: Redis under MTTCG (Amendment 11 Part B)

Run by run_d4.sh from 12:15:25 to 17:23:23: 4 L1s (bzImage-camp-3l, KASAN), `-accel tcg,thread=multi` for all 5 L1
boots (d_accel.log), RUN_TMO 7200, STALL_S 600, BOOT_S 5400. Two redis_camp invocations: d1.json (224 runs) and
d1_rep1.json (1 rule-(b) replacement). Both watchers ended "EXIT clean". d1_check.txt: every cell complete, 16 clean
runs per policy at pools 80/96/128 and 8 at the default pool.

| pool | FIFO | clock | leaf | parent |
|---|---|---|---|---|
| 80 | 1,312 | 1,004 (−23.5%) | 991 (−24.5%) | 984 (−25.0%) |
| 96 | 370 | 298 (−19.7%) | 296 (−20.1%) | 277 (−25.2%) |
| 128 | 63 | 44 (−31.0%) | 42 (−33.3%) | 42 (−33.3%) |
| default | 0 | 0 | 0 | 0 |

mmu_recycled medians, % = ratio of medians vs FIFO (d1an/analysis.txt, percentile bootstrap 95% CI, Mann-Whitney p).

- Evictions vs FIFO: every CLOCK version's CI excludes zero at 80 and 96. At 128, clock [−33.8, +0.0] reaches zero;
  leaf and parent [−35.4, −20.8] exclude it. No CLOCK-vs-CLOCK eviction CI excludes zero at any pool.
- pf_taken vs FIFO: −5.9 / −7.3 / −7.0% at 80 and −2.4 / −2.9 / −3.0% at 96 (clock / leaf / parent), all CIs exclude
  zero. At 128 and default, every CI includes zero (|median| ≤ 1.2%). Among CLOCK versions, only clock vs leaf at 80
  excludes zero: −1.6% [−3.6, −0.2]. Default-pool pf_taken medians 34,196–34,624 with 0 evictions.
- Findings (D_FINDINGS): 1 run, d1-p96-parent-r4, STALL-I. No RUN or SERIAL findings, no patch-site hits, no CAP or
  HARNESS halts. Rule (d): Fisher-Freeman-Halton p = 1 (0/0/0/1 failed of 56/56/56/57 per policy); reported only.
- d1-p96-parent-r4: STALL declared at t=740 s (pf_taken unchanged for 607 s), after L2BENCH_START. HMP: RIP
  native_queued_spin_lock_slowpath+0x83, RFL 0x2 (IF clear), LAPIC ISR and IRR empty. The L2 NMI panic stack shows an
  inner hrtimer_interrupt+0x51 → _raw_spin_lock_irqsave that interrupted __hrtimer_run_queues+0x1f1 at EFLAGS 0x12 (IF
  clear) inside an outer hrtimer_interrupt: the Diagnostic A freeze pattern, during the benchmark rather than at boot.
  The L1 dmesg delta holds only "Unhandled WRMSR(0xc0010007)" and the harness's sysrq output (no KASAN, lockdep or
  other splat). D took no ftrace, so this run gives no evidence on whether L1 injected the inner interrupt once or
  twice.
- Excluded from analysis: d1-p96-parent-r4 only; replaced by d1-p96-parent-r4x (rule b), which ended END.

Scope: D measures fault and eviction counts under MTTCG only. The single STALL-I is reported as a finding; it neither
halts D nor is attributed to the CLOCK code: Diagnostic A produced the same freeze pattern on the unpatched kernel.

## QEMU patch test (Amendments 12 and 13)

Patch: one line in helper_vmrun's SVM_EVTINJ_TYPE_INTR case, `cs->exception_index = -1;` after
`do_interrupt_x86_hardirq` (qemu-822-svm-intr-replay.patch; the same hunk for 11.1.2 in qemu-svm-intr-replay.patch).
Upstream master still lacks it (checked 2026-10-23). Single-thread TCG, bzImage-camp-3l, pool 0, fifo.

| Arm | QEMU | Runs | Outcome |
|---|---|---|---|
| q11 | 11.1.2, unpatched | 2 | 2 L2_EXITED: #DF in early L2 boot (exit_int_info 0x80000b08) |
| q11p | 11.1.2, patched | 4 | 4 L2_EXITED, same #DF |
| u822 | upstream 8.2.2, unpatched | 2 | 2 STALL-I; type-3 residues 3 and 4 |
| p822 | upstream 8.2.2, patched | 4 | 4 END (pf_taken 34,583-34,810); 3 transient L2 soft lockups in 2 runs |

- 11.1.2 cannot boot this nested L2 in either arm, so Amendment 12 gave no reading.
- 8.2.2 gives reading R1: the unpatched control froze in the Diagnostic A pattern, with the same trace signature, and
  every patched run finished; 2 of 4 logged transient L2 soft-lockup warnings (3 in total, 21-22 s), which this test
  does not explain. This supports the stale-exception_index replay as the cause of the single-thread L2 boot freezes on
  upstream 8.2.2. Traces are saved only for stalled runs, so the absence of the type-3 residue under the patch is not
  shown directly. Limits: 4 patched runs; upstream 8.2.2 rather than the Ubuntu binary (different default machine type, build
  options and Debian patches outside the 3 compared files); no test under MTTCG, so the d1-p96-parent-r4 freeze is not
  covered.
