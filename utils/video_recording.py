"""Crash-tolerant webcam recording for live experimental sessions."""

from __future__ import annotations

import json
import os
import threading
import time
from datetime import datetime, timezone
from pathlib import Path


class CameraRecorder:
    """Record one webcam to MP4 on a background thread.

    The first frame is acquired synchronously so a paper session never starts
    under the false assumption that its camera is recording.
    """

    def __init__(self, output, camera_index: int = 0, fps: float = 30.0):
        self.output = Path(output).resolve()
        self.camera_index = int(camera_index)
        self.fps = float(fps)
        self.status_path = self.output.with_name("camera_recording_status.json")
        self._capture = None
        self._writer = None
        self._thread = None
        self._stop = threading.Event()
        self._first_frame = None
        self._started = None
        self._frames = 0
        self._failed_reads = 0
        self._error = None
        self._backend = None
        self._size = None

    def start(self):
        import cv2

        self.output.parent.mkdir(parents=True, exist_ok=True)
        backends = []
        if os.name == "nt":
            backends.extend((cv2.CAP_DSHOW, cv2.CAP_MSMF))
        backends.append(cv2.CAP_ANY)
        for backend in backends:
            capture = cv2.VideoCapture(self.camera_index, backend)
            if capture.isOpened():
                ok, frame = capture.read()
                if ok and frame is not None:
                    self._capture = capture
                    self._first_frame = frame
                    self._backend = int(backend)
                    break
            capture.release()
        if self._capture is None:
            self._error = f"camera {self.camera_index} could not be opened or returned no frame"
            self._write_status("failed")
            raise RuntimeError(self._error)

        height, width = self._first_frame.shape[:2]
        self._size = (int(width), int(height))
        source_fps = float(self._capture.get(cv2.CAP_PROP_FPS) or 0.0)
        if source_fps >= 1.0:
            self.fps = min(self.fps, source_fps)
        self._writer = cv2.VideoWriter(
            str(self.output), cv2.VideoWriter_fourcc(*"mp4v"), self.fps, self._size
        )
        if not self._writer.isOpened():
            self._capture.release()
            self._capture = None
            self._error = f"could not create MP4 writer for {self.output}"
            self._write_status("failed")
            raise RuntimeError(self._error)

        self._started = datetime.now(timezone.utc)
        self._write_status("recording")
        self._thread = threading.Thread(target=self._run, name="paper-camera-recorder", daemon=True)
        self._thread.start()
        return self

    def _run(self):
        period = 1.0 / max(self.fps, 1.0)
        next_frame = time.perf_counter()
        frame = self._first_frame
        self._first_frame = None
        try:
            while not self._stop.is_set():
                if frame is None:
                    ok, frame = self._capture.read()
                    if not ok or frame is None:
                        self._failed_reads += 1
                        frame = None
                        time.sleep(min(period, 0.05))
                        continue
                now = time.perf_counter()
                if now < next_frame:
                    time.sleep(min(next_frame - now, 0.02))
                    continue
                self._writer.write(frame)
                self._frames += 1
                next_frame = max(next_frame + period, now)
                frame = None
        except Exception as error:  # status file preserves asynchronous failures
            self._error = repr(error)
        finally:
            if self._capture is not None:
                self._capture.release()
            if self._writer is not None:
                self._writer.release()

    def stop(self):
        if self._thread is None:
            return
        self._stop.set()
        self._thread.join(timeout=5.0)
        if self._thread.is_alive() and self._error is None:
            self._error = "camera thread did not stop within five seconds"
        status = "failed" if self._error else "complete"
        self._write_status(status)
        return self.output if self.output.exists() else None

    def _write_status(self, status: str):
        payload = {
            "format": "eegthingy-camera-recording-v1",
            "status": status,
            "camera_index": self.camera_index,
            "backend": self._backend,
            "output": str(self.output),
            "started_utc": self._started.isoformat() if self._started else None,
            "updated_utc": datetime.now(timezone.utc).isoformat(),
            "fps": self.fps,
            "width": self._size[0] if self._size else None,
            "height": self._size[1] if self._size else None,
            "frames_written": self._frames,
            "failed_reads": self._failed_reads,
            "error": self._error,
        }
        self.status_path.parent.mkdir(parents=True, exist_ok=True)
        self.status_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")

