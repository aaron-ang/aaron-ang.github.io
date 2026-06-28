+++
title = "Letter Boxed"
slug = "letter-boxed"
weight = 9
links = ["https://aaron-ang.github.io/letter-boxed", "https://github.com/aaron-ang/letter-boxed"]
+++

### Puzzle solver and visualizer for NYT's [Letter Boxed](https://www.nytimes.com/puzzles/letter-boxed)

It started as a Java project for [CS 112](https://www.cs.bu.edu/courses/cs112/) in Spring 2022. That summer I rewrote the recursive backtracking solver in [TypeScript](https://www.typescriptlang.org), built a [React](https://react.dev) frontend to visualize it, and deployed it to GitHub Pages.

The solver did all its work on the client, and heavy puzzles would freeze the UI. I chased that problem for a while: first I moved the algorithm to a Google Cloud Function (which stopped the freezing but added network latency), then rewrote that function in [Go](https://go.dev) to bring latency back down. Eventually I brought the solver home to the client for good — running it in a [Web Worker](https://developer.mozilla.org/en-US/docs/Web/API/Web_Workers_API) so the heavy computation never blocks the interface.

The current version leans into that client-side approach. The solver tracks a puzzle's twelve letters as a 12-bit bitmask, so checking whether a word chain uses every letter is a single bitwise comparison; it precomputes letter adjacency and filters the ~26K-word dictionary down to the few hundred words a given puzzle allows before searching. `Solve` returns the first valid answer, while `Find Best` searches for the optimal one — fewest words, then shortest total length — offloading that heavier combinatorial search to the GPU via [WebGPU](https://developer.mozilla.org/en-US/docs/Web/API/WebGPU_API) compute shaders that extend word chains in parallel across multiple passes, with a CPU fallback. A slider steps through each stage of the solution, and a generator produces random puzzles weighted by English letter frequency.

Tech stack: React, TypeScript, [Tailwind v4](https://tailwindcss.com/), [shadcn/ui](https://ui.shadcn.com/), [Zustand](https://zustand-demo.pmnd.rs/), [Vite](https://vite.dev/), [WebGPU](https://developer.mozilla.org/en-US/docs/Web/API/WebGPU_API) (WGSL), Web Workers, [Bun](https://bun.sh/), [Vitest](https://vitest.dev/).
