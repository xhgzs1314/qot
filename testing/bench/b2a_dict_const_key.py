# B2a 字典密集 · 常量键查找 —— 与 b2a_dict_const_key.qot 同构
d = {"alpha": 1, "beta": 2, "gamma": 3, "delta": 4, "omega": 5}
s = 0
for i in range(87654321):
    s = s + d["alpha"]
print(s)
