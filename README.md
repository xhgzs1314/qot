# QOT 语言

[![license](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)
[![platform](https://img.shields.io/badge/platform-Windows-blue.svg)]()
[![version](https://img.shields.io/badge/version-v1.0-orange.svg)]()

QOT 是一款简洁易用的解释型脚本语言，设计目标是降低编程入门门槛，同时具备灵活的语法特性和实用的功能支持，可满足基础编程场景需求。

# 目录

- [特性](#特性)

- [快速开始](#快速开始)

- [帮助文档](#帮助文档)

- [项目结构](#项目结构)

- [语言手册](#语言手册)

- [许可证](#许可证)

# 特性

-  语法简洁直观

-  支持多种数据类型：数值（多进制）、字符串、布尔、数组、字典、空值

-  较为完整的控制结构：条件语句（if/eif/else）、循环语句（for/loop）

-  模块化设计，支持脚本模块（\.qot）和动态库模块（\.dll）引入

-  丰富的运算符体系，清晰的优先级规则

-  支持编译为 QBC（分发格式不可读）QTC（字节码）可执行文件（EXE）

-  跨平台基础能力（Windows 优先支持）

-  动态类型系统，兼顾灵活性与语义检查

-  内置 Direct2D 图形界面（`vg_*`，共 62 个函数，**仅 QTC 虚拟机可用**），见 [GUI 手册](plugin/gui_manual.html)

# 快速开始
## 克隆项目
```bash
git clone https://github.com/xhgzs1314/qot.git
cd QOT
```
## 环境准备
由于项目需要完整的 Python + PHP + donut + UASM 环境，
请从以下地址下载预置环境包：

- 下载地址
[123网盘](https://1812517209.share.123pan.cn/123pan/WsQ0Vv-m9JlA)
[蓝奏云网盘(提取码9nyz)](https://hcgame.lanzouu.com/b0um9bptc)
[阿里云网盘(exe自解压)](https://www.alipan.com/s/3d1XopkXTXL)
下载后解压到项目根目录，见 [目录结构](#项目结构)
>  **注意**：预置环境包约 115MB，下载后请解压到项目根目录，
> 确保 `env/` 文件夹与 `README.md` 同级

确保安装 Python 环境，并安装依赖：

```bash
pip install -r requirements.txt
```

## 获取 qguoat 命令行工具(编译QOT本体)
`qguoat` 是 QOT 的命令行工具，需要先编译生成：

```bash
# 编译生成 qguoat.exe
python package.py
```
编译成功后，qguoat.exe 位于 dist/QGuoat/ 目录下。

### 配置环境变量（可选）
将 dist/QGuoat/ 添加到系统 PATH 中，即可在任意位置使用 qguoat 命令：

```bash
# 临时添加
set PATH=%PATH%;C:\你的项目路径\dist\QGuoat
# 或永久添加：系统设置 → 环境变量 → Path → 新增路径
```

## 运行脚本

```bash
# 基础运行
qguoat run program.qot

# 带控制台输出运行
qguoat run program.qot --console

#详细的帮助文档(浏览器访问：http://localhost:8080)
qguoat hdps
```

## 编译脚本

```bash
# 编译为 EXE 可执行文件
qguoat build <file.qot> [options]
--mode <1|2|3>           编译模式: 1 - V1, 2 - V2 (默认2) , 3 - V3
--icon <icon_path>     图标文件路径
--exportfile <path>    额外资源文件(最多15个)
--output <output_path> 输出文件路径
qguoat build script.qot --mode 2 --icon logo.ico --exportfile data.txt --exportfile config.json")

# 编译为 QBC 格式（不可直接阅读）
qguoat distrust input.qot [output.qbc]

# 运行 QBC
qguoat run script.qbc

#编译为QTC字节码
qguoat bstrust compile <input.qot> [output.qtc] [--inline 0|1] [--opt]
--inline 0|1    是否启用内联模式 (默认: 0)
--opt           启用优化 (死代码消除、常量折叠等)

#运行QTC字节码
qguoat bstrust run <file.qtc> [--debug]
--debug 启用VM Debug-Info输出

```
# 帮助文档

```bash
#详细的帮助文档(浏览器访问：http://localhost:8080)
qguoat hdps
```

也可以直接用浏览器打开 `plugin/` 下的离线文档：

| 文档 | 内容 |
|---|---|
| [plugin/doc.html](plugin/doc.html) | 语言文档（语法、模块、错误、两个运行时的差异、GUI） |
| [plugin/function_inside.html](plugin/function_inside.html) | 解释器内置函数表 |
| [plugin/function_inside2.html](plugin/function_inside2.html) | 字节码 VM 内置函数表 |
| [plugin/gui_manual.html](plugin/gui_manual.html) | GUI（`vg_*`）开发手册 |

# 项目结构

```
QOT/
├── astcomopt.py        # AST 优化相关
├── ast_encoder.py      # AST 编码处理
├── build/              # 构建输出目录
├── buildcpp.py         # C++ 构建脚本
├── building.py         # 构建核心逻辑
├── building_v3.py      # 构建版本V3
├── builtin_func.py     # 内置函数实现
├── bytecode.py         # 字节码处理
├── console.py          # 控制台交互
├── env/                # 环境配置目录
├── error_system.py     # 错误处理系统
├── grammar.py          # 语法定义
├── index.py            # 入口逻辑
├── LICENSE             # 许可证文件
├── package.py          # 编译QOT本体
├── ot-asset/           # 资源文件目录
├── package/            # 系统模块目录
├── plugin/             # 插件目录
├── qotrun.dll          # 运行时动态库
├── requirements.txt    # Python 依赖
├── require_inliner.py  # 模块内联处理
├── server.py           # 网页控制台服务
├── shellcode.py        # 壳代码相关
├── token_char.py       # 词法分析（字符token）
```

# 语言手册

## 1. 语言简介

QOT 是一种动态类型的脚本语言。它设计简洁，语法直观，适合初学者入门学习，同时也具备一定的灵活性和功能性。

QOT 语言支持基本的数据类型、控制结构、函数定义与调用，以及数组和字典等复合数据类型，能够满足基础编程需求。

### 1.1 两套运行时（重要）

**同一份 `.qot` 源码，QOT 有两条完全独立的执行路径。它们并不等价**——内置函数表不同，部分语义也不同。学习时请先确定自己走的是哪一条：

| 路径 | 命令 | 实现 | 定位 |
| --- | --- | --- | --- |
| 解释器 | python index.py run a.qot | Python 树遍历解释器 | 快速试错、调试；启动快，执行慢 |
| 字节码 VM | python index.py bstrust compile a.qot a.qtc
 python index.py bstrust run a.qtc | C++ 虚拟机（qotrun.dll） | 生产环境用这条；GUI 只在这条路径上可用 |

本文档正文描述的是**两条路径共有**的语法。凡是两边行为不一致的地方，都会用「⚠ 运行时差异」明确标出，并在 [第 21 章](#runtime-diff) 集中列表。

* 建议：写生产代码时始终以字节码 VM 为准。**解释器不支持 GUI，数值输出格式也不同，仅适合验证逻辑。

## 2. 基本语法

### 2.1 程序结构

QOT 程序由一系列语句组成，语句通常以分号`;`结尾。程序文件以`.qot`为扩展名。

### 2.2 注释

QOT **只有一种注释**：单行注释，以 `//` 开头，一直到行尾。

```
// 这是一条注释
var x = 1;   // 也可以写在语句后面
```

**没有块注释。**`/*... */` 不是 QOT 语法，写了会报词法错误。要注释掉多行，只能每行都加 `//`。

另外还有一种以 `@-@-@` 开头的行，叫**注解**（annotation）。它在语法上是一条独立语句，不产生任何运行时行为，作用是给编译器/工具链传递指令（见 [10.2 注解](#anno)）。日常当注释用也没问题，但它必须**独占一行**，不能跟在代码后面。

```
@-@-@ 这是注解，独占一行
var x = 1;   @-@-@ 错误：注解不能跟在语句后面
```

### 2.3 关键字

QOT 包含以下关键字：

- 控制结构：`if`、`else`、`eif`（else if 的简写）、`loop`、`for`、`in`
- 变量声明：`var`、`const`
- 函数相关：`func`、`return`
- 类型说明符：`number_t`、`string_t`、`boolean_t`、`array_t`、`any_t` （**只有这 5 个**，没有 `dict_t`，字典请用 `any_t`）
- 其他：`null`、`stop`、`true`、`false`、`require`
- 数值字面量：支持十进制、十六进制（`0x`）、二进制（`0b`）、八进制（`0o`）

#### 常见误会：这些**不是** QOT 的关键字

- `while`：QOT 的条件循环叫 `loop`
- `break`：跳出循环用 `stop;`
- `continue`：**QOT 没有 continue**，只能用 `if` 把循环体剩下的部分包起来
- `function`：定义函数用 `func`，而且前面必须写返回类型
- `else if`：要写成一个词 `eif`
- `output`、`strings` 等：这些是**内置函数**，不是关键字，可以被同名的用户函数覆盖

## 3. 数据类型

QOT 支持以下基本数据类型：

### 3.1 数值型（number）

包含整数和浮点数，支持多种进制表示：

#### 十进制

```
42;      // 整数
3.14;    // 浮点数
-10;     // 负数
```

#### 十六进制（Hex）

以 `0x` 或 `0X` 开头，后面跟十六进制数字（0-9, a-f, A-F）：

```
0xFF;        // 255
0x10;        // 16
0xabcdef;    // 11259375
0XABCD;      // 43981
```

#### 二进制（Binary）

以 `0b` 或 `0B` 开头，后面跟二进制数字（0, 1）：

```
0b1010;      // 10
0b11111111;  // 255
0B1100;      // 12
```

#### 八进制（Octal）

以 `0o` 或 `0O` 开头，后面跟八进制数字（0-7）：

```
0o755;       // 493
0o777;       // 511
0O1234;      // 668
```

#### 示例

```
var hex = 0xFF;           // 255
var bin = 0b1010;         // 10
var oct = 0o755;          // 493
var dec = 100;            // 100

output("十六进制: " + hex);
output("二进制: " + bin);
output("八进制: " + oct);
```

### 3.2 字符串（string）

用单引号或双引号包裹的字符序列，例如：

```
"Hello, QOT";

'这是一个字符串';
```

字符串可以用 `+` 拼接，另一边是数字时会自动转成字符串：

```
output("a" + 3);     // a3
output(3 + "a");     // 3a
```

不过**推荐显式用 `strings()` 转换**。因为 `+` 是否走拼接取决于运行期的实际类型，一旦左边不是字符串就变成了数字加法，这类 bug 很难查：

```
var n = 3;
output("值是 " + strings(n));   // 推荐：意图明确，结果稳定
```

字符串支持**按下标取字符**，下标从 0 开始，返回一个长度为 1 的字符串：

```
var s = "hello";
output(s[1]);    // e
```

字符串是**不可变**的。`s[1] = "x";` 不报错，但也不生效，`s` 仍然是 `"hello"`。

取长度用内置函数 `glen(s)`：

```
output(glen("hello"));    // 5
output(glen([1, 2, 3]));  // 3
```

### 3.3 布尔型（boolean）

只有两个值：`true`和`false`

### 3.4 数组（array）

有序的元素集合，例如：

```
[1, 2, 3, 4];
["apple", "banana", "cherry"];
```

### 3.5 字典（dict）

键值对的集合，使用 `&<` 和 `>&` 作为分隔符：

```
var person = &< "name": "Alice", "age": 30 >&;
```

#### 字典键的写法

- **字符串字面量**：必须用引号包裹，如 `"name"`
- **变量作为键**：不需要引号，如 `keyName`

```
var keyName = "age";
var person = &< "name": "Alice", keyName: 25 >&;
```

#### 访问字典元素

```
var name = person["name"];   // 获取值
person["age"] = 31;          // 修改值
person["city"] = "Beijing";  // 添加新键值对
```

#### 字典遍历

```
var keys = dict_keys(person);  // 获取所有键，返回数组
for (key in keys) {
    var value = person[key];
    output(key + ": " + value);
}
```

⚠ **`dict_keys` 返回的键顺序是不确定的**，跟你写字面量的顺序没有关系（底层是哈希表）。需要固定顺序请自己另外维护一个键数组。

#### 字典常用函数

- `dict_get(dict, key)`：获取键对应的值，不存在返回 null
- `dict_set(dict, key, value)`：设置键值对
- `dict_keys(dict)`：返回所有键组成的数组（顺序不定）

字典的类型说明符是 `any_t`。**QOT 没有 `dict_t`**。

### 3.6 空值（null）

表示空值，使用`null`关键字。

QOT 中 `null` 出现得比你想象的频繁，它是很多「没有结果」情形的统一返回值：

- 声明了但没赋值的变量：`var x;`
- 数组下标越界读取：`arr[999]`
- 字典中不存在的键：`d["nope"]`
- 没有 `return` 语句的函数的返回值
- 调用了不存在的内置函数

这意味着 QOT 不会因为「取错了东西」而报错，只会安静地给你一个 `null`。写代码时该自己检查就得自己检查

## 4. 变量声明与赋值

### 4.1 变量声明

使用 `var` 关键字声明变量：

```
var age;           // 声明但不赋值，值为 null
var name = "QOT";  // 声明并赋值
```

### 4.2 常量声明

使用 `const` 关键字声明常量：

```
const PI = 3.14159;
const MAX_SIZE = 100;
```

`const` 声明**必须同时赋初值**，`const X;` 是语法错误。

⚠ **运行时差异**：

- **解释器**（`index.py run`）会检查常量：修改或重复声明常量都会报错并中止。
- **字节码 VM**（`bstrust`）**完全不检查**：`const PI = 3.14159; PI = 0;` 会静默成功，`PI` 真的变成 0。

也就是说，在生产路径上 `const` 目前**只是一个给读代码的人看的约定**，没有任何强制力。别指望它拦住谁。这是性能优先取向下的有意取舍——不为运行期加一次检查。

### 4.3 作用域规则

QOT 采用**静态（词法）作用域**，只有两层：**全局**和**函数内**。

这里必须澄清一个常见误解：**QOT 不是动态作用域**。一个函数能看见哪些外部变量，在它被*写下来*的时候就定死了，跟它被*谁*调用无关。

#### 两层作用域

- **全局作用域**：写在所有函数外面的变量，整个程序（包括所有函数内部）都能读写。
- **函数作用域**：函数的参数，以及在函数体内用 `var` 声明的变量，只在这个函数内部可见，函数返回即消失。

**没有块级作用域。**`if`、`loop`、`for` 的 `{}` 不产生新作用域——在里面 `var` 出来的变量，出了大括号照样能用。

```
if (true) {
    var inner = 1;
}
output(inner);   // 1，不是错误
```

#### 函数内访问全局变量

```
var g = "全局";

any_t func test() {
    var local = "局部";
    output(g);        // 可以读全局变量
    output(local);
}

test();
output(local);        // null —— 局部变量在函数外不可见
```

注意最后一行：`output(local)` **不报错**，而是打印 `null`。QOT 不区分「未定义的变量」和「值为 null 的变量」。

#### ⚠ 函数内写全局变量：两个运行时不一致

函数里给一个全局变量赋值，改动是否会留下来，**两条路径的答案不同**：

```
var g = 10;

any_t func setIt() {
    g = 15;
}

setIt();
output(g);
```

| 路径 | 输出 | 行为 |
| --- | --- | --- |
| 解释器 index.py run | 10 | 函数内的赋值只改了函数自己的一份副本，返回后丢弃 |
| 字节码 VM bstrust | 15.000000 | 真正写回了全局槽位，改动保留 |

**依赖这个行为的代码，请只在字节码 VM 上运行。**GUI 回调修改界面状态就是靠这个机制（见 [第 22 章](#gui)），所以 GUI 程序本来也只能走 VM。

如果你希望代码在两条路径上都一样，就**别在函数里写全局变量**——用返回值传出去：

```
var g = 10;

number_t func computeIt() {
    return 15;
}

g = computeIt();   // 两边都是 15
```

#### 变量名的解析规则

编译器决定一个名字指向哪里，规则很简单：

1. 是本函数的参数吗？→ 是，用参数槽位
2. 是本函数里 `var` 声明过的吗？→ 是，用局部槽位
3. 都不是 → 当作**全局槽位**

第 3 条意味着：**拼错的变量名不会报错**，只会变成一个值为 `null` 的新全局变量。

```
var count = 0;

any_t func bump() {
    conut = conut + 1;   // 拼错了，但不报错，只是一直在算 null + 1
}
```

这类问题只能靠自己小心，或者用编辑器插件的静态检查。

### 4.4 变量提升

变量可以在声明前使用（提升行为）：

```
output(x);   // 输出 null（变量已提升但未赋值）
var x = 10;
output(x);   // 输出 10
```

函数同样是提升的：可以在定义之前调用。

```
hello();     // 可以，函数已提升

any_t func hello() {
    output("hi");
}
```

### 4.5 重复声明

重复声明同一个 `var` 变量不会报错，后一次覆盖前一次：

```
var a = 1;
var a = 2;   // 合法，a 的值变为 2
```

重复声明 `const`：

```
const b = 3;
const b = 4;
```

- **解释器**：报错「不能修改常量 'b'」并中止。
- **字节码 VM**：静默通过，`b` 变成 4。

## 5. 运算符

### 5.1 算术运算符

| 运算符 | 说明 | 示例 |
| --- | --- | --- |
| + | 加法；任一边是字符串时做拼接 | 1 + 2 → 3"a" + 3 → a3 |
| - | 减法 | 5 - 2 → 3 |
| * | 乘法 | 3 * 4 → 12 |
| / | 除法（浮点除，不取整） | 7 / 2 → 3.5 |
| % | 取模 | 7 % 3 → 1 |
| ^ | 幂运算，右结合 | 2 ^ 10 → 1024 |
| -（一元） | 取负 | -x |

⚠ 关于幂运算，两点务必记住：

- QOT 的幂运算符是 `^`，**不是 `**`**。`2 ** 10` 是语法错误。
- `^` **不是**按位异或。QOT 没有位运算符（`&`、`|`、`~`、`<<`、`>>` 都不支持）。
- `^` 右结合且优先级高于 `*`：`2 ^ 3 ^ 2` = `2 ^ (3 ^ 2)` = 512；`2 ^ 2 * 3` = `(2 ^ 2) * 3` = 12。

**QOT 没有自增自减**：`i++`、`++i`、`i--` 全部是语法错误。请写 `i = i + 1;` 或 `i += 1;`。

### 5.2 比较运算符

- `==`：等于
- `!=`：不等于
- `<`：小于
- `>`：大于
- `<=`：小于等于
- `>=`：大于等于

QOT 只有这一组比较运算符，**没有 `===` / `!==`**。

⚠ 这 6 个运算符**优先级完全相同**，且从左到右结合。所以 `1 < 2 == true` 会被解析成 `(1 < 2) == true`，结果是 `true`。想表达别的意思请加括号。

### 5.3 逻辑运算符

- `&&`：逻辑与
- `||`：逻辑或

⚠ **QOT 没有逻辑非运算符。**`!a` 是语法错误——注意 `!` 只在 `!=` 里出现，单独用不构成表达式。要取反，请这样写：

```
// 想写 if (!flag)，改成：
if (flag == false) { ... }

// 想写 if (!(a > b))，改成：
if (a <= b) { ... }
```

⚠ **`&&` 和 `||` 优先级完全相同**，从左到右结合。这跟 C、Java、Python、JavaScript 都不一样，是最容易踩的坑：

```
true || false && false
// QOT 解析成： (true || false) && false  →  false
// 其他语言是： true || (false && false)  →  true
```

**只要一个表达式里同时出现 `&&` 和 `||`，就一律加括号。**这不是风格建议，是正确性要求。

```
if (isVip || (age > 60 && hasCard)) { ... }   // 明确，正确
```

### 5.4 赋值运算符

- `=`：赋值
- `+=`、`-=`、`*=`、`/=`、`%=`：复合赋值

⚠ **复合赋值只能用在普通变量名上。**左边写数组下标或字典键是**语法错误**：

```
var a = [1, 2, 3];
a[0] += 5;            // 语法错误！
a[0] = a[0] + 5;      // 正确写法

var d = &< "n": 1 >&;
d["n"] += 1;          // 语法错误！
d["n"] = d["n"] + 1;  // 正确写法
```

**⚠ 赋值是语句，不是表达式。**这意味着：

- 不能连等：`var x = y = z = 5;` 是语法错误
- 不能写在条件里：`if (x = 5)` 是语法错误（这条反而帮你避免了经典的 `=`/`==` 笔误）
- 不能当参数：`f(x = 1)` 是语法错误

### 5.5 运算符优先级

下表是 QOT 的**实际**优先级，来自语法规则本身。**它跟 C 系语言有几处重要区别，请逐行看完。**

| 优先级 | 运算符 | 说明 | 结合性 |
| --- | --- | --- | --- |
| 1（最高） | () [] -> ## -（一元负号） | 括号、下标、方法调用、模块调用、取负 | 从左到右 |
| 2 | ^ | 幂运算 | 从右到左 |
| 3 | * / % | 乘、除、取模 | 从左到右 |
| 4 | + - | 加、减 | 从左到右 |
| 5 | < > <= >= ==!= | 比较（⚠ 6 个同级，相等比较并不比大小比较低） | 从左到右 |
| 6（最低） | && \|\| | 逻辑与、逻辑或（⚠ 两个同级，与不比或高） | 从左到右 |

赋值 `=` `+=` … 不在表中，因为它们是语句层面的东西，不参与表达式求值。

#### 跟 C 系语言的三处差异（务必记住）

1. `&&` 与 `||` **同级**——混用必须加括号
2. `==` `!=` 与 `<` `>` **同级**
3. 幂运算是 `^`（右结合、高于乘除），不是异或

#### 示例

```
var result = 10 + 5 * 2;    // 乘法优先，结果为 20
var flag = a > 0 && b < 10;  // 比较优先于逻辑运算，结果符合直觉
var p = 2 ^ 3 ^ 2;          // 右结合 → 2 ^ 9 → 512
var q = -2 ^ 2;             // 一元负号优先 → (-2) ^ 2 → 4
```

#### 建议

- 不确定优先级时，直接用 `()` 写清楚，不要背表
- 例如：`var result = (10 + 5) * 2;` 结果为 30

## 6. 控制结构

### 6.1 条件语句

#### if 语句

```
if (condition) {

  // 条件为真时执行

}
```

#### if-else 语句

```
if (condition) {

   // 条件为真时执行

} else {

   // 条件为假时执行

}
```

#### if-eif-else 语句

```
if (condition1) {

   // 条件1为真时执行

} eif (condition2) {

  // 条件2为真时执行

} else {

   // 所有条件都为假时执行

}
```

### 6.2 循环语句

#### for 循环（数字范围）

```
for (i in 5) {
    output(i);   // 输出 0, 1, 2, 3, 4
}
```

**注意：** 循环变量从 0 开始，到指定数字-1 结束。

#### for 循环（遍历数组）

```
var names = ["Alice", "Bob", "Charlie"];
for (name in names) {
    output(name);  // 依次输出 Alice, Bob, Charlie
}
```

#### loop 循环

```
var count = 0;
loop (count < 3) {
    output("计数: " + count);
    count = count + 1;
}
// 输出: 计数: 0, 计数: 1, 计数: 2
```

#### 循环控制：stop 语句

使用 `stop` 关键字提前终止循环（相当于其他语言的 `break`）：

```
for (i in 10) {
    if (i == 5) {
        stop;   // 当 i 等于 5 时，退出循环
    }
    output(i);
}
// 输出: 0, 1, 2, 3, 4
```

`stop` 只跳出**最内层**的那一层循环。`stop` 写在循环外面是编译错误。

**⚠ QOT 没有 `continue`。**想跳过本次迭代，只能把剩下的代码用 `if` 包起来：

```
// 其他语言里会写 if (i % 2 == 0) { continue; }
for (i in 10) {
    if (i % 2 != 0) {
        output(i);      // 只处理奇数
    }
}
```

#### 三种循环怎么选

- 知道次数 → `for (i in n)`
- 遍历数组/字典的键数组 → `for (x in arr)`
- 其他一切 → `loop (条件)`

QOT **没有 C 风格的三段式 `for(init; cond; step)`**，也没有 `while` 和 `do-while` 关键字。要手动控制步长，用 `loop`：

```
var i = 0;
loop (i < 10) {
    output(i);
    i = i + 2;   // 步长 2
}
```

#### 注意事项

- `{}` 不能省略。`if (x) output(1);` 是语法错误，单条语句也必须带大括号。
- 循环体、条件分支的 `{}` **不产生新作用域**，里面 `var` 的变量出来还在（见 [4.3](#scope)）。
- 无限循环会让程序失去响应，请确保循环条件最终会变假。QOT 不做任何迭代次数保护。

## 7. 函数

### 7.1 函数定义

使用 `func` 关键字定义函数。**`func` 之前必须写返回类型**，这是语法强制要求，漏写会直接报「定义函数时必须指定返回数据类型」。

**语法格式：**

```
返回类型 func 函数名(参数1, 参数2, ...) {
    // 函数体
    return 返回值;
}
```

#### ⚠ 参数**不能**写类型标注

这是本文档过去写错、也是新手最容易踩的一条。**参数列表里只能写参数名。**给参数加类型标注是**语法错误**：

```
number_t func add(number_t a, number_t b) {   // ✗ 语法错误！
    return a + b;
}

number_t func add(a, b) {                      // ✓ 正确
    return a + b;
}
```

只有**返回类型**有类型说明符这个位置，参数没有。

- **返回类型**：只能是 `number_t`、`string_t`、`boolean_t`、`array_t`、`any_t` 这 5 个之一。没有 `void`，也没有 `dict_t`。
- **参数名**：只写名字，逗号分隔，可以一个都没有。
- **返回值**：通过 `return` 语句返回。没有 `return` 的函数返回 `null`。

**示例：**

```
// 返回数值
number_t func add(a, b) {
    return a + b;
}

// 返回字符串
string_t func greet(name) {
    return "Hello, " + name;
}

// 返回布尔值
boolean_t func isEven(num) {
    if (num % 2 == 0) {
        return true;
    } else {
        return false;
    }
}

// 没有返回值的函数，返回类型写 any_t
any_t func logMessage(msg) {
    output(msg);
}

// 无参数
any_t func banner() {
    output("=====");
}
```

**调用示例：**

```
var sum = add(10, 20);      // sum 为 30
var message = greet("QOT"); // message 为 "Hello, QOT"
var even = isEven(7);       // even 为 false
banner();
```

#### 返回类型到底管不管用

说清楚，免得误会：**返回类型标注在运行期不做任何检查**。你写 `number_t` 却 `return "abc";`，程序照跑，返回的就是字符串。

它的作用有两个：一是语法上必须有（不写编译不过）；二是给语义分析器和编辑器插件提供信息，用于静态提示。**不要把它当成运行期的类型保障。**

#### ⚠ 实参个数不匹配不会报错

QOT 不检查调用时的参数个数，而且多给参数时的行为可能出乎意料：

```
any_t func f(a, b) {
    output("a=" + strings(a) + " b=" + strings(b));
}

f(1, 2);        // a=1 b=2        正常
f(1);           // a=1 b=<none>   少给：缺的参数是「未赋值」
f(1, 2, 3);     // a=2 b=3        多给：取的是最后两个！
f(1, 2, 3, 4);  // a=3 b=4        多给：取的是最后两个！
```

**多给参数时，被丢掉的是*前面*的，不是后面的。**参数按栈顶对齐，最后 N 个实参绑到 N 个形参上。所以一个多写了参数的调用不会报错，只会安静地把值错位——这类 bug 极难发现，务必自己数清楚参数个数。

#### 其他注意事项

- 函数支持声明提升，可以在定义之前调用。
- 函数内部可以读全局变量；写全局变量的行为两个运行时不一致，见 [4.3](#scope)。
- QOT **没有匿名函数 / lambda / 闭包**。所有函数都必须具名、定义在顶层。
- QOT **没有默认参数值、没有可变参数、没有函数重载**。同名函数后定义的覆盖先定义的。
- 支持递归。

### 7.2 函数调用

```
functionName(argument1, argument2);
var r = functionName(1, 2);   // 也可以取返回值
```

⚠ **函数调用的返回值不能直接下标访问。**`getArr()[0]` 是语法错误，下标只能作用在变量名上。请先接一下：

```
var tmp = getArr();
var first = tmp[0];
```

### 7.3 函数引用

用 `&(?)` 取函数引用，主要用于 GUI 事件绑定这类「把函数当参数传」的场景。

#### ⚠ 语法：括号不能省

函数引用的写法是 `&(?)函数名(参数...)`。**后面那对括号是语法的一部分，即使没有参数也必须写。**

```
&(?)myHandler        // ✗ 语法错误：少了括号
&(?)myHandler()      // ✓ 正确：引用 myHandler，不预置参数
&(?)myHandler(5)     // ✓ 正确：引用 myHandler，并预置实参 5
```

#### 括号里的参数是「预置实参」

这里的括号**不是立即调用**，而是把实参先绑定下来，等事件真正发生时再连同事件自己的参数一起交给函数——也就是常说的**偏应用**。

```
any_t func bump(step) {
    count = count + step;
}

vg_button_on_click(btn, &(?)bump(1));
// 点一次按钮，等价于调用 bump(1)
```

如果事件本身也带参数（比如滑块的当前值），**预置参数排在前面，事件参数排在后面**：

```
any_t func onVol(label, value) {
    output(label + ": " + strings(value));
}

vg_slider_on_changed(sl, &(?)onVol("音量"));
// 拖动滑块，等价于调用 onVol("音量", 0.7)
```

#### 函数引用是一等值

函数引用求值后是一个普通的值，可以存进变量、放进数组、当参数传：

```
var handler = &(?)bump(1);
vg_button_on_click(btn1, handler);
vg_button_on_click(btn2, handler);   // 同一个引用可以绑多处
```

实现上它就是一个数组：`[函数, 预置参数1, 预置参数2,...]`。知道这一点有助于理解上面的参数顺序。

事件绑定的三种等价写法，详见 [第 22 章](#gui)。

### 7.4 内置函数

QOT 提供了大量内置函数。**⚠ 两个运行时的内置函数表并不相同**——有些函数只有解释器有，有些只有字节码 VM 有。完整分表见 `funcdoc.html`，这里只列两边都有、最常用的几个：

| 函数 | 说明 |
| --- | --- |
| output(v) | 输出一个值并换行 |
| cinget() | 读取一行输入 |
| glen(v) | 取数组或字符串的长度 |
| strings(v) | 转成字符串 |
| push(arr, v) | 向数组末尾追加元素 |
| pow(a, b) | 幂运算（等价于 a ^ b） |
| sqrt(x) | 平方根 |
| dict_get / dict_set / dict_keys | 字典操作 |
| get_env / set_env / unset_env | 环境变量 |
| fopen / fread / fwrite / fclose | 文件读写 |
| sleep(ms) | 休眠 |

**几个常见误会：**

- `numbers()`（转数字）、`type()`（取类型名）**只有字节码 VM 有**，解释器里调用会返回 `null`。
- `max()`、`min()`、`sum()`、`batch_push()`、`prog_end()`、`utf8_enc()`/`utf8_dec()` **只有解释器有**，字节码 VM 里返回 `null`。要在 VM 上求最大值，请自己写循环。
- 调用一个不存在的内置函数**不会报错**，只会得到 `null`。这是最容易浪费时间的坑——发现结果莫名其妙是 `null` 时，先确认这个函数在你用的运行时里存在。

示例：

```
output("Hello, World!");     // 输出字符串

var x = cinget();            // 获取输入

var len = glen(array);       // 获取数组长度

set_env("MY_VAR", "value");
var val = get_env("MY_VAR", "default");
```

## 8. 数组操作

### 8.1 创建数组

```
var emptyArr = [];                    // 空数组
var numbers = [1, 2, 3, 4, 5];       // 数字数组
var mixed = [1, "hello", true, null]; // 混合类型数组
```

### 8.2 访问数组元素

使用索引访问数组元素，**索引从 0 开始**：

```
var fruits = ["apple", "banana", "cherry"];
var first = fruits[0];   // "apple"
var last = fruits[2];     // "cherry"
```

### 8.3 修改数组元素

```
fruits[1] = "orange";     // 将 "banana" 替换为 "orange"
```

### 8.4 数组常用方法（通过 `->` 调用）

- **`push(元素)`**：在数组末尾添加一个元素
- **`remove(元素)`**：**按值**移除第一个匹配的元素（参数是元素值，不是下标）
- **`pop(索引)`**：**按下标**移除指定位置的元素
- **`get(元素)`**：**按值**查找，返回该元素的下标
- **`glen()`**：获取数组长度

⚠ 特别注意 `remove` 和 `pop` 的区别：**一个吃值，一个吃下标**。`arr->remove(2)` 是「删掉值为 2 的元素」，`arr->pop(2)` 是「删掉第 3 个元素」。写反了不会报错。

这些方法**直接修改原数组**，不返回新数组。

### 8.5 数组操作示例

```
var arr = [1, 2, 3];
arr->push(4);           // arr = [1, 2, 3, 4]
arr->remove(2);         // 按值删除 → arr = [1, 3, 4]
var len = arr->glen();  // len = 3
var idx = arr->get(3);  // idx = 1（值 3 所在的下标）
arr->pop(0);            // 按下标删除 → arr = [3, 4]
```

`batch_push(arr, more)`（批量追加）**只有解释器有**，字节码 VM 上不可用。在 VM 上请自己循环 `push`。

### 8.6 遍历数组

```
var items = ["a", "b", "c"];
for (item in items) {
    output(item);  // 输出 a, b, c —— 拿到的是元素值，不是下标
}

// 需要下标时，用数字范围遍历
for (i in glen(items)) {
    output(strings(i) + ": " + items[i]);
}
```

### 8.7 数组越界处理

**两个运行时对越界的态度完全相反，这是必须先搞清楚的一点。**

| 操作 | run（解释器） | bstrust run（VM） |
| --- | --- | --- |
| 越界读 arr[5] | 报错 2003 并退出数组索引 5 越界（长度为 3） | 得到 null，继续执行 |
| 越界写 arr[5] = 99; | 报错并退出 | 静默忽略，数组不变，不报错 |

```
var arr = [1, 2, 3];

var x = arr[5];    // 解释器：报错退出　　VM：x = null
arr[5] = 99;       // 解释器：报错退出　　VM：静默忽略，arr 仍是 [1, 2, 3]

output(x);         // VM: null
output(arr);       // VM: [1, 2, 3]
```

VM 这么做是语言的设计取向：**性能优先，边界检查交给使用者。**不为每次下标访问付出一次比较的代价。

代价是这类错误会一路沉默地传下去——`null` 参与运算，再产生新的 `null`，最后在离出错点很远的地方才表现出来。**所以下标只要不是你亲手算出来必定合法的，就先自己判一下：**

```
if (i >= 0 && i < glen(arr)) {
    output(arr[i]);
} else {
    output("下标越界: " + strings(i));
}
```

💡 **实用建议：**开发调试期用 `qguoat run` 跑解释器，它会把越界当场炸给你看；确认干净了再编译成 `.qtc` 上线拿性能。这样既不丢速度，也不至于让越界悄悄溜到线上。

字典的行为则两边一致：访问不存在的键得到 `null`，都不报错。

## 9. 字典操作

### 9.1 访问字典元素

```
var person = &< "name": "Alice", "age": 30 >&;

var name = person["name"];  // 获取"name"对应的值
```

### 9.2 修改字典元素

```
person["age"] = 31;  // 修改"age"对应的值
```

### 9.3 字典函数

```
var keys = dict_keys(person);           // 获取所有键
dict_set(person, "city", "Beijing");    // 设置键值
var city = dict_get(person, "city");    // 获取值（不存在返回null）
```

## 10. 注释与注解

### 10.1 注释

如 2.2 节所述，QOT 只有 `//` 单行注释，没有块注释。

### 10.2 注解

以 `@-@-@` 开头的行是**注解**。它是一条独立语句，必须**独占一行**，不能跟在别的代码后面。

注解不产生任何运行时行为，用来给编译器传递指令。目前实际生效的只有一条：

```
@-@-@ rule::nowarning
```

它会关闭语义分析器的警告输出（比如「变量未定义」这类提示）。**注意它关掉的只是警告，语法错误照样会让编译失败。**

其他内容的 `@-@-@` 行会被安静忽略，所以也常被当成「块注释的替代品」用来写多行说明：

```
@-@-@ 计数器示例
@-@-@ count 是全局变量，回调里的修改会保留
var count = 0;
```

## 11. 模块引入

使用 `require` 关键字引入模块，这是 QOT 组织代码和复用功能的主要方式。

### 11.1 基本语法

```
require "模块路径";
```

- 语句必须以分号 `;` 结尾
- 模块路径可以是文件名或相对/绝对路径
- 模块名取自文件名（不含扩展名）

### 11.2 模块类型

QOT 支持两种模块类型：

| 类型 | 扩展名 | 说明 |
| --- | --- | --- |
| 脚本模块 | .qot | QOT 源代码文件。编译时必须加 --inline 1，否则运行会失败，见 §11.6 |
| 动态库模块 | .dll | Windows 动态链接库，运行时加载，不受 --inline 影响 |

### 11.3 脚本模块 (.qot)

**先记住这一条：引用 `.qot` 模块时，编译命令必须写 `--inline 1`。**

```
qguoat bstrust compile main.qot main.qtc --inline 1
```

不写就会在运行时报「无法加载库: utils.qot / 错误码: 193」。详见 [§11.6](#inline)。

#### 基本用法

**utils.qot**

```
var APP_NAME = "MyApp";

any_t func sayHello(name) {
    output("Hello, " + name);
}

number_t func add(a, b) {
    return a + b;
}
```

**main.qot**

```
require "utils.qot";

utils##sayHello("QOT");
var result = utils##add(3, 5); // result = 8
```

#### 两种调用形式都可以

脚本模块被内联后，它的函数和变量既能**带模块名前缀**访问，也能**直接访问**——两种写法完全等价，指向同一个东西。

```
require "utils.qot";

output(utils##add(3, 5));   // 8       带前缀
output(add(1, 2));          // 3       不带前缀，同一个函数
output(utils##APP_NAME);    // MyApp   带前缀取变量
output(APP_NAME);           // MyApp   不带前缀，同一个变量
```

**建议一律带前缀写。** 不带前缀虽然能跑，但模块里的名字是直接摊进全局作用域的：主程序里再定义一个同名 `add`，就会悄悄把模块里的覆盖掉，不报任何错。带上 `utils##` 至少能让读代码的人知道这个名字是从哪来的。

**⚠ 会看到一条误报警告**

语义检查跑在内联之前，它不知道 `APP_NAME` 来自模块，于是会打印：

```
[警告] [2000]
变量 'APP_NAME' 未定义
```

这是**警告不是错误**，编译照常完成，程序运行正常。嫌吵可以在文件开头写 `@-@-@ rule::nowarning` 关掉。

#### `##` 是模块访问符

模块成员访问只有 `##` 这一种写法。`::`、`.`、`->` 都**不是**模块访问符：

```
utils##add(3, 5);    // ✓ 唯一正确写法
utils::add(3, 5);    // ✗ 语法错误
utils.add(3, 5);     // ✗ 语法错误
utils->add(3, 5);    // ✗ 这是方法调用语法，含义完全不同，见第 15 章
```

#### 嵌套引用

模块内部可以继续 `require` 其他模块，QOT 会自动处理依赖关系：

**a.qot**

```
require "b.qot";
any_t func funcA() {
    output("A");
    funcB();
}
```

**b.qot**

```
any_t func funcB() {
    output("B");
}
```

**main.qot**

```
require "a.qot";
funcA();  // 输出 "A" 和 "B"
```

#### 循环依赖

QOT 会检测循环依赖并报错：

```
// a.qot
require "b.qot";  // 错误：循环依赖 a -> b -> a
```

### 11.4 动态库模块 (.dll)

#### 基本用法

```
require "user32.dll";
var result = user32##MessageBoxA(0, "Hello", "Title", 0);
```

#### 调用语法

与脚本模块不同，DLL 模块必须使用 `模块名##函数名` 语法调用：

```
require "miaudio.dll";
var player = miaudio##MusicPlayer_Create();
miaudio##MusicPlayer_Play(player);
```

#### 导出表要求

DLL 模块需要附带 `export.func` 文件，格式为每行一条函数定义：

```
函数名|参数类型列表|返回类型
```

**示例 export.func：**

```
add|int,int|int
greet|str|str
processData|ptr,int|void
```

**支持的类型：**

- `int` - 整数
- `str` - 字符串（UTF-8）
- `bool` - 布尔值
- `ptr` - 指针（用于输出参数）
- `void` - 无返回值
- `hwnd` - 窗口句柄
- `handle` - 通用句柄

#### 安全验证

DLL 模块加载时会验证 `export.func` 的数字签名，验证失败则拒绝加载。

### 11.5 路径解析规则

`require` 按以下顺序查找模块：

1. 当前文件所在目录
2. 执行目录
3. 系统模块目录（`package/` 文件夹）

```
require "module.qot";        // 当前目录
require "./lib/module.qot";  // 相对路径
require "C:/modules/mymod";  // 绝对路径（可省略 .qot）
```

### 11.6 编译选项 `--inline`

用 `bstrust compile` 编译时，`--inline` 控制脚本模块的处理方式。

**⚠ 最重要的一条：`--inline` 默认是 0（关闭），而 `.qot` 脚本模块在关闭时根本用不了。**

非内联模式下，`require "utils.qot";` 会被 VM 当成**动态库**去 `LoadLibrary`，然后报「不是有效的 Win32 应用程序（错误码 193）」。
 **只要用了 `.qot` 模块，就必须显式加 `--inline 1`。**

```
# 引用了 .qot 模块，必须这样编译
qguoat bstrust compile main.qot main.qtc --inline 1
```

#### 内联模式（`--inline 1`）

- 编译器查找并解析被引用的 `.qot` 脚本模块
- 模块中的函数和变量直接合并进主程序
- 生成的 `.qtc` **独立运行**，不再需要原始模块文件
- **用 `.qot` 模块时唯一可用的模式**

#### 非内联模式（`--inline 0`，默认）

- 保留 `require` 语句，运行期再加载
- **只适用于 `.dll` 模块**；对 `.qot` 模块会直接失败

#### 示例

**utils.qot**

```
var APP = "MyApp";

number_t func add(a, b) {
    return a + b;
}
```

**main.qot**

```
require "utils.qot";

output(utils##add(3, 5));   // 8   —— 带模块名前缀
output(add(1, 2));          // 3   —— 直接调用也可以
output(APP);                // MyApp
```

**编译并运行：**

```
qguoat bstrust compile main.qot main.qtc --inline 1
qguoat bstrust run main.qtc
```

⚠ 内联模式下，语义分析器不认识来自模块的变量，会打印一条「变量 'APP' 未定义」的**警告**。这是误报，程序运行正常，可以用 `@-@-@ rule::nowarning` 关掉。

**注意：** DLL 模块（`.dll`）不受 `--inline` 影响，始终是运行时动态加载。

### 11.7 注意事项

- **用 `.qot` 模块就得写 `--inline 1`**：这是最容易踩的坑，默认值 0 对脚本模块是不能用的
- **脚本模块的名字会摊进全局**：`utils##add()` 和 `add()` 都能调，但也意味着主程序里的同名定义会覆盖模块的，且不会报错。建议一律带 `##` 前缀
- **DLL 必须带前缀**：DLL 模块只能用 `模块名##函数名`，没有免前缀写法
- **模块只加载一次**：多次 `require` 同一模块不会重复展开
- **内联期的「未定义」警告是误报**：语义检查早于内联，看到模块里的名字会报警告，不影响编译和运行
- **路径区分大小写**：在大小写敏感的文件系统上需注意
- **DLL 需要导出表**：没有正确 `export.func` 的 DLL 无法加载
- **内联后模块固定**：内联模式编译后模块代码已合并进 `.qtc`，改了模块必须重新编译

## 12. 字符串扩展

⚠ **本章的两个前缀只在解释器上有意义，在字节码 VM 上都得到 `null`。生产代码请不要用。**

### 12.1 Unicode 字符串 `U"..."`

`U"..."` 前缀产生一个**字节序列**（对应 Python 的 bytes），而不是普通字符串。

```
var utf8_bytes = U"中文内容";
output(utf8_bytes);   // 解释器：<<E4 B8 AD E6 96 87>> 这样的十六进制形式
```

| 运行时 | U"中文" 的结果 |
| --- | --- |
| 解释器 | 字节序列，打印为 <<E4 B8 AD E6 96 87>> |
| 字节码 VM | null —— 字节类型无法序列化进.qtc，值直接丢失 |

需要在 VM 上处理 UTF-8 字节，请改用普通字符串加 `json_encode` / 文件读写这类接口。

### 12.2 宽字符串 `L"..."`（Windows）

```
var wide_str = L"宽字符串";
```

| 运行时 | L"宽字符串" 的结果 |
| --- | --- |
| 解释器 | 就是一个普通字符串，gtypes() 返回 "string"，L 前缀没有实际效果 |
| 字节码 VM | null |

**结论：这两个前缀现在都不要用。**QOT 的普通字符串本来就是 UTF-8 的，中文可以直接写在 `"..."` 里，两个运行时都正常。

## 13. 程序执行与编译

下面用 `qguoat` 表示 QOT 的命令行入口。从源码仓库直接跑时，把 `qguoat` 换成 `python index.py` 即可，参数完全一样。

### 13.1 用解释器执行（调试用）

```
qguoat run program.qot
```

直接解释执行 `.qot` 源文件。启动快，适合改一行看一眼；但执行慢，而且**不支持 `vg_*` GUI**。

`--console` 把脚本输出转到网页运行台 `localhost:8000`：

```
qguoat run program.qot --console
```

### 13.2 用字节码 VM 执行（推荐）

分两步：先编译成 `.qtc`，再运行。

```
qguoat bstrust compile program.qot program.qtc
qguoat bstrust run program.qtc
```

可选参数：

| 参数 | 用在 | 说明 |
| --- | --- | --- |
| --opt | compile | 开启 AST 层优化（常量折叠等），详见 18.4 |
| --inline 0\|1 | compile | 默认 0（关）。引用了.qot 脚本模块时必须显式写 --inline 1，否则运行时报错码 193，见 §11.6 |
| --debug / -d | run | 打印 VM 调试信息 |

**编译失败时不会生成 `.qtc`，退出码为 1。**看到「编译成功」才说明整个文件都被正确解析了。

### 13.3 编译为 EXE

```
qguoat build program.qot
```

详见第 17 章。

### 13.4 QBC 字节码

用 `distrust` 命令把源码编译成不可直接阅读的 QBC 字节码：

```
qguoat distrust input.qot [output.qbc]
qguoat run script.qbc
```

⚠ QBC（`.qbc`）和 QTC（`.qtc`）是**两种不同的东西**，别搞混：

|  | QBC.qbc | QTC.qtc |
| --- | --- | --- |
| 内容 | 编码后的 AST | 虚拟机指令序列 |
| 由谁执行 | Python 解释器（run） | C++ 字节码 VM（bstrust run） |
| 目的 | 源码保护，不给人直接读 | 性能 |
| 速度 | 和解释源码基本一样 | 快得多 |

要性能用 QTC，要藏源码用 QBC。QBC 不会让程序变快。

## 14. 类型系统

QOT 是一种**动态类型**语言。变量没有类型，值才有类型，一个变量随时可以换存别的类型。

类型说明符只是**写给人和静态检查工具看的标注**，运行期一律不生效。

### 14.1 类型说明符

一共只有 5 个：

- `number_t`：数值（整数和浮点数不区分，内部统一是双精度浮点）
- `string_t`：字符串
- `boolean_t`：布尔值（true/false）
- `array_t`：数组
- `any_t`：任意类型

⚠ **没有 `dict_t`，也没有 `void`、`int`、`float`、`char`。**字典和「无返回值」都写 `any_t`。

### 14.2 类型说明符只能出现在一个位置

**函数返回类型**——就这一处，没有别的地方能写。

```
number_t func add(a, b) {      // ✓ func 前面是返回类型
    return a + b;
}
```

下面这些写法**全是语法错误**：

```
number_t func add(number_t a, number_t b) { ... }  // ✗ 参数不能标类型
number_t var x = 1;                                 // ✗ 变量不能标类型
number_t x = 1;                                     // ✗ 同上
```

### 14.3 类型检查机制

说明白，免得产生错误预期：

- **运行期完全不检查。**声明 `number_t` 的函数 `return "abc";` 照样跑通，返回字符串。
- **编译期只有语义分析器的提示**，而且是**警告级**——打印一条消息，编译继续，照常产出 `.qtc`。
- 用 `@-@-@ rule::nowarning` 可以把这些警告全部关掉。

所以类型说明符的实际价值是：**让读代码的人和编辑器知道你的意图**。别把它当类型安全网。

### 14.4 获取值的类型

用 `gtypes()` 获取一个值的类型名（两个运行时都有）：

| 值 | gtypes() 返回 |
| --- | --- |
| 42 | "number" |
| "hi" | "string" |
| true | "boolean" |
| [1, 2] | "array" |
| &< "a": 1 >& | "dict" |
| null | "null" |

注意返回的是类型**名字符串**（`"number"`），跟类型说明符（`number_t`）不是一个东西，别拿去比较。

```
if (gtypes(x) == "number") {    // ✓
    output("是数字");
}
```

字节码 VM 上另有一个 `type()`，作用类似，但**解释器没有**——为了两边都能跑，统一用 `gtypes()`。

## 15. 方法调用语法

使用 `->` 箭头语法调用方法：

```
object->methodName(arg1, arg2);
```

本质上 `a->f(b)` 就是 `f(a, b)` 的另一种写法——箭头左边的东西作为第一个实参传进去。所以能这样调用的，都是普通的内置函数：

```
var arr = [1, 2, 3];
arr->push(4);            // 等价于 push(arr, 4)
arr->remove(2);          // 等价于 remove(arr, 2)
var len = arr->glen();   // 等价于 glen(arr)
```

⚠ **这不是面向对象。**QOT 没有类、没有对象、没有 `this`。`->` 纯粹是「把左边当第一个参数」的语法糖，用户自定义函数也一样适用：

```
any_t func twice(x) {
    return x + x;
}

var n = 5;
output(n->twice());   // 10，等价于 twice(n)
```

这个写法在链式处理数组时最好用，其余场合按普通函数调用写就行，不必刻意用箭头。

## 16. 字节码保护（QBC）

QOT 支持将源码编译为不可直接阅读的 QBC 文件，用于代码分发和保护。

**先分清 QBC 和 QTC，这是两个完全不同的东西：**

| 格式 | 内容 | 生成 | 执行 | 用途 |
| --- | --- | --- | --- | --- |
| .qbc | 序列化后的 AST | distrust | qguoat run（解释器） | 防止源码被直接阅读 |
| .qtc | 真正的字节码 | bstrust compile | bstrust run（C++ VM） | 性能 |

**QBC 跑在解释器上，不快**；要性能请用 QTC（第 18 章）。两者不能互相转换，也不能互相执行。

### 16.1 生成 QBC 文件

```
qguoat distrust input.qot [output.qbc]
```

省略输出名时，默认把 `.qot` 换成 `.qbc`。示例：

```
qguoat distrust myprogram.qot            # 生成 myprogram.qbc
qguoat distrust myprogram.qot protected.qbc
```

### 16.2 执行 QBC 文件

```
qguoat run script.qbc
```

`run` 只接受 `.qot` 和 `.qbc` 两种后缀，走的都是解释器，因此 QBC 里能用的内置函数和解释器完全一致（见[第 21 章](#runtime-diff)）。

### 16.3 特点

- 二进制格式，不可直接阅读
- 体积比源码更小
- 适用于代码分发场景
- 执行行为与直接跑 `.qot` 完全相同

⚠ **这只是「不可直接阅读」，不是加密。** 文件里装的是完整的 AST，有心人反序列化回来照样能还原出程序结构和全部字符串常量。它挡的是随手打开记事本看一眼，挡不住真正想逆向的人。

## 17. 编译为原生可执行文件 (Build to EXE)

QOT 支持将脚本编译为独立的 Windows 可执行文件（.exe），方便分发和部署，无需依赖解释器环境。

### 17.1 编译命令

推荐使用具名参数形式：

```
qguoat build <file.qot|file.qtc> [--mode 1|2|3] [--icon path] [--exportfile path]... [--output path]
```

- **file**：源文件，后缀必须是 `.qot` 或 `.qtc`（已编译的字节码也能直接打包）
- **--mode**（可选）：编译模式，默认 `2`
- **--icon**（可选）：图标路径（`.ico`，**仅模式 2 生效**）
- **--exportfile**（可选，可重复）：额外打包进 exe 的资源文件
- **--output**（可选）：输出路径，**仅模式 3 生效**；模式 1、2 会忽略它

也支持不写参数名的简写形式——纯数字被当作 mode，存在的 `.ico` / `.png` 文件被当作图标：

```
qguoat build program.qot 2 logo.ico
# 等价于
qguoat build program.qot --mode 2 --icon logo.ico
```

其余认不出来的参数只会打一行「警告: 未知参数」然后被忽略，**不会报错也不会中止**。参数拼错了程序照样编译，只是你想要的选项没生效——写完命令请看一眼有没有这行警告。

### 17.2 编译模式

QOT 提供三种编译模式，以适应不同的使用场景：

| 模式 | 说明 | 适用场景 | 维护状态 |
| --- | --- | --- | --- |
| 1 | 传统编译模式 | 兼容旧版脚本 | 有限维护，不推荐新项目使用 |
| 2 | 高性能编译模式（默认） | 推荐使用，性能较优 | 维护中 |
| 3 | 独立打包模式（V3） | 编译速度更快，冷启动更快 | 可用 |

#### 模式 1（传统模式）

传统编译模式仅用于兼容旧版语法编写的脚本。**注意：该模式已进入有限维护阶段，仅修复严重问题，不增加新特性支持。**若您的脚本在此模式下编译失败，建议升级脚本语法并切换至模式 2 或模式 3。

```
qguoat build program.qot 1
```

⚠ 模式 1 **不给图标就会停下来问你**：先问「目前只支持部分语法编译,确定要继续吗? y/n:」，回 y 之后再让你输入图标路径。也就是说它**不能用在脚本、CI 这类无人值守的场景**——除非你在命令行里先把 `--icon` 给上，那样就不会有任何交互。

#### 模式 2（高性能模式，默认）

高性能模式是推荐的编译方式之一。

```
qguoat build program.qot 2
# 或省略 mode 参数，默认使用模式 2
qguoat build program.qot
```

#### 模式 3（独立打包模式 V3）

模式 3 可将脚本打包为独立的可执行文件。相比模式 2，模式 3 在编译速度和程序冷启动速度上均有一定优势。

**基本用法：**

```
qguoat build program.qot --mode 3
```

**完整参数示例：**

```
qguoat build program.qot --mode 3 --icon app.ico --exportfile config.json --output ./release/myapp.exe
```

**参数说明：**

- `--mode 3`：指定使用 V3 打包模式
- `--icon`：**模式 3 下无效**，参数会被接受但不会写进 exe（见 §17.3）
- `--exportfile`：打包额外资源文件（可多次使用）
- `--output`：指定输出文件路径，**只有模式 3 认这个参数**

### 17.3 自定义图标

编译时可以指定应用程序图标：

```
qguoat build program.qot 2 logo.ico
```

**注意：** 该图标参数仅在模式 2 下生效。模式 3 下 `--icon` 参数不会为生成的 exe 添加图标，如需为模式 3 生成的可执行文件设置图标，请自行使用 Resource Hacker、IconChanger 等第三方工具添加。

### 17.4 打包额外资源文件（--exportfile）

使用 `--exportfile` 参数可以将资源文件一并打包到生成的可执行文件中。程序运行时，这些文件可供脚本正常访问。

在实际开发中，脚本可能需要读取外部文件（如配置文件、文本资源等）。编译时通过 `--exportfile` 将这些文件打包，生成的可执行文件即可独立运行，无需附带原始文件。

#### 示例场景：读取文本文件

假设有一个脚本 `readfile.qot`，内容如下：

```
var a = fopen("hello.txt", "r");
var b = fread(a);
fclose(a);
output(b);
```

**情况一：未使用 --exportfile**

直接编译生成的 exe 运行时，需要在同目录下存在 `hello.txt` 文件，否则程序将报错。

```
qguoat build readfile.qot
# 运行 readfile.exe 时需要同目录下有 hello.txt
```

**情况二：使用 --exportfile 打包资源**

将 `hello.txt` 一同打包进 exe，生成的可执行文件可独立运行，无需附带原始文本文件。

```
qguoat build readfile.qot --exportfile hello.txt
# 运行 readfile.exe 时无需额外携带 hello.txt
```

**多个资源文件打包示例：**

```
qguoat build script.qot --mode 2 --icon app.ico --exportfile config.json --exportfile data.txt --exportfile logo.png
```

### 17.5 编译输出

模式 1、2 编译成功后，可执行文件输出到项目目录下的 `build` 文件夹，文件名为源文件名（不含扩展名）。

```
build/
├── readfile.exe         # 编译生成的可执行文件
```

模式 3 若指定了 `--output`，则输出到指定路径；未指定时同样落在 `build/` 下。

### 17.6 注意事项

- 编译前请确保脚本语法正确
- **强烈建议优先使用默认的高性能模式（模式 2）或独立打包模式（模式 3）**
- 若使用模式 1 遇到编译失败，请尝试切换至模式 2 或模式 3，或检查脚本是否使用了较新的语法特性
- 模式 1 仅作为过渡兼容方案保留，新编写的脚本请直接使用模式 2 或模式 3
- 使用 `--exportfile` 打包资源文件时：
    - **模式 2**：最多支持 15 个资源文件
    - **模式 3**：无数量限制
- **模式 3 图标支持：** `--icon` 参数在模式 3 下不会生效，生成的可执行文件不包含图标。如需设置图标，请使用 Resource Hacker、IconChanger 等第三方工具自行添加。

## 18. QTC 字节码格式

QOT 提供 QTC 字节码格式，执行效率更高，适合生产环境部署。

### 18.1 编译为 QTC

```
qguoat bstrust compile input.qot [output.qtc]
```

示例：

```
qguoat bstrust compile myprogram.qot
qguoat bstrust compile myprogram.qot program.qtc
```

### 18.2 运行 QTC 文件

```
qguoat bstrust run file.qtc
qbvot file.qtc
```

### 18.3 QTC 特性

- 二进制字节码格式，不可直接阅读
- 执行速度比源码和 QBC 更快
- 适用于频繁执行的程序
- 推荐生产环境使用

### 18.4 编译优化选项 --opt

`bstrust compile` 命令支持 `--opt` 参数，用于在编译时对代码进行优化。优化过程会分析代码结构，消除冗余和不可达的部分，从而生成更高效、更紧凑的 QTC 字节码。

#### 用法示例

```
# 启用优化
qguoat bstrust compile input.qot --opt

# 同时指定输出文件并启用优化
qguoat bstrust compile input.qot output.qtc --opt

# 结合内联模式使用
qguoat bstrust compile main.qot --inline 1 --opt
```

#### 优化效果

启用 `--opt` 后，编译器会执行以下优化：

- **常量折叠**：将编译期可计算的表达式直接替换为结果值
- **死分支移除**：删除条件恒为假或恒为真的分支代码
- **不可达代码删除**：移除 `return` 或 `stop` 之后的无效语句
- **未使用变量消除**：清理未被引用的变量声明

#### 性能对比参考

| 场景 | 未优化 | 启用 --opt |
| --- | --- | --- |
| 代码体积 | 基准 | 减少约 5%~15% |
| 运行时执行效率 | 基准 | 部分场景提升 10%~30% |

#### 注意事项

- `--opt` 参数仅在编译为 `.qtc` 字节码时生效
- 优化过程不会改变程序的原始逻辑和行为
- 建议在正式发布或部署时启用优化，调试阶段可暂不使用以便于排查问题

### 18.5 循环表达式加速执行

从 V1 版本开始，虚拟机在执行循环时，能够自动识别并优化符合特定条件的数值计算表达式，从而显著提升循环执行效率。该优化对开发者完全透明，无需任何额外配置。

#### 18.5.1 优化原理

当虚拟机执行循环时，会分析循环体内部的表达式结构。对于满足条件的纯数值计算表达式，虚拟机会将其转换为一组紧凑的内部指令序列。在后续循环迭代中，表达式直接以指令序列形式执行，避免了每次迭代重复进行表达式解析和语法分析的开销。

#### 18.5.2 触发条件

优化自动生效需同时满足以下条件：

| 条件 | 说明 |
| --- | --- |
| 循环结构 | 仅对 loop for 循环体中的表达式进行优化 |
| 纯数值运算 | 表达式中仅包含数值类型的变量和常量，支持加减乘除等基本算术运算 |
| 无函数调用 | 表达式中不包含自定义函数或内置函数调用 |
| 无复杂控制流 | 表达式内部不包含条件跳转逻辑 |

#### 18.5.3 性能收益示例

**适用优化的写法：**

```
var sum = 0;
var n = 1000000;
loop (n > 0) {
    sum = sum + n * 2;
    n = n - 1;
}
```

**触发效果：** 当循环次数超过 1000 次时，执行效率可提升 30% 至 200%。循环次数越大，收益越明显。

**不适用优化的场景：**

```
loop (count > 0) {
    result = result + process(data);  // 包含函数调用，不触发优化
    str = str + "x";                   // 字符串拼接，不触发优化
    arr[i] = arr[i] * 2;              // 数组成员访问，不触发优化
    count = count - 1;
}
```

#### 18.5.4 注意事项

- **结果等价性**：优化只改变表达式的内部执行路径，不改变最终计算结果。
- **适用规模**：循环次数较少（少于 100 次）时，优化效果不明显，虚拟机会根据实际情况自动决策。
- **类型一致性**：循环表达式中涉及的所有变量建议保持数值类型一致，避免隐式类型转换带来的额外开销。
- **变量修改检测**：如果参与计算的变量在循环内部同时被修改，优化效果可能略有下降，但结果始终保持正确。

## 19. 代码签名与安全

QOT 支持代码签名验证，确保模块来源可信：

### 19.1 DLL 模块签名

使用 `export.func` 文件配合 HMAC-SHA256 签名验证：

```
// export.func 格式
函数名|参数类型|返回类型
// 示例：
add|int,int|int
process|str,ptr|void
```

### 19.2 支持的类型映射

`export.func` 里写的类型名，对应到 ctypes 的实际类型如下：

| export.func 中写法 | 对应 C 类型 | 说明 |
| --- | --- | --- |
| int | c_int | 32 位整数 |
| float | c_float | 单精度浮点 |
| double | c_double | 双精度浮点 |
| str | c_char_p | 字符串（UTF-8 编码传入） |
| bool | c_bool | 布尔值 |
| ptr | 指针 | 用于输出参数 |
| void | — | 无返回值 |
| hwnd | HWND | 窗口句柄 |
| handle | HANDLE | 通用句柄 |
| hmodule | HMODULE | 模块句柄 |
| hinstance | HINSTANCE | 实例句柄 |
| hdc | HDC | 设备上下文句柄 |
| hkey | HKEY | 注册表键句柄 |
| lparam | LPARAM | 消息参数（Long） |
| wparam | WPARAM | 消息参数（Word） |
| dword_ptr | DWORD_PTR | 指针大小的无符号整数 |

⚠ 类型写错不会有任何提示——参数会按你写的类型硬转过去交给 DLL。`int` 写成 `ptr` 之类的错误，轻则拿到垃圾值，重则直接把进程搞崩。**这是本语言「性能优先、安全交给使用者」原则最锋利的地方：调 DLL 等于你自己在写 C，出了事是调用方的责任。**

> **安全提示：** 所有 DLL 模块加载前都会验证签名，签名验证失败将拒绝加载。

## 20. 错误处理系统

QOT 语言内置了完善的错误处理系统，当程序出现语法错误、运行时错误或类型错误时，系统会自动捕获并以友好的格式输出错误信息，帮助开发者快速定位问题。

### 20.1 错误信息格式

当 QOT 程序发生错误时，系统会输出以下格式的错误信息：

```
[错误类型] [错误码]
错误描述
  --> 源文件名:行号
```

**示例输出：**

```
[错误] [2001]
变量 'count' 未定义
  --> main.qot:line(15)
```

### 20.2 错误类型与错误码

QOT 错误系统将错误分为以下几个大类，每个错误都有唯一的错误码：

#### 语法错误 (1000-1099)

| 错误码 | 常量名 | 说明 |
| --- | --- | --- |
| 1000 | ERR_SYNTAX_GENERAL | 语法错误（通用） |
| 1001 | ERR_SYNTAX_MISSING_SEMICOLON | 缺少分号 ';' |
| 1002 | ERR_SYNTAX_MISSING_PAREN | 缺少括号 '()' |
| 1003 | ERR_SYNTAX_MISSING_BRACE | 缺少花括号 '{}' |
| 1004 | ERR_SYNTAX_MISSING_BRACKET | 缺少方括号 '[]' |
| 1005 | ERR_SYNTAX_UNEXPECTED_TOKEN | 意外的符号/关键字 |
| 1006 | ERR_SYNTAX_INVALID_IDENTIFIER | 无效的标识符 |
| 1007 | ERR_SYNTAX_INVALID_NUMBER | 无效的数字格式 |
| 1008 | ERR_SYNTAX_UNTERMINATED_STRING | 字符串未正确闭合 |

#### 运行时错误 (2000-2099)

| 错误码 | 常量名 | 说明 |
| --- | --- | --- |
| 2000 | ERR_RUNTIME_GENERAL | 运行时错误（通用） |
| 2001 | ERR_NAME_NOT_DEFINED | 变量/函数未定义 |
| 2002 | ERR_TYPE_MISMATCH | 类型不匹配 |
| 2003 | ERR_INDEX_OUT_OF_RANGE | 索引超出范围 |
| 2004 | ERR_DIVISION_BY_ZERO | 除以零 |
| 2005 | ERR_VALUE_ERROR | 值错误 |
| 2006 | ERR_CONST_MODIFICATION | 不能修改常量 |
| 2007 | ERR_RETURN_OUT_OF_FUNCTION | return语句不在函数中 |
| 2008 | ERR_STOP_OUT_OF_LOOP | stop语句不在循环中 |
| 2009 | ERR_INVALID_OPERATION | 无效的操作 |
| 2010 | ERR_FUNCTION_ARGS_INVAILD | 函数参数数量不匹配 |
| 2011 | ERR_FUNCTION_NOTFOUND | 函数未定义 |
| 2012 | ERR_FUNCTION_ARGS_TYPEINV | 函数参数类型不匹配 |
| 2013 | ERR_FUNCTION_NOTIT | 对象不是函数 |
| 2014 | ERR_FUNCTION_NOT_ISIN | 函数已存在，无法重复注册 |
| 2015 | ERR_FUNCTION_DEFINITION_FAILED | 函数定义失败 |

#### 循环相关错误 (2020-2029)

| 错误码 | 常量名 | 说明 |
| --- | --- | --- |
| 2020 | ERR_FOR_RANGE_NEGATIVE | for循环范围不能为负数 |
| 2021 | ERR_FOR_RANGE_INVALID | for循环范围必须是数字或数组 |
| 2022 | ERR_LOOP_CONTROL_OUTSIDE | 循环控制语句在循环外使用 |
| 2023 | ERR_ATTRIBUTE_NOT_FOUND | 模块/对象没有指定成员 |
| 2024 | ERR_OBJECT_NOT_CALLABLE | 对象不可调用 |
| 2025 | ERR_UNARY_OP_UNKNOWN | 未知的一元运算符 |

#### 类型系统错误 (2100-2199)

| 错误码 | 常量名 | 说明 |
| --- | --- | --- |
| 2100 | ERR_TYPE_GENERAL | 类型错误（通用） |
| 2101 | ERR_TYPE_NOT_CALLABLE | 对象不可调用 |
| 2102 | ERR_TYPE_NOT_ITERABLE | 对象不可迭代 |
| 2103 | ERR_TYPE_NOT_SUBSCRIPTABLE | 对象不支持下标访问 |
| 2104 | ERR_TYPE_INVALID_ARITHMETIC | 不支持算术运算 |
| 2105 | ERR_TYPE_INVALID_COMPARE | 不支持比较运算 |
| 2106 | ERR_TYPE_NOT_LIST_OR_DICT | 只支持列表和字典类型 |
| 2107 | ERR_TYPE_NOT_MODULE_OR_DICT | 不是模块或字典类型 |

#### 文件/模块错误 (3000-3099)

| 错误码 | 常量名 | 说明 |
| --- | --- | --- |
| 3000 | ERR_IO_GENERAL | I/O错误（通用） |
| 3001 | ERR_FILE_NOT_FOUND | 文件不存在 |
| 3002 | ERR_MODULE_NOT_FOUND | 模块不存在 |
| 3003 | ERR_MODULE_LOAD_FAILED | 模块加载失败 |
| 3004 | ERR_MODULE_CIRCULAR_DEP | 循环导入依赖 |
| 3005 | ERR_PERMISSION_DENIED | 权限不足 |
| 3006 | ERR_MODULE_SIGNATURE_INVALID | 模块签名验证失败 |
| 3007 | ERR_MODULE_EXPORT_TABLE_INVALID | 导出表格式无效 |
| 3008 | ERR_DLL_FUNCTION_NOT_FOUND | DLL中未找到指定函数 |
| 3009 | ERR_DLL_LOAD_FAILED | DLL加载失败 |
| 3010 | ERR_DLL_SIGNATURE_FAILED | DLL签名验证失败 |

#### 编译/检查错误 (4000-4099)

| 错误码 | 常量名 | 说明 |
| --- | --- | --- |
| 4000 | ERR_COMPILE_GENERAL | 编译错误（通用） |
| 4001 | ERR_COMPILE_TYPE_CHECK | 静态类型检查失败 |
| 4002 | ERR_COMPILE_INLINE_FAILED | 内联编译失败 |
| 4003 | ERR_COMPILE_DUPLICATE_FUNC | 重复函数定义 |
| 4004 | ERR_COMPILE_PARSE_FAILED | AST解析失败 |
| 4005 | ERR_COMPILE_QTC_FAILED | QTC字节码编译失败 |
| 4006 | ERR_UNKNOWN_NODE_TYPE | 未知的AST节点类型 |

#### 断言错误 (5000-5099)

| 错误码 | 常量名 | 说明 |
| --- | --- | --- |
| 5000 | ERR_ASSERTION_FAILED | 断言失败 |

#### 系统错误 (9000-9099)

| 错误码 | 常量名 | 说明 |
| --- | --- | --- |
| 9000 | ERR_SYSTEM_GENERAL | 系统错误（通用） |
| 9001 | ERR_OUT_OF_MEMORY | 内存不足 |
| 9002 | ERR_STACK_OVERFLOW | 调用栈溢出 |
| 9003 | ERR_VM_INIT_FAILED | 虚拟机初始化失败 |
| 9004 | ERR_VM_EXECUTION_FAILED | 虚拟机执行失败 |

### 20.3 常见错误示例

**⚠ 本节最重要的一句话：下面这些「错误」，绝大多数只在解释器上是错误。字节码 VM 遇到同样的代码不会报错，会给个默认值继续往下跑。**

这是 QOT「**性能优先，安全交给使用者**」的直接后果：VM 里没有边界检查、没有除零检查、没有常量检查，因为这些检查每条指令都要花钱。**用 VM 跑生产代码，就必须自己保证输入是对的——出了事是你代码的问题，VM 不会拦你。**

调试期建议先用 `qguoat run`（解释器）跑一遍，它会把这些问题当场炸出来；确认没问题再编译成 `.qtc` 上线。

#### 速查：同一段错误代码在两个运行时上的表现

| 代码 | run（解释器） | bstrust run（VM） |
| --- | --- | --- |
| output(x); x 未声明 | 错误 2001，退出 | 输出 null，继续 |
| arr[5]，数组长度 3 | 错误 2003，退出 | 得到 null，继续 |
| 10 / 0 | 错误 2004，退出 | 得到 0，继续 |
| const PI = 3.14; PI = 999; | 错误 2006，退出 | 改成功，输出 999 |
| 同名函数定义两次 | 错误 2014，退出 | 后定义的覆盖先定义的，继续 |
| 缺少分号 | 错误 1000，两边都编译失败（语法错误没有宽容可言） |

#### 变量未定义

```
output(x);  // x 从未声明
```

先在编译期得到一条**警告**（两个运行时都有）：

```
[警告] [2000]
语义错误: 行 1 - 变量 'x' 未定义，请检查是否拼写错误或未声明
```

解释器随后在运行时报错并退出：

```
[错误] [2001]
变量 'x' 未定义
```

VM 则直接把它当 `null`，程序继续跑。**那条编译期警告是你在 VM 上唯一的提示，别把它当噪音划过去。**

#### 字符串与数字相加（这不是错误）

```
var num = "123";
output(num + 5);    // 128 —— 不报错！
```

`+` 遇到字符串和数字时，会**先尝试把字符串当数字解析**；解析得动就做加法，解析不动才退回拼接：

```
output("123" + 5);   // 128    能转成数字 → 相加
output("abc" + 5);   // abc5   转不了   → 拼接
```

两个运行时行为一致（VM 打印为 `128.000000`）。**这条规则很容易咬人**：用户输入的 `"123"` 会悄悄变成数字，而 `"12a"` 会变成字符串拼接，同一段代码在不同输入下走两条完全不同的路。要拼接就显式写 `strings()`，要算术就显式写 `numbers()`（VM）或 `ints()`/`floats()`（解释器）。

#### 索引越界

```
var arr = [1, 2, 3];
output(arr[5]);
```

解释器：

```
[错误] [2003]
数组索引 5 越界（长度为 3）
  --> test.qot:line(2)
```

VM：输出 `null`，不报错。**越界写入（`arr[5] = 9;`）在 VM 上则是被静默丢弃**——赋值看起来成功了，值其实哪也没去。参见 [§8.7](#arr-oob)。

#### 除以零

```
output(10 / 0);
```

解释器：

```
[错误] [2004]
division by zero
```

VM：得到 `0`，不报错。**这是最危险的一条**——一个本该炸掉的除零会变成 0 继续往下算，错误会一路传播到很远的地方才显形。除数来源不确定时请自己先判一下。

#### 常量修改

```
const PI = 3.14;
PI = 999;
```

编译期两边都给警告：

```
[警告] [2000]
语义错误: 行 2 - 不能修改常量 'PI' 的值
```

解释器运行时报错 `[错误] [2006] 不能修改常量 'PI'` 并退出；**VM 完全不管，赋值成功，输出 999**。也就是说 `const` 在 VM 上只是一个编译期的提醒，没有任何运行期强制力。

#### 函数重复定义

```
number_t func add(a, b) { return a + b; }
number_t func add(a, b) { return a + b + 1; }
output(add(1, 2));
```

编译期警告 `语义错误: 函数 'add' 已重复定义`。解释器运行时报 `[错误] [2014] 函数 'add' 已存在，无法重复注册` 并退出；VM 则是**后定义的覆盖先定义的**，输出 `4`。

#### 文件不存在

```
require "nonexistent.qot";
```

```
[错误] [3001]
文件不存在: nonexistent.qot
```

#### 缺少分号

```
var x = 10
output(x);
```

输出（注意错误码是 **1000**，不是 1001）：

```
[错误] [1000]
遇到意外的符号 'output' (类型: IDENTIFIER)，请检查语法或是否遗漏分号
  --> test.qot:line(2)
[错误] [1000]
遇到意外的符号 ';' (类型: SEMICOLON)，可能是多余的分号
  --> test.qot:line(2)
[错误] [4000]
解析失败: test.qot
```

两点值得留意：

- **报的行号是下一行**（这里是第 2 行）。分析器是读到 `output` 才发现前一句没结束的，所以真正要改的通常是**报错行的上一行**。
- **一处错误常常连报好几条。** 从上往下改第一条，后面的多半跟着消失，不要挨条对着改。

**语法错误一定会中止编译，不会产出 `.qtc`。** 只要你看到「编译成功」，就说明语法是干净的。

### 20.4 警告信息

除了错误，QOT 还会输出警告。**警告不中断编译，也不中断执行**，只是提示可能有问题：

```
[警告] [2000]
语义错误: 行 1 - 变量 'x' 未定义，请检查是否拼写错误或未声明
  --> test.qot:line(0)
```

关于警告，有三件事必须知道：

- **警告的错误码统一是 2000**，级别标记为「警告」，看码没法区分具体问题，得看文字。
- **警告里的 `line(0)` 是没有意义的**——语义检查阶段拿不到行号，真正的行号在文字消息里（「行 1 -...」）。别去找第 0 行。
- **在 VM 上，警告往往是你唯一的提示。** 上一节那张表里的问题，VM 运行期一声不吭，全靠编译期这几行警告。**请不要习惯性地无视它们。**

另外，用 `--inline 1` 引入 `.qot` 模块时会出现「变量 'XXX' 未定义」的**误报**警告，那个可以放心忽略，原因见 [§11.3](#inline)。

### 20.5 错误处理最佳实践

QOT 没有 `try/catch`，也没有异常。**错误处理在这门语言里就是「自己先判一下」**——尤其是在 VM 上，没人替你判。

- **调试用解释器，上线用 VM**：`qguoat run` 会把越界、除零、改常量、重复定义当场炸出来；确认干净了再 `bstrust compile`。这是本节最有用的一条。
- **编译期警告一条都别放过**：VM 运行期不报错，那几行警告就是你唯一的防线
- **下标先判范围**：`if (i >= 0 && i < glen(arr))`
- **除数先判零**：VM 上 `x / 0` 得到 0，不会报错，错误会一路传下去
- **取值先判 null**：字典缺键、数组越界、未定义变量在 VM 上一律给 `null`，拿到就先 `if (v == null)`
- **类型转换写显式的**：别依赖 `"123" + 5` 这种隐式行为，拼接写 `strings()`，算术写 `numbers()`
- **始终以分号结尾**：语法错误是唯一两个运行时都会硬拦下来的错误，改起来最省事
- **函数名唯一**：VM 上重名不报错，只是后面的悄悄覆盖前面的
- **写 `.qot` 模块记得 `--inline 1`**：漏了会在运行期报 193，跟代码本身没关系

> **关于本语言的错误哲学：**QOT 的取向是**性能优先，安全交给使用者**。VM 里没有边界检查、没有除零检查、没有常量保护，因为这些检查每条指令都要花时间。这是一个明确的、故意的取舍——它把速度给你，同时把「保证输入正确」的责任也交给你。**用得好，你拿到的是接近原生的执行速度；用得糙，错误会安静地传播很远才显形。**这不是缺陷，是这门语言的定价方式，请按它的规则写代码。

### 20.6 注解关闭警告

如果希望关闭某些警告信息，可以使用注解：

```
@-@-@ rule::nowarning  // 关闭所有警告
```

该注解放置在脚本任意位置即可生效，之后的警告将不再输出。

> **提示：** 错误信息中的行号指向源代码中出错的位置，请根据提示行号检查对应代码。如果遇到无法理解的错误，请检查语法是否正确，或参考本文档其他章节。

## 21. 两个运行时的差异对照

QOT 有**两套互相独立的执行引擎**，同一份 `.qot` 源码在它们上面跑，结果可能不一样。这一章把所有已知差异集中列出来——**如果你只读本文档的一章，就读这一章。**

|  | 解释器 | 字节码 VM |
| --- | --- | --- |
| 启动方式 | qguoat run x.qotqguoat run x.qbc | qguoat bstrust compile x.qot x.qtcqguoat bstrust run x.qtc |
| 实现 | Python，遍历 AST 执行 | C++（qotrun.dll），执行字节码 |
| 速度 | 慢 | 快得多 |
| 定位 | 开发调试 | 生产运行 |

### 21.1 出错行为：最重要的差异

一句话概括：**解释器会拦你，VM 不会。**

| 情况 | 解释器 | VM |
| --- | --- | --- |
| 读未定义变量 | 错误 2001，退出 | null，继续 |
| 数组越界读 | 错误 2003，退出 | null，继续 |
| 数组越界写 | 错误，退出 | 静默丢弃，继续 |
| 除以零 | 错误 2004，退出 | 得到 0，继续 |
| 修改 const | 错误 2006，退出 | 改成功，继续 |
| 函数重复定义 | 错误 2014，退出 | 后者覆盖前者，继续 |
| 语法错误 | 两边都编译失败，不产出.qtc |

这不是 VM 的 bug，是它的定价方式：**省下每条指令上的检查，换取速度**。完整说明见第 20.3 节。

### 21.2 语义差异

| 特性 | 解释器 | VM |
| --- | --- | --- |
| 函数内给全局变量赋值 | 改动丢弃，函数外看不到 | 改动生效，函数外看得到 |
| const 保护 | 运行期强制 | 完全不强制 |
| 数字打印格式 | 3 | 3.000000（一律六位小数） |
| U"..." / L"..." | 字节序列 / 宽字符串 | 都是 null，值直接丢失 |
| require "x.qot" | 直接可用 | 编译时必须加 --inline 1，否则报错码 193 |
| GUI | window_* 一套（旧） | vg_* 一套（第 22 章） |

**「函数内给全局变量赋值」这一条最容易出事**，因为它不报任何错，只是结果不一样：

```
var g = 10;

any_t func touch() {
    g = 15;          // 没有 var，指的是外面那个 g
}

touch();
output(g);           // 解释器: 10　　VM: 15
```

**不要依赖任何一边的行为。**要改全局就 `return` 出来再赋值，要局部就老老实实写 `var`：

```
number_t func compute() {
    return 15;
}
g = compute();       // 两个运行时结果一致
```

### 21.3 内置函数差异

两套运行时的内置函数表是**各自独立维护**的，并不完全重合。

#### 两边都有（38 个，放心用）

```
ScExec  ScLoad  ScSweep  cinget  cmd_exec  dict_get  dict_keys  dict_set
fclose  fnew  fopen  fread  fsize  fwrite  get  get_env  glen  gtypes
http_send  json_decode  json_encode  output  pop  pow  push  rand  remove
round  set_env  sleep  sqrt  strcase  strings  strrep  tnow  tstamp
unlink  unset_env
```

（另有 `__is_number`、`__iter_len` 两个是 `for` 脱糖用的内部函数，两边都有，但**不要直接调用**。）

#### 只有 VM 有（7 个）

```
numbers  type
http_clear_headers  http_get_headers  http_set_header
http_set_user_agent  http_set_verify_ssl
```

加上 [第 22 章](#gui) 的 62 个 `vg_*` GUI 函数。解释器里也注册了同名的 `vg_*`，但全都是**报错桩**，调了只会抛出 `GUI 函数 vg_init() 只在编译执行下可用`。

#### 只有解释器有（33 个）

```
batch_push  beair  bools  entry_get  floats  getpath  ints
json_pretty  json_valid  lists  max  min  parts  prog_end  sum
utf8_dec  utf8_enc
webloc_init  webloc_run  webloc_sbp
window_bind  window_bind_get  window_button  window_config  window_entry
window_label  window_loop  window_msg_info  window_new  window_place
window_size  window_title  window_visible
```

⚠ **用了这些函数，你的程序就编译不成能跑的 `.qtc` 了。** 尤其注意几个常用的：

- `max` / `min` / `sum` —— VM 上没有，得自己写循环
- `ints` / `floats` / `bools` / `lists` —— VM 上只有一个 `numbers`
- `utf8_enc` / `utf8_dec` —— VM 上没有
- `window_*` 这一整套旧 GUI —— VM 上请改用 `vg_*`

### 21.4 该怎么选

推荐的工作方式是**两个都用**：

1. **写代码时用解释器**（`qguoat run`）。它会把越界、除零、改常量、拼错变量名当场炸出来，省你半天时间。
2. **发布前编译成 `.qtc`**（`bstrust compile... --opt`）用 VM 跑，拿性能。
3. **只用两边都有的那 38 个内置函数**，这样两个阶段跑的是同一份代码。

如果你的程序一开始就注定只在 VM 上跑（比如用了 GUI），那就直接按 VM 的规矩写：**自己判边界、自己判零、自己判 null**。

## 22. 图形界面（vg_*）

**`vg_*` 只能在字节码 VM 上运行。**

```
qguoat bstrust compile app.qot app.qtc
qguoat bstrust run app.qtc
```

用 `qguoat run`（解释器）跑会得到 `GUI 函数 vg_init() 只在编译执行下可用` 这句报错——解释器里注册的只是一批占位存根。 解释器自己那套 GUI 是完全不同的 `window_*` 系列函数，两者**不能混用**。

### 22.1 最小可运行程序

```
any_t func onClick() {
    output("按钮被点了");
}

vg_init();
var win = vg_create_window("我的窗口", 400, 300);

var btn = vg_create_button(150, 120, 100, 36, "点我");
vg_button_on_click(btn, onClick);

vg_show_window();      // 注意：不传 win
vg_run();              // 阻塞，直到窗口关闭
vg_uninit();
```

骨架永远是这五步，顺序不能乱：

1. `vg_init()` —— 初始化，必须最先调
2. `vg_create_window(...)` —— 建窗口，返回一个**字符串 ID**
3. 建控件、设属性、绑事件
4. `vg_show_window()` 然后 `vg_run()` —— `vg_run()` 会**阻塞**在这里跑消息循环
5. 窗口关掉后 `vg_run()` 返回，调 `vg_uninit()` 收尾

⚠ **所有 `vg_create_*` 返回的是字符串 ID，不是对象。****控件**类函数都拿这个 ID 当第一个参数。ID 传错了函数**只会返回 `false`，不会报错**——控件没反应时先怀疑这里。

#### ⚠ 但窗口类函数一个都不收窗口 ID

`vg_show_window()`、`vg_close_window()`、`vg_set_window_title()`、 `vg_set_window_size()`、`vg_set_window_bg_gradient()` 内部固定操作「当前主窗口」， 参数是**从第 0 个数起**的。顺手把 `win` 传进去，只会把第一个真参数挤掉，而且不报错：

```
// ✗ 错：win 被当成标题，标题变成了 "obj_1" 这种 ID 字符串
vg_set_window_title(win, "新标题");
// ✗ 错：win 被当成起始色（非数组 → 黑），c2 被完全忽略
vg_set_window_bg_gradient(win, c1, c2);

// ✓ 对
vg_set_window_title("新标题");
vg_set_window_bg_gradient(c1, c2);
```

`vg_show_window(win)` / `vg_close_window(win)` 多传参数无害（直接被忽略）， 但没必要写。**QOT 全程不检查实参个数**，位置写错就是静默地干错事。

#### 创建函数的参数全都可以省

所有 `vg_create_*` 缺参时用内置默认值（位置一律 50,50，尺寸见下面的函数总表）， 所以 `vg_create_button()` 一个参数不给也能建出按钮来。

但 `vg_init()` 之前建窗口、或者窗口之前建控件，函数会往 stderr 打一行 `[Error]` 并返回 `null`；之后拿着这个 `null` 调任何控件函数， 全都静默返回 `false`——界面一片空白却零报错，多半就是这个原因。

#### ⚠ 颜色只认 `[r, g, b, a]` 数组，取值 0–1

传字符串（`"#ff0000"`、`"red"`）、数字、`null`， **一律当成不透明黑 [0,0,0,1]**，不报错也不警告。数组不足 4 个元素时，缺的 r/g/b 补 0、 缺的 a 补 1，所以 `[1, 0, 0]` 就是不透明纯红。

### 22.2 事件绑定：四种写法

所有 `*_on_*` 函数的回调参数都支持下面四种写法，前三种可以混用。

#### 写法一：直接给函数名（最常用）

```
any_t func onClick() {
    output("clicked");
}
vg_button_on_click(btn, onClick);
```

#### 写法二：函数名 + 预置实参

绑定时多写的参数会被记住，触发时**排在事件自带参数的前面**传进去：

```
any_t func log(tag, value) {
    output(tag + ": " + strings(value));
}

vg_slider_on_changed(sl, log, "音量");
// 拖动滑块 → 实际调用 log("音量", 0.7)
//                        ↑预置    ↑事件自带的值
```

这一招最实用的场景是**让多个控件共用一个回调**，靠预置参数区分是谁触发的：

```
any_t func onBtn(which) {
    output("按了第 " + strings(which) + " 个");
}
vg_button_on_click(b1, onBtn, 1);
vg_button_on_click(b2, onBtn, 2);
vg_button_on_click(b3, onBtn, 3);
```

#### 写法三：函数引用 `&(?)f(...)`

用[第 7.3 节](#funcref)的函数引用语法，把回调和预置参数写在一起：

```
vg_button_on_click(btn, &(?)log("保存", 1));
// 点击 → log("保存", 1)
```

函数引用自带的参数排在最前，绑定点后面再多写的接在其后——两种可以混着用。

#### 写法四：字符串函数名

直接给一个函数名字符串也认，触发时再回查函数表：

```
vg_button_on_click(btn, "log", "保存");
// 点击 → log("保存")
```

这种写法能让回调名在运行期决定，但**靠名字查表**——同名函数会互相顶掉， 而且名字写错时是静默不触发，没有任何提示。**能用写法一就别用它。**

#### 各事件传给回调的参数

| 绑定函数 | 触发时追加的参数 |
| --- | --- |
| vg_button_on_click | （无） |
| vg_checkbox_on_changed | checked（布尔） |
| vg_toggle_on_changed | checked（布尔） |
| vg_slider_on_changed | value（数字） |
| vg_combobox_on_selected | index（数字）, text（字符串） |
| vg_listbox_on_selected | index（数字）, text（字符串） |

**⚠ 参数多了会从前面丢，不是后面**

QOT 的实参比形参多时，丢掉的是**最前面**的。所以把一个单参数函数绑到 combobox（会传两个参数）上，收到的是 `text` 而不是 `index`：

```
any_t func pick(x) { output(x); }
vg_combobox_on_selected(cb, pick);
// 实际调用 pick(3, "选项D") → x 拿到的是 "选项D"，不是 3
```

**形参个数请照着上表写全**，不要指望「少写几个就只收前几个」。

### 22.3 函数总表（62 个）

#### 生命周期与窗口

| 函数 | 说明 |
| --- | --- |
| vg_init() | 初始化 GUI，必须最先调用 |
| vg_uninit() | 释放 GUI 资源，最后调用 |
| vg_create_window(title, w, h) | 创建窗口，返回窗口 ID |
| vg_show_window() | 显示当前窗口（不收窗口 ID） |
| vg_close_window() | 关闭并销毁当前窗口（不收窗口 ID） |
| vg_run() | 进入消息循环，阻塞直到窗口关闭 |
| vg_set_window_title(title) | 改标题（不收窗口 ID） |
| vg_set_window_size(w, h) | 改尺寸（不收窗口 ID） |
| vg_set_window_bg_gradient(c1, c2) | 设置背景渐变色（不收窗口 ID），c1/c2 是 [r,g,b,a] 数组 |
| vg_destroy(id) | 把 ID 从对象表里摘掉。注意：控件本身不会从窗口上消失（C++ 里的释放分支是空的），要隐藏请用 vg_set_visible(id, false) |

#### 通用控件属性（对任何控件 ID 都能用）

| 函数 | 说明 |
| --- | --- |
| vg_set_position(id, x, y) | 设置位置 |
| vg_set_size(id, w, h) | 设置尺寸 |
| vg_set_visible(id, bool) | 显示 / 隐藏 |
| vg_set_enabled(id, bool) | 启用 / 禁用 |

#### 按钮 Button

| 函数 | 说明 |
| --- | --- |
| vg_create_button(x, y, w, h, text) | 创建按钮，默认 200×40 |
| vg_button_set_text(id, text) | 改文字 |
| vg_button_set_color(id, color) | 背景色 |
| vg_button_set_hover_color(id, color) | 悬停色 |
| vg_button_set_border_radius(id, r) | 圆角半径 |
| vg_button_set_position(id, x, y) | 位置 |
| vg_button_on_click(id, callback [,...]) | 点击事件（无追加参数） |

#### 复选框 Checkbox / 开关 Toggle

| 函数 | 说明 |
| --- | --- |
| vg_create_checkbox(x, y, w, h, text) | 创建复选框，默认 150×30 |
| vg_checkbox_set_checked(id, bool) | 设置勾选状态 |
| vg_checkbox_get_checked(id) | 读取勾选状态 |
| vg_checkbox_on_changed(id, cb [,...]) | 状态变化，追加 checked |
| vg_create_toggle(x, y) | 创建开关，尺寸固定 60×30，没有文字 |
| vg_toggle_set_checked(id, bool) | 设置开关状态 |
| vg_toggle_get_checked(id) | 读取开关状态 |
| vg_toggle_on_changed(id, cb [,...]) | 状态变化，追加 checked |

#### 文本框 Textbox

| 函数 | 说明 |
| --- | --- |
| vg_create_textbox(x, y, w, h, placeholder) | 创建文本框，默认 200×35 |
| vg_textbox_set_text(id, text) | 设置内容 |
| vg_textbox_get_text(id) | 读取内容 |
| vg_textbox_set_placeholder(id, text) | 占位提示文字 |

⚠ 文本框**没有** `on_changed` 事件。要拿内容请在别的事件里（比如按钮点击）用 `vg_textbox_get_text()` 主动读。

#### 滑块 Slider / 进度条 ProgressBar

| 函数 | 说明 |
| --- | --- |
| vg_create_slider(x, y, w, h, min, max) | 创建滑块，默认 300×40；min/max 都省略时用控件自带范围 |
| vg_slider_set_value(id, v) | 设置值 |
| vg_slider_get_value(id) | 读取值 |
| vg_slider_on_changed(id, cb [,...]) | 拖动事件，追加 value |
| vg_create_progressbar(x, y, w, h) | 创建进度条，默认 300×20 |
| vg_progressbar_set_value(id, v) | 设置进度 |
| vg_progressbar_get_value(id) | 读取进度 |
| vg_progressbar_set_color(id, color) | 进度条颜色 |

#### 下拉框 ComboBox / 列表框 ListBox

| 函数 | 说明 |
| --- | --- |
| vg_create_combobox(x, y, w, h) | 创建下拉框，默认 200×35 |
| vg_combobox_add_item(id, text) | 添加一项 |
| vg_combobox_clear(id) | 清空 |
| vg_combobox_set_selected(id, index) | 设置选中项 |
| vg_combobox_get_selected(id) | 读取选中下标 |
| vg_combobox_on_selected(id, cb [,...]) | 选择事件，追加 index, text |
| vg_create_listbox(x, y, w, h) | 创建列表框，默认 200×150 |
| vg_listbox_add_item(id, text) | 添加一项 |
| vg_listbox_clear(id) | 清空 |
| vg_listbox_set_selected(id, index) | 设置选中项 |
| vg_listbox_get_selected(id) | 读取选中下标 |
| vg_listbox_on_selected(id, cb [,...]) | 选择事件，追加 index, text |

#### 卡片 Card / 标签页 TabControl / 转圈 Spinner

| 函数 | 说明 |
| --- | --- |
| vg_create_card(x, y, w, h, title, subtitle) | 创建卡片容器，默认 250×200 |
| vg_card_set_title(id, text) | 卡片标题 |
| vg_card_set_elevation(id, n) | 阴影层级 |
| vg_create_tabcontrol(x, y, w, h) | 创建标签页，默认 500×400 |
| vg_tabcontrol_add_tab(id, title) | 添加一页 |
| vg_tabcontrol_set_selected(id, index) | 切换到第几页 |
| vg_create_spinner(x, y, w, h) | 创建加载转圈，默认 50×50，建出来就自动开始转 |
| vg_spinner_start(id) | 开始转 |
| vg_spinner_stop(id) | 停止 |

⚠ `vg_tabcontrol` **没有**切换事件回调，也没有 `get_selected`。

### 22.4 一个完整例子

```
var win;
var txt;

any_t func onSave(tag) {
    var content = vg_textbox_get_text(txt);
    output(tag + " -> " + content);
}

any_t func onVolume(value) {
    output("音量: " + strings(value));
}

any_t func onPick(index, text) {
    output("选了第 " + strings(index) + " 项: " + text);
}

vg_init();
win = vg_create_window("设置", 420, 320);
vg_set_window_bg_gradient([0.96, 0.97, 0.98, 1], [0.89, 0.91, 0.94, 1]);  // 颜色只认 [r,g,b,a] 数组

txt = vg_create_textbox(20, 20, 380, 32);
vg_textbox_set_placeholder(txt, "在这里输入…");

var sl = vg_create_slider(20, 70, 380, 24, 0, 1);
vg_slider_set_value(sl, 0.5);
vg_slider_on_changed(sl, onVolume);

var cb = vg_create_combobox(20, 110, 380, 28);
vg_combobox_add_item(cb, "低");
vg_combobox_add_item(cb, "中");
vg_combobox_add_item(cb, "高");
vg_combobox_set_selected(cb, 1);
vg_combobox_on_selected(cb, onPick);

var btn = vg_create_button(20, 160, 100, 36, "保存");
vg_button_set_border_radius(btn, 6);
vg_button_on_click(btn, onSave, "保存按钮");   // 预置实参

vg_show_window();
vg_run();
vg_uninit();
```

### 22.5 注意事项

- **返回值是 ID 字符串**，不是对象；`vg_*` 函数失败时**只返回 `false`，不报错**，控件没反应先查 ID 传对没有
- **窗口类函数不收窗口 ID**（`vg_show_window` / `vg_close_window` / `vg_set_window_*`），多传一个 `win` 会把真参数挤掉
- **`vg_destroy(id)` 并不会让控件从窗口上消失**，它只是把 ID 从对象表里摘掉（C++ 里的释放分支是空的）；要隐藏请用 `vg_set_visible(id, false)`
- **颜色只认 `[r,g,b,a]` 数组、取值 0–1**；传字符串或十六进制一律变成不透明黑，还不报错
- **`vg_run()` 之后的代码要等窗口关掉才执行**；所有 UI 搭建必须写在它前面
- **回调里被 GUI 用到的变量请声明成全局**（顶层 `var`）。VM 上函数内可以写全局变量，这一点和解释器不同，见 [§21.2](#runtime-diff)
- **回调形参个数照 §22.2 的表写全**，多余实参从前面丢，写少了拿到的是后面的参数
- **没有 `vg_create_label`**；要显示静态文字，可以用禁用的按钮或卡片标题代替
- **坐标是绝对像素**，没有自动布局，窗口缩放不会跟着变
- **只能在 VM 上跑**，别忘了 `bstrust compile`

# 许可证

查看 [LICENSE](LICENSE\) 文件了解详细许可信息