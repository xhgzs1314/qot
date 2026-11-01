# B7b 大字典 · 1e6 键命中查找 —— 与 b7b_dict_lookup.qot 同构
m = {"z": 0}
for k in range(1000000):
    m[str(k)] = k
t = 0
for i in range(87654321):
    key = str(i % 1000000)
    t = t + m[key]
print(t)
