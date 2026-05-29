# astrbot_plugin_xiangqi

一个基于 **AstrBot v4.24.x** 的中国象棋插件，使用坐标制走棋，**不依赖外部棋类 API，也不依赖 LLM 理解规则**。

仓库地址：`https://github.com/zxx624/astrbot_plugin_xiangqi`

> 这个仓库现在发布的是 **有剪枝算法版**。  
> 它不是之前那个“无剪枝 / 简单评分版”的公开仓库内容；现在这里的版本已经升级为：
>
> - **negamax 搜索**
> - **alpha-beta 剪枝**
> - **走法排序**
> - **静态局面评估**
> - **缺将终局保护**（避免搜索过程中因将帅被吃后继续进入规则层而崩溃）

---

## 0. 当前版本：2.2.0

本版本重点更新：

- 集成随包 xqwlight Java 象棋引擎：`engine/bin/xqwlight-cli.jar`
- 新增 WebUI 开关 `enable_xqwlight_engine`，可一键开启/关闭象棋引擎
- README 补充 Java 安装、引擎验证、WebUI 配置说明
- 走棋台词区分“人类上一手”和“Bot 当前应手”，避免把 Bot 自己走的子说成玩家走的
- LLM 台词失败/超时时自动使用本地兜底台词，避免 Bot 只走棋不说话
- 引擎异常/超时/非法走法时自动回退 Python builtin 搜索

---

## 1. 功能

- `象棋新局`：开始新对局，默认用户执红，Bot 执黑
- `象棋执黑`：开始新对局，用户执黑，Bot 先手走红方一步
- `走棋 <from> <to>`：按坐标走棋，例如 `走棋 b9 c7`
- `棋盘`：查看当前棋盘图片
- `悔棋`：撤销上一整个回合（用户一步 + Bot 一步）
- `认输`：结束当前对局
- `提示`：让插件给出一个可行着法建议

---

## 2. 坐标说明

棋盘为 **9 列 × 10 行**：

- 列：`a` 到 `i`
- 行：`0` 到 `9`
- `a0` 位于图片左上角
- 图片底部显示 `a b c d e f g h i`
- 图片左侧显示 `0 1 2 3 4 5 6 7 8 9`

方向约定：

- 红方初始在下方
- 黑方初始在上方
- 红方默认向上走（行号减小）
- 黑方默认向下走（行号增大）

这样用户看到的棋盘图和输入命令的坐标是一一对应的。

---

## 3. 命令示例

```text
象棋新局
走棋 b9 c7
棋盘
提示
悔棋
认输
```

执黑开局：

```text
象棋执黑
```

---

## 4. 规则覆盖

已实现：

- 车直走，检查路径阻挡
- 马走日，检查蹩马腿
- 相 / 象走田，不可过河，检查塞象眼
- 士九宫内斜走一步
- 将 / 帅九宫内走一步，禁止将帅对脸
- 炮走直线，吃子时必须隔一子
- 兵 / 卒过河前仅前进，过河后可左右，不能后退
- 不能走出让己方将帅被将军的局面
- 检测将军、将死、无合法走法等结束条件

暂未覆盖：

- 长将、长捉等完整专业判和规则
- 更复杂的残局库 / 开局库

---

## 5. 当前版本 AI 说明

这个版本已经不是早期的“简单评分选一步”了，而是一个**纯本地搜索型 AI**。

### 搜索框架

当前 `engine/ai.py` 采用：

- **negamax**
- **alpha-beta 剪枝**
- **走法排序（move ordering）**

大致流程：

1. 先生成当前所有合法走法
2. 用吃子、将军、自身安全、位置活跃度等因子给候选走法排序
3. 对每一步进入 negamax 搜索
4. 使用 alpha-beta 剪枝跳过明显不可能更优的分支
5. 到达深度上限后，用静态评估函数估值
6. 返回评分最高的着法

### 静态评估包含的因素

- 子力价值
- 棋子活跃度
- 兵过河与推进奖励
- 车 / 炮通路与开放线奖励
- 将帅安全
- 机动性（合法步数量差）
- 被将军惩罚 / 将军奖励
- 被攻击格惩罚

### 为什么比旧版强

和之前无剪枝版相比，这版的核心提升是：

