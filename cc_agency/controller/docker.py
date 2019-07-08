import io
import json
import os
import queue
import tarfile
from queue import Queue
from threading import Thread
import concurrent.futures
import time
from traceback import format_exc
from typing import List, Tuple, Dict

import docker
import jsonschema
from docker.models.containers import Container
from docker.tls import TLSConfig
from bson.objectid import ObjectId

from cc_agency.commons.schemas.callback import callback_schema
from cc_agency.commons.secrets import get_experiment_secret_keys, fill_experiment_secrets, fill_batch_secrets, \
    get_batch_secret_keys
from cc_core.commons.engines import engine_to_runtime
from cc_core.commons.gpu_info import set_nvidia_environment_variables

from cc_agency.commons.helper import generate_secret, create_kdf, batch_failure, calculate_agency_id
from cc_core.commons.red_to_blue import convert_red_to_blue

CURL_IMAGE = 'docker.io/buildpack-deps:bionic-curl'
BLUE_AGENT_FILE_DIR = 'cc'
BLUE_AGENT_CONTAINER_NAME = 'blue_agent.py'
BLUE_FILE_CONTAINER_NAME = 'blue_file.json'
CHECK_RUNNING_CONTAINERS_INTERVAL = 1.0


class ImagePullResult:
    def __init__(self, image_url, auth, successful, debug_info, depending_batches):
        """
        Creates a new DockerImagePull object

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
    NUM_WORKERS = 4

    def __init__(self, node_name, conf, mongo, trustee_client):
        self._node_name = node_name
        self._conf = conf
        self._mongo = mongo
        self._trustee_client = trustee_client

        # queue and list containing tuples of docker containers together with the batch id running inside this container
        self._started_container_batches = queue.Queue()  # type: Queue[Tuple[Container, str]]
        self._running_container_batches = []  # type: List[Tuple[Container, str]]

        node_conf = conf.d['controller']['docker']['nodes'][node_name]
        self._base_url = node_conf['base_url']
        self._tls = False
        if 'tls' in node_conf:
            self._tls = TLSConfig(**node_conf['tls'])

        self._environment = node_conf.get('environment')
        self._network = node_conf.get('network')

        self._external_url = conf.d['broker']['external_url'].rstrip('/')

        self._action_q = None
        self._client = None
        self._online = None

        # using hash of external url to distinguish between volume names created by different agency installations
        self._agency_id = calculate_agency_id(conf)

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
            self._fail_batches_without_assigned_container()  # in case of agency restart
        except Exception:
            self._set_offline(format_exc())
            return

        self._action_q = Queue()
        Thread(target=self._action_loop).start()
        Thread(target=self._check_running_containers_loop).start()
        self._set_online(ram, cpus)
        self._action_q.put({'action': 'inspect'})

        # initialize Executor Pools
        self._pull_executor = concurrent.futures.ThreadPoolExecutor(max_workers=ClientProxy.NUM_WORKERS)
        self._run_executor = concurrent.futures.ThreadPoolExecutor(max_workers=ClientProxy.NUM_WORKERS)

    def is_online(self):
        return bool(self._online)

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
                        'time': time.time(),
                        'debugInfo': None
                    }
                }
            }
        )
        self._action_q.put({'action': 'init_cc_core'})

    def _set_offline(self, debug_info):
        print('Node offline:', self._node_name)
        timestamp = time.time()

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
            {
                'node': self._node_name,
                'state': {'$in': ['scheduled', 'processing']}
            },
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

        filters = {'status': status}
        if status is None:
            filters = None

        containers = self._client.containers.list(all=True, limit=-1, filters=filters)

        for c in containers:
            try:
                ObjectId(c.name)
                batch_containers[c.name] = c
            except:
                pass

        return batch_containers

    def _fail_batches_without_assigned_container(self):
        containers = self._batch_containers(None)

        cursor = self._mongo.db['batches'].find(
            {
                'node': self._node_name,
                'state': {'$in': ['processing']}
            },
            {'_id': 1}
        )

        for batch in cursor:
            bson_id = batch['_id']
            batch_id = str(bson_id)

            if batch_id not in containers:
                debug_info = 'No container assigned.'
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

            if batch['state'] == 'processing':
                batch_failure(self._mongo, batch_id, debug_info, None, self._conf)

    def inspect_offline_node(self):
        try:
            self._client = docker.DockerClient(base_url=self._base_url, tls=self._tls, version='auto')
            ram, cpus = self._info()
            self._inspect()
        except Exception:
            self._client = None
            return

        self._action_q = Queue()
        Thread(target=self._action_loop).start()
        self._set_online(ram, cpus)

    def _inspect(self):
        print('Node inspection:', self._node_name)

        command = 'curl -f {}'.format(self._external_url)

        self._client.containers.run(
            CURL_IMAGE,
            command,
            user='1000:1000',
            remove=True,
            environment=self._environment,
            network=self._network
        )

    def put_action(self, data):
        """
        Adds the action data into the action_q. Returns if this operation was successful or not.

        :param data: The action data to process
        :return: True, if the action could be put into the action_queue, otherwise False
        """
        if self._action_q is None:
            return False

        try:
            self._action_q.put(data)
        except AttributeError:
            return False
        return True

    def _action_loop(self):
        while True:
            data = self._action_q.get()

            if 'action' not in data:
                continue

            action = data['action']

            inspect = False

            if action == 'inspect':
                inspect = True

            elif action == 'check_for_batches':
                try:
                    self._check_for_batches()
                except Exception:
                    inspect = True

            elif action == 'clean_up':
                try:
                    self._remove_cancelled_containers()
                    self._remove_exited_containers()
                except:
                    inspect = True

            if inspect:
                try:
                    self._inspect()
                except Exception:
                    self._set_offline(format_exc())
                    self._action_q = None
                    self._client = None

            if not self._online:
                return

    def _check_running_containers_loop(self):
        """
        Checks running containers and processes them after exiting.
        Retrieves all started containers from the _started_container_batches queue and appends them to the
        _running_container_batches list. Checks whether a running container exited and processes it.
        """
        while True:
            try:
                # noinspection PyTypeChecker
                for started_container_batch in iter(
                        self._started_container_batches.get_nowait,
                        self._started_container_batches
                ):
                    self._running_container_batches.append(started_container_batch)
            except queue.Empty:
                pass

            exited_containers = []

            for running_container, batch_id in self._running_container_batches:
                running_container.reload()

                if running_container.status != 'running':
                    self._check_exited_container(running_container, batch_id)
                    exited_containers.append((running_container, batch_id))

            # remove exited containers
            for exited_container, batch_id in exited_containers:
                self._running_container_batches.remove((exited_container, batch_id))

            time.sleep(CHECK_RUNNING_CONTAINERS_INTERVAL)

    def _check_exited_container(self, container, batch_id):
        """
        Inspects the logs of the given exited container and updates the database accordingly.

        :param container: The container to inspect
        :type container: Container
        :param batch_id: The batch id, that was processed inside the given container
        :type batch_id: str
        """
        bson_batch_id = ObjectId(batch_id)

        try:
            stdout_logs = container.logs(stderr=False).decode('utf-8')
            stderr_logs = container.logs(stdout=False).decode('utf-8')
        except Exception as e:
            debug_info = 'Could not get logs of container: {}'.format(str(e))
            batch_failure(self._mongo, batch_id, debug_info, None, self._conf)
            return

        data = None
        try:
            data = json.loads(stdout_logs)
        except json.JSONDecodeError as e:
            debug_info = 'CC-Agent data is not a valid json object: {}'.format(str(e))
            batch_failure(self._mongo, batch_id, debug_info, data, self._conf)
            return

        try:
            jsonschema.validate(data, callback_schema)
        except jsonschema.ValidationError as e:
            debug_info = 'CC-Agent data sent by callback does not comply with jsonschema: {}'.format(str(e))
            batch_failure(self._mongo, batch_id, debug_info, data, self._conf)
            return

        if data['state'] == 'failed':
            debug_info = 'Batch failed.\nContainer stderr:\n{}\ndebug info:\n{}'.format(stderr_logs, data['debugInfo'])
            batch_failure(self._mongo, batch_id, debug_info, data, self._conf)
            return

        batch = self._mongo.db['batches'].find_one(
            {'_id': bson_batch_id, 'state': 'processing'},
            {'attempts': 1, 'node': 1}
        )
        if not batch:
            raise ValueError('batch id "{}" not found.'.format(batch_id))

        self._mongo.db['batches'].update(
            {'_id': bson_batch_id},
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
                        'ccagent': data
                    }
                }
            }
        )

        container.remove()

    def _check_for_batches(self):
        """
        Queries the database to find batches, which are in state 'scheduled' and are scheduled to the node of this
        ClientProxy.
        First all docker images are pulled, which are used to process these batches. Afterwards the batch processing is
        run. The state in the database for these batches is then updated to 'processing'.

        :raise TrusteeServiceError: If the trustee service is unavailable or the trustee service could not fulfill all
        requested keys
        """

        # query for batches, that are in state 'scheduled' and are scheduled to this node
        query = {
            'state': 'scheduled',
            'node': self._node_name
        }

        # list containing batches that are scheduled to this node and save them together with their experiment
        batches_with_experiments = []  # type: List[Tuple[Dict, Dict]]

        # also create a dictionary, that maps docker image authentications to batches, which need this docker image
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
                    self._pull_image_failure(image_pull_result.debug_info, batch_id)

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

        :raise Exception: If the given image authentication information is not complete (username and password are
        mandatory)
        """

        image_url = ClientProxy._get_image_url(experiment)

        image_auth = experiment['container']['settings']['image'].get('auth')
        if image_auth:
            for mandatory_key in ('username', 'password'):
                if mandatory_key not in image_auth:
                    raise Exception('Image authentication is given, but "{}" is missing'.format(mandatory_key))

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
            self._run_batch_container_failure(batch_id, str(e))

    def _run_batch_container(self, batch, experiment):
        """
        Runs the given batch, with settings described in the given batch and experiment.
        Sets the state of the given batch to 'processing'.
        Creates a callback token for the given batch

        :param batch: The batch to run
        :type batch: dict
        :param experiment: The experiment of this batch
        :type experiment: dict
        """
        batch_id = str(batch['_id'])
        runtime = engine_to_runtime(experiment['container']['engine'])

        # set nvidia gpu environment
        gpus = batch['usedGPUs']
        environment = {}
        if self._environment:
            environment = self._environment.copy()
        if gpus:
            set_nvidia_environment_variables(environment, gpus)

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

        token = generate_secret()
        salt = os.urandom(16)
        kdf = create_kdf(salt)

        self._mongo.db['callback_tokens'].insert_one({
            'batch_id': batch_id,
            'salt': salt,
            'token': kdf.derive(token.encode('utf-8')),
            'timestamp': time.time()
        })

        container_blue_agent_path = os.path.join(BLUE_AGENT_FILE_DIR, BLUE_AGENT_CONTAINER_NAME)
        container_blue_file_path = os.path.join(BLUE_AGENT_FILE_DIR, BLUE_FILE_CONTAINER_NAME)

        command = [
            'python3',
            container_blue_agent_path,
            '--outputs',
            '--debug',
            container_blue_file_path
        ]

        command = ' '.join(command)

        ram = experiment['container']['settings']['ram']
        mem_limit = '{}m'.format(ram)

        self._mongo.db['batches'].update_one(
            {'_id': ObjectId(batch_id)},
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
                        'ccagent': None
                    }
                }
            }
        )

        # remove container if it exists from earlier attempt
        existing_container = self._batch_containers(None).get(batch_id)
        if existing_container is not None:
            existing_container.remove(force=True)

        container = self._client.containers.create(
            image,
            command,
            name=batch_id,
            user='1000:1000',
            detach=True,
            mem_limit=mem_limit,
            memswap_limit=mem_limit,
            runtime=runtime,
            environment=environment,
            network=self._network,
            devices=devices,
            cap_add=capabilities,
            security_opt=security_opt
        )  # type: Container

        # copy blue agent and blue file to container
        tar_archive = self._create_batch_archive(batch, BLUE_AGENT_FILE_DIR)
        container.put_archive('/', tar_archive)
        tar_archive.close()

        container.start()

        self._started_container_batches.put((container, batch_id))

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
            batch_failure(self._mongo, batch_id, debug_info, None, self._conf, disable_retry_if_failed=disable_retry)
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

    def _create_batch_archive(self, batch, directory):
        """
        Creates a tar archive. This archive contains the blue agent and a blue file. The blue file is filled with the
        given blue data. The blue agent and the blue file are stored inside the given directory. The tar archive and all
        files contained in this archive are in memory and are never stored in the local filesystem.

        :param batch: The data to put into the blue file of the returned archive
        :type batch: dict
        :param directory: Inside the archive the blue agent and the blue batch is located under the given directory
        :type directory: str
        :return: A tar archive containing the blue agent and the given blue batch
        :rtype: io.BytesIO or bytes
        """
        data_file = io.BytesIO()
        tar_file = tarfile.open(mode='w', fileobj=data_file)

        # add blue agent
        agent_archive_name = os.path.join(directory, BLUE_AGENT_CONTAINER_NAME)
        tar_file.add(_get_blue_agent_host_path(), arcname=agent_archive_name, recursive=False)

        # add blue file
        blue_batch_name = os.path.join(directory, BLUE_FILE_CONTAINER_NAME)
        blue_batch = self._create_blue_batch(batch)
        blue_batch_content = json.dumps(blue_batch).encode('utf-8')

        # see https://bugs.python.org/issue22208 for more information
        blue_batch_tarinfo = tarfile.TarInfo(blue_batch_name)
        blue_batch_tarinfo.size = len(blue_batch_content)

        tar_file.addfile(blue_batch_tarinfo, io.BytesIO(blue_batch_content))

        # close file
        tar_file.close()
        data_file.seek(0)

        return data_file

    def _run_batch_container_failure(self, batch_id, debug_info):
        batch_failure(self._mongo, batch_id, debug_info, None, self._conf)

    def _pull_image_failure(self, debug_info, batch_id):
        batch_failure(self._mongo, batch_id, debug_info, None, self._conf)


class TrusteeServiceError(Exception):
    pass
