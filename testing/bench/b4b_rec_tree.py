# B4b 深层递归 · 树递归 —— 与 b4b_rec_tree.qot 同构
import sys

sys.setrecursionlimit(10 ** 7)


def tsum(d):
    if d <= 0:
        return 1
    return tsum(d - 1) + tsum(d - 1)


print(tsum(87654321))
