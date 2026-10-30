#!/bin/busybox sh
# PID1 of L2: start PostgreSQL on the data disk, prewarm, run pgbench -S once, print markers, idle.
/bin/busybox --install -s /bin
mount -t proc proc /proc; mount -t sysfs sys /sys; mount -t devtmpfs dev /dev; mount -t tmpfs tmp /tmp
mkdir -p /dev/shm /data; mount -t tmpfs shm /dev/shm; chmod 1777 /tmp /dev/shm
ip link set lo up
C=100; T=1000; SB=512MB
for a in $(cat /proc/cmdline); do case $a in pg_clients=*) C=${a#pg_clients=};; pg_txns=*) T=${a#pg_txns=};; pg_sb=*) SB=${a#pg_sb=};; esac; done
mount -t ext4 /dev/vda /data || { echo "L2_ERROR mount"; echo "L2BENCH_END 0 L2BENCH_RC=90"; while :; do sleep 3600; done; }
B=/usr/lib/postgresql/16/bin
su -s /bin/sh postgres -c "$B/pg_ctl -D /data/pg -l /tmp/pg.log -w -t 3600 -o '-c shared_buffers=$SB' start" || { cat /tmp/pg.log; echo "L2BENCH_END 0 L2BENCH_RC=91"; while :; do sleep 3600; done; }
Q="$B/psql -h /tmp -d bench -qAt"
su -s /bin/sh postgres -c "$Q -c 'create extension if not exists pg_prewarm'" > /tmp/prewarm.out 2>&1
su -s /bin/sh postgres -c "$Q -F ' ' -c \"select c.oid, pg_relation_size(c.oid)/8192 from pg_class c join pg_namespace s on s.oid=c.relnamespace where s.nspname='public' and c.relkind in ('r','i')\"" > /tmp/rels.txt 2>>/tmp/prewarm.out
: > /tmp/prewarm.sql
while read o n; do b=0; while [ $b -lt $n ]; do e=$((b+255)); [ $e -ge $n ] && e=$((n-1)); echo "select pg_prewarm($o::regclass,'buffer','main',$b,$e); select pg_sleep(0.5);" >> /tmp/prewarm.sql; b=$((b+256)); done; done < /tmp/rels.txt
chmod 644 /tmp/prewarm.sql
su -s /bin/sh postgres -c "$Q -f /tmp/prewarm.sql" | awk 'NF==1 && $1 ~ /^[0-9]+$/ {t+=$1} END {print t}' >> /tmp/prewarm.out 2>&1
echo "L2_PREWARM_BLOCKS: $(cat /tmp/prewarm.out)"
echo "L2_MEM_BEFORE: $(grep -E 'MemAvailable|Shmem:|PageTables' /proc/meminfo | tr -s ' ' | tr '\n' ' ')"
grep -E '^(pgscan|pgsteal)' /proc/vmstat | awk '{s+=$2} END{print "L2_VMSTAT_RECLAIM_BEFORE " s}'
echo "L2BENCH_START $(cut -d' ' -f1 /proc/uptime) clients=$C txns=$T sb=$(su -s /bin/sh postgres -c "$Q -c 'show shared_buffers'")"
sleep 15  # harness polls every 5 s; START stats are taken before pgbench begins
su -s /bin/sh postgres -c "$B/pgbench -h /tmp -n -S -c $C -j 1 -t $T bench" > /tmp/bench.out 2>&1
RC=$?
cat /tmp/bench.out | grep -E 'number of|tps|latency|failed|error' | head -20
echo "L2_MEM_AFTER: $(grep -E 'MemAvailable|Shmem:|ShmemPmdMapped|PageTables' /proc/meminfo | tr -s ' ' | tr '\n' ' ')"
grep -E '^(pgscan|pgsteal)' /proc/vmstat | awk '{s+=$2} END{print "L2_VMSTAT_RECLAIM_AFTER " s}'
echo "L2BENCH_END $(cut -d' ' -f1 /proc/uptime) L2BENCH_RC=$RC"
while :; do sleep 3600; done
