#!/usr/bin/env python3
# Send HMP commands to a QEMU unix monitor socket; print responses. Usage: hmp.py SOCK CMD...
import socket, sys, time
s = socket.socket(socket.AF_UNIX); s.connect(sys.argv[1]); s.settimeout(5)
def rd():
    out = b""
    try:
        while True:
            d = s.recv(65536)
            if not d: break
            out += d
            if out.endswith(b"(qemu) "): break
    except socket.timeout: pass
    return out.decode(errors="replace")
rd()
for c in sys.argv[2:]:
    s.sendall((c + "\n").encode()); print("### " + c); print(rd())
