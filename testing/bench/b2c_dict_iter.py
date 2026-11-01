# B2c 字典密集 · 全表迭代 —— 与 b2c_dict_iter.qot 同构
m = {"z": 0}
for j in range(100):
    m["k" + str(j)] = j
t = 0
for r in range(87654321):
    ks = list(m.keys())
    for q in range(len(ks)):
        t = t + m[ks[q]]
print(t)
