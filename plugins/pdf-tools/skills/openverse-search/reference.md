# Openverse API Reference

Consumer docs: https://api.openverse.org/v1/ (ReDoc, loads `/v1/schema/`)

## Endpoints

| Method | Path | Purpose |
|--------|------|---------|
| GET | `/v1/images/` | Search images |
| GET | `/v1/images/{id}/` | Image detail |
| GET | `/v1/images/{id}/related/` | Related images |
| GET | `/v1/images/stats/` | Indexed sources |
| GET | `/v1/audio/` | Search audio |
| GET | `/v1/audio/{id}/` | Audio detail |
| GET | `/v1/audio/{id}/related/` | Related audio |
| GET | `/v1/audio/stats/` | Audio sources |

## Image search parameters

| Param | Type | Description |
|-------|------|-------------|
| `q` | string | Full-text query, max 200 chars. Supports Chinese (URL-encode). |
| `page` | int | Page number, default 1 |
| `page_size` | int | Results per page, default 20 |
| `license` | string | Comma-separated: `by`, `by-nc`, `by-nc-nd`, `by-nc-sa`, `by-nd`, `by-sa`, `cc0`, `nc-sampling+`, `pdm`, `sampling+` |
| `license_type` | string | `all`, `all-cc`, `commercial`, `modification` |
| `source` | string | Comma-separated provider names (see stats) |
| `excluded_source` | string | Exclude providers (cannot combine logic with `source` usefully — pick one approach) |
| `category` | string | `photograph`, `digitized_artwork`, `illustration` |
| `aspect_ratio` | string | `tall`, `wide`, `square` |
| `size` | string | `small`, `medium`, `large` |
| `extension` | string | Comma-separated file extensions |
| `creator` | string | Creator name (ignored when `q` is set) |
| `tags` | string | Tag search only (cannot use with `q`) |
| `title` | string | Title search only (cannot use with `q`) |
| `mature` | bool | Include sensitive content, default false |
| `filter_dead` | bool | Filter 404 links, default true |

## Response fields (per result)

| Field | Meaning |
|-------|---------|
| `id` | UUID for detail/related endpoints |
| `title` | Media title |
| `url` | Direct media file URL |
| `thumbnail` | API-hosted thumbnail |
| `creator` | Author name |
| `license` | License slug (e.g. `by-sa`) |
| `license_version` | Version (e.g. `2.0`) |
| `license_url` | Full license URL |
| `attribution` | Pre-formatted attribution string |
| `source` | Provider slug (`wikimedia`, `flickr`, ...) |
| `foreign_landing_url` | Original page on provider site |
| `width`, `height` | Pixel dimensions |
| `detail_url` | API detail endpoint |

## Common sources (from `/v1/images/stats/`)

`wikimedia`, `flickr`, `rawpixel`, `stocksnap`, `bio_diversity`, `clevelandmuseum`,
`brooklynmuseum`, `europeana`, `met`, `smithsonian`, ...

Run `openverse.py stats` for the live list.

## Authentication

Anonymous access works for search and download. Higher `page_size` / deeper
pagination may require auth — see API docs `#tag/auth` if you hit limits.

## Rate limiting

No published hard limit, but batch politely:

- Default `page_size` 12–20
- Sleep 1–2s between bulk queries
- On HTTP 429, back off and retry
