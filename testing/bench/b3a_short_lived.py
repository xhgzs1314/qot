# B3a 分配密集 · 短命对象 —— 与 b3a_short_lived.qot 同构
acc = 0
base = "abcdefghij"
for i in range(87654321):
    a = [i, i, i]
    d = {"k": i}
    s = base[i % 10] + "x"
    acc = acc + len(a) + len(d) + len(s)
print(acc)
