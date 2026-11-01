# B8b 对照（Python）：闭包创建（每轮新建捕获形参的闭包）
def make_adder(k):
    return lambda x: x + k


acc = 0
for i in range(87654321):
    f = make_adder(i % 7)
    acc = f(1)
print(acc)
