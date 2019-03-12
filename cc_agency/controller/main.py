import os
from argparse import ArgumentParser
import zmq

from cc_core.version import VERSION as CORE_VERSION
from cc_agency.version import VERSION as AGENCY_VERSION
from cc_agency.commons.conf import Conf
from cc_agency.commons.db import Mongo
from cc_agency.controller.scheduler import Scheduler


DESCRIPTION = 'CC-Agency Controller'


def main():
    print('CC-Agency Version:', AGENCY_VERSION)
    print('CC-Core Version:', CORE_VERSION)

    parser = ArgumentParser(description=DESCRIPTION)
    parser.add_argument(
        '-c', '--conf-file', action='store', type=str, metavar='CONF_FILE',
        help='CONF_FILE (yaml) as local path.'
    )
    args = parser.parse_args()

    # Singletons
    conf = Conf(args.conf_file)
    mongo = Mongo(conf)
    scheduler = Scheduler(conf, mongo)

    bind_socket_path = os.path.expanduser(conf.d['controller']['bind_socket_path'])
    bind_socket_dir, _ = os.path.split(bind_socket_path)

    if not os.path.exists(bind_socket_dir):
        try:
            os.makedirs(bind_socket_dir)
        except Exception:
            pass

    old_umask = os.umask(0o077)
    context = zmq.Context()
    socket = context.socket(zmq.PULL)
    socket.bind('ipc://{}'.format(bind_socket_path))
    os.umask(old_umask)

    while True:
        data = socket.recv_json()

        if 'destination' not in data:
            continue

        destination = data['destination']
        if destination == 'scheduler':
            scheduler.schedule()
