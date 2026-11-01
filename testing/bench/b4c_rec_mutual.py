# B4c 深层递归 · 互递归 —— 与 b4c_rec_mutual.qot 同构
import sys

sys.setrecursionlimit(10 ** 7)


def iseven(n):
    if n <= 0:
        return 1
    return isodd(n - 1)


def isodd(n):
    if n <= 0:
        return 0
    return iseven(n - 1)


acc = 0
for r in range(87654321):
    acc = acc + iseven(2000)
print(acc)
