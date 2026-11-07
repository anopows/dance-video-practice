"""Offline music time-stretching and single-clock practice media.

R3 runs ahead of playback. The resulting video is retimed to the same clock as
its audio, so the browser plays it at 1x and never stretches it a second time.
Count/seek/loop coordinates remain in original recording seconds.
"""
import json
import math
from pathlib import Path
import subprocess
import sys
import tempfile

SAMPLE_RATE = 44100
ENGINE = "rubberband-r3-v1"
SLOW_MEDIA = {"0.25": "practice-r3-25.mp4", "0.5": "practice-r3-50.mp4", "0.75": "practice-r3-75.mp4"}


def command(args, timeout=1200):
    result = subprocess.run(args, capture_output=True, timeout=timeout)
    if result.returncode:
        raise RuntimeError("Audio processing failed: " + result.stderr.decode(errors="replace")[-2000:])
    return result.stdout


def probe(path):
    return json.loads(command(["ffprobe", "-v", "error", "-protocol_whitelist", "file,pipe",
        "-show_format", "-show_streams", "-of", "json", str(path)], timeout=30))


def decode_audio(source, target, duration):
    # The normalized media already contains its leading silence and AV offset.
    command(["ffmpeg", "-y", "-v", "error", "-protocol_whitelist", "file,pipe", "-i", str(source),
        "-map", "0:a:0", "-af", "apad", "-t", f"{duration:.9f}", "-ar", str(SAMPLE_RATE),
        "-ac", "2", "-c:a", "pcm_f32le", str(target)])


def stretch_wav(source, target, speed, anchors=()):
    if not math.isfinite(speed) or not .25 <= speed <= 2:
        raise ValueError("Unsupported audio speed")
    info = probe(source)["streams"][0]
    sample_rate = int(info["sample_rate"])
    frames = int(info["duration_ts"])
    if sample_rate != SAMPLE_RATE or frames < 1:
        raise ValueError("Expected nonempty normalized PCM audio")
    # Only interior musical anchors: a terminal keyframe makes R3 truncate its
    # tail. The --time ratio, not the map, determines the overall duration.
    points = sorted({round(float(t)*sample_rate) for t in anchors
                     if math.isfinite(float(t)) and 0 < float(t)*sample_rate < frames-1})
    timemap = Path(target).with_suffix(".timemap")
    timemap.write_text("".join(f"{n} {round(n/speed)}\n" for n in points))
    try:
        mapping = ["--timemap", str(timemap)] if points else []
        command(["rubberband", "--fine", "--centre-focus", "--quiet", "--time", str(1/speed),
                 *mapping, str(source), str(target)])
    finally:
        timemap.unlink(missing_ok=True)
    actual = probe(target)["streams"][0]
    # Offline processing must preserve the requested total duration. Reject an
    # unexpected result instead of publishing a gradually drifting soundtrack.
    if abs(int(actual["duration_ts"])-round(frames/speed)) > 2:
        raise RuntimeError("Stretched audio length does not match its video clock")
    return {"engine": ENGINE, "pitchPreserved": True, "mappedAnchors": len(points),
            "sampleRate": sample_rate, "sourceFrames": frames, "outputFrames": int(actual["duration_ts"])}


def retime_video(source, audio, target, speed, duration):
    # Copy the original picture packets, changing their timestamps only. This
    # avoids both a second video generation and unnecessary duplicate frames.
    command(["ffmpeg", "-y", "-v", "error", "-protocol_whitelist", "file,pipe",
        "-itsscale", str(1/speed), "-i", str(source), "-i", str(audio),
        "-map", "0:v:0", "-map", "1:a:0", "-c:v", "copy",
        "-af", "apad,asetpts=PTS-STARTPTS", "-t", f"{duration/speed:.9f}",
        "-c:a", "aac", "-b:a", "192k", "-ar", str(SAMPLE_RATE), "-ac", "2",
        "-map_metadata", "-1", "-map_chapters", "-1", "-video_track_timescale", "90000",
        "-movflags", "+faststart", str(target)])


def prepare_playback(source, folder, data, progress=lambda _: None):
    folder = Path(folder)
    duration = float(data["duration"])
    anchors = data.get("attack", {}).get("beats", [])
    details = {}
    with tempfile.TemporaryDirectory(prefix="smooth-audio-", dir=folder) as temporary:
        temp = Path(temporary)
        original = temp/"original.wav"
        decode_audio(source, original, duration)
        for rate, filename in SLOW_MEDIA.items():
            progress(f"Preparing smooth audio at {round(float(rate)*100)}%")
            slowed = temp/"stretched.wav"
            details[rate] = stretch_wav(original, slowed, float(rate), anchors)
            retime_video(source, slowed, folder/filename, float(rate), duration)
            slowed.unlink()
    return {"engine": ENGINE, "rates": list(SLOW_MEDIA), "details": details,
            "clock": "Original seconds = media seconds multiplied by selected rate", "listen": "mix"}


def main():
    folder = Path(sys.argv[1])
    data = json.loads((folder/"analysis.json").read_text())
    result = prepare_playback(folder/"practice.mp4", folder, data, print)
    (folder/"slow-playback.json").write_text(json.dumps(result, indent=2)+"\n")


if __name__ == "__main__":
    main()
