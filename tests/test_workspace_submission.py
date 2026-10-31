"""No browser or new package required for deterministic transport edge cases."""
import shutil
import subprocess
import unittest
from pathlib import Path


@unittest.skipUnless(shutil.which('node'), 'Optional Node runtime unavailable')
class WorkspaceSubmissionTests(unittest.TestCase):
    def check_outcome(self, outcome):
        result = subprocess.run([shutil.which('node'), 'tests/workspace_submission.cjs', outcome],
                                cwd=Path(__file__).resolve().parents[1], capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_pending_duplicate_blocked(self): self.check_outcome('pending')
    def test_disconnected_submission_locked(self): self.check_outcome('network')
    def test_timed_out_submission_locked(self): self.check_outcome('timeout')
    def test_server_error_submission_locked(self): self.check_outcome('server')
    def test_confirmed_validation_rejection_can_be_corrected(self): self.check_outcome('validation')
    def test_success_navigates_once_without_consuming_feedback(self): self.check_outcome('success')
    def test_external_navigation_cannot_follow_enhanced_success(self): self.check_outcome('foreign-redirect')
