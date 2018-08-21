from cc_core.commons.schemas.common import pattern_key


_files_schema = {
    'type': 'object',
    'patternProperties': {
        pattern_key: {
            'type': 'object',
            'properties': {
                'isOptional': {'type': 'boolean'},
                'isArray': {'type': 'boolean'},
                'files': {
                    'type': 'array',
                    'items': {
                        'type': 'object',
                        'properties': {
                            'path': {'type': 'string'},
                            'size': {'type': 'number'},
                            'debugInfo': {'type': 'string'}
                        },
                        'additionalProperties': False,
                        'required': ['path', 'size', 'debugInfo']
                    }
                }
            },
            'additionalProperties': False,
            'required': ['isOptional', 'isArray', 'files']
        }
    },
    'additionalProperties': False
}

# TODO: Incomplete
callback_schema = {
    'type': 'object',
    'properties': {
        'state': {'enum': ['succeeded', 'failed']}
    },
    'additionalProperties': True,
    'required': ['state']
}