<?php
// B7b 大字典 · 1e6 键命中查找 —— 与 b7b_dict_lookup.qot 同构
$m = ["z" => 0];
for ($k = 0; $k < 1000000; $k++) {
    $m[(string)$k] = $k;
}
$t = 0;
for ($i = 0; $i < 87654321; $i++) {
    $key = (string)($i % 1000000);
    $t = $t + $m[$key];
}
echo $t, "\n";
