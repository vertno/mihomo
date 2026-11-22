import json
import os
import unittest
from unittest.mock import patch

import sync


class ReleaseContracts(unittest.TestCase):
    app = "vertno/ClashMetaForAndroid"
    tag = "v2.11.37"
    core = dict(version="v1.19.34-patched", upstream_tag="v1.19.34",
                upstream_commit="official-core", source_commit="custom-core")

    def plan(self, project="cmfa", published=(), cores=()):
        cfg = dict(project=project, repository=self.app, upstream="upstream/app", first_tag=self.tag)
        row = dict(tag_name=self.tag, published_at="2026-11-11T15:00:00Z")
        def releases(repo, own=False, floor=None):
            if repo == sync.CORE:
                return cores
            return published if own else [row]
        with (patch.dict(os.environ, GITHUB_REPOSITORY=self.app),
              patch.object(sync, "releases", side_effect=releases),
              patch.object(sync, "sha", side_effect=lambda repo, tag: "custom-core" if repo == sync.CORE else "app-sha"),
              patch.object(sync, "source_lock", return_value=self.core),
              patch.object(sync, "gitlink", return_value="official-core"),
              patch.object(sync, "windows_core", return_value=("official-core", "v1.19.34")) as select_windows):
            result = sync.resolve(cfg)
            if published and published[0]["version"] == self.tag + "-patched":
                select_windows.assert_not_called()
            return result

    def test_clients_wait_for_public_canonical_core_and_issue_once(self):
        legacy_core = dict(self.core, version="v1.19.34-r3")
        older_tag = dict(self.core, version="v1.19.33-patched", upstream_tag="v1.19.33")
        self.assertEqual(self.plan("verge", cores=[older_tag]), (None, True))
        for project in ("cmfa", "verge"):
            self.assertEqual(self.plan(project, cores=[legacy_core]), (None, True))
            self.assertEqual(self.plan(project, cores=[dict(self.core, upstream_commit="other")]), (None, True))
            lock, waiting = self.plan(project, cores=[self.core])
            self.assertFalse(waiting)
            self.assertEqual(lock["version"], self.tag + "-patched")
            self.assertEqual(lock["core"]["commit"], "custom-core")
            self.assertEqual(self.plan(project, [lock], []), (None, False))

    def test_windows_uses_stable_at_release_or_previously_bundled_core(self):
        row = dict(published_at="2026-11-10T12:00:00Z")
        cores = [dict(tag_name="v1.19.34", published_at="2026-11-11T12:00:00Z"),
                 dict(tag_name="v1.19.33", published_at="2026-10-10T12:00:00Z")]
        with patch.object(sync, "release_rows", return_value=cores), patch.object(sync, "sha", side_effect=lambda _, t: t):
            self.assertEqual(sync.windows_core(row, []), ("v1.19.33", "v1.19.33"))
            old = dict(version="v1.0.4", core={"upstream_commit": "shipped", "version": "v1.19.34-r3"})
            self.assertEqual(sync.windows_core(row, [old]), ("shipped", "v1.19.34"))

    def test_plain_tag_draft_and_prerelease_cannot_unblock(self):
        base = dict(tag_name=self.core["version"], draft=False, prerelease=False, published_at="now",
                    body=sync.MARK + json.dumps(self.core) + " -->")
        rows = [dict(base, draft=True), dict(base, prerelease=True), dict(base, published_at=None),
                dict(base, tag_name="updater", body=""), base]
        with patch.object(sync, "api", return_value=rows):
            self.assertEqual(sync.releases(sync.CORE, own=True), [self.core])

    def test_retry_same_tag_and_release_order(self):
        lock, _ = self.plan(cores=[self.core])
        cfg = {"repository": self.app}
        with patch.object(sync, "api", return_value={"object": {"sha": "staged"}}):
            with patch.object(sync, "source_lock", return_value=lock):
                self.assertEqual(sync.source_slot(cfg, lock), "staged")
            with patch.object(sync, "source_lock", return_value=dict(lock, upstream_commit="moved")):
                with self.assertRaisesRegex(ValueError, "different"):
                    sync.source_slot(cfg, lock)
        self.assertGreater(sync.release_order(lock), sync.release_order(dict(lock, version="v1.0.4")))
        self.assertLess(sync.release_order(lock), sync.release_order(dict(lock, upstream_tag="v2.12.0")))
