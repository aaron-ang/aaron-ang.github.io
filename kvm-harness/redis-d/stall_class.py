#!/usr/bin/env python3
"""STALL class of a Redis run log (PREREG Amendment 11 Part B), from the HMP "info registers" captures after "STALL:".
A capture taken in 64-bit mode prints RIP=/RFL=; one taken in real or 32-bit mode prints EIP=/EFL=. Both count.

  I: RFLAGS.IF (0x200) clear in every capture.
  H: IF set and HLT=1 in every capture.
  O: anything else, including a STALL with no capture.
None if the log has no STALL line.
"""
import re, sys
REG = re.compile(r"[RE]IP=[0-9a-f]+ [RE]FL=([0-9a-f]+) \[[^\]]*\] CPL=\d II=\d A20=\d SMM=\d HLT=(\d)")


def captures(path):
    lines = open(path, errors="replace").read().splitlines()
    s = next((i for i, l in enumerate(lines) if l.startswith("STALL:")), None)
    if s is None:
        return None
    return [(int(m.group(1), 16), int(m.group(2))) for l in lines[s + 1:] for m in [REG.search(l)] if m]


def stall_class(path):
    caps = captures(path)
    if caps is None:
        return None
    if caps and all(not rfl & 0x200 for rfl, _ in caps):
        return "I"
    if caps and all(rfl & 0x200 and hlt == 1 for rfl, hlt in caps):
        return "H"
    return "O"


if __name__ == "__main__":
    for p in sys.argv[1:]:
        print(p, stall_class(p), captures(p))
