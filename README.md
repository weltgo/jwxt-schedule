# 课表获取器（jwxt-schedule）

从学校教务系统抓取个人课表，统一成结构化数据，再导出 **JSON / CSV / iCal**。
项目采用“适配器 + 解析器 + 导出器”的结构，方便针对不同学校逐步适配。

> 仅用于获取**本人账号**的课表，请遵守学校教务系统的使用规定，不要高频请求、批量抓取或分享他人数据。

## 功能

- 自动探测教务系统类型，支持常见 **正方 jwglxt** 和简单的 **表单登录 + HTML 表格** 系统
- 支持验证码/统一身份认证场景：不绕过验证，改为导入浏览器 Cookie
- HTML 课表解析：兼容“周次 × 节次”网格表和“课程列表”表两种结构
- 导出 JSON、CSV、iCalendar（可直接导入系统日历）
- 请求限速、Cookie 会话缓存、密码只从环境变量/命令读取
- `parse` 命令可离线解析浏览器“另存为”的课表页面

## 安装

> 请不要使用 `sudo pip install` 或全局 `pip install`。依赖应装在
> 虚拟环境、conda 环境或 pipx 隔离环境中。

```bash
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
python -m pip install -e .

# 不想安装也可以直接在项目目录运行
python -m jwxt --help
```

如果系统里已经有 pipx，也可以隔离安装 CLI：

```bash
pipx install .
```

正方 AES/RSA 登录需要 `cryptography`（可选依赖）：

```bash
python -m pip install -e ".[crypto]"
```

要求 Python 3.11+。核心依赖：`httpx`、`beautifulsoup4`、`lxml`、`pydantic`。

## 快速开始

### 推荐：直接运行交互向导

```bash
jwxt          # 安装后；或 python -m jwxt
```

向导会依次询问少量必要信息：

```text
1. 教务系统地址（例如 https://jw.example.edu.cn）
2. 学号 / 用户名
3. 密码（不显示输入、不保存；直接回车可改用 Cookie 登录）
4. 学期名称（有默认值，直接回车即可）
5. 第一周周一（有默认值；输入 - 可跳过 iCal）
```

之后程序会自动探测系统类型、登录、抓取，并输出：

```text
schedule.json   结构化课表，方便二次处理
schedule.ics    可导入手机/电脑日历
schedule.csv    可用 Excel/WPS 打开
```

终端里还会直接显示一份按星期分组的课表。第一次会生成 `jwxt.toml`
（不含密码，权限 600），以后只需要：

```bash
jwxt fetch       # 自动读取 jwxt.toml 和上次登录会话
```

### 不想用向导时的一行命令

```bash
jwxt fetch https://jw.example.edu.cn --username 20250001
```

它会自动进入同样的登录流程；如果没有配置文件，会提示你补全
或改用 `jwxt setup`。

### 常用选项

`jwxt fetch --help` 默认只显示高频选项：

| 选项 | 说明 |
| --- | --- |
| `--config PATH` | 指定配置文件；不写则自动找 `./jwxt.toml` |
| `--username NAME` | 学号/用户名 |
| `--cookie-string '...'` | 手动提供 Cookie，适合验证码/SSO |
| `--cookie-file PATH` | 从文件读取 Cookie（每行 `name=value`，或 JSON 数组） |
| `--no-login` | 只用已有 Cookie/会话，不执行登录 |
| `--out PATH` | JSON 输出路径，默认 `schedule.json` |
| `--ics PATH` | iCal 输出路径，默认 `schedule.ics` |
| `--csv PATH` | CSV 输出路径，默认 `schedule.csv` |
| `--no-ics` / `--no-csv` | 不导出对应格式 |
| `--print` | 额外在终端打印 JSON |
| `--debug` | 出错时打印完整堆栈 |

高级参数（`--type`、`--schedule-path`、`--term-name`、`--start-date`、
`--xnm`、`--xqm`、`--min-interval`、`--proxy` 等）仍然可用，
但默认不出现在 `--help` 里；需要时可直接传，效果与以前一致。

### 离线解析已保存的 HTML

有些系统前端渲染复杂，但课表所在 HTML 是完整的：

1. 在浏览器打开课表页；
2. `Ctrl+S` 保存为“网页，仅 HTML”；
3. 执行：

