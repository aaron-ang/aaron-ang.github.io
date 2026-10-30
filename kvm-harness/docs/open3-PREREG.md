# KVM posts: rerun of every accessed-bit result on the three-lap sweep, pre-registration

Written 2026-10-21 23:13, after the code change and builds, before any campaign run with the new kernels (smoke runs on
them at 23:14-23:18 are disclosed in the amendments, with their results). Amendments go at the
bottom with timestamps, before the affected data is looked at. Smoke runs check the harness only and are excluded.

## Code change (user decision: "third sweep")

- Repo ~/projects/kvm (uncommitted): `kvm_mmu_clock_zap_pages()` budget = `(lru_age ? 3 : 2) * n_used_mmu_pages`.
  Reason: each visit clears at most one signal (lru_ref first, then the Accessed bits), so with lru_age a page with both
  set needs two visits before the third can zap it. With two laps the sweep could zap nothing even with one vCPU
  (Test U x32: fallbacks leaf 60/68, parent 352/339 per run at 32 pages). Comment updated. W=1 build clean.
- camp/src (validation build) gets the same change, except lru_age=3 (clear-only, never skips for A bits) keeps two laps,
  so its eviction decisions stay equal to lru_age=0. New validation-only knob `kvm.lru_fifo_fallback` (default 1;
  0 = after a sweep that zaps nothing, return 0 as the code did before the fallback existed).
- Kernels: `camp/bzImage-camp-3l-nokasan` (KASAN/lockdep off) and `camp/bzImage-camp-3l` (KASAN + lockdep, same config as
  bzImage-camp). Code paths with lru_age=0 are unchanged from the zp/zp2 kernels.

## What the posts claim that depends on lru_age (all must come from the new kernel)

Part 2: reuse-table leaf/parent columns and the leaf/parent-vs-install comparisons; the fallback paragraph and footnote
(one-vCPU fallback counts, stress-test counts); Redis table leaf/parent columns; idle-page tracking (~6% of hot pages under
leaf); swap test leaf/parent numbers and intervals, and "every policy in the smaller cache lost 11-17 points more".
Part 1: the lap bound (Invariant 2), what can defeat it (Invariant 3), the stress-test paragraph (ENOSPC story, fallback,
"every run passed under every policy").

Results that do not depend on lru_age skips (FIFO, install marking, creation marking, clear-only, folio_set_young and
mark_page_accessed variants, fifo2048) ran on code identical to the current code for those paths and are kept as
within-campaign results; they are not rerun except where a same-campaign comparator is needed (below).

## Shared setup

QEMU TCG with emulated SVM on the aarch64 host, kvm.tdp_mmu=0 kvm_amd.npt=0, run.sh / vmrun.py as before.
Host cap <= 18 emulator threads. Seeds and n are fixed now; one look per test.

## Test U3: reuse table (one-vCPU selftest, deterministic)

Same jobs as Test U (u1: seeds 1001-1020, every cell; u2: seeds 1021-1035 for scan/64, scan/128, hot/128), 5 policies
per boot in random order, `lru_reuse_test -w 100000 -n 200000` (the u2 binary, prints lru_fifo_fallbacks), kernel
bzImage-camp-3l-nokasan, lru_fifo_fallback=1, -smp 1, 3G, single-thread TCG, 10 workers. 165 boots.

- Manipulation / determinism check: fifo, clock_create and clock_fill pf_taken must equal Test U per seed (code path
  unchanged, test deterministic). Any mismatch is reported and the table is built from the new runs regardless.
- U3a (prediction): lru_fifo_fallbacks = 0 in every leaf/parent run (one vCPU, three laps). Any nonzero is reported.
- U3b: new table (mean of per-seed % change vs FIFO), CIs and verdicts exactly as compare_u1.py / analyze_reuse.py
  (95% t-CI and bootstrap; CI excludes 0 -> better/worse, flagged "small" if within +-3%; CI within +-3% -> no difference).
- U3c: leaf vs clock_fill and parent vs clock_fill per cell, as U4 (CI, share of seeds better).
- Reported for the REPORT only (not the post): per-seed change of leaf/parent vs Test U.

## Test S: stress / correctness / fallback (4 vCPUs, KASAN + lockdep)

Kernel bzImage-camp-3l (KASAN), plan as Test 4: `min_alloc_pages 64`, `lru_stress_test -v 4 -d 120 -s SEED`, default
run.sh accel (MTTCG, -smp 4), TIMEOUT 900, 2 boots in parallel.

- S1: arms fifo, clock, leaf, parent, toggle (lru_mmu 0/1 at 50 ms, lru_age 1/2 at 70 ms, as t4-toggle), seeds 1-3,
  lru_fifo_fallback=1. 15 boots. Outcome: PASS/FAIL, splats (scan.sh), lru_fifo_fallbacks per run.
  Prediction: all PASS, no splat; fallbacks 0 for fifo/clock; leaf/parent > 0 is possible (running vCPUs re-set A bits).
- S2 (Part 1's ENOSPC claim, current code): arms clock, leaf, parent with lru_fifo_fallback=0, seeds 1-3. 9 boots.
  Outcome per run: PASS, or FAIL with KVM_RUN ENOSPC and time to failure. No prediction; reported as observed.
- S3: KVM selftest suite (plan "stress": memslot_modification_stress_test, mmu_stress_test, demand_paging_test,
  kvm_page_table_test, 3 rounds) once each under lru_age=1 and lru_age=2 (lru_mmu=1, pool 64). 2 boots. Prediction: PASS.

## Test I: idle-page tracking (deterministic)

Cell R1024p128 of Q4 (`lru_idle_test -a -r 1024 -k 2 -t 20 -s SEED` then the same without -a), policies fifo, clock,
leaf, parent, seeds 1-10, pool 128, transparent_hugepage=never, kernel bzImage-camp-3l-nokasan, single-thread TCG,
6 workers. 40 boots. Metric: hot false_idle % (aging arm), analysis as open/q4/analyze2.py (leaf minus each policy,
paired by seed, 95% t-interval, sign count). Check: no pfn_changes / vmstat_reclaim_migrate.

## Test R4: swap test (host reclaim)

Workload identical to Test R': `lru_reclaim_test -r 1024 -k 16 -t 6 -M 40 -S 24 -s SEED -Z`, L1 8G, SWAP_MB 1024,
compaction_proactiveness 0, single-thread TCG, kernel bzImage-camp-3l-nokasan, lru_zap_accessed=0, lru_fifo_fallback=1.
Arms: fifo128, clock128, leaf128, parent128, fifo2048; seeds 101-164 (n = 64), plus one -M 0 control per arm.
325 boots, shuffled with a fixed RNG seed ("t7"), 6 workers. Same exclusion rule as R' (no RESULT / splat / OOM /
trace overrun -> rerun once with the same seed; dropped if it fails again).

Why the comparators are rerun: run-to-run variance comes from host reclaim timing, which may depend on host load; all
absolute numbers the post states for the policy and cache-size comparisons then come from one campaign. The R'
variant results (clear-only, folio_set_young, mark_page_accessed, entry presence) stay R'-internal differences.

Metric H and statistics as R' (paired by seed, bootstrap 95% CI 10k numpy seed 12345, t-CI, Wilcoxon; unpaired
sensitivity; CI width reported).

- R4-net: leaf128 - {fifo128, clock128, parent128}, Holm-adjusted Wilcoxon. Verdict rules as R1-net (prediction > 0;
  CI lower > 0 supported; CI upper < +5 "no leaf disadvantage of 5+ points"; else inconclusive). Also parent128 - fifo128,
  clock128 - fifo128 (descriptive).
- R4-size: each pool-128 arm minus fifo2048 (prediction > 0).
- Descriptive: H_present / H_absent per arm, fallbacks, hot zaps.

## Test D: Redis (4-vCPU L2, multi-threaded emulation)

