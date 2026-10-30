Pre-registration: KVM post "What's Still Open" points 2 and 3 (written 2026-10-07, before any data)

Kernel: /tmp/kvmval/camp/bzImage-camp (same MMU code as the campaign; lru_mark_fill knob left at default 1).
Emulation: QEMU TCG x86 EPYC+svm on the aarch64 host, single-thread TCG (-accel tcg,thread=single) for every
boot, because the campaign's stab1s showed MTTCG failures are an emulator race. kvm.tdp_mmu=0 kvm_amd.npt=0.
Policies: fifo (lru_mmu=0), clock (1,0), leaf (1,1), parent (1,2).
Host guest-thread cap: <= 16 concurrent QEMU processes (20 cores).

## Q4: accessed bits shared with host aging (point 2)

New selftest x86/lru_idle_test.c (derived from lru_reuse_test, same xorshift trace, 1 vCPU).
- Guest memory: R regions of 2 MiB (one last-level shadow page each). Only K pages per region are used
  (page index i*512/K), so the host can track every used page with page_idle.
- THP disabled in L1 (transparent_hugepage=never on the cmdline) so page_idle works per 4 KiB page.
- Warmup: guest touches every used page once, then W trace accesses.
- Measured phase: T intervals. Each interval:
  1. Host (vCPU stopped at a ucall, so the run is deterministic): if aging=idle, mark every used page idle via
     /sys/kernel/mm/page_idle/bitmap (this calls KVM's clear_young on the leaf SPTEs, like host reclaim aging).
  2. Guest reloads CR3 (so cleared A bits get set again on the next walk), clears its "touched" bitmap,
     then does A trace accesses (hot/cold mix as lru_reuse_test MODE_HOT), recording each touched page.
  3. Host reads the idle bit of every used page and the guest's touched bitmap.
- Arms: policy {fifo, clock, leaf, parent} x aging {none, idle}. With aging=none the host does not touch
  page_idle at all (reading page_idle also clears young bits).
- Parameters (fixed now): R=256, hot=16, hot_pct=90, K=8, W=50000, T=40, A=5000, pool min_alloc_pages=64
  (the campaign's hot/64 cell). Seeds 1..5, every arm on every seed.

Metrics and predictions:
- Q4a (policy -> host), aging=idle arms only: false_idle = sum over intervals 2..T of
  |touched AND idle| / |touched|. Also split hot/cold pages. Prediction: leaf > clock/fifo/parent,
  because leaf clears leaf A bits on pages it keeps. fifo/clock/parent lose bits only when they zap a page
  (6.14 drops the A bit on zap without marking the folio accessed), and a refault marks the folio accessed
  through GUP. No prediction for fifo vs clock vs parent.
  Also report false_active = |not touched AND not idle| / |not touched| (expected near 0).
- Q4b (host -> policy): pf_taken and mmu_shadow_zapped over the measured phase, aging=idle vs none, per policy,
  per seed. Prediction: fifo, clock and parent identical (they never read leaf A bits; deterministic run),
  leaf higher with aging (its signal gets cleared by the host), i.e. leaf moves toward clock.
- Analysis: per-seed paired differences; the run is deterministic, so n=5 seeds show generality across
  traces, not noise. Determinism check: seed 1, all 8 arms, run twice; counters must match exactly.
  If they don't match, add seeds 6..10 and report 95% t-intervals of the paired % differences.

## Q5: realistic cache sizes (point 3)

Default pool (min_alloc_pages=0): KVM sizes it to 2% of guest pages (1 GiB L2 -> 5242 shadow pages).
A single process can't exceed that easily: one last-level shadow page maps 2 MiB of one address space.
Shadow pages are per address space, so many processes that map the same shared memory multiply the count.
This is how prefork servers and PostgreSQL backends use memory.

Workload "forkshare" (static C program in the L2 initramfs, L2 = 1 GiB, 1 vCPU, Redis harness otherwise):
- One MAP_SHARED anonymous region of S MiB, populated once by the parent. P worker processes fork and each
  does N accesses (one byte per access) with the lru_reuse_test hot/cold mix over the region's 2 MiB regions
  (hot set H regions, 90% of accesses), each worker with its own seed (worker index + run seed).
  Workers run concurrently; the kernel scheduler time-slices them on the single vCPU.
- Size: S=512 MiB (256 regions), P=32 -> up to 8192 last-level shadow pages if all regions get touched,
  1.6x the default pool. Hot set H=16 regions -> 512 hot shadow pages, well inside the pool.
  N fixed after one fifo smoke run so a run takes <= 15 min (smoke data not used in the analysis).
- Control at the same default pool: P=8 (2048 shadow pages, fits) -> expect mmu_recycled ~ 0 for all policies.
- Arms: policy x {P=32, P=8}, n=8 runs per cell (runs differ in seed and scheduling).
- Metrics: mmu_recycled (primary), pf_taken, mmu_shadow_zapped. Median ratio vs FIFO, percentile bootstrap
  95% CI (10k), Mann-Whitney U, same "definitive" rule as the campaign (CI excludes 0 or lies within +-3%).
  One extension of +4 per cell if inconclusive on the primary metric. Non-clean runs (panic, stall,
  KVM internal error) replaced as in the campaign and listed.
- Realistic app (stretch, only if L1 has apt access): pgbench -S on PostgreSQL in L2 with shared_buffers
  sized so backends x shared_buffers/2 MiB > 5242 (e.g. 2 GiB L2: pool 10485; 256 MiB shared buffers x 96
  clients = 12288). n=4 per policy, descriptive only.

## Outputs
Raw logs /tmp/kvmval/open/logs, manifests /tmp/kvmval/open/*.csv, analyses /tmp/kvmval/open/analysis_*.txt,
report /tmp/kvmval/open/REPORT.md. Amendments go below with timestamps, before the affected data is looked at.

## Amendments
- 2026-10-07 23:50 AMENDMENT 1 (after design review; only smoke/pilot data seen, none used in analysis):
  Q5: user decision: forkshare dropped; Q5 workload = PostgreSQL pgbench -S in L2 (real application).
  * Pool size: x86 KVM counts memslots of both address spaces (SMM on by default in QEMU pc, smm=auto), so the
    default pool = 2 x guest pages / 50 (2 GiB L2 -> ~20,971). Kept as is (QEMU default = realistic config).
  * Config (realistic OLTP defaults): L2 2 GiB, 1 vCPU, shared_buffers=512MB (25% of RAM, the usual guideline),
    huge_pages=off, max_connections=110, DB scale chosen so the DB >= shared_buffers, prewarmed (pg_prewarm) before
    the measured phase. Clients C in {100 (default max_connections), 16 (control)}. Demand estimate 100 x 256
    regions = 25.6k > 20.9k; 16 x 256 = 4.1k fits.
  * Pilot (not analysed): fifo with a pinned huge pool (min_alloc_pages=200000) measures real demand
    (mmu_cache_miss, zapped ~ 0) for C=100 and C=16. If C=100 demand < 1.2 x default pool, raise C (up to 200,
    max_connections raised to match) before the main runs; recorded here before main runs start.
  * Counters: harness dumps per-VM stats at START (after prewarm and all clients connected) and at END; analysis uses
    END - START deltas. pgbench -t fixed transactions per client (fixed work), set after the pilot.
  * Design: seeds/reps blocked: each rep launches all 4 policies together in random order; paired analysis by rep.
    n = 8 reps per C. Primary mmu_recycled; Holm over the 3 vs-FIFO comparisons per C. Definitive = %-difference CI
    excludes 0 (ratio CI excludes 1) or lies within +-3%. Report TPS descriptively only (emulated CPU).
  * Each run logs MemAvailable, ShmemPmdMapped (must be 0), /proc/vmstat pgscan/pgsteal deltas in L2.
  Q4 fixes:
  * Refault marks the folio accessed via kvm_release_faultin_page -> mark_page_accessed (not GUP FOLL_TOUCH).
  * L1 setup: compaction_proactiveness=0, compact_memory once before the test (drains LRU batches).
  * Test asserts: every used page reads idle right after marking (else count + report untracked per interval),
    PFNs unchanged across intervals, /proc/vmstat pgmigrate_success/pgscan/pgsteal deltas 0 over the run.
  * Primary Q4a metric = hot-page false_idle (cold pages are zapped under every policy). Decision rule: leaf vs
    each other policy, same sign in 5 of 5 seeds. Same rule for Q4b "leaf changes with aging".
  * Control arm: large pool (min_alloc_pages=2048, no zaps, no sweeps): false_idle ~ 0 for every policy.
  * The touched bitmap and CR3 reload make the trace differ from the camp hot/64 cell; not compared directly.
- 2026-10-08 00:20 AMENDMENT 2 (after Q4 smoke, seed 1, T=6, pool 64 only; smoke not used in the analysis):
  Smoke showed: fifo/clock/parent counters identical with and without aging (as predicted); leaf pf +3.4% with aging;
  hot-only control (-p 100) no faults, untouched_busy == untracked (method clean); hot false_idle 0/640 in every
  policy because at pool 64 hot pages still refault (an eviction batch of ~20 is a third of the pool, so the second
  CLOCK pass evicts hot pages too) and a refault marks the page accessed for the rest of the interval.
  Grid amended so the cell where leaf's clearing can show exists (hot pages kept, evictions still happen):
  * cell P64 (pre-registered): R=256, K=8, pool 64.
  * cells R1024: R=1024, K=2 (2048 tracked pages, same page_idle cost), hot=16, pools 64, 128, 256, 512.
  * control: R=1024, K=2, pool 2048 (no evictions), seed 1, all policies.
  * T=20 intervals (was 40) for every cell, to fit time; A=5000, W=50000 unchanged. Seeds 1..5, 4 policies, aging on/off.
  * L1 plan sets compaction_proactiveness=0 and compact_memory=1 first. Test now also reports pfn_changes and the
    /proc/vmstat migrate/scan/steal delta (must be 0 for a run to count).
  Decision rules unchanged (hot false_idle leaf vs each policy, same sign 5/5 seeds, per cell; Q4b per cell).
- 2026-10-08 02:55 Q4 grid finished (104 boots, 208 runs, all complete; pfn_changes=0 and vmstat delta=0 in every aging run;
  the only boot message matching the splat regex was the SRSO mitigation notice). Determinism check launched as
  pre-registered: seed 1, all 20 (cell, policy) combos except the control, fresh boots (tag suffix -det).
- 2026-10-08 03:25 Determinism check FAILED (first 12 of 20 seed-1 re-runs: none identical; pf_taken differs by 0-2, untracked
  and untouched_busy by up to 2x, small touched_* shifts). As pre-registered: seeds 6..10 added for all 5 cells x 4 policies
  (100 boots); Q4 reported with 95% t-intervals of per-seed paired differences over seeds 1..10, alongside the sign rule
  (now 10/10, or t-interval excluding 0).
- 2026-10-08 03:40 AMENDMENT 3 (Q5; no pgbench data seen yet): single-thread TCG L1s cannot boot the full Ubuntu L2 kernel:
  it deadlocks in early boot (APIC timer IRQ taken while timekeeper_lock is held with IRQs off -> interrupt delivered with
  IF=0, an emulation fault in nested SVM under single-thread TCG; RIP in native_queued_spin_lock_slowpath, 2 of 2 boots).
  Q5 therefore uses MTTCG L1s (-smp 4, as in the Redis campaign, 224/233 clean runs there), with the campaign rule:
  non-clean runs (STALL, panic, KVM internal error, L1 unresponsive) are replaced and listed. Pilot = pilot3 (fifo:
  C=100 big pool, C=100 default pool, C=16 big pool, 200 txns per client).
- 2026-10-08 05:15 Pilot3 result (fifo, 200 txns/client): pg_prewarm failed (extension missing in db "bench"), so shared
  buffers were only partly filled (Shmem 326 MB at end). Peak live shadow pages (cache_miss - zapped): C=16 1.3k,
  C=100 big pool 12.6k, C=100 default pool 12.8k, mmu_recycled 0. Demand < 1.2 x default pool (25.2k), so per rule C is raised
  to 200 (max_connections already 210). Fixes: init now runs "create extension if not exists pg_prewarm" first
  (appended cpio overlay, same image otherwise). Pilot4 (fifo, 100 txns/client to bound runtime): C=200 big, C=200 default,
  C=16 big. Main runs use C in {200, 16} if C=200 default-pool mmu_recycled > 0; else L2 1 GiB / shared_buffers 256MB
  (same 25% rule, default pool ~10.5k) is the next step, recorded before running.
- 2026-10-08 06:15 Pilot4 try 1: all 3 non-clean (C200-big and C16-big KVM_INTERNAL_ERROR during L2 boot at 242 s; C200-def
  L2 double fault in pgbench: kernel %gs access with GS base 0, an emulated-SVM state fault). Logs kept in logs/failed.
  Replaced per rule (re-run, same config).
- 2026-10-08 06:45 Pilot4 try 2: 3/3 KVM_INTERNAL_ERROR (suberror 3, X86EMUL_UNHANDLEABLE_VECTORING: #PF intercept needing
  emulation during event delivery, the nested-emulation artifact also seen in the Redis campaign). All 5 such failures
  happened during the pg_prewarm burst (~600 MB sequential read); pilot3 (prewarm broken) had 0/3. Change: prewarm throttled
  (256-block chunks, 0.5 s pause; same final buffer contents). Pilot5 = 3 short runs (C=16, 5 txns) to test prewarm survival;
  then pilot4 again. Not analysed.
- 2026-10-08 07:00 Pilot5 try 1: throttled prewarm failed (plpgsql.so not in the initramfs); 1/3 KVM_INTERNAL_ERROR at 220 s
  before any prewarm, so prewarm is not the trigger. Every non-clean pilot ran while 8 Q4 boots loaded the host; pilot3 (3/3
  clean) ran on an idle host. Prewarm chunking now generated in shell (no plpgsql). Pilot5 re-run under load.
- 2026-10-08 08:23 Pilot5 try 2 (under Q4 load): prewarm OK (76554 blocks), 2/3 KVM_INTERNAL_ERROR (224 s, 504 s). Pilot5 try 3 on an idle host (Q4 finished) tests the load hypothesis.
- 2026-10-08 23:20 Pilot5 try 3 (idle host): 1/3 KVM_INTERNAL_ERROR (171 s), 2/3 END with prewarm OK. Load hypothesis not supported; failure rate since prewarm works ~1/3 per run. Q4 seeds 6..10 finished (408 runs, pfn/vmstat clean); analysis_q4_10seeds.txt. Pilot4 try 3 started (idle host, 3 instances).
- 2026-10-08 23:32 A/B (user question: is the patch causing KVM_INTERNAL_ERROR?): 6 short runs (C=16, 5 txns, default pool) on unmodified 6.14-rc7 (bzImage-upstream), instances 3-4, manifest_ab.csv. Compare crash rate with the patched kernel's ~1/3 (pilot5).
- 2026-10-09 00:25 A/B partial: unmodified 6.14-rc7 also fails with the same signature (KVM internal error suberror 3, exit 0x4e,
  page-aligned vmalloc stack address), 1 of first 4 runs. The fault is not caused by the patch.
  Pilot4 try 3: C=16 big END (peak live 2.6k); C=200 big L2 double fault at 1717 s (GS base 0), C=200 default STALL (NMI panic)
  at ~2500 s; peak live before failure 17.3k / 15.6k, recycled 0. No C=200 run has completed (0/3 that reached pgbench).
  Per the pre-registered fallback: L2 1 GiB, shared_buffers 256 MB (25% rule; passed with pg_ctl -o, data image unchanged),
  default pool ~10.5k, txns 200/client (shorter runs, less fault exposure). Pilot6 = fifo C=100 big, C=100 default, C=16 big.
  Main runs: C in {100, 16} at 1 GiB if C=100 default-pool mmu_recycled > 0.
- 2026-10-09 00:39 A/B final (upstream 6.14-rc7, 6 runs): 4 END, 1 KVM_INTERNAL_ERROR (same signature), 1 L1_UNRESPONSIVE (the L1 itself hung; harness issue or emulator, not L2).
- 2026-10-09 00:55 Test 1 (user: "go 1-4"): harness fix for the KVM_INTERNAL_ERROR artifact. L1 kernels rebuilt with one change in
  svm_check_emulate_instruction (svm.c:4846): test SVM_EXITINTINFO_VALID instead of SVM_EXITINTINFO_TYPE_MASK (patch
  /tmp/kvmval/camp/svmfix.patch; only svm.o recompiled) -> bzImage-camp-svmfix, bzImage-upstream-svmfix. Real AMD hardware
  never leaves stale EXITINTINFO type bits, so this only removes the emulator artifact; applied to every arm alike.
  Check: 9 short runs (C=16, 5 txns, default pool, fifo) on camp-svmfix, instances 3-5. Baseline short-run rate of this error
  5/15. Pass = 0 KVM_INTERNAL_ERROR in 9 (p = 0.026 under the baseline rate). Other failure types reported separately.
- 2026-10-09 01:00 Tests 3 and 4 (user: "go 1-4"), written before any data.
  Test 3 = Q6, real host reclaim (point 2 follow-up). New selftest x86/lru_reclaim_test.c: lru_idle_test trace, no page_idle.
  After warmup the test writes a swap header to a virtio disk (1 GiB, per boot), swapon, and mlocks a hog leaving -M MiB
  available, so L1 reclaim keeps swapping guest pages while the guest runs. At each interval boundary 2..T it reads
  /proc/self/pagemap for every used page: out = not present (swapped). Kernel camp-svmfix, single-thread TCG, THP off.
  Grid (fixed after one smoke run sets -M and T so a run takes <= 60 min; smoke not analysed): R=1024, K=8, hot=16, 90%,
  pools 128 and 2048 (control: no shadow sweeps), 4 policies, seeds 1..5, one run per boot. Plus a no-pressure control
  (-M 0, pool 128, seed 1, 4 policies): out = 0 expected.
  Primary metric: hot out fraction = out_hot / checked_hot. Secondary: pswpin, pgmajfault, pf_taken.
  Prediction: at pool 128 leaf > fifo, clock, parent on hot out fraction (leaf clears the leaf A bits reclaim reads);
  at pool 2048 no difference. Decision: paired by seed, 95% t-interval of the difference excludes 0, and same sign 5/5.
  Test 4 = stress/correctness. New selftest x86/lru_stress_test.c: 4 vCPUs, hot/cold accesses; every checked page holds a
  tag derived from its address (a read through a wrong translation fails a guest assert); host threads churn a raw
  memslot (add/move/delete), toggle dirty logging + read the dirty log, madvise(DONTNEED) a scratch area, mark checked pages
  idle, cycle KVM_SET_NR_MMU_PAGES (64..1024). Kernel camp-svmfix (KASAN, lockdep, DEBUG_LIST), MTTCG L1, THP off.
  Arms: fifo, clock, leaf, parent fixed, plus "toggle" (lru_mmu flipped every 50 ms, lru_age 1/2 every 70 ms); seeds 1..3;
  duration fixed after the smoke (target <= 60 min per boot). Pass per boot: test completes, no guest assert, every vCPU
  progresses in every 30 s window, no splat in scan.sh (KASAN, lockdep, list corruption, WARN, BUG, RCU stall, hung task).
  Boots that fail with a known emulator signature (L1 hang with no kernel output) are re-run and listed.

## 2026-10-09 01:15 AMENDMENT 4: Test 4 smoke found ENOSPC; CLOCK fallback fix
- str-smoke (toggle arm, svmfix kernel) died at ~90 s with KVM_RUN ENOSPC; no KASAN/lockdep splat.
- One-seed per-policy repro (pool 64, 4 vCPUs, 120 s): fifo/clock/leaf PASS, parent FAIL (ENOSPC at 71 s).
- Cause: lru_ref (set by lockless fast_page_fault) and Accessed bits are re-set by running vCPUs during the sweep; with parent aging every non-root page can look young on both passes, clock zaps 0, make_mmu_pages_available returns -ENOSPC.
- Fix (repo + camp/src, uncommitted): if the CLOCK sweep zaps 0 pages, fall through to the FIFO loop; new VM stat lru_fifo_fallbacks. Kernel bzImage-camp-fb.
- Test 4 now runs on bzImage-camp-fb, arms fifo/clock/leaf/parent/toggle x seeds 1..3, unchanged pass criteria. Also report lru_fifo_fallbacks per arm. Prediction: parent > 0 fallbacks, all 15 PASS.
- reclaim smoke: -M 16 left 17 MiB, test OOM-killed (harness sizing). rec-smoke2 uses -r 4096 -M 40.

## 2026-10-09 01:20 pilot6 (1G L2, 256MB shared_buffers, pre-svmfix camp kernel)
- C16-big END recycled=0; C100-def STALL (L2 NMI panic in ktime_get_update_offsets_now, pf unchanged 618 s); C100-big STALL too (no eviction possible there), so the stall is not caused by eviction.
- Gate "C100 default recycled > 0 and completes" not met: no pgbench config both evicts and completes under TCG. Main pgbench campaign (Test 2) NOT run; Q5 needs real x86 hardware. Reported as such.

## 2026-10-09 01:35 Test 4 results (bzImage-camp-fb)
- 14/15 PASS; no KASAN/lockdep splat (only match is the boot SRSO banner).
- lru_fifo_fallbacks: fifo 0, clock 0, leaf 240/234/228, parent 287/280/283, toggle 167/155. Fallback also fires under leaf aging (prediction said parent only).
- toggle-s2 FAIL: "BUG: spinlock recursion" in sched_tick from a timer IRQ taken while the interrupted frame had EFLAGS=0x46 (IF clear), then soft lockup. IRQ delivered with IF=0 = TCG fault (same class as the earlier timekeeper_lock deadlock); none of the CLOCK code is on the stack. Rerun once as t4-toggle-s2r.
- t4-toggle-s2r PASS (lru_fifo_fallbacks=307). Test 4 final: 15/15 PASS with the one TCG-fault replacement.

## 2026-10-09 01:50 notes
- L1 "BUG: sleeping function called from invalid context" (handle_fastpath_hlt -> kvm_skip_emulated_instruction -> emulator insn fetch, IRQs off, because QEMU SVM gives next_rip=0) fires ~330x per pgbench run in BOTH upstream (ab-up1/2/4: 336/338/325) and camp (svmfix-1: 341). Upstream KVM x QEMU interaction, not ours.
- svmfix-6 L2_EXITED: L2 double fault, CR2=0x341c0 (kernel %gs with GS base 0) — same emulator class as before; not KVM_INTERNAL_ERROR.
- rec-smoke2 -r 4096 failed: each region is a 2 MiB mapping, 8 GiB > 7 GiB guest RAM. Rerun with -r 1024 -M 40.

## 2026-10-09 01:55 Test 1 result + Test 3 hog fix
- Test 1 (svmfix, 9 runs): 7 END, 1 L2_EXITED (L2 double fault), 1 L1_UNRESPONSIVE, 0 KVM_INTERNAL_ERROR. PASS (pre-reg: 0/9 vs baseline 5/15, p=0.026).
- rec-smoke2 (-r 1024 -M 40): hog overshot to MemAvailable 4 MiB, OOM at 522 s. hog() now locks half the remaining gap per step and re-reads MemAvailable (converges to target + <1 MiB). Smoke rerun as rec-smoke3, same args.
- rec-smoke3: hog converged (MemAvailable 40 MiB) but OOM at 509 s; slab_unreclaimable 58782 pages (~230 MB) ~= KASAN quarantine cap (RAM/32 = 220 MB), which fills after the hog sized itself. AMENDMENT 5: Test 3 runs on bzImage-camp-fb-nokasan (same source, KASAN/lockdep off); memory-safety coverage stays with Test 4. Smoke rerun rec-smoke4.
- rec-smoke4 (nokasan): PASS 81 s but pswpout=0 (32 MiB set fits in 38 MiB). rec-smoke5: -k 16 (64 MiB set).
- rec-smoke5 (-k 16): pswpout=0 again; warmup makes the set resident before the hog, which only takes free memory. Added -S squeeze_mib (lock S/t MiB at each boundary during the intervals). rec6 smoke: -k 16 -M 40 -S 60, leaf + fifo.
- rec6 (-S 60 fixed MiB): pswpout=0 both; slack beyond MemAvailable absorbed it. -S now = MiB to push to swap (lock 1 MiB until pswpout grows by S/t MiB per boundary). rec7: -S 24.
- rec7 smoke (not analysed): pswpout ~27.7k pages both (each 1 MiB lock wakes kswapd to the high watermark, so ~108 MiB out vs 24 target), 88 s/run, no OOM.

## 2026-10-09 02:40 AMENDMENT 6: Test 3 grid parameters (fixed before any grid run)
- K=16 (was 8; 64 MiB set), -M 40, -S 24, T=6, w/l defaults, kernel bzImage-camp-fb-nokasan, single-thread TCG, SWAP_MB=1024.
- Grid unchanged otherwise: pools 128/2048 x fifo/clock/leaf/parent x seeds 1..5, one run per boot; control -M 0 pool 128 seed 1 x 4 policies.
- Primary metric, prediction and decision rule unchanged (leaf > others on out_hot/checked_hot at pool 128; none at 2048; paired 95% t-interval excludes 0 and sign 5/5).

## 2026-10-09 03:05 Test 3 results (44/44 RESULT, no OOM/assert; controls -M 0: 0 out, 0 pswpout)
- Pre-registered prediction NOT supported. Pool 128 hot out fraction means: fifo 0.176, clock 0.117, leaf 0.100, parent 0.110; leaf-fifo -0.076 [-0.240,+0.088] sign 1/5; leaf-clock -0.016 [-0.191,+0.158]; leaf-parent -0.009 [-0.130,+0.112]. Pool 2048: all ~0 (leaf 0.029 from one seed), no differences.
- EXPLORATORY: pool 128 - pool 2048 paired by policy+seed, n=20: +0.118 [+0.074,+0.162], 16/20 positive, 3 zero. Losses come in whole hot regions (~3 of 16). Shadow-page eviction (any policy) costs hot pages under host reclaim; policy effect not detectable at n=5.
