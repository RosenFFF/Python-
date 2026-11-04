"""生成设计文档 docs/设计文档.docx。用法：python docs/src/build_design_doc.py"""
from __future__ import annotations

from pathlib import Path

from docx import Document
from docx.enum.table import WD_TABLE_ALIGNMENT
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Cm, Pt, RGBColor

HERE = Path(__file__).parent
IMG = HERE / "img"
SHOT = HERE / "screens"
GREEN = RGBColor(0x25, 0x51, 0x8F)
FONT = "微软雅黑"

doc = Document()
section = doc.sections[0]
section.left_margin = section.right_margin = Cm(2.3)
section.top_margin = section.bottom_margin = Cm(2.2)


def set_font(style, size, bold=False, color=None):
    style.font.name = FONT
    style.element.rPr.rFonts.set(qn("w:eastAsia"), FONT)
    style.font.size = Pt(size)
    style.font.bold = bold
    if color is not None:
        style.font.color.rgb = color


set_font(doc.styles["Normal"], 10.5)
doc.styles["Normal"].paragraph_format.line_spacing = 1.35
doc.styles["Normal"].paragraph_format.space_after = Pt(6)
set_font(doc.styles["Heading 1"], 16, True, GREEN)
set_font(doc.styles["Heading 2"], 12.5, True, GREEN)
set_font(doc.styles["Heading 3"], 11, True, RGBColor(0x1F, 0x2A, 0x28))
doc.styles["Heading 1"].paragraph_format.space_before = Pt(18)
doc.styles["Heading 1"].paragraph_format.space_after = Pt(8)
doc.styles["Heading 2"].paragraph_format.space_before = Pt(12)
doc.styles["Heading 2"].paragraph_format.space_after = Pt(4)
set_font(doc.styles["List Bullet"], 10.5)
set_font(doc.styles["Caption"], 9, False, RGBColor(0x60, 0x6C, 0x69))


def h1(text):
    doc.add_heading(text, 1)


def h2(text):
    doc.add_heading(text, 2)


def h3(text):
    doc.add_heading(text, 3)


def p(text):
    return doc.add_paragraph(text)


def bullets(items):
    for item in items:
        paragraph = doc.add_paragraph(style="List Bullet")
        if isinstance(item, tuple):
            run = paragraph.add_run(item[0] + "：")
            run.bold = True
            paragraph.add_run(item[1])
        else:
            paragraph.add_run(item)


def shade(cell, color):
    tc = cell._tc.get_or_add_tcPr()
    element = OxmlElement("w:shd")
    element.set(qn("w:val"), "clear")
    element.set(qn("w:color"), "auto")
    element.set(qn("w:fill"), color)
    tc.append(element)


def table(headers, rows, widths=None, size=9):
    tbl = doc.add_table(rows=1, cols=len(headers))
    tbl.style = "Table Grid"
    tbl.alignment = WD_TABLE_ALIGNMENT.CENTER
    for index, text in enumerate(headers):
        cell = tbl.rows[0].cells[index]
        cell.text = ""
        run = cell.paragraphs[0].add_run(text)
        run.bold = True
        run.font.size = Pt(size)
        run.font.color.rgb = RGBColor(0xFF, 0xFF, 0xFF)
        shade(cell, "25518F")
    # 表头跨页重复
    tr_pr = tbl.rows[0]._tr.get_or_add_trPr()
    repeat = OxmlElement("w:tblHeader")
    repeat.set(qn("w:val"), "true")
    tr_pr.append(repeat)
    for row in rows:
        cells = tbl.add_row().cells
        for index, text in enumerate(row):
            cells[index].text = ""
            run = cells[index].paragraphs[0].add_run(str(text))
            run.font.size = Pt(size)
            cells[index].paragraphs[0].paragraph_format.space_after = Pt(0)
            cells[index].paragraphs[0].paragraph_format.line_spacing = 1.15
    if widths:
        for row in tbl.rows:
            for index, width in enumerate(widths):
                row.cells[index].width = Cm(width)
    doc.add_paragraph().paragraph_format.space_after = Pt(2)
    return tbl


def figure(path, caption, width=16.2):
    doc.add_picture(str(path), width=Cm(width))
    doc.paragraphs[-1].alignment = WD_ALIGN_PARAGRAPH.CENTER
    cap = doc.add_paragraph(caption, style="Caption")
    cap.alignment = WD_ALIGN_PARAGRAPH.CENTER


def code(text):
    paragraph = doc.add_paragraph()
    paragraph.paragraph_format.left_indent = Cm(0.5)
    run = paragraph.add_run(text)
    run.font.name = "Consolas"
    run.font.size = Pt(9.5)


