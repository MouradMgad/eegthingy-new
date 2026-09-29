"""Live left/right SSVEP control panel for the websocket TIAGo maze."""

from __future__ import annotations

import csv
import json
import os
import subprocess
import sys
import time
import tkinter as tk
from datetime import datetime
from pathlib import Path

import mne
import numpy as np
from scipy.signal import resample

from classifier.smoother import Smoother
from classifier.ws import WebSocket

from .acquisition import OpenBCIRecorder
from .config import DEFAULT_RECORDINGS_DIR, EEG_CHANNEL_NAMES
from .decoder import SSVEPDecoder


ROOT = Path(__file__).resolve().parents[1]


def _save_live_capture(captured, subject: str, decisions: list[dict]) -> tuple[Path, Path]:
    DEFAULT_RECORDINGS_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    stem = f"sub-{subject or 'unknown'}_{stamp}_ssvep_live_maze"
    fif_path = DEFAULT_RECORDINGS_DIR / f"{stem}_raw.fif"
    csv_path = DEFAULT_RECORDINGS_DIR / f"{stem}_decisions.csv"
    if captured.eeg_uv.shape[1] == 0:
        raise ValueError("No live EEG samples were captured")
    info = mne.create_info(list(EEG_CHANNEL_NAMES), captured.sample_rate, ["eeg"] * 15)
    raw = mne.io.RawArray(captured.eeg_uv / 1e6, info, verbose="ERROR")
    raw.set_montage("standard_1020", on_missing="warn")
    raw.info["description"] = json.dumps({
        "experiment": "live SSVEP maze control",
        "subject": subject,
        "commands": {"left": 1, "right": 2},
    })
    raw.save(fif_path, overwrite=False, verbose="ERROR")
    fields = ["time", "prediction", "confidence", "p_left", "p_right", "committed"]
    with csv_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(decisions)
    return fif_path, csv_path


