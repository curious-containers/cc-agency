from flask import Flask, jsonify

from cc_agency.version import VERSION
from cc_agency.commons.conf import Conf
from cc_agency.broker.routes.red import red_routes


app = Flask('broker')
conf = Conf(None)


@app.route('/', methods=['GET'])
def get_root():
    return jsonify({'version': VERSION})


red_routes(app)
