+++
title = "PredicT"
slug = "predict"
weight = 7
links = ["https://github.com/kbbtan/hackmit-project-2022", "https://www.youtube.com/watch?v=LslNEI-nmLQ"]
+++

### MBTA congestion predictor

Nobody likes a crowded subway. PredicT estimates how busy Boston's “T” will be from three inputs — date, time, and subway line — so you can plan around the crowds.

We pulled publicly available MBTA tap-in and tap-out records from every station across the day, cleaned them, and trained [InterSystems IntegratedML](https://docs.intersystems.com/irislatest/csp/docbook/DocBook.UI.Page.cls?KEY=GIML_Basics) on the result to predict passenger volume. A React front end queries those predictions through a Flask API.

Built at [HackMIT 2022](https://archive.hackmit.org/2022/) in a team of four within 24 hours.

Tech stack: React, Flask, [InterSystems IntegratedML](https://docs.intersystems.com/irislatest/csp/docbook/DocBook.UI.Page.cls?KEY=GIML_Basics).
