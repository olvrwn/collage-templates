# collage-templates – instructions for Claude Code

This repo is the **template catalogue** for a private iOS collage app. It is published via GitHub Pages;
the app downloads `manifest.json` and caches everything locally. Nothing is bundled in the app, so every
change here is a live release for the app.

## Read first
- `docs/SCHEMA.md` – the template format and design rules (authoritative).
- `tools/build.py` – validator, manifest generator, placeholder-thumbnail renderer, and the **reference
  layout implementation** (`layout_slot`). Do not change layout semantics without also updating
  `docs/SCHEMA.md` and warning the user that the iOS app must change too.

## Hard rules
1. **Never edit `manifest.json` or any `sha256` by hand.** Run `python3 tools/build.py`.
2. Editing an existing `template.json` requires **bumping `version`** (the build fails otherwise).
3. `id` = lowercase-kebab-case = folder name. Never rename or delete an existing id unless the user asks
   (deleting removes it from users' caches on next sync).
4. Stay within schema v1. If a design needs something the schema cannot express (masks such as hearts or
   circles, text, per-slot filters), do **not** invent fields: tell the user it needs a schema change and
   propose one in `docs/SCHEMA.md` first.
5. Originality: take inspiration from layout *types* (grids, photobooth strips, polaroid walls, bento, …),
   never reproduce a specific creator's or app's template, artwork, branding or text.
6. Every template must pass `python3 tools/build.py` and be visually checked (see below) before committing.

## Procedure: add a new template (or run `/new-template <description>`)
1. Understand the request. Pick: `id`, English + German `name`, `category` (see SCHEMA.md; a new category
   needs the user's OK and adding it to `CATEGORIES` in `tools/build.py` and to the list in SCHEMA.md),
   2–3 `tags`, canvas aspect (9:16 story, 4:5 portrait post, 1:1 square, 2:3), background and `defaults`.
2. Sketch the layout in normalised coordinates. Compute real slot aspect ratios (SCHEMA.md, design rules)
   and polaroid/film frame sizes. Reuse an existing template of the same family as the starting point
   (copy its `template.json`, change id/name/slots).
3. Write `templates/<id>/template.json` with `"version": 1`. Set `customizable` ranges so defaults lie
   inside them; hide controls that make no sense (e.g. `gap` for free-floating slots).
4. Run `python3 tools/build.py`. Fix every validation error.
5. **Look at the result:** open `templates/<id>/thumb.jpg` (placeholder render; `--thumbs --only <id>` re-renders it
   after edits). Check: nothing outside the canvas, no unintended overlaps, sensible photo aspect ratios, readable
   balance. Iterate steps 3–5 (while still unreleased you may re-run with the same version; for an already
   committed template bump `version`).
6. Optional: if the user supplies a real preview image, save it as `templates/<id>/thumb.jpg` (JPEG, ~600 px wide).
   Never overwrite a real thumbnail with `--thumbs` (use `--only` for the template you are working on).
7. Commit with a clear message (`Add template <id>`), push, then verify the deployment:
   `curl -s https://<USERNAME>.github.io/collage-templates/manifest.json | head` (GitHub Pages takes 1–3 min;
   retry a few times) and confirm the new id appears.
8. Report to the user: id, name, photo count, aspect ratio, category, and any limitations.

## Commands
```
python3 tools/build.py                       validate + write manifest (+ create missing thumbnails)
python3 tools/build.py --thumbs --only <id>  re-render one placeholder thumbnail
pip install pillow                           only needed for thumbnails
```
