# Distributing data without infringing copyright

> A suggested way of working. This is not legal advice: for commercial standards
> and guides, check each publisher's licence terms and, if needed, consult a lawyer.

## The principle: two layers

| Layer | Content | Where | Who can access it |
|---|---|---|---|
| **Source** `sources/<doc>/` | full extraction: text, tables, figures | the output folder (private) | only you, as a working reference |
| **Derived** `sources/<doc>/derived/` | what you *drew* from the source, in your own words | written in the output folder, distributed through the pack | **everyone** if the pack is published |

A published pack (public repository, website, release) is visible to anyone, not
only to its intended audience. Hence the cautious default: no table is published
unless it is listed in `share.tables`.

What is generally protected: the **wording** of the text, figures, layout and
sometimes the **selection and arrangement** of a table or compilation. What
generally is not: facts, physical values, calculation methods and formulas as
such. The derived layer therefore aims to pass on useful knowledge without
reproducing the expression.

## What the derived layer contains

- `parameters.json`: structured values and parameters, each with its source.
  ```json
  {"reinforced_concrete_unit_weight": {"value": 24.0, "unit": "kN/m3",
    "source": {"doc": "slab-guide-1a6675e5", "section": "2.1.1", "table": "t001", "page": 3}}}
  ```
- `formulas.md`: formulas rewritten with your own notation and explanations.
- `summary.md`: a summary in your own words, not a line-by-line paraphrase.
- The code of your own tools, which implements a method without copying the text.

Claude can produce these files from `document.md` and the CSVs; you review them
before distributing.

## Rules applied by `quarrix dist`

Each `meta.json` decides what goes out:

| `licence.distribution` | Pack content |
|---|---|
| `none` (or `exclude: true`) | nothing |
| `derived` (default) | bibliographic reference, outline (titles and pages), the `derived/` folder, and only the tables listed in `share.tables` |
| `full` | everything (text, tables, figures) except `redact`, **only** if `licence.status` is `public-domain`, `open` or `permission`; otherwise falls back to `derived` |

`redact` removes specific sections (and their subsections), tables or figures,
whatever the mode.

The `dist/quarrix-pack/` pack also contains `manifest.json` (SHA-256 of every
file, to check integrity on arrival) and `NOTICE.md` (list of sources and rights
holders).

## Licence statuses

| `status` | Examples | Suggested distribution |
|---|---|---|
| `public-domain` | laws, government publications depending on the country | `full` possible |
| `open` | CC BY, CC BY-SA (respect attribution and share-alike) | `full` possible |
| `permission` | written permission from the publisher (note it in `notes`) | `full` within the limits granted |
| `proprietary` | commercial standards, manufacturer catalogs | `derived` |
| `unknown` | default | `derived` until checked |

## Suggested workflow

1. Drop the PDF into Quarrix → `<output folder>/sources/<doc>/`.
2. Fill in `meta.json` (publisher, edition, year, licence status).
3. Ask Claude to write `derived/` from the source.
4. Review, then run `quarrix dist` and check `NOTICE.md` and the content of
   `<output folder>/dist/quarrix-pack/`.
5. `quarrix dist --publish-dir <clone of the public repository>`, review the
   diff, then commit and push: this is the step that makes the pack public.
6. Tools that use the data read the pack from that repository (or from a release
   of the zip).

## Ideas for later

- Publish the zip as a versioned GitHub release that tools download and verify
  with `manifest.json`.
- Automatic check: reject a `derived/` folder containing long passages identical
  to the source (similarity detection).
- Signing `manifest.json`.
