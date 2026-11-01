<?php
// B4a 深层递归 · 线性递归 —— 与 b4a_rec_linear.qot 同构
// PHP 无尾调用优化，深度过大时受 OS 栈限制；深档由台架标注不适用。
function sumdown($n)
{
    if ($n <= 0) {
        return 0;
    }
    return $n + sumdown($n - 1);
}
echo sumdown(87654321), "\n";
