# five·six — dance videos with counts and slow motion

Upload a dance video and get **1–8 counts** on it, slow-mo that keeps the pitch,
beat-by-beat stepping and repeat. Share a link or download the video with the
counts on. Self-hostable.

- Upload once; send the **Share** link. Visitors can practice and download count
  videos without a password.
- An upload password protects uploads, the video library, renaming, deletion and
  changes to the shared counts. Cloudflare Turnstile runs once, when unlocking.
- Uploads and generated media stay available for **90 days from upload**.
- Download videos with counts at **100% or 25% speed**.
- Full-mix playback at 25%, 50%, and 75% uses prepared, pitch-preserving Rubber
  Band R3 audio. AI-separated bass, drums, piano, and guitar are under
  **More → Hear one instrument**.

[Self-hosting](SELF_HOSTING.md) · [Operations](deploy/RUNBOOK.md) ·
[Third-party software and models](THIRD_PARTY_NOTICES.md)

## Practice and sharing

The starting eight-count is inferred from the music. Counts always continue in
1–8 order through sparse passages. The attack clock moves switches near musical
attacks without inserting, deleting, or renumbering counts. Steady pulse mode is
also available. The display emphasizes 2/4/6/8, more strongly on 4 and 8.
Counts remain synchronized when changing speed, seeking, and looping.

The player keeps three things on screen: play with beat stepping, speed, and
**Repeat** (loops the current eight; widen to 16 or 32 counts from the repeat
bar). Everything else opens as a short sheet under **More**, without covering or
pausing the video: **Fix the counts** ("This is the 1", 10 ms Earlier/Later
nudges, *Follow the band* or *Even beat*), **Hear one instrument**, **Download
with counts**, and **Step by frame**. Signed-in uploaders can also rename and
delete videos there. There are no flashing instrument indicators.

The count strip marks the current count with a solid fill, the 1 with a dot, and
the accents with one colour in three weights (2/6, 4, 8). Downloads use the same
strip.

Share URLs are unlisted, random 128-bit links: **anyone with the link can view
and forward it**. There is no public video directory. Link visitors cannot change
shared count settings or delete videos. Their adjustments stay in their browser
and apply to their exports. People signed in to the shared room can see, edit,
and delete all room videos; there are no individual user accounts.

Automatic counts are estimates, especially for short clips, unclear music, and
already-slowed recordings. Instrument separation can mix rhythm guitar and
percussion. The player allows correction; it is not perfect musical
transcription. Quarter-speed audio can still have audible artifacts.

## How it works

```mermaid
flowchart LR
  U[Uploader: password + Turnstile] -->|bounded upload parts| A[FastAPI behind Caddy]
  V[Visitor with share link] -->|practice and export| A
  A --> Q[(SQLite jobs and alignment)]
  A --> R[(Private Cloudflare R2)]
  Q --> W[Single media worker]
  W <--> R
  W --> S[Networkless sandbox: FFmpeg and models]
  R -->|signed playback URLs| V
```

Beat This! estimates beats and downbeats. A pretrained metrical-structure model
selects one global eight-count phase. Acoustic onset evidence and Demucs stem
estimates refine timing. Media processing runs on the host in an unprivileged
bubblewrap sandbox with no network or runtime secrets.

The default timing follows coherent detected beats as the tempo changes, fills
missing beat slots, and rejects isolated extra detections. Bar-phase consensus
and observed-slot coverage prevent a faster subdivision from winning merely by
fitting more timestamps. Alternative analysis rates use the same tolerance in
original recording seconds. The straight tempo grid remains a fallback and the
optional *Even beat* mode. Opening counts need repeated audible pulse evidence,
not perfect agreement on each attack; leading silence stays hidden. These are
consistency checks, not calibrated musical-accuracy scores.

