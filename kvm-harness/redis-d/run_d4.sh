#!/bin/bash
# Amendment 11 Part B (open3/PREREG.md). After D_GO, D runs under MTTCG only (4 L1s, bzImage-camp-3l) with rule (b)
# replacement rounds, each round under its own watcher (redis_watch.sh -> d_findings.py). RUN, SERIAL and STALL-I
# findings are recorded, not halted on. Any watcher halt (PATCHSITE, CAP, HARNESS, CHECKERR) or harness anomaly stops
# the runner with D_ATTN. Rule (d) is reported only. Replaces run_d3.sh: no single-thread restart, no s10r gate.
V=/tmp/kvmval; O=$V/open3; R=$V/camp/redis
log() { echo "[$(date '+%F %T')] $*" >> $O/run_d4.log; }
attn() { log "ATTN: $*"; touch $O/D_ATTN; exit 0; }
trap "touch $O/D_ACCEL_STOP" EXIT
echo $$ > $O/run_d4.pid

# Record every L1's accel string from /proc/<pid>/cmdline (once per pid).
recaccel() {
  while [ ! -f $O/D_ACCEL_STOP ]; do
    for p in $(pgrep -x qemu-system-x86); do
      c=$(tr '\0' ' ' < /proc/$p/cmdline 2>/dev/null) || continue
      i=$(grep -o 'l1-i[0-9]*\.qcow2' <<<"$c"); [ -n "$i" ] || continue
      grep -q "^$p " $O/d_accel.log 2>/dev/null || echo "$p $(date '+%F %T') $i $(grep -o -- '-accel [^ ]*' <<<"$c")" >> $O/d_accel.log
    done
    sleep 30
  done
}

# One redis_camp invocation under its own watcher. $1 batch $2 accel $3 instances $4 suffix $5 runs json $6 round
# Returns 0 after a clean watcher exit, 10 after a watcher halt (reason in D_HALTED$4); anything else stops the runner.
batch() {
  local b=$1 acc=$2 n=$3 s=$4 jf=$5 rd=$6
  local donef=$O/D_RCDONE$s.$rd
  local nr=$(python3 -I -c 'import json, sys; print(len(json.load(open(sys.argv[1]))))' $jf 2>/dev/null)
  [ "${nr:-0}" -ge 1 ] 2>/dev/null || attn "empty or unreadable run list $jf"
  [ $nr -lt $n ] && n=$nr  # a replacement round boots only as many L1s as it has runs
  local ports="$(seq -s ' ' 10200 $((10199 + n)))"
  rm -f $donef
  M=$O/manifest_$b.csv DS=$O/D_STARTED$s WLOG=$O/redis_watch$s.log HALTF=$O/D_HALTED$s SNAP=$O/D_FINDINGS$s DONEF=$donef \
    PORTS="$ports" setsid nohup $O/redis_watch.sh >> $O/redis_watch$s.log 2>&1 < /dev/null &
  local wp=$!
  log "batch $b round $rd start: $(basename $jf) ACCEL=$acc -n $n RUN_TMO=$RUN_TMO STALL_S=$STALL_S BOOT_S=$BOOT_S watcher pid $wp"
  (cd $R && ACCEL=$acc KERNEL=$V/camp/bzImage-camp-3l MANIFEST=$O/manifest_$b.csv RUN_TMO=$RUN_TMO STALL_S=$STALL_S \
    BOOT_S=$BOOT_S python3 $R/redis_camp.py run -n $n $jf >> $O/$b.out 2>&1)
  local rc=$?
  # An L1 qemu can outlive redis_camp (Popen children survive python's exit), and it would keep writing its serial
  # after the final scan. Power off and sweep before the done file. Overlays are reused, so power off before killing.
  if [ -n "$(pgrep -x qemu-system-x86)" ]; then
    log "batch $b round $rd: qemu alive after redis_camp returned: $(pgrep -x qemu-system-x86 | tr '\n' ' ')"
    bash $R/poweroff_l1s.sh $ports >/dev/null 2>&1
    for i in $(seq 24); do [ -z "$(pgrep -x qemu-system-x86)" ] && break; sleep 5; done
    log "batch $b round $rd sweep: $($V/clean_qemu.sh | tr '\n' ' ')"
    for i in $(seq 12); do [ -z "$(pgrep -x qemu-system-x86)" ] && break; sleep 5; done
    [ -z "$(pgrep -x qemu-system-x86)" ] || attn "qemu still running after the sweep: $(pgrep -x qemu-system-x86 | tr '\n' ' ')"
  fi
  touch $donef
  log "batch $b round $rd redis_camp rc=$rc; waiting for watcher $wp"
  wait $wp; local wrc=$?
  log "batch $b round $rd watcher rc=$wrc"
  if [ $wrc -eq 10 ] && [ -s $O/D_HALTED$s ]; then return 10; fi
  [ $wrc -eq 0 ] && [ "$(tail -1 $O/redis_watch$s.log | cut -d' ' -f2-)" = "EXIT clean" ] ||
    attn "watcher $wp ended rc=$wrc without EXIT clean or a halt reason"
  [ $rc -eq 0 ] || attn "redis_camp $b round $rd exited rc=$rc without a watcher halt"
  return 0
}