- **会往后看多层变化**，不再只看眼前一步
- **剪枝后搜索效率更高**，同样层数下更能跑得动
- **送子情况明显减少**
- **提示功能也更像真正搜索建议**

---

## 6. 缺将 / 崩溃保护

中国象棋搜索里有一个很容易踩的坑：

如果搜索分支里出现“某一方将 / 帅已经被吃掉”的局面，而规则层的 `is_in_check()` / `legal_moves()` / `find_general()` 仍然假设双将都存在，就会在搜索过程中直接抛出异常。

这个版本专门补了这一层保护：

- 搜索入口会先判断双方将帅是否仍存在
- 缺将时直接按终局评分处理
- 不再把这种局面继续送进普通规则判定链里

这样可以避免之前那类：

```text
ValueError: 将帅不存在，棋局状态异常
```

> 说明：这并不等于所有边界局面都绝对无 bug，但至少已经把“搜索试走吃将后崩溃”这条典型链路纳入保护范围。

---

## 7. 配置项

插件配置在 AstrBot WebUI 的插件配置页里改，主要配置如下：

### `enable_xqwlight_engine`

- 类型：`bool`
- 默认值：`true`
- 含义：是否开启随插件打包的 **xqwlight Java 象棋引擎**

建议：

- `true`：推荐。优先使用 xqwlight，引擎棋力更强；如果 Java / jar 异常，会自动回退到 builtin。
- `false`：关闭外部引擎，只使用 Python 内置搜索；不需要安装 Java，但棋力较弱。

### `ai_backend`

- 类型：`string`
- 默认值：`xqwlight`
- 含义：兼容旧配置的 AI 后端选择。

一般不用改。现在更推荐直接用 `enable_xqwlight_engine` 开关控制是否启用象棋引擎。

### `xqwlight_jar_path`

- 类型：`string`
- 默认值：空
- 含义：xqwlight 引擎 jar 路径。

留空时使用插件自带的：

```text
engine/bin/xqwlight-cli.jar
```

只有你想换成自己编译的 jar 时，才需要填写绝对路径，例如：

```text
/opt/engines/xqwlight-cli.jar
```

### `xqwlight_depth`

- 类型：`int`
- 默认值：`8`
- 含义：xqwlight 搜索深度。

建议：

- `6`：更快，适合低配服务器
- `8`：默认推荐
- `10`：更强但更慢
- 代码中会限制在 `1..12`

### `xqwlight_timeout_ms`

- 类型：`int`
- 默认值：`1500`
- 含义：xqwlight 每步最多思考多少毫秒。

如果超时、Java 不存在、jar 不存在、引擎返回非法走法，插件会自动回退到 Python 内置 AI，不会中断棋局。

### `ai_depth`

- 类型：`int`
- 默认值：`2`
- 含义：Python 内置 builtin AI 的搜索层数，仅在关闭 xqwlight 或引擎回退时使用。

建议：

- `1`：更快，但棋力偏弱
- `2`：比较均衡
- `3`：会明显更慢，但对局面判断更稳一些

### `llm_talk_enabled`

- 类型：`bool`
- 默认值：`true`
- 含义：Bot 走棋后是否生成拟人台词。

注意：LLM **不参与选棋**，只负责说话；真正落子仍由规则层 + 搜索/引擎决定。

### `llm_persona_prompt`

- 类型：`text`
- 含义：Bot 下棋时的人格提示词。可以在 WebUI 里改成自己的角色风格。

### `image_scale`

- 类型：`int`
- 默认值：`1`
- 含义：棋盘图片缩放倍数。

---

## 8. 安装方法

把整个目录放进 AstrBot 插件目录，例如：

```text
/opt/astrbot1/data/plugins/astrbot_plugin_xiangqi/
```

建议目录结构：

```text
astrbot_plugin_xiangqi/
├── __init__.py
├── _conf_schema.json
├── main.py
├── metadata.yaml
├── README.md
├── requirements.txt
├── engine/
├── render/
└── storage/
```

安装依赖：

```bash
pip install -r requirements.txt
```

或者至少安装：

```bash
pip install Pillow
```

### 8.1 象棋引擎安装说明

本插件已经随包带了一个 xqwlight Java 引擎封装：

```text
engine/bin/xqwlight-cli.jar
```

所以正常情况下，下载插件包后**不需要再单独下载象棋引擎 jar**，只需要服务器上有 Java 即可。

