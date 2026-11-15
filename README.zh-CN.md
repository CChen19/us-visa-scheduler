# 武汉美国访客签证预约助手

[English](README.md)

本工具在本地 Windows 桌面运行，通过网站正常界面查询武汉 B1/B2 或 B2
面谈可预约日期，可选择对**已有预约进行至多一次受保护的改期提交**。
默认仅监控，必须明确开启自动改期。

**重要限制：**保存的登录页面明确禁止自动化访问。只有在服务提供方允许的
情况下使用自动化。任何检查间隔、浏览器模式或验证码辅助方法，都不能保证
不被限制、封锁，或保证通过人机验证。

## 功能

- 从 `https://www.usvisascheduling.com/zh-CN/SignIn` 进入，仅在预期的认证域名
  填写账号密码；按实际显示的问题文本匹配本地答案，支持 1/3、2/3 等问题组合。
- 在认证页面尝试一次兼容的 SeleniumBase UC GUI 人机验证操作。不能安全操作
  或未通过时，立即发邮件，暂停并等待用户在原浏览器中手动验证。
  不使用第三方验证码代答服务。
- 首页仅点击已验证的“重新安排预约”链接；检查签证类别、武汉领馆、申请人组
  以及已有预约的日期时间，不点击取消、关闭申请或创建新申请。
- 被动观察网站正常请求返回的完整可预约日期列表，不仅限于屏幕显示的两个月；
  不重放私有 API，不导出认证 Cookie 来加速查询。
- 按配置列表的顺序匹配闭区间日期范围；优先级最高且有日期的范围中选择最早日期。
  不把两个范围之间的间隔误认为可接受日期。
- 仅监控时绝不点击日期、时段或提交；自动改期时仅点击偏好日期，选择第一个
  可用且人数容量足够的时段，并在提交前再次校验。
- 在点击 Submit **之前**持久化不可自动重置的提交标记。观察结果 90 秒，
  发结果邮件、关闭本工具拥有的浏览器并停止。异常、重启或邮件失败均不能触发第二次提交。
- 对已识别的访问限制持久化冷却时间；初次失败后最多进行三轮恢复重试。
  其他未预期错误直接停止并通知。
- 记录事件、日期可用性、改期结果三类本地日志；发送偏好日期、每日状态、
  手动验证、限制/异常和改期结果邮件。

**不能保证：**自动通过验证码、绝不封号、服务端如何计算改期次数、原预约的
原子替换/保留、提交时该时段仍有名额、邮件最终进入收件箱。
无法确认的提交结果标记为 **UNKNOWN（未知）**，不会当作失败后重新提交。

## 安装（Windows PowerShell）

需要交互式 Windows 桌面及已安装的 Google Chrome。本次验证使用 Python 3.9.13；
锁定的 SeleniumBase 版本支持 Python 3.9 及以上。不要在监控过程中自动升级依赖。

```powershell
Set-Location C:\src\USVisa\us-visa-scheduler
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m pip check
```

不必激活虚拟环境。依赖锁定为 SeleniumBase 4.51.12（包含 PyAutoGUI extra）、
PyAutoGUI 0.9.54；SeleniumBase 根据 Python 版本选择对应的 Selenium。
首次启动可能下载匹配的 ChromeDriver，安装依赖/驱动需要网络。

## 配置 `main.py` 开头的设置

以下十六项优先读取环境变量；只有变量不存在时，才使用 `main.py` 对应
`os.environ.get(...)` 第二个参数中的默认值。修改默认值时请保留环境变量读取逻辑。
已存在的环境变量优先于文件修改。设置在 Python 启动时读取，修改外部终端不能改变已运行的进程。

环境变量是字符串。`build_config()` 在校验前转换布尔值、整数和 JSON。
已存在但格式错误（包括空的结构化值）的变量会明确报错，不会悄悄使用默认值。
特别是字符串 `"false"` **不会**开启自动提交。

