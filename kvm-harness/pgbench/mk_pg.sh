#!/bin/bash
# Run INSIDE L1 as root, once. Builds /root/pg/data.img (ext4, PGDATA + pgbench DB) and /root/pg/initramfs.cpio.gz.
set -e
D=/root/pg; SCALE=${SCALE:-40}; mkdir -p $D/logs; cd $D
B=/usr/lib/postgresql/16/bin
if [ ! -f data.img ]; then
  truncate -s 3G data.img; mkfs.ext4 -q -F data.img; mkdir -p /mnt/pgd; mount -o loop data.img /mnt/pgd
  mkdir /mnt/pgd/pg; chown postgres:postgres /mnt/pgd/pg
  su postgres -c "$B/initdb -D /mnt/pgd/pg --locale=C -E UTF8 >/dev/null"
  cat >> /mnt/pgd/pg/postgresql.conf <<C
shared_buffers = 512MB
huge_pages = off
max_connections = 210
listen_addresses = ''
unix_socket_directories = '/tmp'
fsync = off
synchronous_commit = off
full_page_writes = off
autovacuum = off
logging_collector = off
C
  su postgres -c "$B/pg_ctl -D /mnt/pgd/pg -l /tmp/pgbuild.log -w start"
  su postgres -c "$B/createdb -h /tmp bench && $B/pgbench -h /tmp -i -q -s $SCALE bench && $B/psql -h /tmp -d bench -c 'create extension pg_prewarm'"
  su postgres -c "$B/psql -h /tmp -d bench -Atc \"select pg_size_pretty(pg_database_size('bench'))\""
  su postgres -c "$B/pg_ctl -D /mnt/pgd/pg -w stop"; umount /mnt/pgd
fi
R=$(mktemp -d); chmod 755 $R; mkdir -p $R/{bin,proc,sys,dev,tmp,etc,data,var/run/postgresql}; chmod 1777 $R/tmp
cp /bin/busybox $R/bin/busybox
for f in $B/postgres $B/pg_ctl $B/psql $B/pgbench; do
  mkdir -p $R$(dirname $f); cp -L $f $R$f
  for l in $(ldd $f | grep -o '/[^ ]*'); do mkdir -p $R$(dirname $l); cp -L $l $R$l; done; done
mkdir -p $R/usr/lib/postgresql/16/lib $R/usr/share/postgresql/16
cp -a /usr/lib/postgresql/16/lib/pg_prewarm.so $R/usr/lib/postgresql/16/lib/
for l in $(ldd /usr/lib/postgresql/16/lib/pg_prewarm.so | grep -o '/[^ ]*'); do mkdir -p $R$(dirname $l); cp -L $l $R$l; done
cp -a /usr/share/postgresql/16/{timezonesets,extension} $R/usr/share/postgresql/16/ 2>/dev/null || true
mkdir -p $R/usr/share/zoneinfo; cp -a /usr/share/zoneinfo/Etc $R/usr/share/zoneinfo/ 2>/dev/null || true
cp -L /etc/localtime $R/etc/localtime 2>/dev/null || true
grep -E '^(root|postgres):' /etc/passwd > $R/etc/passwd; grep -E '^(root|postgres):' /etc/group > $R/etc/group
chown postgres:postgres $R/var/run/postgresql
cp $D/l2-init-pg.sh $R/init; chmod +x $R/init
(cd $R && find . | cpio -o -H newc --quiet | gzip -1) > $D/initramfs.cpio.gz
rm -rf $R; ls -la $D/initramfs.cpio.gz $D/data.img
