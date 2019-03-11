import os
import sys
from argparse import ArgumentParser

import zmq

from cc_agency.commons.conf import Conf


DESCRIPTION = 'CC-Agency Trustee'


def main():
    parser = ArgumentParser(description=DESCRIPTION)
    parser.add_argument(
        '-c', '--conf-file', action='store', type=str, metavar='CONF_FILE',
        help='CONF_FILE (yaml) as local path.'
    )
    args = parser.parse_args()

    conf = Conf(args.conf_file)

    bind_socket = os.path.expanduser(conf.d['trustee']['bind_socket'])
    bind_socket = 'ipc://{}'.format(bind_socket)
    os.umask(0o077)

    secrets = {}

    context = zmq.Context()
    socket = context.socket(zmq.REP)
    socket.bind(bind_socket)

    while True:
        data = socket.recv_json()

        action = data['action']

        if action == 'store':
            new_secrets = data['secrets']
            for key in new_secrets:
                if key in secrets:
                    socket.send_json({
                        'state': 'failed',
                        'debug_info': 'Key {} exists'.format(key)
                    })
                    continue
            secrets.update(new_secrets)

        elif action == 'delete':
            keys = data['keys']
            failed = []
            for key in keys:
                if key in secrets:
                    del secrets[key]
                else:
                    failed.append(secrets)
            if failed:
                print('Failed to delete keys: {}'.format(keys), file=sys.stderr)

            socket.send_json({
                'state': 'success'
            })

        elif action == 'collect':
            keys = data['keys']
            collected = {}
            for key in keys:
                if key in secrets:
                    collected[key] = secrets[key]
                else:
                    socket.send_json({
                        'state': 'failed',
                        'debug_info': 'Could not collect secret with key {}'.format(key)
                    })
                    continue

            socket.send_json({
                'state': 'success',
                'collected': collected
            })
