#!/usr/bin/env python3

from setuptools import setup

description = 'CC-Core is part of the Curious Containers project. It manages a cluster of docker-engines to execute ' \
              'data-driven experiments in parallel.'

setup(
    name='cc-agency',
    version='3.2.0',
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
        'cc-core >= 3.2, < 3.3'
    ]
)
