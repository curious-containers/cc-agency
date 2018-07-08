from os import urandom
from flask import request
from binascii import hexlify


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
