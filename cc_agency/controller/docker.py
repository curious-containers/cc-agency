import docker
from queue import Queue
from threading import Thread


class InspectionError(Exception):
    pass


class ClientProxy:
    def __init__(self, node_name, conf, db, logger):
        self._node_name = node_name
        self._conf = conf
        self._db = db
        self._logger = logger

        api_timeout = conf.d['controller']['docker']['api_timeout']
        node_conf = conf.d['controller']['docker']['nodes'][node_name]

        tls = False
        if 'tls' in node_conf:
            tls = docker.tls.TLSConfig(**node_conf['tls'])

        self.client = docker.APIClient(
            base_url=node_conf['base_url'],
            tls=tls,
            timeout=api_timeout,
            version='auto'
        )

        self._action_q = Queue()
        self._block_actions_q = Queue(maxsize=1)

        Thread(target=self._pull).start()

    def _pull(self):
        while True:
            try:
                # if a block signal is queued, no exception is raised
                _ = self._block_actions_q.get_nowait()
                self._logger.push(['Node {} got blocked for inspection.'.format(self._node_name)])
                # if no exception got raised, wait for unblock signal
                _ = self._block_actions_q.get()
                self._logger.push(['Node {} got unblocked.'.format(self._node_name)])
            except:
                pass

            data = self._action_q.get()
            action = data['action']

            if action == 'run_container':
                self._run_container(
                    container_id=data['container_id']
                )
            elif action == 'remove_container':
                self._remove_container(
                    container_id=data['container_id']
                )
            elif action == 'update_image':
                self._update_image(
                    image_url=data['image_url'],
                    registry_auth=data.get('registry_auth')
                )

    def _run_container(self, container_id):
        pass

    def _remove_container(self, container_id):
        pass

    def _wait_for_container(self, container_id):
        pass

    def _containers(self):
        pass

    def _update_image(self, image_url, registry_auth):
        pass

    def _inspect(self):
        self._logger.push({'lines': [
            'Inspect node {}.'.format(self._node_name)
        ]})

        image_url = self._conf.d['controller']['docker']['core_image']['image_url']

        self._update_image(
            image_url,
            self._conf.d['controller']['docker']['core_image'].get('registry_auth')
        )

        command = 'ccagent connected {} --inspect'.format(
            self._conf.d['broker']['external_url'].rstrip('/')
        )

        container_id = None

        self._run_container(
            container_id=container_id
        )

        self._wait_for_container(container_id)

        for key, val in self._containers().items():
            if key == container_id:
                if val['exit_status'] != 0:
                    s = 'Inspection container on node {} exited with code {}: {}'.format(
                        self._node_name, val['exit_status'], val['description']
                    )
                    raise InspectionError(s)
                break

        self._remove_container(container_id)
