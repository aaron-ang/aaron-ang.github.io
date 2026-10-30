#!/bin/bash
for port in "$@"; do timeout 300 ssh -q -i /tmp/kvmval/l2/id_l1 -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -o ConnectTimeout=30 -p $port root@127.0.0.1 'sync; poweroff' & done; wait
