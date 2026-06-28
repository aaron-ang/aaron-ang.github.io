+++
title = "Marlin"
slug = "marlin"
weight = 13
links = ["https://devpost.com/software/marlin", "https://github.com/aaron-ang/Marlin"]
+++

Won **3rd Place — Cybersecurity** at [Cerebral Beach Hacks](https://cerebral-beach-hacks.devpost.com).

Marlin is a Chrome extension designed to combat the rising threat of business email compromise, which caused over **$2.9B** in losses in 2023 according to FBI reports. By making email security analysis accessible and user-friendly, Marlin helps protect users from sophisticated phishing attempts that often bypass traditional security measures. The extension works by extracting and anonymizing email content, analyzing it using an open-source Mixtral model via Kindo AI, and presenting users with clear, actionable insights about potential security threats.

Marlin strips identifying information locally before anything reaches the cloud, then surfaces results through an interactive visualization designed to keep users informed without overwhelming them. Planned next steps include dedicated cloud GPUs, model fine-tuning, and direct email-client integration.

Built alongside four other students in a span of 24 hours.

Tech stack: React, TypeScript, Tailwind CSS, FastAPI, Python, Mixtral (via [Kindo AI](https://www.kindo.ai)), [Chrome Extensions API](https://developer.chrome.com/docs/extensions), [scrubadub](https://scrubadub.readthedocs.io/en/stable/)
