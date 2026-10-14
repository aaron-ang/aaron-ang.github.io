+++
title = "Tinkering with Spark"
date = "2023-07-19"
slug = "tinkering-with-spark"
tags = ["Data", "Engineering"]
+++

During my internship at Shopee in summer 2022, we relied on daily Spark jobs to prepare data for our reports. When those jobs failed or took too long, the reporting work had to wait. Fixing the pipelines was outside my responsibilities as a product analyst, but I proposed looking into them because the delays were affecting our work. This post follows that investigation, with some background on Spark and YARN to explain the settings we tried and why their effects varied across jobs.

## Background

In the summer of 2022, I interned at [Shopee](https://shopee.com/) as a product analyst on the Search and Recommendation (SnR) data team. The role's primary responsibility was to deliver reliable and actionable analytics for product managers. Because the team frequently ran large-scale queries throughout the day, any job delays or failures directly impacted reporting timelines and slowed progress toward feature improvements or releases.

The team's main tools for data processing and querying were [Presto](https://prestodb.io/) and [Apache Spark](https://spark.apache.org/), supplemented by internal tools that abstracted away much of the underlying engineering complexity.

An important backdrop was the shortage of compute resources across the company that summer. Acquiring more capacity was expensive, and demand from growing workloads far outpaced the supply. Within SnR, the ML engineers were also running intensive recommendation-model experiments on shared resources. Our queries therefore competed with those experiments. Some delays came from waiting for YARN to allocate executors, while others came from jobs running slowly or crashing after they started. These needed different fixes.

## **What is Spark?**

Apache Spark, originally developed as a research project at UC Berkeley's AMPLab and now maintained by the Apache Software Foundation, is an open-source framework for distributed processing of large-scale data. It uses **in-memory caching** and **optimized query execution** to deliver high performance for analytic queries across massive datasets.

Spark was designed to reduce the repeated disk I/O in MapReduce's multi-step processing. It can read data into memory, apply a sequence of operations, and write the results back, keeping intermediate data in memory where possible. [Resilient Distributed Datasets (RDDs)](https://spark.apache.org/docs/latest/rdd-programming-guide.html#resilient-distributed-datasets-rdds) and later [DataFrames](https://spark.apache.org/docs/latest/sql-programming-guide.html#datasets-and-dataframes) provide ways to express those operations over distributed data, allowing Spark to plan the computation across the cluster without requiring a disk write after every step.

Today, Spark is widely used for machine learning, real-time analytics, interactive queries, and graph processing, making it a cornerstone of modern data engineering and analytics.

## **What is YARN?**

[YARN](https://hadoop.apache.org/docs/stable/hadoop-yarn/hadoop-yarn-site/YARN.html), short for _Yet Another Resource Negotiator_, is Hadoop's cluster resource management framework. Fun fact: programmers often use "Yet Another" to acknowledge that a new system is one of many variations on an existing idea. Other examples include Yacc (Yet Another Compiler-Compiler) and YAML (originally Yet Another Markup Language).

Although this post doesn't cover tuning YARN itself, understanding its role helps explain why Spark jobs could spend so long waiting for resources. In the original MapReduce design, a single JobTracker handled both resource allocation and job scheduling, which could become a bottleneck on a cluster with thousands of nodes. YARN separates cluster-wide resource management from the scheduling and monitoring of individual applications. This distinction matters later, when a Spark configuration change still can't guarantee that a busy cluster will grant the resources requested.

At its core, YARN consists of a **global** ResourceManager (RM) and **per-node** NodeManagers (NMs).

The **ResourceManager** contains two key components:

- **Scheduler** – Allocates resources among competing applications without tracking their execution state.
- **ApplicationsManager** – Accepts job submissions and launches the per-application ApplicationMaster (AM).

The **ApplicationMaster** negotiates resources from the Scheduler, manages task execution, and handles fault tolerance and recovery at the application level in coordination with the NodeManagers.

Here's how these components work together when a client submits a job:

![A client submits a job to the ResourceManager, which launches an ApplicationMaster on a NodeManager. The ApplicationMaster gets containers from the Scheduler and launches tasks in them, while every NodeManager reports its status.](/images/tinkering-with-spark/yarn-architecture.svg "Adapted from [Apache Hadoop YARN](https://hadoop.apache.org/docs/stable/hadoop-yarn/hadoop-yarn-site/YARN.html).")

## **Running Spark on YARN**

On YARN, Spark shares resources with other frameworks. The scheduler can categorize, isolate, and prioritize workloads while managing capacity centrally. That sharing matters when the cluster is busy: requesting an executor doesn't mean we'll get one immediately.

### Components

Running a Spark application in cluster mode follows the same flow, with a few differences (highlighted in blue):

![The same flow in Spark's cluster mode: the ApplicationMaster also runs the Spark Driver, the granted containers become executors, and the driver sends them tasks, which report results back.](/images/tinkering-with-spark/spark-on-yarn.svg "Adapted from [Sujith Jay](https://sujithjay.com/spark/with-yarn).")

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

From the logs, most failures stemmed from **out-of-memory (OOM)** and **timeout** errors, leading to delays from self-restarts. In some cases, applications experienced prolonged waiting times before executors were allocated, while in others, executors simply took an excessive amount of time to finish each stage—both of which often cascaded into timeouts. The task became **twofold**: (1) eliminate errors and (2) improve processing time.

The investigation began with the default Spark configuration (filtered for relevant parameters):

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

This configuration gives the following setup:

{{< table title="Initial Spark resources" >}}
| Role | Instances | Cores each | Memory each | Overhead each |
|---|:---:|:---:|:---:|:---:|
| Driver | 1 | 1 | 5 GiB | 2 GiB |
| Executor | 10 | 1 | 16 GiB | 4 GiB |
{{< /table >}}

The next step was reviewing the [Spark configuration documentation](https://spark.apache.org/docs/latest/configuration.html) to understand the parameters and searching the web for the errors in our logs to find possible causes.

The driver OOMs had a pattern: they happened during broadcast joins. [SPARK-17556](https://issues.apache.org/jira/browse/SPARK-17556) describes the same failure. Before broadcasting, the driver collects results from the executors. The returned data sometimes exceeded its 5GiB of working memory, and the job crashed before the broadcast.

Here's what that failure looks like with the default 5GiB driver:

![With the default 5 GiB of driver memory, the driver collects results from each executor until the data outgrows its memory, and it crashes with an OutOfMemoryError before the broadcast can happen.](/images/tinkering-with-spark/broadcast-oom.svg "Collection runs on the driver, so its memory limits the join no matter how many executors there are.")

## **The Fix**

Once the logs pointed to the driver running out of memory during broadcast joins, increasing its memory allocation was a reasonable next step. The harder choice was how much to request. A 102GiB container limit, allowing about 10% overhead, left room for roughly 92GiB of driver memory. I capped the planned allocation at 48GiB to stay well below that limit, then began with a more conservative **16GiB**, matching the executor memory. The OOM errors disappeared in subsequent runs, so there was no need to raise the allocation further.

With 16GiB, the same broadcast join from earlier now completes:

![With 16 GiB of driver memory, the same data fits, collection finishes, and the driver broadcasts the table to every executor so the broadcast join can run.](/images/tinkering-with-spark/broadcast-fix.svg "Only the driver's memory changed; the executors are the same as before.")

To put these memory numbers into perspective, here they are on a single scale:

![Memory on one scale: the driver grows from 5g plus 2g of overhead to 16g plus 2g, far under the 48 GiB safety cap in a 102 GiB container. The executor keeps 16g plus 4g.](/images/tinkering-with-spark/memory-budget.svg "Each bar is one process; the executor bar stands for each of the ten executors.")

To shorten the slow stages, we increased the number of cores per executor so each could run more tasks concurrently. Most sources suggested between **2 and 5** cores, so we chose **4** as a reasonable middle ground. Running more tasks at once also increased memory consumption, but the default 16GiB per executor accommodated the added concurrency in our trial runs.

To see why more cores per executor shortens a stage, compare the two setups below:

![The same 8-task stage on one executor with 16 GiB. With one core, tasks run one at a time in 8 waves; with four cores, four run at once and finish in 2 waves.](/images/tinkering-with-spark/executor-cores.svg "The four tasks share one 16 GiB heap, so each gets about a quarter of the executor's memory.")

In the end, the effective configuration overrides were:

```bash
--executor-cores 4
--driver-memory 16g
```

### Other Considerations

We also experimented with the other default parameters, trying different combinations over many iterations. Most changes did not produce enough improvement to keep. Adjusting `spark.sql.shuffle.partitions` and `spark.default.parallelism`, for example, benefited only a small subset of jobs, since their effects depended heavily on the data skew and number of joins in each query.

Specifying the number of executors with `--num-executors` to give each job a head start and reduce its wait also seemed promising. With dynamic allocation enabled, however, this only sets the initial count, which was already 10 in our configuration; it doesn't reserve that many executors for the rest of the run. In practice, YARN queues were so congested during peak hours that executors were often preempted from ongoing jobs to free resources for higher-priority workloads, including the ML experiments. Asking for more executors at the start couldn't resolve that competition.

In rare cases, crashes continued even after increasing driver memory. Raising `--driver-cores` to 4 appeared to stabilize those runs, although I still can't fully explain why it helped. (Note that `spark.driver.cores` only takes effect in cluster mode.)

### Room for Improvement

Although I followed an iterative cycle of consolidating evidence, forming hypotheses, and implementing fixes, my approach was somewhat messy. In retrospect, I should have documented the changes more systematically. For instance, a spreadsheet of each Spark job's run times under different configurations would have made it easier to compare results and refine adjustments. Instead, the same overrides were applied across all jobs in search of a general solution. This proved unproductive. I eventually made targeted adjustments to a few jobs but had to deprioritize the project to focus on more urgent tasks.

## Closing Thoughts

Although fixing Spark pipelines was outside the scope of my responsibilities as a product analyst intern, I saw how much the failing jobs were delaying our reports and proposed fixing them. I’m grateful to my team and manager for granting me the flexibility and trust to pursue this side project. Given my limited understanding of systems at that time, I had to learn everything from scratch. The results were tangible, but progress came through continuous iteration and learning from mistakes. I also discovered that no one-size-fits-all solution exists. Each Spark job or query is inherently unique, so improvements varied across jobs. Despite the challenges, I thoroughly enjoyed the process. I embraced failure as part of the norm, stayed open to experimentation, and ultimately developed an interest in systems that I pursued further in subsequent college semesters.

## References

[What is Apache Spark? | Introduction to Apache Spark and Analytics | AWS](https://aws.amazon.com/big-data/what-is-spark/)

[Cluster Mode Overview - Spark 3.5.0 Documentation](https://spark.apache.org/docs/latest/cluster-overview.html)

[Apache Hadoop 3.3.6 – Apache Hadoop YARN](https://hadoop.apache.org/docs/current/hadoop-yarn/hadoop-yarn-site/YARN.html)

[Apache Spark Key Terms, Explained](https://www.databricks.com/blog/2016/06/22/apache-spark-key-terms-explained.html)
