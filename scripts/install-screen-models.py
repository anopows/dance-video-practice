"""Install immutable, hash-verified screening artifacts from their publishers."""
import hashlib,json,os,sys,tempfile,urllib.request
from pathlib import Path
root=Path(sys.argv[1] if len(sys.argv)>1 else '/var/lib/choreo/models')
manifest=json.loads((Path(__file__).parents[1]/'research/model-manifest.json').read_text())
nsfw=manifest['nsfw'];person=manifest['person']
items=[(root/'nsfw'/name,f"https://huggingface.co/{nsfw['repository']}/resolve/{nsfw['revision']}/{name}",sha) for name,sha in nsfw['files'].items()]
items.append((root/'torch/hub/checkpoints'/person['file'],person['url'],person['sha256']))
for path,url,sha in items:
    path.parent.mkdir(parents=True,exist_ok=True)
    if path.exists():
        with path.open('rb') as stream:
            if hashlib.file_digest(stream,'sha256').hexdigest()==sha:continue
    fd,temp=tempfile.mkstemp(dir=path.parent,prefix='.download-')
    try:
        with os.fdopen(fd,'wb') as target,urllib.request.urlopen(url,timeout=60) as source:
            while chunk:=source.read(1024*1024):target.write(chunk)
        with open(temp,'rb') as stream:
            if hashlib.file_digest(stream,'sha256').hexdigest()!=sha:raise RuntimeError('Screening model checksum mismatch: '+path.name)
        os.chmod(temp,0o644);os.replace(temp,path)
    finally:
        if os.path.exists(temp):os.unlink(temp)
    print('Installed and verified',path.name)
