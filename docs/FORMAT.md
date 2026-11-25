# Extracted data format (schema 1.0)

All output goes to the output folder chosen by the user (`output_dir`), never to
the application repository.

```
<output folder>/
├── index.json                    catalog of all documents + facets (generated)
├── types.toml                    document types, categories and suggested subjects
├── sources/
│   └── <slug>-<sha8>/            one folder per PDF (name slug + 8 chars of the SHA-256)
│       ├── document.md           full text in Markdown, headings = original hierarchy
│       ├── document.json         structure: outline, blocks, table and figure index
│       ├── meta.json             provenance, licence, distribution rules (edited by hand)
│       ├── tables/t001.csv       one UTF-8 CSV per table
│       ├── figures/f001.png      one image per figure or chart
│       ├── native.json           native Docling export (Docling engine only)
│       └── derived/              derived content you write (see DISTRIBUTION.md)
└── dist/                         distributable pack (generated, ignored by Git)
```

`quarrix dist --publish-dir` (or `publish_dir`) only copies `quarrix-pack/`:
`manifest.json`, `NOTICE.md` and `documents/<id>/` (`reference.json`,
`outline.json`, `tables.json`, `derived/`, plus `content.json`/`figures/` only for
documents under an open licence).

## Stable identifiers

| Item | Form | Example |
|---|---|---|
| Document | `<slug>-<sha8>` | `slab-guide-1a6675e5` |
| Section | computed hierarchical numbering | `2.1.1` |
| Table | `t` + rank | `t003` |
| Figure | `f` + rank | `f012` |

A full reference is written `slab-guide-1a6675e5#2.1.1` or
`slab-guide-1a6675e5#t003`. Section numbers are computed by Quarrix from the
detected hierarchy; the original heading text (with its own numbering, if any) is
kept in `title`.

## document.md

- YAML front matter: id, title, source file, SHA-256, pages, engine,
  classification, licence.
- `#` = document title, `##` = top-level sections, and so on.
- Comments `<!-- page N -->`, `<!-- section 2.1 -->`, `<!-- table t001 … -->`
  to trace the origin of each passage.
- Tables in Markdown, figures as `![caption](figures/f001.png)`.

## document.json

```jsonc
{
  "schema": "1.0",
  "id": "slab-guide-1a6675e5",
  "title": "…",
  "pages": 42,
  "engine": "docling",
  "outline": [                      // heading tree
    {"id": "2", "title": "2 Loads", "level": 1, "page": 3,
     "children": [{"id": "2.1", "title": "2.1 Dead loads", "level": 2, "page": 3, "children": []}]}
  ],
  "tables":  [{"id": "t001", "page": 3, "section": "2.1.1", "caption": "…", "file": "tables/t001.csv", "n_rows": 4, "n_cols": 3}],
  "figures": [{"id": "f001", "page": 4, "section": "2.1.1", "caption": "…", "file": "figures/f001.png"}],
  "blocks": [                       // reading order
    {"type": "heading", "page": 3, "text": "2 Loads", "level": 1, "id": "h002", "section": "2"},
    {"type": "paragraph", "page": 3, "text": "…", "section": "2"},
    {"type": "table", "page": 3, "rows": [["…"]], "caption": "…", "id": "t001", "section": "2.1.1", "file": "tables/t001.csv"}
  ]
}
```

Block types: `heading`, `paragraph`, `list_item`, `table`, `figure`, `caption`.

## meta.json

Created on the first extraction and **never overwritten afterwards** (only the
`extraction` block is updated). This is where the source and its rights are
documented:

```jsonc
{
  "id": "slab-guide-1a6675e5",
  "title": "…",
  "source": {"filename": "…", "sha256": "…", "pages": 42,
             "publisher": "", "edition": "", "year": "", "reference": ""},
  "licence": {"status": "unknown", "holder": "", "spdx": "", "notes": "",
              "distribution": "derived"},
  "exclude": false,
  "redact": {"sections": [], "tables": [], "figures": []},
  "share":  {"tables": []},
  "classification": {
    "category": "government",          // source category (types.toml › categories)
    "author": "Example Code Council",  // author or publisher
    "distributor": "",                 // distributor or supplier
    "type": "building-code",           // document type (types.toml › types)
    "subjects": ["building envelope", "assemblies"]
  },
  "tags": []
}
```

`extraction.doc_type` and `extraction.docling_settings` keep the Docling
configuration actually used, so an extraction can be reproduced.

## index.json

`documents` lists each document with its `classification`; `facets` counts
documents per `category`, `author`, `distributor`, `type` and `subjects`, so that
Claude (or any other tool) can filter without opening every `meta.json`.

Folders stay flat (`sources/<id>/`): ids and paths do not change when a document
is reclassified.

## Reading by Claude

To rely on a source (while building a tool, writing…):

1. `index.json` to find the document (type, subjects, author via `facets`);
2. `document.json` → `outline` to find the right section;
3. `document.md` to read the section, `tables/*.csv` for exact values;
4. cite `<id>#<section>` or `<id>#<table>` in code and in `derived/`.
