from cc_core.commons.schemas.engines.container import MIN_RAM_LIMIT


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
                                                'assert_hostname': {'type': ['boolean', 'string']}
                                            },
                                            'additionalProperties': True
                                        },
                                        'environment': {
                                            'type': 'object',
                                            'patternProperties': {
                                                '^[a-zA-Z0-9_-]+$': {'type': 'string'}
                                            },
                                            'additionalProperties': False
                                        },
                                        'network': {'type': 'string'},
                                        'hardware': {
                                            'type': 'object',
                                            'properties': {
                                                'gpus': {
                                                    'type': 'array',
                                                    'items': {
                                                        'type': 'object',
                                                        'properties': {
                                                            'id': {'type': 'integer', 'minimum': 0},
                                                            'vram': {'type': 'integer', 'minimum': MIN_RAM_LIMIT},
                                                        },
                                                        'additionalProperties': False,
                                                        'required': ['id']
                                                    }
                                                }
                                            },
                                            'additionalProperties': False
                                        }
                                    },
                                    'required': ['base_url'],
                                    'additionalProperties': False
                                }
                            },
                            'additionalProperties': False
                        },
                        'allow_insecure_capabilities': {'type': 'boolean'}
                    },
                    'additionalProperties': False,
                    'required': ['nodes']
                },
                'scheduling': {
                    'type': 'object',
                    'properties': {
                        'strategy': {'enum': ['spread', 'binpack']}
                    },
                    'additionalProperties': False,
                    'required': ['strategy']
                },
                'notification_hooks': {
                    'type': 'array',
                    'items': {
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
                            }
                        },
                        'additionalProperties': False,
                        'required': ['url']
                    }
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
