"""Create a clean source distribution in an empty directory, without git history.

Only reviewed source paths are copied. No deployment state, secrets, media,
model weights, caches, or old experiment entry points are included.
"""
import argparse
import ast
import hashlib
import json
from pathlib import Path
import shutil

ROOT = Path(__file__).resolve().parents[1]
parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("destination", type=Path)
args = parser.parse_args()
target = args.destination.resolve()
if target == ROOT or ROOT in target.parents:
    parser.error("Choose a destination outside the source checkout")
if target.exists() and any(target.iterdir()):
    parser.error("Destination must be empty; existing files are never overwritten")
target.mkdir(parents=True, exist_ok=True)

paths = ["README.md", "SELF_HOSTING.md", "LICENSE", "THIRD_PARTY_NOTICES.md",
         ".gitignore", "pyproject.toml", "uv.lock", ".github/workflows/check.yml",
         "deploy/.env.example", "deploy/Caddyfile.example",
         "deploy/cloudflare-config.example.json", "deploy/RUNBOOK.md",
         "deploy/choreo-api.service", "deploy/choreo-worker.service",
         "deploy/choreo-bwrap.apparmor", "research/model-manifest.json"]
for directory, suffixes in {"choreo": {".py", ".js", ".css", ".html", ".json"},
                           "tests": {".py", ".cjs"}, "scripts": {".py", ".sh"}}.items():
    paths += [str(p.relative_to(ROOT)) for p in (ROOT/directory).rglob("*")
              if p.is_file() and p.suffix in suffixes and "__pycache__" not in p.parts]
for name in sorted(paths):
    src, dest = ROOT/name, target/name
    if src.is_symlink():
        raise SystemExit(f"Refusing symbolic link: {name}")
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, dest)

# Keep exactly the original function text used by the runtime adapter. Its
# namespace supplies imports/constants; personal experiment entry points do not
# belong in a reusable source release.
selected = {
    "analyze.py": {"run_model", "make_bars"},
    "lock_counts.py": {"fit_grid", "fixed_bars", "locked_model_phase"},
    "attack_timing.py": {"attacks", "pulse_slots", "match_attacks"},
}
for name, names in selected.items():
    source = (ROOT/"research"/name).read_text()
    nodes = [n for n in ast.parse(source).body
             if isinstance(n, ast.FunctionDef) and n.name in names]
    if {n.name for n in nodes} != names:
        raise SystemExit(f"Missing analysis definitions: {name}")
    text = '"""Analysis definitions loaded by choreo.research_adapter, not a standalone script."""\n\n'
    text += "\n\n\n".join(ast.get_source_segment(source, n) for n in nodes)+"\n"
    (target/"research"/name).write_text(text)

manifest = {str(p.relative_to(target)): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(target.rglob("*")) if p.is_file()}
(target/"SOURCE_MANIFEST.json").write_text(json.dumps(manifest, indent=2)+"\n")
print(f"Prepared {len(manifest)} source files in {target}")
