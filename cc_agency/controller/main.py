from argparse import ArgumentParser
import zmq

from cc_agency.commons.conf import Conf


DESCRIPTION = 'CC-Agency Controller'


def main():
    parser = ArgumentParser(description=DESCRIPTION)
    parser.add_argument(
        '-c', '--conf-file', action='store', type=str, metavar='CONF_FILE',
        help='CONF_FILE (yaml) as local path.'
    )
    args = parser.parse_args()

    # Singletons
    conf = Conf(args.conf_file)

    context = zmq.Context()
    socket = context.socket(zmq.PULL)
    socket.bind('tcp://{}:{}'.format(
        conf.d['controller']['bind_host'],
        conf.d['controller']['bind_port']
    ))

    while True:
        data = socket.recv_json()
        action = data['destination']
        if action == 'red':
            pass
