<?php
// B1b 字符串密集 · 短串构造与拼接 —— 与 b1b_strcat_short.qot 同构
$acc = 0;
$last = "";
$base = "abcdefghij";
for ($i = 0; $i < 87654321; $i++) {
    $last = $base[$i % 10] . "-tail";
    $acc = $acc + strlen($last);
}
echo $acc, "\n";
