"""Behavioral checks for the shipped progressive enhancements; no extra packages."""
import shutil
import subprocess
import unittest
from pathlib import Path


@unittest.skipUnless(shutil.which('node'), 'Optional Node runtime unavailable')
class WorkspaceAccessibilityTests(unittest.TestCase):
    def check_scenario(self, scenario):
        result = subprocess.run(
            [shutil.which('node'), 'tests/t22_interactions.cjs', scenario],
            cwd=Path(__file__).resolve().parents[1],
            capture_output=True,
            text=True,
            timeout=10,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_mobile_navigation_contains_and_returns_focus(self):
        self.check_scenario('navigation')

    def test_navigation_resize_does_not_strand_focus(self):
        self.check_scenario('navigation-resize')

    def test_native_dialog_returns_to_its_actual_opener(self):
        self.check_scenario('dialog')

    def test_native_validation_describes_and_clears_owned_errors(self):
        self.check_scenario('validation')

    def test_client_validation_preserves_server_errors_and_hints(self):
        self.check_scenario('validation-preserve')

    def test_general_summary_describes_form_without_false_field_errors(self):
        self.check_scenario('summary')

    def test_action_disclosures_dismiss_without_closing_evidence(self):
        self.check_scenario('disclosure')

    def test_legacy_custom_validation_focuses_safe_associated_summary(self):
        self.check_scenario('legacy-validation')
