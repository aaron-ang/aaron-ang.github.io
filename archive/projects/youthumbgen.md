+++
draft = true
title = "YouThumbGen"
slug = "youthumbgen"
weight = 11
links = ["https://github.com/ss-cosmos-hue/thumbnail_generator", "https://devpost.com/software/fabulous-5"]
+++

### Generate thumbnails for YouTube with the click of a button.

Our program takes an image and some text as input. It will resize, enhance, and position the inputs using AI and custom adaptive logic to create the final thumbnail image.

This proof of concept demonstrates the applicability of image recognition models to potentially create visually appealing thumbnails which will save content creators a large bulk of time and/or money designing and creating custom thumbnails for each of their videos.

Check out the test cases in the repository’s [README](https://github.com/ss-cosmos-hue/thumbnail_generator#readme).

Built alongside four other amazing individuals in 36 hours at [HackPrinceton Spring 2023](https://hackprinceton-spring-2023.devpost.com).

Tech stack: Python, Flask.

Image libraries

- [Pillow](https://github.com/python-pillow/Pillow)
- [Real-ESRGAN](https://github.com/xinntao/Real-ESRGAN)
- [rembg](https://github.com/danielgatis/rembg)
