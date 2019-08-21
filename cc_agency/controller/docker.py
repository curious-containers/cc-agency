import io
import json
import os
import tarfile
from threading import Thread, Event
import concurrent.futures
import time
from traceback import format_exc
from typing import List, Tuple, Dict

import docker
import jsonschema
from docker.models.containers import Container
from docker.tls import TLSConfig
from docker.types import Ulimit
import docker.errors
from bson.objectid import ObjectId
import bson.errors

from cc_agency.commons.schemas.callback import agent_result_schema
from cc_agency.commons.secrets import get_experiment_secret_keys, fill_experiment_secrets, fill_batch_secrets, \
    get_batch_secret_keys
from cc_core.commons.docker_utils import create_container_with_gpus
from cc_core.commons.engines import engine_to_runtime, NVIDIA_DOCKER_RUNTIME
from cc_core.commons.files import create_directory_tarinfo
from cc_core.commons.gpu_info import set_nvidia_environment_variables

from cc_agency.commons.helper import batch_failure
from cc_core.commons.red_to_blue import convert_red_to_blue, CONTAINER_OUTPUT_DIR, CONTAINER_AGENT_PATH, \
    CONTAINER_BLUE_FILE_PATH, CONTAINER_INPUT_DIR

INSPECTION_IMAGE = 'docker.io/busybox:latest'
NOFILE_LIMIT = 4096
CHECK_EXITED_CONTAINERS_INTERVAL = 1.0
OFFLINE_INSPECTION_INTERVAL = 10
CHECK_FOR_BATCHES_INTERVAL = 20


class ImagePullResult:
    def __init__(self, image_url, auth, successful, debug_info, depending_batches):
        """
        Creates a new DockerImagePull object.

        :param image_url: The url of the image to pull
        :type image_url: str
        :param auth: The authentication data for this image. Can be None if no authentication is required, otherwise it
                     has to be a tuple (username, password).
        :type auth: None or Tuple[str, str]
        :param successful: A boolean that is True, if the pull was successful
        :type successful: bool
        :param debug_info: A list of strings describing the error if the pull failed. Otherwise None.
        :type debug_info: List[str] or None
        :param depending_batches: A list of batches that depend on the execution of this docker pull
        :type depending_batches: List[Dict]
        """
        self.image_url = image_url
        self.auth = auth
        self.successful = successful
        self.debug_info = debug_info
        self.depending_batches = depending_batches


def _pull_image(docker_client, image_url, auth, depending_batches):
    """
    Pulls the given docker image and returns a ImagePullResult object.

    :param docker_client: The docker client, which is used to pull the image
    :type docker_client: docker.DockerClient
    :param image_url: The image to pull
    :type image_url: str
    :param auth: A tuple containing (username, password) or None
    :type auth: Tuple[str, str] or None
    :param depending_batches: A list of batches, which depend on the given image
    :type depending_batches: List[Dict]

    :return: An ImagePullResult describing the pull
    :rtype: ImagePullResult
    """
    try:
        docker_client.images.pull(image_url, auth_config=auth)
    except Exception as e:
        debug_info = str(e).split('\n')
        return ImagePullResult(image_url, auth, False, debug_info, depending_batches)

    return ImagePullResult(image_url, auth, True, None, depending_batches)


def _get_blue_agent_host_path():
    """
    :return: the absolute path to the blue agent of the host system
    :rtype: str
    """
    import cc_core.agent.blue.__main__ as blue_main
    return blue_main.__file__


