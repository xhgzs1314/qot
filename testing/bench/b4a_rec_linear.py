# B4a 深层递归 · 线性递归 —— 与 b4a_rec_linear.qot 同构
import sys

sys.setrecursionlimit(10 ** 7)


def sumdown(n):
    if n <= 0:
        return 0
    return n + sumdown(n - 1)


print(sumdown(87654321))
