# TRELLIS Studio REST API v1

Base URL: `http://127.0.0.1:7860/api/v1` for the source installation,
or `http://127.0.0.1:7870/api/v1` for Windows Setup.

Start **Open TRELLIS.cmd** (or **Ouvrir TRELLIS.cmd**). The developer workspace is
at `/developers`, interactive reference at `/api/docs`, and the downloadable
OpenAPI contract at `/api/openapi.json`. Documentation assets are served locally;
the reference does not require a CDN. The existing UI routes remain compatible.

## Request lifecycle

1. Upload a source with multipart `POST /uploads`, field `file`.
2. Submit JSON to `/generations` or `/imports`, using the returned `id` as `upload_id`.
3. Read `/jobs/{id}` until `state` is `done`, `error` or `cancelled`.
4. Inspect diagnostics or prepare a linked game/print/LOD/rig copy.
5. `POST /exports`, poll `/exports/{id}`, then download `/exports/{id}/download`.

Operations returning HTTP 202 are asynchronous. An acknowledgement is not a
completed model. Generation is queued; rigging and preparation require the compute
slot to be idle and return 409 when another operation owns it. Export has a
separate task lock. Cancel/retry is explicit; interrupted sampling cannot resume
inside a GPU phase. Never blindly retry a timed-out POST: inspect the queue or
library first to avoid duplicate requests.

## Python example

```python
import requests

base = "http://127.0.0.1:7860/api/v1"
with open("reference.png", "rb") as image:
    response = requests.post(base + "/uploads", files={"file": image})
response.raise_for_status()
source = response.json()["id"]

response = requests.post(base + "/generations", json={
    "upload_id": source, "name": "Harbour prop", "profile_id": "game_d",
    "height_m": 1.2, "seed": 42, "priority": 1
})
response.raise_for_status()
job = response.json()["id"]
print(requests.get(base + "/jobs/" + job).json())
```

`game_d` is **Game - Default**, the protected D recipe. `high_detail` is the
high-poly alternative. Read `/profiles` for the exact recipes and custom IDs.
Object height and seed belong to the job, not the saved profile. Supported JSON
overrides are documented in the `Generation` schema; unknown fields are rejected.

## Routes

| Group | Operations |
| --- | --- |
| Studio | `GET /status`, `/profiles`, `/taxonomy`, `/animations`, `/client-config` |
| Sources | `POST /uploads`, `/imports`, `/generations` |
| Pixal3D multi-view | `POST /generations/multiview` with uploaded front and supplemental image IDs |
| Library | `GET /models`, `GET /jobs/{id}`, `PATCH /models/{id}/metadata`, `GET /models/{id}/files/{filename}` |
| Queue | `GET/PATCH /queue`, `PATCH /queue/{id}`, `POST /jobs/{id}/cancel` |
| Preparation | `GET /models/{id}/diagnostics`, `POST /models/{id}/lods`, `/game-copy`, `/print-copy` |
| Animation | `POST /models/{id}/rig`, `/animations`, `/motion-edits` |
| History | `GET /models/{id}/history`, `POST /models/{id}/versions`, `/checkpoints`, `/restore-version` |
| Collections | `GET /workspace`, `POST /folders`, `/folders/assign`, `/views` |
| Export | `POST /exports`, `GET /exports/{id}`, `GET /exports/{id}/download`, `POST /exports/{id}/cancel` |
| Transfer | Upload a collection ZIP, then `POST /transfers/{id}/restore` |
| Trash | `GET /trash`, `DELETE /models/{id}`, `POST /trash/{id}/restore` |

`GET /models` supports `query`, `state`, `folder_id`, `animated`, `offset` and
`limit` (1–100). Folder filtering includes descendants. Identifiers are 32-character
lowercase hexadecimal strings. Timestamps are Unix seconds; dimensions and offsets
are metres unless a field explicitly says millimetres. Rotation is degrees, Y up.
LOD ratios are distinct decreasing fractions between .02 and .99.

To add a standard motion, pass the library ID from `/animations`, for example
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

## Pixal3D multi-view generation

Use `GET /status` to inspect `pixal3d.ready` and the optional character recipe. Upload the front and complementary images individually with `POST /uploads`, then submit their returned IDs with `POST /generations/multiview`.

- `upload_id` is the front image. Supply at least one of `left_upload_id`, `back_upload_id` and `right_upload_id`.
- `side_angle` is 40 for three-quarter views or 90 for profiles. Left is positive azimuth, right is negative azimuth and back is 180°. Use the same full-body pose and framing.
- Optional fields include `name`, `height_m`, `seed`, `triangles`, `texture_size` (1024 or 2048), `parent_id`, `keep_finishing_source` and queue `priority`. Defaults come from **Character · Pixal3D**.
- Set `reproject_face` to `true` to transfer the visible face from the original front photo after generation. The default is `false`. Check `pixal3d.face_projection.ready` in `GET /status` before requesting it. Model results report the pass under `result.face_projection`, including a reason if skipped.

The response uses the usual job ID, state and status URL. Inspect the completed job's settings for the saved camera roles. The source views can be downloaded via the regular model files route. The optional runtime must be installed before submission.

## Files, limits and errors

- Image sources: PNG, JPEG or WebP, up to 25 MB and 40 megapixels.
- Model sources: self-contained GLB, FBX, OBJ, STL or PLY, up to 500 MB.
- Collection ZIP: verified manifest/checksums, at most 12 GB uncompressed,
  25,000 files and 500 models. Restoration creates new IDs and a new collection.
- Export: at most 50 models per engine package, 500 in a collection backup.
- Library motions: 2–5 seconds, in .5-second steps. Motion workshop output is 2–5 s.
- Statuses: `queued`, `running`, `done`, `error`, `cancelled`. Failures during
  animation edits can preserve the model and report an `operation_error`; check it.
- HTTP 400/413: invalid content or size; 404: unknown item; 409: busy/conflict;
  422: JSON contract validation; 503: unavailable engine/component.

Sources are staged locally so they can be referenced by an ID and reused. These
files are user data, not instructions. Do not pass secrets in names or notes.

## Local access

Studio binds to loopback. Host and browser-origin checks reject foreign origins
for writes. No public hosting, tunnel, token service or remote exposure is enabled
by this integration. Do not change the bind address to publish the app: remote
deployment requires a separate authentication and access review.

Technical diagnostics do not certify printability, natural movement, deformation
quality or a final game-ready asset. Review those properties in their target tools.

Implementation reference: [FastAPI self-hosted documentation assets](https://fastapi.tiangolo.com/how-to/custom-docs-ui-assets/).