class ClientProxy:
    """
    A client proxy handles a cluster node and the docker client of this node.
    It takes over the following tasks:
    - It queries the db for new batches, that got scheduled to this node to start them
    - It queries the docker client for containers, which are finished and updates the db accordingly
    - It queries the db to remove cancelled containers
    - If an error occurred it tries to reinitialize the docker client

    A client proxy contains a "online-flag" implemented as threading.Event. Any thread inside this client proxy except
    the inspection loop should wait for this event to be set, before starting a new execution cycle.
    Only the inspect-thread is allowed to set/clear the "online-flag" after successful/failed inspection.

    On error the check_for_batches and check_exited_containers-threads can trigger an inspection.

    inspect:
      If this ClientProxy is offline regularly inspects the connection to the docker daemon. If the inspection failed,
      the "online-flag" is cleared and this node is set to offline.

      If this ClientProxy is online inspect the docker engine, by starting a docker container and examine the result
      of this execution. If the execution was successful, set the "online-flag" and make this node online, otherwise
      repeat in some interval.

    check-for-batches:
      Regularly queries the database for batches, which are scheduled to this node. All found batches are then started
      with the docker client, if online. This can be triggered manually by setting the check-for-batches-event.
      If this client proxy is changed to be offline this thread processes the current cycle until it has finished and
      then waits for the "online-flag" to be set.

    check-exited-containers:
      Regularly queries the containers from the docker client and the database, which are currently running on this
      node. Checks the containers, which are not running anymore and handles their execution result. This can be
      triggered by setting the check_exited_containers-flag.
      If this client proxy is changed to be offline this thread processes the current cycle until it has finished and
      then waits for the "online-flag" to be set.
    """
    NUM_WORKERS = 4

    def __init__(self, node_name, conf, mongo, trustee_client, scheduling_event):
        self._node_name = node_name
        self._conf = conf
        self._mongo = mongo
        self._trustee_client = trustee_client

        self._scheduling_event = scheduling_event

        node_conf = conf.d['controller']['docker']['nodes'][node_name]
        self._base_url = node_conf['base_url']
        self._tls = False
        if 'tls' in node_conf:
            self._tls = TLSConfig(**node_conf['tls'])

        self._environment = node_conf.get('environment')
        self._network = node_conf.get('network')

        # create db entry for this node
        node = {
            'nodeName': node_name,
            'state': None,
            'history': [],
            'ram': None,
            'cpus': None
        }

        bson_node_id = self._mongo.db['nodes'].insert_one(node).inserted_id
        self._node_id = str(bson_node_id)

        # init docker client
        self._client = None
        self._runtimes = None
        self._online = Event()  # type: Event

        self._inspection_event = Event()  # type: Event
        self._check_for_batches_event = Event()  # type: Event
        self._check_exited_containers_event = Event()  # type: Event

        if not self._init_docker_client():
            self.do_inspect()
            self._set_offline(format_exc())

        Thread(target=self._inspection_loop).start()
        Thread(target=self._check_for_batches_loop).start()
        Thread(target=self._check_exited_containers_loop).start()

        # initialize Executor Pools
        self._pull_executor = concurrent.futures.ThreadPoolExecutor(max_workers=ClientProxy.NUM_WORKERS)
        self._run_executor = concurrent.futures.ThreadPoolExecutor(max_workers=ClientProxy.NUM_WORKERS)

    def is_online(self):
        return self._online.is_set()

    def _set_online(self, ram, cpus):
        print('Node online:', self._node_name)

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
                        'time': time.time(),
                        'debugInfo': None
                    }
                }
            }
        )

        self._online.set()  # start _check_batch_containers and _check_exited_containers

    def _set_offline(self, debug_info):
        print('Node offline:', self._node_name)

        self._online.clear()

        timestamp = time.time()
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
            {
                'node': self._node_name,
                'state': {'$in': ['scheduled', 'processing']}
            },
            {'_id': 1, 'state': 1}
        )

        for batch in cursor:
            bson_id = batch['_id']
            batch_id = str(bson_id)
            debug_info = 'Node offline: {}'.format(self._node_name)
            batch_failure(self._mongo, batch_id, debug_info, None, batch['state'])

    def _info(self):
        info = self._client.info()
        ram = info['MemTotal'] // (1024 * 1024)
        cpus = info['NCPU']
        runtimes = info['Runtimes']
        return ram, cpus, runtimes

    def _batch_containers(self, status):
        """
        Returns a dictionary that maps container names to the corresponding container.
        If this client proxy is offline, the result will always be an empty dictionary.

        :param status: A status string. Containers, which have a different state are not contained in the result of this
                       function
        :type status: str or None
        :return: A dictionary mapping container names to docker containers
        :rtype: Dict[str, Container]

        :raise docker.errors.DockerException: If the docker engine returns an error
        """
        batch_containers = {}  # type: Dict[str, Container]

        if not self.is_online():
            return batch_containers

        filters = {'status': status}
        if status is None:
            filters = None

        containers = self._client.containers.list(all=True, limit=-1, filters=filters)  # type: List[Container]

        for c in containers:
            try:
                ObjectId(c.name)
                batch_containers[c.name] = c
            except (bson.errors.InvalidId, TypeError):
                pass

        return batch_containers

    def _fail_batches_without_assigned_container(self):
        containers = self._batch_containers(None)

        cursor = self._mongo.db['batches'].find(
            {
                'node': self._node_name,
                'state': {'$in': ['processing']}
            },
            {'_id': 1, 'state': 1}
        )

        for batch in cursor:
            bson_id = batch['_id']
            batch_id = str(bson_id)

            if batch_id not in containers:
                debug_info = 'No container assigned.'
                batch_failure(self._mongo, batch_id, debug_info, None, batch['state'])

    def _remove_cancelled_containers(self):
        """
        Stops all docker containers, whose batches got cancelled.

        :raise docker.errors.DockerException: If the docker server returns an error
        """
        running_containers = self._batch_containers('running')

        cursor = self._mongo.db['batches'].find(
            {
                '_id': {'$in': [ObjectId(_id) for _id in running_containers]},
                'state': 'cancelled'
            },
            {'_id': 1}
        )
        resources_freed = False
        for batch in cursor:
            batch_id = str(batch['_id'])

            c = running_containers[batch_id]
            c.remove(force=True)
            resources_freed = True

        return resources_freed

    def _can_execute_container(self):
        """
        Tries to execute a docker container using the docker client.

        :return: A tuple (successful, info/error)
                 successful: True, if the docker container could be executed, otherwise False
                 info/error: In case of success a tuple containing (ram, cpus, runtimes),
                 in case of failure the error message message as string.
        :rtype: tuple[bool, tuple or str]
        """
        command = ['echo', 'test']

        try:
            self._client.containers.run(
                INSPECTION_IMAGE,
                command,
                user='1000:1000',
                remove=True,
                environment=self._environment,
                network=self._network
            )
            info = self._info()
        except docker.errors.DockerException as e:
            return False, str(e)

        return True, info

    def _inspect_on_error(self):
        """
        Inspects the current docker client and sets this node to offline, if the inspection fails.
        """
        success, state = self._can_execute_container()

        if not success:
            self._set_offline(state)

    def _init_docker_client(self):
        """
        Tries to reinitialize the docker client. If successful, this node is online after this function execution.

        :return: True, if the initialization succeeded, otherwise False
        :rtype: bool
        """
        init_succeeded = False
        try:
            self._client = docker.DockerClient(base_url=self._base_url, tls=self._tls, version='auto')

            successful, state = self._can_execute_container()
            if successful:
                ram, cpus, runtimes = state
                self._runtimes = runtimes
                if not self.is_online():
                    self._set_online(ram, cpus)
                    init_succeeded = True
        except docker.errors.DockerException:
            pass
        return init_succeeded

    def _inspection_loop(self):
        """
        Regularly inspects the connection the docker daemon by running a docker container. If an error was found, clears
        the "online-flag" and puts in a error token in the inspection queue.

        Waits for errors inside the inspection-queue. If an error token was found inside the inspection-queue, handles
        this error. Otherwise performs a routine check of the docker client, after a given timeout.
        If this client proxy is offline, tries to restart it.
        """
        while True:
            if self.is_online():
                self._inspection_event.wait()
                self._inspection_event.clear()
                self._inspect_on_error()
            else:
                self._inspection_event.wait(timeout=OFFLINE_INSPECTION_INTERVAL)
                self._inspection_event.clear()
                self._init_docker_client()  # tries to reinitialize the docker client

    def _check_exited_containers(self):
        """
        Checks all containers which have "recently exited". A container is considered "recently exited", if the docker
        container is in state 'exited' and the corresponding batch is in state 'processing'.

        After this function execution all "recently exited" containers of this docker client should have batches, which
        are in one of the following states:
        - succeeded: If the batch execution was successful
        - failed: If the batch execution has failed

        :return: True if a exited container was found, otherwise False
        :rtype: bool

        :raise docker.errors.DockerException:
        """
        exited_containers = self._batch_containers('exited')  # type: Dict[str, Container]

        batch_cursor = self._mongo.db['batches'].find(
            {'_id': {'$in': [ObjectId(_id) for _id in exited_containers]}},
            {'state': 1}
        )
        resources_freed = False
        for batch in batch_cursor:
            batch_id = str(batch['_id'])

            exited_container = exited_containers[batch_id]

            self._check_exited_container(exited_container, batch)

            exited_container.remove()

            resources_freed = True

        return resources_freed

    def _check_exited_containers_loop(self):
        """
        Regularly checks exited containers. Waits for this client proxy to come online, before starting a new cycle.
        Also removes containers, whose batches got cancelled.
        """
        while True:
            self._online.wait()  # wait for this node to come online

            self._check_exited_containers_event.wait(timeout=CHECK_EXITED_CONTAINERS_INTERVAL)
            self._check_exited_containers_event.clear()

            try:
                resources_freed = self._check_exited_containers()
                resources_freed = self._remove_cancelled_containers() or resources_freed

                if resources_freed:
                    self._scheduling_event.set()
            except docker.errors.DockerException:
                self.do_inspect()

    def _check_exited_container(self, container, batch):
        """
        Inspects the logs of the given exited container and updates the database accordingly.

        :param container: The container to inspect
        :type container: Container
        :param batch: The batch to update according to the result of the container execution.
        :type batch: dict
        """
        bson_batch_id = batch['_id']
        batch_id = str(bson_batch_id)

        try:
            stdout_logs = container.logs(stderr=False).decode('utf-8')
            stderr_logs = container.logs(stdout=False).decode('utf-8')
            docker_stats = container.stats(stream=False)
        except Exception as e:
            debug_info = 'Could not get logs or stats of container: {}'.format(str(e))
            batch_failure(self._mongo, batch_id, debug_info, None, batch['state'])
            return

        data = None
        try:
            data = json.loads(stdout_logs)
        except json.JSONDecodeError as e:
            debug_info = 'CC-Agent data is not a valid json object: {}\n\nstdout was:\n{}'.format(str(e), stdout_logs)
            batch_failure(self._mongo, batch_id, debug_info, data, batch['state'], docker_stats=docker_stats)
            return

        try:
            jsonschema.validate(data, agent_result_schema)
        except jsonschema.ValidationError as e:
            debug_info = 'CC-Agent data sent by callback does not comply with jsonschema: {}'.format(str(e))
            batch_failure(self._mongo, batch_id, debug_info, data, batch['state'], docker_stats=docker_stats)
            return

        if data['state'] == 'failed':
            debug_info = 'Batch failed.\nContainer stderr:\n{}\ndebug info:\n{}'.format(stderr_logs, data['debugInfo'])
            batch_failure(self._mongo, batch_id, debug_info, data, batch['state'], docker_stats=docker_stats)
            return

        batch = self._mongo.db['batches'].find_one(
            {'_id': bson_batch_id, 'state': 'processing'},
            {'attempts': 1, 'node': 1}
        )
        if not batch:
            raise ValueError('batch id "{}" not found.'.format(batch_id))

        self._mongo.db['batches'].update_one(
            {
                '_id': bson_batch_id,
                'state': 'processing'
            },
            {
                '$set': {
                    'state': 'succeeded'
                },
                '$push': {
                    'history': {
                        'state': 'succeeded',
                        'time': time.time(),
                        'debugInfo': None,
                        'node': batch['node'],
                        'ccagent': data,
                        'dockerStats': docker_stats
                    }
                }
            }
        )

    def do_check_for_batches(self):
        """
        Triggers a check-for-batches cycle.
        """
        self._check_for_batches_event.set()

    def do_check_exited_containers(self):
        """
        Triggers a check-exited-containers cycle.
        """
        self._check_exited_containers_event.set()

    def do_inspect(self):
        """
        Triggers an inspection cycle.
        """
        self._inspection_event.set()

    def _check_for_batches_loop(self):
        """
        Regularly calls _check_for_batches. Does wait before executing a new cycle, if this client proxy is offline.
        """
        while True:
            self._online.wait()

            self._check_for_batches_event.wait(timeout=CHECK_FOR_BATCHES_INTERVAL)
            self._check_for_batches_event.clear()

            try:
                self._check_for_batches()
            except TrusteeServiceError:
                self.do_inspect()

    def _check_for_batches(self):
        """
        Queries the database to find batches, which are in state 'scheduled' and are scheduled to the node of this
        ClientProxy.
        First all docker images are pulled, which are used to process these batches. Afterwards the batch processing is
        run. The state in the database for these batches is then updated to 'processing'.

        :raise TrusteeServiceError: If the trustee service is unavailable or the trustee service could not fulfill all
        requested keys
        :raise ImageAuthenticationError: If the image authentication information is invalid.
        """

        # query for batches, that are in state 'scheduled' and are scheduled to this node
        query = {
            'state': 'scheduled',
            'node': self._node_name
        }

        # list containing batches that are scheduled to this node and save them together with their experiment
        batches_with_experiments = []  # type: List[Tuple[Dict, Dict]]

        # dictionary, that maps docker image authentications to batches, which need this docker image
        image_to_batches = {}  # type: Dict[Tuple, List[Dict]]

        for batch in self._mongo.db['batches'].find(query):
            experiment = self._get_experiment_with_secrets(batch['experimentId'])
            batches_with_experiments.append((batch, experiment))

            image_authentication = ClientProxy._get_image_authentication(experiment)
            if image_authentication not in image_to_batches:
                image_to_batches[image_authentication] = []
            image_to_batches[image_authentication].append(batch)

        # pull images
        pull_futures = []
        for image_authentication, depending_batches in image_to_batches.items():
            image_url, auth = image_authentication
            future = self._pull_executor.submit(_pull_image, self._client, image_url, auth, depending_batches)
            pull_futures.append(future)

        for pull_future in pull_futures:
            image_pull_result = pull_future.result()  # type: ImagePullResult

            # If pulling failed, the batches, which needed this image fail and are removed from the
            # batches_with_experiments list
            if not image_pull_result.successful:
                for batch in image_pull_result.depending_batches:
                    # fail the batch
                    batch_id = str(batch['_id'])
                    self._pull_image_failure(image_pull_result.debug_info, batch_id, batch['state'])

                    # remove batches that are failed
                    batches_with_experiments = list(filter(
                        lambda batch_with_experiment: str(batch_with_experiment[0]['_id']) != batch_id,
                        batches_with_experiments
                    ))

        # run every batch, that has not failed
        run_futures = []  # type: List[concurrent.futures.Future]
        for batch, experiment in batches_with_experiments:
            future = self._run_executor.submit(
                ClientProxy._run_batch_container_and_handle_exceptions,
                self,
                batch,
                experiment
            )
            run_futures.append(future)

        # wait for all batches to run
        concurrent.futures.wait(run_futures, return_when=concurrent.futures.ALL_COMPLETED)

    def _get_experiment_with_secrets(self, experiment_id):
        """
        Returns the experiment of the given experiment_id with filled secrets.

        :param experiment_id: The experiment id to resolve.
        :type experiment_id: ObjectId
        :return: The experiment as dictionary with filled template values.
        :raise TrusteeServiceError: If the trustee service is unavailable or the trustee service could not fulfill all
        requested keys
        """
        experiment = self._mongo.db['experiments'].find_one(
            {'_id': ObjectId(experiment_id)},
        )

        experiment = self._fill_experiment_secret_keys(experiment)

        return experiment

    def _fill_experiment_secret_keys(self, experiment):
        """
        Returns the given experiment with filled template keys and values.

        :param experiment: The experiment to complete.
        :return: Returns the given experiment with filled template keys and values.
        :raise TrusteeServiceError: If the trustee service is unavailable or the trustee service could not fulfill all
        requested keys
        """
        experiment_secret_keys = get_experiment_secret_keys(experiment)
        response = self._trustee_client.collect(experiment_secret_keys)
        if response['state'] == 'failed':

            debug_info = response['debugInfo']

            if response.get('inspect'):
                response = self._trustee_client.inspect()
                if response['state'] == 'failed':
                    debug_info = response['debug_info']
                    raise TrusteeServiceError('Trustee service unavailable:{}{}'.format(os.linesep, debug_info))

            experiment_id = str(experiment['_id'])
            raise TrusteeServiceError(
                'Trustee service request failed for experiment "{}":{}{}'.format(experiment_id, os.linesep, debug_info)
            )

        experiment_secrets = response['secrets']
        return fill_experiment_secrets(experiment, experiment_secrets)

    @staticmethod
    def _get_image_url(experiment):
        """
        Gets the url of the docker image for the given experiment

        :param experiment: The experiment whose docker image url is returned
        :type experiment: Dict
        :return: The url of the docker image for the given experiment
        """
        return experiment['container']['settings']['image']['url']

    @staticmethod
    def _get_image_authentication(experiment):
        """
        Reads the docker authentication information from the given experiment and returns it as tuple. The first element
        is always the image_url for the docker image. The second element is a tuple containing the username and password
        for authentication at the docker registry. If no username and password is given, the second return value is None

        :param experiment: An experiment with filled secret keys, whose image authentication information should be
        returned
        :type experiment: Dict

        :return: A tuple containing the image_url as first element. The second element can be None or a Tuple containing
        (username, password) for authentication at the docker registry.
        :rtype: Tuple[str, None] or Tuple[str, Tuple[str, str]]

        :raise ImageAuthenticationError: If the given image authentication information is not complete
        (username and password are mandatory)
        """

        image_url = ClientProxy._get_image_url(experiment)

        image_auth = experiment['container']['settings']['image'].get('auth')
        if image_auth:
            for mandatory_key in ('username', 'password'):
                if mandatory_key not in image_auth:
                    raise ImageAuthenticationError(
                        'Image authentication is given, but "{}" is missing'.format(mandatory_key)
                    )

            image_auth = (image_auth['username'], image_auth['password'])
        else:
            image_auth = None

        return image_url, image_auth

    def _run_batch_container_and_handle_exceptions(self, batch, experiment):
        """
        Runs the given batch by calling _run_batch_container(), but handles exceptions, by calling
        _run_batch_container_failure().

        :param batch: The batch to run
        :type batch: dict
        :param experiment: The experiment of this batch
        :type experiment: dict
        :return:
        """
        try:
            self._run_batch_container(batch, experiment)
        except Exception as e:
            batch_id = str(batch['_id'])
            self._run_batch_container_failure(batch_id, str(e), batch['state'])

    def _run_batch_container(self, batch, experiment):
        """
        Creates a docker container and runs the given batch, with settings described in the given batch and experiment.
        Sets the state of the given batch to 'processing'.

        :param batch: The batch to run
        :type batch: dict
        :param experiment: The experiment of this batch
        :type experiment: dict
        """
        batch_id = str(batch['_id'])

        update_result = self._mongo.db['batches'].update_one(
            {
                '_id': ObjectId(batch_id),
                'state': 'scheduled'
            },
            {
                '$set': {
                    'state': 'processing',
                },
                '$push': {
                    'history': {
                        'state': 'processing',
                        'time': time.time(),
                        'debugInfo': None,
                        'node': self._node_name,
                        'ccagent': None,
                        'dockerStats': None
                    }
                }
            }
        )

        # only run the docker container, if the batch was successfully updated
        if update_result.modified_count == 1:
            self._run_container(batch, experiment)

    def _run_container(self, batch, experiment):
        """
        Runs a docker container for the given batch. Uses the following procedure:

        - Collects all arguments for the docker container execution
        - Removes old containers with the same name
        - Creates the docker container with the collected arguments
        - Creates an archive containing the blue_agent and the blue_file of this batch and copies this archive into the
          container
        - Starts the container

        :param batch: The batch to run inside the container
        :type batch: Dict[str, Any]
        :param experiment: The experiment of the given batch
        :type experiment: Dict[str, Any]
        """
        batch_id = str(batch['_id'])

        environment = {}
        if self._environment:
            environment = self._environment.copy()

        gpus = batch['usedGPUs']

        # set mount variables
        devices = []
        capabilities = []
        security_opt = []
        if batch['mount']:
            devices.append('/dev/fuse')
            capabilities.append('SYS_ADMIN')
            security_opt.append('apparmor:unconfined')

        # set image
        image = experiment['container']['settings']['image']['url']

        command = [
            'python3',
            CONTAINER_AGENT_PATH,
            '--outputs',
            '--debug',
            CONTAINER_BLUE_FILE_PATH
        ]

        ram = experiment['container']['settings']['ram']
        mem_limit = '{}m'.format(ram)

        # set ulimits
        ulimits = [
            docker.types.Ulimit(
                name='nofile',
                soft=NOFILE_LIMIT,
                hard=NOFILE_LIMIT
            )
        ]

        # remove container if it exists from earlier attempt
        existing_container = self._batch_containers(None).get(batch_id)
        if existing_container is not None:
            existing_container.remove(force=True)

        container = create_container_with_gpus(
            client=self._client,
            image=image,
            command=command,
            available_runtimes=self._runtimes,
            name=batch_id,
            user='1000:1000',
            working_dir=CONTAINER_OUTPUT_DIR,
            detach=True,
            mem_limit=mem_limit,
            memswap_limit=mem_limit,
            gpus=gpus,
            environment=environment,
            network=self._network,
            devices=devices,
            cap_add=capabilities,
            security_opt=security_opt,
            ulimits=ulimits
        )  # type: Container

        # copy blue agent and blue file to container
        tar_archive = self._create_batch_archive(batch)
        container.put_archive('/', tar_archive)
        tar_archive.close()

        container.start()

    def _create_blue_batch(self, batch):
        """
        Creates a dictionary containing the data for a blue batch.

        :param batch: The batch description
        :type batch: dict
        :return: A dictionary containing a blue batch
        :rtype: dict
        :raise TrusteeServiceError: If the trustee service is unavailable or unable to collect the requested secret keys
        :raise ValueError: If there was more than one blue batch after red_to_blue
        """
        batch_id = str(batch['_id'])
        batch_secret_keys = get_batch_secret_keys(batch)
        response = self._trustee_client.collect(batch_secret_keys)

        if response['state'] == 'failed':
            debug_info = 'Trustee service failed:\n{}'.format(response['debug_info'])
            disable_retry = response.get('disable_retry')
            batch_failure(
                self._mongo,
                batch_id,
                debug_info,
                None,
                batch['state'],
                disable_retry_if_failed=disable_retry
            )
            raise TrusteeServiceError(debug_info)

        batch_secrets = response['secrets']
        batch = fill_batch_secrets(batch, batch_secrets)

        experiment_id = batch['experimentId']

        experiment = self._mongo.db['experiments'].find_one(
            {'_id': ObjectId(experiment_id)}
        )

        red_data = {
            'redVersion': experiment['redVersion'],
            'cli': experiment['cli'],
            'inputs': batch['inputs'],
            'outputs': batch['outputs']
        }

        blue_batches = convert_red_to_blue(red_data)

        if len(blue_batches) != 1:
            raise ValueError('Got {} batches, but only one was asserted.'.format(len(blue_batches)))

        return blue_batches[0]

    def _create_batch_archive(self, batch):
        """
        Creates a tar archive.
        This archive contains the blue agent, a blue file, the outputs-directory and the inputs-directory.
        The blue file is filled with the blue data from the given batch.
        The outputs-directory is an empty directory, with name 'outputs'
        The inputs-directory is an empty directory, with name 'inputs'
        The tar archive and the blue file are always in memory and never stored on the local filesystem.

        The resulting archive is:
        /cc
        |--/blue_agent.py
        |--/blue_file.json
        |--/outputs/
        |--/inputs/

        :param batch: The data to put into the blue file of the returned archive
        :type batch: dict
        :return: A tar archive containing the blue agent and the given blue batch
        :rtype: io.BytesIO or bytes
        """
        data_file = io.BytesIO()
        tar_file = tarfile.open(mode='w', fileobj=data_file)

        # add blue agent
        tar_file.add(_get_blue_agent_host_path(), arcname=CONTAINER_AGENT_PATH, recursive=False)

        # add blue file. See https://bugs.python.org/issue22208 for more information
        blue_batch = self._create_blue_batch(batch)
        blue_batch_content = json.dumps(blue_batch).encode('utf-8')

        blue_batch_tarinfo = tarfile.TarInfo(CONTAINER_BLUE_FILE_PATH)
        blue_batch_tarinfo.size = len(blue_batch_content)

        tar_file.addfile(blue_batch_tarinfo, io.BytesIO(blue_batch_content))

        # add outputs directory
        output_directory_tarinfo = create_directory_tarinfo(CONTAINER_OUTPUT_DIR, owner_name='cc')
        tar_file.addfile(output_directory_tarinfo)

        # add inputs directory
        input_directory_tarinfo = create_directory_tarinfo(CONTAINER_INPUT_DIR, owner_name='cc')
        tar_file.addfile(input_directory_tarinfo)

        # close file
        tar_file.close()
        data_file.seek(0)

        return data_file

    def _run_batch_container_failure(self, batch_id, debug_info, current_state):
        batch_failure(self._mongo, batch_id, debug_info, None, current_state)

    def _pull_image_failure(self, debug_info, batch_id, current_state):
        batch_failure(self._mongo, batch_id, debug_info, None, current_state)


class TrusteeServiceError(Exception):
    pass


class ImageAuthenticationError(Exception):
    pass
