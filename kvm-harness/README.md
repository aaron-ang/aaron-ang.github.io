# KVM shadow-MMU LRU: test harness (temporary)

Temporary handoff for running the Redis and pgbench tests on bare-metal x86.
Not part of the site: Hugo only builds `content/`, `layouts/`, `static/` and
`themes/`, so this directory is never published. Remove it before merging PR #2.

## What was run before, and why bare metal

On the original machine every test was nested:

- **Host:** QEMU 8.2.2 TCG runs an "L1" VM (`-cpu EPYC,+svm`, 4 vCPUs, 8G).
- **L1:** Ubuntu 24.04 VM running the test kernel, booted with
  `kvm.tdp_mmu=0 kvm_amd.npt=0`, so KVM uses the legacy shadow MMU, which is
  the code the patch changes.
- **L2:** a guest started by L1's KVM. The scripts in this directory all run
  inside L1 as root.

TCG caused most of the failures we saw (see `docs/`). pgbench at realistic
sizes never finished under TCG (`docs/open-REPORT.md`, "Test 2 / Q5"). On bare
metal, the **host is L1**: boot the test kernel on the EC2 metal instance,
turn off two-dimensional paging, and run `run_l2.sh` directly. The host-side
orchestrators (`redis_camp.py`, `pg_camp.py`, `run_d4.sh`) are only needed to
manage TCG L1s. Use them as a reference for the run matrix, not as entry points.

## 1. Build and boot the test kernel

Base: Linux v6.14-rc7.

```sh
sudo apt install -y git patch build-essential flex bison bc cpio libelf-dev libssl-dev dwarves
H=$PWD/kvm-harness                   # absolute path to this directory
cd ~ && git clone --depth 1 -b v6.14-rc7 https://git.kernel.org/pub/scm/linux/kernel/git/torvalds/linux.git
cd ~/linux
patch -p1 < $H/kernel/svmfix.patch      # upstream SVM fix, both kernels need it
patch -p1 < $H/kernel/test-build.patch  # LRU patch + validation-only knobs
cp /boot/config-$(uname -r) .config     # distro config: has NVMe, ENA, x2APIC for EC2
scripts/config -e KVM -e KVM_AMD -e KVM_INTEL -d SYSTEM_TRUSTED_KEYS -d SYSTEM_REVOCATION_KEYS \
  -e KASAN -e PROVE_LOCKING -e DEBUG_FS
make olddefconfig
make -j$(nproc) bzImage modules && sudo make modules_install install
```

Notes:

- `kernel/test-build.patch` is the "test build" from the posts: the LRU patch
  (github.com/aaron-ang/kvm) plus validation-only module parameters
  (`lru_mark_fill`, `lru_fifo_fallback`, `lru_zap_accessed`, `lru_age=3`).
  It predates two small fixes on the kvm repo's master (946ce93f3b48): the
  `lru_age` range check and the once-per-sweep `READ_ONCE(lru_age)`. The
  eviction behaviour is the same for `lru_age` 0, 1 and 2.
- `kernel/svmfix.patch` matters only on AMD (`svm.c`). Applied alone to v6.14-rc7,
  it gives the "baseline kernel" (no LRU code) used as the control.
- `kernel/config` is the config used before, for reference only. It was
  made for a TCG guest and lacks NVMe, ENA and x2APIC, so it cannot boot an
  EC2 metal host. KASAN and lockdep were on in every run. KASAN slows the
  host; drop it (`-d KASAN`) for throughput numbers.

Boot parameters. The distro config builds KVM as modules; these work either way:

- AMD (e.g. m6a.metal): `kvm.tdp_mmu=0 kvm_amd.npt=0`
- Intel (e.g. m6i.metal): `kvm.tdp_mmu=0 kvm_intel.ept=0`

Apply and reboot into the test kernel:

1. Append the parameters to `GRUB_CMDLINE_LINUX` in `/etc/default/grub`.
2. Run `sudo update-grub`. If the newest kernel is not the default entry,
   select it with `grub-reboot` or `GRUB_DEFAULT`.
3. Reboot. Check: `uname -r` shows `6.14.0-rc7`,
   `/sys/module/kvm/parameters/lru_mmu` exists,
   `/sys/module/kvm/parameters/tdp_mmu` reads `N`, and `npt` or `ept` reads `N`/`0`.
4. Set `sudo sysctl kernel.sysrq=1`. Stall captures use sysrq `w`, `d` and `l`;
   Ubuntu's default mask blocks them.

If any check fails, the runs still finish but exercise the TDP MMU, not the
patched shadow-MMU code. Every run log's first line records `lru_mmu`,
`lru_age`, `min_alloc_pages`, `tdp_mmu`, `npt`/`ept` and the kernel; check it.

## 2. Policies and pools

`run_l2.sh TAG LRU_MMU LRU_AGE MIN_ALLOC_PAGES`

| policy | LRU_MMU | LRU_AGE |
|--------|---------|---------|
| fifo (upstream behaviour) | 0 | 0 |
| clock (lru_ref only)      | 1 | 0 |
| leaf (Accessed bits in own SPTEs) | 1 | 1 |
| parent (Accessed bits in parent SPTEs) | 1 | 2 |

`MIN_ALLOC_PAGES` pins the shadow-page pool. `0` = upstream sizing from guest
memory. The Redis runs used pools 80, 96, 128 and 0. The pgbench runs used 0
and 200000 (a pool big enough that nothing is evicted, used to measure demand).

## 3. Redis (`redis/`)

