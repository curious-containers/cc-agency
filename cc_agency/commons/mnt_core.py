import os
import shutil

import cc_core.agent.connected.main
from cc_core.commons.mnt_core import module_dependencies, interpreter_dependencies, CC_DIR
from cc_core.commons.mnt_core import module_destinations, interpreter_destinations


CC_CORE_IMAGE = 'cc-core'


def generic_copy(src, dst):
    if os.path.isdir(src):
        parent_dir = os.path.split(dst)[0]
        if not os.path.exists(parent_dir):
            os.makedirs(parent_dir)
        shutil.copytree(src, dst)
    else:
        parent_dir = os.path.split(dst)[0]
        if not os.path.exists(parent_dir):
            os.makedirs(parent_dir)
        shutil.copy(src, dst)


def init_build_dir(build_dir):
    agent_modules = [cc_core.agent.connected.main]
    module_deps = module_dependencies(agent_modules)
    interpreter_deps = interpreter_dependencies()
    module_dsts = module_destinations(module_deps, build_dir)
    interpreter_dsts = interpreter_destinations(interpreter_deps, build_dir)

    for src, dst in module_dsts + interpreter_dsts:
        generic_copy(src, dst)


def create_core_image_dockerfile(build_dir):
    content = [
        'FROM docker.io/debian:9.5-slim',
        'RUN useradd -ms /bin/bash cc',
        'ADD --chown=cc:cc ./{0} /{0}'.format(CC_DIR)
    ]
    with open(os.path.join(build_dir, 'Dockerfile'), 'w') as f:
        for line in content:
            print(line, file=f)
