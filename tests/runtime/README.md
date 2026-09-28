# AstrBot 环境回归检查

在已安装插件依赖的 AstrBot Python 环境运行，最后一个参数为待验证的插件源码目录：

```sh
python tests/runtime/check_multichapter.py /path/to/astrbot_plugin_qq_like
python tests/runtime/check_count_progress.py /path/to/astrbot_plugin_qq_like
```

使用临时文件和模拟下载、发送接口，不登录账号、不发送QQ消息、不下载真实作品。
离线测试仍使用 `python -m pytest tests -q`；阅读服务测试使用 `python -m unittest discover -s reader -p test_reader.py`。
