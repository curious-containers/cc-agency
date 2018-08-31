from os import urandom
from queue import Queue
from threading import Thread
from time import time
from traceback import format_exc

import docker
from bson.objectid import ObjectId

from cc_agency.commons.helper import generate_secret, create_kdf, batch_failure


class ClientProxy:
    def __init__(self, node_name, conf, mongo):
        self._node_name = node_name
        self._conf = conf
        self._mongo = mongo

        node_conf = conf.d['controller']['docker']['nodes'][node_name]
        self._base_url = node_conf['base_url']
        self._tls = False
        if 'tls' in node_conf:
            self._tls = docker.tls.TLSConfig(**node_conf['tls'])
        self._tls = False
        self._external_url = conf.d['broker']['external_url'].rstrip('/')

        self._action_q = None
        self._client = None
        self._online = None

        node = {
            'nodeName': node_name,
            'state': None,
            'history': [],
            'ram': None,
            'cpus': None
        }

        bson_node_id = self._mongo.db['nodes'].insert_one(node).inserted_id
        self._node_id = str(bson_node_id)

        try:
            self._client = docker.DockerClient(base_url=self._base_url, tls=self._tls, version='auto')
            ram, cpus = self._info()
        except Exception:
            self._set_offline(format_exc())
            return

        self._set_online(ram, cpus)
        self._action_q = Queue()
        Thread(target=self._action_loop).start()

    def _set_online(self, ram, cpus):
        print('Node online:', self._node_name)

        self._online = True
        bson_node_id = ObjectId(self._node_id)
        self._mongo.db['nodes'].update_one(
            {'_id': bson_node_id},
            {
                '$set': {
                    'state': 'online',
                    'ram': ram,
                    'cpus': cpus
                },
                '$push': {
                    'history': {
                        'state': 'online',
                        'time': time(),
                        'debugInfo': None
                    }
                }
            }
        )

    def _set_offline(self, debug_info):
        print('Node offline:', self._node_name)
        timestamp = time()

        self._online = False
        bson_node_id = ObjectId(self._node_id)
        self._mongo.db['nodes'].update_one(
            {'_id': bson_node_id},
            {
                '$set': {'state': 'offline'},
                '$push': {
                    'history': {
                        'state': 'offline',
                        'time': timestamp,
                        'debugInfo': debug_info
                    }
                }
            }
        )

        # change state of assigned batches
        cursor = self._mongo.db['batches'].find(
            {'node': self._node_name, 'state': 'processing'},
            {'_id': 1}
        )

        for batch in cursor:
            bson_id = batch['_id']
            batch_id = str(bson_id)
            debug_info = 'Node offline: {}'.format(self._node_name)
            batch_failure(self._mongo, batch_id, debug_info, None, self._conf)

    def _info(self):
        info = self._client.info()
        ram = info['MemTotal'] // (1024 * 1024)
        cpus = info['NCPU']
        return ram, cpus

    def _batch_containers(self, status):
        batch_containers = {}

        if not self._online:
            return batch_containers

        containers = self._client.containers.list(all=True, limit=-1, filters={'status': status})

        for c in containers:
            try:
                ObjectId(c.name)
                batch_containers[c.name] = c
            except:
                pass

        return batch_containers

    def _fail_processing_batches_without_running_container(self):
        running_containers = self._batch_containers('running')

        cursor = self._mongo.db['batches'].find(
            {'state': 'processing'},
            {'_id': 1}
        )

        for batch in cursor:
            bson_id = batch['_id']
            batch_id = str(bson_id)

            if batch_id not in running_containers:
                debug_info = 'No running container assigned.'
                batch_failure(self._mongo, batch_id, debug_info, None, self._conf)

    def _remove_cancelled_containers(self):
        running_containers = self._batch_containers('running')

        cursor = self._mongo.db['batches'].find(
            {
                '_id': {'$in': [ObjectId(_id) for _id in running_containers]},
                'state': 'cancelled'
            },
            {'_id': 1}
        )
        for batch in cursor:
            bson_id = batch['_id']
            batch_id = str(bson_id)

            c = running_containers[batch_id]
            c.remove(force=True)

    def _remove_exited_containers(self):
        exited_containers = self._batch_containers('exited')

        cursor = self._mongo.db['batches'].find(
            {'_id': {'$in': [ObjectId(_id) for _id in exited_containers]}},
            {'state': 1}
        )
        for batch in cursor:
            bson_id = batch['_id']
            batch_id = str(bson_id)

            c = exited_containers[batch_id]
            debug_info = c.logs().decode('utf-8')
            c.remove()

            if batch['state'] not in ['succeeded', 'failed', 'cancelled']:
                batch_failure(self._mongo, batch_id, debug_info, None, self._conf)

    def inspect_offline_node(self):
        try:
            self._client = docker.DockerClient(base_url=self._base_url, tls=self._tls, version='auto')
            ram, cpus = self._info()
            self._inspect()
        except Exception:
            return

        self._set_online(ram, cpus)
        self._action_q = Queue()
        Thread(target=self._action_loop).start()

    def _inspect(self):
        print('Node inspection:', self._node_name)

        core_image_conf = self._conf.d['controller']['docker']['core_image']
        image = core_image_conf['url']
        auth = core_image_conf.get('auth')
        command = 'ccagent connected {} --inspect'.format(self._external_url)
        disable_pull = self._conf.d['controller']['docker']['core_image'].get('disable_pull', False)

        if not disable_pull:
            self._client.images.pull(image, auth_config=auth)

        self._client.containers.run(
            image,
            command,
            user='1000:1000',
            remove=True
        )

    def put_action(self, data):
        self._action_q.put(data)

    def _action_loop(self):
        while True:
            data = self._action_q.get()

            if 'action' not in data:
                continue

            action = data['action']

            inspect = False

            if action == 'run_batch_container':
                try:
                    self._run_batch_container(batch_id=data['batch_id'])
                except Exception:
                    inspect = True
                    self._run_batch_container_failure(data['batch_id'], format_exc())

            elif action == 'pull_image':
                try:
                    self._pull_image(image=data['url'], auth=data.get('auth'))
                except:
                    inspect = True
                    batch_ids = data['required_by']
                    self._pull_image_failure(format_exc(), batch_ids)

            elif action == 'clean_up':
                try:
                    self._remove_cancelled_containers()
                    self._remove_exited_containers()
                    self._fail_processing_batches_without_running_container()
                except:
                    inspect = True

            if inspect:
                try:
                    self._inspect()
                except Exception:
                    self._set_offline(format_exc())
                    self._action_q = None
                    self._client = None
                    self._remove_cancelled_containers()
                    self._remove_exited_containers()
                    self._fail_processing_batches_without_running_container()

            if not self._online:
                return

    def _run_batch_container(self, batch_id):
        batch = self._mongo.db['batches'].find_one(
            {'_id': ObjectId(batch_id), 'state': 'processing'},
            {'experimentId': 1}
        )
        if not batch:
            print('Batch "{}" not found for processing.'.format(batch_id))
            return

        experiment_id = batch['experimentId']
        experiment = self._mongo.db['experiments'].find_one(
            {'_id': ObjectId(experiment_id)},
            {
                'container.settings.image.url': 1,
                'container.settings.ram': 1,
                'execution.settings.outdir': 1}
        )
        image = experiment['container']['settings']['image']['url']

        token = generate_secret()
        salt = urandom(16)
        kdf = create_kdf(salt)

        self._mongo.db['callback_tokens'].insert_one({
            'batch_id': batch_id,
            'salt': salt,
            'token': kdf.derive(token.encode('utf-8')),
            'timestamp': time()
        })

        command = 'ccagent connected {}/callback/{}/{}'.format(self._external_url, batch_id, token)

        if 'execution' in experiment:
            outdir = experiment['execution']['settings'].get('outdir')
            if outdir:
                command = '{} --outdir {}'.format(command, outdir)

        ram = experiment['container']['settings']['ram']
        mem_limit = '{}m'.format(ram)

        self._client.containers.run(
            image,
            command,
            name=batch_id,
            user='1000:1000',
            remove=False,
            detach=True,
            mem_limit=mem_limit,
            memswap_limit=mem_limit
        )

    def _run_batch_container_failure(self, batch_id, debug_info):
        batch_failure(self._mongo, batch_id, debug_info, None, self._conf)

    def _pull_image(self, image, auth):
        self._client.images.pull(image, auth_config=auth)

    def _pull_image_failure(self, debug_info, batch_ids):
        for batch_id in batch_ids:
            batch_failure(self._mongo, batch_id, debug_info, None, self._conf)

    def _remove_batch_container(self, batch_id):
        pass