*Follow the band* separates acoustic-event identity from waveform attack timing.
It groups candidates only when both their spectral peaks and waveform starts
fit one compact, tempo-scaled cluster. Original-mix bands establish audibility;
stems can refine the onset but are not independent confidence votes. Competing
notes remain uncertain, and a missing supplemental candidate cannot discard a
valid full-mix attack. Waveform searches clipped by their boundaries are excluded.

Onset selection happens inside the acoustic ensemble, including three waveform
rise thresholds, three FFT configurations, mix-only/joint evidence, four
continuity settings and five shifted beat priors (360 combinations). All five
prior groups must agree on the acoustic event first, then on any finer onset.
Refinement must overlap the acoustic timing range at its 12 ms resolution.
Uncertain refinement preserves a stable acoustic attack; an ambiguous acoustic
event retains the pulse. Abstaining trials cannot vote for
an unchanged timestamp. There is no post-consensus early-shift patch or fixed
offset; corrections can go either direction. These checks measure stability,
not the probability that a musical interpretation or eight-count anchor is right.

The three Python files in `research/` supply selected analysis definitions
through `choreo/research_adapter.py`; they are not standalone tools. Model
source and weights are downloaded separately and verified against the manifest.

The server enforces 500 MB / 10-minute input limits, room-wide upload quotas,
one analysis worker, bounded export queues, and rate limits. Guest exports share
the room budget plus a per-IP limit. Identical renders are reused.

Project access ends at the 90-day deadline. The worker deletes stored media,
analysis, and exports; R2 lifecycle rules provide a backstop. Signed media URLs
last at most 15 minutes and never outlive the project. A previously issued URL
can remain usable until its short expiry or object deletion.

## Development

Use Python 3.12, Node.js, FFmpeg, Rubber Band CLI 3+, and
[uv](https://docs.astral.sh/uv/). On Ubuntu install `rubberband-cli`;
on macOS Homebrew provides `rubberband`.

```sh
uv sync --frozen --extra dev
bash scripts/check.sh
```

Tests cover access boundaries, guest exports, count settings, alignment revisions,
upload validation, quotas, expiry, retention migration, playback clocks, and real
FFmpeg / Rubber Band processing, tempo drift, missing/extra detections, and
audible versus silent openings. A frozen detector-output fixture covers the
late-start/doubled-tempo regression without including uploaded media.
Tests need no model weights or Cloudflare
credentials. Full analysis runs on the Linux host in the setup guide.

## License

The application source is MIT-licensed. Third-party software and models retain
their own terms. The pinned hypermeter repository has no explicit top-level
license; obtain the authors' permission where needed. No upstream hypermeter
source, weights, credentials, or uploaded media are included here.
See [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).

### Audio timing regression checks

The ordinary app tests do not measure musical accuracy. With the Linux worker
and development dependencies installed, run the reproducible waveform checks:

```sh
python scripts/benchmark-timing.py --output /tmp/timing-development.json --check
python scripts/benchmark-timing.py --suite validation --seed 841903 --output /tmp/timing-validation.json --check
```

The generator in `tests/timing_controls.py` records note beginnings before any
analysis. These tests exercise the acoustic stage with known pulse priors and
controlled timing errors; they do not evaluate the neural beat/downbeat model. It covers 72–312 BPM, tempo drift, missing notes, silence, quiet/noisy
recordings, different attack slopes, louder swung pickups, instrument entrances,
and early/late model priors. The separate validation settings change tempi,
gains, envelopes and noise. Gates reject lost sounding beats, false attacks in
breaks, wrong entrances, reversed counts, accepted attacks over 50 ms from truth,
and excessive median/tail error. `--baseline-dir` can compare an older importable
`choreo` package and adds a per-case median-regression gate.

The first five seconds are included. Many general beat evaluations discard
that interval and use a 70 ms matching tolerance, which would miss the reported
entrance/attack problem ([mir_eval beat evaluation](https://mir-eval.readthedocs.io/latest/api/beat.html)).
Generated signals validate mechanisms, not real-song listening accuracy or the
choice of count 1. Real recordings still need independently reviewed anchors.
