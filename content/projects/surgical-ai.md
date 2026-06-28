+++
title = "Surgical.AI"
slug = "surgical-ai"
weight = 15
links = ["https://devpost.com/software/surgical-ai", "https://github.com/aaron-ang/Surgical.AI"]
+++

Surgical.AI tackles a critical healthcare safety issue: the **3,000** annual cases of surgical tools being left inside patients' bodies during operations. Inspired by a case where a tool "the size of a dinner plate" was discovered inside a patient 18 months after a c-section, this project aims to prevent such incidents through AI-powered tool tracking, even when traditional manual tracking by medical staff fails.

Our system, named Surgi, uses real-time video streaming to track and segment surgical tools during operations, maintaining a constant inventory of their status (in place, out of place, or missing). Surgi features hands-free voice communication and the ability to instantly replay the last known location of any tool, making it seamless for surgical staff to use during procedures.

Built alongside three other students in a span of 36 hours.

Tech stack: Next.js, [Shadcn](https://ui.shadcn.com), Tailwind CSS, [Deepgram](https://deepgram.com/product/speech-to-text), [Gemini](https://ai.google.dev/gemini-api), [Yolov11](https://docs.ultralytics.com/models/yolo11/), Firebase, Websockets
