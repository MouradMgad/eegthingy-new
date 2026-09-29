"""MP4 capture of the rendered Panda3D maze window."""

from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from pathlib import Path

import numpy as np


class MazeVideoRecorder:
    def __init__(self, window, output, fps=20.0):
        import cv2

        self.cv2 = cv2
        self.window = window
        self.output = Path(output).resolve()
        # Preserve the historical name for a live ``maze.mp4`` recording, but
        # give reconstructed/replayed videos their own non-destructive status
        # file instead of overwriting the original session provenance.
        self.status_path = self.output.with_name(
            "maze_recording_status.json"
            if self.output.name.lower() == "maze.mp4"
            else f"{self.output.stem}_recording_status.json"
        )
        self.fps = float(fps)
        self.width = int(window.getXSize())
        self.height = int(window.getYSize())
        self.frames = 0
        self.capture_failures = 0
        self.error = None
        self.started = datetime.now(timezone.utc)
        self._next_capture = None
        self._closed = False
        self.output.parent.mkdir(parents=True, exist_ok=True)
        self.writer = cv2.VideoWriter(
            str(self.output), cv2.VideoWriter_fourcc(*"mp4v"), self.fps,
            (self.width, self.height),
        )
        if not self.writer.isOpened():
            self.error = f"could not create MP4 writer for {self.output}"
            self._write_status("failed")
            raise RuntimeError(self.error)
        self._write_status("recording")

    def capture_if_due(self, timeline_s=None):
        """Capture at ``fps`` using wall time or a deterministic replay clock.

        Passing the replay's simulation time allows an offscreen replay to run
        faster than real time while still producing the correct video duration
        and frame count.
        """
        if self._closed:
            return
        now = time.perf_counter() if timeline_s is None else float(timeline_s)
        if self._next_capture is None:
            self._next_capture = now
        if now < self._next_capture:
            return
        self._next_capture += 1.0 / max(self.fps, 1.0)
        try:
            texture = self.window.getScreenshot()
            if texture is None:
                self.capture_failures += 1
                return
            rgb = np.frombuffer(texture.getRamImageAs("RGB"), dtype=np.uint8)
            rgb = rgb.reshape((texture.getYSize(), texture.getXSize(), 3))
            # Panda3D's RAM image origin is lower-left; video frames are top-left.
            bgr = self.cv2.cvtColor(np.flipud(rgb), self.cv2.COLOR_RGB2BGR)
            self.writer.write(bgr)
            self.frames += 1
        except Exception as error:
            self.error = repr(error)
            self.capture_failures += 1

    def close(self):
        if self._closed:
            return
        self._closed = True
        self.writer.release()
        self._write_status("failed" if self.error else "complete")

    def _write_status(self, status):
        payload = {
            "format": "eegthingy-maze-recording-v1",
            "status": status,
            "output": str(self.output),
            "started_utc": self.started.isoformat(),
            "updated_utc": datetime.now(timezone.utc).isoformat(),
            "fps": self.fps,
            "width": self.width,
            "height": self.height,
            "frames_written": self.frames,
            "capture_failures": self.capture_failures,
            "error": self.error,
        }
        self.status_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")

