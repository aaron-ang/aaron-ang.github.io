// Step controls for animated SVG diagrams.
//
// An <img> hides its animations from the page, so each figure marked with
// data-stepper swaps its image for an <object>, reads the CSS animations inside,
// and treats every stretch where nothing changes as one step.

const MIN_STEP_MS = 400;
const SETTLE_MS = 50;

const isEqualKeyframe = (a, b) =>
  Object.keys(a).every(
    (key) => ["offset", "computedOffset", "easing", "composite"].includes(key) || a[key] === b[key],
  );

// Returns the settled end time of each step, in loop order, starting with the
// state shown when the loop begins.
function findStepTimes(animations) {
  const loopMs = Math.max(
    ...animations.map((a) => {
      const { duration, delay = 0 } = a.effect.getComputedTiming();
      return delay + duration;
    }),
  );
  const changes = [];

  for (const animation of animations) {
    const { duration, delay = 0 } = animation.effect.getComputedTiming();
    const frames = animation.effect.getKeyframes();

    for (let i = 1; i < frames.length; i++) {
      if (isEqualKeyframe(frames[i - 1], frames[i])) continue;

      changes.push([
        delay + frames[i - 1].computedOffset * duration,
        delay + frames[i].computedOffset * duration,
      ]);
    }
  }

  if (!changes.length) return { loopMs, stepTimes: [] };

  changes.sort((a, b) => a[0] - b[0]);
  const merged = [changes[0].slice()];

  for (const [start, end] of changes.slice(1)) {
    const last = merged[merged.length - 1];

    if (start <= last[1]) last[1] = Math.max(last[1], end);
    else merged.push([start, end]);
  }

  // The quiet stretch after the last change runs into the one before the first
  // change, because the loop repeats.
  const stepTimes = [];

  for (let i = 0; i < merged.length; i++) {
    const quietStart = merged[i][1];
    const quietEnd = i + 1 < merged.length ? merged[i + 1][0] : merged[0][0] + loopMs;

    if (quietEnd - quietStart < MIN_STEP_MS) continue;

    stepTimes.push((quietEnd - SETTLE_MS) % loopMs);
  }

  stepTimes.sort((a, b) => a - b);

  if (stepTimes.length < 2) return { loopMs, stepTimes: findJoints(changes, loopMs) };

  return { loopMs, stepTimes };
}

// For diagrams in continuous motion: the keyframe times where one movement
// hands over to the next, with nothing mid-change.
function findJoints(changes, loopMs) {
  const joints = [];
  const times = [...new Set(changes.flat().map((time) => time % loopMs))].sort((a, b) => a - b);

  for (const time of times) {
    const moving = changes.some(([start, end]) => start < time && time < end);
    const tooClose = joints.length && time - joints[joints.length - 1] < MIN_STEP_MS;

    if (!moving && !tooClose) joints.push(time);
  }

  // The last joint may sit just before the loop restarts at the first one.
  if (joints.length > 1 && joints[0] + loopMs - joints[joints.length - 1] < MIN_STEP_MS) joints.pop();

  return joints;
}

// Loads the SVG next to `sibling`, out of sight. Moving an <object> reloads its
// document, so it is inserted where it will stay.
function loadObject(src, label, sibling) {
  const object = document.createElement("object");
  object.type = "image/svg+xml";
  object.data = src;
  object.className = "diagram-stepper-loading";
  object.setAttribute("aria-label", label);

  const loaded = new Promise((resolve, reject) => {
    object.addEventListener("load", () => {
      const doc = object.contentDocument;

      if (!doc) {
        reject(new Error(`no document for ${src}`));
        return;
      }

      // CSS animations exist once the document has had a style pass.
      doc.defaultView.requestAnimationFrame(() => resolve(doc));
    });
    object.addEventListener("error", () => reject(new Error(`failed to load ${src}`)));
  });

  sibling.after(object);

  return { object, loaded };
}

function svgAspectRatio(doc) {
  const svg = doc.documentElement;
  const width = parseFloat(svg.getAttribute("width"));
  const height = parseFloat(svg.getAttribute("height"));

  return width && height ? `${width} / ${height}` : "";
}

function button(label, text) {
  const element = document.createElement("button");
  element.type = "button";
  element.setAttribute("aria-label", label);
  element.textContent = text;

  return element;
}

class DiagramStepper {
  constructor(figure) {
    this.figure = figure;
    this.image = figure.querySelector("img");
    this.imageLink = this.image.closest("a");
    this.label = this.image.alt;
    this.compactSource = figure.querySelector("source[media]");
    this.object = null;
    this.animations = [];
    this.stepTimes = [];
    this.loopMs = 0;
    this.step = 0;
    this.playing = false;
    this.frame = 0;
    this.loads = 0;
  }

  async start() {
    try {
      await this.image.decode();
    } catch {
      return;
    }

    await this.show(this.image.currentSrc || this.image.src);

    if (!this.compactSource) return;

    const query = window.matchMedia(this.compactSource.media);
    query.addEventListener("change", () =>
      this.show(query.matches ? this.compactSource.srcset : this.image.src),
    );
  }

