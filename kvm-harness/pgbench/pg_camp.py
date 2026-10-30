#!/usr/bin/env python3
"""Redis-in-L2 campaign: N parallel L1 instances (own qcow2 overlay + ssh port), shared random run queue.

Usage: redis_camp.py gen BATCH POOLS REPS [REPSEED]   -> runs/BATCH.json (random order)
       redis_camp.py run -n N RUNS.json [RUNS.json...]
L1: QEMU TCG x86 EPYC+svm, -smp 4 -m 8G, -no-reboot -d cpu_reset,guest_errors, kernel bzImage-camp.
"""
import csv, datetime, json, os, queue, random, re, subprocess, sys, threading, time

R = "/tmp/kvmval/open/pg"
L2 = "/tmp/kvmval/l2"
KERNEL = os.environ.get("KERNEL", "/tmp/kvmval/camp/bzImage-camp")
POL = {"fifo": (0, 0), "clock": (1, 0), "leaf": (1, 1), "parent": (1, 2)}
FIELDS = ["tag", "batch", "instance", "boot", "start_order", "policy", "pool", "procs", "seed", "rep", "result", "elapsed",
          "pf_taken", "pf_fixed", "mmu_shadow_zapped", "mmu_recycled", "mmu_cache_miss", "lru_hand_steps",
          "lru_ref_skips", "lru_age_sptes", "lru_age_skips", "lru_age_flushes", "exits", "sleep_splats",
          "other_splats", "l1_event", "started", "kernel"]
SSHO = ["-q", "-i", f"{L2}/id_l1", "-o", "StrictHostKeyChecking=no", "-o", "UserKnownHostsFile=/dev/null",
        "-o", "ConnectTimeout=30", "-o", "ServerAliveInterval=30", "-o", "ServerAliveCountMax=20"]
lock = threading.Lock()


def die_with_parent():
    """QEMU gets SIGKILL when the harness thread that started it exits, even on kill -9."""
    import ctypes
    ctypes.CDLL("libc.so.6", use_errno=True).prctl(1, 9)  # PR_SET_PDEATHSIG, SIGKILL
order = [0]


def log(msg):
    with lock:
        print(f"[{datetime.datetime.now():%m-%d %H:%M:%S}] {msg}", flush=True)
        open(f"{R}/campaign.log", "a").write(f"[{datetime.datetime.now():%m-%d %H:%M:%S}] {msg}\n")


POOL = int(os.environ.get("POOL", "0"))


def gen(batch, procs_list, reps, n, seed0=1):
    rng = random.Random(f"pg-{batch}")
    runs = []
    for procs in procs_list:
        for pol in POL:
            for r in range(reps):
                runs.append({"tag": f"{batch}-C{procs}-{pol}-r{r+1}", "batch": batch, "pool": POOL, "procs": procs,
                             "policy": pol, "rep": r + 1, "seed": seed0 + r, "n": n})
    rng.shuffle(runs)
    os.makedirs(f"{R}/runs", exist_ok=True)
    json.dump(runs, open(f"{R}/runs/{batch}.json", "w"), indent=1)
    print(len(runs), "runs")