# ---------- 封面 ----------
for _ in range(5):
    doc.add_paragraph()
title = doc.add_paragraph()
title.alignment = WD_ALIGN_PARAGRAPH.CENTER
run = title.add_run("颐护通")
run.bold = True
run.font.size = Pt(34)
run.font.color.rgb = GREEN
sub = doc.add_paragraph()
sub.alignment = WD_ALIGN_PARAGRAPH.CENTER
sub.add_run("社区智慧养老服务与健康监测平台").font.size = Pt(18)
doc.add_paragraph()
info = doc.add_paragraph()
info.alignment = WD_ALIGN_PARAGRAPH.CENTER
info.add_run("设计文档\n2026 年第四届大学生信息系统创新大赛 · Python 编程巅峰赛决赛").font.size = Pt(12)
doc.add_page_break()
toc_title = doc.add_paragraph()
toc_run = toc_title.add_run("目录")
toc_run.bold = True
toc_run.font.size = Pt(16)
toc_run.font.color.rgb = GREEN
field = doc.add_paragraph().add_run()
for kind, instruction in (("begin", None), (None, 'TOC \\o "1-2" \\h \\z \\u'), ("end", None)):
    if kind:
        element = OxmlElement("w:fldChar")
        element.set(qn("w:fldCharType"), kind)
    else:
        element = OxmlElement("w:instrText")
        element.set(qn("xml:space"), "preserve")
        element.text = instruction
    field._r.append(element)
doc.add_page_break()

# ---------- 1 概述 ----------
h1("1 概述")
h2("1.1 要解决的问题")
p("约九成老人居家养老，日常照护的压力落在社区这一层。赛题指出四类具体问题：档案散且更新慢、派单凭经验且过程无留痕、健康数据成孤岛、结算靠手工。颐护通把这些环节放到一个平台上，并让每个环节都留下可核验的记录：谁上门、几点到、做了什么、做得怎么样、费用由谁承担。")
h2("1.2 设计目标与原则")
bullets([
    ("流程不可绕过", "工单、预警、投诉、账单都由状态机控制，状态变更只有一个入口，跳步返回 409。"),
    ("算法可解释", "派单分数列出每一项的原始值、归一值、权重和贡献分；预警保存规则快照；统计口径写在代码和本文里。"),
    ("权限落到数据", "功能权限之外，所有查询都经过数据范围过滤，跨范围访问一律返回 404，不透露记录是否存在。"),
    ("超时有人管", "接单、预警、回访、签到都有定时任务兜底，超时自动顺延或升级，并留痕。"),
    ("时间可驱动", "业务代码统一从 clock.now() 取时间，测试可以冻结时间，演示可以快进，超时逻辑都能验证。"),
])
h2("1.3 角色与数据范围")
table(["角色", "数据范围", "主要职责"], [
    ["老人与家属", "本人（绑定的老人）", "预约、查看进度和证据链、评价、投诉、看健康记录和账单、一键呼救"],
    ["护理员", "本人（承接或被邀约的工单）", "接单拒单、签到、清单、照片、签退、上报异常、离线作业"],
    ["社区网格员", "本网格", "受理、派单改派、异常处置、预警处置、随访录入、时长抽查、账单核对"],
    ["社区管理员", "本社区", "回访、投诉指派和结论、账单生成和复核、缴费登记、预警规则"],
    ["服务机构管理员", "本机构", "服务目录和调价、护理员管理、投诉说明、绩效、账单确认"],
    ["街道监管员", "全街道（只读为主）", "各社区看板、账单抽查、升级预警督办、审计查询"],
    ["系统超级管理员", "全街道", "用户、权限、业务参数、备份恢复、定时任务。默认没有查看敏感明文的权限"],
], [3.2, 3.8, 9.4])
p("赛题流程中多处提到“社区管理员”，角色清单里没有单列。这里增设社区管理员角色，数据范围为本社区，介于网格和街道之间。这样回访、投诉结论和账单复核有明确的责任人，也和网格员的日常操作权限分开。")

