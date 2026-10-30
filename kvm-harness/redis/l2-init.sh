#!/bin/busybox sh
# PID1 of L2 guest: start redis-server on loopback, run redis-benchmark, print markers, then idle
# (L1 harness reads KVM per-VM debugfs stats, then quits QEMU via monitor).
/bin/busybox --install -s /bin
mount -t proc proc /proc; mount -t sysfs sys /sys; mount -t devtmpfs dev /dev; mount -t tmpfs tmp /tmp
ip link set lo up
BENCH=$(cat /bench.args)
for a in $(cat /proc/cmdline); do case $a in bench_n=*) N=${a#bench_n=};; esac; done
[ -n "$N" ] && BENCH=$(echo "$BENCH" | sed "s/-n [0-9]*/-n $N/")
echo "L2_MEM: $(grep MemTotal /proc/meminfo)"
/bin/redis-server --save '' --appendonly no --daemonize yes --bind 127.0.0.1 --port 6379 --logfile /tmp/redis.log
i=0; until /bin/redis-cli ping 2>/dev/null | grep -q PONG; do i=$((i+1)); [ $i -gt 600 ] && break; sleep 0.5; done
echo "L2BENCH_START $(cut -d' ' -f1 /proc/uptime) args: $BENCH"
/bin/redis-benchmark $BENCH </dev/null >/tmp/bench.out 2>&1
RC=$?
tr '\r' '\n' </tmp/bench.out | grep -v 'rps=' | grep .
echo "L2_DBSIZE: $(/bin/redis-cli dbsize) INFO_MEM: $(/bin/redis-cli info memory | grep used_memory_human)"
echo "L2BENCH_END $(cut -d' ' -f1 /proc/uptime) L2BENCH_RC=$RC"
while :; do sleep 3600; done