class LiveMazeWindow:
    def __init__(self, decoder: SSVEPDecoder, recorder: OpenBCIRecorder, *, subject: str,
                 maze_number: int, synthetic: bool = False):
        self.decoder = decoder
        self.recorder = recorder
        self.subject = subject
        self.maze_number = maze_number
        self.synthetic = synthetic
        self.ws = WebSocket()
        self.smoother = Smoother(self.ws, n=5, m=4, conf_floor=0.60, dwell=2)
        self.decisions: list[dict] = []
        self.maze_process: subprocess.Popen | None = None
        self.closed = False
        self.last_analysis = 0.0
        self.started = time.perf_counter()

        self.root = tk.Tk()
        self.root.title("SSVEP live maze control")
        self.root.geometry("900x300+40+40")
        self.root.attributes("-topmost", True)
        self.root.configure(bg="#111318")
        self.root.protocol("WM_DELETE_WINDOW", self.close)

        self.status = tk.StringVar(value="Waiting for a full EEG window...")
        tk.Label(self.root, text="LOOK AT LEFT OR RIGHT WHEN THE ROBOT STOPS",
                 bg="#111318", fg="white", font=("Segoe UI", 16, "bold")).pack(pady=(12, 5))
        row = tk.Frame(self.root, bg="#111318")
        row.pack(fill="both", expand=True, padx=35)
        self.left = self._target(row, "LEFT", decoder.frequencies_hz["left"])
        self.right = self._target(row, "RIGHT", decoder.frequencies_hz["right"])
        tk.Label(self.root, textvariable=self.status, bg="#111318", fg="#f4b3b7",
                 font=("Consolas", 11)).pack(pady=(4, 12))

    def _target(self, parent, name: str, frequency: float):
        frame = tk.Frame(parent, bg="#111318")
        frame.pack(side="left", fill="both", expand=True, padx=25)
        square = tk.Canvas(frame, width=125, height=125, bg="#111318", highlightthickness=0)
        square.pack()
        shape = square.create_rectangle(5, 5, 120, 120, fill="white", outline="#c81d25", width=5)
        tk.Label(frame, text=f"{name}  |  {frequency:g} Hz", bg="#111318", fg="white",
                 font=("Segoe UI", 13, "bold")).pack(pady=3)
        return square, shape, float(frequency)

    def start(self):
        self.recorder.begin()
        self.ws.start()
        sim_dir = ROOT / "simulation"
        env = dict(os.environ)
        env["PYTHONPATH"] = os.pathsep.join(
            item for item in (str(sim_dir / "src"), env.get("PYTHONPATH", "")) if item
        )
        meta = json.dumps({"mode": "live-ssvep-maze", "decoder": "spectral SSVEP"})
        self.maze_process = subprocess.Popen([
            sys.executable, "-m", "tiago_maze", "--standard-maze", str(self.maze_number),
            "--no-manual-keys", "--subject", self.subject, "--test", "live-ssvep-maze",
            "--meta", meta,
        ], cwd=sim_dir, env=env)
        self._tick()
        self.root.mainloop()

    def _tick(self):
        if self.closed:
            return
        now = time.perf_counter()
        for canvas, shape, frequency in (self.left, self.right):
            bright = np.sin(2 * np.pi * frequency * (now - self.started)) >= 0
            canvas.itemconfigure(shape, fill="white" if bright else "#20242d")
        if now - self.last_analysis >= 0.25:
            self.last_analysis = now
            self._classify()
        if self.maze_process is not None and self.maze_process.poll() is not None:
            self.close()
            return
        self.root.after(10, self._tick)

    def _classify(self):
        board = self.recorder.board
        if board is None:
            return
        sfreq = self.recorder.sample_rate
        needed = int(round(self.decoder.window_s * sfreq))
        data = board.get_current_board_data(needed)
        if data.shape[1] < needed:
            return
        epoch = data[self.recorder.eeg_rows, -needed:].astype(np.float64) / 1e6
        model_n = int(round(self.decoder.window_s * self.decoder.sample_rate))
        if epoch.shape[1] != model_n:
            epoch = resample(epoch, model_n, axis=1)
        probabilities = self.decoder.predict_proba(epoch, allowed=("left", "right"))[0]
        index = int(np.argmax(probabilities))
        label = (1, 2)[index]
        confidence = float(probabilities[index])
        _, _, final = self.smoother.add(
            prediction=label,
            confidence=confidence,
            probs={1: probabilities[0], 2: probabilities[1]},
        )
        self.status.set(
            f"LEFT {probabilities[0] * 100:5.1f}%   RIGHT {probabilities[1] * 100:5.1f}%"
            + (f"   |   COMMITTED {'LEFT' if final == 1 else 'RIGHT'}" if final else "")
        )
        self.decisions.append({
            "time": datetime.now().isoformat(timespec="milliseconds"),
            "prediction": "left" if label == 1 else "right",
            "confidence": f"{confidence:.6f}",
            "p_left": f"{probabilities[0]:.6f}",
            "p_right": f"{probabilities[1]:.6f}",
            "committed": int(final),
        })

    def close(self):
        if self.closed:
            return
        self.closed = True
        if self.maze_process is not None and self.maze_process.poll() is None:
            self.maze_process.terminate()
        self.ws.stop()
        try:
            captured = self.recorder.finish()
            try:
                fif_path, csv_path = _save_live_capture(captured, self.subject, self.decisions)
            except (OSError, ValueError) as error:
                print(f"Could not save live SSVEP recording: {error}")
            else:
                print(f"Live SSVEP EEG saved: {fif_path}")
                print(f"Live SSVEP decisions saved: {csv_path}")
        finally:
            self.recorder.close()
            self.root.destroy()


def run_live_maze(model_path: str | Path, *, subject: str, maze_number: int = 1,
                  synthetic: bool = False, serial_port: str = "") -> None:
    decoder = SSVEPDecoder.load(model_path)
    recorder = OpenBCIRecorder(synthetic=synthetic, serial_port=serial_port).connect()
    LiveMazeWindow(decoder, recorder, subject=subject, maze_number=maze_number,
                   synthetic=synthetic).start()
