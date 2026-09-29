# Four-command SSVEP data collector

This folder is intentionally self-contained. The only connection to the rest of
the repository is option `5` in `main.py`.

## What the subject does

The subject sees four targets:

- top: **FORWARD** (default 8 Hz)
- right: **RIGHT** (default 10 Hz)
- bottom: **BACKWARD** (default 12 Hz)
- left: **LEFT** (default 15 Hz)

Each randomized block contains every command exactly once. A trial has three
phases: a static planning cue, simultaneous flicker of all four targets while
the subject looks at the cued one, and rest. A session-level preparation phase
runs before the trials. When preparation is longer than five seconds, an
embedded animation demonstrates the task.

The GUI controls subject/session IDs, repetitions, every phase duration, all
four frequencies, stimulus size and spacing, brightness, random seed, serial
port, output folder, full screen, and synthetic software-test mode.

## Run

From the repository root:

```powershell
uv run python main.py
```

Choose `5) SSVEP Workbench`. The menu contains:

1. labeled four-command data collection;
2. a cap check that reports GOOD, CHECK, or BAD for every connected electrode;
3. model training from one or more SSVEP FIF recordings;
4. held-out testing with accuracy and a four-class confusion matrix; and
5. live left/right SSVEP control of a standard TIAGo maze.

The cap check flags flat/disconnected inputs, saturation, unusually large
artifacts, and strong 60 Hz contamination. It is a signal sanity check, not a
substitute for a hardware impedance meter.

Training learns per-channel reliability weights for harmonic spectral evidence
at the four configured stimulus frequencies. Saved models live in
`models/ssvep/`. Keep testing recordings separate from training recordings.

The live maze uses only LEFT and RIGHT because the maze controller accepts turn
commands. An always-on-top stimulus window remains visible while the simulation
runs. Continuous EEG and per-window decisions are saved under
`ssvep/recordings/` when the live window closes.

Use synthetic mode only to check the screen flow and file-writing path. It does
not contain human SSVEP and must never be used as training data.

Flickering light can trigger symptoms in photosensitive people. Use the lab's
approved participant screening and stopping procedure; the real-board start
flow includes a safety confirmation.

## Saved data

Every session creates three timestamped files under `ssvep/recordings` unless
another output folder is selected:

- `*_raw.fif`: 15 EEG channels in volts, accelerometer channels when exposed by
  the board, a `STI 014` marker channel, and MNE annotations.
- `*_events.csv`: one row for every preparation/planning/action/rest phase,
  including the attended command and frequency.
- `*_metadata.json`: settings, exact channel order, sample rate, randomized
  protocol, marker/sample locations, timing, and whether the session was
  aborted.

During every action period all four targets flicker. The annotation's command
and frequency identify the target the subject was instructed to attend. Marker
descriptions use this form:

```text
ssvep/forward/8Hz/action/t001
```

The physical channel 8 is excluded because it is the known disconnected input
in this 15-electrode setup. Legacy T3/T4/T5/T6 names are saved as their modern
10-20 equivalents T7/T8/P7/P8.

## Display timing note

The stimulus phase is calculated from a monotonic clock instead of counting GUI
callbacks, preventing timing drift. Actual light output is still limited by the
monitor refresh rate. Validate a research display with a photodiode when exact
frequency timing is critical; the event file's observed values describe the
software transition schedule, not a photodiode measurement.
