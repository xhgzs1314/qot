# B2b 字典密集 · 动态键插入 —— 与 b2b_dict_dyn_insert.qot 同构
m = {"z": 0}
for i in range(87654321):
    m[str(i)] = i
print(len(m))
