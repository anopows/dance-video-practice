"""Verify model files before starting a worker. No user media is accessed."""
import hashlib
import json
import sys
from pathlib import Path

root=Path(sys.argv[1] if len(sys.argv)>1 else '/var/lib/choreo/models')
manifest=json.loads((Path(__file__).parents[1]/'research/model-manifest.json').read_text())
def verify(path,expected):
    with path.open('rb') as f:
        actual=hashlib.file_digest(f,'sha256').hexdigest()
    if actual!=expected:
        raise SystemExit(f'Model verification failed: {path.name}')
verify(root/manifest['beat_this']['file'],manifest['beat_this']['sha256'])
verify(root/manifest['hypermeter']['weights'],manifest['hypermeter']['sha256'])
for name,sha in manifest['hypermeter']['sourceFiles'].items():
    verify(root/'hypermeter-source'/name,sha)
demucs=root/'torch/hub/checkpoints/5c90dfd2-34c22ccb.th'
with demucs.open('rb') as f:
    sha=hashlib.file_digest(f,'sha256').hexdigest()
if sha != '34c22ccb381c6f9fdbf324f04e1e2fe21aaaf293f5ded163a162697ff9a02ddd':
    raise SystemExit('Demucs checkpoint verification failed')
print('Verified Beat This!, hypermeter code and weights, and Demucs checkpoint.')

for name,sha in manifest['nsfw']['files'].items():verify(root/'nsfw'/name,sha)
verify(root/'torch/hub/checkpoints'/manifest['person']['file'],manifest['person']['sha256'])
print('Verified pinned NSFW classifier and person detector.')
