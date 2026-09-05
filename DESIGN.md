---
version: alpha
name: "Open Simple Agent Control Panel"
description: "A quiet operations desk for making agent, resource, and deployment state legible at a glance."
colors:
  primary: "#174EA6"
  background: "#F4F6F8"
  surface: "#FFFFFF"
  ink: "#17202A"
  muted: "#667085"
  border: "#D8DEE4"
  focus: "#1C64F2"
  success: "#17653A"
  warning: "#7A5A00"
  danger: "#A32116"
  dark-background: "#11161C"
typography:
  sans:
    fontFamily: "Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, \"Segoe UI\", sans-serif"
  mono:
    fontFamily: "ui-monospace, SFMono-Regular, Menlo, Consolas, monospace"
rounded:
  DEFAULT: "0.5rem"
  sm: "0.25rem"
  md: "0.5rem"
  lg: "0.8rem"
spacing:
  control-height: "2.5rem"
  content-padding: "2rem"
  mobile-content-padding: "1.2rem"
  section-gap: "1.75rem"
  page-max: "1400px"
components:
  button:
    minHeight: "2.5rem"
    radius: "0.5rem"
  card:
    radius: "0.8rem"
    border: "1px solid #D8DEE4"
  table:
    radius: "0.8rem"
    rowPadding: "0.85rem 1rem"
  status:
    radius: "999px"
    fontSize: "0.8rem"
---

# Open Simple Agent Control Panel Design System

## Overview

### Creative North Star

The panel is a quiet operations desk: a practical control room where an
operator can tell what is healthy, what is changing, and what needs a
decision without visual noise. The existing navy action color, pale system
surfaces, and compact metadata rows are the product's established language;
this file records that language rather than introducing a marketing redesign.

### Product context and register

- **Audience and primary job:** Operators and developers manage agent
  definitions, resources, deployments, versions, and runtime access.
- **Target market(s) and evidence:** General technical users; the repository
  product definition and API contract are the maintained evidence. No
  Japan-specific market behavior is currently defined.
- **Locale(s) and language policy:** English UI copy is the current supported
  locale. Dates and numeric values use browser `Intl` formatting; API values
  remain machine-stable. New locale support must centralize messages and
  preserve accessible names and validation meaning.
- **Usage scene:** Frequent desktop use with narrow-screen inspection and
  keyboard navigation for operational workflows.
- **Register:** Product/admin. Familiarity, state clarity, and recovery win
  over decorative expression.
- **Memorable signature:** A restrained navy action rail and compact status
  pills make lifecycle state readable without turning the panel into a
  dashboard collage.
- **Restraint:** Tables, forms, errors, and destructive actions stay quiet and
  explicit. No gradients, decorative illustrations, or color-only meaning.
- **Anti-references:** Do not resemble a marketing landing page, a dense
  spreadsheet, or a neon monitoring wall; each would obscure the operator's
  next safe action.
- **Token ownership/runtime mapping:** This file mirrors the established
  values in `control-plane/frontend/src/styles.css`; CSS remains the runtime
  source today. A future token export must update both files and add a drift
  check.

## Colors

`primary` is reserved for the main action and active navigation. `surface`
and `background` establish a low-contrast page layer; `border` separates
regions without heavy shadows. `ink` is the main reading color and `muted` is
for supporting labels only. `success`, `warning`, and `danger` are semantic
status colors and always include text or structure in addition to color.
Dark mode keeps the same semantic hierarchy on `dark-background`, with lighter
muted text and explicit focus rings. Focus uses `focus` and must remain visible
against both themes.

## Typography

Inter is the established UI face, with the system stack as a resilient
fallback. The sans face handles headings, labels, actions, and prose. The mono
stack is reserved for IDs, URLs, JSON, logs, and other technical values. Upper
case is limited to eyebrow labels and table headings; user-facing actions use
sentence case. `Intl.DateTimeFormat` owns localized timestamps, while raw ISO
values remain in `datetime` attributes for assistive technology and tools.

## Layout

The shell uses a sticky top bar, a 220px navigation rail on wide screens, and
a natural document-scrolling content region capped at 1400px. Cards use
auto-fit grids with a 280px minimum; detail grids use a minimum of
`min(300px, 100%)` so 320px viewports do not overflow. At 760px the rail
becomes a horizontal navigation strip, padding contracts to 1.2rem, and form
controls stack. Tables own horizontal scrolling rather than shrinking text
below a readable size.

## Elevation & Depth

Hierarchy comes from tonal surfaces and 1px borders. Static content does not
need a shadow. The top bar uses a translucent surface and backdrop blur only
to preserve context while scrolling. Logs and JSON use dark technical
surfaces; their contrast is independent of the page theme.

## Shapes

Controls use a 0.5rem radius, cards and table shells use 0.8rem, and status
pills use a full radius. Borders are the primary dividers. Dangerous actions
use the same control geometry as safe actions but a distinct semantic color
and an explicit confirmation step.

## Components

### Foundational visual states

Loading, empty, error, success, and status states use the shared `.state-card`,
status, and button language. Every async state has text; errors retain a
visible retry or correction path. Disabled controls use the native `disabled`
attribute, reduced opacity, and `not-allowed` cursor. `prefers-reduced-motion`
must not remove information or make an operation harder to follow.

### Buttons and actions

Solid navy buttons are primary actions; white bordered buttons are secondary;
danger buttons are reserved for destructive lifecycle operations. Buttons keep
their geometry while busy and expose the busy state in text. Native buttons
are used for actions and links for navigation.

### Navigation and data display

Navigation is a semantic `<nav>` with active links. Read-only data uses native
tables or compact definition lists. Status pills are always paired with text.
Long IDs, URLs, JSON, and logs wrap or scroll without changing the page's
global scroll ownership.

### Forms and overlays

Forms use real labels and native controls with app-owned validation. Errors are
inline and announced with `role="alert"`; loading and success messages use
status semantics. The product currently uses inline confirmation groups for
archive and rollback rather than a browser dialog. No browser `alert`,
`confirm`, or `prompt` is allowed.

### Iconography

The panel currently uses text labels and typographic arrows instead of an icon
library. Text remains mandatory for all operational actions; any future icon
must have an accessible name and a visible focus state.

### Motion

The current interface is intentionally low-motion. Route changes reset scroll
and focus the main landmark; no animation is required to understand state.
Future motion must be interruptible and disabled or shortened under
`prefers-reduced-motion`.

### Content and data visualization

Copy names the operation and consequence directly: “Create version”, “Retry”,
and “Confirm rollback”. Machine values are formatted for reading only at the
presentation edge; prompts, definitions, credentials, and remote outputs are
not placed in audit messages or URLs.

## Do's and Don'ts

- **Do:** Keep operational state legible through hierarchy, labels, and
  recovery paths.
- **Do:** Extend shared CSS and native semantics so sibling screens behave the
  same way.
- **Don't:** Use color, hover, or an icon alone to communicate a critical
  state or action.
- **Don't:** expose secret values or route invocation traffic through the
  Control Plane for UI convenience.
