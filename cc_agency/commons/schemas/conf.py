conf_schema = {
    'type': 'object',
    'properties': {
        'broker': {
            'type': 'object',
            'properties': {
                'external_url': {'type': 'string'}
            },
            'additionalProperties': False,
            'required': ['external_url']
        },
        'controller': {
            'type': 'object',
            'properties': {
                'log_file_path': {'type': 'string'},
                'bind_host': {'type': 'string'},
                'bind_port': {'type': 'integer'},
                'docker': {
                    'type': 'object',
                    'properties': {
                        'core_image': {
                            'type': 'object',
                            'properties': {
                                'image_url': {'type': 'string'},
                                'registry_auth': {
                                    'type': 'object',
                                    'properties': {
                                        'username': {'type': 'string'},
                                        'password': {'type': 'string'}
                                    },
                                    'additionalProperties': False,
                                    'required': ['username', 'password']
                                },
                                'additionalProperties': False,
                                'required': ['image_url']
                            }
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
                }
            },
            'additionalProperties': False,
            'required': ['log_file_path', 'bind_host', 'bind_port']
        },
        'db': {
            'type': 'object',
            'properties': {},
            'additionalProperties': False
        }
    },
    'additionalProperties': False,
    'required': ['broker', 'controller', 'db']
}
