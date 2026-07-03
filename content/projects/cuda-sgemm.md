+++
title = "CUDA SGEMM"
slug = "cuda-sgemm"
weight = 3
links = ["https://github.com/aaron-ang/cuda-sgemm"]
+++

A high-performance single-precision matrix-multiply (SGEMM) CUDA kernel tuned for the NVIDIA T4, reaching **4779.9 GFLOP/s at N = 2048** — approaching cuBLAS.

Each 16×16 thread block computes a 128×128 output tile of C (an 8×8 micro-tile per thread) over 32-deep K sub-tiles staged in shared memory. The kernel combines coalesced global loads, shared-memory staging with bounds checking for non-multiple-of-128 sizes, and register-blocked outer products with `#pragma unroll` to expose instruction-level parallelism. I used register-pressure, occupancy, and roofline analysis to reason about where the kernel is compute- versus memory-bound, and added a non-square variant. The repo includes the full write-up with the roofline model.

Tech stack: CUDA, C++.
