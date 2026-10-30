#!/bin/bash
# inside L1: build debug initramfs from the real one (init with progress markers) and boot L2 for 600 s, serial to dbg.serial
cd /root/pg; pkill -f run_l2.sh; pkill -f "qemu-system-x86_64 -nodefaults"; sleep 2
rm -rf /tmp/dbgi; mkdir /tmp/dbgi; cd /tmp/dbgi; zcat /root/pg/initramfs.cpio.gz | cpio -idm --quiet
sed -i 's|^mount -t ext4 /dev/vda /data|echo DBG mounting $(cut -d" " -f1 /proc/uptime); sleep 2; echo DBG slept2 $(cut -d" " -f1 /proc/uptime)\nmount -t ext4 /dev/vda /data|' init
sed -i 's|^B=/usr/lib/postgresql/16/bin$|B=/usr/lib/postgresql/16/bin; echo DBG mounted $(cut -d" " -f1 /proc/uptime); ls -la /data/pg \| head -3; (while :; do sleep 20; echo DBG tick $(cut -d" " -f1 /proc/uptime); tail -2 /tmp/pg.log; done) \&|' init
grep -n DBG init | head
find . | cpio -o -H newc --quiet | gzip -1 > /root/pg/dbg.cpio.gz; cd /root/pg
rm -f run.qcow2; qemu-img create -q -f qcow2 -F raw -b data.img run.qcow2
K=$(ls /boot/vmlinuz-*-generic | sort -V | tail -1)
timeout 900 qemu-system-x86_64 -nodefaults -vga none -enable-kvm -cpu host -m 2G -smp 1 -kernel $K -initrd dbg.cpio.gz \
  -append "console=ttyS0 panic=-1 pg_clients=4 pg_txns=10" -display none -serial file:/root/pg/logs/dbg.serial \
  -drive file=run.qcow2,if=virtio,format=qcow2,cache=unsafe -net none -no-reboot > /root/pg/logs/dbg.qemu 2>&1
echo DBG_QEMU_EXIT $? >> /root/pg/logs/dbg.serial
