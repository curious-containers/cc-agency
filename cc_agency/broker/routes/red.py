import jsonschema
from traceback import format_exc
from time import time

from flask import jsonify, request
from werkzeug.exceptions import Unauthorized, BadRequest

from cc_core.commons.schemas.red import red_schema
from cc_core.commons.engines import engine_validation


def _prepare_red_data(data, user):
    timestamp = time()

    experiment = {
        'username': user['username'],
        'registrationTime': timestamp,
        'redVersion': data['redVersion'],
        'cli': data['cli'],
        'container': data['container']
    }

    if 'execution' in data:
        stripped_settings = {}

        for key, val in data['execution']['settings']:
            if key == 'access':
                continue

            stripped_settings[key] = val

        experiment['execution'] = {
            'engine': data['execution']['engine'],
            'settings': stripped_settings
        }

    if 'batches' in data:
        batches = data['batches']
    else:
        batches = [{
            'state': 'registered',
            'history': [{
                'state': 'registered',
                'time': timestamp,
                'debugInfo': None
            }],
            'attempts': 0,
            'inputs': data['inputs'],
            'outputs': data['outputs']
        }]

    return experiment, batches


def red_routes(app, mongo, auth, controller):
    @app.route('/red', methods=['POST'])
    def post_red():
        user = auth(request.auth)
        if not user:
            raise Unauthorized()

        try:
            data = request.json()
        except Exception:
            raise BadRequest('Did not send RED data as JSON.')

        try:
            jsonschema.validate(data, red_schema)
        except Exception:
            raise BadRequest('Given RED data does not comply with jsonschema. '
                             'Consider using the FAICE commandline tools for local validation.')

        if 'batches' in data:
            for batch in data['batches']:
                if 'outputs' not in batch:
                    raise BadRequest('At least one batch does not have outputs defined, but is required by CC-Agency.')

        elif 'outputs' not in data:
            raise BadRequest('CC-Agency requires outputs defined in RED data.')

        try:
            engine_validation(data, 'container', ['docker'])
        except Exception:
            raise BadRequest(format_exc())

        try:
            engine_validation(data, 'execution', ['ccagency'], optional=True)
        except Exception:
            raise BadRequest(format_exc())

        experiment, batches = _prepare_red_data(data, user)

        experiment_id = mongo.db['experiments'].insert_one(experiment).inserted_id
        experiment_id = str(experiment_id)

        for batch in batches:
            batch['experiment_id'] = experiment_id

        mongo.db['batches'].insert_many(batches)
        controller.send_json({'destination': 'scheduler'})

        return jsonify({'experiment_id': experiment_id})
