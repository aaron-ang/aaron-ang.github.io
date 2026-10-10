+++
title = "Building an On-Device Automobile Assistant"
date = "2025-09-05"
slug = "building-an-on-device-automobile-assistant"
tags = ["AI", "Engineering"]
+++

A voice assistant in a car should answer in real time, keep the driver's data private, and work without a network. A cloud model adds network delays to every reply and sends the driver's data out of the car. The alternative is _on-device inference_: every model runs on the vehicle's own compute. The models then have to share the car's memory, which averaged [16 GB of DRAM](https://assets.micron.com/adobe/assets/urn:aaid:aem:414b850a-ec3b-43b1-aef7-3b600446ccf2/original/as/automotive-megatrends-white-paper.pdf) per car in 2025.

The automotive industry is entering a new era, which Arm calls the [AI-defined vehicle](https://newsroom.arm.com/blog/what-are-ai-defined-vehicles). In Arm's vision, AI sits at the core of automotive compute and lets the vehicle sense, reason, and adapt in real time to the driver and the environment. Platforms like [Zena CSS](https://www.arm.com/products/automotive/compute-subsystems/zena) make powerful and secure AI processing possible directly inside the vehicle. Local processing also helps safety, convenience, and personalization.

This post describes an on-device assistant we built toward Arm's vision. A supervisor routes each spoken or visual request to small agents for guardrails, vehicle control, retrieval, and vision. The main lesson is that giving each component a narrow job helped in two ways. First, our internal evaluations indicate that a supervisor with specialized agents scales better and gives more consistent results than one large agent. Second, routing every non-vision request to a small language model, together with 4-bit quantization, made real-time inference possible on constrained hardware.

The post follows one request, _"Pair my phone to the car's Bluetooth"_, through the system. It first describes the agents, the tools they call, and why a supervisor beats one large agent. It then traces that request from speech to reply and shows the logs and traces that record each run. Next it explains how two quantized models fit in memory and respond in real time, and how the assistant's capabilities grow in four levels. It ends with the safety constraints on each agent, what deployment still requires, and the research directions that come after.

## System Architecture

An _agent_ is a software component responsible for one capability. Each agent processes its own inputs and outputs and communicates with other agents through defined interfaces. The assistant has agents for guardrails, vehicle control, retrieval, and vision. The _supervisor_ is the coordinating agent: it interprets the user's intent and system context, then dispatches the request to the right agent or tool.

The figure below shows how these components connect:

![High-level architecture of the agentic automobile assistant.](/images/building-an-on-device-automobile-assistant/architecture.png)

The components are the following:

- **Input Module.** It converts spoken queries to text with [whisper.cpp](https://github.com/ggml-org/whisper.cpp) and forwards the text to the Guardrail Agent. It also pulls images from the camera at a configurable frequency for the Vision Agent.
- **Guardrail Agent.** It validates user input and rejects unsafe or malicious instructions, such as attempts to access the system prompt or override safety-critical controls.
- **Supervisor Agent.** It receives the validated input and decides which agent or tool to invoke. Its interface scales because it keeps a registry of the available tools and agents, each described in one consistent format. New functionality can then be plugged in easily, without changes to the Supervisor's core logic.
- **Specialized agents.** The Vehicle Control Agent handles the Bluetooth request. The Vision Agent uses a Vision-Language Model (VLM) to analyze camera feeds, for example to check whether the driver follows traffic rules.
- **Output Queue.** It sits between the agents and the Output Module, which drives speech synthesis and the user interface. Because it delivers responses in the correct order, the driver never hears or sees replies out of order.

### Tools

Agents act on the vehicle and its supporting systems through a set of local tools. The tools are exposed through a [Model Context Protocol (MCP)](https://modelcontextprotocol.io/docs/getting-started/intro) server or as direct function calls. MCP is an open standard that describes each tool's name, inputs, and outputs in one format, and any model can discover and call a tool through it. In the current prototype, the tools return mock data, but the design anticipates integration with real automotive subsystems. The tools fall into two groups:

- **Driving and Safety**
  - Contact emergency services in the event of a crash or detected hazard.
  - Configure vehicle environment settings such as climate control, sunroof, and cabin lighting.
  - Send notifications and alerts to the driver.
- **Personal Assistance**
  - Retrieve vehicle information from a local vector store (via RAG).
  - Control infotainment features, such as Bluetooth connections and media playback.

Exposing these capabilities as tools keeps reasoning in the agents and actuation in the tools. That separation makes the system extensible and eases integration with new automotive features.

The retrieval tool uses Retrieval-Augmented Generation (RAG). A [_vector store_](https://www.pinecone.io/learn/vector-database/) is a database of embeddings, which are numeric representations of text or images, so that passages with similar meaning can be found quickly. RAG retrieves the passages relevant to a question from the vector store and gives them to the language model as context. RAG improves accuracy: by embedding a knowledge base like the car manual, the assistant can answer vehicle-specific technical questions quickly and privately, without internet access. Adding RAG transformed the assistant's responsiveness and reliability. Offline answers of this kind greatly improve its usefulness in real time and the driver's trust.

### Designing for Scalability

The evaluation compared two common designs for agentic systems:

1. A **monolithic** architecture, where one large agent manages all tasks.
2. A **modular** architecture, where a supervisor agent acts as a router and facade for specialized sub-agents.

Our internal evaluations indicate that the modular design scales more effectively and gives more consistent results. Each component can be updated or extended without disrupting the rest of the system, much like replacing a car part without rebuilding the entire engine. The internal results are consistent with benchmarks from LangChain. Those benchmarks show that multi-agent systems built around a supervisor maintain performance as the number of tools grows. In the same benchmarks, single-agent systems degrade rapidly when they are overloaded with context or capabilities. In the chart below, read each line from left to right: as distractor domains are added, the single-agent line drops sharply, while the supervisor line stays nearly flat.

![Agentic architecture scalability—source: [LangChain](https://blog.langchain.com/benchmarking-multi-agent-architectures/).](/images/building-an-on-device-automobile-assistant/multi-agent-scalability.png)

Overall, the architecture supports multimodal interaction, operates reliably without external connectivity, and can be extended with new agents, tools, or capabilities with minimal changes to existing components.

## Example: Connecting Bluetooth

When the driver says _"Pair my phone to the car's Bluetooth"_, the components above handle it in this order:

1. The **Input Module** transcribes the driver's speech into text.
2. The **Guardrail Agent** checks that the request is safe and within scope.
3. The **Supervisor Agent** interprets the intent as an environment control task and routes it to the Vehicle Control Agent.
4. The **Vehicle Control Agent** calls the Automobile MCP tool, which pairs the driver's device, and outputs the result.
5. The **Output Queue** buffers the result and delivers it in order, without interruption from concurrent tasks.
6. The **Output Module** sends the response to the text-to-speech (TTS) client and the UI.

The assistant then confirms: _"Your device has been paired successfully."_

The logs below capture how the system executes this request in real time, one entry per step. Look for three events in order: the voice transcription, the handoff from the Supervisor to the Vehicle Control Agent, and the tool call. Excluding the time needed to connect the Bluetooth device, the agent workflow completes in **under five seconds**.

![Agent logs showing voice transcription, agent handoff, and tool calling.](/images/building-an-on-device-automobile-assistant/agent-logs.png)

Beyond logs, the system uses [MLflow](https://github.com/mlflow/mlflow) tracing, which records each agent's execution time, its tool usage, and the handoffs between agents. Visibility into time, tools, and handoffs accelerates debugging, performance tuning, and design improvements as the system matures. MLflow traces give this breakdown for every request:

![MLflow traces provide fine-grained visibility per request.](/images/building-an-on-device-automobile-assistant/mlflow-traces.png)

## Model and Hardware Configuration

The system keeps two models in memory:

- A large VLM ([InternVL3-Instruct 14B](https://huggingface.co/unsloth/InternVL3-14B-Instruct-GGUF)) for image-text reasoning in the Vision Agent.
- A smaller large language model, or LLM ([Jan-nano 4B](https://huggingface.co/unsloth/Jan-nano-GGUF)), for all other text tasks, including retrieval and tool orchestration.

_Quantization_ stores model weights with fewer bits, for example 4 bits instead of 32. It reduces memory use and speeds up inference, which lets large models run on hardware with limited resources. Both models are **quantized to 4-bit** and served with [llama.cpp](https://github.com/ggml-org/llama.cpp). The quantization uses [Unsloth's Dynamic 2.0 GGUF](https://docs.unsloth.ai/basics/unsloth-dynamic-2.0-ggufs) format, which offered the best trade-off between model size and runtime performance among the leading quantization methods. With it, both models fit **within 14 GB of VRAM**.

The development machine was an [Amazon EC2 g5g.4xlarge](https://aws.amazon.com/ec2/instance-types/g5g/) instance. It has 16 Arm-based Graviton2 vCPUs, 32 GB of RAM, and a T4G Tensor Core GPU with 16 GB of VRAM. Most of that compute and memory is dedicated to ML workloads, which matches the constraints of real vehicles.

The instance's 16 GB of VRAM matches the average car's 16 GB of DRAM in 2025, and that amount is [projected to triple by 2026](https://www.micron.com/about/blog/applications/automotive/new-research-shows-cars-need-more-memory-than-a-rocket). The match makes the development budget mirror the budget available to AI agents in a production car. Graviton2 also supports Arm's [SOAFEE framework](https://aws.amazon.com/blogs/industries/building-an-automotive-embedded-linux-image-for-edge-using-arm-graviton-yocto-project-soafee). With SOAFEE, containerized automotive workloads can be developed and tested in the cloud before they are deployed to in-vehicle systems.

## Real-Time Inference

Running AI models on edge hardware is challenging because compute and memory budgets are tight. The VLM gives strong multimodal reasoning but has higher latency. For that reason, only the Vision Agent uses the VLM, and all non-vision tasks go to the LLM. The Bluetooth request never needs the camera, and both the Supervisor and the Vehicle Control Agent handle it on Jan-nano 4B. Together with 4-bit quantization, this division of labor balances functionality against responsiveness and enables real-time multimodal inference for automotive workloads on resource-constrained devices.

The system prompt, which every conversation reuses as its prefix, gives one more way to reduce latency. A language model keeps the attention keys and values it computes for each token in a _KV cache_, and a later prompt with the same prefix can reuse them. The assistant precomputes the KV cache for the system prompt. The Bluetooth request therefore starts with its system prompt already processed. The chart below compares end-to-end latency with and without this warmup:

![Impact of KV cache warmup on end-to-end latency.](/images/building-an-on-device-automobile-assistant/kv-cache-warmup-latency.png)

In ablation tests, which compare runs with and without the warmup, precomputing the cache delivered an average **2x speedup** in end-to-end latency. In other words, the warmup roughly halved the average end-to-end latency.

## Progressive Capabilities

Development proceeded in stages, and each stage added new perception and action capabilities. The four levels below move from information retrieval to autonomous action that depends on context.

### Level 1: Inspect Vehicle Status

At the base level, the assistant can retrieve the vehicle's real-time state from onboard sensors in response to spoken queries, such as _"What is the current cabin temperature?"_. The driver gets vehicle information immediately and without using their hands.

### Level 2: Modify Vehicle Status

Building on retrieval, the assistant can execute driver commands that adjust vehicle settings, such as _"Set the temperature to 70 degrees"_. The assistant changes from an information source to an active participant in operating the vehicle.

### Level 3: Visual Driver Alerts

With vision integrated, the assistant begins to understand the driving environment through visual input in real time. Integrating a vision component was pivotal. It changed the assistant from a listener to an observer that could understand visual cues inside and outside the vehicle, such as passenger safety or lane conditions. That understanding enables more intuitive and proactive behavior. At this level, the assistant interprets context in complex, changing real-world situations instead of only following instructions.

In one scenario, the assistant can detect when the vehicle is in a High Occupancy Vehicle (HOV) lane and issue a warning if the occupancy requirement is not met. In a synthetic test scene, the exterior camera shows an HOV lane sign:

![Synthetic highway view: an HOV 2+ sign with vehicles detected ahead](/images/building-an-on-device-automobile-assistant/hov-exterior-highway.jpg)

The cabin camera shows the driver, detected as a person:

![Synthetic cabin view: the driver detected as a person](/images/building-an-on-device-automobile-assistant/hov-interior-cabin.jpg)

The Vision Agent combines both views and flags the violation:

![The Vision Agent reads synthetic interior and exterior views of the car and flags an HOV-lane violation.](/images/building-an-on-device-automobile-assistant/vision-hov-violation.png)

### Level 4: Visual-Informed Action

At the top level, the assistant combines deep visual understanding with autonomous decisions to deliver critical safety interventions. If it detects a crash, the assistant can contact emergency services autonomously, and help is dispatched even if the driver cannot respond. The assistant moves from passive monitoring to active response: it perceives and interprets its environment, and it also acts to keep the driver safe.

In a synthetic crash scene, the exterior camera shows a crashed vehicle:

![Synthetic highway view: a crashed vehicle detected](/images/building-an-on-device-automobile-assistant/emergency-exterior-crash.jpg)

The cabin camera shows a driver who does not respond:

![Synthetic cabin view: an unresponsive driver](/images/building-an-on-device-automobile-assistant/emergency-interior-cabin.jpg)

From the interior and exterior views, the assistant detects an emergency and contacts emergency services:

![The assistant detects an emergency based on the car’s synthetic interior and exterior point of view, and contacts emergency services.](/images/building-an-on-device-automobile-assistant/vision-emergency-alert.png)

Across the four levels, the assistant progresses from simple sensor queries to autonomous behavior that depends on context. Each level brings in-vehicle assistants closer to perceiving, understanding, and acting within the vehicle, toward fully AI-defined mobility.

## Constraining Agent Capabilities

The architecture enforces strict constraints on agent behavior for safety and reliability. Each agent can access only the minimal set of tools it requires, following the principle of least privilege.

The Guardrail Agent provides containment. It screens every input with rule-based checks and blocks unsafe or out-of-scope commands before they reach sensitive tools. In the Bluetooth trace, this screening is step 2, which validated the request as safe and within scope. Robustness testing against malicious and adversarial inputs uses frameworks like [DeepEval](https://github.com/confident-ai/deepeval). The testing reduces the risk that a sophisticated input manipulation could trigger unintended agent behavior.

## Deployment Priorities

Moving this assistant from prototype to real-world deployment requires balancing four interdependent priorities: **model intelligence**, **latency**, **memory usage**, and **safety**.

Model intelligence on edge hardware depends on progress in post-training techniques. [Pruning](https://arxiv.org/pdf/2204.09656) removes weights that contribute little, and [knowledge distillation](https://huggingface.co/blog/Kseniase/kd) trains a small model to copy a large one. Together with quantization, these techniques let compact models keep strong performance despite their limited parameter count.

Lower latency depends on hardware architectures that address memory-bound inference. For example, placing compute closer to memory can reduce the cost of moving data. Lower memory usage also needs hardware-aware strategies: [quantization-aware training](https://pytorch.org/blog/quantization-aware-training/), efficient attention implementations, and lightweight model architectures. Together, they ensure that models can operate within strict VRAM budgets while remaining functional.

Safety matters most, and it must be built into every layer. [Arm's IP supports ISO/SAE 21434 compliance](https://community.arm.com/arm-community-blogs/b/automotive-blog/posts/arm-meets-iso-sae-21434-standard), which positions this system well for automotive cybersecurity standards. Deployment at scale still requires alignment with industry validation practices and thorough adversarial testing to meet technical and regulatory requirements. Continued optimization, security risk engineering, and collaboration across the industry will be vital for putting safe, capable, real-time AI assistants into future vehicles.

## Looking Ahead

This prototype is only the beginning, and two frontiers come next for on-device assistants.

**Proactive, context-aware reasoning.** [World models](https://deepmind.google/discover/blog/genie-3-a-new-frontier-for-world-models/) are AI systems that learn and simulate real-world dynamics. With them, future assistants can not only respond to human inputs and predefined triggers but also anticipate scenarios, plan actions, and adapt to long-term consequences. Training these models in rich virtual environments will allow them to handle complex driving conditions safely and reliably before they reach the road.

**Personalized, continual learning.** Future assistants will adapt continuously to each driver. [QLoRA](https://wandb.ai/sauravmaheshkar/QLoRA/reports/What-is-QLoRA---Vmlldzo2MTI2OTc5) is one efficient fine-tuning technique: it freezes a 4-bit model and trains only a few small added weight matrices. Techniques like QLoRA could let models adapt over time to each user's preferences, driving style, and vehicle vocabulary, which would make interactions feel more natural and tailored to each individual.

## Closing Thoughts

The assistant shows that intelligent, collaborative, and extensible in-vehicle AI is within reach, with privacy-first models running directly in the car. As stated at the start, giving each component a narrow job helped in two ways. Our internal evaluations indicate that a supervisor with specialized agents scales better than one large agent. Routing non-vision work to the small LLM and quantizing both models made real-time replies possible. We have only begun to explore what such assistants can do. Each advance in software and hardware brings cars closer to being intelligent and intuitive partners on every journey, not only a mode of transport.

_Unlike my first post, I wrote this one with help from LLMs. The ideas are mine; the LLMs helped with wording and structure, and I edited their output so it would not read like generic generated text._