# ---------- 2 架构 ----------
h1("2 总体架构")
figure(IMG / "architecture.png", "图 2-1 系统分层架构")
h2("2.1 分层与模块边界")
table(["层", "目录", "职责", "约束"], [
    ["API", "app/api", "路由、请求校验、权限声明、事务提交", "不写业务逻辑"],
    ["服务", "app/services", "业务编排、数据范围、审计、消息", "只抛业务异常，不依赖 HTTP"],
    ["领域", "app/domain", "评分、判定、计费、统计、状态机、NLU", "纯函数，不依赖数据库"],
    ["模型", "app/models", "实体和约束", "敏感字段使用透明加密类型"],
    ["任务", "app/jobs", "定时任务注册和执行记录", "任务逻辑可在测试中直接调用"],
    ["核心", "app/core、app/db", "配置、时钟、加密、会话、触发器", "无业务含义"],
], [1.6, 2.8, 7, 5])
h2("2.2 技术选型与取舍")
table(["选择", "理由", "放弃的方案"], [
    ["FastAPI + SQLAlchemy 2", "类型清晰，自带接口文档，同步模型足够应付社区规模", "Django：自带后台很方便，但角色和数据范围要大量定制，反而绕路"],
    ["SQLite（WAL）", "零运维，评委解压即跑，单社区或单街道规模够用。代码只用通用 SQL，迁移走 Alembic", "PostgreSQL：并发更强，但演示和部署门槛高。备份恢复目前只实现了 SQLite 版本"],
    ["APScheduler 进程内调度", "不需要额外组件，任务记录写入数据库", "Celery + Redis：多一套基础设施，当前规模不需要"],
    ["Vue 3 + Vite，构建产物随代码提交", "评委不装 Node 也能运行；严格 CSP 下不需要 unsafe-eval", "服务端模板：做不了离线和移动端体验"],
    ["金额用整数分", "彻底避免浮点误差，三方分摊按分守恒", "Decimal 存库：SQLite 不原生支持"],
    ["预约单和工单合为一个实体", "赛题要求的八个状态本来就是一条状态链，拆开会多一层同步", "两张表：只有预约单和工单生命周期明显不同时才值得"],
], [4.2, 6.6, 5.6])
h2("2.3 事务与并发")
p("写请求用 BEGIN IMMEDIATE 先拿写锁，避免读事务升级写事务时出现 SQLITE_BUSY。GET 请求约定永不写库，走普通事务，在 WAL 模式下不阻塞写者。提交由路由函数显式调用，异常时统一回滚，失败的操作不留半截状态。批处理（批量导入、离线同步、候选顺延）用 SAVEPOINT 隔离单条失败。")

# ---------- 3 数据模型 ----------
h1("3 数据模型")
figure(IMG / "er.png", "图 3-1 主要实体及关系（共 48 张表，含关联表）")
h2("3.1 关键设计")
bullets([
    ("价格版本", "PriceVersion 只增不改，有效区间 [生效, 失效)。工单创建时按服务发生时点绑定版本，之后调价不影响这张工单和已结算金额。不允许把新价格的生效时间设在过去。"),
    ("工单状态事件", "OrderEvent 记录每次流转的操作人、前后状态和说明，工单详情的“全程留痕”就来自这里。"),
    ("时段锁", "CaregiverSlot 把在途工单切成 15 分钟时间片，对（护理员，时间片）加唯一约束。区间重叠无法用普通唯一索引表达，先查后写在并发下有竞态；切片之后，两张重叠工单必然争用同一时间片，由数据库保证不冲突。"),
    ("派单快照", "DispatchRound 保存当时的权重、全部候选的打分明细和被过滤者的原因。之后调权重不影响历史解释，超时顺延也从这份名单往下走。"),
    ("预警规则快照", "Alert 保存触发时的规则内容，规则修改后历史预警的判定依据不变。"),
    ("健康数据窄表", "HealthRecord 一行一个指标，新增指标不用改表。（老人，指标，时间，来源）唯一，设备重传天然幂等。"),
    ("红冲", "退费新增一条负数明细，指向原明细，计入当前月账单，从不修改已生效账单。"),
    ("冗余网格和社区", "工单冗余 grid_id、community_id。老人搬迁后，历史工单和统计仍归发生时的网格。"),
])
h2("3.2 档案字段对照")
table(["赛题要求", "字段", "存储方式"], [
    ["老人编号、姓名、性别、出生日期", "code、name、gender、birth_date", "明文，编号自动生成"],
    ["身份证号、联系电话、详细住址", "id_card、phone、address", "AES-256-GCM 密文；身份证和手机号另存 HMAC 盲索引，支持精确检索和查重"],
    ["所属社区网格", "grid_id", "外键，社区经网格关联"],
    ["紧急联系人及关系", "emergency_name、emergency_relation、emergency_phone", "电话加密"],
    ["医保与长护险类型", "medical_insurance、ltc_insurance", "枚举文本"],
    ["慢性病、用药清单、过敏史", "chronic_diseases、medications、allergies", "JSON、文本"],
    ["失能等级、照护标签", "disability_level、care_tags", "四级枚举、JSON 数组"],
    ["困难类型（结算需要）", "difficulty_type", "无、低保、特困"],
    ["逻辑删除", "is_deleted、deleted_at、deleted_reason", "必须填写原因，可恢复"],
], [5, 5.6, 5.8])

