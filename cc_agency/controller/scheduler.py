from threading import Thread
from queue import Queue
from time import time, sleep

from bson.objectid import ObjectId

from cc_agency.controller.docker import ClientProxy


class Scheduler:
    def __init__(self, conf, mongo):
        self._conf = conf
        self._mongo = mongo

        mongo.db['nodes'].drop()

        self._scheduling_q = Queue(maxsize=1)
        self._inspection_q = Queue(maxsize=1)
        self._voiding_q = Queue(maxsize=1)

        self._nodes = {
            node_name: ClientProxy(node_name, conf, mongo)
            for node_name
            in conf.d['controller']['docker']['nodes'].keys()
        }

        Thread(target=self._scheduling_loop).start()
        Thread(target=self._inspection_loop).start()
        Thread(target=self._voiding_loop).start()
        Thread(target=self._cron).start()

    def _cron(self):
        while True:
            batch = self._mongo.db['batches'].find_one(
                {'state': {'$nin': ['succeeded', 'failed', 'cancelled']}},
                {'_id': 1}
            )
            if batch:
                self.schedule()

            sleep(60)

    def schedule(self):
        try:
            self._scheduling_q.put_nowait(None)
        except:
            pass

    def _inspection_loop(self):
        while True:
            self._inspection_q.get()
            print('inspecting...')

            cursor = self._mongo.db['nodes'].find(
                {'state': 'offline'},
                {'nodeName': 1, 'state': 1}
            )

            threads = []

            for node in cursor:
                node_name = node['nodeName']
                client_proxy = self._nodes[node_name]
                t = Thread(client_proxy.inspect_offline_node)
                t.start()
                threads.append(t)

            for t in threads:
                t.join()

    @staticmethod
    def _void_recursively(data, protected_keys, do_void, access):
        if isinstance(data, dict):
            result = {}
            for key, val in data.items():
                if access and (key == 'password' or key in protected_keys):
                    result[key] = Scheduler._void_recursively(val, protected_keys, True, access)
                elif key == 'access':
                    result[key] = Scheduler._void_recursively(val, protected_keys, do_void, True)
                else:
                    result[key] = Scheduler._void_recursively(val, protected_keys, do_void, access)

            return result
        elif isinstance(data, list):
            return [Scheduler._void_recursively(e, protected_keys, do_void, access) for e in data]
        elif do_void:
            return 'VOID'
        return data

    def _voiding_loop(self):
        while True:
            self._voiding_q.get()
            print('voiding...')

            cursor = self._mongo.db['batches'].find(
                {
                    'state': {'$in': ['succeeded', 'failed', 'cancelled']},
                    'protectedKeysVoided': False
                }
            )

            for batch in cursor:
                bson_id = batch['_id']
                del batch['_id']

                experiment_id = batch['experimentId']
                experiment = self._mongo.db['experiments'].find_one(
                    {'_id': ObjectId(experiment_id)},
                    {'execution.settings.protectedKeys': 1}
                )

                protected_keys = []
                if 'execution' in experiment:
                    settings = experiment['execution']['settings']
                    if 'protectedKeys' in settings:
                        protected_keys = settings['protectedKeys']

                data = Scheduler._void_recursively(batch, protected_keys, False, False)
                data['protectedKeysVoided'] = True
                self._mongo.db['batches'].update_one({'_id': bson_id}, {'$set': data})

    def _scheduling_loop(self):
        while True:
            self._scheduling_q.get()
            print('scheduling...')

            # inspect offline nodes
            try:
                self._inspection_q.put_nowait(None)
            except:
                pass

            # void protected keys
            try:
                self._voiding_q.put_nowait(None)
            except:
                pass

            # schedule
            self._schedule_batches()

            # clean up online nodes
            cursor = self._mongo.db['nodes'].find(
                {'state': 'online'},
                {'nodeName': 1}
            )

            for node in cursor:
                node_name = node['nodeName']
                client_proxy = self._nodes[node_name]
                client_proxy.put_action({'action': 'clean_up'})

    def _online_nodes(self):
        cursor = self._mongo.db['nodes'].find(
            {'state': 'online'},
            {'ram': 1, 'nodeName': 1}
        )

        nodes = list(cursor)
        node_names = [node['nodeName'] for node in nodes]

        cursor = self._mongo.db['batches'].find(
            {'node': {'$in': node_names}, 'state': 'processing'},
            {'experimentId': 1, 'node': 1}
        )
        batches = list(cursor)
        experiment_ids = list(set([ObjectId(b['experimentId']) for b in batches]))

        cursor = self._mongo.db['experiments'].find(
            {'_id': {'$in': experiment_ids}},
            {'container.settings.ram': 1}
        )
        experiments = {str(e['_id']): e for e in cursor}

        for node in nodes:
            used_ram = sum([
                experiments[b['experimentId']]['container']['settings']['ram']
                for b in batches
                if b['node'] == node['nodeName']
            ])

            node['freeRam'] = node['ram'] - used_ram
            node['scheduledImages'] = {}
            node['scheduledBatches'] = []

        return nodes

    def _schedule_batches(self):
        nodes = self._online_nodes()
        strategy = self._conf.d['controller']['scheduling']['strategy']
        timestamp = time()

        experiments = {}

        # select batch to be scheduled
        for batch in self._fifo():
            batch_id = str(batch['_id'])
            experiment_id = batch['experimentId']

            experiment = experiments.get(experiment_id)

            if not experiment:
                experiment = self._mongo.db['experiments'].find_one(
                    {'_id': ObjectId(experiment_id)},
                    {'container.settings': 1, 'execution.settings': 1}
                )
                experiments[experiment_id] = experiment

            ram = experiment['container']['settings']['ram']

            # select node
            possible_nodes = [node for node in nodes if node['freeRam'] >= ram]

            if len(possible_nodes) == 0:
                continue

            if strategy == 'spread':
                possible_nodes.sort(reverse=True, key=lambda n: n['freeRam'])
            elif strategy == 'binpack':
                possible_nodes.sort(reverse=False, key=lambda n: n['freeRam'])

            selected_node = possible_nodes[0]
            selected_node['freeRam'] -= ram

            # schedule image pull on selected node
            disable_pull = False
            if 'execution' in experiment:
                disable_pull = experiment['execution']['settings'].get('disablePull', False)

            if not disable_pull:
                image_data = [experiment['container']['settings']['image']['url']]
                auth = experiment['container']['settings']['image'].get('auth')
                if auth:
                    image_data += [auth['username'], auth['password']]
                image_data = tuple(image_data)

                if image_data not in selected_node['scheduledImages']:
                    selected_node['scheduledImages'][image_data] = []

                selected_node['scheduledImages'][image_data].append(batch_id)

            # schedule batch on selected node
            selected_node['scheduledBatches'].append(batch)

            # update batch data
            self._mongo.db['batches'].update_one(
                {'_id': batch['_id']},
                {
                    '$set': {
                        'state': 'processing',
                        'node': selected_node['nodeName']
                    },
                    '$push': {
                        'history': {
                            'state': 'processing',
                            'time': timestamp,
                            'debugInfo': None,
                            'node': selected_node['nodeName'],
                            'ccagent': None
                        }
                    },
                    '$inc': {
                        'attempts': 1
                    }
                }
            )

        # inform node ClientProxies
        for node in nodes:
            node_name = node['nodeName']
            client_proxy = self._nodes[node_name]

            for image, required_by in node['scheduledImages'].items():
                data = {
                    'action': 'pull_image',
                    'url': image[0],
                    'required_by': required_by
                }

                if len(image) == 3:
                    data['auth'] = {
                        'username': image[1],
                        'password': image[2]
                    }

                client_proxy.put_action(data)

            for batch in node['scheduledBatches']:
                batch_id = str(batch['_id'])

                data = {
                    'action': 'run_batch_container',
                    'batch_id': batch_id
                }

                client_proxy.put_action(data)

    def _fifo(self):
        cursor = self._mongo.db['batches'].aggregate([
            {'$match': {'state': 'registered'}},
            {'$sort': {'registrationTime': 1}},
            {'$project': {'experimentId': 1}}
        ])
        for b in cursor:
            yield b
