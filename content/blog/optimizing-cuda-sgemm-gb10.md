+++
title = "Optimizing Matrix Multiply on CUDA, Part 2"
date = "2026-09-26"
slug = "optimizing-cuda-sgemm-gb10"
tags = ["Engineering", "Research"]
+++

In late summer 2026, I returned to the SGEMM (single-precision matrix multiply) kernel from [Part 1](/optimizing-cuda-sgemm/). Almost two years had passed since the course project, and although I no longer had access to its T4 GPU, I now had an NVIDIA GB10 to experiment with. The course kernel reached about 60% of [cuBLAS](https://developer.nvidia.com/cublas)'s throughput on the new GPU, leaving room to continue the optimizations we had stopped short of implementing.

The starting configuration is unchanged: a block of 256 threads computes a 128×128 tile of $C$, with each thread keeping an 8×8 patch of running sums in registers. To update those sums, the block works through $K$, the inputs' shared dimension, copying slices of $A$ and $B$ from global memory into shared memory. Each thread then reads the shared copies into registers for its multiply-adds. Global memory is off-chip, while shared memory is the on-chip scratchpad of a streaming multiprocessor (SM). Part 1 improved throughput by reusing inputs across outputs; this follow-up examines the cost of moving those inputs and how much independent work the GPU has available while loads are in progress.

The course project stopped around kernel 5 of Simon Boehm's [worklog](https://siboehm.com/articles/22/CUDA-MMM), so the later kernels provided a starting point for this solo follow-up. The T4 analysis had suggested several things to investigate: instruction overhead, a register count that allowed only one block per SM, and matrix sizes that left some SMs idle. On the GB10, these experiments eventually brought the kernel to roughly cuBLAS's throughput, although some changes that seemed promising brought little improvement.

## The GB10

All timings in this post come from the GB10, the Blackwell chip in NVIDIA's [DGX Spark](https://www.nvidia.com/en-us/products/workstations/dgx-spark/). I refer to the initial sequence of changes below as _round two_, following the course project in Part 1.

The GB10 has almost four times the T4’s peak FP32 throughput,[^gb10peak] but about 15% less memory bandwidth:

{{< table title="T4 and GB10 hardware resources" >}}
| Resource | T4 | GB10 |
|---|:---:|:---:|
| SMs | 40 | 48 |
| Shared memory per SM | 64 KB | 100 KB |
| 32-bit registers per SM | 65,536 | 65,536 |
| Peak FP32 throughput | 7.68 TFLOP/s | ~29.5 TFLOP/s |
| Memory bandwidth | 320 GB/s | 273 GB/s |
{{< /table >}}

A TFLOP/s is a trillion floating-point operations per second. The GB10’s memory is shared by the CPU and GPU.

To measure the remaining gap, I benchmarked both the course kernel and cuBLAS on the GB10; the T4 timings from Part 1 serve only as background. cuBLAS runs with [TF32](https://developer.nvidia.com/blog/accelerating-ai-training-with-tf32-tensor-cores/) disabled so that both implementations use full-precision FP32 inputs. Allowing cuBLAS to use lower-precision inputs would change the comparison.

The intermediate kernels are in [`next/`](https://github.com/aaron-ang/cuda-sgemm/tree/main/next), together with a small standalone benchmark. Each kernel adds a step to the previous version, and all outputs are checked against cuBLAS.[^method] The table summarizes this sequence at $N = 2048$ and $N = 4096$.

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

Allowing a second block per SM and widening the loads account for most of the improvement in this sequence. The later changes bring the kernel to about 90% of cuBLAS's throughput, compared with roughly 60% for the course kernel.

## Two Blocks per SM

The first constraint appears in the compiler's register allocation: the course kernel uses 168 registers per thread on the GB10. Shared memory can accommodate two blocks, but the register file cannot hold two 256-thread blocks at that allocation. Adding [`__launch_bounds__(256, 2)`](https://docs.nvidia.com/cuda/cuda-programming-guide/05-appendices/cpp-language-extensions.html#launch-bounds) to the kernel declaration asks the compiler to target two resident blocks, with no more than 256 threads in each. That leaves a budget of $65{,}536 / (2 \times 256) = 128$ registers per thread:

![One SM's register file, drawn to scale. At 168 registers per thread, a second 256-thread block doesn't fit; capped at 128 registers, two blocks fit side by side.](/images/optimizing-cuda-sgemm/two-blocks-per-sm.svg "Shared memory had room for two blocks all along; registers were the only limit.")

Fitting that budget requires the compiler to spill 72 bytes per thread, equivalent to 18 floats, into _local memory_. Despite the name, local memory is off-chip; “local” means that the storage belongs to an individual thread. The extra stores and loads could offset the benefit of a second resident block, but at $N = 2048$ the kernel still runs 19% faster. That result is consistent with the second block hiding more latency than the spills add, as anticipated in [Part 1](/optimizing-cuda-sgemm/#occupancy). I kept the register cap for the next experiments, accepting the spills, less register space for loading inputs early, and the 256-thread maximum block size.

## Wider Loads and Bank Conflicts

[Part 1’s roofline analysis](/optimizing-cuda-sgemm/#the-roofline) suggested that the kernel already reused enough global-memory data that fetching fewer bytes per multiply-add was unlikely to improve throughput much further. The next question was how much work the SM spent moving that data. Even when the number of bytes stays the same, wider loads can reduce the instructions needed to bring the inputs into registers.

### Loading Four Floats at a Time

A [`float4` load](https://developer.nvidia.com/blog/cuda-pro-tip-increase-performance-with-vectorized-memory-access/) transfers four floats in one instruction, reducing the global-load instruction count to a quarter of the scalar version's count. The next kernel combines these loads with a transpose of $A$ in shared memory and improves throughput by 21% at $N = 2048$. Testing the two changes separately showed that, at this size, the gain came from the wider global loads rather than the transpose.

![A row of 32 floats requires 32 scalar global-load instructions or eight float4 instructions, with the same bytes transferred.](/images/optimizing-cuda-sgemm/float4-loads.svg "One row of the input tile is shown; widening the loads does not change its size.")

The transpose follows Boehm’s kernel and has a separate purpose. Thread 0 needs eight values from a column of $A$ at each step through the slice. Transposing the shared copy places them next to one another, permitting two wide reads instead of eight scalar ones:

![At k = 0, thread 0’s eight A values are 32 floats apart before transposition and adjacent afterward.](/images/optimizing-cuda-sgemm/shared-transpose.svg "The layout permits two four-float reads; the compiled instruction counts below test whether it actually saves instructions.")

The compiled kernels showed why the expected saving did not appear: without the transpose, the compiler already combined values from successive loop steps into wide reads. Both layouts issued the same number of shared-memory read instructions.[^transpose] I kept the transpose because removing it from a later kernel with a larger per-thread patch reduced throughput by 2–4%.

### Changing the Shared-Memory Access Pattern

The [bank conflicts](https://docs.nvidia.com/cuda/cuda-programming-guide/02-basics/writing-cuda-kernels.html#shared-memory-bank-conflicts) suspected in Part 1 offered another place to look. Shared memory has 32 banks. When threads request different values from the same bank, the hardware needs separate _passes_ to serve them.

In the previous layout, each thread handled eight consecutive columns of $B$. Bank numbers repeat every 32 columns, causing some threads to request different values from the same banks. Splitting each thread’s columns into two groups of four lets neighboring threads use different banks. The two groups are read by separate instructions, and the rows assigned from $A$ receive the same split.[^bank-mapping]

![Threads 0 and 4 first request different values from banks 0–3. After regrouping, thread 4 uses banks 16–19 instead, avoiding that conflict.](/images/optimizing-cuda-sgemm/bank-conflicts.svg "Only two threads’ first four-float reads are shown; the same regrouping applies to the other threads.")

The revised mapping cuts the profiler’s shared-memory read bank-conflict count from 33.6 million to 54 thousand, but improves throughput by only about 3%.[^bank-passes]

### Assigning a Compact Tile to Each Warp

Changing the region of $C$ assigned to each warp might reduce the shared-memory data it needs. Previously, one warp owned 16 full-width rows of the block's tile. With [warp tiling](https://github.com/NVIDIA/cutlass/blob/main/media/docs/cpp/efficient_gemm.md), it instead owns a compact 32×64 region, requiring 96 distinct shared-memory values per step rather than 144:

![The outputs warp 0 owns in the 128×128 block tile: before, 16 full-width rows; with warp tiling, a 32×64 region. Shaded strips show what it reads from A and B per step: a third less data, but the same number of passes.](/images/optimizing-cuda-sgemm/warp-tiling.svg "Only warp 0 is traced; the other seven warps tile the rest of the block the same way.")

Despite needing fewer distinct values, warp tiling did not meaningfully improve runtime. The earlier mapping allowed the hardware to serve repeated requests for the same value together, and both kernels still required about 50 million shared-memory passes. The change also leaves global-memory traffic and arithmetic intensity unchanged: both kernels compute the same block tile from the same input slices.

### Reducing the Slice Depth

The transposed layout can also create bank conflicts when storing $A$ into shared memory. Reducing the $K$ slice depth from 32 to 16 halves the number of threads writing to the same bank, cutting the required passes from eight to four.[^slice-stores] Throughput improves by another 3%, even though the stores are still not conflict-free.

## Closing the Gap to cuBLAS

After reducing load instructions and bank conflicts, the kernel is within about 10% of cuBLAS's throughput. The remaining load latency suggested a different approach: instead of only making data movement cheaper, the block could begin loading the next slice before it finished computing with the current one.

### Overlapping Loads and Computation

[Double buffering](https://en.wikipedia.org/wiki/Multiple_buffering#Double_buffering_for_DMA) provides a second shared-memory buffer so that loads for the next slice can overlap computation on the current slice. In this version, the global loads first bring the next slice into registers. After computation on the current slice finishes, the threads store those prefetched values into the other shared-memory buffer, synchronize, and swap the buffers’ roles:

![While slice 1 is used for computation from buffer 0, slice 2 is fetched into registers. After computation, it is stored in buffer 1, followed by synchronization and a buffer-role swap.](/images/optimizing-cuda-sgemm/buffer-movement.svg "The next slice waits in registers until it can be stored in the other shared-memory buffer.")

This could reduce time spent waiting at `__syncthreads()`, but the measured gain is only about 2%. One possible explanation is that the two resident blocks already provide much of this overlap, with one block computing while the other waits. A second buffer allows overlap within a block as well, at the cost of doubling its shared-memory use:

![Schematic timelines compare alternating loads and computation, overlapping them within one block, and overlapping work from two resident blocks.](/images/optimizing-cuda-sgemm/buffer-overlap.svg "Two resident blocks share the SM’s execution resources; their timeline contains twice as much work as either one-block example.")

The GB10 supports another way to overlap data movement: [`cp.async`](https://docs.nvidia.com/cuda/cuda-programming-guide/index.html), introduced with Ampere after the T4's Turing architecture. This instruction copies directly from global to shared memory without passing through registers, and allows the kernel to queue slices across several buffers, or _stages_. It seemed worth testing whether that mechanism could reduce the remaining gap to cuBLAS.

In these runs, asynchronous copies did not outperform the double-buffered kernel. The table includes that result and the later increase in per-thread outputs, which eventually brought the kernel to cuBLAS's throughput:

{{< table title="GB10, closing the gap: each step's speed, and its share of cuBLAS" caption="GFLOP/s, from the same runs as round two." >}}
| Step | $N = 2048$ | vs cuBLAS | $N = 4096$ | vs cuBLAS |
|---|---|---|---|---|
| Double buffering (from round two) | 14,933 | 92% | 15,392 | 90% |
| + asynchronous copies | 14,490 | 89% | 15,384 | 90% |
| + 16×8 outputs per thread, 128×256 tile | 16,625 | 103% | 17,839 | 104% |
| cuBLAS | 16,204 | 100% | 17,119 | 100% |
{{< /table >}}

Neither asynchronous-copy configuration outperformed plain double buffering.[^async-stages] To understand why, I needed to distinguish time spent waiting for data from the cost of issuing the instructions that move it.

### Investigating the Cost of Copying

I first removed parts of the kernel to see how they affected runtime. Removing global loads made it faster, but that eliminated both the instructions and any wait for their results. The experiment could not distinguish the two costs, and the modified kernels no longer computed a valid matrix product.[^copy-probes]

A separate probe kept the copy instructions but repeatedly copied the same slice, avoiding the wait on global memory. It ran no faster than the real kernel, suggesting that global-memory latency was not the main cost. Profiler stall samples supported that interpretation. Meanwhile, constructing the transposed $A$ tile still required one `cp.async` instruction per four-byte float, so asynchronous copies did little to reduce the instruction count either.

### Increasing the Outputs per Thread

cuBLAS's logs suggested another direction. At $N = 4096$, it selected a [CUTLASS](https://github.com/NVIDIA/cutlass) kernel computing 128 outputs per thread, twice the 64 in the current 8×8 patch. Giving each thread a 16×8 patch achieves the same output count in our kernel. With 256 threads and two `cp.async` stages retained, the block's output tile grows to 128×256.

The larger patch lets each thread reuse its inputs across more multiply-adds. Thread 0 now loads 24 values for 128 multiply-adds per step, compared with 16 values for 64 previously. Computation doubles while loads increase by only half, reducing the shared-memory loads from four to three per 16 multiply-adds. Math-unit utilization rises from 58% to 70%.

![An 8×8 patch uses 16 shared-memory loads for 64 multiply-adds per step; a 16×8 patch uses 24 for 128.](/images/optimizing-cuda-sgemm/thread-tile-reuse.svg "One thread at one step through K: computation doubles while loads increase by half.")

Holding the larger patch raises the register count to 237 per thread, leaving room for only one block per SM again. This gives up the second block that helped at the start, but each thread now has 128 independent multiply-adds per step to issue while loads are in progress. As in the [T4 kernel](/optimizing-cuda-sgemm/#occupancy), independent work within each thread helps keep the SM busy without high occupancy.

![Two 256-thread blocks fit at 128 registers per thread. At 237 registers per thread, the larger patch leaves space for only one block.](/images/optimizing-cuda-sgemm/thread-tile-registers.svg "Register use is drawn to scale for the SM’s 65,536-register file.")

The larger patch closes the remaining gap at $N = 2048$ and $N = 4096$. Interpreting the small lead in the table requires accounting for the GB10’s power limit, which I discuss below.

## Choosing Tiles for Different Matrix Sizes

The larger tile works well at $N = 2048$ and $N = 4096$, but other sizes bring back the scheduling problem discussed in [Part 1](/optimizing-cuda-sgemm/#small-and-odd-sizes). With _wave quantization_, a final wave can leave most SMs idle while a few finish their blocks. A matrix just beyond a tile boundary is particularly awkward because it adds blocks containing very little useful output. Allowing two resident blocks per SM can reduce the number of waves, but the earlier two-block configuration did not improve throughput at $N = 1025$.

At that size, the 128×128 configuration launches 81 blocks across 48 SMs, so some SMs must process two blocks. Making both resident at once does not give an SM twice the execution resources; in these runs, sharing those resources took about as long as processing the blocks successively. cuBLAS also loses about 30% of its throughput between $N = 1024$ and $N = 1025$. Our double-buffered kernel is about 2% faster than cuBLAS at both $N = 1025$ and $N = 2049$.[^odd]

### Selecting Among Three Kernels

The 128×256 tile has its own problems at these sizes. At $N = 2049$, its final column of tiles contains only one useful column of $C$. At $N = 1024$, it produces just 32 tiles for 48 SMs, leaving a third of the GPU without work. To accommodate these cases, the implementation chooses among three variants:

- the big 128×256 tile
- a 128×128 tile, whose 128 threads each still compute 16×8 outputs
- a [split-K](https://github.com/NVIDIA/cutlass/blob/main/media/docs/cpp/efficient_gemm.md) version, which splits the $K$ dimension three ways so more SMs have work when there are few tiles.

The 128×128 variant leaves $A$ untransposed, allowing copies of 16 bytes per instruction instead of four and avoiding the store conflicts caused by the transposed layout.[^alternate-tile]

Before launch, the CPU estimates the work assigned to the busiest SM for each variant and chooses the one with the smallest estimate. With this selection, throughput reaches 115% of cuBLAS's at $N = 1025$ and 114% at $N = 2049$. At $N = 1024$, selecting a more suitable kernel brings throughput from 86% of cuBLAS with the large tile to roughly parity.

### Distributing the Partially Filled Wave

Tile selection still leaves cases where some SMs finish while others have whole tiles left to compute. [Stream-K](https://arxiv.org/abs/2301.03598) addresses this imbalance by splitting those tiles along $K$ and distributing their work across all 48 SMs. The SM computing a tile's final piece combines the partial sums and writes the result to $C$. Unlike the split-K variant above, this approach leaves the tiles assigned to full waves intact and splits only the work that would form the incomplete wave.

This version improves throughput over the 128×256 kernel by about 11% at $N = 2049$ and 4% at $N = 2048$, although the latter gain is close to the timing variation. The diagram illustrates how splitting the remaining tiles can spread their work more evenly, using six SMs in place of 48:

![Fourteen tiles on six SMs, drawn as lanes over time. Handed out whole, the last two tiles leave four SMs idle; split into K pieces, they spread across all six.](/images/optimizing-cuda-sgemm/last-wave.svg "Six SMs stand in for the GB10's 48; each lane is one SM over time. The kernel actually runs the split tiles first and the whole tiles after them, which takes the same total work.")

Reaching that version took two unsuccessful attempts. The first assigned each SM a long sequence of $K$ steps that could cross tile boundaries. Although this kept the SMs occupied, they progressed far enough apart to lose L2 reuse, increasing time per tile by about 40%. The second allowed a tile to be divided into as many as 16 pieces and reached only 43% of cuBLAS's throughput at $N = 1025$. Keeping every SM busy was not sufficient when the scheduling also increased the cost of processing a tile.

Restricting each tile to two through four pieces gave better results. Before launch, the CPU compares the estimated work on the busiest SM, including the cost of combining partial sums, and chooses the candidate with the lowest estimate.

## Power Limits and the cuBLAS Comparison

As the kernels approached cuBLAS’s throughput, timing variation became harder to ignore. A simple multiply-add loop could sustain 2.4 GHz on the GB10, but SGEMM also moves data through shared memory and registers. During a longer run, it reached the device’s power budget and the clock fell to about 2.1 GHz.[^power]

Horace He's [H100 experiment](https://www.thonking.ai/p/strangely-matrix-multiplications) suggested a way to investigate: multiply zero-filled matrices instead of random ones. With fewer bits changing, power draw can fall and the clock can stay higher. Repeating that experiment on the GB10 made these kernels about 10% faster without changing their instructions. The input values themselves were affecting the benchmark.

The final 128×256 kernel’s 3–4% lead over cuBLAS survived repeated timings in the same session. Brief profiler runs, which stayed at full clock, found a similar lead and higher math-unit utilization.[^profile] But the same kernel’s timings varied by up to 5% between sets of runs, more than the margin over cuBLAS. I therefore treat the result as approximate parity rather than a clear performance advantage.

The profiler also left an unresolved question: cuBLAS used fewer shared-memory passes, but changing the thread mapping to try to match it increased our pass count without improving runtime.[^cublas-passes] I cannot yet explain that difference or conclude that matching the count would help under the power limit.

## Looking Back

Increasing the outputs per thread eventually brought the kernel to cuBLAS's throughput, extending the register-tiling idea from Part 1. The smaller patch benefited from a second resident block, while the larger patch provided enough independent work to perform well with one. The useful balance between reuse and concurrency changed as the patch grew, while tile selection and splitting addressed the separate problem of distributing work across the GPU.

I have stopped here for true 32-bit SGEMM. Allowing lower-precision inputs would open a different route to much larger gains through [tensor cores](https://www.nvidia.com/en-us/data-center/tensor-cores/).

[^method]: Each reported timing is the median of three sets of seven timed runs, collected in one session on an otherwise idle machine. Even within that session, timings for the same kernel varied by up to about 5% as the chip reached its power limit.

[^gb10peak]: At 2.4 GHz, 48 SMs × 128 cores × 2 flops per cycle gives a theoretical peak of about 29.5 TFLOP/s. A simple multiply-add loop reached 28.9 TFLOP/s, while a read-only kernel measured about 240 GB/s against the specified 273 GB/s bandwidth.

[^odd]: The double-buffered kernel reached 11,119 GFLOP/s at $N = 1025$, compared with cuBLAS's 10,851. At $N = 2049$, the figures were 12,825 and 12,578 respectively; cuBLAS reached 15,575 at $N = 1024$. Both implementations used the same layout, with each row padded to a multiple of four floats so the wide loads remained valid at odd sizes.

[^transpose]: Thread 0 computes rows 0–7 and columns 0–7 of $C$, requiring eight $A$ values and eight $B$ values per step. Before transposition, its $A$ values are 32 floats apart. Without the transpose, the compiler combined values from four successive loop steps into wide reads. Both layouts issued 128 wide shared-memory reads per thread per slice, and both profiler runs recorded 16.8 million shared-memory read instructions at $N = 2048$.

[^bank-mapping]: Successive floats map to successive banks, repeating every 32 columns. Previously, thread 0 handled columns 0–7, thread 1 handled 8–15, and thread 4 began at 32. Their first four-float reads could request different values from the same banks. In the revised mapping, thread 0 reads 0–3 and 64–67, while thread 1 reads 4–7 and 68–71. Across eight neighboring threads, the first groups cover 0–31 without repeating a bank. The second groups use separate instructions, so they do not compete with the first groups even though the bank numbers repeat.

[^bank-passes]: The average passes per shared-memory read fell from five to three. This average includes all shared-memory reads, so it does not imply that the $B$ reads illustrated above still require three passes.

[^slice-stores]: Each thread stores one float of $A$, and its destination bank depends on the row it copied. At a slice depth of 32, eight threads in a warp copy values from the same row, requiring eight writes to one bank. At a depth of 16, four threads share the row, matching the profiler’s four passes per store.

[^async-stages]: The configurations use the same shared-memory capacity: two stages with a slice depth of 16, or four stages with a depth of eight. The two-stage version roughly matched plain double buffering; the four-stage version was about 5% slower.

[^copy-probes]: At $N = 4096$, the double-buffered kernel reached about 15.7 TFLOP/s in this set of experiments. Removing global loads raised the nominal throughput to 20.2 TFLOP/s, almost 30% higher; restoring only shared-memory stores brought it to 18.7 TFLOP/s. These modified kernels do not compute a useful matrix product: the figures express runtime changes using the original operation count. The repeated-slice probe reached the same 15.7 TFLOP/s as the real kernel. In the double-buffered kernel, 5% of stall samples were associated with waiting on global memory, compared with 18% on shared memory and barriers.

[^alternate-tile]: A 256×128 tile, tested as an alternative to 128×256, had twice as many rows of $A$ to copy and ran 6–10% slower.

[^power]: During a 50-second SGEMM run, the driver reported about 95 W with the power limit active throughout, while the clock fell from 2.2 GHz to 2.1 GHz. At 2.1 GHz, theoretical FP32 peak is about 25.8 TFLOP/s; cuBLAS’s 17,119 GFLOP/s at $N = 4096$ is roughly two-thirds of that peak. This power constraint was not investigated on the T4.

[^profile]: Brief profiler runs put the 128×256 kernel about 4% ahead of cuBLAS, with math-unit utilization at 70% versus cuBLAS’s 62%. Retiming each size twice in the same session also reproduced the 3–4% lead. Short runs at full clock provide another comparison, but do not remove the longer-run power constraint.

[^cublas-passes]: cuBLAS’s counters showed roughly three shared-memory passes for loads that required four in our kernel. Each half-warp covers two rows of eight outputs; I estimated that rearranging them into a 4×4 square could reduce the count to three. The modified kernel instead required about five passes, with no change in performance, contradicting the estimate.