# ---------- 4 流程 ----------
h1("4 业务流程")
h2("4.1 工单状态机")
figure(IMG / "state.png", "图 4-1 工单状态流转")
h2("4.2 服务预约与智能派单")
bullets([
    "家属提交预约，系统校验：前后 2 小时是否已有工单、地址是否在社区服务半径内、项目和能力等级是否适配。",
    "不满足时不拒绝，而是写明原因并转人工受理，人工受理必须填写依据。服务项目下架、时间已过等硬错误直接拒绝。",
    "网格员受理后进入待派单，按优先级排序（失能等级、独居、失智、卧床等加权），高风险老人排在前面。",
    "系统给出前 3 名及打分明细，网格员选人或一键派单。派出即占用时段，护理员须在 30 分钟内响应。",
    "接单后工单进入已派单，结果推送家属和网格员。拒单必须填写理由，连续 3 次提醒网格员和机构。",
    "超时或拒单自动顺延下一候选并提醒网格员，候选用尽转人工协调。手工改派强制填写原因并写审计。",
])
figure(SHOT / "grid-dispatch.png", "图 4-2 智能派单：前 3 名、逐项得分和位置示意", 15)
h2("4.3 上门服务执行")
bullets([
    "预约时间前 15 分钟提醒护理员；超过预约时间 60 分钟未签到，提醒网格员。",
    "签到记录时间和坐标，距服务地址超过 200 米必须填写说明。签到时间晚于预约时间 5 分钟以上，记为迟到，计入准时率。",
    "按清单勾选，上传不超过 3 张照片。照片校验格式、缩放到长边 1280、重编码为 JPEG，同时清除 EXIF 中的定位信息。",
    "签退必须填写小结，有未完成条目时必须说明。系统计算时长，偏差超过 30 分钟标记时长异常，等待网格员抽查。",
    "老人不在家、拒绝服务、突发不适等进入异常队列并计算处置时长。突发不适直接触发红色预警，不等人工转交。",
])
h2("4.4 健康预警与应急处置")
bullets([
    "数据来源：上门随访、服务签退附带、可穿戴网关批量上报（设备密钥鉴权，提供模拟脚本）。超出生理合理范围的数值拒收，不触发预警。",
    "每条数据逐条判定。同一老人同一规则已有未闭环预警时只累加次数，不重复打扰。",
    "红色：站内加短信，推送网格员、紧急联系人、签约医生，看板置顶。短信不含具体体征数值。黄色：站内推网格员和家属。蓝色：站内推网格员。",
    "红色 30 分钟未响应升级街道，黄色 4 小时、蓝色 24 小时升级社区，时限可配置。升级写时间线和审计。",
    "闭环结论只有三种，各有必填项：已处置（措施）、误报（原因）、已转诊（医院和时间）。全过程时间用于计算响应中位数。",
])
figure(SHOT / "grid-alert-detail.png", "图 4-3 预警详情与处置时间线", 15)
h2("4.5 服务评价与投诉")
bullets([
    "完成后推送评价邀请，24 小时内可评。四项 1 至 5 星，综合分取平均。",
    "综合分不高于 3 星自动生成回访任务，48 小时内处理，超时提醒街道。回访结果为服务改进或转投诉。",
    "投诉流程：待指派 → 机构说明 → 社区结论，不能跳过。结论成立可退费、更换护理员、计扣分。",
    "更换护理员后，今后不再给这位老人派该护理员，未开始的工单退回待派单。",
    "同一护理员当月 3 次投诉成立，列入重点关注，当月派单分数乘以 0.8。",
])
h2("4.6 费用结算与补贴核算")
bullets([
    "每月 1 日 0:30 汇总上月账单，已生成的月份跳过，任务重跑安全。",
    "四类差异校验：同一时段重复计费、月度补贴超限、未完成却计费、时长超过标准 3 倍。命中的明细被拦截，不计入合计。",
    "差异逐条说明和处理，不同类型可选的处理方式不同（例如超限可以截断到上限，未完成只能剔除）。",
    "差异金额占账单总额超过 1% 时，须处理全部差异并人工复核后才能提交。比例在生成时冻结，不因后续处理变化。",
    "网格员核对 → 涉及的每家机构确认 → 监管按不低于 10% 抽查（向上取整，种子落库）→ 生效，生成老人缴费单，缴费状态回写工单。",
    "抽查不通过：明细转入差异清单，账单退回，重走确认流程。",
])
figure(SHOT / "comm-bill-detail.png", "图 4-4 账单详情与对账差异清单", 15)
h2("4.7 数据备份与恢复")
bullets([
    "每日 2:00 全量备份：用 SQLite 在线备份 API 得到一致性快照，连同附件打包，附 manifest（文件哈希、各表行数、关键指标）。按日期命名，放在独立目录，保留最近 30 个版本。",
    "恢复分两步。预览：校验整包哈希、逐文件哈希和数据库完整性，展示受影响的表，发放 5 分钟有效、绑定操作人的令牌。确认：校验令牌、本人口令和确认词。",
    "执行时进入维护模式，拒绝业务请求、暂停定时任务；先自动做一次恢复前快照；写回数据库和附件；把恢复点之后的审计日志原样接回，恢复操作抹不掉审计；最后写入恢复审计。",
    "输出核对报告：各表行数和关键指标在恢复前、备份、恢复后的对比。",
    "按时间点恢复的含义：选择目标时间点之前最近的一份备份。没有增量日志，恢复粒度是备份版本。",
])

