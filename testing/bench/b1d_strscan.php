<?php
// B1d 字符串密集 · 单遍扫描 —— 与 b1d_strscan.qot 同构
$s = "abcdefghij";
for ($k = 0; $k < 20; $k++) {
    $s = $s . $s;
}
$cnt = 0;
for ($i = 0; $i < 87654321; $i++) {
    $c = $s[$i];
    if ($c === "h") {
        $cnt++;
    }
}
echo $cnt, "\n";
echo strlen($s), "\n";
