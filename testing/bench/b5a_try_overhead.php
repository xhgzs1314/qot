<?php
// B5a 对照（PHP）：try 快路径（零 throw）成本
$acc = 0;
for ($i = 0; $i < 87654321; $i++) {
    try {
        $acc = $acc + 3;
    } catch (Exception $e) {
        $acc = $acc - 1;
    }
}
echo $acc, "\n";