| 属性 | 环境变量 | 格式 / 变量不存在时的默认值 |
|---|---|---|
| `USERNAME` | `VISA_USERNAME` | 字符串；默认为空。 |
| `PASSWORD` | `VISA_PASSWORD` | 签证账号密码；默认为空。 |
| `SECURITY_ANSWERS` | `VISA_SECURITY_ANSWERS` | JSON 问题到答案对象；默认使用文件中的映射，初始为空。整体替换，不合并。 |
| `EXPECTED_APPLICANTS` | `VISA_APPLICANTS` | 用分号 `;` 分隔准确姓名；默认空列表。 |
| `EXPECTED_VISA_CLASS` | `VISA_VISA_CLASS` | `B1/B2` 或 `B2`；默认 `B1/B2`。 |
| `EXPECTED_EXISTING_APPOINTMENT` | `VISA_EXISTING_APPOINTMENT` | 已有预约的北京时间 ISO 字符串；默认为空。 |
| `PREFERRED_DATE_RANGES` | `VISA_PREFERRED_DATE_RANGES` | JSON `[起始日期,结束日期]` 数组的数组；默认使用文件中的示例区间。 |
| `AUTO_BOOKING` | `VISA_AUTO_BOOKING` | `true`/`false` 或 `1`/`0`，不区分大小写；默认 `False`。 |
| `DAILY_EMAIL_TIMES` | `VISA_DAILY_EMAIL_TIMES` | 三个北京时间 HH:MM 的 JSON 数组；默认 `["08:00","12:00","17:00"]`。 |
| `POLL_SECONDS_MIN` | `VISA_POLL_SECONDS_MIN` | 正整数秒数，包含下限；默认 `1800`。 |
| `POLL_SECONDS_MAX` | `VISA_POLL_SECONDS_MAX` | 不小于下限的正整数秒数，包含上限；默认 `3600`。 |
| `SMTP_HOST` | `VISA_SMTP_HOST` | 主机名；默认 `smtp.gmail.com`。 |
| `SMTP_PORT` | `VISA_SMTP_PORT` | 整数字符串；默认 `465`，隐式 TLS。 |
| `SMTP_USERNAME` | `VISA_EMAIL_SENDER` | 发件人完整邮箱地址；默认为空。 |
| `SMTP_PASSWORD` | `VISA_SMTP_PASSWORD` | SMTP 应用专用密码；默认为空。 |
| `EMAIL_TO` | `VISA_EMAIL_TO` | 收件人邮箱地址；默认为空。 |

不要把真实密码和答案写入受版本控制的代码。环境变量**不是加密密码库**，
请保护电脑、会话和本地文件。其他运行预算仍在文件中配置；轮询上下限现已加入这十六项环境覆盖。

### 当前进程作用域的 PowerShell 模板（推荐）

```powershell
Copy-Item .\Set-VisaEnvironment.template.ps1 .\Set-VisaEnvironment.local.ps1
# 编辑 .local.ps1：准确姓名、问题文本、日期、邮件地址。
# 运行时通过隐藏输入提示读取签证密码、三个答案和 SMTP 应用密码。
. .\Set-VisaEnvironment.local.ps1
.\.venv\Scripts\python.exe main.py --check-config
```

模板为每项提供注释和示例。按本次选择，它仅设置 **Process（当前进程）** 环境，
不写 User/Machine 注册表，也不需要管理员权限。收集完输入后再设置全部十六项。
必须在同一个 PowerShell 窗口启动 Python；关闭窗口后设置不再保留。
另开 `powershell.exe` 执行脚本不能修改父终端的环境。
`.local.ps1` 副本已被 git 忽略；不要把真实凭据提交到模板中。

### 登录、申请人和已有预约

```powershell
$env:VISA_USERNAME = 'your-username'
$env:VISA_PASSWORD = [System.Net.NetworkCredential]::new(
    '', (Read-Host 'Visa password' -AsSecureString)).Password
$env:VISA_APPLICANTS = 'Exact displayed applicant name'
# 多名申请人使用分号分隔，必须与网站显示的姓名一致。
$env:VISA_VISA_CLASS = 'B1/B2'
$env:VISA_EXISTING_APPOINTMENT = '2030-01-15T10:00:00'
```

最后一项必须替换为**当前已有预约**的北京时间，不是目标日期。
工具预期只有一个领事馆预约条目；其他预约/团体布局不支持时会安全停止。
将 `VISA_VISA_CLASS` 设置为首页实际显示的 `B1/B2` 或 `B2`。

环境变量的问题映射采用以下 JSON 结构（仅为格式示例；真实答案建议通过模板的隐藏提示输入）：

```json
{"Exact question text 1":"answer1","Exact question text 2":"answer2","Exact question text 3":"answer3"}
```

