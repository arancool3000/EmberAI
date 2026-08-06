# Prompt for ChatGPT — Ember visual redesign

Paste everything below the line.

---

You are an art director and UI designer. I need a visual direction for **Ember**, a macOS/Windows
desktop AI assistant that can see the screen and control the mouse, keyboard, files and browser.
It is built in **PyQt6**, and all visuals are drawn with **QPainter** or styled with **Qt Style
Sheets** — so give me things I can implement in code, not Figma files or raster art.

## What Ember is

A local-first agent. Its selling point is that it works on *your* machine and encrypts your data
before it reaches iCloud. The mood should be: **warm, alive, competent, unhurried**. Not
corporate SaaS blue. Not cyberpunk neon. Think a hearth in a well-made room.

The name is Ember. The current palette is a dark slate (`#0d1018` background, `#151a25` cards,
`#e9edf5` text, `#91afff` accent-blue, `#8f99ad` muted).

## What I want from you

### 1. An oranger theme

The accent is currently a cold blue, which fights the name. Give me a **warm** system:

- A full palette: background, raised surface, border, primary text, muted text, primary accent,
  hover/pressed states, and semantic colours for success / warning / danger.
- The accent should read as ember/flame — amber through to a deep coal orange — **without**
  becoming a "warning yellow" that users read as an alert.
- It must survive on a dark background at 11px muted-label size. Tell me the contrast ratios
  against the background and confirm they clear **WCAG AA (4.5:1 for body, 3:1 for large)**.
- Give me hex values in a table I can paste into a stylesheet.
- Warn me where an orange accent will collide with genuine warning/error states, and say how you
  resolve it.

### 2. A flame animation that feels right

There is a live fire simulation painted along the bottom of the settings window. It is a heat
grid (each cell takes the heat of the cell below minus a random decay, drifting sideways with a
wind term), rendered to a small image and smooth-scaled up.

Current complaints from the user, verbatim: **"too blurry, small and too fast — it feels wrong."**

I have already raised the grid to 320x180, dropped a double-scaling pass that was softening
everything, slowed it from 20fps to 12fps, and made the bed occupy 42% of the window height.
Tell me whether those are the right moves and what else matters. Specifically:

- What **frame rate** does fire need to read as burning rather than flickering? Does a real
  hearth want motion blur between frames, or does that just re-introduce the mush?
- The palette maps heat 0→255 to colour. Give me a **fire gradient** that looks like embers
  rather than a Photoshop "fire" filter: where should it go from black to deep red to orange to
  a pale core, and how much of the range should be spent in each band?
- Flames need **silhouette** — readable tongues with dark gaps between them. What does the decay
  and lateral-drift tuning need to look like to get separation rather than an even orange wash?
- It sits *behind* interactive controls. How do I keep it from competing with them? Give me a
  specific approach to opacity/falloff at the top edge.
- Should the fire respond to state — brighter when Ember is working, embers when idle? If so,
  what should the transition look like, and is that a good idea or a gimmick?

### 3. (Removed — do not design a cursor)

Ember used to draw its own click-through pointer alongside the user's. It read as an oversized
growth attached to their real cursor rather than a second, separate one, and the whole feature
has been deleted. Ember now drives the one system cursor like any other automation tool.

**Do not propose a custom cursor, an overlay, or a pointer "marker".** If you think the user
needs to see where the agent is acting, say so and suggest something that is not a second
cursor — but the default answer is that the operating system already draws a perfectly good
pointer and we should use it.

## Constraints, please respect them

- Drawable with QPainter: paths, gradients, pens, brushes, opacity. No shaders, no video, no
  external image assets, no icon fonts.
- No per-frame cost that would show up on a laptop battery. The fire already runs at well under
  a millisecond per frame; keep it there.
- The app is used by people doing real work. Nothing may pulse, strobe or move in a way that
  pulls the eye during typing. Assume some users are sensitive to motion — tell me what the
  reduced-motion version of each of these is.
- Accessibility is not optional: state contrast ratios, do not encode meaning in colour alone.

## Output format

1. **Palette table** — hex, role, contrast ratio vs background.
2. **Flame spec** — gradient stops with heat positions, frame rate, decay/drift numbers, edge
   falloff, and the reduced-motion variant.
3. **What you would cut.** Tell me which of the current effects are earning their keep and
   which are noise. I would rather have three things that look deliberate than eight that look
   busy — the custom cursor has already gone for exactly that reason.

Be opinionated. If a request of mine is wrong, say so and tell me what to do instead.
