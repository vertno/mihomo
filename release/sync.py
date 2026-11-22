"""Resolve a stable upstream release, verify its released dependencies and pin its source."""
import base64
import hashlib
import json
import os
import re
import subprocess
import sys
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

def release_rows(repo):
    """Iterate releases with a small response, including old maintenance releases."""
    page = 1
    while True:
        rows = api(f"repos/{repo}/releases?per_page=100&page={page}", "--jq",
                   "[.[] | {tag_name,draft,prerelease,published_at,body}]")
        for row in rows:
            if row["published_at"] and not row["draft"] and not row["prerelease"]:
                yield row
        if len(rows) < 100:
            return
        page += 1

def releases(repo, own=False, floor=None):
    result = []
    for row in release_rows(repo):
        tag = row["tag_name"]
        if not own:
            if (v := version(tag)) and v >= floor:
                result.append(row)
            continue
        marker = re.search(r"<!-- distribution (\{[^\n]*\}) -->", row.get("body") or "")
        if marker:
            info = json.loads(marker[1])
        elif tag.endswith("-patched") and version(tag.removesuffix("-patched")):
            # Editing release notes does not make a published version eligible again.
            info = {**source_lock(repo, tag), "source_commit": sha(repo, tag)}
        else:
            continue
        if info["version"] != tag:
            raise ValueError("Release tag and identity disagree: " + tag)
        result.append(info)
    return result if own else sorted(result, key=lambda r: version(r["tag_name"]))


def release_including_draft(repo,tag):
    pages=api(f"repos/{repo}/releases?per_page=100","--paginate","--slurp")
    rows=[r for page in pages for r in page if r["tag_name"]==tag]
    if len(rows)>1: raise ValueError("More than one release for "+tag)
    return api(f"repos/{repo}/releases/{rows[0]['id']}") if rows else None

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

def read_core(upstream_sha, records, upstream_tag=None):
    matches = [r for r in records if r["upstream_commit"] == upstream_sha
               and (upstream_tag is None or r["upstream_tag"] == upstream_tag)
               and r["version"] == r["upstream_tag"] + "-patched"]
    if not matches:
        return None
    core = max(matches, key=lambda r: version(r["upstream_tag"]))
    tag = core["version"]
    commit = sha(CORE, tag)
    lock = source_lock(CORE, commit)
    if commit != core["source_commit"] or any(lock[k] != core[k] for k in ("version", "upstream_commit")):
        raise ValueError("Core release identity mismatch: " + tag)
    return {"version": tag, "commit": commit, "upstream_commit": upstream_sha}

def windows_core(official, previous):
    # Migrate the previously shipped bundle without changing its stable core.
    if previous:
        last = max(previous, key=lambda r: version(r["version"]))
        return last["core"]["upstream_commit"], last["core"]["version"].split("-", 1)[0]
    # Upstream's prebuild downloads the latest stable core. Use its publication
    # cutoff so a delayed wake or a retry can never select a later release.
    eligible = (r for r in release_rows("MetaCubeX/mihomo") if version(r["tag_name"])
                and r["published_at"] <= official["published_at"])
    selected = max(eligible, key=lambda r: r["published_at"])
    tag = selected["tag_name"]
    return sha("MetaCubeX/mihomo", tag), tag

def resolve(cfg):
    kind, repo, upstream = cfg["project"], cfg["repository"], cfg["upstream"]
    if repo != os.environ["GITHUB_REPOSITORY"]:
        raise ValueError("Unexpected destination repository")
    own = releases(repo, own=True)
    issued = {r["version"] for r in own}
    official = releases(upstream, floor=version(cfg["first_tag"]))
    core_index = None
    waiting = False
    for row in official:
        tag = row["tag_name"]
        patched = tag + "-patched"
        if patched in issued:
            continue
        commit = sha(upstream, tag)
        previous = [r for r in own if r["upstream_tag"] == tag]
        if any(r["upstream_commit"] != commit for r in previous):
            raise ValueError("Upstream moved a previously released tag: " + tag)
        lock = {"version": patched, "upstream": upstream, "upstream_tag": tag, "upstream_commit": commit}
        if kind != "mihomo":
            if kind == "cmfa":
                wanted, core_tag = gitlink(upstream, commit), None
            else:
                wanted, core_tag = windows_core(row, previous)
            if core_index is None:
                core_index = releases(CORE, own=True)
            core = read_core(wanted, core_index, core_tag)
            if core is None:
                waiting = True
                print(f"{upstream} {tag} waits for a published {CORE} -patched release based on {wanted}")
                continue
            lock["core"] = core
        return lock, waiting
    return None, waiting