问题文本必须与显示的中文或英文匹配；可明确配置多语言别名。
JSON 支持中文及转义后的引号、反斜杠；重复键、非字符串答案或结构不正确会报错。
文件中的默认问题映射仍可引用 `VISA_ANSWER_1` 等单独变量，但只有在未设置
`VISA_SECURITY_ANSWERS` 整体映射时才生效。
工具会规范化空白及 Unicode，不模糊猜测答案；未知问题会停止。
本地另外限制**每个北京时间自然日最多提交两次账号密码**，并持久化计数。
该本地限制不是网站真实每日限额的声明。

### 偏好日期、自动改期和通知

```powershell
$env:VISA_PREFERRED_DATE_RANGES = '[["2026-12-01","2026-12-15"],["2026-12-22","2026-12-25"]]'
$env:VISA_AUTO_BOOKING = 'false'
$env:VISA_DAILY_EMAIL_TIMES = '["08:00","12:00","17:00"]'
```

过期的示例日期必须更新。启动时拒绝颠倒/已开始于过去的区间；监控期间不会
选择已经过去的日期。最偏好的区间放在最前面，即使它在日历上更晚。
仅监控模式也会发偏好日期邮件；关闭改期后不会为了“模拟”而点击日期或时段。

### 使用 Gmail 发信

使用完整 Gmail 地址和 Google 生成的应用专用密码，不使用普通 Google 登录密码。
应用密码需要开启 Google 两步验证，部分账号策略可能不提供该功能。
本工具实现的是密码式 SMTP，而不是 OAuth；若无法创建应用密码，不要改用普通账号密码。

```powershell
$env:VISA_SMTP_HOST = 'smtp.gmail.com'
$env:VISA_SMTP_PORT = '465'
$env:VISA_EMAIL_SENDER = 'your.name@gmail.com'
$env:VISA_EMAIL_TO = 'destination@example.com'
$env:VISA_SMTP_PASSWORD = [System.Net.NetworkCredential]::new(
    '', (Read-Host 'Gmail app password' -AsSecureString)).Password
.\.venv\Scripts\python.exe main.py --test-email
```

Gmail 隐式 TLS 地址为 `smtp.gmail.com:465`；本工具没有实现 STARTTLS/587。
输入 16 字符应用密码时不要包含显示时的分组空格。收件地址可以与 Gmail 发件地址相同。
`main.py` 的主机/端口默认值已改为 Gmail，但必须提供发件人、应用密码和收件人。
打开浏览器前必须成功发送启动通知（由 SMTP 服务器接受）；这不等于保证邮件到达收件箱。

Google 官方参考：`https://developers.google.com/workspace/gmail/imap/imap-smtp`
和 `https://support.google.com/accounts/answer/185833`。

### 运行预算

| 配置项 | 默认值 | 含义 |
|---|---:|---|
| `POLL_SECONDS_MIN` | 1800 | 随机观察间隔的最小秒数，包含下限，可通过环境变量覆盖。 |
| `POLL_SECONDS_MAX` | 3600 | 随机观察间隔的最大秒数，包含上限，可通过环境变量覆盖。 |
| `BLOCK_COOLDOWN_SECONDS` | 86400 | 检测到限制后的本地冷却时间；若 `Retry-After` 更长，采用更长时间。并非已验证的解封时长。 |
| `MAX_RECOVERY_RETRIES` | 3 | 初次失败后最多三轮恢复，重启不会清零。 |
| `MAX_CHECKS_PER_RUN` | 24 | 当前持久化任务的成功查询上限；虽然名称含 per-run，重启也不会清零。 |
| `AUTO_VERIFICATION` | `True` | 尽力自动验证，不保证通过。 |
| `MAX_AUTO_VERIFICATIONS` | 1 | 每次进程运行至多调用一次辅助方法，不等于库内部恰好只有一次点击/请求。 |
| `MANUAL_VERIFICATION_TIMEOUT_SECONDS` | 900 | 手动验证等待上限；超时通知并停止。 |
| `PAGE_TIMEOUT_SECONDS` | 45 | 普通页面/响应等待时间。 |
| `QUEUE_TIMEOUT_SECONDS` | 1800 | 网站自身排队页面的最大等待时间。 |

