"""Web 配置字段校验；扩展字段只按声明类型校验。"""
from __future__ import annotations

import math
from .naming_template import validate_template


def validate_naming(naming: dict) -> None:
    if not isinstance(naming, dict) or not {'movie', 'tv'} <= set(naming):
        raise ValueError('plugins.organizer.naming 必须包含 movie 和 tv Jinja2 模板')
    for kind, template in naming.items():
        validate_template(template, f'plugins.organizer.naming.{kind}')


def validate_declared_field(config: dict, field: dict) -> None:
    path = field.get('path', '')
    value = config
    for part in path.split('.'):
        if not isinstance(value, dict) or part not in value:
            if field.get('required'):
                raise ValueError(f'{path} 必填')
            return
        value = value[part]
    kind = field.get('type', 'string')
    if kind == 'boolean':
        valid = type(value) is bool
    elif kind == 'number':
        valid = type(value) in (int, float) and math.isfinite(value)
    else:
        valid = type(value) is str
    if not valid:
        raise ValueError(f'{path} 类型不正确')
    if kind == 'number' and (value < field.get('min', -math.inf) or value > field.get('max', math.inf)):
        raise ValueError(f'{path} 超出允许范围')
    if field.get('choices') and value not in field['choices']:
        raise ValueError(f'{path} 不是允许的选项')
