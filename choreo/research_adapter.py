"""Load reviewed functions from the recorded research snapshot.

The snapshot remains unchanged for comparison; batch-specific main functions,
personal paths, old predictions and cached stems are never executed or copied.
"""
import ast
import hashlib
from pathlib import Path
import librosa
import numpy as np
import torch
from scipy.ndimage import maximum_filter1d, uniform_filter1d
from scipy.optimize import least_squares
from scipy.signal import butter, sosfiltfilt
from scipy.special import logsumexp
import math

RESEARCH = Path(__file__).resolve().parents[1] / "research"


def definitions(path, names, namespace):
    tree = ast.parse(path.read_text(), filename=str(path))
    nodes = [n for n in tree.body if isinstance(n, (ast.ClassDef, ast.FunctionDef)) and n.name in names]
    if {n.name for n in nodes} != set(names):
        raise ValueError(f"Missing reviewed definitions in {path.name}")
    exec(compile(ast.Module(body=nodes, type_ignores=[]), str(path), "exec"), namespace)


def functions():
    ns = {"np": np, "torch": torch, "librosa": librosa, "maximum_filter1d": maximum_filter1d,
          "logsumexp": logsumexp, "least_squares": least_squares, "math": math,
          "uniform_filter1d": uniform_filter1d, "SR": 22050, "HOP": 64}
    definitions(RESEARCH/"analyze.py", {"run_model", "make_bars"}, ns)
    definitions(RESEARCH/"lock_counts.py", {"fit_grid", "fixed_bars", "locked_model_phase"}, ns)
    definitions(RESEARCH/"attack_timing.py", {"attacks", "pulse_slots", "match_attacks"}, ns)
    return ns


def model(root):
    weight = root / "hypermeter.sdict"
    with weight.open("rb") as f:
        if hashlib.file_digest(f, "sha256").hexdigest() != "abcb96c9c1aaf5191c660b98140262e6a9a43430c8209d4c19ab97b044bf8964":
            raise ValueError("Hypermeter weights failed verification")
    ns = {"np": np, "torch": torch, "nn": torch.nn, "F": torch.nn.functional,
          "NetworkBehavior": torch.nn.Module, "librosa": librosa}
    for file, names in {
        "metrical_crf.py": {"get_binary_transition", "get_ternary_transition"},
        "crf.py": {"CRFDecoder"}, "simple_tcn.py": {"TCNBlock", "TCN"},
        "tcn_audio_metrical_structure.py": {"TCNClassifier"},
        "extractors/audio_helpers.py": {"phase_vocoder", "time_stretch"},
    }.items():
        definitions(root/"hypermeter-source"/file, names, ns)
    result = ns["TCNClassifier"](513, 256, 6, 9, .5, remap_offset=20)
    result.load_state_dict(torch.load(weight, map_location="cpu", weights_only=True)["net"], strict=True)
    result.eval()
    return result, ns

