+++
title = "There Is No Reward Function for Meaning"
date = "2026-06-27"
slug = "there-is-no-reward-function-for-meaning"
tags = ["AI", "Philosophy"]
+++

### **We Only Celebrate Progress Once**

A century ago, sending word to another continent meant waiting weeks for a ship to cross the ocean. Now a livestream lags for half a second and we complain. For some reason, we celebrate a breakthrough only once before it quietly becomes the baseline.

Smartphones put a camera, a map, and a library in our pockets. We barely think about that anymore. Yet we notice right away when the battery runs low. Once something remarkable turns routine, our attention drifts to the places where it falls short.

I see the same pattern in how we talk about AI. Take the [“strawberry” meme](https://techcrunch.com/2024/08/27/why-ai-cant-spell-strawberry/). Language models confidently miscounted the **r**'s in _strawberry_, and the mistake became an easy way for people to dismiss the models' intelligence. Meanwhile, those models were solving [competitive mathematics problems](https://x.com/alexwei_/status/1946477742855532918), [synthesizing scientific literature](https://www.nature.com/articles/s41586-025-10072-4), and [contributing to production code](https://doi.org/10.1145/3798166). The flaw stuck in our memory while we took the broader capabilities for granted.

Over the past year, I caught myself doing the same thing with coding agents. My expectations went up every time they got more reliable at finishing tasks. Those higher expectations made it more frustrating when they misunderstood requirements or overlooked established guidelines.

Still, I find the way they handle failure compelling. An agent reads an error and revises its approach. Then it reruns the tests and tries again. Some sessions go around this loop dozens of times before anything useful comes out, and each attempt gives the agent more information for the next one.

Watching that loop up close has made me appreciate how much problem-solving depends on iteration. Letting go is harder for us humans once we have invested in an approach. Abandoning it can feel like admitting we were wrong.

A coding agent has no attachment to whatever it tried before. Give it useful feedback and it moves on. Seen from outside, that willingness to revise resembles intellectual humility.

### **When the Objective Is Clear**

Iteration only helps if the agent knows what a better result looks like. I learned that while managing agents at my day job in tech. Early on, my coding agent made mistakes constantly. Reading the code was easy for it, but the organization around the code was mostly invisible. It guessed our file-naming and code-location conventions by pattern-matching on whatever files it opened, and some older files had never followed those conventions. It didn't know which projects held the infrastructure behind a service, even though a feature or fix often needs related merge requests there. It had no idea which sources of truth we trusted, or which reasonable-looking approaches had already caused problems. Its investigations stayed shallow as a result, and its implementations were often plausible in isolation but wrong for our system.

Gradually, I started treating each recurring failure as a gap in the agent's environment. Gotchas went into `AGENTS.md`. One set covered the corporate VPN and its limits on external network access, which stumped the agent the first few times it made API requests. Another was a handful of styling rules for GitLab merge request descriptions. GitLab renders a reference like `!123` as a link to that merge request, and the agent never needs the full URL. Workflows I used often became skills, like running Argo, Helm, and Kubernetes tests ad hoc in our dev environment. I also spelled out when the agent should hand a problem back to me. If its SSO session expires, it should ask me to refresh it instead of heading off down some unrelated path.

Most of this was never documented. It was domain knowledge that used to live in one engineer's head or personal notes. I added helpers for common tasks too, and I managed context, compacting long sessions well before they exhausted the model’s context window.[^context-rot]

Birgitta Böckeler has a name for this user-built layer: the [“outer harness”](https://martinfowler.com/articles/harness-engineering.html). Coding agents, she writes, give their users “many features to build an outer harness specifically for our use case and system.” Her phrase fits what I have been building. My harness is a way to hand our team's local knowledge, tools, and feedback to a general-purpose model.

The agent's work got better as the harness did. Its investigations grew more thorough and its implementations more accurate. Yet for much of this period, the underlying model never changed. What I was improving was the environment it worked in.

Building the harness changed how I think about prompting, too. Clear instructions help, but they are only one part of the system. If I can define the problem, explain the important tradeoffs, and provide a reliable test harness, an agent can often finish most of an implementation with little intervention from me. More and more, my job is to pin down the objective and its surrounding context precisely. Working out how to get there is the agent's job.

Software engineering hands agents one more advantage: feedback that arrives often and points at what to fix. The compiler rejects invalid code. Tests expose incorrect behavior, and benchmarks show whether performance improved. With signals like these, an agent can keep adjusting its work and tell whether it is getting closer to the objective. Signals like these help explain why coding agents can appear so capable when the task is well-specified.

My harness has limits, though. It can tell an agent how to run our tests. It cannot settle every decision we face while building software. Should an abstraction live here or somewhere else? Someone also has to judge whether a performance improvement is worth the added complexity, and which document or system should become the source of truth. Tests rule out the solutions that fail. When several plausible ones remain, experience and judgment must choose among them. Whether the choice was right becomes clear only later, when a poor abstraction slows down the next feature or a needless optimization turns into a maintenance burden.

### **There Is No Reward Function for Meaning**

When I think about the questions that shape a life, the tension between feedback and judgment gets harder to ignore. When should I put someone else’s needs before my own? Whether to leave a good job for work that might matter more is another such question. So is the question of when, or whether, I should have children.

My examples are not all of the same kind. Some concern morality and others meaning. Each one, though, asks us to evaluate ends instead of merely discovering means. Life does give plenty of feedback in the form of satisfaction, regret, and the reactions of people we love. What it lacks is a compiler error for purpose. No test suite tells us whether we are becoming the person we hoped to be. Nor is there any authoritative way to weigh one signal against another.

We can gather information, consult people we trust, and reason as carefully as we are able. Greater intelligence might clarify the consequences. It could expose contradictions and turn up possibilities we had overlooked. Two thoughtful people can still land on different conclusions because they value different things. The final judgment belongs to those of us who have to live with the outcome.

Could a richer model of the world do more than clarify the facts? Yann LeCun's [world models](https://amilabs.xyz/) are one example of such a model. They aim to give machines an internal representation of reality, letting them reason about cause and effect and plan ahead. A model like that might predict an action's consequences more accurately. Prediction alone still cannot decide which consequences are worth pursuing.

Whatever the architecture, learning needs some criterion to guide behavior. Someone has to choose that criterion and the environment it operates in.[^autonomous-learning] Often the criterion is anchored to something measurable, like the next token, a reward, or the [next robot action](https://arxiv.org/abs/2307.15818).

Seemingly narrow objectives like these can still give rise to internal structures that researchers never specified. Mechanistic interpretability grew into an entire research field partly because we are still trying to understand how such structures arise.[^mechanistic-interpretability] Anthropic's recent work on Claude's [J-space](https://www.anthropic.com/research/global-workspace) offers one example: a small internal workspace that appears to support deliberate reasoning. It emerged during training rather than by explicit design, and its full role and mechanism remain unclear.

Discussions of artificial general intelligence (AGI) make questions about emergent capacities especially pressing. Nobody knows yet whether or when we will build AGI.[^agi] [Google DeepMind's framework](https://deepmind.google/research/publications/66938/) defines it objectively. In their terms, AGI is a system at least as capable as a human across most tasks, and progress is measured by the breadth and depth of its abilities. Is intelligence simply an amalgamation of capabilities we can quantify and optimize? Much of the hope around AGI rests on scaling. The bet is that more compute and data will yield an unrivaled emergent form of intelligence, and perhaps, as a byproduct, an understanding of what we should value.

Even that system would run into Hume’s is–ought problem. A perfect prediction of every possible future would not, by itself, tell us which one to prefer.[^is-ought] Hume's problem limits what we can infer from facts. It is not necessarily a claim that morality and meaning lie beyond the observable world. Values may themselves be part of human experience, showing up in our emotions, our relationships, and how we live. A capable enough system might learn to model them. Modeling what people value still differs from deciding what a particular life should aim toward. The person living that life chooses its objective, and part of what gives life meaning is that one person's choice does not transfer to another. AGI aims to generalize intelligence across tasks. Meaning resists being generalized from one person to the next. Can AGI, then, deliver what many of its creators promise, a world where all of humanity's problems are solved? I do not know, and answers that make the problem sound simple leave me skeptical.

I have reached a point in my life where judgment interests me more than certainty. Some of my favorite lessons leave room for interpretation. I find meaning in the quirks of human thought, and in the matters of taste and opinion that each of us has to weigh for ourselves. The people I admire are willing to wrestle with ambiguity and accept that some disagreements will stay unresolved.

I expect AI to become an extraordinary scientific collaborator, in part because it is engineered to optimize chosen objectives. It may solve many of the problems on our list. Like the livestream and the smartphone, though, each solution will soon become the new baseline. If every solved problem only reveals the next one, how could solving more of them lead to a utopia, much less to meaning?

Human life, on the other hand, comes with no equivalent specification, and nobody can write one on our behalf. We each have to weigh the competing signals ourselves. What to value, and how to spend the finite years we are given, may stay stubbornly resistant to optimization. The reason isn't a shortage of machine intelligence. There is no reward function for meaning.

[^context-rot]: Opus 4.8 offers a context window of one million tokens, but I set the agent to compact automatically at 272,000 tokens, inspired by Codex. Frontier models still suffer from [context rot](https://www.trychroma.com/research/context-rot). Perhaps they have [benchmaxxed](https://www.jeannelizabeth.com/blog/benchmaxxing-the-ugly-art-of-optimising-for-leaderboards) too hard...

[^autonomous-learning]: Dupoux, LeCun, and Malik propose an architecture that could learn more autonomously. Yet its outer loop still relies on a fitness function and environment designed by humans: “The fitness function (and the environments) are the only handcrafted parts of the system.” See [_Why AI Systems Don’t Learn and What to Do About It_](https://arxiv.org/abs/2603.15381).

[^mechanistic-interpretability]: Mechanistic interpretability tries to reverse-engineer what a neural network has learned to compute. [Neel Nanda](https://www.alignmentforum.org/users/neel-nanda-1) and colleagues applied it to small transformers trained on modular addition and recovered the algorithm inside; see [_Progress measures for grokking via mechanistic interpretability_](https://arxiv.org/abs/2301.05217). The field's unsolved challenges are surveyed in [_Open Problems in Mechanistic Interpretability_](https://arxiv.org/abs/2501.16496).

[^is-ought]: Hume noticed that moral arguments often slide from statements about what _is_ to claims about what _ought_ to be, with no explanation of the step. See [“Is and Ought” in the _Stanford Encyclopedia of Philosophy_](https://plato.stanford.edu/archives/spr2022/entries/hume-moral/#io).

[^agi]: Even the term is slippery. Geoffrey Hinton calls AGI “a serious, though ill-defined, concept,” and researchers still disagree on how they would recognize it. See Matt O’Brien, [“Tech companies want to build artificial general intelligence. But who decides when AGI is attained?”](https://apnews.com/article/agi-artificial-general-intelligence-existential-risk-meta-openai-deepmind-science-ff5662a056d3cf3c5889a73e929e5a34).
