import jsonschema
from traceback import format_exc
from time import time

from flask import jsonify, request
from werkzeug.exceptions import Unauthorized, BadRequest, NotFound
from bson.objectid import ObjectId

from cc_core.commons.schemas.red import red_schema
from cc_core.commons.engines import engine_validation

from cc_agency.commons.helper import str_to_bool


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

        for key, val in data['execution']['settings'].items():
            if key == 'access':
                continue

            stripped_settings[key] = val

        experiment['execution'] = {
            'engine': data['execution']['engine'],
            'settings': stripped_settings
        }

    if 'batches' in data:
        raw_batches = data['batches']
    else:
        raw_batches = [{
            'inputs': data['inputs'],
            'outputs': data['outputs']
        }]

    batches = []

    for rb in raw_batches:
        batches.append({
            'username': user['username'],
            'registrationTime': timestamp,
            'state': 'registered',
            'protectedKeysVoided': False,
            'node': None,
            'history': [{
                'state': 'registered',
                'time': timestamp,
                'debugInfo': None,
                'node': None,
                'ccagent': None
            }],
            'attempts': 0,
            'inputs': rb['inputs'],
            'outputs': rb['outputs']
        })

    return experiment, batches


def red_routes(app, mongo, auth, controller):
    @app.route('/red', methods=['POST'])
    def post_red():
        user = auth.verify_user(request.authorization)
        if not user:
            raise Unauthorized()

        if not request.json:
            raise BadRequest('Did not send RED data as JSON.')

        data = request.json

        try:
            jsonschema.validate(data, red_schema)
        except Exception:
            raise BadRequest('Given RED data does not comply with jsonschema. '
                             'Consider using the FAICE commandline tools for local validation.')

        if 'batches' in data:
            for batch in data['batches']:
                if 'outputs' not in batch:
                    raise BadRequest(
                        'CC-Agency requires all batches to have outputs defined. At least one batch does not comply.'
                    )

        elif 'outputs' not in data:
            raise BadRequest('CC-Agency requires outputs to be defined in RED data.')

        try:
            engine_validation(data, 'container', ['docker'])
        except Exception:
            raise BadRequest(format_exc())

        if 'ram' not in data['container']['settings']:
            raise BadRequest('CC-Agency requires ram to the be defined in container settings.')

        try:
            engine_validation(data, 'execution', ['ccagency'], optional=True)
        except Exception:
            raise BadRequest(format_exc())

        experiment, batches = _prepare_red_data(data, user)

        bson_experiment_id = mongo.db['experiments'].insert_one(experiment).inserted_id
        experiment_id = str(bson_experiment_id)

        for batch in batches:
            batch['experimentId'] = experiment_id

        mongo.db['batches'].insert_many(batches)

        controller.send_json({'destination': 'scheduler'})

        return jsonify({'experimentId': experiment_id})

    @app.route('/batches/<object_id>', methods=['DELETE'])
    def delete_batches(object_id):
        user = auth.verify_user(request.authorization)
        if not user:
            raise Unauthorized()

        try:
            bson_id = ObjectId(object_id)
        except Exception:
            raise BadRequest('Not a valid BSON ObjectId.')

        match = {'_id': bson_id}
        match_with_state = {'_id': bson_id, 'state': {'$nin': ['succeeded', 'failed', 'cancelled']}}

        if not user['is_admin']:
            match['username'] = user['username']
            match_with_state['username'] = user['username']

        o = mongo.db['batches'].find_one(match, {'state': 1})
        if not o:
            raise NotFound('Could not find Object.')

        mongo.db['batches'].update_one(match_with_state, {'$set': {'state': 'cancelled'}})

        o = mongo.db['batches'].find_one(match)
        o['_id'] = str(o['_id'])

        controller.send_json({'destination': 'scheduler'})

        return jsonify(o)

    @app.route('/experiments/count', methods=['GET'])
    def get_experiments_count():
        return get_collection_count('experiments')

    @app.route('/experiments', methods=['GET'])
    def get_experiments():
        return get_collection('experiments')

    @app.route('/experiments/<object_id>', methods=['GET'])
    def get_experiments_id(object_id):
        return get_collection_id('experiments', object_id)

    @app.route('/batches/count', methods=['GET'])
    def get_batches_count():
        return get_collection_count('batches')

    @app.route('/batches', methods=['GET'])
    def get_batches():
        return get_collection('batches')

    @app.route('/batches/<object_id>', methods=['GET'])
    def get_batches_id(object_id):
        return get_collection_id('batches', object_id)

    def get_collection_id(collection, object_id):
        user = auth.verify_user(request.authorization)
        if not user:
            raise Unauthorized()

        try:
            bson_id = ObjectId(object_id)
        except Exception:
            raise BadRequest('Not a valid BSON ObjectId.')

        match = {'_id': bson_id}

        if not user['is_admin']:
            match['username'] = user['username']

        o = mongo.db[collection].find_one(match)
        if not o:
            raise NotFound('Could not find Object.')

        o['_id'] = str(o['_id'])
        return jsonify(o)

    def get_collection_count(collection):
        user = auth.verify_user(request.authorization)
        if not user:
            raise Unauthorized()

        username = request.args.get('username', default=None, type=str)
        node = None
        experiment_id = None
        state = None

        if collection == 'batches':
            node = request.args.get('node', default=None, type=str)
            state = request.args.get('state', default=None, type=str)
            experiment_id = request.args.get('experimentId', default=None, type=str)

            if experiment_id:
                try:
                    _ = ObjectId(experiment_id)
                except Exception:
                    raise BadRequest('Experiment is not a valid BSON ObjectId.')

        aggregate = []

        if not user['is_admin']:
            aggregate.append({'$match': {'username': user['username']}})

        match = {}

        if username:
            match['username'] = username

        if node:
            match['node'] = node

        if experiment_id:
            match['experimentId'] = experiment_id

        if state:
            match['state'] = state

        aggregate.append({'$match': match})
        aggregate.append({'$count': 'count'})

        cursor = mongo.db[collection].aggregate(aggregate)

        return jsonify(list(cursor)[0])

    def get_collection(collection):
        user = auth.verify_user(request.authorization)
        if not user:
            raise Unauthorized()

        skip = request.args.get('skip', default=None, type=int)
        limit = request.args.get('limit', default=None, type=int)
        username = request.args.get('username', default=None, type=str)
        ascending = str_to_bool(request.args.get('ascending', default=None, type=str))

        node = None
        experiment_id = None
        state = None

        if collection == 'batches':
            node = request.args.get('node', default=None, type=str)
            state = request.args.get('state', default=None, type=str)

            states = ['registered', 'processing', 'succeeded', 'failed', 'cancelled']
            if state and state not in states:
                raise BadRequest('Given state is not valid. Must be one of {}.'.format(states))

            experiment_id = request.args.get('experimentId', default=None, type=str)

            if experiment_id:
                try:
                    _ = ObjectId(experiment_id)
                except Exception:
                    raise BadRequest('Experiment is not a valid BSON ObjectId.')

        aggregate = []

        if not user['is_admin']:
            aggregate.append({'$match': {'username': user['username']}})

        match = {}

        if username:
            match['username'] = username

        if node:
            match['node'] = node

        if experiment_id:
            match['experimentId'] = experiment_id

        if state:
            match['state'] = state

        aggregate.append({'$match': match})

        aggregate.append({'$project': {
            'username': 1,
            'registrationTime': 1,
            'state': 1,
            'experimentId': 1,
            'node': 1
        }})

        aggregate.append({'$sort': {'registrationTime': 1 if ascending else -1}})

        if skip is not None:
            if skip < 0:
                raise BadRequest('skip cannot be lower than 0.')
            aggregate.append({'$skip': skip})

        if limit is not None:
            if limit < 1:
                raise BadRequest('limit cannot be lower than 1.')
            aggregate.append({'$limit': limit})

        cursor = mongo.db[collection].aggregate(aggregate)

        result = []
        for e in cursor:
            e['_id'] = str(e['_id'])
            result.append(e)

        return jsonify(result)
