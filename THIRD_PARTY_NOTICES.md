# Third-party software and model assets

MIT covers this repository's application source. It does not grant rights to
separately downloaded software, model weights, user uploads, or music.
Dependencies retain their upstream licenses.

## Analysis models

- [Beat This!](https://github.com/CPJKU/beat_this) supplies beat/downbeat inference
  and the authors' final0 checkpoint.
- [Self-Supervised Metrical Structure](https://github.com/music-x-lab/Self-Supervised-Metrical-Structure)
  by Jiang and Xia supplies the audio hypermeter model. Its pinned snapshot has
  no explicit top-level license. Neither that source nor checkpoint is
  redistributed here. The installer fetches the pinned inference source;
  obtain the checkpoint from the authors and review permission for your use,
  especially redistribution or commercial deployment.
- [Demucs](https://github.com/facebookresearch/demucs) supplies htdemucs_6s
  instrument separation. The model retains upstream terms.

Exact files, source revisions, and verification hashes are in
[research/model-manifest.json](research/model-manifest.json) and
[scripts/check-models.py](scripts/check-models.py). A checksum verifies identity;
it does not grant a license.

## Audio and media dependencies

- [Essentia licensing](https://essentia.upf.edu/licensing_information.html):
  AGPLv3 with separate licensing options. The app uses onset algorithms,
  not Essentia pretrained tagging models.
- [Rubber Band licensing](https://breakfastquay.com/rubberband/license.html):
  the separately installed CLI/library uses GPLv2-or-later or a commercial license.
- [FFmpeg legal information](https://ffmpeg.org/legal.html):
  terms depend on the features in the installed build.

This release contains no dependency binaries or model weights. If packaging a
complete runtime, preserve upstream notices and meet applicable source and
distribution requirements. Versions are in `pyproject.toml` and `uv.lock`;
consult each upstream project for its full license.

## Public-launch screening models

Falconsai/nsfw_image_detection: Apache-2.0, revision 96cb0d0342c7afb80cab76ecc58b265fa44da256. Model card: https://huggingface.co/Falconsai/nsfw_image_detection/tree/96cb0d0342c7afb80cab76ecc58b265fa44da256. Files and SHA-256 hashes are recorded in research/model-manifest.json. Transformers is Apache-2.0.

Torchvision SSDlite320 MobileNet V3 Large COCO_V1: torchvision code is BSD-3-Clause; pretrained detector supplied by PyTorch, trained on COCO. The COCO dataset has its own image licensing terms. Source: https://pytorch.org/vision/0.20/models/generated/torchvision.models.detection.ssdlite320_mobilenet_v3_large.html. No training dataset or model weights are redistributed in this repository.
