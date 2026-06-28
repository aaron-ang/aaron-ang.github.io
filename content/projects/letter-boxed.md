+++
title = "Letter Boxed"
slug = "letter-boxed"
weight = 9
links = ["https://github.com/aaron-ang/letter-boxed"]
+++

### Puzzle solver and visualizer for NYT’s [Letter Boxed](https://www.nytimes.com/puzzles/letter-boxed)

This was initially a Java project for [CS 112: Introduction to Computer Science II](https://www.cs.bu.edu/courses/cs112/) taken in Spring 2022. In Summer 2022, I rewrote the recursive backtracking algorithm in [TypeScript](https://www.typescriptlang.org), used [React](https://react.dev) to implement a frontend visualizer, and deployed the app to [GitHub Pages](https://pages.github.com).

Since the client was doing all the heavy lifting, some randomly generated puzzles caused the app to freeze. A few optimizations were made over time to mitigate this issue that are documented in the repository’s README.

In Summer 2023, I migrated the algorithm source code to a [Node.js Google Cloud HTTP Function](https://cloud.google.com/functions/docs/concepts/nodejs-runtime) and directed the client app to call that endpoint. Even though this change prevented client unresponsiveness, latency significantly increased.

In light of this, I rewrote the algorithm in [Go](https://go.dev) and created a new GCF endpoint. Migrating to Go significantly reduced latency such that it was comparable to that in the original thick client version.

Tech stack: React, Typescript, [Material UI](https://mui.com/material-ui/), Node.js, Google Cloud, Golang.