```sh
sudo apt install -y qemu-system-x86 qemu-utils redis-server redis-tools busybox-static cpio python3 linux-image-generic
sudo systemctl disable --now redis-server
sudo mkdir -p /root/l2
sudo cp $H/redis/l2-init.sh $H/redis/run_l2.sh $H/redis/hmp.py /root/l2/
sudo bash $H/redis/mk_l2_initramfs.sh          # -> /root/l2/initramfs.cpio.gz
sudo bash -c 'cd /root/l2 && bash run_l2.sh test-p128-leaf-r1 1 1 128'
```

`linux-image-generic` provides the L2 kernel: `run_l2.sh` boots the newest
`/boot/vmlinuz-*-generic` (EC2 hosts run `-aws` kernels, which do not match).
The installed package also changes the grub default; re-check step 2 of
section 1 before rebooting.

- L2: 1 GiB, 1 vCPU, initramfs only. `redis-benchmark -n 20000 -r 100000
  -P 16 -q -t set,get,lpush,lpop` (override with `BENCH_ARGS` when building
  the initramfs, or `L2APPEND=bench_n=N` per run).
- Output: `/root/l2/logs/TAG.log`. Last lines: `RESULT=` (`END` = finished,
  `STALL` = `pf_taken` unchanged for `STALL_S` seconds, also `TIMEOUT`,
  `KVM_INTERNAL_ERROR`, `L2_EXITED`), `ELAPSED_S`, splat counts. `START`/`FINAL`
  lines hold the per-VM debugfs counters (`mmu_recycled`, `lru_*`,
  `lru_fifo_fallbacks`, ...).
- Run matrix used before: every policy x pool {80, 96, 128, 0}, randomised
  order, about 14 reps per cell (`redis-d/manifest_d1.csv`, 225 runs). The
  generator is `gen()` in `redis/redis_camp.py`; tags are
  `BATCH-pPOOL-POLICY-rN`. `redis/analyze_redis.py` and
  `redis-d/d_findings.py` read the manifests. They expect the CSV columns that
  `redis_camp.py` writes, which come from `parse()` over each run log.
- The tracefs filter `exit_reason==0x4e` counts SVM #PF exits. On Intel it
  matches nothing; ignore `PF_EXITINTINFO` there.

## 4. pgbench (`pgbench/`)

```sh
sudo apt install -y postgresql-16 qemu-system-x86 qemu-utils busybox-static cpio python3 linux-image-generic
sudo mkdir -p /root/pg
sudo cp $H/pgbench/l2-init-pg.sh $H/pgbench/run_l2.sh $H/pgbench/hmp.py /root/pg/
sudo SCALE=40 bash $H/pgbench/mk_pg.sh   # -> /root/pg/data.img (3G ext4) + initramfs.cpio.gz
sudo cp /root/pg/initramfs.cpio.gz /root/pg/initramfs2.cpio.gz   # run_l2.sh boots initramfs2
sudo bash -c "cd /root/pg && L2MEM=2G L2APPEND='pg_clients=100 pg_txns=200 pg_sb=512MB' TMO=14400 \
  bash run_l2.sh pg-C100-leaf-r1 1 1 0"
```

The L2 initramfs has no modules, so the generic kernel must have virtio-blk
and ext4 built in. Ubuntu's does; check with
`grep -E 'VIRTIO_BLK=|EXT4_FS=' /boot/config-*-generic` (both `=y`).

- On TCG, `initramfs2.cpio.gz` was the base initramfs with `initfix.cpio.gz`
  (an init fix) appended. The current `l2-init-pg.sh` already has that fix,
  so a copy of the freshly built initramfs is enough.
- L2: PostgreSQL 16 with `shared_buffers=512MB`, prewarmed with `pg_prewarm`,
  then `pgbench -S -c C -j 1 -t T`. Each run gets a fresh qcow2 overlay of
  `data.img`.
- Design (`docs/open-PREREG.md`, Amendment 1): shadow pages are per address
  space, so C backends that map the same shared buffers multiply demand. The
  default pool for a 2 GiB L2 is about 20,971 shadow pages. C=100 x 256
  regions = 25.6k should exceed it; C=16 (4.1k) is the control that fits.
  Pilot first: fifo with pool 200000 measures real demand from
  `mmu_cache_miss - mmu_shadow_zapped` at C=100 and C=16. If demand at C=100
  is below 1.2x the default pool, raise C (up to 200; `max_connections` is 210).
  Main runs: 8 reps per C, all 4 policies per rep in random order, primary
  metric `mmu_recycled` (END - START), Holm over the 3 vs-FIFO comparisons.
  Report TPS too: on bare metal it is meaningful.
- Under TCG no C=100 or C=200 run with eviction finished (`pgbench/manifest_pg.csv`).
  On bare metal the first useful result is whether the default pool evicts
  at all (`mmu_recycled > 0` at C=100 with pool 0).

## 5. Files

- `kernel/`: patches, the kernel config used before, and the old cross-build script.
- `redis/`: per-run script, L2 init, initramfs builder, TCG campaign driver,
  analysis, and `campaign_notes.txt` (root causes of earlier TCG stalls).
- `redis-d/`: the last Redis campaign (D): runner, watcher, findings
  classifier, 225-run manifest and analysis.
- `pgbench/`: per-run script, L2 init, data/initramfs builder, TCG campaign
  driver, debug helpers, manifests and log.
- `docs/`: pre-registrations and reports (`open-*` = pgbench era,
  `open3-*` = current evidence for the posts).

Paths inside the TCG drivers are hard-coded to `/tmp/kvmval/...` and expect
an L1 ssh key that is not included. The per-run scripts only use `/root/l2`
or `/root/pg`.