# ---------- 5 算法 ----------
h1("5 关键算法")
h2("5.1 派单加权评分")
figure(IMG / "dispatch.png", "图 5-1 派单决策流程")
code("推荐分 = (w技能·S技能 + w距离·S距离 + w负荷·S负荷 + w评分·S评分) × 重点关注系数\n"
     "S技能 = 命中所需技能数 / 所需技能数\n"
     "S距离 = 1 − 直线距离 / 最大半径（默认 3 公里，Haversine 公式）\n"
     "S负荷 = 1 − 当日已派单数 / 单日上限（默认 6 单，含未响应的邀约）\n"
     "S评分 = (平滑评分 − 1) / 4，平滑评分 = (4.0×5 + 近30天评分总和) / (5 + 评价次数)")
p("评分用贝叶斯平滑，是因为新护理员样本少：只有 1 单 5 星的人，不应该排在几百单 4.8 星的人前面。默认权重 0.4、0.3、0.2、0.1 可在后台修改，保存时校验并自动归一。排序为分数降序，同分取距离近者，再按编号，结果确定、可复现。")
p("算例（图 4-2 第 2 名）：技能 2/2 项，距离 0.58 公里，当日 0 单，平滑评分 4.45 星。推荐分 = 0.4×1.00 + 0.3×0.81 + 0.2×1.00 + 0.1×0.86 = 0.928，界面显示 92.8。")
h2("5.2 预警规则")
table(["类型", "判定", "默认示例", "边界处理"], [
    ["单项越界", "最新值与阈值比较", "收缩压 ≥ 180 或 ≤ 90、血氧 < 90、血糖 ≥ 16.7 或 ≤ 3.9：红", "180 触发，179.9 不触发"],
    ["连续异常", "最近 N 次全部越界", "连续 3 次心率 > 100：黄", "不足 N 次不触发；中间有一次正常则中断"],
    ["长时间无数据", "距最后一次上报的时长", "48 小时：蓝", "从未上报的按建档时间算；闭环后一个周期内不重复触发"],
    ["个体基线偏离", "稳健 Z = (当前值 − 中位数) / (1.4826×MAD)", "|Z| ≥ 3.5：蓝", "样本少于 10 个不判定；MAD 为 0 时分母取 1；越界已报警时不叠加"],
    ["人工触发", "SOS、现场突发不适", "红", "—"],
], [2.6, 4.8, 5, 4])
p("规则存数据库，指标、比较符、阈值、窗口、级别都可以修改，改完立即生效。个体基线的意义在于：同样心率 95，对平时 60 的老人是异常，对平时 90 的老人是正常，固定阈值覆盖不到。")
h2("5.3 三方分摊")
code("政府比例 = min(项目补贴比例, 政策比例)    项目未纳入补贴时为 0\n"
     "长护比例 = min(政策长护比例, 1 − 政府比例)  未参保时为 0\n"
     "政府补贴 = 四舍五入(金额 × 政府比例)\n"
     "长护险 = 四舍五入(金额 × 长护比例)\n"
     "个人自付 = 金额 − 政府补贴 − 长护险")
