#!/usr/bin/env python3
"""L1 kernel splat headers in a Redis run log's dmesg delta or an L1 serial log.

A "BUG: sleeping function called from invalid context" splat is excused only if its stack (the next 60 lines, up to the
next splat header) contains handle_fastpath_hlt, the known upstream one under QEMU (next_rip=0). The L1 serial console
prints headers without stacks; there (whole=True) it is excused only at its known site, include/linux/uaccess.h:106
(4,985 of 4,985 such splats in the published run logs had handle_fastpath_hlt in their stack).

Amendment 11 adds ioapic_exempt() (moved from redis_watch.sh, unchanged) and patch_site(): the first non-excused
report's RIP: line, the function named in its header, and its call-trace frames not starting with "? ", matched against
the frozen open3/mmu_funcs.txt (names cut at the first ".").
"""
import hashlib, os, re, sys
HDR = re.compile(r"\] (BUG: |WARNING: CPU|KASAN:|Oops|general protection fault|"
                 r"WARNING: possible (circular|recursive) locking|WARNING: inconsistent lock state|"
                 r"watchdog: BUG: soft lockup|Watchdog detected hard LOCKUP|rcu: INFO: rcu_\w+ (self-)?detected stall|"
                 r"list_(add|del) corruption|kernel BUG at|Kernel panic)")
FUNCS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "mmu_funcs.txt")
FUNCS_SHA = "b2df63eb84d55d41ce48cb8bdd52c3d5260b2ea389ef10ec075195bc5472e056"  # PREREG Amendment 11 (round 18)
FRAME = re.compile(r"\]\s+(\?\s+)?([A-Za-z_][A-Za-z0-9_.]*)\+0x[0-9a-f]+/0x[0-9a-f]+")
RIPL = re.compile(r"RIP: [0-9a-f]{4}:([A-Za-z_][A-Za-z0-9_.]*)\+0x")
HDRFN = re.compile(r"(?: in | at \S+ )([A-Za-z_][A-Za-z0-9_.]*)\+0x[0-9a-f]+")
LOCKUP = re.compile(r"soft lockup|hard LOCKUP|rcu: INFO: rcu_\w+ (self-)?detected stall")
SPIN = re.compile(r"\] BUG: (spinlock|rwlock) ")
IRQENTRY = re.compile(r"\]\s+asm_(sysvec_|common_interrupt|exc_)")  # reliable frames only
CONT = re.compile(r"\] (Oops|KASAN:)")
KEQ = re.compile(r"\] =+\s*$")
MMU_PATH = "arch/x86/kvm/mmu/"
KVM_PREFIX = ("kvm_", "__kvm_", "svm_", "vcpu_", "x86_emulate", "em_", "nested_svm", "kvm_arch_")


def _lines(path, whole):
    lines = open(path, errors="replace").read().splitlines()
    if not whole:
        idx = [i for i, l in enumerate(lines) if l.startswith("--- dmesg delta:")]
        if not idx:
            return []
        lines = lines[idx[0]:]
    return lines


def _headers(lines, whole):
    """(index, label) of every non-excused splat header."""
    out = []
    for i, l in enumerate(lines):
        m = HDR.search(l)
        if not m:
            continue
        if "BUG: sleeping function called from invalid context" in l and whole:
            if "at include/linux/uaccess.h:106" not in l:
                out.append((i, "BUG: sleeping function at an unknown site"))
            continue
        if "BUG: sleeping function called from invalid context" in l:
            stack = []
            for nl in lines[i + 1:i + 61]:
                if HDR.search(nl):
                    break
                stack.append(nl)
            if any("handle_fastpath_hlt" in s for s in stack):
                continue
            out.append((i, "BUG: sleeping function (not handle_fastpath_hlt)"))
        else:
            out.append((i, m.group(1)))
    return out


def splats(path, whole=False):
    return [lab for _, lab in _headers(_lines(path, whole), whole)]


