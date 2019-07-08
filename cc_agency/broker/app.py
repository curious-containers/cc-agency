import os
from argparse import ArgumentParser

from flask import Flask, jsonify, request
from werkzeug.exceptions import Unauthorized
import zmq

from cc_core.version import VERSION as CORE_VERSION
from cc_agency.version import VERSION as AGENCY_VERSION
from cc_agency.commons.conf import Conf
from cc_agency.commons.db import Mongo
from cc_agency.commons.secrets import TrusteeClient
from cc_agency.broker.auth import Auth
from cc_agency.broker.routes.red import red_routes
from cc_agency.broker.routes.nodes import nodes_routes
from cc_agency.broker.routes.auth import auth_routes


DESCRIPTION = 'CC-Agency Broker.'

app = Flask('broker')
application = app

parser = ArgumentParser(description=DESCRIPTION)
parser.add_argument(
    '-c', '--conf-file', action='store', type=str, metavar='CONF_FILE',
    help='CONF_FILE (yaml) as local path.'
)
args = parser.parse_args()

conf = Conf(args.conf_file)
mongo = Mongo(conf)
auth = Auth(conf, mongo)
trustee_client = TrusteeClient(conf)

bind_socket_path = os.path.expanduser(conf.d['controller']['bind_socket_path'])
bind_socket = 'ipc://{}'.format(bind_socket_path)

context = zmq.Context()
controller = context.socket(zmq.PUSH)
controller.connect(bind_socket)


@app.route('/', methods=['GET'])
def get_root():
    return jsonify({'Hello': 'World'})


@app.route('/version', methods=['GET'])
def get_version():
    user = auth.verify_user(request.authorization)
    if not user:
        raise Unauthorized()

    return jsonify({
        'agencyVersion': AGENCY_VERSION,
        'coreVersion': CORE_VERSION
    })


red_routes(app, mongo, auth, controller, trustee_client)
nodes_routes(app, mongo, auth, conf)
auth_routes(app, auth, conf)

controller.send_json({'destination': 'scheduler'})
