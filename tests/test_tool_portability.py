"""Portable operator selection and WSL argv construction; no databases or WSL launch."""
import importlib.util
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]

def load(name, filename):
    spec = importlib.util.spec_from_file_location(name, ROOT / 'scripts' / filename)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module

singleton = load('portable_singletons', 'verify_phase1_singletons.py')
review = load('portable_ui_review', 'verify_ui_redesign.py')

class OperatorSelectionTests(unittest.TestCase):
    def select(self, environment, owner=None, error=None):
        owner = owner or SimpleNamespace(pw_name='operator', pw_uid=1007, pw_gid=1007, pw_dir=str(ROOT.parent / 'operator-home'))
        fake = SimpleNamespace(getpwnam=lambda name: owner)
        with patch.dict(sys.modules, {'pwd': fake}), patch.dict(singleton.os.environ, environment, clear=True):
            if error:
                fake.getpwnam = lambda name: (_ for _ in ()).throw(error)
            return singleton.operator_owner()

    def test_sudo_account_home_drives_private_state_identity(self):
        owner = self.select({'SUDO_USER': 'operator'})
        self.assertEqual(owner.pw_name, 'operator')
        self.assertEqual(Path(owner.pw_dir) / '.local/state/inventraiq-phase1', ROOT.parent / 'operator-home/.local/state/inventraiq-phase1')

    def test_explicit_operator_overrides_sudo_account(self):
        names = []
        fake = SimpleNamespace(getpwnam=lambda name: names.append(name) or SimpleNamespace(pw_name=name,pw_uid=1008,pw_gid=1008,pw_dir=str(ROOT.parent / 'chosen-home')))
        with patch.dict(sys.modules, {'pwd': fake}), patch.dict(singleton.os.environ, {'INVENTRAIQ_OPERATOR_USER':'chosen','SUDO_USER':'other'},clear=True):
            self.assertEqual(singleton.operator_owner().pw_name,'chosen')
        self.assertEqual(names,['chosen'])

    def test_missing_operator_stops_without_account_lookup(self):
        with self.assertRaisesRegex(RuntimeError,'Run via sudo'):
            self.select({})

    def test_unknown_account_has_actionable_error(self):
        with self.assertRaisesRegex(RuntimeError,'existing Unix account'):
            self.select({'SUDO_USER':'missing'},error=KeyError('missing'))

    def test_root_account_is_rejected(self):
        with self.assertRaisesRegex(RuntimeError,'non-root'):
            self.select({'SUDO_USER':'root'},SimpleNamespace(pw_name='root',pw_uid=0,pw_dir='/root'))

    def test_relative_account_home_is_rejected(self):
        with self.assertRaisesRegex(RuntimeError,'absolute home'):
            self.select({'SUDO_USER':'operator'},SimpleNamespace(pw_name='operator',pw_uid=1007,pw_dir='relative-home'))

class WslCommandTests(unittest.TestCase):
    def test_default_interpreter_uses_release_working_directory(self):
        self.assertEqual(review.wsl_command(r'C:\Projects\Retail Release'),['wsl','--cd','/mnt/c/Projects/Retail Release','--exec','python3','-B','scripts/ui_review_server.py'])

    def test_selected_interpreter_and_before_flag_are_separate_arguments(self):
        self.assertEqual(review.wsl_command(r'D:\Retail Release','/srv/venv with spaces/bin/python',True),['wsl','--cd','/mnt/d/Retail Release','--exec','/srv/venv with spaces/bin/python','-B','scripts/ui_review_server.py','--before'])

    def test_relative_and_unc_roots_fail_clearly(self):
        for root in ('relative',r'\\server\share\project'):
            with self.subTest(root=root),self.assertRaisesRegex(ValueError,'absolute Windows drive'):
                review.wsl_command(root)

    def test_empty_interpreter_is_rejected(self):
        for value in ('','   '):
            with self.subTest(value=value),self.assertRaisesRegex(ValueError,'WSL Python interpreter'):
                review.wsl_command(r'C:\release',value)

if __name__ == '__main__':
    unittest.main()
