+++
title = "Unix File System"
slug = "unix-file-system"
weight = 4
links = ["https://github.com/aaron-ang/file-system"]
+++

A Unix-style file system built on inodes, implemented in C on top of a virtual disk (8,192 blocks × 4KB, stored as a single file). It lays out a superblock, directory table, inode and data bitmaps, and an inode table, then runs file and directory operations against that on-disk layout.

Tech stack: C.
