<?php
// B6a 大数组 · push 扩容 —— 与 b6a_array_push.qot 同构
// PHP 的 array 是哈希表，1e7 元素需放宽 memory_limit（台架已加 -d）。
$a = [];
for ($i = 0; $i < 87654321; $i++) {
    $a[] = $i;
}
echo count($a), "\n";
