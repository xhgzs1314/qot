# B6b 大数组 · 顺序读 —— 与 b6b_array_rw.qot 同构
a = []
for k in range(1000000):
    a.append(k)
t = 0
for i in range(87654321):
    t = t + a[i % 1000000]
print(t)