def ioapic_exempt(path):
    """Amendment 9 / D mode: the upstream mp_irqdomain_alloc race at L1 boot. Exempt only if every splat header
    comes before "Run /sbin/init as init process", the first is the mask_ioapic_irq KASAN report with
    mp_irqdomain_alloc in the following stack lines, and the rest are that crash's own follow-on headers."""
    L = open(path, errors="replace").read().splitlines()
    init = next((i for i, l in enumerate(L) if "Run /sbin/init as init process" in l), len(L))
    hdr = [i for i, l in enumerate(L) if HDR.search(l)]
    if not hdr or hdr[-1] >= init or "KASAN: null-ptr-deref in mask_ioapic_irq" not in L[hdr[0]]:
        return False
    if not any("mp_irqdomain_alloc" in l for l in L[hdr[0]:hdr[0] + 120]):
        return False
    follow = ("BUG: kernel NULL pointer dereference", "Oops", "Kernel panic")
    return all(any(s in L[i] for s in follow) for i in hdr[1:])


def load_funcs(path=FUNCS):
    data = open(path, "rb").read()
    if hashlib.sha256(data).hexdigest() != FUNCS_SHA:
        raise RuntimeError(f"{path}: sha256 differs from the frozen list")
    return set(data.decode().split())


def _report(lines, i):
    """Lines of the report whose header is lines[i]. A KASAN report runs to its closing "=====" line (its Allocated and
    Freed stacks included); any other report to its first </TASK> or end-trace line. Either ends at the next splat
    header, except an Oops or bare KASAN: line before the report's first RIP:/Call Trace: line (the Oops line after
    "BUG: kernel NULL pointer dereference", the KASAN range line after a GPF), which continues it."""
    kasan = "KASAN:" in lines[i]
    end = min(len(lines), i + (400 if kasan else 200))
    body = False
    for j in range(i + 1, end):
        l = lines[j]
        if HDR.search(l) and not (not body and CONT.search(l)):
            return lines[i:j]
        body = body or "RIP: " in l or "Call Trace:" in l
        if (KEQ.search(l) if kasan else ("</TASK>" in l or "---[ end trace" in l)):
            return lines[i:j + 1]
    return lines[i:end]


def patch_site(path, funcs, whole=False):
    """None if no non-excused splat; else dict(header, names, hits, path_hit, cls) for the first report.
    Names by header type (PREREG Amendment 11, round 20):
      lockup/stall (soft lockup, hard LOCKUP, RCU stall): header function and the first RIP: line, the stuck context;
      spinlock/rwlock BUG: call-trace frames from the top down to the first interrupt-entry frame, no RIP: lines, so an
        interrupt taken on top of a vCPU thread does not name that thread's frames;
      any other header: header function, every RIP: line, and every frame.
    Frames starting with "? " never count."""
    lines = _lines(path, whole)
    hs = _headers(lines, whole)
    if not hs:
        return None
    i, _ = hs[0]
    rep, hdr = _report(lines, i), lines[i]
    names = []
    m = HDRFN.search(hdr)
    if m:
        names.append(m.group(1))
    if LOCKUP.search(hdr):
        names += [m.group(1) for l in rep for m in [RIPL.search(l)] if m][:1]
    elif SPIN.search(hdr):
        for l in rep[1:]:
            if IRQENTRY.search(l):
                break
            m = FRAME.search(l)
            if m and not m.group(1):
                names.append(m.group(2))
    else:
        for l in rep:
            m = RIPL.search(l)
            if m:
                names.append(m.group(1))
                continue
            m = FRAME.search(l)
            if m and not m.group(1):
                names.append(m.group(2))
    names = [n.split(".")[0] for n in names]
    hits = sorted({n for n in names if n in funcs})
    path_hit = MMU_PATH in hdr
    if hits or path_hit:
        cls = "kvm-mmu"
    elif "slab" in hdr or "kmalloc" in hdr:
        cls = "slab"
    elif "stack" in hdr:
        cls = "stack"
    elif any(n.startswith(KVM_PREFIX) for n in names):
        cls = "kvm-other"
    else:
        cls = "other"
    return dict(header=hdr.split("] ", 1)[-1][:160], names=names, hits=hits, path_hit=path_hit, cls=cls)


if __name__ == "__main__":
    whole = "--whole" in sys.argv
    for p in [a for a in sys.argv[1:] if a != "--whole"]:
        s = splats(p, whole)
        if s:
            print(p, sorted(set(s)))
