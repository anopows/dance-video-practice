"""Validation and normalization. Called only inside the networkless sandbox."""
import json
import math
import subprocess
import sys
from fractions import Fraction
from pathlib import Path


class InvalidVideo(ValueError):
    pass


def run(args, timeout=1200):
    result = subprocess.run(args, capture_output=True, timeout=timeout)
    if result.returncode:
        sys.stderr.write(result.stderr.decode(errors="replace")[-4000:])
        raise InvalidVideo("This video couldn't be read all the way through. Export it as an MP4 and try again.")
    return result.stdout


def inspect(path):
    if not 0 < Path(path).stat().st_size <= 500_000_000:
        raise InvalidVideo("Videos must be under 500 MB.")
    data = json.loads(run(["ffprobe", "-v", "error", "-protocol_whitelist", "file,pipe",
        "-show_format", "-show_streams", "-of", "json", str(path)], timeout=30))
    formats = set(data.get("format", {}).get("format_name", "").split(","))
    if not formats & {"mov", "mp4", "matroska", "webm"}:
        raise InvalidVideo("This doesn't look like a video file. Use MP4, MOV, M4V or WebM.")
    videos = [s for s in data.get("streams", []) if s.get("codec_type") == "video" and not s.get("disposition", {}).get("attached_pic")]
    audios = [s for s in data.get("streams", []) if s.get("codec_type") == "audio"]
    if len(videos) != 1 or not audios:
        raise InvalidVideo("The video needs a picture and an audio track with music.")
    video, audio = videos[0], audios[0]
    if video.get("codec_name") not in {"h264", "hevc", "vp8", "vp9", "av1", "mpeg4", "prores", "mjpeg"}:
        raise InvalidVideo("five·six can't read this video's format. Export it as an H.264 MP4 and try again.")
    if audio.get("codec_name") not in {"aac", "mp3", "opus", "vorbis", "alac", "flac", "pcm_s16le", "pcm_s24le", "pcm_s32le", "pcm_f32le", "pcm_s16be", "pcm_s24be"}:
        raise InvalidVideo("five·six can't read this video's sound. Export it with AAC audio and try again.")
    try:
        duration = float(data["format"]["duration"])
        width, height = int(video["width"]), int(video["height"])
        fps = float(Fraction(video.get("avg_frame_rate", "0/1")))
        peak_fps = float(Fraction(video.get("r_frame_rate", "0/1")))
        start = float(video.get("start_time", 0))
        rate, channels = int(audio["sample_rate"]), int(audio["channels"])
    except (ValueError, KeyError, ZeroDivisionError):
        raise InvalidVideo("This video file is incomplete or damaged. Export it again and retry.")
    if not math.isfinite(duration) or not 1 <= duration <= 600:
        raise InvalidVideo("Videos must be between 1 second and 10 minutes long.")
    if not 0 < width <= 4096 or not 0 < height <= 4096 or width*height > 4096*2160:
        raise InvalidVideo("Videos must be 4K or smaller.")
    if not 0 < fps <= 120 or not 0 < peak_fps <= 120:
        raise InvalidVideo("Videos must be between 1 and 120 frames per second.")
    if not 8000 <= rate <= 192000 or not 1 <= channels <= 8 or not math.isfinite(start):
        raise InvalidVideo("This video's sound format isn't supported. Export it with standard AAC audio.")
    return {"duration": duration, "width": width, "height": height, "fps": fps,
            "start": start, "videoIndex": video["index"], "audioIndex": audio["index"]}


def normalize(source, target, info):
    # Full decode catches corrupt packets. Audio retains its relative stream
    # offset, padded from the first video frame. All later analysis uses this
    # exact playback copy, eliminating a second clock or MP3 encoder delay.
    run(["ffmpeg", "-y", "-v", "error", "-xerror", "-err_detect", "explode",
        "-protocol_whitelist", "file,pipe", "-copyts", "-i", str(source),
        "-map", f"0:{info['videoIndex']}", "-map", f"0:{info['audioIndex']}",
        "-vf", "setpts=PTS-STARTPTS,scale=w='min(1280,iw)':h='min(1280,ih)':force_original_aspect_ratio=decrease:force_divisible_by=2,setsar=1,fps=30",
        "-af", f"asetpts=PTS-({info['start']})/TB,aresample=async=1:first_pts=0,apad",
        "-t", str(info["duration"]), "-map_metadata", "-1", "-map_chapters", "-1",
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "22", "-pix_fmt", "yuv420p",
        "-threads", "3", "-c:a", "aac", "-b:a", "160k", "-ar", "44100", "-ac", "2",
        "-movflags", "+faststart", str(target)])


def solo_video(source, audio, target, duration):
    """Mux a time-aligned PCM stem with the same normalized video packets."""
    run(["ffmpeg", "-y", "-v", "error", "-protocol_whitelist", "file,pipe",
        "-i", str(source), "-i", str(audio), "-map", "0:v:0", "-map", "1:a:0",
        "-c:v", "copy", "-af", "apad", "-t", str(duration),
        "-c:a", "aac", "-b:a", "160k", "-ar", "44100", "-ac", "2",
        "-map_metadata", "-1", "-map_chapters", "-1", "-movflags", "+faststart", str(target)])

