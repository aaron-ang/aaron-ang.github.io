+++
title = "Letter Boxed: Lessons from Optimizing Search"
date = "2026-04-19"
slug = "solving-letter-boxed"
tags = ["Design", "Engineering"]
+++

[Letter Boxed](https://www.nytimes.com/puzzles/letter-boxed) is a daily New York Times (NYT) word game with twelve letters surrounding a square; three per side. You make words by tracing a path between letters, with a few rules:

- Words must be at least three letters long.
- Two letters in a row cannot come from the same side of the square.
- Each new word starts with the last letter of the previous one.
- You win when every letter has been used. NYT suggests a target, usually four or five words, and fewer is better.

A solution traces a path across the square, one letter at a time. Most of this post uses the puzzle in the figure below as its example puzzle. Its sides are MSO, HGD, RIE and NAF:

![FORMED is drawn letter by letter, alternating sides of the square. DASHING starts from its final D and covers the remaining six letters, solving the puzzle in two words.](/images/solving-letter-boxed/puzzle-rules.svg)

I first wrote a Letter Boxed solver in Java for [CS 112](https://www.cs.bu.edu/courses/cs112/) in 2022. It later became a [TypeScript](https://www.typescriptlang.org/) web app, but non-trivial puzzles could freeze the page while the search ran. To keep the page responsive and to explore cloud computing, I moved the search to a [Node.js](https://nodejs.org/) _cloud function_ and later rewrote that function in [Go](https://go.dev). The [current version](https://aaron-ang.github.io/letter-boxed) brings the search back to the browser, without blocking the page.

The prospect of using WebGPU, improving pruning, and reducing repeated work in the backtracking algorithm gave me a reason to revisit the solver. Frontier models had become much better at coding too, making this an opportunity to see how well they could help optimize an existing solver. The changes below bring the search back to the browser: bitmasks and dictionary filtering reduce the work it does, while WebGPU lets the solver test combinations of words in parallel when searching for the best solution.

## The First Solver: Backtracking

The original assignment used _recursive [backtracking](https://en.wikipedia.org/wiki/Backtracking)_, and the solver still follows that approach. Starting with an empty word, it tries adding a letter and checks whether the result could lead to a solution. If a choice breaks a rule, the search undoes it and tries another letter. This way, it can abandon an invalid beginning without exploring all the words and word chains that would follow from it.

At each step, the search considers the square's twelve letters. A letter is rejected if:

- it is on the same side as the previous letter,
- adding it would leave a partial word that no dictionary word begins with, or
- it would complete a word already used in the chain.

When the letters collected so far form a dictionary word of at least three letters, the search has another option: end that word and begin the next one from its final letter. It can explore this choice as well as continuing to extend the current word.

Of these checks, the dictionary [prefix](https://en.wikipedia.org/wiki/Substring#Prefix) check eliminates the most branches. If no dictionary word begins with the letters collected so far, adding more letters cannot rescue that word. The solver therefore keeps a map of dictionary prefixes, with a marker for those that are complete words too. For example, FORME allows the search to continue toward FORMED, while reaching FORMED gives it the option of ending the word.

The remaining limit is how many words the search may use. **Solve** starts with a limit of one word and repeats the search with limits of two through five until it finds an answer. This is [_iterative deepening_](https://en.wikipedia.org/wiki/Iterative_deepening_depth-first_search): because every smaller word limit has already been tried, the first solution uses the fewest words.

After successfully finding an initial solution, the button changes to **Find Best**. GARNISHED DEFORMED and FORMED DASHING both solve our square in two words, but the second uses fewer letters. Keeping the initial solution's word count as its _word limit_, Find Best searches for the solution with the fewest total letters.

## Where the Solver Runs

The web app has gone through three versions: v1 ran the search on the browser's main thread, v2 moved it to a cloud server, and v3 brought it back to the browser in a [_Web Worker_](https://developer.mozilla.org/en-US/docs/Web/API/Web_Workers_API). The worker runs the search in a background thread so the page stays responsive. The figure below compares these versions:

![Three web versions: v1 blocks the browser's main thread; v2 solves puzzles in a cloud function over the network; v3 solves locally in a Web Worker, with WebGPU for Find Best.](/images/solving-letter-boxed/architecture-evolution.svg)

### v1: On the Main Thread

In the first TypeScript version, the search shared the browser's [main thread](https://developer.mozilla.org/en-US/docs/Glossary/Main_thread) with button clicks and page updates. On a difficult puzzle, the search could keep that thread busy long enough for the page to appear frozen. The browser could not repaint or respond to another click until the solver returned.

### v2: In the Cloud

For v2, I moved the solver to a [Google Cloud Function](https://cloud.google.com/functions). The browser sent the puzzle's letters to the function, which ran the search and returned a solution. At the time, the README described the tradeoff as:

> Significant reduction in client RAM and CPU load in exchange for slightly increased response time.

With the search running on the server, the browser could stay responsive while it waited for a solution. That came with a delay even when the search itself was quick, since every answer had to make a network round trip. An idle function could take longer still: a _[cold start](https://docs.cloud.google.com/functions/1stgendocs/concepts/execution-environment#cold-starts)_ meant waiting for a fresh instance to launch before it could run the solver.

The Go rewrite came a month later. Go generally starts and runs CPU-heavy code faster than Node.js; [gzipping](https://en.wikipedia.org/wiki/Gzip) the responses cut transfer time too.[^rust] Neither removed the round trip.

### v3: Back in the Browser

I hadn't known about Web Workers when I chose the cloud function. By the time I returned to the project for v3, I knew the browser could run the search in a worker while leaving the main thread free to handle clicks and repaint the page. [WebGPU](https://developer.mozilla.org/en-US/docs/Web/API/WebGPU_API) opened up another possibility: parts of the search could run on the user's GPU as well.[^support]

Before moving the search back, I changed the solver's core data structures to reduce the work it repeated during a solve. Together with the worker, those changes made it possible to remove the server, along with its network requests, cold starts, and bill. Solve time now depends on the user's device, with CPU search available when WebGPU is not supported.

## Bitmasks

The Go solver used string searches for both side checks and coverage. For a side check, it searched each of the four sides for both letters. For coverage, it searched the words for each of the twelve letters:

```go
func (ls *LetterSquare) allLettersUsed() bool {
	for _, letter := range ls.letters {
		anyWordHasLetter := false
		for _, word := range ls.words {
			if strings.Contains(word, letter) {
				anyWordHasLetter = true
				break
			}
		}
		if !anyWordHasLetter {
			return false
		}
	}
	return true
}
```

Although this code answers the coverage question correctly, the search can call it millions of times, repeatedly scanning strings for the same twelve letters. We can record that information in a 32-bit integer instead. Assign each letter a bit, using indices 0 through 11 in side order; M then gets bit 0 and F gets bit 11 in our example. Setting a word's bits produces a [_bitmask_](https://en.wikipedia.org/wiki/Mask_%28computing%29) that tells us which puzzle letters it contains, without searching the word again.

Combining the masks of FORMED and DASHING shows which letters the two words use together:

![Each of the twelve letters gets one bit. FORMED and DASHING each set their letters' bits, and OR-ing the two masks sets all twelve.](/images/solving-letter-boxed/bitmask-cover.svg "D appears in both words but still sets just one bit.")

With a mask for each word, checking whether two words cover the square only requires combining their bits and comparing the result with a mask containing all twelve letters:

```ts
(a.coverageMask | b.coverageMask) === allCoveredMask; // 0xFFF
```

The side check can use a similar lookup. Precomputing `sideOf[letterIndex]` lets the search compare the two letters' sides with two array reads, replacing the eight string searches used by the Go version.

Backtracking needs a little more care because it removes letters as well as adding them. A bitwise [OR](https://en.wikipedia.org/wiki/Bitwise_operation#OR) can record that S is present, but the mask does not record how many times S appears. Clearing its bit when undoing the last S in "SEES" would incorrectly forget the first one too. The solver instead rebuilds the mask from the remaining letters; for these short words, that adds little work.

## Precomputation

The square does not change while the solver explores different word chains, so many of the checks inside the search will keep giving the same answer. A word containing a letter outside the square, for example, will be invalid on every branch that reaches it. Rather than reject it repeatedly, we can remove it before searching. The same idea of _[precomputation](https://en.wikipedia.org/wiki/Precomputation)_ applies to the other information that depends only on the puzzle.

Filtering the dictionary removes much of this repeated work. From the original 26,648 words, we discard those with fewer than three letters, letters outside the square, or consecutive letters from the same side:

![The dictionary passes three filters: GO is too short, SNOW uses a letter not on the square, and GRID puts two same-side letters next to each other. FORMED survives.](/images/solving-letter-boxed/dictionary-filter.svg "The counts are for this puzzle; each puzzle keeps its own few hundred words.")

For our example square, only 565 words remain. Two other tested puzzles retained 255 and 580, so each kept roughly 1–2% of the original dictionary. Find Best can search these _valid words_ directly, with their coverage masks and first and last letters computed in advance:

```ts
interface ValidWord {
  word: string; // "FORMED"
  coverageMask: number; // which puzzle letters it covers, one bit each
  firstLetterIdx: number; // which first-letter list it belongs to
  lastLetterIdx: number; // which list the next word must come from
}
```

Alongside the valid words, the solver keeps a few lookups that it can reuse throughout the search:

- **Letter lookups** hold each letter's position, bit, and side in small per-puzzle arrays, avoiding further string searches.
- **An index by first letter** groups valid words into twelve lists, so a chain ending in `D` only needs to consider words in `D`'s list.
- **A per-puzzle cache** retains the valid words from Solve, allowing Find Best to use them without filtering the dictionary again.

## Find Best on the GPU

With complete valid words available, Find Best no longer needs to build each word one letter at a time. It can search for ways to join them instead: start with one word and append another whose first letter matches the previous word's last letter. Each sequence of joined words forms a _chain_.

For a given chain, each possible next word can be tested independently. Checking whether DASHING can follow FORMED does not change the result of checking DEFORMED after FORMED. This gives the GPU many independent (chain, word) pairs to process, each using the same checks.

### Parallelizing Search

To run those checks on the GPU, we use a _[compute shader](https://en.wikipedia.org/wiki/Compute_kernel)_: a program executed by many GPU threads. One thread handles one (chain, word) pair, and a batch of those threads is launched in a _dispatch_. Initially, every valid word forms a one-word chain, so our example's 565 words produce 565 × 565 pairs, or about 320,000 threads. Each thread checks the following:

1. Does this word start with the chain's last letter? If not, stop.
2. Is this word already in the chain? If so, stop.
3. OR the masks together. If the result covers all twelve letters, it is a solution. Otherwise, if the chain is still below the word limit, it is a candidate for the next pass.

WebGPU shaders are written in [WGSL](https://www.w3.org/TR/WGSL/), its shading language. The core of the shader performs those three checks:

```wgsl
if (lastWord.lastLetterIdx != newWord.firstLetterIdx) { return; }
for (var i = 0u; i < chain.wordCount; i++) {
  if (chain.words[i] == wordIdx) { return; }
}

var nc = chain;
nc.words[chain.wordCount] = wordIdx;
nc.coverageMask = chain.coverageMask | newWord.coverageMask;
nc.lastWordIdx = wordIdx;
nc.wordCount = chain.wordCount + 1u;
nc.totalChars = chain.totalChars + newWord.charCount;

if (nc.coverageMask == uniforms.targetMask) {
  let slot = atomicAdd(&counts[0], 1u);
  if (slot < arrayLength(&solutions)) { solutions[slot] = nc; }
} else if (nc.wordCount < uniforms.maxWords) {
  let slot = atomicAdd(&counts[1], 1u);
  if (slot < arrayLength(&nextChains)) { nextChains[slot] = nc; }
}
```

Several threads may find solutions in the same dispatch, so they need distinct slots in the result buffer. Each calls [`atomicAdd`](https://www.w3.org/TR/WGSL/#atomic-rmw) on the shared counter. This [atomic operation](https://en.wikipedia.org/wiki/Atomic_operation) returns a different slot number to each caller, allowing threads to write their chains without overwriting another solution. The example puzzle's four two-word solutions occupy only a small part of the grid:

![A grid of GPU threads, one per chain and candidate word. Mismatched first letters return at once; the rest OR their masks, recording full covers as solutions and dropping partial ones at the word limit.](/images/solving-letter-boxed/gpu-chain-extend.svg "The ellipses stand for the rest of this puzzle's 565 words, as both chains and candidates. All four of its solutions are in the drawn cells.")

### One Pass per Word Count

To find the fewest words, the CPU starts by checking for a one-word solution itself. If none exists, it dispatches the shader to extend each chain by one word, with one thread per (chain, word) pair, and waits for the pass to finish. The first pass produces two-word chains, the next three-word chains, and so on. Each word count forms one _level_ of the search.

Chains stay on the GPU in two _buffers_, blocks of GPU memory. Each pass reads one and writes new chains to the other, then they swap roles. Only the two counters and any solutions are _read back_ to CPU memory, together.

With those counts, the CPU decides what happens next. If the pass found no solutions, it dispatches again, until it reaches the word limit or runs out of chains. If the pass found any, the search stops, since those solutions have the fewest words.

I left ranking on the CPU. One scan selects the fewest total letters, breaking ties alphabetically. Moving that small job to the GPU would add complexity for little benefit.

The example puzzle needs only one pass, because its word limit is two. A second puzzle, with sides DCE, AUB, HMW and RNF, has a word limit of three, and its first solutions only appear on the second pass:

![The CPU uploads the words and one-word chains once. Each pass reads one chains buffer and writes the other. Pass 1 finds nothing, pass 2 finds eight, and the CPU picks CHAMBER REFUND DAWN.](/images/solving-letter-boxed/gpu-passes.svg "Keeping chains on the GPU avoids copying thousands of them back and forth after every pass.")

## CPU Fallback

Without WebGPU, or if a pass overflows the GPU's chain buffers, the worker searches on the CPU. It reuses the valid words, masks, and first-letter index, searching all one-word chains, then all two-word chains, up to the word limit. As on the GPU, the first level with a solution ends the search.

On the CPU, two different chains can leave us with the same search to do. Suppose a chain ends in E, covers C, E, A, M, and R, and has room for one more word. If none of the E-words completes it, another chain with the same last letter, coverage, and remaining word count will fail too.[^reuse] We use [memoization](https://en.wikipedia.org/wiki/Memoization) to record that failed _position_, defined by those three values, and skip the repeated search regardless of which words brought us there.

The number of possible positions is small:

- 12 possible last letters, one for each letter on the square,
- 4,096 possible sets of covered letters, since each of the 12 letters is either covered or not ($2^{12}$),
- one to four words left, with the first of up to five words already placed.

That gives at most 196,608 positions. At one byte each, the table takes under 300 KB. Even ruling out a puzzle with no solution in five words takes only a few milliseconds.

The animation below uses the second puzzle, with sides DCE, AUB, HMW and RNF. It shows one dead end being recorded and then reused:

![At level 3, ARC → CAME ends in E with five letters covered and one word left. None of the 7 E-words completes it. The position is marked dead, and when CREAM → MARE later reaches it, the search skips it.](/images/solving-letter-boxed/cpu-dead-ends.svg "Dead ends are keyed by position, not by the words that led there.")

## Tradeoffs

- **Bounded memory on the GPU.** The device's limits determine the fixed size of the result buffers. If a pass produces more chains than fit, the GPU drops the extras, which could include a chain leading to a solution. The worker therefore reruns the search on the CPU after an overflow. Filtering has kept the counts within the buffers in practice, but the fallback is still needed to avoid losing part of the search.
- **One round trip per pass.** The CPU decides whether another pass is needed and ranks any solutions, so it has to read back the GPU's results after each pass. This involves copying the data into a buffer the CPU can map and waiting on [`mapAsync`](https://developer.mozilla.org/en-US/docs/Web/API/GPUBuffer/mapAsync). Even a small readback has a fixed cost,[^readback] and puzzles requiring more words pay it on each additional pass. Keeping this division of work does leave the shader focused on extending chains, with stopping and ranking handled in TypeScript.
- **Testing every pair.** The CPU uses its first-letter index to consider only words that could follow a chain, whereas the GPU tests every word and rejects most pairs at the first-letter check. That is extra work, but it makes the GPU implementation simpler: every thread starts with the same checks. GPU threads run in groups that execute instructions together, often described as [lockstep](https://en.wikipedia.org/wiki/Single_instruction,_multiple_threads), so differences in their control flow can affect execution. This is a reason to consider the simpler approach, though it does not establish that testing every pair is faster for this solver.
- **Device-dependent speed.** Running locally removes the server, but it makes solve time depend on the user's laptop or phone. A cloud function provides the same server hardware for everyone. The CPU fallback keeps the solver available on devices without WebGPU, while performance still varies between devices.

## Closing Thoughts

When the page froze on hard puzzles in v1, moving the search to a server was the fix I knew how to make. A Web Worker could have kept the search local, but I did not know about it then. Revisiting the solver gave me a reason to reconsider both where the search ran and what it did on each recursive step. Bitmasks removed repeated string searches, while filtering let the solver reject invalid words once rather than rediscovering them on different branches.

WebGPU adds another way to run Find Best, with the CPU search available when GPU support or buffer capacity is insufficient. Although improved coding models helped motivate the revisit, their proposed changes still had to respect the game's rules and the browser's limits. The current solver needs no server to maintain, while its solve time still depends on the user's device.

[^rust]: A [Rust](https://www.rust-lang.org/) version was also started at the end of 2023, but it was abandoned before it did anything useful.

[^support]: [Chrome shipped it in 2023](https://developer.chrome.com/blog/webgpu-release), and Safari and Firefox followed in 2025.

[^reuse]: To make that true, the search lets a chain reuse words, and throws out chains with repeats only when it records a solution.

[^readback]: In Firefox on my machine, that cost is about 100 ms.
