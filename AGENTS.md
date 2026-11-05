# AGENTS.md — memsave

Self-hosted image archive with AI tags. FastAPI + SQLite + vanilla JS, PWA Share Target and a
Chrome MV3 context-menu extension. No admin UI; data is a SQLite file plus image folders, and
backup is a CLI command.

## Layout

| Path | What is there |
|---|---|
| `memsave/` | Python package: config, db, media, vision, link preview, job queue, search, backup, CLI |
| `memsave/server/` | FastAPI app and static UI (no build step) |
| `memsave/server/static/` | `index.html`, `app.js`, `style.css`, `share.html`, `sw.js`, manifest, icons |
| `extension/` | Chrome MV3 worker, options page and icons |
| `deploy/sitehub/` | Traefik fragment for sitehub |
| `deploy/windows/` | host-side update script with backup and rollback |
| `tools/` | `make_icons.py`, `bump_version.py` |
| `tests/` | offline pytest suite |
| `data/` | runtime data — never commit |

## Commands

```bash
python -m pip install -r requirements.txt
python -m pip install -r requirements-dev.txt
python -m memsave init
python -m memsave serve                 # http://127.0.0.1:8780
python -m memsave backup [--full]
python -m memsave reindex [--full]
python -m memsave retry-failed
python -m memsave stats
python -m pytest -q
node --check memsave/server/static/app.js
node --check memsave/server/static/sw.js
docker compose up -d --build
powershell -ExecutionPolicy Bypass -File deploy\windows\update.ps1
```

`MEMSAVE_JOBS=0` disables background workers (used by tests). `MEMSAVE_DATA_DIR` points the
whole service at a temporary repository in tests.

## Architecture rules

- **Ingest is fast.** `POST /api/images` writes bytes and a DB row; `prepare` and `recognize`
  run in background workers. Do not move vision calls into the HTTP handler.
- **The job table is durable.** New job kinds must use `db.enqueue_job`, `claim_job`,
  `finish_job`/`fail_job`; workers are started in `JobRunner`. A job that updates the image
  status and then raises must commit that status before raising (see `recognize_job`).
- **AI output is untrusted.** Keep the tolerant JSON parser, Pydantic validation, category
  coercion, tag normalization and caps in `prompt.py`.
- **Search is hybrid.** FTS5 (exact tokens) plus LIKE fallback for partial words, vector search
  with a score threshold, RRF fusion. `semantic_available()` must not download the model while
  merely browsing; only an actual query/embedding may load it.
- **Link preview is an SSRF boundary.** Every page/image/redirect URL must go through
  `linkpreview._assert_public_url`; no private/loopback/link-local addresses, only http/https
  on ports 80/443, size/time/redirect caps. No image means no DB row.
- **No admin listener.** Do not add `/admin` or a second port. Backup is `python -m memsave
  backup` (inside Docker: `docker compose exec memsave ...`). The site is protected only by
  Cloudflare Access in front of sitehub.
- **CSRF header.** Every mutating route takes `Depends(require_csrf)` and clients send
  `X-Memsave-Request: 1`. Never add a mutating route without it.
- **UI has no build step.** Keep vanilla JS/CSS. All UI strings are Russian; code/comments are
  English. Run `node --check` after editing JS.
- **URL state.** Search, sort, tag and lightbox live in `?q=`, `?sort=`, `?tag=`, `?image=`.
  Keep `history.pushState`/`replaceState` and `popstate` working.
- **PWA.** `/manifest.webmanifest` and `/sw.js` are explicit root routes with no-cache headers.
  `sw.js` handles only `POST /share`; it never caches API or images. The public paths are
  exactly `/manifest.webmanifest`, `/static/favicon.svg`, `/static/icons/*`, both in Traefik
  and Cloudflare Access Bypass.
- **Access and cookies.** The extension uploads with `credentials: "include"`; if Access
  returns HTML instead of JSON, clients must show an auth message rather than parse it.
- **Data safety.** Never commit `.env` or `data/`. Never read `data/memsave.db` from the host
  while the container is running if the host can avoid it; use `docker compose exec` for
  backups and reindexing.

## Versioning

`memsave/__init__.py` is the only source of the application version. `tools/bump_version.py
X.Y.Z` updates it and `extension/manifest.json`; `--check` fails if they drift. Bump minor for
a release, tag `vX.Y.Z`, push tags with the branch. `/api/version` and the header render
`vX.Y.Z` + short commit. The Docker build receives `MEMSAVE_COMMIT`, `MEMSAVE_COMMIT_DATE`,
`MEMSAVE_VERSION`, `MEMSAVE_CREATED` from `update.ps1`.

## Icons

`tools/icon.svg` is the source: a transparent, glowing download arrow with a purple-to-yellow
gradient. `tools/make_icons.py` renders the committed PWA and extension PNGs with Chrome; never
edit the PNGs by hand. The maskable variant uses the same transparent artwork.

## Tests

Offline only: `tmp_path` rewrites `config.DATA_DIR`, `DB_PATH`, `IMAGES_DIR`, `THUMBS_DIR`,
`BACKUPS_DIR`, `MODELS_DIR`; `create_app(start_jobs=False)`; fake vision/embedder and local HTML
fixtures. Add tests for every new route, job kind, parser branch and backup path.
