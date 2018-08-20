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

        nodes = list(cursor)
        node_names = [node['nodeName'] for node in nodes]

        cursor = mongo.db['batches'].find(
            {'node': {'$in': node_names}, 'state': 'processing'},
            {'experimentId': 1, 'node': 1}
        )
        batches = list(cursor)
        experiment_ids = list(set([ObjectId(b['experimentId']) for b in batches]))

        cursor = mongo.db['experiments'].find(
            {'_id': {'$in': experiment_ids}},
            {'container.settings.ram': 1}
        )
        experiments = {str(e['_id']): e for e in cursor}

        for node in nodes:
            batches_ram = [
                {
                    'batchId': str(b['_id']),
                    'ram': experiments[b['experimentId']]['container']['settings']['ram']
                }
                for b in batches
                if b['node'] == node['nodeName']
            ]
            node['currentBatches'] = batches_ram
            del node['_id']

        return jsonify(nodes)