class L1:
    def __init__(self, inst):
        self.inst, self.port, self.boot, self.proc = inst, 10200 + inst, 0, None
        self.img = f"{R}/img/l1-i{inst}.qcow2"
        self.mon = f"{R}/img/l1-i{inst}.mon"

    def ssh(self, cmd, timeout):
        return subprocess.run(["ssh", *SSHO, "-p", str(self.port), "root@127.0.0.1", cmd],
                              capture_output=True, text=True, timeout=timeout)

    def scp_to(self, srcs, dst):
        subprocess.run(["scp", *SSHO, "-P", str(self.port), *srcs, f"root@127.0.0.1:{dst}"], check=True, timeout=300)

    def scp_from(self, src, dst):
        return subprocess.run(["scp", *SSHO, "-P", str(self.port), f"root@127.0.0.1:{src}", dst], timeout=300).returncode

    def start(self):
        if not os.path.exists(self.img):
            subprocess.run(["qemu-img", "create", "-q", "-f", "qcow2", "-F", "qcow2", "-b", f"{R}/l1-pg.qcow2", self.img], check=True)
        self.boot += 1
        self.tagb = f"l1-i{self.inst}-b{self.boot}-{datetime.datetime.now():%m%d%H%M%S}"
        self.serial = f"{R}/logs/{self.tagb}.serial"
        self.qdbg = f"{R}/logs/{self.tagb}.qdbg"
        cmd = ["/tmp/kvmval/camp/ptrace_any_exec.py", "qemu-system-x86_64", "-accel", os.environ.get("ACCEL", "tcg,thread=multi"), "-cpu", "EPYC,+svm", "-smp", "4", "-m", "8G",
               "-kernel", KERNEL, "-append", "root=/dev/vda1 rw console=ttyS0 kvm.tdp_mmu=0 kvm_amd.npt=0 "
               "net.ifnames=0 log_buf_len=16M sysrq_always_enabled=1 tsc_early_khz=1000000 systemd.default_device_timeout_sec=1800",
               "-drive", f"file={self.img},if=virtio,format=qcow2,cache=unsafe",
               "-drive", f"file={L2}/seed.iso,if=virtio,format=raw,readonly=on",
               "-netdev", f"user,id=n0,hostfwd=tcp:127.0.0.1:{self.port}-:22", "-device", "virtio-net-pci,netdev=n0",
               "-display", "none", "-monitor", f"unix:{self.mon},server,nowait", "-serial", f"file:{self.serial}",
               "-no-reboot", "-d", "cpu_reset,guest_errors", "-D", self.qdbg]
        self.proc = subprocess.Popen(cmd, stdout=open(f"{R}/logs/{self.tagb}.qemu.out", "w"), stderr=subprocess.STDOUT,
                                     preexec_fn=die_with_parent)
        threading.Thread(target=self.watch, args=(self.proc, self.serial), daemon=True).start()
        t0 = time.time()
        while time.time() - t0 < int(os.environ.get("BOOT_S", "5400")):
            if self.proc.poll() is not None:
                raise RuntimeError(f"L1 qemu exited during boot rc={self.proc.returncode}")
            try:
                if self.ssh("true", 60).returncode == 0:
                    break
            except subprocess.TimeoutExpired:
                pass
            time.sleep(10)
        else:
            self.proc.kill(); self.proc.wait()
            raise RuntimeError("ssh timeout")
        self.scp_to([f"{R}/run_l2.sh", f"{R}/hmp.py", f"{R}/initfix.cpio.gz"], "/root/pg/")
        self.ssh("cd /root/pg && cat initramfs.cpio.gz initfix.cpio.gz > initramfs2.cpio.gz", 600)
        self.ssh("mountpoint -q /boot || mount /boot; touch /etc/cloud/cloud-init.disabled; "
                 "systemctl disable --now snapd.service snapd.socket unattended-upgrades.service fwupd.service "
                 "multipathd.service apt-daily.timer apt-daily-upgrade.timer motd-news.timer >/dev/null 2>&1; true", 600)
        tsc = self.ssh("dmesg | grep -E 'tsc: (Detected|Refined)|tsc_early|Marking TSC'", 600).stdout
        log(f"inst{self.inst} boot{self.boot} tsc: {' | '.join(l.split('] ', 1)[-1] for l in tsc.splitlines())}")
        out = self.ssh("uname -r; ls -la /root/pg/initramfs.cpio.gz /root/pg/data.img; file -L /bin/busybox | cut -c1-60; cat /sys/module/kvm/parameters/lru_mark_fill; "
                       "cat /sys/module/kvm/parameters/tdp_mmu /sys/module/kvm_amd/parameters/npt", 600).stdout
        log(f"inst{self.inst} boot{self.boot} up in {time.time()-t0:.0f}s: {out.split()}")

    cur = "noctx"

    def watch(self, proc, serial):
        """Tail the L1 serial; on each L2STALL_MARKER (written by run_l2.sh to /dev/kmsg, which then waits
        180 s before touching L2) capture host-side stacks of this outer QEMU + HMP registers."""
        seen = 0
        while proc.poll() is None:
            time.sleep(5)
            try:
                n = open(serial, errors="replace").read().count("L2STALL_MARKER")
            except FileNotFoundError:
                continue
            while seen < n:
                seen += 1
                out = f"{R}/logs/{self.cur}.hoststacks"
                log(f"inst{self.inst} L2STALL_MARKER #{seen} ({self.cur}): capturing host stacks pid={proc.pid}")
                with open(out, "a") as f:
                    f.write(f"### marker #{seen} serial={serial} qemu pid={proc.pid} {datetime.datetime.now():%T}\n")
                    f.flush()
                    try:
                        f.write(subprocess.run(["/tmp/kvmval/camp/hoststacks.sh", str(proc.pid), "3"],
                                               capture_output=True, text=True, timeout=1200).stdout)
                    except Exception as e:
                        f.write(f"hoststacks failed: {e}\n")
                    f.write(self.hmp("info registers -a", "info cpus"))
                log(f"inst{self.inst} host capture done -> {out}")

    def alive(self):
        return self.proc is not None and self.proc.poll() is None

    def hmp(self, *cmds):
        try:
            return subprocess.run(["python3", f"{L2}/hmp.py", self.mon, *cmds], capture_output=True, text=True,
                                  timeout=60).stdout
        except Exception as e:
            return f"hmp failed {e}"

    def stop(self):
        if self.alive():
            try:
                self.ssh("sync; poweroff", 30)
            except Exception:
                pass
            try:
                self.proc.wait(120)
            except subprocess.TimeoutExpired:
                self.proc.kill()
                self.proc.wait()


