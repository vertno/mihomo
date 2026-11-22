"""Expose build steps from the selected upstream Git tree as native GitHub actions."""
from pathlib import Path

import yaml


def write_actions(project, source):
    def steps(workflow, job):
        path = source / ".github/upstream-workflows" / workflow
        return yaml.safe_load(path.read_text())["jobs"][job]["steps"]

    def index(items, name):
        matches = [i for i, item in enumerate(items) if item.get("name") == name]
        if len(matches) != 1:
            raise ValueError(f"Expected one upstream step named {name!r}, found {len(matches)}")
        return matches[0]

    def save(name, items, inputs=None):
        # A composite requires an explicit shell. Retain the runner's native default.
        shell = "pwsh" if project == "verge" else "bash"
        for item in items:
            if "run" in item:
                item.setdefault("shell", shell)
        action = {"name": name, "description": "Steps from the corresponding upstream release",
                  "runs": {"using": "composite", "steps": items}}
        if inputs:
            action["inputs"] = inputs
        path = source / ".github/distribution-actions" / name / "action.yml"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(yaml.safe_dump(action, sort_keys=False, width=120))

    if project == "mihomo":
        build = steps("build.yml", "build")
        save("setup", build[1:index(build, "Set variables")])
        save("test", [build[index(build, "Test")]])
        save("build", build[index(build, "Update CA"):index(build, "Build core") + 1])
    elif project == "verge":
        build = steps("release.yml", "release")
        bundle = build[index(build, "Tauri build")]
        save("setup", build[index(build, "Checkout Repository") + 1:index(build, "Tauri build")])
        frontend = steps("frontend-check.yml", "frontend")
        backend = steps("lint-clippy.yml", "clippy")
        save("test", [frontend[index(frontend, "Run TypeScript typecheck")],
                      frontend[index(frontend, "Run tests")]])
        save("rust-test", [backend[index(backend, "Run Windows security tests")]])
        # Tauri builds and signs locally. The distribution publishes both architectures together.
        bundle["with"] = {k: bundle["with"][k] for k in ("tauriScript", "args")}
        bundle["env"] = {k: v for k, v in bundle["env"].items() if "secrets." not in str(v)}
        save("build", [bundle])
    elif project == "cmfa":
        build = steps("build-release.yaml", "BuildRelease")
        rewrite = index(build, "Re-write version")
        # The upstream action computes versionCode from the numeric official tag.
        build[rewrite]["with"]["versionName"] += "-patched"
        save("setup", build[index(build, "Checkout submodules") + 1:rewrite + 1] + [build[index(build, "Update CA")]],
             {"release-tag": {"description": "Official stable tag before the distribution suffix", "required": True}})
        save("build", [build[index(build, "Release Build")]])
    else:
        raise ValueError("Unknown release project: " + project)
