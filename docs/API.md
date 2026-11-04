# TRELLIS Studio REST API v1

Base URL: `http://127.0.0.1:7860/api/v1`.

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

Generation, LOD, game preparation, print preparation and rigging create linked
versions as described in the reference. Metadata changes and animation additions
operate on the selected record. Model deletion is a move to restorable trash;
no permanent-purge operation is exposed in v1 or MCP.

`POST /models/{id}/versions` duplicates imported, rigged and printing models.
For a generated variant, reuse its source image and stored recipe in `/generations`,
with `overrides.parent_id` set to the original ID. This schedules a new computation.

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
