from os import urandom
from binascii import hexlify
from time import time

from flask import request
from bson.objectid import ObjectId
from cryptography.hazmat.backends import default_backend
from cryptography.hazmat.primitives.hashes import SHA256
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC


def get_ip():
    headers = ['HTTP_X_FORWARDED_FOR', 'HTTP_X_REAL_IP', 'REMOTE_ADDR']
    ip = None
    for header in headers:
        ip = request.environ.get(header)
        if ip:
            break
    if not ip:
        ip = '127.0.0.1'
    return ip


def generate_secret():
    return hexlify(urandom(24)).decode('utf-8')


def create_kdf(salt):
    return PBKDF2HMAC(
        algorithm=SHA256(),
        length=32,
        salt=salt,
        iterations=100000,
        backend=default_backend()
    )


def batch_failure(
        mongo,
        batch_id,
        debug_info,
        ccagent,
        current_state,
        disable_retry_if_failed=False,
        docker_stats=None
):
    """
    Changes the db entry of the given batch to failed, if disable_retry_if_failed is set to True or if the maximal
    number of retries is exceeded. Otherwise the new state of the given batch is set to registered.

    :param mongo: The mongodb client to update
    :param batch_id: The batch id specifying the batch to fail
    :type batch_id: str
    :param debug_info: The debug info to write to the db
    :param ccagent: The ccagent to write to the db
    :param current_state: The expected current state of the batch to cancel. If this state does not match the batch from
                          the db, the db entry is not updated.
    :type current_state: str
    :param disable_retry_if_failed: If set to True, the batch is failed immediately, without giving another attempt
    :param docker_stats: The optional stats of the docker container, that will written under the "docker_stats" key in
                         the history of this batch
    :type docker_stats: dict
    """
    bson_id = ObjectId(batch_id)

    batch = mongo.db['batches'].find_one(
        {'_id': bson_id},
        {'attempts': 1, 'node': 1, 'experimentId': 1}
    )

    timestamp = time()
    attempts = batch['attempts']
    node_name = batch['node']

    new_state = 'registered'
    new_node = None

    if attempts >= 2 or disable_retry_if_failed:
        new_state = 'failed'
        new_node = node_name
    else:
        experiment_id = batch['experimentId']
        bson_experiment_id = ObjectId(experiment_id)
        experiment = mongo.db['experiments'].find_one(
            {'_id': bson_experiment_id},
            {'execution.settings.retryIfFailed': 1}
        )
        if not (experiment and experiment.get('execution', {}).get('settings', {}).get('retryIfFailed')):
            new_state = 'failed'
            new_node = node_name

    mongo.db['batches'].update_one(
        {'_id': bson_id, 'state': current_state},
        {
            '$set': {
                'state': new_state,
                'node': new_node
            },
            '$push': {
                'history': {
                    'state': new_state,
                    'time': timestamp,
                    'debugInfo': debug_info,
                    'node': new_node,
                    'ccagent': ccagent,
                    'dockerStats': docker_stats
                }
            }
        }
    )


def str_to_bool(s):
    if isinstance(s, str) and s.lower() in ['1', 'true']:
        return True
    return False
