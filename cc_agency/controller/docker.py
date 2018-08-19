import docker
from queue import Queue
from threading import Thread
from time import time
from traceback import format_exc
from bson.objectid import ObjectId


class InspectionError(Exception):
    pass


class ClientProxyError(Exception):
    pass


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
            self._inspect()
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

        self._online = False
        bson_node_id = ObjectId(self._node_id)
        self._mongo.db['nodes'].update_one(
            {'_id': bson_node_id},
            {
                '$set': {'state': 'offline'},
                '$push': {
                    'history': {
                        'state': 'offline',
                        'time': time(),
                        'debugInfo': debug_info
                    }
                }
            }
        )

    def _info(self):
        info = self._client.info()
        ram = info['MemTotal'] // (1024 * 1024)
        cpus = info['NCPU']
        return ram, cpus

    def inspect_offline_node_async(self):
        if self._online:
            return

        Thread(target=self.inspect_offline_node).start()

    def inspect_offline_node(self):
        if self._online:
            return

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

    def _batch_failure(self, batch_id, debug_info):
        pass

    def _pull_image_failure(self, image, debug_info):
        pass

    def _action_loop(self):
        while True:
            data = self._action_q.get()

            if 'action' not in data:
                continue

            action = data['action']

            inspect = False

            if action == 'start_batch_container':
                try:
                    self._start_batch_container(batch_id=data['batch_id'])
                except Exception:
                    inspect = True
                    self._batch_failure(data['batch_id'], format_exc())

            elif action == 'remove_batch_container':
                try:
                    self._remove_batch_container(batch_id=data['batch_id'])
                except Exception:
                    pass

            elif action == 'pull_image':
                try:
                    self._pull_image(image=data['url'], auth=data.get('auth'))
                except:
                    inspect = True
                    self._pull_image_failure(data['url'], format_exc())

            if inspect:
                try:
                    self._inspect()
                except Exception:
                    self._set_offline(format_exc())
                    self._action_q = None
                    self._client = None

            if not self._online:
                return

    def _start_batch_container(self, batch_id):
        pass

    def _remove_batch_container(self, batch_id):
        pass

    def _pull_image(self, image, auth):
        pass