p("个人自付由减法得出，不单独舍入，所以三方之和恒等于总额。政策按失能等级 × 困难类型取值，共 12 档，可配置。按小时计价的项目，时长按 15 分钟向上取整。迟取消和爽约费默认为服务费的 30%，全部由个人承担。")
p("算例：中度失能、参保长护险、无困难的老人，助浴 80 元。项目比例 50%，政策比例 30%，政府比例取 30%。政府补贴 24 元，长护险 24 元，个人自付 32 元。")
h2("5.4 统计口径")
table(["指标", "口径"], [
    ["服务量", "统计区间内完成（完成时间落在区间）的工单数"],
    ["覆盖率", "区间内至少完成 1 单服务的老人数 / 当前有效档案老人数"],
    ["环比", "(本期 − 基期) / 基期。整自然月对比上一整月，其他区间取等长的紧邻前一段"],
    ["同比", "同样公式，基期为去年同期。2 月 29 日回退到 2 月 28 日"],
    ["基期为 0 或缺失", "显示“—”，不显示无穷大或 100%"],
    ["预警响应中位数", "首次响应时间 − 触发时间的中位数。用中位数避免个别极端值拉偏均值"],
    ["预警及时率", "时限内响应数 / (已响应数 + 已超时未响应数)，未到时限的不计入"],
    ["准时率", "准时签到工单数 / 已签到工单数，容忍 5 分钟"],
    ["异常率", "异常单数 / (完成工单数 + 异常单数)"],
    ["收入、补贴", "费用明细合计，含退费红冲，不含被剔除的明细"],
], [4, 12.4])
figure(SHOT / "comm-dashboard.png", "图 5-2 社区看板：KPI 环比同比，趋势、占比、排行、分布四类图表", 15)
h2("5.5 自然语言预约")
p("确定性规则抽取服务项目（同义词表）和时间（今天、后天、周几、每周、几月几号、上午下午、几点半），只负责预填表单，提交前由用户确认。配置大模型后，本地解析不全时才调用模型补全；发给模型的只有用户这句话，健康摘要只发统计值，不发姓名、证件号和地址。")

# ---------- 6 安全 ----------
h1("6 权限与数据安全")
h2("6.1 RBAC 叠加数据范围")
p("路由用 require(perm) 声明功能权限，权限矩阵存数据库，超级管理员可在后台修改，立即生效并写审计。服务层所有列表查询经过 scope_* 过滤，单条访问经过 ensure_*，这是访问数据的唯一入口。跨范围访问统一返回 404，不透露记录是否存在。写操作另外校验目标网格，防止把档案写到别的网格。")
figure(SHOT / "admin-roles.png", "图 6-1 角色权限矩阵", 15)
h2("6.2 敏感字段")
table(["措施", "实现"], [
    ["加密存储", "AES-256-GCM，每次随机 nonce，AAD 绑定字段用途，密文搬到别的字段无法解密。ORM 类型透明加解密"],
    ["检索", "HMAC-SHA256 盲索引，支持身份证、手机号精确检索和查重，库里不留明文"],
    ["默认脱敏", "接口默认只返回脱敏值：138****5678、510***********0021、青松街道青松****"],
    ["二次授权", "查看完整信息要有权限、在数据范围内，还要填写理由并重新输入本人口令。成功和失败都留痕"],
    ["最小必要", "护理员只有承接工单后才看到完整地址；超级管理员默认无权查看明文；短信不含体征；变更日志和审计里敏感值只记脱敏形式"],
    ["密钥管理", "密钥来自环境变量，生产环境缺失时拒绝启动。开发环境首次启动随机生成，不写入代码仓库"],
], [3, 13.4])
h2("6.3 审计日志")
bullets([
    "覆盖登录、导出、修改档案、改派、退费、删除、查看敏感信息、调价、改配置、改权限、备份恢复、预警升级等关键操作。",
    "数据库触发器拒绝 UPDATE，以及 180 天保留期内的 DELETE。接口层也没有修改审计的路由。",
    "HMAC 哈希链：每条包含前一条的哈希。绕过触发器直接改库，校验接口也能指出断裂位置和原因。测试里实际删除触发器并篡改数据，确认能被发现。",
    "保留天数可配置，不得低于 180 天。",
])
h2("6.4 其他防护")
bullets([
    "bcrypt 口令；连续 5 次失败锁定 15 分钟；账号不存在时也计算一次哈希，防止通过响应时间枚举账号；改密、停用立刻作废旧令牌。",
    "CSP 禁止内联脚本，加上 nosniff、禁止嵌入等响应头。",
    "上传校验文件内容而非扩展名，重编码后存储；照片访问走鉴权接口。导出做公式注入防护。备份文件名防路径穿越，解压防 zip slip。",
    "容器以非 root 用户运行，文件系统只读，no-new-privileges。",
])