def release_order(record):
    tag = record["upstream_tag"]
    return version(tag), record["version"] == tag + "-patched"

def source_slot(cfg, lock):
    """Retries use the same source and tag. Published artifacts are immutable."""
    repo, tag = cfg["repository"], lock["version"]
    ref = api(f"repos/{repo}/git/ref/heads/builds/{tag}", optional=True)
    if ref is None:
        ref = api(f"repos/{repo}/git/ref/tags/{tag}", optional=True)
    if ref is None:
        return None
    commit = ref["object"]["sha"]
    if source_lock(repo, commit) != lock:
        raise ValueError("Reserved release has different source or core: " + tag)
    return commit


def prepare(cfg,lock):
    repo, up, tag = cfg["repository"], cfg["upstream"], lock["version"]
    branch = "builds/" + tag
    ci = cli("git", "rev-parse", "HEAD", cwd=CONTROL).stdout.strip()
    existing = source_slot(cfg, lock)
    previous_parent = []
    if existing is not None:
        old_ci = api(f"repos/{repo}/git/commits/{existing}", "--jq", ".parents[0].sha|@json")
        if old_ci == ci or api(f"repos/{repo}/git/ref/tags/{tag}", optional=True) is not None:
            return existing
        # Carry an unpublished attempt forward when its release instructions were fixed.
        cli("git", "fetch", "--no-tags", "--depth=1", "origin", "refs/heads/" + branch, cwd=CONTROL)
        previous_parent = ["-p", existing]
    cli("git","fetch","--no-tags","--depth=1","https://github.com/"+up+".git","refs/tags/"+lock["upstream_tag"],cwd=CONTROL)
    fetched=cli("git","rev-parse","FETCH_HEAD^{commit}",cwd=CONTROL).stdout.strip()
    if fetched!=lock["upstream_commit"]: raise ValueError("Fetched source differs from API")
    cli("git","worktree","add","--detach",str(SOURCE.resolve()),fetched,cwd=CONTROL)
    cli("git","apply","--index",str((CONTROL/"release/product.patch").resolve()),cwd=SOURCE)
    if cfg["project"]=="cmfa":
        cli("git","update-index","--cacheinfo","160000,"+lock["core"]["commit"]+","+MOD,cwd=SOURCE)
    (SOURCE/"distribution.lock.json").write_text(json.dumps(lock,indent=2,sort_keys=True)+"\n")
    cli("git","add","distribution.lock.json",cwd=SOURCE)
    # Preserve the original workflow files alongside the steps used in this build.
    cli("git","mv",".github/workflows",".github/upstream-workflows",cwd=SOURCE)
    cli("git","restore","--source="+ci,"--staged","--worktree","--",".github/workflows",cwd=SOURCE)
    from recipes import write_actions
    write_actions(cfg["project"], SOURCE)
    cli("git","add","--force","--",":(glob).github/distribution-actions/*/action.yml",cwd=SOURCE)
    tree=cli("git","write-tree",cwd=SOURCE).stdout.strip()
    source=cli("git","-c","user.name=github-actions[bot]","-c","user.email=41898282+github-actions[bot]@users.noreply.github.com","commit-tree",tree,"-p",ci,"-p",fetched,*previous_parent,
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
    output("upstream_tag",lock["upstream_tag"])
    output("tag",lock["version"]); output("source",commit)
    output("core",lock.get("core",{}).get("version",""))
    print(f"{cfg['repository']}: {lock['version']} -> {commit}")

if __name__=="__main__": main()
