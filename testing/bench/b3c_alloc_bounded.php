<?php
// B3c 分配密集 · 有界工作集 —— 与 b3c_alloc_bounded.qot 同构
$d = ["z" => 0];
$keys = array_fill(0, 10, 0);
for ($i = 0; $i < 10; $i++) {
    $d[strval($i)] = $i;
    $keys[$i] = strval($i);
}
$acc = 0;
for ($j = 0; $j < 87654321; $j++) {
    $acc = $acc + $d[$keys[$j % 10]];
}
echo $acc, "\n";
