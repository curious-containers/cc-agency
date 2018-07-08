from argparse import ArgumentParser
from flask import Flask, jsonify

from cc_agency.version import VERSION
from cc_agency.commons.conf import Conf
from cc_agency.commons.db import Mongo
from cc_agency.broker.auth import Auth
from cc_agency.broker.routes.red import red_routes

DESCRIPTION = 'CC-Agency broker.'

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


@app.route('/', methods=['GET'])
def get_root():
    return jsonify({'version': VERSION})


red_routes(app, mongo, auth)
