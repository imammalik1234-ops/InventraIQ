"""Disposable browser-review backend for the Windows/WSL verification bridge.

Synthetic credentials travel only through process pipes, never files or logs.
The server stops when its parent's stdin closes. Never accepts a database target.
"""
import argparse
from contextlib import redirect_stdout
import json
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
from threading import Thread

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts import verify_transformation as qa


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--before', action='store_true')
    args = parser.parse_args()
    with TemporaryDirectory(prefix='inventraiq-browser-review-') as parent:
        with redirect_stdout(sys.stderr):
            root, app = qa.fixture(parent, 'small')
            _, private = qa.attest(root)
            initial = qa.verify(app)
        if args.before:
            from jinja2 import ChoiceLoader, FileSystemLoader
            app.jinja_loader = ChoiceLoader([
                FileSystemLoader(ROOT / 'data/private/ui-redesign/before-source/templates'), app.jinja_loader])
        server = qa.make_server('127.0.0.1', 0, app, threaded=True, request_handler=qa.Quiet)
        worker = Thread(target=server.serve_forever, daemon=True)
        worker.start()
        try:
            print(json.dumps(dict(origin='http://127.0.0.1:' + str(server.server_port), private=private)), flush=True)
            sys.stdin.readline()
            assert qa.verify(app) == initial, 'Browser review changed business facts'
            print(json.dumps(dict(business_facts_unchanged=True)), flush=True)
        finally:
            server.shutdown()
            worker.join(5)
            server.server_close()
            with app.app_context():
                qa.db.session.remove()
                qa.db.engine.dispose()


if __name__ == '__main__':
    main()
