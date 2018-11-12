import sys
from threading import Thread
from queue import Queue
from time import time, sleep

from bson.objectid import ObjectId
from binascii import hexlify
from traceback import format_exc

from cc_agency.controller.docker import ClientProxy
from cc_agency.commons.helper import create_kdf
from cc_core.commons.gpu_info import GPUDevice, match_gpus, get_gpu_requirements, InsufficientGPUError


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

            cursor = self._mongo.db['nodes'].find(
                {'state': 'offline'},
                {'nodeName': 1, 'state': 1}
            )

            threads = []

            for node in cursor:
                node_name = node['nodeName']
                client_proxy = self._nodes[node_name]
                t = Thread(target=client_proxy.inspect_offline_node)
                t.start()
                threads.append(t)

            for t in threads:
                t.join()

    @staticmethod
    def _void_recursively(data, allowed_section):
        if isinstance(data, dict):
            result = {}
            for key, val in data.items():
                if allowed_section and (key == 'password' or key.startswith('_')):
                    kdf = create_kdf('fixedsalt'.encode('utf-8'))
                    val_hash = kdf.derive(val.encode('utf-8'))
                    val_hash = hexlify(val_hash).decode('utf-8')
                    result[key] = '{{' + val_hash[-8:] + '}}'
                elif key in ['access', 'auth']:
                    result[key] = Scheduler._void_recursively(val, True)
                else:
                    result[key] = Scheduler._void_recursively(val, allowed_section)

            return result
        elif isinstance(data, list):
            return [Scheduler._void_recursively(val, allowed_section) for val in data]

        return data

    def _voiding_loop(self):
        while True:
            self._voiding_q.get()

            try:
                # batches
                cursor = self._mongo.db['batches'].find(
                    {
                        'state': {'$in': ['succeeded', 'failed', 'cancelled']},
                        'protectedKeysVoided': False
                    }
                )

                for batch in cursor:
                    bson_id = batch['_id']
                    del batch['_id']

                    data = Scheduler._void_recursively(batch, False)
                    data['protectedKeysVoided'] = True
                    self._mongo.db['batches'].update_one({'_id': bson_id}, {'$set': data})

                # experiments
                cursor = self._mongo.db['experiments'].find(
                    {
                        'protectedKeysVoided': False
                    }
                )

                for experiment in cursor:
                    bson_id = experiment['_id']
                    experiment_id = str(bson_id)

                    cursor = self._mongo.db['batches'].aggregate([
                        {'$match': {'experimentId': experiment_id}},
                        {'$count': 'count'}
                    ])
                    all_count = list(cursor)[0]['count']

                    cursor = self._mongo.db['batches'].aggregate([
                        {
                            '$match': {
                                'experimentId': experiment_id,
                                'state': {'$in': ['succeeded', 'failed', 'cancelled']}
                            }
                        },
                        {'$count': 'count'}
                    ])
                    cursor = list(cursor)
                    if cursor:
                        finished_count = cursor[0]['count']

                        if all_count == finished_count:
                            data = Scheduler._void_recursively(experiment, False)
                            data['protectedKeysVoided'] = True
                            self._mongo.db['experiments'].update_one({'_id': bson_id}, {'$set': data})
            except Exception:
                print(format_exc(), file=sys.stderr)

    def _scheduling_loop(self):
        while True:
            self._scheduling_q.get()

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

    @staticmethod
    def _get_busy_gpu_ids(batches, node_name):
        """
        Returns a list of busy GPUs in the given batches

        :param batches: The batches to analyse given as list of dictionaries.
                        If GPUs are busy by processing a batch the key 'usedGPUs' should be present.
                        The value of 'usedGPUs' has to be a list of busy device IDs.
        :return: A list of GPUDevice-IDs, which are used by the given batches on the given node
        """

        busy_gpus = []
        for b in batches:
            if b['node'] == node_name:
                batch_gpus = b.get('usedGPUs')
                if type(batch_gpus) == list:
                    busy_gpus.extend(batch_gpus)

        return busy_gpus

    def _get_present_gpus(self, node_name):
        """
        Returns a list of GPUDevices

        :param node_name: The name of the node
        :return: A list of GPUDevices, which are representing the GPU Devices present on the specified node
        """

        result = []

        gpus = self._conf.d['controller']['docker']['nodes'][node_name].get('hardware', {}).get('gpus')
        if gpus:
            for gpu in gpus:
                result.append(GPUDevice(device_id=gpu['id'], vram=gpu['vram']))

        return result

    def _get_available_gpus(self, node, batches):
        """
        Returns a list of available GPUs on the given node.
        Available in this context means, that this device is present on the node and is not busy processing another batch.

        :param node: The node whose available GPUs should be calculated
        :param batches: The batches currently running
        :return: A list of available GPUDevices of the specified node
        """

        node_name = node['nodeName']

        busy_gpu_ids = Scheduler._get_busy_gpu_ids(batches, node_name)
        present_gpus = self._get_present_gpus(node_name)

        return [gpu for gpu in present_gpus if gpu.device_id not in busy_gpu_ids]

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

            available_gpus = self._get_available_gpus(node, batches)
            if available_gpus:
                node['availableGPUs'] = available_gpus

            node['freeRam'] = node['ram'] - used_ram
            node['scheduledImages'] = {}
            node['scheduledBatches'] = []

        return nodes

    @staticmethod
    def _node_sufficient(node, experiment):
        """
        Returns True if the nodes hardware is sufficient for the experiment

        :param node: The node to test
        :param experiment: A dictionary containing hardware requirements for the experiment
        :return: True, if the nodes hardware is sufficient for the experiment, otherwise False
        """

        if node['freeRam'] < experiment['container']['settings']['ram']:
            return False

        # check gpus
        available_gpus = node.get('availableGPUs')
        required_gpus = get_gpu_requirements(experiment['container']['settings'].get('gpus'))

        try:
            match_gpus(available_gpus, required_gpus)
        except InsufficientGPUError:
            return False

        return True

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
            possible_nodes = [node for node in nodes if Scheduler._node_sufficient(node, experiment)]

            if len(possible_nodes) == 0:
                continue

            if strategy == 'spread':
                possible_nodes.sort(reverse=True, key=lambda n: n['freeRam'])
            elif strategy == 'binpack':
                possible_nodes.sort(reverse=False, key=lambda n: n['freeRam'])

            selected_node = possible_nodes[0]

            # calculate ram / gpus
            selected_node['freeRam'] -= ram

            used_gpu_ids = None
            if "availableGPUs" in selected_node:
                gpu_requirements = get_gpu_requirements(experiment['container']['settings'].get('gpus'))
                available_gpus = selected_node['availableGPUs']
                used_gpus = match_gpus(available_gpus, requirements=gpu_requirements)

                used_gpu_ids = []
                for gpu in used_gpus:
                    used_gpu_ids.append(gpu.device_id)
                    available_gpus.remove(gpu)

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
                        'node': selected_node['nodeName'],
                        'usedGPUs': used_gpu_ids
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