# All rounds of one mode. $1 batch $2 accel $3 instances $4 suffix. Returns 0 when complete, 10 on a halt.
mode() {
  local b=$1 acc=$2 n=$3 s=$4 rd=0
  local jfs=($R/runs/$b.json)
  touch $O/D_STARTED$s
  while :; do
    batch $b $acc $n "$s" ${jfs[-1]} $rd || return $?
    rd=$((rd + 1))
    local nj=$R/runs/${b}_rep$rd.json
    python3 -I $O/d_rounds.py next $O/manifest_$b.csv $b $nj "${jfs[@]}" > $O/${b}_rep$rd.txt 2>&1; local nrc=$?
    log "rule (b) $b after round $((rd - 1)): $(tail -1 $O/${b}_rep$rd.txt) (rc=$nrc)"
    [ $nrc -eq 1 ] && break
    [ $nrc -eq 0 ] || attn "d_rounds.py next $b round $rd rc=$nrc: $(grep ANOMALY $O/${b}_rep$rd.txt | head -3 | tr '\n' ' ')"
    jfs+=($nj)
  done
  python3 -I $O/d_rounds.py check $O/manifest_$b.csv $b "${jfs[@]}" > $O/${b}_check.txt 2>&1 ||
    attn "d_rounds.py check $b: $(grep ANOMALY $O/${b}_check.txt | head -3 | tr '\n' ' ')"
  log "$b complete after $rd redis_camp invocations ($(grep -c ' short (' $O/${b}_check.txt) cells short: $(grep ' short (' $O/${b}_check.txt | tr -s ' ' | tr '\n' ';'))"
  return 0
}

cd $V
rm -f $O/D_ACCEL_STOP
log "run_d4 start pid $$"
until [ -f $O/D_GO ]; do sleep 30; done
log "D_GO seen"
[ -z "$(pgrep -f '[r]edis_camp.py run')" ] || attn "a redis_camp run process exists at D start"
[ ! -e $O/manifest_d1.csv ] && [ -z "$(ls $R/logs/d1-*.claim 2>/dev/null)" ] || attn "d1 data already exists"
$V/clean_qemu.sh
[ "$(pgrep -x qemu-system-x86 | wc -l)" -eq 0 ] || attn "qemu still running at D start: $(pgrep -x qemu-system-x86 | tr '\n' ' ')"
recaccel &
RUN_TMO=7200 STALL_S=600 BOOT_S=5400
mode d1 tcg,thread=multi 4 ""; mrc=$?
[ $mrc -eq 10 ] && attn "D halted: $(cat $O/D_HALTED)"
python3 -I $O/fisher_d.py $O/manifest_d1.csv > $O/fisher_d1.txt 2>&1; frc=$?
[ $frc -ge 2 ] && attn "fisher_d.py error on d1: $(tail -1 $O/fisher_d1.txt)"
log "rule (d) on d1: $(tail -1 $O/fisher_d1.txt) (rc=$frc; reported as a finding if p < 0.05, no switch)"
log "findings: $(python3 -I -c 'import json, sys; d = json.load(open(sys.argv[1])); print(d["total"], "total", d["per_policy"], "serial", d["serial_findings"])' $O/D_FINDINGS 2>&1)"
touch $O/D_DONE
log "D complete under MTTCG"
