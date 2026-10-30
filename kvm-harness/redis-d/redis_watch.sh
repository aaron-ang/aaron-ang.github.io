#!/bin/bash
# Test D watcher (open3/PREREG.md Amendments 1-4, 9-11). Each scan runs d_findings.py (stateless; findings snapshot in
# SNAP). RUN, SERIAL and STALL-I findings are recorded; they halt the batch only through the patch-site rule
# (PATCHSITE) or the volume cap (CAP). A harness result (BADPARAM, NO_END, HARNESS_*, unknown) halts it as HARNESS.
# Check exit codes: 0 = halt, 1 = no halt, anything else = check error.
# Fails closed: two consecutive check errors halt the batch (CHECKERR).
# Exit: 0 after a clean scan that started after DONEF existed ("EXIT clean" in WLOG); 10 after a halt (reason in HALTF).
M=${M:-/tmp/kvmval/open3/manifest_d1.csv}
O=/tmp/kvmval/open3
R=/tmp/kvmval/camp/redis
LOGS=${LOGS:-$R/logs}
WLOG=${WLOG:-$O/redis_watch.log}
CHECK_ONLY=${CHECK_ONLY:-0}  # test mode: halt only logs
DS=${DS:-$O/D_STARTED}
SLEEP=${SLEEP:-30}
PORTS=${PORTS:-"10200 10201 10202 10203 10204 10205 10206 10207"}  # Amendment 9: up to 8 L1s
HALTF=${HALTF:-$O/D_HALTED}
SNAP=${SNAP:-$O/D_FINDINGS}
DONEF=${DONEF:?DONEF (touched by the runner when redis_camp returns) is required}
errs=0
halt() {
  echo "$(date +%T) HALT: $1" >> $WLOG
  [ "$CHECK_ONLY" = 1 ] && exit 0
  pkill -f "$R/redis_camp.py run" ; sleep 5; bash $R/poweroff_l1s.sh $PORTS >/dev/null 2>&1; sleep 120; /tmp/kvmval/clean_qemu.sh
  echo "$1" > $HALTF; exit 10
}
while true; do
  done=0; [ -f "$DONEF" ] && done=1  # sampled before the scan, so a clean exit always follows a full scan of the final state
  out=$(python3 -I $O/d_findings.py "$M" "$LOGS" "$DS" "$WLOG" "$SNAP" 2>&1)
  rc=$?
  if [ $rc -eq 0 ]; then
    halt "$out"
  elif [ $rc -ne 1 ]; then
    errs=$((errs + 1))
    echo "$(date +%T) WATCHER ERROR $errs (rc=$rc): $out" >> $WLOG
    [ $errs -ge 2 ] && halt "CHECKERR watcher check failed twice in a row: $out"
  else
    errs=0
    [ $done = 1 ] && { echo "$(date +%T) EXIT clean" >> $WLOG; exit 0; }
  fi
  sleep $SLEEP
done
