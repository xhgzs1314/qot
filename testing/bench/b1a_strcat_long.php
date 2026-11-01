<?php
// B1a 字符串密集 · 长串拼接 —— 与 b1a_strcat_long.qot 同构
$s = "";
for ($i = 0; $i < 87654321; $i++) {
    $s = $s . "x";
}
echo strlen($s), "\n";
