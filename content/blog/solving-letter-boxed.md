+++
title = "Letter Boxed: Lessons from Optimizing Search"
date = "2026-04-19"
slug = "solving-letter-boxed"
tags = ["Design", "Engineering"]
+++

[Letter Boxed](https://www.nytimes.com/puzzles/letter-boxed) is one of the New York Times' (NYT) daily word games. The solver in this post builds words one letter at a time and checks whether its words cover all twelve letters millions of times. Its first web version ran that search inside the page, and hard puzzles froze the page until the search finished.

In the puzzle, twelve letters sit around the sides of a square, three per side. You make words by drawing a path between letters, with a few rules:

- Words must be at least three letters long.
- Two letters in a row cannot come from the same side of the square.
- Each new word starts with the last letter of the previous one.
- You win when every letter has been used. NYT suggests a target, usually four or five words, and fewer is better.

A solution traces a path across the square, one letter at a time. Most of this post uses the puzzle in the figure below as its example puzzle. Its sides are MSO, HGD, RIE and NAF:

![FORMED is drawn letter by letter, alternating sides of the square. DASHING starts from its final D and covers the remaining six letters, solving the puzzle in two words.](/images/solving-letter-boxed/puzzle-rules.svg "Solid orange lines trace FORMED; dashed blue lines trace DASHING.")

I first wrote a solver in 2022 as a Java assignment for [CS 112](https://www.cs.bu.edu/courses/cs112/). You can try the [current version](https://aaron-ang.github.io/letter-boxed) in your browser. Since 2022, the same algorithm has moved four times:

1. It was rewritten in [TypeScript](https://www.typescriptlang.org/) (JavaScript with types) to make it work in a browser.
2. It moved to a [Node.js](https://nodejs.org/) _cloud function_ (code that runs on a provider's servers on request).
3. [Go](https://go.dev) replaced Node.js in the cloud function.
4. It moved back to local compute in the browser, now with help from a GPU.

Each move traded one problem for another. Most of the gain, however, came from shrinking the work, not from where the solver ran. Two changes that do not depend on where the code runs did the shrinking: representing letters as bits, and doing as much work as possible before the search begins. Together, they made the last move back to the browser possible.

The post starts with the backtracking search, which has barely changed since 2022, and then follows the solver to the cloud and back. After that, two sections cover the bitmasks and the precomputation that shrank the work. The last sections show how the more expensive of the app's two search modes runs on the GPU, how a CPU fallback skips dead ends, and what running locally costs.

## The First Solver: Backtracking

The core algorithm has changed little since the assignment. It is _recursive [backtracking](https://en.wikipedia.org/wiki/Backtracking)_: build a solution one letter at a time, and whenever the partial answer breaks a rule, undo the last letter and try the next option.

At each step, the solver considers adding each of the 12 letters to the current word, and immediately rejects the letter if:

- it is on the same side as the previous letter,
- the current word plus that letter is not the start of any dictionary word, or
- it would finish a word that has already been used.

It can also end the current word, if it is a real word of three or more letters, and start the next word with the same letter.

The "start of any dictionary word" test does most of the pruning. The dictionary is loaded into a map where every [prefix](https://en.wikipedia.org/wiki/Substring#Prefix) of every word is a key: "M", "MO", "MOR" and so on map to "prefix only", while complete words like "MORPH" map to "full word". If "MRX" is not in the map, no word starts that way, and the solver never explores anything beneath it.

On top of this, the solver uses [_iterative deepening_](https://en.wikipedia.org/wiki/Iterative_deepening_depth-first_search). It first looks for a one-word solution, then two words, then three, up to five. As a result, the first solution it finds is also one of the shortest in words. The app calls this **Solve**.

After a solve, the button turns into **Find Best**. Among solutions with the fewest words, it finds the one with the fewest total letters. For the example puzzle, Solve might return GARNISHED DEFORMED, while Find Best returns FORMED DASHING. Find Best only looks for solutions with as many words as Solve's answer, and this post calls that number its _word limit_. Find Best is much more expensive than Solve: instead of stopping at the first solution, it has to rule out every other one.

## Where the Solver Runs

Solve's backtracking stayed the same, but where it ran changed four times. The animation below follows the solver from the browser's main thread to the cloud and back to a [_Web Worker_](https://developer.mozilla.org/en-US/docs/Web/API/Web_Workers_API), a background thread in the page:

![Three setups: in 2022 the solver blocks the browser's main thread. In 2023 a cloud function solves it over the network. In 2026 a Web Worker solves it locally, with WebGPU for Find Best.](/images/solving-letter-boxed/architecture-evolution.svg "The tabs replay each year's setup in turn; the final frame shows 2026, with a one-line summary of the earlier years.")

The table below lists every version, with what each move fixed and what it cost:

{{< table title="Where the solver has run" caption="Dates from 2022 onward are from the repository's commit history, but the Java version predates it." >}}
| When | Where it runs | What it fixed | What it cost |
|---|---|---|---|
| Spring 2022 | Java, command line | — | No interface |
| 2022 | TypeScript in the browser's main thread | A visual, shareable app | Hard puzzles froze the page |
| May 2023 | Node.js in Google Cloud Functions | No more freezing | Network latency, a server to maintain |
| June 2023 | Go in Google Cloud Functions | Lower latency, gzipped responses | Still a network round trip |
| April 2026 | TypeScript in a Web Worker, plus WebGPU | No network, no freezing | Depends on the user's device |
{{< /table >}}

### On the Main Thread

The first web version ran the solver directly in the page's JavaScript, alongside every button click and repaint. JavaScript in a browser tab runs on one [main thread](https://developer.mozilla.org/en-US/docs/Glossary/Main_thread). While the solver was searching, nothing else could happen: the page could not repaint, and buttons stopped responding until the search finished.

### In the Cloud

The fix in 2023 was to move the solver to a [Google Cloud Function](https://cloud.google.com/functions). The browser sends the puzzle's letters, the server solves it, and the answer comes back. The project's README at the time put the tradeoff this way:

> Significant reduction in client RAM and CPU load in exchange for slightly increased response time.

Moving to the server fixed the freezing completely, since the browser was only waiting on a network request. In exchange, every solve paid for a network round trip. It also paid for a _[cold start](https://docs.cloud.google.com/functions/1stgendocs/concepts/execution-environment#cold-starts)_ whenever the function had not run recently and the cloud provider had to start a fresh instance.

A month later, the function was rewritten in Go, which typically starts up and runs CPU-heavy code faster than Node.js. The Go version also [gzipped](https://en.wikipedia.org/wiki/Gzip) its responses to cut transfer time.[^rust]

### Back in the Browser

By 2026, running the solver locally looked attractive again, for three reasons:

1. **Web Workers.** The page sends a Web Worker a message, and the worker runs the solver and posts a message back. The main thread never blocks, which fixes the original freezing without a server.
2. **WebGPU.** Browsers can now run general-purpose programs on the GPU through [WebGPU](https://developer.mozilla.org/en-US/docs/Web/API/WebGPU_API).[^support]
3. **A faster solver.** Before the move back, a rewrite of the core data structures made the work itself much smaller, which is what the next two sections cover.

Running the solver locally removes the network round trip, the cold start, and the server bill. The cost is that solve speed now depends on the user's device. Devices without WebGPU fall back to a CPU search, which a later section describes.

## Bitmasks

The old Go solver stored letters as strings and answered every question by searching them. To test whether two letters sat on the same side, it went through the four sides and searched each side's string for both letters. To test whether a solution used all twelve letters, it took each letter in turn and searched every word for it:

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

This code is correct, but the solver asks these questions millions of times. A puzzle has exactly twelve letters, and a 32-bit integer has room for all of them. Give each letter an index from 0 to 11 in side order. For the example puzzle, M is bit 0 and F is bit 11. Then represent "which letters does this word use?" as a number where bit $i$ is set if letter $i$ appears. That number is called a [_bitmask_](https://en.wikipedia.org/wiki/Mask_%28computing%29).

Combining the masks of FORMED and DASHING shows which letters the two words use together:

![Each of the twelve letters gets one bit. FORMED and DASHING each set their letters' bits, and OR-ing the two masks sets all twelve.](/images/solving-letter-boxed/bitmask-cover.svg "D appears in both words but still sets just one bit.")

Once every word has a mask, "do these words use every letter?" becomes a single comparison:

```ts
(a.coverageMask | b.coverageMask) === allCoveredMask; // 0xFFF
```

A second precomputed table, `sideOf[letterIndex]`, handles sides. With it, "are these two letters on the same side?" is two array lookups and a comparison instead of eight string searches.

Adding a letter to a word's mask only takes an [OR](https://en.wikipedia.org/wiki/Bitwise_operation#OR) of its bit, but removing a letter during backtracking is harder. If the word is "SEES" and you remove the last S, the S bit must stay set, because another S is still there. A mask records _whether_ a letter appears, not _how many times_. For that reason, the solver rebuilds a word's mask from its remaining letters whenever it backtracks, which costs little for words this short.

## Precomputation

The second idea is _[precomputation](https://en.wikipedia.org/wiki/Precomputation)_: anything that does not depend on the search should be computed once, before the search starts.

The largest saving comes from the dictionary: the full word list has 26,648 words, but most of them cannot be used in any given puzzle. A word containing a letter that is not on the square can never appear in a solution. Neither can a word where two consecutive letters come from the same side. Filtering those out, along with words shorter than three letters, leaves a much smaller list.

For the example puzzle, the three filters reduce the dictionary as follows:

![The dictionary passes three filters: GO is too short, SNOW uses a letter not on the square, and GRID puts two same-side letters next to each other. FORMED survives.](/images/solving-letter-boxed/dictionary-filter.svg "The counts are for this puzzle; each puzzle keeps its own few hundred words.")

Only about 1 to 2 in every 100 words survive. In three tested puzzles, 255, 565, and 580 words survive, and the example puzzle is the one that keeps 565. Find Best searches only those _valid words_.

For each valid word, Find Best works out up front the facts the search keeps asking about: which letters the word uses (as a bitmask), and which letters it starts and ends with. It stores them alongside the word:

```ts
interface ValidWord {
  word: string; // "FORMED"
  coverageMask: number; // which puzzle letters it covers, one bit each
  firstLetterIdx: number; // which first-letter list it belongs to
  lastLetterIdx: number; // which list the next word must come from
}
```

A few more precomputations follow the same pattern:

- **Letter lookups** are small arrays, built once per puzzle, that give each letter's position in the puzzle, its bit, and its side. Answering any of those questions is a single array read instead of a string search.
- **An index by first letter** keeps one list per puzzle letter (twelve in all), holding the valid words that start with it. When the next word must start with `D`, the solver reads `D`'s list and only considers those words.
- **A per-puzzle cache** keeps the list of valid words, and pressing Find Best after Solve reuses it instead of filtering again.

## Find Best on the GPU

Once the valid words and their masks are precomputed, Find Best can think in whole words instead of letters. A candidate solution is a _chain_ of words, and extending a chain means appending a word whose first letter matches the chain's last letter.

Extending chains suits a GPU. A GPU is best at running the same small program thousands of times in parallel, each copy on different data, with no copy needing to talk to another. Checking whether one word extends one chain is exactly that kind of task.

### Parallelizing Search

A _[compute shader](https://en.wikipedia.org/wiki/Compute_kernel)_ is a small program the GPU runs once per thread. The WebGPU version launches one thread of it for every (chain, word) pair, all in one batch called a _dispatch_. In the first pass, each chain is a single word. The example puzzle has 565 valid words, which gives 565 × 565, or about 320,000 threads. Each thread asks three questions:

1. Does this word start with the chain's last letter? If not, stop.
2. Is this word already in the chain? If so, stop.
3. OR the masks together. If the result covers all twelve letters, it is a solution. Otherwise, if the chain is still below the word limit, it is a candidate for the next pass.

In a single pass, each thread stops early, records a solution, records a chain for the next pass, or drops a partial chain that has reached the word limit.

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

Threads also need a safe way to write their results. Thousands of them may find a solution at the same moment, and they cannot all write to the same spot. Each one calls [`atomicAdd`](https://www.w3.org/TR/WGSL/#atomic-rmw) on a shared counter, an [atomic operation](https://en.wikipedia.org/wiki/Atomic_operation) that hands out slot numbers one at a time. This guarantees that no two threads get the same slot, and each thread then writes its chain into its own slot. All four of the example puzzle's two-word solutions fit in a small slice of the grid of threads:

![A grid of GPU threads, one per chain and candidate word. Mismatched first letters return at once; the rest OR their masks, recording full covers as solutions and dropping partial ones at the word limit.](/images/solving-letter-boxed/gpu-chain-extend.svg "The ellipses stand for the rest of this puzzle's 565 words, as both chains and candidates. All four of its solutions are in the drawn cells.")

### One Pass per Word Count

The CPU drives the search, one word count, or _level_, at a time. First it checks for a one-word solution itself. Then it dispatches the shader, which runs one thread per (chain, word) pair, and waits for that pass to finish. The first pass extends one-word chains to two words, the next extends those to three, and so on.

The chains themselves never leave the GPU. Each pass writes its new chains into a _buffer_, a block of GPU memory, that the next pass reads as its input. After each pass, the two buffers swap roles. The CPU then _reads back_ only the two counters, plus any solutions, copying them from GPU memory in a single step.

With those counts, the CPU decides what happens next. If the pass found no solutions, it dispatches again, until it reaches the word limit or runs out of chains. If the pass found any, the search stops, since those solutions have the fewest words.

The CPU, not the GPU, picks the winner. It scans the solutions once, keeping the one with the fewest total letters and breaking ties alphabetically. One scan over the results is cheap for the CPU, and doing it on the GPU would add complexity for little benefit.

The example puzzle needs only one pass, because its word limit is two. A second puzzle, with sides DCE, AUB, HMW and RNF, has a word limit of three, and its first solutions only appear on the second pass:

![The CPU uploads the words and one-word chains once. Each pass reads one chains buffer and writes the other. Pass 1 finds nothing, pass 2 finds eight, and the CPU picks CHAMBER REFUND DAWN.](/images/solving-letter-boxed/gpu-passes.svg "Keeping chains on the GPU avoids copying thousands of them back and forth after every pass.")

## CPU Fallback

When a browser or device does not support WebGPU, or when a pass produces more chains than the GPU's buffers can hold, the worker runs the same search on the CPU. It uses the same valid words, masks, and first-letter index. Like the GPU version, it goes one level at a time: all one-word chains, then all two-word chains, and so on up to the word limit. It stops at the first level with a solution.

The CPU version also remembers dead ends, a form of [memoization](https://en.wikipedia.org/wiki/Memoization). A partial chain's future depends only on three things: its last letter, the letters it has covered so far, and how many words it has left.[^reuse] Together, those three make up the chain's _position_.

Once the search learns that a position is a dead end, it skips every later chain in the same position, however that chain got there. For example, if no single word can finish a chain that ends in E and covers C, E, A, M, and R, any other chain with that ending and those letters is skipped too.

The number of possible positions is small:

- 12 possible last letters, one for each letter on the square,
- 4,096 possible sets of covered letters, since each of the 12 letters is either covered or not ($2^{12}$),
- one to four words left, with the first of up to five words already placed.

Multiplying these together gives at most 196,608 positions. The solver stores one byte per position, so the whole table takes under 300 KB. Even a puzzle with no solution in five words is ruled out in a few milliseconds.

The animation below uses the second puzzle, with sides DCE, AUB, HMW and RNF. It shows one dead end being recorded and then reused:

![At level 3, ARC → CAME ends in E with five letters covered and one word left. None of the 7 E-words completes it. The position is marked dead, and when CREAM → MARE later reaches it, the search skips it.](/images/solving-letter-boxed/cpu-dead-ends.svg "Dead ends are keyed by position, not by the words that led there.")

## Tradeoffs

The local architecture comes with four tradeoffs:

- **Bounded memory on the GPU.** Result buffers have a fixed size, derived from the device's limits. If a pass produces more chains than fit, the GPU drops the extras and the worker re-runs the search on the CPU. In practice, filtering keeps the counts well within bounds, but it is a real limit.
- **One round trip per pass.** Every pass ends with a readback, because the CPU decides whether to stop and ranks the solutions. A readback copies the data into a buffer the CPU can map, then waits on [`mapAsync`](https://developer.mozilla.org/en-US/docs/Web/API/GPUBuffer/mapAsync). Each readback has a fixed cost, however little it carries.[^readback] A puzzle that needs more words pays that cost once for every extra pass. In exchange, the shader only extends chains, and the stopping and ranking logic stays in ordinary TypeScript.
- **Brute force over branching.** The GPU tests every chain against every word, even though most pairs fail the first-letter check. The CPU version uses the first-letter index to skip them. On a GPU, simple and uniform work usually runs faster than branching code, because threads in a group run in [lockstep](https://en.wikipedia.org/wiki/Single_instruction,_multiple_threads), executing the same instruction together. The brute-force version is also far easier to get right.
- **Device-dependent speed.** A cloud function runs on the same hardware for everyone, while a local solver is only as fast as the user's laptop or phone. The CPU fallback makes sure devices without WebGPU still get an answer.

## Closing Thoughts

Looking back, most of the gain came from shrinking the work rather than from where the solver ran. The cloud function did not solve puzzles any faster: it only kept the page responsive while the user waited on the network. Running in the browser again only worked because bitmasks and filtering had shrunk the work enough for the user's own device.

Where the solver ran depended as much on what I knew as on what browsers offered. In 2022, it ran on the main thread and froze the page. In 2023, I reached for a server because I did not know Web Workers existed. By 2026, I did, and WebGPU had arrived to let Find Best run on the GPU.

[^rust]: A [Rust](https://www.rust-lang.org/) version was also started at the end of 2023, but it was abandoned before it did anything useful.

[^support]: [Chrome shipped it in 2023](https://developer.chrome.com/blog/webgpu-release), and Safari and Firefox followed in 2025.

[^reuse]: To make that true, the search lets a chain reuse words, and throws out chains with repeats only when it records a solution.

[^readback]: In Firefox on my machine, that cost is about 100 ms.
