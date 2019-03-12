import os
import json
from time import sleep
from random import random
from copy import deepcopy
from uuid import uuid4

import zmq
from zmq.error import ZMQError


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

        bind_socket = os.path.expanduser(conf.d['trustee']['bind_socket_path'])
        bind_socket = 'ipc://{}'.format(bind_socket)

        context = zmq.Context()
        self._trustee = context.socket(zmq.REQ)
        self._trustee.connect(bind_socket)

    def store(self, secrets):
        return self._request({
            'action': 'store',
            'keys': secrets
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

    def _request(self, d):
        while True:
            try:
                self._trustee.send_json(d)
            except ZMQError:
                print('PID {} could not send {} request to trustee.'.format(os.getpid(), d['action']))
                sleep(random())
                continue

            return self._trustee.recv_json()
