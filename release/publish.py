"""Publish only the files validated for this exact patched source."""
import json
import os
import sys
from pathlib import Path
from sync import api, cli, digest, sha, source_lock, MARK, release_including_draft

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
# The tag endpoint only lists published releases. Drafts are found by their numeric id.
rel=release_including_draft(repo,tag)
if rel is None:
    rel=api(f"repos/{repo}/releases","-X","POST","-f","tag_name="+tag,"-f","target_commitish="+commit,
            "-f","name="+tag,"-f","body="+notes,"-F","draft=true","-F","prerelease=false")
address=f"repos/{repo}/releases/{rel['id']}"
expected={n:"sha256:"+digest(f) for n,f in files.items()}
actual={a["name"]:a["digest"] for a in rel["assets"]}
if set(actual)-set(expected): raise ValueError("Unexpected files in release")
if not rel["draft"]:
    if MARK+json.dumps(identity,separators=(",",":"))+" -->" not in rel["body"] or actual!=expected:
        raise ValueError("Already published with different inputs or files")
    print("Already verified:",rel["html_url"]); sys.exit(0)
changed=[str(files[n]) for n,checksum in expected.items() if actual.get(n)!=checksum]
if changed: cli("gh","release","upload",tag,"-R",repo,"--clobber",*changed)
d=api(address)
if not d["draft"] or d["tag_name"]!=tag: raise ValueError("Expected original draft")
if {a["name"]:a["digest"] for a in d["assets"]}!=expected: raise ValueError("Uploaded digests differ from verified files")
live=api(address,"-X","PATCH","-f","body="+notes,"-F","draft=false","-f","make_latest=true")
if live["draft"] or live["prerelease"] or not live["published_at"] or sha(repo,tag)!=commit: raise ValueError("Publication failed verification")
print(live["html_url"])
