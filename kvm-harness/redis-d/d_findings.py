#!/usr/bin/env python3
"""Test D findings scan (open3/PREREG.md Amendment 11 Part B). Stateless: every call rescans the manifest and logs.

Usage: d_findings.py MANIFEST LOGS D_STARTED WLOG SNAPSHOT
Exit 0 = halt (reason on stdout), 1 = no halt, 2 = check error. SNAPSHOT is rewritten on every scan.

Findings. A run counts once, for its policy, whatever its kinds:
  RUN: an L1 splat in its run log's dmesg delta, an L1 serial splat that redis_camp's post-run check attributed to it
       (logs/<boot>.attrib run=<tag>, or logs/<tag>.l1splat), result KVM_INTERNAL_ERROR or L2_EXITED, or an L2
       corruption pattern in its run log (the NMI panic after a STALL's injected NMI is excused).
  STALL-I: result STALL with RFLAGS.IF clear in every HMP capture (stall_class.py).
  SERIAL: an L1 serial (mtime >= D_STARTED) with a non-excused splat that no run owns: its .attrib says run=-, or it has
       no .attrib and no live process has it open as its serial. It counts toward the total only. A serial with no
       .attrib whose qemu is still running is pending: redis_camp attributes it after the run in flight. IOAPIC-exempt
       serials are noted in WLOG and never count.
Halts:
  PATCHSITE (rule i): the first report of a non-excused splat in a run log, an L1 dmesg capture (logs/<boot>.dmesg) or
       a serial names a function in the frozen open3/mmu_funcs.txt (RIP, header, or a call-trace frame not starting
       with "? "), or its header names a file under arch/x86/kvm/mmu/.
  CAP (rule ii): 3 findings in one policy, or 6 in total.
  HARNESS: a result other than END, STALL, TIMEOUT, KVM_INTERNAL_ERROR, L2_EXITED, or NO_LOG/"" with an L1 event.
"""
import sys
try:
    import csv, glob, json, os
    from collections import Counter
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import l1splat
    from fisher_d import CORRUPT
    from stall_class import stall_class
except BaseException as e:
    print(f"check error: {type(e).__name__}: {e}")
    sys.exit(2)

OKRES = ("END", "STALL", "TIMEOUT", "KVM_INTERNAL_ERROR", "L2_EXITED")
NMI_PANIC = "Kernel panic - not syncing: NMI: Not continuing"
PCAP, TCAP = 3, 6


def corrupt(path):
    lines = open(path, errors="replace").readlines()
    nmi_ok = any(l.startswith("### nmi") for l in lines)
    return any(CORRUPT.search(l) and "sleeping function" not in l and not (nmi_ok and NMI_PANIC in l) for l in lines)


def live_serials():
    out = set()
    for p in glob.glob("/proc/[0-9]*/cmdline"):
        try:
            args = open(p, "rb").read().split(b"\0")
        except OSError:
            continue
        out |= {a[5:].decode(errors="replace") for a in args if a.startswith(b"file:") and a.endswith(b".serial")}
    return out


def attrib(path):
    kv = dict(x.split("=", 1) for x in open(path).read().split() if "=" in x)
    return kv.get("run", "-"), kv.get("policy", "-")


def scan(man, logs, started):
    t0 = os.path.getmtime(started)  # raises if D_STARTED is missing: counted as a check error
    funcs = l1splat.load_funcs()
    runs, sites, serials, pending, notes, harness = {}, [], [], [], [], []

    def add(tag, pol, kind):
        runs.setdefault(tag, {"policy": pol, "kinds": set()})["kinds"].add(kind)

    def site(src, path, whole=False):
        s = l1splat.patch_site(path, funcs, whole)
        if s:
            sites.append(dict(src=src, **s))
    rows = list(csv.DictReader(open(man))) if os.path.exists(man) else []
    for r in rows:
        tag, pol, res = r["tag"], r["policy"], r["result"]
        f = f"{logs}/{tag}.log"
        has = os.path.exists(f)
        if has and l1splat.splats(f):
            add(tag, pol, "RUN:l1-splat")
            site(f"RUN {tag}", f)
        if os.path.exists(f"{logs}/{tag}.l1splat"):
            add(tag, pol, "RUN:l1-serial")
        if res in ("KVM_INTERNAL_ERROR", "L2_EXITED"):
            add(tag, pol, f"RUN:{res}")
        if has and corrupt(f):
            add(tag, pol, "RUN:corruption")
        if res == "STALL" and has and stall_class(f) == "I":
            add(tag, pol, "STALL-I")
        if not (res in OKRES or (res in ("NO_LOG", "") and r["l1_event"])):
            harness.append(f"HARNESS {tag}={res or 'empty'}")
    live = live_serials()
    for s in sorted(glob.glob(f"{logs}/l1-i*-b*.serial")):
        if os.path.getmtime(s) < t0 or not l1splat.splats(s, whole=True):
            continue
        b = os.path.basename(s)[:-len(".serial")]
        if l1splat.ioapic_exempt(s):
            notes.append(f"IOAPIC-EXEMPT {s}")
            continue
        site(f"SERIAL {b}", s, whole=True)
        a = f"{logs}/{b}.attrib"
        if os.path.exists(a):
            tag, pol = attrib(a)
            if tag == "-":
                serials.append(b)
            else:
                add(tag, pol, "RUN:l1-serial")
        elif s in live:
            pending.append(b)
        else:
            serials.append(b)
    for d in sorted(glob.glob(f"{logs}/l1-i*-b*.dmesg")):
        if os.path.getmtime(d) >= t0 and l1splat.splats(d):
            site(f"DMESG {os.path.basename(d)[:-len('.dmesg')]}", d)
    halts = [f"PATCHSITE {s['src']}: {s['header'][:120]} hits={s['hits']} path_hit={s['path_hit']}"
             for s in sites if s["cls"] == "kvm-mmu"]
    per = Counter(v["policy"] for v in runs.values())
    halts += [f"CAP policy {p}: {n} findings" for p, n in sorted(per.items()) if n >= PCAP]
    total = len(runs) + len(serials)
    if total >= TCAP:
        halts.append(f"CAP total: {total} findings")
    snap = dict(runs={t: dict(policy=v["policy"], kinds=sorted(v["kinds"])) for t, v in sorted(runs.items())},
                serial_findings=serials, pending_serials=pending, sites=sites, per_policy=dict(per), total=total,
                notes=notes, halts=halts, harness=harness)
    return snap, notes, harness + halts


def main(man, logs, started, wlog, snapf):
    snap, notes, reasons = scan(man, logs, started)
    tmp = snapf + ".tmp"
    json.dump(snap, open(tmp, "w"), indent=1)
    os.replace(tmp, snapf)
    seen = open(wlog).read() if os.path.exists(wlog) else ""
    with open(wlog, "a") as w:
        for n in notes:
            if n not in seen:
                w.write(f"{n}\n")
    if reasons:
        print(" ".join(reasons))
    return 0 if reasons else 1


if __name__ == "__main__":
    try:
        sys.exit(main(*sys.argv[1:6]))
    except SystemExit:
        raise
    except BaseException as e:
        print(f"check error: {type(e).__name__}: {e}")
        sys.exit(2)