每轮观察完成后，若允许继续查询，程序从 `[POLL_SECONDS_MIN, POLL_SECONDS_MAX]`
中重新均匀随机选择一个整数秒数。上下限都可能被选中，连续两次抽样也可能相同。
默认间隔为 30–60 分钟。等待从本轮工作结束后开始；两次观察开始时间之间还包含
处理本轮页面/通知所花的时间。空结果和仅监控模式使用相同逻辑。

```powershell
$env:VISA_POLL_SECONDS_MIN = '1800'
$env:VISA_POLL_SECONDS_MAX = '3600'
```

上下限必须为正整数，且最小值不能大于最大值。两项都设置为 `1800` 可保持原先
固定 30 分钟等待。原 `POLL_SECONDS` 已移除；请更新已有本地设置脚本副本，使用两个新变量。
某个变量不存在时，仅该项使用 `main.py` 中的默认值；空值或错误格式会报错。
不需要迁移或删除已有持久化任务状态。

每次抽样会写入 `EventLog`，操作为 `POLL_WAIT`，字段包含 `delay_seconds`、
`minimum_seconds`、`maximum_seconds`。提交后或达到查询预算时不再抽样等待。
限制冷却、`Retry-After`、验证超时、提交后的 90 秒观察以及北京时间每日三次邮件
**不会随机化**。随机等待期间仍执行心跳日志轮转和邮件调度。随机化不保证避免封锁。

接近页面浏览上限的警告也会被视为限制。冷却期间不发送预约查询，
但冷却不会清除服务端限制。一次恢复可能包含正常导航和认证请求，
不是 HTTP 层面只发送一次请求。本地查询/登录预算可能使任务提前停止。

## 运行

```powershell
.\.venv\Scripts\python.exe main.py --check-config
.\.venv\Scripts\python.exe main.py --test-email
.\.venv\Scripts\python.exe main.py
```

`--check-config` 仅本地校验，不写运行状态、不访问网站。
`--test-email` 仅明确发送一封测试邮件，不打开浏览器。
正常运行会先校验设置、获取单实例锁、检查持久化停止/提交状态并发送启动通知。

建议先使用 `VISA_AUTO_BOOKING = 'false'`，文件默认值也是 `False`。
只有在确认设置、访问许可及上述风险后，才把环境值改为 `'true'`。
运行测试不需要真实改期。

浏览器时区固定为 `Asia/Shanghai`，每次选择前检查，避免电脑处于美国时区导致日期偏移。
GUI 验证时保持窗口可见且位于前台，不要最小化或同时操作鼠标/键盘。
窗口不安全、被遮挡或不在前台时使用手动回退。GUI 辅助方法仅限认证页，
不会在已选择预约的页面上尝试点击。

收到手动验证邮件后，只在现有窗口内完成验证，不要自行选择日期或提交预约。
程序暂停浏览器操作并发送截止时间，只有识别到预期下一页面才继续。
如果自动验证辅助方法卡死，工具会终止自己拥有的进程树并停止；
这种情况无法保留原窗口继续手动验证。

Windows 下使用 kill-on-close Job Object 管理浏览器工作进程及其 Chrome 子进程，
不会按进程名终止其他 Chrome 窗口。通过计划任务运行时，GUI 操作需要已登录且
未锁定的交互式桌面。

## 总体流程与结果含义

```text
校验配置 -> 单实例锁/持久化状态 -> 启动邮件 -> 登录
 -> 自动验证或邮件通知手动回退 -> 匹配安全问题
 -> 校验首页 -> 仅点击改期 -> 校验已有预约/申请人
 -> 正常选择领馆 -> 观察返回日期 -> 日志和邮件
 -> 无匹配或仅监控：等待并按预算继续
 -> 有偏好日期：选择第一个可用时段 -> 再次校验
 -> 先写永久提交标记 -> Submit 一次 -> 观察 90 秒
 -> SUCCESS / FAILURE / UNKNOWN -> 结果邮件 -> 关闭浏览器并停止
```

SUCCESS 必须有网站 `AllScheduled` 响应，并能关联到本次请求的日期和时段编号，
不是只看标题/跳转。FAILURE 必须有明确错误响应；证据缺失或冲突为 UNKNOWN。
申请人、已有预约或选择内容变化时，拒绝提交。
偏好日期没有可用时段时不提交，本次观察结束后继续监控。

