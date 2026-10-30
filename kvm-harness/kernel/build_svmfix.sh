#!/bin/bash
X=x86_64-linux-gnu-
for p in "src kvm-build-knob bzImage-camp-svmfix" "upstream-src kvm-build-upstream bzImage-upstream-svmfix"; do set -- $p
  make -C /tmp/kvmval/camp/$1 O=/tmp/$2 ARCH=x86_64 CROSS_COMPILE=$X -j20 bzImage > /tmp/kvmval/camp/build-$3.log 2>&1 && cp /tmp/$2/arch/x86/boot/bzImage /tmp/kvmval/camp/$3 && echo "built $3"
done
md5sum /tmp/kvmval/camp/bzImage-*
