# Self-host Choreo Practice

This single-room service uses Ubuntu for processing and a private Cloudflare R2
bucket for temporary media. Link visitors do not sign in. The room password
grants upload and management of all room videos.

## Requirements

- Ubuntu 24.04 x86-64, Python 3.12, systemd, and AppArmor.
- At least 8 GB RAM, 3 CPU cores, and SSD storage for models and temporary media.
  The worker requires 5 GB free before starting a job; allow substantially more
  for installation and long recordings.
- A domain pointing directly to the host, inbound TCP 80/443, and outbound
  access to Cloudflare and official package/model downloads.
- A Cloudflare account with R2 and Turnstile.
- The authors' hypermeter audio checkpoint; see step 2 and
  [third-party terms](THIRD_PARTY_NOTICES.md).

The scripts target this Linux setup. They are not a Docker image or macOS server
installer. CPU analysis is offline and can take several minutes.

## 1. Install the host

```sh
sudo git clone https://github.com/anopows/choreo-practice-selfhost.git /opt/choreo
cd /opt/choreo
sudo bash scripts/install-host.sh
```

This installs the locked runtime, FFmpeg, Rubber Band, Caddy, bubblewrap, service
account, and systemd units. It does not start the app. Keep source and models
root-owned. The service user writes only under `/var/lib/choreo`.

The AppArmor profile permits only the dedicated bubblewrap executable to create
its sandbox. Do not disable Ubuntu's global namespace restriction. The API
listens on `127.0.0.1:18793`; keep one API process and one media worker.

## 2. Install model assets

Download `tcn_audio_metrical_v1.0_crf.cp.sdict` from the
[authors' model folder](https://drive.google.com/drive/folders/1Er9CJkGLCOTd5ztCblOoGpRx3c3GNcg7),
linked by the [upstream project](https://github.com/music-x-lab/Self-Supervised-Metrical-Structure).
Transfer it to the host, then run:

```sh
sudo bash scripts/install-models.sh /path/to/tcn_audio_metrical_v1.0_crf.cp.sdict
sudo .venv/bin/python scripts/check-models.py
```

This downloads Beat This! final0, pinned hypermeter inference source, and Demucs
htdemucs_6s. It verifies checkpoint and source hashes. The worker verifies them
again before starting. Models are not included in git. Do not substitute a
similarly named checkpoint without reviewing the manifest.

## 3. Configure Cloudflare

Copy the non-secret example and edit the account ID, hostname, origin, bucket,
Turnstile domains, and CORS origins to match your installation:

```sh
sudo cp deploy/cloudflare-config.example.json deploy/cloudflare-config.json
sudoedit deploy/cloudflare-config.json
```

Use a dedicated bucket with public access disabled. Playback uses signed URLs.
The example sets object deletion to 90 days and aborts incomplete multipart
uploads after one day. CORS allows GET/HEAD from your exact HTTPS origin.

Create a temporary Cloudflare token with **Workers R2 Storage Write** and
**Turnstile Sites Write** for your account. Save the token by itself in a
root-only file such as `/root/choreo-provision-token` (mode 0600):

```sh
sudo .venv/bin/python scripts/provision-cloudflare.py /root/choreo-provision-token
```

This configures the private bucket, CORS, lifecycle, and managed Turnstile widget.
Widget credentials go into root-only `/etc/choreo-turnstile.env`.
Use `--config /path/to/config.json` to select another configuration file.

Create a **separate R2 Object Read & Write credential scoped to this bucket**.
Save it in root-only `/root/r2-credentials.json`:

```json
{"access_key_id":"YOUR_R2_ACCESS_KEY","secret_access_key":"YOUR_R2_SECRET_KEY"}
```

## 4. Configure the application

```sh
sudo .venv/bin/python scripts/configure-runtime.py /root/r2-credentials.json
```

Enter your room password at the hidden prompt. The script hashes it with
Argon2id and writes validated settings to root-only `/etc/choreo.env`.
Retention is `CHOREO_RETENTION_SECONDS=7776000` (90 days).
`deploy/.env.example` documents required variables.

After success, revoke the temporary provisioning token. Remove its file, the
temporary R2 JSON, and `/etc/choreo-turnstile.env`. Keep `/etc/choreo.env`;
both services need it. Never shell-source it or include it in source archives.

## 5. Enable HTTPS and start

Create the domain's A record (and AAAA only if IPv6 reaches the host).
The example assumes direct DNS to Caddy, without a CDN proxy.

```sh
sudo cp deploy/Caddyfile.example /etc/caddy/Caddyfile
sudoedit /etc/caddy/Caddyfile
sudo caddy validate --config /etc/caddy/Caddyfile
sudo systemctl enable --now choreo-api choreo-worker caddy
sudo systemctl reload caddy
curl --fail https://dance.example.com/healthz
```

Replace the example hostname with your domain. If the host already serves sites
or uses port 443 for a VPN, merge the configuration and choose Caddy's listen
address instead of overwriting the existing setup.

## 6. Verify the complete flow

1. Open the site. Uploading should require the room password and Turnstile.
2. Upload a short dance clip and wait for analysis.
3. Test 100%, 50%, and 25% playback, seeking, loops, and count correction.
4. Copy **Share** and open it in a private browser window. Playback and 100%/25%
   count exports should work without sign-in.
5. Adjust counts there. It should say **Saved on this device**, leaving the
   shared alignment unchanged.
6. Check the expiry in Tools and the 90-day R2 lifecycle rule.
7. Verify that unsigned access to the R2 bucket remains disabled.

See [Operations](deploy/RUNBOOK.md) for updates, retention migration, backups,
and troubleshooting. Automated tests do not validate your DNS, credentials,
model downloads, or browser upload permissions.
