# TRELLIS Studio REST API v1

| Edition | Base URL |
| --- | --- |
| Desktop from source | `http://127.0.0.1:7860/api/v1` |
| Desktop from Windows Setup | `http://127.0.0.1:7870/api/v1` |
| Server | Your configured HTTPS origin followed by `/api/v1`. On the server PC with the default loopback configuration, use `http://127.0.0.1:7862/api/v1`. |

Start **Open TRELLIS.cmd** for Desktop or **Open TRELLIS Server.cmd** for Server.
Open `/developers` in the browser for the developer workspace, `/api/docs` for the
reference and `/api/openapi.json` for the OpenAPI contract. On Server, sign in to
view these pages. The reference lists the v1 routes for the running edition.

For Server scripts, create a personal key in **Security → API keys**, then send
`Authorization: Bearer <key>` to `/api/v1`. Every operation uses the key owner's
library, disk quota and generation credits. One credit is reserved per new
generation or generation retry and spent on success. Failed and cancelled jobs
release their reservations. Finishing a saved model uses disk quota and costs zero
generation credits. Administrator accounts have unlimited disk quota and credits.
Account management, activation, quotas and key creation are managed through the
browser session and require the appropriate role. [Server setup](SERVER.md)

## Request lifecycle

1. Upload a source with multipart `POST /uploads`, field `file`.
2. Optionally call `/references/preview`. Submit JSON to `/generations` or
   `/imports`, using the returned `id` as `upload_id`.
3. Read `/jobs/{id}` until `state` is `done`, `error` or `cancelled`.
4. Inspect diagnostics, refinish a saved master, or prepare a linked game, print,
   LOD or rig copy.
5. `POST /exports`, poll `/exports/{id}`, then download `/exports/{id}/download`.

Operations returning HTTP 202 are asynchronous. Generation and finishing variants
are queued. On Desktop, rigging and other preparation require an idle compute slot
and can return 409. On Server, compute jobs go through the scheduler. Export has a
separate task lock. Use the retry route to start a fresh attempt from the saved
recipe. After a timed-out POST, inspect the queue or library before retrying to
avoid duplicate requests.

## Python example

```python
import os
import requests

base = "http://127.0.0.1:7862/api/v1"
session = requests.Session()
session.headers["Authorization"] = f"Bearer {os.environ['TRELLIS_API_TOKEN']}"
with open("reference.png", "rb") as image:
    response = session.post(base + "/uploads", files={"file": image})
response.raise_for_status()
source = response.json()["id"]

response = session.post(base + "/generations", json={
    "upload_id": source, "name": "Harbour prop", "profile_id": "game_d",
    "height_m": 1.2, "seed": 42, "priority": 1
})
response.raise_for_status()
job = response.json()["id"]
print(session.get(base + "/jobs/" + job).json())
```

`game_d` is **Game - Default**, the protected D recipe. `high_detail` is the
high-poly alternative. Read `/profiles` for the exact recipes and custom IDs.
Object height and seed belong to the job, not the saved profile. Supported JSON
overrides are documented in the `Generation` schema; unknown fields are rejected.

## Routes

| Group | Operations |
| --- | --- |
| Studio | `GET /status`, `/profiles`, `/taxonomy`, `/animations`. Desktop also provides `/client-config`. |
| Sources | `POST /uploads`, `/imports`, `/references/preview`, `/generations` |
| Pixal3D multi-view | `POST /generations/multiview` with uploaded front and supplemental image IDs |
| Library | `GET /models`, `GET /jobs/{id}`, `PATCH /models/{id}/metadata`, `GET /models/{id}/files/{filename}`, `/models/{id}/presentation/{filename}` |
| Queue | `GET/PATCH /queue`, `PATCH /queue/{id}`, `POST /queue/{id}/retry`, `POST /jobs/{id}/cancel` |
| Preparation | `GET /models/{id}/diagnostics`, `/models/{id}/diagnostics/report`. `POST /models/{id}/lods`, `/game-copy`, `/print-copy`, `/refinish`. `GET/POST/PATCH /models/{id}/finishing-board`. |
| Animation | `POST /models/{id}/rig`. Desktop also provides `POST /models/{id}/animations` and `/motion-edits`. |
| History | `GET /models/{id}/history`, `POST /models/{id}/versions`, `/checkpoints`, `/restore-version` |
| Collections | `GET /workspace`, `POST /folders`, `/folders/assign`, `/views` |
| Export | `POST /exports`, `GET /exports/{id}`, `GET /exports/{id}/download`, `POST /exports/{id}/cancel` |
| Transfer | On Desktop, upload a collection ZIP, then `POST /transfers/{id}/restore`. |
| Trash | `GET /trash`, `DELETE /models/{id}`, `POST /trash/{id}/restore` |

