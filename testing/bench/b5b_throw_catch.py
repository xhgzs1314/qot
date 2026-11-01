# B5b 对照（Python）：复用同一异常实例的抛接
class Boom(Exception):
    pass


payload = Boom("boom")
acc = 0
for i in range(87654321):
    try:
        raise payload
    except Boom:
        acc = acc + 1
print(acc)