# ---------- 界面设计 ----------
h1("7 界面设计")
p("这是一个每天要用的值班工具，用户里还有视力下降的老人。界面的目标是看着舒服、一眼知道先做什么，不追求炫目。")
table(["决定", "理由"], [
    ["主题色用靛蓝 #25518F，点缀暖阳黄 #F2A93B", "绿色已经表示“已完成”，红、黄表示预警，主题色不能和它们抢语义。靛蓝取自蓝印花布，有公共服务的可信感，白字对比度达到 AA。蓝色预警改为偏青的湖蓝并始终带文字。"],
    ["标志：屋檐、暖阳、托举的弧线", "对应居家、照护和托底的含义。字样用思源宋体轮廓，不依赖运行环境的字体。"],
    ["进入动画约 1.8 秒：社区天亮", "夜色里窗户亮起，太阳升起，楼房变亮，标志飞到左上角。动画终点和登录页是同一幅画，揭开时看不到切换。每个会话只播一次，可点击跳过，尊重减少动效设置。"],
    ["登录页是一整幅清晨的社区", "不做左右硬切分，表单放在画面里，页面没有生硬的接缝。"],
    ["工作台是值班交接单，不是 KPI 方块墙", "开头一句话说清还有什么要做，红色预警单独置顶，待办按紧急程度排列，其他信息收在右侧。"],
    ["层次靠底色和留白，不靠边框和阴影", "导航和背景连成一片，内容是一整张白纸。状态用圆点加文字，颜色只传达状态。"],
    ["动效只用于进入和用户操作", "页面切换只做短淡入，弹窗轻微缩放，不做逐块上滑。"],
], [5.6, 10.8])
figure(SHOT / "login.png", "图 7-1 登录页", 15)
figure(SHOT / "grid-workbench.png", "图 7-2 网格员工作台", 15)
p("图表分类色按固定顺序使用，经色盲可分性校验（相邻色差 ΔE ≥ 8.7），超过 6 类时小项并入“其他”。")

# ---------- 8 加分项 ----------
h1("8 加分项")
table(["项目", "实质内容", "验证方式"], [
    ["容器化部署", "多阶段 Dockerfile（构建前端、运行后端），docker-compose，健康检查，非 root，只读文件系统，数据和备份分卷", "本机构建镜像，并以生产模式启动容器，登录和业务接口正常"],
    ["人工智能辅助", "自然语言预约；个体基线异常检测；可解释的巡访风险分；30 天趋势分析和健康摘要。全部有本地实现，可选接入 OpenAI 兼容接口。只做辅助，不替人决定", "单元测试，含大模型失败回退、发送内容不含身份信息"],
    ["适老化设计", "老人账号默认大字高对比；一键呼救；大按钮少步骤；页面朗读；语音输入预约；键盘焦点清晰；尊重减少动效设置", "浏览器验收。语音输入和朗读依赖浏览器能力，没有在真机上实测"],
    ["离线同步", "Service Worker 预缓存全部页面；工单包和操作队列存 IndexedDB；操作记录实际发生时间；按 op_id 幂等；单条失败隔离并告知冲突", "真实浏览器断网走完签到到签退，联网后核对服务端状态"],
], [2.8, 8.6, 5])
figure(SHOT / "family-home.png", "图 8-1 老人与家属端首页（大字模式）", 8.5)

# ---------- 8 测试 ----------
h1("9 测试结论")
p("详细数据见 docs/测试报告.md。")
table(["层次", "内容", "结果"], [
    ["后端自动化测试", "算法单元、接口、权限矩阵、越权、并发、备份恢复、端到端旅程", "484 个，全部通过，用时约 108 秒"],
    ["代码覆盖率", "pytest-cov，不含演示数据和命令行工具", "总体 92%；派单评分、预警判定、计费、状态机 100%"],
    ["代码检查", "Ruff（含安全规则）", "无告警"],
    ["迁移一致性", "alembic check：模型和迁移脚本对比", "无差异"],
    ["真实浏览器验收", "Playwright 驱动 Edge，七类账号走完全部流程，含断网", "23 步全部通过，控制台错误和接口 5xx 为 0"],
    ["容器", "构建镜像，生产模式启动，登录和业务接口", "通过"],
], [3.6, 8, 4.8])
p("测试实际发现并修复的缺陷，举三例：抽查证据含日期时间对象，写入 JSON 列时报错；备份包损坏时校验接口抛出未捕获的解压异常，改为返回校验失败；离线操作时间精确到秒，同一秒内的签到和签退可能乱序，增加客户端序号后解决。")
p("没有做的测试：没有做压力测试，并发只验证了派单冲突场景；短信是模拟网关，没有接入真实运营商；没有用真实可穿戴设备，用脚本模拟上报；手机上的真实定位和语音输入没有实测。")

