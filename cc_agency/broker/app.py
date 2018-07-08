from flask import Flask, jsonify

from cc_agency.version import VERSION
from cc_agency.commons.conf import Conf
from cc_agency.commons.db import Mongo
from cc_agency.broker.auth import Auth
from cc_agency.broker.routes.red import red_routes


app = Flask('broker')
application = app
conf = Conf(None)
mongo = Mongo(conf)
auth = Auth(conf, mongo)


@app.route('/', methods=['GET'])
def get_root():
    return jsonify({'version': VERSION})


red_routes(app, mongo, auth)
