import json,os,unittest
from unittest.mock import patch
import sync as s

class ReleaseContracts(unittest.TestCase):
    def setUp(self):
        self.cfg=dict(project="cmfa",repository="vertno/ClashMetaForAndroid",upstream="MetaCubeX/ClashMetaForAndroid",first_tag="v2.11.37",revision=1)
        self.env=patch.dict(os.environ,GITHUB_REPOSITORY=self.cfg["repository"]);self.env.start();self.addCleanup(self.env.stop)
        self.core={"upstream_commit":"official-core","revision":1,"version":"v1.19.34-r1","source_commit":"patched-core"}
    def plan(self, published, previous=(), revision=1):
        self.cfg["revision"]=revision
        def releases(repo,own=False,floor=None):
            return ([self.core] if published else []) if repo==s.CORE else (list(previous) if own else ["v2.11.37"])
        with patch.object(s,"releases",side_effect=releases),patch.object(s,"sha",return_value="shell"),patch.object(s,"gitlink",return_value="official-core"),patch.object(s,"read_core",wraps=s.read_core) as gate:
            if published: gate.side_effect=lambda *a:dict(version=self.core["version"],commit=self.core["source_commit"],upstream_commit="official-core")
            return s.resolve(self.cfg)
    def test_missing_core_cannot_compile(self):
        self.assertEqual(self.plan(False),(None,True))
    def test_exact_release_and_duplicate_wake(self):
        lock,waiting=self.plan(True)
        self.assertFalse(waiting);self.assertEqual(lock["core"]["commit"],"patched-core")
        self.assertEqual(self.plan(True,[lock]),(None,False))
        second,_=self.plan(True,[lock],2)
        self.assertNotEqual(lock["version"],second["version"])
    def test_moved_upstream_tag(self):
        old,_=self.plan(True);old["upstream_commit"]="wrong"
        with self.assertRaisesRegex(ValueError,"moved"):self.plan(True,[old])
    def test_draft_prerelease_and_plain_tag_never_count(self):
        note=s.MARK+json.dumps(self.core)+" -->"
        base=dict(tag_name=self.core["version"],draft=False,prerelease=False,published_at="some-date",body=note)
        with patch.object(s,"api",return_value=[dict(base,draft=True),dict(base,prerelease=True),dict(base,published_at=None),dict(base,body=""),base]):
            self.assertEqual(s.releases(s.CORE,own=True),[self.core])
    def test_stale_release_source(self):
        with patch.object(s,"api",return_value=dict(draft=False,prerelease=False,published_at="now")),patch.object(s,"sha",return_value="different"),patch.object(s,"source_lock",return_value=self.core):
            with self.assertRaisesRegex(ValueError,"identity"):s.read_core("official-core",[self.core])
    def test_tool_versions_come_from_the_upstream_release_job(self):
        mirror="https://github.com/MetaCubeX/go/releases/download/build"
        def go(v, url=mirror):
            return {"uses":"actions/setup-go@upstream","with":{"go-version":v,"go-download-base-url":url}}
        java={"uses":"actions/setup-java@upstream","with":{"java-version":25,"distribution":"temurin"}}
        steps=[go("1.27"),go("matrix expression"),go("1.30","other architecture mirror"),java]
        workflow={"jobs":{"BuildRelease":{"steps":steps},"build":{"steps":steps}}}
        self.assertEqual(s.release_toolchains("cmfa",workflow),{"go":"1.27","java":"25"})
        self.assertEqual(s.release_toolchains("mihomo",workflow),{"go":"1.27"})
        steps.append(go("1.28"))
        with self.assertRaisesRegex(ValueError,"expected one release go version"):
            s.release_toolchains("mihomo",workflow)

    def test_resume_or_skip_reserved_application_version(self):
        lock,_=self.plan(True)
        ref={"object":{"sha":"staged"}}
        with patch.object(s,"api",return_value=ref),patch.object(s,"source_lock",return_value=dict(lock)):
            self.assertEqual(s.source_slot(self.cfg,lock),"staged")
        prior=dict(lock,core={"commit":"superseded"})
        with patch.object(s,"api",side_effect=[ref,None,None]),patch.object(s,"source_lock",return_value=prior):
            self.assertIsNone(s.source_slot(self.cfg,lock))
            self.assertEqual(lock["version"],"v1.0.2")
        prior=dict(lock,upstream_commit="tag-moved")
        with patch.object(s,"api",return_value=ref),patch.object(s,"source_lock",return_value=prior):
            with self.assertRaisesRegex(ValueError,"moved"):s.source_slot(self.cfg,lock)
    def test_late_retries_cannot_demote_latest(self):
        self.assertLess(s.release_order("verge",{"version":"v1.0.9"}),s.release_order("verge",{"version":"v1.0.10"}))
        self.assertLess(s.release_order("mihomo",{"upstream_tag":"v1.19.34","revision":10}),
                        s.release_order("mihomo",{"upstream_tag":"v1.20.0","revision":1}))
    def test_version_rollover(self):
        self.assertEqual(s.next_app_version([{"version":"v1.0.999"}]),"v1.1.0")
        self.assertEqual(s.next_app_version([{"version":"v1.99.999"}]),"v2.0.0")
    def test_draft_resolved_by_numeric_id(self):
        with patch.object(s,"api",side_effect=[[[{"id":12,"tag_name":"v1.0.1","draft":True}]],{"id":12,"draft":True}]) as api:
            self.assertEqual(s.release_including_draft(s.CORE,"v1.0.1")["id"],12)
            self.assertEqual(api.call_args.args[0],"repos/vertno/mihomo/releases/12")
