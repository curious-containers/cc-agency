import os
import sys
from argparse import ArgumentParser
import atexit

import zmq
from zmq.error import ZMQError

from cc_agency.commons.conf import Conf


DESCRIPTION = 'CC-Agency Trustee'


class SocketWrapper:
    def __init__(self, conf):
        self._conf = conf
        self._bind_socket_path = os.path.expanduser(conf.d['trustee']['bind_socket_path'])
        self._bind_socket_dir, _ = os.path.split(self._bind_socket_path)
        self._bind_socket_ipc_path = 'ipc://{}'.format(self._bind_socket_path)

        if not os.path.exists(self._bind_socket_dir):
            try:
                os.makedirs(self._bind_socket_dir)
            except Exception:
                pass

        self._socket = self._create_socket()

        atexit.register(self._socket.close)

    def _create_socket(self):
        old_umask = os.umask(0o077)
        context = zmq.Context()
        socket = context.socket(zmq.REP)
        socket.bind(self._bind_socket_ipc_path)
        os.umask(old_umask)
        return socket

    def recv_json(self):
        return self._socket.recv_json()

    def send_json(self, data):
        self._socket.send_json(data)


def main():
    parser = ArgumentParser(description=DESCRIPTION)
    parser.add_argument(
        '-c', '--conf-file', action='store', type=str, metavar='CONF_FILE',
        help='CONF_FILE (yaml) as local path.'
    )
    args = parser.parse_args()

    conf = Conf(args.conf_file)

    socket = SocketWrapper(conf)

    secrets = {}

    while True:
        data = socket.recv_json()

        action = data['action']

        if action == 'inspect':
            socket.send_json({
                'state': 'success'
            })
            continue

        if action == 'store':
            new_secrets = data['secrets']
            existing_keys = []

            for key in new_secrets:
                if key in secrets:
                    existing_keys.append(key)

            if existing_keys:
                socket.send_json({
                    'state': 'failed',
                    'debug_info': 'Keys already exist: {}'.format(existing_keys),
                    'disable_retry': False,
                    'inspect': False
                })
                continue

            secrets.update(new_secrets)

            socket.send_json({
                'state': 'success'
            })
            continue

        if action == 'delete':
            keys = data['keys']
            for key in keys:
                if key in secrets:
                    del secrets[key]

            socket.send_json({
                'state': 'success'
            })
            continue

        if action == 'collect':
            keys = data['keys']
            collected = {}
            missing_keys = []
            for key in keys:
                if key in secrets:
                    collected[key] = secrets[key]
                else:
                    missing_keys.append(key)

            if missing_keys:
                socket.send_json({
                    'state': 'failed',
                    'debug_info': 'Could not collect keys: {}'.format(missing_keys),
                    'disable_retry': True,
                    'inspect': False
                })
                continue

            socket.send_json({
                'state': 'success',
                'collected': collected
            })
            continue

        socket.send_json({
            'state': 'failed',
            'debug_info': 'Unknown trustee action.',
            'disable_retry': False,
            'inspect': False
        })
