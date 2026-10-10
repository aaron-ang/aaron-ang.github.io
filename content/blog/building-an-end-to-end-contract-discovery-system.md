+++
title = "Building an End-to-End Contract Discovery System"
date = "2024-08-02"
slug = "building-an-end-to-end-contract-discovery-system"
tags = ["AI", "Engineering"]
+++

## Background

At Red Hat, the Sales and Deal Management team needed to move **hundreds of thousands** of documents from the legacy [Salesforce CRM](https://www.salesforce.com/crm/) to [Sales Cloud](https://www.salesforce.com/sales/). These contracts supply the history for new deals with existing clients and partners. Reading and migrating them by hand wasn't practical; poor access to that history could delay contract work, raise costs, and put millions in potential revenue at risk.

To address this, we developed **RHContract.AI**, a contract discovery tool that uses large language models (LLMs) to extract key fields from legacy contracts and move them into the new system.

This post covers how the system works, how attribute extraction accuracy rose above 90%, and the lessons learned from building it.

## Product Overview

On a high level, the system processes document data through **three** main stages:

1. Input: ingests a data dump of documents alongside predefined definitions of document types and attributes.
2. Processing: analyzes and categorizes the documents based on the predefined types.
3. Output: generates two key deliverables:
   1. A structured directory of organized document types.
   2. A DataFrame that maps file names to their extracted attributes.

![High-level Architecture of Document Processing Pipeline](/images/building-an-end-to-end-contract-discovery-system/pipeline-architecture.png)

### Key Features

The Minimum Viable Product (MVP) focused on delivering these essential capabilities:

- **Contract Filtering**: Accurately identify and segregate contract documents from non-contract files.
- **Attribute Extraction**: Extract key information as defined by the Red Hat Sales team.
- **Signature Detection**: Determine whether contracts are signed or unsigned.
- **Multilingual Support**: Process contracts in languages other than English.

### Development Approach

We built and checked these features independently. If we found that a document was misclassified, we could fix the filtering stage without affecting the extraction. If the extracted fields were incorrect, we could inspect the input and revise the prompt for that stage. This made bottlenecks easier to locate and gave us something smaller to iterate on.

### Performance Metrics

To evaluate the product’s effectiveness and impact, we tracked the following key performance indicators (KPIs):

- **Filtering Accuracy**: How accurately the system classifies contracts and excludes irrelevant PDFs.
- **Extraction Accuracy**: Correctness of attribute extraction across various contract types.
- **Signature Detection Accuracy**: Reliability in distinguishing between signed and unsigned documents.
- **Time Savings**: Quantified reduction in processing time for sales and partner associates (measured per deal/week/day/month).

## Contract Classification

Before asking a model to extract anything, we needed to find which documents were worth processing. The classification loop was:

1. Identifying distinguishing keywords for each relevant document type.
2. Running keyword searches against the documents.
3. Categorizing documents into folders based on their type.
4. Refining the process by identifying additional keywords from unclassified documents.

Deduplication came first: compare file-content hashes and mark matching files as duplicates.

Most files were PDFs, so [PyMuPDF](https://pymupdf.readthedocs.io/en/latest/) gave us an inexpensive way to extract their text. PDFs without text went into an image category for later processing. We kept revising the keyword rules until the relevant/irrelevant classification reached a steady state.

![Document Type Distribution](/images/building-an-end-to-end-contract-discovery-system/document-type-distribution.png)

After filtering the subset of documents, nearly **50%** were deemed irrelevant, and **over 10%** were identified as duplicates. This resulted in removing more than half of the documents from attribute extraction, a time-consuming process.

Applying Optical Character Recognition (OCR) to image documents and reclassifying them showed that 50% of these documents were relevant. This increased the overall proportion of relevant documents to **30%** of the total unprocessed set.

## Attribute Extraction

Following document classification, the next step was extracting relevant attributes from each document type. Initial experiments with text-based LLMs did not yield promising results. The primary issue was that PDF text extraction **distorted the document's structure**, disrupting the order of words and sentences and causing LLMs to misinterpret the content. To overcome this, we shifted focus to multimodal LLMs, specifically [**InternVL2**](https://huggingface.co/OpenGVLab/InternVL2-26B) by OpenGVLab, an Apache 2.0 licensed model with 26 billion parameters. This approach worked much better. Feeding both the document image and text into the model produced a huge increase in attribute extraction accuracy, often surpassing **90%**, a large improvement over the initial attempts.

The InternVL2 model, particularly its text component InternLM2Chat, showed stronger instruction comprehension than the other LLMs we evaluated. Those models sometimes returned malformed JSON, whereas InternLM2Chat consistently produced well-formed output that we could [parse with Pydantic Models](https://pydantic.dev/docs/validation/dev/concepts/models/). Although inference latency increased slightly due to the larger model size, the trade-off was worth it given the accuracy boost. Working with JSON also simplified later steps, because the output could be easily merged into Pandas DataFrames and exported to CSV or other compatible formats.

## Signature Detection

We also needed to determine whether a contract had been signed by Red Hat, the client, or both. Our initial approach used keyword searches to locate the signature page, then passed its image alongside the first page containing the main attributes to the model. Accuracy was only around 50%, as including both pages strained the model's context window, leading to hallucinated signatures and misinterpretations similar to the observations in earlier attribute-extraction experiments.

To address this issue, we came up with the idea to segment the signature page into two distinct parts: one for the client's signature and another for Red Hat's. The keyword search algorithm was extended to identify relevant coordinates and extract these specific sections as images. These signature snippets were then appended to the original first page, as illustrated in the image below (with redactions for privacy reasons).

![Text + Image → JSON](/images/building-an-end-to-end-contract-discovery-system/text-image-to-json.png)

This approach gave two benefits. Firstly, accuracy improved substantially, with rates increasing to between **85%** and **95%**. Secondly, the inference time improved due to the reduced image size, allowing larger batches to be processed using the same compute.

After making considerable headway, we focused on iterative improvements. The prompts were refined continuously, with the aim of balancing conciseness and specificity. The goal has been to optimize the trade-off between processing speed and accuracy, and that balance decided how well the signature detection worked.

## Lessons Learned

This project marked my first deep dive into working with LLMs, after only tinkering with OpenAI's APIs on a small chatbot project when the LLM development ecosystem was still in its early stages.

The LLM landscape has undergone rapid transformation in recent years. Today, developers have access to dozens of tools for building fully-fledged products on LLMs, with [LangChain](https://www.langchain.com/) serving as a prime example. Despite these upgrades in tooling, some basic ideas still decide whether an LLM project works.

### Importance of Quality Input Data

More context wasn't automatically useful. Whole pages of text and images made the model less reliable. Removing unnecessary text, fixing formatting, and selecting the first and signature pages—where most attributes were—reduced hallucinations and improved both accuracy and inference time.

### Leveraging External Resources

The project's success would not have been possible without the wealth of online resources sharing effective techniques for LLM development. Equally important was maintaining open communication with the stakeholders (internal sales team). These regular interactions gave valuable insights into their pain points and a clearer understanding of business definitions. This knowledge went into focused prompts that provided richer context to the LLM and improved its answers.

### Adopting a Startup Mindset

Building this project from scratch required a startup mentality. This approach meant moving fast and being willing to discard previous work when necessary. While I enjoyed the thrill of rapid progress and quick accomplishments, I also had to come to terms with the fact that past ideas might not always be useful in the next sprint. About half of the previous sprint's work regularly had to be thrown out. For instance, early in the project I put a lot of effort into an experiment comparing performance between various open-source models. When the team pivoted to multimodal LLMs and settled on a single model, that experiment became obsolete. Rather than viewing this as a setback, I recognized it as a necessary step in the project's progress.

### Forward-Thinking Approach

Perhaps the most important lesson was the importance of being forward-thinking and constantly moving towards what would work best for the product. The transition from local computing resources to a more powerful computing architecture in the cloud is a great example. Initial experiments ran locally on [Ollama](https://ollama.com/) using models quantized to 4 bits. However, these experiments soon hit a performance ceiling in terms of speed and accuracy. Recognizing that scaling the product would require more computing power, we asked our mentors for high-performance GPUs. We were fortunate to be granted access to a 2x [A100 GPU](https://www.nvidia.com/en-us/data-center/a100/) cluster in the cloud (shoutout [ROSA](https://www.redhat.com/en/technologies/cloud-computing/openshift/aws)). This upgrade was key in the continued iteration and improvement of the product.
