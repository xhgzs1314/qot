# B8a 对照（Python）：闭包调用 + nonlocal 捕获读写
def make_counter():
    n = 0

    def step(dummy):
        nonlocal n
        n = n + 1
        return n

    return step


step = make_counter()
acc = 0
for i in range(87654321):
    acc = step(i)
print(acc)
