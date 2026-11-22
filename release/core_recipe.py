"""Use the Test, Update CA and Build core shell steps from the selected official tag."""
import os
from pathlib import Path
import subprocess
import sys
import yaml

steps=yaml.safe_load(Path(".github/workflows/build.yml").read_text())["jobs"]["build"]["steps"]
mode=sys.argv[1]
env=os.environ.copy()
names=["Test"] if mode=="test" else ["Update CA","Build core"]
for name in names:
    step,=[s for s in steps if s.get("name")==name]
    script=step["run"]
    if mode!="test":
        arch={"amd64-v2":"amd64","arm64":"arm64"}[mode]
        env.update(GOOS="windows",GOARCH=arch,GOAMD64="v2" if arch=="amd64" else "",BUILDTAG="-extldflags --static")
        for key,val in {"goos":"windows","output":mode}.items():
            script=script.replace(chr(36)+"{{matrix.jobs."+key+"}}",val)
    if chr(36)+"{{" in script: raise ValueError("Review changed upstream expressions in "+name)
    subprocess.run(["bash","-e","-o","pipefail","-c",script],env=env,check=True)
