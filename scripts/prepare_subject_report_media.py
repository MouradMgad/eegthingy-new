from __future__ import annotations

import json
from pathlib import Path

import cv2
from PIL import Image, ImageDraw, ImageFont


ROOT = Path(__file__).resolve().parents[1]
RESULTS = ROOT / "paper results"
OUT = ROOT / "report_work" / "thumbs"

SESSIONS = {
    "subject_10_test_1": 193.625,
    "subject_10_test_2": 143.438,
    "subject_6_test_1": 116.0,
    "subject_6_test_2": 243.625,
}


def extract_frame(video: Path, output: Path, requested_s: float) -> dict:
    cap = cv2.VideoCapture(str(video))
    if not cap.isOpened():
        raise RuntimeError(f"Could not open {video}")
    fps = float(cap.get(cv2.CAP_PROP_FPS) or 0.0)
    frame_count = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
    duration_s = frame_count / fps if fps > 0 else 0.0
    timestamp_s = min(max(0.0, requested_s), max(0.0, duration_s - 0.25))
    cap.set(cv2.CAP_PROP_POS_MSEC, timestamp_s * 1000.0)
    ok, frame = cap.read()
    if not ok or frame is None:
        raise RuntimeError(f"Could not read frame from {video} at {timestamp_s:.3f}s")
    output.parent.mkdir(parents=True, exist_ok=True)
    height, width = frame.shape[:2]
    target_ratio = 16 / 10
    if width / height > target_ratio:
        crop_width = int(round(height * target_ratio))
        left = (width - crop_width) // 2
        frame = frame[:, left : left + crop_width]
    elif width / height < target_ratio:
        crop_height = int(round(width / target_ratio))
        top = (height - crop_height) // 2
        frame = frame[top : top + crop_height, :]
    frame = cv2.resize(frame, (960, 600), interpolation=cv2.INTER_AREA)
    if not cv2.imwrite(str(output), frame, [cv2.IMWRITE_JPEG_QUALITY, 92]):
        raise RuntimeError(f"Could not write {output}")
    cap.release()
    return {
        "path": str(video),
        "fps": fps,
        "frame_count": frame_count,
        "duration_s": duration_s,
        "thumbnail_time_s": timestamp_s,
        "thumbnail": str(output),
    }


def main() -> None:
    manifest = {}
    for session, timestamp_s in SESSIONS.items():
        folder = RESULTS / session
        manifest[session] = {
            "face": extract_frame(
                folder / "camera.mp4", OUT / f"{session}_face.jpg", timestamp_s
            ),
            "maze": extract_frame(
                folder / "maze_session_replay.mp4",
                OUT / f"{session}_maze.jpg",
                timestamp_s,
            ),
        }
    manifest_path = ROOT / "report_work" / "media_manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    panels = []
    for session in SESSIONS:
        for kind in ("face", "maze"):
            image = Image.open(manifest[session][kind]["thumbnail"]).convert("RGB")
            image.thumbnail((560, 340))
            panel = Image.new("RGB", (600, 390), "white")
            x = (600 - image.width) // 2
            panel.paste(image, (x, 35))
            draw = ImageDraw.Draw(panel)
            draw.text((16, 10), f"{session}  {kind}", fill="black", font=ImageFont.load_default())
            panels.append(panel)
    sheet = Image.new("RGB", (1200, 1560), "#dddddd")
    for index, panel in enumerate(panels):
        sheet.paste(panel, ((index % 2) * 600, (index // 2) * 390))
    sheet.save(ROOT / "report_work" / "contact_sheet.jpg", quality=92)
    print(manifest_path)


if __name__ == "__main__":
    main()
