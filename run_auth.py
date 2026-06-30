#!/usr/bin/env python3
"""One-time OAuth flow to create token.json. Token is self-renewing thereafter —
this script does not need to run again unless the token is revoked.

Designed to run directly on a headless host (e.g. docker-server over SSH): it binds
the OAuth callback to a fixed local port instead of a random one and never tries to
open a browser locally. To complete consent from another machine, tunnel the port
over SSH first:

    ssh -L 8765:localhost:8765 <user>@<this-host>

then open the printed authorization URL in a browser on that other machine. Google
redirects to http://localhost:8765/... which the tunnel forwards back here.
"""
import argparse

from google_auth_oauthlib.flow import InstalledAppFlow

SCOPES = ['https://www.googleapis.com/auth/gmail.modify']

if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--port', type=int, default=8765,
                         help='Fixed local port for the OAuth callback (tunnel this over SSH if headless)')
    args = parser.parse_args()

    flow = InstalledAppFlow.from_client_secrets_file('secrets/credentials.json', SCOPES)
    creds = flow.run_local_server(port=args.port, open_browser=False)
    with open('secrets/token.json', 'w') as f:
        f.write(creds.to_json())
    print('token.json created at secrets/token.json')
