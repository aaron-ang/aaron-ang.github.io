+++
title = "opthash"
slug = "opthash"
weight = 1
links = ["https://github.com/aaron-ang/opthash-rs", "https://crates.io/crates/opthash", "https://pypi.org/project/opthash"]
+++

Rust implementations of **Elastic Hashing** and **Funnel Hashing** from *Optimal Bounds for Open Addressing Without Reordering* (Farach-Colton, Krapivin, Kuszmaul, 2025) — open-addressing hash maps that achieve optimal expected probe complexity without reordering elements after insertion.

Both maps share a single-arena allocation indexed by per-level descriptors, 7-bit fingerprint control bytes, SIMD control-byte scans for occupancy and lookup, and per-level salt re-randomization to decorrelate probe paths across levels. They differ in how they probe within a level. Published to [crates.io](https://crates.io/crates/opthash) and [PyPI](https://pypi.org/project/opthash) (via PyO3 bindings), with CI and continuous benchmarking.

Tech stack: Rust, [PyO3](https://pyo3.rs/), SIMD.
