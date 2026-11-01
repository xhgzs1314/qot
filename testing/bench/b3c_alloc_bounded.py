# B3c 分配密集 · 有界工作集 —— 与 b3c_alloc_bounded.qot 同构
d = {"z": 0}
keys = [0] * 10
for i in range(10):
    d[str(i)] = i
    keys[i] = str(i)
acc = 0
for j in range(87654321):
    acc = acc + d[keys[j % 10]]
print(acc)