Full rerun of the Redis table on the current code: redis_camp.py, KERNEL=bzImage-camp-3l (KASAN, as the published
campaign's bzImage-camp), 4 L1 instances (-smp 4 -m 8G, MTTCG). Pools 80, 96, 128: 4 policies x 16 reps; KVM default
pool (0): 4 policies x 8 reps. 224 runs, one shuffled batch ("d1", fixed RNG seed). Same failure handling as the
published campaign: runs that do not END cleanly (STALL, L2 exit, L1 event, corruption pattern) are replaced with a run
of the same policy and pool, up to 16 (8) clean runs per cell; all replacements reported.

Analysis analyze_redis.py: median mmu_recycled per cell, median ratio vs FIFO with percentile bootstrap 95% CI,
Mann-Whitney p; same for pf_taken. lru_fifo_fallbacks is added to the manifest fields if the stat is present.

All fifo/clock cells are rerun because the published campaign used a kernel without the SVM EXITINTINFO fix, which
changed shadow zaps for every policy.

## Order and load

Phase 1: R4 (6 workers, single-thread) + U3 (10 workers, single-thread) -> when U3 ends: I (6 workers) then S (2 boots at
a time, 4 threads each). Phase 2 (after R4): D (4 L1 x 4 threads). Never above 18 emulator threads.
qemu cleanup after every phase (clean_qemu.sh, pgrep -x qemu-system-x86 = 0 at the end).

## Reporting

/tmp/kvmval/open3/REPORT.md, then both posts. The posts state the current results only (no comparison with earlier
builds). Nothing is committed without asking.

## Amendments
- 2026-10-21 23:18 Smoke (seed 1 / stress seed 9, not campaign seeds; excluded from every analysis), harness OK:
  - reuse hot/32 seed 1: all five policies pf_taken 212,646 (leaf/parent equal to fifo and clock_fill; lru_age_skips =
    lru_ref_skips = 105,829; fallbacks 0). scan/128 seed 1: leaf = parent = clock_fill = 29,752; fallbacks 0.
  - s3smoke-nofb-parent (lru_fifo_fallback=0 read back N, parent, pool 64, 4 vCPUs, 120 s, KASAN): PASS, no splat,
    lru_fifo_fallbacks 0 (the counter still counts empty sweeps when the fallback is off).
  - No design change from smoke.
- 2026-10-21 23:20 AMENDMENT 1 (after design review; no campaign data exists):
  - Expectations restated with the smoke known: U3a fallbacks 0 in every run; at 32 pages leaf/parent pf_taken expected
    equal to fifo/clock_fill per seed. S1/S2: empty sweeps (lru_fifo_fallbacks, which counts empty sweeps whether or not
    the fallback runs) expected 0 or near 0 in every arm; no ENOSPC expected in S2.
  - Post consequences fixed now. If no S2 run fails with ENOSPC and S1 shows no empty sweep in any arm: Part 1 drops the
    "caught ENOSPC ... 71 seconds" story and describes the FIFO fallback as a safeguard that the tests never triggered on
    the current code; Invariant 3 keeps that the lock-free fast path and running vCPUs *can* set bits behind the hand
    (a property of the code, the reason for the fallback) but states that no test observed an empty sweep. If any S1/S2
    arm shows empty sweeps or ENOSPC, the posts report those counts/times as measured, per arm.
  - Source rule for the swap section: every absolute level the post states (H per arm, present/absent rates, absent
    share, FIFO's loss, entries read by leaf mode) comes from R4. R' supplies only within-R' paired differences
    (clear-only - install, folio_set_young - off, mark_page_accessed - off). "Reads about half as many entries" is
    recomputed as R' clear128 lru_age_sptes vs R4 leaf128 lru_age_sptes (clear-only reads are set by eviction decisions,
    whose code is unchanged).
  - Claims added to the update list: Part 2 intro (line 12), "What the Measurements Show" (6%, swap-test result, "neither
    mode consistently improved"), Part 1 closing paragraph (line 200), Redis footnotes (failure tally, the SVM-bug run,
    the 20-boots x 5-kernels check). The Redis footnote will say the measured kernel includes the EXITINTINFO fix and
    KASAN. The old failure tally and old boot check are replaced by D's own tally; the boot check paragraph is dropped
    unless D's failures meet the concentration threshold below, in which case it becomes a finding to investigate.
  - Test D failure rules: (a) a kernel splat in L1 (KASAN, lockdep report, BUG/Oops/WARNING, list corruption; the known
    upstream "sleeping function called from invalid context" in handle_fastpath_hlt excluded) halts the batch with no
    replacement, pending investigation; a watcher scans every finished run. (b) Emulator-side events (STALL, TSC_MISMATCH,
    L1 unresponsive, L2 exit, KVM_INTERNAL_ERROR) are replaced with the same policy and pool, at most 4 replacement
    attempts per cell; a cell that exceeds the cap is reported with the clean runs it has. (c) Failure table per policy
    and pool in the REPORT. (d) Threshold: Fisher exact test of failed vs clean runs across the 4 policies (all pools
    pooled), p < 0.05, is reported as a finding (and the boot check is rerun); otherwise failures are reported as a tally.
  - U3 mismatch rule: if any fifo/clock_create/clock_fill pf_taken differs from Test U for the same seed, the lru_age=0
    paths differ from the zp/zp2 kernels, so the R' variant results lose current-code status: clear128, fifoY128,
    clockY128 and fifoM128 are then rerun on the new kernel as an R4 extension (64 seeds each, same rules). Same check on
    the R4 -M 0 controls vs R' (t7ctl vs t6ctl for fifo128, clock128, fifo2048: mmu_recycled, hot_zaps, pf_taken).
  - Test I: fifo/clock/parent per-seed results vs Q4 reported descriptively (Q4 kernel lacked the fallback and the SVM
    fix); no consequence.
  - Wording rules for the posts: "worse/better in N rows" counts only rows whose CI excludes 0; bold marks the best point
    estimate per row, all tied values bold.
  - Order: D starts only after every phase-1 test (R4, U3, I, S) has finished.
  - Publish gate: the posts describe the three-lap code behind the GitHub link, but the repo change is uncommitted.
    Committing and pushing ~/projects/kvm (user approval required) is a precondition for publishing the post edits.
  - Declined (reviewer optional): adding clear/fifoY/clockY/fifoM to R4 (+260 boots, ~3.3 h at 6 workers, not 1.3 h).
    Those arms run lru_age=0 or clear-only, whose code paths are unchanged; the U3 and -M 0 control checks above verify
    that, and the rerun becomes mandatory if either check fails.
- 2026-10-21 23:22 AMENDMENT 2 (second review round; no campaign data exists):
  - D rule (a) extended. These also halt the batch, with no replacement, pending a logged diagnosis: RESULT=KVM_INTERNAL_ERROR
    (L1 KVM reporting to L2's VMM, i.e. the code under test; the published one was the EXITINTINFO bug, fixed in this kernel);
    any L2 corruption/crash pattern in the run log (analyze_redis.py CORRUPT list: double fault, Oops, general protection,
    BUG, segfault, Kernel panic, list corruption, KASAN, ...), whatever the run's RESULT, including L2 exits with panic/oops
    output. The batch resumes only after the diagnosis is written here; a run halted this way is reported, never silently
    replaced. In the published campaign these patterns appeared only in 7 runs that also failed (6 TSC-mismatch STALLs, 1
    L2 exit). Rule (b) (replace, cap 4 per cell) now covers only STALL, TSC_MISMATCH and L1 unresponsive with no splat
    and no corruption pattern.
  - S1/S2 consequence restated: Part 1's "caught ENOSPC ... 71 seconds" story is dropped unless an S2 run itself fails with
    ENOSPC on the current code (then the post reports that run's time to failure). Empty-sweep counts are reported per arm
    in every case.
  - 23:23 Implementation note: the harness's OTHER_SPLATS counter is not usable for rule (a). It is cumulative over the L1
    boot, and its "lockdep" pattern matches stack frames of the known sleeping-function splat (16+ old END runs had
    counts 1-88 without a real splat). Rule (a) uses splat *headers* in each run's own dmesg delta instead
    (open3/l1splat.py; zero hits across the 233 published runs). The watcher (open3/redis_watch.sh) applies the halt rules of Amendments 1-2; on the published manifest it flags exactly the 7 failed runs above.
- 2026-10-21 23:23 Phase 1 launched (R4 325 boots x6, U3 165 boots x10, then I 40 x6, then S 26 x2). Reviewer had confirmed the phase-1 designs in round 2; the open round-3 items concern only Test D rules and post wording.
- 2026-10-21 23:24 AMENDMENT 3 (third review round; Test D only, no D data exists):
  - The sleeping-function splat is excused only when it is the known one: in run-log dmesg deltas, only if its stack contains
    handle_fastpath_hlt; on the L1 serial console (headers only, no stacks), only at its known site
    include/linux/uaccess.h:106 (4,985/4,985 such splats in the published run logs had handle_fastpath_hlt in the stack).
    Any other sleeping-function splat is a rule (a) halt.
  - Runs without a run log (L1 unresponsive/exit, NO_LOG) are no longer replaced blind: the watcher scans every L1 serial
    log written after D starts for splat headers, and any hit halts the batch before rule (b) applies.
  - Check against the published campaign: the watcher flags the 7 failed runs and one L1 serial (inst3 boot 2, Sep 28 16:35:
    KASAN null-ptr-deref in sock_poll / file_remove_privs_flags, outside KVM, in the boot that hung in emergency mode).

- 2026-10-21 23:29 AMENDMENT 4 (fourth review round; Test D only, no D data exists)
  - Watcher fails closed. Check exit codes: 0 = bad run, 1 = clean, other = check error (any Python exception, including a missing D_STARTED). Two consecutive check errors halt D like a bad run; each error is logged as WATCHER ERROR in open3/redis_watch.log. run_d.sh touches D_STARTED before it starts the watcher and redis_camp.py. The serial check now runs even before manifest_d1.csv exists.
  - Corruption rule narrowed by one harness artifact: on a STALL, run_l2.sh injects an NMI into L2 (log line "### nmi") and unknown_nmi_panic=1 makes L2 print "Kernel panic - not syncing: NMI: Not continuing". That line is excluded only when the run log contains "### nmi". Other patterns, including "double fault" and any other "Kernel panic", still halt.
  - Back-test on the old Redis manifest (233 runs, old kernel): before the exclusion, 7 r1 runs were flagged (6 harness-NMI STALLs, 1 L2 double fault r1-p96-parent-r8). After it, only r1-p96-parent-r8 is flagged. r2 + r3 (97 runs): 0 flagged, normal exit. Forced error (missing D_STARTED): 2 WATCHER ERROR lines, then HALT. Fake KVM_INTERNAL_ERROR row: HALT. Old flagged serial with an early D_STARTED: HALT.
- 2026-10-21 23:31 TIMESTAMP CORRECTION (reviewer round 5). Earlier labels were wrong: they were typed by hand, not taken from the clock, and several were later than the real clock. Real write times come from the session transcript (UTC minus 4 h) and agree with file mtimes (smoke logs 23:14:01-23:17:46, phase1.out 23:23:26). Corrected labels, old label in brackets: plan 23:13 [23:20]; smoke note 23:18 [23:26]; Amendment 1 and header fix 23:20 [23:40]; Amendment 2 23:22 [23:52]; implementation note 23:23:05 [23:58]; Phase 1 launch 23:23:21 [correct, from date]; Amendment 3 23:24 [2026-10-22 00:10]; Amendment 4 23:29 [23:30]. The plan, Amendments 1-2 and the implementation note predate the phase-1 launch. Amendments 3-4 came after it and change only Test D rules; Test D has no data. The time-zone sentence that was in Amendment 4 was wrong and has been removed. Every later entry takes its time from `date`.
- 2026-10-21 23:32 run_d.sh launched (reviewer aligned round 6); it waits for PHASE1 DONE, then d1smoke, then open3/D_GO.
- 2026-10-22 01:26 R4 done: 325 logs. Controls t7ctl = t6ctl exactly for fifo128, clock128, fifo2048 (no mandatory rerun). 2 excluded: t7-fifo128-s117, t7-fifo128-s151, L1 OOM killer killed lru_reclaim_test (fails=1). Rerun once with the same seed per the R4 exclusion rule; original logs kept as open3/t7-fifo128-s{117,151}.oom.log.
- 2026-10-22 01:27 I parser fix: in i3-R1024p128-fifo-s3 a console message (clocksource watchdog) split the aging=1 RESULT line; analyze_i3.py drops the message and rejoins the continuation line. No data change.
- 2026-10-22 01:33 AMENDMENT 5 (S failures; written after S data was seen, before any X5 run). Observed: S1 toggle s3 crashed (KASAN null-ptr-deref, write of 4 bytes at 0x4c in paging64_page_fault+0x617 = clear_sp_write_flooding_count(it.sptep) in FNAME(fetch): sptep_to_sp() returned NULL, so the shadow walk reached a page that is not a shadow page). Both S3 suites panicked in demand_paging_test: hard lockup on CPU 0 (no NMI backtrace arrived), then soft lockup on CPU 1 waiting for a TLB-flush IPI. The older t4-toggle-s2 (two-lap kernel camp-fb, 2026-10-09) also failed (spinlock recursion on rq lock; timer IRQ taken with EFLAGS.IF clear), and was rerun as s2r without being reported as a failure. Test D is held (no D_GO) until X5 is analysed.
  - Test X5 (diagnostic): does the crash depend on the LRU code? Kernel bzImage-camp-3l (KASAN), run.sh as S (MTTCG -smp 4, TIMEOUT 900), 4 boots in parallel, 24 boots shuffled with RNG seed "x5".
    - Arm toggle: plan as s1-toggle, seeds 4-15.
    - Arm fifotog: FIFO throughout (lru_mmu 0, lru_age 0) with the same two toggler processes writing the same value (t 50 lru_mmu 0 0, t 70 lru_age 0 0), so the L1 load matches the toggle arm. Seeds 4-15.
  - Outcome per boot: FAIL = any of no "ALL DONE fails=0", BUG:/Oops/KASAN:/Kernel panic/lockup/spinlock recursion line (excluding the SRSO boot WARNING). Per-arm failure counts, Fisher exact two-sided.
  - Reading: fifotog failures >= 1 -> the failure is not specific to the LRU code (upstream shadow MMU or the emulator); posts report the failure rates per arm as observed. toggle >= 3 and fifotog 0 (Fisher p < 0.05 needs about 5 vs 0) -> treat as an LRU bug: Test D stays held, root-cause and fix in ~/projects/kvm, then rerun every affected test. Anything else -> inconclusive; one extension of 12 more boots per arm (seeds 16-27), same rule on the pooled counts.
- 2026-10-22 01:34 X5 launched (open3/run_x5.sh).
  - 2026-10-22 01:34 Crash detail (no design change): at the fault R15 = it.level = 1 and RBX = sptep = ffff88800003ae00, i.e. pfn 0x3a, inside the first 1 MiB that x86 reserves at boot. The level-2 SPTE therefore held a garbage pfn rather than a pointer to a recently freed shadow page. KVM_PROVE_MMU, DEBUG_LIST, KASAN and lockdep reported nothing earlier in the run.
- 2026-10-22 01:55 AMENDMENT 6 (written after X5 was stopped; no X6 or S3-control data exists). X5 was stopped at 01:39:58 (killed boots' last log write) when the user asked to address the bugs. Disclosure of the stopped X5: PASS fifotog-s13, fifotog-s9, toggle-s12, toggle-s4; FAIL fifotog-s5 (FIFO throughout, no-op togglers: KASAN null-ptr-deref in __enqueue_entity, concurrent WARNING kernel/sched/sched.h:1512 update_curr_se on CPU 3, lockdep circular-dependency report); killed mid-run, unread: fifotog-s11, fifotog-s4, toggle-s13, toggle-s14 (logs in open3/x5a/). X5 is not analysed further; its stopping rule was never reached.
  - Evidence found in the earlier campaign on the same QEMU 8.2.2 and harness (/tmp/kvmval/camp, 2026-09-28): stab1l under MTTCG failed 37/42 boots, including 6/7 boots of the unpatched upstream kernel; stab1s under single-thread TCG passed 100/100 boots (upstream, fifo, clock, leaf, parent; 20 each). camp/logs/stab1h-upstream-1.log (upstream kernel, MTTCG) shows a timer IRQ taken at dequeue_entities with EFLAGS 0x97 (IF clear) and a second timer IRQ nested inside that handler (EFLAGS 0x46). Guest software cannot make a CPU deliver a maskable IRQ while IF is clear. QEMU 8.2.2's gating (cpu.c x86_cpu_pending_interrupt; svm_helper.c VMRUN/VMEXIT/STGI/CLGI) is correct for one thread; the MTTCG mechanism is not identified. stab1s did not cover the userfaultfd path (its demand_paging_test ran without -u), so the S3 lockup is untested under single-thread TCG.
  - Caveat: single-thread TCG also serialises vCPUs and can hide a real guest-kernel race. Only the IF-clear IRQ frames are specific to the emulator.
  - Failure classes (reviewer round 7 item 1), assigned from the log signature before the arm is read: (M) memory safety: KASAN, garbage SPTE, KVM_PROVE_MMU/mmu BUG, list corruption; (I) interrupt anomaly: IRQ frame with IF clear, spinlock recursion; (L) lockup or timeout: hard/soft lockup, hung task, no "ALL DONE" before TIMEOUT. A boot with several classes is counted in each.
  - Test X6 (primary stability test from now on): as X5 (bzImage-camp-3l KASAN, -smp 4) but QEMU_ACCEL=tcg,thread=single, TIMEOUT 3600, and kvmmmu tracing in every arm (ftrace_dump_on_oops trace_buf_size=256K trace_event=kvmmmu:kvm_mmu_prepare_zap_page,kvmmmu:kvm_mmu_set_spte,kvmmmu:kvm_mmu_get_page,kvmmmu:fast_page_fault). Arms, seeds 4-15: toggle (= s1-toggle), fifotog (= X5 fifotog), fixage (lru_mmu 1, lru_age 1 for odd seeds and 2 for even seeds, togglers writing the same value). 36 boots shuffled with RNG seed "x6" (open3/jobs_x6s.txt), 9 in parallel. FAIL = class M, I or L.
  - Test S3s: s3-suite-age1 and s3-suite-age2 once each under single-thread TCG (TIMEOUT 10800), same pool as X6, started first.
  - Test S3m (MTTCG controls, reviewer item 3, extended): 2 boots each of s3-suite-lru0 (lru_mmu 0, lru_age 0), s3-suite-age1 with kvm.tdp_mmu=1 kvm_amd.npt=1, and s3-suite-upstream (no lru knobs) on bzImage-upstream-svmfix (6.14-rc7 + the committed SVM vectoring fix, same config); TIMEOUT 2700, 2 in parallel (open3/jobs_x6m.txt). Runner: open3/run_x6.sh.
  - Readings, fixed now:
    - Any class M or I failure under single-thread TCG, in any arm, is root-caused regardless of arm or p-value; Test D stays held.
    - Class L under single-thread TCG: if S3m upstream also locks up at the same step, the lockup is reported as present without the patch; otherwise root-caused.
    - X6 with 0/36 failures and S3s passing: stability claims in the posts are stated for single-thread TCG with per-arm counts; MTTCG failures are reported as counts with the upstream-kernel controls, cause inside QEMU unresolved. QEMU is named as the cause only if the upstream-kernel controls fail the same way.
    - toggle or fixage failing while fifotog passes, under single-thread TCG: treated as an LRU bug (fix in ~/projects/kvm, then the affected-tests rule below).
  - Affected tests (reviewer item 5): a fix in sweep or aging code reruns the leaf/parent parts of U3, I, R4, plus S, X6 and D; a fix only in the policy-switching path reruns S and the X6 toggle arm.
  - Test D: held. It runs only if X6 toggle and fixage have 0 failures; its TCG threading mode is decided and logged here before any D data.
  - U3, R4 and I ran under MTTCG with every run PASS, and the policy-independent counts matched Test U exactly per seed; they are kept.
- 2026-10-22 02:02 AMENDMENT 6 REVISION (reviewer round 8; still no X6, S3s, S3m or spot-check data). Replaces the matching parts of Amendment 6.
  - CORRECTION and protocol deviations. Amendment 6 said U3, R4 and I ran under MTTCG; run_phase1.sh shows: I ran with QEMU_ACCEL=tcg,thread=single, as pre-registered. U3 ran through vmrun.py with job accel "tcg" and --smp 1 (MTTCG with one vCPU). R4 ran with QEMU_ACCEL=tcg and -smp 4, which QEMU 8.2.2 runs as MTTCG for an x86 guest on this host. The plan promised single-thread TCG for U3 and R4, so both are protocol deviations; open2's R' also ran with -accel tcg (same deviation). REPORT.md and the posts must not describe U3 or R4 as single-thread runs.
  - Spot checks (single-thread TCG, system QEMU 8.2.2): U3: 6 boots, the first v1 job of each cell (u3spot-v1-s1020-scan-128, s1016-hot-64, s1008-scan-32, s1009-hot-32, s1009-scan-64, s1002-hot-128; open3/jobs_u3spot.json, vmrun.py -n 2 --smp 1 --mem 3G). Pass = pf_taken equal to manifest_v.csv for all five policies in every boot. R4: the 5 t7ctl plans (-M 0) as r4spot-*, same kernel (bzImage-camp-3l-nokasan), MEM 8G, SWAP_MB 1024. Pass = mmu_recycled, hot_zaps and pf_taken equal to the t7ctl logs. A mismatch in either means that whole test reruns under single-thread TCG before any post uses it.
  - S3 under single-thread TCG (same pool): s3s-age1, s3s-age2, s3s-up (s3r-upstream on bzImage-upstream-svmfix), s3s-lru0; TIMEOUT 10800. Upstream has no min_alloc_pages knob and never evicts in this suite: an upstream lockup rules out eviction as the cause, an upstream pass rules out nothing. s3s-lru0 is the comparator for the patched kernel running FIFO. Revised class-L reading: a lockup under single-thread TCG is reported as present without the patch only if s3s-up locks up at the same step; if s3s-lru0 locks up but s3s-up does not, the patched kernel's FIFO path is suspect and is root-caused; otherwise root-caused.
  - Manipulation check: every S3 plan (s3r-*) starts with reads of /sys/module/kvm/parameters/tdp_mmu and /sys/module/kvm_amd/parameters/npt. s3m-tdp boots are void unless both read Y; all other S3 boots are void unless both read N.
  - Stress dose: X6 runs lru_stress_test with -d 480 (S1 and X5 used -d 120) because each vCPU gets about a quarter of one host CPU under single-thread TCG. REPORT and posts give per-arm iters0-3 next to S1's (S1 toggle s1: about 50,000-54,000 per vCPU), and the posts give that work next to the failure count.
  - Test D splat handling, to be restated in the D-mode entry: whatever TCG mode D uses, the halt rules apply to every splat, including an MTTCG-only class I splat (IF-clear IRQ frame); D resumes only after a written diagnosis here names the class.
  - NEW S3q (QEMU version A/B, MTTCG -smp 4): the user installed QEMU build dependencies; QEMU 11.1.2 was built from the release tarball (GPG signature good, key CEACC9E15534EBABB82D3FA03353C9CEF108B584) into /tmp/qemu-11.1.2, x86_64-softmmu only. Smoke boot q11smoke: L1 boots, /dev/kvm present, tdp_mmu=N npt=N, and the TCG-unsupported CPUID feature list for -cpu EPYC,+svm equals 8.2.2's (xsavec, misalignsse, osvw, topoext, fxsr-opt, nrip-save). Boots, 2 each: q11-age1 (s3r-age1, bzImage-camp-3l) and q11-up (s3r-upstream, bzImage-upstream-svmfix) on QEMU 11.1.2. Comparators on QEMU 8.2.2 MTTCG: the two S3 suite boots of Amendment 5 (both locked up in demand_paging_test at about 400 s) and s3m-up (2 boots). Reading: if all 4 QEMU 11.1.2 boots pass while the 8.2.2 boots of the same kernel lock up, the lockup is attributed to QEMU 8.2.2 MTTCG and reported as absent in 11.1.2; if any 11.1.2 boot locks up, newer QEMU does not resolve it and the single-thread results decide. Either way S3q is diagnostic; the stability claims rest on X6 and S3s.
  - Pools (open3/run_x6.sh): single-thread run.sh pool 8 slots (open3/jobs_x6s.txt: S3s first, then r4spot, then X6 shuffled "x6"); vmrun.py 2 slots for u3spot; MTTCG pool 2 slots (open3/jobs_x6m.txt: q11 and s3m interleaved, r1 then r2). Host guest threads: 10 + 8 = 18 (cap 18).
- 2026-10-22 02:04 S3q READING REVISED (reviewer round 9; no S3q data exists). Replaces the S3q reading in the Amendment 6 revision.
  - Comparison uses only 8.2.2 MTTCG boots that run concurrently, interleaved in the same queue: s3m-age1 (NEW, 2 boots: s3r-age1, bzImage-camp-3l, the same kernel and plan as q11-age1), s3m-up, s3m-lru0, s3m-tdp (2 each). Amendment 5's two S3 lockups (phase-1 load) and the 2026-09-27 logs (accel not recorded) are not counted. MTTCG queue order per round: q11-age1, s3m-age1, q11-up, s3m-up, s3m-lru0, s3m-tdp; round 1 then round 2 (12 boots, open3/jobs_x6m.txt).
  - Signature S3-L ("same step"): a hard lockup during demand_paging_test -u MISSING, with or without a following soft lockup waiting on an IPI. A spinlock recursion is class I, not S3-L; a lockup in another test is a different step.
  - Attribution: the S3-L lockup is attributed to QEMU 8.2.2 only if at least one 8.2.2 boot without the patch's shadow-MMU code in use (s3m-up, which never evicts, or s3m-tdp, which uses the TDP MMU) shows S3-L. If s3m-up and s3m-tdp both pass while the camp-3l shadow-paging boots show S3-L, an 11.1.2 pass clears nothing (changed timing can hide a race) and the class-L root-cause continues.
  - Reporting: counts only, "k of n concurrent 8.2.2 boots vs j of 4 on QEMU 11.1.2", no significance claim. The posts name QEMU only when the attribution condition holds.
- 2026-10-22 02:04:50 X6 / S3s / S3q / S3m / u3spot / r4spot launched (open3/run_x6.sh, pid 974765); reviewer round 9 conditions applied in the 02:04 entry.
- 2026-10-22 02:05 r4spot done: 5/5 controls equal t7ctl exactly in mmu_recycled, hot_zaps and pf_taken (fifo128 3318/442/10396, clock128 3024/195/6404, leaf128 2688/0/2925, parent128 2961/144/5592, fifo2048 0/0/2). Single-thread TCG reproduces R4's MTTCG controls; R4 is kept (protocol deviation stays logged).
- 2026-10-22 02:14 u3spot done: 6 boots, 30 tests, all PASS, pf_taken equal to manifest_v.csv in 30/30 (all five policies in every cell). Single-thread TCG reproduces U3; U3 is kept (protocol deviation stays logged).
- 2026-10-22 02:17 AMENDMENT 7 (X6 stopped; no X6b data exists). The X6 boots were stopped at 02:15:25 by PID (S3s, S3q/S3m and the spot checks were not touched) after the first 5 finished X6 boots all failed lru_stress_test's progress check.
  - Classification of every X6 boot (logs in open3/x6a/), done from the signatures: finished, "vCPU n made no progress in 30 s": fifotog-s9, fixage-s7, fixage-s12, fixage-s14, toggle-s11. Killed mid-run, unread before the kill: fifotog-s14, fixage-s8, toggle-s10, toggle-s6. The only kernel warning in any of the 9 logs is the boot-time SRSO message; no class M or class I signature, no lockup.
  - Diagnosis: harness artifact. The guest reports progress once every sync_every = 2000 iterations. Under single-thread TCG a vCPU completes about 2,000 iterations per 30 s (S1 under MTTCG: about 13,500), so a 30 s window holds about one report. At each failure the lagging vCPU was exactly one report behind the other three (fixage-s7: all four still at 0 at t=30, during start-up). These 5 boots are reported as aborted by the harness artifact and are not counted as class L.
  - Fix: lru_stress_test gains -w (progress window in seconds; default 30, so S1 behaviour is unchanged). Patched in ~/projects/kvm (uncommitted) and rebuilt to /tmp/kvmval/selftests (previous binary kept as lru_stress_test.w30-oct09).
  - Test X6b replaces X6: lru_stress_test -v 4 -d 720 -w 240 -s N. A 240 s window holds about 8 reports per vCPU (S1: about 6.75 per 30 s window). 720 s gives about 51,000 iterations per vCPU at the rate seen in fixage-s12 (S1 toggle s1: 50,000-54,000). Everything else as X6 (bzImage-camp-3l KASAN, -smp 4, thread=single, kvmmmu tracing, TIMEOUT 3600, arms toggle/fifotog/fixage, seeds 4-15). Tags x6b-*, 36 boots shuffled with RNG seed "x6b" (open3/jobs_x6b.txt), 6 in parallel while the 4 S3s boots run (open3/run_x6b.sh; 4 + 6 single-thread + 2x4 MTTCG = 18 threads).
  - FAIL = class M, I or L. "made no progress in 240 s" is class L. Any other lru_stress_test assertion is classed from its message before the arm is read; a data or tag mismatch is class M. Readings, Test D rule and affected-tests rule as in Amendment 6 and its revision, with X6b in place of X6.
- 2026-10-22 02:21 AMENDMENT 7 CONDITIONS (reviewer round 10; no X6b data exists). Added to Amendment 7.
  - The 9 X6 boots count as neither pass nor fail; X6b's identical seeds replace them. If any X6b boot fails with "made no progress in 240 s", REPORT.md reopens the 5 X6 progress-check aborts as possible class-L failures instead of excusing them.
  - Crash-site tally: REPORT.md tallies every MTTCG class-M crash by site (MMU walk, vcpu_enter_guest stack, scheduler, other) and by arm, including the upstream, tdp and lru0 controls. Known so far: s1-toggle-s3 (sptep ffff88800003ae00 in FNAME(fetch), MMU walk) and s3m-age1-r1 (raw read of 0x3ad00 in vcpu_enter_guest, Oops RIP in ioctl_has_perm, stack-protector failure in vcpu_enter_guest; vcpu_enter_guest stack), both bzImage-camp-3l with lru_age on.
  - Reading: if KVM-code class-M crashes appear only in boots with lru_age on, the posts may not describe the MTTCG failures as unrelated to the LRU code until the 0x3a signature is root-caused.
  - Optional check: whether 0x3ad00 / 0x3ae00 match an L2 selftest guest address (GDT, IDT, stack, ucall page).
- 2026-10-22 02:21:15 X6b launched (open3/run_x6b.sh, pid 985515); reviewer round 10 conditions in the 02:21 entry.
- 2026-10-22 02:23 Crash-site note, s3m-age1-r1 (no design change): bzImage-camp-3l = /tmp/kvm-build-knob (cmp identical); CONFIG_KASAN_STACK=y, STACKPROTECTOR_STRONG=y. The KASAN read is vcpu_enter_guest+0x11ea: `mov -0xe8(%rbp),%rdi` right after the `sti` that follows handle_exit_irqoff, i.e. the reload of &vcpu->stat.exits (lea 0x1788(%rbx), stored to -0xe8(%rbp) at +0xfe6 before the inner run loop). The slot held 0x3ad00 instead of the kernel pointer, and the stack-protector check of the same frame failed. Between the store and the reload only kvm_x86_vcpu_run (svm_vcpu_run, VMRUN) and handle_exit_irqoff run, with IRQs disabled; neither reaches the shadow-MMU eviction code, and an out-of-bounds stack write by instrumented C would have been reported by KASAN_STACK first. A second vCPU task (PID 84, CPU 3) oopsed at the same moment in ioctl_has_perm. Site class: vcpu_enter_guest stack. 0x3ad00 is not a ucall slot address for 4 vCPUs (slot bases would not be page aligned) and is not page aligned itself, so it is not a GDT/IDT page base; the L2-address check is otherwise inconclusive.
- 2026-10-22 02:28 AMENDMENT 8 (reviewer round 11; efficiency only, no change to classes or readings).
  - Early kill: neither panic path reliably fires under MTTCG (q11-up-r1: hardlockup_panic did not panic at 448 s; softlockup_panic did at 842 s), so failed boots can idle until TIMEOUT. A boot whose log already holds a class M, I or L signature (Amendment 6 definitions) and whose log mtime is at least 10 min older than `date` is killed: one exact qemu PID per kill, PID re-checked against its initramfs tag immediately before the kill, never a pattern match; afterwards the other pools' VM counts are confirmed. REPORT records "killed early: <signature>, log mtime <t1>, kill <t2>". Classes come only from the log as it stood before the kill; a boot with several classes counts in each. Applies to every pool; never to a boot without a signature. The remaining MTTCG jobs keep the pre-registered n (they feed the crash-site tally; stopping early would be optional stopping).
  - S3q reading (round 11 item b): the Amendment 6 revision branch "any 11.1.2 boot locks up: newer QEMU does not resolve it; the single-thread results decide" holds (q11-up-r1). The 02:04 attribution clause names only 8.2.2 boots and is not stretched. Permitted statement for REPORT and posts: the same lockup (S3-L) occurred on the unpatched upstream kernel under QEMU 11.1.2 MTTCG, so it does not depend on the patch; whether QEMU or upstream KVM/userfaultfd causes it stays unresolved and QEMU is not named. REPORT also records q11-age1-r1 as class I on 11.1.2 (the interrupt anomaly persists in the newer QEMU).
  - s3m-age1-r1 crash-site wording for REPORT: consistent with SVM entry/exit state handling, not established. Correction to the reviewer's RBP inference: the uninstrumented `mov -0xe8(%rbp),%rdi` completed (KASAN reported at the following check), so RBP was valid and the frame slot itself held 0x3ad00. The CPU 3 Oops (PID 84, RIP ioctl_has_perm, "#PF: supervisor read ... PMD 0") faulted on the same address 0x3ad00: two vCPU tasks met the same bogus value at the same moment.
  - 2026-10-22 02:28:11 killed early (Amendment 8): q11-age1-r1, qemu pid 975025, signature "BUG: spinlock recursion on CPU#0, mmu_stress_test/80" (class I) at 377 s, log mtime 2026-10-22 02:11:09. Before the kill all four vCPU threads were runnable at about 88% of a host CPU each (about half system time) with 20,000-24,000 voluntary context switches per second each; QEMU was executing guest code, not blocked. A running 8.2.2 MTTCG boot (s3m-up-r1) showed about 4,000 per second at 94% (different QEMU version and guest state; observation only).
- 2026-10-22 02:29:07 Amendment 8 watcher started (open3/watch_a8.sh; kills logged to open3/earlykill.log; exits to notify on the first signature in any single-thread boot).
- 2026-10-22 02:35 Addition to the 02:23 crash-site note and the Amendment 8 wording (reviewer round 12; checked against /tmp/kvm-build-knob/vmlinux). Two vCPU tasks met 0x3ad00 at the same moment: CPU 2 (PID 82) in a vcpu_enter_guest stack slot (site class: stack), CPU 3 (PID 84) via file->f_security in ioctl_has_perm (site class: slab, file pointer unverified). ioctl_has_perm+0x17b is `mov 0x0(%r13),%esi` with r13 = *(file + 0x128) (f_security) + selinux_blob_sizes.lbs_file and r14 = the struct file pointer; there is no stack reload in that path. On CPU 2 two words of the same frame were corrupt, 0xb0 bytes apart: the spill slot at rbp-0xe8 and the canary at rbp-0x38, on either side of the KASAN-guarded locals (rbp-0xb0 to rbp-0x51). No earlier KASAN report preceded CPU 2's ("Not tainted"); the command line has no kasan_multi_shot, so CPU 3's later checks prove nothing. REPORT reading unchanged: consistent with SVM entry/exit state handling, not established. The earlier phrase "on different stacks" is withdrawn.
- 2026-10-22 04:25 RESULTS (classified from signatures by open3/classify_a8.py, arm read afterwards; all pools finished, last boot 04:15:58; watcher exited 04:20:42).
  - S3s (single-thread TCG, QEMU 8.2.2): s3s-age1, s3s-age2, s3s-lru0, s3s-up each 12/12 tests PASS, "ALL DONE fails=0", read-backs N/N. demand_paging_test -u MISSING passed 3 times in each boot. No class M, I or L.
  - X6b (single-thread TCG): 35/36 PASS (toggle 11/12, fifotog 12/12, fixage 12/12); no "made no progress in 240 s" anywhere, so the 5 X6 progress-check aborts stay excused (round 10 condition). Per-vCPU iters of passing boots: 30,000-64,000 (S1 toggle s1: 50,000-54,000). FAIL x6b-toggle-s10: class M (+ no ALL DONE), "BUG: KASAN: null-ptr-deref in mask_ioapic_irq+0x40/0x130" at 12.29 s of L1 boot, CPU 0, swapper, during setup_IO_APIC, before SMP bring-up, before init ran and before any VM existed; lru_stress_test never started. The watcher missed it (boot crashed and exited between two 60 s polls; the watcher only scans running boots).
  - x6b-toggle-s10 root cause (vmlinux /tmp/kvm-build-knob): mp_irqdomain_alloc() stores irq_data->chip = &ioapic_chip at +0x3ca (`movq $ioapic_chip,0x18(%r13)`) and irq_data->chip_data = data at +0x3db (`mov %rbx,0x30(%r13)`), with interrupts enabled (late_time_init runs after local_irq_enable). Legacy ISA IRQ descriptors already use handle_level_irq from init_ISA_irqs. The interrupted frame shows RIP __asan_store8+0x0 with return address mp_irqdomain_alloc+0x3db and R13 = ffff88810012a028; the Oops shows mask_ioapic_irq with RBX = ffff88810012a028 and chip_data (0x30) = NULL. A legacy interrupt arrived between the two stores; handle_level_irq called the new chip's irq_mask = mask_ioapic_irq, which dereferenced NULL chip_data. Upstream race in arch/x86/kernel/apic/io_apic.c; the patch changes no file under arch/x86/kernel or kernel/irq (git diff 4701f33a..HEAD empty there). KASAN's __asan_store8 call between the two stores widens the window; TCG delivers interrupts at block boundaries. One web search found no upstream fix. Not a KVM or LRU failure.
  - S3m / S3q (MTTCG -smp 4): 12/12 boots failed. QEMU 8.2.2 (8 boots): s3m-age1-r1 M (KASAN, mmu_stress_test) + L; s3m-age1-r2 L (silent in mmu_stress_test until TIMEOUT, qemu exit 124); s3m-up-r1 I (spinlock recursion, demand_paging_test) + L (hard LOCKUP, demand_paging_test) killed early; s3m-up-r2 L (hard LOCKUP, demand_paging_test); s3m-lru0-r1 I (spinlock recursion, mmu_stress_test) killed early; s3m-lru0-r2 L (hard LOCKUP, demand_paging_test); s3m-tdp-r1 (read-backs Y/Y) no kernel signature, ALL DONE fails=6: mmu_stress_test 3/3 and demand_paging_test 3/3 FAIL with L2 "exit_reason=SHUTDOWN" / non-SYNC ucall, the other 6 tests PASS; s3m-tdp-r2 (Y/Y) QEMU exited 0 at 3.5 min mid-mmu_stress_test with no console output (L1 reset under -no-reboot), counted L by the "no ALL DONE" definition. QEMU 11.1.2 (4 boots): q11-age1-r1 I and q11-age1-r2 I (spinlock recursion, mmu_stress_test, both killed early), q11-up-r1 L (S3-L), q11-up-r2 L (hard LOCKUP in mmu_stress_test). All read-backs N/N except tdp Y/Y.
  - S3-L (hard lockup during demand_paging_test -u MISSING): 3 of 8 concurrent 8.2.2 boots (s3m-up-r1, s3m-up-r2, s3m-lru0-r2) vs 1 of 4 on QEMU 11.1.2 (q11-up-r1). Any failure: 8 of 8 vs 4 of 4.
  - Early kills (Amendment 8): q11-age1-r1 (by hand 02:28:11), s3m-up-r1 02:44:05, s3m-lru0-r1 02:44:15, q11-age1-r2 03:00:27 (open3/earlykill.log; each signature verified as spinlock recursion).
- 2026-10-22 04:26 R4 final: the two fifo128 reruns (s117, s151) passed (fails=0); analyze_t7.py on the full set: n = 64 per arm, 0 excluded, all -M 0 controls out_hot = out_cold = pswpout = 0. Output open3/analysis_t7_final.txt (no rule change).
- 2026-10-22 04:32 AMENDMENT 9 (reviewer round 13; no s10r or D data exists). The D gate was changed after the X6b data was seen. Under the strict Amendment 6 gate ("X6b toggle and fixage have 0 failures") x6b-toggle-s10 would have blocked D. REPORT states both facts.
  - Gate exception, mechanical and symmetric: a boot whose first signature appears before "Unpacking initramfs" on the console (selftest boots; for D's disk-booted L1, before "Run /sbin/init as init process") ran no arm-specific code: all arms use the same bzImage-camp-3l and the same -append, and the arm's plan is read only by /init. Such a failure does not block D. It applies to every test and arm and changes only the D gate: x6b-toggle-s10 stays a FAIL in every tally and in both posts. (Reviewer scan: toggle-s10 is the only pre-initramfs crash in the 400 most recent logs; mask_ioapic_irq appears in no other log, camp and Redis included.)
  - Seed rerun x6b-toggle-s10r: plan x6b-toggle-s10, bzImage-camp-3l, tcg,thread=single, TIMEOUT 3600, same kvmmmu tracing. It counts fully: any class M, I or L (or a data/tag mismatch) blocks D, with no further reruns.
  - S3q wording for REPORT and posts (counts beside the full MTTCG tally, never alone): the demand-paging lockup (S3-L) occurred under multi-threaded emulation on the patched kernel with the LRU policy off and on the unpatched kernel, on both QEMU versions (3 of 8 boots on 8.2.2, 1 of 4 on 11.1.2); boots with the LRU policy on failed earlier, in another test; it occurred in 0 of 4 single-threaded boots, which at the multi-threaded rate (4 of 12) happens by chance about 20% of the time. Amendment 6 caveat kept. QEMU is not named as the cause.
- 2026-10-22 04:32 TEST D MODE (logged before any D data; restates Amendment 6 revision's D splat handling).
  - Primary: MTTCG as pre-registered. redis_camp.py with ACCEL=tcg,thread=multi passed explicitly, 4 L1 instances, KERNEL=bzImage-camp-3l, runs/d1.json (224 runs, "d1" order), manifest open3/manifest_d1.csv, watcher open3/redis_watch.sh. Each L1's accel string is recorded from /proc/<pid>/cmdline into open3/d_accel.log.
  - Switch to single-thread, decided now: D restarts from scratch under single-thread TCG if (1) the watcher halts for any reason: an l1splat header (including lockups and RCU stalls), any L2 corruption pattern, KVM_INTERNAL_ERROR, or two consecutive check errors; or (2) at the end of the MTTCG batch, rule (d) holds (Fisher-Freeman-Halton exact test of failed vs clean runs across the 4 policies, pools pooled, p < 0.05; open3/fisher_d.py). No diagnose-and-resume under MTTCG.
  - Restart settings: the same 224 runs in the same "d1" order, renamed d1s-* (runs/d1s.json, batch d1s), ACCEL=tcg,thread=single, 8 L1 instances (at most 8: 8 x 8G against 74 GB free), manifest open3/manifest_d1s.csv, its own D_STARTED_S and watcher log. Any aborted MTTCG data goes into the REPORT with a per-policy tally, never into the table. Under single-thread, every halt rule of Amendments 1-4 applies, and a halt pends a written diagnosis here (no further mode switch).
  - IOAPIC signature, pre-declared: an L1 serial whose only splat is "KASAN: null-ptr-deref in mask_ioapic_irq" with mp_irqdomain_alloc in the following stack lines, before "Run /sbin/init as init process", is logged (IOAPIC-EXEMPT in the watcher log) and handled by rule (b). It neither halts nor switches.
  - The watcher's halt path powers off ports 10200-10207 (was 10200-10203).
  - Order: x6b-toggle-s10r first; D starts only if it passes and open3/D_GO exists (runner open3/run_d2.sh).
- 2026-10-22 04:35 TEST D MODE addendum (no s10r or D data exists).
  - Single-thread restart only: per-run timeout RUN_TMO=21600 s (ssh timeout RUN_TMO + 1800). redis_camp.py now reads RUN_TMO (default 7200, the MTTCG value, unchanged; backup redis_camp.py.bak-a9). Reason: the slowest published MTTCG run took 1,177 s, and single-thread TCG is expected to be several times slower. STALL_S (600 s of unchanged pf_taken) and BOOT_S (5400 s) stay as they are.
  - Watcher changes (backup redis_watch.sh.bak-a9): PORTS (10200-10207) and HALTF variables; IOAPIC exemption as declared. Back-tests in CHECK_ONLY mode: x6b-toggle-s10's console as an L1 serial gives IOAPIC-EXEMPT and no halt; s1-toggle-s3's console gives HALT; the published manifest with an early D_STARTED flags exactly r1-p96-parent-r8 and l1-i3-b2-0928163543.serial, as in Amendments 3-4.
  - fisher_d.py check: 2x2 [[3,1],[1,3]] gives p = 0.4857 (scipy fisher_exact: 0.4857); published manifest (failed 1/3/1/4 of 57/59/57/60) gives p = 0.46.
  - run_d2.sh: s10r first, classified by classify_a8.py (pass = 1/1 test PASS, ALL DONE fails=0, no class; extra WARN lines stop the runner for review). D waits for D_GO; D_GO is created after the reviewer checks run_d2.sh and the watcher diff. Accel per L1 goes to d_accel.log.
- 2026-10-22 04:35:38 run_d2.sh launched (pid 1200968); x6b-toggle-s10r started.
- 2026-10-22 04:42:08 run_d2.sh (real pid 1200971; run_d2.pid had recorded 1200968 in error) stopped by PID after reviewer round 14 found gaps (rule (b) replacements missing, watcher races, Fisher failure definition, untested single-thread settings). Its child x6b-toggle-s10r (run.sh pid 1200975) keeps running unchanged; no D data exists.

## AMENDMENT 10, part 1: single-thread smoke for D settings (2026-10-22 04:44:29)
Reason: reviewer round 14. The D switch path runs 8 L1s under single-thread TCG, which no Redis run has used, so the stall and timeout limits are untested there.
- Change: redis_camp.py passes STALL_S from the environment to run_l2.sh (default 600, as before). Backup redis_camp.py.bak-a10.
- Smoke: runs/dsmoke.json = dsmoke-p0-fifo-r1 and dsmoke-p128-fifo-r1; ACCEL=tcg,thread=single; KERNEL=bzImage-camp-3l; FIRST_INST=8 -n 2 (instances 8 and 9, ports 10208-10209, fresh overlays l1-i8/l1-i9.qcow2); MANIFEST=open3/manifest_dsmoke.csv; RUN_TMO=21600, STALL_S=21600, BOOT_S=21600 so the smoke measures time instead of aborting. Smoke runs are not D data and never enter any D table or test.
- The smoke runs next to x6b-toggle-s10r (one single-thread qemu). Neither touches the other's ports or images.
- Pass: both runs END, no l1_event, no corruption pattern in the run log (redis_watch.sh CORRUPT list), and no splats() hit in the l1-i8/l1-i9 serials. Any other outcome: no D_GO; the failure gets a written diagnosis here first.
- Settings derived from a passing smoke, fixed before D_GO:
  - k = max over the two runs of (smoke ELAPSED_S / published median for the same cell); published fifo medians from manifest_redis.csv END runs: pool 0 = 219.5 s (n=8), pool 128 = 243.5 s (n=16).
  - STALL_S = 600 x ceil(k); RUN_TMO = max(21600, 7200 x ceil(k)).
  - b = the longer of the two smoke L1 boot times ("up in Ns" in campaign.log). BOOT_S = 5400 if 2b <= 5400, else 2b rounded up to a multiple of 600.
  - These apply to the single-thread D restart only. The MTTCG D batch keeps STALL_S=600, RUN_TMO=7200, BOOT_S=5400 as published.
- Limitation: the smoke runs 2 L1s, D single-thread runs 8; the 20-core host leaves headroom, and contention is not measured.
- 2026-10-22 04:45:05 smoke launched (Amendment 10 part 1).
- 2026-10-22 04:49:48 x6b-toggle-s10r ended: classify_a8 'x6b-toggle-s10r sys-8.2.2 tcg,thread=single tests=1P/0F done=0 cls=PASS iters=[100000, 100000, 100000, 98000]', one line (no signature, no WARN). Accel from the qemu command line in x6log/x6b-toggle-s10r.run: -accel tcg,thread=single. Amendment 9 gate passed; D still waits for D_GO.

## AMENDMENT 10, part 2: D runner, rule (b) rounds, result rules (2026-10-22 04:55:31; no D data exists)
Reason: reviewer round 14. run_d2.sh had no rule (b) replacement step, the watcher could exit before its final scan, and fisher_d.py did not use the published failure definition.
- Result rules for every D run (both modes):
  - Halt as RUN (watcher): an l1splat header in the run log's dmesg delta, an L2 corruption pattern (NMI panic after a STALL's injected NMI excused, as before), KVM_INTERNAL_ERROR, or L2_EXITED (with or without a pattern; Amendment 2 removed L2 exits from rule (b)).
  - Halt as SERIAL (watcher): a splat header in an L1 serial written after the mode started, except the pre-declared IOAPIC signature (IOAPIC-EXEMPT; the instance retries its boot, which can idle it for up to BOOT_S).
  - Halt as HARNESS (watcher): BADPARAM, NO_END, HARNESS_*, an empty result, or NO_LOG without an L1 event.
  - Rule (b), replaced: STALL or TIMEOUT; or an L1 event (TSC_MISMATCH, L1_UNRESPONSIVE, L1_EXIT, ssh_rc, host_timeout) with result END, STALL, TIMEOUT or NO_LOG; in both cases only with no splat header and no corruption pattern.
- Harness fix: redis_camp.py marked every NO_LOG row BADPARAM (d.get("param_pool") is None when no run log was copied). Published r1-p96-clock-r1 (ssh_rc=255 L1_UNRESPONSIVE, no log) is this case. Fixed to d.get("param_pool", ""), so such rows stay NO_LOG with their L1 event and fall under rule (b).
- Halt consequences. MTTCG: RUN or SERIAL switches to single-thread; HARNESS or CHECKERR stops the runner (D_ATTN) for a written diagnosis, with no switch. Single-thread: any halt stops the runner pending a written diagnosis. The halt reason is written into D_HALTED / D_HALTED_S.
- Rule (b) rounds (open3/d_rounds.py): after each redis_camp invocation with a clean watcher exit, one replacement (tag + "x") is queued for each rule-(b) run that has none, while its cell has used fewer than 4. Each round runs under its own watcher. Rounds repeat until none are queued. Any non-clean run outside rule (b) at that point is an anomaly (D_ATTN).
- Completeness (d_rounds.py check), after the last round of each mode: every queued run has exactly one manifest row, no unqueued rows, and every cell has its target of clean runs (16; 8 for pool 0) or has used all 4 replacements ("short (cap reached)", reported with the clean runs it has). Otherwise D_ATTN.
- Rule (d) (fisher_d.py): failed = mk_replace.py definition (result != END, or l1_event set, or a corruption pattern in the run log), over every attempt of the mode, replacements included, all pools pooled. The script refuses mixed batches, unknown policies, duplicate tags and empty policies. Check: on the published manifest (batches merged) it counts the same 9 failed runs mk_replace.py replaces, p = 0.4644.
- Watcher exit (redis_watch.sh, backup .bak-a10): the runner touches a per-round done file when redis_camp returns. The watcher samples it before each scan and exits 0 with "EXIT clean" only after a clean scan that began after the file existed, or exits 10 after a halt. The runner waits on the watcher's PID and stops (D_ATTN) on any other ending.
- Runner open3/run_d3.sh replaces run_d2.sh. Its gate step re-checks the s10r pass. After D_GO it refuses to start if a redis_camp process, D data, or a qemu process exists. MTTCG settings are as published (RUN_TMO=7200, STALL_S=600, BOOT_S=5400). The single-thread settings come from open3/d_single.env (exactly one STALL_S, RUN_TMO and BOOT_S line, integers only), written from the smoke per part 1 before D_GO.
- Tests:
  - Watcher in CHECK_ONLY mode, 6 cases: the published manifest gives HARNESS r1-p96-clock-r1=BADPARAM, RUN r1-p96-parent-r8 and SERIAL l1-i3-b2-0928163543; with that row as NO_LOG the HARNESS hit disappears. The x6b-toggle-s10 console gives IOAPIC-EXEMPT and EXIT clean. The s1-toggle-s3 console gives SERIAL. With no findings it exits EXIT clean. Without the done file it keeps running. A missing D_STARTED gives CHECKERR after 2 errors.
  - Halt path with the kill/poweroff/cleanup commands stubbed: exit 10, with the reason in HALTF.
  - d_rounds.py on a synthetic 224-run batch: rounds, the cap, NO_LOG with an L1 event, missing or unqueued rows, and an L2_EXITED anomaly.
  - run_d3.sh dry runs against a stub redis_camp in /tmp/kvmval/d3test, 7 scenarios: all clean; rule (b) rounds; RUN halt then switch then single-thread complete; HARNESS halt to D_ATTN; rule (d) switch with 3 cells short at the cap, then single-thread rounds; switch without d_single.env to D_ATTN; single-thread halt pending diagnosis. One dry run wrote the real runs/d1s.json through a hard-coded path. It was verified identical to the switch's deterministic rename and deleted, and the path is now passed in.
- 2026-10-22 05:04:01 AMENDMENT 10, part 3 (reviewer round 15; no D data exists).
  - Halt precedence under MTTCG: a RUN or SERIAL token anywhere in the halt reason ends the MTTCG phase. If the same reason also has a HARNESS token, the runner writes D_SWITCH_PENDING and stops (D_ATTN). After the harness fix, rerunning run_d3.sh resumes at the single-thread restart, which is then the only way on (no MTTCG resume). A reason that starts with CHECKERR, or has no RUN or SERIAL token, stops with D_ATTN as before.
  - A result of "" with an L1 event is treated like NO_LOG with an L1 event (rule (b)), in the watcher and in d_rounds.py. Example: ssh_rc=255 or host_timeout, where scp copied a partial log with no RESULT= line. The partial log is still scanned, so splats and corruption patterns still halt. "" with no L1 event stays HARNESS.
  - No L1 qemu may outlive a round. redis_camp.py now kills the L1 when its reboot fails after ssh came up. Before touching the done file, the runner checks for any qemu still alive: it powers off the round's ports, waits up to 120 s, runs clean_qemu.sh, and stops (D_ATTN) if a qemu is still alive 60 s later. Every kill is logged in run_d3.log.
  - Each round boots min(4 or 8, runs in the round) L1s, which also lowers IOAPIC exposure in replacement rounds.
  - Dry runs rerun with these changes, 12 scenarios, all as specified. The 7 earlier ones plus: "" with an L1 event (replaced); "" without one (HARNESS stop); RUN+HARNESS (D_SWITCH_PENDING, then the resume completes single-thread); a leaked L1 (a renamed sleep binary standing in for qemu, powered off, swept, logged, then the clean exit); -n capped (4, 3, 1 across the rounds).
- 2026-10-22 05:05:37 Reviewer round 16: D_GO unblocked pending the smoke and d_single.env. The reboot-failure path now calls vm.stop() (ssh poweroff, then kill after 120 s) instead of a plain kill, because the overlays use cache=unsafe. The smoke loaded the 04:44 redis_camp.py, which has the STALL_S passthrough but not the NO_LOG fix or the reboot fix. Both later changes touch only failure paths, so they cannot change the timing of END runs, and the smoke-derived settings stay valid. Rule for D: no other qemu may run on the host while D runs (the runner's sweep would stop D with D_ATTN).
- 2026-10-22 05:05:48 run_d3.sh launched (pid 1214612); it waits for D_GO.

## SMOKE RESULT (Amendment 10 part 1): FAIL, no D_GO (2026-10-22 09:59:53; no D data exists)
- Both smoke runs (QEMU 8.2.2 /usr/bin, -accel tcg,thread=single, -cpu EPYC,+svm, bzImage-camp-3l, fifo) froze in L2 early boot, before any L2 serial output. L1 boots: inst8 1,930 s, inst9 2,185 s. dsmoke-p0-fifo-r1: pf_taken frozen at 2,583 from t=53 s. dsmoke-p128-fifo-r1: pf_taken frozen at 16,316 from t=251 s. Afterwards only interrupt exits (about 1,500 to 2,100 per second); insn_emulation frozen. Neither run can reach END, so the pass rule fails whatever happens before RUN_TMO.
- Shadow MMU state on both L1s: lru_mmu=N, tdp_mmu=N, npt=N, mmu_recycled=0, mmu_shadow_zapped=47, lru_hand_steps=0, lru_fifo_fallbacks=0. No shadow page was ever evicted and the CLOCK code never ran.
- L2 state (L2 QEMU monitor inside L1; symbols from /boot/System.map-6.8.0-142-generic, KASLR slide from the IDT #PF gate vs asm_exc_page_fault; files open3/dsmoke_diag/):
  - inst8 (p0): RIP native_queued_spin_lock_slowpath+0x83 (pause loop on lock->locked), RFLAGS 0x2. Stack: asm_common_interrupt -> common_interrupt -> __common_interrupt -> handle_edge_irq+0x20 -> _raw_spin_lock on irq_desc->lock. That interrupt's hardware frame: RIP apic_ack_edge+0x12 (a test/je, not an exiting instruction), RFLAGS 0x86 (IF=0). Below it: common_interrupt -> handle_edge_irq+0x78 (holding the same irq_desc->lock) -> irq_chip_ack_parent -> apic_ack_edge. LAPIC: ISR 0x30, IRR 0x30, TPR 0x10, LVTT masked.
  - inst9 (p128): same RIP function. Stack: asm_sysvec_apic_timer_interrupt -> tick_handle_periodic -> update_process_times -> hrtimer_run_queues -> _raw_spin_lock_irqsave. That interrupt's hardware frame: RIP __x86_indirect_thunk_rax, RFLAGS 0x46 (IF=0), interrupting ktime_get_update_offsets_now inside an outer timer path holding the hrtimer base lock.
  - In both runs a maskable interrupt was delivered to L2 while L2's RFLAGS.IF was 0, and the uniprocessor L2 then deadlocked on a lock held by the interrupted context.
- Cause not established. Candidates: L0 TCG's SVM emulation (VMCB rflags or EXITINTINFO at #VMEXIT) or L1 KVM's injection decision. Reading QEMU 11.1.2 svm_helper.c/seg_helper.c found no defect: HIF is taken from host eflags before the guest load, VIRQ is gated by guest IF, and event_inj VALID is set and cleared around delivery. The patch's committed svm.c change (EXITINTINFO VALID test in svm_check_emulate_instruction) only affects emulation refusal. Same class as the class I anomaly (interrupt taken with IF clear), here in L2 and under single-thread TCG.
- Consequence: the Amendment 10 single-thread restart cannot run Redis. d_single.env is not written; D_GO stays absent; run_d3.sh keeps waiting.
- 2026-10-22 10:08:57 smoke stopped early (reviewer round 17 agreed): both L2s are in an IF=0 self-deadlock and cannot reach END. Action: poweroff_l1s.sh 10208 10209, wait for redis_camp to return, clean_qemu.sh, check pgrep. Smoke rows are not D data.
- 2026-10-22 10:29:55 smoke cleanup: after poweroff, redis_camp (04:44 build) recorded both rows BADPARAM with ssh_rc=255 L1_UNRESPONSIVE (the NO_LOG fix was not loaded) and booted a second L1 on each instance (pids 1566477, 1566596). Killed by exact PID: redis_camp 1203363, then those two qemus (l1-i8/l1-i9 overlays, smoke only). qemu count afterwards: 0. run_d3.sh (pid 1214612) still waits for D_GO.

## AMENDMENT 11: class-I diagnostic before D, then MTTCG-only D (2026-10-22 10:33:14; no D data exists; reviewer round 17)
Part A. Diagnostic (runs BEFORE D: it tests whether D's own kernel, which carries the svm.c EXITINTINFO change, causes the freeze; about 1 h; not D data)
- Harness: copy in open3/diag/ (redis_camp.py with R=open3/diag, own img/ logs/ runs/). D's harness files are untouched. run_l2.sh = v3 plus an ftrace instance l2y (8 MB/CPU, kvm_exit and kvm_inj_virq, no filter). l2y stops 30 s after pf_taken stops changing (restarts if it moves). On STALL it saves the trace, an explicit L2 stack dump at RSP (128 qwords) and the IDT #PF gate (for the KASLR slide), then the usual v3 capture and NMI.
- Arms, all QEMU 8.2.2 /usr/bin, -accel tcg,thread=single, fifo, pool 0, 2 runs each on fresh L1s (one run per L1), all 6 L1s in parallel. RUN_TMO=7200, STALL_S=600, BOOT_S=5400:
  - up: bzImage-upstream (no patch, no svm.c change), instances 8-9
  - upsvm: bzImage-upstream-svmfix (svm.c change only), instances 10-11
  - c3l: bzImage-camp-3l (D's kernel), instances 12-13
  - On upstream kernels run_l2.sh's lru parameter writes fail harmlessly (no such files); param_pool reads "" (not BADPARAM).
- Freeze = STALL whose HMP captures all show RFL with IF (0x200) clear, plus an L2 stack showing an interrupt frame with IF clear.
- Prediction, written before any run:
  - inst8 in the smoke had vector 0x30 delivered while ISR held 0x30 (TPR 0x10). LAPIC priority rules, which KVM's emulated LAPIC follows, block a second 0x30 until EOI. So the second delivery did not come from a fresh LAPIC acceptance in L1. It came from a reinjection (EXITINTINFO -> svm_complete_interrupts -> kvm_queue_interrupt) or from L0 replaying event_inj.
  - Trace reading for any freeze. Take the last kvm_inj_virq before the hang, and the kvm_exit just before it:
    - "[reinjected]", and that exit's intr_info is VALID (bit 31) with the same vector at a RIP inside the already-entered handler: L0 reported VALID for an event it had already delivered (TCG SVM defect).
    - not reinjected: L1 KVM injected a fresh interrupt while L2 had IF clear or the vector blocked by ISR (KVM LAPIC priority logic, or KVM read IF=1 from the VMCB rflags). Splitting those two needs a further trace.
    - no kvm_inj_virq near the hang: the interrupt reached L2 without L1 KVM injecting it (L0 delivery path).
- Arm reading (2 runs per arm; counts only, no significance claim):
  - up freezes (>= 1 of 2): the freeze occurs without the patch and without the svm.c change. D proceeds on camp-3l.
  - up 0/2 and upsvm >= 1/2: the svm.c change is implicated. D is blocked until it is root-caused (D's kernel carries it).
  - up 0/2, upsvm 0/2, c3l >= 1/2: patch-specific even with CLOCK off. D is blocked until it is root-caused.
  - all 0/2: the smoke freeze did not reproduce (2/2 there). It is reported as intermittent, and D proceeds.
- The diagnostic must finish, with its reading written here, before the posts publish (reviewer 4(i)).

Part B. D under MTTCG only (replaces the Amendment 10 single-thread restart; d_single.env never written)
- A RUN or SERIAL finding no longer halts D. The watcher records it (tag or serial, signature, first-report site class per Amendment 8: kvm-mmu, KVM outside mmu, stack, slab, other).
- Stop rules (D_ATTN):
  - (i) Patch-site rule, any arm: halt if the first report of an L1 splat (KASAN, BUG, Oops, WARNING, lockdep) has its RIP or a call-trace frame in a function defined in arch/x86/kvm/mmu/. The function list is generated with nm from the camp-3l build's arch/x86/kvm/mmu/*.o and frozen before D_GO. It includes kvm_mmu_clock_next, kvm_mmu_clock_hand_unlink, spte_test_and_clear_young, kvm_mmu_sp_test_and_clear_young and kvm_mmu_clock_zap_pages if they exist under those names.
  - (ii) Volume cap: 3 RUN/SERIAL/STALL-I findings in one policy, or 6 in total. IOAPIC-exempt findings do not count. A SERIAL finding counts for the policy of the run in flight on that L1 at the splat, and toward the total only if no run was in flight.
  - HARNESS and CHECKERR halt as before.
- L1 boot ends after any L1-side splat (RUN with a splat in the L1 dmesg delta, or SERIAL). redis_camp checks the instance's current L1 serial before each run start and after each run. On a splat it powers that L1 off and boots a fresh one before the next run, so no run starts on a tainted boot. The run in flight counts as failed for its policy. L2-side findings do not end the boot.
- STALL classes from the run log's HMP captures:
  - STALL-I: IF clear in every capture.
  - STALL-H: IF set and HLT=1.
  - STALL-O: anything else.
  - Replacement under rule (b) is unchanged. Classes are reported per policy. STALL-I counts as a class I finding toward the cap.
  - Classifier check before D_GO: STALL-H for the 6 published halt STALLs, STALL-O for r2-p128-parent-r1, STALL-I for both dsmoke logs.
- Recorded failures (splat, corruption pattern, KVM_INTERNAL_ERROR, L2_EXITED) are not replaced. They count as failed in the per-arm counts and in fisher_d.py, and are excluded from the performance tables. d_rounds.py accepts them; completeness reports such a cell as "short by k recorded failures".
- Rule (d) p < 0.05 is reported as a finding, with no switch.
- Dry runs before D_GO: patch-site halt, cap halt, reboot-after-splat, STALL classifier on real logs, plus the earlier 12 scenarios.

Part C. Posts
- Single-thread evidence (X6b, S3s) came from selftest guests. It does not cover a full Linux L2 taking timer interrupts. REPORT and the posts say so where single-thread stability is cited.
- Smoke sentence (describe, do not attribute): "Under single-thread emulation, 2 of 2 Redis runs froze during guest boot: an interrupt reached the guest while its interrupts were disabled. Both runs used FIFO, so no CLOCK code ran. The cause is not established." It is updated by the Part A reading. It is never "unrelated to the patch" and never names QEMU.
- 2026-10-22 10:37:55 Amendment 11 revision (reviewer round 18; no diagnostic or D data exists):
  - Part A trace validity:
    - l2y buffer_size_kb=65536 (256 MB total of the L1's 8 GB).
    - Every sample writes "DIAGMARK S t=<T> pf_taken=<n>" to l2y/trace_marker.
    - On STALL, per_cpu/cpu*/stats (entries, overrun) are saved with the trace.
    - A trace reading counts only if the saved trace still holds the marker of the last sample before pf_taken reached its final value. Otherwise the trace reading is "none" and only the counts apply.
    - The RSP dump is 512 qwords.
  - Part A decision order (trace first, counts second), declared before any run:
    - A valid trace with reading 1 (L0 reported VALID for an already delivered event) or reading 3 (no L1 injection near the hang), on any arm: the defect is below L1 KVM. D proceeds on camp-3l whatever the counts; the counts are reported.
    - Reading 2 (fresh L1 injection) on an up freeze: an upstream KVM bug; D proceeds. Reading 2 only on upsvm or c3l freezes: D is blocked until root-caused.
    - Freezes in different arms with different readings: D is blocked until root-caused.
    - No valid trace: the count table above applies. Every outcome that implicates the svm.c change or the patch means "blocked, root-cause", never proof.
  - Part A time: about 2,000-2,200 s of L1 boot + 600 s STALL_S + about 4 min of capture per freezing run. A run that does not freeze ends at END or at TIMEOUT 7,200 s, so the worst case is about 2.7 h.
  - Part B reboot-after-splat:
    - The check calls l1splat.splats(path, whole=True), so the excused "sleeping function" uaccess headers never reboot an L1.
    - Added dry run: a serial with only excused headers gives no reboot.
  - Part B patch-site list: open3/mmu_funcs.txt, 344 names.
    - Built with nm --defined-only, t/T symbols of /tmp/kvm-build-knob/arch/x86/kvm/mmu/*.o (the build whose bzImage is byte-identical to bzImage-camp-3l). Names are cut at the first ".", and __pfx_* is dropped.
    - Plus kvm_vm_ioctl_set_nr_mmu_pages, kvm_arch_commit_memory_region (x86.c) and svm_check_emulate_instruction (svm.c). The vmx.c hunk is a module-parameter permission only.
    - kvm_mmu_clock_next, kvm_mmu_clock_hand_unlink and kvm_mmu_clock_zap_pages are inlined into callers that are in the list.
    - sha256 b2df63eb84d55d41ce48cb8bdd52c3d5260b2ea389ef10ec075195bc5472e056, frozen.
    - Matching uses the RIP: line, the function named in the KASAN/BUG/WARNING header, and call-trace frames not starting with "? ".
  - Part B: D's STALL branch is unchanged except for an optional RSP stack dump after the STALL (no l2y trace in D).
- 2026-10-22 10:39:34 Amendment 11 revision (reviewer round 19, final for the Part A launch): each sample writes "DIAGMARK pre t=<T>" to l2y/trace_marker before the dump reads pf_taken, and "DIAGMARK S t=<T> pf_taken=<n>" after it. Validity rule, replacing the earlier wording: the saved trace must hold the pre marker of the last sample whose pf_taken differs from the final value. Otherwise the trace reading is "none".
- 2026-10-22 10:39:34 Part A launched (open3/diag/launch.sh).
- 2026-10-22 10:59:05 Amendment 11 Part B implementation (before D_GO; no D data exists; Part A running):
  - Files (backups *.bak-a11):
    - l1splat.py: refactored. splats() output is identical to the backup on 319 published logs and serials (638 file/mode checks, 0 differences). Adds ioapic_exempt() (moved from the watcher, unchanged), load_funcs() (refuses a list whose sha256 differs from the frozen one) and patch_site().
    - stall_class.py: I/H/O from the "info registers" lines after "STALL:".
    - d_findings.py: the watcher's stateless scan. It writes D_FINDINGS (JSON) on every scan.
    - redis_watch.sh: calls d_findings.py. The halt pkill pattern is now "$R/redis_camp.py run", so it cannot match the diagnostic's redis_camp.
    - camp/redis/redis_camp.py: splat_check() before each run and after it. It writes logs/<boot>.attrib (run=<tag> policy=<p>, or run=- with no run in flight) and logs/<tag>.l1splat, and saves the L1 dmesg to logs/<boot>.dmesg if the L1 is alive.
    - fisher_d.py: failed() adds an L1 splat in the run log and the .l1splat marker. LOGS can be set from the environment.
    - d_rounds.py: recorded failures are accepted. Completeness is chain-based: "short (k recorded failures, cap reached)".
    - run_d4.sh: MTTCG only. It replaces run_d3.sh and drops the s10r gate, which gated only the single-thread restart.
  - Implementation choices not spelled out above:
    - A run counts once toward the cap, whatever mix of RUN, SERIAL and STALL-I kinds it has.
    - Pending serials: a serial with a splat, no .attrib, and a live qemu is not counted yet, because redis_camp attributes it after the run in flight (at most RUN_TMO later). Its patch-site check runs at once.
    - The patch-site rule also halts if the report header names a file under arch/x86/kvm/mmu/ (path_hit). This adds triggers and removes none.
    - The pre-run check runs before a run is dequeued, so no run needs to be put back.
    - If every fresh boot splats, each reboot adds a run=- SERIAL finding, and CAP total halts D at 6.
    - The corruption pattern matches "KASAN:" in the dmesg delta too (unchanged), so an L1 KASAN run has both the l1-splat and corruption kinds. It still counts once.
  - Classifier check:
    - H for the 6 published halt STALLs; O for r2-p128-parent-r1.
    - Deviation: the dsmoke logs have no "STALL:" line, because the smoke was powered off before STALL_S. The check ran on copies (open3/dryrun/stall/) with "STALL:" plus their real captures from dsmoke_diag/hmp.txt appended (port 10208 = inst8 = p0, port 10209 = inst9 = p128). Both give I (RFL=00000002, HLT=0, 2 captures each).
  - Dry runs in /tmp/kvmval/d4test (sed copies; a stub redis_camp that calls the real splat_check), 20 scenarios, all as specified:
    - Earlier scenarios, adapted: all clean; rule (b) rounds; "" with an L1 event (replaced); TIMEOUT (replaced); HARNESS BADPARAM; "" without an L1 event (HARNESS); RUN+HARNESS in the same scan (HARNESS halt to D_ATTN); rule (d) p = 2.1e-06 with a cell short at the cap (reported, D_DONE); leaked L1 (swept, clean exit); -n 4, 3, 1.
    - New:
      - A RUN finding (non-mmu KASAN, with a "? kvm_mmu_page_fault" frame) is recorded, not replaced or halted on, and the cell is "short (1 recorded failures)". The "? " frame is not a hit.
      - PATCHSITE fires on a call-trace frame, on a header path only (function not in the list), and on an attributed serial header.
      - CAP fires on fifo 3 (RUN + KVM_INTERNAL_ERROR + STALL-I) and on total 6 (2+2+1 runs plus one run=- serial).
      - 5 findings plus an IOAPIC-exempt serial and an excused-only serial: no halt, an IOAPIC-EXEMPT note, D_DONE.
      - 5 findings plus a no-.attrib serial whose qemu has exited: CAP total 6.
      - One STALL-I is counted and replaced. An attributed serial splat is recorded. An excused-only serial gives no finding.
    - The real redis_camp.worker with a fake L1, 6 cases:
      - A serial splat during a run gives markers, a dmesg capture and a reboot before the next run.
      - A run-log-only splat gives a reboot.
      - An excused-only serial gives no reboot.
      - A boot-time splat is caught before the run (run=-), followed by a reboot.
      - A failed reboot ends the worker with the remaining runs unclaimed.
      - A first-boot splat gives a reboot before any run.
    - d_findings with a live process holding "file:<serial>": pending, then counted once the process exits, then counted under the run once .attrib exists. A serial older than D_STARTED is ignored.
  - Published runs under the new failed(): unchanged (r1 fifo/clock/leaf/parent 1/3/1/3, r2 0/0/0/1, r3 0/0/0/0; 0 rows changed).
- 2026-10-22 11:10:21 Amendment 11 Part B revision (reviewer round 20; no D data exists):
  - Patch-site matching by header type (l1splat.patch_site):
    - Lockup and stall headers (soft lockup, hard LOCKUP, RCU stall): the header function and the first RIP: line (the stuck context) only.
    - Spinlock and rwlock BUGs: call-trace frames from the top down to the first interrupt-entry frame (asm_sysvec_*, asm_common_interrupt, asm_exc_*). No RIP: lines. A recursion raised in CLOCK code still names CLOCK code, because it is the reporting context. An interrupt taken on top of a vCPU thread no longer names that thread's frames. rwlock BUGs are grouped with spinlock BUGs (same debug path, same artifact shape).
    - Every other header (KASAN, Oops, GPF, unable to handle, NULL dereference, kernel BUG at, list corruption, WARNING: CPU, lockdep, others): the header function, every RIP: line and every frame, as before.
    - Frames starting with "? " never count, as before.
  - Report boundary:
    - A KASAN report runs to its closing "=====" line, so its Allocated and Freed stacks count (cap 400 lines).
    - Any other report ends at its first </TASK> or end-trace line (cap 200).
    - Either ends at the next splat header, except an Oops or bare "KASAN:" line before the report's first RIP:/Call Trace: line, which continues it.
    - The old boundary cut "BUG: kernel NULL pointer dereference" at its "Oops:" line 4 lines later, before the RIP:. Check: open3/dryrun/ps/nullderef-first.log (s1-toggle-s3.log sliced so that the NULL dereference with RIP paging64_page_fault is the first report). Old boundary: 4 lines, no RIP:, not kvm-mmu, so a patch-site report would have been missed. New boundary: 99 lines, kvm-mmu.
  - Regression fixture, every file in /tmp/kvmval/logs with a report (32: 23 .log, 9 .out twins), whole=True:
    - s3m-up-r1, q11-up-r2 and t4-toggle-s2 move from kvm-mmu to other.
    - orig-memslot3 (.log and .out) and s1-toggle-s3 stay kvm-mmu.
    - The other 26 keep their class (clock.log and s3m-age1-r1 kvm-other, the rest other).
  - After a halt:
    - CAP or PATCHSITE: D ends until the cause is found. No resume.
    - HARNESS or CHECKERR: fix the harness, then resume. Completed manifest rows and claims are kept, no finished run is rerun, and no data is discarded. The resume code is written only if needed. run_d4.sh refuses to start on existing d1 data, so a resume needs a reviewed resume path.
  - reboot() now uses the first boot's loop (3 attempts), so one IOAPIC-race boot after a splat does not end the instance. Worker test with a fake L1: one failed reboot, then the next attempt boots and runs continue. Three failed reboots end the worker with the runs left unclaimed. A failed first boot is retried.
  - All 20 d4test scenarios rerun with these changes, with the same results.
- 2026-10-22 11:12:39 Amendment 11 Part B revision (reviewer round 21; no D data exists):
  - IRQENTRY now matches only reliable frames: `\]\s+asm_(sysvec_|common_interrupt|exc_)`. A stale "? asm_*" line can no longer end the spinlock/rwlock frame walk, so "? " frames never count in any role.
  - Fixture open3/dryrun/ps/rwlock-stale-irq.log: a "BUG: rwlock recursion" report with "? asm_sysvec_apic_timer_interrupt" above reliable kvm_mmu_zap_oldest_mmu_pages and kvm_mmu_page_fault frames. New pattern: kvm-mmu. Old pattern: other (a miss).
  - rwlock grouping kept. kvm->mmu_lock is an rwlock_t on x86-64 (KVM_HAVE_MMU_RWLOCK) and the build has CONFIG_DEBUG_SPINLOCK=y, so a CLOCK-code mmu_lock recursion prints "BUG: rwlock recursion".
  - The 33-report fixture is unchanged: kvm-mmu for orig-memslot3 (.log, .out), s1-toggle-s3 and nullderef-first; kvm-other for clock.log and s3m-age1-r1; other for the rest.
  - Reviewer: aligned for D_GO on the Part B code. D_GO still waits for the Part A reading.
- 2026-10-22 12:08:42 Amendment 11 Part A reading (diagnostic finished 11:56:41; no D data exists):
  - Runs: all 6 ended STALL. Traces copied off the powered-off overlays (diag/extract.sh: qemu-img convert, debugfs) and read with diag/diag_read.py. L2 stacks symbolized with diag/sym.py: System.map-6.8.0-142-generic from the L1 /boot partition (the only kernel there, so the L2 kernel), KASLR slide from the IDT #PF gate.
  - Validity: all 6 traces hold the "DIAGMARK pre t=<T*>" marker; overrun 0 on every CPU.
  - Freezes (definition above): up-r1, up-r2, upsvm-r1, upsvm-r2, c3l-r1. Every HMP capture has IF clear, and each L2 stack shows a nested interrupt of one vector taken with IF clear inside that same vector's handler, then a spin on a lock or seqcount the outer handler holds:
    - up-r1, up-r2, upsvm-r1, vector 0x30 (IRQ0, edge): outer handle_edge_irq+0x78 (holding irq_desc->lock) -> irq_chip_ack_parent. Nested frame RIP __x86_indirect_thunk_rax, irq_chip_ack_parent+0x0, __x86_indirect_thunk_rax; RFLAGS 0x2, 0x2, 0x16. Inner handle_edge_irq+0x20 -> _raw_spin_lock -> native_queued_spin_lock_slowpath+0x83 (the HMP RIP). up-r1 LAPIC: ISR 0x30, IRR 0x30.
    - upsvm-r2, vector 0xec (LAPIC timer): outer tick_periodic -> update_wall_time -> timekeeping_advance+0x27c, RFLAGS 0x97. Inner timekeeping_advance+0x35 -> _raw_spin_lock_irqsave.
    - c3l-r1, vector 0xec: outer tick_sched_do_timer -> update_wall_time -> timekeeping_advance+0x305 (inside the tk_core.seq write section), RFLAGS 0x46. Inner hrtimer_interrupt -> ktime_get_update_offsets_now+0x2b (the HMP RIP), the seqcount read loop.
  - c3l-r2 is not a freeze: one capture, EIP=0000ff53 EFL=00000046 (real mode, IF clear), no L2 stack (real mode), final pf_taken 95 (early L2 boot).
  - Trace reading, the same in all 5 freezes:
    - The last kvm_inj_virq is a fresh injection of the freeze vector (not reinjected). The exit before it: up-r1 VINTR at handle_softirqs+0x74; up-r2 VINTR at rcu_core+0x61; upsvm-r1 VINTR at __text_poke+0x2b5; upsvm-r2 INTR at srso_return_thunk; c3l-r1 VINTR at free_reserved_area+0xb7. Where the dump reaches the outer interrupt frame (up-r1, up-r2), its RIP equals that exit RIP and its RFLAGS has IF set (0x282, 0x246). L1 injected the outer delivery into an IF-set context.
    - The next exit, 1.5-3.1 ms later with no exit between, reports exit_int_info 0x330 (0x3ec for the 0xec freezes): the same vector, VALID clear, type 3. An EVENTINJ delivery leaves the injected type 0 with VALID cleared (0x00000030 after the other injections in these traces). Type 3 on an external vector is what L0 writes for a delivery that did not come from EVENTINJ (seg_helper.c handle_even_inj). That exit's RIP is the vector's entry stub (irq_entries_start+0x80 or asm_sysvec_apic_timer_interrupt+0x0) in 4 freezes and the spin RIP in upsvm-r1. Every later exit is at the spin RIP.
    - One L1 injection therefore produced two deliveries within one VMRUN. The second, with IF clear and the vector in service, had no L1 injection. This is reading 3's content (the interrupt reached L2 without L1 KVM injecting it; L0 delivery path) and the prediction's second alternative (L0 replaying event_inj).
    - Every type-3 exit with an external vector in the 6 traces follows an L1 injection of the same vector with no exit between: up-r1 9, up-r2 4, upsvm-r1 1, upsvm-r2 4, c3l-r1 11, c3l-r2 0 (plus one 0x308, below). The earlier ones in each trace did not deadlock.
    - c3l-r2: a fresh IRQ 0x8, then the same reinjected (real mode), then the trace's only 0x308 exit, then execution from 0x0 upward through low memory with #PF exits, then a #UD loop at 0xf282 (93,027 UD exits) until the STALL. The same double-delivery signature starts the derailment. Reported; not counted as a freeze.
  - Deviation from the literal rule: the rule takes the last kvm_inj_virq before the hang and reads "not reinjected" as reading 2. Here that injection is the first, legitimate delivery, and reading 2's content (L1 injected while IF was clear or the vector was blocked) is contradicted by the exit RIP and the outer frame's IF. The freezes are classified as reading 3. The literal reading 2 on up freezes gives the same decision (upstream bug; D proceeds).
  - Mechanism, read in the QEMU 11.1.2 source (no 8.2.2 source on the host; the 8.2.2 traces show the same exit_int_info signature): helper_vmrun's SVM_EVTINJ_TYPE_INTR branch sets cs->exception_index = vector and delivers at once through do_interrupt_x86_hardirq, without resetting exception_index. When exit_request is set and no interrupt is processed, cpu_handle_interrupt returns with exception_index unchanged, and cpu_handle_exception delivers the stale vector again through x86_cpu_do_interrupt, which checks neither IF nor the LAPIC; handle_even_inj marks it type 3. Consistent with the traces; no patched build was tested. The single-thread scheduler's periodic kick sets exit_request, which fits the higher rate under single-thread TCG; not measured.
  - Arm counts (freezes/runs): up 2/2, upsvm 2/2, c3l 1/2, plus c3l-r2 (STALL, not a freeze, same signature).
  - Decision: reading 3 on all 5 freezes, one reading in every arm. The defect is below L1 KVM; D proceeds on camp-3l. The count table (up >= 1/2) gives the same.
  - Classifier change before D_GO: stall_class.py also parses EIP=/EFL= captures (real or 32-bit mode). Over the 15 STALL logs only c3l-r2 changes (O to I); the 6 published halt STALLs stay H, r2-p128-parent-r1 O, both dsmoke I. d4test stalli_one and ruleb rerun with the same results.
  - Part C smoke sentence, proposed (posts never name QEMU and never say "unrelated"): "Under single-thread emulation, all 8 Redis runs hung during guest boot, including both runs on the unpatched upstream kernel. In the 6 traced runs, the guest hypervisor injected a timer interrupt once and the guest received it twice. In 5 of them the second delivery arrived while the guest's interrupts were disabled, and the guest deadlocked on a lock or sequence counter that the first handler held." The selftest-guest caveat stays where single-thread stability is cited.
- 2026-10-22 12:16:06 Amendment 11 Part A reading, reviewer round 22: aligned on reading 3 with the declared deviation, the type-3 inference and the classifier change.
  - Type-3 inference, reviewer's added check: L1 cannot leave a type-3 residue on a vector >= 0x20, because KVM injects IRQs as type 0 and helper_vmrun turns an EVENTINJ of type 3 with vector >= 31 into SVM_EXIT_ERR. The reviewer's independent count found 30 type-3 external-vector exits over the 6 traces, each right after a same-vector kvm_inj_virq on the same task with no exit between.
  - Part C smoke sentence, final: "Under single-threaded emulation, all 8 Redis runs hung while the nested guest booted, including both runs on the unpatched upstream kernel. In each of the 6 traced runs, a timer interrupt that the guest hypervisor injected reached the nested guest a second time without a new injection. In 5 of those runs, the second delivery arrived inside the first handler, while interrupts were disabled. The nested guest then deadlocked on a lock or sequence counter that the first handler held." All 6 diag runs hung before L2BENCH_START.
  - REPORT scope guard:
    - The replay mechanism concerns events injected at VMRUN into L2. It is not evidence about the L1-level IF=0 anomalies under MTTCG (the stab1l timer IRQ, the S3 spinlock recursions); those stay unexplained and REPORT does not link them.
    - The replay inference rests on the 0x30 freezes (up-r1, up-r2, upsvm-r1). For the 0xec freezes, vector equality alone does not rule out L1's own LAPIC timer (also 0xec) reaching L2.
  - Launch, in the reviewer's order: run_d3.sh (pid 1214612) killed by PID and gone at 12:14; its EXIT trap touched D_ACCEL_STOP, which run_d4.sh removes at start. run_d4.sh started 12:14:55 (pid 1702464); D_ACCEL_STOP absent; D_GO written; "D_GO seen" 12:15:25; d_accel.log shows -accel tcg,thread=multi for the first L1s.

### 2026-10-22 17:31:34 Amendment 11 Part B results (Test D under MTTCG)

- run_d4.sh ran D from 12:15:25 to 17:23:23 ("D complete under MTTCG"). All 5 L1 boots used `-accel tcg,thread=multi`
  (d_accel.log). Both watchers ended "EXIT clean"; no PATCHSITE, CAP, HARNESS or CHECKERR halt.
- 225 runs: 224 END and 1 STALL-I, d1-p96-parent-r4. Rule (b) replaced it with d1-p96-parent-r4x (END). d1_check.txt:
  complete, no cell short. Rule (d): Fisher-Freeman-Halton p = 1, reported only.
- d1-p96-parent-r4 shows the Diagnostic A freeze pattern during the benchmark: a nested hrtimer_interrupt at IF clear
  inside an outer one, spinning in native_queued_spin_lock_slowpath. No L1 KASAN, lockdep or other splat. No trace was
  taken, so Part C makes no double-delivery claim for this run.
- Analysis (d1an/analysis.txt, d1-p96-parent-r4 excluded): every CLOCK version evicted 20–25% fewer shadow pages than
  FIFO at pools 80 and 96 (all CIs exclude zero); no CLOCK-vs-CLOCK eviction CI excludes zero. pf_taken fell 2–7% at
  80 and 96; at 128 and the default pool no fault CI excludes zero. REPORT.md "Test D" holds the full table.
- Part C: both posts updated with these results and the round-22 smoke sentence; reviewer ALIGNED at round 25.

### 2026-10-22 23:33:43 Amendment 12: QEMU patch test (no runs exist yet)

- Question: does resetting `cs->exception_index` after an SVM EVENTINJ INTR delivery stop the single-thread L2 boot
  freezes of Diagnostic A? The patch (qemu-svm-intr-replay.patch, sha256 16d5ccb531b8602b…) adds `cs->exception_index = -1;`
  after `do_interrupt_x86_hardirq(env, vector, 1)` in helper_vmrun (target/i386/tcg/system/svm_helper.c) of QEMU
  11.1.2. Without it, an exit request makes cpu_exec_loop keep the stale index and cpu_handle_exception delivers the
  vector again, with no IF check.
- Arms, run in parallel, single-thread TCG (`ACCEL=tcg,thread=single`), bzImage-camp-3l, pool 0, fifo:
  - q11: unpatched QEMU 11.1.2 (/tmp/qemu-11.1.2/bin), 2 runs, instances 8-9 (control: does 11.1.2 still freeze?).
  - q11p: patched QEMU 11.1.2 (/tmp/qemu-11.1.2p/bin, same configure options), 4 runs, instances 10-13.
  - Harness: open3/qp, a copy of the Diagnostic A harness with only R changed. RUN_TMO 7200, STALL_S 600, BOOT_S 5400.
    The launcher records each L1's /proc/<pid>/exe.
- Per-run outcome: END, or STALL classified by stall_class.py (I = IF clear in every HMP capture, O otherwise), or other.
- Readings (fixed now):
  - R1, patch supported: q11 has at least 1 STALL-I and q11p has 4/4 END.
  - R2, patch not sufficient: any q11p STALL-I. The replay is then not the whole cause.
  - R3, control not reproduced: q11 has no STALL-I. The design then cannot test the patch; report only.
  - R2 applies whatever the control shows. For a q11p STALL-I, read its l2y trace for the double-delivery signature
    (one kvm_inj_virq, two deliveries, type-3 exit_int_info). Present: the replay persists (patch incomplete or not in
    effect). Absent: another cause.
  - Anything else (a q11p STALL with IF set, boot failures, harness errors) is reported as found, no reading.
- Validity: a run counts only if every qemu pid on its instance shows the arm's /tmp/qemu-11.1.2[p]/bin/qemu-system-x86_64
  and `-accel tcg,thread=single` in qp/qp_exe.log; otherwise it is a harness error.
- Limit: 4 patched runs. The smoke and Diagnostic A hung in 8/8 runs on QEMU 8.2.2 (6 traced), so 4/4 END is unlikely
  without an effect, but this is not a rate estimate.
- Posts: no change is planned from this test. Any post change waits for the result and the user's decision.
- 2026-10-22 23:35:28 Amendment 12 reviewer round 26: patch correct and minimal; 3 wording fixes applied (limit line, R2 precedence + trace check, exe validity rule).
- 2026-10-23 01:27:29 Amendment 12 result: no reading. Validity holds (qp_exe.log: every L1 pid ran its arm's binary with
  tcg,thread=single; inst8 boot 1 failed its ssh setup and boot 2 ran both q11 runs). All 6 runs ended L2_EXITED after
  40-66 s, before any STALL could occur: each run log shows one #PF intercept whose EXITINTINFO is a #DF (intr_info
  0x80000b08) early in L2 boot, then the L2 qemu exits. Same signature in both arms, so QEMU 11.1.2 cannot run this
  nested L2 and the test cannot read the patch. Not an R1-R3 case ("boot failures ... no reading").

### 2026-10-23 01:28:25 Amendment 13: QEMU patch test on 8.2.2 (no runs exist yet)

- Why: Amendment 12 gave no reading, because QEMU 11.1.2 cannot boot the nested L2 in either arm. The freezes came
  from QEMU 8.2.2 (Ubuntu 1:8.2.2+ds-0ubuntu1.18). That package's svm_helper.c, seg_helper.c and sysemu/seg_helper.c
  are byte-identical to upstream 8.2.2 (`diff` against the extracted Debian copies in /tmp/qemu-822-src).
- Source: upstream qemu-8.2.2.tar.xz, sha256 847346c1…, good signature from release key CEAC C9E1 … F108 B584.
  Two extracted trees with identical configure options (--target-list=x86_64-softmmu --disable-docs --enable-slirp
  --disable-werror):
  - u822: unpatched, /tmp/qemu-822u/bin. 2 runs, instances 8-9.
  - p822: patched, /tmp/qemu-822p/bin, qemu-822-svm-intr-replay.patch (sha256 0721cb32b24ee99f…), the same one-line change as
    Amendment 12 at target/i386/tcg/sysemu/svm_helper.c. 4 runs, instances 10-13.
- Everything else as Amendment 12: single-thread TCG, bzImage-camp-3l, pool 0, fifo; a fresh harness copy open3/qp2
  (only R changed); RUN_TMO 7200, STALL_S 600, BOOT_S 5400; qp2/qp2_exe.log records each L1's exe and accel.
- Readings, validity rule, the trace check for a patched STALL-I and the limit: as in Amendment 12, with q11 -> u822 and
  q11p -> p822. R1: u822 has at least 1 STALL-I and p822 has 4/4 END. R2 (any p822 STALL-I) applies whatever the
  control shows. R3: u822 has no STALL-I.
- Limit (added): upstream 8.2.2 differs from the Ubuntu binary in its default machine type (pc-i440fx-8.2 vs
  pc-i440fx-noble-v2; redis_camp passes no -machine), its build options and Debian patches outside the 3 files
  compared. u822 tests whether the freeze reproduces anyway. If it does not (R3), report that upstream 8.2.2 does not
  reproduce the freeze, and draw no conclusion about the patch.
- Launch gate (checked before launch): both bin/qemu-system-x86_64 report 8.2.2 and differ (cmp); both config.status
  lines match apart from paths; each binary's firmware path is its own share/qemu (-L help) with bios-256k.bin present;
  diff -rq of the source trees lists only svm_helper.c plus build-time .pyc files and subprojects/dtc/.git/index.
- Posts: no change planned; any change waits for the result and the user's decision.
- 2026-10-23 01:29:32 Amendment 13 launch gate: all checks pass (output above recorded in this session; reviewer round 28 findings 1-2 applied).
- 2026-10-23 02:59:43 Amendment 13 result: R1, patch supported.
  - Validity: qp2_exe.log shows each of the 6 L1 pids on its arm's binary with tcg,thread=single; one boot each.
  - u822 (unpatched upstream 8.2.2): 2/2 STALL, both class I (stall_class.py). r1 froze at T*=97 s; r2 at T*=158 s
    with the inner frame at native_queued_spin_lock_slowpath+0x83, EFLAGS 0x2. Their l2y traces (qp2/x/, extracted
    with qp2/extract.sh) hold type-3 external-vector residues: r1 3 (0x330), r2 4 (2 x 0x330, 2 x 0x3ec), the same
    signature as Diagnostic A (same grep there: 30).
  - p822 (patched upstream 8.2.2): 4/4 END in 846-2418 s, pf_taken 34,583-34,810, mmu_recycled 0, no other L1 splats
    (sleep splats are the excused handle_fastpath_hlt ones). No traces are saved for runs that do not stall. L2 serial:
    r1 logged 1 soft lockup (swapper/0:1, 22 s, at 142 s, before the benchmark); r2 logged 2 (migration/0:19, 21 s, at
    396 s; redis-benchmark:97, 22 s, at 668 s); r3 and r4 none. No D run had an L2 soft lockup; this test does not
    explain them. END is the criterion and the original freeze was permanent, so R1 stands.
  - u822-r1's manifest other_splats=1 is not a splat: run_l2.sh's grep matched the frame
    "? lockdep_hardirqs_on_prepare+0x134/0x210" in the post-stall sysrq backtrace; l1splat.splats() returns [] for it.
  - Limit as written: 4 patched runs, upstream build rather than the Ubuntu binary; the control reproduced the freeze.
- 2026-10-25 10:51:00 Amendment 13 posts: user chose "update posts". Part 1 gains a paragraph on the QEMU cause and the one-line fix (single-thread, upstream 8.2.2, 4/4 vs 0/2, soft-lockup caveat, not tested under MTTCG, not in QEMU master); Part 2 [^redis-failures] points to it. Reviewer ALIGNED at round 33.