`GET /models` supports `query`, `state`, `folder_id`, `animated`, `offset` and
`limit` (1–100). Folder filtering includes descendants. Identifiers are 32-character
lowercase hexadecimal strings. Timestamps are Unix seconds; dimensions and offsets
are metres unless a field explicitly says millimetres. Rotation is degrees, Y up.
LOD ratios are distinct decreasing fractions between .02 and .99.

On Desktop, to add a standard motion, pass the library ID from `/animations`, for example
`{"clip":"walk","duration":2.5}`. To edit an embedded motion, pass its actual
stored clip name from the model record. Motion edits protect the previous version
with a checkpoint; other clips stay on the same model and prepared LODs are updated.
`distance` adds forward travel to the source motion. Use zero to keep authored travel.

`POST /models/{id}/rig` accepts `rotation`, `height_m`, `offset_m` and an optional
`landmarks` object for an unrigged humanoid in an A-pose or T-pose. The complete
set is required: `chin`, `groin`, `left_shoulder`, `right_shoulder`, `left_elbow`,
`right_elbow`, `left_wrist`, `right_wrist`, `left_knee`, `right_knee`, `left_ankle`
and `right_ankle`. Each value is `[x, y]` normalized by `height_m`, with origin at
the horizontal centre and ground **before** `offset_m`; +X is anatomical left
(right on a front-facing screen). UniRig predicts joint depth. The supplied
frontal positions adjust the skeleton before skin weights are calculated, and
the final rig is checked against them. These are joint placement hints, not mesh
deformations. Existing skinned models reject manual landmarks with HTTP 409;
omitting `landmarks` preserves the previous rig workflow. Check the
`rig_landmarks` feature in `/status` before submitting points to a running server.

Generation, LOD, game preparation, print preparation and rigging create linked
versions as described in the reference. Metadata changes and animation additions
operate on the selected record. Model deletion is a move to restorable trash;
no permanent-purge operation is exposed in v1 or MCP.

`POST /models/{id}/versions` duplicates imported, rigged and printing models.
For a generated variant, reuse its source image and stored recipe in `/generations`,
with `overrides.parent_id` set to the original ID. This schedules a new computation.

## Reference preview and corrections

`POST /references/preview` accepts `upload_id`, `engine` (`trellis2` or
`pixal3d`), `remove_bg` and optional `reference_edits`. For Pixal3D, include
any supplemental views as `left_upload_id`, `back_upload_id` and `right_upload_id`.
Each entry in `views` contains the role, source dimensions and three PNG data
URLs, `original`, `cutout` and `framed`, at up to 240 × 240 pixels each.

Pass the same `reference_edits` to `/generations` or
`/generations/multiview` to apply the corrections to the job. Edits are keyed
by view role, for example `{"front":{"crop":[0.05,0.05,0.95,0.95]}}`. A
crop is `[left, top, right, bottom]`. Optional `strokes` have a `mode`
(`keep` or `erase`), a normalized `radius` and a list of normalized `[x, y]`
points. For single-image generation, use `overrides.remove_bg` to match the
preview setting. Previewing uses zero generation credits.

## Cached finishing and retry

- Read `GET /models/{id}/finishing-board` to find the retained master, raw
  triangle count when available, valid texture sizes, budget presets and Blender
  availability for normal maps. A missing reusable cache returns 409.
