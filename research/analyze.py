"""Analysis definitions loaded by choreo.research_adapter, not a standalone script."""

def run_model(y, beats, downbeat_indices, model, ns):
    # Exact BeatSyncEnergySpec preprocessing: 32 spectral samples per beat.
    subbeat = np.interp(np.arange(len(beats) * 32) / 32, np.arange(len(beats)), beats)
    _, spec = ns['time_stretch'](y, subbeat / (len(y) / SR), 256, True)
    spec = spec.reshape((len(spec) // 8, -1))
    spec = np.pad(spec, ((64, 64), (0, 0)))
    with torch.inference_mode():
        pred = model.inference(torch.tensor(spec, dtype=torch.float32))
    # Exact eval_entry alignment: 64 padding units minus 20 calibration units.
    final = np.zeros((len(pred), 5))
    final[:, 1:] = pred[:, -4:]
    final[:, 0] = logsumexp(pred[:, :-4], axis=1)
    observations = maximum_filter1d(final, size=5, axis=0)[downbeat_indices * 4 + 44]
    transition, levels = ns['get_ternary_transition'](
        np.array([-5., -4., -3., -2.]), np.array([-8., -7., -6., -5.]))
    decoder = ns['CRFDecoder'](torch.tensor(transition))
    with torch.inference_mode():
        states = decoder.viterbi_decode(torch.tensor(observations[:, levels])[None])[0].numpy()
    result = levels[states]
    # Local network evidence; not the CRF posterior and not calibrated accuracy.
    p_two = np.exp(logsumexp(observations[:, 1:], axis=1) - logsumexp(observations, axis=1))
    return result, p_two, observations


def make_bars(beats, downbeat_indices, duration):
    bars = []
    for j, idx in enumerate(downbeat_indices):
        next_idx = int(downbeat_indices[j+1]) if j+1 < len(downbeat_indices) else len(beats)
        end = float(beats[next_idx]) if next_idx < len(beats) else duration
        complete = next_idx < len(beats)
        valid = bool(complete and next_idx - idx == 4)
        bars.append({'index': j, 'beatIndex': int(idx), 'start': float(beats[idx]), 'end': end,
                     'beatCount': int(next_idx-idx), 'valid': valid,
                     'edge': j < 10 or j >= len(downbeat_indices)-10,
                     'reason': None if valid else ('Final bar not bounded by a detected downbeat' if not complete else 'Detected bar does not contain four beats')})
    return bars