#### Linux / Ubuntu / Debian 安装 Java

```bash
sudo apt-get update
sudo apt-get install -y default-jre-headless
```

验证 Java：

```bash
java -version
```

#### CentOS / Rocky / AlmaLinux 安装 Java

```bash
sudo yum install -y java-17-openjdk-headless
```

或：

```bash
sudo dnf install -y java-17-openjdk-headless
```

#### Windows 安装 Java

安装任意 JRE/JDK 17+，然后在命令行确认：

```bat
java -version
```

#### 验证随包引擎是否可运行

在插件目录执行：

```bash
java -jar engine/bin/xqwlight-cli.jar 'rnbakabnr/9/1c5c1/p1p1p1p1p/9/9/P1P1P1P1P/1C5C1/9/RNBAKABNR w' 4 500
```

正常会输出一个 4 位坐标走法，例如：

```text
h9g7
```

只要能输出类似 `[a-i][0-9][a-i][0-9]` 的坐标，就说明引擎可用。

#### 在 WebUI 开关象棋引擎

进入 AstrBot WebUI → 插件管理 → 坐标制中国象棋 → 配置：

- `enable_xqwlight_engine = true`：开启 xqwlight 象棋引擎，推荐
- `enable_xqwlight_engine = false`：关闭 xqwlight，只用 Python 内置 builtin 搜索

`xqwlight_jar_path` 留空即可使用随包 jar。只有你自己替换了引擎 jar，才需要填自定义 jar 的绝对路径。

> 注意：xqwlight 只是负责给 Bot 推荐走法。插件仍会用自己的规则层校验走法是否合法，所以引擎异常、超时、返回非法走法时不会直接把棋局搞坏，而是自动回退 builtin。

部署后重启 AstrBot，例如：

```bash
sudo systemctl restart astrbot1
```

---

## 9. 数据存储

插件通过 `StarTools.get_data_dir()` 获取运行数据目录，并在其中保存：

- `sessions.json`：当前会话中的棋局数据
- `boards/`：渲染出的棋盘 PNG 图片

所以它是一个**有状态插件**：

- 不同群 / 私聊会话分别维护自己的棋局
- 重启后理论上可以继续读取存档

---

## 10. 代码结构

### `main.py`
AstrBot 插件入口，负责：
- 注册命令
- 管理棋局会话
- 调用规则层与 AI 层
- 返回文本与棋盘图片

### `engine/board.py`
棋盘状态、棋子表示、走子执行、历史记录等底层能力。

### `engine/rules.py`
象棋规则校验：
- 合法走法生成
- 将军 / 将死判定
- 棋子走法规则
- 合法落子验证

### `engine/ai.py`
当前版本的 AI 核心：
- negamax
- alpha-beta 剪枝
- 候选排序
- 静态评估
- 缺将终局保护

### `render/board_image.py`
负责渲染棋盘 PNG 图片。

### `storage/session_store.py`
负责会话棋局存档读写。

---

## 11. 适用场景

这个版本适合：

- 想直接拿来玩的 AstrBot 象棋插件
- 想要比简单版明显更强的本地 AI
- 想研究“规则层 + 搜索层”分离结构
- 想继续往更深层搜索或残局优化方向迭代

---

## 12. 已知限制

即便已经有剪枝，这个版本仍然不是专业象棋引擎：

- 没有残局库 / 开局库
- 没有长将长捉完整规则
- 搜索深度仍受聊天机器人运行时性能限制
- 静态评估还是工程化启发式，不是专业大规模调参引擎

所以它的定位是：

**比基础版强很多的纯本地搜索插件**，但不是比赛级引擎。

---

## 13. 与旧公开版的区别

之前公开过一个“无剪枝算法”的简单版。

当前仓库这版和它的区别是：

- 旧版：
  - 主要是简单评分
  - 顶多浅看一层回应
  - 没有 alpha-beta 剪枝
- 当前版：
  - 真正进入 negamax 搜索
  - 有 alpha-beta 剪枝
  - 有走法排序
  - 有更完整的静态评估
  - 对缺将搜索分支做了保护

如果你是第一次下载这个仓库，**请以当前 README 为准，不要再按旧版理解它。**

---

## 14. 许可证

当前仓库未附带单独 LICENSE。

如果准备长期公开维护，建议补一个明确的开源许可证，例如 MIT。