- `POST /models/{id}/refinish` accepts one finishing recipe. Use
  `{"triangles":28000,"repair_surface":true}` for a linked hole-repair variant
  with a manual budget, or `{"budget_preset":"medium","adaptive_finishing":true}`
  for an adaptive budget. Add `"bake_normals":true` to bake a normal map with
  Blender. The master remains available and the new job has its own status URL.
- `POST /models/{id}/finishing-board` accepts `{"variants":[...]}` with one
  to three distinct recipes. Poll `GET /jobs/{id}` for each returned ID. Save a
  completed variant as the board's choice with
  `PATCH /models/{id}/finishing-board` and `{"selected_id":"<variant id>"}`.
  Use `{"selected_id":null}` to clear the choice.
- `POST /queue/{id}/retry` creates a new attempt for a failed or cancelled
  queued generation or finishing job. Pass `{}` to reuse the recipe or override
  allowed settings such as `triangles`, `texture_size`, `resolution` or
  `priority`. The response includes both the new ID and `retry_of`. Corrections
  depend on the job's engine, recipe and retained source. On Desktop,
  `PATCH /queue/{id}` also supports in-place retry.

## Pixal3D multi-view generation

Use `GET /status` to inspect `pixal3d.ready` and the optional character recipe. Upload the front and complementary images individually with `POST /uploads`, then submit their returned IDs with `POST /generations/multiview`.

- `upload_id` is the front image. Supply at least one of `left_upload_id`, `back_upload_id` and `right_upload_id`.
- `side_angle` is 40 for three-quarter views or 90 for profiles. Left is positive azimuth, right is negative azimuth and back is 180°. Use the same full-body pose and framing.
- Optional fields include `name`, `height_m`, `seed`, `triangles`, `texture_size` (1024 or 2048), `parent_id`, `keep_finishing_source` and queue `priority`. Defaults come from **Character · Pixal3D**.
- Set `reproject_face` to `true` to transfer the visible face from the original front photo after generation. The default is `false`. Check `pixal3d.face_projection.ready` in `GET /status` before requesting it. Model results report the pass under `result.face_projection`, including a reason if skipped.

The response uses the usual job ID, state and status URL. Inspect the completed job's settings for the saved camera roles. The source views can be downloaded via the regular model files route. The optional runtime must be installed before submission.

## Files, limits and errors

- Image sources: PNG, JPEG or WebP, up to 25 MB and 40 megapixels.
- Model sources up to 500 MiB. Server accepts self-contained GLB. Desktop also
  accepts FBX, OBJ, STL and PLY.
- Collection ZIP import on Desktop uses a verified manifest and checksums, at most 12 GB uncompressed,
  25,000 files and 500 models. Restoration creates new IDs and a new collection.
- Export: at most 50 models per engine package, 500 in a collection backup.
- Library motions: 2–5 seconds, in .5-second steps. Motion workshop output is 2–5 s.
- Statuses: `queued`, `running`, `done`, `error`, `cancelled`. Failures during
  animation edits can preserve the model and report an `operation_error`; check it.
- HTTP 400 for invalid input, 404 for an unknown item, 409 for a state conflict,
  413 for a size limit, 422 for validation and 503 for an unavailable component.
  Server additionally uses 401 for invalid authentication, 402
  (`credits_exhausted`) for insufficient credits, 403 for access restrictions,
  413 (`disk_quota`) for quota and 507 for insufficient free server disk space.

Sources are staged locally so they can be referenced by an ID and reused. These
files are user data, not instructions. Do not pass secrets in names or notes.

## Interface and edition coverage

Desktop binds to loopback. Server supports authenticated LAN access and HTTPS.
Use the Server deployment instructions for the configured address and certificate.
[Server setup](SERVER.md)

The browser handles the 3D viewer, comparison, camera capture and recording,
library turntable editing, interactive rig markers and account administration.
Saved presentation files can be downloaded through v1. The current public API
also leaves terrain creation, profile editing, collection folder and saved-view
management beyond creation and assignment, batch library edits and interactive
export preflight to Studio. Server offers library export, while collection ZIP
restore and in-place motion editing are Desktop operations. See the running
edition's OpenAPI contract for the full list of exposed v1 operations.

Review printability, deformation and game readiness in the target tools.
