+++
title = "Tinkering with Spark"
date = "2023-07-19"
slug = "tinkering-with-spark"
tags = ["Data", "Engineering"]
+++

Two Spark settings stopped our daily jobs from crashing with out-of-memory errors and made their stages finish faster: more cores per executor, and more memory for the driver. This post explains how Spark runs on a YARN cluster, how I traced the crashes to broadcast joins, and why those two changes fixed them.

## Background

In the summer of 2022, I interned at [Shopee](https://shopee.com/) as a product analyst on the Search and Recommendation (SnR) data team. My primary responsibility was to deliver reliable and actionable analytics for product managers. Because we frequently ran large-scale queries throughout the day, any job delays or failures directly impacted reporting timelines and slowed progress toward feature improvements or releases.

Our main tools for data processing and querying were [Presto](https://prestodb.io/) and [Apache Spark](https://spark.apache.org/), supplemented by internal tools that abstracted away much of the underlying engineering complexity.

During my time there, noticeable job failures and delays pushed deadlines back unnecessarily. While I will analyze specific causes and fixes in a later section, an important backdrop was the resource shortage across the company at the time. It was incredibly expensive to acquire compute resources, and the demand for compute due to increased workloads far outpaced the supply. This was especially challenging for the SnR team, where ML engineers were running intensive experiments on recommendation models that consumed substantial processing power.

Before we dive into the issue, I will first introduce the key technologies involved—namely Spark and YARN.

## **What is Spark?**

Apache Spark, originally developed as a research project at UC Berkeley's AMPLab and now maintained by the Apache Software Foundation, is an open-source framework for distributed processing of large-scale data. It uses **in-memory caching** and **optimized query execution** to deliver high performance for analytic queries across massive datasets.

Spark was designed to overcome the limitations of MapReduce, which relies on a sequential, multi-step process susceptible to disk I/O latency. With Spark, data is read into memory, operations are performed, and results are written back—all in a streamlined process that avoids repeated disk access. The performance gains come from keeping intermediate data in memory where possible. They also come from abstractions like [Resilient Distributed Datasets (RDDs)](https://spark.apache.org/docs/latest/rdd-programming-guide.html#resilient-distributed-datasets-rdds) and later [DataFrames](https://spark.apache.org/docs/latest/sql-programming-guide.html#datasets-and-dataframes), which let Spark plan and optimize whole pipelines instead of writing to disk between every step.

Today, Spark is widely used for machine learning, real-time analytics, interactive queries, and graph processing, making it a cornerstone of modern data engineering and analytics.

## **What is YARN?**

[YARN](https://hadoop.apache.org/docs/stable/hadoop-yarn/hadoop-yarn-site/YARN.html), short for *Yet Another Resource Negotiator*, is Hadoop’s cluster resource management framework. Fun fact: “Yet Another” is an idiomatic qualifier programmers often use to acknowledge that many systems are incremental variations of existing ones—other examples include Yacc (Yet Another Compiler-Compiler) and YAML (originally Yet Another Markup Language).

Although I will not discuss YARN optimization in detail here, it is important to understand its role. Modern data architectures often run on clusters with thousands of nodes, where a single master that handles both resource allocation and every job's scheduling (like MapReduce's original JobTracker) becomes a bottleneck. YARN addresses this by **separating** resource management from job scheduling and monitoring for each application.

At its core, YARN consists of a **global** ResourceManager (RM) and **per-node** NodeManagers (NMs).

The **ResourceManager** contains two key components:

- **Scheduler** – Allocates resources among competing applications without tracking their execution state.
- **ApplicationsManager** – Accepts job submissions and launches the per-application ApplicationMaster (AM).

The **ApplicationMaster** negotiates resources from the Scheduler, manages task execution, and handles fault tolerance and recovery at the application level in coordination with the NodeManagers.

Here's how these components work together when a client submits a job:

![A client submits a job to the ResourceManager, which launches an ApplicationMaster on a NodeManager. The ApplicationMaster gets containers from the Scheduler and launches tasks in them, while every NodeManager reports its status.](/images/tinkering-with-spark/yarn-architecture.svg "Adapted from [Apache Hadoop YARN](https://hadoop.apache.org/docs/stable/hadoop-yarn/hadoop-yarn-site/YARN.html).")

## **Running Spark on YARN**

Running Spark on YARN allows multiple frameworks (not just Spark) to dynamically share and centrally configure the same cluster resources. YARN’s schedulers handle **categorization**, **isolation**, and **prioritization of workloads**, ensuring resources are efficiently allocated instead of sitting idle. In short, YARN is one of the most widely used cluster managers for running Spark applications at scale.

### Components

Running a Spark application in cluster mode follows the same flow, with a few differences (highlighted in blue):

![The same flow in Spark's cluster mode: the ApplicationMaster also runs the Spark Driver, the granted containers become executors, and the driver sends them tasks, which report results back.](/images/tinkering-with-spark/spark-on-yarn.svg "Blue marks what Spark changes in the YARN flow. Adapted from [Sujith Jay](https://sujithjay.com/spark/with-yarn).")

#### Spark Driver

Each Spark application has a single driver. In cluster mode (shown above), it runs within the ApplicationMaster for the duration of the job; in client mode, it runs on the submitting machine instead. The driver coordinates the entire application lifecycle: it manages job flow, schedules tasks, and translates the program into a directed acyclic graph (DAG) of execution steps across the cluster.

#### Spark Executor

Spark executors run inside YARN containers. Each executor:

- Executes multiple tasks over its lifetime (potentially in parallel).
- Resides on a node, with each node potentially hosting multiple executors.
- Is provisioned with a fixed amount of CPU cores and memory, though the number of executors can grow and shrink at runtime when dynamic allocation is enabled.

#### Cores

Cores represent CPU resources allocated to the driver and executors. Increasing cores per executor can improve parallelism but also increases memory requirements.

#### Memory

Memory allocations are split into two segments: 

1. **On-heap process memory** – for objects, data structures, and operations.
2. **Overhead (non-heap) memory** (`memoryOverhead`) – for JVM overhead, interned strings, native libraries, and other non-heap uses. This is separate from Spark's own setting for off-heap memory (`spark.memory.offHeap.size`).

Thus, the container size requested for a driver or executor is roughly `memory + memoryOverhead` (executors also add `spark.memory.offHeap.size` and `spark.executor.pyspark.memory` if set).

## **The Issue**

From the logs, most failures stemmed from **out-of-memory (OOM)** and **timeout** errors, leading to delays from self-restarts. In some cases, applications experienced prolonged waiting times before executors were allocated, while in others, executors simply took an excessive amount of time to finish each stage—both of which often cascaded into timeouts. My task became **twofold**: (1) eliminate errors and (2) improve processing time.

To investigate, I first examined the default Spark configuration (filtered for relevant parameters):

```bash
spark.dynamicAllocation.initialExecutors=0
spark.dynamicAllocation.minExecutors=0
spark.dynamicAllocation.maxExecutors=100
spark.driver.cores=1
spark.driver.memory=5g
spark.driver.memoryOverhead=2g
spark.driver.maxResultSize=2g
spark.executor.cores=1
spark.executor.instances=10
spark.executor.memory=16g
spark.executor.memoryOverhead=4096M
spark.default.parallelism=400
spark.sql.shuffle.partitions=400
...
```

From this, we can extract the following setup:

- **Driver**: 1 core, 5GiB memory (+2GiB overhead)
- **Executors**: 10 instances, each with 1 core, 16GiB memory (+4GiB overhead)

I reviewed the [Spark configuration documentation](https://spark.apache.org/docs/latest/configuration.html) to understand the other parameters and scoured the Internet for possible causes of the observed errors.

Closer inspection of the logs revealed a clear pattern: **driver OOM errors consistently occurred during broadcast joins**. Further research pointed me to a [reported Spark Issue](https://issues.apache.org/jira/browse/SPARK-17556) describing this exact behavior. In short, before broadcasting, the driver must collect results from executors, and in some cases the returned data exceeded the driver’s working memory (5GiB), causing the OOM crash.

Here's what that failure looks like with the default 5GiB driver:

![With the default 5 GiB of driver memory, the driver collects results from each executor until the data outgrows its memory, and it crashes with an OutOfMemoryError before the broadcast can happen.](/images/tinkering-with-spark/broadcast-oom.svg "Collection runs on the driver, so its memory limits the join no matter how many executors there are.")

## **The Fix**

For processing speed, the solution appeared straightforward: increase the number of cores per executor so each executor could run more tasks concurrently. Most sources suggested a range between **2 and 5**, so I chose **4**, an arbitrary but reasonable middle ground. While this increased memory consumption, the default 16GiB of executor memory handled the added concurrency well in trial runs.

To see why more cores per executor shortens a stage, compare the two setups below:

![The same 8-task stage on one executor with 16 GiB. With one core, tasks run one at a time in 8 waves; with four cores, four run at once and finish in 2 waves.](/images/tinkering-with-spark/executor-cores.svg "The four tasks share one 16 GiB heap, so each gets about a quarter of the executor's memory.")

Next, I addressed driver OOM errors by increasing driver memory. The challenge was determining an appropriate limit. With container memory at 102GiB and memory overhead defaulting to about 10% of the requested memory, the driver could request up to roughly 92GiB. To stay safe, I capped the driver allocation at about half of that, or 48GiB. I began conservatively with **16GiB**, mirroring the executor memory, and found that OOM errors disappeared in subsequent runs—so I retained that value.

With 16GiB, the same broadcast join from earlier now completes:

![With 16 GiB of driver memory, the same data fits, collection finishes, and the driver broadcasts the table to every executor so the broadcast join can run.](/images/tinkering-with-spark/broadcast-fix.svg "Only the driver's memory changed; the executors are the same as before.")

To put these memory numbers into perspective, here they are on a single scale:

![Memory on one scale: the driver grows from 5g plus 2g of overhead to 16g plus 2g, far under the 48 GiB safety cap in a 102 GiB container. The executor keeps 16g plus 4g.](/images/tinkering-with-spark/memory-budget.svg "Each bar is one process; the executor bar stands for each of the ten executors.")

In the end, the effective configuration overrides were: 

```bash
--executor-cores 4
--driver-memory 16g
```

### Other Considerations

I also experimented with other default parameters mentioned earlier. After many iterations with different combinations, I discarded most of them since they did not produce meaningful improvements. For example, adjusting `spark.sql.shuffle.partitions` and `spark.default.parallelism` only benefited a small subset of jobs, as their effectiveness depended heavily on factors like data skew and the number of joins in each query.

Another case was the number of executors (`--num-executors`). In theory, specifying this value would give each job a head start and reduce idle wait times. But with dynamic allocation enabled, it only sets the *initial* number of executors (and our config already set it to 10), so it guarantees nothing afterwards. In practice, the YARN queues were so congested during peak hours that YARN often preempted (killed) executors from ongoing runs to free resources for higher-priority workloads, such as ML experiments.

Finally, in rare situations where increasing driver memory still led to crashes, I found that raising the number of driver cores (e.g., `--driver-cores 4`) mitigated the issue. I cannot fully explain why this worked, but it appeared to stabilize execution in those cases. (Note that `spark.driver.cores` only takes effect in cluster mode.)

### Room for Improvement

Although I followed an iterative cycle of consolidating evidence, forming hypotheses, and implementing fixes, my approach was somewhat messy. In retrospect, I should have been more systematic in documenting changes. For instance, I could have recorded the run times of each Spark job under different configurations in a spreadsheet, making it easier to compare results and refine adjustments. Instead, I applied the same overrides across all jobs in search of a general solution. This proved unproductive, and while I eventually made targeted adjustments to a few jobs, I had to deprioritize the project to focus on more urgent tasks.

## Closing Thoughts

This project was a valuable learning experience. Although fixing Spark pipelines was outside the scope of my responsibilities as a product analyst intern, I saw how much the failing jobs were delaying our reports and proposed fixing them. I’m grateful to my team and manager for granting me the flexibility and trust to pursue this side project. Given my limited understanding of systems at that time, I had to learn everything from scratch. While I achieved tangible results, progress came through continuous iteration and learning from mistakes. I also discovered that there is no one-size-fits-all solution—each Spark job or query is inherently unique, so improvements varied across jobs. Despite the challenges, I thoroughly enjoyed the process. I embraced failure as part of the norm, stayed open to experimentation, and ultimately developed an interest in systems that I pursued further in subsequent college semesters.

## References

[What is Apache Spark? | Introduction to Apache Spark and Analytics | AWS](https://aws.amazon.com/big-data/what-is-spark/)

[Cluster Mode Overview - Spark 3.5.0 Documentation](https://spark.apache.org/docs/latest/cluster-overview.html)

[Apache Hadoop 3.3.6 – Apache Hadoop YARN](https://hadoop.apache.org/docs/current/hadoop-yarn/hadoop-yarn-site/YARN.html)

[Apache Spark Key Terms, Explained](https://www.databricks.com/blog/2016/06/22/apache-spark-key-terms-explained.html)
