#!/bin/bash
# Run INSIDE L1 as root. Builds /root/l2/initramfs.cpio.gz with busybox + redis + shared libs.
set -e
BENCH_ARGS=${BENCH_ARGS:-"-n 20000 -r 100000 -P 16 -q -t set,get,lpush,lpop"}
R=$(mktemp -d); mkdir -p $R/{bin,proc,sys,dev,tmp,etc,lib64}
cp /bin/busybox $R/bin/busybox
for b in redis-server redis-benchmark redis-cli; do cp -L $(command -v $b) $R/bin/$b
  for l in $(ldd $(command -v $b) | grep -o '/[^ ]*'); do mkdir -p $R$(dirname $l); cp -L $l $R$l; done; done
cp /root/l2/l2-init.sh $R/init; chmod +x $R/init
echo "$BENCH_ARGS" > $R/bench.args
echo 'root:x:0:0:root:/:/bin/sh' > $R/etc/passwd
mkdir -p /root/l2; (cd $R && find . | cpio -o -H newc --quiet | gzip -1) > /root/l2/initramfs.cpio.gz
rm -rf $R; ls -la /root/l2/initramfs.cpio.gz
