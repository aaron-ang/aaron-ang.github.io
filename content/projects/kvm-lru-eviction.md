+++
title = "KVM Approximate-LRU Page Eviction"
slug = "kvm-lru-eviction"
weight = 4
links = ["https://github.com/aaron-ang/kvm", "https://drive.google.com/file/d/18Hb5Ug48UfW9TsPBnSU7noVIKErcJLc3/view"]
+++

Replaced the **FIFO** page-eviction policy in the Linux **KVM** hypervisor's shadow-paging MMU with an **approximate LRU** (second-chance clock) policy, so that frequently accessed guest pages stay resident and page faults drop.

Working in the kernel's `mmu.c`, I added lightweight per-page recency tracking and reworked `mmu_zap_oldest_pages()` to evict by access recency rather than insertion order, keeping the eviction path thread-safe across multiple vCPUs. I benchmarked against FIFO with `stress-ng`, `redis-benchmark`, and flamegraph profiling on an AWS bare-metal instance: Approximate LRU reduced page faults (112,016 vs. 117,375 under `stress-ng`) and sustained lower latency at high load, at the cost of slightly higher CPU overhead from the recency tracking.

Tech stack: C, Linux kernel, KVM, QEMU.
