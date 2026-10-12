+++
title = "Optimizing Matrix Multiply on CUDA, Part 2"
date = "2026-09-26"
slug = "optimizing-cuda-sgemm-gb10"
tags = ["Engineering", "Research"]
+++

In late summer 2026, almost two years after the course project in [part 1](/optimizing-cuda-sgemm/), I picked up the SGEMM (single-precision matrix multiply) kernel again. The course T4 was no longer available, but I now had an NVIDIA GB10 to work on. How much of the remaining gap to [cuBLAS](https://developer.nvidia.com/cublas) could we close? The old kernel started at about 60% of cuBLAS on this GPU. We'll get it to roughly parity, with a few failed optimizations along the way.

We're starting with the same 256-thread block as before, computing a 128×128 tile of $C$. A thread gets an 8×8 patch, so it keeps 64 running sums in registers. To update them, the block walks along $K$ (the inputs' shared dimension), copying slices of $A$ and $B$ from global into shared memory. Each thread reads its inputs from there. Global memory is off-chip; shared memory is the on-chip scratchpad of a streaming multiprocessor (SM). We'll keep doing the same multiply-adds and try to spend less time feeding them.

The course project stopped around kernel 5 of Simon Boehm's [worklog](https://siboehm.com/articles/22/CUDA-MMM). For this solo follow-up, we'll try the later steps against the limits from the T4 analysis: instruction overhead, registers that allowed only one block per SM, and waves of blocks that didn't fill the GPU.

## The GB10

First, the hardware. All timings below come from the GB10, the Blackwell chip in NVIDIA's [DGX Spark](https://www.nvidia.com/en-us/products/workstations/dgx-spark/). I'll call the initial pass through Boehm's remaining steps _round two_.

Compared with the T4, we're getting almost four times the compute, but less bandwidth:

- 48 SMs rather than 40.
- 100 KB of shared memory per SM rather than 64 KB. The register file is still 64K registers.
- About 29.5 TFLOP/s of peak FP32 compute, versus 7.68 on the T4.[^gb10peak] A TFLOP/s is a trillion floating-point operations per second.
- One memory shared by CPU and GPU, with 273 GB/s of bandwidth. That is about 15% less than the T4's 320 GB/s.

