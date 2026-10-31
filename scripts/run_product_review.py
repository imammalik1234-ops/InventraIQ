"""Owner review: populated, local-only SYN-T15-V1, removed on normal shutdown.

Never accepts a database URL or existing target. Passwords are entered privately;
--check uses random fixture credentials without displaying them.
"""
import argparse
from getpass import getpass
import os
from pathlib import Path
import sys
from tempfile import TemporaryDirectory

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--port', type=int, default=5055)
    parser.add_argument('--check', action='store_true')
    parser.add_argument('--proposals', action='store_true', help='Enable T27 only in this new disposable schema')
    args = parser.parse_args()
    if not 1024 <= args.port <= 65535:
        parser.error('Use a non-privileged local port (1024 to 65535).')
    password = None
    if not args.check:
        password = getpass('Choose a password for this temporary review only (12+ characters): ')
        if len(password) < 12 or password != getpass('Repeat password: '):
            parser.error('Matching passwords of at least 12 characters are required.')
    if 'app' in sys.modules:
        raise RuntimeError('Run this launcher in a fresh Python process.')
    os.environ.pop('GEMINI_API_KEY', None)
    os.environ.update(PYTHON_DOTENV_DISABLED='1', ASSISTANT_PROVIDER='fake', GEMINI_ENABLED='false',
                      DATABASE_URL='sqlite:///:memory:', OPENBLAS_NUM_THREADS='1', OMP_NUM_THREADS='1')
    def local_only(event, values):
        if event == 'socket.connect':
            address = values[1]
            if not isinstance(address, tuple) or address[0] not in ('127.0.0.1', '::1', 'localhost'):
                raise RuntimeError('External connections are disabled during product review.')
    sys.addaudithook(local_only)
    from scripts.measure_t16 import fixture
    from simulation.runtime import attest
    from simulation.verify import verify
    from models import User
    from database import db
    actors = {901: 'HQ administrator', 902: 'Independent HQ approver', 903: 'HQ analyst',
              912: 'Eastside manager', 922: 'Eastside clerk'}
    with TemporaryDirectory(prefix='inventraiq-owner-review-') as parent:
        root, app = fixture(parent, 'small')
        app.config['ASSISTANT_PROPOSALS_ENABLED'] = args.proposals
        try:
            assert verify(app)['ok'], 'The new demo must reconcile before it is served.'
            _, private = attest(root)
            if password is not None:
                with app.app_context():
                    for uid in actors:
                        db.session.get(User, uid).set_password(password)
                    db.session.commit()
            if args.check:
                for uid in actors:
                    caller = app.test_client()
                    portal = 'admin' if uid < 910 else 'staff'
                    caller.get('/login/' + portal)
                    with caller.session_transaction() as state:
                        csrf = state['_csrf_token']
                    response = caller.post('/login/' + portal, data=dict(
                        email=f'synthetic-{uid}@demo.invalid', password=private['credentials'][str(uid)], csrf_token=csrf))
                    assert response.status_code == 302
                    assert caller.get(response.location).status_code == 200
                assert verify(app)['ok']
                print('Review launcher check passed: five role logins, populated schema and reconciled synthetic stock.')
                return
            print(f'Local review: http://127.0.0.1:{args.port}/roles', flush=True)
            print('New disposable SYN-T15-V1. Business clock: 2031-01-03T12:00:00Z. All money is simulated.')
            for uid, label in actors.items():
                print(f'{label}: synthetic-{uid}@demo.invalid')
            print('Use the password you just chose. Ctrl+C stops the server and removes this temporary demo.')
            print('Assistant: local deterministic demonstration. T27: ' + ('enabled on this fresh schema only.' if args.proposals else 'disabled.'))
            app.run(host='127.0.0.1', port=args.port, debug=False, use_reloader=False)
        finally:
            with app.app_context():
                db.session.remove()
                db.engine.dispose()


if __name__ == '__main__':
    main()
