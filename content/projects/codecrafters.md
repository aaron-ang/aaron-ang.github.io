+++
title = "CodeCrafters Builds"
slug = "codecrafters"
weight = 3
+++

Built-from-scratch reimplementations of real systems, following [CodeCrafters](https://codecrafters.io)’ “Build Your Own X” challenges — all in Rust. Each one meant working directly with a system’s wire format, on-disk format, or grammar: parsing protocols, byte layouts, B-trees, and event loops by hand.

The ones I went deepest on:

- [BitTorrent](https://github.com/aaron-ang/bittorrent-rs) — bencode parsing, tracker communication, the peer handshake, and downloading and verifying file pieces over TCP
- [Redis](https://github.com/aaron-ang/redis-rs) — a Redis server speaking the RESP protocol with core commands
- [Shell](https://github.com/aaron-ang/shell-rs) — a POSIX-style shell: a REPL with builtins, external command execution, quoting, redirection, and pipelines
- [Interpreter](https://github.com/aaron-ang/interpreter-rs) — a tree-walk interpreter for the [Lox language](https://craftinginterpreters.com): scanning, parsing to an AST, and evaluation with variables, control flow, and functions

Also built: [Kafka](https://github.com/aaron-ang/kafka-rs), [SQLite](https://github.com/aaron-ang/sqlite-rs), [Git](https://github.com/aaron-ang/git-rs), [DNS server](https://github.com/aaron-ang/dns-server-rs), [HTTP server](https://github.com/aaron-ang/http-server-rs), and [grep](https://github.com/aaron-ang/grep-rs).

Tech stack: Rust.
