"""Local, fail-closed screening. Imports model runtimes only inside the sandbox."""
import gc
import json
import math
import shutil
import time
from pathlib import Path
from .media import InvalidVideo, run

NSFW_FRAME_SCORE = 0.90
NSFW_FRAME_SHARE = 0.05
PERSON_SCORE = 0.40
PERSON_FRAME_SHARE = 0.10
PERSON_CHECK = True

class Rejected(InvalidVideo):
    def __init__(self,code):
        self.code=code
        super().__init__("This video can't be hosted on five·six. See the content rules." if code=='nsfw' else 'five·six is for dance videos. No dancer was found in this clip.')


def decision(scores,persons=None):
    if not scores:raise InvalidVideo("This video couldn't be read all the way through. Export it as an MP4 and try again.")
    if any(not math.isfinite(float(s)) or not 0<=s<=1 for s in scores):raise RuntimeError('Invalid screening scores')
    n=len(scores);flagged=sum(s>=NSFW_FRAME_SCORE for s in scores)
    result='nsfw' if flagged>=min(n,max(3,math.ceil(NSFW_FRAME_SHARE*n))) else 'pass'
    person_count=None
    if result=='pass' and PERSON_CHECK:
        if persons is None or len(persons)!=n:raise RuntimeError('Incomplete person screening')
        person_count=sum(bool(p) for p in persons)
        if person_count<max(1,math.ceil(PERSON_FRAME_SHARE*n)):result='no_person'
    return {'version':1,'frames':n,'nsfwFlagged':flagged,'nsfwMax':round(max(scores),6),'personFrames':person_count,'result':result}


def nsfw_scores(paths,models):
    import torch
    from PIL import Image
    from transformers import AutoImageProcessor,AutoModelForImageClassification
    processor=AutoImageProcessor.from_pretrained(str(models/'nsfw'),local_files_only=True,use_fast=False)
    model=AutoModelForImageClassification.from_pretrained(str(models/'nsfw'),local_files_only=True,use_safetensors=True).cpu().eval()
    label=next(int(i) for i,name in model.config.id2label.items() if name.lower()=='nsfw')
    scores=[]
    with torch.inference_mode():
        for at in range(0,len(paths),16):
            images=[]
            for path in paths[at:at+16]:
                with Image.open(path) as source:images.append(source.convert('RGB'))
            inputs=processor(images=images,return_tensors='pt')
            scores.extend(model(**inputs).logits.softmax(-1)[:,label].tolist())
            for image in images:image.close()
    del model,processor;gc.collect()
    return scores


def person_frames(paths,models):
    import torch
    from PIL import Image
    from torchvision.models.detection import ssdlite320_mobilenet_v3_large,SSDLite320_MobileNet_V3_Large_Weights
    weights=SSDLite320_MobileNet_V3_Large_Weights.COCO_V1
    filename=weights.url.rsplit('/',1)[-1]
    model=ssdlite320_mobilenet_v3_large(weights=None,weights_backbone=None).cpu().eval()
    model.load_state_dict(torch.load(models/'torch/hub/checkpoints'/filename,map_location='cpu',weights_only=True))
    transform=weights.transforms();found=[]
    with torch.inference_mode():
        for at in range(0,len(paths),16):
            tensors=[]
            for path in paths[at:at+16]:
                with Image.open(path) as image:tensors.append(transform(image.convert('RGB')))
            for prediction in model(tensors):found.append(bool(((prediction['labels']==1)&(prediction['scores']>=PERSON_SCORE)).any()))
    del model;gc.collect()
    return found


def screen(folder,models):
    folder,models=Path(folder),Path(models);frames=folder/'screen';summary={'version':1,'frames':0,'nsfwFlagged':0,'nsfwMax':0,'personFrames':None,'result':'error'}
    try:
        frames.mkdir(exist_ok=False)
        duration=float(json.loads(run(['ffprobe','-v','error','-protocol_whitelist','file,pipe','-show_entries','format=duration','-of','json',str(folder/'practice.mp4')],timeout=30))['format']['duration'])
        if not math.isfinite(duration) or not 1<=duration<=600:raise InvalidVideo('Videos must be between 1 second and 10 minutes long.')
        rate='1' if duration<20 else '1/2'
        run(['ffmpeg','-y','-v','error','-protocol_whitelist','file,pipe','-i',str(folder/'practice.mp4'),'-vf',f'fps={rate},scale=320:320:force_original_aspect_ratio=decrease','-frames:v','300','-q:v','4',str(frames/'%04d.jpg')],timeout=180)
        paths=sorted(frames.glob('*.jpg'));summary['frames']=len(paths)
        if not paths:raise InvalidVideo("This video couldn't be read all the way through. Export it as an MP4 and try again.")
        scores=nsfw_scores(paths,models)
        # Assess rejection before spending CPU on person detection.
        flagged=sum(s>=NSFW_FRAME_SCORE for s in scores)
        reject=flagged>=min(len(scores),max(3,math.ceil(NSFW_FRAME_SHARE*len(scores))))
        persons=person_frames(paths,models) if PERSON_CHECK and not reject else None
        summary=decision(scores,persons)
        if summary['result']!='pass':raise Rejected(summary['result'])
        return summary
    finally:
        (folder/'screen.json').write_text(json.dumps(summary,allow_nan=False)+'\n')
        shutil.rmtree(frames,ignore_errors=True)


def main():
    import argparse,os,tempfile
    parser=argparse.ArgumentParser(description='Screen local videos; outputs JSON and fails on any rejection or error.')
    parser.add_argument('files',nargs='+',type=Path);parser.add_argument('--models',type=Path,default=Path(os.environ.get('CHOREO_MODELS','/var/lib/choreo/models')))
    args=parser.parse_args();failed=False
    import torch
    torch.set_num_threads(3)
    for source in args.files:
        with tempfile.TemporaryDirectory(prefix='fivesix-screen-') as temp:
            folder=Path(temp);(folder/'practice.mp4').symlink_to(source.resolve());started=time.monotonic()
            try:screen(folder,args.models)
            except Exception:failed=True
            summary=json.loads((folder/'screen.json').read_text())
            print(json.dumps({'file':str(source),'elapsedSeconds':round(time.monotonic()-started,3),**summary}))
    raise SystemExit(1 if failed else 0)

if __name__=='__main__':main()
