import pathlib
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
import discovery


class DiscoveryTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = pathlib.Path(self.temporary.name).resolve()

    def git(self, path, *arguments):
        return subprocess.run(
            ["/usr/bin/git", "-C", str(path), *arguments], capture_output=True, check=True,
        ).stdout.decode().strip()

    def repository(self, path):
        path.mkdir(parents=True)
        self.git(path, "init", "-b", "main")
        self.git(path, "-c", "user.name=Discovery Test", "-c", "user.email=test@example.invalid",
                 "commit", "--allow-empty", "-m", "Initial")
        return path

    def test_local_repositories_and_linked_worktrees_are_deduplicated(self):
        repo = self.repository(self.root / "repo")
        linked = self.root / "feature"
        self.git(repo, "worktree", "add", "-b", "feature", str(linked))
        detached = self.root / "detached"
        self.git(repo, "worktree", "add", "--detach", str(detached))
        result = discovery.discover_projects([str(self.root), str(repo), str(self.root)])
        found = {item["name"]: item for item in result["projects"]}
        self.assertEqual(set(found), {"repo", "feature", "detached"})
        self.assertEqual(found["repo"]["kind"], "repository")
        self.assertEqual(found["feature"]["branch"], "feature")
        self.assertEqual(found["feature"]["kind"], "worktree")
        self.assertIsNone(found["detached"]["branch"])
        self.assertEqual(found["feature"]["id"], str(linked))
        self.assertEqual(result["warnings"], [])

    def test_depth_exclusions_symlinks_and_outside_worktrees(self):
        repo = self.repository(self.root / "selected" / "repo")
        selected = self.root / "selected"
        outside = self.root / "outside"
        self.git(repo, "worktree", "add", "-b", "outside", str(outside))
        (selected / "symlink").symlink_to(outside, target_is_directory=True)
        for prefix in ("node_modules", ".hidden", "venv", "dist", "build", "Library"):
            self.repository(selected / prefix / "ignored")
        self.repository(selected / "one" / "two" / "included")
        self.repository(selected / "one" / "two" / "three" / "too-deep")
        found = discovery.discover_projects([str(selected)])
        self.assertEqual({item["name"] for item in found["projects"]}, {"repo", "included"})

    def test_invalid_metadata_missing_roots_and_bare_repositories(self):
        invalid = self.root / "invalid"
        invalid.mkdir()
        (invalid / ".git").write_text("not a git checkout\n")
        bare = self.root / "bare"
        self.git(self.root, "init", "--bare", str(bare))
        result = discovery.discover_projects([str(self.root), str(self.root / "missing")])
        self.assertEqual(result["projects"], [])
        self.assertEqual(len(result["warnings"]), 2)

    def test_missing_and_prunable_worktrees_are_ignored(self):
        repo = self.repository(self.root / "repo")
        linked = self.root / "gone"
        self.git(repo, "worktree", "add", "-b", "gone", str(linked))
        import shutil
        shutil.rmtree(linked)
        found = discovery.discover_projects([str(self.root)])
        self.assertEqual([item["name"] for item in found["projects"]], ["repo"])

    def test_limits_are_visible(self):
        self.repository(self.root / "first")
        self.repository(self.root / "second")
        with patch.object(discovery, "MAX_PROJECTS", 1):
            result = discovery.discover_projects([str(self.root)])
        self.assertEqual(len(result["projects"]), 1)
        self.assertIn("1 projects", result["warnings"][0])
        with patch.object(discovery, "MAX_DIRECTORIES", 1):
            result = discovery.discover_projects([str(self.root)])
        self.assertEqual(result["projects"], [])
        self.assertIn("1 directories", result["warnings"][0])

    def test_budget_and_unreadable_roots_are_reported(self):
        with patch.object(discovery.time, "monotonic", side_effect=[0, 6]), \
                patch.object(discovery, "_worktrees") as metadata:
            result = discovery.discover_projects([str(self.root)])
        self.assertIn("5 seconds", result["warnings"][0])
        metadata.assert_not_called()
        with patch.object(discovery.os, "access", return_value=False):
            result = discovery.discover_projects([str(self.root)])
        self.assertIn("not readable", result["warnings"][0])

    def test_nul_metadata_handles_newlines_in_checkout_paths(self):
        repo = self.repository(self.root / "repo with\na newline")
        found = discovery.discover_projects([str(self.root)])
        self.assertEqual(found["projects"][0]["path"], str(repo))
        self.assertEqual(found["warnings"], [])

    def test_git_is_read_only_bounded_and_does_not_use_a_shell(self):
        result = subprocess.CompletedProcess([], 0, b"worktree /checkout\0HEAD abc\0detached\0\0", b"")
        with patch.object(discovery.subprocess, "run", return_value=result) as run:
            self.assertEqual(discovery._worktrees(pathlib.Path("/checkout"))[0]["worktree"], "/checkout")
        args, kwargs = run.call_args
        self.assertIn("--no-optional-locks", args[0])
        self.assertIn("core.fsmonitor=false", args[0])
        self.assertEqual(kwargs["timeout"], 2)
        self.assertNotIn("shell", kwargs)


if __name__ == "__main__":
    unittest.main()
