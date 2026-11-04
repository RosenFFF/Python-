# 颐护通：社区智慧养老服务与健康监测平台

覆盖「档案—预约—派单—服务—预警—结算—监管」全链路的社区养老服务平台。后端为 Python（FastAPI + SQLAlchemy），前端为 Vue 3，支持 Docker 一键部署。

设计文档见 [docs/设计文档.docx](docs/设计文档.docx)，答辩要点见 [docs/答辩问答.md](docs/答辩问答.md)，测试结论见 [docs/测试报告.md](docs/测试报告.md)。

## 快速开始

### 方式一：一键脚本（推荐）

需要 Python 3.11 以上。前端构建产物已包含在 `backend/static`，不需要 Node。

Windows：

```bash
powershell -ExecutionPolicy Bypass -File start.ps1
```

Linux / macOS：

```bash
./start.sh
```

首次运行会创建虚拟环境、安装依赖、生成随机密钥（写入 `.env`）、迁移数据库并写入演示数据。启动后打开 <http://127.0.0.1:8000>。

### 方式二：Docker

```bash
cp deploy/docker.env.example .env.docker
```

按文件内说明填入随机密钥后：

```bash
docker compose up -d --build
```

## 演示账号

密码统一为 `Demo@2026`。登录页也可以点选账号。

| 账号 | 角色 | 数据范围 |
| --- | --- | --- |
| `family_wang` | 家属（老人：王秀英，78 岁，中度失能，独居） | 本人 |
| `cg01` | 护理员 | 本人 |
| `grid_zhang` / `grid_chen` | 社区网格员 | 本网格 |
| `comm_liu` | 社区管理员 | 本社区 |
| `org_zhao` / `org_gao` | 服务机构管理员 | 本机构 |
| `street_sun` | 街道监管员 | 全街道 |
| `admin` | 系统超级管理员 | 全街道（默认无权查看敏感明文） |

## 五分钟体验主流程

1. `family_wang` 登录，在「预约服务」输入「帮我约明天下午两点助浴」，确认提交。
2. `grid_zhang` 在工作台受理，点「派单」查看前 3 名候选和每一项得分，一键派单。
3. `cg01` 在「今日工单」接单，进入工单签到（可点「模拟定位偏差」看超 200 米须说明）、勾选清单、上传照片、录入血压、签退。
4. `family_wang` 打开工单查看证据链并评价。打 3 星以下会生成回访任务。
5. `grid_zhang` 进入一位老人的档案，「录入随访」收缩压 186，触发红色预警。顶栏点「+31 分钟」快进演示时钟，预警自动升级到街道。回到预警看板闭环。
6. `comm_liu` 打开上月账单，逐条处理差异并人工复核；`grid_zhang` 核对，`org_zhao` 确认；`street_sun` 抽查后账单生效。
7. `admin` 在「备份恢复」发起恢复，在「审计日志」（`street_sun`）校验哈希链。

## 可穿戴设备模拟上报

```bash
cd backend
python scripts/simulate_wearable.py --key <YIHU_DEVICE_API_KEY> --devices WB-10000 --scenario tachycardia --rounds 4
```

场景：`normal`、`tachycardia`（连续心率过快，黄色预警）、`hypoxia`（血氧骤降，红色预警）、`still`（连续无体动）。设备密钥在 `.env` 里。

## 开发

```bash
cd backend
pip install -r requirements-dev.txt
```

运行测试和代码检查：

```bash
pytest --cov=app
```

```bash
ruff check .
```

启动开发服务器（默认端口 8000，接口文档 `/api/docs`）：

```bash
uvicorn app.main:app --reload
```

前端开发（端口 5173，接口代理到 8000）：

```bash
cd frontend
npm install
npm run dev
```

构建前端到 `backend/static`：

```bash
npm run build
```

## 目录结构

```text
backend/
  app/
    api/        路由、请求模型、权限依赖（薄层，不写业务）
    services/   业务服务：档案、预约、派单、执行、预警、质量、结算、统计、备份、审计
    domain/     纯算法和规则：派单评分、预警判定、计费、统计口径、状态机、NLU（无数据库依赖）
    models/     SQLAlchemy 实体
    jobs/       定时任务
    core/       配置、时钟、加密、口令、导出
    db/         会话、迁移引导、审计触发器
    seed/       演示数据
  alembic/      数据库迁移
  tests/        单元、接口、端到端测试
  scripts/      可穿戴模拟上报
frontend/       Vue 3 前端（适老化模式、PWA 离线、ECharts 看板）
deploy/         容器入口和环境变量模板
docs/           设计文档、测试报告、答辩问答
```

## 配置

部署级配置走环境变量（`YIHU_*`，模板见 `.env.example`）。业务参数（派单权重、阈值、时限、补贴政策、预警规则）在后台「业务参数」「预警规则」页面修改，立即生效并写入审计。

**务必备份 `.env` 里的 `YIHU_FIELD_KEY`。** 丢失后，已加密的身份证、手机号、地址无法解密。

## 第三方组件

见 [THIRD_PARTY.md](THIRD_PARTY.md)。核心业务逻辑（派单、预警、结算、审计、备份恢复等）均为本项目实现，未套用现成同类系统。
