from flask import jsonify, request
from werkzeug.exceptions import Unauthorized
from bson.objectid import ObjectId


def nodes_routes(app, mongo, auth):
    @app.route('/nodes', methods=['GET'])
    def get_nodes():
        user = auth.verify_user(request.authorization)
        if not user:
            raise Unauthorized()

        cursor = mongo.db['nodes'].find()
        result = []

        for n in cursor:
            del n['_id']

            batches_cursor = mongo.db['batches'].find(
                {'node': n['nodeName']},
                {'experiementId': 1}
            )
            batches = list(batches_cursor)

            experiment_ids = list(set([ObjectId(b['experimentId']) for b in batches]))
            experiments_cursor = mongo.db['experiments'].find(
                {'_id': {'$in': experiment_ids}},
                {'container.settings.ram': 1}
            )

            experiments = {str(e['_id']): e for e in experiments_cursor}

            batches_ram = [
                {
                    'batch_id': str(b['_id']),
                    'ram': experiments[b['experimentId']]['container']['settings']['ram']
                }
                for b in batches
            ]

            n['batches'] = batches_ram

            result.append(n)

        return jsonify(result)
