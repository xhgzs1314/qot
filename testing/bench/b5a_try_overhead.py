# B5a 对照（Python）：try 快路径（零 throw）成本
acc = 0
for i in range(87654321):
    try:
        acc = acc + 3
    except Exception:
        acc = acc - 1
print(acc)
