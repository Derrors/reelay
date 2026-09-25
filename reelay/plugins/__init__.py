"""用户插件目录。

放这里的模块会被 registry.discover() 自动发现。写一个 Hook / EventListener 类，
用 @register 声明，即被挂上；删文件或在 config 里关掉即卸载。内核对此无感知。
"""
