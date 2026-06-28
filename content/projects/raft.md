+++
title = "Raft"
slug = "raft"
weight = 2
links = ["https://github.com/aaron-ang/raft-go"]
+++

A complete implementation of the [Raft consensus algorithm](https://raft.github.io/raft.pdf) in Go: leader election with randomized election timeouts and term management, log replication and consistency checks via AppendEntries RPCs, commitment of entries across the cluster, and state persistence for crash recovery.

Tech stack: Go.
