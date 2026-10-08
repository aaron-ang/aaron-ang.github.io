+++
title = "Optimizing Matrix Multiply on CUDA"
date = "2026-09-27"
slug = "optimizing-cuda-sgemm"
tags = ["Engineering", "Research"]
+++

A neural network on a GPU spends most of its time multiplying two matrices. Attention reduces to that one operation, and so do fully connected layers and convolutions. For 32-bit floats the routine is called **SGEMM** (single-precision [general matrix multiply](https://en.wikipedia.org/wiki/Basic_Linear_Algebra_Subprograms#Level_3)). GPU vendors spend a lot of effort tuning it, and NVIDIA ships its version in [cuBLAS](https://developer.nvidia.com/cublas).

The hard part of SGEMM is not the arithmetic but the data. An [NVIDIA T4](https://www.nvidia.com/en-us/data-center/tesla-t4/) can do about 24 floating-point operations for every byte it fetches from its main memory. The textbook matrix multiply does only 0.25 operations per byte.

My teammate Jerry Ma and I took a graduate course at UCSD where the assignment was to write our own SGEMM _kernel_. A kernel is a function that runs on the GPU. We used [CUDA](https://docs.nvidia.com/cuda/cuda-programming-guide/index.html) on the T4 and tried to get as close to cuBLAS as we could. The first kernel was correct but naive, running at around 100 GFLOP/s (billion floating-point operations per second). The last one hit **4,780 GFLOP/s** at $N = 2048$. Measured on the same T4, cuBLAS ran at roughly the same speed at that size. The code is on [GitHub](https://github.com/aaron-ang/cuda-sgemm).

I went into the project thinking that GPU performance was about parallelism, with more threads meaning more speed. Parallelism turned out to be the easy part, since the naive kernel already had one thread per output. The real gains all came from moving data; the arithmetic barely changed from the first kernel to the last.

The first half of this post follows the data through three kinds of reuse on the T4. Neighboring threads share each fetch from memory, a group of threads shares one on-chip copy of the data it needs, and each thread reuses the values it already holds. An analysis of the final T4 kernel then names the three limits that remained: instruction overhead, register pressure, and rounds of work that only partly fill the GPU.

The second half covers a follow-up that I did alone in late summer 2026 on a newer personal GPU, an NVIDIA GB10. Each of its sections takes up one limit from the T4 and measures the fix against cuBLAS on the GB10. A final section covers a power limit that the T4 analysis did not examine. By the end, the kernel matched cuBLAS on that GPU.

We borrowed heavily from Simon Boehm's worklog, [_How to Optimize a CUDA Matmul Kernel for cuBLAS-like Performance_](https://siboehm.com/articles/22/CUDA-MMM). I recommend it to anyone curious. This post covers the same ideas with more background. For each optimization it explains the cause, the fix, and the measured result.

## Counting the Work

Take two $N \times N$ matrices, $A$ and $B$. Their product $C$ has $N^2$ entries, and this post calls each entry $C[i][j]$ an _output_. One output is the dot product of row $i$ of $A$ with column $j$ of $B$. That dot product takes $N$ multiplications and $N$ additions. Over all $N^2$ outputs, the total comes to $2N^3$ floating-point operations (_flops_). At $N = 2048$ it is about 17 billion.

Each output costs $N$ multiply-adds, where a multiply-add is one multiplication plus one addition. Neighboring outputs also read the same data again:

![One entry of C forms as the dot product of a row of A and a column of B, one multiply-add at a time. The next entry reads the same row of A again.](/images/optimizing-cuda-sgemm/matmul-dot.svg "No kernel can skip any of the $N$ multiply-adds per output. A faster kernel can only fetch each value from main memory fewer times.")

Every optimization in this post attacks that re-reading, because a T4 can do arithmetic far faster than it can fetch numbers from its main memory:

- **Compute:** 40 [streaming multiprocessors](https://docs.nvidia.com/cuda/cuda-programming-guide/01-introduction/programming-model.html#gpu-hardware-model) (SMs) × 64 floating-point units × ~1.5 GHz × 2 flops per [fused multiply-add](https://en.wikipedia.org/wiki/Multiply%E2%80%93accumulate_operation#Fused_multiply%E2%80%93add) ≈ **7,680 GFLOP/s**.
- **Memory:** about **320 GB/s**. The main memory is off-chip GDDR6, and CUDA calls it _global memory_.

Dividing 7,680 by 320 gives about 24 flops per byte. To keep the arithmetic units busy, a kernel has to do at least 24 flops for every byte it fetches from global memory. The flops a kernel does per byte fetched is called its [_arithmetic intensity_](https://en.wikipedia.org/wiki/Roofline_model#Arithmetic_intensity). Each stage below tries to raise it.

To make each stage concrete, this post follows one thread through all of them. Thread 0 computes output $C[0][0]$, and the count at each stage is how many values it loads per multiply-add. A naive dot product loads two 4-byte floats, one from $A$ and one from $B$, for every multiply-add of 2 flops. The result is 8 bytes for 2 flops, an arithmetic intensity of 0.25, or about 100× short of 24.

Matrix multiply has plenty of reuse to exploit. Every element of $A$ is needed by $N$ different outputs, and so is every element of $B$. A kernel that fetched each element from global memory exactly once would reach an intensity of $N/4$. At $N = 2048$ the ceiling is 512, far above 24. Real kernels land somewhere in between. Each stage below moves thread 0's count closer to that ceiling.

## Stage 1: Coalescing

Every kernel in this post is written in CUDA, NVIDIA's C++ extension for its own GPUs. The terms below are CUDA's. A CUDA kernel launches thousands of threads. The hardware runs them 32 at a time, in groups called [_warps_](https://docs.nvidia.com/cuda/cuda-programming-guide/01-introduction/programming-model.html#warps-and-simt). The 32 threads of a warp move in lockstep, one instruction at a time. Warps are grouped into _thread blocks_ (_blocks_ for short), and each block is assigned to one SM.

A naive kernel assigns one thread to each output and loops over $k$, the index along the shared dimension $K$ (the columns of $A$ and the rows of $B$). At every step, thread 0 loads $A[0][k]$ and $B[k][0]$ from global memory. What hurts the naive kernel most is how a warp's 32 threads touch memory.

Reads from global memory happen in chunks called transactions. When a warp's 32 threads ask for 32 consecutive floats, the hardware can merge ([_coalesce_](https://docs.nvidia.com/cuda/cuda-c-best-practices-guide/index.html#coalesced-access-to-global-memory)) those requests into a few wide transactions. When the addresses are far apart, every request gets a transaction of its own, and most of each fetched chunk goes unused. The figure below shows one warp doing strided access, then coalesced access:

![Eight threads of one warp load from global memory. Scattered addresses need one transaction each; consecutive addresses share a single wide transaction.](/images/optimizing-cuda-sgemm/coalescing.svg "Eight threads stand in for a warp's 32; the pattern is the same at full width.")

The only thing that picks the pattern is the mapping from a thread's ID to a row and column of $C$. If consecutive threads in a warp get consecutive _rows_, their reads of $A$ land $N$ floats apart. If they get consecutive _columns_, their reads of $B$ and writes to $C$ become contiguous. The arithmetic is the same under either mapping. Yet the coalesced kernel ran roughly an order of magnitude faster than the naive one,[^plots] because each warp's loads now merge into a few wide transactions instead of one per thread.

Thread 0 still loads 2 values from global memory per multiply-add in the coalesced kernel. The change is in how its warp fetches them: a few wide transactions now serve all 32 threads.

## Stage 2: Shared-Memory Tiling

Coalescing fixed how threads read, but each thread in the coalesced kernel still streams an entire row of $A$ and an entire column of $B$ out of global memory. Its neighbors fetch the same values over and over. A GPU has a faster place to keep values that many threads need, and the two kinds of memory compare as follows:

- _Global memory_ (16 GB on the T4) is the off-chip memory from earlier, shared by every thread.
- [_Shared memory_](https://docs.nvidia.com/cuda/cuda-programming-guide/02-basics/writing-cuda-kernels.html#shared-memory) (up to 64 KB per SM) is on-chip and shared by the threads of one block. It works like a scratchpad that the programmer manages by hand.

With shared memory, a block can fetch each value once and let all of its threads use it. The block is responsible for a square tile of $C$. It walks along the shared dimension $K$ in chunks called _$K$ slices_ (_slices_ for short). For each slice, the threads cooperatively load a tile of $A$ and a tile of $B$ into shared memory and wait for each other (`__syncthreads()`). Then every thread computes its partial sums from the on-chip copies.

Each tile is loaded into shared memory once and then read by every thread in the block:

![For each K slice, a block copies one tile of A and one of B from global into shared memory, waits at a barrier, and every thread reads both tiles to update its running sum.](/images/optimizing-cuda-sgemm/smem-tiling.svg "Each grid cell is a whole tile, not a single value.")

With a $T \times T$ tile, each value loaded from global memory is used $T$ times. Arithmetic intensity counts only global-memory traffic, and by that measure it climbs to about $T/4$ flops per byte. A 32×32 tile gives 8, which on paper is 32× the naive kernel's 0.25. On the T4, the measured gain over the coalesced kernel was 20% to 70%, depending on size, with a top speed near 1,000 GFLOP/s.

For thread 0, the 32×32 tile changes the two halves of its count differently. The block's global loads, shared out among its threads, fall from 2 per multiply-add to 1 per 16 multiply-adds. Loads from shared memory stay at 2 per multiply-add, because every multiply-add still reads one value of $A$ and one of $B$.

The unchanged shared-memory loads explain why the speedup was nowhere near 32×: the bottleneck had only moved. Arithmetic intensity ignores reads from shared memory. They still cost instructions and shared-memory bandwidth, and the threads spend most of their time on them instead of on multiplying.

## Stage 3: Register Tiling

The fastest storage on the GPU is the register file. _Registers_ (64K 32-bit registers per SM) are private to each thread and are the only place arithmetic happens. Register tiling hands each thread more outputs, so that one value loaded from shared memory into a register feeds several multiply-adds. It has two parts.

**1-D tiling** gives each thread a short column of outputs instead of a single one. One value loaded from $B$'s tile then serves every output in that column. According to the course report, throughput on the T4 nearly doubled.

**2-D tiling** goes further and gives each thread an 8×8 _patch_ of $C$ as its share of the block's tile. Thread 0's patch covers rows 0–7 and columns 0–7. After a slice lands in shared memory, the thread steps through it with index $k$, the same index the naive kernel looped over along $K$, but now running only across the slice. At each step $k$, the thread loads 8 values from column $k$ of $A$'s tile and 8 values from row $k$ of $B$'s tile into registers. It then multiplies every $A$ value by every $B$ value. This is an [_outer product_](https://en.wikipedia.org/wiki/Outer_product), and it gets 64 multiply-adds from 16 loads.

The figure below shows the outer product that each thread computes:

![One thread holds an 8×8 patch of C in registers. At each step k it loads 8 values of A and 8 of B, then does all 64 multiply-adds between them.](/images/optimizing-cuda-sgemm/register-tiling.svg "The 64 running sums stay in registers for the whole $K$ loop and are written to $C$ once, at the end.")

Thread 0 now loads 16 values from shared memory for 64 multiply-adds, or 1 load per 4 multiply-adds. In the shared-memory stage it needed 2 loads per multiply-add: 8× more than now.

The inner loop of the final T4 kernel looks like this (unrolling pragmas omitted):

```cpp
for (uint dotIdx = 0; dotIdx < TILEDIM_K; ++dotIdx) {
    for (uint i = 0; i < TILESCALE_M; ++i)
        regM[i] = As[threadRow * TILESCALE_M + i][dotIdx];
    for (uint i = 0; i < TILESCALE_N; ++i)
        regN[i] = Bs[dotIdx][threadCol * TILESCALE_N + i];
    for (uint m = 0; m < TILESCALE_M; ++m)
        for (uint n = 0; n < TILESCALE_N; ++n)
            threadResults[m][n] += regM[m] * regN[n];
}
```

The final T4 configuration:

- **Block:** 16×16 = 256 threads.
- **Block tile:** 128×128 outputs of $C$ (16 threads × 8 outputs each way).
- **$K$ slice:** 32, meaning each slice stages a 128×32 tile of $A$ and a 32×128 tile of $B$, 32 KB in total.
- **Per thread:** an 8×8 patch, which is 64 accumulators plus 16 registers for the $A$ and $B$ values.

This configuration is essentially kernel 5 in Boehm's worklog. On the T4 it raised throughput from ~1,000 to 4,780 GFLOP/s.

Getting here was the hardest part of the project, even though the code is short. The first attempt rested on a misreading of what a "block tile" was, and in that version each _thread_ tried to compute a large tile on its own. It was slower than 1-D tiling, because loads to shared memory stopped coalescing and each thread carried more work than it could keep in registers. A conversation with the course TA made clear that a block tile is the output of the whole block, which is then divided among the threads. The rest of the kernel follows the same pattern, with the block tile sized for shared memory and each thread's patch sized for its registers.

Because the block size and the per-thread patch interact, the sweep below tried a few combinations:

{{< table title="Configuration sweep at $N = 2048$ (GFLOP/s)" >}}
| Threads per block | Outputs per thread | Block tile | Throughput |
|---|---|---|---|
| 32×32 | 2×2 | 64×64 | ~2,210 |
| 8×8 | 8×8 | 64×64 | ~2,470 |
| 16×16 | 4×4 | 64×64 | ~3,720 |
| 16×16 | 8×8 | 128×128 | **4,780** |
{{< /table >}}

More outputs per thread help, as long as the registers can hold them. With only 2×2 outputs per thread, a thread spends most of its time loading. At 16×16 threads, moving from 4×4 to 8×8 outputs per thread (a 128×128 block tile) reuses each loaded value enough times to keep the arithmetic units busy.

## Performance Analysis

The final kernel left room between its 4,780 GFLOP/s and the T4's peak, as the table of every stage shows, with cuBLAS for reference:

{{< table title="Throughput by stage (GFLOP/s, NVIDIA T4)" caption="Ranges span the matrix sizes we tested, from $N = 256$ to $N = 2049$." >}}
| Stage | Idea | Throughput |
|---|---|---|
| Naive | One thread per output, everything from global memory | ~35–120 |
| Coalesced | Neighboring threads read neighboring addresses | ~550–860 |
| Shared-memory tiling | A block stages tiles of $A$ and $B$ on-chip | ~650–1,030 |
| 2-D block tiling | Each thread computes an 8×8 patch from registers | up to 4,780 |
| cuBLAS | NVIDIA's library, for reference | ~2,450–4,570 |
{{< /table >}}

The final kernel also slowed down at some matrix sizes. Three limits account for both: instruction overhead inside each SM, the registers each thread needs, and partly filled waves, meaning rounds of blocks that only partly fill the GPU.

### The Roofline

A [_roofline_](https://people.eecs.berkeley.edu/~kubitron/cs252/handouts/papers/RooflineVyNoYellow.pdf) plot (Williams, Waterman, and Patterson) puts arithmetic intensity on the x-axis and throughput on the y-axis. The "roof" has a slope on the left and a flat ceiling on the right. On the slope, where there is little reuse, throughput is capped by memory bandwidth times intensity. Past the _ridge point_ (about 24 flops per byte on the T4), the ceiling is peak compute.

The shared-memory kernel with 32×32 tiles and the final 128×128 kernel sit on the roofline as shown below:

![Schematic roofline: a sloped memory roof meets a flat compute roof at the ridge. The 32×32 shared-memory kernel sits left of the ridge, below the memory roof; bigger tiles move it past the ridge, at about 24 flops per byte.](/images/optimizing-cuda-sgemm/roofline.svg "Both axes are log scale, and the positions are schematic, not measured.")

The final kernel's position follows from thread 0's count. Each $K$ slice brings 128×32 + 32×128 = 8,192 floats into shared memory, and the block does 128 × 128 × 32 = 524,288 multiply-adds with them. Thread 0's block makes 1 global load per 64 multiply-adds, against 1 per 16 with 32×32 tiles.

One 4-byte load per 64 multiply-adds means 128 flops for every 4 bytes, or about 32 flops per byte.[^units] That puts the kernel past the ridge, where memory bandwidth no longer limits it. Its 4,780 GFLOP/s is 62% of peak compute. The missing 38% goes to the SM's other work, such as shared-memory loads, index math, and barriers, not to waiting on global memory.

We suspected that part of that work came from _bank conflicts_. Shared memory is split into 32 banks, and threads in a warp that read the same bank at once take turns. The estimate was that reads from $B$'s tile land about 2 threads on each bank, which makes each read cost about 2 turns instead of 1.

### Occupancy

When a warp stalls on memory, the SM does not wait. It picks another warp that is ready and runs it instead. [_Occupancy_](https://docs.nvidia.com/cuda/cuda-c-best-practices-guide/index.html#occupancy) is the share of an SM's warp slots that hold a warp. A T4 SM has 32 slots. A fuller SM has more warps to choose from during a stall. Two resources decide the kernel's occupancy.

**Registers.** A hand count suggests about 80 registers per thread: 64 accumulators plus 16 for the $A$ and $B$ values. The real count is higher, because the compiler also keeps indices, pointers, and loop counters in registers. The compiler's `ptxas -v` output reports registers per thread; for the T4 it reports **172 per thread**. The exact count varies slightly between CUDA versions. A block of 256 threads at 172 registers each needs about 44,000 registers. Out of the SM's 65,536, that leaves room for only **one block**. A second block would fit only at or below 128 registers per thread. Capping registers at 128 per thread with `__launch_bounds__(256, 2)` should fit a second block, at the cost of some spilling of values out of registers into slower memory.

**Shared memory.** A block stages 32 KB of tiles, and a T4 SM has 64 KB. By shared memory alone, two blocks would fit, but the register limit applies first.

With one 256-thread block per SM, the block's 8 warps take 8 of the SM's 32 warp slots, for **25% occupancy**. Three-quarters of the slots sat empty, and the kernel still hit 62% of peak. Vasily Volkov explains how in his talk [_Better Performance at Lower Occupancy_](https://www.nvidia.com/content/gtc-2010/pdfs/2238_gtc2010.pdf). An SM can hide latency with many warps, or with fewer warps that each carry lots of [independent work](https://en.wikipedia.org/wiki/Instruction-level_parallelism). Thread 0 has 64 independent accumulators. While one of its loads is in flight, the SM can still issue dozens of its multiply-adds.

### Small and Odd Sizes

The largest configuration in the sweep (128×128 tile) did not win at every size. At $N = 256$, it was the _slowest_ of the four, at under 500 GFLOP/s. Every configuration also lost throughput when $N$ was just past a multiple of the tile size, which this post calls an _odd size_:

{{< table title="Best kernel at different sizes (GFLOP/s)" caption="Measured on an NVIDIA T4 with the 16×16 threads, 8×8 per thread configuration." >}}
| $N$ | 512 | 1023 | 1024 | 1025 | 2048 | 2049 |
|---|---|---|---|---|---|---|
| Throughput | 2,167 | 3,830 | 3,920 | ~2,900 | 4,780 | 4,181 |
{{< /table >}}

When $N$ is not a multiple of 128, the tiles at the right and bottom edges hang off the matrix. Each load and store then has to check bounds. Lanes that fall outside the matrix fill in zeros, which splits the threads of one warp across different branches. The divergence has some cost, but it cannot explain why $N = 1025$ is so much slower than $N = 1023$, because both sizes run the same checks.

The one-block-per-SM limit accounts for both slowdowns. The GPU runs blocks in _waves_: a wave puts as many blocks on every SM as will fit at once. Speed at a given size follows from three facts:

1. Each block computes one 128×128 tile of $C$, and an $N \times N$ matrix needs $\lceil N/128 \rceil^2$ blocks.
2. With one block per SM, a wave holds 40 blocks.
3. A wave that is only partly full still takes as long as a full one.

A small matrix has fewer blocks than the T4 has SMs:

- $N = 256$ makes just 4 blocks. Of the 40 SMs, 36 sit idle.
- $N = 512$ makes 16 blocks and leaves 24 SMs idle.

Just past a multiple of 128, one extra row and column of tiles can add a whole wave:

- $N = 1023$ and $N = 1024$ both give 8×8 = 64 blocks: a full wave of 40, then a partial wave of 24. Both sizes take two waves and run at nearly the same speed.
- $N = 1025$ needs 9×9 = 81 blocks: two full waves, then a third wave with a single block. Taking three waves instead of two, $N = 1025$ should run at about two-thirds of $N = 1024$'s speed. That predicts roughly 2,600 GFLOP/s, and the measured speed was about 2,900.
- $N = 2049$ gives 17×17 = 289 blocks, which takes eight waves instead of $N = 2048$'s seven. It should run at 7/8 of $N = 2048$'s 4,780 GFLOP/s. That predicts about 4,180, and the measurement came in at 4,181.

NVIDIA measured the same pattern on the A100, which has 108 SMs. On the A100, throughput rises with $N$ until the tile count hits a multiple of 108. One more tile starts a new wave, and throughput drops sharply:

![Throughput against N for a matrix multiply on an A100: it rises in a sawtooth and drops sharply at each multiple of 108 tiles.](/images/optimizing-cuda-sgemm/nvidia-wave-quantization.svg "Each drop is the first tile of a new wave. Panel (a) of Figure 8 in NVIDIA's [Matrix Multiplication Background User's Guide](https://docs.nvidia.com/deeplearning/performance/dl-performance-matrix-multiplication/index.html#wave-quant).")

The name for that sawtooth is [_wave quantization_](https://docs.nvidia.com/deeplearning/performance/dl-performance-matrix-multiplication/index.html#wave-quant) (or the tail effect), and cuBLAS picks different kernels for different sizes because of it. Wave quantization explains the T4's odd-size drops. It also explains why smaller tiles won at $N = 256$, where a 64×64 tile splits the matrix into 16 blocks instead of 4 and puts more SMs to work. Two blocks per SM, if the registers allowed them, would also halve the number of waves and soften wave quantization.

## Round Two on a GB10

The course kernel ends where Boehm's kernel 5 does. His worklog keeps going, and each later step targets one of the limits above. Almost two years later, I came back to the project and implemented those steps. In this post, _round two_ means the steps in the first table below. Since the T4 was a course machine I no longer have, these measurements come from an **NVIDIA GB10**, the Blackwell chip in NVIDIA's [DGX Spark](https://www.nvidia.com/en-us/products/workstations/dgx-spark/) desktop.

The GB10 differs from the T4 in several ways:

- It has 48 SMs instead of 40.
- Each SM has 100 KB of shared memory instead of 64 KB, and the same 64K registers.
- Its peak arithmetic rate is about 29.5 TFLOP/s, almost 4× the T4's 7.68.[^gb10peak]
- The CPU and GPU share one memory, with 273 GB/s of bandwidth. That bandwidth is about 15% less than the T4's 320.

**No number in this half of the post should be compared with a T4 number from the first half.** Each step is measured against cuBLAS on the same GB10 instead, with [TF32](https://developer.nvidia.com/blog/accelerating-ai-training-with-tf32-tensor-cores/) turned off so both sides do true 32-bit math.

Every step is its own kernel, built on top of the previous one, inside a small standalone benchmark ([`next/`](https://github.com/aaron-ang/cuda-sgemm/tree/main/next) in the repo). Every result is checked against cuBLAS.[^method]

The table below lists the round-two steps. The sections after it follow the T4's limits in turn: registers, then instruction overhead in two sections, then waves, and finally the power limit.

{{< table title="GB10: each step's speed, and its share of cuBLAS" caption="GFLOP/s. $\mathit{BK}$ is the depth of the $K$ slice loaded into shared memory per step." >}}
| Step | $N = 2048$ | vs cuBLAS | $N = 4096$ | vs cuBLAS |
|---|---|---|---|---|
| Our course kernel | 9,661 | 60% | 10,723 | 63% |
| + two blocks per SM | 11,508 | 71% | 12,616 | 74% |
| + `float4` loads, $A$ transposed | 13,882 | 86% | 14,181 | 83% |
| + bank-conflict fix | 14,323 | 88% | 14,623 | 85% |
| + warp tiling | 14,234 | 88% | 14,812 | 87% |
| + warp tiling, $\mathit{BK} = 16$ | 14,687 | 91% | 15,209 | 89% |
| + double buffering ($\mathit{BK} = 16$) | 14,933 | 92% | 15,392 | 90% |
| cuBLAS | 16,204 | 100% | 17,119 | 100% |
{{< /table >}}

Across the round-two steps, the kernel moved from about 60% of cuBLAS to about 90%.

### Two Blocks per SM

On the GB10, the compiler gives the course kernel 168 registers per thread. As on the T4, that count allows only one block per SM, even though shared memory has room for two. [`__launch_bounds__(256, 2)`](https://docs.nvidia.com/cuda/cuda-programming-guide/05-appendices/cpp-language-extensions.html#launch-bounds) promises the compiler that no block has more than 256 threads. It also asks the compiler to fit at least two blocks on an SM at once. Two 256-thread blocks sharing the SM's 65,536 registers get 128 per thread:

![One SM's register file, drawn to scale. At 168 registers per thread, a second 256-thread block doesn't fit; capped at 128, two blocks fit side by side.](/images/optimizing-cuda-sgemm/two-blocks-per-sm.svg "Shared memory had room for two blocks all along; registers were the only limit.")

Getting down to 128 registers forces the compiler to _spill_. Spilling happens when a thread needs more values at once than fit in its registers. The extras go to _local memory_, which is private to each thread yet sits in the same slow off-chip memory as global memory. Every spilled value costs one extra store and one later load. Here the compiler spills 72 bytes per thread (18 floats).

Each SM now runs two blocks, as the T4 analysis predicted. Throughput at $N = 2048$ rose by 19%. The spills add extra loads and stores, but the second block hides more latency than they cost. The cap has two other costs. The compiler has fewer spare registers for starting loads early, and a block with more than 256 threads can no longer launch.

### Wider Loads and Bank Conflicts

The T4 roofline put the remaining time into the SM's own work, such as shared-memory loads, rather than into waiting on global memory. Four steps on the GB10 targeted load instructions: wider loads, a bank-conflict fix, warp tiling, and a thinner $K$ slice.

**`float4` loads with $A$ transposed: +21%.** A [vectorized load](https://developer.nvidia.com/blog/cuda-pro-tip-increase-performance-with-vectorized-memory-access/) such as `float4` moves four floats in one instruction. With every global load now fetching four floats instead of one, a thread issues 4× fewer load instructions. All of this step's gain at $N = 2048$ came from the wider loads.

The same step also adopts Boehm's trick of storing $A$'s tile transposed in shared memory, which turns its rows into columns. Without the transpose, the 8 values of $A$ that thread 0 needs for one step lie in one column, 32 floats apart. After it, they sit side by side in a row, and the expectation was that the thread would read them with 2 wide reads instead of 8 narrow ones. The figure below traces one row of $A$'s tile through both changes:

![One row of A's tile takes 32 loads one float at a time, and 8 with float4, four floats per load. In shared memory, row 0's values run down column 0 of the transposed tile, t0's four first, then t1's.](/images/optimizing-cuda-sgemm/float4-transpose.svg "Only row 0 of $A$'s tile is traced in global memory; the other rows load the same way.")

Transposing the tile did not reduce the number of reads from shared memory. In the original layout, one row's values for four consecutive steps already sit side by side, and the compiler was already reading them with one wide read. In the compiled code, each thread issues 128 wide reads from shared memory per $K$ slice in both versions. At $N = 2048$ the profiler counted 16.8 million instructions that read shared memory, the same for each version. The transpose stays anyway, because a later kernel with a bigger patch per thread runs 2–4% slower without it.

**Bank-conflict fix: +3%.** This step tested the [bank conflicts](https://docs.nvidia.com/cuda/cuda-programming-guide/02-basics/writing-cuda-kernels.html#shared-memory-bank-conflicts) predicted after the T4 runs. Of the 32 banks, bank 0 holds columns 0, 32, 64, … of $B$'s tile, bank 1 holds columns 1, 33, 65, …, and so on. Threads in a warp that read the same bank at once take turns, one _pass_ per thread. The usual fixes are padding the arrays or changing which columns each thread reads.

Originally each thread read 8 neighboring columns of $B$, 4 columns per read. Thread 0 had columns 0–7, thread 1 had 8–15, and so on. Thread 4's first read therefore started at column 32, which sits in the same banks as thread 0's.

The fix changes which columns each thread reads. It breaks each thread's 8 columns into two groups of 4, placed 64 columns apart. Thread 0 now reads 0–3 and 64–67, and thread 1 reads 4–7 and 68–71. Across 8 neighboring threads, the first groups cover columns 0–31 with no overlap, and each bank serves exactly one thread. The second groups begin at column 64, after the first groups of all 16 threads across the tile. They are separate reads and never compete with the first ones. $A$'s rows get the same split. The figure below maps both layouts onto the banks:

![A map of 32 shared-memory banks while 8 threads each read 16 bytes of B's tile. With 8 contiguous columns per thread, neighboring threads start 8 columns apart and pairs share banks; with each thread's columns split into two groups of 4, neighbors start 4 columns apart and each bank serves one thread.](/images/optimizing-cuda-sgemm/bank-conflicts.svg "Hardware serves a 16-byte read 8 threads at a time; these 8 stand for every group in the warp.")

In the profiler, the bank-conflict counter for shared-memory reads fell from 33.6 million to 54 thousand. The average number of passes per read fell from 5 to 3. That average covers every shared-memory read in the kernel, not just these reads of $B$; it therefore does not fall to 1.

**Warp tiling: no gain on its own.** [Warp tiling](https://github.com/NVIDIA/cutlass/blob/main/media/docs/cpp/efficient_gemm.md) changes which outputs belong to each warp. A warp used to own 16 full-width rows of the block tile. With warp tiling it owns a compact 32×64 region, which needs 96 values from shared memory per step instead of 144:

![The outputs warp 0 owns in the 128×128 block tile: before, 16 full-width rows; with warp tiling, a 32×64 region. Shaded strips show what it reads from A and B per step: a third less data, but the same number of passes.](/images/optimizing-cuda-sgemm/warp-tiling.svg "Only warp 0 is traced; the other seven warps tile the rest of the block the same way.")

Speed stayed flat because the removed reads had been cheap all along. Threads of a warp that read the same value get it in one pass, leaving most of those 144 values free of extra cost. The profiler counted about 50 million passes through shared memory in both versions. Global-memory traffic stayed the same too: a block still builds a 128×128 tile of $C$ from the same slices of $A$ and $B$, and arithmetic intensity against global memory does not move.

**Smaller $K$ slice: +3%.** Cutting the $K$ slice from 32 values deep to 16 added another 3%. The profiler points to a cause. Writing $A$'s tile transposed into shared memory causes heavy bank conflicts, and a slice half as deep halves the passes per store.

The mechanism lies in how the transposed store spreads across banks. Each thread in a transposed store writes one float, and the bank it hits depends only on the row of $A$'s tile that the thread copies. With a 32-deep $K$ slice, each row is copied by 8 threads of the warp. Those 8 threads land on one bank, making the store take 8 passes. With a 16-deep slice only 4 threads share a row, and the profiler counted 4 passes per store, against 1 for a store without bank conflicts.

### Closing the Gap to cuBLAS

The rest of the instruction overhead fell to a bigger patch per thread, the register-tiling idea from the T4 taken one step further. The bigger patch closed the gap to cuBLAS. Before it, two steps that overlapped loading with math gained little or nothing. The explanations in this section rest on small probe kernels, instruction counts in the compiled code, cuBLAS's own logs, and the profiler.

**Double buffering: +2%.** With [double buffering](https://en.wikipedia.org/wiki/Multiple_buffering#Double_buffering_for_DMA), a block loads the next $K$ slice while it computes on the current one, which hides the wait at `__syncthreads()`. It helped less than expected. Most of those loads were probably hidden already by the two blocks per SM, because one block computes while the other waits. In the figure, a second buffer and a second block hide the same loads:

![Timelines of loads and compute. With one buffer they alternate; with two, the next slice loads during the current compute. Two blocks with one buffer each take turns computing, hiding the same loads while doing twice the work.](/images/optimizing-cuda-sgemm/double-buffering.svg "The second buffer doubles the block's shared memory, which is the price of overlapping its own loads.")

**Asynchronous copies: no gain.** The expected fix for the last 10% was [asynchronous copies](https://docs.nvidia.com/cuda/cuda-programming-guide/index.html), a hardware feature that the GB10 has and the T4 does not. NVIDIA added the `cp.async` instruction in its Ampere generation, which came between the T4's Turing and the GB10's Blackwell. `cp.async` moves data from global to shared memory without passing it through registers. A block can use it to queue up slices ahead of time, each in its own buffer called a _stage_.

The table below starts from the double-buffered kernel and adds the two remaining steps:

{{< table title="GB10, closing the gap: each step's speed, and its share of cuBLAS" caption="GFLOP/s, from the same runs as round two." >}}
| Step | $N = 2048$ | vs cuBLAS | $N = 4096$ | vs cuBLAS |
|---|---|---|---|---|
| Double buffering (from round two) | 14,933 | 92% | 15,392 | 90% |
| + asynchronous copies | 14,490 | 89% | 15,384 | 90% |
| + 16×8 outputs per thread, 128×256 tile | 16,625 | 103% | 17,839 | 104% |
| cuBLAS | 16,204 | 100% | 17,119 | 100% |
{{< /table >}}

Two stage counts were tested. Two stages of depth 16 ran exactly as fast as plain double buffering. Four stages of depth 8, in the same amount of shared memory, were about 5% slower.

Small probe kernels measured where the time went. At $N = 4096$ the double-buffered kernel did about 15.7 TFLOP/s. Removing its global loads took it to 20.2, almost 30% faster, which shows that loading did cost something. Adding back just the stores into shared memory dropped it to 18.7.

A probe that re-read one slice every step, and never waited on global memory, also ran at 15.7, exactly as fast as the real kernel. The profiler agreed: only 5% of the double-buffered kernel's stall samples were warps waiting on global memory, against 18% waiting on shared memory and at barriers. The time went to the instructions that move each tile rather than to waiting on memory. `cp.async` removes few of those instructions here, because $A$'s transposed tile still has to be copied one 4-byte float per instruction.

**A bigger patch per thread: the gap closes.** According to cuBLAS's own logs, at $N = 4096$ it runs a [CUTLASS](https://github.com/NVIDIA/cutlass) kernel whose threads each compute 128 outputs, twice the 64 of an 8×8 patch. Following that lead, the next kernel grows each thread's patch of $C$ from 8×8 to 16×8. With the block still at 256 threads, its tile grows from 128×128 to 128×256, still loaded with `cp.async` in two stages.

With a 16×8 patch, every loaded value gets reused more. At each step, thread 0 now loads 24 values from shared memory and does 128 multiply-adds with them, against 16 values and 64 multiply-adds before. The result is twice the math for 50% more loads, or 3 loads per 16 multiply-adds instead of 4. The profiler showed the math units busy 70% of the time, up from 58%.

The 128 running sums need 237 registers per thread, a count that limits the SM to one block again. The single block did not hurt, for the same reason the T4 kernel hit 62% of peak at 25% occupancy. Each thread has enough independent multiply-adds to keep issuing while its loads are in flight: 128 now, 64 on the T4.

The 128×256 kernel ran 3–4% faster than cuBLAS at both $N = 2048$ and $N = 4096$, a margin within the run-to-run spread. The figure below shows one thread's work per step, before and after:

![An 8×8 patch takes 16 loads for 64 multiply-adds per step; a 16×8 patch takes 24 loads for 128. The larger patch's registers leave room for one block per SM instead of two.](/images/optimizing-cuda-sgemm/bigger-thread-tile.svg "One thread's view is shown; all 256 threads in the block do the same.")

### A Tile per Size

The wave quantization that slowed odd sizes on the T4 also appears on the GB10 and its 48 SMs. Two blocks per SM did halve the number of waves, as the T4 analysis predicted, and odd sizes seemed likely to gain the most from it. At $N = 1025$ the gain was zero. There are 81 blocks for 48 SMs, and some SMs have to run two blocks no matter what. At one block per SM they run them back to back. At two per SM they run both together at about half speed each, and they do not finish any sooner.

cuBLAS has its own odd-size problem: it loses about 30% of its speed between $N = 1024$ and $N = 1025$. At odd sizes such as $N = 1025$ and $N = 2049$, the double-buffered kernel actually ran about 2% **faster** than cuBLAS.[^odd]

**Picking a tile per size.** Some sizes are a bad fit for a 128×256 tile. At $N = 2049$ the last column of tiles covers just one column of $C$. $N = 1024$ makes only 32 tiles for 48 SMs, and a third of the GPU idles. The final kernel instead chooses between three versions for each size:

- the big 128×256 tile
- a 128×128 tile, whose 128 threads each still compute 16×8 outputs
- a [split-K](https://github.com/NVIDIA/cutlass/blob/main/media/docs/cpp/efficient_gemm.md) version, which splits the $K$ dimension three ways so more SMs have work when there are few tiles.

The 128×128 version leaves $A$'s tile untransposed. It can then copy $A$ 16 bytes at a time instead of 4, and it avoids the bank conflicts of transposed stores. A 256×128 tile was also tested. With twice as many rows of $A$ to copy, it was 6–10% slower than 128×256.

The launch code runs on the CPU. Before every launch it picks whichever version leaves the busiest SM with the least work. With that choice the kernel reached 115% of cuBLAS at $N = 1025$ and 114% at $N = 2049$. At $N = 1024$ the big tile alone managed 86%, and the kernel now matches cuBLAS there.

**Splitting the last wave: stream-K.** At some sizes the last wave of tiles only partly fills the GPU, and most SMs would sit idle through it. A scheme called [stream-K](https://arxiv.org/abs/2301.03598) avoids that. The kernel cuts the last wave's tiles along $K$ and hands the pieces to all 48 SMs. Only the last wave's tiles get split, which is the difference from the split-K version above, where every tile is split. Whichever SM finishes a tile's last piece adds up the other pieces' sums and writes $C$.

Compared with the 128×256 tile alone, stream-K sped up $N = 2049$ by about 11%. The 4% gain at $N = 2048$ is close to the run-to-run spread. The figure below shows the last wave on the SMs, handed out whole and then split:

![Fourteen tiles on six SMs, drawn as lanes over time. Handed out whole, the last two tiles leave four SMs idle; split into K pieces, they spread across all six.](/images/optimizing-cuda-sgemm/last-wave.svg "Six SMs stand in for the GB10's 48; each lane is one SM over time. The kernel actually runs the split tiles first and the whole tiles after them, which takes the same total work.")

The first two versions of stream-K were slower. In the first, each SM got one long, unbroken run of $K$ steps that crossed tile boundaries. The SMs drifted far enough apart that they stopped sharing data in the L2 cache, and each tile got about 40% slower. The second version allowed up to 16 pieces per tile, and $N = 1025$ fell to 43% of cuBLAS.

The working version cuts each tile into 2 to 4 pieces. For each possible split, the launch code estimates how much work the busiest SM would get, adding a little for summing the pieces. It then launches the kernel with the split that gives the least.

### The Power Limit

By the end of round two, power was holding both kernels back as much as their instructions were. A GPU has a power budget. If a workload draws more than the budget allows, the GPU drops its clock to stay inside it. The GB10 kept its full 2.4 GHz clock on a plain multiply-add loop. SGEMM draws more power because it also pushes data through shared memory and registers.

The driver's report shows the effect. Over a 50-second SGEMM run, the chip drew about 95 W and stayed at its power limit the whole time. Its clock slid from 2.2 to 2.1 GHz. At 2.1 GHz the chip's peak is about 25.8 TFLOP/s. cuBLAS's 17,119 GFLOP/s at $N = 4096$ is about two-thirds of that.

Power throttling during matrix multiply is a known effect. Horace He [showed](https://www.thonking.ai/p/strangely-matrix-multiplications) that an H100 multiplies matrices of zeros faster than random ones. With zeros, fewer bits flip, which draws less power and keeps the clock higher. The GB10 kernels in this post did the same, running about 10% faster on all-zero matrices.

The power limit also made repeated sets of runs of the same kernel differ by up to about 5%, more than the 3–4% lead of the 128×256 kernel over cuBLAS. Even so, that kernel kept its lead. Each size was timed twice in that session, and both times the 128×256 kernel came out 3–4% ahead.

Profiler runs also compared the two kernels' code with the power limit out of the picture. Under the profiler, each kernel runs too briefly for the chip to hit its power limit and slow its clock. At full clock the big-tile kernel beat cuBLAS's kernel by about 4%. It kept the math units busy 70% of the time, against 62% for cuBLAS.

The profiler can count shared-memory passes in any kernel, closed-source cuBLAS included. On the same loads, cuBLAS needed about 3 passes for every 4 of the big-tile kernel's. My guess was that the difference came from the mapping of threads to outputs. Within each half-warp, the big-tile kernel's threads cover 2 rows of 8 outputs. By my count, a 4×4 square should have cut the 4 passes to 3. In a test of that layout, the profiler showed about 5, with no change in speed. My model of how passes get counted was wrong. I still do not know how cuBLAS saves those passes, or whether the savings matter under the power limit.

## Looking Back

Every gain on the T4 came from moving data less, not from adding threads. The naive kernel already had one thread per output. On the T4, coalescing lined up a warp's accesses, shared memory shared loads across a block, and register tiling reused values inside a thread. Thread 0 started with 2 loads from global memory per multiply-add. In the final T4 kernel, its global loads fell from 2 per multiply-add to 1 per 64 (its share of the block's loads), and its shared loads to 1 per 4. On the GB10, the step that matched cuBLAS applied the same idea once more, with a bigger patch per thread.

The other lesson was to test each explanation against the hardware's hard limits. Registers per thread, shared memory per SM, and SM count explained the results better than any intuition about "memory-bound" or "compute-bound". The 172 registers from `ptxas -v`, together with the wave counts, explained results that guessing had not.

[Tensor cores](https://www.nvidia.com/en-us/data-center/tensor-cores/) would be far faster than any of these kernels, but they round the inputs to lower precision, which makes them a solution to a different problem.

[^units]: Intensity here is measured per byte, not per element loaded, since the ridge point is defined in bytes. The figure of 32 flops per byte assumes each value is read from global memory once per tile, and ignores caches.

[^plots]: The repository only has code for the naive and final versions. For the stages in between, this post describes the idea and quotes approximate numbers from the course report.

[^method]: Each figure is the median of 3 sets of 7 timed runs, all measured in one session on an otherwise idle machine. Sets of the same kernel differed by up to about 5%, because the chip hits its power limit, as described below.

[^gb10peak]: The peak comes from 48 SMs × 128 cores × 2 flops per cycle at the 2.4 GHz clock. A plain multiply-add loop reached 28.9 TFLOP/s. A simple read kernel reached about 240 GB/s of the 273 GB/s bandwidth.

[^odd]: At $N = 1025$ the double-buffered kernel ran at 11,119 GFLOP/s against cuBLAS's 10,851, and at $N = 2049$ at 12,825 against 12,578. cuBLAS ran at 15,575 at $N = 1024$. The benchmark pads every row to a multiple of 4 floats, which keeps the wide loads legal at odd sizes, and cuBLAS ran on the same padded layout.
