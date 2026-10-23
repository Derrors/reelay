"""内置插件目录：每个插件一个子目录，插件相关的文件都放在自己的目录里。

::

    plugins/
    ├── organizer/            # 媒体库整理
    │   ├── __init__.py       # 插件代码（入口）
    │   ├── settings.py       # 插件设置的默认值与校验
    │   ├── naming.py         # 插件内部模块
    │   ├── category.py
    │   └── category.yaml     # 插件自带的数据文件，如二级分类规则
    └── daily_report/
        └── __init__.py

子目录会被 registry.discover() 自动发现：写一个 Provider / Hook / EventListener 类，用 @register
声明即被挂上；删目录或在配置里关掉即卸载，内核对此无感知。插件随包发布的数据文件（*.yaml）由
pyproject.toml 的 package-data 一并打包。

``settings.py`` 是内置插件的可选约定：提供 ``DEFAULTS`` 与 ``normalize(section)``，配置规范化时 core/config.py 按目录找到并调用，插件的默认设置因此不必写进内核。
外部插件放在 data/plugins/，组织方式相同（设置用 ui_fields 与 validate_config 声明，见 docs/plugins.md）。
"""
