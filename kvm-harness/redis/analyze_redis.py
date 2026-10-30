#!/usr/bin/env python3
"""Unpaired analysis of Redis-in-L2 manifest.

Usage: analyze_redis.py MANIFEST.csv BATCH[,BATCH...] [--pools 80,96,128,0]
Per pool: median per policy; median ratio B/A with percentile bootstrap 95% CI (10k, groups resampled
independently); Mann-Whitney U two-sided p. Verdict on % = (ratio-1)*100.
"""
import csv, sys, collections
import numpy as np
from scipy import stats

man, batches = sys.argv[1], sys.argv[2].split(",")
rows = [r for r in csv.DictReader(open(man)) if r["batch"] in batches]
import os, re, glob
R = os.path.dirname(os.path.abspath(man))
CORRUPT = re.compile(r"double fault|Oops|general protection|BUG: unable to handle|segfault at|Thread overran|"
                     r"spinlock recursion|DEBUG_LOCKS_WARN_ON|list_(add|del) corruption|KASAN:|stack corrupted|"
                     r"non-static key|Kernel panic|invalid opcode|kernel BUG")


def corrupt(r):
    """Reasons this run's data may be affected by emulator state corruption (L2 serial + L1 dmesg delta)."""
    why = set()
    f = f"{R}/logs/{r['tag']}.log"
    if os.path.exists(f):
        for l in open(f, errors="replace"):
            m = CORRUPT.search(l)
            if m and "sleeping function" not in l:
                why.add(m.group(0))
    return sorted(why)


for r in rows:
    r["_corrupt"] = corrupt(r)
ok = [r for r in rows if r["result"] == "END" and not r["l1_event"] and not r["_corrupt"]]
POLS = ["fifo", "clock", "leaf", "parent"]
COMPS = [("fifo", "clock"), ("fifo", "leaf"), ("fifo", "parent"), ("clock", "leaf"), ("clock", "parent"),
         ("leaf", "parent")]
METRICS = ["mmu_recycled", "pf_taken", "mmu_shadow_zapped"]
rng = np.random.default_rng(777)
g = collections.defaultdict(list)
for r in ok:
    g[(int(r["pool"]), r["policy"])].append(r)
pools = sorted({p for p, _ in g}, key=lambda p: (p == 0, p))


def verdict(lo, hi):
    if lo > 0:
        return "definitive worse"
    if hi < 0:
        return "definitive better"
    if lo >= -3 and hi <= 3:
        return "definitive no difference"
    return "inconclusive"


print(f"batches={batches}; runs total={len(rows)}, clean END={len(ok)}")
for m in METRICS:
    print(f"\n## {m}: median [min-max] per policy")
    print("| pool | " + " | ".join(POLS) + " |")
    for p in pools:
        cells = []
        for pol in POLS:
            v = np.array([float(r[m]) for r in g[(p, pol)]])
            cells.append(f"{np.median(v):,.0f} [{v.min():,.0f}-{v.max():,.0f}] n={len(v)}" if len(v) else "-")
        print(f"| {'default' if p == 0 else p} | " + " | ".join(cells) + " |")
    print(f"\n## {m}: median ratio B/A, as % change (negative = B lower)")
    print("| pool | A | B | nA | nB | median % | 95% bootstrap CI | MW p | verdict |")
    for p in pools:
        for a, b in COMPS:
            A = np.array([float(r[m]) for r in g[(p, a)]])
            B = np.array([float(r[m]) for r in g[(p, b)]])
            if len(A) < 2 or len(B) < 2:
                continue
            if np.median(A) == 0:
                same = (A == 0).all() and (B == 0).all()
                print(f"| {p} | {a} | {b} | {len(A)} | {len(B)} | {'all zero' if same else 'A median 0'} | - | - | "
                      f"{'definitive no difference (identical 0)' if same else 'n/a'} |")
                continue
            ra = rng.choice(A, size=(10000, len(A)))
            rb = rng.choice(B, size=(10000, len(B)))
            ma = np.median(ra, axis=1)
            mb = np.median(rb, axis=1)
            with np.errstate(divide="ignore", invalid="ignore"):
                rat = (mb / ma - 1) * 100
            rat = rat[np.isfinite(rat)]
            lo, hi = np.percentile(rat, [2.5, 97.5])
            pt = (np.median(B) / np.median(A) - 1) * 100
            try:
                mw = stats.mannwhitneyu(A, B, alternative="two-sided").pvalue
            except ValueError:
                mw = float("nan")
            print(f"| {'default' if p == 0 else p} | {a} | {b} | {len(A)} | {len(B)} | {pt:+.1f} | [{lo:+.1f}, {hi:+.1f}] | "
                  f"{mw:.3g} | {verdict(lo, hi)} |")
bad = [r for r in rows if r not in ok]
print(f"\nexcluded runs (not END, L1 event, or corruption signature in L2 serial / L1 dmesg): {len(bad)}")
for r in bad:
    print("  ", r["tag"], r["result"], r["l1_event"], r["_corrupt"])
ex = collections.Counter((int(r["pool"]), r["policy"]) for r in bad)
print("\nexclusions per cell (pool, policy):", dict(sorted(ex.items())))
for p in pools:
    per = [ex.get((p, pol), 0) for pol in POLS]
    if len(set(per)) > 1:
        print(f"  FLAG pool {p}: exclusions differ by policy {dict(zip(POLS, per))}")
