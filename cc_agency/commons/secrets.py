import os
import json
from copy import deepcopy
from uuid import uuid4

import zmq
from zmq.error import ZMQError, Again


_RECEIVE_TIMEOUT = 2000


def separate_secrets_batch(batch):
    batch = deepcopy(batch)
    secrets = {}
    reversed_secrets = {}  # only for deduplication

    for io in ['inputs', 'outputs']:
        for cwl_key, cwl_val in batch[io].items():
            if not isinstance(cwl_val, dict):
                continue
            secret = cwl_val['connector']['access']
            dumped = json.dumps(secret, sort_keys=True)
            if dumped in reversed_secrets:
                key = reversed_secrets[dumped]
            else:
                key = str(uuid4())
                reversed_secrets[dumped] = key
                secrets[key] = secret

            cwl_val['connector']['access'] = key

    return batch, secrets


def separate_secrets_experiment(experiment):
    experiment = deepcopy(experiment)
    secrets = {}
    reversed_secrets = {}  # only for deduplication

    if 'auth' in experiment['container']['settings']['image']:
        key = str(uuid4())
        secret = experiment['container']['settings']['image']['auth']
        experiment['container']['settings']['image']['auth'] = key
        secrets[key] = secret
        reversed_secrets[json.dumps(secret, sort_keys=True)] = key

    return experiment, secrets


def get_batch_secret_keys(batch):
    keys = []
    for io in ['inputs', 'outputs']:
        for cwl_key, cwl_val in batch[io].items():
            if not isinstance(cwl_val, dict):
                continue
            keys.append(cwl_val['connector']['access'])
    return keys


def fill_batch_secrets(batch, secrets):
    batch = deepcopy(batch)
    for io in ['inputs', 'outputs']:
        for cwl_key, cwl_val in batch[io].items():
            if not isinstance(cwl_val, dict):
                continue

            key = cwl_val['connector']['access']
            secret = secrets[key]
            cwl_val['connector']['access'] = secret
    return batch


def get_experiment_secret_keys(experiment):
    keys = []
    if 'auth' in experiment['container']['settings']['image']:
        keys.append(experiment['container']['settings']['image']['auth'])
    return keys


def fill_experiment_secrets(experiment, secrets):
    experiment = deepcopy(experiment)
    if 'auth' in experiment['container']['settings']['image']:
        key = experiment['container']['settings']['image']['auth']
        secret = secrets[key]
        experiment['container']['settings']['image']['auth'] = secret
    return experiment


class TrusteeClient:
    def __init__(self, conf):
        self._conf = conf

        self._bind_socket_path = os.path.expanduser(conf.d['trustee']['bind_socket_path'])
        self._bind_socket_ipc_path = 'ipc://{}'.format(self._bind_socket_path)

        self._socket = self._connect_socket()

    def _connect_socket(self):
        context = zmq.Context()
        socket = context.socket(zmq.REQ)
        socket.setsockopt(zmq.RCVTIMEO, _RECEIVE_TIMEOUT)
        socket.connect(self._bind_socket_ipc_path)
        return socket

    def store(self, secrets):
        return self._request({
            'action': 'store',
            'secrets': secrets
        })

    def delete(self, keys):
        return self._request({
            'action': 'delete',
            'keys': keys
        })

    def collect(self, keys):
        return self._request({
            'action': 'collect',
            'keys': keys
        })

    def inspect(self):
        return self._request({
            'action': 'inspect'
        })

    def _request(self, d):
        try:
            self._socket.send_json(d)
        except ZMQError as e:
            debug_info = '{}:{}{}'.format(repr(e), os.linesep, e)
            self._socket = self._connect_socket()
            return {'state': 'failed', 'debug_info': debug_info, 'disable_retry': False, 'inspect': True}

        try:
            return self._socket.recv_json()
        except (ZMQError, Again) as e:
            debug_info = '{}:{}{}'.format(repr(e), os.linesep, e)
            self._socket = self._connect_socket()
            return {'state': 'failed', 'debug_info': debug_info, 'disable_retry': False, 'inspect': True}
