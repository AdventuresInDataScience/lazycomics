---

# CBML — Comic Book Markup Language
## Specification v1.1

---

## Table of Contents

1. [Introduction](#1-introduction)
2. [Terminology](#2-terminology)
3. [File Format](#3-file-format)
4. [Document Structure](#4-document-structure)
5. [Comic Header](#5-comic-header)
6. [Page Declarations](#6-page-declarations)
   - 6.1 [Preset Layouts](#61-preset-layouts)
   - 6.2 [Custom Grid Layouts](#62-custom-grid-layouts)
   - 6.3 [Spread Pages](#63-spread-pages)
   - 6.4 [Panel Slot Notation](#64-panel-slot-notation)
7. [Panel Blocks](#7-panel-blocks)
   - 7.1 [loc](#71-loc)
   - 7.2 [chars](#72-chars)
   - 7.3 [shot](#73-shot)
   - 7.4 [mood](#74-mood)
   - 7.5 [Action Lines](#75-action-lines)
   - 7.6 [Dialogue Lines](#76-dialogue-lines)
   - 7.7 [Caption Boxes](#77-caption-boxes)
   - 7.8 [SFX](#78-sfx)
8. [Comments](#8-comments)
9. [Required Fields Summary](#9-required-fields-summary)
10. [Validation Rules](#10-validation-rules)
11. [Reserved Keywords](#11-reserved-keywords)
12. [Reading Order and Manga](#12-reading-order-and-manga)
13. [Versioning](#13-versioning)
14. [Examples](#14-examples)
    - 14.1 [Minimal Valid Document](#141-minimal-valid-document)
    - 14.2 [Single Page, Preset Layout](#142-single-page-preset-layout)
    - 14.3 [Multi-Page Action Sequence](#143-multi-page-action-sequence)
    - 14.4 [Splash Page with Narration](#144-splash-page-with-caption-narration)
    - 14.5 [Dialogue-Heavy Scene](#145-dialogue-heavy-scene)
    - 14.6 [Double-Page Spread with SFX](#146-double-page-spread-with-sfx)
    - 14.7 [Complete Short Comic](#147-complete-short-comic)

---

## 1. Introduction

CBML (Comic Book Markup Language) is a plain-text format for defining the content and visual layout of comic book pages. It is designed to be written by hand by a human author, and parsed deterministically by an implementing tool without ambiguity.

CBML separates two kinds of information:

- **Structured data** — panel positions, character identifiers, location identifiers, dialogue attribution. These fields use defined syntax and are parsed precisely.
- **Descriptive prose** — action descriptions, camera framing hints, mood and atmosphere. These fields use free natural-language text and are passed through to downstream systems without interpretation.

CBML is format-agnostic. Any conformant implementation may use the parsed output for any purpose — image generation, layout rendering, script export, accessibility tooling, or other applications. This specification describes the language only; it does not prescribe any implementation behaviour beyond parsing.

CBML files use the `.cbml` file extension.

---

## 2. Terminology

The key words **MUST**, **MUST NOT**, **REQUIRED**, **SHOULD**, **SHOULD NOT**, **RECOMMENDED**, **MAY**, and **OPTIONAL** in this document are to be interpreted as described in [RFC 2119](https://www.ietf.org/rfc/rfc2119.txt).

| Term | Definition |
|---|---|
| **Document** | A complete CBML file |
| **Header** | The metadata block at the top of the document |
| **Page** | A single comic book page, containing one or more panels |
| **Spread** | A multi-page page declaration: one logical page that occupies two or more consecutive physical pages |
| **Grid** | The column/row division system used to position panels on a page |
| **Slot** | A named region of the grid assigned to a panel |
| **Panel** | A single framed image unit within a page |
| **Aspect ratio** | The width-to-height proportion of a single physical page |
| **Sigil** | A reserved character or keyword that identifies a line's type |
| **Identifier** | A string used to reference a named external resource (character or location) |
| **Free-text field** | A field whose value is unrestricted natural language |

---

## 3. File Format

- Encoding: **UTF-8**
- Line endings: LF (`\n`) or CRLF (`\r\n`); both MUST be accepted
- Blank lines: ignored everywhere
- Trailing whitespace on any line: ignored
- Indentation: optional and has no semantic meaning; used for readability only
- Maximum line length: undefined; no limit is imposed

---

## 4. Document Structure

A CBML document consists of, in order:

1. A **Comic Header** (exactly one, REQUIRED, MUST be first)
2. One or more **Page Blocks** (REQUIRED)

Each Page Block consists of:

1. A **Page Declaration** (REQUIRED)
2. Zero or more **Panel Slot Definitions** (REQUIRED for custom grids; MUST NOT appear for presets)
3. One or more **Panel Blocks** (REQUIRED)

Each Panel Block consists of:

1. A **Panel Declaration** (REQUIRED)
2. One or more **Panel Fields** in any order (some REQUIRED, some OPTIONAL)

The overall shape of a document is therefore:

```
[Comic Header]

PAGE ...
  [Slot Definitions — custom grids only]

  PANEL X
    [Panel Fields]

  PANEL Y
    [Panel Fields]

PAGE ...
  PANEL A
    [Panel Fields]
```

---

## 5. Comic Header

The header MUST be the first non-comment, non-blank content in the document.

### Title

The title line MUST begin with `##` followed by a space and the comic title:

```
## The Last Signal
```

### Header Fields

Following the title, the header MUST contain an `aspect:` declaration and MAY contain any of the following other fields, each on its own line, in any order:

```
aspect: 2:3
author: Jane Doe
genre: sci-fi thriller
description: A story about orbital mechanics and memory.
```

| Field | Required | Value type |
|---|---|---|
| `aspect:` | REQUIRED | Aspect ratio (see below) |
| `author:` | OPTIONAL | Free text |
| `genre:` | OPTIONAL | Free text |
| `description:` | OPTIONAL | Free text |

Unknown fields in the header block SHOULD be ignored by conformant parsers with a warning.

The header block ends at the first `PAGE` declaration.

### Aspect Ratio

The `aspect:` field declares the width-to-height ratio of a single physical page. It is REQUIRED — without it the column/row layout of any page grid has no defined geometric meaning.

The value MAY be either:

- An integer pair in `W:H` form (e.g. `aspect: 2:3`, `aspect: 11:15`)
- A named preset (case-sensitive)

**Defined named presets:**

| Name | Ratio | Notes |
|---|---|---|
| `us-comic` | 2:3 | Standard US comic book trim |
| `manga-tankoubon` | 5:7 | Japanese tankōbon format |
| `european-album` | 11:15 | A4-leaning European album format |
| `square` | 1:1 | Square page |
| `web-vertical` | 9:16 | Mobile-first vertical format |

A parser MUST raise an error if `aspect:` is absent, malformed, or names an unknown preset.

For [spread pages](#63-spread-pages), the rendered canvas aspect is `(W × pages) : H` — the parser does not derive a separate spread aspect; consumers compute it from `aspect` and the spread's page count.

---

## 6. Page Declarations

A page is introduced by a `PAGE` declaration. Each `PAGE` declaration begins a new page and MUST specify a layout, either as a preset or a custom grid.

### 6.1 Preset Layouts

A preset layout is a named shorthand for a standard comic layout. Preset declarations take the form:

```
PAGE preset:NAME
```

Where `NAME` is one of the following defined values:

| Preset Name | Description | Panel Labels |
|---|---|---|
| `splash` | Single full-page panel | A |
| `strip-2` | Two equal horizontal panels | A, B |
| `strip-3` | Three equal horizontal panels | A, B, C |
| `strip-4` | Four equal horizontal panels | A, B, C, D |
| `grid-2x2` | 2 columns × 2 rows, all equal | A, B, C, D |
| `grid-2x3` | 2 columns × 3 rows, all equal | A, B, C, D, E, F |
| `grid-3x2` | 3 columns × 2 rows, all equal | A, B, C, D, E, F |
| `feature-left` | Large panel left, 2 equal panels stacked right | A, B, C |
| `feature-right` | 2 equal panels stacked left, large panel right | A, B, C |
| `feature-top` | Wide panel top, 3 equal panels below | A, B, C, D |
| `feature-bottom` | 3 equal panels top, wide panel below | A, B, C, D |
| `wide-top` | One wide top panel, 2 equal panels below | A, B, C |
| `wide-bottom` | 2 equal panels top, one wide bottom panel | A, B, C |

Panel labels for presets are assigned in reading order (left to right, top to bottom), starting at `A`. Slot definitions MUST NOT follow a preset declaration. A parser that encounters slot definitions after a preset declaration MUST raise an error.

### 6.2 Custom Grid Layouts

A custom grid declaration takes the form:

```
PAGE grid:COLSxROWS
```

Where `COLS` and `ROWS` are positive integers defining the number of columns and rows in the grid. This MUST be followed by one or more panel slot definitions before the first `PANEL` declaration of that page.

Example:

```
PAGE grid:3x2
  A: [1-2, 1]
  B: [3, 1-2]
  C: [1, 2]
  D: [2, 2]
```

### 6.3 Spread Pages

A spread is a single logical page whose artwork occupies **two or more consecutive physical pages** — typically a double-page spread across facing pages. A spread declaration takes the form:

```
PAGE spread:N
```

Or, with an internal grid:

```
PAGE spread:N grid:COLSxROWS
```

Where:
- `N` is a positive integer ≥ 2 specifying how many physical pages the spread occupies (almost always `2`)
- `COLS` and `ROWS` are positive integers defining an internal grid that subdivides the spread

**Semantics:**

- A spread consumes `N` physical page numbers. If a comic contains pages `[single, spread:2, single]`, those pages are numbered 1, 2–3, and 4 respectively. The parser exposes `page.index` (zero-indexed first physical page) and `page.span` (the value of `N`).
- The default form `PAGE spread:N` (no `grid:` clause) is equivalent to `PAGE spread:N grid:1x1` — a single panel `A` that fills the entire spread. Authors MAY omit slot definitions in this case.
- When `grid:COLSxROWS` is given with non-trivial dimensions, the spread MUST be followed by one or more slot definitions, exactly as with [Custom Grid Layouts](#62-custom-grid-layouts).
- A spread's columns span the **entire spread canvas**, not a single page. A `spread:2 grid:2x1` therefore yields two panels each occupying one physical page of the spread.

Example — full-bleed double-page spread:

```
PAGE spread:2

PANEL A
loc: battlefield_dawn
> The two armies meet on the plain at first light.
```

Example — double-page spread with two panels:

```
PAGE spread:2 grid:2x1
  A: [1, 1]
  B: [2, 1]

PANEL A
loc: battlefield_dawn
> The hero's army crests the eastern ridge.

PANEL B
loc: battlefield_dawn
> The enemy host floods the western valley below.
```

### 6.4 Panel Slot Notation

Each slot definition maps a label to a cell range within the grid:

```
LABEL: [COL_RANGE, ROW_RANGE]
```

- `LABEL`: An uppercase letter or short alphanumeric string, unique within the page (e.g. `A`, `B`, `P1`, `P2`)
- `COL_RANGE`: Either a single column number (`2`) or a range (`1-3`)
- `ROW_RANGE`: Either a single row number (`1`) or a range (`1-2`)

Column and row indices begin at `1`. A range `X-Y` is inclusive of both endpoints. `X` MUST be less than `Y`.

**Examples:**

| Slot definition | Meaning |
|---|---|
| `A: [1, 1]` | Column 1, row 1 (single cell) |
| `A: [1-2, 1]` | Columns 1–2, row 1 (horizontal span) |
| `A: [3, 1-2]` | Column 3, rows 1–2 (vertical span) |
| `A: [1-2, 1-2]` | Columns 1–2, rows 1–2 (block span) |

Slots MUST NOT overlap. A parser MUST raise an error if any two slots on the same page share one or more cells. Every cell in the grid SHOULD be covered by exactly one slot. Uncovered cells are permitted but SHOULD generate a parser warning.

---

## 7. Panel Blocks

A panel block begins with a panel declaration and ends at the next `PANEL` declaration, `PAGE` declaration, or end of file.

Panel declaration syntax:

```
PANEL LABEL
```

Where `LABEL` matches a slot defined in the current page's grid or preset. A parser MUST raise an error if the label is not defined for the current page.

### 7.1 loc

**REQUIRED.**

Specifies the location for this panel. Format:

```
loc: IDENTIFIER_OR_DESCRIPTION
```

The value MAY be either:
- A short identifier matching an external location resource (e.g. a reference image filename without extension): `warehouse_night`
- A free-text description if no external resource is available: `a rain-soaked harbour at dawn, industrial and grey`

The distinction between identifier and free-text is left to the implementing system. The parser MUST treat the entire value as a string and pass it through without modification.

A parser MUST raise an error if `loc:` is absent from a panel block.

### 7.2 chars

**REQUIRED if any dialogue lines are present. OPTIONAL otherwise.**

Specifies the characters appearing in this panel as a comma-separated list of identifiers:

```
chars: REX, NOVA
chars: COMMANDER_YASH
```

Character identifiers SHOULD match external character resource names (e.g. reference image filenames without extension), case-insensitively. Whitespace around commas is ignored. Order has no defined semantic meaning.

A parser MUST raise an error if:
- A dialogue line names a character not present in `chars:`
- Dialogue lines are present but `chars:` is absent

A parser SHOULD issue a warning if a character identifier does not match any known external resource, but MUST NOT raise an error — the document remains valid.

### 7.3 shot

**OPTIONAL.**

A free-text hint describing the camera framing, angle, or composition of the panel:

```
shot: extreme closeup on hands
shot: wide establishing, low angle looking up
shot: over-the-shoulder, REX's POV
shot: bird's eye view, two figures dwarfed by the space around them
```

The value is free text and MUST be passed through without modification. If absent, the implementing system MAY infer an appropriate framing from other panel fields.

### 7.4 mood

**OPTIONAL.**

A free-text hint describing the emotional atmosphere, lighting feel, or tonal quality of the panel:

```
mood: tense, cold, oppressive
mood: warm and nostalgic, late afternoon gold
mood: quiet dread
```

The value is free text and MUST be passed through without modification.

### 7.5 Action Lines

**OPTIONAL, RECOMMENDED.**

Lines beginning with `>` followed by a space describe what is physically happening in the panel. These are the primary visual description and serve as the core input for image generation or scene rendering.

```
> Rex backs against the wall as Nova raises her weapon, her expression unreadable
```

Multiple action lines are permitted and MUST be treated as a single concatenated description, joined by a space:

```
> The warehouse stretches back into darkness
> Shafts of moonlight cut through the broken skylights
> Two figures, small against the scale of the space
```

Action lines MAY appear anywhere within the panel block alongside other fields. Their order relative to other field types is not significant, but their order relative to each other is preserved.

### 7.6 Dialogue Lines

**OPTIONAL.**

Dialogue lines attribute spoken or thought text to a named character. The general form is:

```
CHARACTER_NAME: BUBBLE_TYPE_MARKER "text" BUBBLE_TYPE_MARKER
```

The character name MUST match an entry in the panel's `chars:` field, case-insensitively.

Four bubble types are defined:

| Type | Syntax | Description |
|---|---|---|
| Speech | `NAME: "text"` | Standard speech bubble |
| Thought | `NAME: ~text~` | Thought or internal monologue bubble |
| Shout | `NAME: !"text"!` | Emphasis/shout bubble, typically rendered with jagged border |
| Whisper | `NAME: ?"text"?` | Whisper bubble, typically rendered with dashed border |

Multiple dialogue lines from the same character are permitted and MUST be treated as separate bubbles, rendered in document order.

```
NOVA: "Stay behind me."
NOVA: "Don't breathe."
```

### 7.7 Caption Boxes

**OPTIONAL.**

Caption boxes are rectangular text overlays on the panel, commonly used for narrator text, location/time stamps, or internal monologue not attributed to a character present in the panel.

Basic form:

```
[caption] Text content here.
```

Captipn boxes MAY include display attributes within the brackets, before the closing `]`:

```
[caption bg:#1a1a2e color:#ffffff pos:top-left] Three hours earlier...
```

**Defined attributes:**

| Attribute | Value format | Default | Description |
|---|---|---|---|
| `bg` | Hex colour (`#rrggbb`) | `#000000` | Background colour of the caption box |
| `color` | Hex colour (`#rrggbb`) | `#ffffff` | Text colour |
| `pos` | See position values below | `top-left` | Position of the box within the panel |

**Defined position values:**

`top-left` · `top-center` · `top-right` · `bottom-left` · `bottom-center` · `bottom-right`

Attribute order within the brackets is not significant. Unknown attributes SHOULD generate a parser warning and MUST be ignored. An invalid hex colour value (not matching `#[0-9a-fA-F]{6}`) MUST generate a parser error.

Multiple caption boxes are permitted per panel and MUST be preserved in document order.

### 7.8 SFX

**OPTIONAL.**

SFX (sound effects) are stylised text overlaid on panel art — for example *BOOM!*, *CRASH!*, *tap tap tap*. Unlike captions, SFX render as free-floating typography directly over the artwork and have **no background box**.

Basic form:

```
[sfx] BOOM!
```

SFX boxes MAY include display attributes within the brackets, before the closing `]`:

```
[sfx pos:center color:#ff0000] CRASH!
```

**Defined attributes:**

| Attribute | Value format | Default | Description |
|---|---|---|---|
| `color` | Hex colour (`#rrggbb`) | `#000000` | Text colour |
| `pos` | See position values below | `center` | Position of the SFX within the panel |

**Defined position values:**

`top-left` · `top-center` · `top-right` · `bottom-left` · `bottom-center` · `bottom-right` · `center`

SFX support the same six anchor positions as captions, plus `center` for SFX that float over the action.

Attribute order within the brackets is not significant. Unknown attributes SHOULD generate a parser warning and MUST be ignored. An invalid hex colour value MUST generate a parser error.

Multiple SFX are permitted per panel and MUST be preserved in document order. SFX MUST NOT carry a `bg:` attribute — SFX are by definition unboxed; a parser SHOULD warn if one is given.

---

## 8. Comments

Lines where the first non-whitespace character is `#` are comments. Comments MUST be ignored by the parser entirely. Comments MAY appear anywhere in the document including within panel blocks and between slot definitions.

```
# This is a comment
## This is the comic title      ← NOT a comment; ## is the title sigil
```

A `#` character that is not the first non-whitespace character on a line is treated as literal text, not a comment.

---

## 9. Required Fields Summary

| Field | Scope | Required |
|---|---|---|
| `## Title` | Document | REQUIRED |
| `aspect:` | Document (header) | REQUIRED |
| `PAGE` declaration | Document | At least one REQUIRED |
| Slot definitions | Page (custom grid; spread with non-1×1 grid) | REQUIRED |
| `PANEL` declaration | Page | At least one REQUIRED per page |
| `loc:` | Panel | REQUIRED |
| `chars:` | Panel | REQUIRED if dialogue present |
| `shot:` | Panel | OPTIONAL |
| `mood:` | Panel | OPTIONAL |
| `>` action line | Panel | OPTIONAL |
| Dialogue lines | Panel | OPTIONAL |
| Caption boxes | Panel | OPTIONAL |
| SFX | Panel | OPTIONAL |

---

## 10. Validation Rules

A conformant parser MUST enforce the following. Violations marked **ERROR** MUST halt parsing or flag the document as invalid. Violations marked **WARNING** MUST be reported but MUST NOT prevent parsing.

| Rule | Level |
|---|---|
| Document must begin with `## Title` | ERROR (parse-time) |
| Document header must declare `aspect:` | ERROR (parse-time) |
| `aspect:` value must be a valid `W:H` pair or a defined preset name | ERROR (parse-time) |
| Document must contain at least one `PAGE` declaration | ERROR |
| Each page must contain at least one `PANEL` block | ERROR |
| `PANEL` label must reference a defined slot on the current page | ERROR |
| All defined slots must have a corresponding `PANEL` block | ERROR |
| `loc:` must be present in every panel | ERROR |
| `chars:` must be present if dialogue lines exist | ERROR |
| Every dialogue speaker must be listed in `chars:` | ERROR |
| Slot definitions must not overlap | ERROR |
| Slot definitions must not follow a preset `PAGE` declaration | ERROR |
| Slot definitions must not follow a `spread:N` declaration with default 1×1 grid | ERROR |
| `spread:N` value `N` must be an integer ≥ 2 | ERROR |
| Column/row indices must be positive integers; range start must be less than end | ERROR |
| Caption `bg` and `color` attributes must be valid hex colour codes | ERROR |
| Caption `pos` attribute must be one of the six defined position values | ERROR |
| SFX `color` attribute must be a valid hex colour code | ERROR |
| SFX `pos` attribute must be one of the seven defined position values | ERROR |
| Character identifier does not match any known external resource | WARNING |
| Location identifier does not match any known external resource | WARNING |
| Unknown attribute in caption or SFX box | WARNING |
| Unknown field in header block | WARNING |
| Grid cell not covered by any slot | WARNING |

---

## 11. Reserved Keywords

The following tokens are reserved and MUST NOT be used as panel labels, character identifiers, or location identifiers:

`PAGE` · `PANEL` · `preset` · `grid` · `spread` · `loc` · `chars` · `shot` · `mood` · `author` · `genre` · `description` · `aspect` · `caption` · `sfx`

---

## 12. Reading Order and Manga

CBML documents are written in **western reading order** — left to right, top to bottom. Pages are numbered from first to last, and panels within each page are labelled in left-to-right, top-to-bottom order starting at `A`.

This convention is the canonical authoring format. **All CBML documents MUST be written in western reading order**, regardless of the intended final output format.

### Writing a Manga-Style Comic

Manga and other right-to-left formats read pages from back to front, and panels from right to left within each page. To produce a manga-style comic from CBML:

1. **Write your script in forward (western) page order.** Page 1 is the first page the reader encounters narratively — even though in a printed manga it would appear at the "back" of the physical book.

2. **Use the parser's `manga` option** to transform the parsed structure into manga reading order at parse time. The parser will:
   - **Reverse page order** — the last page becomes the first.
   - **Horizontally mirror panel positions** — panels on the left of the grid move to the right, and vice versa.
   - **Mirror caption positions** — e.g. `top-left` becomes `top-right`.

   ```python
   parser = CBMLParser()
   comic = parser.parse_file("my_manga.cbml", manga=True)
   ```

3. Alternatively, if you prefer to write your script in the "physical" manga order (last narrative page first), you MAY do so and parse without the `manga` flag. However, the recommended approach is to write in narrative order and let the parser handle the transformation — this keeps the source document readable as a linear story.

The `manga` transformation is purely structural. It does not alter dialogue text, action descriptions, character lists, or any other content — only page sequencing, slot column positions, and caption placement.

> **Note:** The `make_manga()` method can also be called independently on an already-parsed `Comic` object if the transformation needs to be applied after the fact. See the [API Reference](./API_REFERENCE.md) for details.

---

## 13. Versioning

This document describes CBML version **1.1**. Future versions will increment the minor version for backwards-compatible additions and the major version for breaking changes. A CBML document does not currently embed a version declaration; this may be introduced in a future version.

**Changes from v1.0:**

- **REQUIRED** `aspect:` field in the comic header. Documents valid under v1.0 that lack `aspect:` are no longer valid under v1.1. This is the only breaking change in v1.1.
- Added `PAGE spread:N` declaration for multi-page spreads, optionally subdivided with an internal `grid:CxR`.
- Added `[sfx]` panel field for stylised sound-effect text overlays. SFX support a new `center` position value in addition to the six caption anchors.
- Added reserved keywords: `spread`, `aspect`, `sfx`.

---

## 14. Examples

### 14.1 Minimal Valid Document

The smallest possible valid CBML document:

```
## Untitled
aspect: us-comic

PAGE preset:splash

PANEL A
loc: a dark room
> A single figure stands in the darkness.
```

---

### 14.2 Single Page, Preset Layout

A quiet domestic scene. Two characters, four equal panels.

```
## The Morning After
aspect: us-comic
author: J. Okafor
genre: drama

PAGE preset:grid-2x2

PANEL A
loc: apartment_kitchen
chars: MAYA
shot: medium shot, profile
mood: quiet, melancholy, early morning light
> Maya stands at the kitchen counter, hands wrapped around a coffee mug, staring out the window at the grey city below
[caption bg:#111111 color:#cccccc pos:top-left] 6:43 AM.

PANEL B
loc: apartment_kitchen
chars: DANIEL
shot: medium shot from doorway, slightly out of focus foreground
mood: tentative, soft
> Daniel appears in the kitchen doorway, still in yesterday's clothes, hair dishevelled

PANEL C
loc: apartment_kitchen
chars: MAYA, DANIEL
shot: two-shot, facing each other across the kitchen
mood: heavy silence
> They look at each other. Neither speaks first.

PANEL D
loc: apartment_kitchen
chars: MAYA, DANIEL
shot: closeup on Maya's hands around the mug
mood: resignation
> Her knuckles are white.
MAYA: "You should probably go."
```

---

### 14.3 Multi-Page Action Sequence

A chase through a rainy city. Irregular layouts, fast pacing.

```
## Ghost Signal
aspect: 2:3
author: T. Navarro
genre: cyberpunk thriller

PAGE grid:3x2
  A: [1-2, 1]
  B: [3, 1-2]
  C: [1, 2]
  D: [2, 2]

PANEL A
loc: neon_alley_rain
chars: KAI
shot: wide, low angle, running toward camera
mood: desperate, urgent, rain-soaked
> Kai sprints through the neon-lit alley, puddles exploding underfoot, colour bleeding across the wet asphalt
[caption bg:#0d0d1a color:#ff4444 pos:top-left] Three minutes behind her. And closing.

PANEL B
loc: neon_alley_rain
chars: AGENT_CROSS
shot: closeup face, rain running down visor
mood: cold, mechanical, relentless
> Agent Cross raises one fist — a silent signal to the units behind

PANEL C
loc: neon_alley_rain
chars: KAI
shot: over-the-shoulder, glancing back
mood: fear breaking through adrenaline
> Kai looks back — blue strobes, closer than before

PANEL D
loc: neon_alley_rain
chars: KAI
shot: extreme closeup, eyes wide
> Her eyes: calculating, terrified, alive
KAI: ~Left. Always go left. Ghost taught me that.~

# ── Page 2 ─────────────────────────────────────

PAGE grid:2x3
  A: [1, 1-2]
  B: [2, 1]
  C: [2, 2]
  D: [1-2, 3]

PANEL A
loc: construction_site_night
chars: KAI
shot: full body, dynamic leap
mood: kinetic, wild
> Kai launches herself off the fence and into the darkness of the construction site

PANEL B
loc: construction_site_night
chars: AGENT_CROSS
shot: medium shot, arriving at fence, scanning
> Cross reaches the fence and stops
AGENT_CROSS: ?"Grid search. She's still inside."?

PANEL C
loc: construction_site_night
chars: KAI
shot: tight, hiding in shadow behind a concrete pillar
mood: held breath
> Kai presses flat against the pillar, eyes shut, counting

PANEL D
loc: construction_site_night
chars: KAI, AGENT_CROSS
shot: wide, two figures on opposite ends of a vast dark floor, unaware of each other's exact position
mood: tense standoff in the dark
> Fifty metres of darkness, exposed rebar, and silence between them
[caption pos:bottom-center] Neither of them was breathing.
```

---

### 14.4 Splash Page with Caption Narration

A full-page image with layered narrator captions. No dialogue.

```
## The Last Orbit
aspect: us-comic
author: S. Brennan
genre: science fiction

PAGE preset:splash

PANEL A
loc: space_earth_below
chars: COMMANDER_YASH
shot: extreme wide — astronaut tiny against the curvature of the Earth, backlit by the sun
mood: immense, lonely, beautiful, the end of something
> Commander Yash floats in the silence beyond the airlock. Below her, the Earth turns slowly, impossibly blue. She is the last person who knows what happened.
[caption bg:#000000 color:#aaddff pos:top-left] She had been up here for 312 days.
[caption bg:#000000 color:#aaddff pos:top-left] The last transmission from the surface came on day 301.
[caption bg:#000000 color:#ffffff pos:bottom-right] She decided, that morning, to come home anyway.
```

---

### 14.5 Dialogue-Heavy Scene

An interrogation. Tight framing, varied bubble types, expressive caption use.

```
## Salt
aspect: us-comic
author: M. Oduya
genre: crime noir

PAGE grid:3x3
  A: [1-3, 1]
  B: [1, 2]
  C: [2, 2]
  D: [3, 2]
  E: [1-2, 3]
  F: [3, 3]

PANEL A
loc: interrogation_room
chars: DETECTIVE_HARA, VINCENT
shot: wide two-shot across the table, harsh overhead light
mood: clinical, the room itself feels like a weapon
> Detective Hara and Vincent sit across from each other. The table between them is bolted to the floor.
[caption bg:#1a1a1a color:#ffffff pos:top-left] Room 4. 11:58 PM.

PANEL B
loc: interrogation_room
chars: DETECTIVE_HARA
shot: medium closeup, leaning forward, fingers laced
mood: controlled aggression
> Hara leans in
DETECTIVE_HARA: "You were seen at the harbour."
DETECTIVE_HARA: "Don't tell me you were there for the view."

PANEL C
loc: interrogation_room
chars: VINCENT
shot: medium shot, arms crossed, half in shadow
mood: studied indifference
> Vincent says nothing. He examines the ceiling.

PANEL D
loc: interrogation_room
chars: DETECTIVE_HARA
shot: extreme closeup on eyes
mood: patience as a weapon
> She has done this a thousand times.
DETECTIVE_HARA: ?"The silence tells me everything, Vincent."?

PANEL E
loc: interrogation_room
chars: VINCENT
shot: medium shot, finally meeting her eyes
mood: the mask slipping, just slightly
> For the first time, Vincent looks directly at her.
VINCENT: "You want to know what I saw at the harbour?"
VINCENT: "You really, really don't."

PANEL F
loc: interrogation_room
chars: DETECTIVE_HARA
shot: tight closeup, profile, expression unreadable
> Hara reaches into her folder and places a photograph on the table, face down.
```

---

### 14.6 Double-Page Spread with SFX

A two-page spread subdivided into two panels, with overlaid sound effects.

```
## Iron Tide
aspect: us-comic
author: K. Vance
genre: epic fantasy

PAGE spread:2 grid:2x1
  A: [1, 1]
  B: [2, 1]

PANEL A
loc: battlefield_dawn
chars: KAIRA
shot: wide low angle along the ridge line, cavalry silhouetted against the rising sun
mood: defiant, vast, the calm before
> Kaira's host crests the eastern ridge — banners red against the dawn.
[sfx pos:top-left color:#cccccc] tk-tk-tk-tk
[sfx pos:bottom-center color:#a00000] HOOOOOOM
KAIRA: !"FOR THE NINTH GATE!"!

PANEL B
loc: battlefield_dawn
chars: WARLORD_REN
shot: extreme wide — the valley below choked with the enemy host, dust rising
mood: dread, scale, the inevitable
> The valley below boils with armoured ranks, twenty thousand strong.
[sfx pos:center color:#1a1a1a] DOOM
[sfx pos:bottom-right] tap tap tap tap
WARLORD_REN: ~Let them come.~
```

---

### 14.7 Complete Short Comic

A three-page complete story demonstrating the full feature set — custom grids, presets, splash pages, captions, dialogue, thought bubbles, and varied mood and shot descriptions.

```
## The Signal at Pier 9
aspect: us-comic
author: A. Reyes
genre: mystery, coastal noir
description: A lighthouse keeper receives a transmission from a ship that sank forty years ago.

# ── Page 1 ─────────────────────────────────────────

PAGE preset:feature-top

PANEL A
loc: pier9_night_fog
chars: ELARA
shot: extreme wide — a tiny figure on a long empty pier, fog swallowing everything beyond twenty metres
mood: isolated, eerie, quietly beautiful
> Elara walks the length of Pier 9 alone, as she has every night for eleven years. The fog is thicker than usual tonight.
[caption bg:#0a0f14 color:#a0c8d8 pos:top-left] Pier 9. Northern Maine.
[caption bg:#0a0f14 color:#a0c8d8 pos:top-left] November. 2:17 AM.

PANEL B
loc: pier9_night_fog
chars: ELARA
shot: medium shot, stopping mid-step, head tilted
mood: the moment before something changes
> She stops. She heard something.

PANEL C
loc: pier9_night_fog
chars: ELARA
shot: closeup on face, fog backlit by distant lighthouse beam
> Her expression shifts — not fear. Recognition.

# ── Page 2 ─────────────────────────────────────────

PAGE grid:2x3
  A: [1, 1-2]
  B: [2, 1]
  C: [2, 2]
  D: [1-2, 3]

PANEL A
loc: lighthouse_radio_room
chars: ELARA
shot: medium wide, Elara standing over old radio equipment, analogue dial glowing
mood: tense focus, the room held in amber
> She is in the radio room now. The old equipment — decommissioned since the eighties — is active. The needle is moving.

PANEL B
loc: lighthouse_radio_room
chars: ELARA
shot: extreme closeup on radio dial, needle trembling
mood: impossible, wrong in the best way
> The dial reads the frequency of the Maren Clare. The ship that sank in 1983.

PANEL C
loc: lighthouse_radio_room
chars: ELARA
shot: closeup, receiver held to her ear with both hands
ELARA: ~Say something. Please say something.~

PANEL D
loc: lighthouse_radio_room
chars: ELARA
shot: wide — Elara small amid the old equipment, a single lamp, night pressing at the windows
mood: the whole room is listening
> Static. Then beneath it, something that might be a voice.
[caption bg:#0a0f14 color:#a0c8d8 pos:bottom-left] She would later be unable to explain what she heard.
[caption bg:#0a0f14 color:#a0c8d8 pos:bottom-right] She would not try very hard.

# ── Page 3 ─────────────────────────────────────────

PAGE preset:splash

PANEL A
loc: pier9_dawn
chars: ELARA
shot: wide — standing at the end of the pier facing open ocean, dawn breaking cold gold on the horizon, fog lifting
mood: resolution without answers, the peace of accepting mystery
> By the time dawn came, the radio was silent. Elara stood at the end of the pier and watched the light arrive. She was smiling. She didn't know why.
[caption bg:#0a0f14 color:#e8d4a0 pos:top-right] The harbour master's log noted nothing unusual that night.
[caption bg:#0a0f14 color:#e8d4a0 pos:top-right] Elara didn't file a report.
[caption bg:#0a0f14 color:#ffffff pos:bottom-center] Some signals aren't meant to be explained. Only received.
```