**The T4 timings from part 1 aren't the baseline for this post.** We're measuring cuBLAS on the GB10 too, with [TF32](https://developer.nvidia.com/blog/accelerating-ai-training-with-tf32-tensor-cores/) turned off. Otherwise cuBLAS could use lower-precision inputs and we'd be timing different work.

I've kept the intermediate kernels in [`next/`](https://github.com/aaron-ang/cuda-sgemm/tree/main/next), with a small standalone benchmark. Each adds one step to the previous kernel. All output checks are against cuBLAS.[^method]

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

The first two changes do most of the work here. By the end of round two we're at about 90% of cuBLAS, up from 60%.

## Two Blocks per SM

Compiling the course kernel on the GB10 gives 168 registers per thread. Shared memory has room for two blocks; the register file doesn't. Let's change the declaration to include [`__launch_bounds__(256, 2)`](https://docs.nvidia.com/cuda/cuda-programming-guide/05-appendices/cpp-language-extensions.html#launch-bounds), added to the kernel declaration. The 256 is our promise about maximum block size. The 2 asks the compiler to keep its register allocation low enough for two blocks to fit. Our register budget is now $65{,}536 / (2 \times 256) = 128$ per thread:

![One SM's register file, drawn to scale. At 168 registers per thread, a second 256-thread block doesn't fit; capped at 128, two blocks fit side by side.](/images/optimizing-cuda-sgemm/two-blocks-per-sm.svg "Shared memory had room for two blocks all along; registers were the only limit.")

The compiler spills 72 bytes per thread (18 floats) to _local memory_. "Local" means thread-private, not on-chip: these values go to the same off-chip memory as global allocations, with an extra store and load to recover each one. Yet the capped kernel runs 19% faster at $N = 2048$. The second block hides more latency than the spills add, as predicted in [part 1](/optimizing-cuda-sgemm/#occupancy). We'll keep the cap, at the cost of fewer spare registers for early loads and a maximum launch size of 256 threads.

## Wider Loads and Bank Conflicts

The T4 [_roofline_](/optimizing-cuda-sgemm/#the-roofline) suggested that we were spending the remaining time on work inside the SM, rather than waiting for global memory. That plot compares throughput with flops per byte fetched from global memory. We're already reusing those bytes heavily. Can we spend fewer instructions moving them through the SM?

**`float4` loads with $A$ transposed: +21%.** Let's load four floats at once. A [`float4` load](https://developer.nvidia.com/blog/cuda-pro-tip-increase-performance-with-vectorized-memory-access/) does that in one instruction, cutting the global-load instruction count to a quarter. At $N = 2048$ this alone gives 21%. The step includes a transpose of $A$ too, but that turns out not to explain any of the gain at this size.

The transpose is another idea from Boehm's kernel. To see why it should help, take thread 0 and its patch at rows 0–7, columns 0–7. For one step through the slice, we need 8 values of $A$ and 8 of $B$. The $A$ values are in a column, separated by 32 floats. If we transpose the shared copy of $A$, those 8 values become neighbors in a row. That ought to let us use 2 wide reads instead of 8 narrow ones. Below, we follow one row through the global loads and the transpose:

![One row of A's tile takes 32 loads one float at a time, and 8 with float4, four floats per load. In shared memory, row 0's values run down column 0 of the transposed tile, t0's four first, then t1's.](/images/optimizing-cuda-sgemm/float4-transpose.svg "Only row 0 of $A$'s tile is traced in global memory; the other rows load the same way.")

Except the compiler had already found another way to do the wide reads! Without the transpose, it combined values from four successive loop steps. Both compiled kernels issue 128 wide shared-memory reads per thread per slice; both profiler runs count 16.8 million shared-memory read instructions at $N = 2048$. So this explanation doesn't hold on the GB10. The transpose stays for now, since removing it from a later kernel with a larger thread patch costs 2–4%.

**Bank-conflict fix: +3%.** The [bank conflicts](https://docs.nvidia.com/cuda/cuda-programming-guide/02-basics/writing-cuda-kernels.html#shared-memory-bank-conflicts) predicted in part 1 are worth checking next. There are 32 shared-memory banks. In $B$'s tile, columns 0, 32, 64, … use bank 0, columns 1, 33, 65, … use bank 1, and so on. A warp's 32 threads run in lockstep, but different values requested from one bank can't all be read together. They take separate _passes_. Rather than pad the array, we'll change which columns belong to each thread.

Our old assignment gave each thread 8 neighboring columns of $B$, read 4 at a time. Thread 0 got columns 0–7, thread 1 got 8–15, and thread 4 started at column 32. Thread 0 and thread 4's first reads hit the same banks.

We can split each thread's columns into two groups of 4, separated by 64 columns. Thread 0 now reads 0–3 and 64–67; thread 1 reads 4–7 and 68–71. The first groups of 8 neighboring threads cover columns 0–31, one thread per bank. The second groups start at column 64, after all 16 threads' first groups, but they're separate read instructions and don't compete with those first groups. $A$'s rows get the same split:

![A map of 32 shared-memory banks while 8 threads each read 16 bytes of B's tile. With 8 contiguous columns per thread, neighboring threads start 8 columns apart and pairs share banks; with each thread's columns split into two groups of 4, neighbors start 4 columns apart and each bank serves one thread.](/images/optimizing-cuda-sgemm/bank-conflicts.svg "Hardware serves a 16-byte read 8 threads at a time; these 8 stand for every group in the warp.")

The profiler's shared-memory read bank-conflict count drops from 33.6 million to 54 thousand. Average passes per read: 5 before, 3 after. Why not 1? That average includes every shared-memory read in the kernel, not only the $B$ reads we've just fixed.

**Warp tiling: no gain on its own.** Next we'll change the region of $C$ belonging to each warp. Before, a warp owned 16 full-width rows of the block tile. With [warp tiling](https://github.com/NVIDIA/cutlass/blob/main/media/docs/cpp/efficient_gemm.md), it owns a compact 32×64 region. We now need 96 shared-memory values per step instead of 144:

![The outputs warp 0 owns in the 128×128 block tile: before, 16 full-width rows; with warp tiling, a 32×64 region. Shaded strips show what it reads from A and B per step: a third less data, but the same number of passes.](/images/optimizing-cuda-sgemm/warp-tiling.svg "Only warp 0 is traced; the other seven warps tile the rest of the block the same way.")

Fewer values, same runtime. The old mapping had a lot of threads reading the same value, and the hardware could serve those reads together. Counting values made the saving look larger than it was: the profiler still sees about 50 million shared-memory passes in either kernel. Also, we're still computing a 128×128 block tile from the same global-memory slices. None of this changes arithmetic intensity (flops per byte fetched from global memory).

**Smaller $K$ slice: +3%.** Reducing the slice depth from 32 to 16 does give another 3%. Looking at the stores, we can see a cost of that transposed $A$ layout: the stores into shared memory have bank conflicts. A shallower slice reduces them.

Each thread stores one float of the transposed tile, and its bank depends only on the row of $A$ it copied. At depth 32, 8 threads of a warp copy the same row: 8 writes to one bank, 8 passes. With depth 16, only 4 threads share the row. That's the 4 passes per store in the profiler. Still more than a conflict-free store's 1, but half what we had before.

## Closing the Gap to cuBLAS

We're now within about 10% of cuBLAS. So far we've reduced load instructions and bank conflicts. What if we can keep loading while the multiply-adds run?

**Double buffering: +2%.** With a second shared-memory buffer, we can load the next slice while computing on the current one. That's [double buffering](https://en.wikipedia.org/wiki/Multiple_buffering#Double_buffering_for_DMA), and it should reduce time waiting at `__syncthreads()`. The actual gain is only 2%. Two blocks per SM may already be doing most of this job, with one computing while the other waits. The second buffer lets one block overlap its own loads, but it also doubles that block's shared-memory use:

![Timelines of loads and compute. With one buffer they alternate; with two, the next slice loads during the current compute. Two blocks with one buffer each take turns computing, hiding the same loads while doing twice the work.](/images/optimizing-cuda-sgemm/double-buffering.svg "The second buffer doubles the block's shared memory, which is the price of overlapping its own loads.")

**Asynchronous copies: no gain.** The GB10 has a feature the T4 lacked: [`cp.async`](https://docs.nvidia.com/cuda/cuda-programming-guide/index.html), introduced in Ampere, between Turing and Blackwell. We can copy directly from global to shared memory, skipping the registers, and queue slices in multiple buffers (_stages_). This looked like a good way to find the last 10%.

It wasn't. In these runs the asynchronous-copy kernel is no faster than the double-buffered one. The larger thread patch in the last row will finally get us there:

{{< table title="GB10, closing the gap: each step's speed, and its share of cuBLAS" caption="GFLOP/s, from the same runs as round two." >}}
| Step | $N = 2048$ | vs cuBLAS | $N = 4096$ | vs cuBLAS |
|---|---|---|---|---|
| Double buffering (from round two) | 14,933 | 92% | 15,392 | 90% |
| + asynchronous copies | 14,490 | 89% | 15,384 | 90% |
| + 16×8 outputs per thread, 128×256 tile | 16,625 | 103% | 17,839 | 104% |
| cuBLAS | 16,204 | 100% | 17,119 | 100% |
{{< /table >}}

There are two stage configurations in these tests: two stages of depth 16 and four of depth 8, both using the same shared-memory capacity. The first ties plain double buffering; the second loses about 5%. This was not the improvement I expected. We need a way to tell whether it's the wait for the loads or the instructions themselves that are expensive.

Let's take parts out of the kernel to test that. At $N = 4096$, the double-buffered version gives about 15.7 TFLOP/s. Delete its global loads: 20.2, almost 30% faster. Put back only the shared-memory stores: 18.7. These probe kernels aren't useful matrix multiplies anymore, but they let us put a cost on parts of the real kernel. Removing a load still doesn't distinguish instruction cost from latency, though.

A different probe copies the same slice over and over, preserving the instructions without waiting on global memory. It gives 15.7 TFLOP/s, exactly like the real kernel. So copying hurts, but waiting isn't the main problem. We can cross-check with stall samples: 5% waiting on global memory in the double-buffered kernel, 18% on shared memory and barriers. `cp.async` is solving the less expensive problem here. And it still needs one instruction per 4-byte float to build the transposed $A$ tile, so the copy instruction count doesn't fall much.

**A bigger patch per thread: the gap closes.** We can get a clue from cuBLAS's own logs. At $N = 4096$, it selects a [CUTLASS](https://github.com/NVIDIA/cutlass) kernel computing 128 outputs per thread. That's twice the 64 outputs in our 8×8 patch. We can try the same number with a 16×8 patch. Loading still uses two `cp.async` stages, with 256 threads per block. The larger per-thread patch makes the block tile 128×256.

For thread 0, the count changes from 16 loaded values / 64 multiply-adds to 24 / 128. So the math doubles, while the loads increase by half. Expressed per 16 multiply-adds, we're down to 3 loads from 4. The math units are busy 70% of the time now, compared with 58% before.

The register count is now 237 per thread. Only one block fits again, undoing the occupancy increase we started with. That's okay for this patch: a thread has 128 independent multiply-adds to issue while loads are in flight. For comparison, the T4 kernel reached 62% of peak with 64 of them at 25% [occupancy](/optimizing-cuda-sgemm/#occupancy), meaning a quarter of the SM's warp slots were in use. We don't need to fill all the slots if the resident threads have enough independent work.

Now we're at 103–104% of cuBLAS for $N = 2048$ and $N = 4096$. Close enough to call it parity; the timing spread is larger than the lead (we'll check that below).

![An 8×8 patch takes 16 loads for 64 multiply-adds per step; a 16×8 patch takes 24 loads for 128. The larger patch's registers leave room for one block per SM instead of two.](/images/optimizing-cuda-sgemm/bigger-thread-tile.svg "One thread's view is shown; all 256 threads in the block do the same.")

## One Tile per Size

Next, the awkward sizes. [Part 1](/optimizing-cuda-sgemm/#small-and-odd-sizes) called this _wave quantization_: blocks run in waves, and a wave with empty SMs takes as long as a full one. A size just past a tile boundary can leave us with a nearly empty wave at the end. Two blocks per SM halve the wave count on the GB10, which sounds promising for these sizes. Yet at $N = 1025$ there's no speedup.

We have 81 blocks for 48 SMs. Some SMs must do two blocks either way: one after another, or both together at about half speed each. They finish at roughly the same time. cuBLAS has trouble here too, losing about 30% from $N = 1024$ to $N = 1025$. Our double-buffered kernel is about 2% **faster** than cuBLAS at $N = 1025$ and $N = 2049$.[^odd]

**Picking a tile per size.** A 128×256 tile isn't always a sensible fit. At $N = 2049$, the last column of tiles has only one useful column of $C$. At $N = 1024$, we get 32 tiles for 48 SMs, so a third of the GPU has nothing to do. Rather than force that tile onto every size, we'll choose from three versions:

- the big 128×256 tile
- a 128×128 tile, whose 128 threads each still compute 16×8 outputs
- a [split-K](https://github.com/NVIDIA/cutlass/blob/main/media/docs/cpp/efficient_gemm.md) version, which splits the $K$ dimension three ways so more SMs have work when there are few tiles.

The 128×128 version leaves $A$ untransposed, allowing 16-byte copies rather than 4-byte ones and avoiding the transposed stores' bank conflicts. What about a 256×128 tile instead of 128×256? That variant ran 6–10% slower. It has twice as many rows of $A$ to copy.

The selection happens on the CPU before launch: estimate the work for the busiest SM in each version, then choose the smallest estimate. With this dispatch we're at 115% of cuBLAS for $N = 1025$ and 114% for $N = 2049$. The dispatch also fixes $N = 1024$: from 86% with the big tile to roughly 100%.

**Splitting the last wave: stream-K.** Suppose we have a few whole tiles left over after the full waves. Most SMs would have to sit out while those finish. For this case we'll use [stream-K](https://arxiv.org/abs/2301.03598): cut the leftover tiles along $K$ and hand pieces to all 48 SMs. Whoever computes a tile's final piece also adds the other pieces' sums and writes $C$. The split-K version above cuts every tile; this version leaves the full waves alone.

The gain over the 128×256 kernel is about 11% for $N = 2049$, and 4% for $N = 2048$. The latter is small enough to be near the timing spread. We can draw the same scheduling problem with six SMs instead of 48:

![Fourteen tiles on six SMs, drawn as lanes over time. Handed out whole, the last two tiles leave four SMs idle; split into K pieces, they spread across all six.](/images/optimizing-cuda-sgemm/last-wave.svg "Six SMs stand in for the GB10's 48; each lane is one SM over time. The kernel actually runs the split tiles first and the whole tiles after them, which takes the same total work.")

Two failed versions came before this one. The first assigned each SM one long run of $K$ steps across tile boundaries. That kept them busy, but they drifted apart enough to lose L2 reuse: about 40% more time per tile. The second let a tile be split into as many as 16 pieces. Its $N = 1025$ result was just 43% of cuBLAS. Neither was worth keeping.

With only 2–4 pieces allowed, the results improve. Choosing the split is a small CPU calculation before launch: for each candidate, count the work on the busiest SM and add a little for summing the pieces. Then launch the candidate with the least estimated work.

## Power Throttling

The simple multiply-add loop runs at 2.4 GHz. Why doesn't SGEMM hold that clock? The instructions also move data through shared memory and registers, which costs power. On this GB10 it's enough to hit the power budget. The chip responds by lowering the clock, something we hadn't looked into on the T4.

Watch the driver's report during a 50-second SGEMM run: about 95 W, power limit active the whole time, clock sliding from 2.2 GHz to 2.1. At that clock the peak is about 25.8 TFLOP/s. So cuBLAS's 17,119 GFLOP/s at $N = 4096$ is roughly two-thirds of the peak at that clock.

There is a neat check for this in Horace He's [H100 experiment](https://www.thonking.ai/p/strangely-matrix-multiplications). Multiply matrices of zeros instead of random values: fewer bits flip, less power is drawn, and the clock stays higher. It works on the GB10 too, about 10% faster for these kernels with all-zero inputs. That makes input values a benchmark parameter, even when the executed instructions don't change.

This is why I'm hesitant to call the 128×256 kernel faster than cuBLAS. The same kernel's timing changes by up to 5% between sets of runs. Retiming each size in the same session gave the same 3–4% lead for the 128×256 kernel both times, but we can't ignore variation larger than the result we're reporting.

## Profiling Against cuBLAS

We can take the power limit out of this comparison with the profiler. Its kernel runs are brief enough that the clock doesn't drop. The 128×256 kernel again comes out about 4% ahead of cuBLAS at full clock, keeping the math units busy 70% of the time versus 62%.

cuBLAS is closed-source, but the profiler can still count its shared-memory passes. For the same loads, it needs about 3 for every 4 of ours. I suspected the mapping: our half-warps cover 2 rows of 8 outputs. A 4×4 square should, by my count, have needed 3 passes.

Testing that gave about 5 passes instead, with no speed change. My model of the counter was wrong. I still don't know how cuBLAS saves those passes, or whether they'd matter under the power limit.

## Looking Back

We've caught up with cuBLAS by giving each thread more outputs, the same register-tiling idea that worked in part 1. We're back to one block per SM, now with 128 independent multiply-adds per thread per step. The earlier two-block version helped the smaller patch; it isn't needed for this one.

I'm stopping at parity for true 32-bit SGEMM. Power-related timing variation is larger than the 3–4% lead, even though it survived repeated timings and the profiler comparison. [Tensor cores](https://www.nvidia.com/en-us/data-center/tensor-cores/) would offer much larger gains if we allowed lower-precision inputs.

[^method]: Each figure is the median of 3 sets of 7 timed runs, all measured in one session on an otherwise idle machine. Sets of the same kernel differed by up to about 5%, because the chip hits its power limit, as described below.

[^gb10peak]: The peak comes from 48 SMs × 128 cores × 2 flops per cycle at the 2.4 GHz clock. A plain multiply-add loop reached 28.9 TFLOP/s. A simple read kernel reached about 240 GB/s of the 273 GB/s bandwidth.

[^odd]: At $N = 1025$ the double-buffered kernel ran at 11,119 GFLOP/s against cuBLAS's 10,851, and at $N = 2049$ at 12,825 against 12,578. cuBLAS ran at 15,575 at $N = 1024$. The benchmark pads every row to a multiple of 4 floats, which keeps the wide loads legal at odd sizes, and cuBLAS ran on the same padded layout.
