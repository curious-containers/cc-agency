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


def batch_failure(mongo, batch_id, debug_info, ccagent, conf):
    bson_id = ObjectId(batch_id)

    batch = mongo.db['batches'].find_one(
        {'_id': bson_id},
        {'attempts': 1, 'node': 1}
    )

    timestamp = time()
    attempts = batch['attempts']
    node_name = batch['node']

    new_state = 'registered'
    new_node = None

    attempts_to_fail = conf.d['controller']['scheduling']['attempts_to_fail']

    if attempts >= attempts_to_fail:
        new_state = 'failed'
        new_node = node_name

    mongo.db['batches'].update(
        {'_id': bson_id},
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
                    'ccagent': ccagent
                }
            }
        }
    )


def str_to_bool(s):
    if isinstance(s, str) and s.lower() in ['1', 'true']:
        return True
    return False
