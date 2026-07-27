+++
title = "There Is No Reward Function for Meaning"
date = "2026-06-27"
slug = "there-is-no-reward-function-for-meaning"
tags = ["AI", "Philosophy"]
+++

### **We Only Celebrate Progress Once**

A century ago, sending word to another continent meant waiting weeks for a ship to cross the ocean. Today, we complain when a livestream lags for half a second. For some reason, we celebrate a breakthrough once, then it quietly becomes the baseline.

Smartphones put a camera, a map, and a library in our pockets. We barely think about that anymore, but are quick to notice when the battery is running low. Once something remarkable becomes routine, our attention moves to the ways it falls short.

I think the same pattern shapes how we talk about AI. The [“strawberry” meme](https://techcrunch.com/2024/08/27/why-ai-cant-spell-strawberry/) is a good example. Language models confidently miscounted the number of **r**'s in _strawberry_, and the mistake became an easy way to dismiss their intelligence. Meanwhile, those models were solving [competitive mathematics problems](https://x.com/alexwei_/status/1946477742855532918), [synthesizing scientific literature](https://www.nature.com/articles/s41586-025-10072-4), and [contributing to production code](https://doi.org/10.1145/3798166). We remembered the flaw and took the broader capabilities for granted.

I noticed the same tendency in myself while working with coding agents over the past year. Each time they became more reliable at completing tasks, my expectations rose. That made it more frustrating when they misunderstood requirements or overlooked established guidelines.

Despite those mistakes, there is something compelling about how they handle failure. They read an error, revise their approach, rerun the tests, and try again. Sometimes they repeat this loop dozens of times before producing anything useful, with each attempt providing more information for the next one.

Working through that process with coding agents has made me appreciate the role of iteration in problem-solving. We humans have a harder time letting go once we have invested in an approach. Abandoning it can feel like admitting we were wrong.

A coding agent has no attachment to its previous approach. Give it useful feedback and it moves on. From the outside, that willingness to revise resembles intellectual humility. Over time, some of that habit has rubbed off on me. When my own approach fails, I try to take the lesson, change course, and keep going.

### **When the Objective Is Clear**

Iteration only helps when an agent has useful feedback and enough context to act on it. I learned this when managing agents at my day job in tech. At first, my coding agent made mistakes constantly. It could read the code, but it knew very little about the organization around it. It did not know which sources of truth we trusted, how our internal workflows fit together, or which seemingly reasonable approaches had already caused problems. As a result, its investigations were shallow and its implementations were often plausible in isolation but wrong for our system.

Over time, I began treating each recurring failure as a gap in the agent's environment. I recorded gotchas in `AGENTS.md`, created skills for workflows I used often, and added helpers for common tasks. Context management was another part of that work. Even though Opus 4.8 has a one-million-token context window, I had the agent compact automatically at 258,000 tokens (inspired by Codex).[^context-rot]

Birgitta Böckeler calls this user-built layer the [“outer harness”](https://martinfowler.com/articles/harness-engineering.html). Coding agents, she writes, give their users “many features to build an outer harness specifically for our use case and system.” That phrase describes what I have been building: a way to make our team's local knowledge, tools, and feedback available to a general-purpose model.

As the harness improved, so did the agent's work. Its investigations became more thorough, and its implementations became more accurate. For much of this period, the underlying model stayed the same. I was improving the environment in which it worked.

That experience also changed how I think about prompting. Clear instructions help, but they are only one part of the system. When I can define the problem, explain the important tradeoffs, and provide a reliable test harness, an agent can often complete most of an implementation with little intervention. Much of my work has shifted toward making the objective and its surrounding context precise.

Software engineering also gives agents an advantage in the form of frequent, actionable feedback. A compiler rejects invalid code, tests expose incorrect behavior, and benchmarks show whether performance improved. With signals like these, an agent can keep adjusting its work and tell whether it is moving closer to the objective. This helps explain why coding agents can appear so capable when the task is well-specified.

Nevertheless, this setup has limits. It can tell an agent how to run our tests, but it cannot settle every decision we face while building software. Should an abstraction live here or somewhere else? Is a performance improvement worth the added complexity? Which document or system should become the source of truth? Tests can rule out solutions that fail. When there are several plausible solutions, experience and judgment help decide which one best fits the codebase.

### **There Is No Reward Function for Meaning**

The tension between feedback and judgment becomes harder to ignore when I think about the questions that shape a life. When should I put someone else’s needs before my own? Should I leave a good job for work that might matter more? When, or whether, should I have children?

There is no compiler error for purpose and no test suite that tells us whether we are becoming the person we hoped to be.

We can gather information, consult people we trust, and reason as carefully as we can. But facts and evidence do not always answer the question. At some point, we have to decide which problems are worth solving and which tradeoffs are acceptable. The final judgment belongs to those of us who have to live with the outcome.

Even then, two thoughtful people can reach different conclusions because they value and prioritize different things. More intelligence may clarify the facts and consequences without resolving how they should be weighed.

Could a richer model of the world close that gap? Yann LeCun's [world models](https://amilabs.xyz/), for instance, aim to give machines an internal representation of reality so they can reason about cause and effect and plan ahead. Such a model might predict the consequences of an action more accurately. However, prediction alone does not tell it which consequences are worth pursuing.

Whatever the architecture, learning still needs some criterion that guides behavior, and someone has to choose that criterion and the environment in which it operates.[^autonomous-learning] These criteria are often anchored to something measurable: the next token, a reward, or the [next robot action](https://arxiv.org/abs/2307.15818).

Yet, these seemingly narrow objectives have produced complex latent structures that their engineers never specified. Mechanistic interpretability has become an entire research field partly because we are still trying to understand how such structures arise.[^mechanistic-interpretability] Anthropic's recent work on Claude's [J-space](https://www.anthropic.com/research/global-workspace), a small internal workspace that appears to support deliberate reasoning, is one example. It emerged during training, and researchers are still trying to understand what it is and how much philosophical significance it carries.

This is where Hume's is–ought problem comes into play. Even a perfect prediction of every possible future would not tell us which one to prefer.[^is-ought] At the same time, I am not sure whether morality and meaning require anything beyond a world that a sufficiently capable system could observe and model. They may already be present in how we live. If a system could capture all of that, what—if anything—would still be missing?

The question about whether we will build artificial general intelligence (AGI) and when it will be achieved remains open.[^agi] [Google DeepMind's framework](https://deepmind.google/research/publications/66938/) describes AGI as a system at least as capable as a human at most tasks, with progress measured by the breadth and depth of its capabilities. Dealing with coding agents has left me with a different question: what can intelligence offer when nobody can agree on the objective? I do not know the answer, and I am skeptical of answers that make the problem sound simple.

I have reached a point in my life where judgment interests me more than perfection. Some of my favorite lessons leave room for interpretation. I find meaning in the quirks of human thought and in the matters of taste and opinion each of us has to weigh for ourselves. The people I admire are willing to wrestle with ambiguity and accept that some disagreements will remain unresolved.

I expect AI to become an extraordinary scientific collaborator in part because it is engineered to optimize specific chosen objectives. Human life, on the other hand, comes with no equivalent specification. Questions like what to value and how to spend the finite years we are given may remain stubbornly resistant to optimization. Not because machines lack intelligence, but because there is no reward function for meaning.

[^context-rot]: Frontier models still suffer from [context rot](https://www.trychroma.com/research/context-rot). Perhaps they have [benchmaxxed](https://www.jeannelizabeth.com/blog/benchmaxxing-the-ugly-art-of-optimising-for-leaderboards) too hard...

[^autonomous-learning]: Dupoux, LeCun, and Malik propose an architecture that could learn more autonomously, yet its outer loop still relies on a human-designed fitness function and environment: “The fitness function (and the environments) are the only handcrafted parts of the system.” See [_Why AI Systems Don’t Learn and What to Do About It_](https://arxiv.org/abs/2603.15381).

[^mechanistic-interpretability]: Mechanistic interpretability attempts to reverse-engineer the computations learned inside neural networks. [Neel Nanda](https://www.alignmentforum.org/users/neel-nanda-1) and colleagues used it to reconstruct the algorithm learned by small transformers trained on modular addition; see [_Progress measures for grokking via mechanistic interpretability_](https://arxiv.org/abs/2301.05217). For a survey of the field's remaining challenges, see [_Open Problems in Mechanistic Interpretability_](https://arxiv.org/abs/2501.16496).

[^is-ought]: Hume observed that moral arguments often move from statements about what _is_ to claims about what _ought_ to be without explaining the transition. See [“Is and Ought” in the _Stanford Encyclopedia of Philosophy_](https://plato.stanford.edu/archives/spr2022/entries/hume-moral/#io).

[^agi]: The term itself remains slippery. Geoffrey Hinton calls AGI “a serious, though ill-defined, concept,” and researchers still disagree about how to recognize it. See Matt O’Brien, [“Tech companies want to build artificial general intelligence. But who decides when AGI is attained?”](https://apnews.com/article/agi-artificial-general-intelligence-existential-risk-meta-openai-deepmind-science-ff5662a056d3cf3c5889a73e929e5a34).
