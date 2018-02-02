#!/usr/bin/env python3

from distutils.core import setup

description = 'CC-Core is part of the Curious Containers project. It manages a cluster of docker-engines to execute ' \
              'data-driven experiments in parallel.'

setup(
    name='cc-agency',
    version='2.0.0',
    summary=description,
    description=description,
    author='Christoph Jansen',
    author_email='Christoph.Jansen@htw-berlin.de',
    url='https://github.com/curious-containers/cc-agency',
    packages=[
        'cc_agency'
    ],
    license='AGPL-3.0',
    platforms=['any'],
    install_requires=[
        'cc-core >= 2.0, < 2.1'
    ]
)
