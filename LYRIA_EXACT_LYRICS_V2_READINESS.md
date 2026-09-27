# Lyria exact-lyrics v2 readiness

Validated on 2026-09-27. This report covers offline contract readiness only; it does not authorize a paid request or claim provider compliance.

## Base and scope

- Starting latest `origin/main`: `657998682bd302f41641cf00fe2a3fc3371008d4` (matches expected main).
- Branch: `codex/lyria-exact-lyrics-contract-v2`.
- Prompt contract: `lyria_exact_lyrics_v2`.
- Model: `lyria-3-pro-preview`; provider: `google`; backend: `vertex_ai_interactions`.
- Canonical YAML files are byte-for-byte unchanged from base. QA ceiling remains 45 seconds. No QA, lyric, ASR, alignment, rights or approval policy changed.

## Exact target timeline and lyric-once proof

English preschool pop, ages 3–6, approximately 112 BPM; target total duration approximately 34 seconds.

| Timestamp | Canonical lyric | Full-prompt occurrence count |
| --- | --- | --- |
| 00:03 | Red, red, look ahead! | 1 |
| 00:07 | Red is a color, yes, red! | 1 |
| 00:11 | A red apple, round and bright. | 1 |
| 00:16 | A red ball rolls into sight. | 1 |
| 00:21 | Red, red, what do you see? | 1 |
| 00:25 | Red is a color, sing with me! | 1 |
| 00:31 | Red! | 1 |

Canonical lyric count: **7**. The `Lyrics:` block contains exactly these seven lines, in YAML order with unchanged spelling. The complete prompt was checked with `prompt.count(line.text) == 1` for every canonical line, including the second line and final one-word line.

- 00:00: instrumental intro only, no vocal words.
- 00:03: intro ends and first vocal line starts.
- 00:31: final lyric starts.
- 00:33: all vocal words finished; short instrumental ending only.
- 00:34: end track.

The prompt requires exactly-once ordered delivery and forbids repeating or omitting lines, repeating chorus/refrain, invented sung words, worded ad-libs, restarting earlier material and additional words after the final lyric. The internal schedule validates line count, exact text/section order, increasing timestamps, first vocal at or after intro end, final lyric before vocal cutoff, cutoff at or before track end, and target within preferred duration and canonical maximum. Mutation tests fail locally for lyric count, section order, line order, spelling, incompatible required sections and impossible duration constraints.

## Candidate-2 identity

- Planned request count: **1**; selected attempt: **2**. No attempt-1 or attempt-3 request in selected output.
- Project and quota project: `tovitunes`; location: `global` (supplied explicitly as environment values).
- Provider POST body keys: exactly `model`, `input`. Contract identity is provenance outside the body.
- New candidate-2 fingerprint: `b19e80a509dcf6f4520cea5d7c907dc74590afbc191eb84cf7ca7e100f531900`.
- Historical successful candidate-1 fingerprint: `066ebae73bc548bf1d66500b17e50b48de13bd42aef07ce9288cfd0a8aca22cb`.
- Historical HTTP-400 fingerprint: `564a53429d04570ce0e1d1aa42c283f83dde16783d1a1a58455c464411cf746c`.
- The new identity differs from both historical identities.
- Translated-request SHA-256: `1c06f42284154b2687b633876cdac9bc73f1bc5c0b3db87dc5a8111b048c9ed5` (UTF-8 JSON, sorted keys, compact separators, default JSON ASCII escaping).
- Two deterministic attempt-2 plans and the attempt-2 dry-run produced identical JSON, fingerprints and translated requests; each reported `live_calls = 0`.

Commands executed from the changed clone using the specified live configuration:

```powershell
uv run python -m tovitunes.cli --config "C:\Users\Victus\Documents\Codex\2026-09-25\files-pasted-by-the-user-tovitunes-2\work\ToviTunes-live\config.example.yaml" music-benchmark plan --provider google --attempt 2
# Same plan command executed twice.
uv run python -m tovitunes.cli --config "C:\Users\Victus\Documents\Codex\2026-09-25\files-pasted-by-the-user-tovitunes-2\work\ToviTunes-live\config.example.yaml" music-benchmark run --provider google --attempt 2 --dry-run
```

## Historical evidence and read-only collision check

SQLite was opened with URI `mode=ro` and `PRAGMA query_only=ON`. Every table's sorted row digest and count, plus the database file SHA-256, matched before and after validation. No migration or write connection was used against the live DB. Live `music_requests` count remained **2**, receipts **1**, outputs **1**.

Successful request `451ba192-da15-46d3-b5fd-39712190c838` remains:

- Status: `succeeded`; same fingerprint, full request record, provider interaction ID `b_G2ar2vBPOfusEPxqePgAs`.
- Same complete receipt and output records, including retained measured duration `58.01795918367347` seconds.
- Original audio SHA-256: `613d42c4cbbb133366cde4378585197a82246b3fe7106eb69996466a7e0dd9c0`; independently rehashed before and after.
- Rights state: `unknown`; approval state: `pending`.

Failed request `413d085b-f45b-4de6-8687-1ad7816c71de` remains `terminal_failure`, with the same complete request record and fingerprint; no interaction ID, receipt or output. Neither historical translation was retrofitted with v2.

New-fingerprint SQLite query returned **0 rows**. Thus there is no candidate-2 row, unresolved state, `remote_started`, receipt or output. Dry-run created no row. No historical receipt, output or audio was modified.

## Offline validation and audit

- `uv run pytest -q`: **285 passed**. All provider responses used offline mocks/fixtures; tests do not contact Google.
- `uv run ruff check .`: passed.
- `uv run mypy src`: passed, 41 source files.
- `git diff --check`: passed.
- Tovi character lock: CLI `character-pack validate-lock` with committed v1 intake and artifact lock passed, **48 artifacts**.
- CLI isolation tests separately prove both planning and dry-run call ADC zero times, make zero HTTP calls, and create neither DB nor data directory.
- Actual plan (twice), dry-run and character-lock processes ran with fail-closed ADC, HTTP and socket guards. Each process recorded `adc: 0`, `http: 0`, `socket: 0`.
- Live Lyria generation POSTs: **0**.
- Live provider-resume GETs: **0**. No provider-resume CLI invocation was executed.
- Google music-generation requests: **0**.
- New provider songs generated: **0**. Offline tests use existing audio fixtures and temporary fake artifacts.
- Live DB rows created: **0**; live DB rows mutated: **0**, verified across all tables.
- Paid generation spend for this task: **$0**, supported by zero provider calls. Historical cost evidence is unchanged and not reinterpreted.

The stronger prompt is an instruction, not proof of adherence. Future authorized candidate-2 audio must pass independent measured duration, ASR and QA with unchanged thresholds. No audio trimming or post-generation lyric repair was performed.

READY_FOR_AUTHORIZED_LYRIA_V2_CANDIDATE_2
