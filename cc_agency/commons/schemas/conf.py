conf_schema = {
    'type': 'object',
    'properties': {
        'broker': {
            'type': 'object',
            'properties': {
                'external_url': {'type': 'string'},
                'auth': {
                    'type': 'object',
                    'properties': {
                        'num_login_attempts': {'type': 'integer'},
                        'block_for_seconds': {'type': 'integer'},
                        'tokens_valid_for_seconds': {'type': 'integer'}
                    },
                    'additionalProperties': False,
                    'required': ['num_login_attempts', 'block_for_seconds', 'tokens_valid_for_seconds']
                }
            },
            'additionalProperties': False,
            'required': ['external_url', 'auth']
        },
        'controller': {
            'type': 'object',
            'properties': {
                'external_url': {'type': 'string'},
                'bind_host': {'type': 'string'},
                'bind_port': {'type': 'integer'},
                'docker': {
                    'type': 'object',
                    'properties': {
                        'core_image': {
                            'type': 'object',
                            'properties': {
                                'url': {'type': 'string'},
                                'auth': {
                                    'type': 'object',
                                    'properties': {
                                        'username': {'type': 'string'},
                                        'password': {'type': 'string'}
                                    },
                                    'additionalProperties': False,
                                    'required': ['username', 'password']
                                },
                                'disable_pull': {'type': 'boolean'}
                            },
                            'additionalProperties': False,
                            'required': ['url']
                        },
                        'nodes': {
                            'type': 'object',
                            'patternProperties': {
                                '^[a-zA-Z0-9_-]+$': {
                                    'type': 'object',
                                    'properties': {
                                        'base_url': {'type': 'string'},
                                        'tls': {
                                            'type': 'object',
                                            'properties': {
                                                'verify': {'type': 'string'},
                                                'client_cert': {
                                                    'type': 'array',
                                                    'items': {'type': 'string'}
                                                },
                                                'assert_hostname': {'type': 'boolean'}
                                            },
                                            'additionalProperties': True
                                        }
                                    },
                                    'required': ['base_url'],
                                    'additionalProperties': False
                                }
                            },
                            'additionalProperties': False
                        }
                    },
                    'additionalProperties': False,
                    'required': ['nodes']
                },
                'scheduling': {
                    'type': 'object',
                    'properties': {
                        'strategy': {'enum': ['spread', 'binpack']},
                        'attempts_to_fail': {'type': 'integer'}
                    },
                    'additionalProperties': False,
                    'required': ['strategy', 'attempts_to_fail']
                }
            },
            'additionalProperties': False,
            'required': ['external_url', 'bind_host', 'bind_port', 'docker', 'scheduling']
        },
        'mongo': {
            'type': 'object',
            'properties': {
                'host': {'type': 'string'},
                'port': {'type': 'integer'},
                'db': {'type': 'string'},
                'username': {'type': 'string'},
                'password': {'type': 'string'}
            },
            'additionalProperties': False,
            'required': ['db', 'username', 'password']
        }
    },
    'additionalProperties': False,
    'required': ['broker', 'controller', 'mongo']
}