# ---------- 9 需求对照 ----------
h1("10 需求与评分项对照")
h2("10.1 十项开发需求")
table(["需求", "实现位置", "测试"], [
    ["1 档案与能力评估", "elder_service、Elders、ElderDetail", "test_elders_catalog"],
    ["2 服务目录与价格版本", "catalog_service、Catalog", "test_elders_catalog"],
    ["3 预约与受理", "order_service、FamilyBook、Workbench", "test_orders_dispatch"],
    ["4 智能派单", "dispatch_scoring、dispatch_service、DispatchDesk", "test_domain_algorithms、test_orders_dispatch"],
    ["5 上门执行留痕", "execution_service、CaregiverExecute、Exceptions", "test_execution_sync"],
    ["6 健康数据与预警", "alert_rules、health_service、Alerts、simulate_wearable", "test_health_alerts"],
    ["7 评价与投诉", "quality_service、Quality、Performance", "test_quality_billing"],
    ["8 结算与补贴", "billing_calc、billing_service、Bills、BillDetail", "test_quality_billing"],
    ["9 统计看板", "stats_calc、stats_service、Dashboard", "test_security_stats_ops"],
    ["10 权限审计备份", "access、audit_service、backup_service、Audit、AdminBackups", "test_security_stats_ops"],
], [5, 7.4, 4])
h2("10.2 边界情形处理（节选）")
table(["情形", "处理"], [
    ["开始前正好 2 小时取消", "不计费；不足 2 小时且护理员已接单才计费；尚未派单成功不计费"],
    ["时长正好是标准的 3 倍 / 偏差正好 30 分钟", "不算异常，超过才算"],
    ["预约时调价", "按服务时点绑定版本，已有工单价格不变"],
    ["全部候选拒单或超时", "转人工协调，提醒网格员"],
    ["改派到有冲突的护理员", "拦截，回滚，原派单不受影响"],
    ["设备重传、离线重试、任务重跑", "唯一约束或幂等表保证只生效一次"],
    ["离线操作超过 72 小时、时间晚于服务器", "拒收并说明原因，转人工补录"],
    ["离线期间工单被改派", "该条明确拒绝并告知，其他操作继续"],
    ["短信网关故障", "预警照常生成，发件箱重试 5 次后标记失败"],
    ["同一明细命中多条差异", "全部处理完才放行"],
    ["备份损坏", "校验失败，禁止恢复"],
    ["管理员取消自己的权限管理能力", "拒绝，避免把自己锁在外面"],
], [6.5, 9.9])

# ---------- 10 部署 ----------
h1("11 部署与运行")
p("Windows 执行 start.ps1，Linux 或 macOS 执行 start.sh，会创建虚拟环境、安装依赖、生成密钥、迁移数据库并写入演示数据，然后打开 http://127.0.0.1:8000。也可以用 docker compose up -d --build。配置项见 .env.example，业务参数在后台修改。")
p("必须备份 .env 中的字段加密密钥：密钥丢失后，身份证、手机号、地址无法解密。备份文件本身不含密钥。")

# ---------- 11 局限 ----------
h1("12 已知局限与后续方向")
bullets([
    "SQLite 适合单街道规模。多街道或高并发需要切换 PostgreSQL，届时要把备份恢复改用数据库原生工具，并把审计触发器改写为对应语法。",
    "调度器在进程内，只支持单实例部署。多实例需要把任务拆到独立进程。",
    "距离用直线距离（赛题要求），没有接入路径规划。",
    "按时间点恢复精确到备份版本，没有增量日志，两次备份之间的数据无法恢复。",
    "短信、定位来源、可穿戴网关都留了适配边界，真实接入还要联调。",
    "风险分和健康摘要是排序和提示参考，没有经过临床验证，界面已明确标注不是医学诊断。",
])

# ---------- 12 第三方 ----------
h1("13 第三方组件声明")
p("后端：FastAPI、Uvicorn、SQLAlchemy、Alembic、Pydantic、PyJWT、bcrypt、cryptography、Pillow、openpyxl、python-multipart、APScheduler、httpx。前端：Vue、Vue Router、Apache ECharts、Vite。均通过包管理器引用，许可证和来源见 THIRD_PARTY.md。派单、预警、结算、审计、备份恢复等核心业务逻辑没有套用现成同类系统。")
p("演示数据中的人名、证件号、手机号均由脚本随机生成，不对应真实人员。")

output = HERE.parent / "设计文档.docx"
doc.core_properties.title = "颐护通设计文档"
doc.core_properties.author = ""
doc.save(output)
print("written", output)
