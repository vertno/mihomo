"""Publish only the files validated for this exact patched source."""
import json
import os
import re
from pathlib import Path
from sync import api, cli, digest, sha, source_lock, MARK

repo=os.environ["GITHUB_REPOSITORY"]
tag=os.environ["RELEASE_TAG"]
commit=os.environ["SOURCE_SHA"]
root=Path("assets")
lock=source_lock(repo,commit)
if lock["version"]!=tag: raise ValueError("Version mismatch")
files={f.name:f for f in root.iterdir() if f.is_file()}
kind=json.loads(Path("control/release/project.json").read_text())["project"]
if kind=="mihomo":
    expected={f"mihomo-windows-{a}-{tag}.zip" for a in ("amd64-v2","arm64")}|{"version.txt"}
    if set(files)!=expected or files["version.txt"].read_text().strip()!=tag: raise ValueError("Incomplete core release")
elif kind=="verge":
    if len(files)!=4 or any(len([n for n in files if n.endswith(arch+"-setup.exe") and n+".sig" in files])!=1 for arch in ("x64","arm64")): raise ValueError("Missing installer or signature")
else:
    meta=json.loads(files["output-metadata.json"].read_text())
    declared={e["outputFile"] for e in meta["elements"]}
    if not declared or set(files)!=declared|{"output-metadata.json"}: raise ValueError("APK assets differ from Gradle metadata")
if not all(p.stat().st_size>0 for p in files.values()): raise ValueError("Empty artifact")
ref=api(f"repos/{repo}/git/ref/tags/{tag}",optional=True)
if ref is None:
    api(f"repos/{repo}/git/refs","-X","POST","-f","ref=refs/tags/"+tag,"-f","sha="+commit)
elif sha(repo,tag)!=commit: raise ValueError("Tag points to different source")
identity={**lock,"source_commit":commit}
notes=f"Based on [{lock['upstream']} {lock['upstream_tag']}](https://github.com/{lock['upstream']}/releases/tag/{lock['upstream_tag']}).\n\n"
if "core" in lock: notes+=f"Core: [vertno/mihomo {lock['core']['version']}](https://github.com/vertno/mihomo/releases/tag/{lock['core']['version']}).\n\n"
notes+=MARK+json.dumps(identity,separators=(",",":"))+" -->\n"
p=Path("release-notes.md");p.write_text(notes)
release=api(f"repos/{repo}/releases/tags/{tag}",optional=True)
if not release:
    cli("gh","release","create",tag,"-R",repo,"--draft","--verify-tag","--title",tag,"--notes-file",str(p))
else:
    if not release["draft"]:
        if release["body"].strip()!=notes.strip(): raise ValueError("Published release identity changed")
        print("Already published"); exit(0)
    cli("gh","release","edit",tag,"-R",repo,"--notes-file",str(p))
cli("gh","release","upload",tag,"-R",repo,"--clobber",*map(str,files.values()))
d=api(f"repos/{repo}/releases/tags/{tag}")
if not d["draft"]: raise ValueError("Expected a draft")
actual={a["name"]:a["digest"] for a in d["assets"]}
if actual!={n:"sha256:"+digest(f) for n,f in files.items()}: raise ValueError("Uploaded digests differ from verified files")
cli("gh","release","edit",tag,"-R",repo,"--draft=false","--latest")
live=api(f"repos/{repo}/releases/tags/{tag}")
if live["draft"] or live["prerelease"] or not live["published_at"] or sha(repo,tag)!=commit: raise ValueError("Publication failed verification")
print(live["html_url"])