```bash
jwxt parse 课表.html \
  --term-name 2025-2026-1 \
  --start-date 2025-09-01 \
  --ics schedule.ics
```

### 从已保存的 JSON 再导出

```bash
jwxt export schedule.json --format ics --config jwxt.toml
```

### 最小配置示例

正常情况下向导会处理好配置，不需要手写。需要定制时再编辑 `jwxt.toml`：

```toml
[account]
username = "你的学号"
password_env = "JWXT_PASSWORD"

[system]
type = "auto"                       # auto / zhengfang / generic_html
base_url = "https://jw.example.edu.cn"

[http]
session_file = "~/.config/jwxt/xxx.cookies.json"
min_interval = 1.2

[term]
name = "2025-2026-1"
start_date = 2025-09-01            # 第一周周一，导出 iCal 需要
weeks_count = 20
```

密码建议只放环境变量：

```bash
export JWXT_PASSWORD='你的密码'
```


强智、URP 等其他教务系统暂未内置专用 adapter，但 `probe` 会给出线索；
可按“情况 C”基于实际接口快速扩展。

## 配置说明

| 配置项 | 说明 |
| --- | --- |
| `system.type` | `auto`（默认）、`zhengfang`、`generic_html` |
| `system.base_url` | 教务系统地址，可包含 `/jwglxt` 子路径 |
| `system.login_path` | 登录页路径/URL；留空时由 adapter 决定 |
| `system.schedule_path` | 课表页/接口路径；`generic_html` 必填 |
| `system.xnm` / `xqm` | 正方学年和学期代码（`3`=第一学期，`12`=第二学期） |
| `system.extra_login_fields` | 学校定制的额外登录表单字段 |
| `system.schedule_method` | 取课表请求方法，默认 `GET`，可设 `POST` |
| `system.schedule_data` | 取课表 POST 表单参数（表）|
| `account.password_env` | 密码所在环境变量名，默认 `JWXT_PASSWORD` |
| `account.password_cmd` | 通过命令读取密码，例如 `pass show school/jwxt` |
| `account.cookie_string` | 直接提供 Cookie，适合 SSO/验证码场景 |
| `account.cookie_file` | 从文件读取 Cookie |
| `http.session_file` | Cookie 会话缓存路径（权限自动设为 600） |
| `http.min_interval` | 请求最小间隔，默认 1.2 秒 |
| `http.verify` | 是否校验 TLS 证书，默认 `true` |
| `http.proxy` / `trust_env` | 代理设置；`trust_env=false` 可忽略环境变量代理 |
| `term.start_date` | 第一周周一，导出 iCal 必需 |
| `term.weeks_count` | 学期总周数，配合 `ics_assume_all_weeks = true` 使用 |
| `[[periods]]` | 每节课的起止时间，供 HTML/iCal 使用 |

自定义 `generic_html` 系统的 POST 取数示例：

```toml
[system]
type = "generic_html"
base_url = "https://你的学校域名"
login_path = "/login"
schedule_path = "/schedule/list"
schedule_method = "POST"

[system.schedule_data]
xnm = "2025"
xqm = "3"
```

## 项目结构

```text
jwxt/
├── cli.py                 # 命令行入口（默认进入交互向导）
├── wizard.py              # 交互式配置/抓取向导
├── workflow.py            # 抓取、重试、导出共享流程
├── display.py             # 终端课表展示
├── config.py              # TOML 配置与密码读取
├── http.py                # 会话持久化、限速、Cookie
├── models.py              # Course / Schedule 统一模型
├── parsing.py             # 星期、节次、周次、单元格文本解析
├── tables.py              # HTML 网格表/列表表解析
├── crypto.py              # AES / RSA 登录加密
├── adapters/
│   ├── base.py            # adapter 接口与错误类型
│   ├── generic_html.py    # 通用表单登录 + HTML 课表
│   └── zhengfang.py       # 正方 jwglxt
└── exporters/
    ├── ics.py             # iCalendar 导出
    └── csv_out.py         # CSV 导出
```

## 适配自己学校的方法

### 情况 A：正方系统

