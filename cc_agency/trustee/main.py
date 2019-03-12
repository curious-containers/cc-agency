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

        self._socket = None
        self._socket = self._create_socket()

        atexit.register(self._socket.close)

    def _create_socket(self):
        if self._socket is not None:
            try:
                self._socket.close()
                print('Removed existing socket file.')
            except Exception:
                print('Could not close existing socket', file=sys.stderr)

        if os.path.exists(self._bind_socket_path):
            try:
                os.remove(self._bind_socket_path)
                print('Removed existing socket file.')
            except Exception:
                print('Could not remove existing socket file.', file=sys.stderr)
        
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
