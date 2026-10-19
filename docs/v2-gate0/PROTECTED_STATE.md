# Gate 0 protected-state audit

The audit reads original state directly with SQLite URI `mode=ro`,
`PRAGMA query_only=ON` and a read transaction. It does not instantiate the
application's writable store, call `Database.migrate()` on an original, start a
supervisor, invoke recovery or contact a generation provider. Historical migration
replay uses separate SQLite backups under this task's disposable `work/` only.

## Protected scope and identities

| Root alias | Authoritative scope inspected | Baseline |
|---|---|---|
| production | Existing Desktop ToviTunes `data/`, brand/character files and real `config.yaml` | 52 tables, 292 registered artifacts, 29 applied migrations |
| acceptance | 18 October final acceptance `work/acceptance` data/brand/runtime config | 53 tables, 301 registered artifacts, 30 migrations |
| retained-acceptance | 17 October retained acceptance `work/acceptance` data/brand/config | 52 tables, 362 registered artifacts, 29 migrations |
| prior evidence | 17 October production/art-review outputs and 18 October final acceptance, artwork-quality, OOM diagnosis and native-crash/controlled-recovery outputs | All files hashed, including receipts, original logs and post-success successor evidence |
| native outputs | Existing installed ComfyUI output/temp trees | All current files hashed; no new task is submitted or live history queried |

Initial manifest: **2,252 paths**, **13,782,162,544 bytes hashed**. The ledger records
missing optional config filenames explicitly rather than inventing files. SQLite
WAL/SHM files are volatile coordination state and are not byte-equality authorities;
complete logical table hashes cover committed records. Main database bytes and
registered artifacts are hashed as well. Active external changes would be reported,
never overwritten or repaired by this audit.

All **955 registered artifacts** match stored SHA-256 and byte count. Every source
database passes quick-check and foreign-key checks; every applied migration checksum
matches. Actual production and current acceptance copies each contain all **48**
canonical lock assets with valid image decode, byte count, hash and lock/recipe graph.
Pack revision: `tovi-pack-v1-8f7e487b5ac5279b`. Source/active/superseded mappings,
approvals and rights are retained. No approval, rights or media selection is added.

## Historical interrupted request (preserved, not resumed)

| Item | Frozen current identity |
|---|---|
| Original local request | `036c3a81-23e9-40d1-8ac3-947f2989eba7` |
| Original native ComfyUI task | `b9ef6704-75b9-4f00-85e9-1d51f7dbfd24` |
| Original status | `ambiguous` |
| Successor local request | `43fc83d9-200c-497e-ac9e-7f23693029b1` |
| Successor native task | `e6198751-8206-4141-ad92-b47917084ec0` |
| Successor status | `succeeded`; original parent identity retained |
| Shared original/successor input fingerprint | `5d185288c2f4fce00a24f4e7ab50e2817249f507188f8fe6f6b645e7a7d486c7` |
| Retained successor source | Artifact `21259ba2-b76a-47b8-b766-f323e9a05824`, 1,368,651 bytes |
| Source PNG SHA-256 | `3ae836f2ec5b5bb1d14f1c380069460f5c27664d9d94c6a666ed099da570e0ff` |
| Retained selected 37-second WAV SHA-256 | `036fc491fa67f7ee0863201715b84c9f332772dfdb1631caaed633f432176be9` |

The successor receipt has no normalized artifact. The raw source has no selected
production slot or preview admission. This is a successful **provider source**,
not an accepted render. Earlier pre-success reports correctly recorded no successor
at their own time; current Gate 0 captures the later authorized outcome without
rewriting those reports. No new grant, normalization, ingest, selection, generation,
episode, music, resume, render or publication is performed against this historical
or production state. Disposable offline tests create synthetic fixture media and
exercise local FFmpeg rendering in this task's isolated test directories only.

## Configuration, receipts and publication

- Real production config SHA-256:
  `051a7e6ca6f5d649b3eb203af47e2e8330d9274f49296499509375814631ed61`.
  Production capabilities remain enabled as found; `auto_publish=false` and human
  review remain intact. Gate 0 does not use that capability or change its flags.
- Config, source media, selections, dependency graph, original/successor request
  rows, retry/native-crash authorization records, provider receipts/fingerprints,
  music bindings, existing videos, publication attempts and visibility records are
  covered by full table/file digests. No token, credential or provider raw response
  is committed in the documentation evidence.
- All 85 stored illustration provider profiles independently reproduce their
  input fingerprints across the three databases. Twenty retained storyboard/render
  artifacts parse using current historical-compatible validators. Existing offline
  recovery/publication tests supply behavioral evidence without acting on these rows.
- Forty environment-request snapshot rows reproduce their fingerprints from
  retained canonical specs and set generation fingerprints (12 production, 12
  current acceptance, 16 older acceptance). Five historical rows per database
  lack a retained set generation fingerprint, so their fingerprints are preserved
  but **not independently recomputed**. These are 15 snapshot rows, not a claim of
  15 unique requests. No missing metadata is fabricated and no old episode is
  reopened to fill it. `evidence/environment-fingerprints.json` records that limit.
- Thirty required Qwen/ACE-Step installed model/config files are hashed separately
  before/after. They are not loaded, synchronized, converted, removed or replaced.
  This inventory is limited to the configured production models, not every unused
  model cache on the machine. No provider environment is changed.

## Preservation result and evidence custody

**Unchanged**: all three logical database snapshots and the complete protected file manifest compare equal before/after; all 30 configured model/config files also retain their bytes/hashes. No provider, configuration, selection or publication record changed.

`evidence/protected-ledger.json` contains artifact identities/hashes, per-table
digests, sanitized request identities and manifest references. The full machine-local
file manifest, protected receipt snapshots, model inventories, exact commands and
audit scripts are in the local handoff outside Git. Private media/databases/config
contents remain at their original locations. Evidence hashes identify snapshots;
they are not substitutes for retaining the original files.

This audit does not diagnose or finish the historical episode. Its source,
ambiguous parent and successful successor remain roadmap evidence.
