+++
title = "Optimizing Matrix Multiply on CUDA, Part 1"
date = "2024-11-18"
slug = "optimizing-cuda-sgemm"
tags = ["Engineering", "Research"]
+++

A _kernel_ is a function that runs on the GPU. An SGEMM kernel is single-precision [general matrix multiply](https://en.wikipedia.org/wiki/Basic_Linear_Algebra_Subprograms#Level_3), using 32-bit floats. As part of a graduate course at UCSD, Jerry Ma and I wrote an SGEMM kernel to get as close as possible to NVIDIA's [cuBLAS](https://developer.nvidia.com/cublas). Our first correct kernel ran at around 100 GFLOP/s (billion floating-point operations per second). The last hit **4,780 GFLOP/s** at $N = 2048$, roughly matching cuBLAS on the same T4.

Neural network operations such as attention, fully connected layers, and convolutions all use matrix multiply, so there is a lot of value in studying and optimizing this component. An [NVIDIA T4](https://www.nvidia.com/en-us/data-center/tesla-t4/) can do about 24 floating-point operations in the time it takes to fetch one byte. Meanwhile, the textbook algorithm boils down to just 0.25 operations per byte. How do we make up that difference?

The naive kernel has a thread for every output and still runs at around 100 GFLOP/s. There are plenty of threads, but each fetches its own inputs repeatedly. Our implementation will try to have them share that work. The kernels are written in [CUDA](https://docs.nvidia.com/cuda/cuda-programming-guide/index.html), with the code on [GitHub](https://github.com/aaron-ang/cuda-sgemm).

We borrowed heavily from Simon Boehm's [_worklog_](https://siboehm.com/articles/22/CUDA-MMM), which is well worth reading. We'll follow the same optimizations here, with more background and our own measurements on the T4 GPU.

## Counting the Work

Take $C[i][j]$, the dot product of row $i$ of $A$ and column $j$ of $B$. For $N \times N$ inputs, we multiply and add $N$ pairs. Counting a multiply-add as two floating-point operations (_flops_), that's $2N$ flops for this output and $2N^3$ for all $N^2$ outputs. At $N = 2048$, the kernel has about 17 billion flops to do.

Now move one column over to $C[i][j+1]$. The $B$ values change, but we need the same row of $A$ again:

![One entry of C forms as the dot product of a row of A and a column of B, one multiply-add at a time. The next entry reads the same row of A again.](/images/optimizing-cuda-sgemm/matmul-dot.svg "No kernel can skip any of the $N$ multiply-adds per output. To make a kernel faster, we need to fetch each value from main memory fewer times.")

Before changing the kernel, let's formalize T4's compute and memory limits:

- **Compute:** 40 [streaming multiprocessors](https://docs.nvidia.com/cuda/cuda-programming-guide/01-introduction/programming-model.html#gpu-hardware-model) (SMs) × 64 floating-point units × ~1.5 GHz × 2 flops per [fused multiply-add](https://en.wikipedia.org/wiki/Multiply%E2%80%93accumulate_operation#Fused_multiply%E2%80%93add) ≈ **7,680 GFLOP/s**.
- **Memory:** about **320 GB/s**. The main memory is off-chip GDDR6, and CUDA calls it _global memory_.

For the arithmetic units to run at full capacity, every byte fetched has to support $7{,}680 / 320 = 24$ flops. Thread 0, which computes $C[0][0]$ in our naive kernel, loads one 4-byte float from $A$ and another from $B$ for each multiply-add. This amounts to 8 bytes fetched for 2 flops, or 0.25 flops per byte, almost 100× short of the peak.

This flops-per-byte ratio is known as [_arithmetic intensity_](https://en.wikipedia.org/wiki/Roofline_model#Arithmetic_intensity). As we continue to improve the kernel, we will re-evaluate thread 0's loads, including its share of any loads that threads perform together.

At the other extreme, suppose we fetched each element of $A$ and $B$ just once. We read $2N^2$ floats, or $8N^2$ bytes, for our $2N^3$ flops: $N/4$ flops per byte. At $N = 2048$ that is 512. We won't get there, but it gives us room to improve on 0.25.

## Stage 1: Coalescing

Every kernel in this post is written in CUDA, NVIDIA's C++ extension for its own GPUs. The terms below are CUDA's. A CUDA kernel launches thousands of threads. The hardware runs them 32 at a time, in groups called [_warps_](https://docs.nvidia.com/cuda/cuda-programming-guide/01-introduction/programming-model.html#warps-and-simt). The 32 threads of a warp move in lockstep, one instruction at a time. Warps are grouped into _thread blocks_ (_blocks_ for short), and each block is assigned to one SM.

A naive kernel assigns one thread to each output and loops over $k$, the index along the shared dimension $K$ (the columns of $A$ and the rows of $B$). At every step, thread 0 loads $A[0][k]$ and $B[k][0]$ from global memory. Thread 0's load is only one of the warp's 32 requests. The GPU fetches memory in chunks, or _transactions_, so we also need to know where the other threads are reading.

If threads in a warp ask for 32 consecutive floats, the GPU can merge ([_coalesce_](https://docs.nvidia.com/cuda/cuda-c-best-practices-guide/index.html#coalesced-access-to-global-memory)) the requests into a few transactions. If the addresses are far enough apart, the memory controller has to fetch a separate chunk for each request. Each request transfers an entire chunk of memory, although only one float from that chunk is needed. The figure makes that wasted traffic visible:

![Eight threads of one warp load from global memory. Scattered addresses need one transaction each; consecutive addresses share a single wide transaction.](/images/optimizing-cuda-sgemm/coalescing.svg "Eight threads stand in for a warp's 32; the pattern is the same at full width.")

Our thread-to-output assignment decides which of those patterns we get. When neighboring threads compute neighboring _rows_ of $C$, their $A$ reads are $N$ floats apart. Assigning them to neighboring _columns_ instead makes their $B$ reads and $C$ writes contiguous, allowing the warp's requests to share transactions. This change gave us roughly an order of magnitude more throughput,[^plots] even though thread 0 still requested the same 2 global-memory values per multiply-add.

## Stage 2: Shared-Memory Tiling

Thread 0 reads all of row 0 of $A$, but the threads computing $C[0][1]$ and $C[0][2]$ need that same row too. Keeping a copy near those threads would let them reuse it rather than fetch it again. For this, we can compare global memory with CUDA's shared memory:

- _Global memory_ (16 GB on the T4) is the off-chip memory from earlier, shared by every thread.
- [_Shared memory_](https://docs.nvidia.com/cuda/cuda-programming-guide/02-basics/writing-cuda-kernels.html#shared-memory) (up to 64 KB per SM) is on-chip and shared by the threads of one block. It works like a scratchpad that the programmer manages by hand.

We can't keep all of $A$ and $B$ in that small scratchpad. Instead, we assign a square tile of $C$ to the block and walk through $K$ a chunk at a time. Each chunk is a _$K$ slice_ (_slice_ for short).

The threads first divide up the loads for two input tiles, one from $A$ and one from $B$. After `__syncthreads()`, they can all read the shared copies to update their partial sums. Reuse that storage for the next slice and repeat until the output tile is finished:

![For each K slice, a block copies one tile of A and one of B from global into shared memory, waits at a barrier, and every thread reads both tiles to update its running sum.](/images/optimizing-cuda-sgemm/smem-tiling.svg "Each grid cell is a whole tile, not a single value.")

A $T \times T$ tile lets each fetched value serve $T$ outputs, for roughly $T/4$ flops per byte. With 32×32 tiles, arithmetic intensity rises from 0.25 to 8 flops per byte—a 32× increase. However, measured throughput gain was only 20%–70%, topping out near 1,000 GFLOP/s. Where did the rest of that improvement go?

Let's observe the two kinds of loads for thread 0. Its share of the block's global loads is now 1 per 16 multiply-adds, down from 2 per multiply-add. Shared-memory loads are still 2 per multiply-add: one input from $A$, one from $B$.

The 32× improvement only accounts for the global-memory traffic we've saved. Within the SM, every multiply-add still needs two shared-memory reads, which consume instructions and bandwidth. To reduce that work, we can keep the values read from shared memory in registers and use them to compute more than one output.

## Stage 3: Register Tiling

The fastest storage on the GPU is the register file, with 64K 32-bit _registers_ per SM. Registers are private to each thread and are where arithmetic happens. By giving a thread more outputs to compute, register tiling lets it use one value loaded from shared memory for several multiply-adds. We approached this in two steps.

**1-D tiling** gives each thread a short column of outputs instead of a single one. One value loaded from $B$'s tile then serves every output in that column. According to the course report, throughput on the T4 nearly doubled.

For **2-D tiling**, let's give thread 0 rows 0–7 and columns 0–7 of $C$: an 8×8 _patch_ within the block's tile. It still loops over $k$, but only across the slice currently in shared memory. At each step $k$, the thread loads 8 values from column $k$ of $A$'s tile and 8 values from row $k$ of $B$'s tile into registers. It then multiplies every $A$ value by every $B$ value. This is an [_outer product_](https://en.wikipedia.org/wiki/Outer_product), and it gets 64 multiply-adds from 16 loads.

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

This configuration is essentially kernel 5 in Boehm's worklog. On the T4, it raised throughput from ~1,000 to 4,780 GFLOP/s.

Although the loop structure is short, we spent the majority of our time on this piece. Initially we misread "block tile" as the tile belonging to a thread, and made each _thread_ compute a large tile on its own. That version became slower than 1-D tiling: the shared-memory loads no longer coalesced, and the thread's working set was too large for its registers.

After consulting with the course TA, we distinguished the block's output tile from each thread's smaller patch. The inputs for the block's tile are staged in shared memory, while each thread keeps the running sums for its patch in registers. The thread grid and each thread’s patch size together determine the block’s output tile size, so we tried the combinations below:

{{< table title="Configuration sweep at $N = 2048$ (GFLOP/s)" >}}
| Threads per block | Outputs per thread | Block tile | Throughput |
|:---:|:---:|:---:|:---:|
| 32×32 | 2×2 | 64×64 | ~2,210 |
| 8×8 | 8×8 | 64×64 | ~2,470 |
| 16×16 | 4×4 | 64×64 | ~3,720 |
| 16×16 | 8×8 | 128×128 | **4,780** |
{{< /table >}}

A 2×2 patch doesn't provide much reuse, so most of the time is still spent on loads. With 16×16 threads, doubling each side of the patch from 4 to 8 raises throughput from ~3,720 to 4,780 GFLOP/s, but it also requires four times as many running sums per thread. Whether those sums fit in registers becomes important when we consider occupancy below.

## Performance Analysis

We reached 4,780 GFLOP/s, a lot better than the first kernel but still below the T4's peak. Here's the stage-by-stage comparison with cuBLAS:

{{< table title="Throughput by stage (GFLOP/s, NVIDIA T4)" caption="Ranges span the matrix sizes we tested, from $N = 256$ to $N = 2049$." >}}
| Stage | Idea | Throughput |
|---|---|:---:|
| Naive | One thread per output, everything from global memory | ~35–120 |
| Coalesced | Neighboring threads read neighboring addresses | ~550–860 |
| Shared-memory tiling | A block stages tiles of $A$ and $B$ on-chip | ~650–1,030 |
| 2-D block tiling | Each thread computes an 8×8 patch from registers | up to 4,780 |
| cuBLAS | NVIDIA's library, for reference | ~2,450–4,570 |
{{< /table >}}

The peak timing hides some awkward cases. Our largest tile is actually the slowest at small $N$, and adding one row and column can cause a large throughput drop. Before looking at those cases, let's check how close this kernel is to the hardware's limits.

### The Roofline

We counted flops per byte and measured flops per second. Williams, Waterman, and Patterson's [_roofline_](https://people.eecs.berkeley.edu/~kubitron/cs252/handouts/papers/RooflineVyNoYellow.pdf) puts those on the x- and y-axes of one plot. At low intensity, multiplying bandwidth by intensity gives the throughput ceiling. As intensity increases, that sloped ceiling reaches peak compute and becomes flat. The meeting point, or _ridge_, is about 24 flops per byte for our T4.

The shared-memory kernel with 32×32 tiles and the final 128×128 kernel sit on the roofline as shown below:

![Schematic roofline: a sloped memory roof meets a flat compute roof at the ridge. The 32×32 shared-memory kernel sits left of the ridge, below the memory roof; bigger tiles move it past the ridge, at about 24 flops per byte.](/images/optimizing-cuda-sgemm/roofline.svg "Both axes are log scale, and the positions are schematic, not measured.")

The final kernel's position follows from thread 0's count. Each $K$ slice brings 128×32 + 32×128 = 8,192 floats into shared memory, and the block does 128 × 128 × 32 = 524,288 multiply-adds with them. Thread 0's block makes 1 global load per 64 multiply-adds, against 1 per 16 with 32×32 tiles.

That gives 128 flops / 4 bytes = 32 flops per byte,[^units] placing the kernel to the right of the ridge even though it reaches only 62% of peak compute. The roofline suggests that further global-memory reuse would offer less than reducing work within the SM, where shared-memory loads, indexing, and barriers could account for the remaining gap.

One possible cost was _bank conflicts_ in the shared-memory reads. Shared memory has 32 banks, and when threads in a warp request different values from the same bank, those reads have to take separate turns. We estimated roughly 2 threads per bank when reading $B$'s tile, requiring about 2 turns instead of 1. This gave us a reason to investigate the access pattern further, though we didn't measure a bank-conflict fix on the T4.

### Occupancy

An SM can run another ready warp while one waits on memory. How many does our kernel give it to choose from? A T4 SM has 32 warp slots, and the fraction occupied by resident warps is [_occupancy_](https://docs.nvidia.com/cuda/cuda-c-best-practices-guide/index.html#occupancy). To find ours, we need to count registers and shared memory.

**Registers.** On paper, a thread needs about 80 registers for its 64 accumulators and 16 values from $A$ and $B$. The compiler's `ptxas -v` output reports **172 per thread**, since pointers, indices, and loop counters also need storage. The exact count varies slightly between CUDA versions, but we need to budget for the compiled kernel rather than just the variables counted by hand. At 172 registers each, a block of 256 threads needs about 44,000 of the SM's 65,536 registers, leaving room for only **one block**. Two blocks would require at most 128 registers per thread.

**Shared memory.** Each block needs 32 KB for its tiles, so the SM's 64 KB could hold two blocks if the register budget allowed it.

The resident block has 8 warps in an SM with 32 warp slots, giving us **25% occupancy**. Yet we're reaching 62% of peak compute with three-quarters of those slots empty. Vasily Volkov's [_Better Performance at Lower Occupancy_](https://www.nvidia.com/content/gtc-2010/pdfs/2238_gtc2010.pdf) explains another way to hide latency: fewer warps, each with lots of [independent work](https://en.wikipedia.org/wiki/Instruction-level_parallelism). Thread 0 has 64 independent accumulators, so dozens of multiply-adds can issue while a load is in flight.

### Small and Odd Sizes

The largest configuration in the sweep (128×128 tile) did not win at every size. At $N = 256$, it was the _slowest_ of the four, at under 500 GFLOP/s. Every configuration also lost throughput when $N$ was just past a multiple of the tile size, which this post calls an _odd size_:

{{< table title="Best kernel at different sizes (GFLOP/s)" caption="Measured on an NVIDIA T4 with the 16×16 threads, 8×8 per thread configuration." >}}
| $N$ | Tiles of 128 per side | GFLOP/s |
|:---:|:---:|:---:|
| 512 | exactly 4 | 2,167 |
| 1023 | just under 8 | 3,830 |
| 1024 | exactly 8 | 3,920 |
| 1025 | just over 8 | ~2,900 |
| 2048 | exactly 16 | 4,780 |
| 2049 | just over 16 | 4,181 |
{{< /table >}}

At the edges of a matrix that isn't a multiple of 128, tiles hang off the right and bottom sides. We need bounds checks, with lanes outside the matrix supplying zeros. That splits a warp across branches and incurs additional compute. But $N = 1023$ does those checks too; divergence doesn't explain why $N = 1025$ is so much slower.

Let's count blocks instead. The GPU runs them in _waves_, filling each SM with as many blocks as will fit. We need $\lceil N/128 \rceil^2$ blocks, one per 128×128 output tile. Only one block fits on each SM, so our 40 SMs can finish 40 blocks in a wave. If the last wave has fewer blocks, the busy SMs still have to finish theirs while the rest sit idle.

This is especially bad at small sizes:

- $N = 256$ makes just 4 blocks. Of the 40 SMs, 36 sit idle.
- $N = 512$ makes 16 blocks and leaves 24 SMs idle.

Now compare the sizes around a tile boundary. How many waves does each need?

- $N = 1023$ and $N = 1024$ both give 8×8 = 64 blocks: a full wave of 40, then a partial wave of 24. Both sizes take two waves and run at nearly the same speed.
- $N = 1025$ needs 9×9 = 81 blocks: two full waves, then a third wave with a single block. Taking three waves instead of two, $N = 1025$ should run at about two-thirds of $N = 1024$'s speed. That predicts roughly 2,600 GFLOP/s, and the measured speed was about 2,900.
- $N = 2049$ gives 17×17 = 289 blocks, which takes eight waves instead of seven when $N = 2048$. It should run at 7/8 of $N = 2048$'s 4,780 GFLOP/s. That predicts about 4,180, and the measurement came in at 4,181.

The A100's 108 SMs show this too. NVIDIA's plot rises as the tiles fill a wave, then drops when one more tile starts the next:

![Throughput against N for a matrix multiply on an A100: it rises in a sawtooth and drops sharply at each multiple of 108 tiles.](/images/optimizing-cuda-sgemm/nvidia-wave-quantization.svg "Each drop is the first tile of a new wave. Panel (a) of Figure 8 in NVIDIA's [Matrix Multiplication Background User's Guide](https://docs.nvidia.com/deeplearning/performance/dl-performance-matrix-multiplication/index.html#wave-quant).")

This phenomenon is known as [_wave quantization_](https://docs.nvidia.com/deeplearning/performance/dl-performance-matrix-multiplication/index.html#wave-quant), or the tail effect. cuBLAS selects different kernels at different sizes to deal with it. Our 128×128 tile wins at $N = 2048$, but using 64×64 at $N = 256$ would put 16 SMs to work instead of 4. Fitting two blocks per SM would also double a wave's capacity and reduce the tail cost, provided we could bring the register allocation down to 128 per thread.

## Looking Back

We ended the course project at 4,780 GFLOP/s, with thread 0's global loads down from 2 per multiply-add to 1 per 64, and shared-memory loads down to 1 per 4. The kernel still computes the same dot products we started with.

There are still several optimizations left to try: reducing the register allocation to at most 128 per thread so another block could fit, changing the tile size when the last wave was mostly empty, and checking the suspected bank conflicts. We ran out of time before we could test them on the T4.

[Part 2](/optimizing-cuda-sgemm-gb10/) picks up those ideas with a newer NVIDIA GB10, measuring each change against cuBLAS on that GPU.

[^units]: Intensity here is measured per byte, not per element loaded, since the ridge point is defined in bytes. The figure of 32 flops per byte assumes each value is read from global memory once per tile, and ignores caches.

[^plots]: The repository only has code for the naive and final versions. For the stages in between, this post describes the idea and quotes approximate numbers from the course report.
