<?php
// B2b 字典密集 · 动态键插入 —— 与 b2b_dict_dyn_insert.qot 同构
$m = ["z" => 0];
for ($i = 0; $i < 87654321; $i++) {
    $m[(string)$i] = $i;
}
echo count($m), "\n";