def parse(path):
    d = {}
    txt = open(path, errors="replace").read()
    m = re.search(r"^FINAL t=\d+ (.*)$", txt, re.M)
    if m:
        for kv in m.group(1).split():
            k, _, v = kv.partition("=")
            d[k] = v
    for k in ["RESULT", "ELAPSED_S", "SLEEP_SPLATS", "OTHER_SPLATS"]:
        mm = re.search(rf"^{k}=(\S+)", txt, re.M)
        d[k] = mm.group(1) if mm else ""
    mm = re.search(r"min_alloc_pages=(\d+)", txt)
    d["param_pool"] = mm.group(1) if mm else ""
    return d


def worker(inst, q, man):
    vm = L1(inst)
    for attempt in range(3):
        try:
            vm.start()
            break
        except Exception as e:
            log(f"inst{inst} boot failed (attempt {attempt+1}): {e}")
            if vm.alive():
                vm.proc.kill(); vm.proc.wait()
    else:
        return
    while True:
        try:
            run = q.get_nowait()
        except queue.Empty:
            break
        try:  # cross-process claim so several campaign processes can share one run list
            os.close(os.open(f"{R}/logs/{run['tag']}.claim", os.O_CREAT | os.O_EXCL | os.O_WRONLY))
        except FileExistsError:
            continue
        with lock:
            order[0] += 1
            so = order[0]
        lru, age = POL[run["policy"]]
        started = datetime.datetime.now().isoformat(timespec="seconds")
        dst = f"{R}/logs/{run['tag']}.log"
        ev = ""
        vm.cur = run["tag"]
        log(f"inst{inst} start {run['tag']} (order {so})")
        try:
            r = vm.ssh(f"cd /root/pg && L2MEM={run.get('mem', '2G')} L2APPEND='pg_clients={run['procs']} pg_txns={run['n']} pg_sb={run.get('sb', '512MB')}' TMO=14400 ./run_l2.sh {run['tag']} {lru} {age} {run['pool']}", 16000)
            if r.returncode != 0:
                ev = f"ssh_rc={r.returncode}"
        except subprocess.TimeoutExpired:
            ev = "host_timeout"
        if vm.alive() and vm.scp_from(f"/root/pg/logs/{run['tag']}.log", dst) == 0:
            d = parse(dst)
        else:
            d = {"RESULT": "NO_LOG"}
        if not vm.alive():
            time.sleep(2)
            ev = (ev + f" L1_EXIT rc={vm.proc.returncode}").strip()
        elif d["RESULT"] == "NO_LOG" or ev:
            ev = (ev + " L1_UNRESPONSIVE").strip()
            with open(f"{R}/logs/{run['tag']}.l1hmp", "w") as f:
                f.write(vm.hmp("info registers -a", "info cpus"))
        if os.path.exists(dst) and "TSC rate below hardware speed" in open(dst, errors="replace").read():
            ev = (ev + " TSC_MISMATCH").strip()  # L1 kvm default_tsc_khz != refined tsc_khz; reboot L1
        if ev:
            open(f"{R}/logs/{run['tag']}.l1event", "w").write(f"{ev}\nserial={vm.serial}\nqdbg={vm.qdbg}\n")
        row = dict(tag=run["tag"], batch=run["batch"], instance=inst, boot=vm.boot, start_order=so,
                   policy=run["policy"], pool=run["pool"], procs=run["procs"], seed=run["seed"], rep=run["rep"], result=d.get("RESULT", ""),
                   elapsed=d.get("ELAPSED_S", ""), sleep_splats=d.get("SLEEP_SPLATS", ""),
                   other_splats=d.get("OTHER_SPLATS", ""), l1_event=ev, started=started, kernel=KERNEL)
        for k in FIELDS:
            if k in d and k not in row:
                row[k] = d[k]
        if d.get("param_pool") not in ("", str(run["pool"])):
            row["result"] = "BADPARAM"
        with lock:
            with open(man, "a", newline="") as f:
                csv.DictWriter(f, FIELDS).writerow(row)
        log(f"inst{inst} done {run['tag']} result={row['result']} el={row['elapsed']} recycled={row.get('mmu_recycled')} "
            f"pf={row.get('pf_taken')} sleep={row['sleep_splats']} other={row['other_splats']} ev={ev}")
        if ev:
            vm.stop() if vm.alive() else None
            try:
                vm.start()
            except Exception as e:
                log(f"inst{inst} reboot failed: {e}")
                return
    vm.stop()
    log(f"inst{inst} finished")


