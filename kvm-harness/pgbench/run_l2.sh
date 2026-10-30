#!/bin/bash
# v3 (campaign; v2 + L2STALL_MARKER host-capture hook). Run INSIDE L1 as root.  Usage: run_l2.sh TAG LRU_MMU LRU_AGE MIN_ALLOC_PAGES
# Sets kvm params (lru_mark_fill=1 always), boots a fresh L2 (PostgreSQL+pgbench, initramfs + fresh qcow2 overlay of data.img), samples a few
# per-VM debugfs stats, takes a FINAL sample after the benchmark ends (VM still alive), kills QEMU.
# Stall = pf_taken unchanged for STALL_S seconds (wall clock): capture L2 regs/stack via HMP,
# L1 sysrq-l backtraces, then NMI into L2 (unknown_nmi_panic=1 -> L2 prints its stack).
TAG=$1; LRU=$2; AGE=$3; MAP=$4
L2MEM=${L2MEM:-2G}; L2SMP=${L2SMP:-1}; TMO=${TMO:-7200}; STALL_S=${STALL_S:-600}
D=/root/pg; LOG=$D/logs/$TAG.log; SER=$D/logs/$TAG.serial; mkdir -p $D/logs
P=/sys/module/kvm/parameters; K=/sys/kernel/debug/kvm
mountpoint -q /sys/kernel/debug || mount -t debugfs none /sys/kernel/debug
echo $LRU > $P/lru_mmu; echo $AGE > $P/lru_age; echo $MAP > $P/min_alloc_pages; echo ${FILL:-1} > $P/lru_mark_fill
STATS="pf_taken pf_fixed mmu_shadow_zapped mmu_recycled mmu_cache_miss mmu_pte_write mmu_flooded mmu_unsync lru_hand_steps lru_ref_skips lru_age_sptes lru_age_skips lru_age_flushes exits irq_exits insn_emulation halt_exits invlpg"
dump() { for f in $STATS; do printf "%s=%s " $f "$(cat $1/$f 2>/dev/null)"; done; echo; }
{ echo "TAG=$TAG lru_mmu=$(cat $P/lru_mmu) lru_age=$(cat $P/lru_age) min_alloc_pages=$(cat $P/min_alloc_pages) lru_mark_fill=$(cat $P/lru_mark_fill) tdp_mmu=$(cat $P/tdp_mmu) npt=$(cat /sys/module/kvm_amd/parameters/npt 2>/dev/null) ept=$(cat /sys/module/kvm_intel/parameters/ept 2>/dev/null) L2MEM=$L2MEM L2SMP=$L2SMP kernel=$(uname -r) uptime=$(cut -d' ' -f1 /proc/uptime)"
  echo "GLOBAL_BEFORE $(dump $K)"; } > $LOG
dmesg -C
mountpoint -q /boot || mount /boot
KVER=$(ls /boot/vmlinuz-*-generic | sort -V | tail -1)
[ -n "$KVER" ] || { echo "RESULT=HARNESS_NO_KERNEL" >> $LOG; exit 3; }
# v3: count #PF-intercept exits (SVM exit 0x4e) that carry a valid EXITINTINFO (event being delivered)
TI=/sys/kernel/tracing; [ -d $TI/events ] || mount -t tracefs none $TI; TI=$TI/instances/l2x; mkdir -p $TI 2>/dev/null
echo 0 > $TI/tracing_on; echo 4096 > $TI/buffer_size_kb; echo > $TI/trace
echo 'exit_reason==0x4e && intr_info!=0' > $TI/events/kvm/kvm_exit/filter; echo 1 > $TI/events/kvm/kvm_exit/enable; echo 1 > $TI/tracing_on
T0=$(date +%s)
rm -f $SER
rm -f $D/run.qcow2; qemu-img create -q -f qcow2 -F raw -b $D/data.img $D/run.qcow2
STARTED=0
qemu-system-x86_64 -nodefaults -vga none -enable-kvm -cpu host -m $L2MEM -smp $L2SMP -kernel $KVER -initrd $D/initramfs2.cpio.gz \
  -append "console=ttyS0 quiet panic=-1 unknown_nmi_panic=1 ${L2APPEND}" -display none -monitor unix:$D/logs/$TAG.mon,server,nowait -serial file:$SER \
  -drive file=$D/run.qcow2,if=virtio,format=qcow2,cache=unsafe -net none -no-reboot >> $LOG 2>&1 &
