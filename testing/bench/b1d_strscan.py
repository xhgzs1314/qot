# B1d 字符串密集 · 单遍扫描 —— 与 b1d_strscan.qot 同构
s = "abcdefghij"
for k in range(20):
    s = s + s
cnt = 0
for i in range(87654321):
    c = s[i]
    if c == "h":
        cnt += 1
print(cnt)
print(len(s))
