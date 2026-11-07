"""Apply the reviewed Cloudflare config from the VPS; never print credentials.

Usage: sudo .venv/bin/python scripts/provision-cloudflare.py /root/token-file
The short-lived provisioning token must allow R2 Storage Write and Turnstile
Sites Write. Runtime object access uses a separate bucket-scoped S3 credential.
"""
import argparse
import json
import os
from pathlib import Path
import httpx

parser=argparse.ArgumentParser(description=__doc__)
parser.add_argument('token_file',type=Path)
parser.add_argument('--turnstile-only',action='store_true',help='Configure the widget without changing R2')
parser.add_argument('--config',type=Path,default=Path(__file__).resolve().parents[1]/'deploy/cloudflare-config.json')
args=parser.parse_args()
root=Path(__file__).resolve().parents[1]
config=json.loads(args.config.read_text())
token=args.token_file.read_text().strip()
account=config['account_id']
transport=httpx.HTTPTransport(local_address='0.0.0.0')
client=httpx.Client(base_url='https://api.cloudflare.com/client/v4',
    headers={'Authorization':'Bearer '+token},timeout=30,transport=transport)
def api(method,path,data=None,allow_missing=False):
    r=client.request(method,path,json=data)
    value=r.json()
    if allow_missing and r.status_code==404:
        return None
    if not r.is_success or not value.get('success'):
        errors=[{'code':e.get('code'),'message':e.get('message')} for e in value.get('errors',[])]
        raise SystemExit(f'Cloudflare request failed ({r.status_code}): {errors}')
    return value.get('result')

prefix=f'/accounts/{account}'
bucket=f"{prefix}/r2/buckets/{config['bucket']}"
if not args.turnstile_only:
    existing=api('GET',bucket,allow_missing=True)
    if existing is None:
        api('POST',prefix+'/r2/buckets',{'name':config['bucket'],'locationHint':config['location_hint']})
    api('PUT',bucket+'/domains/managed',{'enabled':False})
    api('PUT',bucket+'/cors',config['cors'])
    api('PUT',bucket+'/lifecycle',config['lifecycle'])
widgets=api('GET',prefix+'/challenges/widgets')
if isinstance(widgets,dict):
    widgets=widgets.get('result',widgets.get('widgets',[]))
matched=[w for w in widgets if w.get('name')==config['turnstile']['name']]
if len(matched)>1:
    raise SystemExit('Multiple matching widgets; inspect before proceeding')
if matched:
    sitekey=matched[0]['sitekey']
    widget=api('PUT',prefix+'/challenges/widgets/'+sitekey,config['turnstile'])
else:
    widget=api('POST',prefix+'/challenges/widgets',config['turnstile'])
if not widget.get('secret'):
    widget=api('GET',prefix+'/challenges/widgets/'+widget['sitekey'])
if not widget.get('secret'):
    raise SystemExit('Widget secret unavailable; no secret file written')
secret_path=Path('/etc/choreo-turnstile.env')
fd=os.open(secret_path,os.O_WRONLY|os.O_CREAT|os.O_TRUNC,0o600)
with os.fdopen(fd,'w') as f:
    f.write('CHOREO_TURNSTILE_SITE_KEY='+widget['sitekey']+'\n')
    f.write('CHOREO_TURNSTILE_SECRET='+widget['secret']+'\n')
os.chmod(secret_path,0o600)
if args.turnstile_only:
    print('Managed Turnstile widget configured. Secrets written to root-only file.')
else:
    state={'account_id':account,'bucket':config['bucket'],'turnstile_site_key':widget['sitekey'],
           'hostname':config['hostname'],'public_access':False}
    (root/'deploy/cloudflare-state.json').write_text(json.dumps(state,indent=2)+'\n')
    print('Private bucket, CORS, lifecycle, and managed Turnstile widget configured. Secrets written to root-only file.')
