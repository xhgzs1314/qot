<?php
// B2c 字典密集 · 全表迭代 —— 与 b2c_dict_iter.qot 同构
$m = ["z" => 0];
for ($j = 0; $j < 100; $j++) {
    $m["k" . (string)$j] = $j;
}
$t = 0;
for ($r = 0; $r < 87654321; $r++) {
    $ks = array_keys($m);
    $n = count($ks);
    for ($q = 0; $q < $n; $q++) {
        $t = $t + $m[$ks[$q]];
    }
}
echo $t, "\n";
