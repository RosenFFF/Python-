# 第三方组件与许可证

本项目通过包管理器引用以下开源组件，未修改其源码。

## 后端（Python）

| 组件 | 用途 | 许可证 | 来源 |
| --- | --- | --- | --- |
| FastAPI | Web 框架 | MIT | <https://github.com/fastapi/fastapi> |
| Uvicorn | ASGI 服务器 | BSD-3-Clause | <https://github.com/encode/uvicorn> |
| SQLAlchemy | ORM | MIT | <https://www.sqlalchemy.org> |
| Alembic | 数据库迁移 | MIT | <https://github.com/sqlalchemy/alembic> |
| Pydantic / pydantic-settings | 数据校验、配置 | MIT | <https://github.com/pydantic/pydantic> |
| PyJWT | 访问令牌 | MIT | <https://github.com/jpadilla/pyjwt> |
| bcrypt | 口令哈希 | Apache-2.0 | <https://github.com/pyca/bcrypt> |
| cryptography | AES-GCM 字段加密 | Apache-2.0 / BSD-3-Clause | <https://github.com/pyca/cryptography> |
| Pillow | 图片压缩 | MIT-CMU (HPND) | <https://github.com/python-pillow/Pillow> |
| openpyxl | Excel 导入导出 | MIT | <https://foss.heptapod.net/openpyxl/openpyxl> |
| python-multipart | 文件上传解析 | Apache-2.0 | <https://github.com/Kludex/python-multipart> |
| APScheduler | 定时调度 | MIT | <https://github.com/agronholm/apscheduler> |
| httpx | HTTP 客户端（可选大模型调用、测试） | BSD-3-Clause | <https://github.com/encode/httpx> |
| pytest / pytest-cov | 测试 | MIT | <https://github.com/pytest-dev/pytest> |
| Ruff | 代码检查 | MIT | <https://github.com/astral-sh/ruff> |

## 前端（JavaScript）

| 组件 | 用途 | 许可证 | 来源 |
| --- | --- | --- | --- |
| Vue | 前端框架 | MIT | <https://github.com/vuejs/core> |
| Vue Router | 路由 | MIT | <https://github.com/vuejs/router> |
| Apache ECharts | 图表 | Apache-2.0 | <https://github.com/apache/echarts> |
| Vite / @vitejs/plugin-vue | 构建工具 | MIT | <https://github.com/vitejs/vite> |

## 说明

- 标志字样和登录页标题使用思源宋体（Noto Serif SC，SIL OFL 1.1）的子集，来源 <https://fonts.google.com/noto/specimen/Noto+Serif+SC>。
- 演示数据中的人名、证件号、手机号均由脚本随机生成，不对应真实人员。
- 背景数据引自赛题给出的《2025 年国民经济和社会发展统计公报》。
