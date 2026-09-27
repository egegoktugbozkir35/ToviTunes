# Render donor provenance and license

Inspected first-party-authorized repository `egegoktugbozkir35/mpt-movie-narrator`
at commit `32b5cd33776881f17b1594cce8716443c61e0bb4` on 2026-09-27.

Repository ownership does not establish authorship. The inspected implementation
files have `SPDX-FileCopyrightText: 2026 zcbacxc` and
`SPDX-License-Identifier: AGPL-3.0-or-later`. Git history credits 早川
(`zcbacxc@users.noreply.github.com`), including layout introduction `370dc92`
and later SPDX standardization `e7bcc48`. These are treated as third-party
licensed code; no owner-authorship or relicensing claim is made.

`render/layout.py` copies the tested pure `compute_fit_box` helper, with small
documented adaptations. `render/ffmpeg.py` adapts discovery and subprocess/mux
patterns from `utils/ffmpeg_bin.py`, `pipeline/render.py`, and `utils/process.py`.
`render/qa.py` adapts the ffprobe stream/fraction/encoding-check structure from
`utils/video_qa.py`. `render/composer.py` adapts two-stage encoding and resource
cleanup, with a process worker instead of thread/PID substring discovery.
Original notices are preserved on these adapted modules. Full license text is
in `LICENSES/AGPL-3.0-or-later.txt`; the integrated derivative renderer is
distributed under AGPL-3.0-or-later. Anyone distributing or providing remote
network access to this derivative must satisfy that license, including its
corresponding-source requirements. This local pilot exposes no network service.

Minimum requested donor tests inspected: `test_ffmpeg_bin.py`, `test_render.py`,
`test_render_coverage.py`, `test_render_real.py`, `test_render_template.py`,
`test_video_layout.py`, plus `test_v120_render_process.py`. The relevant layout,
resolution, timeout, stream mapping and atomic-publication cases inform focused
ToviTunes tests. No subtitle, watermark, GPU, HDR, 4K, or template subsystem is
imported. No vendored/upstream implementation beyond the declared AGPL source
was identified in the adopted pieces.

MoviePy 2.2.1 remains an optional MIT-licensed external dependency. Its transitive
imageio-ffmpeg package may be installed by the extra, but the renderer always
sets MoviePy to the explicitly resolved system binary; no binary download or
bundled-binary discovery occurs during rendering.
