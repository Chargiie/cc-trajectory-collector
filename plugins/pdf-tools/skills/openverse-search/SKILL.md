---
name: openverse-search
description: >-
  Search openly-licensed images and audio via the Openverse API (CC / public
  domain). Use when the user needs free stock photos, landmark or travel images,
  Creative Commons media, Wikimedia Commons aggregation, image search with
  license filters, or programmatic image discovery. Prefer over raw curl when
  searching Openverse.
---

# Openverse Image Search

Search [Openverse](https://openverse.org) — an open-source engine indexing 800M+
CC-licensed and public-domain media from Wikimedia Commons, Flickr, museums, etc.

**No API key required.** Base URL: `https://api.openverse.org/v1`

## When to use

| Scenario | Use Openverse | Use Wikimedia Commons API directly |
|----------|---------------|-------------------------------------|
| General keyword search across many sources | Yes | No (Commons only) |
| Landmark / city / architecture photos | Yes | Yes (often deeper for specific sites) |
| Need license filter (`cc0`, `commercial`) | Yes | Manual |
| Need museum / Flickr / multi-source in one call | Yes | No |

Default to this skill for Openverse. Fall back to Commons API only when Openverse
results are thin for a very specific landmark.

## Quick start

Always run the bundled CLI (handles URL encoding for Chinese queries):

```bash
python3 <openverse skill 目录>/scripts/openverse.py search "鼓浪屿" --page-size 8
python3 <openverse skill 目录>/scripts/openverse.py search "Gulangyu island" --source wikimedia --license by,by-sa,cc0
python3 <openverse skill 目录>/scripts/openverse.py search "architecture" --license-type commercial --format json
python3 <openverse skill 目录>/scripts/openverse.py stats
python3 <openverse skill 目录>/scripts/openverse.py download "<url>" -o /tmp/photo.jpg
```

Use `--format json` when you need structured output for downstream processing.

## Standard workflow

Copy this checklist for image-finding tasks:

```
Task Progress:
- [ ] Clarify subject, count, and license needs (commercial? attribution?)
- [ ] Search with openverse.py (try English + Chinese if China-related)
- [ ] Tighten filters: --source wikimedia, --license-type commercial
- [ ] Present top candidates with title, license, attribution, landing URL
- [ ] Download only after user picks (or when task explicitly needs files)
- [ ] Include attribution text when license requires it
```

### Step 1 — Search

```bash
python3 <openverse skill 目录>/scripts/openverse.py search "<query>" \
  --page-size 12 \
  --license-type commercial
```

Useful filters:

| Flag | Values | Notes |
|------|--------|-------|
| `--license` | `by`, `by-sa`, `by-nc`, `by-nd`, `by-nc-sa`, `by-nc-nd`, `cc0`, `pdm` | Comma-separated |
| `--license-type` | `commercial`, `modification`, `all-cc`, `all` | Shortcut presets |
| `--source` | `wikimedia`, `flickr`, `rawpixel`, ... | Run `stats` for full list |
| `--category` | `photograph`, `digitized_artwork`, `illustration` | Image only |
| `--aspect-ratio` | `wide`, `tall`, `square` | Image only |
| `--size` | `small`, `medium`, `large` | Image only |
| `--extension` | `jpg`, `png`, `svg`, ... | Comma-separated |

### Step 2 — Refine if results are weak

1. Rephrase query (English often works better; try Chinese for domestic subjects).
2. Restrict source: `--source wikimedia` for landmarks.
3. Broaden license: drop `--license-type` or add `by,by-sa`.
4. Fetch related: `openverse.py related <uuid>`.
5. Paginate: `--page 2`.

### Step 3 — Present results

For each candidate, show:

- **Title** and thumbnail/full URL
- **License** (`license` + `license_version`, link `license_url`)
- **Attribution** — use the API `attribution` field verbatim when present
- **Source page** — `foreign_landing_url`

### Step 4 — Download

```bash
python3 <openverse skill 目录>/scripts/openverse.py download "<result.url>" -o ./assets/photo.jpg
```

Prefer `url` (original file). Use `thumbnail` only for previews.

## License guidance

| License | Commercial use | Attribution | Derivatives |
|---------|----------------|-------------|-------------|
| `cc0`, `pdm` | Yes | Not required (encouraged) | Yes |
| `by` | Yes | Required | Yes |
| `by-sa` | Yes | Required | Yes, same license |
| `by-nc*` | No | Required | Varies |
| `by-nd*` | Yes/No | Required | No derivatives |

When the user needs **commercial, no-attribution** images, use:

```bash
--license cc0,pdm
# or
--license-type commercial
```

Always surface license info before download. Do not strip attribution for `by*`
licenses.

## Multi-query batch search

For travel decks / reports covering several landmarks, search each term:

```bash
for term in "Gulangyu island" "Xiamen University" "Nanputuo Temple"; do
  echo "=== $term ==="
  python3 <openverse skill 目录>/scripts/openverse.py search "$term" \
    --source wikimedia --page-size 4
done
```

## Audio search

```bash
python3 <openverse skill 目录>/scripts/openverse.py search "piano" --audio --page-size 10
```

## Raw API (escape hatch)

Only use curl when the CLI lacks a needed parameter:

```bash
curl -sG "https://api.openverse.org/v1/images/" \
  --data-urlencode "q=鼓浪屿" \
  --data-urlencode "license=by,by-sa,cc0" \
  --data-urlencode "source=wikimedia" \
  --data-urlencode "page_size=8"
```

Interactive docs: https://api.openverse.org/v1/

## Troubleshooting

| Problem | Fix |
|---------|-----|
| Empty / irrelevant results | Try English query; add `--source wikimedia`; remove strict license filter |
| Chinese query fails in curl | Use `--data-urlencode` or the bundled CLI |
| `HTTP 429` / rate limit | Reduce batch size; add delay between requests |
| Broken download URL | Check `foreign_landing_url`; try `filter_dead=true` (API default) |
| Need highest-res Wikimedia file | Open `foreign_landing_url` or use Commons API for that file |

## Additional resources

- API parameter reference: [reference.md](reference.md)
- Openverse project: https://github.com/WordPress/openverse
