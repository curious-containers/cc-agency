import os
import shutil

from cc_core.commons.mnt_core import module_dependencies, interpreter_dependencies, CC_DIR
from cc_core.commons.mnt_core import module_destinations, interpreter_destinations

from cc_agency.commons.helper import calculate_agency_id


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


def build_dir_path(conf):
    agency_id = calculate_agency_id(conf)
    return os.path.expanduser(os.path.join('~', '.cache', agency_id, 'cc-core'))


def init_build_dir(conf):
    build_dir = build_dir_path(conf)
    if os.path.exists(build_dir):
        shutil.rmtree(build_dir)
    os.makedirs(build_dir)

    import cc_core.agent.connected.__main__

    source_paths, c_source_paths = module_dependencies([cc_core.agent.connected.__main__])
    module_dsts= module_destinations(source_paths, build_dir)
    interpreter_deps = interpreter_dependencies(c_source_paths)
    interpreter_dsts = interpreter_destinations(interpreter_deps, build_dir)

    for src, dst in module_dsts + interpreter_dsts:
        generic_copy(src, dst)

    content = [
        'FROM docker.io/debian:9.5-slim',
        'RUN useradd -ms /bin/bash cc',
        'ADD --chown=cc:cc ./{0} /{0}'.format(CC_DIR)
    ]
    with open(os.path.join(build_dir, 'Dockerfile'), 'w') as f:
        for line in content:
            print(line, file=f)
