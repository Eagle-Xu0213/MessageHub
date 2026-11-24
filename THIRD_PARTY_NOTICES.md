# Third-Party Notices / 第三方组件声明

MessageHub 以 [MIT](LICENSE) 协议发布。本文件列出其依赖或调用的第三方开源组件的来源、许可证与使用方式。各组件版权归其各自的权利所有者；各第三方组件的官方发行由其项目维护者负责。

## Moect（独立的外部工具，不属于本项目）

- 项目：<https://github.com/mofan6/Moect>
- 许可证：GPL-3.0（衍生自 QQ Chat Exporter、WeChatMsg / WeChatMsg-4x，详见其仓库内的归属声明）
- 使用方式：安装脚本在独立目录复用或克隆 Moect 源码。无界面计划任务调用该源码生成本机明文快照，采集桥读取快照。MessageHub 仓库不包含 Moect 源码或二进制文件。

## QQ OneBot 引擎（用户独立安装）

MessageHub 接收 [OneBot 11](https://github.com/botuniverse/onebot-11) 事件。可连接用户已有的兼容引擎，例如 [SnowLuma](https://github.com/baixiangyuan/SnowLuma/releases/tag/v2.4.2) 或 NapCat。引擎及其专有组件由各自发行方独立授权。MessageHub 不随应用分发这些程序。SnowLuma 专有组件的自动部署及再分发限制见 [发行包条款](https://github.com/baixiangyuan/SnowLuma/blob/v2.4.2/EULA.md)。

## 运行时依赖（Python 包）

| 组件 | 许可证 | 来源 |
|---|---|---|
| FastAPI | MIT | <https://github.com/tiangolo/fastapi> |
| Starlette | BSD-3-Clause | <https://github.com/encode/starlette> |
| Uvicorn | BSD-3-Clause | <https://github.com/encode/uvicorn> |
| Jinja2 | BSD-3-Clause | <https://github.com/pallets/jinja2> |
| Pydantic | MIT | <https://github.com/pydantic/pydantic> |
| python-dotenv | BSD-3-Clause | <https://github.com/theskumar/python-dotenv> |
| python-multipart | Apache-2.0 | <https://github.com/Kludex/python-multipart> |
| zstandard | BSD-3-Clause | <https://github.com/indygreg/python-zstandard> |
| httpx | BSD-3-Clause | <https://github.com/encode/httpx> |

| psutil | BSD-3-Clause | <https://github.com/giampaolo/psutil> |
| pycryptodomex | 见上游许可证 | <https://github.com/Legrandin/pycryptodome> |

以上为 `pyproject.toml` 直接声明的依赖；其传递依赖（click、h11、anyio、markupsafe、pydantic-core、typing-extensions 等）随 pip 安装元数据分发，保留各自原许可证。

## 开发依赖

| 组件 | 许可证 | 来源 |
|---|---|---|
| pytest | MIT | <https://github.com/pytest-dev/pytest> |
| pytest-cov | MIT | <https://github.com/pytest-dev/pytest-cov> |

构建后端 setuptools（MIT）随构建环境安装，不随本项目分发。

## 许可证说明

- 上表许可证类型以各组件仓库当前 LICENSE 文件为准；如个别组件许可证版本有出入，以组件自身声明为准。
- Python 依赖通过 PyPI 安装。Moect 源码和 QQ 引擎按各自的方式独立准备。

## 免责声明

本项目及上述组件均按「原样」提供，不含任何形式的担保。各组件的商标与项目名称归其各自所有者。
