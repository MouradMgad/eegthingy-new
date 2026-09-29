# Paper results

Each live maze run creates one overwrite-safe folder named
`subject_<subject-id>_test_<test-id>`. If that name already exists, a timestamp
is appended. The folder contains the raw EEG, decoder decisions, diagnostic
logs, maze CSV/NPZ, optional camera and maze MP4 recordings, a machine-readable
session manifest, and `results summary.xlsx`.

The camera and maze recordings are separate silent MP4 files. JSON status
sidecars record their resolution, frame rate, frames written, and capture
errors so a failed or incomplete recording is diagnosable after the session.