QP=$!
echo "L2 qemu pid=$QP kernel=$KVER" >> $LOG
VD=""; for i in $(seq 1 120); do VD=$(ls -d $K/${QP}-* 2>/dev/null | head -1); [ -n "$VD" ] && break; sleep 1; done
echo "VMDIR=$VD" >> $LOG
LASTPF=""; LASTCHG=$(date +%s); RESULT=NO_END
while kill -0 $QP 2>/dev/null; do
  NOW=$(date +%s); T=$((NOW - T0))
  if [ $STARTED = 0 ] && grep -q L2BENCH_START $SER 2>/dev/null; then echo "START t=$T $(dump $VD)" >> $LOG; STARTED=1; fi
  if grep -q L2BENCH_END $SER 2>/dev/null; then echo "FINAL t=$T $(dump $VD)" >> $LOG; RESULT=END; break; fi
  if grep -q "KVM internal error" $LOG; then
    echo "KVM_INTERNAL_ERROR detected at t=$T (L2 qemu paused the VM)" >> $LOG; RESULT=KVM_INTERNAL_ERROR
    echo "S t=$T $(dump $VD)" >> $LOG
    python3 $D/hmp.py $D/logs/$TAG.mon "info registers" "info lapic" "info cpus" "info status" >> $LOG 2>&1
    break
  fi
  CUR="$(dump $VD)"; echo "S t=$T $CUR" >> $LOG
  PF=$(echo "$CUR" | grep -oE '(^| )pf_taken=[0-9]*')
  if [ "$PF" != "$LASTPF" ]; then LASTPF=$PF; LASTCHG=$NOW; fi
  if [ $((NOW - LASTCHG)) -ge $STALL_S ]; then
    echo "STALL: pf_taken unchanged for $((NOW - LASTCHG))s at t=$T" >> $LOG; RESULT=STALL
    # v3: tell the host harness (it tails the L1 serial) to capture outer-QEMU host stacks, untouched state
    echo "<2>L2STALL_MARKER $TAG" > /dev/kmsg; echo "L2STALL_MARKER sent $(date +%T); waiting ${MARK_WAIT:-180}s" >> $LOG
    sleep ${MARK_WAIT:-180}; echo "S_AFTER_WAIT $(dump $VD)" >> $LOG
    { echo "--- L2 qemu threads in L1 (3 samples, 5 s apart):"
      for i in 1 2 3; do echo "== sample $i $(date +%T)"
        for t in /proc/$QP/task/*; do echo "tid $(basename $t) comm=$(cat $t/comm) state=$(awk '{print $3}' $t/stat) utime=$(awk '{print $14}' $t/stat) stime=$(awk '{print $15}' $t/stat) wchan=$(cat $t/wchan)"; cat $t/stack; done
        [ $i -lt 3 ] && sleep 5; done
      for c in $VD/vcpu*; do echo "== $c"; for f in $c/*; do [ -f $f ] && echo "$(basename $f)=$(cat $f 2>/dev/null | head -c 200)"; done; done; } >> $LOG 2>&1
    echo "--- L1 lockdep/hung-task state:"  >> $LOG
    { grep -E "debug_locks|lock-classes" /proc/lockdep_stats; sysctl kernel.hung_task_warnings kernel.hung_task_timeout_secs; } >> $LOG 2>&1
    echo w > /proc/sysrq-trigger; sleep 2; echo d > /proc/sysrq-trigger; sleep 2
    TR=/sys/kernel/tracing; [ -d $TR/events ] || mount -t tracefs none $TR
    { echo "--- kvm ftrace 10 s:"; echo > $TR/trace; echo 1 > $TR/events/kvm/enable; echo 1 > $TR/events/sched/sched_switch/enable
      echo 1 > $TR/events/sched/sched_wakeup/enable; echo 1 > $TR/tracing_on; sleep 10; echo 0 > $TR/tracing_on
      echo 0 > $TR/events/kvm/enable; echo 0 > $TR/events/sched/enable
      grep -E "CPU [0-9]/KVM|$QP|kvm" $TR/trace | tail -300; echo > $TR/trace; } >> $LOG 2>&1
    python3 $D/hmp.py $D/logs/$TAG.mon "info registers" "x/24i \$pc-0x20" "x/64gx \$rsp" "info lapic" "info cpus" >> $LOG 2>&1
    echo l > /proc/sysrq-trigger; sleep 5
    python3 $D/hmp.py $D/logs/$TAG.mon "nmi" >> $LOG 2>&1; sleep 30
    break
  fi
  [ $T -gt $TMO ] && { echo "TIMEOUT" >> $LOG; RESULT=TIMEOUT; break; }
  sleep 5
done
kill -0 $QP 2>/dev/null || [ $RESULT != NO_END ] || RESULT=L2_EXITED
T1=$(date +%s)
kill $QP 2>/dev/null; wait $QP 2>/dev/null
echo 0 > $TI/tracing_on; echo 0 > $TI/events/kvm/kvm_exit/enable
echo "PF_EXITINTINFO=$(grep -c 'reason' $TI/trace) (#PF intercepts with valid EXITINTINFO; first 40 below)" >> $LOG
grep 'reason' $TI/trace | head -40 >> $LOG; echo > $TI/trace
echo "RESULT=$RESULT" >> $LOG
echo "ELAPSED_S=$((T1-T0))" >> $LOG
echo "GLOBAL_AFTER $(dump $K)" >> $LOG
echo "--- L2 serial:" >> $LOG; cat $SER >> $LOG
echo "--- dmesg delta:" >> $LOG; dmesg >> $LOG
echo "SLEEP_SPLATS=$(dmesg | grep -c 'sleeping function called from invalid context')" >> $LOG
echo "OTHER_SPLATS=$(dmesg | grep -E 'BUG:|WARNING:|KASAN|Oops|lockdep|circular|soft lockup|hard LOCKUP|rcu.*stall|general protection' | grep -vc 'sleeping function')" >> $LOG
grep -E '^RESULT|L2BENCH_END|ELAPSED|SPLATS' $LOG | tail -6
