import jsonschema
from flask import jsonify, request
from werkzeug.exceptions import Unauthorized, BadRequest

from cc_core.commons.schemas.red import red_schema


def red_routes(app, mongo, auth):
    @app.route('/red', methods=['POST'])
    def post_red():
        user = auth(request.auth)
        if not user:
            raise Unauthorized()

        try:
            data = request.json()
        except:
            raise BadRequest('Did not send RED data as JSON.')

        try:
            jsonschema.validate(data, red_schema)
        except:
            raise BadRequest('Given RED data does not comply with jsonschema. '
                             'Consider using the FAICE commandline tools for local validation.')

        # check for docker in red
        # strip red data
        # insert red into database
        # insert batches into database

        return jsonify({'Hello': 'World'})
