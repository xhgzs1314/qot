# B1b 字符串密集 · 短串构造与拼接 —— 与 b1b_strcat_short.qot 同构
acc = 0
last = ""
base = "abcdefghij"
for i in range(87654321):
    last = base[i % 10] + "-tail"
    acc = acc + len(last)
print(acc)