def main():
    if sys.argv[1] == "gen":
        gen(sys.argv[2], [int(p) for p in sys.argv[3].split(",")], int(sys.argv[4]), int(sys.argv[5]),
            int(sys.argv[6]) if len(sys.argv) > 6 else 1)
        return
    args = sys.argv[2:]
    n = int(args[args.index("-n") + 1])
    del args[args.index("-n"):args.index("-n") + 2]
    man = os.environ.get("MANIFEST", f"{R}/manifest_pg.csv")
    first = int(os.environ.get("FIRST_INST", "0"))
    for d in ["logs", "img", "runs"]:
        os.makedirs(f"{R}/{d}", exist_ok=True)
    if not os.path.exists(man):
        with open(man, "w", newline="") as f:
            csv.DictWriter(f, FIELDS).writeheader()
    q = queue.Queue()
    for jf in args:
        for r in json.load(open(jf)):
            if os.path.exists(f"{R}/logs/{r['tag']}.claim"):
                continue
            q.put(r)
    log(f"queued {q.qsize()} runs on {n} instances")
    ts = [threading.Thread(target=worker, args=(first + i, q, man)) for i in range(n)]
    for t in ts:
        t.start()
        time.sleep(20)
    for t in ts:
        t.join()
    log("ALL RUNS DONE")


if __name__ == "__main__":
    main()
