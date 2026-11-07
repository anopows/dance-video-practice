#!/usr/bin/env bash
# Run as root after install-host.sh. Pass the authors' audio checkpoint path.
# The checkpoint is intentionally not redistributed in this source repository.
set -euo pipefail
cd /opt/choreo
test $# -eq 1 || { echo 'Usage: install-models.sh /path/to/tcn_audio_metrical_v1.0_crf.cp.sdict'; exit 2; }
install -d -m 0755 /var/lib/choreo/models
install -m 0644 "$1" /var/lib/choreo/models/hypermeter.sdict
if [ ! -f /var/lib/choreo/models/beat_this-final0.ckpt ]; then
  curl --fail --location --output /var/lib/choreo/models/beat_this-final0.ckpt https://cloud.cp.jku.at/public.php/dav/files/7ik4RrBKTS273gp/final0.ckpt
fi
if [ ! -d /var/lib/choreo/models/hypermeter-source/.git ]; then
  git clone https://github.com/music-x-lab/Self-Supervised-Metrical-Structure.git /var/lib/choreo/models/hypermeter-source
fi
git -C /var/lib/choreo/models/hypermeter-source checkout bdabe679a9a5a5900dba56666b535af734e646b7
TORCH_HOME=/var/lib/choreo/models/torch .venv/bin/python -c 'from demucs.pretrained import get_model; get_model("htdemucs_6s")'
.venv/bin/python scripts/check-models.py
chmod -R a+rX /var/lib/choreo/models
