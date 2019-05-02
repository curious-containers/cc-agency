import os
import sys
from threading import Thread
from queue import Queue, Full
from time import time, sleep
from traceback import format_exc

import requests
from bson.objectid import ObjectId

from cc_core.commons.gpu_info import GPUDevice, match_gpus, get_gpu_requirements, InsufficientGPUError
from cc_core.commons.red import red_get_mount_connectors_from_inputs, red_get_mount_connectors_from_outputs

from cc_agency.controller.docker import ClientProxy
from cc_agency.commons.helper import calculate_agency_id, batch_failure
from cc_agency.commons.build_dir import init_build_dir
from cc_agency.commons.secrets import get_experiment_secret_keys, fill_experiment_secrets
from cc_agency.commons.secrets import get_batch_secret_keys


_CRON_INTERVAL = 60


class Scheduler:
    def __init__(self, conf, mongo, trustee_client):
        self._conf = conf
        self._mongo = mongo
        self._trustee_client = trustee_client

        self._agency_id = calculate_agency_id(conf)

        mongo.db['nodes'].drop()

        self._scheduling_q = Queue(maxsize=1)
        self._inspection_q = Queue(maxsize=1)
        self._voiding_q = Queue(maxsize=1)
        self._notification_q = Queue(maxsize=1)

        init_build_dir(conf)

        self._nodes = {
            node_name: ClientProxy(node_name, conf, mongo)
            for node_name
            in conf.d['controller']['docker']['nodes'].keys()
        }

        Thread(target=self._scheduling_loop).start()
        Thread(target=self._inspection_loop).start()
        Thread(target=self._voiding_loop).start()
        Thread(target=self._notification_loop).start()
        Thread(target=self._cron).start()

    def _cron(self):
        while True:
            batch = self._mongo.db['batches'].find_one(
                {'$or': [
                    {'state': {'$nin': ['succeeded', 'failed', 'cancelled']}},
                    {'protectedKeysVoided': False},
                    {'notificationsSent': False}
                ]},
                {'_id': 1}
            )
            if batch:
                self.schedule()

            sleep(_CRON_INTERVAL)

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

    def _notification_loop(self):
        while True:
            self._notification_q.get()

            # batches
            cursor = self._mongo.db['batches'].find(
                {
                    'state': {'$in': ['succeeded', 'failed', 'cancelled']},
                    'notificationsSent': False
                },
                {'state': 1}
            )

            bson_ids = []
            payload = {'batches': []}

            for batch in cursor:
                bson_id = batch['_id']
                bson_ids.append(bson_id)

                payload['batches'].append({
                    'batchId': str(bson_id),
                    'state': batch['state']
                })

            self._mongo.db['batches'].update(
                {'_id': {'$in': bson_ids}},
                {'$set': {'notificationsSent': True}}
            )

            notification_hooks = self._conf.d['controller'].get('notification_hooks', [])

            for hook in notification_hooks:
                auth = hook.get('auth')

                if auth is not None:
                    auth = (auth['username'], auth['password'])

                try:
                    r = requests.post(hook['url'], auth=auth, json=payload)
                    r.raise_for_status()
                except Exception as e:
                    debug_info = 'Notification post hook failed:{0}{1}{0}{2}'.format(os.linesep, repr(e), e)
                    print(debug_info, file=sys.stderr)

    def _voiding_loop(self):
        while True:
            self._voiding_q.get()

            # batches
            cursor = self._mongo.db['batches'].find(
                {
                    'state': {'$in': ['succeeded', 'failed', 'cancelled']},
                    'protectedKeysVoided': False
                }
            )

            for batch in cursor:
                bson_id = batch['_id']

                batch_secret_keys = get_batch_secret_keys(batch)
                self._trustee_client.delete(batch_secret_keys)

                self._mongo.db['batches'].update_one({'_id': bson_id}, {'$set': {'protectedKeysVoided': True}})

            # experiments
            cursor = self._mongo.db['experiments'].find(
                {
                    'protectedKeysVoided': False
                }
            )

            for experiment in cursor:
                bson_id = experiment['_id']
                experiment_id = str(bson_id)

                all_count = self._mongo.db['batches'].count({'experimentId': experiment_id})

                finished_count = self._mongo.db['batches'].count({
                    'experimentId': experiment_id,
                    'state': {'$in': ['succeeded', 'failed', 'cancelled']}
                })

                if all_count == finished_count:
                    experiment_secret_keys = get_experiment_secret_keys(experiment)
                    self._trustee_client.delete(experiment_secret_keys)

                    self._mongo.db['experiments'].update_one({'_id': bson_id}, {'$set': {'protectedKeysVoided': True}})

    def _scheduling_loop(self):
        while True:
            self._scheduling_q.get()

            # inspect offline nodes
            try:
                self._inspection_q.put_nowait(None)
            except Full:
                pass

            # void protected keys
            try:
                self._voiding_q.put_nowait(None)
            except Full:
                pass

            # send notifications
            try:
                self._notification_q.put_nowait(None)
            except Full:
                pass

            # inspect trustee
            response = self._trustee_client.inspect()
            if response['state'] == 'failed':
                debug_info = response['debug_info']
                print('Trustee service unavailable, retry in {} seconds:{}{}'.format(
                    _CRON_INTERVAL, os.linesep, debug_info
                ), file=sys.stderr)
                sleep(_CRON_INTERVAL)
                continue

            self._schedule_batches()

    @staticmethod
    def _get_busy_gpu_ids(batches, node_name):
        """
        Returns a list of busy GPUs in the given batches

        :param batches: The batches to analyse given as list of dictionaries.
                        If GPUs are busy by a current batch the key 'usedGPUs' should be present.
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
        Available in this context means, that this device is present on the node and is not busy with another batch.

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
            {
                'node': {'$in': node_names},
                'state': {'$in': ['scheduled', 'processing']}},
            {'experimentId': 1, 'node': 1, 'usedGPUs': 1}
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

            experiment_secret_keys = get_experiment_secret_keys(experiment)
            response = self._trustee_client.collect(experiment_secret_keys)
            if response['state'] == 'failed':
                batch_failure(
                    self._mongo,
                    batch_id,
                    response['debug_info'],
                    None,
                    self._conf,
                    disable_retry_if_failed=response.get('disable_retry')
                )

                if response.get('inspect'):
                    response = self._trustee_client.inspect()
                    if response['state'] == 'failed':
                        debug_info = response['debug_info']
                        print('Trustee service unavailable:{}{}'.format(os.linesep, debug_info))
                        break

                continue

            experiment_secrets = response['secrets']
            experiment = fill_experiment_secrets(experiment, experiment_secrets)

            ram = experiment['container']['settings']['ram']

            # limit the number of currently executed batches from a single experiment
            concurrency_limit = experiment.get('execution', {}).get('settings', {}).get('batchConcurrencyLimit', 64)
            batch_count = self._mongo.db['batches'].count({
                'experimentId': experiment_id,
                'state': {'$in': ['scheduled', 'processing']}
            })
            
            if batch_count >= concurrency_limit:
                continue

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

            # check mounting
            mount_connectors = red_get_mount_connectors_from_inputs(batch['inputs'])
            batch_outputs = batch.get('outputs')
            if batch_outputs:
                mount_connectors.extend(red_get_mount_connectors_from_outputs(batch_outputs))

            is_mounting = bool(mount_connectors)

            allow_insecure_capabilities = self._conf.d['controller']['docker'].get('allow_insecure_capabilities', False)

            if not allow_insecure_capabilities and is_mounting:
                # set state to failed, because insecure_capabilities are not allowed but needed, by this batch.
                debug_info = 'FUSE support for this agency is disabled, but the following input/output-keys are ' \
                             'configured to mount inside a docker container.{}{}' \
                    .format(os.linesep, mount_connectors)
                batch_failure(
                    self._mongo,
                    batch_id,
                    debug_info,
                    None,
                    self._conf,
                    disable_retry_if_failed=True
                )
                continue

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
                        'state': 'scheduled',
                        'node': selected_node['nodeName'],
                        'usedGPUs': used_gpu_ids,
                        'mount': is_mounting
                    },
                    '$push': {
                        'history': {
                            'state': 'scheduled',
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
            client_proxy.put_action({'action': 'clean_up'})

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
            {'$project': {'experimentId': 1, 'inputs': 1, 'outputs': 1}}
        ])
        for b in cursor:
            yield b
