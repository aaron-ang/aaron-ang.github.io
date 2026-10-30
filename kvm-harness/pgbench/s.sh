#!/bin/bash
exec ssh -q -i /tmp/kvmval/l2/id_l1 -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -o ServerAliveInterval=30 -p ${PORT:-10300} root@127.0.0.1 "$@"