  async show(src) {
    const load = ++this.loads;
    const current = this.object ?? this.imageLink;
    const { object, loaded } = loadObject(src, this.label, current);
    let doc;

    try {
      doc = await loaded;
    } catch (error) {
      console.warn("diagram-stepper:", error);
      object.remove();
      return;
    }

    // A newer breakpoint change has started its own load.
    if (load !== this.loads) {
      object.remove();
      return;
    }

    const animations = doc.getAnimations();
    const { loopMs, stepTimes } = findStepTimes(animations);

    // Reduced motion turns the animations off; keep the plain image then.
    if (stepTimes.length < 2) {
      object.remove();
      this.restoreImage();
      return;
    }

    object.style.aspectRatio = svgAspectRatio(doc);
    object.classList.remove("diagram-stepper-loading");
    current.remove();
    this.object = object;
    this.animations = animations;
    this.loopMs = loopMs;
    this.stepTimes = stepTimes;
    this.step = Math.min(this.step, stepTimes.length - 1);
    this.renderBar();

    if (this.playing) {
      this.play();
    } else {
      this.pause();
      this.seek(this.step);
    }
  }

  restoreImage() {
    if (!this.object) return;

    this.object.before(this.imageLink);
    this.object.remove();
    // A re-inserted <picture> keeps its old source until the image reloads.
    this.image.src = this.image.getAttribute("src");
    this.object = null;
    this.bar?.remove();
    this.bar = null;
    cancelAnimationFrame(this.frame);
  }

  renderBar() {
    if (!this.bar) {
      this.bar = document.createElement("div");
      this.bar.className = "diagram-stepper";
      this.bar.setAttribute("role", "group");
      this.bar.setAttribute("aria-label", "Diagram steps");

      this.prevButton = button("Previous step", "←");
      this.timeline = document.createElement("div");
      this.timeline.className = "diagram-stepper-timeline";
      this.nextButton = button("Next step", "→");
      this.counter = document.createElement("span");
      this.counter.className = "diagram-stepper-count";
      this.playButton = button("Play", "Play");
      this.playButton.classList.add("diagram-stepper-play");

      this.prevButton.addEventListener("click", () => this.go(this.step - 1));
      this.nextButton.addEventListener("click", () => this.go(this.step + 1));
      this.playButton.addEventListener("click", () => (this.playing ? this.pause() : this.play()));
      this.bar.addEventListener("keydown", (event) => {
        if (event.key === "ArrowLeft") this.go(this.step - 1);
        else if (event.key === "ArrowRight") this.go(this.step + 1);
        else return;

        event.preventDefault();
      });

      this.bar.append(this.playButton, this.prevButton, this.timeline, this.nextButton, this.counter);
      const caption = this.figure.querySelector("figcaption");
      this.figure.insertBefore(this.bar, caption);
    }

    this.renderTimeline();
    this.updateCounter();
  }

  // One segment per step, as wide as the time that step takes in the animation.
  renderTimeline() {
    this.segments = this.stepTimes.map((_, step) => {
      const segment = document.createElement("button");
      segment.type = "button";
      segment.tabIndex = -1;
      segment.className = "diagram-stepper-segment";
      segment.setAttribute("aria-label", `Step ${step + 1}`);
      segment.style.flexGrow = this.stepSpan(step).duration;
      segment.addEventListener("click", () => this.go(step));

      return segment;
    });

    this.timeline.replaceChildren(...this.segments);
  }

  // The stretch of the loop that leads into and holds a step.
  stepSpan(step) {
    const end = this.stepTimes[step];
    const previous = this.stepTimes.at(step - 1);
    const start = step === 0 ? previous - this.loopMs : previous;

    return { start, duration: end - start };
  }

  go(step) {
    const count = this.stepTimes.length;
    this.pause();
    this.seek((step + count) % count);
  }

  seek(step) {
    this.step = step;

    for (const animation of this.animations) animation.currentTime = this.stepTimes[step];

    this.updateCounter();
  }

  play() {
    this.playing = true;

    for (const animation of this.animations) animation.play();

    this.playButton.textContent = "Pause";
    this.playButton.setAttribute("aria-label", "Pause");
    // Announcing every step while it plays would flood screen readers.
    this.counter.setAttribute("aria-live", "off");
    this.track();
  }

  pause() {
    this.playing = false;

    for (const animation of this.animations) animation.pause();

    cancelAnimationFrame(this.frame);
    this.playButton.textContent = "Play";
    this.playButton.setAttribute("aria-label", "Play");
    this.counter.setAttribute("aria-live", "polite");
  }

  // Follows the animation: the counter names the step being drawn, and the
  // current segment fills as that step plays out.
  track() {
    cancelAnimationFrame(this.frame);

    const update = () => {
      const time = this.animations[0].currentTime % this.loopMs;
      const next = this.stepTimes.findIndex((stepTime) => time <= stepTime);
      const step = next === -1 ? 0 : next;
      const { start, duration } = this.stepSpan(step);
      const elapsed = (time - start + this.loopMs) % this.loopMs;

      if (step !== this.step) {
        this.step = step;
        this.updateCounter();
      }

      this.segments[step].style.setProperty("--fill", `${Math.min(elapsed / duration, 1) * 100}%`);
      this.frame = requestAnimationFrame(update);
    };

    this.frame = requestAnimationFrame(update);
  }

  updateCounter() {
    const count = this.stepTimes.length;
    this.counter.textContent = `${this.step + 1} / ${count}`;
    this.counter.setAttribute("aria-label", `Step ${this.step + 1} of ${count}`);

    this.segments.forEach((segment, index) => {
      segment.classList.toggle("is-past", index < this.step);
      segment.toggleAttribute("aria-current", index === this.step);
      segment.style.setProperty("--fill", index === this.step ? "100%" : "0%");
    });
  }
}

const observer = new IntersectionObserver(
  (entries) => {
    for (const entry of entries) {
      if (!entry.isIntersecting) continue;

      observer.unobserve(entry.target);
      new DiagramStepper(entry.target).start();
    }
  },
  { rootMargin: "200px" },
);

for (const figure of document.querySelectorAll("figure[data-stepper]")) observer.observe(figure);
