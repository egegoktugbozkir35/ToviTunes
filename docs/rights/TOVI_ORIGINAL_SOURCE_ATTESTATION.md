# Tovi original source attestation

On 2026-10-05, the ToviTunes operator confirmed that both the original Tovi
profile and original Tovi banner were generated through ChatGPT / OpenAI image
generation. The operator owns or controls the ToviTunes project and intends to
use those outputs commercially. This is the operator's provenance and permission
attestation for the project's release gate. No generation IDs were recovered for
these two originals, so none are asserted here.

| Source | Artifact ID | SHA-256 |
| --- | --- | --- |
| `source_original_profile` | `db50d001-33ea-4f1c-8c0d-36e2760a2c7a` | `13bb72b1297f5a354736fbb28aae108247a377145658ef5b88f47ed0c8f2d3f1` |
| `source_original_banner` | `de41c057-abc7-45f4-a06f-e7359ff1f350` | `c191f268f6b1e97e3ab5f70ef208b9d579b274d8fa2abdfdcaaff4f64cf12030` |

The exact `original-profile.png` and `original-banner.png` source files in the
earlier Tovi pack intake workspace (`data/tovi-pack-v1/sources/`) were hashed
again on 2026-10-05. Both hashes match the immutable artifact bytes, the
[intake recipe](../../brands/tovitunes/characters/tovi/packs/v1/intake.yaml),
and the dependency pins in the artifact lock. This establishes byte identity
between the reviewed intake files and the release graph roots. The files remain
outside Git by the project's media storage policy.

The existing [OpenAI Terms of Use, effective January 1, 2026](https://openai.com/policies/row-terms-of-use/)
state that, as between the user and OpenAI and to the extent permitted by law,
the user owns the Output and OpenAI assigns its interest, if any, in Output to
the user. The same terms evidence is used for the other OpenAI-generated Tovi
sources in the character intake. The operator's confirmation supplies the
previously missing link that these two exact files were also ChatGPT / OpenAI
image outputs.

The profile and banner decisions are appended to `rights_decisions` through
`AssetStore.record_rights(RightsDecision(...))` with status
`commercial_use_confirmed`, actor `human:operator`, and an evidence URI pointing
to this committed record. Earlier `unknown` rows remain intact. This evidence
supports project provenance and permission for release purposes. It makes no
claim of copyrightability, uniqueness, or trademark clearance.