1. 用浏览器登录正方，打开课表页，按 F12 → Network；
2. 找到 `xskbcx_cxXsKb.html` 请求，确认响应是 JSON（含 `kbList`）还是 HTML；
3. 在 `jwxt.toml` 中设置：

```toml
[system]
type = "zhengfang"
base_url = "https://你的学校域名/jwglxt"
```

4. 如果学校用了统一身份认证/验证码，直接在浏览器登录后复制 Cookie：

```bash
jwxt fetch --config jwxt.toml --cookie-string 'JSESSIONID=xxx; route=xxx' --no-login
```

### 情况 B：简单表单系统

1. 打开登录页，保存为 HTML；查看表单里用户名、密码输入框的 `name`；
2. 打开课表页，保存在 `examples/` 下；
3. 设置：

```toml
[system]
type = "generic_html"
base_url = "https://你的学校域名"
login_path = "/login"
schedule_path = "/schedule/page"
```

4. 先用 `jwxt parse 保存的课表.html` 验证解析器能否识别；
5. 若取数需要 POST 或额外参数，用 `system.schedule_method`、`[system.schedule_data]` 扩展。

### 情况 C：完全定制/前后端分离

实现一个新的 adapter：

```python
from jwxt.adapters.base import BaseAdapter
from jwxt.models import Course, Schedule


class MySchoolAdapter(BaseAdapter):
    name = "myschool"

    def login(self, client, config):
        # 按学校接口提交登录、处理 token
        ...

    def fetch(self, client, config):
        data = client.get("/api/schedule").json()
        courses = [
            Course(
                name=item["courseName"],
                teacher=item["teacher"],
                location=item["room"],
                day=item["weekday"],
                start_period=item["start"],
                end_period=item["end"],
                weeks=item["weeks"],
            )
            for item in data["items"]
        ]
        return Schedule(term=config.term.name, courses=courses)
```

然后在 `jwxt/adapters/__init__.py` 的 `ADAPTERS` 中注册。
如果是接口结构已知、只是字段名不同，也可以基于 `ZhengfangAdapter` 复制修改，
通常只需要改 `_parse_kb_item` 和接口地址。

## 常见问题

**Q：提示“包含验证码，工具不自动识别”**
A：这是设计要求。验证码/滑块/短信验证不应用自动化绕过。
浏览器手动登录后，复制 Cookie 并使用 `--cookie-string`，或导出 Cookie 文件用 `--cookie-file`。

**Q：跳转到统一身份认证（CAS/SSO）**
A：同上，使用浏览器会话。如果学校支持原系统账号登录，也可以设置 `login_path` 指向原登录页。

**Q：登录成功但抓不到课表**
A：用 F12 看课表接口的 URL、方法和请求体，把接口地址填到 `system.schedule_path`；
如果是 JSON，需要按“情况 C”写一个简单的字段映射 adapter。

**Q：导出的 iCal 缺课**
A：检查配置里的 `term.start_date`、`[[periods]]`，以及课程是否解析出了周次和节次/时间。
没有周次的课程默认不导出，避免生成错误安排；确认可补全后可设置 `ics_assume_all_weeks = true`。

**Q：会不会泄露密码？**
A：程序不会把密码写入 JSON 或日志；配置模板只保存密码所在的环境变量名。
Cookie 会缓存在 `session_file`（默认无），权限设为 600，仍然请妥善保管。
- `--cookie-string` 可能留在 shell 历史或进程列表中，优先使用 `--cookie-file` 或配置文件。

## 开发与测试

```bash
python -m unittest discover -s tests -v
```

新增适配学校时，建议一并提供一份脱敏后的 HTML fixture 和单元测试，方便回归。

## 路线图

- [ ] Playwright adapter：处理纯 JS 渲染和 CAS 登录
- [ ] HAR 导入：从浏览器抓包文件自动生成 adapter 初稿
- [ ] 课表变化提醒（新增/调课/停课）
- [ ] 终端周课表视图 / 图片导出

## 合规提示

- 只抓取本人账号有权限访问的数据；
- 不绕过验证码、二次验证、访问控制；
- 遵守学校网络与信息系统管理规定以及相关法律法规；
- 控制请求频率，默认限速 1.2 秒，避免影响教务系统正常运行；
- 不将本工具用于代刷课、抢课、批量查询或数据倒卖。