一旦可能已经提交，就不再自动处理验证码、刷新、重新登录、取消或重试提交。
网站自身的正常回调仍可能运行，但客户端不会再次提交。
Ctrl+C、进程/电脑故障可以中断 90 秒观察；永久提交标记仍阻止再次尝试。

## 日志、持久化及邮件恢复

路径均以项目根目录为基准，与启动命令所在目录无关：

- `logs\EventLog_YYYY_MM_DD.jsonl`：操作、结果、错误和重试。
- `logs\AvailabilityLog_YYYY_MM_DD.jsonl`：每次成功返回的完整日期观察，
  含非偏好日期及明确的偏好标记/优先级。
- `logs\BookingLog_YYYY_MM_DD.jsonl`：尝试的北京时间和结果。
- `.state\state.json`：停止/冷却/重试计数、上次观察、每日通知及邮件发件箱；
  不保存签证密码或安全问题答案。
- `.state\submission.json`：以独占创建并 fsync 的方式写入，永久阻止自动重复提交。

每条日志都有 PST（固定 UTC-08:00）、UTC、北京时间三个时间戳。
按北京时间日期切换文件，等待/冷却期间也执行轮转。
同一天重启会创建带唯一后缀的新文件，不截断旧文件。
午夜轮转由事件循环心跳驱动，不是硬实时保证。

**每次成功观察发现偏好日期时都会发邮件**，即使日期与上次相同。
每日三次邮件使用缓存的最新观察并标注时间/状态，不额外强制访问网站。
启动前错过多个通知时段时只补发最近一次；已记录的时段重启后不重复发送。
任务停止后不再继续每日通知。

邮件最多尝试发送三次，失败保存在本地。只重发未发送邮件、不打开浏览器：

```powershell
.\.venv\Scripts\python.exe main.py --send-pending
```

该命令仅明确重置未发送邮件的尝试次数。SMTP 接受邮件后、写入本地确认前若崩溃，
恢复时可能重复发送；邮件失败永远不会解除预约提交锁或重启改期。

**不要删除 `.state` 来绕过停止状态。** 提交后必须先人工核实真实预约及剩余改期次数。
确实需要新任务时，应在所有实例停止、人工核对并更新配置之后，明确离线归档/重置
对应状态；没有自动重置命令。每个账号只使用一个项目副本；
本地锁不能协调不同目录、电脑上的实例。

日志、状态和浏览器资料包含个人信息，应限制访问，不要上传含认证信息的网页或截图。
`.gitignore` 已排除状态、日志、虚拟环境及 SeleniumBase 测试产物。

退出码：`0` 正常完成或达到查询预算；`1` 异常、失败/未知的改期结果或仍有未发邮件；
`2` 配置/启动错误或拒绝重启。退出码为零本身不能证明预约成功。

## 验证与边界

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
# 可选：真实 Chrome，仅访问本地合成测试页面
$env:VISA_LOCAL_BROWSER_TESTS = '1'
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
Remove-Item Env:\VISA_LOCAL_BROWSER_TESTS
```

本地浏览器测试阻止访问签证、认证、Cloudflare 域名；独立 UC 冒烟测试仅打开默认
空白页面并验证工作进程清理。测试覆盖日期优先级/间隔、安全问题组合、不点击
非偏好日期、空结果、跨月份、真实时段列、陈旧状态、一次提交/重启、90 秒结果观察、
重试上限、邮件失败及午夜日志轮转。
配置测试还覆盖十六项环境覆盖及缺省值、严格的布尔/JSON 转换、不泄露敏感值的错误，
并在一次性 PowerShell 子进程中使用模拟密码输入验证 Process 作用域和 Python 继承。
轮询测试覆盖包含端点、每次重新抽样、相等上下限、非法范围、日志内容，以及不额外等待或改变安全等待。

**未验证真实账号登录、实际 Cloudflare 接受情况、服务端次数扣减或真实改期交易。**
网站结构变化会安全失败，而不是伪造可用日期或成功结果。
不支持首次新预约、其他领馆、OFC/生物信息预约、取消或重建申请。

详细资源分析和逐项需求评估位于
`C:\src\USVisa\CopilotOutput\VisaTool_analysis_astra.md`。

原始项目致谢：`SYuan03/VisaAppointmentWatcher` 和 `CChen19/us-visa-scheduler` 草稿。
