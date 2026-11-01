# -*- coding: utf-8 -*-
"""fuzz 已知双运行时定价分歧清单 ——  §3.1（S-01）/ SPEC R5。

本文件是 fuzz 体系与 T-15 语义钉之间的【契约文档】：
  * 每一条已知分歧都必须（a）在这里登记（b）被 testing/cases/ 下的专钉用例
    双侧钉住（c）由生成器约束规避。
  * fuzz 运行中若检出本清单之外的分歧 → 一律 FAIL（这不是容差，是发现机制）。
  * 本清单内的分歧若从生成约束中「漏出」被 fuzz 抓到，同样 FAIL——
    说明约束实现有 bug，而不是放宽比较。

| 编号 | 分歧 | 解释器 | VM / native | 生成规避 | 专钉 |
|---|---|---|---|---|---|
| KNOWN-MOD-SIGN | 负被除数取模符号 | Python %（符号随除数）：-7%3=2 | C fmod（符号随被除数）：-7%3=-1 | % 仅在 被除数区间≥0 且 除数区间>0 生成 | vm/bound_mod_negative_vm + interp/bound_mod_negative_interp |
| KNOWN-LOGIC-NUMERIC | 数值×数值 &&/|| | 一律 bool：0&&1=false | 数字 switch default → null | &&/|| 操作数仅 bool 型 | vm/bound_logic_numeric_vm + interp/bound_logic_numeric_interp |
| KNOWN-INT-PRECISION | >2^53 整数精度 | 精确 int | double 舍入 | 数值区间 |（无；表示法本质差异，容差 ULP≤8 吸收邻位） |
| KNOWN-PRINT-FORMAT | 数字打印 | str() 最短表示 | %.6f 六位小数 | 不规避 | compare.py token 化 + 2e-6/8ULP 容差 |
| KNOWN-STRING-NUM-CONCAT | 数字拼进字符串 | "1" | "1.000000" | 不规避（有意覆盖） | compare.py 同上（D-04 家族） |
| KNOWN-GLOBAL-SCOPE | 函数内读写全局 | 可见可写 | 写落局部槽 | 函数体只可见形参/局部；dict 声明仅顶层 | sema/bound_global_write_*（T-15） |
| KNOWN-GL-DICT | glen(dict) | 2010 报错 | 返回 size | glen/__iter_len 仅 number/array/string | vm/bound_dict_glen |
| KNOWN-STRINGS-BOOL | strings(布尔) | 【P1030-3 已收敛】'true'/'false'（原 str(True)='True'，Python 渗漏；demo oracle 裁判实证后按 CP-N4 向 native 对齐） | 'true'/'false' | strings() 仅作用于 number（约束保守保留） | vm+interp/bound_strings_gtypes_bool_* |
| KNOWN-GTYPES-BOOL | gtypes(布尔) | 【P1030-4 已收敛】'boolean'（原 'any'，Python 类型面渗漏；CP-N4 向 native 对齐） | 'boolean' | 不对布尔生成 gtypes（约束保守保留） | 同上 |
| KNOWN-ITERLEN-FRAC | __iter_len(正非整数) | max(0,int(val)) 截断 | 原值透传 | __iter_len 仅作用于整数区间表达式 | vm+interp/bound_iterlen_frac_* |
| KNOWN-NUMERIC-STR-ATOM | 【已解除 】数字样式字符串字面量参与算术 | 字面量被转数字，Python + 对 (int,str) TypeError 2002 | 字面量转数字后 toString 拼接成功 | 字符串原子禁用数字样式 | vm+interp/bound_numeric_str_literal_* —— S-14 终审（M5）：字面量保持字符串、+ 恒拼接，三引擎一致（两侧钉已翻转为同价 '9a'）。生成器约束保守保留（数字样字符串原子不再构成分歧，但放开属独立验证项） |
| KNOWN-UNARY-NEST | 嵌套一元负号 -(-x) | 语义分析器警告「一元负号不能用于 any 类型」（执行不受影响） | 无此分析 | 不生成嵌套一元负号 |（分析器弱点，非运行时分歧） |
| KNOWN-EMPTY-STR | 空字符串字面量 | 合法字符串值（== null 为 false） | 常量装载层即 null（== null 为 true） | 禁用空串原子 | vm+interp/bound_empty_string_* |

关于 ±1e308 边界（§3.1 原案）的偏离说明：
  词法器无科学计数法字面量，1e308 只能写成 309 位十进制 → 解释器侧是精确
  int、VM 侧是 double，两侧对「超出 double 域的中间值」根本不同表示
  （KNOWN-INT-PRECISION 的极端形态），输出差异是表示法噪声而非引擎分歧。
  fuzz 的数值上界定为 ±1e9（含 2^53-1 常量），把 ULP 容差内的合法舍入与
  真实逻辑分歧清晰分离。此偏离是有意的工程决策，不是遗漏。
"""
