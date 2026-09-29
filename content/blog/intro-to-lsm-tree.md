+++
title = "Intro to LSM Tree"
date = "2023-08-31"
slug = "intro-to-lsm-tree"
tags = ["Design", "Research"]
+++

## Introduction

**Log-Structured Merge-Tree (LSM-Tree)** is an **append-only, key-value** data structure that provides **high write throughput**. Write throughput is the amount of data that can be written to a storage system over a period of time. LSM Tree is used by various databases including [Apache Cassandra](https://cassandra.apache.org/_/index.html), [LevelDB](https://dbdb.io/db/leveldb), and [RocksDB](https://rocksdb.org) just to name a few.

On a high level, LSM appends all incoming data then uses merge sort to handle deduplication and deletions. Underlying LSM are a few in-memory and on-disk data structures which will be explained in the following sections.

## **Sorted String Table** (SSTable)

SSTable is a **disk-based** data structure consisting of a **sorted**, **immutable** sequence of **key-value pairs**. The sortedness allows for efficient data retrieval using algorithms like binary search.

Initially, incoming writes are buffered in a **sorted**, **in-memory** data structure called a [Memtable](https://github.com/facebook/rocksdb/wiki/MemTable). Once the Memtable reaches a configurable threshold, it is flushed to disk to become a new SSTable. Transactions are also appended to a Commit log or Write Ahead Log (WAL) file to recover from a crash and ensure durability. As more SSTables are added and a certain threshold is reached, the SSTables are **merged** via **compaction** to form a larger SSTable. Note that **updates** to a given key will just append the new key value. Older entries will eventually be removed during compaction.

Here's how a handful of writes flow through this path, from the WAL and Memtable all the way to SSTables and compaction:

![Writes go to the WAL on disk and to the sorted Memtable, which flushes as an immutable SSTable; the update put(b,3) adds a new entry beside the old b=1. Compaction later merges three SSTables and drops stale entries.](/images/intro-to-lsm-tree/write-path.svg "Adapted from [ScyllaDB](https://www.scylladb.com/glossary/sstable/).")

## Compaction

Compaction can be thought of as garbage collection for the LSM tree. It removes keys that are duplicated and/or marked for deletion. Under the hood, it utilizes a [multiway merge algorithm](https://www.baeldung.com/cs/2-way-vs-k-way-merge#k-way-merge-algorithms) to merge multiple SSTables into a new SSTable.

In most implementations, a dedicated background thread is used to perform compaction. There are many ways to customize how and when compaction gets triggered with different tradeoffs. Below are two classic compaction strategies.

### Leveled Compaction

- Each level is a sorted run consisting of multiple SSTables
- When the run of **level $i$ is full**, it is merged into the run of level **$i+1$**
- Good read performance and lower space amplification since no duplicate keys are present in each level

### Size-Tiered Compaction

- Organizes SSTables into sorted runs based on their size
- Every level must accumulate $T$ runs before they are sort-merged
- When the number of **runs $\geq T$**, the whole level is merged into a single **larger sorted run** in the next tier
- Good ingestion performance since runs are lazily merged

### Tiered vs Leveled Compaction

To see where each strategy spends its merging effort, below is the same three flushes fed into a leveled tree and a tiered tree side by side:

![Three flushes with size ratio T = 3. The leveled tree merges each flush into L1's single run and rewrites L2 when L1 fills; the tiered tree stacks runs in L1 and merges all three into a new L2 run.](/images/intro-to-lsm-tree/leveled-vs-tiered.svg "Adapted from [RocksDB](https://github.com/facebook/rocksdb/wiki/Leveled-Compaction), [Alibaba Cloud](https://www.alibabacloud.com/blog/an-in-depth-discussion-on-the-lsm-compaction-mechanism_596780), and [*Monkey: Optimal Navigable Key-Value Store*](https://nivdayan.github.io/monkeykeyvaluestore.pdf).")

### Partial Compaction

Leveled compaction can lead to cascading compactions, which results in high latency spikes, write stalls, and overall unpredictable system performance. Partial compaction aims to mitigate this by executing compaction with **file-level granularity**. The compaction condition/trigger remains unchanged. However, the compaction routine selects a subset of files from the current and next level with overlapping key ranges to merge. By breaking down compaction into smaller units, the cost is amortized, leading to more predictable and consistent system performance.

A comparison of full and partial compaction (starting from the same full level) is shown below:

![From the same full level Lᵢ, full compaction merges every file in Lᵢ and Lᵢ₊₁, rewriting 9 files; partial compaction merges one file, g–m, with the two overlapping files below, rewriting 3.](/images/intro-to-lsm-tree/partial-compaction.svg "Adapted from [Compactionary](https://disc-projects.bu.edu/compactionary/background.html).")

The table below shows the compaction granularity used by several production storage engines. Leveled engines typically compact at file granularity, while tiered engines merge whole sorted runs.

{{< table title="Compaction granularity by engine" caption="Adapted from [*Constructing and Analyzing the LSM Compaction Design Space*](https://vldb.org/pvldb/vol14/p2216-sarkar.pdf)." >}}
| Engine | Data layout | Level | Sorted run | File (single) | File (multiple) |
| --- | --- | :-: | :-: | :-: | :-: |
| RocksDB | Leveling | | | ✓ | ✓ |
|  | Tiering | | ✓ | | |
| LevelDB | Leveling | | | ✓ | |
| Cassandra | Tiering | | ✓ | | |
|  | Leveling | | | ✓ | ✓ |
| ScyllaDB | Tiering | | ✓ | | |
|  | Leveling | | | ✓ | ✓ |
| HBase | Tiering | | ✓ | | |
| WiredTiger | Leveling | ✓ | | | |
{{< /table >}}

#### Data Movement Policy

![Source: [*Constructing and Analyzing the LSM Compaction Design Space*](https://vldb.org/pvldb/vol14/p2216-sarkar.pdf)](/images/intro-to-lsm-tree/data-movement-policy.png)

When executing partial compaction, we need to decide which data files to compact. Here are several policies (non-exhaustive), each with its own strengths:

1. Round robin (LevelDB's approach)
2. Least overlap with the next level (improve write amplification)
3. Coldest file (improve read throughput)
4. File with most tombstones (improve space amplification)

The table below shows which policies several production storage engines use. Engines that compact an entire level or sorted run at once have no file to choose.

{{< table title="Data movement policy by engine" caption="Adapted from [*Constructing and Analyzing the LSM Compaction Design Space*](https://vldb.org/pvldb/vol14/p2216-sarkar.pdf). Least overlap combines the paper's least overlap with the next level (+1) and the level after (+2)." >}}
| Engine | Data layout | Round robin | Least overlap | Coldest file | Oldest file | Tombstone density | Expired TTL | Entire level |
| --- | --- | :-: | :-: | :-: | :-: | :-: | :-: | :-: |
| RocksDB | Leveling | | ✓ | ✓ | ✓ | ✓ | | |
|  | Tiering | | | | | | | ✓ |
| LevelDB | Leveling | ✓ | ✓ | | | | | |
| Cassandra | Tiering | | | | | | | ✓ |
|  | Leveling | | ✓ | | | ✓ | ✓ | |
| ScyllaDB | Tiering | | | | | | | ✓ |
|  | Leveling | | ✓ | | | ✓ | ✓ | |
| HBase | Tiering | | | | | | | ✓ |
| WiredTiger | Leveling | | | | | | | ✓ |
{{< /table >}}

### Compaction Triggers

The two main compaction strategies discussed so far are *leveled* compaction, which is triggered when a level is saturated, and *tiered* compaction, which is triggered when the number of sorted runs exceeds a threshold $T$. However, modern storage engines support additional compaction triggers. For instance, RocksDB's [Universal Compaction](https://github.com/facebook/rocksdb/wiki/Universal-Compaction) estimates the tree's overall space amplification and triggers compaction when it exceeds a threshold. Alternatively, compaction could be invoked based on the age of a file, i.e., how long it has existed in a particular level. Age-based triggers already exist in production engines, and delete-aware variants based on tombstone age are an active area of research (more details in the last section). The table below summarizes the compaction triggers used by several production storage engines.

{{< table title="Compaction triggers by engine" caption="Adapted from [*Constructing and Analyzing the LSM Compaction Design Space*](https://vldb.org/pvldb/vol14/p2216-sarkar.pdf), which also covers research storage engines." >}}
| Engine | Data layout | Level saturation | # Sorted runs | File staleness | Space amp. | Tombstone TTL |
| --- | --- | :-: | :-: | :-: | :-: | :-: |
| RocksDB | Leveling | ✓ | | ✓ | | |
|  | Tiering | | ✓ | | ✓ | ✓ |
| LevelDB | Leveling | ✓ | | | | |
| Cassandra | Tiering | | ✓ | ✓ | | ✓ |
|  | Leveling | ✓ | | | | ✓ |
| ScyllaDB | Tiering | | ✓ | ✓ | | ✓ |
|  | Leveling | ✓ | | | | ✓ |
| HBase | Tiering | | ✓ | | | |
| WiredTiger | Leveling | ✓ | | | | |
{{< /table >}}

## Reading data

All reads in an LSM Tree are first served from the Memtable. If the key is not found, it is then looked up in SSTables by level until the key is found. Otherwise, a **null** value is returned.

Since a key may live in any of several sorted runs, a read may need to probe multiple SSTables, leading to extraneous I/Os. Below are two main strategies to **optimize read performance**.

### Bloom Filter

A Bloom filter is a **probabilistic** data structure that provides an efficient way to verify that an entry is **certainly not** in a set. A detailed explanation of how bloom filters work under the hood can be found [here](https://www.educative.io/answers/what-is-a-bloom-filter). Essentially, bloom filters are kept in memory and checked before touching disk to reduce the amount of (expensive) disk reads. The SSTable will only be searched if the bloom filter indicates that the key **may be present**. Note that standard bloom filters only help point queries, i.e., getting the value of a specific key, and cannot determine the presence of a key range. Variants exist for these cases: RocksDB's [prefix Bloom filters](https://github.com/facebook/rocksdb/wiki/Prefix-Seek) speed up scans over keys sharing a prefix, and range filters like [SuRF](https://www.cs.cmu.edu/~huanche1/publications/surf_paper.pdf) can answer "is any key in this range present?"

### Sparse Index / Fence Pointers

As the size of deeper levels increases, even using binary search to find a key can become expensive, since each step of a binary search on disk costs an I/O. To put this into perspective, RocksDB reports that close to **90%** of storage data resides in the last level ([Dong et al., 2017](https://www.cidrdb.org/cidr2017/papers/p82-dong-cidr17.pdf)). To mitigate the potentially large read cost, a **subset of keys** in each SSTable is **mapped in-memory**. With this key mapping, ranges can be quickly skipped, narrowing the search space and significantly reducing lookup time.

The sparse index, containing the key mapping, is typically encoded at the end of the file or as a separate index file. When an SSTable is read, its sparse index is loaded into memory and subsequently used for key lookups during read operations. Since SSTables are immutable, compaction writes fresh sparse indexes for the new SSTables it produces.

Putting the two together, below are two lookups: one key that costs a single disk read, and one that the bloom filters rule out without ever touching disk:

![get(42) skips L0 on its Bloom filter; L1's filter says maybe, and the fence pointers pick one block, so it costs one disk read. get(57) is ruled out by every filter and never touches disk.](/images/intro-to-lsm-tree/read-path.svg "Everything left of the dashed line lives in memory, so only the SSTable blocks cost a disk read.")

### Block Cache

The block cache keeps recently read data blocks in memory and, optionally, metadata blocks such as bloom filters and fence pointers. When the block cache is initially empty, it fetches the necessary pages from the on-disk files. It may also prefetch data pages to improve read performance. During read operations, the block cache is consulted first; if the requested block is not found in the cache, disk I/O is performed. Because compaction deletes its input SSTables, their cached pages become useless, so reads may miss the cache until it warms up again. Separately, production systems like RocksDB keep a *Manifest File*, a log of which SSTables live at each level and their key ranges, as shown below.

![Source: [*Optimizing Space Amplification in RocksDB*](https://www.semanticscholar.org/paper/Optimizing-Space-Amplification-in-RocksDB-Dong-Callaghan/9b90568faad1fd394737b79503571b7f5f0b2f4b)](/images/intro-to-lsm-tree/block-cache-manifest.png)

## Deletion

Deletes in LSM-trees are realized by **inserting** a special type of key-value entry, known as a **tombstone**. Once inserted, a tombstone logically invalidates all entries in a tree that have a matching key, without necessarily disturbing the physical target data entries. The target entries are only guaranteed to be **persistently** deleted from the data store once the corresponding tombstone reaches the **last level** of the tree through **compactions**.

Here's the journey of a single delete, from the Memtable down to the last level:

![delete(7) puts a tombstone in the Memtable, so get(7) returns null while the old value stays in L3. Compactions carry the tombstone down level by level until it meets the old entry and both are deleted.](/images/intro-to-lsm-tree/tombstone.svg "Adapted from [Lethe](https://disc-projects.bu.edu/lethe/).")

## Research @ [DiSC Lab](https://disc.bu.edu)

At Boston University’s Data-intensive Systems and Computing (DiSC) Lab, research is conducted on all aspects of log-structured systems to optimize performance. The main performance metrics and tradeoffs are listed below.

### Read Amplification

Read amplification refers to the number of **disk reads per query**. As discussed earlier, read optimizations such as bloom filters and fence pointers significantly reduce read amplification by selectively processing read requests or reducing the search space.

### Write Amplification

Write amplification refers to the **ratio** of the amount of physical data **written to the storage device** to the amount of logical data **written to the database**. Different compaction strategies result in different write amplification. In general, write amplification increases as the frequency of compactions increases.

### Space Amplification

Space amplification refers to the **ratio** of the amount of physical data stored on the **storage device** to the amount of logical data in the **database**. Similar to write amplification, the compaction strategy employed significantly impacts space amplification. Size-tiered compaction generally results in **higher space amplification** compared to leveled compaction. In size-tiered compaction, when an SSTable in the deepest tier becomes very large, compaction requires substantial temporary space, since the input SSTables can only be deleted after the new, larger SSTable is fully written. Moreover, overwritten or deleted keys persist in the SSTable until it is eventually merged, leading to wasted space.

### Tradeoffs

There are often trade-offs between different amplification metrics. For instance, introducing additional metadata to optimize read amplification can lead to increased space amplification. Compression techniques, employed by most storage solutions, help mitigate space amplification. Ultimately, the acceptable trade-offs depend on the system's use case. Write-heavy workloads might prioritize minimizing write amplification at the expense of higher read and space amplification. Conversely, read-intensive workloads may favor optimizing read amplification over the other metrics.

### What I’m working on

At the DiSC Lab, I’m working on extending [MySQL](https://github.com/mysql/mysql-server) to incorporate application support for a novel LSM delete engine called [Lethe](https://disc-projects.bu.edu/lethe/). Lethe provides persistence guarantees for primary delete operations. A write-up of the motivations of the project and current progress can be found [here](https://docs.google.com/document/d/1B6eS_YCTRvrcCuAtHlK42Kctx354K5_YqQoqAWimAV4/edit?usp=sharing). I will also provide a concise summary below.

We previously discussed that deletions in LSM are “lazily” materialized, meaning the “deleted” key is physically removed from the system only during compactions. Furthermore, a tombstone might need to reach the last level of the LSM tree for the associated key to be physically removed, requiring compactions through every level. As the size of the tree grows, compaction might be delayed, and the process itself could be time-consuming. This introduces a significant challenge, as the duration between a deletion request from the client and the actual physical key deletion could extend to days or even months. Such a delay poses a considerable privacy risk for companies, particularly those committed to specific turnaround times for personal data removal (e.g., 30 days). In cases where company data is compromised, the persistence of user data beyond the stipulated period could result in legal complications for these organizations.

The envisioned long-term outcome of this project is to advocate for integrating the new SQL syntax into [ANSI](https://blog.ansi.org/sql-standard-iso-iec-9075-2023-ansi-x3-135/#gref) standards, **establishing persistent deletes as a foundational capability in SQL**. This paradigm shift aims to compel Database Management Systems (DBMS) vendors to natively implement persistent delete functionality. We hope to empower not only database engineers but also SQL users with greater control over their data lifecycles. This increased agency will enable them to meet Service Level Agreements (SLAs) and address various business requirements more effectively, particularly those related to data privacy and protection.

We are living in an era where emphasis on data security and privacy is paramount. Stringent regulations such as the [GDPR](https://gdpr.eu/) and [CCPA](https://oag.ca.gov/privacy/ccpa) exist to minimize our data exposure by delineating strict guidelines for managing user data. Modern data systems must align with these legal frameworks, which are designed to safeguard our online privacy rights. I look forward to the successful realization of this project in light of these considerations.

## References

[What is a SSTable? Definition & FAQs | ScyllaDB](https://www.scylladb.com/glossary/sstable)

[An In-depth Discussion on the LSM Compaction Mechanism](https://www.alibabacloud.com/blog/an-in-depth-discussion-on-the-lsm-compaction-mechanism_596780)

[B-Tree vs LSM-Tree](https://tikv.org/deep-dive/key-value-engine/b-tree-vs-lsm/)

[Lethe: Enabling Efficient Deletes in LSMs](https://disc-projects.bu.edu/lethe/)

[SIGMOD 2022: Dissecting, Designing, and Optimizing LSM-based Data Stores (Tutorial)](https://www.youtube.com/watch?v=Al3krW4Sh3Q)
