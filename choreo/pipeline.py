"""Networkless worker entry point: validate, normalize and analyze one video."""
import gc
import hashlib
import json
import os
from pathlib import Path
import sys

from .media import inspect, normalize, solo_video, run, InvalidVideo


def write(path, data):
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, allow_nan=False, separators=(",", ":")))
    tmp.replace(path)


# The player shows four steps: upload/check, beat, one, slowmo. Sub-step
# callbacks from the models keep the most recent step key.
STEP = {"value": "check"}


def stage(folder, text, step=None):
    if step:
        STEP["value"] = step
    write(folder/"progress.json", {"stage": text, "step": STEP["value"]})


def analyze(folder, models):
    import numpy as np
    import librosa
    import soundfile as sf
    import torch
    from scipy.signal import butter, sosfiltfilt
    from beat_this.inference import Audio2Beats
    from .research_adapter import functions, model
    from .beat_grid import detect, pulse_slots
    from .pulse_track import phase_bars, times_at
    from .pulse_timing import ALGORITHM, feature_cache, select_clock
    torch.set_num_threads(3)
    torch.set_num_interop_threads(1)
    source = folder/"original"
    stage(folder, "Validating and preparing video", "check")
    info = inspect(source)
    normalize(source, folder/"practice.mp4", info)
    actual = inspect(folder/"practice.mp4")
    duration = actual["duration"]
    run(["ffmpeg", "-y", "-v", "error", "-i", str(folder/"practice.mp4"), "-map", "0:a:0",
         "-ac", "2", "-ar", "44100", str(folder/"audio.wav")])
    run(["ffmpeg", "-y", "-v", "error", "-ss", str(min(1, duration/2)), "-i", str(folder/"practice.mp4"),
         "-frames:v", "1", "-update", "1", str(folder/"poster.jpg")])
    stereo, sr = sf.read(folder/"audio.wav", dtype="float32", always_2d=True)
    y = librosa.resample(stereo.mean(axis=1), orig_sr=sr, target_sr=22050)
    if float(np.sqrt(np.mean(y*y))) < .0001:
        raise InvalidVideo("The music is too quiet to count. Try a clip where the music is clearly audible.")
    stage(folder, "Finding music beats", "beat")
    beat_model = Audio2Beats(checkpoint_path=str(models/"beat_this-final0.ckpt"), device="cpu", dbn=False)
    result = detect(beat_model, y, 22050, duration, lambda text:stage(folder,text))
    del beat_model
    gc.collect()
    if result is None:
        raise InvalidVideo("No steady beat was found. Try a longer clip with clearer music.")
    grid, count_quality, beats, downbeats = result
    f = functions()
    fixed, _ = f["fixed_bars"](grid, duration)
    phase_beats, bars = phase_bars(grid, duration)
    stage(folder, "Choosing the starting eight-count", "one")
    phase_model, ns = model(models)
    analysis_rate=count_quality['analysisRate']
    phase_audio=y if analysis_rate==1 else librosa.resample(y,orig_sr=22050*analysis_rate,target_sr=22050)
    _, _, observations = f["run_model"](phase_audio, phase_beats/analysis_rate, np.array([b["beatIndex"] for b in bars]), phase_model, ns)
    eligible = np.array([b["complete"] and b["end"] <= beats[-1]+grid["period"] for b in bars])
    core = eligible.copy(); core[:10] = False; core[-10:] = False
    if core.sum() >= 8:
        eligible = core
    if not eligible.any():
        eligible[:] = True
    parity, scores = f["locked_model_phase"](observations, eligible)
    anchor = int(parity*4)
    count_quality.update(phaseMargin=round(abs(scores[0]-scores[1]),4),phaseBars=int(eligible.sum()))
    if eligible.sum()<4 or abs(scores[0]-scores[1])<.08:count_quality['needsReview']=True
    grid.update(modelAnchorIndex=anchor, modelFirstOne=float(times_at(grid,[anchor])[0]))
    grid["id"] = hashlib.sha256(json.dumps(grid, sort_keys=True).encode()).hexdigest()[:16]
    del phase_model, ns, observations
    gc.collect()

    stage(folder, "Separating instrument estimates")
    from demucs.pretrained import get_model
    from demucs.apply import apply_model
    demucs = get_model("htdemucs_6s").cpu().eval()
    tensor = torch.from_numpy(stereo.T.copy())
    ref = tensor.mean(0)
    mean, std = ref.mean(), ref.std()
    with torch.inference_mode():
        separated = apply_model(demucs, ((tensor-mean)/(std+1e-8))[None],
                                device="cpu", shifts=0, split=True, overlap=.25, progress=False)[0]
    separated = separated*std+mean
    names = list(demucs.sources)
    del demucs, tensor
    gc.collect()
    stage(folder, "Aligning counts to acoustic attacks")
    rms = float(np.sqrt(np.mean(y*y)))
    lanes = {}
    acoustic = {"mix":stereo.mean(axis=1)}
    for name, (low, high) in {"low": (45,300), "mid": (250,2500), "high": (2500,9500)}.items():
        filtered = sosfiltfilt(butter(3, [low, high], btype="bandpass", fs=22050, output="sos"), y)
        lanes[name] = f["attacks"](filtered, rms, name)
    for name in ("bass", "drums", "piano", "guitar"):
        mono = separated[names.index(name)].mean(0).numpy()
        acoustic[name]=mono.copy()
        samples = librosa.resample(mono, orig_sr=44100, target_sr=22050)
        # Fresh PCM stems share the normalized video's time zero. The old
        # recording-specific MP3 offset MUST NOT be applied to new uploads.
        lanes[name] = f["attacks"](samples, rms, name, offset=0)
        pcm = separated[names.index(name)].T.numpy()
        # Preserve the stem's dynamics while preventing PCM/AAC clipping.
        peak = float(np.max(np.abs(pcm)))
        sf.write(folder/"stem.wav", pcm*min(1, .98/max(peak, 1e-8)), 44100, subtype="PCM_16")
        solo_video(folder/"practice.mp4", folder/"stem.wav", folder/f"{name}.mp4", duration)
        (folder/"stem.wav").unlink()
    arrays = feature_cache(acoustic, folder/"acoustic-cache", lambda text:stage(folder,text))
    events, timing_quality = select_clock(acoustic, arrays, pulse_slots(grid, beats, duration), duration,
                                           lambda text:stage(folder,text), onsets=lanes)
    if not events:
        raise InvalidVideo("The counts couldn't be worked out for this clip. Try a longer clip with clearer music.")
    with source.open("rb") as stream:
        source_hash = hashlib.file_digest(stream, "sha256").hexdigest()
    labels = {k: k.title()+" estimate" for k in ("bass", "drums", "piano", "guitar")}
    labels.update(low="Low register", mid="Mid register", high="High percussion band", mix="Full mix", pulse="Estimated pulse")
    attack = {"algorithm": ALGORITHM, "quality": timing_quality, "events": events, "beats": [e["time"] for e in events],
        "lanes": lanes, "labels": labels, "accuracyMeasured": False, "stemOffsetSeconds": 0,
        "basis": "Fresh Demucs htdemucs_6s PCM estimates plus acoustic mix bands"}
    attack["id"] = hashlib.sha256(json.dumps(events, sort_keys=True).encode()).hexdigest()[:16]
    data = {"schema": 3, "sourceSha256": source_hash, "duration": duration, "audioDuration": duration,
        "fps": 30, "beats": fixed.tolist(), "detectedBeats": beats.tolist(), "grid": grid, "attack": attack,
        "bpm": grid["bpm"], "defaultAnchor": anchor, "defaultTiming": "attack",
        "phaseEvidence": scores, "countQuality": count_quality, "anchorStatus": "Automatic eight-count suggestion; review before relying on it.",
        "pipeline": "tempo-track-pulse-onset-consensus-v6", "audioAlignment": "Analyzed the exact normalized playback audio, including leading silence.",
        "soloInstruments": ["bass", "drums", "piano", "guitar"]}
    from .slow_audio import prepare_playback
    stage(folder, "Preparing slow-motion audio", "slowmo")
    data["slowPlayback"] = prepare_playback(folder/"practice.mp4", folder, data, lambda text:stage(folder,text))
    write(folder/"analysis.json", data)
    stage(folder, "Saving your practice room")


def main():
    folder = Path(sys.argv[1])
    models = Path(os.environ.get("CHOREO_MODELS", "/models"))
    try:
        if (folder/"export.json").exists():
            from .render import render
            spec = json.loads((folder/"export.json").read_text())
            data = json.loads((folder/"analysis.json").read_text())
            render(folder/"practice.mp4", folder/"export.mp4", data, speed=spec["speed"],
                   settings=spec["settings"] or None, timing=spec["settings"].get("timingMode", "attack"))
        else:
            analyze(folder, models)
    except InvalidVideo as e:
        write(folder/"error.json", {"message": str(e), "validation": True})
        sys.exit(2)
    except Exception:
        write(folder/"error.json", {"message": "Something went wrong with this video. Try again, or ask the person who runs this site.", "validation": False})
        raise


if __name__ == "__main__":
    main()
