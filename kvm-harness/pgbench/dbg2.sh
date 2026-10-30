#!/bin/bash
# inside L1: boot L2 (MEM) with monitor; after 480 s sample RIP 5x and resolve against System.map; then kill
MEM=$1; cd /root/pg; T=dbg-$MEM
pkill -f "qemu-system-x86_64 -nodefaults"; sleep 2
rm -f run-$MEM.qcow2; qemu-img create -q -f qcow2 -F raw -b data.img run-$MEM.qcow2
K=$(ls /boot/vmlinuz-*-generic | sort -V | tail -1); V=${K#/boot/vmlinuz-}
qemu-system-x86_64 -nodefaults -vga none -enable-kvm -cpu host -m $MEM -smp 1 -kernel $K -initrd dbg.cpio.gz \
  -append "console=ttyS0 panic=-1 nokaslr pg_clients=4 pg_txns=10" -display none -serial file:logs/$T.serial \
  -monitor unix:logs/$T.mon,server,nowait -drive file=run-$MEM.qcow2,if=virtio,format=qcow2,cache=unsafe -net none -no-reboot > logs/$T.qemu 2>&1 &
QP=$!; sleep 480
for i in 1 2 3 4 5; do python3 hmp.py logs/$T.mon "info registers" | grep -oE "RIP=[0-9a-f]+" ; sleep 3; done > logs/$T.rips
while read l; do r=${l#RIP=}; awk -v a=$r 'BEGIN{t=strtonum("0x" a)} {v=strtonum("0x" $1); if (v<=t) {best=$3} else {print a, best; exit}}' /boot/System.map-$V; done < logs/$T.rips > logs/$T.syms
python3 hmp.py logs/$T.mon "info registers" > logs/$T.regs
tail -3 logs/$T.serial > logs/$T.tail; kill $QP
