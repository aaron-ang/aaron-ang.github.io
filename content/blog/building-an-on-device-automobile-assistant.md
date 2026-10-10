+++
title = "Building an On-Device Automobile Assistant"
date = "2025-09-05"
slug = "building-an-on-device-automobile-assistant"
tags = ["AI", "Engineering"]
+++

Arm’s vision of the [AI-defined vehicle](https://newsroom.arm.com/blog/what-are-ai-defined-vehicles) involves bringing AI into how a car understands its surroundings and responds to the driver. Platforms such as [Zena CSS](https://www.arm.com/products/automotive/compute-subsystems/zena) are intended to support that processing within the vehicle. During my internship at Arm, we explored this direction by building a prototype assistant that could answer questions about the car, use tools for vehicle controls, and interpret camera images. The aim was to show how cooperating agents could make an in-vehicle assistant more useful: answering vehicle-specific questions, helping with everyday controls, and responding to information from the car’s surroundings, with the models running locally.

We wanted these capabilities to run locally, so the assistant could work without an internet connection and keep the driver’s data within the vehicle. Running every model on-device, however, meant balancing accuracy and response time against limited compute and memory. Adding more capabilities would make that balance harder to maintain, so we also needed a design that could scale without requiring every part of the assistant to change.

Much of the work came down to deciding how to divide those responsibilities. Should one agent handle every request, or should several agents specialize? Would they share the whole conversation, or receive only the context needed for a task? These choices affected accuracy and response time as well as how easily we could add features. Through experiments, we worked toward a design that could bring these capabilities together within a limited memory budget, while leaving room to extend the assistant.

## Dividing the Work Between Agents

We considered two designs: one agent responsible for all the assistant’s capabilities, and a supervisor that routes requests to specialized agents. Our internal evaluations favored the supervisor design for consistency as capabilities grew. It also gave us a way to update the vehicle-control, retrieval, or vision behavior separately, rather than changing one agent responsible for all three.

Here, an _agent_ is a component with a particular responsibility, its own inputs and outputs, and access to a defined set of tools. Splitting the assistant into agents does not require a separate model for each one. Several agents can use the same language model while working with different instructions, tools, and context.

We chose to have agents receive only the context supplied by the supervisor, rather than share the entire conversation history. Each handoff therefore includes the assigned task and the context the supervisor has selected for it.

LangChain’s [multi-agent benchmarks](https://blog.langchain.com/benchmarking-multi-agent-architectures/) examine a related question: how does performance change when an agent has more domains to consider? In the chart below, the single-agent design loses accuracy as distractor domains are added, while the supervisor’s performance stays comparatively stable. Those results provide context for our choice, although they are a separate benchmark from our evaluations of the automobile assistant.

![Agentic architecture scalability—source: [LangChain](https://blog.langchain.com/benchmarking-multi-agent-architectures/).](/images/building-an-on-device-automobile-assistant/multi-agent-scalability.png "The supervisor maintains performance as distractor domains are added, while the single-agent design declines. Source: [LangChain](https://blog.langchain.com/benchmarking-multi-agent-architectures/).")

## From a Spoken Request to a Tool Call

With the responsibilities divided, the supervisor needs a way to identify the agent or tool for each request. We keep a registry whose entries describe the available agents and tools in a consistent format. Adding a capability means supplying another entry and its implementation, while keeping the same routing interface.

The architecture includes the speech and camera inputs, the agents that handle them, and the output path back to the driver:

![High-level architecture of the agentic automobile assistant.](/images/building-an-on-device-automobile-assistant/architecture.png "The supervisor routes each request to a specialized agent, while the output queue coordinates delivery of their replies.")

Consider the request _“Pair my phone to the car’s Bluetooth.”_ It passes through the system as follows:

1. The **Input Module** transcribes the driver’s speech with [whisper.cpp](https://github.com/ggml-org/whisper.cpp) and forwards the text to the Guardrail Agent.
2. The **Guardrail Agent** checks the request before it reaches the supervisor. Its checks are intended to reject unsafe or out-of-scope instructions, including attempts to reveal the system prompt or override safety-critical controls.
3. The **Supervisor Agent** identifies a vehicle-control request and hands it, along with the selected context, to the Vehicle Control Agent.
4. The **Vehicle Control Agent** calls the Bluetooth pairing tool and returns its result.
5. The result waits in the **Output Queue** for delivery to the **Output Module**, which sends the reply to the text-to-speech (TTS) client and the user interface. The queue coordinates replies when several tasks run concurrently.

Our prototype’s tools return mock data, so the confirmation _“Your device has been paired successfully”_ reflects the tool’s response rather than a connection to a real phone. The logs show the transcription, handoff, and tool call completing in **under five seconds**. That measurement excludes the time a real Bluetooth connection would take.

![Agent logs showing voice transcription, agent handoff, and tool calling.](/images/building-an-on-device-automobile-assistant/agent-logs.png "The pairing trace follows transcription, agent handoff, and a mock tool call; it does not measure a real Bluetooth connection.")

Logs tell us which steps ran, but investigating a slow response also requires knowing where the time went. We use [MLflow](https://github.com/mlflow/mlflow) traces to record each agent’s execution time, tool calls, and handoffs. That breakdown lets us examine whether a delay occurred within an agent, during a tool call, or while handing work to another agent:

![MLflow traces provide fine-grained visibility per request.](/images/building-an-on-device-automobile-assistant/mlflow-traces.png "Timing individual agents, tool calls, and handoffs helps locate delays within a request.")

### Tools and Vehicle Information

The agents access local tools through a [Model Context Protocol (MCP)](https://modelcontextprotocol.io/docs/getting-started/intro) server or direct function calls. MCP describes each tool’s name, inputs, and outputs in a standard format. For Bluetooth pairing, the agent chooses the tool and supplies its arguments; the tool implementation is responsible for the operation. Connecting those implementations to actual vehicle subsystems remains future work.

The tool interfaces cover several kinds of requests:

- **Vehicle settings and infotainment:** climate control, sunroof, cabin lighting, Bluetooth connections, and media playback.
- **Alerts and emergency assistance:** notifying the driver or requesting help after a detected hazard.
- **Vehicle information:** retrieving relevant passages from a local copy of the car manual.

For questions about the car, we use Retrieval-Augmented Generation (RAG). We embedded passages from the manual in a local [_vector store_](https://www.pinecone.io/learn/vector-database/), which stores numeric representations that support searches for passages with similar meaning. When the driver asks a question, the retrieval tool finds relevant passages and supplies them to the model as context. This gives the model vehicle-specific information to work from while keeping the manual lookup on-device. For the driver, that means being able to ask about the car in ordinary language and get an answer grounded in its manual, even without an internet connection.

## Choosing Models Within the Memory Budget

The agent architecture determines who handles a request, but we still had to choose which models would do the inference. Some of the models we tried hallucinated less but responded more slowly; others used less memory at the cost of accuracy. We wanted to keep VRAM usage down without giving up the accuracy needed for the assistant’s tasks, so the largest model we could fit was not automatically the best choice for every agent.

Our experiments led to two models:

- **[InternVL3-Instruct 14B](https://huggingface.co/unsloth/InternVL3-14B-Instruct-GGUF)** handles image-text reasoning for the Vision Agent. It can work with camera images, but its higher latency makes it less suitable for requests that do not need vision.
- **[Jan-nano 4B](https://huggingface.co/unsloth/Jan-nano-GGUF)** handles the remaining text tasks, including retrieval and tool orchestration. Both the Supervisor and Vehicle Control Agent use this smaller model for the Bluetooth request.

To keep both models resident, we used _quantization_, which stores model weights with fewer bits. Both models use **4-bit** quantization in [Unsloth’s Dynamic 2.0 GGUF](https://docs.unsloth.ai/basics/unsloth-dynamic-2.0-ggufs) format and are served with [llama.cpp](https://github.com/ggml-org/llama.cpp). In this configuration, they fit together **within 14 GB of VRAM**. The model split lets us reserve the larger model for camera-based tasks while avoiding its inference cost on ordinary text requests.

We developed the assistant on an [Amazon EC2 g5g.4xlarge](https://aws.amazon.com/ec2/instance-types/g5g/) instance with 16 Arm-based Graviton2 vCPUs, 32 GB of RAM, and a T4G Tensor Core GPU with 16 GB of VRAM. That provided a concrete memory limit for our experiments. It does not establish that the same configuration will fit a production vehicle, where the available memory and other workloads depend on the target system.

Graviton2 also supports development with Arm’s [SOAFEE framework](https://aws.amazon.com/blogs/industries/building-an-automotive-embedded-linux-image-for-edge-using-arm-graviton-yocto-project-soafee). SOAFEE supports developing and testing containerized automotive workloads in the cloud before deploying them to in-vehicle systems.

## Reusing the System Prompt’s KV Cache

Choosing a smaller model for text requests reduced the work needed for those requests, but the model was still processing the same system prompt at the start of each conversation. Since that prefix does not change, we could process it ahead of time and reuse the result.

A language model’s _KV cache_ holds the attention keys and values computed for earlier tokens. We precompute those values for the system prompt and reuse them when a request begins with the same prefix. For the Bluetooth request, the model can then start from the cached prefix instead of processing the system prompt again.

We compared end-to-end latency with and without this warmup:

![Impact of KV cache warmup on end-to-end latency.](/images/building-an-on-device-automobile-assistant/kv-cache-warmup-latency.png "Reusing the system prompt’s KV cache roughly halves average end-to-end latency in these runs.")

In these runs, warmup roughly halved average end-to-end latency, an average **2× speedup**. The cache avoids repeating the prefix computation; the model still has to process the new request and generate its response.

## Adding Camera Input and Actions

We developed the capabilities in four stages. The first two use spoken requests to inspect and modify vehicle state. The next two introduce camera input, first for alerts and then for actions triggered by visual observations. The vehicle tools remain mocked, and the camera examples below use synthetic scenes. Together, the stages illustrate how an assistant can grow from answering the driver’s questions to using observations of its surroundings to offer help without a spoken prompt.

### Reading and Changing Vehicle State

At the first level, a driver can ask _“What is the current cabin temperature?”_ and the assistant retrieves the value through a tool. At the second, _“Set the temperature to 70 degrees”_ calls a tool to change the setting. Both follow the routing and tool-calling path described above, but one retrieves information while the other requests a change.

### Combining Camera Views for an Alert

Camera input allows the assistant to consider information beyond the spoken request. The Input Module supplies images at a configurable frequency, and the Vision Agent interprets them with the larger model. We tested combining interior and exterior views to identify a possible High Occupancy Vehicle (HOV) lane violation.

In the synthetic scene, the exterior camera shows an HOV 2+ sign, providing the occupancy requirement:

![Synthetic highway view: an HOV 2+ sign with vehicles detected ahead](/images/building-an-on-device-automobile-assistant/hov-exterior-highway.jpg "The HOV 2+ sign supplies the occupancy requirement for this synthetic test.")

The cabin view shows only the driver, giving the assistant an occupant count to compare with that requirement:

![Synthetic cabin view: the driver detected as a person](/images/building-an-on-device-automobile-assistant/hov-interior-cabin.jpg "The cabin view supplies the person count to compare with the HOV requirement.")

Combining the two views lets the Vision Agent flag the mismatch:

![The Vision Agent reads synthetic interior and exterior views of the car and flags an HOV-lane violation.](/images/building-an-on-device-automobile-assistant/vision-hov-violation.png "Combining the lane requirement with the cabin occupancy produces an HOV alert in the synthetic scenario.")

### Triggering an Action From Camera Input

The fourth level adds a tool call after interpreting the images. We tested a synthetic emergency scenario in which the exterior view shows a crashed vehicle:

![Synthetic highway view: a crashed vehicle detected](/images/building-an-on-device-automobile-assistant/emergency-exterior-crash.jpg "The exterior view supplies evidence of a crash in this synthetic emergency scenario.")

The interior view depicts an unresponsive driver:

![Synthetic cabin view: an unresponsive driver](/images/building-an-on-device-automobile-assistant/emergency-interior-cabin.jpg "The synthetic scenario pairs the crash with a driver who cannot respond, testing action without a spoken request.")

The assistant interprets the pair as an emergency and calls the emergency-service tool without waiting for a spoken request:

![The assistant detects an emergency based on the car’s synthetic interior and exterior point of view, and contacts emergency services.](/images/building-an-on-device-automobile-assistant/vision-emergency-alert.png "The synthetic emergency scenario triggers an emergency-service tool call; the prototype’s tools still return mock data.")

Because the tool returns mock data, this demonstrates the path from visual interpretation to a tool call, rather than an actual request for emergency assistance.

## Restricting What Each Agent Can Do

Allowing an observation to trigger a tool call makes the available tools part of the design decision. We give each agent access only to the tools required for its role, following the principle of least privilege. A request assigned to one agent therefore does not give it access to every vehicle-control tool.

The Guardrail Agent provides another check before requests reach the supervisor. We use rule-based checks for unsafe or out-of-scope commands and frameworks such as [DeepEval](https://github.com/confident-ai/deepeval) to test malicious and adversarial inputs. These tests help us look for cases where a manipulated input gets past the checks or causes unintended agent behavior.

## From Prototype to In-Vehicle Deployment

The prototype brings voice interaction, vehicle information, and visual reasoning into one system that we can extend with new agents and tools. Connecting the mocked tools to vehicle subsystems would let us build on that foundation and evaluate complete workflows on the intended hardware. That next stage would need to address response time and memory use alongside the validation and safety requirements of operating within a vehicle.

Further model optimization could give us other choices within the same memory budget. [Pruning](https://arxiv.org/pdf/2204.09656) removes model weights, while [knowledge distillation](https://huggingface.co/blog/Kseniase/kd) trains a smaller model to reproduce aspects of a larger model’s behavior. [Quantization-aware training](https://pytorch.org/blog/quantization-aware-training/) is another option for adapting a model to reduced precision. For this assistant, any of these would need to be evaluated against the same concerns that shaped the model split: accuracy, response time, and memory use.

## Looking Ahead

Beyond integrating the current capabilities, there are opportunities to make the assistant more proactive and personal. One is using [world models](https://deepmind.google/discover/blog/genie-3-a-new-frontier-for-world-models/) to explore what could happen after an action, rather than relying only on a spoken request or predefined trigger. Virtual environments could offer a way to investigate how an assistant anticipates situations and considers possible responses before testing those behaviors in a vehicle.

The other is [continual learning](https://arxiv.org/abs/2402.01364), allowing the assistant to adapt to a particular driver’s preferences and vehicle vocabulary over time. [QLoRA](https://wandb.ai/sauravmaheshkar/QLoRA/reports/What-is-QLoRA---Vmlldzo2MTI2OTc5) is one possible fine-tuning method: it keeps a quantized model’s base weights frozen and trains small additional matrices. Those smaller updates could offer a way to adapt the assistant without retraining all of its weights, as part of a broader system for learning from the driver over time.

## Closing Thoughts

The assistant shows a practical path toward collaborative, extensible in-vehicle AI, bringing speech, vehicle knowledge, tool use, and visual reasoning together on local hardware. Its specialized agents can work together while reserving the larger model for tasks that require vision, and new capabilities can be added without redesigning the whole assistant. Running inference locally also supports the privacy and offline requirements that motivated the project.

We put these ideas into a prototype with two quantized models fitting within 14 GB of VRAM, and roughly halved average end-to-end latency through KV-cache warmup. Together, these improvements brought us closer to running voice interaction and camera-based assistance within the constraints of on-device compute.

Connecting the prototype to vehicle systems would bring that work closer to the driving experience it is intended to support. We have only begun to explore the possibilities, from more proactive assistance to interactions tailored to each driver. As software and hardware improve, cars can become more than a way to get from one place to another, with assistants that understand more of the journey and help along the way.

_Unlike my first post, I wrote this one with help from LLMs. The ideas are mine; the LLMs helped with wording and structure, and I edited their output so it would not read like generic generated text._
