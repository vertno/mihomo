"""Resolve a stable upstream release, verify its released dependencies and pin its source."""
import base64
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path
from urllib.parse import quote

CONTROL = Path("control")
SOURCE = Path("source")
CORE = "vertno/mihomo"
MARK = "<!-- distribution "
MOD = "core/src/foss/golang/clash"

def cli(*args, cwd=None, check=True):
    result = subprocess.run(args, cwd=cwd, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if check and result.returncode:
        raise RuntimeError(f"{args[0]} {args[1]} failed: {result.stderr.strip()}")
    return result

def api(path, *args, optional=False):
    p = cli("gh","api",path,*args,check=False)
    if p.returncode:
        if optional and "HTTP 404" in p.stderr: return None
        raise RuntimeError(p.stderr.strip())
    return json.loads(p.stdout)

def version(tag):
    if re.fullmatch(r"v\d+\.\d+\.\d+",tag):
        return tuple(int(n) for n in tag[1:].split("."))
    return None

def releases(repo, own=False, floor=None):
    rows=[]
    for page in range(1,1001):
        batch=api(f"repos/{repo}/releases?per_page=25&page={page}","--jq","[.[]|{tag_name,draft,prerelease,published_at,body}]" if own else "[.[]|{tag_name,draft,prerelease,published_at}]")
        for r in batch:
            if r["draft"] or r["prerelease"] or not r["published_at"]: continue
            if own:
                match=re.search(r"<!-- distribution (\{[^\n]*\}) -->",r.get("body") or "")
                if match:
                    info=json.loads(match[1])
                    if info["version"]!=r["tag_name"]: raise ValueError("Release tag and identity disagree")
                    rows.append(info)
            elif (v:=version(r["tag_name"])) and v>=floor: rows.append(r["tag_name"])
        if len(batch)<25 or (not own and any(version(r["tag_name"]) and version(r["tag_name"])<floor and not r["prerelease"] for r in batch)): break
    return rows if own else sorted(set(rows),key=version)

def sha(repo,ref):
    return api(f"repos/{repo}/commits/{quote(ref,safe='')}","--jq",".sha|@json")

def source_lock(repo,ref):
    result=api(f"repos/{repo}/contents/distribution.lock.json?ref={quote(ref,safe='')}")
    return json.loads(base64.b64decode(result["content"]))

def gitlink(repo,ref):
    return api(f"repos/{repo}/contents/{MOD}?ref={ref}")["sha"]

def output(name,value):
    with open(os.environ["GITHUB_OUTPUT"],"a") as f: print(f"{name}={value}",file=f)

def digest(path):
    with path.open("rb") as f: return hashlib.file_digest(f,"sha256").hexdigest()

def read_core(upstream_sha, records, windows):
    candidates=[r for r in records if r["upstream_commit"]==upstream_sha]
    if not candidates: return None
    c=max(candidates,key=lambda r:r["revision"])
    tag=c["version"]
    rel=api(f"repos/{CORE}/releases/tags/{tag}")
    commit=sha(CORE,tag)
    lock=source_lock(CORE,commit)
    if rel["draft"] or rel["prerelease"] or not rel["published_at"] or commit!=c["source_commit"] or any(lock[k]!=c[k] for k in ("version","upstream_commit","revision")):
        raise ValueError("Core release identity mismatch: "+tag)
    result={"version":tag,"commit":commit,"upstream_commit":upstream_sha}
    if windows:
        assets={a["name"]:a for a in rel["assets"]}
        result["windows"]={}
        with tempfile.TemporaryDirectory() as folder:
            names=["version.txt"]+[f"mihomo-windows-{arch}-{tag}.zip" for arch in ("amd64-v2","arm64")]
            args=["gh","release","download",tag,"-R",CORE,"-D",folder]
            for name in names: args+=["-p",name]
            cli(*args)
            for name in names:
                p=Path(folder)/name
                if "sha256:"+digest(p)!=assets[name]["digest"]: raise ValueError("Bad release digest: "+name)
                if name=="version.txt":
                    if p.read_text().strip()!=tag: raise ValueError("Wrong core version.txt")
                    continue
                arch=name.removeprefix("mihomo-windows-").removesuffix("-"+tag+".zip")
                with zipfile.ZipFile(p) as z:
                    member=f"mihomo-windows-{arch}.exe"
                    if z.namelist()!=[member]: raise ValueError("Unexpected ZIP entries: "+name)
                    with z.open(member) as exe: exe_hash=hashlib.file_digest(exe,"sha256").hexdigest()
                result["windows"][arch]={"zip":name,"sha256":assets[name]["digest"].split(":")[1],"exe_sha256":exe_hash}
    return result

def next_app_version(own):
    major,minor,patch=max((version(r["version"]) for r in own),default=(1,0,0))
    if patch>=999: minor,patch=minor+1,0
    return f"v{major}.{minor}.{patch+1}"

def resolve(cfg):
    kind,repo,up=cfg["project"],cfg["repository"],cfg["upstream"]
    if repo!=os.environ["GITHUB_REPOSITORY"]: raise ValueError("Unexpected destination repository")
    own=releases(repo,own=True)
    tags=releases(up,floor=version(cfg["first_tag"]))
    if not tags: raise ValueError("No official stable releases at the configured baseline")
    core_index=[] if kind=="mihomo" else releases(CORE,own=True)
    newest_seen=max((version(r["upstream_tag"]) for r in own),default=(0,0,0))
    wait=False
    for tag in tags:
        if version(tag)<newest_seen: continue
        prior=[r for r in own if r["upstream_tag"]==tag]
        if prior and tag!=tags[-1]: continue
        usha=sha(up,tag)
        if any(r["upstream_commit"]!=usha for r in prior): raise ValueError(f"Upstream moved {tag}")
        rec={"upstream":up,"upstream_tag":tag,"upstream_commit":usha,"revision":cfg["revision"]}
        if kind!="mihomo":
            wanted=gitlink(up,usha) if kind=="cmfa" else sha("MetaCubeX/mihomo",api("repos/MetaCubeX/mihomo/releases/latest")["tag_name"])
            if prior:
                old=max(prior,key=lambda r:version(r["version"]))
                matching=[c for c in core_index if c["upstream_commit"]==wanted]
                if matching and not cfg["revision"]>old["revision"] and old["core"]["commit"]==max(matching,key=lambda c:c["revision"])["source_commit"]:
                    continue
            core=read_core(wanted,core_index,kind=="verge")
            if not core:
                wait=True
                print(f"{up} {tag} waits for a published {CORE} based on {wanted}")
                continue
            rec["core"]=core
        elif prior and max(r["revision"] for r in prior)>=cfg["revision"]: continue
        rec["version"]=f"{tag}-r{cfg['revision']}" if kind=="mihomo" else next_app_version(own)
        return rec,wait
    return None,wait

def prepare(cfg,lock):
    repo,up,tag=cfg["repository"],cfg["upstream"],lock["version"]
    branch="builds/"+tag
    ref=api(f"repos/{repo}/git/ref/heads/{branch}",optional=True)
    if ref:
        existing=ref["object"]["sha"]
        if source_lock(repo,existing)!=lock: raise ValueError("Existing source branch has different inputs: "+branch)
        return existing
    cli("git","fetch","--no-tags","--depth=1","https://github.com/"+up+".git","refs/tags/"+lock["upstream_tag"],cwd=CONTROL)
    fetched=cli("git","rev-parse","FETCH_HEAD^{commit}",cwd=CONTROL).stdout.strip()
    if fetched!=lock["upstream_commit"]: raise ValueError("Fetched source differs from API")
    cli("git","worktree","add","--detach",str(SOURCE.resolve()),fetched,cwd=CONTROL)
    cli("git","apply","--index",str((CONTROL/"release/product.patch").resolve()),cwd=SOURCE)
    if cfg["project"]=="cmfa":
        cli("git","update-index","--cacheinfo","160000,"+lock["core"]["commit"]+","+MOD,cwd=SOURCE)
    (SOURCE/"distribution.lock.json").write_text(json.dumps(lock,indent=2,sort_keys=True)+"\n")
    cli("git","add","distribution.lock.json",cwd=SOURCE)
    # Keep upstream recipes available as data. Only this repository's reviewed CI is executable.
    ci=cli("git","rev-parse","HEAD",cwd=CONTROL).stdout.strip()
    cli("git","mv",".github/workflows",".github/upstream-workflows",cwd=SOURCE)
    cli("git","restore","--source="+ci,"--staged","--worktree","--",".github/workflows",cwd=SOURCE)
    tree=cli("git","write-tree",cwd=SOURCE).stdout.strip()
    source=cli("git","-c","user.name=github-actions[bot]","-c","user.email=41898282+github-actions[bot]@users.noreply.github.com","commit-tree",tree,"-p",ci,"-p",fetched,
               "-m",f"Stable distribution {tag} based on {up} {lock['upstream_tag']}",cwd=SOURCE).stdout.strip()
    cli("git","push","origin",source+":refs/heads/"+branch,cwd=SOURCE)
    return source

def main():
    cfg=json.loads((CONTROL/"release/project.json").read_text())
    lock,wait=resolve(cfg)
    output("waiting","true" if wait else "false")
    if lock is None:
        print("No build is due."); return
    commit=prepare(cfg,lock)
    output("tag",lock["version"]); output("source",commit)
    output("core",lock.get("core",{}).get("version",""))
    print(f"{cfg['repository']}: {lock['version']} -> {commit}")

if __name__=="__main__": main()
