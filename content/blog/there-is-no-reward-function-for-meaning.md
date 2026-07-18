+++
title = "There Is No Reward Function for Meaning"
date = "2026-06-27"
slug = "there-is-no-reward-function-for-meaning"
tags = ["AI", "Philosophy"]
+++

### **We Only Celebrate Progress Once**

A century ago, sending word to another continent meant waiting weeks for a ship to cross the ocean. Today, we complain when a video call lags for half a second. For some reason, we celebrate a breakthrough once, then it quietly becomes the baseline.

Smartphones put a camera, a map, and a library in our pockets. We barely think about that anymore, but we notice immediately when the battery runs low. Once something remarkable becomes routine, our attention moves to the ways it still falls short.

I think the same pattern shapes how we talk about AI. The [“strawberry” meme](https://techcrunch.com/2024/08/27/why-ai-cant-spell-strawberry/) is a good example. Language models confidently miscounted the number of **r**'s in _strawberry_, and the mistake became an easy way to dismiss their intelligence. Meanwhile, those models were explaining advanced mathematics, summarizing research papers, and writing working software. We remembered the flaw and took the broader capabilities for granted.

I noticed the same tendency in myself while working with coding agents over the past year. Each time they became more reliable at completing tasks, my expectations rose. That made it more frustrating when they misunderstood requirements or overlooked established guidelines.

Despite those mistakes, there is something compelling about how they fundamentally handle failure. They read an error, revise their approach, rerun the tests, and try again. Sometimes they repeat this loop dozens of times before producing anything useful, with each attempt providing more information for the next one.

Watching that process has made me appreciate the role of iteration in problem-solving. We humans have a harder time letting go of an approach once we have invested in it. Changing course can feel like admitting we were wrong.

A coding agent has no attachment to its previous approach. Give it useful feedback and it moves on. From the outside, that willingness to revise resembles intellectual humility. Effective problem-solving requires the same willingness to change course.

### **When the Objective Is Clear**

I learned how much this kind of iteration depends on the surrounding environment when running agents in a corporate setting. At first, my coding agent made mistakes constantly. It could read the code, but it knew very little about the organization around it. It did not know which sources of truth we trusted, how our internal workflows fit together, or which seemingly reasonable approaches had already caused problems. As a result, its investigations were shallow and its implementations were often plausible in isolation but wrong for our system.

Over time, I began treating each recurring failure as a gap in the agent's environment. I recorded gotchas in `AGENTS.md`, created skills for workflows I used often, and added helpers for common tasks. Context management was another part of that work. Even though Opus 4.8 has a one-million-token context window, I had the agent compact automatically at 258,000 tokens (inspired by Codex).[^context-rot]

Birgitta Böckeler calls this user-built layer the [“outer harness”](https://martinfowler.com/articles/harness-engineering.html). Coding agents, she writes, give their users “many features to build an outer harness specifically for our use case and system.” That phrase describes what I have been building: a way to make our team's local knowledge, tools, and feedback available to a general-purpose model.

As the harness improved, so did the agent's work. Its investigations became more thorough, and its implementations became more accurate. For much of this period, the underlying model stayed the same. I was improving the environment in which it worked.

That experience also changed how I think about prompting. Clear instructions help, but they are only one part of the system. When I can define the problem, explain the important tradeoffs, and provide a reliable test harness, an agent can often complete most of an implementation with little intervention. Much of my work has shifted toward making the objective and its surrounding context precise.

Software engineering gives agents an unusual advantage here: frequent, legible feedback. A compiler rejects invalid code, tests expose incorrect behavior, and benchmarks show whether performance improved. With signals like these, an agent can keep adjusting its work and tell whether it is moving closer to the objective. This helps explain why coding agents can appear so capable when the task is well specified.

The harness has limits, though. It can tell an agent how to run our tests, but it cannot settle every decision we face while building software. Should an abstraction live here or somewhere else? Is a performance improvement worth the added complexity? Which document or system should become the source of truth? Tests can rule out solutions that fail. When several solutions work, experience and judgment help decide which one best fits the codebase.

At first, I saw these as ordinary design decisions at the edge of an otherwise measurable process. The more I thought about them, however, the more they changed how I understood intelligence itself.

### **There Is No Reward Function for Meaning**

The tension between feedback and judgment becomes harder to ignore when I think about the questions that shape a life. Should I forgive someone who never apologized? Should I leave a good job for work that might matter more? When, or whether, should I have children?

We can gather information and ask people we trust, but eventually the evidence runs out and our values have to do the remaining work. There is no compiler error for purpose and no test suite that tells us whether we are becoming the person we hoped to be.

Two thoughtful people can understand the same situation and still reach different conclusions because different things matter to them. More intelligence does not necessarily resolve that difference. This has changed how I think about intelligence. Deciding which problems are worth solving requires judgment too.

More ambitious AI architectures may encounter the same boundary. Yann LeCun's [world models](https://amilabs.xyz/), for instance, aim to give machines an internal representation of reality so they can reason about cause and effect and plan ahead. This may be a major advance beyond next-token prediction. A richer model of the world could help a system predict the consequences of an action, but prediction alone does not tell it which consequences are worth pursuing.

Language models and world models do not all learn in the same way; they may use different combinations of loss functions, reward signals, or self-supervised objectives. Still, each approach needs some criterion that guides learning or behavior, and someone has to choose that criterion and the environment in which the system pursues it.

This is the old gap between what _is_ and what _ought_ to be. A perfect prediction of every possible future would still leave us to decide which future we prefer. Programming often supplies a clear objective, and nature gives scientists constraints against which to test their ideas. Many of the choices that shape a life are less accommodating.

This does not make AI irrelevant to those choices. It may help us examine assumptions and consequences from perspectives we might have missed. That is a meaningful contribution to deliberation, even if it does not resolve it. The final judgment still belongs to people who have to live with the outcome.

Whenever the conversation turns to artificial general intelligence (AGI), people ask whether we will build it and when. Working with coding agents has left me with a related question: what can intelligence offer when nobody can agree on the objective? I do not know the answer, and I am suspicious of answers that make the problem sound simple.

At this point in my life, judgment interests me more than perfection. Some of my favorite lessons leave room for interpretation. I find meaning in the quirks of human thought and in the matters of taste and opinion each of us has to weigh for ourselves. The people I admire are willing to wrestle with ambiguity and accept that some disagreements will remain unresolved.

AI may become an extraordinary scientific collaborator. I hope it does. The questions that define a human life—what to value, what beauty is, and how to spend the finite years we are given—may remain stubbornly resistant to optimization. Not because machines lack intelligence, but because there is no reward function for meaning.

[^context-rot]: Frontier models still suffer from [context rot](https://www.trychroma.com/research/context-rot). Perhaps they have [benchmaxxed](https://www.jeannelizabeth.com/blog/benchmaxxing-the-ugly-art-of-optimising-for-leaderboards) too hard...
